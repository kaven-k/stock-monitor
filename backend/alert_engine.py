"""
股票监控系统 - 预警引擎
支持多种预警规则类型
"""
import json
from datetime import datetime


class AlertEngine:
    """预警引擎 - 检查各类预警规则是否触发"""

    def __init__(self, db_module):
        self.db = db_module

    def check_all_rules(self, quotes):
        """
        检查所有预警规则
        quotes: {code: {price, change_pct, volume, turnover_pct, ...}}
        返回: [(rule_id, rule_name, code, stock_name, alert_type, alert_msg), ...]
        """
        rules = self.db.get_all_alert_rules()
        triggered = []

        for rule in rules:
            rule_id = rule['id']
            rule_name = rule['name']
            rule_type = rule['rule_type']
            params = rule.get('params', {})
            stocks = rule.get('stocks', [])

            for stock in stocks:
                code = stock['stock_code']
                stock_name = stock.get('stock_name', '')
                quote = quotes.get(code)
                if not quote:
                    continue

                alert = self._check_rule(rule_id, rule_name, rule_type, params, code, stock_name, quote)
                if alert:
                    triggered.append(alert)

        return triggered

    def _check_rule(self, rule_id, rule_name, rule_type, params, code, stock_name, quote):
        """检查单个规则"""
        checkers = {
            "price_up": self._check_price_up,
            "price_down": self._check_price_down,
            "change_up": self._check_change_up,
            "change_down": self._check_change_down,
            "volume_surge": self._check_volume_surge,
            "turnover_high": self._check_turnover_high,
            "amplitude_high": self._check_amplitude_high,
            "price_break_ma": self._check_price_break_ma,
            "continuous_up": self._check_continuous_up,
            "continuous_down": self._check_continuous_down,
            "volume_ratio": self._check_volume_ratio,
            "limit_up": self._check_limit_up,
            "limit_down": self._check_limit_down,
            "ma_signal_change": self._check_ma_signal_change,
            "compound": self._check_compound,
        }

        checker = checkers.get(rule_type)
        if checker:
            return checker(rule_id, rule_name, params, code, stock_name, quote)
        return None

    def _check_price_up(self, rule_id, rule_name, params, code, stock_name, quote):
        """价格向上突破"""
        threshold = float(params.get("threshold", 0))
        if quote.get("price", 0) >= threshold:
            return (rule_id, rule_name, code, stock_name, "price_up",
                    f"{stock_name}({code}) 价格 {quote['price']} 已突破 {threshold} 元")
        return None

    def _check_price_down(self, rule_id, rule_name, params, code, stock_name, quote):
        """价格向下跌破"""
        threshold = float(params.get("threshold", 0))
        if quote.get("price", 0) <= threshold:
            return (rule_id, rule_name, code, stock_name, "price_down",
                    f"{stock_name}({code}) 价格 {quote['price']} 已跌破 {threshold} 元")
        return None

    def _check_change_up(self, rule_id, rule_name, params, code, stock_name, quote):
        """涨幅超过阈值"""
        threshold = float(params.get("threshold", 5))
        change_pct = quote.get("change_pct", 0)
        if change_pct >= threshold:
            return (rule_id, rule_name, code, stock_name, "change_up",
                    f"{stock_name}({code}) 涨幅 {change_pct}% 超过 {threshold}%")
        return None

    def _check_change_down(self, rule_id, rule_name, params, code, stock_name, quote):
        """跌幅超过阈值"""
        threshold = float(params.get("threshold", 5))
        change_pct = abs(quote.get("change_pct", 0))
        if quote.get("change_pct", 0) <= -threshold:
            return (rule_id, rule_name, code, stock_name, "change_down",
                    f"{stock_name}({code}) 跌幅 {quote['change_pct']}% 超过 {threshold}%")
        return None

    def _check_volume_surge(self, rule_id, rule_name, params, code, stock_name, quote):
        """成交量异动"""
        multiplier = float(params.get("multiplier", 3))
        avg_vol = self._get_avg_volume(code, int(params.get("avg_days", 20)))
        current_vol = quote.get("volume", 0)
        if avg_vol > 0 and current_vol >= avg_vol * multiplier:
            return (rule_id, rule_name, code, stock_name, "volume_surge",
                    f"{stock_name}({code}) 成交量 {current_vol} 为均量({avg_vol:.0f})的 {current_vol/avg_vol:.1f} 倍")
        return None

    def _check_turnover_high(self, rule_id, rule_name, params, code, stock_name, quote):
        """换手率过高"""
        threshold = float(params.get("threshold", 10))
        turnover = quote.get("turnover_pct", 0)
        if turnover >= threshold:
            return (rule_id, rule_name, code, stock_name, "turnover_high",
                    f"{stock_name}({code}) 换手率 {turnover}% 超过 {threshold}%")
        return None

    def _check_amplitude_high(self, rule_id, rule_name, params, code, stock_name, quote):
        """振幅过高"""
        threshold = float(params.get("threshold", 10))
        amplitude = quote.get("amplitude_pct", 0)
        if amplitude >= threshold:
            return (rule_id, rule_name, code, stock_name, "amplitude_high",
                    f"{stock_name}({code}) 振幅 {amplitude}% 超过 {threshold}%")
        return None

    def _check_price_break_ma(self, rule_id, rule_name, params, code, stock_name, quote):
        """价格突破均线 - 需要在data_fetcher中配合计算"""
        direction = params.get("direction", "up")
        ma_period = int(params.get("ma_period", 20))
        # 从历史数据中计算MA
        hist = self._get_price_history(code, ma_period * 2)
        if not hist or len(hist) < ma_period:
            return None
        closes = [h["close"] for h in hist[-ma_period:]]
        ma = sum(closes) / len(closes)
        price = quote.get("price", 0)
        prev_price = hist[-2]["close"] if len(hist) >= 2 else price
        prev_ma = (sum(closes[:-1]) / (ma_period - 1)) if len(closes) > 1 else ma

        if direction == "up":
            if price > ma and prev_price <= prev_ma:
                return (rule_id, rule_name, code, stock_name, "ma_break",
                        f"{stock_name}({code}) 价格 {price} 向上突破 MA{ma_period}({ma:.2f})")
        else:
            if price < ma and prev_price >= prev_ma:
                return (rule_id, rule_name, code, stock_name, "ma_break",
                        f"{stock_name}({code}) 价格 {price} 向下跌破 MA{ma_period}({ma:.2f})")
        return None

    def _check_continuous_up(self, rule_id, rule_name, params, code, stock_name, quote):
        """连续上涨"""
        return self._check_continuous(rule_id, rule_name, params, code, stock_name, True)

    def _check_continuous_down(self, rule_id, rule_name, params, code, stock_name, quote):
        """连续下跌"""
        return self._check_continuous(rule_id, rule_name, params, code, stock_name, False)

    def _check_continuous(self, rule_id, rule_name, params, code, stock_name, is_up):
        """检查连续涨跌"""
        days = int(params.get("days", 3))
        hist = self._get_price_history(code, days + 5)
        if not hist or len(hist) < days + 1:
            return None
        recent = hist[-days-1:]
        consecutive = 0
        for i in range(len(recent)-1, 0, -1):
            if is_up and recent[i]["close"] > recent[i-1]["close"]:
                consecutive += 1
            elif not is_up and recent[i]["close"] < recent[i-1]["close"]:
                consecutive += 1
            else:
                break
        if consecutive >= days:
            direction = "上涨" if is_up else "下跌"
            return (rule_id, rule_name, code, stock_name,
                    "continuous_up" if is_up else "continuous_down",
                    f"{stock_name}({code}) 连续{direction} {consecutive} 天")
        return None

    def _check_volume_ratio(self, rule_id, rule_name, params, code, stock_name, quote):
        """量比异常"""
        threshold = float(params.get("threshold", 2))
        vol_ratio = quote.get("vol_ratio", 0)
        if vol_ratio >= threshold:
            return (rule_id, rule_name, code, stock_name, "volume_ratio",
                    f"{stock_name}({code}) 量比 {vol_ratio} 超过 {threshold}")
        return None

    def _check_limit_up(self, rule_id, rule_name, params, code, stock_name, quote):
        """触及涨停"""
        price = quote.get("price", 0)
        limit_up = quote.get("limit_up", 0)
        if limit_up > 0 and price >= limit_up:
            return (rule_id, rule_name, code, stock_name, "limit_up",
                    f"{stock_name}({code}) 涨停! 价格 {price}")
        return None

    def _check_limit_down(self, rule_id, rule_name, params, code, stock_name, quote):
        """触及跌停"""
        price = quote.get("price", 0)
        limit_down = quote.get("limit_down", 0)
        if limit_down > 0 and price <= limit_down:
            return (rule_id, rule_name, code, stock_name, "limit_down",
                    f"{stock_name}({code}) 跌停! 价格 {price}")
        return None

    def _check_ma_signal_change(self, rule_id, rule_name, params, code, stock_name, quote):
        """MA信号变化（由信号引擎驱动，此处为占位检查器）"""
        # 信号变化检测在监控循环中通过 signal_engine 实现
        # 该规则通过 WebSocket signal_update 事件推送到前端
        return None

    def _check_compound(self, rule_id, rule_name, params, code, stock_name, quote):
        """复合条件预警：支持 AND/OR 组合多条子规则"""
        operator = params.get("operator", "and")
        sub_rules = params.get("rules", [])
        if not sub_rules:
            return None

        sub_checkers = {
            "price_up": self._check_price_up,
            "price_down": self._check_price_down,
            "change_up": self._check_change_up,
            "change_down": self._check_change_down,
            "volume_surge": self._check_volume_surge,
            "turnover_high": self._check_turnover_high,
            "amplitude_high": self._check_amplitude_high,
            "price_break_ma": self._check_price_break_ma,
            "volume_ratio": self._check_volume_ratio,
            "limit_up": self._check_limit_up,
            "limit_down": self._check_limit_down,
        }

        results = []
        for sr in sub_rules:
            checker = sub_checkers.get(sr.get("type", ""))
            if checker:
                r = checker(rule_id, rule_name, sr.get("params", {}), code, stock_name, quote)
                results.append(r is not None)

        if operator == "and":
            triggered = all(results) and len(results) > 0
        else:
            triggered = any(results)

        if triggered:
            sub_types = [r.get("type", "") for r in sub_rules]
            return (rule_id, rule_name, code, stock_name, "compound",
                    f"{stock_name}({code}) 复合条件触发: {', '.join(sub_types)}")

        return None

    def _get_avg_volume(self, code, days=20):
        """获取平均成交量"""
        hist = self._get_price_history(code, days + 5)
        if not hist or len(hist) < days:
            return 0
        volumes = [h.get("volume", 0) for h in hist[-days:]]
        return sum(volumes) / len(volumes) if volumes else 0

    def _get_price_history(self, code, days):
        """获取历史价格数据"""
        return self.db.get_price_history(code, days)
