/**
 * StockMonitor API 客户端
 * 封装所有 REST API 调用，统一处理认证和错误
 */

const API_BASE = 'http://localhost:5000/api/v1';

class ApiClient {
    constructor(baseUrl = API_BASE) {
        this.baseUrl = baseUrl;
        this.token = localStorage.getItem('sm_token') || null;
    }

    // ====== 认证 ======
    setToken(token) {
        this.token = token;
        if (token) {
            localStorage.setItem('sm_token', token);
        } else {
            localStorage.removeItem('sm_token');
        }
    }

    clearToken() {
        this.setToken(null);
    }

    // ====== HTTP 核心 ======
    async request(method, endpoint, body = null) {
        const url = `${this.baseUrl}${endpoint}`;
        const headers = {
            'Content-Type': 'application/json',
        };

        if (this.token) {
            headers['Authorization'] = `Bearer ${this.token}`;
        }

        const opts = { method, headers };
        if (body && method !== 'GET') {
            opts.body = JSON.stringify(body);
        }

        try {
            const res = await fetch(url, opts);
            const data = await res.json();

            if (data.code === 40101 || data.code === 40103 || data.code === 40104) {
                // Token 无效, 触发重新登录
                this.clearToken();
                if (typeof onAuthExpired === 'function') {
                    onAuthExpired();
                }
                throw new ApiError(data.code, data.msg, data.error);
            }

            if (data.code !== 0) {
                throw new ApiError(data.code, data.msg, data.error);
            }

            return data;
        } catch (e) {
            if (e instanceof ApiError) throw e;
            throw new ApiError(0, `网络错误: ${e.message}`, 'NETWORK_ERROR');
        }
    }

    get(endpoint) { return this.request('GET', endpoint); }
    post(endpoint, body) { return this.request('POST', endpoint, body); }
    put(endpoint, body) { return this.request('PUT', endpoint, body); }
    delete(endpoint) { return this.request('DELETE', endpoint); }

    // ====== 认证接口 ======
    async login(username, password) {
        const res = await this.request('POST', '/auth/login', { username, password });
        this.setToken(res.data.token);
        return res.data;
    }

    async register(username, password) {
        const res = await this.request('POST', '/auth/register', { username, password });
        this.setToken(res.data.token);
        return res.data;
    }

    async logout() {
        try { await this.post('/auth/logout'); } catch (e) { /* ignore */ }
        this.clearToken();
    }

    async getMe() {
        return (await this.get('/auth/me')).data;
    }

    // ====== 股票接口 ======
    async getStocks() { return (await this.get('/stocks')).data; }
    async searchStocks(keyword) { return (await this.get(`/stocks/search?keyword=${encodeURIComponent(keyword)}`)).data; }
    async addStock(code, name, market = 'A') { return (await this.post('/stocks', { code, name, market })).data; }
    async removeStock(code) { return (await this.delete(`/stocks/${code}`)).data; }
    async updateStockTags(code, tags) { return (await this.put(`/stocks/${code}/tags`, { tags })); }

    // ====== 分组接口 ======
    async getGroups() { return (await this.get('/groups')).data; }
    async createGroup(name, color = '#3b82f6') { return (await this.post('/groups', { name, color })).data; }
    async deleteGroup(id) { return (await this.delete(`/groups/${id}`)); }
    async updateGroup(id, data) { return (await this.put(`/groups/${id}`, data)); }
    async addStockToGroup(groupId, code) { return (await this.post(`/groups/${groupId}/stocks`, { code })); }
    async removeStockFromGroup(groupId, code) { return (await this.delete(`/groups/${groupId}/stocks/${code}`)); }

    // ====== 预警接口 ======
    async getAlertRules(all = false) { return (await this.get(`/alerts/rules${all ? '?all=1' : ''}`)).data; }
    async createAlertRule(data) { return (await this.post('/alerts/rules', data)).data; }
    async updateAlertRule(id, data) { return (await this.put(`/alerts/rules/${id}`, data)); }
    async deleteAlertRule(id) { return (await this.delete(`/alerts/rules/${id}`)); }
    async toggleAlertRule(id) { return (await this.post(`/alerts/rules/${id}/toggle`)); }
    async getAlertLogs(limit = 50) { return (await this.get(`/alerts/logs?limit=${limit}`)).data; }
    async markAlertRead(logId) { return (await this.post(`/alerts/logs/${logId}/read`)); }

    // ====== 行情接口 ======
    async getQuotes(codes = '') {
        const url = codes ? `/quotes?codes=${codes}` : '/quotes';
        return (await this.get(url)).data;
    }
    async getKline(code, period = 'day', count = 250) {
        return (await this.get(`/kline/${code}?period=${period}&count=${count}`)).data;
    }

    // ====== 监控接口 ======
    async getMonitorStatus() { return (await this.get('/monitor/status')).data; }
    async startMonitor() { return (await this.post('/monitor/start')); }
    async stopMonitor() { return (await this.post('/monitor/stop')); }
    async setMonitorInterval(interval) { return (await this.post('/monitor/interval', { interval })); }
}

class ApiError extends Error {
    constructor(code, message, errorCode) {
        super(message);
        this.code = code;
        this.errorCode = errorCode;
    }
}

// 全局回调 - 认证过期时触发
let onAuthExpired = null;

// 全局 API 实例
const api = new ApiClient();
