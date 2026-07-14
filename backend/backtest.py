"""
AI选股历史回测分析
基于 price_history 表中的K线数据，模拟每笔推荐的持仓过程
"""
import sqlite3
import json
import os
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_monitor.db")

def get_klines(code, db_conn):
    """获取某只股票的所有日K线，按日期排序"""
    rows = db_conn.execute(
        "SELECT trade_date, open, high, low, close, volume FROM price_history WHERE code=? ORDER BY trade_date",
        (code,)
    ).fetchall()
    return [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]


def simulate_hold(klines, entry_date, hold_days, entry_price=None):
    """
    模拟持仓过程
    从 entry_date 的下一个交易日起，持仓 hold_days 个交易日
    返回: {entry_date, entry_price, exit_date, exit_price, max_price, min_price, return_pct, max_drawdown_pct}
    """
    if not klines or hold_days <= 0:
        return None
    
    # 将 entry_date 转为纯日期用于匹配
    if ' ' in str(entry_date):
        entry_date = str(entry_date).split(' ')[0]
    
    # 找到 entry_date 之后的第一个交易日
    entry_idx = None
    for i, (d, *_) in enumerate(klines):
        if d >= entry_date:
            entry_idx = i
            break
    
    if entry_idx is None:
        return None
    
    # 价格 = 下一个交易日的开盘价（模拟实盘买入）
    entry_row = klines[entry_idx]
    buy_price = entry_price if entry_price else entry_row[1]  # open
    
    # 持仓期间的K线
    hold_klines = klines[entry_idx:entry_idx + hold_days]
    if not hold_klines:
        return None
    
    actual_hold = len(hold_klines)
    
    highs = [k[2] for k in hold_klines]
    lows = [k[3] for k in hold_klines]
    max_price = max(highs)
    min_price = min(lows)
    exit_price = hold_klines[-1][4]  # close of last day
    
    ret_pct = round((exit_price - buy_price) / buy_price * 100, 2)
    max_dd = round((buy_price - min_price) / buy_price * 100, 2)
    
    return {
        "entry_date": klines[entry_idx][0],
        "entry_price": buy_price,
        "exit_date": hold_klines[-1][0],
        "exit_price": exit_price,
        "max_price": max_price,
        "min_price": min_price,
        "return_pct": ret_pct,
        "max_drawdown_pct": max_dd,
        "actual_hold": actual_hold,
        "hit_target": "unknown",
        "hit_stop": "unknown",
    }


def get_csi300_benchmark(db_conn, start_date, end_date):
    """获取同期沪深300表现 (用510300 ETF近似)"""
    klines = get_klines("510300", db_conn)
    if not klines:
        # fallback: use 000300 沪深300指数
        klines = get_klines("000300", db_conn)
    if not klines:
        return None
    
    if ' ' in str(start_date):
        start_date = str(start_date).split(' ')[0]
    if ' ' in str(end_date):
        end_date = str(end_date).split(' ')[0]
    
    s_idx = e_idx = None
    for i, (d, *_) in enumerate(klines):
        if d >= start_date and s_idx is None:
            s_idx = i
        if d >= end_date and e_idx is None:
            e_idx = i
    
    if s_idx is None:
        return None
    if e_idx is None:
        e_idx = len(klines) - 1
    
    start_p = klines[s_idx][1]  # open
    end_p = klines[e_idx][4]    # close
    return round((end_p - start_p) / start_p * 100, 2)


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # 1. 获取所有选股记录
    picks = conn.execute('''
        SELECT * FROM ai_picks 
        WHERE rec_price > 0 
        ORDER BY pick_date, score DESC
    ''').fetchall()
    
    if not picks:
        print("无选股记录")
        return
    
    print(f"共 {len(picks)} 条选股记录")
    
    # 2. 按 pick_date 分组
    sessions = defaultdict(list)
    for p in picks:
        # 统一pick_date为日期
        pd = p['pick_date']
        if ' ' in str(pd):
            pd = str(pd).split(' ')[0]
        sessions[pd].append(dict(p))
    
    # 3. 逐笔回测
    results = []
    session_results = {}
    all_returns = []
    win_count = 0
    loss_count = 0
    flat_count = 0
    
    for session_date, session_picks in sorted(sessions.items()):
        session_returns = []
        session_details = []
        
        for p in session_picks:
            klines = get_klines(p['code'], conn)
            if not klines:
                continue
            
            # 使用 rec_price 作为入场价（推荐时的实时价格）
            # 处理 hold_days 可能是 '2-3' 这样的范围值
            hd = str(p['hold_days']).replace(' ', '')
            if '-' in hd:
                hd = int(hd.split('-')[-1])  # 取最大值
            else:
                hd = int(hd)
            
            # 处理 rec_price 可能是 '55.80-56.50' 这样的范围
            rp = str(p['rec_price']).replace(' ', '')
            if '-' in rp:
                # 取中间价
                parts = rp.split('-')
                rp = (float(parts[0]) + float(parts[1])) / 2
            else:
                rp = float(rp)
            
            sim = simulate_hold(klines, p['pick_date'], hd, entry_price=rp)
            if not sim:
                continue
            
            # 检查是否触发止损/止盈
            try:
                sl = float(str(p.get('stop_loss', '')).replace("'", ""))
                if sim['min_price'] <= sl:
                    sim['hit_stop'] = True
            except (ValueError, TypeError):
                pass
            try:
                tg = float(str(p.get('target', '')).replace("'", ""))
                if sim['max_price'] >= tg:
                    sim['hit_target'] = True
            except (ValueError, TypeError):
                pass
            
            # 统计
            ret = sim['return_pct']
            all_returns.append(ret)
            session_returns.append(ret)
            
            if ret > 0:
                win_count += 1
            elif ret < 0:
                loss_count += 1
            else:
                flat_count += 1
            
            detail = {
                'pick_date': str(p['pick_date']),
                'code': p['code'],
                'name': p['name'],
                'score': p['score'],
                'reason': p['reason'],
                'hold_days': p['hold_days'],
                'actual_hold': sim['actual_hold'],
                'entry_price': sim['entry_price'],
                'exit_price': sim['exit_price'],
                'return_pct': ret,
                'max_dd': sim['max_drawdown_pct'],
                'max_price': sim['max_price'],
                'min_price': sim['min_price'],
                'hit_target': sim['hit_target'],
                'hit_stop': sim['hit_stop'],
                'is_688': p['code'].startswith('688'),
            }
            session_details.append(detail)
            results.append(detail)
        
        if session_returns:
            session_results[session_date] = {
                'avg_return': round(sum(session_returns) / len(session_returns), 2),
                'win_rate': round(sum(1 for r in session_returns if r > 0) / len(session_returns) * 100, 1),
                'picks': session_details,
            }
    
    # 4. 整体统计
    total = win_count + loss_count + flat_count
    win_rate = round(win_count / total * 100, 1) if total else 0
    avg_return = round(sum(all_returns) / len(all_returns), 2) if all_returns else 0
    avg_max_dd = round(sum(r['max_dd'] for r in results) / len(results), 2) if results else 0
    
    # 5. 沪深300对比
    if sessions:
        first_date = min(sessions.keys())
        last_date = max(sessions.keys())
        csi300 = get_csi300_benchmark(conn, first_date, last_date)
    else:
        csi300 = None
    
    # 6. 按行业/特征分组分析
    by_hold = defaultdict(list)
    by_score = defaultdict(list)
    by_688 = {'688': [], 'non_688': []}
    
    for r in results:
        by_hold[r['hold_days']].append(r['return_pct'])
        score_bucket = (r['score'] // 5) * 5
        by_score[score_bucket].append(r['return_pct'])
        key = '688' if r['is_688'] else 'non_688'
        by_688[key].append(r['return_pct'])
    
    # 7. 按代码去重后的综合表现
    stock_returns = defaultdict(list)
    for r in results:
        stock_returns[r['code']].append(r['return_pct'])
    
    top_stocks = sorted([(c, round(sum(v)/len(v),2), len(v)) for c, v in stock_returns.items() if len(v) >= 2],
                        key=lambda x: x[1], reverse=True)[:10]
    worst_stocks = sorted([(c, round(sum(v)/len(v),2), len(v)) for c, v in stock_returns.items() if len(v) >= 2],
                          key=lambda x: x[1])[:10]
    
    conn.close()
    
    # 8. 生成报告
    generate_report(
        results, session_results, total, win_rate, avg_return, avg_max_dd,
        csi300, all_returns, by_hold, by_score, by_688,
        top_stocks, worst_stocks
    )


def generate_report(results, session_results, total, win_rate, avg_return, avg_max_dd,
                    csi300, all_returns, by_hold, by_score, by_688,
                    top_stocks, worst_stocks):
    """生成HTML报告"""
    
    # 数据转为JSON
    ret_js = json.dumps(all_returns)
    hold_labels = json.dumps([f"{k}天" for k in sorted(by_hold.keys(), key=lambda x: int(x) if str(x).isdigit() else 99)])
    hold_values = json.dumps([round(sum(v)/len(v), 2) for v in [by_hold[k] for k in sorted(by_hold.keys(), key=lambda x: int(x) if str(x).isdigit() else 99)]])
    score_labels = json.dumps([str(k) for k in sorted(by_score.keys())])
    score_values = json.dumps([round(sum(v)/len(v), 2) for v in [by_score[k] for k in sorted(by_score.keys())]])
    session_dates = json.dumps([str(k) for k in session_results.keys()])
    session_avgs = json.dumps([v['avg_return'] for v in session_results.values()])
    session_wrs = json.dumps([v['win_rate'] for v in session_results.values()])
    
    # 688分析
    non_688 = [r for r in results if not r['is_688']]
    _688 = [r for r in results if r['is_688']]
    non_wr = round(sum(1 for r in non_688 if r['return_pct']>0)/len(non_688)*100,1) if non_688 else 0
    _688_wr = round(sum(1 for r in _688 if r['return_pct']>0)/len(_688)*100,1) if _688 else 0
    non_avg = round(sum(r['return_pct'] for r in non_688)/len(non_688),2) if non_688 else 0
    _688_avg = round(sum(r['return_pct'] for r in _688)/len(_688),2) if _688 else 0
    
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI选股回测分析报告</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;font-size:14px;color:#1a1a2e;background:#f0f2f5;line-height:1.6}}
.container{{max-width:1200px;margin:0 auto;padding:40px 20px}}
h1{{font-size:28px;font-weight:700;margin-bottom:8px}}
.subtitle{{font-size:14px;color:#606770;margin-bottom:32px}}
.card{{background:#fff;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.06);padding:24px;margin-bottom:20px}}
.card h2{{font-size:18px;margin-bottom:16px;border-bottom:2px solid #3b82f6;padding-bottom:8px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:24px}}
.stat-card{{background:#fff;border-radius:10px;padding:20px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.stat-value{{font-size:32px;font-weight:700}}
.stat-label{{font-size:12px;color:#606770;margin-top:4px}}
.up{{color:#e53e3e}}.down{{color:#38a169}}.neutral{{color:#718096}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:#f8f9fa;padding:10px 12px;text-align:left;font-weight:600;border-bottom:2px solid #e4e6eb;white-space:nowrap}}
td{{padding:8px 12px;border-bottom:1px solid #f0f2f5}}
tr:hover td{{background:#f8f9fa}}
.chart{{width:100%;height:350px}}
.badge{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}}
.badge-win{{background:#fed7d7;color:#e53e3e}}
.badge-loss{{background:#c6f6d5;color:#38a169}}
.badge-688{{background:#fefcbf;color:#975a16}}
.insight{{background:#eff6ff;border-left:3px solid #3b82f6;padding:14px 18px;border-radius:6px;margin:16px 0;font-size:13px}}
.insight strong{{color:#1e40af}}
.warn{{background:#fff3bf;border-left:3px solid #f59e0b}}
.section-divider{{border-top:2px solid #e4e6eb;margin:24px 0}}
</style>
</head>
<body>
<div class="container">
<h1>📊 AI智能选股 · 回测分析报告</h1>
<div class="subtitle">
    样本数: {total} 笔推荐 | 时间跨度: {min(s['pick_date'] for s in results)} ~ {max(s['pick_date'] for s in results)}
    | 基准: 沪深300 ETF (510300)
</div>

<!-- 核心指标 -->
<div class="stats">
    <div class="stat-card">
        <div class="stat-value {"up" if avg_return > 0 else "down" if avg_return < 0 else "neutral"}">{avg_return:+.1f}%</div>
        <div class="stat-label">平均收益率</div>
    </div>
    <div class="stat-card">
        <div class="stat-value {"up" if win_rate > 50 else "neutral"}">{win_rate}%</div>
        <div class="stat-label">胜率 ({sum(1 for r in results if r["return_pct"]>0)}/{total})</div>
    </div>
    <div class="stat-card">
        <div class="stat-value down">{avg_max_dd:.1f}%</div>
        <div class="stat-label">平均最大回撤</div>
    </div>
    <div class="stat-card">
        <div class="stat-value {"up" if (csi300 or 0) < avg_return else "down"}">{(csi300 or 0):+.1f}%</div>
        <div class="stat-label">同期沪深300</div>
    </div>
    <div class="stat-card">
        <div class="stat-value {"up" if (avg_return - (csi300 or 0)) > 0 else "down"}">{(avg_return - (csi300 or 0)):+.1f}%</div>
        <div class="stat-label">超额收益 α</div>
    </div>
</div>

<!-- 科创板分析 -->
<div class="card">
    <h2>🔬 科创板 vs 非科创板对比</h2>
    <div class="stats">
        <div class="stat-card">
            <div class="stat-value">{non_avg:+.1f}%</div>
            <div class="stat-label">非科创板平均收益 ({len(non_688)}笔)</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{non_wr}%</div>
            <div class="stat-label">非科创板胜率</div>
        </div>
        <div class="stat-card">
            <div class="stat-value bad" style="color:#e53e3e">{_688_avg:+.1f}%</div>
            <div class="stat-label">科创板平均收益 ({len(_688)}笔)</div>
        </div>
        <div class="stat-card">
            <div class="stat-value" style="color:#718096">{_688_wr}%</div>
            <div class="stat-label">科创板胜率</div>
        </div>
    </div>
</div>

<!-- 持仓天数分析 -->
<div class="card">
    <h2>📅 持仓天数 vs 收益率</h2>
    <div id="chart-hold" class="chart"></div>
</div>

<!-- 评分 vs 收益率 -->
<div class="card">
    <h2>⭐ 评分区间 vs 平均收益率</h2>
    <div id="chart-score" class="chart"></div>
</div>

<!-- 各批次表现 -->
<div class="card">
    <h2>📈 各批次选股表现</h2>
    <div id="chart-session" class="chart"></div>
</div>

<!-- 收益分布 -->
<div class="card">
    <h2>📊 收益分布</h2>
    <div id="chart-dist" class="chart"></div>
</div>

<!-- 表现最好/最差 -->
<div class="card">
    <h2>🏆 高频推荐股票排行（≥2次）</h2>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div>
            <h4 style="color:#e53e3e;margin-bottom:8px">TOP 10 最佳</h4>
            <table>
                <tr><th>代码</th><th>名称</th><th>次数</th><th>平均收益</th></tr>
                {''.join(f'<tr><td>{c}</td><td>--</td><td>{n}</td><td class="up">{r:+.1f}%</td></tr>' for c,r,n in top_stocks)}
            </table>
        </div>
        <div>
            <h4 style="color:#38a169;margin-bottom:8px">TOP 10 最差</h4>
            <table>
                <tr><th>代码</th><th>名称</th><th>次数</th><th>平均收益</th></tr>
                {''.join(f'<tr><td>{c}</td><td>--</td><td>{n}</td><td class="down">{r:+.1f}%</td></tr>' for c,r,n in worst_stocks)}
            </table>
        </div>
    </div>
</div>

<!-- 详细记录 -->
<div class="card">
    <h2>📋 全部推荐明细（最近100笔）</h2>
    <div style="overflow-x:auto">
    <table>
        <tr>
            <th>日期</th><th>代码</th><th>名称</th><th>评分</th>
            <th>买入价</th><th>退出价</th><th>收益率</th><th>最大回撤</th>
            <th>持仓</th><th>止损</th><th>止盈</th><th>688</th>
        </tr>
        {''.join(f'''
        <tr>
            <td>{r['pick_date'][:10]}</td>
            <td>{r['code']}</td>
            <td>{r['name']}</td>
            <td>{r['score']}</td>
            <td>{r['entry_price']:.2f}</td>
            <td>{r['exit_price']:.2f}</td>
            <td class="{"up" if r['return_pct']>0 else "down" if r['return_pct']<0 else ""}">{r['return_pct']:+.2f}%</td>
            <td class="down">{r['max_dd']:.1f}%</td>
            <td>{r['actual_hold']}/{r['hold_days']}d</td>
            <td><span class="badge badge-{'loss' if r['hit_stop'] else 'win'}">{"⚠️" if r['hit_stop'] else "✓"}</span></td>
            <td><span class="badge badge-{'win' if r['hit_target'] else 'loss'}">{"🎯" if r['hit_target'] else "✗"}</span></td>
            <td>{'🔬' if r['is_688'] else ''}</td>
        </tr>''' for r in list(reversed(results))[:100])}
    </table>
    </div>
</div>

<!-- 改进建议 -->
<div class="card">
    <h2>💡 改进建议</h2>
    {generate_suggestions(by_hold, by_score, by_688, non_avg, _688_avg, non_wr, _688_wr, avg_return, win_rate, csi300, avg_max_dd, results)}
</div>

</div>

<script>
const darkTheme = false;

function makeChart(id, option) {{
    const dom = document.getElementById(id);
    if (!dom) return;
    const chart = echarts.init(dom, darkTheme ? 'dark' : undefined);
    chart.setOption(option);
    window.addEventListener('resize', () => chart.resize());
}}

// 持仓天数 vs 收益
makeChart('chart-hold', {{
    tooltip: {{trigger:'axis'}},
    xAxis: {{type:'category', data:{hold_labels}}},
    yAxis: {{type:'value', name:'平均收益率 %', axisLabel:{{formatter:'{{value}}%'}}}},
    series: [{{name:'平均收益', type:'bar', data:{hold_values},
        itemStyle:{{color:function(p){{return p.value>=0?'#e53e3e':'#38a169'}}}}
    }}]
}});

// 评分 vs 收益
makeChart('chart-score', {{
    tooltip: {{trigger:'axis'}},
    xAxis: {{type:'category', data:{score_labels}, name:'评分区间'}},
    yAxis: {{type:'value', name:'平均收益率 %', axisLabel:{{formatter:'{{value}}%'}}}},
    series: [{{name:'平均收益', type:'bar', data:{score_values},
        itemStyle:{{color:'#3b82f6'}}
    }}]
}});

// 批次表现
makeChart('chart-session', {{
    tooltip: {{trigger:'axis'}},
    legend: {{data:['平均收益','胜率']}},
    xAxis: {{type:'category', data:{session_dates}, axisLabel:{{rotate:45,fontSize:10}}}},
    yAxis: [
        {{type:'value', name:'收益率 %', axisLabel:{{formatter:'{{value}}%'}}}},
        {{type:'value', name:'胜率 %', max:100, axisLabel:{{formatter:'{{value}}%'}}}}
    ],
    series: [
        {{name:'平均收益', type:'bar', data:{session_avgs}, yAxisIndex:0,
            itemStyle:{{color:function(p){{return p.value>=0?'#e53e3e':'#38a169'}}}}}},
        {{name:'胜率', type:'line', data:{session_wrs}, yAxisIndex:1,
            lineStyle:{{color:'#3b82f6'}}, itemStyle:{{color:'#3b82f6'}}}}
    ]
}});

// 收益分布
const retData = {ret_js};
const bins = [-15,-10,-8,-6,-4,-2,0,2,4,6,8,10,15,20,999];
const counts = new Array(bins.length-1).fill(0);
retData.forEach(v => {{
    for(let i=0;i<bins.length-1;i++) {{
        if(v >= bins[i] && v < bins[i+1]) {{ counts[i]++; break; }}
    }}
}});
const binLabels = bins.slice(0,-1).map((v,i) => (v>=0?'+':'')+v+'~'+(bins[i+1]>=999?'∞':bins[i+1])+'%');

makeChart('chart-dist', {{
    tooltip: {{trigger:'axis'}},
    xAxis: {{type:'category', data:binLabels, axisLabel:{{rotate:45,fontSize:9}}}},
    yAxis: {{type:'value', name:'笔数'}},
    series: [{{name:'笔数', type:'bar', data:counts,
        itemStyle:{{color:function(p){{const v=parseFloat(p.name); return v<0?'#38a169':'#e53e3e'}}}}
    }}]
}});
</script>
</body>
</html>'''

    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AI选股回测分析报告.html")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n报告已生成: {report_path}")


def generate_suggestions(by_hold, by_score, by_688, non_avg, _688_avg, non_wr, _688_wr, avg_return, win_rate, csi300, avg_max_dd, results):
    """生成改进建议"""
    suggestions = []
    
    # 1. 科创板
    if len(by_688.get('688', [])) > 0:
        if _688_avg < non_avg and _688_wr < non_wr:
            suggestions.append(f'<strong>✅ 过滤科创板正确</strong>：科创板推荐平均收益 {_688_avg:+.1f}%(胜率{_688_wr}%)，远低于非科创板的 {non_avg:+.1f}%(胜率{non_wr}%)。当前已全局过滤688xxx，此决策被回测数据支持。')
        else:
            suggestions.append(f'<strong>⚠️ 重新审视科创板</strong>：科创板平均收益 {_688_avg:+.1f}%，相比非科创板 {non_avg:+.1f}% 并未明显劣势。如需覆盖科创板，需评估账户权限。')
    
    # 2. 持仓天数
    hold_analysis = [(k, round(sum(v)/len(v),2), len(v)) for k,v in by_hold.items() if len(v)>=3]
    best_hold = max(hold_analysis, key=lambda x: x[1]) if hold_analysis else None
    worst_hold = min(hold_analysis, key=lambda x: x[1]) if hold_analysis else None
    if best_hold and worst_hold:
        suggestions.append(f'<strong>📅 优化持仓周期</strong>：最优持仓 {best_hold[0]} 天(均收益{best_hold[1]:+.1f}%)，最差 {worst_hold[0]} 天(均收益{worst_hold[1]:+.1f}%)。建议优先选择 {best_hold[0]} 天持仓周期的股票。')
    
    # 3. 评分与收益相关
    score_analysis = [(k, round(sum(v)/len(v),2)) for k,v in by_score.items() if len(v)>=3]
    if len(score_analysis) >= 2:
        high_scores = max(score_analysis, key=lambda x: x[0])
        low_scores = min(score_analysis, key=lambda x: x[0])
        if high_scores[1] < low_scores[1]:
            suggestions.append(f'<strong>⚠️ 高评分≠高收益</strong>：评分{high_scores[0]}+区间的平均收益{high_scores[1]:+.1f}%低于{low_scores[0]}分区间{low_scores[1]:+.1f}%。AI评分标准可能需要调整，考虑降低追高权重、增加估值因子。')
    
    # 4. 止损分析
    hit_stop_count = sum(1 for r in results if r['hit_stop'] == True)
    if hit_stop_count > 0:
        suggestions.append(f'<strong>🛡 止损有效性</strong>：{hit_stop_count}/{len(results)} 笔触及止损线，建议将止损比例从固定值改为ATR动态止损（如2×ATR），减少过早止损。')
    
    # 5. benchmark对比
    if csi300 is not None:
        excess = avg_return - csi300
        if excess > 0:
            suggestions.append(f'<strong>📈 超额收益</strong>：相比同期沪深300({csi300:+.1f}%)，策略超额收益 {excess:+.1f}%，表现{"显著优于" if excess > 5 else "略优于"}大盘。')
        else:
            suggestions.append(f'<strong>📉 跑输大盘</strong>：策略收益率 {avg_return:+.1f}% 低于沪深300的 {csi300:+.1f}%，超额收益 {excess:+.1f}%。建议加强市场择时，在大盘下跌时空仓或减少推荐数量。')
    
    # 6. 回撤
    if avg_max_dd > 10:
        suggestions.append(f'<strong>🔻 回撤过大</strong>：平均最大回撤 {avg_max_dd:.1f}%，风险控制有待加强。建议增加仓位管理（单票不超过20%）、设置组合层面止损。')
    
    # 7. 胜率
    if win_rate < 50:
        suggestions.append(f'<strong>🎲 胜率待提升</strong>：当前胜率 {win_rate}% 不足50%，建议增加选股确认信号（如MACD金叉+量能放大双信号），减少误判。')
    
    # 8. 行业集中度
    from collections import Counter
    name_words = Counter()
    for r in results:
        for w in ['半导体','芯片','封装','PCB','新能源','医药','材料','化工','金融','农业','消费','机器人','设备']:
            if w in r['reason']:
                name_words[w] += 1
    
    top_sectors = name_words.most_common(3)
    if top_sectors:
        sector_str = '、'.join(f'{k}({v}次)' for k,v in top_sectors)
        suggestions.append(f'<strong>🏭 行业集中度</strong>：推荐集中在 {sector_str}。建议增加行业分散度，避免单一板块系统性风险。如半导体板块整体调整将导致多笔推荐同时亏损。')
    
    return '\n'.join(f'<div class="insight {"warn" if "⚠" in s else ""}">{s}</div>' for s in suggestions)


if __name__ == '__main__':
    main()
