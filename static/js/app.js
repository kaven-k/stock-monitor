/**
 * StockMonitor - 股票监控系统前端
 * 实时行情、K线图、技术指标、预警管理
 */

// ============ 全局状态 ============
let appState = {
    stocks: [],           // 所有股票列表
    quotes: {},           // 实时行情数据 {code: quote}
    groups: [],           // 分组数据
    alertRules: [],       // 预警规则
    alertLogs: [],        // 预警日志
    currentView: 'all',   // 当前视图: 'all' | groupId | 'alerts'
    currentGroupId: null,
    selectedStock: null,  // 当前选中的股票
    monitorRunning: true,
    refreshInterval: 5,
    countdown: 5,
    sortField: 'change_pct',
    sortOrder: 'desc',
    searchKeyword: '',
    detailTab: 'chart',   // 'chart' | 'indicator' | 'info'
    klineData: null,      // 缓存K线数据
    chartInstance: null,  // ECharts实例
    indicatorChartInstance: null,
    macdChartInstance: null,
    kdjChartInstance: null,
};

// SocketIO
let socket = null;

// ============ 初始化 ============
document.addEventListener('DOMContentLoaded', async () => {
    await loadInitialData();
    initSocket();
    startCountdown();
    initAlertParams(); // 初始化预警参数表单
});

async function loadInitialData() {
    try {
        // 并行加载
        const [stocksRes, groupsRes, rulesRes] = await Promise.all([
            fetch('/api/stocks').then(r => r.json()),
            fetch('/api/groups').then(r => r.json()),
            fetch('/api/alerts/rules').then(r => r.json()),
        ]);

        appState.stocks = stocksRes.data || [];
        appState.groups = groupsRes.data || [];
        appState.alertRules = rulesRes.data || [];

        renderGroups();
        updateNavCounts();

        // 加载初始行情
        if (appState.stocks.length > 0) {
            const codes = appState.stocks.map(s => s.code).join(',');
            const quotesRes = await fetch(`/api/quotes?codes=${codes}`).then(r => r.json());
            if (quotesRes.code === 0) {
                appState.quotes = quotesRes.data || {};
                updateLastUpdateTime(quotesRes.time);
            }
        }

        renderStockTable();
    } catch (e) {
        console.error('初始化加载失败:', e);
    }
}

// ============ SocketIO ============
function initSocket() {
    socket = io();

    socket.on('connect', () => {
        console.log('[SocketIO] 已连接');
    });

    socket.on('connected', (data) => {
        appState.monitorRunning = data.monitor_running;
        updateMonitorStatus();
    });

    socket.on('quotes_update', (data) => {
        if (data.data) {
            appState.quotes = { ...appState.quotes, ...data.data };
            updateLastUpdateTime(data.time);
            renderStockTable();

            // 如果详情面板打开，更新详情
            if (appState.selectedStock && !document.getElementById('detail-panel').classList.contains('collapsed')) {
                updateDetailPanel();
            }
        }
    });

    socket.on('alerts_new', (data) => {
        if (data.alerts && data.alerts.length > 0) {
            showAlertPopup(data.alerts);
            updateNavCounts();
        }
    });

    socket.on('disconnect', () => {
        console.log('[SocketIO] 连接断开');
    });
}

// ============ 倒计时 ============
function startCountdown() {
    setInterval(() => {
        appState.countdown--;
        if (appState.countdown <= 0) {
            appState.countdown = appState.refreshInterval;
        }
        const el = document.getElementById('refresh-countdown');
        if (el) el.textContent = appState.countdown;
    }, 1000);
}

// ============ 监控状态 ============
function updateMonitorStatus() {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const btn = document.getElementById('btn-monitor-toggle');

    if (appState.monitorRunning) {
        dot.className = 'status-dot running';
        text.textContent = '监控运行中';
        if (btn) btn.innerHTML = '⏸ 暂停监控';
    } else {
        dot.className = 'status-dot stopped';
        text.textContent = '监控已暂停';
        if (btn) btn.innerHTML = '▶ 启动监控';
    }
}

async function toggleMonitor() {
    const url = appState.monitorRunning ? '/api/monitor/stop' : '/api/monitor/start';
    try {
        const res = await fetch(url, { method: 'POST' }).then(r => r.json());
        appState.monitorRunning = !appState.monitorRunning;
        updateMonitorStatus();
        showToast(res.msg, 'info');
    } catch (e) {
        showToast('操作失败', 'error');
    }
}

function updateLastUpdateTime(time) {
    const el = document.getElementById('last-update-time');
    if (el) el.textContent = `数据更新: ${time}`;
    const statusTime = document.getElementById('status-time');
    if (statusTime) statusTime.textContent = time;
}

// ============ 股票表格渲染 ============
function renderStockTable() {
    const tbody = document.getElementById('stock-table-body');
    if (!tbody) return;

    let displayStocks = getFilteredStocks();

    if (displayStocks.length === 0) {
        tbody.innerHTML = `<tr><td colspan="12">
            <div class="empty-state">
                <div class="icon">📊</div>
                <div class="text">${appState.stocks.length === 0 ? '暂无股票数据<br><small>点击"添加股票"开始监控</small>' : '无匹配结果'}</div>
            </div>
        </td></tr>`;
        return;
    }

    // 排序
    displayStocks.sort((a, b) => {
        const qa = appState.quotes[a.code] || {};
        const qb = appState.quotes[b.code] || {};
        let va = qa[appState.sortField] || 0;
        let vb = qb[appState.sortField] || 0;
        if (typeof va === 'string') va = parseFloat(va) || 0;
        if (typeof vb === 'string') vb = parseFloat(vb) || 0;
        return appState.sortOrder === 'desc' ? vb - va : va - vb;
    });

    // 搜索过滤
    if (appState.searchKeyword) {
        const kw = appState.searchKeyword.toLowerCase();
        displayStocks = displayStocks.filter(s => {
            const q = appState.quotes[s.code];
            return s.code.includes(kw) ||
                s.name.toLowerCase().includes(kw) ||
                (q && q.name && q.name.toLowerCase().includes(kw));
        });
    }

    let html = '';
    displayStocks.forEach(s => {
        const q = appState.quotes[s.code];
        if (!q) {
            html += `<tr data-code="${s.code}">
                <td><span class="stock-name">${s.name}</span> <span class="stock-code">${s.code}</span></td>
                <td colspan="10" style="text-align:center;color:var(--text-muted)">加载中...</td>
                <td><button class="btn btn-xs btn-danger" onclick="event.stopPropagation();removeStock('${s.code}')">删除</button></td>
            </tr>`;
            return;
        }

        const changeClass = q.change_pct > 0 ? 'change-up' : (q.change_pct < 0 ? 'change-down' : 'change-flat');
        const changeSign = q.change_pct > 0 ? '+' : '';
        const selectedClass = appState.selectedStock === s.code ? 'selected' : '';
        const priceColor = q.change_pct > 0 ? 'color:var(--color-up)' : (q.change_pct < 0 ? 'color:var(--color-down)' : '');

        html += `<tr data-code="${s.code}" class="${selectedClass}" onclick="selectStock('${s.code}')">
            <td>
                <span class="stock-name">${s.name}</span>
                <span class="stock-code">${s.code}</span>
            </td>
            <td style="${priceColor};font-weight:600">${q.price.toFixed(2)}</td>
            <td class="${changeClass}" style="font-weight:600">${changeSign}${q.change_pct.toFixed(2)}%</td>
            <td>${q.open.toFixed(2)}</td>
            <td style="color:var(--color-up)">${q.high.toFixed(2)}</td>
            <td style="color:var(--color-down)">${q.low.toFixed(2)}</td>
            <td>${formatVolume(q.volume)}</td>
            <td>${formatAmount(q.amount_wan)}</td>
            <td>${q.turnover_pct.toFixed(2)}%</td>
            <td>${q.pe_ttm > 0 ? q.pe_ttm.toFixed(1) : '--'}</td>
            <td>${q.pb > 0 ? q.pb.toFixed(2) : '--'}</td>
            <td>
                <button class="btn btn-xs" onclick="event.stopPropagation();addToGroupPrompt('${s.code}')" title="加入分组">📁</button>
                <button class="btn btn-xs btn-danger" onclick="event.stopPropagation();removeStock('${s.code}')" title="删除">✕</button>
            </td>
        </tr>`;
    });

    tbody.innerHTML = html;
}

function getFilteredStocks() {
    if (appState.currentView === 'all') {
        return [...appState.stocks];
    } else if (appState.currentView === 'alerts') {
        // 预警视图：显示有预警规则的股票
        const alertCodes = new Set();
        appState.alertRules.forEach(rule => {
            (rule.stocks || []).forEach(s => alertCodes.add(s.stock_code));
        });
        return appState.stocks.filter(s => alertCodes.has(s.code));
    } else {
        // 分组视图
        const group = appState.groups.find(g => g.id == appState.currentGroupId);
        if (!group) return [];
        const memberCodes = new Set((group.members || []).map(m => m.stock_code));
        return appState.stocks.filter(s => memberCodes.has(s.code));
    }
}

function filterStocks() {
    appState.searchKeyword = document.getElementById('table-search')?.value || '';
    renderStockTable();
}

function sortBy(field) {
    if (appState.sortField === field) {
        appState.sortOrder = appState.sortOrder === 'desc' ? 'asc' : 'desc';
    } else {
        appState.sortField = field;
        appState.sortOrder = 'desc';
    }
    renderStockTable();
}

function formatVolume(vol) {
    if (vol >= 100000000) return (vol / 100000000).toFixed(1) + '亿';
    if (vol >= 10000) return (vol / 10000).toFixed(1) + '万';
    return vol.toFixed(0);
}

function formatAmount(amt) {
    if (amt >= 10000) return (amt / 10000).toFixed(2) + '亿';
    return amt.toFixed(2) + '万';
}

// ============ 股票选择与详情 ============
function selectStock(code) {
    appState.selectedStock = code;
    appState.klineData = null;
    document.getElementById('detail-panel').classList.remove('collapsed');
    updateDetailPanel();
    loadKlineData(code);
    renderStockTable();
}

function closeDetail() {
    appState.selectedStock = null;
    appState.klineData = null;
    document.getElementById('detail-panel').classList.add('collapsed');
    if (appState.chartInstance) appState.chartInstance.dispose();
    if (appState.indicatorChartInstance) appState.indicatorChartInstance.dispose();
    if (appState.macdChartInstance) appState.macdChartInstance.dispose();
    if (appState.kdjChartInstance) appState.kdjChartInstance.dispose();
    appState.chartInstance = null;
    appState.indicatorChartInstance = null;
    renderStockTable();
}

function updateDetailPanel() {
    const code = appState.selectedStock;
    if (!code) return;

    const q = appState.quotes[code];
    const stock = appState.stocks.find(s => s.code === code);
    const name = q?.name || stock?.name || code;

    document.getElementById('detail-name').textContent = name;
    document.getElementById('detail-code').textContent = code;

    if (!q) return;

    const changeClass = q.change_pct > 0 ? 'change-up' : (q.change_pct < 0 ? 'change-down' : 'change-flat');
    const changeSign = q.change_pct > 0 ? '+' : '';
    const priceColor = q.change_pct > 0 ? 'var(--color-up)' : (q.change_pct < 0 ? 'var(--color-down)' : 'var(--text-primary)');

    const tab = appState.detailTab || 'chart';
    const container = document.getElementById('detail-content');
    if (!container) return;

    let html = `
        <div class="price-card">
            <div class="price-main">
                <span class="price-value" style="color:${priceColor}">${q.price.toFixed(2)}</span>
                <span class="price-change ${changeClass}">${changeSign}${q.change_pct.toFixed(2)}%</span>
            </div>
            <div class="price-stats">
                <div class="stat-item"><div class="stat-label">今开</div><div class="stat-value">${q.open.toFixed(2)}</div></div>
                <div class="stat-item"><div class="stat-label">最高</div><div class="stat-value" style="color:var(--color-up)">${q.high.toFixed(2)}</div></div>
                <div class="stat-item"><div class="stat-label">最低</div><div class="stat-value" style="color:var(--color-down)">${q.low.toFixed(2)}</div></div>
                <div class="stat-item"><div class="stat-label">成交量</div><div class="stat-value">${formatVolume(q.volume)}</div></div>
                <div class="stat-item"><div class="stat-label">成交额</div><div class="stat-value">${formatAmount(q.amount_wan)}</div></div>
                <div class="stat-item"><div class="stat-label">换手率</div><div class="stat-value">${q.turnover_pct.toFixed(2)}%</div></div>
                <div class="stat-item"><div class="stat-label">PE(TTM)</div><div class="stat-value">${q.pe_ttm > 0 ? q.pe_ttm.toFixed(1) : '--'}</div></div>
                <div class="stat-item"><div class="stat-label">PB</div><div class="stat-value">${q.pb > 0 ? q.pb.toFixed(2) : '--'}</div></div>
                <div class="stat-item"><div class="stat-label">总市值</div><div class="stat-value">${q.mcap_yi > 0 ? (q.mcap_yi >= 10000 ? (q.mcap_yi/10000).toFixed(2)+'万亿' : q.mcap_yi.toFixed(0)+'亿') : '--'}</div></div>
            </div>
        </div>
    `;

    if (tab === 'chart') {
        html += `
            <div class="chart-container" id="kline-chart"></div>
            <div class="indicator-legend">
                <div class="legend-item"><div class="legend-line" style="background:#e03131"></div>MA5</div>
                <div class="legend-item"><div class="legend-line" style="background:#f59e0b"></div>MA10</div>
                <div class="legend-item"><div class="legend-line" style="background:#6366f1"></div>MA20</div>
                <div class="legend-item"><div class="legend-line" style="background:#10b981"></div>MA60</div>
            </div>
        `;
    } else if (tab === 'indicator') {
        html += `
            <div class="chart-container" id="rsi-chart"></div>
            <div class="chart-container" id="macd-chart"></div>
            <div class="chart-container" id="kdj-chart"></div>
            <div class="chart-container" id="boll-chart"></div>
        `;
    } else if (tab === 'info') {
        html += `
            <div style="padding:8px 0">
                <div class="form-group"><label>涨停价</label><span style="font-weight:600;color:var(--color-up)">${q.limit_up.toFixed(2)}</span></div>
                <div class="form-group"><label>跌停价</label><span style="font-weight:600;color:var(--color-down)">${q.limit_down.toFixed(2)}</span></div>
                <div class="form-group"><label>量比</label><span>${q.vol_ratio.toFixed(2)}</span></div>
                <div class="form-group"><label>振幅</label><span>${q.amplitude_pct.toFixed(2)}%</span></div>
                <div class="form-group"><label>PE(静态)</label><span>${q.pe_static > 0 ? q.pe_static.toFixed(1) : '--'}</span></div>
                <div class="form-group"><label>流通市值</label><span>${q.float_mcap_yi > 0 ? (q.float_mcap_yi >= 10000 ? (q.float_mcap_yi/10000).toFixed(2)+'万亿' : q.float_mcap_yi.toFixed(0)+'亿') : '--'}</span></div>
            </div>
        `;
    }

    container.innerHTML = html;

    // 渲染图表
    if (appState.klineData) {
        if (tab === 'chart') {
            setTimeout(() => renderKlineChart(), 100);
        } else if (tab === 'indicator') {
            setTimeout(() => renderIndicatorCharts(), 100);
        }
    }
}

function switchDetailTab(tab, el) {
    appState.detailTab = tab;
    document.querySelectorAll('.detail-tab').forEach(t => t.classList.remove('active'));
    if (el) el.classList.add('active');
    updateDetailPanel();
}

// ============ K线数据加载 ============
async function loadKlineData(code) {
    try {
        const res = await fetch(`/api/kline/${code}?count=250`).then(r => r.json());
        if (res.code === 0 && res.data) {
            appState.klineData = res.data;
            const tab = appState.detailTab || 'chart';
            if (tab === 'chart') {
                renderKlineChart();
            } else if (tab === 'indicator') {
                renderIndicatorCharts();
            }
        }
    } catch (e) {
        console.error('加载K线数据失败:', e);
    }
}

// ============ ECharts 图表 ============
function renderKlineChart() {
    const data = appState.klineData;
    if (!data || !data.kline || data.kline.length === 0) return;

    const container = document.getElementById('kline-chart');
    if (!container) return;

    if (appState.chartInstance) appState.chartInstance.dispose();
    const chart = echarts.init(container);
    appState.chartInstance = chart;

    const kline = data.kline;
    const dates = kline.map(d => d.date);
    const ohlc = kline.map(d => [d.open, d.close, d.low, d.high]);
    const volumes = kline.map(d => d.volume);

    const indicators = data.indicators || {};

    const option = {
        animation: false,
        tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'cross' },
            formatter: function(params) {
                const i = params[0]?.dataIndex;
                if (i === undefined) return '';
                const d = kline[i];
                return `<b>${d.date}</b><br/>
                    开盘: ${d.open.toFixed(2)}<br/>
                    收盘: ${d.close.toFixed(2)}<br/>
                    最高: <span style="color:#e03131">${d.high.toFixed(2)}</span><br/>
                    最低: <span style="color:#2f9e44">${d.low.toFixed(2)}</span><br/>
                    成交量: ${formatVolume(d.volume)}`;
            }
        },
        axisPointer: {
            link: [{ xAxisIndex: 'all' }]
        },
        grid: [
            { left: '8%', right: '3%', top: '5%', height: '55%' },
            { left: '8%', right: '3%', top: '68%', height: '20%' }
        ],
        xAxis: [
            {
                type: 'category', data: dates, gridIndex: 0,
                axisLine: { onZero: false },
                axisLabel: { show: false },
                splitLine: { show: false },
                axisTick: { show: false },
            },
            {
                type: 'category', data: dates, gridIndex: 1,
                axisLabel: {
                    formatter: (v) => v.slice(5),
                    fontSize: 10,
                },
                splitLine: { show: false },
                axisTick: { show: false },
            }
        ],
        yAxis: [
            {
                scale: true, gridIndex: 0,
                splitLine: { lineStyle: { color: '#f0f0f0' } },
                axisLabel: { fontSize: 10 },
                splitNumber: 5,
            },
            {
                scale: true, gridIndex: 1,
                splitLine: { show: false },
                axisLabel: { fontSize: 10, formatter: v => v >= 1e8 ? (v/1e8).toFixed(1)+'亿' : (v/1e4).toFixed(0)+'万' },
            }
        ],
        dataZoom: [
            { type: 'inside', xAxisIndex: [0,1], start: 70, end: 100 },
        ],
        series: [
            {
                name: 'K线', type: 'candlestick', data: ohlc,
                xAxisIndex: 0, yAxisIndex: 0,
                itemStyle: {
                    color: '#e03131', color0: '#2f9e44',
                    borderColor: '#e03131', borderColor0: '#2f9e44',
                },
                markPoint: { data: [] },
            },
            {
                name: 'MA5', type: 'line', data: indicators.ma5 || [],
                xAxisIndex: 0, yAxisIndex: 0,
                lineStyle: { width: 1, color: '#e03131' },
                symbol: 'none', smooth: true,
            },
            {
                name: 'MA10', type: 'line', data: indicators.ma10 || [],
                xAxisIndex: 0, yAxisIndex: 0,
                lineStyle: { width: 1, color: '#f59e0b' },
                symbol: 'none', smooth: true,
            },
            {
                name: 'MA20', type: 'line', data: indicators.ma20 || [],
                xAxisIndex: 0, yAxisIndex: 0,
                lineStyle: { width: 1, color: '#6366f1' },
                symbol: 'none', smooth: true,
            },
            {
                name: 'MA60', type: 'line', data: indicators.ma60 || [],
                xAxisIndex: 0, yAxisIndex: 0,
                lineStyle: { width: 1, color: '#10b981' },
                symbol: 'none', smooth: true,
            },
            {
                name: '成交量', type: 'bar', data: volumes,
                xAxisIndex: 1, yAxisIndex: 1,
                itemStyle: {
                    color: function(p) {
                        const i = p.dataIndex;
                        return kline[i].close >= kline[i].open ? '#e03131' : '#2f9e44';
                    }
                },
            }
        ]
    };

    chart.setOption(option);
    window.addEventListener('resize', () => chart.resize());
}

function renderIndicatorCharts() {
    const data = appState.klineData;
    if (!data || !data.kline || data.kline.length === 0) return;

    const kline = data.kline;
    const indicators = data.indicators || {};
    const dates = kline.map(d => d.date);

    // RSI Chart
    renderSingleIndicator('rsi-chart', dates, [
        { name: 'RSI6', data: indicators.rsi6 || [], color: '#6366f1', lineWidth: 1 },
        { name: 'RSI14', data: indicators.rsi14 || [], color: '#f59e0b', lineWidth: 1 },
    ], {
        title: 'RSI 相对强弱指标',
        markLines: [
            { yAxis: 80, label: { formatter: '超买80' }, lineStyle: { color: '#e03131', type: 'dashed' } },
            { yAxis: 20, label: { formatter: '超卖20' }, lineStyle: { color: '#2f9e44', type: 'dashed' } },
            { yAxis: 50, label: { formatter: '50' }, lineStyle: { color: '#ccc', type: 'dashed' } },
        ],
    });

    // MACD Chart
    renderMacdChart('macd-chart', dates, indicators);

    // KDJ Chart
    renderSingleIndicator('kdj-chart', dates, [
        { name: 'K', data: indicators.k || [], color: '#6366f1', lineWidth: 1.5 },
        { name: 'D', data: indicators.d || [], color: '#f59e0b', lineWidth: 1.5 },
        { name: 'J', data: indicators.j || [], color: '#e03131', lineWidth: 1 },
    ], {
        title: 'KDJ 随机指标',
        markLines: [
            { yAxis: 80, lineStyle: { color: '#e03131', type: 'dashed' } },
            { yAxis: 20, lineStyle: { color: '#2f9e44', type: 'dashed' } },
        ],
    });

    // BOLL Chart
    renderSingleIndicator('boll-chart', dates, [
        { name: '上轨', data: indicators.upper || [], color: '#f59e0b', lineWidth: 1, areaStyle: false },
        { name: '中轨', data: indicators.mid || [], color: '#6366f1', lineWidth: 1.5 },
        { name: '下轨', data: indicators.lower || [], color: '#f59e0b', lineWidth: 1, areaStyle: false },
    ], {
        title: 'BOLL 布林带',
        fillBetween: { upper: indicators.upper || [], lower: indicators.lower || [] },
    });
}

function renderSingleIndicator(containerId, dates, lines, opts = {}) {
    const container = document.getElementById(containerId);
    if (!container) return;

    // Dispose old instance if exists on container
    const oldInstance = echarts.getInstanceByDom(container);
    if (oldInstance) oldInstance.dispose();

    const chart = echarts.init(container);

    const series = lines.map(l => ({
        name: l.name,
        type: 'line',
        data: l.data,
        lineStyle: { width: l.lineWidth || 1.5, color: l.color },
        itemStyle: { color: l.color },
        symbol: 'none',
        smooth: true,
    }));

    const markLines = (opts.markLines || []).map(ml => ({
        yAxis: ml.yAxis,
        label: ml.label || {},
        lineStyle: ml.lineStyle || { color: '#ccc', type: 'dashed' },
        silent: true,
    }));

    // Add markLine to first series
    if (markLines.length > 0 && series.length > 0) {
        series[0].markLine = { data: markLines, symbol: 'none', silent: true };
    }

    // Fill between for BOLL
    if (opts.fillBetween) {
        series.push({
            name: 'band',
            type: 'line',
            data: opts.fillBetween.upper,
            lineStyle: { opacity: 0 },
            symbol: 'none',
            stack: 'confidence-band',
            areaStyle: { color: 'rgba(245,158,11,0.08)' },
        });
        series.push({
            name: 'band',
            type: 'line',
            data: opts.fillBetween.lower,
            lineStyle: { opacity: 0 },
            symbol: 'none',
            stack: 'confidence-band',
            areaStyle: { color: 'rgba(255,255,255,0.8)' },
        });
    }

    const option = {
        title: {
            text: opts.title || '',
            left: 10, top: 5,
            textStyle: { fontSize: 12, fontWeight: 'normal', color: '#606770' },
        },
        tooltip: { trigger: 'axis' },
        legend: {
            data: lines.map(l => l.name),
            right: 10, top: 2,
            textStyle: { fontSize: 10 },
        },
        grid: { left: '8%', right: '3%', top: '25%', bottom: '10%' },
        xAxis: {
            type: 'category', data: dates,
            axisLabel: { fontSize: 9, formatter: v => v.slice(5) },
            axisTick: { show: false },
            splitLine: { show: false },
        },
        yAxis: {
            scale: true,
            splitLine: { lineStyle: { color: '#f0f0f0' } },
            axisLabel: { fontSize: 10 },
        },
        series: series,
    };

    chart.setOption(option);
    window.addEventListener('resize', () => chart.resize());
}

function renderMacdChart(containerId, dates, indicators) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const oldInstance = echarts.getInstanceByDom(container);
    if (oldInstance) oldInstance.dispose();

    const chart = echarts.init(container);

    const dif = indicators.dif || [];
    const dea = indicators.dea || [];
    const bar = indicators.bar || [];

    const barColors = bar.map(v => v >= 0 ? '#e03131' : '#2f9e44');

    const option = {
        title: {
            text: 'MACD 异同移动平均线',
            left: 10, top: 5,
            textStyle: { fontSize: 12, fontWeight: 'normal', color: '#606770' },
        },
        tooltip: { trigger: 'axis' },
        legend: {
            data: ['DIF', 'DEA', 'BAR'],
            right: 10, top: 2,
            textStyle: { fontSize: 10 },
        },
        grid: { left: '8%', right: '3%', top: '25%', bottom: '10%' },
        xAxis: {
            type: 'category', data: dates,
            axisLabel: { fontSize: 9, formatter: v => v.slice(5) },
            axisTick: { show: false },
            splitLine: { show: false },
        },
        yAxis: {
            scale: true,
            splitLine: { lineStyle: { color: '#f0f0f0' } },
            axisLabel: { fontSize: 10 },
        },
        series: [
            {
                name: 'DIF', type: 'line', data: dif,
                lineStyle: { width: 1.5, color: '#e03131' },
                symbol: 'none', smooth: true,
            },
            {
                name: 'DEA', type: 'line', data: dea,
                lineStyle: { width: 1.5, color: '#6366f1' },
                symbol: 'none', smooth: true,
            },
            {
                name: 'BAR', type: 'bar', data: bar,
                itemStyle: {
                    color: function(p) { return barColors[p.dataIndex]; }
                },
            },
        ],
    };

    chart.setOption(option);
    window.addEventListener('resize', () => chart.resize());
}

// ============ 侧边栏分组 ============
function renderGroups() {
    const container = document.getElementById('group-list');
    if (!container) return;

    let html = '';
    appState.groups.forEach(g => {
        const memberCount = (g.members || []).length;
        html += `
            <div class="sidebar-item" data-group-id="${g.id}" onclick="switchGroupView(${g.id})">
                <span class="dot" style="background:${g.color}"></span>
                <span>${g.name}</span>
                <span class="count">${memberCount}</span>
            </div>
        `;
    });

    if (appState.groups.length === 0) {
        html = '<div style="padding:8px 12px;font-size:12px;color:var(--text-muted)">暂无分组</div>';
    }

    container.innerHTML = html;
}

function switchGroupView(groupId) {
    appState.currentView = groupId ? 'group' : 'all';
    appState.currentGroupId = groupId;
    document.getElementById('view-title').textContent = groupId
        ? (appState.groups.find(g => g.id == groupId)?.name || '分组')
        : '全部股票';

    document.querySelectorAll('.sidebar-item').forEach(el => el.classList.remove('active'));
    const activeItem = document.querySelector(`.sidebar-item[data-group-id="${groupId}"]`);
    if (activeItem) activeItem.classList.add('active');
    else document.querySelector('.sidebar-item[data-view="all"]')?.classList.add('active');

    renderStockTable();
}

// ============ 股票管理 ============
function showAddStockModal() {
    document.getElementById('modal-add-stock').style.display = 'flex';
    document.getElementById('stock-search-input').value = '';
    document.getElementById('search-results').style.display = 'none';
    document.getElementById('manual-code').value = '';
    document.getElementById('manual-name').value = '';
    setTimeout(() => document.getElementById('stock-search-input').focus(), 100);
}

let searchTimeout;
async function searchStocks() {
    const keyword = document.getElementById('stock-search-input').value.trim();
    const resultsDiv = document.getElementById('search-results');

    if (!keyword || keyword.length < 1) {
        resultsDiv.style.display = 'none';
        return;
    }

    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(async () => {
        try {
            const res = await fetch(`/api/stocks/search?keyword=${encodeURIComponent(keyword)}`).then(r => r.json());
            if (res.code === 0 && res.data) {
                let html = '';
                res.data.forEach(item => {
                    html += `<div class="search-result-item" onclick="addStockFromSearch('${item.code}','${item.name}','${item.market}')">
                        <span><span class="name">${item.name}</span> <span class="code">${item.code}</span></span>
                        <span class="market">${item.market}</span>
                    </div>`;
                });
                resultsDiv.innerHTML = html || '<div style="padding:12px;color:var(--text-muted);font-size:12px">无结果</div>';
                resultsDiv.style.display = 'block';
            }
        } catch (e) {
            console.error('搜索失败:', e);
        }
    }, 300);
}

async function addStockFromSearch(code, name, market) {
    await addStock(code, name, market);
    document.getElementById('modal-add-stock').style.display = 'none';
}

async function addStockManual() {
    const code = document.getElementById('manual-code').value.trim();
    const name = document.getElementById('manual-name').value.trim();
    if (!code || !name) {
        showToast('请输入股票代码和名称', 'warning');
        return;
    }
    await addStock(code, name, 'A');
    document.getElementById('modal-add-stock').style.display = 'none';
}

async function addStock(code, name, market) {
    try {
        const res = await fetch('/api/stocks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code, name, market }),
        }).then(r => r.json());

        if (res.code === 0) {
            showToast(`${name}(${code}) 添加成功`, 'success');
            // 刷新数据
            await loadInitialData();
            // 立即获取一下行情
            const qRes = await fetch(`/api/quotes?codes=${code}`).then(r => r.json());
            if (qRes.code === 0) {
                appState.quotes = { ...appState.quotes, ...qRes.data };
                renderStockTable();
            }
        } else {
            showToast(res.msg || '添加失败', 'error');
        }
    } catch (e) {
        showToast('添加失败', 'error');
    }
}

async function removeStock(code) {
    if (!confirm(`确定要删除股票 ${code} 吗？`)) return;

    try {
        const res = await fetch(`/api/stocks/${code}`, { method: 'DELETE' }).then(r => r.json());
        if (res.code === 0) {
            showToast('删除成功', 'success');
            appState.stocks = appState.stocks.filter(s => s.code !== code);
            delete appState.quotes[code];
            if (appState.selectedStock === code) closeDetail();
            renderGroups();
            updateNavCounts();
            renderStockTable();
        }
    } catch (e) {
        showToast('删除失败', 'error');
    }
}

async function addToGroupPrompt(code) {
    if (appState.groups.length === 0) {
        showToast('请先创建分组', 'warning');
        return;
    }

    const groupNames = appState.groups.map(g => `${g.name} [${g.id}]`).join('\n');
    const groupId = prompt(`选择分组ID:\n${groupNames}`);
    if (!groupId) return;

    try {
        const res = await fetch(`/api/groups/${groupId}/stocks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code }),
        }).then(r => r.json());

        if (res.code === 0) {
            showToast('已加入分组', 'success');
            const gRes = await fetch('/api/groups').then(r => r.json());
            appState.groups = gRes.data || [];
            renderGroups();
        }
    } catch (e) {
        showToast('操作失败', 'error');
    }
}

// ============ 分组管理 ============
function showAddGroupModal() {
    document.getElementById('modal-add-group').style.display = 'flex';
    document.getElementById('group-name').value = '';
    setTimeout(() => document.getElementById('group-name').focus(), 100);
}

async function createGroup() {
    const name = document.getElementById('group-name').value.trim();
    const color = document.getElementById('group-color').value;
    if (!name) {
        showToast('请输入分组名称', 'warning');
        return;
    }

    try {
        const res = await fetch('/api/groups', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, color }),
        }).then(r => r.json());

        if (res.code === 0) {
            showToast('分组创建成功', 'success');
            document.getElementById('modal-add-group').style.display = 'none';
            const gRes = await fetch('/api/groups').then(r => r.json());
            appState.groups = gRes.data || [];
            renderGroups();
        }
    } catch (e) {
        showToast('创建失败', 'error');
    }
}

// ============ 预警规则管理 ============
function showAlertRuleModal() {
    document.getElementById('modal-alert-rule').style.display = 'flex';
    document.getElementById('alert-rule-name').value = '';
    document.getElementById('alert-rule-type').value = 'price_up';
    onAlertTypeChange();

    // 加载股票选择
    const container = document.getElementById('alert-stock-select');
    if (appState.stocks.length === 0) {
        container.innerHTML = '<div style="color:var(--text-muted);font-size:12px">请先添加股票到监控列表</div>';
    } else {
        container.innerHTML = appState.stocks.map(s => `
            <label style="display:flex;align-items:center;gap:8px;padding:4px 0;cursor:pointer;font-size:13px">
                <input type="checkbox" value="${s.code}" class="alert-stock-checkbox">
                ${s.name} (${s.code})
            </label>
        `).join('');
    }
}

function onAlertTypeChange() {
    const type = document.getElementById('alert-rule-type').value;
    const paramsContainer = document.getElementById('alert-params');

    const paramConfigs = {
        price_up: '<input type="number" class="form-input" id="param-threshold" placeholder="价格阈值, 如 2000" step="0.01">',
        price_down: '<input type="number" class="form-input" id="param-threshold" placeholder="价格阈值, 如 1500" step="0.01">',
        change_up: '<input type="number" class="form-input" id="param-threshold" placeholder="涨幅阈值(%), 如 5" step="0.1" value="5">',
        change_down: '<input type="number" class="form-input" id="param-threshold" placeholder="跌幅阈值(%), 如 5" step="0.1" value="5">',
        volume_surge: '<div style="display:flex;gap:8px"><input type="number" class="form-input" id="param-multiplier" placeholder="放量倍数, 如 3" step="0.1" value="3"><input type="number" class="form-input" id="param-avg_days" placeholder="均量天数, 如 20" value="20"></div>',
        turnover_high: '<input type="number" class="form-input" id="param-threshold" placeholder="换手率阈值(%), 如 10" step="0.1" value="10">',
        amplitude_high: '<input type="number" class="form-input" id="param-threshold" placeholder="振幅阈值(%), 如 10" step="0.1" value="10">',
        price_break_ma: '<div style="display:flex;gap:8px"><select class="form-select" id="param-direction" style="width:120px"><option value="up">向上突破</option><option value="down">向下跌破</option></select><input type="number" class="form-input" id="param-ma_period" placeholder="均线周期, 如 20" value="20"></div>',
        continuous_up: '<input type="number" class="form-input" id="param-days" placeholder="连续上涨天数, 如 3" value="3">',
        continuous_down: '<input type="number" class="form-input" id="param-days" placeholder="连续下跌天数, 如 3" value="3">',
        volume_ratio: '<input type="number" class="form-input" id="param-threshold" placeholder="量比阈值, 如 2" step="0.1" value="2">',
        limit_up: '<div style="color:var(--text-muted);font-size:12px">当股价触及涨停价时触发</div>',
        limit_down: '<div style="color:var(--text-muted);font-size:12px">当股价触及跌停价时触发</div>',
    };

    paramsContainer.innerHTML = paramConfigs[type] || '';
}

function initAlertParams() {
    // Initialize on first load
}

async function createAlertRule() {
    const name = document.getElementById('alert-rule-name').value.trim();
    const ruleType = document.getElementById('alert-rule-type').value;
    const notifyFeishu = document.getElementById('alert-notify-feishu')?.checked ? 1 : 0;

    if (!name) {
        showToast('请输入规则名称', 'warning');
        return;
    }

    // 收集参数
    const params = {};
    const paramConfigs = {
        price_up: ['threshold'],
        price_down: ['threshold'],
        change_up: ['threshold'],
        change_down: ['threshold'],
        volume_surge: ['multiplier', 'avg_days'],
        turnover_high: ['threshold'],
        amplitude_high: ['threshold'],
        price_break_ma: ['direction', 'ma_period'],
        continuous_up: ['days'],
        continuous_down: ['days'],
        volume_ratio: ['threshold'],
        limit_up: [],
        limit_down: [],
    };

    const keys = paramConfigs[ruleType] || [];
    keys.forEach(key => {
        const el = document.getElementById(`param-${key}`);
        if (el) params[key] = el.value;
    });

    // 收集选中股票
    const checkboxes = document.querySelectorAll('.alert-stock-checkbox:checked');
    const stockCodes = Array.from(checkboxes).map(cb => cb.value);

    if (stockCodes.length === 0) {
        showToast('请选择至少一只监控股票', 'warning');
        return;
    }

    try {
        const res = await fetch('/api/alerts/rules', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name, rule_type: ruleType, params,
                stock_codes: stockCodes, notify_feishu: notifyFeishu,
            }),
        }).then(r => r.json());

        if (res.code === 0) {
            showToast('预警规则创建成功', 'success');
            document.getElementById('modal-alert-rule').style.display = 'none';
            const rulesRes = await fetch('/api/alerts/rules').then(r => r.json());
            appState.alertRules = rulesRes.data || [];
        } else {
            showToast(res.msg || '创建失败', 'error');
        }
    } catch (e) {
        showToast('创建失败', 'error');
    }
}

// ============ 预警日志 ============
async function showAlertLogs() {
    document.getElementById('modal-alert-logs').style.display = 'flex';
    await loadAlertLogs();
}

async function loadAlertLogs() {
    try {
        const res = await fetch('/api/alerts/logs?limit=100').then(r => r.json());
        if (res.code === 0) {
            appState.alertLogs = res.data || [];
            const container = document.getElementById('alert-logs-content');
            if (!container) return;

            if (appState.alertLogs.length === 0) {
                container.innerHTML = '<div class="empty-state"><div class="text">暂无预警记录</div></div>';
                return;
            }

            container.innerHTML = appState.alertLogs.map(log => `
                <div class="alert-item ${log.is_read ? '' : 'unread'}" onclick="markAlertRead(${log.id})">
                    <div class="alert-item-header">
                        <span class="alert-item-type ${log.alert_type}">${log.alert_type}</span>
                        <span class="alert-item-time">${log.triggered_at}</span>
                    </div>
                    <div class="alert-item-msg">${log.alert_msg}</div>
                    <div style="font-size:11px;color:var(--text-muted);margin-top:4px">规则: ${log.rule_name} | ${log.stock_name}(${log.stock_code})</div>
                </div>
            `).join('');

            updateNavCounts();
        }
    } catch (e) {
        console.error('加载预警日志失败:', e);
    }
}

async function markAlertRead(logId) {
    try {
        await fetch(`/api/alerts/logs/${logId}/read`, { method: 'POST' });
    } catch (e) {}
}

async function updateNavCounts() {
    try {
        // 更新全部股票数量
        const allCount = document.getElementById('nav-all-count');
        if (allCount) allCount.textContent = appState.stocks.length;

        // 更新分组数量
        renderGroups();

        // 获取未读预警数
        const logsRes = await fetch('/api/alerts/logs?limit=1').then(r => r.json());
        const unread = logsRes.unread || 0;
        const alertCount = document.getElementById('nav-alert-count');
        if (alertCount) {
            alertCount.textContent = unread;
            alertCount.style.background = unread > 0 ? '#fef2f2' : 'var(--bg-hover)';
            alertCount.style.color = unread > 0 ? '#ef4444' : 'var(--text-muted)';
        }
    } catch (e) {}
}

// ============ 预警弹窗 ============
function showAlertPopup(alerts) {
    const container = document.getElementById('alert-popup-container');
    alerts.forEach(alert => {
        const popup = document.createElement('div');
        popup.className = 'alert-popup';
        popup.innerHTML = `
            <div class="alert-popup-header">
                ⚠️ ${alert.type}
                <span class="alert-popup-close" onclick="this.parentElement.parentElement.remove()">✕</span>
            </div>
            <div style="font-size:12px">${alert.msg}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:4px">${alert.time}</div>
        `;
        container.appendChild(popup);
        // 5秒后自动消失
        setTimeout(() => { if (popup.parentElement) popup.remove(); }, 8000);
    });

    updateNavCounts();
}

// ============ Toast 通知 ============
function showToast(msg, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = msg;
    container.appendChild(toast);
    setTimeout(() => { if (toast.parentElement) toast.remove(); }, 3000);
}

// ============ 模态框 ============
function closeModal(id) {
    document.getElementById(id).style.display = 'none';
}

// 点击模态框遮罩关闭
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('modal-overlay')) {
        e.target.style.display = 'none';
    }
});

// 侧边栏导航点击
document.addEventListener('click', function(e) {
    const item = e.target.closest('.sidebar-item[data-view]');
    if (!item) return;

    const view = item.getAttribute('data-view');
    document.querySelectorAll('.sidebar-item').forEach(el => el.classList.remove('active'));
    item.classList.add('active');

    if (view === 'all') {
        appState.currentView = 'all';
        appState.currentGroupId = null;
        document.getElementById('view-title').textContent = '全部股票';
        renderStockTable();
    } else if (view === 'alerts') {
        showAlertLogs();
    }
});

// ============ 键盘快捷键 ============
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        closeDetail();
        document.querySelectorAll('.modal-overlay').forEach(m => m.style.display = 'none');
    }
    // Ctrl+F 聚焦搜索
    if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
        e.preventDefault();
        const searchInput = document.getElementById('table-search');
        if (searchInput) searchInput.focus();
    }
});
