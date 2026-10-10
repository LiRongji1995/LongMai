"""Bounded exact/PIT tests; all market and membership data is TEST_FIXTURE."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction as F
import json
import sys
from zoneinfo import ZoneInfo

import pytest

from stockfish_core.yaogu import theme_evolution as te
from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu.history_artifacts import digest_json_stream, digest_market_input

SH = ZoneInfo('Asia/Shanghai')
IDS = ('600001.SH', '000001.SZ', '688001.SH', '300001.SZ')


def fixture_market(session_count=67):
    days, cursor = [], date(2026, 1, 5)
    while len(days) < session_count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    receipts, panels, themes, all_bars = [], [], [], []
    for index, day in enumerate(days):
        event = datetime(day.year, day.month, day.day, 15, tzinfo=SH)
        seen = event + timedelta(hours=1)
        clocks = dict(event_time=event.isoformat(), source_published_at=seen.isoformat(),
                      first_seen_at=seen.isoformat(), ingested_at=(seen + timedelta(minutes=1)).isoformat())
        rid = 'TEST_FIXTURE_DAY_' + str(day)
        receipts.append(dict(receipt_id=rid, provider='TEST_FIXTURE', dataset='TEST_FIXTURE_RAW_AND_DATED_MEMBERS',
            content_hash=digest({'TEST_FIXTURE': str(day)}), field_version='TEST_FIXTURE_V1',
            row_count=4, pagination_complete=True, status='COMPLETE', instrument_ids=IDS,
            business_sessions=[str(day)], **clocks))
        rows = []
        for j, iid in enumerate(IDS):
            price = Decimal('10') * Decimal('1.01') ** index if j < 2 else Decimal('10')
            amount = '30' if j < 2 and index >= 65 else '10' if j < 2 else '70' if index >= 65 else '90'
            rows.append(dict(instrument_id=iid, session=str(day), open=str(price), high=str(price),
                low=str(price), close=str(price), reference_prev_close=str(price),
                comparable_close=str(price), comparable_basis='TEST_FIXTURE_UNCHANGED_BASIS',
                amount_cny=amount, volume_shares='1', source_refs=[rid], quality='VALID', **clocks))
        all_bars.extend(rows)
        panels.append(dict(session=str(day), scope=te.MARKET_SCOPE, expected_instrument_ids=IDS,
            rows=rows, source_refs=[rid], clocks=clocks, status='COMPLETE', pagination_complete=True,
            amount_cny='999999999999'))  # Deliberately untrusted scalar.
        themes.append(dict(theme_id='TEST_FIXTURE_THEME', name='TEST_FIXTURE', theme_type='EVENT',
            member_ids=IDS[:2], effective_from=str(day), effective_to=str(day + timedelta(days=1)),
            definition_version='TEST_FIXTURE_DATED_MEMBERS_V1', source_refs=[rid], **clocks))
    last = days[-1]
    clock = panels[-1]['clocks']
    securities = [dict(instrument_id=iid, exchange='SSE' if iid.endswith('.SH') else 'SZSE',
        board='MAIN' if j < 2 else 'STAR' if j == 2 else 'CHINEXT', security_type='A_COMMON',
        listed_on='2020-01-01', effective_from=str(last), name='TEST_FIXTURE', st=False,
        suspended=False, delisting_period=False, source_refs=[receipts[-1]['receipt_id']], **clock)
        for j, iid in enumerate(IDS)]
    bundle = MarketInput.model_validate(dict(observation_date=str(last), sessions=list(map(str, days)),
        universe=dict(snapshot_id='TEST_FIXTURE_UNIVERSE', securities=securities, complete=True,
            expected_count=4, source_refs=[receipts[-1]['receipt_id']], **clock),
        bars=all_bars[-24:], themes=themes, receipts=receipts, coverage_type='TEST_FIXTURE'))
    cutoff = datetime(last.year, last.month, last.day, 17, tzinfo=SH)
    pack = dict(schema_version='THEME_MARKET_PACK_V1', source_input_hash=digest_market_input(bundle),
        observation_date=str(last), decision_cutoff=cutoff.isoformat(), coverage_type='TEST_FIXTURE',
        receipts=receipts, sessions=panels, themes=[])
    return bundle, cutoff, pack


def fixture_capability(pack, *, bundle, cutoff):
    # Explicit isolated fixture capability; production must verify raw bytes and
    # dated security masters. No JSON verified flag is accepted by the core.
    assert bundle.coverage_type == 'TEST_FIXTURE'
    assert pack['coverage_type'] == 'TEST_FIXTURE'


def facts(**changes):
    return dict(session='2026-10-09', previous_exchange_session='2026-10-08',
        expansion=F(3, 2), rs3=F(1, 100), rs5=F(1, 50), breadth=F(3, 5),
        share=F(1, 10), theme_return_1d=F(1, 100), **changes)


@pytest.mark.parametrize('delta,expected', [(F(0), 'PASS'), (-F(1, 10**12), 'FAIL')])
def test_strengthening_exact_boundaries(delta, expected):
    values = facts()
    values['expansion'] += delta
    assert te.evaluate_day(values)['candidate_states']['strengthening'] == expected
    values = facts()
    values['rs5'] += delta
    assert te.evaluate_day(values)['candidate_states']['strengthening'] == expected
    values = facts()
    values['breadth'] += delta
    assert te.evaluate_day(values)['candidate_states']['strengthening'] == expected


def test_emerging_requires_positive_rs_and_fifty_five_percent_breadth():
    values = facts()
    values.update(expansion=F(5, 4), breadth=F(11, 20), rs3=F(1, 10**20))
    assert te.evaluate_day(values)['candidate_states']['emerging'] == 'PASS'
    values['rs3'] = F(0)
    assert te.evaluate_day(values)['candidate_states']['emerging'] == 'FAIL'


def test_two_consecutive_exchange_sessions_required_missing_day_cannot_confirm():
    today = facts()
    previous = facts()
    previous.update(session='2026-10-08', candidate_states={'strengthening': 'PASS', 'emerging': 'PASS', 'weakening': 'FAIL'})
    assert te.evaluate_day(today, previous)['stage'] == 'STRENGTHENING'
    previous['session'] = '2026-09-30'
    assert te.evaluate_day(today, previous)['stage'] == 'STRENGTHENING_PENDING'
    assert te.evaluate_day(today, previous)['confirmation_states']['strengthening'] == 'UNKNOWN'


def test_unknown_is_not_known_mixed_and_two_day_weakening():
    current = facts()
    current.update(expansion=None, rs3=None, rs5=None, breadth=None)
    assert te.evaluate_day(current)['stage'] == 'UNKNOWN'
    current.update(expansion=F(1), rs3=F(0), rs5=F(0), breadth=F(1, 2))
    assert te.evaluate_day(current)['stage'] == 'MIXED_PENDING'
    current.update(rs3=-F(1, 100), breadth=F(2, 5))
    previous = {**current, 'session': '2026-10-08', 'candidate_states': {'weakening': 'PASS'}}
    assert te.evaluate_day(current, previous)['stage'] == 'WEAKENING'


def test_divergence_after_strength_retains_independent_risk_axis():
    current = facts()
    previous = {**current, 'session': '2026-10-08', 'breadth': F(7, 10),
                'candidate_states': {'strengthening': 'PASS', 'emerging': 'PASS', 'weakening': 'FAIL'}}
    result = te.evaluate_day(current, previous, previous_stage='STRENGTHENING')
    assert result['stage'] == 'DIVERGENCE'
    assert result['high_risk']['independent_of_stage'] is True


def test_high_risk_exact_breadth_drop_is_independent_and_missing_history_unknown():
    current = facts()
    current.update(breadth=F(11, 20), share=F(1, 5), theme_return_1d=F(0))
    history = [{'share': F(1, 10), 'breadth': F(7, 10)} for _ in range(60)]
    risk = te.evaluate_day(current, history=history)['high_risk']
    assert risk['status'] == 'PASS'
    assert risk['breadth_decline'] == F(3, 20)
    current['breadth'] += F(1, 10**12)
    assert te.evaluate_day(current, history=history)['high_risk']['status'] == 'FAIL'
    current['breadth'] -= F(1, 10**12)
    assert te.evaluate_day(current, history=history[:-1])['high_risk']['status'] == 'UNKNOWN'
    history[-20]['share'] = None
    assert te.evaluate_day(current, history=history)['high_risk']['flag'] is None


def test_quantile_and_invalid_exact_values_do_not_use_float_display():
    assert te.quantile([F(n) for n in range(60)], F(9, 10)) == F(531, 10)
    for value in (True, False, 'NaN', 'inf', '-inf', {'numerator': 1, 'denominator': 0}):
        assert te.exact(value) is None


def test_five_thousand_digit_fraction_roundtrip_hashes_without_global_limit_change():
    limit = sys.get_int_max_str_digits()
    huge = 10 ** 5000
    values = [F(huge + 1, huge - 1), -F(huge + 1, huge - 1), F(1, huge - 1)]
    for value in values:
        payload = te.encoded(value)
        assert isinstance(payload['denominator'], str)
        if abs(value.numerator).bit_length() > te.INTEGER_JSON_SAFE_BITS:
            assert isinstance(payload['numerator'], str)
        restored = json.loads(json.dumps(payload, allow_nan=False))
        assert te.exact(restored) == value
        assert digest_json_stream(payload) == digest_json_stream(restored)
    assert sys.get_int_max_str_digits() == limit
    assert te.encoded(F(3, 20)) == {'numerator': 3, 'denominator': 20}
    assert te.exact(te.encoded(F(3, 20))) == F(3, 20)
    for invalid in ('NaN', '1e5000', '1.0', '+1', '01', ' 1', '-01'):
        assert te.exact({'numerator': invalid, 'denominator': '2'}) is None
    assert te.exact({'numerator': '1', 'denominator': '0'}) is None


def test_run_local_return_cache_reuses_values_and_preserves_complete_audit_dependencies(monkeypatch):
    bundle, cutoff, pack = fixture_market()
    original, calls = te._return1, []
    def counted(*args, **kwargs):
        calls.append(args[:3])
        return original(*args, **kwargs)
    monkeypatch.setattr(te, '_return1', counted)
    cached = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
                                   _market_pack_validator=fixture_capability, output_history=True)
    cached_calls = len(calls)
    assert cached_calls == 67 * len(IDS)
    def uncached_reader(bars, resolver, markets):
        return lambda iid, day, prior: te._return1(iid, day, prior, bars, resolver, market=markets.get(day))
    monkeypatch.setattr(te, '_return_reader', uncached_reader)
    uncached = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
                                     _market_pack_validator=fixture_capability, output_history=True)
    assert len(calls) - cached_calls == 67 * (len(IDS) + 2)
    assert uncached == cached
    assert uncached['effective_input_hash'] == cached['effective_input_hash']
    assert uncached['source_refs'] == cached['source_refs']


def test_market_compound_cache_shares_windows_without_changing_any_result_or_audit_hash(monkeypatch):
    bundle, cutoff, pack = fixture_market()
    second = tuple(row.model_copy(update={'theme_id': 'TEST_FIXTURE_THEME_TWO', 'name': 'TEST_FIXTURE_TWO'})
                   for row in bundle.themes)
    bundle = bundle.model_copy(update={'themes': (*bundle.themes, *second)})
    pack['source_input_hash'] = digest_market_input(bundle)
    original, counts = te._compound, {'market_return_1d': 0, 'theme_return_1d': 0}
    def counted(rows, key, count):
        counts[key] += 1
        return original(rows, key, count)
    monkeypatch.setattr(te, '_compound', counted)
    cached = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
                                   _market_pack_validator=fixture_capability, output_history=True)
    assert counts == {'market_return_1d': 67 * 2, 'theme_return_1d': 67 * 2 * 2}
    counts.update(market_return_1d=0, theme_return_1d=0)
    monkeypatch.setattr(te, '_market_compound_reader',
                        lambda: lambda rows, count: te._compound(rows, 'market_return_1d', count))
    uncached = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
                                     _market_pack_validator=fixture_capability, output_history=True)
    assert counts == {'market_return_1d': 67 * 2 * 2, 'theme_return_1d': 67 * 2 * 2}
    assert uncached == cached
    assert uncached['items'] == cached['items']
    assert uncached['context_content_hash'] == cached['context_content_hash']
    assert uncached['effective_input_hash'] == cached['effective_input_hash']


def test_market_compound_cache_preserves_warmup_basis_unknown_and_run_isolation():
    rows = [{'session': str(index), 'price_return_basis': 'TEST_FIXTURE_A', 'market_return_1d': F(1, 100)}
            for index in range(4)]
    read = te._market_compound_reader()
    assert read(rows[:2], 3) is None
    assert read(rows[:3], 3) == F(101, 100) ** 3 - 1
    changed_basis = [*rows[:3], {**rows[-1], 'price_return_basis': 'TEST_FIXTURE_B'}]
    assert read(changed_basis, 3) is None
    other_run = [{**row, 'market_return_1d': F(1, 10)} for row in rows]
    assert te._market_compound_reader()(other_run[:3], 3) == F(11, 10) ** 3 - 1


def test_pack_capability_and_input_binding_required_not_a_verified_flag():
    bundle, cutoff, pack = fixture_market()
    pack['verified'] = True
    with pytest.raises(ValueError, match='VALIDATOR_CAPABILITY_REQUIRED'):
        te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack)
    pack.pop('verified')
    pack['source_input_hash'] = '0' * 64
    with pytest.raises(ValueError, match='INPUT_BINDING_MISMATCH'):
        te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack, _market_pack_validator=fixture_capability)


def test_dated_pack_recomputes_amount_all_boards_exact_concentration_and_preserves_input():
    bundle, cutoff, pack = fixture_market()
    before = digest_market_input(bundle)
    result = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack, _market_pack_validator=fixture_capability)
    today = result['items'][-1]
    assert digest_market_input(bundle) == before
    assert len(result['items']) == 2
    assert today['stage'] == 'STRENGTHENING'
    assert te.exact(today['market_amount_cny']) == 200
    assert te.exact(today['theme_amount_cny']) == 60
    assert te.exact(today['share']) == F(3, 10)
    assert te.exact(today['expansion']) == 3
    assert te.exact(today['concentration_adjusted_hhi']) == 0
    assert today['historical_membership_verified'] is True
    assert result['formal_focus'] is False and result['core_pass'] is False
    assert result['official_pit_validation_countable'] is False


@pytest.mark.parametrize('kind', ['late', 'partial', 'missing_star', 'missing_amount', 'conflicting_price'])
def test_source_gaps_do_not_mint_complete_market_or_theme_stage(kind):
    bundle, cutoff, pack = fixture_market()
    panel = pack['sessions'][-1]
    if kind == 'late':
        receipt = pack['receipts'][-1]
        receipt['first_seen_at'] = (cutoff + timedelta(minutes=1)).isoformat()
        receipt['ingested_at'] = receipt['first_seen_at']
    elif kind == 'partial':
        pack['receipts'][-1]['status'] = 'PARTIAL'
    elif kind == 'missing_star':
        panel['rows'] = [row for row in panel['rows'] if row['instrument_id'] != IDS[2]]
    elif kind == 'missing_amount':
        panel['rows'][0]['amount_cny'] = None
    else:
        other = deepcopy(panel['rows'][0])
        other['comparable_close'] = '999'
        panel['rows'].append(other)
    result = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack, _market_pack_validator=fixture_capability)
    today = result['items'][-1]
    assert today['stage'] == 'UNKNOWN'
    assert today['market_amount_cny'] is None
    assert today['share'] is None


def test_current_members_do_not_backfill_dated_history_or_twenty_day_expansion():
    bundle, cutoff, pack = fixture_market()
    bundle = bundle.model_copy(update={'themes': (bundle.themes[-1],)})
    pack['source_input_hash'] = digest_market_input(bundle)
    result = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack, _market_pack_validator=fixture_capability)
    assert result['items'][-2]['historical_membership_verified'] is False
    assert result['items'][-1]['expansion'] is None
    assert result['items'][-1]['stage'] == 'UNKNOWN'


def test_explicit_current_cohort_backview_visible_after_price_day_has_separate_role():
    bundle, cutoff, pack = fixture_market()
    source = deepcopy(pack['receipts'][-1])
    next_day = bundle.observation_date + timedelta(days=1)
    capture = cutoff + timedelta(days=1)
    source.update(receipt_id='TEST_FIXTURE_FUTURE_COHORT', business_sessions=[str(next_day)],
        event_time=capture.isoformat(), source_published_at=capture.isoformat(),
        first_seen_at=capture.isoformat(), ingested_at=capture.isoformat())
    theme = bundle.themes[-1].model_dump(mode='json')
    theme.update(effective_from=str(next_day), effective_to=None,
                 source_refs=[source['receipt_id']], **{key: source[key] for key in te.Clocked.model_fields})
    bundle = bundle.model_copy(update={'themes': (te.ThemeSnapshot.model_validate(theme),)})
    new_cutoff = cutoff + timedelta(days=3)
    pack.update(source_input_hash=digest_market_input(bundle), decision_cutoff=new_cutoff.isoformat(),
                receipts=[*pack['receipts'], source])
    strict = te.run_theme_evolution(bundle, cutoff=new_cutoff, market_pack=pack, _market_pack_validator=fixture_capability)
    assert strict['items'][-1]['stage'] == 'UNKNOWN'
    backview = te.run_theme_evolution(bundle, cutoff=new_cutoff, market_pack=pack,
        _market_pack_validator=fixture_capability, membership_mode='CURRENT_COHORT_BACKVIEW')
    assert backview['items'][-1]['stage'] == 'STRENGTHENING'
    assert backview['items'][-1]['historical_membership_verified'] is False
    assert backview['data_role'] == 'CURRENT_COHORT_BACKVIEW_ASOF_DECISION'
    assert backview['items'][-1]['cohort_effective_from'] == str(next_day)
    assert backview['official_pit_validation_countable'] is False
    early = te.run_theme_evolution(bundle, cutoff=cutoff, membership_mode='CURRENT_COHORT_BACKVIEW')
    assert early['items'][-1]['cohort_captured_at'] is None


def test_no_pack_does_not_read_store_or_create_market_denominator():
    bundle, cutoff, _ = fixture_market()
    result = te.run_theme_evolution(bundle, cutoff=cutoff)
    assert result['market_pack_hash'] is None
    assert all(row['market_amount_cny'] is None for row in result['items'])
    assert all(row['stage'] == 'UNKNOWN' for row in result['items'])


def test_current_cohort_backview_requires_actual_capture_even_with_reconstruction_allowance():
    bundle, cutoff, _ = fixture_market()
    late = bundle.themes[-1].model_copy(update={
        'first_seen_at': cutoff + timedelta(minutes=1),
        'ingested_at': cutoff + timedelta(minutes=1)})
    bundle = bundle.model_copy(update={'themes': (late,), 'coverage_type': 'RECONSTRUCTED_HISTORICAL'})
    # The source parent is captured before cutoff, so the ordinary resolver can
    # allow late normalization. Explicit current-cohort capture still must not.
    resolver = te.EvidenceResolver(bundle.receipts, cutoff=cutoff,
        coverage_type=bundle.coverage_type, sessions=bundle.sessions, observation_date=bundle.observation_date)
    assert resolver.resolve(late, session=late.effective_from, require_complete=True).allowed
    result = te.run_theme_evolution(bundle, cutoff=cutoff, membership_mode='CURRENT_COHORT_BACKVIEW')
    assert result['items'][-1]['cohort_captured_at'] is None
    assert result['items'][-1]['historical_membership_verified'] is False


def test_history_output_only_changes_display_scope_and_hash_not_daily_state():
    bundle, cutoff, pack = fixture_market()
    short = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
                                  _market_pack_validator=fixture_capability)
    history = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
        _market_pack_validator=fixture_capability, output_history=True)
    assert len(history['items']) == 67
    assert history['items'][-2:] == short['items']
    assert history['context_content_hash'] != short['context_content_hash']
    assert history['requested_output_scope'] == 'ALL_BOUNDED_HISTORY'
    assert history['items'][0]['expansion'] is None
    assert history['items'][0]['high_risk']['status'] == 'UNKNOWN'
    assert history['official_pit_validation_countable'] is False


def test_research_all_scope_equals_default_when_input_has_exactly_sixty_seven_sessions():
    bundle, cutoff, pack = fixture_market()
    default = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
        _market_pack_validator=fixture_capability, output_history=True)
    research = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
        _market_pack_validator=fixture_capability, output_history=True,
        history_scope='ALL_OBSERVED_SESSIONS_RESEARCH')
    assert research['items'] == default['items']
    assert research['computed_exchange_sessions'] == default['computed_exchange_sessions']
    assert default['requested_history_scope'] == 'LATEST_67'
    assert research['analysis_role'] == research['selection_role'] == 'RETROSPECTIVE_CONTEXT_ANALYSIS'
    assert research['context_content_hash'] != default['context_content_hash']
    assert research['official_pit_validation_countable'] is False


def test_research_all_scope_retains_earlier_observed_sessions_and_never_future():
    bundle, cutoff, pack = fixture_market(session_count=70)
    future = bundle.observation_date + timedelta(days=1)
    bundle = bundle.model_copy(update={'sessions': (*bundle.sessions, future)})
    pack['source_input_hash'] = digest_market_input(bundle)
    default = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
        _market_pack_validator=fixture_capability, output_history=True)
    research = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
        _market_pack_validator=fixture_capability, output_history=True,
        history_scope='ALL_OBSERVED_SESSIONS_RESEARCH')
    assert len(default['items']) == 67 and len(research['items']) == 70
    assert research['computed_exchange_sessions'][0] == str(bundle.sessions[0])
    assert default['computed_exchange_sessions'][0] == str(bundle.sessions[3])
    assert str(future) not in research['computed_exchange_sessions']
    assert research['items'][-2:] == default['items'][-2:]
    with pytest.raises(ValueError, match='HISTORY_SCOPE_UNSUPPORTED'):
        te.run_theme_evolution(bundle, cutoff=cutoff, history_scope='BEST_RETURN_WINDOW')
    with pytest.raises(ValueError, match='FUTURE_OBSERVATION_DATE'):
        te.run_theme_evolution(bundle, cutoff=cutoff - timedelta(days=1),
                              history_scope='ALL_OBSERVED_SESSIONS_RESEARCH')


def test_current_cohort_exposes_dated_absence_without_backfilling_or_zero_imputation():
    bundle, cutoff, pack = fixture_market()
    new_member = '600999.SH'
    theme = bundle.themes[-1].model_copy(update={'member_ids': (*IDS[:2], new_member)})
    bundle = bundle.model_copy(update={'themes': (theme,)})
    pack['source_input_hash'] = digest_market_input(bundle)
    # TEST_FIXTURE current listing is authenticated only on the last day. All
    # preceding dated masters have complete expected scopes without this id.
    panel = pack['sessions'][-1]
    new_bar = deepcopy(panel['rows'][-1])
    new_bar.update(instrument_id=new_member, amount_cny='10')
    panel['rows'].append(new_bar)
    panel['expected_instrument_ids'] = [*IDS, new_member]
    pack['receipts'][-1]['instrument_ids'] = [*IDS, new_member]
    pack['receipts'][-1]['row_count'] = 5
    bundle = bundle.model_copy(update={'receipts': (*bundle.receipts[:-1],
        te.SourceReceipt.model_validate(pack['receipts'][-1]))})
    pack['source_input_hash'] = digest_market_input(bundle)
    result = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
        _market_pack_validator=fixture_capability, membership_mode='CURRENT_COHORT_BACKVIEW')
    yesterday, today = result['items']
    assert yesterday['member_ids'] == today['member_ids'] == sorted((*IDS[:2], new_member))
    assert yesterday['membership_count'] == 3
    assert yesterday['eligible_membership_count'] == yesterday['active_membership_count'] == 2
    assert yesterday['absent_from_dated_market_ids'] == [new_member]
    assert yesterday['absent_from_dated_market_count'] == 1
    assert te.exact(yesterday['theme_amount_cny']) == 60
    assert today['eligible_membership_count'] == 3
    assert today['absent_from_dated_market_count'] == 0
    assert te.exact(today['theme_amount_cny']) == 70
    assert yesterday['membership_scope'] == 'CURRENT_COHORT_WITH_DATED_ELIGIBILITY'
    assert yesterday['historical_membership_verified'] is False


@pytest.mark.parametrize('unsupported', ['830001.BJ', '200001.SZ', 'UNKNOWN'])
def test_current_cohort_cannot_hide_bse_b_share_or_unknown_identity(unsupported):
    bundle, cutoff, pack = fixture_market()
    theme = bundle.themes[-1].model_copy(update={'member_ids': (*IDS[:2], unsupported)})
    bundle = bundle.model_copy(update={'themes': (theme,)})
    pack['source_input_hash'] = digest_market_input(bundle)
    result = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
        _market_pack_validator=fixture_capability, membership_mode='CURRENT_COHORT_BACKVIEW')
    today = result['items'][-1]
    assert today['dated_eligibility_verified'] is False
    assert today['absent_from_dated_market_count'] is None
    assert today['theme_amount_cny'] is None
    assert unsupported in today['member_ids']
    assert 'THEME_CURRENT_COHORT_SECURITY_SCOPE_UNVERIFIED' in today['reason_codes']
    assert today['stage'] == 'UNKNOWN'


def test_dated_only_outside_full_scope_still_unknown_without_cohort_exclusion():
    bundle, cutoff, pack = fixture_market()
    theme = bundle.themes[-1].model_copy(update={'member_ids': (*IDS[:2], '600999.SH')})
    bundle = bundle.model_copy(update={'themes': (theme,)})
    pack['source_input_hash'] = digest_market_input(bundle)
    result = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
                                   _market_pack_validator=fixture_capability)
    today = result['items'][-1]
    assert today['theme_amount_cny'] is None
    assert 'THEME_MEMBER_OUTSIDE_VERIFIED_A_COMMON_SCOPE' in today['reason_codes']
    assert today['dated_eligibility_verified'] is False
    assert today['stage'] == 'UNKNOWN'


def fixture_reference_chain():
    bundle, cutoff, pack = fixture_market()
    model = bundle.model_dump(mode='json')
    for row in model['bars']:
        row.update(comparable_close=None, comparable_basis=None)
    bundle = MarketInput.model_validate(model)
    pack['source_input_hash'] = digest_market_input(bundle)
    for panel in pack['sessions']:
        panel['return_basis'] = te.CONFIG['reference_return_basis']
        for row in panel['rows']:
            factor = Decimal('1.01') if row['instrument_id'] in IDS[:2] else Decimal('1')
            row.update(comparable_close=None, comparable_basis=None,
                       reference_prev_close=str(Decimal(row['close']) / factor))
    return bundle, cutoff, pack


def test_explicit_authenticated_reference_chain_without_comparable_price_has_separate_role():
    bundle, cutoff, pack = fixture_reference_chain()
    before = digest_market_input(bundle)
    result = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
                                   _market_pack_validator=fixture_capability)
    today = result['items'][-1]
    assert today['stage'] == 'STRENGTHENING'
    assert te.exact(today['theme_return_1d']) == pytest.approx(.01)
    assert today['return_basis'] == 'SOURCE_DAILY_REFERENCE_CHAIN_EQUAL_WEIGHT'
    assert today['price_return_basis'] == 'BAOSTOCK_REFERENCE_RETURN_CHAIN_V1'
    assert digest_market_input(bundle) == before
    assert all(row.comparable_close is None for row in bundle.bars)


@pytest.mark.parametrize('basis', [None, 'UNRECOGNIZED_REFERENCE_CHAIN'])
def test_missing_or_unrecognized_reference_basis_cannot_silently_use_raw(basis):
    bundle, cutoff, pack = fixture_reference_chain()
    for panel in pack['sessions']:
        panel['return_basis'] = basis
    result = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
                                   _market_pack_validator=fixture_capability)
    today = result['items'][-1]
    assert te.exact(today['market_amount_cny']) == 200
    assert today['theme_return_1d'] is None and today['rs3'] is None and today['rs5'] is None
    assert today['stage'] == 'UNKNOWN'


def test_mixed_reference_and_comparable_basis_window_is_unknown_despite_legal_daily_returns():
    bundle, cutoff, pack = fixture_market()
    pack['sessions'][-1]['return_basis'] = te.CONFIG['reference_return_basis']
    result = te.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
                                   _market_pack_validator=fixture_capability)
    yesterday, today = result['items']
    assert yesterday['rs3'] is not None
    assert today['theme_return_1d'] is not None and today['market_return_1d'] is not None
    assert te.exact(today['share']) == F(3, 10)
    assert today['rs3'] is None and today['rs5'] is None
    assert today['stage'] == 'UNKNOWN'


def test_association_removes_shared_members_and_market_does_not_use_share_closure():
    rows = []
    for index in range(4):
        market, residual = F(index, 100), F(index * index, 100)
        rows.append({'session': str(index), 'market_return': market,
            'left_member_returns': {'left': market + residual, 'overlap': F(999)},
            'right_member_returns': {'right': market - residual, 'overlap': F(999)},
            'left_share': F(1, 2), 'right_share': F(1, 2)})
    result = te.describe_theme_association(rows)
    assert result['market_adjusted_ex_overlap_return_correlation'] == pytest.approx(-1)
    assert result['overlapping_member_observations_removed'] == 4
    assert result['share_closure_used'] is False
    assert result['causal_interpretation'] == 'NOT_ESTABLISHED'
    assert te.describe_theme_association(rows[:2])['status'] == 'UNKNOWN'
    assert te.describe_theme_association([rows[0], rows[0]])['status'] == 'UNKNOWN'
