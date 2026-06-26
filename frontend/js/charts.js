/**
 * StockMonitor - ECharts 图表组件
 * K线图 + 技术指标图表 (RSI/MACD/KDJ/BOLL)
 */

const Charts = {
    instances: {},

    dispose(id) {
        if (this.instances[id]) {
            this.instances[id].dispose();
            delete this.instances[id];
        }
    },

    disposeAll() {
        Object.keys(this.instances).forEach(id => {
            this.instances[id].dispose();
        });
        this.instances = {};
    },

    renderKline(containerId, kline, indicators) {
        const container = document.getElementById(containerId);
        if (!container || !kline || kline.length === 0) return;

        this.dispose(containerId);
        const chart = echarts.init(container);
        this.instances[containerId] = chart;

        const dates = kline.map(d => d.date);
        const ohlc = kline.map(d => [d.open, d.close, d.low, d.high]);
        const volumes = kline.map(d => d.volume);

        // 计算MA金叉/死叉标记点 (MA5 vs MA20)
        const ma5 = indicators.ma5 || [];
        const ma20 = indicators.ma20 || [];
        const goldenCross = []; // 金叉点 (买入信号)
        const deathCross = [];  // 死叉点 (卖出信号)
        
        for (let i = 1; i < Math.min(ma5.length, ma20.length); i++) {
            const prev5 = ma5[i-1], prev20 = ma20[i-1];
            const curr5 = ma5[i], curr20 = ma20[i];
            if (prev5 === null || prev20 === null || curr5 === null || curr20 === null) continue;
            
            // 金叉: MA5从下向上穿越MA20
            if (prev5 <= prev20 && curr5 > curr20) {
                goldenCross.push({ name: '金叉 ▲', coord: [dates[i], 'min'], value: '买入信号', symbol: 'triangle', symbolSize: 14, itemStyle: { color: '#e03131' }, label: { show: true, position: 'bottom', color: '#e03131', fontSize: 11, fontWeight: 'bold' } });
            }
            // 死叉: MA5从上向下跌破MA20
            if (prev5 >= prev20 && curr5 < curr20) {
                deathCross.push({ name: '死叉 ▼', coord: [dates[i], 'max'], value: '卖出信号', symbol: 'triangle', symbolSize: 14, symbolRotate: 180, itemStyle: { color: '#2f9e44' }, label: { show: true, position: 'top', color: '#2f9e44', fontSize: 11, fontWeight: 'bold' } });
            }
        }

        const option = {
            animation: false,
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
                        signal = m5 > m20 ? '<br/><span style="color:#e03131;font-weight:600">▲ MA5 > MA20 (多头)</span>' : '<br/><span style="color:#2f9e44;font-weight:600">▼ MA5 < MA20 (空头)</span>';
                    }
                    return `<b>${d.date}</b><br/>
                        开盘: ${d.open.toFixed(2)}<br/>
                        收盘: ${d.close.toFixed(2)}<br/>
                        最高: <span style="color:#e03131">${d.high.toFixed(2)}</span><br/>
                        最低: <span style="color:#2f9e44">${d.low.toFixed(2)}</span><br/>
                        MA5: ${m5 ? m5.toFixed(2) : '-'} | MA20: ${m20 ? m20.toFixed(2) : '-'}${signal}<br/>
                        成交量: ${Charts.formatVol(d.volume)}`;
                }
            },
            axisPointer: { link: [{ xAxisIndex: 'all' }] },
            grid: [
                { left: '8%', right: '3%', top: '5%', height: '55%' },
                { left: '8%', right: '3%', top: '68%', height: '20%' }
            ],
            xAxis: [
                { type: 'category', data: dates, gridIndex: 0, axisLine: { onZero: false }, axisLabel: { show: false }, axisTick: { show: false } },
                { type: 'category', data: dates, gridIndex: 1, axisLabel: { formatter: v => v.slice(5), fontSize: 10 }, axisTick: { show: false } }
            ],
            yAxis: [
                { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: '#f0f0f0' } }, axisLabel: { fontSize: 10 } },
                { scale: true, gridIndex: 1, splitLine: { show: false }, axisLabel: { fontSize: 10, formatter: v => v >= 1e8 ? (v/1e8).toFixed(1)+'亿' : (v/1e4).toFixed(0)+'万' } }
            ],
            dataZoom: [{ type: 'inside', xAxisIndex: [0,1], start: 70, end: 100 }],
            series: [
                { name: 'K线', type: 'candlestick', data: ohlc, xAxisIndex: 0, yAxisIndex: 0, itemStyle: { color: '#e03131', color0: '#2f9e44', borderColor: '#e03131', borderColor0: '#2f9e44' } },
                { name: 'MA5', type: 'line', data: ma5, symbol: 'none', smooth: true, lineStyle: { width: 1, color: '#e03131' },
                  markPoint: { data: goldenCross.slice(-5), symbol: 'pin', symbolSize: 30, label: { fontSize: 10 } } },
                { name: 'MA10', type: 'line', data: indicators.ma10 || [], symbol: 'none', smooth: true, lineStyle: { width: 1, color: '#f59e0b' } },
                { name: 'MA20', type: 'line', data: ma20, symbol: 'none', smooth: true, lineStyle: { width: 1, color: '#6366f1' },
                  markPoint: { data: deathCross.slice(-5), symbol: 'pin', symbolSize: 30, symbolRotate: 180, label: { fontSize: 10 } } },
                { name: 'MA60', type: 'line', data: indicators.ma60 || [], symbol: 'none', smooth: true, lineStyle: { width: 1, color: '#10b981' } },
                { name: '成交量', type: 'bar', data: volumes, xAxisIndex: 1, yAxisIndex: 1, itemStyle: { color: p => kline[p.dataIndex].close >= kline[p.dataIndex].open ? '#e03131' : '#2f9e44' } }
            ]
        };

        chart.setOption(option);
        window.addEventListener('resize', () => chart.resize());
        
        // 返回信号信息给详情面板
        return { goldenCross, deathCross, ma5, ma20, ma10: indicators.ma10 || [], ma60: indicators.ma60 || [] };
    },

    renderIndicator(containerId, title, dates, lines, markLines = []) {
        const container = document.getElementById(containerId);
        if (!container) return;

        this.dispose(containerId);
        const chart = echarts.init(container);
        this.instances[containerId] = chart;

        const series = lines.map(l => ({
            name: l.name, type: 'line', data: l.data,
            lineStyle: { width: 1.5, color: l.color },
            symbol: 'none', smooth: true,
        }));

        if (markLines.length > 0 && series.length > 0) {
            series[0].markLine = {
                symbol: 'none', silent: true,
                data: markLines.map(ml => ({ yAxis: ml.value, label: ml.label || {}, lineStyle: ml.lineStyle || { color: '#ccc', type: 'dashed' } })),
            };
        }

        chart.setOption({
            title: { text: title, left: 10, top: 5, textStyle: { fontSize: 12, fontWeight: 'normal', color: '#606770' } },
            tooltip: { trigger: 'axis' },
            legend: { data: lines.map(l => l.name), right: 10, top: 2, textStyle: { fontSize: 10 } },
            grid: { left: '8%', right: '3%', top: '25%', bottom: '10%' },
            xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 9, formatter: v => v.slice(5) }, axisTick: { show: false } },
            yAxis: { scale: true, splitLine: { lineStyle: { color: '#f0f0f0' } }, axisLabel: { fontSize: 10 } },
            series,
        });
        window.addEventListener('resize', () => chart.resize());
    },

    renderMacd(containerId, dates, indicators) {
        const container = document.getElementById(containerId);
        if (!container) return;

        this.dispose(containerId);
        const chart = echarts.init(container);
        this.instances[containerId] = chart;

        const bar = indicators.bar || [];
        const barColors = bar.map(v => v >= 0 ? '#e03131' : '#2f9e44');

        chart.setOption({
            title: { text: 'MACD', left: 10, top: 5, textStyle: { fontSize: 12, fontWeight: 'normal', color: '#606770' } },
            tooltip: { trigger: 'axis' },
            legend: { data: ['DIF', 'DEA', 'BAR'], right: 10, top: 2, textStyle: { fontSize: 10 } },
            grid: { left: '8%', right: '3%', top: '25%', bottom: '10%' },
            xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 9, formatter: v => v.slice(5) }, axisTick: { show: false } },
            yAxis: { scale: true, splitLine: { lineStyle: { color: '#f0f0f0' } }, axisLabel: { fontSize: 10 } },
            series: [
                { name: 'DIF', type: 'line', data: indicators.dif || [], lineStyle: { width: 1.5, color: '#e03131' }, symbol: 'none', smooth: true },
                { name: 'DEA', type: 'line', data: indicators.dea || [], lineStyle: { width: 1.5, color: '#6366f1' }, symbol: 'none', smooth: true },
                { name: 'BAR', type: 'bar', data: bar, itemStyle: { color: p => barColors[p.dataIndex] } },
            ],
        });
        window.addEventListener('resize', () => chart.resize());
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
    }
};
