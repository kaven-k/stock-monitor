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
from datetime import datetime
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, DEEPSEEK_BASE_URL

API_URL = f"{DEEPSEEK_BASE_URL}/v1/chat/completions"
UA = "StockMonitor/2.0"
SHORT_TERM_PROFILE = """
【用户交易画像】
- 风格: 超短线交易者
- 最大持仓周期: 5个交易日（比如周一买入，最晚周五必卖出）
- 偏好: 强势股突破、热门板块轮动、资金驱动型
- 风控: 严格止损（-5%无条件离场），不恋战
- 选股逻辑: 追涨不追高，关注资金异动和板块联动
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


def _build_market_context(quotes, sentiment, sector_ranking, fund_flow, indices):
    """构建全市场上下文（喂给LLM的背景信息）"""
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

    # 3. 行业涨幅 TOP10
    if sector_ranking and sector_ranking.get("top"):
        top_sectors = sector_ranking["top"][:10]
        sector_str = " | ".join(
            f"{r['name']}({r['change_pct']:+.1f}% 资金{r.get('fund_flow_str','')})"
            for r in top_sectors
        )
        ctx_parts.append(f"行业涨幅TOP10: {sector_str}")

    # 4. 概念涨幅 TOP10
    if sector_ranking and sector_ranking.get("top"):
        # 从 sector_ranking 的 top 中筛选概念板块（有 leader_name 的更好）
        pass  # 概念数据在 get_sector_ranking("concept") 里

    # 5. 资金流向 TOP5
    if fund_flow and fund_flow.get("top_inflow"):
        fund_str = " | ".join(
            f"{r['name']}({r.get('net_flow_str','')})"
            for r in fund_flow["top_inflow"][:5]
        )
        ctx_parts.append(f"资金流入TOP5: {fund_str}")

    # 6. 龙头股信息
    if sector_ranking and sector_ranking.get("top"):
        leaders = []
        for r in sector_ranking["top"][:8]:
            if r.get("leader_name") and r["leader_name"] != "-":
                leaders.append(f"{r['name']}→{r['leader_name']}({r['leader_code']})")
        if leaders:
            ctx_parts.append(f"各板块领涨龙头: {' | '.join(leaders[:6])}")

    # 7. 全市场候选股（板块龙头 + 监控股，取涨跌幅前20）
    if quotes:
        sorted_stocks = sorted(quotes.items(), key=lambda x: x[1].get("change_pct", 0), reverse=True)
        top_movers = [f"{q.get('name', c)}({c}) {q['change_pct']:+.1f}%" for c, q in sorted_stocks[:15] if q.get("change_pct", 0) > 0]
        if top_movers:
            ctx_parts.append(f"全市场候选股(板块龙头+监控): {' | '.join(top_movers[:12])}")
        # 也列一下跌幅最大的（可能超跌反弹机会）
        bottom_movers = [f"{q.get('name', c)}({c}) {q['change_pct']:+.1f}%" for c, q in sorted_stocks[-8:] if q.get("change_pct", 0) < -3]
        if bottom_movers:
            ctx_parts.append(f"跌幅较大(关注超跌反弹): {' | '.join(bottom_movers[:5])}")

    return "\n".join(ctx_parts)


def ai_quick_pick(quotes, sentiment, sector_ranking, fund_flow, concept_ranking, indices):
    """
    一键选股：自动分析市场，推荐8只短线标的

    返回格式: {success, stocks: [{code, name, reason, score, hold_days, stop_loss, target}], summary}
    """
    market_ctx = _build_market_context(quotes, sentiment, sector_ranking, fund_flow, indices)
    if concept_ranking and concept_ranking.get("top"):
        concept_str = " | ".join(
            f"{r['name']}({r['change_pct']:+.1f}%)"
            for r in concept_ranking["top"][:8]
        )
        market_ctx += f"\n概念涨幅TOP8: {concept_str}"

    # 精选热点股票数据
    hot_stocks = _extract_hot_stocks(quotes, sector_ranking, fund_flow)

    system_prompt = f"""你是一位专业的A股短线交易分析师。

{SHORT_TERM_PROFILE}

【当前市场概况】
{market_ctx}

【候选股票池·全市场实时数据（板块龙头+涨幅领先+成交活跃，不限于用户监控股票）】
{hot_stocks}

【你的任务】
请从候选池中精选8只最适合短线（1-5天持有）操作的股票。候选池已覆盖全市场热门板块龙头股。

⚠️ 硬性过滤规则（违反直接淘汰）：
1. 【绝对禁止推荐涨停股】涨幅≥9.8%的股票已封板，无法买入！涨停板次日高开低走概率>60%，追板风险极大。候选池中标"⚠️已涨停"的股票一票否决！
2. 【禁止推荐跌停股】跌幅≤-9.8%为弱势确认，次日大概率继续下跌。
3. 【警惕接近涨停股】涨幅7%-9.5%的股票如果已无买入窗口，也应排除。

✅ 优先选股标准：
1. 所属板块今日强势（涨幅>0%或有资金持续流入），但个股本身涨幅在2%-7%之间最佳
2. 技术面多头排列或刚出现金叉信号，有继续上涨空间
3. 有量能支撑（换手率3%-15%适中，不能太低缩量也不能太高异常放量）
4. 距离前期压力位有一定空间（上方无密集套牢盘）
5. 龙头股优先于跟风股，但龙头股已涨停则选次龙头
6. 优先选择处于板块轮动早期、刚启动的标的，而非已连续大涨数日的标的

【输出格式】严格按以下JSON格式输出，不要包含任何其他文字：
{{
  "summary": "一句话总结当前市场环境和核心策略",
  "stocks": [
    {{
      "code": "股票代码(6位)",
      "name": "股票名称",
      "score": 评分(1-100),
      "reason": "详细买入逻辑，包含技术面、资金面、板块面三个维度的分析，80-150字",
      "hold_days": "建议持有天数(1-5)",
      "stop_loss": "止损价",
      "target": "目标价"
    }}
  ]
}}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "请严格按照JSON格式输出8只最佳短线标的，不要输出任何其他内容。"},
    ]

    data, err = _call_deepseek(messages, temperature=0.6, max_tokens=4096)
    if err:
        return {"success": False, "error": err}

    try:
        content = data["choices"][0]["message"]["content"]
        # 提取 JSON（处理可能的 markdown 代码块包装）
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        result = json.loads(content.strip())
        return {"success": True, **result}
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        return {"success": False, "error": f"解析失败: {e}", "raw": content}


def _extract_hot_stocks(quotes, sector_ranking, fund_flow):
    """从全市场行情中提取热点候选股（板块龙头 + 涨幅前列 + 成交活跃）"""
    if not quotes:
        return "暂无实时行情数据"

    # 收集所有板块领涨股
    leader_codes = set()
    if sector_ranking and sector_ranking.get("top"):
        for r in sector_ranking["top"]:
            lc = r.get("leader_code", "")
            if lc and lc != "-":
                leader_codes.add(lc)

    # 精选：领涨股 + 涨幅前20 + 成交额前15（扩大范围覆盖全市场）
    hot_codes = set(leader_codes)
    sorted_by_change = sorted(quotes.items(), key=lambda x: x[1].get("change_pct", 0), reverse=True)
    for code, _ in sorted_by_change[:20]:
        hot_codes.add(code)
    sorted_by_amount = sorted(quotes.items(), key=lambda x: x[1].get("amount_wan", 0), reverse=True)
    for code, _ in sorted_by_amount[:15]:
        hot_codes.add(code)

    # 格式化（最多50只，标注板块龙头和涨停/跌停状态）
    lines = []
    limit_up_codes = []  # 已涨停股票列表
    for code in list(hot_codes)[:50]:
        q = quotes.get(code, {})
        if not q:
            continue
        change = q.get('change_pct', 0)
        # 标记涨跌停状态（A股主板±10%，创业板/科创板±20%）
        tags = []
        if change >= 9.8:
            tags.append("⚠️已涨停")
            limit_up_codes.append(code)
        elif change >= 7:
            tags.append("接近涨停")
        elif change <= -9.8:
            tags.append("已跌停")
        elif change <= -7:
            tags.append("接近跌停")
        if code in leader_codes:
            tags.append("板块龙头")
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        lines.append(
            f"{q.get('name', code)}({code}) "
            f"价格{q['price']:.2f} 涨幅{q['change_pct']:+.2f}% "
            f"换手{q.get('turnover_pct', 0):.1f}% "
            f"量比{q.get('vol_ratio', 0):.1f} "
            f"成交额{q.get('amount_wan', 0)/10000:.1f}亿{tag_str}"
        )
    
    # 在候选池末尾添加涨停股排除提示
    if limit_up_codes:
        lines.append(f"\n⚠️ 注意：以下股票已涨停无法买入，请勿推荐: {', '.join(limit_up_codes)}")
    return "\n".join(lines)


def ai_screen(query, quotes, sentiment, sector_ranking, fund_flow, concept_ranking, indices):
    """
    对话式选股：用户自由提问

    返回格式: {success, answer, stocks: [...]}
    """
    market_ctx = _build_market_context(quotes, sentiment, sector_ranking, fund_flow, indices)
    if concept_ranking and concept_ranking.get("top"):
        concept_str = " | ".join(
            f"{r['name']}({r['change_pct']:+.1f}%)"
            for r in concept_ranking["top"][:8]
        )
        market_ctx += f"\n概念涨幅: {concept_str}"

    system_prompt = f"""你是StockMonitor的AI选股助手，专为A股短线交易者服务。

{SHORT_TERM_PROFILE}

【当前市场概况（全市场数据，不限于用户持仓）】
{market_ctx}

【你的能力】
- 根据用户条件从全市场筛选股票（技术面、资金面、板块面、基本面）
- 分析当前市场热点和主线板块
- 评估个股短线操作价值和风险
- 回答A股交易相关问题

【重要】你的分析基于全市场实时数据，包括所有板块龙头和活跃标的，不局限于用户监控列表。
选股时务必优先考虑当前市场主线板块的龙头股。

【输出要求】
1. 先给出你的分析和结论
2. 如果涉及股票推荐，在末尾用如下JSON格式列出：
```json
{{"stocks": [{{"code": "000001", "name": "平安银行", "reason": "推荐理由30-50字"}}]}}
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
        if "```json" in content:
            try:
                json_str = content.split("```json")[1].split("```")[0]
                stocks = json.loads(json_str.strip()).get("stocks", [])
            except:
                pass

        return {"success": True, "answer": content, "stocks": stocks}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    print("[AI Screener] 模块就绪")
    print(f"  Model: {DEEPSEEK_MODEL}")
    print(f"  Key: {'已配置' if DEEPSEEK_API_KEY else '未配置'}")
