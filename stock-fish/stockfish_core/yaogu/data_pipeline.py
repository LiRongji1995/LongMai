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
from .request_queue import RequestQueue, RequestBudget
from .provider_requests import execute_request, FIELD_VERSION

PIPELINE_VERSION = 'MAINBOARD_DATA_DAG_R03_V1'
SH = ZoneInfo('Asia/Shanghai')


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
    return demand


class MainboardPipeline:
    def __init__(self, root, *, service=None, config=None, executor=execute_request, normalizer=None, budget=None):
        self.root, self.service, self.executor = Path(root), service, executor
        self.normalizer = normalizer
        self.config = config if config is not None else service.current_config() if service else None
        self.budget = budget or RequestBudget()

    def capture(self, output_dir, now, *, observation_date=None, checkpoint=lambda: None, progress=lambda _: None):
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('REAL_AWARE_CAPTURE_CLOCK_REQUIRED')
        from ..observed_data import exchange_calendar_2026
        local = now.astimezone(SH)
        registered = tuple(sorted(exchange_calendar_2026().sessions))
        # A manual weekend run requests the latest actual exchange session.
        day = observation_date or max((s for s in registered if s <= local.date()), default=None)
        if day is None or day > local.date() or day not in registered:
            raise ValueError('VERIFIED_OBSERVATION_SESSION_REQUIRED')
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        epoch = local.strftime('%Y-%m-%dT%H')
        started = datetime.now(SH)
        stages = []
        with RequestQueue(self.root, budget=self.budget) as queue:
            def add(endpoint, parameters, stage, *, current=False):
                return queue.enqueue(endpoint, parameters, stage=stage, definition_version=FIELD_VERSION,
                                     cache_epoch=epoch if current else None)

            def drain(stage, ids):
                before = queue.calls
                queue.drain(self.executor, ids=ids, checkpoint=checkpoint, progress=progress)
                rows = queue.rows(ids)
                stages.append({'stage': stage, 'request_ids': list(ids), 'requested': len(rows),
                               'complete': sum(r['state'] == 'COMPLETE' for r in rows),
                               'network_calls': queue.calls-before,
                               'status': 'COMPLETE' if rows and all(r['state']=='COMPLETE' for r in rows) else 'PARTIAL'})

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
            structure_start = sessions[max(0, len(sessions)-22)]
            history_start = sessions[max(0, len(sessions)-251)]
            demand = published_outcome_demand(self.service, day, sessions)
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
                    'minimum_structure_sessions': 22, 'scoring_history_sessions': 251,
                    'calendar_source': calendar_source, 'calendar_sessions': len(sessions),
                    'initial_estimate': {'full_pool_structure_members': len(ids),
                        'full_pool_raw_daily_requests': sum(structure_start <= s <= day for s in sessions),
                        'current_direct_limit_batches': (len(ids)+19)//20,
                        'full_pool_adjustment_factor_batches': (len(ids)+99)//100,
                        'basic_directory_batches': (len(missing_listing)+99)//100, 'published_outcome_objects': len(demand),
                        'candidate_history_requests': 'DETERMINED_AFTER_ALL_POOL_STRUCTURE_DISCOVERY',
                        'theme_member_requests': 'DETERMINED_BY_REGISTERED_PROVIDER_THEME_DIRECTORY'},
                    'budget': vars(self.budget), 'published_outcome_demand': demand,
                    'scope_policy': 'ALL_DIRECTORY_MEMBERS_BEFORE_SCORE_NO_LIMIT_UP_POOL_OR_CODE_QUOTA'}
            _write_once(output / 'plan.json', plan)
            basics = [add('stock_basic', {'codes': missing_listing[i:i+100]}, 'MainBoardMasterCapture', current=True)
                      for i in range(0, len(missing_listing), 100)]
            breadth = [add('master_all', {'date': str(day)}, 'MarketBreadthCapture', current=True),
                       add('spot_all', {}, 'MarketBreadthCapture', current=True)]
            current_status_evidence = [add('delisting_sse', {}, 'MainBoardMasterCapture', current=True)]
            theme_list = add('concept_list', {}, 'CurrentThemeCapture', current=True)
            drain('CurrentIdentityAndBreadth', breadth + basics + current_status_evidence + [theme_list])
            selected_theme_endpoint = 'concept_members'
            if not any(r.get('status')=='COMPLETE' and r.get('pagination_complete') for r in queue.results([theme_list])):
                theme_list = add('concept_list_sina', {}, 'CurrentThemeCapture', current=True)
                selected_theme_endpoint = 'concept_members_sina'
                drain('CurrentThemeRegisteredAlternative', [theme_list])
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
                    if required-covered:
                        remaining.append((iid, min(required-covered)))
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
            structures = [add('market_daily', {'date':str(s)}, 'StructureHistoryCapture', current=s==day)
                          for s in sessions if structure_start <= s <= day]
            # The installed provider publishes cumulative back-adjustment
            # factors. The adapter's versioned formula is independently checked
            # against HFQ samples; it rejects conflicting historical factors.
            structures += [add('adjustment_factors', {'codes':ids[i:i+100],
                'start_date':'1990-01-01', 'end_date':str(day)}, 'StructureHistoryCapture')
                for i in range(0,len(ids),100)]
            current_limit = add('limit_up_pool', {'date':str(day)}, 'MarketBreadthLimitEvidence', current=True)
            direct_limits = [add('daily_limits_eastmoney', {'date':str(day), 'codes':ids[i:i+20]},
                'MarketBreadthDirectLimitEvidence', current=True) for i in range(0,len(ids),20)]
            drain('MarketBreadthLimitEvidence', [current_limit] + direct_limits)
            outcome_requests = [rid for item in demand for endpoint in ('history','history_adjusted')
                                for rid in history(item['instrument_id'], date.fromisoformat(item['start_date']), day,
                                                   'PublishedCandidateOutcomeCapture', endpoint)]
            outcome_ids=sorted({item['instrument_id'] for item in demand})
            outcome_requests += [add('stock_basic', {'codes':outcome_ids[i:i+100]}, 'PublishedCandidateOutcomeCapture', current=True)
                                 for i in range(0,len(outcome_ids),100)]
            # Mature results have their own demand priority; losing candidates
            # do not wait for inclusion in today's discovery pool.
            drain('PublishedCandidateOutcomeCapture', sorted(set(outcome_requests)))
            drain('StructureHistoryCapture', structures)
            directory = [r for result in queue.results([theme_list]) for r in result.get('rows', ())]
            theme_requests = []
            for theme in directory:
                symbol = theme.get('label') or theme.get('板块代码') or theme.get('theme_id') or theme.get('板块名称')
                if symbol:
                    theme_requests.append(add(selected_theme_endpoint, {'symbol':str(symbol)}, 'CurrentThemeCapture', current=True))
            drain('CurrentThemeCapture', theme_requests)
            normalize = self.normalizer
            if normalize is None:
                from .provider_normalization import normalize_capture_with_report
                normalize = normalize_capture_with_report
            bundle = normalize(queue.results(), observation_date=day, now=datetime.now(SH), sessions=sessions)
            if isinstance(bundle, tuple):
                bundle, gaps = bundle
            else:
                gaps = []
            bundle = MarketInput.model_validate(bundle)
            from .engine import run
            config = self.config
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
            normalized = normalize(queue.results(), observation_date=day, now=datetime.now(SH), sessions=sessions)
            if isinstance(normalized, tuple):
                bundle, gaps = normalized
            else:
                bundle = normalized
            bundle = MarketInput.model_validate(bundle)
            actual_cutoff = datetime.now(SH)
            check = run(bundle, config=config, cutoff=actual_cutoff, profile='DAILY_V1')
            queue_summary = queue.summary()
            report = {'pipeline_version': PIPELINE_VERSION, 'started_at': started.isoformat(),
                'completed_at': actual_cutoff.isoformat(), 'observation_date': str(day),
                'raw_bundle_hash': digest(bundle), 'input_id': 'market_'+digest(bundle),
                'coverage_type': bundle.coverage_type,
                'frozen_config_hash': check.config_hash,
                'scope': 'FULL_MAINBOARD_DIRECTORY_DISCOVERY_WITH_PERSISTENT_PENDING_REQUESTS',
                'master_ids': ids, 'structure_candidate_ids': candidate_ids, 'frozen_peer_ids': peer_ids,
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
