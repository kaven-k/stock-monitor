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
};

let socket = null;
let countdownTimer = null;  // 倒计时定时器

// ====== 入口 ======
document.addEventListener('DOMContentLoaded', async () => {
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
        if (status.interval) State.refreshInterval = status.interval;

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
            document.getElementById('last-update-time').textContent = `数据更新: ${data.time}`;
            document.getElementById('status-time').textContent = data.time;
            renderStockTable();
            if (State.selectedStock && !document.getElementById('detail-panel').classList.contains('collapsed')) {
                updateDetailPanel();
            }
        }
    });
    socket.on('alerts_new', data => {
        if (data.alerts?.length > 0) {
            showAlertPopup(data.alerts);
            updateNavCounts();
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
            <td class="${cc}" style="font-weight:600">${sign}${q.change_pct.toFixed(2)}%</td>
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
async function selectStock(code) {
    State.selectedStock = code;
    State.klineData = null;
    document.getElementById('detail-panel').classList.remove('collapsed');
    updateDetailPanel();
    try {
        State.klineData = await api.getKline(code);
        if (State.detailTab === 'chart') renderKlineChart();
        else if (State.detailTab === 'indicator') renderIndicatorCharts();
    } catch (e) { console.error(e); }
    renderStockTable();
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
    const stock = State.stocks.find(s => s.code === code);
    const name = q?.name || stock?.name || code;

    document.getElementById('detail-name').textContent = name;
    document.getElementById('detail-code').textContent = code;

    const container = document.getElementById('detail-content');
    if (!container || !q) return;

    const cc = q.change_pct > 0 ? 'change-up' : (q.change_pct < 0 ? 'change-down' : 'change-flat');
    const sign = q.change_pct > 0 ? '+' : '';
    const pc = q.change_pct > 0 ? 'var(--color-up)' : (q.change_pct < 0 ? 'var(--color-down)' : 'var(--text-primary)');

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
        html += '<div class="chart-container" id="kline-chart"></div><div class="indicator-legend"><div class="legend-item"><div class="legend-line" style="background:#e03131"></div>MA5</div><div class="legend-item"><div class="legend-line" style="background:#f59e0b"></div>MA10</div><div class="legend-item"><div class="legend-line" style="background:#6366f1"></div>MA20</div><div class="legend-item"><div class="legend-line" style="background:#10b981"></div>MA60</div></div><div id="ma-signals"></div>';
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

function switchDetailTab(tab, el) {
    State.detailTab = tab;
    document.querySelectorAll('.detail-tab').forEach(t => t.classList.remove('active'));
    if (el) el.classList.add('active');
    updateDetailPanel();
}

function renderKlineChart() {
    const d = State.klineData;
    if (!d?.kline) return;
    Charts.renderKline('kline-chart', d.kline, d.indicators || {});
    
    // MA 买卖信号分析
    const analysis = Charts.analyzeMA(d.kline, d.indicators || {});
    const signalEl = document.getElementById('ma-signals');
    if (signalEl && analysis) {
        signalEl.innerHTML = `
            <div style="padding:12px 16px;border-radius:8px;margin:8px 0;background:${analysis.suggestion.color === '#e03131' ? '#fff5f5' : analysis.suggestion.color === '#2f9e44' ? '#f0fff4' : '#f8f9fa'};border:1px solid ${analysis.suggestion.color}20">
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
                    <span style="font-weight:700;font-size:15px;color:${analysis.suggestion.color}">${analysis.suggestion.text}</span>
                    <span style="font-size:12px;color:var(--text-muted)">${analysis.suggestion.desc}</span>
                </div>
                ${analysis.signals.map(s => `<div style="display:flex;align-items:center;gap:6px;margin:4px 0;font-size:12px">
                    <span style="color:${s.type === 'bullish' ? '#e03131' : s.type === 'bearish' ? '#2f9e44' : '#868e96'}">${s.type === 'bullish' ? '▲' : s.type === 'bearish' ? '▼' : '◆'}</span>
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
    const item = document.querySelector(`.sidebar-item[data-group-id="${groupId}"]`) || document.querySelector('.sidebar-item[data-view="all"]');
    if (item) item.classList.add('active');
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
    closeDetail();  // 关闭详情面板
    document.getElementById('modal-alert-logs').style.display = 'flex';
    try {
        const data = await api.getAlertLogs(100);
        const c = document.getElementById('alert-logs-content');
        c.innerHTML = data.logs.length ? data.logs.map(log => `<div class="alert-item ${log.is_read ? '' : 'unread'}" onclick="markAlertRead(${log.id})"><div class="alert-item-header"><span class="alert-item-type ${log.alert_type}">${log.alert_type}</span><span class="alert-item-time">${log.triggered_at}</span></div><div class="alert-item-msg">${log.alert_msg}</div><div style="font-size:11px;color:var(--text-muted);margin-top:4px">${log.rule_name} | ${log.stock_name}(${log.stock_code})</div></div>`).join('') : '<div class="empty-state"><div class="text">暂无预警记录</div></div>';
        updateNavCounts();
    } catch (e) { console.error(e); }
}

async function markAlertRead(id) { try { await api.markAlertRead(id); } catch (e) {} }

async function updateNavCounts() {
    document.getElementById('nav-all-count').textContent = State.stocks.length;
    document.getElementById('nav-rules-count').textContent = State.alertRules.length;
    renderGroups();
    try {
        const data = await api.getAlertLogs(1);
        const ac = document.getElementById('nav-alert-count');
        ac.textContent = data.unread || 0;
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

// ====== 视图切换 ======
function showStockView() {
    document.getElementById('stock-panel').style.display = '';
    document.getElementById('rules-panel').style.display = 'none';
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

async function showRulesView() {
    closeDetail();  // 关闭详情面板
    State.currentView = 'rules';
    document.getElementById('view-title').textContent = '规则管理';
    document.getElementById('stock-panel').style.display = 'none';
    document.getElementById('rules-panel').style.display = '';
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
});

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

// 快捷键
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { closeDetail(); document.querySelectorAll('.modal-overlay').forEach(m => { m.style.display = 'none'; }); }
    if ((e.ctrlKey || e.metaKey) && e.key === 'f') { e.preventDefault(); document.getElementById('table-search')?.focus(); }
});
