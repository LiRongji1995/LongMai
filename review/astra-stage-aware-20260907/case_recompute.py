"""Read-only case audit; writes only compact derived evidence, never MarketInput.

Run with the project's existing Python. Private inputs are not redistributed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('case_evidence.json'))
    args = parser.parse_args()
    root = args.root.resolve()
    app = root / 'stock-fish'
    sys.path.insert(0, str(app))
    from stockfish_core.yaogu.contracts import MarketInput
    from stockfish_core.yaogu.strategy_experiments import run_pv_observation, run_pv_focus_preview

    source = app / 'reports/codex/pv_observation_20260904_rebuilt_20260907T091708'
    audit = app / 'reports/codex/stage_aware_momentum_v1_initial_audit_20260907T111840/audit-summary.json'
    paths = [source / 'market-input.json', source / 'strategy-experiments.json', audit,
             root / 'review/r05-data-strategy/pv-observation-top20.json']
    before = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    raw, saved, stage_audit, old20 = [json.loads(p.read_text(encoding='utf-8-sig')) for p in paths]
    bundle = MarketInput.model_validate(raw)
    cutoff = datetime.fromisoformat(saved['decision_cutoff'])
    pv = run_pv_observation(bundle, cutoff=cutoff)
    focus = run_pv_focus_preview(pv)
    prevpv = saved['results']['PV_OBSERVATION_V1']
    prevfocus = saved['selection_results']['PV_FOCUS_PREVIEW_V0.1']
    keys = ('instrument_id', 'rank', 'A', 'R', 'H', 'S_PV')
    project = lambda rows: [{k: r.get(k) for k in keys} for r in rows]
    exact_frozen = project(pv['items']) == project(prevpv['items'])
    top20_same = all(all(abs(float(old[k])-float(new[k])) <= 0.00000051 for k in ('A','R','H','S_PV'))
                     and old['instrument_id'] == new['instrument_id'] and old['rank'] == new['rank']
                     for old, new in zip(old20['items'], pv['items'][:20])) and len(old20['items']) == 20
    out = {
        'evidence_levels': ['ACTUAL_EXECUTION', 'HISTORICAL_REPLAY'],
        'interpretation': '2026-09-07 acquired reconstruction of 2026-09-04 observations; not a preserved 09-04 decision',
        'run_metadata': {k: saved.get(k) for k in ('run_id','input_id','input_hash','observation_date','decision_cutoff','audit_mode','coverage_type','data_watermark')},
        'input_sha256': before,
        'freeze_checks': {'all_525_rank_factor_score_equal': exact_frozen,
                          'all_17_focus_ids_equal': focus['selected_ids'] == prevfocus['selected_ids'],
                          'older_published_top20_match_with_rounding': top20_same,
                          'scope': 'available reconstruction and earlier published top20; original full historical DB absent'},
        'cases': {},
    }
    D = lambda v: Decimal(str(v))
    for iid in ('601086.SH','600127.SH'):
        bars = sorted((r for r in raw['bars'] if r['instrument_id'] == iid), key=lambda r: r['session'])
        b = bars[-1]
        prior = bars[-6:-1]
        high, low, close, prev = [D(b[k]) for k in ('high','low','close','reference_prev_close')]
        avg = sum(D(r['amount_cny']) for r in prior) / len(prior)
        ret = {str(w): float((D(b['comparable_close'])/D(bars[-w-1]['comparable_close'])-1)*100)
               if len(bars)>w else None for w in (1,3,5,10,20)}
        clv=(close-low)/(high-low)
        proposed_up=(prev*D('1.1')).quantize(D('.01'),rounding=ROUND_HALF_UP)
        proposed_down=(prev*D('.9')).quantize(D('.01'),rounding=ROUND_HALF_UP)
        feature=next(r for r in pv['eligible_feature_items'] if r['instrument_id']==iid)
        f=next((r for r in focus['items'] if r['instrument_id']==iid),None)
        s=stage_audit['case_rows'][iid]['new_stage_aware_row']
        out['cases'][iid]={
            'name': feature['name'], 'price_sessions': [r['session'] for r in bars],
            'old_pv_rank': next((r['rank'] for r in pv['items'] if r['instrument_id']==iid),None),
            'old_pv_rejection': next((r['reason_codes'] for r in pv['rejected'] if r['instrument_id']==iid),[]),
            'A': float(D(b['amount_cny'])/avg), 'R': ret['5']/100, 'H': float((high-low)/prev),
            'OHLC_and_prev': {k:float(D(b[k])) for k in ('open','high','low','close','reference_prev_close')},
            'CLV':float(clv), 'CLV_gap_to_0_70':float(D('.70')-clv),
            'close_gap_to_0_70_price':float(low+D('.7')*(high-low)-close),
            'amount_cny':float(D(b['amount_cny'])), 'turnover_pct':100*b['turnover_fraction'],
            'prior5_amount_avg_cny':float(avg), 'prior10_amount_avg_cny':None,
            'prior5_amount_range_cny':[float(min(D(r['amount_cny']) for r in prior)),float(max(D(r['amount_cny']) for r in prior))],
            'prior5_turnover_pct':[100*r['turnover_fraction'] for r in prior],
            'return_pct': ret, 'low_return_pct':float((low/prev-1)*100),
            'high_return_pct':float((high/prev-1)*100), 'close_return_pct':float((close/prev-1)*100),
            'recovery_points_pct':float((close-low)/prev*100),
            'amplitude_prev_close_pct':float((high-low)/prev*100),
            'amplitude_low_denominator_pct':float((high-low)/low*100),
            'old_focus': None if f is None else {k:f.get(k) for k in ('selected','reason_codes','parent_rank','close_location')},
            'old_focus_gate_margins': {'A_minus_2':float(D(b['amount_cny'])/avg-2),
                                       'R_minus_0_10':ret['5']/100-.10, 'CLV_minus_0_70':float(clv-D('.7')),
                                       'close_minus_open':float(close-D(b['open']))},
            'new_stage_from_saved_result': {k:s.get(k) for k in ('status','primary_stage','alternate_stages','stage_candidates','relative_strength','market_status','trend_strength','reason_codes')},
            'limit_diagnostic': {'formal_event_status':'UNKNOWN', 'reason':'no verified direct limits / rule exception evidence in input',
                                 'ordinary_10pct_rule_assumption_only': {'upper':str(proposed_up),'lower':str(proposed_down),
                                 'low_equals_computed_lower':low==proposed_down,'high_reaches_computed_upper':high>=proposed_up,'close_equals_computed_upper':close==proposed_up},
                                 'intraday_order_not_observable_from_OHLC':True},
            'source_clock': {k:b.get(k) for k in ('event_time','source_published_at','first_seen_at','ingested_at')},
            'missing': ['prior10_amount','MA10/20/60/120/250','dated_sector_and_sector_RS','structured_LHB','post_signal_outcomes'],
        }
        assert abs(out['cases'][iid]['A']-feature['A']) < .00000051
        assert abs(out['cases'][iid]['CLV']-s['CLV']) < .00000051
    after={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    out['private_source_hashes_unchanged']=before==after
    assert exact_frozen and top20_same and out['freeze_checks']['all_17_focus_ids_equal'] and before==after
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'freeze_checks':out['freeze_checks'],'source_unchanged':before==after,
                      'output':str(args.output)},ensure_ascii=False))


if __name__=='__main__':
    main()
