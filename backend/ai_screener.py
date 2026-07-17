"""
AI 选股引擎 v1.0
- 基于 DeepSeek 大模型的智能选股
- 两种模式: 对话选股 / 一键选股
- 结合6维数据: 技术面、资金面、龙头效应、市场情绪、国际行情、政策面

用户画像: 短线选手, 持股不超过5天 (周一买周五必卖)
"""
import json
import time
import requests
import os
from datetime import datetime
from config import (
    DEEPSEEK_API_KEY, DEEPSEEK_MODEL, DEEPSEEK_BASE_URL,
    is_excluded_stock, build_exclusion_prompt,
    SELECTION_CONFIG, is_limit_up, is_buyable_candidate, passes_fund_filter,
)

API_URL = f"{DEEPSEEK_BASE_URL}/v1/chat/completions"
UA = "StockMonitor/2.0"
SHORT_TERM_PROFILE = f"""
【用户交易画像】
- 风格: 超短线交易者
- 最大持仓周期: 5个交易日（比如周一买入，最晚周五必卖出）
- 偏好: 强势股突破、热门板块轮动、资金驱动型
- 风控: 严格止损（-5%无条件离场），不恋战
- 选股逻辑: 追涨不追高，关注资金异动和板块联动
{build_exclusion_prompt()}
"""


def _call_deepseek(messages, temperature=0.7, max_tokens=4096, stream=False):
    """调用 DeepSeek API"""
    if not DEEPSEEK_API_KEY:
        return None, "DeepSeek API Key 未配置"

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": UA,
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }

    for attempt in range(3):
        try:
            r = requests.post(API_URL, json=payload, headers=headers, timeout=90)
            if r.status_code == 200:
                return r.json(), None
            err = f"HTTP {r.status_code}: {r.text[:200]}"
            if attempt < 2:
                time.sleep(2)
        except Exception as e:
            err = str(e)
            if attempt < 2:
                time.sleep(2)

    return None, err


def _build_market_context(quotes, sentiment, sector_ranking, fund_flow, indices, international=None):
    """构建全市场上下文（喂给LLM的背景信息），聚焦『可买性优先』的多因子环境。"""
    ctx_parts = []

    # 1. 三大指数
    if indices:
        idx_info = []
        for key, name in [("sh", "上证"), ("sz", "深证"), ("cy", "创业板")]:
            d = indices.get(key, {})
            if d and d.get("price"):
                idx_info.append(f"{name} {d['price']:.0f} ({d['change_pct']:+.2f}%)")
        if idx_info:
            ctx_parts.append(f"今日指数: {' | '.join(idx_info)}")

    # 2. 市场情绪
    if sentiment:
        s = sentiment.get("sentiment", {})
        b = sentiment.get("breadth", {})
        ctx_parts.append(
            f"恐慌贪婪指数: {s.get('score', 50)} ({s.get('level_text', '中性')}) | "
            f"涨跌比: 涨{b.get('up_count', 0)}/跌{b.get('down_count', 0)}"
        )

    # 3. 行业涨幅 TOP10（板块主线）
    if sector_ranking and sector_ranking.get("top"):
        top_sectors = sector_ranking["top"][:10]
        sector_str = " | ".join(
            f"{r['name']}({r['change_pct']:+.1f}% 资金{r.get('fund_flow_str','')})"
            for r in top_sectors
        )
        ctx_parts.append(f"行业涨幅TOP10(板块主线): {sector_str}")

    # 4. 资金流入 TOP5（主力偏好）
    if fund_flow and fund_flow.get("top_inflow"):
        fund_str = " | ".join(
            f"{r['name']}({r.get('net_flow_str','')})"
            for r in fund_flow["top_inflow"][:5]
        )
        ctx_parts.append(f"资金流入TOP5(主力偏好): {fund_str}")

    # 5. 各板块领涨龙头（仅作强度观察，不推荐追板）
    if sector_ranking and sector_ranking.get("top"):
        leaders = []
        for r in sector_ranking["top"][:8]:
            if r.get("leader_name") and r["leader_name"] != "-":
                leaders.append(f"{r['name']}→{r['leader_name']}({r['leader_code']})")
        if leaders:
            ctx_parts.append(f"各板块领涨龙头(观察): {' | '.join(leaders[:6])}")

    # 6. 国际行情环境（美股 + 汇率 + 大宗商品，真实数据）
    if international:
        intl_parts = []
        for idx in international.get("us_indices", []) or []:
            intl_parts.append(f"{idx.get('name', '')} {idx.get('price', 0):.2f}({idx.get('change_pct', 0):+.2f}%)")
        if intl_parts:
            ctx_parts.append(f"隔夜美股: {' | '.join(intl_parts)}")
        usdcny = international.get("usdcny")
        if usdcny and usdcny.get("price"):
            ctx_parts.append(f"美元/人民币: {usdcny['price']:.4f} ({usdcny.get('change_pct', 0):+.2f}%)")
        commods = international.get("commodities", []) or []
        if commods:
            cparts = [f"{c.get('name', '')} {c.get('price', 0):.2f}({c.get('change_pct', 0):+.2f}%)" for c in commods]
            ctx_parts.append(f"大宗商品: {' | '.join(cparts)}")

    # 7. 技术面共振概览（SignalEngine 评分，偏多标的）
    if quotes:
        tech_bulls = []
        for c, q in quotes.items():
            tech = q.get("tech")
            if tech and tech.get("score", 0) >= 30 and not is_excluded_stock(c)[0]:
                tech_bulls.append(f"{q.get('name', c)}({tech.get('level_text', '')})")
        if tech_bulls:
            ctx_parts.append(f"技术面共振(偏多): {' | '.join(tech_bulls[:10])}")

    # 8. 跌幅较大（超跌反弹观察）
    if quotes:
        sorted_stocks = sorted(quotes.items(), key=lambda x: x[1].get("change_pct", 0))
        bottom_movers = [f"{q.get('name', c)}({c}) {q['change_pct']:+.1f}%" for c, q in sorted_stocks[:8] if q.get("change_pct", 0) < -3 and not is_excluded_stock(c)[0]]
        if bottom_movers:
            ctx_parts.append(f"跌幅较大(关注超跌反弹): {' | '.join(bottom_movers[:5])}")

    return "\n".join(ctx_parts)


def ai_quick_pick(quotes, sentiment, sector_ranking, fund_flow, concept_ranking, indices, international=None):
    """
    一键选股：自动分析市场，推荐可买入的短线标的（可买性优先，数量见 SELECTION_CONFIG.pick_count）

    返回格式: {success, stocks: [{code, name, reason, score, hold_days, stop_loss, target}], summary, watch?}
    """
    market_ctx = _build_market_context(quotes, sentiment, sector_ranking, fund_flow, indices, international)
    if concept_ranking and concept_ranking.get("top"):
        concept_str = " | ".join(
            f"{r['name']}({r['change_pct']:+.1f}%)"
            for r in concept_ranking["top"][:8]
        )
        market_ctx += f"\n概念涨幅TOP8: {concept_str}"

    # 精选热点股票数据
    hot_stocks = _extract_hot_stocks(quotes, sector_ranking, fund_flow)

    system_prompt = f"""你是一位专业的A股短线交易分析师，核心原则是『可操作性优先』：推荐的标的必须是当前时点普通账户可以实际买入、且具备上行潜力的股票。

{SHORT_TERM_PROFILE}

【当前市场概况（全市场数据）】
{market_ctx}

【候选股票池·已按可买性分层】
{hot_stocks}

【你的任务】
请从『可买入候选池』中精选{SELECTION_CONFIG['pick_count']}只最适合短线（1-5天持有）操作的股票。
⚠️ 必须返回满{SELECTION_CONFIG['pick_count']}只：候选池已足够大，请尽量覆盖不同板块/主线，不要把数量凑不够；个别次优标的（资金温和、技术偏多但非最强主线）也可纳入，但一律不得推荐涨幅≥7%的票。
{build_exclusion_prompt()}

【选股铁律·必须严格遵守】
1. 默认只从『可买入候选池』选择：未涨停、未接近涨停（涨幅<7%），且量比/换手/成交额处于健康区间。
2. 已涨停（≥9.8%）的股票一律不推荐买入——次日无法以合理成本介入，实战意义极低，仅作板块强度观察。
3. 『打板观察区』的票（接近涨停）仅作风险提示，不得进入主推荐列表；如确需保留观察，最多2只且必须明确标注『高风险·不可买入』。
4. 选股优先级：板块主线强 > 资金持续流入（量比/换手健康）> 技术面偏多（金叉/多头/共振）。三者至少满足一项即可入选；板块主线为强烈加分项但非强制——若某标的资金与技术共振极强，即使非当前最强主线也可入选。
5. 优先考虑强板块中的『次龙头/早启动』标的，而非已封板的绝对龙头——它们往往有更舒适的买点和更大上行空间。

【每只推荐必须包含的买入逻辑（reason，80-150字）】
- 为什么现在可买：当前涨幅处于甜区（0.5%-6.5%），距压力位有空间，未封板可成交
- 资金与板块驱动：所属主线板块 + 主力资金流入情况
- 技术信号：MACD/均线/量价等共振证据（若有 SignalEngine 评分请引用）
- 现实买入价：给出具体可执行的买入区间（需结合实时价，勿写模糊的『现价附近』）

【输出格式】严格按以下JSON格式输出，不要包含任何其他文字：
⚠️ 极其重要：JSON中所有字符串的值（包括summary、reason、entry_price、stop_loss、target等）绝对不允许出现英文双引号(")字符！若有引用需求，请用单引号(')或中文引号「」替代。例如：写 'MACD金叉' 或 「资金流入」 而不是 "MACD金叉"。
{{
  "summary": "一句话总结当前市场环境和核心策略",
  "stocks": [
    {{
      "code": "股票代码(6位)",
      "name": "股票名称",
      "score": 评分(1-100),
      "reason": "详细买入逻辑，包含可买性+资金+板块+技术四维，80-150字",
      "entry_price": "建议买入价区间，如 26.50-27.00",
      "hold_days": "建议持有天数(1-5)",
      "stop_loss": "止损价",
      "target": "目标价"
    }}
  ]
}}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请严格按照JSON格式输出{SELECTION_CONFIG['pick_count']}只最佳短线标的，不要输出任何其他内容。"},
    ]

    data, err = _call_deepseek(messages, temperature=0.6, max_tokens=32768)
    if err:
        return {"success": False, "error": err}

    try:
        content = data["choices"][0]["message"]["content"].strip()
        
        # --- 强大的 JSON 提取逻辑 ---
        json_str = None
        
        # 1. 处理 markdown 代码块
        if "```json" in content:
            json_str = content.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in content:
            json_str = content.split("```", 1)[1].split("```", 1)[0].strip()
        # 2. 尝试找第一个 { 到最后一个 } 
        elif "{" in content and "}" in content:
            start = content.find("{")
            end = content.rfind("}")
            json_str = content[start:end+1].strip()
        # 3. 纯 JSON
        else:
            json_str = content
        
        if not json_str:
            return {"success": False, "error": "AI返回内容为空，无法解析", "raw": content[:500]}
        
        result = json.loads(json_str)
        # 安全过滤：剔除无交易权限的板块股票（用户无权限买入，依据 SCREENING_CONFIG）
        if result.get("stocks"):
            result["stocks"] = [s for s in result["stocks"] if not is_excluded_stock(s.get("code", ""))[0]]
        # 后置可买性兜底：剔除无法买入的涨停/接近涨停票，必要时从行情补齐到 pick_count
        result = _apply_buyable_filter(result, quotes, pad=True)
        return {"success": True, **result}
    except json.JSONDecodeError as e:
        # JSON解析失败：保存原始内容以便调试
        snippet = (json_str or content)[:800]
        print(f"[AI] JSON解析失败: {e}")
        print(f"[AI] 原始返回(前800字): {snippet}")
        # 保存到文件以便调试
        debug_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_debug_last.json")
        try:
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(f"ERROR: {e}\n\n")
                f.write(f"JSON_STR:\n{json_str}\n\n")
                f.write(f"CONTENT:\n{content}")
            print(f"[AI] 调试信息已保存到: {debug_path}")
        except Exception as save_err:
            print(f"[AI] 保存调试信息失败: {save_err}")
        return {"success": False, "error": f"解析失败: {e}", "raw": snippet}
    except (KeyError, IndexError) as e:
        print(f"[AI] 数据结构异常: {e}, data keys: {list(data.keys()) if data else 'None'}")
        return {"success": False, "error": f"数据结构异常: {e}"}


def _extract_hot_stocks(quotes, sector_ranking, fund_flow):
    """从全市场行情中提取热点候选股，按『可买性优先』分层。

    返回两段文本：
    1) 可买入候选池（未涨停/未接近涨停 + 资金健康 + 技术偏多）—— LLM 重点分析对象
    2) 打板观察区（涨停/接近涨停，最多 board_watch_max 只）—— 仅风险观察，不推荐买入
    """
    if not quotes:
        return "暂无实时行情数据"

    # 收集所有板块领涨股
    leader_codes = set()
    if sector_ranking and sector_ranking.get("top"):
        for r in sector_ranking["top"]:
            lc = r.get("leader_code", "")
            if lc and lc != "-":
                leader_codes.add(lc)

    # 候选集：领涨股 + 涨幅前40 + 成交额前25 + 技术偏多Top20（放大池子，覆盖全市场，排除无权限板块）
    cand = set(leader_codes)
    for code, _ in sorted(quotes.items(), key=lambda x: x[1].get("change_pct", 0), reverse=True)[:40]:
        if not is_excluded_stock(code)[0]:
            cand.add(code)
    for code, _ in sorted(quotes.items(), key=lambda x: x[1].get("amount_wan", 0), reverse=True)[:25]:
        if not is_excluded_stock(code)[0]:
            cand.add(code)
    # 补充：技术面偏多（SignalEngine 评分>0）的标的，确保潜力股进入候选池
    tech_ranked = sorted(
        (c for c in quotes
         if not is_excluded_stock(c)[0] and (quotes[c].get("tech") or {}).get("score", 0) > 0),
        key=lambda c: quotes[c].get("tech", {}).get("score", 0), reverse=True
    )[:20]
    cand.update(tech_ranked)
    cand = [c for c in cand if c in quotes]

    buyable, watch = [], []
    for code in cand:
        q = quotes.get(code, {})
        change = q.get("change_pct", 0)
        vr = q.get("vol_ratio", 0)
        to = q.get("turnover_pct", 0)
        amt = q.get("amount_wan", 0)
        tech = q.get("tech")
        tech_score = tech.get("score") if tech else None

        if is_limit_up(change):
            # 涨停/接近涨停：归入打板观察区，不进入可买入候选池
            watch.append((code, q, tech))
            continue
        if is_buyable_candidate(change, vr, to, amt, tech_score):
            buyable.append((code, q, tech))

    # === XGBoost 排序（v3.0）：用训练好的模型预测每只候选的 2 天盈利概率 ===
    xgb_proba = {}
    try:
        from xgb_ranker import _load_model, build_features
        xgb_model = _load_model()
        if xgb_model is not None:
            import sqlite3, statistics
            db = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_monitor.db"))
            for code, q, tech in buyable + list(watch):
                rows = db.execute(
                    "SELECT trade_date,open,high,low,close,volume FROM price_history WHERE code=? ORDER BY trade_date",
                    (code,)
                ).fetchall()
                if not rows or len(rows) < 20:
                    continue
                k = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]
                feats = build_features(k, len(k) - 1)
                import numpy as np
                X = np.array(feats, dtype=np.float32).reshape(1, -1)
                xgb_proba[code] = float(xgb_model.predict_proba(X)[0][1])
            db.close()
            if xgb_proba:
                # 用 XGBoost 概率排序，替代纯技术分排序
                buyable.sort(key=lambda item: xgb_proba.get(item[0], 0.5), reverse=True)
    except Exception as e:
        # 模型不可用时回退到技术分排序
        pass
    
    if not xgb_proba:
        # 降级排序：技术评分优先
        def _buyable_sort_key(item):
            _, q, tech = item
            score = tech.get("score", 0) if tech else 0
            return (score, q.get("change_pct", 0))
        buyable.sort(key=_buyable_sort_key, reverse=True)
    # 打板观察区：仅保留最强的 board_watch_max 只
    watch = watch[:SELECTION_CONFIG["board_watch_max"]]

    lines = []
    # ---- 可买入候选池 ----
    if buyable:
        xgb_tag = " [已按 XGBoost 短线盈利概率排序]" if xgb_proba else ""
        lines.append(f"【可买入候选池·重点分析对象（未涨停、资金健康、技术偏多）{xgb_tag}】")
        for code, q, tech in buyable[:60]:
            tags = []
            if code in leader_codes:
                tags.append("板块强势")
            if tech and tech.get("score", 0) >= 30:
                tags.append(f"技术{tech.get('level_text', '')}")
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            lines.append(
                f"{q.get('name', code)}({code}) "
                f"价{q.get('price', 0):.2f} 涨幅{q.get('change_pct', 0):+.2f}% "
                f"换手{q.get('turnover_pct', 0):.1f}% "
                f"量比{q.get('vol_ratio', 0):.1f} "
                f"额{q.get('amount_wan', 0)/10000:.1f}亿{tag_str}"
            )
    else:
        lines.append("【可买入候选池】当前市场无满足条件的可买入标的（多处于涨停/高位），请参考下方打板观察区。")

    # ---- 打板观察区 ----
    if watch:
        lines.append("")
        lines.append("【打板观察区·仅风险观察，不推荐买入（已涨停/接近涨停）】")
        for code, q, tech in watch:
            tags = []
            if code in leader_codes:
                tags.append("板块龙头")
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            lines.append(
                f"{q.get('name', code)}({code}) "
                f"价{q.get('price', 0):.2f} 涨幅{q.get('change_pct', 0):+.2f}% "
                f"换手{q.get('turnover_pct', 0):.1f}% "
                f"量比{q.get('vol_ratio', 0):.1f} "
                f"额{q.get('amount_wan', 0)/10000:.1f}亿{tag_str}"
            )

    return "\n".join(lines)


def ai_screen(query, quotes, sentiment, sector_ranking, fund_flow, concept_ranking, indices, international=None):
    """
    对话式选股：用户自由提问（可买性优先）

    返回格式: {success, answer, stocks: [...]}
    """
    market_ctx = _build_market_context(quotes, sentiment, sector_ranking, fund_flow, indices, international)
    if concept_ranking and concept_ranking.get("top"):
        concept_str = " | ".join(
            f"{r['name']}({r['change_pct']:+.1f}%)"
            for r in concept_ranking["top"][:8]
        )
        market_ctx += f"\n概念涨幅: {concept_str}"

    system_prompt = f"""你是StockMonitor的AI选股助手，专为A股短线交易者服务，核心原则『可买性优先』。

{SHORT_TERM_PROFILE}

【当前市场概况（全市场数据，不限于用户持仓）】
{market_ctx}

【你的能力】
- 根据用户条件从全市场筛选股票（技术面、资金面、板块面、基本面）
- 分析当前市场热点和主线板块
- 评估个股短线操作价值和风险（重点判断『现在是否可买入』）
- 回答A股交易相关问题

【重要】你的分析基于全市场实时数据，包括所有板块龙头和活跃标的，不局限于用户监控列表。
选股时务必优先考虑当前市场主线板块中『可买入』的标的（未涨停/未接近涨停、资金健康、技术偏多），而非只推已封板的绝对龙头。涨停/接近涨停的票默认不推荐买入，仅作板块强度观察。
{build_exclusion_prompt()}
⚠️ 所有字符串值中严禁使用英文双引号("), 统一用单引号(')替代。若需引号，用中文引号「」替代。

【输出要求】
1. 先给出你的分析和结论
2. 如果涉及股票推荐，在末尾用如下JSON格式列出（推荐需具备可买入性）：
```json
{{"stocks": [{{"code": "000001", "name": "平安银行", "reason": "推荐理由30-50字，含当前可买性判断"}}]}}
```
3. 如果用户问的问题和数据无关，正常回答即可"""

    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ]
        data, err = _call_deepseek(messages, temperature=0.7, max_tokens=4096)
        if err:
            return {"success": False, "error": err}

        content = data["choices"][0]["message"]["content"]
        stocks = []
        # 提取内嵌JSON (AI可能在markdown代码块中输出)
        json_str = None
        if "```json" in content:
            json_str = content.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in content:
            json_str = content.split("```", 1)[1].split("```", 1)[0].strip()
        elif "{" in content and "}" in content:
            start = content.find("{")
            end = content.rfind("}")
            json_str = content[start:end+1].strip()
        
        if json_str:
            try:
                stocks = json.loads(json_str).get("stocks", [])
                # 安全过滤：剔除无交易权限的板块股票（依据 SCREENING_CONFIG）
                stocks = [s for s in stocks if not is_excluded_stock(s.get("code", ""))[0]]
            except json.JSONDecodeError:
                stocks = []
            # 后置可买性兜底：涨停/接近涨停票降级，仅保留可买入主推
            stocks = _apply_buyable_filter({"stocks": stocks}, quotes).get("stocks", [])

        return {"success": True, "answer": content, "stocks": stocks}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _apply_buyable_filter(result, quotes, pad=False):
    """后置可买性兜底：涨停/接近涨停票不进主推荐，仅保留≤board_watch_max只到打板观察区。

    - 主推荐被全部清空（多半是涨停/高位）：从行情兜底筛选可买入标的
    - pad=True（一键选股）：主推荐不足 pick_count 时，从可买入候选补齐到 pick_count
      以保证最终稳定返回 pick_count 只（用户要求数量=10）
    """
    c = SELECTION_CONFIG
    n = c["pick_count"]
    stocks = result.get("stocks") or []
    main, watch = [], []
    for s in stocks:
        code = s.get("code", "")
        q = quotes.get(code, {})
        chg = q.get("change_pct", None)
        if chg is None:
            # 行情中查不到（如用户自定义标的），保留但标注不确定
            main.append(s)
            continue
        if chg >= c["limit_up_pct"]:
            s["risk"] = "已涨停，无法买入，仅作打板观察"
            watch.append(s)
        elif chg >= c["near_limit_pct"]:
            s["risk"] = "接近涨停，追高被套风险极高，仅作打板观察"
            watch.append(s)
        else:
            main.append(s)

    if not main and stocks:
        # 主推荐被全部清空，兜底从行情筛选可买入标的
        result["stocks"] = _get_fallback_picks(quotes, n)
        result["note"] = "AI主推标的因涨停/接近涨停无法买入，已自动切换为可买入标的兜底推荐"
    else:
        result["stocks"] = main[:n]
        # 一键选股模式：不足 pick_count 时，从可买入候选补齐，确保返回数量稳定
        if pad and len(result["stocks"]) < n:
            seen = {s.get("code") for s in result["stocks"]}
            for f in _get_fallback_picks(quotes, n):
                if f.get("code") not in seen and len(result["stocks"]) < n:
                    result["stocks"].append(f)
                    seen.add(f.get("code"))
            if len(result["stocks"]) < n:
                result["note"] = (result.get("note", "") + "；可买入候选不足，返回数量少于" + str(n) + "只").strip("；")
            else:
                result["note"] = (result.get("note", "") + "；主推不足" + str(n) + "只，已用可买入候选补齐").strip("；")

    if watch:
        result["watch"] = watch[:c["board_watch_max"]]
    return result


def _get_fallback_picks(quotes, n=10):
    """兜底/补齐选股：从行情中筛选可买入标的（未涨停、非无权限板块、有成交）。

    优先选「资金健康 + 技术偏多」的；行情缺量比/换手字段时退化为「仅未涨停」也能补齐。
    """
    c = SELECTION_CONFIG
    cands = []
    for code, q in quotes.items():
        if is_excluded_stock(code)[0]:
            continue
        change = q.get("change_pct", 0) or 0
        if change >= c["near_limit_pct"]:      # 排除涨停/接近涨停
            continue
        price = q.get("price", 0) or 0
        if price <= 0:
            continue
        tech = q.get("tech")
        score = tech.get("score", 0) if tech else 0
        amt = q.get("amount_wan", 0) or 0
        fund_ok = passes_fund_filter(q.get("vol_ratio", 0), q.get("turnover_pct", 0), amt)
        cands.append({"fund_ok": fund_ok, "score": score, "amt": amt, "code": code, "q": q})
    # 先按资金健康、再按技术分、再按成交额排序
    cands.sort(key=lambda x: (x["fund_ok"], x["score"], x["amt"]), reverse=True)
    picks = []
    for cd in cands[:n]:
        q = cd["q"]
        price = q.get("price", 0) or 0
        picks.append({
            "code": cd["code"],
            "name": q.get("name", cd["code"]),
            "score": max(50, min(90, 50 + cd["score"] // 3)),
            "reason": ("系统兜底筛选：未涨停、资金健康、技术偏多，当前价位具备可买入性"
                       if cd["fund_ok"] else "系统兜底筛选：未涨停、当前价位可买入（资金数据不足，按技术信号补齐）"),
            "entry_price": f"{price:.2f}附近",
            "hold_days": "3",
            "stop_loss": f"{price * 0.95:.2f}",
            "target": f"{price * 1.08:.2f}",
        })
    return picks


if __name__ == "__main__":
    print("[AI Screener] 模块就绪")
    print(f"  Model: {DEEPSEEK_MODEL}")
    print(f"  Key: {'已配置' if DEEPSEEK_API_KEY else '未配置'}")
