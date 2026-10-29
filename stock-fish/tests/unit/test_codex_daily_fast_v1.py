"""TEST_FIXTURE explicit fast gateway, admission and durable no-retry guards."""
from datetime import timedelta
import json
from pathlib import Path
from types import FunctionType
from unittest.mock import patch

import pytest

from scripts import codex_daily_fast_v1 as gateway
from scripts import codex_premarket_fast_v1 as fast
from tests.unit import test_codex_daily_entry_v2 as daily_fixture
from tests.unit import test_codex_runtime_recovery_v1 as recovery_fixture


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    import socket
    import sqlite3
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_NETWORK_AND_DATABASE_FORBIDDEN')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)


@pytest.fixture
def fixture():
    value = daily_fixture.GatewayTests(methodName='runTest')
    value.setUp()
    value.version = fast.VERSION
    value.worker.VERSION = fast.VERSION
    frozen = daily_fixture.frozen
    value.worker.verify_job = FunctionType(frozen.verify_job.__code__,
        {**frozen.verify_job.__globals__, 'VERSION': fast.VERSION}, frozen.verify_job.__name__)
    try:
        with patch.object(gateway, 'fast', value.worker):
            yield value
    finally:
        value.doCleanups()


def test_complete_registered_flow_preserves_exact_once_and_versions(fixture):
    code, result = gateway.execute(fixture.runtime, fixture.args)
    assert code == 2 and result['status'] == 'RECORDED'
    assert result['receipt']['schema_version'] == gateway.VERSION
    assert result['receipt']['execution_engine'] == 'original'
    assert result['execution_version'] == gateway.VERSION
    assert result['validation']['source_authority_restored'] is False
    assert result['validation']['complete_daily_delivery'] is True
    assert result['validation']['parent_budget_scope'] == 'FRESH_ACTION_SUPERVISION_AND_RECEIPT_VALIDATION'
    with patch.object(gateway.daily, '_plan', side_effect=AssertionError('TEST_FIXTURE_NO_RECAPTURE')):
        second_code, second = gateway.execute(fixture.runtime, fixture.args)
    assert second_code == 2 and second['status'] == 'REUSED' and fixture.calls == 1
    assert second['receipt'] == result['receipt']
    assert second['validation']['parent_budget_scope'] == 'CURRENT_READONLY_REUSE_VALIDATION'
    assert gateway.daily.VERSION == 'CODEX_DAILY_EXECUTION_PIPELINE_V2'


def test_blocked_preflight_does_not_claim_request_or_start_worker(fixture):
    fixture.admission['execution_admitted'] = False
    code, result = gateway.execute(fixture.runtime, fixture.args)
    assert code == 4 and result['status'] == 'BLOCKED_BEFORE_COLLECTION'
    assert result['worker_started'] is result['request_claimed'] is False
    assert not fixture.runtime.directory.exists() and fixture.calls == 0


def test_locked_admission_is_rechecked_before_start(fixture):
    with patch.object(gateway.daily, '_plan', side_effect=[fixture.admission,
            {**fixture.admission, 'execution_admitted': False}]):
        with pytest.raises(ValueError, match='ADMISSION_CHANGED_BEFORE_EXECUTION'):
            gateway.execute(fixture.runtime, fixture.args)
    assert fixture.calls == 0
    records = list((fixture.runtime.directory/'operations').glob('*.json'))
    assert len(records) == 1
    assert json.loads(records[0].read_text())['phase'] == 'FAILED_OR_UNKNOWN'


@pytest.mark.parametrize('changed_key', [False, True])
def test_old_v2_request_cannot_be_retried_through_fast(fixture, changed_key):
    gateway.daily.execute(fixture.runtime, fixture.args)
    if changed_key:
        fixture.args.request_key = 'TEST_FIXTURE_different_key'
    with pytest.raises(ValueError, match='EXISTING_(REQUEST|SESSION)_RECONCILE_NO_RETRY'):
        gateway.execute(fixture.runtime, fixture.args)
    assert fixture.calls == 1


@pytest.mark.parametrize('changed_key', [False, True])
def test_failed_request_cannot_restart_with_same_or_new_key(fixture, changed_key):
    with patch.object(fixture.worker, 'supervise', return_value=(4, {'status': 'BUDGET_EXCEEDED'})) as worker:
        with pytest.raises(ValueError, match='NO_AUTOMATIC_RETRY'):
            gateway.execute(fixture.runtime, fixture.args)
        if changed_key:
            fixture.args.request_key = 'TEST_FIXTURE_changed_failed_key'
        with pytest.raises(ValueError, match='(STATE_UNKNOWN|EXISTING_SESSION)_RECONCILE_NO_RETRY'):
            gateway.execute(fixture.runtime, fixture.args)
    assert worker.call_count == 1


def test_current_session_new_key_and_old_key_new_sessions_are_refused(fixture):
    gateway.execute(fixture.runtime, fixture.args)
    old_key = fixture.args.request_key
    fixture.args.request_key = 'TEST_FIXTURE_new_key'
    with pytest.raises(ValueError, match='EXISTING_SESSION_RECONCILE_NO_RETRY'):
        gateway.execute(fixture.runtime, fixture.args)
    fixture.args.request_key = old_key
    fixture.args.observation_date += timedelta(days=1)
    fixture.args.target_session += timedelta(days=1)
    with pytest.raises(ValueError, match='KEY_REUSED_ACROSS_SESSIONS'):
        gateway.execute(fixture.runtime, fixture.args)
    assert fixture.calls == 1


def test_source_change_and_archived_physical_change_cannot_create_retry(fixture):
    _, first = gateway.execute(fixture.runtime, fixture.args)
    fixture.snapshot['TEST_FIXTURE_changed_source'] = True
    with pytest.raises(ValueError, match='EXISTING_REQUEST_RECONCILE_NO_RETRY'):
        gateway.execute(fixture.runtime, fixture.args)
    del fixture.snapshot['TEST_FIXTURE_changed_source']
    path = Path(first['receipt']['job'])/'all.csv'
    replacement = path.with_suffix('.TEST_FIXTURE_new_inode')
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
    with pytest.raises(ValueError, match='PHYSICAL_IDENTITY_CHANGED'):
        gateway.execute(fixture.runtime, fixture.args)
    assert fixture.calls == 1


def test_shared_engine_is_rejected_before_journal_creation(fixture):
    fixture.args.engine = 'shared'
    with pytest.raises(ValueError, match='ORIGINAL_ENGINE_REQUIRED'):
        gateway.execute(fixture.runtime, fixture.args)
    assert fixture.calls == 0 and not fixture.runtime.directory.exists()


@pytest.mark.parametrize('recovered', [False, True])
def test_main_loads_only_the_explicit_registry_kind(fixture, recovered, capsys):
    args = ['--runtime', 'TEST_FIXTURE_registered', 'premarket-plan']
    if recovered:
        args.insert(0, '--recovered-runtime')
    with patch.object(gateway.daily.workspace, 'load_runtime', return_value=fixture.runtime) as normal, \
         patch.object(gateway.recovery, 'load_runtime', return_value=fixture.runtime) as restored, \
         patch.object(gateway.recovery, 'apply_recovery', side_effect=AssertionError('TEST_FIXTURE_NO_AUTO_RECOVERY')), \
         patch.object(gateway.daily.workspace, 'register_profile', side_effect=AssertionError('TEST_FIXTURE_NO_REGISTRATION')), \
         patch.object(gateway, '_registered', return_value=None):
        assert gateway.main(args) == 0
    assert restored.call_count == int(recovered) and normal.call_count == int(not recovered)
    loader = restored if recovered else normal
    assert loader.call_args.args[0] == 'TEST_FIXTURE_registered'
    assert 'allow_test_fixture' not in loader.call_args.kwargs
    value = json.loads(capsys.readouterr().out)
    assert value['entry_version'] == gateway.VERSION and value['command_exit_code'] == 0
    assert fixture.calls == 0 and not fixture.runtime.directory.exists()


def test_recovery_ancestor_history_still_blocks_changed_key_and_version():
    value = recovery_fixture.RecoveryFixture(methodName='runTest')
    value.setUp()
    try:
        runtime = value.recover()
        for identity in (value.existing,
                {**value.existing, 'schema_version': gateway.VERSION},
                {**value.existing, 'request_key': 'TEST_FIXTURE_bypass_attempt'}):
            with pytest.raises(ValueError, match='ANCESTOR_REQUEST_RECONCILE_NO_RETRY'):
                gateway._prior_request(runtime, identity)
        gateway._prior_request(runtime, value.fresh_identity())
        assert not (runtime.directory/'operations').exists()
    finally:
        value.doCleanups()


def test_full_code_identity_contains_gateway_and_transitive_recovery_sources():
    references = fast.code_refs()
    paths = {Path(row['path']).name for row in references}
    assert {'codex_daily_fast_v1.py', 'codex_premarket_fast_v1.py',
            'codex_runtime_recovery_v1.py', 'codex_daily_v2.py',
            'codex_workspace.py', 'codex_first_core.py',
            'short_horizon_archive_bridge_v1.py'} <= paths
    assert all(len(row['sha256']) == 64 and len(row['fp']) == 7 for row in references)


def test_readonly_legacy_commands_and_defaults_remain_unchanged(fixture):
    args = gateway.parser().parse_args(['show', '--role', 'all.csv'])
    with patch.object(gateway.daily.legacy, 'execute', return_value=(0, {'TEST_FIXTURE': True})) as old:
        assert gateway.execute(fixture.runtime, args) == (0, {'TEST_FIXTURE': True})
    old.assert_called_once_with(fixture.runtime, args)
    assert gateway.daily.parser().parse_args(['premarket', '--observation-date', '2026-10-26',
        '--target-session', '2026-10-27', '--request-key', 'TEST_FIXTURE']).engine == 'original'
