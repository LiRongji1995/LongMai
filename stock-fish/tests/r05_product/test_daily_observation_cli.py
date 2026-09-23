"""TEST_FIXTURE: isolated observation CLI contracts; never query a live service."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from urllib import parse

import pytest

from scripts import codex_research as entry


@pytest.fixture
def observation_report():
    return {
        "schema_version": "DAILY_OBSERVATION_V1",
        "report_as_of": "2026-09-23T09:00:00+08:00",
        "target_session": "2026-09-23",
        "requested_days": 20,
        "available_days": 20,
        "calendar_status": "AVAILABLE",
        "current": {
            "coverage_type": "TEST_FIXTURE",
            "execution_status": "READY",
            "run_id": "TEST_FIXTURE_frozen_run",
            "snapshot_id": "TEST_FIXTURE_frozen_snapshot",
            "focus": [{"instrument_id": "600001.SH", "setup": "START", "reason_summary": "原冻结解释"}],
            "watch": [],
        },
        "history": [{"target_session": "2026-09-22", "execution_status": "NOT_AVAILABLE"}],
        "markdown": "# TEST_FIXTURE 每日观察\n\nREADY；原冻结解释。\n",
    }


class ObservationClient:
    """Refuse any payload or endpoint except the single observation GET."""

    def __init__(self, report):
        self.report = deepcopy(report)
        self.calls = []

    def get(self, path, payload=None):
        self.calls.append((path, payload))
        assert payload is None, "Observation must not submit a job or outcome update"
        assert parse.urlsplit(path).path == "/api/daily-research/observation"
        return deepcopy(self.report)


def arguments(*values):
    return entry.parser().parse_args(["daily-observation", *values])


def test_default_reads_once_and_leaves_date_selection_to_server_without_files(observation_report, tmp_path, monkeypatch):
    client = ObservationClient(observation_report)
    monkeypatch.setattr(entry, "ROOT", tmp_path)
    monkeypatch.setattr(entry, "calendar_context", lambda *a, **k: pytest.fail("Must not guess the target locally"))
    result = entry.execute(arguments(), client)
    assert result == observation_report  # No recalc, truncation, renaming or hidden fields.
    assert client.calls == [("/api/daily-research/observation?days=20", None)]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("days", [1, 20])
def test_explicit_historical_query_preserves_date_timezone_and_single_read(observation_report, days):
    client = ObservationClient(observation_report)
    entry.execute(arguments("--days", str(days), "--target-session", "2026-09-22",
                            "--as-of", "2026-09-23T01:00:00Z"), client)
    assert len(client.calls) == 1
    path, payload = client.calls[0]
    assert payload is None
    assert parse.parse_qs(parse.urlsplit(path).query) == {
        "days": [str(days)], "target_session": ["2026-09-22"], "as_of": ["2026-09-23T01:00:00+00:00"]}


@pytest.mark.parametrize("days", ["0", "-1", "21", "1000"])
def test_out_of_range_days_never_contact_service(observation_report, days):
    client = ObservationClient(observation_report)
    with pytest.raises(entry.ResearchError, match="OBSERVATION_DAYS_MUST_BE_1_TO_20"):
        entry.execute(arguments("--days", days), client)
    assert client.calls == []


@pytest.mark.parametrize("values", [
    ["--days", "1.5"], ["--days", "all"], ["--target-session", "2026-02-30"],
    ["--target-session", "20260923"], ["--target-session", "2026-W39-3"],
    ["--target-session", "tomorrow"], ["--update"],
])
def test_invalid_cli_inputs_fail_before_execution(values):
    with pytest.raises(SystemExit) as result:
        arguments(*values)
    assert result.value.code == 2


@pytest.mark.parametrize("value,reason", [
    ("not-a-timestamp", "INVALID_OBSERVATION_CUTOFF"),
    ("2026-09-23", "OBSERVATION_CUTOFF_REQUIRES_TIMEZONE"),
    ("2026-09-23T09:00:00", "OBSERVATION_CUTOFF_REQUIRES_TIMEZONE"),
])
def test_invalid_or_naive_cutoff_never_contacts_service(observation_report, value, reason):
    client = ObservationClient(observation_report)
    with pytest.raises(entry.ResearchError, match=reason):
        entry.execute(arguments("--as-of", value), client)
    assert client.calls == []


def test_explicit_export_preserves_full_server_report_and_hashes(observation_report, tmp_path):
    client = ObservationClient(observation_report)
    output = tmp_path / "private" / "observation"
    result = entry.execute(arguments("--target-session", "2026-09-23", "--output", str(output)), client)
    assert result == {**observation_report, "report_directory": str(output.resolve())}
    assert set(path.name for path in output.iterdir()) == {"report.json", "report.md", "manifest.json"}
    assert json.loads((output / "report.json").read_text(encoding="utf-8")) == observation_report
    assert (output / "report.md").read_bytes() == observation_report["markdown"].encode("utf-8")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"] == {"days": 20, "target_session": "2026-09-23"}
    assert manifest["report_as_of"] == observation_report["report_as_of"]
    assert manifest["api_path"] == client.calls[0][0]
    for name in ("report.json", "report.md"):
        raw = (output / name).read_bytes()
        assert manifest["files"][name] == {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


@pytest.mark.parametrize("existing_file", [False, True])
def test_existing_output_is_preserved(observation_report, tmp_path, existing_file):
    output = tmp_path / "existing"
    if existing_file:
        output.write_bytes(b"original file")
        preserved = output
    else:
        output.mkdir()
        preserved = output / "report.json"
        preserved.write_bytes(b"original snapshot")
    original = preserved.read_bytes()
    with pytest.raises(entry.ResearchError, match="OUTPUT_DIRECTORY_EXISTS_PRESERVED"):
        entry.execute(arguments("--output", str(output)), ObservationClient(observation_report))
    assert preserved.read_bytes() == original
    if not existing_file:
        assert list(output.iterdir()) == [preserved]


@pytest.mark.parametrize("corruption", ["version", "markdown", "current", "history", "metadata"])
def test_invalid_response_creates_no_bundle(observation_report, tmp_path, corruption):
    report = deepcopy(observation_report)
    if corruption == "version":
        report["schema_version"] = "unrelated_schema"
    elif corruption == "metadata":
        del report["report_as_of"]
    else:
        report[corruption] = None
    output = tmp_path / "must-not-exist"
    with pytest.raises(entry.ResearchError, match="INVALID_OBSERVATION_RESPONSE"):
        entry.execute(arguments("--output", str(output)), ObservationClient(report))
    assert not output.exists()


def test_failed_read_is_not_retried_and_creates_no_bundle(tmp_path):
    calls = []

    class UnavailableClient:
        def get(self, path, payload=None):
            calls.append((path, payload))
            raise entry.ResearchError("LOCAL_SERVICE_UNREACHABLE")

    output = tmp_path / "must-not-exist"
    with pytest.raises(entry.ResearchError, match="LOCAL_SERVICE_UNREACHABLE"):
        entry.execute(arguments("--output", str(output)), UnavailableClient())
    assert len(calls) == 1 and calls[0][1] is None
    assert not output.exists()


def test_transport_is_get_only_and_console_redacts_configured_key(observation_report, monkeypatch, capsys):
    key = "TEST_FIXTURE_API_KEY_789"
    report = {**observation_report, "fixture_echo": key}
    calls = []

    class IsolatedOpener:
        def open(self, req, timeout):
            calls.append(req)
            return io.BytesIO(json.dumps(report).encode("utf-8"))

    monkeypatch.setenv("STOCKFISH_API_KEY", key)
    monkeypatch.setattr(entry.request, "build_opener", lambda *handlers: IsolatedOpener())
    assert entry.main(["--base-url", "http://127.0.0.1:8001", "daily-observation"]) == 0
    rendered = capsys.readouterr().out
    assert key not in rendered
    assert json.loads(rendered)["fixture_echo"] == "[REDACTED]"
    assert len(calls) == 1
    assert calls[0].get_method() == "GET" and calls[0].data is None
    assert calls[0].get_header("Authorization") == "Bearer " + key
    assert calls[0].full_url == "http://127.0.0.1:8001/api/daily-research/observation?days=20"


def test_main_invalid_cutoff_uses_controlled_credential_free_error(monkeypatch, capsys):
    monkeypatch.delenv("STOCKFISH_API_KEY", raising=False)
    value = "TEST_FIXTURE_never_echo_invalid_cutoff"
    assert entry.main(["daily-observation", "--as-of", value]) == 2
    output = capsys.readouterr().out
    assert value not in output
    assert json.loads(output) == {"status": "BLOCKED", "reason": "INVALID_OBSERVATION_CUTOFF"}
