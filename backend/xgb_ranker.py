"""
XGBoost 选股排序器
集成到 ai_screener：用模型对所有候选股打分排序，取 TOP N 喂给 AI 写理由
"""
import os
import joblib
import numpy as np
from collections import defaultdict

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xgb_model.pkl")

_model = None

def _load_model():
    """惰性加载模型"""
    global _model
    if _model is None:
        if not os.path.exists(MODEL_PATH):
            print("[XGB] 模型文件不存在，使用降级排序")
            return None
        _model = joblib.load(MODEL_PATH)
    return _model


def build_features(klines, idx):
    """为K线序列的第 idx 天提取特征（与训练时一致）"""
    import statistics
    k = klines
    today = k[idx]
    today_open, today_high, today_low, today_close, today_vol = today[1:]
    
    f = []
    
    # gap_pct
    if idx > 0:
        f.append(round((today_open - k[idx-1][4]) / k[idx-1][4] * 100, 4))
    else:
        f.append(0.0)
    
    # today_chg
    if idx > 0:
        f.append(round((today_close - k[idx-1][4]) / k[idx-1][4] * 100, 4))
    else:
        f.append(0.0)
    
    # day_range
    f.append(round((today_high - today_low) / today_open * 100, 4) if today_open > 0 else 0)
    
    # upper_shadow / lower_shadow
    rng = f[-1]
    if rng > 0.001:
        f.append(round((today_high - max(today_open, today_close)) / today_open * 100, 4))
        f.append(round((min(today_open, today_close) - today_low) / today_open * 100, 4))
    else:
        f.append(0.0); f.append(0.0)
    
    # vol_ratio
    vols = [x[5] for x in k[max(0, idx-5):idx]]
    avg = statistics.mean(vols) if vols else today_vol
    f.append(round(today_vol / avg, 4) if avg > 0 else 1.0)
    
    # pri_5d_chg
    if idx >= 5:
        f.append(round((k[idx-1][4] - k[idx-5][4]) / k[idx-5][4] * 100, 4))
    else:
        f.append(0.0)
    
    # pri_10d_chg
    if idx >= 10:
        f.append(round((k[idx-1][4] - k[idx-10][4]) / k[idx-10][4] * 100, 4))
    else:
        f.append(0.0)
    
    # pri_1d_chg
    if idx >= 2:
        f.append(round((k[idx-1][4] - k[idx-2][4]) / k[idx-2][4] * 100, 4))
    else:
        f.append(0.0)
    
    # up_days_5
    if idx >= 5:
        up = sum(1 for i in range(idx-4, idx+1) if k[i][4] > k[i-1][4]) if idx >= 4 else 0
        f.append(up / 5.0)
    else:
        f.append(0.5)
    
    # price_position
    if idx >= 19:
        p20 = [x[4] for x in k[idx-19:idx+1]]
        h, l = max(p20), min(p20)
        f.append(round((today_close - l) / (h - l), 4) if h - l > 0.001 else 0.5)
    else:
        f.append(0.5)
    
    # vol_trend
    if idx >= 5:
        ve = statistics.mean([x[5] for x in k[idx-5:idx-3]]) if idx >= 3 else today_vol
        vl = statistics.mean([x[5] for x in k[max(0, idx-2):idx]])
        f.append(round(vl / ve, 4) if ve > 0 else 1.0)
    else:
        f.append(1.0)
    
    # vs_ma5
    if idx >= 4:
        ma5 = statistics.mean([x[4] for x in k[idx-4:idx+1]])
        f.append(round(today_close / ma5 - 1, 4) if ma5 > 0 else 0)
    else:
        f.append(0.0)
    
    # vs_ma10
    if idx >= 9:
        ma10 = statistics.mean([x[4] for x in k[idx-9:idx+1]])
        f.append(round(today_close / ma10 - 1, 4) if ma10 > 0 else 0)
    else:
        f.append(0.0)
    
    return f


def rank_candidates(quotes, db_conn, top_n=30):
    """
    用 XGBoost 对所有候选股排序
    
    Args:
        quotes: {code: {name, price, change_pct, ...}} 实时行情
        db_conn: SQLite 连接
        top_n: 返回前 N 只
    
    Returns:
        [{code, name, proba, features_summary}, ...]
    """
    model = _load_model()
    if model is None:
        # 降级：按涨跌幅 + 成交额排序
        print("[XGB] 模型未加载，降级为量价排序")
        ranked = sorted(quotes.items(),
            key=lambda x: (x[1].get('change_pct', 0), x[1].get('amount_wan', 0)),
            reverse=True)
        return [{'code': c, 'name': q.get('name', c), 'proba': None} for c, q in ranked[:top_n]]
    
    def get_klines(code):
        rows = db_conn.execute(
            "SELECT trade_date,open,high,low,close,volume FROM price_history WHERE code=? ORDER BY trade_date",
            (code,)
        ).fetchall()
        return [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]
    
    scored = []
    for code, q in quotes.items():
        k = get_klines(code)
        if not k or len(k) < 20:
            scored.append((code, q, 0.5))
            continue
        
        # 用最新一个交易日做特征
        features = build_features(k, len(k) - 1)
        X = np.array(features, dtype=np.float32).reshape(1, -1)
        proba = model.predict_proba(X)[0][1]  # 盈利概率
        scored.append((code, q, float(proba)))
    
    # 按概率降序
    scored.sort(key=lambda x: x[2], reverse=True)
    
    result = []
    for code, q, proba in scored[:top_n]:
        result.append({
            'code': code,
            'name': q.get('name', code),
            'proba': round(proba, 4),
        })
    
    return result


if __name__ == '__main__':
    import sqlite3
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_monitor.db")
    conn = sqlite3.connect(db)
    
    # 简单测试：打印模型信息
    model = _load_model()
    if model:
        print(f"模型加载成功")
        print(f"特征数: {model.n_features_in_}")
    else:
        print("模型未找到，请先运行 train_xgb.py")
    
    conn.close()
