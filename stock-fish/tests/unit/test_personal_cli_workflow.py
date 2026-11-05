"""Exercise the actual single CLI in fresh processes and isolated TEST_FIXTURE state."""
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from scripts import codex_workspace as workspace
from stockfish_core.storage import SQLiteStore
from tests.support.local_service import ResearchService
from tests.yaogu.fixtures import make_bundle

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / 'scripts/codex_daily_v2.py'


def command(registry, *arguments):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('PYTHON', 'STOCKFISH_', 'QLIB_', 'TUSHARE'))
           and key not in {'OPENAI_API_KEY', 'VIRTUAL_ENV'}}
    run = subprocess.run([sys.executable, '-I', '-B', str(ENTRY), '--allow-test-fixture',
                          '--registry-root', str(registry), *map(str, arguments)],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    lines = run.stdout.strip().splitlines()
    assert lines, (run.returncode, run.stderr)
    result = json.loads(lines[-1])
    assert result['command_exit_code'] == run.returncode
    return run.returncode, result


def documents(db):
    with sqlite3.connect(db.as_uri()+'?mode=ro', uri=True) as connection:
        return connection.execute('SELECT namespace,document_id,content_hash FROM documents ORDER BY namespace,document_id').fetchall()


@pytest.fixture
def research(tmp_path):
    root = (tmp_path / 'TEST_FIXTURE_local_cli').resolve()
    root.mkdir()
    state = root / 'state'
    state.mkdir()
    (state / 'TEST_FIXTURE_ONLY').write_bytes(b'TEST_FIXTURE\n')
    reports = root / 'reports'
    reports.mkdir()
    db = state / 'research.sqlite3'
    core = ResearchService(root=state, store=SQLiteStore(db), allow_test_fixture=True)
    bundle, _ = make_bundle(8)
    try:
        input_id = core.candidates.register_input(bundle, activate=False)
    finally:
        core.close()
    config = root / 'config.json'
    config.write_text(json.dumps({'state_root':str(state), 'capture':{'allow_test_fixture':True},
                                 'limits':{'max_seconds':60}}))
    profile = root / 'profile.json'
    profile.write_text(json.dumps({'schema_version':workspace.PROFILE_VERSION, 'name':'TEST_FIXTURE',
        'backend':'premarket', 'state_root':str(state),'report_root':str(reports),
        'source_root':str(ROOT),'python':str(Path(sys.executable).absolute()),'config':str(config),
        'storage':{'roots':[{'name':'fixture','path':str(root),'role':'runtime'}],
                   'target_root':'fixture','expected_growth_bytes':1024,
                   'min_free_bytes':0,'max_managed_bytes':128*1024**2}}))
    registry = root / 'registry'
    workspace.register_profile(profile, registry, make_default=True)
    return root, registry, state, db, input_id


def test_actual_cli_research_saved_history_export_outcomes_backup_and_restore(research):
    root, registry, state, db, input_id = research
    common = ['--db', db, '--state', state]
    code, first = command(registry, 'research', *common, '--input-id',input_id,
                          '--request-key','TEST_FIXTURE_daily', '--wait-seconds','60')
    assert code == 0, first
    assert first['reused'] is False
    run_id = first['receipt']['run_id']
    before = documents(db)
    code, repeated = command(registry,'research',*common,'--input-id',input_id,
                             '--request-key','TEST_FIXTURE_daily','--wait-seconds','60')
    assert code == 0 and repeated['reused'] is True, repeated
    assert documents(db) == before
    code, changed_key = command(registry,'research',*common,'--input-id',input_id,
                               '--request-key','TEST_FIXTURE_changed_key','--wait-seconds','60')
    assert code == 2 and 'NO_RETRY' in changed_key['reason'], changed_key
    code, saved = command(registry,'history',*common,'--run-id',run_id)
    assert code == 0 and saved['read_only'] is True, saved
    document = json.loads(saved['content'])
    assert document['run_id'] == run_id
    assert set(document['results']) == {'PV_OBSERVATION_V1','STAGE_AWARE_MOMENTUM_V2'}
    assert document['coverage_type'] == 'TEST_FIXTURE'
    assert document['results']['STAGE_AWARE_MOMENTUM_V2']['market_edge'] == 'NOT_ESTABLISHED'
    output = root / 'saved.csv'
    code, exported = command(registry,'history',*common,'--run-id',run_id,'--format','csv','--output',output)
    assert code == 0 and exported['read_only'] is True, exported
    original = output.read_bytes()
    assert hashlib.sha256(original).hexdigest() == exported['sha256']
    code, duplicate = command(registry,'history',*common,'--run-id',run_id,'--format','csv','--output',output)
    assert code == 2 and output.read_bytes() == original, duplicate
    assert documents(db) == before
    code, outcome = command(registry,'research-outcomes',*common,'--run-id',run_id,'--input-id',input_id,
                            '--request-key','TEST_FIXTURE_outcomes','--update','--wait-seconds','60')
    assert code == 0, outcome
    before = documents(db)
    code, viewed = command(registry,'research-outcomes',*common,'--run-id',run_id)
    assert code == 0 and viewed['read_only'] is True, viewed
    assert viewed['outcomes']
    assert all(item['metrics']['close_reference_change'] is None for item in viewed['outcomes'])
    assert documents(db) == before
    code, backup = command(registry,'backup','--database',db,'--artifact-root',reports_path(root),
                           '--destination',root/'backup')
    assert code == 0 and backup['status'] == 'VERIFIED', backup
    code, verified = command(registry,'verify-backup','--backup',root/'backup')
    assert code == 0 and verified['status'] == 'VERIFIED', verified
    code, restored = command(registry,'restore','--backup',root/'backup','--destination',root/'restored')
    assert code == 0 and restored['status'] == 'VERIFIED', restored
    assert documents(root/'restored/database.sqlite3') == documents(db)


def reports_path(root):
    return root / 'reports'


def test_actual_cli_missing_input_retains_failure_and_never_retries(research):
    root, registry, state, db, _ = research
    args = ['research','--db',db,'--state',state,'--input-id','market_'+'0'*64,
            '--request-key','TEST_FIXTURE_missing','--wait-seconds','60']
    before = documents(db)
    code, failed = command(registry,*args)
    assert code == 2, failed
    runtime = workspace.load_runtime(registry_root=registry, allow_test_fixture=True)
    paths = list((runtime.directory/'operations').glob('*.json'))
    assert len(paths) == 1
    original = paths[0].read_bytes()
    assert json.loads(original)['phase'] == 'FAILED_OR_UNKNOWN'
    code, refused = command(registry,*args)
    assert code == 2 and paths[0].read_bytes() == original, refused
    args[args.index('TEST_FIXTURE_missing')] = 'TEST_FIXTURE_missing_other_key'
    code, refused = command(registry,*args)
    assert code == 2 and 'NO_RETRY' in refused['reason'], refused
    assert paths[0].read_bytes() == original
    assert documents(db) == before


@pytest.mark.parametrize('legacy_state', ['QUEUED', 'FAILED'])
def test_actual_cli_existing_legacy_job_cannot_be_retried_by_new_key(research, legacy_state):
    from argparse import Namespace
    from stockfish_core.jobs import JobService
    from stockfish_core.yaogu.r2_closure_cli import service_context
    from tests.unit.test_durable_jobs import settled
    root, registry, state, db, input_id = research
    jobs = JobService(SQLiteStore(db), recover=False)
    try:
        payload = {'input_id': input_id}
        if legacy_state == 'FAILED':
            def fail(_context):
                raise ValueError('TEST_FIXTURE_OLD_FAILURE')
            created = jobs.submit('strategy_experiments', payload, fail, job_id='TEST_FIXTURE_legacy_job')
            old = settled(jobs, created['job_id'])
        else:
            old = jobs.create('strategy_experiments', payload, job_id='TEST_FIXTURE_legacy_job')
        events = jobs.events(old['job_id'])
    finally:
        # Preserve a legacy unknown queue rather than cancelling it in fixture teardown.
        jobs.shutdown(cancel_pending=False)
    before = documents(db)
    code, result = command(registry, 'research', '--db', db, '--state', state,
                           '--input-id', input_id, '--request-key', 'TEST_FIXTURE_new_entry_key')
    assert code == 2 and result['reason'] == 'RESEARCH_EXISTING_JOB_USE_HISTORY_NO_RETRY', result
    with service_context(Namespace(db=db, state=state, allow_test_fixture=True)) as service:
        assert service.jobs.list() == [old]
        assert service.jobs.events(old['job_id']) == events
    assert documents(db) == before
