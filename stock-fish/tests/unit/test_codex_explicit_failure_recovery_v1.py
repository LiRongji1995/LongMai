"""TEST_FIXTURE append-only recovery; no production state, SDK or network."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import sys

import pytest

from scripts import codex_explicit_failure_recovery_v1 as entry
from scripts import codex_recovered_premarket_v1 as bounded


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("TEST_FIXTURE_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(entry.bundle.previous.canonical(value))
    return entry.bundle.read(path)[1]


class FixtureRuntime(entry.recovery.RecoveryRuntime):
    def check_binding(self):
        if getattr(self, "binding_failed", False):
            raise ValueError("TEST_FIXTURE_BINDING_CHANGED")

    @contextmanager
    def exclusive(self):
        self.check_binding()
        self.lock_depth += 1
        try:
            yield
            if self.fail_lock_exit:
                raise ValueError("TEST_FIXTURE_LOCK_EXIT_CHANGED")
        finally:
            self.lock_depth -= 1

    def preflight(self, **kwargs):
        assert self.lock_depth > 0
        return {"allowed": self.capacity_allowed}


class FixtureCollector:
    def __init__(self, runtime, original_limits):
        self.runtime = runtime
        self.original_limits = original_limits
        self.calls = 0
        self.verify_calls = 0
        self.complete = True
        self.fail = False
        self.fail_verification_at = None
        self.scope = "TEST_FIXTURE_SCOPE_A"

    def budget_baseline(self, runtime, old_job):
        remaining = dict(self.original_limits)
        remaining.update(max_seconds=8220, max_requests=999, max_sdk_calls=9052, max_http_attempts=32576)
        remaining["capture_read_bytes"] -= 2998127258
        remaining["supervisor_read_bytes"] -= 46510618
        return dict(original_limits=self.original_limits, remaining_limits=remaining,
            consumed=dict(requests=25, sdk_reserved=948, http_reserved=192, wall_seconds=2580),
            references=[entry.bundle.read(old_job / "final-control.json")[1]])

    def plan(self, runtime, bundle_directory, manifest_sha256, old_job, *, budget):
        budget.charge_read(31)
        return dict(plan_hash=entry._digest(self.scope), source_role=entry.ROLE,
            requested_codes=["sh.600001"], original_request_key="TEST_FIXTURE_original_key")

    def collect(self, source_plan, job, budget, *, checkpoint):
        self.calls += 1
        assert self.runtime.lock_depth > 0
        slot = self.runtime.directory / "explicit-failure-recoveries" / self.runtime.operation_id
        claim = entry._read(slot / "claim.json")[0]
        assert claim["source_job"] == str(job) and claim["request_key"] == "TEST_FIXTURE_original_key"
        assert job.is_dir()
        checkpoint(dict(stage="BEFORE_REQUEST", request_key="TEST_FIXTURE_factor_request"))
        if self.fail:
            raise RuntimeError("TEST_FIXTURE_private_exception_do_not_publish")
        budget.preclaim_request("adjustment_factors", dict(codes=["sh.600001"], start_date="1990-01-01", end_date="2026-10-28"))
        # No actual provider: do not pretend this fixture reservation is a real source.
        payload = dict(source_complete=self.complete, source_role=entry.ROLE,
            requested_codes_count=1, complete_codes_count=int(self.complete),
            historical_pit_restored=False, first_seen_at=entry._stamp())
        reference = write(job / "TEST_FIXTURE_result.json", payload)
        checkpoint(dict(stage="AFTER_RESULT", result=reference))
        return {**payload, "reference": reference, "source_references": [reference]}

    def verify_result(self, result, plan, job, budget):
        self.verify_calls += 1
        if self.fail_verification_at == self.verify_calls:
            raise ValueError("TEST_FIXTURE_TERMINAL_CHANGED")
        entry._reference(result["reference"])
        assert result["historical_pit_restored"] is False
        return True


def make_case(tmp_path, monkeypatch):
    """Reusable isolated failure factory for independent reference tests."""
    root = (tmp_path / "TEST_FIXTURE_explicit_recovery").resolve()
    state = root / "state"
    state.mkdir(parents=True)
    config = bounded.frozen.load_config(overrides=dict(state_root=str(state), limits=dict(bounded.CAPS),
        capture=dict(offline=False, retry_failed=False, allow_test_fixture=False)))
    config_ref = write(root / "configuration.json", config)
    runtime = FixtureRuntime.__new__(FixtureRuntime)
    runtime.registry_root = root / "registry"
    runtime.scope = dict(backend="premarket", device=state.stat().st_dev, inode=state.stat().st_ino)
    runtime.directory = runtime.registry_root / "scopes" / entry.workspace._digest(runtime.scope)
    runtime.profile = dict(name="TEST_FIXTURE_recovered", backend="premarket", state_root=str(state),
        source_root=str(entry.ROOT), config=str(root / "configuration.json"), python=sys.executable,
        storage=dict(expected_growth_bytes=1024, roots=[dict(name="TEST_FIXTURE", path=str(root))]))
    runtime.recovery_plan = {"test_fixture": True}
    runtime.lock_depth, runtime.fail_lock_exit, runtime.capacity_allowed = 0, False, True
    job = state / "jobs" / "TEST_FIXTURE_original_failed_job"
    job.mkdir(parents=True)
    original_code = write(root / "TEST_FIXTURE_original_producer.json", {"source": "TEST_FIXTURE"})
    code_ref = write(job / "code-registry.json", [original_code])
    job_config_ref = write(job / "configuration.json", config)
    completion_ref = write(job / "worker-completion.json", dict(schema_version="CODEX_PREMARKET_SHARED_WORKFLOW_V1",
        status="FAILED", core_verified=False, execution_engine="original",
        expected_sessions=["2026-10-28", "2026-10-29", "2026-10-29"],
        phases={name: "FAILED" if name == "capture" else "NOT_RUN" for name in
            ("capture", "core", "news", "theme_sources", "theme", "presentation")}))
    now = datetime.now(timezone.utc)
    final = dict(schema_version="CODEX_PREMARKET_SHARED_WORKFLOW_V1", status="FAILED", job=str(job),
        external_worker_reaped=True, external_worker_exit_code=3, implementation_epoch_verified=True,
        completion=completion_ref, configuration=job_config_ref, code_registry=code_ref,
        final_sealed_at=(now-timedelta(seconds=90)).isoformat())
    write(job / "final-control.json", final)
    identity = dict(schema_version="CODEX_RECOVERED_PREMARKET_V1", backend="premarket",
        request_key="TEST_FIXTURE_original_key", observation_date="2026-10-28", target_session="2026-10-29",
        execution_engine="original", explicit_collection_profile=bounded.PROFILE, configuration=config_ref)
    operation_id = entry.workspace._digest(dict(scope=runtime.scope, identity=identity))
    operation = runtime.directory / "operations" / (operation_id + ".json")
    write(operation, dict(schema_version=entry.workspace.VERSION, scope=runtime.scope, identity=identity,
        operation_id=operation_id, phase="FAILED_OR_UNKNOWN", started_at=(now-timedelta(seconds=120)).isoformat(),
        finished_at=(now-timedelta(seconds=60)).isoformat()))
    runtime.operation_id = operation_id
    producer = FixtureCollector(runtime, config["limits"])
    manifest = write(root / "TEST_FIXTURE_bundle.json", {"source": "TEST_FIXTURE"})
    guard = dict(bundle_root=str(root), manifest=manifest, verification={"status": "TEST_FIXTURE"},
        external_sources=[original_code], source_capability_issued=False)
    def fixture_guard(*args, **kwargs):
        entry._references(guard["external_sources"])
        entry._reference(guard["manifest"])
        return deepcopy(guard)
    monkeypatch.setattr(entry, "_source_guard", fixture_guard)
    return dict(root=root, runtime=runtime, collector=producer, operation=operation, job=job,
        guard=guard, config=config, prepare=dict(operation_path=operation, job_path=job,
            bundle_directory=root, manifest_sha256=manifest["sha256"], allow_test_fixture=True, collector=producer))


@pytest.fixture
def case(tmp_path, monkeypatch):
    return make_case(tmp_path, monkeypatch)


def plan_case(case):
    return entry.prepare(case["runtime"], **case["prepare"])


def execute_case(case, plan, **kwargs):
    return entry.execute(case["runtime"], plan, allow_test_fixture=True, collector=case["collector"], **kwargs)


def test_prepare_is_readonly_and_deducts_original_and_planning_budgets(case):
    original = entry.bundle.read(case["operation"])[1]
    plan = plan_case(case)
    assert not Path(plan["slot"]).exists() and not Path(plan["source_job"]).exists()
    assert case["collector"].calls == 0
    assert entry.bundle.read(case["operation"])[1] == original
    limits = entry._execution_limits(plan)
    assert limits["max_seconds"] == 8220 - plan["planning"]["conservative_elapsed_seconds"]
    assert limits["max_sdk_calls"] == 9052 and limits["max_http_attempts"] == 32576
    assert plan["failure"]["job_binding"] == "EXPLICIT_CURRENT_RECONCILIATION_NOT_ORIGINAL_RECEIPT"
    assert plan["complete_daily_delivery"] is False


def test_complete_keeps_original_failure_and_request_key_and_cannot_repeat(case):
    original = entry.bundle.read(case["operation"])[1]
    plan = plan_case(case)
    result = execute_case(case, plan)
    assert result["status"] == entry.SUCCESS
    assert result["request_key"] == "TEST_FIXTURE_original_key"
    assert result["complete_daily_delivery"] is result["historical_pit_restored"] is False
    assert entry.bundle.read(case["operation"])[1] == original
    assert entry.status(case["runtime"], plan["operation_id"])["status"] == entry.SUCCESS
    with pytest.raises(ValueError, match="EXISTING_SLOT"):
        execute_case(case, plan)
    assert case["collector"].calls == 1


def test_failed_runner_leaves_claim_and_failure_without_disclosing_exception(case):
    plan = plan_case(case)
    case["collector"].fail = True
    result = execute_case(case, plan)
    assert result["status"] == entry.FAILED
    assert "private_exception" not in json.dumps(result)
    assert (Path(plan["slot"]) / "claim.json").exists()
    assert not (Path(plan["slot"]) / "completion.json").exists()
    with pytest.raises(ValueError, match="EXISTING_SLOT"):
        execute_case(case, plan)
    assert case["collector"].calls == 1


def test_partial_is_not_complete_and_is_not_retried(case):
    plan = plan_case(case)
    case["collector"].complete = False
    result = execute_case(case, plan)
    assert result["status"] == entry.PARTIAL
    assert result["complete_daily_delivery"] is False
    with pytest.raises(ValueError, match="EXISTING_SLOT"):
        execute_case(case, plan)


@pytest.mark.parametrize("phase", ["SUBMITTING", "RECORDED"])
def test_lost_or_completed_original_operation_cannot_be_recovered(case, phase):
    value = entry._read(case["operation"])[0]
    value["phase"] = phase
    write(case["operation"], value)
    with pytest.raises(ValueError, match="ORIGINAL_FAILED_OPERATION"):
        plan_case(case)


def test_unreaped_original_worker_refused(case):
    path = case["job"] / "final-control.json"
    value = entry._read(path)[0]
    value["external_worker_reaped"] = False
    write(path, value)
    with pytest.raises(ValueError, match="REAPED_FAILED_JOB"):
        plan_case(case)


def test_changed_source_scope_before_claim_refused(case):
    plan = plan_case(case)
    case["collector"].scope = "TEST_FIXTURE_CHANGED"
    with pytest.raises(ValueError, match="SOURCE_SCOPE_CHANGED"):
        execute_case(case, plan)
    assert not Path(plan["slot"]).exists()
    assert case["collector"].calls == 0


def test_changed_operation_id_cannot_create_a_second_slot(case):
    plan = plan_case(case)
    plan["operation_id"] = "f" * 64
    slot, job = entry._paths(case["runtime"], plan["operation_id"])
    plan["slot"], plan["source_job"] = str(slot), str(job)
    plan["plan_id"] = entry._plan_key(plan)
    with pytest.raises(ValueError, match="PLAN_SCOPE_CHANGED"):
        execute_case(case, plan)
    assert case["collector"].calls == 0


def test_changed_request_key_cannot_recover_original_operation(case):
    plan = plan_case(case)
    plan["request_key"] = "TEST_FIXTURE_changed_key"
    plan["plan_id"] = entry._plan_key(plan)
    with pytest.raises(ValueError, match="PLAN_SCOPE_CHANGED"):
        execute_case(case, plan)
    assert case["collector"].calls == 0


def test_existing_empty_slot_is_unknown_and_never_restarted(case):
    plan = plan_case(case)
    Path(plan["slot"]).mkdir(parents=True)
    assert entry.status(case["runtime"], plan["operation_id"])["status"] == "INTERRUPTED_SLOT_RECONCILE_NO_RETRY"
    with pytest.raises(ValueError, match="EXISTING_SLOT"):
        execute_case(case, plan)
    assert case["collector"].calls == 0


def test_capacity_refusal_does_not_claim(case):
    plan = plan_case(case)
    case["runtime"].capacity_allowed = False
    with pytest.raises(ValueError, match="CAPACITY_BLOCKED"):
        execute_case(case, plan)
    assert not Path(plan["slot"]).exists()


def test_post_publication_failure_revokes_success(case):
    plan = plan_case(case)
    case["collector"].fail_verification_at = 2
    result = execute_case(case, plan)
    slot = Path(plan["slot"])
    assert result["status"] == entry.FAILED
    assert not (slot / "completion.json").exists()
    assert (slot / "rejected-completion.json").exists()
    assert entry.status(case["runtime"], plan["operation_id"])["status"] == entry.FAILED


def test_lock_exit_failure_revokes_success(case):
    plan = plan_case(case)
    case["runtime"].fail_lock_exit = True
    result = execute_case(case, plan)
    assert result["status"] == entry.FAILED
    assert not (Path(plan["slot"]) / "completion.json").exists()


def test_plan_file_is_physically_pinned(case):
    plan = plan_case(case)
    ref = write(case["root"] / "TEST_FIXTURE_saved_plan.json", plan)
    Path(ref["path"]).write_bytes(Path(ref["path"]).read_bytes())
    with pytest.raises(ValueError, match="PHYSICAL_REFERENCE_CHANGED"):
        execute_case(case, plan, plan_reference=ref)


def test_negative_planning_reads_cannot_expand_budget(case):
    plan = plan_case(case)
    plan["planning"]["metrics"]["returned_read_bytes"]["capture"] = -1
    plan["plan_id"] = entry._plan_key(plan)
    with pytest.raises(ValueError, match="PLANNING_READ_ACCOUNTING"):
        execute_case(case, plan)


def test_fixture_injection_not_available_to_production_runtime(case):
    case["runtime"].recovery_plan["test_fixture"] = False
    with pytest.raises(ValueError, match="COLLABORATOR_REQUIRES_TEST_FIXTURE"):
        plan_case(case)


def test_ancestor_symlink_cannot_select_another_operation(case):
    alias = case["root"] / "alias"
    alias.symlink_to(case["operation"].parent, target_is_directory=True)
    with pytest.raises(ValueError, match="OPERATION_OUTSIDE_ORIGINAL_SCOPE"):
        entry.prepare(case["runtime"], **{**case["prepare"], "operation_path": alias / case["operation"].name})


def test_remaining_time_takes_actual_utc_wait_even_when_monotonic_is_short(case):
    budget, _ = entry._new_budget(case["runtime"])
    budget.limits["max_seconds"] = 60
    budget.utc_started_at -= timedelta(seconds=40)
    assert 0 < budget.remaining_seconds() < 21
    budget.utc_started_at -= timedelta(seconds=30)
    with pytest.raises(bounded.frozen.BudgetExceeded, match="UTC_WALL_TIME"):
        budget.check("TEST_FIXTURE_next_request", force=True)


def test_backwards_utc_clock_is_rejected_before_next_request(case):
    budget, _ = entry._new_budget(case["runtime"])
    budget.utc_started_at += timedelta(seconds=10)
    with pytest.raises(bounded.frozen.BudgetExceeded, match="UTC_CLOCK_MOVED_BACKWARD"):
        budget.remaining_seconds()


def test_another_key_for_same_business_day_blocks_selected_failure_recovery(case):
    record = entry._read(case["operation"])[0]
    record["identity"]["request_key"] = "TEST_FIXTURE_second_key_same_day"
    record["operation_id"] = entry.workspace._digest(dict(scope=case["runtime"].scope, identity=record["identity"]))
    write(case["operation"].parent / (record["operation_id"] + ".json"), record)
    with pytest.raises(ValueError, match="OTHER_BUSINESS_REQUEST_CONFLICT"):
        plan_case(case)
    assert case["collector"].calls == 0


def test_rollback_error_is_recorded_and_never_prevents_failure_terminal(case, monkeypatch):
    plan = plan_case(case)
    case["collector"].fail_verification_at = 2
    def unavailable(*args):
        raise OSError("TEST_FIXTURE_private_location")
    monkeypatch.setattr(entry, "_revoke_completion", unavailable)
    result = execute_case(case, plan)
    assert result["status"] == entry.FAILED
    assert result["revoke_error"]["error_reason"] == "OSError"
    assert entry.status(case["runtime"], plan["operation_id"])["status"] == entry.FAILED


def test_saved_completion_boolean_conflict_rejected(case):
    plan = plan_case(case)
    assert execute_case(case, plan)["status"] == entry.SUCCESS
    path = Path(plan["slot"]) / "completion.json"
    value = entry._read(path)[0]
    value["result"]["source_complete"] = False
    path.chmod(0o600)
    write(path, value)
    with pytest.raises(ValueError, match="SAVED_COMPLETION_STATUS_CHANGED"):
        entry.status(case["runtime"], plan["operation_id"])
