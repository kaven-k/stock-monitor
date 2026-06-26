"""
共享监控状态 - 解决 app.py 与 api_v1.py 的监控状态同步问题
"""
import threading

monitor_running = False
monitor_thread = None
_lock = threading.Lock()

def start_monitor(loop_func, *args):
    """启动监控循环"""
    global monitor_running, monitor_thread
    with _lock:
        if monitor_running:
            return False
        monitor_running = True
        monitor_thread = threading.Thread(target=loop_func, args=args, daemon=True)
        monitor_thread.start()
        return True

def stop_monitor():
    """停止监控循环"""
    global monitor_running
    with _lock:
        monitor_running = False
    return True

def is_running():
    return monitor_running
