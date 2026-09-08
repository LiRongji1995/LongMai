"""Append-only exchange-session observations. Price references are not user fills."""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator

from .contracts import Frozen, CandidateRun, MarketInput, DailyBar, digest
from .events import limit_event
from .visibility import EvidenceResolver

SH = ZoneInfo('Asia/Shanghai')
HORIZONS = (1, 2, 3, 5, 10)


class OutcomeObservation(Frozen):
    observation_id: str
    run_id: str
    instrument_id: str
    family: str
    schema_version: str = 'TRADING_SESSION_OBSERVATION_V1'
    horizon: int
    horizon_unit: Literal['exchange_trading_sessions'] = 'exchange_trading_sessions'
    observation_date: date
    label_end: date | None
    evaluated_at: datetime
    coverage_type: str
    status: Literal['MATURE', 'NOT_MATURED', 'CENSORED', 'UNAVAILABLE']
    structure_success: bool | None
    structure_label: str
    future_three_session_limit_up: bool | None
    metrics: dict[str, float | bool | None]
    session_states: tuple[dict[str, Any], ...]
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    input_hash: str
    reference_method: str = 'COMPARABLE_MARKED_PRICES_NOT_ASSUMED_FILLS'
    execution_status: str = 'NOT_VERIFIED'
    path_order: str = 'DAILY_OHLC_DOES_NOT_IDENTIFY_INTRADAY_ORDER'
    t_plus_one_note: str = 'Next-open to same-day-close is a price reference, not realizable T+1 intraday profit.'

    @field_validator('horizon')
    @classmethod
    def allowed_horizon(cls, value):
        if value not in HORIZONS:
            raise ValueError('unsupported exchange-session observation horizon')
        return value


def _states(bundle: MarketInput, iid: str, day: date, cutoff: datetime, resolver=None):
    from .history_artifacts import history_resolver
    resolver=resolver or history_resolver(bundle,cutoff=cutoff,allow_future_sessions=True)
    outer=bundle.universe.model_dump(mode='json',exclude={'securities'})
    if not resolver.is_visible(outer,require_complete=True):
        return None
    from .history_artifacts import iter_history_securities
    records = [s for s in iter_history_securities(bundle,instrument_id=iid,start=day,end=day) if s.instrument_id == iid and s.effective_from <= day
               and (s.effective_to is None or day < s.effective_to)
               and resolver.is_visible(s,instrument_id=iid,session=day,require_complete=True)]
    if not records:
        return None
    latest = max(s.ingested_at for s in records)
    matches = [s for s in records if s.ingested_at == latest]
    if len(set(digest(s) for s in matches)) > 1:
        return None
    return matches[0]


def _bar_map(bundle: MarketInput, cutoff: datetime,resolver=None):
    if bundle.history_artifact_ref:
        from .history_artifacts import OutcomeBarMapping
        return OutcomeBarMapping(bundle,cutoff,resolver=resolver,strict_ingested=True)
    result = {}
    resolver=resolver or EvidenceResolver(bundle.receipts,cutoff=cutoff,coverage_type=bundle.coverage_type,sessions=bundle.sessions)
    for bar in bundle.bars:
        if not resolver.is_visible(bar,instrument_id=bar.instrument_id,session=bar.session,require_session=True):
            continue
        key = (bar.instrument_id, bar.session)
        previous = result.get(key)
        if previous is None or previous.ingested_at < bar.ingested_at:
            result[key] = bar
        elif previous.ingested_at == bar.ingested_at and previous != bar:
            raise ValueError('conflicting outcome price revision')
    return result


def _limit(bar: DailyBar | None, side='up'):
    if bar is None:
        return None
    event = limit_event(bar)
    value = event.close_limit_up if side == 'up' else event.close_limit_down
    return True if value == 'TRUE' else False if value in {'FALSE', 'NOT_APPLICABLE'} else None


def _price_valid(bar):
    raw = (bar.open, bar.high, bar.low, bar.close)
    if bar.quality == 'INVALID' or any(p is None for p in raw):
        return False
    if any(p / bar.tick_size != (p / bar.tick_size).to_integral_value() for p in raw):
        return False
    if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
        return False
    comparable = (bar.comparable_open, bar.comparable_high, bar.comparable_low, bar.comparable_close)
    if all(p is not None for p in comparable):
        return bar.comparable_low <= min(bar.comparable_open, bar.comparable_close) <= max(bar.comparable_open, bar.comparable_close) <= bar.comparable_high
    return True


def evaluate_outcomes(run: CandidateRun, bundle: MarketInput, evaluated_at: datetime,
                      horizons: tuple[int, ...] = HORIZONS) -> tuple[OutcomeObservation, ...]:
    if evaluated_at.tzinfo is None or evaluated_at < run.generated_at:
        raise ValueError('outcome evaluation precedes original publication')
    if any(h not in HORIZONS for h in horizons):
        raise ValueError('unsupported trading-session horizon')
    calendar = bundle.sessions
    if run.observation_date not in calendar:
        raise ValueError('original observation date missing from calendar')
    future = calendar[calendar.index(run.observation_date) + 1:]
    from .history_artifacts import history_resolver
    resolver=history_resolver(bundle,cutoff=evaluated_at,allow_future_sessions=True)
    bars = _bar_map(bundle, evaluated_at,resolver)
    results = []
    for item in run.items:
        reference = bars.get((item.instrument_id, run.observation_date))
        # Re-read reference must agree with the frozen input; a later adjustment
        # regime cannot silently restate an old run's price comparisons.
        frozen_close = item.features.get('comparable_close')
        frozen_last = item.features.get('previous_run_end_close')
        reference_close = Decimal(str(frozen_close.value)) if frozen_close and frozen_close.value is not None else None
        target_close = Decimal(str(frozen_last.value)) if frozen_last and frozen_last.value is not None else None
        if item.thesis_conditions and 'reference_comparable_close' in item.thesis_conditions[0]:
            frozen = item.thesis_conditions[0]
            reference_close = Decimal(str(frozen['reference_comparable_close'])) if frozen['reference_comparable_close'] is not None else None
            target_close = Decimal(str(frozen['previous_run_end_close'])) if frozen['previous_run_end_close'] is not None else None
        reference_mismatch = reference is not None and reference.comparable_close != reference_close
        for horizon in horizons:
            days = future[:horizon]
            end = days[-1] if len(days) == horizon else None
            mature = end is not None and datetime.combine(end, time(15), SH) <= evaluated_at
            reasons, records, refs = [], [], []
            if end is None:
                reasons.append('CALENDAR_HORIZON_UNAVAILABLE')
            if not mature:
                reasons.append('NOT_MATURED')
            observed_bars = []
            for day in days:
                state = _states(bundle, item.instrument_id, day, evaluated_at,resolver)
                bar = bars.get((item.instrument_id, day))
                event = None
                if datetime.combine(day, time(15), SH) > evaluated_at:
                    status = 'NOT_MATURED'
                elif state is None or state.suspended is None:
                    status = 'SECURITY_STATUS_UNKNOWN'
                elif state.delisted_on is not None and day >= state.delisted_on:
                    status = 'DELISTED'
                elif state.suspended:
                    status, event = 'SUSPENDED', False
                elif bar is None or bar.close is None or bar.quality != 'VALID':
                    status = 'MISSING_PRICE'
                elif not _price_valid(bar):
                    status = 'INVALID_PRICE'
                elif reference is None or reference_close is None or reference.comparable_basis is None or bar.comparable_basis != reference.comparable_basis or bar.comparable_close is None:
                    status = 'COMPANY_ACTION_OR_COMPARABILITY_UNSUPPORTED'
                    event = _limit(bar)
                else:
                    status, event = 'OBSERVED', _limit(bar)
                    observed_bars.append(bar)
                one_price = None
                if bar:
                    refs.extend(bar.source_refs)
                    if event is True:
                        one_price = bar.open == bar.high == bar.low == bar.close == bar.limit_up
                    elif event is False:
                        one_price = False
                down = False if status == 'SUSPENDED' else _limit(bar, 'down') if status in {'OBSERVED', 'COMPANY_ACTION_OR_COMPARABILITY_UNSUPPORTED'} else None
                records.append({'session': day.isoformat(), 'status': status, 'close_limit_up': event,
                                'close_limit_down': down, 'one_price_up': one_price})
                if status != 'OBSERVED':
                    reasons.append(status)
            metrics = dict(close_reference_change=None, next_open_gap=None, next_open_reference_change=None,
                           mfe_bound=None, mae_bound=None, close_max_drawdown=None, any_close_limit_up=None,
                           any_close_limit_down=None, one_price_up_count=None)
            if mature and records:
                events = [r['close_limit_up'] for r in records]
                downs = [r['close_limit_down'] for r in records]
                metrics['any_close_limit_up'] = True if True in events else False if all(v is False for v in events) else None
                metrics['any_close_limit_down'] = True if True in downs else False if all(v is False for v in downs) else None
                metrics['one_price_up_count'] = sum(r['one_price_up'] is True for r in records) if all(r['one_price_up'] is not None for r in records) else None
            if mature and len(observed_bars) == horizon and reference_close and reference:
                first, last = observed_bars[0], observed_bars[-1]
                if reference.comparable_close is None or reference.comparable_close != reference_close:
                    reasons.append('REFERENCE_REVISION_MISMATCH')
                else:
                    metrics['close_reference_change'] = float(last.comparable_close / reference_close - 1)
                    if first.comparable_open:
                        metrics['next_open_gap'] = float(first.comparable_open / reference_close - 1)
                        metrics['next_open_reference_change'] = float(last.comparable_close / first.comparable_open - 1)
                    if all(b.comparable_high is not None and b.comparable_low is not None for b in observed_bars):
                        metrics['mfe_bound'] = float(max(b.comparable_high for b in observed_bars) / reference_close - 1)
                        metrics['mae_bound'] = float(min(b.comparable_low for b in observed_bars) / reference_close - 1)
                    peak, worst = reference_close, Decimal(0)
                    for bar in observed_bars:
                        peak = max(peak, bar.comparable_close)
                        worst = min(worst, bar.comparable_close / peak - 1)
                    metrics['close_max_drawdown'] = float(worst)
            label_days = 1 if item.primary_family in {'ONE_TO_TWO', 'TWO_TO_THREE', 'HIGH_BOARD'} else 3
            label_name = 'NEXT_SESSION_CLOSE_LIMIT_UP' if label_days == 1 else 'WITHIN_3_SESSIONS_CLOSE_LIMIT_UP' if item.primary_family == 'TREND_NM' else 'WITHIN_3_SESSIONS_RECLAIM_PREVIOUS_RUN_CLOSE'
            success = None
            three_limit = None
            if mature and horizon >= label_days:
                label_records = records[:label_days]
                if item.primary_family != 'REBOUND_SETUP':
                    events = [r['close_limit_up'] for r in label_records]
                    success = True if True in events else False if all(v is False for v in events) else None
                elif target_close is not None and not reference_mismatch:
                    reclaim = []
                    for row in label_records:
                        bar = bars.get((item.instrument_id, date.fromisoformat(row['session'])))
                        reclaim.append(bar.comparable_close >= target_close if row['status'] == 'OBSERVED' else False if row['status'] == 'SUSPENDED' else None)
                    success = True if True in reclaim else False if all(v is False for v in reclaim) else None
            if mature and horizon >= 3:
                events = [r['close_limit_up'] for r in records[:3]]
                three_limit = True if True in events else False if all(v is False for v in events) else None
            status = 'NOT_MATURED' if not mature else 'MATURE' if not reasons else 'CENSORED'
            if mature and not observed_bars and all(r['status'] in {'SECURITY_STATUS_UNKNOWN', 'MISSING_PRICE'} for r in records):
                status = 'UNAVAILABLE'
            if any(r['one_price_up'] for r in records):
                reasons.append('ONE_PRICE_EXECUTION_UNCONFIRMED')
            value = dict(run_id=run.run_id, instrument_id=item.instrument_id, family=item.primary_family,
                         horizon=horizon, observation_date=run.observation_date, label_end=end,
                         evaluated_at=evaluated_at, coverage_type=bundle.coverage_type, status=status,
                         structure_success=success, structure_label=label_name, future_three_session_limit_up=three_limit,
                         metrics=metrics, session_states=tuple(records), reason_codes=tuple(sorted(set(reasons))),
                         evidence_refs=tuple(sorted(set(refs))), input_hash=digest(bundle))
            provisional = OutcomeObservation(observation_id='pending', **value)
            results.append(provisional.model_copy(update={'observation_id': 'outcome_' + digest(provisional.model_dump(mode='json', exclude={'observation_id'}))}))
    return tuple(results)
