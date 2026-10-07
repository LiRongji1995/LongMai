"""Check stored labels against immutable minute evidence before model loading."""
from ..common import LiveError, aware
from ..contracts.quality import effective_pit_status
from ..forecast.engine import horizon_targets
from ..models.regime import classify_regime
from .parquet_store import ParquetStore


def validate_history_provenance(root, states, outcomes, calendar, scope, *, as_of, yield_outcomes=False):
    """This is archive verification, run before inference, never feature input.

    A self-consistent research manifest alone does not establish that a label
    has an actual parent, a complete future path or the declared availability.
    ``yield_outcomes`` returns a checked iterator for startup preparation.
    Its consumer must exhaust it before publishing a usable model. The normal
    call consumes the same proof and returns the original report contract.
    """
    checked = _validated_outcomes(root, states, outcomes, calendar, scope, as_of=aware(as_of))
    if yield_outcomes:
        return checked
    while True:
        try:
            next(checked)
        except StopIteration as completed:
            return completed.value


def _validated_outcomes(root, states, outcomes, calendar, scope, *, as_of):
    store = ParquetStore(root)
    types = {item.instrument_id: item.asset_type for item in scope.instruments}
    state_map, parents, targets, windows, anchors = {}, {}, {}, {}, {}
    cached_day, rows, session = None, {}, ()
    seen_parents, seen_days, outcome_count = set(), set(), 0
    for state in states:
        if types.get(state.instrument_id) != "INDEX":
            raise LiveError("INSTRUMENT_TYPE_MISMATCH")
        calendar.require_session(state.decision_at.date(), state.available_at, (state.instrument_id[-2:],))
        if scope.available_at > state.available_at:
            raise LiveError("SCOPE_NOT_YET_AVAILABLE")
        if state.state_id in state_map and state_map[state.state_id] != state:
            raise LiveError("STATE_ID_CONFLICT")
        state_map[state.state_id] = state
    for label in outcomes:
        state = state_map.get(label.state_id)
        if state is None:
            raise LiveError("OUTCOME_STATE_MISSING")
        if (state.instrument_id != label.instrument_id or state.decision_at != label.decision_at
            or state.current_price != label.anchor_price):
            raise LiveError("OUTCOME_STATE_MISMATCH")
        if not label.source_ids:
            raise LiveError("SOURCE_RECEIPT_MISSING")
        if label.available_at > as_of or state.available_at > as_of:
            raise LiveError("FUTURE_ACCESS_DENIED")
        day = label.decision_at.date()
        if day != cached_day:
            # Keep one day's raw prices and source receipts, even if partitions
            # arrive in a different session order. Global sets retain IDs only.
            parents.clear()
            targets.clear()
            windows.clear()
            anchors.clear()
            rows.clear()
            session = calendar.minutes(day)
            rows = {(bar.instrument_id, bar.timestamp): bar for bar in store.read_day(day)}
            cached_day = day
            seen_days.add(day)
        for source in label.source_ids:
            if source not in parents:
                parents[source] = store._source(source)
            parent = parents[source]
            if any(value is not None and value > label.available_at for value in (
                parent.received_at, parent.available_at, parent.source_timestamp)):
                raise LiveError("OUTCOME_PARENT_AFTER_LABEL")
            seen_parents.add(source)
        if label.decision_at not in targets:
            targets[label.decision_at] = horizon_targets(calendar, label.decision_at)
        if targets[label.decision_at].get(label.horizon) != label.target_at:
            raise LiveError("OUTCOME_HORIZON_MISMATCH")
        window = (label.decision_at, label.target_at)
        if window not in windows:
            windows[window] = tuple(t for t in session if label.decision_at < t <= label.target_at)
        times = windows[window]
        path = [rows.get((label.instrument_id, moment)) for moment in times]
        if not path or any(bar is None or bar.close is None or bar.price_adjustment != "RAW" or bar.quality == "INVALID" for bar in path):
            raise LiveError("OUTCOME_SOURCE_PATH_UNAVAILABLE")
        if set(label.source_ids) != {bar.source_id for bar in path}:
            raise LiveError("OUTCOME_SOURCE_PATH_MISMATCH")
        if max([state.available_at, calendar.available_at] + [bar.available_at for bar in path]) > label.available_at:
            raise LiveError("OUTCOME_PARENT_AFTER_LABEL")
        if state.decision_at not in anchors:
            anchors[state.decision_at] = max((moment for moment in session if moment <= state.decision_at), default=None)
        anchor = rows.get((state.instrument_id, anchors[state.decision_at]))
        if anchor is None or anchor.close != state.current_price or anchor.available_at > state.available_at:
            raise LiveError("OUTCOME_ANCHOR_SOURCE_MISMATCH")
        if path[-1].close != label.target_price:
            raise LiveError("OUTCOME_TARGET_SOURCE_MISMATCH")
        if label.future_path and [(point.timestamp, point.price) for point in label.future_path] != [(bar.timestamp, bar.close) for bar in path]:
            raise LiveError("OUTCOME_SOURCE_PATH_MISMATCH")
        prices = [state.current_price] + [bar.close for bar in path]
        regime = classify_regime(prices[0], prices[-1], max(prices), min(prices), gap_pct=state.features.get("gap_pct"))
        if label.regime is not None and label.regime != regime:
            raise LiveError("OUTCOME_REGIME_SOURCE_MISMATCH")
        consumed = [state.pit_status, calendar.pit_status, scope.pit_status,
            *(bar.pit_status for bar in path), *(parents[source].pit_status for source in label.source_ids)]
        if effective_pit_status([label.pit_status, *consumed]) != label.pit_status:
            raise LiveError("OUTCOME_PIT_STATUS_UPGRADED")
        outcome_count += 1
        yield label
    return {"schema_version": "LIVE_LABEL_PROOF_V1", "states": len(state_map),
        "outcomes": outcome_count, "source_receipts": len(seen_parents), "sessions": len(seen_days)}
