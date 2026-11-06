"""Explicit TEST_FIXTURE originals for conservative factor collection scope."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from stockfish_core.yaogu.collection_qualification import factor_query_scope
from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report
from stockfish_core.yaogu.strategy_experiments import _prepare_context
from tests.support.saved_r2_v3_fixture import physical_source


@pytest.fixture
def originals(tmp_path, monkeypatch):
    import socket
    import sqlite3
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_NO_NETWORK_OR_BUSINESS_DB')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    observed = date(2026, 9, 30)
    seen = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    sessions = tuple(observed - timedelta(days=59-index) for index in range(60))
    sources = []
    def add(endpoint, rows, parameters=None):
        day = date.fromisoformat(parameters['date']) if parameters and 'date' in parameters else None
        item = physical_source(tmp_path/'TEST_FIXTURE-originals', endpoint, rows, seen,
            day=day, parameters=parameters or {}, include_result=True)
        sources.append(json.loads(Path(item['result']['path']).read_text()))
    add('calendar', [{'calendar_date': str(day), 'is_trading_day': '1'}
        for day in (*sessions, observed + timedelta(days=1))],
        {'start_date': str(sessions[0]), 'end_date': str(observed + timedelta(days=1))})
    add('mainboard_sse', [{'证券代码': '600001', '证券简称': 'TEST_FIXTURE_ACTIVE',
                          '上市日期': '2010-01-01'}])
    sz = [dict(A股代码=code, A股简称='TEST_FIXTURE_'+('退' if code == '000005' else code),
        A股上市日期=str(observed) if code == '000004' else '2010-01-01',
        板块='创业板' if code == '300001' else '主板')
        for code in ('000001', '000002', '000003', '000004', '000005', '300001')]
    add('master_szse', sz)
    add('delisting_sse', [])
    codes = ['sh.600001', *['sz.'+row['A股代码'] for row in sz]]
    add('master_all', [dict(code=code, code_name='TEST_FIXTURE',
        tradeStatus='0' if code == 'sz.000002' else '1') for code in codes],
        {'date': str(observed)})
    add('market_daily', [dict(code=code, date=str(observed), adjustflag='3',
        open='10', high='11', low='9', close='10', preclose='10', amount='1000000000',
        volume='1000', turn='10', tradestatus='0' if code == 'sz.000002' else '1',
        isST=None if code == 'sz.000003' else '1' if code == 'sz.000001' else '0')
        for code in codes], {'date': str(observed)})
    arguments = dict(requested_codes=codes + ['sh.600099'], observation_date=observed,
        cutoff=seen+timedelta(hours=1), sessions=sessions, coverage_type='TEST_FIXTURE')
    return sources, arguments


def test_only_original_known_exclusions_removed_and_unknown_kept(originals):
    sources, arguments = originals
    before = deepcopy(sources)
    result = factor_query_scope(sources, **arguments)
    assert result['status'] == 'COMPLETE'
    assert result['factor_query_codes'] == ['sh.600001', 'sz.000003', 'sh.600099']
    assert result['skipped_factor_codes'] == ['sz.000001', 'sz.000002', 'sz.000004', 'sz.000005', 'sz.300001']
    assert result['eligible_ids'] == ['600001.SH']
    assert result['unknown_ids'] == ['000003.SZ', '600099.SH']
    assert {row['reason'] for row in result['known_excluded_records']} == {
        'ST_POOL_DISABLED', 'SUSPENDED_CURRENT_SESSION', 'INSUFFICIENT_LISTING_SESSIONS',
        'DELISTING_OR_DELISTED', 'OUTSIDE_MAIN_BOARD_A_COMMON'}
    bundle, _ = normalize_capture_with_report(sources, arguments['observation_date'],
        arguments['cutoff'], arguments['sessions'], coverage_type='TEST_FIXTURE')
    assert result['qualification'] == _prepare_context(bundle, arguments['cutoff']).qualification
    assert result['normalizer_calls'] == 1
    assert result['strategy_execution_calls'] == result['source_capabilities_issued'] == 0
    assert sources == before


@pytest.mark.parametrize('endpoint', ['calendar', 'mainboard_sse', 'master_szse',
                                    'delisting_sse', 'market_daily', 'master_all'])
def test_missing_required_parent_never_shrinks_scope(originals, endpoint):
    sources, arguments = originals
    result = factor_query_scope([row for row in sources if row['endpoint'] != endpoint], **arguments)
    assert result['status'] == 'UNKNOWN_NO_PRUNING'
    assert result['factor_query_codes'] == arguments['requested_codes']
    assert result['skipped_factor_codes'] == []
    assert result['normalizer_calls'] == 0


@pytest.mark.parametrize('fault', ['partial', 'pagination', 'future_seen', 'future_completion',
    'clock_order', 'date_suffix', 'raw_date', 'duplicate_receipt', 'missing_receipt', 'calendar'])
def test_unproven_parents_keep_every_request(originals, fault):
    sources, arguments = originals
    result = next(row for row in sources if row['endpoint'] == 'market_daily')
    if fault == 'partial':
        result['status'] = 'PARTIAL'
    elif fault == 'pagination':
        result['pagination_complete'] = False
    elif fault == 'future_seen':
        result['receipts'][0]['first_seen_at'] = (arguments['cutoff']+timedelta(hours=1)).isoformat()
    elif fault == 'future_completion':
        result['completed_at'] = (arguments['cutoff']+timedelta(hours=1)).isoformat()
    elif fault == 'clock_order':
        result['started_at'] = arguments['cutoff'].isoformat()
    elif fault == 'date_suffix':
        result['parameters']['date'] += 'malformed'
    elif fault == 'raw_date':
        result['rows'][0]['date'] = '2026-09-29'
    elif fault == 'duplicate_receipt':
        sources.append(deepcopy(result))
    elif fault == 'missing_receipt':
        result['receipts'] = []
    elif fault == 'calendar':
        next(row for row in sources if row['endpoint'] == 'calendar')['rows'].pop(0)
    scoped = factor_query_scope(sources, **arguments)
    assert scoped['status'] == 'UNKNOWN_NO_PRUNING'
    assert scoped['factor_query_codes'] == arguments['requested_codes']
    assert scoped['skipped_factor_codes'] == []
    assert scoped['normalizer_calls'] == 0


@pytest.mark.parametrize('codes', [['sh.600001', 'sh.600001'], ['600001.SH'], ['sh.６００００１']])
def test_invalid_or_duplicate_request_identity_refused(originals, codes):
    sources, arguments = originals
    with pytest.raises(ValueError, match='UNIQUE_SECURITY_CODES'):
        factor_query_scope(sources, **dict(arguments, requested_codes=codes))


def test_unordered_or_forged_calendar_refused(originals):
    sources, arguments = originals
    with pytest.raises(ValueError, match='CALENDAR_SCOPE'):
        factor_query_scope(sources, **dict(arguments, sessions=arguments['sessions'][::-1]))
    result = factor_query_scope(sources, **dict(arguments, sessions=arguments['sessions'][1:]))
    assert result['status'] == 'UNKNOWN_NO_PRUNING'
    assert result['reason_codes'] == ['QUALIFICATION_CALENDAR_DIFFERS_FROM_ORIGINAL']
    assert result['factor_query_codes'] == arguments['requested_codes']


def test_current_day_preview_matches_full_25_session_qualification(originals, tmp_path, monkeypatch):
    from stockfish_core.yaogu import collection_qualification as module
    sources, arguments = originals
    current = deepcopy(sources)
    for day in arguments['sessions'][-25:-1]:
        for endpoint in ('market_daily', 'master_all'):
            original = next(row for row in current if row['endpoint'] == endpoint)
            rows = deepcopy(original['rows'])
            if endpoint == 'market_daily':
                for row in rows:
                    row.update(date=str(day), isST='0', tradestatus='1')
            item = physical_source(tmp_path/'TEST_FIXTURE-25-days', endpoint, rows,
                datetime.fromisoformat(original['receipts'][0]['first_seen_at'])-timedelta(days=1),
                day=day, parameters={'date': str(day)}, include_result=True)
            sources.append(json.loads(Path(item['result']['path']).read_text()))
    full, _ = normalize_capture_with_report(sources, arguments['observation_date'],
        arguments['cutoff'], arguments['sessions'], coverage_type='TEST_FIXTURE')
    full_qualification = _prepare_context(full, arguments['cutoff']).qualification
    normalized_dates = []
    def observe(rows, *args, **kwargs):
        normalized_dates.append([row['parameters']['date'] for row in rows
                                if row['endpoint'] in ('market_daily', 'master_all')])
        return normalize_capture_with_report(rows, *args, **kwargs)
    monkeypatch.setattr(module, 'normalize_capture_with_report', observe)
    full_sources_preview = factor_query_scope(sources, **arguments)
    current_sources_preview = factor_query_scope(current, **arguments)
    assert full_sources_preview['qualification'] == full_qualification
    assert current_sources_preview['qualification'] == full_qualification
    assert full_sources_preview['factor_query_codes'] == current_sources_preview['factor_query_codes']
    assert normalized_dates == [[str(arguments['observation_date'])]*2]*2


def test_historical_exit_identity_cannot_become_definite_exclusion(originals, tmp_path):
    sources, arguments = originals
    prior = arguments['sessions'][-2]
    seen = arguments['cutoff']-timedelta(hours=2)
    market = next(row for row in sources if row['endpoint'] == 'market_daily')
    row = deepcopy(market['rows'][0])
    row.update(code='sh.600099', date=str(prior), isST='1')
    for endpoint, rows, parameters in (
        ('market_daily', [row], {'date': str(prior)}),
        ('stock_basic_all', [dict(code='sh.600099', code_name='TEST_FIXTURE_EXIT',
             ipoDate='2010-01-01', outDate='2026-09-30')], {})):
        item = physical_source(tmp_path/'TEST_FIXTURE-exit', endpoint, rows, seen,
            day=prior if endpoint == 'market_daily' else None,
            parameters=parameters, include_result=True)
        sources.append(json.loads(Path(item['result']['path']).read_text()))
    full, _ = normalize_capture_with_report(sources, arguments['observation_date'],
        arguments['cutoff'], arguments['sessions'], coverage_type='TEST_FIXTURE')
    full_qualification = _prepare_context(full, arguments['cutoff']).qualification
    result = factor_query_scope(sources, **arguments)
    assert any(row['instrument_id'] == '600099.SH'
               for row in full_qualification['eligibility_unknown_records'])
    assert result['qualification'] != full_qualification
    assert result['qualification_scope'] == 'CURRENT_DAY_COLLECTION_PREVIEW_NOT_FINAL_CORE_QUALIFICATION'
    assert 'sh.600099' in result['factor_query_codes']
    assert '600099.SH' in result['unknown_ids']
    assert 'sh.600099' not in result['skipped_factor_codes']
