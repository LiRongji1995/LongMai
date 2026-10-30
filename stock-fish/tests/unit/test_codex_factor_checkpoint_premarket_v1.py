"""TEST_FIXTURE: the new producer cannot escape the existing request journal."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts import codex_factor_checkpoint_premarket_v1 as entry
from scripts import codex_runtime_recovery_v1 as recovery
from scripts import codex_workspace as workspace


class FixtureRuntime(recovery.RecoveryRuntime):
    def __init__(self, root):
        self.profile = dict(backend="premarket", source_root=str(root / "original"),
                            state_root=str(root / "state"), config=str(root / "config.json"))
        self.saved_profile = deepcopy(self.profile)
        self.binding = {"TEST_FIXTURE": True}
        self.scope = dict(backend="premarket", device=1, inode=2)
        self.registry_root = root / "registry"
        self.directory = self.registry_root / "scopes" / "TEST_FIXTURE"
        self.resources = ["TEST_FIXTURE_original_resource"]
        self.checks = self.locks = 0
        self.lock_hook = None

    def check_binding(self):
        assert self.profile == self.saved_profile
        self.checks += 1

    @contextmanager
    def exclusive(self):
        self.check_binding()
        self.locks += 1
        if self.lock_hook:
            self.lock_hook()
        yield
        self.check_binding()

    def preflight(self, **kwargs):
        self.check_binding()
        return dict(allowed=True, reason_codes=[], expected_growth_bytes=1024,
                    min_free_bytes=1, max_managed_bytes=1024**3,
                    projected_unique_logical_bytes=2048, destination_checks=[])

    def guard_ancestor_request(self, selected):
        assert self.locks > 0


class FixtureGuard:
    def __init__(self, root, runtime):
        self.directory = root / "TEST_FIXTURE_bundle"
        self.runtime = runtime
        self.snapshot = {"TEST_FIXTURE": True, "manifest_sha256": "a" * 64}
        self.failed = False

    def check(self):
        self.runtime.check_binding()
        if self.failed:
            raise ValueError("TEST_FIXTURE_SOURCE_CHANGED")


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "TEST_FIXTURE_factor_gateway"
    root.mkdir()
    base = FixtureRuntime(root)
    guard = FixtureGuard(root, base)
    return base, guard, entry.BundleRuntime(base, guard)


def identity(**changes):
    return dict(dict(schema_version=entry.VERSION, backend="premarket",
        observation_date="2026-10-30", target_session="2026-11-02",
        request_key="TEST_FIXTURE_original_request", source_closure_sha256="a" * 64), **changes)


def journal(runtime, selected, phase="FAILED_OR_UNKNOWN"):
    key = workspace._digest({"scope": runtime.scope, "identity": selected})
    path = runtime.directory / "operations" / (key + ".json")
    workspace._write(path, dict(schema_version=workspace.VERSION, scope=runtime.scope,
        identity=selected, operation_id=key, phase=phase))
    return path


def test_execution_profile_does_not_replace_registered_binding_or_resources(setup):
    base, guard, runtime = setup
    assert runtime.profile["source_root"] == str(guard.directory)
    assert base.profile == base.saved_profile
    assert runtime.profile["state_root"] == base.profile["state_root"]
    assert runtime.profile["config"] == base.profile["config"]
    for name in ("binding", "scope", "resources", "directory", "registry_root"):
        assert getattr(runtime, name) is getattr(base, name)


def test_facade_cannot_upgrade_unattested_runtime_to_recovery_profile(setup):
    _, guard, _ = setup
    ordinary = object.__new__(workspace.Runtime)
    with pytest.raises(ValueError, match="ATTESTED_RUNTIME_REQUIRED"):
        entry.BundleRuntime(ordinary, guard)


def test_success_reuses_same_original_scope_without_another_action(setup):
    base, _, runtime = setup
    calls = []
    def action():
        calls.append("TEST_FIXTURE_action")
        return {"TEST_FIXTURE": True}
    first = runtime.run_once(identity(), action, lambda _: True)
    second = runtime.run_once(identity(), action, lambda _: True)
    assert first["status"] == "RECORDED" and second["status"] == "REUSED"
    assert first["operation_id"] == second["operation_id"]
    assert calls == ["TEST_FIXTURE_action"]
    assert base.locks == 2 and base.checks > 2


def test_failure_is_retained_and_same_identity_cannot_retry(setup):
    _, _, runtime = setup
    calls = []
    def action():
        calls.append(1)
        raise ValueError("TEST_FIXTURE_provider_failure")
    with pytest.raises(ValueError, match="provider_failure"):
        runtime.run_once(identity(), action, lambda _: True)
    paths = list((runtime.directory / "operations").glob("*.json"))
    assert len(paths) == 1
    before = paths[0].read_bytes()
    assert workspace._read(paths[0])["phase"] == "FAILED_OR_UNKNOWN"
    with pytest.raises(ValueError, match="STATE_UNKNOWN_RECONCILE_NO_RETRY"):
        runtime.run_once(identity(), action, lambda _: True)
    assert paths[0].read_bytes() == before and calls == [1]


@pytest.mark.parametrize("changes", [
    {"source_closure_sha256": "b" * 64},
    {"request_key": "TEST_FIXTURE_different_key"},
    {"schema_version": "TEST_FIXTURE_NEW_VERSION"},
    {"observation_date": "2026-10-29", "target_session": "2026-10-30"},
])
@pytest.mark.parametrize("phase", ["FAILED_OR_UNKNOWN", "SUBMITTING", "RECORDED"])
def test_new_source_key_version_or_dates_never_hide_prior_request(setup, changes, phase):
    _, _, runtime = setup
    path = journal(runtime, identity(), phase)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="EXISTING_REQUEST_RECONCILE_NO_RETRY"):
        runtime.run_once(identity(**changes), lambda: pytest.fail("must not execute"), lambda _: True)
    assert path.read_bytes() == before
    assert len(list(path.parent.glob("*.json"))) == 1


def test_locked_recheck_blocks_race_before_new_submitting_record(setup):
    base, _, runtime = setup
    base.lock_hook = lambda: journal(runtime, identity(source_closure_sha256="b" * 64))
    with pytest.raises(ValueError, match="EXISTING_REQUEST_RECONCILE_NO_RETRY"):
        runtime.run_once(identity(), lambda: pytest.fail("must not execute"), lambda _: True)
    assert len(list((runtime.directory / "operations").glob("*.json"))) == 1


def test_ancestor_guard_still_runs_under_real_lock(setup):
    base, guard, _ = setup
    class FixtureRecovery(FixtureRuntime):
        def guard_ancestor_request(self, selected):
            assert self.locks > 0
            raise ValueError("TEST_FIXTURE_ANCESTOR_NO_RETRY")
    recovered = FixtureRecovery(base.directory.parent)
    runtime = entry.BundleRuntime(recovered, FixtureGuard(guard.directory, recovered))
    with pytest.raises(ValueError, match="ANCESTOR_NO_RETRY"):
        runtime.run_once(identity(), lambda: pytest.fail("must not execute"), lambda _: True)
    assert not runtime.directory.exists()


def test_invalid_operation_binding_is_not_ignored(setup):
    _, _, runtime = setup
    path = journal(runtime, identity())
    value = workspace._read(path)
    value["scope"] = {"TEST_FIXTURE": "foreign_scope"}
    workspace._write(path, value, replace=True)
    with pytest.raises(ValueError, match="OPERATION_BINDING_INVALID"):
        runtime.run_once(identity(), lambda: pytest.fail("must not execute"), lambda _: True)


def test_source_mutation_during_action_preserves_failed_journal(setup):
    _, guard, runtime = setup
    def action():
        guard.failed = True
        return {"TEST_FIXTURE": True}
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        runtime.run_once(identity(), action, lambda _: True)
    path, = (runtime.directory / "operations").glob("*.json")
    assert workspace._read(path)["phase"] == "FAILED_OR_UNKNOWN"


def test_frozen_function_globals_are_not_monkeypatched(setup, monkeypatch):
    _, guard, _ = setup
    original_snapshot, original_prior = entry.daily._snapshot, entry.daily._prior_request
    function = entry.bound_functions(guard)
    assert entry.daily._snapshot is original_snapshot
    assert entry.daily._prior_request is original_prior
    assert entry.bounded.run.__globals__["daily"] is entry.daily
    assert function.__globals__["daily"]._prior_request is entry.prior_request
    assert function.__code__ is entry.bounded.run.__code__


def test_parser_requires_explicit_source_profile_and_has_no_engine_or_retry():
    args = ["--runtime", "TEST_FIXTURE", "--bundle", "/TEST_FIXTURE_bundle",
            "--manifest-sha256", "a" * 64, "--profile", entry.bounded.PROFILE,
            "premarket", "--observation-date", "2026-10-30", "--target-session", "2026-11-02",
            "--request-key", "TEST_FIXTURE_request"]
    parsed = entry.parser().parse_args(args)
    assert parsed.command == "premarket"
    for extra in (["--retry-failed"], ["--engine", "shared"]):
        with pytest.raises(SystemExit):
            entry.parser().parse_args([*args, *extra])
    with pytest.raises(SystemExit):
        entry.parser().parse_args([x for x in args if x not in ("--profile", entry.bounded.PROFILE)])


def test_execute_refuses_wrong_source_process_before_any_action(setup):
    base, guard, _ = setup
    with pytest.raises(ValueError, match="BUNDLE_PROCESS_REQUIRED"):
        entry.execute(base, guard, SimpleNamespace(command="premarket"))


@pytest.mark.parametrize("failed", [False, True])
def test_bound_original_run_uses_real_journal_and_receipt_validation(tmp_path, monkeypatch, failed):
    # Reuse the original full-receipt TEST_FIXTURE, including on-disk final,
    # completion, code registry and display files. Only provider work is faked.
    from tests.unit.test_codex_daily_entry_v2 import GatewayTests
    case = GatewayTests()
    case.setUp()
    try:
        base = FixtureRuntime(tmp_path / "TEST_FIXTURE_bound_gateway")
        base.profile.update(source_root=str(entry.ROOT), python=str(Path(sys.executable)),
                            state_root=str(case.state), config=str(case.config))
        base.saved_profile = deepcopy(base.profile)
        guard = FixtureGuard(tmp_path, base)
        guard.directory = entry.ROOT
        args = SimpleNamespace(command="premarket", profile=entry.bounded.PROFILE,
            observation_date=case.observation, target_session=case.target,
            request_key="TEST_FIXTURE_actual_bound_run")
        monkeypatch.setattr(entry.bounded, "configuration", lambda runtime, profile:
                            entry.bounded.frozen.load_config(runtime.profile["config"]))
        calendar_calls = []
        def calendar(prepared, config, observation, target, budget):
            assert base.locks > 0
            assert (observation, target) == (str(case.observation), str(case.target))
            calendar_calls.append(1)
            return {"TEST_FIXTURE_calendar": True}
        monkeypatch.setattr(entry.bounded, "calendar_gate", calendar)
        def supervise(config, *, progress, started_at, expected_sessions, engine):
            assert progress is True and engine == "original"
            code, final = case.supervise(config, progress=False, started_at=started_at,
                                        expected_sessions=expected_sessions, engine=engine)
            if failed:
                final.update(status="FAILED", external_worker_exit_code=4)
                Path(final["job"], "final-control.json").write_text(json.dumps(final))
                return 4, final
            return code, final
        workflow = SimpleNamespace(supervise=supervise,
                                   _expected_gate=entry.bounded.workflow._expected_gate)
        monkeypatch.setattr(entry.bounded, "workflow", workflow)
        if failed:
            with pytest.raises(ValueError, match="EXECUTION_FAILED_RETAINED_NO_RETRY"):
                entry.execute(base, guard, args)
            record, = (base.directory / "operations").glob("*.json")
            assert workspace._read(record)["phase"] == "FAILED_OR_UNKNOWN"
            saved = Path(case.state, "jobs/TEST_FIXTURE_1/final-control.json").read_bytes()
            with pytest.raises(ValueError, match="STATE_UNKNOWN_RECONCILE_NO_RETRY"):
                entry.execute(base, guard, args)
            assert Path(case.state, "jobs/TEST_FIXTURE_1/final-control.json").read_bytes() == saved
            assert case.calls == 1
        else:
            code, value = entry.execute(base, guard, args)
            assert code == 2 and value["status"] == "RECORDED"
            assert value["validation"]["complete_daily_delivery"] is True
            assert value["source_bundle"] == guard.snapshot
            assert value["registered_runtime_changed"] is False
            record, = (base.directory / "operations").glob("*.json")
            snapshot = {**case.snapshot, "factor_source_epoch": guard.snapshot}
            expected = hashlib.sha256(entry.original._canonical(snapshot)).hexdigest()
            assert workspace._read(record)["identity"]["source_closure_sha256"] == expected
            _, reused = entry.execute(base, guard, args)
            assert reused["status"] == "REUSED" and case.calls == 1
        assert calendar_calls
        assert base.profile == base.saved_profile
    finally:
        case.doCleanups()
