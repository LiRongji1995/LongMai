"""Date selection and explicit hindsight use isolated TEST_FIXTURE archives."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from fastapi.testclient import TestClient
import pytest

from stockfish_live.api.app import create_app
from stockfish_live.api.catalog import RunCatalog
from stockfish_live.common import SHANGHAI, canonical_json, digest
from stockfish_live.contracts.market import MinuteBar
from test_api import ENDPOINTS, synthetic_forecast


DAY = date(2026, 10, 6)


def moment(day=DAY, hour=9, minute=45, second=0):
    return datetime.combine(day, time(hour, minute, second), SHANGHAI)


def forecast_for(day):
    shift = day - date(2026, 10, 9)
    def move(value):
        if isinstance(value, dict):
            return {key: move(item) for key, item in value.items()}
        if isinstance(value, list):
            return [move(item) for item in value]
        if isinstance(value, str) and value[:4].isdigit() and "T" in value:
            return (datetime.fromisoformat(value) + shift).isoformat()
        return value
    result = move(synthetic_forecast())
    result["forecast_id"] = digest(["TEST_FIXTURE_DATE_FORECAST", day.isoformat()])
    return result


def save_run(root, day=DAY, *, full=False, model=True, late_close=False,
             missing_minute=None, shifted_minute=False, second_index=False):
    """Two immutable snapshots; full=True has independently specified 240 END bars."""
    (root / "snapshots").mkdir(parents=True)
    minutes = ([moment(day, 9, 31) + timedelta(minutes=i) for i in range(120)]
               + [moment(day, 13, 1) + timedelta(minutes=i) for i in range(120)]) if full else [
                   moment(day), moment(day, 15, 0)]
    if missing_minute is not None:
        minutes.remove(missing_minute)
    if shifted_minute:
        minutes[1] = moment(day, 9, 30)  # Same count, wrong minute coverage.
    bars = []
    for when in minutes:
        price = 1234. if when.hour == 15 else 100.
        available = when + timedelta(seconds=1) if late_close and when.hour == 15 else when
        row = MinuteBar(instrument_id="000001.SH", exchange="SH", symbol="000001", asset_type="INDEX",
            timestamp=when, open=price, high=price, low=price, close=price, volume=100., turnover=price*100,
            provider="TEST_FIXTURE_DATE_API", source_timestamp=when, source_timestamp_basis="PUBLISHED",
            available_at=available, source_id=digest(["TEST_FIXTURE_SOURCE", when.isoformat()]),
            quality="VALID", pit_status="TEST_FIXTURE").model_dump(mode="json")
        bars.append(row)
        if second_index and when.hour < 12:
            other = {**row, "instrument_id": "399001.SZ", "exchange": "SZ", "symbol": "399001"}
            bars.append(other)
    snapshots = []
    for when in (moment(day), moment(day, 15, 1)):
        received = [row for row in bars if datetime.fromisoformat(row["available_at"]) <= when
                    and (not snapshots or datetime.fromisoformat(row["available_at"]) > moment(day))]
        indices = {row["instrument_id"]: {"current_price": row["close"]} for row in received}
        value = {"schema_version": "LIVE_REPLAY_STATE_V2", "as_of": when.isoformat(),
            "data_cutoff": max(row["timestamp"] for row in received),
            "input_hash": digest(["TEST_FIXTURE_INPUT", when.isoformat()]),
            "config_hash": digest("TEST_FIXTURE_CONFIG"), "scope": "DECLARED_SCOPE_ONLY",
            "pit_status": "TEST_FIXTURE", "cycle_error": None,
            "state": {"quality": "VALID", "bar_count": len(received), "warnings": []},
            "received_bars": received,
            "market_state": {"as_of": when.isoformat(), "indices": indices,
                "breadth": {"status": "UNAVAILABLE"}, "sentiment": {"state": "UNKNOWN"},
                "sectors": {"status": "UNAVAILABLE", "sectors": []}},
            "forecasts": {"000001.SH": forecast_for(day)} if model else {}}
        value["snapshot_id"] = digest(value)
        snapshots.append(value)
    write_archive(root, day, snapshots)
    return snapshots


def write_archive(root, day, records):
    for value in records:
        path = root / "snapshots" / f"{datetime.fromisoformat(value['as_of']):%H%M}.json"
        path.write_text(canonical_json(value), encoding="utf-8")
    summary = {"date": day.isoformat(), "ticks": len(records),
        "snapshot_sequence_hash": digest([row["snapshot_id"] for row in records]),
        "final_state": records[-1]["state"], "pit_status": "TEST_FIXTURE",
        "scope": "DECLARED_SCOPE_ONLY", "failed_cycles": sum(row.get("cycle_error") is not None for row in records)}
    (root / "summary.json").write_text(canonical_json(summary), encoding="utf-8")


def fixed_now():
    return moment(date(2026, 10, 7), 12)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_each_date_selected_endpoint_preserves_original_time_cutoff(tmp_path, endpoint):
    root = tmp_path / "TEST_FIXTURE_RANGE"
    save_run(root / DAY.isoformat())
    save_run(root / "2026-10-05", date(2026, 10, 5))
    with TestClient(create_app(root, now=fixed_now), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        result = client.get("/api/live/"+endpoint, params={"date": DAY.isoformat(), "as_of": moment().isoformat()})
    assert result.status_code == 200, result.text
    assert result.json()["selected_date"] == DAY.isoformat()
    assert result.json()["as_of"] == moment().isoformat()
    assert "1234" not in result.text
    assert result.json()["input_hash"] == digest(["TEST_FIXTURE_INPUT", moment().isoformat()])
    # Previous-day dates legitimately occur in mature neighbor evidence; the
    # other day's saved forecast itself must never become the selected result.
    assert forecast_for(date(2026, 10, 5))["forecast_id"] not in result.text


@pytest.mark.parametrize(("selected", "error"), [
    ("20261006", "INVALID_DATE"), ("../2026-10-06", "INVALID_DATE"),
    ("2026-02-30", "INVALID_DATE"), ("2026-10-08", "DATE_OUT_OF_RANGE"),
    ("2025-10-06", "DATE_OUT_OF_RANGE"), ("2026-10-04", "DATE_NOT_AVAILABLE")])
def test_invalid_out_of_range_and_missing_dates_fail_distinctly(tmp_path, selected, error):
    root = tmp_path / "TEST_FIXTURE_RANGE"
    save_run(root / DAY.isoformat())
    with TestClient(create_app(root, now=fixed_now), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        result = client.get("/api/live/status", params={"date": selected})
    assert result.status_code == 422
    assert result.json()["error"] == error


@pytest.mark.parametrize("endpoint", (*ENDPOINTS, "comparison"))
def test_date_and_observation_timestamp_must_agree(tmp_path, endpoint):
    root = tmp_path / "TEST_FIXTURE_RUN"
    save_run(root)
    with TestClient(create_app(root, now=fixed_now), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        result = client.get("/api/live/"+endpoint, params={
            "date": DAY.isoformat(), "as_of": moment(DAY - timedelta(days=1)).isoformat()})
    assert result.status_code == 422
    assert result.json()["error"] == "AS_OF_DATE_MISMATCH"


def test_catalog_one_calendar_year_is_inclusive_and_uses_shanghai_clock(tmp_path):
    root = tmp_path / "TEST_FIXTURE_RANGE"
    # UTC October 6 evening is October 7 in Shanghai.
    now = datetime(2026, 10, 6, 23, 30, tzinfo=timezone.utc)
    for day in (date(2025, 10, 6), date(2025, 10, 7), DAY, date(2026, 10, 8)):
        save_run(root / day.isoformat(), day)
    with TestClient(create_app(root, now=lambda: now), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        value = client.get("/api/live/dates").json()
        latest = client.get("/api/live/status").json()
    assert value == {"schema_version": "LIVE_DASHBOARD_DATES_V1", "min_date": "2025-10-07",
        "max_date": "2026-10-07", "latest_date": "2026-10-06",
        "available_dates": ["2025-10-07", "2026-10-06"]}
    assert latest["selected_date"] == DAY.isoformat()
    assert str(root) not in canonical_json(value)
    assert RunCatalog.bounds(moment(date(2024, 2, 29))) == (date(2023, 2, 28), date(2024, 2, 29))


def test_closed_day_comparison_has_full_actual_and_original_saved_prediction(tmp_path):
    root = tmp_path / "TEST_FIXTURE_RUN"
    save_run(root, full=True)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    with TestClient(create_app(root, now=fixed_now), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        params = {"date": DAY.isoformat(), "as_of": moment().isoformat()}
        pit = client.get("/api/live/market", params=params).json()
        comparison = client.get("/api/live/comparison", params=params).json()
        prediction = client.get("/api/live/forecast", params=params).json()
    assert len(pit["actual_paths"]["000001.SH"]) == 15
    assert len(comparison["actual_paths"]["000001.SH"]) == 240
    assert comparison["as_of"] == prediction["forecast"]["as_of"] == moment().isoformat()
    assert comparison["input_hash"] == pit["input_hash"]
    assert comparison["snapshot_id"] == pit["snapshot_id"]
    assert comparison["data_cutoff"] == moment().isoformat()
    assert comparison["actual_as_of"] == moment(DAY, 15, 0).isoformat()
    assert comparison["session_closed"] is True
    assert comparison["display_mode"] == "CLOSED_SESSION_COMPARISON"
    assert comparison["archive_status"] == "COMPLETE"
    assert comparison["forecast_status"] == "SAVED_FORECAST"
    assert "forecasts" not in comparison and "market_state" not in comparison
    assert {path: path.read_bytes() for path in before} == before


def test_a_finished_job_does_not_close_current_trading_session_or_expose_later_snapshots(tmp_path):
    root = tmp_path / "TEST_FIXTURE_RUN"
    save_run(root, full=True)
    now = lambda: moment(DAY, 9, 46)
    with TestClient(create_app(root, now=now), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        status = client.get("/api/live/status", params={"date": DAY.isoformat()}).json()
        value = client.get("/api/live/comparison", params={"date": DAY.isoformat()}).json()
        future = client.get("/api/live/market", params={
            "date": DAY.isoformat(), "as_of": moment(DAY, 10, 0).isoformat()})
    assert status["timeline"] == [moment().isoformat()]
    assert status["summary"] is None
    assert len(value["actual_paths"]["000001.SH"]) == 15
    assert "1234" not in canonical_json(value)
    assert value["session_closed"] is False and value["display_mode"] == "AS_OF_ONLY"
    assert value["archive_status"] == "PARTIAL"
    assert future.status_code == 422 and future.json()["error"] == "FUTURE_ACCESS_DENIED"


def test_today_before_first_snapshot_is_unavailable(tmp_path):
    root = tmp_path / "TEST_FIXTURE_RUN"
    save_run(root)
    with TestClient(create_app(root, now=lambda: moment(DAY, 9, 30)), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        result = client.get("/api/live/status", params={"date": DAY.isoformat()})
    assert result.status_code == 422 and result.json()["error"] == "DATE_NOT_AVAILABLE"


def test_unseen_today_does_not_hide_latest_available_past_date(tmp_path):
    root = tmp_path / "TEST_FIXTURE_RANGE"
    save_run(root / DAY.isoformat())
    previous = DAY - timedelta(days=1)
    save_run(root / previous.isoformat(), previous)
    with TestClient(create_app(root, now=lambda: moment(DAY, 9, 30)), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        catalog = client.get("/api/live/dates").json()
        latest = client.get("/api/live/status").json()
    assert catalog["available_dates"] == [previous.isoformat()]
    assert catalog["latest_date"] == previous.isoformat()
    assert latest["selected_date"] == previous.isoformat()
    # Once the same immutable first tick is visible, its date becomes available.
    with TestClient(create_app(root, now=lambda: moment(DAY, 9, 45)), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        catalog = client.get("/api/live/dates").json()
    assert catalog["latest_date"] == DAY.isoformat()


def test_delayed_close_bar_becomes_visible_after_receipt_without_changing_pit_snapshot(tmp_path):
    root = tmp_path / "TEST_FIXTURE_RUN"
    save_run(root, full=True, late_close=True)
    params = {"date": DAY.isoformat(), "as_of": moment().isoformat()}
    with TestClient(create_app(root, now=lambda: moment(DAY, 15, 0)), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        not_received = client.get("/api/live/comparison", params=params).json()
    with TestClient(create_app(root, now=lambda: moment(DAY, 15, 1)), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        received = client.get("/api/live/comparison", params=params).json()
        pit = client.get("/api/live/market", params=params).json()
    assert not_received["session_closed"] is True
    assert not_received["archive_status"] == "PARTIAL"
    assert not_received["actual_as_of"] == moment().isoformat()  # Final snapshot itself was not yet visible.
    assert received["actual_as_of"] == moment(DAY, 15, 0).isoformat()
    assert received["archive_status"] == "COMPLETE"
    assert len(received["actual_paths"]["000001.SH"]) == 240
    assert len(pit["actual_paths"]["000001.SH"]) == 15


@pytest.mark.parametrize("kind", ["missing", "shifted", "failed", "quality", "no_summary"])
def test_partial_archives_never_claim_complete_even_with_session_end_bar(tmp_path, kind):
    root = tmp_path / "TEST_FIXTURE_RUN"
    records = save_run(root, full=True,
        missing_minute=moment(DAY, 10, 5) if kind == "missing" else None,
        shifted_minute=kind == "shifted")
    if kind == "failed":
        records[-1]["cycle_error"] = "TEST_FIXTURE_TIMEOUT"
    if kind == "quality":
        records[-1]["state"]["quality"] = "GAPPED"
    if kind in {"failed", "quality"}:
        records[-1]["snapshot_id"] = digest({key: value for key, value in records[-1].items() if key != "snapshot_id"})
        write_archive(root, DAY, records)
    if kind == "no_summary":
        (root / "summary.json").unlink()
    with TestClient(create_app(root, now=fixed_now), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        result = client.get("/api/live/comparison", params={"date": DAY.isoformat()})
    assert result.status_code == 200, result.text
    assert result.json()["archive_status"] == "PARTIAL"
    assert result.json()["actual_as_of"] == moment(DAY, 15, 0).isoformat()


def test_selected_index_actual_cutoff_cannot_borrow_another_index_close(tmp_path):
    root = tmp_path / "TEST_FIXTURE_RUN"
    save_run(root, full=True, second_index=True, model=False)
    with TestClient(create_app(root, now=fixed_now), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        first = client.get("/api/live/comparison", params={"date": DAY.isoformat()}).json()
        other = client.get("/api/live/comparison", params={"date": DAY.isoformat(), "index": "399001.SZ"}).json()
    assert first["actual_as_of"] == moment(DAY, 15, 0).isoformat()
    assert other["actual_as_of"] == moment(DAY, 11, 30).isoformat()
    assert other["archive_status"] == "PARTIAL"
    assert other["forecast_status"] == "NO_SAVED_FORECAST"


def test_unknown_selected_index_has_no_actual_cutoff_or_complete_claim(tmp_path):
    root = tmp_path / "TEST_FIXTURE_RUN"
    save_run(root, full=True)
    with TestClient(create_app(root, now=fixed_now), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        result = client.get("/api/live/comparison", params={"date": DAY.isoformat(), "index": "399006.SZ"}).json()
    assert result["actual_as_of"] is None
    assert result["archive_status"] == "PARTIAL"
    assert result["forecast_status"] == "NO_SAVED_FORECAST"


def test_extra_post_session_actual_bar_is_hidden_and_marks_archive_partial(tmp_path):
    root = tmp_path / "TEST_FIXTURE_RUN"
    records = save_run(root, full=True)
    extra = {**records[-1]["received_bars"][-1],
             "timestamp": moment(DAY, 15, 1).isoformat(),
             "source_timestamp": moment(DAY, 15, 1).isoformat(),
             "available_at": moment(DAY, 15, 1).isoformat()}
    records[-1]["received_bars"].append(extra)
    records[-1]["data_cutoff"] = extra["timestamp"]
    records[-1]["snapshot_id"] = digest({key: value for key, value in records[-1].items() if key != "snapshot_id"})
    write_archive(root, DAY, records)
    with TestClient(create_app(root, now=fixed_now), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        result = client.get("/api/live/comparison", params={"date": DAY.isoformat()}).json()
    assert len(result["actual_paths"]["000001.SH"]) == 240
    assert result["actual_as_of"] == moment(DAY, 15, 0).isoformat()
    assert result["archive_status"] == "PARTIAL"


def test_catalog_only_inspects_direct_days_and_keeps_full_views_bounded(tmp_path, monkeypatch):
    root = tmp_path / "TEST_FIXTURE_RANGE"
    for delta in range(6):
        day = DAY - timedelta(days=delta)
        save_run(root / day.isoformat(), day)
    save_run(root / "ignored_backup" / "2026-10-07", date(2026, 10, 7))
    import stockfish_live.api.view as view_module
    reads = []
    original = view_module._read_json
    monkeypatch.setattr(view_module, "_read_json", lambda path: (reads.append(path), original(path))[1])
    catalog = RunCatalog(root, now=fixed_now)
    assert len(catalog.dates()["available_dates"]) == 6
    assert reads == []  # Completed inventory deserializes no daily snapshots.
    for day in catalog.dates()["available_dates"]:
        catalog.select(day)
    assert len(catalog._views) == 4


@pytest.mark.parametrize("kind", ["symlink_day", "symlink_root", "wrong_named_day", "wrong_forecast_day"])
def test_selected_archives_reject_identity_and_symlink_tampering(tmp_path, kind):
    root = tmp_path / "TEST_FIXTURE_RANGE"
    day_root = root / DAY.isoformat()
    records = save_run(day_root)
    selected_root = root
    if kind == "symlink_day":
        (root / "2026-10-05").symlink_to(day_root, target_is_directory=True)
    elif kind == "symlink_root":
        selected_root = tmp_path / "TEST_FIXTURE_SYMLINK"
        selected_root.symlink_to(root, target_is_directory=True)
    elif kind == "wrong_named_day":
        day_root.rename(root / "2026-10-05")
    else:
        for record in records:
            forecast = record["forecasts"]["000001.SH"]
            forecast["horizons"][0]["target_at"] = moment(DAY + timedelta(days=1), 10, 15).isoformat()
            record["snapshot_id"] = digest({key: value for key, value in record.items() if key != "snapshot_id"})
        write_archive(day_root, DAY, records)
    with TestClient(create_app(selected_root, now=fixed_now), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        response = client.get("/api/live/status", params={"date": DAY.isoformat()})
    assert response.status_code == 503
    assert response.json()["error"] == "RUN_INTEGRITY_ERROR"


def test_websocket_date_selection_is_pit_and_input_errors_are_explicit(tmp_path):
    root = tmp_path / "TEST_FIXTURE_RANGE"
    save_run(root / DAY.isoformat())
    with TestClient(create_app(root, now=fixed_now, poll_interval=.05), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        with client.websocket_connect(f"ws://127.0.0.1/ws/live?date={DAY.isoformat()}&as_of={moment().isoformat().replace('+', '%2B')}") as socket:
            value = socket.receive_json()
            assert value["selected_date"] == DAY.isoformat()
            assert "1234" not in canonical_json(value)
        with client.websocket_connect("ws://127.0.0.1/ws/live?date=2026-10-08") as socket:
            assert socket.receive_json()["error"] == "DATE_OUT_OF_RANGE"


def test_empty_catalog_never_creates_missing_run_or_fake_date(tmp_path):
    root = tmp_path / "TEST_FIXTURE_MISSING_RUN"
    with TestClient(create_app(root, now=fixed_now), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        catalog = client.get("/api/live/dates").json()
        missing = client.get("/api/live/comparison", params={"date": DAY.isoformat()})
    assert catalog["available_dates"] == [] and catalog["latest_date"] is None
    assert missing.status_code == 422 and missing.json()["error"] == "DATE_NOT_AVAILABLE"
    assert not root.exists()
