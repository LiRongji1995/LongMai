"""Explicit offline fixtures for independent final engineering gates.

Every price, calendar, feature, model label and LLM response in this module is
synthetic. These artifacts are never evidence of actual investment performance.
"""
from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import time as clock_time
from zoneinfo import ZoneInfo

from stockfish_core.api import create_app
from stockfish_core.backtest import BacktestEngine, BacktestRequest, FeeSchedule, PolicyReplayAdapter, PolicyReplayInput
from stockfish_core.common import digest
from stockfish_core.data import CanonicalMarketBar, SecurityMaster, SecurityState, TradingCalendar, TradabilityService
from stockfish_core.quant import FeatureRow
from stockfish_core.recommendation import Candidate, RecommendationContext
from stockfish_core.research import LLMConfiguration, ResearchEngine, paired_evaluation
from stockfish_core.risk import PortfolioSnapshot, RiskInput
from stockfish_core.service import ApplicationService, InputSnapshot, RecommendationRequest
from stockfish_core.storage import SQLiteStore
from tests.integration.core_fixture import fixture_service, IIDS

SH = ZoneInfo("Asia/Shanghai")
CLASSIFICATION = "EXPLICIT_TEST_FIXTURE_NOT_ACTUAL_INVESTMENT_PERFORMANCE"
ARMS = ("quant_only", "quant_research", "quant_research_llm")


def instant(day, hour=15, minute=0):
    return datetime.combine(day, time(hour, minute), SH)


def await_job(client, job_id):
    deadline = clock_time.monotonic() + 8
    while clock_time.monotonic() < deadline:
        response = client.get("/api/jobs/" + job_id)
        assert response.status_code == 200
        if response.json["state"] in {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}:
            return response.json
        clock_time.sleep(.01)
    raise AssertionError("fixture job did not finish")


def fixture_client(core):
    client = create_app(core).test_client()
    # The isolated test client may inherit a server-managed API key. Authenticate
    # locally without changing user configuration or writing the key to artifacts.
    token = os.environ.get("STOCKFISH_API_KEY")
    if token:
        client.environ_base["HTTP_AUTHORIZATION"] = "Bearer " + token
    return client


def tracker_api_gate(root: Path) -> dict:
    """API -> actual quant/policy/job -> registered outcomes -> reopened API."""
    core, source_id, model = fixture_service(root)
    try:
        original = core.snapshot(source_id)
        day = date(2026, 4, 1)
        cutoff = instant(day)
        sessions = tuple(day + timedelta(days=n) for n in range(153)
                         if (day + timedelta(days=n)).weekday() < 5)
        snapshot = InputSnapshot(as_of=cutoff, available_at=cutoff, source=CLASSIFICATION, kind="TEST_FIXTURE",
            bars=tuple(bar.model_copy(update={"timestamp": cutoff, "source_timestamp": cutoff,
                                               "available_at": cutoff, "snapshot_id": "april_fixture"}) for bar in original.bars),
            securities=tuple(state.model_copy(update={"effective_from": day, "effective_to": day + timedelta(days=1),
                       "available_at": cutoff, "limits_date": day}) for state in original.securities),
            sessions=sessions, calendar_start=day, calendar_end=date(2026, 8, 31),
            features=tuple(feature.model_copy(update={"available_at": cutoff}) for feature in original.features),
            data_license="NOT_FOR_PRODUCTION", evidence={"classification": CLASSIFICATION})
        snapshot_id = core.register_snapshot(snapshot)
        client = fixture_client(core)
        submitted = client.post("/api/recommendations", json={"snapshot_id": snapshot_id,
            "model_id": model.model_id, "model_version": model.model_version, "instruments": list(IIDS),
            "mode": "REPLAY", "decision_time": instant(day, 16).isoformat(), "cash": 100000,
            "include_research": False, "include_llm": False})
        assert submitted.status_code == 202, submitted.json
        job = await_job(client, submitted.json["job_id"])
        assert job["state"] == "SUCCEEDED", job
        recommendation_id = job["best_stock"]["recommendation_id"]
        record_before = core.tracker.get(recommendation_id)
        assert record_before["status"] == "RESEARCH_ONLY"
        benchmark_id = record_before["benchmark"]
        prices, benchmark = [], []
        for index, session in enumerate(sessions):
            common = dict(session=session, open_at=instant(session, 9, 30), close_at=instant(session),
                available_at=instant(session, 15, 1), price_adjustment="raw", provider="offline_fixture",
                data_snapshot_id="fixture-outcomes", can_buy=True, can_sell=True, corporate_actions_known=True)
            prices.append(dict(common, instrument_id=record_before["instrument_id"], open=10 + index * .02, close=10 + index * .02))
            benchmark.append(dict(common, instrument_id=benchmark_id, open=10, close=10))
        outcome_snapshot_id = core.tracker.register_outcome_snapshot({"source": CLASSIFICATION, "kind": "TEST_FIXTURE",
            "available_at": instant(sessions[-1], 15, 1), "bars": prices, "benchmark_bars": benchmark,
            "trading_sessions": sessions, "calendar_source": "FICTIONAL_WEEKDAY_CALENDAR_NOT_EXCHANGE_HISTORY",
            "calendar_coverage_start": day, "calendar_coverage_end": date(2026, 8, 31), "data_license": "NOT_FOR_PRODUCTION"})
        outcomes = [core.tracker.evaluate_registered(recommendation_id, outcome_snapshot_id,
                        horizon=h, evaluated_at=instant(date(2026, 9, 1), 16)) for h in (7, 30, 90)]
        assert all(row["state"] == "MATURED" and row["provenance"] == "TEST_FIXTURE" for row in outcomes)
        assert core.tracker.get(recommendation_id) == record_before
        history_before = client.get("/api/recommendations/history").json
        record_api_before = client.get("/api/recommendations/" + recommendation_id).json
        job_before = client.get("/api/jobs/" + job["job_id"]).json
        # No endpoint accepts caller-invented realized-return observations.
        forged = client.post("/api/recommendations/" + recommendation_id + "/evaluations", json={"return": 999})
        assert forged.status_code in (404, 405)
        store_path, core_path, data_dir = core.store.path, core.root, core.data_dir
    finally:
        core.close()
    reopened = ApplicationService(core_path, store=SQLiteStore(store_path), data_dir=data_dir)
    try:
        client = fixture_client(reopened)
        history_after = client.get("/api/recommendations/history").json
        record_api_after = client.get("/api/recommendations/" + recommendation_id).json
        job_after = client.get("/api/jobs/" + job["job_id"]).json
        assert history_before == history_after
        assert record_api_before == record_api_after
        assert job_before == job_after
        assert record_api_after["record"] == record_before
        assert sorted(row["horizon"] for row in record_api_after["evaluations"]) == [7, 30, 90]
        return {"classification": CLASSIFICATION, "snapshot_id": snapshot_id, "outcome_snapshot_id": outcome_snapshot_id,
                "recommendation_id": recommendation_id, "history_before": history_before, "history_after": history_after,
                "record_and_observations_before": record_api_before, "record_and_observations_after": record_api_after,
                "job_before": job_before, "job_after": job_after, "source_write_api_rejected": forged.status_code,
                "database": str(store_path), "storage_health": reopened.store.health(),
                "assertions": {"same_history_after_restart": True, "same_record_after_restart": True,
                    "same_observations_after_restart": True, "same_job_after_restart": True,
                    "registered_fixture_source": True, "actual_market_performance_claimed": False}}
    finally:
        reopened.close()


def paired_cash_nav_gate(root: Path) -> dict:
    """Real BacktestEngine, real QuantEngine and identical live policy, all fixtures."""
    root.mkdir(parents=True, exist_ok=True)
    core, _, model = fixture_service(root / "application")
    mock_calls = []
    def mock_transport(prompt, configuration):
        snapshot = json.loads(prompt.rsplit("\n", 1)[1])
        evidence = tuple(row["evidence_id"] for row in snapshot["evidence"])
        mock_calls.append({"instrument_id": snapshot["instrument_id"], "as_of": snapshot["as_of"],
                           "evidence_ids": evidence, "provider": configuration.provider})
        return {"output": json.dumps({"summary": "TEST FIXTURE: strongly negative opinion; diagnostic has no final vote.",
            "outlook": "NEGATIVE", "evidence_ids": evidence, "limitations": [CLASSIFICATION, "NO_FINAL_VOTE"],
            "research_score": -1}), "usage": {"mock_calls": 1}}
    core.research = ResearchEngine(core.store, LLMConfiguration(provider="offline_mock", model="adversarial_fixture",
                                   model_snapshot="fixed-fixture-v1", temperature=0, seed=7), transport=mock_transport)
    cases = {"rising_prices": (10., 10.5, 11., 12.), "falling_prices": (10., 10., 9.5, 9.),
             "flat_prices_fee_loss": (10., 10., 10., 10.)}
    days = tuple(date(2026, 4, 6) + timedelta(days=n) for n in range(5))
    benchmark_id = "000300.SH"
    calendar = TradingCalendar(days)
    states = tuple(SecurityState(instrument_id=iid, effective_from=day, effective_to=day + timedelta(days=1),
        available_at=instant(day, 9), provider="explicit_offline_fixture", verified=True,
        listing_date=date(2000, 1, 1), board="MAIN", is_st=False, suspended=False, lot_size=100,
        min_buy_shares=100, industry=iid, limits_date=day, limit_up=100, limit_down=1, no_price_limit=False)
        for day in days for iid in (*IIDS, benchmark_id))
    master = SecurityMaster(states)
    tradability = TradabilityService(calendar, master)
    simulator = BacktestEngine(calendar, tradability, root / "backtests")
    paired_inputs, full_results, decisions, research_counts = {}, {}, {}, {}
    try:
        procedure = {"classification": CLASSIFICATION, "cases": cases, "arms": ARMS,
            "universe_schedule": {str(day): list(IIDS) if index < 2 else [] for index, day in enumerate(days[:4])},
            "policy": core.recommendations.policy.model_dump(mode="json"),
            "risk_policy": core.recommendations.risk.policy.model_dump(mode="json"),
            "model_id": model.model_id, "model_version": model.model_version,
            "feature_rule": "fixed ex-ante fixture values 3,2,1; available at each visible session close",
            "selection": "NO_CASE_OR_ARM_SELECTION_ALL_PERSISTED", "llm_output": "fixed negative research_score=-1",
            "initial_cash": 100000, "calendar": [str(day) for day in days]}
        (root / "preregistered_procedure.json").write_text(json.dumps(procedure, indent=2), encoding="utf-8")
        for case_name, trajectory in cases.items():
            bars = tuple(CanonicalMarketBar(instrument_id=iid, exchange=iid[-2:], symbol=iid[:6], timestamp=instant(day),
                open=10 if iid == benchmark_id or index < 2 else trajectory[index],
                close=10 if iid == benchmark_id else trajectory[index],
                high=max(10 if iid == benchmark_id or index < 2 else trajectory[index], 10 if iid == benchmark_id else trajectory[index]),
                low=min(10 if iid == benchmark_id or index < 2 else trajectory[index], 10 if iid == benchmark_id else trajectory[index]),
                volume=1000000, turnover=10000000, currency="CNY", price_adjustment="RAW",
                provider="explicit_offline_fixture", source_timestamp=instant(day), available_at=instant(day), quality="VERIFIED",
                snapshot_id=f"{case_name}-{day}") for index, day in enumerate(days[:4]) for iid in (*IIDS, benchmark_id))
            request = BacktestRequest(start_date=days[0], end_date=days[3],
                initial_portfolio=PortfolioSnapshot(as_of=instant(days[0], 8), cash=100000),
                policy_version=core.recommendations.policy.version, data_version=digest([bar.model_dump(mode="json") for bar in bars]),
                universe_version=master.version, benchmark_id=benchmark_id, corporate_actions_complete=True,
                fees=FeeSchedule())
            pair_hash = digest({"market_request": request, "procedure": procedure, "case": case_name})
            paired_inputs[case_name], full_results[case_name], decisions[case_name] = {}, {}, {}
            for arm in ARMS:
                run_name = case_name + "-" + arm
                research_before, mock_before = len(core.store.list_documents("research_opinions", limit=10000)), len(mock_calls)
                def build(at, visible, portfolio):
                    current = tuple(bar for bar in visible if bar.timestamp.astimezone(SH).date() == at.date())
                    assert current and all(bar.available_at <= at for bar in current)
                    snapshot = InputSnapshot(as_of=max(bar.timestamp for bar in current), available_at=at,
                        source=CLASSIFICATION, kind="TEST_FIXTURE", bars=current,
                        securities=tuple(state for state in states if state.available_at <= at),
                        sessions=days, calendar_start=days[0], calendar_end=days[-1],
                        features=tuple(FeatureRow(instrument_id=iid, available_at=max(bar.timestamp for bar in current),
                                                values=(float(3-index),)) for index, iid in enumerate(IIDS)),
                        data_license="NOT_FOR_PRODUCTION", evidence={"classification": CLASSIFICATION})
                    snapshot_id = core.register_snapshot(snapshot)
                    active_universe = IIDS if at.date() in days[:2] else ()
                    result = core.recommend(RecommendationRequest(snapshot_id=snapshot_id, model_id=model.model_id,
                        model_version=model.model_version, instruments=active_universe, mode="REPLAY", decision_time=at,
                        cash=portfolio.cash, holdings=portfolio.holdings, include_research=arm != "quant_only",
                        include_llm=arm == "quant_research_llm"))
                    canonical = core.store.get_document("recommendation_inputs", result["final_recommendation"]["input_hash"])
                    return PolicyReplayInput(context=RecommendationContext.model_validate(canonical["context"]),
                        candidates=tuple(Candidate.model_validate(c) for c in canonical["candidates"]),
                        risk_inputs={key: RiskInput.model_validate(value) for key, value in canonical["risk_inputs"].items()})
                adapter = PolicyReplayAdapter(core.recommendations, build)
                result = simulator.run(request.model_copy(update={"run_id": run_name}), bars, adapter)
                full_results[case_name][arm] = result.model_dump(mode="json")
                decisions[case_name][arm] = [{"status": d.status, "target_weights": d.target_weights,
                    "selected": [{"instrument_id": item.instrument_id, "quant_score": item.quant_score,
                        "quant_rank": item.quant_rank, "final_score": item.final_score, "final_rank": item.final_rank,
                        "target_shares": item.target_shares, "risk": item.risk_decision.model_dump(mode="json")} for item in d.items]}
                    for d in adapter.decisions]
                paired_inputs[case_name][arm] = {"input_hash": pair_hash, "policy_version": result.policy_version,
                    "policy_hash": result.policy_hash, "risk_policy_hash": result.risk_policy_hash,
                    "execution_method": "CASH_NAV", "return": result.total_return, "cost": result.total_fees,
                    "max_drawdown": abs(result.max_drawdown), "drawdown_convention": "positive_magnitude_of_simulator_negative_drawdown",
                    "data_version": result.data_version, "universe_version": result.universe_version,
                    "backtest_run_id": result.run_id, "artifact_path": result.artifact_path,
                    "classification": CLASSIFICATION, "initial_nav": request.initial_portfolio.nav,
                    "final_nav": result.daily[-1].nav, "final_cash": result.final_portfolio.cash}
                research_counts[run_name] = {"opinions": len(core.store.list_documents("research_opinions", limit=10000)) - research_before,
                                             "mock_llm_calls": len(mock_calls) - mock_before}
                assert result.policy_adapter == "CANONICAL_RECOMMENDATION"
                assert result.total_fees > 0 and len(result.orders) >= 6
                assert result.final_portfolio.holdings == ()
                assert result.total_return == result.daily[-1].nav / request.initial_portfolio.nav - 1 or abs(
                    result.total_return - (result.daily[-1].nav / request.initial_portfolio.nav - 1)) < 1e-12
            base = full_results[case_name]["quant_only"]
            for arm in ARMS[1:]:
                other = full_results[case_name][arm]
                assert base["daily"] == other["daily"]
                assert [{k: v for k, v in row.items() if k != "order_id"} for row in base["orders"]] == [
                    {k: v for k, v in row.items() if k != "order_id"} for row in other["orders"]]
                assert decisions[case_name]["quant_only"] == decisions[case_name][arm]
        comparison = paired_evaluation(paired_inputs)
        assert len(comparison["observations"]) == 9
        assert all(row["paired_return_difference"] == 0 for row in comparison["observations"])
        assert paired_inputs["falling_prices"]["quant_only"]["return"] < 0
        assert paired_inputs["flat_prices_fee_loss"]["quant_only"]["return"] < 0
        assert paired_inputs["rising_prices"]["quant_only"]["return"] > 0
        assert all(counts["mock_llm_calls"] > 0 for name, counts in research_counts.items() if name.endswith("quant_research_llm"))
        assert all(counts["mock_llm_calls"] == 0 for name, counts in research_counts.items() if not name.endswith("quant_research_llm"))
        assert all(counts["opinions"] > 0 for name, counts in research_counts.items() if not name.endswith("quant_only"))
        assert all(counts["opinions"] == 0 for name, counts in research_counts.items() if name.endswith("quant_only"))
        audits = core.store.list_documents("llm_audits", limit=10000)
        assert len(audits) == len(mock_calls) == 18
        assert all(row["payload"]["status"] == "VALID" and json.loads(row["payload"]["output"])["research_score"] == -1 for row in audits)
        evidence = {"classification": CLASSIFICATION, "procedure": procedure, "paired_accounting": comparison,
            "accounting_results": full_results, "decision_comparison": decisions, "research_counts": research_counts,
            "mock_calls": mock_calls, "llm_audit_ids": [row["document_id"] for row in audits],
            "database": str(core.store.path), "assertions": {
                "all_cases_and_arms_saved": True, "includes_losses": True, "same_cash_nav_and_trades": True,
                "same_final_ranking_risk_and_target": True, "actual_quant_and_backtest_engines_used": True,
                "live_recommendation_policy_used": True, "all_18_mock_outputs_schema_valid_and_negative": True,
                "llm_final_vote": False, "actual_market_performance_claimed": False}}
        (root / "paired_cash_nav_evidence.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
        return evidence
    finally:
        core.close()
