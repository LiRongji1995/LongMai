from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pytest

from stockfish_core.tracker import RecommendationTracker, RecommendationRecord, EvaluationBar
from stockfish_core.storage import SQLiteStore, ImmutableConflict, StorageError


def fixture_record(**overrides):
    roles = {"data": "data_snapshots", "model": "model_artifacts", "policy": "recommendation_policies",
             "risk": "risk_policies", "quant": "quant_runs", "input": "recommendation_inputs"}
    refs = [{"role": role, "namespace": namespace, "document_id": role + "_v1"} for role, namespace in roles.items()]
    value = dict(recommendation_id="rec_original", run_id="run_one", instrument_id="600000.SH",
                 recommendation_time="2024-01-01T08:00:00Z", as_of="2024-01-01T07:00:00Z",
                 earliest_trade_at="2024-01-02T01:30:00Z", model_id="fixture", model_version="1",
                 data_snapshot_id="data_v1", universe_version="u1", policy_version="p1", quant_score=.2,
                 quant_rank=1, final_score=.2, final_rank=1, risk_decision={"decision": "ACCEPT"},
                 reason_codes=["FIXTURE_RESEARCH"], reason="offline engineering fixture", evidence_ids=[],
                 target_horizon=7, benchmark="000300.SH", metadata={"artifact_refs": refs})
    value.update(overrides)
    documents = [(ref["namespace"], ref["document_id"], {"model_version": "1", "fixture": True}) for ref in refs]
    return value, documents


@pytest.fixture
def tracker(tmp_path):
    tracker = RecommendationTracker(SQLiteStore(tmp_path / "tracker.sqlite3"))
    record, documents = fixture_record()
    tracker.publish_many([record], documents=documents)
    return tracker


def price_fixture():
    # Explicit fictional exchange calendar, never fetched or sold as historical market data.
    sessions = [date(2024, 1, 2) + timedelta(days=n) for n in range(145)
                if (date(2024, 1, 2) + timedelta(days=n)).weekday() < 5]
    bars, benchmark = [], []
    for index, session in enumerate(sessions):
        common = dict(session=session, open_at=f"{session}T01:30:00Z", close_at=f"{session}T07:00:00Z",
                      available_at=f"{session}T07:01:00Z", price_adjustment="raw", provider="offline_test_fixture",
                      data_snapshot_id="outcome_fixture", can_buy=True, can_sell=True, corporate_actions_known=True)
        bars.append(dict(**common, instrument_id="600000.SH", open=100 + index, close=100 + index))
        benchmark.append(dict(**common, instrument_id="000300.SH", open=100, close=100))
    return sessions, bars, benchmark


def test_history_revisions_are_immutable_and_durable(tracker):
    original = tracker.get("rec_original")
    revision = tracker.revise("rec_original", {"reason": "new evidence", "final_score": .3})
    assert tracker.get("rec_original") == original
    assert tracker.get(revision)["supersedes"] == "rec_original"
    assert tracker.get(revision)["revision"] == 2
    assert len(tracker.list_history()) == 2
    assert tracker.list_history(latest_only=True)[0]["recommendation_id"] == revision
    with pytest.raises(ImmutableConflict):
        tracker.revise("rec_original", {"reason": "stale revision"})
    with pytest.raises(StorageError):
        with tracker.store.transaction() as connection:
            connection.execute("DELETE FROM recommendation_records")
    assert RecommendationTracker(SQLiteStore(tracker.store.path)).get("rec_original") == original


def test_concurrent_revisions_only_one_child_can_win(tracker):
    def revise(number):
        try:
            return tracker.revise("rec_original", {"reason": str(number)})
        except ImmutableConflict:
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(revise, range(12)))
    assert sum(result is not None for result in results) == 1
    assert len(tracker.list_history()) == 2


def test_publication_atomic_lineage_and_research_gate(tmp_path):
    tracker = RecommendationTracker(SQLiteStore(tmp_path / "state.sqlite3"))
    record, docs = fixture_record()
    bad = dict(record, recommendation_id="rec_bad", metadata={"artifact_refs": []})
    with pytest.raises(ValueError):
        tracker.publish_many([record, bad], documents=docs)
    assert tracker.list_history() == []
    assert tracker.store.get_document("data_snapshots", "data_v1") is None
    with pytest.raises(ValueError):
        RecommendationRecord.model_validate(dict(record, status="PUBLISHED"))
    production = dict(record, status="PUBLISHED", mode="production",
                      metadata={**record["metadata"], "production_approved": True})
    tracker.publish_many([production], documents=docs)
    with pytest.raises(ValueError):
        tracker.publish(dict(record, recommendation_id="rec_wrong", data_snapshot_id="mismatched"))
    other = SQLiteStore(tmp_path / "other.sqlite3")
    with pytest.raises(ValueError, match="another store"):
        with other.transaction() as connection:
            tracker.publish_many([dict(record, recommendation_id="cross_database")], documents=docs, connection=connection)


@pytest.mark.parametrize("horizon", [7, 30, 90])
def test_7_30_90_maturity_fees_benchmark_and_separate_storage(tracker, horizon):
    sessions, bars, benchmark = price_fixture()
    original = tracker.get("rec_original")
    before = datetime(2024, 1, 2, 8, tzinfo=timezone.utc) + timedelta(days=horizon - 1)
    not_due = tracker.evaluate("rec_original", bars, benchmark, horizon=horizon,
                               evaluated_at=before, trading_sessions=sessions)
    assert not_due["state"] == "NOT_MATURED" and not_due["return"] is None
    now = datetime(2024, 5, 1, 8, tzinfo=timezone.utc)
    outcome = tracker.evaluate("rec_original", bars, benchmark, horizon=horizon,
                               evaluated_at=now, trading_sessions=sessions)
    due = date(2024, 1, 2) + timedelta(days=horizon)
    exit_day = next(day for day in sessions if day >= due)
    exit_price = 100 + sessions.index(exit_day)
    expected = (exit_price * 100 - max(5, exit_price * 100 * .0003) - exit_price * 100 * .0005) / 10005 - 1
    assert outcome["state"] == "MATURED"
    assert outcome["return"] == pytest.approx(expected)
    assert outcome["benchmark_return"] == 0
    assert outcome["excess_return"] == pytest.approx(expected)
    assert datetime.fromisoformat(outcome["exit_at"]).date() == exit_day
    assert tracker.get("rec_original") == original
    assert len(tracker.evaluations("rec_original", horizon=horizon)) == 2
    assert tracker.evaluate("rec_original", bars, benchmark, horizon=horizon,
                            evaluated_at=now, trading_sessions=sessions) == outcome
    assert len(tracker.evaluations("rec_original", horizon=horizon)) == 2


def test_corporate_actions_raw_prices_and_complete_earliest_entry(tracker):
    sessions, bars, benchmark = price_fixture()
    now = datetime(2024, 5, 1, 8, tzinfo=timezone.utc)
    kwargs = dict(evaluated_at=now, trading_sessions=sessions)
    partial = tracker.evaluate("rec_original", bars[1:], benchmark, **kwargs)
    assert partial["reason_codes"] == ["INCOMPLETE_ENTRY_PATH"]
    bars[2]["corporate_actions_known"] = False
    unknown = tracker.evaluate("rec_original", bars, benchmark, **kwargs)
    assert unknown["state"] == "DATA_UNAVAILABLE" and unknown["return"] is None
    bars[2]["corporate_actions_known"] = True
    bars[2]["price_adjustment"] = "qfq"
    with pytest.raises(ValueError):
        tracker.evaluate("rec_original", bars, benchmark, **kwargs)
    bars[2]["price_adjustment"] = "raw"
    # A 2-for-1 split plus CNY 1 per new share: no false 50% loss.
    for row in bars[1:]:
        row["open"] = row["close"] = 50
    bars[1]["split_ratio"] = 2
    bars[1]["cash_dividend_per_share"] = 1
    result = tracker.evaluate("rec_original", bars, benchmark, costs={"minimum_commission": 0,
                              "buy_commission_rate": 0, "sell_commission_rate": 0, "sell_tax_rate": 0}, **kwargs)
    assert result["accounting"]["final_shares"] == 200
    assert result["accounting"]["dividends"] == 200
    assert result["return"] == pytest.approx(.02)


def test_trading_days_horizon_and_exit_suspension(tracker):
    revised = tracker.revise("rec_original", {"horizon_unit": "trading_days"})
    sessions, bars, benchmark = price_fixture()
    kwargs = dict(evaluated_at=datetime(2024, 5, 1, 8, tzinfo=timezone.utc), trading_sessions=sessions)
    result = tracker.evaluate(revised, bars, benchmark, horizon=7, **kwargs)
    assert datetime.fromisoformat(result["exit_at"]).date() == sessions[7]
    bars[7]["can_sell"] = False
    result = tracker.evaluate(revised, bars, benchmark, horizon=7, **kwargs)
    assert result["state"] == "NOT_EXECUTABLE" and result["return"] is None
    with pytest.raises(ValueError):
        tracker.append_evaluation({**result, "horizon_unit": "calendar_days"})


def test_derived_metrics_cannot_be_fabricated_and_raw_inputs_are_retrievable(tracker):
    sessions, bars, benchmark = price_fixture()
    result = tracker.evaluate("rec_original", bars, benchmark, evaluated_at=datetime(2024, 5, 1, 8, tzinfo=timezone.utc),
                              trading_sessions=sessions)
    source = tracker.store.get_document("evaluation_inputs", result["input_snapshot_id"])
    assert len(source["bars"]) == len(bars)
    assert source["bars"][0]["price_adjustment"] == "raw"
    assert result["provenance"] == "UNVERIFIED_RESEARCH_INPUT"
    with pytest.raises(ValueError, match="recomputation"):
        tracker.append_evaluation({**result, "return": 10, "excess_return": 10, "observation_id": "fabricated"})
    with pytest.raises(ValueError):
        tracker.append_evaluation({**result, "input_snapshot_id": "missing"})
    assert len(tracker.evaluations("rec_original")) == 1


def test_observed_source_registration_and_production_evaluation_gate(tracker):
    sessions, bars, benchmark = price_fixture()
    now = datetime(2024, 6, 1, 8, tzinfo=timezone.utc)
    value = dict(source="offline_evaluation_fixture", kind="TEST_FIXTURE", available_at=now,
                 bars=bars, benchmark_bars=benchmark, trading_sessions=sessions, calendar_source="test_calendar",
                 calendar_coverage_start=sessions[0], calendar_coverage_end=sessions[-1], data_license="NOT_FOR_PRODUCTION")
    snapshot_id = tracker.register_outcome_snapshot(value)
    result = tracker.evaluate_registered("rec_original", snapshot_id, evaluated_at=now)
    assert result["state"] == "MATURED" and result["provenance"] == "TEST_FIXTURE"
    production = tracker.revise("rec_original", {"status": "PUBLISHED", "mode": "production",
                         "metadata": {**tracker.get("rec_original")["metadata"], "production_approved": True}})
    with pytest.raises(ValueError, match="approved observed"):
        tracker.evaluate_registered(production, snapshot_id, evaluated_at=now)
    with pytest.raises(ValueError, match="approved observed"):
        tracker.evaluate(production, bars, benchmark, evaluated_at=now, trading_sessions=sessions)
