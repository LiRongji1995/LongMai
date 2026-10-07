"""Streaming V2 facts match stateless V1; append clocks remain fail closed."""
from datetime import timedelta

import pytest
from pydantic import ValidationError

from stockfish_live.common import LiveError, canonical_json
from stockfish_live.features.market import MarketStateEngine
from stockfish_live.features.streaming import StreamingMarketStateEngine
from test_market_state import STOCKS, clock, context_bundle, market_inputs


def assert_facts(left, right):
    if isinstance(left, dict):
        assert left.keys() == right.keys()
        for key in left:
            assert_facts(left[key], right[key])
    elif isinstance(left, list):
        assert len(left) == len(right)
        for a, b in zip(left, right):
            assert_facts(a, b)
    elif isinstance(left, float):
        assert left == pytest.approx(right, abs=1e-10, rel=1e-10)
    else:
        assert left == right


def delta_at(bars, now):
    return [bar for bar in bars if bar.timestamp == now]


def step(stream, now, bars, receipts, **kwargs):
    delta = list(bars)
    parents = {bar.source_id: receipts[bar.source_id] for bar in delta if bar.source_id in receipts}
    return stream.build(now, delta, parents, **kwargs)


@pytest.mark.parametrize("defect", [None, "gap", "partial", "missing_context"])
def test_every_minute_stream_facts_match_stateless_reference(market_inputs, defect):
    calendar, scope, bars, receipts = market_inputs
    context = None if defect == "missing_context" else context_bundle()
    if defect == "gap":
        bars = [bar for bar in bars if not (bar.instrument_id == STOCKS[0] and bar.timestamp == clock(9, 48))]
    if defect == "partial":
        bars = [bar.model_copy(update={"close": None, "turnover": None, "quality": "PARTIAL"})
                if bar.instrument_id == STOCKS[0] and bar.timestamp == clock(9, 48) else bar for bar in bars]
    reference = MarketStateEngine(calendar, scope, context=context)
    stream = StreamingMarketStateEngine(calendar, scope, context=context)
    cumulative = []
    for now in calendar.ticks(calendar.sessions[0])[:32]:
        delta = delta_at(bars, now)
        parents = {bar.source_id: receipts[bar.source_id] for bar in delta}
        cumulative.extend(delta)
        expected = reference.build(now, cumulative, receipts)
        actual = step(stream, now, delta, parents)
        for key in ("indices", "breadth", "sentiment", "sectors", "environments", "features", "warnings"):
            assert_facts(actual[key], expected[key])
        assert actual["feature_version"] == "MARKET_FEATURES_V2"
        assert actual["config_hash"] != expected["config_hash"]
        assert actual["input_hash_kind"] == "APPEND_ONLY_DELTA_CONTEXT_CHAIN_V1"
    assert all(len(value.recent) <= 31 and len(value.changes) <= 15 for value in stream._accumulators.values())
    assert len(stream._views) <= 31


def test_duplicate_batch_and_future_parent_are_atomic_and_retryable(market_inputs):
    calendar, scope, bars, receipts = market_inputs
    first = delta_at(bars, clock(9, 31))
    parent = receipts[first[0].source_id]
    stream = StreamingMarketStateEngine(calendar, scope)
    with pytest.raises(LiveError, match="STREAM_LATE_OR_DUPLICATE_BAR"):
        step(stream, clock(9, 31), [*first, first[0]], {parent.source_id: parent})
    assert stream._last_as_of is None
    assert all(value.count == 0 for value in stream._accumulators.values())
    future = parent.model_copy(update={"received_at": clock(9, 32), "available_at": clock(9, 32)})
    with pytest.raises(LiveError, match="FUTURE_ACCESS_DENIED"):
        step(stream, clock(9, 31), first, {future.source_id: future})
    good = step(stream, clock(9, 31), first, {parent.source_id: parent})
    assert good["indices"]["000001.SH"]["current_price"] is not None


def test_stream_rejects_rewinds_duplicates_and_old_bar_backfills(market_inputs):
    calendar, scope, bars, receipts = market_inputs
    stream = StreamingMarketStateEngine(calendar, scope)
    first = delta_at(bars, clock(9, 32))
    step(stream, clock(9, 32), first, receipts)
    with pytest.raises(LiveError, match="STREAM_CLOCK_NOT_ADVANCING"):
        step(stream, clock(9, 32), (), {})
    with pytest.raises(LiveError, match="STREAM_LATE_OR_DUPLICATE_BAR"):
        step(stream, clock(9, 33), delta_at(bars, clock(9, 31)), receipts)
    assert stream._last_as_of == clock(9, 32)


def test_future_bar_and_bypassed_invalid_model_copy_are_rejected(market_inputs):
    calendar, scope, bars, receipts = market_inputs
    stream = StreamingMarketStateEngine(calendar, scope)
    with pytest.raises(LiveError, match="FUTURE_ACCESS_DENIED"):
        step(stream, clock(9, 31), delta_at(bars, clock(9, 32)), receipts)
    row = delta_at(bars, clock(9, 31))[0].model_copy(update={"close": True})
    with pytest.raises(ValidationError):
        step(stream, clock(9, 31), (row,), receipts)


def test_no_context_revision_can_rewrite_previously_consumed_facts(market_inputs):
    calendar, scope, bars, receipts = market_inputs
    context = context_bundle()
    stream = StreamingMarketStateEngine(calendar, scope, context=context)
    step(stream, clock(9, 31), delta_at(bars, clock(9, 31)), receipts)
    revised = context.model_copy(update={"reference_prices": tuple(row.model_copy(update={"previous_close": 11})
        if row.instrument_id == STOCKS[0] else row for row in context.reference_prices)})
    with pytest.raises(LiveError, match="STREAM_CONTEXT_HISTORY_REMOVED"):
        step(stream, clock(9, 32), delta_at(bars, clock(9, 32)), receipts, context=revised)
    assert stream._last_as_of == clock(9, 31)


def test_newly_supplied_past_context_is_not_retroactively_invented(market_inputs):
    calendar, scope, bars, receipts = market_inputs
    stream = StreamingMarketStateEngine(calendar, scope)
    step(stream, clock(9, 31), delta_at(bars, clock(9, 31)), receipts)
    with pytest.raises(LiveError, match="STREAM_RETROACTIVE_CONTEXT_INPUT"):
        step(stream, clock(9, 32), delta_at(bars, clock(9, 32)), receipts, context=context_bundle())


def test_new_actual_context_capture_is_allowed_and_does_not_backfill_old_marginals(market_inputs):
    calendar, scope, bars, receipts = market_inputs
    stream = StreamingMarketStateEngine(calendar, scope)
    for now in calendar.minutes(calendar.sessions[0])[:30]:
        step(stream, now, delta_at(bars, now), receipts)
    actual = step(stream, clock(10, 1), delta_at(bars, clock(10, 1)), receipts,
        context=context_bundle(available_at=clock(10, 1)))
    assert actual["breadth"]["status"] == "AVAILABLE"
    assert actual["sectors"]["sectors"][0]["relative_strength_change_5m_pct"] is None
    assert actual["sentiment"]["previous_state"] == "UNKNOWN"


def test_returned_snapshot_mutation_cannot_change_internal_prior_view(market_inputs):
    calendar, scope, bars, receipts = market_inputs
    stream = StreamingMarketStateEngine(calendar, scope, context=context_bundle())
    for now in calendar.minutes(calendar.sessions[0])[:26]:
        previous = step(stream, now, delta_at(bars, now), receipts)
    previous["breadth"]["advance_ratio"] = 0.99
    previous["sectors"]["sectors"][0]["members"].append("600099.SH")
    for now in calendar.minutes(calendar.sessions[0])[26:31]:
        actual = step(stream, now, delta_at(bars, now), receipts)
    assert actual["sentiment"]["advance_ratio_change_5m"] == 0
    assert actual["sectors"]["sectors"][0]["relative_strength_change_5m_pct"] == pytest.approx(0)


def test_chain_is_deterministic_and_binds_observation_cutoffs(market_inputs):
    calendar, scope, bars, receipts = market_inputs
    a = StreamingMarketStateEngine(calendar, scope)
    b = StreamingMarketStateEngine(calendar, scope)
    for now in calendar.minutes(calendar.sessions[0])[:3]:
        delta = delta_at(bars, now)
        parents = {bar.source_id: receipts[bar.source_id] for bar in delta}
        left = a.build(now, delta, parents)
        right = b.build(now, list(reversed(delta)), parents)
        assert canonical_json(left) == canonical_json(right)
    c = StreamingMarketStateEngine(calendar, scope)
    changed = step(c, clock(9, 33) + timedelta(seconds=1), bars[:27], receipts)
    assert changed["input_hash"] != left["input_hash"]


def test_earlier_limit_counterevidence_cannot_enter_stream_and_failure_is_atomic(market_inputs):
    calendar, scope, bars, receipts = market_inputs
    stream = StreamingMarketStateEngine(calendar, scope, context=context_bundle())
    first = delta_at(bars, clock(9, 31))
    invalid = [bar.model_copy(update={"high": 12.1}) if bar.instrument_id == STOCKS[0] else bar for bar in first]
    with pytest.raises(LiveError, match="PRICE_OUTSIDE_CONTEXT_LIMITS"):
        step(stream, clock(9, 31), invalid, receipts)
    assert stream._last_as_of is None
    good = step(stream, clock(9, 31), first, receipts)
    assert good["breadth"]["status"] == "AVAILABLE"
