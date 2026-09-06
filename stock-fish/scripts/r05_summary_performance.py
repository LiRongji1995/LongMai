from datetime import datetime,timezone
from pathlib import Path
import json,time,tracemalloc,sys
from tempfile import TemporaryDirectory
sys.path.insert(0,str(Path.cwd()))
from stockfish_core.service import ApplicationService
from stockfish_core.storage import SQLiteStore
from stockfish_core.api import create_app
out=Path('../review/r05-rectification/tracking-service-evidence');out.mkdir(parents=True,exist_ok=True)
with TemporaryDirectory(prefix='r05_summary_fixture_') as folder:
    root=Path(folder);core=ApplicationService(root=root/'state',store=SQLiteStore(root/'test.sqlite3'),allow_test_fixture=True,data_dir=root/'no_model')
    try:
        rows=[{'instrument_id':f'TEST{i:04d}','rank':i+1,'S_PV':80,'A':2.5,'R':.12,'H':.1,'evidence':{'fixture_only_padding':'X'*4000}} for i in range(1000)]
        run={'run_id':'TEST_FIXTURE_PERF_1000','input_id':'TEST_ONLY','observation_date':'2026-09-04','implementation_version':'TEST_FIXTURE',
             'coverage_type':'TEST_FIXTURE','results':{'PV_OBSERVATION_V1':{'strategy_id':'PV_OBSERVATION_V1','status':'COMPLETE','items':rows}},
             'selection_results':{'PV_FOCUS_PREVIEW_V0.1':{'selected_ids':[r['instrument_id'] for r in rows]}}}
        service=core.candidates;service.store.put_document('strategy_experiment_runs',run['run_id'],run)
        with service.store.transaction() as connection:service._write_strategy_projection(connection,run)
        original=service.store.get_document;reads=[]
        def spy(namespace,key):
            reads.append(namespace)
            if namespace=='strategy_experiment_runs':raise AssertionError('summary read full document')
            return original(namespace,key)
        service.store.get_document=spy
        client=create_app(core).test_client();tracemalloc.start();start=time.perf_counter()
        response=client.get('/api/strategy-experiments/TEST_FIXTURE_PERF_1000/summary?page_size=1')
        elapsed=time.perf_counter()-start;current,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
        assert response.status_code==200 and len(response.json['items'])==1
        result={'executed_at':datetime.now(timezone.utc).isoformat(),'evidence_class':'TEST_FIXTURE_ENGINEERING_ONLY',
                'row_count':1000,'full_run_json_bytes':len(json.dumps(run).encode()),'summary_page_size':1,
                'http_status':response.status_code,'response_bytes':len(response.data),'elapsed_seconds':elapsed,
                'python_tracemalloc_peak_bytes':peak,'read_namespaces':reads,'full_document_reads':reads.count('strategy_experiment_runs'),
                'filtered_total':response.json['filtered_total']}
        (out/'summary-1000-performance.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
    finally:core.close()
