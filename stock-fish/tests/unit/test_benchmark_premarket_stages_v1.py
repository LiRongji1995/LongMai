"""Historical-stage controls use only isolated, explicitly labeled TEST_FIXTUREs."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from scripts import benchmark_premarket_stages_v1 as bench
from stockfish_core.yaogu import premarket_workflow_v1 as workflow


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
    (state / "premarket.lock").write_text("TEST_FIXTURE_EXISTING_LOCK\n")
    return workflow.load_config(overrides=dict(state_root=str(state),
        limits={"max_seconds": 30, "output_bytes": 256 * 1024**2},
        capture={"offline": False, "retry_failed": True}))


def _save(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return workflow._ref(path)


def _collection():
    observed = date(2026, 9, 30)
    return dict(schema_version="PREMARKET_CAPTURE_COLLECTION_V1", coverage_type="RECONSTRUCTED_HISTORICAL",
        ready_for_core=True, core_records=[{"provider": "TEST_FIXTURE_SCHEMA_ONLY_NOT_EXECUTABLE"}],
        observation_date=str(observed), target_session="2026-10-01",
        price_sessions=[str(observed-timedelta(days=n)) for n in range(24, -1, -1)])


def test_original_limits_preserved_except_explicit_output_tightening(config):
    saved = deepcopy(config)
    result = bench._configuration(config, 128)
    assert config == saved
    assert result["capture"]["offline"] is True and result["capture"]["retry_failed"] is False
    assert result["news"]["offline"] is True
    assert result["limits"] == {**saved["limits"], "output_bytes": 128*1024**2}
    assert bench._configuration(config)["limits"] == saved["limits"]


@pytest.mark.parametrize("value", (0, -1, 257, True, 1.5))
def test_output_budget_must_be_explicit_integer_reduction(config, value):
    with pytest.raises(ValueError, match="ONLY_TIGHTEN"):
        bench._configuration(config, value)


@pytest.mark.parametrize("field,value", (("max_seconds", 10801), ("max_seconds", None)))
def test_no_unbounded_or_expanded_time_budget(config, field, value):
    config["limits"][field] = value
    with pytest.raises(ValueError):
        bench._configuration(config)


@pytest.mark.parametrize("order", ([], ["reference", "canonical"], ["canonical", "reference"],
    ["reference", "reference"], ["canonical", "canonical"], ["original"]))
def test_only_explicit_single_arm_or_abba(order):
    with pytest.raises(ValueError, match="ABBA"):
        bench._order(order)
    assert bench._order(["reference"]) == ["reference"]
    assert bench._order(["canonical"]) == ["canonical"]
    assert bench._order(["reference", "canonical", "canonical", "reference"])[-1] == "reference"


@pytest.mark.parametrize("fault", ("fixture", "not_ready", "short", "duplicate", "future", "hidden_theme"))
def test_saved_collection_admission_never_relaxes_history_scope(fault):
    value, theme, cutoff = _collection(), None, "2026-10-29T12:00:00+00:00"
    assert bench._collections(value, None, "core", cutoff) == ("2026-09-30", "2026-10-01")
    if fault == "fixture":
        value["coverage_type"] = "TEST_FIXTURE"
    elif fault == "not_ready":
        value["ready_for_core"] = False
    elif fault == "short":
        value["price_sessions"].pop(0)
    elif fault == "duplicate":
        value["price_sessions"][0] = value["price_sessions"][1]
    elif fault == "future":
        value["target_session"] = "2027-01-01"
    else:
        theme = {"TEST_FIXTURE": True}
    with pytest.raises(ValueError):
        bench._collections(value, theme, "core", cutoff)


def _run():
    value = dict(run_id="TEST_FIXTURE", decision_cutoff="2026-10-29T12:00:00+00:00",
        generated_at="2026-10-29T12:00:01+00:00", results={"TEST_FIXTURE":
            {"reason": "UNKNOWN_NOT_FALSE", "amount": "1/3", "source_seen": "2026-09-30T12:00:00Z"}})
    value["content_hash"] = bench._digest(value)
    return value


def test_full_run_preserves_strings_fractions_unknown_and_source_clocks():
    first = _run()
    second = deepcopy(first)
    second["generated_at"] = "2026-10-29T12:00:02+00:00"
    second["content_hash"] = bench._digest({k: v for k, v in second.items() if k != "content_hash"})
    def project(value):
        return bench._run_projection(value, first["decision_cutoff"], first["decision_cutoff"],
                                     "2026-10-29T12:00:03+00:00")
    assert project(first) == project(second)
    for key, new_value in (("reason", "FALSE"), ("amount", "0.333333"), ("source_seen", "2026-09-30T12:00:01Z")):
        changed = deepcopy(second)
        changed["results"]["TEST_FIXTURE"][key] = new_value
        changed["content_hash"] = bench._digest({k: v for k, v in changed.items() if k != "content_hash"})
        assert project(changed) != project(first)
    assert first == _run()


@pytest.mark.parametrize("fault", ("hash", "cutoff", "outside_clock"))
def test_clock_and_canonical_hash_must_be_guarded_before_exclusion(fault):
    value = _run()
    if fault == "hash":
        value["content_hash"] = "0"*64
    elif fault == "cutoff":
        value["decision_cutoff"] = "2026-10-29T12:00:00.000001+00:00"
    else:
        value["generated_at"] = "2026-10-30T12:00:00+00:00"
    with pytest.raises(ValueError):
        bench._run_projection(value, "2026-10-29T12:00:00+00:00", "2026-10-29T12:00:00+00:00",
                              "2026-10-29T12:00:03+00:00")


def test_view_parent_and_whole_hash_are_checked():
    run = _run()
    view = {k: v for k, v in run.items() if k != "content_hash"}
    view["export_view"] = dict(source_content_hash=run["content_hash"], selection="observation")
    view["view_content_hash"] = bench._digest(view)
    result = bench._view_projection(view, run)
    assert result["results"] == run["results"] and result["export_view"] == {"selection": "observation"}
    view["results"] = {"TEST_FIXTURE": "UNAUTHORIZED_CHANGE"}
    with pytest.raises(ValueError, match="CONTENT_HASH"):
        bench._view_projection(view, run)


def _measurement(profile="reference", seconds=10):
    return dict(schema_version=bench.VERSION, status="COMPLETE", engine=profile, stage_wall_seconds=seconds,
        comparison_proof=dict(scope="core", common_actual_cutoff="2026-10-29T12:00:00+00:00",
            observation_date="2026-09-30", target_session="2026-10-01", run_id="TEST_FIXTURE",
            hashes={key: "a"*64 for key in bench.CORE_KEYS}))


@pytest.mark.parametrize("fault", ("failed", "reason", "input", "csv", "missing", "cutoff", "run_id", "scope"))
def test_no_speedup_on_incomplete_or_incomparable_evidence(fault):
    value = _measurement("canonical", 5)
    if fault == "failed":
        value["status"] = "FAILED"
    elif fault in ("reason", "input", "csv"):
        key = {"reason": "whole_strategy_result", "input": "market_input", "csv": "formal_csv"}[fault]
        value["comparison_proof"]["hashes"][key] = "b"*64
    elif fault == "missing":
        del value["comparison_proof"]["hashes"]["normalization_report"]
    else:
        value["comparison_proof"]["common_actual_cutoff" if fault == "cutoff" else fault] = "CHANGED"
    result = bench.compare(_measurement(), value)
    assert result["comparable"] is False and "stage_speed_ratio" not in result
    assert result["end_to_end_speedup_established"] is False


def test_compared_stage_speed_is_explicitly_not_daily_delivery():
    result = bench.compare(_measurement(), _measurement("canonical", 5))
    assert result["stage_latency_reduction_percent"] == 50
    assert result["stage_speed_ratio"] == 2 and result["end_to_end_speedup_established"] is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX readonly native coordination lock")
def test_existing_lock_is_not_rewritten_and_second_owner_refuses(config):
    path = Path(config["state_root"])/"premarket.lock"
    before = workflow._ref(path)
    with bench._readonly_job_lock(config["state_root"]):
        with pytest.raises(BlockingIOError):
            with bench._readonly_job_lock(config["state_root"]):
                pytest.fail("second owner admitted")
    assert workflow._ref(path) == before


def test_terminal_source_verification_detects_same_bytes_new_inode(tmp_path):
    path = tmp_path/"TEST_FIXTURE_source.json"
    ref = _save(path, {"provider": "TEST_FIXTURE"})
    copy = tmp_path/"replacement.json"
    copy.write_bytes(path.read_bytes())
    copy.replace(path)
    with pytest.raises(ValueError):
        bench._terminal([ref], lambda _: None)


def test_script_closure_contains_new_scope_and_original_harnesses():
    refs = bench._script_refs(lambda _: None)
    names = {Path(row["path"]).name for row in refs}
    assert {"benchmark_premarket_stages_v1.py", "codex_premarket_fast_v1.py",
            "benchmark_real_premarket_v1.py", "benchmark_real_premarket_v2.py", "codex_first_core.py"} <= names


def test_worker_guard_forbids_network_children_and_writes_to_real_state(config, tmp_path):
    output = tmp_path/"TEST_FIXTURE_owned"
    output.mkdir()
    guard = bench._worker_guard(output)
    guard("open", (str(output/"result.json"), "x", os.O_WRONLY|os.O_CREAT))
    guard("open", (str(Path(config["state_root"])/"premarket.lock"), "r", os.O_RDONLY))
    for event, args in (("socket.__new__", ()), ("subprocess.Popen", ()), ("os.fork", ()),
            ("open", (str(Path(config["state_root"])/"DO_NOT_WRITE.json"), "w", os.O_WRONLY)),
            ("os.rename", (str(output/"result.json"), str(Path(config["state_root"])/"DO_NOT_WRITE.json")))):
        with pytest.raises((RuntimeError, ValueError)):
            guard(event, args)


def test_theme_audit_omits_only_verified_fresh_fp_and_keeps_sha_counters_unknown(tmp_path):
    values = []
    for index in range(2):
        root = tmp_path/str(index)/"price-journal"
        root.mkdir(parents=True)
        ref = _save(root/"rows.jsonl", {"provider": "TEST_FIXTURE", "unknown": None, "exact": "1/3"})
        audit = dict(complete_allowed_row_audit=True, files={"rows.jsonl": {k: v for k, v in ref.items() if k != "path"}},
                     row_count=1, schema_version="TEST_FIXTURE")
        manifest = _save(root/"manifest.json", audit)
        theme = SimpleNamespace(artifacts={"native-theme-parent.json.gz": {"path": str(root.parent/"native-theme-parent.json.gz")}},
                                validation_references=[manifest, ref])
        provenance = dict(audit_manifest=audit, source_terminal_callback_status="LIVE_CALLBACK_COMPLETED",
                          journal_read_metrics={"actual_read_calls": 10}, UNKNOWN="UNKNOWN")
        projection, excluded = bench._theme_provenance(provenance, theme, lambda _: None)
        assert projection["journal_read_metrics"] == provenance["journal_read_metrics"]
        assert projection["UNKNOWN"] == "UNKNOWN" and excluded == {"rows.jsonl": ref["fp"]}
        assert projection["audit_manifest"]["files"]["rows.jsonl"] == {"bytes": ref["bytes"], "sha256": ref["sha256"]}
        values.append(projection)
    assert values[0] == values[1]


def test_actual_fresh_worker_refuses_fixture_before_native_and_keeps_state(config, tmp_path):
    output = tmp_path/"TEST_FIXTURE_child_experiment"
    directory = output/"01-reference"
    directory.mkdir(parents=True)
    capture = _collection()
    capture["coverage_type"] = "TEST_FIXTURE"
    capref = _save(tmp_path/"TEST_FIXTURE_capture.json", capture)
    cfgref = _save(tmp_path/"TEST_FIXTURE_config.json", config)
    request = dict(schema_version=bench.VERSION, configuration=bench._configuration(config),
        original_configuration=config, arm_output_mib=None, configuration_source=cfgref,
        capture_summary=capref, theme_summary=None, references=[cfgref, capref],
        stage_scope="core", common_actual_cutoff=datetime.now(timezone.utc).isoformat())
    request_path = output/"experiment.json"
    request_ref = _save(request_path, request)
    before = workflow._ref(Path(config["state_root"])/"premarket.lock")
    result = bench._run_process(bench._configuration(config), directory, "reference", request_path,
                                [cfgref, capref, request_ref])
    assert result["status"] == "FAILED" and result["comparison_proof"] is None
    assert result["external_worker_reaped"] is True and result["external_exit_code"] == 4
    assert not (directory/"job").exists()
    assert "FULL_REAL_SAVED_CAPTURE_COLLECTION_REQUIRED" in (directory/"console.txt").read_text()
    assert workflow._ref(Path(config["state_root"])/"premarket.lock") == before


def test_timeout_reaps_owned_child_and_cannot_publish_complete(config, tmp_path, monkeypatch):
    directory = tmp_path/"TEST_FIXTURE_timeout"/"01-reference"
    directory.mkdir(parents=True)
    request_path = directory.parent/"experiment.json"
    ref = _save(request_path, {"provider": "TEST_FIXTURE"})
    launch = subprocess.Popen
    children = []
    def sleeping_child(command, *args, **kwargs):
        if command[0] == sys.executable and str(Path(bench.__file__).resolve()) in command:
            child = launch([sys.executable, "-I", "-B", "-c", "import time; time.sleep(30)"], *args, **kwargs)
            children.append(child)
            return child
        return launch(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", sleeping_child)
    config["limits"]["max_seconds"] = .5
    result = bench._run_process(config, directory, "reference", request_path, [ref])
    assert len(children) == 1 and children[0].poll() is not None
    assert workflow._owned_group_members(children[0].pid) == []
    assert result["status"] == "FAILED" and result["comparison_proof"] is None


def test_parent_output_aggregate_and_receipt_failure_never_publish_speed(config, tmp_path, monkeypatch):
    directory = tmp_path/"TEST_FIXTURE_experiment"/"01-reference"
    directory.mkdir(parents=True)
    started = time.perf_counter()
    bench._reserve_failure_receipts(config, directory, "reference", started)
    monkeypatch.setattr(bench, "MAX_EXPERIMENT_BYTES", 1)
    result = bench._publish_process_result(config, directory, "reference", started, _measurement())
    assert result["status"] == "FAILED" and result["comparison_proof"] is None
    for path in bench._receipt_paths(directory, "reference"):
        assert workflow._read_control(path)["status"] == "FAILED"


@pytest.mark.parametrize("stage", ("first-candidate", "second-candidate", "last-rename", "last-quota-check"))
@pytest.mark.parametrize("target", ("source", "artifact"))
def test_parent_publication_races_retract_both_positive_receipts(config, tmp_path, monkeypatch, stage, target):
    directory = tmp_path/"TEST_FIXTURE_race"/"01-reference"
    directory.mkdir(parents=True)
    source = _save(tmp_path/"TEST_FIXTURE_source.json", {"provider": "TEST_FIXTURE"})
    artifact = _save(directory/"TEST_FIXTURE_artifact.json", {"result": "UNKNOWN"})
    changed = Path((source if target == "source" else artifact)["path"])
    started = time.perf_counter()
    bench._reserve_failure_receipts(config, directory, "reference", started)
    charge, count = bench._meter(config, started)
    save, rename, quota = bench._save, Path.rename, bench._check_parent_budget
    triggered = []
    def replace_same_bytes():
        if not triggered:
            # Identical SHA with a different inode still invalidates source authority.
            replacement = changed.with_name(changed.name+".replacement")
            replacement.write_bytes(changed.read_bytes())
            replacement.replace(changed)
            triggered.append(True)
    def candidate(path, value, **kwargs):
        save(path, value, **kwargs)
        if stage == "first-candidate" and path.name == "process-result.candidate.json":
            replace_same_bytes()
        elif stage == "second-candidate" and path.name == "01-reference-receipt.candidate.json":
            replace_same_bytes()
    def move(path, destination):
        value = rename(path, destination)
        if stage == "last-rename" and path.name == "01-reference-receipt.candidate.json":
            replace_same_bytes()
        return value
    def check(*args):
        quota(*args)
        if stage == "last-quota-check" and all(path.exists() for path in bench._receipt_paths(directory, "reference")):
            replace_same_bytes()
    monkeypatch.setattr(bench, "_save", candidate)
    monkeypatch.setattr(Path, "rename", move)
    monkeypatch.setattr(bench, "_check_parent_budget", check)
    result = bench._publish_process_result(config, directory, "reference", started,
        {**_measurement(), "artifact_references": [artifact]}, [source], charge=charge, read_count=count)
    assert triggered == [True]
    assert count[0] == source["bytes"]+artifact["bytes"]  # Same cumulative meter includes the complete SHA pass.
    assert result["status"] == "FAILED" and result["comparison_proof"] is None
    assert result["parent_failure"]["reason"].endswith("PARENT_FINAL_REFERENCE_CHANGED")
    for path in bench._receipt_paths(directory, "reference"):
        assert workflow._read_control(path) == result
        assert path.stat().st_size == bench.FAILURE_RECEIPT_BYTES
    assert not list(directory.parent.rglob("*.candidate.json"))
    assert list(directory.parent.rglob("*.rejected.json"))
    assert bench.compare(_measurement(), result)["comparable"] is False


def test_parent_publication_sha_failure_uses_reserved_failed_receipts(config, tmp_path):
    directory = tmp_path/"TEST_FIXTURE_bad_sha"/"01-reference"
    directory.mkdir(parents=True)
    ref = _save(tmp_path/"TEST_FIXTURE_source.json", {"provider": "TEST_FIXTURE"})
    ref["sha256"] = "0"*64
    started = time.perf_counter()
    bench._reserve_failure_receipts(config, directory, "reference", started)
    result = bench._publish_process_result(config, directory, "reference", started, _measurement(), [ref])
    assert result["status"] == "FAILED" and result["comparison_proof"] is None
    assert "ARCHIVED_REFERENCE_CHANGED" in result["parent_failure"]["reason"]
    assert not list(directory.parent.rglob("*.candidate.json"))
    assert not list(directory.parent.rglob("*.rejected.json"))
    assert all(workflow._read_control(path)["status"] == "FAILED" for path in bench._receipt_paths(directory, "reference"))


def test_abba_stops_immediately_on_failed_arm_without_recovery(config, tmp_path, monkeypatch):
    capture = tmp_path/"TEST_FIXTURE_capture.json"
    _save(capture, _collection())
    config_ref = _save(tmp_path/"TEST_FIXTURE_config.json", config)
    monkeypatch.setattr(bench, "_script_refs", lambda charge: [])
    monkeypatch.setattr(workflow, "_code_refs", lambda charge: [])
    calls = []
    def failed(config, directory, profile, request_path, references):
        calls.append(profile)
        return {**_measurement(profile), "status": "FAILED", "comparison_proof": None}
    monkeypatch.setattr(bench, "_run_process", failed)
    result = bench.benchmark(config, capture, None, "core", ["reference", "canonical", "canonical", "reference"],
        tmp_path/"TEST_FIXTURE_output", configuration_source=config_ref, arm_output_mib=128)
    assert calls == ["reference"] and result["unrun_profiles"] == ["canonical", "canonical", "reference"]
    assert "stage_speed_ratio" not in result
    request = workflow._read_control(tmp_path/"TEST_FIXTURE_output"/"experiment.json")
    assert request["original_configuration"] == config
    assert request["configuration"]["limits"]["output_bytes"] == 128*1024**2
    assert request["system_clock_frozen"] is False and request["failed_daily_job_retried"] is False


def test_both_native_profiles_compare_all_fixture_core_artifacts(tmp_path):
    """Only TEST_FIXTURE originals; no daily request or real-data execution."""
    from scripts import codex_premarket_fast_v1 as fast
    from stockfish_core.yaogu import premarket_native_core_v1 as core
    from stockfish_core.yaogu.premarket_runtime_v1 import Budget
    from tests.yaogu.test_premarket_workflow_v1 import WorkflowFixtureTests
    fixture = WorkflowFixtureTests(methodName="runTest")
    fixture.setUp()
    try:
        adapters, cutoff = fixture._adapters()
        collection = adapters["collect"]()
        captures = [record.to_dict() for record in collection.core_records]
        proof_capture = dict(observation_date=collection.observation_date, target_session=collection.target_session,
                             core_records=captures, coverage_type="TEST_FIXTURE")
        results = []
        for profile in bench.PROFILES:
            job = tmp_path/profile
            job.mkdir()
            budget = Budget(dict(max_seconds=120), job_dir=job)
            started = datetime.now(timezone.utc).isoformat()
            with budget.phase("core"), fast.execution_scope(profile, budget) as metrics:
                with budget.meter_files():
                    owner = core.run_core(captures=captures, observation_date=collection.observation_date,
                        target_session=collection.target_session, cutoff=cutoff.isoformat(), output_dir=job/"core",
                        budget=budget, coverage_type="TEST_FIXTURE")
                    owner.validate()
            finished = datetime.now(timezone.utc).isoformat()
            result = _measurement(profile)
            result["comparison_proof"] = bench._proof(owner, None, proof_capture, None, "core", cutoff.isoformat(),
                started, finished, lambda _: None)
            assert metrics["bindings_restored"] is True
            assert result["comparison_proof"]["source_clocks_removed"] is False
            assert result["comparison_proof"]["full_input_compared"] is True
            results.append(result)
        assert bench.compare(*results)["comparable"] is True
        assert results[0]["comparison_proof"]["excluded_values"]["canonical-run/generated_at"] != \
            results[1]["comparison_proof"]["excluded_values"]["canonical-run/generated_at"]
    finally:
        fixture.doCleanups()
