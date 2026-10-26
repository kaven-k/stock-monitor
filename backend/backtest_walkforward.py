"""
严格 Walk-Forward（滚动前进）回测 —— 绝对不使用未来数据

★ 为什么需要它：
  随机切分 train_test_split 对时间序列是【作弊】。实测同一模型：
      随机切分 AUC 77.3%  vs  时间切分 AUC 53.0%
  原因：① 同一只股票相邻交易日样本跨越切分边界；
        ② 市场情绪是全市场共享的日级特征，随机切分下"同一天的其他股票"
           会把当日市场状态泄漏进训练集。
  两者都是用"当时不可能知道的信息"在做预测，实盘必然失效。

★ 本回测的严格性：
  第 i 段：只用【第 i 段之前】的全部数据训练，在第 i 段上预测。
  训练时从未见过测试期的任何一天，与实盘"用历史预测未来"完全一致。

★ 评估方式（贴近实盘，而非看 AUC）：
  每个交易日 T 收盘：
    - 给当日全市场候选打分，取概率最高的 Top K 只
    - 按「T 日收盘买入 → T+1 日收盘卖出」计算真实收益
  汇总：累计收益、日胜率、平均收益，并与基准（全市场等权 / 随机选）对比

用法：
    python3 backtest_walkforward.py              # 默认 4 段, Top10
    python3 backtest_walkforward.py 5 10 --market
"""
import os
import sys
import json
import numpy as np
from xgboost import XGBClassifier
from sklearn.metrics import roc_auc_score

import train_xgb as T

DB_PATH = T.DB_PATH
N_SPLITS = 4
TOP_K = 10
RANDOM_SEED = 42


# 可买入约束：T日收盘买入，必须排除已涨停/接近涨停的股票（封板买不进）
# 实测：涨停股(>=9.8%)次日平均 +1.10%，但收盘根本无法买入；
#       不排除的话回测会虚高到"日收益3.3%、累计400倍"的荒谬水平。
NEAR_LIMIT_PCT = 7.0
# 单日双边交易成本（印花税0.05%卖出 + 佣金约0.05%×2 + 冲击成本）
# 每日全额换手 Top K，故按日计提
COST_PER_DAY = 0.0015


def build_model(y_train):
    pos = (1 - y_train.mean()) / max(y_train.mean(), 1e-6)
    return XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, scale_pos_weight=pos,
        objective="binary:logistic", eval_metric="auc",
        random_state=RANDOM_SEED, n_jobs=-1,
    )


def run(n_splits=N_SPLITS, top_k=TOP_K, use_market=True, use_fund=False,
        hold=1, entry="close"):
    T.HOLD_DAYS = hold
    T.ENTRY_MODE = entry
    T.USE_MARKET = use_market
    T.USE_FUND = use_fund
    T.VALID_ONLY = use_market   # 只用有效交易日

    print("=" * 68)
    print("Walk-Forward 严格回测（无未来函数）")
    print(f"  标签: T日{'收盘' if entry=='close' else '开盘'}买入 → T+{hold}日收盘卖出")
    print(f"  特征: 个股量价14" + (" + 市场情绪6" if use_market else "")
          + (" + 资金流/板块7" if use_fund else ""))
    print(f"  分段: {n_splits} 段滚动前进 | 每日选 Top{top_k}")
    print("=" * 68)

    X, y, names, dates, codes, rets = T.generate_samples()
    if len(X) == 0:
        print("样本为空")
        return None

    # 按日期排序（全局时间顺序）
    order = np.argsort(dates)
    X, y, dates, codes, rets = X[order], y[order], dates[order], codes[order], rets[order]
    uniq_dates = np.unique(dates)
    print(f"\n样本 {len(X)} 条 | {len(uniq_dates)} 个交易日 "
          f"({uniq_dates[0]} ~ {uniq_dates[-1]})")

    # 按交易日数量等分时间段
    splits = np.array_split(uniq_dates, n_splits)
    results = []
    all_daily = []

    for i in range(1, n_splits):
        train_end = splits[i - 1][-1]     # 训练截止日（含）
        test_dates = set(splits[i])
        tr_mask = dates <= train_end
        te_mask = np.isin(dates, list(test_dates))
        if tr_mask.sum() < 10000 or te_mask.sum() < 100:
            continue

        print(f"\n--- 第{i}段 --- 训练≤{train_end} ({tr_mask.sum()}条) | "
              f"测试 {splits[i][0]}~{splits[i][-1]} ({te_mask.sum()}条)")

        model = build_model(y[tr_mask])
        model.fit(X[tr_mask], y[tr_mask], verbose=False)
        proba = model.predict_proba(X[te_mask])[:, 1]

        # 可买入过滤（先算，AUC 与选股都基于该子集）
        seg_X = X[te_mask]
        if "today_chg" in names:
            chg_col = names.index("today_chg")
            buyable = seg_X[:, chg_col] < NEAR_LIMIT_PCT
        else:
            buyable = np.ones(len(seg_X), dtype=bool)

        # AUC 只在【可买入子集】上计算 —— 全体样本含涨停股，会扭曲真实选股能力
        auc = (roc_auc_score(y[te_mask][buyable], proba[buyable])
               if buyable.sum() > 100 else float("nan"))

        # 逐日选 Top K
        seg_dates = dates[te_mask]
        seg_codes = codes[te_mask]
        seg_rets = rets[te_mask]
        seg_proba = proba

        skipped_limited = int((~buyable).sum())
        print(f"    AUC: {auc*100:.1f}% | 剔除涨停/接近涨停 {skipped_limited} 只 "
              f"({skipped_limited/max(1,len(buyable))*100:.1f}%)")
        for d in np.unique(seg_dates):
            m = (seg_dates == d) & buyable      # 只从"可买入"股票中选
            if m.sum() < top_k:
                continue
            p, r = seg_proba[m], seg_rets[m]
            idx = np.argsort(p)[::-1][:top_k]
            top_ret = float(np.mean(r[idx]))
            mkt_ret = float(np.mean(r))          # 基准：当日全市场等权
            # 随机对照：同一可买入池内随机选 top_k，重复20次取均值
            rng = np.random.default_rng(RANDOM_SEED)
            rnd = [float(np.mean(r[rng.choice(len(r), top_k, replace=False)]))
                   for _ in range(20)]
            rand_ret = float(np.mean(rnd))
            all_daily.append({
                "date": str(d), "top_ret": top_ret,
                "mkt_ret": mkt_ret, "excess": top_ret - mkt_ret,
                "rand_ret": rand_ret,
            })
        results.append({"split": i, "auc": round(auc * 100, 1),
                        "train_n": int(tr_mask.sum()), "test_n": int(te_mask.sum())})

    if not all_daily:
        print("无有效回测日")
        return None

    # 汇总
    tr = np.array([d["top_ret"] for d in all_daily])
    mr = np.array([d["mkt_ret"] for d in all_daily])
    ex = np.array([d["excess"] for d in all_daily])
    rr = np.array([d.get("rand_ret", 0) for d in all_daily])
    n_days = len(all_daily)

    cum_top = float(np.prod(1 + tr) - 1)
    cum_mkt = float(np.prod(1 + mr) - 1)
    win_rate = float((tr > 0).mean())
    win_vs_mkt = float((ex > 0).mean())

    summary = {
        "days": n_days,
        "topk_daily_mean": round(float(tr.mean()) * 100, 3),
        "market_daily_mean": round(float(mr.mean()) * 100, 3),
        "excess_daily_mean": round(float(ex.mean()) * 100, 3),
        "cum_return_topk": round(cum_top * 100, 2),
        "cum_return_market": round(cum_mkt * 100, 2),
        "win_rate": round(win_rate * 100, 1),
        "beat_market_rate": round(win_vs_mkt * 100, 1),
        "cost_per_day": round(COST_PER_DAY * 100, 3),
        "net_daily_mean": round((float(tr.mean()) - COST_PER_DAY) * 100, 3),
        "cum_return_net": round((float(np.prod(1 + tr - COST_PER_DAY)) - 1) * 100, 2),
        "random_daily_mean": round(float(rr.mean()) * 100, 3),
        "excess_vs_random": round(float((tr - rr).mean()) * 100, 3),
        "splits": results,
        "config": {"hold": hold, "entry": entry, "top_k": top_k,
                   "market": use_market, "fund": use_fund},
    }

    print("\n" + "=" * 68)
    print("回测结果（严格无未来函数）")
    print("=" * 68)
    print(f"  回测天数           : {n_days} 个交易日")
    print(f"  Top{top_k} 日均收益     : {summary['topk_daily_mean']}%")
    print(f"  全市场等权日均     : {summary['market_daily_mean']}%")
    print(f"  超额收益(日均)     : {summary['excess_daily_mean']}%")
    print(f"  累计收益 Top{top_k}      : {summary['cum_return_topk']}%")
    print(f"  累计收益 基准      : {summary['cum_return_market']}%")
    print(f"  日胜率(盈利天数)   : {summary['win_rate']}%")
    print(f"  跑赢市场天数占比   : {summary['beat_market_rate']}%")
    print(f"  随机选{top_k}只日均     : {summary['random_daily_mean']}%  "
          f"(模型超额 {summary['excess_vs_random']}%)")
    print(f"  ---- 计入交易成本({summary['cost_per_day']}%/日) ----")
    print(f"  净日均收益         : {summary['net_daily_mean']}%")
    print(f"  净累计收益         : {summary['cum_return_net']}%")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out}")
    return summary


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = int(args[0]) if len(args) >= 1 else N_SPLITS
    k = int(args[1]) if len(args) >= 2 else TOP_K
    run(n_splits=n, top_k=k,
        use_market="--market" in sys.argv or "--nomarket" not in sys.argv,
        use_fund="--fund" in sys.argv)
