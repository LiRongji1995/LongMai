"""TEST_FIXTURE: bounded execution and rejection of already-owned work."""
import json
from pathlib import Path
import sys

import psutil
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
from r16_r1_supervise import supervise, live_identity

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('budget', [float('nan'), float('inf'), 0, -1])
def test_nonfinite_or_nonpositive_budget_rejected_before_process(tmp_path, budget):
    with pytest.raises(ValueError, match='FINITE_POSITIVE_BUDGET'):
        supervise([sys.executable, '-c', 'raise AssertionError("must not run")'], tmp_path/'output',
            cwd=ROOT, source_root=ROOT, registry=tmp_path/'registry.json', budget_seconds=budget)
    assert not (tmp_path/'output').exists()


def test_recorded_live_descendant_blocks_even_if_previous_wrapper_succeeded(tmp_path):
    owner = psutil.Process()
    registry = tmp_path/'registry.json'
    registry.write_text(json.dumps({'status': 'SUCCESS', 'child': None,
        'observed_processes': [{'pid': owner.pid, 'create_time': owner.create_time()}]}), encoding='utf-8')
    with pytest.raises(RuntimeError, match='STILL_RUNNING'):
        supervise([sys.executable, '-c', 'raise AssertionError("must not run")'], tmp_path/'output',
            cwd=ROOT, source_root=ROOT, registry=registry, budget_seconds=5)
    assert not (tmp_path/'output').exists()


def test_timeout_keeps_raw_checkpoint_and_stops_only_owned_processes(tmp_path):
    registry = tmp_path/'registry.json'
    result = supervise([sys.executable, '-c', 'import time; print("TEST_FIXTURE_STARTED",flush=True); time.sleep(30)'],
        tmp_path/'output', cwd=ROOT, source_root=ROOT, registry=registry, budget_seconds=1.5)
    assert result['exit_code'] == 124 and result['status'] == 'TIMEOUT'
    assert (tmp_path/'output/timeout-checkpoint.json').is_file()
    assert not any(live_identity(row) for row in result['observed_processes'])
    assert psutil.Process().is_running()
    assert json.loads(registry.read_text())['exit_code'] == 124
