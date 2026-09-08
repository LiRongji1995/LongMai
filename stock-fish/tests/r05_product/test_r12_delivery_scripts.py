"""Isolated delivery checks: actual scheduled IDs and final-commit source bytes."""
from datetime import datetime
import hashlib
import json
import sqlite3
from urllib.error import HTTPError
import zipfile
from zoneinfo import ZoneInfo

import pytest

from scripts import build_r12_review_bundle as review_bundle
from scripts import r12_post_cutoff_check as post_cutoff

SH = ZoneInfo('Asia/Shanghai')
NOW = datetime(2026, 9, 8, 20, 40, tzinfo=SH)


def _probe_fixture(tmp_path):
    payload = json.dumps({'TEST_FIXTURE': 'x' * 150000})
    checksum = hashlib.sha256(payload.encode()).hexdigest()
    input_id = 'market_' + checksum
    database = tmp_path / 'TEST_FIXTURE.sqlite3'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE documents(namespace TEXT, document_id TEXT, content_hash TEXT, payload TEXT)')
        connection.execute('INSERT INTO documents VALUES(?,?,?,?)', ('candidate_inputs', input_id, checksum, payload))
    epoch = 'validation_epoch_TEST_FIXTURE'
    job_id = 'schedule_2026-09-08_EVENING_' + epoch
    primary = {'run_id': 'TEST_FIXTURE_RUN', 'input_id': input_id, 'signal_observation_date': '2026-09-08',
               'validation_epoch_id': epoch, 'official_primary_capture': True, 'official_validation_signal_countable': False}
    event = {'event_id': 'different-event-identity', 'job_id': job_id, 'validation_epoch_id': epoch,
             'market_session': 'EVENING', 'scheduled_at': '2026-09-08T20:30:00+08:00',
             'observed_at': '2026-09-08T20:30:00+08:00'}
    state = {'validation_epoch_id': epoch, 'validation_state': {'validation_epoch_id': epoch},
             'service_identity': {'db_path': str(database)}, 'scheduler_thread_alive': True,
             'primary_schedule': {'job_id': job_id, 'signal_session': '2026-09-08', 'validation_epoch_id': epoch},
             'latest_primary_capture': primary, 'scheduler_event_state': event}
    values = {
        '/api/candidate-schedule': {'events': [{**event, 'job_id': 'WRONG_OLD_EPOCH', 'validation_epoch_id': 'OLD',
                                               'observed_at': '2026-09-08T20:39:00+08:00'}], 'prospective_validation': state},
        '/api/health': {'status': 'healthy'},
        '/api/recommendation-scores/latest': {'latest': {'headline_status': 'N_A_SIGNAL_DATA_UNAVAILABLE'}},
        '/api/jobs/' + job_id: {'job_id': job_id, 'state': 'SUCCEEDED', 'result': {'stage_aware_primary_run_id': primary['run_id']}},
        '/api/strategy-experiments/TEST_FIXTURE_RUN/summary?strategy_id=STAGE_AWARE_MOMENTUM_V2&page_size=1': {
            'run_id': primary['run_id'], 'input_id': input_id, 'validation_epoch_id': epoch,
            'signal_data_quality': {'data_quality_status': 'SIGNAL_DATA_UNAVAILABLE'}},
    }
    return values, database, job_id


def test_post_cutoff_uses_real_epoch_job_identity_and_stream_verifies_input(tmp_path):
    values, database, job_id = _probe_fixture(tmp_path)
    requested = []

    def fetch(base, path):
        requested.append(path)
        return values[path]

    report = post_cutoff.collect_report('http://127.0.0.1:8001', NOW, fetch=fetch)
    assert '/api/jobs/' + job_id in requested
    assert report['evening_event']['event_id'] == 'different-event-identity'
    assert report['market_input_verification']['status'] == 'VERIFIED'
    assert report['market_input_verification']['payload_bytes'] > 65536
    assert all(report['primary_identity_checks'].values())
    assert report['today_actual_primary']['official_validation_signal_countable'] is False
    assert report['latest_score']['latest']['headline_status'] == 'N_A_SIGNAL_DATA_UNAVAILABLE'
    assert report['check_status'] == 'POST_CUTOFF_ACTUAL_EVIDENCE'
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE documents SET payload='corrupted' WHERE namespace='candidate_inputs'")
    assert post_cutoff.collect_report('http://127.0.0.1:8001', NOW, fetch=fetch)['check_status'] == 'POST_CUTOFF_IDENTITY_MISMATCH'


def test_post_cutoff_retains_failed_job_and_does_not_call_yesterdays_primary_today(tmp_path):
    values, _, job_id = _probe_fixture(tmp_path)
    values['/api/candidate-schedule']['prospective_validation']['latest_primary_capture']['signal_observation_date'] = '2026-09-07'
    values['/api/jobs/' + job_id] = {'state': 'FAILED', 'error': {'code': 'TEST_FIXTURE_CAPTURE_FAILED'}}
    report = post_cutoff.collect_report('http://127.0.0.1:8001', NOW, fetch=lambda base, path: values[path])
    assert report['check_status'] == 'POST_CUTOFF_CAPTURE_FAILED'
    assert report['today_actual_primary'] is None
    assert report['latest_actual_primary']['signal_observation_date'] == '2026-09-07'


def test_post_cutoff_api_failure_is_saved_as_diagnostic_not_false_completion(tmp_path):
    values, _, _ = _probe_fixture(tmp_path)

    def fetch(base, path):
        if path == '/api/health':
            raise HTTPError(base + path, 503, 'TEST_FIXTURE_UNAVAILABLE', None, None)
        return values[path]

    report = post_cutoff.collect_report('http://127.0.0.1:8001', NOW, fetch=fetch)
    assert report['check_status'] == 'POST_CUTOFF_PROBE_INCOMPLETE'
    assert report['probe_errors'] == [{'path': '/api/health', 'error_type': 'HTTPError', 'http_status': 503}]
    early = post_cutoff.collect_report('http://127.0.0.1:8001', NOW.replace(hour=19), fetch=fetch)
    assert early['check_status'] == 'BEFORE_CUTOFF_NO_PRIMARY_CLAIM'


def _git_fixture(*, dirty=False, remote_head=None):
    commit = 'a' * 40
    source = {'stock-fish/stockfish_core/example.py': b'FINAL_COMMIT_BYTES\n', 'LICENSE': b'TEST_FIXTURE_LICENSE',
              'stock-fish/.env': b'MUST_NOT_BE_INCLUDED'}
    calls = []

    def git(*args):
        calls.append(args)
        if args == ('rev-parse', 'HEAD') or args == ('rev-parse', 'origin/main'):
            return commit.encode()
        if args == ('ls-remote', 'origin', 'refs/heads/main'):
            return ((remote_head or commit) + '\trefs/heads/main\n').encode()
        if args == ('status', '--porcelain'):
            return b' M tracked.py\n' if dirty else b''
        if args == ('status', '--short', '--branch'):
            return b'## main...origin/main\n'
        if args == ('rev-parse', commit + '^'):
            return b'b' * 40
        if args == ('rev-parse', commit + '^{tree}'):
            return b'c' * 40
        if args == ('remote', 'get-url', 'origin'):
            return b'https://example.invalid/TEST_FIXTURE.git'
        if args == ('ls-tree', '-r', '--name-only', '-z', commit):
            return b'\0'.join(path.encode() for path in source) + b'\0'
        if args[0] == 'show':
            assert args[1].startswith(commit + ':')
            return source[args[1].split(':', 1)[1]]
        if args[0] == 'rev-parse' and args[1].startswith(commit + ':'):
            return b'd' * 40
        raise AssertionError(args)

    return git, calls


def test_review_bundle_uses_final_commit_bytes_and_separate_runtime_hashes(tmp_path, monkeypatch):
    git, calls = _git_fixture()
    monkeypatch.setattr(review_bundle, 'git', git)
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    (evidence / 'summary.json').write_text('{"TEST_FIXTURE":true}', encoding='utf-8')
    (evidence / 'market-input.json').write_text('PRIVATE_FULL_MARKET', encoding='utf-8')
    output = tmp_path / 'review.zip'
    review_bundle.main(['--output', str(output), '--evidence-dir', str(evidence)])
    with zipfile.ZipFile(output) as archive:
        assert archive.read('source/stock-fish/stockfish_core/example.py') == b'FINAL_COMMIT_BYTES\n'
        assert 'source/LICENSE' in archive.namelist()
        assert 'source/stock-fish/.env' not in archive.namelist()
        assert 'runtime-evidence/market-input.json' not in archive.namelist()
        sources = json.loads(archive.read('source-manifest.json'))
        runtime = json.loads(archive.read('runtime-evidence-manifest.json'))
        assert sources['stock-fish/stockfish_core/example.py']['sha256'] == hashlib.sha256(b'FINAL_COMMIT_BYTES\n').hexdigest()
        assert runtime['summary.json']['sha256'] == hashlib.sha256(archive.read('runtime-evidence/summary.json')).hexdigest()
        assert json.loads(archive.read('provenance.json'))['final_commit'] == 'a' * 40
    assert ('show', 'a' * 40 + ':stock-fish/stockfish_core/example.py') in calls
    assert json.loads(output.with_suffix('.zip.sha256.json').read_text())['sha256'] == hashlib.sha256(output.read_bytes()).hexdigest()


@pytest.mark.parametrize('dirty,remote_head,reason', [(True, None, 'WORKTREE_NOT_CLEAN'), (False, 'e' * 40, 'FINAL_COMMIT_NOT_EQUAL')])
def test_review_bundle_rejects_dirty_or_unpushed_commit(tmp_path, monkeypatch, dirty, remote_head, reason):
    git, _ = _git_fixture(dirty=dirty, remote_head=remote_head)
    monkeypatch.setattr(review_bundle, 'git', git)
    output = tmp_path / 'must-not-exist.zip'
    with pytest.raises(SystemExit, match=reason):
        review_bundle.main(['--output', str(output)])
    assert not output.exists()
