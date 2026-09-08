"""An official scheduled job is bound to its captured validation identity."""
from datetime import datetime, time

from stockfish_core.yaogu.scheduler import CandidateScheduler
from stockfish_core.yaogu.validation_identity import current_validation_identity
from tests.r02_product.test_scheduled_outcomes import observer, future_input, next_session
from tests.r02_product.test_candidate_product_path import wait_job
from tests.yaogu.fixtures import TZ


def test_same_day_old_epoch_and_legacy_jobs_do_not_block_current_evening(observer):
    client, core, bundle, clock, saved, _ = observer
    day = next_session(bundle)
    clock.current = datetime.combine(day, time(20, 30), TZ)
    captured = future_input(bundle, day).model_copy(update={'coverage_type': 'PROSPECTIVE_CAPTURED'})
    scheduler = CandidateScheduler(core.candidates, capture=lambda *args: captured, clock=clock)
    prefix = 'schedule_' + str(day) + '_EVENING'
    for old_id in (prefix, prefix + '_validation_epoch_OLD'):
        core.store.put_document('candidate_schedule_events', old_id, {
            'event_id': old_id, 'job_id': old_id, 'validation_epoch_id': 'validation_epoch_OLD',
            'market_session': 'EVENING', 'scheduled_at': clock.current.isoformat(), 'status': 'SUBMITTED'})
    expected = scheduler._primary_schedule_for(day)
    evening = next(event for event in scheduler.tick() if event['market_session'] == 'EVENING')
    assert evening['job_id'] == expected['job_id'] == prefix + '_' + current_validation_identity()['validation_epoch_id']
    assert evening['job_id'] not in {prefix, prefix + '_validation_epoch_OLD'}
    done = wait_job(client, evening['job_id'])
    assert done['state'] == 'SUCCEEDED', done
    run = core.candidates.strategy_experiment(done['result']['stage_aware_primary_run_id'])
    assert run['validation_epoch_id'] == evening['validation_epoch_id']
    assert run['prospective_validation']['official_validation_signal_countable'] is True
    assert not [event for event in scheduler.tick() if event.get('market_session') == 'EVENING']
    assert core.store.get_document('candidate_schedule_events', prefix)['validation_epoch_id'] == 'validation_epoch_OLD'
    primary = core.store.get_document('stage_aware_prospective_primary_runs', run['run_id'])
    assert primary['implementation_version'] == primary['signal_implementation_version']
    assert primary['spec_revision'] == primary['signal_spec_revision']


def test_queued_evening_with_changed_epoch_fails_before_capturing(observer):
    client, core, bundle, clock, saved, _ = observer
    day = next_session(bundle)
    clock.current = datetime.combine(day, time(20, 30), TZ)
    called = []
    scheduler = CandidateScheduler(core.candidates, capture=lambda *args: called.append(True), clock=clock)
    job = core.candidates.jobs.submit('scheduled_candidate_capture', {
        'market_session': 'EVENING', 'scheduled_at': clock.current.isoformat(), 'session': str(day),
        'validation_epoch_id': 'TEST_FIXTURE_OLD_EPOCH', 'config_hash': core.candidates.current_config().config_hash})
    done = wait_job(client, job['job_id'])
    assert done['state'] == 'FAILED', done
    assert called == []
    assert core.candidates._all('stage_aware_prospective_primary_runs') == []


def test_epoch_change_during_capture_is_failed_operational_day_not_new_signal(observer, monkeypatch):
    import stockfish_core.yaogu.validation_identity as identity_module

    client, core, bundle, clock, saved, _ = observer
    day = next_session(bundle)
    clock.current = datetime.combine(day, time(20, 30), TZ)
    captured = future_input(bundle, day).model_copy(update={'coverage_type': 'PROSPECTIVE_CAPTURED'})
    initial = current_validation_identity()
    changed = []
    original = identity_module.current_validation_identity

    def current():
        return {**original(), 'validation_epoch_id': 'TEST_FIXTURE_CHANGED_EPOCH'} if changed else original()

    def capture(*args):
        changed.append(True)
        return captured

    monkeypatch.setattr(identity_module, 'current_validation_identity', current)
    scheduler = CandidateScheduler(core.candidates, capture=capture, clock=clock)
    evening = next(event for event in scheduler.tick() if event['market_session'] == 'EVENING')
    done = wait_job(client, evening['job_id'])
    assert done['state'] == 'FAILED', done
    assert core.candidates._all('stage_aware_prospective_primary_runs') == []
    state = core.candidates.validation_state(initial['validation_epoch_id'])
    assert state['primary_capture_attempted_days'] == 1
    assert state['failed_primary_capture_days'] == 1
    assert state['data_unavailable_primary_days'] == 1
    assert state['evaluable_official_signal_days'] == 0


def test_auction_primary_lookup_cannot_choose_later_old_epoch_record(observer):
    client, core, bundle, clock, saved, _ = observer
    day = next_session(bundle)
    scheduler = CandidateScheduler(core.candidates, clock=clock)
    current_epoch = current_validation_identity()['validation_epoch_id']
    for run_id, epoch, cutoff in [('TEST_FIXTURE_CURRENT', current_epoch, '20:30'), ('TEST_FIXTURE_OLD', 'OLD_EPOCH', '20:38')]:
        core.store.put_document('stage_aware_prospective_primary_runs', run_id, {
            'run_id': run_id, 'signal_observation_date': str(day), 'validation_epoch_id': epoch,
            'decision_cutoff': f'{day}T{cutoff}:00+08:00', 'official_primary_signal_countable': True})
    assert scheduler._latest_stage_aware_primary(day)['run_id'] == 'TEST_FIXTURE_CURRENT'
