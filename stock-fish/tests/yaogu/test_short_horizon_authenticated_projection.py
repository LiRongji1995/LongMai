"""Invocation-only optimizations; isolated TEST_FIXTURE originals and databases.

No archived source is executed, no network is used, and none of these synthetic
local seals represents vendor evidence or an investment validation.
"""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.candidate_service import CandidateService
from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from tests.support.short_horizon_archive_fixture import reseal
from tests.yaogu.test_short_horizon_archive_bridge_v1 import (
    archive, service, authenticate, _offline_fixture_document,
)


def _repack_fixture_run(archive, *, empty=False, encoding='utf-8', prefix=b'', duplicate=False):
    """Create a new explicitly synthetic seal, preserving all real validators."""
    run = deepcopy(archive['run'])
    run['TEST_FIXTURE_JSON_TYPES'] = {'z': '中文😀\u0000\n\t', 'a': -0.0,
        'number': 5e-324, 'huge': 2**255 - 1, 'nested': {'z': 1, 'a': 2}}
    if empty:
        result = run['results'][bridge.STRATEGY]
        result.update(items=[], eligible_pool_ids=[], research_selected_ids=[])
        result['counts'] = {key: 0 for key in result['counts']}
        selection = run['selection_results'][result['selection_policy_id']]
        selection.update(selected_ids=[], count=0)
    run['content_hash'] = bridge.digest_json_stream({key: value for key, value in run.items() if key != 'content_hash'})
    text = json.dumps(run, ensure_ascii=False, allow_nan=False)
    if duplicate:
        text = '{"TEST_FIXTURE_DUPLICATE":1,"TEST_FIXTURE_DUPLICATE":1,' + text[1:]
    path = Path(archive['artifacts']['canonical-run.json.gz']['path'])
    path.write_bytes(gzip.compress(prefix + text.encode(encoding), mtime=0))
    archive['artifacts']['canonical-run.json.gz'] = workflow._ref(path)
    class Encoder(CandidateService):
        def strategy_experiment(self, run_id):
            assert run_id == run['run_id']
            return run
    encoder = object.__new__(Encoder)
    for name in bridge._EXPORTS:
        path = Path(archive['artifacts'][name]['path'])
        rendered = (encoder._format_strategy_experiment_csv(run, strategy_id=bridge.STRATEGY,
            selection=name[:-4]) if name.endswith('.csv') else
            encoder.export_strategy_experiment_json(run['run_id'], strategy_id=bridge.STRATEGY,
                selection='observation'))
        path.write_bytes(rendered.encode('utf-8'))
        archive['artifacts'][name] = workflow._ref(path)
    manifest = json.loads(Path(archive['artifacts']['manifest.json']['path']).read_bytes())
    manifest['artifacts'] = {key: value for key, value in archive['artifacts'].items() if key != 'manifest.json'}
    manifest['counts'] = deepcopy(run['results'][bridge.STRATEGY]['counts'])
    manifest['canonical_content_hash'] = run['content_hash']
    manifest['official_exports'] = {name: dict(source_content_hash=run['content_hash'],
        reference=archive['artifacts'][name]) for name in bridge._EXPORTS}
    archive['run'] = run
    reseal(archive, manifest=manifest)


def _registered(archive, service, *, retain=False):
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session, retain_input=retain)
    session.release_cached_archives()
    proof = session._verified_views[(id(service.store), bridge.RUN_BINDINGS, cert['run_id'])]
    return cert, session, proof


def test_projection_nonempty_keeps_default_shape_and_avoids_any_run_decode(archive, service, monkeypatch):
    cert, session, proof = _registered(archive, service)
    assert proof['pool_nonempty'] is True
    baseline = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session)
    assert set(baseline) == {'credential', 'run_metadata', 'input_metadata'}
    assert 'pending_run_projections' not in session.audit()
    def forbidden(*args, **kwargs):
        pytest.fail('NONEMPTY_PROJECTION_MUST_NOT_MATERIALIZE_RUN')
    for name in ('_verified_run', '_decompress', '_verified_second_run_decode'):
        monkeypatch.setattr(bridge, name, forbidden)
    view = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_if_empty=True)
    assert view['pool_nonempty'] is True and view['run'] is None
    assert not session.capabilities and not session._borrowed_values
    view['pool_nonempty'] = False
    view['run_metadata']['observation_date'] = '1900-01-01'
    again = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_if_empty=True)
    assert again['pool_nonempty'] is True and again['run_metadata'] == baseline['run_metadata']
    audit = session.terminal()
    assert audit['borrowed_values'] == 0 and audit['borrowed_value_identities'] == []
    assert audit['pending_run_projections'] == [dict(run_id=cert['run_id'], bridge_id=cert['bridge_id'],
        input_id=cert['input_id'], input_hash=cert['input_hash'], signal_content_hash=cert['signal_content_hash'],
        run_document_hash=proof['run_hash'], pool_nonempty=True)]
    audit['pending_run_projections'][0]['pool_nonempty'] = False
    assert session.audit()['pending_run_projections'][0]['pool_nonempty'] is True


def test_projection_actual_empty_still_borrows_entire_authenticated_run(archive, service):
    _repack_fixture_run(archive, empty=True)
    cert, session, proof = _registered(archive, service)
    assert proof['pool_nonempty'] is False
    view = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_if_empty=True)
    assert view['pool_nonempty'] is False and view['run'] == archive['run']
    assert view['run']['results'][bridge.STRATEGY]['items'] == []
    assert session._borrowed_values[id(view['run'])][0] is view['run']
    audit = session.terminal()
    assert audit['pending_run_projections'] == [] and audit['borrowed_values'] == 1
    assert audit['borrowed_value_identities'] == [dict(kind='run', document_id=cert['run_id'],
        content_hash=proof['run_hash'], bridge_id=cert['bridge_id'], run_id=cert['run_id'], input_id=cert['input_id'])]
    view['run']['results'][bridge.STRATEGY]['counts']['TEST_FIXTURE_CHANGED'] = 1
    session.release_cached_archives()
    with pytest.raises(ValueError, match='BORROWED_VERIFIED_CONTENT_CHANGED'):
        session.terminal()


@pytest.mark.parametrize('arguments', [dict(run_id='TEST_FIXTURE', borrow_if_empty=1),
    dict(run_id='TEST_FIXTURE', borrow_if_empty=None), dict(run_id='TEST_FIXTURE', borrow_if_empty='true'),
    dict(input_id='TEST_FIXTURE', borrow_if_empty=True),
    dict(run_id='TEST_FIXTURE', borrow_if_empty=True, borrow_run=True)])
def test_projection_requires_exact_bool_exclusive_run_optin(arguments):
    with pytest.raises(ValueError, match='BORROW_IF_EMPTY_REQUIRES_EXCLUSIVE_RUN_VIEW'):
        bridge.authenticated_bridge_view(None, session=bridge.ArchiveVerificationSession(), **arguments)


def test_projection_precise_borrowed_identities_include_objects_loaded_before_optin(archive, service):
    cert, session, proof = _registered(archive, service, retain=True)
    bundle = bridge.load_verified_bridge_input(service, cert['input_id'], session=session)
    run = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)['run']
    assert 'borrowed_value_identities' not in session.audit()
    bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_if_empty=True)
    session.release_cached_archives()
    audit = session.terminal()
    assert audit['borrowed_values'] == 2
    assert {row['kind']: row for row in audit['borrowed_value_identities']} == {
        kind: dict(kind=kind, document_id=cert['input_id' if kind == 'input' else 'run_id'],
            content_hash=cert['input_hash'] if kind == 'input' else proof['run_hash'],
            bridge_id=cert['bridge_id'], run_id=cert['run_id'], input_id=cert['input_id'])
        for kind in ('input', 'run')}
    assert session._borrowed_values[id(bundle)][0] is bundle and session._borrowed_values[id(run)][0] is run


@pytest.mark.parametrize('target', ['binding', 'run', 'input', 'source'])
def test_projection_always_checks_current_store_binding_full_parents_and_sources(archive, service, target):
    cert, session, _ = _registered(archive, service)
    if target == 'binding':
        namespace, key, reason = bridge.RUN_BINDINGS, cert['run_id'], 'VERIFIED_VIEW_BINDING_CHANGED'
    elif target == 'run':
        namespace, key, reason = 'strategy_experiment_runs', cert['run_id'], 'BRIDGED_DATABASE_PARENT_CHANGED'
    elif target == 'input':
        namespace, key, reason = 'candidate_inputs', cert['input_id'], 'BRIDGED_DATABASE_PARENT_CHANGED'
    else:
        namespace, key, reason = bridge.SOURCES, session._verified_views[
            (id(service.store), bridge.RUN_BINDINGS, cert['run_id'])]['source_rows'][0][0], 'BRIDGED_SOURCE_REGISTRATION_CHANGED'
    payload = service.store.get_document(namespace, key)
    payload['TEST_FIXTURE_OFFLINE_DAMAGE'] = True
    _offline_fixture_document(service, namespace, key, payload)
    with pytest.raises(ValueError, match=reason):
        bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_if_empty=True)
    assert not session._pending_run_projections and not session._borrowed_values


def test_projection_and_second_decoder_reject_other_session_store_and_public_json(archive, service, tmp_path):
    cert, session, proof = _registered(archive, service)
    view = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_if_empty=True)
    with pytest.raises(ValueError, match='GENUINE_INVOCATION_VIEW_REQUIRED'):
        bridge._verified_second_run_decode(json.loads(json.dumps(view)), session)
    with pytest.raises(ValueError, match='GENUINE_INVOCATION_VIEW_REQUIRED'):
        bridge._verified_second_run_decode(dict(proof), session)
    other_session = bridge.ArchiveVerificationSession()
    other_session._verified_views[(id(service.store), bridge.RUN_BINDINGS, cert['run_id'])] = proof
    with pytest.raises(ValueError, match='GENUINE_INVOCATION_VIEW_REQUIRED'):
        bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=other_session, borrow_if_empty=True)
    other_store = SimpleNamespace(store=SQLiteStore(tmp_path / 'TEST_FIXTURE-other.db'))
    session._verified_views[(id(other_store.store), bridge.RUN_BINDINGS, cert['run_id'])] = proof
    with pytest.raises(ValueError, match='VERIFIED_VIEW_STORE_MISMATCH'):
        bridge.authenticated_bridge_view(other_store, run_id=cert['run_id'], session=session, borrow_if_empty=True)
    assert not other_session.files and not other_session._borrowed_values


@pytest.mark.parametrize('target', ['raw', 'source', 'run'])
def test_projection_release_preserves_all_original_terminal_guards(archive, service, target):
    cert, session, _ = _registered(archive, service)
    bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_if_empty=True)
    session.release_cached_archives()
    path = (Path(archive['items'][0]['raw']['path']) if target == 'raw' else
        Path(archive['registry'][0]['path']) if target == 'source' else
        Path(archive['artifacts']['canonical-run.json.gz']['path']))
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='FILE_SHA_OR_IDENTITY_MISMATCH'):
        session.terminal()


@pytest.mark.parametrize('encoding,prefix', [('utf-8', b''), ('utf-8', b'\xef\xbb\xbf'),
    ('utf-16', b''), ('utf-16-le', b''), ('utf-32', b''), ('utf-32-be', b'')])
def test_second_stdlib_decode_preserves_bytes_semantics_number_types_and_key_order(archive, service, monkeypatch, encoding, prefix):
    _repack_fixture_run(archive, encoding=encoding, prefix=prefix)
    cert, session, proof = _registered(archive, service)
    raw = gzip.decompress(Path(proof['run_reference']['path']).read_bytes())
    expected = bridge._json(raw)
    calls = []
    original = json.loads
    def loads(value, *args, **kwargs):
        if isinstance(value, bytes) and value == raw:
            calls.append(dict(kwargs))
        return original(value, *args, **kwargs)
    monkeypatch.setattr(bridge.json, 'loads', loads)
    run = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)['run']
    assert run == expected
    assert len(calls) == 1 and set(calls[0]) == {'parse_constant'}
    assert calls[0]['parse_constant'] is bridge._nonfinite_json
    assert list(run) == list(expected)
    assert list(run['TEST_FIXTURE_JSON_TYPES']['nested']) == ['z', 'a']
    assert json.dumps(run, ensure_ascii=False, allow_nan=False) == json.dumps(expected, ensure_ascii=False, allow_nan=False)
    assert bridge.digest_json_stream(run) == proof['run_hash']
    assert session.terminal()['borrowed_values'] == 1


def test_initial_run_authentication_still_rejects_resealed_duplicate_keys(archive):
    _repack_fixture_run(archive, duplicate=True)
    with pytest.raises(ValueError, match='DUPLICATE_JSON_KEY'):
        authenticate(archive)


@pytest.mark.parametrize('raw', [b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}',
    b'{"x":1,"\\u0078":1}', b'{"nested":{"x":1,"x":1}}'])
def test_initial_strict_parser_rejects_nonfinite_and_escaped_duplicate_keys(raw):
    with pytest.raises(ValueError, match='NONFINITE_JSON|DUPLICATE_JSON_KEY'):
        bridge._json(raw)


def test_second_decode_requires_registered_exact_original_before_any_read(archive, service, monkeypatch):
    cert, session, proof = _registered(archive, service)
    del session.files[proof['run_reference']['path']]
    monkeypatch.setattr(session, 'read', lambda *a, **k: pytest.fail('MUST_NOT_REGISTER_CALLER_REFERENCE'))
    with pytest.raises(ValueError, match='STRICTLY_AUTHENTICATED_RUN_REFERENCE_REQUIRED'):
        bridge._verified_second_run_decode(proof, session)
    assert not session._borrowed_values


def test_second_decode_same_json_different_original_bytes_rejected_before_parser(archive, service, monkeypatch):
    cert, session, proof = _registered(archive, service)
    path = Path(proof['run_reference']['path'])
    raw = gzip.decompress(path.read_bytes())
    path.write_bytes(gzip.compress(b' ' + raw, mtime=0))
    original = bridge._decompressed_bytes
    monkeypatch.setattr(bridge, '_decompressed_bytes', lambda *a, **k: pytest.fail('MUST_REJECT_CHANGED_GZIP_FIRST'))
    with pytest.raises(ValueError, match='FILE_SHA_OR_IDENTITY_MISMATCH'):
        bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)
    monkeypatch.setattr(bridge, '_decompressed_bytes', original)
    assert not session._borrowed_values


def test_second_decode_full_canonical_hash_still_checked_before_borrow(archive, service, monkeypatch):
    cert, session, proof = _registered(archive, service)
    original = bridge._verified_second_run_decode
    def corrupted(*args):
        run = original(*args)
        run['results'][bridge.STRATEGY]['counts']['TEST_FIXTURE_CORRUPTED_DECODE'] = 1
        return run
    monkeypatch.setattr(bridge, '_verified_second_run_decode', corrupted)
    with pytest.raises(ValueError, match='BORROWED_VERIFIED_CONTENT_CHANGED'):
        bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)
    assert not session._borrowed_values


@pytest.mark.parametrize('stage', ['before', 'after'])
def test_second_decode_cancellation_keeps_exact_exception_and_mints_no_borrow(archive, service, monkeypatch, stage):
    cert, session, proof = _registered(archive, service)
    failure = KeyboardInterrupt('TEST_FIXTURE_SECOND_DECODE_CANCELLED')
    parsed = False
    original = json.loads
    def loads(*args, **kwargs):
        nonlocal parsed
        result = original(*args, **kwargs)
        if kwargs.get('parse_constant') is bridge._nonfinite_json and 'object_pairs_hook' not in kwargs:
            parsed = True
        return result
    def checkpoint():
        if stage == 'before' or parsed:
            raise failure
    monkeypatch.setattr(bridge.json, 'loads', loads)
    session.checkpoint = checkpoint
    with pytest.raises(KeyboardInterrupt) as caught:
        bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)
    assert caught.value is failure and not session._borrowed_values


def test_second_decode_strong_guard_survives_release_and_projection(archive, service):
    cert, session, _ = _registered(archive, service)
    view = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)
    bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_if_empty=True)
    session.release_cached_archives()
    view['run']['results'][bridge.STRATEGY]['items'][0]['TEST_FIXTURE_NESTED_MUTATION'] = True
    with pytest.raises(ValueError, match='BORROWED_VERIFIED_CONTENT_CHANGED'):
        session.terminal()
