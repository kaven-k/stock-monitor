"""
股票监控系统 - 配置文件
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============ 加载 .env 文件 ============

def _load_dotenv():
    """简单的 .env 文件加载器（无需 python-dotenv）"""
    # 尝试项目根目录的 .env
    for env_path in [
        os.path.join(BASE_DIR, "..", ".env"),
        os.path.join(BASE_DIR, ".env"),
    ]:
        env_path = os.path.normpath(env_path)
        if os.path.isfile(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, _, val = line.partition("=")
                        key = key.strip()
                        val = val.strip().strip('"').strip("'")
                        if key and key not in os.environ:
                            os.environ[key] = val
            break

_load_dotenv()

# ============ 数据库 ============

# ============ 数据库
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

# 飞书通知
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_USER_ID = os.environ.get("FEISHU_USER_ID", "")

# DeepSeek AI 选股
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# 接口限流 (每分钟最大请求数)
RATE_LIMIT_PER_MINUTE = 100
