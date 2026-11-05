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
from .short_horizon_archive_bridge_v1 import CLOSURE_ENCODING_PROFILE, INPUT_BINDINGS
from .short_horizon_outcomes_v1 import HORIZONS, NAMESPACES, VERSION as OUTCOME_VERSION, SH, evaluate
from .visibility import stamp
from .short_horizon_resource_profiles_v1 import resolve_verification_profile, session_options


VERSION = 'SHORT_HORIZON_DAILY_CLOSURE_V1'
JOB_KIND = 'short_horizon_daily_closure_v1'
LEDGERS = 'short_horizon_daily_closure_ledgers_v1'
MAX_ATTEMPTS = 3
MAX_DISCOVERY_RECORDS = 10000
PENDING_RUN_MATERIALIZATION = 'AUTHENTICATED_NONEMPTY_V1'


def _closed_archive_seal(view, cutoff):
    # This field must come from this invocation's authenticated private proof,
    # never from a CLI timestamp, a saved index, or an ordinary JSON receipt.
    raw = view.get('archive_sealed_at')
    if raw is None:
        raise ValueError('SHORT_HORIZON_REPLAY_AUTHENTICATED_ARCHIVE_SEAL_REQUIRED')
    sealed = stamp(raw).astimezone(timezone.utc)
    if sealed > cutoff:
        raise ValueError('SHORT_HORIZON_REPLAY_ARCHIVE_SEALED_AFTER_CUTOFF')
    return sealed.isoformat()


def _policy_identity():
    directory = Path(__file__).parent
    paths = [directory / name for name in (
        'short_horizon_daily_closure_v1.py', 'short_horizon_outcomes_v1.py',
        'short_horizon_archive_bridge_v1.py', 'outcomes.py', 'visibility.py',
        'history_artifacts.py', 'contracts.py')]
    paths += [directory.parent / name for name in ('observed_data.py', 'data.py', 'exchange_calendar_2026.json')]
    return {'version': VERSION, 'outcome_policy': OUTCOME_VERSION,
            'worker_encoding_profile': CLOSURE_ENCODING_PROFILE,
            'pending_run_materialization': PENDING_RUN_MATERIALIZATION,
            'source_hashes': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}}


def _bridge_module():
    from . import short_horizon_archive_bridge_v1
    return short_horizon_archive_bridge_v1


def _credential_identity(value):
    # Validation timestamps or diagnostic counters must not create new daily
    # jobs. These immutable content identities are returned by the verifier.
    return {key: value[key] for key in ('bridge_id', 'run_id', 'signal_content_hash',
        'input_id', 'archive_final_sha256', 'input_hash', 'source_protocol') if key in value}


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


    version = VERSION
    job_kind = JOB_KIND
    ledgers = LEDGERS
    job_prefix = 'short_daily_'

    def _policy_identity(self):
        policy = _policy_identity()
        resource = resolve_verification_profile(self.verification_profile)
        if resource is not None:
            policy = {**policy, 'verification_resource_profile': resource}
        return policy

    def _terminal(self, verification):
        result = verification.terminal()
        resource = resolve_verification_profile(self.verification_profile)
        if resource is None:
            return result
        limits = resource['limits']
        expected = {key: limits['max_' + key] for key in
                    ('total_read_bytes', 'unique_bytes', 'files', 'seconds', 'rss_bytes')}
        if canonical_json(result.get('limits')) != canonical_json(expected):
            raise ValueError('SHORT_HORIZON_CLOSURE_RESOURCE_LIMITS_MISMATCH')
        return {**result, 'verification_resource_profile': resource}

    def _session_options(self):
        return session_options(self.verification_profile)


    def _request_identity(self, request):
        return {key: request[key] for key in ('policy', 'run_ids', 'input_id', 'evaluation_session', 'bindings')}

    def _sealed_cutoff(self, request, evaluated):
        return None

    def _execution_context(self, request, started_at):
        return {}

    def _load_outcome_input(self, input_id, verification):
        return _bridge_module().load_verified_bridge_input(self.service, input_id, session=verification)

    def _validated_evaluation(self, request):
        identity = self._request_identity(request)
        evaluated = stamp(request['evaluated_at'])
        session_day = evaluated.astimezone(SH).date()
        if (request['root_job_id'] != self.job_prefix + digest(identity)
                or request['horizons'] != list(HORIZONS)
                or request['evaluation_session'] != str(session_day)
                or session_day not in exchange_calendar_2026().sessions
                or evaluated.astimezone(SH).time().replace(tzinfo=None) < time(15)
                or evaluated > stamp(self.clock())):
            raise ValueError('SHORT_HORIZON_CLOSURE_FROZEN_REQUEST_INVALID')
        return evaluated, session_day

    def __init__(self, service, *, clock=utc_now, max_runs=5, verification_profile=None):
        if type(max_runs) is not int or not 1 <= max_runs <= 100:
            raise ValueError('SHORT_HORIZON_CLOSURE_MAX_RUNS_REQUIRES_1_TO_100')
        resolve_verification_profile(verification_profile)
        self.service, self.clock, self.max_runs = service, clock, max_runs
        self.verification_profile = verification_profile

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
                       retain_input=False, sealed_cutoff=None):
        if not isinstance(input_id, str) or not input_id:
            raise ValueError('SHORT_HORIZON_REGISTERED_OUTCOME_INPUT_ID_REQUIRED')
        bridge = _bridge_module()
        sealed_archives = {}
        seal_options = {'include_archive_seal': True} if sealed_cutoff is not None else {}
        def check_seal(view):
            if sealed_cutoff is not None:
                value = _closed_archive_seal(view, sealed_cutoff)
                key = view['credential']['bridge_id']
                if key in sealed_archives and sealed_archives[key] != value:
                    raise ValueError('SHORT_HORIZON_REPLAY_ARCHIVE_SEAL_CONFLICT')
                sealed_archives[key] = value
        def authenticate_input():
            view = bridge.authenticated_bridge_view(self.service, input_id=input_id, session=session,
                **({'retain_input': True} if retain_input else {}), **seal_options)
            certificate = view['credential']
            check_seal(view)
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
        # A saved input binding is only a scheduling hint. Every requested run
        # still passes authenticated_bridge_view. Delaying its owning run lets
        # this invocation reuse the input's fully verified private proof rather
        # than normalizing that same archive a third time in a multi-run batch.
        hint, deferred = None, None
        if retain_input and len(run_ids) > 1:
            hint = self.service.store.get_document(INPUT_BINDINGS, input_id)
            if isinstance(hint, dict) and hint.get('run_id') in run_ids:
                deferred = hint['run_id']

        def authenticate_run(run_id):
            checkpoint()
            view = bridge.authenticated_bridge_view(self.service, run_id=run_id, session=session,
                                                    borrow_run=borrow_run, **seal_options)
            certificate = view['credential']
            check_seal(view)
            run = view['run'] if borrow_run else view['run_metadata']
            if (run.get('execution_profile') != 'SHORT_HORIZON_RESEARCH_SINGLE'
                    or run.get('strategy_id') != 'SHORT_HORIZON_STRENGTH_R2'
                    or certificate.get('run_id') != run_id
                    or certificate.get('signal_content_hash') != run.get('content_hash')
                    or certificate.get('input_id') != run.get('input_id')):
                raise ValueError('SHORT_HORIZON_CLOSURE_TRUSTED_FROZEN_R2_REQUIRED')
            original = _input_calendar(view, run['input_id'])
            session.release_cached_archives()
            return run, _credential_identity(certificate), original

        authenticated = {}
        for run_id in run_ids:
            if run_id != deferred:
                authenticated[run_id] = authenticate_run(run_id)
        if retain_input:
            input_certificate, bundle = authenticate_input()
        if deferred is not None:
            # Neither a stale hint nor an input view grants run authority. The
            # run-specific API below rechecks its actual binding, certificate,
            # source registrations and parent bytes under the same store proof.
            if (input_certificate.get('run_id') != deferred
                    or any(hint.get(key) != value for key, value in input_certificate.items())
                    or self.service.store.get_document(INPUT_BINDINGS, input_id) != hint):
                raise ValueError('SHORT_HORIZON_CLOSURE_INPUT_RUN_HINT_CHANGED')
            authenticated[deferred] = authenticate_run(deferred)
        ordered = [authenticated[run_id] for run_id in run_ids]
        runs = [value[0] for value in ordered]
        credentials = [value[1] for value in ordered]
        originals = [value[2] for value in ordered]
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
        bindings = {'runs': credentials, 'outcome_input': _credential_identity(input_certificate)}
        if sealed_cutoff is not None:
            bindings['archive_seals'] = sealed_archives
        return runs, bundle, bindings

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
        verification = _bridge_module().ArchiveVerificationSession(**self._session_options())
        runs, bundle, bindings = self._authenticated(run_ids, input_id, verification,
            sealed_cutoff=self._sealed_cutoff(None, evaluated))
        latest_input_session = max(bundle.sessions) if bundle.sessions else None
        if bundle.observation_date != session_day or latest_input_session != session_day:
            reasons.append('INPUT_NOT_CURRENT_SESSION')
        if any(evaluated < max(stamp(run['decision_cutoff']), stamp(run.get('generated_at', run['decision_cutoff'])))
               for run in runs):
            reasons.append('EVALUATION_PRECEDES_FROZEN_SIGNAL')
        plans = self._schedule(runs, bundle, evaluated)
        authentication = self._terminal(verification)
        policy = self._policy_identity()
        identity = {'policy': policy, 'run_ids': run_ids, 'input_id': input_id,
                    'evaluation_session': str(session_day), 'bindings': bindings}
        job_id = self.job_prefix + digest(identity)
        request = {**identity, 'root_job_id': job_id, 'evaluated_at': evaluated.isoformat(),
                   'horizons': list(HORIZONS)}
        return {'schema_version': self.version, 'job_id': job_id, 'status': reasons[0] if reasons else 'READY',
                'can_submit': not reasons, 'reason_codes': reasons, 'request': request, 'runs': plans,
                'run_count': len(run_ids), 'max_runs': self.max_runs, 'collection': 'NOT_REQUESTED',
                'outcome_input_observation_date': str(bundle.observation_date),
                'outcome_input_latest_session': str(latest_input_session) if latest_input_session else None,
                'archive_verification': authentication,
                'market_edge': 'NOT_ESTABLISHED'}

    @staticmethod
    def _all_horizons_pending(planned):
        rows = planned.get('horizons')
        return (planned.get('due_horizons') == [] and planned.get('unknown_horizons') == []
            and type(rows) is list and len(rows) == len(HORIZONS)
            and all(type(row) is dict and type(row.get('horizon')) is int
                    and row['horizon'] == horizon and row.get('maturity_state') == 'PENDING'
                    for row, horizon in zip(rows, HORIZONS)))

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
        verification = bridge.ArchiveVerificationSession(**self._session_options())
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
        authentication = self._terminal(verification)
        inventory.sort(key=lambda row: (row['observation_date'], row['run_id']))
        return {'schema_version': self.version, 'status': gate, 'input_id': input_id,
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
        return self._submit_plan(plan)

    def _submit_plan(self, plan):
        if not plan['can_submit']:
            raise ValueError('SHORT_HORIZON_CLOSURE_' + plan['status'])
        with self._write_jobs()._lock:
            # Policy/source revisions and a smaller batch must not hide an old
            # accepted request. Keep its first cutoff, failure and spent budget.
            requested = plan['request']
            previous_roots = {}
            offset = 0
            while True:
                rows = self.service.jobs.list(limit=1000, offset=offset)
                for prior in rows:
                    payload = prior.get('payload') or {}
                    if (prior.get('kind') in {'strategy_outcomes', 'strategy_tracking'}
                            and payload.get('input_id') == requested['input_id']
                            and payload.get('run_id') in requested['run_ids']):
                        raise JobConflict('prior legacy outcome request requires explicit reconciliation')
                    if (str(prior.get('kind', '')).startswith('short_horizon_')
                            and payload.get('input_id') == requested['input_id']
                            and payload.get('evaluation_session') == requested['evaluation_session']
                            and set(payload.get('run_ids') or ()) & set(requested['run_ids'])):
                        if (prior['kind'] != self.job_kind
                                or set(payload.get('run_ids') or ()) != set(requested['run_ids'])):
                            raise JobConflict('prior overlapping outcome request requires explicit reconciliation')
                        previous_roots.setdefault(payload['root_job_id'], prior)
                offset += len(rows)
                if len(rows) < 1000:
                    break
                if offset >= MAX_DISCOVERY_RECORDS:
                    raise JobConflict('outcome request reconciliation scan limit reached')
            if len(previous_roots) > 1:
                raise JobConflict('multiple prior outcome requests require explicit reconciliation')
            if previous_roots:
                root_id = next(iter(previous_roots))
                root = self.service.jobs.get(root_id)
                if root is None:
                    raise JobConflict('prior outcome root request is missing')
                return self._latest_attempt(root)
            self.service.jobs.register(self.job_kind, self._worker)
            existing = self.service.jobs.get(plan['job_id'])
            if existing is not None:
                # One day's first accepted cutoff is immutable. A later command
                # with the same input returns it, including FAILED/CANCELLED state.
                if existing['kind'] != self.job_kind or self._stable_request(existing['payload']) != self._stable_request(plan['request']):
                    raise JobConflict('daily closure job identity collision')
                return self._latest_attempt(existing)
            try:
                return self.service.jobs.submit(self.job_kind, plan['request'], job_id=plan['job_id'])
            except JobConflict:
                # Another caller can freeze the first cutoff between get/create.
                existing = self.service.jobs.get(plan['job_id'])
                if (existing is None or existing['kind'] != self.job_kind
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
            if (candidate['kind'] != self.job_kind or candidate['payload'] != original['payload']
                    or candidate['retry_of'] != latest['job_id']):
                raise JobConflict('daily closure retry identity collision')
            latest = candidate
        return latest

    def _job(self, job_id):
        job = self.service.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job['kind'] != self.job_kind:
            raise ValueError('SHORT_HORIZON_CLOSURE_JOB_REQUIRED')
        return job

    def status(self, job_id):
        job = self._job(job_id)
        return {'schema_version': self.version, 'job': job,
                'ledger': self.service.store.get_document(self.ledgers, job_id),
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


    def _worker(self, ctx):
        request = ctx.payload
        execution_started_at = utc_now()
        ctx.check_cancelled()
        if request['policy'] != self._policy_identity():
            raise ValueError('SHORT_HORIZON_CLOSURE_POLICY_CHANGED_AFTER_SUBMISSION')
        if self._run_ids(request['run_ids']) != request['run_ids']:
            raise ValueError('SHORT_HORIZON_CLOSURE_CANONICAL_RUN_SET_REQUIRED')
        evaluated, session_day = self._validated_evaluation(request)
        # The strict policy equality above binds this optional execution choice.
        # Policies without the field retain the original default call contract.
        encoding_options = ({'encoding_profile': request['policy']['worker_encoding_profile']}
                            if 'worker_encoding_profile' in request['policy'] else {})
        verification = _bridge_module().ArchiveVerificationSession(checkpoint=ctx.check_cancelled,
                                                                   **encoding_options,
                                                                   **self._session_options())
        runs, calendar, bindings = self._authenticated(request['run_ids'], request['input_id'], verification,
                                                     checkpoint=ctx.check_cancelled, retain_input=True,
                                                     sealed_cutoff=self._sealed_cutoff(request, evaluated))
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
        bundle = self._load_outcome_input(request['input_id'], verification)
        if bundle.observation_date != calendar.observation_date or bundle.sessions != calendar.sessions:
            raise ValueError('SHORT_HORIZON_CLOSURE_INPUT_CHANGED_AFTER_AUTHENTICATION')
        borrowed, pending_nonempty = [], set()
        seal_options = ({'include_archive_seal': True}
                        if self._sealed_cutoff(request, evaluated) is not None else {})
        use_pending_projection = (request['policy'].get('pending_run_materialization')
                                  == PENDING_RUN_MATERIALIZATION)
        def checked_run_view(metadata, credential, **borrow_options):
            ctx.check_cancelled()
            view = _bridge_module().authenticated_bridge_view(self.service, run_id=metadata['run_id'],
                session=verification, **borrow_options, **seal_options)
            if self._sealed_cutoff(request, evaluated) is not None:
                sealed = _closed_archive_seal(view, evaluated)
                if sealed != bindings['archive_seals'].get(view['credential']['bridge_id']):
                    raise ValueError('SHORT_HORIZON_REPLAY_ARCHIVE_SEAL_CONFLICT')
            if view['run_metadata'] != metadata or _credential_identity(view['credential']) != credential:
                raise ValueError('SHORT_HORIZON_CLOSURE_BORROWED_RUN_IDENTITY_CHANGED')
            return view

        for index, (metadata, credential, planned) in enumerate(zip(runs, bindings['runs'], plans)):
            projected = use_pending_projection and self._all_horizons_pending(planned)
            view = checked_run_view(metadata, credential,
                                    **({'borrow_if_empty': True} if projected else {'borrow_run': True}))
            if projected and 'pool_nonempty' not in view:
                # Older injected verifier boundaries have no authenticated
                # projection. Missing evidence always retains full borrowing.
                view = checked_run_view(metadata, credential, borrow_run=True)
                projected = False
            if projected:
                nonempty = view['pool_nonempty']
                if type(nonempty) is not bool:
                    raise ValueError('SHORT_HORIZON_CLOSURE_PENDING_POOL_PROJECTION_INVALID')
                if nonempty:
                    if view.get('run') is not None:
                        raise ValueError('SHORT_HORIZON_CLOSURE_PENDING_POOL_PROJECTION_INVALID')
                    # Every archive and both stored parent bytes were verified
                    # by this invocation. The private proof only establishes
                    # nonemptiness; scheduling still comes from _schedule.
                    pending_nonempty.add(index)
                    borrowed.append(metadata)
                    continue
            run = view['run']
            if {key: run.get(key) for key in metadata} != metadata:
                raise ValueError('SHORT_HORIZON_CLOSURE_BORROWED_RUN_IDENTITY_CHANGED')
            if projected and run['results'][run['strategy_id']].get('items'):
                raise ValueError('SHORT_HORIZON_CLOSURE_PENDING_POOL_PROJECTION_INVALID')
            borrowed.append(run)
        runs = borrowed
        ctx.check_cancelled()
        products, records = [], []
        for index, (run, planned) in enumerate(zip(runs, plans)):
            ctx.check_cancelled()
            if index in pending_nonempty:
                records.append({**planned, 'status': 'PENDING', 'revision_id': None,
                                'counts': {}, 'product_revision_available': False})
                continue
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
            result = evaluate(run, bundle, evaluated, horizons=horizons, checkpoint=ctx.check_cancelled,
                              **encoding_options)
            products.append(result)
            counts = result['outcomes']['counts']
            records.append({**planned, 'status': 'COMPLETED_WITH_UNKNOWNS' if counts.get('UNKNOWN') else 'OBSERVED',
                'revision_id': result['outcomes']['revision_id'], 'counts': counts, 'product_revision_available': True})
        authentication = self._terminal(verification)
        ctx.check_cancelled()
        has_unknown = any(record['status'] in {'UNKNOWN', 'UNKNOWN_EMPTY_POOL_PROOF_REQUIRED', 'COMPLETED_WITH_UNKNOWNS'}
                          or record['unknown_horizons'] for record in records)
        has_pending = any(row['maturity_state'] == 'PENDING' for record in records
                          if record['status'] != 'LEGAL_EMPTY_POOL' for row in record['horizons'])
        ledger = {'schema_version': self.version, 'job_id': ctx.job_id, 'root_job_id': request['root_job_id'],
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
        ledger.update(self._execution_context(request, execution_started_at))

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
            self.service.store._put_document(connection, self.ledgers, ctx.job_id, ledger)
            # The ledger retains the successful prepublication audit snapshot.
            # Recheck every original and borrowed value after all domain writes,
            # while failure can still roll back the ledger and the whole batch.
            self._terminal(verification)

        return ctx.complete({'schema_version': self.version, 'ledger_id': ctx.job_id, 'status': ledger['status'],
                             'run_count': len(runs), 'revision_ids': [result['outcomes']['revision_id'] for result in products],
                             'market_edge': 'NOT_ESTABLISHED'}, write)
