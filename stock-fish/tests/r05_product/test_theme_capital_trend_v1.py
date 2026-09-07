from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu.strategy_experiments import run_pv_observation, run_strategy_experiments
from stockfish_core.yaogu.theme_capital_trend import (
    SIMPLE_TREND_TEMPLATE_ID,
    THEME_CAPITAL_TECHNICAL_ID,
    THEME_CAPITAL_TREND_ID,
    run_theme_capital_trend,
    run_theme_capital_trend_technical_only,
)
from scripts.r05_pv_observation_capture import pv_count
from tests.yaogu.fixtures import TZ


def _days(count):
    result, day = [], date(2025, 8, 18)
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day)
        day += timedelta(days=1)
    return result


def _clocks(day, at=time(15)):
    stamp = datetime.combine(day, at, TZ)
    return {"event_time": stamp, "source_published_at": stamp, "first_seen_at": stamp, "ingested_at": stamp}


def _receipt(receipt_id, payload, day):
    return {
        "receipt_id": receipt_id,
        "provider": "TEST_FIXTURE",
        "dataset": receipt_id,
        "content_hash": digest(payload),
        "field_version": "TEST_FIXTURE_THEME_CAPITAL_TREND_V1",
        "row_count": len(payload),
        "pagination_complete": True,
        "status": "COMPLETE",
        **_clocks(day, time(9)),
    }


def _series(index, days, *, strong_peer=False, pullback_days=0):
    base = [Decimal("10") + Decimal(k) * Decimal("0.05") + Decimal(index) * Decimal("0.01") for k in range(len(days))]
    if strong_peer:
        start = base[-6]
        for offset in range(6):
            base[-6 + offset] = start * (Decimal("1.00") + Decimal(offset) * Decimal("0.025"))
    if pullback_days:
        for offset in range(1, pullback_days + 1):
            base[-offset] = base[-pullback_days - 1] * (Decimal("0.985") - Decimal(offset - 1) * Decimal("0.002"))
    return [value.quantize(Decimal("0.0001")) for value in base]


def trend_bundle(*, amount=Decimal("600000000"), turnover=Decimal("0.08"), sessions=280, pullback_days=0, funds=None, theme_members=None, extra_themes=()):
    days = _days(sessions)
    ids = [f"TEST{i:03d}" for i in range(8)]
    securities, bars = [], []
    for number, iid in enumerate(ids):
        securities.append({
            "instrument_id": iid,
            "exchange": "SSE" if number % 2 == 0 else "SZSE",
            "board": "MAIN",
            "security_type": "A_COMMON",
            "listed_on": days[0],
            "effective_from": days[0],
            "st": False,
            "suspended": False,
            "name": iid,
            "source_refs": ["fixture-master"],
            **_clocks(days[0], time(9)),
        })
        closes = _series(number, days, strong_peer=1 <= number <= 6, pullback_days=pullback_days if number == 0 else 0)
        for index, day in enumerate(days):
            close = closes[index]
            prev = closes[index - 1] if index else close / Decimal("1.01")
            opened = prev
            high = max(opened, close) * Decimal("1.01")
            low = min(opened, close) * Decimal("0.99")
            current_amount = amount if number == 0 else Decimal("700000000")
            prior_amount = Decimal("1000000000") if number == 0 else Decimal("500000000")
            day_amount = current_amount if day == days[-1] else prior_amount if day in days[-6:-1] else Decimal("300000000")
            current_turnover = turnover if number == 0 else Decimal("0.08")
            bars.append({
                "instrument_id": iid,
                "session": day,
                "open": opened.quantize(Decimal("0.0001")),
                "high": high.quantize(Decimal("0.0001")),
                "low": low.quantize(Decimal("0.0001")),
                "close": close,
                "reference_prev_close": prev.quantize(Decimal("0.0001")),
                "volume_shares": Decimal("8000000"),
                "amount_cny": day_amount,
                "limit_up": (prev * Decimal("1.10")).quantize(Decimal("0.01")),
                "limit_down": (prev * Decimal("0.90")).quantize(Decimal("0.01")),
                "limit_applicable": True,
                "limit_verified": True,
                "rule_id": "TEST_FIXTURE_10_PERCENT",
                "comparable_open": opened.quantize(Decimal("0.0001")),
                "comparable_high": high.quantize(Decimal("0.0001")),
                "comparable_low": low.quantize(Decimal("0.0001")),
                "comparable_close": close,
                "comparable_basis": "TEST_FIXTURE_NO_ACTION",
                "float_shares": Decimal("100000000"),
                "turnover_fraction": float(current_turnover if day == days[-1] else Decimal("0.08")),
                "turnover_basis": "TEST_FIXTURE_VERIFIED_FLOAT",
                "source_refs": ["fixture-bars"],
                **_clocks(day),
            })
    members = tuple(theme_members or ids[:7])
    theme = {
        "theme_id": "THEME_A",
        "name": "Fixture active theme",
        "theme_type": "EVENT",
        "member_ids": members,
        "effective_from": days[0],
        "definition_version": "TEST_FIXTURE_THEME_CAPITAL_TREND_V1",
        "source_refs": ["fixture-theme"],
        **_clocks(days[0], time(9)),
    }
    themes = [theme, *extra_themes]
    default_funds = [
        _fund("TEST001", days[-1], Decimal("60000000"), Decimal("40000000"), seat="S1"),
        _fund("TEST002", days[-2], Decimal("60000000"), Decimal("30000000"), seat="S2"),
    ]
    payloads = [("fixture-master", securities), ("fixture-bars", bars), ("fixture-theme", themes)]
    return MarketInput.model_validate({
        "observation_date": days[-1],
        "sessions": days,
        "universe": {
            "snapshot_id": "TEST_FIXTURE_THEME_CAPITAL_UNIVERSE",
            "securities": securities,
            "complete": True,
            "expected_count": len(ids),
            "source_refs": ["fixture-master"],
            **_clocks(days[0], time(9)),
        },
        "bars": bars,
        "themes": themes,
        "receipts": [_receipt(rid, payload, days[0]) for rid, payload in payloads],
        "funds": default_funds if funds is None else funds,
        "coverage_type": "TEST_FIXTURE",
    }), datetime.combine(days[-1], time(20, 30), TZ)


def _fund(iid, day, buy, net, *, theme_id="THEME_A", layer="SUPPLIER_CLASSIFIED_ACTIVE_SEAT", seat="S1", window_start=None, window_end=None, dedupe_key=None):
    return {
        "instrument_id": iid,
        "theme_id": theme_id,
        "session": day,
        "window_start": window_start or day,
        "window_end": window_end or day,
        "seat_id": seat,
        "buy_amount_cny": buy,
        "net_amount_cny": net,
        "source_layer": layer,
        **_clocks(day, time(16)),
        **({"dedupe_key": dedupe_key} if dedupe_key else {}),
    }


def _row(result, iid="TEST000"):
    return next(row for row in result["items"] if row["instrument_id"] == iid)


def test_full_strategy_selects_stock_rejected_by_old_pv_ar_filter():
    bundle, cutoff = trend_bundle()
    pv = run_pv_observation(bundle, cutoff=cutoff)
    assert "TEST000" not in {row["instrument_id"] for row in pv["items"]}
    assert any(row["instrument_id"] == "TEST000" for row in pv["eligible_feature_items"])

    result = run_theme_capital_trend(bundle, cutoff=cutoff)
    row = _row(result)
    assert row["status"] == "PASS"
    assert row["rank"] >= 1
    assert row["reference_score_status"] == "AVAILABLE"
    assert row["condition_statuses"]["theme_linkage"] == "PASS"
    assert row["condition_statuses"]["capital_linkage"] == "PASS"


def test_amount_and_turnover_thresholds_are_inclusive_boundaries():
    exact, cutoff = trend_bundle(amount=Decimal("500000000"), turnover=Decimal("0.07"))
    assert _row(run_theme_capital_trend(exact, cutoff=cutoff))["status"] == "PASS"

    low_amount, cutoff = trend_bundle(amount=Decimal("499999999.99"))
    row = _row(run_theme_capital_trend(low_amount, cutoff=cutoff))
    assert row["status"] == "FAIL"
    assert "TCT_AMOUNT_BELOW_500M" in row["reason_codes"]

    low_turnover, cutoff = trend_bundle(turnover=Decimal("0.069999"))
    row = _row(run_theme_capital_trend(low_turnover, cutoff=cutoff))
    assert row["status"] == "FAIL"
    assert "TCT_TURNOVER_BELOW_7PCT" in row["reason_codes"]


def test_missing_ma250_is_unknown_not_imputed():
    bundle, cutoff = trend_bundle(sessions=200)
    row = _row(run_theme_capital_trend_technical_only(bundle, cutoff=cutoff))
    assert row["status"] == "UNKNOWN"
    assert "TCT_MA250_WINDOW_UNAVAILABLE" in row["reason_codes"]


def test_one_to_three_day_pullback_is_allowed_but_four_days_fails():
    two_day, cutoff = trend_bundle(pullback_days=2)
    row = _row(run_theme_capital_trend(two_day, cutoff=cutoff))
    assert row["status"] == "PASS"
    assert row["shape_type"] == "PULLBACK_1_TO_3_SESSIONS"

    four_day, cutoff = trend_bundle(pullback_days=4)
    row = _row(run_theme_capital_trend(four_day, cutoff=cutoff))
    assert row["status"] == "FAIL"
    assert "TCT_PULLBACK_BEYOND_3_SESSIONS" in row["reason_codes"]


def test_theme_peer_gate_excludes_self_and_requires_five_other_members():
    bundle, cutoff = trend_bundle(theme_members=("TEST000", "TEST001", "TEST002", "TEST003", "TEST004"))
    row = _row(run_theme_capital_trend(bundle, cutoff=cutoff))
    assert row["condition_statuses"]["theme_linkage"] == "FAIL"
    assert "TCT_THEME_PEER_COUNT_BELOW_5" in row["reason_codes"]


def test_capital_must_match_the_same_passing_theme_not_another_theme():
    days = _days(280)
    theme_b = {
        "theme_id": "THEME_B",
        "name": "Fixture weak theme with money",
        "theme_type": "EVENT",
        "member_ids": ("TEST000", "TEST001", "TEST002"),
        "effective_from": days[0],
        "definition_version": "TEST_FIXTURE_THEME_CAPITAL_TREND_V1",
        "source_refs": ["fixture-theme"],
        **_clocks(days[0], time(9)),
    }
    funds = [
        _fund("TEST001", days[-1], Decimal("70000000"), Decimal("50000000"), theme_id="THEME_B", seat="B1"),
        _fund("TEST002", days[-1], Decimal("70000000"), Decimal("50000000"), theme_id="THEME_B", seat="B2"),
    ]
    bundle, cutoff = trend_bundle(funds=funds, extra_themes=(theme_b,))
    row = _row(run_theme_capital_trend(bundle, cutoff=cutoff))
    assert row["status"] == "FAIL"
    assert row["condition_statuses"]["theme_linkage"] == "PASS"
    assert row["condition_statuses"]["capital_linkage"] == "FAIL"
    assert "TCT_NO_ACTIVE_CLASSIFIED_SECTOR_CAPITAL_EVIDENCE" in row["reason_codes"]


def test_proxy_only_capital_is_unknown_and_not_a_formal_pass():
    days = _days(280)
    funds = [
        _fund("TEST001", days[-1], Decimal("70000000"), Decimal("50000000"), layer="CAPITAL_PROXY", seat="P1"),
        _fund("TEST002", days[-1], Decimal("70000000"), Decimal("50000000"), layer="CAPITAL_PROXY", seat="P2"),
    ]
    bundle, cutoff = trend_bundle(funds=funds)
    row = _row(run_theme_capital_trend(bundle, cutoff=cutoff))
    assert row["status"] == "UNKNOWN"
    assert row["condition_statuses"]["capital_linkage"] == "UNKNOWN"
    assert "TCT_CAPITAL_PROXY_ONLY_PENDING_VERIFICATION" in row["reason_codes"]


def test_overlapping_classified_windows_are_unknown_before_summing_money():
    days = _days(280)
    funds = [
        _fund("TEST001", days[-1], Decimal("80000000"), Decimal("50000000"), seat="SAME", window_start=days[-3], window_end=days[-1]),
        _fund("TEST001", days[-1], Decimal("80000000"), Decimal("50000000"), seat="SAME", window_start=days[-2], window_end=days[-1]),
        _fund("TEST002", days[-1], Decimal("80000000"), Decimal("50000000"), seat="OTHER"),
    ]
    bundle, cutoff = trend_bundle(funds=funds)
    row = _row(run_theme_capital_trend(bundle, cutoff=cutoff))
    assert row["status"] == "UNKNOWN"
    assert "TCT_CAPITAL_OVERLAPPING_CLASSIFIED_WINDOWS" in row["reason_codes"]


def test_strategy_experiment_publishes_new_arms_without_replacing_old_ones():
    bundle, cutoff = trend_bundle()
    result = run_strategy_experiments(bundle, cutoff=cutoff)
    assert {"PV_OBSERVATION_V1", "PV_FOCUS_PREVIEW_V0.1"} <= set(result["results"]) | set(result["selection_results"])
    assert {THEME_CAPITAL_TECHNICAL_ID, THEME_CAPITAL_TREND_ID, SIMPLE_TREND_TEMPLATE_ID} <= set(result["results"])
    strategies = {row["strategy_id"] for row in result["comparison"]["strategies"]}
    assert {THEME_CAPITAL_TECHNICAL_ID, THEME_CAPITAL_TREND_ID, SIMPLE_TREND_TEMPLATE_ID} <= strategies
    assert result["results"][THEME_CAPITAL_TREND_ID]["traceability"]["initial_parameters_are_not_user_rules"] is True
    assert result["results"][THEME_CAPITAL_TREND_ID]["parameter_status_label_zh"] == "尚未验证"


def test_capture_script_accepts_current_and_legacy_count_names():
    assert pv_count({"filter_passed": 525}, "ranked", "filter_passed") == 525
    assert pv_count({"ranked": 17, "filter_passed": 525}, "ranked", "filter_passed") == 17
    assert pv_count({}, "missing_required_data", "missing") == 0
