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

from scripts import codex_sdk_tree_supervisor_v3 as supervisor


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("TEST_FIXTURE_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)






























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


@pytest.fixture
def native_worker(tmp_path):
    import subprocess
    processes = []
    def start(body, *, seconds=10):
        script = tmp_path / ("TEST_FIXTURE_worker_" + str(len(processes)) + ".py")
        script.write_text(body)
        session = supervisor.NativeProcessSession({"max_seconds": seconds})
        process = subprocess.Popen([sys.executable, "-I", "-B", str(script)], start_new_session=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append((process, session))
        session.bind(process)
        session.meter = lambda _amount: None
        return session
    yield start
    for process, session in processes:
        if not session.anchor.reaped or session.ownership.present():
            session.stop(session)


@pytest.mark.parametrize('exit_code',[0,2,19])
def test_native_session_preserves_actual_kernel_exit_and_sampling(native_worker,exit_code):
    import time
    session=native_worker('import time; time.sleep(1.5); raise SystemExit('+str(exit_code)+')')
    charged=[]
    while session.poll() is None:
        session.sample(session.pid,charged.append)
        time.sleep(.01)
    assert session.wait(timeout=1)==exit_code
    assert session.remaining(session.pid,charged.append)==[]
    session.require_complete()
    report=session.report()
    assert report['root_reaped'] is True
    assert report['sample_count']>0
    assert report['observed_peak_tree_rss_bytes']>=report['observed_peak_root_rss_bytes']>0
    assert sum(charged)==report['process_snapshot_bytes']>0


def test_native_deadline_is_sticky_and_cleanup_reaps_only_owned_process(native_worker):
    import time
    session=native_worker('import time; time.sleep(30)',seconds=2)
    session.sample(session.pid,lambda _amount:None)
    time.sleep(2.1)
    with pytest.raises(supervisor.SupervisorError,match='DEADLINE_EXCEEDED'):
        session.check()
    session.stop(session)
    assert session.report()['root_reaped'] is True
    with pytest.raises(supervisor.SupervisorError,match='DEADLINE_EXCEEDED'):
        session.require_complete()


def test_native_session_meters_and_reaps_observed_descendant(native_worker,tmp_path):
    import time
    marker=tmp_path/'TEST_FIXTURE-child-pid'
    body='import subprocess,sys,time\np=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"])\nopen('+repr(str(marker))+',"w").write(str(p.pid))\ntime.sleep(30)\n'
    session=native_worker(body)
    deadline=time.monotonic()+3
    while not marker.exists() and time.monotonic()<deadline:time.sleep(.01)
    assert marker.exists()
    child=int(marker.read_text())
    session.sample(session.pid,lambda _amount:None)
    assert any(identity[0]==child for identity in session.ownership.known)
    session.stop(session)
    report=session.report()
    assert report['cleanup']['root_reaped'] is True
    assert report['cleanup']['all_observed_descendants_gone'] is True
    assert report['cleanup']['errors']==[]


def test_native_sample_failure_cannot_be_cleared_by_later_success(native_worker,monkeypatch):
    session=native_worker('import time; time.sleep(30)')
    original=supervisor._snapshot
    def failed():raise supervisor.SupervisorError('TEST_FIXTURE_SAMPLE_FAILURE')
    monkeypatch.setattr(supervisor,'_snapshot',failed)
    with pytest.raises(supervisor.SupervisorError,match='TEST_FIXTURE_SAMPLE_FAILURE'):
        session.sample(session.pid,lambda _amount:None)
    monkeypatch.setattr(supervisor,'_snapshot',original)
    session.stop(session)
    with pytest.raises(supervisor.SupervisorError,match='TEST_FIXTURE_SAMPLE_FAILURE'):
        session.require_complete()
