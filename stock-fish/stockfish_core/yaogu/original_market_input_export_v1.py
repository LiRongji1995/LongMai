"""Read the exact registered market-input bytes from their existing owner.

This is a local, explicitly selected, read-only export. It does not register an
input, expand compact history, deserialize evidence or issue a source capability.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import stat
import sys
import time

VERSION = 'ORIGINAL_MARKET_INPUT_EXPORT_V1'
FEATURE_VERSION = 'EXACT_REGISTERED_SQLITE_INPUT_BYTES_READ_ONLY_V1'
BLOCK_BYTES = 64 * 1024
RECEIPT_BYTES = 16 * 1024
FP_KEYS = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_flags')


@dataclass(frozen=True)
class Limits:
    wall_seconds: float = 120
    input_bytes: int = 256 * 1024**2
    process_rss_bytes: int = 256 * 1024**2

    def validate(self):
        maximum = type(self)()
        for key in ('wall_seconds', 'input_bytes', 'process_rss_bytes'):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= getattr(maximum, key):
                raise ValueError('REGISTERED_EXPORT_BUDGET_CANNOT_BE_EXPANDED')
            if key != 'wall_seconds' and type(value) is not int:
                raise ValueError('REGISTERED_EXPORT_BYTE_BUDGET_REQUIRES_INTEGER')
        return self


def _now():
    return datetime.now(timezone.utc).isoformat()


def _rss():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == 'darwin' else value * 1024)


def _fp(path):
    value = Path(path).lstat()
    return {key: getattr(value, key, 0) for key in FP_KEYS}


def _fd_fp(handle):
    value = os.fstat(handle.fileno())
    return {key: getattr(value, key, 0) for key in FP_KEYS}


@contextmanager
def _owned_reader(path, expected):
    path = Path(path)
    if (path.is_symlink() or not stat.S_ISREG(expected['st_mode'])
            or _fp(path) != expected):
        raise ValueError('EXPORT_OWNED_FILE_IDENTITY_CHANGED')
    # NONBLOCK prevents a replaced FIFO from defeating the wall guard between
    # lstat and open. Regular files still read normally after fd verification.
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as handle:
        if _fd_fp(handle) != expected or not stat.S_ISREG(_fd_fp(handle)['st_mode']):
            raise ValueError('EXPORT_OPENED_FILE_IDENTITY_CHANGED')
        yield handle


def _regular(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('EXPLICIT_DATABASE_SYMLINK_REJECTED')
    path = path.resolve(strict=True)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('EXISTING_REGULAR_DATABASE_REQUIRED')
    return path


class _Budget:
    def __init__(self, limits):
        self.limits = limits.validate()
        self.started = time.monotonic()
        self.read_bytes = 0

    def guard(self):
        if time.monotonic() - self.started >= self.limits.wall_seconds:
            raise TimeoutError('REGISTERED_EXPORT_WALL_BUDGET_EXCEEDED')
        if _rss() > self.limits.process_rss_bytes:
            raise MemoryError('REGISTERED_EXPORT_PROCESS_RSS_BUDGET_EXCEEDED')

    def read(self, count):
        self.read_bytes += count
        if self.read_bytes > 2 * self.limits.input_bytes:
            raise ValueError('REGISTERED_EXPORT_CUMULATIVE_READ_BUDGET_EXCEEDED')
        self.guard()


def _seal(path, value, identity):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                     allow_nan=False).encode('utf-8')
    if len(raw) + 1 > RECEIPT_BYTES:
        raise ValueError('REGISTERED_EXPORT_RECEIPT_RESERVE_EXCEEDED')
    path = Path(path)
    before = _fp(path)
    if path.is_symlink() or any(before[key] != identity[key] for key in identity):
        raise ValueError('EXPORT_RECEIPT_IDENTITY_CHANGED')
    fd = os.open(path, os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'r+b') as handle:
        opened = os.fstat(handle.fileno())
        if any(getattr(opened, key, 0) != identity[key] for key in identity):
            raise ValueError('EXPORT_RECEIPT_OPENED_IDENTITY_CHANGED')
        handle.seek(0)
        handle.write(raw + b'\n' + b' ' * (RECEIPT_BYTES - len(raw) - 1))
        handle.flush()
        os.fsync(handle.fileno())
    after = _fp(path)
    if any(after[key] != identity[key] for key in identity):
        raise ValueError('EXPORT_RECEIPT_POST_WRITE_IDENTITY_CHANGED')
    return {'sha256': hashlib.sha256(raw + b'\n' + b' ' * (RECEIPT_BYTES-len(raw)-1)).hexdigest(),
            'fingerprint': after}


def export_original_input(*, database, input_id, output, limits=Limits()):
    """Export one exact ID, preserving its original canonical text and hash.

    The receipt is a locator/integrity record, not evidence permission. The
    output's typed/schema/history validation remains with its native consumer.
    No latest-input fallback or reconstruction of compact partitions occurs.
    """
    if not isinstance(input_id, str) or re.fullmatch(r'market_[0-9a-f]{64}', input_id) is None:
        raise ValueError('EXACT_REGISTERED_MARKET_INPUT_ID_REQUIRED')
    limits.validate()
    source = _regular(database)
    directory = Path(output)
    if directory.is_symlink() or directory.exists():
        raise ValueError('EXPORT_OUTPUT_MUST_BE_NEW')
    parent = directory.parent.resolve(strict=True)
    directory = parent / directory.name
    if directory.name in ('', '.', '..'):
        raise ValueError('EXPORT_OUTPUT_MUST_BE_NEW')
    # URI mode=ro plus query_only; no CandidateService construction, migrations,
    # current-config activation, index rebuilding or readonly-looking writes.
    from ..storage import ReadOnlySQLiteStore
    store = ReadOnlySQLiteStore(source, timeout=min(1, limits.wall_seconds))
    budget = _Budget(limits)
    budget.guard()
    source_before = _fp(source)
    directory.mkdir(mode=0o700, exist_ok=False)
    receipt_path = directory / 'export-receipt.json'
    with receipt_path.open('xb') as handle:
        handle.write(b' ' * RECEIPT_BYTES)
        handle.flush()
        os.fsync(handle.fileno())
    receipt_identity = {key: _fp(receipt_path)[key] for key in ('st_dev', 'st_ino', 'st_mode', 'st_size')}
    value = dict(version=VERSION, feature_version=FEATURE_VERSION, status='RUNNING',
                 started_at=_now(), input_id=input_id, input_hash=input_id[7:],
                 limits=asdict(limits), database_path=str(source),
                 database_initial_fingerprint=source_before, output_directory=str(directory),
                 source_snapshot='ONE_SQLITE_READ_TRANSACTION_EXACT_DOCUMENT_ID',
                 sqlite_uri_mode='ro', sqlite_query_only=True,
                 input_original_bytes_preserved=False, typed_schema_verified=False,
                 history_owner_authenticated=False,
                 history_owner_status='NOT_EXPORTED_OR_RESTORED_FROM_RECEIPT',
                 source_capability_restored_from_JSON=False,
                 database_created_or_migrated=False, selector_capture_network_calls=0,
                 market_edge='NOT_ESTABLISHED')
    try:
        _seal(receipt_path, value, receipt_identity)
        budget.guard()
        partial = directory / 'input.partial'
        expected_hash = input_id[7:]
        with store.transaction(write=False) as connection:
            budget.guard()
            row = connection.execute('SELECT rowid,content_hash,created_at '
                'FROM documents WHERE namespace=? AND document_id=?', ('candidate_inputs', input_id)).fetchone()
            budget.guard()
            if row is None:
                raise ValueError('EXACT_REGISTERED_INPUT_MISSING')
            if row['content_hash'] != expected_hash:
                raise ValueError('REGISTERED_INPUT_CONTENT_ADDRESS_MISMATCH')
            if not hasattr(connection, 'blobopen'):
                raise ValueError('BOUNDED_SQLITE_BLOB_READER_REQUIRED')
            hasher, count = hashlib.sha256(), 0
            with connection.blobopen('documents', 'payload', row['rowid'], readonly=True) as blob:
                # len(blob) is the byte count without CAST/json_extract or a
                # full TEXT/BLOB allocation before the preflight gate.
                byte_length = len(blob)
                if not 0 < byte_length <= limits.input_bytes:
                    raise ValueError('REGISTERED_INPUT_EMPTY_OR_OVER_BYTE_BUDGET')
                value.update(original_created_at=row['created_at'], original_bytes=byte_length)
                with partial.open('xb') as handle:
                    while True:
                        budget.guard()
                        raw = blob.read(BLOCK_BYTES)
                        budget.read(len(raw))
                        if not raw:
                            break
                        count += len(raw)
                        if count > byte_length or count > limits.input_bytes:
                            raise ValueError('REGISTERED_INPUT_STREAM_SIZE_CHANGED')
                        hasher.update(raw)
                        handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                    written_fingerprint = _fd_fp(handle)
                    if _fp(partial) != written_fingerprint or not stat.S_ISREG(written_fingerprint['st_mode']):
                        raise ValueError('EXPORT_WRITTEN_PARTIAL_IDENTITY_CHANGED')
            budget.guard()
            if count != byte_length or hasher.hexdigest() != expected_hash:
                raise ValueError('REGISTERED_INPUT_ORIGINAL_BYTES_HASH_MISMATCH')
            # Transaction closes only after the complete original payload has
            # been copied and verified. Appends to unrelated IDs are permitted.
        target = directory / 'input.json'
        # New exclusive directory has no preexisting destination. Do not use a
        # JSON dump, expand bars, copy a database or alter the market_ address.
        # link is atomic and fails if another file appeared at the final name;
        # rename's prior exists check would permit an intervening replacement.
        if partial.is_symlink() or _fp(partial) != written_fingerprint:
            raise ValueError('EXPORT_PARTIAL_PRE_PUBLISH_IDENTITY_CHANGED')
        os.link(partial, target, follow_symlinks=False)
        linked = _fp(target)
        if (target.is_symlink() or not stat.S_ISREG(linked['st_mode'])
                or any(linked[key] != written_fingerprint[key]
                       for key in ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_flags'))):
            raise ValueError('EXPORT_PUBLISHED_FILE_IDENTITY_CHANGED')
        partial.unlink()
        before = _fp(target)
        actual, counted = hashlib.sha256(), 0
        with _owned_reader(target, before) as handle:
            while True:
                budget.guard()
                raw = handle.read(BLOCK_BYTES)
                budget.read(len(raw))
                if not raw:
                    break
                counted += len(raw)
                actual.update(raw)
        if counted != count or actual.hexdigest() != expected_hash or _fp(target) != before:
            raise ValueError('EXPORTED_ORIGINAL_BYTES_CHANGED')
        source_after = _fp(source)
        if any(source_after[key] != source_before[key] for key in ('st_dev', 'st_ino', 'st_mode')):
            raise ValueError('SOURCE_DATABASE_IDENTITY_CHANGED')
        value.update(status='PASS', input_original_bytes_preserved=True,
                     input_path=str(target), input_sha256=actual.hexdigest(),
                     input_fingerprint=before, cumulative_read_bytes=budget.read_bytes,
                     database_terminal_fingerprint=source_after,
                     database_fingerprint_change_is_not_a_WAL_snapshot_hash=True,
                     finished_at=_now(), elapsed_seconds=time.monotonic()-budget.started,
                     os_peak_rss_bytes=_rss())
        sealed = _seal(receipt_path, value, receipt_identity)
        budget.guard()
        with _owned_reader(receipt_path, sealed['fingerprint']) as handle:
            receipt_bytes = handle.read(RECEIPT_BYTES + 1)
        budget.guard()
        if (_fp(target) != before or _fp(receipt_path) != sealed['fingerprint']
                or len(receipt_bytes) != RECEIPT_BYTES
                or hashlib.sha256(receipt_bytes).hexdigest() != sealed['sha256']):
            raise ValueError('EXPORT_TERMINAL_FILE_CHANGED')
        return value
    except BaseException as exc:
        # Preserve all original/partial bytes and the failed attempt. A new
        # attempt needs a new namespace; never delete evidence or auto-retry.
        value.update(status='FAILED', failure_type=type(exc).__name__,
                     failure_reason=str(exc)[:1024], finished_at=_now(),
                     cumulative_read_bytes=budget.read_bytes,
                     elapsed_seconds=time.monotonic()-budget.started,
                     os_peak_rss_bytes=_rss())
        try:
            _seal(receipt_path, value, receipt_identity)
        except (OSError, ValueError) as seal_error:
            # Preserve the foreign/replaced path. An exclusive failure record
            # uses the second fixed 16KiB control reserve and never follows it.
            value['failure_receipt_reason'] = str(seal_error)[:1024]
            fallback = directory / 'export-failure.json'
            with fallback.open('xb') as handle:
                handle.write(b' ' * RECEIPT_BYTES)
                handle.flush()
                os.fsync(handle.fileno())
            fallback_identity = {key: _fp(fallback)[key] for key in ('st_dev', 'st_ino', 'st_mode', 'st_size')}
            _seal(fallback, value, fallback_identity)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description='只读导出精确已登记input原字节；不授来源或历史owner权限')
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--input-id', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = export_original_input(database=args.database, input_id=args.input_id, output=args.output)
    except Exception as exc:
        print(json.dumps({'status': 'FAILED', 'reason': str(exc), 'output': str(args.output)}, ensure_ascii=False))
        return 1
    print(json.dumps({key: result[key] for key in ('version', 'status', 'input_id', 'input_path',
          'input_sha256', 'original_bytes', 'history_owner_status', 'elapsed_seconds')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
