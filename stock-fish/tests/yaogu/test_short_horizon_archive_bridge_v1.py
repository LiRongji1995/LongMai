"""Isolated TEST_FIXTURE originals and DB; no real market or service access."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path
import pickle
import socket
import sys
from unittest.mock import patch

import pytest

from stockfish_core.storage import SQLiteStore, canonical_json
from stockfish_core.jobs import JobService
from stockfish_core.yaogu.candidate_service import CandidateService
from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from tests.support.short_horizon_archive_fixture import fixture_archive, reseal


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('TEST_FIXTURE_NETWORK'))
    return fixture_archive(tmp_path / 'TEST_FIXTURE')


def authenticate(archive, **kwargs):
    return bridge.authenticate_archive(archive['job'], expected_final_sha256=archive['final_sha256'],
        allow_test_fixture=True, **kwargs)


@pytest.fixture
def service(tmp_path):
    root = tmp_path / 'TEST_FIXTURE-state'
    root.mkdir()
    store = SQLiteStore(root / 'fixture.db')
    jobs = JobService(store, recover=False)
    service = object.__new__(CandidateService)
    service.store, service.jobs, service.root, service.allow_test_fixture = store, jobs, root, True
    yield service
    jobs.shutdown()


def test_native_archive_authenticates_without_selector_or_owner_restoration(archive, monkeypatch):
    from stockfish_core.yaogu import strategy_experiments, premarket_native_core_v1
    monkeypatch.setattr(strategy_experiments, 'run_short_horizon_strategy',
        lambda *a, **k: pytest.fail('SELECTOR_MUST_NOT_RUN'))
    monkeypatch.setattr(premarket_native_core_v1, 'run_core', lambda *a, **k: pytest.fail('CORE_MUST_NOT_RUN'))
    cap = authenticate(archive)
    audit = cap.audit()
    assert audit['status'] == 'VERIFIED' and audit['authenticated_source_count'] == len(archive['items'])
    assert audit['run_id'] == archive['run']['run_id']
    assert audit['reexecuted_selector'] is audit['historical_owner_restored'] is audit['native_core_owner_restored'] is False
    assert audit['verification_budget']['terminal_rereads'] == 1
    with pytest.raises(TypeError, match='NOT_SERIALIZABLE'):
        pickle.dumps(cap)
    with pytest.raises(ValueError, match='AUTHENTICATION_REQUIRED'):
        bridge.ArchiveBridgeCapability()


@pytest.mark.parametrize('value', [None, False, True, 0, -1, 2**255 - 1,
    0.0, -0.0, 5e-324, 1e308, math.nextafter(1.0, 2.0),
    {'z': '中文😀', 'a': '\"\\\b\f\n\r\t\x00\x1f\u2028\u2029'},
    {'TEST_FIXTURE': [Decimal('1.0010'), datetime(2026, 10, 16, tzinfo=timezone.utc), (1, 2)]},
    {1: 'one', 2: 'two'}, {None: 'null'}, {True: 'yes'}])
def test_canonical_c_encoder_matches_frozen_bytes_and_hash(value):
    from stockfish_core.yaogu.history_artifacts import digest_json_stream as reference
    expected = ''.join(json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(',', ':'),
        default=str, allow_nan=False).iterencode(value)).encode('utf-8')
    chunks = list(bridge._canonical_chunks(value))
    assert b''.join(chunks) == expected
    assert all(len(chunk) <= 1024**2 for chunk in chunks)
    assert bridge.digest_json_stream(value) == reference(value) == hashlib.sha256(expected).hexdigest()


def test_canonical_c_encoder_large_unicode_shared_values_and_mutation():
    from stockfish_core.yaogu.history_artifacts import digest_json_stream as reference
    shared = {'TEST_FIXTURE': ('😀中文\\\"\x00\n' * 30000)}
    value = {'z': [shared, shared], 'a': {'nested': -0.0}}
    chunks = list(bridge._canonical_chunks(value))
    assert len(chunks) > 1 and max(map(len, chunks)) <= 1024**2
    expected = canonical_json(value).encode('utf-8')
    assert b''.join(chunks) == expected
    before = bridge.digest_json_stream(value)
    assert before == reference(value)
    shared['TEST_FIXTURE'] = 'changed nested borrowed value'
    assert bridge.digest_json_stream(value) == reference(value) != before


def test_canonical_c_encoder_retains_reference_rejections_and_integer_limit():
    from stockfish_core.yaogu.history_artifacts import digest_json_stream as reference
    cyclic = []
    cyclic.append(cyclic)
    mapping = {}
    mapping['TEST_FIXTURE'] = mapping
    values = [float('nan'), float('inf'), -float('inf'), {'v': float('inf')},
        '\ud800', {'long': 'x' * 300000 + '\udfff'}, {'a': 1, 2: 3}, cyclic, mapping]
    before = sys.get_int_max_str_digits()
    if before:
        values.append(10**(before + 1))
    for value in values:
        with pytest.raises(Exception) as expected:
            reference(value)
        with pytest.raises(type(expected.value)):
            bridge.digest_json_stream(value)
    assert sys.get_int_max_str_digits() == before


def test_canonical_c_encoder_checks_after_c_allocation_before_yield(monkeypatch):
    original = json.JSONEncoder
    allocated = False
    failure = KeyboardInterrupt('TEST_FIXTURE_POST_C_ALLOCATION_CANCELLED')
    class ObservedEncoder(original):
        def iterencode(self, value, _one_shot=False):
            nonlocal allocated
            assert _one_shot is True
            result = super().iterencode(value, _one_shot=_one_shot)
            allocated = True
            return result
    def checkpoint():
        if allocated:
            raise failure
    monkeypatch.setattr(bridge.json, 'JSONEncoder', ObservedEncoder)
    with pytest.raises(KeyboardInterrupt) as caught:
        next(bridge._canonical_chunks({'TEST_FIXTURE': '😀' * 1000}, checkpoint=checkpoint))
    assert caught.value is failure


def test_canonical_c_encoder_partial_spool_cancel_rolls_back_and_closes(service, tmp_path, monkeypatch):
    document = {'TEST_FIXTURE': '😀' * 600000}
    expected = hashlib.sha256(canonical_json(document).encode()).hexdigest()
    handles = []
    original = bridge.tempfile.TemporaryFile
    failure = KeyboardInterrupt('TEST_FIXTURE_PARTIAL_CANONICAL_SPOOL_CANCELLED')
    def temporary(*args, **kwargs):
        handle = original(*args, **kwargs)
        handles.append(handle)
        return handle
    def checkpoint():
        if handles and handles[0].tell() > 0:
            raise failure
    monkeypatch.setattr(bridge.tempfile, 'TemporaryFile', temporary)
    session = bridge.ArchiveVerificationSession(checkpoint=checkpoint)
    with pytest.raises(KeyboardInterrupt) as caught:
        with service.store.transaction() as connection:
            service.store._put_document(connection, 'TEST_FIXTURE_spool', 'prior', {'TEST_FIXTURE': True})
            bridge._put_archived_document(connection, 'TEST_FIXTURE_spool', 'next', expected, session,
                document=document, temporary_dir=tmp_path)
    assert caught.value is failure and handles and all(handle.closed for handle in handles)
    assert service.store.get_document('TEST_FIXTURE_spool', 'prior') is None
    assert service.store.get_document('TEST_FIXTURE_spool', 'next') is None
    assert not list(tmp_path.glob('stockfish-verified-bridge-*'))


def test_atomic_import_retains_original_run_input_and_is_idempotent(archive, service):
    cap = authenticate(archive)
    raw_run = deepcopy(archive['run'])
    raw_input = json.loads(gzip.decompress(Path(archive['artifacts']['input.json.gz']['path']).read_bytes()))
    before = {ref['path']: Path(ref['path']).read_bytes() for ref in archive['artifacts'].values()}
    cert = bridge.commit_bridge(service, cap)
    cert2 = bridge.commit_bridge(service, authenticate(archive))
    assert cert2 == cert
    assert service.store.get_document('strategy_experiment_runs', cert['run_id']) == raw_run
    assert service.store.get_document('candidate_inputs', cert['input_id']) == raw_input
    assert service.store.get_document_bytes('candidate_inputs', cert['input_id']) == canonical_json(raw_input).encode()
    assert raw_run['source_database_written'] is False and cert['source_database_written'] is True
    assert cert['imported_at'] >= cert['verified_at']
    assert before == {path: Path(path).read_bytes() for path in before}
    shared = bridge.ArchiveVerificationSession()
    run_credential = bridge.require_verified_bridge(service, cert['run_id'], session=shared)
    read1 = shared.read_bytes
    input_credential = bridge.require_verified_bridge_input(service, cert['input_id'], session=shared)
    assert input_credential == run_credential
    assert shared.read_bytes == read1
    assert shared.terminal()['terminal_rereads'] == 2
    assert service.strategy_experiment(cert['run_id']) == raw_run


@pytest.mark.parametrize('field,value', [('status', 'FAILED'), ('core_verified', False),
    ('source_terminal_binding_verified', False), ('implementation_epoch_verified', False),
    ('source_capability_restored', True), ('external_worker_reaped', False)])
def test_rejects_unsealed_or_failed_job(archive, field, value):
    reseal(archive, final={field: value})
    with pytest.raises(ValueError, match='SEALED_SUCCESSFUL'):
        authenticate(archive)


def test_explicit_hash_and_fixture_scope_required(archive):
    with pytest.raises(ValueError, match='FILE_SHA'):
        bridge.authenticate_archive(archive['job'], expected_final_sha256='0' * 64, allow_test_fixture=True)
    with pytest.raises(ValueError, match='EXPLICIT_FIXTURE'):
        bridge.authenticate_archive(archive['job'], expected_final_sha256=archive['final_sha256'])


@pytest.mark.parametrize('role', ['raw', 'receipt', 'result'])
def test_rejects_changed_physical_original(archive, role):
    path = Path(archive['items'][0][role]['path'])
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='PHYSICAL_IDENTITY'):
        authenticate(archive)


def test_missing_old_source_requires_explicit_exact_byte_copy(archive, tmp_path):
    key = 'stockfish_core/yaogu/candidate_service.py'
    path = archive['source'] / key
    original = path.read_bytes()
    copy = tmp_path / 'TEST_FIXTURE-old-source.py'
    copy.write_bytes(original)
    path.write_bytes(original + b'\n# TEST_FIXTURE changed implementation\n')
    with pytest.raises(ValueError, match='PHYSICAL_IDENTITY'):
        authenticate(archive)
    cap = authenticate(archive, source_overrides={key: str(copy)})
    proof = cap.audit()['source_proofs'][key]
    assert proof['proof_mode'] == 'EXPLICIT_COPY_CONTENT_PROOF_NOT_ORIGINAL_PHYSICAL_OWNER'
    assert proof['current_reference']['fp'] != proof['original_reference']['fp']
    copy.write_bytes(b'TEST_FIXTURE_WRONG_SOURCE')
    with pytest.raises(ValueError, match='FILE_SHA'):
        authenticate(archive, source_overrides={key: str(copy)})


def test_unknown_source_override_rejected(archive, tmp_path):
    with pytest.raises(ValueError, match='UNREGISTERED_SOURCE_OVERRIDE'):
        authenticate(archive, source_overrides={'../../unknown.py': str(tmp_path)})


def test_export_tamper_rejected_even_with_resealed_archive(archive):
    name = 'all.csv'
    path = Path(archive['artifacts'][name]['path'])
    path.write_bytes(path.read_bytes().replace(b'600001.SH', b'600009.SH'))
    newref = workflow._ref(path)
    archive['artifacts'][name] = newref
    manifest = json.loads(Path(archive['artifacts']['manifest.json']['path']).read_bytes())
    manifest['artifacts'][name] = newref
    manifest['official_exports'][name]['reference'] = newref
    reseal(archive, manifest=manifest)
    with pytest.raises(ValueError, match='OFFICIAL_CSV_MISMATCH'):
        authenticate(archive)


def test_locator_summary_cannot_omit_original_receipt(archive):
    path = archive['job'] / 'capture-summary.json'
    document = json.loads(path.read_bytes())
    document['core_records'].pop()
    path.write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(ValueError, match='EXACT_CORE_RECEIPT_SET'):
        authenticate(archive)


def test_terminal_rejects_changed_file_and_does_not_commit(archive, service):
    cap = authenticate(archive)
    path = Path(archive['items'][0]['raw']['path'])
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='PHYSICAL_IDENTITY'):
        bridge.commit_bridge(service, cap)
    assert service.store.get_document('candidate_inputs', archive['run']['input_id']) is None
    assert service.store.get_document(bridge.RUN_BINDINGS, archive['run']['run_id']) is None


def test_plain_json_certificate_cannot_commit_or_restore_owner(archive, service):
    cap = authenticate(archive)
    document = json.loads(json.dumps(cap.audit()))
    with pytest.raises(ValueError, match='GENUINE_FRESH'):
        bridge.commit_bridge(service, document)
    with pytest.raises(ValueError, match='VERIFIED_BRIDGE_BINDING_REQUIRED'):
        bridge.require_verified_bridge(service, archive['run']['run_id'])


def test_import_rollback_on_projection_failure(archive, service, monkeypatch):
    cap = authenticate(archive)
    def fail(*args):
        raise RuntimeError('TEST_FIXTURE_ATOMIC_ROLLBACK')
    monkeypatch.setattr(service, '_write_strategy_projection', fail)
    with pytest.raises(RuntimeError, match='ATOMIC_ROLLBACK'):
        bridge.commit_bridge(service, cap)
    assert service.store.get_document('candidate_inputs', archive['run']['input_id']) is None
    assert service.store.get_document('strategy_experiment_runs', archive['run']['run_id']) is None


def test_archived_document_spool_preserves_canonical_unicode_and_text(service, tmp_path):
    document = {'TEST_FIXTURE': '中文 € 😀', 'z': [{'次序': 1}], 'a': None}
    path = tmp_path / 'TEST_FIXTURE-pretty.json.gz'
    path.write_bytes(gzip.compress(json.dumps(document, ensure_ascii=False, indent=4).encode()))
    expected = canonical_json(document).encode()
    digest = hashlib.sha256(expected).hexdigest()
    session = bridge.ArchiveVerificationSession()
    with service.store.transaction() as connection:
        bridge._put_archived_document(connection, 'TEST_FIXTURE_spool', 'one', digest, session,
            reference=workflow._ref(path), temporary_dir=tmp_path)
        assert connection.execute('SELECT typeof(payload) FROM documents WHERE namespace=?',
            ('TEST_FIXTURE_spool',)).fetchone()[0] == 'text'
    assert service.store.get_document_bytes('TEST_FIXTURE_spool', 'one') == expected
    assert service.store.get_document('TEST_FIXTURE_spool', 'one') == document
    assert not list(tmp_path.glob('stockfish-verified-bridge-*'))
    with service.store.transaction() as connection:
        bridge._put_archived_document(connection, 'TEST_FIXTURE_spool', 'one', digest, session,
            document=document, temporary_dir=tmp_path)
    with pytest.raises(ValueError, match='DATABASE_PARENT_CHANGED'):
        with service.store.transaction() as connection:
            bridge._put_archived_document(connection, 'TEST_FIXTURE_spool', 'one', '0' * 64, session,
                document=document, temporary_dir=tmp_path)


@pytest.mark.parametrize('cancel', [False, True])
def test_archived_document_spool_failure_rolls_back_and_removes_temporary_file(service, tmp_path, cancel):
    document = {'TEST_FIXTURE': ['中文' * 1000] * 200}
    armed = False
    def checkpoint():
        if cancel and armed:
            raise RuntimeError('TEST_FIXTURE_SPOOL_CANCELLED')
    session = bridge.ArchiveVerificationSession(checkpoint=checkpoint)
    armed = True
    expected = hashlib.sha256(canonical_json(document).encode()).hexdigest() if cancel else '0' * 64
    with pytest.raises((ValueError, RuntimeError), match='CONTENT_CHANGED|SPOOL_CANCELLED'):
        with service.store.transaction() as connection:
            service.store._put_document(connection, 'TEST_FIXTURE_spool', 'earlier', {'TEST_FIXTURE': True})
            bridge._put_archived_document(connection, 'TEST_FIXTURE_spool', 'new', expected, session,
                document=document, temporary_dir=tmp_path)
    assert service.store.get_document('TEST_FIXTURE_spool', 'earlier') is None
    assert service.store.get_document('TEST_FIXTURE_spool', 'new') is None
    assert not list(tmp_path.glob('stockfish-verified-bridge-*'))


def test_authenticated_metadata_releases_heavy_cache_without_reauthentication(archive, service, monkeypatch):
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    original = bridge.authenticate_archive
    calls = []
    def counted(*a, **k):
        calls.append(True)
        return original(*a, **k)
    monkeypatch.setattr(bridge, 'authenticate_archive', counted)
    view = bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session)
    assert 'run' not in view and view['input_metadata']['sessions']
    sessions = list(view['input_metadata']['sessions'])
    view['input_metadata']['sessions'].clear()
    before = (dict(session.files), list(session.locators), session.read_bytes, session.started)
    session.release_cached_archives()
    assert not session.capabilities
    assert (session.files, session.locators, session.read_bytes, session.started) == before
    run_view = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)
    assert len(calls) == 1 and run_view['input_metadata']['sessions'] == sessions
    assert run_view['run'] == archive['run']
    bundle = bridge.load_verified_bridge_input(service, cert['input_id'], session=session)
    assert [str(day) for day in bundle.sessions] == sessions
    session.release_cached_archives()
    assert session.terminal()['borrowed_values'] == 2


@pytest.mark.parametrize('target', ['run', 'input'])
def test_borrowed_objects_cannot_be_mutated_after_cache_release(archive, service, target):
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    view = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)
    bundle = bridge.load_verified_bridge_input(service, cert['input_id'], session=session)
    session.release_cached_archives()
    if target == 'run':
        view['run']['results'][bridge.STRATEGY]['counts']['TEST_FIXTURE_MUTATION'] = 1
    else:
        object.__setattr__(bundle, 'coverage_type', 'UNVERIFIABLE_AS_OF')
    with pytest.raises(ValueError, match='BORROWED_VERIFIED_CONTENT_CHANGED'):
        session.terminal()


def test_verified_loader_cannot_use_plain_metadata_other_session_or_other_store(archive, service, tmp_path):
    from types import SimpleNamespace
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    view = bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session)
    with pytest.raises(ValueError, match='GENUINE_SESSION_REQUIRED'):
        bridge.load_verified_bridge_input(service, cert['input_id'], session=view)
    with pytest.raises(ValueError, match='VERIFIED_INPUT_VIEW_REQUIRED'):
        bridge.load_verified_bridge_input(service, cert['input_id'], session=bridge.ArchiveVerificationSession())
    other = SimpleNamespace(store=SQLiteStore(tmp_path / 'TEST_FIXTURE-other.db'))
    with pytest.raises(ValueError, match='VERIFIED_INPUT_VIEW_REQUIRED'):
        bridge.load_verified_bridge_input(other, cert['input_id'], session=session)
    # Even a second store object at the same path must authenticate for itself.
    other = SimpleNamespace(store=SQLiteStore(service.store.path))
    with pytest.raises(ValueError, match='VERIFIED_INPUT_VIEW_REQUIRED'):
        bridge.load_verified_bridge_input(other, cert['input_id'], session=session)


def test_release_preserves_original_file_terminal_guard(archive, service):
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session)
    session.release_cached_archives()
    path = Path(archive['items'][0]['raw']['path'])
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='PHYSICAL_IDENTITY|REGISTERED_FILE_CHANGED'):
        session.terminal()


def test_verified_view_rechecks_database_binding_after_release(archive, service):
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session)
    session.release_cached_archives()
    binding = service.store.get_document(bridge.INPUT_BINDINGS, cert['input_id'])
    binding['run_id'] = 'TEST_FIXTURE_other_run'
    raw = canonical_json(binding)
    with service.store.transaction() as connection:
        # Simulate offline corruption in this isolated fixture, then restore
        # the production immutability trigger before the verifier reads it.
        connection.execute('DROP TRIGGER documents_no_update')
        connection.execute('UPDATE documents SET payload=?,content_hash=? WHERE namespace=? AND document_id=?',
            (raw, hashlib.sha256(raw.encode()).hexdigest(), bridge.INPUT_BINDINGS, cert['input_id']))
        connection.execute("CREATE TRIGGER documents_no_update BEFORE UPDATE ON documents BEGIN SELECT RAISE(ABORT, 'immutable documents'); END")
    with pytest.raises(ValueError, match='VERIFIED_VIEW_BINDING_CHANGED'):
        bridge.load_verified_bridge_input(service, cert['input_id'], session=session)


def test_import_releases_expanded_run_during_input_insert_and_same_cap_reimports(archive, service, monkeypatch):
    cap = authenticate(archive)
    original = bridge._put_archived_document
    observed = []
    def inspect(connection, namespace, *args, **kwargs):
        if namespace == 'candidate_inputs':
            observed.append(bridge._state(cap).get('run') is None)
        return original(connection, namespace, *args, **kwargs)
    monkeypatch.setattr(bridge, '_put_archived_document', inspect)
    cert = bridge.commit_bridge(service, cap)
    assert bridge._state(cap).get('run') is None
    assert bridge.commit_bridge(service, cap) == cert
    assert observed == [True, True]
    session = cap._session
    view = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)
    assert view['run'] == archive['run']
    session.terminal()


def test_run_original_changed_after_input_insert_rolls_back_everything(archive, service, monkeypatch):
    cap = authenticate(archive)
    original = bridge._put_archived_document
    path = Path(archive['artifacts']['canonical-run.json.gz']['path'])
    def corrupt(connection, namespace, *args, **kwargs):
        result = original(connection, namespace, *args, **kwargs)
        if namespace == 'candidate_inputs':
            path.write_bytes(path.read_bytes() + b'TEST_FIXTURE_CHANGED_AFTER_INPUT_INSERT')
        return result
    monkeypatch.setattr(bridge, '_put_archived_document', corrupt)
    with pytest.raises(ValueError, match='PHYSICAL_IDENTITY|REGISTERED_FILE_CHANGED'):
        bridge.commit_bridge(service, cap)
    assert service.store.get_document('candidate_inputs', archive['run']['input_id']) is None
    assert service.store.get_document('strategy_experiment_runs', archive['run']['run_id']) is None
    assert service.store.get_document(bridge.CERTIFICATES, cap.audit()['bridge_id']) is None
    with pytest.raises(ValueError, match='PHYSICAL_IDENTITY|REGISTERED_FILE_CHANGED'):
        bridge.commit_bridge(service, cap)


def test_sqlite_payload_callback_releases_bytes_before_constraints_and_unregisters(service, tmp_path, monkeypatch):
    import sqlite3
    document = {'TEST_FIXTURE': '中文 callback lifecycle'}
    expected = hashlib.sha256(canonical_json(document).encode()).hexdigest()
    events, alive = [], [False]
    class TrackedBytes(bytes):
        def __new__(cls, raw):
            alive[0] = True
            return super().__new__(cls, raw)
        def __del__(self):
            alive[0] = False
            events.append('bytes_released')
    original = bridge.tempfile.TemporaryFile
    class Spool:
        def __init__(self, *a, **k):
            self.file = original(*a, **k)
        def __getattr__(self, key):
            return getattr(self.file, key)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return self.file.__exit__(*a)
        def read(self, size=-1):
            return TrackedBytes(self.file.read(size))
    monkeypatch.setattr(bridge.tempfile, 'TemporaryFile', Spool)
    session = bridge.ArchiveVerificationSession()
    with service.store.transaction() as connection:
        def observed(value):
            assert not alive[0] and json.loads(value) == document
            events.append('insert_trigger')
            return 1
        connection.create_function('TEST_FIXTURE_after_payload', 1, observed)
        connection.execute("CREATE TRIGGER TEST_FIXTURE_after_insert AFTER INSERT ON documents WHEN NEW.namespace='TEST_FIXTURE_udf' BEGIN SELECT TEST_FIXTURE_after_payload(NEW.payload); END")
        bridge._put_archived_document(connection, 'TEST_FIXTURE_udf', 'one', expected, session,
            document=document, temporary_dir=tmp_path)
        with pytest.raises(sqlite3.Error):
            connection.execute('SELECT _stockfish_verified_bridge_payload_v1()')
    assert events == ['bytes_released', 'insert_trigger']
    assert service.store.get_document('TEST_FIXTURE_udf', 'one') == document


@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt])
def test_sqlite_payload_callback_restores_original_exception_and_cleans_up(service, tmp_path, error_type):
    import sqlite3
    import sys
    failure = error_type('TEST_FIXTURE_CALLBACK_CANCELLED')
    def checkpoint():
        if sys._getframe(2).f_code.co_name == 'payload':
            raise failure
    session = bridge.ArchiveVerificationSession(checkpoint=checkpoint)
    document = {'TEST_FIXTURE': 'callback interruption'}
    expected = hashlib.sha256(canonical_json(document).encode()).hexdigest()
    with pytest.raises(error_type) as captured:
        with service.store.transaction() as connection:
            service.store._put_document(connection, 'TEST_FIXTURE_udf', 'earlier', document)
            try:
                bridge._put_archived_document(connection, 'TEST_FIXTURE_udf', 'one', expected, session,
                    document=document, temporary_dir=tmp_path)
            except BaseException:
                with pytest.raises(sqlite3.Error):
                    connection.execute('SELECT _stockfish_verified_bridge_payload_v1()')
                raise
    assert captured.value is failure
    assert service.store.get_document('TEST_FIXTURE_udf', 'earlier') is None
    assert service.store.get_document('TEST_FIXTURE_udf', 'one') is None
    assert not list(tmp_path.glob('stockfish-verified-bridge-*'))


def _offline_fixture_document(service, namespace, document_id, payload=None):
    """Simulate offline damage only inside an isolated TEST_FIXTURE database."""
    with service.store.transaction() as connection:
        trigger = 'documents_no_delete' if payload is None else 'documents_no_update'
        operation = 'DELETE' if payload is None else 'UPDATE'
        connection.execute('DROP TRIGGER ' + trigger)
        if payload is None:
            connection.execute('DELETE FROM documents WHERE namespace=? AND document_id=?', (namespace, document_id))
        else:
            raw = canonical_json(payload)
            connection.execute('UPDATE documents SET payload=?,content_hash=? WHERE namespace=? AND document_id=?',
                (raw, hashlib.sha256(raw.encode()).hexdigest(), namespace, document_id))
        connection.execute("CREATE TRIGGER " + trigger + " BEFORE " + operation + " ON documents BEGIN SELECT RAISE(ABORT, 'immutable documents'); END")


def test_saved_csv_retains_original_noncanonical_nested_key_order(tmp_path, service):
    archive = fixture_archive(tmp_path / 'TEST_FIXTURE-csv-order', observation_date='2026-09-15')
    cert = bridge.commit_bridge(service, authenticate(archive))
    saved = service.strategy_experiment(cert['run_id'])
    original = Path(archive['artifacts']['all.csv']['path']).read_bytes()
    regenerated = service._format_strategy_experiment_csv(saved, strategy_id=bridge.STRATEGY, selection='all').encode()
    assert regenerated != original, 'TEST_FIXTURE must expose canonical nested-key reordering'
    for selection in bridge.CSV_SELECTIONS:
        expected = Path(archive['artifacts'][selection + '.csv']['path']).read_bytes()
        assert service.export_strategy_experiment_csv(cert['run_id'], strategy_id=bridge.STRATEGY,
            selection=selection).encode() == expected
    assert service.export_strategy_experiment_csv(cert['run_id'], strategy_id=bridge.STRATEGY).encode() == original
    assert cert['run_document_hash'] == bridge.digest_json_stream(saved)


@pytest.mark.parametrize('missing', ['csv', 'binding', 'certificate'])
def test_saved_csv_missing_parent_or_original_fails_closed(archive, service, missing):
    cert = bridge.commit_bridge(service, authenticate(archive))
    namespace, key = {'csv': (bridge.CSV_EXPORTS, cert['run_id'] + ':all'),
        'binding': (bridge.RUN_BINDINGS, cert['run_id']),
        'certificate': (bridge.CERTIFICATES, cert['bridge_id'])}[missing]
    _offline_fixture_document(service, namespace, key)
    with pytest.raises(ValueError, match='CSV_'):
        service.export_strategy_experiment_csv(cert['run_id'], strategy_id=bridge.STRATEGY, selection='focus')


@pytest.mark.parametrize('damage', ['text', 'selection', 'parent_hash', 'reference'])
def test_saved_csv_self_hashed_corruption_is_not_authority(archive, service, damage):
    cert = bridge.commit_bridge(service, authenticate(archive))
    key = cert['run_id'] + ':all'
    document = service.store.get_document(bridge.CSV_EXPORTS, key)
    if damage == 'text':
        document['csv_text'] += '\nTEST_FIXTURE_CHANGED'
        raw = document['csv_text'].encode()
        document['csv_sha256'], document['csv_bytes'] = hashlib.sha256(raw).hexdigest(), len(raw)
    elif damage == 'selection':
        document = service.store.get_document(bridge.CSV_EXPORTS, cert['run_id'] + ':focus')
    elif damage == 'parent_hash':
        document['run_document_hash'] = '0' * 64
    else:
        document['original_reference']['sha256'] = '0' * 64
    _offline_fixture_document(service, bridge.CSV_EXPORTS, key, document)
    with pytest.raises(ValueError, match='CSV_'):
        service.export_strategy_experiment_csv(cert['run_id'], strategy_id=bridge.STRATEGY, selection='all')


def test_saved_csv_valid_other_run_binding_cannot_be_substituted(archive, service, tmp_path):
    first = bridge.commit_bridge(service, authenticate(archive))
    other = fixture_archive(tmp_path / 'TEST_FIXTURE-other-csv', observation_date='2026-09-15')
    second = bridge.commit_bridge(service, authenticate(other))
    replacement = service.store.get_document(bridge.RUN_BINDINGS, second['run_id'])
    _offline_fixture_document(service, bridge.RUN_BINDINGS, first['run_id'], replacement)
    with pytest.raises(ValueError, match='CSV_RUN_BINDING_REQUIRED'):
        service.export_strategy_experiment_csv(first['run_id'], strategy_id=bridge.STRATEGY, selection='all')


def test_saved_csv_public_request_cannot_be_substituted_with_another_complete_run(archive, service, tmp_path):
    first = bridge.commit_bridge(service, authenticate(archive))
    other = fixture_archive(tmp_path / 'TEST_FIXTURE-other-request', observation_date='2026-09-15')
    second = bridge.commit_bridge(service, authenticate(other))
    replacement = service.strategy_experiment(second['run_id'])
    _offline_fixture_document(service, 'strategy_experiment_runs', first['run_id'], replacement)
    with pytest.raises(ValueError, match='STRATEGY_EXPERIMENT_RUN_ID_MISMATCH'):
        service.export_strategy_experiment_csv(first['run_id'], strategy_id=bridge.STRATEGY, selection='all')


@pytest.mark.parametrize('remaining', ['source', 'input_binding'])
def test_saved_csv_orphan_bridge_state_cannot_fall_back_to_rendering(archive, service, remaining):
    cert = bridge.commit_bridge(service, authenticate(archive))
    for namespace in (bridge.CSV_EXPORTS, bridge.RUN_BINDINGS, bridge.CERTIFICATES,
                      bridge.INPUT_BINDINGS if remaining == 'source' else bridge.SOURCES):
        with service.store.transaction(write=False) as connection:
            keys = [row[0] for row in connection.execute('SELECT document_id FROM documents WHERE namespace=?',
                (namespace,)).fetchall()]
        for key in keys:
            _offline_fixture_document(service, namespace, key)
    with pytest.raises(ValueError, match='CSV_ORPHAN_BRIDGE_STATE'):
        service.export_strategy_experiment_csv(cert['run_id'], strategy_id=bridge.STRATEGY, selection='all')
    # A shared input binding for a different run does not mark this run as bridged.
    if remaining == 'input_binding':
        binding = service.store.get_document(bridge.INPUT_BINDINGS, cert['input_id'])
        binding['run_id'] = 'TEST_FIXTURE_OTHER_RUN_WITH_SHARED_INPUT'
        _offline_fixture_document(service, bridge.INPUT_BINDINGS, cert['input_id'], binding)
        assert bridge.saved_csv_export(service, archive['run'], bridge.STRATEGY, 'all') is None


def test_saved_csv_collision_rolls_back_original_parents_and_source_registration(archive, service):
    from stockfish_core.storage import ImmutableConflict
    run_id = archive['run']['run_id']
    service.store.put_document(bridge.CSV_EXPORTS, run_id + ':all', {'TEST_FIXTURE': 'conflicting original'})
    with pytest.raises(ImmutableConflict):
        bridge.commit_bridge(service, authenticate(archive))
    assert service.store.get_document('candidate_inputs', archive['run']['input_id']) is None
    assert service.store.get_document('strategy_experiment_runs', run_id) is None
    assert service.store.list_documents(bridge.CERTIFICATES) == []
    assert service.store.list_documents(bridge.SOURCES) == []
    assert len(service.store.list_documents(bridge.CSV_EXPORTS)) == 1


def test_unbridged_csv_falls_back_and_offline_encoder_does_not_gain_authority(archive, service):
    run = archive['run']
    assert bridge.saved_csv_export(service, run, bridge.STRATEGY, 'all') is None
    class Encoder(CandidateService):
        def strategy_experiment(self, run_id):
            assert run_id == run['run_id']
            return run
    encoder = object.__new__(Encoder)
    expected = Path(archive['artifacts']['all.csv']['path']).read_bytes()
    assert encoder.export_strategy_experiment_csv(run['run_id'], strategy_id=bridge.STRATEGY).encode() == expected
    genuine = object.__new__(CandidateService)
    with pytest.raises(AttributeError):
        genuine.export_strategy_experiment_csv(run['run_id'], strategy_id=bridge.STRATEGY)


@pytest.mark.parametrize('target', ['passed_run', 'stored_run'])
def test_saved_csv_requires_actual_complete_frozen_run_hash(archive, service, target):
    cert = bridge.commit_bridge(service, authenticate(archive))
    changed = deepcopy(archive['run'])
    changed['TEST_FIXTURE_NEW_FIELD'] = 'must change complete canonical hash'
    if target == 'stored_run':
        _offline_fixture_document(service, 'strategy_experiment_runs', cert['run_id'], changed)
        changed = archive['run']
    with pytest.raises(ValueError, match='CSV_FROZEN_RUN_IDENTITY_MISMATCH|BRIDGED_DATABASE_PARENT_CHANGED'):
        bridge.saved_csv_export(service, changed, bridge.STRATEGY, 'all')


def test_missing_saved_csv_only_fresh_authenticated_commit_repairs(archive, service):
    cert = bridge.commit_bridge(service, authenticate(archive))
    _offline_fixture_document(service, bridge.CSV_EXPORTS, cert['run_id'] + ':all')
    with pytest.raises(ValueError, match='CSV_COMPLETE_ORIGINAL_SET_REQUIRED'):
        service.export_strategy_experiment_csv(cert['run_id'], strategy_id=bridge.STRATEGY, selection='all')
    assert bridge.commit_bridge(service, authenticate(archive)) == cert
    expected = Path(archive['artifacts']['all.csv']['path']).read_bytes()
    assert service.export_strategy_experiment_csv(cert['run_id'], strategy_id=bridge.STRATEGY, selection='all').encode() == expected


@pytest.mark.parametrize('changed', ['raw', 'source'])
def test_commit_terminal_rejects_original_change_after_csv_and_binding_writes(archive, service, monkeypatch, changed):
    cap = authenticate(archive)
    path = Path(archive['items'][0]['raw']['path']) if changed == 'raw' else archive['source'] / 'stockfish_core/yaogu/candidate_service.py'
    put = service.store._put_document
    def mutate_after_binding(connection, namespace, key, payload):
        result = put(connection, namespace, key, payload)
        if namespace == bridge.INPUT_BINDINGS:
            assert connection.execute('SELECT COUNT(*) FROM documents WHERE namespace=?', (bridge.CSV_EXPORTS,)).fetchone()[0] == 4
            path.write_bytes(path.read_bytes() + b'\nTEST_FIXTURE_CHANGED_BEFORE_COMMIT')
        return result
    monkeypatch.setattr(service.store, '_put_document', mutate_after_binding)
    with pytest.raises(ValueError, match='PHYSICAL_IDENTITY|REGISTERED_FILE_CHANGED'):
        bridge.commit_bridge(service, cap)
    for namespace in (bridge.CSV_EXPORTS, bridge.CERTIFICATES, bridge.SOURCES, bridge.RUN_BINDINGS,
                      'candidate_inputs', 'strategy_experiment_runs'):
        assert service.store.list_documents(namespace) == []


@pytest.mark.parametrize('budget', [{'max_total_read_bytes': 10}, {'max_unique_bytes': 10}, {'max_files': 1},
    {'max_rss_bytes': 1}, {'max_seconds': 1e-12}])
def test_shared_budget_is_bounded(archive, budget):
    with pytest.raises(ValueError, match='BUDGET_EXCEEDED'):
        authenticate(archive, session=bridge.ArchiveVerificationSession(**budget))


def test_cancellation_checkpoint_aborts_authentication(archive):
    count = 0
    def checkpoint():
        nonlocal count
        count += 1
        if count > 8:
            raise RuntimeError('TEST_FIXTURE_CANCELLED')
    with pytest.raises(RuntimeError, match='CANCELLED'):
        authenticate(archive, session=bridge.ArchiveVerificationSession(checkpoint=checkpoint))


def test_stored_source_corruption_cannot_be_reused(archive, service, monkeypatch):
    cert = bridge.commit_bridge(service, authenticate(archive))
    original = service.store.get_document
    monkeypatch.setattr(service.store, 'get_document',
        lambda namespace, key: None if namespace == bridge.SOURCES else original(namespace, key))
    with pytest.raises(ValueError, match='SOURCE_REGISTRATION_CHANGED'):
        bridge.require_verified_bridge(service, cert['run_id'])


def test_legitimate_other_certificate_cannot_answer_requested_key(archive, service):
    cert = bridge.commit_bridge(service, authenticate(archive))
    binding = service.store.get_document(bridge.RUN_BINDINGS, cert['run_id'])
    service.store.put_document(bridge.RUN_BINDINGS, 'TEST_FIXTURE_WRONG_RUN', binding)
    with pytest.raises(ValueError, match='REQUESTED_BRIDGE_IDENTITY_MISMATCH'):
        bridge.require_verified_bridge(service, 'TEST_FIXTURE_WRONG_RUN')


def test_closed_or_forged_jobs_do_not_authorize_import(archive, service):
    cap = authenticate(archive)
    service.jobs.shutdown()
    with pytest.raises(ValueError, match='JOB_LOCK_OWNER'):
        bridge.commit_bridge(service, cap)
    original = service.jobs
    from types import SimpleNamespace
    service.jobs = SimpleNamespace(owner_id='TEST_FIXTURE_FORGED', _closed=False)
    try:
        with pytest.raises(ValueError, match='JOB_LOCK_OWNER'):
            bridge.commit_bridge(service, cap)
    finally:
        service.jobs = original


@pytest.mark.parametrize('budget', [{'max_total_read_bytes': float('inf')}, {'max_files': 1.5},
    {'max_seconds': float('nan')}, {'max_seconds': float('inf')}, {'max_rss_bytes': True}])
def test_nonfinite_and_fractional_budgets_are_rejected(budget):
    with pytest.raises(ValueError, match='BUDGET_REQUIRED'):
        bridge.ArchiveVerificationSession(**budget)


def test_inline_reconstructed_coverage_is_preserved_never_promoted(tmp_path, service):
    archive = fixture_archive(tmp_path / 'TEST_FIXTURE-reconstructed', coverage_type='RECONSTRUCTED_HISTORICAL')
    cap = authenticate(archive)
    audit = cap.audit()
    assert audit['coverage_type'] == 'RECONSTRUCTED_HISTORICAL'
    assert audit['historical_owner_restored'] is False
    cert = bridge.commit_bridge(service, cap)
    bundle = service.store.get_document('candidate_inputs', cert['input_id'])
    assert bundle['coverage_type'] == 'RECONSTRUCTED_HISTORICAL'
    assert bundle['history_artifact_ref'] is bundle['history_artifact_hash'] is None
    assert all(receipt['historical_time_proof'] is None for receipt in bundle['receipts'])
    assert service.strategy_experiment(cert['run_id']) == archive['run']


def test_reconstructed_archive_still_requires_three_original_producers(tmp_path):
    archive = fixture_archive(tmp_path / 'TEST_FIXTURE-reconstructed', coverage_type='RECONSTRUCTED_HISTORICAL')
    path = archive['job'] / 'capture-summary.json'
    document = json.loads(path.read_bytes())
    document['core_records'][0]['producer_files'] = []
    path.write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(ValueError, match='ORIGINAL_THREE_PRODUCER_FILES_REQUIRED'):
        authenticate(archive)


def test_dated_fixture_uses_official_sessions_without_changing_product(tmp_path):
    archive = fixture_archive(tmp_path / 'TEST_FIXTURE-dated', observation_date='2026-09-01',
        seen_at='2026-09-01T12:00:00+00:00', generated_at='2026-09-01T13:00:00+00:00')
    audit = authenticate(archive).audit()
    assert audit['observation_date'] == '2026-09-01' and audit['target_session'] == '2026-09-02'
    assert audit['authenticated_source_count'] == 139


def test_fifo_replacement_during_open_is_nonblocking_and_refused(tmp_path, monkeypatch):
    import os
    if not hasattr(os, 'mkfifo'):
        pytest.skip('OS has no FIFO API')
    path = tmp_path / 'TEST_FIXTURE-original'
    path.write_bytes(b'TEST_FIXTURE')
    original_open = bridge.os.open
    def replaced_open(target, flags):
        path.unlink()
        os.mkfifo(path)
        return original_open(target, flags)
    monkeypatch.setattr(bridge.os, 'open', replaced_open)
    with pytest.raises(ValueError, match='OPENED_FILE_IDENTITY_CHANGED'):
        bridge.ArchiveVerificationSession().read(path)


@pytest.mark.parametrize('mode', ['UNVERIFIABLE', 'HISTORICAL_PROOF'])
def test_unverifiable_input_or_serialized_historical_proof_cannot_be_promoted(archive, mode):
    path = Path(archive['artifacts']['input.json.gz']['path'])
    document = json.loads(gzip.decompress(path.read_bytes()))
    if mode == 'UNVERIFIABLE':
        document['coverage_type'] = 'UNVERIFIABLE_AS_OF'
    else:
        document['receipts'][0]['historical_time_proof'] = dict(policy_id='TEST_FIXTURE_PROOF',
            source_url='https://TEST_FIXTURE.invalid/proof', evidence_hash='a' * 64,
            event_time_basis='SOURCE_DATED_EVENT_RECORD', publication_time_basis='SOURCE_PUBLICATION_TIMESTAMP',
            applies_from='2026-01-01', applies_to='2026-12-31', evidence_refs=['TEST_FIXTURE_REF'],
            verified_at='2026-09-30T12:00:00+00:00')
    path.write_bytes(gzip.compress(json.dumps(document).encode(), mtime=0))
    ref = workflow._ref(path)
    archive['artifacts']['input.json.gz'] = ref
    manifest = json.loads(Path(archive['artifacts']['manifest.json']['path']).read_bytes())
    manifest['artifacts']['input.json.gz'] = ref
    reseal(archive, manifest=manifest)
    with pytest.raises(ValueError, match='EXPLICIT_FIXTURE_REQUIRED|WITHOUT_HISTORICAL_OWNER_REQUIRED'):
        authenticate(archive)


def test_cached_capture_original_producer_can_be_a_distinct_older_checkout(tmp_path):
    import shutil
    archive = fixture_archive(tmp_path / 'TEST_FIXTURE-cached', coverage_type='RECONSTRUCTED_HISTORICAL')
    copies = {}
    for ref in archive['items'][0]['producer_files']:
        path = tmp_path / 'TEST_FIXTURE-older-worker' / Path(ref['path']).name
        path.parent.mkdir(exist_ok=True)
        shutil.copyfile(ref['path'], path)
        copies[ref['path']] = workflow._ref(path)
    for item in archive['items']:
        item['producer_files'] = [copies[ref['path']] for ref in item['producer_files']]
    locator = archive['job'] / 'capture-summary.json'
    locator.write_text(json.dumps({'core_records': archive['items']}), encoding='utf-8')
    archive['completion']['validation_references'].extend(copies.values())
    archive['final']['source_reference_count'] = len(archive['completion']['validation_references'])
    reseal(archive)
    cap = authenticate(archive)
    assert cap.audit()['coverage_type'] == 'RECONSTRUCTED_HISTORICAL'
    path = Path(next(iter(copies.values()))['path'])
    path.write_bytes(path.read_bytes() + b'\n# TEST_FIXTURE alteration\n')
    with pytest.raises(ValueError, match='PHYSICAL_IDENTITY'):
        authenticate(archive)


def test_session_capability_cache_has_no_global_strong_reference_leak(archive):
    import gc
    import weakref
    session = bridge.ArchiveVerificationSession()
    cap = authenticate(archive, session=session)
    assert authenticate(archive, session=session) is cap
    session_ref, cap_ref = weakref.ref(session), weakref.ref(cap)
    del session, cap
    gc.collect()
    assert session_ref() is None and cap_ref() is None


def test_immutable_reimport_validates_original_bytes_without_reexpanding_input(archive, service, monkeypatch):
    cap = authenticate(archive)
    cert = bridge.commit_bridge(service, cap)
    original = bridge._decompress
    input_sha = archive['artifacts']['input.json.gz']['sha256']
    def refuse_input(raw, session):
        assert hashlib.sha256(raw).hexdigest() != input_sha, 'REEXPANDED_IMMUTABLE_INPUT'
        return original(raw, session)  # A released run is intentionally reread.
    monkeypatch.setattr(bridge, '_decompress', refuse_input)
    assert bridge.commit_bridge(service, cap) == cert
    assert bridge.require_verified_bridge(service, cert['run_id'], session=cap._session)['input_hash'] == cert['input_hash']


def _reseal_fixture_input(archive, document=None, *, raw=None, rebind_hash=False):
    """Corrupt a private fixture and reseal its refs, never real archive bytes.

    Rebinding the declared hash chain makes the fresh reconstruction, rather
    than an obsolete declared input hash, reject the changed saved content.
    The old official exports remain untouched and can never authorize it.
    """
    path = Path(archive['artifacts']['input.json.gz']['path'])
    path.write_bytes(gzip.compress(raw if raw is not None else json.dumps(document).encode(), mtime=0))
    archive['artifacts']['input.json.gz'] = workflow._ref(path)
    manifest = json.loads(Path(archive['artifacts']['manifest.json']['path']).read_bytes())
    manifest['artifacts']['input.json.gz'] = archive['artifacts']['input.json.gz']
    if rebind_hash:
        run = deepcopy(archive['run'])
        input_hash = bridge.digest_json_stream(document)
        run['input_hash'] = run['results'][bridge.STRATEGY]['input_hash'] = input_hash
        run['input_id'] = 'market_' + input_hash
        fields = ('input_hash', 'observation_date', 'decision_cutoff', 'spec_hash', 'source_hash',
            'implementation_version', 'universe_hash', 'execution_profile', 'feature_version', 'research_context_hash')
        run['run_id'] = 'strategy_experiment_' + bridge.digest_json_stream({key: run[key] for key in fields})[:24]
        run['content_hash'] = bridge.digest_json_stream({key: value for key, value in run.items() if key != 'content_hash'})
        run_path = Path(archive['artifacts']['canonical-run.json.gz']['path'])
        run_path.write_bytes(gzip.compress(json.dumps(run).encode(), mtime=0))
        archive['artifacts']['canonical-run.json.gz'] = workflow._ref(run_path)
        archive['run'] = run
        archive['completion']['run_id'] = manifest['run_id'] = run['run_id']
        manifest['input_hash'] = input_hash
        manifest['canonical_content_hash'] = run['content_hash']
        manifest['artifacts']['canonical-run.json.gz'] = archive['artifacts']['canonical-run.json.gz']
    reseal(archive, manifest=manifest)


@pytest.mark.parametrize('change', ['extra_root', 'extra_bar', 'missing_default', 'missing_required',
    'decimal_number', 'boolean_number', 'timestamp_spelling', 'timestamp_naive',
    'changed_price', 'reordered_bars', 'duplicate_bar'])
def test_saved_input_full_canonical_identity_rejects_resealed_changes(archive, change, monkeypatch):
    document = json.loads(gzip.decompress(Path(archive['artifacts']['input.json.gz']['path']).read_bytes()))
    if change == 'extra_root':
        document['TEST_FIXTURE_EXTRA'] = None
    elif change == 'extra_bar':
        document['bars'][0]['TEST_FIXTURE_EXTRA'] = None
    elif change == 'missing_default':
        del document['themes']
    elif change == 'missing_required':
        del document['universe']['snapshot_id']
    elif change == 'decimal_number':
        document['bars'][0]['close'] = float(document['bars'][0]['close'])
    elif change == 'boolean_number':
        document['universe']['complete'] = int(document['universe']['complete'])
    elif change == 'timestamp_spelling':
        from datetime import datetime, timezone
        value = document['bars'][0]['event_time']
        document['bars'][0]['event_time'] = datetime.fromisoformat(value).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
        assert document['bars'][0]['event_time'] != value
        assert datetime.fromisoformat(document['bars'][0]['event_time']) == datetime.fromisoformat(value)
    elif change == 'timestamp_naive':
        document['bars'][0]['event_time'] = document['bars'][0]['event_time'][:19]
    elif change == 'changed_price':
        document['bars'][0]['close'] = '999.001'
    elif change == 'reordered_bars':
        document['bars'].reverse()
    else:
        document['bars'].append(deepcopy(document['bars'][0]))
    _reseal_fixture_input(archive, document, rebind_hash=True)
    monkeypatch.setattr(bridge, '_validate_run', lambda *a, **k: pytest.fail('MUST_REJECT_BEFORE_RUN_EXPORT_CHECKS'))
    with pytest.raises(ValueError, match='FRESH_NORMALIZATION_DIFFERS_FROM_ORIGINAL_INPUT'):
        authenticate(archive)


@pytest.mark.parametrize('change,reason', [
    ('observation_date', 'ORIGINAL_SESSION_CALENDAR_MISMATCH'),
    ('sessions', 'ORIGINAL_SESSION_CALENDAR_MISMATCH'),
    ('missing_receipt', 'EXACT_CORE_RECEIPT_SET_REQUIRED'),
    ('duplicate_receipt', 'FRESH_NORMALIZATION_DIFFERS_FROM_ORIGINAL_INPUT')])
def test_saved_input_metadata_never_replaces_full_source_identity(archive, change, reason):
    document = json.loads(gzip.decompress(Path(archive['artifacts']['input.json.gz']['path']).read_bytes()))
    if change == 'observation_date':
        document['observation_date'] = document['sessions'][-2]
    elif change == 'sessions':
        document['sessions'].pop(0)
    elif change == 'missing_receipt':
        document['receipts'].pop()
    else:
        document['receipts'].append(deepcopy(document['receipts'][0]))
    _reseal_fixture_input(archive, document, rebind_hash=True)
    with pytest.raises(ValueError, match=reason):
        authenticate(archive)


@pytest.mark.parametrize('prefix,reason', [
    ('"observation_date":"TEST_FIXTURE_duplicate",', 'DUPLICATE_JSON_KEY'),
    ('"TEST_FIXTURE_nested":{"same":0,"same":1},', 'DUPLICATE_JSON_KEY'),
    ('"TEST_FIXTURE_nonfinite":NaN,', 'NONFINITE_JSON'),
    ('"TEST_FIXTURE_nonfinite":Infinity,', 'NONFINITE_JSON'),
    ('"TEST_FIXTURE_nonfinite":1e309,', 'Out of range float values')])
def test_saved_input_still_uses_strict_json_and_finite_canonical_hash(archive, prefix, reason):
    raw = gzip.decompress(Path(archive['artifacts']['input.json.gz']['path']).read_bytes())
    _reseal_fixture_input(archive, raw=b'{' + prefix.encode() + raw[1:])
    with pytest.raises(ValueError, match=reason):
        authenticate(archive)


def test_fresh_normalized_full_content_must_equal_saved_canonical_hash(archive, monkeypatch):
    from decimal import Decimal
    from stockfish_core.yaogu import provider_normalization
    original = provider_normalization.normalize_capture_with_report
    def changed(*args, **kwargs):
        bundle, report = original(*args, **kwargs)
        bars = (bundle.bars[0].model_copy(update={'close': Decimal('999.001')}), *bundle.bars[1:])
        return bundle.model_copy(update={'bars': bars}), report
    monkeypatch.setattr(provider_normalization, 'normalize_capture_with_report', changed)
    with pytest.raises(ValueError, match='FRESH_NORMALIZATION_DIFFERS_FROM_ORIGINAL_INPUT'):
        authenticate(archive)


def test_saved_input_formatting_preserved_without_duplicate_typed_input(archive, monkeypatch):
    import weakref
    from stockfish_core.yaogu import provider_normalization
    document = json.loads(gzip.decompress(Path(archive['artifacts']['input.json.gz']['path']).read_bytes()))
    formatted = json.dumps(dict(reversed(tuple(document.items()))), ensure_ascii=True, indent=2).encode()
    _reseal_fixture_input(archive, raw=formatted)
    del document
    input_sha = archive['artifacts']['input.json.gz']['sha256']
    run_sha = archive['artifacts']['canonical-run.json.gz']['sha256']
    original_decode, original_normalize, original_validate = bridge._decompress, provider_normalization.normalize_capture_with_report, bridge._validate_run
    seen = {}
    class WeakDocument(dict):
        pass
    def decoded(raw, session):
        checksum = hashlib.sha256(raw).hexdigest()
        if checksum == run_sha:
            assert seen['saved_input']() is None, 'SAVED_FULL_INPUT_OVERLAPS_EXPANDED_RUN'
        result = original_decode(raw, session)
        if checksum == input_sha:
            result = WeakDocument(result)
            seen['saved_input'] = weakref.ref(result)
        return result
    def normalized(*args, **kwargs):
        result = original_normalize(*args, **kwargs)
        assert len(args[0]) == len(archive['items'])
        seen['fresh_bundle'] = result[0]
        return result
    def validated(run, bundle, *args, **kwargs):
        assert bundle is seen['fresh_bundle']
        seen['run_checked'] = True
        return original_validate(run, bundle, *args, **kwargs)
    monkeypatch.setattr(bridge, '_decompress', decoded)
    monkeypatch.setattr(bridge.MarketInput, 'model_validate',
        classmethod(lambda cls, *a, **k: pytest.fail('DUPLICATE_SAVED_TYPED_INPUT')))
    monkeypatch.setattr(provider_normalization, 'normalize_capture_with_report', normalized)
    monkeypatch.setattr(bridge, '_validate_run', validated)
    audit = authenticate(archive).audit()
    assert seen['run_checked'] and audit['status'] == 'VERIFIED'
    assert audit['input_hash'] == archive['run']['input_hash']
    assert audit['verification_budget']['terminal_rereads'] == 1


def test_retained_fresh_input_is_private_and_reused_after_cap_release(archive, service, monkeypatch):
    import gc
    import weakref
    from stockfish_core.yaogu import provider_normalization
    cert = bridge.commit_bridge(service, authenticate(archive))
    original = provider_normalization.normalize_capture_with_report
    fresh = []
    def normalized(*args, **kwargs):
        result = original(*args, **kwargs)
        fresh.append(result[0])
        return result
    monkeypatch.setattr(provider_normalization, 'normalize_capture_with_report', normalized)
    session = bridge.ArchiveVerificationSession()
    view = bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session, retain_input=True)
    assert set(view) == {'credential', 'run_metadata', 'input_metadata'}
    assert len(fresh) == 1 and fresh[0]._history_store is None
    json.dumps(view)  # No typed object or process-local proof is returned.
    original_sessions = tuple(view['input_metadata']['sessions'])
    view['input_metadata']['sessions'].clear()
    before = (session.started, session.read_bytes, len(session.files), len(session.locators))
    caps = [weakref.ref(cap) for cap in session.capabilities.values()]
    session.release_cached_archives()
    gc.collect()
    assert caps and all(ref() is None for ref in caps)
    assert before == (session.started, session.read_bytes, len(session.files), len(session.locators))
    monkeypatch.setattr(bridge.MarketInput, 'model_validate_json',
        classmethod(lambda cls, *a, **k: pytest.fail('RETAINED_INPUT_WAS_REPARSED')))
    bundle = bridge.load_verified_bridge_input(service, cert['input_id'], session=session)
    assert bundle is fresh[0] and tuple(map(str, bundle.sessions)) == original_sessions
    assert bridge.digest_market_input(bundle) == cert['input_hash']
    assert session.terminal()['borrowed_values'] == 1
    assert session.terminal()['borrowed_values'] == 1


@pytest.mark.parametrize('arguments', [{'run_id': 'TEST_FIXTURE_RUN', 'retain_input': True},
    {'input_id': 'TEST_FIXTURE_INPUT', 'retain_input': 1},
    {'input_id': 'TEST_FIXTURE_INPUT', 'retain_input': 'yes'}])
def test_retained_input_requires_explicit_boolean_input_view(service, arguments):
    with pytest.raises(ValueError, match='RETAIN_INPUT_REQUIRES_INPUT_VIEW'):
        bridge.authenticated_bridge_view(service, session=bridge.ArchiveVerificationSession(), **arguments)


def test_retained_input_cannot_upgrade_metadata_or_cap_cache_without_fresh_normalization(archive, service, monkeypatch):
    from stockfish_core.yaogu import provider_normalization
    cert = bridge.commit_bridge(service, authenticate(archive))
    original = provider_normalization.normalize_capture_with_report
    calls = []
    def normalized(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(provider_normalization, 'normalize_capture_with_report', normalized)
    session = bridge.ArchiveVerificationSession()
    bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session)
    assert len(calls) == 1
    bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session, retain_input=True)
    assert len(calls) == 2
    bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session, retain_input=True)
    assert len(calls) == 2
    assert {bridge._state(cap)['retained_input'] is not None for cap in session.capabilities.values()} == {False, True}
    assert bridge.load_verified_bridge_input(service, cert['input_id'], session=session) is not None
    session.terminal()


@pytest.mark.parametrize('phase', ['before_load', 'after_load'])
def test_retained_input_nested_mutation_is_rejected_before_use_or_at_atomic_terminal(archive, service, phase):
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session, retain_input=True)
    proof = session._verified_views[(id(service.store), bridge.INPUT_BINDINGS, cert['input_id'])]
    bundle = proof['retained_input']
    if phase == 'after_load':
        assert bridge.load_verified_bridge_input(service, cert['input_id'], session=session) is bundle
    session.release_cached_archives()
    bundle.receipts[0].parameters['TEST_FIXTURE_MUTATION'] = True
    with pytest.raises(ValueError, match='BORROWED_VERIFIED_CONTENT_CHANGED'):
        if phase == 'before_load':
            bridge.load_verified_bridge_input(service, cert['input_id'], session=session)
        else:
            with service.store.transaction() as connection:
                service.store._put_document(connection, 'TEST_FIXTURE_products', 'retained', {'TEST_FIXTURE': True})
                session.terminal()
    assert service.store.get_document('TEST_FIXTURE_products', 'retained') is None


@pytest.mark.parametrize('phase', ['retain', 'load', 'terminal'])
def test_retained_input_private_history_owner_is_rejected_even_with_unchanged_hash(archive, service, monkeypatch, phase):
    from stockfish_core.yaogu import provider_normalization
    cert = bridge.commit_bridge(service, authenticate(archive))
    original = provider_normalization.normalize_capture_with_report
    if phase == 'retain':
        def contaminated(*args, **kwargs):
            bundle, report = original(*args, **kwargs)
            before = bridge.digest_market_input(bundle)
            bundle._history_store = object()
            assert bridge.digest_market_input(bundle) == before
            return bundle, report
        monkeypatch.setattr(provider_normalization, 'normalize_capture_with_report', contaminated)
    session = bridge.ArchiveVerificationSession()
    if phase == 'retain':
        with pytest.raises(ValueError, match='INLINE_TYPED_INPUT_WITHOUT_HISTORY_OWNER_REQUIRED'):
            bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session, retain_input=True)
        assert not session._verified_views and not session.capabilities
        return
    bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session, retain_input=True)
    bundle = session._verified_views[(id(service.store), bridge.INPUT_BINDINGS, cert['input_id'])]['retained_input']
    if phase == 'terminal':
        assert bridge.load_verified_bridge_input(service, cert['input_id'], session=session) is bundle
    before = bridge.digest_market_input(bundle)
    bundle._history_store = object()
    assert bridge.digest_market_input(bundle) == before
    session.release_cached_archives()
    with pytest.raises(ValueError, match='INLINE_TYPED_INPUT_WITHOUT_HISTORY_OWNER_REQUIRED'):
        if phase == 'load':
            bridge.load_verified_bridge_input(service, cert['input_id'], session=session)
        else:
            session.terminal()


@pytest.mark.parametrize('field', ['history_artifact_ref', 'history_artifact_hash'])
def test_retained_input_cannot_gain_historical_reference(archive, service, field):
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session, retain_input=True)
    bundle = session._verified_views[(id(service.store), bridge.INPUT_BINDINGS, cert['input_id'])]['retained_input']
    object.__setattr__(bundle, field, 'TEST_FIXTURE_UNTRUSTED_HISTORY')
    with pytest.raises(ValueError, match='INLINE_TYPED_INPUT_WITHOUT_HISTORY_OWNER_REQUIRED'):
        bridge.load_verified_bridge_input(service, cert['input_id'], session=session)


def test_retained_input_stays_bound_to_session_store_and_actual_database_bytes(archive, service, tmp_path):
    from types import SimpleNamespace
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    view = bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session, retain_input=True)
    with pytest.raises(ValueError, match='GENUINE_SESSION_REQUIRED'):
        bridge.load_verified_bridge_input(service, cert['input_id'], session=view)
    with pytest.raises(ValueError, match='VERIFIED_INPUT_VIEW_REQUIRED'):
        bridge.load_verified_bridge_input(service, cert['input_id'], session=bridge.ArchiveVerificationSession())
    for store in (SQLiteStore(tmp_path / 'TEST_FIXTURE_OTHER.db'), SQLiteStore(service.store.path)):
        with pytest.raises(ValueError, match='VERIFIED_INPUT_VIEW_REQUIRED'):
            bridge.load_verified_bridge_input(SimpleNamespace(store=store), cert['input_id'], session=session)
    saved = service.store.get_document('candidate_inputs', cert['input_id'])
    saved['coverage_type'] = 'UNVERIFIABLE_AS_OF'
    _offline_fixture_document(service, 'candidate_inputs', cert['input_id'], saved)
    with pytest.raises(ValueError, match='BRIDGED_DATABASE_PARENT_CHANGED'):
        bridge.load_verified_bridge_input(service, cert['input_id'], session=session)


@pytest.mark.parametrize('changed', ['binding', 'raw'])
def test_retained_input_preserves_binding_and_original_terminal_guards(archive, service, changed):
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session, retain_input=True)
    session.release_cached_archives()
    if changed == 'binding':
        binding = service.store.get_document(bridge.INPUT_BINDINGS, cert['input_id'])
        binding['run_id'] = 'TEST_FIXTURE_OTHER_RUN'
        _offline_fixture_document(service, bridge.INPUT_BINDINGS, cert['input_id'], binding)
        with pytest.raises(ValueError, match='VERIFIED_VIEW_BINDING_CHANGED'):
            bridge.load_verified_bridge_input(service, cert['input_id'], session=session)
    else:
        bridge.load_verified_bridge_input(service, cert['input_id'], session=session)
        path = Path(archive['items'][0]['raw']['path'])
        path.write_bytes(path.read_bytes() + b' ')
        with pytest.raises(ValueError, match='PHYSICAL_IDENTITY|REGISTERED_FILE_CHANGED'):
            with service.store.transaction() as connection:
                service.store._put_document(connection, 'TEST_FIXTURE_products', 'late_raw', {'TEST_FIXTURE': True})
                session.terminal()
        assert service.store.get_document('TEST_FIXTURE_products', 'late_raw') is None


@pytest.mark.parametrize('phase', ['load', 'terminal'])
def test_retained_input_cancellation_preserves_original_exception_and_atomicity(archive, service, phase):
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session, retain_input=True)
    if phase == 'terminal':
        bridge.load_verified_bridge_input(service, cert['input_id'], session=session)
    session.release_cached_archives()
    failure = KeyboardInterrupt('TEST_FIXTURE_RETAINED_INPUT_CANCELLED')
    def cancelled():
        raise failure
    session.checkpoint = cancelled
    with pytest.raises(KeyboardInterrupt) as caught:
        with service.store.transaction() as connection:
            service.store._put_document(connection, 'TEST_FIXTURE_products', 'cancelled', {'TEST_FIXTURE': True})
            if phase == 'load':
                bridge.load_verified_bridge_input(service, cert['input_id'], session=session)
            else:
                session.terminal()
    assert caught.value is failure
    assert service.store.get_document('TEST_FIXTURE_products', 'cancelled') is None


def test_private_run_metadata_does_not_rehash_or_reexpand_but_checks_database(archive, service, monkeypatch):
    cap = authenticate(archive)
    cert = bridge.commit_bridge(service, cap)
    assert bridge._state(cap)['run'] is None
    session = cap._session
    original = bridge._stored_matches
    checked = []
    def stored(connection, namespace, key, expected, verification=None):
        checked.append((namespace, key))
        return original(connection, namespace, key, expected, verification)
    monkeypatch.setattr(bridge, '_stored_matches', stored)
    monkeypatch.setattr(bridge, '_verified_run', lambda *a: pytest.fail('METADATA_REEXPANDED_OR_REHASHED_RUN'))
    view = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session)
    assert view['run_metadata'] == {key: archive['run'].get(key) for key in bridge._RUN_METADATA_FIELDS}
    assert ('strategy_experiment_runs', cert['run_id']) in checked
    assert ('candidate_inputs', cert['input_id']) in checked
    assert bridge._state(cap)['run'] is None
    view['run_metadata']['observation_date'] = '1900-01-01'
    audit = cap.audit()
    audit['observation_date'] = '1900-01-01'
    again = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session)
    assert again['run_metadata']['observation_date'] == archive['run']['observation_date']
    assert bridge._state(cap)['run_metadata']['observation_date'] == archive['run']['observation_date']
    session.terminal()


def test_private_run_metadata_is_minted_after_all_original_checks(archive, monkeypatch):
    original = bridge._validate_run
    def invalid(*args, **kwargs):
        original(*args, **kwargs)
        raise ValueError('TEST_FIXTURE_LAST_RUN_CHECK_FAILED')
    monkeypatch.setattr(bridge, '_validate_run', invalid)
    session = bridge.ArchiveVerificationSession()
    with pytest.raises(ValueError, match='LAST_RUN_CHECK_FAILED'):
        authenticate(archive, session=session)
    assert not session.capabilities and not session._verified_views


@pytest.mark.parametrize('changed', ['run', 'binding', 'raw'])
def test_private_run_metadata_never_authorizes_replaced_database_or_originals(archive, service, changed):
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session)
    session.release_cached_archives()
    if changed == 'run':
        saved = service.store.get_document('strategy_experiment_runs', cert['run_id'])
        saved['observation_date'] = '1900-01-01'
        _offline_fixture_document(service, 'strategy_experiment_runs', cert['run_id'], saved)
        with pytest.raises(ValueError, match='BRIDGED_DATABASE_PARENT_CHANGED'):
            bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session)
    elif changed == 'binding':
        saved = service.store.get_document(bridge.RUN_BINDINGS, cert['run_id'])
        saved['input_id'] = 'TEST_FIXTURE_OTHER_INPUT'
        _offline_fixture_document(service, bridge.RUN_BINDINGS, cert['run_id'], saved)
        with pytest.raises(ValueError, match='VERIFIED_VIEW_BINDING_CHANGED'):
            bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session)
    else:
        path = Path(archive['items'][0]['raw']['path'])
        path.write_bytes(path.read_bytes() + b' ')
        with pytest.raises(ValueError, match='PHYSICAL_IDENTITY|REGISTERED_FILE_CHANGED'):
            session.terminal()


def test_private_run_metadata_retention_upgrade_keeps_existing_borrow_guard(archive, service):
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    view = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)
    borrowed = view['run']
    assert session._borrowed_values[id(borrowed)][0] is borrowed
    bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session, retain_input=True)
    session.release_cached_archives()
    assert session._borrowed_values[id(borrowed)][0] is borrowed
    borrowed['results'][bridge.STRATEGY]['counts']['TEST_FIXTURE_MUTATION'] = True
    # Metadata still describes the original; it cannot make the mutation pass.
    metadata = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session)
    assert metadata['run_metadata']['content_hash'] == cert['signal_content_hash']
    with pytest.raises(ValueError, match='BORROWED_VERIFIED_CONTENT_CHANGED'):
        session.terminal()


def test_private_run_metadata_does_not_skip_new_borrow_complete_hash(archive, service):
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session)
    # The private capability's expanded object is not the source of metadata.
    cap = next(iter(session.capabilities.values()))
    bridge._state(cap)['run']['results'][bridge.STRATEGY]['counts']['TEST_FIXTURE_MUTATION'] = 1
    with pytest.raises(ValueError, match='VERIFIED_CAPABILITY_CONTENT_CHANGED|BORROWED_VERIFIED_CONTENT_CHANGED'):
        # Use an uncached view binding so that the original _verified_run path
        # must still validate any expanded object before it can be borrowed.
        del session._verified_views[(id(service.store), bridge.RUN_BINDINGS, cert['run_id'])]
        bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)


def _typed_adapter_fixture(count=129):
    """Small explicit TEST_FIXTURE, with no source capability or real files."""
    from datetime import date
    from stockfish_core.yaogu.contracts import MarketInput, UniverseSnapshot, SecurityRecord, DailyBar
    day = date(2026, 10, 16)
    now = datetime(2026, 10, 16, 9, tzinfo=timezone.utc)
    clocks = dict(event_time=now, source_published_at=now, first_seen_at=now, ingested_at=now)
    securities = tuple(SecurityRecord(instrument_id='TEST_FIXTURE_' + str(i), exchange='SSE', board='MAIN',
        security_type='A_COMMON', listed_on=date(2010, 1, 1), effective_from=day, st=False, suspended=False,
        name='中文😀\\\"\x00\n\r\t\u2028\u2029', **clocks) for i in range(count))
    bars = tuple(DailyBar(instrument_id='TEST_FIXTURE_' + str(i), session=day,
        close=Decimal('10.0010'), turnover_fraction=-0.0, **clocks) for i in range(count))
    return MarketInput(observation_date=day, sessions=(day,), coverage_type='TEST_FIXTURE', bars=bars,
        universe=UniverseSnapshot(snapshot_id='TEST_FIXTURE', securities=securities, complete=True, **clocks),
        funds=({'TEST_FIXTURE': {'price': '10.0', 'unicode': '中文😀\x00\n'}},))


@pytest.mark.parametrize('count', [0, 1, 127, 128, 129, 257])
def test_typed_adapter_batch_boundaries_match_frozen_canonical_bytes(count, monkeypatch):
    from pydantic import TypeAdapter
    from stockfish_core.yaogu.history_artifacts import digest_market_input as reference
    bundle = _typed_adapter_fixture(count)
    canonical = canonical_json(bundle.model_dump(mode='json')).encode('utf-8')
    expected = reference(bundle)
    assert expected == hashlib.sha256(canonical).hexdigest()
    chunks = list(bridge._typed_canonical_chunks(bundle))
    assert b''.join(chunks) == canonical and all(len(chunk) <= 1024**2 for chunk in chunks)
    calls = []
    original = TypeAdapter.dump_python
    def dump(adapter, values, *args, **kwargs):
        calls.append(len(values))
        assert 1 <= len(values) <= 128 and kwargs.get('mode') == 'json'
        return original(adapter, values, *args, **kwargs)
    monkeypatch.setattr(TypeAdapter, 'dump_python', dump)
    assert bridge.digest_market_input(bundle) == expected
    assert calls == ([128] * (count // 128) + ([count % 128] if count % 128 else [])) * 2


def test_typed_adapter_exact_rows_do_not_dump_python_or_expand_parent(monkeypatch):
    from stockfish_core.yaogu.contracts import MarketInput, UniverseSnapshot, DailyBar, SecurityRecord
    from stockfish_core.yaogu.history_artifacts import digest_market_input as reference
    bundle = _typed_adapter_fixture()
    expected = reference(bundle)
    input_dump, universe_dump = MarketInput.model_dump, UniverseSnapshot.model_dump
    def input_envelope(value, *args, **kwargs):
        assert kwargs.get('exclude') == {'bars', 'universe'}
        return input_dump(value, *args, **kwargs)
    def universe_envelope(value, *args, **kwargs):
        assert kwargs.get('exclude') == {'securities'}
        return universe_dump(value, *args, **kwargs)
    monkeypatch.setattr(MarketInput, 'model_dump', input_envelope)
    monkeypatch.setattr(UniverseSnapshot, 'model_dump', universe_envelope)
    monkeypatch.setattr(DailyBar, 'model_dump', lambda *a, **k: pytest.fail('UNNECESSARY_PYTHON_BAR_DUMP'))
    monkeypatch.setattr(SecurityRecord, 'model_dump', lambda *a, **k: pytest.fail('UNNECESSARY_PYTHON_SECURITY_DUMP'))
    assert bridge.digest_market_input(bundle) == expected


def test_typed_adapter_large_single_row_bounds_utf8_chunks_not_row_allocation():
    from stockfish_core.yaogu.history_artifacts import digest_market_input as reference
    bundle = _typed_adapter_fixture(1)
    security = bundle.universe.securities[0].model_copy(update={'name': 'TEST_FIXTURE_' + '😀' * 300000})
    bundle = bundle.model_copy(update={'universe': bundle.universe.model_copy(update={'securities': (security,)})})
    chunks = list(bridge._typed_canonical_chunks(bundle))
    expected = canonical_json(bundle.model_dump(mode='json')).encode()
    assert len(expected) > 1024**2 and b''.join(chunks) == expected
    assert max(map(len, chunks)) <= 1024**2
    assert bridge.digest_market_input(bundle) == reference(bundle)


@pytest.mark.parametrize('turnover', [-0.0, 5e-324, math.nextafter(1.0, 2.0), 1e308])
def test_typed_adapter_precision_offsets_and_control_characters(turnover):
    from datetime import timedelta
    from stockfish_core.yaogu.history_artifacts import digest_market_input as reference
    bundle = _typed_adapter_fixture(1)
    row = bundle.bars[0].model_copy(update={
        'event_time': datetime(2026, 10, 16, 15, tzinfo=timezone(timedelta(hours=8))),
        'close': Decimal('1234567890123.000000000000001'), 'amount_cny': Decimal('1E+25'),
        'volume_shares': None, 'turnover_fraction': turnover, 'comparable_basis': 'TEST_FIXTURE_中文😀\x00\n'})
    changed = bundle.model_copy(update={'bars': (row,)})
    assert bridge.digest_market_input(changed) == reference(changed)


@pytest.mark.parametrize('number', [float('nan'), float('inf'), -float('inf')])
@pytest.mark.parametrize('location', ['envelope', 'row'])
def test_typed_adapter_nonfinite_retains_original_typed_behavior(number, location):
    from stockfish_core.yaogu.history_artifacts import digest_market_input as reference
    bundle = _typed_adapter_fixture(1)
    if location == 'envelope':
        bundle.funds[0]['TEST_FIXTURE']['price'] = number
        assert bundle.model_dump(mode='json')['funds'][0]['TEST_FIXTURE']['price'] is None
        assert bridge.digest_market_input(bundle) == reference(bundle)
    else:
        bundle = bundle.model_copy(update={'bars': (bundle.bars[0].model_copy(update={'turnover_fraction': number}),)})
        with pytest.raises(ValueError):
            reference(bundle)
        with pytest.raises(ValueError):
            bridge.digest_market_input(bundle)
    # This preserves an already-typed serialization rule. Raw archive ingress
    # continues to reject nonfinite tokens before any typed object is accepted.
    with pytest.raises(ValueError):
        bridge._json(b'{"TEST_FIXTURE":NaN}')


def test_typed_adapter_row_subclasses_preserve_frozen_extra_field_dump():
    from stockfish_core.yaogu.contracts import DailyBar, SecurityRecord
    from stockfish_core.yaogu.history_artifacts import digest_market_input as reference
    class ExtraBar(DailyBar):
        test_fixture_extra: str = 'TEST_FIXTURE_BAR'
    class ExtraSecurity(SecurityRecord):
        test_fixture_extra: str = 'TEST_FIXTURE_SECURITY'
    bundle = _typed_adapter_fixture(129)
    bar = ExtraBar.model_validate(bundle.bars[-1].model_dump())
    security = ExtraSecurity.model_validate(bundle.universe.securities[-1].model_dump())
    changed = bundle.model_copy(update={'bars': bundle.bars[:-1] + (bar,), 'universe':
        bundle.universe.model_copy(update={'securities': bundle.universe.securities[:-1] + (security,)})})
    expected = reference(changed)
    assert expected != hashlib.sha256(canonical_json(changed.model_dump(mode='json')).encode()).hexdigest()
    assert bridge.digest_market_input(changed) == expected


@pytest.mark.parametrize('kind', ['input_subclass', 'universe_subclass', 'mixed_dict_row'])
def test_typed_adapter_nonexact_types_use_original_semantics(kind):
    from stockfish_core.yaogu.contracts import MarketInput, UniverseSnapshot
    from stockfish_core.yaogu.history_artifacts import digest_market_input as reference
    class ExtraInput(MarketInput):
        test_fixture_extra: str = 'TEST_FIXTURE_INPUT'
    class ExtraUniverse(UniverseSnapshot):
        test_fixture_extra: str = 'TEST_FIXTURE_UNIVERSE'
    bundle = _typed_adapter_fixture(129)
    if kind == 'input_subclass':
        bundle = ExtraInput.model_validate(bundle.model_dump())
    elif kind == 'universe_subclass':
        bundle = bundle.model_copy(update={'universe': ExtraUniverse.model_validate(bundle.universe.model_dump())})
    else:
        bundle = bundle.model_copy(update={'bars': bundle.bars[:-1] + ({'TEST_FIXTURE_RAW_ROW': Decimal('1.0010')},)})
    assert bridge.digest_market_input(bundle) == reference(bundle)


@pytest.mark.parametrize('kind', ['missing_required', 'unknown_extra', 'row_unserializable',
                                  'envelope_unserializable', 'raw_row_nonfinite', 'invalid_universe'])
def test_typed_adapter_constructed_anomalies_match_reference_result_or_exception(kind):
    from stockfish_core.yaogu.contracts import DailyBar
    from stockfish_core.yaogu.history_artifacts import digest_market_input as reference
    bundle = _typed_adapter_fixture(1)
    if kind == 'missing_required':
        bundle = bundle.model_copy(update={'bars': (DailyBar.model_construct(close=Decimal('10')), )})
    elif kind == 'unknown_extra':
        row = DailyBar.model_construct(**dict(bundle.bars[0].model_dump(), TEST_FIXTURE_UNDECLARED='value'))
        bundle = bundle.model_copy(update={'bars': (row,)})
    elif kind == 'row_unserializable':
        bundle = bundle.model_copy(update={'bars': (bundle.bars[0].model_copy(update={'close': object()}),)})
    elif kind == 'envelope_unserializable':
        bundle = bundle.model_copy(update={'funds': ({'TEST_FIXTURE': object()},)})
    elif kind == 'raw_row_nonfinite':
        bundle = bundle.model_copy(update={'bars': ({'TEST_FIXTURE': float('nan')},)})
    else:
        bundle = bundle.model_copy(update={'universe': None})
    try:
        expected = reference(bundle)
    except Exception as expected_error:
        with pytest.raises(type(expected_error)):
            bridge.digest_market_input(bundle)
    else:
        assert bridge.digest_market_input(bundle) == expected


def test_typed_adapter_raw_validation_still_rejects_extra_and_missing_fields():
    from pydantic import ValidationError
    from stockfish_core.yaogu.contracts import DailyBar, SecurityRecord
    bundle = _typed_adapter_fixture(1)
    for model, row in ((DailyBar, bundle.bars[0]), (SecurityRecord, bundle.universe.securities[0])):
        for malformed in (dict(row.model_dump(), TEST_FIXTURE_EXTRA=True),
                          {k: v for k, v in row.model_dump().items() if k != 'instrument_id'}):
            with pytest.raises(ValidationError):
                model.model_validate(malformed)


@pytest.mark.parametrize('kind', ['fast', 'fallback'])
def test_typed_adapter_initial_cancellation_keeps_original_exception(kind):
    from stockfish_core.yaogu.contracts import MarketInput
    class ExtraInput(MarketInput):
        test_fixture_extra: str = 'TEST_FIXTURE_INPUT'
    bundle = _typed_adapter_fixture(129)
    if kind == 'fallback':
        bundle = ExtraInput.model_validate(bundle.model_dump())
    failure = KeyboardInterrupt('TEST_FIXTURE_TYPED_CANCELLED')
    def cancel():
        raise failure
    with pytest.raises(KeyboardInterrupt) as caught:
        bridge.digest_market_input(bundle, checkpoint=cancel)
    assert caught.value is failure


def test_typed_adapter_cancellation_after_a_batch_propagates_before_next_batch(monkeypatch):
    from pydantic import TypeAdapter
    original = TypeAdapter.dump_python
    batches = []
    failure = RuntimeError('TEST_FIXTURE_TYPED_BATCH_CANCELLED')
    def dump(adapter, values, *args, **kwargs):
        result = original(adapter, values, *args, **kwargs)
        batches.append(len(values))
        return result
    def checkpoint():
        if batches:
            raise failure
    monkeypatch.setattr(TypeAdapter, 'dump_python', dump)
    with pytest.raises(RuntimeError) as caught:
        bridge.digest_market_input(_typed_adapter_fixture(257), checkpoint=checkpoint)
    assert caught.value is failure and batches == [128]


@pytest.mark.parametrize('change', ['nested', 'late_last_bar', 'private_owner', 'history_ref'])
def test_typed_adapter_late_borrowed_change_still_rolls_back_terminal(service, change):
    from stockfish_core.yaogu.history_artifacts import digest_market_input as reference
    bundle = _typed_adapter_fixture(129)
    expected = reference(bundle)
    session = bridge.ArchiveVerificationSession()
    # Explicit TEST_FIXTURE borrowed-value unit seam, never a minted archive
    # proof; original integration cases above cover the actual loader path.
    session._borrowed_values[id(bundle)] = (bundle, expected, True)
    assert session.terminal()['borrowed_values'] == 1
    session.release_cached_archives()
    if change in ('nested', 'late_last_bar'):
        if change == 'nested':
            bundle.funds[0]['TEST_FIXTURE']['price'] = 'changed'
        else:
            object.__setattr__(bundle.bars[-1], 'close', Decimal('11.0010'))
        reason = 'BORROWED_VERIFIED_CONTENT_CHANGED'
    elif change == 'private_owner':
        bundle._history_store = object()
        assert bridge.digest_market_input(bundle) == expected
        reason = 'INLINE_TYPED_INPUT_WITHOUT_HISTORY_OWNER_REQUIRED'
    else:
        object.__setattr__(bundle, 'history_artifact_ref', 'TEST_FIXTURE_LATE_REF')
        reason = 'INLINE_TYPED_INPUT_WITHOUT_HISTORY_OWNER_REQUIRED'
    with pytest.raises(ValueError, match=reason):
        with service.store.transaction() as con:
            service.store._put_document(con, 'TEST_FIXTURE_products', 'typed-late', {'TEST_FIXTURE': True})
            session.terminal()
    assert service.store.get_document('TEST_FIXTURE_products', 'typed-late') is None


def test_typed_adapter_terminal_batch_cancellation_rolls_back(service, monkeypatch):
    from pydantic import TypeAdapter
    from stockfish_core.yaogu.history_artifacts import digest_market_input as reference
    bundle = _typed_adapter_fixture(129)
    expected = reference(bundle)
    original = TypeAdapter.dump_python
    batches = []
    failure = KeyboardInterrupt('TEST_FIXTURE_TERMINAL_TYPED_BATCH_CANCELLED')
    def dump(adapter, values, *args, **kwargs):
        result = original(adapter, values, *args, **kwargs)
        batches.append(len(values))
        return result
    def checkpoint():
        if batches:
            raise failure
    session = bridge.ArchiveVerificationSession(checkpoint=checkpoint)
    session._borrowed_values[id(bundle)] = (bundle, expected, True)
    monkeypatch.setattr(TypeAdapter, 'dump_python', dump)
    with pytest.raises(KeyboardInterrupt) as caught:
        with service.store.transaction() as con:
            service.store._put_document(con, 'TEST_FIXTURE_products', 'typed-cancelled', {'TEST_FIXTURE': True})
            session.terminal()
    assert caught.value is failure and batches == [128]
    assert service.store.get_document('TEST_FIXTURE_products', 'typed-cancelled') is None


def _typed_guard_side_effect_fixture(field, kind):
    """Bypass validation only to exercise an already-borrowed object's guard."""
    from stockfish_core.yaogu.history_artifacts import digest_market_input as reference
    base = _typed_adapter_fixture(2)
    expected = reference(base)
    rows = base.bars if field == 'bars' else base.universe.securities
    value_field = 'close' if field == 'bars' else 'name'
    original_value = getattr(rows[0], value_field)
    changed_value = Decimal('20') if field == 'bars' else 'TEST_FIXTURE_CHANGED_NAME'
    if kind == 'row_subclass':
        model_type = type(rows[0])
        saved = {}
        class SideEffectRow(model_type):
            def model_dump(self, *args, **kwargs):
                value = super().model_dump(*args, **kwargs)
                if self.instrument_id == rows[0].instrument_id:
                    saved['original'] = value[value_field]
                    value[value_field] = str(changed_value)
                    saved['first'] = value
                else:
                    # A batch that dumps both rows before encoding either one
                    # would erase the first row's changed canonical content.
                    saved['first'][value_field] = saved['original']
                return value
        replacement = tuple(SideEffectRow.model_validate(row.model_dump()) for row in rows)
    else:
        def values():
            first = rows[0].model_copy(update={value_field: changed_value})
            yield first
            object.__setattr__(first, value_field, original_value)
            yield rows[1]
        class RepeatedRows:
            def __iter__(self):
                return values()
        replacement = values() if kind == 'generator' else RepeatedRows()
    if field == 'bars':
        object.__setattr__(base, 'bars', replacement)
    else:
        object.__setattr__(base.universe, 'securities', replacement)
    return expected, base


@pytest.mark.parametrize('field', ['bars', 'securities'])
@pytest.mark.parametrize('kind', ['generator', 'repeatable_iterator', 'row_subclass'])
def test_typed_adapter_side_effect_fallback_keeps_reference_and_rejects_late_guard(field, kind):
    from stockfish_core.yaogu.history_artifacts import digest_market_input as reference
    expected, original_order = _typed_guard_side_effect_fixture(field, kind)
    original_hash = reference(original_order)
    assert original_hash != expected
    expected_again, changed = _typed_guard_side_effect_fixture(field, kind)
    assert expected_again == expected
    assert bridge.digest_market_input(changed) == original_hash
    _, chunks_input = _typed_guard_side_effect_fixture(field, kind)
    assert hashlib.sha256(b''.join(bridge._typed_canonical_chunks(chunks_input))).hexdigest() == original_hash
    expected_again, borrowed = _typed_guard_side_effect_fixture(field, kind)
    session = bridge.ArchiveVerificationSession()
    # This unit seam holds the original canonical identity of a normal tuple.
    # No archive capability or provenance is minted by the malformed fixture.
    session._borrowed_values[id(borrowed)] = (borrowed, expected_again, True)
    for _ in range(2):
        with pytest.raises(ValueError, match='BORROWED_VERIFIED_CONTENT_CHANGED'):
            session.terminal()
    assert session.terminal_count == 0


def test_typed_adapter_side_effect_assignment_is_frozen_but_guard_must_handle_bypass():
    from pydantic import ValidationError
    bundle = _typed_adapter_fixture(2)
    with pytest.raises(ValidationError) as caught:
        bundle.bars = iter(bundle.bars)
    assert caught.value.errors()[0]['type'] == 'frozen_instance'
    with pytest.raises(ValidationError) as caught:
        bundle.universe.securities = iter(bundle.universe.securities)
    assert caught.value.errors()[0]['type'] == 'frozen_instance'


@pytest.mark.parametrize('raw', [
    b'{"a":1,"a":2}', b'{"a":1,"\\u0061":2}',
    b'{"TEST_FIXTURE":[{"nested":0,"nested":1}]}',
    b'{"TEST_FIXTURE":{"":0,"":1}}',
    '{"中文":1,"\\u4e2d\\u6587":2}'.encode(),
])
def test_strict_decoder_rejects_every_duplicate_spelling(raw):
    with pytest.raises(ValueError, match='DUPLICATE_JSON_KEY'):
        bridge._json(raw)


@pytest.mark.parametrize('raw', [
    b'{"TEST_FIXTURE":NaN}', b'{"TEST_FIXTURE":Infinity}',
    b'{"TEST_FIXTURE":-Infinity}', b'[{"nested":[NaN]}]',
])
def test_strict_decoder_keeps_nonfinite_rejection(raw):
    with pytest.raises(ValueError, match='NONFINITE_JSON'):
        bridge._json(raw)


def test_strict_decoder_distinct_keys_and_nested_order():
    raw = '{"TEST_FIXTURE":{"a":1,"A":2,"中文":3,"":4},"a":5}'
    assert bridge._json(raw) == json.loads(raw)
    assert list(bridge._json(raw)['TEST_FIXTURE']) == ['a', 'A', '中文', '']


def test_view_seal_clock_comes_from_private_fresh_authentication(archive, service):
    cap = authenticate(archive)
    cert = bridge.commit_bridge(service, cap)
    session = cap._session
    expected = bridge._state(cap)['audit']['original_final_sealed_at']
    view = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, include_archive_seal=True)
    assert view['archive_sealed_at'] == expected
    view['archive_sealed_at'] = '1900-01-01T00:00:00Z'
    public_audit = cap.audit()
    public_audit['original_final_sealed_at'] = view['archive_sealed_at']
    again = bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session, include_archive_seal=True)
    assert again['archive_sealed_at'] == expected
    session.terminal()


@pytest.mark.parametrize('option', [1, 'true', None, []])
def test_seal_metadata_requires_explicit_boolean_opt_in(option):
    with pytest.raises(ValueError, match='INCLUDE_ARCHIVE_SEAL_BOOL_REQUIRED'):
        bridge.authenticated_bridge_view(None, run_id='TEST_FIXTURE',
            session=bridge.ArchiveVerificationSession(), include_archive_seal=option)
