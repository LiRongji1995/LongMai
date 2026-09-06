"""Explicit-only F10 export probes (synthetic TEST_FIXTURE).
Filename intentionally lacks test_ so frozen broad runs do not auto-collect it.
Run directly: python -m pytest -q tests/r05_product/export_view_consistency_probe.py
"""
import csv,io,json
import pytest
from stockfish_core.service import ApplicationService
from stockfish_core.storage import SQLiteStore


@pytest.fixture
def service(tmp_path):
    core=ApplicationService(root=tmp_path/'state',store=SQLiteStore(tmp_path/'fixture.sqlite3'),allow_test_fixture=True,data_dir=tmp_path/'no_model')
    yield core.candidates
    core.close()


def save(service,*,mixed=False):
    base=[{'instrument_id':'TEST000','rank':1,'S_PV':90},{'instrument_id':'TEST001','rank':2,'S_PV':80}]
    dna=([{'instrument_id':'TEST000','rank':1,'S_DNA':85,'status':'EVALUATED'}] if mixed else [
        {'instrument_id':'TEST000','status':'INSUFFICIENT_COVERAGE','reason':'DNA_INSUFFICIENT_MATURE_HISTORY'}])
    dna.append({'instrument_id':'TEST001','status':'INSUFFICIENT_COVERAGE','reason':'DNA_INSUFFICIENT_MATURE_HISTORY'})
    run={'run_id':'TEST_EXPORT_MIXED' if mixed else 'TEST_EXPORT_UNAVAILABLE','input_id':'TEST_INPUT','observation_date':'2026-09-04',
         'implementation_version':'TEST_FIXTURE_ONLY','coverage_type':'TEST_FIXTURE',
         'results':{'PV_OBSERVATION_V1':{'strategy_id':'PV_OBSERVATION_V1','status':'COMPLETE','items':base},
                    'DNA_RERANK_V1':{'strategy_id':'DNA_RERANK_V1','status':'LIMITED_SCOPE' if mixed else 'INSUFFICIENT_COVERAGE','items':dna}},
         'selection_results':{'PV_FOCUS_PREVIEW_V0.1':{'selected_ids':['TEST000','TEST001']}}}
    service.store.put_document('strategy_experiment_runs',run['run_id'],run)
    with service.store.transaction() as connection:service._write_strategy_projection(connection,run)
    return run


@pytest.mark.parametrize('format',['csv','json'])
@pytest.mark.parametrize('mixed',[False,True])
def test_selected_strategy_export_rows_equal_summary_actual_rank_set(service,format,mixed):
    run=save(service,mixed=mixed);rid=run['run_id']
    summary=service.strategy_experiment_summary(rid,strategy_id='DNA_RERANK_V1',selection='focus')
    expected=[row['instrument_id'] for row in summary['items']]
    if format=='csv':
        rows=list(csv.DictReader(io.StringIO(service.export_strategy_experiment_csv(rid,strategy_id='DNA_RERANK_V1',selection='focus'))))
    else:
        exported=json.loads(service.export_strategy_experiment_json(rid,strategy_id='DNA_RERANK_V1',selection='focus'))
        rows=exported['results']['DNA_RERANK_V1']['items']
    assert [row['instrument_id'] for row in rows]==expected


def test_full_raw_json_preserves_unavailable_diagnostics_without_rewriting_history(service):
    run=save(service)
    exported=json.loads(service.export_strategy_experiment_json(run['run_id']))
    assert exported==run
    assert len(exported['results']['DNA_RERANK_V1']['items'])==2
    assert all(row['status']=='INSUFFICIENT_COVERAGE' for row in exported['results']['DNA_RERANK_V1']['items'])
    assert service.strategy_experiment(run['run_id'])==run


def test_csv_rank_export_explains_evaluation_status_and_focus_membership(service):
    run=save(service,mixed=True)
    rows=list(csv.DictReader(io.StringIO(service.export_strategy_experiment_csv(run['run_id'],strategy_id='DNA_RERANK_V1',selection='focus'))))
    assert rows and rows[0].get('status')=='EVALUATED'
    assert rows[0].get('selected_focus','').lower()=='true'
    assert 'reason_codes' in rows[0] or 'reason' in rows[0]
