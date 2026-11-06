"""TEST_FIXTURE old recovery markers: source-bound compatibility, no network."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from stockfish_core.yaogu.collection_inherited_scope import EPOCH_SCHEMA
from stockfish_core.yaogu.premarket_capture_cache_v1 import CaptureCache, RequestSpec
from tests.yaogu.test_premarket_capture_cache_v1 import FixtureBudget, FixtureExecutor


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')


def make_epoch(tmp_path, *, endpoint='calendar', failed=False):
    source = CaptureCache(tmp_path / 'TEST_FIXTURE_source', FixtureBudget(),
        config=dict(allow_test_fixture=True),
        executor=FixtureExecutor(lhb_status='FETCH_FAILED' if failed else 'COMPLETE'))
    parameters = dict(start_date='2026-10-01', end_date='2026-10-09')
    old = RequestSpec(endpoint, parameters, role='TEST_FIXTURE_OLD_UNDATED_BUSINESS_SESSION')
    record = source.capture_one(old)
    target = CaptureCache(tmp_path / 'TEST_FIXTURE_epoch', FixtureBudget(),
        config=dict(allow_test_fixture=True), executor=FixtureExecutor())
    original = record.to_dict()
    seed = dict(schema_version=EPOCH_SCHEMA, sources=[{
        **{role:original[role] for role in ('raw', 'receipt', 'result')},
        'archive_root': original['archive_root']}], raw_receipt_sources=[])
    write(target.root / 'epoch-seed.json', seed)
    target.import_seed(target.root / 'epoch-seed.json')
    current = next(target._entries())
    assert current['request_key'] != old.key
    plan = hashlib.sha256(b'TEST_FIXTURE_RECORDED_RECOVERY_PLAN').hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    claim = dict(schema_version=EPOCH_SCHEMA, source_cache=str(source.root), plan_sha256=plan,
                 status='STARTED_NO_IMPLICIT_RETRY', actual_started_at=now)
    complete = dict(schema_version=EPOCH_SCHEMA, source_cache=str(source.root),
        cache_root=str(target.root), plan_sha256=plan,
        status='CURRENT_ORIGINALS_RECERTIFIED_NEW_METADATA_EPOCH', actual_completed_at=now,
        network_calls=0, new_market_data=False, old_records_rewritten=False,
        source_clocks_rewritten=False, source_authority_restored=False,
        request_key_mappings=[dict(old_request_key=old.key,
            current_request_keys=[current['request_key']], receipt_id=original['receipt_id'])])
    marker = dict(schema_version=EPOCH_SCHEMA, request_key=old.key, indexed=True,
        source_attempt_directory=str(source.root / 'raw' / old.key), source_plan_sha256=plan,
        status='INHERITED_ORIGINAL_ATTEMPT_NO_IMPLICIT_RETRY')
    folder = target.root / 'raw' / old.key
    write(target.root / 'epoch-claim.json', claim)
    write(target.root / 'epoch-complete.json', complete)
    write(folder / 'inherited-original-attempt/request-claim.json', marker)
    # New invocation verifies every on-disk reference independently.
    target = CaptureCache(target.root, FixtureBudget(),
        config=dict(allow_test_fixture=True, offline=True), executor=FixtureExecutor())
    return target, source, old, record, current, folder


def lhb_spec():
    return RequestSpec('lhb_summary', dict(start_date='2026-10-05', end_date='2026-10-12'),
                       '2026-10-12')


def factor_spec():
    return RequestSpec('adjustment_factors', dict(codes=['sh.600001'],
        start_date='1990-01-01', end_date='2026-10-12'), '2026-10-12')


def test_complete_calendar_marker_resolves_and_both_guards_allow_unrelated_scope(tmp_path):
    cache, source, old, original, current, folder = make_epoch(tmp_path)
    before = {str(path):hashlib.sha256(path.read_bytes()).hexdigest()
              for root in (cache.root, source.root) for path in root.rglob('*') if path.is_file()}
    proof = cache.inherited_attempt_scope(folder)
    assert proof['status'] == 'VERIFIED_COMPLETE_ORIGINAL'
    assert proof['request_spec'] == old.to_dict()
    assert proof['receipt_ids'] == [original.receipt_id]
    assert proof['source_capability_issued'] is False
    assert cache.lhb_overlap_blockers(lhb_spec()) == []
    assert cache.factor_overlap_blockers(factor_spec()) == []
    cache.terminal()
    assert cache.executor.calls == []
    after = {str(path):hashlib.sha256(path.read_bytes()).hexdigest()
             for root in (cache.root, source.root) for path in root.rglob('*') if path.is_file()}
    assert after == before


@pytest.mark.parametrize('fault', ['plan', 'mapping_receipt', 'mapping_key', 'source_root',
    'indexed_false', 'seed', 'old_raw', 'old_claim_commit', 'additional_attempt',
    'source_index_deleted', 'current_index_deleted', 'marker_extra_file', 'source_failed_marker',
    'source_claim_changed', 'malformed_mapping'])
def test_invalid_or_incomplete_inherited_chain_remains_blocked(tmp_path, fault):
    cache, source, old, record, current, folder = make_epoch(tmp_path)
    complete_path = cache.root / 'epoch-complete.json'
    complete = json.loads(complete_path.read_text())
    if fault == 'plan':
        complete['plan_sha256'] = 'f' * 64
        write(complete_path, complete)
    elif fault == 'mapping_receipt':
        complete['request_key_mappings'][0]['receipt_id'] = 'f' * 64
        write(complete_path, complete)
    elif fault == 'mapping_key':
        complete['request_key_mappings'][0]['current_request_keys'] = ['f' * 64]
        write(complete_path, complete)
    elif fault == 'source_root':
        complete['source_cache'] = str(tmp_path)
        write(complete_path, complete)
    elif fault == 'indexed_false':
        p = folder / 'inherited-original-attempt/request-claim.json'
        marker = json.loads(p.read_text()); marker['indexed'] = False; write(p, marker)
    elif fault == 'seed':
        p = cache.root / 'epoch-seed.json'; p.write_bytes(p.read_bytes() + b' ')
    elif fault == 'old_raw':
        p = Path(record.metadata['raw']['path']); p.write_bytes(p.read_bytes() + b' ')
    elif fault == 'old_claim_commit':
        p = Path(record.metadata['capture_attempt_dir']) / 'committed.json'
        write(p, dict(request_key=old.key, receipt_id='f' * 64))
    elif fault == 'additional_attempt':
        (source.root / 'raw' / old.key / 'raw' / 'TEST_FIXTURE_NEW_UNFINISHED').mkdir()
    elif fault == 'source_index_deleted':
        next((source.root / 'index' / old.key).glob('*.json')).unlink()
    elif fault == 'current_index_deleted':
        next((cache.root / 'index' / current['request_key']).glob('*.json')).unlink()
    elif fault == 'marker_extra_file':
        (folder / 'TEST_FIXTURE_UNIDENTIFIED').write_bytes(b'TEST_FIXTURE')
    elif fault == 'source_failed_marker':
        write(Path(record.metadata['capture_attempt_dir']) / 'attempt-failed.json',
              dict(status='FAILED_RETAINED_NO_RETRY'))
    elif fault == 'source_claim_changed':
        p = Path(record.metadata['capture_attempt_dir']) / 'request-claim.json'
        d = json.loads(p.read_text()); d['request_spec'] = lhb_spec().to_dict(); write(p, d)
    elif fault == 'malformed_mapping':
        complete['request_key_mappings'].append(None); write(complete_path, complete)
    proof = cache.inherited_attempt_scope(folder)
    assert proof['status'] == 'UNKNOWN_RETAINED_NO_IMPLICIT_RETRY'
    assert cache.lhb_overlap_blockers(lhb_spec())[0]['request_key'] == old.key
    assert cache.factor_overlap_blockers(factor_spec())[0]['request_key'] == old.key
    assert cache.executor.calls == []


def test_failed_lhb_original_and_changed_window_are_never_cleared_by_mapping(tmp_path):
    cache, source, old, record, current, folder = make_epoch(tmp_path, endpoint='lhb_summary', failed=True)
    assert cache.inherited_attempt_scope(folder)['status'] == 'UNKNOWN_RETAINED_NO_IMPLICIT_RETRY'
    blockers = cache.lhb_overlap_blockers(lhb_spec())
    assert {row['request_key'] for row in blockers} == {old.key, current['request_key']}
    assert cache.lookup(RequestSpec(**current['request_spec'])).status == 'FETCH_FAILED'
    assert cache.executor.calls == []


def test_empty_epoch_claim_or_mapping_is_not_source_authority(tmp_path, monkeypatch):
    cache, source, old, record, current, folder = make_epoch(tmp_path)
    def forbidden(*args, **kwargs):
        raise ValueError('TEST_FIXTURE_NATIVE_ORIGINAL_READER_REJECTED')
    monkeypatch.setattr(cache, 'verify', forbidden)
    proof = cache.inherited_attempt_scope(folder)
    assert proof['status'] == 'UNKNOWN_RETAINED_NO_IMPLICIT_RETRY'
    assert 'NATIVE_ORIGINAL_READER_REJECTED' in proof['reason']
    assert cache.lhb_overlap_blockers(lhb_spec())


def test_new_unknown_attempt_after_valid_scope_is_seen_without_rewriting_marker(tmp_path):
    cache, source, old, record, current, folder = make_epoch(tmp_path)
    assert cache.inherited_attempt_scope(folder)['status'] == 'VERIFIED_COMPLETE_ORIGINAL'
    (source.root / 'raw' / old.key / 'raw' / 'TEST_FIXTURE_UNFINISHED').mkdir()
    assert cache.inherited_attempt_scope(folder)['status'] == 'UNKNOWN_RETAINED_NO_IMPLICIT_RETRY'
    assert cache.lhb_overlap_blockers(lhb_spec())


def test_changed_original_after_valid_scope_is_detected_by_terminal(tmp_path):
    cache, source, old, record, current, folder = make_epoch(tmp_path)
    assert cache.inherited_attempt_scope(folder)['status'] == 'VERIFIED_COMPLETE_ORIGINAL'
    p = Path(record.metadata['raw']['path']); p.write_bytes(p.read_bytes() + b' ')
    with pytest.raises(ValueError):
        cache.terminal()
