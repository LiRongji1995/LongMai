"""Read-only saved Python vs Go diagnostic comparison, never an oracle run.
Intentionally reports the remaining unchecked semantics as blockers.
"""
import argparse,json,pathlib,sys,gzip,hashlib,collections
p=argparse.ArgumentParser();p.add_argument('--python-saved',required=True);p.add_argument('--go-diagnostic',required=True);p.add_argument('--input',required=True);p.add_argument('--output',required=True);a=p.parse_args()
repo=pathlib.Path(__file__).resolve().parents[2];sys.path.insert(0,str(repo/'stock-fish'))
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.research_r1_storage import verify_shared_report
old=pathlib.Path(a.python_saved);new=pathlib.Path(a.go_diagnostic)
report=json.loads((old/'result.json').read_text());manifest=json.loads((old/'manifest.json').read_text());complete=json.loads((old/'completion.json').read_text())
assert complete==dict(result_hash=digest(report),manifest_hash=digest(manifest))
assert report['manifest_hash']==digest(manifest)
verify_shared_report(old,report)
completion=json.loads((new/'diagnostic-completion.json').read_text())
for name,h in completion['files'].items():
 assert pathlib.Path(name).name==name and hashlib.sha256((new/name).read_bytes()).hexdigest()==h
summary=json.loads((new/'result.json').read_text());raw=pathlib.Path(a.input).read_bytes();payload=json.loads(raw)
assert hashlib.sha256(raw).hexdigest()==summary['input_bytes_hash']
assert len(manifest['inputs'])==1 and manifest['inputs'][0]['hash']==digest(payload['input'])
assert manifest['inputs'][0]['cutoff']==summary['cutoff']
index=json.loads((old/'objects/index.json').read_text());cache=collections.OrderedDict()
def deref(v):
 while isinstance(v,dict) and set(v)=={'$r1_ref'}:
  key=v['$r1_ref'];shard=index[key]
  if shard not in cache:
   cache[shard]=json.loads((old/'objects'/f'{shard}.json').read_text());assert digest(cache[shard])==shard
   if len(cache)>8:cache.popitem(last=False)
  cache.move_to_end(shard);v=cache[shard][key];assert digest(v)==key
 if isinstance(v,dict) and set(v)=={'$r1_list'}:
  return [item for part in v['$r1_list'] for item in deref(deref(part)['items'])]
 return v
def at(v,k):return deref(deref(v)[k])
detail=deref(report['results'][0]['detail']);groups=at(detail,'groups');r1=at(groups,'R1_FULL');items=at(r1,'items');expected={at(row,'instrument_id'):row for row in items}
qualification=at(detail,'qualification')
diffs=[];counts=collections.Counter();setup_counts={s:collections.Counter() for s in ('START','ACCELERATION','HIGH_LEVEL_REPAIR','TREND_PULLBACK')};seen=set();candidate_diagnostic_ids=[]
def compare(iid,field,got,want):
 if got!=want:diffs.append(dict(instrument_id=iid,field=field,go=got,python=want))
compare(None,'eligible_ids',summary['qualification']['eligible_ids'],at(qualification,'eligible_ids'))
for name in ('identity_absent',):compare(None,name,summary['qualification'][name],at(qualification,name))
with gzip.open(new/'details.ndjson.gz','rt') as stream:
 for line in stream:
  row=json.loads(line);d=row['decision'];iid=d['instrument_id'];seen.add(iid);expected_row=expected.get(iid)
  if expected_row is None:diffs.append(dict(instrument_id=iid,field='unexpected_go_row'));continue
  for key in ('candidate_status','matched_setups'):compare(iid,key,d[key],at(expected_row,key))
  compare(iid,'common.status',d['common']['status'],at(at(expected_row,'common'),'status'))
  setups=at(expected_row,'setups')
  for s,c in d['setups'].items():compare(iid,'setups.'+s+'.status',c['status'],at(at(setups,s),'status'));setup_counts[s][c['status']]+=1
  counts[d['candidate_status']]+=1
  if d['candidate_status']=='CORE_PASS':candidate_diagnostic_ids.append(iid)
for iid in sorted(set(expected)-seen):diffs.append(dict(instrument_id=iid,field='missing_go_row'))
result=dict(contract='GO_R1_SAVED_PARTIAL_DIFFERENTIAL_V1',status='MATCHED_CHECKED_FIELDS_ONLY' if not diffs else 'DIFFERENCES_FOUND',input_hash=manifest['inputs'][0]['hash'],input_bytes_hash=summary['input_bytes_hash'],observation_date=summary['observation_date'],cutoff=summary['cutoff'],rows=len(seen),diagnostic_counts=dict(counts),diagnostic_setup_counts={k:dict(v) for k,v in setup_counts.items()},diagnostic_core_pass_ids=candidate_diagnostic_ids,differences=diffs,unchecked=['full background','all reasons','all raw selected evidence identities in real run','all public fact values in real run','formal coverage contract'],accepted_candidate_list=False,performance_speedup='NOT_ESTABLISHED_DIFFERENT_WORK_SCOPE_NO_CONTROLLED_BASELINE')
with open(a.output,'x') as f:json.dump(result,f,ensure_ascii=False,indent=2)
print(json.dumps({k:result[k] for k in ('status','rows','diagnostic_counts')},ensure_ascii=False));print('differences',len(diffs))
