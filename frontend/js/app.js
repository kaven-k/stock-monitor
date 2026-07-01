/**
 * StockMonitor v2.0 - 前端 SPA 主应用
 * 前后端分离, 通过 REST API + WebSocket 通信
 */

// ====== 全局状态 ======
const State = {
    stocks: [],
    quotes: {},
    groups: [],
    alertRules: [],
    currentView: 'all',
    currentGroupId: null,
    selectedStock: null,
    monitorRunning: true,
    refreshInterval: 5,
    countdown: 5,
    sortField: 'change_pct',
    sortOrder: 'desc',
    searchKeyword: '',
    detailTab: 'chart',
    klineData: null,
    user: null,
    priceSparkline: {},  // {code: [最近5次change_pct]}
};

let socket = null;
let countdownTimer = null;  // 倒计时定时器

// ====== 主题管理 ======
function initTheme() {
    const saved = localStorage.getItem('stockmonitor-theme') || 'light';
    applyTheme(saved);
}
function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'light';
    const next = current === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    localStorage.setItem('stockmonitor-theme', next);
}
function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    const btn = document.getElementById('theme-toggle-btn');
    if (btn) btn.textContent = theme === 'dark' ? '☀️' : '🌙';
    // 刷新所有活跃的 ECharts 图表
    if (typeof Charts !== 'undefined' && Charts.refreshAllThemes) {
        Charts.refreshAllThemes();
    }
}

// ====== 入口 ======
document.addEventListener('DOMContentLoaded', async () => {
    initTheme();
    
    // 事件委托：K线周期按钮 (动态HTML, 用委托监听)
    const detailContent = document.getElementById('detail-content');
    if (detailContent) {
        detailContent.addEventListener('click', e => {
            const btn = e.target.closest('.kline-period-btn');
            if (btn && btn.dataset.period) {
                changeKlinePeriod(btn.dataset.period);
            }
        });
    }
    
    // 检查是否已登录
    if (api.token) {
        try {
            State.user = await api.getMe();
            showApp();
        } catch (e) {
            api.clearToken();
            showLogin();
        }
    } else {
        showLogin();
    }
});

// ====== 登录 ======
function showLogin() {
    document.getElementById('login-page').style.display = 'flex';
    document.getElementById('app-page').classList.add('app-hidden');
    checkApiStatus();
}

function showApp() {
    document.getElementById('login-page').style.display = 'none';
    document.getElementById('app-page').classList.remove('app-hidden');
    initApp();
}

async function checkApiStatus() {
    const dot = document.getElementById('api-status-dot');
    try {
        await fetch(`${API_BASE}/`);
        dot.classList.add('online');
        dot.classList.remove('offline');
    } catch (e) {
        dot.classList.add('offline');
        dot.classList.remove('online');
    }
}

let isRegisterMode = false;

function toggleRegister() {
    isRegisterMode = !isRegisterMode;
    const btn = document.getElementById('login-btn');
    const confirm = document.getElementById('register-confirm-group');
    const hint = document.getElementById('login-hint');
    const err = document.getElementById('login-error');
    err.textContent = '';
    
    if (isRegisterMode) {
        btn.textContent = '注 册';
        confirm.style.display = '';
        hint.innerHTML = '<a onclick="toggleRegister()">返回登录</a>';
    } else {
        btn.textContent = '登 录';
        confirm.style.display = 'none';
        hint.innerHTML = '没有账号？<a onclick="toggleRegister()">立即注册</a>';
    }
}

async function doLogin() {
    const username = document.getElementById('login-username').value.trim();
    const password = document.getElementById('login-password').value.trim();
    const btn = document.getElementById('login-btn');
    const errEl = document.getElementById('login-error');

    if (!username || !password) {
        errEl.textContent = '请输入用户名和密码';
        return;
    }

    if (isRegisterMode) {
        const confirm = document.getElementById('login-password-confirm').value.trim();
        if (password !== confirm) {
            errEl.textContent = '两次密码不一致';
            return;
        }
        if (password.length < 6) {
            errEl.textContent = '密码至少6个字符';
            return;
        }
    }

    btn.disabled = true;
    btn.textContent = isRegisterMode ? '注册中...' : '登录中...';
    errEl.textContent = '';

    try {
        const data = isRegisterMode ? await api.register(username, password) : await api.login(username, password);
        State.user = { user_id: data.user_id, username: data.username, role: data.role };
        showApp();
    } catch (e) {
        errEl.textContent = e.message || (isRegisterMode ? '注册失败' : '登录失败');
    } finally {
        btn.disabled = false;
        btn.textContent = isRegisterMode ? '注 册' : '登 录';
    }
}

// 监听回车键登录
document.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && document.getElementById('login-page').style.display !== 'none') {
        doLogin();
    }
});

// 认证过期回调
onAuthExpired = () => {
    showLogin();
    showToast('登录已过期，请重新登录', 'warning');
};

// ====== 应用初始化 ======
async function initApp() {
    document.getElementById('sidebar-username').textContent = State.user.username;
    document.getElementById('login-page').style.display = 'none';

    await loadInitialData();
    initSocket();
    startCountdown();
}

async function loadInitialData() {
    try {
        const [stocks, groups, rules, status] = await Promise.all([
            api.getStocks(),
            api.getGroups(),
            api.getAlertRules(),
            api.getMonitorStatus().catch(() => ({})),
        ]);

        State.stocks = stocks;
        State.groups = groups;
        State.alertRules = rules;
        if (status.running !== undefined) State.monitorRunning = status.running;
        if (status.interval) {
            State.refreshInterval = status.interval;
            State.countdown = status.interval;  // 同步倒计时初始值
        }

        renderGroups();
        updateNavCounts();
        updateMonitorUI();

        if (State.stocks.length > 0) {
            const codes = State.stocks.map(s => s.code).join(',');
            const data = await api.getQuotes(codes);
            State.quotes = data.quotes || {};
            document.getElementById('last-update-time').textContent = `数据更新: ${data.time}`;
        }

        renderStockTable();
    } catch (e) {
        console.error('加载数据失败:', e);
        showToast('数据加载失败: ' + e.message, 'error');
    }
}

// ====== WebSocket ======
function initSocket() {
    socket = io(API_BASE.replace('/api/v1', ''));

    socket.on('connect', () => console.log('[Socket] 已连接'));
    socket.on('quotes_update', data => {
        if (data.data) {
            State.quotes = { ...State.quotes, ...data.data };
            // 更新 sparkline 历史数据 (保留最近5次)
            Object.entries(data.data).forEach(([code, q]) => {
                if (!State.priceSparkline[code]) State.priceSparkline[code] = [];
                State.priceSparkline[code].push(q.change_pct || 0);
                if (State.priceSparkline[code].length > 5) State.priceSparkline[code].shift();
            });
            document.getElementById('last-update-time').textContent = `数据更新: ${data.time}`;
            document.getElementById('status-time').textContent = data.time;
            renderStockTable();
            if (State.selectedStock && !document.getElementById('detail-panel').classList.contains('collapsed')) {
                refreshDetailPrices();  // 仅刷新数字，不重建图表
            }
        }
    });
    socket.on('alerts_new', data => {
        if (data.alerts?.length > 0) {
            showAlertPopup(data.alerts);
            updateNavCounts();
        }
    });
    socket.on('signal_update', data => {
        if (data.signals?.length > 0) {
            // 在股票表格中闪烁信号变化的股票行
            data.signals.forEach(s => {
                const row = document.querySelector(`tr[data-code="${s.code}"]`);
                if (row) {
                    row.style.transition = 'background 0.3s';
                    row.style.background = s.level === 'strong_buy' || s.level === 'buy'
                        ? 'rgba(235,80,50,0.08)'
                        : s.level === 'strong_sell' || s.level === 'sell'
                            ? 'rgba(47,158,68,0.08)'
                            : 'rgba(59,130,246,0.08)';
                    setTimeout(() => { row.style.background = ''; }, 3000);
                }
            });
            // 在右下角弹窗提示（仅强烈信号）
            const strongSignals = data.signals.filter(s => s.level === 'strong_buy' || s.level === 'strong_sell');
            if (strongSignals.length > 0) {
                showSignalPopup(strongSignals);
            }
        }
    });
    socket.on('disconnect', () => {
        console.log('[Socket] 已断开');
        showToast('实时连接已断开，尝试重连...', 'warning');
    });
}

// ====== 监控状态 ======
function updateMonitorUI() {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const btn = document.getElementById('btn-monitor-toggle');
    if (State.monitorRunning) {
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
    try {
        if (State.monitorRunning) {
            await api.stopMonitor();
        } else {
            await api.startMonitor();
        }
        State.monitorRunning = !State.monitorRunning;
        updateMonitorUI();
    } catch (e) {
        showToast('操作失败', 'error');
    }
}

// ====== 倒计时 ======
function startCountdown() {
    if (countdownTimer) clearInterval(countdownTimer);
    countdownTimer = setInterval(() => {
        State.countdown--;
        if (State.countdown <= 0) State.countdown = State.refreshInterval;
        const el = document.getElementById('refresh-countdown');
        if (el) el.textContent = State.countdown;
    }, 1000);
}

// ====== 股票表格 ======
function renderStockTable() {
    const tbody = document.getElementById('stock-table-body');
    if (!tbody) return;

    let displayStocks = getFilteredStocks();
    if (displayStocks.length === 0) {
        tbody.innerHTML = `<tr><td colspan="13"><div class="empty-state"><div class="icon">📊</div><div class="text">${State.stocks.length === 0 ? '暂无股票<br><small>点击"添加股票"开始监控</small>' : '无匹配结果'}</div></div></td></tr>`;
        return;
    }

    displayStocks.sort((a, b) => {
        const qa = State.quotes[a.code] || {};
        const qb = State.quotes[b.code] || {};
        let va = parseFloat(qa[State.sortField]) || 0;
        let vb = parseFloat(qb[State.sortField]) || 0;
        return State.sortOrder === 'desc' ? vb - va : va - vb;
    });

    if (State.searchKeyword) {
        const kw = State.searchKeyword.toLowerCase();
        displayStocks = displayStocks.filter(s => {
            const q = State.quotes[s.code];
            return s.code.includes(kw) || s.name.toLowerCase().includes(kw) || (q?.name && q.name.toLowerCase().includes(kw));
        });
    }

    let html = '';
    displayStocks.forEach(s => {
        const q = State.quotes[s.code];
        const tags = (s.tags || '').split(',').filter(t => t.trim());
        const tagsHtml = tags.length ? tags.map(t => `<span class="tag tag-primary" style="margin:1px;cursor:pointer" onclick="event.stopPropagation();editTags('${s.code}')" title="点击编辑标签">${t.trim()}</span>`).join(' ') : `<span style="color:var(--text-muted);font-size:11px;cursor:pointer" onclick="event.stopPropagation();editTags('${s.code}')" title="点击添加标签">+标签</span>`;

        if (!q) {
            html += `<tr data-code="${s.code}"><td><span class="stock-name">${s.name}</span> <span class="stock-code">${s.code}</span></td><td>${tagsHtml}</td><td colspan="10" style="text-align:center;color:var(--text-muted)">加载中...</td><td><button class="btn btn-xs btn-danger" onclick="event.stopPropagation();deleteStock('${s.code}')">删除</button></td></tr>`;
            return;
        }
        const cc = q.change_pct > 0 ? 'change-up' : (q.change_pct < 0 ? 'change-down' : 'change-flat');
        const sign = q.change_pct > 0 ? '+' : '';
        const sc = State.selectedStock === s.code ? 'selected' : '';
        const pc = q.change_pct > 0 ? 'color:var(--color-up)' : (q.change_pct < 0 ? 'color:var(--color-down)' : '');

        html += `<tr data-code="${s.code}" class="${sc}" onclick="selectStock('${s.code}')">
            <td><span class="stock-name">${s.name}</span> <span class="stock-code">${s.code}</span></td>
            <td style="max-width:140px;overflow:hidden">${tagsHtml}</td>
            <td style="${pc};font-weight:600">${q.price.toFixed(2)}</td>
            <td class="${cc}" style="font-weight:600;min-width:90px">${sign}${q.change_pct.toFixed(2)}%${renderSparkline(s.code)}</td>
            <td>${q.open.toFixed(2)}</td>
            <td style="color:var(--color-up)">${q.high.toFixed(2)}</td>
            <td style="color:var(--color-down)">${q.low.toFixed(2)}</td>
            <td>${Charts.formatVol(q.volume)}</td>
            <td>${Charts.formatAmt(q.amount_wan)}</td>
            <td>${q.turnover_pct.toFixed(2)}%</td>
            <td>${q.pe_ttm > 0 ? q.pe_ttm.toFixed(1) : '--'}</td>
            <td>${q.pb > 0 ? q.pb.toFixed(2) : '--'}</td>
            <td><button class="btn btn-xs" onclick="event.stopPropagation();addToGroupPrompt('${s.code}')">📁</button> <button class="btn btn-xs btn-danger" onclick="event.stopPropagation();deleteStock('${s.code}')">✕</button></td>
        </tr>`;
    });
    tbody.innerHTML = html;
}

function getFilteredStocks() {
    if (State.currentView === 'all') return [...State.stocks];
    if (State.currentView === 'rules') return [];
    if (State.currentView === 'alerts') {
        const codes = new Set();
        State.alertRules.forEach(r => (r.stocks || []).forEach(s => codes.add(s.stock_code)));
        return State.stocks.filter(s => codes.has(s.code));
    }
    const g = State.groups.find(g => g.id == State.currentGroupId);
    if (!g) return [];
    const mc = new Set((g.members || []).map(m => m.stock_code));
    return State.stocks.filter(s => mc.has(s.code));
}

function filterStocks() {
    State.searchKeyword = document.getElementById('table-search')?.value || '';
    renderStockTable();
}

function sortBy(field) {
    State.sortOrder = State.sortField === field ? (State.sortOrder === 'desc' ? 'asc' : 'desc') : 'desc';
    State.sortField = field;
    renderStockTable();
}

// ====== 股票管理 ======
function showAddStockModal() {
    document.getElementById('modal-add-stock').style.display = 'flex';
    document.getElementById('stock-search-input').value = '';
    document.getElementById('search-results').style.display = 'none';
    document.getElementById('manual-code').value = '';
    document.getElementById('manual-name').value = '';
    setTimeout(() => document.getElementById('stock-search-input').focus(), 100);
}

let searchTimer;
async function searchStocks() {
    const kw = document.getElementById('stock-search-input').value.trim();
    const rd = document.getElementById('search-results');
    if (!kw || kw.length < 1) { rd.style.display = 'none'; return; }
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
        try {
            const results = await api.searchStocks(kw);
            rd.innerHTML = results.length ? results.map(r => `<div class="search-result-item" onclick="addStockFromSearch('${r.code}','${r.name}','${r.market}')"><span><span class="name">${r.name}</span> <span class="code">${r.code}</span></span></div>`).join('') : '<div style="padding:12px;color:var(--text-muted);font-size:12px">无结果</div>';
            rd.style.display = 'block';
        } catch (e) { console.error(e); }
    }, 300);
}

async function addStockFromSearch(code, name, market) {
    await addStock(code, name, market);
    document.getElementById('modal-add-stock').style.display = 'none';
}

async function addStockManual() {
    const code = document.getElementById('manual-code').value.trim();
    const name = document.getElementById('manual-name').value.trim();
    if (!code || !name) { showToast('请输入代码和名称', 'warning'); return; }
    await addStock(code, name);
    document.getElementById('modal-add-stock').style.display = 'none';
}

async function addStock(code, name, market = 'A') {
    try {
        await api.addStock(code, name, market);
        showToast(`${name}(${code}) 添加成功`, 'success');
        await loadInitialData();
        // loadInitialData 已包含 renderStockTable()，无需再次调用
    } catch (e) { showToast(e.message, 'error'); }
}

async function deleteStock(code) {
    // 使用自定义确认弹窗
    const stock = State.stocks.find(s => s.code === code);
    const name = stock?.name || code;
    document.getElementById('delete-msg').textContent = `确定要删除 "${name}" (${code}) 吗？`;
    document.getElementById('modal-confirm-delete').style.display = 'flex';
    document.getElementById('btn-confirm-delete').onclick = async () => {
        document.getElementById('modal-confirm-delete').style.display = 'none';
        try {
            await api.removeStock(code);
            State.stocks = State.stocks.filter(s => s.code !== code);
            delete State.quotes[code];
            if (State.selectedStock === code) closeDetail();
            renderStockTable();
            renderGroups();
            showToast('已删除', 'success');
        } catch (e) { showToast(e.message, 'error'); }
    };
}

// ====== 详情面板 ======
State.klinePeriod = 'day';  // 当前K线周期

async function selectStock(code) {
    State.selectedStock = code;
    State.klineData = null;
    State.klinePeriod = 'day';
    document.getElementById('detail-panel').classList.remove('collapsed');
    updateDetailPanel();
    try {
        State.klineData = await api.getKline(code, State.klinePeriod);
        if (State.detailTab === 'chart') renderKlineChart();
        else if (State.detailTab === 'indicator') renderIndicatorCharts();
    } catch (e) { console.error(e); }
    renderStockTable();
}

async function changeKlinePeriod(period) {
    if (State.klinePeriod === period) return;
    State.klinePeriod = period;
    State.klineData = null;
    
    // 更新按钮状态
    document.querySelectorAll('.kline-period-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.period === period);
    });
    
    const code = State.selectedStock;
    if (!code) return;
    try {
        State.klineData = await api.getKline(code, period);
        renderKlineChart();
    } catch (e) { console.error(e); }
}

function closeDetail() {
    State.selectedStock = null;
    State.klineData = null;
    document.getElementById('detail-panel').classList.add('collapsed');
    Charts.disposeAll();
    renderStockTable();
}

function updateDetailPanel() {
    if (!State.selectedStock) return;
    const code = State.selectedStock;
    const q = State.quotes[code];
    if (!q) return;
    const stock = State.stocks.find(s => s.code === code);
    const name = q?.name || stock?.name || code;

    document.getElementById('detail-name').textContent = name;
    document.getElementById('detail-code').textContent = code;

    const container = document.getElementById('detail-content');
    if (!container) return;

    const pc = q.change_pct > 0 ? 'var(--color-up)' : (q.change_pct < 0 ? 'var(--color-down)' : 'var(--text-primary)');
    const sign = q.change_pct > 0 ? '+' : '';
    const cc = q.change_pct > 0 ? 'change-up' : (q.change_pct < 0 ? 'change-down' : 'change-flat');

    // 价格卡片（updateDetailPanel 会重建，refreshDetailPrices 只更新文本）
    let html = `<div class="price-card">
        <div class="price-main"><span class="price-value" style="color:${pc}">${q.price.toFixed(2)}</span><span class="price-change ${cc}">${sign}${q.change_pct.toFixed(2)}%</span></div>
        <div class="price-stats">
            <div class="stat-item"><div class="stat-label">今开</div><div class="stat-value">${q.open.toFixed(2)}</div></div>
            <div class="stat-item"><div class="stat-label">最高</div><div class="stat-value" style="color:var(--color-up)">${q.high.toFixed(2)}</div></div>
            <div class="stat-item"><div class="stat-label">最低</div><div class="stat-value" style="color:var(--color-down)">${q.low.toFixed(2)}</div></div>
            <div class="stat-item"><div class="stat-label">成交量</div><div class="stat-value">${Charts.formatVol(q.volume)}</div></div>
            <div class="stat-item"><div class="stat-label">成交额</div><div class="stat-value">${Charts.formatAmt(q.amount_wan)}</div></div>
            <div class="stat-item"><div class="stat-label">换手率</div><div class="stat-value">${q.turnover_pct.toFixed(2)}%</div></div>
            <div class="stat-item"><div class="stat-label">PE</div><div class="stat-value">${q.pe_ttm > 0 ? q.pe_ttm.toFixed(1) : '--'}</div></div>
            <div class="stat-item"><div class="stat-label">PB</div><div class="stat-value">${q.pb > 0 ? q.pb.toFixed(2) : '--'}</div></div>
            <div class="stat-item"><div class="stat-label">市值</div><div class="stat-value">${q.mcap_yi > 0 ? (q.mcap_yi >= 10000 ? (q.mcap_yi/10000).toFixed(2)+'万亿' : q.mcap_yi.toFixed(0)+'亿') : '--'}</div></div>
        </div></div>`;
    if (State.detailTab === 'chart') {
        const p = State.klinePeriod || 'day';
        html += '<div style="display:flex;align-items:center;gap:6px;margin-bottom:8px">' +
            '<span style="font-size:11px;color:var(--text-muted)">周期:</span>' +
            `<button class="kline-period-btn${p === 'day' ? ' active' : ''}" data-period="day">日K</button>` +
            `<button class="kline-period-btn${p === 'week' ? ' active' : ''}" data-period="week">周K</button>` +
            `<button class="kline-period-btn${p === 'month' ? ' active' : ''}" data-period="month">月K</button>` +
            '<span style="font-size:10px;color:var(--text-muted);margin-left:auto">MACD内嵌</span></div>';
        html += '<div class="chart-container" id="kline-chart" style="height:420px"></div>';
        html += '<div class="indicator-legend"><div class="legend-item"><div class="legend-line" style="background:var(--color-up)"></div>MA5</div><div class="legend-item"><div class="legend-line" style="background:#6366f1"></div>MA20</div><div class="legend-item"><div class="legend-line" style="background:#a78bfa"></div>BOLL</div><div class="legend-item"><div class="legend-line" style="background:var(--color-up)"></div>MACD</div></div><div id="ma-signals"></div>';
    } else if (State.detailTab === 'indicator') {
        html += '<div class="chart-container" id="rsi-chart"></div><div class="chart-container" id="macd-chart"></div><div class="chart-container" id="kdj-chart"></div>';
    } else {
        html += `<div style="padding:8px 0">
            <div class="form-group"><label>涨停价</label><span style="font-weight:600;color:var(--color-up)">${q.limit_up.toFixed(2)}</span></div>
            <div class="form-group"><label>跌停价</label><span style="font-weight:600;color:var(--color-down)">${q.limit_down.toFixed(2)}</span></div>
            <div class="form-group"><label>量比</label><span>${(q.vol_ratio || 0).toFixed(2)}</span></div>
            <div class="form-group"><label>振幅</label><span>${(q.amplitude_pct || 0).toFixed(2)}%</span></div>
        </div>`;
    }
    container.innerHTML = html;
    if (State.klineData) {
        if (State.detailTab === 'chart') renderKlineChart();
        else if (State.detailTab === 'indicator') renderIndicatorCharts();
    }
}

/** 仅刷新详情面板价格数字，不重建图表（行情推送时调用） */
function refreshDetailPrices() {
    if (!State.selectedStock) return;
    const code = State.selectedStock;
    const q = State.quotes[code];
    if (!q) return;

    const stock = State.stocks.find(s => s.code === code);
    const name = q?.name || stock?.name || code;
    document.getElementById('detail-name').textContent = name;
    document.getElementById('detail-code').textContent = code;
    buildDetailHeader(q);

    // 仅在信息Tab时刷新（因为没图表，直接reconstruct安全）
    if (State.detailTab === 'info') {
        const container = document.getElementById('detail-content');
        if (container) {
            container.innerHTML = `<div style="padding:8px 0">
                <div class="form-group"><label>涨停价</label><span style="font-weight:600;color:var(--color-up)">${q.limit_up.toFixed(2)}</span></div>
                <div class="form-group"><label>跌停价</label><span style="font-weight:600;color:var(--color-down)">${q.limit_down.toFixed(2)}</span></div>
                <div class="form-group"><label>量比</label><span>${(q.vol_ratio || 0).toFixed(2)}</span></div>
                <div class="form-group"><label>振幅</label><span>${(q.amplitude_pct || 0).toFixed(2)}%</span></div>
            </div>`;
        }
    }
}

/** 构建详情面板标题栏价格卡片 */
function buildDetailHeader(q) {
    const pc = q.change_pct > 0 ? 'var(--color-up)' : (q.change_pct < 0 ? 'var(--color-down)' : 'var(--text-primary)');
    const sign = q.change_pct > 0 ? '+' : '';
    const cc = q.change_pct > 0 ? 'change-up' : (q.change_pct < 0 ? 'change-down' : 'change-flat');

    // 用 price-card 容器如果已存在则复用，否则创建
    let card = document.querySelector('.price-card');
    if (!card) {
        // 首次创建时 updateDetailPanel 会生成，此后 refreshDetailPrices 直接更新已有DOM
        return;
    }
    card.querySelector('.price-value').style.color = pc;
    card.querySelector('.price-value').textContent = q.price.toFixed(2);
    const chgEl = card.querySelector('.price-change');
    chgEl.textContent = sign + q.change_pct.toFixed(2) + '%';
    chgEl.className = 'price-change ' + cc;

    // 更新统计项
    const stats = card.querySelectorAll('.stat-value');
    if (stats.length >= 9) {
        stats[0].textContent = q.open.toFixed(2);
        stats[1].textContent = q.high.toFixed(2);
        stats[1].style.color = 'var(--color-up)';
        stats[2].textContent = q.low.toFixed(2);
        stats[2].style.color = 'var(--color-down)';
        stats[3].textContent = Charts.formatVol(q.volume);
        stats[4].textContent = Charts.formatAmt(q.amount_wan);
        stats[5].textContent = q.turnover_pct.toFixed(2) + '%';
        stats[6].textContent = q.pe_ttm > 0 ? q.pe_ttm.toFixed(1) : '--';
        stats[7].textContent = q.pb > 0 ? q.pb.toFixed(2) : '--';
        stats[8].textContent = q.mcap_yi > 0 ? (q.mcap_yi >= 10000 ? (q.mcap_yi/10000).toFixed(2)+'万亿' : q.mcap_yi.toFixed(0)+'亿') : '--';
    }
}

function switchDetailTab(tab, el) {
    State.detailTab = tab;
    document.querySelectorAll('.detail-tab').forEach(t => t.classList.remove('active'));
    if (el) el.classList.add('active');
    updateDetailPanel();
}

function renderKlineChart() {
    const d = State.klineData;
    if (!d?.kline) return;
    Charts.renderKline('kline-chart', d.kline, d.indicators || {}, State.klinePeriod || 'day');
    
    // MA 买卖信号分析
    const analysis = Charts.analyzeMA(d.kline, d.indicators || {});
    const signalEl = document.getElementById('ma-signals');
    if (signalEl && analysis) {
        signalEl.innerHTML = `
            <div style="padding:12px 16px;border-radius:8px;margin:8px 0;background:${analysis.suggestion.color === '#e03131' || analysis.suggestion.color === '#ef4444' ? 'var(--bg-tertiary)' : 'var(--bg-tertiary)'};border:1px solid ${analysis.suggestion.color}30">
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
                    <span style="font-weight:700;font-size:15px;color:${analysis.suggestion.color}">${analysis.suggestion.text}</span>
                    <span style="font-size:12px;color:var(--text-muted)">${analysis.suggestion.desc}</span>
                </div>
                ${analysis.signals.map(s => `<div style="display:flex;align-items:center;gap:6px;margin:4px 0;font-size:12px">
                    <span style="color:${s.type === 'bullish' ? 'var(--color-up)' : s.type === 'bearish' ? 'var(--color-down)' : 'var(--text-muted)'}">${s.type === 'bullish' ? '▲' : s.type === 'bearish' ? '▼' : '◆'}</span>
                    <span style="color:var(--text-secondary)">${s.text}</span>
                </div>`).join('')}
            </div>`;
    }
}

function renderIndicatorCharts() {
    const d = State.klineData;
    if (!d?.kline) return;
    const dates = d.kline.map(k => k.date);
    const ind = d.indicators || {};
    Charts.renderIndicator('rsi-chart', 'RSI', dates, [
        { name: 'RSI6', data: ind.rsi6 || [], color: '#6366f1' },
        { name: 'RSI14', data: ind.rsi14 || [], color: '#f59e0b' },
    ], [{ value: 80, label: { formatter: '超买80' }, lineStyle: { color: '#e03131', type: 'dashed' } }, { value: 20, label: { formatter: '超卖20' }, lineStyle: { color: '#2f9e44', type: 'dashed' } }]);
    Charts.renderMacd('macd-chart', dates, ind);
    Charts.renderIndicator('kdj-chart', 'KDJ', dates, [
        { name: 'K', data: ind.k || [], color: '#6366f1' },
        { name: 'D', data: ind.d || [], color: '#f59e0b' },
        { name: 'J', data: ind.j || [], color: '#e03131' },
    ]);
}

// ====== 侧边栏分组 ======
function renderGroups() {
    const c = document.getElementById('group-list');
    if (!c) return;
    c.innerHTML = State.groups.length ? State.groups.map(g => `<div class="sidebar-item" data-group-id="${g.id}" onclick="switchGroupView(${g.id})"><span class="dot" style="background:${g.color}"></span><span>${g.name}</span><span class="count">${(g.members||[]).length}</span></div>`).join('') : '<div style="padding:8px 12px;font-size:12px;color:var(--text-muted)">暂无分组</div>';
}

function switchGroupView(groupId) {
    State.currentView = groupId ? 'group' : 'all';
    State.currentGroupId = groupId;
    document.getElementById('view-title').textContent = groupId ? (State.groups.find(g => g.id == groupId)?.name || '') : '全部股票';
    document.querySelectorAll('.sidebar-item').forEach(e => e.classList.remove('active'));
    const item = document.querySelector('.sidebar-item[data-group-id="' + groupId + '"]') || document.querySelector('.sidebar-item[data-view="all"]');
    if (item) item.classList.add('active');
    showStockView();  // 确保从AI视图/市场视图切换回股票面板
    renderStockTable();
}

function showAddGroupModal() {
    document.getElementById('modal-add-group').style.display = 'flex';
    document.getElementById('group-name').value = '';
    setTimeout(() => document.getElementById('group-name').focus(), 100);
}

async function createGroup() {
    const name = document.getElementById('group-name').value.trim();
    const color = document.getElementById('group-color').value;
    if (!name) { showToast('请输入分组名称', 'warning'); return; }
    try {
        await api.createGroup(name, color);
        State.groups = await api.getGroups();
        renderGroups();
        document.getElementById('modal-add-group').style.display = 'none';
        showToast('创建成功', 'success');
    } catch (e) { showToast(e.message, 'error'); }
}

async function addToGroupPrompt(code) {
    if (!State.groups.length) { showToast('请先创建分组', 'warning'); return; }
    const stock = State.stocks.find(s => s.code === code);
    
    // 使用自定义弹窗选择分组
    const groupOptions = State.groups.map(g => 
        `<div class="search-result-item" onclick="selectGroupForStock('${code}', ${g.id})" style="cursor:pointer">
            <span><span class="dot" style="background:${g.color};width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:8px"></span>${g.name}</span>
            <span style="font-size:11px;color:var(--text-muted)">${(g.members||[]).length} 只</span>
        </div>`
    ).join('');
    
    document.getElementById('delete-msg').innerHTML = `<div style="text-align:left"><div style="font-size:14px;margin-bottom:12px">将 <b>${stock?.name||code}</b> 加入分组:</div><div style="max-height:200px;overflow-y:auto;border:1px solid var(--border-color);border-radius:6px">${groupOptions}</div></div>`;
    document.getElementById('btn-confirm-delete').style.display = 'none';
    document.getElementById('modal-confirm-delete').querySelector('.modal-header h3').textContent = '加入分组';
    document.getElementById('modal-confirm-delete').style.display = 'flex';
}

function selectGroupForStock(code, groupId) {
    // 恢复确认弹窗状态
    document.getElementById('modal-confirm-delete').style.display = 'none';
    document.getElementById('btn-confirm-delete').style.display = '';
    document.getElementById('modal-confirm-delete').querySelector('.modal-header h3').textContent = '确认删除';
    document.getElementById('delete-msg').textContent = '';  // 清空消息
    
    api.addStockToGroup(groupId, code).then(async () => {
        State.groups = await api.getGroups();
        renderGroups();
        showToast('已加入分组', 'success');
    }).catch(e => showToast(e.message, 'error'));
}

// ====== 预警 ======
function showAlertRuleModal() {
    document.getElementById('modal-alert-rule').style.display = 'flex';
    document.getElementById('alert-rule-name').value = '';
    document.getElementById('alert-rule-type').value = 'price_up';
    onAlertTypeChange();
    resetAlertRuleModal();
    const c = document.getElementById('alert-stock-select');
    c.innerHTML = State.stocks.length ? State.stocks.map(s => `<label style="display:flex;align-items:center;gap:8px;padding:4px 0;cursor:pointer;font-size:13px"><input type="checkbox" value="${s.code}" class="alert-stock-checkbox"> ${s.name} (${s.code})</label>`).join('') : '<div style="color:var(--text-muted);font-size:12px">请先添加股票</div>';
}

function onAlertTypeChange() {
    const type = document.getElementById('alert-rule-type').value;
    const configs = {
        price_up: '<input type="number" class="form-input" id="param-threshold" placeholder="价格阈值" step="0.01">',
        price_down: '<input type="number" class="form-input" id="param-threshold" placeholder="价格阈值" step="0.01">',
        change_up: '<input type="number" class="form-input" id="param-threshold" placeholder="涨幅阈值(%)" step="0.1" value="5">',
        change_down: '<input type="number" class="form-input" id="param-threshold" placeholder="跌幅阈值(%)" step="0.1" value="5">',
        volume_surge: '<div style="display:flex;gap:8px"><input type="number" class="form-input" id="param-multiplier" placeholder="放量倍数" step="0.1" value="3"><input type="number" class="form-input" id="param-avg_days" placeholder="均量天数" value="20"></div>',
        turnover_high: '<input type="number" class="form-input" id="param-threshold" placeholder="换手率(%)" step="0.1" value="10">',
        amplitude_high: '<input type="number" class="form-input" id="param-threshold" placeholder="振幅(%)" step="0.1" value="10">',
        price_break_ma: '<div style="display:flex;gap:8px"><select class="form-select" id="param-direction" style="width:120px"><option value="up">向上突破</option><option value="down">向下跌破</option></select><input type="number" class="form-input" id="param-ma_period" placeholder="均线周期" value="20"></div>',
        continuous_up: '<input type="number" class="form-input" id="param-days" placeholder="连续上涨天数" value="3">',
        continuous_down: '<input type="number" class="form-input" id="param-days" placeholder="连续下跌天数" value="3">',
        volume_ratio: '<input type="number" class="form-input" id="param-threshold" placeholder="量比阈值" step="0.1" value="2">',
        limit_up: '<div style="color:var(--text-muted);font-size:12px">触及涨停时触发</div>',
        limit_down: '<div style="color:var(--text-muted);font-size:12px">触及跌停时触发</div>',
        ma_signal_change: '<div style="color:var(--text-muted);font-size:12px">AI信号引擎检测到强烈买卖信号变化时触发</div>',
        compound: '<div style="color:var(--text-muted);font-size:12px">🔧 复合规则需在JSON编辑器中手动配置params: {"operator":"and","rules":[{"type":"change_up","params":{"threshold":3}},{"type":"volume_ratio","params":{"threshold":1.5}}]}</div>',
    };
    document.getElementById('alert-params').innerHTML = configs[type] || '';
}

async function createAlertRule() {
    const name = document.getElementById('alert-rule-name').value.trim();
    const ruleType = document.getElementById('alert-rule-type').value;
    const notifyFeishu = document.getElementById('alert-notify-feishu')?.checked ? 1 : 0;
    if (!name) { showToast('请输入规则名称', 'warning'); return; }

    const paramKeys = {
        price_up: ['threshold'], price_down: ['threshold'], change_up: ['threshold'], change_down: ['threshold'],
        volume_surge: ['multiplier', 'avg_days'], turnover_high: ['threshold'], amplitude_high: ['threshold'],
        price_break_ma: ['direction', 'ma_period'], continuous_up: ['days'], continuous_down: ['days'],
        volume_ratio: ['threshold'], limit_up: [], limit_down: [],
        ma_signal_change: [], compound: [],
    };
    const params = {};
    (paramKeys[ruleType] || []).forEach(k => { const el = document.getElementById(`param-${k}`); if (el) params[k] = el.value; });

    const stockCodes = [...document.querySelectorAll('.alert-stock-checkbox:checked')].map(cb => cb.value);
    if (!stockCodes.length) { showToast('请选择至少一只股票', 'warning'); return; }

    try {
        await api.createAlertRule({ name, rule_type: ruleType, params, stock_codes: stockCodes, notify_feishu: notifyFeishu });
        State.alertRules = await api.getAlertRules(true);
        document.getElementById('modal-alert-rule').style.display = 'none';
        showToast('预警规则创建成功', 'success');
        if (State.currentView === 'rules') renderRulesList();
        updateNavCounts();
    } catch (e) { showToast(e.message, 'error'); }
}

async function showAlertLogs() {
    closeDetail();
    document.getElementById('view-title').textContent = '预警记录';
    document.getElementById('stock-panel').style.display = 'none';
    document.getElementById('rules-panel').style.display = 'none';
    document.getElementById('market-panel').style.display = 'none';
    document.getElementById('ai-panel').style.display = 'none';
    document.getElementById('ai-picks-panel').style.display = 'none';
    document.getElementById('toolbar-stock').style.display = 'none';
    document.getElementById('alert-logs-panel').style.display = '';
    
    try {
        const data = await api.getAlertLogs(100);
        const c = document.getElementById('alert-logs-content');
        c.innerHTML = data.logs.length
            ? data.logs.map(log => {
                const isUnread = !log.is_read;
                return `<div class="alert-item${isUnread ? ' unread' : ''}" onclick="markAlertRead(${log.id}, this)">
                    <div class="alert-item-header">
                        <span class="alert-item-type ${log.alert_type}">${log.alert_type}</span>
                        <span class="alert-item-time">${log.triggered_at}</span>
                    </div>
                    <div class="alert-item-msg">${log.alert_msg}</div>
                    <div style="font-size:11px;color:var(--text-muted);margin-top:4px">${log.rule_name} | ${log.stock_name}(${log.stock_code})</div>
                </div>`;
            }).join('')
            : '<div class="empty-state" style="text-align:center;padding:80px 0;color:var(--text-muted)"><div style="font-size:48px;margin-bottom:12px">🔔</div><div>暂无预警记录</div></div>';
        updateNavCounts();
    } catch (e) { console.error(e); }
}

async function markAlertRead(id, el) {
    try {
        await api.markAlertRead(id);
        if (el) el.classList.remove('unread');
    } catch (e) {}
}

async function updateNavCounts() {
    document.getElementById('nav-all-count').textContent = State.stocks.length;
    document.getElementById('nav-rules-count').textContent = State.alertRules.length;
    renderGroups();
    try {
        const data = await api.getAlertLogs(1);
        const ac = document.getElementById('nav-alert-count');
        ac.textContent = data.total || 0;
        ac.style.background = data.unread > 0 ? '#fef2f2' : 'var(--bg-hover)';
        ac.style.color = data.unread > 0 ? '#ef4444' : 'var(--text-muted)';
    } catch (e) {}
}

function showAlertPopup(alerts) {
    const container = document.getElementById('alert-popup-container');
    alerts.forEach(a => {
        const p = document.createElement('div');
        p.className = 'alert-popup';
        p.innerHTML = `<div class="alert-popup-header">⚠️ ${a.type}<span class="alert-popup-close" onclick="this.closest('.alert-popup').remove()">✕</span></div><div style="font-size:12px">${a.msg}</div><div style="font-size:11px;color:var(--text-muted);margin-top:4px">${a.time}</div>`;
        container.appendChild(p);
        setTimeout(() => p.remove(), 8000);
    });
}

function showSignalPopup(signals) {
    const container = document.getElementById('alert-popup-container');
    signals.forEach(s => {
        const p = document.createElement('div');
        const icon = s.level === 'strong_buy' ? '📈' : '📉';
        const color = s.level === 'strong_buy' ? 'var(--color-up)' : 'var(--color-down)';
        p.className = 'alert-popup';
        p.innerHTML = `<div class="alert-popup-header" style="color:${color}">${icon} 信号变化 — ${s.name}(${s.code})<span class="alert-popup-close" onclick="this.closest('.alert-popup').remove()">✕</span></div><div style="font-size:12px">${s.level_text}</div><div style="font-size:11px;color:var(--text-muted);margin-top:4px">评分: ${s.score} | 价格: ¥${s.price} ${s.change_pct >= 0 ? '+' : ''}${s.change_pct}%</div>`;
        container.appendChild(p);
        setTimeout(() => p.remove(), 10000);
    });
}

// ====== 视图切换 ======
function showStockView() {
    document.getElementById('stock-panel').style.display = '';
    document.getElementById('rules-panel').style.display = 'none';
    document.getElementById('market-panel').style.display = 'none';
    document.getElementById('ai-panel').style.display = 'none';
    document.getElementById('ai-picks-panel').style.display = 'none';
    document.getElementById('alert-logs-panel').style.display = 'none';
    document.getElementById('toolbar-stock').style.display = '';
}

// ====== 刷新间隔 ======
function toggleRefreshMenu() {
    const menu = document.getElementById('refresh-menu');
    menu.style.display = menu.style.display === 'none' ? '' : 'none';
}

async function setRefreshInterval(seconds) {
    document.getElementById('refresh-menu').style.display = 'none';
    try {
        await api.setMonitorInterval(seconds);
        State.refreshInterval = seconds;
        State.countdown = seconds;
        showToast(`刷新间隔已设为 ${seconds} 秒`, 'success');
    } catch (e) { showToast(e.message, 'error'); }
}

// 点击空白关闭菜单
document.addEventListener('click', e => {
    if (!e.target.closest('.refresh-control')) {
        const menu = document.getElementById('refresh-menu');
        if (menu) menu.style.display = 'none';
    }
});

async function markAllAlertsRead() {
    if (!confirm('确定将所有预警记录标记为已读？')) return;
    try {
        await api.post('/alerts/logs/read-all');
        // 刷新列表，去掉所有 unread 样式
        document.querySelectorAll('#alert-logs-content .alert-item.unread').forEach(el => el.classList.remove('unread'));
        updateNavCounts();
        showToast('已全部标记为已读', 'success');
    } catch (e) {
        showToast('操作失败: ' + e.message, 'error');
    }
}

async function showRulesView() {
    closeDetail();  // 关闭详情面板
    State.currentView = 'rules';
    document.getElementById('view-title').textContent = '规则管理';
    document.getElementById('stock-panel').style.display = 'none';
    document.getElementById('rules-panel').style.display = '';
    document.getElementById('market-panel').style.display = 'none';
    document.getElementById('ai-panel').style.display = 'none';
    document.getElementById('ai-picks-panel').style.display = 'none';
    document.getElementById('alert-logs-panel').style.display = 'none';
    document.getElementById('toolbar-stock').style.display = 'none';
    await loadRulesView();
}

async function loadRulesView() {
    try {
        State.alertRules = await api.getAlertRules(true); // 获取所有规则（含已禁用）
        renderRulesList();
        updateNavCounts();
    } catch (e) { console.error('加载规则列表失败:', e); }
}

function renderRulesList() {
    const wrapper = document.getElementById('rules-table-wrapper');
    if (!wrapper) return;

    const rules = State.alertRules || [];
    const kw = (document.getElementById('rules-search')?.value || '').toLowerCase();
    const filtered = kw ? rules.filter(r =>
        r.name.toLowerCase().includes(kw) ||
        r.rule_type.toLowerCase().includes(kw)
    ) : rules;

    if (!filtered.length) {
        wrapper.innerHTML = `<div class="empty-state"><div class="icon">⚙️</div><div class="text">${rules.length ? '无匹配规则' : '暂无预警规则'}<br><small>点击"+ 新建规则"创建第一条规则</small></div></div>`;
        return;
    }

    const typeLabels = {
        price_up: '价格向上突破', price_down: '价格向下跌破',
        change_up: '涨幅超过阈值', change_down: '跌幅超过阈值',
        volume_surge: '成交量异动', turnover_high: '换手率过高',
        amplitude_high: '振幅过大', price_break_ma: '价格突破均线',
        continuous_up: '连续上涨', continuous_down: '连续下跌',
        volume_ratio: '量比异常', limit_up: '触及涨停', limit_down: '触及跌停',
    };

    let html = '<table class="stock-table"><thead><tr><th style="width:180px;text-align:left;padding-left:20px">规则名称</th><th>类型</th><th>参数</th><th>监控股票</th><th>状态</th><th>飞书通知</th><th>操作</th></tr></thead><tbody>';

    filtered.forEach(r => {
        const paramsStr = Object.entries(r.params || {}).map(([k, v]) => `${k}: ${v}`).join(', ') || '-';
        const stocksStr = (r.stocks || []).map(s => `${s.stock_name}(${s.stock_code})`).join(', ') || '-';
        const enabled = r.enabled !== 0;
        const fsEnabled = r.notify_feishu === 1;

        html += `<tr>
            <td style="text-align:left;padding-left:20px"><span class="stock-name">${r.name}</span></td>
            <td><span class="tag tag-primary">${typeLabels[r.rule_type] || r.rule_type}</span></td>
            <td style="font-size:12px;color:var(--text-secondary)">${paramsStr}</td>
            <td style="font-size:12px;color:var(--text-secondary);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${stocksStr}">${stocksStr}</td>
            <td><span class="tag ${enabled ? 'tag-success' : 'tag-danger'}">${enabled ? '已启用' : '已禁用'}</span></td>
            <td>${fsEnabled ? '✅' : '❌'}</td>
            <td>
                <button class="btn btn-xs" onclick="event.stopPropagation();editRule(${r.id})">编辑</button>
                <button class="btn btn-xs" onclick="event.stopPropagation();toggleRule(${r.id},${!enabled})">${enabled ? '禁用' : '启用'}</button>
                <button class="btn btn-xs btn-danger" onclick="event.stopPropagation();confirmDeleteRule(${r.id},'${r.name}')">删除</button>
            </td>
        </tr>`;
    });

    html += '</tbody></table>';
    wrapper.innerHTML = html;
}

function filterRulesList() {
    renderRulesList();
}

async function toggleRule(ruleId, enable) {
    try {
        await api.toggleAlertRule(ruleId);
        State.alertRules = await api.getAlertRules(true);
        renderRulesList();
        showToast(enable ? '规则已启用' : '规则已禁用', 'success');
    } catch (e) { showToast(e.message, 'error'); }
}

async function confirmDeleteRule(ruleId, ruleName) {
    document.getElementById('delete-msg').textContent = `确定要删除规则 "${ruleName}" 吗？此操作不可恢复。`;
    document.getElementById('modal-confirm-delete').style.display = 'flex';
    document.getElementById('btn-confirm-delete').onclick = async () => {
        document.getElementById('modal-confirm-delete').style.display = 'none';
        try {
            await api.deleteAlertRule(ruleId);
            State.alertRules = await api.getAlertRules(true);
            renderRulesList();
            updateNavCounts();
            showToast('规则已删除', 'success');
        } catch (e) { showToast(e.message, 'error'); }
    };
}

// ====== Toast ======
function showToast(msg, type = 'info') {
    const c = document.getElementById('toast-container');
    const t = document.createElement('div');
    t.className = `toast toast-${type}`;
    t.textContent = msg;
    c.appendChild(t);
    setTimeout(() => t.remove(), 3000);
}

// ====== 模态框 ======
function closeModal(id) { document.getElementById(id).style.display = 'none'; }
document.addEventListener('click', e => { if (e.target.classList.contains('modal-overlay')) e.target.style.display = 'none'; });

// ====== 标签编辑 ======
function editTags(code) {
    const stock = State.stocks.find(s => s.code === code);
    if (!stock) return;
    
    document.getElementById('tag-stock-name').textContent = `${stock.name} (${code})`;
    document.getElementById('tag-input').value = stock.tags || '';
    document.getElementById('modal-edit-tags').style.display = 'flex';
    
    document.getElementById('btn-save-tags').onclick = async () => {
        const newTags = document.getElementById('tag-input').value.trim();
        document.getElementById('modal-edit-tags').style.display = 'none';
        try {
            await api.updateStockTags(code, newTags);
            stock.tags = newTags;
            renderStockTable();
            showToast('标签已更新', 'success');
        } catch (e) { showToast(e.message, 'error'); }
    };
}

function quickAddTag(tag) {
    const input = document.getElementById('tag-input');
    const current = input.value.split(',').map(t => t.trim()).filter(Boolean);
    if (!current.includes(tag)) {
        current.push(tag);
        input.value = current.join(', ');
    }
}

// ====== 规则编辑 ======
async function editRule(ruleId) {
    const rule = State.alertRules.find(r => r.id === ruleId);
    if (!rule) return;

    document.getElementById('modal-alert-rule').style.display = 'flex';
    document.getElementById('alert-rule-name').value = rule.name;
    document.getElementById('alert-rule-type').value = rule.rule_type;
    onAlertTypeChange();

    // 填充参数
    const params = rule.params || {};
    setTimeout(() => {
        Object.entries(params).forEach(([k, v]) => {
            const el = document.getElementById(`param-${k}`);
            if (el) el.value = v;
        });
    }, 100);

    // 选中监控股票
    document.getElementById('alert-stock-select').innerHTML = State.stocks.map(s => {
        const checked = (rule.stocks || []).some(rs => rs.stock_code === s.code);
        return `<label style="display:flex;align-items:center;gap:8px;padding:4px 0;cursor:pointer;font-size:13px"><input type="checkbox" value="${s.code}" class="alert-stock-checkbox" ${checked ? 'checked' : ''}> ${s.name} (${s.code})</label>`;
    }).join('');

    // 设置飞书通知状态
    const fsCheckbox = document.getElementById('alert-notify-feishu');
    if (fsCheckbox) fsCheckbox.checked = rule.notify_feishu === 1;

    // 修改提交按钮行为
    const footer = document.querySelector('#modal-alert-rule .modal-footer');
    if (footer) {
        footer.innerHTML = `
            <button class="btn" onclick="closeModal('modal-alert-rule')">取消</button>
            <button class="btn btn-primary" onclick="updateAlertRule(${ruleId})">更新规则</button>
        `;
    }
}

async function updateAlertRule(ruleId) {
    const name = document.getElementById('alert-rule-name').value.trim();
    const ruleType = document.getElementById('alert-rule-type').value;
    const notifyFeishu = document.getElementById('alert-notify-feishu')?.checked ? 1 : 0;
    if (!name) { showToast('请输入规则名称', 'warning'); return; }

    const paramKeys = {
        price_up: ['threshold'], price_down: ['threshold'], change_up: ['threshold'], change_down: ['threshold'],
        volume_surge: ['multiplier', 'avg_days'], turnover_high: ['threshold'], amplitude_high: ['threshold'],
        price_break_ma: ['direction', 'ma_period'], continuous_up: ['days'], continuous_down: ['days'],
        volume_ratio: ['threshold'], limit_up: [], limit_down: [],
        ma_signal_change: [], compound: [],
    };
    const params = {};
    (paramKeys[ruleType] || []).forEach(k => { const el = document.getElementById(`param-${k}`); if (el) params[k] = el.value; });

    const stockCodes = [...document.querySelectorAll('.alert-stock-checkbox:checked')].map(cb => cb.value);
    if (!stockCodes.length) { showToast('请选择至少一只股票', 'warning'); return; }

    try {
        await api.updateAlertRule(ruleId, { name, rule_type: ruleType, params, stock_codes: stockCodes, notify_feishu: notifyFeishu });
        State.alertRules = await api.getAlertRules(true);
        document.getElementById('modal-alert-rule').style.display = 'none';
        resetAlertRuleModal();
        if (State.currentView === 'rules') renderRulesList();
        updateNavCounts();
        showToast('规则已更新', 'success');
    } catch (e) { showToast(e.message, 'error'); }
}

function resetAlertRuleModal() {
    const footer = document.querySelector('#modal-alert-rule .modal-footer');
    if (footer) {
        footer.innerHTML = `<button class="btn" onclick="closeModal('modal-alert-rule')">取消</button><button class="btn btn-primary" onclick="createAlertRule()">创建规则</button>`;
    }
}

// ====== 市场概览 ======
async function showMarketView() {
    closeDetail();
    State.currentView = 'market';
    document.getElementById('view-title').textContent = '市场概览';
    document.getElementById('stock-panel').style.display = 'none';
    document.getElementById('rules-panel').style.display = 'none';
    document.getElementById('market-panel').style.display = '';
    document.getElementById('ai-panel').style.display = 'none';
    document.getElementById('ai-picks-panel').style.display = 'none';
    document.getElementById('alert-logs-panel').style.display = 'none';
    document.getElementById('toolbar-stock').style.display = 'none';
    await loadMarketData();
}

async function loadMarketData() {
    try {
        // 1. 获取市场温度计（情绪 + 涨跌 + 指数）
        const thermo = await api.get('/sentiment/thermometer');
        if (thermo.code === 0) {
            const d = thermo.data;
            const s = d.sentiment || {};
            
            // 情绪指数
            document.getElementById('sent-score').textContent = s.score != null ? s.score : '--';
            document.getElementById('sent-score').style.color = s.level_color || 'var(--text-primary)';
            document.getElementById('sent-level').textContent = s.level_text || '--';
            document.getElementById('sent-level').style.color = s.level_color || 'var(--text-primary)';
            document.getElementById('sent-bar').style.width = (s.score || 50) + '%';
            document.getElementById('sent-bar').style.background = s.level_color || 'var(--primary)';
            
            if (s.factors && s.factors.length > 0) {
                const factorTips = s.factors.map(f => f.name + ': ' + f.value + ' (' + f.score + '分 权重' + f.weight + '%)').join('\n');
                document.getElementById('sent-score').title = factorTips;
            }
            
            // 涨跌分布
            if (d.breadth) {
                document.getElementById('up-count').textContent = d.breadth.up_count || 0;
                document.getElementById('down-count').textContent = d.breadth.down_count || 0;
                document.getElementById('flat-count').textContent = d.breadth.flat_count || 0;
                document.getElementById('up-ratio').textContent = (d.breadth.up_ratio || 0) + '%';
            }
            
            // 涨跌停
            if (d.limit) {
                document.getElementById('limit-up').textContent = d.limit.limit_up || 0;
                document.getElementById('limit-down').textContent = d.limit.limit_down || 0;
            }
            
            // 指数数据
            if (d.indices) {
                const idx = d.indices;
                const setIdx = (idPrice, idChange, data) => {
                    if (data && data.price) {
                        document.getElementById(idPrice).textContent = data.price.toFixed(0);
                        const chgEl = document.getElementById(idChange);
                        const sign = data.change_pct >= 0 ? '+' : '';
                        chgEl.textContent = sign + data.change_pct.toFixed(2) + '%';
                        chgEl.style.color = data.change_pct >= 0 ? 'var(--color-up)' : 'var(--color-down)';
                    }
                };
                setIdx('idx-sh-price', 'idx-sh-change', idx.sh);
                setIdx('idx-sz-price', 'idx-sz-change', idx.sz);
                setIdx('idx-cy-price', 'idx-cy-change', idx.cy);
            }
            
            // 投资建议
            const advice = d.advice || '';
            document.getElementById('market-advice').innerHTML = '<div style="display:flex;align-items:center;gap:8px"><span style="font-size:20px">📊</span><span>' + advice + '</span></div>';
            document.getElementById('market-advice').style.background = '#f8f9fa';
            
            // 数据来源
            document.getElementById('market-source').textContent = '数据来源: 东方财富 | ' + (d.time || '');
        }

        // 2. 行业板块排名
        renderSectorRanking('industry-ranking', 'industry');
        
        // 3. 概念板块排名
        renderSectorRanking('concept-ranking', 'concept');
        
        // 4. 主线板块
        try {
            const mainRes = await api.get('/sector/main?top_n=5');
            if (mainRes.code === 0) {
                const m = mainRes.data;
                const indItems = (m.industry_main || []).slice(0, 3);
                const conItems = (m.concept_main || []).slice(0, 3);
                const allMains = [...indItems, ...conItems].sort((a, b) => a.main_score - b.main_score).slice(0, 5);
                
                if (allMains.length > 0) {
                    document.getElementById('main-sectors').innerHTML = allMains.map(r =>
                        '<div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid var(--border-light)">' +
                        '<div><span style="font-weight:600">' + r.name + '</span><span style="font-size:10px;margin-left:4px;color:#f59e0b">' + (r.main_level || '') + '</span></div>' +
                        '<div style="text-align:right"><span style="color:var(--color-up);font-weight:600">+' + r.change_pct + '%</span>' +
                        '<div style="font-size:10px;color:var(--text-muted)">资金+' + r.fund_flow_yi.toFixed(1) + '亿</div></div></div>'
                    ).join('');
                } else {
                    document.getElementById('main-sectors').innerHTML = '<span style="color:var(--text-muted)">暂无符合条件的主线板块</span>';
                }
            }
        } catch(e) {
            document.getElementById('main-sectors').innerHTML = '<span style="color:var(--text-muted)">加载中...</span>';
        }
        
        // 5. 资金流向 TOP10
        try {
            const fundRes = await api.get('/fund/sector?type=all&top_n=10');
            if (fundRes.code === 0 && fundRes.data.top_inflow && fundRes.data.top_inflow.length > 0) {
                document.getElementById('fund-flow-top').innerHTML = fundRes.data.top_inflow.slice(0, 10).map(r =>
                    '<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid var(--border-light)">' +
                    '<span style="font-weight:600">' + r.name + '</span>' +
                    '<div style="text-align:right"><span style="color:' + (r.net_flow_yi >= 0 ? 'var(--color-up)' : 'var(--color-down)') + ';font-weight:600">' + r.net_flow_str + '</span>' +
                    '<span style="font-size:10px;color:var(--text-muted);margin-left:6px">' + (r.change_pct >= 0 ? '+' : '') + r.change_pct + '%</span></div></div>'
                ).join('');
            } else {
                document.getElementById('fund-flow-top').innerHTML = '<span style="color:var(--text-muted)">暂无数据</span>';
            }
        } catch(e) {
            document.getElementById('fund-flow-top').innerHTML = '<span style="color:var(--text-muted)">加载中...</span>';
        }
    } catch(e) {
        console.error('市场数据加载失败:', e);
    }
}

async function renderSectorRanking(elementId, sectorType) {
    try {
        const res = await api.get('/sector/ranking?type=' + sectorType + '&top_n=10');
        if (res.code === 0 && res.data.top && res.data.top.length > 0) {
            document.getElementById(elementId).innerHTML = res.data.top.map(r =>
                '<div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid var(--border-light)">' +
                '<div style="flex:1;min-width:0">' +
                '<span style="font-weight:600">' + r.name + '</span>' +
                '<div style="font-size:10px;color:var(--text-muted)">领涨:' + (r.leader_name || '--') + ' ' + (r.fund_flow_str || '') + '</div></div>' +
                '<span style="color:' + (r.change_pct >= 0 ? 'var(--color-up)' : 'var(--color-down)') + ';font-weight:700;margin-left:8px">' + (r.change_pct >= 0 ? '+' : '') + r.change_pct + '%</span></div>'
            ).join('');
        } else {
            document.getElementById(elementId).innerHTML = '<span style="color:var(--text-muted)">暂无数据，请检查网络</span>';
        }
    } catch(e) {
        console.error('板块数据加载失败:', e);
        document.getElementById(elementId).innerHTML = '<span style="color:var(--text-muted)">加载失败</span>';
    }
}

// 侧边栏导航
document.addEventListener('click', e => {
    const item = e.target.closest('.sidebar-item[data-view]');
    if (!item) return;
    const view = item.getAttribute('data-view');
    document.querySelectorAll('.sidebar-item').forEach(el => el.classList.remove('active'));
    item.classList.add('active');
    if (view === 'all') { State.currentView = 'all'; State.currentGroupId = null; document.getElementById('view-title').textContent = '全部股票'; showStockView(); renderStockTable(); }
    else if (view === 'alerts') showAlertLogs();
    else if (view === 'rules') showRulesView();
    else if (view === 'market') showMarketView();
    else if (view === 'ai') showAIView();
    else if (view === 'ai-picks') showAIPicksView();
});

// ====== AI 智能选股 ======
function showAIView() {
    document.getElementById('stock-panel').style.display = 'none';
    document.getElementById('rules-panel').style.display = 'none';
    document.getElementById('market-panel').style.display = 'none';
    document.getElementById('ai-panel').style.display = '';
    document.getElementById('ai-picks-panel').style.display = 'none';
    document.getElementById('alert-logs-panel').style.display = 'none';
    document.getElementById('view-title').textContent = 'AI 智能选股';
    document.getElementById('toolbar-stock').style.display = 'none';
    document.getElementById('stock-table').style.display = 'none';
}

// ====== AI 选股记录 ======
function showAIPicksView() {
    document.getElementById('stock-panel').style.display = 'none';
    document.getElementById('rules-panel').style.display = 'none';
    document.getElementById('market-panel').style.display = 'none';
    document.getElementById('ai-panel').style.display = 'none';
    document.getElementById('ai-picks-panel').style.display = '';
    document.getElementById('alert-logs-panel').style.display = 'none';
    document.getElementById('view-title').textContent = 'AI选股记录';
    document.getElementById('toolbar-stock').style.display = 'none';
    loadAIPickDates();
}

async function loadAIPickDates() {
    const dateList = document.getElementById('ai-picks-date-list');
    dateList.innerHTML = '<div style="padding:12px;text-align:center;color:var(--text-muted);font-size:13px">加载中...</div>';
    
    try {
        const resp = await fetch(`${API_BASE}/ai/picks/dates`, {
            headers: { 'Authorization': `Bearer ${api.token}` }
        });
        const data = await resp.json();
        if (data.code !== 0 || !data.data || data.data.length === 0) {
            dateList.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:13px">暂无选股记录<br><small>使用「一键选股」生成推荐后自动记录</small></div>';
            return;
        }
        
        // 构建日期→会话两级列表
        dateList.innerHTML = data.data.map((dateGroup, di) => {
            const sessionsHtml = dateGroup.sessions.map((s, si) => `
                <div class="ai-pick-session-item${di === 0 && si === 0 ? ' active' : ''}" 
                     onclick="event.stopPropagation(); loadAIPicksByDate('${dateGroup.date}', '${s.session}', this, '${s.time}')"
                     data-session="${s.session}" data-date="${dateGroup.date}">
                    <span style="font-size:12px">🕐 ${s.time}</span>
                    <span style="font-size:10px;color:var(--text-muted);margin-left:4px">${s.count}只</span>
                </div>
            `).join('');
            
            // 删除功能：点击日期右侧的删除按钮
            const delBtn = `<span style="cursor:pointer;font-size:12px;opacity:0.5;float:right" 
                onclick="event.stopPropagation(); deleteAIPickDate('${dateGroup.date}', event)" title="删除该日所有记录">🗑</span>`;
            
            return `
            <div class="ai-pick-date-group" style="margin-bottom:4px">
                <div class="ai-pick-date-header" style="padding:8px 12px;font-weight:600;font-size:13px;background:var(--bg-hover);display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid var(--primary)">
                    <span>📅 ${dateGroup.date}</span>
                    <span style="display:flex;align-items:center;gap:8px">
                        <span style="font-size:10px;color:var(--text-muted)">${dateGroup.sessions.length}次选股·${dateGroup.total_count}只</span>
                        <span style="cursor:pointer;font-size:12px;opacity:0.4;transition:opacity 0.2s" 
                            onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.4'"
                            onclick="event.stopPropagation(); deleteAIPickDate('${dateGroup.date}', event)" title="删除该日所有记录">🗑</span>
                    </span>
                </div>
                <div class="ai-pick-sessions" style="padding:2px 0">
                    ${sessionsHtml}
                </div>
            </div>`;
        }).join('');
        
        // 自动加载最新一次选股
        if (data.data.length > 0 && data.data[0].sessions.length > 0) {
            const firstSession = data.data[0].sessions[0];
            loadAIPicksByDate(data.data[0].date, firstSession.session, 
                dateList.querySelector('.ai-pick-session-item'), firstSession.time);
        }
    } catch (e) {
        dateList.innerHTML = '<div style="padding:12px;text-align:center;color:var(--danger);font-size:13px">加载失败: ' + e.message + '</div>';
    }
}

async function loadAIPicksByDate(dateStr, sessionId, sessionItemEl, timeLabel) {
    // 高亮选中的会话
    document.querySelectorAll('.ai-pick-session-item').forEach(el => el.classList.remove('active'));
    if (sessionItemEl) sessionItemEl.classList.add('active');
    
    const detail = document.getElementById('ai-picks-detail');
    detail.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:40px 0">加载中...</div>';
    
    try {
        // 用 session 参数精确匹配
        const encodedSession = encodeURIComponent(sessionId);
        const resp = await fetch(`${API_BASE}/ai/picks?session=${encodedSession}`, {
            headers: { 'Authorization': `Bearer ${api.token}` }
        });
        const data = await resp.json();
        if (data.code !== 0 || !data.data || data.data.length === 0) {
            detail.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:80px 0">暂无记录</div>';
            return;
        }
        
        const picks = data.data;
        const total = picks.reduce((s, p) => s + p.score, 0);
        const avg = Math.round(total / picks.length);
        const displayTime = timeLabel || sessionId.substring(11, 19);
        
        detail.innerHTML = `
            <div style="margin-bottom:16px">
                <h3 style="font-size:15px;font-weight:600;margin:0 0 4px 0">📅 ${dateStr} 🕐 ${displayTime} AI选股</h3>
                <div style="font-size:12px;color:var(--text-muted)">共 ${picks.length} 只推荐 | 平均评分 ${avg} 分</div>
            </div>
            <div style="display:grid;gap:12px">
                ${picks.map((p, i) => {
                    const snapshot = p.rec_price ? `推荐时: ¥${p.rec_price.toFixed(2)} ${p.rec_change_pct >= 0 ? '+' : ''}${p.rec_change_pct.toFixed(2)}%` : '';
                    const entryInfo = p.entry_price ? `💰 买点: ${p.entry_price}` : '';
                    const volumeInfo = p.rec_volume ? `量: ${(p.rec_volume/10000).toFixed(0)}万手` : '';
                    return `
                    <div class="ai-pick-card">
                        <div class="ai-pick-card-header">
                            <div class="ai-pick-rank">#${i + 1}</div>
                            <div class="ai-pick-stock-info">
                                <span class="ai-pick-name">${p.name}</span>
                                <span class="ai-pick-code">${p.code}</span>
                            </div>
                            <div class="ai-pick-score" style="background:${p.score >= 80 ? '#22c55e' : p.score >= 70 ? '#3b82f6' : p.score >= 60 ? '#f59e0b' : '#6b7280'}">
                                ${p.score}分
                            </div>
                        </div>
                        <div class="ai-pick-reason">💡 ${p.reason}</div>
                        <div class="ai-pick-meta">
                            ${entryInfo ? '<span class="ai-pick-meta-item ai-pick-meta-buy" style="font-weight:600">' + entryInfo + '</span>' : ''}
                            <span class="ai-pick-meta-item">⏱ 持有 ${p.hold_days} 天</span>
                            <span class="ai-pick-meta-item">🛑 止损 ${p.stop_loss}</span>
                            <span class="ai-pick-meta-item">🎯 目标 ${p.target}</span>
                            ${snapshot ? '<span class="ai-pick-meta-item">📊 ' + snapshot + '</span>' : ''}
                            ${volumeInfo ? '<span class="ai-pick-meta-item">' + volumeInfo + '</span>' : ''}
                        </div>
                    </div>
                    `;
                }).join('')}
            </div>
        `;
    } catch (e) {
        detail.innerHTML = '<div style="text-align:center;color:var(--danger);padding:40px 0">加载失败: ' + e.message + '</div>';
    }
}

// 删除指定日期的所有选股记录
async function deleteAIPickDate(dateStr, event) {
    if (event) {
        event.stopPropagation();
        event.preventDefault();
    }
    if (!confirm(`确定要删除 ${dateStr} 全部选股记录吗？\n此操作不可恢复。`)) return;
    
    try {
        const resp = await fetch(`${API_BASE}/ai/picks?date=${dateStr}`, {
            method: 'DELETE',
            headers: { 'Authorization': `Bearer ${api.token}` }
        });
        if (resp.ok) {
            loadAIPickDates(); // 刷新列表
        }
    } catch (e) {
        alert('删除失败: ' + e.message);
    }
}

async function aiQuickPick() {
    const btn = document.getElementById('btn-quick-pick');
    const loading = document.getElementById('ai-loading');
    const result = document.getElementById('ai-result');
    
    btn.disabled = true;
    btn.textContent = '⏳ AI 分析中...';
    loading.style.display = 'block';
    result.innerHTML = '';
    
    try {
        const res = await api.post('/ai/quick-pick');
        loading.style.display = 'none';
        btn.textContent = '⚡ 一键选股 — AI 自动分析市场，推荐8只短线标的';
        btn.disabled = false;
        
        if (res.code === 0 && res.data.success) {
            renderAIQuickPickResult(res.data);
            // 自动刷新侧边栏分组（选股时会自动创建AI分组）
            if (res.data.group_id) {
                try {
                    State.groups = await api.getGroups();
                    renderGroups();
                } catch (e) { /* 分组刷新失败不影响主流程 */ }
            }
        } else {
            result.innerHTML = '<div style="color:var(--danger);padding:16px;text-align:center">' +
                'AI 分析失败: ' + ((res.data && res.data.error) || '未知错误') + '</div>';
        }
    } catch (e) {
        loading.style.display = 'none';
        btn.textContent = '⚡ 一键选股 — AI 自动分析市场，推荐8只短线标的';
        btn.disabled = false;
        result.innerHTML = '<div style="color:var(--danger);padding:16px;text-align:center">网络请求失败: ' + e.message + '</div>';
    }
}

function renderAIQuickPickResult(data) {
    const summary = data.summary || '';
    const stocks = data.stocks || [];
    const saved = data.saved || 0;
    const groupName = data.group_name || '';
    const groupStocks = data.group_stocks || 0;
    
    let html = '<div style="margin-bottom:16px;padding:12px 16px;background:var(--bg-tertiary);border-radius:var(--radius);font-size:13px;color:var(--text-secondary)">' +
        '<span style="font-weight:600;color:var(--text-primary)">市场策略: </span>' + summary;
    if (saved > 0) {
        html += ' <span style="font-size:11px;color:var(--success)">(已记录📋)</span>';
    }
    if (groupName) {
        html += '<br><span style="font-size:11px;color:var(--primary)">📁 已自动创建分组「' + groupName + '」(' + groupStocks + '只)</span>';
        html += ' <span style="font-size:10px;color:var(--text-muted)">—— 在左侧分组中查看实时涨跌，便于复盘</span>';
    }
    html += '</div>';
    
    html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">';
    stocks.forEach((s, i) => {
        const scoreColor = s.score >= 80 ? 'var(--color-up)' : s.score >= 60 ? 'var(--warning)' : 'var(--text-muted)';
        const entryInfo = s.entry_price ? '💰 买点: ' + s.entry_price + '' : '';
        html += '<div style="background:var(--bg-tertiary);border-radius:var(--radius);padding:14px;border-left:3px solid ' + scoreColor + '">' +
            '<div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:8px">' +
            '<div><span style="font-weight:700;font-size:15px">#' + (i+1) + ' ' + s.name + '</span><span style="color:var(--text-muted);font-size:11px;margin-left:6px">' + s.code + '</span></div>' +
            '<span style="font-size:12px;font-weight:700;color:' + scoreColor + '">' + s.score + '分</span></div>' +
            '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:10px;line-height:1.5">' + (s.reason || '') + '</div>' +
            '<div style="display:flex;gap:12px;font-size:11px;color:var(--text-muted);flex-wrap:wrap">' +
            (entryInfo ? '<span style="font-weight:600;color:var(--primary)">' + entryInfo + '</span>' : '') +
            '<span>⏱ ' + (s.hold_days || '?') + '天</span>' +
            '<span style="color:var(--color-down)">🛑 ' + (s.stop_loss || '?') + '</span>' +
            '<span style="color:var(--color-up)">🎯 ' + (s.target || '?') + '</span>' +
            '</div></div>';
    });
    html += '</div>';
    
    document.getElementById('ai-result').innerHTML = html;
}

async function aiScreen() {
    const input = document.getElementById('ai-query-input');
    const query = input.value.trim();
    if (!query) return;
    
    const result = document.getElementById('ai-result');
    const loading = document.getElementById('ai-loading');
    loading.style.display = 'block';
    result.innerHTML = '';
    input.disabled = true;
    
    try {
        const res = await api.post('/ai/screen', { query });
        loading.style.display = 'none';
        input.disabled = false;
        
        if (res.code === 0 && res.data.success) {
            let html = '<div style="background:var(--bg-tertiary);border-radius:var(--radius);padding:16px;margin-bottom:12px;font-size:13px;line-height:1.7;white-space:pre-wrap">' +
                (res.data.answer || '') + '</div>';
            
            if (res.data.stocks && res.data.stocks.length > 0) {
                html += '<div style="font-size:12px;font-weight:600;margin-bottom:8px">推荐标的:</div>';
                res.data.stocks.forEach((s, i) => {
                    html += '<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;margin-bottom:4px;background:var(--bg-tertiary);border-radius:4px;font-size:12px">' +
                        '<span style="font-weight:600">' + s.name + '(' + s.code + ')</span>' +
                        '<span style="color:var(--text-secondary);max-width:300px;text-align:right">' + (s.reason || '') + '</span></div>';
                });
            }
            result.innerHTML = html;
        } else {
            result.innerHTML = '<div style="color:var(--danger);padding:16px;text-align:center">AI 分析失败</div>';
        }
    } catch (e) {
        loading.style.display = 'none';
        input.disabled = false;
        result.innerHTML = '<div style="color:var(--danger);padding:16px;text-align:center">请求失败: ' + e.message + '</div>';
    }
}

// 退出
async function doLogout() {
    await api.logout();
    State.user = null;
    State.stocks = [];
    State.quotes = {};
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
    if (socket) { socket.disconnect(); socket = null; }
    Charts.disposeAll();
    showLogin();
}

// ====== 迷你走势图（sparkline）======
function renderSparkline(code) {
    const history = State.priceSparkline[code];
    if (!history || history.length < 2) return '';
    
    const w = 50, h = 16, pad = 2;
    const min = Math.min(...history);
    const max = Math.max(...history);
    const range = (max - min) || 1;
    
    const points = history.map((v, i) => {
        const x = pad + (i / (history.length - 1)) * (w - pad * 2);
        const y = h - pad - ((v - min) / range) * (h - pad * 2);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    
    const color = history[history.length - 1] >= 0 ? 'var(--color-up)' : 'var(--color-down)';
    const fillColor = history[history.length - 1] >= 0
        ? (document.documentElement.getAttribute('data-theme') === 'dark' ? '#ef444422' : '#e0313122')
        : (document.documentElement.getAttribute('data-theme') === 'dark' ? '#22c55e22' : '#2f9e4422');
    
    // 构建填充区域
    const firstX = pad;
    const lastX = w - pad;
    const fillPoints = `${firstX},${h - pad} ${points} ${lastX},${h - pad}`;
    
    return `<svg width="${w}" height="${h}" style="vertical-align:middle;margin-left:4px" viewBox="0 0 ${w} ${h}">
        <polygon points="${fillPoints}" fill="${fillColor}"/>
        <polyline points="${points}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
}

// 快捷键
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { closeDetail(); document.querySelectorAll('.modal-overlay').forEach(m => { m.style.display = 'none'; }); }
    if ((e.ctrlKey || e.metaKey) && e.key === 'f') { e.preventDefault(); document.getElementById('table-search')?.focus(); }
});
