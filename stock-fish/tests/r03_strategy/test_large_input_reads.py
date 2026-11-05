"""Scale boundary regressions use isolated TEST_FIXTURE state, never daily DBs."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
import time

import pytest

from tests.support.local_service import ResearchService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.candidate_service import CandidateRequest
from tests.yaogu.fixtures import make_bundle


@pytest.fixture
def product(tmp_path):
    store=SQLiteStore(tmp_path/'TEST_FIXTURE_reads.sqlite3')
    core=ResearchService(root=tmp_path/'TEST_FIXTURE_state',store=store,
                            allow_test_fixture=True,data_dir=tmp_path/'NO_QLIB')
    bundle,_=make_bundle()
    input_id=core.candidates.register_input(bundle)
    yield core,bundle,input_id
    core.close()


def test_diagnostic_coverage_selects_metadata_without_deserializing_all_input_history(product,monkeypatch):
    core,bundle,input_id=product
    original=core.candidates._all
    def limited(namespace):
        assert namespace!='candidate_inputs','coverage loaded all registered market payloads'
        return original(namespace)
    monkeypatch.setattr(core.candidates,'_all',limited)
    value=core.candidates.coverage()
    assert value['input_id']==input_id and value['bar_rows']==len(bundle.bars)


def test_storage_health_hashes_current_bytes_without_materializing_payload_objects(product,monkeypatch):
    core,_,_=product
    import stockfish_core.storage as storage
    def forbidden(row):raise AssertionError('health deserialized an entire immutable market input')
    monkeypatch.setattr(storage,'checked_payload',forbidden)
    health=core.store.health()
    assert health['status']=='healthy'
    assert health['payload_rows_checked']['documents']>=2




def test_repeated_concurrent_coverage_reuses_only_verified_compact_projection(product,monkeypatch):
    core,bundle,input_id=product
    calls=[];original=core.candidates.input
    def count(*args,**kwargs):
        calls.append(args)
        return original(*args,**kwargs)
    monkeypatch.setattr(core.candidates,'input',count)
    with ThreadPoolExecutor(max_workers=3) as pool:
        values=list(pool.map(lambda _:core.candidates.coverage(),range(3)))
    assert all(v['input_id']==input_id and v['bar_rows']==len(bundle.bars) for v in values)
    assert len(calls)==1,'same input was re-decoded or read simultaneously for coverage'
    projection=core.candidates._coverage_projection_cache[1]
    assert projection.universe.securities==() and projection.themes==() and projection.funds==()
    assert all(b.session==bundle.observation_date for b in projection.bars)
    # The public result must not be the cache itself.
    values[0]['current_event_previews'].clear()
    assert core.candidates.coverage()['current_event_previews']


@pytest.mark.parametrize('update_hash',[False,True])
def test_cached_projection_rechecks_database_bytes_and_content_address_every_request(product,update_hash):
    core,_,input_id=product
    assert core.candidates.coverage()['input_id']==input_id
    with sqlite3.connect(core.store.path) as db:
        db.execute('DROP TRIGGER documents_no_update')
        original=db.execute('SELECT payload FROM documents WHERE namespace=? AND document_id=?',('candidate_inputs',input_id)).fetchone()[0]
        changed=json.loads(original);changed['receipts'][0]['row_count']+=1
        raw=json.dumps(changed,sort_keys=True,ensure_ascii=False,separators=(',',':'))
        if update_hash:
            db.execute('UPDATE documents SET payload=?,content_hash=? WHERE namespace=? AND document_id=?',
                       (raw,hashlib.sha256(raw.encode()).hexdigest(),'candidate_inputs',input_id))
        else:
            db.execute('UPDATE documents SET payload=? WHERE namespace=? AND document_id=?',(raw,'candidate_inputs',input_id))
    value=core.candidates.coverage()
    assert value['status']=='UNAVAILABLE' and not value.get('current_event_previews')


def test_health_streaming_hash_rejects_corruption_and_same_named_weak_trigger(product):
    core,_,input_id=product
    with sqlite3.connect(core.store.path) as db:
        db.execute('DROP TRIGGER documents_no_update')
        db.execute("CREATE TRIGGER documents_no_update BEFORE UPDATE ON documents BEGIN SELECT 1; END")
        db.execute("UPDATE documents SET payload='{}' WHERE namespace='candidate_inputs' AND document_id=?",(input_id,))
    value=core.store.health()
    assert value['status']=='unavailable' and not value['immutable_triggers']
    assert value['corrupt_payload_rows']['documents']==1


def test_coverage_correct_input_publication_is_not_hidden_by_other_input_pages(product,monkeypatch):
    core,_,input_id=product
    job=core.candidates.submit(CandidateRequest(input_id=input_id))
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        job=core.jobs.get(job['job_id'])
        if job['state'] in {'SUCCEEDED','FAILED','CANCELLED'}:break
        time.sleep(.005)
    assert job['state']=='SUCCEEDED',job
    target=job['result']['candidate_run_id']
    expected=core.candidates.get_run(target)
    with core.store.transaction() as db:
        for index in range(1001):
            core.store._put_document(db,'candidate_publications',f'TEST_OTHER_{index:05}',
                {'input_id':'TEST_OTHER_INPUT','job_id':'TEST_NOT_SELECTED','run_hash':'TEST_NOT_SELECTED'})
    # A last-100 global run page contains only unrelated inputs. It must never
    # become the search space for this input's latest authorized publication.
    monkeypatch.setattr(core.candidates,'list_runs',lambda limit=100:[])
    value=core.candidates.coverage()
    assert value['consumer_status']==expected.status
    assert value['coverage']==expected.coverage
