"""Offline TEST_FIXTURE app, job, CLI and SQLite smoke in a fresh private directory."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args(argv)
    target = args.destination.absolute()
    if target.exists() or target.is_symlink():
        raise ValueError('TEST_FIXTURE_DESTINATION_MUST_BE_NEW')
    target.mkdir(parents=True, mode=0o700, exist_ok=False)
    bootstrap = runpy.run_path(str(ROOT / 'scripts/bootstrap_daily_runtime.py'))
    bootstrap['installed_identity']()
    # CLI help imports the real terminal surface but never runs a collection.
    cli = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'scripts/stockfish.py'), '--help'],
                         cwd=target, env=bootstrap['clean_environment'](), capture_output=True, text=True, timeout=30)
    (target / 'cli-help.json').write_text(json.dumps({'argv': [sys.executable, '-I', '-B', str(ROOT / 'scripts/stockfish.py'), '--help'],
        'exit_code': cli.returncode, 'stdout': cli.stdout, 'stderr': cli.stderr}, indent=2), encoding='utf-8')
    if cli.returncode != 0 or 'premarket' not in cli.stdout:
        raise AssertionError('REAL_CLI_HELP_FAILED')
    def guard(event, arguments):
        if event.startswith('socket.'):
            raise PermissionError('TEST_FIXTURE_NETWORK_DENIED')
        if event == 'open' and isinstance(arguments[0], (str, bytes)):
            name = Path(os.fsdecode(arguments[0])).name.lower()
            if name == '.env' or name.startswith('.env.'):
                raise PermissionError('TEST_FIXTURE_ENV_FILE_READ_DENIED')
    sys.addaudithook(guard)
    sys.path.insert(0, str(ROOT))  # Project source only; no third-party path injection.
    from stockfish_core.api import create_app
    from stockfish_core.service import ApplicationService
    from stockfish_core.storage import SQLiteStore, ImmutableConflict
    database = target / 'TEST_FIXTURE.sqlite3'
    store = SQLiteStore(database)
    fixture = {'coverage_type': 'TEST_FIXTURE', 'purpose': 'RUNTIME_SMOKE_NO_MARKET_CLAIM', 'value': None}
    core = ApplicationService(root=target / 'TEST_FIXTURE_state', store=store,
        data_dir=target / 'TEST_FIXTURE_no_qlib_data', allow_test_fixture=True,
        quant=SimpleNamespace(health=lambda: {'status': 'unavailable', 'reason': 'TEST_FIXTURE_NO_QLIB'}))
    try:
        client = create_app(core).test_client()
        assert client.get('/health/live').status_code == 200
        assert client.get('/health/ready').status_code == 503  # No scheduler was started.
        def handler(context):
            return context.complete(fixture, lambda connection: store._put_document(
                connection, 'TEST_FIXTURE_runtime', 'TEST_FIXTURE_result', fixture))
        core.jobs.register('TEST_FIXTURE_runtime', handler)
        job = core.jobs.submit('TEST_FIXTURE_runtime', {'coverage_type': 'TEST_FIXTURE'})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            saved = client.get('/api/jobs/' + job['job_id']).get_json()
            if saved['state'] in {'SUCCEEDED', 'FAILED', 'CANCELLED'}:
                break
            time.sleep(.01)
        assert saved['state'] == 'SUCCEEDED', saved
        assert store.get_document('TEST_FIXTURE_runtime', 'TEST_FIXTURE_result') == fixture
        try:
            store.put_document('TEST_FIXTURE_runtime', 'TEST_FIXTURE_result', {**fixture, 'value': 1})
        except ImmutableConflict:
            pass
        else:
            raise AssertionError('IMMUTABILITY_GUARD_FAILED')
    finally:
        core.close()
    assert SQLiteStore(database).get_document('TEST_FIXTURE_runtime', 'TEST_FIXTURE_result') == fixture
    result = {'status': 'PASSED', 'scope': 'TEST_FIXTURE', 'cli_help': True, 'flask_in_process': True,
              'durable_atomic_job': True, 'database_reopened': True, 'immutability': True,
              'http_listener_started': False, 'scheduler_started': False, 'real_market_data_used': False,
              'market_edge': 'NOT_ESTABLISHED'}
    (target / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
