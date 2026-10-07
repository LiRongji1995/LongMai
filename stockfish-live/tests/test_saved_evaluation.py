"""Saved distributions are evaluated unchanged against mature actual outcomes."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from stockfish_live.common import LiveError, digest
from stockfish_live.contracts.forecast import (ForecastResult, HistoricalState, HorizonForecast,
                                              MatureOutcome, REGIMES)
from stockfish_live.evaluation.saved import evaluate_saved_forecasts
from stockfish_live.models.similarity import PreparedHistory, SimilarityModel

TZ = ZoneInfo("Asia/Shanghai")
DECISION = datetime(2026, 9, 30, 10, 0, tzinfo=TZ)
TARGET = DECISION.replace(hour=15)


def fixture(*, provenance="TEST_FIXTURE", available_delay=0., forecast_delay=.2, actual=.02):
    state = HistoricalState(state_id="TEST_FIXTURE_SAVED_STATE", instrument_id="000001.SH", decision_at=DECISION,
        available_at=DECISION + timedelta(seconds=available_delay), current_price=100.,
        features={"return_pct": 1.}, context={"input_hash": "TEST_FIXTURE_INPUT"}, pit_status=provenance)
    quantiles = {"q10": -.02, "q25": -.01, "q50": .01, "q75": .03, "q90": .04}
    probabilities = dict.fromkeys(REGIMES, 0.) | {"TREND_UP": .75, "RANGE_NEUTRAL": .25}
    horizon = HorizonForecast(horizon="15:00", target_at=TARGET, sample_size=50, available_sample_size=60,
        status="AVAILABLE", confidence="LOW", quantiles=quantiles,
        price_quantiles={name: 100 * (1 + value) for name, value in quantiles.items()},
        up_probability=.75, regime_probabilities=probabilities, regime_sample_size=50,
        model_hash="a" * 64, pit_status=provenance)
    forecast = ForecastResult(forecast_id=digest({"fixture": "SAVED_FORECAST"}), instrument_id=state.instrument_id,
        decision_at=DECISION, as_of=DECISION + timedelta(seconds=forecast_delay), current_price=state.current_price,
        state_id=state.state_id, input_hash="TEST_FIXTURE_INPUT", data_cutoff=state.available_at,
        configuration_hash="b" * 64, horizons=(horizon,), confidence="LOW", status="AVAILABLE",
        warnings=("MODEL_PARAMETERS_UNVALIDATED",), pit_status=provenance)
    outcome = MatureOutcome(state_id=state.state_id, instrument_id=state.instrument_id, decision_at=DECISION,
        horizon="15:00", target_at=TARGET, available_at=TARGET + timedelta(seconds=.1), anchor_price=100.,
        target_price=100 * (1 + actual), regime="TREND_UP", pit_status=provenance)
    return state, forecast, outcome


def test_saved_posterior_uses_original_distribution_and_lineage_without_refit(monkeypatch):
    state, forecast, actual = fixture()
    def forbidden(*_args, **_kwargs):
        raise AssertionError("saved posterior cannot call inference")
    monkeypatch.setattr(SimilarityModel, "predict", forbidden)
    before = forecast.model_dump(mode="json")
    report = evaluate_saved_forecasts([forecast], [state], [actual], as_of=TARGET + timedelta(minutes=1))
    assert report["retrained"] is report["recomputed_predictions"] is False
    assert report["investment_evaluation"] == "NOT_EVALUATED"
    assert report["baseline_status"] == "NOT_EVALUATED_NO_SAVED_BASELINE"
    assert report["real_oos_metrics"] is None
    assert report["fixture_sample_count"] == 1
    row = report["rows"][0]
    assert row["forecast_id"] == forecast.forecast_id
    assert row["model_hash"] == "a" * 64
    assert row["configuration_hash"] == "b" * 64
    assert row["actual_as_of"] == forecast.as_of.isoformat()
    assert row["quantiles"] == forecast.horizons[0].quantiles
    assert forecast.model_dump(mode="json") == before
    metrics = report["synthetic_engineering_metrics"]["distribution_metrics"]
    assert metrics["mae"] == pytest.approx(.01)
    assert metrics["coverage_50"] == metrics["coverage_80"] == 1.
    assert metrics["direction_probability"]["brier"] == pytest.approx(.0625)


def test_actual_is_not_mature_until_its_real_availability_and_never_zero():
    state, forecast, actual = fixture()
    report = evaluate_saved_forecasts([forecast], [state], [actual], as_of=TARGET)
    assert report["rows"] == []
    assert report["synthetic_engineering_metrics"] is None
    assert report["skipped"][0]["reason"] == "OUTCOME_UNAVAILABLE_OR_UNMATURED"
    changed = actual.model_copy(update={"target_price": 1000000.})
    assert report == evaluate_saved_forecasts([forecast], [state], [changed], as_of=TARGET)
    assert evaluate_saved_forecasts([forecast], [state], [], as_of=TARGET)["rows"] == []


def test_compressed_posterior_keeps_original_distribution_hash_and_maturity(monkeypatch):
    state, forecast, actual = fixture()
    prepared = PreparedHistory([state], [actual])
    def forbidden(*_args, **_kwargs):
        raise AssertionError("prepared scalar posterior must not hydrate complete paths")
    monkeypatch.setattr(type(prepared.visible_label(state.state_id, "15:00", as_of=TARGET + timedelta(minutes=1))),
                        "as_model", forbidden)
    for cutoff in (TARGET, TARGET + timedelta(minutes=1)):
        assert evaluate_saved_forecasts([forecast], [state], prepared, as_of=cutoff) == evaluate_saved_forecasts(
            [forecast], [state], [actual], as_of=cutoff)


def test_same_minute_real_capture_is_allowed_but_cross_minute_is_skipped():
    state, forecast, actual = fixture(available_delay=.1, forecast_delay=.2)
    assert len(evaluate_saved_forecasts([forecast], [state], [actual], as_of=TARGET + timedelta(seconds=1))["rows"]) == 1
    later = forecast.model_copy(update={"as_of": DECISION + timedelta(minutes=1)})
    report = evaluate_saved_forecasts([later], [state], [actual], as_of=TARGET + timedelta(seconds=1))
    assert not report["rows"]
    assert report["skipped"][0]["reason"] == "STALE_DECISION_CAPTURE"


def test_feature_captured_after_saved_forecast_is_not_used():
    state, forecast, actual = fixture(forecast_delay=.2)
    state = state.model_copy(update={"available_at": DECISION + timedelta(seconds=.3)})
    report = evaluate_saved_forecasts([forecast], [state], [actual], as_of=TARGET + timedelta(seconds=1))
    assert not report["rows"]
    assert report["skipped"][0]["reason"] == "FEATURE_NOT_AVAILABLE_AT_FORECAST"


def test_insufficient_original_forecast_is_not_filled_or_recomputed():
    state, forecast, actual = fixture()
    missing = HorizonForecast(horizon="15:00", target_at=TARGET, sample_size=0, available_sample_size=2,
        status="INSUFFICIENT", confidence="INSUFFICIENT", model_hash="a" * 64, pit_status="TEST_FIXTURE")
    forecast = forecast.model_copy(update={"horizons": (missing,), "status": "INSUFFICIENT", "confidence": "INSUFFICIENT"})
    report = evaluate_saved_forecasts([forecast], [state], [actual], as_of=TARGET + timedelta(seconds=1))
    assert not report["rows"]
    assert report["skipped"][0]["reason"] == "PREDICTION_INSUFFICIENT"


@pytest.mark.parametrize("field,value", [("anchor_price", 99.), ("instrument_id", "399001.SZ"),
                                         ("target_at", TARGET - timedelta(minutes=1))])
def test_inconsistent_actual_identity_anchor_and_target_fail_closed(field, value):
    state, forecast, actual = fixture()
    changed = actual.model_copy(update={field: value})
    with pytest.raises(LiveError, match="OUTCOME_STATE_MISMATCH"):
        evaluate_saved_forecasts([forecast], [state], [changed], as_of=TARGET + timedelta(seconds=1))


def test_conflicting_saved_ids_reject_and_identical_replays_deduplicate():
    state, forecast, actual = fixture()
    report = evaluate_saved_forecasts([forecast, forecast], [state, state], [actual, actual], as_of=TARGET + timedelta(seconds=1))
    assert len(report["rows"]) == 1
    changed = forecast.model_copy(update={"configuration_hash": "c" * 64})
    with pytest.raises(LiveError, match="FORECAST_ID_CONFLICT"):
        evaluate_saved_forecasts([forecast, changed], [state], [actual], as_of=TARGET + timedelta(seconds=1))
    with pytest.raises(LiveError, match="STATE_ID_CONFLICT"):
        evaluate_saved_forecasts([forecast], [state, state.model_copy(update={"current_price": 99.})], [actual],
                                 as_of=TARGET + timedelta(seconds=1))


def test_verified_research_and_fixture_are_separate_and_do_not_establish_edge():
    for provenance, count_key, metrics_key in (("VERIFIED_PIT", "real_oos_sample_count", "real_oos_metrics"),
            ("RESEARCH_ONLY", "pit_unverified_sample_count", "pit_unverified_research_metrics"),
            ("TEST_FIXTURE", "fixture_sample_count", "synthetic_engineering_metrics")):
        state, forecast, actual = fixture(provenance=provenance)
        report = evaluate_saved_forecasts([forecast], [state], [actual], as_of=TARGET + timedelta(seconds=1))
        assert report[count_key] == 1
        assert report[metrics_key] is not None
        assert report["market_edge"] == "NOT_ESTABLISHED"
        assert report["incremental_edge"] == "NO_EVIDENCE_OF_INCREMENTAL_EDGE"
        assert len(report["by_instrument_time_horizon"]) == 1


def test_future_forecast_and_missing_feature_have_explicit_skip_reasons():
    state, forecast, actual = fixture()
    future = evaluate_saved_forecasts([forecast], [state], [actual], as_of=DECISION)
    assert not future["rows"]
    assert future["skipped"][0]["reason"] == "FORECAST_NOT_YET_AVAILABLE"
    missing = evaluate_saved_forecasts([forecast], [], [actual], as_of=TARGET + timedelta(seconds=1))
    assert missing["skipped"][0]["reason"] == "FEATURE_STATE_UNAVAILABLE"
    empty = evaluate_saved_forecasts([], [], [], as_of=TARGET)
    assert empty["investment_evaluation"] == "NOT_EVALUATED"
