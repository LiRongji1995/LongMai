"""Durable, bounded provider work. Raw evidence is immutable; queue state is not.

The OS lock is released by a crash. Recovery requeues interrupted calls without
pretending they completed. A successful cached response is checked by content
hash before reuse; partial pages remain explicit partial work.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
import time
import inspect
from typing import Callable

from ..jobs import _ServiceLock
from .contracts import digest


QUEUE_VERSION = 'MAINBOARD_REQUEST_QUEUE_R03_V1'


@dataclass(frozen=True)
class RequestBudget:
    max_requests: int = 128
    max_seconds: float = 600
    timeout_seconds: float = 40
    max_attempts: int = 2
    minimum_interval_seconds: float = .25

    def __post_init__(self):
        from math import isfinite
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not isfinite(x)
               for x in vars(self).values()):
            raise ValueError('FINITE_NUMERIC_BUDGET_REQUIRED')
        if not isinstance(self.max_requests, int) or not 0 <= self.max_requests <= 10000:
            raise ValueError('REQUEST_BUDGET_OUT_OF_RANGE')
        if not isinstance(self.max_attempts, int) or not 1 <= self.max_attempts <= 3:
            raise ValueError('RETRY_BUDGET_OUT_OF_RANGE')
        if not 0 < self.max_seconds <= 7200 or not 0 < self.timeout_seconds <= 60:
            raise ValueError('TIME_BUDGET_OUT_OF_RANGE')
        if not 0 <= self.minimum_interval_seconds <= 10:
            raise ValueError('RATE_INTERVAL_OUT_OF_RANGE')


class RequestQueue:
    def __init__(self, root, *, budget=None, monotonic=time.monotonic, sleeper=time.sleep):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.budget = budget or RequestBudget()
        self.monotonic, self.sleeper = monotonic, sleeper
        self.lock = _ServiceLock(self.root / 'request-queue.lock')
        self.connection = sqlite3.connect(self.root / 'request-queue.sqlite3')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, result_path TEXT, result_hash TEXT,
                failure_category TEXT, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (
                request_id TEXT NOT NULL, attempt INTEGER NOT NULL, state TEXT NOT NULL,
                result_path TEXT, result_hash TEXT, recorded_at TEXT NOT NULL,
                PRIMARY KEY(request_id,attempt));''')
        self.connection.execute("UPDATE requests SET state='PENDING',failure_category='INTERRUPTED' WHERE state='RUNNING'")
        self.connection.commit()
        self.started = monotonic()
        self.calls = self.cache_hits = 0
        self.pages = self.retry_attempts = 0
        from .provider_budget import initialize
        self.budget_path = self.root / 'transport-budget.sqlite3'
        self.budget_id = initialize(self.budget_path, self.budget.max_requests,
                                    minimum_interval=self.budget.minimum_interval_seconds)
        self.last_call = {}
        self.requested_ids = set()

    def close(self):
        self.connection.close()
        self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def timestamp():
        return datetime.now(timezone.utc).isoformat()

    def enqueue(self, endpoint, parameters, *, stage, definition_version, dependencies=(), cache_epoch=None):
        # Stage is provenance, not identity: one history response can satisfy
        # breadth, structure, scoring and outcome dependencies without re-fetch.
        identity = {'endpoint': endpoint, 'parameters': parameters,
                    'definition_version': definition_version, 'queue_version': QUEUE_VERSION, 'cache_epoch': cache_epoch}
        request_id = 'request_' + digest(identity)
        payload = dict(identity, stage=stage, dependencies=sorted(set(dependencies)))
        existing = self.connection.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone()
        if existing is None:
            self.connection.execute('INSERT INTO requests(request_id,payload,state,updated_at) VALUES(?,?,?,?)',
                                    (request_id, json.dumps(payload, sort_keys=True), 'PENDING', self.timestamp()))
            self.connection.commit()
        elif endpoint.startswith('tushare_') and existing['failure_category']=='AUTH':
            import os
            if os.environ.get('TUSHARE_TOKEN') or os.environ.get('TUSHARE_PRO_TOKEN'):
                # A previously missing credential is a changed prerequisite.
                # Preserve every attempt/index; permit the first authorized
                # attempt without rewriting or duplicating prior receipts.
                self.connection.execute("UPDATE requests SET state='PENDING',failure_category='AUTH_RECOVERED',updated_at=? WHERE request_id=?",
                                        (self.timestamp(),request_id))
                self.connection.commit()
        elif existing['state'] == 'COMPLETE' and request_id not in self.requested_ids:
            self.read_result(existing)  # Verify bytes before counting a cache hit.
            self.cache_hits += 1
        self.requested_ids.add(request_id)
        return request_id

    def read_result(self, row):
        if not row['result_path']:
            return None
        path = (self.root / row['result_path']).resolve()
        if not path.is_relative_to(self.root) or path.is_symlink():
            raise ValueError('CACHE_PATH_INVALID')
        value = json.loads(path.read_text(encoding='utf-8'))
        if digest(value) != row['result_hash']:
            raise ValueError('CACHE_CONTENT_CORRUPT')
        return value

    def rows(self, ids=None):
        selected = set(ids) if ids is not None else self.requested_ids
        return [dict(r) for r in self.connection.execute('SELECT * FROM requests ORDER BY request_id')
                if r['request_id'] in selected]

    def results(self, ids=None):
        values=[]
        for row in self.rows(ids):
            if not row['result_path']:continue
            endpoint=json.loads(row['payload'])['endpoint']
            # Repeated historical requests can supply complementary verified
            # fragments. Current directory/theme snapshots remain replacement
            # snapshots: unioning their attempts would resurrect old members.
            values.extend(self._historical_attempt_results(row) if endpoint in ('history','history_adjusted','adjustment_factors')
                          else [self.read_result(row)])
        return values

    def _historical_attempt_results(self,row):
        """Verify every saved attempt; a failed retry does not erase old rows.

        This restores transport evidence only. The consuming normalizer still
        resolves row revisions, source clocks and comparable-price bases.
        """
        return [value for value,_ in self._historical_attempt_versions(row)]

    def _historical_attempt_versions(self,row):
        versions=list(self.connection.execute('SELECT result_path,result_hash,recorded_at FROM attempts WHERE request_id=? ORDER BY attempt',
                                               (row['request_id'],)))
        versions.append(dict(row,recorded_at=row['updated_at']))
        seen=set();values=[]
        for version in versions:
            if not version['result_path'] or version['result_hash'] in seen:continue
            values.append((self.read_result(version),version['recorded_at']));seen.add(version['result_hash'])
        return values

    def cached_history(self, endpoint, instrument_id, start, end, definition_version,*,expected_sessions=None):
        """Return completed fragments in the same source definition only.

        Only actual rows for this security are reusable. Requested ranges are
        transport scope, not proof of per-security/session data. A supplied
        authoritative calendar makes missing sessions resumable partial work.
        """
        matches = [];latest={};matched_requests={}
        wanted={str(d) for d in expected_sessions} if expected_sessions is not None else None
        def identity(value):
            import re
            value=str(value)
            found=re.fullmatch(r'(sh|sz|bj)\.(\d{6})',value,re.IGNORECASE)
            return found.group(2)+'.'+found.group(1).upper() if found else value
        for r in list(self.connection.execute("SELECT * FROM requests WHERE result_path IS NOT NULL")):
            p = json.loads(r['payload'])
            args = p['parameters']
            if p['endpoint'] != endpoint or p['definition_version'] != definition_version:
                continue
            if instrument_id not in args.get('codes', ()):
                continue
            if args.get('start_date', '') <= end and args.get('end_date', '') >= start:
                matched_requests[r['request_id']]=(r,args)
                for value,recorded_at in self._historical_attempt_versions(r):
                    authority=next((p for p in value.get('receipts',()) if p.get('receipt_id')==value.get('request_receipt_id')),None)
                    captured_at=(authority or {}).get('ingested_at',recorded_at)
                    order=(datetime.fromisoformat(captured_at).timestamp(),datetime.fromisoformat(recorded_at).timestamp(),r['request_id'])
                    for record in value.get('rows',()):
                        if identity(record.get('instrument_id',record.get('code','')))!=instrument_id:continue
                        try:session=date.fromisoformat(str(record.get('session',record.get('date'))))
                        except (TypeError,ValueError):continue
                        if not start<=str(session)<=end:continue
                        if session not in latest or order>=latest[session][0]:
                            latest[session]=(order,record,r['request_id'])
        observed=set();by_request={}
        for session,(_,record,request_id) in latest.items():
            # Retain even the selected invalid revision for the consuming
            # normalizer: omitting it would resurrect the old valid row there.
            if request_id not in self.requested_ids:
                self.cache_hits+=1;self.requested_ids.add(request_id)
            suspended=str(record.get('tradestatus',record.get('trade_status','')))=='0' or record.get('suspended') is True
            if not suspended:
                from math import isfinite
                try:valid_close=not isinstance(record.get('close'),bool) and isfinite(float(record.get('close'))) and float(record['close'])>0
                except (TypeError,ValueError):valid_close=False
                if not valid_close:continue
            observed.add(session);by_request.setdefault(request_id,[]).append(session)
        for request_id,(r,args) in matched_requests.items():
            expected={d for d in wanted or () if max(start,args['start_date'])<=d<=min(end,args['end_date'])}
            if wanted is not None and expected-{str(d) for d in observed} and r['state']=='COMPLETE':
                self.connection.execute("UPDATE requests SET state='PARTIAL',failure_category='ACTUAL_SECURITY_SESSION_GAP',updated_at=? WHERE request_id=?",
                                        (self.timestamp(),request_id))
                self.connection.commit()
            # Merge consecutive calendar dates only. A missing trading day
            # cannot hide inside a min/max span; weekends may yield separate
            # fragments without inventing extra required exchange sessions.
            if by_request.get(request_id):
                ordered=sorted(by_request[request_id]);left=right=ordered[0]
                for current in ordered[1:]:
                    if current==right+timedelta(days=1):right=current
                    else:
                        matches.append((str(left),str(right),request_id))
                        left=right=current
                matches.append((str(left),str(right),request_id))
        return sorted(matches)

    def drain(self, executor: Callable, *, ids=None, checkpoint=lambda: None, progress=lambda _: None):
        selected = set(ids) if ids is not None else self.requested_ids
        # At most one attempt per request per drain. A later explicit run may
        # retry up to max_attempts; no nested endless provider retry loop.
        for row in self.rows(selected):
            if row['state'] in ('COMPLETE', 'BLOCKED'):
                continue
            if row['attempts'] >= self.budget.max_attempts and row['failure_category'] not in ('BUDGET','INTERRUPTED','AUTH_RECOVERED'):
                self.connection.execute("UPDATE requests SET state='BLOCKED' WHERE request_id=?", (row['request_id'],))
                self.connection.commit()
                continue
            dependencies=[self.connection.execute('SELECT state FROM requests WHERE request_id=?',(dep,)).fetchone()
                          for dep in json.loads(row['payload'])['dependencies']]
            if any(dep is None or dep[0]!='COMPLETE' for dep in dependencies):
                if any(dep is None for dep in dependencies):
                    self.connection.execute("UPDATE requests SET failure_category='DEPENDENCY_NOT_REGISTERED' WHERE request_id=?",(row['request_id'],))
                    self.connection.commit()
                continue
            remaining = self.budget.max_seconds - (self.monotonic() - self.started)
            if self.calls >= self.budget.max_requests or self.transport_counts().get('HTTP',0) >= self.budget.max_requests or remaining <= 0:
                break
            checkpoint()
            payload = json.loads(row['payload'])
            from .provider_requests import ENDPOINTS
            provider = ENDPOINTS.get(payload['endpoint'], {}).get('provider', payload['endpoint'].split('.')[0])
            delay = self.budget.minimum_interval_seconds - (self.monotonic() - self.last_call.get(provider, -1e20))
            if delay > 0:
                self.sleeper(min(delay, remaining))
            # The rate wait consumes budget and can carry a cancellation. Do
            # not start an upstream request using the pre-wait remaining time.
            checkpoint()
            remaining=self.budget.max_seconds-(self.monotonic()-self.started)
            if self.calls>=self.budget.max_requests or self.transport_counts().get('HTTP',0)>=self.budget.max_requests or remaining<=0:
                break
            self.connection.execute("UPDATE requests SET state='RUNNING',attempts=attempts+1,updated_at=? WHERE request_id=?",
                                    (self.timestamp(), row['request_id']))
            self.connection.commit()
            self.calls += 1
            self.retry_attempts += bool(row['attempts'])
            self.last_call[provider] = self.monotonic()
            archive = self.root / 'raw' / row['request_id'] / str(row['attempts'] + 1)
            try:
                options = {'timeout':min(self.budget.timeout_seconds, remaining)}
                # Legacy injected offline executors remain usable, but their
                # invocations are never reported as observed HTTP requests.
                if 'budget_path' in inspect.signature(executor).parameters:
                    options.update(budget_path=str(self.budget_path),budget_id=self.budget_id,
                        queue_request_id=row['request_id'],max_http_requests=self.budget.max_requests,
                        checkpoint_dir=str(self.root/'checkpoints'/row['request_id']))
                result = executor(payload['endpoint'], payload['parameters'], archive, **options)
            except TimeoutError:
                result = {'status': 'FETCH_FAILED', 'failure_category': 'NETWORK', 'error_code': 'TIMEOUT', 'rows': [], 'receipts': []}
            except Exception as exc:
                # Exception text may contain URLs/tokens; retain type only.
                result = {'status': 'FETCH_FAILED', 'failure_category': 'ENGINEERING', 'error_code': type(exc).__name__, 'rows': [], 'receipts': []}
            result = dict(result, queue_request_id=row['request_id'], request_definition_version=payload['definition_version'])
            self.pages += result.get('coverage',{}).get('pages_this_run',0)
            complete = result.get('status') == 'COMPLETE'
            if result.get('coverage', {}).get('pagination_complete') is False or result.get('pagination_complete') is False:
                complete = False
            authority = [r for r in result.get('receipts', ()) if r.get('receipt_id') == result.get('request_receipt_id')]
            if not authority or any(r.get('status') != 'COMPLETE' or not r.get('pagination_complete', False) for r in authority):
                complete = False
            state = 'COMPLETE' if complete else 'PARTIAL' if result.get('rows') else 'PENDING'
            content_hash = digest(result)
            path = self.root / 'results' / (content_hash + '.json')
            path.parent.mkdir(exist_ok=True)
            if not path.exists():
                path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), encoding='utf-8')
            relative = path.relative_to(self.root).as_posix()
            with self.connection:
                self.connection.execute('UPDATE requests SET state=?,result_path=?,result_hash=?,failure_category=?,updated_at=? WHERE request_id=?',
                    (state, relative, content_hash, result.get('failure_category'), self.timestamp(), row['request_id']))
                self.connection.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?)',
                    (row['request_id'], row['attempts']+1, state, relative, content_hash, self.timestamp()))
            checkpoint()
            progress(self.summary())
        return self.summary()

    def transport_counts(self):
        from .provider_budget import counts
        return counts(self.budget_path,self.budget_id)

    def summary(self):
        rows = self.rows()
        counts = {s: sum(r['state'] == s for r in rows) for s in ('PENDING', 'RUNNING', 'COMPLETE', 'PARTIAL', 'BLOCKED')}
        return {'queue_version': QUEUE_VERSION, 'requested': len(rows), 'states': counts,
                'task_attempts_this_run': self.calls,
                'http_attempts_this_run': self.transport_counts().get('HTTP',0),
                'network_requests_this_run': self.transport_counts().get('HTTP',0),
                'sdk_operations_this_run': self.transport_counts().get('SDK',0),
                'pages_this_run':self.pages,'task_retries_this_run':self.retry_attempts,
                'cache_hits_this_run': self.cache_hits,
                'count_semantics':'HTTP_ADMISSION_BEFORE_IO; SDK_OPERATIONS_SEPARATE; TASK_SAFETY_CAP_EQUALS_MAX_REQUESTS',
                'elapsed_seconds': self.monotonic() - self.started, 'budget': vars(self.budget),
                'budget_exhausted': self.calls >= self.budget.max_requests or self.transport_counts().get('HTTP',0)>=self.budget.max_requests or self.monotonic()-self.started >= self.budget.max_seconds,
                'status': 'COMPLETE' if rows and counts['COMPLETE'] == len(rows) else 'PARTIAL',
                'pending_request_ids': [r['request_id'] for r in rows if r['state'] != 'COMPLETE']}
