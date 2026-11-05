"""TEST_FIXTURE byte recovery: legacy provenance stays historical; byte-equivalent files remain usable."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
from stockfish_core.yaogu import r2_closure_cli as cli
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from stockfish_core.yaogu.sealed_result_restoration_v1 import PROFILE, protocol_identity
from tests.support.short_horizon_archive_fixture import fixture_archive
from tests.yaogu.test_short_horizon_archive_bridge_v1 import service, _offline_fixture_document


def ref(path):
    return workflow._ref(path)


def write_manifest(path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
    return ref(path)


def rematerialize(original):
    path = Path(original['path']); raw = path.read_bytes()
    replacement = path.with_name(path.name + '.TEST_FIXTURE-recovered')
    replacement.write_bytes(raw); os.replace(replacement, path)
    current = ref(path)
    assert current['sha256'] == original['sha256'] and current['bytes'] == original['bytes']
    assert current['fp'] != original['fp']
    return current


@pytest.fixture
def recovered(tmp_path):
    archive = fixture_archive(tmp_path / 'TEST_FIXTURE-original')
    item = next(i for i in archive['items'] if json.loads(Path(i['result']['path']).read_bytes())['endpoint'] == 'market_daily')
    original = deepcopy(item['result']); current = rematerialize(original)
    value = dict(schema_version=PROFILE, archives=[dict(archive_final_sha256=archive['final_sha256'],
        results=[dict(original_reference=original, current_reference=current)])])
    manifest = write_manifest(tmp_path / 'TEST_FIXTURE-restoration.json', value)
    return archive, original, current, value, manifest


def authenticate(archive, manifest=None, **kwargs):
    options = {'restoration_manifest_ref': manifest} if manifest is not None else {}
    session = bridge.ArchiveVerificationSession(**options)
    return bridge.authenticate_archive(archive['job'], expected_final_sha256=archive['final_sha256'],
        session=session, allow_test_fixture=True, **kwargs)


def test_explicit_result_restoration_preserves_loss_and_complete_originals(recovered, service):
    archive, original, current, value, manifest = recovered
    assert authenticate(archive).audit()['run_document_hash'] == bridge.digest_json_stream(archive['run'])
    cap = authenticate(archive, manifest)
    audit = cap.audit(); protocol = protocol_identity(manifest)
    assert audit['source_protocol'] == protocol
    assert audit['original_result_physical_ownership'] == 'LOST_NOT_RESTORED'
    assert audit['run_document_hash'] == bridge.digest_json_stream(archive['run'])
    assert audit['input_hash'] == archive['run']['input_hash']
    assert audit['verification_budget']['source_protocol'] == protocol
    assert audit['verification_budget']['result_restorations'] == [dict(original_reference=original,
        current_reference=current, original_physical_identity_status='LOST_NOT_RESTORED',
        proof_mode='SEALED_BYTES_AND_CURRENT_CONTENT_REFERENCE')]
    with pytest.raises(ValueError, match='EXPLICIT_RESULT_RESTORATION_COMMIT_REQUIRED'):
        bridge.commit_bridge(service, cap)
    cert = bridge.commit_bridge(service, cap, restoration_manifest_ref=manifest)
    assert cert['source_protocol'] == protocol
    assert service.store.get_document('strategy_experiment_runs', cert['run_id']) == archive['run']
    # An ordinary service is not implicitly authorized by the stored JSON.
    ordinary = SimpleNamespace(store=service.store)
    with pytest.raises(ValueError, match='EXPLICIT_MATCHING_RESULT_RESTORATION_PROTOCOL_REQUIRED'):
        bridge.saved_csv_export(ordinary, archive['run'], archive['run']['strategy_id'], 'all')
    assert bridge.saved_csv_export(service, archive['run'], archive['run']['strategy_id'], 'all') == Path(archive['artifacts']['all.csv']['path']).read_bytes().decode('utf-8')
    with pytest.raises(ValueError, match='EXPLICIT_MATCHING_RESULT_RESTORATION_PROTOCOL_REQUIRED'):
        bridge.require_verified_bridge(service, cert['run_id'])
    session = bridge.ArchiveVerificationSession(restoration_manifest_ref=manifest)
    verified = bridge.require_verified_bridge(service, cert['run_id'], session=session)
    assert verified['source_protocol'] == protocol
    with service.store.transaction(write=False) as connection:
        sources = [json.loads(r['payload']) for r in connection.execute('SELECT payload FROM documents WHERE namespace=?', (bridge.SOURCES,))]
    proof = next(x for x in sources if x.get('result_restoration'))
    assert proof['originals']['result'] == original and proof['result_restoration']['current_reference'] == current
    assert original['fp'] != current['fp']
    session.terminal()


@pytest.mark.parametrize('change', ['wrong_version','extra_field','empty_archives','duplicate_archive','empty_results',
    'duplicate_result','different_path','different_sha','different_size','same_fp','extra_entry_field'])
def test_malformed_manifest_rejected_before_authority(recovered, tmp_path, change):
    archive, original, current, value, manifest = recovered
    value = deepcopy(value); scope = value['archives'][0]; entry = scope['results'][0]
    if change == 'wrong_version': value['schema_version'] = 'V1'
    elif change == 'extra_field': value['skip_physical'] = True
    elif change == 'empty_archives': value['archives'] = []
    elif change == 'duplicate_archive': value['archives'].append(deepcopy(scope))
    elif change == 'empty_results': scope['results'] = []
    elif change == 'duplicate_result': scope['results'].append(deepcopy(entry))
    elif change == 'different_path': entry['current_reference']['path'] += '-other'
    elif change == 'different_sha': entry['current_reference']['sha256'] = '0' * 64
    elif change == 'different_size': entry['current_reference']['bytes'] += 1; entry['current_reference']['fp']['st_size'] += 1
    elif change == 'same_fp': entry['current_reference'] = deepcopy(original)
    else: entry['role'] = 'raw'
    changed = write_manifest(tmp_path / 'TEST_FIXTURE-bad-map.json', value)
    if change == 'same_fp':
        authenticate(archive, changed)
        p = Path(current['path']); raw = p.read_bytes(); p.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(ValueError): authenticate(archive, changed)


@pytest.mark.parametrize('kind', ['raw','receipt','producer','core_artifact','configuration'])
def test_map_cannot_authorize_non_result_roles(recovered, tmp_path, kind):
    archive, original, current, value, manifest = recovered
    if kind in {'raw','receipt'}: target = archive['items'][0][kind]
    elif kind == 'producer': target = archive['registry'][0]
    elif kind == 'core_artifact': target = archive['artifacts']['all.csv']
    else: target = archive['final']['configuration']
    fake = deepcopy(target); fake['fp']['st_ino'] += 1
    value['archives'][0]['results'].append(dict(original_reference=target, current_reference=fake))
    changed = write_manifest(tmp_path / 'TEST_FIXTURE-wrong-role.json', value)
    with pytest.raises(ValueError, match='ONLY_EXACT_SEALED_CORE_RESULT_ALLOWED'):
        authenticate(archive, changed)


def test_changed_old_ref_or_unlisted_required_recovery_cannot_pass(recovered, tmp_path):
    archive, original, current, value, manifest = recovered
    value['archives'][0]['results'][0]['original_reference']['fp']['st_ino'] += 1
    changed = write_manifest(tmp_path / 'TEST_FIXTURE-old-ref.json', value)
    authenticate(archive, changed)
    other = archive['items'][0]['result']; rematerialize(other)
    authenticate(archive, manifest)
    p = Path(other['path']); raw = p.read_bytes(); p.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(ValueError): authenticate(archive, manifest)


def test_scoped_manifest_other_archive_is_not_authority(recovered, tmp_path):
    archive, original, current, value, manifest = recovered
    value['archives'][0]['archive_final_sha256'] = '0' * 64
    changed = write_manifest(tmp_path / 'TEST_FIXTURE-wrong-seal.json', value)
    assert authenticate(archive, changed).audit()['verification_budget']['result_restorations'] == []
    p=Path(current['path']);raw=p.read_bytes();p.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
    with pytest.raises(ValueError): authenticate(archive, changed)


@pytest.mark.parametrize('kind', ['raw','receipt','configuration','core_artifact'])
def test_valid_recovery_accepts_same_bytes_and_retains_other_role_content_checks(recovered, kind):
    archive, original, current, value, manifest = recovered
    target = (archive['items'][0][kind] if kind in {'raw','receipt'} else
              archive['final']['configuration'] if kind == 'configuration' else archive['artifacts']['all.csv'])
    rematerialize(target)
    authenticate(archive, manifest)
    p = Path(target['path']); raw = p.read_bytes(); p.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(ValueError): authenticate(archive, manifest)


@pytest.mark.parametrize('mutation', ['changed_bytes','same_bytes_new_inode','manifest_changed'])
def test_all_recovered_bytes_and_map_remain_required_terminal_sources(recovered, mutation):
    archive, original, current, value, manifest = recovered
    cap = authenticate(archive, manifest); session = bridge._state(cap)['session']
    if mutation == 'changed_bytes':
        p = Path(current['path']); raw = p.read_bytes(); p.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    elif mutation == 'same_bytes_new_inode': rematerialize(current)
    else:
        p = Path(manifest['path']); p.write_bytes(p.read_bytes() + b' ')
    if mutation == 'same_bytes_new_inode':
        session.terminal()
        p = Path(current['path']); raw = p.read_bytes(); p.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(ValueError): session.terminal()


def test_live_manifest_mutation_or_symlink_refused(recovered, tmp_path):
    archive, original, current, value, manifest = recovered
    session = bridge.ArchiveVerificationSession(restoration_manifest_ref=manifest)
    session._restoration.archives[archive['final_sha256']][original['path']]['current_reference']['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='IN_MEMORY_MANIFEST_CHANGED'): session.terminal()
    p = Path(current['path']); other = tmp_path / 'TEST_FIXTURE-copy'; other.write_bytes(p.read_bytes())
    p.unlink(); p.symlink_to(other)
    with pytest.raises(ValueError): authenticate(archive, manifest)


def test_wrong_protocol_default_and_cache_proofs_cannot_be_mixed(recovered, service):
    archive, original, current, value, manifest = recovered
    cap = authenticate(archive, manifest); cert = bridge.commit_bridge(service, cap, restoration_manifest_ref=manifest)
    session = bridge.ArchiveVerificationSession(restoration_manifest_ref=manifest)
    view = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)
    key = bridge._verified_view_key(service, bridge.RUN_BINDINGS, cert['run_id'], session)
    proof = session._verified_views[key]
    assert key[-1] == bridge.digest_json_stream(protocol_identity(manifest))
    ordinary = bridge.ArchiveVerificationSession()
    ordinary._verified_views[(id(service.store), bridge.RUN_BINDINGS, cert['run_id'])] = proof
    with pytest.raises(ValueError):
        bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=ordinary, borrow_run=True)
    with pytest.raises(ValueError): bridge._verified_second_run_decode(proof, ordinary)
    proof['source_protocol_context'] = None
    with pytest.raises(ValueError, match='SOURCE_PROTOCOL_CHANGED'):
        bridge.load_verified_bridge_input(service, cert['input_id'], session=session)




def test_cli_requires_explicit_protocol_and_manifest_pin(tmp_path):
    args = ['r2-archive-verify', str(tmp_path), '--expected-final-sha256', '0'*64]
    partial = cli.parser().parse_args(args + ['--source-protocol', PROFILE])
    with pytest.raises(ValueError, match='EXPLICIT_SOURCE_PROTOCOL_AND_PINNED_MANIFEST_REQUIRED'):
        cli.execute(partial)
    parsed = cli.parser().parse_args(args + ['--source-protocol', PROFILE,
        '--restoration-manifest', str(tmp_path/'map'), '--restoration-manifest-sha256', '0'*64])
    assert parsed.source_protocol == PROFILE and parsed.restoration_manifest_sha256 == '0'*64
    ordinary = cli.parser().parse_args(args)
    assert ordinary.source_protocol is ordinary.restoration_manifest is ordinary.restoration_manifest_sha256 is None


@pytest.mark.parametrize('tamper', [False, True])
def test_import_transaction_last_terminal_checks_content(recovered, service, monkeypatch, tamper):
    archive, original, current, value, manifest = recovered
    cap = authenticate(archive, manifest)
    service.store.put_document('TEST_FIXTURE_PROTECTED', 'prior', {'preserve': True})
    put = service.store._put_document; changed = False
    def observed_put(connection, namespace, document_id, value):
        nonlocal changed
        result = put(connection, namespace, document_id, value)
        if namespace == bridge.CERTIFICATES and not changed:
            changed = True; rematerialize(current)
            if tamper:
                p = Path(current['path']); raw = p.read_bytes(); p.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        return result
    monkeypatch.setattr(service.store, '_put_document', observed_put)
    if tamper:
        with pytest.raises(ValueError):
            bridge.commit_bridge(service, cap, restoration_manifest_ref=manifest)
        with service.store.transaction(write=False) as connection:
            assert [(r['namespace'], r['document_id']) for r in connection.execute('SELECT namespace,document_id FROM documents')] == [('TEST_FIXTURE_PROTECTED', 'prior')]
    else:
        certificate = bridge.commit_bridge(service, cap, restoration_manifest_ref=manifest)
        assert certificate['run_id'] == archive['run']['run_id']
    assert changed










def test_current_metadata_time_does_not_replace_source_cutoff(recovered, service, tmp_path):
    archive, original, current, value, manifest = recovered
    altered = deepcopy(value)
    future_ns = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp() * 10**9)
    for scope in altered['archives']:
        for result in scope['results']:
            result['current_reference']['fp']['st_ctime_ns'] = future_ns
            result['current_reference']['fp']['st_mtime_ns'] = future_ns
    reference = write_manifest(tmp_path/'TEST_FIXTURE-metadata-only.json', altered)
    cap = authenticate(archive, reference)
    cert = bridge.commit_bridge(service, cap, restoration_manifest_ref=reference)
    saved = service.store.get_document('strategy_experiment_runs', cert['run_id'])
    assert saved == archive['run']
    assert saved['decision_cutoff'] == archive['run']['decision_cutoff']
    path = Path(current['path']); raw = path.read_bytes()
    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(ValueError):
        bridge._state(cap)['session'].terminal()


def test_default_capability_cannot_be_rebound_to_another_source_protocol(recovered):
    archive,original,current,value,manifest=recovered
    cap=authenticate(archive,manifest);session=bridge._state(cap)['session']
    session._restoration_reference=None
    with pytest.raises(ValueError,match='SOURCE_PROTOCOL_CHANGED'):
        cap.audit()


@pytest.mark.parametrize('strip_every_marker',[False,True])
def test_csv_cannot_silently_downgrade_when_protocol_metadata_is_removed(recovered,service,strip_every_marker):
    archive,original,current,value,manifest=recovered
    cap=authenticate(archive,manifest)
    cert=bridge.commit_bridge(service,cap,restoration_manifest_ref=manifest)
    cert.pop('source_protocol')
    if strip_every_marker:
        cert.pop('original_result_physical_ownership')
        cert['verification_budget'].pop('source_protocol')
        cert['verification_budget'].pop('result_restorations')
    _offline_fixture_document(service,bridge.CERTIFICATES,cert['bridge_id'],cert)
    for ns,key in [(bridge.RUN_BINDINGS,cert['run_id']),(bridge.INPUT_BINDINGS,cert['input_id'])]+[
            (bridge.CSV_EXPORTS,cert['run_id']+':'+selection) for selection in bridge.CSV_SELECTIONS]:
        payload=service.store.get_document(ns,key);payload.pop('source_protocol',None)
        _offline_fixture_document(service,ns,key,payload)
    ordinary=SimpleNamespace(store=service.store)
    with pytest.raises(ValueError,match='CSV_RESTORED_SOURCE_PROTOCOL_CANNOT_BE_REMOVED|CSV_SOURCE_PROTOCOL_BRIDGE_IDENTITY_MISMATCH'):
        bridge.saved_csv_export(ordinary,archive['run'],archive['run']['strategy_id'],'all')
