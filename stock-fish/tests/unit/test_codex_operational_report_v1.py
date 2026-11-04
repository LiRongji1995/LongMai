"""TEST_FIXTURE: independent report composition and source identity boundaries."""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from scripts import codex_operational_report_v1 as operational
from scripts import codex_delivery_audit_v1 as delivery
from tests.unit.test_codex_delivery_audit_v1 import evidence


def fixture_run():
    source = Path(__file__).with_name("test_codex_research_coverage_v1.py")
    helpers = runpy.run_path(str(source))
    value = helpers["fixture_run"]()
    value["target_session"] = "2026-10-16"
    return helpers["seal"](value)


def attach_run(evidence):
    run = fixture_run()
    path = evidence.job / "canonical-run.json.gz"
    raw = gzip.compress(json.dumps(run).encode(), mtime=0)
    evidence.core[path.name] = evidence.write(path, raw)
    evidence.completion.update({key: run[key] for key in
        ("run_id", "observation_date", "target_session", "strategy_id")})
    evidence.seal()
    return run, path, hashlib.sha256(raw).hexdigest()


def test_exact_job_run_coverage_is_composed_without_promoting_delivery(evidence):
    run, path, sha = attach_run(evidence)
    evidence.final.update(status="FAILED", external_worker_exit_code=-9, display=None)
    evidence.seal()
    before = {p: p.read_bytes() for p in evidence.job.iterdir() if p.is_file()}
    result, inspection = operational.combined_delivery(evidence.job, path, sha)
    assert result["status"] == "FAILED"
    assert result["coverage"]["run_id"] == run["run_id"]
    assert result["coverage"]["outcome_status"] == "NOT_PROVIDED"
    assert result["coverage"]["qualification"]["eligibility_unknown"] == 2
    assert result["partial_core_is_complete_daily_delivery"] is False
    assert result["source_reauthenticated_now"] is False
    assert result["last_source_authentication_at"] is None
    assert result["user_delivery_status"] == "UNKNOWN_NOT_OBSERVED"
    inspection.terminal()
    assert all(p.read_bytes() == data for p, data in before.items())


def test_same_run_copied_elsewhere_cannot_be_attached_to_job(evidence, tmp_path):
    _, path, sha = attach_run(evidence)
    other = tmp_path / path.name
    other.write_bytes(path.read_bytes())
    with pytest.raises(ValueError, match="JOB_RUN_NOT_BOUND"):
        operational.combined_delivery(evidence.job, other, sha)


def test_audited_same_name_and_run_id_attachment_is_not_the_original_core(evidence):
    run, original_path, original_sha = attach_run(evidence)
    alternate = deepcopy(run)
    alternate["TEST_FIXTURE_annotation"] = "different saved content, same run identity"
    alternate["content_hash"] = hashlib.sha256(json.dumps(
        {key: value for key, value in alternate.items() if key != "content_hash"},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    directory = evidence.job / "TEST_FIXTURE_attachment"
    directory.mkdir()
    other = directory / original_path.name
    raw = gzip.compress(json.dumps(alternate).encode(), mtime=0)
    reference = evidence.write(other, raw)
    evidence.completion["validation_references"] = [reference]
    evidence.seal()
    audit = delivery.audit_job(evidence.job)
    assert reference in audit["evidence"] and alternate["run_id"] == audit["run_id"]
    # The alternative is internally valid; rejection must be the core-role
    # binding, not an accidental malformed-fixture failure.
    from scripts.codex_research_coverage_v1 import build_coverage_report
    assert build_coverage_report(alternate)["signal_content_hash"] == alternate["content_hash"]
    with pytest.raises(ValueError, match="JOB_RUN_NOT_BOUND"):
        operational.combined_delivery(evidence.job, other, hashlib.sha256(raw).hexdigest())
    result, _ = operational.combined_delivery(evidence.job, original_path, original_sha)
    assert result["coverage"]["signal_content_hash"] == run["content_hash"]


def test_canonical_role_can_name_a_legitimate_core_subdirectory(evidence):
    run, path, sha = attach_run(evidence)
    subdirectory = evidence.job / "TEST_FIXTURE_core"
    subdirectory.mkdir()
    nested = subdirectory / path.name
    path.rename(nested)
    evidence.core[nested.name] = delivery.original._reference(nested)
    evidence.seal()
    report, _ = operational.combined_delivery(evidence.job, nested, sha)
    assert report["coverage"]["signal_content_hash"] == run["content_hash"]
    assert report["canonical_run_reference"] == evidence.core[nested.name]


def test_unbound_failed_worker_completion_does_not_bind_a_canonical_core(evidence):
    _, path, sha = attach_run(evidence)
    evidence.final.update(status="FAILED", external_worker_exit_code=-9, completion=None, display=None)
    evidence.write(evidence.job / "final-control.json", evidence.final)
    audit = delivery.audit_job(evidence.job)
    assert audit["completion_binding"] == "UNBOUND_WORKER_RECORD_AFTER_FAILED_SUPERVISOR"
    with pytest.raises(ValueError, match="JOB_RUN_NOT_BOUND"):
        operational.combined_delivery(evidence.job, path, sha)


def test_complete_workflow_is_not_current_source_or_daily_strategy_acceptance(evidence):
    _, path, sha = attach_run(evidence)
    result, _ = operational.combined_delivery(evidence.job, path, sha)
    assert result["status"] == "COMPLETE"
    assert result["daily_v1_readiness"] == "UNKNOWN_NOT_EVALUATED"
    assert result["historical_pit_verified"] is False


def test_job_date_conflict_rejected(evidence):
    _, path, sha = attach_run(evidence)
    evidence.completion["target_session"] = "2026-10-19"
    evidence.seal()
    with pytest.raises(ValueError, match="IDENTITY_CONFLICT"):
        operational.combined_delivery(evidence.job, path, sha)


def test_terminal_detects_late_control_replacement(evidence):
    _, path, sha = attach_run(evidence)
    _, session = operational.combined_delivery(evidence.job, path, sha)
    final = evidence.job/"final-control.json"
    replacement = evidence.job/"TEST_FIXTURE_replacement"
    replacement.write_bytes(final.read_bytes())
    replacement.replace(final)
    with pytest.raises(ValueError):
        session.terminal()


def test_terminal_rejects_parent_replacement_during_actual_read(tmp_path, monkeypatch):
    parent = tmp_path / "TEST_FIXTURE_source_parent"
    parent.mkdir()
    path = parent / "control.json"
    raw = b'{"TEST_FIXTURE":"original"}'
    path.write_bytes(raw)
    session = operational.Inspection()
    session.snapshot(path, hashlib.sha256(raw).hexdigest())
    moved = tmp_path / "TEST_FIXTURE_moved_parent"
    original_read = delivery.os.read
    changed = []
    def replace_ancestor(fd, size):
        value = original_read(fd, size)
        if value and not changed:
            changed.append(True)
            parent.rename(moved)
            parent.mkdir()
            (parent / path.name).write_bytes(raw)
        return value
    monkeypatch.setattr(delivery.os, "read", replace_ancestor)
    with pytest.raises(ValueError, match="PARENT_DIRECTORY_CHANGED"):
        session.terminal()
    assert session.used == 2 * len(raw)
    assert (moved / path.name).read_bytes() == raw
    assert path.read_bytes() == raw


def test_terminal_counts_each_physical_read_once_and_preflights_remaining_bytes(tmp_path):
    path = tmp_path / "TEST_FIXTURE_control.json"
    raw = b'{"TEST_FIXTURE":true}'
    path.write_bytes(raw)
    session = operational.Inspection(max_bytes=3 * len(raw))
    session.snapshot(path, hashlib.sha256(raw).hexdigest())
    assert session.used == len(raw)
    session.terminal()
    assert session.used == 2 * len(raw)
    session.terminal()
    assert session.used == 3 * len(raw)
    with pytest.raises(ValueError, match="READ_BUDGET_EXCEEDED"):
        session.terminal()
    assert session.used == 3 * len(raw)


def test_missing_final_cannot_be_bound_to_an_existing_core(evidence):
    _, path, sha = attach_run(evidence)
    (evidence.job/"final-control.json").unlink()
    with pytest.raises(ValueError, match="JOB_RUN_NOT_BOUND"):
        operational.combined_delivery(evidence.job, path, sha)


def test_read_budget_and_invalid_budget_rejected(evidence):
    _, path, sha = attach_run(evidence)
    with pytest.raises(ValueError):
        operational.combined_delivery(evidence.job, path, sha,
            inspection=operational.Inspection(max_bytes=64))
    for value in (0, -1, True, operational.MAX_BYTES+1):
        with pytest.raises(ValueError):
            operational.Inspection(max_bytes=value)


def spec():
    return dict(schema_version="EXPLICIT_COMPONENT_INVENTORY_V1", components={
        "strategy": dict(version="TEST_FIXTURE_V1", files=["strategy.py"], depends_on=[]),
        "presentation": dict(version="TEST_FIXTURE_V1", files=["display.py"], depends_on=["strategy"])})


def test_explicit_identity_changes_only_affected_components(tmp_path):
    (tmp_path/"strategy.py").write_text("VALUE = 1\n")
    (tmp_path/"display.py").write_text("TITLE = 'old'\n")
    old = operational.component_identity(tmp_path, spec())
    (tmp_path/"unrelated.py").write_text("OTHER = True\n")
    assert old["component_hashes"] == operational.component_identity(tmp_path, spec())["component_hashes"]
    (tmp_path/"display.py").write_text("TITLE = 'new'\n")
    new = operational.component_identity(tmp_path, spec())
    assert old["component_hashes"]["strategy"] == new["component_hashes"]["strategy"]
    assert old["component_hashes"]["presentation"] != new["component_hashes"]["presentation"]
    (tmp_path/"strategy.py").write_text("VALUE = 2\n")
    changed = operational.component_identity(tmp_path, spec())
    assert all(changed["component_hashes"][k] != new["component_hashes"][k] for k in new["component_hashes"])
    assert changed["legacy_verification_replaced"] is False
    assert changed["inspected_read_bytes"] == 2*sum(ref["bytes"] for ref in changed["evidence"])


@pytest.mark.parametrize("bad", ["../escape.py", "/tmp/escape.py", "strategy.py/../display.py", "strategy.pem"])
def test_component_paths_reject_escaping_or_private_files(tmp_path, bad):
    (tmp_path/"display.py").write_text("TITLE = 'TEST_FIXTURE'\n")
    value = spec(); value["components"]["strategy"]["files"] = [bad]
    with pytest.raises(ValueError):
        operational.component_identity(tmp_path, value)


def test_component_cycle_missing_dependency_and_link_rejected(tmp_path):
    (tmp_path/"strategy.py").write_text("VALUE=1")
    (tmp_path/"display.py").write_text("TITLE=1")
    value = spec(); value["components"]["strategy"]["depends_on"] = ["presentation"]
    with pytest.raises(ValueError, match="CYCLE_OR_MISSING"):
        operational.component_identity(tmp_path, value)
    value["components"]["strategy"]["depends_on"] = ["absent"]
    with pytest.raises(ValueError, match="CYCLE_OR_MISSING"):
        operational.component_identity(tmp_path, value)
    (tmp_path/"display.py").unlink(); (tmp_path/"display.py").symlink_to(tmp_path/"strategy.py")
    with pytest.raises((ValueError, OSError)):
        operational.component_identity(tmp_path, spec())


def test_performance_retains_failures_unknowns_and_wall_clock():
    reports = [dict(schema_version=delivery.VERSION, operation_id=str(i),
        workflow_status=status, timing=dict(utc_operation_seconds=wall,
        operation_action_monotonic_seconds=mono)) for i, (status, wall, mono) in enumerate([
            ("COMPLETE", 10, 10), ("FAILED_OR_UNKNOWN", 100, 20), ("IN_PROGRESS_OR_UNKNOWN", None, None)])]
    result = operational.performance_summary(reports)
    assert result["durations"]["utc_operation_seconds"]["median"] == 55
    assert result["durations"]["utc_operation_seconds"]["p95_nearest_rank"] == 100
    assert result["durations"]["utc_operation_seconds"]["missing_count"] == 1
    assert result["workflow_counts"]["FAILED_OR_UNKNOWN"] == 1
    assert result["end_to_end_speedup"] is None
    assert result["continuous_delivery_accepted"] is False
    with pytest.raises(ValueError, match="DUPLICATE"):
        operational.performance_summary(reports+reports[:1])
    reports[0]["timing"]["utc_operation_seconds"] = float("nan")
    with pytest.raises(ValueError, match="NONFINITE"):
        operational.performance_summary(reports)


def test_runtime_manifest_never_echoes_private_config_values(tmp_path):
    source = tmp_path/"source"; source.mkdir()
    config = tmp_path/"TEST_FIXTURE_configuration.json"
    config.write_text(json.dumps(dict(limits={"max_seconds": 300}, api_key="TEST_FIXTURE_SECRET")))
    calls = []
    runtime = SimpleNamespace(profile=dict(name="TEST_FIXTURE", backend="premarket",
        config=str(config), source_root=str(source), python="TEST_FIXTURE_python",
        state_root="TEST_FIXTURE_state", report_root="TEST_FIXTURE_reports"),
        scope={"TEST_FIXTURE": True}, check_binding=lambda: calls.append("checked"))
    result = operational.runtime_manifest(runtime)
    assert len(calls) == 2
    assert "TEST_FIXTURE_SECRET" not in json.dumps(result)
    assert result["registered_limits"] == {"max_seconds": 300}
    assert result["last_complete_business_acceptance"] is None
    assert all(row["reference"] is None for row in result["entry_inventory"])


def test_delivery_cli_publishes_new_private_report_and_refuses_overwrite(evidence, capsys):
    _, path, sha = attach_run(evidence)
    output = evidence.root/"TEST_FIXTURE_report.json"
    args = ["delivery-report", "--job", str(evidence.job), "--run", str(path),
            "--run-file-sha256", sha, "--output", str(output)]
    assert operational.main(args) == 0
    data = output.read_bytes()
    assert json.loads(data)["coverage"]["outcome_status"] == "NOT_PROVIDED"
    assert operational.main(args) == 2
    assert output.read_bytes() == data
    assert output.stat().st_mode & 0o777 == 0o600


def test_publication_rechecks_control_and_rolls_back_own_report(evidence, monkeypatch):
    _, path, sha = attach_run(evidence)
    output = evidence.root/"TEST_FIXTURE_report.json"
    publish = operational.io.publish_new
    def mutate_then_publish(destination, value, **kwargs):
        (evidence.job/"final-control.json").write_text('{"TEST_FIXTURE":"late change"}')
        return publish(destination, value, **kwargs)
    monkeypatch.setattr(operational.io, "publish_new", mutate_then_publish)
    assert operational.main(["delivery-report", "--job", str(evidence.job), "--run", str(path),
        "--run-file-sha256", sha, "--output", str(output)]) == 2
    assert not output.exists()


def test_explicit_routes_preserve_arguments_and_exit_without_retry(monkeypatch):
    import importlib
    calls = []
    def load(name):
        assert name == "scripts.codex_supervised_premarket_v3"
        return SimpleNamespace(main=lambda args: calls.append(list(args)) or 4)
    monkeypatch.setattr(importlib, "import_module", load)
    original = ["premarket", "--runtime", "TEST_FIXTURE", "--plan", "original.json", "--plan-sha256", "a"*64]
    assert operational.main(["supervised", "--", *original]) == 4
    assert calls == [original]


def test_component_cli_charges_reader_to_same_cumulative_budget(tmp_path, monkeypatch):
    (tmp_path/"strategy.py").write_text("VALUE=1")
    (tmp_path/"display.py").write_text("TITLE=1")
    source = tmp_path/"TEST_FIXTURE_spec.json"
    source.write_text(json.dumps(spec()))
    output = tmp_path/"TEST_FIXTURE_report.json"
    charges = []
    charge = operational.Inspection.charge
    def observe(self, count):
        charges.append(count)
        return charge(self, count)
    monkeypatch.setattr(operational.Inspection, "charge", observe)
    assert operational.main(["component-manifest", "--source-root", str(tmp_path),
        "--spec", str(source), "--spec-file-sha256", hashlib.sha256(source.read_bytes()).hexdigest(),
        "--output", str(output)]) == 0
    assert 2*(len(b"VALUE=1")+len(b"TITLE=1")) in charges
