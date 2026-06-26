"""
股票监控系统 - 配置文件
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 数据库
DB_PATH = os.path.join(BASE_DIR, "stock_monitor.db")

# JWT 认证
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "stock-monitor-jwt-secret-key-2024-long")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

# 服务器
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", 5000))

# Flask
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "stock-monitor-flask-secret-2024")

# CORS - 前端地址列表
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")

# 监控
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", 5))

# 接口限流 (每分钟最大请求数)
RATE_LIMIT_PER_MINUTE = 100
