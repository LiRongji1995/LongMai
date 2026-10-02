"""Exact saved-result comparison across old/new adapters or cold/warm runs.

Each side's stored header, raw item and membership hashes are first validated.
Every item JSON field is compared, including all selected evidence hashes;
only explicitly listed operational summary/manifest fields are excluded.
"""
import argparse
import itertools
import json
from saved_go import SavedGo

p=argparse.ArgumentParser()
p.add_argument('--left-db',required=True);p.add_argument('--left-run-id',required=True)
p.add_argument('--right-db',required=True);p.add_argument('--right-run-id',required=True)
p.add_argument('--output',required=True)
a=p.parse_args()
left=SavedGo(database=a.left_db,run_id=a.left_run_id);right=SavedGo(database=a.right_db,run_id=a.right_run_id)
diffs=[];checked=0
EXCLUDED_SUMMARY={'metrics','fact_cache_hits','fact_cache_misses'}
EXCLUDED_MANIFEST={'history_storage_backend'}

def compare(path,x,y):
    global checked
    checked+=1
    if type(x)!=type(y):
        diffs.append(dict(field=path,left=x,right=y));return
    if isinstance(x,dict):
        for key in sorted(set(x)|set(y)):
            if key not in x or key not in y: diffs.append(dict(field=path+'.'+key,left_present=key in x,right_present=key in y))
            else: compare(path+'.'+key,x[key],y[key])
    elif isinstance(x,list):
        if len(x)!=len(y):diffs.append(dict(field=path+'.length',left=len(x),right=len(y)))
        for i,(v,w) in enumerate(zip(x,y)):compare(path+f'[{i}]',v,w)
    elif x!=y:diffs.append(dict(field=path,left=x,right=y))

try:
    compare('strategy',left.strategy,right.strategy)
    compare('summary',{k:v for k,v in left.summary.items() if k not in EXCLUDED_SUMMARY},{k:v for k,v in right.summary.items() if k not in EXCLUDED_SUMMARY})
    compare('manifest',{k:v for k,v in left.manifest.items() if k not in EXCLUDED_MANIFEST},{k:v for k,v in right.manifest.items() if k not in EXCLUDED_MANIFEST})
    count=0;sentinel=object()
    for count,(x,y) in enumerate(itertools.zip_longest(left.records(),right.records(),fillvalue=sentinel),1):
        if x is sentinel or y is sentinel:diffs.append(dict(field=f'items[{count-1}]',left_present=x is not sentinel,right_present=y is not sentinel));continue
        compare('item_raw_sha256.'+x['fact']['instrument_id'],left.current_raw_hash,right.current_raw_hash)
        compare('item.'+x['fact']['instrument_id'],x,y)
    result=dict(contract='GO_CROSS_STORAGE_EXACT_SEMANTICS_V1',status='PASS' if not diffs else 'DIFFERENCES_FOUND',rows=count,checked_values=checked,difference_count=len(diffs),differences=diffs,excluded_summary=sorted(EXCLUDED_SUMMARY),excluded_manifest=sorted(EXCLUDED_MANIFEST),integrity='BOTH_HEADERS_RAW_ITEMS_AND_ORDERED_MEMBERSHIP_VERIFIED',selected_evidence_policy='ALL_ITEM_FIELDS_EXACT_INCLUDING_SELECTED_EVIDENCE_HASHES')
    with open(a.output,'x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:result[k] for k in ('status','rows','checked_values','difference_count')}))
finally:
    left.close();right.close()
raise SystemExit(bool(diffs))
