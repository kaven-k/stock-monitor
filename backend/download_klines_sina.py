"""
全市场日K线批量下载（新浪源兜底）

为什么需要：mootdx(通达信) / 东财push2his / 同花顺 在部分网络环境不可达，
导致 price_history 表为空 → XGBoost 无法打分、模型无法训练。
新浪财经日K接口通常可用，作为可靠兜底。

优化：先通过 market_snapshot 获取【真实存在】的股票代码（~5400只），
      再只对这些代码下载，避免对不存在的代码浪费请求（原 download_klines.py
      暴力生成 9896 个候选代码，其中近半是无效请求）。

用法：
    python3 download_klines_sina.py            # 全量下载（约5000只）
    python3 download_klines_sina.py 300        # 只下载前300只（快速验证）
    python3 download_klines_sina.py 0 250      # 全量，每只250根K线
"""
import os
import sys
import time
import sqlite3
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_monitor.db")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0"
SINA_URL = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "CN_MarketData.getKLineData")

MAX_WORKERS = 10
BATCH_REPORT = 200


def get_prefix(code):
    """根据代码判断 sh/sz 前缀"""
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith(("0", "2", "3")):
        return "sz"
    if code.startswith(("4", "8")):
        return "bj"
    return "sh"


def fetch_one(code, datalen=250):
    """下载单只股票日K线，返回 (code, rows) 或 (code, None)"""
    url = SINA_URL
    params = {"symbol": f"{get_prefix(code)}{code}", "scale": "240",
              "ma": "no", "datalen": datalen}
    try:
        r = requests.get(url, params=params, timeout=12,
                         headers={"User-Agent": UA,
                                  "Referer": "https://finance.sina.com.cn"})
        rows = r.json()
        if not rows:
            return code, None
        out = []
        for row in rows:
            try:
                out.append((
                    code, row["day"][:10].replace("-", ""),
                    float(row["open"]), float(row["high"]), float(row["low"]),
                    float(row["close"]), float(row["volume"]), 0.0, 0.0,
                ))
            except (KeyError, ValueError, TypeError):
                continue
        return code, out if out else None
    except Exception:
        return code, None


def get_real_codes(limit=0):
    """
    获取真实存在的股票代码列表
    优先用全市场快照（真实有效），失败则退化为代码段生成
    """
    try:
        from market_snapshot import get_full_market_snapshot
        snap = get_full_market_snapshot()
        codes = sorted(snap.keys())
        print(f"[DL] 从全市场快照获取真实代码 {len(codes)} 只")
        return codes
    except Exception as e:
        print(f"[DL] 快照失败({e})，退化为代码段生成")
        codes = []
        for p in ["600", "601", "603"]:
            codes += [f"{p}{i:03d}" for i in range(1000)]
        codes += [f"605{i:03d}" for i in range(200)]
        codes += [f"688{i:03d}" for i in range(700)]
        for p in ["000", "001", "002", "003"]:
            codes += [f"{p}{i:03d}" for i in range(1, 1000)]
        for p in ["300", "301"]:
            codes += [f"{p}{i:03d}" for i in range(1000)]
        return codes


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    datalen = int(sys.argv[2]) if len(sys.argv) > 2 else 250

    all_codes = get_real_codes()
    if limit and limit > 0:
        # 均匀抽样，保证覆盖各板块而非只取前面
        step = max(1, len(all_codes) // limit)
        all_codes = all_codes[::step][:limit]
    print(f"[DL] 目标 {len(all_codes)} 只，每只 {datalen} 根日K，{MAX_WORKERS} 线程")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS price_history (
            code TEXT NOT NULL, trade_date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL,
            volume REAL, amount REAL, change_pct REAL,
            PRIMARY KEY (code, trade_date))""")
    conn.commit()

    # 断点续传：跳过已有足够K线的
    existing = dict(conn.execute(
        "SELECT code, COUNT(*) FROM price_history GROUP BY code").fetchall())
    todo = [c for c in all_codes if existing.get(c, 0) < 60]
    skipped = len(all_codes) - len(todo)
    print(f"[DL] 已有数据跳过 {skipped} 只，待下载 {len(todo)} 只")

    t0 = time.time()
    ok, fail, total_rows = 0, 0, 0
    pending = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_one, c, datalen): c for c in todo}
        for i, fu in enumerate(as_completed(futures), 1):
            code, rows = fu.result()
            if rows:
                pending.extend(rows)
                total_rows += len(rows)
                ok += 1
            else:
                fail += 1
            # 分批落盘
            if len(pending) >= 20000:
                conn.executemany(
                    "INSERT OR IGNORE INTO price_history"
                    "(code,trade_date,open,high,low,close,volume,amount,change_pct)"
                    " VALUES (?,?,?,?,?,?,?,?,?)", pending)
                conn.commit()
                pending.clear()
            if i % BATCH_REPORT == 0:
                el = time.time() - t0
                print(f"  {i}/{len(todo)} 成功{ok} 失败{fail} "
                      f"行数{total_rows} 耗时{el:.0f}s 预计剩余{el/i*(len(todo)-i):.0f}s")

    if pending:
        conn.executemany(
            "INSERT OR IGNORE INTO price_history"
            "(code,trade_date,open,high,low,close,volume,amount,change_pct)"
            " VALUES (?,?,?,?,?,?,?,?,?)", pending)
        conn.commit()

    total = conn.execute("SELECT COUNT(*), COUNT(DISTINCT code) FROM price_history").fetchone()
    conn.close()
    print(f"\n[DL] 完成: +{ok}只 失败{fail}只 | 库内总计 {total[0]}行 {total[1]}只 "
          f"| 耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
