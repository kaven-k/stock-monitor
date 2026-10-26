"""
资金流 / 板块强度 特征构建（供 train_xgb --fund 使用）

前置：python3 download_fund_history.py（需东财网络，本机运行）

★ 严格时点对齐（无前视偏差）：
  所有特征均取自 T 日【收盘后】可知的数据：
    - 个股主力资金流：T 日全天主力净流入（收盘后确定）
    - 板块涨幅/排名：T 日板块收盘涨幅及其在全市场板块中的百分位（收盘后确定）
  因此可安全用于预测「T 日收盘买入 → T+1 日收盘卖出」。

特征列表（FUND_FEATURE_NAMES）：
  main_net_norm    T日主力净流入 / 近20日平均成交额（归一化，可比）
  main_net_ma3     近3日主力净流入均值（含T日）
  main_net_trend   T日主力净流入 - 5日均值（资金是进场还是撤离）
  sector_chg       所属最强板块 T日涨幅
  sector_chg_ma5   所属板块近5日累计涨幅（板块趋势）
  sector_rank_pct  板块涨幅在当日全部板块中的百分位（0~1，越高越强）
  stock_vs_sector  个股T日涨幅 - 板块T日涨幅（相对强弱）
"""
import os
import sqlite3
import numpy as np
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_monitor.db")

FUND_FEATURE_NAMES = [
    "main_net_norm", "main_net_ma3", "main_net_trend",
    "sector_chg", "sector_chg_ma5", "sector_rank_pct", "stock_vs_sector",
]


def _has_tables(conn):
    try:
        conn.execute("SELECT 1 FROM stock_fund_daily LIMIT 1")
        conn.execute("SELECT 1 FROM sector_daily LIMIT 1")
        conn.execute("SELECT 1 FROM stock_sector LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def load_fund_features():
    """
    返回 {trade_date: {code: [f1..f7]}}（列表顺序 = FUND_FEATURE_NAMES）
    表不存在时返回 {}，由调用方降级。
    """
    conn = sqlite3.connect(DB_PATH)
    if not _has_tables(conn):
        conn.close()
        print("[FundFeat] 资金流/板块表不存在，请先运行 download_fund_history.py")
        return {}

    # ---------- 1. 个股资金流 ----------
    fd = pd.read_sql("SELECT code, trade_date, main_net FROM stock_fund_daily", conn)
    fd = fd.sort_values(["code", "trade_date"])
    fd["ma3"] = fd.groupby("code")["main_net"].transform(
        lambda s: s.rolling(3, min_periods=1).mean())
    fd["mean20"] = fd.groupby("code")["main_net"].transform(
        lambda s: s.rolling(20, min_periods=1).mean())
    fd["trend"] = fd["main_net"] - fd["mean20"]

    # 归一化：主力净流入 / 个股近20日平均成交额（用K线成交额近似）
    ph = pd.read_sql("SELECT code, trade_date, close, volume FROM price_history", conn)
    ph["amt"] = ph["close"] * ph["volume"]
    amt20 = ph.sort_values(["code", "trade_date"]).groupby("code")["amt"].transform(
        lambda s: s.rolling(20, min_periods=1).mean())
    ph = ph.assign(amt20=amt20)[["code", "trade_date", "amt20"]]
    fd = fd.merge(ph, on=["code", "trade_date"], how="left")
    fd["main_net_norm"] = fd["main_net"] / fd["amt20"].replace(0, np.nan)
    fd["main_net_norm"] = fd["main_net_norm"].replace([np.inf, -np.inf], np.nan).fillna(0)

    # ---------- 2. 板块强度 ----------
    sd = pd.read_sql("SELECT sector_code, trade_date, change_pct FROM sector_daily", conn)
    sd = sd.sort_values(["sector_code", "trade_date"])
    sd["ma5"] = sd.groupby("sector_code")["change_pct"].transform(
        lambda s: s.rolling(5, min_periods=1).sum())
    # 板块涨幅当日百分位排名（0~1）
    sd["rank_pct"] = sd.groupby("trade_date")["change_pct"].rank(pct=True)

    # ---------- 3. 个股→板块（取最强板块） ----------
    ss = pd.read_sql("SELECT code, sector_code FROM stock_sector", conn)
    # 个股当日涨幅（用于相对强弱）
    ph2 = pd.read_sql("SELECT code, trade_date, close FROM price_history", conn)
    ph2 = ph2.sort_values(["code", "trade_date"])
    ph2["chg"] = ph2.groupby("code")["close"].pct_change() * 100

    # 个股在当日所属板块中，取涨幅最高的板块作为"最强归属"
    m = ss.merge(sd, on="sector_code", how="inner")
    m = m.rename(columns={"trade_date": "sec_date"})
    # 板块每日特征展开到个股-日期
    best = (m.sort_values(["code", "sec_date", "change_pct"], ascending=[True, True, False])
              .groupby(["code", "sec_date"]).first().reset_index())
    best = best.rename(columns={"sec_date": "trade_date", "change_pct": "sector_chg"})
    best = best[["code", "trade_date", "sector_chg", "ma5", "rank_pct"]]

    # ---------- 4. 合并 ----------
    df = fd.merge(best, on=["code", "trade_date"], how="left")
    df = df.merge(ph2[["code", "trade_date", "chg"]], on=["code", "trade_date"], how="left")
    df["stock_vs_sector"] = df["chg"] - df["sector_chg"]

    cols = {
        "main_net_norm": "main_net_norm", "main_net_ma3": "ma3",
        "main_net_trend": "trend", "sector_chg": "sector_chg",
        "sector_chg_ma5": "ma5", "sector_rank_pct": "rank_pct",
        "stock_vs_sector": "stock_vs_sector",
    }
    out = {}
    sub = df[["code", "trade_date"] + list(cols.values())].fillna(0)
    for code, date, *vals in sub.itertuples(index=False, name=None):
        out.setdefault(str(date).replace("-", ""), {})[str(code)] = [float(v) for v in vals]

    conn.close()
    print(f"[FundFeat] 构建完成: {len(out)} 个交易日")
    return out


if __name__ == "__main__":
    f = load_fund_features()
    if f:
        d = sorted(f.keys())[-1]
        print(f"最新交易日 {d}: {len(f[d])} 只")
        k = list(f[d].keys())[0]
        print(f"  样本 {k}: {dict(zip(FUND_FEATURE_NAMES, f[d][k]))}")
    else:
        print("无数据，请先运行 download_fund_history.py")
