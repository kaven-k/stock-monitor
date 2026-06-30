"""
市场情绪指标模块 v3.1
- 涨跌比 / 涨跌停统计（全市场东方财富数据）
- 市场成交量（基于 Sina API 指数数据）
- 恐慌贪婪指数
- 市场温度计 (0-100)

数据源: 东方财富 emdatah5 + Sina hq.sinajs.cn (全市场，不限于监控股票)

升级点(v3.1):
- 修复全市场API分页：每页100条，遍历全部5534只A股
- 涨跌停从全量数据统计，不再采样
- 增加5分钟缓存，避免重复请求
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


def _fetch_one_page(url, base_params, page, page_size=100):
    """抓取单页数据，返回 change_pct 列表（float）"""
    params = base_params.copy()
    params["pz"] = str(page_size)
    params["pn"] = str(page)
    try:
        data = _emdatah5_get(url, params, timeout=8)
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


def _fetch_all_market_changes(force_refresh=False):
    """
    分页遍历全市场所有A股，返回所有股票的涨跌幅列表
    使用 5 线程并行 + 5 分钟缓存
    每页 100 条，遍历全部 ~5500 只股票
    """
    with _full_market_cache["lock"]:
        now = time.time()
        if not force_refresh and _full_market_cache["data"] is not None and (now - _full_market_cache["timestamp"]) < _CACHE_TTL:
            return _full_market_cache["data"]

    url = "https://push2.eastmoney.com/api/qt/clist/get"
    base_params = {
        "fid": "f3", "po": "1", "np": "1",
        "fltt": "2", "invt": "2",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f3",
    }

    # 第一步：获取总数
    params_count = base_params.copy()
    params_count["pz"] = "1"
    params_count["pn"] = "1"
    try:
        count_data = _emdatah5_get(url, params_count, timeout=8)
        total = count_data.get("data", {}).get("total", 0) if count_data else 0
    except Exception:
        total = 0

    if total == 0:
        return None

    page_size = 100
    total_pages = (total + page_size - 1) // page_size

    # 第二步：分页并行抓取
    all_changes = []
    # 用线程池并行抓取，最多 5 个并发
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        for page in range(1, total_pages + 1):
            future = executor.submit(_fetch_one_page, url, base_params, page, page_size)
            futures[future] = page

        for future in as_completed(futures):
            page = futures[future]
            try:
                changes = future.result()
                all_changes.extend(changes)
            except Exception as e:
                print(f"[Sentiment] 第{page}页抓取失败: {e}")

    result = all_changes if all_changes else None

    # 缓存
    with _full_market_cache["lock"]:
        _full_market_cache["data"] = result
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

        return {
            "up_count": up, "down_count": down, "flat_count": flat,
            "total": total, "up_ratio": round(up / total * 100, 1) if total else 0,
            "time": datetime.now().strftime("%H:%M:%S"),
            "source": f"全市场(东财) {total}只A股",
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

        return {
            "limit_up": limit_up, "limit_down": limit_down,
            "net": limit_up - limit_down,
            "time": datetime.now().strftime("%H:%M:%S"),
            "source": f"全市场(东财) {len(all_changes)}只A股",
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
    
    基于监控股票数据计算：
    1. 涨跌比 (权重 40%)
    2. 涨停跌停差 (权重 25%)
    3. 平均涨跌幅 (权重 20%)
    4. 成交量活跃度 (权重 15%)
    
    Returns:
        {score, level, level_text, level_color, factors, time}
    """
    if not quotes:
        return {
            "score": 50, "level": "neutral",
            "level_text": "无数据 ⚖️", "level_color": "#868e96",
            "factors": [],
            "time": datetime.now().strftime("%H:%M:%S"),
        }
    
    score = 50.0
    factors = []
    
    # 1. 涨跌比
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
    
    # 2. 涨停跌停差
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
    
    # 3. 平均涨跌幅
    all_changes = [q.get("change_pct", 0) for q in quotes.values()]
    avg_change = round(sum(all_changes) / len(all_changes), 2) if all_changes else 0
    # 映射：-5%→0, 0%→50, +5%→100
    avg_score = min(100, max(0, 50 + avg_change * 10))
    factors.append({
        "name": "平均涨幅",
        "value": f"{avg_change}%",
        "score": round(avg_score, 1),
        "weight": 20,
    })
    score = score * 0.80 + avg_score * 0.20
    
    # 4. 活跃度（换手率均值）
    turnovers = [q.get("turnover_pct", 0) for q in quotes.values() if q.get("turnover_pct", 0) > 0]
    avg_turnover = round(sum(turnovers) / len(turnovers), 1) if turnovers else 0
    # 换手率 < 1% 偏冷, 1-3% 正常, > 5% 偏热
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
