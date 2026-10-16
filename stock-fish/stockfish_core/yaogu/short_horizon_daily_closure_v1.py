"""Explicit, bounded daily review of trusted archived R2 runs.

No timer, collector, latest-input selection or strategy recomputation lives here.
One existing JobService owns the batch; its final transaction commits every
product and the batch ledger together, or commits none of them.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import hashlib
from pathlib import Path

from ..common import canonical_json, utc_now
from ..jobs import JobConflict, JobService
from ..observed_data import exchange_calendar_2026
from ..storage import ImmutableConflict, checked_payload
from .contracts import digest
from .short_horizon_outcomes_v1 import HORIZONS, NAMESPACES, VERSION as OUTCOME_VERSION, SH, evaluate
from .visibility import stamp


VERSION = 'SHORT_HORIZON_DAILY_CLOSURE_V1'
JOB_KIND = 'short_horizon_daily_closure_v1'
LEDGERS = 'short_horizon_daily_closure_ledgers_v1'
MAX_ATTEMPTS = 3
MAX_DISCOVERY_RECORDS = 10000


def _policy_identity():
    directory = Path(__file__).parent
    paths = [directory / name for name in (
        'short_horizon_daily_closure_v1.py', 'short_horizon_outcomes_v1.py',
        'short_horizon_archive_bridge_v1.py', 'outcomes.py', 'visibility.py',
        'history_artifacts.py', 'contracts.py')]
    paths += [directory.parent / name for name in ('observed_data.py', 'data.py', 'exchange_calendar_2026.json')]
    return {'version': VERSION, 'outcome_policy': OUTCOME_VERSION,
            'source_hashes': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}}


def _bridge_module():
    from . import short_horizon_archive_bridge_v1
    return short_horizon_archive_bridge_v1


def _credential_identity(value):
    # Validation timestamps or diagnostic counters must not create new daily
    # jobs. These immutable content identities are returned by the verifier.
    return {key: value[key] for key in ('bridge_id', 'run_id', 'signal_content_hash',
        'input_id', 'archive_final_sha256', 'input_hash') if key in value}


@dataclass(frozen=True)
class _InputCalendar:
    input_id: str
    input_hash: str
    observation_date: date
    sessions: tuple[date, ...]


def _input_calendar(view, input_id):
    """Small fields from fresh bridge authentication, never a saved index."""
    value, credential = view['input_metadata'], view['credential']
    if (value.get('input_id') != input_id or credential.get('input_id') != input_id
            or value.get('input_hash') != credential.get('input_hash')):
        raise ValueError('SHORT_HORIZON_CLOSURE_INPUT_IDENTITY_MISMATCH')
    sessions = tuple(date.fromisoformat(day) for day in value['sessions'])
    observed = date.fromisoformat(value['observation_date'])
    if tuple(sorted(set(sessions))) != sessions or observed not in sessions:
        raise ValueError('SHORT_HORIZON_CLOSURE_AUTHENTICATED_CALENDAR_INVALID')
    return _InputCalendar(input_id, value['input_hash'], observed, sessions)


class ShortHorizonDailyClosure:
    """Local-admin orchestration; construction, plan and status are read-only."""

    def __init__(self, service, *, clock=utc_now, max_runs=5):
        if type(max_runs) is not int or not 1 <= max_runs <= 100:
            raise ValueError('SHORT_HORIZON_CLOSURE_MAX_RUNS_REQUIRES_1_TO_100')
        self.service, self.clock, self.max_runs = service, clock, max_runs

    def _run_ids(self, run_ids):
        if not isinstance(run_ids, (list, tuple)) or not run_ids:
            raise ValueError('SHORT_HORIZON_CLOSURE_EXPLICIT_RUN_IDS_REQUIRED')
        if any(not isinstance(value, str) or not value for value in run_ids):
            raise ValueError('SHORT_HORIZON_CLOSURE_INVALID_RUN_ID')
        values = sorted(set(run_ids))
        if len(values) > self.max_runs:
            raise ValueError('SHORT_HORIZON_CLOSURE_RUN_LIMIT_EXCEEDED_NO_TRUNCATION')
        return values

    def _authenticated(self, run_ids, input_id, session, checkpoint=lambda: None, *, borrow_run=False,
                       retain_input=False):
        if not isinstance(input_id, str) or not input_id:
            raise ValueError('SHORT_HORIZON_REGISTERED_OUTCOME_INPUT_ID_REQUIRED')
        bridge = _bridge_module()
        def authenticate_input():
            view = bridge.authenticated_bridge_view(self.service, input_id=input_id, session=session,
                **({'retain_input': True} if retain_input else {}))
            certificate = view['credential']
            checkpoint()
            if certificate.get('input_id') != input_id or not certificate.get('bridge_id'):
                raise ValueError('SHORT_HORIZON_CLOSURE_INPUT_CERTIFICATE_MISMATCH')
            calendar = _input_calendar(view, input_id)
            session.release_cached_archives()
            return certificate, calendar

        # Retain only the last archive's freshly reconstructed typed input.
        # Earlier run authentication must not overlap that large object. Plans
        # keep their metadata-only path and never request typed retention.
        if not retain_input:
            input_certificate, bundle = authenticate_input()
        runs, credentials, originals = [], [], []
        for run_id in run_ids:
            checkpoint()
            view = bridge.authenticated_bridge_view(self.service, run_id=run_id, session=session,
                                                    borrow_run=borrow_run)
            certificate = view['credential']
            run = view['run'] if borrow_run else view['run_metadata']
            if (run.get('execution_profile') != 'SHORT_HORIZON_RESEARCH_SINGLE'
                    or run.get('strategy_id') != 'SHORT_HORIZON_STRENGTH_R2'
                    or certificate.get('run_id') != run_id
                    or certificate.get('signal_content_hash') != run.get('content_hash')
                    or certificate.get('input_id') != run.get('input_id')):
                raise ValueError('SHORT_HORIZON_CLOSURE_TRUSTED_FROZEN_R2_REQUIRED')
            originals.append(_input_calendar(view, run['input_id']))
            runs.append(run)
            credentials.append(_credential_identity(certificate))
            session.release_cached_archives()
        if retain_input:
            input_certificate, bundle = authenticate_input()
        for run, original in zip(runs, originals):
            signal_day = date.fromisoformat(run['observation_date'])
            if signal_day in bundle.sessions and original.sessions:
                # Daily archives retain rolling windows. Only their shared
                # history must agree; discarded older sessions are not a gap.
                start = max(original.sessions[0], bundle.sessions[0])
                original_prefix = tuple(day for day in original.sessions if start <= day <= signal_day)
                current_prefix = tuple(day for day in bundle.sessions if start <= day <= signal_day)
                if current_prefix != original_prefix:
                    raise ValueError('SHORT_HORIZON_SIGNAL_CALENDAR_PREFIX_CONFLICT')
        return runs, bundle, {'runs': credentials, 'outcome_input': _credential_identity(input_certificate)}

    @staticmethod
    def _schedule(runs, bundle, evaluated_at):
        sessions = exchange_calendar_2026().sessions
        plans = []
        for run in runs:
            signal_day = date.fromisoformat(run['observation_date'])
            future = sessions[sessions.index(signal_day) + 1:] if signal_day in sessions else ()
            rows = []
            for horizon in HORIZONS:
                end = future[horizon - 1] if len(future) >= horizon else None
                close = datetime.combine(end, time(15), SH) if end else None
                state = 'UNKNOWN' if end is None else 'DUE' if close <= evaluated_at else 'PENDING'
                axis_complete = bool(end and signal_day in bundle.sessions and
                    tuple(day for day in bundle.sessions if signal_day < day <= end) == tuple(future[:horizon]))
                rows.append({'horizon': horizon, 'label_end': str(end) if end else None,
                    'matures_at': close.isoformat() if close else None, 'maturity_state': state,
                    'registered_axis_complete': axis_complete,
                    'reason_codes': ['VERIFIED_CALENDAR_HORIZON_UNAVAILABLE'] if end is None else
                        [] if axis_complete else ['REGISTERED_CALENDAR_HORIZON_UNAVAILABLE']})
            plans.append({'run_id': run['run_id'], 'observation_date': str(signal_day),
                'horizons': rows, 'due_horizons': [row['horizon'] for row in rows if row['maturity_state'] == 'DUE'],
                'unknown_horizons': [row['horizon'] for row in rows if row['maturity_state'] == 'UNKNOWN']})
        return plans

    def plan(self, run_ids, input_id, *, evaluated_at=None):
        run_ids = self._run_ids(run_ids)
        now = stamp(self.clock())
        evaluated = stamp(evaluated_at if evaluated_at is not None else now).astimezone(timezone.utc)
        session_day = evaluated.astimezone(SH).date()
        reasons = []
        if evaluated > now:
            reasons.append('FUTURE_EVALUATION_CUTOFF')
        if session_day != now.astimezone(SH).date():
            reasons.append('EVALUATION_NOT_CURRENT_SESSION')
        if session_day not in exchange_calendar_2026().sessions:
            reasons.append('NO_SESSION')
        elif evaluated.astimezone(SH).time().replace(tzinfo=None) < time(15):
            reasons.append('BEFORE_SESSION_CLOSE')
        verification = _bridge_module().ArchiveVerificationSession()
        runs, bundle, bindings = self._authenticated(run_ids, input_id, verification)
        latest_input_session = max(bundle.sessions) if bundle.sessions else None
        if bundle.observation_date != session_day or latest_input_session != session_day:
            reasons.append('INPUT_NOT_CURRENT_SESSION')
        if any(evaluated < max(stamp(run['decision_cutoff']), stamp(run.get('generated_at', run['decision_cutoff'])))
               for run in runs):
            reasons.append('EVALUATION_PRECEDES_FROZEN_SIGNAL')
        plans = self._schedule(runs, bundle, evaluated)
        authentication = verification.terminal()
        policy = _policy_identity()
        identity = {'policy': policy, 'run_ids': run_ids, 'input_id': input_id,
                    'evaluation_session': str(session_day), 'bindings': bindings}
        job_id = 'short_daily_' + digest(identity)
        request = {**identity, 'root_job_id': job_id, 'evaluated_at': evaluated.isoformat(),
                   'horizons': list(HORIZONS)}
        return {'schema_version': VERSION, 'job_id': job_id, 'status': reasons[0] if reasons else 'READY',
                'can_submit': not reasons, 'reason_codes': reasons, 'request': request, 'runs': plans,
                'run_count': len(run_ids), 'max_runs': self.max_runs, 'collection': 'NOT_REQUESTED',
                'outcome_input_observation_date': str(bundle.observation_date),
                'outcome_input_latest_session': str(latest_input_session) if latest_input_session else None,
                'archive_verification': authentication,
                'market_edge': 'NOT_ESTABLISHED'}

    @staticmethod
    def _legal_empty(run):
        result = run['results'][run['strategy_id']]
        scope = (run.get('data_readiness') or {}).get('SHORT_HORIZON_QUALIFICATION') or {}
        counts = result.get('counts') or {}
        return (not result.get('items') and result.get('status') == 'EMPTY'
            and result.get('full_eligible_pool_retained') is True and result.get('eligible_pool_ids') == []
            and all(type(value) is int for value in (counts.get('eligible'), counts.get('evaluated'),
                                                     scope.get('eligible'), scope.get('evaluated')))
            and counts.get('eligible') == 0 and counts.get('evaluated') == 0
            and scope.get('status') == 'COMPLETE' and scope.get('universe_complete') is True
            and scope.get('eligible') == 0 and scope.get('evaluated') == 0)

    def pending(self, input_id, *, limit=100, offset=0):
        """Discover from saved bridge bindings; execution reauthenticates RAW.

        Totals cover the entire binding namespace, independent of max_runs.
        Old archives are not all reopened merely to list pending work. A stored
        completion is descriptive saved evidence, not fresh source verification.
        """
        if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or offset < 0:
            raise ValueError('SHORT_HORIZON_CLOSURE_INVALID_DISCOVERY_PAGE')
        if not isinstance(input_id, str) or not input_id:
            raise ValueError('SHORT_HORIZON_REGISTERED_OUTCOME_INPUT_ID_REQUIRED')
        bridge = _bridge_module()
        verification = bridge.ArchiveVerificationSession()
        view = bridge.authenticated_bridge_view(self.service, input_id=input_id, session=verification)
        credential = view['credential']
        if credential.get('input_id') != input_id:
            raise ValueError('SHORT_HORIZON_CLOSURE_INPUT_CERTIFICATE_MISMATCH')
        bundle = _input_calendar(view, input_id)
        verification.release_cached_archives()
        now = stamp(self.clock())
        day = now.astimezone(SH).date()
        latest_session = max(bundle.sessions) if bundle.sessions else None
        gate = ('NO_SESSION' if day not in exchange_calendar_2026().sessions else
                'BEFORE_SESSION_CLOSE' if now.astimezone(SH).time().replace(tzinfo=None) < time(15) else
                'INPUT_NOT_CURRENT_SESSION' if bundle.observation_date != day or latest_session != day else 'READY')
        inventory, complete, legal_empty, scanned = [], 0, 0, 0
        while True:
            page = self.service.store.list_documents(bridge.RUN_BINDINGS, limit=1000, offset=scanned)
            for document in page:
                scanned += 1
                if scanned > MAX_DISCOVERY_RECORDS:
                    raise ValueError('SHORT_HORIZON_CLOSURE_DISCOVERY_LIMIT_EXCEEDED_NO_PARTIAL_TOTAL')
                binding = document['payload']
                certificate = self.service.store.get_document(bridge.CERTIFICATES, binding['bridge_id'])
                if (binding.get('schema_version') != bridge.VERSION or certificate is None
                        or document['document_id'] != binding.get('run_id')
                        or any(certificate.get(key) != value for key, value in binding.items())):
                    raise ValueError('SHORT_HORIZON_CLOSURE_STORED_BRIDGE_BINDING_INVALID')
                run = self.service.strategy_experiment(binding['run_id'])
                if (run.get('strategy_id') != 'SHORT_HORIZON_STRENGTH_R2'
                        or run.get('execution_profile') != 'SHORT_HORIZON_RESEARCH_SINGLE'
                        or run.get('content_hash') != binding['signal_content_hash']
                        or run.get('input_id') != binding['input_id']):
                    raise ValueError('SHORT_HORIZON_CLOSURE_STORED_RUN_BINDING_INVALID')
                items = run['results'][run['strategy_id']].get('items') or []
                ids = {item['instrument_id'] for item in items}
                if len(ids) != len(items):
                    raise ValueError('SHORT_HORIZON_CLOSURE_DUPLICATE_FROZEN_INSTRUMENT')
                revisions = self.service.store.documents_for_run(NAMESPACES['outcomes'], run['run_id'])
                latest = max(revisions, key=lambda row: (stamp(row['evaluated_at']), row['revision_id'])) if revisions else None
                if latest and (latest.get('schema_version') != OUTCOME_VERSION
                        or latest.get('signal_content_hash') != run['content_hash']):
                    raise ValueError('SHORT_HORIZON_CLOSURE_STORED_OUTCOME_IDENTITY_INVALID')
                observed = (latest.get('items') or []) if latest else []
                actual = {(row['instrument_id'], row['horizon']): row for row in observed}
                expected = {(iid, horizon) for iid in ids for horizon in HORIZONS}
                all_mature = (bool(expected) and len(actual) == len(observed) and set(actual) == expected
                    and latest.get('horizons') == list(HORIZONS)
                    and all(type(row['horizon']) is int for row in observed)
                    and all(row['status'] == 'MATURE' for row in observed))
                empty_complete = self._legal_empty(run)
                if all_mature or empty_complete:
                    complete += 1
                    legal_empty += bool(empty_complete)
                    continue
                planned = self._schedule([run], bundle, now)[0]
                due = {(iid, horizon) for iid in ids for horizon in planned['due_horizons']}
                observed_due = due and due <= set(actual) and all(actual[key]['status'] == 'MATURE' for key in due)
                same_input = latest is not None and latest.get('outcome_input_id') == input_id
                if not ids:
                    state = 'UNKNOWN_EMPTY_POOL_PROOF_REQUIRED'
                elif not due or (observed_due and same_input):
                    state = 'UNKNOWN_CALENDAR' if planned['unknown_horizons'] and not due else 'WAITING_MATURITY'
                elif same_input and due <= set(actual) and any(actual[key]['status'] == 'UNKNOWN' for key in due):
                    state = 'UNKNOWN_AWAITING_NEW_INPUT'
                else:
                    state = 'DUE'
                inventory.append({**planned, 'state': state, 'actionable': state == 'DUE' and gate == 'READY',
                    'bridge_id': binding['bridge_id'], 'frozen_instrument_count': len(ids),
                    'required_observation_count': len(expected), 'latest_revision_id': latest['revision_id'] if latest else None,
                    'latest_outcome_input_id': latest.get('outcome_input_id') if latest else None,
                    'source_reauthentication_required': True})
            if len(page) < 1000:
                break
        authentication = verification.terminal()
        inventory.sort(key=lambda row: (row['observation_date'], row['run_id']))
        return {'schema_version': VERSION, 'status': gate, 'input_id': input_id,
            'outcome_input_observation_date': str(bundle.observation_date),
            'outcome_input_latest_session': str(latest_session) if latest_session else None,
            'evaluation_session': str(day), 'total': len(inventory), 'indexed_run_total': scanned,
            'stored_complete_total': complete, 'legal_empty_pool_total': legal_empty,
            'actionable_total': sum(row['actionable'] for row in inventory),
            'limit': limit, 'offset': offset, 'items': inventory[offset:offset + limit],
            'max_discovery_records': MAX_DISCOVERY_RECORDS,
            'next_offset': offset + limit if offset + limit < len(inventory) else None,
            'discovery_evidence': 'CURRENT_SAVED_BYTES_CHECKED_ORIGINAL_ARCHIVES_NOT_REAUTHENTICATED',
            'source_reauthentication_required': True, 'archive_verification': authentication,
            'collection': 'NOT_REQUESTED', 'market_edge': 'NOT_ESTABLISHED'}

    def submit(self, run_ids, input_id, *, evaluated_at=None):
        plan = self.plan(run_ids, input_id, evaluated_at=evaluated_at)
        if not plan['can_submit']:
            raise ValueError('SHORT_HORIZON_CLOSURE_' + plan['status'])
        self._write_jobs()
        self.service.jobs.register(JOB_KIND, self._worker)
        existing = self.service.jobs.get(plan['job_id'])
        if existing is not None:
            # One day's first accepted cutoff is immutable. A later command
            # with the same input returns it, including FAILED/CANCELLED state.
            if existing['kind'] != JOB_KIND or self._stable_request(existing['payload']) != self._stable_request(plan['request']):
                raise JobConflict('daily closure job identity collision')
            return self._latest_attempt(existing)
        try:
            return self.service.jobs.submit(JOB_KIND, plan['request'], job_id=plan['job_id'])
        except JobConflict:
            # Another caller can freeze the first cutoff between get/create.
            existing = self.service.jobs.get(plan['job_id'])
            if (existing is None or existing['kind'] != JOB_KIND
                    or self._stable_request(existing['payload']) != self._stable_request(plan['request'])):
                raise
            return self._latest_attempt(existing)

    @staticmethod
    def _stable_request(request):
        return {key: value for key, value in request.items() if key != 'evaluated_at'}

    def _latest_attempt(self, original):
        latest = original
        for attempt in range(original['attempt'] + 1, MAX_ATTEMPTS + 1):
            candidate = self.service.jobs.get(original['payload']['root_job_id'] + '_attempt_' + str(attempt))
            if candidate is None:
                break
            if (candidate['kind'] != JOB_KIND or candidate['payload'] != original['payload']
                    or candidate['retry_of'] != latest['job_id']):
                raise JobConflict('daily closure retry identity collision')
            latest = candidate
        return latest

    def _job(self, job_id):
        job = self.service.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job['kind'] != JOB_KIND:
            raise ValueError('SHORT_HORIZON_CLOSURE_JOB_REQUIRED')
        return job

    def status(self, job_id):
        job = self._job(job_id)
        return {'schema_version': VERSION, 'job': job,
                'ledger': self.service.store.get_document(LEDGERS, job_id),
                'events': self.service.jobs.events(job_id)}

    def cancel(self, job_id):
        self._job(job_id)
        self._write_jobs()
        return self.service.jobs.cancel(job_id)

    def _write_jobs(self):
        jobs = self.service.jobs
        if (not isinstance(jobs, JobService) or jobs.store.path != self.service.store.path
                or jobs._closed or jobs._service_lock.file.closed):
            raise JobConflict('closure writes require the active matching job-service lock')
        return jobs

    def retry(self, job_id):
        previous = self._job(job_id)
        self._run_ids(previous['payload']['run_ids'])
        if previous['attempt'] >= MAX_ATTEMPTS:
            raise JobConflict('daily closure retry limit reached')
        self._write_jobs()
        if previous['state'] in {'QUEUED', 'RUNNING', 'CANCEL_REQUESTED'}:
            previous = self._recover_target(job_id)
        if previous['state'] not in {'FAILED', 'INTERRUPTED', 'CANCELLED'}:
            raise JobConflict('only failed, interrupted or cancelled closure jobs may be retried')
        self.service.jobs.register(JOB_KIND, self._worker)
        next_id = previous['payload']['root_job_id'] + '_attempt_' + str(previous['attempt'] + 1)
        job = self.service.jobs.create(JOB_KIND, previous['payload'], job_id=next_id, retry_of=job_id)
        if job['state'] == 'QUEUED':
            self.service.jobs.start(next_id)
        return self.service.jobs.get(next_id)

    def _recover_target(self, job_id):
        """Explicit orphan recovery under the existing owner's OS lock only."""
        jobs = self._write_jobs()
        with jobs._lock:
            self._write_jobs()  # Serialize against shutdown before any update.
            if job_id in jobs._futures:
                raise JobConflict('active current-process closure cannot be recovered')
            with self.service.store.transaction() as connection:
                row = connection.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
                previous = jobs._decode(row)
                if previous['kind'] != JOB_KIND:
                    raise JobConflict('target recovery requires a closure job')
                if previous['state'] not in {'QUEUED', 'RUNNING', 'CANCEL_REQUESTED'}:
                    return previous
                if previous['owner_id'] == jobs.owner_id:
                    raise JobConflict('current-process closure cannot be recovered')
                # Recheck the configured budget under the same write lock;
                # refusal must not change even the target's audit state.
                self._run_ids(previous['payload']['run_ids'])
                if previous['attempt'] >= MAX_ATTEMPTS:
                    raise JobConflict('daily closure retry limit reached')
                state = 'CANCELLED' if previous['cancel_requested'] else 'INTERRUPTED'
                error = {'code': 'EXPLICIT_CLOSURE_TARGET_RECOVERY',
                         'message': 'only the explicitly selected orphaned closure was recovered'}
                now = utc_now().isoformat()
                connection.execute('UPDATE jobs SET state=?,error=?,finished_at=?,updated_at=?,revision=revision+1 WHERE job_id=?',
                                   (state, canonical_json(error), now, now, job_id))
                jobs._event(connection, job_id, state, previous['progress'],
                    {'target_only_recovery': True, 'previous_state': previous['state'],
                     'previous_owner_id': previous['owner_id']})
        return jobs.get(job_id)

    def _worker(self, ctx):
        request = ctx.payload
        ctx.check_cancelled()
        if request['policy'] != _policy_identity():
            raise ValueError('SHORT_HORIZON_CLOSURE_POLICY_CHANGED_AFTER_SUBMISSION')
        if self._run_ids(request['run_ids']) != request['run_ids']:
            raise ValueError('SHORT_HORIZON_CLOSURE_CANONICAL_RUN_SET_REQUIRED')
        identity = {key: request[key] for key in ('policy', 'run_ids', 'input_id', 'evaluation_session', 'bindings')}
        evaluated = stamp(request['evaluated_at'])
        session_day = evaluated.astimezone(SH).date()
        if (request['root_job_id'] != 'short_daily_' + digest(identity)
                or request['horizons'] != list(HORIZONS)
                or request['evaluation_session'] != str(session_day)
                or session_day not in exchange_calendar_2026().sessions
                or evaluated.astimezone(SH).time().replace(tzinfo=None) < time(15)
                or evaluated > stamp(self.clock())):
            raise ValueError('SHORT_HORIZON_CLOSURE_FROZEN_REQUEST_INVALID')
        verification = _bridge_module().ArchiveVerificationSession(checkpoint=ctx.check_cancelled)
        runs, calendar, bindings = self._authenticated(request['run_ids'], request['input_id'], verification,
                                                     checkpoint=ctx.check_cancelled, retain_input=True)
        if bindings != request['bindings']:
            raise ValueError('SHORT_HORIZON_CLOSURE_BRIDGE_IDENTITY_CHANGED')
        if calendar.observation_date != session_day or not calendar.sessions or max(calendar.sessions) != session_day:
            raise ValueError('SHORT_HORIZON_CLOSURE_INPUT_NOT_CURRENT_SESSION')
        if any(evaluated < max(stamp(run['decision_cutoff']), stamp(run.get('generated_at', run['decision_cutoff'])))
               for run in runs):
            raise ValueError('SHORT_HORIZON_EVALUATION_PRECEDES_SIGNAL')
        plans = self._schedule(runs, calendar, evaluated)
        ctx.check_cancelled()
        # Authenticate every archive before loading the typed input. Delay the
        # full run borrows until after its parser's temporary buffers are gone;
        # the same session keeps the original source and store-bound proofs.
        bundle = _bridge_module().load_verified_bridge_input(self.service, request['input_id'], session=verification)
        if bundle.observation_date != calendar.observation_date or bundle.sessions != calendar.sessions:
            raise ValueError('SHORT_HORIZON_CLOSURE_INPUT_CHANGED_AFTER_AUTHENTICATION')
        borrowed = []
        for metadata, credential in zip(runs, bindings['runs']):
            ctx.check_cancelled()
            view = _bridge_module().authenticated_bridge_view(self.service, run_id=metadata['run_id'],
                session=verification, borrow_run=True)
            run = view['run']
            if (view['run_metadata'] != metadata or _credential_identity(view['credential']) != credential
                    or {key: run.get(key) for key in metadata} != metadata):
                raise ValueError('SHORT_HORIZON_CLOSURE_BORROWED_RUN_IDENTITY_CHANGED')
            borrowed.append(run)
        runs = borrowed
        ctx.check_cancelled()
        products, records = [], []
        for run, planned in zip(runs, plans):
            ctx.check_cancelled()
            if not run['results'][run['strategy_id']].get('items'):
                legal_empty = self._legal_empty(run)
                records.append({**planned, 'status': 'LEGAL_EMPTY_POOL' if legal_empty else 'UNKNOWN_EMPTY_POOL_PROOF_REQUIRED',
                    'revision_id': None, 'counts': {}, 'product_revision_available': False,
                    'empty_pool_proof': 'VERIFIED_FROZEN_QUALIFICATION' if legal_empty else 'NOT_ESTABLISHED'})
                continue
            horizons = planned['due_horizons']
            if not horizons:
                records.append({**planned, 'status': 'UNKNOWN' if planned['unknown_horizons'] else 'PENDING',
                                'revision_id': None, 'counts': {}, 'product_revision_available': False})
                continue
            result = evaluate(run, bundle, evaluated, horizons=horizons, checkpoint=ctx.check_cancelled)
            products.append(result)
            counts = result['outcomes']['counts']
            records.append({**planned, 'status': 'COMPLETED_WITH_UNKNOWNS' if counts.get('UNKNOWN') else 'OBSERVED',
                'revision_id': result['outcomes']['revision_id'], 'counts': counts, 'product_revision_available': True})
        authentication = verification.terminal()
        ctx.check_cancelled()
        has_unknown = any(record['status'] in {'UNKNOWN', 'UNKNOWN_EMPTY_POOL_PROOF_REQUIRED', 'COMPLETED_WITH_UNKNOWNS'}
                          or record['unknown_horizons'] for record in records)
        has_pending = any(row['maturity_state'] == 'PENDING' for record in records
                          if record['status'] != 'LEGAL_EMPTY_POOL' for row in record['horizons'])
        ledger = {'schema_version': VERSION, 'job_id': ctx.job_id, 'root_job_id': request['root_job_id'],
            'evaluation_session': request['evaluation_session'], 'evaluated_at': request['evaluated_at'],
            'input_id': request['input_id'], 'run_ids': request['run_ids'], 'run_count': len(runs),
            'policy': request['policy'], 'bindings': bindings, 'runs': records,
            'archive_verification': authentication,
            'run_status_counts': dict(Counter(record['status'] for record in records)),
            'status': 'COMPLETED_WITH_UNKNOWNS' if has_unknown else
                'COMPLETED_PENDING_MATURITY' if has_pending else 'COMPLETED',
            'outcome_policy': OUTCOME_VERSION, 'collection': 'NOT_REQUESTED',
            'signal_policy_mutated': False, 'market_edge': 'NOT_ESTABLISHED',
            'execution_status': 'NOT_VERIFIED', 'returns': None}

        def write(connection):
            for result in products:
                for kind, product in result.items():
                    revision_id = product['revision_id']
                    existing = connection.execute('SELECT * FROM documents WHERE namespace=? AND document_id=?',
                        (NAMESPACES[kind], revision_id)).fetchone()
                    if existing:
                        saved = checked_payload(existing)
                        if {key: value for key, value in saved.items() if key != 'evaluated_at'} != {
                                key: value for key, value in product.items() if key != 'evaluated_at'}:
                            raise ImmutableConflict('short-horizon revision identity collision')
                        continue
                    self.service.store._put_document(connection, NAMESPACES[kind], revision_id, product)
            self.service.store._put_document(connection, LEDGERS, ctx.job_id, ledger)
            # The ledger retains the successful prepublication audit snapshot.
            # Recheck every original and borrowed value after all domain writes,
            # while failure can still roll back the ledger and the whole batch.
            verification.terminal()

        return ctx.complete({'schema_version': VERSION, 'ledger_id': ctx.job_id, 'status': ledger['status'],
                             'run_count': len(runs), 'revision_ids': [result['outcomes']['revision_id'] for result in products],
                             'market_edge': 'NOT_ESTABLISHED'}, write)
