"""Local-admin boundary tests; all state is isolated TEST_FIXTURE data."""
from argparse import Namespace
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from stockfish_core.jobs import JobService
from stockfish_core.storage import SQLiteStore, StorageError, _SCHEMA
from stockfish_core.yaogu import r2_closure_cli as cli


def arguments(tmp_path, **changes):
    state = tmp_path / 'TEST_FIXTURE_STATE'
    state.mkdir(exist_ok=True)
    value = dict(db=state / 'service.sqlite3', state=state,
        allow_test_fixture=False, command='r2-daily-plan')
    value.update(changes)
    return Namespace(**value)


def test_read_context_does_not_initialize_service_or_write(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    store = SQLiteStore(args.db)
    store.put_document('TEST_FIXTURE', 'original', {'status': 'UNKNOWN'})
    before = args.db.read_bytes()
    from stockfish_core.yaogu.candidate_service import CandidateService
    monkeypatch.setattr(CandidateService, '__init__', lambda *a, **k: pytest.fail('service startup forbidden'))
    with cli.service_context(args) as service:
        assert service.store.read_only is True
        assert service._all('TEST_FIXTURE') == [{'status': 'UNKNOWN'}]
        with pytest.raises(StorageError, match='READ_ONLY'):
            service.store.put_document('TEST_FIXTURE', 'must_not_exist', {})
        assert service.jobs.get('absent') is None
    assert args.db.read_bytes() == before
    assert not (args.state / 'yaogu').exists()
    assert not args.db.with_suffix('.sqlite3.jobs.lock').exists()


def test_active_service_rejected_before_mutable_store_open(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    store = SQLiteStore(args.db)
    owner = JobService(store, recover=False)
    from stockfish_core import storage
    monkeypatch.setattr(storage, 'SQLiteStore', lambda *a, **k: pytest.fail('mutable store opened before owner lock'))
    try:
        with pytest.raises(StorageError, match='another job worker'):
            with cli.service_context(args, writable=True):
                pytest.fail('active service must not be entered')
    finally:
        owner.shutdown(cancel_pending=False)


def test_local_admin_does_not_recover_or_cancel_unrelated_queue(tmp_path):
    args = arguments(tmp_path)
    store = SQLiteStore(args.db)
    old = JobService(store, recover=False)
    job = old.create('unrelated_TEST_FIXTURE', {'kind': 'TEST_FIXTURE'}, job_id='TEST_FIXTURE_OTHER')
    events = old.events(job['job_id'])
    old.shutdown(cancel_pending=False)
    with cli.service_context(args, writable=True) as service:
        assert service.jobs.get(job['job_id'])['state'] == 'QUEUED'
        assert service.jobs.events(job['job_id']) == events
        assert service._all('strategy_configs') == []
    with cli.service_context(args) as service:
        assert service.jobs.get(job['job_id']) == job
        assert service.jobs.events(job['job_id']) == events


def test_old_schema_rejected_without_migration(tmp_path):
    args = arguments(tmp_path)
    with sqlite3.connect(args.db) as connection:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.executescript(_SCHEMA)
        connection.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, checksum TEXT, applied_at TEXT)')
        connection.execute('INSERT INTO schema_migrations VALUES (1,?,?)',
            (hashlib.sha256(_SCHEMA.encode()).hexdigest(), 'TEST_FIXTURE'))
    before = args.db.read_bytes()
    with pytest.raises(ValueError, match='NO_AUTOMATIC_MIGRATION'):
        with cli.service_context(args, writable=True):
            pytest.fail('old schema accepted')
    assert args.db.read_bytes() == before
    with sqlite3.connect(args.db) as connection:
        assert connection.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == 1


def test_missing_database_never_created(tmp_path):
    args = arguments(tmp_path)
    with pytest.raises(ValueError, match='EXPLICIT_EXISTING'):
        with cli.service_context(args, writable=True):
            pass
    assert not args.db.exists()


def test_write_view_never_migrates_and_rechecks_schema_inside_transaction(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    SQLiteStore(args.db)
    monkeypatch.setattr(SQLiteStore, '_migrate', lambda *a: pytest.fail('migration forbidden'))
    with cli.service_context(args, writable=True) as service:
        # Simulate a maintenance writer changing schema after initial checks.
        with sqlite3.connect(args.db) as connection:
            connection.execute('DELETE FROM schema_migrations WHERE version=2')
        before = args.db.read_bytes()
        with pytest.raises(ValueError, match='NO_AUTOMATIC_MIGRATION'):
            service.store.put_document('TEST_FIXTURE', 'must_not_write', {})
        assert args.db.read_bytes() == before
        with sqlite3.connect(args.db) as connection:
            assert connection.execute('SELECT COUNT(*) FROM documents').fetchone()[0] == 0
            assert connection.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == 1


def test_replaced_database_file_is_rejected(tmp_path):
    args = arguments(tmp_path)
    SQLiteStore(args.db)
    with cli.service_context(args, writable=True) as service:
        original = args.db.with_name('TEST_FIXTURE_original.sqlite3')
        args.db.rename(original)
        SQLiteStore(args.db)
        with pytest.raises(ValueError, match='FILE_IDENTITY_CHANGED'):
            service.store.put_document('TEST_FIXTURE', 'must_not_write', {})
        assert SQLiteStore(args.db).list_documents('TEST_FIXTURE') == []


def test_interrupt_cancels_only_new_instance_futures(tmp_path):
    import threading
    import time
    args = arguments(tmp_path)
    store = SQLiteStore(args.db)
    old = JobService(store, recover=False)
    queued = old.create('TEST_FIXTURE_OTHER', {}, job_id='TEST_FIXTURE_OLD')
    events = old.events(queued['job_id'])
    old.shutdown(cancel_pending=False)
    started = threading.Event()
    def worker(ctx):
        started.set()
        while True:
            ctx.check_cancelled()
            time.sleep(.005)
    with pytest.raises(KeyboardInterrupt):
        with cli.service_context(args, writable=True) as service:
            service.jobs.register('TEST_FIXTURE_NEW', worker)
            own = service.jobs.submit('TEST_FIXTURE_NEW', {}, job_id='TEST_FIXTURE_NEW')
            assert started.wait(5)
            raise KeyboardInterrupt()
    with cli.service_context(args) as service:
        assert service.jobs.get(own['job_id'])['state'] == 'CANCELLED'
        assert service.jobs.get(queued['job_id']) == queued
        assert service.jobs.events(queued['job_id']) == events


def test_fixture_cli_requires_explicit_isolated_marker(tmp_path):
    args = arguments(tmp_path, allow_test_fixture=True)
    SQLiteStore(args.db)
    with pytest.raises(ValueError, match='TEST_FIXTURE_MARKER'):
        cli._fixture_boundary(args)
    (args.state / 'TEST_FIXTURE_ONLY').write_text('TEST_FIXTURE\n')
    cli._fixture_boundary(args)
    outside = tmp_path / 'outside.sqlite3'
    SQLiteStore(outside)
    args.db = outside
    with pytest.raises(ValueError, match='MUST_BE_INSIDE'):
        cli._fixture_boundary(args)


@pytest.mark.parametrize('value', [
    {'schema_version': 'wrong', 'files': {}},
    {'schema_version': 'SHORT_HORIZON_ARCHIVE_SOURCE_OVERRIDES_V1', 'files': {'../bad.py': '/example/source.py'}},
    {'schema_version': 'SHORT_HORIZON_ARCHIVE_SOURCE_OVERRIDES_V1', 'files': {'ok.py': 'relative.py'}},
])
def test_original_source_mapping_rejects_ambiguous_paths(tmp_path, value):
    path = tmp_path / 'TEST_FIXTURE_mapping.json'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        cli._overrides(path)


@pytest.mark.parametrize('owned', [True, False])
def test_wait_timeout_cancels_only_owned_work(monkeypatch, owned):
    clock = iter([0, 2])
    monkeypatch.setattr(cli.time, 'monotonic', lambda: next(clock))
    class Closure:
        cancelled = []
        def status(self, job_id):
            return {'job_id': job_id, 'state': 'RUNNING'}
        def cancel(self, job_id):
            self.cancelled.append(job_id)
    closure = Closure()
    code, result = cli._wait(closure, {'job_id': 'TEST_FIXTURE'}, 1, cancel_owned=owned)
    assert code == 4
    assert closure.cancelled == (['TEST_FIXTURE'] if owned else [])
    assert result['cancel_requested'] is owned


def test_invalid_wait_budget_rejected_before_service(tmp_path, monkeypatch):
    args = arguments(tmp_path, command='r2-daily-run', wait_seconds=float('nan'))
    monkeypatch.setattr(cli, 'service_context', lambda *a, **k: pytest.fail('service opened'))
    with pytest.raises(ValueError, match='WAIT_SECONDS'):
        cli.execute(args)


def test_console_sanitizes_arbitrary_exception(tmp_path, monkeypatch, capsys):
    def fail(args):
        raise RuntimeError('SECRET_TOKEN https://private.example/path')
    monkeypatch.setattr(cli, 'execute', fail)
    code = cli.main(['r2-daily-status', 'TEST_FIXTURE', '--db', str(tmp_path / 'db'), '--state', str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 3 and not captured.out
    assert 'SECRET_TOKEN' not in captured.err and 'private.example' not in captured.err
    assert json.loads(captured.err)['reason'] == 'LOCAL_R2_CLOSURE_FAILED'


def test_native_archive_to_daily_cli_without_mocking_authentication(tmp_path, monkeypatch):
    """Real original trios -> bridge -> exact saved run -> daily job -> read API.

    Clock is deliberately historical TEST_FIXTURE time. No verifier, input
    normalizer, outcome evaluator, database or job implementation is replaced.
    """
    from datetime import datetime
    import socket
    from tests.support.short_horizon_archive_fixture import fixture_archive
    from stockfish_core.yaogu import short_horizon_daily_closure_v1 as daily
    from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
    from stockfish_core.yaogu.short_horizon_outcomes_v1 import NAMESPACES, HORIZONS

    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('TEST_FIXTURE_NETWORK'))
    archives = [fixture_archive(tmp_path / ('TEST_FIXTURE_' + day), observation_date=day,
        seen_at=day + 'T12:00:00+00:00', generated_at=day + 'T13:00:00+00:00')
        for day in ('2026-09-01', '2026-09-15')]
    original, latest = archives
    args = arguments(tmp_path, allow_test_fixture=True)
    (args.state / 'TEST_FIXTURE_ONLY').write_text('TEST_FIXTURE\n')
    SQLiteStore(args.db)
    common = ['--db', str(args.db), '--state', str(args.state), '--allow-test-fixture']
    code, cert = cli.execute(cli.parser().parse_args(['r2-archive-import', str(original['job']),
        '--expected-final-sha256', original['final_sha256'], *common]))
    assert code == 0 and cert['run_id'] == original['run']['run_id']
    real_closure = daily.ShortHorizonDailyClosure
    now = datetime.fromisoformat('2026-09-15T14:00:00+00:00')
    monkeypatch.setattr(daily, 'ShortHorizonDailyClosure',
        lambda service, **kwargs: real_closure(service, clock=lambda: now, **kwargs))
    code, latest_cert = cli.execute(cli.parser().parse_args(['r2-archive-import', str(latest['job']),
        '--expected-final-sha256', latest['final_sha256'], *common]))
    assert code == 0 and latest_cert['input_id'] == latest['run']['input_id']
    daily_args = cli.parser().parse_args(['r2-daily-run', '--run-id', cert['run_id'],
        '--input-id', latest_cert['input_id'], *common])
    code, result = cli.execute(daily_args)
    assert code == 0, result
    job_id = result['job']['job_id']
    assert result['job']['state'] == 'SUCCEEDED'
    ledger = result['ledger']
    assert ledger['run_ids'] == [cert['run_id']] and ledger['input_id'] == latest['run']['input_id']
    assert ledger['runs'][0]['due_horizons'] == list(HORIZONS)
    with cli.service_context(args) as service:
        assert service.strategy_experiment(cert['run_id']) == original['run']
        assert service.store.get_document(bridge.RUN_BINDINGS, cert['run_id'])['bridge_id'] == cert['bridge_id']
        revisions = service.store.documents_for_run(NAMESPACES['outcomes'], cert['run_id'])
        assert len(revisions) == 1
        product = revisions[0]
        expected = {(row['instrument_id'], h) for row in original['run']['results'][
            'SHORT_HORIZON_STRENGTH_R2']['items'] for h in HORIZONS}
        assert {(row['instrument_id'], row['horizon']) for row in product['items']} == expected
        # Missing event/eligibility evidence stays UNKNOWN with absent metrics.
        for row in product['items']:
            if row['status'] == 'UNKNOWN':
                assert all(value is None for value in row['metrics'].values())
        details = service.strategy_experiment_item_detail(cert['run_id'], '600001.SH')
        assert details['instrument_id'] == '600001.SH'
        assert len(details['outcomes']) == len(HORIZONS)
    frozen_db = args.db.read_bytes()
    code, status = cli.execute(cli.parser().parse_args(['r2-daily-status', job_id, *common]))
    assert code == 0 and status['ledger'] == ledger
    code, pending = cli.execute(cli.parser().parse_args(['r2-daily-pending',
        '--input-id', latest['run']['input_id'], *common]))
    assert code == 0 and pending['indexed_run_total'] == 2
    assert args.db.read_bytes() == frozen_db
    code, repeat = cli.execute(daily_args)
    assert code == 0 and repeat['job']['job_id'] == job_id
    assert repeat['ledger'] == ledger
