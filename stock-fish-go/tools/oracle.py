"""Isolated Python reference generator; the Go executable never imports this.
Scope: independent same-evidence R1, V2, background, coverage, reasons and facts.
Raw Python rows are retained alongside the semantic projection for audit.
"""
import argparse,json,pathlib,sys,time
from decimal import Decimal
from datetime import timedelta, time as daytime
p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args()
repo=pathlib.Path(__file__).resolve().parents[2];sys.path.insert(0,str(repo/'stock-fish'))
from tests.r05_product.test_stage_aware_momentum_v1 import _with_high_level_repair_target,_with_high_activity_weakening_target,_payload,_set_bar,_set_tail_closes
from tests.r05_product.test_theme_capital_trend_v1 import trend_bundle,_days,_fund,_clocks
from stockfish_core.yaogu import research_r1 as r,stage_aware_momentum as v
from stockfish_core.yaogu.contracts import MarketInput,digest
out=pathlib.Path(a.output);out.mkdir(parents=True,exist_ok=False)
cases=[('repair',_with_high_level_repair_target()),('weakening',_with_high_activity_weakening_target()),('pullback',trend_bundle(pullback_days=2)),('low_amount',trend_bundle(amount=1)),('missing_price',trend_bundle())]
days=_days(280)
cases.extend([
 ('funds_missing',trend_bundle(funds=[])),
 ('funds_proxy',trend_bundle(funds=[_fund('TEST001',days[-1],Decimal('70000000'),Decimal('50000000'),layer='CAPITAL_PROXY',seat='P1')])),
 ('funds_future',trend_bundle(funds=[_fund('TEST001',days[-1],Decimal('70000000'),Decimal('50000000'),window_end=days[-1]+timedelta(days=1))])),
 ('funds_conflict',trend_bundle(funds=[_fund('TEST001',days[-1],Decimal(v),Decimal('50000000'),seat='C1',dedupe_key='same-disclosure') for v in ('70000000','80000000')])),
 ('funds_complete_empty',trend_bundle(funds=[dict(theme_id='THEME_A',record_type='FUNDS_QUERY_COMPLETENESS',completion_status='COMPLETE_NO_RECORD',query_status='COMPLETE',row_count=0,query_scope='THEME_A_PEERS_LOOKBACK_5',expected_scope=tuple(f'TEST{i:03d}' for i in range(7)),actual_scope=(),session=days[-1],window_start=days[-5],window_end=days[-1],source_refs=['fixture-funds'],**_clocks(days[-1],daytime(16)))])),
])
for name in ('sector_negative','repeated_supply','close_limit_down','theme_missing'):
 bundle,cutoff=trend_bundle();payload=_payload(bundle)
 if name=='sector_negative':
  for i in range(1,7):_set_tail_closes(payload,f'TEST{i:03d}',('20','19.8','19.6','19.4','19.2','19.0'))
 elif name=='repeated_supply':
  for day,o,h,l,c in ((payload['sessions'][-1],'23.90','25.00','23.70','23.95'),(payload['sessions'][-2],'23.80','25.10','23.60','23.90')):
   _set_bar(next(r for r in payload['bars'] if r['instrument_id']=='TEST000' and r['session']==day),open_=o,high=h,low=l,close=c)
 elif name=='close_limit_down':
  bar=next(r for r in payload['bars'] if r['instrument_id']=='TEST000' and r['session']==payload['sessions'][-1]);_set_bar(bar,open_='25.80',high='26.70',low='23.81',close='23.81',reference_prev_close='26.45',amount='600000000',turnover='.08');bar['limit_down']=Decimal('23.81')
 else:payload['themes']=[]
 cases.append((name,(type(bundle).model_validate(payload),cutoff)))
summary=[]
def rat(x):return str(x) if x is not None else None
for name,(bundle,cutoff) in cases:
 payload=bundle.model_dump(mode='json')
 if name=='missing_price':payload['bars']=[b for b in payload['bars'] if not(b['instrument_id']=='TEST000' and b['session']==payload['observation_date'])];bundle=MarketInput.model_validate(payload)
 frozen=dict(input=payload,cutoff=cutoff.isoformat(),purpose='TEST_FIXTURE_GO_MIGRATION')
 (out/f'{name}.json').write_text(json.dumps(frozen,ensure_ascii=False))
 start=time.monotonic();result=r.run_research(bundle,cutoff=cutoff,groups=['R1_FULL']);ctx=v._prepare_context(bundle,cutoff);index=v._session_index(ctx)
 expected={}
 for row in result['groups']['R1_FULL']['items']:
  iid=row['instrument_id'];returns=v._return_map(ctx,iid,index);current,_=v._ma_value(ctx,iid,index,20);prior,_=v._ma_value(ctx,iid,index-5,20)
  expected[iid]=dict(full_r1=row,candidate_status=row['candidate_status'],matched_setups=row['matched_setups'],common=row['common']['status'],setups={s:x['status'] for s,x in row['setups'].items()},returns={k:rat(x) for k,x in returns.items()},rs={str(n):rat(v._rs_at(ctx,iid,index,n)[0]) for n in (1,3,5,10)},ma20=rat(current),prior_ma20=rat(prior),close_limit_down=v._daily_path(ctx,iid).get('close_limit_down','UNKNOWN'))
 for iid, item in expected.items():
  by_session={}
  for bar in bundle.bars:
   if bar.instrument_id==iid and bar.quality!='INVALID' and ctx.resolver.resolve(bar,instrument_id=iid,session=bar.session,require_session=True).allowed:
    by_session.setdefault(bar.session,[]).append(bar)
  item['selected_hashes']=sorted(digest(selected) for values in by_session.values() if (selected:=v._select_revision(values)) is not None)
 document=dict(v2_coverage=r.coverage(result['v2_control']['items'],ctx.qualification,ctx.universe_complete),full_python=result, v2_expected={row['instrument_id']:row for row in result['v2_control']['items']},coverage=result['groups']['R1_FULL']['coverage'],coverage_type='TEST_FIXTURE',eligible_ids=ctx.eligible_ids,universe_complete=ctx.universe_complete,expected=expected,python_seconds=time.monotonic()-start,scope='R1_V2_FULL_SEMANTIC_PROJECTION_WITH_RAW_ORACLE')
 (out/f'{name}.expected.json').write_text(json.dumps(document,sort_keys=True))
 summary.append(dict(case=name,stocks=len(expected),python_seconds=document['python_seconds']));print(json.dumps(summary[-1]),flush=True)
(out/'summary.json').write_text(json.dumps(summary,indent=2))
