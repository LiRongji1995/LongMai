"""Read-only delivery and historical availability, in-memory TEST_FIXTURE only."""
from copy import deepcopy
from datetime import date

import pytest

from stockfish_core.yaogu.daily_research_service import DailyResearchMixin


def stamp(clock, day='2026-09-21'):
    return f'{day}T{clock}:00+08:00'


class FixtureJobs:
    def __init__(self):
        self.rows = {}

    def get(self, key):
        return deepcopy(self.rows.get(key))

    def list(self, *, kind, limit, offset):
        return deepcopy([j for j in self.rows.values() if j['kind'] == kind][offset:offset + limit])

    def submit(self, *args, **kwargs):
        pytest.fail('delivery GET must not submit work')


class FixtureDelivery(DailyResearchMixin):
    def __init__(self):
        self.docs, self.snapshots = {}, {}
        self.jobs = FixtureJobs()

    def _all(self, namespace):
        return deepcopy(self.docs.get(namespace, []))

    def _daily_research_sessions(self):
        return [date(2026, 9, 21), date(2026, 9, 22)]

    def daily_recommendation_snapshot(self, run_id):
        return deepcopy(self.snapshots[run_id])

    def query(self, clock='20:40', day='2026-09-21'):
        original = deepcopy((self.docs, self.jobs.rows, self.snapshots))
        result = self.daily_research_delivery('2026-09-22', as_of=stamp(clock, day))
        assert (self.docs, self.jobs.rows, self.snapshots) == original
        assert result['read_only'] is True
        return result


def identity():
    return {'signal_implementation_version': 'TEST_FIXTURE_V2',
            'signal_config_hash': 'TEST_FIXTURE_CONFIG', 'validation_epoch_id': 'TEST_FIXTURE_EPOCH'}


def add_job(probe, key, *, state='SUCCEEDED', created='20:30', started='20:30', finished='20:32',
            mode='SCHEDULED_EVENING'):
    payload = {'research_identity': identity()}
    if mode == 'SCHEDULED_EVENING':
        kind = 'scheduled_candidate_capture'
        payload.update(session='2026-09-21', market_session='EVENING', root_job_id='TEST_FIXTURE_primary')
    else:
        kind = 'daily_primary_strategy'
        payload.update(expected_observation_session='2026-09-21', daily_primary_mode=mode)
    job = {'job_id': key, 'kind': kind, 'payload': payload, 'state': state,
           'created_at': stamp(created), 'started_at': stamp(started) if started else None,
           'finished_at': stamp(finished) if finished else None,
           'updated_at': stamp(finished or started or created), 'error': {'code': 'TEST_FIXTURE_ERROR'}}
    probe.jobs.rows[key] = job
    return job


def add_attempt(probe, key, status, *, clock='20:31', run_id=None, mode='SCHEDULED_EVENING'):
    row = {'attempt_id': 'TEST_FIXTURE_attempt_' + key, 'job_id': key, 'final_status': status,
           'timestamp': stamp(clock), 'actual_decision_cutoff': stamp('20:30'),
           'expected_observation_session': '2026-09-21', 'daily_primary_mode': mode,
           'run_id': run_id, 'stage': 'READINESS' if status == 'NOT_READY' else 'STRATEGY',
           'error': {'code': 'TEST_FIXTURE_STRATEGY_FAILURE'} if status == 'FAILED' else None,
           'blocking_requirements': ['TEST_FIXTURE_MISSING_PRICE'] if status == 'NOT_READY' else [],
           'missing_evidence': ['TEST_FIXTURE_MISSING_PRICE'] if status == 'NOT_READY' else [],
           'strategy_version': 'TEST_FIXTURE_V2', 'config_version': 'TEST_FIXTURE_CONFIG'}
    probe.docs.setdefault('daily_primary_attempts', []).append(row)
    return row


def add_ready(probe, key='TEST_FIXTURE_primary', *, finished='20:34', generated='20:31'):
    run_id = 'TEST_FIXTURE_run_' + key
    add_job(probe, key, finished=finished)
    add_attempt(probe, key, 'READY', run_id=run_id)
    probe.docs.setdefault('stage_aware_prospective_primary_runs', []).append({
        'run_id': run_id, 'target_session': '2026-09-22', 'scheduler_job_id': key,
        'decision_cutoff': stamp('20:30')})
    probe.snapshots[run_id] = {'identity': {'run_id': run_id, 'observation_date': '2026-09-21',
        'target_session': '2026-09-22', 'generated_at': stamp(generated), 'data_cutoff': stamp('20:30')}}
    return run_id


def test_primary_index_cannot_publish_before_commit_even_after_generation():
    probe = FixtureDelivery()
    add_ready(probe)
    early = probe.query('20:32')
    assert early['execution_status'] == 'RUNNING' and early['run_id'] is None
    assert early['attempts'] == []
    late = probe.query('20:34')
    assert late['execution_status'] == 'READY'
    assert late['published_at'] == stamp('20:34')


@pytest.mark.parametrize('status,state', [('NOT_READY', 'SUCCEEDED'), ('FAILED', 'FAILED')])
@pytest.mark.parametrize('retry_state', ['RUNNING', 'QUEUED'])
def test_live_manual_retry_has_no_event_but_replaces_previous_terminal_display(status, state, retry_state):
    probe = FixtureDelivery()
    add_job(probe, 'TEST_FIXTURE_primary', state=state)
    old = add_attempt(probe, 'TEST_FIXTURE_primary', status)
    add_job(probe, 'TEST_FIXTURE_random_retry', state=retry_state, created='20:36',
            started='20:36' if retry_state == 'RUNNING' else None, finished=None)
    result = probe.query()
    assert result['execution_status'] == retry_state and result['job_id'] == 'TEST_FIXTURE_random_retry'
    assert result['attempts'] == [old] and result['previous_attempt_status'] == status
    assert result['strategy_version'] == 'TEST_FIXTURE_V2'


def test_manual_retry_failure_without_evening_event_is_the_latest_failure():
    probe = FixtureDelivery()
    add_job(probe, 'TEST_FIXTURE_primary')
    add_attempt(probe, 'TEST_FIXTURE_primary', 'NOT_READY')
    add_job(probe, 'TEST_FIXTURE_random_retry', state='FAILED', created='20:36', started='20:36', finished='20:38')
    newest = add_attempt(probe, 'TEST_FIXTURE_random_retry', 'FAILED', clock='20:37')
    result = probe.query()
    assert result['execution_status'] == 'FAILED' and result['stage'] == newest['stage']
    assert result['error'] == newest['error'] and len(result['attempts']) == 2


def test_job_only_manual_retry_failure_keeps_old_attempt_and_new_error():
    probe = FixtureDelivery()
    add_job(probe, 'TEST_FIXTURE_primary')
    add_attempt(probe, 'TEST_FIXTURE_primary', 'NOT_READY')
    failed = add_job(probe, 'TEST_FIXTURE_random_retry', state='INTERRUPTED', created='20:36', started='20:36', finished='20:38')
    result = probe.query()
    assert result['execution_status'] == 'FAILED' and result['reason'] == 'INTERRUPTED'
    assert result['error'] == failed['error'] and len(result['attempts']) == 1


def test_published_ready_survives_a_later_failure():
    probe = FixtureDelivery()
    run_id = add_ready(probe)
    add_job(probe, 'TEST_FIXTURE_random_retry', state='FAILED', created='20:36', started='20:36', finished='20:38')
    add_attempt(probe, 'TEST_FIXTURE_random_retry', 'FAILED', clock='20:37')
    result = probe.query()
    assert result['execution_status'] == 'READY' and result['run_id'] == run_id
    assert result['latest_attempt_status'] == 'FAILED'


def test_ready_without_completion_proof_is_unknown_instead_of_published():
    probe = FixtureDelivery()
    add_ready(probe, finished=None)
    result = probe.query()
    assert result['execution_status'] == 'NOT_READY' and result['run_id'] is None
    assert result['reason'] == 'PUBLICATION_COMPLETION_TIME_UNVERIFIED'


def test_terminal_event_can_prove_publication_when_job_finished_time_is_missing():
    probe = FixtureDelivery()
    add_ready(probe, finished=None)
    probe.docs['candidate_schedule_events'] = [{'event_id': 'TEST_FIXTURE_primary_terminal',
        'job_id': 'TEST_FIXTURE_primary', 'market_session': 'DAILY_PRIMARY_TERMINAL',
        'signal_session': '2026-09-21', 'status': 'SUCCEEDED', 'observed_at': stamp('20:35')}]
    assert probe.query('20:34')['execution_status'] == 'NOT_READY'
    assert probe.query('20:35')['execution_status'] == 'READY'


def test_unplanned_history_is_not_silently_counted_as_missed():
    result = FixtureDelivery().query('09:00', '2026-09-22')
    assert result['execution_status'] == 'NOT_READY'
    assert result['reason'] == 'NO_REGISTERED_DAILY_PRIMARY_PLAN'


def test_registered_plan_deadline_and_explicit_missed_reason_are_read_only():
    probe = FixtureDelivery()
    probe.docs['stage_aware_prospective_validation_schedules'] = [{
        'signal_session': '2026-09-21', 'registered_at': stamp('19:00'), **identity()}]
    assert probe.query()['execution_status'] == 'PLANNED'
    result = probe.query('08:45', '2026-09-22')
    assert result['execution_status'] == 'MISSED' and result['strategy_version'] == 'TEST_FIXTURE_V2'
    probe.docs['candidate_schedule_events'] = [{'event_id': 'TEST_FIXTURE_missed',
        'market_session': 'EVENING', 'signal_session': '2026-09-21', 'status': 'MISSED_SERVICE_OFFLINE',
        'reason': 'TEST_FIXTURE_OFFLINE_REASON', 'observed_at': stamp('21:00'), **identity()}]
    assert probe.query('21:01')['reason'] == 'TEST_FIXTURE_OFFLINE_REASON'


def test_normal_latest_attempts_are_daily_but_explicit_saved_input_is_not():
    probe = FixtureDelivery()
    add_job(probe, 'TEST_FIXTURE_explicit', state='FAILED', mode='SPECIFIC_INPUT')
    add_attempt(probe, 'TEST_FIXTURE_explicit', 'FAILED', mode='SPECIFIC_INPUT')
    assert probe.query()['attempts'] == []
    add_job(probe, 'TEST_FIXTURE_latest', mode='NORMAL_LATEST')
    add_attempt(probe, 'TEST_FIXTURE_latest', 'NOT_READY', mode='NORMAL_LATEST')
    result = probe.query()
    assert result['execution_status'] == 'NOT_READY'
    assert result['missing_evidence'] == ['TEST_FIXTURE_MISSING_PRICE']


def test_future_retry_and_terminal_error_do_not_leak_into_earlier_report():
    probe = FixtureDelivery()
    add_job(probe, 'TEST_FIXTURE_primary')
    old = add_attempt(probe, 'TEST_FIXTURE_primary', 'NOT_READY')
    add_job(probe, 'TEST_FIXTURE_random_retry', state='FAILED', created='20:36', started='20:36', finished='20:38')
    add_attempt(probe, 'TEST_FIXTURE_random_retry', 'FAILED', clock='20:37')
    result = probe.query('20:34')
    assert result['execution_status'] == 'NOT_READY' and result['attempts'] == [old]
    assert result['error'] is None
