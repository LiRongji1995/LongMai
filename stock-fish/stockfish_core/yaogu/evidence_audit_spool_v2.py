"""Explicit, isolated joint audit backend; no production caller uses this V2.

The write ledger is born from the bytes actually appended by this resolver.
Closing bytes, a saved manifest, or caller-supplied flags cannot create it.
Visibility and dependency ordering remain those of EvidenceResolver.
"""
from __future__ import annotations

from collections.abc import MutableMapping
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
from types import MappingProxyType

from .history_artifacts import digest_json_stream
from .visibility import EvidenceResolver, POLICY_ID

VERSION = 'EXACT_EVIDENCE_ROW_AUDIT_JOINT_SPOOL_V2'
SEGMENT_BYTES = 32 * 1024 * 1024
INDEX_LINE_BYTES = 4096
FP_FIELDS = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_flags')


class EvidenceAuditIntegrityError(RuntimeError):
    """Fatal audit integrity errors are not malformed evidence values."""


def _require(condition, reason):
    if not condition:
        raise EvidenceAuditIntegrityError('EVIDENCE_AUDIT_V2_' + reason)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False, default=str).encode('utf-8')


def _fp(value):
    return {name: getattr(value, name, 0) for name in FP_FIELDS}


def _parse(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise EvidenceAuditIntegrityError('EVIDENCE_AUDIT_V2_JSON_INVALID') from exc


class _CanonicalRecords(list):
    """Ordinary access authenticates the full journal, never a saved flag."""
    def __init__(self, journal):
        super().__init__()
        self._journal = journal

    def __len__(self):
        return len(self._journal)

    def __iter__(self):
        yield from self._journal.authenticated_records()

    def __getitem__(self, index):
        # Explicit slices are caller-requested materialization. The joint and
        # ordinary streaming paths retain only one canonical row at a time.
        pre = self._journal.verify()
        with self._journal._verified_operation(pre, 'ORDINARY_ACCESS'):
            keys = sorted(self._journal)
            chosen = keys[index]
            result = ([self._journal._read_row(key, pre) for key in chosen]
                      if isinstance(index, slice) else self._journal._read_row(chosen, pre))
        return result


class _JointRecords(list):
    """A local view created and exhausted only by the owned joint operation."""
    def __init__(self, journal, pre):
        super().__init__()
        self._journal, self._pre = journal, pre

    def __len__(self):
        return len(self._journal)

    def __iter__(self):
        for key in sorted(self._journal):
            yield self._journal._read_row(key, self._pre)


class _RowJournal(MutableMapping):
    def __init__(self, directory, segment_bytes):
        if (type(segment_bytes) is not int or not 0 < segment_bytes <= SEGMENT_BYTES):
            raise ValueError('EVIDENCE_AUDIT_V2_SEGMENT_LIMIT_INVALID')
        self.directory = Path(os.path.abspath(directory))
        for ancestor in reversed((self.directory.parent, *self.directory.parent.parents)):
            if ancestor.exists() or ancestor.is_symlink():
                _require(not ancestor.is_symlink() and ancestor.is_dir(), 'DIRECTORY_PATH_NOT_REGULAR')
        self.directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        self._directory_identity = self._identity(self.directory.lstat())
        self.limit = segment_bytes
        self._index, self._order, self._ledger = {}, [], {}
        self.segments = []
        self.writer = None
        self.index_path = self.directory / 'record-index.ndjson'
        self.index_writer = self._new_writer(self.index_path)
        self.closed = False
        self.sealed = None
        self._manifest_pin = None
        self.read_bytes = self.read_calls = 0
        self.failures = []

    @staticmethod
    def _identity(value):
        return value.st_dev, value.st_ino, value.st_mode

    @property
    def index(self):
        return MappingProxyType(self._index)

    def _directory_check(self):
        for ancestor in (self.directory.parent, *self.directory.parent.parents):
            value = ancestor.lstat()
            _require(stat.S_ISDIR(value.st_mode), 'DIRECTORY_PATH_NOT_REGULAR')
        value = self.directory.lstat()
        _require(stat.S_ISDIR(value.st_mode) and self._identity(value) == self._directory_identity,
                 'DIRECTORY_IDENTITY_CHANGED')
        if hasattr(self, '_ledger'):
            _require({path.name for path in self.directory.iterdir()} == set(self._ledger),
                     'DIRECTORY_FILE_COVERAGE_CHANGED')

    def _new_writer(self, path):
        self._directory_check()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(path, flags, 0o600)
        try:
            value = os.fstat(fd)
            _require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1, 'WRITER_NOT_REGULAR')
            handle = os.fdopen(fd, 'wb')
        except BaseException:
            os.close(fd)
            raise
        self._ledger[path.name] = {'identity': self._identity(value), 'bytes': 0, 'hasher': hashlib.sha256()}
        return handle

    def _append(self, path, handle, raw):
        _require(handle.write(raw) == len(raw), 'SHORT_WRITE')
        ledger = self._ledger[path.name]
        ledger['hasher'].update(raw)
        ledger['bytes'] += len(raw)

    @contextmanager
    def _reader(self, path, expected_fp=None):
        self._directory_check()
        before = path.lstat()
        _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'SOURCE_NOT_REGULAR')
        ledger = self._ledger[path.name]
        _require(self._identity(before) == ledger['identity'], 'SOURCE_IDENTITY_CHANGED')
        _require(before.st_size == ledger['bytes'], 'WRITE_LEDGER_SIZE_MISMATCH')
        if expected_fp is not None:
            _require(_fp(before) == expected_fp, 'SOURCE_7FP_CHANGED')
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(path, flags)
        try:
            _require(_fp(os.fstat(fd)) == _fp(before), 'OPENED_SOURCE_IDENTITY_CHANGED')
            handle = os.fdopen(fd, 'rb')
        except BaseException:
            os.close(fd)
            raise
        primary = None
        try:
            yield handle, _fp(before)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            cleanup_errors = []
            after_fd = None
            try:
                after_fd = _fp(os.fstat(handle.fileno()))
            except BaseException as exc:
                cleanup_errors.append(exc)
            try:
                handle.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
            try:
                _require(after_fd == _fp(before) and _fp(path.lstat()) == _fp(before), 'READ_7FP_CHANGED')
                self._directory_check()
            except BaseException as exc:
                cleanup_errors.append(exc)
            if cleanup_errors:
                if primary is not None:
                    self.failures.append({'phase': 'READER_PRIMARY',
                        'error': type(primary).__name__ + ':' + str(primary)})
                for exc in cleanup_errors:
                    self.failures.append({'phase': 'READER_CLEANUP',
                        'error': type(exc).__name__ + ':' + str(exc)})
                if primary is None:
                    raise cleanup_errors[0]

    def _read(self, source, size, *, line=False):
        raw = source.readline(size) if line else source.read(size)
        self.read_bytes += len(raw)
        self.read_calls += 1
        return raw

    def __len__(self):
        return len(self._index)

    def __iter__(self):
        return iter(self._index)

    def __delitem__(self, key):
        raise TypeError('EVIDENCE_AUDIT_V2_ROWS_ARE_APPEND_ONLY')

    def __setitem__(self, key, value):
        _require(not self.closed, 'ALREADY_CLOSED')
        raw = _json(value)
        _require(hashlib.sha256(raw).hexdigest() == key, 'ROW_KEY_MISMATCH')
        if key in self._index:
            # Preserve the V1 fresh disk SHA/canonical comparison. No LRU.
            _require(self[key] == _parse(raw), 'ROW_HASH_CONFLICT')
            return
        _require(len(raw) + 1 <= self.limit, 'ROW_EXCEEDS_SEGMENT_LIMIT')
        if self.writer is None or self.writer.tell() + len(raw) + 1 > self.limit:
            if self.writer is not None:
                self.writer.flush()
                os.fsync(self.writer.fileno())
                self.writer.close()
            path = self.directory / ('rows-%06d.ndjson' % len(self.segments))
            self.segments.append(path)
            self.writer = self._new_writer(path)
        position = len(self.segments) - 1, self.writer.tell(), len(raw)
        self._append(self.segments[position[0]], self.writer, raw + b'\n')
        entry = {'record_hash': key, 'segment': position[0], 'offset': position[1], 'bytes': position[2]}
        self._append(self.index_path, self.index_writer, _json(entry) + b'\n')
        self._index[key] = position
        self._order.append(key)

    def __getitem__(self, key):
        self.flush()
        return self._read_row(key)

    def _read_row(self, key, pre=None):
        segment, offset, size = self._index[key]
        path = self.segments[segment]
        expected = pre['files'][path.name]['fp'] if pre is not None else None
        with self._reader(path, expected) as (source, _):
            source.seek(offset)
            raw, ending = self._read(source, size), self._read(source, 1)
        _require(ending == b'\n' and len(raw) == size and hashlib.sha256(raw).hexdigest() == key,
                 'ORIGINAL_ROW_SHA_MISMATCH')
        row = _parse(raw)
        _require(_json(row) == raw, 'ROW_NOT_CANONICAL')
        return row

    def flush(self):
        if not self.closed:
            if self.writer is not None:
                self.writer.flush()
            self.index_writer.flush()

    def _scan(self):
        """One whole pass authenticates bytes, exact positions and coverage."""
        self.flush()
        files = {}
        entries = iter(self._order)
        with self._reader(self.index_path) as (source, pin):
            hasher = hashlib.sha256()
            count = 0
            while raw := self._read(source, INDEX_LINE_BYTES + 1, line=True):
                _require(len(raw) <= INDEX_LINE_BYTES and raw.endswith(b'\n'), 'INDEX_LINE_INVALID')
                hasher.update(raw)
                item = _parse(raw[:-1])
                key = next(entries, None)
                _require(key is not None and isinstance(item, dict) and set(item) ==
                         {'record_hash', 'segment', 'offset', 'bytes'}, 'INDEX_COVERAGE_MISMATCH')
                _require(_json(item) + b'\n' == raw and item['record_hash'] == key and
                         all(type(item[k]) is int for k in ('segment', 'offset', 'bytes')) and
                         tuple(item[k] for k in ('segment', 'offset', 'bytes')) == self._index[key],
                         'INDEX_POSITION_OR_CANONICAL_MISMATCH')
                count += 1
            _require(next(entries, None) is None and count == len(self._index), 'INDEX_COVERAGE_MISMATCH')
        _require(hasher.hexdigest() == self._ledger[self.index_path.name]['hasher'].hexdigest(),
                 'WRITE_LEDGER_SHA_MISMATCH')
        files[self.index_path.name] = {'bytes': pin['st_size'], 'sha256': hasher.hexdigest(), 'fp': pin}
        order = iter(self._order)
        next_key = next(order, None)
        for number, path in enumerate(self.segments):
            with self._reader(path) as (source, pin):
                hasher, offset = hashlib.sha256(), 0
                while raw := self._read(source, self.limit + 1, line=True):
                    _require(len(raw) <= self.limit and raw.endswith(b'\n'), 'ROW_LINE_INVALID')
                    hasher.update(raw)
                    _require(next_key is not None and self._index[next_key] == (number, offset, len(raw)-1),
                             'ROW_POSITION_OR_COVERAGE_MISMATCH')
                    row = _parse(raw[:-1])
                    _require(_json(row) + b'\n' == raw and hashlib.sha256(raw[:-1]).hexdigest() == next_key,
                             'ORIGINAL_ROW_SHA_OR_CANONICAL_MISMATCH')
                    offset += len(raw)
                    next_key = next(order, None)
            _require(hasher.hexdigest() == self._ledger[path.name]['hasher'].hexdigest(),
                     'WRITE_LEDGER_SHA_MISMATCH')
            files[path.name] = {'bytes': pin['st_size'], 'sha256': hasher.hexdigest(), 'fp': pin}
        _require(next_key is None, 'ROW_COVERAGE_MISMATCH')
        return {'schema_version': VERSION, 'policy_id': POLICY_ID, 'row_count': len(self._index),
                'segment_bytes_limit': self.limit, 'complete_allowed_row_audit': True,
                'authority': 'CURRENT_OWNED_WRITE_LEDGER_AND_ACTUAL_WHOLE_SHA_7FP', 'files': files}

    def _finish_writers(self):
        if self.closed:
            return
        errors = []
        handles = tuple(handle for handle in (self.writer, self.index_writer) if handle is not None)
        for handle in handles:
            if handle is not None and not handle.closed:
                try:
                    handle.flush()
                    os.fsync(handle.fileno())
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    try:
                        handle.close()
                    except BaseException as exc:
                        errors.append(exc)
        self.closed = all(handle.closed for handle in handles)
        if errors:
            raise errors[0]

    def _publish(self, manifest):
        path = self.directory / 'manifest.json'
        handle = self._new_writer(path)
        primary = None
        try:
            self._append(path, handle, _json(manifest))
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException as exc:
            primary = exc
            self.failures.append({'phase': 'PUBLISH_PRIMARY',
                                  'error': type(exc).__name__ + ':' + str(exc)})
            raise
        finally:
            try:
                handle.close()
            except BaseException as exc:
                self.failures.append({'phase': 'PUBLISH_CLEANUP',
                                      'error': type(exc).__name__ + ':' + str(exc)})
                if primary is None:
                    raise
        self._manifest_pin = self._manifest_check()
        self.sealed = deepcopy(manifest)

    def _manifest_check(self):
        path = self.directory / 'manifest.json'
        with self._reader(path, self._manifest_pin) as (source, pin):
            hasher = hashlib.sha256()
            while raw := self._read(source, 64 * 1024):
                hasher.update(raw)
        _require(hasher.hexdigest() == self._ledger[path.name]['hasher'].hexdigest(),
                 'MANIFEST_WRITE_LEDGER_SHA_MISMATCH')
        return pin

    def verify(self, expected=None):
        actual = self._scan()
        if self.sealed is not None:
            _require(actual == self.sealed, 'SEALED_JOURNAL_CHANGED')
            self._manifest_check()
        if expected is not None:
            _require(actual == expected, 'ITERATION_JOURNAL_CHANGED')
        return actual

    def close(self):
        try:
            self._finish_writers()
            actual = self.verify()
            if self.sealed is None:
                self._publish(actual)
            return actual
        except BaseException as exc:
            self.failures.append({'phase': 'CLOSE', 'error': type(exc).__name__ + ':' + str(exc)})
            raise

    @contextmanager
    def _verified_operation(self, pre, phase):
        primary = None
        try:
            yield
        except BaseException as exc:
            primary = exc
            self.failures.append({'phase': phase + '_PRIMARY',
                                  'error': type(exc).__name__ + ':' + str(exc)})
            raise
        finally:
            try:
                self.verify(pre)
            except BaseException as exc:
                self.failures.append({'phase': phase + '_CLEANUP',
                                      'error': type(exc).__name__ + ':' + str(exc)})
                if primary is None:
                    raise

    def authenticated_records(self):
        pre = self.verify()
        with self._verified_operation(pre, 'ORDINARY_ITERATION'):
            for key in sorted(self):
                yield self._read_row(key, pre)


class SpoolingEvidenceResolverV2(EvidenceResolver):
    """Explicit mathematical backend, never a source-reader capability."""
    def __init__(self, receipts, *, evidence_directory, segment_bytes=SEGMENT_BYTES, **kwargs):
        if kwargs.get('record_rows') is False:
            raise ValueError('EVIDENCE_AUDIT_V2_COMPLETE_ROW_RECORDING_REQUIRED')
        kwargs['record_rows'] = True
        super().__init__(receipts, **kwargs)
        self._receipt_identity = _json([self.receipts[k].model_dump(mode='json') for k in sorted(self.receipts)])
        self.rows = _RowJournal(evidence_directory, segment_bytes)

    def _sources(self):
        _require(_json([self.receipts[k].model_dump(mode='json') for k in sorted(self.receipts)]) ==
                 self._receipt_identity, 'RECEIPT_IDENTITY_CHANGED')
        result = []
        for key in sorted(self.used):
            _require(key in self.receipts and self.used[key] == self.receipts[key].model_dump(mode='json'),
                     'USED_SOURCE_CHANGED')
            result.append(deepcopy(self.used[key]))
        return result

    def effective_dependencies(self):
        self.flush()
        return {'policy_id': POLICY_ID, 'sources': self._sources(), 'records': _CanonicalRecords(self.rows)}

    def joint_dependency_digest_and_audit_manifest(self):
        """One owned epoch: full pre, canonical digest, full post; no cache."""
        try:
            self.rows._finish_writers()
            pre = self.rows.verify()
            if self.rows.sealed is None:
                self.rows._publish(pre)
            sources = self._sources()
            dependencies = {'policy_id': POLICY_ID, 'sources': sources, 'records': _JointRecords(self.rows, pre)}
            dependency_hash = digest_json_stream(dependencies)
            post = self.rows.verify(pre)
            _require(self._sources() == sources, 'USED_SOURCE_CHANGED_DURING_JOINT')
            return {'schema_version': VERSION, 'dependency_hash': dependency_hash,
                    'audit_manifest': deepcopy(post), 'row_count': len(self.rows),
                    'complete_allowed_row_audit': True,
                    'validation': 'PRE_AND_POST_WHOLE_SHA_7FP_ALL_CANONICAL_ROWS_AND_EXACT_INDEX',
                    'read_metrics': self.read_metrics}
        except BaseException as exc:
            self.rows.failures.append({'phase': 'JOINT', 'error': type(exc).__name__ + ':' + str(exc)})
            raise

    @property
    def read_metrics(self):
        return {'actual_returned_read_bytes': self.rows.read_bytes, 'actual_read_calls': self.rows.read_calls,
                'scope': 'V2_JOURNAL_EXPLICIT_READS_ONLY_NOT_ALL_KERNEL_OR_SOURCE_IO'}

    @property
    def failure_provenance(self):
        return deepcopy(self.rows.failures)

    def flush(self):
        self.rows.flush()

    def audit_manifest(self):
        return deepcopy(self.rows.verify())

    def close(self):
        return self.rows.close()

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        if value is not None:
            self.rows.failures.append({'phase': 'PRIMARY', 'error': kind.__name__ + ':' + str(value)})
        try:
            self.close()
        except BaseException as exc:
            self.rows.failures.append({'phase': 'CLEANUP', 'error': type(exc).__name__ + ':' + str(exc)})
            if value is None:
                raise
        return False
