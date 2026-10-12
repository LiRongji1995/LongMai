"""Explicit TEST_FIXTURE: exact V2 parity, full gzip audit, fatal budgets."""
from copy import deepcopy
import gzip
import hashlib
import json
import os
from pathlib import Path
import resource
import time

import pytest

from stockfish_core.yaogu import evidence_audit_spool_v2 as old
from stockfish_core.yaogu import evidence_audit_spool_v3 as module
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.history_artifacts import digest_json_stream
from stockfish_core.yaogu.visibility import EvidenceResolver, POLICY_ID
from tests.yaogu.test_theme_evolution_r1 import fixture_market


def make(tmp_path, *, limit=2000, callback=None):
    bundle, cutoff, _ = fixture_market(7)
    kwargs = dict(cutoff=cutoff, coverage_type='TEST_FIXTURE', sessions=bundle.sessions,
                  observation_date=bundle.observation_date)
    plain = EvidenceResolver(bundle.receipts, **kwargs)
    v2 = old.SpoolingEvidenceResolverV2(bundle.receipts, evidence_directory=tmp_path / 'v2',
                                       segment_bytes=limit, **kwargs)
    v3 = module.SpoolingEvidenceResolverV3(bundle.receipts, evidence_directory=tmp_path / 'v3',
                segment_bytes=limit, logical_read_callback=callback, **kwargs)
    return bundle, plain, v2, v3


def populate(bundle, *resolvers):
    for row in reversed(bundle.bars):
        kwargs = dict(instrument_id=row.instrument_id, session=row.session,
                      require_complete=True, require_session=True)
        for _ in range(2):
            decisions = [resolver.resolve(row, **kwargs) for resolver in resolvers]
            assert all(item == decisions[0] for item in decisions)


def mutate(path, *, restore_mtime=True):
    before, raw = path.stat(), bytearray(path.read_bytes())
    assert raw
    position = len(raw) // 2
    raw[position] ^= 1
    path.write_bytes(raw)
    if restore_mtime:
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


def row_path(new):
    return new.rows.segments[0]


def test_all_decisions_canonical_records_sources_and_dependency_hash_match_v2(tmp_path):
    bundle, plain, v2, v3 = make(tmp_path)
    populate(bundle, plain, v2, v3)
    first = bundle.bars[0]
    for kwargs in (dict(instrument_id='600999.SH'), dict(source_refs=('MISSING',)),
                   dict(session='1999-01-01', require_session=True)):
        assert plain.resolve(first, **kwargs) == v2.resolve(first, **kwargs) == v3.resolve(first, **kwargs)
    late = first.model_copy(update={'first_seen_at': plain.cutoff.replace(year=2099),
                                    'ingested_at': plain.cutoff.replace(year=2099)})
    assert plain.resolve(late) == v2.resolve(late) == v3.resolve(late)
    expected = plain.effective_dependencies()
    p2, p3 = (r.joint_dependency_digest_and_audit_manifest() for r in (v2, v3))
    assert p2['dependency_hash'] == p3['dependency_hash'] == digest(expected)
    assert list(v3.effective_dependencies()['records']) == expected['records']
    assert json.loads(json.dumps(v3.effective_dependencies())) == expected
    assert p3['row_count'] == 24 and len(v3.rows.segments) > 1
    assert sorted(v3.rows) == [hashlib.sha256(old._json(r)).hexdigest() for r in expected['records']]
    assert p3['complete_allowed_row_audit'] is True
    assert p3['audit_manifest']['canonical_decoded_row_count'] == 24
    assert p3['validation'] == 'PRE_POST_WHOLE_SHA_7FP_AND_ALL_CANONICAL_FRAMES_CRC_EOF_EXACT_INDEX'
    for number, path in enumerate(v3.rows.segments):
        meta = p3['audit_manifest']['files'][path.name]
        assert meta['logical_bytes'] <= 2000
        assert meta['logical_sha256'] == hashlib.sha256(v2.rows.segments[number].read_bytes()).hexdigest()
    v2.close()
    v3.close()


def test_pre_post_physical_scans_never_claim_logical_crc_and_joint_decodes_exactly_once(tmp_path):
    charged = []
    bundle, plain, _, new = make(tmp_path, callback=lambda chunk: charged.append(len(chunk)))
    populate(bundle, plain, new)
    before = new.rows.logical_read_bytes
    physical = new.rows.verify()
    assert new.rows.logical_read_bytes == before
    assert physical['complete_allowed_row_audit'] is False
    assert physical['logical_decode_validation'] == 'NOT_PERFORMED_BY_PHYSICAL_SCAN'
    assert new.close()['complete_allowed_row_audit'] is False
    before = new.rows.logical_read_bytes
    proof = new.joint_dependency_digest_and_audit_manifest()
    assert new.rows.logical_read_bytes - before == physical['logical_canonical_bytes']
    assert sum(charged) == new.rows.logical_read_bytes
    assert proof['dependency_hash'] == digest(plain.effective_dependencies())
    assert proof['audit_manifest']['logical_decode_validation'] == 'ALL_CANONICAL_ROWS_GZIP_CRC_EOF_SHA_AND_NEWLINE'
    assert new.audit_manifest()['complete_allowed_row_audit'] is True
    assert json.loads((tmp_path / 'v3' / 'manifest.json').read_bytes())['complete_allowed_row_audit'] is False


def test_metered_v2_close_digest_manifest_vs_v3_joint_all_rows_and_checks_retained(tmp_path):
    bundle, plain, v2, v3 = make(tmp_path, limit=8000)
    populate(bundle, plain, v2, v3)
    b2, b3 = v2.read_metrics['actual_returned_read_bytes'], v3.read_metrics['actual_returned_read_bytes']
    v2.close()
    h2 = digest_json_stream(v2.effective_dependencies())
    m2 = v2.audit_manifest()
    proof = v3.joint_dependency_digest_and_audit_manifest()
    r2 = v2.read_metrics['actual_returned_read_bytes'] - b2
    r3 = v3.read_metrics['actual_returned_read_bytes'] - b3
    assert h2 == proof['dependency_hash'] == digest(plain.effective_dependencies())
    assert m2['row_count'] == proof['row_count'] == 24
    assert r3 < r2
    assert v3.rows.logical_read_bytes > 0 and v3.rows.read_bytes > 0
    assert v3.read_metrics['actual_returned_read_bytes'] == v3.rows.read_bytes + v3.rows.logical_read_bytes
    # The written index authenticates exact frame offsets and contiguous coverage;
    # each physical file still has a real pre and post whole digest and all 7 FP.
    assert all(set(meta['fp']) == set(module.FP_FIELDS) for meta in proof['audit_manifest']['files'].values())
    print(json.dumps({'TEST_FIXTURE': True, 'v2_close_digest_manifest_bytes': r2,
                      'v3_joint_physical_plus_logical_bytes': r3, 'saved_returned_bytes': r2-r3,
                      'canonical_rows': 24, 'same_dependency_hash': h2}))


@pytest.mark.parametrize('target', ['payload', 'index', 'truncated', 'extra_file', 'replaced_inode'])
def test_before_first_seal_real_append_ledger_rejects_tamper(tmp_path, target):
    bundle, _, _, new = make(tmp_path)
    new.resolve(bundle.bars[0])
    new.flush()
    path = new.rows.index_path if target == 'index' else row_path(new)
    if target in ('payload', 'index'):
        mutate(path)
    elif target == 'truncated':
        path.write_bytes(path.read_bytes()[:-1])
    elif target == 'extra_file':
        (tmp_path / 'v3' / 'rows-999999.ndjson.gzframes').write_bytes(b'fake')
    else:
        raw = path.read_bytes()
        path.unlink()
        path.write_bytes(raw)
    with pytest.raises(module.EvidenceAuditIntegrityError):
        new.joint_dependency_digest_and_audit_manifest()
    assert new.failure_provenance[-1]['phase'] == 'JOINT'
    assert new.rows.writer.closed and new.rows.index_writer.closed
    assert path.exists() and new.rows.index_path.exists()
    assert not (tmp_path / 'v3' / 'manifest.json').exists()


@pytest.mark.parametrize('target', ['payload', 'index', 'manifest', 'mtime', 'mode'])
def test_after_seal_actual_whole_sha_and_all_fp_refuse_mutation(tmp_path, target):
    bundle, _, _, new = make(tmp_path)
    new.resolve(bundle.bars[0])
    new.close()
    path = {'index': new.rows.index_path, 'manifest': tmp_path/'v3'/'manifest.json'}.get(target, row_path(new))
    if target in ('payload', 'index', 'manifest'):
        mutate(path)
    elif target == 'mtime':
        value = path.stat()
        os.utime(path, ns=(value.st_atime_ns, value.st_mtime_ns+1000000))
    else:
        path.chmod(0o644)
    with pytest.raises(module.EvidenceAuditIntegrityError):
        new.joint_dependency_digest_and_audit_manifest()
    with pytest.raises(module.EvidenceAuditIntegrityError):
        digest_json_stream(new.effective_dependencies())


@pytest.mark.parametrize('operation', ['ordinary', 'joint'])
@pytest.mark.parametrize('target', ['payload', 'index'])
def test_mid_iteration_same_size_restored_mtime_is_not_cacheable(tmp_path, monkeypatch, operation, target):
    bundle, _, _, new = make(tmp_path)
    populate(bundle, new)
    new.close()
    path = new.rows.index_path if target == 'index' else row_path(new)
    original, once = new.rows._read_row, []
    def tamper(key, pre=None):
        value = original(key, pre)
        if not once:
            once.append(True)
            mutate(path)
        return value
    monkeypatch.setattr(new.rows, '_read_row', tamper)
    with pytest.raises(module.EvidenceAuditIntegrityError):
        if operation == 'joint':
            new.joint_dependency_digest_and_audit_manifest()
        else:
            digest_json_stream(new.effective_dependencies())
    assert once


@pytest.mark.parametrize('target', ['rows', 'index'])
@pytest.mark.parametrize('kind', ['symlink', 'fifo'])
def test_nonregular_file_is_refused_nonblocking(tmp_path, target, kind):
    bundle, _, _, new = make(tmp_path)
    new.resolve(bundle.bars[0])
    new.flush()
    path = new.rows.index_path if target == 'index' else row_path(new)
    raw = path.read_bytes()
    path.unlink()
    if kind == 'symlink':
        other = tmp_path / 'elsewhere'
        other.write_bytes(raw)
        path.symlink_to(other)
    else:
        os.mkfifo(path)
    started = time.monotonic()
    with pytest.raises(module.EvidenceAuditIntegrityError, match='SOURCE_NOT_REGULAR'):
        new.joint_dependency_digest_and_audit_manifest()
    assert time.monotonic()-started < 1


@pytest.mark.parametrize('kind', ['trailing', 'duplicate', 'truncated', 'crc', 'bomb', 'newline', 'json', 'key'])
def test_owned_written_bad_frame_passes_no_false_logical_authority(tmp_path, monkeypatch, kind):
    bundle, _, _, new = make(tmp_path)
    original = new.rows._compress
    def bad(logical):
        frame = original(logical)
        if kind == 'trailing':
            return frame+b'junk'
        if kind == 'duplicate':
            return frame+frame
        if kind == 'truncated':
            return frame[:-1]
        if kind == 'crc':
            return frame[:-8]+bytes([frame[-8]^1])+frame[-7:]
        if kind == 'bomb':
            return gzip.compress(b'x'*(len(logical)+module.CHUNK_BYTES), mtime=0)
        if kind == 'newline':
            return original(logical[:-1]+b' ')
        if kind == 'json':
            return original(b'!'+logical[1:])
        return original(logical.replace(b'2026', b'2025', 1))
    monkeypatch.setattr(new.rows, '_compress', bad)
    new.resolve(bundle.bars[0])
    physical = new.close()
    assert physical['complete_allowed_row_audit'] is False
    assert physical['physical_write_ledger_audit_complete'] is True
    before = new.rows.logical_read_bytes
    with pytest.raises(module.EvidenceAuditIntegrityError):
        new.joint_dependency_digest_and_audit_manifest()
    # Actual inflation, including the limit-crossing chunk, is never omitted.
    assert new.rows.logical_read_bytes >= before
    if kind == 'bomb':
        assert new.rows.logical_read_bytes-before > physical['logical_canonical_bytes']
    assert (tmp_path/'v3'/'manifest.json').exists()
    assert new.failure_provenance[-1]['phase'] == 'JOINT'


def test_duplicate_has_fresh_crc_sha_read_and_failure_is_sticky(tmp_path):
    bundle, _, _, new = make(tmp_path)
    row = bundle.bars[0]
    assert new.resolve(row).allowed
    before = new.read_metrics['actual_returned_read_bytes']
    assert new.resolve(row).allowed
    assert new.read_metrics['actual_returned_read_bytes'] > before
    mutate(row_path(new))
    with pytest.raises(module.EvidenceAuditIntegrityError) as first:
        new.resolve(row)
    with pytest.raises(module.EvidenceAuditIntegrityError) as second:
        new.joint_dependency_digest_and_audit_manifest()
    assert first.value is second.value


@pytest.mark.parametrize('kind', [ValueError, RuntimeError])
@pytest.mark.parametrize('operation', ['duplicate', 'joint', 'ordinary'])
def test_live_logical_budget_original_object_never_becomes_unknown(tmp_path, kind, operation):
    sentinel = kind('TEST_FIXTURE_ACTUAL_LOGICAL_BUDGET')
    calls = []
    def callback(raw):
        assert isinstance(raw, bytes) and raw
        calls.append(len(raw))
        raise sentinel
    bundle, _, _, new = make(tmp_path, callback=callback)
    row = bundle.bars[0]
    new.resolve(row)
    with pytest.raises(kind) as failure:
        if operation == 'duplicate':
            new.resolve(row)
        elif operation == 'joint':
            new.joint_dependency_digest_and_audit_manifest()
        else:
            digest_json_stream(new.effective_dependencies())
    assert failure.value is sentinel and new.rows.logical_read_bytes == sum(calls) > 0
    for operation in (lambda: new.resolve(row), new.effective_dependencies,
                      new.joint_dependency_digest_and_audit_manifest, new.close):
        with pytest.raises(kind) as sticky:
            operation()
        assert sticky.value is sentinel
    assert row_path(new).exists() and new.rows.index_path.exists()
    assert new.rows.writer.closed and new.rows.index_writer.closed


def test_callback_primary_preserved_when_reader_cleanup_finds_mutation(tmp_path):
    sentinel = RuntimeError('TEST_FIXTURE_PRIMARY_LOGICAL_BUDGET')
    holder = {}
    def callback(raw):
        mutate(row_path(holder['new']))
        raise sentinel
    bundle, _, _, new = make(tmp_path, callback=callback)
    holder['new'] = new
    new.resolve(bundle.bars[0])
    with pytest.raises(RuntimeError) as failure:
        new.joint_dependency_digest_and_audit_manifest()
    assert failure.value is sentinel
    assert any(r['phase']=='READER_PRIMARY' and 'PRIMARY_LOGICAL_BUDGET' in r['error']
               for r in new.failure_provenance)
    assert any(r['phase']=='READER_CLEANUP' and 'READ_7FP_CHANGED' in r['error']
               for r in new.failure_provenance)


def test_physical_budget_primary_and_cleanup_preserve_actual_read(tmp_path, monkeypatch):
    bundle, _, _, new = make(tmp_path)
    new.resolve(bundle.bars[0])
    sentinel = ValueError('TEST_FIXTURE_PRIMARY_PHYSICAL_BUDGET')
    original = new.rows._read
    def failed(source, size, *, line=False):
        original(source, size, line=line)
        mutate(new.rows.index_path)
        raise sentinel
    monkeypatch.setattr(new.rows, '_read', failed)
    with pytest.raises(ValueError) as failure:
        new.joint_dependency_digest_and_audit_manifest()
    assert failure.value is sentinel and new.rows.read_bytes > 0
    assert any(r['phase']=='READER_CLEANUP' for r in new.failure_provenance)


@pytest.mark.parametrize('mid_read', [False, True])
def test_parent_alias_with_same_leaf_inode_is_refused(tmp_path, monkeypatch, mid_read):
    sandbox = tmp_path/'sandbox'
    sandbox.mkdir()
    bundle, _, _, new = make(sandbox)
    new.resolve(bundle.bars[0])
    new.flush()
    changed = []
    def alias():
        if not changed:
            changed.append(True)
            moved = tmp_path/'moved'
            sandbox.rename(moved)
            sandbox.symlink_to(moved, target_is_directory=True)
    if mid_read:
        original = new.rows._read
        def read(source, size, *, line=False):
            raw = original(source, size, line=line)
            alias()
            return raw
        monkeypatch.setattr(new.rows, '_read', read)
    else:
        alias()
    with pytest.raises(module.EvidenceAuditIntegrityError, match='DIRECTORY_PATH_NOT_REGULAR'):
        new.joint_dependency_digest_and_audit_manifest()
    assert changed


def test_manifest_publish_primary_and_close_secondary_are_separate(tmp_path, monkeypatch):
    bundle, _, _, new = make(tmp_path)
    new.resolve(bundle.bars[0])
    original_writer, original_append = new.rows._new_writer, new.rows._append
    sentinel = RuntimeError('TEST_FIXTURE_MANIFEST_APPEND_PRIMARY')
    handles = []
    class Proxy:
        def __init__(self, handle): self.handle = handle
        def __getattr__(self, name): return getattr(self.handle, name)
        def close(self):
            self.handle.close()
            raise OSError('TEST_FIXTURE_MANIFEST_CLOSE_SECONDARY')
    def writer(path):
        handle = original_writer(path)
        if path.name == 'manifest.json':
            handles.append(handle)
            return Proxy(handle)
        return handle
    def append(path, handle, raw):
        if path.name == 'manifest.json': raise sentinel
        return original_append(path, handle, raw)
    monkeypatch.setattr(new.rows, '_new_writer', writer)
    monkeypatch.setattr(new.rows, '_append', append)
    with pytest.raises(RuntimeError) as failure:
        new.close()
    assert failure.value is sentinel and handles[0].closed
    assert any(r['phase']=='PUBLISH_PRIMARY' for r in new.failure_provenance)
    assert any(r['phase']=='PUBLISH_CLEANUP' for r in new.failure_provenance)


def test_writer_close_error_still_attempts_both_handles(tmp_path):
    bundle, _, _, new = make(tmp_path)
    new.resolve(bundle.bars[0])
    attempted = []
    sentinel = OSError('TEST_FIXTURE_WRITER_CLOSE')
    class Proxy:
        def __init__(self, handle, label): self.handle, self.label = handle, label
        def __getattr__(self, name): return getattr(self.handle, name)
        def close(self):
            attempted.append(self.label)
            self.handle.close()
            if self.label == 'rows': raise sentinel
    new.rows.writer = Proxy(new.rows.writer, 'rows')
    new.rows.index_writer = Proxy(new.rows.index_writer, 'index')
    with pytest.raises(OSError) as failure:
        new.close()
    assert failure.value is sentinel and attempted == ['rows', 'index']
    assert new.rows.writer.closed and new.rows.index_writer.closed


def test_constructor_callback_is_accounting_not_capability_no_disabled_or_restored_owner(tmp_path):
    bundle, plain, _, new = make(tmp_path)
    with pytest.raises(ValueError, match='LIVE_LOGICAL_ACCOUNTING_REQUIRED'):
        module.SpoolingEvidenceResolverV3(bundle.receipts, evidence_directory=tmp_path/'nonfixture',
                                          cutoff=plain.cutoff, coverage_type='PROSPECTIVE_CAPTURED')
    assert not (tmp_path/'nonfixture').exists()
    with pytest.raises(TypeError, match='CALLBACK_REQUIRED'):
        module.SpoolingEvidenceResolverV3([], evidence_directory=tmp_path/'badcallback',
                   cutoff=plain.cutoff, coverage_type='TEST_FIXTURE', logical_read_callback={})
    with pytest.raises(ValueError, match='COMPLETE_ROW_RECORDING_REQUIRED'):
        module.SpoolingEvidenceResolverV3([], evidence_directory=tmp_path/'disabled',
                   cutoff=plain.cutoff, coverage_type='TEST_FIXTURE', record_rows=False)
    with pytest.raises(FileExistsError):
        module.SpoolingEvidenceResolverV3([], evidence_directory=tmp_path/'v3',
                                          cutoff=plain.cutoff, coverage_type='TEST_FIXTURE')
    missing = module.SpoolingEvidenceResolverV3([], evidence_directory=tmp_path/'missing',
            cutoff=plain.cutoff, coverage_type='TEST_FIXTURE', logical_read_callback=lambda raw: None)
    assert missing.resolve(bundle.bars[0]).allowed is False
    assert len(missing.rows) == 0
    missing.close()
    with pytest.raises(TypeError): del new.rows['0'*64]
    with pytest.raises(module.EvidenceAuditIntegrityError, match='ROW_KEY_MISMATCH') as first:
        new.rows['0'*64] = bundle.bars[0].model_dump(mode='json')
    with pytest.raises(module.EvidenceAuditIntegrityError) as second:
        new.joint_dependency_digest_and_audit_manifest()
    assert first.value is second.value


def test_sources_mutation_and_returned_manifest_cannot_authorize(tmp_path):
    bundle, _, _, new = make(tmp_path)
    new.resolve(bundle.bars[0])
    proof = new.joint_dependency_digest_and_audit_manifest()
    proof['audit_manifest']['files'].clear()
    assert new.joint_dependency_digest_and_audit_manifest()['audit_manifest']['files']
    with pytest.raises(TypeError): new.joint_dependency_digest_and_audit_manifest(proof)
    key = next(iter(new.used))
    new.used[key]['content_hash'] = '0'*64
    with pytest.raises(module.EvidenceAuditIntegrityError, match='USED_SOURCE_CHANGED'):
        new.joint_dependency_digest_and_audit_manifest()


def test_empty_and_implementation_identity_frozen_dependencies(tmp_path):
    _, plain, _, new = make(tmp_path)
    proof = new.joint_dependency_digest_and_audit_manifest()
    assert proof['row_count']==0 and proof['dependency_hash']==digest(plain.effective_dependencies())
    assert digest_json_stream(new.effective_dependencies())==proof['dependency_hash']
    identity = module.implementation_source_identity()
    assert identity['implementation_version']==module.VERSION
    assert identity['role']=='IMPLEMENTATION_IDENTITY_NOT_SOURCE_CAPABILITY'
    assert identity['source_file_hashes']['frozen_spool_v2']==(
        '1fd61b53e73da1ba7cb3afe24f17aba73cdcb067b63b74da3e784a733c0ee710')
    assert identity['source_file_hashes']['spec_v2']==(
        'a3afd5b188aad82828907e99beb32ebbeabe2b49354bed9028a2fe35511b7875')
    assert hashlib.sha256(Path(old.__file__).parents[2].joinpath('tests/yaogu/test_evidence_audit_spool_v2.py').read_bytes()).hexdigest()==(
        '8667ce4b13fa44f6acad19f2cf21a0d5e2cacd05d0fb27a97399b6742e431e96')
    public = module.source_identity()
    assert public['role']=='IMPLEMENTATION_IDENTITY_NOT_SOURCE_CAPABILITY'
    assert public['source_hash']==digest_json_stream(public['source_file_hashes'])
    assert set(public['source_file_hashes'])=={
        'stockfish_core/yaogu/evidence_audit_spool_v3.py',
        'stockfish_core/yaogu/evidence_audit_spool_v2.py',
        'stockfish_core/yaogu/visibility.py', 'stockfish_core/yaogu/history_artifacts.py',
        'docs/EVIDENCE_AUDIT_SPOOL_V2.md', 'docs/EVIDENCE_AUDIT_SPOOL_V3.md'}
    assert public['source_file_hashes']['stockfish_core/yaogu/evidence_audit_spool_v2.py']==(
        identity['source_file_hashes']['frozen_spool_v2'])
    new.close()
    new.close()


@pytest.mark.parametrize('wrong', ['boolean_offset', 'duplicated_line', 'missing_line', 'wrong_key'])
def test_index_semantics_are_exact_before_first_seal_not_only_current_disk_hash(tmp_path, wrong):
    bundle, _, _, new = make(tmp_path)
    populate(bundle, new)
    new.flush()
    path = new.rows.index_path
    lines = path.read_bytes().splitlines(keepends=True)
    if wrong == 'duplicated_line':
        lines.append(lines[0])
    elif wrong == 'missing_line':
        lines.pop()
    else:
        row = json.loads(lines[0])
        row['offset' if wrong=='boolean_offset' else 'record_hash'] = False if wrong=='boolean_offset' else '0'*64
        lines[0] = old._json(row)+b'\n'
    original = path.stat()
    path.write_bytes(b''.join(lines))
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    with pytest.raises(module.EvidenceAuditIntegrityError):
        new.joint_dependency_digest_and_audit_manifest()
    assert not (tmp_path/'v3'/'manifest.json').exists()


def test_logical_row_and_segment_limits_do_not_expand_under_compression(tmp_path):
    bundle, _, _, new = make(tmp_path, limit=2000)
    row = {'TEST_FIXTURE': True, 'payload': 'a'*2000}
    raw = old._json(row)
    assert len(gzip.compress(raw)) < 2000 < len(raw)+1
    with pytest.raises(module.EvidenceAuditIntegrityError, match='ROW_EXCEEDS_SEGMENT_LIMIT'):
        new.rows[hashlib.sha256(raw).hexdigest()] = row
    assert not new.rows.segments and new.rows.index_path.exists()


def test_synthetic_420mib_canonical_full_logical_resource_proof_only(tmp_path):
    """One-row-at-a-time logical scale; synthetic compression is not real 67 proof."""
    _, cutoff, _ = fixture_market(7)
    new = module.SpoolingEvidenceResolverV3([], evidence_directory=tmp_path/'scale',
            cutoff=cutoff, coverage_type='TEST_FIXTURE', logical_read_callback=lambda raw: None)
    payload = 'TEST_FIXTURE_COMPRESSIBLE_ONLY_'*(1024*1024//31+1)
    payload = payload[:1024*1024]
    indices = {}
    logical = 0
    for ordinal in range(420):
        row = {'TEST_FIXTURE': True, 'ordinal': ordinal, 'payload': payload}
        raw = old._json(row)
        key = hashlib.sha256(raw).hexdigest()
        indices[key] = ordinal
        logical += len(raw)+1
        new.rows[key] = row
    class ExpectedRows(list):
        def __len__(self):
            return len(indices)
        def __iter__(self):
            for key in sorted(indices):
                yield {'TEST_FIXTURE': True, 'ordinal': indices[key], 'payload': payload}
    expected = digest_json_stream({'policy_id': POLICY_ID, 'sources': [], 'records': ExpectedRows()})
    before = new.read_metrics['actual_returned_read_bytes']
    proof = new.joint_dependency_digest_and_audit_manifest()
    consumed = new.read_metrics['actual_returned_read_bytes']-before
    output = sum(path.stat().st_size for path in (tmp_path/'scale').iterdir())
    assert proof['row_count']==420 and proof['dependency_hash']==expected
    assert new.rows.logical_read_bytes==logical >= 420*1024*1024
    assert output < 64*1024*1024 and consumed < logical+8*1024*1024
    # Darwin reports bytes; Linux reports KiB. Both enforce the declared fixture RSS.
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss = peak if os.uname().sysname=='Darwin' else peak*1024
    assert rss < 512*1024*1024
    print(json.dumps({'TEST_FIXTURE': True, 'synthetic_logical_canonical_bytes': logical,
          'physical_output_bytes': output, 'joint_physical_plus_logical_returned_bytes': consumed,
          'OS_peak_rss_bytes': rss, 'canonical_rows': 420, 'same_dependency_hash': expected,
          'real_67_session_or_global_4GiB_success': 'NOT_TESTED'}))
