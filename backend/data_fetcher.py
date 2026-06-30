"""
股票监控系统 - 数据抓取模块
支持: 腾讯财经(实时行情)、mootdx(K线)、stockstats(技术指标)
"""
import urllib.request
import json
import time
import random
from datetime import datetime
from collections import defaultdict

try:
    from mootdx.quotes import Quotes
    MOOTDX_AVAILABLE = True
    _tdx_client = None
except ImportError:
    MOOTDX_AVAILABLE = False
    print("[WARN] mootdx未安装，K线数据将使用百度股市通替代")

try:
    import stockstats
    STOCKSTATS_AVAILABLE = True
except ImportError:
    STOCKSTATS_AVAILABLE = False
    print("[WARN] stockstats未安装，技术指标计算受限")

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _get_prefix(code):
    """获取市场前缀"""
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith("8"):
        return "bj"
    else:
        return "sz"


# ============ 腾讯财经实时行情 ============

def fetch_tencent_quotes(codes):
    """
    通过腾讯财经API批量获取实时行情（不封IP）
    返回: {code: {name, price, change_pct, ...}}
    """
    if not codes:
        return {}

    prefixed = []
    for c in codes:
        prefixed.append(f"{_get_prefix(c)}{c}")

    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = resp.read().decode("gbk")
    except Exception as e:
        print(f"[ERROR] 腾讯财经请求失败: {e}")
        return {}

    result = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        try:
            key = line.split("=")[0].split("_")[-1]
            vals = line.split('"')[1].split("~")
            if len(vals) < 53:
                continue
            code = key[2:]
            result[code] = {
                "name": vals[1],
                "price": float(vals[3]) if vals[3] else 0,
                "last_close": float(vals[4]) if vals[4] else 0,
                "open": float(vals[5]) if vals[5] else 0,
                "high": float(vals[33]) if vals[33] else 0,
                "low": float(vals[34]) if vals[34] else 0,
                "change_amt": round(float(vals[31]) if vals[31] else 0, 2),
                "change_pct": round(float(vals[32]) if vals[32] else 0, 2),
                "volume": float(vals[6]) if vals[6] else 0,  # 成交量(手)
                "amount_wan": float(vals[37]) if vals[37] else 0,  # 成交额(万)
                "turnover_pct": round(float(vals[38]) if vals[38] else 0, 2),
                "pe_ttm": round(float(vals[39]) if vals[39] else 0, 2),
                "amplitude_pct": round(float(vals[43]) if vals[43] else 0, 2),
                "mcap_yi": round(float(vals[44]) if vals[44] else 0, 2),  # 总市值(亿)
                "float_mcap_yi": round(float(vals[45]) if vals[45] else 0, 2),
                "pb": round(float(vals[46]) if vals[46] else 0, 2),
                "limit_up": float(vals[47]) if vals[47] else 0,
                "limit_down": float(vals[48]) if vals[48] else 0,
                "vol_ratio": round(float(vals[49]) if vals[49] else 0, 2),
                "pe_static": round(float(vals[52]) if vals[52] else 0, 2),
                "bid1": float(vals[9]) if vals[9] else 0,
                "bid1_vol": float(vals[10]) if vals[10] else 0,
                "ask1": float(vals[19]) if vals[19] else 0,
                "ask1_vol": float(vals[20]) if vals[20] else 0,
            }
        except (IndexError, ValueError) as e:
            continue

    return result


# ============ K线数据 ============

def fetch_kline_mootdx(code, category=4, count=250):
    """
    通过mootdx获取K线数据
    category: 4=日线, 5=周线, 6=月线
    """
    if not MOOTDX_AVAILABLE:
        return fetch_kline_baidu(code, category, count)

    global _tdx_client
    if _tdx_client is None:
        try:
            _tdx_client = Quotes.factory(market='std', timeout=10)
        except Exception as e:
            print(f"[ERROR] mootdx连接失败: {e}")
            return fetch_kline_baidu(code, category, count)

    try:
        market = 0 if code.startswith(("0", "3", "8")) else 1
        klines = _tdx_client.bars(symbol=code, category=category, offset=count, market=market)
        if klines is None or len(klines) == 0:
            return []

        result = []
        for _, row in klines.iterrows():
            result.append({
                "date": str(row.get("datetime", ""))[:10],
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": float(row.get("vol", 0)),
                "amount": float(row.get("amount", 0)),
            })
        return result
    except Exception as e:
        print(f"[ERROR] mootdx K线请求失败: {e}")
        return fetch_kline_baidu(code, category, count)


def fetch_kline_baidu(code, category=4, count=250):
    """
    通过腾讯财经/百度获取K线数据
    优先使用同花顺 API (更稳定)
    """
    return _fetch_kline_tencent(code, category, count)


def _get_tencent_code(code):
    """转换代码为腾讯格式"""
    if code.startswith(('6', '9')):
        return f"sh{code}"
    elif code.startswith(('0', '3', '8')):
        return f"sz{code}"
    return code


def _fetch_kline_tencent(code, category=4, count=250):
    """通过同花顺获取K线数据"""
    return _fetch_kline_10jqka(code, category, count)


# 同花顺 period 映射: mootdx category → 同花顺路径码
_CATEGORY_TO_10JQKA = {4: "01", 5: "02", 6: "03"}


def _fetch_kline_10jqka(code, category=4, count=250):
    """通过同花顺 API 获取K线数据 (支持日/周/月)"""
    period_code = _CATEGORY_TO_10JQKA.get(category, "01")
    
    # 同花顺 URL 格式: /v2/line/hs_{code}/01/last.js  (01=日, 02=周, 03=月)
    url = f"http://d.10jqka.com.cn/v2/line/hs_{code}/{period_code}/last.js"
    headers = {
        "User-Agent": UA,
        "Referer": "http://www.10jqka.com.cn/",
    }
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        text = r.text
        
        # 解析 JSONP: quotebridge_v2_line_xxx_last({...})
        prefix = f"quotebridge_v2_line_hs_{code}_{period_code}_last("
        json_str = text
        if text.startswith(prefix):
            json_str = text[len(prefix):-1]  # Remove prefix and trailing )
        
        data = json.loads(json_str)
        raw_data = data.get("data", "")
        
        if not raw_data:
            return []
        
        rows = raw_data.split(";")
        result = []
        for row in rows:
            if not row:
                continue
            parts = row.split(",")
            if len(parts) >= 7:
                try:
                    result.append({
                        "date": parts[0],
                        "open": float(parts[1]),
                        "high": float(parts[2]),
                        "low": float(parts[3]),
                        "close": float(parts[4]),
                        "volume": float(parts[5]),
                        "amount": float(parts[6]) / 10000,  # 转万元
                    })
                except (ValueError, IndexError):
                    continue
        
        return result[-count:] if len(result) > count else result
    except Exception as e:
        print(f"[WARN] 同花顺K线请求失败: {e}")
        return []


def _get_eastmoney_code(code):
    """转换为东方财富代码格式"""
    if code.startswith('6'):
        return f"1.{code}"
    elif code.startswith('0') or code.startswith('3'):
        return f"0.{code}"
    return code


def _fetch_kline_eastmoney(code, category=4, count=250):
    """通过东方财富 API 获取K线 (支持日/周/月)"""
    # klt: 101=日线, 102=周线, 103=月线
    klt_map = {4: "101", 5: "102", 6: "103"}
    klt = klt_map.get(category, "101")
    
    import requests as req
    secid = _get_eastmoney_code(code)
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": klt,
        "fqt": "1",
        "end": "20500101",
        "lmt": count,
    }
    headers = {
        "User-Agent": UA,
        "Referer": "https://quote.eastmoney.com/",
    }
    
    try:
        r = req.get(url, params=params, headers=headers, timeout=10)
        data = r.json()
        
        if data.get("data") is None or data["data"].get("klines") is None:
            return []
        
        klines = data["data"]["klines"]
        result = []
        for row in klines:
            parts = row.split(",")
            if len(parts) >= 11:
                try:
                    result.append({
                        "date": parts[0],
                        "open": float(parts[1]),
                        "close": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                        "volume": float(parts[5]),
                        "amount": float(parts[6]) / 10000,
                        "change_pct": float(parts[8]) if parts[8] != '-' else 0,
                    })
                except (ValueError, IndexError):
                    continue
        
        return result[-count:] if len(result) > count else result
    except Exception as e:
        print(f"[WARN] 东方财富K线请求失败: {e}")
        return []


def _fetch_kline_tencent_fallback(code, count=250):
    """腾讯财经K线备用方案 → 使用百度"""
    return _fetch_kline_baidu_fallback(code, count)


def _fetch_kline_baidu_fallback(code, count=250):
    """
    百度股市通K线备用方案
    """
    pre = _get_prefix(code)
    url = "https://finance.pae.baidu.com/selfselect/getstockquotation"
    params = {
        "all": "1", "isIndex": "false", "isBk": "false", "isBlock": "false",
        "isFutures": "false", "isStock": "true", "newFormat": "1",
        "group": "quotation_kline_ab", "finClientType": "pc",
        "code": f"{pre}{code}", "start_time": "", "ktype": "1",
    }
    headers = {
        "User-Agent": UA,
        "Accept": "application/vnd.finance-web.v1+json",
        "Origin": "https://gushitong.baidu.com",
        "Referer": "https://gushitong.baidu.com/",
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        d = r.json()
        
        # 兼容不同的返回格式
        md = None
        if isinstance(d, dict):
            result = d.get("Result", {})
            if isinstance(result, dict):
                md = result.get("newMarketData", {})
            elif isinstance(result, list) and len(result) > 0:
                md = result[0] if isinstance(result[0], dict) else {}
        
        if not md or not isinstance(md, dict):
            return []
            
        keys = md.get("keys", [])
        if not keys:
            return []
            
        market_data = md.get("marketData", "")
        if not market_data:
            return []
        
        rows_raw = market_data.split(";") if isinstance(market_data, str) else []
        
        result = []
        time_idx = keys.index("time") if "time" in keys else 0
        open_idx = keys.index("open") if "open" in keys else 1
        high_idx = keys.index("high") if "high" in keys else 2
        low_idx = keys.index("low") if "low" in keys else 3
        close_idx = keys.index("close") if "close" in keys else 4
        vol_idx = keys.index("volume") if "volume" in keys else 5
        amt_idx = keys.index("amount") if "amount" in keys else 6
        
        for row in rows_raw:
            if not row:
                continue
            parts = row.split(",")
            if len(parts) > max(time_idx, close_idx):
                try:
                    result.append({
                        "date": parts[time_idx],
                        "open": float(parts[open_idx]) if parts[open_idx] else 0,
                        "high": float(parts[high_idx]) if parts[high_idx] else 0,
                        "low": float(parts[low_idx]) if parts[low_idx] else 0,
                        "close": float(parts[close_idx]) if parts[close_idx] else 0,
                        "volume": float(parts[vol_idx]) if len(parts) > vol_idx and parts[vol_idx] else 0,
                        "amount": float(parts[amt_idx]) if len(parts) > amt_idx and parts[amt_idx] else 0,
                    })
                except (ValueError, IndexError):
                    continue
        return result[-count:] if len(result) > count else result
    except Exception as e:
        print(f"[ERROR] 百度K线备用请求失败: {e}")
        return []


def fetch_kline(code, period="day", count=250):
    """统一的K线获取入口, 支持 day/week/month"""
    category_map = {"day": 4, "week": 5, "month": 6}
    category = category_map.get(period, 4)
    
    # 1. 优先尝试 mootdx (所有周期)
    if MOOTDX_AVAILABLE:
        result = fetch_kline_mootdx(code, category, count)
        if result:
            return result
    
    # 2. 尝试东方财富 (push2his 支持日/周/月, 本环境可用)
    result = _fetch_kline_eastmoney(code, category, count)
    if result:
        return result
    
    # 3. 使用同花顺 (仅日线)
    if category == 4:
        result = _fetch_kline_10jqka(code, category, count)
        if result:
            return result
    
    # 4. 最后尝试 mootdx 降级 (日线)
    if MOOTDX_AVAILABLE:
        return fetch_kline_mootdx(code, 4, count)
    
    return []


# ============ 技术指标计算 ============

def calculate_ma(data, periods=[5, 10, 20, 60, 120, 250]):
    """计算移动均线"""
    closes = [d["close"] for d in data]
    result = {}
    for p in periods:
        if p > len(closes):
            continue
        ma_values = []
        for i in range(len(closes)):
            if i < p - 1:
                ma_values.append(None)
            else:
                ma_values.append(round(sum(closes[i-p+1:i+1]) / p, 2))
        result[f"ma{p}"] = ma_values
    return result


def calculate_macd(data, fast=12, slow=26, signal=9):
    """计算MACD指标"""
    closes = [d["close"] for d in data]
    if len(closes) < slow + signal:
        return {"dif": [], "dea": [], "bar": []}

    ema_fast = [closes[0]]
    ema_slow = [closes[0]]
    for i in range(1, len(closes)):
        ema_fast.append(ema_fast[-1] * (fast-1)/(fast+1) + closes[i] * 2/(fast+1))
        ema_slow.append(ema_slow[-1] * (slow-1)/(slow+1) + closes[i] * 2/(slow+1))

    dif = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]
    dea = [dif[0]]
    for i in range(1, len(dif)):
        dea.append(dea[-1] * (signal-1)/(signal+1) + dif[i] * 2/(signal+1))
    bar = [(dif[i] - dea[i]) * 2 for i in range(len(dif))]

    return {
        "dif": [round(x, 4) for x in dif],
        "dea": [round(x, 4) for x in dea],
        "bar": [round(x, 4) for x in bar],
    }


def calculate_rsi(data, period=14):
    """计算RSI指标"""
    closes = [d["close"] for d in data]
    if len(closes) < period + 1:
        return []

    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(diff if diff > 0 else 0)
        losses.append(-diff if diff < 0 else 0)

    rsi_values = [None] * period
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        rsi_values.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi_values.append(round(100 - 100 / (1 + rs), 2))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(round(100 - 100 / (1 + rs), 2))

    # 补齐前面的None
    while len(rsi_values) < len(closes):
        rsi_values.insert(0, None)
    return rsi_values[:len(closes)]


def calculate_boll(data, period=20, std_mult=2):
    """计算布林带"""
    closes = [d["close"] for d in data]
    if len(closes) < period:
        return {"upper": [], "mid": [], "lower": []}

    import math
    upper, mid, lower = [], [], []
    for i in range(len(closes)):
        if i < period - 1:
            upper.append(None)
            mid.append(None)
            lower.append(None)
        else:
            window = closes[i-period+1:i+1]
            ma = sum(window) / period
            variance = sum((x - ma) ** 2 for x in window) / period
            std = math.sqrt(variance)
            upper.append(round(ma + std_mult * std, 2))
            mid.append(round(ma, 2))
            lower.append(round(ma - std_mult * std, 2))
    return {"upper": upper, "mid": mid, "lower": lower}


def calculate_kdj(data, period=9):
    """计算KDJ指标"""
    highs = [d["high"] for d in data]
    lows = [d["low"] for d in data]
    closes = [d["close"] for d in data]
    
    if len(closes) < period:
        return {"k": [], "d": [], "j": []}

    k_values, d_values, j_values = [], [], []
    k = 50
    d = 50

    for i in range(len(closes)):
        if i < period - 1:
            k_values.append(None)
            d_values.append(None)
            j_values.append(None)
            continue
        
        highest = max(highs[i-period+1:i+1])
        lowest = min(lows[i-period+1:i+1])
        
        if highest == lowest:
            rsv = 50
        else:
            rsv = (closes[i] - lowest) / (highest - lowest) * 100
        
        k = 2/3 * k + 1/3 * rsv
        d = 2/3 * d + 1/3 * k
        j = 3 * k - 2 * d
        
        k_values.append(round(k, 2))
        d_values.append(round(d, 2))
        j_values.append(round(j, 2))

    return {"k": k_values, "d": d_values, "j": j_values}


def get_technical_indicators(data):
    """计算所有技术指标"""
    result = {}
    
    # MA
    result.update(calculate_ma(data))
    
    # MACD
    result.update(calculate_macd(data))
    
    # RSI
    result["rsi14"] = calculate_rsi(data, 14)
    # RSI6 for short term
    result["rsi6"] = calculate_rsi(data, 6)
    
    # BOLL
    result.update(calculate_boll(data))
    
    # KDJ
    result.update(calculate_kdj(data))
    
    # VWMA (成交量加权均线)
    result["vwma20"] = calculate_vwma(data, 20)
    
    # ATR (平均真实波幅)
    result["atr14"] = calculate_atr(data, 14)
    
    return result


# ============ VWMA 成交量加权均线 ============

def calculate_vwma(data, period=20):
    """
    成交量加权移动均线
    VWMA = Σ(收盘价 × 成交量) / Σ(成交量)
    成交量大的K线权重更高，更真实反映主力成本
    data: [{close, volume}, ...]
    返回: [vwma, ...] 前 period-1 个为 None
    """
    result = []
    for i in range(len(data)):
        if i < period - 1:
            result.append(None)
            continue
        total_weight = 0
        total_value = 0
        for j in range(i - period + 1, i + 1):
            vol = data[j].get('volume', 0)
            close = data[j].get('close', 0)
            total_weight += vol
            total_value += close * vol
        if total_weight > 0:
            result.append(round(total_value / total_weight, 2))
        else:
            result.append(None)
    return result


# ============ ATR 平均真实波幅 ============

def calculate_atr(data, period=14):
    """
    平均真实波幅 (Average True Range)
    TR = max(high-low, |high-prev_close|, |low-prev_close|)
    ATR = TR 的 period 周期移动平均
    用于动态止损/止盈：止损 = 入口价 - N×ATR
    
    data: [{high, low, close}, ...]
    返回: [atr, ...] 前 period 个为 None
    """
    tr_list = []
    for i in range(len(data)):
        high = data[i].get('high', data[i].get('close', 0))
        low = data[i].get('low', data[i].get('close', 0))
        prev_close = data[i-1].get('close', data[i].get('close', 0)) if i > 0 else data[i].get('close', 0)
        
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        tr_list.append(tr)
    
    # 计算 period 周期移动平均
    result = []
    for i in range(len(tr_list)):
        if i < period - 1:
            result.append(None)
            continue
        avg = sum(tr_list[i - period + 1 : i + 1]) / period
        result.append(round(avg, 2))
    return result


# ============ K线存储 ============

def sync_kline_to_db(code, count=365):
    """同步个股K线数据到数据库"""
    from database import save_price_history
    
    data = fetch_kline(code, "day", count)
    if not data:
        return 0
    
    records = []
    for d in data:
        change_pct = 0
        if d["close"] != 0 and len(records) > 0:
            prev_close = records[-1][4]  # close of previous record
            if prev_close != 0:
                change_pct = round((d["close"] - prev_close) / prev_close * 100, 2)
        
        records.append((
            code, d["date"],
            d["open"], d["high"], d["low"], d["close"],
            d.get("volume", 0), d.get("amount", 0), change_pct
        ))
    
    save_price_history(records)
    return len(records)


# ============ 股票搜索 ============

def search_stock(keyword):
    """搜索股票（通过东财搜索接口）"""
    try:
        url = "https://searchapi.eastmoney.com/api/suggest/get"
        params = {
            "input": keyword,
            "type": 14,
            "token": "D43BF722C8E33BDC906FB84D85E326E8",
            "count": 20,
        }
        r = requests.get(url, params=params, timeout=10)
        d = r.json()
        results = []
        for item in d.get("QuotationCodeTable", {}).get("Data", []):
            code = item.get("Code", "")
            market_id = item.get("MktNum", "")
            # 只保留A股
            if market_id in ("", " ", "0", "1") or not market_id:
                if len(code) == 6 and code.isdigit():
                    results.append({
                        "code": code,
                        "name": item.get("Name", ""),
                        "market": "SH" if code.startswith(("6", "9")) else ("BJ" if code.startswith("8") else "SZ"),
                    })
        return results[:10]
    except Exception as e:
        print(f"[ERROR] 股票搜索失败: {e}")
        return []


if __name__ == '__main__':
    # 测试
    quotes = fetch_tencent_quotes(["600519", "000858"])
    for code, q in quotes.items():
        print(f"{q['name']}({code}): {q['price']} 涨跌幅={q['change_pct']}% PE={q['pe_ttm']}")
    
    # 测试K线
    kline = fetch_kline("600519", "day", 10)
    if kline:
        print(f"\nK线数据 ({len(kline)}条):")
        for k in kline[-3:]:
            print(f"  {k['date']}: O={k['open']} H={k['high']} L={k['low']} C={k['close']}")
