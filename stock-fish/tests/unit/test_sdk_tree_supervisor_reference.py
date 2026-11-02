"""Independent real-process TEST_FIXTURE supervision boundaries.

No SDK, network or database. Process identities are independently captured with
creation times; emergency fixture cleanup touches only those exact identities.
Measured RSS is a sampled observation, never an unobserved instantaneous peak.
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

from scripts import codex_sdk_tree_supervisor_v1 as supervisor


MIB = 1024**2
TREE_PROGRAM = r'''
from pathlib import Path
import json,os,signal,socket,sqlite3,subprocess,sys,time
import psutil
role,mode,base_s,memory_s=sys.argv[1:]
base=Path(base_s);memory=int(memory_s)
MIB=1024**2
assert "TEST_FIXTURE" in str(base)
def forbidden(*a,**k):raise RuntimeError("TEST_FIXTURE_NO_NETWORK_OR_DATABASE")
socket.socket.connect=socket.socket.connect_ex=socket.create_connection=forbidden
sqlite3.connect=forbidden
if mode!="complete" and role!="foreign":signal.signal(signal.SIGTERM,signal.SIG_IGN)
identity=dict(role=role,pid=os.getpid(),create_time=psutil.Process().create_time(),
              pgid=os.getpgid(0),sid=os.getsid(0),tag="TEST_FIXTURE_SDK_TREE")
temporary=base/(role+".identity.tmp")
with temporary.open("x") as stream:
 json.dump(identity,stream);stream.flush();os.fsync(stream.fileno())
os.replace(temporary,base/(role+".json"))
block=bytearray(memory*MIB) if role in ("child","grandchild") else bytearray()
for offset in range(0,len(block),4096):block[offset]=17
with (base/(role+".ready")).open("x") as stream:stream.write("TEST_FIXTURE_READY")
children=[]
if role in ("root","child"):
 next_role="child" if role=="root" else "grandchild"
 command=[sys.executable,"-I","-B","-X","pycache_prefix="+str(base/("ABSENT_PYCACHE_"+next_role)),
          __file__,next_role,mode,str(base),memory_s]
 children.append(subprocess.Popen(command,start_new_session=next_role=="grandchild" and mode=="escaped_orphan"))
if mode=="complete":
 if role=="grandchild":time.sleep(.8)
 for child in children:
  assert child.wait(timeout=5)==0
elif mode in ("orphan","escaped_orphan") and role in ("root","child"):
 deadline=time.monotonic()+5
 while not (base/"grandchild.ready").exists() and time.monotonic()<deadline:time.sleep(.01)
 assert (base/"grandchild.ready").exists()
 time.sleep(.7 if role=="root" else .9)
 os._exit(0)
else:
 deadline=time.monotonic()+30
 while time.monotonic()<deadline:time.sleep(.05)
'''


def same_process(identity):
    try:
        process = psutil.Process(identity['pid'])
        return process if process.create_time() == identity['create_time'] else None
    except psutil.NoSuchProcess:
        return None


def is_live(identity):
    process = same_process(identity)
    if process is None:
        return False
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


class OwnedFixture:
    def __init__(self, directory):
        self.directory = directory
        self.program = directory / 'TEST_FIXTURE_tree.py'
        self.program.write_text(TREE_PROGRAM, encoding='utf-8')
        self.launched = []

    def after_launch(self, pid):
        process = psutil.Process(pid)
        self.launched.append(dict(pid=pid, create_time=process.create_time()))

    def identities(self):
        values = list(self.launched)
        for path in self.directory.glob('*.json'):
            value = json.loads(path.read_bytes())
            assert value['tag'] == 'TEST_FIXTURE_SDK_TREE'
            values.append(value)
        return {(v['pid'], v['create_time']): v for v in values}.values()

    def roles(self):
        return {row['role']: row for row in self.identities() if 'role' in row}

    def cleanup(self):
        # A failed assertion must not leave a test-owned process running. Never
        # infer ownership from a same-user scan, command name, or a bare PID.
        identities = list(self.identities())
        for row in reversed(identities):
            process = same_process(row)
            if process is not None:
                try:
                    if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                        process.kill()
                except psutil.NoSuchProcess:
                    pass
        deadline = time.monotonic() + 3
        while any(is_live(row) for row in identities) and time.monotonic() < deadline:
            time.sleep(.02)
        for row in self.launched:
            try:
                os.waitpid(row['pid'], os.WNOHANG)
            except ChildProcessError:
                pass
        assert not any(is_live(row) for row in identities), 'TEST_FIXTURE_OWNED_LIVE_PROCESS_LEAK'

    def run(self, mode, *, memory=1, tree_limit=256*MIB, hooks=None):
        control = self.directory / ('TEST_FIXTURE_control_' + mode)
        combined = dict(after_launch=self.after_launch, **(hooks or {}))
        limits = dict(tree_rss_bytes=tree_limit, self_rss_bytes=512*MIB,
            max_seconds=5, output_bytes=2*MIB, supervisor_read_bytes=64*MIB,
            sample_interval_seconds=.03, term_grace_seconds=.12, kill_grace_seconds=2)
        argv = [sys.executable, '-I', '-B', '-X',
            'pycache_prefix=' + str(self.directory / 'ABSENT_PYCACHE_root'),
            str(self.program), 'root', mode, str(self.directory), str(memory)]
        report = supervisor.run_supervised(argv, cwd=self.directory, env=dict(os.environ),
            limits=limits, control_dir=control, test_fixture=True, _test_hooks=combined)
        (self.directory / ('TEST_FIXTURE_returned_' + mode + '.txt')).write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2), encoding='utf-8')
        return report, control


@pytest.fixture(autouse=True)
def deny_parent_network_and_sql(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_NETWORK_OR_SQL_FORBIDDEN')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)


@pytest.fixture
def owned(tmp_path):
    assert os.name == 'posix', 'TEST_FIXTURE_REQUIRES_THE_REGISTERED_POSIX_RUNTIME'
    directory = tmp_path / 'TEST_FIXTURE_independent_sdk_tree'
    directory.mkdir()
    fixture = OwnedFixture(directory)
    try:
        yield fixture
    finally:
        fixture.cleanup()


def assert_reaped(owned, report):
    assert report['root_reaped'] is True
    assert report['all_observed_owned_exited'] is True
    deadline = time.monotonic() + 2
    while any(is_live(row) for row in owned.identities()) and time.monotonic() < deadline:
        time.sleep(.02)
    assert not any(is_live(row) for row in owned.identities())


def assert_failed_receipt(control, report):
    assert report['status'] == 'FAILED'
    assert report['failure_reason']
    saved = json.loads((control / 'completion.json').read_bytes())
    assert saved['status'] == 'FAILED'
    assert saved['failure_reason'] == report['failure_reason']


def test_real_three_generation_memory_is_measured_and_normal_exit_is_reaped(owned):
    report, control = owned.run('complete', memory=48)
    assert report['status'] == 'COMPLETE' and report['child_exit_code'] == 0
    assert set(owned.roles()) == {'root', 'child', 'grandchild'}
    # Two separately touched 48 MiB buffers outlive multiple sample intervals.
    # A root-only (or root plus one child) measurement cannot satisfy this oracle.
    assert report['observed_peak_tree_rss_bytes'] >= 96*MIB
    assert report['observed_peak_tree_rss_bytes'] > report['observed_peak_root_rss_bytes']
    assert report['sample_count'] >= 2
    assert report['max_observed_processes'] >= 3
    assert report['cleanup']['signals'] == []
    assert report['source_capability_issued'] is False
    assert 'NOT_OS_HARD_QUOTA' in report['observation_scope']
    assert json.loads((control / 'completion.json').read_bytes())['status'] == 'COMPLETE'
    assert_reaped(owned, report)


def test_rss_breach_kills_term_ignoring_descendants_without_touching_foreign_group(owned):
    foreign_directory = owned.directory / 'TEST_FIXTURE_foreign'
    foreign_directory.mkdir()
    foreign = subprocess.Popen([sys.executable, '-I', '-B', str(owned.program), 'foreign',
        'foreign', str(foreign_directory), '0'], start_new_session=True)
    foreign_identity = dict(pid=foreign.pid, create_time=psutil.Process(foreign.pid).create_time())
    try:
        report, control = owned.run('rss_breach', memory=64, tree_limit=128*MIB)
        assert_failed_receipt(control, report)
        assert 'RSS' in report['failure_reason']
        assert report['observed_peak_tree_rss_bytes'] > 128*MIB
        assert any(row['signal'] == signal.SIGKILL for row in report['cleanup']['signals'])
        assert is_live(foreign_identity), 'UNRELATED_PROCESS_GROUP_WAS_KILLED'
        assert set(owned.roles()) == {'root', 'child', 'grandchild'}
        assert_reaped(owned, report)
    finally:
        if foreign.poll() is None:
            foreign.terminate()
            try:
                foreign.wait(timeout=2)
            except subprocess.TimeoutExpired:
                foreign.kill()
                foreign.wait(timeout=2)


@pytest.mark.parametrize('mode', ['orphan', 'escaped_orphan'])
def test_zero_exit_parent_with_live_grandchild_is_failure_even_after_cleanup(owned, mode):
    report, control = owned.run(mode)
    roles = owned.roles()
    assert set(roles) == {'root', 'child', 'grandchild'}
    if mode == 'escaped_orphan':
        assert roles['grandchild']['sid'] != roles['root']['sid']
        assert roles['grandchild']['pgid'] != roles['root']['pgid']
    assert report['child_exit_code'] == 0
    assert_failed_receipt(control, report)
    assert report['failure_reason'] == 'SUPERVISOR_DESCENDANTS_REMAIN_AFTER_ROOT_EXIT'
    assert report['max_observed_processes'] >= 3
    assert_reaped(owned, report)


def test_sampling_failure_remains_failure_and_reaps_all_observed_processes(owned):
    calls = []
    def before_sample(state):
        calls.append(1)
        if len(calls) >= 12 and (owned.directory / 'grandchild.ready').exists():
            raise RuntimeError('TEST_FIXTURE_SAMPLE_UNAVAILABLE')
    report, control = owned.run('sample_failure', hooks=dict(before_sample=before_sample))
    assert len(calls) >= 12
    assert_failed_receipt(control, report)
    assert report['sample_count'] > 0
    assert_reaped(owned, report)


def test_utc_deadline_with_short_monotonic_runtime_stops_term_ignoring_tree(owned):
    def utc_now():
        now = datetime.now(timezone.utc)
        if (owned.directory / 'grandchild.ready').exists():
            return now + timedelta(seconds=30)
        return now
    started = time.monotonic()
    report, control = owned.run('utc_deadline', hooks=dict(utc_now=utc_now))
    elapsed = time.monotonic() - started
    assert elapsed < 5, 'TEST_FIXTURE_UTC_JUMP_MUST_PRECEDE_MONOTONIC_TIMEOUT'
    assert_failed_receipt(control, report)
    assert 'UTC' in report['failure_reason'] or 'TIME' in report['failure_reason']
    assert report['actual_utc_elapsed_seconds'] >= 30
    assert report['monotonic_elapsed_seconds'] < 5
    assert_reaped(owned, report)
