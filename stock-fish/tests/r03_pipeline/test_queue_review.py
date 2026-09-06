"""Independent queue counterexamples. No live provider or user state."""
from pathlib import Path

from stockfish_core.yaogu.request_queue import RequestQueue,RequestBudget


def response(rows):
    return {'status':'COMPLETE','endpoint':'history','rows':rows,'pagination_complete':True,
        'request_receipt_id':'TEST_FIXTURE_RECEIPT','receipts':[{'receipt_id':'TEST_FIXTURE_RECEIPT',
        'status':'COMPLETE','pagination_complete':True}]}


def test_cached_history_does_not_invent_absent_security_or_session(tmp_path):
    with RequestQueue(tmp_path,budget=RequestBudget(minimum_interval_seconds=0)) as q:
        rid=q.enqueue('history',{'codes':['600000.SH','600001.SH'],'start_date':'2026-09-02','end_date':'2026-09-04'},
            stage='TEST_FIXTURE',definition_version='TEST_FIXTURE')
        q.drain(lambda *a,**k:response([{'code':'sh.600000','date':'2026-09-02','close':'10'},
                                     {'code':'sh.600000','date':'2026-09-04','tradestatus':'0','close':''}]))
        assert q.cached_history('history','600001.SH','2026-09-02','2026-09-04','TEST_FIXTURE')==[]
        fragments=q.cached_history('history','600000.SH','2026-09-02','2026-09-04','TEST_FIXTURE')
        assert not any(a<='2026-09-03'<=z for a,z,_ in fragments)
        assert any(a<='2026-09-04'<=z for a,z,_ in fragments),'known suspension row remains covered'


def test_rate_wait_cannot_start_another_call_after_time_budget(tmp_path):
    clock=[0.]
    calls=[]
    with RequestQueue(tmp_path,budget=RequestBudget(max_seconds=1,minimum_interval_seconds=1),
                      monotonic=lambda:clock[0],sleeper=lambda n:clock.__setitem__(0,clock[0]+n)) as q:
        for iid in ('600000.SH','600001.SH'):
            q.enqueue('history',{'codes':[iid]},stage='TEST_FIXTURE',definition_version='TEST_FIXTURE')
        def executor(*a,**k):
            calls.append(clock[0])
            return response([])
        q.drain(executor)
        assert len(calls)==1 and all(t<1 for t in calls)


def test_actual_calendar_gap_reopens_complete_request_for_bounded_recovery(tmp_path):
    sessions=['2026-09-02','2026-09-03','2026-09-04']
    with RequestQueue(tmp_path,budget=RequestBudget(minimum_interval_seconds=0)) as q:
        args={'codes':['600000.SH'],'start_date':sessions[0],'end_date':sessions[-1]}
        rid=q.enqueue('history',args,stage='TEST_FIXTURE',definition_version='TEST_FIXTURE')
        q.drain(lambda *a,**k:response([{'instrument_id':'600000.SH','session':sessions[-1],'close':10}]))
        parts=q.cached_history('history','600000.SH',sessions[0],sessions[-1],'TEST_FIXTURE',expected_sessions=sessions)
        assert parts==[(sessions[-1],sessions[-1],rid)]
        assert q.rows([rid])[0]['state']=='PARTIAL'
        assert q.enqueue('history',args,stage='RECOVERY',definition_version='TEST_FIXTURE')==rid
        q.drain(lambda *a,**k:response([{'instrument_id':'600000.SH','session':d,'close':10} for d in sessions]))
        assert q.calls==2
        assert q.cached_history('history','600000.SH',sessions[0],sessions[-1],'TEST_FIXTURE',expected_sessions=sessions)==[(sessions[0],sessions[-1],rid)]


def test_cancellation_during_rate_wait_does_not_issue_second_request(tmp_path):
    import pytest
    clock=[0.];calls=[]
    class Cancelled(Exception):pass
    def checkpoint():
        if clock[0]>.5:raise Cancelled()
    with RequestQueue(tmp_path,budget=RequestBudget(max_seconds=3,minimum_interval_seconds=1),
        monotonic=lambda:clock[0],sleeper=lambda n:clock.__setitem__(0,clock[0]+n)) as q:
        for iid in ('600000.SH','600001.SH'):
            q.enqueue('history',{'codes':[iid]},stage='TEST_FIXTURE',definition_version='TEST_FIXTURE')
        def executor(*a,**k):
            calls.append(clock[0]);return response([])
        with pytest.raises(Cancelled):q.drain(executor,checkpoint=checkpoint)
        assert len(calls)==1


def test_missing_dependency_remains_explicit_pending_instead_of_crashing_whole_drain(tmp_path):
    with RequestQueue(tmp_path) as q:
        rid=q.enqueue('history',{},stage='TEST_FIXTURE',definition_version='TEST_FIXTURE',dependencies=['UNREGISTERED_TEST_FIXTURE'])
        q.drain(lambda *a,**k:response([]))
        row=q.rows([rid])[0]
        assert q.calls==0 and row['state']=='PENDING'
        assert row['failure_category']=='DEPENDENCY_NOT_REGISTERED'


def test_returned_fragment_counts_real_cache_reuse_on_restart(tmp_path):
    budget=RequestBudget(minimum_interval_seconds=0)
    with RequestQueue(tmp_path,budget=budget) as q:
        q.enqueue('history',{'codes':['600000.SH'],'start_date':'2026-09-04','end_date':'2026-09-04'},stage='TEST_FIXTURE',definition_version='TEST_FIXTURE')
        q.drain(lambda *a,**k:response([{'code':'sh.600000','date':'2026-09-04','close':10}]))
    with RequestQueue(tmp_path,budget=budget) as q:
        for _ in range(2):q.cached_history('history','600000.SH','2026-09-04','2026-09-04','TEST_FIXTURE')
        assert q.cache_hits==1 and q.calls==0


def test_failed_retry_keeps_previously_archived_history_rows_available(tmp_path):
    sessions=['2026-09-02','2026-09-03']
    budget=RequestBudget(minimum_interval_seconds=0)
    with RequestQueue(tmp_path,budget=budget) as q:
        rid=q.enqueue('history',{'codes':['600000.SH'],'start_date':sessions[0],'end_date':sessions[-1]},
            stage='TEST_FIXTURE',definition_version='TEST_FIXTURE')
        q.drain(lambda *a,**k:response([{'code':'sh.600000','date':sessions[0],'close':10}]))
        q.cached_history('history','600000.SH',sessions[0],sessions[-1],'TEST_FIXTURE',expected_sessions=sessions)
        q.drain(lambda *a,**k:{'status':'FETCH_FAILED','rows':[],'receipts':[]})
        assert any(row.get('date')==sessions[0] for value in q.results([rid]) for row in value.get('rows',()))
        assert q.cached_history('history','600000.SH',sessions[0],sessions[-1],'TEST_FIXTURE',expected_sessions=sessions)==[(sessions[0],sessions[0],rid)]
    with RequestQueue(tmp_path,budget=budget) as q:
        assert q.cached_history('history','600000.SH',sessions[0],sessions[-1],'TEST_FIXTURE',expected_sessions=sessions)==[(sessions[0],sessions[0],rid)]


def test_retry_fragments_union_actual_days_without_unioning_current_master(tmp_path):
    sessions=['2026-09-02','2026-09-03']
    with RequestQueue(tmp_path,budget=RequestBudget(minimum_interval_seconds=0)) as q:
        rid=q.enqueue('history_adjusted',{'codes':['600000.SH'],'start_date':sessions[0],'end_date':sessions[-1]},
            stage='TEST_FIXTURE',definition_version='TEST_FIXTURE')
        q.drain(lambda *a,**k:response([{'code':'sh.600000','date':sessions[0],'close':10}]))
        q.cached_history('history_adjusted','600000.SH',sessions[0],sessions[-1],'TEST_FIXTURE',expected_sessions=sessions)
        q.drain(lambda *a,**k:response([{'code':'sh.600000','date':sessions[-1],'close':11}]))
        assert q.cached_history('history_adjusted','600000.SH',sessions[0],sessions[-1],'TEST_FIXTURE',expected_sessions=sessions)==[(sessions[0],sessions[-1],rid)]
        assert len(q.results([rid]))==2
        mid=q.enqueue('mainboard_sse',{},stage='TEST_FIXTURE',definition_version='TEST_FIXTURE')
        first=response([{'instrument_id':'600000.SH'}]);first['pagination_complete']=False
        q.drain(lambda *a,**k:first,ids=[mid])
        q.drain(lambda *a,**k:response([{'instrument_id':'600001.SH'}]),ids=[mid])
        assert [r['instrument_id'] for value in q.results([mid]) for r in value['rows']]==['600001.SH']


def test_latest_explicit_invalid_revision_is_not_hidden_by_old_valid_cache(tmp_path):
    sessions=['2026-09-02','2026-09-03']
    with RequestQueue(tmp_path,budget=RequestBudget(minimum_interval_seconds=0)) as q:
        rid=q.enqueue('history',{'codes':['600000.SH'],'start_date':sessions[0],'end_date':sessions[-1]},
            stage='TEST_FIXTURE',definition_version='TEST_FIXTURE')
        q.drain(lambda *a,**k:response([{'code':'sh.600000','date':sessions[0],'close':10}]))
        q.cached_history('history','600000.SH',sessions[0],sessions[-1],'TEST_FIXTURE',expected_sessions=sessions)
        q.drain(lambda *a,**k:response([{'code':'sh.600000','date':sessions[0],'close':''},
                                      {'code':'sh.600000','date':sessions[-1],'close':11}]))
        assert q.cached_history('history','600000.SH',sessions[0],sessions[-1],'TEST_FIXTURE',expected_sessions=sessions)==[(sessions[-1],sessions[-1],rid)]
        assert q.rows([rid])[0]['state']=='PARTIAL'


def test_new_overlapping_request_correction_is_not_hidden_by_an_old_range(tmp_path):
    with RequestQueue(tmp_path,budget=RequestBudget(minimum_interval_seconds=0)) as q:
        first=q.enqueue('history',{'codes':['600000.SH'],'start_date':'2026-09-02','end_date':'2026-09-03'},
            stage='TEST_FIXTURE',definition_version='TEST_FIXTURE')
        q.drain(lambda *a,**k:response([{'code':'sh.600000','date':'2026-09-02','close':10},
                                      {'code':'sh.600000','date':'2026-09-03','close':11}]),ids=[first])
        correction=q.enqueue('history',{'codes':['600000.SH'],'start_date':'2026-09-03','end_date':'2026-09-03'},
            stage='TEST_FIXTURE',definition_version='TEST_FIXTURE')
        q.drain(lambda *a,**k:response([{'code':'sh.600000','date':'2026-09-03','close':''}]),ids=[correction])
        parts=q.cached_history('history','600000.SH','2026-09-02','2026-09-03','TEST_FIXTURE',expected_sessions=['2026-09-02','2026-09-03'])
        assert parts==[('2026-09-02','2026-09-02',first)]


def test_adjustment_factor_partial_success_survives_failed_retry_with_source_metadata(tmp_path):
    budget=RequestBudget(minimum_interval_seconds=0)
    with RequestQueue(tmp_path,budget=budget) as q:
        rid=q.enqueue('adjustment_factors',{'codes':['600000.SH','600001.SH'],
            'start_date':'1990-01-01','end_date':'2026-09-04'},stage='TEST_FIXTURE',definition_version='TEST_FIXTURE')
        partial={'status':'PARTIAL','endpoint':'adjustment_factors','pagination_complete':False,
            'rows':[{'code':'sh.600000','dividOperateDate':'2026-09-02','backAdjustFactor':'2'}],
            'coverage':{'security_statuses':[
                {'code':'sh.600000','status':'COMPLETE','cursor_exhausted':True,'row_count':1,'captured_at':'2026-09-04T08:00:00+00:00'},
                {'code':'sh.600001','status':'FETCH_FAILED','cursor_exhausted':False,'row_count':0,'captured_at':'2026-09-04T08:00:01+00:00'}]},
            'request_receipt_id':'TEST_FIXTURE_FACTOR_PARTIAL','receipts':[
                {'receipt_id':'TEST_FIXTURE_FACTOR_PARTIAL','status':'PARTIAL','pagination_complete':False}]}
        q.drain(lambda *a,**k:partial)
        assert q.rows([rid])[0]['state']=='PARTIAL'
        q.drain(lambda *a,**k:{'status':'FETCH_FAILED','endpoint':'adjustment_factors','rows':[],
            'receipts':[],'coverage':{'security_statuses':[{'code':'sh.600000','status':'FETCH_FAILED',
                'cursor_exhausted':False,'row_count':0,'captured_at':'2026-09-04T08:01:00+00:00'}]}})
        values=q.results([rid])
        assert len(values)==2
        assert values[0]['rows']==partial['rows']
        assert values[0]['coverage']==partial['coverage']
        assert values[0]['receipts']==partial['receipts']
        assert values[0]['status']=='PARTIAL' and values[1]['status']=='FETCH_FAILED'
        assert q.rows([rid])[0]['state']!='COMPLETE' and q.calls==2
    with RequestQueue(tmp_path,budget=budget) as q:
        values=q.results([rid])
        assert len(values)==2 and values[0]['rows']==partial['rows']
        assert q.calls==0 and q.rows([rid])[0]['state']!='COMPLETE'
