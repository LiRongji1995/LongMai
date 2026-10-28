"""Isolated controls for the explicit real-data benchmark; no real sources."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import gzip
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from scripts import benchmark_real_premarket_v1 as bench
from stockfish_core.yaogu import premarket_news_v1 as news
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from stockfish_core.yaogu.premarket_capture_cache_v1 import CaptureCache, RequestSpec
from stockfish_core.yaogu.premarket_runtime_v1 import Budget


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("TEST_FIXTURE_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def config(tmp_path):
    state = tmp_path / "TEST_FIXTURE_state"
    for name in ("index", "raw", "raw-index", "locators"):
        (state / "cache" / name).mkdir(parents=True)
    return workflow.load_config(overrides=dict(state_root=str(state),
        limits={"max_seconds": 30}, capture={"offline": False, "retry_failed": True}))


def test_offline_profile_preserves_limits_and_input(config):
    previous = deepcopy(config)
    result = bench._offline_configuration(config)
    assert config == previous
    assert result["limits"] == previous["limits"]
    assert result["capture"]["offline"] is True
    assert result["capture"]["retry_failed"] is False
    assert result["news"]["offline"] is True


@pytest.mark.parametrize("change,reason", [
    ({"capture": {"allow_test_fixture": True}}, "REAL_SOURCES_REQUIRED"),
    ({"seed_sources": ["TEST_FIXTURE_source.json"]}, "SEED_IMPORT_FORBIDDEN"),
    ({"limits": {"max_seconds": None}}, "FINITE_TIME_LIMIT"),
])
def test_unsafe_or_unbounded_profiles_refuse(config, change, reason):
    config.update(change)
    with pytest.raises(ValueError, match=reason):
        bench._offline_configuration(config)


def test_native_offline_missing_capture_never_reserves_or_executes(config):
    actual = bench._offline_configuration(config)
    called = []
    budget = Budget(actual["limits"], cache_dir=Path(actual["state_root"]) / "cache")
    cache = CaptureCache(Path(actual["state_root"]) / "cache", budget,
        config=actual["capture"], executor=lambda *args, **kwargs: called.append(True))
    spec = RequestSpec("calendar", dict(start_date="2026-01-01", end_date="2026-12-31"),
                       "2026-10-26", role="CALENDAR")
    with pytest.raises(ValueError, match="OFFLINE_SOURCE_MISSING_OR_FAILED_NO_NETWORK"):
        cache.capture_one(spec)
    assert called == []
    assert all(value == 0 for value in budget.metrics["calls"].values())
    assert not list((cache.root / "raw").iterdir())
    assert not list((cache.root / "index").iterdir())


def test_native_news_offline_does_not_construct_opener(config, tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError("TEST_FIXTURE_NETWORK_OPENER_FORBIDDEN")
    monkeypatch.setattr(news, "_make_opener", forbidden)
    actual = bench._offline_configuration(config)
    job = tmp_path / "TEST_FIXTURE_news_job"
    job.mkdir()
    budget = Budget(actual["limits"], job_dir=job)
    result = news.collect_reported_news(core_run={"observation_date": "2026-10-26",
        "run_id": "TEST_FIXTURE_news", "target_session": "2026-10-27",
        "strategy_id": bench.STRATEGY, "results": {bench.STRATEGY: {"items": []}}},
        output_dir=job / "news", budget=budget, config=actual["news"])
    assert result["summary"]["status"] == "OFFLINE_NOT_COLLECTED_UNKNOWN"
    assert all(value == 0 for value in budget.metrics["calls"].values())


@pytest.mark.parametrize("engine", bench.ENGINES)
def test_phase_instrumentation_is_local_and_keeps_native_budget(engine, config, tmp_path):
    before = Budget.phase
    stages = []
    function = bench._instrumented(engine, stages)
    factory = function.__globals__["Budget" if engine == "original-ref" else "_workflow_budget"]
    job = tmp_path / ("TEST_FIXTURE_" + engine)
    job.mkdir()
    budget = factory(config["limits"], job_dir=job)
    assert type(budget) is Budget
    assert budget.limits == config["limits"]
    with budget.phase("capture"):
        budget.charge_read(7)
    assert Budget.phase is before
    assert stages[0]["phase"] == "capture"
    assert stages[0]["returned_read_bytes"]["capture"] >= 7
    assert stages[0]["wall_seconds"] >= 0
    assert stages[0]["cpu_seconds"] >= 0
    assert stages[0]["process_peak_rss_bytes_at_end"] > 0


def measurement(engine, seconds=10):
    return dict(engine=engine, status="COMPLETED_WITH_UNKNOWNS", workflow_wall_seconds=seconds,
        job_output_bytes=100, comparison_proof={"run_id": "TEST_FIXTURE_"+engine,
            "decision_cutoff": "TEST_FIXTURE_ACTUAL_CLOCK_"+engine,
            "observation_date": "2026-10-26", "target_session": "2026-10-27",
            "hashes": {key: "a"*64 for key in bench.COMPARISON_KEYS}})


def test_distinct_run_ids_and_clocks_do_not_become_claimed_byte_equality():
    result = bench.compare(measurement("original-ref"), measurement("original", 8))
    assert result["comparable"] is True
    assert result["worker_wall_speedup_percent"] == 20
    assert result["formal_export_byte_equality_claimed"] is False


@pytest.mark.parametrize("difference", ["capture_sources", "market_input", "core_numeric_leaves", "theme_values"])
def test_source_or_numeric_difference_blocks_speed_claim(difference):
    candidate = measurement("shared", 1)
    candidate["comparison_proof"]["hashes"][difference] = "b"*64
    result = bench.compare(measurement("original-ref"), candidate)
    assert result["comparable"] is False
    assert "worker_wall_speedup_percent" not in result
    assert result["reasons"] == ["DIFF:"+difference]


def test_missing_proof_fields_cannot_match_each_other():
    first, second = measurement("original-ref"), measurement("original")
    for row in (first, second):
        row["comparison_proof"]["hashes"].pop("theme_values")
    assert bench.compare(first, second)["comparable"] is False


def test_csv_only_removes_actual_run_id_and_numeric_nulls_stay():
    first = bench._csv_values(b"run_id,instrument_id,A,status\nx,600001.SH,,UNKNOWN\n", "x")
    second = bench._csv_values(b"run_id,instrument_id,A,status\ny,600001.SH,,UNKNOWN\n", "y")
    assert first == second
    assert first["rows"][0]["A"] == ""
    assert bench._numeric_leaves({"numerator": "1", "denominator": "3", "missing": None}) != \
           bench._numeric_leaves({"numerator": "1", "denominator": "3", "missing": 0})
    with pytest.raises(ValueError, match="FORMAL_CSV_RUN_OR_ROW_CHANGED"):
        bench._csv_values(b"run_id,instrument_id\nx,600001.SH\n", "wrong")


def test_fresh_process_empty_cache_stops_without_engine_retry(config, tmp_path):
    local = datetime.now(timezone.utc).astimezone(workflow.SH).date()
    observed, target = str(local-timedelta(days=1)), str(local+timedelta(days=1))
    config_path = tmp_path / "TEST_FIXTURE_config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output = tmp_path / "TEST_FIXTURE_benchmark"
    completed = subprocess.run([sys.executable, "-I", "-B", "-X",
        "pycache_prefix="+str(tmp_path / "TEST_FIXTURE_absent_bytecode"), bench.__file__, "run",
        "--config", str(config_path), "--output", str(output),
        "--observation-date", observed, "--target-session", target],
        capture_output=True, text=True, timeout=45, check=False)
    assert completed.returncode == 4, completed.stderr
    summary = json.loads((output / "summary.json").read_text())
    assert summary["unrun_engines"] == ["original", "shared"]
    assert len(summary["results"]) == 1
    row = summary["results"][0]
    assert row["status"] == "FAILED"
    assert row["external_exit_code"] == 4
    assert row["comparison_proof"] is None
    assert all(value == 0 for value in row["metrics"]["calls"].values())
    assert not (output / "original").exists()
    assert not list((Path(config["state_root"]) / "cache/raw").iterdir())
    assert json.loads((output / "experiment.json").read_text())["configuration"]["capture"]["offline"] is True
    with pytest.raises(ValueError, match="NEW_UNALIASED_OWNED_OUTPUT_REQUIRED"):
        bench.benchmark(config, output, ["original-ref"], observed, target)


@pytest.mark.parametrize("fault", ["reserve", "process-output", "receipt-output", "process-time",
                                  "receipt-time", "last-rename-time", None])
def test_parent_receipt_limits_fail_closed_and_stop_next_arm(config, tmp_path, monkeypatch, fault):
    """A successful worker is insufficient when its parent publication exceeds quota."""
    config["limits"].update(output_bytes=65536, max_seconds=30)
    output = tmp_path / "TEST_FIXTURE_parent_receipts"
    started, clock = [], [100.0]
    save, rename = bench._save, Path.rename
    monkeypatch.setattr(bench.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(bench.original, "_owned_group_members", lambda pid: ())

    class CompletedFixtureProcess:
        pid, returncode = 999999, 0

        def __init__(self, command, **kwargs):
            engine = command[command.index("--engine")+1]
            directory = Path(command[command.index("--directory")+1])
            started.append(engine)
            row = dict(measurement(engine), schema_version=bench.VERSION)
            if fault == "reserve":
                # Worker output fits; two complete parent copies do not.
                row["test_fixture_evidence"] = "x"*20000
            save(directory / "measurement.json", row)

        def poll(self):
            return 0

        def wait(self):
            return 0

    def publication(path, value, **kwargs):
        save(path, value, **kwargs)
        stage = ("process" if path.name == "process-result.candidate.json" else
                 "receipt" if path.name.endswith("-receipt.candidate.json") else None)
        if fault == str(stage)+"-output":
            # Simulate the parent's actual serialized write exceeding its reservation.
            with path.open("ab") as handle:
                handle.write(b" "*config["limits"]["output_bytes"])
        if fault == str(stage)+"-time":
            clock[0] += config["limits"]["max_seconds"]+1

    def publication_rename(path, target):
        result = rename(path, target)
        if fault == "last-rename-time" and path.name.endswith("-receipt.candidate.json"):
            clock[0] += config["limits"]["max_seconds"]+1
        return result

    monkeypatch.setattr(bench.subprocess, "Popen", CompletedFixtureProcess)
    monkeypatch.setattr(bench, "_save", publication)
    monkeypatch.setattr(Path, "rename", publication_rename)
    local = datetime.now(timezone.utc).astimezone(workflow.SH).date()
    result = bench.benchmark(config, output, ["original-ref", "original"],
        str(local-timedelta(days=1)), str(local+timedelta(days=1)))
    first = result["results"][0]
    process_path = output / "original-ref/process-result.json"
    receipt_path = output / "original-ref-receipt.json"
    assert json.loads(process_path.read_text()) == first
    assert json.loads(receipt_path.read_text()) == first
    assert json.loads((output / "summary.json").read_text()) == result
    if fault is None:
        assert started == ["original-ref", "original"]
        assert first["status"] in bench.SUCCESS and first["comparison_proof"] is not None
        assert result["comparisons"][0]["comparable"] is True
        assert result["unrun_engines"] == []
        for engine in started:
            assert bench._arm_output_bytes(output / engine, engine) <= config["limits"]["output_bytes"]
    else:
        assert started == ["original-ref"]
        assert not (output / "original").exists()
        assert result["unrun_engines"] == ["original"]
        assert first["status"] == "FAILED" and first["comparison_proof"] is None
        assert first["quota_enlarged"] is False and first["worker_measurement_is_final_result"] is False
        assert first["parent_failure"]["reason"].endswith(
            "PARENT_RECEIPT_OUTPUT_LIMIT" if fault in ("reserve", "process-output", "receipt-output")
            else "PARENT_RECEIPT_TIME_LIMIT")
        # The actual worker evidence and all rejected positive bytes survive.
        worker = json.loads((output / "original-ref/measurement.json").read_text())
        assert worker["comparison_proof"] is not None
        assert process_path.stat().st_size == receipt_path.stat().st_size == bench.FAILURE_RECEIPT_BYTES
        assert not list(output.rglob("*.candidate.json"))
        if fault == "reserve":
            assert not list(output.rglob("*.rejected.json"))
            assert bench._arm_output_bytes(output / "original-ref", "original-ref") <= config["limits"]["output_bytes"]
        else:
            rejected = list(output.rglob("*.rejected.json"))
            assert rejected and all(json.loads(path.read_text())["comparison_proof"] is not None for path in rejected)
            if fault.endswith("output"):
                assert max(path.stat().st_size for path in rejected) > config["limits"]["output_bytes"]


@pytest.mark.parametrize("suffix", [".json", ".candidate.json"])
def test_parent_publication_never_moves_existing_receipt(config, tmp_path, suffix):
    directory = tmp_path / "TEST_FIXTURE_owned_arm"
    directory.mkdir()
    receipt = bench._receipt_paths(directory, "original-ref")[0].with_suffix(suffix)
    original = b'{"status":"TEST_FIXTURE_EXISTING_USER_FILE"}'
    receipt.write_bytes(original)
    before = receipt.stat()
    with pytest.raises(ValueError, match="PARENT_RECEIPT_DESTINATION_EXISTS"):
        bench._publish_process_result(config, directory, "original-ref", bench.time.perf_counter(),
            measurement("original-ref"))
    assert receipt.read_bytes() == original
    assert receipt.stat().st_ino == before.st_ino
    assert not list(tmp_path.rglob("*.rejected.json"))


def test_interrupt_after_publication_retracts_positive_receipts(config, tmp_path, monkeypatch):
    directory = tmp_path / "TEST_FIXTURE_interrupted_arm"
    directory.mkdir()
    engine, started = "original-ref", bench.time.perf_counter()
    bench._reserve_failure_receipts(config, directory, engine, started)
    rename = Path.rename
    def interrupt(path, target):
        result = rename(path, target)
        if path.name.endswith("-receipt.candidate.json"):
            raise KeyboardInterrupt("TEST_FIXTURE_PUBLICATION_INTERRUPTED")
        return result
    monkeypatch.setattr(Path, "rename", interrupt)
    with pytest.raises(KeyboardInterrupt, match="TEST_FIXTURE_PUBLICATION_INTERRUPTED"):
        bench._publish_process_result(config, directory, engine, started, measurement(engine))
    for path in bench._receipt_paths(directory, engine):
        failed = json.loads(path.read_text())
        assert failed["status"] == "FAILED" and failed["comparison_proof"] is None
        assert json.loads(path.with_suffix(".rejected.json").read_text())["comparison_proof"] is not None


def test_failure_reservation_must_fit_before_child_starts(config, tmp_path, monkeypatch):
    directory = tmp_path / "TEST_FIXTURE_reserve_refusal"
    directory.mkdir()
    config["limits"]["output_bytes"] = bench.FAILURE_RECEIPT_BYTES
    def forbidden(*args, **kwargs):
        raise AssertionError("TEST_FIXTURE_CHILD_MUST_NOT_START")
    monkeypatch.setattr(bench.subprocess, "Popen", forbidden)
    with pytest.raises(ValueError, match="PARENT_RECEIPT_OUTPUT_LIMIT"):
        bench._run_process(config, directory, "original-ref", ("2026-10-26", "2026-10-27", "2026-10-27"))
    assert not list(tmp_path.rglob("*.failure-reserve.json"))
    assert not (directory / "process-result.json").exists()
    assert bench._arm_output_bytes(directory, "original-ref") <= config["limits"]["output_bytes"]


def test_original_canonical_cutoff_changes_only_proven_envelope_projection():
    """Actual native TEST_FIXTURE outputs at two explicit historical cutoffs.

    No datetime module or process clock is patched. These are isolated fixture
    calculations, not claims of real captured market data or production success.
    """
    from stockfish_core.yaogu import premarket_native_core_v1 as core
    from stockfish_core.yaogu import premarket_theme_context_v1 as theme
    from tests.yaogu.test_premarket_native_core_v1 import FixtureBudget
    from tests.yaogu.test_premarket_workflow_v1 import WorkflowFixtureTests

    fixture = WorkflowFixtureTests(methodName="runTest")
    fixture.setUp()
    try:
        adapters, cutoff = fixture._adapters()
        collection = adapters["collect"]()
        sources = adapters["theme_sources"]()
        documents, projections, themes, csv_rows, numbers = [], [], [], [], []
        for index in range(2):
            instant = cutoff + timedelta(seconds=index)
            budget = FixtureBudget()
            owner = core.run_core(captures=[record.to_dict() for record in collection.core_records],
                observation_date=collection.observation_date, target_session=collection.target_session,
                cutoff=instant, output_dir=fixture.root / ("TEST_FIXTURE_core_"+str(index)),
                budget=budget, coverage_type="TEST_FIXTURE")
            run = owner.run
            artifacts = owner.artifacts
            market = json.loads(gzip.decompress(Path(artifacts["input.json.gz"]["path"]).read_bytes()))
            manifest = json.loads(Path(artifacts["manifest.json"]["path"]).read_bytes())
            projection, metadata = bench._market_projection(market, run, manifest)
            assert run["coverage_type"] == market["coverage_type"] == "TEST_FIXTURE"
            assert "input_id" not in market and "data_cutoff" not in market
            assert run["input_id"] == "market_"+run["input_hash"]
            assert set(metadata["excluded_comparison_values"]) == {
                "/universe/"+key for key in bench.ASSEMBLY_CLOCKS}
            assert metadata["source_or_security_or_price_or_theme_clocks_removed"] is False
            assert metadata["full_market_input_sha256"] == run["input_hash"]
            documents.append((market, run, manifest))
            projections.append(projection)
            csv_rows.append({name: bench._csv_values(Path(artifacts[name]["path"]).read_bytes(), run["run_id"])
                             for name in bench.CSV_NAMES})
            numbers.append(bench._numeric_leaves(run["results"][bench.STRATEGY]["items"]))
            context = theme.attach_theme_context(owner,
                context_captures=sources.context_captures,
                raw_receipt_sources=sources.raw_receipt_sources,
                context_cutoff=instant+timedelta(minutes=1),
                output_dir=fixture.root / ("TEST_FIXTURE_theme_"+str(index)), budget=budget,
                requested_industry_nodes=sources.requested_industry_nodes,
                requested_concept_symbols=sources.requested_concept_symbols,
                parent_acquisition=sources.parent_acquisition)
            context.validate()
            parent = json.loads(gzip.decompress(Path(
                context.artifacts["native-theme-parent.json.gz"]["path"]).read_bytes()))
            assert parent["source_input_hash"] == run["input_hash"]
            assert parent["observation_date"] == run["observation_date"]
            assert bench._clock(parent["decision_cutoff"]) >= bench._clock(run["decision_cutoff"])
            assert parent["effective_input_hash"] and parent["context_content_hash"]
            themes.append(parent["items"])
        assert documents[0][1]["run_id"] != documents[1][1]["run_id"]
        assert documents[0][1]["input_hash"] != documents[1][1]["input_hash"]
        assert documents[0][0] != documents[1][0]
        assert projections[0] == projections[1]
        assert csv_rows[0] == csv_rows[1]
        assert numbers[0] == numbers[1]
        assert themes[0] == themes[1] and themes[0]

        market, run, manifest = documents[0]
        original_market = deepcopy(market)
        def rebound(changed):
            copied_run, copied_manifest = deepcopy(run), deepcopy(manifest)
            sha = bench._digest(changed)
            copied_run.update(input_hash=sha, input_id="market_"+sha)
            copied_manifest["input_hash"] = sha
            return copied_run, copied_manifest
        # Source/security/business clocks and actual values are never stripped.
        changes = (
            ("bars", "close", "10.123"),
            ("bars", "first_seen_at", "2026-09-30T12:00:01Z"),
            ("receipts", "ingested_at", "2026-09-30T12:00:01Z"),
        )
        for section, field, value in changes:
            changed = deepcopy(market)
            changed[section][0][field] = value
            changed_run, changed_manifest = rebound(changed)
            different, _ = bench._market_projection(changed, changed_run, changed_manifest)
            assert different != projections[0]
        changed = deepcopy(market)
        changed["universe"]["securities"][0]["first_seen_at"] = "2026-09-30T12:00:01Z"
        changed_run, changed_manifest = rebound(changed)
        assert bench._market_projection(changed, changed_run, changed_manifest)[0] != projections[0]
        changed = deepcopy(market)
        changed["universe"]["first_seen_at"] = "2026-09-30T12:00:01Z"
        changed_run, changed_manifest = rebound(changed)
        with pytest.raises(ValueError, match="UNIVERSE_ENVELOPE_CLOCK_NOT_ACTUAL_ASSEMBLY_CUTOFF"):
            bench._market_projection(changed, changed_run, changed_manifest)
        changed = deepcopy(market)
        changed["input_id"] = "TEST_FIXTURE_UNSUPPORTED_ENVELOPE"
        with pytest.raises(ValueError, match="CANONICAL_MARKET_INPUT_SCHEMA_REQUIRED"):
            bench._market_projection(changed, run, manifest)
        changed_manifest = deepcopy(manifest)
        changed_manifest["normalization_report"]["normalization_version"] = "TEST_FIXTURE_UNKNOWN_VERSION"
        with pytest.raises(ValueError, match="ORIGINAL_NORMALIZATION_AND_INPUT_BINDING_REQUIRED"):
            bench._market_projection(market, run, changed_manifest)
        assert market == original_market
    finally:
        fixture.doCleanups()
