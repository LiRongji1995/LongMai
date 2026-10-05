"""Mainboard discovery DAG shared by manual capture and the default scheduler.

Every directory member receives a structure-history request before scoring.
Only the additional expensive history is narrowed to structural candidates and
their frozen theme peers. Published observations are an independent input set.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .contracts import MarketInput, digest
from .daily_primary_fast_decision import evaluate_fast_decision_sufficiency
from .request_queue import RequestQueue, RequestBudget, capture_priority
from .provider_requests import execute_request, FIELD_VERSION

PIPELINE_VERSION = 'MAINBOARD_DATA_DAG_R12_FULL_MARKET_COMPARABLE_PRIORITY_V1'
CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL = 'DAILY_PRIMARY_CRITICAL'
CAPTURE_PURPOSE_BACKGROUND_RESEARCH = 'BACKGROUND_RESEARCH'
CRITICAL_CAPTURE_STAGES = {
    'MainBoardMasterCapture',
    'ExchangeCalendarCapture',
    'CurrentEligibilityStatusCapture',
    'CurrentDailyPriceCapture',
    'MarketBreadthCapture',
    'MarketBreadthRegisteredAlternative',
    'StructureHistoryCapture',
    'HistoricalSecurityReplayCapture',
}
BACKGROUND_RESEARCH_STAGES = {
    'PublishedCandidateOutcomeCapture',
    'MarketBreadthLimitEvidence',
    'MarketBreadthDirectLimitEvidence',
    'CurrentThemeCapture',
    'TushareOptionalEnrichment',
    'CandidateAndPeerHistoryCapture',
    'OptionalFundsCapture',
}
SH = ZoneInfo('Asia/Shanghai')


class _FastDecisionCaptureComplete(Exception):
    def __init__(self, bundle):
        super().__init__('DAILY_PRIMARY_FAST_DECISION_CAPTURE_COMPLETE')
        self.bundle = bundle


def capture_stage_purpose(stage):
    return (CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL
            if stage in CRITICAL_CAPTURE_STAGES
            else CAPTURE_PURPOSE_BACKGROUND_RESEARCH)


def _task_boundary_summary(queue):
    def summarize(patterns, stages):
        where = ' OR '.join('payload LIKE ?' for _ in patterns)
        params = ['%' + pattern + '%' for pattern in patterns]
        rows = list(queue.connection.execute(
            'SELECT state, COUNT(*) AS count FROM requests WHERE ' + where + ' GROUP BY state',
            params))
        counts = {state: 0 for state in ('PENDING', 'RUNNING', 'COMPLETE', 'PARTIAL', 'BLOCKED')}
        for row in rows:
            if row['state'] in counts:
                counts[row['state']] = row['count']
        return {'requests': sum(counts.values()), 'states': counts,
                'pending_count': sum(count for state, count in counts.items() if state != 'COMPLETE'),
                'stages': sorted(stages)}
    critical_patterns = [CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL, *CRITICAL_CAPTURE_STAGES]
    background_patterns = [CAPTURE_PURPOSE_BACKGROUND_RESEARCH, *BACKGROUND_RESEARCH_STAGES]
    critical = summarize(critical_patterns, CRITICAL_CAPTURE_STAGES)
    background = summarize(background_patterns, BACKGROUND_RESEARCH_STAGES)
    return {CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL: critical,
            CAPTURE_PURPOSE_BACKGROUND_RESEARCH: background}


def _write_once(path, value):
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding='utf-8') != content:
            raise ValueError('IMMUTABLE_PIPELINE_ARTIFACT_CONFLICT')
    else:
        path.write_text(content, encoding='utf-8')


def master_ids(results):
    """Identity comes from the exchange directory and its explicit board field.

    Code syntax formats the identifier only; it does not decide the board.
    """
    ids = set()
    for result in results:
        endpoint = result.get('endpoint')
        if endpoint not in ('mainboard_sse', 'master_szse'):
            continue
        for row in result.get('rows', ()):
            if endpoint == 'master_szse' and row.get('板块', row.get('所属板块', row.get('board'))) not in ('主板', 'MAIN'):
                continue
            code = row.get('证券代码') or row.get('A股代码') or row.get('代码') or row.get('instrument_id')
            if code is None:
                continue
            code = str(code)
            if code.endswith(('.SH', '.SZ')):
                iid = code
            else:
                iid = code.zfill(6) + ('.SH' if endpoint == 'mainboard_sse' else '.SZ')
            ids.add(iid)
    return sorted(ids)


def published_runs(service):
    if hasattr(service, '_all'):
        return [service.get_run(row['run_id']) for row in service._all('candidate_runs')]
    return service.list_runs(10000)


def published_outcome_demand(service, observation_date, sessions):
    """Read authoritative publications, including names no longer in any pool.

    A horizon missing its actual result remains pending after maturity. This
    intentionally retains suspended/delisted/losing securities for censoring.
    """
    if service is None:
        return []
    demand = []
    for run in published_runs(service):
        if run.observation_date >= observation_date:
            continue
        later = [s for s in sessions if run.observation_date < s <= observation_date]
        if not later:
            continue
        saved = service.outcomes(run.run_id)
        for item in run.items:
            pending={}
            for horizon in (1,2,3,5,10):
                records=[r for r in saved if r.get('instrument_id')==item.instrument_id and r.get('horizon')==horizon]
                choices=[]
                for record in records:
                    reasons=set(record.get('reason_codes',()))-{'ONE_PRICE_EXECUTION_UNCONFIRMED'}
                    states=record.get('session_states',())
                    # Confirmed suspension/delisting censors price references
                    # permanently; execution warnings do not make them gaps.
                    if (record.get('status')=='CENSORED' and record.get('label_end') is not None
                            and reasons and reasons <= {'SUSPENDED','DELISTED'} and len(states)==horizon
                            and all(s.get('status') in {'OBSERVED','SUSPENDED','DELISTED'} for s in states)):
                        # Price metrics remain permanently censored, while a
                        # separate missing event on a traded session can still
                        # become known. Do not discard that metric's demand.
                        choices.append(['session_states.'+str(s.get('session',index))+'.'+field
                            for index,s in enumerate(states) if s.get('status')=='OBSERVED'
                            for field in ('close_limit_up','close_limit_down','one_price_up') if s.get(field) is None])
                        continue
                    if record.get('status')=='NOT_MATURED':
                        choices.append(['MATURITY_NOT_REACHED']);continue
                    fields=[]
                    if record.get('label_end') is None:fields.append('label_end')
                    if len(states)!=horizon:fields.append('session_states')
                    metrics=record.get('metrics',{})
                    for field in ('close_reference_change','next_open_gap','next_open_reference_change',
                                  'mfe_bound','mae_bound','close_max_drawdown','any_close_limit_up',
                                  'any_close_limit_down','one_price_up_count'):
                        if metrics.get(field) is None:fields.append('metrics.'+field)
                    family=record.get('family') or getattr(item,'primary_family',None)
                    label_days=1 if family in {'ONE_TO_TWO','TWO_TO_THREE','HIGH_BOARD'} else 3
                    if horizon>=label_days and record.get('structure_success') is None:fields.append('structure_success')
                    if horizon>=3 and record.get('future_three_session_limit_up') is None:fields.append('future_three_session_limit_up')
                    if record.get('status')!='MATURE':
                        fields.extend('reason_codes.'+reason for reason in sorted(reasons))
                        if not fields:fields.append('NON_TERMINAL_OBSERVATION_STATUS')
                    choices.append(fields)
                if not any(not fields for fields in choices):
                    pending[str(horizon)]=min(choices,key=lambda fields:(len(fields),fields)) if choices else ['OBSERVATION_NOT_RECORDED']
            horizons=[int(h) for h in pending]
            if horizons:
                demand.append({'run_id': run.run_id, 'instrument_id': item.instrument_id,
                               'observation_date': str(run.observation_date), 'horizons': horizons,
                               'pending_fields':pending,
                               'start_date': str(run.observation_date), 'end_date': str(observation_date),
                               'selection_reason': 'AUTHORITATIVE_PUBLISHED_CANDIDATE_PENDING_OUTCOME'})
    if hasattr(service,'experiment_outcome_demand'):
        for item in service.experiment_outcome_demand(observation_date,sessions):
            demand.append({**item,'start_date':str(item.get('start_date') or item.get('required_start') or item['observation_date']),
                'end_date':str(item.get('end_date') or item.get('required_end') or observation_date),
                'selection_reason':item.get('selection_reason') or item.get('reason','EXPERIMENT_PENDING_OUTCOME_OR_EPISODE')})
    return demand


def stage_aware_history_sessions_required(*, planned_replay_signal_sessions=60):
    """History needed before a V2 signal can honestly evaluate long trend.

    This is data readiness math, not parameter search: MA250 plus slope lookback
    are warm-up requirements, and the replay signal range is a bounded reporting
    target.
    """
    try:
        import yaml
        config_path = Path(__file__).resolve().parents[2] / 'config' / 'stage_aware_momentum_v2.yaml'
        config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
        seed = config['research_seed_parameters']
        ma_required = max(seed['trend_state']['ma_windows']) + int(seed['trend_state']['slope_lookback_sessions'])
        momentum_required = int(seed['strength_momentum']['equal_window_sessions']) * 2
        price_required = max(seed['price_strength_windows'])
        supply_required = int(seed['supply_pressure']['recent_window_sessions_including_T'])
        return max(ma_required, momentum_required, price_required, supply_required) + planned_replay_signal_sessions
    except Exception:
        return 315


def tushare_request_plan(day, sessions):
    """Registered optional enrichment; no token is ever a PV dependency."""
    selected=[s for s in sessions if s<=day][-22:]
    plan=[('tushare_stock_basic',{'list_status':'L'}),
          ('tushare_trade_cal',{'start_date':str(sessions[0]),'end_date':str(day),'is_open':'1'})]
    for session in selected:
        plan.extend((endpoint,{'trade_date':str(session)}) for endpoint in
                    ('tushare_daily','tushare_adj_factor','tushare_daily_basic','tushare_stk_limit','tushare_stock_st'))
    plan.extend((endpoint,{'trade_date':str(day)}) for endpoint in
                ('tushare_limit_list_d','tushare_top_list','tushare_top_inst','tushare_moneyflow',
                 'tushare_dc_member','tushare_stk_auction','tushare_stk_auction_o'))
    plan.append(('tushare_dc_index',{'trade_date':str(day),'idx_type':'概念板块'}))
    plan.append(('tushare_suspend_d',{'trade_date':str(day)}))
    return plan


class MainboardPipeline:
    def __init__(self, root, *, service=None, config=None, executor=execute_request, normalizer=None, budget=None,
                 history_store=None, history_mode=None, history_coverage_plan=None):
        self.root, self.service, self.executor = Path(root), service, executor
        self.normalizer = normalizer
        import os
        self.history_mode = history_mode or os.environ.get('STOCKFISH_CAPTURE_HISTORY_MODE', 'ALL_MARKET_DAILY')
        if self.history_mode not in {'ALL_MARKET_DAILY', 'PER_SECURITY_RAW'}:
            raise ValueError('UNREGISTERED_HISTORY_CAPTURE_MODE')
        self.config = config if config is not None else service.current_config() if service else None
        self.budget = budget if budget is not None else RequestBudget.from_environment()
        self.history_store = history_store
        self.history_coverage_plan = history_coverage_plan
        if self.history_store is None and service is not None and hasattr(service, 'store'):
            from .history_artifacts import HistoryArtifactStore
            self.history_store = HistoryArtifactStore(service.store)

    def _latest_prior_history_artifact(self, day, *, cutoff=None):
        if self.service is None or not hasattr(self.service, 'store'):
            return None
        from .history_base_selection import select_prior_history_base
        allowed = {'baostock','tushare'}
        if getattr(self.service,'allow_test_fixture',False):
            allowed.add('TEST_FIXTURE')
        return select_prior_history_base(self.service.store,observation_date=day,
            cutoff=cutoff or datetime.now(SH),allowed_price_providers=allowed)

    def _validate_history_base_for_results(self, selected, results, day, cutoff):
        if selected is None:
            return None
        from .history_base_selection import validate_history_base
        raw = self.service.store.get_document_bytes('candidate_inputs', selected['input_id'],
            expected_hash=selected['input_id'].removeprefix('market_'))
        if raw is None:
            raise ValueError('SELECTED_HISTORY_BASE_INPUT_MISSING')
        base = MarketInput.model_validate_json(raw)
        if base.history_artifact_ref != selected['history_artifact_ref'] or base.history_artifact_hash != selected['history_artifact_hash']:
            raise ValueError('SELECTED_HISTORY_BASE_IDENTITY_CHANGED')
        # Selection already verified the immutable partition descriptors.  The
        # exact actual captured schemas must pass again at each normalization.
        proof = validate_history_base(base, self.history_store, observation_date=day, cutoff=cutoff,
            allowed_price_providers=set(selected['price_providers']), verify_partitions=False,
            incoming_price_receipts=results)
        proof['partition_integrity'] = selected['partition_integrity']
        return {**selected, **proof}

    def capture(self, output_dir, now, *, observation_date=None, purpose=CAPTURE_PURPOSE_BACKGROUND_RESEARCH,
                checkpoint=lambda: None, progress=lambda _: None, fast_decision=None):
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('REAL_AWARE_CAPTURE_CLOCK_REQUIRED')
        if purpose not in {CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL, CAPTURE_PURPOSE_BACKGROUND_RESEARCH}:
            raise ValueError('UNREGISTERED_CAPTURE_PURPOSE')
        if fast_decision is None:
            import os
            configured = os.environ.get('STOCKFISH_CAPTURE_FAST_DECISION', 'true').lower()
            if configured not in {'true', 'false'}:
                raise ValueError('INVALID_CAPTURE_FAST_DECISION')
            fast_decision = configured == 'true'
        if not isinstance(fast_decision, bool):
            raise ValueError('INVALID_CAPTURE_FAST_DECISION')
        critical_only = purpose == CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL
        if self.history_coverage_plan is not None and not critical_only:
            raise ValueError('GO_HISTORY_COVERAGE_ONLY_DAILY_PRIMARY_CRITICAL')
        per_security_history = critical_only and self.history_mode == 'PER_SECURITY_RAW'
        history_endpoint = 'history' if per_security_history else 'market_daily'
        history_granularity = ('TEN_CURRENT_DIRECTORY_SECURITIES_PER_REQUEST_FULL_REQUIRED_WINDOW'
                               if per_security_history else 'ONE_REQUEST_PER_EXCHANGE_SESSION_ALL_MARKET_ROWS')
        history_policy = ('ALL_CURRENT_DIRECTORY_RAW_HISTORY_PLUS_ADJUSTMENT_FACTORS_V1'
                          if per_security_history else 'ALL_MARKET_DAILY_BY_EXCHANGE_SESSION_PLUS_ALL_DIRECTORY_ADJUSTMENT_FACTORS')
        from ..observed_data import exchange_calendar_2026
        local = now.astimezone(SH)
        registered = tuple(sorted(exchange_calendar_2026().sessions))
        # A manual weekend run requests the latest actual exchange session.
        day = observation_date or max((s for s in registered if s <= local.date()), default=None)
        if day is None or day > local.date() or day not in registered:
            raise ValueError('VERIFIED_OBSERVATION_SESSION_REQUIRED')
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        epoch = str(day)
        started = datetime.now(SH)
        stages = []
        fast_decision_checkpoint = None
        with RequestQueue(self.root, budget=self.budget) as queue:
            def add(endpoint, parameters, stage, *, current=False, purpose=None):
                priority = capture_priority(endpoint)
                if endpoint == 'market_daily' and parameters.get('date') == str(day): priority = 0
                if endpoint == 'master_all' and parameters.get('date') != str(day): priority = 2
                if endpoint == 'stock_basic_all': priority = 2
                task_purpose = purpose or capture_stage_purpose(stage)
                return queue.enqueue(endpoint, parameters, stage=stage, definition_version=FIELD_VERSION,
                                     cache_epoch=epoch if current else None, priority=priority,
                                     purpose=task_purpose)

            def drain(stage, ids):
                before = queue.calls;http_before=queue.transport_counts().get('HTTP',0)
                def drain_progress(value):
                    progress(value)
                    if fast_decision_checkpoint is not None:
                        fast_decision_checkpoint(stage, value)
                queue.drain(self.executor, ids=ids, checkpoint=checkpoint, progress=drain_progress)
                rows = queue.rows(ids)
                stages.append({'stage': stage, 'request_ids': list(ids), 'requested': len(rows),
                               'complete': sum(r['state'] == 'COMPLETE' for r in rows),
                               'task_attempts': queue.calls-before,
                               'http_attempts':queue.transport_counts().get('HTTP',0)-http_before,
                               'status': 'COMPLETE' if rows and all(r['state']=='COMPLETE' for r in rows) else 'PARTIAL'})
                if fast_decision_checkpoint is not None:
                    fast_decision_checkpoint(stage, queue.summary())

            masters = [add(ep, {}, 'MainBoardMasterCapture', current=True) for ep in ('mainboard_sse', 'master_szse')]
            calendar_id = add('calendar', {'start_date': str(day-timedelta(days=800)), 'end_date': str(day)}, 'ExchangeCalendarCapture')
            drain('MainBoardMasterCapture', masters + [calendar_id])
            initial = queue.results()
            ids = master_ids(initial)
            calendar_rows = [row for r in initial if r.get('endpoint')=='calendar' and r.get('status')=='COMPLETE'
                             for row in r.get('rows', ()) if str(row.get('is_trading_day', row.get('isTradingDay', '1'))) == '1']
            sessions = tuple(sorted({date.fromisoformat(str(r.get('calendar_date', r.get('calendarDate', r.get('session'))))) for r in calendar_rows}))
            sessions = tuple(s for s in sessions if s <= day)
            # Registered current-year fallback remains explicit. It cannot
            # manufacture the missing older exchange sessions required by G.
            calendar_source = 'CAPTURED_PROVIDER_CALENDAR' if sessions and sessions[-1] == day else 'REGISTERED_2026_FALLBACK'
            if calendar_source != 'CAPTURED_PROVIDER_CALENDAR':
                sessions = tuple(s for s in registered if s <= day)
            stage_aware_history_sessions = stage_aware_history_sessions_required()
            if self.history_coverage_plan is not None:
                self.history_coverage_plan.prepare(ids, sessions, day, initial)
            history_start = sessions[max(0, len(sessions)-stage_aware_history_sessions)]
            structure_start = sessions[max(0, len(sessions)-22)]
            v2_long_history_start = history_start
            demand = [] if critical_only else published_outcome_demand(self.service, day, sessions)
            history_base = self._latest_prior_history_artifact(day,cutoff=datetime.now(SH)) if self.history_store is not None else None
            missing_listing=[]
            for r in initial:
                if r.get('endpoint') not in ('mainboard_sse','master_szse'):
                    continue
                for row in r.get('rows', ()):
                    parsed=master_ids([{'endpoint':r['endpoint'],'rows':[row]}])
                    if not parsed:
                        continue
                    listing=row.get('上市日期') or row.get('A股上市日期')
                    try:date.fromisoformat(str(listing)[:10])
                    except (TypeError,ValueError):missing_listing.extend(parsed)
            missing_listing=sorted(set(missing_listing))
            plan = {'pipeline_version': PIPELINE_VERSION, 'captured_at': now.isoformat(), 'observation_date': str(day),
                    'master_instrument_ids': ids, 'master_observed_count': len(ids),
                    'minimum_structure_sessions': 22, 'scoring_history_sessions': stage_aware_history_sessions,
                    'v2_long_history_start': str(v2_long_history_start),
                    'scoring_history_basis': 'STAGE_AWARE_V2_MA250_PLUS_SLOPE_WARMUP_PLUS_60_SIGNAL_REPLAY_SESSIONS',
                    'v2_long_history_universe_policy': 'FULL_CURRENT_MAINBOARD_DIRECTORY_BEFORE_DAILY_PREVIEW_OR_STAGE_SCORING',
                    'v2_long_history_data_policy': history_policy,
                    'history_capture_mode': 'PER_SECURITY_RAW' if per_security_history else 'ALL_MARKET_DAILY',
                    'calendar_source': calendar_source, 'calendar_sessions': len(sessions),
                    'history_append_base': history_base,
                    'verified_go_history_reuse': (self.history_coverage_plan.audit()
                                                  if self.history_coverage_plan is not None else None),
                    'initial_estimate': {'full_pool_structure_members': len(ids),
                        'full_pool_raw_daily_requests': (len(ids)+9)//10 if per_security_history else sum(v2_long_history_start <= s <= day for s in sessions),
                        'historical_security_master_requests': 0 if per_security_history else sum(v2_long_history_start <= s <= day for s in sessions),
                        'historical_security_basic_all_requests': 0 if per_security_history else 1,
                        'current_direct_limit_batches': (len(ids)+19)//20,
                        'full_pool_adjustment_factor_batches': (len(ids)+99)//100,
                        'basic_directory_batches': (len(missing_listing)+99)//100, 'published_outcome_objects': len(demand),
                        'candidate_history_requests': 'DETERMINED_AFTER_ALL_POOL_STRUCTURE_DISCOVERY',
                        'theme_member_requests': 'DETERMINED_BY_REGISTERED_PROVIDER_THEME_DIRECTORY'},
                    'budget': vars(self.budget), 'published_outcome_demand': demand,
                    'capture_purpose': purpose,
                    'fast_decision_enabled': fast_decision,
                    'task_classification': {
                        'classification_version': 'DAILY_PRIMARY_CRITICAL_CAPTURE_BOUNDARY_V1',
                        'critical_purpose': CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL,
                        'background_purpose': CAPTURE_PURPOSE_BACKGROUND_RESEARCH,
                        'critical_stages': sorted(CRITICAL_CAPTURE_STAGES),
                        'background_stages': sorted(BACKGROUND_RESEARCH_STAGES),
                        'critical_only': critical_only,
                    },
                    'background_research_deferred': critical_only,
                    'background_research_deferred_stages': sorted(BACKGROUND_RESEARCH_STAGES) if critical_only else [],
                    'optional_tushare_requests':[{'endpoint':ep,'parameters':params} for ep,params in tushare_request_plan(day,sessions)],
                    'scope_policy': 'ALL_DIRECTORY_MEMBERS_BEFORE_SCORE_NO_LIMIT_UP_POOL_OR_CODE_QUOTA'}
            _write_once(output / 'plan.json', plan)
            basics = [add('stock_basic', {'codes': missing_listing[i:i+100]}, 'MainBoardMasterCapture', current=True)
                      for i in range(0, len(missing_listing), 100)]
            breadth = [add('master_all', {'date': str(day)}, 'MarketBreadthCapture', current=True),
                       add('spot_all', {}, 'MarketBreadthCapture', current=True)]
            current_price = add('market_daily', {'date': str(day)}, 'CurrentDailyPriceCapture', current=True)
            current_status_evidence = [add('delisting_sse', {}, 'CurrentEligibilityStatusCapture', current=True)]
            theme_list = None if critical_only else add('concept_list', {}, 'CurrentThemeCapture', current=True)
            drain('CurrentIdentityAndBreadth', breadth + basics + current_status_evidence + [current_price])
            selected_theme_endpoint = 'concept_members'
            if not any(r.get('status')=='COMPLETE' and r.get('pagination_complete') for r in queue.results([breadth[-1]])):
                alternate_spot = add('spot_sina', {}, 'MarketBreadthCapture', current=True)
                drain('MarketBreadthRegisteredAlternative', [alternate_spot])

            def history(iid, start, end, stage, endpoint='history'):
                fragments = queue.cached_history(endpoint, iid, str(start), str(end), FIELD_VERSION,
                                                 expected_sessions=[s for s in sessions if start <= s <= end])
                wanted = [s for s in sessions if start <= s <= end]
                cached = set()
                for a, z, rid in fragments:
                    cached.update(s for s in wanted if a <= str(s) <= z)
                    queue.requested_ids.add(rid)
                missing = [s for s in wanted if s not in cached]
                if self.history_coverage_plan is not None and endpoint == 'history':
                    external = self.history_coverage_plan.covered_sessions(iid)
                    missing = [s for s in missing if str(s) not in external]
                targets = [r[2] for r in fragments]
                if missing:
                    # Gaps are requested as contiguous calendar spans. Never
                    # infer price validity from this request-range coverage.
                    # One explicit HFQ generation supplies the whole requested
                    # comparison window when no verified same-basis link exists.
                    # RAW remains incremental. It is never joined by guessed
                    # adjustment factors simply to avoid an initial warm-up.
                    a,z=(start,end) if endpoint=='history_adjusted' else (min(missing),max(missing))
                    targets.append(add(endpoint, {'codes':[iid], 'start_date':str(a), 'end_date':str(z)}, stage))
                return targets

            def history_batch(instrument_ids, start, end, stage, endpoint, batch_size=50):
                requested, remaining = [], []
                required = {s for s in sessions if start <= s <= end}
                for iid in instrument_ids:
                    fragments = queue.cached_history(endpoint, iid, str(start), str(end), FIELD_VERSION,
                                                     expected_sessions=required)
                    covered = set()
                    for a,z,rid in fragments:
                        covered.update(s for s in required if a <= str(s) <= z)
                        queue.requested_ids.add(rid)
                        requested.append(rid)
                    external = (self.history_coverage_plan.covered_sessions(iid)
                                if self.history_coverage_plan is not None and endpoint == 'history' else set())
                    missing = {s for s in required-covered if str(s) not in external}
                    if missing:
                        remaining.append((iid, min(missing)))
                # Batching changes transport cost, never membership or rank.
                # Each named ID is explicitly recorded before any call executes.
                for offset in range(0,len(remaining),batch_size):
                    batch=remaining[offset:offset+batch_size]
                    requested.append(add(endpoint, {'codes':[v[0] for v in batch],
                        'start_date':str(min(v[1] for v in batch)), 'end_date':str(end)}, stage))
                return sorted(set(requested))

            # These sets are frozen in full before network budget is consumed.
            # The installed public SDK exposes all-A daily history. One complete
            # date response supplies every directory member's RAW row, with
            # identity joins and missing-day checks left to the consumer.
            if per_security_history:
                # Same registered RAW adapter and full current directory/window.
                # This prospective bootstrap does not claim a historical universe replay.
                structures = history_batch(ids, v2_long_history_start, day,
                                           'StructureHistoryCapture', 'history', batch_size=10)
            else:
                structures = [add('market_daily', {'date':str(s)}, 'StructureHistoryCapture', current=s==day)
                              for s in sessions if v2_long_history_start <= s <= day
                              and (self.history_coverage_plan is None
                                   or self.history_coverage_plan.raw_request_needed(s, ids))]
                structures += [add('master_all', {'date':str(s)}, 'HistoricalSecurityReplayCapture', current=s==day)
                               for s in sessions if v2_long_history_start <= s <= day]
                structures.append(add('stock_basic_all', {}, 'HistoricalSecurityReplayCapture', current=True))
            # The installed provider publishes cumulative back-adjustment
            # factors. The adapter's versioned formula is independently checked
            # against HFQ samples; it rejects conflicting historical factors.
            structures += [add('adjustment_factors', {'codes':ids[i:i+100],
                'start_date':'1990-01-01', 'end_date':str(day)}, 'StructureHistoryCapture')
                for i in range(0,len(ids),100)]
            current_limit = None
            direct_limits = []
            outcome_requests = []
            if not critical_only:
                current_limit = add('limit_up_pool', {'date':str(day)}, 'MarketBreadthLimitEvidence', current=True)
                direct_limits = [add('daily_limits_eastmoney', {'date':str(day), 'codes':ids[i:i+20]},
                    'MarketBreadthDirectLimitEvidence', current=True) for i in range(0,len(ids),20)]
                outcome_requests = [rid for item in demand for endpoint in ('history','history_adjusted')
                                    for rid in history(item['instrument_id'], date.fromisoformat(item['start_date']), day,
                                                       'PublishedCandidateOutcomeCapture', endpoint)]
                outcome_ids=sorted({item['instrument_id'] for item in demand})
                outcome_requests += [add('stock_basic', {'codes':outcome_ids[i:i+100]}, 'PublishedCandidateOutcomeCapture', current=True)
                                     for i in range(0,len(outcome_ids),100)]
            normalize = self.normalizer
            if normalize is None:
                from .provider_normalization import normalize_capture_with_report
                normalize = normalize_capture_with_report
            import inspect
            signature = inspect.signature(normalize).parameters
            config = self.config

            def normalization_options(*, write_history):
                options = {}
                if write_history and self.history_store is not None and 'history_store' in signature:
                    options['history_store'] = self.history_store
                if write_history and history_base is not None and 'history_base_ref' in signature:
                    options['history_base_ref'] = history_base['history_artifact_ref']
                return options

            def normalize_current_queue(*, write_history):
                nonlocal history_base
                captured_results = queue.results()
                normalization_cutoff = datetime.now(SH)
                if write_history:
                    history_base = self._validate_history_base_for_results(
                        history_base, captured_results, day, normalization_cutoff)
                current = normalize(captured_results, observation_date=day, now=normalization_cutoff,
                                    sessions=sessions, **normalization_options(write_history=write_history))
                if isinstance(current, tuple):
                    bundle, gaps = current
                else:
                    bundle, gaps = current, []
                return MarketInput.model_validate(bundle), gaps, normalization_cutoff

            def finish_critical_capture(bundle, gaps, actual_cutoff, readiness, *, fast_decision_result=None):
                queue_summary = queue.summary()
                boundary_summary = _task_boundary_summary(queue)
                critical_summary = boundary_summary[CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL]
                background_summary = boundary_summary[CAPTURE_PURPOSE_BACKGROUND_RESEARCH]
                early = bool(fast_decision_result and fast_decision_result.get('decision') == 'NOT_READY')
                report = {'pipeline_version': PIPELINE_VERSION, 'started_at': started.isoformat(),
                    'completed_at': actual_cutoff.isoformat(), 'observation_date': str(day),
                    'raw_bundle_hash': digest(bundle), 'input_id': 'market_'+digest(bundle),
                    'coverage_type': bundle.coverage_type,
                    'history_artifact_ref':bundle.history_artifact_ref,
                    'history_artifact_hash':bundle.history_artifact_hash,
                    'frozen_config_hash': config.config_hash if config is not None else None,
                    'scope': 'DAILY_PRIMARY_CRITICAL_CAPTURE_ONLY_REUSING_MAINBOARD_DAG',
                    'capture_purpose': purpose,
                    'fast_decision_enabled': fast_decision,
                    'task_classification': {
                        'classification_version': 'DAILY_PRIMARY_CRITICAL_CAPTURE_BOUNDARY_V1',
                        'critical_purpose': CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL,
                        'background_purpose': CAPTURE_PURPOSE_BACKGROUND_RESEARCH,
                        'critical_stages': sorted(CRITICAL_CAPTURE_STAGES),
                        'background_stages': sorted(BACKGROUND_RESEARCH_STAGES),
                        'critical_only': True,
                        'decision_roles': {
                            'IMMEDIATE': sorted(stage for stage in CRITICAL_CAPTURE_STAGES
                                                if stage != 'HistoricalSecurityReplayCapture'),
                            'DEFERRED': sorted(BACKGROUND_RESEARCH_STAGES | {'HistoricalSecurityReplayCapture'}),
                        },
                    },
                    'critical_path_trace': {
                        'request': 'DAILY_PRIMARY_REQUEST',
                        'critical_tasks': sorted(CRITICAL_CAPTURE_STAGES),
                        'background_tasks': sorted(BACKGROUND_RESEARCH_STAGES),
                        'critical_completed': critical_summary['pending_count'] == 0,
                        'background_pending': background_summary['pending_count'],
                        'readiness': 'READY' if readiness.get('global_required_ready') else 'NOT_READY',
                        'strategy_started': False if early else None,
                    },
                    'daily_primary_fast_decision': fast_decision_result or {
                        'version': 'DAILY_PRIMARY_FAST_DECISION_BOUNDARY_V1',
                        'sufficient': readiness.get('global_required_ready') and critical_summary['pending_count'] == 0,
                        'decision': 'READY' if readiness.get('global_required_ready') else 'CONTINUE_REFRESH',
                        'reason': 'FINAL_CRITICAL_CAPTURE_EVALUATION',
                    },
                    'capture_boundary': boundary_summary,
                    'master_ids': ids, 'structure_candidate_ids': [],
                    'frozen_peer_ids': [], 'v2_long_history': {
                        'required_history_sessions': stage_aware_history_sessions,
                        'requested_start': str(v2_long_history_start),
                        'requested_end': str(day),
                        'requested_sessions': sum(v2_long_history_start <= s <= day for s in sessions),
                        'universe_policy': 'FULL_CURRENT_MAINBOARD_DIRECTORY_BEFORE_DAILY_PREVIEW_OR_STAGE_SCORING',
                        'raw_daily_endpoint': history_endpoint,
                        'raw_daily_request_granularity': history_granularity,
                        'adjustment_factor_policy': 'ALL_DIRECTORY_IDS_BATCHED_INDEPENDENTLY_OF_DAILY_CANDIDATE_IDS',
                        'historical_security_policy': ('CURRENT_ROSTER_WITH_DATED_RAW_STATUS_NOT_HISTORICAL_UNIVERSE_REPLAY' if per_security_history else 'DATED_MASTER_ALL_AND_STOCK_BASIC_ALL_FOR_RECONSTRUCTED_HISTORICAL_REPLAY_SCOPE'),
                    },
                    'history_append_base': history_base,
                    'published_outcome_demand': [],
                    'published_outcome_demand_policy': 'DEFERRED_BACKGROUND_RESEARCH_NOT_COMPUTED_ON_DAILY_PRIMARY_CRITICAL_PATH',
                    'background_research_deferred': True,
                    'background_research_deferred_stages': sorted(BACKGROUND_RESEARCH_STAGES),
                    'stages': stages, 'queue': queue_summary,
                    'daily_primary_readiness': readiness,
                    'consumer_status': readiness['final_readiness'],
                    'consumer_coverage': readiness.get('data_coverage_diagnostics', {}).get('coverage_scope'),
                    'consumer_emotion': None,
                    'candidate_count': 0, 'full_score_count': 0,
                    'field_level_gaps': gaps, 'market_edge': 'NOT_ESTABLISHED',
                    'actual_daily_ready': bool(readiness.get('global_required_ready')),
                    'remaining_requests': [{k:r[k] for k in ('request_id','state','attempts','failure_category','payload')}
                                           for r in queue.rows() if r['state']!='COMPLETE']}
                _write_once(output/'market-input.json', bundle.model_dump(mode='json'))
                _write_once(output/'capture-report.json', report)
                self.last_report = report
                return report

            last_fast_decision_signature = None

            def fast_decision_signature():
                adjustment_states = {}
                current_states = {}
                pending_factor_codes = 0
                structure_pending = False
                for row in queue.rows():
                    payload = json.loads(row['payload'])
                    state = row['state']
                    endpoint = payload.get('endpoint')
                    row_stage = payload.get('stage')
                    if endpoint == 'adjustment_factors':
                        adjustment_states[state] = adjustment_states.get(state, 0) + 1
                        if state not in {'COMPLETE', 'BLOCKED'}:
                            pending_factor_codes += len((payload.get('parameters') or {}).get('codes') or ())
                    if endpoint == 'market_daily' and row_stage == 'CurrentDailyPriceCapture':
                        current_states[state] = current_states.get(state, 0) + 1
                    if row_stage == 'StructureHistoryCapture' and state not in {'COMPLETE', 'BLOCKED'}:
                        structure_pending = True
                return (tuple(sorted(current_states.items())),
                        tuple(sorted(adjustment_states.items())),
                        pending_factor_codes,
                        structure_pending)

            def evaluate_fast_checkpoint(stage, _summary):
                nonlocal last_fast_decision_signature
                if not (critical_only and fast_decision and config is not None):
                    return
                signature_value = fast_decision_signature()
                if signature_value == last_fast_decision_signature:
                    return
                last_fast_decision_signature = signature_value
                try:
                    bundle, gaps, actual_cutoff = normalize_current_queue(write_history=False)
                    from .daily_primary_readiness import evaluate_daily_primary_readiness
                    readiness = evaluate_daily_primary_readiness(
                        'market_'+digest(bundle), bundle, actual_cutoff,
                        config_hash=config.config_hash if config is not None else None)
                    decision = evaluate_fast_decision_sufficiency(
                        readiness, queue.rows(), checkpoint=stage)
                except Exception:
                    return
                if decision.get('sufficient') and decision.get('decision') == 'NOT_READY':
                    finish_critical_capture(bundle, gaps, actual_cutoff, readiness,
                                            fast_decision_result=decision)
                    raise _FastDecisionCaptureComplete(bundle)

            fast_decision_checkpoint = evaluate_fast_checkpoint
            # All P0 current inputs precede P1 comparison bases, then P2
            # full-market long-window history. Optional context cannot spend
            # this core budget.
            try:
                fast_decision_checkpoint('PRE_STRUCTURE_HISTORY', queue.summary())
                drain('StructureHistoryCapture', structures)
            except _FastDecisionCaptureComplete as done:
                return done.bundle
            # Mature results remain explicit demand even for former losers.
            if not critical_only:
                drain('PublishedCandidateOutcomeCapture', sorted(set(outcome_requests)))
                # Price/amount completeness is independent of optional board-event
                # evidence; quote-by-quote calls cannot consume PV's initial budget.
                import os
                token_configured=bool(os.environ.get('TUSHARE_TOKEN') or os.environ.get('TUSHARE_PRO_TOKEN'))
                ts_plan=tushare_request_plan(day,sessions)
                if not token_configured:
                    # One explicit request per endpoint records AUTH_REQUIRED with
                    # zero HTTP, instead of hundreds of identical auth failures.
                    ts_plan=list({ep:(ep,params) for ep,params in ts_plan}.values())
                ts_ids=[add(ep,params,'TushareOptionalEnrichment',current=ep=='tushare_stock_basic') for ep,params in ts_plan]
                drain('MarketBreadthLimitEvidence', [current_limit] + direct_limits)
                drain('CurrentThemeDirectory', [theme_list])
                if not any(r.get('status')=='COMPLETE' and r.get('pagination_complete') for r in queue.results([theme_list])):
                    theme_list = add('concept_list_sina', {}, 'CurrentThemeCapture', current=True)
                    selected_theme_endpoint = 'concept_members_sina'
                    drain('CurrentThemeRegisteredAlternative', [theme_list])
                directory = [r for result in queue.results([theme_list]) for r in result.get('rows', ())]
                theme_requests = []
                for theme in directory:
                    symbol = theme.get('label') or theme.get('板块代码') or theme.get('theme_id') or theme.get('板块名称')
                    if symbol:
                        theme_requests.append(add(selected_theme_endpoint, {'symbol':str(symbol)}, 'CurrentThemeCapture', current=True))
                drain('CurrentThemeCapture', theme_requests)
                drain('TushareOptionalEnrichment',ts_ids)
            bundle, gaps, normalization_cutoff = normalize_current_queue(write_history=True)
            config = self.config
            if critical_only:
                actual_cutoff = datetime.now(SH)
                from .daily_primary_readiness import evaluate_daily_primary_readiness
                readiness = evaluate_daily_primary_readiness(
                    'market_'+digest(bundle), bundle, actual_cutoff,
                    config_hash=config.config_hash if config is not None else None)
                finish_critical_capture(bundle, gaps, actual_cutoff, readiness)
                return bundle
            from .engine import run
            preview = run(bundle, config=config, cutoff=datetime.now(SH), profile='DAILY_V1')
            candidate_ids = sorted({i.instrument_id for i in preview.items})
            peer_ids = sorted({iid for item in preview.items for theme in item.theme_evidence
                               for iid in theme.get('member_ids', ()) if iid in ids})
            deep = [rid for iid in candidate_ids for endpoint in ('history', 'history_adjusted')
                    for rid in history(iid, history_start, day, 'CandidateAndPeerHistoryCapture', endpoint)]
            # L needs full M10/ret5/median_amount20 peers, not the final TopN.
            deep += [rid for iid in peer_ids for rid in history(iid, structure_start, day, 'CandidateAndPeerHistoryCapture', 'history_adjusted')]
            drain('CandidateAndPeerHistoryCapture', sorted(set(deep)))
            # Funds is a shadow extension of the entire frozen structure set.
            funds = []
            if candidate_ids:
                funds.append(add('lhb_summary', {'start_date':str(sessions[-3]), 'end_date':str(day)}, 'OptionalFundsCapture', current=True))
                for iid in candidate_ids:
                    funds.append(add('money_flow', {'stock':iid[:6], 'market':iid[-2:].lower()}, 'OptionalFundsCapture', current=True))
            drain('OptionalFundsCapture', funds)
            captured_results = queue.results()
            normalization_cutoff = datetime.now(SH)
            history_base = self._validate_history_base_for_results(history_base, captured_results, day, normalization_cutoff)
            normalized = normalize(captured_results, observation_date=day, now=normalization_cutoff, sessions=sessions,
                                   **normalization_options(write_history=True))
            if isinstance(normalized, tuple):
                bundle, gaps = normalized
            else:
                bundle = normalized
            bundle = MarketInput.model_validate(bundle)
            actual_cutoff = datetime.now(SH)
            check = run(bundle, config=config, cutoff=actual_cutoff, profile='DAILY_V1')
            queue_summary = queue.summary()
            boundary_summary = _task_boundary_summary(queue)
            report = {'pipeline_version': PIPELINE_VERSION, 'started_at': started.isoformat(),
                'completed_at': actual_cutoff.isoformat(), 'observation_date': str(day),
                'raw_bundle_hash': digest(bundle), 'input_id': 'market_'+digest(bundle),
                'coverage_type': bundle.coverage_type,
                'history_artifact_ref':bundle.history_artifact_ref,
                'history_artifact_hash':bundle.history_artifact_hash,
                'frozen_config_hash': check.config_hash,
                'scope': 'FULL_MAINBOARD_DIRECTORY_DISCOVERY_WITH_PERSISTENT_PENDING_REQUESTS',
                'capture_purpose': purpose,
                'task_classification': {
                    'classification_version': 'DAILY_PRIMARY_CRITICAL_CAPTURE_BOUNDARY_V1',
                    'critical_purpose': CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL,
                    'background_purpose': CAPTURE_PURPOSE_BACKGROUND_RESEARCH,
                    'critical_stages': sorted(CRITICAL_CAPTURE_STAGES),
                    'background_stages': sorted(BACKGROUND_RESEARCH_STAGES),
                    'critical_only': False,
                },
                'critical_path_trace': {
                    'request': 'BACKGROUND_RESEARCH_CAPTURE_COMPATIBILITY_PATH',
                    'critical_tasks': sorted(CRITICAL_CAPTURE_STAGES),
                    'background_tasks': sorted(BACKGROUND_RESEARCH_STAGES),
                    'critical_completed': boundary_summary[CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL]['pending_count'] == 0,
                    'background_pending': boundary_summary[CAPTURE_PURPOSE_BACKGROUND_RESEARCH]['pending_count'],
                    'readiness': None,
                    'strategy_started': None,
                },
                'capture_boundary': boundary_summary,
                'master_ids': ids, 'structure_candidate_ids': candidate_ids, 'frozen_peer_ids': peer_ids,
                'v2_long_history': {
                    'required_history_sessions': stage_aware_history_sessions,
                    'requested_start': str(v2_long_history_start),
                    'requested_end': str(day),
                    'requested_sessions': sum(v2_long_history_start <= s <= day for s in sessions),
                    'universe_policy': 'FULL_CURRENT_MAINBOARD_DIRECTORY_BEFORE_DAILY_PREVIEW_OR_STAGE_SCORING',
                    'raw_daily_endpoint': history_endpoint,
                    'raw_daily_request_granularity': history_granularity,
                    'adjustment_factor_policy': 'ALL_DIRECTORY_IDS_BATCHED_INDEPENDENTLY_OF_DAILY_CANDIDATE_IDS',
                    'historical_security_policy': ('CURRENT_ROSTER_WITH_DATED_RAW_STATUS_NOT_HISTORICAL_UNIVERSE_REPLAY' if per_security_history else 'DATED_MASTER_ALL_AND_STOCK_BASIC_ALL_FOR_RECONSTRUCTED_HISTORICAL_REPLAY_SCOPE'),
                },
                'history_append_base': history_base,
                'published_outcome_demand': demand, 'stages': stages, 'queue': queue_summary,
                'consumer_status': check.status, 'consumer_coverage': check.coverage,
                'consumer_emotion': check.emotion.model_dump(mode='json'),
                'candidate_count': len(check.items), 'full_score_count': sum(i.score is not None for i in check.items),
                'field_level_gaps': gaps, 'market_edge': 'NOT_ESTABLISHED',
                'actual_daily_ready': check.status in ('COMPLETE','EMPTY'),
                'remaining_requests': [{k:r[k] for k in ('request_id','state','attempts','failure_category','payload')}
                                       for r in queue.rows() if r['state']!='COMPLETE']}
            _write_once(output/'market-input.json', bundle.model_dump(mode='json'))
            _write_once(output/'capture-report.json', report)
            self.last_report = report
            return bundle
