"""Process-safe HTTP admission ledger. A request is charged before any I/O.

The append-only entries survive worker/parent termination. SDK operations are
counted separately: BaoStock uses its own socket protocol and is not HTTP.
"""
from datetime import datetime, timezone
import sqlite3
import time
import uuid


class HttpBudgetExhausted(RuntimeError):
    pass


def initialize(path, limit, *, minimum_interval=0):
    key = uuid.uuid4().hex
    with sqlite3.connect(str(path), timeout=20) as db:
        db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS http_budgets
            (id TEXT PRIMARY KEY, maximum INTEGER NOT NULL, minimum_interval REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS transport_attempts
            (id INTEGER PRIMARY KEY, budget_id TEXT NOT NULL, request_id TEXT NOT NULL,
             kind TEXT NOT NULL, provider TEXT NOT NULL, endpoint TEXT NOT NULL,
             started_at TEXT NOT NULL, started_epoch REAL NOT NULL);''')
        db.execute('INSERT INTO http_budgets VALUES(?,?,?)', (key, limit, minimum_interval))
    return key


def charge(path, budget_id, request_id, provider, endpoint, *, kind='HTTP'):
    """Reserve before sending, serializing providers across concurrent workers."""
    if not path:
        return
    while True:
        with sqlite3.connect(str(path), timeout=20, isolation_level=None) as db:
            db.execute('BEGIN IMMEDIATE')
            maximum, interval = db.execute('SELECT maximum,minimum_interval FROM http_budgets WHERE id=?', (budget_id,)).fetchone()
            count = db.execute("SELECT count(*) FROM transport_attempts WHERE budget_id=? AND kind='HTTP'", (budget_id,)).fetchone()[0]
            if kind == 'HTTP' and count >= maximum:
                db.rollback()
                raise HttpBudgetExhausted('EXPLICIT_HTTP_REQUEST_BUDGET_EXHAUSTED')
            last = db.execute('SELECT max(started_epoch) FROM transport_attempts WHERE budget_id=? AND provider=?', (budget_id, provider)).fetchone()[0]
            delay = interval - (time.time() - last) if last is not None else 0
            if delay <= 0:
                now = datetime.now(timezone.utc)
                db.execute('INSERT INTO transport_attempts(budget_id,request_id,kind,provider,endpoint,started_at,started_epoch) VALUES(?,?,?,?,?,?,?)',
                           (budget_id, request_id, kind, provider, endpoint, now.isoformat(), now.timestamp()))
                db.commit()
                return
            db.rollback()
        time.sleep(min(delay, 1))


def counts(path, budget_id):
    with sqlite3.connect(str(path), timeout=20) as db:
        return dict(db.execute('SELECT kind,count(*) FROM transport_attempts WHERE budget_id=? GROUP BY kind', (budget_id,)))
