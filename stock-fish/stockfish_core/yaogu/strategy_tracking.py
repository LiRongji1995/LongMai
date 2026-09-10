"""R05 append-only price labels, stable research episodes and date-block reviews."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from statistics import median
from types import MappingProxyType
from typing import Any, Mapping
from zoneinfo import ZoneInfo
from .contracts import DailyBar, MarketInput, digest
from .events import TRUE, limit_event
from .visibility import EvidenceResolver

SH = ZoneInfo('Asia/Shanghai')
DEFAULT_RESEARCH_HORIZONS = (1, 2, 3, 4, 5, 10, 20)
POST_EXIT_HORIZONS = (5, 10)
OUTCOME_VERSION = 'R05_STRATEGY_OUTCOME_OBSERVATION_V3'
METRIC_POLICY_ID = 'R05_COMPARABLE_PRICE_AND_LIMIT_EVENT_LABEL_V1'
TRACKING_VERSION = 'R05_RESEARCH_EPISODE_TRACKING_V2'
REVIEW_VERSION = 'R05_RESEARCH_FEEDBACK_V2'
FOCUS = 'PV_FOCUS_PREVIEW_V0.1'
STAGE_AWARE_FOCUS = 'STAGE_AWARE_MOMENTUM_V2_FOCUS'
STAGE_AWARE = 'STAGE_AWARE_MOMENTUM_V2'
V2_OUTCOME_POLICY_ID = 'STAGE_AWARE_MOMENTUM_V2_SETUP_SPECIFIC_OUTCOMES_V2'


def _day(value):
    return value.date() if isinstance(value, datetime) else value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _dt(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _dec(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except Exception:
        return None


def _num(value):
    return float(value.quantize(Decimal('0.000001'))) if value is not None else None


def validate_horizons(horizons):
    if not horizons or any(type(h) is not int or h <= 0 for h in horizons):
        raise ValueError('research horizons must be positive integers')
    return tuple(dict.fromkeys(horizons))


def _sessions(bundle):
    return sorted(set(_day(day) for day in bundle.sessions))


def _visible_bars(bundle, evaluated_at):
    if bundle.history_artifact_ref:
        from .history_artifacts import OutcomeBarMapping
        return OutcomeBarMapping(bundle,evaluated_at)
    resolver = EvidenceResolver(bundle.receipts, cutoff=evaluated_at, coverage_type=bundle.coverage_type, sessions=bundle.sessions)
    grouped = {}
    for bar in bundle.bars:
        if bar.quality != 'INVALID' and resolver.is_visible(bar, instrument_id=bar.instrument_id, session=bar.session, require_session=True):
            grouped.setdefault((bar.instrument_id, bar.session), []).append(bar)
    values = {}
    for key, rows in grouped.items():
        priority = lambda row: (row.source_published_at, row.ingested_at, row.first_seen_at)
        latest = max(priority(row) for row in rows)
        choices = [row for row in rows if priority(row) == latest]
        if len({digest(row) for row in choices}) == 1:
            values[key] = choices[0]
    return values


@dataclass(frozen=True, slots=True, init=False)
class OutcomeContext:
    """Immutable, explicitly scoped reuse of one input's cutoff-visible prices."""
    _bundle: MarketInput
    evaluated_at: datetime
    sessions: tuple[date, ...]
    bars: Mapping[tuple[str, date], DailyBar]
    input_hash: str

    def __init__(self, bundle, evaluated_at):
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise ValueError('evaluated_at requires timezone')
        object.__setattr__(self, '_bundle', bundle)
        object.__setattr__(self, 'evaluated_at', evaluated_at)
        object.__setattr__(self, 'sessions', tuple(_sessions(bundle)))
        object.__setattr__(self, 'bars', MappingProxyType(_visible_bars(bundle, evaluated_at)))
        object.__setattr__(self, 'input_hash', digest(bundle))

    def validate_binding(self, bundle, evaluated_at):
        if bundle is not self._bundle:
            raise ValueError('outcome context requires the same bundle object')
        if evaluated_at != self.evaluated_at or evaluated_at.isoformat() != self.evaluated_at.isoformat():
            raise ValueError('outcome context evaluated_at mismatch')


def _outcome_context(bundle, evaluated_at, context):
    if context is None:
        return OutcomeContext(bundle, evaluated_at)
    if not isinstance(context, OutcomeContext):
        raise ValueError('invalid outcome context')
    context.validate_binding(bundle, evaluated_at)
    return context


def authoritative_strategy_items(run):
    """One actual ranked set per canonical strategy; no display fallback or aliases."""
    results = {**(run.get('results') or {}), **(run.get('evaluation_baselines') or {})}
    pv = {row['instrument_id']: row for row in (results.get('PV_OBSERVATION_V1') or {}).get('items', [])}
    seen = set()
    for key, result in results.items():
        sid = result.get('canonical_strategy_id') or result.get('strategy_id') or key
        if sid in seen:
            continue
        seen.add(sid)
        items = []
        ids = set()
        for row in result.get('items', []):
            if not row.get('instrument_id') or row['instrument_id'] in ids:
                continue
            if sid in {'DNA_RERANK_V1', 'THEME_CONTEXT_V1', 'DNA_REVERSAL_V1', 'THEME_LIMITUP_V1'} and row.get('status') not in {None, 'EVALUATED', 'COMPLETE'}:
                continue
            if row.get('rank') is None:
                continue
            ids.add(row['instrument_id'])
            items.append({**pv.get(row['instrument_id'], {}), **row})
        yield sid, items


def _strategy_items(run, strategy_id='PV_OBSERVATION_V1'):
    return next((items for sid, items in authoritative_strategy_items(run) if sid == strategy_id), [])


def _focus_ids(run):
    policy, _, selected = _primary_focus_selection(run)
    return selected


def _primary_focus_selection(run):
    selections = run.get('selection_results') or {}
    for policy_id in (STAGE_AWARE_FOCUS, FOCUS):
        value = selections.get(policy_id)
        if value is not None:
            return policy_id, value, set(value.get('selected_ids') or [])
    return None, {}, set()


def _strategy_focus(run, strategy_id, items):
    if strategy_id == STAGE_AWARE:
        selected = {row['instrument_id'] for row in items
                    if row.get('instrument_id') and row.get('decision_status') == 'FOCUS'}
        if selected:
            return STAGE_AWARE_FOCUS, selected
        saved_focus = (run.get('selection_results') or {}).get(STAGE_AWARE_FOCUS)
        if saved_focus is not None:
            return STAGE_AWARE_FOCUS, set(saved_focus.get('selected_ids') or [])
    policy_id, _, selected = _primary_focus_selection(run)
    return policy_id or FOCUS, selected


def _tracking_strategy_id(run):
    return STAGE_AWARE if (run.get('selection_results') or {}).get(STAGE_AWARE_FOCUS) is not None else 'PV_OBSERVATION_V1'


def _reference_close(item):
    return _dec((item.get('exact_prices') or {}).get('comparable_close', item.get('comparable_close')))


def _metric(value, mature, reason=None):
    return {'status': 'NOT_MATURED' if not mature else 'MATURE_VALID' if value is not None else 'UNAVAILABLE',
            'valid': mature and value is not None, 'reason_codes': [reason] if mature and value is None and reason else []}


def _session_mature(day, evaluated_at):
    return datetime.combine(day, time(15), SH) <= evaluated_at.astimezone(SH)


def _event_flags(bar):
    if bar is None:
        return {'close_limit_up': None, 'close_limit_down': None, 'event_status': 'MISSING_PRICE'}
    event = limit_event(bar)
    return {
        'close_limit_up': True if event.close_limit_up == TRUE else False if event.close_limit_up == 'FALSE' else None,
        'close_limit_down': True if event.close_limit_down == TRUE else False if event.close_limit_down == 'FALSE' else None,
        'event_status': 'VERIFIED' if event.close_limit_up in (TRUE, 'FALSE') and event.close_limit_down in (TRUE, 'FALSE') else 'LIMIT_EVENT_UNVERIFIED',
    }


def _future_state(iid, day, bars, evaluated_at, basis):
    state = {'session': str(day), 'status': 'NOT_MATURED', 'close_limit_up': None, 'close_limit_down': None}
    if not _session_mature(day, evaluated_at):
        return state
    bar = bars.get((iid, day))
    if bar is None or bar.comparable_close is None:
        state['status'] = 'MISSING_PRICE'
        return state
    if not basis or bar.comparable_basis != basis:
        state['status'] = 'COMPANY_ACTION_OR_COMPARABILITY_UNSUPPORTED'
        return state
    state.update(
        status='OBSERVED',
        open=str(bar.open) if bar.open is not None else None,
        high=str(bar.high) if bar.high is not None else None,
        low=str(bar.low) if bar.low is not None else None,
        close=str(bar.close) if bar.close is not None else None,
        reference_prev_close=str(bar.reference_prev_close) if bar.reference_prev_close is not None else None,
        comparable_open=str(bar.comparable_open) if bar.comparable_open is not None else None,
        comparable_high=str(bar.comparable_high) if bar.comparable_high is not None else None,
        comparable_low=str(bar.comparable_low) if bar.comparable_low is not None else None,
        comparable_close=str(bar.comparable_close) if bar.comparable_close is not None else None,
    )
    state.update(_event_flags(bar))
    return state


def _reversal_hit(state, t_high):
    close = _dec(state.get('comparable_close'))
    return state.get('status') == 'OBSERVED' and (state.get('close_limit_up') is True or (close is not None and t_high is not None and close > t_high))


def _first_hit(states, predicate):
    for offset, state in enumerate(states, 1):
        if predicate(state):
            return offset, state['session']
    return None, None


def _latency_milestone(*, definition, states, max_sessions, predicate, reference_level=None):
    offset, session = _first_hit(states[:max_sessions], predicate)
    window = states[:max_sessions]
    all_mature = len(window) == max_sessions and all(state.get('status') != 'NOT_MATURED' for state in window)
    unavailable = all_mature and any(state.get('status') != 'OBSERVED' for state in window) and offset is None
    return {
        'policy_id': V2_OUTCOME_POLICY_ID,
        'definition': definition,
        'max_sessions': max_sessions,
        'reference_level': str(reference_level) if reference_level is not None else None,
        'session_statuses': window,
        'first_hit_session': session,
        'sessions_to_hit': offset,
        'result': 'HIT' if offset else 'UNAVAILABLE' if unavailable else 'NOT_HIT_WITHIN_WINDOW' if all_mature else 'NOT_MATURED',
        'maturity_status': 'MATURE_VALID' if offset or all_mature else 'NOT_MATURED',
        'parameter_status': 'INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED',
    }


def _v2_setup_specific_outcomes(item, iid, observation_date, calendar, future, bars, evaluated_at, basis, signal_bar):
    try:
        from .stage_aware_momentum import CONFIG as STAGE_AWARE_CONFIG
        outcome_config = STAGE_AWARE_CONFIG['research_seed_parameters'].get('tracking_outcomes', {})
    except Exception:
        outcome_config = {}
    max_repair = int((outcome_config.get('repair_latency') or {}).get('max_sessions', 3))
    max_new_high = int((outcome_config.get('time_to_new_high') or {}).get('max_sessions', 3))
    local_peak_lookback = int((outcome_config.get('time_to_new_high') or {}).get('local_peak_lookback_sessions_excluding_T', 10))
    t_high = signal_bar.comparable_high if signal_bar is not None and signal_bar.comparable_basis == basis else None
    t_close = signal_bar.comparable_close if signal_bar is not None and signal_bar.comparable_basis == basis else None
    t_reference_prev_close = signal_bar.reference_prev_close if signal_bar is not None else None
    t_ohlc = {
        'open': str(signal_bar.open) if signal_bar and signal_bar.open is not None else None,
        'high': str(signal_bar.high) if signal_bar and signal_bar.high is not None else None,
        'low': str(signal_bar.low) if signal_bar and signal_bar.low is not None else None,
        'close': str(signal_bar.close) if signal_bar and signal_bar.close is not None else None,
        'comparable_high': str(t_high) if t_high is not None else None,
        'comparable_close': str(signal_bar.comparable_close) if signal_bar and signal_bar.comparable_close is not None else None,
        'comparable_basis': basis,
    }
    future3 = [_future_state(iid, day, bars, evaluated_at, basis) for day in future[:max(max_repair, max_new_high, 3)]]
    t1_state = future3[0] if future3 else {'status': 'NOT_MATURED', 'session': None}
    t1_mature = t1_state.get('status') != 'NOT_MATURED'
    reversal_value = _reversal_hit(t1_state, t_high) if t1_mature and t1_state.get('status') == 'OBSERVED' else None
    reversal = {
        'policy_id': V2_OUTCOME_POLICY_ID,
        'event_date': str(observation_date),
        'event_identity': item.get('primary_stage') or 'UNCLASSIFIED_STAGE_AWARE_SIGNAL',
        't_ohlc': t_ohlc,
        't_plus_1_ohlc': t1_state,
        'verified_t1_limit_event': t1_state.get('close_limit_up'),
        'reclaim_definition': 'VERIFIED_CLOSE_LIMIT_UP_OR_T_PLUS_1_CLOSE_GT_T_HIGH',
        'result': reversal_value,
        'maturity_status': 'NOT_MATURED' if not t1_mature else 'MATURE_VALID' if reversal_value is not None else 'UNAVAILABLE',
        'parameter_status': 'INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED',
    }

    turn_positive_latency = _latency_milestone(
        definition='RAW_CLOSE_GT_SIGNAL_DAY_REFERENCE_PREV_CLOSE',
        states=future3,
        max_sessions=max_repair,
        reference_level=t_reference_prev_close,
        predicate=lambda state: state.get('status') == 'OBSERVED' and _dec(state.get('close')) is not None and t_reference_prev_close is not None and _dec(state.get('close')) > t_reference_prev_close,
    )
    close_reclaim_latency = _latency_milestone(
        definition='COMPARABLE_CLOSE_GT_SIGNAL_DAY_COMPARABLE_CLOSE',
        states=future3,
        max_sessions=max_repair,
        reference_level=t_close,
        predicate=lambda state: state.get('status') == 'OBSERVED' and _dec(state.get('comparable_close')) is not None and t_close is not None and _dec(state.get('comparable_close')) > t_close,
    )
    drop_day_high_reclaim_latency = _latency_milestone(
        definition='COMPARABLE_HIGH_GT_SIGNAL_DAY_COMPARABLE_HIGH',
        states=future3,
        max_sessions=max_new_high,
        reference_level=t_high,
        predicate=lambda state: state.get('status') == 'OBSERVED' and _dec(state.get('comparable_high')) is not None and t_high is not None and _dec(state.get('comparable_high')) > t_high,
    )

    repair_states = future3[:max_repair]
    first_repair, first_repair_session = close_reclaim_latency['sessions_to_hit'], close_reclaim_latency['first_hit_session']
    repair_latency = {
        'policy_id': V2_OUTCOME_POLICY_ID,
        'repair_definition': 'CLOSE_RECLAIM_SIGNAL_DAY_COMPARABLE_CLOSE',
        'max_sessions': max_repair,
        'session_statuses': repair_states,
        'first_repair_session': first_repair_session,
        'sessions_to_repair': first_repair,
        'result': 'REPAIRED' if first_repair else 'NOT_REPAIRED_WITHIN_WINDOW' if close_reclaim_latency['result'] == 'NOT_HIT_WITHIN_WINDOW' else close_reclaim_latency['result'],
        'maturity_status': close_reclaim_latency['maturity_status'],
        'parameter_status': 'INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED',
    }

    observation_index = calendar.index(observation_date)
    local_peak_bars = [
        bars.get((iid, day)) for day in calendar[max(0, observation_index - local_peak_lookback):observation_index]
    ]
    local_peak_values = [
        bar.comparable_high for bar in local_peak_bars
        if bar is not None and bar.comparable_high is not None and bar.comparable_basis == basis
    ]
    local_peak = max(local_peak_values) if local_peak_values else None
    new_high_states = future3[:max_new_high]
    local_peak_reclaim_latency = _latency_milestone(
        definition='COMPARABLE_HIGH_GT_PRE_DIVERGENCE_LOCAL_PEAK',
        states=future3,
        max_sessions=max_new_high,
        reference_level=local_peak,
        predicate=lambda state: state.get('status') == 'OBSERVED' and _dec(state.get('comparable_high')) is not None and local_peak is not None and _dec(state.get('comparable_high')) > local_peak,
    )
    drop_high_offset, drop_high_session = drop_day_high_reclaim_latency['sessions_to_hit'], drop_day_high_reclaim_latency['first_hit_session']
    local_peak_offset, local_peak_session = local_peak_reclaim_latency['sessions_to_hit'], local_peak_reclaim_latency['first_hit_session']
    new_high_all_mature = len(new_high_states) == max_new_high and all(state.get('status') != 'NOT_MATURED' for state in new_high_states)
    time_to_new_high = {
        'policy_id': V2_OUTCOME_POLICY_ID,
        'max_sessions': max_new_high,
        'drop_day_high': str(t_high) if t_high is not None else None,
        'local_peak_lookback_sessions_excluding_T': local_peak_lookback,
        'pre_divergence_local_peak': str(local_peak) if local_peak is not None else None,
        'session_statuses': new_high_states,
        'drop_day_high_first_session_reached': drop_high_session,
        'drop_day_high_sessions_to_reclaim': drop_high_offset,
        'local_peak_first_session_reached': local_peak_session,
        'local_peak_sessions_to_reclaim': local_peak_offset,
        'drop_day_high_reclaim_latency': drop_day_high_reclaim_latency,
        'local_peak_reclaim_latency': local_peak_reclaim_latency,
        'maturity_status': 'MATURE_VALID' if drop_high_offset or local_peak_offset or new_high_all_mature else 'NOT_MATURED',
        'parameter_status': 'INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED',
    }

    breakout_level = _dec(((item.get('stage_candidates') or {}).get('START') or {}).get('breakout_prior_high'))
    breakout_states = future3[:max_repair]
    if breakout_level is None:
        breakout_hold = {'status': 'NOT_APPLICABLE', 'reason_codes': ['SAM_BREAKOUT_LEVEL_NOT_IN_SIGNAL']}
        breakout_failure = {'status': 'NOT_APPLICABLE', 'reason_codes': ['SAM_BREAKOUT_LEVEL_NOT_IN_SIGNAL']}
    else:
        observed_breakout = [state for state in breakout_states if state.get('status') == 'OBSERVED']
        failed = any((_dec(state.get('comparable_close')) is not None and _dec(state.get('comparable_close')) < breakout_level) for state in observed_breakout)
        all_mature = len(breakout_states) == max_repair and all(state.get('status') != 'NOT_MATURED' for state in breakout_states)
        breakout_hold = {'status': 'MATURE_VALID' if all_mature else 'NOT_MATURED', 'breakout_level': str(breakout_level), 'held_through_window': bool(observed_breakout) and not failed and all_mature}
        breakout_failure = {'status': 'MATURE_VALID' if failed or all_mature else 'NOT_MATURED', 'breakout_level': str(breakout_level), 'failed': failed}

    shadow_rows = [row for row in ((item.get('supply_pressure') or {}).get('upper_shadow_rows') or []) if row.get('long_upper_shadow')]
    levels = [_dec(row.get('shadow_high')) for row in shadow_rows if _dec(row.get('shadow_high')) is not None]
    supply_level = max(levels) if levels else None
    supply_offset, supply_session = _first_hit(future3[:max_repair], lambda state: state.get('status') == 'OBSERVED' and _dec(state.get('comparable_close')) is not None and supply_level is not None and _dec(state.get('comparable_close')) >= supply_level)
    supply_all_mature = len(future3[:max_repair]) == max_repair and all(state.get('status') != 'NOT_MATURED' for state in future3[:max_repair])
    supply_absorption = {
        'policy_id': V2_OUTCOME_POLICY_ID,
        'supply_reference_level': str(supply_level) if supply_level is not None else None,
        'first_absorption_session': supply_session,
        'sessions_to_absorption': supply_offset,
        'result': 'SUPPLY_ABSORBED' if supply_offset else 'SUPPLY_UNRESOLVED' if supply_level is not None and supply_all_mature else 'NOT_MATURED' if supply_level is not None else 'NOT_APPLICABLE',
        'maturity_status': 'MATURE_VALID' if supply_offset or supply_all_mature else 'NOT_MATURED',
        'parameter_status': 'INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED',
    }
    return {
        'policy_id': V2_OUTCOME_POLICY_ID,
        'next_session_reversal': reversal,
        'turn_positive_latency': turn_positive_latency,
        'close_reclaim_latency': close_reclaim_latency,
        'drop_day_high_reclaim_latency': drop_day_high_reclaim_latency,
        'local_peak_reclaim_latency': local_peak_reclaim_latency,
        'repair_latency': repair_latency,
        'time_to_new_high': time_to_new_high,
        'breakout_hold': breakout_hold,
        'breakout_failure': breakout_failure,
        'supply_absorption': supply_absorption,
        'failed_limit_up_repair': {'status': 'UNKNOWN', 'reason_codes': ['SAM_INTRADAY_LIMIT_UP_REPAIR_SEQUENCE_UNAVAILABLE']},
        'temporal_leadership': {'status': 'UNKNOWN', 'reason_codes': ['SAM_INTRADAY_TIMESTAMPS_NOT_IN_MARKET_INPUT']},
        'future_only_policy': 'APPEND_OUTCOME_REVISION_ONLY_DOES_NOT_REWRITE_SIGNAL',
    }


def evaluate_strategy_experiment_outcomes(run, bundle, evaluated_at, *, horizons=DEFAULT_RESEARCH_HORIZONS, _context=None):
    if evaluated_at.tzinfo is None:
        raise ValueError('evaluated_at requires timezone')
    horizons = validate_horizons(horizons)
    observation_date = _day(run['observation_date'])
    context = _outcome_context(bundle, evaluated_at, _context)
    calendar = context.sessions
    if observation_date not in calendar:
        raise ValueError('experiment observation date missing from outcome calendar')
    future = calendar[calendar.index(observation_date) + 1:]
    bars = context.bars
    input_hash = context.input_hash
    values = []
    for sid, items in authoritative_strategy_items(run):
        focus_policy_id, focus_ids = _strategy_focus(run, sid, items)
        for item in items:
            iid = item['instrument_id']
            signal_bar = bars.get((iid, observation_date))
            reference = _reference_close(item)
            basis = item.get('comparable_basis')
            if reference is None and signal_bar is not None:
                reference, basis = signal_bar.comparable_close, signal_bar.comparable_basis
            for horizon in horizons:
                days = future[:horizon]
                end = days[-1] if len(days) == horizon else None
                mature = end is not None and datetime.combine(end, time(15), SH) <= evaluated_at.astimezone(SH)
                states, valid_bars, reasons = [], {}, []
                for day in days:
                    state = {'session': str(day), 'close_limit_up': None, 'close_limit_down': None}
                    bar = bars.get((iid, day))
                    if datetime.combine(day, time(15), SH) > evaluated_at.astimezone(SH):
                        state['status'] = 'NOT_MATURED'
                    elif bar is None or bar.comparable_close is None:
                        state['status'] = 'MISSING_PRICE'
                    elif not basis or bar.comparable_basis != basis:
                        state['status'] = 'COMPANY_ACTION_OR_COMPARABILITY_UNSUPPORTED'
                    else:
                        valid_bars[day] = bar
                        state.update(status='OBSERVED', close=str(bar.close), comparable_close=str(bar.comparable_close))
                        event = limit_event(bar)
                        for field in ('close_limit_up', 'close_limit_down'):
                            event_value = getattr(event, field)
                            state[field] = True if event_value == TRUE else False if event_value == 'FALSE' else None
                        state['event_status'] = 'VERIFIED' if all(state[f] is not None for f in ('close_limit_up', 'close_limit_down')) else 'LIMIT_EVENT_UNVERIFIED'
                    if state['status'] != 'OBSERVED':
                        reasons.append(state['status'])
                    states.append(state)
                metrics = dict.fromkeys(('close_reference_change', 'next_open_gap', 'mfe_bound', 'mae_bound', 'any_close_limit_up', 'any_close_limit_down'))
                reference_ok = reference is not None and reference > 0 and bool(basis)
                if not reference_ok:
                    reasons.append('REFERENCE_COMPARABLE_CLOSE_UNAVAILABLE')
                return_exact = None
                if mature and reference_ok:
                    last = valid_bars.get(end)
                    first = valid_bars.get(days[0]) if days else None
                    # Endpoint return is independent of intermediate gaps and board events.
                    if last is not None:
                        return_exact = last.comparable_close / reference - 1
                        metrics['close_reference_change'] = float(return_exact)
                    if first is not None and first.comparable_open is not None:
                        metrics['next_open_gap'] = _num(first.comparable_open / reference - 1)
                    if len(valid_bars) == horizon and all(b.comparable_high is not None and b.comparable_low is not None for b in valid_bars.values()):
                        metrics['mfe_bound'] = _num(max(b.comparable_high for b in valid_bars.values()) / reference - 1)
                        metrics['mae_bound'] = _num(min(b.comparable_low for b in valid_bars.values()) / reference - 1)
                if mature and len(states) == horizon:
                    for field in ('close_limit_up', 'close_limit_down'):
                        events = [row.get(field) for row in states]
                        metrics['any_' + field] = True if True in events else False if all(v is False for v in events) else None
                metric_states = {key: _metric(value, mature, 'REQUIRED_PRICE_OR_EVENT_EVIDENCE_UNAVAILABLE') for key, value in metrics.items()}
                status = 'NOT_MATURED' if not mature else 'MATURE' if not reasons and metrics['close_reference_change'] is not None else 'CENSORED'
                if mature and not valid_bars:
                    status = 'UNAVAILABLE'
                logical = {'run_id': run['run_id'], 'strategy_id': sid, 'instrument_id': iid, 'observation_date': str(observation_date),
                           'horizon': horizon, 'spec_hash': run.get('spec_hash') or run.get('strategy_spec_hash'),
                           'config_hash': run.get('config_hash'),
                           'parameter_version': run.get('parameter_version') or run.get('config_hash') or run.get('spec_hash') or run.get('strategy_spec_hash'),
                           'implementation_version': run.get('implementation_version'), 'implementation_source_hash': run.get('source_hash'),
                           'metric_policy_id': METRIC_POLICY_ID, 'label_kind': run.get('label_kind', 'SIGNAL')}
                value = {'schema_version': OUTCOME_VERSION, **logical, 'run_id': run['run_id'],
                         'signal_input_id': run.get('input_id'), 'input_id': 'market_' + input_hash, 'input_hash': input_hash,
                         'selection_policy_id': focus_policy_id if iid in focus_ids else None, 'selected_focus': iid in focus_ids,
                         'parent_rank': item.get('rank'), 'parent_score': item.get('S_PV'), 'horizon_unit': 'exchange_trading_sessions',
                         'label_end': str(end) if end else None, 'evaluated_at': evaluated_at.isoformat(),
                         'coverage_type': bundle.coverage_type, 'status': status, 'metrics': metrics, 'metric_states': metric_states,
                         'price_label_status': metric_states['close_reference_change']['status'],
                         'return_exact': str(return_exact) if return_exact is not None else None,
                         'price_direction': 'UP' if return_exact is not None and return_exact > 0 else 'DOWN' if return_exact is not None and return_exact < 0 else 'FLAT' if return_exact is not None else None, 'session_states': states,
                         'reason_codes': sorted(set(reasons)), 'market_edge': 'NOT_ESTABLISHED',
                         'reference_method': 'COMPARABLE_MARKED_PRICES_NOT_ASSUMED_FILLS',
                         'logical_label_id': 'strategy_label_' + digest(logical),
                         'signal_id': 'strategy_signal_' + digest({k: v for k, v in logical.items() if k != 'horizon'})}
                if sid == STAGE_AWARE:
                    setup_outcomes = _v2_setup_specific_outcomes(item, iid, observation_date, calendar, future, bars, evaluated_at, basis, signal_bar)
                    value['setup_specific_outcomes'] = setup_outcomes
                    value['next_session_reversal'] = setup_outcomes['next_session_reversal']
                    value['turn_positive_latency'] = setup_outcomes['turn_positive_latency']
                    value['close_reclaim_latency'] = setup_outcomes['close_reclaim_latency']
                    value['drop_day_high_reclaim_latency'] = setup_outcomes['drop_day_high_reclaim_latency']
                    value['local_peak_reclaim_latency'] = setup_outcomes['local_peak_reclaim_latency']
                    value['repair_latency'] = setup_outcomes['repair_latency']
                    value['time_to_new_high'] = setup_outcomes['time_to_new_high']
                    value['breakout_hold'] = setup_outcomes['breakout_hold']
                    value['breakout_failure'] = setup_outcomes['breakout_failure']
                    value['supply_absorption'] = setup_outcomes['supply_absorption']
                value['observation_id'] = 'strategy_outcome_' + digest(value)
                values.append(value)
    return values


def build_strategy_tracking(run, bundle, evaluated_at, *, horizons=DEFAULT_RESEARCH_HORIZONS,
                            existing_episodes=(), prior_observations=(), episode_events=(), _context=None):
    horizons = validate_horizons(horizons)
    signal_day = _day(run['observation_date'])
    context = _outcome_context(bundle, evaluated_at, _context)
    calendar = context.sessions
    bars = context.bars
    archive_records = []
    for record in (*prior_observations, *episode_events):
        if record.get('state') != 'ARCHIVED':
            continue
        recorded_at = record.get('recorded_at') or record.get('evaluated_at')
        archive_day = _day(record.get('session') or record['effective_session'])
        if recorded_at and _dt(recorded_at) > evaluated_at:
            continue
        if datetime.combine(archive_day, time(15), SH) <= evaluated_at.astimezone(SH):
            archive_records.append(record)
    episodes, observations, signals, shadows = [], [], [], []
    primary_strategy_id = _tracking_strategy_id(run)
    focus_policy_id, focus_ids = _strategy_focus(run, primary_strategy_id, _strategy_items(run, primary_strategy_id))
    for item in _strategy_items(run, primary_strategy_id):
        iid = item['instrument_id']
        if iid not in focus_ids:
            continue
        choices = [e for e in existing_episodes if e['instrument_id'] == iid and e.get('selection_policy_id') == focus_policy_id and e.get('tracking_policy_id') == 'TRACKING_POLICY_PILOT_V0.1' and _day(e['observation_date']) <= signal_day]
        choices.sort(key=lambda e: (e['observation_date'], e['episode_id']))
        previous = choices[-1] if choices else None
        exit_day = None
        if previous:
            exits = [r for r in prior_observations if r.get('episode_id') == previous['episode_id'] and r.get('state') == 'THESIS_INVALIDATED']
            exits += [r for r in archive_records if r.get('episode_id') == previous['episode_id']]
            if exits:
                exit_day = min(_day(r.get('session') or r['effective_session']) for r in exits)
        if previous and (exit_day is None or signal_day <= exit_day):
            episode = previous
        else:
            key = {'instrument_id': iid, 'selection_policy_id': focus_policy_id, 'observation_date': str(signal_day),
                   'tracking_policy_id': 'TRACKING_POLICY_PILOT_V0.1', 'previous_episode_id': previous['episode_id'] if previous else None}
            episode = {'schema_version': TRACKING_VERSION, **key, 'episode_id': 'research_episode_' + digest(key),
                       'run_id': run['run_id'], 'strategy_id': primary_strategy_id, 'pending_horizons': list(horizons),
                       'post_exit_horizons': list(POST_EXIT_HORIZONS), 'started_at': datetime.combine(signal_day, time(15), SH).isoformat(),
                       'market_edge': 'NOT_ESTABLISHED'}
        episodes.append(episode)
        eid = episode['episode_id']
        start = _day(episode['observation_date'])
        signal = {'run_id': run['run_id'], 'instrument_id': iid, 'strategy_id': primary_strategy_id, 'episode_id': eid,
                  'observation_date': str(signal_day), 'selection_policy_id': focus_policy_id}
        signals.append({**signal, 'signal_record_id': 'episode_signal_' + digest(signal)})
        volume_peak, below_streak, cooling_streak, state, actual_exit = None, 0, 0, 'ACTIVE', None
        episode_volume_basis = None
        confirmed_exits = [r for r in prior_observations if r.get('episode_id') == eid and r.get('state') == 'THESIS_INVALIDATED']
        confirmed_exit = min((_day(r['session']) for r in confirmed_exits), default=None)
        archives = [_day(r.get('session') or r['effective_session']) for r in archive_records if r.get('episode_id') == eid]
        for index, day in enumerate(calendar):
            if day < start or datetime.combine(day, time(15), SH) > evaluated_at.astimezone(SH):
                continue
            bar = bars.get((iid, day))
            basis = bar.comparable_basis if bar else None
            close = bar.comparable_close if bar and basis else None
            reason = []
            previous20 = [bars.get((iid, d)) for d in calendar[max(0, index - 19):index + 1]]
            comparable20 = len(previous20) == 20 and all(b is not None and b.comparable_close is not None and b.comparable_basis == basis and basis for b in previous20)
            ma20 = sum((b.comparable_close for b in previous20), Decimal(0)) / 20 if comparable20 else None
            base = bars.get((iid, calendar[index - 5])) if index >= 5 else None
            r5 = close / base.comparable_close - 1 if close is not None and base and base.comparable_close and base.comparable_basis == basis else None
            # Volume needs its own certified no-action/adjusted basis; prices alone do not certify it.
            volume_basis = getattr(bar, 'comparable_volume_basis', None) if bar else None
            if not volume_basis and basis and 'NO_ACTION' in basis:
                volume_basis = basis
            if episode_volume_basis is None and volume_basis is not None:
                episode_volume_basis = volume_basis
            volume = bar.volume_shares if bar and volume_basis and volume_basis == episode_volume_basis else None
            if volume is not None:
                volume_peak = max(volume_peak or Decimal(0), volume)
            ratio = volume / volume_peak if volume is not None and volume_peak else None
            below_streak = below_streak + 1 if close is not None and ma20 is not None and close < ma20 else 0
            cooling_streak = cooling_streak + 1 if ratio is not None and ratio <= Decimal('.30') and r5 is not None and close is not None else 0
            if actual_exit is None:
                if archives and day >= min(archives):
                    state, actual_exit = 'ARCHIVED', day
                    reason.append('MANUAL_ARCHIVE')
                elif below_streak >= 2 or confirmed_exit is not None and day >= confirmed_exit:
                    state, actual_exit = 'THESIS_INVALIDATED', day
                    reason.append('TWO_CONSECUTIVE_CLOSES_BELOW_OWN_MA20')
                elif cooling_streak >= 3 and r5 is not None and r5 <= 0:
                    state = 'COOLING'
                    reason.append('THREE_SESSION_VOLUME_SHRINK_AND_R5_NON_POSITIVE')
                elif state == 'COOLING' and ratio is not None and r5 is not None:
                    state = 'ACTIVE'
            if close is None:
                reason.append('MISSING_COMPARABLE_PRICE')
            if ma20 is None:
                reason.append('MA20_CONTIGUOUS_HISTORY_UNAVAILABLE')
            if volume is None:
                reason.append('COMPARABLE_VOLUME_BASIS_UNAVAILABLE')
            row = {'episode_id': eid, 'run_id': run['run_id'], 'instrument_id': iid, 'session': str(day),
                   'evaluated_at': evaluated_at.isoformat(), 'state': state, 'research_state': state,
                   'data_state': 'UNAVAILABLE' if close is None else 'PARTIAL' if ma20 is None or volume is None else 'CURRENT',
                   'status': 'MISSING_PRICE' if close is None else 'OBSERVED', 'close': str(bar.close) if bar and bar.close else None,
                   'comparable_close': str(close) if close else None, 'ma20': _num(ma20), 'r5': _num(r5),
                   'volume_ratio_since_signal_peak': _num(ratio), 'below_ma20_verified_streak': below_streak,
                   'cooling_verified_streak': cooling_streak, 'reason_codes': reason, 'exit_session': str(actual_exit) if actual_exit else None}
            row['observation_id'] = 'research_episode_observation_' + digest(row)
            observations.append(row)
        if actual_exit:
            exit_bar = bars.get((iid, actual_exit))
            shadow_run = {**{key: run.get(key) for key in ('parameter_version', 'config_hash', 'spec_hash', 'strategy_spec_hash', 'implementation_version', 'source_hash')},
                          'run_id': 'shadow_' + eid, 'input_id': run.get('input_id'), 'observation_date': str(actual_exit),
                          'label_kind': 'POST_EXIT_SHADOW', 'results': {'PV_OBSERVATION_V1': {'items': [{
                              'instrument_id': iid, 'rank': 1, 'comparable_close': str(exit_bar.comparable_close) if exit_bar and exit_bar.comparable_close else None,
                              'comparable_basis': exit_bar.comparable_basis if exit_bar else None}]}}}
            for label in evaluate_strategy_experiment_outcomes(shadow_run, bundle, evaluated_at, horizons=POST_EXIT_HORIZONS, _context=context):
                label['episode_id'] = eid
                label['observation_id'] = 'strategy_outcome_' + digest({k:v for k,v in label.items() if k != 'observation_id'})
                shadows.append(label)
    value = {'schema_version': TRACKING_VERSION, 'run_id': run['run_id'], 'input_id': 'market_' + context.input_hash,
             'signal_input_id': run.get('input_id'), 'evaluated_at': evaluated_at.isoformat(), 'observation_date': str(signal_day),
             'episode_count': len(episodes), 'observation_count': len(observations), 'episodes': episodes, 'observations': observations,
             'signals': signals, 'shadow_outcomes': shadows, 'horizons': list(horizons), 'market_edge': 'NOT_ESTABLISHED',
             'status': 'TRACKED' if episodes else 'NO_FOCUS_SELECTIONS'}
    value['tracking_run_id'] = 'strategy_tracking_' + digest(value)
    return value


def select_label_revisions(outcomes, evaluated_at, metric='close_reference_change'):
    """The latest valid revision for each logical metric as of report cutoff."""
    grouped = {}
    for row in outcomes:
        if _dt(row['evaluated_at']) > evaluated_at:
            continue
        key = row.get('logical_label_id') or digest({k: row.get(k) for k in ('run_id', 'strategy_id', 'instrument_id', 'observation_date', 'horizon', 'parameter_version', 'config_hash', 'implementation_version', 'implementation_source_hash', 'spec_hash', 'metric_policy_id', 'label_kind')})
        grouped.setdefault(key, []).append(row)
    selected = []
    for revisions in grouped.values():
        valid = [r for r in revisions if r.get('metrics', {}).get(metric) is not None and r.get('status') != 'NOT_MATURED']
        selected.append(max(enumerate(valid or revisions), key=lambda pair: (pair[1]['evaluated_at'], pair[0]))[1])
    return selected


def _statistics(rows):
    valid = [r for r in rows if r.get('metrics', {}).get('close_reference_change') is not None and r.get('status') != 'NOT_MATURED']
    returns = sorted(r['metrics']['close_reference_change'] for r in valid)
    positive = sum(r > 0 for r in returns)
    dates = {}
    for row in valid:
        dates.setdefault(row['observation_date'], []).append(row['metrics']['close_reference_change'])
    return {'signals': len(rows), 'unique_instruments': len({r['instrument_id'] for r in rows}), 'signal_dates': len({r['observation_date'] for r in rows}),
            'mature_valid': len(valid), 'not_matured': sum(r['status'] == 'NOT_MATURED' for r in rows),
            'missing_or_incomparable': len(rows) - len(valid) - sum(r['status'] == 'NOT_MATURED' for r in rows),
            'positive': positive, 'zero': sum(r == 0 for r in returns), 'negative': sum(r < 0 for r in returns),
            'up_rate': positive / len(returns) if returns else None, 'mean_return': sum(returns) / len(returns) if returns else None,
            'median_return': median(returns) if returns else None, 'p10_return': returns[int((len(returns)-1)*.1)] if returns else None,
            'date_equal_up_rate': sum(sum(r > 0 for r in rs)/len(rs) for rs in dates.values())/len(dates) if dates else None,
            'date_equal_mean_return': sum(sum(rs)/len(rs) for rs in dates.values())/len(dates) if dates else None}


def _episode_signal_index(labels, tracking_runs, runs):
    """Find formal first signals across supplied history before slicing any window."""
    run_index = {run['run_id']: run for run in runs}
    by_signal, starts, first_signal, first_labels = {}, {}, {}, {}

    def order(run_id, day):
        run = run_index.get(run_id, {})
        return (str(day), str(run.get('decision_cutoff') or run.get('cutoff') or run.get('generated_at') or ''), run_id)

    for tracking in tracking_runs:
        for episode in tracking.get('episodes', []):
            eid, day = episode['episode_id'], episode.get('observation_date')
            if day:
                starts[eid] = min(starts.get(eid, str(day)), str(day))
                if episode.get('run_id') and episode.get('instrument_id'):
                    run_id = episode['run_id']
                    by_signal[(run_id, episode['instrument_id'])] = eid
                    first_signal[eid] = min(first_signal.get(eid, order(run_id, day)), order(run_id, day))
        for signal in tracking.get('signals', []):
            eid, run_id = signal['episode_id'], signal['run_id']
            by_signal[(run_id, signal['instrument_id'])] = eid
            day = signal.get('observation_date') or run_index.get(run_id, {}).get('observation_date')
            if day:
                starts[eid] = min(starts.get(eid, str(day)), str(day))
                first_signal[eid] = min(first_signal.get(eid, order(run_id, day)), order(run_id, day))
    for label in labels:
        if label.get('label_kind', 'SIGNAL') != 'SIGNAL' or label.get('strategy_id') != 'PV_OBSERVATION_V1':
            continue
        eid = by_signal.get((label['run_id'], label['instrument_id']))
        if eid is None:
            continue
        # Episode metadata may prove an earlier start for which no label was supplied.
        day, key = str(label['observation_date']), order(label['run_id'], label['observation_date'])
        if day != starts.get(eid) or key != first_signal.get(eid):
            continue
        first_labels[(eid, label['horizon'])] = label
    return {'by_signal': by_signal, 'starts': starts, 'first_labels': first_labels}


def _episode_window_cohorts(rows, strategy_id, horizon, dates, index):
    window_first, unlinked = {}, 0
    for row in sorted(rows, key=lambda r: (r['observation_date'], r['run_id'])):
        eid = index['by_signal'].get((row['run_id'], row['instrument_id'])) if strategy_id in {'PV_OBSERVATION_V1', FOCUS} else None
        if eid is None:
            unlinked += 1
        else:
            window_first.setdefault(eid, row)
    first, missing = [], 0
    for eid in window_first:
        label = index['first_labels'].get((eid, horizon))
        if label is None or strategy_id == FOCUS and not label.get('selected_focus'):
            missing += 1
        elif label['observation_date'] in dates:
            first.append(label)
    start = min(dates) if dates else None
    before = sum(index['starts'].get(eid, start) < start for eid in window_first) if start else 0
    return {'first': first, 'window_first': list(window_first.values()),
            'episode_count': len(window_first), 'signals_without_episode': unlinked,
            'continuing_episode_count': before, 'started_before_window_episode_count': before,
            'started_in_window_episode_count': sum(index['starts'].get(eid) in dates for eid in window_first),
            'episode_first_label_unavailable_count': missing}


def build_strategy_feedback_report(run, outcomes, tracking_runs, evaluated_at, *, runs=None, sessions=None):
    runs = list(runs or [run])
    conformant = [r for r in runs if r.get('spec_conformance') != 'SPEC_NONCONFORMANT' and r.get('status') != 'SPEC_NONCONFORMANT' and r.get('implementation_version') != 'R05_STRATEGY_EXPERIMENTS_V2']
    allowed = {r['run_id'] for r in conformant}
    labels = select_label_revisions([r for r in outcomes if r.get('run_id') in allowed or r.get('label_kind') == 'POST_EXIT_SHADOW'], evaluated_at)
    signals = [r for r in labels if r.get('label_kind', 'SIGNAL') == 'SIGNAL']
    mature = [r for r in signals if r.get('metrics', {}).get('close_reference_change') is not None and r['status'] != 'NOT_MATURED']
    pending = [r for r in signals if r['status'] == 'NOT_MATURED']
    mature_ids = {r['observation_id'] for r in mature}
    by_horizon = {}
    for row in signals:
        bucket = by_horizon.setdefault(str(row['horizon']), {'MATURE': 0, 'CENSORED': 0, 'UNAVAILABLE': 0, 'NOT_MATURED': 0})
        status = 'MATURE' if row['observation_id'] in mature_ids else row['status']
        bucket[status] = bucket.get(status, 0) + 1
    end_days = sorted(set(_day(d) for d in (sessions or [r['observation_date'] for r in runs]) if _day(d) <= evaluated_at.astimezone(SH).date()))
    episode_index = _episode_signal_index(signals, tracking_runs, runs)
    run_index = {r['run_id']: r for r in runs}
    windows = {}
    for length in (5, 20, 60):
        days = set(str(d) for d in end_days[-length:])
        selected_runs = [r for r in runs if str(r['observation_date']) in days]
        selected = [r for r in signals if r['observation_date'] in days]
        groups = {}
        for row in selected:
            groups.setdefault((row['strategy_id'], row['horizon']), []).append(row)
            if row.get('selected_focus') and row['strategy_id'] == 'PV_OBSERVATION_V1':
                groups.setdefault((FOCUS, row['horizon']), []).append(row)
        baseline_id = 'ELIGIBLE_UNIVERSE_EQUAL_WEIGHT_PRICE_DIAGNOSTIC'
        benchmark = {}
        for row in selected:
            value = row.get('metrics', {}).get('close_reference_change')
            if row['strategy_id'] == baseline_id and value is not None and row['status'] != 'NOT_MATURED':
                benchmark.setdefault((row['observation_date'], row['horizon']), []).append(value)
        stats = []
        for (sid, horizon), rows in groups.items():
            cohorts = _episode_window_cohorts(rows, sid, horizon, days, episode_index)
            dates = {}
            states = {}
            for row in rows:
                states.setdefault((run_index.get(row['run_id'], {}).get('market_state') or {}).get('state', 'UNKNOWN'), []).append(row)
                if row.get('metrics', {}).get('close_reference_change') is not None and row['status'] != 'NOT_MATURED':
                    dates.setdefault(row['observation_date'], []).append(row['metrics']['close_reference_change'])
            relative = [{'observation_date': day, 'strategy_mean':sum(values)/len(values),
                         'benchmark_mean':sum(benchmark[(day,horizon)])/len(benchmark[(day,horizon)]),
                         'strategy_valid':len(values),'benchmark_valid':len(benchmark[(day,horizon)]),
                         'excess_mean':sum(values)/len(values)-sum(benchmark[(day,horizon)])/len(benchmark[(day,horizon)])}
                        for day,values in dates.items() if (day,horizon) in benchmark]
            stats.append({'strategy_id': sid, 'horizon': horizon, 'pooled_signals': _statistics(rows),
                          'episode_first_signal': {**_statistics(cohorts['first']), 'status':'EVALUATED' if cohorts['first'] else 'UNAVAILABLE',
                              'scope':'TRUE_EPISODE_FIRST_SIGNAL_DATE_WITHIN_WINDOW' if sid in {'PV_OBSERVATION_V1',FOCUS} else 'NO_INDEPENDENT_STRATEGY_EPISODE_POLICY'},
                          'episode_window_first_signal': {**_statistics(cohorts['window_first']),
                              'scope':'FIRST_SIGNAL_OBSERVED_INSIDE_THIS_WINDOW_INCLUDES_CONTINUING_EPISODES'},
                          **{key:value for key,value in cohorts.items() if key not in {'first','window_first'}},
                          'relative_eligible_universe_price_benchmark': {'status':'EVALUATED' if relative else 'UNAVAILABLE',
                              'policy':'SAME_SIGNAL_DATE_HORIZON_AND_FROZEN_ELIGIBILITY_PRICE_BASIS', 'by_date':relative,
                              'date_equal_excess_mean':sum(r['excess_mean'] for r in relative)/len(relative) if relative else None},
                          'market_state_strata': {state:_statistics(values) for state,values in states.items()}})
        windows[str(length)] = {'requested_exchange_sessions': length, 'available_calendar_sessions': len(days),
                               'observed_run_dates': sorted({r['observation_date'] for r in selected_runs}),
                               'missing_run_dates': sorted(days - {r['observation_date'] for r in selected_runs}),
                               'date_diagnostics': [{'run_id': r['run_id'], 'observation_date': r['observation_date'],
                                  'strategies': {sid: {'actual_count': len(items), 'status': (r.get('results', {}).get(sid) or {}).get('status')} for sid, items in authoritative_strategy_items(r)},
                                  'focus_count': len(_focus_ids(r)), 'comparison': r.get('comparison')} for r in selected_runs],
                               'statistics': stats, 'same_date_common_eligibility': [{'run_id':r['run_id'], 'observation_date':r['observation_date'],
                                   'frozen_eligible_ids':r.get('qualification',{}).get('eligible_ids'),
                                   'strategy_actual_coverage':{sid:result.get('qualified_ids') for sid,result in r.get('results',{}).items() if not result.get('compatibility_alias')},
                                   'selected_intersection_used_as_benchmark':False} for r in selected_runs],
                               'market_state_stratification': {'status':'EVALUATED' if any(r.get('market_state') for r in selected_runs) else 'UNAVAILABLE',
                                  'unknown_state_kept_separate':True},
                               'relative_benchmark': {'status':'EVALUATED' if benchmark else 'UNAVAILABLE',
                                   'reason':None if benchmark else 'MATCHED_ELIGIBLE_UNIVERSE_LABELS_REQUIRED'}}
    shadow = [r for r in labels if r.get('label_kind') == 'POST_EXIT_SHADOW']
    value = {'schema_version': REVIEW_VERSION, 'run_id': run['run_id'], 'observation_date': run.get('observation_date'),
             'evaluated_at': evaluated_at.isoformat(), 'status': 'HAS_MATURED_OBSERVATIONS' if mature else 'PENDING_MARKET_VALIDATION',
             'market_edge': 'NOT_ESTABLISHED', 'counts': {'outcome_observations': len(signals), 'stored_revisions': len(outcomes),
             'mature': len(mature), 'censored_or_unavailable': len(signals)-len(mature)-len(pending), 'not_matured': len(pending),
             'focus_episode_count': len({e['episode_id'] for t in tracking_runs for e in t.get('episodes', [])})},
             'by_horizon': by_horizon, 'rolling_windows': windows,
             'revision_selection': [{'logical_label_id': r.get('logical_label_id'), 'observation_id': r['observation_id']} for r in labels],
             'post_exit_analysis': {'5': _statistics([r for r in shadow if r['horizon'] == 5]), '10': _statistics([r for r in shadow if r['horizon'] == 10])},
             'uncertainty': {'unit': 'DATE_BLOCK_WITH_EPISODE_DEPENDENCE_DISCLOSED', 'status': 'INSUFFICIENT_DATES_FOR_STABLE_INFERENCE',
                             'validation_gap': 'PURGE_OR_GAP_AT_LEAST_EACH_LABEL_HORIZON'},
             'interpretation': {'periodic_review': '以逻辑价格标签的有效修订计算；零收益计入分母，不计上涨。',
                               'next_hypothesis': '只在另一个未看过的日期窗口预注册和验证焦点相对同条件PV的差异；保持现行参数。'}}
    value['review_id'] = 'strategy_review_' + digest(value)
    return value
