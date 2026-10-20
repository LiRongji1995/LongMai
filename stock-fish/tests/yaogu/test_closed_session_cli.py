"""Explicit historical entry points; all identifiers are TEST_FIXTURE values."""
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import pytest

from stockfish_core.yaogu import r2_closure_cli as cli
from stockfish_core.yaogu import short_horizon_daily_closure_v1 as daily


def arguments(command):
    return [command, '--db', '/TEST_FIXTURE/service.sqlite3', '--state', '/TEST_FIXTURE/state']


@pytest.mark.parametrize('missing', ['--business-session', '--knowledge-cutoff'])
def test_closed_session_cli_requires_both_explicit_clocks(missing):
    args = arguments('r2-closed-session-plan') + ['--run-id', 'TEST_FIXTURE_run', '--input-id', 'TEST_FIXTURE_input']
    if missing != '--business-session':
        args += ['--business-session', '2026-10-16']
    if missing != '--knowledge-cutoff':
        args += ['--knowledge-cutoff', '2026-10-16T15:51:06Z']
    with pytest.raises(SystemExit) as exc:
        cli.parser().parse_args(args)
    assert exc.value.code == 2


@pytest.mark.parametrize('command,writable', [
    ('r2-closed-session-plan', False), ('r2-closed-session-run', True),
    ('r2-closed-session-status', False), ('r2-closed-session-retry', True),
])
def test_closed_session_cli_dispatch_keeps_its_own_contract(monkeypatch, command, writable):
    calls = []
    args = arguments(command)
    if command.endswith(('plan', 'run')):
        args += ['--run-id', 'TEST_FIXTURE_run', '--input-id', 'TEST_FIXTURE_input',
                 '--business-session', '2026-10-16', '--knowledge-cutoff', '2026-10-16T15:51:06Z']
    else:
        args += ['TEST_FIXTURE_job']
    @contextmanager
    def context(config, *, writable=False):
        calls.append(('context', writable))
        yield SimpleNamespace()
    class Closure:
        def __init__(self, service, **kw):
            calls.append(('construct', kw))
        def plan_replay(self, runs, input_id, **kw):
            calls.append(('plan', runs, input_id, kw)); return {'status': 'TEST_FIXTURE_READY'}
        def submit_replay(self, runs, input_id, **kw):
            calls.append(('run', runs, input_id, kw)); return {'job_id': 'TEST_FIXTURE_job'}
        def status(self, job_id):
            calls.append(('status', job_id)); return {'job_id': job_id}
        def retry(self, job_id):
            calls.append(('retry', job_id)); return {'job_id': job_id}
    monkeypatch.setattr(cli, 'service_context', context)
    monkeypatch.setattr(daily, 'ClosedSessionClosure', Closure)
    monkeypatch.setattr(daily, 'ShortHorizonDailyClosure', lambda *a, **k: pytest.fail('WRONG_CURRENT_DAY_CONTRACT'))
    monkeypatch.setattr(cli, '_job_wait', lambda service, closure, job, wait: (0, job))
    code, result = cli.execute(cli.parser().parse_args(args))
    assert code == 0 and calls[0] == ('context', writable)
    if command.endswith(('plan', 'run')):
        assert calls[2][1:3] == (['TEST_FIXTURE_run'], 'TEST_FIXTURE_input')
        assert calls[2][3] == {'business_session': date(2026, 10, 16), 'evaluated_at': '2026-10-16T15:51:06Z'}
    else:
        assert calls[2] == (command.rsplit('-', 1)[1], 'TEST_FIXTURE_job')


def test_daily_cli_does_not_accept_historical_cutoff_implicitly():
    args = arguments('r2-daily-run') + ['--run-id', 'TEST_FIXTURE_run', '--input-id', 'TEST_FIXTURE_input',
                                     '--knowledge-cutoff', '2026-10-16T15:51:06Z']
    with pytest.raises(SystemExit) as exc:
        cli.parser().parse_args(args)
    assert exc.value.code == 2
