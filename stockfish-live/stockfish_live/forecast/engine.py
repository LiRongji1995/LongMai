"""Calendar-aware forecast orchestration and independent mature label creation."""
from __future__ import annotations

from datetime import datetime, time
from copy import deepcopy

from ..calendar import TradingCalendar
from ..common import LiveError, aware, digest
from ..contracts.forecast import ForecastConfig, ForecastResult, HistoricalState, MatureOutcome, PathPoint
from ..contracts.market import MinuteBar, SourceReceipt, validate_receipt
from ..contracts.quality import effective_pit_status
from ..models.regime import classify_regime
from ..models.similarity import PreparedHistory, SimilarityModel


def horizon_targets(calendar: TradingCalendar, decision_at: datetime) -> dict[str, datetime]:
    """+30/+60 mean completed trading minutes, never lunch wall-clock minutes."""
    decision_at = aware(decision_at)
    calendar._require_day(decision_at.date())
    if decision_at not in calendar.ticks(decision_at.date()):
        raise LiveError("INVALID_DECISION_TIME")
    remaining = [minute for minute in calendar.minutes(decision_at.date()) if minute > decision_at]
    result = {f"{n}m": remaining[n - 1] for n in (30, 60) if len(remaining) >= n}
    for label in ("11:30", "14:00", "15:00"):
        hour, minute = map(int, label.split(":"))
        target = decision_at.replace(hour=hour, minute=minute)
        if target > decision_at:
            result[label] = target
    return result


def state_from_market_state(state: dict, instrument_id: str, decision_at: datetime) -> HistoricalState | None:
    """Bridge the independent MARKET_FEATURES_V1 output without parallel features."""
    index = state.get("indices", {}).get(instrument_id)
    if not index or index.get("current_price") is None:
        return None
    decision_at = aware(decision_at)
    features = {**state.get("features", {}), **index.get("features", {})}
    payload = dict(instrument_id=instrument_id, decision_at=decision_at,
        available_at=state.get("available_at", state.get("as_of", decision_at)),
        current_price=index["current_price"], features=features,
        feature_version=state.get("feature_version", "MARKET_FEATURES_V1"),
        pit_status=state["pit_status"], warnings=tuple(state.get("warnings", ())),
        context={"input_hash": state.get("input_hash"), "data_cutoff": state.get("data_cutoff"),
                 "configuration_hash": state.get("config_hash"),
                 "sentiment": deepcopy(state.get("sentiment")), "sectors": deepcopy(state.get("sectors"))})
    return HistoricalState(state_id=digest(payload), **payload)


def mature_outcomes(states, bars, receipts, calendar: TradingCalendar, *, as_of: datetime) -> tuple[MatureOutcome, ...]:
    """Create labels only after all future-path bars and parent receipts are visible.

    Incomplete paths yield no label. The actual last availability of the full
    path, state and calendar is retained. No result is filled with a zero return.
    """
    as_of = aware(as_of)
    receipt_map = {r.source_id: SourceReceipt.model_validate(r.model_dump()) for r in receipts}
    visible = {}
    for raw in bars:
        if aware(raw.available_at) > as_of or aware(raw.timestamp) > as_of:
            continue
        bar = MinuteBar.model_validate(raw.model_dump())
        receipt = receipt_map.get(bar.source_id)
        if receipt is None:
            raise LiveError("SOURCE_RECEIPT_MISSING")
        if receipt.available_at > as_of or receipt.received_at > as_of:
            continue
        validate_receipt(bar, receipt, as_of)
        if bar.quality == "INVALID" or bar.price_adjustment != "RAW" or bar.close is None:
            continue
        calendar.require_session(bar.timestamp.date(), as_of, (bar.exchange,))
        calendar.validate_bar_time(bar.timestamp)
        key = (bar.instrument_id, bar.timestamp)
        if key in visible and visible[key] != bar:
            raise LiveError("OUTCOME_BAR_CONFLICT")
        visible[key] = bar
    results = []
    for raw in states:
        if aware(raw.available_at) > as_of:
            continue
        state = HistoricalState.model_validate(raw.model_dump())
        calendar.require_session(state.decision_at.date(), as_of, (state.instrument_id[-2:],))
        for horizon, target in horizon_targets(calendar, state.decision_at).items():
            if target > as_of:
                continue
            times = [t for t in calendar.minutes(target.date()) if state.decision_at < t <= target]
            path_bars = [visible.get((state.instrument_id, t)) for t in times]
            if not path_bars or any(b is None for b in path_bars):
                continue
            prices = [state.current_price] + [b.close for b in path_bars]
            parent_receipts = [receipt_map[b.source_id] for b in path_bars]
            status = effective_pit_status([state.pit_status, calendar.pit_status] +
                [x.pit_status for x in (*path_bars, *parent_receipts)])
            available = max([state.available_at, calendar.available_at] +
                [b.available_at for b in path_bars] + [r.available_at for r in parent_receipts])
            results.append(MatureOutcome(state_id=state.state_id, instrument_id=state.instrument_id,
                decision_at=state.decision_at, horizon=horizon, target_at=target, available_at=available,
                anchor_price=state.current_price, target_price=prices[-1],
                regime=classify_regime(prices[0], prices[-1], max(prices), min(prices),
                                       gap_pct=state.features.get("gap_pct")),
                future_path=tuple(PathPoint(timestamp=b.timestamp, price=b.close) for b in path_bars),
                pit_status=status, source_ids=tuple(sorted({b.source_id for b in path_bars}))))
    return tuple(sorted(results, key=lambda y: (y.decision_at, y.state_id, y.horizon)))


class ForecastEngine:
    def __init__(self, calendar: TradingCalendar, config: ForecastConfig | None = None):
        self.calendar = calendar
        self.config = ForecastConfig.model_validate((config or ForecastConfig()).model_dump())
        self.model = SimilarityModel(self.config)

    def prepare_history(self, states, outcomes) -> PreparedHistory:
        """Validate/index the archive once before collection or replay starts."""
        prepared = PreparedHistory(states, outcomes)
        prepared.validate_targets(self.calendar, horizon_targets)
        return prepared

    def predict(self, query: HistoricalState, states, outcomes=(), *, as_of: datetime) -> ForecastResult:
        query = HistoricalState.model_validate(query.model_dump())
        as_of = aware(as_of)
        if query.decision_at > as_of or query.available_at > as_of:
            raise LiveError("FUTURE_FEATURE_ACCESS_DENIED")
        self.calendar.require_session(query.decision_at.date(), as_of, (query.instrument_id[-2:],))
        targets = horizon_targets(self.calendar, query.decision_at)
        if isinstance(states, PreparedHistory):
            if outcomes is not None and tuple(outcomes):
                raise LiveError("PREPARED_HISTORY_ARGUMENT_CONFLICT")
            outcomes = ()
            states.validate_targets(self.calendar, horizon_targets)
        else:
            states, outcomes = tuple(states), tuple(outcomes)
            # Validate target identity independently of user-supplied label names.
            for label in outcomes:
                if aware(label.available_at) <= as_of:
                    expected = horizon_targets(self.calendar, label.decision_at).get(label.horizon)
                    if expected != label.target_at:
                        raise LiveError("OUTCOME_HORIZON_MISMATCH")
        warnings = list(query.warnings)
        decision_clock = query.decision_at.time()
        scheduled = query.decision_at.minute % 5 == 0 and decision_clock >= time(9, 35)
        if not scheduled:
            warnings.append("FORECAST_NOT_SCHEDULED")
        early = decision_clock in (time(9, 35), time(9, 40))
        if early:
            warnings.append("LOW_CONFIDENCE_EARLY")
        remaining = [t for t in self.calendar.minutes(query.decision_at.date()) if query.decision_at < t <= as_of]
        stale = len(remaining) > self.config.freshness_minutes
        if stale:
            warnings.append("QUERY_FEATURES_STALE")
        forecasts = tuple(self.model.predict(query, states, outcomes, as_of=as_of,
                          horizon=h, target_at=target) for h, target in targets.items()) if scheduled else ()
        if early or stale:
            forecasts = tuple(h.model_copy(update={"confidence": "LOW"}) if h.status == "AVAILABLE" else h
                              for h in forecasts)
        for forecast in forecasts:
            warnings.extend(forecast.warnings)
        available = [h for h in forecasts if h.status == "AVAILABLE"]
        status = ("NOT_SCHEDULED" if not scheduled else "INSUFFICIENT" if not available else
                  "AVAILABLE" if len(available) == len(forecasts) else "PARTIAL")
        rank = {"INSUFFICIENT": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
        confidence = min((h.confidence for h in available), key=rank.get) if available else "INSUFFICIENT"
        provenance = effective_pit_status([query.pit_status, self.calendar.pit_status] +
            [h.pit_status for h in forecasts])
        payload = dict(instrument_id=query.instrument_id, decision_at=query.decision_at, as_of=as_of,
            current_price=query.current_price, state_id=query.state_id,
            feature_version=query.feature_version, input_hash=query.context.get("input_hash"),
            data_cutoff=query.context.get("data_cutoff", query.decision_at),
            configuration_hash=self.config.configuration_hash, horizons=forecasts, confidence=confidence,
            status=status, warnings=tuple(sorted(set(warnings))), pit_status=provenance)
        return ForecastResult(forecast_id=digest(payload), **payload)
