"""Native TEST_FIXTURE originals and read-only synthetic API report contracts.

No live HTTP, DB, SDK or selector calls. API coverage labels below exercise the
existing report protocol only; every issued market capability remains explicitly
TEST_FIXTURE, and TEST_FIXTURE saved runs retain the ordinary report rejection.
"""
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import socket
import sqlite3
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import codex_research as entry
from stockfish_core.yaogu import market_explanation_artifacts as artifacts
from stockfish_core.yaogu import market_explanation as explanation
from stockfish_core.yaogu import providers, strategy_experiments
from stockfish_core.yaogu.history_artifacts import digest_json_stream, digest_market_input
from tests.yaogu.test_market_descriptive_context import fixture as native_fixture


def _json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


@pytest.fixture(autouse=True)
def no_external_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE forbids network, DB, capture and selectors')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(providers, 'capture_akshare', forbidden)
    for name in ('run_strategy_experiments', 'run_active_daily_strategy', 'run_short_horizon_strategy'):
        monkeypatch.setattr(strategy_experiments, name, forbidden)


def _originals(tmp_path, *, api_coverage='TEST_FIXTURE'):
    original_dir = tmp_path / 'TEST_FIXTURE-originals'
    original_dir.mkdir()
    bundle, cutoff, stream, raw_paths = native_fixture(original_dir)
    assert bundle.coverage_type == 'TEST_FIXTURE'
    rows = [dict(instrument_id=security.instrument_id, name=security.name,
                 rank=index, A=1.2 + index / 10, R=0.01 * index,
                 H=0.03, S_PV=80 - index, decision_status='WATCH',
                 risk_flags=['TEST_FIXTURE'])
            for index, security in enumerate(bundle.universe.securities[:3], 1)]
    input_hash = digest_market_input(bundle)
    run = dict(run_id='TEST_FIXTURE_MARKET_EXPLANATION_READ_ONLY_RUN',
        input_id='market_' + input_hash, input_hash=input_hash,
        effective_input_hash=input_hash, universe_hash='TEST_FIXTURE_UNIVERSE',
        observation_date=str(bundle.observation_date), decision_cutoff=cutoff.isoformat(),
        generated_at=cutoff.isoformat(), coverage_type=api_coverage,
        implementation_version='TEST_FIXTURE_SAVED_API_PROTOCOL_NOT_SELECTOR_OUTPUT',
        spec_hash='TEST_FIXTURE_SPEC', source_hash='TEST_FIXTURE_SOURCE',
        data_watermark=str(bundle.observation_date),
        results={entry.PV: dict(items=rows, status='LIMITED_SCOPE',
            counts=dict(eligible=3, evaluated=3, missing=0))},
        selection_results={entry.FOCUS: dict(selected_ids=[])})
    # A declared saved-run hash is a protocol binding, not selector authority.
    run['content_hash'] = digest_json_stream(run)
    run['export_view'] = dict(strategy_id=entry.PV, selection='all',
        source_content_hash=run['content_hash'], filtered_total=len(rows), ranked_total=len(rows))
    files = {name: tmp_path / (name + '.json')
             for name in ('input', 'run', 'source_receipts', 'source_map')}
    _json(files['input'], {'input': bundle.model_dump(mode='json')})
    _json(files['run'], run)
    _json(files['source_receipts'], {'source_receipts': [r.model_dump(mode='json') for r in stream.receipts]})
    _json(files['source_map'], {'mapping': {rid: {'raw_path': str(path)} for rid, path in raw_paths.items()}})
    return dict(bundle=bundle, cutoff=cutoff, run=run, files=files,
                raw_paths=raw_paths, receipts=stream.receipts)


def _generate(original, output, **overrides):
    arguments = dict(input_path=original['files']['input'], run_path=original['files']['run'],
        source_receipts_path=original['files']['source_receipts'],
        source_map_path=original['files']['source_map'], output=output,
        cutoff=original['cutoff'])
    return artifacts.run_market_explanation(**(arguments | overrides))


def _snapshot(run):
    snapshot = dict(identity={key: run[key] for key in ('run_id', 'observation_date')},
        input_hash=run['input_hash'], source_run_hash=run['content_hash'],
        coverage_type='TEST_FIXTURE', items=deepcopy(run['results'][entry.PV]['items']),
        source_counts=deepcopy(run['results'][entry.PV]['counts']),
        prospective_validation=dict(status='NOT_ESTABLISHED', sample_count=None))
    snapshot['snapshot_hash'] = digest_json_stream(snapshot)
    return snapshot


class ReadOnlyFakeClient:
    """Saved API payloads only; every accepted path is explicitly read-only."""
    base_url = 'http://127.0.0.1:8000'

    def __init__(self, run, daily=None):
        self.run, self.daily = deepcopy(run), deepcopy(daily)
        self.calls = []
        self.json_bytes = json.dumps(self.run, ensure_ascii=False).encode()
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=['run_id', 'observation_date',
            'strategy_id', 'instrument_id', 'rank', 'A', 'R', 'H', 'S_PV'])
        writer.writeheader()
        writer.writerows(dict(run_id=run['run_id'], observation_date=run['observation_date'],
            strategy_id=entry.PV, **{key: row[key] for key in ('instrument_id', 'rank', 'A', 'R', 'H', 'S_PV')})
            for row in run['results'][entry.PV]['items'])
        self.csv_bytes = ('\ufeff' + stream.getvalue()).encode()

    def raw(self, path, payload=None):
        self.calls.append((path, payload))
        parsed, query = urlsplit(path), parse_qs(urlsplit(path).query)
        assert payload is None
        assert parsed.path == entry.prefix(self.run['run_id']) + '/export'
        assert query['selection'] == ['all'] and query['strategy_id'] == [entry.PV]
        assert query['format'] in (['json'], ['csv'])
        return self.json_bytes if query['format'] == ['json'] else self.csv_bytes

    def get(self, path, payload=None):
        self.calls.append((path, payload))
        assert payload is None
        parsed = urlsplit(path)
        if parsed.path == '/api/daily-research/report':
            assert self.daily is not None
            assert parse_qs(parsed.query)['run_id'] == [self.run['run_id']]
            return deepcopy(self.daily)
        if parsed.path == entry.prefix(self.run['run_id']) + '/summary':
            return dict(run_id=self.run['run_id'], input_id=self.run['input_id'],
                observation_date=self.run['observation_date'], decision_cutoff=self.run['decision_cutoff'],
                generated_at=self.run['generated_at'], coverage_type=self.run['coverage_type'],
                source_run_hash=self.run['content_hash'], filtered_total=3, page=1, has_next=False,
                items=deepcopy(self.run['results'][entry.PV]['items']), focus_ids=[],
                results={entry.PV: dict(status='LIMITED_SCOPE', item_count=3)})
        if parsed.path == entry.prefix(self.run['run_id']) + '/review':
            return dict(run_id=self.run['run_id'], status='TEST_FIXTURE_UNMATURED',
                        market_edge='NOT_ESTABLISHED', counts=dict(mature=0, not_matured=3))
        raise AssertionError('Unregistered read path: ' + path)


def test_market_explanation_cli_issues_from_originals_without_api_or_parent_changes(tmp_path, monkeypatch, capsys):
    original = _originals(tmp_path)
    parents_before = {path: path.read_bytes() for path in (*original['files'].values(), *original['raw_paths'].values())}
    def no_api(*args, **kwargs):
        raise AssertionError('Offline explanation must not create an API client')
    monkeypatch.setattr(entry, 'Client', no_api)
    output = tmp_path / 'attachment'
    arguments = ['market-explanation', '--input', str(original['files']['input']),
        '--run', str(original['files']['run']), '--source-receipts', str(original['files']['source_receipts']),
        '--source-map', str(original['files']['source_map']), '--output', str(output),
        '--cutoff', original['cutoff'].isoformat()]
    assert entry.main(arguments) == 0
    console = json.loads(capsys.readouterr().out)
    assert console['status'] == 'SAVED_NATIVE_MARKET_EXPLANATION'
    request = json.loads((output / 'request.json').read_text())
    manifest = json.loads((output / 'manifest.json').read_text())
    attachment = json.loads((output / 'market-explanation.json').read_text())
    assert request['saved_context_capability_restored'] is False
    assert request['network_calls'] == 0 and manifest['source_database_written'] is False
    assert attachment['coverage_type'] == 'TEST_FIXTURE'
    assert attachment['native_context_hash'] == manifest['native_context_hash']
    assert attachment['frozen_run_binding']['input_hash'] == digest_market_input(original['bundle'])
    assert attachment['frozen_run_binding']['run_content_hash'] == digest_json_stream(original['run'])
    assert attachment['stock_selection_changed'] is False and attachment['score_changed'] is False
    assert attachment['selector_reauthenticated'] is False
    assert attachment['consumed_source_four_clocks']
    by_id = {receipt.receipt_id: receipt for receipt in original['receipts']}
    for rid, clocks in attachment['consumed_source_four_clocks'].items():
        for field, instant in clocks.items():
            assert datetime.fromisoformat(instant) == getattr(by_id[rid], field)
    assert set(next(iter(attachment['consumed_source_four_clocks'].values()))) == {
        'event_time', 'source_published_at', 'first_seen_at', 'ingested_at'}
    assert {path: path.read_bytes() for path in parents_before} == parents_before
    assert all(hashlib.sha256(Path(meta['path']).read_bytes()).hexdigest() == meta['sha256']
               for meta in manifest['native_context_files'].values())


@pytest.mark.parametrize('mismatch', ['input_hash', 'observation_date', 'input_file'])
def test_input_run_mismatch_rejected_before_output(tmp_path, mismatch):
    original = _originals(tmp_path)
    if mismatch == 'input_file':
        payload = json.loads(original['files']['input'].read_text())
        payload['input']['universe']['securities'][0]['name'] += ' changed'
        _json(original['files']['input'], payload)
    else:
        run = deepcopy(original['run'])
        run[mismatch] = 'f' * 64 if mismatch == 'input_hash' else str(original['bundle'].sessions[-2])
        _json(original['files']['run'], run)
    output = tmp_path / 'rejected'
    with pytest.raises(ValueError, match='SAVED_RUN_INPUT_MISMATCH'):
        _generate(original, output)
    assert not output.exists()


def test_future_cutoff_and_existing_output_stop_before_native_issue(tmp_path, monkeypatch):
    original = _originals(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError('Preflight failure must not issue native evidence')
    monkeypatch.setattr(artifacts, 'run_market_descriptive_context', forbidden)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    with pytest.raises(ValueError, match='FUTURE_CUTOFF'):
        _generate(original, tmp_path / 'future', cutoff=future)
    assert not (tmp_path / 'future').exists()
    prior = tmp_path / 'prior'; prior.mkdir()
    (prior / 'keep.txt').write_text('TEST_FIXTURE previous evidence')
    with pytest.raises(ValueError, match='OUTPUT_EXISTS_PRESERVED'):
        _generate(original, prior)
    assert (prior / 'keep.txt').read_text() == 'TEST_FIXTURE previous evidence'
    assert list(prior.iterdir()) == [prior / 'keep.txt']


@pytest.mark.parametrize('parent,value', [
    ('input', []), ('input', {'input': []}), ('run', []),
    ('source_map', []), ('source_map', {'mapping': []}),
    ('source_receipts', None), ('source_receipts', {'source_receipts': None}),
])
def test_malformed_original_documents_fail_as_value_error_before_output(tmp_path, parent, value):
    original = _originals(tmp_path)
    _json(original['files'][parent], value)
    output = tmp_path / 'malformed'
    with pytest.raises(ValueError):
        _generate(original, output)
    assert not output.exists()


@pytest.mark.parametrize('document,snapshot_kind', [
    ([], 'valid'), ({'frozen_run_binding': []}, 'valid'),
    ({'frozen_run_binding': None}, 'valid'), ({'frozen_run_binding': {}}, 'list'),
    ({'frozen_run_binding': {}}, 'bad_identity'),
])
def test_malformed_saved_display_document_and_snapshot_are_controlled(tmp_path, document, snapshot_kind):
    original = _originals(tmp_path)
    path = tmp_path / 'malformed-attachment.json'; _json(path, document)
    snapshot = _snapshot(original['run'])
    if snapshot_kind == 'list':
        snapshot = ['TEST_FIXTURE_INVALID_SNAPSHOT_SHAPE']
    elif snapshot_kind == 'bad_identity':
        snapshot['identity'] = []
    with pytest.raises(ValueError):
        artifacts.archived_explanation(path, snapshot=snapshot, as_of=datetime.now(timezone.utc))


def test_saved_attachment_cannot_bypass_original_raw_reauthentication(tmp_path):
    original = _originals(tmp_path)
    _generate(original, tmp_path / 'first')
    raw_path = next(path for rid, path in original['raw_paths'].items()
                    if rid.endswith(str(original['bundle'].observation_date)) and 'market_daily' in rid)
    raw_path.write_bytes(raw_path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='ORIGINAL_SOURCE_SHA_MISMATCH'):
        _generate(original, tmp_path / 'second')
    assert not (tmp_path / 'second' / 'manifest.json').exists()
    assert (tmp_path / 'first' / 'manifest.json').exists()


def test_source_capture_clock_must_match_original_bytes(tmp_path):
    original = _originals(tmp_path)
    payload = json.loads(original['files']['source_receipts'].read_text())
    for receipt in payload['source_receipts']:
        # Preserve valid typed chronology; only the authenticated RAW capture
        # clock disagrees with this shifted receipt, exercising the native gate.
        for field in ('first_seen_at', 'ingested_at'):
            receipt[field] = (datetime.fromisoformat(receipt[field]) + timedelta(seconds=1)).isoformat()
    _json(original['files']['source_receipts'], payload)
    with pytest.raises(ValueError, match='CAPTURE_CLOCK_MISMATCH'):
        _generate(original, tmp_path / 'bad-clock')
    assert not (tmp_path / 'bad-clock' / 'manifest.json').exists()


@pytest.mark.parametrize('parent', ['input', 'run', 'source_receipts', 'source_map', 'consumed_raw'])
def test_terminal_parent_mutation_never_claims_success(tmp_path, monkeypatch, parent):
    original = _originals(tmp_path)
    target = (next(path for rid, path in original['raw_paths'].items()
                   if rid.endswith(str(original['bundle'].observation_date)) and 'market_daily' in rid)
              if parent == 'consumed_raw' else original['files'][parent])
    render = explanation.render_market_explanation
    def mutate_after_render(attachment, *, as_of):
        rendered = render(attachment, as_of=as_of)
        target.write_bytes(target.read_bytes() + b' ')
        return rendered
    monkeypatch.setattr(explanation, 'render_market_explanation', mutate_after_render)
    output = tmp_path / 'late-mutation'
    with pytest.raises(ValueError, match='PARENT_CHANGED|ORIGINAL_SOURCE_SHA_MISMATCH|FILE_CHANGED|SOURCE_.*CHANGED'):
        _generate(original, output)
    assert not (output / 'manifest.json').exists()
    failure = json.loads((output / 'failure.json').read_text())
    assert failure['successful_attachment_claimed'] is False
    assert failure['source_database_written'] is False


@pytest.fixture
def generated(tmp_path):
    original = _originals(tmp_path)
    output = tmp_path / 'generated'
    _generate(original, output)
    return original, output / 'market-explanation.json'


@pytest.mark.parametrize('field', ['run_id', 'input_hash', 'observation_date', 'source_run_hash'])
def test_saved_attachment_rejects_report_identity_and_declared_hash_mismatch(generated, field):
    original, path = generated
    snapshot = _snapshot(original['run'])
    if field in ('run_id', 'observation_date'):
        snapshot['identity'][field] = 'WRONG_TEST_FIXTURE_BINDING'
    else:
        snapshot[field] = 'f' * 64
    with pytest.raises(ValueError, match='REPORT_RUN_MISMATCH|REPORT_SOURCE_RUN_HASH_MISMATCH'):
        artifacts.archived_explanation(path, snapshot=snapshot, as_of=datetime.now(timezone.utc))


@pytest.mark.parametrize('field', ['run_id', 'input_hash', 'observation_date'])
def test_missing_snapshot_identity_is_rejected(generated, field):
    original, path = generated
    snapshot = _snapshot(original['run'])
    (snapshot['identity'] if field != 'input_hash' else snapshot).pop(field)
    with pytest.raises(ValueError, match='REPORT_RUN_MISMATCH'):
        artifacts.archived_explanation(path, snapshot=snapshot, as_of=datetime.now(timezone.utc))


def test_missing_source_run_hash_is_unknown_display_not_verified(generated):
    original, path = generated
    snapshot = _snapshot(original['run']); snapshot.pop('source_run_hash')
    result = artifacts.archived_explanation(path, snapshot=snapshot, as_of=datetime.now(timezone.utc))
    assert result['source_run_hash_verification'] == 'UNKNOWN'
    assert result['source_run_hash_reason_codes'] == ['SOURCE_RUN_HASH_NOT_CAPTURED']
    assert result['artifact_role'] == 'ARCHIVED_EXPLANATION_DISPLAY_NOT_LIVE_CAPABILITY'
    assert result['selection_changed'] is False


def test_historical_as_of_hides_attachment_created_after_original_cutoff(generated):
    original, path = generated
    table = '| 指标 | 当前水平 | 1日变化 | 3日变化 | 5日变化 |'
    metric_rows = ('| 成交额 |', '| 上涨广度 |', '| Top10成交集中度 |',
                   '| 当日收益中位数 |', '| 流通股换手中位数 |', '| 收益分布IQR |')
    result = artifacts.archived_explanation(path, snapshot=_snapshot(original['run']), as_of=original['cutoff'])
    assert 'NOT_YET_VISIBLE' in result['markdown']
    assert '水平：' not in result['markdown'] and '变化/水平OLS斜率：' not in result['markdown']
    assert table not in result['markdown'] and all(row not in result['markdown'] for row in metric_rows)
    assert all(token not in result['markdown'] for token in ('亿元', 'pp', '%', '交易家数：', '前20日（', '描述反证：'))
    current = artifacts.archived_explanation(path, snapshot=_snapshot(original['run']), as_of=datetime.now(timezone.utc))
    assert 'NOT_YET_VISIBLE' not in current['markdown'] and table in current['markdown']
    assert all(row in current['markdown'] for row in metric_rows)


def test_archived_attachment_file_mutation_during_render_is_rejected(generated, monkeypatch):
    original, path = generated
    render = explanation.render_market_explanation
    def mutate(attachment, *, as_of):
        rendered = render(attachment, as_of=as_of)
        path.write_bytes(path.read_bytes() + b' ')
        return rendered
    monkeypatch.setattr(explanation, 'render_market_explanation', mutate)
    with pytest.raises(ValueError, match='SAVED_PARENT_CHANGED'):
        artifacts.archived_explanation(path, snapshot=_snapshot(original['run']), as_of=datetime.now(timezone.utc))


@pytest.mark.parametrize('historical', [False, True])
def test_daily_report_cli_appends_without_mutating_snapshot_scores_or_prior_attachments(generated, tmp_path, monkeypatch, capsys, historical):
    original, attachment_path = generated
    instant = original['cutoff'] if historical else datetime.now(timezone.utc)
    daily = dict(schema_version='TEST_FIXTURE_DAILY_REPORT', report_as_of=instant.isoformat(),
        snapshot=_snapshot(original['run']), delivery=dict(status='TEST_FIXTURE_READ_ONLY'),
        markdown='TEST_FIXTURE original complete daily report',
        validation_summary=dict(mature_count=None, mean_return=None),
        research_attachments=[dict(artifact_role='TEST_FIXTURE_EXISTING_ATTACHMENT', markdown='keep existing')])
    before = deepcopy(daily)
    client = ReadOnlyFakeClient(original['run'], daily)
    monkeypatch.setattr(entry, 'Client', lambda *args, **kwargs: client)
    output = tmp_path / 'daily-report'
    assert entry.main(['daily-report', '--run-id', original['run']['run_id'], '--as-of', instant.isoformat(),
        '--market-explanation', str(attachment_path), '--output', str(output)]) == 0
    assert json.loads(capsys.readouterr().out)['identity'] == before['snapshot']['identity']
    saved = json.loads((output / 'report.json').read_text())
    for key in before.keys() - {'markdown', 'research_attachments'}:
        assert saved[key] == before[key]
    assert saved['research_attachments'][0] == before['research_attachments'][0]
    assert len(saved['research_attachments']) == 2
    assert saved['markdown'].startswith(before['markdown'])
    assert ('NOT_YET_VISIBLE' in saved['research_attachments'][-1]['markdown']) is historical
    assert client.daily == before and client.run == original['run']
    assert len(client.calls) == 1 and client.calls[0][1] is None


def test_report_keeps_test_fixture_saved_run_rejection(generated, tmp_path):
    original, path = generated
    client = ReadOnlyFakeClient(original['run'])
    with pytest.raises(entry.ResearchError, match='NON_REAL_OR_UNVERIFIABLE'):
        entry.execute(entry.parser().parse_args(['report', original['run']['run_id'],
            '--market-explanation', str(path), '--output', str(tmp_path / 'forbidden-report')]), client)
    assert not (tmp_path / 'forbidden-report').exists()


def test_report_cli_appends_native_fixture_attachment_without_changing_official_exports(tmp_path, monkeypatch, capsys):
    # Synthetic HTTP protocol example, as in test_codex_research.saved_http.
    # This API label never changes the native input/capability TEST_FIXTURE role.
    original = _originals(tmp_path, api_coverage='RECONSTRUCTED_HISTORICAL')
    generated_dir = tmp_path / 'generated'
    _generate(original, generated_dir)
    attachment_path = generated_dir / 'market-explanation.json'
    client = ReadOnlyFakeClient(original['run'])
    before = deepcopy(client.run)
    json_before, csv_before = client.json_bytes, client.csv_bytes
    review = client.get(entry.prefix(before['run_id']) + '/review'); client.calls.clear()
    initial_markdown = entry.report_text(before, review)
    monkeypatch.setattr(entry, 'Client', lambda *args, **kwargs: client)
    output = tmp_path / 'report'
    assert entry.main(['report', before['run_id'], '--market-explanation', str(attachment_path),
        '--attachment-as-of', datetime.now(timezone.utc).isoformat(), '--output', str(output)]) == 0
    capsys.readouterr()
    assert (output / 'source.json').read_bytes() == json_before
    assert (output / 'candidates.csv').read_bytes() == csv_before
    assert json.loads((output / 'source.json').read_text()) == before
    assert (output / 'report.md').read_text().startswith(initial_markdown)
    assert '独立市场趋势解释附件' in (output / 'report.md').read_text()
    manifest = json.loads((output / 'manifest.json').read_text())
    assert manifest['input_hash'] == before['input_hash']
    assert manifest['source_content_hash'] == before['content_hash']
    assert manifest['files']['source.json']['sha256'] == hashlib.sha256(json_before).hexdigest()
    assert manifest['files']['candidates.csv']['sha256'] == hashlib.sha256(csv_before).hexdigest()
    display = json.loads((output / 'market-explanation-display.json').read_text())
    assert display['artifact_role'] == 'ARCHIVED_EXPLANATION_DISPLAY_NOT_LIVE_CAPABILITY'
    assert client.run == before and client.csv_bytes == csv_before and client.json_bytes == json_before
    assert len(client.calls) == 4 and all(payload is None for _, payload in client.calls)

