"""Independent exact arithmetic and source-boundary tests; all data is TEST_FIXTURE."""
from copy import deepcopy
from datetime import timedelta
from fractions import Fraction as F
from itertools import permutations
import json

import pytest
from pydantic import ValidationError

from stockfish_core.yaogu import short_horizon_strength as sh
from stockfish_core.yaogu.contracts import MarketInput, digest
from tests.support.short_horizon_strength_fixture import (
    CONTINUATION, PULLBACK, START, WEAK, current_row, payload, strength_bundle,
)


def exact_start():
    # All cutoffs come directly from the approved contract, without calling the evaluator.
    return dict(amount=F(100000000), returns={"1": F(6, 100), "3": F(6, 100),
        "5": F(6, 100), "10": F(6, 100), "20": F(6, 100)},
        rs={"1": F(2, 100), "3": F(2, 100), "5": F(2, 100)},
        ma={"5": F(506, 25), "10": F(503, 25), "20": F(1003, 50)},
        previous_ma20=F(20), close_comparable=F(106, 5), A=F(3, 2),
        previous_ret5=F(12, 100), prior5_close_high=F(20),
        raw={"open": F(10), "high": F(53, 5), "low": F(10),
             "close": F(53, 5), "reference_prev_close": F(10)},
        clv=F(1), prior_daily_returns=[F(0)] * 5,
        current_raw_present=True, current_quality="VALID", current_source_bound=True,
        current_visibility_valid=True, close_limit_down="UNKNOWN")


def exact_continuation():
    f = exact_start()
    f.update(amount=F(50000000), A=F(1), previous_ret5=F(1, 5),
        prior_daily_returns=[F(1, 10)] * 5)
    f["returns"].update({"1": F(5, 100), "3": F(0), "5": F(15, 100), "10": F(0)})
    f["rs"]["5"] = F(1, 1000000000)
    return f


def exact_pullback():
    f = exact_start()
    f.update(amount=F(200000000), A=F(1), previous_ret5=F(1, 2),
        prior_daily_returns=[F(1, 10)] * 5, clv=F(1, 10),
        raw={"open": F(11), "high": F(11), "low": F(10),
             "close": F(53, 5), "reference_prev_close": F(265, 23)})
    f["returns"].update({"1": F(-8, 100), "3": F(5, 100), "5": F(20, 100), "10": F(20, 100)})
    return f


def stage(f, key):
    return sh.evaluate_exact_features(f)["arms"][key]


def matched(f):
    return sh.evaluate_exact_features(f)["matched_arms"]


def test_approved_three_stages_and_research_safety_at_exact_thresholds():
    examples = [(exact_start(), "STRONG_START"),
        (exact_continuation(), "STRONG_CONTINUATION"),
        (exact_pullback(), "FIRST_PULLBACK")]
    for f, arm in examples:
        result = sh.evaluate_exact_features(f)
        assert result["research_selected"] is True
        assert arm in result["matched_arms"]
        assert result["formal_focus"] is False and result["core_pass"] is False
        assert result["decision_status"] != "FOCUS"
        assert result["limit_safety"]["status"] == "UNKNOWN"


@pytest.mark.parametrize("field,key,value", [
    ("amount", None, F(100000000) - F(1, 100)),
    ("A", None, F(3, 2) - F(1, 1000000000)),
    ("returns", "1", F(6, 100) - F(1, 1000000000)),
    ("rs", "1", F(2, 100) - F(1, 1000000000)),
    ("previous_ret5", None, F(12, 100) + F(1, 1000000000)),
    ("prior5_close_high", None, F(106, 5)),
    ("ma", "5", F(106, 5)),
    ("ma", "10", F(106, 5)),
])
def test_strong_start_independent_exact_failure_boundaries(field, key, value):
    f = exact_start()
    if key is None:
        f[field] = value
    else:
        f[field][key] = value
    assert stage(f, "STRONG_START")["status"] == "FAIL"
    assert sh.evaluate_exact_features(f)["research_selected"] is False


def test_same_display_rounding_does_not_merge_opposite_sides_of_six_percent():
    below, at = exact_start(), exact_start()
    below["returns"]["1"] -= F(1, 1000000000)
    assert round(float(below["returns"]["1"] * 100), 4) == round(float(at["returns"]["1"] * 100), 4)
    assert stage(below, "STRONG_START")["status"] == "FAIL"
    assert stage(at, "STRONG_START")["status"] == "PASS"


def test_clv_inclusive_boundary_and_valid_flat_alternative_preserve_null():
    f = exact_start()
    f["clv"] = F(7, 10)
    assert stage(f, "STRONG_START")["status"] == "PASS"
    f["clv"] -= F(1, 1000000000)
    assert stage(f, "STRONG_START")["status"] == "FAIL"
    f["clv"] = None
    for key in ("open", "high", "low", "close"):
        f["raw"][key] = F(53, 5)
    before = deepcopy(f)
    result = sh.evaluate_exact_features(f)
    assert result["research_selected"] is True
    assert result["limit_safety"]["status"] == "UNKNOWN"
    assert f == before and f["clv"] is None


@pytest.mark.parametrize("change", ["zero_price", "negative_price", "missing_previous", "no_amount", "no_source", "invalid_quality", "no_visibility"])
def test_flat_ohlc_does_not_create_false_complete_price_evidence(change):
    f = exact_start()
    f["clv"] = None
    for key in ("open", "high", "low", "close"):
        f["raw"][key] = F(53, 5)
    if change in {"zero_price", "negative_price"}:
        for key in ("open", "high", "low", "close"):
            f["raw"][key] = F(0 if change == "zero_price" else -1)
    elif change == "missing_previous":
        f["raw"]["reference_prev_close"] = None
    elif change == "no_amount":
        f["amount"] = None
    elif change == "no_source":
        f["current_source_bound"] = False
    elif change == "invalid_quality":
        f["current_quality"] = "INVALID"
    else:
        f["current_visibility_valid"] = False
    assert sh.evaluate_exact_features(f)["research_selected"] is False


def test_flat_negative_day_cannot_substitute_positive_start_or_continuation():
    f = exact_start()
    f["clv"] = None
    for key in ("open", "high", "low", "close"):
        f["raw"][key] = F(53, 5)
    f["raw"]["reference_prev_close"] = F(11)
    f["returns"]["1"] = F(-4, 100)
    assert sh.evaluate_exact_features(f)["research_selected"] is False


@pytest.mark.parametrize("field,key,value", [
    ("amount", None, F(50000000) - F(1, 100)),
    ("returns", "1", F(5, 100) - F(1, 1000000000)),
    ("returns", "3", F(-1, 1000000000)),
    ("rs", "5", F(0)),
    ("previous_ma20", None, F(1003, 50) + F(1, 1000000000)),
])
def test_continuation_independent_boundaries(field, key, value):
    f = exact_continuation()
    if key is None:
        f[field] = value
    else:
        f[field][key] = value
    assert stage(f, "STRONG_CONTINUATION")["status"] == "FAIL"


def test_continuation_absolute_strength_uses_five_or_ten_sessions():
    f = exact_continuation()
    f["returns"]["5"] = F(0)
    f["returns"]["10"] = F(25, 100)
    assert stage(f, "STRONG_CONTINUATION")["status"] == "PASS"
    f["returns"]["10"] -= F(1, 1000000000)
    assert stage(f, "STRONG_CONTINUATION")["status"] == "FAIL"
    f["returns"]["10"] = None
    assert stage(f, "STRONG_CONTINUATION")["status"] == "UNKNOWN"


@pytest.mark.parametrize("value,expected", [(F(-8, 100), "PASS"),
    (F(-8, 100) - F(1, 1000000000), "FAIL"), (F(0), "FAIL"), (F(1, 100), "FAIL"), (None, "UNKNOWN")])
def test_first_pullback_is_today_negative_with_inclusive_loss_floor(value, expected):
    f = exact_pullback()
    f["returns"]["1"] = value
    assert stage(f, "FIRST_PULLBACK")["status"] == expected


@pytest.mark.parametrize("value,expected", [(F(0), "FAIL"), (F(-1, 100), "FAIL"), (None, "UNKNOWN")])
@pytest.mark.parametrize("offset", [-3, -2, -1])
def test_first_pullback_requires_each_of_previous_three_days_positive(offset, value, expected):
    f = exact_pullback()
    f["prior_daily_returns"][offset] = value
    assert stage(f, "FIRST_PULLBACK")["status"] == expected


def test_missing_pullback_window_remains_unknown_and_older_negative_is_not_recent_negative():
    f = exact_pullback()
    f["prior_daily_returns"] = [F(1, 10)] * 2
    assert stage(f, "FIRST_PULLBACK")["status"] == "UNKNOWN"
    f["prior_daily_returns"] = [F(-1, 10), F(-1, 10), F(1, 10), F(1, 10), F(1, 10)]
    assert stage(f, "FIRST_PULLBACK")["status"] == "PASS"


def test_known_limit_down_rejects_despite_unknowns_and_otherwise_passing_stage():
    f = exact_start()
    f.update(close_limit_down="TRUE", current_visibility_valid=None)
    result = sh.evaluate_exact_features(f)
    assert result["research_selected"] is False
    assert result["decision_status"] == "REJECTED"
    assert result["limit_safety"]["status"] == "FAIL"


def test_feature_predicates_ignore_stock_name_identity_industry_and_labels():
    f = exact_start()
    expected = sh.evaluate_exact_features(f)
    for iid, name, industry in (("000001.SZ", "平安银行", "银行"),
        ("600050.SH", "中国联通", "通信"), ("002242.SZ", "九阳股份", "家电")):
        renamed = dict(f, instrument_id=iid, name=name, industry=industry,
            development_label=False, original_rank=3037, original_start_status="FAIL",
            original_HIGH="FAIL", original_v2_control={"START": "FAIL", "rank": 3037})
        assert sh.evaluate_exact_features(renamed) == expected


def test_old_start_and_rank_labels_cannot_upgrade_a_weak_price_path():
    f = exact_start()
    f["returns"]["1"] = F(1, 100)
    f.update(original_start_status="PASS", original_HIGH="PASS", original_rank=1,
        original_v2_control={"START": "PASS", "rank": 1})
    assert sh.evaluate_exact_features(f)["research_selected"] is False


def test_previous_five_session_return_unavailable_stays_unknown_in_start():
    f = exact_start()
    f["previous_ret5"] = None
    assert stage(f, "STRONG_START")["status"] == "UNKNOWN"
    assert sh.evaluate_exact_features(f)["research_selected"] is False


def test_inconsistent_raw_ohlc_cannot_become_a_passing_close_location_path():
    f = exact_start()
    f["raw"]["high"] = F(10)
    assert sh.evaluate_exact_features(f)["research_selected"] is False


def by_id(result):
    return {row["instrument_id"]: row for row in result["items"]}


def run(bundle, cutoff):
    return sh.run_short_horizon_strength(bundle, cutoff=cutoff)


def test_context_run_retains_complete_pool_and_short_stage_paths_with_low_turnover():
    bundle, cutoff = strength_bundle()
    result = run(bundle, cutoff)
    rows = by_id(result)
    assert len(rows) == len(result["items"]) == 4
    assert {iid for iid, row in rows.items() if row["research_selected"]} == {START, CONTINUATION, PULLBACK}
    assert rows[WEAK]["research_selected"] is False
    assert "STRONG_START" in rows[START]["matched_arms"]
    assert "STRONG_CONTINUATION" in rows[CONTINUATION]["matched_arms"]
    assert "FIRST_PULLBACK" in rows[PULLBACK]["matched_arms"]
    assert rows[PULLBACK].get("repair_status") == "UNCONFIRMED"
    assert all(row["decision_status"] != "FOCUS" and not row["core_pass"] for row in rows.values())
    assert all(row["limit_safety"]["status"] == "UNKNOWN" for row in rows.values())


@pytest.mark.parametrize("attack,reason", [
    ("no_reference", "SOURCE_REFERENCE_REQUIRED"),
    ("unknown_reference", "SOURCE_REFERENCE_UNRESOLVED"),
    ("late_parent", "CAPTURE_AFTER_CUTOFF"),
    ("wrong_parent_instrument", "SOURCE_INSTRUMENT_SCOPE_MISMATCH"),
    ("wrong_parent_session", "SOURCE_SESSION_SCOPE_MISMATCH"),
    ("row_precedes_parent", "ROW_CAPTURE_PRECEDES_PARENT_RECEIPT"),
    ("publication_before_business_day", "EVENT_OR_PUBLICATION_PRECEDES_BUSINESS_SESSION"),
    ("partial_parent", "SOURCE_NOT_COMPLETE"),
    ("incomplete_pagination", "SOURCE_NOT_COMPLETE"),
])
def test_context_rejects_unavailable_current_price_and_preserves_reason(attack, reason):
    bundle, cutoff = strength_bundle()
    value = payload(bundle)
    bar = current_row(value)
    # Use a new current-only parent so history stays valid while this source is attacked.
    parent = deepcopy(next(receipt for receipt in value["receipts"] if receipt["receipt_id"] == "TEST_FIXTURE_BARS"))
    parent.update(receipt_id="TEST_FIXTURE_CURRENT_ATTACK", instrument_ids=[START],
        business_sessions=[value["observation_date"]])
    value["receipts"].append(parent)
    bar["source_refs"] = [parent["receipt_id"]]
    if attack == "no_reference":
        bar["source_refs"] = []
    elif attack == "unknown_reference":
        bar["source_refs"] = ["TEST_FIXTURE_MISSING"]
    elif attack == "late_parent":
        parent.update(first_seen_at=cutoff + timedelta(seconds=1), ingested_at=cutoff + timedelta(seconds=1))
        bar.update(first_seen_at=cutoff + timedelta(seconds=1), ingested_at=cutoff + timedelta(seconds=1))
    elif attack == "wrong_parent_instrument":
        parent["instrument_ids"] = [WEAK]
    elif attack == "wrong_parent_session":
        parent["business_sessions"] = [value["sessions"][-2]]
    elif attack == "row_precedes_parent":
        parent.update(first_seen_at=cutoff - timedelta(hours=1), ingested_at=cutoff - timedelta(hours=1))
    elif attack == "partial_parent":
        parent["status"] = "PARTIAL"
    elif attack == "incomplete_pagination":
        parent["pagination_complete"] = False
    else:
        bar.update(event_time=bar["event_time"] - timedelta(days=1),
            source_published_at=bar["source_published_at"] - timedelta(days=1))
    result = run(MarketInput.model_validate(value), cutoff)
    assert by_id(result)[START]["research_selected"] is False
    assert reason in str(result)


def test_changed_comparable_basis_does_not_mix_hfq_and_raw_history():
    bundle, cutoff = strength_bundle()
    value = payload(bundle)
    current_row(value)["comparable_basis"] = "TEST_FIXTURE_OTHER_ACTION_BASIS"
    result = run(MarketInput.model_validate(value), cutoff)
    assert by_id(result)[START]["research_selected"] is False


def test_interior_history_basis_conflict_cannot_escape_endpoint_return_checks():
    bundle, cutoff = strength_bundle()
    value = payload(bundle)
    previous = next(row for row in value["bars"] if row["instrument_id"] == CONTINUATION
        and row["session"] == value["sessions"][-4])
    previous["comparable_basis"] = "TEST_FIXTURE_INCOMPATIBLE_INTERIOR_ACTION"
    result = run(MarketInput.model_validate(value), cutoff)
    assert by_id(result)[CONTINUATION]["research_selected"] is False


def test_missing_prior_bar_remains_unknown_not_synthetic_zero_return():
    bundle, cutoff = strength_bundle()
    value = payload(bundle)
    value["bars"] = [row for row in value["bars"] if not
        (row["instrument_id"] == PULLBACK and row["session"] == value["sessions"][-2])]
    result = run(MarketInput.model_validate(value), cutoff)
    row = by_id(result)[PULLBACK]
    assert row["research_selected"] is False
    assert row["arms"]["FIRST_PULLBACK"]["status"] == "UNKNOWN"


def test_missing_far_ma20_slope_history_does_not_block_complete_short_start_window():
    bundle, cutoff = strength_bundle()
    value = payload(bundle)
    # T-24 is required by MA20(T-5), but lies outside every START requirement.
    value["bars"] = [row for row in value["bars"] if row["session"] != value["sessions"][-25]]
    rows = by_id(run(MarketInput.model_validate(value), cutoff))
    assert rows[START]["research_selected"] is True
    assert rows[START]["arms"]["STRONG_START"]["status"] == "PASS"
    assert rows[CONTINUATION]["arms"]["STRONG_CONTINUATION"]["status"] == "UNKNOWN"
    assert rows[PULLBACK]["arms"]["FIRST_PULLBACK"]["status"] == "UNKNOWN"
    assert rows[CONTINUATION]["research_selected"] is False
    assert rows[PULLBACK]["research_selected"] is False


def test_start_specific_ma10_interior_basis_conflict_is_unknown():
    bundle, cutoff = strength_bundle()
    value = payload(bundle)
    previous = next(row for row in value["bars"] if row["instrument_id"] == START
        and row["session"] == value["sessions"][-9])
    previous["comparable_basis"] = "TEST_FIXTURE_START_MA10_CONFLICT"
    result = run(MarketInput.model_validate(value), cutoff)
    row = by_id(result)[START]
    assert row["arms"]["STRONG_START"]["status"] == "UNKNOWN"
    assert row["research_selected"] is False


def test_duplicate_equal_revision_conflict_is_rejected_at_market_input_boundary():
    bundle, cutoff = strength_bundle()
    value = payload(bundle)
    conflict = deepcopy(current_row(value))
    conflict["close"] += 1
    conflict["high"] += 1
    conflict["comparable_close"] += 2
    conflict["comparable_high"] += 2
    value["bars"].append(conflict)
    # The existing input contract refuses the ambiguity before any strategy can run.
    with pytest.raises(ValidationError, match="duplicate bar revision"):
        MarketInput.model_validate(value)


def test_input_order_does_not_change_all_pool_paths_or_rank():
    bundle, cutoff = strength_bundle()
    baseline = run(bundle, cutoff)
    for fields in permutations(("bars", "receipts", "securities")):
        value = payload(bundle)
        for field in fields:
            target = value["universe"] if field == "securities" else value
            target[field] = list(reversed(target[field]))
        result = run(MarketInput.model_validate(value), cutoff)
        assert result["items"] == baseline["items"]


def test_core_source_identity_covers_reused_evidence_and_formula_implementations():
    identity = sh.short_horizon_source_identity()
    hashes = identity.get("source_files", identity.get("source_file_hashes", {}))
    required = {"short_horizon_strength.py", "short_horizon_strength_r1.yaml", "strategy_experiments.py",
        "contracts.py", "visibility.py", "source_resolution.py", "history_artifacts.py",
        "events.py", "theme_capital_trend.py"}
    assert required.issubset({path.rsplit("/", 1)[-1] for path in hashes})
    assert all(len(value) == 64 for value in hashes.values())


def test_fresh_collector_matches_independent_exact_arithmetic_with_hfq_distinct_from_raw():
    from stockfish_core.yaogu.strategy_experiments import _prepare_context, _preload_bars
    bundle, cutoff = strength_bundle()
    ctx = _prepare_context(bundle, cutoff)
    _preload_bars(ctx, ctx.sessions[-25:], ctx.eligible_ids)
    start = sh.collect_exact_features(ctx, START)
    continuation = sh.collect_exact_features(ctx, CONTINUATION)
    pullback = sh.collect_exact_features(ctx, PULLBACK)
    assert start["returns"]["1"] == F(1, 10)
    assert start["A"] == F(3, 2)
    assert continuation["returns"]["5"] == F(61051, 100000)
    assert pullback["returns"]["1"] == F(-841, 14641)
    assert pullback["returns"]["3"] == F(17, 121)
    assert pullback["close_comparable"] == F(138, 5)
    assert pullback["raw"]["close"] == F(69, 5)
    assert start["prior5_close_high"] == F(20)


def test_new_implementation_byte_change_changes_source_identity(monkeypatch):
    from pathlib import Path
    before = sh.short_horizon_source_identity()
    original = Path.read_bytes
    def changed(path):
        value = original(path)
        return value + b"\n# TEST_FIXTURE_SOURCE_CHANGE\n" if path.name == "short_horizon_strength.py" else value
    monkeypatch.setattr(Path, "read_bytes", changed)
    after = sh.short_horizon_source_identity()
    assert before["source_hash"] != after["source_hash"]


def test_display_stage_order_cannot_change_independent_branch_truth_or_selection():
    f = exact_start()
    f["returns"]["5"] = F(15, 100)
    before = sh.evaluate_exact_features(f)
    assert set(before["matched_arms"]) == {"STRONG_START", "STRONG_CONTINUATION"}
    cfg = deepcopy(sh.CONFIG)
    cfg["stage_order"] = list(reversed(cfg["stage_order"]))
    after = sh.evaluate_exact_features(f, cfg)
    assert before["arms"] == after["arms"]
    assert before["research_selected"] == after["research_selected"]
    assert set(before["matched_arms"]) == set(after["matched_arms"])


def strict_peer_payload(*, five=False):
    bundle, cutoff = strength_bundle()
    value = payload(bundle)
    if five:
        extra = "TEST_OTHER_PEER"
        security = deepcopy(next(row for row in value["universe"]["securities"] if row["instrument_id"] == WEAK))
        security.update(instrument_id=extra, name="TEST_FIXTURE_EXTRA_PEER")
        value["universe"]["securities"].append(security)
        extra_bars = []
        for row in value["bars"]:
            if row["instrument_id"] == WEAK:
                cloned = deepcopy(row)
                cloned["instrument_id"] = extra
                extra_bars.append(cloned)
        value["bars"].extend(extra_bars)
        value["universe"]["expected_count"] = 5
        for receipt in value["receipts"]:
            receipt["instrument_ids"] = [*receipt["instrument_ids"], extra]
            rows = value["bars"] if receipt["receipt_id"].endswith("BARS") else value["universe"]["securities"]
            receipt.update(content_hash=digest(rows), row_count=len(rows))
    return value, cutoff


def damage_peer_interior(value, cutoff, iid, attack):
    """T-2 is interior for return5 but leaves both return5 endpoints intact."""
    bar = next(row for row in value["bars"] if row["instrument_id"] == iid
        and row["session"] == value["sessions"][-3])
    if attack == "basis":
        bar["comparable_basis"] = "TEST_FIXTURE_INTERIOR_PEER_BASIS_CONFLICT"
    elif attack == "missing":
        value["bars"].remove(bar)
    elif attack in {"late_parent", "partial_parent"}:
        parent = deepcopy(next(receipt for receipt in value["receipts"] if receipt["receipt_id"] == "TEST_FIXTURE_BARS"))
        parent.update(receipt_id="TEST_FIXTURE_ATTACK_PEER_" + iid,
            instrument_ids=[iid], business_sessions=[bar["session"]], row_count=1,
            content_hash=digest(bar))
        if attack == "late_parent":
            parent.update(first_seen_at=cutoff + timedelta(seconds=1), ingested_at=cutoff + timedelta(seconds=1))
            bar.update(first_seen_at=parent["first_seen_at"], ingested_at=parent["ingested_at"])
        else:
            parent["status"] = "PARTIAL"
        bar["source_refs"] = [parent["receipt_id"]]
        value["receipts"].append(parent)
    else:
        raise AssertionError("Unknown TEST_FIXTURE peer attack")


def peer_context(value, cutoff):
    from stockfish_core.yaogu.strategy_experiments import _prepare_context, _preload_bars
    ctx = _prepare_context(MarketInput.model_validate(value), cutoff)
    _preload_bars(ctx, ctx.sessions[-25:], ctx.eligible_ids)
    return ctx


@pytest.mark.parametrize("attack", ["basis", "missing", "late_parent", "partial_parent"])
def test_strict_rs_excludes_a_peer_with_bad_interior_and_recomputes_equal_mean(attack):
    value, cutoff = strict_peer_payload()
    damage_peer_interior(value, cutoff, PULLBACK, attack)
    ctx = peer_context(value, cutoff)
    facts = sh.collect_exact_features(ctx, CONTINUATION)
    # Independent prices: START=11/10-1, CONT=16.1051/10-1, WEAK=10.2/10-1.
    expected_market = (F(1, 10) + F(61051, 100000) + F(1, 50)) / 3
    assert facts["returns"]["5"] == F(61051, 100000)
    assert facts["rs"]["5"] == F(61051, 100000) - expected_market
    market, coverage = sh._r1_market_return_at(ctx, len(ctx.sessions)-1, 5)
    assert market == expected_market
    assert coverage["valid_count"] == 3 and coverage["expected_count"] == 4
    assert coverage["coverage"] == F(3, 4)
    assert coverage["minimum_coverage"] == F(3, 5)
    assert coverage["status"] == "COMPLETE"
    # The bad peer is excluded from the benchmark, not dropped from the eligible pool.
    assert set(ctx.eligible_ids) == {START, CONTINUATION, PULLBACK, WEAK}


@pytest.mark.parametrize("attack", ["basis", "missing", "late_parent", "partial_parent"])
def test_strict_rs_below_real_coverage_gate_is_unknown_not_zero_or_endpoint_average(attack):
    value, cutoff = strict_peer_payload()
    for iid in (PULLBACK, WEAK):
        damage_peer_interior(value, cutoff, iid, attack)
    ctx = peer_context(value, cutoff)
    facts = sh.collect_exact_features(ctx, CONTINUATION)
    market, coverage = sh._r1_market_return_at(ctx, len(ctx.sessions)-1, 5)
    assert facts["returns"]["5"] == F(61051, 100000)
    assert facts["rs"]["5"] is None and market is None
    assert coverage["valid_count"] == 2 and coverage["expected_count"] == 4
    assert coverage["coverage"] == F(1, 2)
    assert coverage["status"] == "INSUFFICIENT_COVERAGE"
    assert sh.evaluate_exact_features(facts)["arms"]["STRONG_CONTINUATION"]["status"] == "UNKNOWN"


def test_strict_rs_sixty_percent_inclusive_boundary_uses_three_of_five_actual_peers():
    value, cutoff = strict_peer_payload(five=True)
    for iid in (PULLBACK, "TEST_OTHER_PEER"):
        damage_peer_interior(value, cutoff, iid, "basis")
    ctx = peer_context(value, cutoff)
    facts = sh.collect_exact_features(ctx, CONTINUATION)
    market, coverage = sh._r1_market_return_at(ctx, len(ctx.sessions)-1, 5)
    expected_market = (F(1, 10) + F(61051, 100000) + F(1, 50)) / 3
    assert len(ctx.eligible_ids) == coverage["expected_count"] == 5
    assert coverage["valid_count"] == 3 and coverage["coverage"] == F(3, 5)
    assert coverage["coverage"] == coverage["minimum_coverage"]
    assert coverage["status"] == "COMPLETE" and market == expected_market
    assert facts["rs"]["5"] == F(61051, 100000) - expected_market


def test_strict_rs_cache_is_independent_of_frozen_sam_endpoint_cache():
    from stockfish_core.yaogu import stage_aware_momentum as sam
    value, cutoff = strict_peer_payload()
    damage_peer_interior(value, cutoff, PULLBACK, "basis")
    ctx = peer_context(value, cutoff)
    end = len(ctx.sessions) - 1
    # Frozen V2 intentionally still uses its original endpoint return primitive.
    original_market = (F(1, 10) + F(61051, 100000) + F(19, 50) + F(1, 50)) / 4
    original_rs = sam._rs_at(ctx, CONTINUATION, end, 5)[0]
    assert original_rs == F(61051, 100000) - original_market
    sam_keys = {key: value for key, value in ctx.scalar_cache.items()
        if isinstance(key, tuple) and key[0] == "SAM_MARKET_RETURN_AT"}
    assert sam_keys
    facts = sh.collect_exact_features(ctx, CONTINUATION)
    strict_market = (F(1, 10) + F(61051, 100000) + F(1, 50)) / 3
    assert facts["rs"]["5"] == F(61051, 100000) - strict_market
    assert facts["rs"]["5"] != original_rs
    assert sam._rs_at(ctx, CONTINUATION, end, 5)[0] == original_rs
    assert all(ctx.scalar_cache[key] == value for key, value in sam_keys.items())
    assert any(isinstance(key, tuple) and key[0] == "SHORT_HORIZON_MARKET_RETURN_AT"
        for key in ctx.scalar_cache)


def test_full_pool_detail_is_retained_once_in_core_items():
    bundle, cutoff = strength_bundle()
    result = run(bundle, cutoff)
    assert len(result["items"]) == 4
    assert {row["instrument_id"] for row in result["items"]} == {START, CONTINUATION, PULLBACK, WEAK}
    assert "diagnostics" not in result
    assert result["diagnostics_reference"] == "items"
    assert result["diagnostics_detail_policy"] == "ALL_ELIGIBLE_ROW_DETAILS_RETAINED_ONCE_IN_ITEMS"
    assert all(row["exact_features"] and row["arms"] and row["current_source_refs"] for row in result["items"])
    # Each row's detailed facts have one serialized home, independent of unknown/context state.
    assert json.dumps(result, ensure_ascii=False).count('"exact_features"') == 4
