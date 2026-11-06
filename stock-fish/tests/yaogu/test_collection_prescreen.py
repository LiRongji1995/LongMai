"""Offline TEST_FIXTURE checks for collection routing, never source certification."""
from copy import deepcopy
from fractions import Fraction

import pytest

from stockfish_core.yaogu import short_horizon_participation as r2
from stockfish_core.yaogu import strategy_experiments as se
from stockfish_core.yaogu.collection_prescreen import build_collection_prescreen
from tests.yaogu.test_short_horizon_strength_r2 import fixture_bundle, market_sources, START


SESSIONS = ['2026-09-25', '2026-09-28', '2026-09-29', '2026-09-30', '2026-10-09']
DAY = SESSIONS[-1]
CUTOFF = DAY + 'T20:30:00+08:00'
VISIBLE = DAY + 'T18:00:00+08:00'
IID = '600001.SH'


def core(*, amount='300000000', participation='PASS', selected=False):
    facts = {'session': DAY, 'evidence_status': 'VALID', 'amount_cny': amount,
             'source_refs': ['TEST_FIXTURE_RAW'], 'unit': 'CNY'}
    liquidity = r2.evaluate_liquidity(facts, {'status': 'AVAILABLE',
                                           'median_amount_cny': '1000000000000'})
    item = {'instrument_id': IID, 'research_selected': selected,
            'decision_status': 'WATCH' if selected else 'REJECTED',
            'liquidity': liquidity, 'participation': {'status': participation,
                'observations': [{'status': 'VALID', 'source_refs': ['TEST_FIXTURE_TURNOVER']}]}}
    return {'run_id': 'TEST_FIXTURE_R2', 'observation_date': DAY, 'decision_cutoff': CUTOFF,
        'results': {r2.STRATEGY_ID: {'strategy_id': r2.STRATEGY_ID,
            'observation_date': DAY, 'decision_cutoff': CUTOFF,
            'eligible_pool_ids': [IID], 'full_eligible_pool_retained': True,
            'research_selected_ids': [IID] if selected else [], 'items': [item],
            'market_turnover_context': {'source_refs': ['TEST_FIXTURE_FULL_MARKET']}}}}


def summary(*, listed=False, status='COMPLETE'):
    record = {'instrument_id': IID, 'session': SESSIONS[0], 'evidence_status': 'VALID',
              'source_refs': ['TEST_FIXTURE_LHB'], 'visible_at': VISIBLE}
    return {'window_sessions': list(SESSIONS), 'status': status, 'pagination_complete': True,
            'source_refs': ['TEST_FIXTURE_LHB'], 'visible_at': VISIBLE,
            'records': [record] if listed else []}


def route(run=None, lhb=None, sessions=SESSIONS):
    return build_collection_prescreen(core() if run is None else run,
        calendar_sessions=sessions, lhb_summary=summary() if lhb is None else lhb)


@pytest.mark.parametrize('lhb_state,amount_state,expected', [
    ('PASS', 'PASS', 'ADMITTED'), ('PASS', 'FAIL', 'ADMITTED'),
    ('PASS', 'UNKNOWN', 'ADMITTED'), ('FAIL', 'PASS', 'ADMITTED'),
    ('FAIL', 'FAIL', 'NOT_PRIORITIZED'), ('FAIL', 'UNKNOWN', 'UNKNOWN'),
    ('UNKNOWN', 'PASS', 'ADMITTED'), ('UNKNOWN', 'FAIL', 'UNKNOWN'),
    ('UNKNOWN', 'UNKNOWN', 'UNKNOWN')])
def test_three_valued_or_routes_all_rows(lhb_state, amount_state, expected):
    run = core(amount={'PASS': '300000000', 'FAIL': '299999999', 'UNKNOWN': None}[amount_state])
    lhb = summary(listed=lhb_state == 'PASS', status='UNKNOWN' if lhb_state == 'UNKNOWN' else 'COMPLETE')
    result = route(run, lhb)
    assert result['items'][0]['status'] == expected
    assert result['items'][0]['lhb']['status'] == lhb_state
    assert result['items'][0]['strong_amount']['status'] == amount_state
    assert result['eligible_pool_ids'] == [IID]
    assert sum(result['counts'].values()) == 1


@pytest.mark.parametrize('amount,expected', [
    (Fraction(300000000) - Fraction(1, 10**12), 'NOT_PRIORITIZED'),
    (Fraction(300000000), 'ADMITTED'),
    (Fraction(300000000) + Fraction(1, 10**12), 'ADMITTED')])
def test_dynamic_reference_boundary_uses_exact_values(amount, expected):
    result = route(core(amount=amount))
    assert result['items'][0]['status'] == expected
    assert result['items'][0]['strong_amount']['reference_cny'] == {
        'numerator': 300000000, 'denominator': 1}


@pytest.mark.parametrize('participation,expected', [('PASS', 'ADMITTED'),
    ('FAIL', 'NOT_PRIORITIZED'), ('UNKNOWN', 'UNKNOWN'), (None, 'UNKNOWN')])
def test_strong_amount_also_requires_original_participation(participation, expected):
    assert route(core(participation=participation))['items'][0]['status'] == expected


def test_known_amount_failure_and_unknown_participation_do_not_invent_match():
    row = route(core(amount='1', participation='UNKNOWN'))['items'][0]
    assert row['strong_amount']['status'] == 'FAIL'
    assert row['status'] == 'NOT_PRIORITIZED'


@pytest.mark.parametrize('bad', ['status', 'date', 'source'])
def test_unverified_large_amount_cannot_admit(bad):
    run = core(amount='999999999999')
    liquidity = run['results'][r2.STRATEGY_ID]['items'][0]['liquidity']
    if bad == 'status':
        liquidity['status'] = 'UNKNOWN'
    elif bad == 'date':
        liquidity['evidence']['session'] = SESSIONS[-2]
    else:
        liquidity['evidence']['source_refs'] = []
    result = route(run)
    assert result['items'][0]['status'] == 'UNKNOWN'
    assert result['items'][0]['strong_amount']['amount_cny'] is None


def test_lhb_does_not_require_positive_net_buy_or_use_post_listing_returns():
    lhb = summary(listed=True)
    lhb['records'][0].update(net_buy_cny=-999999999, reason='TEST_FIXTURE',
                             上榜后5日涨幅='-99.9', 上榜解读='TEST_FIXTURE')
    result = route(core(amount='1', participation='FAIL'), lhb)
    assert result['items'][0]['status'] == 'ADMITTED'
    assert result['items'][0]['admitted_by'] == ['LHB_CLUE']
    assert result['items'][0]['lhb']['records'] == [{
        'session': SESSIONS[0], 'source_refs': ['TEST_FIXTURE_LHB'], 'visible_at': VISIBLE}]


@pytest.mark.parametrize('bad', ['PARTIAL', 'FETCH_FAILED', 'UNKNOWN', 'NOT_REQUESTED'])
def test_incomplete_parent_never_proves_positive_or_absence(bad):
    for listed in (False, True):
        result = route(core(amount='1'), summary(listed=listed, status=bad))
        assert result['items'][0]['lhb']['status'] == 'UNKNOWN'
        assert result['items'][0]['status'] == 'UNKNOWN'
        assert result['lhb_window_complete'] is False
        assert result['lhb_source_status'] == bad


@pytest.mark.parametrize('change', [
    {'pagination_complete': False}, {'source_refs': []}, {'visible_at': None},
    {'visible_at': DAY + 'T20:30:01+08:00'}, {'visible_at': DAY + 'T18:00:00'},
    {'window_sessions': SESSIONS[1:]}, {'records': None}])
def test_complete_label_alone_cannot_prove_no_lhb(change):
    lhb = summary()
    lhb.update(change)
    assert route(core(amount='1'), lhb)['items'][0]['status'] == 'UNKNOWN'


@pytest.mark.parametrize('change', [
    {'session': '2026-09-24'}, {'evidence_status': 'UNKNOWN'}, {'source_refs': []},
    {'source_refs': ['TEST_FIXTURE_UNBOUND_PARENT']}, {'visible_at': None},
    {'visible_at': DAY + 'T20:30:01+08:00'}, {'instrument_id': None}])
def test_invalid_lhb_record_neither_admits_nor_proves_no_record(change):
    lhb = summary(listed=True)
    lhb['records'][0].update(change)
    result = route(core(amount='1'), lhb)
    assert result['items'][0]['status'] == 'UNKNOWN'
    assert result['items'][0]['lhb']['records'] == []


def test_exchange_calendar_window_includes_pre_holiday_session_and_ignores_future_calendar():
    result = route(core(amount='1'), summary(listed=True), SESSIONS + ['2026-10-12'])
    assert result['window_sessions'] == SESSIONS
    assert result['items'][0]['status'] == 'ADMITTED'
    assert route(core(amount='1'), summary(listed=True), SESSIONS[-4:])['items'][0]['status'] == 'UNKNOWN'


@pytest.mark.parametrize('sessions', [SESSIONS[::-1], SESSIONS + [DAY],
                                     SESSIONS[:-1], ['not-a-date', *SESSIONS]])
def test_invalid_calendar_cannot_be_silently_sorted_or_filled(sessions):
    with pytest.raises(ValueError, match='CALENDAR_INVALID'):
        route(sessions=sessions)


def test_exact_instrument_identity_and_multiple_disclosures():
    lhb = summary(listed=True)
    lhb['records'].append({**lhb['records'][0], 'session': SESSIONS[1]})
    result = route(core(amount='1'), lhb)
    assert len(result['items'][0]['lhb']['records']) == 2
    for row in lhb['records']:
        row['instrument_id'] = '600001.SZ'
    assert route(core(amount='1'), lhb)['items'][0]['status'] == 'NOT_PRIORITIZED'


def test_original_selected_is_annotation_not_admission_and_inputs_are_unchanged():
    run, lhb = core(amount='1', selected=True), summary()
    before = deepcopy((run, lhb, SESSIONS))
    result = route(run, lhb)
    assert result['items'][0]['status'] == 'NOT_PRIORITIZED'
    assert result['original_r2_selected_ids'] == [IID]
    assert result['items'][0]['original_r2_selected'] is True
    assert result['items'][0]['original_r2_decision_status'] == 'WATCH'
    assert (run, lhb, SESSIONS) == before
    assert result['not_prioritized_is_strategy_rejection'] is False
    assert result['market_edge'] == 'NOT_ESTABLISHED'
    assert result['source_authentication'] == 'REQUIRED_FROM_CALLER_ORIGINAL_READERS'


@pytest.mark.parametrize('bad', ['truncated', 'duplicate', 'selected', 'cutoff', 'strategy'])
def test_unbound_or_truncated_core_shapes_are_rejected(bad):
    run = core()
    result = run['results'][r2.STRATEGY_ID]
    if bad == 'truncated':
        result['items'] = []
    elif bad == 'duplicate':
        result['items'] *= 2
    elif bad == 'selected':
        result['research_selected_ids'] = [IID]
    elif bad == 'cutoff':
        result['decision_cutoff'] = DAY + 'T21:00:00+08:00'
    else:
        result['strategy_id'] = 'PV_OBSERVATION_V1'
    with pytest.raises(ValueError, match='COLLECTION_PRESCREEN_'):
        route(run)


def test_real_native_r2_shape_and_source_refs_without_changing_strategy_output():
    bundle, cutoff = fixture_bundle({START: [Fraction(1, 20)] * 5})
    reader = market_sources(bundle)
    run = se.run_short_horizon_strategy(bundle, cutoff=cutoff, strategy_id=r2.STRATEGY_ID,
                                      _source_payload_reader=reader)
    before = deepcopy(run)
    result = build_collection_prescreen(run, calendar_sessions=bundle.sessions,
                                        lhb_summary={'status': 'NOT_REQUESTED'})
    assert result['admitted_ids'] == [START]
    assert result['market_reference_source_refs'] == sorted(reader.original_bytes)
    assert len(result['items']) == len(run['results'][r2.STRATEGY_ID]['eligible_pool_ids'])
    assert result['original_r2_selected_ids'] == run['results'][r2.STRATEGY_ID]['research_selected_ids']
    assert result['items'][0]['strong_amount']['source_refs']
    assert result['decision_cutoff'] == cutoff.isoformat()
    assert run == before
