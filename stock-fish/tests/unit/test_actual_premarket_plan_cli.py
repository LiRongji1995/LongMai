"""Actual main-entry planning uses saved TEST_FIXTURE cache without collecting."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def complete_cache():
    from tests.unit.test_codex_premarket_preflight_v1 import PreflightTests
    case = PreflightTests()
    try:
        case.setUp()
        case.complete()
        yield case
    finally:
        case.doCleanups()


def cache_bytes(case):
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in case.cache.root.rglob('*') if path.is_file()}


@pytest.mark.parametrize('allow_fixture', [True, False])
def test_actual_cli_plan_preserves_cache_and_requires_explicit_fixture_flag(complete_cache, allow_fixture):
    case = complete_cache
    before = cache_bytes(case)
    args = [sys.executable, '-I', '-B', str(ROOT/'scripts/codex_daily_v2.py'),
            '--registry-root', str(case.registry), '--runtime', 'TEST_FIXTURE-core']
    if allow_fixture:
        args.append('--allow-test-fixture')
    args.append('premarket-plan')
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('PYTHON', 'STOCKFISH_', 'QLIB_', 'TUSHARE', 'OPENAI_'))
           and key != 'VIRTUAL_ENV'}
    completed = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert completed.stdout.strip(), (completed.returncode, completed.stderr)
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result['command_exit_code'] == completed.returncode
    if allow_fixture:
        assert completed.returncode == 0, result
        assert result['execution_admitted'] is True, result['reasons']
        assert result['observation_date'] == case.observed and result['target_session'] == case.target
        assert result['network_calls'] == result['strategy_calls'] == result['totals']['requests'] == 0
        assert len(result['core_sessions']) == 25 and len(result['theme_sessions']) == 67
    else:
        assert completed.returncode == 2 and result['reason'] == 'CORE_SOURCE_REAL_AND_FIXTURE_SEPARATION', result
    assert cache_bytes(case) == before
    assert list((case.runtime.directory/'operations').glob('*.json')) == []
