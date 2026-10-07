"""Research readiness uses real fixture paths, never the presence of two tables."""
from datetime import date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path

import pytest

from stockfish_live import cli
from stockfish_live.calendar import TradingCalendar
from stockfish_live.common import canonical_json
from stockfish_live.contracts.forecast import ForecastConfig, HistoricalState
from stockfish_live.contracts.market import DatasetScope, Instrument, MinuteBar, SourceReceipt
from stockfish_live.features.market import INDEX_IDS
from stockfish_live.features.streaming import StreamingMarketStateEngine
from stockfish_live.forecast.engine import mature_outcomes
from stockfish_live.storage.parquet_store import ParquetStore
from stockfish_live.storage.research_store import ResearchStore


def dataset(root, *, sessions=2, mismatch=None, missing=None, bad_parent=False):
    root.mkdir()
    day = date(2026, 9, 30)
    days = tuple(day - timedelta(days=offset) for offset in range(sessions, -1, -1))
    available = datetime.combine(days[0], time(9), cli.SHANGHAI)
    calendar = TradingCalendar(schema_version="LIVE_CALENDAR_V1", sessions=days,
        coverage_start=days[0], coverage_end=day, exchanges=("SH", "SZ"),
        source="TEST_FIXTURE_RESEARCH_READINESS_NOT_OFFICIAL", available_at=available, pit_status="TEST_FIXTURE")
    scope = DatasetScope(calendar_version=calendar.version, available_at=available, pit_status="TEST_FIXTURE",
        instruments=tuple(Instrument(instrument_id=identity, asset_type="INDEX") for identity in INDEX_IDS))
    config = ForecastConfig(feature_version="MARKET_FEATURES_V2", min_samples=2, k=2, k_candidates=(2,))
    feature_hash = StreamingMarketStateEngine(calendar, scope).config_hash
    (root / "calendar.json").write_text(canonical_json(calendar))
    (root / "dataset.json").write_text(canonical_json(scope))
    (root / "research-config.json").write_text(canonical_json({"schema_version": "LIVE_RESEARCH_CONFIG_V1", "forecast": config}))
    research = ResearchStore(root)
    for n, historical_day in enumerate(days[:-1]):
        batches, bars, receipts, states = [], [], [], []
        for minute in calendar.minutes(historical_day):
            raw = f"TEST_FIXTURE_READINESS_RAW_{minute.isoformat()}".encode()
            source = hashlib.sha256(raw).hexdigest()
            parent = SourceReceipt(source_id=source, provider="mock", source_timestamp=minute,
                source_timestamp_basis="PUBLISHED", received_at=minute, available_at=minute, pit_status="TEST_FIXTURE")
            group = tuple(MinuteBar(instrument_id=identity, symbol=identity[:6], exchange=identity[-2:],
                asset_type="INDEX", timestamp=minute, open=100., high=100., low=100., close=100., volume=1.,
                turnover=100., provider="mock", source_id=source, source_timestamp=minute,
                source_timestamp_basis="PUBLISHED", available_at=minute, quality="VALID", pit_status="TEST_FIXTURE")
                for identity in INDEX_IDS)
            batches.append((group, parent, raw))
            bars.extend(group)
            receipts.append(parent)
        ParquetStore(root).append_many(batches)
        for identity in INDEX_IDS:
            for clock in cli.RESEARCH_DECISION_TIMES:
                hour, minute = map(int, clock.split(":"))
                decision = datetime.combine(historical_day, time(hour, minute), cli.SHANGHAI)
                changes = {}
                if n == sessions - 1 and mismatch == "version":
                    changes["feature_version"] = "MARKET_FEATURES_V1"
                states.append(HistoricalState(state_id=f"TEST_FIXTURE_{identity}_{decision.isoformat()}",
                    instrument_id=identity, decision_at=decision, available_at=decision, current_price=100.,
                    features={"return_pct": 1.}, feature_version=changes.get("feature_version", config.feature_version),
                    context={"configuration_hash": "f" * 64 if n == sessions - 1 and mismatch == "hash" else feature_hash},
                    pit_status="TEST_FIXTURE"))
        labels = list(mature_outcomes(states, bars, receipts, calendar,
            as_of=datetime.combine(historical_day, time(15), cli.SHANGHAI)))
        if missing and n == sessions - 1:
            labels = [item for item in labels if (item.instrument_id, item.decision_at.strftime("%H:%M"), item.horizon) != missing]
        if bad_parent and n == 0:
            labels[0] = labels[0].model_copy(update={"source_ids": ("f" * 64,)})
        research.append("features", states)
        research.append("outcomes", labels)
    return calendar, scope, config


def test_doctor_nonempty_research_tables_are_not_enough(tmp_path):
    root = tmp_path / "TEST_FIXTURE_ONE_PRIOR_SESSION"
    dataset(root, sessions=1)
    report, code = cli.doctor(root)
    assert code == 0 and report["replay_ready"]
    assert not report["research_ready"] and not report["test_fixture_research_ready"]
    inputs = report["research_inputs"]
    assert inputs["feature_states"] == 20 and inputs["mature_outcomes"] == 95
    assert inputs["status"] == "INSUFFICIENT_HISTORY"
    assert len(inputs["coverage"]) == 20
    assert all(not item["ready"] for item in inputs["coverage"])


def test_doctor_fixture_history_ready_separates_real_research_and_edge(tmp_path, monkeypatch):
    root = tmp_path / "TEST_FIXTURE_TWO_PRIOR_SESSIONS"
    dataset(root)
    original = ResearchStore.visible
    def visible(self, kind, **kwargs):
        assert kind != "outcomes", "doctor must not materialize the full label archive"
        return original(self, kind, **kwargs)
    monkeypatch.setattr(ResearchStore, "visible", visible)
    report, code = cli.doctor(root)
    assert code == 0 and report["replay_ready"]
    assert report["test_fixture_research_ready"] and not report["research_ready"]
    inputs = report["research_inputs"]
    assert inputs["status"] == "TEST_FIXTURE_HISTORY_READY"
    assert inputs["label_proof"]["outcomes"] == 190
    assert inputs["min_samples"] == 2 and all(item["ready"] for item in inputs["coverage"])
    assert all(horizon["eligible_prior_sessions"] == horizon["test_fixture_sessions"] == 2
        for group in inputs["coverage"] for horizon in group["horizons"].values())
    assert inputs["investment_evaluation"] == "NOT_EVALUATED"
    assert inputs["incremental_edge"] == "NO_EVIDENCE_OF_INCREMENTAL_EDGE"
    assert report["real_minute_validation"] == "NOT_PERFORMED"


@pytest.mark.parametrize("mismatch", ["version", "hash"])
def test_doctor_does_not_pool_different_feature_buckets(tmp_path, mismatch):
    root = tmp_path / "TEST_FIXTURE_INCOMPATIBLE_BUCKET"
    dataset(root, mismatch=mismatch)
    report, code = cli.doctor(root)
    assert code == 0 and not report["test_fixture_research_ready"]
    inputs = report["research_inputs"]
    assert len(inputs["feature_buckets"]) == 2
    assert sum(inputs["excluded_states"].values()) == 20
    assert all(item["compatible_prior_sessions"] == 1 for item in inputs["coverage"])


def test_doctor_one_missing_horizon_prevents_whole_grid_readiness(tmp_path):
    root = tmp_path / "TEST_FIXTURE_MISSING_HORIZON"
    dataset(root, missing=("000300.SH", "10:00", "30m"))
    report, code = cli.doctor(root)
    assert code == 0 and not report["test_fixture_research_ready"]
    limited = [item for item in report["research_inputs"]["coverage"] if not item["ready"]]
    assert len(limited) == 1
    assert limited[0]["instrument_id"] == "000300.SH" and limited[0]["decision_time"] == "10:00"
    assert limited[0]["horizons"]["30m"]["eligible_prior_sessions"] == 1


def test_doctor_research_labels_need_actual_parent_proof(tmp_path):
    root = tmp_path / "TEST_FIXTURE_MISSING_PARENT"
    dataset(root, bad_parent=True)
    report, code = cli.doctor(root)
    assert code == 2 and not report["research_ready"] and not report["test_fixture_research_ready"]
    assert "STORAGE_INTEGRITY_ERROR" in report["errors"] or "SOURCE_RECEIPT_MISSING" in report["errors"]


@pytest.mark.parametrize("value", ["", "9:45", "09:5", "09:45:00", "nan", "24:00", "09:30",
    "09:44", "11:35", "12:00", "12:55", "15:00", "15:05", "09:45,", ",09:45",
    "09:45,09:45", "09:45, 10:00", "09:45;10:00"])
def test_evaluate_rejects_invalid_decision_times_before_storage(tmp_path, capsys, value):
    root = tmp_path / "TEST_FIXTURE_ABSENT"
    assert cli.main(["evaluate", "--data-root", str(root), "--as-of", "2026-09-30T16:00:00+08:00",
                     "--decision-times=" + value]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"] == "INVALID_EVALUATION_DECISION_TIMES"
    assert not root.exists()


def test_evaluation_time_boundaries_and_explicit_early_times():
    assert cli._decision_times("09:35,09:40,09:45,11:30,13:00,14:55") == (
        "09:35", "09:40", "09:45", "11:30", "13:00", "14:55")


def test_serve_reuses_the_app_view_after_prelisten_validation(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    import stockfish_live.api.app as api_module
    import uvicorn
    events = []
    class Catalog:
        def validate_startup(self):
            events.append("precheck")
    app = SimpleNamespace(state=SimpleNamespace(run_catalog=Catalog()))
    def create_app(path):
        assert path == tmp_path
        events.append("create")
        return app
    def run(actual_app, *, host, port):
        assert actual_app is app and events == ["create", "precheck"]
        assert host == "127.0.0.1" and port == 8765
        events.append("listen")
    monkeypatch.setattr(api_module, "create_app", create_app)
    monkeypatch.setattr(uvicorn, "run", run)
    assert cli.main(["serve", "--run-dir", str(tmp_path)]) == 0
    assert events == ["create", "precheck", "listen"]
    assert json.loads(capsys.readouterr().out) == {"server": "STOPPED"}


def test_serve_range_directory_uses_same_validated_catalog_without_writing(tmp_path, monkeypatch, capsys):
    from fastapi.testclient import TestClient
    import stockfish_live.api.app as api_module
    from stockfish_live.common import digest
    import uvicorn

    root = tmp_path / "TEST_FIXTURE_RANGE_SERVE"
    days = ("2026-10-06", "2026-10-07")
    for day in days:
        folder = root / day / "snapshots"
        folder.mkdir(parents=True)
        record = {"schema_version": "LIVE_REPLAY_STATE_V2", "as_of": day+"T09:45:00+08:00",
            "pit_status": "TEST_FIXTURE", "scope": "DECLARED_SCOPE_ONLY", "received_bars": [],
            "state": {"quality": "PARTIAL", "warnings": ["TEST_FIXTURE_NO_DATA"]}, "forecasts": {}}
        record["snapshot_id"] = digest(record)
        (folder / "0945.json").write_text(canonical_json(record))
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    real_create_app = api_module.create_app
    applications = []
    def create_app(path):
        app = real_create_app(path, now=lambda: datetime(2026, 10, 8, 9, tzinfo=cli.SHANGHAI))
        applications.append(app)
        return app
    def run(app, *, host, port):
        assert applications == [app]
        assert app.state.run_catalog.is_range
        assert host == "127.0.0.1" and port == 8765
        with TestClient(app) as client:
            assert client.get("/api/live/dates").json()["available_dates"] == list(days)
            for day in days:
                response = client.get("/api/live/market", params={"date": day})
                assert response.status_code == 200
                assert response.json()["as_of"].startswith(day)
    monkeypatch.setattr(api_module, "create_app", create_app)
    monkeypatch.setattr(uvicorn, "run", run)
    assert cli.main(["serve", "--run-dir", str(root)]) == 0
    assert json.loads(capsys.readouterr().out) == {"server": "STOPPED"}
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_evaluate_filters_selected_history_but_preserves_unselected_saved_forecast(tmp_path, monkeypatch, capsys):
    from stockfish_live.forecast.engine import ForecastEngine
    root = tmp_path / "TEST_FIXTURE_EVAL_FILTER"
    calendar, scope, config = dataset(root)
    as_of = datetime.combine(calendar.sessions[-1], time(16), cli.SHANGHAI)
    store = ResearchStore(root)
    states = store.visible("features", cutoff=as_of, now=as_of)
    outcomes = store.visible("outcomes", cutoff=as_of, now=as_of)
    query = next(item for item in states if item.instrument_id == INDEX_IDS[0]
                 and item.decision_at.date() == calendar.sessions[-2] and item.decision_at.time() == time(10))
    original_config = config.model_copy(update={"k": 1, "min_samples": 1})
    saved = ForecastEngine(calendar, original_config).predict(query, states, outcomes, as_of=query.available_at)
    assert saved.status == "AVAILABLE"
    store.append("forecasts", [saved])
    original_visible = ResearchStore.visible
    def visible(self, kind, **kwargs):
        assert kind != "outcomes", "evaluation must not materialize the full label archive"
        return original_visible(self, kind, **kwargs)
    monkeypatch.setattr(ResearchStore, "visible", visible)
    assert cli.main(["evaluate", "--data-root", str(root), "--as-of", as_of.isoformat(),
                     "--decision-times", "09:45", "--no-tune"]) == 0
    output = json.loads(capsys.readouterr().out)
    report = json.loads(Path(output["output"]).read_text())
    assert report["input_scope"]["archive_feature_states"] == 40
    assert report["input_scope"]["included_feature_states"] == 11
    assert report["input_scope"]["included_mature_outcomes"] == 55
    assert report["input_scope"]["unselected_archive_parts_integrity_checked"]
    posterior = report["saved_forecast_posterior"]
    assert posterior["fixture_sample_count"] == 5
    assert {item["forecast_id"] for item in posterior["rows"]} == {saved.forecast_id}
    assert all(item["configuration_hash"] == original_config.configuration_hash for item in posterior["rows"])
    assert cli.main(["evaluate", "--data-root", str(root), "--as-of", as_of.isoformat(),
                     "--all-decision-times", "--no-tune"]) == 0
    all_output = json.loads(capsys.readouterr().out)
    all_report = json.loads(Path(all_output["output"]).read_text())
    assert all_report["decision_times"] == "ALL_RECORDED_SCHEDULED"
    assert all_report["input_scope"]["all_decision_times"]
    assert all_report["input_scope"]["included_feature_states"] == 40
    assert all_report["input_scope"]["included_mature_outcomes"] == 190
    assert all_report["saved_forecast_posterior"] == posterior
