"""Actual loopback HTTP checks against saved real R05 ranks, never fake HTTP 200."""
import argparse,csv,gzip,io,json,time,hashlib
from datetime import datetime,timezone
from pathlib import Path
import requests


def main():
    p=argparse.ArgumentParser();p.add_argument('--rank',type=Path,required=True);p.add_argument('--url',default='http://127.0.0.1:18006');p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    run=json.loads(args.rank.read_text(encoding='utf-8'));rid=run['run_id'];pv=run['results']['PV_OBSERVATION_V1']['items'];focus=run['selection_results']['PV_FOCUS_PREVIEW_V0.1']['selected_ids'];checks=[]
    args.output.mkdir(parents=True,exist_ok=False)
    def get(name,path,**params):
        start=time.perf_counter();response=requests.get(args.url+path,params=params,timeout=120)
        (args.output/(name+'.response')).write_bytes(response.content)
        checks.append({'check':name,'url':response.url,'http_status':response.status_code,'elapsed_seconds':time.perf_counter()-start,
                       'response_bytes':len(response.content),'response_sha256':hashlib.sha256(response.content).hexdigest()})
        response.raise_for_status();return response
    for selection,expected in [('all',pv),('focus',[r for r in pv if r['instrument_id'] in focus]),('unselected',[r for r in pv if r['instrument_id'] not in focus])]:
        prefix='/api/strategy-experiments/'+rid;ids=[r['instrument_id'] for r in expected]
        summary=get(selection+'-summary',prefix+'/summary',selection=selection,page_size=1).json()
        assert summary['filtered_total']==len(expected) and len(summary['items'])==min(1,len(expected))
        if expected:assert summary['items'][0]['rank']==expected[0]['rank']
        data=get(selection+'-csv',prefix+'/export',selection=selection,strategy_id='PV_OBSERVATION_V1',format='csv')
        exported=list(csv.DictReader(io.StringIO(data.content.decode('utf-8-sig'))));assert [r['instrument_id'] for r in exported]==ids
        data=get(selection+'-json',prefix+'/export',selection=selection,strategy_id='PV_OBSERVATION_V1',format='json').json()
        assert [r['instrument_id'] for r in data['results']['PV_OBSERVATION_V1']['items']]==ids
    if len(pv)>100:
        page=get('page2',prefix+'/summary',page=2,page_size=100).json();assert [r['rank'] for r in page['items']]==[r['rank'] for r in pv[100:200]]
    for sid in ('DNA_RERANK_V1','THEME_CONTEXT_V1'):
        saved=run['results'][sid]
        ids=[r['instrument_id'] for r in saved['items'] if r.get('rank') is not None and r.get('status') in (None,'EVALUATED','COMPLETE')]
        summary=get(sid+'-summary',prefix+'/summary',strategy_id=sid,page_size=100).json()
        assert summary['filtered_total']==len(ids)
        data=get(sid+'-csv',prefix+'/export',strategy_id=sid,format='csv')
        exported=list(csv.DictReader(io.StringIO(data.content.decode('utf-8-sig'))))
        assert [r['instrument_id'] for r in exported]==ids
        data=get(sid+'-json',prefix+'/export',strategy_id=sid,format='json').json()
        assert [r['instrument_id'] for r in data['results'][sid]['items']]==ids
        assert data['results'][sid]['status']==saved['status']
        assert 'view_content_hash' in data and data['export_view']['source_content_hash']
    tracking=json.loads((args.rank.parent/'tracking.json').read_text(encoding='utf-8'))
    with gzip.open(args.rank.parent/'canonical_outcomes.jsonl.gz','rt',encoding='utf-8') as source:
        expected_outcomes=[json.loads(line) for line in source if line.strip()]
    for number, instrument in enumerate(dict.fromkeys((focus[0],focus[-1]))):
        item=get('detail-'+str(number),prefix+'/items/'+instrument).json()
        assert item['focus_selection']['selected'] and item['focus_selection']['rank']==next(r['rank'] for r in pv if r['instrument_id']==instrument)
        expected_tracking={r['observation_id']:r for r in tracking['observations'] if r['run_id']==rid and r['instrument_id']==instrument}
        actual_tracking={r['observation_id']:r for r in item['tracking']}
        assert expected_tracking and actual_tracking==expected_tracking
        expected_labels={r['observation_id']:r for r in expected_outcomes if r['run_id']==rid and r['instrument_id']==instrument}
        actual_labels={r['observation_id']:r for r in item['outcomes']}
        assert expected_labels and all(actual_labels.get(k)==v for k,v in expected_labels.items())
        checks.append({'check':'saved_detail_payloads_equal_'+instrument,'tracking_rows':len(actual_tracking),'canonical_labels':len(expected_labels),'status':'PASS'})
    review=get('review',prefix+'/review').json()
    result={'executed_at':datetime.now(timezone.utc).isoformat(),'run_id':rid,'observation_date':run['observation_date'],
            'actual_pv_count':len(pv),'actual_focus_count':len(focus),'checks':checks,'evidence_class':'ACTUAL_HTTP_SAVED_REAL_RUN',
            'fixture_data':False,'account_present':False,'validation_result':'PASS'}
    (args.output/'api-checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in result.items() if k!='checks'},ensure_ascii=False))


if __name__=='__main__':main()
