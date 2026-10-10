"""Original-byte context authentication boundaries; isolated TEST_FIXTURE."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

from stockfish_core.yaogu.research_context_artifacts import SourceArtifactReader, RETURN_BASIS
from stockfish_core.yaogu.contracts import SourceReceipt, ThemeSnapshot
from tests.yaogu.test_short_horizon_strength_r2 import fixture_bundle, market_sources


def capability(tmp_path):
    bundle, cutoff = fixture_bundle()
    def prices(index, master, market):
        for row in market:
            row.update(open='10', high='11', low='9', close='10.5', preclose='10')
    original = market_sources(bundle, alter=prices)
    paths = {}
    for iid, raw in original.original_bytes.items():
        path = tmp_path / (iid + '.json')
        path.write_bytes(raw)
        paths[iid] = path
    reader = SourceArtifactReader(original.receipts, paths)
    return bundle, cutoff, reader, paths


def test_authenticated_full_scope_pack_keeps_original_input_and_unadjusted_prices(tmp_path):
    bundle, cutoff, reader, _ = capability(tmp_path)
    before = bundle.model_dump(mode='json')
    pack = reader.build_market_pack(bundle, cutoff=cutoff, sessions=bundle.sessions[-5:], return_basis=RETURN_BASIS)
    reader.validate_theme_market_pack(pack, bundle=bundle, cutoff=cutoff)
    assert bundle.model_dump(mode='json') == before
    assert len(pack['sessions']) == 5
    assert {row['scope'] for row in pack['sessions']} == {'SSE_SZSE_A_COMMON_ALL_BOARDS'}
    assert pack['sessions'][0]['rows'][0]['comparable_close'] is None


@pytest.mark.parametrize('mutation', ['price', 'member', 'parent', 'date', 'cutoff'])
def test_pack_cannot_change_source_price_membership_parents_or_binding(tmp_path, mutation):
    bundle, cutoff, reader, _ = capability(tmp_path)
    pack = reader.build_market_pack(bundle, cutoff=cutoff, sessions=bundle.sessions[-5:])
    changed = deepcopy(pack)
    if mutation == 'price':
        changed['sessions'][0]['rows'][0]['close'] = '10.6'
    elif mutation == 'member':
        changed['sessions'][0]['expected_instrument_ids'].pop()
    elif mutation == 'parent':
        changed['sessions'][0]['source_refs'] = []
    elif mutation == 'date':
        changed['observation_date'] = str(bundle.sessions[-2])
    else:
        changed['decision_cutoff'] = (cutoff + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError):
        reader.validate_theme_market_pack(changed, bundle=bundle, cutoff=cutoff)


def test_original_byte_tampering_is_rejected_even_for_unchanged_pack(tmp_path):
    bundle, cutoff, reader, paths = capability(tmp_path)
    pack = reader.build_market_pack(bundle, cutoff=cutoff, sessions=bundle.sessions[-5:])
    first_parent = pack['sessions'][0]['source_refs'][0]
    paths[first_parent].write_bytes(paths[first_parent].read_bytes() + b' ')
    with pytest.raises(ValueError, match='SHA_MISMATCH'):
        reader.validate_theme_market_pack(pack, bundle=bundle, cutoff=cutoff)


def test_newly_captured_history_is_unavailable_at_an_old_cutoff(tmp_path):
    bundle, cutoff = fixture_bundle()
    original = market_sources(bundle, capture_at=cutoff + timedelta(seconds=1))
    paths = {}
    for iid, raw in original.original_bytes.items():
        path = tmp_path / (iid + '.json')
        path.write_bytes(raw)
        paths[iid] = path
    reader = SourceArtifactReader(original.receipts, paths)
    with pytest.raises(ValueError, match='CAPTURE_AFTER_CUTOFF'):
        reader.build_market_pack(bundle, cutoff=cutoff, sessions=bundle.sessions[-5:])


def current_members_capability(tmp_path, *, count=2, member_symbol='sh600001',
                               name='食品安全', capture_after=False):
    """Original bytes for a complete TEST_FIXTURE directory and constituent page."""
    _, cutoff = fixture_bundle()
    clock = cutoff + timedelta(seconds=1) if capture_after else cutoff
    receipts, paths = [], {}
    contents = (
        ('concept_list_sina', [{'label': 'gn_spaq', '板块': name, '公司家数': count,
                               '成交额': '999999999999999999', '涨跌幅': '99'}], {}),
        ('concept_members_sina', [{'code': '600001', 'symbol': member_symbol, 'trade': '9999'},
                                  {'code': '000001', 'symbol': 'sz000001', 'trade': '9999'}],
                                  {'symbol': 'gn_spaq'}),
    )
    for dataset, rows, parameters in contents:
        rid = 'TEST_FIXTURE_' + dataset
        payload = {'status': 'COMPLETE', 'pagination_complete': True,
                   'field_version': 'TEST_FIXTURE_SINA_V1',
                   'first_seen_at': clock.isoformat(), 'rows': rows}
        raw = json.dumps(payload, ensure_ascii=False).encode()
        path = tmp_path / (rid + '.json')
        path.write_bytes(raw)
        paths[rid] = path
        receipts.append(SourceReceipt(receipt_id=rid, provider='TEST_FIXTURE', dataset=dataset,
            content_hash=hashlib.sha256(raw).hexdigest(), field_version=payload['field_version'],
            status='COMPLETE', pagination_complete=True, row_count=len(rows), parameters=parameters,
            event_time=clock, source_published_at=clock, first_seen_at=clock, ingested_at=clock))
    return SourceArtifactReader(receipts, paths), cutoff


def test_current_theme_uses_full_membership_without_undated_quote_fields(tmp_path):
    reader, cutoff = current_members_capability(tmp_path)
    result = reader.build_current_theme_context(cutoff=cutoff)
    assert result['status'] == 'AVAILABLE'
    assert result['historical_membership_verified'] is False
    theme = result['themes'][0]
    assert theme['member_ids'] == ['000001.SZ', '600001.SH']
    assert theme['effective_from'] == cutoff.date().isoformat()
    assert theme['source_refs'] == ['TEST_FIXTURE_concept_members_sina', 'TEST_FIXTURE_concept_list_sina']
    assert not {'trade', '成交额', '涨跌幅'}.intersection(theme)


@pytest.mark.parametrize('kwargs,reason', [
    ({'count': 3}, 'DIRECTORY_MEMBER_COUNT_MISMATCH'),
    ({'member_symbol': 'sz600001'}, 'SOURCE_EXCHANGE_CONFLICT'),
])
def test_complete_transport_cannot_override_member_count_or_exchange_proof(tmp_path, kwargs, reason):
    reader, cutoff = current_members_capability(tmp_path, **kwargs)
    result = reader.build_current_theme_context(cutoff=cutoff)
    assert result['status'] == 'PARTIAL'
    assert result['themes'] == []
    assert reason in result['unknown'][0]['reason']


def test_current_theme_cannot_backdate_members_or_spoof_original_directory_name(tmp_path):
    reader, cutoff = current_members_capability(tmp_path)
    theme = reader.build_current_theme_context(cutoff=cutoff)['themes'][0]
    for field, value, reason in (
        ('effective_from', (cutoff.date() - timedelta(days=1)).isoformat(), 'CANNOT_BE_BACKDATED'),
        ('name', '虚构热点', 'DIRECTORY_NAME_MISMATCH'),
    ):
        changed = {**theme, field: value}
        with pytest.raises(ValueError, match=reason):
            reader._validate_theme(ThemeSnapshot.model_validate(changed), cutoff)


def test_qualification_tags_and_later_capture_are_not_current_theme_evidence(tmp_path):
    reader, cutoff = current_members_capability(tmp_path, name='沪股通')
    result = reader.build_current_theme_context(cutoff=cutoff)
    assert result['themes'] == []
    assert result['qualification_categories'][0]['reason'] == 'QUALIFICATION_CATEGORY_NOT_THEME'
    reader, cutoff = current_members_capability(tmp_path, capture_after=True)
    result = reader.build_current_theme_context(cutoff=cutoff)
    assert result['status'] == 'UNKNOWN'
    assert result['themes'] == []
    assert result['unknown'][0]['reason'] == 'CURRENT_CONCEPT_DIRECTORY_UNAVAILABLE'


def calendar_capability(tmp_path, *, incomplete=False):
    from datetime import datetime, timezone
    clock = datetime(2026, 10, 10, 19, tzinfo=timezone.utc)
    rows = [{'calendar_date': day, 'is_trading_day': flag} for day, flag in (
        ('2026-10-09', '1'), ('2026-10-10', '0'), ('2026-10-11', '0'), ('2026-10-12', '1'))]
    if incomplete:
        rows.pop(1)
    raw = json.dumps({'status': 'COMPLETE', 'pagination_complete': True,
        'field_version': 'TEST_FIXTURE_CALENDAR_V1', 'first_seen_at': clock.isoformat(), 'rows': rows}).encode()
    path = tmp_path / 'TEST_FIXTURE_calendar.json'
    path.write_bytes(raw)
    receipt = SourceReceipt(receipt_id='TEST_FIXTURE_calendar', provider='baostock', dataset='calendar',
        content_hash=hashlib.sha256(raw).hexdigest(), field_version='TEST_FIXTURE_CALENDAR_V1',
        status='COMPLETE', pagination_complete=True, row_count=len(rows),
        parameters={'start_date': '2026-10-09', 'end_date': '2026-10-12'},
        event_time=clock, source_published_at=clock, first_seen_at=clock, ingested_at=clock)
    return SourceArtifactReader([receipt], {receipt.receipt_id: path}), clock


def test_target_calendar_preserves_old_cutoff_unknown_and_confirms_actual_next_session(tmp_path):
    reader, cutoff = calendar_capability(tmp_path)
    kwargs = dict(observation_date='2026-10-09', target_session='2026-10-12')
    old = reader.target_calendar_evidence(**kwargs, cutoff=cutoff-timedelta(seconds=1), coverage_type='TEST_FIXTURE')
    assert old['status'] == 'UNKNOWN' and old['verified_next_session'] is None
    actual = reader.target_calendar_evidence(**kwargs, cutoff=cutoff, coverage_type='TEST_FIXTURE')
    assert actual['status'] == 'AVAILABLE'
    assert actual['verified_next_session'] == '2026-10-12'
    assert actual['source_refs'] == ['TEST_FIXTURE_calendar']


@pytest.mark.parametrize('target', ['2026-10-10', '2026-10-11', '2026-10-13'])
def test_target_cannot_be_weekend_or_skip_the_known_next_trading_session(tmp_path, target):
    reader, cutoff = calendar_capability(tmp_path)
    with pytest.raises(ValueError, match='NOT_VERIFIED_NEXT_TRADING_SESSION'):
        reader.target_calendar_evidence(observation_date='2026-10-09', target_session=target, cutoff=cutoff,
            coverage_type='TEST_FIXTURE')


def test_calendar_transport_complete_without_all_dates_is_unverified(tmp_path):
    reader, cutoff = calendar_capability(tmp_path, incomplete=True)
    with pytest.raises(ValueError, match='FULL_DATE_SCOPE_UNVERIFIED'):
        reader.target_calendar_evidence(observation_date='2026-10-09', target_session='2026-10-12', cutoff=cutoff,
            coverage_type='TEST_FIXTURE')


def test_fixture_calendar_field_definition_is_not_a_production_definition(tmp_path):
    reader, cutoff = calendar_capability(tmp_path)
    with pytest.raises(ValueError, match='FIELD_VERSION_UNRECOGNIZED'):
        reader.target_calendar_evidence(observation_date='2026-10-09', target_session='2026-10-12', cutoff=cutoff)


def test_calendar_future_coverage_event_is_not_a_later_received_price(tmp_path):
    from datetime import date, datetime, time
    from stockfish_core.yaogu.visibility import SH
    reader, cutoff = calendar_capability(tmp_path)
    parent = reader.receipts[0]
    content = json.loads(reader(parent.receipt_id))
    content['field_version'] = 'BAOSTOCK_00.9.30_R03_V1'
    raw = json.dumps(content).encode()
    path = tmp_path / 'TEST_FIXTURE_native_calendar_schema.json'
    path.write_bytes(raw)
    source = parent.model_copy(update={'field_version': content['field_version'],
        'content_hash': hashlib.sha256(raw).hexdigest(),
        'event_time': datetime.combine(date(2026, 10, 12), time(15), SH)})
    before = source.model_dump(mode='json')
    capability = SourceArtifactReader([source], {source.receipt_id: path})
    result = capability.target_calendar_evidence(observation_date='2026-10-09',
        target_session='2026-10-12', cutoff=cutoff)
    assert result['status'] == 'AVAILABLE'
    assert result['original_event_time'] == source.event_time.isoformat()
    assert result['event_time_role'] == 'ORIGINAL_TYPED_CALENDAR_COVERAGE_END_NOT_PRICE_EVENT'
    assert source.model_dump(mode='json') == before
    late = source.model_copy(update={'source_published_at': cutoff+timedelta(seconds=1)})
    late_capability = SourceArtifactReader([late], {late.receipt_id: path})
    assert late_capability.target_calendar_evidence(observation_date='2026-10-09',
        target_session='2026-10-12', cutoff=cutoff)['status'] == 'UNKNOWN'


def test_same_clock_conflicting_original_calendar_sources_are_rejected(tmp_path):
    reader, cutoff = calendar_capability(tmp_path)
    parent = reader.receipts[0]
    original = json.loads(reader(parent.receipt_id))
    original['rows'][-1]['is_trading_day'] = '0'
    raw = json.dumps(original).encode()
    path = tmp_path / 'TEST_FIXTURE_calendar_conflict.json'
    path.write_bytes(raw)
    conflict = parent.model_copy(update={'receipt_id': 'TEST_FIXTURE_calendar_conflict',
        'content_hash': hashlib.sha256(raw).hexdigest()})
    conflicted = SourceArtifactReader([parent, conflict],
        {parent.receipt_id: reader._paths[parent.receipt_id], conflict.receipt_id: path})
    with pytest.raises(ValueError, match='SAME_CLOCK_SOURCE_CONFLICT'):
        conflicted.target_calendar_evidence(observation_date='2026-10-09', target_session='2026-10-12',
            cutoff=cutoff, coverage_type='TEST_FIXTURE')


def test_native_target_metadata_is_identity_bound_without_changing_selection():
    from stockfish_core.yaogu.strategy_experiments import run_short_horizon_strategy
    from tests.yaogu.test_short_horizon_strength_r2 import frozen_parent, provenance
    bundle, cutoff = fixture_bundle()
    base = frozen_parent(bundle, cutoff)
    reader = SourceArtifactReader([], {})
    kwargs = dict(cutoff=cutoff, strategy_id='SHORT_HORIZON_STRENGTH_R2', _frozen_base_run=base,
        _base_provenance=provenance(base, bundle), _source_payload_reader=reader,
        _target_calendar_validator=reader.validate_target_calendar_context)
    outputs = []
    for offset in (1, 2):
        context = reader.target_calendar_evidence(observation_date=bundle.observation_date,
            target_session=bundle.observation_date+timedelta(days=offset), cutoff=cutoff,
            coverage_type=bundle.coverage_type)
        outputs.append(run_short_horizon_strategy(bundle, _target_calendar_context=context, **kwargs))
    left, right = outputs
    assert left['input_hash'] == right['input_hash']
    assert left['run_id'] != right['run_id']
    assert left['research_context_hash'] != right['research_context_hash']
    assert left['effective_input_hash'] != right['effective_input_hash']
    assert left['results']['SHORT_HORIZON_STRENGTH_R2']['items'] == right['results']['SHORT_HORIZON_STRENGTH_R2']['items']
    forged = {**context, 'status': 'AVAILABLE'}
    with pytest.raises(ValueError, match='ORIGINAL_SOURCE_MISMATCH'):
        run_short_horizon_strategy(bundle, _target_calendar_context=forged, **kwargs)
