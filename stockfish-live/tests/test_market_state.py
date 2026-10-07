"""TEST_FIXTURE only: factual missingness, parent clocks and denominators."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from stockfish_live.calendar import TradingCalendar
from stockfish_live.common import LiveError, canonical_json, digest
from stockfish_live.contracts.context import (
    ContextBundle, DailyContext, ReferencePrice, SectorMembership, SecurityStatus,
    TurnoverHistory, UniverseCoverage,
)
from stockfish_live.contracts.market import DatasetScope, Instrument, MinuteBar, SourceReceipt
from stockfish_live.features.market import INDEX_IDS, MarketFeatureConfig, MarketStateEngine
from stockfish_live.features.sentiment import SentimentConfig, calculate_sentiment
from stockfish_live.features.sector import _correlation, marginal_state

TZ = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 9, 30)
KNOWN = datetime(2026, 9, 30, 9, 0, tzinfo=TZ)
STOCKS = ("600001.SH", "600002.SH", "600003.SH", "600004.SH")


def clock(hour, minute):
    return datetime(2026, 9, 30, hour, minute, tzinfo=TZ)


@pytest.fixture
def market_inputs():
    calendar = TradingCalendar(schema_version="LIVE_CALENDAR_V1", sessions=(DAY,), coverage_start=DAY,
        coverage_end=DAY, exchanges=("SH", "SZ"), source="TEST_FIXTURE explicit session",
        available_at=KNOWN, pit_status="TEST_FIXTURE")
    instruments = tuple(Instrument(instrument_id=identity, asset_type="INDEX") for identity in INDEX_IDS)
    instruments += tuple(Instrument(instrument_id=identity, asset_type="STOCK") for identity in STOCKS)
    scope = DatasetScope(calendar_version=calendar.version, instruments=instruments, available_at=KNOWN, pit_status="TEST_FIXTURE")
    bars, receipts = [], {}
    for n, minute in enumerate(calendar.minutes(DAY)[:31], 1):
        source_id = digest({"TEST_FIXTURE": minute})
        parent = SourceReceipt(source_id=source_id, provider="mock", source_timestamp=minute,
            source_timestamp_basis="PUBLISHED", received_at=minute, available_at=minute, pit_status="TEST_FIXTURE")
        receipts[source_id] = parent
        for item in instruments:
            index = item.asset_type == "INDEX"
            base = 1000.0 if index else 10.0
            sign = -1 if item.instrument_id == STOCKS[1] else 1
            close = base + sign * n * (1.0 if index else 0.02)
            opening = base + sign * (n - 1) * (1.0 if index else 0.02)
            bars.append(MinuteBar(instrument_id=item.instrument_id, exchange=item.instrument_id[-2:],
                symbol=item.instrument_id[:6], asset_type=item.asset_type, timestamp=minute,
                open=opening, high=max(opening, close) + 0.01, low=min(opening, close) - 0.01, close=close,
                volume=1000, turnover=10000, provider="mock", source_timestamp=minute,
                source_timestamp_basis="PUBLISHED", available_at=minute, source_id=source_id,
                quality="VALID", pit_status="TEST_FIXTURE"))
    return calendar, scope, bars, receipts


def context_bundle(*, missing_status=(), missing_membership=(), full_market=False, available_at=KNOWN):
    source_id = digest({"TEST_FIXTURE_CONTEXT": available_at})
    parent = SourceReceipt(source_id=source_id, provider="mock", source_timestamp=available_at,
        source_timestamp_basis="PUBLISHED", received_at=available_at, available_at=available_at, pit_status="TEST_FIXTURE")
    metadata = {"source_id": source_id, "provider": "mock", "source_timestamp": available_at,
        "source_timestamp_basis": "PUBLISHED", "available_at": available_at, "pit_status": "TEST_FIXTURE",
        "effective_from": clock(0, 0)}
    references = tuple(ReferencePrice(instrument_id=identity, trading_date=DAY,
        previous_close=1000 if identity in INDEX_IDS else 10,
        limit_up=None if identity in INDEX_IDS else 12, limit_down=None if identity in INDEX_IDS else 8,
        **metadata) for identity in (*INDEX_IDS, *STOCKS))
    statuses = tuple(SecurityStatus(instrument_id=identity, trading_eligible=True,
        is_st=identity == STOCKS[2], suspended=identity == STOCKS[3], size_bucket="LARGE" if n == 0 else "SMALL",
        previous_limit_up=n == 0, **metadata) for n, identity in enumerate(STOCKS) if identity not in missing_status)
    memberships = tuple(SectorMembership(instrument_id=identity, classification_version="TEST_FIXTURE_SW_1",
        sector_id="TEST_SECTOR", sector_name="测试行业", **metadata) for identity in STOCKS if identity not in missing_membership)
    historical = tuple(TurnoverHistory(instrument_id=identity, observation_date=DAY - timedelta(days=offset),
        minute_index=31, cumulative_turnover=155000, **metadata) for identity in (*INDEX_IDS, *STOCKS) for offset in range(1, 6))
    daily = tuple(DailyContext(instrument_id=identity, based_on_date=DAY - timedelta(days=1),
        long_return_20d_pct=4, medium_return_5d_pct=-2, yesterday_sentiment="WEAK", **metadata) for identity in INDEX_IDS)
    coverage = (UniverseCoverage(trading_date=DAY, coverage="COMPLETE_MARKET", instrument_ids=STOCKS, **metadata),) if full_market else ()
    return ContextBundle(receipts=(parent,), reference_prices=references, security_statuses=statuses,
        sector_memberships=memberships, turnover_history=historical, daily_context=daily, universe_coverage=coverage)


def build(market_inputs, context=None, *, bars=None, now=None, config=None):
    calendar, scope, all_bars, receipts = market_inputs
    engine = MarketStateEngine(calendar, scope, context=context, config=config)
    return engine.build(now or clock(10, 1), all_bars if bars is None else bars, receipts)


def test_without_context_only_observed_intraday_features_are_available(market_inputs):
    state = build(market_inputs)
    assert tuple(state["indices"]) == INDEX_IDS
    index = state["indices"]["000001.SH"]
    assert index["current_price"] == 1031
    assert index["features"]["return_from_open_pct"] == pytest.approx(3.1)
    assert index["features"]["return_pct"] is None
    assert index["features"]["gap_pct"] is None
    assert state["breadth"]["status"] == "UNAVAILABLE"
    assert state["breadth"]["advance_count"] is None
    assert state["breadth"]["limit_up_count"] is None
    assert state["sentiment"]["state"] == "UNKNOWN"
    assert state["environments"]["long"]["state"] == "UNKNOWN"
    assert any(value is not None for value in state["features"].values())
    assert state["market_edge"] == "NOT_ESTABLISHED"


def test_sourced_prices_eligibility_and_declared_denominator(market_inputs):
    state = build(market_inputs, context_bundle())
    breadth = state["breadth"]
    assert breadth["universe_declared_count"] == 4
    assert breadth["qualified_count"] == 2  # explicit ST and suspension facts exclude two
    assert breadth["return_observed_count"] == 2
    assert breadth["advance_count"] == breadth["decline_count"] == 1
    assert breadth["advance_ratio"] == 0.5
    assert breadth["cumulative_turnover"] == 620000
    assert breadth["market_turnover_ratio"] == 2
    assert breadth["full_market_status"] == "UNAVAILABLE"
    assert breadth["full_market_denominator"] is None
    assert state["indices"]["000001.SH"]["features"]["return_pct"] == pytest.approx(3.1)
    assert state["environments"]["long"]["state"] == "UP"
    assert state["environments"]["medium"]["state"] == "DOWN"
    assert state["environments"]["long"]["probabilities"] is None
    assert state["sentiment"]["next_state_status"] == "UNAVAILABLE_NO_MATURE_LABELS"


def test_full_market_needs_inventory_and_complete_eligibility_and_prices(market_inputs):
    state = build(market_inputs, context_bundle(full_market=True))
    assert state["breadth"]["full_market_status"] == "AVAILABLE"
    assert state["breadth"]["full_market_denominator"] == 2
    missing = build(market_inputs, context_bundle(full_market=True, missing_status=(STOCKS[1],)))
    assert missing["breadth"]["full_market_status"] == "UNAVAILABLE"
    assert missing["breadth"]["eligibility_unknown_count"] == 1
    assert missing["features"]["breadth_advance_ratio"] is None
    assert missing["sentiment"]["state"] == "UNKNOWN"


def test_complete_inventory_with_extra_members_cannot_be_claimed_as_complete(market_inputs):
    bundle = context_bundle(full_market=True)
    coverage = bundle.universe_coverage[0].model_copy(update={"instrument_ids": (*STOCKS, "600099.SH")})
    state = build(market_inputs, bundle.model_copy(update={"universe_coverage": (coverage,)}))
    assert state["breadth"]["full_market_status"] == "UNAVAILABLE"


@pytest.mark.parametrize("unknown_field", ["is_st", "suspended", "trading_eligible"])
def test_unknown_security_fact_is_not_false(market_inputs, unknown_field):
    bundle = context_bundle()
    statuses = list(bundle.security_statuses)
    statuses[0] = statuses[0].model_copy(update={unknown_field: None})
    state = build(market_inputs, bundle.model_copy(update={"security_statuses": tuple(statuses)}))
    assert state["breadth"]["eligibility_unknown_count"] == 1
    assert state["sentiment"]["state"] == "UNKNOWN"


def test_explicit_disqualification_is_sufficient_even_with_other_unknown_flags(market_inputs):
    bundle = context_bundle()
    statuses = list(bundle.security_statuses)
    statuses[2] = statuses[2].model_copy(update={"trading_eligible": None, "suspended": None})
    statuses[3] = statuses[3].model_copy(update={"trading_eligible": None, "is_st": None})
    state = build(market_inputs, bundle.model_copy(update={"security_statuses": tuple(statuses)}))
    assert state["breadth"]["qualified_count"] == 2
    assert state["breadth"]["eligibility_unknown_count"] == 0


def test_future_context_capture_does_not_backfill_old_market_state(market_inputs):
    baseline = build(market_inputs)
    future = build(market_inputs, context_bundle(available_at=clock(10, 2)))
    assert canonical_json(future) == canonical_json(baseline)


def test_expired_membership_is_not_current_membership(market_inputs):
    bundle = context_bundle()
    rows = tuple(row.model_copy(update={"effective_to": clock(10, 0)}) for row in bundle.sector_memberships)
    state = build(market_inputs, bundle.model_copy(update={"sector_memberships": rows}))
    assert state["sectors"]["status"] == "UNAVAILABLE"
    assert state["sectors"]["membership_missing_count"] == 2


def test_missing_membership_does_not_become_a_healthy_empty_industry(market_inputs):
    state = build(market_inputs, context_bundle(missing_membership=(STOCKS[1],)))
    assert state["sectors"]["status"] == "PARTIAL"
    assert state["sectors"]["membership_missing_count"] == 1
    sector = state["sectors"]["sectors"][0]
    assert sector["dispersion_pct"] is None
    assert sector["internal_correlation"] is None


def test_sector_marginal_strength_and_momentum_use_trading_minutes(market_inputs):
    state = build(market_inputs, context_bundle())
    sector = state["sectors"]["sectors"][0]
    assert sector["member_count"] == 2
    assert sector["sector_return_pct"] == pytest.approx(0)
    assert sector["relative_strength_vs_market_pct"] == pytest.approx(0)
    assert sector["relative_strength_change_5m_pct"] == pytest.approx(0)
    assert sector["relative_strength_change_15m_pct"] == pytest.approx(0)
    assert sector["relative_strength_change_30m_pct"] == pytest.approx(0)
    assert sector["momentum_5m_pct"] is not None
    assert sector["momentum_15m_pct"] is not None
    assert sector["momentum_30m_pct"] is not None
    assert sector["dispersion_pct"] == pytest.approx(6.2)
    assert sector["internal_correlation"] is not None
    assert -1.00000001 <= sector["internal_correlation"] <= 1.00000001
    assert sector["continuation_probabilities"] is None


def test_newly_visible_membership_does_not_backfill_marginal_change(market_inputs):
    bundle = context_bundle(available_at=clock(10, 0))
    state = build(market_inputs, bundle)
    sector = state["sectors"]["sectors"][0]
    assert sector["relative_strength_vs_market_pct"] is not None
    assert sector["relative_strength_change_5m_pct"] is None
    assert state["environments"]["long"]["state"] == "UNKNOWN"  # daily arrived after open


def test_gap_does_not_create_zero_turnover_or_complete_window(market_inputs):
    all_bars = market_inputs[2]
    rows = [bar for bar in all_bars if not (bar.instrument_id == "000001.SH" and bar.timestamp == clock(9, 59))]
    index = build(market_inputs, context_bundle(), bars=rows)["indices"]["000001.SH"]
    assert index["cumulative_turnover"] is None
    assert index["features"]["turnover_ratio"] is None
    assert index["features"]["return_5m_pct"] is None
    assert index["features"]["realized_volatility_pct"] is None
    assert index["day_high"] is None


def test_stale_last_price_is_not_current_model_price(market_inputs):
    rows = [bar for bar in market_inputs[2] if not (bar.instrument_id == "000001.SH" and bar.timestamp == clock(10, 1))]
    index = build(market_inputs, context_bundle(), bars=rows)["indices"]["000001.SH"]
    assert index["current_price"] is None
    assert index["status"] == "UNAVAILABLE"
    assert index["features"]["return_pct"] is None


def test_context_parent_after_record_is_rejected_even_if_construct_bypasses_validation(market_inputs):
    bundle = context_bundle()
    parent = bundle.receipts[0].model_copy(update={"received_at": clock(10, 2), "available_at": clock(10, 2)})
    forged = bundle.model_copy(update={"receipts": (parent,)})
    with pytest.raises(LiveError, match="CONTEXT_PARENT_AFTER_RECORD"):
        build(market_inputs, forged)


def test_missing_context_parent_rejected(market_inputs):
    bundle = context_bundle().model_copy(update={"receipts": ()})
    with pytest.raises(LiveError, match="CONTEXT_RECEIPT_MISSING"):
        build(market_inputs, bundle)


def test_reference_price_conflicts_with_observed_price_fail_closed(market_inputs):
    bundle = context_bundle()
    rows = tuple(row.model_copy(update={"limit_up": 10.1}) if row.instrument_id == STOCKS[0] else row
                 for row in bundle.reference_prices)
    with pytest.raises(LiveError, match="PRICE_OUTSIDE_CONTEXT_LIMITS"):
        build(market_inputs, bundle.model_copy(update={"reference_prices": rows}))


@pytest.mark.parametrize("kind", ["daily", "turnover"])
def test_today_or_future_history_cannot_be_used_as_past_environment(market_inputs, kind):
    bundle = context_bundle()
    field = "daily_context" if kind == "daily" else "turnover_history"
    rows = list(getattr(bundle, field))
    date_field = "based_on_date" if kind == "daily" else "observation_date"
    rows[0] = rows[0].model_copy(update={date_field: DAY})
    with pytest.raises(LiveError, match="NOT_HISTORICAL"):
        build(market_inputs, bundle.model_copy(update={field: tuple(rows)}))


def test_future_bar_is_rejected(market_inputs):
    with pytest.raises(LiveError, match="FUTURE_ACCESS_DENIED"):
        build(market_inputs, now=clock(10, 0))


def test_duplicate_feature_bar_rejected(market_inputs):
    with pytest.raises(LiveError, match="DUPLICATE_FEATURE_BAR"):
        build(market_inputs, bars=[*market_inputs[2], market_inputs[2][0]])


def test_qfq_cannot_enter_raw_state(market_inputs):
    rows = list(market_inputs[2])
    rows[0] = rows[0].model_copy(update={"price_adjustment": "QFQ"})
    with pytest.raises(LiveError, match="FEATURE_REQUIRES_RAW"):
        build(market_inputs, bars=rows)


def test_rule_hash_and_determinism(market_inputs):
    context = context_bundle()
    state = build(market_inputs, context)
    assert canonical_json(build(market_inputs, context, bars=list(reversed(market_inputs[2])))) == canonical_json(state)
    revised = build(market_inputs, context, config=MarketFeatureConfig(exclude_st=False))
    assert revised["config_hash"] != state["config_hash"]
    assert revised["input_hash"] != state["input_hash"]
    assert revised["breadth"]["qualified_count"] == 3
    assert state["validation_status"] == "INITIAL_UNVALIDATED"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, -1, 0])
def test_bad_reference_prices_are_rejected(value):
    row = context_bundle().reference_prices[0]
    with pytest.raises(ValidationError):
        ReferencePrice.model_validate({**row.model_dump(), "previous_close": value})


def test_sentiment_rules_are_explainable_and_initial_unvalidated(market_inputs):
    breadth = build(market_inputs, context_bundle())["breadth"]
    strong = {**breadth, "advance_ratio": 0.8, "median_return_pct": 1.2}
    state = calculate_sentiment(strong)
    assert state["state"] == "STRONG"
    assert state["reasons"] == ["BROAD_ADVANCE_AND_POSITIVE_MEDIAN"]
    weak = {**breadth, "advance_ratio": 0.2, "median_return_pct": -1}
    assert calculate_sentiment(weak, index_return_pct=1)["state"] == "DIVERGENCE"
    assert calculate_sentiment(weak, previous=strong)["state"] == "RETREAT"
    assert SentimentConfig(strong_advance_ratio=0.8).config_hash != SentimentConfig().config_hash


def test_expired_context_consumed_by_marginal_state_is_bound_to_input_hash(market_inputs):
    bundle = context_bundle()
    expired = bundle.security_statuses[0].model_copy(update={"effective_to": clock(9, 59)})
    current = bundle.security_statuses[0].model_copy(update={"effective_from": clock(9, 59)})
    first = bundle.model_copy(update={"security_statuses": (*bundle.security_statuses[1:], expired, current)})
    second = first.model_copy(update={"security_statuses": (*bundle.security_statuses[1:], expired.model_copy(update={"previous_limit_up": False}), current)})
    assert build(market_inputs, first)["input_hash"] != build(market_inputs, second)["input_hash"]


def test_insufficient_turnover_history_is_missing_not_zero(market_inputs):
    bundle = context_bundle()
    rows = tuple(row for row in bundle.turnover_history if row.observation_date > DAY - timedelta(days=5))
    state = build(market_inputs, bundle.model_copy(update={"turnover_history": rows}))
    assert state["indices"]["000001.SH"]["features"]["turnover_ratio"] is None
    assert state["breadth"]["market_turnover_ratio"] is None


def test_sourced_limit_touch_fade_and_board_history_are_explicit(market_inputs):
    bundle = context_bundle()
    statuses = tuple(row.model_copy(update={"previous_board_height": 2 if row.instrument_id == STOCKS[0] else 0})
                     for row in bundle.security_statuses)
    references = tuple(row.model_copy(update={"limit_up": 10.62}) if row.instrument_id == STOCKS[0] else row
                       for row in bundle.reference_prices)
    context = bundle.model_copy(update={"security_statuses": statuses, "reference_prices": references})
    rows = [bar.model_copy(update={"high": 10.62}) if bar.instrument_id == STOCKS[0] and bar.timestamp == clock(9, 50)
            else bar for bar in market_inputs[2]]
    rows = [bar.model_copy(update={"high": min(bar.high, 10.62)}) if bar.instrument_id == STOCKS[0]
            else bar for bar in rows]
    breadth = build(market_inputs, context, bars=rows)["breadth"]
    assert breadth["limit_up_count"] == 1
    assert breadth["max_board_height"] == 3
    assert breadth["broken_limit_count"] == 0
    assert breadth["high_momentum_return_pct"] == pytest.approx(6.2)
    rows = [bar.model_copy(update={"close": 10.61}) if bar.instrument_id == STOCKS[0] and bar.timestamp == clock(10, 1)
            else bar for bar in rows]
    breadth = build(market_inputs, context, bars=rows)["breadth"]
    assert breadth["broken_limit_count"] == 1
    assert breadth["max_board_height"] == 0


def test_limit_touch_and_board_stats_require_complete_paths_and_history(market_inputs):
    context = context_bundle()
    rows = [bar for bar in market_inputs[2] if not (bar.instrument_id == STOCKS[0] and bar.timestamp == clock(9, 50))]
    breadth = build(market_inputs, context, bars=rows)["breadth"]
    assert breadth["broken_limit_count"] is None
    assert breadth["max_board_height"] is None
    assert breadth["high_momentum_return_pct"] is None


def test_aggregate_correlation_equals_exact_all_pairs_and_rejects_misaligned_windows():
    from itertools import combinations
    from math import sqrt
    from statistics import mean
    series = [{str(n): (n ** power) % 17 + n / 10 for n in range(1, 16)} for power in (1, 2, 3, 4)]
    pairwise = []
    for left, right in combinations(series, 2):
        keys = sorted(left)
        a, b = [left[key] for key in keys], [right[key] for key in keys]
        ma, mb = mean(a), mean(b)
        pairwise.append(sum((x - ma) * (y - mb) for x, y in zip(a, b)) /
            sqrt(sum((x - ma) ** 2 for x in a) * sum((x - mb) ** 2 for x in b)))
    assert _correlation(series) == pytest.approx(mean(pairwise), abs=1e-12)
    mismatch = [dict(item) for item in series]
    mismatch[0].pop("2")
    assert _correlation(mismatch) is None
    assert _correlation([{str(n): 1 for n in range(15)}, series[0]]) is None


def test_validation_cache_preserves_exact_input_hash_and_revalidates_changed_objects(market_inputs):
    calendar, scope, bars, receipts = market_inputs
    context = context_bundle()
    engine = MarketStateEngine(calendar, scope, context=context)
    cold = engine.build(clock(10, 1), bars, receipts)
    assert canonical_json(engine.build(clock(10, 1), bars, receipts)) == canonical_json(cold)
    # New bypassed model_copy instances must always go through the contract.
    bad = list(bars)
    bad[0] = bad[0].model_copy(update={"close": True})
    with pytest.raises(ValidationError):
        engine.build(clock(10, 1), bad, receipts)
    # Even forced mutation of the original cached object is detected.
    object.__setattr__(bars[0], "close", True)
    with pytest.raises(ValidationError):
        engine.build(clock(10, 1), bars, receipts)


def test_cached_input_digest_is_exactly_the_canonical_contract_hash(market_inputs):
    calendar, scope, bars, receipts = market_inputs
    engine = MarketStateEngine(calendar, scope)
    state = engine.build(clock(10, 1), bars, receipts)
    expected = digest({"bars": sorted(bars, key=lambda bar: (bar.timestamp, bar.instrument_id)),
        "receipts": [receipts[key] for key in sorted(receipts)], "context": [], "context_receipts": [],
        "scope": scope, "calendar": calendar.version, "config_hash": engine.config.config_hash})
    assert state["input_hash"] == expected


@pytest.mark.parametrize("field,value", [("is_st", 0), ("suspended", "false"), ("trading_eligible", 1)])
def test_security_fact_requires_explicit_json_boolean(field, value):
    row = context_bundle().security_statuses[0]
    with pytest.raises(ValidationError):
        SecurityStatus.model_validate({**row.model_dump(), field: value})


def test_explicit_board_history_counterevidence_is_rejected():
    row = context_bundle().security_statuses[0]
    with pytest.raises(ValidationError, match="facts conflict"):
        SecurityStatus.model_validate({**row.model_dump(), "previous_limit_up": False, "previous_board_height": 3})


@pytest.mark.parametrize("relative,change,expected", [
    (0.5, 0.2, "STRENGTHENING"), (0.5, 0.01, "STABLE_STRONG"),
    (0.5, -0.2, "WEAKENING"), (0.1, 0.3, "REVERSING"), (-0.1, -0.3, "REVERSING"),
    (0.0, 0.01, "NEUTRAL"), (None, 0, "UNKNOWN"), (1, None, "UNKNOWN"),
])
def test_sector_marginal_states_are_versionable_and_explainable(relative, change, expected):
    state, reason = marginal_state(relative, change)
    assert state == expected
    assert reason


def test_sentiment_change_does_not_compare_different_qualified_cohorts(market_inputs):
    breadth = build(market_inputs, context_bundle())["breadth"]
    previous = {**breadth, "advance_ratio": 0.9, "qualified_inventory_hash": "different-qualified-inventory"}
    actual = calculate_sentiment(breadth, previous=previous)
    assert actual["advance_ratio_change_5m"] is None
    assert actual["state"] != "RETREAT"


def test_unknown_prior_cohort_membership_does_not_produce_complete_cohort_return(market_inputs):
    bundle = context_bundle()
    statuses = tuple(row.model_copy(update={"previous_limit_up": None, "size_bucket": "UNKNOWN"})
                     if row.instrument_id == STOCKS[1] else row for row in bundle.security_statuses)
    breadth = build(market_inputs, bundle.model_copy(update={"security_statuses": statuses}))["breadth"]
    assert breadth["previous_limit_up_return_pct"] is None
    assert breadth["small_vs_large_pct"] is None


def test_sourced_limit_counterevidence_in_earlier_bar_is_not_hidden_by_current_price(market_inputs):
    rows = [bar.model_copy(update={"high": 12.1}) if bar.instrument_id == STOCKS[0] and bar.timestamp == clock(9, 50)
            else bar for bar in market_inputs[2]]
    with pytest.raises(LiveError, match="PRICE_OUTSIDE_CONTEXT_LIMITS"):
        build(market_inputs, context_bundle(), bars=rows)


def test_constructed_context_with_mutable_list_is_never_trusted_from_cache(market_inputs):
    bundle = context_bundle()
    mutable = list(bundle.reference_prices)
    bypassed = bundle.model_copy(update={"reference_prices": mutable})
    calendar, scope, bars, receipts = market_inputs
    engine = MarketStateEngine(calendar, scope, context=bypassed)
    with pytest.warns(UserWarning, match="Pydantic serializer warnings"):
        engine.build(clock(10, 1), bars, receipts)
    mutable[0] = mutable[0].model_copy(update={"previous_close": True})
    with pytest.warns(UserWarning, match="Pydantic serializer warnings"):
        with pytest.raises(ValidationError):
            engine.build(clock(10, 1), bars, receipts)
