"""TEST_FIXTURE late admission and resource identity; no real archives/network."""
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from stockfish_core.common import canonical_json
from stockfish_core.yaogu import short_horizon_daily_closure_v1 as closure
from stockfish_core.yaogu.short_horizon_outcomes_v1 import NAMESPACES
from stockfish_core.yaogu.short_horizon_resource_profiles_v1 import FULL_ARCHIVE_900S_V1, resolve_verification_profile
from tests.yaogu.test_short_horizon_daily_closure_v1 import setup as daily_setup, finished


@pytest.fixture
def late(daily_setup, monkeypatch):
    f = daily_setup
    f.business = f.evaluated.astimezone(closure.SH).date()
    f.cutoff = datetime.combine(f.business + timedelta(days=1), time(8), closure.SH)
    f.now[0] = f.cutoff + timedelta(minutes=1)
    f.seal = f.cutoff - timedelta(seconds=1)
    actual = f.bridge.authenticated_bridge_view
    def authenticated(*args, **kwargs):
        include = kwargs.pop('include_archive_seal', False)
        view = actual(*args, **kwargs)
        if include:
            view['archive_sealed_at'] = f.seal.isoformat() if f.seal else None
        return view
    monkeypatch.setattr(f.bridge, 'authenticated_bridge_view', authenticated)
    f.late = closure.LateCompletedSessionClosure(f.service, clock=lambda: f.now[0])
    return f


def plan(f, **changes):
    return f.late.plan_late([f.run['run_id']], f.input_id,
        **{'business_session': f.business, 'evaluated_at': f.cutoff, **changes})


def submit(f):
    return f.late.submit_late([f.run['run_id']], f.input_id,
                             business_session=f.business, evaluated_at=f.cutoff)


def no_products(f):
    assert f.store.list_documents(closure.LATE_LEDGERS) == []
    assert all(f.store.list_documents(ns) == [] for ns in NAMESPACES.values())


def install_profile_session(f, monkeypatch):
    previous = f.bridge.ArchiveVerificationSession
    f.session_options = []
    class Session(previous):
        def __init__(self, *, checkpoint=lambda: None, **options):
            f.session_options.append(dict(options))
            self.options = options
            super().__init__(checkpoint=checkpoint)
        def terminal(self):
            result = super().terminal()
            if self.options:
                result['limits'] = {key: self.options['max_' + key] for key in
                    ('seconds', 'rss_bytes', 'total_read_bytes', 'unique_bytes', 'files')}
            return result
    monkeypatch.setattr(f.bridge, 'ArchiveVerificationSession', Session)


def test_late_plan_keeps_actual_cutoff_and_original_modes_reject(late):
    p = plan(late)
    assert p['can_submit'] and p['status'] == 'READY'
    assert p['execution_mode'] == closure.LATE_MODE
    assert p['knowledge_cutoff'] == late.cutoff.astimezone(timezone.utc).isoformat()
    assert p['business_session'] == str(late.business)
    assert p['late_processing'] is True and p['historical_visibility_upgraded'] is False
    assert p['current_session_acceptance'] is False
    assert p['request']['bindings']['archive_seals']
    assert late.cycle.plan([late.run['run_id']], late.input_id, evaluated_at=late.cutoff)['can_submit'] is False
    replay = closure.ClosedSessionClosure(late.service, clock=lambda: late.now[0])
    old = replay.plan_replay([late.run['run_id']], late.input_id,
                            business_session=late.business, evaluated_at=late.cutoff)
    assert not old['can_submit'] and 'REPLAY_CUTOFF_BUSINESS_SESSION_MISMATCH' in old['reason_codes']
    assert late.service.jobs.list() == []
    no_products(late)


def test_late_success_three_products_idempotence_and_parent_bytes(late):
    parents = {(ns, id): late.store.get_document_bytes(ns, id) for ns, id in (
        ('strategy_experiment_runs', late.run['run_id']), ('candidate_inputs', late.input_id),
        ('candidate_inputs', late.run['input_id']))}
    job = finished(late.service, submit(late)['job_id'])
    assert job['state'] == 'SUCCEEDED', job
    result = late.late.status(job['job_id'])
    ledger = result['ledger']
    assert ledger['schema_version'] == closure.LATE_VERSION
    assert ledger['knowledge_cutoff'] == job['payload']['evaluated_at']
    assert ledger['business_session'] == str(late.business)
    assert ledger['late_processing'] is True and ledger['historical_visibility_upgraded'] is False
    assert ledger['current_session_acceptance'] is False and ledger['original_failed_job_rewritten'] is False
    assert ledger['evaluated_at_semantics'] == 'ACTUAL_LATE_KNOWLEDGE_CUTOFF_NOT_BUSINESS_DAY_PIT'
    assert datetime.fromisoformat(ledger['receipt_created_at']) >= datetime.fromisoformat(ledger['execution_started_at'])
    products = {k: late.store.documents_for_run(ns, late.run['run_id']) for k, ns in NAMESPACES.items()}
    assert all(len(v) == 1 for v in products.values())
    assert len({v[0]['revision_id'] for v in products.values()}) == 1
    assert late.verification['terminal'] == 3  # Plan, prepublication, same write transaction.
    assert submit(late) == job
    assert result == late.late.status(job['job_id'])
    assert products == {k: late.store.documents_for_run(ns, late.run['run_id']) for k, ns in NAMESPACES.items()}
    assert all(late.store.get_document_bytes(ns, id) == value for (ns, id), value in parents.items())
    assert late.store.list_documents(closure.LEDGERS) == late.store.list_documents(closure.REPLAY_LEDGERS) == []


@pytest.mark.parametrize('instant,expected', [
    ('2026-10-21T14:59:59+08:00', '2026-10-20'),
    ('2026-10-21T15:00:00+08:00', '2026-10-21'),
    ('2026-10-18T12:00:00+08:00', '2026-10-16'),
    ('2026-10-08T08:00:00+08:00', '2026-09-30'),
])
def test_official_latest_completed_close_weekend_and_holiday(instant, expected):
    assert str(closure.LateCompletedSessionClosure._latest_completed(instant)) == expected


@pytest.mark.parametrize('instant', ['2025-12-31T20:00:00+08:00', '2027-01-01T08:00:00+08:00'])
def test_calendar_coverage_does_not_extend_weekday_rule(instant):
    with pytest.raises(ValueError, match='CALENDAR_OUT_OF_COVERAGE'):
        closure.LateCompletedSessionClosure._latest_completed(instant)


@pytest.mark.parametrize('fault,reason', [
    ('future', 'FUTURE_EVALUATION_CUTOFF'),
    ('same_day', 'LATE_PROCESSING_REQUIRES_LATER_CALENDAR_DATE'),
    ('old_business', 'LATE_BUSINESS_NOT_LATEST_COMPLETED_AT_CUTOFF'),
    ('stale_admission', 'LATE_BUSINESS_NOT_LATEST_COMPLETED_AT_ADMISSION'),
])
def test_invalid_late_admission_does_not_authenticate(late, fault, reason):
    values = {'business_session': late.business, 'evaluated_at': late.cutoff}
    if fault == 'future': values['evaluated_at'] = late.now[0] + timedelta(seconds=1)
    elif fault == 'same_day': values['evaluated_at'] = late.evaluated
    elif fault == 'old_business': values['business_session'] = closure.exchange_calendar_2026().sessions[
        closure.exchange_calendar_2026().sessions.index(late.business) - 1]
    else:
        next_day = next(d for d in closure.exchange_calendar_2026().sessions if d > late.business)
        late.now[0] = datetime.combine(next_day, time(15), closure.SH)
    before = late.verification['created']
    p = late.late.plan_late([late.run['run_id']], late.input_id, **values)
    assert not p['can_submit'] and reason in p['reason_codes']
    assert late.verification['created'] == before
    assert late.service.jobs.list() == []
    no_products(late)


def test_close_crossed_during_authentication_refuses_admission(late, monkeypatch):
    original = late.late._authenticated
    next_day = next(d for d in closure.exchange_calendar_2026().sessions if d > late.business)
    def auth(*args, **kwargs):
        result = original(*args, **kwargs)
        late.now[0] = datetime.combine(next_day, time(15), closure.SH)
        return result
    monkeypatch.setattr(late.late, '_authenticated', auth)
    p = plan(late)
    assert not p['can_submit'] and 'LATE_BUSINESS_NOT_LATEST_COMPLETED_AT_ADMISSION' in p['reason_codes']
    assert late.verification['terminal'] == 1 and not late.service.jobs.list()


def test_existing_accepted_retry_keeps_cutoff_after_later_close(late, monkeypatch):
    payload = plan(late)['request']
    job = late.service.jobs.create(late.late.job_kind, payload, job_id=payload['root_job_id'])
    with late.store.transaction() as c:
        c.execute("UPDATE jobs SET state='FAILED' WHERE job_id=?", (job['job_id'],))
    late.now[0] += timedelta(days=4)
    assert late.late._validated_evaluation(payload)[0] == late.cutoff
    assert not plan(late)['can_submit']
    result = finished(late.service, late.late.retry(job['job_id'])['job_id'])
    assert result['state'] == 'SUCCEEDED', result
    assert result['payload'] == payload and result['retry_of'] == job['job_id']
    assert late.service.jobs.get(job['job_id'])['state'] == 'FAILED'


@pytest.mark.parametrize('fault', ['missing_seal', 'late_seal'])
def test_source_seal_is_actual_cutoff_not_business_date(late, fault):
    late.seal = None if fault == 'missing_seal' else late.cutoff + timedelta(microseconds=1)
    with pytest.raises(ValueError, match='ARCHIVE_SEAL'):
        plan(late)
    no_products(late)


def test_late_source_mutation_rolls_back_products_and_ledger(late, monkeypatch):
    original = late.store._put_document
    def put(connection, namespace, document_id, value):
        original(connection, namespace, document_id, value)
        if namespace == closure.LATE_LEDGERS:
            late.verification['fail_terminal'] = True
    monkeypatch.setattr(late.store, '_put_document', put)
    result = finished(late.service, submit(late)['job_id'])
    assert result['state'] == 'FAILED'
    no_products(late)


def test_nested_borrowed_run_mutation_refused(late, monkeypatch):
    original = closure.evaluate
    def evaluate(*args, **kwargs):
        result = original(*args, **kwargs)
        args[0]['results'][args[0]['strategy_id']]['counts']['TEST_FIXTURE_LATE_CHANGE'] = 1
        return result
    monkeypatch.setattr(closure, 'evaluate', evaluate)
    assert finished(late.service, submit(late)['job_id'])['state'] == 'FAILED'
    no_products(late)


def test_profile_propagates_plan_worker_and_terminal_exact_limits(late, monkeypatch):
    install_profile_session(late, monkeypatch)
    late.late = closure.LateCompletedSessionClosure(late.service, clock=lambda: late.now[0],
                                                   verification_profile=FULL_ARCHIVE_900S_V1)
    p = plan(late)
    resource = resolve_verification_profile(FULL_ARCHIVE_900S_V1)
    assert p['request']['policy']['verification_resource_profile'] == resource
    assert p['archive_verification']['verification_resource_profile'] == resource
    result = finished(late.service, late.late._submit_plan(p)['job_id'])
    assert result['state'] == 'SUCCEEDED', result
    assert late.session_options == [resource['limits'], resource['limits']]
    assert late.late.status(result['job_id'])['ledger']['archive_verification']['verification_resource_profile'] == resource


def test_default_policy_shape_and_plan_kwargs_unchanged(late, monkeypatch):
    install_profile_session(late, monkeypatch)
    p = plan(late)
    assert 'verification_resource_profile' not in p['request']['policy']
    assert 'verification_resource_profile' not in p['archive_verification']
    assert late.session_options == [{}]


@pytest.mark.parametrize('mode', ['daily', 'replay'])
def test_existing_mode_explicit_profile_session(late, monkeypatch, mode):
    install_profile_session(late, monkeypatch)
    constructor = closure.ShortHorizonDailyClosure if mode == 'daily' else closure.ClosedSessionClosure
    cycle = constructor(late.service, clock=lambda: late.now[0], verification_profile=FULL_ARCHIVE_900S_V1)
    if mode == 'daily':
        late.now[0] = late.evaluated
        p = cycle.plan([late.run['run_id']], late.input_id)
    else:
        late.seal = late.evaluated - timedelta(seconds=1)
        p = cycle.plan_replay([late.run['run_id']], late.input_id,
                             business_session=late.business, evaluated_at=late.evaluated)
    assert p['can_submit']
    assert p['request']['policy']['verification_resource_profile'] == resolve_verification_profile(FULL_ARCHIVE_900S_V1)
    assert late.session_options == [resolve_verification_profile(FULL_ARCHIVE_900S_V1)['limits']]


@pytest.mark.parametrize('state', ['FAILED', 'RUNNING'])
def test_old_300_job_cannot_be_recovered_or_retried_as_900(late, monkeypatch, state):
    p = plan(late)
    job = late.service.jobs.create(late.late.job_kind, p['request'], job_id=p['job_id'])
    with late.store.transaction() as c:
        c.execute('UPDATE jobs SET state=?,owner_id=? WHERE job_id=?', (state, 'TEST_FIXTURE_OLD_OWNER', job['job_id']))
    before = late.service.jobs.get(job['job_id']), late.service.jobs.events(job['job_id'])
    upgraded = closure.LateCompletedSessionClosure(late.service, clock=lambda: late.now[0],
                                                   verification_profile=FULL_ARCHIVE_900S_V1)
    with pytest.raises(ValueError, match='RETRY_RESOURCE_PROFILE_MISMATCH'):
        upgraded.retry(job['job_id'])
    if state == 'RUNNING':
        with pytest.raises(ValueError, match='RETRY_RESOURCE_PROFILE_MISMATCH'):
            upgraded._recover_target(job['job_id'])
    assert before == (late.service.jobs.get(job['job_id']), late.service.jobs.events(job['job_id']))
    assert len(late.service.jobs.list()) == 1
    no_products(late)


def test_changed_limits_reject_terminal(late, monkeypatch):
    install_profile_session(late, monkeypatch)
    late.late = closure.LateCompletedSessionClosure(late.service, clock=lambda: late.now[0],
                                                   verification_profile=FULL_ARCHIVE_900S_V1)
    previous = late.bridge.ArchiveVerificationSession.terminal
    def terminal(self):
        result = previous(self)
        result['limits']['seconds'] = 901
        return result
    monkeypatch.setattr(late.bridge.ArchiveVerificationSession, 'terminal', terminal)
    with pytest.raises(ValueError, match='RESOURCE_LIMITS_MISMATCH'):
        plan(late)
    no_products(late)


def test_real_clock_required_for_new_mode():
    with pytest.raises(ValueError, match='REAL_CLOCK_REQUIRED'):
        closure.LateCompletedSessionClosure(SimpleNamespace(), clock=lambda: datetime.now(timezone.utc))


def test_latest_input_axis_must_equal_business_session(late, monkeypatch):
    original = late.late._authenticated
    def auth(*args, **kwargs):
        runs, calendar, bindings = original(*args, **kwargs)
        altered = closure._InputCalendar(calendar.input_id, calendar.input_hash,
            calendar.observation_date, calendar.sessions + (late.business + timedelta(days=3),))
        return runs, altered, bindings
    monkeypatch.setattr(late.late, '_authenticated', auth)
    p = plan(late)
    assert not p['can_submit'] and 'LATE_INPUT_BUSINESS_SESSION_MISMATCH' in p['reason_codes']
    no_products(late)


@pytest.mark.parametrize('field,value', [
    ('execution_mode', closure.REPLAY_MODE), ('business_session', '2026-10-01'),
    ('evaluation_session', '2026-10-01'), ('horizons', [1]),
    ('evaluated_at', '2026-10-20T20:00:00+00:00'),
])
def test_frozen_late_request_mutation_refused(late, field, value):
    request = deepcopy(plan(late)['request'])
    request[field] = value
    with pytest.raises(ValueError, match='FROZEN_REQUEST_INVALID'):
        late.late._validated_evaluation(request)
    no_products(late)


def test_cancel_before_evaluation_publishes_nothing(late, monkeypatch):
    original = late.late._authenticated
    def auth(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get('retain_input'):
            job = late.service.jobs.list()[0]
            late.service.jobs.cancel(job['job_id'])
        return result
    monkeypatch.setattr(late.late, '_authenticated', auth)
    result = finished(late.service, submit(late)['job_id'])
    assert result['state'] == 'CANCELLED', result
    no_products(late)


def test_explicit_profile_applies_to_pending_discovery(late, monkeypatch):
    install_profile_session(late, monkeypatch)
    late.now[0] = late.evaluated
    cycle = closure.ShortHorizonDailyClosure(late.service, clock=lambda: late.now[0],
                                             verification_profile=FULL_ARCHIVE_900S_V1)
    result = cycle.pending(late.input_id)
    resource = resolve_verification_profile(FULL_ARCHIVE_900S_V1)
    assert result['archive_verification']['verification_resource_profile'] == resource
    assert late.session_options == [resource['limits']]
    assert not late.service.jobs.list()


def test_profile_tampered_after_submission_refuses_before_auth(late, monkeypatch):
    install_profile_session(late, monkeypatch)
    late.late = closure.LateCompletedSessionClosure(late.service, clock=lambda: late.now[0],
                                                   verification_profile=FULL_ARCHIVE_900S_V1)
    p = plan(late)
    p['request']['policy']['verification_resource_profile']['limits']['max_seconds'] = 901
    before = late.verification['created']
    result = finished(late.service, late.late._submit_plan(p)['job_id'])
    assert result['state'] == 'FAILED'
    assert late.verification['created'] == before
    no_products(late)
