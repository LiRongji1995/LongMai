"""Transactional local storage. Immutable facts and mutable job state are separate.

SQLite is the durable source of truth; Python objects and caches are expendable.
Connections are short lived and never shared between worker threads.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Iterator

from .common import canonical_json, utc_now, UnavailableError


class StorageError(UnavailableError):
    def __init__(self, reason: str = "durable storage unavailable"):
        super().__init__(reason, component="storage")


class ImmutableConflict(ValueError):
    """An ID already identifies different immutable content."""


class IntegrityError(StorageError):
    """Persisted content failed an integrity check."""


_SCHEMA = """
CREATE TABLE documents (
 namespace TEXT NOT NULL,
 document_id TEXT NOT NULL,
 payload TEXT NOT NULL CHECK(json_valid(payload)),
 content_hash TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(namespace, document_id)
);
CREATE TABLE recommendation_records (
 recommendation_id TEXT PRIMARY KEY,
 root_recommendation_id TEXT NOT NULL REFERENCES recommendation_records(recommendation_id),
 revision INTEGER NOT NULL CHECK(revision > 0),
 supersedes TEXT REFERENCES recommendation_records(recommendation_id),
 run_id TEXT NOT NULL,
 instrument_id TEXT NOT NULL,
 recommendation_time TEXT NOT NULL,
 target_horizon INTEGER NOT NULL CHECK(target_horizon > 0),
 status TEXT NOT NULL,
 payload TEXT NOT NULL CHECK(json_valid(payload)),
 content_hash TEXT NOT NULL,
 UNIQUE(root_recommendation_id, revision)
);
CREATE INDEX recommendations_run ON recommendation_records(run_id);
CREATE INDEX recommendations_instrument_time ON recommendation_records(instrument_id, recommendation_time);
CREATE TABLE evaluation_observations (
 observation_id TEXT PRIMARY KEY,
 recommendation_id TEXT NOT NULL REFERENCES recommendation_records(recommendation_id),
 horizon INTEGER NOT NULL CHECK(horizon > 0),
 evaluation_date TEXT NOT NULL,
 method_version TEXT NOT NULL,
 payload TEXT NOT NULL CHECK(json_valid(payload)),
 content_hash TEXT NOT NULL
);
CREATE INDEX evaluations_recommendation ON evaluation_observations(recommendation_id, horizon, evaluation_date);
CREATE TABLE jobs (
 job_id TEXT PRIMARY KEY,
 kind TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCEL_REQUESTED','CANCELLED','INTERRUPTED')),
 progress REAL NOT NULL DEFAULT 0 CHECK(progress >= 0 AND progress <= 1),
 message TEXT NOT NULL DEFAULT '',
 payload TEXT NOT NULL CHECK(json_valid(payload)),
 data TEXT NOT NULL CHECK(json_valid(data)),
 result TEXT CHECK(result IS NULL OR json_valid(result)),
 error TEXT CHECK(error IS NULL OR json_valid(error)),
 created_at TEXT NOT NULL,
 started_at TEXT,
 finished_at TEXT,
 updated_at TEXT NOT NULL,
 owner_id TEXT,
 attempt INTEGER NOT NULL DEFAULT 1,
 retry_of TEXT REFERENCES jobs(job_id),
 cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
 revision INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX jobs_state ON jobs(state, created_at);
CREATE TABLE job_events (
 event_id INTEGER PRIMARY KEY AUTOINCREMENT,
 job_id TEXT NOT NULL REFERENCES jobs(job_id),
 state TEXT NOT NULL,
 progress REAL NOT NULL,
 recorded_at TEXT NOT NULL,
 detail TEXT NOT NULL CHECK(json_valid(detail))
);
CREATE TRIGGER documents_no_update BEFORE UPDATE ON documents BEGIN SELECT RAISE(ABORT, 'immutable documents'); END;
CREATE TRIGGER documents_no_delete BEFORE DELETE ON documents BEGIN SELECT RAISE(ABORT, 'immutable documents'); END;
CREATE TRIGGER recommendations_no_update BEFORE UPDATE ON recommendation_records BEGIN SELECT RAISE(ABORT, 'immutable recommendations'); END;
CREATE TRIGGER recommendations_no_delete BEFORE DELETE ON recommendation_records BEGIN SELECT RAISE(ABORT, 'immutable recommendations'); END;
CREATE TRIGGER evaluations_no_update BEFORE UPDATE ON evaluation_observations BEGIN SELECT RAISE(ABORT, 'immutable evaluations'); END;
CREATE TRIGGER evaluations_no_delete BEFORE DELETE ON evaluation_observations BEGIN SELECT RAISE(ABORT, 'immutable evaluations'); END;
CREATE TRIGGER job_events_no_update BEFORE UPDATE ON job_events BEGIN SELECT RAISE(ABORT, 'immutable job events'); END;
CREATE TRIGGER job_events_no_delete BEFORE DELETE ON job_events BEGIN SELECT RAISE(ABORT, 'immutable job events'); END;
"""

_MIGRATIONS = [(1, _SCHEMA)]


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def checked_payload(row: sqlite3.Row | dict) -> dict:
    raw = row["payload"]
    if _hash_text(raw) != row["content_hash"]:
        raise IntegrityError("persisted document hash mismatch")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise IntegrityError("persisted document is not an object")
    return value


class SQLiteStore:
    """WAL/FULL synchronous, schema-versioned, thread-safe by connection isolation."""

    def __init__(self, path: str | Path | None = None, *, timeout: float = 15.0):
        self.path = Path(path or os.environ.get("STOCKFISH_DB_PATH") or
                         Path(__file__).resolve().parents[1] / "data" / "stockfish.sqlite3").resolve()
        self.timeout = timeout
        self._health_lock = RLock()
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError("cannot create durable storage directory") from exc
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=self.timeout, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA busy_timeout={int(self.timeout * 1000)}")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA recursive_triggers=ON")
        except BaseException:
            connection.close()
            raise
        return connection

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            if connection:
                connection.rollback()
            raise StorageError("SQLite transaction failed") from exc
        except BaseException:
            if connection:
                connection.rollback()
            raise
        finally:
            if connection:
                connection.close()

    def _migrate(self) -> None:
        connection = None
        try:
            connection = self._connect()
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if mode.lower() != "wal":
                raise StorageError("durable storage requires SQLite WAL mode")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)")
            applied = {row["version"]: row["checksum"] for row in connection.execute("SELECT * FROM schema_migrations")}
            if set(applied) - {version for version, _ in _MIGRATIONS}:
                raise IntegrityError("database schema is newer than this runtime")
            for version, script in _MIGRATIONS:
                checksum = _hash_text(script)
                if version in applied:
                    if applied[version] != checksum:
                        raise IntegrityError("database migration checksum mismatch")
                    continue
                # executescript commits implicitly; execute complete statements instead.
                statement = ""
                for line in script.splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        connection.execute(statement)
                        statement = ""
                if statement.strip():
                    raise IntegrityError("incomplete migration statement")
                connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)",
                                   (version, checksum, utc_now().isoformat()))
            connection.commit()
        except sqlite3.Error as exc:
            if connection:
                connection.rollback()
            raise StorageError("database migration failed") from exc
        except BaseException:
            if connection:
                connection.rollback()
            raise
        finally:
            if connection:
                connection.close()

    @staticmethod
    def _put_document(connection: sqlite3.Connection, namespace: str,
                      document_id: str, payload: dict) -> str:
        if not isinstance(namespace, str) or not namespace or not isinstance(document_id, str) or not document_id:
            raise ValueError("namespace and document_id must be non-empty strings")
        if not isinstance(payload, dict):
            raise ValueError("document payload must be an object")
        raw = canonical_json(payload)
        checksum = _hash_text(raw)
        existing = connection.execute("SELECT * FROM documents WHERE namespace=? AND document_id=?",
                                      (namespace, document_id)).fetchone()
        if existing:
            checked_payload(existing)
            if existing["content_hash"] != checksum:
                raise ImmutableConflict(f"immutable document conflict: {namespace}/{document_id}")
            return document_id
        connection.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?)",
                           (namespace, document_id, raw, checksum, utc_now().isoformat()))
        return document_id

    def put_document(self, namespace: str, document_id: str, payload: dict) -> str:
        with self.transaction() as connection:
            return self._put_document(connection, namespace, document_id, payload)

    def get_document(self, namespace: str, document_id: str) -> dict | None:
        with self.transaction(write=False) as connection:
            row = connection.execute("SELECT * FROM documents WHERE namespace=? AND document_id=?",
                                     (namespace, document_id)).fetchone()
            return checked_payload(row) if row else None

    def list_documents(self, namespace: str, *, limit: int = 100, offset: int = 0) -> list[dict]:
        _validate_page(limit, offset)
        with self.transaction(write=False) as connection:
            rows = connection.execute("SELECT * FROM documents WHERE namespace=? ORDER BY created_at, document_id LIMIT ? OFFSET ?",
                                      (namespace, limit, offset)).fetchall()
            return [{"namespace": row["namespace"], "document_id": row["document_id"],
                     "content_hash": row["content_hash"], "created_at": row["created_at"],
                     "payload": checked_payload(row)} for row in rows]

    def list_document_metadata(self, namespace: str, *, limit: int = 100, offset: int = 0,
                               descending: bool = False) -> list[dict]:
        """Routing metadata only; callers must verify selected content before use."""
        _validate_page(limit, offset)
        direction = 'DESC' if descending else 'ASC'
        with self.transaction(write=False) as connection:
            rows = connection.execute(
                f'SELECT namespace,document_id,content_hash,created_at FROM documents WHERE namespace=? '
                f'ORDER BY created_at {direction},document_id {direction} LIMIT ? OFFSET ?',
                (namespace, limit, offset)).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _blob_hash(connection, table, rowid):
        checksum = hashlib.sha256()
        with connection.blobopen(table, 'payload', rowid, readonly=True) as blob:
            while chunk := blob.read(1024 * 1024):
                checksum.update(chunk)
        return checksum.hexdigest()

    def verified_document_metadata(self, namespace: str, document_id: str, *, expected_hash=None):
        """Hash current bytes, in a read transaction, without JSON object expansion.

        This is usable for a previously schema-validated projection only when
        the expected content address is supplied. It is not schema validation.
        """
        with self.transaction(write=False) as connection:
            row = connection.execute('SELECT rowid,namespace,document_id,content_hash,created_at FROM documents '
                                     'WHERE namespace=? AND document_id=?', (namespace, document_id)).fetchone()
            if row is None:
                return None
            if ((expected_hash is not None and row['content_hash'] != expected_hash)
                    or self._blob_hash(connection, 'documents', row['rowid']) != row['content_hash']):
                raise IntegrityError('persisted document hash mismatch')
            return {key: row[key] for key in ('namespace', 'document_id', 'content_hash', 'created_at')}

    def get_document_bytes(self, namespace: str, document_id: str, *, expected_hash=None):
        """Read and verify one immutable payload once, for a typed JSON consumer."""
        with self.transaction(write=False) as connection:
            row = connection.execute('SELECT rowid,content_hash FROM documents WHERE namespace=? AND document_id=?',
                                     (namespace, document_id)).fetchone()
            if row is None:
                return None
            if expected_hash is not None and row['content_hash'] != expected_hash:
                raise IntegrityError('persisted document content address mismatch')
            with connection.blobopen('documents', 'payload', row['rowid'], readonly=True) as blob:
                raw = blob.read()
            if hashlib.sha256(raw).hexdigest() != row['content_hash']:
                raise IntegrityError('persisted document hash mismatch')
            return raw

    def health(self) -> dict:
        # Multiple browser/startup polls must not expand the same large DB in
        # parallel. Every invocation still checks current persisted bytes.
        with self._health_lock:
            return self._health_checked()

    def _health_checked(self) -> dict:
        checked_at = utc_now().isoformat()
        scope = {'structure': 'CHECKED', 'payload_hashes': 'CHECKED_ALL_IMMUTABLE_FACT_TABLES',
                 'payload_json': 'SQLITE_JSON_OBJECT_CHECK_NO_PYTHON_OBJECT_EXPANSION',
                 'external_artifacts': 'NOT_CHECKED_USE_MODEL_REGISTRY_HEALTH',
                 'mutable_jobs': 'SQLITE_STRUCTURE_ONLY_NO_CONTENT_HASH'}
        try:
            with self.transaction(write=False) as connection:
                integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
                fk_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
                mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                schema_version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
                # Compare definitions, not merely names. A same-named no-op
                # trigger is not append-only enforcement.
                normalize = lambda sql: re.sub(r'\s+', ' ', sql.strip().rstrip(';')).strip()
                with sqlite3.connect(':memory:') as reference:
                    for _, script in _MIGRATIONS:
                        reference.executescript(script)
                    expected = {(r[0], r[1]): normalize(r[2]) for r in reference.execute(
                        "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL")}
                actual = {(r[0], r[1]): normalize(r[2]) for r in connection.execute(
                    "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL")}
                schema_mismatches = sorted(name for key, definition in expected.items()
                                           if actual.get(key) != definition for _, name in [key])
                trigger_valid = all(actual.get(key) == value for key, value in expected.items() if key[0] == 'trigger')
                migration_valid = {row['version']: row['checksum'] for row in connection.execute(
                    'SELECT version,checksum FROM schema_migrations')} == {v: _hash_text(s) for v, s in _MIGRATIONS}
                checked_counts, corrupt_counts = {}, {}
                for table in ('documents', 'recommendation_records', 'evaluation_observations'):
                    checked_counts[table], corrupt_counts[table] = 0, 0
                    for row in connection.execute(f"SELECT rowid,content_hash,CASE WHEN json_valid(payload) "
                                                  f"THEN json_type(payload) ELSE 'invalid' END AS json_kind FROM {table}"):
                        checked_counts[table] += 1
                        if (row['json_kind'] != 'object'
                                or self._blob_hash(connection, table, row['rowid']) != row['content_hash']):
                            corrupt_counts[table] += 1
                valid = (integrity == 'ok' and not fk_errors and mode == 'wal' and not schema_mismatches
                         and migration_valid and not any(corrupt_counts.values()))
                return {"component": "storage", "status": "healthy" if valid else "unavailable",
                        "journal_mode": mode, "integrity": integrity, "schema_version": schema_version,
                        "foreign_key_errors": len(fk_errors), "immutable_triggers": trigger_valid,
                        "path": str(self.path), 'checked_at': checked_at, 'scope': scope,
                        'schema_definition_mismatches': schema_mismatches, 'migration_checksums_valid': migration_valid,
                        'payload_rows_checked': checked_counts, 'corrupt_payload_rows': corrupt_counts,
                        'integrity_guarantee': 'LOCAL_CORRUPTION_DETECTION_NOT_FULL_DISK_WRITER_AUTHENTICATION'}
        except (StorageError, OSError, ValueError, TypeError, KeyError):
            return {"component": "storage", "status": "unavailable", "reason_codes": ["STORAGE_UNAVAILABLE"],
                    'checked_at': checked_at, 'scope': scope}


def _validate_page(limit: int, offset: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError("limit must be between 1 and 10000")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be non-negative")
