"""Daily observation acceptance: in-memory / temporary SQLite TEST_FIXTURE only.

No live service, market download, recommendation execution or production files.
Synthetic saved snapshots exercise the consumer contract, not strategy quality.
"""
from copy import deepcopy
from datetime import date, datetime, time, timedelta
import json

import pytest

from stockfish_core.api import create_app
from stockfish_core.observed_data import exchange_calendar_2026
from stockfish_core.yaogu.daily_observation import observation_report
from stockfish_core.yaogu.daily_research_outcomes import evaluate_daily_research_outcomes
from tests.r05_product.test_daily_primary_reliability_v1 import TZ, _MutableClock, _core
from tests.support.daily_delivery import (
    FixtureDelivery, FixtureJobs, add_attempt, add_job, add_ready, identity, stamp,
)
from tests.r05_product.test_daily_research_outcomes import case, edit_bar, security_interval
from tests.r05_product.test_daily_research_service_outcomes import fingerprint, store_fixture_snapshot


class EmptyFixtureStore:
    def get_document(self, namespace, document_id):
        return None


class ObservationDelivery(FixtureDelivery):
    """Real delivery state machine with explicit in-memory TEST_FIXTURE records."""
    def __init__(self):
        super().__init__()
        self.store = EmptyFixtureStore()
        self.now = datetime(2026, 9, 22, 9, tzinfo=TZ)
        self.sessions = super()._daily_research_sessions()

    def _daily_research_now(self):
        return self.now

    def _daily_research_sessions(self):
        return self.sessions


@pytest.fixture
def consumer(tmp_path, monkeypatch):
    """Create isolated saved publications; do not invoke strategy or job workers."""
    cores = []

    def create(*, decisions=('FOCUS', 'WATCH', 'UNKNOWN', 'REJECTED'), register=True):
        snapshot, bundle, cutoff = case(len(decisions))
        for index, (item, decision) in enumerate(zip(snapshot['items'], decisions)):
            item.update(decision_status=decision, primary_setup=('START', 'ACCELERATION',
                        'HIGH_LEVEL_REPAIR', 'TREND_PULLBACK')[index % 4],
                        reason_summary=f'TEST_FIXTURE 原始解释 {index} | 不重算',
                        reason_codes=[f'TEST_FIXTURE_REASON_{index}'])
        clock = _MutableClock(cutoff)
        core, _, _ = _core(tmp_path / f'TEST_FIXTURE_{len(cores)}', monkeypatch, bundle, now=clock)
        cores.append(core)
        monkeypatch.setattr(core.candidates, '_daily_research_now', clock)
        monkeypatch.setattr(core.candidates, '_daily_research_sessions', lambda: list(bundle.sessions))
        snapshot = store_fixture_snapshot(core, snapshot)
        run_id = snapshot['identity']['run_id']
        job_id = 'TEST_FIXTURE_PUBLISHED_PRIMARY'
        generated = datetime.fromisoformat(snapshot['identity']['generated_at'])
        jobs = FixtureJobs()
        jobs.rows[job_id] = {'job_id': job_id, 'kind': 'scheduled_candidate_capture',
            'state': 'SUCCEEDED', 'created_at': (generated - timedelta(minutes=1)).isoformat(),
            'started_at': (generated - timedelta(minutes=1)).isoformat(),
            'finished_at': generated.isoformat(), 'updated_at': generated.isoformat(),
            'payload': {'market_session': 'EVENING',
                'session': snapshot['identity']['observation_date']}}
        monkeypatch.setattr(core.candidates, 'jobs', jobs)
        core.store.put_document('daily_primary_attempts', 'TEST_FIXTURE_READY_ATTEMPT', {
            'attempt_id': 'TEST_FIXTURE_READY_ATTEMPT', 'job_id': job_id, 'final_status': 'READY',
            'timestamp': generated.isoformat(), 'run_id': run_id,
            'expected_observation_session': snapshot['identity']['observation_date'],
            'daily_primary_mode': 'SCHEDULED_EVENING'})
        core.store.put_document('stage_aware_prospective_primary_runs', run_id, {
            'run_id': run_id, 'scheduler_job_id': job_id,
            'target_session': snapshot['identity']['target_session'],
            'decision_cutoff': snapshot['identity']['data_cutoff']})
        if register:
            core.store.put_document('daily_research_outcome_pending', run_id, {
                'run_id': run_id, 'snapshot_hash': snapshot['snapshot_hash'],
                'created_at': snapshot['identity']['generated_at'],
                'instrument_ids': [r['instrument_id'] for r in snapshot['items']
                                   if r['decision_status'] in {'FOCUS', 'WATCH'}],
                'horizons': list(range(1, 21))})
        return core, snapshot, bundle, clock

    yield create
    for core in cores:
        core.close()


def saved_observation(core, snapshot, **kwargs):
    before = fingerprint(core)
    report = observation_report(core.candidates, target_session=snapshot['identity']['target_session'],
                                days=1, **kwargs)
    assert fingerprint(core) == before
    return report


def save_outcomes(core, rows):
    for row in rows:
        core.store.put_document('daily_research_outcomes', row['observation_id'], row)


def test_ready_saved_snapshot_preserves_identity_decisions_and_original_explanations(consumer):
    core, snapshot, _, _ = consumer()
    report = saved_observation(core, snapshot)
    current = report['current']
    assert current['status'] == current['execution_status'] == 'READY'
    assert current['daily_primary_activity'] == 'ATTEMPT_RECORDED'
    for key in ('run_id', 'strategy_version', 'config_version', 'data_cutoff', 'target_session'):
        assert current[key] == snapshot['identity'][key]
    assert current['no_run_reason'] is None
    assert current['snapshot_status'] == 'SAVED'
    assert current['snapshot_hash'] == snapshot['snapshot_hash']
    assert current['counts'] == {'FOCUS': 1, 'WATCH': 1, 'UNKNOWN': 1, 'REJECTED': 1}
    for decision, index in [('FOCUS', 0), ('WATCH', 1)]:
        assert current['recommendations'][decision] == [{key: snapshot['items'][index][key]
            for key in ('instrument_id', 'primary_setup', 'reason_summary')}]
    assert sorted(current['non_recommendation_reasons'], key=lambda row: row['decision_status']) == [
        {'decision_status': decision, 'reason_summary': snapshot['items'][index]['reason_summary'], 'count': 1}
        for decision, index in [('REJECTED', 3), ('UNKNOWN', 2)]]
    assert current['outcome']['eligible_recommendations'] == 2
    assert current['outcome']['registered_recommendations'] == 2
    assert current['outcome']['enrollment_status'] == 'REGISTERED'
    assert 'TEST_FIXTURE 原始解释 0 \\| 不重算' in report['markdown']
    assert 'return_distribution' not in json.dumps(report)
    assert 'mfe_distribution' not in json.dumps(report)


@pytest.mark.parametrize('decisions', [(), ('UNKNOWN', 'REJECTED')])
def test_ready_legal_empty_is_normal_and_has_no_phantom_tracking(consumer, decisions):
    core, snapshot, _, _ = consumer(decisions=decisions)
    current = saved_observation(core, snapshot)['current']
    assert current['status'] == 'READY' and current['snapshot_status'] == 'SAVED'
    assert current['counts']['FOCUS'] == current['counts']['WATCH'] == 0
    assert current['recommendations'] == {'FOCUS': [], 'WATCH': []}
    assert '正常交付' in current['message']
    assert current['outcome']['status'] == 'NO_TRACKED_RECOMMENDATIONS'
    assert current['outcome']['registered_recommendations'] == 0
    assert current['outcome']['totals']['pending'] == 0


@pytest.mark.parametrize('status,state', [('NOT_READY', 'SUCCEEDED'), ('FAILED', 'FAILED')])
def test_non_ready_attempt_exposes_blocker_or_failure_without_false_empty(status, state):
    probe = ObservationDelivery()
    add_job(probe, 'TEST_FIXTURE_primary', state=state)
    attempt = add_attempt(probe, 'TEST_FIXTURE_primary', status)
    before = deepcopy((probe.docs, probe.jobs.rows))
    current = observation_report(probe, target_session='2026-09-22', days=1)['current']
    assert current['status'] == status and current['run_id'] is None
    assert current['daily_primary_activity'] == 'ATTEMPT_RECORDED'
    assert current['no_run_reason'] == status
    assert current['snapshot_status'] == 'NOT_AVAILABLE'
    assert all(count is None for count in current['counts'].values())
    if status == 'NOT_READY':
        assert current['blockers'] == ['TEST_FIXTURE_MISSING_PRICE']
        assert current['missing_evidence'] == attempt['missing_evidence']
    else:
        assert current['error'] == {'code': 'TEST_FIXTURE_STRATEGY_FAILURE'}
        assert current['stage'] == 'STRATEGY'
    assert (probe.docs, probe.jobs.rows) == before


def test_unrecorded_history_is_not_available_while_registered_overdue_is_missed():
    probe = ObservationDelivery()
    probe.sessions = [date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22)]
    probe.docs['stage_aware_prospective_validation_schedules'] = [{
        'signal_session': '2026-09-21', 'registered_at': stamp('19:00'), **identity()}]
    report = observation_report(probe, target_session='2026-09-22', days=2)
    current, previous = report['history']
    assert current['status'] == 'MISSED'
    assert current['reason'] == 'REGISTERED_PRIMARY_DEADLINE_PASSED_WITHOUT_ATTEMPT'
    assert previous['status'] == 'NOT_AVAILABLE'
    assert previous['reason'] == 'NO_REGISTERED_DAILY_PRIMARY_PLAN'
    assert previous['counts']['FOCUS'] is None
    assert report['current']['daily_primary_activity'] == 'NOT_STARTED'


def test_planned_running_and_retry_remain_distinct_at_historical_cutoff():
    probe = ObservationDelivery()
    probe.docs['stage_aware_prospective_validation_schedules'] = [{
        'signal_session': '2026-09-21', 'registered_at': stamp('19:00'), **identity()}]
    planned = observation_report(probe, target_session='2026-09-22', days=1, as_of=stamp('20:00'))
    assert planned['current']['status'] == 'PLANNED'
    assert planned['current']['daily_primary_activity'] == 'NOT_STARTED'
    add_job(probe, 'TEST_FIXTURE_old')
    add_attempt(probe, 'TEST_FIXTURE_old', 'NOT_READY')
    add_job(probe, 'TEST_FIXTURE_retry', state='RUNNING', created='20:36', started='20:36', finished=None)
    old = observation_report(probe, target_session='2026-09-22', days=1, as_of=stamp('20:34'))
    assert old['current']['status'] == 'NOT_READY'
    retry = observation_report(probe, target_session='2026-09-22', days=1, as_of=stamp('20:37'))
    assert retry['current']['status'] == retry['current']['daily_primary_activity'] == 'RUNNING'
    assert retry['current']['previous_attempt_status'] == 'NOT_READY'
    assert retry['current']['previous_blockers'] == ['TEST_FIXTURE_MISSING_PRICE']


def test_existing_legacy_projection_is_not_reported_as_a_persisted_snapshot():
    probe = ObservationDelivery()
    run_id = add_ready(probe)
    probe.snapshots[run_id]['items'] = [{'instrument_id': 'TEST000', 'decision_status': 'FOCUS',
        'primary_setup': 'START', 'reason_summary': 'TEST_FIXTURE_LEGACY_PROJECTION'}]
    current = observation_report(probe, target_session='2026-09-22', days=1)['current']
    assert current['status'] == 'READY' and current['run_id'] == run_id
    assert current['snapshot_status'] == 'NOT_AVAILABLE'
    assert current['counts']['FOCUS'] is None
    assert current['recommendations']['FOCUS'] == []
    assert current['outcome']['status'] == 'NOT_AVAILABLE'
    assert '不能视为合法空结果' in current['message']


def test_twenty_history_rows_use_verified_calendar_excluding_weekends_and_exchange_holidays():
    probe = ObservationDelivery()
    probe.now = datetime(2026, 10, 10, 9, tzinfo=TZ)
    probe.sessions = sorted(exchange_calendar_2026().sessions)
    report = observation_report(probe, target_session='2026-10-09')
    expected = sorted([day for day in probe.sessions if day <= date(2026, 10, 9)], reverse=True)[:20]
    assert [row['target_session'] for row in report['history']] == list(map(str, expected))
    assert report['available_days'] == 20 and report['calendar_status'] == 'AVAILABLE'
    assert all(day.weekday() < 5 for day in expected)
    assert not any(date(2026, 10, 1) <= day <= date(2026, 10, 7) for day in expected)
    assert date(2026, 9, 25) not in expected
    assert all(row['status'] == 'NOT_AVAILABLE' for row in report['history'])
    assert observation_report(probe)['target_session'] == '2026-10-09'


def test_calendar_unknown_or_explicit_closed_day_never_fabricates_twenty_dates():
    probe = ObservationDelivery()
    probe.now = datetime(2027, 1, 4, 9, tzinfo=TZ)
    probe.sessions = sorted(exchange_calendar_2026().sessions)
    for target in ('2027-01-04', '2026-10-01'):
        report = observation_report(probe, target_session=target)
        assert report['target_session'] == target
        assert report['calendar_status'] == 'NOT_AVAILABLE'
        assert report['history'] == [] and report['available_days'] == 0
        assert report['current']['status'] == 'NOT_AVAILABLE'
        assert report['current']['reason'] == 'VERIFIED_TARGET_CALENDAR_UNAVAILABLE'


@pytest.mark.parametrize('days', [0, 21, -1, True, '20', 1.5])
def test_history_window_rejects_unbounded_or_ambiguous_days(days):
    with pytest.raises(ValueError, match='1..20'):
        observation_report(ObservationDelivery(), days=days)


def test_future_or_naive_cutoff_is_rejected():
    probe = ObservationDelivery()
    with pytest.raises(ValueError, match='future'):
        observation_report(probe, as_of=probe.now + timedelta(seconds=1))
    with pytest.raises(ValueError, match='timezone-aware'):
        observation_report(probe, as_of='2026-09-21T20:00:00')


def test_api_includes_markdown_and_invalid_queries_are_read_only(consumer):
    core, snapshot, _, _ = consumer()
    client = create_app(core).test_client()
    path = '/api/daily-research/observation'
    before = fingerprint(core)
    response = client.get(path, query_string={'target_session': snapshot['identity']['target_session'], 'days': 20})
    assert response.status_code == 200, response.text
    assert response.json['current']['run_id'] == snapshot['identity']['run_id']
    assert response.json['read_only'] is True
    assert 'Daily Observation Monitor' in response.json['markdown']
    for query in ({'days': 21}, {'days': 'abc'}, {'run_id': 'TEST_FIXTURE_RUN'},
                  {'as_of': '2099-01-01T00:00:00+08:00'}, {'target_session': 'not-a-date'},
                  {'format': 'markdown'}):
        assert client.get(path, query_string=query).status_code == 400
    assert fingerprint(core) == before


def test_snapshot_publication_and_future_outcomes_do_not_leak_into_earlier_cutoff(consumer):
    core, snapshot, bundle, clock = consumer(decisions=('FOCUS',))
    rows = evaluate_daily_research_outcomes(snapshot, bundle, clock.current)
    save_outcomes(core, rows)
    generated = datetime.fromisoformat(snapshot['identity']['generated_at'])
    early = saved_observation(core, snapshot, as_of=generated - timedelta(seconds=1))
    assert early['current']['status'] == 'RUNNING'
    assert early['current']['run_id'] is None
    assert early['current']['snapshot_status'] == 'NOT_AVAILABLE'
    visible = saved_observation(core, snapshot, as_of=generated)
    assert visible['current']['snapshot_status'] == 'SAVED'
    assert visible['current']['outcome']['status'] == 'PENDING_CAPTURE'
    assert visible['current']['outcome']['totals']['recorded'] == 0
    assert visible['current']['outcome']['totals']['normal'] == 0
    assert visible['current']['outcome']['totals']['pending'] == 20


def test_matured_t1_t5_t20_counts_are_separate_from_successful_capture(consumer):
    core, snapshot, bundle, clock = consumer(decisions=('FOCUS', 'WATCH'))
    save_outcomes(core, evaluate_daily_research_outcomes(snapshot, bundle, clock.current))
    outcome = saved_observation(core, snapshot)['current']['outcome']
    assert outcome['status'] == 'RECORDED'
    for offset in ('1', '5', '20'):
        assert outcome['horizons'][offset]['signals'] == 2
        assert outcome['horizons'][offset]['matured'] == 2
        assert outcome['horizons'][offset]['normal'] == 2
        assert outcome['horizons'][offset]['pending'] == 0
    assert outcome['totals']['signals'] == outcome['totals']['recorded'] == 40


def test_maturity_uses_session_close_not_calendar_age_or_presence_of_label(consumer):
    core, snapshot, bundle, _ = consumer(decisions=('FOCUS',))
    t5_close = datetime.combine(bundle.sessions[10], time(15), TZ)
    before = saved_observation(core, snapshot, as_of=t5_close - timedelta(seconds=1))['current']['outcome']
    after = saved_observation(core, snapshot, as_of=t5_close)['current']['outcome']
    assert before['horizons']['1']['matured'] == 1
    assert before['horizons']['5']['matured'] == 0
    assert after['horizons']['5']['matured'] == 1
    assert after['horizons']['20']['matured'] == 0
    assert after['horizons']['5']['pending_capture'] == 1
    assert after['horizons']['20']['not_matured'] == 1
    assert after['totals']['normal'] == 0
    assert after['totals']['pending'] == 20
    assert after['totals']['missing'] == 0


def test_explicit_missing_suspended_and_unavailable_are_not_merged_or_dropped(consumer):
    core, snapshot, bundle, clock = consumer(decisions=('FOCUS',))
    bundle = bundle.model_copy(update={'bars': tuple(row for row in bundle.bars
        if not (row.instrument_id == 'TEST000' and row.session == bundle.sessions[6]))})
    bundle = security_interval(bundle, 'TEST000', bundle.sessions[10], suspended=True)
    bundle = edit_bar(bundle, 'TEST000', bundle.sessions[25], comparable_basis='TEST_FIXTURE_UNAVAILABLE_BASIS')
    save_outcomes(core, evaluate_daily_research_outcomes(snapshot, bundle, clock.current))
    outcome = saved_observation(core, snapshot)['current']['outcome']
    for offset, status in [('1', 'missing'), ('5', 'suspended'), ('20', 'unavailable')]:
        row = outcome['horizons'][offset]
        assert row['matured'] == row['recorded'] == 1
        assert row[status] == 1 and row['normal'] == 0
        assert row['pending'] == 0
    assert outcome['totals']['signals'] == 20
    assert outcome['totals']['missing'] == 1
    assert outcome['totals']['suspended'] == 1
    assert outcome['totals']['unavailable'] == 1


def test_overdue_absent_or_old_not_matured_labels_remain_pending_capture(consumer):
    core, snapshot, bundle, clock = consumer(decisions=('FOCUS',))
    initial = datetime.fromisoformat(snapshot['identity']['generated_at'])
    early_rows = evaluate_daily_research_outcomes(snapshot, bundle, initial)
    assert {row['status'] for row in early_rows} == {'NOT_MATURED'}
    # Save one prior NOT_MATURED label; nineteen positions have no row at all.
    save_outcomes(core, early_rows[:1])
    outcome = saved_observation(core, snapshot)['current']['outcome']
    assert outcome['horizons']['1']['recorded'] == 1
    assert outcome['horizons']['1']['pending_capture'] == 1
    assert outcome['horizons']['5']['recorded'] == 0
    assert outcome['horizons']['5']['pending_capture'] == 1
    assert outcome['totals']['matured'] == 20
    assert outcome['totals']['pending'] == outcome['totals']['pending_capture'] == 20
    assert outcome['totals']['normal'] == outcome['totals']['missing'] == 0


@pytest.mark.parametrize('variation,expected,registered', [
    ('absent', 'NOT_AVAILABLE', 0), ('wrong_run', 'NOT_AVAILABLE', 0),
    ('wrong_hash', 'NOT_AVAILABLE', 0), ('future_created', 'NOT_AVAILABLE', 0),
    ('partial', 'PARTIAL', 1), ('extra', 'SCOPE_MISMATCH', 2), ('valid', 'REGISTERED', 2),
])
def test_tracking_registry_must_match_saved_identity_and_recommendation_scope(consumer, variation, expected, registered):
    core, snapshot, _, clock = consumer(decisions=('FOCUS', 'WATCH'), register=False)
    run_id = snapshot['identity']['run_id']
    pending = {'run_id': run_id, 'snapshot_hash': snapshot['snapshot_hash'],
        'created_at': snapshot['identity']['generated_at'], 'instrument_ids': ['TEST000', 'TEST001'],
        'horizons': list(range(1, 21))}
    if variation == 'wrong_run':
        pending['run_id'] = 'TEST_FIXTURE_OTHER_RUN'
    elif variation == 'wrong_hash':
        pending['snapshot_hash'] = 'TEST_FIXTURE_OTHER_HASH'
    elif variation == 'future_created':
        pending['created_at'] = (clock.current + timedelta(seconds=1)).isoformat()
    elif variation == 'partial':
        pending['instrument_ids'] = ['TEST000']
    elif variation == 'extra':
        pending['instrument_ids'].append('TEST_FIXTURE_NOT_RECOMMENDED')
    if variation != 'absent':
        core.store.put_document('daily_research_outcome_pending', run_id, pending)
    outcome = saved_observation(core, snapshot)['current']['outcome']
    assert outcome['enrollment_status'] == expected
    assert outcome['registered_recommendations'] == registered
    assert outcome['eligible_recommendations'] == 2
    assert outcome['totals']['signals'] == 40


def test_real_sqlite_jobs_match_single_day_delivery_and_outcomes(consumer, monkeypatch):
    """Exercise batched job lookup against the durable table, not FixtureJobs."""
    import time as wall_time
    from stockfish_core.yaogu.daily_observation import _observation_report
    core, snapshot, bundle, clock = consumer()
    fixture_job = core.candidates.jobs.rows['TEST_FIXTURE_PUBLISHED_PRIMARY']
    generated = datetime.fromisoformat(fixture_job['finished_at'])
    monkeypatch.setattr('stockfish_core.jobs.utc_now', lambda: generated)
    core.jobs.register('scheduled_candidate_capture', lambda ctx: {'kind': 'TEST_FIXTURE'})
    job = core.jobs.submit('scheduled_candidate_capture', fixture_job['payload'],
                           job_id=fixture_job['job_id'])
    deadline = wall_time.monotonic() + 5
    while core.jobs.get(job['job_id'])['state'] not in {'SUCCEEDED', 'FAILED'}:
        assert wall_time.monotonic() < deadline
        wall_time.sleep(.01)
    monkeypatch.setattr(core.candidates, 'jobs', core.jobs)
    arguments = dict(target_session=snapshot['identity']['target_session'], days=1, as_of=clock())
    before = fingerprint(core)
    result = observation_report(core.candidates, **arguments)
    assert result == _observation_report(core.candidates, **arguments)
    assert result['current']['execution_status'] == 'READY'
    assert result['current']['snapshot_hash'] == snapshot['snapshot_hash']
    assert fingerprint(core) == before
