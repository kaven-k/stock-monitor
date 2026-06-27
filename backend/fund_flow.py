"""
资金流向模块 v3.0
- 全市场板块资金流向排名（主力净流入）
- 行业/概念维度
- 北向资金（待接入）

数据源: emdatah5.eastmoney.com 资金流向API (稳定可靠)
"""
import requests
import time
from datetime import datetime

UA = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
REFERER = "https://emdatah5.eastmoney.com/dc/zjlx/block"
BASE_URL = "https://emdatah5.eastmoney.com/dc/ZJLX/getZDYLBData"


def _em_h5_get(params, timeout=15):
    """调用 emdatah5 资金流向 API"""
    h = {
        "User-Agent": UA,
        "Referer": REFERER,
        "Accept": "application/json",
    }
    for attempt in range(3):
        try:
            r = requests.get(BASE_URL, params=params, headers=h, timeout=timeout)
            data = r.json()
            if data.get("rc") == 0 and data.get("data"):
                return data["data"]
            if attempt < 2:
                time.sleep(1)
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(1)
    return None


def get_sector_fund_flow(sector_type="all", top_n=20):
    """
    获取全市场板块资金流向排名
    
    Args:
        sector_type: "industry" | "concept" | "all"
        top_n: 返回前N条
    
    Returns:
        {top_inflow: [...], top_outflow: [...], total: int, time: str}
    """
    result = {"top_inflow": [], "top_outflow": [], "total": 0, "time": datetime.now().strftime("%H:%M:%S")}
    
    if sector_type == "all":
        # 合并行业+概念
        ind = get_sector_fund_flow("industry", top_n)
        con = get_sector_fund_flow("concept", top_n)
        combined = (ind["top_inflow"] or []) + (con["top_inflow"] or [])
        combined.sort(key=lambda x: x.get("net_flow_yi", 0), reverse=True)
        return {
            "top_inflow": combined[:top_n],
            "top_outflow": sorted(combined, key=lambda x: x.get("net_flow_yi", 0))[:top_n],
            "total": len(combined),
            "time": datetime.now().strftime("%H:%M:%S"),
        }
    
    fs_filter = "m:90+t:2" if sector_type == "industry" else "m:90+t:3"
    
    try:
        data = _em_h5_get({
            "pn": "1",
            "pz": str(max(top_n * 5, 100)),
            "po": "1",
            "fid": "f62",
            "fs": fs_filter,
            "fields": "f1,f2,f3,f4,f12,f14,f128,f62,f140",
        })
        
        if not data or not data.get("diff"):
            return result
        
        items = []
        for item in data["diff"]:
            if item.get("f1") != 2:
                continue
            code = item.get("f12", "")
            if not code or not code.startswith("BK"):
                continue
            
            net_flow = float(item.get("f62", 0)) / 100000000
            items.append({
                "code": code,
                "name": item.get("f14", ""),
                "net_flow_yi": round(net_flow, 2),
                "net_flow_str": f"{'+' if net_flow >= 0 else ''}{net_flow:.2f}亿",
                "change_pct": round(float(item.get("f3", 0)), 2),
                "leader_name": item.get("f128", ""),
                "leader_code": item.get("f140", ""),
                "sector_type": sector_type,
            })
        
        items.sort(key=lambda x: x["net_flow_yi"], reverse=True)
        
        return {
            "top_inflow": items[:top_n],
            "top_outflow": items[-top_n:][::-1] if len(items) >= top_n else items[::-1],
            "total": len(items),
            "time": datetime.now().strftime("%H:%M:%S"),
        }
    except Exception as e:
        result["error"] = str(e)
        return result


def get_northbound_flow():
    """
    北向资金（暂不可用）
    
    注意: 北向资金API目前在该环境中不可达，
    待找到可用数据源后再接入。
    """
    return {
        "sh_net_yi": 0,
        "sz_net_yi": 0,
        "total_net_yi": 0,
        "time": datetime.now().strftime("%H:%M:%S"),
        "note": "北向资金数据源暂不可用",
    }


def get_stock_fund_flow(code, days=5):
    """
    个股资金流向（暂用板块API近似，待接入个股级别API）
    """
    return {
        "code": code,
        "net_flow_yi": 0,
        "time": datetime.now().strftime("%H:%M:%S"),
        "note": "个股资金流暂不可用",
    }


if __name__ == "__main__":
    print("=== 行业资金流入 TOP5 ===")
    flow = get_sector_fund_flow("industry", 20)
    for i, f in enumerate(flow["top_inflow"][:5]):
        print(f"  {i+1}. {f['name']}: {f['net_flow_str']} (涨幅{f['change_pct']}%)")
    
    print("\n=== 行业资金流出 TOP5 ===")
    for i, f in enumerate(flow["top_outflow"][:5]):
        print(f"  {i+1}. {f['name']}: {f['net_flow_str']} (涨幅{f['change_pct']}%)")
    
    print("\n=== 概念资金流入 TOP5 ===")
    flow = get_sector_fund_flow("concept", 20)
    for i, f in enumerate(flow["top_inflow"][:5]):
        print(f"  {i+1}. {f['name']}: {f['net_flow_str']} (涨幅{f['change_pct']}%)")
