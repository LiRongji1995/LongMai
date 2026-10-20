"""Transactional local storage. Immutable facts and mutable job state are separate.

SQLite is the durable source of truth; Python objects and caches are expendable.
Connections are never shared between worker threads. An explicit worker scope
may reuse an idle read connection while every transaction remains short lived.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import stat
from threading import RLock, get_ident, local
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

_QUERY_INDEXES_V2 = """
CREATE INDEX IF NOT EXISTS documents_namespace_created ON documents(namespace,created_at,document_id);
CREATE INDEX IF NOT EXISTS job_events_job_order ON job_events(job_id,event_id);
CREATE INDEX IF NOT EXISTS strategy_item_observation_lookup ON documents(namespace,json_extract(payload,'$.run_id'),json_extract(payload,'$.instrument_id'),created_at,document_id);
"""

# V1 is frozen. The existing CandidateService expression index is adopted under
# its original name rather than building a duplicate over every document.
_MIGRATIONS = [(1, _SCHEMA), (2, _QUERY_INDEXES_V2)]


def _verify_query_indexes(connection):
    for statement in _QUERY_INDEXES_V2.strip().splitlines():
        expected = statement.replace(" IF NOT EXISTS", "").rstrip(";")
        name = expected.split()[2]
        row = connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
        if row is None or re.sub(r"\s+", " ", row[0].strip().rstrip(";")) != expected:
            raise IntegrityError("database query index definition mismatch: " + name)


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


_READ_CONNECTION_SCOPES = local()


class _ReadConnectionScope:
    def __init__(self, store):
        self.store, self.path, self.thread_id = store, store.path, get_ident()
        self.connection, self.in_use = None, False
        self.active_depth, self.closed = 0, False
        physical = self.path.lstat()
        if not stat.S_ISREG(physical.st_mode):
            raise StorageError('scoped read connection requires a regular database')
        self.identity = physical.st_dev, physical.st_ino

    def validate(self):
        if self.closed or get_ident() != self.thread_id or self.store.path != self.path:
            raise StorageError('scoped read connection ownership changed')
        try:
            physical = self.path.lstat()
        except OSError as exc:
            raise StorageError('scoped database identity unavailable') from exc
        if (not stat.S_ISREG(physical.st_mode)
                or (physical.st_dev, physical.st_ino) != self.identity):
            raise StorageError('scoped database physical identity changed')

    def acquire(self):
        self.validate()
        self.in_use = True
        if self.connection is not None:
            if self.connection.in_transaction:
                raise StorageError('scoped read connection has an unfinished transaction')
            self.connection.row_factory = sqlite3.Row
            self.connection.text_factory = str
            # A reusable cancellation reader must never resolve a shadow jobs
            # table or keep an attached database from another operation.
            if (self.connection.execute('SELECT 1 FROM sqlite_temp_master LIMIT 1').fetchone()
                    or any(row[1] not in {'main', 'temp'} for row in
                           self.connection.execute('PRAGMA database_list'))):
                self.close()
        if self.connection is None:
            self.connection = self.store._connect()
        self.connection.row_factory = sqlite3.Row
        self.connection.text_factory = str
        self.connection.isolation_level = None
        # Read every required setting on every borrow. Unconditional setting
        # can invalidate otherwise reusable prepared statements even when the
        # connection already has the required value. Never cache acceptance.
        for name, expected in (('foreign_keys', 1),
                ('busy_timeout', int(self.store.timeout * 1000)),
                ('synchronous', 2), ('recursive_triggers', 1), ('query_only', 1)):
            current = self.connection.execute('PRAGMA ' + name).fetchone()
            if current is None:
                raise StorageError('scoped read connection setting unavailable')
            if current[0] != expected:
                self.connection.execute(f'PRAGMA {name}={expected}')
        self.validate()
        return self.connection

    @contextmanager
    def activate(self):
        """Opt in only around a known read, not all work in the outer scope."""
        self.validate()
        self.active_depth += 1
        try:
            yield
        finally:
            self.active_depth -= 1

    def release(self, *, failed):
        self.in_use = False
        if failed:
            self.close()

    def close(self):
        connection, self.connection = self.connection, None
        if connection is not None:
            connection.close()


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
    def read_connection_scope(self):
        """Own this thread's optional reader; activate only known safe reads.

        Transaction entry points and subclass validation remain unchanged.
        Writes and nested reads use separate connections. Cancellation reads
        therefore see committed external changes even during atomic publication.
        """
        scopes = getattr(_READ_CONNECTION_SCOPES, 'values', None)
        if scopes is None:
            scopes = _READ_CONNECTION_SCOPES.values = {}
        if self in scopes:
            scopes[self].validate()
            yield scopes[self]
            return
        scope = scopes[self] = _ReadConnectionScope(self)
        try:
            yield scope
        finally:
            del scopes[self]
            try:
                scope.close()
            finally:
                scope.closed = True

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        connection = None
        scope = getattr(_READ_CONNECTION_SCOPES, 'values', {}).get(self)
        reused = scope is not None and scope.active_depth > 0 and not write and not scope.in_use
        failed = True
        try:
            if scope is not None:
                scope.validate()
            connection = scope.acquire() if reused else self._connect()
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            if scope is not None:
                scope.validate()
            connection.commit()
            failed = False
        except sqlite3.Error as exc:
            if connection:
                connection.rollback()
            raise StorageError("SQLite transaction failed") from exc
        except BaseException:
            if connection:
                connection.rollback()
            raise
        finally:
            if reused:
                scope.release(failed=failed)
            elif connection:
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
                    if version == 2:
                        _verify_query_indexes(connection)
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
                if version == 2:
                    _verify_query_indexes(connection)
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

    def documents_for_run(self, namespace: str, run_id: str, *, instrument_id: str | None = None,
                          limit: int | None = None, descending: bool = False) -> list[dict]:
        """Return checked revisions in one saved run, in stable order.

        The lookup index only narrows candidates; current payload bytes still
        pass the same hash/JSON check. No revision, status or maturity is inferred.
        All revisions are returned unless the caller supplies an explicit limit.
        Subclasses can supply a request-wide read transaction unchanged.
        """
        if not isinstance(namespace, str) or not namespace or not isinstance(run_id, str) or not run_id:
            raise ValueError("namespace and run_id must be non-empty strings")
        if instrument_id is not None and (not isinstance(instrument_id, str) or not instrument_id):
            raise ValueError("instrument_id must be a non-empty string when supplied")
        if limit is not None:
            _validate_page(limit, 0)
        if not isinstance(descending, bool):
            raise ValueError("descending must be a boolean")
        with self.transaction(write=False) as connection:
            # Without this selection SQLite can prefer the namespace/time
            # index to avoid sorting, then scan every historical run. The old
            # read-only V1 format may not yet have the expression index.
            indexed = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='strategy_item_observation_lookup'"
            ).fetchone()
            sql = "SELECT * FROM documents"
            if indexed:
                sql += " INDEXED BY strategy_item_observation_lookup"
            sql += " WHERE namespace=? AND json_extract(payload,'$.run_id')=?"
            parameters = [namespace, run_id]
            if instrument_id is not None:
                sql += " AND json_extract(payload,'$.instrument_id')=?"
                parameters.append(instrument_id)
            direction = 'DESC' if descending else 'ASC'
            sql += f" ORDER BY created_at {direction},document_id {direction}"
            if limit is not None:
                sql += " LIMIT ?"
                parameters.append(limit)
            return [checked_payload(row) for row in connection.execute(sql, parameters)]

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

    def missing_document_ids(self, namespace: str, projection_namespace: str, *, limit=1000):
        """Find missing projections; retain fail-closed existing-header integrity.

        No timestamp cursor: late inserts, equal timestamps and deleted headers
        remain discoverable. Completion is the durable projection itself. On
        completion, stream existing header bytes once without JSON expansion;
        this intentionally remains O(total header bytes), not constant I/O.
        """
        _validate_page(limit, 0)
        with self.transaction(write=False) as connection:
            missing = [row['document_id'] for row in connection.execute(
                'SELECT source.document_id FROM documents AS source '
                'WHERE source.namespace=? AND NOT EXISTS ('
                'SELECT 1 FROM documents AS projection WHERE projection.namespace=? '
                'AND projection.document_id=source.document_id) '
                'ORDER BY source.created_at, source.document_id LIMIT ?',
                (namespace, projection_namespace, limit))]
            if not missing:
                # Preserve the previous startup rejection of tampered headers.
                # The DB already enforces json_valid; checked_payload's remaining
                # checks are the byte checksum and the top-level object type.
                rows = connection.execute(
                    'SELECT projection.payload, projection.content_hash, '
                    'json_type(projection.payload) AS payload_type FROM documents AS projection '
                    'WHERE projection.namespace=? AND EXISTS ('
                    'SELECT 1 FROM documents AS source WHERE source.namespace=? '
                    'AND source.document_id=projection.document_id)',
                    (projection_namespace, namespace))
                for row in rows:
                    if _hash_text(row['payload']) != row['content_hash']:
                        raise IntegrityError('persisted document hash mismatch')
                    if row['payload_type'] != 'object':
                        raise IntegrityError('persisted document is not an object')
            return missing

    @staticmethod
    def _blob_hash(connection, table, rowid):
        checksum = hashlib.sha256()
        if hasattr(connection, 'blobopen'):
            with connection.blobopen(table, 'payload', rowid, readonly=True) as blob:
                while chunk := blob.read(1024 * 1024):
                    checksum.update(chunk)
        else:
            row = connection.execute(f'SELECT payload FROM {table} WHERE rowid=?', (rowid,)).fetchone()
            if row is None:
                raise IntegrityError('persisted document missing during hash verification')
            checksum.update(row['payload'].encode('utf-8'))
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
            if hasattr(connection, 'blobopen'):
                with connection.blobopen('documents', 'payload', row['rowid'], readonly=True) as blob:
                    raw = blob.read()
            else:
                payload = connection.execute('SELECT payload FROM documents WHERE rowid=?', (row['rowid'],)).fetchone()
                if payload is None:
                    raise IntegrityError('persisted document missing during read verification')
                raw = payload['payload'].encode('utf-8')
            if hashlib.sha256(raw).hexdigest() != row['content_hash']:
                raise IntegrityError('persisted document hash mismatch')
            return raw

    def health(self) -> dict:
        # Multiple browser/startup polls must not expand the same large DB in
        # parallel. Every invocation still checks current persisted bytes.
        with self._health_lock:
            return self._health_checked()

    def readiness(self) -> dict:
        """Cheap storage readiness check for startup and request routing.

        Deep integrity remains in ``health()``. This check proves that the
        service can open the configured database and see the expected schema
        contract without traversing large immutable payloads.
        """
        checked_at = utc_now().isoformat()
        scope = {'connection': 'CHECKED', 'journal_mode': 'CHECKED',
                 'schema_version': 'CHECKED', 'migration_checksums': 'CHECKED',
                 'deep_integrity': 'NOT_CHECKED_USE_/health/deep',
                 'payload_hashes': 'NOT_CHECKED_READINESS_MUST_BE_LIGHTWEIGHT'}
        try:
            with self.transaction(write=False) as connection:
                mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                schema_version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
                migration_valid = {row['version']: row['checksum'] for row in connection.execute(
                    'SELECT version,checksum FROM schema_migrations')} == {v: _hash_text(s) for v, s in _MIGRATIONS}
                required_tables = {'documents', 'recommendation_records', 'evaluation_observations',
                                   'jobs', 'job_events', 'schema_migrations'}
                existing_tables = {row['name'] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                missing_tables = sorted(required_tables - existing_tables)
                valid = mode == 'wal' and schema_version == max(v for v, _ in _MIGRATIONS) and migration_valid and not missing_tables
                return {"component": "storage", "status": "healthy" if valid else "unavailable",
                        "journal_mode": mode, "schema_version": schema_version,
                        "path": str(self.path), 'checked_at': checked_at, 'scope': scope,
                        'migration_checksums_valid': migration_valid,
                        'missing_required_tables': missing_tables,
                        'integrity_guarantee': 'READINESS_ONLY_DEEP_INTEGRITY_NOT_CHECKED'}
        except (StorageError, OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            return {"component": "storage", "status": "unavailable", "reason_codes": ["STORAGE_UNAVAILABLE"],
                    'checked_at': checked_at, 'scope': scope, "path": str(self.path)}

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


class ReadOnlySQLiteStore(SQLiteStore):
    """Existing SQLite evidence, without directory creation or migrations.

    URI mode=ro protects the database even when a caller obtains a connection;
    query_only also prohibits temporary writes. Never use immutable=1 on a live
    WAL database because it can hide committed WAL content.
    """
    read_only = True

    def __init__(self, path: str | Path, *, timeout: float = 15.0):
        self.path = Path(path).resolve(strict=True)
        self.timeout = timeout
        self._health_lock = RLock()
        if timeout <= 0 or not self.path.is_file():
            raise ValueError("existing database and positive timeout required")

    def _connect(self):
        connection = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True,
                                     timeout=self.timeout, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        return connection

    @contextmanager
    def transaction(self, *, write: bool = True):
        if write:
            raise StorageError('READ_ONLY_EVIDENCE_STORE_WRITE_REJECTED')
        with super().transaction(write=False) as connection:
            yield connection

    def _migrate(self):
        raise StorageError('READ_ONLY_EVIDENCE_STORE_MIGRATION_REJECTED')


def _validate_page(limit: int, offset: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError("limit must be between 1 and 10000")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be non-negative")
