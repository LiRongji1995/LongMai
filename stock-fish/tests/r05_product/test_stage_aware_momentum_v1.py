from __future__ import annotations

from datetime import time, timedelta
from decimal import Decimal

from stockfish_core.yaogu.stage_aware_momentum import (
    STAGE_AWARE_ID,
    run_stage_aware_momentum,
)
from stockfish_core.yaogu.strategy_experiments import run_pv_focus_preview, run_pv_observation, run_strategy_experiments
from tests.r05_product.test_theme_capital_trend_v1 import _clocks, _days, _fund, trend_bundle


def _payload(bundle):
    value = bundle.model_dump(mode="python")
    value["sessions"] = list(value["sessions"])
    value["bars"] = [dict(row) for row in value["bars"]]
    value["themes"] = [dict(row) for row in value["themes"]]
    value["funds"] = [dict(row) for row in value["funds"]]
    value["receipts"] = [dict(row) for row in value["receipts"]]
    return value


def _q(value):
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _set_bar(row, *, open_=None, high=None, low=None, close=None, reference_prev_close=None, amount=None, turnover=None, limit_verified=True):
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
        limit_applicable=True,
        limit_verified=limit_verified,
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


def _with_high_activity_weakening_target():
    bundle, cutoff = trend_bundle()
    payload = _payload(bundle)
    _set_tail_closes(payload, "TEST000", ("23.00", "24.38", "25.07", "25.76", "26.45", "26.22"))
    last = payload["sessions"][-1]
    row = next(row for row in payload["bars"] if row["instrument_id"] == "TEST000" and row["session"] == last)
    _set_bar(row, open_="27.00", high="27.50", low="25.80", close="26.22", reference_prev_close="26.45", amount="600000000", turnover="0.08")
    return type(bundle).model_validate(payload), cutoff


def _with_high_level_repair_target(*, close="26.68", low="25.20", high="28.20"):
    bundle, cutoff = trend_bundle()
    payload = _payload(bundle)
    _set_tail_closes(payload, "TEST000", ("23.80", "24.80", "25.25", "25.70", "26.45", close))
    last = payload["sessions"][-1]
    row = next(row for row in payload["bars"] if row["instrument_id"] == "TEST000" and row["session"] == last)
    _set_bar(row, open_="25.90", high=high, low=low, close=close, reference_prev_close="26.45", amount="600000000", turnover="0.08")
    return type(bundle).model_validate(payload), cutoff


def test_stage_aware_v2_is_active_core_and_published_with_old_results():
    bundle, cutoff = trend_bundle()
    pv = run_pv_observation(bundle, cutoff=cutoff)
    focus = run_pv_focus_preview(pv)
    result = run_strategy_experiments(bundle, cutoff=cutoff)

    assert STAGE_AWARE_ID == "STAGE_AWARE_MOMENTUM_V2"
    assert result["results"]["PV_OBSERVATION_V1"]["top_ids"] == pv["top_ids"]
    assert result["selection_results"]["PV_FOCUS_PREVIEW_V0.1"]["selected_ids"] == focus["selected_ids"]
    assert STAGE_AWARE_ID in result["results"]
    assert result["results"][STAGE_AWARE_ID]["traceability"]["product_role"] == "ACTIVE_DEFAULT_CORE_STRONG_STOCK_STRATEGY"
    assert result["results"][STAGE_AWARE_ID]["market_edge"] == "NOT_ESTABLISHED"
    assert "STAGE_AWARE_MOMENTUM_V1" in result["results"][STAGE_AWARE_ID]["traceability"]["legacy_read_aliases"]


def test_high_activity_old_strength_with_falling_momentum_is_watch_not_acceleration_focus():
    bundle, cutoff = _with_high_activity_weakening_target()
    row = _row(run_stage_aware_momentum(bundle, cutoff=cutoff))

    assert row["status"] == "WATCH"
    assert row["stage_candidates"]["ACCELERATION"]["setup_state"] == "WEAKENING_HIGH_ACTIVITY"
    assert row["stage_candidates"]["ACCELERATION"]["status"] == "FAIL"
    assert "SAM_WATCH_WEAKENING_HIGH_ACTIVITY" in row["watch_reasons"]


def test_high_level_repair_requires_today_repair_not_old_rs():
    bundle, cutoff = _with_high_level_repair_target(close="26.30", low="25.20", high="28.20")
    row = _row(run_stage_aware_momentum(bundle, cutoff=cutoff))

    repair = row["stage_candidates"]["HIGH_LEVEL_REPAIR"]
    assert repair["status"] == "FAIL"
    assert repair["subconditions"]["TODAY_DIVERGENCE"] == "PASS"
    assert repair["subconditions"]["TODAY_REPAIR"] == "FAIL"
    assert "SAM_HLR_CLV_REPAIR_NOT_MET" in repair["reason_codes"]


def test_high_level_repair_records_path_recovery_and_v1_alias():
    bundle, cutoff = _with_high_level_repair_target()
    row = _row(run_stage_aware_momentum(bundle, cutoff=cutoff))

    repair = row["stage_candidates"]["HIGH_LEVEL_REPAIR"]
    assert repair["status"] == "PASS"
    assert repair["legacy_setup_alias"] == "HIGH_LEVEL_DIVERGENCE"
    assert row["A"] < 1.2
    assert "SAM_HLR_RECOVERY_POINTS" in repair["repair_evidence"]
    assert repair["amount_ratio_policy"] == "NOT_USED_FOR_HIGH_LEVEL_REPAIR_GATE"


def test_verified_close_limit_down_invalidates_acceleration_and_repair():
    bundle, cutoff = _with_high_level_repair_target(close="23.81", low="23.81", high="26.70")
    payload = _payload(bundle)
    last = payload["sessions"][-1]
    row = next(row for row in payload["bars"] if row["instrument_id"] == "TEST000" and row["session"] == last)
    _set_bar(row, open_="25.80", high="26.70", low="23.81", close="23.81", reference_prev_close="26.45", amount="600000000", turnover="0.08")
    row["limit_down"] = Decimal("23.81")
    result_row = _row(run_stage_aware_momentum(type(bundle).model_validate(payload), cutoff=cutoff))

    assert result_row["status"] == "REJECTED"
    assert result_row["daily_path"]["close_limit_down"] == "TRUE"
    assert result_row["critical_counterevidence"]["status"] == "FAIL"
    assert result_row["stage_candidates"]["HIGH_LEVEL_REPAIR"]["setup_state"] == "LATE_CYCLE_WARNING"


def test_five_session_turnover_counts_exactly_recent_five_including_t():
    bundle, cutoff = trend_bundle()
    row = _row(run_stage_aware_momentum(bundle, cutoff=cutoff))

    assert row["high_turnover_days_5_including_T"] == 5
    assert row["conditions"]["liquidity_bottom"]["high_turnover_days_5_excluding_T"] == 5
    assert "high_turnover_days_5_including_today" not in row["conditions"]["liquidity_bottom"]


def test_market_limit_event_low_coverage_is_unknown_not_low_market():
    bundle, cutoff = trend_bundle()
    row = _row(run_stage_aware_momentum(bundle, cutoff=cutoff))

    assert row["market_context_state"] == "UNKNOWN"
    assert row["market_status"]["status"] == "UNKNOWN"
    assert row["market_status"]["market_width"]["limit_event_coverage"] < 0.60
    assert row["market_status"]["status_label"] != "LOW"


def test_late_or_invisible_theme_is_unknown_not_known_fail():
    days = _days(280)
    invisible_theme = {
        "theme_id": "THEME_LATE",
        "name": "Late theme",
        "theme_type": "EVENT",
        "member_ids": ("TEST000", "TEST001", "TEST002"),
        "effective_from": days[-1] + timedelta(days=1),
        "definition_version": "TEST_FIXTURE_STAGE_AWARE_V2",
        "source_refs": ["fixture-theme"],
        **_clocks(days[0], time(9)),
    }
    bundle, cutoff = trend_bundle(extra_themes=(invisible_theme,), theme_members=("TEST001", "TEST002", "TEST003"))
    row = _row(run_stage_aware_momentum(bundle, cutoff=cutoff))

    assert row["sector_status"] == "UNKNOWN"
    assert row["same_theme_context_status"] == "UNKNOWN"
    assert "SAM_NO_VISIBLE_AUTHORIZED_SECTOR_LINKAGE" in row["reason_codes"]


def test_cross_theme_sector_and_funds_do_not_combine():
    days = _days(280)
    theme_b = {
        "theme_id": "THEME_B",
        "name": "Money-only unrelated theme",
        "theme_type": "EVENT",
        "member_ids": ("TEST001", "TEST002", "TEST003"),
        "effective_from": days[0],
        "definition_version": "TEST_FIXTURE_STAGE_AWARE_V2",
        "source_refs": ["fixture-theme"],
        **_clocks(days[0], time(9)),
    }
    funds = [
        _fund("TEST001", days[-1], Decimal("70000000"), Decimal("50000000"), theme_id="THEME_B", seat="B1"),
        _fund("TEST002", days[-1], Decimal("70000000"), Decimal("50000000"), theme_id="THEME_B", seat="B2"),
    ]
    bundle, cutoff = trend_bundle(funds=funds, extra_themes=(theme_b,))
    row = _row(run_stage_aware_momentum(bundle, cutoff=cutoff))

    assert row["sector_status"] == "PASS"
    assert row["funds_status"] == "UNKNOWN"
    assert row["same_theme_context_status"] == "UNKNOWN"
    assert "SAM_FUNDS_NOT_CAPTURED_OR_COMPLETENESS_UNPROVEN" in row["reason_codes"]


def test_funds_proxy_future_and_conflict_rows_remain_unknown():
    days = _days(280)
    proxy = _fund("TEST001", days[-1], Decimal("70000000"), Decimal("50000000"), layer="CAPITAL_PROXY", seat="P1")
    proxy["classified_active"] = True
    proxy_bundle, cutoff = trend_bundle(funds=[proxy])
    proxy_row = _row(run_stage_aware_momentum(proxy_bundle, cutoff=cutoff))
    assert proxy_row["funds_status"] == "UNKNOWN"
    assert "SAM_FUNDS_PROXY_ONLY_PENDING_VERIFICATION" in proxy_row["reason_codes"]

    future = _fund("TEST001", days[-1], Decimal("70000000"), Decimal("50000000"), window_end=days[-1] + timedelta(days=1))
    future_bundle, cutoff = trend_bundle(funds=[future])
    future_row = _row(run_stage_aware_momentum(future_bundle, cutoff=cutoff))
    assert future_row["funds_status"] == "UNKNOWN"
    assert "SAM_FUNDS_EVIDENCE_REJECTED_OR_UNVERIFIED" in future_row["reason_codes"]

    conflict = [
        _fund("TEST001", days[-1], Decimal("70000000"), Decimal("50000000"), seat="C1", dedupe_key="same-disclosure"),
        _fund("TEST001", days[-1], Decimal("80000000"), Decimal("50000000"), seat="C1", dedupe_key="same-disclosure"),
    ]
    conflict_bundle, cutoff = trend_bundle(funds=conflict)
    conflict_row = _row(run_stage_aware_momentum(conflict_bundle, cutoff=cutoff))
    assert conflict_row["funds_status"] == "UNKNOWN"
    assert "SAM_FUNDS_DISCLOSURE_KEY_CONFLICT" in conflict_row["reason_codes"]


def test_auction_and_temporal_leadership_are_not_imputed_from_daily_bar():
    bundle, cutoff = _with_high_level_repair_target()
    row = _row(run_stage_aware_momentum(bundle, cutoff=cutoff))

    events = row["behavioral_confirmation"]["events"]
    assert events["AUCTION_WEAK_TO_STRONG"]["status"] == "UNKNOWN"
    assert events["AUCTION_WEAK_TO_STRONG"]["signal_policy"] == "NO_FINAL_AUCTION_SNAPSHOT_PROXY"
    assert row["temporal_leadership"]["status"] == "UNKNOWN"


def test_single_upper_shadow_is_not_critical_reject_but_repeated_unreclaimed_warns():
    bundle, cutoff = trend_bundle()
    payload = _payload(bundle)
    last = payload["sessions"][-1]
    bar = next(row for row in payload["bars"] if row["instrument_id"] == "TEST000" and row["session"] == last)
    _set_bar(bar, open_="23.90", high="25.00", low="23.70", close="23.95")
    single = _row(run_stage_aware_momentum(type(bundle).model_validate(payload), cutoff=cutoff))
    assert single["critical_counterevidence"]["status"] != "FAIL"
    assert single["supply_pressure"]["single_upper_shadow_policy"] == "NOT_CRITICAL_REJECTION_WITHOUT_REPEATED_UNRECLAIMED_SUPPLY"

    prior = payload["sessions"][-2]
    bar = next(row for row in payload["bars"] if row["instrument_id"] == "TEST000" and row["session"] == prior)
    _set_bar(bar, open_="23.80", high="25.10", low="23.60", close="23.90")
    repeated = _row(run_stage_aware_momentum(type(bundle).model_validate(payload), cutoff=cutoff))
    assert repeated["supply_pressure"]["status"] == "UNKNOWN"
    assert "SAM_REPEATED_UNRECLAIMED_UPPER_SHADOW_SUPPLY_WARNING" in repeated["critical_counterevidence"]["reason_codes"]


def test_v2_does_not_publish_global_pv_score_ranking():
    bundle, cutoff = trend_bundle()
    result = run_stage_aware_momentum(bundle, cutoff=cutoff)

    assert result["rank_semantics"] == "SETUP_AND_DECISION_TIER_RESEARCH_ORDER_NOT_GLOBAL_SCORE"
    assert result["tie_break"].startswith("setup_rank_features within decision tier and setup")
    assert result["full_eligible_pool_retained"] is True


def test_future_bars_after_cutoff_do_not_change_historical_result():
    bundle, cutoff = _with_high_activity_weakening_target()
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

    assert after["status"] == before["status"]
    assert after["stage_candidates"]["ACCELERATION"]["setup_state"] == before["stage_candidates"]["ACCELERATION"]["setup_state"]
    assert after["A"] == before["A"]
