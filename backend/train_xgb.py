"""
全市场K线标注 + XGBoost 训练
对 price_history 中每只股票每一天，计算特征并标注"持有 N 天后是否盈利"

★ 入场模式（v7 新增）—— 这是实盘能否落地的关键：
  entry='open'  : 第 idx 天【开盘】买入 → 第 idx+N-1 天收盘卖出
                  N=2 时即"今天开盘买、明天收盘卖"（原模型）
                  ⚠️ N=1 时变成"当天开盘买、当天收盘卖"，A股 T+1 禁止，不可用
  entry='close' : 第 idx 天【收盘】买入 → 第 idx+N 天收盘卖出
                  N=1 时即"今天尾盘买、明天收盘卖" ← 这才是短线实盘的正确姿势
                  T+1 约束下，尾盘买入次日卖出是唯一能吃到"当日趋势→次日溢价"的路径

用法：
    python3 train_xgb.py                  # 默认 hold=1, entry=close（尾盘买次日卖）
    python3 train_xgb.py 2 open           # 复现原模型（开盘买持有2天）
    python3 train_xgb.py 1 close --default  # 训练并覆盖为默认模型

输出: backend/xgb_model_h{hold}_{entry}.pkl + backend/xgb_eval_h{hold}_{entry}.json
"""
import sqlite3
import statistics
import json
import os
import sys
import numpy as np
from collections import defaultdict
from xgboost import XGBClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_monitor.db")

# 默认训练参数：hold=1, entry=close（尾盘买 → 次日收盘卖）
HOLD_DAYS = 1
ENTRY_MODE = "close"
MIN_HISTORY = 20
AS_DEFAULT = False
SPLIT_MODE = "random"
NO_FUTURE = False
USE_MARKET = False   # 是否加入市场情绪特征（需先运行 market_features.py）
USE_FUND = False     # 是否加入资金流/板块特征（需先运行 download_fund_history.py）
VALID_ONLY = False   # 仅用有效交易日样本（不加特征，用于公平对比基线）

# 开盘时刻【可知】的特征（不含任何当日收盘后才产生的量价信息）
# 顺序对应 compute_features 的键顺序
OPEN_KNOWN_FEATURES = ["gap_pct", "pri_5d_chg", "pri_10d_chg", "pri_1d_chg", "up_days_5"]

_MODEL_TPL = "xgb_model_h{hold}_{entry}.pkl"
_EVAL_TPL = "xgb_eval_h{hold}_{entry}.json"


def _paths(hold, entry):
    d = os.path.dirname(os.path.abspath(__file__))
    return (os.path.join(d, _MODEL_TPL.format(hold=hold, entry=entry)),
            os.path.join(d, _EVAL_TPL.format(hold=hold, entry=entry)))

def _norm_date(d):
    """日期归一化为 YYYYMMDD（东财源为 2026-10-26，新浪源为 20261026）"""
    return str(d).replace("-", "").strip()


def load_stock_pool():
    """加载所有有足够K线的股票代码"""
    conn = sqlite3.connect(DB_PATH)
    codes = conn.execute(
        "SELECT DISTINCT code FROM price_history GROUP BY code HAVING COUNT(*) >= ?",
        (MIN_HISTORY + HOLD_DAYS + 1,)
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

def compute_label(k, idx, hold_days, entry="open"):
    """
    标注：按入场模式计算持有期盈亏
    entry='open'  : idx 日开盘买入 → idx+hold_days-1 日收盘卖出
    entry='close' : idx 日收盘买入 → idx+hold_days 日收盘卖出（尾盘买，T+1 次日卖）
    """
    if entry == "close":
        if idx + hold_days >= len(k):
            return None
        entry_price = k[idx][4]       # close of day idx
        exit_price = k[idx + hold_days][4]   # close after hold_days
    else:
        if idx + hold_days >= len(k):
            return None
        entry_price = k[idx][1]       # open of day idx
        exit_price = k[idx + hold_days - 1][4]
    if not entry_price or not exit_price:
        return None
    return 1 if exit_price > entry_price else 0

def generate_samples():
    """全市场滚动标注，生成训练样本"""
    codes = load_stock_pool()
    print(f"候选股票: {len(codes)} 只")
    
    conn = sqlite3.connect(DB_PATH)
    X_list, y_list, date_list, code_list, ret_list = [], [], [], [], []
    stock_counts = []

    # 市场情绪特征（T日收盘可知，无前视偏差）
    market_feats = {}
    if USE_MARKET or VALID_ONLY:
        try:
            from market_features import load_market_features, MARKET_FEATURE_NAMES
            market_feats = load_market_features()
            print(f"  市场情绪特征: {len(market_feats)} 个交易日 × {len(MARKET_FEATURE_NAMES)} 维")
            if not market_feats:
                print("  ⚠️  market_daily 为空，请先运行: python3 market_features.py")
        except Exception as e:
            print(f"  ⚠️  市场特征加载失败({e})，本次不使用")

    # 资金流/板块特征（历史，需 download_fund_history.py 生成）
    fund_feats = {}
    FUND_FEATURE_NAMES = []
    if USE_FUND:
        try:
            from fund_history import load_fund_features, FUND_FEATURE_NAMES
            fund_feats = load_fund_features()
            print(f"  资金流/板块特征: {len(fund_feats)} 个交易日")
            if not fund_feats:
                print("  ⚠️  资金流历史为空，请先运行: python3 download_fund_history.py")
        except Exception as e:
            print(f"  ⚠️  资金流特征不可用({e})，本次不使用")
            fund_feats = {}
            FUND_FEATURE_NAMES = []
    
    # 收盘买入模式需多留一天做卖出价
    tail_reserve = HOLD_DAYS if ENTRY_MODE == "close" else HOLD_DAYS
    
    for ci, code in enumerate(codes):
        k = get_klines(code, conn)
        if len(k) < MIN_HISTORY + tail_reserve + 1:
            continue
        
        stock_samples = 0
        # 从 MIN_HISTORY 天开始，留出尾部做标签
        for idx in range(MIN_HISTORY - 1, len(k) - tail_reserve - 1):
            label = compute_label(k, idx, HOLD_DAYS, ENTRY_MODE)
            if label is None:
                continue
            d = _norm_date(k[idx][0])
            # 只保留有效交易日（过滤早期稀疏/脏数据）
            if (USE_MARKET or VALID_ONLY) and market_feats and d not in market_feats:
                continue
            features = compute_features(k, idx)
            feats = list(features.values())
            if USE_MARKET and market_feats:
                feats += [market_feats[d].get(n, 0) for n in MARKET_FEATURE_NAMES]
            if USE_FUND and fund_feats and FUND_FEATURE_NAMES:
                fv = fund_feats.get(d, {}).get(code)
                feats += list(fv) if fv else [0] * len(FUND_FEATURE_NAMES)
            X_list.append(feats)
            y_list.append(label)
            date_list.append(k[idx][0])   # 样本日期（用于时间序列切分）
            code_list.append(code)
            # 真实收益（与标签同口径），供回测计算
            if ENTRY_MODE == "close":
                ep, xp = k[idx][4], k[idx + HOLD_DAYS][4]
            else:
                ep, xp = k[idx][1], k[idx + HOLD_DAYS - 1][4]
            ret_list.append((xp - ep) / ep if ep else 0.0)
            stock_samples += 1
        
        if stock_samples > 0:
            stock_counts.append((code, stock_samples))
        
        if (ci + 1) % 500 == 0:
            print(f"  进度: {ci+1}/{len(codes)} 只股票, 累计 {len(X_list)} 条样本")
    
    if not X_list:
        conn.close()
        print("⚠️  无有效样本，请检查 price_history 数据量")
        return np.array([]), np.array([]), [], [], [], [], []

    # 特征名（必须在 conn 关闭前取，取一只样本股票的稳定索引）
    feature_names = []
    for c in codes[:20]:
        kk = get_klines(c, conn)
        if len(kk) > MIN_HISTORY + 1:
            feature_names = list(compute_features(kk, MIN_HISTORY).keys())
            break

    conn.close()
    
    X = np.array(X_list, dtype=np.float32)
    y = np.array(y_list, dtype=np.int32)
    
    print(f"\n总样本: {len(X)} 条")
    print(f"正样本(盈利): {sum(y)} ({sum(y)/len(y)*100:.1f}%)")
    print(f"负样本(亏损): {len(y)-sum(y)} ({(len(y)-sum(y))/len(y)*100:.1f}%)")
    
    return X, y, feature_names, np.array(date_list), np.array(code_list), np.array(ret_list)

def train_model(X, y, feature_names, dates=None, split_mode="random"):
    """
    训练 XGBoost 分类器

    split_mode:
      'random' —— 随机切分（原做法）。⚠️ 对时间序列数据有信息泄漏风险：
                  同一只股票相邻交易日的样本高度相关，随机切分会让"未来样本"
                  进入训练集，导致 AUC 虚高（实测可比时间切分高出 20+ 个点）。
      'time'   —— 按日期切分：前 80% 时间训练，后 20% 时间测试。
                  这才是实盘真实场景（用历史预测未来），指标可信但会低得多。
    """
    print("\n===== 训练 XGBoost =====")
    
    # 划分训练/测试集
    if split_mode == "time" and dates is not None and len(dates) == len(X):
        order = np.argsort(dates)
        X, y = X[order], y[order]
        cut = int(len(X) * 0.8)
        X_train, X_test = X[:cut], X[cut:]
        y_train, y_test = y[:cut], y[cut:]
        print(f"  切分模式: 时间序列 | 训练≤{dates[order][cut-1]} "
              f"| 测试>{dates[order][cut]} ({len(y_test)} 条)")
    else:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )
        print(f"  切分模式: 随机（含时序泄漏风险）")
    
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
        'entry_mode': ENTRY_MODE,
        'split_mode': split_mode,
        'train_samples': len(y_train),
        'label_desc': (f"第idx日{'收盘' if ENTRY_MODE=='close' else '开盘'}买入 → "
                       f"第idx+{HOLD_DAYS}日收盘卖出"),
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
    d = os.path.dirname(os.path.abspath(__file__))
    model_path, eval_path = _paths(HOLD_DAYS, ENTRY_MODE)
    joblib.dump(model, model_path)
    with open(eval_path, 'w', encoding='utf-8') as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"\n模型已保存: {model_path}")
    print(f"评估已保存: {eval_path}")

    if AS_DEFAULT:
        default_model = os.path.join(d, "xgb_model.pkl")
        default_eval = os.path.join(d, "xgb_eval.json")
        joblib.dump(model, default_model)
        with open(default_eval, 'w', encoding='utf-8') as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
        print(f"已覆盖默认模型: {default_model}")

def main():
    global HOLD_DAYS, ENTRY_MODE, AS_DEFAULT, SPLIT_MODE, NO_FUTURE, USE_MARKET, USE_FUND, VALID_ONLY

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    AS_DEFAULT = "--default" in sys.argv
    SPLIT_MODE = "time" if "--time" in sys.argv else "random"
    NO_FUTURE = "--nofuture" in sys.argv
    if len(args) >= 1:
        HOLD_DAYS = int(args[0])
    if len(args) >= 2:
        ENTRY_MODE = args[1].lower()
    USE_MARKET = "--market" in sys.argv
    USE_FUND = "--fund" in sys.argv
    VALID_ONLY = "--validonly" in sys.argv
    if ENTRY_MODE not in ("open", "close"):
        ENTRY_MODE = "close"
    if HOLD_DAYS < 1:
        HOLD_DAYS = 1
    if ENTRY_MODE == "open" and HOLD_DAYS == 1:
        print("⚠️  entry=open 且 hold=1 意味着'当天开盘买、当天收盘卖'，A股 T+1 禁止！")
        print("    已自动改为 hold=2（今天开盘买、明天收盘卖）")
        HOLD_DAYS = 2

    print(f"全市场K线标注 + XGBoost 训练")
    print(f"  持仓天数: {HOLD_DAYS}  入场: {ENTRY_MODE}"
          f"  ({'收盘买入·尾盘买次日卖' if ENTRY_MODE=='close' else '开盘买入'})")
    print(f"  标签定义: 第idx日{'收盘' if ENTRY_MODE=='close' else '开盘'}买入 → "
          f"第idx+{HOLD_DAYS}日收盘卖出")
    print(f"  DB: {DB_PATH}")
    print("-" * 60)
    
    X, y, feature_names, dates, codes_arr, rets_arr = generate_samples()
    if NO_FUTURE:
        keep = [i for i, n in enumerate(feature_names) if n in OPEN_KNOWN_FEATURES]
        print(f"  ⚠️  剔除前视特征，仅保留开盘可知特征: {[feature_names[i] for i in keep]}")
        X = X[:, keep]
        feature_names = [feature_names[i] for i in keep]
    model, metrics = train_model(X, y, feature_names, dates, SPLIT_MODE)
    save_model(model, metrics)

if __name__ == '__main__':
    main()
