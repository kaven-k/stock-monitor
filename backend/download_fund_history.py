"""
历史资金流 / 板块强度 数据下载（需能访问东方财富，本机运行）

⚠️ 沙箱内东财 push2his 不可达，请在【本机】运行：
    python3 download_fund_history.py            # 全量
    python3 download_fund_history.py 200        # 只下前200只（快速验证）

产出三张表（全部为「T日收盘后可知」的历史数据，不含未来信息）：
  1. stock_fund_daily  个股每日主力资金流
  2. sector_daily      板块每日涨幅/资金流
  3. stock_sector      个股→板块 归属映射

对应特征（见 fund_history.py 的 FUND_FEATURE_NAMES）：
  主力资金：main_net_ratio(主力净流入占比) / main_net_ma3 / main_net_trend
  板块强度：sector_chg(板块当日涨幅) / sector_chg_ma5 / sector_rank_pct
            / stock_vs_sector(个股相对板块强弱)
"""
import os
import sys
import time
import sqlite3
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_monitor.db")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0"
REFERER = "https://quote.eastmoney.com/"
FFLOW_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
BLOCK_URL = "https://push2.eastmoney.com/api/qt/clist/get"

MAX_WORKERS = 8
DAYS = 250


def _get(url, params, timeout=12):
    try:
        r = requests.get(url, params=params, timeout=timeout,
                         headers={"User-Agent": UA, "Referer": REFERER})
        d = r.json()
        return (d.get("data") or {})
    except Exception:
        return {}


def secid(code):
    """代码转东财 secid：沪市1. 深市0."""
    if code.startswith(("6", "9")):
        return f"1.{code}"
    return f"0.{code}"


def fetch_fund_flow(code, days=DAYS):
    """个股历史主力资金流，返回 [(date, main_net, main_net_ratio...), ...]"""
    d = _get(FFLOW_URL, {
        "secid": secid(code), "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "lmt": days, "klt": "101",
    })
    klines = d.get("klines") or []
    out = []
    for row in klines:
        p = row.split(",")
        # f51日期 f52主力净流入 f53小单 f54中单 f55大单 f56超大单
        if len(p) < 7:
            continue
        try:
            out.append((code, p[0].replace("-", ""), float(p[1]), float(p[2]),
                        float(p[3]), float(p[4]), float(p[5])))
        except ValueError:
            continue
    return out


def fetch_sector_list():
    """获取全部行业+概念板块 (BK 代码)"""
    sectors = []
    for fs in ["m:90+t:2+f:!50", "m:90+t:3+f:!50"]:
        d = _get(BLOCK_URL, {
            "pn": "1", "pz": "500", "po": "1", "fid": "f3",
            "fs": fs, "fields": "f12,f14,f3,f62",
        })
        for it in (d.get("diff") or []):
            code = it.get("f12", "")
            if code.startswith("BK"):
                sectors.append((code, it.get("f14", "")))
    return sectors


def fetch_sector_kline(bk_code, days=DAYS):
    """板块历史K线（涨幅/成交额）"""
    d = _get(KLINE_URL, {
        "secid": f"90.{bk_code}", "fields1": "f1,f2,f3",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "lmt": days, "klt": "101", "fqt": "1",
    })
    out = []
    for row in (d.get("klines") or []):
        p = row.split(",")
        if len(p) < 7:
            continue
        try:
            # f51日期 f52收盘 f53开盘(实为close/open...) 用涨跌幅需自算
            out.append((bk_code, p[0].replace("-", ""), float(p[2]), float(p[5])))
        except ValueError:
            continue
    # 计算板块日涨幅：收盘价环比
    res = []
    for i in range(1, len(out)):
        prev_close = out[i - 1][2]
        if prev_close > 0:
            chg = (out[i][2] - prev_close) / prev_close * 100
            res.append((out[i][0], out[i][1], round(chg, 4), out[i][3]))
    return res


def fetch_sector_members(bk_code):
    """板块成分股"""
    d = _get(BLOCK_URL, {
        "pn": "1", "pz": "500", "po": "1", "fid": "f3",
        "fs": f"b:{bk_code}", "fields": "f12,f14",
    })
    return [(bk_code, it.get("f12", "")) for it in (d.get("diff") or [])
            if not str(it.get("f12", "")).startswith("BK")]


def init_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS stock_fund_daily (
        code TEXT, trade_date TEXT, main_net REAL, small_net REAL,
        mid_net REAL, big_net REAL, huge_net REAL,
        PRIMARY KEY (code, trade_date))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sector_daily (
        sector_code TEXT, trade_date TEXT, change_pct REAL, amount REAL,
        PRIMARY KEY (sector_code, trade_date))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS stock_sector (
        code TEXT, sector_code TEXT, PRIMARY KEY (code, sector_code))""")
    conn.commit()


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    conn = sqlite3.connect(DB_PATH)
    init_tables(conn)

    # 1. 个股资金流
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT code FROM price_history").fetchall()]
    if limit and limit > 0:
        step = max(1, len(codes) // limit)
        codes = codes[::step][:limit]
    print(f"[Fund] 下载个股资金流: {len(codes)} 只")

    t0 = time.time()
    ok = fail = 0
    pending = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_fund_flow, c): c for c in codes}
        for i, fu in enumerate(as_completed(futs), 1):
            rows = fu.result()
            if rows:
                pending.extend(rows); ok += 1
            else:
                fail += 1
            if len(pending) >= 20000:
                conn.executemany("INSERT OR IGNORE INTO stock_fund_daily VALUES (?,?,?,?,?,?,?)", pending)
                conn.commit(); pending.clear()
            if i % 300 == 0:
                print(f"  {i}/{len(codes)} ok={ok} fail={fail} {time.time()-t0:.0f}s")
    if pending:
        conn.executemany("INSERT OR IGNORE INTO stock_fund_daily VALUES (?,?,?,?,?,?,?)", pending)
        conn.commit()
    print(f"[Fund] 资金流完成: ok={ok} fail={fail} "
          f"共{conn.execute('SELECT COUNT(*) FROM stock_fund_daily').fetchone()[0]}行")

    # 2. 板块历史K线
    sectors = fetch_sector_list()
    print(f"[Fund] 板块 {len(sectors)} 个，下载历史K线...")
    rows_all = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for fu in as_completed([ex.submit(fetch_sector_kline, s[0]) for s in sectors]):
            rows_all.extend(fu.result() or [])
    conn.executemany("INSERT OR IGNORE INTO sector_daily VALUES (?,?,?,?)", rows_all)
    conn.commit()
    print(f"[Fund] sector_daily {len(rows_all)} 行")

    # 3. 板块成分股映射
    print(f"[Fund] 下载板块成分股映射...")
    mem = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for fu in as_completed([ex.submit(fetch_sector_members, s[0]) for s in sectors]):
            mem.extend(fu.result() or [])
    conn.executemany("INSERT OR IGNORE INTO stock_sector VALUES (?,?)", mem)
    conn.commit()
    print(f"[Fund] stock_sector {len(mem)} 条")

    conn.close()
    print(f"[Fund] 全部完成，耗时 {time.time()-t0:.0f}s")
    print("下一步: python3 fund_history.py 构建特征 → python3 train_xgb.py 1 close --market --fund --time")


if __name__ == "__main__":
    main()
