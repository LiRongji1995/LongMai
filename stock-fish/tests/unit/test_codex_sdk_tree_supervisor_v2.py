"""TEST_FIXTURE real POSIX processes; no SDK, network, database or old state."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import socket
import sys
from types import SimpleNamespace

import pytest

from scripts import codex_sdk_tree_supervisor_v2 as supervisor


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("TEST_FIXTURE_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.fixture
def case(tmp_path):
    root = (tmp_path / "TEST_FIXTURE_sdk_tree").resolve()
    root.mkdir()
    return dict(root=root, limits=dict(tree_rss_bytes=512*1024**2, self_rss_bytes=2*1024**3,
        root_reap_seconds=1., max_seconds=10., output_bytes=2*1024**2, supervisor_read_bytes=64*1024**2,
        sample_interval_seconds=.03, term_grace_seconds=.1, kill_grace_seconds=3.),
        env=dict(PATH="/usr/bin:/bin", PYTHONDONTWRITEBYTECODE="1"))


def worker(case, body):
    path = case["root"] / "TEST_FIXTURE_worker.py"
    path.write_text(body)
    return path


def run(case, body="import time; time.sleep(.6)\n", **kwargs):
    script = worker(case, body)
    return supervisor.run_supervised([sys.executable, "-I", "-B", str(script)],
        cwd=case["root"], env=case["env"], limits=case["limits"], control_dir=case["root"] / "control",
        source_references=[supervisor.reference(script)[1]], test_fixture=True, **kwargs)


def test_healthy_command_preserves_exit_and_reports_measured_sampling(case):
    value = run(case)
    assert value["status"] == "COMPLETE"
    assert value["child_exit_code"] == 0
    assert value["root_reaped"] is value["all_observed_owned_exited"] is value["all_observed_descendants_gone"] is True
    assert value["sample_count"] > 0
    assert value["observed_peak_tree_rss_bytes"] >= value["observed_peak_root_rss_bytes"] > 0
    assert value["supervisor_peak_self_rss_bytes"] > 0
    assert value["process_snapshot_bytes_total"] > 0
    assert value["business_result_verified"] is value["source_capability_issued"] is False
    assert value["cleanup"]["signals"] == []
    persisted = json.loads((case["root"] / "control/completion.json").read_bytes())
    assert persisted["status"] == "COMPLETE"


def test_nonzero_exit_is_preserved_and_is_not_a_success(case):
    value = run(case, "import time; time.sleep(.6); raise SystemExit(19)\n")
    assert value["status"] == "FAILED" and value["child_exit_code"] == 19
    assert value["failure_reason"] == "SUPERVISOR_CHILD_EXIT_NOT_ACCEPTED"
    assert value["root_reaped"] and value["all_observed_owned_exited"]


def test_explicit_accepted_inner_unknown_exit_does_not_claim_business_success(case):
    value = run(case, "import time; time.sleep(.6); raise SystemExit(2)\n", accepted_exit_codes=(0, 2))
    assert value["status"] == "COMPLETE" and value["child_exit_code"] == 2
    assert value["business_result_verified"] is False


def test_existing_control_is_never_reused_or_overwritten(case):
    value = run(case)
    before = supervisor.reference(case["root"] / "control/claim.json")[1]
    with pytest.raises(FileExistsError):
        run(case)
    assert supervisor.reference(case["root"] / "control/claim.json")[1] == before
    assert value["root_reaped"]


def test_monotonic_deadline_stops_blocking_owned_command(case):
    case["limits"]["max_seconds"] = 1.
    value = run(case, "import time; time.sleep(30)\n")
    assert value["status"] == "FAILED"
    assert value["failure_reason"] in ("SUPERVISOR_UTC_DEADLINE_EXCEEDED", "SUPERVISOR_MONOTONIC_DEADLINE_EXCEEDED")
    assert value["root_reaped"] and value["all_observed_descendants_gone"]
    assert value["child_exit_code"] < 0


def test_backward_utc_clock_cannot_be_accepted(case):
    now = datetime.now(timezone.utc)
    calls = []
    def clock():
        calls.append(1)
        return now if len(calls) == 1 else now-timedelta(seconds=60)
    value = run(case, "import time; time.sleep(30)\n", _test_hooks={"utc_now": clock})
    assert value["status"] == "FAILED"
    assert value["failure_reason"] == "SUPERVISOR_UTC_CLOCK_MOVED_BACKWARD"
    # Admission checks catch this clock reversal before Popen can execute.
    assert value["root_identity"] is None and value["child_exit_code"] is None


def test_production_cannot_inject_monitor_hooks(case):
    path = worker(case, "pass\n")
    with pytest.raises(supervisor.SupervisorError, match="HOOKS_REQUIRE_EXPLICIT_TEST_FIXTURE"):
        supervisor.run_supervised([sys.executable, str(path)], cwd=case["root"], env=case["env"],
            limits=case["limits"], control_dir=case["root"] / "control", _test_hooks={"before_sample": lambda value: None})
    assert not (case["root"] / "control").exists()


def test_live_worker_context_binds_pipe_parent_argv_cwd_and_sources(case):
    module = Path(supervisor.__file__).resolve()
    control = case["root"] / "control"
    script = worker(case, f'''import importlib.util,json,time
spec=importlib.util.spec_from_file_location("TEST_FIXTURE_supervisor",{str(module)!r})
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
ticket=module.validate_worker_context({str(control)!r})
assert ticket["source_capability_issued"] is False
assert ticket["context"]=="LIVE_OWNED_PROCESS_CONTEXT_NOT_SOURCE_AUTHORITY"
print(json.dumps({{"validated": True}}),flush=True)
time.sleep(.6)
''')
    value = supervisor.run_supervised([sys.executable, "-I", "-B", str(script)],
        cwd=case["root"], env=case["env"], limits=case["limits"], control_dir=control,
        source_references=[supervisor.reference(script)[1]], test_fixture=True)
    assert value["status"] == "COMPLETE", (control / "console.log").read_text()
    assert json.loads((control / "console.log").read_text())["validated"] is True


def test_saved_claim_without_live_pipe_cannot_authorize_a_worker(case, monkeypatch):
    run(case)
    monkeypatch.delenv(supervisor.TICKET_FD, raising=False)
    with pytest.raises(supervisor.SupervisorError, match="LIVE_WORKER_PIPE_REQUIRED"):
        supervisor.validate_worker_context(case["root"] / "control")


def test_source_mutation_while_running_is_failure_even_if_inner_exit_zero(case):
    script = worker(case, "import time; time.sleep(.6)\n")
    reference = supervisor.reference(script)[1]
    changed = []
    def mutate(state):
        if not changed:
            changed.append(True)
            script.write_text(script.read_text() + "# TEST_FIXTURE_mutation\n")
    value = supervisor.run_supervised([sys.executable, "-I", "-B", str(script)],
        cwd=case["root"], env=case["env"], limits=case["limits"], control_dir=case["root"] / "control",
        source_references=[reference], test_fixture=True, _test_hooks={"after_sample": mutate})
    assert value["status"] == "FAILED" and value["child_exit_code"] == 0
    assert value["failure_reason"] == "SUPERVISOR_SOURCE_REFERENCE_CHANGED"


def test_console_output_limit_is_supervised_while_worker_runs(case):
    value = run(case, "import sys,time; sys.stdout.write('x'*600000);sys.stdout.flush();time.sleep(30)\n")
    assert value["status"] == "FAILED"
    assert value["failure_reason"] == "SUPERVISOR_CONSOLE_LIMIT_EXCEEDED"
    assert value["root_reaped"] and value["all_observed_owned_exited"]


def test_pid_reuse_identity_does_not_signal_a_foreign_process(monkeypatch):
    owner = supervisor.Ownership(dict(pid=100, create_time=10., uid=501))
    owner.known[(200, 11.)] = dict(pid=200, create_time=11., uid=501)
    calls = []
    class FakeProcess:
        def __init__(self, pid): self.pid = pid
        @property
        def _proc(self): return self
        def create_time(self, *, monotonic=False):
            assert monotonic
            return 10. if self.pid == 100 else 99.
        def uids(self): return SimpleNamespace(real=501)
        def status(self): return "running"
        def send_signal(self, signum): calls.append((self.pid, signum))
    monkeypatch.setattr(supervisor.psutil, "Process", FakeProcess)
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "getsid", lambda pid: pid)
    monkeypatch.setattr(os, "killpg", lambda pid, signum: calls.append(("group", pid, signum)))
    sent, errors = [], []
    process = SimpleNamespace(pid=100, returncode=None)
    monkeypatch.setattr(os, "waitpid", lambda pid, flags: (0, 0))
    supervisor._signal_owned(owner, signal.SIGKILL, sent, errors, anchor=supervisor.RootAnchor(process, owner.root))
    assert calls == [("group", 100, signal.SIGKILL)]
    assert errors == []


@pytest.mark.parametrize("state,creation_time,accepted", [
    (supervisor.psutil.STATUS_ZOMBIE, 10., True),
    (supervisor.psutil.STATUS_RUNNING, 10., False),
    (supervisor.psutil.STATUS_ZOMBIE, 99., False),
])
def test_missing_root_gets_one_terminal_identity_observation_only(monkeypatch, state, creation_time, accepted):
    identity = dict(pid=100, create_time=10., uid=501)
    owner = supervisor.Ownership(identity)
    observations = []
    class KernelProcess:
        def __init__(self, pid):
            self.pid = pid
            observations.append(pid)
        @property
        def _proc(self): return self
        def create_time(self, *, monotonic=False):
            assert monotonic
            return creation_time
        def uids(self): return SimpleNamespace(real=501)
        def status(self): return state
    monkeypatch.setattr(supervisor.psutil, "Process", KernelProcess)
    if accepted:
        rows = owner.observe({})
        assert rows == [dict(identity, ppid=None, status=state, rss=0, pgid=None, sid=None)]
        assert owner.known == {(100, 10.): identity}
    else:
        with pytest.raises(supervisor.SupervisorError, match="ROOT_IDENTITY_LOST"):
            owner.observe({})
    assert observations == [100]


def test_terminal_root_reobservation_cannot_replace_live_rss_coverage(case, monkeypatch):
    monkeypatch.setattr(supervisor, "_snapshot", lambda: ({}, []))
    def wait_for_real_zombie(pid):
        # Python startup itself can exceed a guessed sleep under load. Observe
        # the actual waitable terminal state without consuming its wait status.
        deadline = supervisor.time.monotonic() + 5.
        while supervisor.time.monotonic() < deadline:
            try:
                if supervisor.psutil.Process(pid).status() in supervisor.DEAD:
                    return
            except supervisor.psutil.NoSuchProcess:
                pass  # A missing row alone is not the required zombie proof.
            supervisor.time.sleep(.01)
        raise AssertionError("TEST_FIXTURE_ROOT_DID_NOT_REACH_TERMINAL_STATE")
    value = run(case, "import time;time.sleep(.1)\n", _test_hooks={
        "after_launch": wait_for_real_zombie})
    assert value["status"] == "FAILED"
    assert value["failure_reason"] == "SUPERVISOR_LIVE_SAMPLING_COVERAGE_INSUFFICIENT"
    assert value["observed_peak_root_rss_bytes"] == 0
    assert value["child_exit_code"] == 0 and value["root_reaped"]
    assert value["cleanup"]["signals"] == []


def test_ancestor_symlink_and_unnamed_fingerprint_are_rejected(case):
    path = worker(case, "pass\n")
    alias = case["root"] / "alias"
    alias.symlink_to(case["root"], target_is_directory=True)
    with pytest.raises(OSError):
        supervisor.reference(alias / path.name)
    ref = supervisor.reference(path)[1]
    ref["fp"] = list(ref["fp"].values())
    with pytest.raises(supervisor.SupervisorError, match="NAMED_PHYSICAL_REFERENCE_REQUIRED"):
        supervisor.reference(path, ref)
    partial = supervisor.reference(path)[1]
    partial.pop("sha256")
    with pytest.raises(supervisor.SupervisorError, match="NAMED_PHYSICAL_REFERENCE_REQUIRED"):
        supervisor.reference(path, partial)


def test_sampling_evidence_is_bounded_but_count_and_gaps_cover_all_samples(case, monkeypatch):
    case["limits"]["sample_interval_seconds"] = .01
    owned = {}
    def launched(pid):
        owned["pid"] = pid
    # Bound only enumeration to this real, childless fixture. Keep the actual
    # snapshot implementation, including Darwin live -> zombie re-observation;
    # a hand-written fast snapshot previously dropped that kernel transition.
    def fixture_processes():
        return iter([supervisor.psutil.Process(owned["pid"])])
    monkeypatch.setattr(supervisor.psutil, "process_iter", fixture_processes)
    value = run(case, "import time;time.sleep(1.5)\n", _test_hooks={"after_launch": launched})
    assert value["status"] == "COMPLETE"
    assert value["failure_reason"] is None and value["cleanup"]["errors"] == []
    assert value["sample_count"] > 64 and len(value["samples"]) == 64
    assert value["samples"][0]["sample_number"] == 1
    assert value["samples"][-1]["sample_number"] == value["sample_count"]
    assert value["max_sample_gap_seconds"] > 0
    assert value["cleanup_process_snapshot_bytes"] > 0
    assert value["terminal_supervisor_returned_read_bytes"] > value["process_snapshot_bytes_total"]


def test_deadline_during_completion_publication_cannot_report_success(case, monkeypatch):
    case["limits"]["max_seconds"] = 3.
    original = supervisor._write
    candidate_status = []
    def slow_publication(path, value):
        reference = original(path, value)
        if Path(path).name == "completion.json":
            candidate_status.append(value["status"])
            supervisor.time.sleep(3.1)
        return reference
    monkeypatch.setattr(supervisor, "_write", slow_publication)
    value = run(case)
    assert candidate_status == ["COMPLETE"]
    assert value["status"] == "FAILED"
    assert value["failure_reason"] == "SUPERVISOR_UTC_DEADLINE_EXCEEDED"
    assert value["child_exit_code"] == 0 and value["root_reaped"]
    assert (case["root"] / "control/failure.json").exists()


def test_self_rss_admission_failure_does_not_launch_worker(case):
    case["limits"]["self_rss_bytes"] = 1
    value = run(case)
    assert value["status"] == "FAILED"
    assert value["failure_reason"] == "SUPERVISOR_SELF_RSS_LIMIT_EXCEEDED"
    assert value["root_identity"] is None and value["child_exit_code"] is None


def test_failure_during_child_identity_acquisition_still_reaps_direct_child(case, monkeypatch):
    original = supervisor._identity
    def broken_child_identity(process):
        if process.pid != os.getpid():
            raise supervisor.SupervisorError("TEST_FIXTURE_IDENTITY_UNAVAILABLE")
        return original(process)
    monkeypatch.setattr(supervisor, "_identity", broken_child_identity)
    value = run(case, "import time;time.sleep(30)\n")
    assert value["status"] == "FAILED"
    assert value["failure_reason"] == "TEST_FIXTURE_IDENTITY_UNAVAILABLE"
    assert value["root_reaped"] and value["child_exit_code"] in (-signal.SIGTERM, -signal.SIGKILL)


def test_stable_native_identity_ignores_public_wall_adjustment(case, monkeypatch):
    original = supervisor.psutil.Process.create_time
    reads = []
    def shifted(process):
        reads.append(process.pid)
        return original(process) + 3600.
    monkeypatch.setattr(supervisor.psutil.Process, 'create_time', shifted)
    value = run(case)
    assert value['status'] == 'COMPLETE'
    assert value['identity_basis'] == supervisor.IDENTITY_BASIS
    assert value['root_reaped'] and value['cleanup']['root_reap_evidence']['exit_code_proven']
    assert value['cleanup']['root_reap_evidence']['wait_status'] == 0
    assert value['cleanup']['signals'] == []
    # The supervisor never uses the public wall-adjusted method for ownership.
    assert reads == []


def test_missing_stable_native_api_has_no_wall_fallback(monkeypatch):
    process = SimpleNamespace(pid=123, create_time=lambda: 100., uids=lambda: SimpleNamespace(real=501))
    with pytest.raises(supervisor.SupervisorError, match='STABLE_IDENTITY_API_UNAVAILABLE'):
        supervisor._identity(process)
    monkeypatch.setattr(supervisor.psutil, '__version__', 'UNREGISTERED_TEST_FIXTURE')
    with pytest.raises(supervisor.SupervisorError, match='STABLE_IDENTITY_PLATFORM_UNSUPPORTED'):
        supervisor._identity(supervisor.psutil.Process())


def test_root_kernel_identity_conflict_forbids_group_signal(monkeypatch):
    identity = dict(pid=100, create_time=10., uid=501)
    owner = supervisor.Ownership(identity)
    class OtherProcess:
        pid = 100
        _proc = SimpleNamespace(create_time=lambda **kwargs: 99.)
        def uids(self): return SimpleNamespace(real=501)
    monkeypatch.setattr(supervisor.psutil, 'Process', lambda pid: OtherProcess())
    monkeypatch.setattr(os, 'waitpid', lambda pid, flags: (0, 0))
    signals = []
    monkeypatch.setattr(os, 'killpg', lambda *args: signals.append(args))
    errors, recorded = [], []
    anchor = supervisor.RootAnchor(SimpleNamespace(pid=100, returncode=None), identity)
    supervisor._signal_owned(owner, signal.SIGKILL, recorded, errors, anchor=anchor)
    assert signals == recorded == []
    assert errors == ['SUPERVISOR_ROOT_IDENTITY_CHANGED']


def test_kernel_reaped_anchor_never_signals_old_group(monkeypatch):
    process = SimpleNamespace(pid=100, returncode=None)
    calls = []
    monkeypatch.setattr(os, 'waitpid', lambda pid, flags: (100, 0))
    monkeypatch.setattr(os, 'killpg', lambda *args: calls.append(args))
    anchor = supervisor.RootAnchor(process)
    signals, errors = [], []
    supervisor._signal_owned(None, signal.SIGKILL, signals, errors, anchor=anchor)
    assert calls == signals == errors == []
    assert anchor.reaped and anchor.code == 0 and process.returncode == 0
    assert anchor.evidence()['exit_code_proven']


def test_echild_cannot_fabricate_success_or_signal_old_group(monkeypatch):
    process = SimpleNamespace(pid=100, returncode=None)
    def echild(*args):
        raise ChildProcessError('TEST_FIXTURE_EXTERNAL_REAPER')
    calls = []
    monkeypatch.setattr(os, 'waitpid', echild)
    monkeypatch.setattr(os, 'killpg', lambda *args: calls.append(args))
    anchor = supervisor.RootAnchor(process)
    signals, errors = [], []
    supervisor._signal_owned(None, signal.SIGKILL, signals, errors, anchor=anchor)
    assert not anchor.reaped and anchor.code is process.returncode is None
    assert anchor.evidence()['exit_code_proven'] is False
    assert anchor.reason == 'SUPERVISOR_ROOT_ECHILD_EXIT_UNKNOWN'
    assert calls == signals == []
    assert errors == ['SUPERVISOR_ROOT_ECHILD_EXIT_UNKNOWN']


def test_cleanup_waits_are_bounded_and_do_not_grant_business_time(case):
    case['limits']['term_grace_seconds'] = 10.
    case['limits']['kill_grace_seconds'] = 10.
    case['limits']['root_reap_seconds'] = 11.
    with pytest.raises(supervisor.SupervisorError, match='POLL_OR_CLEANUP_LIMIT'):
        run(case)
    assert not (case['root'] / 'control').exists()


@pytest.mark.parametrize('handler', [signal.SIG_IGN, lambda *_: None])
def test_nondefault_sigchld_is_rejected_before_claim_or_launch(case, monkeypatch, handler):
    original = supervisor.signal.getsignal
    monkeypatch.setattr(supervisor.signal, 'getsignal',
                        lambda signum: handler if signum == signal.SIGCHLD else original(signum))
    with pytest.raises(supervisor.SupervisorError, match='EXCLUSIVE_DEFAULT_SIGCHLD_REQUIRED'):
        run(case)
    assert not (case['root'] / 'control').exists()


def test_utc_sample_gap_is_independent_of_monotonic_gap(case):
    now = datetime.now(timezone.utc)
    delta = [0.]
    def clock():
        return now + timedelta(seconds=delta[0])
    def sampled(state):
        if state['sample_count'] == 1:
            delta[0] = 30.
    value = run(case, 'import time;time.sleep(30)\n',
                _test_hooks={'utc_now': clock, 'after_sample': sampled})
    assert value['status'] == 'FAILED' and value['root_reaped']
    assert value['failure_reason'] == 'SUPERVISOR_UTC_DEADLINE_EXCEEDED'
    # This jump is observed at checks immediately after sample1, so no second
    # sample may be invented merely to make the recorded gap equal 30 seconds.
    assert value['sample_count'] == 1 and value['max_utc_sample_gap_seconds'] == 0
    assert value['actual_utc_elapsed_seconds'] == 30.
    assert value['max_sample_gap_clock'] == 'MONOTONIC'
    assert value['cleanup']['business_budget_extension_seconds'] == 0


def test_inter_sample_utc_gap_and_rollback_are_measured(case):
    now = datetime.now(timezone.utc)
    delta = [0.]
    def clock(): return now + timedelta(seconds=delta[0])
    def before(state):
        if state['sample_count'] == 1:
            delta[0] = 30.
    value = run(case, 'import time;time.sleep(30)\n',
                _test_hooks={'utc_now': clock, 'before_sample': before})
    assert value['status'] == 'FAILED' and value['root_reaped']
    assert value['max_utc_sample_gap_seconds'] == 30.
    assert value['max_sample_gap_seconds'] < 5.
    assert value['sample_count'] == 2


def test_final_waitpid_after_timeout_keeps_failure_and_real_exit(case, monkeypatch):
    case['limits']['root_reap_seconds'] = .12
    case['limits']['kill_grace_seconds'] = .1
    native_waitpid = os.waitpid
    state = {'pid': None, 'final': False}
    def waitpid(pid, flags):
        if pid == state['pid'] and not state['final']:
            return (0, 0)
        return native_waitpid(pid, flags)
    def launched(pid):
        state['pid'] = pid
        supervisor.time.sleep(.2)  # let the real fixture install its TERM handler
    def before_sample(value):
        raise supervisor.SupervisorError('TEST_FIXTURE_TRIGGER_CLEANUP')
    def phase(name, value):
        if name == 'before_final_waitpid':
            state['final'] = True
    monkeypatch.setattr(os, 'waitpid', waitpid)
    value = run(case, 'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)\n',
                _test_hooks={'after_launch': launched, 'before_sample': before_sample, 'cleanup_phase': phase})
    assert value['status'] == 'FAILED'
    assert value['failure_reason'] == 'TEST_FIXTURE_TRIGGER_CLEANUP'
    cleanup = value['cleanup']
    assert cleanup['root_reaped'] and cleanup['child_exit_code'] == -signal.SIGKILL
    assert 'SUPERVISOR_ROOT_REAP_TIMEOUT' in cleanup['errors']
    assert cleanup['root_reap_window_elapsed_seconds'] >= .12
    assert cleanup['final_waitpid_attempted']
    assert cleanup['root_reap_evidence']['last_phase'] == 'FINAL_WNOHANG'
    assert cleanup['root_reap_evidence']['exit_code_proven']
    assert cleanup['root_reap_evidence']['wait_status'] is not None
    assert cleanup['all_observed_descendants_gone']
    assert [item['signal'] for item in cleanup['signals']] == [signal.SIGTERM, signal.SIGKILL]


@pytest.mark.parametrize('kernel_state', ['EXITED', 'LIVE', 'ECHILD'])
def test_missing_fresh_root_requires_actual_owned_kernel_terminal(monkeypatch, kernel_state):
    identity = dict(pid=100, create_time=10., uid=501)
    process = SimpleNamespace(pid=100, returncode=None)
    anchor = supervisor.RootAnchor(process, identity)
    owner = supervisor.Ownership(identity, anchor)
    def missing(pid):
        raise supervisor.psutil.NoSuchProcess(pid)
    def waitpid(pid, flags):
        assert (pid, flags) == (100, os.WNOHANG)
        if kernel_state == 'ECHILD':
            raise ChildProcessError('TEST_FIXTURE_EXTERNAL_REAPER')
        return (100, 0) if kernel_state == 'EXITED' else (0, 0)
    monkeypatch.setattr(supervisor.psutil, 'Process', missing)
    monkeypatch.setattr(os, 'waitpid', waitpid)
    if kernel_state == 'EXITED':
        rows = owner.observe({})
        assert rows == [dict(identity, ppid=None, status=supervisor.psutil.STATUS_DEAD, rss=0, pgid=None, sid=None)]
        assert anchor.reaped and anchor.code == 0 and anchor.wait_status == 0
    else:
        with pytest.raises(supervisor.SupervisorError, match='ROOT_IDENTITY_LOST'):
            owner.observe({})
        assert not anchor.reaped and anchor.code is None
    assert anchor.calls == 1
    assert owner.known == {(100, 10.): identity}


@pytest.mark.parametrize('condition', ['DENIED', 'FRESH_IDENTITY_CONFLICT', 'SNAPSHOT_IDENTITY_CONFLICT'])
def test_unknown_or_conflicting_root_never_consumes_terminal_fallback(monkeypatch, condition):
    identity = dict(pid=100, create_time=10., uid=501)
    anchor = supervisor.RootAnchor(SimpleNamespace(pid=100, returncode=None), identity)
    owner = supervisor.Ownership(identity, anchor)
    class Changed:
        pid = 100
        _proc = SimpleNamespace(create_time=lambda **kwargs: 99.)
        def uids(self): return SimpleNamespace(real=501)
        def status(self): return supervisor.psutil.STATUS_ZOMBIE
    def process(pid):
        if condition == 'DENIED':
            raise supervisor.psutil.AccessDenied(pid)
        return Changed()
    calls = []
    monkeypatch.setattr(supervisor.psutil, 'Process', process)
    monkeypatch.setattr(os, 'waitpid', lambda *args: calls.append(args))
    rows = ({100: dict(identity, create_time=99., ppid=1, status=supervisor.psutil.STATUS_ZOMBIE,
                      rss=0, pgid=None, sid=None)} if condition == 'SNAPSHOT_IDENTITY_CONFLICT' else {})
    with pytest.raises(supervisor.SupervisorError, match='ROOT_IDENTITY_LOST'):
        owner.observe(rows)
    assert calls == [] and not anchor.reaped


@pytest.mark.parametrize('relation', ['SAME_SESSION', 'CHILD_OF_KNOWN'])
def test_reaped_root_never_authorizes_new_session_or_child_identity(monkeypatch, relation):
    identity = dict(pid=100, create_time=10., uid=501)
    anchor = supervisor.RootAnchor(SimpleNamespace(pid=100, returncode=None), identity)
    anchor.reaped, anchor.code, anchor.wait_status = True, 0, 0
    owner = supervisor.Ownership(identity, anchor)
    child = dict(pid=200, create_time=11., uid=501)
    owner.known[(200, 11.)] = child
    rows = {200: dict(child, ppid=1, status=supervisor.psutil.STATUS_RUNNING, rss=4096, pgid=200, sid=200),
            300: dict(pid=300, create_time=12., uid=501, ppid=1 if relation == 'SAME_SESSION' else 200,
                      status=supervisor.psutil.STATUS_RUNNING, rss=4096,
                      pgid=100 if relation == 'SAME_SESSION' else 300,
                      sid=100 if relation == 'SAME_SESSION' else 300)}
    with pytest.raises(supervisor.SupervisorError, match='UNOBSERVED_PROCESS_AFTER_ROOT_REAP'):
        owner.observe(rows)
    assert list(owner.known) == [(100, 10.), (200, 11.)]
    assert (300, 12.) not in owner.known
