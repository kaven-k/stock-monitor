# StockMonitor v2.0

A股实时监控系统 - 前后端分离架构

## 功能

- 实时行情监控（腾讯财经 API）
- K线烛形图 + 技术指标（MA/MACD/RSI/KDJ/BOLL）+ MA买卖信号
- 13种预警规则引擎 + 飞书机器人通知
- 股票分组/标签管理（自动打标签 + 手动编辑）
- JWT 用户认证
- WebSocket 实时数据推送

## 架构

```
stock-monitor/
├── backend/              # Flask REST API
│   ├── app.py            # 入口，注册蓝图
│   ├── auth.py           # JWT 认证
│   ├── api_v1.py         # REST API v1
│   ├── database.py       # SQLite 数据层
│   ├── data_fetcher.py   # 行情/K线抓取
│   ├── alert_engine.py   # 预警规则引擎
│   ├── feishu_notify.py  # 飞书通知
│   ├── stock_tagger.py   # 自动打标签
│   └── config.py         # 配置
├── frontend/             # 独立 SPA
│   ├── index.html
│   ├── css/style.css
│   └── js/
│       ├── api.js        # API 客户端
│       ├── charts.js     # ECharts 图表
│       └── app.js        # 主应用逻辑
└── api-docs.html         # API 文档
```

## 快速启动

### 后端

```bash
cd backend
pip install -r requirements.txt
python app.py
# → http://localhost:5000/api/v1/
```

### 前端

```bash
cd frontend
python -m http.server 8080
# → http://localhost:8080
```

### 飞书通知（可选）

```bash
export FEISHU_APP_ID=your_app_id
export FEISHU_APP_SECRET=your_app_secret
export FEISHU_USER_ID=target_open_id
```

## 数据源

| 数据类型 | 来源 |
|----------|------|
| 实时行情 | 腾讯财经 HTTP API |
| K线数据 | 同花顺 API |
| 股票搜索 | 东方财富搜索接口 |
| 技术指标 | 纯 Python 计算 |

## License

MIT
