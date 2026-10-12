"""Explicit compressed, owned evidence journal; no production wiring.

Physical pre/post scans authenticate the actual append ledger. The one joint
canonical pass independently decodes every gzip frame and charges every byte
actually returned by the decoder. A saved manifest cannot recreate the owner.
"""
from __future__ import annotations

from copy import deepcopy
import gzip
import hashlib
from pathlib import Path
import zlib

from . import evidence_audit_spool_v2 as v2
from .history_artifacts import digest_json_stream
from .visibility import EvidenceResolver, POLICY_ID

VERSION = 'EXACT_EVIDENCE_ROW_AUDIT_COMPRESSED_JOINT_SPOOL_V3'
SEGMENT_BYTES = v2.SEGMENT_BYTES
CHUNK_BYTES = 64 * 1024
INDEX_LINE_BYTES = v2.INDEX_LINE_BYTES
FP_FIELDS = v2.FP_FIELDS


# Reuse the frozen fatal type: inherited V2 path/ledger guards raise this same
# type. A subclass would incorrectly exclude their genuine failures.
EvidenceAuditIntegrityError = v2.EvidenceAuditIntegrityError


def _require(condition, reason):
    if not condition:
        raise EvidenceAuditIntegrityError('EVIDENCE_AUDIT_V3_' + reason)


class _CompressedRowJournal(v2._RowJournal):
    def __init__(self, directory, segment_bytes, logical_read_callback):
        self.logical_read_callback = logical_read_callback
        self.logical_read_bytes = self.logical_read_calls = 0
        self._fatal = None
        self._logical_sizes, self._logical_hashers = [], []
        super().__init__(directory, segment_bytes)

    def _guard(self):
        if self._fatal is not None:
            raise self._fatal

    def _failed(self, exc, phase):
        if self._fatal is None:
            self._fatal = exc
        self.failures.append({'phase': phase, 'error': type(exc).__name__ + ':' + str(exc)})

    def _logical_returned(self, raw):
        # Count before the live callback: a chunk that crosses its limit really
        # was returned. Preserve its original exception object, including a
        # ValueError that EvidenceResolver would otherwise convert to UNKNOWN.
        self.logical_read_bytes += len(raw)
        self.logical_read_calls += 1
        if raw and self.logical_read_callback is not None:
            try:
                self.logical_read_callback(raw)
            except BaseException as exc:
                self._failed(exc, 'LOGICAL_READ_CALLBACK')
                raise

    @staticmethod
    def _compress(raw):
        return gzip.compress(raw, compresslevel=6, mtime=0)

    def __setitem__(self, key, value):
        self._guard()
        # Keep ordinary envelope serialization errors as the original resolver
        # semantics; they are not budget failures or stored-journal corruption.
        raw = v2._json(value)
        try:
            _require(not self.closed, 'ALREADY_CLOSED')
            _require(hashlib.sha256(raw).hexdigest() == key, 'ROW_KEY_MISMATCH')
            if key in self._index:
                _require(self[key] == v2._parse(raw), 'ROW_HASH_CONFLICT')
                return
            logical = raw + b'\n'
            _require(len(logical) <= self.limit, 'ROW_EXCEEDS_SEGMENT_LIMIT')
            frame = self._compress(logical)
            _require(0 < len(frame) <= 2 * len(logical) + CHUNK_BYTES, 'FRAME_SIZE_BOUND')
            if self.writer is None or self._logical_sizes[-1] + len(logical) > self.limit:
                if self.writer is not None:
                    self.writer.flush()
                    v2.os.fsync(self.writer.fileno())
                    self.writer.close()
                path = self.directory / ('rows-%06d.ndjson.gzframes' % len(self.segments))
                self.segments.append(path)
                self._logical_sizes.append(0)
                self._logical_hashers.append(hashlib.sha256())
                self.writer = self._new_writer(path)
            number = len(self.segments) - 1
            position = (number, self.writer.tell(), len(frame), len(logical),
                        hashlib.sha256(frame).hexdigest())
            self._append(self.segments[number], self.writer, frame)
            self._logical_sizes[number] += len(logical)
            self._logical_hashers[number].update(logical)
            entry = dict(zip(('segment', 'offset', 'compressed_bytes', 'logical_bytes', 'frame_sha256'),
                             position))
            entry['record_hash'] = key
            self._append(self.index_path, self.index_writer, v2._json(entry) + b'\n')
            self._index[key] = position
            self._order.append(key)
        except BaseException as exc:
            self._failed(exc, 'APPEND')
            raise

    def __getitem__(self, key):
        self._guard()
        try:
            self.flush()
            return self._read_row(key)
        except BaseException as exc:
            self._failed(exc, 'ROW_ACCESS')
            raise

    def __delitem__(self, key):
        raise TypeError('EVIDENCE_AUDIT_V3_ROWS_ARE_APPEND_ONLY')

    def _decode_frame(self, source, compressed_bytes, logical_bytes, frame_sha256, key):
        decoder = zlib.decompressobj(wbits=31)
        hasher, chunks, returned = hashlib.sha256(), [], 0
        remaining = compressed_bytes
        try:
            while remaining:
                size = min(CHUNK_BYTES, remaining)
                raw = self._read(source, size)
                _require(len(raw) == size, 'FRAME_TRUNCATED')
                hasher.update(raw)
                remaining -= len(raw)
                pending = raw
                while True:
                    decoded = decoder.decompress(pending, CHUNK_BYTES)
                    self._logical_returned(decoded)
                    returned += len(decoded)
                    _require(returned <= logical_bytes and returned <= self.limit,
                             'DECOMPRESSED_LENGTH_LIMIT')
                    chunks.append(decoded)
                    pending = decoder.unconsumed_tail
                    if decoder.eof:
                        _require(not decoder.unused_data and not pending and remaining == 0,
                                 'TRAILING_OR_MULTIPLE_GZIP_FRAME')
                        break
                    if pending or len(decoded) == CHUNK_BYTES:
                        continue
                    break
            _require(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail,
                     'FRAME_CRC_OR_EOF_INCOMPLETE')
            _require(returned == logical_bytes and hasher.hexdigest() == frame_sha256,
                     'FRAME_SHA_OR_LOGICAL_LENGTH_MISMATCH')
        except zlib.error as exc:
            raise EvidenceAuditIntegrityError('EVIDENCE_AUDIT_V3_GZIP_CRC_OR_FORMAT_INVALID') from exc
        logical = b''.join(chunks)
        _require(logical.endswith(b'\n') and len(logical) == logical_bytes and
                 hashlib.sha256(logical[:-1]).hexdigest() == key, 'ORIGINAL_LOGICAL_ROW_SHA_MISMATCH')
        row = v2._parse(logical[:-1])
        _require(v2._json(row) + b'\n' == logical, 'LOGICAL_ROW_NOT_CANONICAL')
        return row

    def _read_row(self, key, pre=None):
        self._guard()
        try:
            number, offset, compressed, logical, frame_sha = self._index[key]
            path = self.segments[number]
            expected = pre['files'][path.name]['fp'] if pre is not None else None
            with self._reader(path, expected) as (source, _):
                source.seek(offset)
                return self._decode_frame(source, compressed, logical, frame_sha, key)
        except BaseException as exc:
            self._failed(exc, 'LOGICAL_ROW_READ')
            raise

    def _scan(self):
        """Whole physical bytes + owned logical write mapping, no inflation.

        CRC/canonical decoding belongs to the complete canonical pass, once.
        This scan never advertises that it performed a logical decode.
        """
        self.flush()
        files, order = {}, iter(self._order)
        with self._reader(self.index_path) as (source, pin):
            hasher, count = hashlib.sha256(), 0
            while raw := self._read(source, INDEX_LINE_BYTES + 1, line=True):
                _require(len(raw) <= INDEX_LINE_BYTES and raw.endswith(b'\n'), 'INDEX_LINE_INVALID')
                hasher.update(raw)
                item, key = v2._parse(raw[:-1]), next(order, None)
                fields = ('segment', 'offset', 'compressed_bytes', 'logical_bytes', 'frame_sha256')
                _require(key is not None and isinstance(item, dict) and
                         set(item) == {'record_hash', *fields}, 'INDEX_COVERAGE_MISMATCH')
                _require(v2._json(item) + b'\n' == raw and item['record_hash'] == key and
                         all(type(item[k]) is int for k in fields[:-1]) and
                         tuple(item[k] for k in fields) == self._index[key],
                         'INDEX_POSITION_OR_CANONICAL_MISMATCH')
                count += 1
            _require(next(order, None) is None and count == len(self._index), 'INDEX_COVERAGE_MISMATCH')
        _require(hasher.hexdigest() == self._ledger[self.index_path.name]['hasher'].hexdigest(),
                 'WRITE_LEDGER_SHA_MISMATCH')
        files[self.index_path.name] = {'bytes': pin['st_size'], 'sha256': hasher.hexdigest(), 'fp': pin}
        order, next_key = iter(self._order), None
        next_key = next(order, None)
        for number, path in enumerate(self.segments):
            with self._reader(path) as (source, pin):
                hasher, offset, logical_total = hashlib.sha256(), 0, 0
                while next_key is not None and self._index[next_key][0] == number:
                    segment, position, compressed, logical, frame_sha = self._index[next_key]
                    _require(segment == number and position == offset and type(compressed) is int and
                             type(logical) is int and 0 < logical <= self.limit and
                             0 < compressed <= 2 * logical + CHUNK_BYTES,
                             'FRAME_POSITION_OR_LENGTH_MISMATCH')
                    frame_hasher, remaining = hashlib.sha256(), compressed
                    while remaining:
                        size = min(CHUNK_BYTES, remaining)
                        raw = self._read(source, size)
                        _require(len(raw) == size, 'FRAME_TRUNCATED')
                        hasher.update(raw)
                        frame_hasher.update(raw)
                        remaining -= len(raw)
                    _require(frame_hasher.hexdigest() == frame_sha, 'WRITTEN_FRAME_SHA_MISMATCH')
                    offset += compressed
                    logical_total += logical
                    _require(logical_total <= self.limit, 'LOGICAL_SEGMENT_LIMIT')
                    next_key = next(order, None)
                _require(offset == pin['st_size'] and logical_total == self._logical_sizes[number],
                         'FRAME_WHOLE_POSITION_OR_COVERAGE_MISMATCH')
            _require(hasher.hexdigest() == self._ledger[path.name]['hasher'].hexdigest(),
                     'WRITE_LEDGER_SHA_MISMATCH')
            files[path.name] = {'bytes': pin['st_size'], 'sha256': hasher.hexdigest(), 'fp': pin,
                               'logical_bytes': logical_total,
                               'logical_sha256': self._logical_hashers[number].hexdigest()}
        _require(next_key is None, 'ROW_COVERAGE_MISMATCH')
        return {'schema_version': VERSION, 'policy_id': POLICY_ID, 'row_count': len(self._index),
                'segment_bytes_limit': self.limit, 'complete_allowed_row_audit': False,
                'physical_write_ledger_audit_complete': True,
                'logical_decode_validation': 'NOT_PERFORMED_BY_PHYSICAL_SCAN',
                'logical_canonical_bytes': sum(self._logical_sizes),
                'authority': 'CURRENT_OWNED_APPEND_LEDGER_AND_ACTUAL_WHOLE_SHA_7FP', 'files': files}

    def verify(self, expected=None):
        self._guard()
        try:
            return super().verify(expected)
        except BaseException as exc:
            self._failed(exc, 'PHYSICAL_VERIFY')
            raise

    def close(self):
        if self._fatal is not None:
            try:
                self._finish_writers()
            except BaseException as exc:
                self.failures.append({'phase': 'CLOSE_CLEANUP',
                                      'error': type(exc).__name__ + ':' + str(exc)})
            raise self._fatal
        try:
            return super().close()
        except BaseException as exc:
            self._failed(exc, 'CLOSE_PRIMARY')
            raise


_RowJournal = _CompressedRowJournal
_JointRecords = v2._JointRecords


class SpoolingEvidenceResolverV3(v2.SpoolingEvidenceResolverV2):
    """Explicit compressed backend, not a source-reader or callback capability."""
    def __init__(self, receipts, *, evidence_directory, segment_bytes=SEGMENT_BYTES,
                 logical_read_callback=None, **kwargs):
        if kwargs.get('record_rows') is False:
            raise ValueError('EVIDENCE_AUDIT_V3_COMPLETE_ROW_RECORDING_REQUIRED')
        if logical_read_callback is not None and not callable(logical_read_callback):
            raise TypeError('EVIDENCE_AUDIT_V3_LOGICAL_READ_CALLBACK_REQUIRED')
        if kwargs.get('coverage_type') != 'TEST_FIXTURE' and logical_read_callback is None:
            raise ValueError('EVIDENCE_AUDIT_V3_LIVE_LOGICAL_ACCOUNTING_REQUIRED')
        kwargs['record_rows'] = True
        EvidenceResolver.__init__(self, receipts, **kwargs)
        self._receipt_identity = v2._json([self.receipts[k].model_dump(mode='json')
                                         for k in sorted(self.receipts)])
        self.rows = _CompressedRowJournal(evidence_directory, segment_bytes, logical_read_callback)

    def resolve(self, *args, **kwargs):
        self.rows._guard()
        try:
            return super().resolve(*args, **kwargs)
        finally:
            self.rows._guard()

    def joint_dependency_digest_and_audit_manifest(self):
        self.rows._guard()
        try:
            self.rows._finish_writers()
            pre = self.rows.verify()
            if self.rows.sealed is None:
                self.rows._publish(pre)
            sources = self._sources()
            dependencies = {'policy_id': POLICY_ID, 'sources': sources,
                            'records': _JointRecords(self.rows, pre)}
            before = self.rows.logical_read_bytes
            dependency_hash = digest_json_stream(dependencies)
            _require(self.rows.logical_read_bytes - before == pre['logical_canonical_bytes'],
                     'COMPLETE_CANONICAL_LOGICAL_BYTE_COVERAGE')
            post = self.rows.verify(pre)
            _require(self._sources() == sources, 'USED_SOURCE_CHANGED_DURING_JOINT')
            complete = deepcopy(post)
            complete.update(complete_allowed_row_audit=True,
                            logical_decode_validation='ALL_CANONICAL_ROWS_GZIP_CRC_EOF_SHA_AND_NEWLINE',
                            canonical_decoded_row_count=len(self.rows))
            return {'schema_version': VERSION, 'dependency_hash': dependency_hash,
                    'audit_manifest': complete, 'row_count': len(self.rows),
                    'complete_allowed_row_audit': True,
                    'validation': 'PRE_POST_WHOLE_SHA_7FP_AND_ALL_CANONICAL_FRAMES_CRC_EOF_EXACT_INDEX',
                    'read_metrics': self.read_metrics}
        except BaseException as exc:
            self.rows._failed(exc, 'JOINT')
            raise

    def effective_dependencies(self):
        self.rows._guard()
        return super().effective_dependencies()

    def audit_manifest(self):
        return self.joint_dependency_digest_and_audit_manifest()['audit_manifest']

    @property
    def read_metrics(self):
        physical, logical = self.rows.read_bytes, self.rows.logical_read_bytes
        return {'actual_returned_read_bytes': physical + logical,
                'actual_physical_returned_read_bytes': physical,
                'actual_logical_decompressed_returned_bytes': logical,
                'actual_read_calls': self.rows.read_calls + self.rows.logical_read_calls,
                'actual_physical_read_calls': self.rows.read_calls,
                'actual_logical_decode_calls': self.rows.logical_read_calls,
                'scope': 'V3_EXPLICIT_PHYSICAL_READS_PLUS_ACTUAL_LOGICAL_DECODER_RETURNS_NOT_ALL_KERNEL_IO',
                'logical_callback_role': 'LIVE_ACCOUNTING_ONLY_NOT_SOURCE_AUTHENTICATION'}


def implementation_source_identity():
    """Version identity only; never issues or restores a source capability."""
    root = Path(__file__).parents[1]
    files = {'module_v3': Path(__file__), 'frozen_spool_v2': Path(v2.__file__),
             'visibility': root / 'yaogu/visibility.py',
             'history_artifacts': root / 'yaogu/history_artifacts.py',
             'spec_v2': root.parent / 'docs/EVIDENCE_AUDIT_SPOOL_V2.md',
             'spec_v3': root.parent / 'docs/EVIDENCE_AUDIT_SPOOL_V3.md'}
    return {'implementation_version': VERSION, 'policy_id': POLICY_ID,
            'source_file_hashes': {key: hashlib.sha256(path.read_bytes()).hexdigest()
                                   for key, path in files.items()},
            'role': 'IMPLEMENTATION_IDENTITY_NOT_SOURCE_CAPABILITY'}


def source_identity():
    """Consumer-friendly relative source pins; never a serialized capability."""
    root = Path(__file__).parents[2]
    paths = (Path(__file__), Path(v2.__file__),
             root / 'stockfish_core/yaogu/visibility.py',
             root / 'stockfish_core/yaogu/history_artifacts.py',
             root / 'docs/EVIDENCE_AUDIT_SPOOL_V2.md',
             root / 'docs/EVIDENCE_AUDIT_SPOOL_V3.md')
    hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in paths}
    return {'implementation_version': VERSION, 'policy_id': POLICY_ID,
            'source_file_hashes': hashes, 'source_hash': digest_json_stream(hashes),
            'role': 'IMPLEMENTATION_IDENTITY_NOT_SOURCE_CAPABILITY'}
