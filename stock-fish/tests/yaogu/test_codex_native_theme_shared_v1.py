"""Isolated TEST_FIXTURE original captures; no network, database or real data."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import codex_daily_session_v1 as pipeline
from scripts import codex_native_theme_shared_v1 as shared
from stockfish_core.yaogu import premarket_native_core_v1 as core
from stockfish_core.yaogu import premarket_theme_context_v1 as original
from stockfish_core.yaogu import provider_requests_worker, providers
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, BudgetExceeded
from tests.support.saved_r2_v3_fixture import physical_source, write
from tests.yaogu.test_premarket_native_core_v1 import captures, FixtureBudget
from tests.yaogu.test_industry_sina_context_v1 import responses, directory_response

MIB = 1024 ** 2


@pytest.fixture(autouse=True)
def forbid_external_io(monkeypatch):
    import socket
    import sqlite3
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_NETWORK_DB_FORBIDDEN')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)


def budget(job):
    return Budget(dict(max_seconds=180, theme_read_bytes=256 * MIB,
                       output_bytes=64 * MIB, self_rss_bytes=1024 * MIB), job_dir=job)


def contexts(root, cutoff, kind):
    seen = cutoff - timedelta(minutes=1)
    rows = [{'code': '600001', 'symbol': 'sh600001', 'name': 'TEST_FIXTURE'},
            {'code': '000001', 'symbol': 'sz000001', 'name': 'TEST_FIXTURE'}]
    symbol = 'gn_TEST_FIXTURE' if kind == 'concept' else 'hangye_TEST_FIXTURE'
    directory_rows = [{'label': symbol, '板块': 'TEST_FIXTURE主题', '公司家数': 2}]
    if kind == 'concept':
        common = dict(provider='sina', version='TEST_FIXTURE_SINA_V1', include_result=True)
        items = [physical_source(root, 'concept_list_sina', directory_rows, seen,
                     parameters={}, **common),
                 physical_source(root, 'concept_members_sina', rows, seen,
                     parameters={'symbol': symbol}, **common)]
    else:
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return seen.astimezone(tz or timezone.utc)
        items = []
        for dataset, values, parameters, http in (
            ('industry_list_sina', directory_rows, {}, directory_response()),
            ('industry_members_sina', rows, {'symbol': symbol}, responses(rows, node=symbol))):
            proof = provider_requests_worker.sina_industry_capture_proof(dataset, parameters, values, http)
            payload = dict(status='COMPLETE', pagination_complete=True,
                field_version='TEST_FIXTURE_SINA_INDUSTRY_V1', first_seen_at=seen.isoformat(),
                rows=values, coverage={'sina_industry': proof}, gaps=[])
            with patch.object(providers, 'datetime', Clock):
                receipt = providers.archive_receipt(root/'cache/raw', 'TEST_FIXTURE', dataset,
                    parameters, payload, pagination_complete=True,
                    field_version=payload['field_version'], first_seen_at=seen.isoformat(),
                    source_url='https://TEST_FIXTURE.invalid/original')
            rid = receipt['receipt_id']
            result = dict(**payload, provider='TEST_FIXTURE', endpoint=dataset,
                parameters=parameters, request_receipt_id=rid, receipts=[receipt],
                started_at=seen.isoformat(), completed_at=seen.isoformat())
            items.append(dict(archive_root=str(root),
                result=write(root/'results'/(rid+'.json'), result),
                raw={'path': str(root/'cache/raw'/(receipt['content_hash']+'.raw.json')),
                     'sha256': receipt['content_hash']},
                receipt={'path': str(root/'cache/raw'/(rid+'.receipt.json'))}))
    for item in items:
        for role in ('result', 'raw', 'receipt'):
            item[role] = FixtureBudget().read(item[role]['path'])[1]
    return items, dict(requested_concept_symbols=(symbol,) if kind == 'concept' else (),
                       requested_industry_nodes=(symbol,) if kind == 'industry' else ())


def producer_profile(items):
    from stockfish_core.yaogu import provider_requests
    from stockfish_core.yaogu.premarket_capture_cache_v1 import PRODUCERS
    producers = [FixtureBudget().read(Path(provider_requests.__file__).with_name(name))[1]
                 for name in PRODUCERS]
    for item in items:
        path = Path(item['result']['path'])
        result = json.loads(path.read_bytes())
        result['implementation_source_hashes'] = {Path(ref['path']).name: ref['sha256'] for ref in producers}
        path.write_text(json.dumps(result), encoding='utf-8')
        item['producer_files'] = deepcopy(producers)
        for role in ('result', 'raw', 'receipt'):
            item[role] = FixtureBudget().read(item[role]['path'])[1]


def scenario(root, *, kind='concept', missing=False, sessions=67, production_profile=False):
    records, observation, target, cutoff = captures(root, sessions=sessions, history=True)
    if production_profile:
        calendar = json.loads(Path(records[0]['result']['path']).read_bytes())
        records[0] = physical_source(root/'TEST_FIXTURE_production_profile', 'calendar',
            calendar['rows'], datetime.fromisoformat(calendar['first_seen_at']), day=target,
            parameters=calendar['parameters'], version='BAOSTOCK_00.9.30_R03_V1', include_result=True)
        producer_profile(records)
    gap = str(observation - timedelta(days=40))
    if missing:
        records = [row for row in records if not (
            json.loads(Path(row['result']['path']).read_bytes())['endpoint'] == 'market_daily' and
            json.loads(Path(row['result']['path']).read_bytes())['parameters'].get('date') == gap)]
    owner = core.run_core(captures=records, observation_date=observation, target_session=target,
        cutoff=cutoff, output_dir=root/'core', budget=FixtureBudget(),
        coverage_type='RECONSTRUCTED_HISTORICAL' if production_profile else 'TEST_FIXTURE')
    sources, options = contexts(root/'contexts', cutoff, kind)
    if production_profile:
        producer_profile(sources)
    return owner, records, sources, cutoff, options, gap


def read_parent(result):
    return json.loads(gzip.decompress(Path(result.artifacts['native-theme-parent.json.gz']['path']).read_bytes()))


@pytest.mark.parametrize('kind', ['concept', 'industry'])
@pytest.mark.parametrize('missing', [False, True])
def test_native_full_and_gap_differential_preserves_numeric_and_selection(tmp_path, kind, missing):
    owner, records, sources, cutoff, options, gap = scenario(tmp_path, kind=kind, missing=missing)
    before = owner.run
    frozen = {row[role]['path']: (Path(row[role]['path']).read_bytes(),
              pipeline._fp(Path(row[role]['path']).stat()))
              for row in records + sources for role in ('result', 'raw', 'receipt')}
    globals_before = dict(original.attach_theme_context.__globals__)
    expected = original.attach_theme_context(owner, context_captures=sources,
        context_cutoff=cutoff, output_dir=tmp_path/'reference', budget=FixtureBudget(), **options)
    assert expected.to_dict()['status'] == 'AVAILABLE', expected.to_dict().get('failure')
    resources = budget(tmp_path)
    actual = shared.attach_theme_context_shared(owner, context_captures=sources,
        context_cutoff=cutoff, output_dir=tmp_path/'shared', budget=resources, **options)
    value, reference = actual.to_dict(), expected.to_dict()
    assert value['status'] == reference['status'] == 'AVAILABLE', value.get('failure')
    assert value['schema_version'] == shared.VERSION
    for field in ('source_context', 'scope', 'membership_mode', 'historical_PIT_claimed',
                  'event_confirmation', 'stock_selection_changed'):
        assert value[field] == reference[field], field
    first, second = read_parent(expected), read_parent(actual)
    for field in ('items', 'effective_input_hash', 'source_refs', 'computed_exchange_sessions',
                  'source_input_hash', 'membership_mode', 'status', 'market_edge',
                  'historical_membership_verified', 'official_pit_validation_countable'):
        assert first[field] == second[field], field
    assert first['context_content_hash'] != second['context_content_hash']
    assert second['implementation_version'] == shared.VERSION
    ignored = {'theme_context_hash', 'diagnostic_content_hash'}
    assert {k: v for k, v in value['diagnostic'].items() if k not in ignored} == {
        k: v for k, v in reference['diagnostic'].items() if k not in ignored}
    manifest = json.loads(Path(actual.artifacts['shared-session/manifest.json']['path']).read_bytes())
    assert len(manifest['requested_sessions']) == 67
    assert manifest['source_projection_count'] == (66 if missing else 67)
    assert (gap in manifest['missing_sessions']) is missing
    assert manifest['restorable_source_capability'] is False
    measured = json.loads(Path(actual.artifacts['shared-session/execution-metrics.json']['path']).read_bytes())
    assert measured['pipeline_metrics']['source_payload_decodes'] == (132 if missing else 134)
    assert value['formal_focus'] is False
    assert resources.metrics['returned_read_bytes']['theme'] > 0
    assert actual.validate() is True  # The pipeline scope has already expired.
    assert owner.run == before
    assert original.attach_theme_context.__globals__ == globals_before
    assert frozen == {path: (Path(path).read_bytes(), pipeline._fp(Path(path).stat())) for path in frozen}


@pytest.mark.parametrize('target', ['raw', 'fact', 'journal', 'manifest', 'parent', 'document'])
def test_after_return_same_sha_new_inode_is_rejected(tmp_path, target):
    owner, records, sources, cutoff, options, _ = scenario(tmp_path)
    result = shared.attach_theme_context_shared(owner, context_captures=sources,
        context_cutoff=cutoff, output_dir=tmp_path/'shared', budget=budget(tmp_path), **options)
    assert result.to_dict()['status'] == 'AVAILABLE', result.to_dict().get('failure')
    if target == 'raw':
        path = Path(sources[0]['raw']['path'])
    elif target == 'fact':
        path = next((tmp_path/'shared/shared-session/facts').glob('*.json'))
    elif target == 'journal':
        path = next(path for path in (tmp_path/'shared/price-journal').iterdir() if path.name != 'manifest.json')
    else:
        filename = {'manifest': 'shared-session/manifest.json', 'parent': 'native-theme-parent.json.gz',
                    'document': 'theme-context.json'}[target]
        path = Path(result.artifacts[filename]['path'])
    before = pipeline._fp(path.stat())
    content = path.read_bytes()
    replacement = path.with_suffix('.replacement')
    replacement.write_bytes(content)
    replacement.replace(path)
    assert path.read_bytes() == content and pipeline._fp(path.stat()) != before
    with pytest.raises((ValueError, BudgetExceeded), match='CHANGED|MISMATCH'):
        result.validate()


def test_short_calendar_stays_unknown_and_does_not_create_facts(tmp_path):
    owner, _, sources, cutoff, options, _ = scenario(tmp_path, sessions=60)
    result = shared.attach_theme_context_shared(owner, context_captures=sources,
        context_cutoff=cutoff, output_dir=tmp_path/'shared', budget=budget(tmp_path), **options)
    value = result.to_dict()
    assert value['status'] == 'UNKNOWN' and value['diagnostic'] is None
    assert value['reason_codes'] == ['VERIFIED_CALENDAR_SHORTER_THAN_67_SESSIONS']
    assert not (tmp_path/'shared/shared-session').exists()
    assert result.validate() is True


def test_plain_json_cannot_restore_owner_or_unmetered_budget(tmp_path):
    with pytest.raises(ValueError, match='GENUINE_LIVE_OWNER'):
        shared.attach_theme_context_shared({}, context_cutoff='2026-09-30T13:00:00+00:00',
            output_dir=tmp_path/'forged', budget=budget(tmp_path))
    assert not (tmp_path/'forged').exists()
    owner, _, _, cutoff, options, _ = scenario(tmp_path, sessions=60)
    with pytest.raises(ValueError, match='ORIGINAL_EXECUTION_BUDGET_REQUIRED'):
        shared.attach_theme_context_shared(owner, context_cutoff=cutoff,
            output_dir=tmp_path/'unmetered', budget=FixtureBudget(), **options)
    assert not (tmp_path/'unmetered').exists()


def test_whole_phase_read_budget_remains_sticky(tmp_path):
    owner, _, _, cutoff, options, _ = scenario(tmp_path, sessions=60)
    resources = Budget(dict(max_seconds=180, theme_read_bytes=1), job_dir=tmp_path)
    with pytest.raises(BudgetExceeded, match='READ_LIMIT_EXCEEDED:theme'):
        shared.attach_theme_context_shared(owner, context_cutoff=cutoff,
            output_dir=tmp_path/'exhausted', budget=resources, **options)
    assert resources.metrics['failures'] == ['READ_LIMIT_EXCEEDED:theme']
    assert not resources._meter_active
    assert not (tmp_path/'exhausted').exists()


def test_production_coverage_control_still_uses_real_issuer_and_terminal(tmp_path):
    """Synthetic files test the production gate, never claimed as real history."""
    owner, _, sources, cutoff, options, _ = scenario(tmp_path, production_profile=True)
    assert owner.run['coverage_type'] == 'RECONSTRUCTED_HISTORICAL'
    result = shared.attach_theme_context_shared(owner, context_captures=sources,
        context_cutoff=cutoff, output_dir=tmp_path/'shared', budget=budget(tmp_path), **options)
    assert result.to_dict()['status'] == 'AVAILABLE', result.to_dict().get('failure')
    parent = read_parent(result)
    assert parent['coverage_type'] == 'RECONSTRUCTED_HISTORICAL'
    assert parent['execution_provenance']['source_terminal_callback_status'] == 'LIVE_CALLBACK_COMPLETED'
    manifest = json.loads(Path(result.artifacts['shared-session/manifest.json']['path']).read_bytes())
    assert any('/contexts/' in row['path'] and row['path'].endswith('.raw.json')
               for row in manifest['source_references'])
    assert result.validate() is True


@pytest.mark.parametrize('mutation', ['same_sha_raw', 'nested_receipt', 'borrowed_row'])
def test_inflight_change_cannot_publish_parent(tmp_path, monkeypatch, mutation):
    owner, records, sources, cutoff, options, _ = scenario(tmp_path)
    original_panel = pipeline._ScopeStream.panel
    changed = False
    def mutate(scope, day):
        nonlocal changed
        value = original_panel(scope, day)
        if not changed:
            changed = True
            if mutation == 'same_sha_raw':
                path = Path(sources[0]['raw']['path'])
                replacement = path.with_suffix('.replacement')
                replacement.write_bytes(path.read_bytes())
                replacement.replace(path)
            elif mutation == 'nested_receipt':
                scope.reader.receipts[0].parameters['TEST_FIXTURE_ADVERSARIAL'] = True
            else:
                scope._test_borrowed_row = value.rows[0]
        elif mutation == 'borrowed_row':
            object.__setattr__(scope._test_borrowed_row, 'quality', 'INVALID')
        return value
    monkeypatch.setattr(pipeline._ScopeStream, 'panel', mutate)
    if mutation == 'borrowed_row':
        result = shared.attach_theme_context_shared(owner, context_captures=sources,
            context_cutoff=cutoff, output_dir=tmp_path/'shared', budget=budget(tmp_path), **options)
        assert result.to_dict()['status'] == 'FAILED'
        assert 'BORROWED_PANEL_CHANGED' in result.to_dict()['failure']['message']
    else:
        with pytest.raises((ValueError, BudgetExceeded), match='CHANGED|MISMATCH'):
            shared.attach_theme_context_shared(owner, context_captures=sources,
                context_cutoff=cutoff, output_dir=tmp_path/'shared', budget=budget(tmp_path), **options)
    assert changed
    assert not (tmp_path/'shared/native-theme-parent.json.gz').exists()


def test_new_output_cannot_escape_existing_whole_job_budget(tmp_path):
    owner, _, _, cutoff, options, _ = scenario(tmp_path, sessions=60)
    job = tmp_path/'job'
    job.mkdir()
    with pytest.raises(ValueError, match='OUTPUT_MUST_BELONG'):
        shared.attach_theme_context_shared(owner, context_cutoff=cutoff,
            output_dir=tmp_path/'outside', budget=budget(job), **options)
    assert not (tmp_path/'outside').exists()
