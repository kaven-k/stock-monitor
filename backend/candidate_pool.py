"""
选股候选池构建 —— 主线扩散链（v7 新增）

解决的问题：
  原选股候选池 = 监控列表 + 板块龙头（通常仅几十只），全市场数据虽已抓取
  但只用于统计涨跌家数（market_sentiment 只返回涨跌幅数字，无代码），
  导致"全市场选股"名不副实，监控列表为空时候选池枯竭。

本模块实现「先主线 → 再扩散」的两段式候选池构建：
  第0步 全市场快照      market_snapshot.get_full_market_snapshot()  ~5400 只带 code 行情
  第1步 定主线板块      sector_analysis.get_main_sectors()         涨幅+资金双排名
  第2步 扩散到成份股    sector_analysis.get_sector_detail()        主线板块成分股
  第3步 硬过滤          SCREENING_CONFIG(权限) + SELECTION_CONFIG(可买性)
  第4步 排序打分        XGBoost 盈利概率 + SignalEngine 技术共振分
  第5步 输出            TOP N 候选（交给 ai_screener 写理由）

设计原则：
  - 任一环节失败都优雅降级，保证候选池不为空（最坏情况退化为全市场量价排序）
  - XGBoost 打分需日K线（14特征），全市场5000+只拉K线不现实，
    因此"先主线再扩散"不仅是逻辑需求，更是性能上的必然
"""
import os
import time
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_monitor.db")

# 默认配置（可由调用方覆盖）
DEFAULT_POOL_CONFIG = {
    "main_sector_top": 5,        # 取前 N 个主线板块（行业+概念各取一半时可调整）
    "max_constituents": 200,     # 扩散后成分股上限
    "max_scored": 60,            # 实际拉K线打分的上限（性能保护）
    "xgb_weight": 0.6,           # XGBoost 盈利概率权重
    "tech_weight": 0.25,         # 技术共振分权重
    "fund_weight": 0.15,         # 资金面（成交额/量比健康度）权重
    "fallback_top_n": 60,        # 降级排序时的候选数
    "min_pool_size": 60,         # 候选池最小规模（不足则从全市场补充）
}


def _merge_config(cfg):
    c = dict(DEFAULT_POOL_CONFIG)
    if cfg:
        c.update(cfg)
    return c


def _get_db_conn():
    try:
        return sqlite3.connect(DB_PATH)
    except Exception as e:
        print(f"[Pool] 数据库连接失败: {e}")
        return None


# ============ 第1步：定主线 ============

def _dedup_sectors(sectors):
    """
    板块去重：东财板块存在同题材多层级/多分支（如「游戏Ⅲ」「游戏Ⅱ」「游戏Ⅰ」），
    名称去掉罗马数字后缀后视为同一主线，只保留评分最优的一个。
    """
    import re
    seen, out = {}, []
    for s in sectors:
        name = s.get("name", "")
        base = re.sub(r"[ⅠⅡⅢⅣⅤ\d]+$", "", name).strip()
        base = base or name
        if base in seen:
            # 保留主线评分更优的
            if s.get("main_score", 999) < seen[base].get("main_score", 999):
                out[out.index(seen[base])] = s
                seen[base] = s
            continue
        seen[base] = s
        out.append(s)
    return out


def pick_main_sectors(max_sectors=5):
    """
    识别当前市场主线板块（涨幅 + 资金双排名）
    返回: [{'sector_code','name','change_pct','fund_flow_yi','main_level',...}, ...]
    """
    try:
        from sector_analysis import get_main_sectors
        mains = get_main_sectors(days=5, top_n=10)
    except Exception as e:
        print(f"[Pool] 主线板块获取失败: {e}")
        return []

    sectors = []
    for key in ("industry_main", "concept_main"):
        for s in (mains.get(key) or [])[:max_sectors]:
            s = dict(s)
            s["_type"] = "industry" if key == "industry_main" else "concept"
            sectors.append(s)

    # 按主线评分排序
    sectors.sort(key=lambda x: x.get("main_score", 999))
    sectors = _dedup_sectors(sectors)
    return sectors[:max_sectors]


# ============ 第2步：扩散到成份股 ============

def expand_sector_constituents(sectors, snapshot):
    """
    从主线板块扩散出成分股，并用全市场快照补齐行情字段
    返回: (candidates {code: quote}, sector_map {code: [板块名]})
    """
    candidates = {}
    sector_map = {}

    for s in sectors:
        code = s.get("sector_code") or s.get("code") or ""
        name = s.get("name", "")
        if not code:
            continue
        try:
            from sector_analysis import get_sector_detail
            detail = get_sector_detail(code)
        except Exception as e:
            print(f"[Pool] 板块 {name}({code}) 成份股获取失败: {e}")
            continue

        cons = (detail or {}).get("constituents") or []
        print(f"[Pool] 板块 {name}({code}) 扩散 {len(cons)} 只成份股")
        for c in cons:
            ccode = c.get("code", "")
            if not ccode:
                continue
            # 用全市场快照补齐成交额/换手/量比（板块接口只给涨跌幅和资金流）
            snap_q = snapshot.get(ccode)
            if snap_q:
                merged = dict(snap_q)
                merged["sector_fund_flow_yi"] = c.get("fund_flow_yi", 0)
            else:
                merged = {
                    "code": ccode, "name": c.get("name", ccode),
                    "price": c.get("price", 0), "change_pct": c.get("change_pct", 0),
                    "amount_wan": 0, "turnover_pct": 0, "vol_ratio": 0,
                    "sector_fund_flow_yi": c.get("fund_flow_yi", 0),
                }
            candidates[ccode] = merged
            sector_map.setdefault(ccode, []).append(name)

    return candidates, sector_map


# ============ 第3步：硬过滤 ============

def hard_filter(candidates, strict_buyable=False):
    """
    硬性过滤，分两档：

    strict_buyable=False（默认·核心过滤）：
        权限板块(SCREENING_CONFIG) + 未涨停/未接近涨停 + 成交额≥1亿
        量比/换手降级为排序权重 —— 实测严格过滤会把 143 只砍到 4 只，候选池枯竭，
        而量比/换手本就是"健康度偏好"而非"能不能买"的硬门槛。
    strict_buyable=True（严格模式）：
        额外启用 is_buyable_candidate（量比 1.2-3.5 且换手 3-15%）

    返回: (filtered {code: quote}, stats dict)
    """
    from config import is_excluded_stock, is_buyable_candidate, is_limit_up, SELECTION_CONFIG as C

    filtered = {}
    stat = {"total": len(candidates), "excluded_board": 0, "limit_up": 0,
            "low_amount": 0, "not_buyable": 0, "passed": 0, "mode":
            "strict" if strict_buyable else "core"}

    for code, q in candidates.items():
        if is_excluded_stock(code)[0]:
            stat["excluded_board"] += 1
            continue
        # 涨停/接近涨停：归入打板观察区，不进主推候选
        if is_limit_up(q.get("change_pct", 0)):
            stat["limit_up"] += 1
            continue
        # 成交额门槛：排除僵尸股（实盘必要条件）
        if (q.get("amount_wan", 0) or 0) < C["amount_min_wan"]:
            stat["low_amount"] += 1
            continue
        if strict_buyable:
            if not is_buyable_candidate(
                q.get("change_pct", 0), q.get("vol_ratio", 0),
                q.get("turnover_pct", 0), q.get("amount_wan", 0)
            ):
                stat["not_buyable"] += 1
                continue
        filtered[code] = q

    stat["passed"] = len(filtered)
    return filtered, stat


def supplement_from_market(snapshot, filtered, need_more, exclude_codes=None):
    """
    候选不足时从全市场快照补充：取「涨幅甜区 + 成交额达标 + 未涨停」的标的
    （主线扩散的候选数量受板块大小限制，需全市场兜底保证池子够大）
    """
    from config import is_excluded_stock, is_limit_up, SELECTION_CONFIG as C

    exclude = set(exclude_codes or []) | set(filtered.keys())
    pool = []
    for code, q in snapshot.items():
        if code in exclude:
            continue
        if is_excluded_stock(code)[0] or is_limit_up(q.get("change_pct", 0)):
            continue
        if (q.get("amount_wan", 0) or 0) < C["amount_min_wan"]:
            continue
        pool.append((code, q))

    # 甜区涨幅优先，其次是成交额
    def rank(item):
        q = item[1]
        chg = q.get("change_pct", 0)
        sweet = 1 if C["sweet_low"] <= chg <= C["sweet_high"] else 0
        return (sweet, q.get("amount_wan", 0), chg)

    pool.sort(key=rank, reverse=True)
    added = 0
    for code, q in pool:
        if added >= need_more:
            break
        filtered[code] = q
        added += 1
    print(f"[Pool] 全市场补充候选 {added} 只（当前总数 {len(filtered)}）")
    return filtered


# ============ 第4步：排序打分 ============

def _fund_health_score(q):
    """资金面健康度评分 0~1（量比/换手/成交额越健康越高）"""
    from config import SELECTION_CONFIG as C
    vr, to = q.get("vol_ratio", 0) or 0, q.get("turnover_pct", 0) or 0
    amt = q.get("amount_wan", 0) or 0
    score = 0.0
    # 量比：健康区间内越高越好（<3.5 上限）
    if C["vol_ratio_min"] <= vr <= C["vol_ratio_max"]:
        score += 0.4 * min(vr / C["vol_ratio_max"], 1.0)
    elif vr > C["vol_ratio_max"]:
        score += 0.15
    # 换手：健康区间内给分
    if C["turnover_min"] <= to <= C["turnover_max"]:
        score += 0.3
    elif to > C["turnover_max"]:
        score += 0.1
    # 成交额：越大越好（1亿基准，10亿满分）
    score += 0.3 * min(amt / 100000.0, 1.0)
    return round(score, 4)


def _tech_score(code, conn):
    """SignalEngine 技术共振分（-100~+100 归一到 0~1）"""
    try:
        from data_fetcher import fetch_kline
        from signal_engine import SignalEngine
        kl = fetch_kline(code, period="day", count=120)
        if not kl or len(kl) < 60:
            return 0.5, None
        eng = SignalEngine()
        res = eng.analyze(kl)
        if not res:
            return 0.5, None
        s = res.get("score", 0)
        return round((s + 100) / 200.0, 4), res.get("level_text", "")
    except Exception:
        return 0.5, None


def score_candidates(candidates, conn, sector_map, cfg):
    """
    XGBoost 盈利概率 + 技术分 + 资金分 综合排序
    技术分需逐只拉K线（IO密集），用线程池并发避免串行超时拖慢整体
    返回: [{code, name, score, xgb_proba, tech_score, fund_score, sectors, quote}, ...]
    """
    from concurrent.futures import ThreadPoolExecutor

    max_scored = cfg["max_scored"]
    # 先按成交额+涨幅粗筛，控制拉K线的数量（性能保护）
    pre_sorted = sorted(
        candidates.items(),
        key=lambda x: (x[1].get("amount_wan", 0) * 0.6 + x[1].get("change_pct", 0) * 4000),
        reverse=True
    )[:max_scored]

    # XGBoost 批量打分（读本地DB，很快）
    xgb_proba = {}
    try:
        from xgb_ranker import rank_candidates
        ranked = rank_candidates({c: q for c, q in pre_sorted}, conn, top_n=max_scored)
        xgb_proba = {r["code"]: r.get("proba") for r in ranked if r.get("proba") is not None}
        print(f"[Pool] XGBoost 打分完成: {len(xgb_proba)} 只")
    except Exception as e:
        print(f"[Pool] XGBoost 打分失败，降级（无模型分）: {e}")

    # 技术分并发计算
    tech_workers = cfg.get("tech_workers", 8)
    tech_result = {}
    codes = [c for c, _ in pre_sorted]
    with ThreadPoolExecutor(max_workers=tech_workers) as ex:
        futures = {ex.submit(_tech_score, c, conn): c for c in codes}
        for fu in futures:
            code = futures[fu]
            try:
                tech_result[code] = fu.result()
            except Exception:
                tech_result[code] = (0.5, None)
    ok_tech = sum(1 for v in tech_result.values() if v[1])
    print(f"[Pool] 技术分完成: {ok_tech}/{len(codes)} 只有效（{tech_workers}线程）")

    results = []
    for code, q in pre_sorted:
        p = xgb_proba.get(code, 0.5)
        t_score, t_text = tech_result.get(code, (0.5, None))
        f_score = _fund_health_score(q)
        total = (cfg["xgb_weight"] * p
                 + cfg["tech_weight"] * t_score
                 + cfg["fund_weight"] * f_score)
        results.append({
            "code": code,
            "name": q.get("name", code),
            "score": round(total, 4),
            "xgb_proba": round(p, 4),
            "tech_score": t_score,
            "tech_level": t_text,
            "fund_score": f_score,
            "sectors": sector_map.get(code, []),
            "quote": q,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ============ 主入口 ============

def build_candidate_pool(top_n=35, config=None, require_buyable=True, use_snapshot=True):
    """
    构建选股候选池（主线扩散链）

    Args:
        top_n: 返回候选数量（默认35，喂给 AI 挑10只）
        config: 覆盖 DEFAULT_POOL_CONFIG
        require_buyable: 是否启用可买性过滤
        use_snapshot: 是否使用全市场快照（False 则退化为原逻辑）

    Returns:
        {
          success, candidates: [...], sectors: [...], stats: {...},
          snapshot_meta: {...}, time, degraded_reason
        }
    """
    cfg = _merge_config(config)
    t0 = time.time()
    out = {
        "success": False, "candidates": [], "sectors": [],
        "stats": {}, "snapshot_meta": {}, "time": "", "degraded_reason": None,
    }

    # 第0步：全市场快照
    snapshot = {}
    if use_snapshot:
        try:
            from market_snapshot import get_full_market_snapshot, snapshot_stats
            snapshot = get_full_market_snapshot() or {}
            out["snapshot_meta"] = snapshot_stats(snapshot)
        except Exception as e:
            print(f"[Pool] 全市场快照失败: {e}")
            out["degraded_reason"] = f"快照失败: {e}"

    # 第1步：定主线
    sectors = pick_main_sectors(cfg["main_sector_top"])
    out["sectors"] = [
        {"name": s.get("name"), "code": s.get("sector_code"),
         "change_pct": s.get("change_pct"), "fund_flow_yi": s.get("fund_flow_yi"),
         "level": s.get("main_level"), "type": s.get("_type")}
        for s in sectors
    ]
    print(f"[Pool] 主线板块 {len(sectors)} 个: "
          f"{', '.join(s.get('name','') for s in sectors)}")

    # 第2步：扩散
    candidates, sector_map = {}, {}
    if sectors and snapshot:
        candidates, sector_map = expand_sector_constituents(sectors, snapshot)

    # 降级：主线扩散失败/为空 → 直接用全市场快照按成交额构建
    if not candidates:
        out["degraded_reason"] = "主线扩散为空，退化为全市场量价候选"
        print(f"[Pool] {out['degraded_reason']}")
        if snapshot:
            ranked = sorted(snapshot.items(),
                            key=lambda x: x[1].get("amount_wan", 0), reverse=True)
            candidates = dict(ranked[:cfg["fallback_top_n"] * 4])
        if not candidates:
            out["time"] = datetime.now().strftime("%H:%M:%S")
            return out

    # 第3步：硬过滤（默认核心过滤，量比/换手只作排序权重）
    strict = bool((config or {}).get("strict_buyable", False)) if config else False
    filtered, stat = hard_filter(candidates, strict_buyable=strict)
    out["stats"] = stat
    print(f"[Pool] 过滤[{stat['mode']}]: 扩散{stat['total']} → 权限剔除{stat['excluded_board']} "
          f"→ 涨停{stat['limit_up']} → 成交额不足{stat['low_amount']} "
          f"→ 不可买{stat['not_buyable']} → 剩余{stat['passed']}")

    if not filtered and strict:
        # 严格模式清空 → 放宽为核心过滤
        out["degraded_reason"] = "严格过滤后为空，放宽为核心过滤"
        print(f"[Pool] {out['degraded_reason']}")
        filtered, stat = hard_filter(candidates, strict_buyable=False)
        out["stats"] = stat

    # 候选不足 → 全市场兜底补充（保证池子够 AI 挑）
    min_pool = cfg.get("min_pool_size", 60)
    if len(filtered) < min_pool and snapshot:
        filtered = supplement_from_market(
            snapshot, filtered, min_pool - len(filtered), exclude_codes=set(candidates.keys()))

    # 第4步：排序打分
    conn = _get_db_conn()
    try:
        scored = score_candidates(filtered, conn, sector_map, cfg)
    finally:
        if conn:
            conn.close()

    out["candidates"] = scored[:top_n]
    out["success"] = len(scored) > 0
    out["time"] = datetime.now().strftime("%H:%M:%S")
    print(f"[Pool] 候选池构建完成: {len(out['candidates'])} 只, "
          f"耗时 {time.time()-t0:.1f}s")
    return out


def pool_to_quotes(pool_result):
    """把候选池结果转成 ai_screener 需要的 quotes 格式 {code: quote}"""
    quotes = {}
    for c in pool_result.get("candidates", []):
        q = dict(c.get("quote", {}))
        q.setdefault("code", c["code"])
        q.setdefault("name", c["name"])
        q["pool_score"] = c.get("score")
        q["xgb_proba"] = c.get("xgb_proba")
        q["tech_level"] = c.get("tech_level")
        q["sectors"] = c.get("sectors", [])
        quotes[c["code"]] = q
    return quotes


if __name__ == "__main__":
    print("=" * 60)
    print("候选池构建测试（主线扩散链）")
    print("=" * 60)
    r = build_candidate_pool(top_n=15)
    print(f"\nsuccess={r['success']} 快照={r['snapshot_meta']}")
    print(f"主线板块: {[s['name'] for s in r['sectors']]}")
    print(f"过滤统计: {r['stats']}")
    if r.get("degraded_reason"):
        print(f"降级原因: {r['degraded_reason']}")
    print(f"\nTOP {len(r['candidates'])} 候选:")
    for i, c in enumerate(r["candidates"], 1):
        q = c["quote"]
        print(f"  {i:2d}. {c['name']}({c['code']}) 综合{c['score']:.3f} "
              f"[XGB {c['xgb_proba']} 技术 {c['tech_level'] or '-'}] "
              f"涨幅{q.get('change_pct',0):+.2f}% 额{q.get('amount_wan',0)/10000:.1f}亿 "
              f"板块{','.join(c['sectors'][:2])}")
