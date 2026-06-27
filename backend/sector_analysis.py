"""
板块分析模块 v3.0
- 全市场行业/概念板块排名（带涨跌幅+资金流向）
- 主线板块识别
- 板块成份股

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


def get_sector_ranking(sector_type="all", top_n=20):
    """
    获取全市场板块排名（含资金流向数据）
    
    行业 fs=m:90+t:2 / 概念 fs=m:90+t:3
    
    Returns:
        {top: [{code, name, change_pct, fund_flow_yi, ...}], ...}
    """
    result = {"top": [], "bottom": [], "total": 0, "time": datetime.now().strftime("%H:%M:%S")}
    
    if sector_type == "industry":
        fs_filter = "m:90+t:2"
    elif sector_type == "concept":
        fs_filter = "m:90+t:3"
    else:
        # "all": 先取概念+行业各top, 再合并排序
        industry = get_sector_ranking("industry", top_n)
        concept = get_sector_ranking("concept", top_n)
        all_items = industry["top"] + concept["top"]
        all_items.sort(key=lambda x: x["change_pct"], reverse=True)
        return {
            "top": all_items[:top_n],
            "bottom": sorted(all_items, key=lambda x: x["change_pct"])[:top_n],
            "total": len(all_items),
            "time": datetime.now().strftime("%H:%M:%S"),
        }
    
    try:
        data = _em_h5_get({
            "pn": "1",
            "pz": str(max(top_n * 5, 100)),
            "po": "1",
            "fid": "f62",
            "fs": fs_filter,
            "fields": "f1,f2,f3,f4,f12,f13,f14,f128,f62,f140",
        })
        
        if not data or not data.get("diff"):
            return result
        
        items = []
        for item in data["diff"]:
            if item.get("f1") != 2:  # f1=2 表示板块类型
                continue
            code = item.get("f12", "")
            if not code or not code.startswith("BK"):
                continue
            
            chg = float(item.get("f3", 0))
            fund_flow = float(item.get("f62", 0)) / 100000000  # 元 → 亿
            items.append({
                "code": code,
                "name": item.get("f14", ""),
                "change_pct": round(chg, 2),
                "fund_flow_yi": round(fund_flow, 2),
                "fund_flow_str": f"{'+' if fund_flow >= 0 else ''}{fund_flow:.2f}亿",
                "index_price": float(item.get("f2", 0)),
                "leader_name": item.get("f128", ""),
                "leader_code": item.get("f140", ""),
                "sector_type": sector_type if sector_type != "all" else ("概念" if fs_filter.endswith("3") else "行业"),
            })
        
        items.sort(key=lambda x: x["change_pct"], reverse=True)
        
        return {
            "top": items[:top_n],
            "bottom": items[-top_n:][::-1] if len(items) >= top_n else items[::-1],
            "total": len(items),
            "time": datetime.now().strftime("%H:%M:%S"),
        }
    except Exception as e:
        result["error"] = str(e)
        return result


def get_main_sectors(days=5, top_n=10):
    """
    识别主线板块
    
    主线判断标准:
    1. 涨幅靠前 (change_pct > 0)
    2. 资金净流入 (fund_flow_yi > 0)
    3. 综合评分 = 涨幅排名 + 资金排名
    
    Returns:
        {industry_main: [...], concept_main: [...], time: str}
    """
    def compute_mains(sector_type):
        ranking = get_sector_ranking(sector_type, 50)
        items = ranking.get("top", [])
        
        # 综合评分：涨幅排名 + 资金流入排名
        # 涨幅排名（位置越前越优）
        for i, item in enumerate(items):
            item["rank_change"] = i + 1
        
        # 资金排名
        by_fund = sorted(items, key=lambda x: x.get("fund_flow_yi", 0), reverse=True)
        for i, item in enumerate(by_fund):
            item["rank_fund"] = i + 1
        
        # 综合评分 (越低越好)
        for item in items:
            item["main_score"] = item.get("rank_change", 999) + item.get("rank_fund", 999)
        
        # 主线过滤: 涨幅>0 且 资金>0
        mains = [it for it in items if it["change_pct"] > 0 and it.get("fund_flow_yi", 0) > 0]
        mains.sort(key=lambda x: x["main_score"])
        
        mains = mains[:top_n]
        for m in mains:
            m["main_level"] = "🔥 强主线" if m["main_score"] <= 10 else ("⭐ 次主线" if m["main_score"] <= 30 else "观察")
        
        return mains
    
    try:
        return {
            "industry_main": compute_mains("industry"),
            "concept_main": compute_mains("concept"),
            "time": datetime.now().strftime("%H:%M:%S"),
        }
    except Exception as e:
        return {
            "industry_main": [], "concept_main": [],
            "time": datetime.now().strftime("%H:%M:%S"),
            "error": str(e),
        }


def get_sector_detail(sector_code):
    """
    获取板块详情（成份股）
    
    fs=b:BK{code} 获取板块内个股
    
    Returns:
        {sector_code, name, constituents: [...], time}
    """
    # 确保code是BK格式
    if not sector_code.startswith("BK"):
        sector_code = f"BK{sector_code}" if sector_code.isdigit() else sector_code
    
    try:
        data = _em_h5_get({
            "pn": "1",
            "pz": "200",
            "po": "0",
            "fid": "f3",
            "fs": f"b:{sector_code}",
            "fields": "f2,f3,f12,f14,f62",
        })
        
        if not data or not data.get("diff"):
            return {
                "sector_code": sector_code,
                "name": sector_code,
                "constituents": [],
                "total": 0,
                "time": datetime.now().strftime("%H:%M:%S"),
            }
        
        constituents = []
        for item in data["diff"]:
            if item.get("f12", "").startswith("BK"):  # 排除嵌套板块
                continue
            
            chg = float(item.get("f3", 0))
            constituents.append({
                "code": item.get("f12", ""),
                "name": item.get("f14", ""),
                "price": float(item.get("f2", 0)),
                "change_pct": round(chg, 2),
                "fund_flow_yi": round(float(item.get("f62", 0)) / 100000000, 2),
            })
        
        constituents.sort(key=lambda x: x["change_pct"], reverse=True)
        
        return {
            "sector_code": sector_code,
            "name": sector_code,
            "constituents": constituents,
            "total": len(constituents),
            "time": datetime.now().strftime("%H:%M:%S"),
        }
    except Exception as e:
        return {
            "sector_code": sector_code,
            "name": sector_code,
            "constituents": [],
            "total": 0,
            "error": str(e),
            "time": datetime.now().strftime("%H:%M:%S"),
        }


if __name__ == "__main__":
    print("=== 行业板块 TOP10（按涨幅）===")
    ranking = get_sector_ranking("industry", 10)
    for i, r in enumerate(ranking["top"]):
        sign = "+" if r["change_pct"] >= 0 else ""
        print(f"  {i+1}. {r['name']}: {sign}{r['change_pct']}%  资金:{r['fund_flow_str']}  领涨:{r['leader_name']}")
    
    print("\n=== 概念板块 TOP10（按涨幅）===")
    ranking = get_sector_ranking("concept", 10)
    for i, r in enumerate(ranking["top"]):
        sign = "+" if r["change_pct"] >= 0 else ""
        print(f"  {i+1}. {r['name']}: {sign}{r['change_pct']}%  资金:{r['fund_flow_str']}")
    
    print("\n=== 主线板块 ===")
    mains = get_main_sectors(top_n=5)
    print("  行业主线:")
    for m in mains["industry_main"][:5]:
        print(f"    {m['name']}: {m['main_level']} (涨幅+{m['change_pct']}%, 资金+{m['fund_flow_yi']:.1f}亿)")
    print("  概念主线:")
    for m in mains["concept_main"][:5]:
        print(f"    {m['name']}: {m['main_level']} (涨幅+{m['change_pct']}%, 资金+{m['fund_flow_yi']:.1f}亿)")
    
    print("\n=== 板块详情（面板 BK1335）===")
    detail = get_sector_detail("BK1335")
    print(f"  共 {detail['total']} 只股票")
    for s in detail["constituents"][:5]:
        print(f"    {s['name']}({s['code']}): {s['change_pct']}%")
