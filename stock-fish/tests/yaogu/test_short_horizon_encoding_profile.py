"""TEST_FIXTURE execution-profile wiring; strategy and output values stay fixed.

The original daily/outcome tests remain unchanged. Archive authentication in
the imported daily fixture is explicitly mocked; the final three cases use
complete TEST_FIXTURE archives and the actual verifier. Neither group is
real-market or investment-performance acceptance.
"""
from copy import deepcopy
from datetime import date, datetime, time

import pytest

from stockfish_core.jobs import JobCancelled
from stockfish_core.storage import IntegrityError, SQLiteStore, StorageError
from stockfish_core.yaogu import history_artifacts as history
from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
from stockfish_core.yaogu import short_horizon_daily_closure_v1 as daily
from stockfish_core.yaogu import short_horizon_outcomes_v1 as outcomes
from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu.strategy_experiments import run_short_horizon_strategy
from tests.support.short_horizon_strength_fixture import START, TZ, payload, strength_bundle
from tests.yaogu.test_short_horizon_daily_closure_v1 import setup as daily_setup, finished
from tests.yaogu.test_short_horizon_archive_bridge_v1 import service as archive_service
from tests.yaogu.test_short_horizon_archive_bridge_v1 import _offline_fixture_document
from tests.yaogu.test_short_horizon_outcomes_v1 import future_input


@pytest.fixture
def evidence():
    original, cutoff = strength_bundle()
    run = run_short_horizon_strategy(original, cutoff=cutoff, strategy_id='SHORT_HORIZON_STRENGTH_R2')
    run['content_hash'] = history.digest_json_stream(run)
    bundle, evaluated = future_input(original)
    return run, original, bundle, evaluated


@pytest.mark.parametrize('case', ['mature', 'pending', 'missing_price', 'unknown_security', 'basis'])
def test_profiles_preserve_every_output_field_and_parent(evidence, case):
    run, original, bundle, evaluated = evidence
    value = payload(bundle)
    future = next(row for row in value['bars'] if row['instrument_id'] == START
                  and row['session'] > original.observation_date)
    if case == 'pending':
        evaluated = datetime.combine(future['session'], time(14, 59), TZ)
    elif case == 'missing_price':
        value['bars'].remove(future)
    elif case == 'unknown_security':
        value['universe']['securities'][0]['suspended'] = None
    elif case == 'basis':
        future['comparable_basis'] = 'TEST_FIXTURE_OTHER_BASIS'
    bundle = MarketInput.model_validate(value)
    frozen_run, frozen_input = deepcopy(run), history.digest_market_input(bundle)
    default = outcomes.evaluate(run, bundle, evaluated)
    explicit_default = outcomes.evaluate(run, bundle, evaluated, encoding_profile=bridge.DEFAULT_ENCODING_PROFILE)
    fast = outcomes.evaluate(run, bundle, evaluated, encoding_profile=bridge.CLOSURE_ENCODING_PROFILE)
    assert default == explicit_default == fast
    assert len(fast['outcomes']['items']) == 4 * len(outcomes.HORIZONS)
    assert 'encoding_profile' not in fast['outcomes']
    assert fast['outcomes']['outcome_input_id'] == 'market_' + frozen_input
    assert run == frozen_run and history.digest_market_input(bundle) == frozen_input
    if case == 'pending':
        assert set(fast['outcomes']['counts']) == {'PENDING'}
    elif case != 'mature':
        assert fast['outcomes']['counts'].get('UNKNOWN', 0) > 0
        assert all(value is None for row in fast['outcomes']['items'] if row['status'] == 'UNKNOWN'
                   for value in row['metrics'].values())


def test_none_profile_keeps_original_inline_hash_call_signature(evidence, monkeypatch):
    run, _, bundle, evaluated = evidence
    expected = outcomes.evaluate(run, bundle, evaluated)
    calls = []
    def old_signature(value, *, checkpoint):
        calls.append(value)
        checkpoint()
        return history.digest_market_input(value)
    monkeypatch.setattr(outcomes, 'digest_market_input', old_signature)
    assert outcomes.evaluate(run, bundle, evaluated, encoding_profile=None) == expected
    assert calls == [bundle]


def test_explicit_profile_reaches_inline_hash_and_preserves_cancel_exception(evidence, monkeypatch):
    run, _, bundle, evaluated = evidence
    original, calls = outcomes.digest_market_input, []
    failure = JobCancelled('TEST_FIXTURE_FAST_INPUT_CANCELLED')
    hashing = False
    def observed(value, *, checkpoint, encoding_profile):
        nonlocal hashing
        calls.append(encoding_profile)
        hashing = True
        try:
            return original(value, checkpoint=checkpoint, encoding_profile=encoding_profile)
        finally:
            hashing = False
    def checkpoint():
        if hashing:
            raise failure
    monkeypatch.setattr(outcomes, 'digest_market_input', observed)
    with pytest.raises(JobCancelled) as caught:
        outcomes.evaluate(run, bundle, evaluated, encoding_profile=bridge.CLOSURE_ENCODING_PROFILE,
                          checkpoint=checkpoint)
    assert caught.value is failure and calls == [bridge.CLOSURE_ENCODING_PROFILE]


@pytest.mark.parametrize('profile', [False, True, 1024, '', 'TYPED_ROWS_512_V1', [], {}])
def test_invalid_profile_rejected_before_inline_or_history_path(evidence, profile, monkeypatch):
    run, _, bundle, evaluated = evidence
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_INVALID_PROFILE_REACHED_HASH')
    monkeypatch.setattr(outcomes, 'digest_market_input', forbidden)
    monkeypatch.setattr(outcomes, '_reference_digest_market_input', forbidden)
    with pytest.raises(ValueError, match='R2_ARCHIVE_BRIDGE_ENCODING_PROFILE_INVALID'):
        outcomes.evaluate(run, bundle, evaluated, encoding_profile=profile)
    # A history owner cannot turn an invalid execution profile into permission
    # to call the reference path. This is a unit seam, not a valid owner.
    object.__setattr__(bundle, '_history_store', object())
    with pytest.raises(ValueError, match='R2_ARCHIVE_BRIDGE_ENCODING_PROFILE_INVALID'):
        outcomes._outcome_input_hash(bundle, checkpoint=lambda: None, encoding_profile=profile)


def test_history_fast_profile_retains_reference_owner_and_full_products(evidence, tmp_path, monkeypatch):
    run, _, bundle, evaluated = evidence
    store = SQLiteStore(tmp_path / 'TEST_FIXTURE_encoding_profile_history.sqlite3')
    compact = history.compact_input(bundle, store, recent_sessions=6)
    assert compact.history_artifact_ref and compact._history_store is not None
    expected = outcomes.evaluate(run, compact, evaluated)
    calls, reference = [], outcomes._reference_digest_market_input
    def original_history(value):
        calls.append(value)
        return reference(value)
    def unexpected_inline(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_HISTORY_MUST_KEEP_REFERENCE_HASH')
    monkeypatch.setattr(outcomes, '_reference_digest_market_input', original_history)
    monkeypatch.setattr(outcomes, 'digest_market_input', unexpected_inline)
    assert outcomes.evaluate(run, compact, evaluated, encoding_profile=bridge.CLOSURE_ENCODING_PROFILE) == expected
    assert calls == [compact]
    unbound = MarketInput.model_validate(compact.model_dump(mode='python'))
    assert unbound._history_store is None
    with pytest.raises(IntegrityError, match='HISTORY_ARTIFACT_STORE_NOT_BOUND'):
        outcomes.evaluate(run, unbound, evaluated, encoding_profile=bridge.CLOSURE_ENCODING_PROFILE)


def test_real_policy_declares_only_worker_fast_profile():
    policy = daily._policy_identity()
    assert policy['worker_encoding_profile'] == bridge.CLOSURE_ENCODING_PROFILE
    assert policy['version'] == daily.VERSION
    assert policy['outcome_policy'] == outcomes.VERSION
    assert {'short_horizon_archive_bridge_v1.py', 'short_horizon_outcomes_v1.py'} <= set(policy['source_hashes'])


@pytest.fixture
def fast_daily(daily_setup, monkeypatch):
    setup = daily_setup
    policy = {**daily._policy_identity(), 'worker_encoding_profile': bridge.CLOSURE_ENCODING_PROFILE}
    monkeypatch.setattr(daily, '_policy_identity', lambda: deepcopy(policy))
    base_session = setup.bridge.ArchiveVerificationSession
    setup.profiles = []
    class ProfileSession(base_session):
        def __init__(self, *, checkpoint=lambda: None, encoding_profile=bridge.DEFAULT_ENCODING_PROFILE):
            self.encoding_profile = bridge.validate_encoding_profile(encoding_profile)
            setup.profiles.append(self.encoding_profile)
            super().__init__(checkpoint=checkpoint)
    monkeypatch.setattr(setup.bridge, 'ArchiveVerificationSession', ProfileSession)
    setup.fast_policy = policy
    return setup


def test_daily_plan_default_worker_fast_evaluation_and_atomic_products(fast_daily, monkeypatch):
    setup = fast_daily
    evaluated, original = [], daily.evaluate
    def observed(*args, **kwargs):
        evaluated.append(kwargs.get('encoding_profile'))
        return original(*args, **kwargs)
    monkeypatch.setattr(daily, 'evaluate', observed)
    plan = setup.cycle.plan([setup.run['run_id']], setup.input_id)
    assert plan['request']['policy']['worker_encoding_profile'] == bridge.CLOSURE_ENCODING_PROFILE
    assert setup.profiles == [bridge.DEFAULT_ENCODING_PROFILE]
    job = finished(setup.service, setup.cycle.submit([setup.run['run_id']], setup.input_id)['job_id'])
    assert job['state'] == 'SUCCEEDED', job
    assert setup.profiles == [bridge.DEFAULT_ENCODING_PROFILE, bridge.DEFAULT_ENCODING_PROFILE,
                              bridge.CLOSURE_ENCODING_PROFILE]
    assert evaluated == [bridge.CLOSURE_ENCODING_PROFILE]
    actual = setup.cycle.status(job['job_id'])
    assert actual['ledger']['policy'] == setup.fast_policy
    # register_input legitimately compacts this fixture into content-addressed
    # history. Compare the actual registered input that the worker consumes.
    _, registered = setup.service.input(setup.input_id)
    expected = outcomes.evaluate(setup.run, registered,
        datetime.fromisoformat(plan['request']['evaluated_at']))
    for kind, namespace in outcomes.NAMESPACES.items():
        saved = setup.store.documents_for_run(namespace, setup.run['run_id'])
        assert saved == [expected[kind]]


@pytest.mark.parametrize('tamper', ['remove', 'default', 'unknown'])
def test_profile_request_tamper_rejected_before_auth_even_with_rehashed_job_id(fast_daily, monkeypatch, tamper):
    setup = fast_daily
    plan = setup.cycle.plan([setup.run['run_id']], setup.input_id)
    request = deepcopy(plan['request'])
    if tamper == 'remove':
        request['policy'].pop('worker_encoding_profile')
    else:
        request['policy']['worker_encoding_profile'] = (bridge.DEFAULT_ENCODING_PROFILE if tamper == 'default'
                                                        else 'TEST_FIXTURE_UNKNOWN_PROFILE')
    request['root_job_id'] = setup.cycle.job_prefix + digest(setup.cycle._request_identity(request))
    before = setup.verification['created']
    errors, original = [], setup.cycle._worker
    def observed(ctx):
        try:
            return original(ctx)
        except ValueError as exc:
            errors.append(str(exc))
            raise
    setup.service.jobs.register(daily.JOB_KIND, observed)
    job = finished(setup.service, setup.service.jobs.submit(daily.JOB_KIND, request,
        job_id=request['root_job_id'])['job_id'])
    assert job['state'] == 'FAILED' and job['error']['type'] == 'ValueError'
    assert errors == ['SHORT_HORIZON_CLOSURE_POLICY_CHANGED_AFTER_SUBMISSION']
    assert setup.verification['created'] == before
    assert setup.store.list_documents(daily.LEDGERS) == []
    assert all(setup.store.list_documents(namespace) == [] for namespace in outcomes.NAMESPACES.values())


def test_fast_worker_terminal_failure_keeps_entire_batch_unpublished(fast_daily, monkeypatch):
    setup = fast_daily
    terminal = setup.bridge.ArchiveVerificationSession.terminal
    calls = []
    def changed(session):
        result = terminal(session)
        if session.encoding_profile == bridge.CLOSURE_ENCODING_PROFILE:
            calls.append(session.encoding_profile)
            if len(calls) == 2:
                raise ValueError('TEST_FIXTURE_FAST_POST_WRITE_SOURCE_CHANGED')
        return result
    monkeypatch.setattr(setup.bridge.ArchiveVerificationSession, 'terminal', changed)
    job = finished(setup.service, setup.cycle.submit([setup.run['run_id']], setup.input_id)['job_id'])
    assert job['state'] == 'FAILED' and calls == [bridge.CLOSURE_ENCODING_PROFILE] * 2
    assert setup.store.list_documents(daily.LEDGERS) == []
    assert all(setup.store.list_documents(namespace) == [] for namespace in outcomes.NAMESPACES.values())


def _shared_input_pair(setup, monkeypatch, hint='valid'):
    """Explicit mock authority for ordering tests, never an archive capability."""
    current = deepcopy(setup.run)
    current.update(run_id='strategy_experiment_000_TEST_FIXTURE_current', input_id=setup.input_id,
        observation_date=str(setup.bundle.observation_date), decision_cutoff=setup.evaluated.isoformat(),
        generated_at=setup.evaluated.isoformat())
    current['content_hash'] = history.digest_json_stream({k: v for k, v in current.items() if k != 'content_hash'})
    setup.store.put_document('strategy_experiment_runs', current['run_id'], current)
    setup.bind(current)
    certificate = setup.bridge.require_verified_bridge(setup.service, current['run_id'], session=None)
    binding = {'schema_version': bridge.VERSION, **certificate}
    if hint == 'wrong':
        binding['run_id'] = setup.run['run_id']
    elif hint == 'unrequested':
        binding['run_id'] = 'TEST_FIXTURE_NOT_REQUESTED'
    if hint != 'missing':
        setup.store.put_document(bridge.INPUT_BINDINGS, setup.input_id, binding)
    original, calls = setup.bridge.authenticated_bridge_view, []
    def view(svc, **kwargs):
        calls.append(('run', kwargs['run_id']) if kwargs.get('run_id') else ('input', kwargs['input_id']))
        result = original(svc, **kwargs)
        if kwargs.get('input_id') == setup.input_id:
            result['credential'] = deepcopy(certificate)
        return result
    monkeypatch.setattr(setup.bridge, 'authenticated_bridge_view', view)
    return current, sorted([setup.run['run_id'], current['run_id']]), calls


def test_shared_input_hint_only_changes_auth_order_and_restores_canonical_results(fast_daily, monkeypatch):
    setup = fast_daily
    current, requested, calls = _shared_input_pair(setup, monkeypatch)
    session = setup.bridge.ArchiveVerificationSession(encoding_profile=bridge.CLOSURE_ENCODING_PROFILE)
    runs, calendar, bindings = setup.cycle._authenticated(requested, setup.input_id, session, retain_input=True)
    assert calls == [('run', setup.run['run_id']), ('input', setup.input_id), ('run', current['run_id'])]
    assert [row['run_id'] for row in runs] == requested
    assert [row['run_id'] for row in bindings['runs']] == requested
    assert calendar.observation_date == setup.bundle.observation_date
    assert len([e for e in setup.verification['events'] if e[0] == 'retain_input']) == 1


@pytest.mark.parametrize('hint', ['missing', 'unrequested'])
def test_missing_or_unrequested_hint_falls_back_without_omitting_any_authentication(fast_daily, monkeypatch, hint):
    setup = fast_daily
    _, requested, calls = _shared_input_pair(setup, monkeypatch, hint)
    session = setup.bridge.ArchiveVerificationSession(encoding_profile=bridge.CLOSURE_ENCODING_PROFILE)
    runs, _, bindings = setup.cycle._authenticated(requested, setup.input_id, session, retain_input=True)
    assert calls == [('run', identifier) for identifier in requested] + [('input', setup.input_id)]
    assert [r['run_id'] for r in runs] == [r['run_id'] for r in bindings['runs']] == requested


@pytest.mark.parametrize('fault', ['wrong_hint', 'late_binding', 'deferred_run_rejected'])
def test_hint_never_replaces_deferred_run_authority(fast_daily, monkeypatch, fault):
    setup = fast_daily
    current, requested, calls = _shared_input_pair(setup, monkeypatch, 'wrong' if fault == 'wrong_hint' else 'valid')
    if fault == 'late_binding':
        original, reads = setup.store.get_document, []
        def changed(namespace, identifier):
            result = original(namespace, identifier)
            if namespace == bridge.INPUT_BINDINGS and identifier == setup.input_id:
                reads.append(identifier)
                if len(reads) > 1:
                    result['bridge_id'] = 'TEST_FIXTURE_CHANGED_BINDING'
            return result
        monkeypatch.setattr(setup.store, 'get_document', changed)
    elif fault == 'deferred_run_rejected':
        original = setup.bridge.authenticated_bridge_view
        def rejected(svc, **kwargs):
            if kwargs.get('run_id') == current['run_id']:
                raise ValueError('TEST_FIXTURE_DEFERRED_RUN_NOT_AUTHENTICATED')
            return original(svc, **kwargs)
        monkeypatch.setattr(setup.bridge, 'authenticated_bridge_view', rejected)
    session = setup.bridge.ArchiveVerificationSession(encoding_profile=bridge.CLOSURE_ENCODING_PROFILE)
    message = ('TEST_FIXTURE_DEFERRED_RUN_NOT_AUTHENTICATED' if fault == 'deferred_run_rejected'
               else 'SHORT_HORIZON_CLOSURE_INPUT_RUN_HINT_CHANGED')
    with pytest.raises(ValueError, match=message):
        setup.cycle._authenticated(requested, setup.input_id, session, retain_input=True)
    assert ('input', setup.input_id) in calls
    assert setup.service.jobs.list() == []
    assert all(setup.store.list_documents(namespace) == [] for namespace in outcomes.NAMESPACES.values())


def test_single_old_run_retains_original_auth_order_even_when_hint_exists(fast_daily, monkeypatch):
    setup = fast_daily
    _, _, calls = _shared_input_pair(setup, monkeypatch)
    session = setup.bridge.ArchiveVerificationSession(encoding_profile=bridge.CLOSURE_ENCODING_PROFILE)
    runs, _, _ = setup.cycle._authenticated([setup.run['run_id']], setup.input_id, session, retain_input=True)
    assert calls == [('run', setup.run['run_id']), ('input', setup.input_id)]
    assert [run['run_id'] for run in runs] == [setup.run['run_id']]


@pytest.mark.parametrize('late_change', ['none', 'parent', 'source'])
def test_real_fixture_pair_reuses_two_full_normalizations_and_keeps_late_guards(
        archive_service, tmp_path, monkeypatch, late_change):
    """Full original TEST_FIXTURE trios and actual verifier, not mock authority."""
    import hashlib
    import socket
    from pathlib import Path
    from stockfish_core.common import canonical_json
    from stockfish_core.yaogu import provider_normalization, strategy_experiments
    from tests.support.short_horizon_archive_fixture import fixture_archive
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('TEST_FIXTURE_NETWORK'))
    archives = [fixture_archive(tmp_path / ('TEST_FIXTURE_PROFILE_' + day), observation_date=day,
        seen_at=day + 'T12:00:00+00:00', generated_at=day + 'T13:00:00+00:00')
        for day in ('2026-09-01', '2026-09-15')]
    certificates = [bridge.commit_bridge(archive_service, bridge.authenticate_archive(value['job'],
        expected_final_sha256=value['final_sha256'], allow_test_fixture=True)) for value in archives]
    monkeypatch.setattr(strategy_experiments, 'run_short_horizon_strategy',
        lambda *a, **k: pytest.fail('TEST_FIXTURE_SELECTOR_MUST_NOT_RERUN'))
    normalized, normalize = [], provider_normalization.normalize_capture_with_report
    def counted(*args, **kwargs):
        result = normalize(*args, **kwargs)
        normalized.append(result[0].observation_date)
        return result
    monkeypatch.setattr(provider_normalization, 'normalize_capture_with_report', counted)
    input_id = certificates[1]['input_id']
    original_view, input_views = bridge.authenticated_bridge_view, []
    def changed(service, **kwargs):
        result = original_view(service, **kwargs)
        if kwargs.get('input_id') == input_id:
            input_views.append(input_id)
            if late_change == 'parent':
                document = service.store.get_document('candidate_inputs', input_id)
                document['TEST_FIXTURE_LATE_UNAUTHORIZED_FIELD'] = True
                raw = canonical_json(document)
                with pytest.raises(StorageError, match='SQLite transaction failed'):
                    with service.store.transaction() as connection:
                        connection.execute('UPDATE documents SET payload=?,content_hash=? WHERE namespace=? AND document_id=?',
                            (raw, hashlib.sha256(raw.encode()).hexdigest(), 'candidate_inputs', input_id))
                assert 'TEST_FIXTURE_LATE_UNAUTHORIZED_FIELD' not in service.store.get_document(
                    'candidate_inputs', input_id)
                # Simulate offline corruption after proving normal writes are
                # rejected. This existing fixture helper restores the trigger
                # in the same isolated transaction; production is untouched.
                _offline_fixture_document(service, 'candidate_inputs', input_id, document)
            elif late_change == 'source':
                path = Path(archives[1]['items'][0]['raw']['path'])
                path.write_bytes(path.read_bytes() + b'\n')
        return result
    monkeypatch.setattr(bridge, 'authenticated_bridge_view', changed)
    closure = daily.ShortHorizonDailyClosure(archive_service)
    requested = sorted(cert['run_id'] for cert in certificates)
    session = bridge.ArchiveVerificationSession(encoding_profile=bridge.CLOSURE_ENCODING_PROFILE)
    if late_change == 'parent':
        with pytest.raises(ValueError, match='BRIDGED_DATABASE_PARENT_CHANGED'):
            closure._authenticated(requested, input_id, session, retain_input=True)
    else:
        runs, _, _ = closure._authenticated(requested, input_id, session, retain_input=True)
        assert [run['run_id'] for run in runs] == requested
        if late_change == 'source':
            with pytest.raises(ValueError, match='R2_ARCHIVE_BRIDGE_FILE_SHA_OR_IDENTITY_MISMATCH'):
                session.terminal()
        else:
            audit = session.terminal()
            assert audit['encoding_profile'] == bridge.CLOSURE_ENCODING_PROFILE
            assert audit['batch_rows'] == 1024
    assert len(normalized) == 2 and sorted(normalized) == [date.fromisoformat(
        value['run']['observation_date']) for value in archives]
    assert input_views == [input_id]
    assert archive_service.jobs.list() == []
    assert all(archive_service.store.list_documents(namespace) == [] for namespace in outcomes.NAMESPACES.values())
