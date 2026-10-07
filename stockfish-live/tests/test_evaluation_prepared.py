"""Compressed prepared evaluation must retain the original causal report."""
from datetime import date, datetime, time, timedelta

import pytest

from stockfish_live.calendar import TradingCalendar
from stockfish_live.common import SHANGHAI
from stockfish_live.contracts.forecast import ForecastConfig, HistoricalState, MatureOutcome, PathPoint
from stockfish_live.evaluation.walk_forward import expanding_walk_forward
from stockfish_live.forecast.engine import ForecastEngine, horizon_targets


def history():
    start = date(2026, 9, 1)
    days = tuple(start + timedelta(days=n) for n in range(9))
    calendar = TradingCalendar(schema_version="LIVE_CALENDAR_V1", sessions=days,
        coverage_start=days[0], coverage_end=days[-1], exchanges=("SH",),
        available_at=datetime.combine(days[0], time(9), SHANGHAI),
        source="TEST_FIXTURE_PREPARED_EVALUATION", pit_status="TEST_FIXTURE")
    states, outcomes = [], []
    for n, day in enumerate(days[:-1]):
        for clock in (time(9, 45), time(10), time(14)):
            decision = datetime.combine(day, clock, SHANGHAI)
            state = HistoricalState(state_id=f"TEST_FIXTURE_EVAL_{decision.isoformat()}", instrument_id="000001.SH",
                decision_at=decision, available_at=decision, current_price=100., features={"x": float(n)}, pit_status="TEST_FIXTURE")
            states.append(state)
            for horizon, target in horizon_targets(calendar, decision).items():
                price = 100 * (1 + (n % 3 - 1) / 100)
                outcomes.append(MatureOutcome(state_id=state.state_id, instrument_id=state.instrument_id,
                    decision_at=decision, horizon=horizon, target_at=target, available_at=target,
                    anchor_price=100., target_price=price, regime="RANGE_NEUTRAL", pit_status="TEST_FIXTURE",
                    future_path=(PathPoint(timestamp=target, price=price),), source_ids=("f" * 64,)))
    return calendar, states, outcomes


@pytest.mark.parametrize("tune", [False, True])
@pytest.mark.parametrize("times", [None, ("09:45", "10:00")])
def test_raw_and_compressed_walk_forward_reports_are_identical(tune, times):
    calendar, states, outcomes = history()
    config = ForecastConfig(min_samples=2, k=2, k_candidates=(2, 3), feature_names=("x",))
    as_of = datetime.combine(calendar.sessions[-1], time(16), SHANGHAI)
    prepared = ForecastEngine(calendar, config).prepare_history(states, iter(outcomes))
    options = dict(as_of=as_of, decision_times=times, min_train_sessions=2,
                   tune_k=tune, min_validation_samples=2)
    raw = expanding_walk_forward(states, outcomes, calendar, config, **options)
    compressed = expanding_walk_forward(states, prepared, calendar, config, **options)
    assert compressed == raw
    assert compressed["fixture_sample_count"] > 0


def test_prepared_future_actual_cannot_enter_posterior_or_model_hash():
    calendar, states, outcomes = history()
    config = ForecastConfig(min_samples=2, k=2, k_candidates=(2,), feature_names=("x",))
    as_of = states[-1].decision_at
    changed = [item.model_copy(update={"target_price": 1000000., "future_path": ()})
               if item.available_at > as_of else item for item in outcomes]
    first = ForecastEngine(calendar, config).prepare_history(states, outcomes)
    second = ForecastEngine(calendar, config).prepare_history(states, changed)
    options = dict(as_of=as_of, min_train_sessions=2, tune_k=False)
    assert expanding_walk_forward(states, first, calendar, config, **options) == expanding_walk_forward(
        states, second, calendar, config, **options)
