"""TEST_FIXTURE scheduler progress; no providers or business database."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
import threading

import pytest

from stockfish_core.scheduler_health import SchedulerHealth
from stockfish_core.service import ApplicationService
from stockfish_core.yaogu.scheduler import CandidateScheduler

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


def fixture_scheduler():
    elapsed = [0.]
    scheduler = object.__new__(CandidateScheduler)
    scheduler.clock = lambda: NOW
    scheduler.interval = .01
    scheduler._stop = threading.Event()
    scheduler._tick_lock = threading.Lock()
    scheduler._progress_health = SchedulerHealth(20, monotonic=lambda: elapsed[0])
    scheduler.thread = SimpleNamespace(is_alive=lambda: True, name='TEST_FIXTURE', ident=1)
    scheduler._tick = lambda now=None: ['TEST_FIXTURE']
    return scheduler, elapsed


def test_alive_thread_requires_success_and_stale_success_is_not_ready():
    scheduler, elapsed = fixture_scheduler()
    assert scheduler.health_snapshot()['reason_codes'] == ['SCHEDULER_NO_SUCCESSFUL_TICK']
    assert scheduler.tick() == ['TEST_FIXTURE']
    assert scheduler.health_snapshot()['status'] == 'ok'
    elapsed[0] = 61
    assert scheduler.health_snapshot()['reason_codes'] == ['SCHEDULER_PROGRESS_STALE']
    scheduler.tick()
    assert scheduler.health_snapshot()['status'] == 'ok'


def test_failure_preserves_last_success_redacts_secret_and_recovers():
    scheduler, _ = fixture_scheduler()
    scheduler.tick()
    original = scheduler.health_snapshot()['last_successful_tick_at']
    def fail(now=None):
        raise RuntimeError('TEST_FIXTURE_SECRET')
    scheduler._tick = fail
    for _ in range(2):
        with pytest.raises(RuntimeError):
            scheduler.tick()
    value = scheduler.health_snapshot()
    assert value['last_successful_tick_at'] == original
    assert value['consecutive_failures'] == 2
    assert value['last_error_type'] == 'RuntimeError'
    assert 'TEST_FIXTURE_SECRET' not in str(value)
    assert value['status'] == 'not_ready'
    scheduler._tick = lambda now=None: []
    scheduler.tick()
    assert scheduler.health_snapshot()['status'] == 'ok'


def test_database_diagnostic_failure_cannot_hide_scheduler_failure():
    scheduler, _ = fixture_scheduler()
    scheduler.thread = None
    def fail(now=None):
        raise RuntimeError('TEST_FIXTURE_TICK_SECRET')
    def event(*args):
        scheduler._stop.set()
        raise OSError('TEST_FIXTURE_DB_SECRET')
    scheduler._tick, scheduler._event = fail, event
    scheduler.start()
    scheduler.thread.join(timeout=2)
    assert not scheduler.thread.is_alive()
    result = scheduler.health_snapshot()
    assert result['diagnostic_write_failed'] is True
    assert result['consecutive_failures'] == 1
    assert 'SCHEDULER_DIAGNOSTIC_WRITE_FAILED' in result['reason_codes']
    assert 'SECRET' not in str(result)


def test_readiness_uses_progress_without_deep_scan_or_market_claim():
    scheduler, elapsed = fixture_scheduler()
    core = object.__new__(ApplicationService)
    core.store = SimpleNamespace(path=Path('TEST_FIXTURE.sqlite3'), readiness=lambda: {'status':'healthy'})
    core.jobs = SimpleNamespace(health=lambda **_: {'status':'healthy','accepting_work':True})
    core.candidates = SimpleNamespace(root=Path('TEST_FIXTURE_state'))
    core.candidate_scheduler = scheduler
    assert core.readiness()['ready'] is False
    scheduler.tick()
    assert core.readiness()['ready'] is True
    assert core.readiness()['checks']['scheduler']['market_readiness'] == 'NOT_EVALUATED'
    elapsed[0] = 61
    assert core.readiness()['ready'] is False
    assert 'SCHEDULER_PROGRESS_STALE' in core.readiness()['reason_codes']


@pytest.mark.parametrize('interval', [0, -1, float('inf'), float('nan'), True])
def test_invalid_intervals_are_rejected(interval):
    with pytest.raises(ValueError):
        SchedulerHealth(interval)


def describe_fixture():
    """Saved scheduling metadata only; all storage calls stay in memory."""
    scheduler, elapsed = fixture_scheduler()
    scheduler.started_at = NOW
    scheduler.thread.native_id = 1
    state = {'run': None, 'event': None}
    scheduler.plan = lambda: {'plan_id': 'TEST_FIXTURE_PLAN'}
    scheduler.ensure_primary_validation_schedule = lambda now: {
        'job_id': 'TEST_FIXTURE_JOB', 'signal_session': '2026-10-15',
        'scheduled_at': '2026-10-15T20:30:00+08:00',
        'validation_epoch_id': 'TEST_FIXTURE_EPOCH'}
    scheduler._primary_jobs = lambda day: (None, [])
    scheduler._latest_stage_aware_primary = lambda day: state['run']
    scheduler.service = SimpleNamespace(
        root=Path('TEST_FIXTURE_state'),
        store=SimpleNamespace(path=Path('TEST_FIXTURE.sqlite3'), get_document=lambda *args: state['event']),
        jobs=SimpleNamespace(get=lambda *args: None, owner_id='TEST_FIXTURE_OWNER'),
        validation_state=lambda epoch: {'epoch': {}, 'validation_epoch_id': 'TEST_FIXTURE_EPOCH'},
        _all=lambda namespace: [], current_config=lambda: SimpleNamespace(strategy_id='TEST_FIXTURE'))
    return scheduler, elapsed, state


@pytest.mark.parametrize('failure,reason', [
    ('never', 'SCHEDULER_NO_SUCCESSFUL_TICK'),
    ('stale', 'SCHEDULER_PROGRESS_STALE'),
    ('failed', 'SCHEDULER_TICK_FAILED'),
])
def test_describe_state_cannot_advertise_ready_while_progress_is_unhealthy(monkeypatch, failure, reason):
    scheduler, elapsed, _ = describe_fixture()
    if failure != 'never':
        scheduler.tick()
    if failure == 'stale':
        elapsed[0] = 61
    elif failure == 'failed':
        scheduler._progress_health.failure(NOW, RuntimeError('TEST_FIXTURE_SECRET'))
    original = scheduler.health_snapshot
    calls = []
    def snapshot():
        calls.append('TEST_FIXTURE')
        return original()
    monkeypatch.setattr(scheduler, 'health_snapshot', snapshot)
    result = scheduler.describe_state()
    assert calls == ['TEST_FIXTURE']
    assert result['status'] == 'SCHEDULER_NOT_READY'
    assert reason in result['readiness_blockers']
    assert result['scheduler_progress']['status'] == 'not_ready'
    assert 'SECRET' not in str(result)


@pytest.mark.parametrize('historical,expected', [
    ('run', 'PROSPECTIVE_PRIMARY_PERSISTED'),
    ('missed', 'MISSED_SERVICE_OFFLINE_NOT_BACKFILLED'),
])
def test_describe_keeps_historical_outcome_and_current_failure_separate(historical, expected):
    scheduler, _, state = describe_fixture()
    scheduler.tick()
    scheduler._progress_health.failure(NOW, RuntimeError('TEST_FIXTURE_SECRET'))
    if historical == 'run':
        state['run'] = {'run_id': 'TEST_FIXTURE_SAVED'}
    else:
        state['event'] = {'status': 'MISSED_SERVICE_OFFLINE'}
    result = scheduler.describe_state()
    assert result['status'] == expected
    assert 'SCHEDULER_TICK_FAILED' in result['readiness_blockers']
    assert result['scheduler_progress']['status'] == 'not_ready'


@pytest.mark.parametrize('missing', [True, False])
def test_describe_psutil_failure_is_partial_identity_not_false_scheduler_failure(monkeypatch, missing):
    scheduler, _, _ = describe_fixture()
    scheduler.tick()
    def denied():
        raise PermissionError('TEST_FIXTURE_PRIVATE_PATH')
    monkeypatch.setitem(sys.modules, 'psutil', None if missing else SimpleNamespace(Process=denied))
    result = scheduler.describe_state()
    assert result['status'] == 'READY_AND_RUNNING_WAITING_FOR_CUTOFF'
    assert result['readiness_blockers'] == []
    assert result['scheduler_progress']['status'] == 'ok'
    identity = result['service_identity']
    assert identity['process_creation_time'] is None
    assert identity['process_identity_status'] == 'PARTIAL'
    assert identity['process_identity_error_type'] == ('ModuleNotFoundError' if missing else 'PermissionError')
    assert 'PRIVATE_PATH' not in str(result)
