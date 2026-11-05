from pathlib import Path
"""Isolated TEST_FIXTURE originals; no endpoint, DB or selector invocation."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest

from stockfish_core.yaogu.contracts import SourceReceipt, ThemeSnapshot
from stockfish_core.yaogu.industry_sina_context_v1 import (
    IndustrySinaSourceArtifactReader, build_industry_context, source_identity,
    DIRECTORY, MEMBERS, DIRECTORY_URL, MEMBERS_URL, FIXTURE_FIELD_VERSION,
)
from stockfish_core.yaogu.provider_requests import ENDPOINTS, validate_request
from stockfish_core.yaogu.provider_requests_worker import sina_industry_capture_proof
from stockfish_core.yaogu.research_context_artifacts import SourceArtifactReader

CLOCK = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
NODE = 'hangye_TEST_A'
DATA_URL = MEMBERS_URL.replace('getHQNodeStockCount', 'getHQNodeData')


def members(count=2):
    return [dict(code=f'{600001+i:06d}', symbol=f'sh{600001+i:06d}', trade='999999',
                 amount='999999999999', ticktime='2099-01-01') for i in range(count)]


def responses(rows, *, node=NODE):
    result = [dict(url=MEMBERS_URL, parameters={'node': node}, body=str(len(rows)), status_code=200)]
    for index in range(0, len(rows), 80):
        result.append(dict(url=DATA_URL, parameters={'node': node, 'num': '80', 'page': index//80+1},
                           body=deepcopy(rows[index:index+80]), status_code=200))
    return result


def directory_response():
    return [dict(url=DIRECTORY_URL, parameters={'param': 'industry'},
                 body={'TEST_FIXTURE': 'directory original'}, status_code=200)]


def context_sources(tmp_path, *, alter=None, unrequested=False):
    """Every altered fixture has newly hashed bytes, never a real-source label."""
    rows = members()
    directory = [dict(label=NODE, 板块='TEST_FIXTURE 行业', 公司家数=2, 成交额='999999', 涨跌幅='99')]
    sources = [dict(dataset=DIRECTORY, rows=directory, parameters={},
                    proof=sina_industry_capture_proof(DIRECTORY, {}, directory, directory_response())),
               dict(dataset=MEMBERS, rows=rows, parameters={'symbol': NODE},
                    proof=sina_industry_capture_proof(MEMBERS, {'symbol': NODE}, rows, responses(rows)))]
    if unrequested:
        other = members(1)
        sources.append(dict(dataset=MEMBERS, rows=other, parameters={'symbol': 'hangye_TEST_B'},
            proof=sina_industry_capture_proof(MEMBERS, {'symbol': 'hangye_TEST_B'}, other,
                                              responses(other, node='hangye_TEST_B'))))
    receipts, paths = [], {}
    for index, source in enumerate(sources):
        payload = dict(status='COMPLETE', pagination_complete=True, field_version=FIXTURE_FIELD_VERSION,
                       first_seen_at=CLOCK.isoformat(), rows=source['rows'],
                       coverage={'sina_industry': source['proof']})
        receipt_values = dict(parameters=source['parameters'], status='COMPLETE', pagination_complete=True,
                              event_time=CLOCK, source_published_at=CLOCK, first_seen_at=CLOCK,
                              ingested_at=CLOCK+timedelta(milliseconds=2))
        if alter is not None:
            alter(index, payload, receipt_values)
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        rid = 'TEST_FIXTURE_INDUSTRY_' + str(index)
        path = tmp_path / (rid + '.json')
        path.write_bytes(raw)
        paths[rid] = path
        receipts.append(SourceReceipt(receipt_id=rid, provider='TEST_FIXTURE', dataset=source['dataset'],
            content_hash=hashlib.sha256(raw).hexdigest(), field_version=payload['field_version'],
            row_count=len(payload['rows']), source_url='https://example.invalid/TEST_FIXTURE', **receipt_values))
    return IndustrySinaSourceArtifactReader(receipts, paths), CLOCK+timedelta(seconds=1), paths


def test_explicit_new_endpoints_and_old_concept_contracts():
    assert validate_request(DIRECTORY, {}) == {}
    assert validate_request(MEMBERS, {'symbol': NODE}) == {'symbol': NODE}
    assert ENDPOINTS[DIRECTORY]['function'] == 'stock_sector_spot'
    assert ENDPOINTS[MEMBERS]['function'] == 'stock_sector_detail'
    assert validate_request('concept_members_sina', {'symbol': 'gn_ldc'}) == {'symbol': 'gn_ldc'}
    assert validate_request('concept_list_sina', {}) == {}


@pytest.mark.parametrize('endpoint,params', [
    (DIRECTORY, {'symbol': NODE}), (MEMBERS, {}), (MEMBERS, {'symbol': 'gn_ldc'}),
    (MEMBERS, {'symbol': '零售'}), (MEMBERS, {'symbol': 'hangye_TEST_A', 'date': '2026-10-09'}),
])
def test_new_request_scope_is_explicit(endpoint, params):
    with pytest.raises(ValueError):
        validate_request(endpoint, params)


def test_original_count_and_two_complete_pages_are_required():
    rows = members(81)
    proof = sina_industry_capture_proof(MEMBERS, {'symbol': NODE}, rows, responses(rows))
    assert proof['complete'] is True and proof['upstream_count'] == 81
    assert proof['expected_pages'] == proof['observed_pages'] == [1, 2]
    assert proof['page_row_counts'] == [{'page': 1, 'rows': 80}, {'page': 2, 'rows': 1}]


def test_malformed_gigantic_count_is_retained_without_unbounded_pages():
    rows = members()
    captured = responses(rows)
    captured[0]['body'] = '9' * 100
    proof = sina_industry_capture_proof(MEMBERS, {'symbol': NODE}, rows, captured)
    assert proof['complete'] is False and proof['expected_pages'] is None
    assert str(proof['upstream_count']) == '9' * 100
    assert proof['observed_pages'] == [1]


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'wrong_node', 'count', 'wrong_rows', 'extra_http', 'bad_identity'])
def test_worker_original_proof_does_not_trust_sdk_success(mutation):
    rows = members(81)
    captured = responses(rows)
    if mutation == 'missing': captured.pop()
    elif mutation == 'duplicate': captured.append(deepcopy(captured[-1]))
    elif mutation == 'wrong_node': captured[-1]['parameters']['node'] = 'hangye_TEST_B'
    elif mutation == 'count': captured[0]['body'] = '82'
    elif mutation == 'wrong_rows': captured[-1]['body'][0]['symbol'] = 'sz600081'
    elif mutation == 'extra_http': captured.append(dict(url='https://example.invalid/TEST_FIXTURE', body=[], status_code=200))
    else: captured[-1]['body'][0]['code'] = ['invalid']
    proof = sina_industry_capture_proof(MEMBERS, {'symbol': NODE}, rows, captured)
    assert proof['complete'] is False and proof['reason_codes']
    assert len(rows) == 81  # All reported originals retained, no truncated recovery.


@pytest.mark.parametrize('mutation', ['wrong_scope', 'extra', 'namespace', 'duplicate'])
def test_worker_directory_requires_one_exact_industry_response(mutation):
    rows = [dict(label=NODE)]
    captured = directory_response()
    if mutation == 'wrong_scope': captured[0]['parameters'] = {'param': 'class'}
    elif mutation == 'extra': captured += deepcopy(captured)
    elif mutation == 'namespace': rows[0]['label'] = 'gn_ldc'
    else: rows += deepcopy(rows)
    assert sina_industry_capture_proof(DIRECTORY, {}, rows, captured)['complete'] is False


def test_context_uses_original_members_and_current_clock_without_quote_prices(tmp_path):
    reader, cutoff, _ = context_sources(tmp_path)
    before = [row.model_dump(mode='json') for row in reader.receipts]
    result = build_industry_context(reader, cutoff=cutoff, nodes=(NODE,))
    assert result['status'] == 'AVAILABLE' and result['source_coverage_type'] == 'TEST_FIXTURE'
    theme = result['themes'][0]
    assert theme['name'] == 'TEST_FIXTURE 行业' and theme['theme_type'] == 'INDUSTRY_PROXY'
    assert theme['member_ids'] == ['600001.SH', '600002.SH']
    assert theme['source_refs'] == ['TEST_FIXTURE_INDUSTRY_1', 'TEST_FIXTURE_INDUSTRY_0']
    assert theme['effective_from'] == '2026-10-11'
    assert datetime.fromisoformat(theme['ingested_at'].replace('Z', '+00:00')) == CLOCK+timedelta(milliseconds=2)
    assert not {'trade', 'amount', 'ticktime', '成交额', '涨跌幅'}.intersection(theme)
    assert result['historical_membership_verified'] is False
    assert result['official_pit_validation_countable'] is False
    assert result['economic_mainline_completeness'] == 'UNKNOWN'
    assert result['stock_selection_or_score_changed'] is False
    assert [row.model_dump(mode='json') for row in reader.receipts] == before


@pytest.mark.parametrize('mutation,reason', [
    ('count', 'DIRECTORY_MEMBER_COUNT'), ('namespace', 'NAMESPACE'), ('name', 'NAME_UNAVAILABLE'),
    ('duplicate_node', 'DUPLICATE_DIRECTORY'), ('exchange', 'EXCHANGE_CONFLICT'),
    ('duplicate_member', 'EMPTY_OR_DUPLICATE'), ('code', 'IDENTITY_UNVERIFIED'),
    ('page', 'PAGE_COVERAGE'), ('boolean_page', 'PAGE_COVERAGE'), ('proof_node', 'COUNT_OR_NODE'),
    ('coverage_null', 'PROOF_MISSING'), ('clock', 'CLOCK_MISMATCH'), ('naive', 'AWARE_CLOCK'),
    ('backdate', 'CANNOT_BE_BACKDATED'), ('field', 'SCHEMA_UNREGISTERED'),
])
def test_original_semantic_conflicts_stay_unknown(tmp_path, mutation, reason):
    def change(index, payload, receipt):
        if mutation == 'count' and index == 0: payload['rows'][0]['公司家数'] = 3
        elif mutation == 'namespace' and index == 0: payload['rows'][0]['label'] = 'gn_ldc'
        elif mutation == 'name' and index == 0: payload['rows'][0]['板块'] = ''
        elif mutation == 'duplicate_node' and index == 0:
            payload['rows'].append(deepcopy(payload['rows'][0]))
            payload['coverage']['sina_industry'].update(returned_rows=2, directory_row_count=2)
        elif mutation == 'exchange' and index == 1: payload['rows'][0]['symbol'] = 'sz600001'
        elif mutation == 'duplicate_member' and index == 1: payload['rows'][1] = deepcopy(payload['rows'][0])
        elif mutation == 'code' and index == 1: payload['rows'][0].update(code='830001', symbol='bj830001')
        elif mutation in ('page', 'boolean_page') and index == 1:
            payload['coverage']['sina_industry']['observed_pages'] = [] if mutation == 'page' else [True]
        elif mutation == 'proof_node' and index == 1: payload['coverage']['sina_industry']['node'] = 'hangye_TEST_B'
        elif mutation == 'coverage_null' and index == 1: payload['coverage'] = None
        elif mutation == 'clock' and index == 1: payload['first_seen_at'] = (CLOCK-timedelta(days=1)).isoformat()
        elif mutation == 'naive' and index == 1: payload['first_seen_at'] = '2026-10-11T12:00:00'
        elif mutation == 'backdate' and index == 1: receipt['event_time'] = CLOCK-timedelta(days=1)
        elif mutation == 'field' and index == 1: payload['field_version'] = 'TEST_FIXTURE_WRONG_SCHEMA'
    reader, cutoff, _ = context_sources(tmp_path, alter=change)
    result = reader.build_current_industry_context(cutoff=cutoff, nodes=(NODE,))
    assert result['status'] == 'UNKNOWN' and result['themes'] == []
    assert any(reason in row['reason'] for row in result['unknown'])


@pytest.mark.parametrize('mutation', ['partial', 'future', 'absent_node'])
def test_incomplete_or_later_captures_are_not_legal_empty_members(tmp_path, mutation):
    def change(index, payload, receipt):
        if index == 1 and mutation == 'partial':
            payload.update(status='PARTIAL', pagination_complete=False)
            receipt.update(status='PARTIAL', pagination_complete=False)
        if index == 1 and mutation == 'future': receipt['ingested_at'] = CLOCK+timedelta(seconds=2)
    reader, cutoff, _ = context_sources(tmp_path, alter=change)
    result = reader.build_current_industry_context(cutoff=cutoff,
              nodes=('hangye_TEST_MISSING',) if mutation == 'absent_node' else (NODE,))
    assert result['status'] == 'UNKNOWN' and not result['themes'] and result['unknown']


@pytest.mark.parametrize('nodes', [(), (NODE, NODE), ('gn_ldc',), NODE, (['hangye_TEST_A'],), ('hangye_'+'A'*81,)])
def test_explicit_node_scope_cannot_be_implicitly_expanded(tmp_path, nodes):
    reader, cutoff, _ = context_sources(tmp_path)
    with pytest.raises(ValueError): reader.build_current_industry_context(cutoff=cutoff, nodes=nodes)


@pytest.mark.parametrize('mutation', ['paths_clear', 'paths_delete', 'lookup_clear', 'lookup_replace', 'receipts_subset'])
def test_registered_source_scope_cannot_be_weakened(tmp_path, mutation):
    reader, cutoff, _ = context_sources(tmp_path, unrequested=True)
    other = 'TEST_FIXTURE_INDUSTRY_2'
    if mutation == 'paths_clear': reader._paths.clear()
    elif mutation == 'paths_delete': del reader._paths[other]
    elif mutation == 'lookup_clear': reader._receipts.clear()
    elif mutation == 'lookup_replace': reader._receipts[other] = reader._receipts['TEST_FIXTURE_INDUSTRY_1']
    else: reader.receipts = tuple(row for row in reader.receipts if row.receipt_id != other)
    with pytest.raises(ValueError, match='SOURCE_SCOPE_CHANGED'):
        reader.build_current_industry_context(cutoff=cutoff, nodes=(NODE,))


def test_unrequested_original_tamper_is_detected_by_terminal_scope(tmp_path):
    reader, cutoff, paths = context_sources(tmp_path, unrequested=True)
    path = paths['TEST_FIXTURE_INDUSTRY_2']
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError, match='SHA_MISMATCH'):
        reader.build_current_industry_context(cutoff=cutoff, nodes=(NODE,))


def test_equivalent_aware_offset_is_same_capture_instant(tmp_path):
    def change(index, payload, receipt):
        payload['first_seen_at'] = '2026-10-11T20:00:00+08:00'
    reader, cutoff, _ = context_sources(tmp_path, alter=change)
    assert reader.build_current_industry_context(cutoff=cutoff, nodes=(NODE,))['status'] == 'AVAILABLE'


@pytest.mark.parametrize('field', ['event_time', 'source_published_at', 'first_seen_at', 'ingested_at'])
def test_every_parent_clock_must_be_visible(tmp_path, field):
    def change(index, payload, receipt):
        if index == 1:
            receipt[field] = CLOCK+timedelta(seconds=2)
            if field == 'first_seen_at': receipt['ingested_at'] = CLOCK+timedelta(seconds=2)
    reader, cutoff, _ = context_sources(tmp_path, alter=change)
    result = reader.build_current_industry_context(cutoff=cutoff, nodes=(NODE,))
    assert result['status'] == 'UNKNOWN' and not result['themes']


def test_registered_production_schema_rejects_wrong_original_url(tmp_path):
    from stockfish_core.yaogu.industry_sina_context_v1 import FIELD_VERSION
    reader, cutoff, paths = context_sources(tmp_path)
    originals = []
    for row in reader.receipts:
        payload = json.loads(paths[row.receipt_id].read_bytes())
        payload['field_version'] = FIELD_VERSION
        raw = json.dumps(payload, ensure_ascii=False).encode()
        paths[row.receipt_id].write_bytes(raw)
        originals.append(row.model_copy(update=dict(provider='akshare', field_version=FIELD_VERSION,
            content_hash=hashlib.sha256(raw).hexdigest(), source_url='https://example.invalid/TEST_FIXTURE_WRONG_URL')))
    fresh = IndustrySinaSourceArtifactReader(originals, paths)
    result = fresh.build_current_industry_context(cutoff=cutoff, nodes=(NODE,))
    assert result['status'] == 'UNKNOWN'
    assert 'SOURCE_URL_MISMATCH' in result['unknown'][0]['reason']


def test_during_validation_original_mutation_is_caught_at_return(tmp_path, monkeypatch):
    reader, cutoff, paths = context_sources(tmp_path, unrequested=True)
    original = reader._validate_theme
    def validate_then_mutate(theme, cutoff):
        original(theme, cutoff)
        path = paths['TEST_FIXTURE_INDUSTRY_2']
        path.write_bytes(path.read_bytes()+b' ')
    monkeypatch.setattr(reader, '_validate_theme', validate_then_mutate)
    with pytest.raises(ValueError, match='SHA_MISMATCH'):
        reader.build_current_industry_context(cutoff=cutoff, nodes=(NODE,))


@pytest.mark.parametrize('mutation', ['missing', 'oversized'])
def test_missing_or_oversized_registered_original_cannot_be_consumed(tmp_path, mutation):
    from stockfish_core.yaogu.industry_sina_context_v1 import MAX_RAW_BYTES
    reader, cutoff, paths = context_sources(tmp_path)
    path = paths['TEST_FIXTURE_INDUSTRY_0']
    if mutation == 'missing': path.unlink()
    else:
        with path.open('r+b') as output: output.truncate(MAX_RAW_BYTES+1)
    with pytest.raises(ValueError, match='UNAVAILABLE_OR_TOO_LARGE'):
        reader.build_current_industry_context(cutoff=cutoff, nodes=(NODE,))


def test_duplicate_json_is_rejected_even_with_matching_whole_sha(tmp_path):
    reader, cutoff, paths = context_sources(tmp_path)
    row = reader._receipts['TEST_FIXTURE_INDUSTRY_0']
    path = paths[row.receipt_id]
    raw = path.read_bytes().replace(b'{', b'{"status":"PARTIAL",', 1)
    path.write_bytes(raw)
    changed = row.model_copy(update={'content_hash': hashlib.sha256(raw).hexdigest()})
    fresh = IndustrySinaSourceArtifactReader([changed, reader._receipts['TEST_FIXTURE_INDUSTRY_1']], paths)
    result = fresh.build_current_industry_context(cutoff=cutoff, nodes=(NODE,))
    assert result['status'] == 'UNKNOWN'
    assert 'DUPLICATE_JSON_KEY' in result['unknown'][0]['reason']


@pytest.mark.parametrize('field,value,reason', [
    ('name', '虚构零售热点', 'NAME_NAMESPACE_OR_MEMBERS'), ('theme_id', 'gn_ldc', 'NAME_NAMESPACE_OR_MEMBERS'),
    ('effective_from', '2026-10-09', 'CANNOT_BE_BACKDATED'),
    ('source_refs', ['TEST_FIXTURE_INDUSTRY_1'], 'EXACT_DIRECTORY_AND_MEMBER'),
    ('source_published_at', CLOCK.isoformat(), 'PARENT_FOUR_CLOCKS'),
])
def test_saved_theme_cannot_spoof_source_membership_or_visibility(tmp_path, field, value, reason):
    reader, cutoff, _ = context_sources(tmp_path)
    theme = reader.build_current_industry_context(cutoff=cutoff, nodes=(NODE,))['themes'][0]
    if field == 'source_published_at': value = (CLOCK-timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match=reason):
        reader._validate_theme(ThemeSnapshot.model_validate({**theme, field: value}), cutoff)


def test_saved_context_does_not_restore_source_authority(tmp_path):
    reader, cutoff, _ = context_sources(tmp_path)
    saved = reader.build_current_industry_context(cutoff=cutoff, nodes=(NODE,))
    with pytest.raises(ValueError, match='NOT_SAVED_JSON'):
        build_industry_context(saved, cutoff=cutoff, nodes=(NODE,))
    with pytest.raises(ValueError, match='TEST_FIXTURE_NOT_PRODUCTION'):
        reader.validate_theme_market_pack({'themes': saved['themes']},
            bundle=SimpleNamespace(coverage_type='PROSPECTIVE_CAPTURED'), cutoff=cutoff)


def test_old_concepts_and_base_market_validation_remain_inherited(tmp_path):
    from tests.yaogu.test_research_context_artifacts import current_members_capability, capability
    base, cutoff = current_members_capability(tmp_path)
    adapted = IndustrySinaSourceArtifactReader(base.receipts, base._paths)
    assert adapted.build_current_theme_context(cutoff=cutoff) == base.build_current_theme_context(cutoff=cutoff)
    bundle, cutoff, market, _ = capability(tmp_path)
    fresh = IndustrySinaSourceArtifactReader(market.receipts, market._paths)
    pack = market.build_market_pack(bundle, cutoff=cutoff, sessions=bundle.sessions[-3:])
    before = deepcopy(pack)
    fresh.validate_theme_market_pack(pack, bundle=bundle, cutoff=cutoff)
    assert pack == before


@pytest.mark.parametrize('endpoint,expected', [
    (DIRECTORY, {'indicator': '行业'}), (MEMBERS, {'sector': NODE}),
    ('concept_list_sina', {'indicator': '概念'}), ('concept_members_sina', {'sector': 'gn_TEST_FIXTURE'}),
])
def test_genuine_sdk_function_fixture_receives_only_expected_kwargs(monkeypatch, endpoint, expected):
    """Installed SDK function replaced only for TEST_FIXTURE; zero HTTP/SDK IO."""
    import akshare
    import pandas
    import requests
    from stockfish_core.yaogu.provider_requests_worker import akshare_request
    captured, seen = [], []
    rows = members()
    params = {} if endpoint.endswith('list_sina') else {'symbol': NODE if endpoint == MEMBERS else 'gn_TEST_FIXTURE'}
    def target(**kwargs):
        seen.append(kwargs)
        if endpoint == DIRECTORY:
            answer = [dict(label=NODE, 板块='TEST_FIXTURE 行业', 公司家数=2)]
            captured.extend(directory_response())
        elif endpoint == MEMBERS:
            answer = rows
            captured.extend(responses(rows))
        elif endpoint == 'concept_list_sina':
            answer = [dict(label='gn_TEST_FIXTURE', 板块='TEST_FIXTURE 概念', 公司家数=2)]
            captured.append(dict(url=DIRECTORY_URL, parameters={'param': 'class'}, body={}, status_code=200))
        else:
            answer = rows
            captured.append(dict(url=MEMBERS_URL, parameters={'node': 'gn_TEST_FIXTURE'}, body=2, status_code=200))
        return pandas.DataFrame(answer)
    monkeypatch.setattr(akshare, ENDPOINTS[endpoint]['function'], target)
    # Register restore even though the worker itself temporarily sets this method.
    monkeypatch.setattr(requests.Session, 'request', requests.Session.request)
    result = akshare_request(endpoint, params, captured, max_requests=0)
    assert seen == [expected] and result['status'] == 'COMPLETE'
    assert result['coverage']['http_requests'] == 0
    if endpoint in (DIRECTORY, MEMBERS):
        assert result['coverage']['sina_industry']['complete'] is True
        assert result['field_version'].endswith('_SINA_INDUSTRY_ORIGINAL_COUNT_PAGES_V1')
    else:
        assert 'sina_industry' not in result['coverage']
        assert result['field_version'].endswith('_R03_V1')


def test_source_identity_binds_new_adapter_spec_and_frozen_dependencies():
    identity = source_identity()
    assert identity['explicit_opt_in'] is True and identity['default_source_reader_unchanged'] is True
    files = identity['source_file_hashes']
    assert len(identity['source_hash']) == 64
    assert files['stockfish_core/yaogu/industry_sina_context_v1.py']
    assert files['stockfish_core/yaogu/research_context_artifacts.py']
    assert len(files) == 8
    root = Path(__file__).resolve().parents[2]
    assert all(hashlib.sha256((root / name).read_bytes()).hexdigest() == sha for name, sha in files.items())
