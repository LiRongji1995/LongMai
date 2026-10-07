"""TEST_FIXTURE: detached revision inputs preserve strict comparison semantics."""
from datetime import timedelta

import pytest
from pydantic import ValidationError

from stockfish_live.common import LiveError, canonical_json, digest
from stockfish_live.contracts.forecast import ForecastResult, MatureOutcome, PathPoint, SimilarNeighbor
from stockfish_live.features.market import INDEX_IDS
from stockfish_live.forecast.revision import PreparedRevisionHistory, RevisionEngine
from test_forecasting import START, at, forecast_at


def forecast(hour=10, minute=0, *, instrument="000001.SH", price=100., remaining=.01, neighbors=False):
    base = forecast_at(hour, minute, price, remaining)
    if neighbors:
        old = START - timedelta(days=1)
        outcome = MatureOutcome(state_id="TEST_FIXTURE_REVISION_HISTORY", instrument_id=instrument,
            decision_at=at(old), horizon="15:00", target_at=at(old, 15), available_at=at(old, 15),
            anchor_price=100., target_price=101., pit_status="TEST_FIXTURE", source_ids=("c" * 64,),
            future_path=(PathPoint(timestamp=at(old, 10, 1), price=100.5), PathPoint(timestamp=at(old, 15), price=101.)))
        neighbor = SimilarNeighbor(state_id=outcome.state_id, decision_at=outcome.decision_at,
            distance=1., context={"nested": {"values": [1, 2]}}, outcome=outcome)
        base = base.model_copy(update={"horizons": (base.horizons[0].model_copy(update={"neighbors": (neighbor,)}),)})
    return ForecastResult.model_validate(base.model_copy(update={"instrument_id": instrument,
        "forecast_id": digest({"instrument": instrument, "hour": hour, "minute": minute,
                               "price": price, "remaining": remaining})}).model_dump())


def accept(index, item):
    return index.accept(item, as_of=item.as_of)


def test_all_46_decisions_for_five_indices_are_byte_identical_to_strict_reference():
    ticks = [at(START, 9, 30) + timedelta(minutes=i) for i in range(121)]
    ticks += [at(START, 13) + timedelta(minutes=i) for i in range(121)]
    ticks = [tick for tick in ticks if tick.minute % 5 == 0 and at(START, 9, 45) <= tick < at(START, 15)]
    assert len(ticks) == 46
    prepared, strict, history = PreparedRevisionHistory(), RevisionEngine(), []
    for step, tick in enumerate(ticks):
        for number, instrument in enumerate(INDEX_IDS):
            item = forecast(tick.hour, tick.minute, instrument=instrument,
                price=100. + number + (step % 7) / 10, remaining=(step % 5 - 2) * .003, neighbors=True)
            expected = strict.compare(item, history)
            actual = accept(prepared, item)
            assert canonical_json(actual) == canonical_json(expected), (instrument, tick)
            history.append(item)
    assert len(history) == 230


def test_lunch_boundary_keeps_1130_and_1300_forecasts_distinct():
    index = PreparedRevisionHistory()
    items = [forecast(11, 25, price=100.), forecast(11, 30, price=101.),
             forecast(13, 0, price=102.), forecast(13, 5, price=103.)]
    history = []
    for item in items:
        result = accept(index, item)
        assert result == RevisionEngine().compare(item, history)
        history.append(item)
    assert result["comparisons"][0]["previous_forecast_id"] == items[2].forecast_id
    assert result["warnings"] == []  # 11:30 -> 13:00 has zero trading-minute distance.


def test_future_capture_rejected_without_accepting_or_reading_its_path(monkeypatch):
    index = PreparedRevisionHistory()
    item = forecast(neighbors=True).model_copy(update={"as_of": at(START) + timedelta(milliseconds=200)})
    calls = []
    original = ForecastResult.model_validate

    def validate(value, *args, **kwargs):
        calls.append(value)
        return original(value, *args, **kwargs)

    monkeypatch.setattr(ForecastResult, "model_validate", validate)
    with pytest.raises(LiveError, match="FUTURE_FORECAST_ACCESS_DENIED"):
        index.accept(item, as_of=at(START) + timedelta(milliseconds=100))
    assert calls == [] and index._by_instrument == {}


def test_future_asof_prior_is_invisible_to_earlier_prediction():
    index = PreparedRevisionHistory()
    delayed = forecast(10, 0).model_copy(update={"as_of": at(START, 10, 20)})
    accept(index, delayed)
    current = forecast(10, 5)
    assert accept(index, current) == RevisionEngine().compare(current, [delayed])
    assert RevisionEngine().compare(current, [delayed])["comparisons"] == []


def test_future_decision_prior_is_invisible_and_out_of_order_calls_keep_reference_order():
    index = PreparedRevisionHistory()
    future, prior, current = forecast(10, 15), forecast(10, 0), forecast(10, 5)
    accept(index, future)
    accept(index, prior)
    assert accept(index, current) == RevisionEngine().compare(current, [future, prior])


def test_actual_subminute_asof_is_retained_for_visibility():
    index = PreparedRevisionHistory()
    prior = forecast(10, 0).model_copy(update={"as_of": at(START, 10, 5) + timedelta(milliseconds=200)})
    accept(index, prior)
    current = forecast(10, 5).model_copy(update={"as_of": at(START, 10, 5) + timedelta(milliseconds=100)})
    assert accept(index, current)["comparisons"] == []


def test_accepted_input_and_returned_payload_cannot_mutate_the_scalar_index():
    index = PreparedRevisionHistory()
    prior = forecast(10, 0, neighbors=True)
    pristine = ForecastResult.model_validate(prior.model_dump())
    accept(index, prior)
    prior.horizons[0].quantiles["q50"] = -999.
    prior.horizons[0].price_quantiles["q50"] = -999.
    prior.horizons[0].neighbors[0].context["nested"]["values"].append(999)
    object.__setattr__(prior.horizons[0].neighbors[0].outcome.future_path[0], "price", -1.)
    current = forecast(10, 5)
    result = accept(index, current)
    assert result == RevisionEngine().compare(current, [pristine])
    result["comparisons"][0]["previous_expected_price"] = -999.
    later = forecast(10, 15)
    assert accept(index, later) == RevisionEngine().compare(later, [pristine, current])


@pytest.mark.parametrize("change", ["price", "anchor", "future_path", "source_ids", "future_neighbor"])
def test_model_copy_bypass_is_fully_checked_before_acceptance(change):
    index = PreparedRevisionHistory()
    item = forecast(neighbors=True)
    if change == "price":
        item = item.model_copy(update={"current_price": -1.})
    elif change == "anchor":
        item.horizons[0].price_quantiles["q50"] += 5
    else:
        neighbor = item.horizons[0].neighbors[0]
        if change == "future_path":
            outcome = neighbor.outcome.model_copy(update={"future_path": (
                neighbor.outcome.future_path[0].model_copy(update={"price": -1.}), neighbor.outcome.future_path[1])})
        elif change == "source_ids":
            outcome = neighbor.outcome.model_copy(update={"source_ids": ("invalid",)})
        else:
            outcome = neighbor.outcome.model_copy(update={"available_at": item.as_of + timedelta(seconds=1)})
        item = item.model_copy(update={"horizons": (item.horizons[0].model_copy(update={
            "neighbors": (neighbor.model_copy(update={"outcome": outcome}),)}),)})
    with pytest.raises(ValidationError):
        accept(index, item)
    assert index._by_instrument == {}


def test_accept_revalidates_each_new_forecast_once_without_revisiting_old_models(monkeypatch):
    index = PreparedRevisionHistory()
    items = [forecast(10, minute, neighbors=True) for minute in (0, 5, 10, 15)]
    calls = []
    original = ForecastResult.model_validate

    def validate(value, *args, **kwargs):
        calls.append(value["forecast_id"])
        return original(value, *args, **kwargs)

    monkeypatch.setattr(ForecastResult, "model_validate", validate)
    for item in items:
        accept(index, item)
    assert calls == [item.forecast_id for item in items]


def test_public_compare_still_revalidates_arbitrary_matching_old_history():
    prior = forecast(9, 45, neighbors=True)
    object.__setattr__(prior.horizons[0].neighbors[0].outcome.future_path[0], "price", -1.)
    with pytest.raises(ValidationError):
        RevisionEngine().compare(forecast(10, 15), [prior])


def test_failed_acceptance_does_not_change_previously_accepted_history():
    index = PreparedRevisionHistory()
    prior = forecast(10, 0)
    accept(index, prior)
    bad = forecast(10, 5).model_copy(update={"current_price": -1.})
    with pytest.raises(ValidationError):
        accept(index, bad)
    assert len(index._by_instrument[prior.instrument_id]) == 1
    current = forecast(10, 15)
    assert accept(index, current) == RevisionEngine().compare(current, [prior])


def test_equal_decision_times_keep_strict_history_stable_order():
    index = PreparedRevisionHistory()
    first, duplicate_time = forecast(10, 0, price=100.), forecast(10, 0, price=105.)
    accept(index, first)
    accept(index, duplicate_time)
    current = forecast(10, 5)
    result = accept(index, current)
    assert result == RevisionEngine().compare(current, [first, duplicate_time])
    assert result["comparisons"][0]["previous_forecast_id"] == first.forecast_id


def test_previous_day_never_supplies_a_common_target_or_continuous_warning():
    index = PreparedRevisionHistory()
    prior = forecast(10, 0)
    accept(index, prior)
    value = forecast(10, 5).model_dump()
    value["decision_at"] += timedelta(days=1)
    value["as_of"] += timedelta(days=1)
    value["forecast_id"] = digest({"TEST_FIXTURE": "tomorrow"})
    for horizon in value["horizons"]:
        horizon["target_at"] += timedelta(days=1)
    current = ForecastResult.model_validate(value)
    result = accept(index, current)
    assert result == RevisionEngine().compare(current, [prior])
    assert result["comparisons"] == result["warnings"] == []


def test_missing_common_target_and_shared_target_horizon_order_match_reference():
    index = PreparedRevisionHistory()
    prior = forecast(14, 30)
    old_close = prior.horizons[0]
    # At 14:30 a 30m and 15:00 horizon can share their absolute target.
    first_common = old_close.model_copy(update={"horizon": "30m",
        "quantiles": dict.fromkeys(old_close.quantiles, .02),
        "price_quantiles": dict.fromkeys(old_close.price_quantiles, prior.current_price * 1.02)})
    prior = ForecastResult.model_validate(prior.model_copy(update={"horizons": (first_common, old_close)}).model_dump())
    accept(index, prior)
    current = forecast(14, 35)
    result = accept(index, current)
    assert result == RevisionEngine().compare(current, [prior])
    assert result["comparisons"][0]["previous_expected_price"] == 102.
    missing = forecast(14, 40).model_copy(update={"horizons": (), "status": "INSUFFICIENT", "confidence": "INSUFFICIENT"})
    assert accept(index, missing) == RevisionEngine().compare(missing, [prior, current])


@pytest.mark.parametrize("increasing", [True, False])
def test_continuous_warning_uses_same_four_prices(increasing):
    index, history = PreparedRevisionHistory(), []
    for number in range(4):
        item = forecast(10, 5 * number, price=100 + number * (1 if increasing else -1), remaining=0.)
        result = accept(index, item)
        assert result == RevisionEngine().compare(item, history)
        history.append(item)
    assert result["warnings"] == ["FORECAST_CONTINUOUS_IMPROVEMENT" if increasing else "FORECAST_CONTINUOUS_DETERIORATION"]
