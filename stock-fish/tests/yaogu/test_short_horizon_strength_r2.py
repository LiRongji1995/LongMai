"""Exact participation and immutable-parent boundary tests; TEST_FIXTURE only."""
from copy import deepcopy
from datetime import timedelta
from fractions import Fraction as F
import hashlib
import json

import pytest
from pydantic import ValidationError

from stockfish_core.storage import IntegrityError
from stockfish_core.yaogu import short_horizon_participation as r2
from stockfish_core.yaogu import short_horizon_strength as r1
from stockfish_core.yaogu import strategy_experiments as se
from stockfish_core.yaogu.contracts import MarketInput, SourceReceipt, digest
from stockfish_core.yaogu.history_artifacts import digest_json_stream, digest_market_input
from tests.support.short_horizon_strength_fixture import (
    START, CONTINUATION, PULLBACK, WEAK, payload, strength_bundle,
)


def fixture_bundle(turns=None, alter=None):
    bundle, cutoff = strength_bundle()
    value = payload(bundle)
    recent = value['sessions'][-5:]
    for row in value['bars']:
        row['turnover_basis'] = 'BAOSTOCK_TURN_PERCENT_FLOAT_SHARES'
        if row['session'] == value['observation_date']:
            row['amount_cny'] = '500000000'
        if row['session'] in recent:
            index = recent.index(row['session'])
            row['turnover_fraction'] = (turns or {}).get(row['instrument_id'], [F(1, 100)] * 5)[index]
    if alter:
        alter(value, cutoff)
    return MarketInput.model_validate(value), cutoff


def style(bundle, cutoff, iid=START):
    ctx = se._prepare_context(bundle, cutoff)
    facts = r2.collect_participation_features(ctx, iid)
    return r2.evaluate_participation(facts), facts


def frozen_parent(bundle, cutoff):
    return se.run_short_horizon_strategy(bundle, cutoff=cutoff)


def provenance(parent, bundle):
    result = parent['results'][r1.STRATEGY_ID]
    return {'mode': 'VERIFIED_FROZEN_R1_INLINE_STYLE', 'base_run_id': parent['run_id'],
        # The API caller's archive reader verifies bytes; this fixture supplies
        # a separate explicit SHA-bound archive evidence role.
        'base_run_sha256': digest(parent), 'base_run_content_hash': digest_json_stream(parent),
        'base_result_hash': digest_json_stream(result), 'base_source_hash': result['source_hash'],
        'input_id': parent['input_id'], 'input_hash': digest_market_input(bundle),
        'observation_date': parent['observation_date'], 'decision_cutoff': parent['decision_cutoff']}


class SourceBytes:
    """Only authenticated original bytes; no trusted total scalar."""
    def __init__(self, receipts, original_bytes):
        self.receipts = tuple(receipts)
        self.original_bytes = original_bytes

    def __call__(self, receipt_id):
        return self.original_bytes[receipt_id]


def market_sources(bundle, totals=None, alter=None, receipt_alter=None, capture_at=None):
    totals = totals or [1000000000000] * 5
    receipts, original_bytes = [], {}
    for index, day in enumerate(bundle.sessions[-5:]):
        session = str(day)
        clock = (capture_at or next(row.event_time for row in bundle.bars if row.session == day)).isoformat()
        codes = ['sh.600001', 'sh.688001', 'sz.000001', 'sz.300001']
        master = [{'code': code, 'code_name': 'TEST_FIXTURE', 'tradeStatus': '1'} for code in codes]
        # Complete master also contains identities excluded from the definition.
        master += [{'code': 'sh.000001', 'tradeStatus': '1'}, {'code': 'sh.900001', 'tradeStatus': '1'},
                   {'code': 'bj.830001', 'tradeStatus': '1'}, {'code': 'sz.159001', 'tradeStatus': '1'}]
        market = [{'code': code, 'date': session, 'adjustflag': '3', 'tradestatus': '1',
                   'amount': str(totals[index] if offset == 0 else 0), 'volume': '100'}
                  for offset, code in enumerate(codes)]
        if alter:
            alter(index, master, market)
        for dataset, rows in (('master_all', master), ('market_daily', market)):
            receipt_id = 'TEST_FIXTURE_' + dataset + '_' + session
            source = {'status': 'COMPLETE', 'pagination_complete': True,
                      'field_version': 'BAOSTOCK_00.9.30_R03_V1', 'first_seen_at': clock, 'rows': rows}
            raw = json.dumps(source, sort_keys=True).encode()
            value = dict(receipt_id=receipt_id, provider='baostock', dataset=dataset,
                content_hash=hashlib.sha256(raw).hexdigest(), field_version=source['field_version'],
                row_count=len(rows), status='COMPLETE', pagination_complete=True, parameters={'date': session},
                business_sessions=[session], event_time=clock, source_published_at=clock,
                first_seen_at=clock, ingested_at=clock)
            if receipt_alter:
                receipt_alter(index, dataset, value)
            receipts.append(SourceReceipt.model_validate(value))
            original_bytes[receipt_id] = raw
    return SourceBytes(receipts, original_bytes)


def market_context(bundle, cutoff, reader):
    ctx = r2.prepare_inline_style_context(bundle, cutoff)
    return r2.collect_market_turnover_context(ctx, _source_payload_reader=reader)


@pytest.mark.parametrize('value,expected', [(F(1, 20), 'PASS'),
    (F(1, 20) - F(1, 10**12), 'FAIL'), (F(1, 20) + F(1, 10**12), 'PASS'),
    (F(0), 'FAIL')])
def test_exact_five_percent_boundary_uses_fraction_not_display_rounding(value, expected):
    bundle, cutoff = fixture_bundle({START: [value] * 5})
    decision, _ = style(bundle, cutoff)
    assert decision['status'] == expected
    assert decision['threshold'] == {'numerator': 1, 'denominator': 20}
    assert round(float((F(1, 20) - F(1, 10**12)) * 100), 6) == round(float(F(1, 20) * 100), 6)


def test_recent_active_session_allows_today_low_turnover_and_preserves_all_evidence():
    bundle, cutoff = fixture_bundle({START: [F(1, 100), F(1, 20), F(1, 100), F(1, 100), F(3, 1000)]})
    decision, facts = style(bundle, cutoff)
    assert decision['status'] == 'PASS'
    assert decision['active_sessions'] == [str(bundle.sessions[-4])]
    assert decision['window_complete'] is True
    assert facts['observations'][-1]['turnover_fraction'] == F(3, 1000)


def test_only_one_verified_high_session_can_pass_other_missing_sessions():
    def alter(value, _):
        recent = set(value['sessions'][-5:])
        value['bars'] = [row for row in value['bars'] if row['instrument_id'] != START
            or row['session'] not in recent or row['session'] == value['sessions'][-3]]
    bundle, cutoff = fixture_bundle({START: [F(1, 100), F(1, 100), F(1, 20), F(1, 100), F(1, 100)]}, alter)
    decision, _ = style(bundle, cutoff)
    assert decision['status'] == 'PASS'
    assert decision['verified_session_count'] == 1
    assert decision['window_complete'] is False


@pytest.mark.parametrize('kind', ['missing', 'unknown_basis', 'partial_quality', 'late_row',
                                'partial_receipt', 'incomplete_pagination', 'late_receipt', 'wrong_id_scope'])
def test_unavailable_high_turnover_is_unknown_and_never_counts_as_active(kind):
    def alter(value, cutoff):
        current = next(row for row in value['bars'] if row['instrument_id'] == START
                       and row['session'] == value['observation_date'])
        if kind == 'missing':
            current['turnover_fraction'] = None
        elif kind == 'unknown_basis':
            current['turnover_basis'] = 'UNVERIFIED_SHARES_OR_ANY_NONEMPTY_STRING'
        elif kind == 'partial_quality':
            current['quality'] = 'PARTIAL'
        elif kind == 'late_row':
            current['first_seen_at'] = current['ingested_at'] = cutoff + timedelta(seconds=1)
        else:
            original = next(row for row in value['receipts'] if row['receipt_id'] == 'TEST_FIXTURE_BARS')
            parent = {**original, 'receipt_id': 'TEST_FIXTURE_CURRENT_BAD_PARENT'}
            current['source_refs'] = [parent['receipt_id']]
            if kind == 'partial_receipt':
                parent['status'] = 'PARTIAL'
            elif kind == 'incomplete_pagination':
                parent['pagination_complete'] = False
            elif kind == 'late_receipt':
                parent['first_seen_at'] = parent['ingested_at'] = cutoff + timedelta(seconds=1)
                current['first_seen_at'] = current['ingested_at'] = parent['ingested_at']
            elif kind == 'wrong_id_scope':
                parent['instrument_ids'] = [WEAK]
            value['receipts'].append(parent)
    bundle, cutoff = fixture_bundle({START: [F(1, 100)] * 4 + [F(1, 20)]}, alter)
    decision, facts = style(bundle, cutoff)
    assert decision['status'] == 'UNKNOWN'
    assert decision['verified_session_count'] == 4
    assert decision['verified_active_session_count'] == 0
    assert facts['observations'][-1]['turnover_fraction'] is None


def test_conflicting_same_clock_revisions_are_rejected_by_original_input_contract():
    def alter(value, _):
        current = next(row for row in value['bars'] if row['instrument_id'] == START
                       and row['session'] == value['observation_date'])
        value['bars'].append({**current, 'turnover_fraction': F(1, 20)})
    with pytest.raises(ValidationError, match='duplicate bar revision'):
        fixture_bundle(alter=alter)


def test_session_before_recent_window_cannot_authorize_participation():
    def alter(value, _):
        row = next(row for row in value['bars'] if row['instrument_id'] == START
                   and row['session'] == value['sessions'][-6])
        row['turnover_fraction'] = F(1, 10)
    bundle, cutoff = fixture_bundle(alter=alter)
    assert style(bundle, cutoff)[0]['status'] == 'FAIL'


def test_names_industries_and_old_rank_labels_do_not_change_predicate():
    bundle, cutoff = fixture_bundle({START: [F(1, 20)] + [F(1, 100)] * 4})
    _, facts = style(bundle, cutoff)
    expected = r2.evaluate_participation(facts)
    for name, industry in [('平安银行', '银行'), ('中国联通', '通信'), ('九阳股份', '消费')]:
        assert r2.evaluate_participation({**facts, 'name': name, 'industry': industry,
            'instrument_id': 'IGNORE', 'original_rank': 1}) == expected


def test_native_run_intersects_original_r1_without_changing_formula_or_safety():
    bundle, cutoff = fixture_bundle({START: [F(1, 20)] + [F(3, 1000)] * 4,
        PULLBACK: [F(11, 200)] + [F(1, 100)] * 4, WEAK: [F(1, 10)] * 5})
    base = r1.run_short_horizon_strength(bundle, cutoff=cutoff)
    original = deepcopy(base)
    result = r2.run_short_horizon_strength(bundle, cutoff=cutoff)
    assert base == original
    assert result['research_selected_ids'] == sorted([START, PULLBACK])
    assert len(result['items']) == len(base['items']) == 4
    assert result['top_ids'] == result['qualified_ids'] == []
    assert result['counts']['focus'] == result['counts']['passed'] == 0
    rows, old = ({row['instrument_id']: row for row in document['items']} for document in (result, base))
    for iid, row in rows.items():
        assert row['arms'] == old[iid]['arms']
        assert row['matched_arms'] == old[iid]['matched_arms']
        assert row['limit_safety'] == old[iid]['limit_safety']
        assert row['formal_focus'] is row['core_pass'] is False
        assert row['market_edge'] == 'NOT_ESTABLISHED'
    assert rows[CONTINUATION]['participation_status'] == 'FAIL'
    assert rows[CONTINUATION]['decision_status'] == 'REJECTED'
    assert rows[WEAK]['participation_status'] == 'PASS'
    assert rows[WEAK]['decision_status'] == 'REJECTED'
    assert result['base_run_provenance']['base_run_id_role'] == 'IN_MEMORY_NATIVE_R1_RESULT_NOT_SAVED_RUN'


def test_frozen_native_parent_uses_inline_features_preserves_original_and_complete_ids():
    bundle, cutoff = fixture_bundle({START: [F(1, 20)] + [F(3, 1000)] * 4,
                                    PULLBACK: [F(11, 200)] + [F(1, 100)] * 4})
    parent = frozen_parent(bundle, cutoff)
    original = deepcopy(parent)
    ctx = r2.prepare_inline_style_context(bundle, cutoff)
    result = r2.apply_style_to_r1_result(bundle, parent['results'][r1.STRATEGY_ID], cutoff=cutoff,
        _context=ctx, base_run=parent, base_provenance=provenance(parent, bundle))
    assert parent == original
    assert result['research_selected_ids'] == sorted([START, PULLBACK])
    assert result['eligible_pool_ids'] == parent['results'][r1.STRATEGY_ID]['eligible_pool_ids']
    assert result['base_run_provenance']['style_source_scope'] == 'ONLY_ORIGINAL_INPUT_INLINE_BARS_AND_RECEIPTS'
    assert result['base_run_provenance']['original_r1_history_database_reverified_in_this_application'] is False
    assert 'base_run' not in result['base_run_provenance']


@pytest.mark.parametrize('change', ['run_hash', 'stable_run_id', 'input_hash', 'cutoff',
                                 'result_hash', 'qualification', 'missing_ids', 'duplicate_ids', 'source_hash'])
def test_frozen_binding_rejects_tampered_or_unbound_inputs(change):
    bundle, cutoff = fixture_bundle({START: [F(1, 20)] * 5})
    parent = frozen_parent(bundle, cutoff)
    proof = provenance(parent, bundle)
    result = parent['results'][r1.STRATEGY_ID]
    if change == 'run_hash':
        proof['base_run_content_hash'] = '0' * 64
    elif change == 'stable_run_id':
        parent['run_id'] = proof['base_run_id'] = 'strategy_experiment_UNVERIFIED'
        proof['base_run_content_hash'] = digest_json_stream(parent)
    elif change == 'input_hash':
        proof['input_hash'] = '0' * 64
    elif change == 'cutoff':
        proof['decision_cutoff'] = (cutoff + timedelta(seconds=1)).isoformat()
    else:
        result = deepcopy(result)
        if change == 'result_hash':
            proof['base_result_hash'] = '0' * 64
        elif change == 'qualification':
            result['qualification']['eligible'] += 1
        elif change == 'missing_ids':
            result['items'].pop()
        elif change == 'duplicate_ids':
            result['items'][-1] = result['items'][0]
        elif change == 'source_hash':
            result['source_hash'] = '0' * 64
    with pytest.raises(ValueError, match='SHORT_HORIZON_R2_'):
        r2.apply_style_to_r1_result(bundle, result, cutoff=cutoff, base_run=parent, base_provenance=proof)


def test_unbound_historical_input_remains_rejected_by_native_r1_and_r2():
    bundle, cutoff = fixture_bundle()
    historical = bundle.model_copy(update={'history_artifact_ref': 'TEST_FIXTURE_UNAVAILABLE_HISTORY',
                                           'history_artifact_hash': '0' * 64})
    with pytest.raises(IntegrityError, match='HISTORY_ARTIFACT_STORE_NOT_BOUND'):
        r1.run_short_horizon_strength(historical, cutoff=cutoff)
    with pytest.raises(IntegrityError, match='HISTORY_ARTIFACT_STORE_NOT_BOUND'):
        r2.run_short_horizon_strength(historical, cutoff=cutoff)
    # Preparing an explicitly inline-only new feature view does not grant
    # historical R1 authority and leaves original identities untouched.
    ctx = r2.prepare_inline_style_context(historical, cutoff)
    assert ctx.bundle.history_artifact_ref is None
    assert historical.history_artifact_ref == 'TEST_FIXTURE_UNAVAILABLE_HISTORY'


def test_unknown_style_cannot_promote_unselected_r1_and_does_not_become_zero():
    bundle, cutoff = fixture_bundle({START: [None] * 5, WEAK: [F(1, 10)] * 5})
    result = r2.run_short_horizon_strength(bundle, cutoff=cutoff)
    rows = {row['instrument_id']: row for row in result['items']}
    assert rows[START]['r1_research_selected'] is True
    assert rows[START]['participation_status'] == rows[START]['decision_status'] == 'UNKNOWN'
    assert rows[START]['participation_peak_turnover_fraction'] is None
    assert rows[WEAK]['decision_status'] == 'REJECTED'
    assert result['counts']['focus'] == 0


def test_invalid_or_out_of_window_numeric_evidence_never_authorizes_pass():
    facts = {'window_sessions': ['2026-10-02', '2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08'],
             'observations': [{'session': '2026-10-01', 'status': 'VALID', 'turnover_fraction': '0.5'}]}
    assert r2.evaluate_participation(facts)['status'] == 'UNKNOWN'
    for value in ('NaN', 'Infinity', '-0.1', '10.00001', None):
        facts['observations'] = [{'session': day, 'status': 'VALID', 'turnover_fraction': value}
                                 for day in facts['window_sessions']]
        assert r2.evaluate_participation(facts)['status'] == 'UNKNOWN'


@pytest.mark.parametrize('amount,position', [('300000000', 'AT_REFERENCE'),
    ('299999999.99', 'BELOW_REFERENCE'), ('300000000.01', 'ABOVE_REFERENCE'),
    ('0', 'BELOW_REFERENCE'), (None, 'UNKNOWN')])
def test_dynamic_reference_exact_boundary_and_missing_are_only_context(amount, position):
    def alter(value, _):
        row = next(row for row in value['bars'] if row['instrument_id'] == START
                   and row['session'] == value['observation_date'])
        row['amount_cny'] = amount
    bundle, cutoff = fixture_bundle({START: [F(1, 20)] * 5}, alter)
    ctx = se._prepare_context(bundle, cutoff)
    context = market_context(bundle, cutoff, market_sources(bundle))
    result = r2.evaluate_liquidity(r2.collect_liquidity_features(ctx, START), context)
    assert result['status'] == ('UNKNOWN' if amount is None else 'AVAILABLE')
    assert result['position'] == position
    assert result['selection_gate'] is False
    assert result['reference_cny'] == {'numerator': 300000000, 'denominator': 1}
    if amount is None:
        assert result['value'] is None
    else:
        assert r1.exact(result['value']) == F(amount)


@pytest.mark.parametrize('kind', ['late_row', 'partial_quality', 'incomplete_parent',
                                'unrecognized_identity_scope', 'invalid_ohlc'])
def test_unqualified_current_high_amount_is_unknown_not_verified_capacity(kind):
    def alter(value, cutoff):
        row = next(row for row in value['bars'] if row['instrument_id'] == START
                   and row['session'] == value['observation_date'])
        row['amount_cny'] = '900000000'
        if kind == 'late_row':
            row['first_seen_at'] = row['ingested_at'] = cutoff + timedelta(seconds=1)
        elif kind == 'partial_quality':
            row['quality'] = 'PARTIAL'
        elif kind == 'invalid_ohlc':
            row['high'] = '1'
        else:
            original = next(item for item in value['receipts'] if item['receipt_id'] == 'TEST_FIXTURE_BARS')
            parent = {**original, 'receipt_id': 'TEST_FIXTURE_BAD_CURRENT_AMOUNT'}
            if kind == 'incomplete_parent':
                parent['pagination_complete'] = False
            else:
                parent['instrument_ids'] = [WEAK]
            row['source_refs'] = [parent['receipt_id']]
            value['receipts'].append(parent)
    bundle, cutoff = fixture_bundle({START: [F(1, 20)] * 5}, alter)
    ctx = se._prepare_context(bundle, cutoff)
    result = r2.evaluate_liquidity(r2.collect_liquidity_features(ctx, START))
    assert result['status'] == 'UNKNOWN'
    assert result['value'] is None


def test_below_dynamic_reference_does_not_remove_original_r1_and_participation_match():
    def alter(value, _):
        for row in value['bars']:
            if row['instrument_id'] == START and row['session'] == value['observation_date']:
                row['amount_cny'] = '299999999.99'
    bundle, cutoff = fixture_bundle({START: [F(1, 20)] * 5}, alter)
    result = r2.run_short_horizon_strength(bundle, cutoff=cutoff, _source_payload_reader=market_sources(bundle))
    row = next(row for row in result['items'] if row['instrument_id'] == START)
    assert row['participation_status'] == 'PASS'
    assert row['liquidity_status'] == 'AVAILABLE'
    assert row['liquidity_position'] == 'BELOW_REFERENCE'
    assert row['style_status'] == 'PASS'
    assert row['research_selected'] is True
    assert row['decision_status'] == 'WATCH'


def test_liquidity_predicate_ignores_names_industries_and_target_labels():
    facts = {'amount_cny': F(500000000), 'evidence_status': 'VALID', 'reason_codes': []}
    context = {'status': 'AVAILABLE', 'median_amount_cny': r1.encode(F(1000000000000))}
    expected = r2.evaluate_liquidity(facts, context)
    for name, industry in [('平安银行', '银行'), ('中国联通', '通信'), ('九阳股份', '消费')]:
        actual = r2.evaluate_liquidity({**facts, 'name': name, 'industry': industry,
            'user_target': True, 'original_rank': 1}, context)
        assert {key: value for key, value in actual.items() if key != 'evidence'} == {
            key: value for key, value in expected.items() if key != 'evidence'}
        assert actual['evidence']['name'] == name


def test_original_source_bytes_recompute_five_day_median_full_four_board_scope():
    bundle, cutoff = fixture_bundle()
    original_hash = digest_market_input(bundle)
    totals = [1700000000000, 1000000000000, 1900000000000, 1200000000000, 1400000000000]
    context = market_context(bundle, cutoff, market_sources(bundle, totals))
    assert context['status'] == 'AVAILABLE'
    assert r1.exact(context['median_amount_cny']) == F(1400000000000)
    assert [r1.exact(row['amount_cny']) for row in context['observations']] == list(map(F, totals))
    assert all(row['expected_count'] == row['observed_count'] == row['trading_count'] == 4
               for row in context['observations'])
    assert len(context['source_refs']) == len(context['source_receipts']) == 10
    assert context['source_receipts_hash'] == digest(context['source_receipts'])
    assert context['considered_source_receipts_hash'] == digest(context['considered_source_receipts'])
    assert context['selection_gate'] is False
    assert digest_market_input(bundle) == original_hash
    liquidity = r2.evaluate_liquidity({'amount_cny': F(380000000), 'evidence_status': 'VALID'}, context)
    assert r1.exact(liquidity['reference_cny']) == F(380000000)
    assert r1.exact(liquidity['soft_score_exact']) == F(50)


def test_missing_reader_and_missing_calendar_window_are_unknown_without_zero_or_selection_gate():
    bundle, cutoff = fixture_bundle({START: [F(1, 20)] * 5})
    reader = market_sources(bundle)
    reader.receipts = tuple(receipt for receipt in reader.receipts
                            if str(receipt.parameters['date']) != str(bundle.sessions[-5]))
    partial = market_context(bundle, cutoff, reader)
    assert partial['status'] == 'UNKNOWN'
    assert partial['median_amount_cny'] is None
    assert partial['observations'][0]['amount_cny'] is None
    result = r2.run_short_horizon_strength(bundle, cutoff=cutoff)
    row = next(row for row in result['items'] if row['instrument_id'] == START)
    assert result['market_turnover_context']['status'] == row['liquidity_status'] == 'UNKNOWN'
    assert row['research_selected'] is True and row['style_status'] == 'PASS'
    assert row['liquidity_reference_cny'] is None


@pytest.mark.parametrize('kind', ['missing_trading_amount', 'incomplete_scope', 'duplicate_raw',
    'duplicate_master', 'unknown_master_status', 'nonraw_basis', 'wrong_date', 'nonfinite_amount',
    'unexpected_market_identity', 'nontrading_positive_amount'])
def test_market_business_gaps_are_unknown_never_partial_total(kind):
    def alter(index, master, market):
        if index != 4:
            return
        if kind == 'missing_trading_amount':
            market[0]['amount'] = ''
        elif kind == 'incomplete_scope':
            market.pop()
        elif kind == 'duplicate_raw':
            market.append(dict(market[0]))
        elif kind == 'duplicate_master':
            master.append(dict(master[0]))
        elif kind == 'unknown_master_status':
            master[0]['tradeStatus'] = ''
        elif kind == 'nonraw_basis':
            market[0]['adjustflag'] = '2'
        elif kind == 'wrong_date':
            market[0]['date'] = str(bundle.sessions[-2])
        elif kind == 'nonfinite_amount':
            market[0]['amount'] = 'NaN'
        elif kind == 'unexpected_market_identity':
            market.append({**market[0], 'code': 'bj.830001'})
        else:
            master[0]['tradeStatus'] = market[0]['tradestatus'] = '0'
    bundle, cutoff = fixture_bundle()
    context = market_context(bundle, cutoff, market_sources(bundle, alter=alter))
    assert context['status'] == 'UNKNOWN'
    assert context['median_amount_cny'] is context['observations'][-1]['amount_cny'] is None
    assert context['observations'][-1]['reason_codes']


def test_verified_nontrading_identity_excluded_without_zero_imputation():
    def alter(_, master, market):
        master[-5]['tradeStatus'] = market[-1]['tradestatus'] = '0'
        market[-1]['amount'] = market[-1]['volume'] = ''
    bundle, cutoff = fixture_bundle()
    context = market_context(bundle, cutoff, market_sources(bundle, alter=alter))
    assert context['status'] == 'AVAILABLE'
    assert all(row['known_nontrading_excluded_count'] == 1 and row['trading_count'] == 3
               and row['expected_count'] == 4 for row in context['observations'])
    assert r1.exact(context['median_amount_cny']) == F(1000000000000)


@pytest.mark.parametrize('kind', ['late', 'partial', 'pagination', 'wrong_session_scope', 'field_version',
                                'raw_sha', 'raw_clock'])
def test_market_source_authority_and_original_bytes_must_be_verified(kind):
    def alter(index, dataset, receipt):
        if index != 4 or dataset != 'market_daily':
            return
        if kind == 'late':
            receipt['first_seen_at'] = receipt['ingested_at'] = cutoff + timedelta(seconds=1)
        elif kind == 'partial':
            receipt['status'] = 'PARTIAL'
        elif kind == 'pagination':
            receipt['pagination_complete'] = False
        elif kind == 'wrong_session_scope':
            receipt['business_sessions'] = [str(bundle.sessions[-2])]
        elif kind == 'field_version':
            receipt['field_version'] = 'UNRECOGNIZED_AMOUNT_UNIT'
        elif kind == 'raw_sha':
            receipt['content_hash'] = '0' * 64
        elif kind == 'raw_clock':
            receipt['first_seen_at'] = receipt['ingested_at'] = cutoff - timedelta(minutes=1)
    bundle, cutoff = fixture_bundle()
    context = market_context(bundle, cutoff, market_sources(bundle, receipt_alter=alter))
    assert context['status'] == 'UNKNOWN'
    assert context['median_amount_cny'] is None
    assert len(context['considered_source_receipts']) == 10


def test_same_receipt_id_different_hash_is_rejected_not_replaced_by_context_layer():
    bundle, cutoff = fixture_bundle()
    reader = market_sources(bundle)
    reader.receipts += (reader.receipts[0].model_copy(update={'content_hash': '0' * 64}),)
    with pytest.raises(ValueError, match='SHR2_MARKET_CONTEXT_SOURCE_RECEIPT_CONFLICT'):
        market_context(bundle, cutoff, reader)


def test_later_context_cutoff_binds_frozen_parent_old_cutoff_without_backdating_sources():
    bundle, cutoff = fixture_bundle({START: [F(1, 20)] * 5})
    parent = frozen_parent(bundle, cutoff)
    original = deepcopy(parent)
    later = cutoff + timedelta(hours=2)
    reader = market_sources(bundle, capture_at=cutoff + timedelta(hours=1))
    original_context = market_context(bundle, cutoff, reader)
    assert original_context['status'] == 'UNKNOWN'
    result = r2.apply_style_to_r1_result(bundle, parent['results'][r1.STRATEGY_ID], cutoff=later,
        base_run=parent, base_provenance=provenance(parent, bundle), _source_payload_reader=reader)
    assert result['market_turnover_context']['status'] == 'AVAILABLE'
    assert result['decision_cutoff'] == result['new_context_cutoff'] == later.isoformat()
    assert result['base_decision_cutoff'] == parent['decision_cutoff']
    assert result['research_selected_ids'] == [START]
    assert result['base_run_provenance']['original_r1_history_database_reverified_in_this_application'] is False
    assert parent == original


def test_frozen_new_cutoff_before_parent_is_rejected():
    bundle, cutoff = fixture_bundle({START: [F(1, 20)] * 5})
    parent = frozen_parent(bundle, cutoff)
    with pytest.raises(ValueError, match='SHORT_HORIZON_R2_FROZEN_CONTEXT_EXTENSION_SCOPE_MISMATCH'):
        r2.apply_style_to_r1_result(bundle, parent['results'][r1.STRATEGY_ID],
            cutoff=cutoff - timedelta(minutes=1), base_run=parent, base_provenance=provenance(parent, bundle))


def test_later_visible_original_price_revision_cannot_change_frozen_parent():
    def alter(value, cutoff):
        row = next(row for row in value['bars'] if row['instrument_id'] == START
                   and row['session'] == value['observation_date'])
        value['bars'].append({**row, 'amount_cny': '700000000',
            'first_seen_at': cutoff + timedelta(minutes=1), 'ingested_at': cutoff + timedelta(minutes=1)})
    bundle, cutoff = fixture_bundle({START: [F(1, 20)] * 5}, alter)
    parent = frozen_parent(bundle, cutoff)
    with pytest.raises(ValueError, match='SHORT_HORIZON_R2_FROZEN_CONTEXT_EXTENSION_PRICE_REVISION'):
        r2.apply_style_to_r1_result(bundle, parent['results'][r1.STRATEGY_ID],
            cutoff=cutoff + timedelta(hours=1), base_run=parent, base_provenance=provenance(parent, bundle))
