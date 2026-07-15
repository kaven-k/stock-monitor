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

# ============ 选股范围配置 ============
# 选股覆盖范围: 全A股市场
#   候选池由「东方财富全市场板块领涨龙头 + 用户监控列表」构成，
#   覆盖全市场主线板块，而非仅限用户监控列表。
# 排除板块: 因当前可用资金有限，不具备部分板块的交易权限，需从候选池与推荐结果中剔除。
SCREENING_CONFIG = {
    # 市场覆盖范围: full_a_share = 全A股(板块龙头 + 监控列表)
    "market_coverage": "full_a_share",
    # 当前可用资金(元): 仅用于说明排除原因，不作硬性计算
    "available_capital": 20000,
    # 需排除的板块代码前缀 -> 排除原因（展示给用户 / AI 提示词）
    # 科创板(688): 需账户资产≥50万且交易经验≥2年
    # 北交所(83/87/88/92/43): 同样需账户资产≥50万门槛
    # 二者当前可用资金约2万元均不具备交易权限。
    # 如已开通某板块交易权限，直接删除对应行即可恢复该板块选股。
    "exclude_boards": {
        "688": "科创板(688xxx)，需账户资产≥50万且交易经验≥2年",
        "300": "创业板(300xxx)，需账户资产≥10万且交易经验≥2年",
        "301": "创业板(301xxx)，需账户资产≥10万且交易经验≥2年",
        "83": "北交所(83xxxx)，需账户资产≥50万门槛",
        "87": "北交所(87xxxx)，需账户资产≥50万门槛",
        "88": "北交所(88xxxx)，需账户资产≥50万门槛",
        "92": "北交所(92xxxx)，需账户资产≥50万门槛",
        "43": "北交所/老三板(43xxxx)，需单独交易权限",
    },
}


def is_excluded_stock(code):
    """判断股票是否因权限/资金约束需从选股范围剔除。

    返回 (bool_excluded, reason_or_None)
    """
    if not code:
        return False, None
    code = str(code).strip()
    for prefix, reason in SCREENING_CONFIG.get("exclude_boards", {}).items():
        if code.startswith(prefix):
            return True, reason
    return False, None


def build_exclusion_prompt():
    """生成嵌入 AI 提示词的排除说明（与 SCREENING_CONFIG 同步，单一数据源）"""
    boards = SCREENING_CONFIG.get("exclude_boards", {})
    if not boards:
        return ""
    lines = ["【选股范围限制·禁止推荐以下板块（用户无交易权限）】"]
    for prefix, reason in boards.items():
        lines.append(f"- {reason}（代码以 {prefix} 开头）")
    cap = SCREENING_CONFIG.get("available_capital")
    if cap:
        lines.append(f"- 当前可用资金约 {cap} 元，不足以开通上述板块交易权限")
    lines.append("⚠️ 严禁推荐上述被排除板块的股票代码！")
    return "\n".join(lines)


# ============ 选股可买性配置 ============
# 控制候选池构建与后置兜底，使选股结果聚焦「可买入 + 有潜力」的标的，
# 而非只追已涨停的龙头（实战中往往买不到）。
SELECTION_CONFIG = {
    "prefer_buyable": True,          # 默认不推荐涨停/接近涨停票
    "limit_up_pct": 9.8,             # 涨停阈值（主板/中小板±10%，取9.8判定封板）
    "near_limit_pct": 7.0,           # 接近涨停阈值（>=7% 视为打板区，买入风险高）
    "sweet_low": 0.5,                # 涨幅甜区下界（已启动未封板）
    "sweet_high": 6.5,               # 涨幅甜区上界（仍有上行空间）
    "vol_ratio_min": 1.2,            # 量比健康下界
    "vol_ratio_max": 3.5,            # 量比健康上界（>3.5 警惕异常巨量）
    "turnover_min": 3.0,             # 换手率健康下界(%)
    "turnover_max": 15.0,            # 换手率健康上界(%)
    "amount_min_wan": 10000,         # 最小日成交额(万)=1亿，排除僵尸股
    "board_watch_max": 2,            # 打板观察区最多保留几只
    "pick_count": 10,                # 一键选股最终推荐数量（主推列表长度）
}


def is_limit_up(change_pct):
    """是否达到/接近涨停（归入打板观察区）"""
    return change_pct >= SELECTION_CONFIG["near_limit_pct"]


def passes_fund_filter(vol_ratio, turnover_pct, amount_wan):
    """资金面健康度初筛（零成本，基于已有行情字段）"""
    c = SELECTION_CONFIG
    vr = vol_ratio or 0
    to = turnover_pct or 0
    amt = amount_wan or 0
    return (c["vol_ratio_min"] <= vr <= c["vol_ratio_max"]
            and c["turnover_min"] <= to <= c["turnover_max"]
            and amt >= c["amount_min_wan"])


def is_buyable_candidate(change_pct, vol_ratio, turnover_pct, amount_wan, tech_score=None):
    """综合判定是否为『可买入候选』：未涨停/未接近涨停 + 资金健康 (+ 技术多头优先)"""
    if is_limit_up(change_pct):
        return False
    if not passes_fund_filter(vol_ratio, turnover_pct, amount_wan):
        return False
    if tech_score is not None and tech_score <= 0:
        return False
    return True
