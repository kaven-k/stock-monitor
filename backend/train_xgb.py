"""
全市场K线标注 + XGBoost 训练
对 price_history 中每只股票每一天，计算特征并标注"2天后是否盈利"
输出: backend/xgb_model.pkl + backend/xgb_eval.json
"""
import sqlite3
import statistics
import json
import os
import numpy as np
from collections import defaultdict
from xgboost import XGBClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_monitor.db")
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xgb_model.pkl")
EVAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xgb_eval.json")

HOLD_DAYS = 2
MIN_HISTORY = 20

def load_stock_pool():
    """加载所有有足够K线的股票代码"""
    conn = sqlite3.connect(DB_PATH)
    codes = conn.execute(
        "SELECT DISTINCT code FROM price_history GROUP BY code HAVING COUNT(*) >= ?",
        (MIN_HISTORY + HOLD_DAYS,)
    ).fetchall()
    conn.close()
    return [c[0] for c in codes]

def get_klines(code, conn):
    rows = conn.execute(
        "SELECT trade_date,open,high,low,close,volume FROM price_history WHERE code=? ORDER BY trade_date",
        (code,)
    ).fetchall()
    return [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]

def compute_features(k, idx):
    """对K线序列 k 的第 idx 天，计算特征向量"""
    f = {}
    
    today = k[idx]
    today_open, today_high, today_low, today_close, today_vol = today[1:]
    
    # 1. 跳空幅度
    if idx > 0:
        prev_close = k[idx-1][4]
        f['gap_pct'] = round((today_open - prev_close) / prev_close * 100, 4)
    else:
        f['gap_pct'] = 0.0
    
    # 2. 当日涨跌幅
    if idx > 0:
        f['today_chg'] = round((today_close - prev_close) / prev_close * 100, 4)
    else:
        f['today_chg'] = 0.0
    
    # 3. 当日振幅
    f['day_range'] = round((today_high - today_low) / today_open * 100, 4) if today_open > 0 else 0
    
    # 4. 上影线/下影线
    if f['day_range'] > 0.001:
        f['upper_shadow'] = round((today_high - max(today_open, today_close)) / today_open * 100, 4)
        f['lower_shadow'] = round((min(today_open, today_close) - today_low) / today_open * 100, 4)
    else:
        f['upper_shadow'] = f['lower_shadow'] = 0.0
    
    # 5. 量比 (当日成交量 / 前5日均量)
    vols = [x[5] for x in k[max(0, idx-5):idx]]
    avg_vol = statistics.mean(vols) if vols else today_vol
    f['vol_ratio'] = round(today_vol / avg_vol, 4) if avg_vol > 0 else 1.0
    
    # 6. 前5日涨跌幅
    if idx >= 5:
        f['pri_5d_chg'] = round((k[idx-1][4] - k[idx-5][4]) / k[idx-5][4] * 100, 4)
    else:
        f['pri_5d_chg'] = 0.0
    
    # 7. 前10日涨跌幅
    if idx >= 10:
        f['pri_10d_chg'] = round((k[idx-1][4] - k[idx-10][4]) / k[idx-10][4] * 100, 4)
    else:
        f['pri_10d_chg'] = 0.0
    
    # 8. 前1日涨跌幅
    if idx >= 2:
        f['pri_1d_chg'] = round((k[idx-1][4] - k[idx-2][4]) / k[idx-2][4] * 100, 4)
    else:
        f['pri_1d_chg'] = 0.0
    
    # 9. RSI proxy: 最近5日上涨天数
    if idx >= 5:
        up_days = sum(1 for i in range(idx-4, idx+1) if k[i][4] > k[i-1][4]) if idx >= 4 else 0
        f['up_days_5'] = up_days / 5.0
    else:
        f['up_days_5'] = 0.5
    
    # 10. 价格位置 (当前价在最近20日高低区间的位置)
    if idx >= 19:
        prices_20 = [x[4] for x in k[idx-19:idx+1]]
        high_20, low_20 = max(prices_20), min(prices_20)
        if high_20 - low_20 > 0.001:
            f['price_position'] = round((today_close - low_20) / (high_20 - low_20), 4)
        else:
            f['price_position'] = 0.5
    else:
        f['price_position'] = 0.5
    
    # 11. 成交量趋势 (量增/量缩)
    if idx >= 5:
        vol_early = statistics.mean([x[5] for x in k[idx-5:idx-3]]) if idx >= 3 else today_vol
        vol_late = statistics.mean([x[5] for x in k[max(0, idx-2):idx]])
        if vol_early > 0:
            f['vol_trend'] = round(vol_late / vol_early, 4)
        else:
            f['vol_trend'] = 1.0
    else:
        f['vol_trend'] = 1.0
    
    # 12. 现价 vs MA5
    if idx >= 4:
        ma5 = statistics.mean([x[4] for x in k[idx-4:idx+1]])
        f['vs_ma5'] = round(today_close / ma5 - 1, 4) if ma5 > 0 else 0
    else:
        f['vs_ma5'] = 0.0
    
    # 13. 现价 vs MA10
    if idx >= 9:
        ma10 = statistics.mean([x[4] for x in k[idx-9:idx+1]])
        f['vs_ma10'] = round(today_close / ma10 - 1, 4) if ma10 > 0 else 0
    else:
        f['vs_ma10'] = 0.0
    
    return f

def compute_label(k, idx, hold_days):
    """标注：从idx那天开盘买入，hold_days天后卖出盈亏"""
    if idx + hold_days >= len(k):
        return None
    entry_price = k[idx][1]  # open of day idx
    exit_price = k[idx + hold_days - 1][4]  # close after hold_days-1
    return 1 if exit_price > entry_price else 0

def generate_samples():
    """全市场滚动标注，生成训练样本"""
    codes = load_stock_pool()
    print(f"候选股票: {len(codes)} 只")
    
    conn = sqlite3.connect(DB_PATH)
    X_list, y_list = [], []
    stock_counts = []
    
    for ci, code in enumerate(codes):
        k = get_klines(code, conn)
        if len(k) < MIN_HISTORY + HOLD_DAYS:
            continue
        
        stock_samples = 0
        # 从 MIN_HISTORY 天开始，留出最后 HOLD_DAYS 做标签
        for idx in range(MIN_HISTORY - 1, len(k) - HOLD_DAYS):
            label = compute_label(k, idx, HOLD_DAYS)
            if label is None:
                continue
            features = compute_features(k, idx)
            feats = list(features.values())
            X_list.append(feats)
            y_list.append(label)
            stock_samples += 1
        
        if stock_samples > 0:
            stock_counts.append((code, stock_samples))
        
        if (ci + 1) % 500 == 0:
            print(f"  进度: {ci+1}/{len(codes)} 只股票, 累计 {len(X_list)} 条样本")
    
    conn.close()
    
    X = np.array(X_list, dtype=np.float32)
    y = np.array(y_list, dtype=np.int32)
    
    # 特征名
    feature_names = list(compute_features(
        get_klines(codes[0], sqlite3.connect(DB_PATH)), MIN_HISTORY
    ).keys())
    
    print(f"\n总样本: {len(X)} 条")
    print(f"正样本(盈利): {sum(y)} ({sum(y)/len(y)*100:.1f}%)")
    print(f"负样本(亏损): {len(y)-sum(y)} ({(len(y)-sum(y))/len(y)*100:.1f}%)")
    
    return X, y, feature_names

def train_model(X, y, feature_names):
    """训练 XGBoost 分类器"""
    print("\n===== 训练 XGBoost =====")
    
    # 划分训练/测试集
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    
    # 处理类别不平衡
    pos_ratio = sum(y) / len(y)
    scale_pos_weight = (1 - pos_ratio) / pos_ratio
    
    model = XGBClassifier(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos_weight,
        objective='binary:logistic',
        eval_metric='auc',
        random_state=42,
        n_jobs=-1,
    )
    
    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=False
    )
    
    # 评估
    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]
    
    metrics = {
        'accuracy': round(accuracy_score(y_test, y_pred) * 100, 1),
        'precision': round(precision_score(y_test, y_pred) * 100, 1),
        'recall': round(recall_score(y_test, y_pred) * 100, 1),
        'f1': round(f1_score(y_test, y_pred) * 100, 1),
        'auc': round(roc_auc_score(y_test, y_proba) * 100, 1),
        'test_samples': len(y_test),
        'positive_ratio': round(pos_ratio * 100, 1),
        'scale_pos_weight': round(scale_pos_weight, 2),
        'feature_names': feature_names,
        'hold_days': HOLD_DAYS,
    }
    
    print(f"\n===== 模型评估 =====")
    print(f"测试集: {metrics['test_samples']} 条")
    print(f"准确率: {metrics['accuracy']}%")
    print(f"精确率: {metrics['precision']}%")
    print(f"召回率: {metrics['recall']}%")
    print(f"F1 分数: {metrics['f1']}%")
    print(f"AUC: {metrics['auc']}%")
    
    # 特征重要性
    print(f"\n===== 特征重要性 TOP10 =====")
    importance = list(zip(feature_names, model.feature_importances_))
    importance.sort(key=lambda x: x[1], reverse=True)
    for name, imp in importance[:10]:
        print(f"  {name:<20} {imp:.4f}")
    
    return model, metrics

def save_model(model, metrics):
    """保存模型和评估指标"""
    import joblib
    joblib.dump(model, MODEL_PATH)
    with open(EVAL_PATH, 'w', encoding='utf-8') as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"\n模型已保存: {MODEL_PATH}")
    print(f"评估已保存: {EVAL_PATH}")

def main():
    print(f"全市场K线标注 + XGBoost 训练 (持仓 {HOLD_DAYS} 天)")
    print(f"DB: {DB_PATH}")
    print("-" * 50)
    
    X, y, feature_names = generate_samples()
    model, metrics = train_model(X, y, feature_names)
    save_model(model, metrics)

if __name__ == '__main__':
    main()
