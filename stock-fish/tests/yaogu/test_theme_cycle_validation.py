"""Isolated TEST_FIXTURE mathematics; no claim about any actual market year."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction as F
import hashlib
import json

import pytest

from stockfish_core.yaogu import theme_cycle_validation as validation
from stockfish_core.yaogu import theme_evolution as theme
from stockfish_core.yaogu.contracts import MarketInput, SourceReceipt, digest
from stockfish_core.yaogu.history_artifacts import digest_json_stream
from stockfish_core.yaogu.research_context_artifacts import SourceArtifactReader, RETURN_BASIS
from tests.support.short_horizon_strength_fixture import payload, strength_bundle


def _text(value):
    value = F(value)
    return str(Decimal(value.numerator) / Decimal(value.denominator))


@pytest.fixture(scope='module')
def authentic_panel(tmp_path_factory):
    folder = tmp_path_factory.mktemp('TEST_FIXTURE_THEME_CYCLE')
    base, _ = strength_bundle()
    value = payload(base)
    sessions = list(base.sessions)
    while len(sessions) < 110:
        day = sessions[-1] + timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        sessions.append(day)
    capture = base.bars[-1].event_time.replace(year=sessions[-1].year,
        month=sessions[-1].month, day=sessions[-1].day)
    cutoff = capture + timedelta(hours=5)
    value.update(observation_date=sessions[-1], sessions=sessions, bars=[], receipts=[], themes=[])
    value['universe'].update(securities=[], complete=False, expected_count=0, source_refs=[])
    bundle = MarketInput.model_validate(value)
    codes = ['sh.600001', 'sh.688001', 'sz.000001', 'sz.300001']
    receipts, paths = [], {}

    def source(dataset, session, rows, symbol=None):
        rid = 'TEST_FIXTURE_' + dataset + '_' + (symbol or str(session))
        field = 'BAOSTOCK_00.9.30_R03_V1' if dataset != 'concept_members' else 'TEST_FIXTURE_THEME_MEMBERS_V1'
        data = {'status': 'COMPLETE', 'pagination_complete': True, 'field_version': field,
                'first_seen_at': capture.isoformat(), 'rows': rows}
        raw = json.dumps(data, sort_keys=True).encode()
        filename = folder / (rid + '.json')
        filename.write_bytes(raw)
        clock = capture.replace(year=session.year, month=session.month, day=session.day)
        receipts.append(SourceReceipt.model_validate(dict(receipt_id=rid, provider='baostock', dataset=dataset,
            content_hash=hashlib.sha256(raw).hexdigest(), field_version=field, row_count=len(rows),
            status='COMPLETE', pagination_complete=True,
            parameters={'date': str(session), **({'symbol': symbol} if symbol else {})},
            business_sessions=[session], event_time=clock, source_published_at=capture,
            first_seen_at=capture, ingested_at=capture)))
        paths[rid] = filename
        return rid

    for index, session in enumerate(sessions):
        m = 1000000000 + index * 1000000 + (index % 7) * 10000000
        noise = ((index * 3) % 11) * 10000000
        amounts = [100000000 + 2 * (m - 1000000000) + noise,
                   50000000, 150000000 + 3 * (m - 1000000000) + 2 * noise, m]
        market_return = F(index % 7 - 3, 1000)
        residual = F((index * 3) % 11 - 5, 1000)
        returns = [F(5, 1000) + 2 * market_return + residual,
                   F(1, 1000), F(7, 1000) + 3 * market_return + 2 * residual, market_return]
        source('master_all', session, [{'code': code, 'code_name': 'TEST_FIXTURE', 'tradeStatus': '1'} for code in codes])
        source('market_daily', session, [{'code': code, 'date': str(session), 'adjustflag': '3',
            'tradestatus': '1', 'amount': str(amount), 'volume': '1000', 'open': '10', 'high': '11',
            'low': '9', 'close': _text(10 * (1 + ret)), 'preclose': '10'}
            for code, amount, ret in zip(codes, amounts, returns)])
    snapshots = []
    for name, members in [('TEST_THEME_A', ['600001', '688001']), ('TEST_THEME_B', ['000001', '688001'])]:
        rid = source('concept_members', sessions[-1], [{'代码': code} for code in members], name)
        snapshots.append(dict(theme_id=name, name=name, theme_type='EVENT',
            member_ids=[code + ('.SH' if code.startswith(('60', '68')) else '.SZ') for code in members],
            effective_from=sessions[-1], definition_version='TEST_FIXTURE_EXACT_MEMBERS', source_refs=[rid],
            event_time=capture, source_published_at=capture, first_seen_at=capture, ingested_at=capture))
    reader = SourceArtifactReader(receipts, paths)
    pack = reader.build_market_pack(bundle, cutoff=cutoff, sessions=sessions,
        themes=snapshots, return_basis=RETURN_BASIS)
    result = theme.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
        _market_pack_validator=reader.validate_theme_market_pack, membership_mode='CURRENT_COHORT_BACKVIEW',
        output_history=True, history_scope='ALL_OBSERVED_SESSIONS_RESEARCH')
    registry = [{'cycle_id': 'TEST_CYCLE_' + str(index), 'start_session': str(sessions[start]),
        'end_session': str(sessions[start + 9]), 'registered_at': cutoff.isoformat(),
        'interval_kind': 'MARKET_EMOTION_CYCLE', 'emotion_label': label,
        'classification_basis': 'TEST_FIXTURE_INDEPENDENT_EMOTION_CALENDAR'}
        for index, (start, label) in enumerate([(70, 'UP'), (80, 'DIVERGENCE'), (90, 'REPAIR')])]
    return bundle, cutoff, reader, pack, result, registry


def _cycle_verifier(registry, **kwargs):
    assert all(row['classification_basis'] == 'TEST_FIXTURE_INDEPENDENT_EMOTION_CALENDAR' for row in registry)


def _run(data, registry=None, verifier=_cycle_verifier, result=None, pack=None):
    bundle, cutoff, reader, source, parent, recorded = data
    return validation.run_theme_cycle_validation(bundle, parent if result is None else result, cutoff=cutoff,
        market_pack=source if pack is None else pack, _market_pack_validator=reader.validate_theme_market_pack,
        cycle_registry=recorded if registry is None else registry, analysis_at=cutoff + timedelta(minutes=1),
        _cycle_registry_validator=verifier, theme_pairs=[['TEST_THEME_A', 'TEST_THEME_B']],
        pair_scope_provenance={'scope_basis': 'TEST_FIXTURE_INDEPENDENT_RESEARCH_PAIR_SCOPE'})


def test_exact_prior60_ols_holds_current_out_and_does_not_leak_future():
    prior = [(F(x), F(2 * x + 3)) for x in range(60)]
    result = validation._ols_residual(prior, (F(60), F(133)))
    assert result['status'] == 'AVAILABLE'
    assert result['beta'] == 2 and result['intercept'] == 3 and result['residual'] == 10
    assert result['current_in_training'] is False
    assert validation._ols_residual(prior, (F(60), F(123)))['residual'] == 0
    assert prior == [(F(x), F(2 * x + 3)) for x in range(60)]


def test_source_identity_binds_actual_numeric_and_authentication_implementations():
    identity = validation.source_identity()
    paths = identity['source_file_hashes']
    assert {'stockfish_core/yaogu/theme_cycle_validation.py', 'stockfish_core/yaogu/theme_evolution.py',
            'stockfish_core/yaogu/research_context_artifacts.py',
            'stockfish_core/yaogu/short_horizon_strength.py',
            'stockfish_core/yaogu/short_horizon_participation.py',
            'stockfish_core/yaogu/visibility.py', 'stockfish_core/yaogu/contracts.py'}.issubset(paths)
    assert identity['source_hash'] == digest(paths)
    assert identity['parameters_hash'] == digest(validation.PARAMETERS)
    assert identity['parameters']['parameter_status'] == 'INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED'


def test_current_cohort_late_capture_cannot_use_historical_reconstruction_permission():
    from datetime import datetime, date, timezone
    from types import SimpleNamespace
    from stockfish_core.yaogu.contracts import ThemeSnapshot
    from stockfish_core.yaogu.visibility import EvidenceResolver
    day = date(2026, 2, 2)
    early = datetime(2026, 2, 2, 7, tzinfo=timezone.utc)
    cutoff, late = early + timedelta(hours=1), early + timedelta(hours=2)
    receipt = SourceReceipt.model_validate(dict(receipt_id='TEST_FIXTURE_LATE_COHORT', provider='TEST_FIXTURE',
        dataset='concept_members', content_hash='0' * 64, field_version='TEST_FIXTURE', row_count=1,
        status='COMPLETE', pagination_complete=True, event_time=early, source_published_at=early,
        first_seen_at=late, ingested_at=late, historical_time_proof=dict(policy_id='TEST_FIXTURE_PROOF',
            source_url='https://example.invalid/test-fixture', evidence_hash='0' * 64,
            event_time_basis='SOURCE_DATED_EVENT_RECORD', publication_time_basis='SOURCE_PUBLICATION_TIMESTAMP',
            applies_from=day, applies_to=day, evidence_refs=['TEST_FIXTURE_PROOF_RECORD'], verified_at=late)))
    snapshot = ThemeSnapshot.model_validate(dict(theme_id='TEST_LATE', name='TEST_LATE', theme_type='EVENT',
        member_ids=['600001.SH'], effective_from=day, definition_version='TEST_FIXTURE', source_refs=[receipt.receipt_id],
        event_time=early, source_published_at=early, first_seen_at=late, ingested_at=late))
    resolver = EvidenceResolver([receipt], cutoff=cutoff, coverage_type='RECONSTRUCTED_HISTORICAL',
        sessions=[day], observation_date=day)
    assert resolver.resolve(snapshot, session=day, require_complete=True).allowed is True
    parent = {'membership_mode': 'CURRENT_COHORT_BACKVIEW', 'computed_exchange_sessions': [str(day)],
        'items': [{'theme_id': 'TEST_LATE', 'session': str(day), 'member_ids': [], 'membership_count': 0,
            'membership_mode': 'CURRENT_COHORT_BACKVIEW', 'historical_membership_verified': False,
            'dated_eligibility_verified': False, 'eligible_member_ids': [], 'absent_from_dated_market_ids': None}]}
    rows = validation._validated_rows(SimpleNamespace(themes=(snapshot,)), cutoff,
        SimpleNamespace(themes=()), parent, resolver,
        {str(day): {'status': 'COMPLETE', 'expected_ids': {'600001.SH'}}})
    assert rows[('TEST_LATE', str(day))]['member_ids'] == []


@pytest.mark.parametrize('kind', ['short', 'missing', 'constant', 'unknown_current'])
def test_ols_missing_or_constant_evidence_is_unknown_not_zero(kind):
    prior, current = [(F(x), F(2 * x + 3)) for x in range(60)], (F(60), F(123))
    if kind == 'short':
        prior.pop()
    elif kind == 'missing':
        prior[12] = (None, F(27))
    elif kind == 'constant':
        prior = [(F(1), F(x)) for x in range(60)]
    else:
        current = (None, F(123))
    result = validation._ols_residual(prior, current)
    assert result['status'] == 'UNKNOWN' and result['residual'] is None


def test_native_full_panel_exclusive_members_market_complement_and_three_independent_cycles(authentic_panel):
    before = deepcopy(authentic_panel[4])
    result = _run(authentic_panel)
    assert authentic_panel[4] == before
    assert result['status'] == 'DESCRIPTIVE_COMPARISON_AVAILABLE'
    assert result['data_role'] == 'CURRENT_COHORT_RETROSPECTIVE'
    assert result['official_pit_validation_count'] == 0
    assert result['formal_focus'] is result['core_pass'] is False
    assert result['market_edge'] == result['causal_interpretation'] == result['statistical_stability'] == 'NOT_ESTABLISHED'
    pair = result['pairs'][0]
    assert pair['valid_authenticated_emotion_cycle_count'] == 3
    for interval in pair['intervals']:
        assert interval['expected_exchange_session_count'] == interval['observed_exchange_session_count'] == 10
        for kind in ('amount', 'price'):
            correlation = interval['statistics']['exclusive_' + kind + '_market_residual_correlation']
            assert correlation['status'] == 'AVAILABLE' and correlation['value'] == pytest.approx(1)
    row = pair['observations'][-1]
    assert row['left_exclusive_member_ids'] == ['600001.SH']
    assert row['right_exclusive_member_ids'] == ['000001.SZ']
    assert row['overlap_member_ids'] == ['688001.SH']
    assert row['market_complement_membership_hash'] == digest(['300001.SZ'])
    assert row['market_complement_member_count'] == 1
    assert 'market_complement_member_ids' not in row
    assert row['session'] not in row['training_sessions'] and len(row['training_sessions']) == 60
    assert pair['price_association_does_not_identify_fund_flow'] is True
    assert pair['share_closure_used_for_relationship_conclusion'] is False


def test_calendar_or_unverified_emotion_intervals_never_count_as_authenticated_emotion_cycles(authentic_panel):
    calendar = deepcopy(authentic_panel[-1])
    for interval in calendar:
        interval['interval_kind'] = 'CALENDAR_WINDOW'
    result = _run(authentic_panel, registry=calendar)
    assert result['status'] == 'UNKNOWN'
    assert result['pairs'][0]['valid_authenticated_emotion_cycle_count'] == 0
    assert all(row['statistics_coverage_status'] == 'AVAILABLE' for row in result['pairs'][0]['intervals'])
    assert result['pairs'][0]['same_sign_across_observed_cycles'] == {'amount': None, 'price': None}
    unverified = _run(authentic_panel, verifier=None)
    assert unverified['status'] == 'UNKNOWN'
    assert unverified['pairs'][0]['valid_authenticated_emotion_cycle_count'] == 0


def test_fewer_than_three_valid_complete_cycles_stays_unknown(authentic_panel):
    result = _run(authentic_panel, registry=authentic_panel[-1][:2])
    assert result['status'] == 'UNKNOWN'
    assert result['pairs'][0]['valid_authenticated_emotion_cycle_count'] == 2


def test_explicit_current_selected_pair_scope_is_bound_and_discloses_survivorship_bias(authentic_panel):
    bundle, cutoff, reader, pack, parent, registry = authentic_panel
    scope = {'scope_basis': 'CURRENT_SELECTED_CANDIDATES_AND_ACTIVE_THEMES',
             'selected_run_id': 'TEST_FIXTURE_SAVED_RUN', 'selected_run_content_hash': '0' * 64}
    result = validation.run_theme_cycle_validation(bundle, parent, cutoff=cutoff, market_pack=pack,
        _market_pack_validator=reader.validate_theme_market_pack, cycle_registry=registry,
        analysis_at=cutoff + timedelta(minutes=1), _cycle_registry_validator=_cycle_verifier,
        theme_pairs=[['TEST_THEME_B', 'TEST_THEME_A']], pair_scope_provenance=scope)
    assert result['pair_scope'] == [['TEST_THEME_A', 'TEST_THEME_B']]
    assert result['pair_scope_provenance_hash'] == digest_json_stream(scope)
    assert 'SURVIVORSHIP_SELECTION_BIAS' in result['sample_selection_bias']
    assert result['historical_opportunity_set_claimed'] is False
    assert result['historical_mainline_eligibility_status'] == 'UNKNOWN'
    assert result['historical_mainline_eligibility_reason_codes'] == ['NO_AUTHENTICATED_DATED_MAINLINE_RULE_AND_QUALIFICATION_EVIDENCE']
    assert result['multiple_comparison_adjustment'] == 'NOT_PERFORMED_NO_SIGNIFICANCE_OR_EDGE_CLAIM'
    with pytest.raises(ValueError, match='PAIR_SCOPE_DUPLICATE'):
        validation.run_theme_cycle_validation(bundle, parent, cutoff=cutoff, market_pack=pack,
            _market_pack_validator=reader.validate_theme_market_pack,
            theme_pairs=[['TEST_THEME_A', 'TEST_THEME_B'], ['TEST_THEME_B', 'TEST_THEME_A']])


def test_explicit_primary_scope_allows_necessary_background_but_never_background_only_pairs(authentic_panel):
    bundle, cutoff, reader, pack, parent, _ = authentic_panel
    kwargs = dict(cutoff=cutoff, market_pack=pack, _market_pack_validator=reader.validate_theme_market_pack,
        theme_pairs=[['TEST_THEME_A', 'TEST_THEME_B']], analysis_at=cutoff + timedelta(minutes=1))
    scope = {'scope_basis': 'CURRENT_SELECTED_CANDIDATES_AND_ACTIVE_THEMES',
        'selected_run_id': 'TEST_FIXTURE_SAVED_RUN', 'background_reason': 'TEST_FIXTURE_ROTATION_CONTEXT'}
    result = validation.run_theme_cycle_validation(bundle, parent, **kwargs,
        eligible_theme_ids=['TEST_THEME_A'], background_theme_ids=['TEST_THEME_B'], pair_scope_provenance=scope)
    assert result['eligible_theme_ids'] == ['TEST_THEME_A']
    assert result['necessary_background_theme_ids'] == ['TEST_THEME_B']
    assert result['pairs'][0]['pair_display_role'] == 'PRIMARY_THEME_WITH_EXPLICIT_NECESSARY_ROTATION_BACKGROUND'
    assert result['pair_scope_provenance'] == scope
    assert result['historical_mainline_eligibility_status'] == 'UNKNOWN'
    with pytest.raises(ValueError, match='PAIR_OUTSIDE_ELIGIBLE_OR_NECESSARY_BACKGROUND_SCOPE'):
        validation.run_theme_cycle_validation(bundle, parent, **kwargs,
            eligible_theme_ids=[], background_theme_ids=['TEST_THEME_A', 'TEST_THEME_B'])
    with pytest.raises(ValueError, match='PAIR_OUTSIDE_ELIGIBLE_OR_NECESSARY_BACKGROUND_SCOPE'):
        validation.run_theme_cycle_validation(bundle, parent, **kwargs, eligible_theme_ids=['TEST_THEME_A'])


def test_parent_fsm_stage_self_hash_is_not_native_authentication(authentic_panel):
    bundle, cutoff, reader, pack, parent, _ = authentic_panel
    forged = deepcopy(parent)
    for row in forged['items']:
        row['stage'] = 'TEST_FIXTURE_FORGED_STAGE'
    forged['context_content_hash'] = digest_json_stream({k: v for k, v in forged.items() if k != 'context_content_hash'})
    result = _run(authentic_panel, result=forged)
    assert result['parent_fsm_authentication_status'] == 'UNKNOWN'
    assert all(row['left_fsm_stage'] is None and row['right_fsm_stage'] is None for row in result['pairs'][0]['observations'])
    # The producer/archive authenticator binds an external immutable authority,
    # rather than trusting a hash supplied by the same caller as the labels.
    def validate_native(value, **kwargs):
        return digest_json_stream(value) == digest_json_stream(parent)
    kwargs = dict(cutoff=cutoff, market_pack=pack, _market_pack_validator=reader.validate_theme_market_pack,
        theme_pairs=[['TEST_THEME_A', 'TEST_THEME_B']], analysis_at=cutoff + timedelta(minutes=1),
        _theme_result_validator=validate_native)
    with pytest.raises(ValueError, match='NATIVE_PARENT_AUTHENTICATION_FAILED'):
        validation.run_theme_cycle_validation(bundle, forged, **kwargs)
    authenticated = validation.run_theme_cycle_validation(bundle, parent, **kwargs)
    assert authenticated['parent_fsm_authentication_status'] == 'AUTHENTICATED_BY_NATIVE_CAPABILITY'
    last = authenticated['pairs'][0]['observations'][-1]
    assert last['left_fsm_stage'] == next(row['stage'] for row in parent['items']
        if row['theme_id'] == 'TEST_THEME_A' and row['session'] == last['session'])


def test_missing_explicit_pair_scope_never_launches_all_concept_combinations(authentic_panel):
    bundle, cutoff, reader, pack, parent, _ = authentic_panel
    result = validation.run_theme_cycle_validation(bundle, parent, cutoff=cutoff, market_pack=pack,
        _market_pack_validator=reader.validate_theme_market_pack, analysis_at=cutoff + timedelta(minutes=1))
    assert result['status'] == 'UNKNOWN' and result['pair_count'] == 0 and result['pairs'] == []
    assert result['reason_codes'] == ['NO_EXPLICIT_RESEARCH_PAIR_SCOPE_AVAILABLE']
    assert result['pair_scope_role'] == 'EXPLICIT_RESEARCH_PAIR_SCOPE_NOT_SUPPLIED'


@pytest.mark.parametrize('kind', ['overlap', 'late_registration', 'missing_basis', 'duplicate_id'])
def test_registry_rejects_postregistered_or_ambiguous_intervals(authentic_panel, kind):
    registry = deepcopy(authentic_panel[-1])
    if kind == 'overlap':
        registry[1]['start_session'] = registry[0]['end_session']
    elif kind == 'late_registration':
        registry[0]['registered_at'] = (authentic_panel[1] + timedelta(hours=1)).isoformat()
    elif kind == 'missing_basis':
        registry[0]['classification_basis'] = ''
    else:
        registry[1]['cycle_id'] = registry[0]['cycle_id']
    with pytest.raises(ValueError, match='CYCLE_REGISTRY'):
        _run(authentic_panel, registry=registry)


def test_partial_interval_scope_does_not_become_complete_cycle(authentic_panel):
    registry = deepcopy(authentic_panel[-1])
    registry[0]['start_session'] = str(authentic_panel[0].sessions[59])
    result = _run(authentic_panel, registry=registry)
    assert result['status'] == 'UNKNOWN'
    first = result['pairs'][0]['intervals'][0]
    assert first['expected_exchange_session_count'] == 21
    assert first['statistics_coverage_status'] == 'UNKNOWN'
    assert first['countable_emotion_cycle'] is False


@pytest.mark.parametrize('kind', ['parent_hash', 'membership', 'source_price'])
def test_source_and_parent_tampering_rejected_before_numeric_computation(authentic_panel, kind):
    result, pack = deepcopy(authentic_panel[4]), deepcopy(authentic_panel[3])
    if kind == 'parent_hash':
        result['context_content_hash'] = '0' * 64
    elif kind == 'membership':
        result['items'][-1]['member_ids'] = ['600001.SH']
        result['items'][-1]['membership_count'] = 1
        result['context_content_hash'] = digest_json_stream({k: v for k, v in result.items() if k != 'context_content_hash'})
    else:
        pack['sessions'][-1]['rows'][0]['close'] = '10.111'
    with pytest.raises(ValueError):
        _run(authentic_panel, result=result, pack=pack)


def test_closed_share_opposition_does_not_require_opposite_absolute_trading_value():
    sessions = ['2026-01-01', '2026-01-02', '2026-01-05']
    rows, markets, returns = {}, {}, {}
    for index, day in enumerate(sessions):
        rows[('A', day)] = {'member_ids': ['A'], 'source_refs': [], 'stage': 'TEST_FIXTURE'}
        rows[('B', day)] = {'member_ids': ['B'], 'source_refs': [], 'stage': 'TEST_FIXTURE'}
        amounts = {'A': F(100 + index * 100), 'B': F(200), 'M': F(1000 + index * 50)}
        markets[day] = {'status': 'COMPLETE', 'expected_ids': set(amounts), 'trading_ids': set(amounts),
            'amounts': amounts, 'amount_cny': sum(amounts.values()), 'return_basis': 'TEST_FIXTURE_SAME_BASIS'}
        returns[day] = {'A': F(index, 100), 'B': F(index, 200), 'M': F(index, 300)}
    daily = validation._daily_pair('A', 'B', sessions, rows, markets, returns)
    assert daily[1]['closed_share_opposite_without_absolute_amount_opposition'] is True
    assert daily[1]['right_amount_cny_change'] == 0
    assert daily[1]['left_amount_market_residual']['residual'] is None


def test_missing_market_scope_and_share_diagnostics_are_unknown_not_zero():
    days = ['2026-01-01', '2026-01-02']
    rows = {(name, day): {'member_ids': [name], 'source_refs': []} for name in ('A', 'B') for day in days}
    daily = validation._daily_pair('A', 'B', days, rows, {}, {})
    assert all(row['market_complement_membership_hash'] is None and row['market_complement_member_count'] is None for row in daily)
    interval = validation._intervals(daily, [{'cycle_id': 'TEST_FIXTURE', 'start_session': days[0],
        'end_session': days[-1], 'interval_kind': 'CALENDAR_WINDOW'}], False, days)[0]
    assert interval['share_closure_diagnostic_session_count'] is None
    assert interval['share_closure_diagnostic_verified_session_count'] == 0
    assert interval['share_closure_diagnostic_unknown_session_count'] == 2
    assert interval['excluded_sessions_by_axis']['exclusive_amount_market_residual_correlation'] == days
    assert interval['excluded_sessions_by_axis']['exclusive_price_market_residual_correlation'] == days


def test_initial_warmup_and_minimum_coverage_do_not_report_correlation_early(authentic_panel):
    result = _run(authentic_panel)
    observations = result['pairs'][0]['observations']
    assert all(row['left_amount_market_residual']['residual'] is None for row in observations[:61])
    minimum = validation.PARAMETERS['minimum_valid_test_sessions']
    assert validation._correlation([(F(x), F(-x)) for x in range(minimum - 1)], minimum)['value'] is None
    assert validation._correlation([(F(x), F(-x)) for x in range(minimum)], minimum)['value'] == -1


@pytest.mark.parametrize('kind', ['missing_day', 'membership_change', 'basis_change'])
def test_missing_day_or_changed_cohort_breaks_preceding_window_without_compression(kind):
    from datetime import date
    days = [str(date(2026, 1, 1) + timedelta(days=index)) for index in range(100)]
    rows, markets, returns = {}, {}, {}
    for index, day in enumerate(days):
        rows[('A', day)] = {'member_ids': ['A'], 'source_refs': [], 'stage': 'TEST_FIXTURE'}
        rows[('B', day)] = {'member_ids': ['B'], 'source_refs': [], 'stage': 'TEST_FIXTURE'}
        common, noise = F(1000 + index % 7 * 10), F(index % 11)
        amounts = {'A': 100 + 2 * common + noise, 'B': 200 + 3 * common + noise,
                   'X': F(50), 'M': common}
        markets[day] = {'status': 'COMPLETE', 'expected_ids': set(amounts), 'trading_ids': set(amounts),
            'amounts': amounts, 'amount_cny': sum(amounts.values()), 'return_basis': 'TEST_FIXTURE_SAME_BASIS'}
        returns[day] = {'A': F(index % 7, 100) + F(index % 11, 100),
                       'B': F(index % 7, 50) + F(index % 11, 100), 'X': F(0), 'M': F(index % 7, 100)}
    if kind == 'missing_day':
        del markets[days[85]]
    elif kind == 'membership_change':
        rows[('A', days[85])]['member_ids'].append('X')
    else:
        markets[days[85]]['return_basis'] = 'TEST_FIXTURE_DIFFERENT_BASIS'
    observations = validation._daily_pair('A', 'B', days, rows, markets, returns)
    assert observations[84]['left_amount_market_residual']['status'] == 'AVAILABLE'
    assert observations[85]['left_amount_market_residual']['residual'] is None
    assert observations[86]['left_amount_market_residual']['residual'] is None
    assert len(observations[86]['training_sessions']) == 60
    assert days[85] in observations[86]['training_sessions']


def test_new_market_identity_is_a_disclosed_dynamic_factor_not_missing_zero():
    from datetime import date
    days = [str(date(2026, 1, 1) + timedelta(days=index)) for index in range(100)]
    rows, markets, returns = {}, {}, {}
    for index, day in enumerate(days):
        rows[('A', day)] = {'member_ids': ['A'], 'source_refs': []}
        rows[('B', day)] = {'member_ids': ['B'], 'source_refs': []}
        m, noise = F(1000 + index % 7 * 10), F(index % 11)
        amounts = {'A': 100 + 2 * m + noise, 'B': 200 + 3 * m + noise, 'M': m}
        prices = {'A': F(index % 7 + index % 11, 100),
                  'B': F(2 * (index % 7) + index % 11, 100), 'M': F(index % 7, 100)}
        if index >= 85:
            amounts['NEW'] = F(70 + index)
            prices['NEW'] = F(1, 100)
        markets[day] = {'status': 'COMPLETE', 'expected_ids': set(amounts), 'trading_ids': set(amounts),
            'amounts': amounts, 'amount_cny': sum(amounts.values()), 'return_basis': 'TEST_FIXTURE_SAME_BASIS'}
        returns[day] = prices
    observations = validation._daily_pair('A', 'B', days, rows, markets, returns)
    assert observations[85]['market_complement_membership_changed'] is True
    assert observations[85]['market_complement_member_ids'] == ['M', 'NEW']
    assert observations[85]['left_amount_market_residual']['status'] == 'AVAILABLE'
    assert observations[85]['market_complement_amount_cny'] == markets[days[85]]['amounts']['M'] + F(155)
    assert observations[85]['market_complement_amount_cny_change'] == (
        observations[85]['market_complement_amount_cny'] - observations[84]['market_complement_amount_cny'])
