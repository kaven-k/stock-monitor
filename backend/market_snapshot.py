"""
全市场行情快照（选股候选池的地基）

背景：market_sentiment._fetch_all_market_changes() 只返回涨跌幅数字列表（无代码），
仅用于统计涨跌家数，抓完即丢弃。选股模块拿不到全市场逐只行情，候选池被迫依赖监控列表。

本模块补齐这一环：抓取全市场 ~5300 只 A 股的【带代码】完整行情快照，
供 candidate_pool 做主线扩散与候选筛选。

数据源：腾讯 qt.gtimg.cn（批量120只/批，4线程并行，5分钟缓存），失败降级新浪。
"""
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0"

# 全市场快照缓存
_snapshot_cache = {
    "data": None,
    "timestamp": 0,
    "source": None,
}
_CACHE_TTL = 300  # 5 分钟


def _generate_a_share_codes():
    """生成全 A 股代码列表（与 market_sentiment 保持一致，沪深两市 ~5300 只）"""
    codes = []
    for prefix in ["600", "601", "603"]:
        for i in range(0, 1000):
            codes.append(f"sh{prefix}{i:03d}")
    for i in range(0, 200):
        codes.append(f"sh605{i:03d}")
    for i in range(0, 700):
        codes.append(f"sh688{i:03d}")
    for prefix in ["000", "001", "002", "003"]:
        for i in range(1, 1000):
            codes.append(f"sz{prefix}{i:03d}")
    for prefix in ["300", "301"]:
        for i in range(0, 1000):
            codes.append(f"sz{prefix}{i:03d}")
    return codes


def _parse_tencent_line(line):
    """解析腾讯行情单行，返回 (code, quote_dict) 或 None"""
    if not line or "=" not in line or '"' not in line:
        return None
    try:
        code = line.split("=")[0].split("_")[-1][2:]  # 去掉 sh/sz 前缀
        vals = line.split('"')[1].split("~")
        if len(vals) < 50:
            return None
        price = float(vals[3]) if vals[3] else 0
        prev = float(vals[4]) if vals[4] else 0
        if price <= 0 or prev <= 0:
            return None  # 停牌 / 无效代码

        def f(idx, default=0.0):
            try:
                return float(vals[idx]) if vals[idx] else default
            except (IndexError, ValueError):
                return default

        return code, {
            "name": vals[1],
            "code": code,
            "price": price,
            "last_close": prev,
            "open": f(5),
            "high": f(33),
            "low": f(34),
            "change_amt": round(f(31), 2),
            "change_pct": round(f(32), 2),
            "amount_wan": f(37),          # 成交额(万)
            "turnover_pct": round(f(38), 2),
            "amplitude_pct": round(f(43), 2),
            "mcap_yi": round(f(44), 2),
            "float_mcap_yi": round(f(45), 2),
            "limit_up": f(47),
            "limit_down": f(48),
            "vol_ratio": round(f(49), 2),
            "volume": f(6),
        }
    except (IndexError, ValueError, ZeroDivisionError):
        return None


def _fetch_tencent_snapshot(codes, batch_size=120, max_workers=4):
    """腾讯批量抓取全市场快照，返回 {code: quote}"""
    batches = [codes[i:i + batch_size] for i in range(0, len(codes), batch_size)]
    result = {}

    def fetch_one(batch):
        local = {}
        url = "https://qt.gtimg.cn/q=" + ",".join(batch)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            data = urllib.request.urlopen(req, timeout=12).read().decode("gbk", errors="ignore")
        except Exception:
            return local
        for line in data.strip().split(";"):
            parsed = _parse_tencent_line(line.strip())
            if parsed:
                local[parsed[0]] = parsed[1]
        return local

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(fetch_one, b) for b in batches]
        for fu in as_completed(futures):
            result.update(fu.result())
    return result


def _fetch_sina_snapshot(codes, batch_size=80, max_workers=4):
    """降级源：新浪批量抓取，字段少于腾讯（无换手率/量比，用 0 占位）"""
    batches = [codes[i:i + batch_size] for i in range(0, len(codes), batch_size)]
    result = {}

    def fetch_one(batch):
        local = {}
        url = "https://hq.sinajs.cn/list=" + ",".join(batch)
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Referer": "https://finance.sina.com.cn"})
        try:
            data = urllib.request.urlopen(req, timeout=12).read().decode("gbk", errors="ignore")
        except Exception:
            return local
        for line in data.strip().split(";"):
            if "=" not in line or '"' not in line:
                continue
            try:
                code = line.split("=")[0].split("_")[-1][2:]
                vals = line.split('"')[1].split(",")
                if len(vals) < 10:
                    continue
                price = float(vals[3])
                prev = float(vals[2])
                if price <= 0 or prev <= 0:
                    continue
                local[code] = {
                    "name": vals[0], "code": code,
                    "price": price, "last_close": prev,
                    "open": float(vals[1]), "high": float(vals[4]), "low": float(vals[5]),
                    "change_pct": round((price - prev) / prev * 100, 2),
                    "change_amt": round(price - prev, 2),
                    "amount_wan": float(vals[9]) / 10000.0,
                    "turnover_pct": 0.0, "vol_ratio": 0.0,
                    "amplitude_pct": 0.0, "mcap_yi": 0.0, "float_mcap_yi": 0.0,
                    "limit_up": 0.0, "limit_down": 0.0, "volume": float(vals[8]),
                }
            except (IndexError, ValueError, ZeroDivisionError):
                continue
        return local

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(fetch_one, b) for b in batches]
        for fu in as_completed(futures):
            result.update(fu.result())
    return result


def get_full_market_snapshot(force_refresh=False, min_valid=1000):
    """
    获取全市场 A 股行情快照（带代码），供选股候选池使用。

    返回: {code: {name, price, change_pct, amount_wan, turnover_pct, vol_ratio, ...}}
    失败返回 {}（调用方应降级到监控列表）
    """
    now = time.time()
    if (not force_refresh and _snapshot_cache["data"]
            and (now - _snapshot_cache["timestamp"]) < _CACHE_TTL):
        return _snapshot_cache["data"]

    codes = _generate_a_share_codes()
    print(f"[Snapshot] 抓取全市场快照，候选代码 {len(codes)} 只...")

    data = _fetch_tencent_snapshot(codes)
    source = "腾讯"
    if len(data) < min_valid:
        print(f"[Snapshot] 腾讯仅返回 {len(data)} 只，降级新浪...")
        data = _fetch_sina_snapshot(codes)
        source = "新浪"
    if len(data) < min_valid:
        print(f"[Snapshot] 全市场快照失败（{len(data)} 只），返回空")
        return {}

    print(f"[Snapshot] 完成：{len(data)} 只（来源：{source}）")
    _snapshot_cache["data"] = data
    _snapshot_cache["timestamp"] = now
    _snapshot_cache["source"] = source
    return data


def snapshot_stats(snapshot):
    """快照统计信息（用于日志/前端展示）"""
    if not snapshot:
        return {}
    ups = sum(1 for q in snapshot.values() if q.get("change_pct", 0) > 0)
    downs = sum(1 for q in snapshot.values() if q.get("change_pct", 0) < 0)
    return {
        "total": len(snapshot),
        "up": ups,
        "down": downs,
        "source": _snapshot_cache.get("source"),
        "time": time.strftime("%H:%M:%S", time.localtime(_snapshot_cache.get("timestamp", 0))),
    }


if __name__ == "__main__":
    snap = get_full_market_snapshot()
    print("统计:", snapshot_stats(snap))
    top = sorted(snap.values(), key=lambda x: x.get("change_pct", 0), reverse=True)[:5]
    for q in top:
        print(f"  {q['name']}({q['code']}) {q['price']} {q['change_pct']:+.2f}% "
              f"成交额{q['amount_wan']/10000:.1f}亿 换手{q['turnover_pct']}% 量比{q['vol_ratio']}")
