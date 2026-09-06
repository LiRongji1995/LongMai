"""In-process Shanghai exchange-day schedule. No fictitious catch-up captures."""
from __future__ import annotations

from datetime import date, datetime, time
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
    MAX_OUTCOME_SUBMISSIONS_PER_TICK = 5
    def __init__(self, service, *, capture=None, clock=utc_now, interval=20):
        self.service, self.clock, self.interval = service, clock, interval
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
        schedule = config.schedule
        results = []
        for name, scheduled_clock in [('PRELIMINARY', schedule.preliminary), ('EVENING', schedule.evening), ('AUCTION_REVIEW', schedule.auction_review)]:
            hour, minute = map(int, scheduled_clock.split(':'))
            planned = datetime.combine(day, time(hour, minute), SH)
            if planned > now:
                continue
            key = 'schedule_' + day.isoformat() + '_' + name
            if self.service.store.get_document('candidate_schedule_events', key) is not None or self.service.jobs.get(key) is not None:
                continue
            if planned < self.started_at:
                value = {'event_id': key, 'scheduled_at': planned.isoformat(), 'observed_at': now.isoformat(),
                         'status': 'MISSED_SERVICE_OFFLINE', 'market_session': name, 'snapshot_created': False}
                self._event(key, value)
                results.append(value)
                continue
            if name == 'AUCTION_REVIEW':
                previous = max((d for d in self.sessions if d < day), default=None)
                prior = self._latest_run(previous,cutoff=now,market_session='EVENING')
                if not prior:
                    value = {'event_id': key, 'scheduled_at': planned.isoformat(), 'observed_at': now.isoformat(),
                             'status': 'EVENING_RUN_UNAVAILABLE', 'market_session': name, 'snapshot_created': False}
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
                     'config_hash': config.config_hash}, job_id=key)
            value = {'event_id': key, 'scheduled_at': planned.isoformat(), 'observed_at': now.isoformat(),
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
        if bundle.observation_date != day:
            return []
        rows = self.service.store.list_document_metadata('strategy_experiment_runs', limit=100, descending=True)
        submitted, results = 0, []
        for row in rows:
            run = self.service.strategy_experiment(row['document_id'])
            observation = date.fromisoformat(str(run['observation_date'])[:10])
            if observation >= day:
                continue
            for kind, submitter in (
                ('STRATEGY_OUTCOME_OBSERVATION', self.service.submit_strategy_outcomes),
                ('STRATEGY_RESEARCH_TRACKING', self.service.submit_strategy_tracking),
            ):
                job_id = kind.lower() + '_' + digest({'run_id':run['run_id'],'day':day.isoformat(),'input_id':input_id})
                if self.service.jobs.get(job_id) is not None or self.service.store.get_document('candidate_schedule_events',job_id) is not None:
                    continue
                if submitted >= self.MAX_OUTCOME_SUBMISSIONS_PER_TICK:
                    return results
                try:
                    job = submitter(run['run_id'], input_id, job_id=job_id)
                except JobConflict:
                    return results
                value = {'event_id':job_id,'job_id':job['job_id'],'run_id':run['run_id'],'input_id':input_id,
                         'evaluation_session':day.isoformat(),'observed_at':now.isoformat(),
                         'market_session':kind,'status':'SUBMITTED','snapshot_created':False,
                         'coverage_type':bundle.coverage_type,
                         'research_horizons':[1,2,3,4,5,10,20],
                         'missing_policy':'NULL_CENSORED_OR_UNAVAILABLE_NEVER_SYNTHETIC_PRICE',
                         'market_edge':'NOT_ESTABLISHED'}
                self._event(job_id,value)
                results.append(value)
                submitted += 1
        return results

    def _capture_job(self, ctx):
        payload = ctx.payload
        now = self.clock()
        ctx.update(.05, '按真实接收时间采集已允许的公开数据；不回填旧时点')
        archive = self.service.root / 'raw_captures' / ctx.job_id
        bundle = self.capture(archive, now) if self.custom_capture else self._capture(archive, now, ctx)
        ctx.check_cancelled()
        if bundle.observation_date.isoformat() != payload['session']:
            raise ValueError('captured market date does not match scheduled session')
        input_id = self.service.register_input(bundle, activate=False)
        actual_cutoff = self.clock()
        parent = self._latest_run(bundle.observation_date,cutoff=actual_cutoff)
        config = self.service.config(payload['config_hash'])
        candidate_payload = {'input_id': input_id, 'config_hash': config.config_hash,
            'profile': 'FUNDS_V1' if config.funds_primary else 'DAILY_V1', 'decision_cutoff': actual_cutoff.isoformat(),
            'market_session': payload['market_session'], 'parent_run_id': parent.run_id if parent else None,
            'revision': parent.revision + 1 if parent else 1}
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
