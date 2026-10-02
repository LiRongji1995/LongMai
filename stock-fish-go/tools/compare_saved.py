"""Read-only semantic differential against an integrity-verified saved Python run.

No interpreter strategy is invoked. Go directories and streaming application
exports are supported. Format reshaping and displayed precision are explicit;
no float epsilon and no candidate-only comparison are used.
"""
import argparse,collections,gzip,hashlib,json,pathlib,sys
from fractions import Fraction
from decimal import Decimal,localcontext,ROUND_HALF_EVEN
p=argparse.ArgumentParser();p.add_argument('--python-saved',required=True)
g=p.add_mutually_exclusive_group(required=True);g.add_argument('--go-diagnostic');g.add_argument('--go-export');g.add_argument('--go-db')
p.add_argument('--go-run-id')
p.add_argument('--input',required=True);p.add_argument('--output',required=True);p.add_argument('--strategy',choices=['R1_FULL','V2_CONTROL'],default='R1_FULL');a=p.parse_args()
repo=pathlib.Path(__file__).resolve().parents[2];sys.path.insert(0,str(repo/'stock-fish'))
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.research_r1_storage import verify_shared_report
old=pathlib.Path(a.python_saved);report=json.loads((old/'result.json').read_text());manifest=json.loads((old/'manifest.json').read_text());complete=json.loads((old/'completion.json').read_text())
assert complete==dict(result_hash=digest(report),manifest_hash=digest(manifest)),'PYTHON_COMPLETION_IDENTITY'
assert report['manifest_hash']==digest(manifest),'PYTHON_MANIFEST_IDENTITY'
verify_shared_report(old,report)
index=json.loads((old/'objects/index.json').read_text());cache=collections.OrderedDict()
def deref(v):
 while isinstance(v,dict) and set(v)=={'$r1_ref'}:
  key=v['$r1_ref'];shard=index[key]
  if shard not in cache:
   cache[shard]=json.loads((old/'objects'/f'{shard}.json').read_text());assert digest(cache[shard])==shard
   if len(cache)>8:cache.popitem(last=False)
  cache.move_to_end(shard);v=cache[shard][key];assert digest(v)==key
 if isinstance(v,dict) and set(v)=={'$r1_list'}:return [item for part in v['$r1_list'] for item in deref(deref(part)['items'])]
 return v
def deep(v):
 v=deref(v)
 if isinstance(v,dict):return {k:deep(x) for k,x in v.items()}
 if isinstance(v,list):return [deep(x) for x in v]
 return v
def at(v,k):return deref(deref(v)[k])
if a.go_db or a.go_export:
 from saved_go import SavedGo
 if a.go_db and not a.go_run_id:p.error('--go-db requires --go-run-id')
 source=SavedGo(database=a.go_db,run_id=a.go_run_id,export=a.go_export)
 assert source.strategy=={'R1_FULL':'STAGE_AWARE_RESEARCH_R1','V2_CONTROL':'STAGE_AWARE_MOMENTUM_V2'}[a.strategy],'GO_STRATEGY_IDENTITY'
 summary=source.summary;go_manifest=source.manifest;records=source.records
else:
 new=pathlib.Path(a.go_diagnostic);path=new/'completion.json';path=path if path.exists() else new/'diagnostic-completion.json';completion=json.loads(path.read_text())
 for name,h in completion['files'].items():assert pathlib.Path(name).name==name and hashlib.sha256((new/name).read_bytes()).hexdigest()==h
 summary=json.loads((new/'result.json').read_text());go_manifest=json.loads((new/'manifest.json').read_text())
 def records():
  with gzip.open(new/'details.ndjson.gz','rt') as stream:
   for line in stream:yield json.loads(line)
raw=pathlib.Path(a.input).read_bytes();payload=json.loads(raw);assert hashlib.sha256(raw).hexdigest()==summary['input_bytes_hash'],'FROZEN_BYTES_IDENTITY'
assert len(manifest['inputs'])==1 and manifest['inputs'][0]['hash']==digest(payload['input']),'FROZEN_MODEL_IDENTITY'
assert manifest['inputs'][0]['cutoff']==summary['cutoff'],'CUTOFF_IDENTITY'
detail=deref(report['results'][0]['detail']);groups=at(detail,'groups');group=at(groups,a.strategy);expected={at(row,'instrument_id'):row for row in at(group,'items')};control=at(detail,'v2_control');v2={at(row,'instrument_id'):row for row in at(control,'items')};qualification=deep(at(detail,'qualification'));expected_coverage=deep(at(group,'coverage'))
diffs=[];checked=collections.Counter();seen=set();counts=collections.Counter()
def cmp(iid,path,got,want):
 checked[path.split('.')[0]]+=1
 if got!=want:diffs.append(dict(instrument_id=iid,field=path,go=got,python=want))
def ratio(v):return None if v is None or v=='' else Fraction(str(v))
def num(iid,path,got,want,places=None,scale=1):
 g,w=ratio(got),ratio(want)
 if g is not None:
  g*=scale
  if places is not None:
   with localcontext() as ctx:
    ctx.prec=60;g=Fraction((Decimal(g.numerator)/Decimal(g.denominator)).quantize(Decimal(1).scaleb(-places),rounding=ROUND_HALF_EVEN))
 cmp(iid,path,None if g is None else str(g),None if w is None else str(w))
def condition(iid,path,g,w):
 cmp(iid,path+'.status',g.get('status'),w.get('status'));cmp(iid,path+'.reason_codes',sorted(g.get('reason_codes') or []),sorted(w.get('reason_codes') or []))
 for key in ('value','threshold'):
  if key in w:num(iid,path+'.'+key,g.get(key),w[key])
 for key,v in w.get('conditions',{}).items():condition(iid,path+'.'+key,(g.get('conditions') or {}).get(key,{}),v)
def funds(iid,path,got,want):
 cmp(iid,path+'.themes',[x['theme_id'] for x in got],[x['theme_id'] for x in want])
 for g,w in zip(got,want):
  condition(iid,path+'.'+g['theme_id'],g['funds_condition'],w['funds_condition']);cmp(iid,path+'.grade',g['funds_condition'].get('details',{}).get('grade'),w['funds_condition'].get('grade'))
  gm,wm=g['funds_metrics'],w['funds_metrics']
  for k in ('classified_events','proxy_events','rejected_events','active_instrument_count','active_seat_count','overlapping_window_risk','days_since_last_activity','completeness_status'):
   if k in wm:cmp(iid,path+'.metrics.'+k,gm.get(k),wm[k])
  for k in ('dedup_buy_amount_cny','dedup_sell_amount_cny','dedup_net_amount_cny'):
   if k in wm:num(iid,path+'.metrics.'+k,gm.get(k),wm[k],2)
cmp(None,'qualification.eligible_ids',summary['qualification']['eligible_ids'],qualification['eligible_ids']);cmp(None,'qualification.identity_absent',summary['qualification']['identity_absent'],qualification['identity_absent'])
for key in ('candidate_availability','universe_coverage','selection_decidability','required_data_coverage','background_coverage','selection_evaluation_status'):
 cmp(None,'coverage.'+key,summary['coverage'][key],expected_coverage[key])
for row in records():
 fact=row['fact'];d=row.get('v2') or row['decision'];iid=fact['instrument_id'];seen.add(iid)
 if iid not in expected:cmp(iid,'population',True,False);continue
 want=deep(expected[iid]);legacy=deep(v2[iid]);f=fact['facts'];counts[d.get('candidate_status',d.get('decision_status'))]+=1
 if a.strategy=='R1_FULL':
  for k in ('candidate_status','matched_setups'):cmp(iid,k,d[k],want[k])
  cmp(iid,'primary_setup',d['primary_setup'] or None,want['primary_setup']);condition(iid,'common',d['common'],want['common'])
  for setup,value in want['setups'].items():condition(iid,'setup.'+setup,d['setups'][setup],value)
 else:
  for k in ('decision_status','primary_stage','alternate_stages','rank','setup_rank','rank_scope'):cmp(iid,k,d.get(k) or None,want.get(k) or None)
  for setup,value in want['stage_candidates'].items():condition(iid,'setup.'+setup,d['stage_candidates'][setup],value)
  for key,value in d['conditions'].items():condition(iid,'conditions.'+key,value,want['conditions'][key])
 for n in (1,3,5,10,20):num(iid,'facts.return.'+str(n),f['Returns'].get(str(n)),legacy['ret'+str(n)+'_pct'],4,100)
 num(iid,'facts.amount',fact['amount_cny'],legacy['amount_cny'],2);num(iid,'facts.turnover',fact['turnover_fraction'],legacy['turnover_fraction'],6);num(iid,'facts.amount_ratio',f['AmountRatio'],legacy['A'],6)
 num(iid,'facts.rs1',f['LegacyRS'].get('1'),legacy['RS_market_1d_pct'],4,100);num(iid,'facts.sector_rs1',f['SectorRS1'],legacy['RS_sector_1d_pct'],4,100)
 path=f['Path'];daily=legacy['daily_path']
 for g,k in [('CloseReturn','close_return_pct'),('LowReturn','low_return_pct'),('Amplitude','amplitude_pct'),('Recovery','recovery_points_pct')]:num(iid,'facts.path.'+g,path.get(g),daily.get(k),4,100)
 num(iid,'facts.path.CLV',path['CLV'],daily.get('CLV'),6)
 for g,k in [('CloseLimitDown','close_limit_down'),('TouchedLimitDown','touched_limit_down')]:cmp(iid,'facts.path.'+g,path[g],daily.get(k,'UNKNOWN'))
 trend=f['Trend'];tw=legacy['trend_strength'];condition(iid,'facts.trend',trend['Condition'],tw);cmp(iid,'facts.trend.state',trend['State'],tw['trend_state']);num(iid,'facts.trend.days',trend['Days'],tw['pullback_days_below_ma5']);num(iid,'facts.trend.distance',trend['Distance'],tw['matched_ma_distance'],6)
 for n,g in trend['MA'].items():num(iid,'facts.ma'+n,g,tw['ma_values'][n],6)
 for n,g in trend['Slopes'].items():num(iid,'facts.slope'+n,g,tw['ma_slopes'][n],6)
 strength=f.get('shared_strength_momentum',{});sw=legacy['shared_strength_momentum']
 for k in ('calculation_id','return_momentum','rs_momentum','strength_momentum','activity_momentum'):cmp(iid,'facts.momentum.'+k,strength.get(k),sw[k])
 for k in ('strength_momentum_raw','return_momentum_raw','rs_momentum_raw','activity_momentum_raw','amount_momentum_raw','turnover_momentum_raw'):num(iid,'facts.momentum.'+k,strength.get(k),sw[k],4,100)
 for k in ('recent_amount_avg_cny','previous_amount_avg_cny'):num(iid,'facts.momentum.'+k,strength.get(k),sw[k],2)
 for k in ('recent_turnover_avg','previous_turnover_avg'):num(iid,'facts.momentum.'+k,strength.get(k),sw[k],6)
 supply=fact['supply_pressure'];sp=legacy['supply_pressure'];condition(iid,'background.supply',supply,sp)
 for k in ('long_upper_shadow_count','unreclaimed_recent_shadow_count','unresolved_supply_count','reclaimed_then_failed_count'):
  if k in sp:cmp(iid,'background.supply.'+k,supply['details'].get(k),sp[k])
 if 'shared_market_ref' in row:assert row['shared_market_ref']=='summary.market_context' and 'market_context' in summary,'SHARED_MARKET_REFERENCE_UNRESOLVED'
 market=summary.get('market_context',{}) or d.get('background',{}).get('market_status',{});mw=legacy['market_status'];cmp(iid,'background.market.state',market.get('ecosystem_state'),mw['ecosystem_state'])
 for k,w in mw['market_width'].items():
  g=market.get('market_width',{}).get(k)
  if isinstance(w,(int,float)) or w is None:num(iid,'background.market.width.'+k,g,w,6)
  else:cmp(iid,'background.market.width.'+k,g,w)
 if 'background' in d:
  bg=d['background'];sectors=bg['sector_candidates'];ws=legacy['sector_candidates'];cmp(iid,'background.sectors.themes',[x['theme_id'] for x in sectors],[x['theme_id'] for x in ws])
  for s,w in zip(sectors,ws):
   for k in ('theme_id','theme_name','theme_type','status','member_count','peer_expected','sector_level_state','sector_absolute_level','sector_relative_level','sector_momentum_state','breadth_state','stock_role'):cmp(iid,'background.sector.'+k,s[k],w[k])
   for setup,c in s['setup_compatibility'].items():cmp(iid,'background.sector.compatibility.'+setup,c['details']['context_status'],w['setup_compatibility'][setup]['context_status']);cmp(iid,'background.sector.compatibility_reasons.'+setup,c['reason_codes'],w['setup_compatibility'][setup]['reason_codes'])
   for n,c in s['windows'].items():
    wc=w['windows'][n]
    for g,k in [('valid_count','valid_peer_count'),('expected_count','expected_peer_count'),('status','coverage_status')]:cmp(iid,'background.sector.window.'+n+'.'+g,c[g],wc[k])
    for g,k in [('value','peer_equal_weight_return_pct'),('peer_median_return','peer_median_return_pct'),('peer_amount_change_median','peer_amount_change_median_pct'),('sector_market_rs','sector_market_rs_pct'),('stock_return','stock_return_pct'),('rs_sector','rs_sector_pct')]:num(iid,'background.sector.window.'+n+'.'+g,c[g],wc[k],4,100)
   num(iid,'background.sector.role_score',s['stock_role_score'],w['stock_role_score'],6)
  funds(iid,'background.funds',bg['funds_candidates'],legacy['funds_candidates']);
  if a.strategy=='R1_FULL':funds(iid,'background.independent_funds',bg['independent_theme_funds'],want['independent_theme_funds'])
 support=row.get('coverage') or summary.get('coverage',{}).get('support',{}).get(iid)
 assert support is not None,'GO_COVERAGE_SUPPORT_MISSING'
 wc=expected_coverage['support'][iid]
 for k in ('core_facts_evaluable','core_truth','selection_decidable','selection_truth','background_coverage'):cmp(iid,'coverage.'+k,support[k],wc[k])
if a.go_db or a.go_export:source.close()
for iid in sorted(set(expected)-seen):cmp(iid,'population',False,True)
result=dict(contract='GO_SAVED_SEMANTIC_DIFFERENTIAL_V2',status='PASS_CHECKED_SEMANTIC_PROJECTION' if not diffs else 'DIFFERENCES_FOUND',strategy=a.strategy,input_hash=manifest['inputs'][0]['hash'],input_bytes_hash=summary['input_bytes_hash'],rows=len(seen),counts=dict(counts),checks=dict(checked),difference_count=len(diffs),differences=diffs,oracle='INTEGRITY_VERIFIED_ORIGINAL_PYTHON_SAVED_RESULT',numeric_policy='EXACT_RATIONAL; EXPLICIT_ORIGINAL_DISPLAY_ROUNDING_ONLY; NO_EPSILON',not_recoverable_from_saved_oracle=['selected historical bar raw-byte bindings: saved Python rows omit them; require source integrity and projection-to-raw binding verification'],performance_speedup='NOT_ESTABLISHED_WITHOUT_CONTROLLED_FULL_SCOPE_TIMING')
with open(a.output,'x') as f:json.dump(result,f,ensure_ascii=False,indent=2)
print(json.dumps({k:result[k] for k in ('status','strategy','rows','difference_count','checks')},ensure_ascii=False));sys.exit(bool(diffs))
