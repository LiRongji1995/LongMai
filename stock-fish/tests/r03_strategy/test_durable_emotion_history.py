"""Actual JobService publications and restart; no fabricated DB success rows."""
from datetime import datetime,time,timedelta
from statistics import mean
import time as walltime
import pytest
from stockfish_core.common import UnavailableError
from stockfish_core.jobs import JobService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.candidate_service import CandidateService,CandidateRequest
from stockfish_core.yaogu.config import StrategyConfig
from stockfish_core.yaogu.contracts import CandidateRun,digest
from tests.yaogu.fixtures import make_bundle,TZ
from tests.r03_strategy.test_history_partition_hashes import history


class FixtureClock:
    def __init__(self,current):self.current=current
    def __call__(self):return self.current


def finish(service,started):
    deadline=walltime.monotonic()+15
    while walltime.monotonic()<deadline:
        job=service.jobs.get(started['job_id'])
        if job['state'] in ('SUCCEEDED','FAILED','CANCELLED','INTERRUPTED'):
            assert job['state']=='SUCCEEDED',job
            return service.get_run(job['result']['candidate_run_id'])
        walltime.sleep(.005)
    pytest.fail('bounded fixture job did not finish')


def open_service(tmp_path):
    store=SQLiteStore(tmp_path/'TEST_FIXTURE_history.sqlite3')
    jobs=JobService(store)
    return CandidateService(store,jobs,tmp_path/'TEST_FIXTURE_state',allow_test_fixture=True)


@pytest.fixture(autouse=True)
def transaction_clock_matches_fixture_service_clock(monkeypatch):
    import stockfish_core.jobs as jobs_module
    import stockfish_core.yaogu.candidate_service as candidate_module
    monkeypatch.setattr(jobs_module,'utc_now',lambda:candidate_module.utc_now())


def publish(service,bundle,clock,day):
    clock.current=datetime.combine(day,time(20,30),TZ)
    current=bundle.model_copy(update={'observation_date':day})
    input_id=service.register_input(current,activate=True)
    return finish(service,service.submit(CandidateRequest(input_id=input_id))),input_id


def test_three_real_jobs_restart_then_fourth_reads_authorized_history(tmp_path,monkeypatch):
    import stockfish_core.yaogu.candidate_service as module
    bundle,cutoff=make_bundle();clock=FixtureClock(cutoff);monkeypatch.setattr(module,'utc_now',clock)
    service=open_service(tmp_path);days=bundle.sessions[-4:];published=[]
    try:
        for day in days[:3]:
            result,_=publish(service,bundle,clock,day);published.append(result)
            assert result.emotion.score is not None
            assert result.emotion.readings['d'] is None
        old_bytes=[service.store.get_document('candidate_runs',r.run_id) for r in published]
    finally:service.jobs.shutdown()
    service=open_service(tmp_path)
    try:
        fourth,input_id=publish(service,bundle,clock,days[3])
        selection=fourth.emotion.history_selection
        assert selection['status']=='COMPLETE'
        assert [x['document_id'] for x in selection['selected']]==[r.run_id for r in published]
        assert fourth.emotion.readings['d']==pytest.approx(fourth.emotion.score-mean(r.emotion.score for r in published))
        assert fourth.raw_bundle_hash==input_id.removeprefix('market_')
        assert fourth.effective_dependencies['emotion_history']['selected']==selection['selected']
        assert [service.store.get_document('candidate_runs',r.run_id) for r in published]==old_bytes
        clock.current+=timedelta(minutes=1)
        again=finish(service,service.submit(CandidateRequest(input_id=input_id)))
        assert again.semantic_hash==fourth.semantic_hash
        assert again.effective_input_hash==fourth.effective_input_hash
    finally:service.jobs.shutdown()


def test_product_ignores_injected_history_and_missing_publication_authority(tmp_path,monkeypatch):
    import stockfish_core.yaogu.candidate_service as module
    b,c=make_bundle();clock=FixtureClock(c);monkeypatch.setattr(module,'utc_now',clock)
    service=open_service(tmp_path)
    try:
        forged=history(b)
        for e in forged:service.store.put_document('market_emotions',e.document_id,e.model_dump(mode='json'))
        iid=service.register_input(b.model_copy(update={'previous_emotions':forged}),activate=True)
        r=finish(service,service.submit(CandidateRequest(input_id=iid)))
        assert r.emotion.readings['d'] is None and r.emotion.history_selection['selected']==[]
        assert len(r.emotion.history_selection['missing_sessions'])==3
    finally:service.jobs.shutdown()


def test_latest_visible_day_revision_and_new_config_do_not_mix(tmp_path,monkeypatch):
    import stockfish_core.yaogu.candidate_service as module
    b,c=make_bundle();clock=FixtureClock(c);monkeypatch.setattr(module,'utc_now',clock)
    service=open_service(tmp_path)
    try:
        days=b.sessions[-4:];prior=[]
        for day in days[:3]:prior.append(publish(service,b,clock,day)[0])
        # A later run on the same past session is not three separate days.
        clock.current=datetime.combine(days[-1],time(21),TZ)
        old_input='market_'+digest(b.model_copy(update={'observation_date':days[-2]}))
        late=finish(service,service.submit(CandidateRequest(input_id=old_input)))
        clock.current=datetime.combine(days[-1],time(20,30),TZ)
        today=b.model_copy(update={'observation_date':days[-1]})
        iid=service.register_input(today,activate=True)
        r=finish(service,service.submit(CandidateRequest(input_id=iid)))
        assert r.emotion.history_selection['selected'][-1]['document_id']==prior[-1].run_id
        assert late.run_id not in [x['document_id'] for x in r.emotion.history_selection['selected']]
        clock.current+=timedelta(hours=1)
        latest=finish(service,service.submit(CandidateRequest(input_id=iid)))
        assert latest.emotion.history_selection['selected'][-1]['document_id']==late.run_id
        changed=StrategyConfig(strategy_id='TEST_FIXTURE_NEW_CONFIG')
        service.register_config(changed)
        other=finish(service,service.submit(CandidateRequest(input_id=iid)))
        assert other.emotion.readings['d'] is None and other.emotion.history_selection['selected']==[]
        assert service.get_run(r.run_id)==r
    finally:service.jobs.shutdown()


def test_legacy_config_hash_and_old_run_remain_readable_but_new_publish_requires_version(tmp_path,monkeypatch):
    import stockfish_core.yaogu.candidate_service as module
    b,c=make_bundle();clock=FixtureClock(c);monkeypatch.setattr(module,'utc_now',clock)
    service=open_service(tmp_path)
    try:
        old=StrategyConfig(strategy_id='TEST_FIXTURE_LEGACY_CUSTOM',feature_definition_version='YAOGU_FEATURES_V1')
        saved=service.register_config(old,activate=False)
        assert service.config(old.config_hash).model_dump(mode='json')==saved['config']
        iid=service.register_input(b)
        with pytest.raises(UnavailableError,match='LEGACY_STRATEGY_READ_ONLY'):
            service.submit(CandidateRequest(input_id=iid,config_hash=old.config_hash))
        current=finish(service,service.submit(CandidateRequest(input_id=iid)))
        legacy=current.model_dump(mode='json')
        for key in ('hash_contract_version','raw_bundle_hash','effective_input_hash','semantic_hash_version','effective_dependencies'):legacy.pop(key)
        parsed=CandidateRun.model_validate(legacy)
        assert parsed.hash_contract_version=='LEGACY_R02_NARROW_INPUT_V1' and parsed.raw_bundle_hash is None
    finally:service.jobs.shutdown()


def test_stale_default_rejected_but_explicit_history_remains_readable(tmp_path,monkeypatch):
    import stockfish_core.yaogu.candidate_service as module
    b,c=make_bundle();clock=FixtureClock(c);monkeypatch.setattr(module,'utc_now',clock)
    service=open_service(tmp_path)
    try:
        iid=service.register_input(b,activate=True)
        r=finish(service,service.submit(CandidateRequest()))
        clock.current=c+timedelta(days=3)
        cov=service.coverage()
        assert cov['stale'] and cov['status']=='STALE' and cov['observation_age_sessions']==1
        assert cov['observation_date']==str(b.observation_date)
        with pytest.raises(UnavailableError,match='ACTIVE_INPUT_STALE'):
            service.submit(CandidateRequest())
        assert finish(service,service.submit(CandidateRequest(input_id=iid))).observation_date==b.observation_date
        assert service.get_run(r.run_id)==r
    finally:service.jobs.shutdown()


@pytest.mark.parametrize('strategy_id,expected',[('YAOGU_V1.0.0','YAOGU_V1.1.0'),('TEST_FIXTURE_CUSTOM_LEGACY','TEST_FIXTURE_CUSTOM_LEGACY')])
def test_restart_migrates_only_original_default_without_rewriting_registration(tmp_path,monkeypatch,strategy_id,expected):
    import stockfish_core.yaogu.candidate_service as module
    b,c=make_bundle();clock=FixtureClock(c);monkeypatch.setattr(module,'utc_now',clock)
    service=open_service(tmp_path)
    try:
        old=StrategyConfig(strategy_id=strategy_id,feature_definition_version='YAOGU_FEATURES_V1')
        saved=service.register_config(old,activate=True)
        assert service.current_config().strategy_id==strategy_id
    finally:service.jobs.shutdown()
    reopened=open_service(tmp_path)
    try:
        assert reopened.current_config().strategy_id==expected
        assert reopened.store.get_document('strategy_configs',old.config_hash)==saved
        assert reopened.config(old.config_hash).config_hash==old.config_hash
    finally:reopened.jobs.shutdown()
