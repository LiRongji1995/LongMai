"""Read-only delivery and historical availability, in-memory TEST_FIXTURE only."""

import pytest


from tests.support.daily_delivery import (
    FixtureDelivery, FixtureJobs, add_attempt, add_job, add_ready, identity, stamp,
)


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
