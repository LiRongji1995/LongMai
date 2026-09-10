"""R15 true cross-session incremental cache and percentile tests."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time
from decimal import Decimal
from fractions import Fraction

import pytest

from tests.yaogu.fixtures import TZ, make_bundle
from stockfish_core.storage import IntegrityError, SQLiteStore
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.derived_feature_store import (
    FEATURE_FAMILY_SPECS,
    MATERIALIZED_FAMILIES,
    DerivedFeatureStore,
    dependency_plan_for_context,
    family_cross_as_of_safe,
    feature_spec_hash,
    identity_for_context,
)
from stockfish_core.yaogu.strategy_experiments import (
    _prepare_context,
    percentile_rank_midpoint,
    percentile_rank_midpoint_lookup,
    percentile_rank_midpoint_map,
    run_active_daily_strategy,
)


def _store(tmp_path, name="r15.sqlite3"):
    return SQLiteStore(tmp_path / name)


def _slice_bundle(bundle, index):
    day = bundle.sessions[index]
    return (
        bundle.model_copy(update={
            "observation_date": day,
            "sessions": tuple(session for session in bundle.sessions if session <= day),
            "bars": tuple(bar for bar in bundle.bars if bar.session <= day),
        }),
        datetime.combine(day, time(20, 30), TZ),
    )


def _stage_slice(run):
    stage = run["results"]["STAGE_AWARE_MOMENTUM_V2"]
    return {"counts": stage["counts"], "top_ids": stage["top_ids"], "items": stage["items"]}


def _sample_dependency(fingerprint, session="2026-01-01"):
    return {
        family: {
            "feature_family": family,
            "feature_spec_id": FEATURE_FAMILY_SPECS[family]["feature_spec_id"],
            "feature_spec_hash": "TEST_SPEC_HASH_" + family,
            "instrument_id": "TEST000",
            "feature_as_of_session": session,
            "required_source_session_range": {
                "start": session,
                "end": session,
                "sessions": [session],
            },
            "comparable_basis_identity": "TEST_BASIS",
            "source_data_dependency_fingerprint": "TEST_SOURCE_" + fingerprint,
            "pit_policy_identity": "TEST_PIT",
            "provider_revision_identity": "TEST_PROVIDER_" + fingerprint,
            "dependency_fingerprint": fingerprint + "_" + session + "_" + family,
        }
        for family in MATERIALIZED_FAMILIES
    }


def _sample_row(fingerprint="A", session="2026-01-01"):
    return {
        "instrument_id": "TEST000",
        "session": session,
        "comparable_basis": "TEST_BASIS",
        "dependency_identities": _sample_dependency(fingerprint, session),
        "features": {
            "comparable_close": {"numerator": 10, "denominator": 1},
            "amount_cny": {"numerator": 20_000_000, "denominator": 1},
            "turnover_fraction": {"numerator": 8, "denominator": 100},
            "returns": {"1": {"numerator": 1, "denominator": 100}},
            "amount_change": {"1": {"numerator": 0, "denominator": 1}},
            "ma": {"5": {"numerator": 10, "denominator": 1}},
            "ma_reasons": {"5": None},
            "amount_mean": {},
            "amount_median": {},
            "turnover_mean": {},
        },
    }


def test_r15_true_next_session_reuses_prior_feature_sessions(tmp_path):
    bundle, _ = make_bundle(8)
    day_t, cutoff_t = _slice_bundle(bundle, -2)
    day_tp1, cutoff_tp1 = _slice_bundle(bundle, -1)
    store = DerivedFeatureStore(_store(tmp_path))

    first = run_active_daily_strategy(day_t, cutoff=cutoff_t, input_id="TEST_T", derived_store=store)
    second = run_active_daily_strategy(day_tp1, cutoff=cutoff_tp1, input_id="TEST_T_PLUS_1", derived_store=store)

    first_sessions = set(first["derived_feature_store"]["target_sessions"])
    second_sessions = set(second["derived_feature_store"]["target_sessions"])
    expected_reused = len(first_sessions & second_sessions) * second["derived_feature_store"]["eligible_count"]

    assert first["derived_feature_store"]["store_version"] == "DERIVED_FEATURE_STORE_V2"
    assert second["derived_feature_store"]["cache_hits"] == expected_reused
    assert second["derived_feature_store"]["newly_computed"] == second["derived_feature_store"]["requested_feature_rows"] - expected_reused
    assert second["derived_feature_store"]["reuse_ratio"] > 0.90


def test_r15_future_replay_after_newer_cache_matches_cold_t_result(tmp_path):
    bundle, _ = make_bundle(7)
    day_t, cutoff_t = _slice_bundle(bundle, -2)
    day_tp1, cutoff_tp1 = _slice_bundle(bundle, -1)

    cold_t = run_active_daily_strategy(
        day_t,
        cutoff=cutoff_t,
        input_id="TEST_T",
        derived_store=DerivedFeatureStore(_store(tmp_path, "cold-t.sqlite3")),
    )
    future_store = DerivedFeatureStore(_store(tmp_path, "future-first.sqlite3"))
    run_active_daily_strategy(day_tp1, cutoff=cutoff_tp1, input_id="TEST_T_PLUS_1", derived_store=future_store)
    replay_t = run_active_daily_strategy(day_t, cutoff=cutoff_t, input_id="TEST_T", derived_store=future_store)

    assert digest(_stage_slice(replay_t)) == digest(_stage_slice(cold_t))
    assert replay_t["derived_feature_store"]["cache_hits"] > 0


def test_r15_cache_key_is_immutable_and_records_conflict(tmp_path):
    bundle, cutoff = make_bundle(1)
    ctx = _prepare_context(bundle, cutoff)
    identity = identity_for_context(ctx, "TEST_INPUT_HASH")
    store = DerivedFeatureStore(_store(tmp_path))
    row = _sample_row("SAME_DEP")
    store.put_many(identity, [row])

    changed = deepcopy(row)
    changed["features"]["returns"]["1"] = {"numerator": 2, "denominator": 100}
    with pytest.raises(IntegrityError, match="DERIVED_FEATURE_INTEGRITY_CONFLICT"):
        store.put_many(identity, [changed])

    with store.store.transaction(write=False) as con:
        conflicts = con.execute("SELECT count(*) AS n FROM yaogu_derived_feature_integrity_conflicts_v2").fetchone()["n"]
    assert conflicts == 1


def test_r15_dependency_revision_invalidates_only_matching_feature_sessions(tmp_path):
    bundle, cutoff = make_bundle(1)
    ctx = _prepare_context(bundle, cutoff)
    identity = identity_for_context(ctx, "TEST_INPUT_HASH")
    store = DerivedFeatureStore(_store(tmp_path))
    store.put_many(identity, [_sample_row("OLD_SOURCE")])

    plan = {("TEST000", "2026-01-01"): _sample_dependency("REVISED_SOURCE")}
    assert store.get_many(identity, ["TEST000"], ["2026-01-01"], dependency_plan=plan) == {}
    assert store.last_get_many_stats["invalidated_family_rows"] == len(MATERIALIZED_FAMILIES)


def test_r15_dependency_revision_keeps_unaffected_feature_sessions(tmp_path):
    bundle, cutoff = make_bundle(1)
    ctx = _prepare_context(bundle, cutoff)
    identity = identity_for_context(ctx, "TEST_INPUT_HASH")
    store = DerivedFeatureStore(_store(tmp_path))
    store.put_many(identity, [
        _sample_row("OLD_SOURCE", "2026-01-01"),
        _sample_row("OLD_SOURCE", "2026-01-02"),
    ])

    plan = {
        ("TEST000", "2026-01-01"): _sample_dependency("REVISED_SOURCE", "2026-01-01"),
        ("TEST000", "2026-01-02"): _sample_dependency("OLD_SOURCE", "2026-01-02"),
    }
    result = store.get_many(identity, ["TEST000"], ["2026-01-01", "2026-01-02"], dependency_plan=plan)

    assert ("TEST000", "2026-01-01") not in result
    assert ("TEST000", "2026-01-02") in result
    assert store.last_get_many_stats["row_hits"] == 1
    assert store.last_get_many_stats["invalidated_family_rows"] == len(MATERIALIZED_FAMILIES)


def test_r15_strategy_threshold_change_does_not_invalidate_feature_spec(monkeypatch):
    from stockfish_core.yaogu import stage_aware_momentum

    before = feature_spec_hash()
    path = stage_aware_momentum.CONFIG["hard_requirements"]["liquidity_bottom"]
    original = path["amount_cny_min_inclusive"]
    try:
        path["amount_cny_min_inclusive"] = "999999999999"
        assert feature_spec_hash() == before
    finally:
        path["amount_cny_min_inclusive"] = original


def test_r15_pit_dependent_families_are_not_cross_as_of_materialized():
    assert family_cross_as_of_safe("PRICE_BASE") is True
    assert family_cross_as_of_safe("ROLLING_PRICE") is True
    assert family_cross_as_of_safe("ACTIVITY") is True
    assert family_cross_as_of_safe("SECTOR_RELATIVE") is False
    assert "SECTOR_RELATIVE" not in MATERIALIZED_FAMILIES
    assert "MARKET_RELATIVE" not in MATERIALIZED_FAMILIES


@pytest.mark.parametrize(
    "values",
    [
        [1, 2, 3, 4],
        [7, 7, 7, 7],
        [1, 2, 2, 3, 5],
        [42],
        [Decimal("-1.5"), Decimal("0"), Decimal("2.25"), Decimal("2.25")],
        [Fraction(1, 3), Fraction(2, 3), Decimal("0.3333333333333333333333333333")],
    ],
)
def test_r15_percentile_map_matches_legacy_midpoint(values):
    pmap = percentile_rank_midpoint_map(values)
    for value in values:
        assert percentile_rank_midpoint_lookup(value, pmap) == percentile_rank_midpoint(value, values)
    reversed_map = percentile_rank_midpoint_map(list(reversed(values)))
    assert {str(k): v for k, v in pmap.items()} == {str(k): v for k, v in reversed_map.items()}


def test_r15_percentile_map_matches_legacy_for_3041_synthetic_population():
    values = [Fraction((index * 37) % 211 - 105, 13) for index in range(3041)]
    pmap = percentile_rank_midpoint_map(values)
    for index in (0, 1, 127, 2048, 3040):
        assert percentile_rank_midpoint_lookup(values[index], pmap) == percentile_rank_midpoint(values[index], values)


def test_r15_dependency_plan_does_not_use_market_input_or_manifest_hash_as_key(tmp_path):
    bundle, cutoff = make_bundle(3)
    ctx = _prepare_context(bundle, cutoff)
    sessions = [ctx.sessions[-1]]
    identity = identity_for_context(ctx, "TEST_INPUT_HASH")
    plan = dependency_plan_for_context(ctx, sessions, list(ctx.eligible_ids))

    sample_dep = plan[(ctx.eligible_ids[0], sessions[0])]["ROLLING_PRICE"]
    assert identity["input_hash_in_cache_key"] is False
    assert identity["history_manifest_hash_in_cache_key"] is False
    assert "source_data_dependency_fingerprint" in sample_dep
    assert sample_dep["feature_as_of_session"] == sessions[0]
