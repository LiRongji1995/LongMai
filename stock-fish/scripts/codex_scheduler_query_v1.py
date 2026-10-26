"""Explicit scheduler job-family query extension; the original scheduler is retained.

status/explain are read-only. migrate requires the existing worker ownership lock
and changes only this versioned extension. serve is an opt-in local API entry;
neither importing this module nor migrating starts a service or a capture.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stockfish_core.common import utc_now
from stockfish_core.jobs import JobService, _ServiceLock
from stockfish_core.storage import (
    IntegrityError, ReadOnlySQLiteStore, StorageError, _validate_page,
    _verify_query_indexes,
)
from stockfish_core.yaogu.r2_closure_cli import _ExistingWritableStore
from stockfish_core.yaogu.scheduler import CandidateScheduler

VERSION = "CODEX_SCHEDULER_QUERY_V1"
TABLE = "codex_scheduler_query_v1"
INDEX = "codex_scheduler_job_family_v1"
KIND = "scheduled_candidate_capture"
TABLE_DDL = (f"CREATE TABLE {TABLE} (version TEXT PRIMARY KEY, "
             "ddl_hash TEXT NOT NULL, applied_at TEXT NOT NULL)")
INDEX_DDL = (f"CREATE INDEX {INDEX} ON jobs("
             "json_extract(payload,'$.root_job_id'),created_at DESC,job_id) "
             f"WHERE kind='{KIND}' AND json_type(payload,'$.root_job_id')='text'")


def _guard_ddl(name, operation):
    # Python json.loads retains the last duplicate key; SQLite json_extract
    # selects the first. Reject ambiguous roots rather than silently miss work.
    return (f"CREATE TRIGGER {name} BEFORE {operation} ON jobs "
            f"WHEN NEW.kind='{KIND}' AND (json_type(NEW.payload)!='object' OR "
            "(SELECT COUNT(*) FROM json_each(NEW.payload) WHERE key='root_job_id')>1) "
            "BEGIN SELECT RAISE(ABORT,'SCHEDULER_QUERY_AMBIGUOUS_JOB_PAYLOAD'); END")


OBJECTS = {
    TABLE: ("table", TABLE_DDL), INDEX: ("index", INDEX_DDL),
    "codex_scheduler_root_insert_v1": ("trigger", _guard_ddl(
        "codex_scheduler_root_insert_v1", "INSERT")),
    "codex_scheduler_root_update_v1": ("trigger", _guard_ddl(
        "codex_scheduler_root_update_v1", "UPDATE OF payload,kind")),
}
DDL_HASH = hashlib.sha256("\n".join(v[1] for v in OBJECTS.values()).encode()).hexdigest()
FAMILY_SQL = (f"SELECT * FROM jobs INDEXED BY {INDEX} WHERE kind='{KIND}' "
              "AND json_type(payload,'$.root_job_id')='text' "
              "AND json_extract(payload,'$.root_job_id')=?")
FAMILY_ORDER = " ORDER BY created_at DESC,job_id LIMIT ? OFFSET ?"
_FROZEN_PLAN = ContextVar("codex_scheduler_frozen_plan_v1", default=None)


def _normalized(sql):
    return re.sub(r"\s+", " ", sql.strip().rstrip(";"))


def _verify_extension(connection, *, allow_absent=False):
    names = tuple(OBJECTS)
    rows = {row["name"]: row for row in connection.execute(
        "SELECT name,type,sql FROM sqlite_master WHERE name IN (" +
        ",".join("?" for _ in names) + ")", names)}
    if not rows and allow_absent:
        return False
    if set(rows) != set(OBJECTS):
        raise IntegrityError("SCHEDULER_QUERY_EXTENSION_MISSING_OR_PARTIAL")
    for name, (kind, ddl) in OBJECTS.items():
        if rows[name]["type"] != kind or _normalized(rows[name]["sql"] or "") != _normalized(ddl):
            raise IntegrityError("SCHEDULER_QUERY_EXTENSION_DDL_MISMATCH")
    values = list(connection.execute(f"SELECT version,ddl_hash,applied_at FROM {TABLE}"))
    if (len(values) != 1 or values[0]["version"] != VERSION or
            values[0]["ddl_hash"] != DDL_HASH or not values[0]["applied_at"]):
        raise IntegrityError("SCHEDULER_QUERY_EXTENSION_RECEIPT_MISMATCH")
    return True


def _existing_database(path):
    value = Path(path).expanduser().absolute()
    if value.is_symlink() or not value.is_file() or value.resolve(strict=True) != value:
        raise ValueError("EXPLICIT_EXISTING_UNALIASED_DATABASE_REQUIRED")
    stat = value.stat()
    store = ReadOnlySQLiteStore(value)
    if store.readiness()["status"] != "healthy":
        raise ValueError("DATABASE_SCHEMA_NOT_READY_NO_AUTOMATIC_MIGRATION")
    with store.transaction(write=False) as connection:
        _verify_query_indexes(connection)
    return value, (stat.st_dev, stat.st_ino), store


def status(database):
    _, _, store = _existing_database(database)
    with store.transaction(write=False) as connection:
        installed = _verify_extension(connection, allow_absent=True)
    return {"version": VERSION, "status": "INSTALLED" if installed else "NOT_INSTALLED",
            "read_only": True, "default_service_changed": False, "ddl_hash": DDL_HASH}


def migrate(database):
    path, identity, _ = _existing_database(database)
    # Same process ownership boundary as the original JobService. No executor,
    # job recovery, default-store constructor, or automatic schema upgrade.
    lock = _ServiceLock(path.with_suffix(path.suffix + ".jobs.lock"))
    try:
        store = _ExistingWritableStore(path, expected_identity=identity)
        with store.transaction() as connection:
            if _verify_extension(connection, allow_absent=True):
                return {"version": VERSION, "status": "ALREADY_INSTALLED", "ddl_hash": DDL_HASH}
            invalid = connection.execute(
                f"SELECT job_id FROM jobs WHERE kind='{KIND}' AND (json_type(payload)!='object' OR "
                "(SELECT COUNT(*) FROM json_each(jobs.payload) WHERE key='root_job_id')>1) LIMIT 1"
            ).fetchone()
            if invalid:
                raise IntegrityError("SCHEDULER_QUERY_AMBIGUOUS_EXISTING_JOB_PAYLOAD")
            for _, ddl in OBJECTS.values():
                connection.execute(ddl)
            connection.execute(f"INSERT INTO {TABLE} VALUES(?,?,?)", (VERSION, DDL_HASH, utc_now().isoformat()))
            _verify_extension(connection)
        return {"version": VERSION, "status": "INSTALLED", "ddl_hash": DDL_HASH,
                "default_service_changed": False, "jobs_recovered": False}
    finally:
        lock.close()


def explain(database, root_job_id):
    _validate_root(root_job_id)
    _, _, store = _existing_database(database)
    with store.transaction(write=False) as connection:
        _verify_extension(connection)
        rows = connection.execute("EXPLAIN QUERY PLAN " + FAMILY_SQL + FAMILY_ORDER,
                                  (root_job_id, 1000, 0))
        plan = [dict(row) for row in rows]
    return {"version": VERSION, "read_only": True, "query_plan": plan}


def _validate_root(root):
    if not isinstance(root, str) or not root:
        raise ValueError("SCHEDULER_QUERY_NONEMPTY_ROOT_REQUIRED")


class FamilyQueryJobService(JobService):
    """Original workers and ordinary lists; indexed reads only inside one scope."""

    def __init__(self, store, **kwargs):
        self._family = ContextVar("codex_scheduler_job_family_" + str(id(self)), default=None)
        with store.transaction(write=False) as connection:
            _verify_extension(connection)
        super().__init__(store, **kwargs)

    @contextmanager
    def family_scope(self, root):
        _validate_root(root)
        token = self._family.set(root)
        try:
            yield
        finally:
            self._family.reset(token)

    def list(self, *, kind=None, state=None, limit=100, offset=0):
        root = self._family.get()
        if root is None or kind != KIND:
            return super().list(kind=kind, state=state, limit=limit, offset=offset)
        _validate_page(limit, offset)
        sql, parameters = FAMILY_SQL, [root]
        if state is not None:
            sql += " AND state=?"
            parameters.append(state)
        parameters.extend((limit, offset))
        with self.store.transaction(write=False) as connection:
            _verify_extension(connection)
            return [self._decode(row) for row in connection.execute(sql + FAMILY_ORDER, parameters)]


class FamilyQueryScheduler(CandidateScheduler):
    """Run the original family/retry algorithm with a scoped indexed list reader."""

    def _primary_schedule_for(self, signal_day, config=None):
        frozen = _FROZEN_PLAN.get()
        if frozen is not None and frozen[0] is self and frozen[1] == signal_day and config is None:
            return frozen[2]
        return super()._primary_schedule_for(signal_day, config)

    def _primary_jobs(self, signal_day):
        if not isinstance(self.service.jobs, FamilyQueryJobService):
            raise ValueError("SCHEDULER_QUERY_JOB_ADAPTER_REQUIRED")
        # Freeze exactly one original plan. Config/epoch changes cannot select
        # one root for fixed IDs and a different root for the indexed query.
        plan = super()._primary_schedule_for(signal_day)
        token = _FROZEN_PLAN.set((self, signal_day, plan))
        try:
            with self.service.jobs.family_scope(plan["job_id"]):
                return super()._primary_jobs(signal_day)
        finally:
            _FROZEN_PLAN.reset(token)


def build_application(database, state):
    """Build the explicit application without starting its scheduler or HTTP."""
    path, identity, _ = _existing_database(database)
    state = Path(state).expanduser().absolute()
    if state.is_symlink() or not state.is_dir() or state.resolve(strict=True) != state:
        raise ValueError("EXPLICIT_EXISTING_UNALIASED_STATE_REQUIRED")
    store = _ExistingWritableStore(path, expected_identity=identity)
    jobs = FamilyQueryJobService(store, max_workers=1, max_pending=20)
    core = None
    try:
        from stockfish_core.service import ApplicationService
        from stockfish_core.api import create_app
        core = ApplicationService(root=state, store=store, jobs=jobs)
        core.candidate_scheduler = FamilyQueryScheduler(core.candidates)
        return create_app(core), core
    except BaseException:
        if core is not None and core.candidate_scheduler is not None:
            core.candidate_scheduler.stop()
        jobs.shutdown(wait=True, cancel_pending=False)
        raise


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("status", "migrate", "explain", "serve"):
        command = commands.add_parser(name)
        command.add_argument("--database", type=Path, required=True)
        if name == "explain":
            command.add_argument("--root-job-id", required=True)
        if name == "serve":
            command.add_argument("--state", type=Path, required=True)
            command.add_argument("--host", choices=("127.0.0.1", "localhost", "::1"), default="127.0.0.1")
            command.add_argument("--port", type=int, required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "serve":
            if not 1 <= args.port <= 65535:
                raise ValueError("PORT_MUST_BE_1_TO_65535")
            from waitress import serve
            app, core = build_application(args.database, args.state)
            core.runtime_port = core.candidates.runtime_port = args.port
            try:
                core.candidate_scheduler.start()
                serve(app, host=args.host, port=args.port, threads=4)
            finally:
                core.close()
            return 0
        value = (explain(args.database, args.root_job_id) if args.command == "explain" else
                 {"status": status, "migrate": migrate}[args.command](args.database))
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, StorageError, OSError) as exc:
        print(json.dumps({"version": VERSION, "status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
