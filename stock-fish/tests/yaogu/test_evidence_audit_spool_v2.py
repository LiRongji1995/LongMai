"""Isolated TEST_FIXTURE parity, genuine journal tampering and read counts."""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import time

import pytest

from stockfish_core.yaogu import evidence_audit_spool as old_module
from stockfish_core.yaogu import evidence_audit_spool_v2 as module
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.history_artifacts import digest_json_stream
from stockfish_core.yaogu.visibility import EvidenceResolver
from tests.yaogu.test_theme_evolution_r1 import fixture_market


def make(tmp_path, *, limit=2000):
    bundle, cutoff, _ = fixture_market(7)
    assert bundle.coverage_type == 'TEST_FIXTURE'
    kwargs = dict(cutoff=cutoff, coverage_type='TEST_FIXTURE', sessions=bundle.sessions,
                  observation_date=bundle.observation_date)
    plain = EvidenceResolver(bundle.receipts, **kwargs)
    old = old_module.SpoolingEvidenceResolver(bundle.receipts, evidence_directory=tmp_path / 'old',
                                             segment_bytes=limit, **kwargs)
    new = module.SpoolingEvidenceResolverV2(bundle.receipts, evidence_directory=tmp_path / 'new',
                                           segment_bytes=limit, **kwargs)
    return bundle, plain, old, new


def populate(bundle, *resolvers):
    for row in reversed(bundle.bars):
        kwargs = dict(instrument_id=row.instrument_id, session=row.session,
                      require_complete=True, require_session=True)
        decisions = [resolver.resolve(row, **kwargs) for resolver in resolvers]
        assert all(item == decisions[0] for item in decisions)
        decisions = [resolver.resolve(row, **kwargs) for resolver in resolvers]
        assert all(item == decisions[0] for item in decisions)


def changed_same_size(path, *, restore_mtime=True):
    before = path.stat()
    raw = path.read_bytes()
    # Leave the size unchanged and restore mtime; ctime and whole bytes remain
    # real. This deliberately defeats a size/mtime-only integrity shortcut.
    position = next(index for index, byte in enumerate(raw) if 48 <= byte <= 57)
    raw = raw[:position] + (b'8' if raw[position:position+1] != b'8' else b'7') + raw[position+1:]
    path.write_bytes(raw)
    if restore_mtime:
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


def test_full_original_decisions_records_and_joint_digest_match(tmp_path):
    bundle, plain, old, new = make(tmp_path)
    populate(bundle, plain, old, new)
    first = bundle.bars[0]
    cases = [dict(instrument_id='600999.SH'), dict(source_refs=('MISSING',)),
             dict(session='1999-01-01', require_session=True)]
    for kwargs in cases:
        assert plain.resolve(first, **kwargs) == old.resolve(first, **kwargs) == new.resolve(first, **kwargs)
    late = first.model_copy(update={'first_seen_at': plain.cutoff.replace(year=2099),
                                    'ingested_at': plain.cutoff.replace(year=2099)})
    assert plain.resolve(late) == old.resolve(late) == new.resolve(late)
    old.close()
    expected = plain.effective_dependencies()
    assert digest_json_stream(old.effective_dependencies()) == digest(expected)
    joint = new.joint_dependency_digest_and_audit_manifest()
    assert joint['dependency_hash'] == digest(expected)
    assert joint['row_count'] == 24
    assert list(new.effective_dependencies()['records']) == expected['records']
    assert json.loads(json.dumps(new.effective_dependencies())) == expected
    assert joint['audit_manifest']['complete_allowed_row_audit'] is True
    assert len(new.rows.segments) > 1
    assert len(new.rows.index) == len(bundle.bars)
    assert all(meta['bytes'] <= 2000 for name, meta in joint['audit_manifest']['files'].items()
               if name.startswith('rows-'))
    new.close()


class ReadMeter:
    def __init__(self, handle, meter):
        self.handle, self.meter = handle, meter

    def read(self, *args):
        value = self.handle.read(*args)
        self.meter['bytes'] += len(value)
        self.meter['calls'] += 1
        return value

    def __getattr__(self, name):
        return getattr(self.handle, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.handle.__exit__(*args)


def test_measured_joint_reads_replace_redundant_full_passes_without_omitting_rows(tmp_path, monkeypatch):
    bundle, plain, old, new = make(tmp_path)
    populate(bundle, plain, old, new)
    meter = {'bytes': 0, 'calls': 0}
    original = Path.open
    old_root = tmp_path / 'old'
    def measured(path, mode='r', *args, **kwargs):
        handle = original(path, mode, *args, **kwargs)
        return ReadMeter(handle, meter) if path.parent == old_root and 'r' in mode else handle
    monkeypatch.setattr(Path, 'open', measured)
    old.close()
    expected_hash = digest_json_stream(old.effective_dependencies())
    old_manifest = old.audit_manifest()
    before = new.read_metrics['actual_returned_read_bytes']
    proof = new.joint_dependency_digest_and_audit_manifest()
    new_bytes = new.read_metrics['actual_returned_read_bytes'] - before
    data_bytes = sum(meta['bytes'] for meta in old_manifest['files'].values())
    manifest_bytes = (tmp_path / 'new' / 'manifest.json').stat().st_size
    assert expected_hash == proof['dependency_hash'] == digest(plain.effective_dependencies())
    assert old_manifest['row_count'] == proof['row_count'] == 24
    assert meter['bytes'] - new_bytes == 2 * data_bytes - 2 * manifest_bytes
    assert new_bytes < meter['bytes']
    # Actual returned bytes, not elapsed-time inference or declared source size.
    print(json.dumps({'TEST_FIXTURE': True, 'old_returned_bytes': meter['bytes'],
                     'v2_joint_returned_bytes': new_bytes, 'saved_returned_bytes': meter['bytes']-new_bytes,
                     'complete_row_count': proof['row_count'], 'same_dependency_hash': proof['dependency_hash']}))


@pytest.mark.parametrize('target', ['payload', 'index', 'newline', 'truncated', 'extra_file', 'replaced_inode'])
def test_before_first_seal_write_ledger_rejects_modified_state(tmp_path, target):
    bundle, _, _, new = make(tmp_path)
    new.resolve(bundle.bars[0])
    new.flush()
    row = tmp_path / 'new' / 'rows-000000.ndjson'
    index = tmp_path / 'new' / 'record-index.ndjson'
    if target in ('payload', 'index'):
        changed_same_size(row if target == 'payload' else index)
    elif target == 'newline':
        raw = row.read_bytes()
        row.write_bytes(raw[:-1] + b' ')
    elif target == 'truncated':
        row.write_bytes(row.read_bytes()[:-1])
    elif target == 'extra_file':
        (tmp_path / 'new' / 'rows-999999.ndjson').write_bytes(b'{}\n')
    else:
        raw = row.read_bytes()
        row.unlink()
        row.write_bytes(raw)
    with pytest.raises(module.EvidenceAuditIntegrityError):
        new.joint_dependency_digest_and_audit_manifest()
    assert not (tmp_path / 'new' / 'manifest.json').exists()
    assert index.exists() and row.exists()
    assert new.failure_provenance[-1]['phase'] == 'JOINT'
    assert new.rows.writer.closed and new.rows.index_writer.closed


@pytest.mark.parametrize('target', ['payload', 'index', 'manifest', 'mtime', 'mode'])
def test_after_seal_whole_sha_and_all_seven_fp_fields_are_live(tmp_path, target):
    bundle, _, _, new = make(tmp_path)
    new.resolve(bundle.bars[0])
    new.close()
    path = tmp_path / 'new' / ({'index': 'record-index.ndjson', 'manifest': 'manifest.json'}.get(target,
                              'rows-000000.ndjson'))
    if target in ('payload', 'index', 'manifest'):
        changed_same_size(path)
    elif target == 'mtime':
        value = path.stat()
        os.utime(path, ns=(value.st_atime_ns, value.st_mtime_ns + 1000000))
    else:
        path.chmod(0o644)
    with pytest.raises(module.EvidenceAuditIntegrityError):
        new.joint_dependency_digest_and_audit_manifest()
    with pytest.raises(module.EvidenceAuditIntegrityError):
        digest_json_stream(new.effective_dependencies())


@pytest.mark.parametrize('operation', ['ordinary', 'joint'])
@pytest.mark.parametrize('target', ['payload', 'index'])
def test_mid_iteration_same_size_restored_mtime_mutation_is_rejected(tmp_path, monkeypatch, operation, target):
    bundle, _, _, new = make(tmp_path)
    populate(bundle, new)
    new.close()
    path = tmp_path / 'new' / ('record-index.ndjson' if target == 'index' else 'rows-000000.ndjson')
    original = new.rows._read_row
    once = []
    def tamper(key, pre=None):
        value = original(key, pre)
        if not once:
            once.append(True)
            changed_same_size(path)
        return value
    monkeypatch.setattr(new.rows, '_read_row', tamper)
    with pytest.raises(module.EvidenceAuditIntegrityError):
        if operation == 'joint':
            new.joint_dependency_digest_and_audit_manifest()
        else:
            digest_json_stream(new.effective_dependencies())
    assert once


@pytest.mark.parametrize('target', ['rows-000000.ndjson', 'record-index.ndjson'])
@pytest.mark.parametrize('kind', ['symlink', 'fifo'])
def test_nonregular_sources_are_refused_before_any_blocking_open(tmp_path, target, kind):
    bundle, _, _, new = make(tmp_path)
    new.resolve(bundle.bars[0])
    new.flush()
    path = tmp_path / 'new' / target
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
    assert time.monotonic() - started < 1


def test_duplicate_still_performs_fresh_disk_check_and_integrity_is_fatal(tmp_path):
    bundle, _, _, new = make(tmp_path)
    row = bundle.bars[0]
    assert new.resolve(row).allowed
    before = new.read_metrics['actual_returned_read_bytes']
    assert new.resolve(row).allowed
    assert new.read_metrics['actual_returned_read_bytes'] > before
    changed_same_size(tmp_path / 'new' / 'rows-000000.ndjson')
    with pytest.raises(module.EvidenceAuditIntegrityError, match='ORIGINAL_ROW_SHA'):
        new.resolve(row)


def test_wrong_key_append_only_no_disable_and_no_manifest_restore(tmp_path):
    bundle, _, _, new = make(tmp_path)
    with pytest.raises(module.EvidenceAuditIntegrityError, match='ROW_KEY_MISMATCH'):
        new.rows['0' * 64] = bundle.bars[0].model_dump(mode='json')
    with pytest.raises(TypeError, match='APPEND_ONLY'):
        del new.rows['0' * 64]
    with pytest.raises(ValueError, match='COMPLETE_ROW_RECORDING_REQUIRED'):
        module.SpoolingEvidenceResolverV2([], evidence_directory=tmp_path / 'disabled',
                                         cutoff=new.cutoff, coverage_type='TEST_FIXTURE', record_rows=False)
    with pytest.raises(FileExistsError):
        module.SpoolingEvidenceResolverV2([], evidence_directory=tmp_path / 'new',
                                         cutoff=new.cutoff, coverage_type='TEST_FIXTURE')
    proof = new.joint_dependency_digest_and_audit_manifest()
    changed = deepcopy(proof)
    changed['audit_manifest']['complete_allowed_row_audit'] = False
    assert new.joint_dependency_digest_and_audit_manifest()['audit_manifest']['complete_allowed_row_audit']
    with pytest.raises(TypeError):
        new.joint_dependency_digest_and_audit_manifest(changed)


def test_empty_joint_and_ordinary_digest_are_canonical(tmp_path):
    _, plain, _, new = make(tmp_path)
    proof = new.joint_dependency_digest_and_audit_manifest()
    assert proof['row_count'] == 0
    assert proof['dependency_hash'] == digest(plain.effective_dependencies())
    assert digest_json_stream(new.effective_dependencies()) == proof['dependency_hash']
    assert json.loads(json.dumps(new.effective_dependencies())) == plain.effective_dependencies()
    new.close()
    new.close()


def test_primary_and_cleanup_failure_provenance_preserve_partial_files(tmp_path):
    bundle, _, _, new = make(tmp_path)
    with pytest.raises(RuntimeError, match='TEST_FIXTURE_PRIMARY'):
        with new:
            new.resolve(bundle.bars[0])
            new.flush()
            changed_same_size(tmp_path / 'new' / 'record-index.ndjson')
            raise RuntimeError('TEST_FIXTURE_PRIMARY')
    phases = [entry['phase'] for entry in new.failure_provenance]
    assert phases == ['PRIMARY', 'CLOSE', 'CLEANUP']
    assert new.rows.writer.closed and new.rows.index_writer.closed
    assert (tmp_path / 'new' / 'record-index.ndjson').exists()
    assert not (tmp_path / 'new' / 'manifest.json').exists()


def test_writer_cleanup_closes_all_handles_even_if_fsync_fails(tmp_path, monkeypatch):
    bundle, _, _, new = make(tmp_path)
    new.resolve(bundle.bars[0])
    def refused(fd):
        raise OSError('TEST_FIXTURE_FSYNC_FAILURE')
    monkeypatch.setattr(module.os, 'fsync', refused)
    with pytest.raises(OSError, match='TEST_FIXTURE_FSYNC_FAILURE'):
        new.close()
    assert new.rows.writer.closed and new.rows.index_writer.closed
    assert (tmp_path / 'new' / 'record-index.ndjson').exists()
    assert new.failure_provenance[-1]['phase'] == 'CLOSE'


def test_source_state_mutation_cannot_be_hidden_by_a_valid_journal(tmp_path):
    bundle, _, _, new = make(tmp_path)
    new.resolve(bundle.bars[0])
    key = next(iter(new.used))
    new.used[key]['content_hash'] = '0' * 64
    with pytest.raises(module.EvidenceAuditIntegrityError, match='USED_SOURCE_CHANGED'):
        new.joint_dependency_digest_and_audit_manifest()


@pytest.mark.parametrize('file_name', ['record-index.ndjson', 'rows-000000.ndjson'])
def test_invalid_json_is_a_fatal_integrity_failure_not_unknown_evidence(tmp_path, file_name):
    bundle, _, _, new = make(tmp_path)
    new.resolve(bundle.bars[0])
    new.flush()
    path = tmp_path / 'new' / file_name
    raw = path.read_bytes()
    path.write_bytes(b'!' + raw[1:])
    with pytest.raises(module.EvidenceAuditIntegrityError, match='JSON_INVALID'):
        new.joint_dependency_digest_and_audit_manifest()
    assert not (tmp_path / 'new' / 'manifest.json').exists()


def test_parent_path_alias_is_refused_even_if_owned_directory_inode_is_unchanged(tmp_path):
    sandbox = tmp_path / 'sandbox'
    sandbox.mkdir()
    bundle, _, _, new = make(sandbox)
    new.resolve(bundle.bars[0])
    new.flush()
    moved = tmp_path / 'moved'
    sandbox.rename(moved)
    sandbox.symlink_to(moved, target_is_directory=True)
    with pytest.raises(module.EvidenceAuditIntegrityError, match='DIRECTORY_PATH_NOT_REGULAR'):
        new.joint_dependency_digest_and_audit_manifest()


def test_v1_bytes_remain_frozen():
    assert hashlib.sha256(Path(old_module.__file__).read_bytes()).hexdigest() == (
        'a8e7b9fd1293bd2f7e68475092955a15f5b24973911ce515ed5be83e60ff70fb')


def test_primary_budget_error_survives_reader_cleanup_mutation(tmp_path, monkeypatch):
    bundle, _, _, new = make(tmp_path)
    new.resolve(bundle.bars[0])
    new.flush()
    path = tmp_path / 'new' / 'record-index.ndjson'
    original = new.rows._read
    def budget_failure(source, size, *, line=False):
        original(source, size, line=line)
        changed_same_size(path)
        raise RuntimeError('TEST_FIXTURE_ORIGINAL_READ_BUDGET')
    monkeypatch.setattr(new.rows, '_read', budget_failure)
    with pytest.raises(RuntimeError, match='TEST_FIXTURE_ORIGINAL_READ_BUDGET'):
        new.joint_dependency_digest_and_audit_manifest()
    failures = new.failure_provenance
    assert any(entry['phase'] == 'READER_PRIMARY' and 'ORIGINAL_READ_BUDGET' in entry['error']
               for entry in failures)
    assert any(entry['phase'] == 'READER_CLEANUP' and 'READ_7FP_CHANGED' in entry['error']
               for entry in failures)
    assert failures[-1]['phase'] == 'JOINT' and 'ORIGINAL_READ_BUDGET' in failures[-1]['error']
    assert not (tmp_path / 'new' / 'manifest.json').exists()


def test_writer_close_error_still_attempts_other_writer(tmp_path):
    bundle, _, _, new = make(tmp_path)
    new.resolve(bundle.bars[0])
    attempted = []
    class CloseProxy:
        def __init__(self, handle, label, fail=False):
            self.handle, self.label, self.fail = handle, label, fail
        def __getattr__(self, name):
            return getattr(self.handle, name)
        def close(self):
            attempted.append(self.label)
            self.handle.close()
            if self.fail:
                raise OSError('TEST_FIXTURE_ORIGINAL_WRITER_CLOSE')
    new.rows.writer = CloseProxy(new.rows.writer, 'rows', fail=True)
    new.rows.index_writer = CloseProxy(new.rows.index_writer, 'index')
    with pytest.raises(OSError, match='TEST_FIXTURE_ORIGINAL_WRITER_CLOSE'):
        new.close()
    assert attempted == ['rows', 'index']
    assert new.rows.writer.closed and new.rows.index_writer.closed
    assert new.failure_provenance[-1]['phase'] == 'CLOSE'
    assert not (tmp_path / 'new' / 'manifest.json').exists()


def test_parent_alias_introduced_during_reader_is_rejected(tmp_path, monkeypatch):
    sandbox = tmp_path / 'sandbox'
    sandbox.mkdir()
    bundle, _, _, new = make(sandbox)
    new.resolve(bundle.bars[0])
    new.flush()
    original = new.rows._read
    changed = []
    def alias_parent(source, size, *, line=False):
        value = original(source, size, line=line)
        if not changed:
            changed.append(True)
            moved = tmp_path / 'moved'
            sandbox.rename(moved)
            sandbox.symlink_to(moved, target_is_directory=True)
        return value
    monkeypatch.setattr(new.rows, '_read', alias_parent)
    with pytest.raises(module.EvidenceAuditIntegrityError, match='DIRECTORY_PATH_NOT_REGULAR'):
        new.joint_dependency_digest_and_audit_manifest()
    assert changed


@pytest.mark.parametrize('operation', ['index', 'iteration'])
def test_ordinary_access_preserves_primary_budget_and_failed_post_verify(tmp_path, monkeypatch, operation):
    bundle, _, _, new = make(tmp_path)
    new.resolve(bundle.bars[0])
    new.close()
    original = new.rows._read_row
    def budget_failure(key, pre=None):
        original(key, pre)
        changed_same_size(tmp_path / 'new' / 'rows-000000.ndjson')
        raise RuntimeError('TEST_FIXTURE_PRIMARY_READ_BUDGET')
    monkeypatch.setattr(new.rows, '_read_row', budget_failure)
    with pytest.raises(RuntimeError, match='TEST_FIXTURE_PRIMARY_READ_BUDGET'):
        records = new.effective_dependencies()['records']
        if operation == 'index':
            records[0]
        else:
            digest_json_stream({'records': records})
    phase = 'ORDINARY_ACCESS' if operation == 'index' else 'ORDINARY_ITERATION'
    failures = new.failure_provenance
    assert any(entry['phase'] == phase + '_PRIMARY' and 'PRIMARY_READ_BUDGET' in entry['error']
               for entry in failures)
    assert any(entry['phase'] == phase + '_CLEANUP' and
               'ORIGINAL_ROW_SHA_OR_CANONICAL_MISMATCH' in entry['error'] for entry in failures)
    assert (tmp_path / 'new' / 'rows-000000.ndjson').exists()
    assert (tmp_path / 'new' / 'manifest.json').exists()


def test_manifest_publish_preserves_primary_append_and_secondary_close(tmp_path, monkeypatch):
    bundle, _, _, new = make(tmp_path)
    new.resolve(bundle.bars[0])
    original_writer, original_append = new.rows._new_writer, new.rows._append
    handles = []
    class ClosingProxy:
        def __init__(self, handle):
            self.handle = handle
        def __getattr__(self, name):
            return getattr(self.handle, name)
        def close(self):
            self.handle.close()
            raise OSError('TEST_FIXTURE_SECONDARY_MANIFEST_CLOSE')
    def writer(path):
        handle = original_writer(path)
        if path.name == 'manifest.json':
            handles.append(handle)
            return ClosingProxy(handle)
        return handle
    def append(path, handle, raw):
        if path.name == 'manifest.json':
            raise RuntimeError('TEST_FIXTURE_PRIMARY_MANIFEST_APPEND')
        return original_append(path, handle, raw)
    monkeypatch.setattr(new.rows, '_new_writer', writer)
    monkeypatch.setattr(new.rows, '_append', append)
    with pytest.raises(RuntimeError, match='TEST_FIXTURE_PRIMARY_MANIFEST_APPEND'):
        new.close()
    failures = new.failure_provenance
    assert any(entry['phase'] == 'PUBLISH_PRIMARY' and 'PRIMARY_MANIFEST_APPEND' in entry['error']
               for entry in failures)
    assert any(entry['phase'] == 'PUBLISH_CLEANUP' and 'SECONDARY_MANIFEST_CLOSE' in entry['error']
               for entry in failures)
    assert failures[-1]['phase'] == 'CLOSE' and 'PRIMARY_MANIFEST_APPEND' in failures[-1]['error']
    assert len(handles) == 1 and handles[0].closed
    assert new.rows.writer.closed and new.rows.index_writer.closed
    assert new.rows.sealed is None and new.rows._manifest_pin is None
    assert (tmp_path / 'new' / 'manifest.json').exists()
