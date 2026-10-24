"""Explicit TEST_FIXTURE consumer tests; no network or real saved data.

Only the archive-authentication boundary is mocked. SQLite, job locking, genuine
verification sessions, canonical typed spool/BLOB writes, registration seals,
the projection and the outcome evaluator remain real implementations.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
import hashlib
import json
import socket
from types import SimpleNamespace
import weakref

import pytest

from stockfish_core.common import canonical_json, utc_now
from stockfish_core.jobs import JobService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.candidate_service import CandidateService
from stockfish_core.yaogu.contracts import DailyBar, MarketInput, digest
from stockfish_core.yaogu import dated_identity_outcome_closure_v1 as consumer
from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
from stockfish_core.yaogu import short_horizon_daily_closure_v1 as closure
from stockfish_core.yaogu import sealed_result_local_copy_v1 as local
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from stockfish_core.yaogu.short_horizon_outcomes_v1 import NAMESPACES
from tests.yaogu.test_dated_identity_composite_input_v1 import fixture as identity_fixture, clocks, CAPTURE
from tests.yaogu.test_short_horizon_daily_closure_v1 import finished


def _rewrite_fixture_document(store, namespace, document_id, value):
    """Deliberate corruption, scoped to this test's isolated SQLite database."""
    assert store.path.name.startswith('TEST_FIXTURE_')
    raw = canonical_json(value)
    with store.transaction() as connection:
        # Exercise a payload corrupted outside the normal immutable API. The
        # trigger is restored in the same isolated test transaction.
        connection.execute('DROP TRIGGER documents_no_update')
        connection.execute('UPDATE documents SET payload=?,content_hash=? WHERE namespace=? AND document_id=?',
            (raw, hashlib.sha256(raw.encode()).hexdigest(), namespace, document_id))
        connection.execute("CREATE TRIGGER documents_no_update BEFORE UPDATE ON documents BEGIN SELECT RAISE(ABORT, 'immutable documents'); END")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('TEST_FIXTURE_NETWORK_FORBIDDEN'))
    # Admission-calendar behavior has its own whole-file regression suite.
    # Keep this explicit fixture's historical business day usable after 2026,
    # while retaining actual file ctimes and the unmodified seal time checks.
    test_clock = lambda: max(utc_now(), datetime(2026, 10, 24, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(consumer, 'utc_now', test_clock)
    monkeypatch.setattr(closure.LateCompletedSessionClosure, '_latest_completed',
        staticmethod(lambda instant: date(2026, 10, 23)))
    store = SQLiteStore(tmp_path / 'TEST_FIXTURE_DATED.sqlite3')
    jobs = JobService(store, max_workers=1)
    service = object.__new__(CandidateService)
    service.store, service.jobs, service.allow_test_fixture = store, jobs, True
    service.root = tmp_path / 'TEST_FIXTURE_STATE'
    service.root.mkdir()
    base, identity = identity_fixture()
    days = tuple(date(2026, 10, day) for day in (15, 16, 19, 20, 21, 22, 23))
    source = base.receipts[1].model_copy(update={'receipt_id': 'TEST_FIXTURE_HISTORY', 'dataset': 'history',
        'parameters': {}, 'business_sessions': days, **clocks(CAPTURE + timedelta(days=7))})
    bars = tuple(DailyBar(instrument_id='000001.SZ', session=day, open='10', high='12', low='9',
        close='10' if day == days[0] else '11', comparable_open='10', comparable_high='12',
        comparable_low='9', comparable_close='10' if day == days[0] else '11',
        comparable_basis='TEST_FIXTURE_FIXED_BASIS', quality='VALID', tick_size='.01',
        source_refs=(source.receipt_id,), **clocks(CAPTURE + timedelta(days=7))) for day in days)
    base = base.model_copy(update={'sessions': days, 'bars': bars, 'receipts': base.receipts + (source,)})
    identity = identity.model_copy(update={'sessions': (days[0], days[1])})
    signal = base.model_copy(update={'observation_date': days[0], 'sessions': (days[0],), 'bars': bars[:1]})
    bundles = {}
    run_by_input = {}
    credentials = {}
    for index, bundle in enumerate((signal, identity, base)):
        # Bridge imports retain the original inline document. The ordinary
        # admin register_input compacts history, which is outside this consumer.
        input_id = 'market_' + digest(bundle)
        store.put_document('candidate_inputs', input_id, bundle.model_dump(mode='json'))
        store.put_document('candidate_input_indexes', input_id, dict(input_id=input_id,
            observation_date=str(bundle.observation_date), coverage_type='TEST_FIXTURE',
            max_session=str(max(bundle.sessions))))
        run = dict(run_id='TEST_FIXTURE_R2_' + str(index), strategy_id='SHORT_HORIZON_STRENGTH_R2',
            execution_profile='SHORT_HORIZON_RESEARCH_SINGLE', coverage_type='TEST_FIXTURE',
            input_id=input_id, input_hash=input_id.removeprefix('market_'),
            observation_date=str(bundle.observation_date), decision_cutoff='2026-10-23T20:00:00+00:00',
            generated_at='2026-10-23T20:00:00+00:00', results={'SHORT_HORIZON_STRENGTH_R2': {
                'items': [dict(instrument_id='000001.SZ', research_selected=True, formal_focus=False,
                    primary_stage='TEST_FIXTURE_STAGE', decision_status='WATCH', matched_arms=['TEST_FIXTURE_ARM'],
                    exact_features={'close_comparable': {'numerator': 10, 'denominator': 1}},
                    comparable_basis='TEST_FIXTURE_FIXED_BASIS')], 'counts': {'eligible': 1, 'evaluated': 1}}})
        run['content_hash'] = bridge.digest_json_stream(run)
        store.put_document('strategy_experiment_runs', run['run_id'], run)
        credential = dict(bridge_id='TEST_FIXTURE_CERT_' + str(index), run_id=run['run_id'],
            signal_content_hash=run['content_hash'], input_id=input_id,
            input_hash=input_id.removeprefix('market_'), archive_final_sha256=hex(index+1)[2:] * 64)
        credentials[input_id], run_by_input[input_id], bundles[input_id] = credential, run, bundle
    signal_id, identity_id, base_id = list(bundles)

    # The RESULT mapping is metadata at the mocked archive boundary. These
    # tests do not assert OS protection or grant a real RESULT capability.
    copy_root = tmp_path / 'TEST_FIXTURE_MOCK_RESULT_ROOT'
    copy_root.mkdir()
    original = tmp_path / 'TEST_FIXTURE_MOCK_ORIGINAL.json'
    copied = copy_root / 'TEST_FIXTURE_MOCK_COPY.json'
    original.write_text('{"TEST_FIXTURE":true}')
    copied.write_bytes(original.read_bytes())
    old_ref, copied_ref = workflow._ref(original), workflow._ref(copied)
    copied_ref['fp']['st_flags'] |= local.IMMUTABLE
    manifest_path = tmp_path / 'TEST_FIXTURE_LOCAL_COPY_MANIFEST.json'
    manifest_path.write_text(json.dumps(dict(schema_version=local.PROFILE,
        copy_root_reference=local.root_reference(copy_root), archives=[dict(
            archive_final_sha256='a'*64, results=[dict(original_reference=old_ref, current_reference=copied_ref)])])))
    manifest_ref = workflow._ref(manifest_path)
    protocol = bridge.configure_local_copy_source_protocol(service, manifest_ref)
    for credential in credentials.values():
        credential['source_protocol'] = deepcopy(protocol)
        binding = dict(schema_version=bridge.VERSION, **credential)
        store.put_document(bridge.CERTIFICATES, credential['bridge_id'], binding)
        store.put_document(bridge.INPUT_BINDINGS, credential['input_id'], binding)
        store.put_document(bridge.RUN_BINDINGS, credential['run_id'], binding)
    raw_guard = tmp_path / 'TEST_FIXTURE_AUTHENTICATED_RAW.txt'
    raw_guard.write_text('TEST_FIXTURE_AUTHENTICATED_ARCHIVE_BOUNDARY')
    raw_ref = workflow._ref(raw_guard)
    authenticated = []

    def authenticated_view(svc, *, run_id=None, input_id=None, session, borrow_run=False,
                           retain_input=False, include_archive_seal=False, borrow_if_empty=False):
        assert svc is service, 'TEST_FIXTURE_WRONG_SERVICE_AT_ARCHIVE_BOUNDARY'
        session.check()
        input_id = input_id or next(key for key, run in run_by_input.items() if run['run_id'] == run_id)
        credential, saved_run = credentials[input_id], store.get_document('strategy_experiment_runs', credentials[input_id]['run_id'])
        namespace, document_id = (bridge.RUN_BINDINGS, run_id) if run_id else (bridge.INPUT_BINDINGS, input_id)
        key = bridge._verified_view_key(svc, namespace, document_id, session)
        proof = session._verified_views.get(key)
        if proof is None:
            session.read(raw_ref['path'], raw_ref)
            authenticated.append((id(session), namespace, document_id))
            proof = dict(source_protocol_context=session.source_protocol(), session_ref=weakref.ref(session),
                store=store, store_identity=bridge._view_store_identity(svc), namespace=namespace,
                document_id=document_id, credential=deepcopy(credential),
                binding=store.get_document(namespace, document_id),
                certificate=store.get_document(bridge.CERTIFICATES, credential['bridge_id']),
                input_hash=credential['input_hash'], run_hash=bridge.digest_json_stream(saved_run), source_rows=[])
            session._verified_views[key] = proof
        bridge._check_verified_view(svc, proof, session)
        if retain_input and proof.get('retained_input') is None:
            proof['retained_input'] = MarketInput.model_validate(store.get_document('candidate_inputs', input_id))
        metadata = {name: deepcopy(saved_run[name]) for name in ('run_id', 'strategy_id', 'execution_profile',
            'coverage_type', 'content_hash', 'input_id', 'observation_date', 'decision_cutoff', 'generated_at')}
        original_bundle = bundles[input_id]
        view = dict(credential=deepcopy(credential), run_metadata=metadata, input_metadata=dict(
            input_id=input_id, input_hash=credential['input_hash'], observation_date=str(original_bundle.observation_date),
            sessions=list(map(str, original_bundle.sessions)), coverage_type='TEST_FIXTURE'))
        if include_archive_seal:
            view['archive_sealed_at'] = '2026-10-23T21:00:00+00:00'
        if borrow_run:
            view['run'] = saved_run
            session._borrowed_values[id(saved_run)] = (saved_run, proof['run_hash'], False)
            bridge._record_borrowed_identity(session, proof, saved_run, typed=False)
        return view

    monkeypatch.setattr(bridge, 'authenticated_bridge_view', authenticated_view)
    before = {(ns, key): store.get_document_bytes(ns, key) for ns, key in
        [('candidate_inputs', key) for key in bundles] + [('strategy_experiment_runs', run['run_id']) for run in run_by_input.values()]}
    now = [test_clock() + timedelta(minutes=1)]
    cycle = consumer.DatedIdentityLateClosure(service, local_copy_manifest_ref=manifest_ref, clock=lambda: now[0])
    try:
        yield SimpleNamespace(store=store, service=service, jobs=jobs, manifest_ref=manifest_ref,
            base_id=base_id, identity_id=identity_id, signal_id=signal_id, run=run_by_input[signal_id],
            base=base, identity=identity, cycle=cycle, now=now, before=before, authenticated=authenticated,
            raw_guard=raw_guard, tmp_path=tmp_path, clock=test_clock)
    finally:
        jobs.shutdown()


def register(f):
    value = consumer.register_input(f.service, base_input_id=f.base_id,
        identity_input_ids=[f.identity_id], local_copy_manifest_ref=f.manifest_ref)
    f.registered, f.cutoff = value, f.clock()
    f.now[0] = f.cutoff + timedelta(minutes=1)
    return value


def plan(f):
    return f.cycle.plan_late([f.run['run_id']], f.registered['input_id'],
        business_session=date(2026, 10, 23), evaluated_at=f.cutoff)


def no_products(f):
    assert f.store.list_documents(f.cycle.ledgers) == []
    assert all(f.store.list_documents(namespace) == [] for namespace in NAMESPACES.values())


def test_real_typed_registration_and_numerical_h1_three_products_are_idempotent(setup):
    f = setup
    registered = register(f)
    assert registered['input_id'] != f.base_id
    saved = f.store.get_document('candidate_inputs', registered['input_id'])
    assert digest(MarketInput.model_validate(saved)) == registered['input_hash']
    assert saved['bars'] == f.base.model_dump(mode='json')['bars']
    assert f.store.get_document(consumer.SEALS, registered['input_id']) == registered['registration_seal']
    planned = plan(f)
    assert planned['can_submit'], planned
    first = finished(f.service, f.cycle._submit_plan(planned)['job_id'])
    assert first['state'] == 'SUCCEEDED', first
    status = f.cycle.status(first['job_id'])
    products = {kind: f.store.documents_for_run(namespace, f.run['run_id']) for kind, namespace in NAMESPACES.items()}
    assert all(len(values) == 1 for values in products.values())
    assert len({values[0]['revision_id'] for values in products.values()}) == 1
    h1 = next(row for row in products['outcomes'][0]['items'] if row['horizon'] == 1)
    assert h1['status'] == 'MATURE' and h1['metrics']['close_reference_change'] == float(Fraction(11, 10) - 1)
    assert h1['frozen_reference'] == {'comparable_close': {'numerator': 10, 'denominator': 1}, 'comparable_basis': 'TEST_FIXTURE_FIXED_BASIS'}
    assert status['ledger']['input_id'] == registered['input_id']
    assert status['ledger']['historical_visibility_upgraded'] is False
    assert status['ledger']['derived_input_version'] == consumer.VERSION
    assert f.cycle._submit_plan(planned) == first
    assert products == {kind: f.store.documents_for_run(namespace, f.run['run_id']) for kind, namespace in NAMESPACES.items()}
    assert all(f.store.get_document_bytes(ns, key) == value for (ns, key), value in f.before.items())
    assert len({row[0] for row in f.authenticated}) >= 3


def test_plain_saved_descriptor_and_input_without_registration_seal_are_rejected(setup, monkeypatch):
    f = setup
    put = f.store._put_document
    def fail_seal(connection, namespace, document_id, value):
        if namespace == consumer.SEALS:
            raise ValueError('TEST_FIXTURE_SEAL_PUBLICATION_FAILED')
        return put(connection, namespace, document_id, value)
    monkeypatch.setattr(f.store, '_put_document', fail_seal)
    with pytest.raises(ValueError, match='TEST_FIXTURE_SEAL_PUBLICATION_FAILED'):
        register(f)
    descriptors = f.store.list_documents(consumer.REGISTRY)
    assert len(descriptors) == 1 and f.store.list_documents(consumer.SEALS) == []
    f.registered, f.cutoff = dict(input_id=descriptors[0]['payload']['input_id']), f.clock()
    assert f.store.get_document('candidate_inputs', f.registered['input_id']) is not None
    with pytest.raises(ValueError, match='COMPLETED_REGISTRATION_SEAL_REQUIRED'):
        plan(f)
    no_products(f)


def test_private_loader_rejects_different_invocation_and_actual_store(setup):
    f = setup
    register(f)
    session = bridge.ArchiveVerificationSession(local_copy_manifest_ref=f.manifest_ref)
    f.cycle._authenticated([f.run['run_id']], f.registered['input_id'], session, sealed_cutoff=f.cutoff)
    assert f.cycle._load_outcome_input(f.registered['input_id'], session).observation_date == date(2026, 10, 23)
    another = bridge.ArchiveVerificationSession(local_copy_manifest_ref=f.manifest_ref)
    with pytest.raises(ValueError, match='PRIVATE_INVOCATION_PROOF_REQUIRED'):
        f.cycle._load_outcome_input(f.registered['input_id'], another)
    same_path_other_store = SQLiteStore(f.store.path)
    other = SimpleNamespace(store=same_path_other_store)
    assert consumer._proof_key(other, session, f.registered['input_id']) != consumer._proof_key(f.service, session, f.registered['input_id'])


@pytest.mark.parametrize('kind', ['descriptor_time', 'descriptor_lineage', 'input_payload'])
def test_self_consistent_database_corruption_is_not_source_authority(setup, kind):
    f = setup
    registered = register(f)
    if kind == 'input_payload':
        value = f.store.get_document('candidate_inputs', registered['input_id'])
        value['bars'][0]['close'] = '999'
        _rewrite_fixture_document(f.store, 'candidate_inputs', registered['input_id'], value)
    else:
        value = deepcopy(registered['descriptor'])
        if kind == 'descriptor_time':
            value['derived_at'] = '2026-10-23T21:01:00+00:00'
            value['registered_at'] = value['derived_at']
        else:
            value['projection']['replacement_count'] += 1
        _rewrite_fixture_document(f.store, consumer.REGISTRY, registered['input_id'], value)
    with pytest.raises(ValueError, match='REGISTRATION_SEAL_BINDING_CHANGED|BRIDGED_DATABASE_PARENT_CHANGED'):
        plan(f)
    no_products(f)


def test_parent_descriptor_and_source_bytes_stay_guarded_after_authentication(setup):
    f = setup
    register(f)
    session = bridge.ArchiveVerificationSession(local_copy_manifest_ref=f.manifest_ref)
    f.cycle._authenticated([f.run['run_id']], f.registered['input_id'], session, sealed_cutoff=f.cutoff)
    f.raw_guard.write_text('TEST_FIXTURE_LATE_ORIGINAL_CHANGE')
    with pytest.raises(ValueError, match='REGISTERED_FILE_CHANGED|ORIGINAL_PHYSICAL_IDENTITY_CHANGED'):
        f.cycle._terminal(session)


def test_late_nested_borrowed_derived_mutation_is_rejected(setup):
    f = setup
    register(f)
    session = bridge.ArchiveVerificationSession(local_copy_manifest_ref=f.manifest_ref)
    f.cycle._authenticated([f.run['run_id']], f.registered['input_id'], session, sealed_cutoff=f.cutoff)
    value = f.cycle._load_outcome_input(f.registered['input_id'], session)
    value.receipts[0].parameters['TEST_FIXTURE_LATE_MUTATION'] = True
    with pytest.raises(ValueError, match='BORROWED_DERIVED_INPUT_CHANGED'):
        f.cycle._load_outcome_input(f.registered['input_id'], session)
    with pytest.raises(ValueError, match='BORROWED_VERIFIED_CONTENT_CHANGED'):
        f.cycle._terminal(session)


def test_terminal_failure_rolls_back_derived_input_registration(setup, monkeypatch):
    f = setup
    before = f.store.list_document_metadata('candidate_inputs')
    actual = bridge.ArchiveVerificationSession.terminal
    calls = []
    def terminal(session):
        result = actual(session)
        calls.append(1)
        if len(calls) == 2:
            raise ValueError('TEST_FIXTURE_REGISTER_TERMINAL_FAILURE')
        return result
    monkeypatch.setattr(bridge.ArchiveVerificationSession, 'terminal', terminal)
    with pytest.raises(ValueError, match='TEST_FIXTURE_REGISTER_TERMINAL_FAILURE'):
        register(f)
    after = f.store.list_document_metadata('candidate_inputs')
    assert before == after
    assert f.store.list_documents(consumer.REGISTRY) == f.store.list_documents(consumer.SEALS) == []


def test_final_worker_terminal_failure_rolls_back_all_products_and_ledger(setup, monkeypatch):
    f = setup
    register(f)
    planned = plan(f)
    actual = f.store._put_document
    fail = [False]
    def put(connection, namespace, document_id, value):
        actual(connection, namespace, document_id, value)
        if namespace == f.cycle.ledgers:
            fail[0] = True
    terminal = f.cycle._terminal
    def guarded(session):
        result = terminal(session)
        if fail[0]:
            raise ValueError('TEST_FIXTURE_FINAL_ATOMIC_TERMINAL_FAILURE')
        return result
    monkeypatch.setattr(f.store, '_put_document', put)
    monkeypatch.setattr(f.cycle, '_terminal', guarded)
    result = finished(f.service, f.cycle._submit_plan(planned)['job_id'])
    assert result['state'] == 'FAILED', result
    no_products(f)
    assert f.store.get_document(consumer.REGISTRY, f.registered['input_id']) == f.registered['descriptor']


def test_typed_writer_refuses_wrong_hash_without_partial_document(setup):
    f = setup
    session = bridge.ArchiveVerificationSession()
    with pytest.raises(ValueError, match='VERIFIED_CAPABILITY_CONTENT_CHANGED'):
        with f.store.transaction() as connection:
            bridge._put_archived_document(connection, 'TEST_FIXTURE_TYPED_WRITER', 'TEST_FIXTURE_BAD', 'f'*64,
                session, typed_document=f.base, temporary_dir=f.tmp_path)
    assert f.store.list_documents('TEST_FIXTURE_TYPED_WRITER') == []
    assert list(f.tmp_path.glob('stockfish-verified-bridge-*')) == []


def test_plan_reconstructs_full_derived_input_instead_of_trusting_ready_metadata(setup, monkeypatch):
    f = setup
    register(f)
    actual = consumer.project_dated_identity
    def changed(*args, **kwargs):
        value, manifest = actual(*args, **kwargs)
        return value.model_copy(update={'coverage_type': 'RECONSTRUCTED_HISTORICAL'}), manifest
    monkeypatch.setattr(consumer, 'project_dated_identity', changed)
    with pytest.raises(ValueError, match='FULL_DERIVED_INPUT_CHANGED'):
        plan(f)
    no_products(f)


def test_cutoff_before_registration_file_pin_is_rejected(setup):
    f = setup
    registered = register(f)
    ref = registered['registration_seal']['seal_reference']
    before_pin = datetime.fromtimestamp(ref['fp']['st_ctime_ns'] / 1_000_000_000, timezone.utc) - timedelta(microseconds=1)
    session = bridge.ArchiveVerificationSession(local_copy_manifest_ref=f.manifest_ref)
    with pytest.raises(ValueError, match='REGISTRATION_PHYSICAL_PIN_AFTER_CUTOFF'):
        consumer._check_seal(f.service, registered['descriptor'], session, before_pin)
    no_products(f)


@pytest.mark.parametrize('kind', ['completion_clock', 'source_clock', 'aliased_path'])
def test_seal_cannot_claim_completion_or_new_source_before_actual_cutoff(setup, kind):
    f = setup
    registered = register(f)
    seal_document = deepcopy(registered['registration_seal'])
    path = f.service.root / 'dated_identity_inputs' / registered['input_id'] / 'registration-seal.json'
    value = json.loads(path.read_text())
    if kind == 'completion_clock':
        value['registration_completed_at'] = (f.cutoff + timedelta(days=10)).isoformat()
    elif kind == 'source_clock':
        for ref in value['source_references'].values():
            ref['fp']['st_ctime_ns'] = consumer._cutoff_ns(f.cutoff + timedelta(days=10))
    else:
        path = f.tmp_path / 'TEST_FIXTURE_ALIASED_SEAL.json'
    path.write_text(json.dumps(value, sort_keys=True))
    seal_document['seal_reference'] = workflow._ref(path)
    _rewrite_fixture_document(f.store, consumer.SEALS, registered['input_id'], seal_document)
    # The rewritten test seal is now a physically new observation; only the
    # explicit field under test remains beyond this fresh, actual cutoff.
    f.cutoff = f.clock()
    expected = {'completion_clock': 'COMPLETED_REGISTRATION_AFTER_CUTOFF',
        'source_clock': 'DERIVATION_SOURCE_PIN_AFTER_CUTOFF', 'aliased_path': 'OWNED_REGISTRATION_SEAL_PATH_REQUIRED'}[kind]
    with pytest.raises(ValueError, match=expected):
        plan(f)
    no_products(f)


def test_seal_same_byte_inode_replacement_remains_rejected_after_authentication(setup):
    f = setup
    registered = register(f)
    session = bridge.ArchiveVerificationSession(local_copy_manifest_ref=f.manifest_ref)
    f.cycle._authenticated([f.run['run_id']], registered['input_id'], session, sealed_cutoff=f.cutoff)
    path = f.service.root / 'dated_identity_inputs' / registered['input_id'] / 'registration-seal.json'
    replacement = path.with_name('TEST_FIXTURE_REPLACEMENT.json')
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
    with pytest.raises(ValueError, match='ORIGINAL_PHYSICAL_IDENTITY_CHANGED|REGISTRATION_PHYSICAL_PIN_AFTER_CUTOFF'):
        f.cycle._terminal(session)


def test_registration_requires_active_matching_real_job_service_lock(setup):
    f = setup
    f.service.jobs = SimpleNamespace(store=f.store)
    with pytest.raises(ValueError, match='ACTIVE_MATCHING_JOB_LOCK_REQUIRED'):
        register(f)
    assert f.store.list_documents(consumer.REGISTRY) == []
