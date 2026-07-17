"""
市场情绪指标模块 v3.2
- 涨跌比 / 涨跌停统计（全市场数据）
- 市场成交量（基于 Sina API 指数数据）
- 恐慌贪婪指数
- 市场温度计 (0-100)

数据源: 腾讯 qt.gtimg.cn → 东财延时 push2delay → 新浪 hq.sinajs.cn（1->2->3 降级链，全市场）

升级点(v3.2):
- 多数据源降级链：1号腾讯 / 2号东财延时 / 3号新浪，任一失败自动切换下一源
- 当前生效数据源编号与名称写入 source 标签，便于排查
- 涨跌停从全量数据统计，不再采样
- 5分钟缓存，避免重复请求
"""
import requests
import time
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# 全市场数据缓存（避免短时间内重复拉取）
_full_market_cache = {"data": None, "timestamp": 0, "lock": threading.Lock()}
_CACHE_TTL = 300  # 5分钟缓存


def _sina_get(url, timeout=10):
    for attempt in range(3):
        try:
            r = requests.get(url, headers={
                "User-Agent": UA,
                "Referer": "https://finance.sina.com.cn/"
            }, timeout=timeout)
            return r
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1)


# ---- 腾讯全市场涨跌数据源（替代不可用的东财 push2）----
# 腾讯 qt.gtimg.cn 单次可接受大量代码，gbk 解码，格式稳定不封 IP

def _generate_a_share_codes():
    """生成全 A 股代码列表（约 5300 只活跃股票）"""
    codes = []
    # 上海主板 600000-600xxx / 601000-601xxx / 603000-603xxx
    for prefix in ["600", "601", "603"]:
        for i in range(0, 1000):
            codes.append(f"sh{prefix}{i:03d}")
    # 上海主板 605 (近年新股)
    for i in range(0, 200):
        codes.append(f"sh605{i:03d}")
    # 上海科创板 688
    for i in range(0, 700):
        codes.append(f"sh688{i:03d}")
    # 深圳主板/中小板 000001-000xxx / 001001-001xxx / 002001-002xxx / 003001-003xxx
    for prefix in ["000", "001", "002", "003"]:
        for i in range(1, 1000):
            codes.append(f"sz{prefix}{i:03d}")
    # 深圳创业板 300/301
    for prefix in ["300", "301"]:
        for i in range(0, 1000):
            codes.append(f"sz{prefix}{i:03d}")
    # 北交所 bj 前缀腾讯不支持良好，跳过（仅沪深两市已覆盖 ~5300 只活跃股）
    return codes


def _fetch_tencent_batch(codes, batch_size=120):
    """分批从腾讯获取股票行情，返回 {code: change_pct} 字典（4线程并行，单批最多120只）"""
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor, as_completed
    result = {}
    total = len(codes)
    # 分批
    batches = []
    for start in range(0, total, batch_size):
        batches.append(codes[start:start + batch_size])

    def fetch_one_batch(batch):
        """抓取一批，返回 {code: change_pct} 局部字典"""
        local = {}
        url = "https://qt.gtimg.cn/q=" + ",".join(batch)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            data = urllib.request.urlopen(req, timeout=10).read().decode("gbk")
        except Exception:
            return local
        for line in data.strip().split(";"):
            line = line.strip()
            if not line or "=" not in line or '"' not in line:
                continue
            try:
                code = line.split("=")[0].split("_")[-1]
                vals = line.split('"')[1].split("~")
                if len(vals) < 5 or not vals[3]:
                    continue
                price = float(vals[3])
                prev = float(vals[4])
                if prev == 0:
                    continue
                local[code] = round((price - prev) / prev * 100, 2)
            except (ValueError, IndexError, ZeroDivisionError):
                continue
        return local

    # 4 线程并行抓取
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fetch_one_batch, b) for b in batches]
        for future in as_completed(futures):
            result.update(future.result())

    return result


def _fetch_all_market_changes_tencent():
    """使用腾讯批量接口获取全市场涨跌幅（push2.eastmoney.com 替代方案）"""
    codes = _generate_a_share_codes()
    changes_dict = _fetch_tencent_batch(codes, batch_size=120)
    if not changes_dict:
        return None
    return list(changes_dict.values())


def _emdatah5_get(url, params=None, timeout=10):
    """东方财富 emdatah5 API 请求"""
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers={
                "User-Agent": UA,
                "Referer": "https://data.eastmoney.com/"
            }, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1)
    return {}


def _fetch_em_clist_page(host, base_params, page, page_size=500):
    """抓取东方财富 clist 单页，返回 change_pct 列表（float）"""
    params = base_params.copy()
    params["pz"] = str(page_size)
    params["pn"] = str(page)
    try:
        data = _emdatah5_get(f"https://{host}/api/qt/clist/get", params, timeout=8)
        if not data or "data" not in data:
            return []
        diffs = data["data"].get("diff", [])
        result = []
        for d in diffs:
            val = d.get("f3")
            if val is None:
                continue
            try:
                result.append(float(val) if val != "-" else 0.0)
            except (ValueError, TypeError):
                result.append(0.0)
        return result
    except Exception:
        return []


def _fetch_all_market_changes_em():
    """2号数据源: 东方财富延时行情 push2delay.eastmoney.com 全市场 clist
    注意: 该接口每页固定返回 100 条（pz 参数被忽略），必须按 pn 翻页至全量。"""
    host = "push2delay.eastmoney.com"
    base_params = {
        "fid": "f3", "po": "1", "np": "1", "fltt": "2", "invt": "2",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23", "fields": "f3",
    }
    count_params = base_params.copy()
    count_params["pz"] = "1"
    count_params["pn"] = "1"
    try:
        count_data = _emdatah5_get(f"https://{host}/api/qt/clist/get", count_params, timeout=8)
        total = count_data.get("data", {}).get("total", 0) if count_data else 0
        if total <= 0:
            return None
        page_size = 100  # 该接口每页固定 100 条，pz 参数无效
        total_pages = (total + page_size - 1) // page_size
        all_changes = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(_fetch_em_clist_page, host, base_params, p, page_size): p
                       for p in range(1, total_pages + 1)}
            for future in as_completed(futures):
                try:
                    all_changes.extend(future.result())
                except Exception:
                    pass
        # 要求拿到足够完整的样本，避免网络抖动导致的残缺数据
        if len(all_changes) >= max(500, int(total * 0.6)):
            return all_changes
    except Exception:
        return None
    return None


def _fetch_sina_batch(codes, batch_size=80):
    """分批从新浪获取行情，返回 {code: change_pct}（8线程并行）"""
    import urllib.request
    result = {}
    batches = [codes[i:i + batch_size] for i in range(0, len(codes), batch_size)]

    def fetch_one(batch):
        local = {}
        url = "https://hq.sinajs.cn/list=" + ",".join(batch)
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Referer": "https://finance.sina.com.cn/"
        })
        try:
            data = urllib.request.urlopen(req, timeout=10).read().decode("gbk")
        except Exception:
            return local
        for line in data.strip().split("\n"):
            if "hq_str" not in line or "=" not in line or '"' not in line:
                continue
            try:
                key = line.split("=")[0].replace("var hq_str_", "")
                vals = line.split('"')[1].split(",")
                if len(vals) < 4 or not vals[2] or not vals[3]:
                    continue
                prev = float(vals[2])
                price = float(vals[3])
                if prev == 0:
                    continue
                local[key] = round((price - prev) / prev * 100, 2)
            except (ValueError, IndexError, ZeroDivisionError):
                continue
        return local

    with ThreadPoolExecutor(max_workers=8) as executor:
        for future in as_completed([executor.submit(fetch_one, b) for b in batches]):
            result.update(future.result())
    return result


def _fetch_all_market_changes_sina():
    """3号数据源: 新浪 hq.sinajs.cn 批量接口（独立供应商）"""
    codes = _generate_a_share_codes()
    changes = _fetch_sina_batch(codes, batch_size=80)
    return list(changes.values()) if changes else None


# ===== 全市场涨跌数据源链（1 -> 2 -> 3 降级）=====
# 1号: 腾讯 qt.gtimg.cn      （批量, 4线程并行, 稳定不封IP）
# 2号: 东财延时 push2delay    （clist 全量, 备用主机）
# 3号: 新浪 hq.sinajs.cn      （批量, 独立供应商）
def _build_source_chain():
    return [
        {"id": 1, "name": "腾讯", "fn": _fetch_all_market_changes_tencent},
        {"id": 2, "name": "东财延时", "fn": _fetch_all_market_changes_em},
        {"id": 3, "name": "新浪", "fn": _fetch_all_market_changes_sina},
    ]


def _current_source():
    """返回当前缓存使用的数据源 (source_id, source_name)，无有效数据时均为 None"""
    with _full_market_cache["lock"]:
        return _full_market_cache.get("source_id"), _full_market_cache.get("source_name")


def _fetch_all_market_changes(force_refresh=False):
    """
    获取全市场所有A股的涨跌幅列表（按 1->2->3 数据源链降级）
    返回 [float, ...] 涨跌幅列表，或 None（全部失败时）
    使用 5 分钟缓存，并缓存当前生效的数据源编号/名称。
    """
    with _full_market_cache["lock"]:
        now = time.time()
        if not force_refresh and _full_market_cache["data"] is not None and (now - _full_market_cache["timestamp"]) < _CACHE_TTL:
            return _full_market_cache["data"]

    result = None
    used_source = {"id": None, "name": None}

    for src in _build_source_chain():
        try:
            print(f"[Sentiment] 尝试 {src['id']}号数据源({src['name']})...")
            r = src["fn"]()
            if r and len(r) > 100:
                result = r
                used_source = {"id": src["id"], "name": src["name"]}
                print(f"[Sentiment] {src['id']}号({src['name']})成功: {len(result)} 只股票")
                break
            print(f"[Sentiment] {src['id']}号({src['name']})无有效数据, 尝试下一源")
        except Exception as e:
            print(f"[Sentiment] {src['id']}号({src['name']})失败: {e}")

    if result is None:
        print("[Sentiment] 全部数据源均失败, 全市场宽度降级为监控股票")

    with _full_market_cache["lock"]:
        _full_market_cache["data"] = result
        _full_market_cache["source_id"] = used_source["id"]
        _full_market_cache["source_name"] = used_source["name"]
        _full_market_cache["timestamp"] = time.time()

    return result


def get_full_market_breadth():
    """
    全市场涨跌家数（东方财富 API，分页遍历全部 ~5500 只A股）
    返回: {up_count, down_count, flat_count, total, up_ratio, time, source}
    降级: API 失败返回 None，调用方应改用 quotes 数据
    """
    try:
        all_changes = _fetch_all_market_changes()
        if not all_changes:
            return None

        total = len(all_changes)
        up = sum(1 for c in all_changes if c > 0)
        down = sum(1 for c in all_changes if c < 0)
        flat = total - up - down

        sid, sname = _current_source()
        source_label = f"全市场({sid}号·{sname}) {total}只A股" if sid else f"全市场 {total}只A股"
        return {
            "up_count": up, "down_count": down, "flat_count": flat,
            "total": total, "up_ratio": round(up / total * 100, 1) if total else 0,
            "time": datetime.now().strftime("%H:%M:%S"),
            "source": source_label,
        }
    except Exception as e:
        print(f"[Sentiment] 全市场涨跌数据获取失败: {e}")
        return None


def get_full_market_limit_stats():
    """
    全市场涨跌停家数（东方财富 API，分页遍历全部 ~5500 只A股）
    返回: {limit_up, limit_down, net, time, source}
    """
    try:
        all_changes = _fetch_all_market_changes()
        if not all_changes:
            return None

        limit_up = sum(1 for c in all_changes if c >= 9.8)
        limit_down = sum(1 for c in all_changes if c <= -9.8)

        sid, sname = _current_source()
        source_label = f"全市场({sid}号·{sname}) {len(all_changes)}只A股" if sid else f"全市场 {len(all_changes)}只A股"
        return {
            "limit_up": limit_up, "limit_down": limit_down,
            "net": limit_up - limit_down,
            "time": datetime.now().strftime("%H:%M:%S"),
            "source": source_label,
        }
    except Exception as e:
        print(f"[Sentiment] 全市场涨跌停数据获取失败: {e}")
        return None


def get_market_breadth(quotes=None):
    """
    市场宽度指标 - 优先全市场数据，降级为监控股票
    
    Args:
        quotes: {code: {price, change_pct, ...}} 实时行情（降级用）
    Returns:
        {up_count, down_count, flat_count, up_ratio, total, time, source}
    """
    # 优先尝试全市场数据
    full = get_full_market_breadth()
    if full and full["total"] > 100:
        return full

    if not quotes:
        return {
            "up_count": 0, "down_count": 0, "flat_count": 0,
            "total": 0, "up_ratio": 0,
            "time": datetime.now().strftime("%H:%M:%S"),
            "source": "无数据",
        }

    up = sum(1 for q in quotes.values() if q.get("change_pct", 0) > 0)
    down = sum(1 for q in quotes.values() if q.get("change_pct", 0) < 0)
    total = len(quotes)
    flat = total - up - down

    return {
        "up_count": up, "down_count": down, "flat_count": flat,
        "total": total, "up_ratio": round(up / total * 100, 1) if total else 0,
        "time": datetime.now().strftime("%H:%M:%S"),
        "source": f"监控股票({total}只)",
    }


def get_limit_stats(quotes=None):
    """
    涨跌停统计 - 优先全市场数据，降级为监控股票
    """
    full = get_full_market_limit_stats()
    if full and full["limit_up"] + full["limit_down"] > 0:
        return full

    if not quotes:
        return {
            "limit_up": 0, "limit_down": 0, "net": 0,
            "time": datetime.now().strftime("%H:%M:%S"),
            "source": "无数据",
        }

    up = sum(1 for q in quotes.values() if q.get("change_pct", 0) >= 9.8)
    down = sum(1 for q in quotes.values() if q.get("change_pct", 0) <= -9.8)

    return {
        "limit_up": up, "limit_down": down, "net": up - down,
        "time": datetime.now().strftime("%H:%M:%S"),
        "source": f"监控股票({len(quotes)}只)",
    }


def get_index_data():
    """
    获取三大指数实时数据（Sina API）
    
    Returns:
        {sh: {price, change_pct, volume_yi}, sz: {...}, cy: {...}}
    """
    url = "https://hq.sinajs.cn/list=sh000001,sz399001,sz399006"
    
    try:
        r = _sina_get(url, timeout=10)
        data = r.text
        
        indices = {}
        for line in data.strip().split(";"):
            if not line.strip() or "=" not in line or '"' not in line:
                continue
            try:
                key = line.split("=")[0].split("_")[-1]
                vals = line.split('"')[1].split(",")
                if len(vals) < 9:
                    continue
                
                name = vals[0]
                # Sina 指数格式: [0]名称 [1]今开 [2]昨收 [3]当前价 [4]最高 [5]最低 [8]成交量 [9]成交额
                last_close = float(vals[2]) if vals[2] else 0
                price = float(vals[3]) if vals[3] else 0
                change_pct = round((price - last_close) / last_close * 100, 2) if last_close != 0 else 0
                
                # 成交额（元）转亿
                amount = float(vals[9]) if len(vals) > 9 and vals[9] else 0
                amount_yi = round(amount / 100000000, 2) if amount else 0
                
                key_name = "sh" if "000001" in key else ("sz" if "399001" in key else "cy")
                indices[key_name] = {
                    "name": name,
                    "price": price,
                    "change_pct": round(change_pct, 2),
                    "volume_yi": amount_yi,
                }
            except (IndexError, ValueError):
                continue
        
        return indices
    except Exception as e:
        return {"error": str(e)}


def get_sentiment_index(quotes=None):
    """
    综合市场情绪指标 (恐慌/贪婪指数 0-100)

    1. 涨跌比 (权重 40%) — 全市场数据，无需监控
    2. 涨停跌停差 (权重 25%) — 全市场数据，无需监控
    3. 平均涨跌幅 (权重 20%) — 全市场数据，无需监控
    4. 成交量活跃度 (权重 15%) — 需逐股数据，仅监控启动时激活

    Returns:
        {score, level, level_text, level_color, factors, time}
    """
    score = 50.0
    factors = []

    # 1. 涨跌比 — 全市场优先
    breadth = get_market_breadth(quotes)
    if breadth["total"] > 0:
        up_ratio = breadth["up_ratio"]
        breadth_score = min(100, max(0, up_ratio * 1.5))
        factors.append({
            "name": "涨跌比",
            "value": f"{up_ratio}% ({breadth['up_count']}/{breadth['total']})",
            "score": round(breadth_score, 1),
            "weight": 40,
        })
        score = score * 0.60 + breadth_score * 0.40

    # 2. 涨停跌停差 — 全市场优先
    limit = get_limit_stats(quotes)
    net = limit["net"]
    limit_score = min(100, max(0, 50 + net * 10))
    factors.append({
        "name": "涨跌停差",
        "value": f"+{limit['limit_up']}/-{limit['limit_down']}",
        "score": round(limit_score, 1),
        "weight": 25,
    })
    score = score * 0.75 + limit_score * 0.25

    # 3. 平均涨跌幅 — 优先全市场，降级监控
    full_changes = _fetch_all_market_changes()
    if full_changes and len(full_changes) > 100:
        all_changes = full_changes
        sid, sname = _current_source()
        src_tag = f"{sid}号·{sname}" if sid else "全市场"
        sample_label = f"{src_tag}({len(all_changes)}只)"
    elif quotes:
        all_changes = [q.get("change_pct", 0) for q in quotes.values()]
        sample_label = f"监控({len(all_changes)}只)"
    else:
        all_changes = []
    if all_changes:
        avg_change = round(sum(all_changes) / len(all_changes), 2)
        avg_score = min(100, max(0, 50 + avg_change * 10))
        factors.append({
            "name": "平均涨幅",
            "value": f"{avg_change}% ({sample_label})",
            "score": round(avg_score, 1),
            "weight": 20,
        })
        score = score * 0.80 + avg_score * 0.20

    # 4. 活跃度 — 需监控逐股数据，可选
    if quotes:
        turnovers = [q.get("turnover_pct", 0) for q in quotes.values() if q.get("turnover_pct", 0) > 0]
        avg_turnover = round(sum(turnovers) / len(turnovers), 1) if turnovers else 0
        if avg_turnover < 1:
            active_score = max(0, avg_turnover * 30)
        elif avg_turnover < 5:
            active_score = 30 + (avg_turnover - 1) * 15
        else:
            active_score = min(100, 90 + (avg_turnover - 5) * 2)
        factors.append({
            "name": "活跃度",
            "value": f"换手{avg_turnover}%",
            "score": round(active_score, 1),
            "weight": 15,
        })
        score = score * 0.85 + active_score * 0.15

    # 因子全空 → 真正无数据
    if not factors:
        return {
            "score": 50, "level": "neutral",
            "level_text": "⚖️ 数据源暂不可用", "level_color": "#868e96",
            "factors": [],
            "time": datetime.now().strftime("%H:%M:%S"),
        }
    
    score = round(score, 1)
    
    # 情绪等级
    if score >= 80:
        level, level_text, level_color = "overheat", "过热 🔥", "#e03131"
    elif score >= 65:
        level, level_text, level_color = "warm", "偏热 🔶", "#f59e0b"
    elif score >= 45:
        level, level_text, level_color = "neutral", "中性 ⚖️", "#6366f1"
    elif score >= 30:
        level, level_text, level_color = "cool", "偏冷 🔵", "#3b82f6"
    else:
        level, level_text, level_color = "fear", "恐慌 ❄️", "#2f9e44"
    
    return {
        "score": score,
        "level": level,
        "level_text": level_text,
        "level_color": level_color,
        "factors": factors,
        "time": datetime.now().strftime("%H:%M:%S"),
    }


def get_market_thermometer(quotes=None):
    """
    综合市场温度计 - 一页看全市场状态
    
    Returns:
        {sentiment, breadth, limit, indices, advice, time}
    """
    sentiment = get_sentiment_index(quotes)
    breadth = get_market_breadth(quotes)
    limit = get_limit_stats(quotes)
    indices = get_index_data()
    
    # 生成投资建议
    advice = ""
    s = sentiment["score"]
    if s >= 80:
        advice = "市场情绪过热，警惕回调风险，不宜追高"
    elif s >= 65:
        advice = "市场偏暖，可适当参与，注意控制仓位"
    elif s >= 45:
        advice = "市场情绪中性，精选个股，波段操作"
    elif s >= 30:
        advice = "市场偏冷，精选优质标的，逢低布局"
    else:
        advice = "市场恐慌，关注超跌反弹机会，严格止损"
    
    return {
        "sentiment": sentiment,
        "breadth": breadth,
        "limit": limit,
        "indices": indices,
        "advice": advice,
        "time": datetime.now().strftime("%H:%M:%S"),
    }


if __name__ == "__main__":
    # 测试
    test_quotes = {
        "000001": {"change_pct": 1.5, "turnover_pct": 2.1},
        "000002": {"change_pct": -2.3, "turnover_pct": 1.8},
        "000003": {"change_pct": 3.1, "turnover_pct": 3.5},
        "000004": {"change_pct": -0.5, "turnover_pct": 0.8},
        "000005": {"change_pct": 0.2, "turnover_pct": 1.2},
        "000006": {"change_pct": 9.9, "turnover_pct": 8.5},
        "000007": {"change_pct": -5.2, "turnover_pct": 2.3},
        "000008": {"change_pct": 1.8, "turnover_pct": 1.5},
    }
    
    print("=== 市场情绪 ===")
    s = get_sentiment_index(test_quotes)
    print(f"  恐慌贪婪指数: {s['score']} ({s['level_text']})")
    for f in s.get('factors', []):
        print(f"    {f['name']}: {f['value']} → {f['score']}分 (权重{f['weight']}%)")
    
    print("\n=== 涨跌统计 ===")
    b = get_market_breadth(test_quotes)
    print(f"  涨{b['up_count']} / 跌{b['down_count']} / 平{b['flat_count']} = 涨跌比{b['up_ratio']}%")
    
    print("\n=== 涨跌停 ===")
    l = get_limit_stats(test_quotes)
    print(f"  涨停{l['limit_up']} / 跌停{l['limit_down']}")
    
    print("\n=== 指数 ===")
    idx = get_index_data()
    for k, v in idx.items():
        if not isinstance(v, dict):
            continue
        print(f"  {v['name']}: {v['price']} ({v['change_pct']}%) 成交{v['volume_yi']}亿")
