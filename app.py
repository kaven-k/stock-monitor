"""
股票监控系统 - Flask 主服务
提供 REST API + WebSocket 实时推送
"""
import os
import sys
import json
import time
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, render_template_string
from flask_socketio import SocketIO, emit
from flask_cors import CORS

# 添加项目目录到 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import init_db, get_all_stocks, add_stock, remove_stock
from database import get_all_groups, create_group, delete_group, add_to_group, remove_from_group, update_group
from database import get_all_alert_rules, create_alert_rule, update_alert_rule, delete_alert_rule
from database import add_alert_log, get_recent_alerts, mark_alert_read, get_unread_alert_count
from database import get_price_history, save_snapshots, cleanup_old_snapshots
from data_fetcher import fetch_tencent_quotes, fetch_kline, get_technical_indicators, search_stock, sync_kline_to_db
from alert_engine import AlertEngine
import database as db_module

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config['SECRET_KEY'] = 'stock-monitor-secret-2024'
app.config['JSON_AS_ASCII'] = False

CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# 初始化
init_db()

# 监控状态
monitor_running = False
monitor_thread = None
last_quotes = {}
REFRESH_INTERVAL = 5  # 刷新间隔（秒）

alert_engine = AlertEngine(db_module)


# ============ 首页 ============

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


# ============ 股票管理 API ============

@app.route('/api/stocks', methods=['GET'])
def api_stocks():
    """获取所有股票列表"""
    stocks = get_all_stocks()
    return jsonify({"code": 0, "data": stocks})


@app.route('/api/stocks/search')
def api_search_stock():
    """搜索股票"""
    keyword = request.args.get('keyword', '')
    if not keyword:
        return jsonify({"code": 1, "msg": "请输入搜索关键词"})
    results = search_stock(keyword)
    return jsonify({"code": 0, "data": results})


@app.route('/api/stocks', methods=['POST'])
def api_add_stock():
    """添加股票"""
    data = request.get_json()
    code = data.get('code', '').strip()
    name = data.get('name', '').strip()
    market = data.get('market', 'A')
    if not code:
        return jsonify({"code": 1, "msg": "股票代码不能为空"})
    if not name:
        return jsonify({"code": 1, "msg": "股票名称不能为空"})
    
    success = add_stock(code, name, market)
    if success:
        # 异步同步K线数据
        threading.Thread(target=sync_kline_to_db, args=(code,), daemon=True).start()
        return jsonify({"code": 0, "msg": "添加成功"})
    return jsonify({"code": 1, "msg": "添加失败"})


@app.route('/api/stocks/<code>', methods=['DELETE'])
def api_remove_stock(code):
    """删除股票"""
    remove_stock(code)
    return jsonify({"code": 0, "msg": "删除成功"})


# ============ 分组管理 API ============

@app.route('/api/groups', methods=['GET'])
def api_groups():
    """获取所有分组"""
    groups = get_all_groups()
    return jsonify({"code": 0, "data": groups})


@app.route('/api/groups', methods=['POST'])
def api_create_group():
    """创建分组"""
    data = request.get_json()
    name = data.get('name', '').strip()
    color = data.get('color', '#3b82f6')
    if not name:
        return jsonify({"code": 1, "msg": "分组名不能为空"})
    gid = create_group(name, color)
    if gid:
        return jsonify({"code": 0, "data": {"id": gid, "name": name, "color": color}})
    return jsonify({"code": 1, "msg": "创建失败"})


@app.route('/api/groups/<int:group_id>', methods=['DELETE'])
def api_delete_group(group_id):
    """删除分组"""
    delete_group(group_id)
    return jsonify({"code": 0, "msg": "删除成功"})


@app.route('/api/groups/<int:group_id>', methods=['PUT'])
def api_update_group(group_id):
    """更新分组"""
    data = request.get_json()
    update_group(group_id, name=data.get('name'), color=data.get('color'))
    return jsonify({"code": 0, "msg": "更新成功"})


@app.route('/api/groups/<int:group_id>/stocks', methods=['POST'])
def api_add_to_group(group_id):
    """将股票加入分组"""
    data = request.get_json()
    code = data.get('code', '')
    if not code:
        return jsonify({"code": 1, "msg": "股票代码不能为空"})
    success = add_to_group(group_id, code)
    return jsonify({"code": 0 if success else 1, "msg": "添加成功" if success else "添加失败"})


@app.route('/api/groups/<int:group_id>/stocks/<code>', methods=['DELETE'])
def api_remove_from_group(group_id, code):
    """从分组移除股票"""
    remove_from_group(group_id, code)
    return jsonify({"code": 0, "msg": "移除成功"})


# ============ 预警规则 API ============

@app.route('/api/alerts/rules', methods=['GET'])
def api_alert_rules():
    """获取所有预警规则"""
    rules = get_all_alert_rules()
    return jsonify({"code": 0, "data": rules})


@app.route('/api/alerts/rules', methods=['POST'])
def api_create_alert_rule():
    """创建预警规则"""
    data = request.get_json()
    name = data.get('name', '').strip()
    rule_type = data.get('rule_type', '')
    params = data.get('params', {})
    stock_codes = data.get('stock_codes', [])
    notify_feishu = data.get('notify_feishu', 0)
    
    if not name or not rule_type:
        return jsonify({"code": 1, "msg": "规则名称和类型不能为空"})
    
    rid = create_alert_rule(name, rule_type, params, stock_codes, notify_feishu)
    if rid:
        return jsonify({"code": 0, "data": {"id": rid}})
    return jsonify({"code": 1, "msg": "创建失败"})


@app.route('/api/alerts/rules/<int:rule_id>', methods=['PUT'])
def api_update_alert_rule(rule_id):
    """更新预警规则"""
    data = request.get_json()
    update_alert_rule(rule_id, **{k: v for k, v in data.items() if v is not None})
    return jsonify({"code": 0, "msg": "更新成功"})


@app.route('/api/alerts/rules/<int:rule_id>', methods=['DELETE'])
def api_delete_alert_rule(rule_id):
    """删除预警规则"""
    delete_alert_rule(rule_id)
    return jsonify({"code": 0, "msg": "删除成功"})


@app.route('/api/alerts/logs', methods=['GET'])
def api_alert_logs():
    """获取预警日志"""
    limit = request.args.get('limit', 50, type=int)
    logs = get_recent_alerts(limit)
    unread = get_unread_alert_count()
    return jsonify({"code": 0, "data": logs, "unread": unread})


@app.route('/api/alerts/logs/<int:log_id>/read', methods=['POST'])
def api_mark_alert_read(log_id):
    """标记预警已读"""
    mark_alert_read(log_id)
    return jsonify({"code": 0, "msg": "已标记"})


# ============ 行情数据 API ============

@app.route('/api/quotes', methods=['GET'])
def api_quotes():
    """获取实时行情"""
    codes_param = request.args.get('codes', '')
    if codes_param:
        codes = [c.strip() for c in codes_param.split(',') if c.strip()]
    else:
        stocks = get_all_stocks()
        codes = [s['code'] for s in stocks]

    if not codes:
        return jsonify({"code": 0, "data": {}, "time": datetime.now().strftime('%H:%M:%S')})

    quotes = fetch_tencent_quotes(codes)
    return jsonify({
        "code": 0,
        "data": quotes,
        "time": datetime.now().strftime('%H:%M:%S'),
        "count": len(quotes)
    })


@app.route('/api/kline/<code>', methods=['GET'])
def api_kline(code):
    """获取K线数据"""
    period = request.args.get('period', 'day')
    count = request.args.get('count', 250, type=int)
    
    # 先从数据库获取
    from database import get_price_history as get_db_history
    hist = get_db_history(code, count)
    
    if hist and len(hist) >= max(20, count // 2):
        # 计算技术指标
        indicators = get_technical_indicators(hist)
        return jsonify({
            "code": 0,
            "data": {"kline": hist, "indicators": indicators},
            "source": "db"
        })
    
    # 从API获取
    kline = fetch_kline(code, period, count)
    if kline:
        indicators = get_technical_indicators(kline)
        return jsonify({
            "code": 0,
            "data": {"kline": kline, "indicators": indicators},
            "source": "api"
        })
    
    return jsonify({"code": 1, "msg": "获取K线数据失败"})


# ============ 监控控制 API ============

@app.route('/api/monitor/status', methods=['GET'])
def api_monitor_status():
    """获取监控状态"""
    return jsonify({
        "code": 0,
        "running": monitor_running,
        "interval": REFRESH_INTERVAL,
        "last_quotes_count": len(last_quotes),
        "time": datetime.now().strftime('%H:%M:%S')
    })


@app.route('/api/monitor/start', methods=['POST'])
def api_monitor_start():
    """启动监控"""
    global monitor_running, monitor_thread
    if not monitor_running:
        monitor_running = True
        monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        monitor_thread.start()
        return jsonify({"code": 0, "msg": "监控已启动"})
    return jsonify({"code": 0, "msg": "监控已在运行中"})


@app.route('/api/monitor/stop', methods=['POST'])
def api_monitor_stop():
    """停止监控"""
    global monitor_running
    monitor_running = False
    return jsonify({"code": 0, "msg": "监控已停止"})


@app.route('/api/monitor/interval', methods=['POST'])
def api_monitor_interval():
    """设置刷新间隔"""
    global REFRESH_INTERVAL
    data = request.get_json()
    interval = data.get('interval', 5)
    REFRESH_INTERVAL = max(1, min(60, int(interval)))
    return jsonify({"code": 0, "msg": f"刷新间隔已设置为 {REFRESH_INTERVAL} 秒"})


# ============ SocketIO 事件 ============

@socketio.on('connect')
def handle_connect():
    print(f"[SocketIO] 客户端连接: {request.sid}")
    emit('connected', {'status': 'ok', 'monitor_running': monitor_running})


@socketio.on('disconnect')
def handle_disconnect():
    print(f"[SocketIO] 客户端断开: {request.sid}")


@socketio.on('subscribe')
def handle_subscribe(data):
    """客户端订阅特定股票"""
    codes = data.get('codes', [])
    print(f"[SocketIO] 客户端 {request.sid} 订阅: {codes}")


# ============ 监控循环 ============

def monitor_loop():
    """监控主循环 - 定期拉取行情、检查预警、推送更新"""
    global last_quotes, monitor_running
    print("[Monitor] 监控循环启动")
    
    while monitor_running:
        try:
            stocks = get_all_stocks()
            if not stocks:
                time.sleep(REFRESH_INTERVAL)
                continue

            codes = [s['code'] for s in stocks]
            
            # 获取实时行情
            quotes = fetch_tencent_quotes(codes)
            if quotes:
                last_quotes = quotes
                now = datetime.now()
                ts = now.strftime('%Y-%m-%d %H:%M:%S')
                
                # 保存快照
                snapshots = []
                for code, q in quotes.items():
                    snapshots.append((
                        code, ts, q.get('price', 0), q.get('change_pct', 0),
                        q.get('volume', 0), q.get('amount_wan', 0), q.get('turnover_pct', 0)
                    ))
                save_snapshots(snapshots)
                
                # 检查预警
                triggered = alert_engine.check_all_rules(quotes)
                for t in triggered:
                    rule_id, rule_name, code, stock_name, alert_type, alert_msg = t
                    add_alert_log(rule_id, rule_name, code, stock_name, alert_type, alert_msg)
                    print(f"[Alert] {alert_msg}")
                
                # 推送行情更新
                socketio.emit('quotes_update', {
                    'data': quotes,
                    'time': now.strftime('%H:%M:%S'),
                    'timestamp': ts,
                })
                
                # 如果有新预警，推送
                if triggered:
                    socketio.emit('alerts_new', {
                        'count': len(triggered),
                        'alerts': [
                            {
                                'rule_name': t[1],
                                'stock_code': t[2],
                                'stock_name': t[3],
                                'type': t[4],
                                'msg': t[5],
                                'time': ts,
                            }
                            for t in triggered
                        ]
                    })
            
            # 每小时清理一次旧快照
            if now.minute == 0:
                cleanup_old_snapshots(24)
                
        except Exception as e:
            print(f"[Monitor] 监控循环异常: {e}")
            import traceback
            traceback.print_exc()
        
        # 等待下一次刷新
        for _ in range(REFRESH_INTERVAL):
            if not monitor_running:
                break
            time.sleep(1)
    
    print("[Monitor] 监控循环已停止")


# ============ 启动 ============

if __name__ == '__main__':
    # 同步已有股票的K线数据
    print("[Init] 同步历史K线数据...")
    stocks = get_all_stocks()
    for s in stocks:
        try:
            count = sync_kline_to_db(s['code'], 365)
            print(f"  {s['name']}({s['code']}): {count} 条")
        except Exception as e:
            print(f"  {s['name']}({s['code']}): 同步失败 - {e}")
    
    # 启动监控
    monitor_running = True
    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()
    
    port = int(os.environ.get('PORT', 5000))
    print(f"\n{'='*50}")
    print(f"  股票监控系统已启动")
    print(f"  地址: http://localhost:{port}")
    print(f"{'='*50}\n")
    
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
