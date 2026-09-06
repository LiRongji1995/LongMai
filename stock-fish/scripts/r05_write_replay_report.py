"""Render the Chinese R05 report from executed artifacts, without preset metrics."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from stockfish_core.yaogu.replay_statistics import PV,FOCUS,BASELINE

NAMES={PV:'完整PV',FOCUS:'独立焦点',BASELINE:'合格池等权',
       'AMOUNT_STRENGTH_TOP5_DIAGNOSTIC':'单因子A前5','AMOUNT_STRENGTH_TOP10_DIAGNOSTIC':'单因子A前10',
       'AMOUNT_STRENGTH_TOP20_DIAGNOSTIC':'单因子A前20','DAILY_V1':'冻结DAILY','DNA_RERANK_V1':'DNA','THEME_CONTEXT_V1':'题材'}


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def pct(v):return '不可计算' if v is None else f'{v*100:.2f}%'
def table(headers,rows):return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join('---' for _ in headers)+' |',*['| '+' | '.join(map(str,r))+' |' for r in rows]])


def report(root):
    plan=read(root/'replay_plan.json');attempts=read(root/'historical_attempts_20_sessions.json');primary=read(root/'statistics_primary_5_sessions.json')
    dates=read(root/'statistics_by_date.json');paper=read(root/'paper_execution_summary.json');supplement=read(root/'statistics_mature_supplement.json')
    index={(r['strategy_id'],r['horizon']):r for r in primary};by_date={r['observation_date']:{(s['strategy_id'],s['horizon']):s for s in r['statistics']} for r in dates}
    primary_attempts=[r for r in attempts if r['observation_date'] in plan['primary_sessions']]
    lines=['# 过去一周实证回放与胜率口径报告','',f"共同市场信息截止：{plan['execution_cutoff']}。主窗口：{plan['primary_sessions'][0]} 至 {plan['primary_sessions'][-1]}；总尝试窗口：{plan['attempt_sessions'][0]} 至 {plan['attempt_sessions'][-1]}，共 {len(attempts)} 个交易日。",'',
        '**本次是现在下载历史资料后的重建，不是当时存档的实盘或前瞻验证。** 最近五日使用已认证的沪深主板记录；8月31日前仅认证深圳范围。未知身份不进入资格池，整体仍标记 LIMITED_SCOPE。原始采集时钟保留，未用今日ST、今日名称或今日成员代替过去。', '',
        f"冻结实现：`{read(root/plan['primary_sessions'][0]/'rank.json')['implementation_version']}`，保持 R05；spec hash `{plan['selection_identity']['spec_hash']}`。完整 PV 和焦点都是动态全部保存，不设5/20名额。",'',
        table(['信号日','可评估资格池','完整PV','焦点','PV次日上涨/有效','PV次日均值','焦点次日上涨/有效','焦点次日均值'],
              [[r['observation_date'],r.get('eligible_complete','不可用'),r.get('pv_count','不可用'),r.get('focus_count','不可用'),
                f"{by_date[r['observation_date']][(PV,1)]['pooled_signals']['positive']}/{by_date[r['observation_date']][(PV,1)]['pooled_signals']['mature_valid']}",pct(by_date[r['observation_date']][(PV,1)]['pooled_signals']['mean_return']),
                f"{by_date[r['observation_date']][(FOCUS,1)]['pooled_signals']['positive']}/{by_date[r['observation_date']][(FOCUS,1)]['pooled_signals']['mature_valid']}",pct(by_date[r['observation_date']][(FOCUS,1)]['pooled_signals']['mean_return'])] for r in primary_attempts]),'',
        '表中上涨/有效的分母只包含该日期、该指标已成熟且可比的逻辑信号；0/0表示尚无有效结果，不表示0%上涨率。主窗口h=5/10/20均未成熟，不能生成完整周胜率。', '']
    lines += ['同条件次日价格对照（主窗口已成熟部分）：','',
        table(['范围','成熟有效数','上涨率','平均价格变化','日期等权平均变化','相对共同池日期等权均值差'],
              [[NAMES[sid],(row:=index[(sid,1)])['pooled_signals']['mature_valid'],pct(row['pooled_signals']['up_rate']),pct(row['pooled_signals']['mean_return']),pct(row['pooled_signals']['date_equal_mean_return']),pct(row['date_equal_excess_mean'])] for sid in (PV,FOCUS,BASELINE)]),'']
    primary_paper=[r for r in paper if r['window']=='PRIMARY_5' and r['horizon']==1 and r['strategy_id'] in (PV,FOCUS)]
    lines += [table(['模拟口径：持有h=1','总信号','已到尝试日','买入可评估','已平仓','未核验/未成交/未成熟等状态','30bp模拟胜率'],
                    [[NAMES[r['strategy_id']],r['total_signals'],r['attempted'],r['evaluable'],r['closed'],str(r['states']),pct(r['cost_scenarios']['30']['win_rate'])] for r in primary_paper]),'',
              '**未取得经核验的历史涨跌停价格，不能确认成交，模拟胜率与已平仓均值不可计算。** 这些尝试完整保留为 FILL_UNVERIFIED/NOT_MATURED 等状态；价格上涨率继续独立计算。`MARKET_EDGE=NOT_ESTABLISHED`。','',
              '## 成熟度与同条件价格对照','',
              '价格标签为T收盘至T+h同一复权基准收盘的变化，不声称T收盘能买到。零收益计入有效分母但不算上涨。最高/最低仅为上探/下探界。完整逻辑标签与修订选择分别见 [outcomes.csv](outcomes.csv)、[outcomes.jsonl](outcomes.jsonl)、[revision_selection.jsonl](revision_selection.jsonl)。','']
    for sid in (PV,FOCUS,BASELINE):
        rows=[index[(sid,h)] for h in plan['horizons']]
        lines += [f'### {NAMES[sid]}：主窗口合并逻辑信号','',table(['h','总信号','证券数','有信号日期','成熟有效','未成熟','缺数/不可比','涨/平/跌','上涨率','均值','中位数','P10'],
            [[r['horizon'],(p:=r['pooled_signals'])['signals'],p['unique_instruments'],p['signal_dates'],p['mature_valid'],p['not_matured'],p['missing_or_incomparable'],f"{p['positive']}/{p['zero']}/{p['negative']}",pct(p['up_rate']),pct(p['mean_return']),pct(p['median_return']),pct(p['p10_return'])] for r in rows]),'']
    lines += ['### 日期等权与共同池','',
        '共同池是在每个T按生产资格核验且六个连续交易日的价格/成交额字段完整的证券集合，在PV筛选门槛之前确定。基准不是各策略选中证券的交集。单因子按A精确值降序、代码升序预先定义，前5/10/20只用于排序诊断。主窗口的所有策略使用同一日期、h和价格基准比较；没有搜索阈值。','',
        table(['策略','h','有效结果日期','日期等权上涨率','日期等权均值','相对资格池日期等权均值差','逐日均值范围'],
            [[NAMES[r['strategy_id']],r['horizon'],len((p:=r['pooled_signals'])['valid_result_dates']),pct(p['date_equal_up_rate']),pct(p['date_equal_mean_return']),pct(r['date_equal_excess_mean']),str(p['date_mean_range'])] for r in primary if r['strategy_id'] in NAMES and r['horizon'] in (1,2,3,4) and r['strategy_id'] not in ('DAILY_V1','DNA_RERANK_V1','THEME_CONTEXT_V1')]),'',
        '缺少有效标签的日期不被当成零收益；它们保留在日期审计和成熟度矩阵中。日期等权的有效日期数始终公开。五个信号日期及其重叠标签不足以稳定估计优势，不能把重复股票视为相互独立试验。','',
        '### episode首信号口径','',
        '正式 TRACKING_POLICY_PILOT_V0.1 只为焦点建立研究周期。完整PV的episode统计只覆盖其中与正式焦点周期关联的子集，不能当作完整PV的可比替代分母；非PV策略没有独立episode政策，明确不可计算。真实首信号从全部已知周期历史确定，只在其真实开始日在所报窗口内时计入；此前开始、本周持续推荐的周期另列。原首日标签缺失时不会用后来的重复推荐替代。观察历史左界是本次20日窗口，仍存在左截断。','',
        table(['策略','h','窗口涉及周期','此前已开始','无episode信号数','真实首信号有效数','上涨率','均值','窗口内首项有效数','窗口内首项均值'],
              [[NAMES[r['strategy_id']],r['horizon'],r['episode_count'],r['started_before_window_episode_count'],r['signals_without_episode'],r['episode_first_signal']['mature_valid'],pct(r['episode_first_signal']['up_rate']),pct(r['episode_first_signal']['mean_return']),r['episode_window_first_signal']['mature_valid'],pct(r['episode_window_first_signal']['mean_return'])] for r in primary if r['strategy_id'] in (PV,FOCUS,BASELINE) and r['horizon'] in (1,5,20)]),'',
        '### 单独的成熟辅助窗口','',
        '每个h仅按日历选取20日尝试范围内、截至同一cutoff已到T+h的最近五个信号日；不按收益或数据质量挑选。它们不能替代最近五日主窗口。8月31日前的上海资格没有前版官方证明，因此辅助h=5等较早窗口采用深圳认证范围，不能与主窗口绝对收益直接混为同一市场样本。','']
    lines += [table(['h','固定辅助日期','策略','有效数','上涨率','均值','日期等权均值'],
                    [[h,', '.join(s['dates']),NAMES[r['strategy_id']],r['pooled_signals']['mature_valid'],pct(r['pooled_signals']['up_rate']),pct(r['pooled_signals']['mean_return']),pct(r['pooled_signals']['date_equal_mean_return'])] for h,s in supplement.items() for r in s['statistics'] if r['strategy_id'] in (PV,FOCUS,BASELINE)]),'',
        '## 每日实际名单','',
        '以下逐日焦点保留原PV名次；全部PV见各日CSV，权威JSON保存因素、精确数值、来源及剔除审计。未作人工风险复核，板事件未核验。','']
    for d in plan['primary_sessions']:
        run=read(root/d/'rank.json');focus=run['selection_results'][FOCUS]['selected_items']
        lines += [f'### {d}','',f"[完整PV名单]({d}/pv_full.csv) · [焦点CSV]({d}/focus.csv) · [权威运行]({d}/rank.json) · [资格审计]({d}/qualification_audit.json) · [数据缺口]({d}/data_gaps.json)",'',
            table(['原PV名次','证券','名称','PV分','A','R','H'],[[r['rank'],r['instrument_id'],r['name'],r['S_PV'],r['A'],pct(r['R']),pct(r['H'])] for r in focus]) if focus else '当日已核验范围内没有满足冻结焦点条件的证券，零结果保留。','']
    lines += ['## 模拟执行、不可用策略和数据限制','',
        'NEXT_OPEN_HOLD_H_PAPER_V1：信号后T+1开盘尝试买入，以E为尝试日，计划E+h收盘退出，h≥1。未买入不追入；停牌、边界价或未核验限价不默认成交。受限退出保留OPEN/BLOCKED和市值，后续验证后才能延迟平仓；平仓后的行情不再进入该笔市值。两套目标日期逐条保存在各日paper_trades.jsonl。','',
        '成本采用买卖合计0/30/60基点的纯研究情景，30基点为主，两侧各半，滑点计在该总假设内，不另扣。净收益为卖出可比价×(1−bps/20000)÷[买入可比价×(1+bps/20000)]−1；这不是当前税费、实际佣金或实盘成交。完整分母见 [paper_execution_summary.json](paper_execution_summary.json)。','',
        '冻结DAILY实际调用生产引擎；缺少核验限价、情绪、主题及完整结构历史时不生成可比DAILY排名。DNA和题材各自保留覆盖不足状态，无PV回退替代。市场环境分层缺少合格历史供给，记UNKNOWN，不编造资金或板块解释。','',
        'BaoStock历史日线、日期化证券存在与交易状态、原始IPO事实和复权因子均有原回执及字节hash校验。原始OHLC/amount使用真实端点；可比价是raw×同提供方后复权因子，并非含分红再投资的实际总回报。现在取得的历史记录可能经过供应商修订，本次不是严格存档PIT。','',
        '真实成交量为原始股数，未取得独立的可比成交量基准证明；周期中的缩量比保留未知，不能据此确认COOLING。具备连续可比收盘的MA20退出仍独立计算。跟踪记录的PARTIAL与缺口原因没有被改成全字段可用。','',
        '上海主板分类证据为2026-08-31版官方代码分配指南，信号cutoff20:00；更早版本原URL实际404，不倒填。深圳依据2021年并板规则与当日真实记录联合核验。上海退市整理为简称前冠“退市”，深圳为后冠“退”；ST与停牌来自当日字段，不使用今日状态。','',
        '原独立审阅中的4599标识/27587行快照未在本机或附件找到，因此没有冒称对该原快照逐字节重现；本报告使用本次新取得且有完整回执的真实历史记录。Tushare未配置合法Token，16端点实际授权检查为AUTH_REQUIRED、HTTP零次；不把无权接口当成功数据供给。历史限价/题材/资金缺口各自隔离。','',
        '## 复现、工程证据与结论边界','',
        '[冻结信号计划](replay_plan.json)绑定实际源文件hash、输入要求、成本和对照；[后验执行源码计划](outcome_execution_plan.json)记录后验实际源码。正式信号为修复资格哈希的规范JSON往返稳定性后，再次调用生产算法完成的20日运行。此前名单、原计划、首次后验中断日志及实现源码保留在审计目录；资格哈希修复不改变选股公式、门槛、成本和价格基准。','',
        '[20日尝试清单](historical_attempts_20_sessions.json)、[逐日×h成熟度矩阵](maturity_matrix.csv)、[共同池与实际覆盖](scope_and_comparators.json)、[生产5/20/60日复盘](production_feedback_5_20_60.json)均来自本次实际执行。退出后的5/10日影子标签另列[post_exit_shadow.jsonl](post_exit_shadow.jsonl)，不会混入信号上涨率。每个日期固定一个合规run；同run重算选择最新有效修订，不把修订次数当样本数。','',
        '原始全市场缓存和canonical MarketInput不随公开源码或审阅包分发，只提供本机精确路径、hash及采集命令；因此审阅包不是含全市场数据的完全自含包。必要源码、全部实际相关测试/fixtures、独立反例、脱敏命令日志和派生结果由统一包提供。真实API、合成工程测试、浏览器/Docker和投资验证分别报告。','',
        '本窗口已被查看，不能再用作任何根据本报告提出的修改的独立验证集。若要检验焦点相对完整PV的差异，应先登记一个明确假设，在以后未看过的日期窗口保持同条件数据与退出规则，并按h设置至少对应长度的purge/gap。当前正式参数保持不变；没有建立长期投资优势。','']
    return '\n'.join(lines)


def main():
    p=argparse.ArgumentParser();p.add_argument('--replay',required=True,type=Path);args=p.parse_args()
    target=args.replay/'过去一周实证回放与胜率口径报告.md'
    with target.open('x',encoding='utf-8') as out:out.write(report(args.replay))
    print(target.resolve())


if __name__=='__main__':main()
