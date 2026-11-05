"""TEST_FIXTURE orchestration tests; archive trust itself has separate tests.

Only the archive verifier boundary is replaced here. SQLite, JobService,
registered-input decoding and SHORT_HORIZON_OUTCOME_V1 are real implementations.
"""
from copy import deepcopy
from datetime import datetime, time, timedelta
from types import SimpleNamespace
import threading
import time as elapsed

import pytest

from stockfish_core.jobs import JobConflict, JobService
from stockfish_core.storage import SQLiteStore, ReadOnlySQLiteStore
from stockfish_core.yaogu.candidate_service import CandidateService
from stockfish_core.yaogu.contracts import MarketInput
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.history_artifacts import digest_json_stream
from stockfish_core.yaogu import short_horizon_daily_closure_v1 as closure
from stockfish_core.yaogu.short_horizon_outcomes_v1 import NAMESPACES
from stockfish_core.yaogu.strategy_experiments import run_short_horizon_strategy
from tests.support.short_horizon_strength_fixture import strength_bundle, payload, TZ, START
from tests.yaogu.test_short_horizon_outcomes_v1 import future_input


def finished(service, job_id):
    deadline = elapsed.monotonic() + 10
    while elapsed.monotonic() < deadline:
        job = service.jobs.get(job_id)
        if job['state'] in {'SUCCEEDED', 'FAILED', 'CANCELLED', 'INTERRUPTED'}:
            return job
        elapsed.sleep(.005)
    raise AssertionError('TEST_FIXTURE closure did not finish within 10 seconds')


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / 'TEST_FIXTURE_CLOSURE.sqlite3')
    jobs = JobService(store, max_workers=1)
    service = object.__new__(CandidateService)
    service.store, service.jobs, service.allow_test_fixture = store, jobs, True
    original, cutoff = strength_bundle()
    signal_id = service.register_input(original)
    run = run_short_horizon_strategy(original, cutoff=cutoff, input_id=signal_id,
                                     strategy_id='SHORT_HORIZON_STRENGTH_R2')
    run['content_hash'] = digest_json_stream(run)
    store.put_document('strategy_experiment_runs', run['run_id'], run)
    bundle, evaluated = future_input(original)
    input_id = service.register_input(bundle)
    now = [evaluated]
    verification = {'created': 0, 'terminal': 0, 'deny': False, 'fail_terminal': False,
                    'events': [], 'borrowed': []}

    class Session:
        def __init__(self, *, checkpoint=lambda: None):
            verification['created'] += 1
            self.checkpoint = checkpoint
            self.inputs, self.borrowed = set(), []

        def release_cached_archives(self):
            verification['events'].append(('release', id(self)))

        def terminal(self):
            self.checkpoint()
            verification['terminal'] += 1
            if verification['fail_terminal']:
                raise ValueError('TEST_FIXTURE_ARCHIVE_CHANGED_BEFORE_TERMINAL')
            for run, checksum in self.borrowed:
                if digest_json_stream(run) != checksum:
                    raise ValueError('TEST_FIXTURE_BORROWED_RUN_CHANGED')
            return {'scope': 'TEST_FIXTURE_MOCK_ARCHIVE_VERIFIER', 'terminal': 'VERIFIED'}

    def require_input(svc, identifier, *, session):
        if verification['deny']:
            raise ValueError('TEST_FIXTURE_ARCHIVE_NOT_TRUSTED')
        assert svc.store.get_document('candidate_inputs', identifier) is not None
        return {'bridge_id': 'TEST_FIXTURE_INPUT_CERT_' + identifier, 'input_id': identifier,
                'input_hash': identifier.removeprefix('market_'), 'archive_final_sha256': 'TEST_FIXTURE_ARCHIVE'}

    def require_run(svc, identifier, *, session):
        saved = svc.store.get_document('strategy_experiment_runs', identifier)
        return {**require_input(svc, saved['input_id'], session=session), 'run_id': identifier,
                'bridge_id': 'TEST_FIXTURE_RUN_CERT_' + identifier, 'signal_content_hash': saved['content_hash']}

    def authenticated_view(svc, *, run_id=None, input_id=None, session, borrow_run=False, retain_input=False):
        session.checkpoint()
        saved = svc.store.get_document('strategy_experiment_runs', run_id) if run_id else None
        certificate = (require_run(svc, run_id, session=session) if run_id else
                       require_input(svc, input_id, session=session))
        identifier = certificate['input_id']
        original = svc.store.get_document('candidate_inputs', identifier)
        session.inputs.add(identifier)
        verification['events'].append(('borrow_run' if borrow_run else
            'authenticate_run' if run_id else 'authenticate_input', id(session), run_id or input_id))
        if retain_input:
            assert input_id is not None and not borrow_run
            verification['events'].append(('retain_input', id(session), input_id))
        result = {'credential': certificate, 'input_metadata': {
            'input_id': identifier, 'input_hash': certificate['input_hash'],
            'observation_date': original['observation_date'], 'sessions': original['sessions']},
            'run_metadata': {key: saved[key] for key in ('run_id', 'strategy_id', 'execution_profile',
                'content_hash', 'input_id', 'observation_date', 'decision_cutoff', 'generated_at', 'coverage_type')
                if key in saved}
                if saved else {}}
        if borrow_run:
            result['run'] = saved
            session.borrowed.append((saved, digest_json_stream(saved)))
            verification['borrowed'].append(saved)
        return result

    def load_verified_input(svc, input_id, *, session):
        session.checkpoint()
        assert input_id in session.inputs, 'TEST_FIXTURE_INPUT_NOT_AUTHENTICATED'
        verification['events'].append(('load_input', id(session), input_id))
        return svc.input(input_id)[1]

    bridge = SimpleNamespace(ArchiveVerificationSession=Session, require_verified_bridge=require_run,
        require_verified_bridge_input=require_input, VERSION='TEST_FIXTURE_BRIDGE',
        authenticated_bridge_view=authenticated_view, load_verified_bridge_input=load_verified_input,
        RUN_BINDINGS='TEST_FIXTURE_BRIDGE_RUN_BINDINGS', CERTIFICATES='TEST_FIXTURE_BRIDGE_CERTIFICATES')

    def bind(saved):
        binding = dict(schema_version=bridge.VERSION, **require_run(service, saved['run_id'], session=None))
        store.put_document(bridge.CERTIFICATES, binding['bridge_id'], binding)
        store.put_document(bridge.RUN_BINDINGS, saved['run_id'], binding)

    bind(run)
    monkeypatch.setattr(closure, '_bridge_module', lambda: bridge)
    monkeypatch.setattr(closure, '_policy_identity', lambda: {'version': closure.VERSION, 'source_hash': 'TEST_FIXTURE_POLICY'})
    cycle = closure.ShortHorizonDailyClosure(service, clock=lambda: now[0])
    try:
        yield SimpleNamespace(service=service, store=store, cycle=cycle, now=now, verification=verification,
            run=run, original=original, bundle=bundle, input_id=input_id, evaluated=evaluated, bind=bind,
            bridge=bridge)
    finally:
        jobs.shutdown()


def rows(fixture, namespace):
    return fixture.store.documents_for_run(namespace, fixture.run['run_id'])


def capture_worker_errors(fixture, monkeypatch):
    errors, actual = [], fixture.cycle._worker

    def observed(ctx):
        try:
            return actual(ctx)
        except ValueError as exc:
            errors.append(str(exc))
            raise

    monkeypatch.setattr(fixture.cycle, '_worker', observed)
    return errors


def test_same_day_input_is_idempotent_and_first_cutoff_frozen(setup):
    original_bytes = setup.store.get_document_bytes('strategy_experiment_runs', setup.run['run_id'])
    first = setup.cycle.submit([setup.run['run_id']], setup.input_id)
    first = finished(setup.service, first['job_id'])
    assert first['state'] == 'SUCCEEDED', first
    setup.now[0] += timedelta(minutes=10)
    again = setup.cycle.submit([setup.run['run_id'], setup.run['run_id']], setup.input_id)
    assert again['job_id'] == first['job_id']
    assert again['payload']['evaluated_at'] == first['payload']['evaluated_at']
    report = setup.cycle.status(first['job_id'])
    assert report['ledger']['run_ids'] == [setup.run['run_id']]
    assert report['ledger']['runs'][0]['due_horizons'] == [1, 2, 3, 5, 10]
    assert report['ledger']['runs'][0]['counts'] == {'MATURE': 20}
    assert report['ledger']['returns'] is None
    assert report['ledger']['collection'] == 'NOT_REQUESTED'
    assert [e['state'] for e in report['events']] == ['QUEUED', 'RUNNING', 'SUCCEEDED']
    assert all(len(rows(setup, namespace)) == 1 for namespace in NAMESPACES.values())
    assert setup.store.get_document_bytes('strategy_experiment_runs', setup.run['run_id']) == original_bytes
    assert setup.verification['created'] == 3
    assert setup.verification['terminal'] == 4


@pytest.mark.parametrize('clock,status', [
    (datetime(2026, 5, 16, 20, tzinfo=TZ), 'NO_SESSION'),
    (datetime(2026, 5, 18, 14, 59, tzinfo=TZ), 'BEFORE_SESSION_CLOSE'),
])
def test_non_session_and_before_close_never_submit(setup, clock, status):
    setup.now[0] = clock
    plan = setup.cycle.plan([setup.run['run_id']], setup.input_id)
    assert plan['status'] == status and not plan['can_submit']
    with pytest.raises(ValueError, match=status):
        setup.cycle.submit([setup.run['run_id']], setup.input_id)
    assert setup.service.jobs.list(kind=closure.JOB_KIND) == []
    assert setup.store.list_documents(closure.LEDGERS) == []


def test_future_old_or_naive_cutoff_rejected_without_jobs(setup):
    for cutoff, reason in [(setup.now[0] + timedelta(seconds=1), 'FUTURE_EVALUATION_CUTOFF'),
                           (setup.now[0] - timedelta(days=1), 'EVALUATION_NOT_CURRENT_SESSION')]:
        with pytest.raises(ValueError, match=reason):
            setup.cycle.submit([setup.run['run_id']], setup.input_id, evaluated_at=cutoff)
    with pytest.raises(ValueError, match='EXPLICIT_TIMEZONE_REQUIRED'):
        setup.cycle.plan([setup.run['run_id']], setup.input_id, evaluated_at=datetime(2026, 1, 1))
    assert setup.service.jobs.list(kind=closure.JOB_KIND) == []


def test_real_session_horizons_do_not_count_weekends_and_only_due_evaluated(setup):
    first = setup.original.observation_date + timedelta(days=3)
    setup.now[0] = datetime.combine(first, time(20), TZ)
    value = payload(setup.bundle)
    value['observation_date'] = first
    value['sessions'] = [day for day in value['sessions'] if day <= first]
    value['bars'] = [bar for bar in value['bars'] if bar['session'] <= first]
    current_input = setup.service.register_input(MarketInput.model_validate(value))
    plan = setup.cycle.plan([setup.run['run_id']], current_input)
    assert plan['runs'][0]['due_horizons'] == [1]
    assert [r['maturity_state'] for r in plan['runs'][0]['horizons']] == ['DUE', 'PENDING', 'PENDING', 'PENDING', 'PENDING']
    job = finished(setup.service, setup.cycle.submit([setup.run['run_id']], current_input)['job_id'])
    assert job['state'] == 'SUCCEEDED', job
    assert rows(setup, NAMESPACES['outcomes'])[0]['horizons'] == [1]
    assert setup.cycle.status(job['job_id'])['ledger']['status'] == 'COMPLETED_PENDING_MATURITY'


def test_missing_price_and_calendar_stay_unknown_then_new_input_can_revise(setup):
    value = payload(setup.bundle)
    middle = setup.original.observation_date + timedelta(days=4)
    value['bars'] = [bar for bar in value['bars'] if not (bar['instrument_id'] == START and bar['session'] == middle)]
    missing = setup.service.register_input(MarketInput.model_validate(value))
    first = finished(setup.service, setup.cycle.submit([setup.run['run_id']], missing)['job_id'])
    assert first['state'] == 'SUCCEEDED', first
    ledger = setup.cycle.status(first['job_id'])['ledger']
    assert ledger['status'] == 'COMPLETED_WITH_UNKNOWNS'
    outcome = rows(setup, NAMESPACES['outcomes'])[0]
    unknown = [item for item in outcome['items'] if item['status'] == 'UNKNOWN']
    assert unknown and all(value is None for item in unknown for value in item['metrics'].values())
    second = finished(setup.service, setup.cycle.submit([setup.run['run_id']], setup.input_id)['job_id'])
    assert second['state'] == 'SUCCEEDED' and second['job_id'] != first['job_id']
    assert len(rows(setup, NAMESPACES['outcomes'])) == 2
    value = payload(setup.bundle)
    value['sessions'].remove(setup.original.observation_date + timedelta(days=3))
    gap = setup.service.register_input(MarketInput.model_validate(value))
    third = finished(setup.service, setup.cycle.submit([setup.run['run_id']], gap)['job_id'])
    assert third['state'] == 'SUCCEEDED'
    assert setup.cycle.status(third['job_id'])['ledger']['runs'][0]['counts'] == {'UNKNOWN': 20}


def test_rolling_calendar_retains_shared_history_without_requiring_discarded_prefix(setup):
    frozen = setup.store.get_document_bytes('strategy_experiment_runs', setup.run['run_id'])
    value = payload(setup.bundle)
    # The next ten sessions displace the oldest ten, as equal-size daily
    # standalone windows do. Keep the frozen signal and its full future axis.
    value['sessions'] = value['sessions'][-len(setup.original.sessions):]
    assert value['sessions'][0] > setup.original.sessions[0]
    value['bars'] = [bar for bar in value['bars'] if bar['session'] >= value['sessions'][0]]
    current = setup.service.register_input(MarketInput.model_validate(value))
    plan = setup.cycle.plan([setup.run['run_id']], current)
    assert plan['can_submit'] and plan['runs'][0]['due_horizons'] == [1, 2, 3, 5, 10]
    assert all(row['registered_axis_complete'] for row in plan['runs'][0]['horizons'])
    job = finished(setup.service, setup.cycle.submit([setup.run['run_id']], current)['job_id'])
    assert job['state'] == 'SUCCEEDED', job
    ledger = setup.cycle.status(job['job_id'])['ledger']
    assert ledger['status'] == 'COMPLETED'
    assert ledger['runs'][0]['counts'] == {'MATURE': 20}
    assert setup.store.get_document_bytes('strategy_experiment_runs', setup.run['run_id']) == frozen


@pytest.mark.parametrize('fault', ['missing_shared_session', 'inserted_shared_weekend'])
def test_rolling_calendar_conflicting_shared_history_is_rejected(setup, fault):
    value = payload(setup.bundle)
    value['sessions'] = value['sessions'][-len(setup.original.sessions):]
    if fault == 'missing_shared_session':
        value['sessions'].remove(value['sessions'][5])
    else:
        saturday = next(day + timedelta(days=1) for day in value['sessions'][1:]
                        if day.weekday() == 4 and day < setup.original.observation_date)
        assert saturday not in value['sessions']
        value['sessions'] = sorted([*value['sessions'], saturday])
    current = setup.service.register_input(MarketInput.model_validate(value))
    with pytest.raises(ValueError, match='SHORT_HORIZON_SIGNAL_CALENDAR_PREFIX_CONFLICT'):
        setup.cycle.submit([setup.run['run_id']], current)
    assert setup.service.jobs.list(kind=closure.JOB_KIND) == []
    assert setup.store.list_documents(closure.LEDGERS) == []
    assert all(rows(setup, namespace) == [] for namespace in NAMESPACES.values())


def test_rolling_calendar_without_signal_session_remains_unknown(setup):
    value = payload(setup.bundle)
    value['sessions'] = [day for day in value['sessions'] if day > setup.original.observation_date]
    value['bars'] = [bar for bar in value['bars'] if bar['session'] > setup.original.observation_date]
    current = setup.service.register_input(MarketInput.model_validate(value))
    plan = setup.cycle.plan([setup.run['run_id']], current)
    assert all(not row['registered_axis_complete'] for row in plan['runs'][0]['horizons'])
    job = finished(setup.service, setup.cycle.submit([setup.run['run_id']], current)['job_id'])
    assert job['state'] == 'SUCCEEDED', job
    ledger = setup.cycle.status(job['job_id'])['ledger']
    assert ledger['status'] == 'COMPLETED_WITH_UNKNOWNS'
    assert ledger['runs'][0]['counts'] == {'UNKNOWN': 20}
    observed = rows(setup, NAMESPACES['outcomes'])[0]['items']
    assert all('SIGNAL_SESSION_ABSENT_FROM_REGISTERED_CALENDAR' in row['reason_codes'] for row in observed)
    assert all(value is None for row in observed for value in row['metrics'].values())


def test_plan_and_pending_use_authenticated_calendar_without_typed_input_decode(setup, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_UNNECESSARY_FULL_DOCUMENT_DECODE')

    monkeypatch.setattr(setup.service, 'input', forbidden)
    with monkeypatch.context() as context:
        context.setattr(setup.service, 'strategy_experiment', forbidden)
        assert setup.cycle.plan([setup.run['run_id']], setup.input_id)['can_submit']
    assert setup.cycle.pending(setup.input_id)['actionable_total'] == 1
    assert all(event[0] != 'load_input' for event in setup.verification['events'])
    assert all(event[0] != 'retain_input' for event in setup.verification['events'])
    assert setup.verification['borrowed'] == []


def test_worker_authenticates_all_runs_before_single_typed_input_and_reuses_borrowed_runs(setup, monkeypatch):
    second = extra_run(setup, '_TEST_FIXTURE_SECOND')
    actual_input, actual_evaluate, decoded, evaluated = setup.service.input, closure.evaluate, [], []

    def load(identifier, *args, **kwargs):
        assert setup.verification['borrowed'] == [], 'TEST_FIXTURE_RUN_OVERLAPS_TYPED_INPUT_PARSER'
        decoded.append(identifier)
        return actual_input(identifier, *args, **kwargs)

    def evaluate_borrowed(run, *args, **kwargs):
        assert any(run is borrowed for borrowed in setup.verification['borrowed'])
        evaluated.append(run['run_id'])
        return actual_evaluate(run, *args, **kwargs)

    monkeypatch.setattr(setup.service, 'input', load)
    monkeypatch.setattr(setup.service, 'strategy_experiment', lambda *args, **kwargs:
        (_ for _ in ()).throw(AssertionError('TEST_FIXTURE_SECOND_RUN_DECODE_FORBIDDEN')))
    monkeypatch.setattr(closure, 'evaluate', evaluate_borrowed)
    identifiers = sorted([setup.run['run_id'], second['run_id']])
    job = finished(setup.service, setup.cycle.submit(identifiers, setup.input_id)['job_id'])
    assert job['state'] == 'SUCCEEDED', job
    assert decoded == [setup.input_id] and evaluated == identifiers
    events = setup.verification['events']
    loaded = [index for index, event in enumerate(events) if event[0] == 'load_input']
    assert len(loaded) == 1
    worker_session = events[loaded[0]][1]
    authenticated = [event[2] for event in events[:loaded[0]]
                     if event[0] == 'authenticate_run' and event[1] == worker_session]
    assert authenticated == identifiers
    worker_events = [event for event in events[:loaded[0]] if event[1] == worker_session]
    assert [event[0] for event in worker_events if event[0].startswith('authenticate')] == [
        'authenticate_run', 'authenticate_run', 'authenticate_input']
    assert [event[2] for event in worker_events if event[0] == 'retain_input'] == [setup.input_id]
    assert not any(event[0].startswith('authenticate') for event in events[loaded[0] + 1:])
    assert [event[2] for event in events[loaded[0] + 1:] if event[0] == 'borrow_run'] == identifiers
    assert all(event[1] == worker_session for event in events[loaded[0] + 1:] if event[0] == 'borrow_run')


def test_second_run_authentication_failure_never_loads_today_input_or_publishes(setup, monkeypatch):
    second = extra_run(setup, '_TEST_FIXTURE_SECOND')
    actual = setup.bridge.authenticated_bridge_view

    def reject_worker_second(*args, **kwargs):
        if (setup.verification['created'] == 2 and not kwargs.get('borrow_run')
                and kwargs.get('run_id') == second['run_id']):
            raise ValueError('TEST_FIXTURE_SECOND_RUN_AUTHENTICATION_FAILED')
        return actual(*args, **kwargs)

    monkeypatch.setattr(setup.bridge, 'authenticated_bridge_view', reject_worker_second)
    job = finished(setup.service, setup.cycle.submit([setup.run['run_id'], second['run_id']], setup.input_id)['job_id'])
    assert job['state'] == 'FAILED'
    assert all(event[0] != 'load_input' for event in setup.verification['events'])
    assert all(event[0] != 'retain_input' for event in setup.verification['events'])
    assert all(setup.store.list_documents(namespace) == [] for namespace in NAMESPACES.values())
    assert setup.cycle.status(job['job_id'])['ledger'] is None


def test_current_input_authentication_failure_after_all_runs_never_borrows_or_publishes(setup, monkeypatch):
    actual = setup.bridge.authenticated_bridge_view
    errors = capture_worker_errors(setup, monkeypatch)

    def reject_retention(*args, **kwargs):
        if kwargs.get('retain_input'):
            assert any(event[0] == 'authenticate_run' for event in setup.verification['events'])
            raise ValueError('TEST_FIXTURE_CURRENT_ARCHIVE_AUTHENTICATION_FAILED')
        return actual(*args, **kwargs)

    monkeypatch.setattr(setup.bridge, 'authenticated_bridge_view', reject_retention)
    monkeypatch.setattr(closure, 'evaluate', lambda *args, **kwargs: pytest.fail('UNVERIFIED_INPUT_EVALUATED'))
    job = finished(setup.service, setup.cycle.submit([setup.run['run_id']], setup.input_id)['job_id'])
    assert job['state'] == 'FAILED'
    assert errors == ['TEST_FIXTURE_CURRENT_ARCHIVE_AUTHENTICATION_FAILED']
    assert not any(event[0] in {'load_input', 'borrow_run', 'retain_input'}
                   for event in setup.verification['events'])
    assert all(setup.store.list_documents(namespace) == [] for namespace in NAMESPACES.values())
    assert setup.cycle.status(job['job_id'])['ledger'] is None


def test_second_run_borrow_failure_after_input_load_never_evaluates_or_publishes(setup, monkeypatch):
    second = extra_run(setup, '_TEST_FIXTURE_SECOND')
    identifiers = sorted([setup.run['run_id'], second['run_id']])
    actual = setup.bridge.authenticated_bridge_view
    errors = capture_worker_errors(setup, monkeypatch)

    def reject_last(*args, **kwargs):
        if kwargs.get('borrow_run') and kwargs.get('run_id') == identifiers[-1]:
            raise ValueError('TEST_FIXTURE_PINNED_RUN_CHANGED_AFTER_INPUT_LOAD')
        return actual(*args, **kwargs)

    monkeypatch.setattr(setup.bridge, 'authenticated_bridge_view', reject_last)
    monkeypatch.setattr(closure, 'evaluate', lambda *args, **kwargs: pytest.fail('UNVERIFIED_BATCH_EVALUATED'))
    job = finished(setup.service, setup.cycle.submit(identifiers, setup.input_id)['job_id'])
    assert job['state'] == 'FAILED'
    assert errors == ['TEST_FIXTURE_PINNED_RUN_CHANGED_AFTER_INPUT_LOAD']
    assert job['error']['code'] == 'WORKER_FAILED' and job['error']['type'] == 'ValueError'
    assert [event[2] for event in setup.verification['events'] if event[0] == 'load_input'] == [setup.input_id]
    assert len(setup.verification['borrowed']) == 1
    assert all(setup.store.list_documents(namespace) == [] for namespace in NAMESPACES.values())
    assert setup.cycle.status(job['job_id'])['ledger'] is None


@pytest.mark.parametrize('part', ['run_metadata', 'credential', 'run'])
def test_deferred_borrow_must_match_authenticated_metadata_and_binding(setup, monkeypatch, part):
    actual = setup.bridge.authenticated_bridge_view
    before = setup.store.get_document_bytes('strategy_experiment_runs', setup.run['run_id'])
    errors = capture_worker_errors(setup, monkeypatch)

    def changed(*args, **kwargs):
        view = actual(*args, **kwargs)
        if kwargs.get('borrow_run'):
            view[part]['input_id'] = 'market_TEST_FIXTURE_CHANGED_AFTER_METADATA'
        return view

    monkeypatch.setattr(setup.bridge, 'authenticated_bridge_view', changed)
    monkeypatch.setattr(closure, 'evaluate', lambda *args, **kwargs: pytest.fail('CHANGED_BORROW_EVALUATED'))
    job = finished(setup.service, setup.cycle.submit([setup.run['run_id']], setup.input_id)['job_id'])
    assert job['state'] == 'FAILED'
    assert errors == ['SHORT_HORIZON_CLOSURE_BORROWED_RUN_IDENTITY_CHANGED']
    assert job['error']['code'] == 'WORKER_FAILED' and job['error']['type'] == 'ValueError'
    assert all(setup.store.list_documents(namespace) == [] for namespace in NAMESPACES.values())
    assert setup.cycle.status(job['job_id'])['ledger'] is None
    assert setup.store.get_document_bytes('strategy_experiment_runs', setup.run['run_id']) == before


def test_borrowed_run_mutation_after_evaluate_is_rejected_at_terminal(setup, monkeypatch):
    actual = closure.evaluate
    before = setup.store.get_document_bytes('strategy_experiment_runs', setup.run['run_id'])

    def tamper(run, *args, **kwargs):
        products = actual(run, *args, **kwargs)
        run['results'][run['strategy_id']]['items'][0]['comparable_basis'] = 'TEST_FIXTURE_CHANGED'
        return products

    monkeypatch.setattr(closure, 'evaluate', tamper)
    job = finished(setup.service, setup.cycle.submit([setup.run['run_id']], setup.input_id)['job_id'])
    assert job['state'] == 'FAILED'
    assert all(rows(setup, namespace) == [] for namespace in NAMESPACES.values())
    assert setup.cycle.status(job['job_id'])['ledger'] is None
    assert setup.store.get_document_bytes('strategy_experiment_runs', setup.run['run_id']) == before


def test_loaded_input_calendar_cannot_differ_from_authenticated_metadata(setup, monkeypatch):
    actual = setup.bridge.load_verified_bridge_input

    def changed(*args, **kwargs):
        bundle = actual(*args, **kwargs)
        return bundle.model_copy(update={'sessions': bundle.sessions[1:]})

    monkeypatch.setattr(setup.bridge, 'load_verified_bridge_input', changed)
    job = finished(setup.service, setup.cycle.submit([setup.run['run_id']], setup.input_id)['job_id'])
    assert job['state'] == 'FAILED'
    assert all(rows(setup, namespace) == [] for namespace in NAMESPACES.values())
    assert setup.cycle.status(job['job_id'])['ledger'] is None


def test_cancel_after_evaluation_before_commit_publishes_no_products_or_ledger(setup, monkeypatch):
    entered, resume = threading.Event(), threading.Event()
    actual = closure.evaluate

    def paused(*args, **kwargs):
        products = actual(*args, **kwargs)
        entered.set()
        assert resume.wait(5), 'TEST_FIXTURE cancellation synchronization timed out'
        return products

    monkeypatch.setattr(closure, 'evaluate', paused)
    job = setup.cycle.submit([setup.run['run_id']], setup.input_id)
    try:
        assert entered.wait(5)
        setup.cycle.cancel(job['job_id'])
    finally:
        resume.set()
    assert finished(setup.service, job['job_id'])['state'] == 'CANCELLED'
    assert all(rows(setup, namespace) == [] for namespace in NAMESPACES.values())
    assert setup.cycle.status(job['job_id'])['ledger'] is None


@pytest.mark.parametrize('fails', [False, True])
def test_source_revision_keeps_first_request_cutoff_failure_and_products(setup, monkeypatch, fails):
    if fails:
        monkeypatch.setattr(closure, 'evaluate', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('TEST_FIXTURE_FAILURE')))
    first = finished(setup.service, setup.cycle.submit([setup.run['run_id']], setup.input_id)['job_id'])
    assert first['state'] == ('FAILED' if fails else 'SUCCEEDED')
    first_events = setup.service.jobs.events(first['job_id'])
    products = {name: setup.store.list_documents(name) for name in NAMESPACES.values()}
    setup.now[0] += timedelta(minutes=10)
    monkeypatch.setattr(closure, '_policy_identity', lambda: {'version': closure.VERSION, 'source_hash': 'TEST_FIXTURE_REVISED_SOURCE'})
    revised = setup.cycle.plan([setup.run['run_id']], setup.input_id)
    assert revised['job_id'] != first['job_id']
    again = setup.cycle.submit([setup.run['run_id']], setup.input_id)
    assert again == first
    assert again['payload']['evaluated_at'] == first['payload']['evaluated_at']
    assert setup.service.jobs.events(first['job_id']) == first_events
    assert len(setup.service.jobs.list(kind=closure.JOB_KIND)) == 1
    assert {name: setup.store.list_documents(name) for name in NAMESPACES.values()} == products
    if fails:
        assert all(value == [] for value in products.values())
        assert setup.cycle.status(first['job_id'])['ledger'] is None
    assert not hasattr(setup.cycle, 'retry')


def test_overlapping_prior_batch_is_not_silently_reexecuted(setup):
    first = finished(setup.service, setup.cycle.submit([setup.run['run_id']], setup.input_id)['job_id'])
    second = extra_run(setup, '_TEST_FIXTURE_OVERLAPPING')
    before = setup.service.jobs.list()
    with pytest.raises(JobConflict, match='prior overlapping outcome request'):
        setup.cycle.submit([setup.run['run_id'], second['run_id']], setup.input_id)
    assert setup.service.jobs.list() == before
    assert setup.service.jobs.get(first['job_id']) == first


@pytest.mark.parametrize('state', ['FAILED', 'QUEUED', 'RUNNING', 'SUCCEEDED'])
@pytest.mark.parametrize('kind', ['strategy_outcomes', 'strategy_tracking'])
def test_legacy_outcome_job_is_not_reexecuted_by_current_closure(setup, state, kind):
    old = setup.service.jobs.create(kind, {
        'run_id': setup.run['run_id'], 'input_id': setup.input_id,
        'evaluated_at': setup.evaluated.isoformat()}, job_id='TEST_FIXTURE_LEGACY_OUTCOME')
    with setup.store.transaction() as connection:
        connection.execute('UPDATE jobs SET state=? WHERE job_id=?', (state, old['job_id']))
    before = setup.service.jobs.get(old['job_id'])
    events = setup.service.jobs.events(old['job_id'])
    with pytest.raises(JobConflict, match='prior legacy outcome request requires explicit reconciliation'):
        setup.cycle.submit([setup.run['run_id']], setup.input_id)
    assert setup.service.jobs.list() == [before]
    assert setup.service.jobs.events(old['job_id']) == events
    assert setup.store.list_documents(closure.LEDGERS) == []
    assert all(rows(setup, namespace) == [] for namespace in NAMESPACES.values())


def test_untrusted_input_and_run_limit_fail_before_job_creation(setup):
    setup.verification['deny'] = True
    with pytest.raises(ValueError, match='ARCHIVE_NOT_TRUSTED'):
        setup.cycle.submit([setup.run['run_id']], setup.input_id)
    with pytest.raises(ValueError, match='RUN_LIMIT_EXCEEDED_NO_TRUNCATION'):
        setup.cycle.plan(['TEST_FIXTURE_' + str(i) for i in range(6)], setup.input_id)
    assert setup.service.jobs.list(kind=closure.JOB_KIND) == []


def test_constructor_and_plan_work_with_real_readonly_store_and_no_job_writer(setup):
    readonly = object.__new__(CandidateService)
    readonly.store = ReadOnlySQLiteStore(setup.store.path)
    readonly.allow_test_fixture = True
    readonly.jobs = object()  # Construction/plan cannot register a worker.
    before = setup.store.path.read_bytes()
    cycle = closure.ShortHorizonDailyClosure(readonly, clock=lambda: setup.now[0])
    assert cycle.plan([setup.run['run_id']], setup.input_id)['can_submit']
    with pytest.raises(JobConflict, match='active matching job-service lock'):
        cycle.submit([setup.run['run_id']], setup.input_id)
    assert setup.store.path.read_bytes() == before
    assert setup.service.jobs.list(kind=closure.JOB_KIND) == []


def test_atomic_collision_rolls_back_all_other_products(setup, monkeypatch):
    actual = closure.evaluate
    saved_run = setup.service.strategy_experiment(setup.run['run_id'])
    saved_input = setup.service.input(setup.input_id)[1]
    generated = actual(saved_run, saved_input, setup.evaluated)
    bad = deepcopy(generated['tracking'])
    bad['execution_status'] = 'TEST_FIXTURE_COLLISION'
    setup.store.put_document(NAMESPACES['tracking'], bad['revision_id'], bad)
    job = finished(setup.service, setup.cycle.submit([setup.run['run_id']], setup.input_id)['job_id'])
    assert job['state'] == 'FAILED'
    assert rows(setup, NAMESPACES['outcomes']) == []
    assert rows(setup, NAMESPACES['review']) == []
    assert rows(setup, NAMESPACES['tracking']) == [bad]
    assert setup.cycle.status(job['job_id'])['ledger'] is None


def test_stale_registered_input_is_not_ready_for_todays_daily_cycle(setup):
    setup.now[0] += timedelta(days=3)  # Friday fixture -> next exchange Monday.
    plan = setup.cycle.plan([setup.run['run_id']], setup.input_id)
    assert plan['status'] == 'INPUT_NOT_CURRENT_SESSION'
    assert not plan['can_submit']
    assert plan['outcome_input_observation_date'] == str(setup.bundle.observation_date)
    assert plan['outcome_input_latest_session'] == str(max(setup.bundle.sessions))
    with pytest.raises(ValueError, match='INPUT_NOT_CURRENT_SESSION'):
        setup.cycle.submit([setup.run['run_id']], setup.input_id)
    assert setup.service.jobs.list(kind=closure.JOB_KIND) == []


def test_terminal_reverification_failure_rolls_back_batch(setup, monkeypatch):
    actual = closure.evaluate

    def change_after_evaluation(*args, **kwargs):
        products = actual(*args, **kwargs)
        setup.verification['fail_terminal'] = True
        return products

    monkeypatch.setattr(closure, 'evaluate', change_after_evaluation)
    job = finished(setup.service, setup.cycle.submit([setup.run['run_id']], setup.input_id)['job_id'])
    assert job['state'] == 'FAILED'
    assert all(rows(setup, namespace) == [] for namespace in NAMESPACES.values())
    assert setup.cycle.status(job['job_id'])['ledger'] is None


@pytest.mark.parametrize('late_stage', ['all_products', 'ledger'])
def test_raw_change_during_publication_rolls_back_every_run(setup, tmp_path, monkeypatch, late_stage):
    from stockfish_core.yaogu.short_horizon_archive_bridge_v1 import ArchiveVerificationSession

    original_path = tmp_path / 'TEST_FIXTURE_late_publication.raw.json'
    original_path.write_bytes(b'{"coverage_type":"TEST_FIXTURE","rows":[]}')
    parent_session = setup.bridge.ArchiveVerificationSession
    verification_failures = []

    class OriginalFileSession(parent_session):
        def __init__(self, *, checkpoint=lambda: None):
            super().__init__(checkpoint=checkpoint)
            self.originals = ArchiveVerificationSession(checkpoint=checkpoint)
            self.originals.read(original_path)

        def terminal(self):
            try:
                result = super().terminal()
                self.originals.terminal()
                return result
            except ValueError as exc:
                verification_failures.append(str(exc))
                raise

    monkeypatch.setattr(setup.bridge, 'ArchiveVerificationSession', OriginalFileSession)
    second = extra_run(setup, '_TEST_FIXTURE_LATE_RAW')
    run_ids = [setup.run['run_id'], second['run_id']]
    originals = {run_id: setup.store.get_document_bytes('strategy_experiment_runs', run_id) for run_id in run_ids}
    put, written, changed = setup.store._put_document, [], []

    def change_after_writes(connection, namespace, key, payload):
        result = put(connection, namespace, key, payload)
        if namespace in NAMESPACES.values():
            written.append((namespace, key))
        if not changed and ((late_stage == 'all_products' and len(written) == 6)
                            or (late_stage == 'ledger' and namespace == closure.LEDGERS)):
            assert connection.in_transaction and len(written) == 6
            original_path.write_bytes(original_path.read_bytes() + b' TEST_FIXTURE_CHANGED_AFTER_WRITES')
            changed.append(namespace)
        return result

    monkeypatch.setattr(setup.store, '_put_document', change_after_writes)
    job = finished(setup.service, setup.cycle.submit(run_ids, setup.input_id)['job_id'])
    assert changed and job['state'] == 'FAILED', job
    assert job['error']['type'] == 'ValueError'
    assert any('REGISTERED_FILE_CHANGED' in reason or 'FILE_SHA_OR_IDENTITY_MISMATCH' in reason for reason in verification_failures)
    assert all(setup.store.list_documents(namespace) == [] for namespace in NAMESPACES.values())
    assert setup.cycle.status(job['job_id'])['ledger'] is None
    assert all(event['state'] != 'SUCCEEDED' for event in setup.service.jobs.events(job['job_id']))
    assert {run_id: setup.store.get_document_bytes('strategy_experiment_runs', run_id) for run_id in run_ids} == originals


def test_borrowed_run_change_after_ledger_rolls_back_every_run(setup, monkeypatch):
    second = extra_run(setup, '_TEST_FIXTURE_LATE_BORROW')
    run_ids = [setup.run['run_id'], second['run_id']]
    originals = {run_id: setup.store.get_document_bytes('strategy_experiment_runs', run_id) for run_id in run_ids}
    put, changed, verification_failures = setup.store._put_document, [], []
    terminal = setup.bridge.ArchiveVerificationSession.terminal

    def observed_terminal(session):
        try:
            return terminal(session)
        except ValueError as exc:
            verification_failures.append(str(exc))
            raise

    def change_after_ledger(connection, namespace, key, payload):
        result = put(connection, namespace, key, payload)
        if namespace == closure.LEDGERS:
            assert connection.in_transaction
            assert sum(connection.execute('SELECT COUNT(*) FROM documents WHERE namespace=?', (item,)).fetchone()[0]
                       for item in NAMESPACES.values()) == 6
            borrowed = setup.verification['borrowed'][0]
            borrowed['results'][borrowed['strategy_id']]['items'][0]['comparable_basis'] = 'TEST_FIXTURE_LATE_MUTATION'
            changed.append(key)
        return result

    monkeypatch.setattr(setup.store, '_put_document', change_after_ledger)
    monkeypatch.setattr(setup.bridge.ArchiveVerificationSession, 'terminal', observed_terminal)
    job = finished(setup.service, setup.cycle.submit(run_ids, setup.input_id)['job_id'])
    assert changed == [job['job_id']] and job['state'] == 'FAILED', job
    assert job['error']['type'] == 'ValueError'
    assert any('BORROWED_RUN_CHANGED' in reason for reason in verification_failures)
    assert all(setup.store.list_documents(namespace) == [] for namespace in NAMESPACES.values())
    assert setup.cycle.status(job['job_id'])['ledger'] is None
    assert all(event['state'] != 'SUCCEEDED' for event in setup.service.jobs.events(job['job_id']))
    assert {run_id: setup.store.get_document_bytes('strategy_experiment_runs', run_id) for run_id in run_ids} == originals


def test_success_receipt_keeps_prepublication_snapshot_and_checks_after_ledger(setup, monkeypatch):
    terminal = setup.bridge.ArchiveVerificationSession.terminal
    put = setup.store._put_document
    events, write_connection = [], []

    def observed_terminal(session):
        result = terminal(session)
        if write_connection:
            connection = write_connection[0]
            assert connection.in_transaction
            assert connection.execute('SELECT COUNT(*) FROM documents WHERE namespace=?', (closure.LEDGERS,)).fetchone()[0] == 1
            assert connection.execute('SELECT state FROM jobs WHERE kind=?', (closure.JOB_KIND,)).fetchone()[0] == 'RUNNING'
        events.append('terminal')
        return {**result, 'checks_completed': events.count('terminal')}

    def observed_put(connection, namespace, key, payload):
        result = put(connection, namespace, key, payload)
        if namespace == closure.LEDGERS:
            events.append('ledger')
            write_connection.append(connection)
        return result

    monkeypatch.setattr(setup.bridge.ArchiveVerificationSession, 'terminal', observed_terminal)
    monkeypatch.setattr(setup.store, '_put_document', observed_put)
    job = finished(setup.service, setup.cycle.submit([setup.run['run_id']], setup.input_id)['job_id'])
    assert job['state'] == 'SUCCEEDED', job
    assert events == ['terminal', 'terminal', 'ledger', 'terminal']
    report = setup.cycle.status(job['job_id'])
    assert report['ledger']['archive_verification']['checks_completed'] == 2
    assert [event['state'] for event in report['events']] == ['QUEUED', 'RUNNING', 'SUCCEEDED']
    assert all(len(rows(setup, namespace)) == 1 for namespace in NAMESPACES.values())


def extra_run(fixture, suffix, *, empty=False, legal_empty=False):
    run = deepcopy(fixture.run)
    run.pop('content_hash')
    run['run_id'] += suffix
    if empty:
        result = run['results'][run['strategy_id']]
        result['items'] = []
        if legal_empty:
            result.update(status='EMPTY', full_eligible_pool_retained=True, eligible_pool_ids=[],
                          counts={'eligible': 0, 'evaluated': 0})
            run['data_readiness']['SHORT_HORIZON_QUALIFICATION'].update(
                status='COMPLETE', universe_complete=True, eligible=0, evaluated=0)
    run['content_hash'] = digest_json_stream(run)
    fixture.store.put_document('strategy_experiment_runs', run['run_id'], run)
    fixture.bind(run)
    return run


def test_pending_counts_full_pool_and_pages_without_five_run_truncation(setup):
    for number in range(7):
        extra_run(setup, '_TEST_FIXTURE_' + str(number))
    page = setup.cycle.pending(setup.input_id, limit=3)
    assert page['total'] == page['indexed_run_total'] == page['actionable_total'] == 8
    assert len(page['items']) == 3 and page['next_offset'] == 3
    second = setup.cycle.pending(setup.input_id, limit=3, offset=3)
    last = setup.cycle.pending(setup.input_id, limit=3, offset=6)
    ids = [item['run_id'] for item in page['items'] + second['items'] + last['items']]
    assert len(ids) == len(set(ids)) == 8 and last['next_offset'] is None
    assert page['source_reauthentication_required']
    assert all(item['source_reauthentication_required'] for item in page['items'])
    with pytest.raises(ValueError, match='RUN_LIMIT_EXCEEDED_NO_TRUNCATION'):
        setup.cycle.submit(ids, setup.input_id)


def test_only_h10_mature_does_not_close_other_horizons_or_unknown_empty_pool(setup):
    saved = setup.service.input(setup.input_id)[1]
    run = setup.service.strategy_experiment(setup.run['run_id'])
    product = closure.evaluate(run, saved, setup.evaluated - timedelta(minutes=1), horizons=[10])['outcomes']
    setup.store.put_document(NAMESPACES['outcomes'], product['revision_id'], product)
    no_proof = extra_run(setup, '_TEST_FIXTURE_EMPTY_NO_PROOF', empty=True)
    extra_run(setup, '_TEST_FIXTURE_LEGAL_EMPTY', empty=True, legal_empty=True)
    report = setup.cycle.pending(setup.input_id)
    assert report['indexed_run_total'] == 3 and report['total'] == 2
    assert report['stored_complete_total'] == report['legal_empty_pool_total'] == 1
    states = {item['run_id']: item['state'] for item in report['items']}
    assert states[setup.run['run_id']] == 'DUE'
    assert states[no_proof['run_id']] == 'UNKNOWN_EMPTY_POOL_PROOF_REQUIRED'
    job = finished(setup.service, setup.cycle.submit([run['run_id']], setup.input_id)['job_id'])
    assert job['state'] == 'SUCCEEDED', job
    report = setup.cycle.pending(setup.input_id)
    assert report['stored_complete_total'] == 2 and report['total'] == 1


def test_unknown_requires_new_explicit_input_to_be_actionable(setup):
    value = payload(setup.bundle)
    value['bars'] = [bar for bar in value['bars'] if bar['instrument_id'] != START
                    or bar['session'] <= setup.original.observation_date]
    missing = setup.service.register_input(MarketInput.model_validate(value))
    job = finished(setup.service, setup.cycle.submit([setup.run['run_id']], missing)['job_id'])
    assert job['state'] == 'SUCCEEDED'
    current = setup.cycle.pending(missing)
    assert current['items'][0]['state'] == 'UNKNOWN_AWAITING_NEW_INPUT'
    assert current['total'] == 1 and current['actionable_total'] == 0
    revised = setup.cycle.pending(setup.input_id)
    assert revised['total'] == revised['actionable_total'] == 1


def test_second_run_failure_keeps_entire_batch_unpublished(setup, monkeypatch):
    second = extra_run(setup, '_TEST_FIXTURE_SECOND')
    actual, calls = closure.evaluate, []

    def failing_second(*args, **kwargs):
        calls.append(args[0]['run_id'])
        if len(calls) == 2:
            raise RuntimeError('TEST_FIXTURE_SECOND_RUN_FAILURE')
        return actual(*args, **kwargs)

    monkeypatch.setattr(closure, 'evaluate', failing_second)
    job = finished(setup.service, setup.cycle.submit([setup.run['run_id'], second['run_id']], setup.input_id)['job_id'])
    assert job['state'] == 'FAILED' and len(calls) == 2
    assert all(setup.store.list_documents(namespace) == [] for namespace in NAMESPACES.values())
    assert setup.cycle.status(job['job_id'])['ledger'] is None


@pytest.mark.parametrize('state', ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'])
def test_existing_orphan_is_retained_without_recovery_or_execution(setup, state):
    request = setup.cycle.plan([setup.run['run_id']], setup.input_id)['request']
    original = setup.service.jobs.create(closure.JOB_KIND, request, job_id=request['root_job_id'])
    other = setup.service.jobs.create('TEST_FIXTURE_UNRELATED', {'coverage_type': 'TEST_FIXTURE'})
    if state != 'QUEUED':
        with setup.store.transaction() as connection:
            connection.execute('UPDATE jobs SET state=?,owner_id=?,cancel_requested=? WHERE job_id=?',
                (state, 'TEST_FIXTURE_PREVIOUS_PROCESS', int(state == 'CANCEL_REQUESTED'), original['job_id']))
            setup.service.jobs._event(connection, original['job_id'], state, 0, {'coverage_type': 'TEST_FIXTURE'})
    before = setup.service.jobs.get(original['job_id'])
    previous_events = setup.service.jobs.events(original['job_id'])
    other_events = setup.service.jobs.events(other['job_id'])
    again = setup.cycle.submit([setup.run['run_id']], setup.input_id)
    assert again == before
    assert setup.service.jobs.events(original['job_id']) == previous_events
    assert setup.service.jobs.get(other['job_id']) == other
    assert setup.service.jobs.events(other['job_id']) == other_events
    assert all(rows(setup, namespace) == [] for namespace in NAMESPACES.values())


def oversized_request(setup):
    request = setup.cycle.plan([setup.run['run_id']], setup.input_id)['request']
    request['run_ids'] = ['TEST_FIXTURE_FORGED_' + str(i) for i in range(6)]
    identity = {key: request[key] for key in ('policy', 'run_ids', 'input_id', 'evaluation_session', 'bindings')}
    request['root_job_id'] = 'short_daily_' + digest(identity)
    return request


def test_recomputed_request_hash_cannot_bypass_worker_batch_budget(setup):
    request = oversized_request(setup)
    job = setup.service.jobs.submit(closure.JOB_KIND, request, setup.cycle._worker, job_id=request['root_job_id'])
    assert finished(setup.service, job['job_id'])['state'] == 'FAILED'
    assert all(setup.store.list_documents(namespace) == [] for namespace in NAMESPACES.values())
    assert setup.cycle.status(job['job_id'])['ledger'] is None


def test_current_process_active_job_is_not_started_twice(setup, monkeypatch):
    entered, resume = threading.Event(), threading.Event()
    actual = closure.evaluate

    def paused(*args, **kwargs):
        entered.set()
        assert resume.wait(5)
        return actual(*args, **kwargs)

    monkeypatch.setattr(closure, 'evaluate', paused)
    job = setup.cycle.submit([setup.run['run_id']], setup.input_id)
    try:
        assert entered.wait(5)
        before = setup.service.jobs.get(job['job_id'])
        events = setup.service.jobs.events(job['job_id'])
        assert setup.cycle.submit([setup.run['run_id']], setup.input_id) == before
        assert setup.service.jobs.get(job['job_id']) == before
        assert setup.service.jobs.events(job['job_id']) == events
    finally:
        resume.set()
    assert finished(setup.service, job['job_id'])['state'] == 'SUCCEEDED'


@pytest.mark.parametrize('fault', ['different_store', 'closed', 'no_job_service'])
def test_submission_requires_real_live_matching_owner(setup, tmp_path, fault):
    # Authenticate a real plan before swapping owners. A new empty DB would
    # otherwise fail the separate read-only input check before reaching writes.
    plan = setup.cycle.plan([setup.run['run_id']], setup.input_id)
    request = plan['request']
    jobs = setup.service.jobs
    original = jobs.create(closure.JOB_KIND, request, job_id=request['root_job_id'])
    events = jobs.events(original['job_id'])
    try:
        if fault == 'different_store':
            setup.service.store = SQLiteStore(tmp_path / 'TEST_FIXTURE_OTHER.sqlite3')
        elif fault == 'closed':
            jobs.shutdown(cancel_pending=False)
        else:
            setup.service.jobs = SimpleNamespace(get=jobs.get)
        with pytest.raises(JobConflict, match='active matching job-service lock'):
            setup.cycle._submit_plan(plan)
    finally:
        setup.service.store, setup.service.jobs = setup.store, jobs
    assert jobs.get(original['job_id']) == original
    assert jobs.events(original['job_id']) == events


@pytest.mark.parametrize('legal', [False, True])
def test_worker_empty_pool_requires_explicit_qualification_proof(setup, legal):
    run = extra_run(setup, '_TEST_FIXTURE_EMPTY_WORKER', empty=True, legal_empty=legal)
    job = finished(setup.service, setup.cycle.submit([run['run_id']], setup.input_id)['job_id'])
    assert job['state'] == 'SUCCEEDED', job
    ledger = setup.cycle.status(job['job_id'])['ledger']
    assert ledger['status'] == ('COMPLETED' if legal else 'COMPLETED_WITH_UNKNOWNS')
    assert ledger['runs'][0]['status'] == ('LEGAL_EMPTY_POOL' if legal else 'UNKNOWN_EMPTY_POOL_PROOF_REQUIRED')
    assert ledger['runs'][0]['revision_id'] is None and ledger['returns'] is None
    assert all(setup.store.list_documents(namespace) == [] for namespace in NAMESPACES.values())
