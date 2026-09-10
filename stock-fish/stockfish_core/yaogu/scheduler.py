"""In-process Shanghai exchange-day schedule. No fictitious catch-up captures."""
from __future__ import annotations

from datetime import date, datetime, time
import os
import json
from pathlib import Path
import threading
from uuid import uuid4
from zoneinfo import ZoneInfo

from ..common import utc_now, UnavailableError
from ..jobs import JobConflict
from ..storage import ImmutableConflict
from .contracts import digest
from .candidate_service import CandidateRequest

SH = ZoneInfo('Asia/Shanghai')


class CandidateScheduler:
    OBSERVATION_PLAN_VERSION = 'FORWARD_OBSERVATION_PLAN_R03_V3'
    SCHEDULER_STATE_SOURCE = 'ACTUAL_CANDIDATE_SCHEDULER_STATE_R12'
    MAX_OUTCOME_SUBMISSIONS_PER_TICK = 5
    def __init__(self, service, *, capture=None, clock=utc_now, interval=20):
        self.service, self.clock, self.interval = service, clock, interval
        existing_scheduler = getattr(service, 'scheduler', None)
        if existing_scheduler and existing_scheduler.thread and existing_scheduler.thread.is_alive():
            raise RuntimeError('CANDIDATE_SCHEDULER_ALREADY_RUNNING_FOR_SERVICE')
        self.started_at = clock()
        self.capture = capture or self._capture
        self.custom_capture = capture is not None
        self.service.scheduler = self
        self._stop = threading.Event()
        self.thread = None
        from ..observed_data import exchange_calendar_2026
        calendar = exchange_calendar_2026()
        self.sessions = tuple(sorted(calendar.sessions))
        source = Path(__file__).resolve().parents[1] / 'exchange_calendar_2026.json'
        self.calendar_evidence = json.loads(source.read_text(encoding='utf-8'))
        self.calendar_hash = digest(self.calendar_evidence)
        self.service.jobs.register('scheduled_candidate_capture', self._capture_job)
        self.plan()

    def _capture(self, output_dir, now, ctx=None):
        from .data_pipeline import MainboardPipeline
        config=self.service.config(ctx.payload['config_hash']) if ctx else self.service.current_config()
        pipeline = MainboardPipeline(self.service.root / 'provider_cache', service=self.service, config=config)
        def progress(value):
            if ctx:
                ctx.update(.05, '全主板采集：本次请求 %d，缓存 %d，待续采 %d' % (
                    value['network_requests_this_run'], value['cache_hits_this_run'], len(value['pending_request_ids'])))
        bundle = pipeline.capture(output_dir, now, checkpoint=ctx.check_cancelled if ctx else lambda: None, progress=progress)
        report = dict(pipeline.last_report)
        report['capture_job_id'] = ctx.job_id if ctx else None
        self.service.store.put_document('candidate_pipeline_attempts', 'pipeline_'+digest(report), report)
        return bundle

    def submit_capture(self):
        """A bounded manual/resume run; the server owns date, source and clocks."""
        now = self.clock()
        day = max((d for d in self.sessions if d <= now.astimezone(SH).date()), default=None)
        if day is None:
            raise ValueError('VERIFIED_CAPTURE_SESSION_UNAVAILABLE')
        return self.service.jobs.submit('scheduled_candidate_capture', {
            'scheduled_at': None, 'actual_requested_at': now.isoformat(),
            'market_session': 'ON_DEMAND_CAPTURE', 'session': day.isoformat(),
            'config_hash': self.service.current_config().config_hash}, job_id='capture_'+uuid4().hex)

    def plan(self):
        config = self.service.current_config()
        existing = [p for p in self.service._all('candidate_observation_plans') if p['strategy_id'] == config.strategy_id
                    and p.get('plan_version') == self.OBSERVATION_PLAN_VERSION and p.get('calendar_hash') == self.calendar_hash]
        if existing:
            return existing[-1]
        today = self.started_at.astimezone(SH).date()
        future = [s for s in self.sessions if s > today][:config.schedule.prospective_sessions]
        value = {'plan_id': 'prospective_' + digest({'strategy': config.config_hash, 'created': self.started_at.isoformat(), 'version': self.OBSERVATION_PLAN_VERSION}),
                 'plan_version': self.OBSERVATION_PLAN_VERSION,
                 'created_at': self.started_at.isoformat(), 'strategy_id': config.strategy_id, 'config_hash': config.config_hash,
                 'requested_sessions': config.schedule.prospective_sessions, 'sessions': [s.isoformat() for s in future],
                 'calendar_hash': self.calendar_hash, 'calendar_evidence': self.calendar_evidence,
                 'schedule': config.schedule.model_dump(mode='json'),
                 'status': 'PLANNED' if len(future) == config.schedule.prospective_sessions else 'CALENDAR_COVERAGE_PARTIAL',
                 'observations': 'FUTURE_NOT_OBSERVED', 'market_edge': 'NOT_ESTABLISHED',
                 'service_required': True, 'offline_policy': 'MISSED_NOT_BACKFILLED',
                 'outcome_schedule': {'stage': 'AFTER_CLOSE_WITH_CURRENT_REGISTERED_INPUT',
                    'lookback_exchange_sessions': config.schedule.prospective_sessions,
                    'horizons': list(config.evaluation.horizons),
                    'deduplication': ['run_id', 'evaluation_exchange_session', 'input_id'],
                    'missing_policy': 'NULL_CENSORED_OR_UNAVAILABLE_NEVER_SYNTHETIC_PRICE',
                    'max_submissions_per_tick': self.MAX_OUTCOME_SUBMISSIONS_PER_TICK,
                    'provider_scope': 'All pending published candidate IDs independently enter the durable result-data queue; missing results remain NULL.'}}
        self.service.store.put_document('candidate_observation_plans', value['plan_id'], value)
        return value

    def _next_session_after(self, day):
        later = [session for session in self.sessions if session > day]
        return later[0] if later else None

    def _primary_schedule_for(self, signal_day, config=None):
        from .validation_identity import current_validation_identity
        config = config or self.service.current_config()
        identity = current_validation_identity()
        hour, minute = map(int, config.schedule.evening.split(':'))
        scheduled = datetime.combine(signal_day, time(hour, minute), SH)
        return {
            'signal_session': signal_day.isoformat(),
            'target_session': self._next_session_after(signal_day).isoformat() if self._next_session_after(signal_day) else None,
            'scheduled_at': scheduled.isoformat(),
            'job_id': 'schedule_' + signal_day.isoformat() + '_EVENING_' + identity['validation_epoch_id'],
            'primary_signal_cutoff': 'SCHEDULED_TRIGGER_20_30_ACTUAL_DECISION_CUTOFF',
            **identity,
            'strategy_id': 'STAGE_AWARE_MOMENTUM_V2',
        }

    def ensure_primary_validation_schedule(self, now=None):
        now = now or self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('scheduler clock must be timezone-aware')
        local = now.astimezone(SH)
        config = self.service.current_config()
        hour, minute = map(int, config.schedule.evening.split(':'))
        today = local.date()
        if today in self.sessions:
            today_plan = self._primary_schedule_for(today, config)
            today_cutoff = datetime.fromisoformat(today_plan['scheduled_at'])
            today_event = self.service.store.get_document('candidate_schedule_events', today_plan['job_id'])
            today_job = self.service.jobs.get(today_plan['job_id'])
            if local <= today_cutoff or today_event is not None or today_job is not None:
                signal_day = today
            else:
                signal_day = self._next_session_after(today)
        else:
            signal_day = min((session for session in self.sessions if session > today), default=None)
        if signal_day is None:
            return {'status': 'CALENDAR_COVERAGE_EXHAUSTED', 'strategy_id': 'STAGE_AWARE_MOMENTUM_V2'}
        planned = self._primary_schedule_for(signal_day, config)
        doc_id = 'stage_aware_primary_schedule_' + signal_day.isoformat() + '_' + planned['validation_epoch_id']
        existing = self.service.store.get_document('stage_aware_prospective_validation_schedules', doc_id)
        if existing is None:
            value = {
                'schedule_id': doc_id,
                'schedule_version': 'STAGE_AWARE_MOMENTUM_V2_PRIMARY_SCHEDULE_R14_DAILY_FAST_PATH',
                'registered_at': now.isoformat(),
                'config_hash': config.config_hash,
                'calendar_hash': self.calendar_hash,
                'status': 'PERSISTENTLY_PLANNED_WAITING_FOR_CUTOFF',
                'offline_policy': 'MISSED_SERVICE_OFFLINE_NOT_BACKFILLED_AS_PROSPECTIVE',
                'capture_flow': [
                    'capture/update current MarketInput',
                    'validate cutoff',
                    'run Daily Primary Fast Path active STAGE_AWARE_MOMENTUM_V2',
                    'persist immutable strategy_experiment_run',
                    'persist compact eligible summaries, compressed detail documents, and Focus/Watch/Rejected/Unknown counts',
                    'register append-only tracking/outcome demand',
                ],
                **planned,
                'market_edge': 'NOT_ESTABLISHED',
            }
            self.service.store.put_document('stage_aware_prospective_validation_schedules', doc_id, value)
            existing = value
        epoch_id = planned['validation_epoch_id']
        if self.service.store.get_document('stage_aware_prospective_validation_epochs', epoch_id) is None:
            from .validation_identity import current_validation_identity
            self.service.store.put_document('stage_aware_prospective_validation_epochs', epoch_id, {
                'epoch_id': epoch_id, **current_validation_identity(),
                'status': 'REGISTERED_IMMUTABLE_IDENTITY',
                'registered_at': now.isoformat(), 'checkpoints_signal_sessions': [5,20,60,120],
                'market_edge': 'NOT_ESTABLISHED',
            })
        return existing

    def _latest_stage_aware_primary(self, signal_day):
        from .validation_identity import current_validation_identity
        epoch_id = current_validation_identity()['validation_epoch_id']
        rows = [
            row for row in self.service._all('stage_aware_prospective_primary_runs')
            if row.get('signal_observation_date') == signal_day.isoformat()
            and row.get('official_primary_signal_countable') is True
            and row.get('validation_epoch_id') == epoch_id
        ]
        return max(rows, key=lambda row: (row.get('decision_cutoff') or '', row.get('run_id') or ''), default=None)

    def describe_state(self, now=None):
        now = now or self.clock()
        plan = self.plan()
        primary = self.ensure_primary_validation_schedule(now)
        job_id = primary.get('job_id')
        job = self.service.jobs.get(job_id) if job_id else None
        event = self.service.store.get_document('candidate_schedule_events', job_id) if job_id else None
        signal_day = date.fromisoformat(primary['signal_session']) if primary.get('signal_session') else None
        primary_run = self._latest_stage_aware_primary(signal_day) if signal_day else None
        cutoff = datetime.fromisoformat(primary['scheduled_at']) if primary.get('scheduled_at') else None
        thread_alive = bool(self.thread and self.thread.is_alive())
        readiness_blockers = []
        if not thread_alive:
            readiness_blockers.append('SCHEDULER_THREAD_NOT_ALIVE')
        if not job_id:
            readiness_blockers.append('PRIMARY_SCHEDULE_NOT_REGISTERED')
        validation_state = self.service.validation_state(primary.get('validation_epoch_id'))
        latest_capture = validation_state.get('latest_primary_capture')
        if primary_run:
            status = 'PROSPECTIVE_PRIMARY_PERSISTED'
        elif latest_capture and latest_capture.get('signal_observation_date') == primary.get('signal_session'):
            status = 'PRIMARY_CAPTURE_PERSISTED_SIGNAL_DATA_UNAVAILABLE'
        elif event and event.get('status') == 'MISSED_SERVICE_OFFLINE':
            status = 'MISSED_SERVICE_OFFLINE_NOT_BACKFILLED'
        elif job:
            status = 'JOB_' + job['state']
        elif not thread_alive and cutoff:
            status = 'PLANNED_BUT_NO_LIVE_SCHEDULER'
        elif cutoff and now.astimezone(SH) < cutoff:
            status = 'READY_AND_RUNNING_WAITING_FOR_CUTOFF'
        elif cutoff:
            status = 'CUTOFF_REACHED_AWAITING_SCHEDULER_TICK'
        else:
            status = primary.get('status', 'UNKNOWN')
        if status in {'PROSPECTIVE_PRIMARY_PERSISTED', 'MISSED_SERVICE_OFFLINE_NOT_BACKFILLED'}:
            readiness_blockers = []
        epoch = validation_state['epoch']
        import psutil
        process = psutil.Process()
        return {
            'source': self.SCHEDULER_STATE_SOURCE,
            'observed_at': now.isoformat(),
            'status': status,
            'scheduler_class': type(self).__name__,
            'scheduler_started_at': self.started_at.isoformat(),
            'scheduler_thread_alive': thread_alive,
            'readiness_blockers': readiness_blockers,
            'service_identity': {
                'state_root': str(self.service.root),
                'db_path': str(self.service.store.path),
                'job_owner_id': self.service.jobs.owner_id,
                'process_id': os.getpid(),
                'process_creation_time': datetime.fromtimestamp(process.create_time(), SH).isoformat(),
                'repo_root': str(Path(__file__).resolve().parents[3]),
                'port': getattr(self.service, 'runtime_port', None),
                'scheduler_thread_name': self.thread.name if self.thread else None,
                'scheduler_thread_ident': self.thread.ident if self.thread else None,
                'scheduler_thread_native_id': self.thread.native_id if self.thread else None,
            },
            'active_candidate_strategy_id': self.service.current_config().strategy_id,
            'stage_aware_strategy_id': 'STAGE_AWARE_MOMENTUM_V2',
            'observation_plan': {k: plan.get(k) for k in ('plan_id','plan_version','status','requested_sessions','calendar_hash')},
            'primary_schedule': primary,
            'next_real_primary_capture': primary.get('scheduled_at'),
            'expected_target_session': primary.get('target_session'),
            'scheduler_job_id': job_id,
            'scheduler_job_state': {k: job.get(k) for k in ('job_id','kind','state','progress','created_at','started_at','finished_at','updated_at','result','error')} if job else None,
            'scheduler_event_state': event,
            'prospective_primary_run': primary_run,
            'validation_epoch': epoch,
            'validation_state': validation_state,
            'validation_epoch_id': validation_state['validation_epoch_id'],
            'primary_cutoff_policy_id': primary.get('primary_cutoff_policy_id'),
            'latest_primary_capture': latest_capture,
            'market_edge': 'NOT_ESTABLISHED',
        }

    def _event(self, event_id, value):
        self.service.store.put_document('candidate_schedule_events', event_id, value)

    def _latest_run(self,observation_date,*,cutoff,market_session=None):
        from .data_pipeline import published_runs
        candidates=[r for r in published_runs(self.service) if r.observation_date==observation_date
                    and r.generated_at<=cutoff and r.decision_cutoff<=cutoff
                    and (market_session is None or r.market_session==market_session)]
        return max(candidates,key=lambda r:(r.decision_cutoff,r.generated_at,r.revision,r.run_id),default=None)

    def tick(self, now=None):
        now = now or self.clock()
        if now.tzinfo is None:
            raise ValueError('scheduler clock must be timezone-aware')
        day = now.astimezone(SH).date()
        if day not in self.sessions:
            return []
        config = self.service.current_config()
        self.plan()
        self.ensure_primary_validation_schedule(now)
        schedule = config.schedule
        results = []
        for name, scheduled_clock in [('PRELIMINARY', schedule.preliminary), ('EVENING', schedule.evening), ('AUCTION_REVIEW', schedule.auction_review)]:
            hour, minute = map(int, scheduled_clock.split(':'))
            planned = datetime.combine(day, time(hour, minute), SH)
            if planned > now:
                continue
            primary_identity = self._primary_schedule_for(day, config) if name == 'EVENING' else None
            key = primary_identity['job_id'] if primary_identity else 'schedule_' + day.isoformat() + '_' + name
            if self.service.store.get_document('candidate_schedule_events', key) is not None or self.service.jobs.get(key) is not None:
                continue
            if planned < self.started_at:
                value = {'event_id': key, 'scheduled_at': planned.isoformat(), 'observed_at': now.isoformat(),
                         **({'validation_epoch_id': primary_identity['validation_epoch_id']} if primary_identity else {}),
                         'status': 'MISSED_SERVICE_OFFLINE', 'market_session': name, 'snapshot_created': False}
                self._event(key, value)
                results.append(value)
                continue
            if name == 'AUCTION_REVIEW':
                previous = max((d for d in self.sessions if d < day), default=None)
                prior = self._latest_run(previous,cutoff=now,market_session='EVENING')
                primary = self._latest_stage_aware_primary(previous) if previous else None
                if not prior or not primary:
                    value = {'event_id': key, 'scheduled_at': planned.isoformat(), 'observed_at': now.isoformat(),
                             'status': 'EVENING_PRIMARY_RUN_UNAVAILABLE', 'market_session': name, 'snapshot_created': False,
                             'prior_evening_candidate_run_available': bool(prior),
                             'prior_stage_aware_primary_run_available': bool(primary)}
                    self._event(key, value)
                    results.append(value)
                    continue
                available = self.service._all('auction_inputs')
                from .auction_input_selector import select_auction_inputs
                selected=select_auction_inputs(prior,available,now)
                snapshot_id=None
                if selected:
                    snapshot_id=self.service.register_auction(selected['quotes'],coverage_type=selected['coverage_type'],
                        receipts=selected['receipts'],sessions=selected['sessions'])
                    evidence={**selected['selection_evidence'],'selected_input_id':snapshot_id}
                    self.service.store.put_document('auction_input_selections','auction_selection_'+digest(evidence),evidence)
                # No live 09:25 adapter is silently replaced by a current quote.
                job = self.service.submit_auction(prior.run_id, snapshot_id, job_id=key)
            else:
                job = self.service.jobs.submit('scheduled_candidate_capture', {'scheduled_at': planned.isoformat(),
                     'actual_requested_at': now.isoformat(), 'market_session': name, 'session': day.isoformat(),
                     **({'validation_epoch_id': primary_identity['validation_epoch_id']} if primary_identity else {}),
                     'config_hash': config.config_hash}, job_id=key)
            from .validation_identity import current_validation_identity
            value = {'event_id': key, 'scheduled_at': planned.isoformat(), 'observed_at': now.isoformat(),
                     **({key: value for key, value in primary_identity.items() if key not in {'job_id', 'scheduled_at'}} if primary_identity else current_validation_identity()),
                     'status': 'SUBMITTED', 'market_session': name, 'job_id': job['job_id'], 'snapshot_created': False}
            self._event(key, value)
            results.append(value)
        # Observation jobs form a later independent stage. A capture is already
        # committed (or still running); observer failure cannot undo its success.
        results.extend(self.schedule_observations(now))
        results.extend(self.schedule_strategy_research(now))
        return results

    def schedule_observations(self, now=None):
        """Append factual outcomes from current registered inputs, never backdate.

        Current-day active inputs and successful scheduled diagnostic captures
        are both eligible evidence. Missing prices/statuses are evaluated by the
        outcome consumer, not replaced here. Each new input revision may append
        one new observation set for each saved run; the original run is untouched.
        """
        now = now or self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('observer clock must be timezone-aware')
        local = now.astimezone(SH)
        day = local.date()
        if day not in self.sessions or local.time().replace(tzinfo=None) < time(15):
            return []
        config = self.service.current_config()
        index = self.sessions.index(day)
        from .data_pipeline import published_outcome_demand, published_runs
        pending_ids = {v['run_id'] for v in published_outcome_demand(self.service, day, self.sessions)}
        runs = [r for r in published_runs(self.service) if r.run_id in pending_ids and r.generated_at <= now]
        if not runs:
            return []
        sources = {}
        try:
            iid, bundle = self.service.input()
            if bundle.observation_date == day:
                sources[iid] = bundle
        except UnavailableError:
            pass
        for result in self.service._all('candidate_capture_results'):
            cutoff = datetime.fromisoformat(result['actual_cutoff'])
            if cutoff > now or cutoff.astimezone(SH).date() != day:
                continue
            # A diagnostic's input is usable for honest outcome missingness even
            # when it did not qualify for default DAILY activation. Publication
            # authority still has to be intact and the capture job successful.
            captured_run = self.service.get_run(result['candidate_run_id'])
            if captured_run.observation_date != day:
                continue
            iid, bundle = self.service.input(result['input_id'])
            if bundle.observation_date == day:
                sources[iid] = bundle
        results, submitted = [], 0
        for saved in sorted(runs, key=lambda r:(r.observation_date,r.run_id)):
            if not sources:
                event_id = 'outcome_wait_' + digest({'run_id':saved.run_id,'day':day.isoformat()})
                if self.service.store.get_document('candidate_schedule_events',event_id) is None:
                    value = {'event_id':event_id,'run_id':saved.run_id,'evaluation_session':day.isoformat(),
                             'observed_at':now.isoformat(),'market_session':'OUTCOME_OBSERVATION',
                             'status':'NO_CURRENT_SESSION_REGISTERED_INPUT','snapshot_created':False,
                             'outcome_rows_created':False,'coverage':None}
                    self._event(event_id,value)
                    results.append(value)
                continue
            for input_id, bundle in sorted(sources.items()):
                job_id = 'observe_' + digest({'run_id':saved.run_id,'day':day.isoformat(),'input_id':input_id})
                if self.service.jobs.get(job_id) is not None or self.service.store.get_document('candidate_schedule_events',job_id) is not None:
                    continue
                if submitted >= self.MAX_OUTCOME_SUBMISSIONS_PER_TICK:
                    return results
                try:
                    job = self.service.submit_outcomes(saved.run_id,input_id,job_id=job_id)
                except JobConflict:
                    # Queue capacity is transient; do not mark the logical job
                    # delivered, and do not retry it inside a busy loop.
                    return results
                observed = {}
                for bar in bundle.bars:
                    if bar.session == day and bar.visible(now):
                        previous = observed.get(bar.instrument_id)
                        if previous is None or previous.ingested_at < bar.ingested_at:
                            observed[bar.instrument_id] = bar
                ids = {item.instrument_id for item in saved.items}
                present = sorted(i for i in ids if i in observed and observed[i].quality=='VALID' and observed[i].close is not None)
                value = {'event_id':job_id,'job_id':job['job_id'],'run_id':saved.run_id,'input_id':input_id,
                         'evaluation_session':day.isoformat(),'observed_at':now.isoformat(),
                         'market_session':'OUTCOME_OBSERVATION','status':'SUBMITTED','snapshot_created':False,
                         'coverage_type':bundle.coverage_type,
                         'coverage':{'candidate_count':len(ids),'current_close_present_count':len(present),
                             'current_close_missing_ids':sorted(ids-set(present)),
                             'scope':'CURRENT_RAW_CLOSE_PRESENCE_ONLY; outcome consumer separately validates states, sources, comparable prices and each horizon'},
                         'calendar_hash':self.calendar_hash,'lookback_exchange_sessions':config.schedule.prospective_sessions,
                         'backdated_capture':False,'reference_method':'MARKED_PRICES_NOT_ASSUMED_FILLS'}
                self._event(job_id,value)
                results.append(value)
                submitted += 1
        return results

    def schedule_strategy_research(self, now=None):
        now = now or self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('observer clock must be timezone-aware')
        local = now.astimezone(SH)
        day = local.date()
        if day not in self.sessions or local.time().replace(tzinfo=None) < time(15):
            return []
        try:
            input_id, bundle = self.service.strategy_input()
        except UnavailableError:
            return []
        # A weekend refresh can still advance the latest completed session.
        rows = self.service.pending_strategy_research()
        submitted, results = 0, []
        for row in rows:
            observation = date.fromisoformat(str(row['observation_date'])[:10])
            if observation > bundle.observation_date:
                continue
            run_id = row['run_id']
            kind = 'STRATEGY_RESEARCH_TRACKING'
            base_id = kind.lower() + '_' + digest({'run_id':run_id,'day':day.isoformat(),'input_id':input_id})
            job_id, attempt = base_id, 0
            while self.service.jobs.get(job_id) is not None:
                old = self.service.jobs.get(job_id)
                if old['state'] not in {'FAILED','CANCELLED','INTERRUPTED'}:
                    break
                old_event = self.service.store.get_document('candidate_schedule_events',job_id) or {}
                last = datetime.fromisoformat(old_event['observed_at']) if old_event.get('observed_at') else now
                if (now-last).total_seconds() < min(3600, 60 * 2**min(attempt,6)):
                    break
                attempt += 1
                job_id = base_id + '_retry_' + str(attempt)
            if self.service.jobs.get(job_id) is not None:
                continue
            if submitted >= self.MAX_OUTCOME_SUBMISSIONS_PER_TICK:
                return results
            try:
                job = self.service.submit_strategy_tracking(run_id, input_id, horizons=row['horizons'], job_id=job_id)
            except JobConflict:
                return results
            value = {'event_id':job_id,'job_id':job['job_id'],'run_id':run_id,'input_id':input_id,
                     'evaluation_session':day.isoformat(),'observed_at':now.isoformat(),
                     'market_session':kind,'status':'SUBMITTED','snapshot_created':False,
                     'coverage_type':bundle.coverage_type,'attempt':attempt,
                     'research_horizons':[1,2,3,4,5,10,20],
                     'dependency_order':'PRICE_AND_EVENT_LABELS_THEN_TRACKING_THEN_FEEDBACK',
                     'missing_policy':'NULL_CENSORED_OR_UNAVAILABLE_NEVER_SYNTHETIC_PRICE',
                     'market_edge':'NOT_ESTABLISHED'}
            self._event(job_id,value)
            results.append(value)
            submitted += 1
        return results

    def _capture_job(self, ctx):
        payload = ctx.payload
        now = self.clock()
        from .validation_identity import current_validation_identity
        expected_epoch = payload.get('validation_epoch_id')
        if payload.get('market_session') == 'EVENING' and expected_epoch != current_validation_identity()['validation_epoch_id']:
            raise ValueError('SCHEDULED_VALIDATION_EPOCH_CHANGED_BEFORE_CAPTURE')
        ctx.update(.05, '按真实接收时间采集已允许的公开数据；不回填旧时点')
        archive = self.service.root / 'raw_captures' / ctx.job_id
        bundle = self.capture(archive, now) if self.custom_capture else self._capture(archive, now, ctx)
        capture_completed_at = self.clock()
        ctx.check_cancelled()
        if bundle.observation_date.isoformat() != payload['session']:
            raise ValueError('captured market date does not match scheduled session')
        input_id = self.service.register_input(bundle, activate=False)
        actual_cutoff = self.clock()
        if expected_epoch and expected_epoch != current_validation_identity()['validation_epoch_id']:
            raise ValueError('SCHEDULED_VALIDATION_EPOCH_CHANGED_DURING_CAPTURE')
        scheduled = datetime.fromisoformat(payload['scheduled_at']) if payload.get('scheduled_at') else None
        if scheduled and now < scheduled:
            raise ValueError('PRIMARY_CAPTURE_BEFORE_SCHEDULED_TRIGGER')
        timing = {'scheduled_at': payload.get('scheduled_at'),
            'capture_started_at': now.isoformat(), 'capture_completed_at': capture_completed_at.isoformat(),
            'actual_decision_cutoff': actual_cutoff.isoformat(),
            'capture_latency_seconds': (capture_completed_at-now).total_seconds(),
            'start_latency_seconds': (now-scheduled).total_seconds() if scheduled else None,
            'decision_latency_seconds': (actual_cutoff-scheduled).total_seconds() if scheduled else None,
            'scheduled_validation_epoch_id': expected_epoch,
            'primary_cutoff_policy_id': 'SCHEDULED_2030_ACTUAL_COMPLETION'}
        parent = self._latest_run(bundle.observation_date,cutoff=actual_cutoff)
        config = self.service.config(payload['config_hash'])
        candidate_payload = {'input_id': input_id, 'config_hash': config.config_hash,
            'profile': 'FUNDS_V1' if config.funds_primary else 'DAILY_V1', 'decision_cutoff': actual_cutoff.isoformat(),
            'market_session': payload['market_session'], 'parent_run_id': parent.run_id if parent else None,
            'revision': parent.revision + 1 if parent else 1, 'capture_timing': timing}
        activation = {'input_id': input_id, 'observation_date': bundle.observation_date.isoformat(),
                      'activated_at': actual_cutoff.isoformat(), 'coverage_type': bundle.coverage_type}
        # Raw files may have been prepared already. Activation, candidate authority
        # and successful capture job share this one DB transaction.
        return self.service._candidate_job(ctx, candidate_payload, activation=activation)

    def start(self):
        if self.thread is not None:
            return
        def loop():
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception as exc:
                    # Retain a bounded safe diagnostic; no request payload or key.
                    value = {'observed_at': self.clock().isoformat(), 'error_type': type(exc).__name__, 'status': 'SCHEDULER_FAILED'}
                    try:
                        self._event('schedule_error_' + digest(value), value)
                    except Exception:
                        pass
                self._stop.wait(self.interval)
        self.thread = threading.Thread(target=loop, name='stockfish-candidate-schedule', daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()
        if self.thread is not None:
            self.thread.join(timeout=min(10, self.interval + 1))
