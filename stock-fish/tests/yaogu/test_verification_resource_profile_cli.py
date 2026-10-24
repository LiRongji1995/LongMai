"""TEST_FIXTURE: resource admission only; no real archive, DB, or network."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
from datetime import date

import pytest

from stockfish_core.yaogu import r2_closure_cli as cli
from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
from stockfish_core.yaogu import short_horizon_daily_closure_v1 as daily
from stockfish_core.yaogu import short_horizon_resource_profiles_v1 as resources


PROFILE = resources.FULL_ARCHIVE_900S_V1


def test_legacy_default_has_no_options_or_added_profile_descriptor():
    assert resources.resolve_verification_profile() is None
    assert resources.session_options() == {}
    session = bridge.ArchiveVerificationSession(**resources.session_options())
    assert session.max_seconds == 300
    assert session.max_rss_bytes == 2 * 1024**3


def test_explicit_resources_only_increase_time_and_pin_module():
    descriptor = resources.resolve_verification_profile(PROFILE)
    assert descriptor == {
        'schema_version': resources.VERSION,
        'profile': PROFILE,
        'implementation_sha256': hashlib.sha256(Path(resources.__file__).read_bytes()).hexdigest(),
        'limits': {'max_seconds': 900, 'max_rss_bytes': 2 * 1024**3,
                   'max_total_read_bytes': 8 * 1024**3,
                   'max_unique_bytes': 4 * 1024**3, 'max_files': 4096},
    }
    session = bridge.ArchiveVerificationSession(**resources.session_options(PROFILE))
    assert session.audit()['limits'] == {
        'seconds': 900, 'rss_bytes': 2 * 1024**3, 'total_read_bytes': 8 * 1024**3,
        'unique_bytes': 4 * 1024**3, 'files': 4096}


def test_resource_descriptions_and_kwargs_do_not_share_mutable_state():
    first = resources.resolve_verification_profile(PROFILE)
    first['limits']['max_seconds'] = 10**9
    options = resources.session_options(PROFILE)
    options['max_files'] = 10**9
    assert resources.session_options(PROFILE)['max_seconds'] == 900
    assert resources.session_options(PROFILE)['max_files'] == 4096


class StringSubclass(str):
    pass


@pytest.mark.parametrize('invalid', [True, False, 900, 900.0, {}, [], '', 'DEFAULT',
    'FULL_ARCHIVE_900S_V1 ', 'FULL_ARCHIVE_UNLIMITED', StringSubclass(PROFILE)])
def test_invalid_or_nonexact_profile_rejected(invalid):
    with pytest.raises(ValueError, match='VERIFICATION_PROFILE_INVALID'):
        resources.resolve_verification_profile(invalid)
    with pytest.raises(ValueError, match='VERIFICATION_PROFILE_INVALID'):
        resources.session_options(invalid)


def arguments(command, profile=None):
    # Use actual command contexts. Late cutoff fields are not ordinary daily
    # cycle inputs; injecting them used to exercise no real parser invocation.
    argv = [command]
    if command in cli.ARCHIVE_COMMANDS:
        argv += ['/TEST_FIXTURE_ONLY/archive', '--expected-final-sha256', '0'*64]
    if command != 'r2-archive-verify':
        argv += ['--db', '/TEST_FIXTURE_ONLY/service.sqlite3', '--state', '/TEST_FIXTURE_ONLY']
    if command.endswith(('-plan', '-run')) or command == 'r2-daily-cycle':
        argv += ['--run-id', 'TEST_FIXTURE_RUN']
        if command != 'r2-daily-cycle':
            argv += ['--input-id', 'TEST_FIXTURE_INPUT']
    if command.endswith(('-retry', '-status')):
        argv += ['TEST_FIXTURE_JOB']
    if command.endswith(('-plan', '-run', '-retry')) or command == 'r2-daily-cycle':
        argv += ['--max-runs', '2']
    if command.endswith(('-plan', '-run')) and command.startswith(('r2-closed-session-', 'r2-late-completed-')):
        argv += ['--business-session', '2026-10-16', '--knowledge-cutoff', '2026-10-16T15:51:06+00:00']
    if command.endswith(('-run', '-retry')) or command == 'r2-daily-cycle':
        argv += ['--wait-seconds', '1200']
    if profile is not None:
        argv += ['--verification-profile', profile]
    return cli.parser().parse_args(argv)


@pytest.mark.parametrize('profile', [None, PROFILE])
def test_archive_verify_passes_explicit_session_only_when_requested(monkeypatch, profile):
    seen = {}
    class Capability:
        def audit(self):
            return {'status': 'TEST_FIXTURE_VERIFIED'}
    def authenticate(*args, **kwargs):
        seen.update(kwargs)
        return Capability()
    monkeypatch.setattr(bridge, 'authenticate_archive', authenticate)
    code, result = cli.execute(arguments('r2-archive-verify', profile))
    assert code == 0
    if profile is None:
        assert 'session' not in seen
        assert result == {'status': 'TEST_FIXTURE_VERIFIED'}
    else:
        assert type(seen['session']) is bridge.ArchiveVerificationSession
        assert seen['session'].max_seconds == 900
        assert result['verification_resource_profile'] == resources.resolve_verification_profile(PROFILE)


@pytest.mark.parametrize('profile', [None, PROFILE])
@pytest.mark.parametrize('command', ['r2-daily-run', 'r2-daily-retry',
    'r2-closed-session-run', 'r2-closed-session-retry', 'r2-daily-cycle',
    'r2-late-completed-run', 'r2-late-completed-retry'])
def test_closure_profile_and_wait_have_independent_arguments(monkeypatch, profile, command):
    seen = {}
    service = object()
    @contextmanager
    def context(*args, **kwargs):
        assert kwargs == {'writable': True}
        yield service
    class Closure:
        def __init__(self, actual, **kwargs):
            assert actual is service
            seen['closure_kwargs'] = kwargs
        def submit(self, *args, **kwargs):
            return {'job_id': 'TEST_FIXTURE_JOB'}
        submit_replay = submit
        submit_late = submit
        retry = submit
    def wait(actual, closure, job, seconds):
        assert actual is service
        seen['wait_seconds'] = seconds
        return 0, {'job': job}
    monkeypatch.setattr(cli, 'service_context', context)
    monkeypatch.setattr(cli, '_job_wait', wait)
    monkeypatch.setattr(daily, 'ShortHorizonDailyClosure', Closure)
    monkeypatch.setattr(daily, 'ClosedSessionClosure', Closure)
    monkeypatch.setattr(daily, 'LateCompletedSessionClosure', Closure)
    monkeypatch.setattr(bridge, 'authenticate_archive', lambda *a, **k: object())
    monkeypatch.setattr(bridge, 'commit_bridge', lambda *a, **k: {'input_id': 'TEST_FIXTURE_INPUT'})
    code, _ = cli.execute(arguments(command, profile))
    assert code == 0
    assert seen['closure_kwargs'] == ({'max_runs': 2} if profile is None
        else {'max_runs': 2, 'verification_profile': PROFILE})
    assert seen['wait_seconds'] == 1200


def test_invalid_profile_fails_before_archive_or_service(monkeypatch):
    monkeypatch.setattr(cli, 'service_context', lambda *a, **k: pytest.fail('DB access forbidden'))
    monkeypatch.setattr(bridge, 'authenticate_archive', lambda *a, **k: pytest.fail('archive access forbidden'))
    with pytest.raises(ValueError, match='VERIFICATION_PROFILE_INVALID'):
        args = arguments('r2-archive-import')
        args.verification_profile = 'UNLIMITED'  # Deliberately invalid direct execute input.
        cli.execute(args)


def test_cli_default_wait_remains_120_and_does_not_imply_larger_session():
    args = cli.parser().parse_args(['r2-daily-run', '--db', '/TEST_FIXTURE_ONLY/db',
        '--state', '/TEST_FIXTURE_ONLY', '--run-id', 'TEST_FIXTURE', '--input-id', 'TEST_FIXTURE'])
    assert args.wait_seconds == 120
    assert args.verification_profile is None


def test_cli_explicit_known_profile_does_not_change_wait():
    args = cli.parser().parse_args(['r2-daily-run', '--db', '/TEST_FIXTURE_ONLY/db',
        '--state', '/TEST_FIXTURE_ONLY', '--run-id', 'TEST_FIXTURE', '--input-id', 'TEST_FIXTURE',
        '--verification-profile', PROFILE])
    assert args.wait_seconds == 120
    assert args.verification_profile == PROFILE


def test_cli_rejects_arbitrary_resource_profile():
    with pytest.raises(SystemExit) as exc:
        cli.parser().parse_args(['r2-archive-verify', '/TEST_FIXTURE_ONLY/archive',
            '--expected-final-sha256', '0'*64, '--verification-profile', 'UNLIMITED'])
    assert exc.value.code == 2


@pytest.mark.parametrize('command', ['r2-late-completed-plan', 'r2-late-completed-status'])
def test_late_read_commands_only_use_readonly_service(monkeypatch, command):
    seen = {}
    @contextmanager
    def context(*args, **kwargs):
        assert kwargs == {'writable': False}
        yield object()
    class Closure:
        def __init__(self, service, **kwargs):
            seen['options'] = kwargs
        def plan_late(self, run_ids, input_id, **kwargs):
            seen['request'] = dict(run_ids=run_ids, input_id=input_id, **kwargs)
            return {'status': 'TEST_FIXTURE_PLAN'}
        def status(self, job_id):
            assert job_id == 'TEST_FIXTURE_JOB'
            return {'status': 'TEST_FIXTURE_STATUS'}
    monkeypatch.setattr(cli, 'service_context', context)
    monkeypatch.setattr(daily, 'LateCompletedSessionClosure', Closure)
    code, result = cli.execute(arguments(command, PROFILE if command.endswith('plan') else None))
    assert code == 0
    if command.endswith('plan'):
        assert seen['options'] == {'max_runs': 2, 'verification_profile': PROFILE}
        assert seen['request'] == dict(run_ids=['TEST_FIXTURE_RUN'], input_id='TEST_FIXTURE_INPUT',
            business_session=date(2026, 10, 16), evaluated_at='2026-10-16T15:51:06+00:00')
    else:
        assert seen['options'] == {'max_runs': 5}


@pytest.mark.parametrize('command', ['r2-late-completed-plan', 'r2-late-completed-run'])
def test_late_cli_requires_explicit_session_and_cutoff(command):
    common = [command, '--db', '/TEST_FIXTURE_ONLY/db', '--state', '/TEST_FIXTURE_ONLY',
              '--run-id', 'TEST_FIXTURE', '--input-id', 'TEST_FIXTURE']
    with pytest.raises(SystemExit):
        cli.parser().parse_args(common)
    with pytest.raises(SystemExit):
        cli.parser().parse_args(common+['--business-session', '2026-10-20'])
    args = cli.parser().parse_args(common+['--business-session','2026-10-20',
        '--knowledge-cutoff','2026-10-21T02:00:00+08:00', '--verification-profile',PROFILE])
    assert args.verification_profile == PROFILE
    assert str(args.business_session) == '2026-10-20'
    assert args.knowledge_cutoff == '2026-10-21T02:00:00+08:00'


@pytest.mark.parametrize('profile', [None, PROFILE])
def test_plain_cycle_actual_parser_does_not_supply_late_or_copy_inputs(profile):
    args = arguments('r2-daily-cycle', profile)
    assert args.business_session is None and args.knowledge_cutoff is None
    assert args.source_protocol is None
    assert args.local_copy_manifest is None and args.local_copy_manifest_sha256 is None
    assert args.restoration_manifest is None and args.restoration_manifest_sha256 is None
    assert args.verification_profile == profile and args.wait_seconds == 1200
    assert args.run_id == ['TEST_FIXTURE_RUN'] and args.max_runs == 2


@pytest.mark.parametrize('profile', [None, PROFILE])
@pytest.mark.parametrize('late_fields', [
    ['--business-session', '2026-10-16'],
    ['--knowledge-cutoff', '2026-10-16T15:51:06+00:00'],
    ['--business-session', '2026-10-16', '--knowledge-cutoff', '2026-10-16T15:51:06+00:00'],
])
def test_explicit_cycle_late_inputs_without_copy_protocol_rejected_before_io(monkeypatch, profile, late_fields):
    argv = ['r2-daily-cycle', '/TEST_FIXTURE_ONLY/archive', '--expected-final-sha256', '0'*64,
        '--db', '/TEST_FIXTURE_ONLY/service.sqlite3', '--state', '/TEST_FIXTURE_ONLY',
        '--run-id', 'TEST_FIXTURE_RUN', '--wait-seconds', '1200', *late_fields]
    if profile is not None:
        argv += ['--verification-profile', profile]
    args = cli.parser().parse_args(argv)
    monkeypatch.setattr(cli, 'service_context', lambda *a, **k: pytest.fail('DB access forbidden'))
    monkeypatch.setattr(bridge, 'authenticate_archive', lambda *a, **k: pytest.fail('archive access forbidden'))
    with pytest.raises(ValueError, match='^LOCAL_COPY_CYCLE_PROTOCOL_REQUIRED_FOR_LATE_ARGUMENTS$'):
        cli.execute(args)
    assert args.verification_profile == profile and args.wait_seconds == 1200
