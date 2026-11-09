"""TEST_FIXTURE: invocation-local clock reuse keeps all source semantics."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from stockfish_core.yaogu import provider_normalization as normalization
from tests.r03_sources.test_provider_boundaries import DAY, NOW, SESSIONS, master, raw, result


def receipt(seen, ingested=None):
    return {'first_seen_at': seen, 'ingested_at': ingested or seen}


@pytest.mark.parametrize('session', [None, date(2026, 9, 4)])
def test_exact_clock_projection_order_aliasing_and_changed_receipt(session):
    sources = {
        'TEST_FIXTURE_first': receipt('2026-09-05T21:00:00+08:00', '2026-09-05T21:01:00+08:00'),
        'TEST_FIXTURE_equal_utc': receipt('2026-09-05T13:00:00+00:00', '2026-09-05T13:01:00+00:00'),
        'TEST_FIXTURE_later': receipt('2026-09-05T22:00:00+08:00'),
    }
    clock, derived = normalization._normalization_clock_projections()
    for refs in (('TEST_FIXTURE_first', 'TEST_FIXTURE_equal_utc'),
                 ('TEST_FIXTURE_equal_utc', 'TEST_FIXTURE_first'),
                 ('TEST_FIXTURE_first', 'TEST_FIXTURE_first', 'TEST_FIXTURE_later')):
        expected = normalization._derived_clock(sources, refs, session)
        actual = derived(sources, refs, session)
        assert {k: v.isoformat() for k, v in actual.items()} == {k: v.isoformat() for k, v in expected.items()}
        actual['first_seen_at'] = datetime(1900, 1, 1)
        assert derived(sources, refs, session) == expected
    original = clock(sources['TEST_FIXTURE_first'], session)
    original.clear()
    assert clock(sources['TEST_FIXTURE_first'], session) == normalization._clock(sources['TEST_FIXTURE_first'], session)
    sources['TEST_FIXTURE_first']['ingested_at'] = '2026-09-05T23:00:00+08:00'
    assert derived(sources, ('TEST_FIXTURE_first',), session) == normalization._derived_clock(sources, ('TEST_FIXTURE_first',), session)


def test_missing_or_invalid_source_is_never_filled_from_previous_projection():
    clock, derived = normalization._normalization_clock_projections()
    source = receipt('2026-09-05T21:00:00+08:00')
    clock(source)
    source['ingested_at'] = 'TEST_FIXTURE_INVALID'
    with pytest.raises(ValueError):
        clock(source)
    with pytest.raises(KeyError):
        derived({}, ('TEST_FIXTURE_missing',))
    with pytest.raises(ValueError):
        derived({}, ())


def test_repeated_projection_work_is_local_to_this_normalization():
    source = receipt('2026-09-05T21:00:00+08:00')
    with patch.object(normalization, '_clock', wraps=normalization._clock) as original:
        first, _ = normalization._normalization_clock_projections()
        for _ in range(20):
            first(source, DAY)
        assert original.call_count == 1
        second, _ = normalization._normalization_clock_projections()
        second(source, DAY)
        assert original.call_count == 2


@pytest.mark.parametrize('composed', [False, True])
def test_projection_bound_falls_back_to_original_semantics(composed):
    clock, derived = normalization._normalization_clock_projections()
    inputs = [receipt((NOW-timedelta(seconds=index)).isoformat()) for index in range(4097)]
    name = '_derived_clock' if composed else '_clock'
    original = getattr(normalization, name)
    def project(source):
        return derived({'TEST_FIXTURE': source}, ('TEST_FIXTURE',), DAY) if composed else clock(source, DAY)
    expected = original({'TEST_FIXTURE': inputs[-1]}, ('TEST_FIXTURE',), DAY) if composed else original(inputs[-1], DAY)
    with patch.object(normalization, name, wraps=original) as called:
        for source in inputs:
            project(source)
        assert called.call_count == 4097
        project(inputs[0])
        assert called.call_count == 4097
        assert project(inputs[-1]) == expected
        assert called.call_count == 4098


def sources():
    codes = ('sh.600000', 'sh.600001')
    rows = [raw(code, day) for code in codes for day in SESSIONS]
    return [result('mainboard_sse', [master('600000'), master('600001')], stamp=NOW-timedelta(hours=2)),
            result('master_szse', [], stamp=NOW-timedelta(hours=2)),
            result('history', rows, {'codes': list(codes), 'start_date': str(SESSIONS[0]), 'end_date': str(DAY)},
                   stamp=NOW-timedelta(hours=1)),
            result('master_all', [{'code': code, 'tradeStatus': '1'} for code in codes], {'date': str(DAY)},
                   stamp=NOW-timedelta(minutes=30)),
            result('adjustment_factors', [{'code': code, 'dividOperateDate': '2000-01-01',
                   'backAdjustFactor': '2.123456'} for code in codes],
                   {'codes': list(codes), 'start_date': '1990-01-01', 'end_date': str(DAY)}, stamp=NOW)]


@pytest.mark.parametrize('scenario', ['complete', 'missing_factors', 'partial_factors', 'later_master'])
def test_whole_normalization_equals_original_projections(scenario):
    data = sources()
    if scenario == 'missing_factors':
        data.pop()
    elif scenario == 'partial_factors':
        data[-1].update(status='PARTIAL', pagination_complete=False,
            coverage={'security_statuses': [{'code': 'sh.600000', 'status': 'COMPLETE',
                       'row_count': 1, 'cursor_exhausted': True},
                      {'code': 'sh.600001', 'status': 'FETCH_FAILED', 'row_count': 0,
                       'cursor_exhausted': False, 'error_code': 'TEST_FIXTURE_TIMEOUT'}]})
        data[-1]['rows'] = data[-1]['rows'][:1]
    elif scenario == 'later_master':
        data.append(result('mainboard_sse', [master('600000')], stamp=NOW))
    before_input = deepcopy(data)
    with patch.object(normalization, '_normalization_clock_projections',
                      return_value=(normalization._clock, normalization._derived_clock)):
        reference, reference_report = normalization.normalize_capture_with_report(data, DAY, NOW, SESSIONS)
    actual, actual_report = normalization.normalize_capture_with_report(data, DAY, NOW, SESSIONS)
    assert actual.model_dump(mode='json') == reference.model_dump(mode='json')
    assert actual_report == reference_report
    assert data == before_input
    assert len(actual.bars) > 0
    data[0]['receipts'][0]['ingested_at'] = (NOW+timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match='CUTOFF_PRECEDES_ACTUAL_CAPTURE_COMPLETION'):
        normalization.normalize_capture_with_report(data, DAY, NOW, SESSIONS)
