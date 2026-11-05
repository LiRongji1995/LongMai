"""Daily user entry acceptance; temporary TEST_FIXTURE state only."""
from datetime import datetime, timedelta
import hashlib
import json

import pytest

from tests.yaogu.fixtures import make_bundle
from tests.r05_product.test_daily_primary_reliability_v1 import (
    TZ, _empty_core, _core, _run_daily_primary,
)
from tests.r05_product.test_daily_research_service_outcomes import fingerprint, isolated_consumer


def test_latest_without_registered_input_records_not_ready_and_requested_identity(tmp_path, monkeypatch):
    now = datetime(2026, 9, 23, 21, tzinfo=TZ)
    # Job publication and domain clocks share the explicit historical fixture.
    # A real present-day finish timestamp must remain invisible to this report.
    monkeypatch.setattr("stockfish_core.jobs.utc_now", lambda: now)
    core = _empty_core(tmp_path, monkeypatch, now=now)
    try:
        _, job, attempt, _, _ = _run_daily_primary(core)
        assert job['state'] == 'SUCCEEDED'
        assert attempt['execution_status'] == 'NOT_READY'
        assert attempt['run_id'] is None
        assert attempt['missing_evidence']
        assert attempt['strategy_version'] == job['payload']['research_identity']['signal_implementation_version']
        assert attempt['config_version'] == job['payload']['research_identity']['signal_config_hash']
        assert attempt['target_session'] == '2026-09-24'
        # Durable job completion precedes the worker's final trace append.
        # Wait for that same worker, so the full fingerprint isolates the GET.
        with core.jobs._lock:
            worker = core.jobs._futures.get(job['job_id'])
        if worker is not None:
            worker.result(timeout=5)
        before = fingerprint(core)
        result = core.candidates.daily_recommendation_report(target_session='2026-09-24')
        assert result['delivery']['execution_status'] == 'NOT_READY'
        assert result['snapshot'] is None
        assert '这不是合法空名单' in result['markdown']
        assert 'MARKET_INPUT_MISSING' in result['markdown']
        assert fingerprint(core) == before
    finally:
        core.close()


def test_same_primary_input_identity_reuses_success_without_second_signal(tmp_path, monkeypatch):
    bundle, _ = make_bundle(1, session_count=260)
    core, input_id, _ = _core(tmp_path, monkeypatch, bundle)
    try:
        first, job, attempt, _, _ = _run_daily_primary(core, input_id=input_id)
        assert attempt['final_status'] == 'READY'
        second = core.candidates.submit_daily_primary_strategy(input_id=input_id)
        assert second['job_id'] == first['job_id']
        assert len(core.jobs.list(kind='daily_primary_strategy')) == 1
        assert len(core.store.list_documents('daily_research_snapshots')) == 1
        snapshot = core.candidates.daily_recommendation_snapshot(job['result']['run_id'])
        assert sum(snapshot['counts'].values()) == len(snapshot['items'])
    finally:
        core.close()


def test_persisted_missed_day_and_calendar_gap_remain_distinct_and_read_only(tmp_path, monkeypatch):
    now = datetime(2026, 9, 23, 9, tzinfo=TZ)
    core = _empty_core(tmp_path, monkeypatch, now=now)
    try:
        core.store.put_document('candidate_schedule_events', 'TEST_FIXTURE_MISSED', {
            'event_id': 'TEST_FIXTURE_MISSED', 'market_session': 'EVENING',
            'scheduled_at': '2026-09-22T20:30:00+08:00',
            'observed_at': '2026-09-23T08:50:00+08:00',
            'status': 'MISSED_SERVICE_OFFLINE', 'reason': 'TEST_FIXTURE_SERVICE_OFFLINE',
        })
        before = fingerprint(core)
        service = core.candidates
        result = service.daily_recommendation_report(target_session='2026-09-23')
        assert result['delivery']['execution_status'] == 'MISSED'
        assert result['delivery']['reason'] == 'TEST_FIXTURE_SERVICE_OFFLINE'
        assert result['snapshot'] is None
        assert service.daily_research_delivery('2027-01-04')['execution_status'] == 'NOT_READY'
        assert service.daily_research_delivery('2027-01-04')['reason'] == 'VERIFIED_TARGET_CALENDAR_UNAVAILABLE'
        assert fingerprint(core) == before
    finally:
        core.close()


def test_saved_run_asof_obeys_known_commit_time_beyond_generation(tmp_path, monkeypatch):
    core, _, snapshot, _, clock = isolated_consumer(tmp_path, monkeypatch)
    run_id = snapshot['identity']['run_id']
    job_id = 'TEST_FIXTURE_LATER_PUBLICATION'
    core.store.put_document('daily_primary_attempts', 'TEST_FIXTURE_LATER_ATTEMPT', {
        'run_id': run_id, 'job_id': job_id, 'final_status': 'READY',
    })
    original_get = core.jobs.get
    publication = clock.current + timedelta(minutes=1)
    monkeypatch.setattr(core.jobs, 'get', lambda value: {'finished_at': publication.isoformat(), 'state': 'SUCCEEDED'}
                        if value == job_id else original_get(value))
    try:
        for method in (lambda: core.candidates.daily_recommendation_report(run_id=run_id),
                       lambda: core.candidates.daily_recommendation_outcomes(run_id)):
            with pytest.raises(ValueError, match='not published'):
                method()
        summary = core.candidates.daily_recommendation_analysis()
        assert summary['groups'] == []
        assert summary['snapshot_availability']['excluded_not_yet_available'] == 1
        clock.current = publication
        assert core.candidates.daily_recommendation_report(run_id=run_id)['snapshot'] == snapshot
        assert core.candidates.daily_recommendation_analysis()['snapshot_availability']['included_snapshots'] == 1
    finally:
        core.close()
