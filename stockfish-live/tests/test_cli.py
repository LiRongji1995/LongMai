"""Small isolated TEST_FIXTURE checks for the public Phase 1 CLI boundary."""
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from stockfish_live import cli
from stockfish_live.calendar import TradingCalendar
from stockfish_live.common import canonical_json
from stockfish_live.contracts.market import DatasetScope, Instrument, MinuteBar, SourceReceipt
from stockfish_live.providers import mock
from stockfish_live.storage.parquet_store import ParquetStore

SH = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 10, 9)


def at(hour=9, minute=0):
    return datetime(2026, 10, 9, hour, minute, tzinfo=SH)


def small_dataset(root: Path, *, sessions=(DAY,), scope_available=None,
                  scope_version=None, calendar_available=None,
                  bar_changes=None, receipt_changes=None):
    """One genuine Parquet fixture row, with explicit calendar/scope metadata."""
    root.mkdir()
    calendar = TradingCalendar(schema_version="LIVE_CALENDAR_V1", sessions=sessions,
        coverage_start=min(sessions), coverage_end=max(sessions), exchanges=("SH",),
        source="TEST_FIXTURE_CLI_EXPLICIT_CALENDAR", available_at=calendar_available or at(),
        pit_status="TEST_FIXTURE")
    scope = DatasetScope(calendar_version=scope_version or calendar.version,
        instruments=(Instrument(instrument_id="000001.SH", asset_type="INDEX"),),
        available_at=scope_available or at(), pit_status="TEST_FIXTURE")
    (root / "calendar.json").write_text(canonical_json(calendar), encoding="utf-8")
    (root / "dataset.json").write_text(canonical_json(scope), encoding="utf-8")
    raw = b"TEST_FIXTURE_CLI_SINGLE_MINUTE"
    source_id = hashlib.sha256(raw).hexdigest()
    event = at(9, 31)
    receipt_fields = dict(source_id=source_id, provider="TEST_FIXTURE",
        source_timestamp=event, source_timestamp_basis="PUBLISHED", received_at=event,
        available_at=event, pit_status="TEST_FIXTURE")
    receipt_fields.update(receipt_changes or {})
    receipt = SourceReceipt(**receipt_fields)
    bar_fields = dict(instrument_id="000001.SH", symbol="000001", exchange="SH", asset_type="INDEX",
        timestamp=event, open=10., high=10.2, low=9.9, close=10.1, volume=1000., turnover=10100.,
        provider=receipt.provider, source_timestamp=event, source_timestamp_basis="PUBLISHED",
        available_at=event, source_id=source_id, quality="VALID", pit_status="TEST_FIXTURE")
    bar_fields.update(bar_changes or {})
    bar = MinuteBar(**bar_fields)
    ParquetStore(root).append([bar], receipt, raw)
    return calendar, scope


def assert_error(capsys, code):
    captured = capsys.readouterr()
    assert captured.out == ""
    result = json.loads(captured.err)
    assert result["error"] == code
    assert result["market_edge"] == "NOT_ESTABLISHED"


def test_doctor_empty_root_is_unavailable_and_does_not_create_directory(tmp_path, capsys):
    root = tmp_path / "TEST_FIXTURE_absent"
    assert cli.main(["doctor", "--data-root", str(root)]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["replay_ready"] is False
    assert result["research_ready"] is False
    assert result["real_minute_validation"] == "NOT_PERFORMED"
    assert result["storage"] == "UNAVAILABLE"
    assert "CALENDAR_INVALID" in result["errors"]
    assert "MINUTE_DATA_UNAVAILABLE" in result["errors"]
    assert not root.exists()


@pytest.mark.parametrize("command,code", [
    ("live", "LIVE_PROVIDER_UNAVAILABLE"),
    ("serve", "RUN_DIRECTORY_REQUIRED"),
    ("evaluate", "EVALUATION_CUTOFF_REQUIRED"),
])
def test_unavailable_commands_fail_explicitly_without_data_side_effects(tmp_path, capsys, command, code):
    root = tmp_path / "TEST_FIXTURE_absent"
    assert cli.main([command, "--data-root", str(root)]) == 2
    assert_error(capsys, code)
    assert not root.exists()


def test_fixture_creates_new_isolated_root_and_refuses_overwrite(tmp_path, monkeypatch, capsys):
    root = tmp_path / "TEST_FIXTURE_new"
    # Keep the real builder's directory, metadata, raw source and Parquet path,
    # while reducing its 240-minute engineering fixture to one test minute.
    monkeypatch.setattr(mock.TradingCalendar, "minutes", lambda self, day: (at(9, 31),))
    command = ["fixture", "--data-root", str(root), "--date", DAY.isoformat()]
    assert cli.main(command) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["label"] == "TEST_FIXTURE"
    assert created["rows"] == 8
    saved = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert "calendar.json" in {str(path) for path in saved}
    assert cli.main(command) == 2
    assert_error(capsys, "FIXTURE_DESTINATION_EXISTS")
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == saved


@pytest.mark.parametrize("range_flag", ["--start", "--end"])
def test_replay_date_and_range_conflict_precedes_storage_access(tmp_path, capsys, range_flag):
    root = tmp_path / "TEST_FIXTURE_absent"
    assert cli.main(["replay", "--data-root", str(root), "--date", DAY.isoformat(),
                     range_flag, DAY.isoformat()]) == 2
    assert_error(capsys, "REPLAY_DATE_ARGUMENT_CONFLICT")
    assert not root.exists()


def test_replay_unknown_calendar_fails_without_creating_output(tmp_path, capsys):
    root = tmp_path / "TEST_FIXTURE_absent"
    output = tmp_path / "TEST_FIXTURE_output"
    assert cli.main(["replay", "--data-root", str(root), "--date", DAY.isoformat(),
                     "--output", str(output)]) == 2
    assert_error(capsys, "CALENDAR_INVALID")
    assert not root.exists() and not output.exists()


@pytest.mark.parametrize("options,code", [
    ({"scope_available": at(9, 31)}, "SCOPE_NOT_YET_AVAILABLE"),
    ({"scope_version": "0" * 64}, "SCOPE_CALENDAR_MISMATCH"),
    ({"calendar_available": at(9, 31)}, "CALENDAR_NOT_YET_AVAILABLE"),
])
def test_replay_late_or_wrong_authority_fails_before_output(tmp_path, capsys, options, code):
    root = tmp_path / "TEST_FIXTURE_data"
    output = tmp_path / "TEST_FIXTURE_output"
    small_dataset(root, **options)
    saved = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert cli.main(["replay", "--data-root", str(root), "--date", DAY.isoformat(),
                     "--output", str(output)]) == 2
    assert_error(capsys, code)
    assert not output.exists()
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == saved


@pytest.mark.parametrize("arguments,code", [
    (["--start", "2026-10-08", "--end", "2026-10-09"], "CALENDAR_OUT_OF_COVERAGE"),
    (["--start", "2026-10-09"], "REPLAY_DATE_RANGE_REQUIRED"),
    (["--start", "2026-10-10", "--end", "2026-10-09"], "REPLAY_DATE_RANGE_REQUIRED"),
    (["--date", "2026-10-10"], "CALENDAR_OUT_OF_COVERAGE"),
])
def test_replay_range_and_date_need_explicit_calendar_coverage(tmp_path, capsys, arguments, code):
    root = tmp_path / "TEST_FIXTURE_data"
    output = tmp_path / "TEST_FIXTURE_output"
    small_dataset(root)
    assert cli.main(["replay", "--data-root", str(root), "--output", str(output), *arguments]) == 2
    assert_error(capsys, code)
    assert not output.exists()


def test_range_wrong_calendar_authority_does_not_create_partial_output(tmp_path, capsys):
    root = tmp_path / "TEST_FIXTURE_data"
    output = tmp_path / "TEST_FIXTURE_output"
    small_dataset(root, sessions=(DAY, date(2026, 10, 12)), scope_version="0" * 64)
    assert cli.main(["replay", "--data-root", str(root), "--start", DAY.isoformat(),
                     "--end", "2026-10-12", "--output", str(output)]) == 2
    assert_error(capsys, "SCOPE_CALENDAR_MISMATCH")
    assert not output.exists()


def test_doctor_archive_rows_available_tomorrow_do_not_prove_strict_replay_ready(tmp_path, capsys):
    root = tmp_path / "TEST_FIXTURE_late_archive"
    tomorrow = at(9, 31) + timedelta(days=1)
    small_dataset(root, bar_changes={"available_at": tomorrow},
        receipt_changes={"received_at": tomorrow, "available_at": tomorrow})
    original = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert cli.main(["doctor", "--data-root", str(root)]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report["historical_data_coverage"]["row_count"] == 1
    assert report["storage"] == "AVAILABLE"
    assert report["strict_visible_rows_at_close"] == 0
    assert report["late_or_unavailable_rows"] == 1
    assert report["replay_ready"] is False
    assert report["errors"] == ["STRICT_REPLAY_DATA_UNAVAILABLE"]
    assert report["declared_scope_coverage"] == [{
        "date": DAY.isoformat(), "strict_visible_rows": 0,
        "expected_rows": 240, "gap_count": 240, "quality": "GAPPED",
    }]
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == original


def test_range_failure_retains_completed_day_and_marks_incomplete(tmp_path, capsys):
    root = tmp_path / "TEST_FIXTURE_partial_range"
    output = tmp_path / "TEST_FIXTURE_partial_output"
    last_day = date(2026, 10, 12)
    small_dataset(root, sessions=(DAY, last_day))  # second day has no archive
    assert cli.main(["replay", "--data-root", str(root), "--start", DAY.isoformat(),
                     "--end", last_day.isoformat(), "--output", str(output)]) == 2
    assert_error(capsys, "REPLAY_DATA_UNAVAILABLE")
    assert (output / "FAILED").read_text() == "REPLAY_RANGE_INCOMPLETE\n"
    assert (output / DAY.isoformat() / "summary.json").is_file()
    assert len(list((output / DAY.isoformat() / "snapshots").glob("*.json"))) == 242
    assert not (output / last_day.isoformat()).exists()


@pytest.mark.parametrize("bar_changes,receipt_changes,code", [
    ({"instrument_id": "600000.SH", "symbol": "600000"}, {}, "INSTRUMENT_TYPE_MISMATCH"),
    ({"asset_type": "STOCK"}, {}, "INSTRUMENT_TYPE_MISMATCH"),
    ({"price_adjustment": "QFQ"}, {}, "RAW_MINUTE_REQUIRED"),
    # Synthetic contamination is confined to this TEST_FIXTURE directory;
    # the intentionally foreign provenance is never presented as real data.
    ({"pit_status": "RESEARCH_ONLY"}, {"pit_status": "RESEARCH_ONLY"}, "DATA_PROVENANCE_MIXED"),
])
def test_doctor_rejects_archive_outside_declared_contract(tmp_path, capsys, bar_changes, receipt_changes, code):
    root = tmp_path / "TEST_FIXTURE_invalid_archive"
    small_dataset(root, bar_changes=bar_changes, receipt_changes=receipt_changes)
    original = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert cli.main(["doctor", "--data-root", str(root)]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report["historical_data_coverage"]["row_count"] == 1
    assert report["replay_ready"] is False
    assert code in report["errors"]
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == original


@pytest.mark.parametrize("timeout", ["nan", "inf", "0", "-1"])
def test_replay_timeout_must_be_finite_positive_before_output(tmp_path, capsys, timeout):
    root = tmp_path / "TEST_FIXTURE_data"
    output = tmp_path / "TEST_FIXTURE_output"
    small_dataset(root)
    original = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert cli.main(["replay", "--data-root", str(root), "--date", DAY.isoformat(),
                     "--output", str(output), "--timeout=" + timeout]) == 2
    assert_error(capsys, "INVALID_PROVIDER_TIMEOUT")
    assert not output.exists()
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == original


def test_successful_two_day_range_uses_real_engine_with_isolated_daily_state(tmp_path, capsys):
    root = tmp_path / "TEST_FIXTURE_two_day_data"
    output = tmp_path / "TEST_FIXTURE_two_day_output"
    last_day = date(2026, 10, 12)
    calendar, scope = small_dataset(root, sessions=(DAY, last_day))
    event = datetime.combine(last_day, at(9, 31).timetz())
    raw = b"TEST_FIXTURE_CLI_SECOND_DAY_SINGLE_MINUTE"
    source_id = hashlib.sha256(raw).hexdigest()
    receipt = SourceReceipt(source_id=source_id, provider="TEST_FIXTURE",
        source_timestamp=event, source_timestamp_basis="PUBLISHED", received_at=event,
        available_at=event, pit_status="TEST_FIXTURE")
    second_bar = MinuteBar(instrument_id="000001.SH", symbol="000001", exchange="SH", asset_type="INDEX",
        timestamp=event, open=20., high=20.2, low=19.9, close=20.1, volume=2000., turnover=40200.,
        provider=receipt.provider, source_timestamp=event, source_timestamp_basis="PUBLISHED",
        available_at=event, source_id=source_id, quality="VALID", pit_status="TEST_FIXTURE")
    ParquetStore(root).append([second_bar], receipt, raw)
    original = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert cli.main(["replay", "--data-root", str(root), "--start", DAY.isoformat(),
                     "--end", last_day.isoformat(), "--output", str(output)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["output"] == str(output.resolve())
    assert [row["date"] for row in result["results"]] == [DAY.isoformat(), last_day.isoformat()]
    assert {path.name for path in output.iterdir()} == {DAY.isoformat(), last_day.isoformat()}
    config_hashes = set()
    first_sources = set()
    second_sources = set()
    for day, target_sources in ((DAY, first_sources), (last_day, second_sources)):
        day_output = output / day.isoformat()
        files = sorted((day_output / "snapshots").glob("*.json"))
        assert len(files) == 242
        records = [json.loads(path.read_text(encoding="utf-8")) for path in files]
        assert len({record["as_of"] for record in records}) == 242
        assert all(record["as_of"].startswith(day.isoformat()) for record in records)
        assert all(record["scope"] == "DECLARED_SCOPE_ONLY" for record in records)
        assert all(record["pit_status"] == "TEST_FIXTURE" for record in records)
        config_hashes.update(record["config_hash"] for record in records)
        first = records[0]
        assert first["as_of"].endswith("09:30:00+08:00")
        assert first["state"]["bar_count"] == 0
        assert first["state"]["last_event_time"] is None
        assert first["data_cutoff"] is None
        assert first["received_bars"] == []
        received = [bar for record in records for bar in record["received_bars"]]
        assert len(received) == 1
        assert received[0]["timestamp"] == day.isoformat() + "T09:31:00+08:00"
        target_sources.add(received[0]["source_id"])
        summary = json.loads((day_output / "summary.json").read_text(encoding="utf-8"))
        assert summary["date"] == day.isoformat()
        assert summary["ticks"] == 242 and summary["failed_cycles"] == 0
        assert summary["final_state"]["bar_count"] == 1
        assert summary["final_state"]["gap_count"] == 239
        assert summary["final_state"]["quality"] == "GAPPED"
        assert summary["final_state"]["last_event_time"] == received[0]["timestamp"]
        assert not (day_output / "FAILED").exists()
    assert len(config_hashes) == 1
    assert first_sources.isdisjoint(second_sources)
    assert scope.calendar_version == calendar.version
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == original
