"""Independent TEST_FIXTURE checks; no real-market effectiveness assertions."""
from datetime import date, datetime, timedelta
from math import log, sqrt
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from stockfish_live.calendar import TradingCalendar
from stockfish_live.common import LiveError, digest
from stockfish_live.contracts.forecast import (REGIMES, ForecastConfig, ForecastResult,
    HistoricalState, HorizonForecast, MatureOutcome)
from stockfish_live.contracts.market import MinuteBar, SourceReceipt
from stockfish_live.evaluation.calibration import calibration_table
from stockfish_live.evaluation.metrics import (distribution_metrics, probability_metrics,
    regime_metrics, regression_metrics, spearman)
from stockfish_live.evaluation.walk_forward import expanding_walk_forward, select_k_candidates
from stockfish_live.forecast.engine import (ForecastEngine, horizon_targets, mature_outcomes,
    state_from_market_state)
from stockfish_live.forecast.revision import RevisionEngine
from stockfish_live.models.baseline import baseline_predictions
from stockfish_live.models.regime import classify_regime, empirical_regime
from stockfish_live.models.similarity import SimilarityModel

TZ = ZoneInfo("Asia/Shanghai")
START = date(2026, 7, 1)


def at(day, hour=10, minute=0):
    return datetime.combine(day, datetime.min.time(), TZ).replace(hour=hour, minute=minute)


@pytest.fixture
def calendar():
    return TradingCalendar(schema_version="LIVE_CALENDAR_V1",
        sessions=tuple(START + timedelta(days=i) for i in range(20)),
        coverage_start=START, coverage_end=START + timedelta(days=19), exchanges=("SH", "SZ"),
        source="TEST_FIXTURE explicit synthetic sessions, including weekends",
        available_at=at(START - timedelta(days=1)), pit_status="TEST_FIXTURE")


def state(number, *, hour=10, minute=0, x=None, instrument="000001.SH", **changes):
    decision = at(START + timedelta(days=number), hour, minute)
    payload = dict(state_id=f"TEST_FIXTURE-{number}-{hour}-{minute}-{instrument}", instrument_id=instrument,
        decision_at=decision, available_at=decision, current_price=100.,
        features={"x": float(number if x is None else x)}, pit_status="TEST_FIXTURE",
        context={"market": "TEST_FIXTURE"})
    payload.update(changes)
    return HistoricalState(**payload)


def labels(calendar, item, *, result=0., available_at=None, regime="RANGE_NEUTRAL"):
    return [MatureOutcome(state_id=item.state_id, instrument_id=item.instrument_id,
        decision_at=item.decision_at, horizon=horizon, target_at=target,
        available_at=available_at or target, anchor_price=item.current_price,
        target_price=item.current_price * (1 + result), regime=regime, pit_status="TEST_FIXTURE")
        for horizon, target in horizon_targets(calendar, item.decision_at).items()]


def small_config(**changes):
    return ForecastConfig(**({"k": 3, "k_candidates": (2, 3, 4), "min_samples": 3} | changes))


def test_trading_horizons_skip_lunch_and_expired_absolute_targets(calendar):
    targets = horizon_targets(calendar, at(START, 11, 20))
    assert targets["30m"] == at(START, 13, 20)
    assert targets["60m"] == at(START, 13, 50)
    assert targets["11:30"] == at(START, 11, 30)
    late = horizon_targets(calendar, at(START, 14, 45))
    assert set(late) == {"15:00"}
    assert horizon_targets(calendar, at(START, 15)) == {}
    with pytest.raises(LiveError, match="INVALID_DECISION_TIME"):
        horizon_targets(calendar, at(START, 12))


def test_exact_empirical_quantiles_and_neighbor_explanation(calendar):
    history = [state(i, x=i) for i in range(3)]
    outcomes = [y for i, s in enumerate(history) for y in labels(calendar, s, result=(i - 1) / 100)]
    query = state(4, x=1)
    forecast = ForecastEngine(calendar, small_config()).predict(query, history, outcomes, as_of=query.decision_at)
    first = forecast.horizons[0]
    assert first.quantiles == pytest.approx({"q10": -.008, "q25": -.005, "q50": 0., "q75": .005, "q90": .008})
    assert first.price_quantiles["q10"] == pytest.approx(99.2)
    assert first.up_probability == pytest.approx(1 / 3)
    assert sum(first.regime_probabilities.values()) == pytest.approx(1)
    assert first.regime_sample_size == 3
    assert len(first.neighbors) == 3
    assert first.neighbors[0].state_id == history[1].state_id
    assert first.neighbors[0].context == {"market": "TEST_FIXTURE"}
    assert first.neighbors[0].outcome.target_price == 100
    assert forecast.confidence == "LOW"
    assert forecast.market_edge == "NOT_ESTABLISHED"
    assert "SMALL_SAMPLE_CONFIGURATION" in forecast.warnings


def test_normalizer_fit_excludes_query_future_same_day_and_different_time(calendar):
    history = [state(i, x=i + 1) for i in range(3)]
    query = state(5, x=100)
    ignored = [state(8, x=10000), state(5, x=10000), state(1, hour=11, x=10000),
               state(2, x=10000, instrument="399001.SZ")]
    outcomes = [y for s in history + ignored for y in labels(calendar, s)]
    result = SimilarityModel(small_config()).predict(query, history + ignored, outcomes,
        as_of=query.decision_at, horizon="15:00", target_at=at(query.decision_at.date(), 15))
    assert result.normalization_mean["x"] == 2.
    assert result.normalization_scale["x"] == pytest.approx(sqrt(2 / 3))
    assert result.available_sample_size == 3
    assert set(result.training_state_ids) == {s.state_id for s in history}
    assert "SIMILARITY_DISTANCE_HIGH" in result.warnings


def test_late_download_future_labels_and_missing_labels_do_not_become_zero(calendar):
    history = [state(i) for i in range(3)]
    query = state(5)
    outcomes = labels(calendar, history[0], result=.02)
    outcomes += labels(calendar, history[1], result=-.4, available_at=at(START + timedelta(days=10), 16))
    result = SimilarityModel(small_config()).predict(query, history, outcomes, as_of=query.decision_at,
        horizon="15:00", target_at=at(query.decision_at.date(), 15))
    assert result.status == result.confidence == "INSUFFICIENT"
    assert result.available_sample_size == 1
    assert result.quantiles == {}
    assert result.up_probability is None
    assert not result.neighbors


def test_unavailable_label_values_cannot_change_forecast_or_hash(calendar):
    history = [state(i) for i in range(4)]
    query = state(5)
    ready = [y for s in history[:3] for y in labels(calendar, s)]
    late = labels(calendar, history[3], result=.01, available_at=at(START + timedelta(days=10), 16))
    changed = [y.model_copy(update={"target_price": 999999.}) for y in late]
    engine = ForecastEngine(calendar, small_config())
    assert engine.predict(query, history, ready + late, as_of=query.decision_at) == engine.predict(
        query, history, ready + changed, as_of=query.decision_at)


@pytest.mark.parametrize("features", [{}, {"x": None}])
def test_empty_unknown_and_future_features_fail_closed(calendar, features):
    query = state(5, features=features)
    result = ForecastEngine(calendar, small_config()).predict(query, (), (), as_of=query.decision_at)
    assert result.status == "INSUFFICIENT"
    with pytest.raises(LiveError, match="FUTURE_FEATURE_ACCESS_DENIED"):
        ForecastEngine(calendar, small_config()).predict(query, (), (), as_of=query.decision_at - timedelta(minutes=1))
    with pytest.raises(LiveError, match="FUTURE_FEATURE_ACCESS_DENIED"):
        SimilarityModel(small_config()).predict(query, (), (), as_of=query.decision_at - timedelta(minutes=1),
            horizon="15:00", target_at=at(query.decision_at.date(), 15))


def test_feature_missingness_is_explicit_and_no_zero_imputation(calendar):
    history = [state(i, features={"x": 1., "required": None}) for i in range(3)]
    query = state(5, features={"x": 1., "required": 3.})
    outcomes = [y for s in history for y in labels(calendar, s)]
    config = small_config(feature_names=("x", "required"))
    result = ForecastEngine(calendar, config).predict(query, history, outcomes, as_of=query.decision_at)
    assert all(h.available_sample_size == 0 for h in result.horizons)


def test_corrupt_and_wrong_anchor_labels_are_rejected(calendar):
    history = [state(i) for i in range(3)]
    query = state(5)
    outcomes = [y for s in history for y in labels(calendar, s)]
    wrong = outcomes[0].model_copy(update={"target_at": outcomes[0].target_at + timedelta(minutes=1)})
    with pytest.raises(LiveError, match="OUTCOME_HORIZON_MISMATCH"):
        ForecastEngine(calendar, small_config()).predict(query, history, [wrong] + outcomes[1:], as_of=query.decision_at)
    wrong = outcomes[0].model_copy(update={"anchor_price": 50.})
    with pytest.raises(LiveError, match="OUTCOME_STATE_MISMATCH"):
        ForecastEngine(calendar, small_config()).predict(query, history, [wrong] + outcomes[1:], as_of=query.decision_at)


def test_duplicate_conflicting_labels_and_mutated_contracts_fail(calendar):
    history = [state(i) for i in range(3)]
    query = state(5)
    outcomes = [y for s in history for y in labels(calendar, s)]
    wrong = outcomes[0].model_copy(update={"target_price": 101.})
    with pytest.raises(LiveError, match="OUTCOME_ID_CONFLICT"):
        ForecastEngine(calendar, small_config()).predict(query, history, outcomes + [wrong], as_of=query.decision_at)
    with pytest.raises(ValidationError):
        ForecastEngine(calendar, small_config()).predict(query.model_copy(update={"current_price": -1}),
            history, outcomes, as_of=query.decision_at)


def test_early_forecasts_low_and_off_schedule_explicit(calendar):
    history = [state(i, hour=9, minute=35) for i in range(3)]
    query = state(5, hour=9, minute=35)
    outcomes = [y for s in history for y in labels(calendar, s)]
    result = ForecastEngine(calendar, small_config()).predict(query, history, outcomes, as_of=query.decision_at)
    assert "LOW_CONFIDENCE_EARLY" in result.warnings
    assert result.confidence == "LOW"
    query = state(5, hour=10, minute=1)
    result = ForecastEngine(calendar, small_config()).predict(query, history, outcomes, as_of=query.decision_at)
    assert result.status == "NOT_SCHEDULED"
    assert result.horizons == ()


def bars_for(calendar, item, *, missing=None, late=False):
    bars, receipts = [], []
    for index, timestamp in enumerate(calendar.minutes(item.decision_at.date())):
        if timestamp <= item.decision_at or timestamp == missing:
            continue
        source_id = digest({"fixture": item.state_id, "timestamp": timestamp})
        available = timestamp + (timedelta(days=2) if late else timedelta())
        receipt = SourceReceipt(source_id=source_id, provider="mock", source_timestamp=timestamp,
            source_timestamp_basis="PUBLISHED", received_at=available, available_at=available, pit_status="TEST_FIXTURE")
        price = 100. + index / 1000
        bars.append(MinuteBar(instrument_id=item.instrument_id, exchange="SH", symbol="000001", asset_type="INDEX",
            timestamp=timestamp, open=price, high=price, low=price, close=price, volume=1., turnover=price,
            provider="mock", source_timestamp=timestamp, source_timestamp_basis="PUBLISHED", available_at=available,
            source_id=source_id, quality="VALID", pit_status="TEST_FIXTURE"))
        receipts.append(receipt)
    return bars, receipts


def test_mature_labels_require_every_future_minute_and_actual_receipt(calendar):
    item = state(0, hour=11, minute=20)
    bars, receipts = bars_for(calendar, item)
    early = mature_outcomes([item], bars, receipts, calendar, as_of=at(START, 11, 30))
    assert {y.horizon for y in early} == {"11:30"}
    assert early[0].available_at == at(START, 11, 30)
    assert len(early[0].future_path) == 10
    mature = mature_outcomes([item], bars, receipts, calendar, as_of=at(START, 15))
    assert {y.horizon for y in mature} == {"30m", "60m", "11:30", "14:00", "15:00"}
    assert next(y for y in mature if y.horizon == "30m").target_at == at(START, 13, 20)
    missing = [b for b in bars if b.timestamp != at(START, 11, 21)]
    assert mature_outcomes([item], missing, receipts, calendar, as_of=at(START, 15)) == ()
    bars, receipts = bars_for(calendar, item, late=True)
    assert mature_outcomes([item], bars, receipts, calendar, as_of=at(START, 15)) == ()


def test_market_state_bridge_has_exact_existing_features_and_lineage():
    item = state(0)
    market = {"as_of": item.decision_at, "data_cutoff": item.decision_at, "input_hash": "abc",
        "pit_status": "TEST_FIXTURE", "features": {"breadth_pct": None},
        "indices": {item.instrument_id: {"current_price": 100., "features": {"return_pct": 1.}}}}
    bridged = state_from_market_state(market, item.instrument_id, item.decision_at)
    assert bridged.features == {"breadth_pct": None, "return_pct": 1.}
    assert bridged.context["input_hash"] == "abc"
    assert state_from_market_state(market, "399001.SZ", item.decision_at) is None


@pytest.mark.parametrize("anchor,target,high,low,gap,label", [
    (100, 101, 101, 100, None, "TREND_UP"), (100, 99, 100, 99, None, "TREND_DOWN"),
    (100, 100.2, 101, 99, None, "RANGE_STRONG"), (100, 99.8, 101, 99, None, "WEAKENING"),
    (100, 100, 101, 99, None, "RANGE_NEUTRAL"), (100, 99, 100, 99, .5, "HIGH_OPEN_FADE")])
def test_versioned_regime_initial_hypotheses(anchor, target, high, low, gap, label):
    assert classify_regime(anchor, target, high, low, gap_pct=gap) == label


def test_regime_unknown_excluded_from_denominator():
    probabilities, n = empirical_regime(["TREND_UP", None, "TREND_DOWN", "TREND_UP"])
    assert n == 3
    assert probabilities["TREND_UP"] == pytest.approx(2 / 3)
    assert sum(probabilities.values()) == pytest.approx(1.)
    assert empirical_regime([None]) == ({}, 0)


def forecast_at(hour, minute, current_price, remaining_return):
    decision = at(START, hour, minute)
    values = dict.fromkeys(("q10", "q25", "q50", "q75", "q90"), remaining_return)
    horizon = HorizonForecast(horizon="15:00", target_at=at(START, 15), sample_size=50,
        available_sample_size=50, status="AVAILABLE", confidence="LOW", quantiles=values,
        price_quantiles={k: current_price * (1 + v) for k, v in values.items()},
        up_probability=float(remaining_return > 0), model_hash="a" * 64)
    return ForecastResult(forecast_id=digest({"hour": hour, "minute": minute}), instrument_id="000001.SH",
        decision_at=decision, as_of=decision, current_price=current_price, state_id="fixture",
        configuration_hash="b" * 64, horizons=(horizon,), confidence="LOW", status="AVAILABLE",
        warnings=(), pit_status="TEST_FIXTURE")


def test_revision_common_future_price_includes_already_realized_anchor_change():
    previous = forecast_at(10, 0, 100., .01)  # expected close 101
    current = forecast_at(10, 5, 102., 0.)   # expected close 102 despite smaller remaining return
    result = RevisionEngine().compare(current, [previous])
    comparison = result["comparisons"][0]
    assert comparison["direction"] == "IMPROVING"
    assert comparison["delta_q50_price_fraction"] == pytest.approx(102 / 101 - 1)
    assert comparison["delta_remaining_return_q50"] == -.01
    assert comparison["realized_anchor_change_fraction"] == pytest.approx(.02)
    assert result["comparison_anchor"] == "COMMON_FUTURE_TARGET_PRICE"


def test_revision_lookback_counts_trading_minutes_through_lunch():
    result = RevisionEngine().compare(forecast_at(13, 5, 100., .01), [forecast_at(11, 30, 100., 0.)])
    assert result["comparisons"][0]["lookback_minutes"] == 5


def test_revision_continuous_warning_requires_consecutive_changes():
    prior = [forecast_at(10, minute, price, 0.) for minute, price in ((0, 100.), (5, 101.), (10, 102.))]
    current = forecast_at(10, 15, 103., 0.)
    assert "FORECAST_CONTINUOUS_IMPROVEMENT" in RevisionEngine().compare(current, prior)["warnings"]
    prior[1] = forecast_at(10, 5, 102.5, 0.)
    assert "FORECAST_CONTINUOUS_IMPROVEMENT" not in RevisionEngine().compare(current, prior)["warnings"]


def test_metric_independent_oracles_and_missing_values():
    result = regression_metrics([1., 2., None, 100.], [2., 4., 3., None])
    assert result["n"] == 2
    assert result["missing_or_unavailable"] == 2
    assert result["mae"] == 1.5
    assert result["rmse"] == pytest.approx(sqrt(2.5))
    assert result["direction_accuracy"] == 1.
    assert result["spearman"] == pytest.approx(1.)
    assert spearman([1., 1., 2.], [3., 3., 1.]) == pytest.approx(-1.)
    assert spearman([1., 1.], [1., 2.]) is None
    assert regression_metrics([0.], [None])["mae"] is None
    probabilities = probability_metrics([.2, .8, None], [0, 1, 0])
    assert probabilities["brier"] == pytest.approx(.04)
    assert probabilities["log_loss"] == pytest.approx(-log(.8))


def test_pinball_coverage_calibration_and_confusion_oracles():
    values = {"q10": -2., "q25": -1., "q50": 0., "q75": 1., "q90": 2.}
    result = distribution_metrics([{"actual": .5, "quantiles": values, "up_probability": .5},
                                   {"actual": None, "quantiles": values, "up_probability": .5}])
    assert result["n"] == 1
    assert result["coverage_50"] == result["coverage_80"] == 1.
    assert result["pinball_loss"]["q10"] == .25
    assert result["pinball_loss"]["q90"] == pytest.approx(.15)
    assert result["mean_width_50"] == 2.
    calibration = calibration_table([0., .25, 1.], [0, 1, 1], bins=2)
    assert [b["n"] for b in calibration["bins"]] == [2, 1]
    assert calibration["expected_calibration_error"] == pytest.approx(.25)
    p = dict.fromkeys(REGIMES, 0.) | {"TREND_UP": 1.}
    result = regime_metrics([p, p], ["TREND_UP", "TREND_DOWN"])
    assert result["brier_multiclass"] == 1.
    assert result["accuracy"] == .5
    assert regime_metrics([], [])["accuracy"] is None
    assert result["confusion_matrix_actual_rows"]["TREND_DOWN"]["TREND_UP"] == 1


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True])
def test_nonfinite_and_boolean_features_rejected(bad):
    with pytest.raises(ValidationError):
        state(0, features={"x": bad})


def test_baselines_preserve_unavailable_and_fraction_units(calendar):
    query = state(5, features={"return_5m_pct": 1.})
    result = baseline_predictions(query, (), (), as_of=query.decision_at, horizon="30m", target_at=at(query.decision_at.date(), 10, 30))
    assert result["ZERO_RETURN"]["prediction"] == 0.
    assert result["CURRENT_TREND"]["prediction"] == pytest.approx(.06)
    assert result["PREVIOUS_DAY_PATH"]["status"] == "UNAVAILABLE"
    assert result["TIME_OF_DAY_MEAN"]["prediction"] is None


def test_expanding_walk_forward_multiple_times_and_honest_fixture_evidence(calendar):
    states = [state(i, hour=hour, features={"x": float(i), "return_5m_pct": .01})
              for i in range(9) for hour in (10, 14)]
    outcomes = [y for i, s in enumerate(states) for y in labels(calendar, s, result=(i % 3 - 1) / 100)]
    report = expanding_walk_forward(states, outcomes, calendar, small_config(), as_of=at(START + timedelta(days=9), 16),
                                    min_train_sessions=3, min_validation_samples=2)
    assert report["investment_evaluation"] == "NOT_EVALUATED"
    assert report["incremental_edge"] == "NO_EVIDENCE_OF_INCREMENTAL_EDGE"
    assert report["real_oos_metrics"] is None
    assert report["fixture_sample_count"] > 0
    assert report["synthetic_engineering_metrics"]["model_metrics"]["SIMILARITY_MODEL_V1"]["n"] == report["fixture_sample_count"]
    assert {r["decision_time"] for r in report["rows"]} == {"10:00", "14:00"}
    state_lookup = {s.state_id: s for s in states}
    for row in report["rows"]:
        query = state_lookup[row["state_id"]]
        assert all(state_lookup[identity].decision_at.date() < query.decision_at.date() for identity in row["training_state_ids"])
    assert report == expanding_walk_forward(states, outcomes, calendar, small_config(), as_of=at(START + timedelta(days=9), 16),
                                            min_train_sessions=3, min_validation_samples=2)


def test_walk_forward_missing_actual_not_counted_zero_and_no_data_not_evaluated(calendar):
    states = [state(i) for i in range(6)]
    outcomes = [y for s in states[:5] for y in labels(calendar, s, result=.01)]
    report = expanding_walk_forward(states, outcomes, calendar, small_config(), as_of=at(START + timedelta(days=7), 16), tune_k=False)
    assert all(row["state_id"] != states[-1].state_id for row in report["rows"])
    assert any(item["state_id"] == states[-1].state_id and item["reason"] == "OUTCOME_UNAVAILABLE_OR_UNMATURED" for item in report["skipped"])
    empty = expanding_walk_forward([], [], calendar, as_of=at(START, 16))
    assert empty["investment_evaluation"] == "NOT_EVALUATED"
    assert empty["synthetic_engineering_metrics"] is None
    assert empty["real_oos_sample_count"] == 0


def test_inner_k_selection_cannot_use_outer_actual(calendar):
    states = [state(i) for i in range(9)]
    outcomes = [y for s in states for y in labels(calendar, s, result=.01)]
    query = states[-1]
    config = small_config()
    first = select_k_candidates(query, states, outcomes, calendar, config, min_validation_samples=2)
    changed = [y.model_copy(update={"target_price": 100000.}) if y.state_id == query.state_id else y for y in outcomes]
    second = select_k_candidates(query, states, changed, calendar, config, min_validation_samples=2)
    assert first == second
    assert query.state_id not in first["validation_state_ids"]
