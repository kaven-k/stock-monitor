"""
业务 API v1 蓝图
/api/v1/stocks/   - 股票 CRUD
/api/v1/groups/   - 分组管理
/api/v1/alerts/   - 预警规则 & 日志
/api/v1/quotes/   - 实时行情
/api/v1/kline/    - K线 & 技术指标
/api/v1/monitor/  - 监控控制
"""
import json
import os
import sys
import time
import threading
from datetime import datetime
from flask import Blueprint, request, jsonify, g

# 添加 backend 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database as db
from data_fetcher import fetch_tencent_quotes, fetch_kline, get_technical_indicators, search_stock, sync_kline_to_db
from alert_engine import AlertEngine
from auth import login_required
from config import REFRESH_INTERVAL
import monitor_state
import feishu_notify
import stock_tagger

api_v1 = Blueprint('api_v1', __name__, url_prefix='/api/v1')

alert_engine = AlertEngine(db)

# 监控状态
monitor_running = False
monitor_thread = None
last_quotes = {}


# ============ 统一响应函数 ============

def success(data=None, msg="ok"):
    resp = {"code": 0, "msg": msg}
    if data is not None:
        resp["data"] = data
    return jsonify(resp)

def error(code, msg, error_code="ERROR", status=400):
    return jsonify({"code": code, "msg": msg, "error": error_code}), status


# ============ 股票管理 ============

@api_v1.route('/stocks', methods=['GET'])
@login_required
def get_stocks():
    """获取所有股票"""
    stocks = db.get_all_stocks()
    return success(stocks)


@api_v1.route('/stocks/search', methods=['GET'])
@login_required
def search_stocks():
    """搜索股票"""
    keyword = request.args.get('keyword', '')
    if not keyword:
        return error(40001, "请输入搜索关键词", "VALIDATION_ERROR")
    results = search_stock(keyword)
    return success(results)


@api_v1.route('/stocks', methods=['POST'])
@login_required
def add_stock():
    """添加股票"""
    data = request.get_json()
    if not data:
        return error(40001, "请提供JSON数据", "BAD_REQUEST")
    
    code = data.get('code', '').strip()
    name = data.get('name', '').strip()
    market = data.get('market', 'A')
    
    if not code:
        return error(40002, "股票代码不能为空", "VALIDATION_ERROR")
    if not name:
        return error(40003, "股票名称不能为空", "VALIDATION_ERROR")
    
    success_ = db.add_stock(code, name, market)
    if not success_:
        return error(50001, "添加失败，可能已存在", "DB_ERROR")
    
    # 自动打标签
    auto_tags = stock_tagger.auto_tag_stock(code, name)
    if auto_tags:
        db.update_stock_tags(code, auto_tags)
    
    # 异步同步K线
    threading.Thread(target=sync_kline_to_db, args=(code,), daemon=True).start()
    return success({"code": code, "name": name, "market": market, "tags": auto_tags}, "添加成功")


@api_v1.route('/stocks/<code>', methods=['DELETE'])
@login_required
def delete_stock(code):
    """删除股票"""
    db.remove_stock(code)
    return success({"code": code}, "删除成功")


@api_v1.route('/stocks/retag', methods=['POST'])
@login_required
def retag_all_stocks():
    """重新为所有股票打标签"""
    stocks = db.get_all_stocks()
    count = 0
    for s in stocks:
        tags = stock_tagger.auto_tag_stock(s['code'], s['name'])
        if tags:
            db.update_stock_tags(s['code'], tags)
            count += 1
    return success({"tagged": count, "total": len(stocks)}, f"已为 {count}/{len(stocks)} 只股票重新打标签")


@api_v1.route('/stocks/<code>/tags', methods=['PUT'])
@login_required
def update_tags(code):
    """更新股票标签"""
    data = request.get_json()
    tags = data.get('tags', '')
    db.update_stock_tags(code, tags)
    return success({"tags": tags}, "标签已更新")


# ============ 分组管理 ============

@api_v1.route('/groups', methods=['GET'])
@login_required
def get_groups():
    """获取所有分组"""
    groups = db.get_all_groups()
    return success(groups)


@api_v1.route('/groups', methods=['POST'])
@login_required
def create_group():
    """创建分组"""
    data = request.get_json()
    name = data.get('name', '').strip()
    color = data.get('color', '#3b82f6')
    if not name:
        return error(40002, "分组名不能为空", "VALIDATION_ERROR")
    
    gid = db.create_group(name, color)
    if gid is None:
        return error(50001, "创建失败", "DB_ERROR")
    return success({"id": gid, "name": name, "color": color}, "创建成功")


@api_v1.route('/groups/<int:group_id>', methods=['DELETE'])
@login_required
def delete_group(group_id):
    """删除分组"""
    db.delete_group(group_id)
    return success(msg="删除成功")


@api_v1.route('/groups/<int:group_id>', methods=['PUT'])
@login_required
def update_group(group_id):
    """更新分组"""
    data = request.get_json()
    db.update_group(group_id, name=data.get('name'), color=data.get('color'))
    return success(msg="更新成功")


@api_v1.route('/groups/<int:group_id>/stocks', methods=['POST'])
@login_required
def add_stock_to_group(group_id):
    """股票加入分组"""
    data = request.get_json()
    code = data.get('code', '').strip()
    if not code:
        return error(40002, "股票代码不能为空", "VALIDATION_ERROR")
    
    ok = db.add_to_group(group_id, code)
    return success(msg="添加成功") if ok else error(50001, "添加失败", "DB_ERROR")


@api_v1.route('/groups/<int:group_id>/stocks/<code>', methods=['DELETE'])
@login_required
def remove_stock_from_group(group_id, code):
    """从分组移除股票"""
    db.remove_from_group(group_id, code)
    return success(msg="移除成功")


# ============ 预警规则 ============

@api_v1.route('/alerts/rules', methods=['GET'])
@login_required
def get_alert_rules():
    """获取预警规则 (支持 ?all=1 获取含已禁用的)"""
    include_disabled = request.args.get('all', '0') == '1'
    if include_disabled:
        rules = db.get_all_alert_rules_including_disabled()
    else:
        rules = db.get_all_alert_rules()
    return success(rules)


@api_v1.route('/alerts/rules/<int:rule_id>/toggle', methods=['POST'])
@login_required
def toggle_alert_rule(rule_id):
    """切换预警规则启用/禁用状态"""
    rule = db.get_alert_rule_by_id(rule_id)
    if not rule:
        return error(40401, "规则不存在", "NOT_FOUND")
    new_enabled = 0 if rule.get('enabled') else 1
    db.update_alert_rule(rule_id, enabled=new_enabled)
    return success({"enabled": bool(new_enabled)}, "已启用" if new_enabled else "已禁用")


@api_v1.route('/alerts/rules', methods=['POST'])
@login_required
def create_alert_rule():
    """创建预警规则"""
    data = request.get_json()
    name = data.get('name', '').strip()
    rule_type = data.get('rule_type', '')
    params = data.get('params', {})
    stock_codes = data.get('stock_codes', [])
    notify_feishu = data.get('notify_feishu', 0)
    
    if not name or not rule_type:
        return error(40002, "规则名称和类型不能为空", "VALIDATION_ERROR")
    if not stock_codes:
        return error(40002, "请选择至少一只股票", "VALIDATION_ERROR")
    
    rid = db.create_alert_rule(name, rule_type, params, stock_codes, notify_feishu)
    if rid is None:
        return error(50001, "创建失败", "DB_ERROR")
    return success({"id": rid}, "创建成功")


@api_v1.route('/alerts/rules/<int:rule_id>', methods=['PUT'])
@login_required
def update_alert_rule(rule_id):
    """更新预警规则"""
    data = request.get_json()
    fields = {k: v for k, v in data.items() if v is not None and k != 'stock_codes'}
    db.update_alert_rule(rule_id, **fields)
    # 更新股票关联
    stock_codes = data.get('stock_codes')
    if stock_codes is not None:
        db.update_alert_stocks(rule_id, stock_codes)
    return success(msg="更新成功")


@api_v1.route('/alerts/rules/<int:rule_id>', methods=['DELETE'])
@login_required
def delete_alert_rule(rule_id):
    """删除预警规则"""
    db.delete_alert_rule(rule_id)
    return success(msg="删除成功")


@api_v1.route('/alerts/logs', methods=['GET'])
@login_required
def get_alert_logs():
    """获取预警日志"""
    limit = request.args.get('limit', 50, type=int)
    logs = db.get_recent_alerts(limit)
    unread = db.get_unread_alert_count()
    return success({"logs": logs, "unread": unread})


@api_v1.route('/alerts/logs/<int:log_id>/read', methods=['POST'])
@login_required
def mark_alert_read(log_id):
    """标记预警已读"""
    db.mark_alert_read(log_id)
    return success(msg="已标记")


# ============ 行情数据 ============

@api_v1.route('/quotes', methods=['GET'])
@login_required
def get_quotes():
    """获取实时行情"""
    codes_param = request.args.get('codes', '')
    if codes_param:
        codes = [c.strip() for c in codes_param.split(',') if c.strip()]
    else:
        stocks = db.get_all_stocks()
        codes = [s['code'] for s in stocks]
    
    if not codes:
        return success({"quotes": {}, "time": datetime.now().strftime('%H:%M:%S')})
    
    quotes = fetch_tencent_quotes(codes)
    return success({
        "quotes": quotes,
        "time": datetime.now().strftime('%H:%M:%S'),
        "count": len(quotes),
    })


@api_v1.route('/kline/<code>', methods=['GET'])
@login_required
def get_kline(code):
    """获取K线 & 技术指标"""
    period = request.args.get('period', 'day')
    count = request.args.get('count', 250, type=int)
    
    # 优先从数据库读取（缓存命中要求: 至少有 count 条或至少20条）
    hist = db.get_price_history(code, count)
    if hist and len(hist) >= min(count, 20):
        indicators = get_technical_indicators(hist)
        return success({"kline": hist, "indicators": indicators, "source": "db"})
    
    # 从API获取
    kline = fetch_kline(code, period, count)
    if kline:
        # 保存到数据库缓存
        try:
            records = []
            for item in kline:
                records.append((
                    code, item.get("date", ""),
                    item.get("open", 0), item.get("high", 0),
                    item.get("low", 0), item.get("close", 0),
                    item.get("volume", 0), item.get("amount", 0),
                    item.get("change_pct", 0),
                ))
            db.save_price_history(records)
        except Exception as e:
            print(f"[Kline Cache] 保存失败: {e}")
        
        indicators = get_technical_indicators(kline)
        return success({"kline": kline, "indicators": indicators, "source": "api"})
    
    return error(50002, "获取K线数据失败", "DATA_ERROR")


# ============ 监控控制 ============

@api_v1.route('/monitor/status', methods=['GET'])
@login_required
def monitor_status():
    """获取监控状态"""
    import config
    return success({
        "running": monitor_state.is_running(),
        "interval": config.REFRESH_INTERVAL,
        "last_quotes_count": len(last_quotes),
        "time": datetime.now().strftime('%H:%M:%S'),
    })


@api_v1.route('/monitor/start', methods=['POST'])
@login_required
def monitor_start():
    """启动监控"""
    global monitor_running
    if monitor_state.is_running():
        return success(msg="监控已在运行中")
    
    # 重新启动监控循环（引用 app.py 的 exec_loop_with_socketio）
    import app as app_module
    monitor_state.start_monitor(app_module.exec_loop_with_socketio, app_module.socketio)
    monitor_running = True
    return success(msg="监控已启动")


@api_v1.route('/monitor/stop', methods=['POST'])
@login_required
def monitor_stop():
    """停止监控"""
    global monitor_running
    monitor_state.stop_monitor()
    monitor_running = False
    return success(msg="监控已停止")


@api_v1.route('/monitor/interval', methods=['POST'])
@login_required
def monitor_interval():
    """设置刷新间隔"""
    data = request.get_json()
    interval = data.get('interval', 5)
    new_interval = max(1, min(60, int(interval)))
    # 通过 config 模块更新（app.py 监控循环从 config 读取）
    import config
    config.REFRESH_INTERVAL = new_interval
    return success({"interval": new_interval}, f"刷新间隔已设置为 {new_interval}s")


# ============ 接口列表 ============

@api_v1.route('/', methods=['GET'])
def api_index():
    """API 版本信息"""
    return success({
        "version": "v1",
        "description": "StockMonitor REST API",
        "auth_endpoints": {
            "register": "POST /api/v1/auth/register",
            "login": "POST /api/v1/auth/login",
            "logout": "POST /api/v1/auth/logout",
            "me": "GET /api/v1/auth/me",
        },
        "api_endpoints": {
            "stocks": "GET/POST /api/v1/stocks",
            "stocks_search": "GET /api/v1/stocks/search",
            "stocks_detail": "DELETE /api/v1/stocks/<code>",
            "groups": "GET/POST /api/v1/groups",
            "groups_detail": "DELETE/PUT /api/v1/groups/<id>",
            "groups_stocks": "POST/DELETE /api/v1/groups/<id>/stocks/<code>",
            "alerts_rules": "GET/POST /api/v1/alerts/rules",
            "alerts_rules_detail": "PUT/DELETE /api/v1/alerts/rules/<id>",
            "alerts_logs": "GET /api/v1/alerts/logs",
            "alerts_logs_read": "POST /api/v1/alerts/logs/<id>/read",
            "quotes": "GET /api/v1/quotes",
            "kline": "GET /api/v1/kline/<code>",
            "monitor_status": "GET /api/v1/monitor/status",
            "monitor_start": "POST /api/v1/monitor/start",
            "monitor_stop": "POST /api/v1/monitor/stop",
            "monitor_interval": "POST /api/v1/monitor/interval",
        }
    })
