"""Evidence packaging must preserve failures, original bytes and privacy boundaries."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

from scripts import r05_build_review_bundle as builder
from scripts.r05_evidence_runner import run_command


def write(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
    return path


def setup_case(tmp_path):
    dev, evidence, attachment = [tmp_path / name for name in ('dev', 'evidence', 'attachment')]
    for root in (dev, evidence, attachment):
        root.mkdir()
    write(dev, 'stockfish_core/module.py', b'# original\r\nVALUE = 1\r\n')
    write(dev, 'tests/fixtures/inputs.csv', 'code,close\n000001,10\n')
    write(dev, 'tests/all_tests.py', 'def test_placeholder(): pass\n')
    write(dev, 'config/source.lock', 'locked\n')
    write(dev, 'migrations/schema.sql', 'CREATE TABLE example(id);\n')
    write(dev, 'LICENSE', 'Fixture license\n')
    write(dev, '.env.example', 'TUSHARE_TOKEN=\n')
    write(dev, '.env', 'TUSHARE_TOKEN=not-in-bundle\n')
    write(dev, 'data/private.json', '{"private":true}')
    write(dev, 'qlib-zh/DATA/analysis_outputs/private.json', '{"private":"PRIVATE_Q_LIB_OUTPUT"}')
    write(dev, 'qlib-zh/data_runner.py', '# actual source\n')
    write(dev, 'tests/private.db', 'SQLite format 3\x00private')
    write(dev, 'node_modules/private/index.js', 'PRIVATE_JS')
    write(attachment, 'evidence/expanded/probes/test_closure_independent.py', '# original 29\r\n')
    write(attachment, 'evidence/expanded/previous_probes/probes/test_r05_independent.py', '# original 8\r\n')
    write(evidence, 'supply/canonical-input.json', '{"raw_rows":["RAW_MARKET_SENTINEL"]}')
    write(evidence, 'private/run.json', '{"private":"PRIVATE_RUN_SENTINEL"}')
    args = builder.parser().parse_args(['--dev-root', str(dev), '--project-root', str(tmp_path),
        '--evidence-root', str(evidence), '--attachment-root', str(attachment),
        '--output', str(tmp_path / 'delivery.zip')])
    return args, dev, evidence, attachment


def test_real_executions_failures_junit_and_original_source_bytes(tmp_path, monkeypatch):
    args, dev, evidence, attachment = setup_case(tmp_path)
    monkeypatch.setenv('R05_TEST_API_KEY', 'private-test-secret-for-redaction')
    success = run_command([sys.executable, '-c', 'print("real success")'], dev, evidence / 'pytest-pass')
    failure = run_command([sys.executable, '-c', 'import sys; print("real failed attempt"); sys.exit(3)'],
                          dev, evidence / 'pytest-fail')
    assert success['exit_code'] == 0 and failure['exit_code'] == 3
    log = evidence / 'pytest-fail/stdout.log'
    log.write_text(log.read_text() + os.environ['R05_TEST_API_KEY'], encoding='utf-8')
    write(evidence, 'pytest-fail/private.log', 'DO_NOT_ARCHIVE_UNREFERENCED_LOG')
    write(evidence, 'pytest-fail/checks.xml', '<testsuites tests="99"><testsuite tests="3">'
          '<testcase name="ok"/><testcase name="fail"><failure>real failure</failure></testcase>'
          '<testcase name="skip"><skipped/></testcase></testsuite></testsuites>')
    verified = builder.build_bundle(args)
    assert verified['zip_sha256'] == builder.sha256(Path(args.output))
    with ZipFile(args.output) as archive:
        index = json.loads(archive.read('evidence-index.json'))
        assert index['evidence']['execution_counts_by_state'] == {'EXIT_ZERO': 1, 'EXIT_NONZERO': 1}
        assert index['evidence']['aggregate_junit_case_counts'] == {
            'tests': 3, 'passed': 1, 'failures': 1, 'errors': 0, 'skipped': 1}
        failed = next(r for r in index['evidence']['source_runs'] if r['exit_code'] == 3)
        assert failed['log_integrity'] == 'MISMATCH'
        assert failed['SOURCE_RUN']['command'] == failure['command']
        assert failed['started_at'] and failed['ended_at']
        assert b'[REDACTED]' in archive.read('evidence/pytest-fail/stdout.log')
        assert archive.read('source/stock-fish/stockfish_core/module.py') == (dev / 'stockfish_core/module.py').read_bytes()
        assert 'source/stock-fish/tests/fixtures/inputs.csv' in archive.namelist()
        assert 'source/stock-fish/migrations/schema.sql' in archive.namelist()
        for rel in ('evidence/expanded/probes/test_closure_independent.py',
                    'evidence/expanded/previous_probes/probes/test_r05_independent.py'):
            assert archive.read('review-context/' + rel) == (attachment / rel).read_bytes()
        names = archive.namelist()
        assert not any('/node_modules/' in n or n.endswith('.db') or n.endswith('/.env') for n in names)
        all_bytes = b'\n'.join(archive.read(n) for n in names)
        assert os.environ['R05_TEST_API_KEY'].encode() not in all_bytes
        assert b'RAW_MARKET_SENTINEL' not in all_bytes
        assert b'PRIVATE_RUN_SENTINEL' not in all_bytes
        assert b'PRIVATE_Q_LIB_OUTPUT' not in all_bytes
        assert 'source/stock-fish/qlib-zh/data_runner.py' in names
        assert b'DO_NOT_ARCHIVE_UNREFERENCED_LOG' not in all_bytes
        refs = json.loads(archive.read('private-input-references.json'))
        raw = evidence / 'supply/canonical-input.json'
        assert refs['files'][0]['sha256'] == builder.sha256(raw)
        assert refs['files'][0]['path'] == str(raw.resolve())
        assert index['self_contained_for_real_market_replay'] is False
    assert builder.verify_zip(args.output)['verified_file_count'] == verified['verified_file_count']


def test_unsupported_success_declaration_never_becomes_observed_pass(tmp_path):
    args, _, evidence, _ = setup_case(tmp_path)
    write(evidence, 'old/result.json', '{"result":"PASS","count":999}')
    builder.build_bundle(args)
    with ZipFile(args.output) as archive:
        index = json.loads(archive.read('evidence-index.json'))
    assert index['evidence']['execution_counts_by_state'] == {'INCOMPLETE_EXECUTION_RECORD': 1}


def test_timeout_and_json_credentials_are_preserved_as_redacted_non_success(tmp_path):
    args, _, evidence, _ = setup_case(tmp_path)
    write(evidence, 'timeout/run.json', json.dumps({'command': ['python', 'task.py'],
        'started_at': '2026-09-06T00:00:00Z', 'ended_at': '2026-09-06T00:00:02Z',
        'exit_code': None, 'execution_state': 'TIMED_OUT'}))
    write(evidence, 'timeout/stdout.log', '{"token": "private-unknown-token-123456789"}')
    builder.build_bundle(args)
    with ZipFile(args.output) as archive:
        index = json.loads(archive.read('evidence-index.json'))
        assert index['evidence']['execution_counts_by_state'] == {'TIMED_OUT': 1}
        assert b'private-unknown-token-123456789' not in archive.read('evidence/timeout/stdout.log')
        assert b'[REDACTED]' in archive.read('evidence/timeout/stdout.log')


def test_posthoc_plan_source_provenance_and_failed_audit_are_retained(tmp_path):
    args, _, evidence, _ = setup_case(tmp_path)
    args.replay_root = str(evidence / 'replay')
    write(evidence, 'replay/outcome_execution_plan.json', '{"signal_plan_unchanged": true}')
    original = write(evidence, 'provenance/replay-runtime-original/strategy_tracking.py', b'# original\r\n')
    write(evidence, 'outcome-attempt-01-before-context-reuse/outcome_input_reference.json', '{"path":"private-input.json","sha256":"abc"}')
    write(evidence, 'independent-primary-audit/initial-schema-mismatch/summary.json', '{"status":"FAIL","failures":["schema mismatch"]}')
    write(evidence, 'independent-primary-audit/execution.json', json.dumps({
        'command': 'python independent.py', 'working_directory': str(tmp_path),
        'exit_code': 0, 'started_at': '2026-09-06T00:00:00Z', 'finished_at': '2026-09-06T00:00:01Z',
        'script_sha256': 'actual-script-reference', 'console_log': '../audit-console.log'}))
    write(evidence, 'audit-console.log', 'actual independent console')
    write(evidence, 'tracking-temporal-audit/result.json', '{"prefix_equivalent":true,"source_sha256":"original-hash"}')
    write(evidence, 'tracking-temporal-audit/probe.py', '# original probe\n')
    write(evidence, 'production-memory-probe/summary.json', '{"peak_memory_bytes":12345}')
    extra = tmp_path / 'explicit-audit'
    write(extra, 'report.md', 'Actual additional audit\n')
    args.evidence_file.append(str(extra))
    builder.build_bundle(args)
    with ZipFile(args.output) as archive:
        assert 'replay/outcome_execution_plan.json' in archive.namelist()
        assert archive.read('evidence/provenance/replay-runtime-original/strategy_tracking.py') == original.read_bytes()
        assert b'FAIL' in archive.read('evidence/independent-primary-audit/initial-schema-mismatch/summary.json')
        assert 'historical-replay-attempts/outcome-attempt-01-before-context-reuse/outcome_input_reference.json' in archive.namelist()
        assert archive.read('evidence/audit-console.log') == b'actual independent console'
        assert b'prefix_equivalent' in archive.read('audit-details/tracking-temporal-audit/result.json')
        assert b'peak_memory_bytes' in archive.read('audit-details/production-memory-probe/summary.json')
        assert 'additional-evidence/000-explicit-audit/report.md' in archive.namelist()
        index = json.loads(archive.read('evidence-index.json'))
        actual_run = next(r for r in index['evidence']['source_runs'] if r['record_path'].endswith('independent-primary-audit' + os.sep + 'execution.json'))
        assert actual_run['SOURCE_RUN']['script_sha256'] == 'actual-script-reference'


def test_replay_allowlist_rejects_raw_input_but_packages_derived_reports(tmp_path):
    args, _, evidence, _ = setup_case(tmp_path)
    replay = evidence / 'replay'
    args.replay_root = str(replay)
    write(replay, 'focus.csv', 'instrument_id,rank\n000001.SZ,1\n')
    write(replay, 'canonical-market-input.json', '{"raw_rows":[1]}')
    builder.build_bundle(args)
    with ZipFile(args.output) as archive:
        assert 'replay/focus.csv' in archive.namelist()
        assert 'replay/canonical-market-input.json' not in archive.namelist()
    args.output = str(tmp_path / 'rejected.zip')
    write(replay, 'rank.json', '{"raw_rows":[1]}')
    with pytest.raises(ValueError, match='raw market payload'):
        builder.build_bundle(args)
    assert not Path(args.output).exists()


def test_existing_delivery_is_not_overwritten_and_archive_tampering_detected(tmp_path):
    args, _, _, _ = setup_case(tmp_path)
    builder.build_bundle(args)
    with pytest.raises(FileExistsError):
        builder.build_bundle(args)
    with ZipFile(args.output) as original:
        entries = {n: original.read(n) for n in original.namelist()}
    entries['source/stock-fish/stockfish_core/module.py'] = b'altered'
    tampered = tmp_path / 'tampered.zip'
    with ZipFile(tampered, 'w') as archive:
        for name, raw in entries.items():
            archive.writestr(name, raw)
    with pytest.raises(ValueError, match='byte/hash mismatch'):
        builder.verify_zip(tampered)


def test_environment_credential_in_source_fails_before_zip_creation(tmp_path, monkeypatch):
    args, dev, _, _ = setup_case(tmp_path)
    monkeypatch.setenv('R05_TEST_TOKEN', 'secret-source-not-allowed-123456')
    write(dev, 'bad.py', "TOKEN = 'secret-source-not-allowed-123456'\n")
    with pytest.raises(ValueError, match='credential appears'):
        builder.build_bundle(args)
    assert not Path(args.output).exists()


def test_xml_entity_is_not_processed(tmp_path):
    path = write(tmp_path, 'evil.xml', '<!DOCTYPE testsuite [<!ENTITY leak SYSTEM "file:///private">]>'
                 '<testsuite><testcase>&leak;</testcase></testsuite>')
    with pytest.raises(ValueError, match='DOCTYPE'):
        builder.junit_counts(path)


def test_source_outside_root_symlink_is_never_followed(tmp_path):
    args, dev, _, _ = setup_case(tmp_path)
    outside = write(tmp_path, 'outside/private.py', 'PRIVATE_SYMLINK_SENTINEL')
    try:
        (dev / 'outside.py').symlink_to(outside)
    except OSError:
        pytest.skip('OS does not grant unprivileged symbolic link creation')
    builder.build_bundle(args)
    with ZipFile(args.output) as archive:
        assert 'source/stock-fish/outside.py' not in archive.namelist()
