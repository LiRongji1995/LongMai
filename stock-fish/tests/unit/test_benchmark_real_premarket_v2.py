"""V2 controls use fresh owned TEST_FIXTURE paths, never real sources/network."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from scripts import benchmark_real_premarket_v1 as v1
from scripts import benchmark_real_premarket_v2 as bench
from stockfish_core.yaogu import premarket_news_v1 as news
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
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
        limits={"max_seconds": 30, "output_bytes": 256 * 1024**2},
        capture={"offline": False, "retry_failed": True}))


def sealed_phases():
    return dict(status="COMPLETED_WITH_UNKNOWNS", core_verified=True, phase_failures={},
        phases=dict(capture="COMPLETE", core="COMPLETE", news="UNKNOWN", theme_sources="PARTIAL",
                    theme="COMPLETE", presentation="COMPLETE"))


@pytest.mark.parametrize("key", ("capture", "core", "theme", "presentation", "theme_sources", "news"))
def test_no_failed_stage_is_admitted(key):
    value = sealed_phases()
    value["phases"][key] = "FAILED"
    with pytest.raises(ValueError):
        bench._complete_phases(value, offline=True)


@pytest.mark.parametrize("status", ("BUDGET_EXCEEDED", "FAILED", "UNSEALED", "RUNNING"))
def test_incomplete_or_budget_status_rejected(status):
    value = sealed_phases()
    value["status"] = status
    with pytest.raises(ValueError, match="SUCCESSFUL_VERIFIED_CORE"):
        bench._complete_phases(value, offline=True)


def test_retained_unknowns_and_online_partial_news_are_explicit():
    value = sealed_phases()
    saved = deepcopy(value)
    bench._complete_phases(value, offline=True)
    assert value == saved
    value["phases"]["news"] = "PARTIAL"
    bench._complete_phases(value, offline=False)
    with pytest.raises(ValueError, match="NEWS"):
        bench._complete_phases(value, offline=True)
    value["status"] = "COMPLETE"
    with pytest.raises(ValueError, match="UNKNOWN_STATUS"):
        bench._complete_phases(value, offline=False)
    value["status"] = "COMPLETED_WITH_UNKNOWNS"
    value["phase_failures"]["theme"] = {"reason": "TEST_FIXTURE_IO_ERROR"}
    with pytest.raises(ValueError, match="EXECUTION_FAILURE"):
        bench._complete_phases(value, offline=False)


def _save_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return workflow._ref(path)


@pytest.fixture
def theme_fixture(tmp_path):
    selected = ["600001.SH"]
    directory_captures, records, captures = [], [], []
    declarations = {"industry_members_sina": ["hangye_TEST_FIXTURE"],
                    "concept_members_sina": ["gn_TEST_FIXTURE_POTENTIAL", "gn_TEST_FIXTURE_UNKNOWN"]}
    for endpoint, nodes in declarations.items():
        directory_endpoint = endpoint.replace("_members_", "_list_")
        ref = _save_json(tmp_path / (directory_endpoint+".json"), dict(provider="TEST_FIXTURE",
            status="COMPLETE", pagination_complete=True, rows=[{"label": node} for node in nodes]))
        directory_captures.append(dict(endpoint=directory_endpoint, status="COMPLETE",
            pagination_complete=True, result=ref))
        for index, node in enumerate(nodes):
            partial = endpoint == "concept_members_sina"
            intersection = selected if index == 0 else []
            rows = [{"code": "600001", "symbol": "sh600001"}] if intersection else []
            result = dict(provider="TEST_FIXTURE", endpoint=endpoint, parameters={"symbol": node},
                status="PARTIAL" if partial else "COMPLETE", pagination_complete=not partial,
                rows=rows, gaps=["PAGINATION_OR_EMPTY_RESULT_NOT_VERIFIED"] if partial else [], pagination_reason="TEST_FIXTURE_UNPROVEN_PAGE" if partial else None)
            ref = _save_json(tmp_path / (node+".json"), result)
            capture = dict(receipt_id="TEST_FIXTURE_"+node, endpoint=endpoint, parameters={"symbol": node},
                status=result["status"], pagination_complete=not partial, rows=len(rows), result=ref)
            captures.append(capture)
            records.append(dict(endpoint=endpoint, parameters=capture["parameters"], status=result["status"],
                pagination_complete=not partial, rows=len(rows), actual_capture=capture,
                literal_intersection_with_full_original_selected=intersection, potential_reader_request=bool(intersection),
                omitted_from_potential_scope_reason=None if intersection else
                    "INCOMPLETE_SOURCE_CANNOT_PROVE_ABSENCE_OF_MEMBERSHIP",
                literal_intersection_is_not_authenticated_membership=True))
    facts = dict(schema_version="PREMARKET_THEME_ACQUISITION_FACTS_V1", status="PARTIAL_OR_FAILED_RETAINED",
        original_selected_count=1, display_amount_filter_applied=False,
        scope="ALL_DECLARED_SOURCE_NODES_THEN_GENUINE_READER_INTERSECTION_WITH_FULL_ORIGINAL_SELECTION",
        records=records, directory_captures=directory_captures, requested_members=3,
        all_requested_industry_nodes=declarations["industry_members_sina"],
        all_requested_concept_symbols=declarations["concept_members_sina"],
        potential_industry_nodes=["hangye_TEST_FIXTURE"], potential_concept_symbols=["gn_TEST_FIXTURE_POTENTIAL"],
        excluded_from_potential_scope_count=1, potential_scope_is_not_ready_or_source_authority=True,
        native_authority="NOT_ISSUED_BY_CAPTURE_CACHE")
    themes = dict(schema_version="PREMARKET_THEME_SOURCE_COLLECTION_V1", source_capabilities_issued=0,
        context_captures=captures, context_capture_count=len(captures), raw_receipt_sources=[], raw_receipt_source_count=0, parent_acquisition=facts,
        requested_industry_nodes=facts["potential_industry_nodes"], requested_concept_symbols=facts["potential_concept_symbols"])
    return themes, {"research_selected_ids": selected}


def test_all_partial_nodes_including_no_intersection_remain_in_ledger(theme_fixture):
    themes, arm = theme_fixture
    before = deepcopy(themes)
    projection = bench._theme_acquisition(themes, arm, "PARTIAL", lambda _: None)
    assert themes == before
    assert len(projection["retained_pagination_gaps"]) == 2
    assert projection["retained_pagination_gaps"][1]["fact"]["potential_reader_request"] is False
    assert projection["full_acquisition"]["requested_members"] == 3
    assert projection["full_market_source_completeness_claimed"] is False
    assert all(row["original_result_metadata"]["pagination_reason"] == "TEST_FIXTURE_UNPROVEN_PAGE"
               for row in projection["retained_pagination_gaps"])


@pytest.mark.parametrize("fault", ("failed", "false_empty", "phase_relabel", "omit_node", "omit_directory_node",
                                    "partial_page_true", "selection", "scope", "potential", "actual_page", "provider_failure"))
def test_theme_unknown_and_full_scope_guards(theme_fixture, fault):
    themes, arm = theme_fixture
    themes = deepcopy(themes)
    facts = themes["parent_acquisition"]
    phase = "PARTIAL"
    if fault == "failed":
        facts["records"][1]["status"] = "FAILED"
    elif fault == "false_empty":
        facts["records"][-1]["omitted_from_potential_scope_reason"] = "NO_LITERAL_INTERSECTION_OBSERVED"
    elif fault == "phase_relabel":
        phase = "COMPLETE"
    elif fault == "omit_node":
        facts["records"].pop()
    elif fault == "omit_directory_node":
        facts["all_requested_concept_symbols"].pop()
        facts["records"].pop()
        facts["requested_members"] -= 1
    elif fault == "partial_page_true":
        facts["records"][1]["pagination_complete"] = True
    elif fault == "selection":
        arm = {"research_selected_ids": []}
    elif fault == "scope":
        facts["scope"] = "TEST_FIXTURE_ONLY_COMPLETE_NODES"
    elif fault == "potential":
        facts["potential_concept_symbols"].clear()
    else:
        capture = facts["records"][1]["actual_capture"]
        result = json.loads(Path(capture["result"]["path"]).read_text())
        if fault == "provider_failure":
            result["error_code"] = "REQUEST_TIMEOUT_AFTER_PARTIAL"
        else:
            result["pagination_complete"] = True
        capture["result"] = _save_json(Path(capture["result"]["path"]), result)
    with pytest.raises(ValueError):
        bench._theme_acquisition(themes, arm, phase, lambda _: None)


@pytest.fixture
def offline_news(config, tmp_path):
    job = tmp_path / "TEST_FIXTURE_news"
    job.mkdir()
    budget = Budget(config["limits"], job_dir=job)
    now = datetime.now(timezone.utc)
    run = dict(run_id="TEST_FIXTURE_news_run", decision_cutoff=(now-timedelta(seconds=2)).isoformat(),
        observation_date="2026-10-28", target_session="2026-10-29", strategy_id=bench.STRATEGY,
        results={bench.STRATEGY: {"items": []}})
    value = news.collect_reported_news(core_run=run, output_dir=job / "news", budget=budget,
                                      config=bench._offline_configuration(config)["news"])
    completion = dict(news=value, completed_at=datetime.now(timezone.utc).isoformat())
    return completion, run


def test_native_offline_news_keeps_only_bound_clock_and_run_out_of_projection(offline_news):
    completion, run = offline_news
    projection = bench._news_projection(completion, run, lambda _: None)
    assert "run_id" not in projection and "actual_generated_at" not in projection
    assert projection["summary"]["acquisition_complete"] is None
    assert projection["summary"]["HTTP_GET_attempts"] == 0
    assert projection["articles"] == [] and projection["discovered_candidates"] == []
    assert projection["summary"]["source_coverage"] == "UNKNOWN"


@pytest.mark.parametrize("fault", ("articles", "network", "false_empty", "wrong_run", "clock", "homepage_error"))
def test_offline_news_semantic_and_clock_differences_refuse(offline_news, fault):
    completion, run = offline_news
    value = json.loads(Path(completion["news"]["metadata"]["path"]).read_text())
    if fault == "articles":
        value["articles"] = [{"title": "TEST_FIXTURE"}]
    elif fault == "network":
        value["summary"]["HTTP_GET_attempts"] = 1
    elif fault == "false_empty":
        value["summary"]["acquisition_complete"] = False
    elif fault == "wrong_run":
        value["run_id"] = "TEST_FIXTURE_other"
    elif fault == "clock":
        value["actual_generated_at"] = "2000-01-01T00:00:00Z"
    else:
        value["homepage_parse_errors"] = [{"reason": "TEST_FIXTURE"}]
    completion["news"]["summary"] = value["summary"]
    completion["news"]["metadata"] = _save_json(Path(completion["news"]["metadata"]["path"]), value)
    with pytest.raises(ValueError):
        bench._news_projection(completion, run, lambda _: None)


def measurement(engine, seconds=10):
    local = datetime.now(timezone.utc).astimezone(workflow.SH).date()
    expected = [str(local-timedelta(days=1)), str(local+timedelta(days=1)), str(local)]
    return dict(schema_version=bench.VERSION, engine=engine, status="COMPLETED_WITH_UNKNOWNS",
        workflow_wall_seconds=seconds, job_output_bytes=100, external_exit_code=0, external_worker_reaped=True,
        prerequisite={"run_id": "TEST_FIXTURE_current_original", "expected_sessions": expected}, comparison_proof=dict(
            hashes={key: "a"*64 for key in bench.COMPARISON_KEYS}, observation_date="2026-10-28",
            target_session="2026-10-29"))


def test_strict_comparison_keeps_unknowns_and_reports_narrow_scope():
    result = bench.compare(measurement("original-ref"), measurement("shared", 8))
    assert result["comparable"] and result["worker_wall_speedup_percent"] == 20
    assert result["real_end_to_end_speedup_established"] is False
    assert result["full_market_source_completeness_claimed"] is False
    assert result["original_online_news_equivalence_claimed"] is False


@pytest.mark.parametrize("difference", bench.COMPARISON_KEYS)
def test_every_v1_and_new_semantic_projection_is_required(difference):
    first, second = measurement("original-ref"), measurement("shared", 1)
    second["comparison_proof"]["hashes"][difference] = "b"*64
    result = bench.compare(first, second)
    assert not result["comparable"] and "worker_wall_speedup_percent" not in result
    assert "DIFF:"+difference in result["reasons"]


@pytest.mark.parametrize("fault", ("original_job", "version", "exit", "reaped", "missing_proof_key"))
def test_success_summary_alone_never_qualifies(fault):
    first, second = measurement("original-ref"), measurement("shared", 1)
    if fault == "original_job":
        second["prerequisite"]["run_id"] = "TEST_FIXTURE_old_failed"
    elif fault == "version":
        second["schema_version"] = v1.VERSION
    elif fault == "exit":
        second["external_exit_code"] = 4
    elif fault == "reaped":
        second["external_worker_reaped"] = False
    else:
        for row in (first, second):
            row["comparison_proof"]["hashes"].pop("offline_news")
    result = bench.compare(first, second)
    assert not result["comparable"] and "worker_speed_ratio" not in result


def test_v2_closure_and_fresh_process_command_are_actual_not_return_schema(config, tmp_path, monkeypatch):
    assert bench._run_process.__globals__ is vars(bench)
    assert bench._run_process.__globals__["__file__"] == bench.__file__
    assert v1._run_process.__globals__["VERSION"] == v1.VERSION
    assert bench._reserve_failure_receipts.__globals__["VERSION"] == bench.VERSION
    refs = bench._code_refs()
    assert str(Path(bench.__file__).resolve()) in {ref["path"] for ref in refs}
    assert str(Path(v1.__file__).resolve()) in {ref["path"] for ref in refs}
    directory = tmp_path / "TEST_FIXTURE_arm"
    directory.mkdir()
    # A real isolated subprocess enters V2 and refuses missing experiment
    # before any native worker or provider can start.
    result = bench._run_process(config, directory, "original-ref",
        ("2026-10-28", "2026-10-29", str(datetime.now(timezone.utc).astimezone(workflow.SH).date())))
    assert result["schema_version"] == bench.VERSION and result["status"] == "FAILED"
    assert result["external_worker_reaped"] is True
    assert not (directory / "job").exists()
    console = (directory / "console.txt").read_text()
    assert "benchmark_real_premarket_v2.py" in console


def test_v2_run_cannot_start_before_current_original_is_sealed(config, tmp_path, monkeypatch):
    original_job = Path(config["state_root"]) / "TEST_FIXTURE_current_original"
    original_job.mkdir()
    _save_json(original_job / "final-control.json", {"status": "UNSEALED"})
    monkeypatch.setattr(bench.shared, "_expected_gate", lambda expected: None)
    def forbidden(*args, **kwargs):
        raise AssertionError("TEST_FIXTURE_ARM_MUST_NOT_START")
    monkeypatch.setattr(bench, "_run_process", forbidden)
    output = tmp_path / "TEST_FIXTURE_output"
    with pytest.raises(ValueError, match="CURRENT_SUCCESSFUL_SEALED_ORIGINAL"):
        bench.benchmark(config, output, ["original-ref"], "2026-10-28", "2026-10-29", original_job)
    assert not output.exists()


@pytest.mark.parametrize("fault", ("read", "time", "delete_before_publish", "alias_before_publish", "mutate_after_rename", "interrupt_after_rename", "date_after_rename", None))
def test_parent_full_terminal_and_final_receipt_guards(config, tmp_path, monkeypatch, fault):
    directory = tmp_path / "TEST_FIXTURE_parent"
    directory.mkdir()
    source = tmp_path / "TEST_FIXTURE_original_source.json"
    reference = _save_json(source, {"role": "TEST_FIXTURE"})
    row = measurement("original-ref")
    row["comparison_proof"]["terminal_references"] = [reference]
    started = bench.time.perf_counter()
    bench._reserve_failure_receipts(config, directory, "original-ref", started)
    if fault == "read":
        row["comparison_verification_returned_bytes"] = config["limits"]["supervisor_read_bytes"]
    if fault == "time":
        started -= config["limits"]["max_seconds"] + 1
    if fault == "delete_before_publish":
        source.unlink()
    elif fault == "alias_before_publish":
        alias_target = tmp_path / "TEST_FIXTURE_other_source"
        source.rename(alias_target)
        source.symlink_to(alias_target)
    gate_calls = []
    def date_gate(expected):
        if gate_calls:
            raise ValueError("TEST_FIXTURE_DATE_CHANGED")
    if fault == "date_after_rename":
        monkeypatch.setattr(bench.shared, "_expected_gate", date_gate)
    rename = Path.rename
    def changed(path, target):
        result = rename(path, target)
        if path.name == "original-ref-receipt.candidate.json":
            if fault == "date_after_rename":
                gate_calls.append(True)
            if fault == "mutate_after_rename":
                source.write_text('{"role":"TEST_FIXTURE_CHANGED"}')
            if fault == "interrupt_after_rename":
                raise KeyboardInterrupt("TEST_FIXTURE_INTERRUPT")
        return result
    monkeypatch.setattr(Path, "rename", changed)
    if fault == "interrupt_after_rename":
        with pytest.raises(KeyboardInterrupt):
            bench._publish_process_result(config, directory, "original-ref", started, row)
    else:
        result = bench._publish_process_result(config, directory, "original-ref", started, row)
        assert result["status"] == ("FAILED" if fault else "COMPLETED_WITH_UNKNOWNS")
    for path in bench._receipt_paths(directory, "original-ref"):
        value = json.loads(path.read_text())
        assert value["schema_version"] == bench.VERSION
        if fault:
            assert value["status"] == "FAILED" and value["comparison_proof"] is None


def test_terminal_rechecks_nonpotential_source_after_proof(theme_fixture):
    themes, _ = theme_fixture
    references = bench._references(themes)
    changed = Path(themes["parent_acquisition"]["records"][-1]["actual_capture"]["result"]["path"])
    changed.write_text('{"provider":"TEST_FIXTURE_CHANGED"}')
    with pytest.raises(ValueError):
        bench._terminal(references, lambda _: None)


def test_cli_requires_current_original_and_explicit_arms():
    with pytest.raises(SystemExit):
        bench.parser().parse_args(["run", "--config", "TEST_FIXTURE", "--output", "TEST_FIXTURE",
                                  "--observation-date", "2026-10-28", "--target-session", "2026-10-29"])
    args = bench.parser().parse_args(["run", "--config", "TEST_FIXTURE", "--output", "TEST_FIXTURE",
        "--observation-date", "2026-10-28", "--target-session", "2026-10-29",
        "--completed-original-job", "TEST_FIXTURE_original", "--engines", "shared"])
    assert args.engines == ["shared"]


@pytest.fixture
def saved_arms(config, tmp_path, monkeypatch):
    """Only compare orchestration is substituted; production proof stays strict."""
    config = bench._offline_configuration(config)
    prerequisite = dict(measurement("original")["prerequisite"], job="TEST_FIXTURE_qualified_job",
        baseline_hashes={key: "a"*64 for key in bench.BASELINE_KEYS}, terminal_references=[])
    source = tmp_path / "TEST_FIXTURE_evidence.json"
    source_ref = _save_json(source, {"role": "TEST_FIXTURE_original_source"})
    proof_calls = []
    monkeypatch.setattr(bench, "_code_refs", lambda charge=None: [])
    monkeypatch.setattr(bench, "_prerequisite", lambda *args: deepcopy(prerequisite))
    monkeypatch.setattr(bench.shared, "_expected_gate", lambda expected: None)
    def fixture_proof(completion, job, charge, **kwargs):
        assert completion["fixture_role"] == "TEST_FIXTURE_compare_orchestration_only"
        proof_calls.append((job, id(charge)))
        charge(7)
        return deepcopy(proof)
    monkeypatch.setattr(bench, "_proof", fixture_proof)
    proof = dict(measurement("original")["comparison_proof"], terminal_references=[source_ref])
    directories = []
    for number, engine in enumerate(("original", "shared")):
        root = tmp_path / ("TEST_FIXTURE_experiment_"+str(number))
        directory = root / engine
        (directory / "job").mkdir(parents=True)
        experiment = dict(schema_version=bench.VERSION, engines=[engine], configuration=config,
            expected_sessions=prerequisite["expected_sessions"], source_code=[], prerequisite=prerequisite)
        _save_json(root / "experiment.json", experiment)
        completion = dict(schema_version=bench.shared.VERSION, execution_engine=engine,
            expected_sessions=prerequisite["expected_sessions"], observation_date=prerequisite["expected_sessions"][0],
            target_session=prerequisite["expected_sessions"][1], fixture_role="TEST_FIXTURE_compare_orchestration_only")
        completion_ref = _save_json(directory / "job" / "worker-completion.json", completion)
        row = measurement(engine, 10-number)
        row.update(prerequisite=prerequisite, comparison_proof=proof, job=str(directory / "job"),
            completion=completion_ref, external_wall_seconds=2, sampled_peak_tree_rss_bytes=1,
            parent_terminal_total_returned_bytes=100,
            started_at="2026-10-28T18:00:00Z", completed_at="2026-10-28T18:00:01Z")
        _save_json(directory / "measurement.json", row)
        for path in bench._receipt_paths(directory, engine):
            _save_json(path, row)
        directories.append(directory)
    return directories, source, proof_calls


def test_cross_experiment_compare_reopens_actual_receipts_and_recomputes_both_proofs(saved_arms, tmp_path):
    directories, _, calls = saved_arms
    result = bench.compare_saved(*directories, tmp_path / "TEST_FIXTURE_comparison")
    assert result["comparable"] and result["saved_parent_receipts_and_full_proofs_revalidated"]
    assert len(calls) == 2 and calls[0][1] == calls[1][1]
    assert [row[0] for row in calls] == [path / "job" for path in directories]
    assert result["comparison_revalidation_returned_bytes"] > 14


@pytest.mark.parametrize("fault", ("source", "receipt", "configuration", "engine", "schema", "sessions", "proof", "read_budget"))
def test_saved_compare_refuses_tamper_and_budget(saved_arms, tmp_path, monkeypatch, fault):
    directories, source, _ = saved_arms
    second = directories[1]
    if fault == "source":
        source.write_text('{"role":"TEST_FIXTURE_changed"}')
    elif fault == "receipt":
        path = second / "process-result.json"
        row = json.loads(path.read_text())
        row["workflow_wall_seconds"] = 0.1
        _save_json(path, row)
    elif fault in ("engine", "schema", "sessions"):
        path = second / "job" / "worker-completion.json"
        row = json.loads(path.read_text())
        key = {"engine": "execution_engine", "schema": "schema_version", "sessions": "expected_sessions"}[fault]
        row[key] = "TEST_FIXTURE_WRONG"
        ref = _save_json(path, row)
        for receipt in (second / "measurement.json", *bench._receipt_paths(second, second.name)):
            row = json.loads(receipt.read_text())
            row["completion"] = ref
            _save_json(receipt, row)
    elif fault == "proof":
        for receipt in (second / "measurement.json", *bench._receipt_paths(second, second.name)):
            row = json.loads(receipt.read_text())
            row["comparison_proof"]["hashes"]["offline_news"] = "b"*64
            _save_json(receipt, row)
    else:
        path = (directories[0] if fault == "read_budget" else second).parent / "experiment.json"
        row = json.loads(path.read_text())
        row["configuration"]["limits"]["supervisor_read_bytes"] = 1
        _save_json(path, row)
    output = tmp_path / "TEST_FIXTURE_refused_comparison"
    with pytest.raises(ValueError):
        bench.compare_saved(*directories, output)
    assert not (output / "comparison.json").exists()


@pytest.mark.parametrize("fault", ("time", "source", "date"))
def test_saved_compare_after_rename_cannot_leave_positive_result(saved_arms, tmp_path, monkeypatch, fault):
    directories, source, _ = saved_arms
    clock = [100.0]
    monkeypatch.setattr(bench.time, "perf_counter", lambda: clock[0])
    changed = []
    def date_gate(expected):
        if fault == "date" and changed:
            raise ValueError("TEST_FIXTURE_DATE_CHANGED")
    monkeypatch.setattr(bench.shared, "_expected_gate", date_gate)
    rename = Path.rename
    def injected(path, target):
        result = rename(path, target)
        if path.name == "comparison.candidate.json":
            changed.append(True)
            if fault == "time":
                clock[0] += 31
            elif fault == "source":
                source.write_text('{"role":"TEST_FIXTURE_changed_after_publish"}')
        return result
    monkeypatch.setattr(Path, "rename", injected)
    output = tmp_path / "TEST_FIXTURE_tail_failure"
    result = bench.compare_saved(*directories, output)
    assert result["status"] == "FAILED" and result["comparable"] is False
    assert json.loads((output / "comparison.json").read_text())["comparable"] is False
    assert json.loads((output / "comparison.rejected.json").read_text())["comparable"] is True


def test_native_fixture_whole_pipeline_is_not_relabelled_as_real_proof():
    from tests.yaogu.test_premarket_workflow_v1 import WorkflowFixtureTests
    fixture = WorkflowFixtureTests(methodName="runTest")
    fixture.setUp()
    try:
        adapters, now = fixture._adapters()
        completion = fixture._execute(adapters, now)
        assert completion["phases"]["core"] == completion["phases"]["theme"] == "COMPLETE"
        with pytest.raises(ValueError, match="REAL_COVERAGE_REQUIRED"):
            bench._proof(completion, fixture.root / "job", lambda _: None)
    finally:
        fixture.doCleanups()


def test_worker_initial_config_read_is_charged(config, tmp_path, monkeypatch):
    directory = tmp_path / "TEST_FIXTURE_worker_initial_read"
    directory.mkdir()
    config_path = directory / "configuration.json"
    _save_json(config_path, config)
    called = []
    native_meter = bench._meter
    def meter(actual, started, initial=0):
        called.append(initial)
        return native_meter(actual, started, initial)
    monkeypatch.setattr(bench, "_meter", meter)
    monkeypatch.setattr(bench.shared, "_expected_gate", lambda expected: None)
    with pytest.raises(FileNotFoundError):
        bench._worker(config_path, directory, "original-ref", ("2026-10-28", "2026-10-29", "2026-10-29"))
    assert called == [config_path.stat().st_size]
    assert not (directory / "job").exists()


def test_parent_measurement_read_budget_failure_seals_failed(config, tmp_path, monkeypatch):
    directory = tmp_path / "TEST_FIXTURE_parent_measurement_read"
    directory.mkdir()
    config["limits"]["supervisor_read_bytes"] = 64
    monkeypatch.setattr(bench.original, "_owned_group_members", lambda *args: ())
    class FixtureProcess:
        pid, returncode = 999999, 0
        def __init__(self, command, **kwargs):
            assert command[1:4] == ["-I", "-B", "-X"]
            assert command[5] == str(Path(bench.__file__).resolve())
            row = measurement("original-ref")
            row["fixture_role"] = "TEST_FIXTURE_control_read_limit"
            _save_json(directory / "measurement.json", row)
        def poll(self):
            return 0
        def wait(self):
            return 0
    monkeypatch.setattr(bench.subprocess, "Popen", FixtureProcess)
    result = bench._run_process(config, directory, "original-ref", ("2026-10-28", "2026-10-29", "2026-10-29"))
    assert result["status"] == "FAILED" and result["comparison_proof"] is None
    assert result["parent_control_returned_bytes"] > 64
    assert "COMPARISON_READ_LIMIT" in result["process_failure"]["reason"]
    for path in bench._receipt_paths(directory, "original-ref"):
        assert json.loads(path.read_text())["status"] == "FAILED"
