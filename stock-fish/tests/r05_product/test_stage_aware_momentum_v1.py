from __future__ import annotations

from datetime import time, timedelta
from decimal import Decimal

from stockfish_core.yaogu.stage_aware_momentum import (
    STAGE_AWARE_ID,
    run_stage_aware_momentum,
)
from stockfish_core.yaogu.strategy_experiments import run_pv_focus_preview, run_pv_observation, run_strategy_experiments
from tests.r05_product.test_theme_capital_trend_v1 import _clocks, _days, _fund, trend_bundle
from tests.yaogu.fixtures import TZ


def _payload(bundle):
    value = bundle.model_dump(mode="python")
    value["sessions"] = list(value["sessions"])
    value["bars"] = [dict(row) for row in value["bars"]]
    value["themes"] = [dict(row) for row in value["themes"]]
    value["funds"] = [dict(row) for row in value["funds"]]
    return value


def _q(value):
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _set_bar(row, *, open_=None, high=None, low=None, close=None, reference_prev_close=None, amount=None, turnover=None):
    prev = _q(reference_prev_close if reference_prev_close is not None else row["reference_prev_close"])
    open_value = _q(open_ if open_ is not None else prev)
    close_value = _q(close if close is not None else row["close"])
    high_value = _q(high if high is not None else max(open_value, close_value) * Decimal("1.01"))
    low_value = _q(low if low is not None else min(open_value, close_value) * Decimal("0.99"))
    row.update(
        open=open_value,
        high=high_value,
        low=low_value,
        close=close_value,
        reference_prev_close=prev,
        limit_up=(prev * Decimal("1.10")).quantize(Decimal("0.01")),
        limit_down=(prev * Decimal("0.90")).quantize(Decimal("0.01")),
        comparable_open=open_value,
        comparable_high=high_value,
        comparable_low=low_value,
        comparable_close=close_value,
    )
    if amount is not None:
        row["amount_cny"] = Decimal(str(amount))
    if turnover is not None:
        row["turnover_fraction"] = float(Decimal(str(turnover)))


def _set_tail_closes(payload, iid, closes):
    days = payload["sessions"][-len(closes):]
    prior = None
    for day, close in zip(days, closes):
        row = next(row for row in payload["bars"] if row["instrument_id"] == iid and row["session"] == day)
        prev = prior if prior is not None else row["reference_prev_close"]
        _set_bar(row, close=close, reference_prev_close=prev)
        prior = Decimal(str(close))


def _row(result, iid="TEST000"):
    return next(row for row in result["items"] if row["instrument_id"] == iid)


def _with_high_level_divergence_target():
    bundle, cutoff = trend_bundle()
    payload = _payload(bundle)
    _set_tail_closes(payload, "TEST000", ("10.00", "10.60", "10.90", "11.20", "11.50", "11.40"))
    last = payload["sessions"][-1]
    row = next(row for row in payload["bars"] if row["instrument_id"] == "TEST000" and row["session"] == last)
    _set_bar(
        row,
        open_="11.00",
        high="12.60",
        low="10.40",
        close="11.40",
        reference_prev_close="11.50",
        amount="600000000",
        turnover="0.08",
    )
    return type(bundle).model_validate(payload), cutoff


def test_stage_aware_is_additive_and_published_with_old_results():
    bundle, cutoff = trend_bundle()
    pv = run_pv_observation(bundle, cutoff=cutoff)
    focus = run_pv_focus_preview(pv)
    result = run_strategy_experiments(bundle, cutoff=cutoff)

    assert result["results"]["PV_OBSERVATION_V1"]["top_ids"] == pv["top_ids"]
    assert result["selection_results"]["PV_FOCUS_PREVIEW_V0.1"]["selected_ids"] == focus["selected_ids"]
    assert STAGE_AWARE_ID in result["results"]
    assert {row["strategy_id"] for row in result["comparison"]["strategies"]} >= {STAGE_AWARE_ID}
    assert result["results"][STAGE_AWARE_ID]["traceability"]["initial_parameters_are_not_user_rules"] is True
    assert result["results"][STAGE_AWARE_ID]["traceability"]["stage_labels_are_research_classifications"] is True


def test_high_level_divergence_is_not_rejected_by_amount_ratio():
    bundle, cutoff = _with_high_level_divergence_target()
    row = _row(run_stage_aware_momentum(bundle, cutoff=cutoff))

    assert row["status"] == "PASS"
    assert row["primary_stage"] == "HIGH_LEVEL_DIVERGENCE"
    assert row["A"] < 1.2
    assert row["condition_statuses"]["liquidity_bottom"] == "PASS"
    assert row["stage_candidates"]["HIGH_LEVEL_DIVERGENCE"]["amount_ratio_policy"] == "NOT_USED_FOR_HIGH_LEVEL_DIVERGENCE_GATE"


def test_high_level_divergence_records_path_recovery_without_clv_gate():
    bundle, cutoff = _with_high_level_divergence_target()
    row = _row(run_stage_aware_momentum(bundle, cutoff=cutoff))

    assert row["stage_candidates"]["HIGH_LEVEL_DIVERGENCE"]["status"] == "PASS"
    assert row["CLV"] < 0.70
    assert row["recovery_points_pct"] >= 5.0
    assert "SAM_HLD_RECOVERY_POINTS" in row["stage_candidates"]["HIGH_LEVEL_DIVERGENCE"]["repair_evidence"]


def test_trend_pullback_accepts_red_short_adjustment_without_daily_volume_expansion():
    bundle, cutoff = trend_bundle(pullback_days=2)
    payload = _payload(bundle)
    last = payload["sessions"][-1]
    bar = next(row for row in payload["bars"] if row["instrument_id"] == "TEST000" and row["session"] == last)
    _set_bar(bar, open_=bar["reference_prev_close"], close=Decimal(str(bar["reference_prev_close"])) * Decimal("0.99"))
    row = _row(run_stage_aware_momentum(type(bundle).model_validate(payload), cutoff=cutoff))

    assert row["status"] == "PASS"
    assert row["primary_stage"] == "TREND_PULLBACK"
    assert row["ret1_pct"] < 0
    assert row["stage_candidates"]["TREND_PULLBACK"]["pullback_days_below_ma5"] == 2


def test_missing_ma250_makes_trend_pullback_unknown_not_false():
    bundle, cutoff = trend_bundle(sessions=200, pullback_days=2)
    row = _row(run_stage_aware_momentum(bundle, cutoff=cutoff))

    assert row["stage_candidates"]["TREND_PULLBACK"]["status"] == "UNKNOWN"
    assert "SAM_MA250_WINDOW_UNAVAILABLE" in row["stage_candidates"]["TREND_PULLBACK"]["reason_codes"]


def test_rs_market_uses_fixed_mainboard_equal_weight_benchmark():
    bundle, cutoff = trend_bundle()
    payload = _payload(bundle)
    for iid in [f"TEST{i:03d}" for i in range(1, 8)]:
        _set_tail_closes(payload, iid, ("10.00", "10.10", "10.20", "10.30", "10.40", "10.00"))
    _set_tail_closes(payload, "TEST000", ("10.00", "10.20", "10.40", "10.60", "10.80", "11.40"))
    row = _row(run_stage_aware_momentum(type(bundle).model_validate(payload), cutoff=cutoff))

    assert row["relative_strength"]["primary_benchmark"] == "MAINBOARD_EQUAL_WEIGHT_PRICE_DIAGNOSTIC"
    assert row["RS_market_1d_pct"] > 0
    assert row["relative_strength"]["windows"]["1"]["status"] == "PASS"


def test_rs_sector_excludes_self_and_saves_all_themes_without_best_theme_claim():
    days = _days(280)
    theme_b = {
        "theme_id": "THEME_B",
        "name": "Fixture second theme",
        "theme_type": "EVENT",
        "member_ids": ("TEST000", "TEST001", "TEST002", "TEST003"),
        "effective_from": days[0],
        "definition_version": "TEST_FIXTURE_STAGE_AWARE_V1",
        "source_refs": ["fixture-theme"],
        **_clocks(days[0], time(9)),
    }
    bundle, cutoff = trend_bundle(extra_themes=(theme_b,))
    payload = _payload(bundle)
    for iid in ("TEST001", "TEST002", "TEST003"):
        _set_tail_closes(payload, iid, ("10.00", "10.10", "10.20", "10.30", "10.40", "10.00"))
    _set_tail_closes(payload, "TEST000", ("10.00", "10.20", "10.40", "10.60", "10.80", "11.20"))
    row = _row(run_stage_aware_momentum(type(bundle).model_validate(payload), cutoff=cutoff))

    assert len(row["sector_candidates"]) == 2
    assert [item["theme_id"] for item in row["sector_candidates"]] == ["THEME_A", "THEME_B"]
    assert row["RS_sector_1d_pct"] > 0
    assert row["conditions"]["sector_linkage"]["primary_theme_order"] == "theme_id_asc"


def test_liquidity_bottom_is_still_a_hard_formal_gate():
    low_amount, cutoff = trend_bundle(amount=Decimal("499999999.99"), turnover=Decimal("0.08"))
    row = _row(run_stage_aware_momentum(low_amount, cutoff=cutoff))
    assert row["status"] == "FAIL"
    assert "SAM_AMOUNT_BELOW_500M" in row["reason_codes"]

    low_turnover, cutoff = trend_bundle(amount=Decimal("600000000"), turnover=Decimal("0.069999"))
    row = _row(run_stage_aware_momentum(low_turnover, cutoff=cutoff))
    assert row["status"] == "FAIL"
    assert "SAM_TURNOVER_BELOW_7PCT" in row["reason_codes"]


def test_funds_grade_pass_fail_unknown_states():
    no_funds, cutoff = trend_bundle(funds=[])
    row = _row(run_stage_aware_momentum(no_funds, cutoff=cutoff))
    assert row["funds_status"] == "UNKNOWN"
    assert row["funds_grade"] == "UNKNOWN"

    weak_funds, cutoff = trend_bundle(funds=[_fund("TEST001", _days(280)[-1], Decimal("10000000"), Decimal("1000000"))])
    row = _row(run_stage_aware_momentum(weak_funds, cutoff=cutoff))
    assert row["funds_status"] == "FAIL"
    assert row["funds_grade"] == "WEAK"

    strong, cutoff = trend_bundle()
    row = _row(run_stage_aware_momentum(strong, cutoff=cutoff))
    assert row["funds_status"] == "PASS"
    assert row["funds_grade"] == "STRONG"


def test_future_bars_after_cutoff_do_not_change_historical_result():
    bundle, cutoff = _with_high_level_divergence_target()
    before = _row(run_stage_aware_momentum(bundle, cutoff=cutoff))
    payload = _payload(bundle)
    future = payload["sessions"][-1] + timedelta(days=1)
    payload["sessions"].append(future)
    for iid in [f"TEST{i:03d}" for i in range(8)]:
        payload["bars"].append({
            "instrument_id": iid,
            "session": future,
            "open": Decimal("1.00"),
            "high": Decimal("1.10"),
            "low": Decimal("0.90"),
            "close": Decimal("1.05"),
            "reference_prev_close": Decimal("1.00"),
            "volume_shares": Decimal("1000"),
            "amount_cny": Decimal("1000"),
            "limit_up": Decimal("1.10"),
            "limit_down": Decimal("0.90"),
            "limit_applicable": True,
            "limit_verified": True,
            "rule_id": "TEST_FIXTURE_10_PERCENT",
            "comparable_open": Decimal("1.00"),
            "comparable_high": Decimal("1.10"),
            "comparable_low": Decimal("0.90"),
            "comparable_close": Decimal("1.05"),
            "comparable_basis": "FUTURE_SHOULD_BE_IGNORED",
            "float_shares": Decimal("100000000"),
            "turnover_fraction": 0.01,
            "turnover_basis": "TEST_FIXTURE_VERIFIED_FLOAT",
            "source_refs": ["future-fixture"],
            **_clocks(future),
        })
    after = _row(run_stage_aware_momentum(type(bundle).model_validate(payload), cutoff=cutoff))

    assert after["primary_stage"] == before["primary_stage"]
    assert after["A"] == before["A"]
    assert after["recovery_points_pct"] == before["recovery_points_pct"]
