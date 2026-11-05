"""Exact-result and work-count regressions for context-local market reuse."""
from collections import Counter
from datetime import datetime
from fractions import Fraction
from types import SimpleNamespace

import pytest

from stockfish_core.yaogu import stage_aware_momentum as sam
from tests.r05_product.test_stage_aware_momentum_v1 import (
    _with_high_activity_weakening_target,
    _with_high_level_repair_target,
)
from tests.support.stage_market_fixture import trend_bundle


def _context(values):
    return SimpleNamespace(
        eligible_ids=list(values), values=values, scalar_cache={},
        observation_date="2026-09-08", cutoff=datetime.fromisoformat("2026-09-09T09:00:00+08:00"),
    )


@pytest.mark.parametrize("values", [
    {"A": Fraction(2, 7), "B": Fraction(-5, 13), "C": Fraction(0), "D": Fraction(11, 37)},
    {"A": Fraction(1, 3), "B": None, "C": None, "D": None},
    {"A": None, "B": None, "C": None, "D": None},
    {},
])
def test_market_return_exact_reuse_preserves_partial_unknown_and_zero(monkeypatch, values):
    ctx = _context(values)
    reads, reductions = [], []
    original_reduce = sam._market_return_at_uncached

    def read(ctx_arg, iid, end_index, lookback):
        reads.append((iid, end_index, lookback))
        return ctx_arg.values[iid]

    def reduce(ctx_arg, end_index, lookback):
        reductions.append((end_index, lookback))
        return original_reduce(ctx_arg, end_index, lookback)

    monkeypatch.setattr(sam, "_price_return", read)
    monkeypatch.setattr(sam, "_market_return_at_uncached", reduce)
    population = [value for value in values.values() if value is not None]
    coverage = Fraction(len(population), len(values)) if values else Fraction(0)
    threshold = Fraction(str(sam.CONFIG["research_seed_parameters"]["market_context"]["min_benchmark_coverage_fraction_inclusive"]))
    complete = bool(population) and coverage >= threshold
    expected = sum(population, Fraction(0)) / len(population) if complete else None
    expected_status = "COMPLETE" if complete else "INSUFFICIENT_COVERAGE" if population else "UNKNOWN"

    for _ in range(12):
        result, metadata = sam._market_return_at(ctx, 20, 5)
        assert result == expected
        assert result is None or isinstance(result, Fraction)
        assert metadata["status"] == expected_status
        assert metadata["coverage"] == coverage
        assert metadata["valid_count"] == len(population)
        assert metadata["expected_count"] == len(values)
    assert len(reads) == len(values)
    assert reductions == [(20, 5)]

    # Metadata is caller-owned, including the nested list of UNKNOWN reasons.
    expected_reasons = list(metadata["reason_codes"])
    metadata["status"] = "MUTATED"
    metadata["reason_codes"].append("MUTATED")
    assert sam._market_return_at(ctx, 20, 5)[1]["status"] == expected_status
    assert sam._market_return_at(ctx, 20, 5)[1]["reason_codes"] == expected_reasons


def test_none_session_is_cached_without_reading_prices(monkeypatch):
    ctx = _context({"A": Fraction(1, 7)})
    reductions = []
    original_reduce = sam._market_return_at_uncached

    def reduce(ctx_arg, end_index, lookback):
        reductions.append(end_index)
        return original_reduce(ctx_arg, end_index, lookback)

    def unexpected_read(*args):
        pytest.fail("A missing session must not request a price")

    monkeypatch.setattr(sam, "_market_return_at_uncached", reduce)
    monkeypatch.setattr(sam, "_price_return", unexpected_read)
    for _ in range(5):
        value, metadata = sam._market_return_at(ctx, None, 5)
        assert value is None
        assert metadata["status"] == "UNKNOWN"
        assert metadata["expected_count"] == 1
    assert reductions == [None]


def test_cache_isolates_context_full_universe_end_and_window(monkeypatch):
    ctx = _context({"A": Fraction(1, 7), "B": Fraction(2, 7)})
    other = _context({"A": Fraction(3, 7), "B": Fraction(4, 7)})
    reads = Counter()

    def read(ctx_arg, iid, end_index, lookback):
        reads[(id(ctx_arg), tuple(ctx_arg.eligible_ids), end_index, lookback)] += 1
        return ctx_arg.values[iid] + Fraction(end_index, 101) + Fraction(lookback, 103)

    monkeypatch.setattr(sam, "_price_return", read)
    original = sam._market_return_at(ctx, 20, 5)
    assert sam._market_return_at(ctx, 20, 5) == original
    assert sam._market_return_at(other, 20, 5)[0] != original[0]
    assert sam._market_return_at(ctx, 19, 5)[0] != original[0]
    assert sam._market_return_at(ctx, 20, 10)[0] != original[0]
    # Same pool length, but a different effective identity; mutate in place to
    # reject cache keys based only on list identity or eligible count.
    ctx.values["C"] = Fraction(6, 7)
    ctx.eligible_ids[:] = ["A", "C"]
    changed = sam._market_return_at(ctx, 20, 5)
    assert changed[0] != original[0]
    assert sam._market_return_at(ctx, 20, 5) == changed
    ctx.eligible_ids[:] = ["A", "B"]
    assert sam._market_return_at(ctx, 20, 5) == original
    assert len(reads) == 5
    assert set(reads.values()) == {2}


def test_benchmarks_share_population_but_keep_partial_mean_semantics(monkeypatch):
    ctx = _context({"A": Fraction(3, 11), "B": None, "C": None, "D": None})
    reads = Counter()

    def read(ctx_arg, iid, end_index, lookback):
        reads[(iid, end_index, lookback)] += 1
        return ctx_arg.values[iid]

    monkeypatch.setattr(sam, "_price_return", read)
    market = sam._market_benchmarks(ctx, 20)
    result, metadata = sam._market_return_at(ctx, 20, 5)
    assert result is None
    assert metadata["status"] == "INSUFFICIENT_COVERAGE"
    # The pre-existing benchmark diagnostic keeps the observed partial mean,
    # while _market_return_at only exposes a usable mean with enough coverage.
    assert market["_primary_values"]["5"]["benchmark_return"] == Fraction(3, 11)
    assert market["_primary_values"]["5"]["status"] == "INSUFFICIENT_COVERAGE"
    assert all(reads[(iid, 20, 5)] == 1 for iid in ctx.eligible_ids)
    assert sam._market_return_population(ctx, 20, 5) == (Fraction(3, 11),)


@pytest.mark.parametrize("make_fixture", [
    trend_bundle,
    _with_high_level_repair_target,
    _with_high_activity_weakening_target,
])
def test_full_strategy_matches_cold_reductions_and_population_is_built_once(monkeypatch, make_fixture):
    bundle, cutoff = make_fixture()
    populations, reductions = Counter(), Counter()
    original_population = sam._market_return_population_uncached
    original_reduce = sam._market_return_at_uncached

    def population(ctx, end_index, lookback):
        populations[(tuple(ctx.eligible_ids), end_index, lookback)] += 1
        return original_population(ctx, end_index, lookback)

    def reduce(ctx, end_index, lookback):
        reductions[(tuple(ctx.eligible_ids), end_index, lookback)] += 1
        return original_reduce(ctx, end_index, lookback)

    monkeypatch.setattr(sam, "_market_return_population_uncached", population)
    monkeypatch.setattr(sam, "_market_return_at_uncached", reduce)
    cached = sam.run_stage_aware_momentum(bundle, cutoff=cutoff)
    assert populations and set(populations.values()) == {1}
    assert reductions and set(reductions.values()) == {1}
    assert any(end == len(bundle.sessions) - 2 and window == 10 for _, end, window in populations)

    monkeypatch.setattr(sam, "_market_return_population", original_population)
    monkeypatch.setattr(sam, "_market_return_at", original_reduce)
    cold = sam.run_stage_aware_momentum(bundle, cutoff=cutoff)
    # Includes every decision/setup/reason/rank and the exact saved evidence;
    # compare the complete result, not just top IDs or rounded scores.
    assert cached == cold
