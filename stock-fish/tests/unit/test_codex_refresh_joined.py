"""TEST_FIXTURE CLI/coordinator/validation composition with genuine meters."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from scripts import codex_daily_v2 as daily
from scripts import codex_premarket_refresh as refresh
from scripts import codex_premarket_shared_v1 as shared
from stockfish_core.yaogu import premarket_workflow_v1 as frozen
from stockfish_core.yaogu.premarket_runtime_v1 import Budget
from tests.unit import test_codex_daily_entry_v2 as gateway_fixture
from tests.unit.test_codex_premarket_refresh import fixture as parent_fixture


def test_cli_real_refresh_coordinator_and_terminal_accounting_then_zero_worker_reuse(tmp_path, monkeypatch):
    f = parent_fixture(tmp_path, monkeypatch)
    runtime, instant = f['runtime'], f['instant']
    state = f['cache'].root.parent
    observation = datetime.fromisoformat(f['operation']['identity']['observation_date']).date()
    target = datetime.fromisoformat(f['operation']['identity']['target_session']).date()
    config_path = tmp_path / 'TEST_FIXTURE_registered_config.json'
    config_path.write_text(json.dumps(dict(state_root=str(state),
        capture=dict(allow_test_fixture=True, retry_failed=False), limits=f['proposal']['original_limits'])))
    config_before = config_path.read_bytes()
    prepared = dict(state=state, source=daily.ROOT,
                    python=daily.workspace.interpreter_path(sys.executable), config=config_path)
    snapshot = dict(configuration=daily.original._reference(config_path),
                    python={'TEST_FIXTURE': True}, shared_execution_sources=[])
    runtime.capture_recovery_context = lambda: None
    monkeypatch.setattr(daily.original, '_prepared', lambda _: prepared)
    monkeypatch.setattr(daily, '_snapshot', lambda _: deepcopy(snapshot))
    # Only the explicitly isolated fixture's clocks are replaced. All prepare,
    # execute, action, validate and final accounting functions remain genuine.
    monkeypatch.setattr(refresh, '_now', lambda proposal=None, **kwargs: instant)

    class FixtureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz or timezone.utc)

    monkeypatch.setattr(frozen, 'datetime', FixtureClock)
    harness = SimpleNamespace(state=state, calls=0, version=shared.VERSION,
                              observation=observation, target=target)
    worker_records = []

    def supervise(config, *, require_premarket=False, **kwargs):
        assert require_premarket is True
        assert 'continuation_context' not in kwargs and 'continuation_context' not in config
        assert kwargs['expected_sessions'] == (str(observation), str(target), str(target))
        code, final = gateway_fixture.GatewayTests.supervise(harness, config, **kwargs)
        job = Path(final['job'])
        completion_path = job / 'worker-completion.json'
        completion = json.loads(completion_path.read_bytes())
        deadline = shared._premarket_gate(kwargs['expected_sessions'], instant)
        completion['premarket_deadline'] = deadline
        completion['completed_at'] = instant.isoformat()
        completion['calendar_rows'] = [dict(calendar_date=str(observation + timedelta(days=i)),
            is_trading_day='1' if (observation + timedelta(days=i)).weekday() < 5 else '0')
            for i in range(-4, (target - observation).days + 5)]
        # This would fail READ_METER_ALREADY_ACTIVE if the coordinator or daily
        # wrapper left its independent meter active across the worker callback.
        worker_budget = Budget(config['limits'])
        with worker_budget.phase('capture'), worker_budget.meter_files():
            worker_budget.read(job / 'configuration.json', expected=final['configuration'])
        completion['metrics'] = worker_budget.metrics
        completion_path.write_text(json.dumps(completion))
        final['completion'] = frozen._ref(completion_path)
        final['premarket_deadline'] = deadline
        final['final_sealed_at'] = instant.isoformat()
        (job / 'final-control.json').write_text(json.dumps(final))
        worker_records.append((deepcopy(config), deepcopy(completion)))
        return code, final

    worker = SimpleNamespace(VERSION=shared.VERSION, code_refs=lambda: [], verify_job=shared.verify_job,
        supervise=supervise, _expected_gate=lambda expected: shared._expected_gate(expected, instant))
    monkeypatch.setattr(daily, '_workflow', lambda: worker)
    argv = ['premarket-refresh', '--operation', f['operation_ref']['path'],
            '--expected-terminal-sha256', f['terminal_ref']['sha256'], '--authorize-missing-refresh']
    args = daily.parser().parse_args(argv)
    args.allow_test_fixture = True
    originals = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}

    code, first = daily.execute(runtime, args)
    assert code == 2 and first['status'] == 'RECORDED' and first['new_action_calls'] == 1
    assert first['complete_daily_delivery'] is True and harness.calls == 1
    slot = f['slot'].parent / 'refresh'
    assert {path.name for path in slot.iterdir()} == {'proposal.json', 'claim.json', 'terminal.json'}
    proposal = json.loads((slot / 'proposal.json').read_bytes())
    claim = json.loads((slot / 'claim.json').read_bytes())
    terminal = json.loads((slot / 'terminal.json').read_bytes())
    assert proposal['proposal_id'] == refresh._digest(proposal) == claim['proposal_id'] == terminal['proposal_id']
    assert terminal['status'] == 'SUCCEEDED' and terminal['retry_authorized'] is False
    assert terminal['new_accounting']['worker_metrics'] == worker_records[0][1]['metrics']
    assert terminal['new_accounting']['worker_metrics']['returned_read_bytes']['capture'] > 0
    assert terminal['new_accounting']['coordinator_metrics']['returned_read_bytes']['capture'] > 0
    assert terminal['new_accounting']['wrapper_preparation_metrics']['pending_request_results'] == 0
    assert terminal['new_accounting']['cumulative_calls'] == f['terminal']['new_accounting']['cumulative_calls']
    assert worker_records[0][0]['limits'] == claim['remaining_limits'] == proposal['remaining_limits']
    assert worker_records[0][0]['limits']['max_requests'] < f['proposal']['original_limits']['max_requests']
    assert json.loads(Path(f['operation_ref']['path']).read_bytes())['phase'] == 'FAILED_OR_UNKNOWN'
    assert {path: path.read_bytes() for path in originals} == originals
    assert config_path.read_bytes() == config_before

    first_result_files = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    code, reused = daily.execute(runtime, args)
    assert code == 2 and reused['status'] == 'REUSED' and reused['new_action_calls'] == 0
    assert harness.calls == 1 and len(worker_records) == 1
    assert reused['receipt'] == first['receipt']
    assert {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()} == first_result_files
