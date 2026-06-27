/**
 * StockMonitor - ECharts 图表组件 v2.0
 * K线图(日/周/月) + MACD子图 + BOLL布林带 + 技术指标
 */
(function() {
const isDark = () => document.documentElement.getAttribute('data-theme') === 'dark';
const colorUp = () => isDark() ? '#ef4444' : '#e03131';
const colorDown = () => isDark() ? '#22c55e' : '#2f9e44';
const textColor = () => isDark() ? '#9ca3af' : '#606770';
const gridColor = () => isDark() ? '#27272a' : '#f0f0f0';
const axisColor = () => isDark() ? '#3f3f46' : '#e0e0e0';

const Charts = {
    instances: {},
    _resizeHandlers: {},

    dispose(id) {
        if (this.instances[id]) {
            if (this._resizeHandlers[id]) {
                window.removeEventListener('resize', this._resizeHandlers[id]);
                delete this._resizeHandlers[id];
            }
            this.instances[id].dispose();
            delete this.instances[id];
        }
    },

    disposeAll() {
        Object.keys(this.instances).forEach(id => this.dispose(id));
        Object.keys(this._resizeHandlers).forEach(k => {
            window.removeEventListener('resize', this._resizeHandlers[k]);
        });
        this._resizeHandlers = {};
        this.instances = {};
    },

    _onResize(id) {
        const handler = () => {
            const inst = this.instances[id];
            if (inst && !inst.isDisposed()) inst.resize();
        };
        this._resizeHandlers[id] = handler;
        window.addEventListener('resize', handler);
    },

    renderKline(containerId, kline, indicators, period = 'day') {
        const container = document.getElementById(containerId);
        if (!container || !kline || kline.length === 0) return;

        this.dispose(containerId);
        const chart = echarts.init(container, isDark() ? 'dark' : undefined);
        this.instances[containerId] = chart;

        const dates = kline.map(d => d.date);
        const ohlc = kline.map(d => [d.open, d.close, d.low, d.high]);
        const volumes = kline.map(d => d.volume);

        const ma5 = indicators.ma5 || [];
        const ma10 = indicators.ma10 || [];
        const ma20 = indicators.ma20 || [];
        const ma60 = indicators.ma60 || [];
        const boll = indicators.boll || { upper: [], mid: [], lower: [] };

        // 金叉/死叉
        const goldenCross = [], deathCross = [];
        for (let i = 1; i < Math.min(ma5.length, ma20.length); i++) {
            const p5 = ma5[i-1], p20 = ma20[i-1], c5 = ma5[i], c20 = ma20[i];
            if (p5 === null || p20 === null || c5 === null || c20 === null) continue;
            if (p5 <= p20 && c5 > c20) goldenCross.push({ name: '金叉 ▲', coord: [dates[i], 'min'], value: '买入', symbol: 'triangle', symbolSize: 14, itemStyle: { color: colorUp() }, label: { show: true, position: 'bottom', color: colorUp(), fontSize: 11, fontWeight: 'bold' } });
            if (p5 >= p20 && c5 < c20) deathCross.push({ name: '死叉 ▼', coord: [dates[i], 'max'], value: '卖出', symbol: 'triangle', symbolSize: 14, symbolRotate: 180, itemStyle: { color: colorDown() }, label: { show: true, position: 'top', color: colorDown(), fontSize: 11, fontWeight: 'bold' } });
        }

        const periodLabel = { day: '日K', week: '周K', month: '月K' }[period] || '日K';

        const option = {
            animation: false,
            backgroundColor: isDark() ? '#18181b' : '#ffffff',
            tooltip: {
                trigger: 'axis',
                axisPointer: { type: 'cross' },
                formatter: function(params) {
                    const i = params[0]?.dataIndex;
                    if (i === undefined) return '';
                    const d = kline[i];
                    const m5 = ma5[i], m20 = ma20[i];
                    let signal = '';
                    if (m5 && m20) {
                        signal = m5 > m20
                            ? '<br/><span style="color:' + colorUp() + ';font-weight:600">▲ MA5 > MA20 (多头)</span>'
                            : '<br/><span style="color:' + colorDown() + ';font-weight:600">▼ MA5 < MA20 (空头)</span>';
                    }
                    const bu = boll.upper[i], bl = boll.lower[i];
                    let bollInfo = '';
                    if (bu && bl) {
                        bollInfo = '<br/>BOLL上轨: ' + bu.toFixed(2) + ' | 下轨: ' + bl.toFixed(2);
                        if (d.close >= bu) bollInfo += ' <span style="color:' + colorDown() + '">(触及上轨)</span>';
                        else if (d.close <= bl) bollInfo += ' <span style="color:' + colorUp() + '">(触及下轨)</span>';
                    }
                    return '<b>' + d.date + ' (' + periodLabel + ')</b><br/>' +
                        '开盘: ' + d.open.toFixed(2) + '<br/>' +
                        '收盘: ' + d.close.toFixed(2) + '<br/>' +
                        '最高: <span style="color:' + colorUp() + '">' + d.high.toFixed(2) + '</span><br/>' +
                        '最低: <span style="color:' + colorDown() + '">' + d.low.toFixed(2) + '</span><br/>' +
                        'MA5: ' + (m5 ? m5.toFixed(2) : '-') + ' | MA20: ' + (m20 ? m20.toFixed(2) : '-') + signal + bollInfo + '<br/>' +
                        '成交量: ' + Charts.formatVol(d.volume);
                }
            },
            axisPointer: { link: [{ xAxisIndex: 'all' }] },
            grid: [
                { left: '8%', right: '3%', top: '5%', height: '45%' },
                { left: '8%', right: '3%', top: '56%', height: '15%' },
                { left: '8%', right: '3%', top: '76%', height: '16%' }
            ],
            xAxis: [
                { type: 'category', data: dates, gridIndex: 0, axisLine: { onZero: false, lineStyle: { color: axisColor() } }, axisLabel: { show: false }, axisTick: { show: false } },
                { type: 'category', data: dates, gridIndex: 1, axisLabel: { show: false }, axisTick: { show: false }, axisLine: { lineStyle: { color: axisColor() } } },
                { type: 'category', data: dates, gridIndex: 2, axisLabel: { formatter: v => v.slice(5), fontSize: 10, color: textColor() }, axisTick: { show: false }, axisLine: { lineStyle: { color: axisColor() } } }
            ],
            yAxis: [
                { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: gridColor() } }, axisLabel: { fontSize: 10, color: textColor() } },
                { scale: true, gridIndex: 1, splitLine: { show: false }, axisLabel: { fontSize: 9, color: textColor(), formatter: v => v >= 1e8 ? (v/1e8).toFixed(1)+'亿' : (v/1e4).toFixed(0)+'万' } },
                { scale: true, gridIndex: 2, splitLine: { lineStyle: { color: gridColor() } }, axisLabel: { fontSize: 9, color: textColor() } }
            ],
            dataZoom: [{ type: 'inside', xAxisIndex: [0,1,2], start: 70, end: 100 }],
            series: [
                { name: 'K线', type: 'candlestick', data: ohlc, xAxisIndex: 0, yAxisIndex: 0,
                  itemStyle: { color: colorUp(), color0: colorDown(), borderColor: colorUp(), borderColor0: colorDown() } },
                { name: 'MA5', type: 'line', data: ma5, symbol: 'none', smooth: true, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1, color: colorUp() },
                  markPoint: { data: goldenCross.slice(-5), symbol: 'pin', symbolSize: 30, label: { fontSize: 10 } } },
                { name: 'MA20', type: 'line', data: ma20, symbol: 'none', smooth: true, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1, color: '#6366f1' },
                  markPoint: { data: deathCross.slice(-5), symbol: 'pin', symbolSize: 30, symbolRotate: 180, label: { fontSize: 10 } } },
                // BOLL 布林带
                { name: 'BOLL上轨', type: 'line', data: boll.upper || [], symbol: 'none', xAxisIndex: 0, yAxisIndex: 0,
                  lineStyle: { width: 1, color: '#a78bfa', type: 'dashed' }, itemStyle: { color: '#a78bfa' } },
                { name: 'BOLL中轨', type: 'line', data: boll.mid || [], symbol: 'none', xAxisIndex: 0, yAxisIndex: 0,
                  lineStyle: { width: 1, color: '#8b5cf6' }, itemStyle: { color: '#8b5cf6' } },
                { name: 'BOLL下轨', type: 'line', data: boll.lower || [], symbol: 'none', xAxisIndex: 0, yAxisIndex: 0,
                  lineStyle: { width: 1, color: '#a78bfa', type: 'dashed' }, itemStyle: { color: '#a78bfa' } },
                // 成交量
                { name: '成交量', type: 'bar', data: volumes, xAxisIndex: 1, yAxisIndex: 1,
                  itemStyle: { color: p => kline[p.dataIndex].close >= kline[p.dataIndex].open ? colorUp() : colorDown() } },
                // MACD 子图
                { name: 'DIF', type: 'line', data: indicators.dif || [], symbol: 'none', smooth: true, xAxisIndex: 2, yAxisIndex: 2,
                  lineStyle: { width: 1.5, color: colorUp() } },
                { name: 'DEA', type: 'line', data: indicators.dea || [], symbol: 'none', smooth: true, xAxisIndex: 2, yAxisIndex: 2,
                  lineStyle: { width: 1.5, color: '#6366f1' } },
                { name: 'MACD柱', type: 'bar', data: indicators.bar || [], xAxisIndex: 2, yAxisIndex: 2,
                  itemStyle: { color: p => (indicators.bar && indicators.bar[p.dataIndex] >= 0) ? colorUp() : colorDown() } },
            ]
        };

        chart.setOption(option);
        this._onResize(containerId);
        return { goldenCross, deathCross, ma5, ma20, boll };
    },

    renderIndicator(containerId, title, dates, lines, markLines = []) {
        const container = document.getElementById(containerId);
        if (!container) return;

        this.dispose(containerId);
        const chart = echarts.init(container, isDark() ? 'dark' : undefined);
        this.instances[containerId] = chart;

        const series = lines.map(l => ({
            name: l.name, type: 'line', data: l.data,
            lineStyle: { width: 1.5, color: l.color },
            symbol: 'none', smooth: true,
        }));

        if (markLines.length > 0 && series.length > 0) {
            series[0].markLine = {
                symbol: 'none', silent: true,
                data: markLines.map(ml => ({ yAxis: ml.value, label: ml.label || {}, lineStyle: ml.lineStyle || { color: gridColor(), type: 'dashed' } })),
            };
        }

        chart.setOption({
            title: { text: title, left: 10, top: 5, textStyle: { fontSize: 12, fontWeight: 'normal', color: textColor() } },
            tooltip: { trigger: 'axis' },
            legend: { data: lines.map(l => l.name), right: 10, top: 2, textStyle: { fontSize: 10, color: textColor() } },
            grid: { left: '8%', right: '3%', top: '25%', bottom: '10%' },
            xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 9, formatter: v => v.slice(5), color: textColor() }, axisTick: { show: false }, axisLine: { lineStyle: { color: axisColor() } } },
            yAxis: { scale: true, splitLine: { lineStyle: { color: gridColor() } }, axisLabel: { fontSize: 10, color: textColor() } },
            series,
        });
        this._onResize(containerId);
    },

    renderMacd(containerId, dates, indicators) {
        const container = document.getElementById(containerId);
        if (!container) return;

        this.dispose(containerId);
        const chart = echarts.init(container, isDark() ? 'dark' : undefined);
        this.instances[containerId] = chart;

        const bar = indicators.bar || [];

        chart.setOption({
            title: { text: 'MACD', left: 10, top: 5, textStyle: { fontSize: 12, fontWeight: 'normal', color: textColor() } },
            tooltip: { trigger: 'axis' },
            legend: { data: ['DIF', 'DEA', 'BAR'], right: 10, top: 2, textStyle: { fontSize: 10, color: textColor() } },
            grid: { left: '8%', right: '3%', top: '25%', bottom: '10%' },
            xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 9, formatter: v => v.slice(5), color: textColor() }, axisTick: { show: false }, axisLine: { lineStyle: { color: axisColor() } } },
            yAxis: { scale: true, splitLine: { lineStyle: { color: gridColor() } }, axisLabel: { fontSize: 10, color: textColor() } },
            series: [
                { name: 'DIF', type: 'line', data: indicators.dif || [], lineStyle: { width: 1.5, color: colorUp() }, symbol: 'none', smooth: true },
                { name: 'DEA', type: 'line', data: indicators.dea || [], lineStyle: { width: 1.5, color: '#6366f1' }, symbol: 'none', smooth: true },
                { name: 'BAR', type: 'bar', data: bar, itemStyle: { color: p => bar[p.dataIndex] >= 0 ? colorUp() : colorDown() } },
            ],
        });
        this._onResize(containerId);
    },

    formatVol(v) {
        if (v >= 1e8) return (v / 1e8).toFixed(1) + '亿';
        if (v >= 1e4) return (v / 1e4).toFixed(1) + '万';
        return v.toFixed(0);
    },

    formatAmt(a) {
        if (a >= 10000) return (a / 10000).toFixed(2) + '亿';
        return a.toFixed(2) + '万';
    },

    /**
     * 分析均线信号，返回买卖建议
     */
    analyzeMA(kline, indicators) {
        if (!kline || kline.length < 60) return null;
        
        const ma5 = indicators.ma5 || [];
        const ma10 = indicators.ma10 || [];
        const ma20 = indicators.ma20 || [];
        const ma60 = indicators.ma60 || [];
        const last = kline.length - 1;
        const lastClose = kline[last].close;
        
        const c5 = ma5[last], c10 = ma10[last], c20 = ma20[last], c60 = ma60[last];
        const p5 = ma5[last-1], p20 = ma20[last-1];
        
        const signals = [];
        let overall = 'neutral';
        
        // 1. 价格与MA20关系 (核心趋势判断)
        if (lastClose > c20) {
            signals.push({ type: 'bullish', text: `价格 ${lastClose.toFixed(2)} > MA20 ${c20.toFixed(2)}，短期处于上升趋势` });
        } else {
            signals.push({ type: 'bearish', text: `价格 ${lastClose.toFixed(2)} < MA20 ${c20.toFixed(2)}，短期处于下降趋势` });
        }
        
        // 2. MA5 vs MA20 交叉
        if (c5 > c20) {
            if (p5 && p20 && p5 <= p20) {
                signals.push({ type: 'bullish', text: '⚠️ 刚出现金叉 ▲ (MA5上穿MA20)，短期买入信号' });
                overall = 'strong_buy';
            } else {
                signals.push({ type: 'bullish', text: `MA5 ${c5.toFixed(2)} > MA20 ${c20.toFixed(2)}，多头排列` });
                overall = overall === 'neutral' ? 'buy' : overall;
            }
        } else {
            if (p5 && p20 && p5 >= p20) {
                signals.push({ type: 'bearish', text: '⚠️ 刚出现死叉 ▼ (MA5下穿MA20)，短期卖出信号' });
                overall = 'strong_sell';
            } else {
                signals.push({ type: 'bearish', text: `MA5 ${c5.toFixed(2)} < MA20 ${c20.toFixed(2)}，空头排列` });
                overall = overall === 'neutral' ? 'sell' : overall;
            }
        }
        
        // 3. 均线排列
        const aligned = c5 > c10 && c10 > c20 && c20 > c60;
        const reversed = c5 < c10 && c10 < c20 && c20 < c60;
        if (aligned) {
            signals.push({ type: 'bullish', text: '均线多头排列 (MA5>MA10>MA20>MA60)，强势上涨' });
            overall = overall === 'buy' ? 'strong_buy' : 'buy';
        } else if (reversed) {
            signals.push({ type: 'bearish', text: '均线空头排列 (MA5<MA10<MA20<MA60)，弱势下跌' });
            overall = overall === 'sell' ? 'strong_sell' : 'sell';
        } else {
            signals.push({ type: 'neutral', text: '均线交织，方向不明确，建议观望' });
        }
        
        // 4. 价格与MA60长线判断
        if (lastClose > c60) {
            signals.push({ type: 'bullish', text: `价格 > MA60 ${c60.toFixed(2)}，中长期趋势向上` });
        } else {
            signals.push({ type: 'bearish', text: `价格 < MA60 ${c60.toFixed(2)}，中长期趋势向下` });
        }
        
        const suggestions = {
            strong_buy: { text: '🟢 强烈看多', color: '#e03131', desc: '多项指标共振向上，可考虑买入' },
            buy: { text: '🟡 偏多', color: '#e8590c', desc: '短期趋势向上，可逢低关注' },
            neutral: { text: '⚪ 震荡观望', color: '#868e96', desc: '方向不明，建议等待信号明确' },
            sell: { text: '🔵 偏空', color: '#2f9e44', desc: '短期趋势向下，谨慎持有' },
            strong_sell: { text: '🟢 强烈看空', color: '#2f9e44', desc: '多项指标共振向下，可考虑减仓' },
        };
        
        return { signals, overall, suggestion: suggestions[overall] };
    },

    /** 重新渲染所有活跃图表（主题切换时调用）*/
    refreshAllThemes() {
        Object.keys(this.instances).forEach(id => {
            const inst = this.instances[id];
            if (inst && !inst.isDisposed()) {
                inst.dispose();
                delete this.instances[id];
            }
        });
    }
};

window.Charts = Charts;
})();
