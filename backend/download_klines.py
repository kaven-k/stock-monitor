"""批量下载K线 v3 — 用全市场真实代码"""
import sqlite3, time, os, sys

# 直接生成已知存在的股票代码（用market_sentiment中的逻辑）
def generate_real_codes():
    codes = []
    # 沪市
    for pfx in [600000, 601000, 603000, 605000]:
        for i in range(1000):
            codes.append(str(pfx + i))
    # 深市主板
    for pfx in [0, 1000, 2000, 3000]:
        for i in range(1000):
            codes.append("00" + str(pfx + i).zfill(4))
    # 创业板
    for i in range(1000):
        codes.append("300" + str(i).zfill(3))
    for i in range(600):
        codes.append("301" + str(i).zfill(3))
    # 科创板
    for i in range(800):
        codes.append("688" + str(i).zfill(3))
    return codes

if __name__ == '__main__':
    target_count = 500
all_real = generate_real_codes()
print(f"候选: {len(all_real)} 只")

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_monitor.db")
conn = sqlite3.connect(DB)
existing = {r[0] for r in conn.execute("SELECT DISTINCT code FROM price_history").fetchall()}
need = [c for c in all_real if c not in existing]
print(f"已有: {len(existing)}, 需下载: {len(need)}")

need = need[:max(0, target_count - len(existing))]
print(f"目标: {len(need)} 只")

from mootdx.quotes import Quotes
qt = Quotes.factory(market='std', timeout=10)

ok, ng = 0, 0
t0 = time.time()

for i, code in enumerate(need):
    try:
        df = qt.bars(code, 4, 0, 250)
        if df is None or len(df) == 0:
            ng += 1; continue
        rows = []
        for dt, r in df.iterrows():
            td = str(dt.date()) if hasattr(dt, 'date') else str(dt)[:10]
            rows.append((code, td,
                float(r.get('open',0)), float(r.get('high',0)),
                float(r.get('low',0)), float(r.get('close',0)),
                float(r.get('vol', r.get('volume', 0)))))
        conn.executemany(
            "INSERT OR IGNORE INTO price_history(code,trade_date,open,high,low,close,volume,amount,change_pct) VALUES (?,?,?,?,?,?,?,0,0)",
            rows)
        ok += 1
    except: ng += 1

    if (i+1) % 50 == 0:
        conn.commit()
        e = time.time()-t0
        print(f"  {i+1}/{len(need)} +{ok} 耗时{e:.0f}s")

conn.commit()
total = conn.execute("SELECT COUNT(*), COUNT(DISTINCT code) FROM price_history").fetchone()
print(f"\n完成: +{ok}只 | 总计 {total[0]}条 {total[1]}只 | 耗时 {time.time()-t0:.0f}s")
conn.close()
