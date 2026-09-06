"""Run real commands and record immutable, redacted evidence; never infer PASS."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def redact(value):
    value = str(value)
    for key, secret in os.environ.items():
        if len(secret) >= 8 and any(word in key.upper() for word in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY')):
            value = value.replace(secret, '[REDACTED]')
    return re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._-]+', r'\1[REDACTED]', value)


def run_command(command, cwd, output, timeout=1800, *, input_path=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc)
    tick = time.perf_counter()
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    try:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=timeout,
                                input=Path(input_path).read_bytes() if input_path is not None else None)
        code, stdout, stderr, state = result.returncode, result.stdout, result.stderr, 'EXECUTED'
    except subprocess.TimeoutExpired as exc:
        code, stdout, stderr, state = None, exc.stdout or b'', exc.stderr or b'', 'TIMED_OUT'
    except OSError as exc:
        code, stdout, stderr, state = None, b'', str(exc).encode(), 'ENVIRONMENT_BLOCKED'
    for name, data in [('stdout.log', stdout), ('stderr.log', stderr)]:
        (output / name).write_text(redact(data.decode('utf-8', errors='replace')), encoding='utf-8')
    record = dict(command=[redact(x) for x in command], cwd=str(Path(cwd).resolve()),
                  started_at=started.isoformat(), ended_at=datetime.now(timezone.utc).isoformat(),
                  elapsed_seconds=time.perf_counter()-tick, exit_code=code, execution_state=state,
                  result='PASS' if code == 0 else 'BLOCKED' if code is None else 'FAIL',
                  environment={'python': sys.version, 'platform': platform.platform()},
                  logs={n:sha(output/n) for n in ('stdout.log','stderr.log')})
    if input_path is not None:
        record['stdin']={'path':str(Path(input_path).resolve()),'sha256':sha(input_path),'bytes':Path(input_path).stat().st_size,'contents_logged':False}
    (output/'execution.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cwd', default='.')
    parser.add_argument('--output', required=True)
    parser.add_argument('--timeout', type=int, default=1800)
    parser.add_argument('--input-path')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args=parser.parse_args()
    command=args.command[1:] if args.command[:1]==['--'] else args.command
    if not command:parser.error('command required')
    result=run_command(command,args.cwd,args.output,args.timeout,input_path=args.input_path)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result['exit_code'] if result['exit_code'] is not None else 124


if __name__=='__main__':
    raise SystemExit(main())
