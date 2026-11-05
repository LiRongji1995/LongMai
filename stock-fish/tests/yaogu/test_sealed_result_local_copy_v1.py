"""TEST_FIXTURE-only different-path RESULT copies and late-session trust gates."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone, date
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
from stockfish_core.yaogu import r2_closure_cli as cli
from stockfish_core.yaogu import sealed_result_local_copy_v1 as local
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from tests.support.short_horizon_archive_fixture import fixture_archive
from tests.yaogu.test_short_horizon_archive_bridge_v1 import service, _offline_fixture_document


def write_manifest(path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
    return workflow._ref(path)


def locked_copy(original, path):
    path.write_bytes(Path(original['path']).read_bytes())
    os.chflags(path, path.stat().st_flags | local.IMMUTABLE)
    current = workflow._ref(path)
    assert current['path'] != original['path'] and current['sha256'] == original['sha256']
    assert current['bytes'] == original['bytes'] and current['fp']['st_flags'] & local.IMMUTABLE
    return current


def replace_copy(current, *, change=False):
    path = Path(current['path'])
    os.chflags(path, path.stat().st_flags & ~local.IMMUTABLE)
    raw = path.read_bytes()
    other = path.with_suffix('.TEST_FIXTURE-replaced')
    other.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]) if change else raw)
    os.replace(other, path)
    os.chflags(path, path.stat().st_flags | local.IMMUTABLE)


def clear_fixture_flags(root):
    for path in root.rglob('*'):
        if path.is_file() and not path.is_symlink() and path.stat().st_flags & local.IMMUTABLE:
            os.chflags(path, path.stat().st_flags & ~local.IMMUTABLE)


@pytest.fixture
def copied(tmp_path):
    archive = fixture_archive(tmp_path / 'TEST_FIXTURE-original')
    original = deepcopy(next(item['result'] for item in archive['items']
        if json.loads(Path(item['result']['path']).read_bytes())['endpoint'] == 'market_daily'))
    root = tmp_path / 'TEST_FIXTURE-objects'
    root.mkdir()
    current = locked_copy(original, root / 'result.json')
    Path(original['path']).unlink()
    value = dict(schema_version=local.PROFILE, copy_root_reference=local.root_reference(root),
        archives=[dict(archive_final_sha256=archive['final_sha256'],
            results=[dict(original_reference=original, current_reference=current)])])
    manifest = write_manifest(tmp_path / 'TEST_FIXTURE-local-copy.json', value)
    try:
        yield archive, original, current, value, manifest
    finally:
        clear_fixture_flags(tmp_path)


def authenticate(archive, manifest=None, **kwargs):
    session = bridge.ArchiveVerificationSession(**({'local_copy_manifest_ref': manifest} if manifest else {}))
    return bridge.authenticate_archive(archive['job'], expected_final_sha256=archive['final_sha256'],
        session=session, allow_test_fixture=True, **kwargs)


def test_different_path_explicit_authentication_exact_bytes_and_default_rejection(copied, service):
    archive, original, current, value, manifest = copied
    with pytest.raises((ValueError, FileNotFoundError)):
        authenticate(archive)
    with pytest.raises(ValueError):
        bridge.ArchiveVerificationSession(restoration_manifest_ref=manifest)
    cap = authenticate(archive, manifest)
    protocol = local.protocol_identity(manifest, value['copy_root_reference'])
    audit = cap.audit()
    assert audit['source_protocol'] == protocol
    assert audit['input_hash'] == archive['run']['input_hash']
    assert audit['run_document_hash'] == bridge.digest_json_stream(archive['run'])
    assert audit['verification_budget']['limits']['seconds'] == 300
    proof = dict(original_reference=original, current_reference=current,
        original_physical_identity_status='LOST_NOT_RESTORED', proof_mode='SEALED_BYTES_AND_LOCAL_COPY_CONTENT_REFERENCE')
    assert audit['verification_budget']['result_local_copies'] == [proof]
    assert 'result_restorations' not in audit['verification_budget']
    with pytest.raises(ValueError, match='EXPLICIT_RESULT_LOCAL_COPY_COMMIT_REQUIRED'):
        bridge.commit_bridge(service, cap)
    with pytest.raises(ValueError):
        bridge.commit_bridge(service, cap, restoration_manifest_ref=manifest)
    cert = bridge.commit_bridge(service, cap, local_copy_manifest_ref=manifest)
    assert cert['source_protocol'] == protocol and cert['original_result_physical_ownership'] == 'LOST_NOT_RESTORED'
    assert service.store.get_document('strategy_experiment_runs', cert['run_id']) == archive['run']
    ordinary = SimpleNamespace(store=service.store)
    with pytest.raises(ValueError, match='EXPLICIT_MATCHING_RESULT_LOCAL_COPY_PROTOCOL_REQUIRED'):
        bridge.saved_csv_export(ordinary, archive['run'], archive['run']['strategy_id'], 'all')
    with pytest.raises(ValueError, match='EXPLICIT_MATCHING_RESULT_LOCAL_COPY_PROTOCOL_REQUIRED'):
        bridge.require_verified_bridge(service, cert['run_id'])
    for selected in bridge.CSV_SELECTIONS:
        assert bridge.saved_csv_export(service, archive['run'], archive['run']['strategy_id'], selected) == Path(
            archive['artifacts'][selected + '.csv']['path']).read_bytes().decode('utf-8')
    session = bridge.ArchiveVerificationSession(local_copy_manifest_ref=manifest)
    assert bridge.require_verified_bridge(service, cert['run_id'], session=session)['source_protocol'] == protocol
    with service.store.transaction(write=False) as connection:
        sources = [json.loads(row['payload']) for row in connection.execute('SELECT payload FROM documents WHERE namespace=?', (bridge.SOURCES,))]
    saved = next(row for row in sources if row.get('result_local_copy'))
    assert saved['originals']['result'] == original and saved['result_local_copy'] == proof
    session.terminal()


@pytest.mark.parametrize('change', ['same_path', 'wrong_sha', 'outside_root', 'not_immutable', 'extra_field',
    'duplicate_archive', 'duplicate_result', 'root_inode', 'manifest_inside_root'])
def test_local_manifest_scope_and_shape_refusals(copied, tmp_path, change):
    archive, original, current, value, manifest = copied
    value = deepcopy(value)
    entry = value['archives'][0]['results'][0]
    if change == 'same_path': entry['current_reference']['path'] = original['path']
    elif change == 'wrong_sha': entry['current_reference']['sha256'] = '0' * 64
    elif change == 'outside_root': entry['current_reference']['path'] = str(tmp_path / 'outside.json')
    elif change == 'not_immutable': entry['current_reference']['fp']['st_flags'] &= ~local.IMMUTABLE
    elif change == 'extra_field': value['skip_raw'] = True
    elif change == 'duplicate_archive': value['archives'].append(deepcopy(value['archives'][0]))
    elif change == 'duplicate_result': value['archives'][0]['results'].append(deepcopy(entry))
    elif change == 'root_inode': value['copy_root_reference']['fp']['st_ino'] += 1
    path = tmp_path / 'TEST_FIXTURE-invalid-map.json'
    if change == 'manifest_inside_root': path = Path(value['copy_root_reference']['path']) / 'manifest.json'
    changed = write_manifest(path, value)
    if change in {'not_immutable', 'root_inode'}:
        authenticate(archive, changed)
        replace_copy(current, change=True)
    with pytest.raises(ValueError): authenticate(archive, changed)


@pytest.mark.parametrize('role', ['raw', 'receipt', 'producer', 'control', 'artifact'])
def test_only_original_sealed_core_result_may_be_mapped(copied, tmp_path, role):
    archive, original, current, value, manifest = copied
    chosen = (archive['items'][0][role] if role in {'raw', 'receipt'} else
        workflow._ref(Path(bridge.__file__).with_name('provider_requests.py')) if role == 'producer' else
        archive['final']['configuration'] if role == 'control' else archive['artifacts']['all.csv'])
    copied_ref = locked_copy(chosen, Path(value['copy_root_reference']['path']) / 'wrong-role.json')
    value['archives'][0]['results'].append(dict(original_reference=chosen, current_reference=copied_ref))
    changed = write_manifest(tmp_path / 'TEST_FIXTURE-wrong-role-map.json', value)
    with pytest.raises(ValueError, match='ONLY_EXACT_SEALED_CORE_RESULT_ALLOWED'): authenticate(archive, changed)


@pytest.mark.parametrize('change', ['same_bytes_inode', 'changed_bytes', 'manifest_bytes', 'copy_root'])
def test_whole_source_and_root_terminal_guards(copied, change, tmp_path):
    archive, original, current, value, manifest = copied
    cap = authenticate(archive, manifest)
    if change in {'same_bytes_inode', 'changed_bytes'}: replace_copy(current, change=change == 'changed_bytes')
    elif change == 'manifest_bytes': Path(manifest['path']).write_bytes(Path(manifest['path']).read_bytes() + b' ')
    else: Path(value['copy_root_reference']['path']).rename(tmp_path / 'TEST_FIXTURE-renamed-root')
    if change == 'same_bytes_inode':
        bridge._state(cap)['session'].terminal()
        replace_copy(current, change=True)
    with pytest.raises((ValueError, FileNotFoundError)): bridge._state(cap)['session'].terminal()


def test_manifest_first_duplicate_keys_and_mutually_exclusive_authority(copied, tmp_path):
    archive, original, current, value, manifest = copied
    path = tmp_path / 'TEST_FIXTURE-duplicate.json'
    path.write_text('{"schema_version":"bad","schema_version":"SEALED_RESULT_LOCAL_COPY_V1"}')
    with pytest.raises(ValueError, match='DUPLICATE_JSON_KEY'):
        bridge.ArchiveVerificationSession(local_copy_manifest_ref=workflow._ref(path))
    with pytest.raises(ValueError, match='MUTUALLY_EXCLUSIVE'):
        bridge.ArchiveVerificationSession(local_copy_manifest_ref=manifest, restoration_manifest_ref=manifest)


def test_global_copy_reverse_uniqueness_and_same_original_reuse(copied, tmp_path):
    archive, original, current, value, manifest = copied
    second = deepcopy(value['archives'][0])
    second['archive_final_sha256'] = '0' * 64
    value['archives'].append(second)
    reused = write_manifest(tmp_path / 'TEST_FIXTURE-same-original-reused.json', value)
    bridge.ArchiveVerificationSession(local_copy_manifest_ref=reused).terminal()
    second['results'][0]['original_reference']['path'] = str(tmp_path / 'TEST_FIXTURE-other-original.json')
    alias = write_manifest(tmp_path / 'TEST_FIXTURE-cross-scope-alias.json', value)
    with pytest.raises(ValueError, match='CROSS_SCOPE_COPY_PATH_ALIAS'):
        bridge.ArchiveVerificationSession(local_copy_manifest_ref=alias)


def test_private_view_and_borrow_proofs_cannot_cross_protocol(copied, service):
    archive, original, current, value, manifest = copied
    cert = bridge.commit_bridge(service, authenticate(archive, manifest), local_copy_manifest_ref=manifest)
    session = bridge.ArchiveVerificationSession(local_copy_manifest_ref=manifest)
    bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)
    proof = session._verified_views[bridge._verified_view_key(service, bridge.RUN_BINDINGS, cert['run_id'], session)]
    ordinary = bridge.ArchiveVerificationSession()
    ordinary._verified_views[(id(service.store), bridge.RUN_BINDINGS, cert['run_id'])] = proof
    with pytest.raises(ValueError):
        bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=ordinary, borrow_run=True)
    with pytest.raises(ValueError): bridge._verified_second_run_decode(proof, ordinary)
    proof['source_protocol_context'] = None
    with pytest.raises(ValueError, match='SOURCE_PROTOCOL_CHANGED'):
        bridge.load_verified_bridge_input(service, cert['input_id'], session=session)


@pytest.mark.parametrize('strip_all', [False, True])
def test_csv_certificate_marker_removal_cannot_downgrade(copied, service, strip_all):
    archive, original, current, value, manifest = copied
    cert = bridge.commit_bridge(service, authenticate(archive, manifest), local_copy_manifest_ref=manifest)
    cert.pop('source_protocol')
    if strip_all:
        cert.pop('original_result_physical_ownership')
        cert['verification_budget'].pop('source_protocol')
        cert['verification_budget'].pop('result_local_copies')
    _offline_fixture_document(service, bridge.CERTIFICATES, cert['bridge_id'], cert)
    for ns, key in [(bridge.RUN_BINDINGS, cert['run_id']), (bridge.INPUT_BINDINGS, cert['input_id'])] + [
        (bridge.CSV_EXPORTS, cert['run_id'] + ':' + selection) for selection in bridge.CSV_SELECTIONS]:
        payload = service.store.get_document(ns, key); payload.pop('source_protocol', None)
        _offline_fixture_document(service, ns, key, payload)
    with pytest.raises(ValueError, match='PROTOCOL_CANNOT_BE_REMOVED|BRIDGE_IDENTITY_MISMATCH'):
        bridge.saved_csv_export(SimpleNamespace(store=service.store), archive['run'], archive['run']['strategy_id'], 'all')


@pytest.mark.parametrize('tamper', [False, True])
def test_import_last_terminal_checks_copy_content(copied, service, monkeypatch, tamper):
    archive, original, current, value, manifest = copied
    cap = authenticate(archive, manifest)
    service.store.put_document('TEST_FIXTURE_PROTECTED', 'prior', {'preserve': True})
    put = service.store._put_document
    changed = False
    def observed(connection, namespace, document_id, value):
        nonlocal changed
        result = put(connection, namespace, document_id, value)
        if namespace == bridge.CERTIFICATES and not changed:
            changed = True; replace_copy(current, change=tamper)
        return result
    monkeypatch.setattr(service.store, '_put_document', observed)
    if tamper:
        with pytest.raises(ValueError): bridge.commit_bridge(service, cap, local_copy_manifest_ref=manifest)
        with service.store.transaction(write=False) as connection:
            assert [(row['namespace'], row['document_id']) for row in connection.execute('SELECT namespace,document_id FROM documents')] == [('TEST_FIXTURE_PROTECTED', 'prior')]
    else:
        assert bridge.commit_bridge(service, cap, local_copy_manifest_ref=manifest)['run_id'] == archive['run']['run_id']
