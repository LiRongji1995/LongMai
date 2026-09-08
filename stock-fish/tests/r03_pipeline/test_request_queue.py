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


def test_same_session_repeated_hours_and_normalized_batch_share_logical_request(tmp_path):
    with RequestQueue(tmp_path) as q:
        first=q.enqueue('daily_limits_eastmoney',{'date':'20260908','codes':['sz.000001','600000.SH']},
            stage='TEST_FIXTURE',definition_version='TEST',cache_epoch='2026-09-08T16')
        again=q.enqueue('daily_limits_eastmoney',{'codes':['600000.SH','000001.SZ'],'date':'2026-09-08'},
            stage='OTHER_CONSUMER',definition_version='TEST',cache_epoch='2026-09-08T20')
        assert first==again and q.summary()['requested']==1
        assert q.summary()['active_logical_requests']==1
        assert json.loads(q.rows()[0]['payload'])['stages']==['OTHER_CONSUMER','TEST_FIXTURE']


def test_undated_snapshot_reuses_day_but_separates_session_and_provider(tmp_path):
    with RequestQueue(tmp_path) as q:
        def add_snapshot(day,provider='TEST_PROVIDER'):
            return q.enqueue('concept_members',{'symbol':'TEST_THEME'},stage='TEST_FIXTURE',
                definition_version='TEST',cache_epoch=day,provider=provider)
        first=add_snapshot('2026-09-08T16')
        assert first==add_snapshot('2026-09-08T20')
        assert first!=add_snapshot('2026-09-09T16')
        assert first!=add_snapshot('2026-09-08T16','OTHER_PROVIDER')


def test_legacy_hourly_duplicates_resume_one_verified_request_without_erasing_audit(tmp_path):
    from stockfish_core.yaogu.contracts import digest
    import sqlite3
    with RequestQueue(tmp_path) as q:
        for hour,state in [('16','PENDING'),('18','PENDING')]:
            identity={'endpoint':'concept_members','parameters':{'symbol':'TEST_THEME'},
                'definition_version':'TEST','queue_version':'MAINBOARD_REQUEST_QUEUE_R03_V1',
                'cache_epoch':'2026-09-08T'+hour}
            q.connection.execute('INSERT INTO requests(request_id,payload,state,updated_at) VALUES(?,?,?,?)',
                ('legacy_'+hour,json.dumps(dict(identity,stage='TEST_FIXTURE',dependencies=[])),state,'2026-09-08T'+hour+':00:00+08:00'))
        q.connection.commit()
    with RequestQueue(tmp_path) as q:
        rid=q.enqueue('concept_members',{'symbol':'TEST_THEME'},stage='TEST_FIXTURE',definition_version='TEST',
                      cache_epoch='2026-09-08T20')
        assert rid=='legacy_18'
        q.drain(lambda *a,**k:result())
        assert q.summary()['active_logical_requests']==1
        assert q.summary()['stored_request_rows']==2
        assert q.summary()['legacy_duplicate_rows_preserved']==1
    with RequestQueue(tmp_path) as q:
        assert q.enqueue('concept_members',{'symbol':'TEST_THEME'},stage='TEST_FIXTURE',definition_version='TEST',
                         cache_epoch='2026-09-08T21')==rid
        assert q.summary()['cache_hits_this_run']==1


def test_capture_priority_orders_core_before_enrichment_with_same_budget(tmp_path):
    calls=[]
    with RequestQueue(tmp_path,budget=RequestBudget(max_requests=3,minimum_interval_seconds=0)) as q:
        for ep,priority in [('concept_members',4),('daily_limits_eastmoney',3),('money_flow',5),
                            ('adjustment_factors',2),('market_daily',1),('mainboard_sse',0)]:
            q.enqueue(ep,{},stage='TEST_FIXTURE',definition_version='TEST',priority=priority)
        q.drain(lambda ep,*a,**kw:(calls.append(ep) or result()))
        assert calls==['mainboard_sse','market_daily','adjustment_factors']
        assert q.summary()['states']['PENDING']==3


def test_empty_core_response_is_not_a_complete_universe_and_resumes_same_identity(tmp_path):
    empty=result();empty['rows']=[]
    budget=RequestBudget(max_requests=2,max_attempts=1,minimum_interval_seconds=0)
    with RequestQueue(tmp_path,budget=budget) as q:
        rid=q.enqueue('master_all',{'date':'2026-09-08'},stage='TEST_FIXTURE',definition_version='TEST')
        q.drain(lambda *a,**k:empty)
        q.drain(lambda *a,**k:empty)
        assert q.calls==1 and q.rows([rid])[0]['state']=='PENDING'
        assert q.rows([rid])[0]['failure_category']=='SOURCE_SCOPE_EMPTY'
    with RequestQueue(tmp_path,budget=budget) as q:
        assert q.enqueue('master_all',{'date':'2026-09-08'},stage='TEST_FIXTURE',definition_version='TEST')==rid
        q.drain(lambda *a,**k:result())
        assert q.calls==1 and q.rows([rid])[0]['state']=='COMPLETE'
        assert q.rows([rid])[0]['attempts']==2


def test_preclose_complete_price_is_refreshed_after_release_without_new_request_id(tmp_path):
    from datetime import datetime
    morning=datetime.fromisoformat('2026-09-08T11:00:00+08:00')
    evening=datetime.fromisoformat('2026-09-08T20:30:00+08:00')
    response=result();response['receipts'][0]['first_seen_at']=morning.isoformat()
    budget=RequestBudget(max_attempts=1,minimum_interval_seconds=0)
    with RequestQueue(tmp_path,budget=budget,wall_clock=lambda:morning) as q:
        rid=q.enqueue('market_daily',{'date':'2026-09-08'},stage='TEST_FIXTURE',definition_version='TEST')
        q.drain(lambda *a,**k:response)
    with RequestQueue(tmp_path,budget=budget,wall_clock=lambda:evening) as q:
        assert q.enqueue('market_daily',{'date':'2026-09-08'},stage='TEST_FIXTURE',definition_version='TEST')==rid
        assert q.rows([rid])[0]['failure_category']=='PRE_FINAL_CAPTURE'
        final=result();final['receipts'][0]['first_seen_at']=evening.isoformat()
        q.drain(lambda *a,**k:final)
        assert q.rows([rid])[0]['state']=='COMPLETE'
        assert q.connection.execute('SELECT count(*) FROM attempts WHERE request_id=?',(rid,)).fetchone()[0]==2
        assert q.summary()['stored_request_rows']==1
