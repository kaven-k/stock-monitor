"""
信号引擎 v1.0
- 多指标共振信号分析（MA + MACD + RSI + BOLL）
- 加权打分体系（-100 ~ +100）
- 后端化 MA 信号计算（原前端 Charts.analyzeMA）
"""
from data_fetcher import calculate_ma, calculate_macd, calculate_rsi, calculate_boll, calculate_vwma, calculate_atr


class SignalEngine:
    """多指标共振信号引擎"""

    def analyze(self, kline):
        """
        对单只股票进行多指标共振分析
        kline: [{date, open, high, low, close, volume, ...}, ...] 至少60条

        返回: {
            score: 加权总分 (-100 ~ +100),
            level: 'strong_buy' | 'buy' | 'neutral' | 'sell' | 'strong_sell',
            level_text: 评级中文描述,
            signals: [{type, text}, ...],
            details: {ma, macd, rsi, boll}
        }
        """
        if not kline or len(kline) < 60:
            return None

        closes = [d['close'] for d in kline]
        volumes = [d.get('volume', 0) for d in kline]

        # 计算指标（所有函数都接受 dict 格式的 kline 数据）
        ma_result = calculate_ma(kline, [5, 10, 20, 60])
        ma5 = ma_result.get('ma5', [])
        ma10 = ma_result.get('ma10', [])
        ma20 = ma_result.get('ma20', [])
        ma60 = ma_result.get('ma60', [])
        macd_result = calculate_macd(kline)
        rsi6 = calculate_rsi(kline, 6)
        rsi14 = calculate_rsi(kline, 14)
        boll_result = calculate_boll(kline)

        last = len(kline) - 1
        last_close = closes[last]

        c5, c10, c20, c60 = ma5[last], ma10[last], ma20[last], ma60[last]
        p5, p20 = ma5[last-1] if last > 0 else None, ma20[last-1] if last > 0 else None

        score = 0
        signals = []

        # ========== MA 均线分析（权重最大） ==========
        # 1. 价格 vs MA20 (核心趋势) ±15
        if last_close > c20:
            signals.append({'type': 'bullish', 'text': f'价格 {last_close:.2f} > MA20 {c20:.2f}，短期上升趋势', 'source': 'ma'})
            score += 15
        else:
            signals.append({'type': 'bearish', 'text': f'价格 {last_close:.2f} < MA20 {c20:.2f}，短期下降趋势', 'source': 'ma'})
            score -= 15

        # 2. MA5 vs MA20 金叉/死叉 ±25
        if c5 and c20:
            if p5 is not None and p20 is not None:
                if c5 > c20 and p5 <= p20:
                    signals.append({'type': 'bullish', 'text': '⚠️ 金叉 ▲ (MA5上穿MA20)，强烈买入信号', 'source': 'ma'})
                    score += 25
                elif c5 < c20 and p5 >= p20:
                    signals.append({'type': 'bearish', 'text': '⚠️ 死叉 ▼ (MA5下穿MA20)，强烈卖出信号', 'source': 'ma'})
                    score -= 25
                elif c5 > c20:
                    signals.append({'type': 'bullish', 'text': f'MA5 {c5:.2f} > MA20 {c20:.2f}，多头排列', 'source': 'ma'})
                    score += 10
                else:
                    signals.append({'type': 'bearish', 'text': f'MA5 {c5:.2f} < MA20 {c20:.2f}，空头排列', 'source': 'ma'})
                    score -= 10

        # 3. 均线排列 ±20
        if all(x is not None for x in [c5, c10, c20, c60]):
            if c5 > c10 > c20 > c60:
                signals.append({'type': 'bullish', 'text': '均线多头排列 (MA5>MA10>MA20>MA60)，强势上涨', 'source': 'ma'})
                score += 20
            elif c5 < c10 < c20 < c60:
                signals.append({'type': 'bearish', 'text': '均线空头排列 (MA5<MA10<MA20<MA60)，弱势下跌', 'source': 'ma'})
                score -= 20
            else:
                signals.append({'type': 'neutral', 'text': '均线交织，方向不明', 'source': 'ma'})

        # 4. 价格 vs MA60 (长线) ±10
        if c60 and last_close > c60:
            signals.append({'type': 'bullish', 'text': f'价格 > MA60 {c60:.2f}，中长期向上', 'source': 'ma'})
            score += 10
        elif c60:
            signals.append({'type': 'bearish', 'text': f'价格 < MA60 {c60:.2f}，中长期向下', 'source': 'ma'})
            score -= 10

        # ========== MACD 共振确认 ±15 ==========
        macd_dif = macd_result['dif']
        macd_dea = macd_result['dea']
        if macd_dif and macd_dea and len(macd_dif) > 1 and len(macd_dea) > 1:
            cdif, p_dif = macd_dif[last], macd_dif[last-1]
            cdea, p_dea = macd_dea[last], macd_dea[last-1]
            if cdif is not None and cdea is not None and p_dif is not None and p_dea is not None:
                if cdif > cdea and p_dif <= p_dea:
                    signals.append({'type': 'bullish', 'text': 'MACD金叉 (DIF上穿DEA)，动能转强', 'source': 'macd'})
                    score += 15
                elif cdif < cdea and p_dif >= p_dea:
                    signals.append({'type': 'bearish', 'text': 'MACD死叉 (DIF下穿DEA)，动能转弱', 'source': 'macd'})
                    score -= 15
                elif cdif > cdea:
                    signals.append({'type': 'bullish', 'text': 'MACD多头 (DIF > DEA)，动能持续', 'source': 'macd'})
                    score += 5
                else:
                    score -= 5

        # ========== RSI 过滤（避免追高/杀跌） ±5 ==========
        rsi14_val = rsi14[last] if rsi14 and len(rsi14) > last else None
        if rsi14_val is not None:
            if rsi14_val >= 80:
                signals.append({'type': 'bearish', 'text': f'RSI14={rsi14_val:.0f}，超买区域，追高风险大', 'source': 'rsi'})
                score -= 10
            elif rsi14_val >= 70:
                score -= 5
            elif rsi14_val <= 20:
                signals.append({'type': 'bullish', 'text': f'RSI14={rsi14_val:.0f}，超卖区域，可能反弹', 'source': 'rsi'})
                score += 10
            elif rsi14_val <= 30:
                score += 5
            else:
                score += 5  # RSI 在 30-70 正常区间，正面

        # ========== BOLL 辅助 ±10 ==========
        boll_up = boll_result['upper']
        boll_low = boll_result['lower']
        if boll_up and boll_low and len(boll_up) > last:
            bu, bl = boll_up[last], boll_low[last]
            if bu and bl:
                if last_close >= bu:
                    signals.append({'type': 'bearish', 'text': f'价格触及BOLL上轨 {bu:.2f}，高位回调风险', 'source': 'boll'})
                    # 若同时有金叉+MACH金叉，则是强势突破而非见顶
                    if score >= 30:
                        score += 5  # 趋势强时不轻易看空
                    else:
                        score -= 10
                elif last_close <= bl:
                    signals.append({'type': 'bullish', 'text': f'价格触及BOLL下轨 {bl:.2f}，超跌反弹机会', 'source': 'boll'})
                    score += 10

        # ========== 连续涨跌（追高风险/超跌机会） ==========
        consecutive_up = 0
        for i in range(last, max(last-5, -1), -1):
            if i > 0 and closes[i] > closes[i-1]:
                consecutive_up += 1
            else:
                break
        if consecutive_up >= 3:
            signals.append({'type': 'neutral', 'text': f'已连续上涨{consecutive_up}天，追高风险', 'source': 'trend'})
            score -= 10

        # ========== VWMA 成交量加权确认（±5） ==========
        vwma20 = calculate_vwma(kline, 20)
        vwma20_val = vwma20[last] if vwma20 and len(vwma20) > last else None
        if vwma20_val and c20:
            if last_close > vwma20_val and c5 > vwma20_val:
                signals.append({'type': 'bullish', 'text': f'VWMA {vwma20_val:.2f} < MA20 {c20:.2f}，实际成本在低位，支撑有效', 'source': 'vwma'})
                score += 5

        # ========== ATR 波动率（用于动态止损参考） ==========
        atr14 = calculate_atr(kline, 14)
        atr14_val = atr14[last] if atr14 and len(atr14) > last else None
        atr_stop = None
        if atr14_val and last_close:
            atr_stop = round(last_close - 2 * atr14_val, 2)
            if atr14_val / last_close > 0.05:
                signals.append({'type': 'neutral', 'text': f'ATR={atr14_val:.2f}，波动率偏高(>{((atr14_val/last_close)*100):.1f}%)，注意仓位控制', 'source': 'atr'})

        # ========== 评分映射 ==========
        score = max(-100, min(100, score))
        level, level_text, color = self._score_to_level(score)

        return {
            'score': score,
            'level': level,
            'level_text': level_text,
            'color': color,
            'signals': signals,
            'details': {
                'last_close': round(last_close, 2),
                'ma5': round(c5, 2) if c5 else None,
                'ma10': round(c10, 2) if c10 else None,
                'ma20': round(c20, 2) if c20 else None,
                'ma60': round(c60, 2) if c60 else None,
                'vwma20': round(vwma20_val, 2) if vwma20_val else None,
                'rsi14': round(rsi14_val, 1) if rsi14_val else None,
                'boll_upper': round(boll_up[last], 2) if boll_up and len(boll_up) > last and boll_up[last] else None,
                'boll_lower': round(boll_low[last], 2) if boll_low and len(boll_low) > last and boll_low[last] else None,
                'atr14': round(atr14_val, 2) if atr14_val else None,
                'atr_stop': atr_stop,
            },
        }

    def _score_to_level(self, score):
        """评分 → 信号等级"""
        if score >= 60:
            return 'strong_buy', '🟢 强烈看多 — 多指标共振向上，可考虑买入', '#e03131'
        elif score >= 30:
            return 'buy', '🟡 偏多 — 短期趋势向上，可逢低关注', '#e8590c'
        elif score > -30:
            return 'neutral', '⚪ 震荡观望 — 方向不明，建议等待信号', '#868e96'
        elif score > -60:
            return 'sell', '🔵 偏空 — 短期趋势向下，谨慎持有', '#2f9e44'
        else:
            return 'strong_sell', '🟢 强烈看空 — 多指标共振向下，可考虑减仓', '#2f9e44'

    def bulk_analyze(self, kline_data):
        """
        批量分析多只股票
        kline_data: {code: [{date,open,high,low,close,volume}, ...]}
        返回: {code: {score, level, level_text, signals, details}, ...}
        """
        results = {}
        for code, kline in kline_data.items():
            r = self.analyze(kline)
            if r:
                results[code] = r
        return results


# 历史数据缓存（监控循环复用）
_history_cache = {}

def load_history_cache(db_module, codes, days=60):
    """初始化：预加载所有监控股票的历史K线到内存缓存"""
    global _history_cache
    _history_cache = {}
    for code in codes:
        hist = db_module.get_price_history(code, days)
        if hist and len(hist) >= 30:
            _history_cache[code] = hist
    return len(_history_cache)

def update_history_cache(code, new_bar):
    """增量更新：将最新K线追加到缓存"""
    if code in _history_cache:
        cache = _history_cache[code]
        if not cache or cache[-1].get('trade_date') != new_bar.get('trade_date'):
            cache.append(new_bar)
            if len(cache) > 120:
                cache.pop(0)

def get_cached_history(code):
    """获取缓存的历史K线"""
    return _history_cache.get(code, [])
