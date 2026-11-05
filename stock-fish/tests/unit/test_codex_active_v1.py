"""TEST_FIXTURE routing only; never runs a real source capture or activation."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import codex_active_v1 as subject
from scripts import daily_delivery_contract_v1 as contract
from scripts import business_authority_v1 as authority_module
from tests.unit.test_active_runtime_v1 import active, child


@pytest.mark.parametrize("route,module", list(subject.ROUTES.items()))
def test_thin_routes_preserve_exact_arguments_and_exit_code(monkeypatch, route, module):
    seen = []
    def load(name):
        assert name == module
        return SimpleNamespace(main=lambda argv: seen.append(argv) or 7)
    monkeypatch.setattr(subject, "_module", load)
    arguments = ["--TEST_FIXTURE", "original-key"]
    assert subject.main([route, *arguments]) == 7
    assert seen == [[route, *arguments] if route in {"build", "verify"} else arguments]


def dispatch_fixture(tmp_path, monkeypatch):
    source = (tmp_path / "TEST_FIXTURE_active").resolve(); source.mkdir()
    registry = (tmp_path / "TEST_FIXTURE_registry").resolve(); registry.mkdir()
    manifest = dict(output_sha256={"scripts/codex_daily_v2.py": "a"*64})
    seen = []
    runtime = SimpleNamespace(profile=dict(backend="premarket", source_root=str(source), python="/TEST_FIXTURE/registered/python"),
        active_source_attested=True, check_binding=lambda: seen.append("binding"))
    active = SimpleNamespace(verify=lambda path: seen.append(("verify", path)) or manifest)
    recovery = SimpleNamespace(load_runtime=lambda name, root: seen.append(("runtime", name, root)) or runtime)
    gateway = SimpleNamespace(load_runtime=lambda name, root: seen.append(("bound_control_read", name, root)) or runtime)
    monkeypatch.setattr(subject, "_module", lambda name: {"active_runtime_v1":active,"codex_runtime_recovery_v2":recovery,
        "active_business_gateway_v1":gateway}[name])
    def run(argv, **kwargs):
        seen.append(("child", argv, kwargs))
        return SimpleNamespace(returncode=4)
    monkeypatch.setattr(subject.subprocess, "run", run)
    args = ["--source-root", str(source), "--runtime", "TEST_FIXTURE_registered", "--registry-root", str(registry)]
    return source, registry, runtime, active, seen, args


@pytest.mark.parametrize("engine", [None, "shared"])
def test_premarket_uses_only_verified_registered_tree_once(tmp_path, monkeypatch, engine):
    source, registry, runtime, active, seen, args = dispatch_fixture(tmp_path, monkeypatch)
    args += ["--observation-date", "2026-11-05", "--target-session", "2026-11-06", "--request-key", "TEST_FIXTURE_ORIGINAL_KEY"]
    if engine: args += ["--engine", engine]
    assert subject.main(["premarket", *args]) == 4
    children = [s for s in seen if isinstance(s, tuple) and s[0] == "child"]
    assert len(children) == 1
    assert children[0][1] == [runtime.profile["python"], "-I", "-B", str(source/"scripts/codex_daily_v2.py"),
        "--runtime", "TEST_FIXTURE_registered", "--registry-root", str(registry), "premarket",
        "--observation-date", "2026-11-05", "--target-session", "2026-11-06", "--request-key", "TEST_FIXTURE_ORIGINAL_KEY",
        "--engine", engine or "original"]
    assert children[0][2] == dict(cwd=source)
    assert seen.index("binding") < seen.index(children[0])
    assert seen[-1] == ("verify", source)


@pytest.mark.parametrize("daily", [False, True])
def test_native_outcomes_use_same_registered_tree_and_original_request_once(tmp_path, monkeypatch, daily):
    source, registry, runtime, active, seen, args = dispatch_fixture(tmp_path, monkeypatch)
    active.verify(source)["output_sha256"]["scripts/active_outcome_pipeline_v1.py"] = "b"*64
    fields = {"parent-business-id":"TEST_FIXTURE_PARENT", "signal-job":"TEST_FIXTURE_SIGNAL",
        "signal-final-sha256":"c"*64, "outcome-job":"TEST_FIXTURE_LATER",
        "outcome-final-sha256":"d"*64, "destination":"TEST_FIXTURE_NEW_OUTPUT", "request-key":"ORIGINAL_KEY"}
    forwarded = [item for key,value in fields.items() for item in ("--"+key,value)]
    if daily:
        forwarded += ["--daily-archives", "TEST_FIXTURE_daily_sources.json", "--daily-archives-sha256", "e"*64]
    assert subject.main(["outcomes", *args, *forwarded]) == 4
    children = [row for row in seen if isinstance(row,tuple) and row[0] == "child"]
    assert len(children) == 1
    assert children[0][1] == [runtime.profile["python"], "-I", "-B", str(source/"scripts/active_outcome_pipeline_v1.py"),
        "--runtime", "TEST_FIXTURE_registered", "--registry-root", str(registry), *forwarded]
    seen.clear()
    assert subject.main(["outcomes", *args, *forwarded, "--signal-job", "TEST_FIXTURE_ALTERNATIVE"]) == 2
    assert not any(isinstance(row,tuple) and row[0] == "child" for row in seen)
    if daily:
        assert subject.main(["outcomes", *args, *forwarded[:-2]]) == 2
        assert not any(isinstance(row,tuple) and row[0] == "child" for row in seen)


@pytest.mark.parametrize("fault", ["unverified", "different_source", "unattested", "duplicate", "missing_source"])
def test_no_fallback_or_child_on_wrong_or_implicit_identity(tmp_path, monkeypatch, fault):
    source, registry, runtime, active, seen, args = dispatch_fixture(tmp_path, monkeypatch)
    if fault == "unverified":
        active.verify = lambda _: (_ for _ in ()).throw(ValueError("TEST_FIXTURE_INCOMPLETE_ACTIVE_TREE"))
    elif fault == "different_source": runtime.profile["source_root"] = str(tmp_path)
    elif fault == "unattested": runtime.active_source_attested = False
    elif fault == "duplicate": args += ["--runtime", "TEST_FIXTURE_ALTERNATIVE"]
    else: args = args[2:]
    if fault == "missing_source":
        with pytest.raises(SystemExit) as caught: subject.main(["premarket-plan", *args])
        assert caught.value.code == 2
    else:
        assert subject.main(["premarket-plan", *args]) == 2
    assert not any(isinstance(row, tuple) and row[0] == "child" for row in seen)


def test_terminal_source_change_is_failure_without_second_child(tmp_path, monkeypatch):
    source, registry, runtime, active, seen, args = dispatch_fixture(tmp_path, monkeypatch)
    original, calls = active.verify, [0]
    def verify(path):
        calls[0] += 1
        if calls[0] == 3: raise ValueError("TEST_FIXTURE_SOURCE_CHANGED_AFTER_RUN")
        return original(path)
    active.verify = verify
    assert subject.main(["premarket-plan", *args]) == 2
    assert len([row for row in seen if isinstance(row, tuple) and row[0] == "child"]) == 1


def test_active_tree_driver_uses_existing_narrow_gateway_control_read(tmp_path, monkeypatch):
    source, registry, runtime, active, seen, args = dispatch_fixture(tmp_path, monkeypatch)
    (source/"ACTIVE_RUNTIME_MANIFEST.json").write_text("TEST_FIXTURE_PRESENT; VERIFY_IS_SEPARATELY_TESTED")
    monkeypatch.setattr(subject, "ROOT", source)
    assert subject.main(["premarket-plan", *args]) == 4
    assert ("bound_control_read", "TEST_FIXTURE_registered", registry) in seen
    assert not any(isinstance(row, tuple) and row[0] == "runtime" for row in seen)


def test_active_tree_driver_cannot_use_another_active_tree(tmp_path, monkeypatch):
    source, registry, runtime, active, seen, args = dispatch_fixture(tmp_path, monkeypatch)
    other = (tmp_path/"TEST_FIXTURE_other_active").resolve(); other.mkdir()
    (other/"ACTIVE_RUNTIME_MANIFEST.json").write_text("TEST_FIXTURE_PRESENT")
    monkeypatch.setattr(subject, "ROOT", other)
    assert subject.main(["premarket-plan", *args]) == 2
    assert not any(isinstance(row, tuple) and row[0] in {"runtime", "bound_control_read", "child"} for row in seen)


def test_generated_tree_driver_uses_real_gateway_control_boundary(active):
    # The active tree, imports, manifest and SQLite authority are real isolated
    # TEST_FIXTURE objects. Only registration readiness and child execution are
    # substitutes; no capture, production registration or worker is launched.
    result = child(active, """
from types import SimpleNamespace
from scripts import codex_active_v1 as driver
from scripts import active_business_gateway_v1 as guard
from scripts import codex_runtime_recovery_v2 as recovery
from scripts.business_authority_v1 import Authority
guard.install(driver.__file__)
with guard._control():
 owner=Authority(Path(sys.argv[2])/'authority.sqlite3','TEST_FIXTURE_ACTIVE')
checks=[]
def runtime(name,registry):
 assert guard._CONTROL.get() is True
 checks.append('narrow-bound-read')
 return SimpleNamespace(authority=owner,profile={'backend':'premarket','source_root':sys.argv[1],'python':sys.executable},
     active_source_attested=True,check_binding=lambda:None)
recovery.load_runtime=runtime
driver.subprocess.run=lambda *a,**k: SimpleNamespace(returncode=4)
code=driver.main(['premarket-plan','--source-root',sys.argv[1],'--runtime','TEST_FIXTURE_registered',
    '--registry-root',str(Path(sys.argv[2])/'registry')])
assert code==4,(code,checks)
assert checks==['narrow-bound-read'] and not guard._CONTROL.get()
print(json.dumps({'code':code,'checks':checks,'worker_launched':False}))
""")
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout)["worker_launched"] is False


def carrier(path, value):
    raw = json.dumps(value).encode(); path.write_bytes(raw)
    return str(path), hashlib.sha256(raw).hexdigest()


def plan(tmp_path):
    output = (tmp_path / "TEST_FIXTURE_twenty_days.json").resolve()
    assert subject.main(["delivery-protocol", "plan", "--first-target-session", "2026-11-09", "--output", str(output)]) == 0
    return output, json.loads(output.read_text())["result"]


def test_actual_plan_is_finite_official_and_never_creates_authority(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, "freeze_protocol", lambda *_a, **_k: pytest.fail("implicit freeze"))
    output, value = plan(tmp_path)
    assert value["expected_sessions"][0]["observation_date"] == "2026-11-06"
    assert len(value["expected_sessions"]) == 20 and value["real_acceptance"] == "NOT_RUN"
    assert value["collection_authorized"] is False
    assert subject.main(["delivery-protocol", "plan", "--first-target-session", "2027-01-04",
        "--output", str((tmp_path / "TEST_FIXTURE_no_2027.json").resolve())]) == 2
    assert subject.main(["delivery-protocol", "plan", "--first-target-session", "2026-11-09", "--output", str(output)]) == 2


@pytest.fixture
def authority(tmp_path):
    root = (tmp_path / "TEST_FIXTURE_authority").resolve(); root.mkdir()
    state = root / "state"; state.mkdir()
    return authority_module.Authority.initialize(root / "authority.sqlite3", "TEST_FIXTURE_DELIVERY_CLI",
        onboarding=dict(mode="EMPTY_NEW_WORKSPACE", state_roots=[str(state)]))


def test_sequence_without_active_binding_is_rejected_before_audit(tmp_path, authority, monkeypatch):
    # The original 20 x NOT_RUN / accepted=0 / unchanged DB assertions now run
    # through a real V2 registration in test_active_read_control_v1. This keeps
    # the old unbound invocation as a separate public-interface rejection.
    output, protocol = plan(tmp_path)
    pp, ph = str(output), hashlib.sha256(output.read_bytes()).hexdigest()
    bp, bh = carrier((tmp_path/"TEST_FIXTURE_requests.json").resolve(), dict(requests=[]))
    target = (tmp_path / "TEST_FIXTURE_audit.json").resolve()
    monkeypatch.setattr(contract, "freeze_protocol", lambda *_a, **_k: pytest.fail("implicit freeze"))
    with pytest.raises(SystemExit) as rejected:
        subject.main(["delivery-protocol", "inspect-sequence", "--protocol", pp, "--protocol-sha256", ph,
            "--requests", bp, "--requests-sha256", bh, "--authority-database", str(authority.path),
            "--workspace-id", authority.workspace_id, "--output", str(target)])
    assert rejected.value.code == 2 and not target.exists()
    with authority._connect(readonly=True) as db:
        assert db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0


def test_freeze_requires_explicit_switch_before_calling_contract(tmp_path, authority, monkeypatch):
    path, protocol = plan(tmp_path)
    called = []
    monkeypatch.setattr(contract, "freeze_protocol", lambda *a, **k: called.append((a,k)) or dict(status="TEST_FIXTURE_RECORDED"))
    args = ["delivery-protocol", "freeze", "--protocol", str(path), "--protocol-sha256", hashlib.sha256(path.read_bytes()).hexdigest(),
        "--authority-database", str(authority.path), "--workspace-id", authority.workspace_id,
        "--epoch-id", "TEST_FIXTURE_epoch", "--authorization-id", "TEST_FIXTURE_EXPLICIT_AUTHORIZATION",
        "--output", str((tmp_path/"TEST_FIXTURE_freeze.json").resolve())]
    assert subject.main(args) == 2 and called == []
    assert subject.main([*args, "--authorize-freeze"]) == 0
    assert len(called) == 1 and called[0][1] == dict(epoch_id="TEST_FIXTURE_epoch")


def test_incomplete_native_calendar_locator_is_rejected_without_cache_open(tmp_path):
    output = (tmp_path/"TEST_FIXTURE_never_written.json").resolve()
    assert subject.main(["delivery-protocol", "plan", "--first-target-session", "2026-11-09", "--calendar-record", "TEST_FIXTURE_missing",
        "--output", str(output)]) == 2
    assert not output.exists()
