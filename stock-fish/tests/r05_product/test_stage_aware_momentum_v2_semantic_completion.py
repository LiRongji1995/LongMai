from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time, timedelta
from decimal import Decimal

from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.strategy_tracking import evaluate_strategy_experiment_outcomes
from stockfish_core.yaogu import stage_aware_momentum as sam
from stockfish_core.yaogu.stage_aware_momentum import STAGE_AWARE_ID, run_stage_aware_momentum
from tests.r05_product.test_stage_aware_momentum_v1 import (
    _payload,
    _q,
    _row,
    _set_bar,
    _set_tail_closes,
    _with_high_activity_weakening_target,
    _with_high_level_repair_target,
)
from tests.support.stage_market_fixture import _clocks, _days, trend_bundle
from tests.yaogu.fixtures import TZ


def test_behavioral_fail_cannot_focus_for_setup_that_requires_it():
    decision = sam._decision(
        sam._condition("PASS"),
        sam._condition("PASS"),
        sam._condition("PASS", details={"primary_stage": "HIGH_LEVEL_REPAIR", "stage_candidates": {"ACCELERATION": {"setup_state": "ACCELERATION_NOT_CONFIRMED"}}}),
        sam._condition("FAIL", ["SAM_INTRADAY_WEAK_TO_STRONG_NOT_CONFIRMED_FROM_DAILY_OHLC"]),
        sam._condition("PASS"),
        sam._condition("PASS"),
        sam._condition("PASS"),
    )

    assert decision["decision_status"] == "WATCH"
    assert "SAM_WATCH_BEHAVIORAL_CONFIRMATION_FAILED_FOR_SETUP" in decision["watch_reasons"]


def test_acceleration_positive_returns_but_equal_window_momentum_falling_not_confirmed():
    bundle, cutoff = trend_bundle()
    payload = _payload(bundle)
    _set_tail_closes(payload, "TEST000", ("20", "24", "28", "31", "33", "35", "36"))
    last = payload["sessions"][-1]
    row_data = next(row for row in payload["bars"] if row["instrument_id"] == "TEST000" and row["session"] == last)
    _set_bar(row_data, open_="35.20", high="36.60", low="34.90", close="36.00", reference_prev_close="35.00", amount="600000000", turnover="0.08")
    bundle = type(bundle).model_validate(payload)
    row = _row(run_stage_aware_momentum(bundle, cutoff=cutoff))

    accel = row["stage_candidates"]["ACCELERATION"]
    assert row["ret1_pct"] > 0
    assert row["ret3_pct"] > 0
    assert accel["strength_momentum_details"]["return_momentum"] == "FALLING"
    assert accel["setup_state"] == "WEAKENING_HIGH_ACTIVITY"
    assert accel["status"] == "FAIL"


def test_acceleration_weakening_does_not_suppress_valid_high_level_repair():
    decision = sam._decision(
        sam._condition("PASS"),
        sam._condition("PASS"),
        sam._condition("PASS", details={
            "primary_stage": "HIGH_LEVEL_REPAIR",
            "stage_candidates": {"ACCELERATION": {"setup_state": "WEAKENING_HIGH_ACTIVITY"}},
        }),
        sam._condition("PASS"),
        sam._condition("PASS"),
        sam._condition("PASS"),
        sam._condition("PASS"),
    )

    assert decision["decision_status"] == "FOCUS"
    assert "SAM_WATCH_WEAKENING_HIGH_ACTIVITY" not in decision.get("watch_reasons", [])


def test_sector_level_comes_from_peers_not_target_stock_return():
    bundle, cutoff = trend_bundle()
    payload = _payload(bundle)
    _set_tail_closes(payload, "TEST000", ("20", "21", "22", "23", "24", "25"))
    for peer in [f"TEST{i:03d}" for i in range(1, 7)]:
        _set_tail_closes(payload, peer, ("20", "19.8", "19.6", "19.4", "19.2", "19.0"))
    row = _row(run_stage_aware_momentum(type(bundle).model_validate(payload), cutoff=cutoff))

    sector = row["sector_candidates"][0]
    assert row["ret5_pct"] > 20
    assert sector["sector_level_state"] == "LOW"
    assert sector["sector_level_raw_pct"] < 0


def test_sector_momentum_compares_recent_and_previous_equal_windows():
    bundle, cutoff = trend_bundle()
    payload = _payload(bundle)
    for peer in [f"TEST{i:03d}" for i in range(1, 7)]:
        _set_tail_closes(payload, peer, ("10", "11", "12", "12.1", "12.2", "12.3", "12.4"))
    row = _row(run_stage_aware_momentum(type(bundle).model_validate(payload), cutoff=cutoff))

    momentum = row["sector_candidates"][0]["sector_momentum"]
    assert momentum["recent_peer_return_pct"] > 0
    assert momentum["previous_peer_return_pct"] > momentum["recent_peer_return_pct"]
    assert momentum["sector_momentum_state"] == "WEAKENING"


def test_market_expansion_cannot_mask_severe_divergence():
    summary = {
        "instrument_limits": {},
        "close_limit_count": 30,
        "touched_not_closed_count": 25,
        "close_limit_down_count": 3,
        "verified_max_height": 5,
        "known_limit_event_count": 100,
        "unknown_limit_event_count": 0,
        "expected_limit_event_count": 100,
        "limit_event_coverage": sam.Fraction(1),
    }

    state, reasons = sam._market_raw_state(summary, {})

    assert state == "DIVERGENCE"
    assert "SAM_MARKET_EXPANSION_WITH_MAJOR_DIVERGENCE_PRESSURE" in reasons


def test_market_momentum_unknown_when_only_evidence_coverage_changes(monkeypatch):
    bundle, cutoff = trend_bundle()
    ctx = sam._prepare_context(bundle, cutoff)
    end_index = sam._session_index(ctx)

    def fake_summary(ctx_arg, end_index_arg, *, include_instruments=False):
        coverage = sam.Fraction(3, 5) if end_index_arg == end_index - 1 else sam.Fraction(1)
        known = 60 if end_index_arg == end_index - 1 else 100
        return {
            "instrument_limits": {},
            "close_limit_count": int(known * 0.10),
            "touched_not_closed_count": int(known * 0.05),
            "close_limit_down_count": 0,
            "verified_max_height": 1,
            "known_limit_event_count": known,
            "unknown_limit_event_count": 100 - known,
            "expected_limit_event_count": 100,
            "limit_event_coverage": coverage,
        }

    monkeypatch.setattr(sam, "_market_day_summary", fake_summary)
    market = sam._market_position_context(ctx, end_index)

    assert market["market_momentum"] == "UNKNOWN"
    assert "SAM_MARKET_MOMENTUM_COVERAGE_NOT_COMPARABLE" in market["market_momentum_reason_codes"]


def test_sector_momentum_requires_matched_peer_cohort(monkeypatch):
    seed = {
        "momentum_equal_window_sessions": 3,
        "min_peer_return_coverage_fraction_inclusive": 0.50,
        "momentum_stable_band_fraction_inclusive": 0.001,
    }

    def fake_price_return(ctx_arg, peer, end_index_arg, window_arg):
        if end_index_arg == 10 and peer in {"A", "B"}:
            return sam.Fraction(1, 10)
        if end_index_arg == 7 and peer in {"C", "D"}:
            return sam.Fraction(1, 20)
        return None

    monkeypatch.setattr(sam, "_price_return", fake_price_return)
    momentum = sam._sector_momentum_stats(object(), ["A", "B", "C", "D"], 10, seed)

    assert momentum["recent_coverage"]["coverage_status"] == "COMPLETE"
    assert momentum["previous_coverage"]["coverage_status"] == "COMPLETE"
    assert momentum["matched_peer_count"] == 0
    assert momentum["sector_momentum_state"] == "UNKNOWN"


def test_sector_relative_level_distinguishes_broad_market_rally():
    bundle, cutoff = trend_bundle()
    payload = _payload(bundle)
    for member in [security["instrument_id"] for security in payload["universe"]["securities"]]:
        _set_tail_closes(payload, member, ("20", "20.4", "20.8", "21.2", "21.6", "22.0"))
    row = _row(run_stage_aware_momentum(type(bundle).model_validate(payload), cutoff=cutoff))

    sector = row["sector_candidates"][0]
    assert sector["sector_absolute_level"] == "HIGH"
    assert sector["sector_relative_level"] == "MARKET_IN_LINE"
    assert sector["sector_level_state"] == "NORMAL"


def test_limit_streak_role_feature_is_peer_percentile_not_constant():
    from tests.yaogu.fixtures import make_bundle

    bundle, cutoff = make_bundle(7)
    row = _row(run_stage_aware_momentum(bundle, cutoff=cutoff))
    sector = row["sector_candidates"][0]

    assert sector["stock_role_features"]["limit_streak_percentile"] not in {50.0, 100.0}
    assert sector["limit_streak_evidence"]["status"] == "COMPLETE"
    assert sector["stock_role_known_feature_count"] + sector["stock_role_unknown_feature_count"] == len(sector["stock_role_features"])


def test_limit_streak_role_feature_unknown_when_peer_limit_evidence_sparse():
    from tests.yaogu.fixtures import make_bundle

    bundle, cutoff = make_bundle(7)
    payload = _payload(bundle)
    latest = payload["sessions"][-1]
    for row in payload["bars"]:
        if row["session"] == latest and row["instrument_id"] != "TEST000":
            row["limit_verified"] = False
            row["limit_up"] = None
            row["limit_down"] = None
    result = _row(run_stage_aware_momentum(type(bundle).model_validate(payload), cutoff=cutoff))
    sector = result["sector_candidates"][0]

    assert sector["stock_role_features"]["limit_streak_percentile"] is None
    assert sector["limit_streak_evidence"]["status"] == "UNKNOWN"


def _liquidity_row(*, amount, turnover):
    bundle, cutoff = trend_bundle()
    payload = _payload(bundle)
    last = payload["sessions"][-1]
    row = next(row for row in payload["bars"] if row["instrument_id"] == "TEST000" and row["session"] == last)
    row["amount_cny"] = Decimal(str(amount)) if amount is not None else None
    row["turnover_fraction"] = float(Decimal(str(turnover))) if turnover is not None else None
    bundle = type(bundle).model_validate(payload)
    return _row(run_stage_aware_momentum(bundle, cutoff=cutoff))


def test_liquidity_hard_fail_takes_precedence_over_unknown_turnover():
    row = _liquidity_row(amount="499999999.99", turnover=None)

    assert row["liquidity_status"] == "FAIL"
    assert row["status"] == "REJECTED"
    assert "SAM_DECISION_LIQUIDITY_HARD_FAIL" in row["reason_codes"]
    assert row["status"] != "WATCH"


def test_liquidity_amount_pass_turnover_unknown_is_unknown():
    row = _liquidity_row(amount="500000000", turnover=None)

    assert row["liquidity_status"] == "UNKNOWN"
    assert row["conditions"]["liquidity_bottom"]["reason_codes"] == ["SAM_TURNOVER_UNAVAILABLE"]


def test_liquidity_amount_fail_turnover_pass_is_fail():
    row = _liquidity_row(amount="499999999.99", turnover="0.07")

    assert row["liquidity_status"] == "FAIL"
    assert row["conditions"]["liquidity_bottom"]["reason_codes"] == ["SAM_AMOUNT_BELOW_500M"]


def test_liquidity_amount_and_turnover_pass():
    row = _liquidity_row(amount="500000000", turnover="0.07")

    assert row["liquidity_status"] == "PASS"
    assert row["conditions"]["liquidity_bottom"]["reason_codes"] == []


def test_liquidity_fail_is_rejected_before_behavioral_watch():
    decision = sam._decision(
        sam._condition("FAIL", ["SAM_AMOUNT_BELOW_500M"]),
        sam._condition("PASS"),
        sam._condition("PASS", details={"primary_stage": "HIGH_LEVEL_REPAIR", "stage_candidates": {}}),
        sam._condition("FAIL", ["SAM_INTRADAY_WEAK_TO_STRONG_NOT_CONFIRMED_FROM_DAILY_OHLC"]),
        sam._condition("PASS"),
        sam._condition("PASS"),
        sam._condition("PASS"),
    )

    assert decision["decision_status"] == "REJECTED"
    assert "SAM_DECISION_LIQUIDITY_HARD_FAIL" in decision["reason_codes"]


def test_sector_coverage_is_independent_per_window():
    bundle, cutoff = trend_bundle()
    payload = _payload(bundle)
    t_minus_1 = payload["sessions"][-2]
    weak_peers = {f"TEST{i:03d}" for i in range(1, 5)}
    payload["bars"] = [
        row for row in payload["bars"]
        if not (row["instrument_id"] in weak_peers and row["session"] == t_minus_1)
    ]
    row = _row(run_stage_aware_momentum(type(bundle).model_validate(payload), cutoff=cutoff))
    windows = row["sector_candidates"][0]["windows"]

    assert windows["1"]["coverage_status"] == "INSUFFICIENT_COVERAGE"
    assert windows["1"]["rs_sector_pct"] is None
    assert windows["5"]["coverage_status"] == "COMPLETE"


def test_sector_negative_context_separates_from_unknown_context():
    bundle, cutoff = trend_bundle()
    payload = _payload(bundle)
    for peer in [f"TEST{i:03d}" for i in range(1, 7)]:
        _set_tail_closes(payload, peer, ("20", "19.8", "19.6", "19.4", "19.2", "19.0"))
    negative = _row(run_stage_aware_momentum(type(bundle).model_validate(payload), cutoff=cutoff))

    no_theme, cutoff = trend_bundle(theme_members=("TEST001", "TEST002", "TEST003"))
    unknown = _row(run_stage_aware_momentum(no_theme, cutoff=cutoff))

    assert negative["sector_candidates"][0]["setup_compatibility"]["ACCELERATION"]["context_status"] == "NEGATIVE"
    assert unknown["sector_status"] == "UNKNOWN"
    assert "SAM_NO_VISIBLE_AUTHORIZED_SECTOR_LINKAGE" in unknown["reason_codes"]


def test_complete_no_record_funds_requires_completeness_evidence():
    days = _days(280)
    no_record = {
        "theme_id": "THEME_A",
        "record_type": "FUNDS_QUERY_COMPLETENESS",
        "completion_status": "COMPLETE_NO_RECORD",
        "query_status": "COMPLETE",
        "row_count": 0,
        "query_scope": "THEME_A_PEERS_LOOKBACK_5",
        "expected_scope": tuple(f"TEST{i:03d}" for i in range(7)),
        "actual_scope": (),
        "session": days[-1],
        "window_start": days[-5],
        "window_end": days[-1],
        "source_refs": ["fixture-funds"],
        **_clocks(days[-1], time(16)),
    }
    complete_bundle, cutoff = trend_bundle(funds=[no_record])
    complete_row = _row(run_stage_aware_momentum(complete_bundle, cutoff=cutoff))
    missing_bundle, cutoff = trend_bundle(funds=[])
    missing_row = _row(run_stage_aware_momentum(missing_bundle, cutoff=cutoff))

    assert complete_row["funds_status"] == "FAIL"
    assert "SAM_FUNDS_COMPLETE_NO_RECORD" in complete_row["reason_codes"]
    assert missing_row["funds_status"] == "UNKNOWN"
    assert "SAM_FUNDS_DATA_UNAVAILABLE" in missing_row["reason_codes"]


def test_start_and_acceleration_use_same_momentum_primitive():
    bundle, cutoff = trend_bundle()
    row = _row(run_stage_aware_momentum(bundle, cutoff=cutoff))

    start = row["stage_candidates"]["START"]
    accel = row["stage_candidates"]["ACCELERATION"]
    assert start["shared_momentum_primitive"] == row["shared_strength_momentum"]["calculation_id"]
    assert accel["shared_momentum_primitive"] == row["shared_strength_momentum"]["calculation_id"]


def test_high_level_repair_background_excludes_t_session():
    bundle, cutoff = trend_bundle()
    payload = _payload(bundle)
    _set_tail_closes(payload, "TEST000", ("20.00", "20.10", "20.20", "20.30", "20.40", "22.70"))
    last = payload["sessions"][-1]
    row = next(row for row in payload["bars"] if row["instrument_id"] == "TEST000" and row["session"] == last)
    _set_bar(row, open_="20.50", high="22.90", low="19.10", close="22.70", reference_prev_close="20.40", amount="600000000", turnover="0.08")

    result_row = _row(run_stage_aware_momentum(type(bundle).model_validate(payload), cutoff=cutoff))
    repair = result_row["stage_candidates"]["HIGH_LEVEL_REPAIR"]
    assert result_row["ret5_pct"] > 10
    assert repair["pre_event_ret5_excluding_T_pct"] < 10
    assert repair["pre_event_high_level_state"] == "FAIL"
    assert repair["status"] != "PASS"


def test_trend_state_not_unknown_when_pullback_segment_is_unknown(monkeypatch):
    bundle, cutoff = trend_bundle()
    ctx = sam._prepare_context(bundle, cutoff)
    end_index = sam._session_index(ctx)
    original = sam._ma_value

    def patched(ctx_arg, iid_arg, index_arg, window_arg):
        if window_arg == 5:
            return None, "TCT_MA5_WINDOW_UNAVAILABLE"
        return original(ctx_arg, iid_arg, index_arg, window_arg)

    monkeypatch.setattr(sam, "_ma_value", patched)
    trend = sam._trend(ctx, "TEST000", end_index)

    assert trend["status"] != "UNKNOWN"
    assert trend["long_term_trend_qualified"] is True
    assert trend["pullback_segment_status"] == "UNKNOWN"


def test_long_term_trend_qualified_despite_short_ma_pressure():
    bundle, cutoff = trend_bundle(pullback_days=2)
    row = _row(run_stage_aware_momentum(bundle, cutoff=cutoff))

    assert row["trend_strength"]["long_term_trend_qualified"] is True
    assert row["trend_strength"]["short_term_trend_health"] in {"HEALTHY", "PRESSURED"}


def test_upper_shadow_ever_reclaimed_and_reclaimed_then_failed_are_distinct():
    bundle, cutoff = trend_bundle()
    payload = _payload(bundle)
    closes = ("9.20", "10.50", "10.20", "10.00", "9.80")
    days = payload["sessions"][-5:]
    prev = Decimal("10.00")
    for day, close in zip(days, closes):
        row = next(row for row in payload["bars"] if row["instrument_id"] == "TEST000" and row["session"] == day)
        _set_bar(row, open_="9.20" if day == days[0] else prev, high="10.00" if day == days[0] else Decimal(close) * Decimal("1.01"), low="9.00" if day == days[0] else Decimal(close) * Decimal("0.99"), close=close, reference_prev_close=prev)
        prev = Decimal(close)
    reclaimed = _row(run_stage_aware_momentum(type(bundle).model_validate(payload), cutoff=cutoff))
    event = reclaimed["supply_pressure"]["upper_shadow_rows"][0]

    assert event["long_upper_shadow"] is True
    assert event["ever_reclaimed"] is True
    assert event["reclaimed_then_failed"] is True
    assert event["unresolved_supply"] is False


def test_never_reclaimed_upper_shadow_remains_unresolved_supply():
    bundle, cutoff = trend_bundle()
    payload = _payload(bundle)
    closes = ("9.20", "9.50", "9.40", "9.30", "9.20")
    days = payload["sessions"][-5:]
    prev = Decimal("10.00")
    for day, close in zip(days, closes):
        row = next(row for row in payload["bars"] if row["instrument_id"] == "TEST000" and row["session"] == day)
        _set_bar(row, open_="9.20" if day == days[0] else prev, high="10.00" if day == days[0] else Decimal(close) * Decimal("1.01"), low="9.00" if day == days[0] else Decimal(close) * Decimal("0.99"), close=close, reference_prev_close=prev)
        prev = Decimal(close)
    never = _row(run_stage_aware_momentum(type(bundle).model_validate(payload), cutoff=cutoff))
    event = never["supply_pressure"]["upper_shadow_rows"][0]

    assert event["ever_reclaimed"] is False
    assert event["reclaimed_then_failed"] is False
    assert event["unresolved_supply"] is True


def test_watch_rows_have_setup_scoped_rank():
    bundle, cutoff = _with_high_level_repair_target()
    row = _row(run_stage_aware_momentum(bundle, cutoff=cutoff))

    assert row["status"] == "WATCH"
    assert row["primary_stage"] == "HIGH_LEVEL_REPAIR"
    assert row["rank_scope"] == "WATCH:HIGH_LEVEL_REPAIR"
    assert row["watch_rank"] == 1


def test_market_repair_requires_transition_evidence(monkeypatch):
    bundle, cutoff = trend_bundle()
    ctx = sam._prepare_context(bundle, cutoff)

    def fake_summary(ctx_arg, end_index_arg, *, include_instruments=False):
        base = {
            "instrument_limits": {},
            "close_limit_count": 1,
            "touched_not_closed_count": 0,
            "close_limit_down_count": 0,
            "verified_max_height": 1,
            "known_limit_event_count": 8,
            "unknown_limit_event_count": 0,
            "expected_limit_event_count": 8,
            "limit_event_coverage": sam.Fraction(1),
        }
        if end_index_arg == sam._session_index(ctx) - 1:
            base["close_limit_count"] = 1
        return base

    monkeypatch.setattr(sam, "_market_day_summary", fake_summary)
    market = sam._market_position_context(ctx, sam._session_index(ctx))

    assert market["current_raw_state"] == "RETREAT"
    assert market["ecosystem_state"] == "RETREAT"


def _bundle_and_run_for_outcomes(*, repaired=True):
    bundle, _ = trend_bundle(sessions=285)
    payload = _payload(bundle)
    days = payload["sessions"]
    signal_day = days[-4]
    signal = next(row for row in payload["bars"] if row["instrument_id"] == "TEST000" and row["session"] == signal_day)
    _set_bar(signal, open_="10.00", high="10.50", low="9.40", close="9.80", reference_prev_close="10.00")
    t1 = next(row for row in payload["bars"] if row["instrument_id"] == "TEST000" and row["session"] == days[-3])
    if repaired:
        _set_bar(t1, open_="9.90", high="10.90", low="9.80", close="10.80", reference_prev_close="9.80")
    else:
        _set_bar(t1, open_="9.70", high="10.10", low="9.60", close="9.70", reference_prev_close="9.80")
    t2 = next(row for row in payload["bars"] if row["instrument_id"] == "TEST000" and row["session"] == days[-2])
    _set_bar(t2, open_="10.00", high="10.20", low="9.70", close="10.00", reference_prev_close=t1["close"])
    t3 = next(row for row in payload["bars"] if row["instrument_id"] == "TEST000" and row["session"] == days[-1])
    _set_bar(t3, open_="10.10", high="11.10", low="10.00", close="10.90", reference_prev_close=t2["close"])
    future_bundle = type(bundle).model_validate(payload)
    item = {
        "instrument_id": "TEST000",
        "rank": 1,
        "primary_stage": "HIGH_LEVEL_REPAIR",
        "comparable_close": str(signal["comparable_close"]),
        "comparable_basis": signal["comparable_basis"],
        "stage_candidates": {"START": {"breakout_prior_high": 10.0}},
        "supply_pressure": {"upper_shadow_rows": [{"long_upper_shadow": True, "shadow_high": 10.5}]},
    }
    run = {
        "run_id": "strategy_experiment_fixture_v2",
        "observation_date": str(signal_day),
        "spec_hash": "fixture",
        "config_hash": "fixture",
        "implementation_version": "fixture",
        "source_hash": "fixture",
        "results": {STAGE_AWARE_ID: {"strategy_id": STAGE_AWARE_ID, "canonical_strategy_id": STAGE_AWARE_ID, "items": [item]}},
        "selection_results": {},
    }
    return future_bundle, run, days


def test_t1_reversal_is_future_outcome_only_and_does_not_mutate_signal():
    bundle, run, days = _bundle_and_run_for_outcomes(repaired=True)
    before = digest(run)
    labels = evaluate_strategy_experiment_outcomes(run, bundle, datetime.combine(days[-1], time(20, 30), TZ), horizons=(1,))

    assert digest(run) == before
    outcome = labels[0]["next_session_reversal"]
    assert outcome["result"] is True
    assert outcome["maturity_status"] == "MATURE_VALID"
    assert labels[0]["repair_latency"]["repair_definition"] == "CLOSE_RECLAIM_SIGNAL_DAY_COMPARABLE_CLOSE"
    assert labels[0]["turn_positive_latency"]["result"] == "HIT"
    assert labels[0]["close_reclaim_latency"]["result"] == "HIT"
    assert labels[0]["drop_day_high_reclaim_latency"]["result"] == "HIT"
    assert "next_session_reversal" not in run["results"][STAGE_AWARE_ID]["items"][0]


def test_t3_new_high_is_future_outcome_and_cannot_alter_t_signal():
    bundle, run, days = _bundle_and_run_for_outcomes(repaired=True)
    before = deepcopy(run)
    labels = evaluate_strategy_experiment_outcomes(run, bundle, datetime.combine(days[-1], time(20, 30), TZ), horizons=(3,))

    assert run == before
    new_high = labels[0]["time_to_new_high"]
    assert new_high["drop_day_high_sessions_to_reclaim"] in {1, 3}
    assert new_high["maturity_status"] == "MATURE_VALID"


def test_repair_latency_reports_not_matured_before_window_completes():
    bundle, run, days = _bundle_and_run_for_outcomes(repaired=False)
    labels = evaluate_strategy_experiment_outcomes(run, bundle, datetime.combine(days[-3], time(20, 30), TZ), horizons=(1,))

    latency = labels[0]["repair_latency"]
    assert latency["sessions_to_repair"] is None
    assert latency["result"] == "NOT_MATURED"
    assert latency["maturity_status"] == "NOT_MATURED"
