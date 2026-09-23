"""Daily loop scheduling contracts; isolated TEST_FIXTURE state, no providers."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from stockfish_core.jobs import JobCancelled, JobConflict
from stockfish_core.yaogu.scheduler import CandidateScheduler, SH


class FixtureClock:
    def __init__(self, current):
        self.current = current

    def __call__(self):
        return self.current


class FixtureStore:
    def __init__(self):
        self.rows = {}

    def get_document(self, namespace, key):
        return deepcopy(self.rows.get((namespace, key)))

    def put_document(self, namespace, key, value):
        old = self.rows.setdefault((namespace, key), deepcopy(value))
        assert old == value, 'TEST_FIXTURE immutable record overwritten'

    def _put_document(self, connection, namespace, key, value):
        self.put_document(namespace, key, value)


class FixtureJobs:
    def __init__(self):
        self.rows = {}

    def get(self, key):
        return deepcopy(self.rows.get(key))

    def list(self, *, kind, limit, offset):
        return deepcopy([row for row in self.rows.values() if row['kind'] == kind][offset:offset + limit])

    def submit(self, kind, payload, *, job_id):
        if job_id in self.rows:
            if self.rows[job_id]['payload'] != payload:
                raise JobConflict('TEST_FIXTURE payload differs')
            return self.get(job_id)
        self.rows[job_id] = {'job_id': job_id, 'kind': kind, 'payload': deepcopy(payload),
                            'state': 'QUEUED', 'result': None, 'error': None}
        return self.get(job_id)


class FixtureContext:
    def __init__(self, payload, job_id='TEST_FIXTURE_primary'):
        self.payload, self.job_id = payload, job_id
        self.failed = None
        self.completed = None

    def check_cancelled(self):
        return None

    def update(self, *args, **kwargs):
        return None

    def complete(self, result, writer):
        writer(None)
        self.completed = result
        return result

    def fail(self, result, error, writer):
        writer(None)
        self.failed = (result, error)
        return result


@pytest.fixture
def scheduler(tmp_path):
    clock = FixtureClock(datetime(2026, 9, 21, 20, 30, tzinfo=SH))
    config = SimpleNamespace(config_hash='TEST_FIXTURE_config', funds_primary=False,
                             schedule=SimpleNamespace(evening='20:30'))
    service = SimpleNamespace(root=tmp_path / 'TEST_FIXTURE_state', store=FixtureStore(), jobs=FixtureJobs(),
                              current_config=lambda: config, config=lambda _: config)
    scheduler = object.__new__(CandidateScheduler)
    scheduler.service, scheduler.clock, scheduler.monotonic = service, clock, lambda: 0.0
    scheduler.started_at = clock.current
    scheduler.sessions = (date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23))
    scheduler.custom_capture = False
    scheduler._latest_run = lambda *args, **kwargs: None
    service.register_input = lambda bundle, **kwargs: 'market_TEST_FIXTURE'
    service._all = lambda namespace: [deepcopy(value) for (ns, key), value in service.store.rows.items() if ns == namespace]
    service._daily_primary_attempt = lambda **kwargs: kwargs
    service._write_daily_primary_attempt = lambda connection, row: service.store._put_document(
        connection, 'daily_primary_attempts', row['job_id'], row)
    return scheduler


def primary_payload(scheduler):
    plan = scheduler._primary_schedule_for(scheduler.clock.current.date())
    return {'market_session': 'EVENING', 'session': str(scheduler.clock.current.date()),
            'scheduled_at': plan['scheduled_at'], 'actual_requested_at': scheduler.clock.current.isoformat(),
            'config_hash': 'TEST_FIXTURE_config', 'validation_epoch_id': plan['validation_epoch_id'],
            'research_identity': plan,
            'root_job_id': plan['job_id'], 'attempt_number': 1,
            'delivery_deadline': scheduler._delivery_deadline(scheduler.clock.current.date()).isoformat()}


def completed_primary(scheduler, *, status='NOT_READY', state='SUCCEEDED', error=None):
    payload = primary_payload(scheduler)
    job_id = payload['root_job_id']
    scheduler.service.jobs.submit('scheduled_candidate_capture', payload, job_id=job_id)
    scheduler.service.jobs.rows[job_id].update(state=state,
        result={'daily_primary_final_status': status}, error=error)
    return job_id


def test_evening_capture_passes_critical_purpose_and_expected_session(scheduler):
    calls = []
    day = scheduler.clock.current.date()
    scheduler._capture = lambda *args, **kwargs: calls.append(kwargs) or SimpleNamespace(
        observation_date=day, coverage_type='TEST_FIXTURE')
    scheduler.service._candidate_job = lambda ctx, payload, **kwargs: {'candidate_payload': payload}
    result = scheduler._capture_job(FixtureContext(primary_payload(scheduler)))
    assert calls == [{'purpose': 'DAILY_PRIMARY_CRITICAL', 'observation_date': day}]
    assert result['candidate_payload']['expected_observation_session'] == str(day)
    assert result['candidate_payload']['market_session'] == 'EVENING'


def test_scheduled_payload_freezes_identity_from_its_original_plan(scheduler):
    config = scheduler.service.current_config()
    config.schedule.preliminary, config.schedule.auction_review = '16:10', '09:26'
    scheduler.plan = lambda: {}
    scheduler.ensure_primary_validation_schedule = lambda now: {}
    plan = scheduler._primary_schedule_for(scheduler.clock.current.date())
    event = next(row for row in scheduler._tick() if row.get('market_session') == 'EVENING')
    payload = scheduler.service.jobs.get(event['job_id'])['payload']
    assert payload['research_identity'] == plan
    assert payload['research_identity']['signal_implementation_version']
    assert payload['research_identity']['signal_config_hash']
    assert payload['research_identity']['validation_epoch_id'] == payload['validation_epoch_id']


def test_failure_is_persisted_with_safe_stage_and_no_exception_secret(scheduler):
    def failed(*args, **kwargs):
        raise RuntimeError('TEST_FIXTURE_SECRET_must_not_be_persisted')
    scheduler._capture = failed
    context = FixtureContext(primary_payload(scheduler))
    result = scheduler._capture_job(context)
    assert context.failed is not None and result['final_status'] == 'FAILED'
    assert result['stage'] == 'CAPTURE' and result['timestamp'] == scheduler.clock.current.isoformat()
    assert result['error']['type'] == 'RuntimeError'
    assert 'TEST_FIXTURE_SECRET' not in str(scheduler.service.store.rows)
    assert scheduler.service.store.get_document('daily_primary_attempts', context.job_id) == result


def test_deadline_after_capture_prevents_publication(scheduler):
    timer = [0.0]
    scheduler.monotonic = lambda: timer[0]
    def capture(*args, **kwargs):
        timer[0] = scheduler.PRIMARY_TIMEOUT_SECONDS
        return SimpleNamespace(observation_date=scheduler.clock.current.date(), coverage_type='TEST_FIXTURE')
    scheduler._capture = capture
    scheduler.service._candidate_job = lambda *args, **kwargs: pytest.fail('late primary must not publish')
    result = scheduler._capture_job(FixtureContext(primary_payload(scheduler)))
    assert result['final_status'] == 'FAILED' and result['error']['code'] == 'DAILY_PRIMARY_TIMEOUT'


def test_commit_deadline_prevents_late_recommendation(scheduler):
    scheduler._capture = lambda *args, **kwargs: SimpleNamespace(
        observation_date=scheduler.clock.current.date(), coverage_type='TEST_FIXTURE')
    def candidate(ctx, *args, **kwargs):
        ctx.stage, ctx.strategy_started = 'STRATEGY', True
        scheduler.clock.current = datetime.fromisoformat(ctx.payload['delivery_deadline'])
        return ctx.complete({'recommendation_published': True}, lambda _: pytest.fail('late transaction'))
    scheduler.service._candidate_job = candidate
    result = scheduler._capture_job(FixtureContext(primary_payload(scheduler)))
    assert result['final_status'] == 'FAILED' and result['stage'] == 'PERSISTENCE'
    assert result['strategy_started'] is True


def test_cancel_is_not_reclassified_as_failure(scheduler):
    scheduler._capture = lambda *args, **kwargs: (_ for _ in ()).throw(JobCancelled('TEST_FIXTURE_cancel'))
    with pytest.raises(JobCancelled):
        scheduler._capture_job(FixtureContext(primary_payload(scheduler)))
    assert scheduler.service.store.rows == {}


def test_not_ready_retries_are_bounded_delayed_deduplicated_and_keep_original_clock(scheduler):
    root = completed_primary(scheduler)
    assert scheduler.schedule_primary_retries() == []
    original = scheduler.service.jobs.get(root)
    scheduler.clock.current += timedelta(minutes=4)
    assert scheduler.schedule_primary_retries() == []
    assert scheduler._primary_blocks_background(scheduler.clock.current)
    scheduler.clock.current += timedelta(minutes=1)
    retry = scheduler.schedule_primary_retries()[0]
    assert retry['job_id'] == root + '_retry_1'
    payload = scheduler.service.jobs.get(retry['job_id'])['payload']
    assert payload['scheduled_at'] == original['payload']['scheduled_at']
    assert payload['actual_requested_at'] != original['payload']['actual_requested_at']
    assert scheduler.schedule_primary_retries() == []
    scheduler.service.jobs.rows[retry['job_id']].update(state='SUCCEEDED', result={'final_status': 'NOT_READY'})
    scheduler.schedule_primary_retries()
    scheduler.clock.current += timedelta(minutes=14)
    assert scheduler.schedule_primary_retries() == []
    scheduler.clock.current += timedelta(minutes=1)
    second = scheduler.schedule_primary_retries()[0]
    scheduler.service.jobs.rows[second['job_id']].update(state='FAILED', error={'type': 'TimeoutError'})
    scheduler.clock.current += timedelta(hours=1)
    assert scheduler.schedule_primary_retries() == []
    assert len(scheduler.service.jobs.rows) == 3
    assert scheduler.service.jobs.get(root) == original


@pytest.mark.parametrize('state,status,error', [
    ('SUCCEEDED', 'READY', None), ('CANCELLED', '', None),
    ('FAILED', '', {'type': 'ImmutableConflict'}), ('FAILED', '', {'type': 'ValueError'})])
def test_published_cancelled_and_nonretryable_jobs_do_not_retry(scheduler, state, status, error):
    completed_primary(scheduler, status=status, state=state, error=error)
    scheduler.schedule_primary_retries()
    scheduler.clock.current += timedelta(hours=1)
    assert scheduler.schedule_primary_retries() == []


def test_interrupted_job_retries_but_target_deadline_stops_new_attempts(scheduler):
    root = completed_primary(scheduler, status='', state='INTERRUPTED')
    scheduler.schedule_primary_retries()
    scheduler.clock.current += timedelta(minutes=5)
    assert scheduler.schedule_primary_retries()[0]['retry_of'] == root
    retry = scheduler.service.jobs.rows[root + '_retry_1']
    retry.update(state='FAILED', error={'type': 'TimeoutError'})
    scheduler.clock.current = datetime(2026, 9, 22, 8, 45, tzinfo=SH)
    assert scheduler.schedule_primary_retries() == []


def test_missed_evening_never_becomes_a_fabricated_retry(scheduler):
    root = scheduler._primary_schedule_for(scheduler.clock.current.date())['job_id']
    scheduler.service.store.put_document('candidate_schedule_events', root,
        {'status': 'MISSED_SERVICE_OFFLINE', 'snapshot_created': False})
    scheduler.clock.current += timedelta(hours=1)
    assert scheduler.schedule_primary_retries() == []
    assert scheduler.service.jobs.rows == {}


def test_explicit_retry_is_in_primary_family_and_prevents_automatic_duplicate(scheduler):
    root = completed_primary(scheduler, state='FAILED', status='', error={'type': 'TimeoutError'})
    scheduler.schedule_primary_retries()
    scheduler.clock.current += timedelta(minutes=5)
    payload = deepcopy(scheduler.service.jobs.get(root)['payload'])
    scheduler.service.jobs.submit('scheduled_candidate_capture', payload, job_id='TEST_FIXTURE_random_retry_id')
    assert scheduler.schedule_primary_retries() == []
    assert len(scheduler._primary_jobs(scheduler.clock.current.date())[1]) == 2
    assert scheduler._primary_blocks_background(scheduler.clock.current)


def test_explicit_retry_queued_before_publication_reuses_committed_primary(scheduler):
    root = completed_primary(scheduler, status='READY')
    scheduler.service.jobs.rows[root]['result']['stage_aware_primary_run_id'] = 'TEST_FIXTURE_saved_run'
    scheduler._capture = lambda *args, **kwargs: pytest.fail('must reuse the already committed recommendation')
    context = FixtureContext(primary_payload(scheduler), 'TEST_FIXTURE_explicit_retry')
    result = scheduler._capture_job(context)
    assert result['stage_aware_primary_run_id'] == 'TEST_FIXTURE_saved_run'
    assert result['reused_primary_job_id'] == root
    assert result['duplicate_capture_prevented'] is True
    assert context.completed == result


def test_duplicate_guard_never_relabels_old_epoch_as_current_primary(scheduler):
    completed_primary(scheduler, status='READY')
    scheduler._capture = lambda *args, **kwargs: pytest.fail('old epoch must fail before capture')
    payload = primary_payload(scheduler)
    payload['validation_epoch_id'] = 'TEST_FIXTURE_OLD_EPOCH'
    result = scheduler._capture_job(FixtureContext(payload, 'TEST_FIXTURE_old_epoch_retry'))
    assert result['final_status'] == 'FAILED'
    assert result['error']['type'] == 'ValueError'
    assert 'reused_primary_job_id' not in result


def test_offline_reconciliation_only_marks_registered_unexecuted_schedules(scheduler):
    original = scheduler.clock.current
    for day in (date(2026, 9, 18), date(2026, 9, 21)):
        plan = scheduler._primary_schedule_for(day)
        scheduler.service.store.put_document('stage_aware_prospective_validation_schedules', str(day), plan)
    scheduler.started_at = original + timedelta(days=1)
    scheduler.clock.current = scheduler.started_at
    events = scheduler.reconcile_missed_primary_schedules()
    assert len(events) == 2
    assert all(row['status'] == 'MISSED_SERVICE_OFFLINE' and row['snapshot_created'] is False for row in events)
    assert all(row['observed_at'] == scheduler.clock.current.isoformat() for row in events)
    assert {row['signal_session'] for row in events} == {'2026-09-18', '2026-09-21'}
    assert scheduler.reconcile_missed_primary_schedules() == []
    assert scheduler.service.jobs.rows == {}


def test_background_capture_waits_for_primary_then_uses_small_full_capture_budget(scheduler):
    completed_primary(scheduler)
    scheduler.clock.current = scheduler.clock.current.replace(hour=21, minute=15)
    assert scheduler.schedule_background_capture() == []
    root, _ = scheduler._primary_jobs(scheduler.clock.current.date())
    scheduler.service.jobs.rows[root]['result'] = {'daily_primary_final_status': 'READY'}
    event = scheduler.schedule_background_capture()[0]
    assert scheduler.schedule_background_capture() == []
    calls = []
    scheduler._capture = lambda *args, **kwargs: calls.append(kwargs) or SimpleNamespace(
        observation_date=scheduler.clock.current.date(), coverage_type='TEST_FIXTURE')
    scheduler.last_capture_report = {'queue': {'pending_request_ids': ['TEST_FIXTURE_pending']}}
    context = FixtureContext(scheduler.service.jobs.get(event['job_id'])['payload'], event['job_id'])
    result = scheduler._background_capture_job(context)
    assert calls[0]['purpose'] == 'BACKGROUND_RESEARCH'
    assert calls[0]['budget'].max_requests == 32 and calls[0]['budget'].max_seconds == 120
    assert result['pending_request_count'] == 1 and result['recommendation_published'] is False
    assert scheduler.service.store.get_document('candidate_background_capture_results', context.job_id) == result


def test_background_capture_retains_pending_work_without_unbounded_batches(scheduler):
    completed_primary(scheduler, status='READY')
    scheduler.clock.current = scheduler.clock.current.replace(hour=21, minute=15)
    for batch in range(1, 4):
        event = scheduler.schedule_background_capture()[0]
        assert event['batch'] == batch
        scheduler.service.jobs.rows[event['job_id']].update(
            state='SUCCEEDED', result={'capture_queue_complete': False, 'pending_request_count': 100})
    assert scheduler.schedule_background_capture() == []
    background = [job for job in scheduler.service.jobs.rows.values()
                  if job['kind'] == 'scheduled_background_research_capture']
    assert len(background) == 3 and all(job['result']['pending_request_count'] == 100 for job in background)


def test_daily_outcomes_use_existing_service_are_deduplicated_and_yield_to_primary(scheduler):
    scheduler.service.pending_daily_recommendation_outcomes = lambda: [
        {'run_id': 'TEST_FIXTURE_run', 'observation_date': '2026-09-18'}]
    scheduler.service.strategy_input = lambda: ('market_TEST_FIXTURE', SimpleNamespace(observation_date=date(2026, 9, 21)))
    scheduler.service.submit_daily_recommendation_outcomes = lambda run_id, input_id, job_id: scheduler.service.jobs.submit(
        'daily_recommendation_outcomes', {'run_id': run_id, 'input_id': input_id}, job_id=job_id)
    scheduler.clock.current = scheduler.clock.current.replace(hour=16)
    event = scheduler.schedule_daily_recommendation_outcomes()[0]
    assert event['horizons'] == list(range(1, 21))
    assert scheduler.schedule_daily_recommendation_outcomes() == []
    scheduler.clock.current = scheduler.clock.current.replace(hour=20, minute=26)
    assert scheduler.schedule_daily_recommendation_outcomes() == []


def test_capture_failure_atomically_saves_real_isolated_job_and_attempt(tmp_path, monkeypatch):
    from tests.r05_product.test_daily_primary_reliability_v1 import _empty_core, _wait_job, _attempt
    clock = FixtureClock(datetime(2026, 9, 21, 20, 30, tzinfo=SH))
    def unavailable(*args):
        raise RuntimeError('TEST_FIXTURE_PROVIDER_SECRET')
    core = _empty_core(tmp_path / 'TEST_FIXTURE_atomic_failure', monkeypatch, now=clock)
    try:
        scheduler = CandidateScheduler(core.candidates, capture=unavailable, clock=clock)
        payload = primary_payload(scheduler)
        job = core.jobs.submit('scheduled_candidate_capture', payload, job_id=payload['root_job_id'])
        done = _wait_job(core, job['job_id'])
        attempt = _attempt(core, job['job_id'])
        assert done['state'] == 'FAILED'
        assert attempt['final_status'] == 'FAILED' and attempt['stage'] == 'CAPTURE'
        assert attempt['recommendation_published'] is False
        assert attempt['strategy_started'] is False
        assert 'TEST_FIXTURE_PROVIDER_SECRET' not in str(attempt) + str(done)
        assert core.candidates._all('stage_aware_prospective_primary_runs') == []
        assert core.candidates._all('strategy_experiment_runs') == []
    finally:
        core.close()
