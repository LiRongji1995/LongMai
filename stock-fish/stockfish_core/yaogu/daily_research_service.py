"""Daily research artifacts on the existing authoritative store.

No strategy calculation, external ingestion, or writes occur in read methods.
"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from ..common import UnavailableError
from ..storage import checked_payload
from .contracts import digest

SH = ZoneInfo('Asia/Shanghai')
STAGE = 'STAGE_AWARE_MOMENTUM_V2'
SNAPSHOTS = 'daily_research_snapshots'
OUTCOMES = 'daily_research_outcomes'
CAPTURES = 'daily_research_outcome_captures'
PENDING = 'daily_research_outcome_pending'


def _instant(value):
    if value is None:
        return None
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('timezone-aware report cutoff required')
    return result


class DailyResearchMixin:
    def _reusable_daily_request(self, request_key, input_id):
        """Called under the existing service lock; reuse only identical evidence.

        Later visibility inside the same registered input prevents completed-run
        reuse. Failed/NOT_READY attempts remain retryable and are never erased.
        """
        offset = 0
        while True:
            page = self.jobs.list(kind='daily_primary_strategy', limit=1000, offset=offset)
            for job in page:
                if (job.get('payload') or {}).get('daily_request_key') != request_key:
                    continue
                if job['state'] in {'QUEUED', 'RUNNING'}:
                    return job
                result = job.get('result') or {}
                if input_id and job['state'] == 'SUCCEEDED' and result.get('final_status') == 'READY':
                    _, bundle = self.input(input_id)
                    latest_visibility = self._max_evidence_clock(bundle)
                    cutoff = _instant(result.get('actual_decision_cutoff'))
                    if cutoff is not None and latest_visibility is not None and latest_visibility <= cutoff:
                        return job
            if len(page) < 1000:
                return None
            offset += len(page)

    def _daily_research_now(self):
        scheduler = getattr(self, 'scheduler', None)
        if scheduler is not None:
            return scheduler.clock()
        from .candidate_service import utc_now
        return utc_now()

    def _daily_research_sessions(self):
        scheduler = getattr(self, 'scheduler', None)
        if scheduler is not None:
            return sorted(set(scheduler.sessions))
        from ..observed_data import exchange_calendar_2026
        return sorted(set(exchange_calendar_2026().sessions))

    def _daily_target_after(self, observation_date):
        if not observation_date:
            return None
        day = date.fromisoformat(str(observation_date)[:10])
        return next((str(s) for s in self._daily_research_sessions() if s > day), None)

    def _init_daily_research(self):
        self.jobs.register('daily_research_outcomes', self._daily_recommendation_outcome_job)
        # JobService has already marked crashed jobs INTERRUPTED. Reconcile those
        # durable failures at startup, never as a side effect of a GET request.
        for kind in ('daily_primary_strategy', 'scheduled_candidate_capture'):
            offset = 0
            while True:
                page = self.jobs.list(kind=kind, limit=1000, offset=offset)
                for job in page:
                    self._record_daily_research_terminal(job)
                if len(page) < 1000:
                    break
                offset += len(page)

    def _record_daily_research_terminal(self, job):
        if job.get('state') not in {'FAILED', 'INTERRUPTED', 'CANCELLED'}:
            return
        payload = job.get('payload') or {}
        if job.get('kind') == 'scheduled_candidate_capture' and payload.get('market_session') != 'EVENING':
            return
        attempt_id = self._daily_primary_attempt_id(job['job_id'])
        if self.store.get_document('daily_primary_attempts', attempt_id) is not None:
            return
        when = _instant(job.get('finished_at') or job.get('updated_at') or job.get('created_at'))
        observation = payload.get('expected_observation_session') or payload.get('session') or payload.get('requested_observation_date')
        if not observation and payload.get('input_id'):
            index = self.store.get_document('candidate_input_indexes', payload['input_id']) or {}
            observation = index.get('observation_date')
        attempt = self._daily_primary_attempt(
            job_id=job['job_id'], input_id=payload.get('input_id'), cutoff=when,
            final_status='FAILED', readiness=None,
            error=job.get('error') or {'code': job['state']},
            expected_observation_session=observation,
            daily_primary_mode='SCHEDULED_PRIMARY' if payload.get('market_session') == 'EVENING' else payload.get('daily_primary_mode'),
        )
        attempt.update(observation_date=observation, target_session=self._daily_target_after(observation),
                       stage='JOB_' + job['state'], timestamp=when.isoformat(),
                       generated_at=when.isoformat(), recovered_at=self._daily_research_now().isoformat(),
                       job_terminal_state=job['state'], recovered_from_job=True)
        self.store.put_document('daily_primary_attempts', attempt_id, attempt)

    def _write_daily_research_snapshot(self, connection, run, expanded_items):
        if STAGE not in (run.get('results') or {}):
            return
        from .daily_research import recommendation_snapshot
        attempt = {'final_status': 'READY'} if run.get('execution_profile') == 'DAILY_PRIMARY_FAST' else None
        snapshot = recommendation_snapshot(run, expanded_items=expanded_items, attempt=attempt)
        self.store._put_document(connection, SNAPSHOTS, run['run_id'], snapshot)
        selected = [r['instrument_id'] for r in snapshot['items'] if r['decision_status'] in {'FOCUS', 'WATCH'}]
        self.store._put_document(connection, PENDING, run['run_id'], {
            'run_id': run['run_id'], 'observation_date': run['observation_date'],
            'snapshot_hash': snapshot['snapshot_hash'], 'instrument_ids': selected,
            'horizons': list(range(1, 21)), 'created_at': run.get('generated_at'),
        })

    def daily_recommendation_snapshot(self, run_id):
        saved = self.store.get_document(SNAPSHOTS, run_id)
        if saved is not None:
            return saved
        # Legacy run is a read-only projection. Never rerun V2 or fill identity
        # from today's configuration/implementation.
        from .daily_research import recommendation_snapshot
        run = self.strategy_experiment(run_id)
        stage = (run.get('results') or {}).get(STAGE)
        if stage is None:
            raise KeyError(run_id)
        rows = [self._expand_strategy_row_detail(row) for row in stage.get('items') or []]
        return recommendation_snapshot(run, expanded_items=rows)

    def _daily_outcome_rows(self, run_id=None):
        sql = 'SELECT * FROM documents WHERE namespace=?'
        params = [OUTCOMES]
        if run_id is not None:
            sql += " AND json_extract(payload,'$.run_id')=?"
            params.append(run_id)
        with self.store.transaction(write=False) as connection:
            return [checked_payload(row) for row in connection.execute(sql, params)]

    def _daily_snapshot_inventory(self):
        # Lightweight summaries identify legacy runs; only those needed by a
        # consumer are expanded. No fixed latest-N exclusion of old signals.
        return [r for r in self._all('strategy_experiment_summaries')
                if STAGE in (r.get('results') or {})]

    def _daily_publication_time(self, snapshot):
        """Known durable completion bounds, without editing the frozen snapshot."""
        job_ids = {(snapshot.get('prospective_validation') or {}).get('scheduler_job_id')}
        with self.store.transaction(write=False) as connection:
            attempts = connection.execute(
                "SELECT * FROM documents WHERE namespace=? AND json_extract(payload,'$.run_id')=?",
                ('daily_primary_attempts', snapshot['identity']['run_id']))
            job_ids.update(checked_payload(row).get('job_id') for row in attempts)
        clocks = []
        for job_id in job_ids - {None}:
            job = self.jobs.get(job_id) or {}
            if job.get('finished_at'):
                clocks.append(_instant(job['finished_at']))
        return max(clocks) if clocks else None

    def pending_daily_recommendation_outcomes(self):
        from .daily_research_outcomes import select_daily_outcome_revisions
        now = self._daily_research_now()
        values = []
        for meta in self._daily_snapshot_inventory():
            run_id = meta['run_id']
            snapshot = self.daily_recommendation_snapshot(run_id)
            wanted = {r['instrument_id'] for r in snapshot['items'] if r['decision_status'] in {'FOCUS', 'WATCH'}}
            if not wanted:
                continue
            rows = select_daily_outcome_revisions(self._daily_outcome_rows(run_id), now)
            valid = {(r['instrument_id'], r['offset']) for r in rows
                     if r.get('snapshot_hash') == snapshot['snapshot_hash'] and r.get('status') == 'NORMAL'
                     and r.get('close_reference_change') is not None and r.get('mfe_bound') is not None and r.get('mae_bound') is not None}
            horizons = [h for h in range(1, 21) if any((iid, h) not in valid for iid in wanted)]
            if horizons:
                values.append({'run_id': run_id, 'observation_date': snapshot['identity']['observation_date'],
                               'horizons': horizons, 'instrument_ids': sorted(wanted)})
        return sorted(values, key=lambda row: (row['observation_date'], row['run_id']))

    def daily_recommendation_outcome_demand(self, observation_date, sessions):
        days = sorted(set(sessions))
        values = []
        for pending in self.pending_daily_recommendation_outcomes():
            start = date.fromisoformat(pending['observation_date'])
            if start >= observation_date:
                continue
            future = [d for d in days if start < d <= observation_date][:20]
            if not future:
                continue
            due = [h for h in pending['horizons'] if h <= len(future)]
            if not due:
                continue
            for iid in pending['instrument_ids']:
                values.append({**pending, 'instrument_id': iid, 'horizons': due,
                    'pending_fields': {str(h): ['daily_close', 'comparable_path', 'trading_status'] for h in due},
                    'start_date': str(start), 'end_date': str(future[-1]),
                    'selection_reason': 'DAILY_RESEARCH_T1_T20_ORIGINAL_FOCUS_WATCH'})
        return values

    def submit_daily_recommendation_outcomes(self, run_id, input_id=None, *, job_id=None):
        from ..jobs import JobConflict
        from .daily_research_outcomes import POLICY_ID
        snapshot = self.daily_recommendation_snapshot(run_id)
        resolved, bundle = self.strategy_input(input_id)
        if str(bundle.observation_date) < snapshot['identity']['observation_date']:
            raise ValueError('outcome input predates signal')
        # The server chooses evaluation time; callers cannot supply returns,
        # trust, cutoffs, or arbitrary file paths.
        now = self._daily_research_now()
        closed = [s for s in self._daily_research_sessions() if datetime.combine(s, time(15), SH) <= now]
        key = 'daily_research_outcome_' + digest({'run_id': run_id, 'input_id': resolved,
            'snapshot_hash': snapshot['snapshot_hash'], 'policy': POLICY_ID,
            'evaluation_session': str(closed[-1]) if closed else None})
        base_id = job_id or key
        def checked_existing(existing):
            frozen = existing.get('payload') or {}
            expected = {'run_id': run_id, 'input_id': resolved,
                        'snapshot_hash': snapshot['snapshot_hash'], 'policy': POLICY_ID}
            if existing.get('kind') != 'daily_research_outcomes' or any(
                    frozen.get(field) != value for field, value in expected.items()):
                raise ValueError('daily research outcome job identity mismatch')
            return existing
        existing = self.jobs.get(base_id)
        if existing is not None:
            return checked_existing(existing)
        try:
            return self.jobs.submit('daily_research_outcomes', {
                'run_id': run_id, 'input_id': resolved, 'evaluated_at': now.isoformat(),
                'snapshot_hash': snapshot['snapshot_hash'], 'policy': POLICY_ID,
            }, job_id=base_id)
        except JobConflict:
            # Concurrent callers may differ only by server evaluation microseconds.
            existing = self.jobs.get(base_id)
            if existing is None:
                raise
            return checked_existing(existing)

    def _daily_recommendation_outcome_job(self, ctx):
        from .daily_research_outcomes import POLICY_ID, evaluate_daily_research_outcomes
        payload = ctx.payload
        if payload.get('policy') != POLICY_ID:
            raise ValueError('daily research outcome policy mismatch')
        snapshot = self.daily_recommendation_snapshot(payload['run_id'])
        if snapshot['snapshot_hash'] != payload['snapshot_hash']:
            raise ValueError('daily research snapshot identity mismatch')
        _, bundle = self.input(payload['input_id'])
        ctx.update(.1, '读取冻结推荐并跟踪 T+1 到 T+20；不重算选股')
        rows = evaluate_daily_research_outcomes(snapshot, bundle, _instant(payload['evaluated_at']),
                                               sessions=self._daily_research_sessions())
        ctx.check_cancelled()
        capture = {'run_id': payload['run_id'], 'input_id': payload['input_id'],
                   'snapshot_hash': snapshot['snapshot_hash'], 'evaluated_at': payload['evaluated_at'],
                   'job_id': ctx.job_id, 'row_count': len(rows), 'policy': payload['policy']}
        def write(connection):
            for row in rows:
                self.store._put_document(connection, OUTCOMES, row['observation_id'], row)
            self.store._put_document(connection, CAPTURES, ctx.job_id, capture)
        return ctx.complete({**capture, 'status': 'complete'}, write)

    def daily_recommendation_outcomes(self, run_id, *, as_of=None, instrument_id=None):
        from .daily_research_outcomes import select_daily_outcome_revisions, summarize_daily_research_outcomes, snapshot_available_as_of
        now = _instant(as_of) or self._daily_research_now()
        snapshot = self.daily_recommendation_snapshot(run_id)
        if not snapshot_available_as_of(snapshot, now):
            raise ValueError('snapshot availability cannot be established at the requested report cutoff')
        published = self._daily_publication_time(snapshot)
        if published and published > now:
            raise ValueError('snapshot was not published at the requested report cutoff')
        all_rows = self._daily_outcome_rows(run_id)
        rows = [r for r in select_daily_outcome_revisions(all_rows, now)
                if r.get('snapshot_hash') == snapshot['snapshot_hash']]
        if instrument_id is not None:
            if not any(r['instrument_id'] == instrument_id for r in snapshot['items']):
                raise KeyError(instrument_id)
            rows = [r for r in rows if r['instrument_id'] == instrument_id]
        summary = summarize_daily_research_outcomes([snapshot], all_rows, now,
                                                    sessions=self._daily_research_sessions())
        selected = [r for r in snapshot['items'] if r['decision_status'] in {'FOCUS', 'WATCH'}]
        tracking_status = 'RECORDED' if rows else 'PENDING_CAPTURE'
        if instrument_id and not any(r['instrument_id'] == instrument_id for r in selected):
            tracking_status = 'NOT_TRACKED_DECISION'
        elif not selected:
            tracking_status = 'NO_TRACKED_RECOMMENDATIONS'
        return {'run_id': run_id, 'report_as_of': now.isoformat(), 'horizons': list(range(1, 21)),
                'instrument_id': instrument_id, 'tracking_status': tracking_status, 'items': rows,
                'summary_scope': 'RUN', 'summary': summary, 'market_edge': 'NOT_ESTABLISHED'}

    def daily_recommendation_analysis(self, *, as_of=None, strategy_version=None, run_id=None):
        from .daily_research_outcomes import summarize_daily_research_outcomes
        now = _instant(as_of) or self._daily_research_now()
        metas = [{'run_id': run_id}] if run_id else self._daily_snapshot_inventory()
        snapshots = []
        later_publications = 0
        for meta in metas:
            snap = self.daily_recommendation_snapshot(meta['run_id'])
            if strategy_version and snap['identity']['strategy_version'] != strategy_version:
                continue
            published = self._daily_publication_time(snap)
            if published and published > now:
                later_publications += 1
                continue
            snapshots.append(snap)
        rows = self._daily_outcome_rows(run_id)
        result = summarize_daily_research_outcomes(snapshots, rows, now, sessions=self._daily_research_sessions())
        result['snapshot_availability']['excluded_not_yet_available'] += later_publications
        return result

    def daily_research_delivery(self, target_session, *, as_of=None):
        from .daily_research_outcomes import snapshot_available_as_of
        now = _instant(as_of) or self._daily_research_now()
        target = date.fromisoformat(str(target_session))
        sessions = self._daily_research_sessions()
        prior = [s for s in sessions if s < target]
        base = {'target_session': str(target), 'report_as_of': now.isoformat(), 'run_id': None,
                'execution_status': 'NOT_READY', 'observation_date': str(prior[-1]) if prior else None,
                'blockers': [], 'missing_evidence': [], 'reason': None, 'error': None,
                'strategy_version': None, 'config_version': None, 'data_cutoff': None, 'generated_at': None,
                'stage': None, 'timestamp': None, 'read_only': True, 'attempts': []}
        if target not in sessions or not prior:
            return {**base, 'reason': 'VERIFIED_TARGET_CALENDAR_UNAVAILABLE',
                    'blockers': ['VERIFIED_TARGET_CALENDAR_UNAVAILABLE']}
        observation = str(prior[-1])

        def for_observation(row):
            if row.get('signal_session') == observation:
                return True
            stamp = _instant(row.get('scheduled_at'))
            return stamp is not None and stamp.astimezone(SH).date().isoformat() == observation

        def frozen_identity(row):
            identity = row.get('research_identity') or row
            return {'strategy_version': identity.get('signal_implementation_version'),
                    'config_version': identity.get('signal_config_hash'),
                    'validation_epoch_id': identity.get('validation_epoch_id')}

        events = [e for e in self._all('candidate_schedule_events')
                  if e.get('market_session') in {'EVENING', 'DAILY_PRIMARY_TERMINAL'}
                  and for_observation(e) and _instant(e.get('observed_at')) is not None
                  and _instant(e['observed_at']) <= now]
        events.sort(key=lambda e: (_instant(e['observed_at']), e.get('event_id') or ''))
        job_ids = {e.get('job_id') or e.get('event_id') for e in events}
        plans = [p for p in self._all('stage_aware_prospective_validation_schedules')
                 if for_observation(p) and _instant(p.get('registered_at')) is not None
                 and _instant(p['registered_at']) <= now]
        jobs = {}
        # Generic explicit retries have random IDs and may have no EVENING
        # submission event. Their original session/mode remains in the payload.
        for kind in ('daily_primary_strategy', 'scheduled_candidate_capture'):
            offset = 0
            while True:
                page = self.jobs.list(kind=kind, limit=1000, offset=offset)
                for job in page:
                    payload = job.get('payload') or {}
                    scheduled = kind == 'scheduled_candidate_capture' and payload.get('market_session') == 'EVENING'
                    latest = kind == 'daily_primary_strategy' and payload.get('daily_primary_mode') == 'NORMAL_LATEST'
                    day = payload.get('expected_observation_session') or payload.get('session')
                    if (scheduled or latest) and day == observation:
                        jobs[job['job_id']] = job
                if len(page) < 1000:
                    break
                offset += len(page)
        for job_id in job_ids:
            job = self.jobs.get(job_id)
            if job is not None:
                jobs[job_id] = job
        job_ids.update(jobs)

        def created_at(job):
            payload = job.get('payload') or {}
            return _instant(job.get('created_at') or payload.get('actual_requested_at')
                            or payload.get('request_started_at'))

        def completion(job_id):
            job = jobs.get(job_id) or self.jobs.get(job_id)
            if job and job.get('state') in {'SUCCEEDED', 'FAILED', 'CANCELLED', 'INTERRUPTED'}:
                stamp = _instant(job.get('finished_at'))
                if stamp is not None:
                    return stamp, job['state']
            terminal = [e for e in events if e.get('job_id') == job_id
                        and e.get('market_session') == 'DAILY_PRIMARY_TERMINAL'
                        and e.get('status') in {'SUCCEEDED', 'FAILED', 'CANCELLED', 'INTERRUPTED'}]
            if terminal:
                return _instant(terminal[-1]['observed_at']), terminal[-1]['status']
            return None, None

        attempts = [a for a in self._all('daily_primary_attempts') if (a.get('job_id') in job_ids
                    or (a.get('daily_primary_mode') == 'NORMAL_LATEST'
                        and (a.get('expected_observation_session') or a.get('observation_date')) == observation))
                    and _instant(a.get('timestamp') or a.get('actual_decision_cutoff')) is not None
                    and _instant(a.get('timestamp') or a.get('actual_decision_cutoff')) <= now
                    and (completion(a.get('job_id'))[0] is None or completion(a.get('job_id'))[0] <= now)]
        attempts.sort(key=lambda a: (_instant(a.get('timestamp') or a.get('actual_decision_cutoff')), a['attempt_id']))
        base['attempts'] = attempts
        ready = [a for a in attempts if a.get('final_status') == 'READY' and a.get('run_id')]
        primary = [r for r in self._all('stage_aware_prospective_primary_runs')
                   if r.get('target_session') == str(target) and r.get('decision_cutoff')
                   and _instant(r['decision_cutoff']) <= now]
        visible = []
        for item in ready + primary:
            finished, state = completion(item.get('job_id') or item.get('scheduler_job_id'))
            if state != 'SUCCEEDED' or finished is None or finished > now:
                continue
            candidate_snapshot = self.daily_recommendation_snapshot(item['run_id'])
            if snapshot_available_as_of(candidate_snapshot, now):
                visible.append((finished, item))
        if visible:
            published_at, selected = max(visible, key=lambda pair: (pair[0], pair[1]['run_id']))
            snapshot = self.daily_recommendation_snapshot(selected['run_id'])
            return {**base, **snapshot['identity'], 'target_session': str(target), 'execution_status': 'READY',
                    'timestamp': published_at.isoformat(), 'published_at': published_at.isoformat(),
                    'published': True, 'reason': 'SAVED_DAILY_PRIMARY',
                    'latest_attempt_status': attempts[-1]['final_status'] if attempts else None}

        in_progress = []
        failed_jobs = []
        for job_id, job in jobs.items():
            created = created_at(job)
            if created is not None and created > now:
                continue
            finished, state = completion(job_id)
            if finished is not None and finished <= now:
                if state in {'FAILED', 'CANCELLED', 'INTERRUPTED'}:
                    failed_jobs.append((finished, job))
                continue
            started = _instant(job.get('started_at'))
            if job.get('state') in {'QUEUED', 'RUNNING', 'CANCEL_REQUESTED'} or (finished and finished > now):
                in_progress.append((created or started or now, job,
                                    'RUNNING' if started and started <= now else 'QUEUED'))
        if in_progress:
            _, job, state = max(in_progress, key=lambda row: (row[0], row[1]['job_id']))
            return {**base, **frozen_identity(job.get('payload') or {}), 'execution_status': state,
                    'reason': 'DAILY_PRIMARY_IN_PROGRESS', 'job_id': job['job_id'],
                    'timestamp': (created_at(job) or _instant(job.get('started_at')) or now).isoformat(),
                    'previous_attempt_status': attempts[-1]['final_status'] if attempts else None,
                    'previous_blockers': attempts[-1].get('blocking_requirements') or [] if attempts else []}
        if attempts:
            last = attempts[-1]
            if last['final_status'] == 'READY':
                return {**base, 'reason': 'PUBLICATION_COMPLETION_TIME_UNVERIFIED',
                        'blockers': ['PUBLICATION_COMPLETION_TIME_UNVERIFIED']}
            # A manual retry may have a terminal job but no domain attempt yet.
            last_stamp = _instant(last.get('timestamp') or last.get('actual_decision_cutoff'))
            if failed_jobs:
                newest_failure_time, newest_failure_job = max(failed_jobs, key=lambda pair: pair[0])
                if newest_failure_job['job_id'] != last.get('job_id') and newest_failure_time > last_stamp:
                    last = None
            if last is not None:
                return {**base, 'execution_status': last['final_status'], 'error': last.get('error'),
                        **{key: last.get(key) for key in ('strategy_version', 'config_version', 'data_cutoff', 'generated_at')},
                        'stage': last.get('stage'), 'timestamp': last.get('timestamp') or last.get('actual_decision_cutoff'),
                        'blockers': last.get('blocking_requirements') or [],
                        'missing_evidence': last.get('missing_evidence') or last.get('blocking_requirements') or [],
                        'reason': last.get('reason') or last['final_status']}
        if failed_jobs:
            finished, job = max(failed_jobs, key=lambda pair: (pair[0], pair[1]['job_id']))
            return {**base, **frozen_identity(job.get('payload') or {}), 'execution_status': 'FAILED',
                    'reason': job['state'], 'error': job.get('error'), 'job_id': job['job_id'],
                    'stage': 'JOB_' + job['state'], 'timestamp': finished.isoformat()}
        for event in reversed(events):
            if event.get('status') == 'MISSED_SERVICE_OFFLINE':
                return {**base, **frozen_identity(event), 'execution_status': 'MISSED',
                        'reason': event.get('reason') or 'MISSED_SERVICE_OFFLINE',
                        'timestamp': event.get('observed_at')}
        if ready or primary:
            return {**base, 'reason': 'PUBLICATION_COMPLETION_TIME_UNVERIFIED',
                    'blockers': ['PUBLICATION_COMPLETION_TIME_UNVERIFIED']}
        if not plans:
            return {**base, 'reason': 'NO_REGISTERED_DAILY_PRIMARY_PLAN',
                    'blockers': ['NO_REGISTERED_DAILY_PRIMARY_PLAN']}
        plan = max(plans, key=lambda row: _instant(row['registered_at']))
        deadline = datetime.combine(target, time(8, 45), SH)
        return {**base, **frozen_identity(plan), 'execution_status': 'MISSED' if now >= deadline else 'PLANNED',
                'reason': 'REGISTERED_PRIMARY_DEADLINE_PASSED_WITHOUT_ATTEMPT' if now >= deadline else 'AWAITING_DAILY_PRIMARY',
                'status_source': 'READ_ONLY_REGISTERED_PLAN_RECONCILIATION', 'delivery_deadline': deadline.isoformat()}

    def daily_recommendation_report(self, *, target_session=None, run_id=None, as_of=None):
        from .daily_research import render_daily_report
        from .daily_research_outcomes import snapshot_available_as_of
        now = _instant(as_of) or self._daily_research_now()
        if bool(target_session) == bool(run_id):
            raise ValueError('choose exactly one target_session or saved run_id')
        delivery = self.daily_research_delivery(target_session, as_of=now) if target_session else None
        selected = delivery.get('run_id') if delivery else run_id
        snapshot = self.daily_recommendation_snapshot(selected) if selected else None
        if snapshot and not snapshot_available_as_of(snapshot, now):
            raise ValueError('snapshot was not available at report cutoff')
        published = self._daily_publication_time(snapshot) if snapshot else None
        if published and published > now:
            raise ValueError('snapshot was not published at report cutoff')
        summary = self.daily_recommendation_analysis(as_of=now)
        markdown = render_daily_report(snapshot, validation_summary=summary, delivery=delivery)
        return {'report_version': 'DAILY_RESEARCH_REPORT_V1', 'report_as_of': now.isoformat(),
                'delivery': delivery, 'snapshot': snapshot, 'history': summary, 'markdown': markdown,
                'market_edge': 'NOT_ESTABLISHED'}
