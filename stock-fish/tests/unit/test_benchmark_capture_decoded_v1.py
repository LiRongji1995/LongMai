"""Read-only, whole-summary TEST_FIXTURE benchmark and failure contracts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import sqlite3

import pytest

from scripts import benchmark_capture_decoded_v1 as bench
from stockfish_core.yaogu import premarket_capture_cache_v1 as capture
from tests.yaogu.test_premarket_capture_cache_v1 import FixtureBudget, FixtureExecutor


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("TEST_FIXTURE_NETWORK_DATABASE_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)


def snapshot(root):
    return {str(path): (bench.workflow.fingerprint(path), hashlib.sha256(path.read_bytes()).hexdigest())
            for path in Path(root).rglob("*") if path.is_file()}


@pytest.fixture
def original(tmp_path):
    state = tmp_path / "TEST_FIXTURE_original_state"
    executor = FixtureExecutor()
    cache = capture.CaptureCache(state / "cache", FixtureBudget(),
        config={"allow_test_fixture": True}, executor=executor)
    records = [cache.capture_one(capture.RequestSpec("market_daily", {"date": day}, day)).to_dict()
               for day in ("2026-10-14", "2026-10-15")]
    (state / "premarket.lock").touch()
    summary = dict(schema_version=capture.COLLECTION_VERSION, core_records=records,
        coverage_type="TEST_FIXTURE", observation_date="2026-10-15", target_session="2026-10-16",
        completeness="TEST_FIXTURE_PARTIAL")
    config = dict(schema_version=bench.workflow.VERSION, state_root=str(state),
        capture={"allow_test_fixture": True}, limits={"max_seconds": 240}, seed_sources=[])
    summary_path, configuration = state / "capture-summary.json", state / "configuration.json"
    summary_path.write_bytes(bench.json_bytes(summary))
    configuration.write_bytes(bench.json_bytes(config))
    return dict(state=state, cache=cache, executor=executor, records=records, summary=summary,
        summary_path=summary_path, configuration=configuration, config=config)


@pytest.mark.parametrize("variant", ["reference", "candidate"])
def test_constructor_verification_and_terminal_leave_all_original_files_unchanged(original, variant):
    before = snapshot(original["state"])
    reader = bench.readonly_cache(original["cache"].root, FixtureBudget(),
        {"allow_test_fixture": True}, variant)
    for record in original["records"]:
        assert reader.verify(record)["status"] == "COMPLETE"
    assert reader.terminal()
    for operation in (reader.capture_one, reader.import_seed, reader._save, reader._commit,
                      reader._writer, reader.executor):
        with pytest.raises(ValueError, match="READONLY_CACHE_WRITE_OR_NETWORK_FORBIDDEN"):
            operation(None)
    assert snapshot(original["state"]) == before
    assert len(original["executor"].calls) == 2


def test_missing_cache_directory_is_not_created(original):
    locators = original["cache"].root / "locators"
    for path in locators.iterdir():
        path.unlink()
    locators.rmdir()
    with pytest.raises(ValueError, match="EXISTING_CACHE_REQUIRED"):
        bench.readonly_cache(original["cache"].root, FixtureBudget(), {}, "reference")
    assert not locators.exists()


@pytest.mark.parametrize("change,reason", [
    ({"seed_sources": ["TEST_FIXTURE"]}, "SEED_IMPORT_FORBIDDEN"),
    ({"limits": {"max_seconds": None}}, "FINITE_ORIGINAL_TIME_LIMIT_REQUIRED"),
    ({"schema_version": "INVALID"}, "ORIGINAL_CONFIGURATION_SCHEMA"),
    ({"unexpected": True}, "ORIGINAL_CONFIGURATION_SCHEMA"),
    ({"capture": {}}, "EXPLICIT_TEST_FIXTURE_OR_REAL_CONFIGURATION_REQUIRED"),
])
def test_original_config_rejects_unsafe_or_unbounded_mode(original, change, reason):
    with pytest.raises(ValueError, match=reason):
        bench._configuration({**original["config"], **change}, True)


def test_four_arms_cannot_expand_original_budgets(original):
    config = bench._configuration(original["config"], True)
    limits = bench._arm_limits(config, 900)
    for key in ("max_seconds", "capture_read_bytes", "output_bytes"):
        assert limits[key] * 4 <= config["limits"][key]
    assert limits["max_seconds"] == 60
    assert config["capture"]["offline"] and not config["capture"]["retry_failed"]


@pytest.mark.parametrize("corruption", ["count", "duplicate", "coverage"])
def test_all_original_records_required_without_sampling(original, corruption):
    summary = deepcopy(original["summary"])
    if corruption == "count":
        summary["core_records"].pop()
    elif corruption == "duplicate":
        summary["core_records"][1] = summary["core_records"][0]
    else:
        summary["coverage_type"] = "RECONSTRUCTED_HISTORICAL"
    with pytest.raises(ValueError):
        bench._records(summary, 2, True)


def test_four_real_fresh_fixture_processes_full_equality_and_original_unchanged(original, tmp_path):
    before = snapshot(original["state"])
    output = tmp_path / "TEST_FIXTURE_ABBA"
    result = bench.benchmark(original["configuration"], original["summary_path"], output,
        expected_record_count=2, arm_seconds=60, test_fixture=True)
    assert result["status"] == "COMPLETE", result
    assert result["comparison"]["comparable"]
    assert result["order"] == list(bench.ORDER) and not result["unrun_arms"]
    assert not result["sample_filter_applied"] and result["original_summary_record_count"] == 2
    assert not result["full_premarket_speedup_established"]
    assert not result["network_speedup_established"]
    assert snapshot(original["state"]) == before
    assert len(original["executor"].calls) == 2
    proofs = []
    for index, row in enumerate(result["results"], 1):
        assert row["external_exit_code"] == 0 and row["external_worker_reaped"]
        assert row["sampled_peak_tree_rss_bytes"] > 0
        measurement = json.loads(Path(row["measurement"]["path"]).read_bytes())
        assert len(measurement["record_results"]) == 2
        assert measurement["terminal_wall_seconds"] > 0
        assert measurement["comparison_proof"]["source_returned_read_bytes"] > 0
        assert all(value == 0 for value in measurement["metrics"]["calls"].values())
        if row["variant"] == "candidate":
            assert row["decode_metrics"]["native_json_reuses"] == 4
            assert row["decode_metrics"]["fresh_native_reads"] == 8
        proofs.append(row["comparison_proof"])
    assert proofs.count(proofs[0]) == 4
    published = json.loads((output / "summary.json").read_bytes())
    assert published["status"] == "COMPLETE"


def test_source_failure_stops_after_first_worker_and_never_retries(original, tmp_path):
    raw = Path(original["records"][0]["raw"]["path"])
    raw.write_bytes(raw.read_bytes() + b" ")
    before = snapshot(original["state"])
    result = bench.benchmark(original["configuration"], original["summary_path"],
        tmp_path / "TEST_FIXTURE_failed", expected_record_count=2, arm_seconds=60, test_fixture=True)
    assert result["status"] == "FAILED" and len(result["results"]) == 1
    assert result["unrun_arms"] == list(bench.ORDER[1:])
    assert result["results"][0]["external_exit_code"] != 0
    assert result["results"][0]["external_worker_reaped"]
    assert result["results"][0]["comparison_proof"] is None
    assert not result["comparison"]["comparable"]
    assert snapshot(original["state"]) == before


@pytest.mark.parametrize("field", ["all_results_sha256", "records_metadata_sha256",
    "authenticated_sources_sha256", "source_returned_read_bytes", "summary", "configuration"])
def test_any_complete_proof_mismatch_forbids_performance_claim(field):
    proof = {key: "TEST_FIXTURE_IDENTICAL" for key in ("all_results_sha256", "records_metadata_sha256",
        "authenticated_sources_sha256", "source_returned_read_bytes", "summary", "configuration")}
    rows = [dict(variant=variant, status="COMPLETE", external_exit_code=0,
        external_worker_reaped=True, comparison_proof=deepcopy(proof)) for variant in bench.ORDER]
    rows[1]["comparison_proof"][field] = "TEST_FIXTURE_CHANGED"
    assert bench.compare(rows) == dict(comparable=False,
        reason="FULL_RESULTS_METADATA_SOURCES_OR_READ_BYTES_DIFFER")


def test_absent_original_lock_is_not_created(original, tmp_path):
    path = original["state"] / "premarket.lock"
    path.unlink()
    result = bench.benchmark(original["configuration"], original["summary_path"],
        tmp_path / "TEST_FIXTURE_missing_lock", expected_record_count=2, test_fixture=True)
    assert result["status"] == "FAILED" and result["results"] == []
    assert result["comparison"] is None and not path.exists()
    assert "EXISTING_ORIGINAL_LOCK_REQUIRED" in result["error"]["reason"]


def test_output_cannot_enter_original_state(original):
    output = original["state"] / "TEST_FIXTURE_forbidden_output"
    with pytest.raises(ValueError, match="PRIVATE_OUTPUT_SEPARATE_FROM_SOURCE_STATE"):
        bench.benchmark(original["configuration"], original["summary_path"], output,
            expected_record_count=2, test_fixture=True)
    assert not output.exists()


@pytest.mark.parametrize("publication", ["parent-result.json", "summary.json"])
def test_publication_window_source_change_cannot_leave_complete_claim(original, tmp_path, monkeypatch, publication):
    publish = bench._publish
    triggered = []
    raw = Path(original["records"][0]["raw"]["path"])

    def late_change(path, identity, value):
        publish(path, identity, value)
        if Path(path).name == publication and value.get("status") == "COMPLETE" and not triggered:
            triggered.append(True)
            raw.write_bytes(raw.read_bytes() + b" ")

    monkeypatch.setattr(bench, "_publish", late_change)
    output = tmp_path / ("TEST_FIXTURE_late_" + publication.replace(".", "_"))
    result = bench.benchmark(original["configuration"], original["summary_path"], output,
        expected_record_count=2, arm_seconds=60, test_fixture=True)
    assert triggered and result["status"] == "FAILED"
    published = json.loads((output / "summary.json").read_bytes())
    assert published["status"] == "FAILED"
    assert published["comparison"] is None or not published["comparison"]["comparable"]
    if publication == "parent-result.json":
        assert len(result["results"]) == 1
        assert result["results"][0]["comparison_proof"] is None
        parent = json.loads((output / "01-reference" / publication).read_bytes())
        assert parent["status"] == "FAILED" and parent["comparison_proof"] is None
    else:
        assert result["comparison"] is None and len(result["results"]) == 4
