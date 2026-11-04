"""Independent TEST_FIXTURE kernel ownership and cleanup regressions for V3.

All processes belong to this fixture. No SDK, socket connection or database is
used. Fault injection changes observations/wait readiness, never source data.
An emergency oracle uses the real kernel start token and waitpid independently
of the supervisor under test so a failed assertion cannot leave a live root.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import time

import psutil
import pytest

from scripts import codex_sdk_tree_supervisor_v3 as supervisor


MIB = 1024**2
PROCESS = psutil.Process
WAITPID = os.waitpid
KILL = os.kill
PROGRAM = r'''
import json,os,signal,socket,sqlite3,subprocess,sys,time
from pathlib import Path
import psutil
base=Path(sys.argv[1]);mode=sys.argv[2]
assert "TEST_FIXTURE" in str(base)
def forbidden(*a,**k):raise RuntimeError("TEST_FIXTURE_NO_NETWORK_OR_SQL")
socket.socket.connect=socket.socket.connect_ex=socket.create_connection=forbidden
sqlite3.connect=forbidden
if mode not in ("normal","exit","foreign","ticket") and not mode.startswith("natural_"):
 signal.signal(signal.SIGTERM,signal.SIG_IGN)
p=psutil.Process()
identity=dict(pid=os.getpid(),create_time=p._proc.create_time(monotonic=True),uid=p.uids().real)
(base/(mode+".identity.json")).write_text(json.dumps(identity))
(base/(mode+".session.json")).write_text(json.dumps(dict(pid=os.getpid(),sid=os.getsid(0),pgid=os.getpgid(0))))
if mode=="ticket":
 sys.path.insert(0,sys.argv[3])
 from scripts import codex_sdk_tree_supervisor_v3 as s
 ticket=s.validate_worker_context(Path(sys.argv[4]))
 assert ticket["child"]==identity
 (base/"ticket-result.json").write_text(json.dumps(ticket))
children=[]
category,_,role=mode.partition("_")
if category in ("natural","rss","orphan","escaped"):
 if role!="root":
  block=bytearray((64 if category=="rss" else 32)*1024**2)
  for offset in range(0,len(block),4096):block[offset]=29
 if role in ("root","child"):
  next_role="child" if role=="root" else "grandchild"
  next_mode=category+"_"+next_role
  children.append(subprocess.Popen([sys.executable,"-I","-B",__file__,str(base),next_mode],
   start_new_session=category=="escaped" and next_role=="grandchild"))
  deadline=time.monotonic()+3
  while not (base/(next_mode+".ready")).exists() and time.monotonic()<deadline:time.sleep(.005)
  assert (base/(next_mode+".ready")).exists()
if mode=="tree":
 children.append(subprocess.Popen([sys.executable,"-I","-B",__file__,str(base),"leaf"]))
 deadline=time.monotonic()+3
 while not (base/"leaf.ready").exists() and time.monotonic()<deadline:time.sleep(.005)
 assert (base/"leaf.ready").exists()
(base/(mode+".ready")).write_text("TEST_FIXTURE_READY")
if category=="natural":
 if role=="grandchild":time.sleep(.5)
 for child in children:assert child.wait(timeout=3)==0
elif category in ("orphan","escaped") and role=="root":time.sleep(.4)
elif mode in ("normal","ticket"):time.sleep(.3)
elif mode=="exit":time.sleep(.08)
else:
 deadline=time.monotonic()+20
 while time.monotonic()<deadline:time.sleep(.02)
'''


def kernel_identity(pid):
    process = PROCESS(pid)
    return dict(pid=pid, create_time=process._proc.create_time(monotonic=True), uid=process.uids().real)


def exact_live(identity):
    try:
        process = PROCESS(identity['pid'])
        return kernel_identity(identity['pid']) == identity and process.status() not in {
            psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}
    except psutil.NoSuchProcess:
        return False


class Fixture:
    def __init__(self, directory):
        self.directory = directory
        self.program = directory/'TEST_FIXTURE_kernel_owned.py'
        self.program.write_text(PROGRAM, encoding='utf-8')
        self.roots = []

    def identities(self):
        values = {tuple(row.values()): row for row in self.roots}
        for path in self.directory.glob('*.identity.json'):
            row = json.loads(path.read_bytes())
            values[(row['pid'], row['create_time'], row['uid'])] = row
        return list(values.values())

    def ready(self, mode):
        end = time.monotonic()+3
        while not (self.directory/(mode+'.ready')).exists() and time.monotonic() < end:
            time.sleep(.005)
        assert (self.directory/(mode+'.ready')).exists(), 'TEST_FIXTURE_CHILD_NOT_READY'

    def launch(self, pid):
        self.roots.append(kernel_identity(pid))

    def cleanup(self):
        values = self.identities()
        for row in reversed(values):
            if exact_live(row):
                try:
                    KILL(row['pid'], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        end = time.monotonic()+3
        pending = {row['pid'] for row in self.roots}
        while pending and time.monotonic() < end:
            for pid in tuple(pending):
                try:
                    result, _ = WAITPID(pid, os.WNOHANG)
                    if result == pid:
                        pending.remove(pid)
                except ChildProcessError:
                    pending.remove(pid)
            time.sleep(.005)
        assert not pending, 'TEST_FIXTURE_WAITABLE_ROOT_LEAK'
        assert not any(exact_live(row) for row in values), 'TEST_FIXTURE_LIVE_PROCESS_LEAK'

    def run(self, mode='block', *, hooks=None, limits=None, control=None):
        control = control or self.directory/('TEST_FIXTURE_control_'+mode)
        options = dict(tree_rss_bytes=256*MIB, self_rss_bytes=512*MIB,
            max_seconds=3, output_bytes=2*MIB, supervisor_read_bytes=64*MIB,
            sample_interval_seconds=.02, term_grace_seconds=.02,
            kill_grace_seconds=.1, root_reap_seconds=.35)
        options.update(limits or {})
        supplied = dict(hooks or {})
        callback = supplied.pop('after_launch', None)
        def launched(pid):
            self.launch(pid)
            self.ready(mode)
            if callback:
                callback(pid)
        argv = [sys.executable, '-I', '-B', '-X',
            'pycache_prefix='+str(self.directory/'TEST_FIXTURE_ABSENT_CHILD_BYTECODE'),
            str(self.program), str(self.directory), mode]
        if mode == 'ticket':
            argv += [str(Path(supervisor.__file__).parent.parent), str(control)]
        result = supervisor.run_supervised(argv, cwd=self.directory, env=dict(os.environ),
            limits=options, control_dir=control, test_fixture=True,
            _test_hooks=dict(after_launch=launched, **supplied))
        (self.directory/(control.name+'-returned.json')).write_text(
            json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2), encoding='utf-8')
        return result, control


@pytest.fixture(autouse=True)
def no_network_or_sql(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_NO_NETWORK_OR_SQL')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)


@pytest.fixture
def owned(tmp_path):
    directory = tmp_path/'TEST_FIXTURE_independent_sdk_tree_v2'
    directory.mkdir()
    value = Fixture(directory)
    try:
        yield value
    finally:
        value.cleanup()


@pytest.fixture
def foreign(owned):
    directory = owned.directory/'TEST_FIXTURE_unrelated_sentinel'
    directory.mkdir()
    process = subprocess.Popen([sys.executable, '-I', '-B', str(owned.program), str(directory), 'foreign'],
                               start_new_session=True)
    identity = kernel_identity(process.pid)
    try:
        yield identity
    finally:
        if exact_live(identity):
            KILL(identity['pid'], signal.SIGKILL)
        process.wait(timeout=3)


def failed(result, control):
    assert result['status'] == 'FAILED'
    assert result['failure_reason']
    assert result['retry_authorized'] is False
    assert result['source_capability_issued'] is False
    saved = json.loads((control/'completion.json').read_bytes())
    assert saved['status'] == 'FAILED' or (control/'failure.json').exists()


def reaped(result, owned):
    assert result['root_reaped'] is True
    assert result['child_exit_code'] is not None
    assert not any(exact_live(row) for row in owned.identities())
    # A kernel exit status has been consumed, not merely a ps observation.
    for identity in owned.roots:
        with pytest.raises(ChildProcessError):
            WAITPID(identity['pid'], os.WNOHANG)


def test_wall_creation_clock_drift_does_not_change_kernel_ownership(owned, monkeypatch):
    original = PROCESS.create_time
    drift = {'seconds': 0}
    monkeypatch.setattr(PROCESS, 'create_time', lambda process: original(process)+drift['seconds'])
    def launched(_pid):
        drift['seconds'] = 7200
    result, _ = owned.run('normal', hooks=dict(after_launch=launched))
    assert result['status'] == 'COMPLETE'
    assert result['root_identity'] == owned.roots[0]
    assert result['child_exit_code'] == 0
    reaped(result, owned)


def test_live_ticket_uses_same_stable_kernel_identity_in_fresh_worker(owned):
    result, _ = owned.run('ticket', limits=dict(max_seconds=10))
    assert result['status'] == 'COMPLETE'
    ticket = json.loads((owned.directory/'ticket-result.json').read_bytes())
    assert ticket['child'] == result['root_identity'] == owned.roots[0]
    reaped(result, owned)


def test_utc_deadline_already_exceeded_still_kills_and_waits_owned_tree(owned):
    def utc_now():
        offset = 30 if (owned.directory/'tree.ready').exists() else 0
        return datetime.now(timezone.utc)+timedelta(seconds=offset)
    result, control = owned.run('tree', hooks=dict(utc_now=utc_now))
    failed(result, control)
    assert 'UTC' in result['failure_reason']
    assert result['actual_utc_elapsed_seconds'] >= 30
    assert result['monotonic_elapsed_seconds'] < 3
    reaped(result, owned)


def test_sampler_exception_cannot_skip_direct_child_reap(owned, foreign, monkeypatch):
    def unavailable():
        raise RuntimeError('TEST_FIXTURE_PROCESS_TABLE_UNAVAILABLE')
    monkeypatch.setattr(supervisor, '_snapshot', unavailable)
    result, control = owned.run()
    failed(result, control)
    assert result['observed_peak_tree_rss_bytes'] == 0
    reaped(result, owned)
    assert exact_live(foreign), 'TEST_FIXTURE_UNRELATED_PROCESS_WAS_SIGNALED'


def test_unknown_ps_root_observation_uses_only_proven_child_fallback(owned, monkeypatch):
    original = supervisor._identity
    denied = []
    def unavailable(process):
        if owned.roots and process.pid == owned.roots[0]['pid']:
            denied.append(process.pid)
            raise psutil.AccessDenied(process.pid)
        return original(process)
    monkeypatch.setattr(supervisor, '_identity', unavailable)
    def fail_sample(_state):
        raise RuntimeError('TEST_FIXTURE_OBSERVATION_UNAVAILABLE')
    result, control = owned.run(hooks=dict(before_sample=fail_sample))
    failed(result, control)
    assert denied, 'TEST_FIXTURE_MUST_HIT_ACTUAL_ROOT_IDENTITY_PATH'
    assert 'OBSERVATION' in json.dumps(result['cleanup'])
    reaped(result, owned)


def test_descendant_wait_cannot_consume_dedicated_root_reap_window(owned, monkeypatch):
    phase = {'release_at': None, 'waits': []}
    def waitpid(pid, flags):
        if owned.roots and pid == owned.roots[0]['pid']:
            phase['waits'].append(time.monotonic())
            if phase['release_at'] is None or time.monotonic() < phase['release_at']:
                return 0, 0
        return WAITPID(pid, flags)
    def cleanup_phase(name, _state):
        if name == 'after_descendant_wait':
            # Spend more than the entire descendant allowance, then require
            # real wait readiness after 120ms. A shared 10ms remainder fails.
            time.sleep(.12)
            phase['release_at'] = time.monotonic()+.12
    def fail_sample(_state):
        raise RuntimeError('TEST_FIXTURE_FORCE_CLEANUP')
    with monkeypatch.context() as patch:
        patch.setattr(supervisor.os, 'waitpid', waitpid)
        result, control = owned.run('tree', hooks=dict(before_sample=fail_sample, cleanup_phase=cleanup_phase))
    failed(result, control)
    assert phase['release_at'] is not None and any(value >= phase['release_at'] for value in phase['waits'])
    reaped(result, owned)


def test_final_nonblocking_wait_runs_even_after_root_reap_window(owned, monkeypatch):
    state = {'final': False, 'final_calls': 0}
    def waitpid(pid, flags):
        if owned.roots and pid == owned.roots[0]['pid']:
            if not state['final']:
                return 0, 0
            state['final_calls'] += 1
        return WAITPID(pid, flags)
    def cleanup_phase(name, _state):
        if name == 'before_final_waitpid':
            state['final'] = True
    with monkeypatch.context() as patch:
        patch.setattr(supervisor.os, 'waitpid', waitpid)
        # The actual child exits zero normally. The only failure is exhaustion
        # of its reap window; a final successful probe must not erase it.
        result, control = owned.run('normal', hooks=dict(cleanup_phase=cleanup_phase))
    failed(result, control)
    assert state['final_calls'] >= 1
    assert result['child_exit_code'] == 0
    assert result['cleanup']['final_waitpid_attempted'] is True
    assert 'SUPERVISOR_ROOT_REAP_TIMEOUT' in result['cleanup']['errors']
    reaped(result, owned)


def test_external_reap_is_unknown_never_synthesized_zero_exit(owned, foreign):
    consumed = []
    def external_reap(pid):
        consumed.append(WAITPID(pid, 0))
    result, control = owned.run('exit', hooks=dict(after_launch=external_reap))
    failed(result, control)
    assert consumed and consumed[0][0] == owned.roots[0]['pid']
    assert result['root_reaped'] is False
    assert result['child_exit_code'] is None
    assert not any(row.get('kind') == 'OWNED_ROOT_GROUP' for row in result['cleanup']['signals'])
    assert exact_live(foreign), 'TEST_FIXTURE_UNRELATED_PROCESS_WAS_SIGNALED'


def test_changed_kernel_start_never_authorizes_signal_or_success(owned, monkeypatch):
    original = supervisor._identity
    def changed(process):
        value = original(process)
        if owned.roots and value['pid'] == owned.roots[0]['pid']:
            return dict(value, create_time=value['create_time']+1)
        return value
    monkeypatch.setattr(supervisor, '_identity', changed)
    result, control = owned.run(limits=dict(max_seconds=.3, root_reap_seconds=.1))
    failed(result, control)
    assert not result['root_reaped']
    assert exact_live(owned.roots[0])
    assert not result['cleanup']['signals']
    # The test oracle, not a changed-identity production signal, owns teardown.


def test_failed_control_directory_cannot_launch_a_second_attempt(owned, monkeypatch):
    def fail_sample(_state):
        raise RuntimeError('TEST_FIXTURE_FORCE_CLEANUP')
    result, control = owned.run(hooks=dict(before_sample=fail_sample))
    failed(result, control)
    reaped(result, owned)
    def forbidden_launch(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_SECOND_ATTEMPT_LAUNCHED')
    monkeypatch.setattr(supervisor.subprocess, 'Popen', forbidden_launch)
    with pytest.raises(FileExistsError):
        owned.run(control=control)
    assert len(owned.roots) == 1


def test_three_generation_normal_exit_has_no_signals_and_measures_descendants(owned):
    result, _ = owned.run('natural_root', limits=dict(max_seconds=5, kill_grace_seconds=2,
                                                    root_reap_seconds=1))
    assert result['status'] == 'COMPLETE' and result['child_exit_code'] == 0
    assert result['cleanup']['signals'] == []
    assert result['max_observed_processes'] >= 3
    assert result['observed_peak_tree_rss_bytes'] >= 64*MIB
    assert result['observed_peak_tree_rss_bytes'] > result['observed_peak_root_rss_bytes']
    reaped(result, owned)


def test_tree_rss_breach_escalates_ignoring_processes_without_foreign_signal(owned, foreign):
    result, control = owned.run('rss_root', limits=dict(tree_rss_bytes=112*MIB, max_seconds=5,
        kill_grace_seconds=2, root_reap_seconds=1))
    failed(result, control)
    assert 'RSS' in result['failure_reason']
    assert result['observed_peak_tree_rss_bytes'] > 112*MIB
    sent = {row['signal'] for row in result['cleanup']['signals']}
    assert {signal.SIGTERM, signal.SIGKILL} <= sent
    assert exact_live(foreign), 'TEST_FIXTURE_UNRELATED_PROCESS_WAS_SIGNALED'
    reaped(result, owned)


@pytest.mark.parametrize('category', ['orphan', 'escaped'])
def test_exit_zero_root_with_live_same_or_new_session_descendants_is_failed(owned, category):
    result, control = owned.run(category+'_root', limits=dict(max_seconds=5, kill_grace_seconds=2,
                                                            root_reap_seconds=1))
    failed(result, control)
    assert result['failure_reason'] == 'SUPERVISOR_DESCENDANTS_REMAIN_AFTER_ROOT_EXIT'
    assert result['child_exit_code'] == 0 and result['max_observed_processes'] >= 3
    root = json.loads((owned.directory/(category+'_root.session.json')).read_bytes())
    grandchild = json.loads((owned.directory/(category+'_grandchild.session.json')).read_bytes())
    assert (grandchild['sid'] != root['sid']) is (category == 'escaped')
    assert not any(row.get('kind') == 'KERNEL_OWNED_UNREAPED_ROOT_GROUP'
                   for row in result['cleanup']['signals'])
    reaped(result, owned)


@pytest.mark.parametrize('observation_error,accepted', [
    (psutil.NoSuchProcess, True), (psutil.AccessDenied, False),
])
def test_missing_terminal_root_needs_actual_wait_status_and_no_denied_identity(
        owned, monkeypatch, observation_error, accepted):
    original_snapshot, original_identity = supervisor._snapshot, supervisor._identity
    state = {'missing': False, 'live_samples': 0, 'faults': 0}
    def identity(process):
        if owned.roots and process.pid == owned.roots[0]['pid'] and state['missing']:
            state['faults'] += 1
            raise observation_error(process.pid)
        return original_identity(process)
    def snapshot():
        rows, unavailable = original_snapshot()
        pid = owned.roots[0]['pid'] if owned.roots else None
        if pid is not None:
            try:
                terminal = PROCESS(pid).status() in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}
            except psutil.NoSuchProcess:
                terminal = state['missing']
            if terminal:
                state['missing'] = True
                rows.pop(pid, None)
                unavailable = [value for value in unavailable if value != pid]
            elif pid in rows:
                state['live_samples'] += 1
        return rows, unavailable
    monkeypatch.setattr(supervisor, '_identity', identity)
    monkeypatch.setattr(supervisor, '_snapshot', snapshot)
    result, control = owned.run('normal')
    assert state['missing'] and state['faults'] and state['live_samples'] > 0
    assert result['child_exit_code'] == 0
    if accepted:
        assert result['status'] == 'COMPLETE'
        assert result['cleanup']['signals'] == []
    else:
        failed(result, control)
    reaped(result, owned)


def test_missing_live_root_cannot_be_synthesized_as_terminal(owned, monkeypatch):
    original_identity, original_snapshot = supervisor._identity, supervisor._snapshot
    denied = []
    def identity(process):
        if owned.roots and process.pid == owned.roots[0]['pid']:
            denied.append(process.pid)
            raise psutil.NoSuchProcess(process.pid)
        return original_identity(process)
    def snapshot():
        rows, unavailable = original_snapshot()
        if owned.roots:
            rows.pop(owned.roots[0]['pid'], None)
        return rows, unavailable
    monkeypatch.setattr(supervisor, '_identity', identity)
    monkeypatch.setattr(supervisor, '_snapshot', snapshot)
    result, control = owned.run()
    failed(result, control)
    assert denied and result['observed_peak_root_rss_bytes'] == 0
    assert result['child_exit_code'] == -signal.SIGKILL
    reaped(result, owned)


@pytest.mark.parametrize('relationship', ['old_session', 'old_parent'])
def test_actual_reaped_root_cannot_grant_new_session_or_parent_ownership(owned, relationship):
    process = subprocess.Popen([sys.executable, '-I', '-B', str(owned.program),
                                str(owned.directory), 'exit'], start_new_session=True)
    owned.launch(process.pid)
    identity = owned.roots[0]
    anchor = supervisor.RootAnchor(process, identity)
    deadline = time.monotonic()+3
    while anchor.probe('TEST_FIXTURE_ACTUAL_WAIT') != 'REAPED' and time.monotonic() < deadline:
        time.sleep(.005)
    assert anchor.reaped and anchor.code == 0
    owner = supervisor.Ownership(identity, anchor)
    before = dict(owner.known)
    unknown = dict(pid=process.pid+100000, create_time=identity['create_time']+1,
        uid=identity['uid'], ppid=process.pid if relationship == 'old_parent' else 1,
        sid=process.pid if relationship == 'old_session' else None, pgid=None,
        status=psutil.STATUS_RUNNING, rss=4096)
    with pytest.raises(supervisor.SupervisorError, match='UNOBSERVED_PROCESS_AFTER_ROOT_REAP'):
        owner.observe({unknown['pid']: unknown})
    assert owner.known == before
