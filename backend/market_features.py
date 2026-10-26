"""
市场情绪 / 资金活跃度 历史特征构建（无前视偏差）

数据源：price_history（个股日K）—— 由全市场K线聚合出【每个交易日】的市场状态。
这类特征的价值：个股次日走势高度依赖当日市场环境（普涨/普跌/缩量/放量）。

★ 严格的时点对齐（防止未来函数）：
  本表每一行的特征，全部由「该交易日收盘后」的数据计算，
  即在 T 日收盘时刻即可 100% 确定，不含任何 T+1 及之后的信息。
  因此它可以安全地用于预测「T 日收盘买入 → T+1 日收盘卖出」的收益。

★ 数据质量：
  只统计"有效交易日"——当日有行情的股票数 >= MIN_STOCKS（默认1000）才纳入，
  避免个别股票的超长历史（如1996年数据）污染统计。

输出表：market_daily (trade_date PRIMARY KEY, ...)
"""
import os
import sqlite3
import numpy as np
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_monitor.db")
MIN_STOCKS = 1000          # 有效交易日的最小股票数
LIMIT_PCT = 9.8            # 涨停判定阈值


def build_market_daily(verbose=True):
    """从个股日K聚合出每日市场特征，写入 market_daily 表"""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        "SELECT code, trade_date, close, volume FROM price_history", conn)
    if df.empty:
        print("[Mkt] price_history 为空")
        conn.close()
        return 0

    df = df.sort_values(["code", "trade_date"])
    df["prev_close"] = df.groupby("code")["close"].shift(1)
    df = df.dropna(subset=["prev_close"])
    df = df[df["prev_close"] > 0]
    df["chg"] = (df["close"] - df["prev_close"]) / df["prev_close"] * 100
    # 用 volume*close 近似成交额（price_history.amount 未落库）
    df["amt"] = df["volume"] * df["close"]

    # 有效交易日过滤
    cnt = df.groupby("trade_date")["code"].count()
    valid_days = set(cnt[cnt >= MIN_STOCKS].index)
    df = df[df["trade_date"].isin(valid_days)]
    if verbose:
        print(f"[Mkt] 有效交易日 {len(valid_days)} 天，样本 {len(df)} 行")

    g = df.groupby("trade_date")
    stat = pd.DataFrame({
        "stock_cnt": g["chg"].size(),
        "up_ratio": g["chg"].apply(lambda s: float((s > 0).mean())),
        "avg_chg": g["chg"].mean(),
        "median_chg": g["chg"].median(),
        "limit_up_cnt": g["chg"].apply(lambda s: int((s >= LIMIT_PCT).sum())),
        "limit_down_cnt": g["chg"].apply(lambda s: int((s <= -LIMIT_PCT).sum())),
        "total_amt": g["amt"].sum(),
    }).reset_index()

    stat = stat.sort_values("trade_date").reset_index(drop=True)

    # —— 以下均为「截至当日收盘可知」的衍生指标 ——
    stat["up_ratio_ma5"] = stat["up_ratio"].rolling(5, min_periods=1).mean()
    stat["avg_chg_ma5"] = stat["avg_chg"].rolling(5, min_periods=1).mean()
    # 情绪趋势：今日上涨占比 - 5日前（反映情绪是在修复还是恶化）
    stat["sentiment_trend"] = stat["up_ratio"] - stat["up_ratio"].shift(5)
    # 放量/缩量：今日总成交额 / 前5日均额
    amt_ma5 = stat["total_amt"].rolling(5, min_periods=1).mean()
    stat["amt_ratio"] = stat["total_amt"] / amt_ma5.replace(0, np.nan)
    # 综合情绪分 0~100：上涨占比与平均涨幅各占一半（均为当日收盘已知）
    stat["sentiment_score"] = (
        stat["up_ratio"] * 50 + (stat["avg_chg"].clip(-5, 5) / 5 * 0.5 + 0.5) * 50
    )
    stat["sentiment_ma5"] = stat["sentiment_score"].rolling(5, min_periods=1).mean()

    stat = stat.replace([np.inf, -np.inf], np.nan).fillna(0)

    conn.execute("DROP TABLE IF EXISTS market_daily")
    conn.execute("""
        CREATE TABLE market_daily (
            trade_date TEXT PRIMARY KEY,
            stock_cnt INTEGER, up_ratio REAL, avg_chg REAL, median_chg REAL,
            limit_up_cnt INTEGER, limit_down_cnt INTEGER, total_amt REAL,
            up_ratio_ma5 REAL, avg_chg_ma5 REAL, sentiment_trend REAL,
            amt_ratio REAL, sentiment_score REAL, sentiment_ma5 REAL
        )
    """)
    rows = [tuple(r) for r in stat[[
        "trade_date", "stock_cnt", "up_ratio", "avg_chg", "median_chg",
        "limit_up_cnt", "limit_down_cnt", "total_amt",
        "up_ratio_ma5", "avg_chg_ma5", "sentiment_trend",
        "amt_ratio", "sentiment_score", "sentiment_ma5"]].values]
    conn.executemany(
        "INSERT OR REPLACE INTO market_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

    if verbose:
        print(f"[Mkt] market_daily 写入 {len(rows)} 天")
        print(stat[["trade_date", "up_ratio", "avg_chg", "limit_up_cnt",
                    "sentiment_score"]].tail(3).to_string(index=False))
    return len(rows)


def load_market_features():
    """读取市场特征为 {trade_date: {feat: val}}"""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("SELECT * FROM market_daily").fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM market_daily LIMIT 1").description]
    except sqlite3.OperationalError:
        conn.close()
        return {}
    conn.close()
    out = {}
    for r in rows:
        d = dict(zip(cols, r))
        date = d.pop("trade_date")
        out[date] = d
    return out


# 参与训练的市场特征（全部为 T 日收盘可知）
MARKET_FEATURE_NAMES = [
    "up_ratio",           # 当日上涨家数占比
    "avg_chg",            # 当日全市场平均涨幅
    "limit_up_cnt",       # 当日涨停家数
    "sentiment_trend",    # 情绪5日变化
    "amt_ratio",          # 成交额相对5日均量（放量/缩量）
    "sentiment_score",    # 综合情绪分
]


if __name__ == "__main__":
    n = build_market_daily()
    print(f"\n完成: {n} 个交易日")
