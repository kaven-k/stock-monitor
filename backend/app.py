"""
====================================================
  StockMonitor - 股票监控系统 (前后端分离)
  后端: Flask REST API (JWT认证 + CORS + API版本化)
  前端: 独立 SPA (HTML/CSS/JS)
====================================================
"""

import os
import sys
import threading

# 确保当前目录在路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask
from flask_socketio import SocketIO
from flask_cors import CORS

from config import (
    FLASK_SECRET_KEY, API_HOST, API_PORT, CORS_ORIGINS,
    REFRESH_INTERVAL, DB_PATH,
)
from database import init_db, get_all_stocks
from auth import auth_bp
from api_v1 import api_v1


def create_app():
    """创建并配置 Flask 应用"""
    # 静态文件路径：backend/../frontend (新版前端SPA)
    static_folder = os.path.join(os.path.dirname(__file__), '..', 'frontend')
    app = Flask(__name__, static_folder=static_folder, static_url_path='')
    app.config['SECRET_KEY'] = FLASK_SECRET_KEY
    app.config['JSON_AS_ASCII'] = False

    # CORS 配置 (允许前后端分离部署)
    CORS(app, 
         origins=CORS_ORIGINS,
         supports_credentials=True,
         allow_headers=['Content-Type', 'Authorization'],
         methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])

    # 注册蓝图
    app.register_blueprint(auth_bp, url_prefix='/api/v1/auth')
    app.register_blueprint(api_v1)

    # 首页（服务前端 SPA）
    from flask import send_from_directory
    @app.route('/')
    def index():
        return send_from_directory(static_folder, 'index.html')

    # 开发模式：禁用静态文件缓存（方便调试）
    @app.after_request
    def add_no_cache_header(response):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    # 全局错误处理
    @app.errorhandler(404)
    def not_found(e):
        return {"code": 40400, "msg": "接口不存在", "error": "NOT_FOUND"}, 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return {"code": 40500, "msg": "请求方法不允许", "error": "METHOD_NOT_ALLOWED"}, 405

    @app.errorhandler(500)
    def server_error(e):
        return {"code": 50000, "msg": "服务器内部错误", "error": "INTERNAL_ERROR"}, 500

    return app


def create_socketio(app):
    """创建 SocketIO 实例"""
    return SocketIO(app, cors_allowed_origins=CORS_ORIGINS, async_mode='threading')


def startup_monitor(socketio_instance):
    """启动监控循环 (将 socketio 注入)"""
    import monitor_state
    
    stocks = get_all_stocks()
    
    # 为已有股票补打标签
    print("[Init] 自动打标签...")
    from stock_tagger import auto_tag_stock
    from database import update_stock_tags
    tagged = 0
    for s in stocks:
        tags = auto_tag_stock(s['code'], s['name'])
        if tags:
            update_stock_tags(s['code'], tags)
            tagged += 1
    print(f"  已为 {tagged}/{len(stocks)} 只股票打标签")
    
    # 初始化历史K线
    print("[Init] 同步历史K线数据...")
    from data_fetcher import sync_kline_to_db
    for s in stocks:
        try:
            count = sync_kline_to_db(s['code'], 365)
            if count > 0:
                print(f"  {s['name']}({s['code']}): {count} 条")
        except Exception as e:
            print(f"  {s['name']}({s['code']}): 同步失败 - {e}")
    
    # 启动监控 (使用共享状态)
    # 默认不自动启动：登录后由用户在前端手动点击“启动监控”开启刷新
    import config as _cfg
    if _cfg.AUTO_START_MONITOR:
        monitor_state.start_monitor(exec_loop_with_socketio, socketio_instance)
        print("[Init] 监控已按配置自动启动")
    else:
        print("[Init] 监控未自动启动 (AUTO_START_MONITOR=false)，请登录后手动点击“启动监控”")

    # 同步到 api_v1 模块
    import api_v1
    api_v1.monitor_running = monitor_state.is_running()


def exec_loop_with_socketio(sio):
    """执行监控循环（带 socketio 引用 + 飞书通知集成）"""
    import time
    import monitor_state
    import config as app_config
    from datetime import datetime
    from database import get_all_stocks, save_snapshots, cleanup_old_snapshots, add_alert_log
    from data_fetcher import fetch_tencent_quotes
    from alert_engine import AlertEngine
    import database as db_module
    import feishu_notify
    
    alert_engine = AlertEngine(db_module)
    last_quotes = {}
    last_fs_notify = {}  # 防重复通知
    alert_cooldown = {}  # 预警去重
    
    # === 性能优化：历史数据预缓存（先获取stocks列表）===
    import signal_engine
    stocks = get_all_stocks()
    if stocks:
        cached = signal_engine.load_history_cache(db_module, [s['code'] for s in stocks])
        print(f"[Monitor] 历史数据缓存就绪: {cached}/{len(stocks)} 只")
    
    # === 信号引擎（多指标共振分析）===
    sig_engine = signal_engine.SignalEngine()
    last_signals = {}  # {code: score} 用于检测信号变化
    signal_cycle = 0   # 信号分析周期计数（每60次循环 ≈ 5分钟）
    
    print("[Monitor] 监控循环启动 (已关联监控状态 + 飞书通知 + 信号引擎)")
    
    while monitor_state.is_running():
        try:
            stocks = get_all_stocks()
            if not stocks:
                time.sleep(app_config.REFRESH_INTERVAL)
                continue
            
            codes = [s['code'] for s in stocks]
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
                
                # 检查预警（所有已启用规则），带价格变化去重
                triggered = alert_engine.check_all_rules(quotes)
                deduped = []
                for t in triggered:
                    rule_id, rule_name, code, stock_name, alert_type, alert_msg = t
                    cd_key = f"{rule_id}_{code}"
                    q = quotes.get(code, {})
                    current_price = q.get('price', 0)
                    
                    # 去重逻辑：同一规则+股票，只有价格变化超过0.5%或超过10分钟才重新触发
                    if cd_key in alert_cooldown:
                        last_price, last_time = alert_cooldown[cd_key]
                        price_change = abs(current_price - last_price) / max(last_price, 0.01) if last_price > 0 else 1
                        if price_change < 0.005 and time.time() - last_time < 600:
                            continue  # 价格基本未变且未超10分钟，跳过
                    
                    alert_cooldown[cd_key] = (current_price, time.time())
                    deduped.append(t)
                    add_alert_log(rule_id, rule_name, code, stock_name, alert_type, alert_msg)
                    print(f"[Alert] {alert_msg}")
                    
                    # 飞书通知：检查该规则是否启用飞书通知
                    feishu_key = f"{rule_id}_{code}"
                    last_time = last_fs_notify.get(feishu_key, 0)
                    if time.time() - last_time > 180:  # 3分钟防重复
                        try:
                            rule = db_module.get_alert_rule_by_id(rule_id)
                            if rule and rule.get('notify_feishu'):
                                feishu_notify.send_stock_alert(
                                    rule_name, stock_name, code, alert_type, alert_msg
                                )
                                last_fs_notify[feishu_key] = time.time()
                        except Exception as e:
                            print(f"[Feishu] 通知发送异常: {e}")
                
                # SocketIO 推送
                try:
                    sio.emit('quotes_update', {
                        'data': quotes,
                        'time': now.strftime('%H:%M:%S'),
                        'timestamp': ts,
                    })
                    if deduped:
                        sio.emit('alerts_new', {
                            'count': len(deduped),
                            'alerts': [{
                                'rule_name': t[1], 'stock_code': t[2],
                                'stock_name': t[3], 'type': t[4],
                                'msg': t[5], 'time': ts,
                            } for t in deduped],
                        })
                except Exception as e:
                    print(f"[SocketIO Error] {e}")
                
                # === 信号引擎：每60次循环 ≈ 每5分钟分析一次 ===
                signal_cycle += 1
                if signal_cycle >= 60:
                    signal_cycle = 0
                    try:
                        # 1. 更新历史数据缓存
                        for code, q in quotes.items():
                            bar = {
                                'trade_date': now.strftime('%Y-%m-%d'),
                                'open': q.get('open', q.get('price', 0)),
                                'high': q.get('high', q.get('price', 0)),
                                'low': q.get('low', q.get('price', 0)),
                                'close': q.get('price', 0),
                                'volume': q.get('volume', 0),
                            }
                            signal_engine.update_history_cache(code, bar)
                        
                        # 2. 从缓存中取K线数据进行分析
                        kline_map = {}
                        for code in codes:
                            hist = signal_engine.get_cached_history(code)
                            if hist and len(hist) >= 60:
                                # 统一字段名 trade_date → date
                                for h in hist:
                                    if 'trade_date' in h and 'date' not in h:
                                        h['date'] = h['trade_date']
                                kline_map[code] = hist
                        
                        # 3. 批量分析
                        sig_results = sig_engine.bulk_analyze(kline_map)
                        
                        # 4. 检测信号变化并推送
                        signal_changes = []
                        for code, result in sig_results.items():
                            prev_score = last_signals.get(code, 0)
                            new_score = result.get('score', 0)
                            prev_level = last_signals.get(f'{code}_level', 'neutral')
                            new_level = result.get('level', 'neutral')
                            
                            # 信号等级变化 或 分数变化超25分
                            if prev_level != new_level or abs(new_score - prev_score) >= 25:
                                result['code'] = code
                                result['name'] = quotes.get(code, {}).get('name', '')
                                result['price'] = quotes.get(code, {}).get('price', 0)
                                result['change_pct'] = quotes.get(code, {}).get('change_pct', 0)
                                signal_changes.append(result)
                                last_signals[code] = new_score
                                last_signals[f'{code}_level'] = new_level
                        
                        if signal_changes:
                            sio.emit('signal_update', {
                                'signals': signal_changes,
                                'time': now.strftime('%H:%M:%S'),
                            })
                            # 检查 ma_signal_change 预警规则
                            from alert_engine import AlertEngine as AE
                            for sc in signal_changes:
                                if sc['level'] in ('strong_buy', 'strong_sell'):
                                    print(f"[Signal] {sc['name']}({sc['code']}) {sc['level_text']} (评分:{sc['score']})")
                    except Exception as e:
                        print(f"[Signal Engine Error] {e}")
                        import traceback
                        traceback.print_exc()
                # === 信号引擎结束 ===
                
                if now.minute == 0:
                    cleanup_old_snapshots(24)
                    
        except Exception as e:
            print(f"[Monitor Error] {e}")
            import traceback
            traceback.print_exc()
        
        for _ in range(app_config.REFRESH_INTERVAL):
            if not monitor_state.is_running():
                break
            time.sleep(1)
    
    print("[Monitor] 监控循环已停止")
    # 同步到 api_v1
    import api_v1
    api_v1.monitor_running = False


if __name__ == '__main__':
    # 调试Flash问题

    print("=" * 60)
    print("  StockMonitor 股票监控系统 v2.0")
    print("  架构: 前后端分离 | REST API v1 | JWT 认证")
    print("=" * 60)
    
    # 初始化数据库
    init_db()
    print(f"  数据库: {DB_PATH}")
    print(f"  API 地址: http://localhost:{API_PORT}/api/v1/")
    print(f"  API 文档: http://localhost:{API_PORT}/api/v1/")
    print(f"  前端地址: 在 frontend/ 目录单独运行")
    print("=" * 60)
    
    app = create_app()
    socketio = create_socketio(app)
    
    # 将 socketio 引用注入到模块级别 (供 api_v1 引用)
    import app as self_module
    self_module.socketio = socketio
    
    startup_monitor(socketio)
    print(app.url_map)

    
    socketio.run(app, host=API_HOST, port=API_PORT, debug=False, allow_unsafe_werkzeug=True)
