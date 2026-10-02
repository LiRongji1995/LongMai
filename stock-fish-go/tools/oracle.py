"""Isolated Python reference generator; the Go executable never imports this.
Scope: same raw TEST_FIXTURE evidence, qualification, facts, four decisions.
Full background/reason/reference parity is explicitly outside this checkpoint.
"""
import argparse,json,pathlib,sys,time
p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args()
repo=pathlib.Path(__file__).resolve().parents[2];sys.path.insert(0,str(repo/'stock-fish'))
from tests.r05_product.test_stage_aware_momentum_v1 import _with_high_level_repair_target,_with_high_activity_weakening_target
from tests.r05_product.test_theme_capital_trend_v1 import trend_bundle
from stockfish_core.yaogu import research_r1 as r,stage_aware_momentum as v
from stockfish_core.yaogu.contracts import MarketInput,digest
out=pathlib.Path(a.output);out.mkdir(parents=True,exist_ok=False)
cases=[('repair',_with_high_level_repair_target()),('weakening',_with_high_activity_weakening_target()),('pullback',trend_bundle(pullback_days=2)),('low_amount',trend_bundle(amount=1)),('missing_price',trend_bundle())]
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
  expected[iid]=dict(candidate_status=row['candidate_status'],matched_setups=row['matched_setups'],common=row['common']['status'],setups={s:x['status'] for s,x in row['setups'].items()},returns={k:rat(x) for k,x in returns.items()},rs={str(n):rat(v._rs_at(ctx,iid,index,n)[0]) for n in (1,3,5,10)},ma20=rat(current),prior_ma20=rat(prior),close_limit_down=v._daily_path(ctx,iid).get('close_limit_down','UNKNOWN'))
 for iid, item in expected.items():
  by_session={}
  for bar in bundle.bars:
   if bar.instrument_id==iid and bar.quality!='INVALID' and ctx.resolver.resolve(bar,instrument_id=iid,session=bar.session,require_session=True).allowed:
    by_session.setdefault(bar.session,[]).append(bar)
  item['selected_hashes']=sorted(digest(selected) for values in by_session.values() if (selected:=v._select_revision(values)) is not None)
 document=dict(coverage_type='TEST_FIXTURE',eligible_ids=ctx.eligible_ids,universe_complete=ctx.universe_complete,expected=expected,python_seconds=time.monotonic()-start,scope='QUALIFICATION_EXACT_FACTS_COMMON_SETUP_STATUS_MATCHED_CORE_COLLECTION_ONLY')
 (out/f'{name}.expected.json').write_text(json.dumps(document,sort_keys=True))
 summary.append(dict(case=name,stocks=len(expected),python_seconds=document['python_seconds']));print(json.dumps(summary[-1]),flush=True)
(out/'summary.json').write_text(json.dumps(summary,indent=2))
