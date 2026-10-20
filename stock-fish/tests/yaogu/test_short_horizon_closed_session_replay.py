"""Explicit TEST_FIXTURE closed-session contract; no real archive or clock override.

The original daily fixture supplies real SQLite, JobService and the evaluator.
Only its archive-authentication boundary is replaced with an explicit fixture.
"""
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone

import pytest

from stockfish_core.yaogu import short_horizon_daily_closure_v1 as closure
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.history_artifacts import digest_json_stream
from stockfish_core.yaogu.short_horizon_outcomes_v1 import NAMESPACES
from stockfish_core.yaogu.strategy_experiments import run_short_horizon_strategy
from tests.yaogu.test_short_horizon_daily_closure_v1 import setup as daily_setup, finished


@pytest.fixture
def replay(daily_setup, monkeypatch):
    fixture = daily_setup
    fixture.now[0] = fixture.evaluated + timedelta(days=2)
    fixture.seal = fixture.evaluated - timedelta(minutes=1)
    actual = fixture.bridge.authenticated_bridge_view
    def authenticated(*args, **kwargs):
        include_archive_seal = kwargs.pop('include_archive_seal', False)
        assert type(include_archive_seal) is bool
        view = actual(*args, **kwargs)
        if include_archive_seal:
            view['archive_sealed_at'] = fixture.seal.isoformat() if fixture.seal is not None else None
        return view
    monkeypatch.setattr(fixture.bridge, 'authenticated_bridge_view', authenticated)
    fixture.replay = closure.ClosedSessionClosure(fixture.service, clock=lambda: fixture.now[0])
    fixture.business = fixture.evaluated.astimezone(closure.SH).date()
    return fixture


def plan(fixture, **changes):
    values = {'business_session': fixture.business, 'evaluated_at': fixture.evaluated, **changes}
    return fixture.replay.plan_replay([fixture.run['run_id']], fixture.input_id, **values)


def submit(fixture, **changes):
    values = {'business_session': fixture.business, 'evaluated_at': fixture.evaluated, **changes}
    return fixture.replay.submit_replay([fixture.run['run_id']], fixture.input_id, **values)


def no_products(fixture):
    assert fixture.store.list_documents(closure.REPLAY_LEDGERS) == []
    assert fixture.store.list_documents(closure.LEDGERS) == []
    assert all(fixture.store.list_documents(namespace) == [] for namespace in NAMESPACES.values())


def test_replay_old_business_day_is_explicit_and_original_daily_stays_rejected(replay):
    current = replay.cycle.plan([replay.run['run_id']], replay.input_id, evaluated_at=replay.evaluated)
    assert not current['can_submit'] and 'EVALUATION_NOT_CURRENT_SESSION' in current['reason_codes']
    historical = plan(replay)
    assert historical['can_submit'] and historical['status'] == 'READY'
    assert historical['schema_version'] == closure.REPLAY_VERSION
    assert historical['execution_mode'] == closure.REPLAY_MODE
    assert historical['current_session_acceptance'] is False
    assert historical['request']['business_session'] == str(replay.business)
    assert historical['request']['bindings']['archive_seals']
    assert replay.service.jobs.list() == []
    no_products(replay)


def test_original_daily_request_keys_and_identity_stay_unchanged(replay):
    replay.now[0] = replay.evaluated
    request = replay.cycle.plan([replay.run['run_id']], replay.input_id)['request']
    assert set(request) == {'policy', 'run_ids', 'input_id', 'evaluation_session', 'bindings',
                            'root_job_id', 'evaluated_at', 'horizons'}
    assert set(request['bindings']) == {'runs', 'outcome_input'}
    identity = {k: request[k] for k in ('policy', 'run_ids', 'input_id', 'evaluation_session', 'bindings')}
    assert request['root_job_id'] == 'short_daily_' + digest(identity)
    assert request['policy'] == {'version': closure.VERSION, 'source_hash': 'TEST_FIXTURE_POLICY'}


def test_replay_success_is_atomic_idempotent_and_keeps_actual_execution_separate(replay):
    before = {key: replay.store.get_document_bytes(namespace, key) for namespace, key in (
        ('strategy_experiment_runs', replay.run['run_id']), ('candidate_inputs', replay.input_id),
        ('candidate_inputs', replay.run['input_id']))}
    first = finished(replay.service, submit(replay)['job_id'])
    assert first['state'] == 'SUCCEEDED', first
    status = replay.replay.status(first['job_id'])
    ledger = status['ledger']
    assert first['kind'] == closure.REPLAY_JOB_KIND and ledger['schema_version'] == closure.REPLAY_VERSION
    assert ledger['execution_mode'] == closure.REPLAY_MODE and ledger['current_session_acceptance'] is False
    assert ledger['knowledge_cutoff'] == first['payload']['evaluated_at']
    assert ledger['evaluated_at_semantics'] == 'HISTORICAL_KNOWLEDGE_CUTOFF_NOT_EXECUTION_TIME'
    assert datetime.fromisoformat(ledger['execution_started_at']) >= replay.evaluated
    assert datetime.fromisoformat(ledger['receipt_created_at']) >= datetime.fromisoformat(ledger['execution_started_at'])
    assert ledger['original_failed_job_rewritten'] is False and ledger['returns'] is None
    assert replay.store.list_documents(closure.LEDGERS) == []
    revisions = {kind: replay.store.documents_for_run(ns, replay.run['run_id']) for kind, ns in NAMESPACES.items()}
    assert all(len(value) == 1 for value in revisions.values())
    assert len({value[0]['revision_id'] for value in revisions.values()}) == 1
    events = replay.service.jobs.events(first['job_id'])
    replay.now[0] += timedelta(days=3)
    again = submit(replay)
    assert again == first and replay.service.jobs.events(first['job_id']) == events
    assert replay.replay.status(first['job_id'])['ledger'] == ledger
    assert {kind: replay.store.documents_for_run(ns, replay.run['run_id']) for kind, ns in NAMESPACES.items()} == revisions
    assert before == {key: replay.store.get_document_bytes(namespace, key) for namespace, key in (
        ('strategy_experiment_runs', replay.run['run_id']), ('candidate_inputs', replay.input_id),
        ('candidate_inputs', replay.run['input_id']))}


def test_replay_cutoff_is_identity_but_equivalent_timezone_is_canonical(replay):
    first = plan(replay)
    later = plan(replay, evaluated_at=replay.evaluated + timedelta(seconds=1))
    assert first['job_id'] != later['job_id']
    assert first['request']['evaluated_at'] != later['request']['evaluated_at']
    assert plan(replay, evaluated_at=replay.evaluated.astimezone(timezone.utc))['request'] == first['request']
    a = finished(replay.service, submit(replay)['job_id'])
    b = finished(replay.service, submit(replay, evaluated_at=replay.evaluated + timedelta(seconds=1))['job_id'])
    assert a['state'] == b['state'] == 'SUCCEEDED' and a['job_id'] != b['job_id']
    assert a['result']['revision_ids'] == b['result']['revision_ids']
    assert len(replay.store.list_documents(closure.REPLAY_LEDGERS)) == 2
    assert all(len(replay.store.documents_for_run(ns, replay.run['run_id'])) == 1 for ns in NAMESPACES.values())


@pytest.mark.parametrize('fault,reason', [
    ('future', 'FUTURE_EVALUATION_CUTOFF'),
    ('different_day', 'REPLAY_CUTOFF_BUSINESS_SESSION_MISMATCH'),
    ('before_close', 'BEFORE_SESSION_CLOSE'),
    ('weekend', 'NO_SESSION'),
])
def test_replay_invalid_date_gate_never_authenticates_or_writes(replay, fault, reason):
    values = {}
    if fault == 'future': values['evaluated_at'] = replay.now[0] + timedelta(seconds=1)
    elif fault == 'different_day': values['business_session'] = replay.business - timedelta(days=1)
    elif fault == 'before_close': values['evaluated_at'] = datetime.combine(replay.business, time(14, 59), closure.SH)
    else:
        day = replay.business
        while day.weekday() != 5: day -= timedelta(days=1)
        values = {'business_session': day, 'evaluated_at': datetime.combine(day, time(20), closure.SH)}
    created = replay.verification['created']
    result = plan(replay, **values)
    assert not result['can_submit'] and reason in result['reason_codes']
    with pytest.raises(ValueError): submit(replay, **values)
    assert replay.verification['created'] == created and replay.service.jobs.list() == []
    no_products(replay)


@pytest.mark.parametrize('fault', ['naive_cutoff', 'datetime_business', 'missing_cutoff'])
def test_replay_requires_explicit_aware_cutoff_and_business_date(replay, fault):
    with pytest.raises((ValueError, TypeError)):
        if fault == 'naive_cutoff': plan(replay, evaluated_at=replay.evaluated.replace(tzinfo=None))
        elif fault == 'datetime_business': plan(replay, business_session=replay.evaluated)
        else: replay.replay.plan_replay([replay.run['run_id']], replay.input_id, business_session=replay.business)
    assert replay.service.jobs.list() == []


@pytest.mark.parametrize('fault', ['missing', 'late', 'naive'])
def test_replay_requires_authenticated_seal_at_or_before_frozen_cutoff(replay, fault):
    if fault == 'missing': replay.seal = None
    elif fault == 'late': replay.seal = replay.evaluated + timedelta(seconds=1)
    else: replay.seal = replay.seal.replace(tzinfo=None)
    with pytest.raises(ValueError): submit(replay)
    assert replay.service.jobs.list() == []
    no_products(replay)


def test_replay_does_not_allow_clock_override_for_real_service(replay):
    replay.service.allow_test_fixture = False
    with pytest.raises(ValueError, match='REAL_CLOCK_REQUIRED'):
        closure.ClosedSessionClosure(replay.service, clock=lambda: replay.now[0])
    assert closure.ClosedSessionClosure(replay.service).clock is closure.utc_now


@pytest.mark.parametrize('method', ['plan', 'submit', 'pending'])
def test_replay_cannot_accidentally_use_daily_entrypoint(replay, method):
    with pytest.raises(ValueError, match='EXPLICIT'):
        getattr(replay.replay, method)([replay.run['run_id']], replay.input_id)
    assert replay.service.jobs.list() == []


@pytest.mark.parametrize('fault', ['cutoff', 'mode', 'business', 'policy', 'horizons'])
def test_worker_rechecks_frozen_replay_request_even_with_recomputed_identity(replay, fault):
    request = deepcopy(plan(replay)['request'])
    if fault == 'cutoff': request['evaluated_at'] = (replay.now[0] + timedelta(days=1)).astimezone(timezone.utc).isoformat()
    elif fault == 'mode': request['execution_mode'] = 'TEST_FIXTURE_FAKE_CURRENT_DAY'
    elif fault == 'business': request['business_session'] = str(replay.business - timedelta(days=1))
    elif fault == 'policy': request['policy']['source_hash'] = 'TEST_FIXTURE_CHANGED_POLICY'
    else: request['horizons'] = [1]
    request['root_job_id'] = replay.replay.job_prefix + digest(replay.replay._request_identity(request))
    job = replay.service.jobs.submit(closure.REPLAY_JOB_KIND, request, replay.replay._worker, job_id=request['root_job_id'])
    assert finished(replay.service, job['job_id'])['state'] == 'FAILED'
    no_products(replay)


def test_worker_reauthenticates_seal_and_original_binding(replay, monkeypatch):
    actual = replay.bridge.authenticated_bridge_view
    def changed(*args, **kwargs):
        view = actual(*args, **kwargs)
        if replay.verification['created'] >= 2:
            view['archive_sealed_at'] = (replay.evaluated + timedelta(seconds=1)).isoformat()
        return view
    monkeypatch.setattr(replay.bridge, 'authenticated_bridge_view', changed)
    job = finished(replay.service, submit(replay)['job_id'])
    assert job['state'] == 'FAILED'
    no_products(replay)


def test_final_transaction_terminal_failure_rolls_back_three_products_and_replay_receipt(replay, monkeypatch):
    original = replay.bridge.ArchiveVerificationSession.terminal
    seen = []
    def terminal(session):
        result = original(session)
        seen.append('terminal')
        if len(seen) == 3:
            raise ValueError('TEST_FIXTURE_LATE_ORIGINAL_MUTATION')
        return result
    monkeypatch.setattr(replay.bridge.ArchiveVerificationSession, 'terminal', terminal)
    job = finished(replay.service, submit(replay)['job_id'])
    assert job['state'] == 'FAILED' and len(seen) == 3
    no_products(replay)


def test_replay_retry_keeps_exact_cutoff_mode_and_prior_failure(replay, monkeypatch):
    original = replay.bridge.ArchiveVerificationSession.terminal
    failing = [True]
    def terminal(session):
        if replay.verification['created'] > 1 and failing[0]:
            raise ValueError('TEST_FIXTURE_VERIFICATION_BUDGET_OR_SOURCE_FAILURE')
        return original(session)
    monkeypatch.setattr(replay.bridge.ArchiveVerificationSession, 'terminal', terminal)
    first = finished(replay.service, submit(replay)['job_id'])
    assert first['state'] == 'FAILED'
    no_products(replay)
    failing[0] = False
    replay.now[0] += timedelta(days=1)
    retried = finished(replay.service, replay.replay.retry(first['job_id'])['job_id'])
    assert retried['state'] == 'SUCCEEDED' and retried['attempt'] == 2
    assert retried['payload'] == first['payload'] and retried['retry_of'] == first['job_id']
    assert replay.service.jobs.get(first['job_id']) == first
    with pytest.raises(ValueError, match='JOB_REQUIRED'):
        replay.cycle.status(retried['job_id'])


def test_two_frozen_runs_allow_due_and_pending_without_manufacturing_second_products(replay):
    new = run_short_horizon_strategy(replay.bundle, cutoff=replay.evaluated, input_id=replay.input_id,
                                     strategy_id='SHORT_HORIZON_STRENGTH_R2')
    new['content_hash'] = digest_json_stream(new)
    replay.store.put_document('strategy_experiment_runs', new['run_id'], new)
    replay.bind(new)
    job = replay.replay.submit_replay([new['run_id'], replay.run['run_id']], replay.input_id,
        business_session=replay.business, evaluated_at=replay.evaluated)
    job = finished(replay.service, job['job_id'])
    assert job['state'] == 'SUCCEEDED', job
    records = {r['run_id']: r for r in replay.replay.status(job['job_id'])['ledger']['runs']}
    assert records[replay.run['run_id']]['product_revision_available'] is True
    assert records[new['run_id']]['status'] == 'PENDING'
    assert records[new['run_id']]['revision_id'] is None
    assert not records[new['run_id']]['product_revision_available']
    assert all(replay.store.documents_for_run(ns, new['run_id']) == [] for ns in NAMESPACES.values())


def test_replay_input_business_day_mismatch_never_submits(replay):
    sessions = closure.exchange_calendar_2026().sessions
    business = sessions[sessions.index(replay.business) - 1]
    evaluated = datetime.combine(business, time(20), closure.SH)
    replay.seal = evaluated - timedelta(minutes=1)
    result = plan(replay, business_session=business, evaluated_at=evaluated)
    assert not result['can_submit'] and 'REPLAY_INPUT_BUSINESS_SESSION_MISMATCH' in result['reason_codes']
    with pytest.raises(ValueError, match='REPLAY_INPUT_BUSINESS_SESSION_MISMATCH'):
        submit(replay, business_session=business, evaluated_at=evaluated)
    assert replay.service.jobs.list() == []
    no_products(replay)


def test_replay_missing_price_is_unknown_with_null_metrics(replay):
    bar = next(row for row in replay.bundle.bars if row.session > replay.original.observation_date)
    changed = replay.bundle.model_copy(update={'bars': tuple(row for row in replay.bundle.bars if row is not bar)})
    input_id = replay.service.register_input(changed)
    job = replay.replay.submit_replay([replay.run['run_id']], input_id,
        business_session=replay.business, evaluated_at=replay.evaluated)
    job = finished(replay.service, job['job_id'])
    assert job['state'] == 'SUCCEEDED', job
    outcome = replay.store.documents_for_run(NAMESPACES['outcomes'], replay.run['run_id'])[0]
    unknown = [item for item in outcome['items'] if item['status'] == 'UNKNOWN']
    assert unknown and all(item['reason_codes'] and all(value is None for value in item['metrics'].values()) for item in unknown)
    assert replay.replay.status(job['job_id'])['ledger']['status'] == 'COMPLETED_WITH_UNKNOWNS'


def test_replay_cancel_before_publication_keeps_all_products_absent(replay, monkeypatch):
    original = closure.evaluate
    def cancel_after_evaluation(*args, **kwargs):
        products = original(*args, **kwargs)
        job = replay.service.jobs.list(kind=closure.REPLAY_JOB_KIND)[0]
        replay.replay.cancel(job['job_id'])
        return products
    monkeypatch.setattr(closure, 'evaluate', cancel_after_evaluation)
    job = finished(replay.service, submit(replay)['job_id'])
    assert job['state'] == 'CANCELLED', job
    no_products(replay)


def test_replay_deferred_borrow_cannot_change_previously_authenticated_seal(replay, monkeypatch):
    original = replay.bridge.authenticated_bridge_view
    def changed(*args, **kwargs):
        view = original(*args, **kwargs)
        if kwargs.get('borrow_run'):
            view['archive_sealed_at'] = (replay.seal - timedelta(seconds=1)).isoformat()
        return view
    monkeypatch.setattr(replay.bridge, 'authenticated_bridge_view', changed)
    job = finished(replay.service, submit(replay)['job_id'])
    assert job['state'] == 'FAILED'
    no_products(replay)
