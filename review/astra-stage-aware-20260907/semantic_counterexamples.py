"""Read-only production strategy audit; all inputs are explicit TEST_FIXTURE.

Run from stock-fish with its existing virtualenv. Never writes a production DB.
The assertions document observed behavior, not acceptance of that behavior.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from fractions import Fraction

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "stock-fish"))
from stockfish_core.yaogu import stage_aware_momentum as sam
from tests.r05_product.test_stage_aware_momentum_v1 import (
    _payload, _row, _set_bar, _set_tail_closes, _with_high_level_divergence_target,
)
from tests.r05_product.test_theme_capital_trend_v1 import trend_bundle

evidence = {"evidence_level": "SYNTHETIC_COUNTEREXAMPLE", "input_kind": "TEST_FIXTURE", "cases": {}}


def run(payload, original, cutoff):
    bundle = type(original).model_validate(payload)
    assert bundle.coverage_type == "TEST_FIXTURE"
    return sam.run_stage_aware_momentum(bundle, cutoff=cutoff)


def summary(row):
    return {key: row.get(key) for key in (
        "status", "primary_stage", "alternate_stages", "stage_candidates", "A", "CLV",
        "ret1_pct", "ret3_pct", "ret5_pct", "recovery_points_pct", "low_return_pct",
        "RS_market_1d_pct", "relative_strength", "trend_strength", "market_status",
        "condition_statuses", "rank", "reference_score",
    )}


bundle, cutoff = _with_high_level_divergence_target()
baseline = _row(sam.run_stage_aware_momentum(bundle, cutoff=cutoff))
assert baseline["status"] == "PASS" and baseline["A"] < 1.2 and baseline["CLV"] < .7
evidence["cases"]["hld_bypasses_old_A_CLV"] = summary(baseline)

# A sixth-day turnover observation can change formal inclusion.
bundle, cutoff = trend_bundle()
payload = _payload(bundle)
for iid in [f"TEST{i:03d}" for i in range(8)]:
    _set_tail_closes(payload, iid, ("10", "10", "10", "10", "10", "10"))
_set_tail_closes(payload, "TEST000", ("10", "10.05", "10.10", "10.15", "10.20", "10.25"))
tail = payload["sessions"][-6:]
for bar in payload["bars"]:
    if bar["instrument_id"] == "TEST000" and bar["session"] in tail:
        bar["turnover_fraction"] = .08 if bar["session"] in (tail[0], tail[-2], tail[-1]) else .01
sixth_day = _row(run(payload, bundle, cutoff))
bar = next(b for b in payload["bars"] if b["instrument_id"] == "TEST000" and b["session"] == tail[0])
bar["turnover_fraction"] = .01
without_sixth = _row(run(payload, bundle, cutoff))
assert sixth_day["conditions"]["liquidity_bottom"]["high_turnover_days_5_including_today"] == 3
assert without_sixth["conditions"]["liquidity_bottom"]["high_turnover_days_5_including_today"] == 2
assert sixth_day["stage_candidates"]["ACCELERATION"]["status"] == "PASS"
assert without_sixth["stage_candidates"]["ACCELERATION"]["status"] == "FAIL"
assert sixth_day["status"] == "PASS" and without_sixth["status"] == "FAIL"
evidence["cases"]["sixth_day_turnover_changes_formal_inclusion"] = {
    "actual_latest_five_high_turnover_count": 2,
    "reported_with_sixth_day": 3,
    "with_sixth_day": summary(sixth_day), "without_sixth_day": summary(without_sixth),
}

# Fixed benchmark is not posthoc-selected, but any usable constituent means COMPLETE.
payload = _payload(bundle)
for bar in payload["bars"]:
    bar["limit_verified"] = False
    bar["limit_up"] = None
    bar["limit_down"] = None
unknown_limits = _row(run(payload, bundle, cutoff))
assert unknown_limits["market_status"]["instrument_limit"]["close_limit_up"] == "UNKNOWN"
assert unknown_limits["market_status"]["market_width"]["close_limit_count"] == 0
assert unknown_limits["market_status"]["status_label"] == "LOW"
evidence["cases"]["all_limit_events_unknown_becomes_market_LOW"] = unknown_limits["market_status"]
last = payload["sessions"][-1]
payload["bars"] = [b for b in payload["bars"] if b["instrument_id"] == "TEST000" or b["session"] != last]
thin = run(payload, bundle, cutoff)["market_benchmarks"]["MAINBOARD_EQUAL_WEIGHT_PRICE_DIAGNOSTIC"]
assert thin["1"]["status"] == "COMPLETE" and thin["1"]["coverage"] == .125
evidence["cases"]["benchmark_12point5pct_coverage_marked_COMPLETE"] = thin

# Current day closes at its low (-10%), zero recovery; 5-day RS alone supplies repair.
payload = _payload(bundle)
for iid in [f"TEST{i:03d}" for i in range(1, 8)]:
    _set_tail_closes(payload, iid, ("10", "10", "10", "10", "10", "10"))
_set_tail_closes(payload, "TEST000", ("10", "11", "11.5", "12", "12.5", "11.25"))
bar = next(b for b in payload["bars"] if b["instrument_id"] == "TEST000" and b["session"] == payload["sessions"][-1])
_set_bar(bar, open_="12.5", high="12.8", low="11.25", close="11.25", reference_prev_close="12.5")
no_repair = _row(run(payload, bundle, cutoff))
assert no_repair["recovery_points_pct"] == 0 and no_repair["CLV"] == 0
assert no_repair["stage_candidates"]["HIGH_LEVEL_DIVERGENCE"]["status"] == "PASS"
assert no_repair["stage_candidates"]["HIGH_LEVEL_DIVERGENCE"]["repair_evidence"] == ["SAM_HLD_RS_MARKET_SUPPORT", "SAM_HLD_RS_SECTOR_SUPPORT"]
evidence["cases"]["zero_recovery_close_at_limit_down_passes_HLD"] = summary(no_repair)

# Ordinary positive range is sufficient for recovery_points without a weak price.
_set_tail_closes(payload, "TEST000", ("10", "10.2", "10.4", "10.6", "10.8", "11.61"))
bar = next(b for b in payload["bars"] if b["instrument_id"] == "TEST000" and b["session"] == payload["sessions"][-1])
_set_bar(bar, open_="10.8", high="11.67", low="10.8", close="11.61", reference_prev_close="10.8")
all_positive = _row(run(payload, bundle, cutoff))
assert all_positive["low_return_pct"] == 0 and all_positive["recovery_points_pct"] == 7.5
assert all_positive["stage_candidates"]["HIGH_LEVEL_DIVERGENCE"]["status"] == "PASS"
evidence["cases"]["positive_range_without_underwater_low_passes_HLD"] = summary(all_positive)

# START does not require recency, breakout, first strong-state entry, or RS improvement.
start = sam._stage_start({"5": Fraction(1, 10000)}, {"amount_ratio_prior5": 1.2})
assert start["status"] == "PASS"
evidence["cases"]["start_only_A_and_tiny_positive_ret5"] = start

# Fourth below-MA5 session is correctly rejected; missing bar propagates UNKNOWN.
bundle, cutoff = trend_bundle()
payload = _payload(bundle)
_set_tail_closes(payload, "TEST000", ("23.60", "23.595", "23.59", "23.585"))
fourth = _row(run(payload, bundle, cutoff))
assert fourth["trend_strength"]["pullback_days_below_ma5"] == 4
assert fourth["stage_candidates"]["TREND_PULLBACK"]["status"] == "FAIL"
evidence["cases"]["fourth_below_MA5_day_rejected"] = summary(fourth)
missing = payload["sessions"][-2]
payload["bars"] = [b for b in payload["bars"] if not (b["instrument_id"] == "TEST000" and b["session"] == missing)]
missing_row = _row(run(payload, bundle, cutoff))
assert missing_row["stage_candidates"]["TREND_PULLBACK"]["status"] == "UNKNOWN"
evidence["cases"]["known_session_missing_bar_is_UNKNOWN"] = summary(missing_row)

# Missing whole calendar session can compress four below-MA5 sessions to three.
payload = _payload(bundle)
_set_tail_closes(payload, "TEST000", ("23.60", "23.595", "23.59", "23.585"))
payload["sessions"] = [s for s in payload["sessions"] if s != missing]
payload["bars"] = [b for b in payload["bars"] if b["session"] != missing]
compressed = _row(run(payload, bundle, cutoff))
assert compressed["trend_strength"]["pullback_days_below_ma5"] == 3
evidence["cases"]["missing_whole_session_compresses_four_to_three"] = summary(compressed)

# Healthy, moderate upward trend above MA5 has no TREND_RUN stage.
payload = _payload(bundle)
for iid in [f"TEST{i:03d}" for i in range(1, 8)]:
    _set_tail_closes(payload, iid, ("23.9", "23.9", "23.9", "23.9", "23.9", "23.9"))
for bar in payload["bars"]:
    if bar["instrument_id"] == "TEST000":
        bar["amount_cny"] = 600_000_000
        bar["turnover_fraction"] = .08 if bar["session"] == payload["sessions"][-1] else .01
flat_before = _row(run(payload, bundle, cutoff))
assert flat_before["trend_strength"]["status"] == "PASS"
assert flat_before["trend_strength"]["bullish_arrangement"] is True
assert flat_before["trend_strength"]["pullback_days_below_ma5"] == 0
assert flat_before["relative_strength"]["status"] == "PASS"
assert flat_before["status"] == "FAIL" and flat_before["primary_stage"] is None
evidence["cases"]["stage_gap_and_overlap_diagnostics"] = {
    "healthy_quiet_trend": summary(flat_before),
    "overlap": summary(baseline),
    "uncertainty_saved": "stage_candidates with statuses/reasons and alternate_stages; no calibrated confidence or transition history",
}

# A small threshold crossing changes primary phase without transition state.
def boundary(ret3):
    return sam._stages(
        {"3": ret3, "5": Fraction(".01"), "10": Fraction(".01")},
        {"daily_path_status": "AVAILABLE"},
        {"amount_ratio_prior5": 1.2, "high_turnover_days_5_including_today": 3},
        {"status": "FAIL"}, {}, {"windows": {"1": {"status": "FAIL"}}}, [],
    )
before, after = boundary(Fraction(".09999")), boundary(Fraction(".10001"))
assert before["primary_stage"] == "START" and after["primary_stage"] == "ACCELERATION"
evidence["cases"]["ret3_9point999_to_10point001_changes_primary"] = {"before": before, "after": after}

# UNKNOWN OR facts are collapsed into known FAIL in two stage predicates.
partial_hld = sam._stage_high_level_divergence(
    {"5": Fraction(".045"), "10": None},
    {"daily_path_status": "AVAILABLE"}, {}, {"windows": {}}, [],
)
assert partial_hld["status"] == "FAIL"
partial_accel = sam._stage_acceleration(
    {"3": Fraction(0), "5": Fraction(0), "10": Fraction(0)},
    {"high_turnover_days_5_including_today": 3},
    {"instrument_limit": {"close_limit_up": "UNKNOWN"}},
    {"windows": {str(w): {"status": "UNKNOWN"} for w in (1, 3, 5)}},
)
assert partial_accel["status"] == "FAIL"
evidence["cases"]["partial_missing_stage_OR_evidence_is_FALSE"] = {
    "hld_ret10_and_percentile_unknown": partial_hld,
    "acceleration_RS_and_limit_unknown": partial_accel,
}

# A sector declared UNKNOWN or FAIL can still support HLD through its numeric RS.
assert sam._sector_rs_support([{"status": "UNKNOWN", "windows": {"5": {"rs_sector_pct": 1}}}]) is True
evidence["cases"]["unqualified_sector_numeric_RS_supports_repair"] = {
    "input": {"status": "UNKNOWN", "windows": {"5": {"rs_sector_pct": 1}}},
    "output": True,
}

def numeric_leaves(value, prefix=""):
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in numeric_leaves(v, f"{prefix}.{k}" if prefix else k)]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in numeric_leaves(v, f"{prefix}[{i}]" )]
    return [{"path": prefix, "value": value}] if type(value) in (int, float) else []

leaves = numeric_leaves(sam.CONFIG)
evidence["configuration_numeric_leaves"] = {
    "count_including_window_lists_duplicates_and_version": len(leaves),
    "research_seed_count": len(numeric_leaves(sam.CONFIG["research_seed_parameters"])),
    "note": "A syntactic count, not independent statistical degrees of freedom; overlapping windows, duplicate values and unused/documentary fields are included.",
    "leaves": leaves,
}

output = Path(__file__).with_name("semantic_counterexamples.json")
output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"cases": len(evidence["cases"]), "output": str(output), "assertions": "all passed"}, ensure_ascii=False))
