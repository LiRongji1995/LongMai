"""Explicit local-admin archive ingestion and bounded daily observations.

No default database, server startup, source acquisition, or automatic recovery
of unrelated work. Read commands never construct a writable service.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from threading import RLock

from ..storage import SQLiteStore


ARCHIVE_COMMANDS = frozenset({'r2-archive-verify', 'r2-archive-import', 'r2-daily-cycle'})
COMMANDS = ARCHIVE_COMMANDS | frozenset({'r2-daily-plan', 'r2-daily-pending',
    'r2-daily-run', 'r2-daily-status', 'r2-daily-retry'})


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest='command', required=True)
    for name in sorted(COMMANDS):
        command = commands.add_parser(name)
        if name in ARCHIVE_COMMANDS:
            command.add_argument('archive', type=Path, help='Exact original sealed premarket job directory')
            command.add_argument('--expected-final-sha256', required=True,
                help='Explicit pinned SHA256 of original final-control.json')
            command.add_argument('--source-overrides', type=Path,
                help='Original source bytes mapping; does not execute archived code')
        if name != 'r2-archive-verify':
            command.add_argument('--db', type=Path, required=True, help='Existing explicitly selected service database')
            command.add_argument('--state', type=Path, required=True, help='Existing corresponding service state directory')
        if name in {'r2-daily-plan', 'r2-daily-run', 'r2-daily-cycle'}:
            command.add_argument('--run-id', action='append', required=True)
        if name in {'r2-daily-plan', 'r2-daily-run', 'r2-daily-cycle', 'r2-daily-retry'}:
            command.add_argument('--max-runs', type=int, default=5)
        if name in {'r2-daily-plan', 'r2-daily-run', 'r2-daily-pending'}:
            command.add_argument('--input-id', required=True, help='Exact newly authenticated archive input ID')
        if name == 'r2-daily-pending':
            command.add_argument('--limit', type=int, default=100)
            command.add_argument('--offset', type=int, default=0)
        if name in {'r2-daily-status', 'r2-daily-retry'}:
            command.add_argument('job_id')
        if name in {'r2-daily-run', 'r2-daily-retry', 'r2-daily-cycle'}:
            command.add_argument('--wait-seconds', type=float, default=120,
                help='Wait budget, 1..3600 seconds; expiry cancels only newly started work')
        command.add_argument('--allow-test-fixture', action='store_true',
            help='Only for an explicitly marked isolated TEST_FIXTURE directory')
    return result


def _overrides(path):
    if path is None:
        return None
    from .premarket_runtime_v1 import strict_json
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError('SOURCE_OVERRIDES_FILE_LIMIT')
    with path.open('rb') as handle:
        raw = handle.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('SOURCE_OVERRIDES_FILE_LIMIT')
    value = strict_json(raw)
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'files'}
            or value['schema_version'] != 'SHORT_HORIZON_ARCHIVE_SOURCE_OVERRIDES_V1'
            or not isinstance(value['files'], dict)):
        raise ValueError('SOURCE_OVERRIDES_SCHEMA_REQUIRED')
    for key, target in value['files'].items():
        if (not isinstance(key, str) or Path(key).is_absolute() or '..' in Path(key).parts
                or '\\' in key or not isinstance(target, str) or not Path(target).is_absolute()):
            raise ValueError('SOURCE_OVERRIDES_RELATIVE_KEY_ABSOLUTE_TARGET_REQUIRED')
    return value['files']


def _fixture_boundary(args):
    if not args.allow_test_fixture:
        return
    root = args.archive if args.command == 'r2-archive-verify' else args.state
    production = Path(__file__).resolve().parents[2] / 'data'
    resolved = root.resolve(strict=True)
    marker = resolved / 'TEST_FIXTURE_ONLY'
    if (resolved.is_relative_to(production.resolve()) or not marker.is_file()
            or marker.read_bytes() != b'TEST_FIXTURE\n'):
        raise ValueError('EXPLICIT_ISOLATED_TEST_FIXTURE_MARKER_REQUIRED')
    if hasattr(args, 'db') and not args.db.resolve(strict=True).is_relative_to(resolved):
        raise ValueError('FIXTURE_DATABASE_MUST_BE_INSIDE_FIXTURE_STATE')


class _ReadJobs:
    def __init__(self, store):
        self.store = store

    def get(self, job_id):
        from ..jobs import JobService
        return JobService.get(self, job_id)

    def events(self, job_id):
        from ..jobs import JobService
        return JobService.events(self, job_id)

    def list(self, **kwargs):
        from ..jobs import JobService
        return JobService.list(self, **kwargs)

    @staticmethod
    def _decode(row):
        from ..jobs import JobService
        return JobService._decode(row)


class _ExistingWritableStore(SQLiteStore):
    """Open only an existing V2 database; never invoke schema migration.

    Every transaction validates the schema while holding that transaction's
    SQLite lock, so a preceding read-only check cannot authorize later upgrades.
    """
    def __init__(self, path, *, expected_identity, timeout=15.0):
        self.path, self.timeout = Path(path).resolve(strict=True), timeout
        self._health_lock = RLock()
        self._identity = expected_identity
        self._check_file()

    def _check_file(self):
        stat = self.path.stat()
        if self.path.is_symlink() or (stat.st_dev, stat.st_ino) != self._identity:
            raise ValueError('DATABASE_FILE_IDENTITY_CHANGED')

    def _connect(self):
        self._check_file()
        connection = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True,
            timeout=self.timeout, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('PRAGMA recursive_triggers=ON')
            self._check_file()
            return connection
        except BaseException:
            connection.close()
            raise

    @contextmanager
    def transaction(self, *, write=True):
        from ..storage import _MIGRATIONS, _hash_text, _verify_query_indexes
        with super().transaction(write=write) as connection:
            applied = {row['version']: row['checksum'] for row in
                connection.execute('SELECT version,checksum FROM schema_migrations')}
            expected = {version: _hash_text(script) for version, script in _MIGRATIONS}
            if (applied != expected or connection.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'wal'):
                raise ValueError('DATABASE_SCHEMA_NOT_READY_NO_AUTOMATIC_MIGRATION')
            _verify_query_indexes(connection)
            yield connection
            self._check_file()

    def _migrate(self):
        raise ValueError('LOCAL_ADMIN_AUTOMATIC_MIGRATION_FORBIDDEN')


def _cancel_owned(jobs):
    # Only this invocation's actual futures; never scan or cancel old queues.
    with jobs._lock:
        owned = tuple(jobs._futures)
    failures = []
    for job_id in owned:
        try:
            jobs.cancel(job_id)
        except Exception as exc:
            failures.append({'job_id': job_id, **_safe_error(exc)})
    return {'cancel_requested_job_ids': list(owned), 'cancel_failures': failures}


def _candidate_view(store, jobs, state, allow_test_fixture):
    from .candidate_service import CandidateService
    # Deliberately bypass startup config/projection migrations. These commands
    # only call saved-run readers and the explicit bridge/closure contracts.
    service = object.__new__(CandidateService)
    service.store, service.jobs = store, jobs
    service.root = state / 'yaogu'
    service.allow_test_fixture = allow_test_fixture
    return service


@contextmanager
def service_context(args, *, writable=False):
    from ..storage import ReadOnlySQLiteStore, _verify_query_indexes
    from ..jobs import JobService
    if args.db.is_symlink() or not args.db.is_file() or not args.state.is_dir():
        raise ValueError('EXPLICIT_EXISTING_DATABASE_AND_STATE_REQUIRED')
    database, state = args.db.resolve(strict=True), args.state.resolve(strict=True)
    stat = database.stat()
    identity = stat.st_dev, stat.st_ino
    readonly = ReadOnlySQLiteStore(database)
    if readonly.readiness()['status'] != 'healthy':
        raise ValueError('DATABASE_SCHEMA_NOT_READY_NO_AUTOMATIC_MIGRATION')
    with readonly.transaction(write=False) as connection:
        _verify_query_indexes(connection)
    if not writable:
        yield _candidate_view(readonly, _ReadJobs(readonly), state, args.allow_test_fixture)
        return
    # Acquire the existing single-writer worker lock BEFORE opening a mutable
    # store. recover=False preserves unrelated queued/interrupted work.
    jobs = JobService(readonly, max_workers=1, max_pending=20, recover=False)
    try:
        jobs.store = _ExistingWritableStore(database, expected_identity=identity)
        yield _candidate_view(jobs.store, jobs, state, args.allow_test_fixture)
    except KeyboardInterrupt:
        _cancel_owned(jobs)
        raise
    finally:
        # Never cancel other jobs just because this local-admin process exits.
        jobs.shutdown(wait=True, cancel_pending=False)


def _wait(closure, job, seconds, *, cancel_owned):
    from ..jobs import TERMINAL_STATES
    if not 1 <= seconds <= 3600:
        raise ValueError('WAIT_SECONDS_MUST_BE_1_TO_3600')
    deadline = time.monotonic() + seconds
    try:
        while True:
            result = closure.status(job['job_id'])
            state = result.get('state') or (result.get('job') or {}).get('state')
            if state in TERMINAL_STATES:
                return (0 if state == 'SUCCEEDED' else 3), result
            if time.monotonic() >= deadline:
                if cancel_owned:
                    closure.cancel(job['job_id'])
                return 4, {'status': 'WAIT_BUDGET_EXCEEDED', 'job_id': job['job_id'],
                    'cancel_requested': bool(cancel_owned), 'result': closure.status(job['job_id'])}
            time.sleep(min(.1, max(0, deadline - time.monotonic())))
    except KeyboardInterrupt:
        if cancel_owned:
            closure.cancel(job['job_id'])
        raise


def _job_wait(service, closure, job, seconds):
    owned = (job['job_id'] in service.jobs._futures
             or job.get('owner_id') == service.jobs.owner_id)
    return _wait(closure, job, seconds, cancel_owned=owned)


def _safe_error(exc):
    reason = str(exc)
    if 'another job worker service owns this database' in reason:
        reason = 'DATABASE_BUSY_ACTIVE_SERVICE'
    if not re.fullmatch(r'[A-Z0-9_:.-]{1,180}', reason):
        reason = 'LOCAL_R2_CLOSURE_FAILED'
    return {'error_type': type(exc).__name__, 'reason': reason}


def execute(args):
    _fixture_boundary(args)
    if hasattr(args, 'wait_seconds') and not 1 <= args.wait_seconds <= 3600:
        raise ValueError('WAIT_SECONDS_MUST_BE_1_TO_3600')
    if args.command in ARCHIVE_COMMANDS:
        from .short_horizon_archive_bridge_v1 import authenticate_archive, commit_bridge
        capability = authenticate_archive(args.archive,
            expected_final_sha256=args.expected_final_sha256,
            source_overrides=_overrides(args.source_overrides),
            allow_test_fixture=args.allow_test_fixture)
        if args.command == 'r2-archive-verify':
            return 0, capability.audit()
        with service_context(args, writable=True) as service:
            imported = commit_bridge(service, capability)
            if args.command == 'r2-archive-import':
                return 0, imported
            # Import and observation are two durable stages. A failed daily
            # job never pretends the successful archive import was rolled back.
            try:
                # Daily planning/worker reauthenticate independently. Do not
                # retain the import session's large run across those stages.
                import gc
                del capability
                gc.collect()
                from .short_horizon_daily_closure_v1 import ShortHorizonDailyClosure
                closure = ShortHorizonDailyClosure(service, max_runs=args.max_runs)
                job = closure.submit(args.run_id, imported['input_id'])
                code, result = _job_wait(service, closure, job, args.wait_seconds)
                return code, {'status': 'ARCHIVE_IMPORTED_CLOSURE_FINISHED' if code == 0
                    else 'ARCHIVE_IMPORTED_CLOSURE_INCOMPLETE', 'bridge': imported, 'closure': result}
            except KeyboardInterrupt:
                return 130, {'status': 'ARCHIVE_IMPORTED_CLOSURE_INTERRUPTED', 'bridge': imported,
                    'reason': 'USER_INTERRUPTED', **_cancel_owned(service.jobs)}
            except Exception as exc:
                return 3, {'status': 'ARCHIVE_IMPORTED_CLOSURE_FAILED', 'bridge': imported,
                    **_safe_error(exc)}
    from .short_horizon_daily_closure_v1 import ShortHorizonDailyClosure
    write = args.command in {'r2-daily-run', 'r2-daily-retry'}
    with service_context(args, writable=write) as service:
        closure = ShortHorizonDailyClosure(service, max_runs=getattr(args, 'max_runs', 5))
        if args.command == 'r2-daily-plan':
            return 0, closure.plan(args.run_id, args.input_id)
        if args.command == 'r2-daily-pending':
            return 0, closure.pending(args.input_id, limit=args.limit, offset=args.offset)
        if args.command == 'r2-daily-status':
            return 0, closure.status(args.job_id)
        if args.command == 'r2-daily-retry':
            job = closure.retry(args.job_id)
        else:
            job = closure.submit(args.run_id, args.input_id)
        # This process holds the sole job-service lock; other active processes
        # are rejected before any mutable database construction.
        return _job_wait(service, closure, job, args.wait_seconds)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        code, result = execute(args)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return code
    except KeyboardInterrupt:
        print(json.dumps({'status': 'INTERRUPTED', 'reason': 'USER_INTERRUPTED'}), file=sys.stderr)
        return 130
    except Exception as exc:
        print(json.dumps({'status': 'FAILED', **_safe_error(exc)}), file=sys.stderr)
        return 3
