# StockMonitor v2.0 (feat/v3)

A股实时监控系统 — 前后端分离 + AI 智能选股

## 功能

### 核心监控
- 实时行情监控（腾讯财经 API + WebSocket 推送）
- K线烛形图 + 技术指标（MA/MACD/RSI/KDJ/BOLL）
- 股票分组/标签管理（自动打标签 + 手动编辑）
- JWT 用户认证 + Token 黑名单

### AI 智能选股 (v3.0)
- **一键选股**：AI 自动分析全市场，综合技术面+资金面+板块面推荐 8 只短线标的
- **对话选股**：自然语言提问（如"推荐半导体板块短线标的"）
- **选股记录**：按时间戳分组落库，含推荐时行情快照，支持复盘对比
- **自动分组**：选股结束后自动创建同名分组（🤖 AI 2026/07/01 14:30:25），侧边栏直接查看实时涨跌

### 预警引擎
- **13 种预警规则**：价格/涨跌/成交量/换手/振幅/突破/连续涨跌/量比/涨停跌停
- **复合预警** (v3.0)：AND/OR 组合 11 种子规则
- **多指标共振信号** (v3.0)：MA/MACD/RSI/BOLL/VWMA/ATR 加权评分 (-100~+100)
- 飞书机器人通知（可选）
- 预警记录内嵌面板展示 + 一键已读

### 市场概览 (v3.0)
- **全市场情绪温度计**：恐慌贪婪指数（全市场 5534 只A股统计）
- 涨跌分布 / 涨跌停统计 / 三大指数
- 行业+概念板块排名 TOP10
- 主线板块分析（资金+涨幅双排序）

## 架构

```
stock-monitor/
├── backend/                # Flask REST API
│   ├── app.py              # 入口，注册蓝图 + SocketIO
│   ├── auth.py             # JWT 认证
│   ├── api_v1.py           # REST API v1（全部端点）
│   ├── database.py         # SQLite 数据层（8张表）
│   ├── data_fetcher.py     # 行情/K线抓取 + VWMA/ATR 计算
│   ├── alert_engine.py     # 预警规则引擎（13+ 复合规则）
│   ├── signal_engine.py    # 多指标共振信号引擎 (v3.0)
│   ├── market_sentiment.py # 全市场情绪计算 (v3.0)
│   ├── ai_screener.py      # AI 智能选股核心 (v3.0)
│   ├── feishu_notify.py    # 飞书通知
│   ├── stock_tagger.py     # 自动打标签
│   ├── monitor_state.py    # 监控运行状态管理
│   └── config.py           # 配置
├── frontend/               # 独立 SPA
│   ├── index.html
│   ├── css/style.css
│   └── js/
│       ├── api.js          # API 客户端（自动跟随部署端口）
│       ├── charts.js       # ECharts K线图
│       └── app.js          # 主应用逻辑
├── AI_STRATEGY.md          # AI 选股策略说明
├── TUTORIAL.html           # 使用教程（可本地打开）
└── api-docs.html           # API 文档
```

## 快速启动

```bash
# 克隆项目
git clone https://github.com/kaven-k/stock-monitor.git
cd stock-monitor

# 安装依赖
cd backend
pip install -r requirements.txt

# 启动（后端 + 前端一起，端口 5000）
python app.py
# → 浏览器打开 http://localhost:5000
# 默认账号: admin / admin123
```

### 如需分开部署

```bash
# 后端
cd backend && python app.py          # → :5000

# 前端（可选，单独开发调试）
cd frontend && python -m http.server 8080  # → :8080
```

### 飞书通知（可选）

```bash
export FEISHU_APP_ID=your_app_id
export FEISHU_APP_SECRET=your_app_secret
export FEISHU_USER_ID=target_open_id
```

## 数据库

SQLite 本地存储（`backend/stock_monitor.db`），零配置，自动建表。

8 张表：`stocks` / `stock_groups` / `group_members` / `alert_rules` / `alert_logs` / `price_history` / `price_snapshots` / `ai_picks`

## 数据源

| 数据类型 | 来源 | 备注 |
|----------|------|------|
| 实时行情 | 腾讯财经 HTTP API | 全市场延迟 3-5s |
| K线数据 | mootdx（优先）/ 百度股市通（备选） | 日/周/月线 |
| 全市场情绪 | 东方财富 push2 API | 5534只A股分页并行遍历 |
| 板块排名 | 东方财富 emdatah5 API | 行业+概念 TOP10 |
| 资金流向 | 东方财富资金流向 API | 板块主力净流入 |
| 股票搜索 | 东方财富搜索接口 | — |
| 三大指数 | 新浪财经 API | 上证/深证/创业板 |
| 技术指标 | 纯 Python 计算 | MA/MACD/RSI/KDJ/BOLL/VWMA/ATR |

## License

MIT
