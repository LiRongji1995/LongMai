"""Opt-in CLI routing only; identifiers and all mocked authority are TEST_FIXTURE.

Fresh source authentication and numeric outcomes are exercised separately by
the consumer tests. These tests never open a real service or change a locator.
"""
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from stockfish_core.yaogu import r2_closure_cli as cli
from stockfish_core.yaogu import dated_identity_outcome_closure_v1 as dated
from stockfish_core.yaogu import sealed_result_local_copy_v1 as copies
from stockfish_core.yaogu import short_horizon_daily_closure_v1 as daily


MANIFEST = {'schema_version': 'TEST_FIXTURE_PINNED_REFERENCE'}


def arguments(command):
    result = [command, '--db', '/TEST_FIXTURE/service.sqlite3', '--state', '/TEST_FIXTURE/state',
        '--source-protocol', 'SEALED_RESULT_LOCAL_COPY_V1',
        '--local-copy-manifest', '/TEST_FIXTURE/local-copy-manifest.json',
        '--local-copy-manifest-sha256', 'a' * 64]
    if command == 'r2-dated-input-register':
        result += ['--base-input-id', 'TEST_FIXTURE_base', '--identity-input-id', 'TEST_FIXTURE_identity']
    elif command.endswith(('plan', 'run')):
        result += ['--run-id', 'TEST_FIXTURE_run', '--input-id', 'TEST_FIXTURE_derived',
            '--business-session', '2026-10-23', '--knowledge-cutoff', '2026-10-24T12:30:00Z']
    else:
        result += ['TEST_FIXTURE_job']
    return result


def mocks(monkeypatch):
    calls = []
    @contextmanager
    def context(config, *, writable=False):
        calls.append(('context', writable))
        yield SimpleNamespace(TEST_FIXTURE=True)
    def reference(path, sha):
        calls.append(('manifest', path, sha))
        return MANIFEST
    monkeypatch.setattr(cli, 'service_context', context)
    monkeypatch.setattr(copies, 'manifest_reference', reference)
    return calls


@pytest.mark.parametrize('command,writable', [
    ('r2-dated-late-plan', False), ('r2-dated-late-run', True),
    ('r2-dated-late-status', False), ('r2-dated-late-retry', True),
])
def test_dated_late_dispatch_is_explicit_and_status_readonly(monkeypatch, command, writable):
    calls = mocks(monkeypatch)
    class Closure:
        def __init__(self, service, **kw):
            calls.append(('construct', kw))
        def plan_late(self, runs, input_id, **kw):
            calls.append(('plan', runs, input_id, kw)); return {'status': 'TEST_FIXTURE_READY'}
        def submit_late(self, runs, input_id, **kw):
            calls.append(('run', runs, input_id, kw)); return {'job_id': 'TEST_FIXTURE_job'}
        def status(self, job_id):
            calls.append(('status', job_id)); return {'job_id': job_id}
        def retry(self, job_id):
            calls.append(('retry', job_id)); return {'job_id': job_id}
    monkeypatch.setattr(dated, 'DatedIdentityLateClosure', Closure)
    monkeypatch.setattr(daily, 'LocalCopyLateCompletedSessionClosure',
                        lambda *a, **k: pytest.fail('DEFAULT_LOCAL_COPY_CLOSURE_SELECTED'))
    monkeypatch.setattr(daily, 'ShortHorizonDailyClosure',
                        lambda *a, **k: pytest.fail('DEFAULT_DAILY_CLOSURE_SELECTED'))
    def wait(service, closure, job, seconds):
        calls.append(('wait', job['job_id'], seconds)); return 0, job
    monkeypatch.setattr(cli, '_job_wait', wait)
    code, result = cli.execute(cli.parser().parse_args(arguments(command)))
    assert code == 0
    assert calls[:3] == [('manifest', Path('/TEST_FIXTURE/local-copy-manifest.json'), 'a' * 64),
                         ('context', writable), ('construct', {'max_runs': 5, 'local_copy_manifest_ref': MANIFEST})]
    action = command.rsplit('-', 1)[1]
    if action in ('plan', 'run'):
        assert calls[3] == (action, ['TEST_FIXTURE_run'], 'TEST_FIXTURE_derived',
            {'business_session': date(2026, 10, 23), 'evaluated_at': '2026-10-24T12:30:00Z'})
    else:
        assert calls[3] == (action, 'TEST_FIXTURE_job')
    assert [call for call in calls if call[0] == 'wait'] == (
        [('wait', 'TEST_FIXTURE_job', 120)] if action in ('run', 'retry') else [])


@pytest.mark.parametrize('profile', [None, 'FULL_ARCHIVE_900S_V1'])
def test_register_uses_matching_job_service_and_profile_only_when_explicit(monkeypatch, profile):
    calls = mocks(monkeypatch)
    def register(service, **kw):
        calls.append(('register', kw)); return {'input_id': 'TEST_FIXTURE_new'}
    monkeypatch.setattr(dated, 'register_input', register)
    args = arguments('r2-dated-input-register') + ['--identity-input-id', 'TEST_FIXTURE_second_identity']
    if profile:
        args += ['--verification-profile', profile]
    code, result = cli.execute(cli.parser().parse_args(args))
    expected = dict(base_input_id='TEST_FIXTURE_base',
        identity_input_ids=['TEST_FIXTURE_identity', 'TEST_FIXTURE_second_identity'],
        local_copy_manifest_ref=MANIFEST)
    if profile:
        expected['verification_profile'] = profile
    assert code == 0 and result == {'input_id': 'TEST_FIXTURE_new'}
    assert calls[1:] == [('context', True), ('register', expected)]


@pytest.mark.parametrize('command', sorted(cli.DATED_COMMANDS))
@pytest.mark.parametrize('missing', ['--db', '--state', '--source-protocol', '--local-copy-manifest',
                                     '--local-copy-manifest-sha256'])
def test_every_dated_command_requires_explicit_boundaries(command, missing):
    args = arguments(command)
    index = args.index(missing)
    del args[index:index + 2]
    with pytest.raises(SystemExit) as exc:
        cli.parser().parse_args(args)
    assert exc.value.code == 2


@pytest.mark.parametrize('missing', ['--base-input-id', '--identity-input-id'])
def test_register_requires_both_parent_roles(missing):
    args = arguments('r2-dated-input-register')
    index = args.index(missing)
    del args[index:index + 2]
    with pytest.raises(SystemExit) as exc:
        cli.parser().parse_args(args)
    assert exc.value.code == 2


@pytest.mark.parametrize('missing', ['--business-session', '--knowledge-cutoff', '--input-id', '--run-id'])
def test_dated_plan_requires_exact_runs_input_and_both_clocks(missing):
    args = arguments('r2-dated-late-plan')
    index = args.index(missing)
    del args[index:index + 2]
    with pytest.raises(SystemExit) as exc:
        cli.parser().parse_args(args)
    assert exc.value.code == 2


@pytest.mark.parametrize('command', sorted(cli.DATED_COMMANDS))
def test_dated_commands_reject_restoration_in_place_of_copy_protocol(command):
    args = arguments(command)
    args[args.index('--source-protocol') + 1] = 'SEALED_RESULT_BYTES_RESTORED_V1'
    with pytest.raises(SystemExit) as exc:
        cli.parser().parse_args(args)
    assert exc.value.code == 2


def test_mixed_protocol_arguments_rejected_before_service(monkeypatch):
    args = cli.parser().parse_args(arguments('r2-dated-input-register') +
        ['--restoration-manifest', '/TEST_FIXTURE/restored.json', '--restoration-manifest-sha256', 'b' * 64])
    monkeypatch.setattr(cli, 'service_context', lambda *a, **k: pytest.fail('DATABASE_OPENED'))
    with pytest.raises(ValueError, match='EXPLICIT_SOURCE_PROTOCOL_AND_PINNED_MANIFEST_REQUIRED'):
        cli.execute(args)


@pytest.mark.parametrize('flag,value', [('--verification-profile', 'FULL_ARCHIVE_900S_V1'),
                                       ('--wait-seconds', '900'), ('--input-id', 'TEST_FIXTURE_input')])
def test_status_has_no_write_or_execution_flags(flag, value):
    with pytest.raises(SystemExit) as exc:
        cli.parser().parse_args(arguments('r2-dated-late-status') + [flag, value])
    assert exc.value.code == 2


def test_default_daily_command_still_has_no_dated_source_options():
    args = cli.parser().parse_args(['r2-daily-plan', '--db', '/TEST_FIXTURE/db', '--state', '/TEST_FIXTURE/state',
                                   '--input-id', 'TEST_FIXTURE_input', '--run-id', 'TEST_FIXTURE_run'])
    assert not hasattr(args, 'source_protocol')
    assert args.verification_profile is None


def test_wait_expiry_only_cancels_owned_dated_job(monkeypatch):
    clock = iter([0, 2])
    monkeypatch.setattr(cli.time, 'monotonic', lambda: next(clock))
    class Closure:
        cancelled = []
        def status(self, job_id):
            return {'job_id': job_id, 'state': 'RUNNING'}
        def cancel(self, job_id):
            self.cancelled.append(job_id)
    closure = Closure()
    code, result = cli._wait(closure, {'job_id': 'TEST_FIXTURE_dated_job'}, 1, cancel_owned=False)
    assert code == 4 and closure.cancelled == [] and result['cancel_requested'] is False
