"""Private, bounded journals for the existing resolver's complete row audit.

This is an execution backend, not an evidence policy or a business database.
All visibility decisions remain in EvidenceResolver. Every allowed row is
written and included in its original, sorted canonical dependency identity.
"""
from __future__ import annotations

from collections.abc import MutableMapping
import hashlib
import json
from pathlib import Path

from .visibility import EvidenceResolver, POLICY_ID

VERSION = 'EXACT_EVIDENCE_ROW_AUDIT_SPOOL_V1'
SEGMENT_BYTES = 32 * 1024 * 1024


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False, default=str).encode('utf-8')


def _file_hash(path):
    result = hashlib.sha256()
    with path.open('rb') as source:
        for part in iter(lambda: source.read(1024 * 1024), b''):
            result.update(part)
    return result.hexdigest()


class _CanonicalRecords(list):
    """JSONEncoder-compatible list view, loading exactly one record at a time."""
    def __init__(self, journal):
        super().__init__()
        self.journal = journal

    def __len__(self):
        return len(self.journal)

    def __iter__(self):
        self.journal.verify_seal()
        for key in sorted(self.journal):
            yield self.journal[key]

    def __getitem__(self, index):
        keys = sorted(self.journal)
        if isinstance(index, slice):
            return [self.journal[key] for key in keys[index]]
        return self.journal[keys[index]]


class _RowJournal(MutableMapping):
    def __init__(self, directory, segment_bytes=SEGMENT_BYTES):
        if not isinstance(segment_bytes, int) or isinstance(segment_bytes, bool) or segment_bytes <= 0:
            raise ValueError('EVIDENCE_AUDIT_SEGMENT_LIMIT_INVALID')
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.limit = segment_bytes
        self.index = {}
        self.segments = []
        self.writer = None
        self.closed = False
        self.sealed = None
        self.index_path = self.directory / 'record-index.ndjson'
        self.index_writer = self.index_path.open('xb')

    def __len__(self):
        return len(self.index)

    def __iter__(self):
        return iter(self.index)

    def __delitem__(self, key):
        raise TypeError('EVIDENCE_AUDIT_ROWS_ARE_APPEND_ONLY')

    def __setitem__(self, key, value):
        if self.closed:
            raise ValueError('EVIDENCE_AUDIT_ALREADY_SEALED')
        raw = _json(value)
        if hashlib.sha256(raw).hexdigest() != key:
            raise ValueError('EVIDENCE_AUDIT_ROW_KEY_MISMATCH')
        if key in self.index:
            if self[key] != json.loads(raw):
                raise ValueError('EVIDENCE_AUDIT_ROW_HASH_CONFLICT')
            return
        if len(raw) + 1 > self.limit:
            raise ValueError('EVIDENCE_AUDIT_ROW_EXCEEDS_SEGMENT_LIMIT')
        if self.writer is None or self.writer.tell() + len(raw) + 1 > self.limit:
            if self.writer is not None:
                self.writer.close()
            path = self.directory / ('rows-%06d.ndjson' % len(self.segments))
            self.segments.append(path)
            self.writer = path.open('xb')
        position = (len(self.segments) - 1, self.writer.tell(), len(raw))
        self.writer.write(raw + b'\n')
        self.index_writer.write(_json({'record_hash': key, 'segment': position[0],
                                      'offset': position[1], 'bytes': position[2]}) + b'\n')
        self.index[key] = position

    def __getitem__(self, key):
        segment, offset, size = self.index[key]
        self.flush()
        with self.segments[segment].open('rb') as source:
            source.seek(offset)
            raw = source.read(size)
            ending = source.read(1)
        if ending != b'\n' or len(raw) != size or hashlib.sha256(raw).hexdigest() != key:
            raise ValueError('EVIDENCE_AUDIT_ORIGINAL_ROW_SHA_MISMATCH')
        row = json.loads(raw)
        if _json(row) != raw:
            raise ValueError('EVIDENCE_AUDIT_ROW_NOT_CANONICAL')
        return row

    def flush(self):
        if not self.closed:
            if self.writer is not None:
                self.writer.flush()
            self.index_writer.flush()

    def _manifest(self):
        self.flush()
        paths = self.segments + [self.index_path]
        return {'schema_version': VERSION, 'policy_id': POLICY_ID,
                'row_count': len(self.index), 'segment_bytes_limit': self.limit,
                'complete_allowed_row_audit': True,
                'files': {path.name: {'bytes': path.stat().st_size, 'sha256': _file_hash(path)}
                          for path in paths}}

    def verify_seal(self):
        if self.closed and self.sealed != self._manifest():
            raise ValueError('EVIDENCE_AUDIT_SEALED_JOURNAL_CHANGED')

    def close(self):
        if self.closed:
            self.verify_seal()
            return
        self.flush()
        if self.writer is not None:
            self.writer.close()
        self.index_writer.close()
        self.closed = True
        self.sealed = self._manifest()
        path = self.directory / 'manifest.json'
        with path.open('xb') as destination:
            destination.write(_json(self.sealed))


class SpoolingEvidenceResolver(EvidenceResolver):
    def __init__(self, receipts, *, evidence_directory, segment_bytes=SEGMENT_BYTES, **kwargs):
        if kwargs.get('record_rows') is False:
            raise ValueError('EVIDENCE_AUDIT_COMPLETE_ROW_RECORDING_REQUIRED')
        kwargs['record_rows'] = True
        super().__init__(receipts, **kwargs)
        self.rows = _RowJournal(evidence_directory, segment_bytes)

    def effective_dependencies(self):
        self.flush()
        return {'policy_id': POLICY_ID, 'sources': [self.used[key] for key in sorted(self.used)],
                'records': _CanonicalRecords(self.rows)}

    def flush(self):
        self.rows.flush()

    def audit_manifest(self):
        self.rows.verify_seal()
        return self.rows._manifest()

    def close(self):
        self.rows.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
