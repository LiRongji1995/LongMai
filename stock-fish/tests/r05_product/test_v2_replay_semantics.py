from __future__ import annotations

from datetime import date

from stockfish_core.yaogu.contracts import MarketInput
from scripts import v2_semantic_delivery as delivery
from tests.r05_product.test_stage_aware_momentum_v1 import _payload
from tests.r05_product.test_theme_capital_trend_v1 import trend_bundle


def test_non_reconstructed_replay_is_blocked_not_successful(tmp_path):
    bundle, cutoff = trend_bundle()

    report = delivery.run_replay(bundle, cutoff, tmp_path, max_sessions=20)

    assert report["attempt_count"] == 20
    assert report["source_constructed_count"] == 0
    assert report["evaluable_signal_day_count"] == 0
    assert report["unevaluable_signal_day_count"] == 20
    assert report["failed_count"] == 0
    assert report["success_count"] == 0
    assert str(bundle.observation_date) in report["objective_blocker"]
    assert bundle.coverage_type in report["objective_blocker"]
    assert report["attempts"][0]["strategy_status"] == "FAILED"
    assert (tmp_path / "v2_historical_replay_status_taxonomy.json").is_file()


def test_reconstructed_replay_counts_only_days_with_dated_signal_identity_as_evaluable(tmp_path, monkeypatch):
    bundle, cutoff = trend_bundle()
    payload = _payload(bundle)
    payload["coverage_type"] = "RECONSTRUCTED_HISTORICAL"
    first_evaluable = payload["sessions"][-2]
    for security in payload["universe"]["securities"]:
        security["effective_from"] = first_evaluable

    replay_input = MarketInput.model_validate(payload)

    def fake_stage_aware(replay_bundle, *, cutoff):
        eligible = len(replay_bundle.universe.securities)
        return {
            "status": "COMPLETE" if eligible else "EMPTY",
            "counts": {
                "eligible": eligible,
                "focus": 0,
                "watch": 0,
                "rejected": 0,
                "unknown": eligible,
                "condition_counts": {
                    "sector_context": {"PASS": 0},
                    "capital_linkage": {"PASS": 0},
                },
            },
            "items": [],
        }

    monkeypatch.setattr(delivery, "run_stage_aware_momentum", fake_stage_aware)

    report = delivery.run_replay(replay_input, cutoff, tmp_path, max_sessions=20)

    assert report["attempt_count"] == 20
    assert report["source_constructed_count"] == 20
    assert report["evaluable_signal_day_count"] == 2
    assert report["unevaluable_signal_day_count"] == 18
    assert report["failed_count"] == 0
    assert report["success_count"] == 2
    assert report["scope_summary"]["PRICE_TREND_REPLAY_SCOPE"]["evaluable_signal_day_count"] == 2
    assert report["scope_summary"]["PRICE_TREND_REPLAY_SCOPE"]["unevaluable_signal_day_count"] == 18
    assert report["scope_summary"]["FULL_CONTEXT_REPLAY_SCOPE"]["evaluable_signal_day_count"] == 0
    assert report["attempts"][0]["strategy_status"] == "INSUFFICIENT_IDENTITY"
    assert report["attempts"][0]["status"] == "INSUFFICIENT_IDENTITY"
    assert report["attempts"][-1]["strategy_status"] == "EVALUATED"
    assert {date.fromisoformat(row["observation_date"]) for row in report["attempts"] if row["strategy_status"] == "EVALUATED"} == set(payload["sessions"][-2:])
