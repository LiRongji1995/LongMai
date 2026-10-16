"""Create a new, hashed daily/review runtime; never repair an existing venv."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import signal
import site
import subprocess
import sys
from urllib.parse import urlparse
import uuid
import venv

ROOT = Path(__file__).resolve().parents[1]
LOCK_ROOT = ROOT / 'runtime-locks'
MANIFEST = LOCK_ROOT / 'daily-review-py312-macos-x86_64.json'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean_environment():
    value = {key: item for key, item in os.environ.items()
             if not key.startswith(('PYTHON', 'PIP_', 'STOCKFISH_', 'QLIB_', 'CONDA_'))
             and key not in {'VIRTUAL_ENV', 'TUSHARE_TOKEN'}}
    value.update(PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
                 PIP_CONFIG_FILE=os.devnull)
    return value


def validate_lock(root=ROOT, manifest_path=MANIFEST):
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest['contract'] != 'DAILY_REVIEW_NATIVE_V1' or manifest['index_url'] != 'https://pypi.org/simple':
        raise ValueError('UNSUPPORTED_RUNTIME_LOCK')
    for filename, expected in manifest['source_requirements'].items():
        if Path(filename).name != filename or sha256(root / filename) != expected:
            raise ValueError('SOURCE_REQUIREMENTS_CHANGED_RELOCK_REQUIRED')
    for filename, expected in manifest['locks'].items():
        if Path(filename).name != filename or sha256(manifest_path.parent / filename) != expected:
            raise ValueError('RUNTIME_LOCK_HASH_MISMATCH')
    for package in manifest['packages']:
        parsed = urlparse(package['url'])
        if parsed.scheme != 'https' or parsed.hostname != 'files.pythonhosted.org' or parsed.username or parsed.password:
            raise ValueError('NON_PYPI_ARTIFACT_REJECTED')
        if not re.fullmatch(r'[0-9a-f]{64}', package['sha256']):
            raise ValueError('ARTIFACT_SHA256_REQUIRED')
    return manifest


def native_identity(manifest):
    identity = dict(python=platform.python_version(), implementation=platform.python_implementation(),
                    machine=platform.machine(), system=platform.system(), executable=sys.executable,
                    base_executable=getattr(sys, '_base_executable', sys.executable),
                    prefix=sys.prefix, base_prefix=sys.base_prefix)
    if (identity['implementation'] != 'CPython' or '.'.join(identity['python'].split('.')[:2]) != manifest['python_minor']
            or identity['system'] != manifest['system'] or identity['machine'] != manifest['machine']):
        raise ValueError('RUNTIME_PLATFORM_NOT_COVERED_BY_LOCK')
    if hasattr(os, 'uname') and os.uname().machine != identity['machine']:
        raise ValueError('NATIVE_ARCHITECTURE_MISMATCH')
    if identity['system'] == 'Darwin':
        probe = subprocess.run(['/usr/sbin/sysctl', '-in', 'sysctl.proc_translated'], capture_output=True, text=True)
        if probe.returncode == 0 and probe.stdout.strip() == '1':
            raise ValueError('TRANSLATED_INTERPRETER_REJECTED')
    return identity


def installed_identity():
    prefix = Path(sys.prefix).resolve()
    if sys.prefix == sys.base_prefix or site.ENABLE_USER_SITE:
        raise ValueError('ISOLATED_VENV_REQUIRED')
    if not sys.flags.isolated:
        raise ValueError('PYTHON_ISOLATED_FLAG_REQUIRED_USE_DASH_I')
    distributions = []
    for distribution in importlib.metadata.distributions():
        location = Path(distribution.locate_file('')).resolve()
        if not location.is_relative_to(prefix):
            raise ValueError('FOREIGN_DISTRIBUTION_PATH')
        direct = distribution.read_text('direct_url.json')
        if direct and json.loads(direct).get('dir_info', {}).get('editable'):
            raise ValueError('EDITABLE_DISTRIBUTION_REJECTED')
        record = distribution.read_text('RECORD')
        wheel = distribution.read_text('WHEEL')
        if record is None or wheel is None:
            raise ValueError('WHEEL_RECORD_REQUIRED')
        distributions.append({'name': distribution.metadata['Name'], 'version': distribution.version,
                              'location': str(location),
                              'record_sha256': hashlib.sha256(record.encode()).hexdigest(),
                              'wheel_metadata_sha256': hashlib.sha256(wheel.encode()).hexdigest()})
    for value in sys.path:
        path = Path(value).resolve()
        if ('site-packages' in path.parts or 'dist-packages' in path.parts) and not path.is_relative_to(prefix):
            raise ValueError('FOREIGN_SITE_PACKAGES_PATH')
    base_executable = Path(getattr(sys, '_base_executable', sys.executable)).resolve()
    return {'executable': sys.executable, 'prefix': str(prefix), 'base_prefix': sys.base_prefix,
            'base_executable': str(base_executable), 'base_executable_sha256': sha256(base_executable),
            'python': platform.python_version(), 'machine': platform.machine(),
            'user_site_enabled': site.ENABLE_USER_SITE, 'isolated': bool(sys.flags.isolated),
            'distributions': sorted(distributions, key=lambda item: item['name'].lower())}


def execute(argv, directory, report_dir, label, *, timeout=600):
    started = datetime.now(timezone.utc).isoformat()
    process = subprocess.Popen([str(item) for item in argv], cwd=directory, env=clean_environment(),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               start_new_session=os.name == 'posix')
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
    record = {'argv': [str(item) for item in argv], 'cwd': str(directory), 'started_at': started,
              'finished_at': datetime.now(timezone.utc).isoformat(), 'exit_code': process.returncode,
              'status': 'TIMED_OUT' if timed_out else 'COMPLETED', 'timeout_seconds': timeout,
              'stdout': stdout, 'stderr': stderr}
    (report_dir / (label + '.json')).write_text(json.dumps(record, indent=2), encoding='utf-8')
    (report_dir / (label + '.log')).write_text(stdout + stderr, encoding='utf-8')
    print(json.dumps({'step': label, 'exit_code': process.returncode, 'status': record['status']}), flush=True)
    if timed_out or process.returncode:
        raise RuntimeError('RUNTIME_STEP_FAILED_' + label)
    return stdout


def verify(destination, report_dir, manifest):
    python = destination / 'bin/python'
    common = [python, '-I', '-B']
    execute([*common, '-m', 'pip', '--isolated', 'check'], report_dir, report_dir, 'pip-check')
    source = 'import runpy,json; m=runpy.run_path(' + repr(str(Path(__file__).resolve())) + '); print(json.dumps(m["installed_identity"]()))'
    identity = json.loads(execute([*common, '-c', source], report_dir, report_dir, 'installed-identity'))
    if (Path(identity['prefix']).resolve() != destination.resolve()
            or identity['machine'] != manifest['machine']
            or '.'.join(identity['python'].split('.')[:2]) != manifest['python_minor']):
        raise ValueError('TARGET_RUNTIME_IDENTITY_MISMATCH')
    expected = {re.sub(r'[-_.]+', '-', package['name']).lower(): package['version'] for package in manifest['packages']}
    installed = {re.sub(r'[-_.]+', '-', package['name']).lower(): package['version'] for package in identity['distributions']}
    if installed != expected:
        raise ValueError('INSTALLED_DISTRIBUTIONS_DIFFER_FROM_LOCK')
    for profile in ('rules', 'daily', 'review'):
        execute([*common, ROOT / 'scripts/runtime_doctor.py', '--profile', profile,
                 '--timeout', '45', '--budget-seconds', '240', '--json'],
                report_dir, report_dir, 'doctor-' + profile)
    execute([*common, ROOT / 'scripts/runtime_environment_smoke.py', '--destination', report_dir / 'TEST_FIXTURE_smoke'],
            report_dir, report_dir, 'app-cli-database-smoke')
    return identity


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('create', 'verify'))
    parser.add_argument('--destination', type=Path, required=True, help='New private venv directory; never overwrites an existing path')
    args = parser.parse_args(argv)
    manifest = validate_lock()
    host = native_identity(manifest)
    destination = args.destination.expanduser().absolute()
    if args.action == 'create':
        if destination.exists() or destination.is_symlink():
            raise ValueError('DESTINATION_ALREADY_EXISTS')
        destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    elif not (destination / 'pyvenv.cfg').is_file():
        raise ValueError('EXISTING_VENV_REQUIRED_FOR_VERIFICATION')
    report_dir = destination / 'acceptance' / uuid.uuid4().hex
    report_dir.mkdir(parents=True, mode=0o700)
    status = {'contract': manifest['contract'], 'status': 'STARTED', 'host': host,
              'manifest_sha256': sha256(MANIFEST), 'destination': str(destination), 'report_directory': str(report_dir),
              'production_switched': False, 'qlib_installed_or_validated': False}
    try:
        if args.action == 'create':
            venv.EnvBuilder(with_pip=True, system_site_packages=False, clear=False).create(destination)
            pip = [destination / 'bin/python', '-I', '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check',
                   'install', '--no-cache-dir', '--index-url', manifest['index_url'], '--require-hashes']
            execute([*pip, '--only-binary=:all:', '-r', LOCK_ROOT / 'build-tools-py312.lock'], report_dir, report_dir, 'install-build-tools')
            execute([*pip, '--no-build-isolation', '-r', LOCK_ROOT / 'daily-review-py312-macos-x86_64.lock'], report_dir, report_dir, 'install-runtime')
        status['runtime'] = verify(destination, report_dir, manifest)
        status['status'] = 'ACCEPTED_TEST_FIXTURE_RUNTIME'
    except Exception as exc:
        status.update(status='FAILED', error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        (report_dir / 'summary.json').write_text(json.dumps(status, indent=2), encoding='utf-8')
        print(json.dumps({key: status[key] for key in ('status', 'destination', 'report_directory')}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
