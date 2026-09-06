"""TEST_FIXTURE: durable provider faults, not claims about market coverage."""
import json
import pytest
from stockfish_core.yaogu.request_queue import RequestQueue, RequestBudget


def result(status='COMPLETE', pages=True):
    return {'status': status, 'rows': [{'TEST_FIXTURE': True}], 'request_receipt_id': 'test',
            'receipts': [{'receipt_id': 'test', 'status': status, 'pagination_complete': pages}],
            'pagination_complete': pages}


def add(q, code='a', version='TEST_FIXTURE_V1'):
    return q.enqueue('fixture.history', {'codes':[code]}, stage='StructureHistoryCapture', definition_version=version)


def test_budget_resume_dedup_and_verified_cache(tmp_path):
    calls=[]
    def execute(ep, params, root, timeout):
        calls.append(params)
        return result()
    with RequestQueue(tmp_path, budget=RequestBudget(max_requests=1, minimum_interval_seconds=0)) as q:
        a,b=add(q,'a'),add(q,'b')
        assert add(q,'a')==a
        report=q.drain(execute)
        assert report['requested']==2 and report['states']['COMPLETE']==1
        assert report['budget_exhausted'] and report['status']=='PARTIAL'
    with RequestQueue(tmp_path, budget=RequestBudget(max_requests=1, minimum_interval_seconds=0)) as q:
        add(q,'a');add(q,'b')
        assert q.summary()['cache_hits_this_run']==1
        assert q.drain(execute)['status']=='COMPLETE'
    assert len(calls)==2


def test_partial_page_is_not_cached_as_complete(tmp_path):
    with RequestQueue(tmp_path) as q:
        add(q)
        r=q.drain(lambda *a,**kw:result(pages=False))
        assert r['states']['PARTIAL']==1 and r['status']=='PARTIAL'
        assert q.results()[0]['rows']


def test_timeout_finite_attempts_never_fake_data(tmp_path):
    def fail(*a,**kw):raise TimeoutError('ignored possible sensitive exception text')
    with RequestQueue(tmp_path, budget=RequestBudget(minimum_interval_seconds=0)) as q:
        add(q)
        for _ in range(4):q.drain(fail)
        assert q.calls==2 and q.summary()['states']['BLOCKED']==1
        assert q.results()[0]['error_code']=='TIMEOUT' and q.results()[0]['rows']==[]


def test_crash_releases_lock_and_requeues_work(tmp_path):
    class PowerLoss(BaseException):pass
    def crash(*a,**kw):raise PowerLoss()
    with RequestQueue(tmp_path) as q:
        add(q)
        with pytest.raises(PowerLoss):q.drain(crash)
        assert q.summary()['states']['RUNNING']==1
    with RequestQueue(tmp_path) as q:
        add(q)
        assert q.summary()['states']['PENDING']==1
        assert q.drain(lambda *a,**kw:result())['status']=='COMPLETE'


def test_source_definition_change_cannot_reuse_old_response(tmp_path):
    with RequestQueue(tmp_path, budget=RequestBudget(minimum_interval_seconds=0)) as q:
        first=add(q)
        q.drain(lambda *a,**kw:result())
        second=add(q,version='TEST_FIXTURE_V2')
        assert first!=second and q.summary()['states']['PENDING']==1


def test_tampered_cache_is_rejected_before_reuse(tmp_path):
    with RequestQueue(tmp_path) as q:
        add(q);q.drain(lambda *a,**kw:result())
        path=tmp_path/q.rows()[0]['result_path']
        path.write_text(json.dumps({'status':'COMPLETE'}))
    with RequestQueue(tmp_path) as q:
        with pytest.raises(ValueError,match='CACHE_CONTENT_CORRUPT'):add(q)


@pytest.mark.parametrize('kw',[{'max_requests':float('nan')},{'timeout_seconds':float('inf')},{'max_attempts':100},{'max_requests':True}])
def test_invalid_budgets_are_rejected(kw):
    with pytest.raises(ValueError):RequestBudget(**kw)


def test_dependencies_and_actual_provider_rate_limit(tmp_path):
    with RequestQueue(tmp_path,budget=RequestBudget(max_requests=1)) as q:
        a=add(q)
        b=q.enqueue('fixture.dependent',{},stage='score',definition_version='TEST',dependencies=[a])
        q.drain(lambda *a,**kw:result(),ids=[b])
        assert q.calls==0


def test_only_authoritative_page_proof_controls_request_completeness(tmp_path):
    response=result()
    response['receipts'].append({'receipt_id':'raw_http','status':'COMPLETE','pagination_complete':False})
    with RequestQueue(tmp_path) as q:
        add(q)
        assert q.drain(lambda *a,**kw:response)['status']=='COMPLETE'
