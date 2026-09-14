"""Bounded evidence command runner with durable registry and process-family RSS.

Pass the command after --. Reconnecting callers inspect registry/run/checkpoint
before launching. A live registered process is never duplicated or killed by a
different invocation. This runner only terminates its own verified descendants.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback

import psutil


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def source_metadata(root):
    root = Path(root).resolve()
    def git(*args):
        try:
            return subprocess.check_output(['git', '-C', str(root), *args], text=True,
                stderr=subprocess.DEVNULL, timeout=10).strip()
        except (OSError, subprocess.SubprocessError):
            return None
    # Archives inside another checkout must not inherit that checkout's HEAD.
    actual_root = git('rev-parse', '--show-toplevel')
    valid_root = actual_root is not None and Path(actual_root).resolve() == root
    return {'source_root': str(root), 'git_root_matches': valid_root,
        'commit': git('rev-parse', 'HEAD') if valid_root else None,
        'tree': git('rev-parse', 'HEAD^{tree}') if valid_root else None,
        'tracked_dirty': bool(git('status', '--porcelain', '--untracked-files=no')) if valid_root else None,
        'status_raw': git('status', '--short') if valid_root else None,
        'branch': git('branch', '--show-current') if valid_root else None}


def live_identity(value):
    if not value or not value.get('pid'):
        return False
    try:
        process = psutil.Process(value['pid'])
        return process.is_running() and abs(process.create_time()-value['create_time']) < .01
    except (psutil.Error, KeyError):
        return False


def supervise(command, output, *, cwd, source_root, registry, budget_seconds, input_manifest=None, label=None):
    if not math.isfinite(budget_seconds) or budget_seconds <= 0:
        raise ValueError('FINITE_POSITIVE_BUDGET_REQUIRED')
    registry = Path(registry).resolve()
    registry.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(registry)+'.lock').open('a+b') as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _supervise_locked(command, output, cwd=cwd, source_root=source_root, registry=registry,
            budget_seconds=budget_seconds, input_manifest=input_manifest, label=label)


def _supervise_locked(command, output, *, cwd, source_root, registry, budget_seconds, input_manifest=None, label=None):
    output, registry = Path(output).resolve(), Path(registry).resolve()
    if output.exists():
        raise ValueError('EVIDENCE_OUTPUT_EXISTS_SELECT_NEW_DIRECTORY')
    registry.parent.mkdir(parents=True, exist_ok=True)
    previous = json.loads(registry.read_text(encoding='utf-8')) if registry.exists() else {}
    prior_children = [previous.get('child'), *previous.get('observed_processes', [])]
    if (any(live_identity(row) for row in prior_children) or
            previous.get('status') in {'RUNNING', 'ORPHANED_ACTIVE'} and live_identity(previous.get('supervisor'))):
        raise RuntimeError('REGISTERED_EVIDENCE_PROCESS_STILL_RUNNING_INSPECT_EXISTING_JOB')
    output.mkdir(parents=True)
    started, started_perf = now(), time.perf_counter()
    owner = psutil.Process()
    meta = {'evidence_runner_version': 'R16_R1_BOUNDED_PROCESS_FAMILY_V1', 'label': label,
        'command_argv': command, 'cwd': str(Path(cwd).resolve()), 'start': started,
        'budget_seconds': budget_seconds, 'source': source_metadata(source_root),
        'input_manifest': {'path': str(Path(input_manifest).resolve()), 'sha256': sha256(input_manifest)} if input_manifest else None,
        'environment': {'python': sys.version, 'python_executable': sys.executable, 'psutil': psutil.__version__,
            'os': platform.platform(), 'machine': platform.machine(), 'logical_cpu_count': psutil.cpu_count(),
            'physical_cpu_count': psutil.cpu_count(logical=False), 'ram_bytes': psutil.virtual_memory().total},
        'supervisor': {'pid': owner.pid, 'create_time': owner.create_time()}, 'child': None,
        'stdout': str(output/'stdout.log'), 'stderr': str(output/'stderr.log'),
        'status': 'RUNNING', 'rss_scope': 'CHILD_AND_ALL_OBSERVED_DESCENDANTS_EXCLUDES_SUPERVISOR',
        'rss_sampling_seconds': .1, 'heartbeat_seconds': 10, 'eta_seconds': None,
        'previous_registry': {key: previous.get(key) for key in ('label', 'status', 'child', 'end')} if previous else None}
    write_json(registry, meta)
    write_json(output/'run.json', meta)
    known, peak, last_heartbeat = {}, 0, -10.0
    process = None
    try:
        with (output/'stdout.log').open('w', encoding='utf-8') as stdout, (output/'stderr.log').open('w', encoding='utf-8') as stderr, (output/'rss.jsonl').open('w', encoding='utf-8') as rss_log:
            process = subprocess.Popen(command, cwd=cwd, stdout=stdout, stderr=stderr)
            child = psutil.Process(process.pid)
            meta['child'] = {'pid': child.pid, 'create_time': child.create_time()}
            write_json(registry, meta); write_json(output/'run.json', meta)
            while True:
                elapsed = time.perf_counter()-started_perf
                try:
                    family = [child, *child.children(recursive=True)]
                except psutil.Error:
                    family = []
                rows, total_rss = [], 0
                for item in family:
                    try:
                        memory, cpu, io = item.memory_info(), item.cpu_times(), item.io_counters()
                        row = {'pid': item.pid, 'parent_pid': item.ppid(), 'create_time': item.create_time(),
                            'command_argv': item.cmdline(), 'rss_bytes': memory.rss,
                            'native_peak_working_set_bytes': getattr(memory, 'peak_wset', None),
                            'cpu_user_seconds': cpu.user, 'cpu_system_seconds': cpu.system,
                            'io_read_bytes': io.read_bytes, 'io_write_bytes': io.write_bytes}
                        known[item.pid] = row
                        rows.append(row); total_rss += memory.rss
                    except psutil.Error:
                        pass
                peak = max(peak, total_rss)
                sample = {'recorded_at': now(), 'perf_counter': time.perf_counter(), 'elapsed_seconds': elapsed, 'rss_family_bytes': total_rss,
                    'rss_family_sampled_peak_bytes': peak, 'processes': rows}
                rss_log.write(json.dumps(sample, ensure_ascii=False)+'\n')
                if elapsed-last_heartbeat >= 10:
                    write_json(output/'checkpoint.json', sample)
                    meta['observed_processes'] = list(known.values())
                    write_json(registry, meta)
                    rss_log.flush(); stdout.flush(); stderr.flush()
                    print(json.dumps({'event': 'HEARTBEAT', 'label': label, 'pid': process.pid,
                        'elapsed_seconds': elapsed, 'rss_family_bytes': total_rss, 'eta_seconds': None}), flush=True)
                    last_heartbeat = elapsed
                code = process.poll()
                if code is not None:
                    remaining = [row for row in known.values() if live_identity(row)]
                    if remaining:
                        meta.update(status='ORPHANED_ACTIVE', exit_code=125, live_descendants=remaining,
                                    wrapper_exit_code=code)
                    else:
                        meta.update(status='SUCCESS' if code == 0 else 'FAILED', exit_code=code)
                    break
                if elapsed >= budget_seconds:
                    meta.update(status='TIMEOUT', exit_code=124)
                    write_json(output/'timeout-checkpoint.json', sample)
                    terminating = []
                    for row in reversed(list(known.values())):
                        if live_identity(row):
                            try:
                                owned = psutil.Process(row['pid'])
                                owned.terminate()
                                terminating.append(owned)
                            except psutil.Error:
                                pass
                    psutil.wait_procs(terminating, timeout=5)
                    process.wait(timeout=10)
                    remaining = [row for row in known.values() if live_identity(row)]
                    if remaining:
                        meta.update(status='ORPHANED_ACTIVE', live_descendants=remaining)
                    break
                time.sleep(.1)
    except BaseException as exc:
        meta.update(status='RUNNER_FAILED', exit_code=125, error_type=type(exc).__name__, traceback=traceback.format_exc())
        if process is not None and process.poll() is None:
            # Preserve a RUNNING registry if ownership could not be safely stopped.
            meta['status'] = 'RUNNING'
        raise
    finally:
        meta.update(end=now(), elapsed_seconds=time.perf_counter()-started_perf,
            rss_family_sampled_peak_bytes=peak, observed_processes=list(known.values()))
        write_json(output/'run.json', meta); write_json(registry, meta)
        print(json.dumps({key: meta.get(key) for key in ('label','status','exit_code','elapsed_seconds','rss_family_sampled_peak_bytes')}, ensure_ascii=False), flush=True)
    return meta


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--registry', required=True)
    parser.add_argument('--cwd', default=str(Path.cwd()))
    parser.add_argument('--source-root', default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument('--budget-seconds', type=float, required=True)
    parser.add_argument('--input-manifest')
    parser.add_argument('--label', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command or args.budget_seconds <= 0:
        parser.error('positive budget and command after -- required')
    result = supervise(command, args.output, cwd=args.cwd, source_root=args.source_root,
        registry=args.registry, budget_seconds=args.budget_seconds, input_manifest=args.input_manifest, label=args.label)
    raise SystemExit(result['exit_code'])


if __name__ == '__main__':
    main()
