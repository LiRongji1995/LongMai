"""Native TEST_FIXTURE parents and frozen selection; no investment validation."""
from copy import deepcopy
from datetime import datetime, timedelta
from dataclasses import dataclass
from decimal import Decimal

import pytest

from stockfish_core.yaogu import short_horizon_strength as r1
from stockfish_core.yaogu import theme_evolution as theme
from stockfish_core.yaogu import theme_observation_diagnostics as diagnostics
from stockfish_core.yaogu.contracts import MarketInput, ThemeSnapshot, digest
from stockfish_core.yaogu.history_artifacts import digest_json_stream, digest_market_input
from tests.support.short_horizon_strength_fixture import (CONTINUATION, PULLBACK,
    START, WEAK, strength_bundle)

IDS = ('600001.SH', '000001.SZ', '688001.SH', '300001.SZ')
CONT_ID, WEAK_ID = IDS[1], IDS[3]


@dataclass
class NativeCase:
    bundle: MarketInput
    cutoff: datetime
    context: dict
    stock_parent: dict
    pack: dict

    def __post_init__(self):
        assert self.bundle.coverage_type == 'TEST_FIXTURE'
        self.input_hash = digest_market_input(self.bundle)
        self.context_hash = digest_json_stream(self.context)
        self.stock_parent_hash = digest_json_stream(self.stock_parent)
        self.selected_ids = tuple(sorted(self.stock_parent['research_selected_ids']))
        self.context_identity = theme.source_identity()
        self.stock_identity = r1.short_horizon_source_identity()
        self.calls = {'context': 0, 'selection': 0}

    def _input(self, bundle, cutoff):
        if (bundle is not self.bundle or bundle.coverage_type != 'TEST_FIXTURE'
                or digest_market_input(bundle) != self.input_hash or cutoff != self.cutoff):
            raise ValueError('TEST_FIXTURE_NATIVE_INPUT_OR_CUTOFF_NOT_BOUND')

    def verify_context(self, parent, *, bundle, cutoff):
        self.calls['context'] += 1
        self._input(bundle, cutoff)
        if (parent is not self.context or digest_json_stream(parent) != self.context_hash
                or theme.source_identity() != self.context_identity):
            raise ValueError('TEST_FIXTURE_NATIVE_THEME_PARENT_NOT_ORIGINAL')

    def verify_selection(self, selected, *, bundle, cutoff):
        self.calls['selection'] += 1
        self._input(bundle, cutoff)
        if (tuple(selected) != self.selected_ids
                or digest_json_stream(self.stock_parent) != self.stock_parent_hash
                or r1.short_horizon_source_identity() != self.stock_identity):
            raise ValueError('TEST_FIXTURE_NATIVE_FROZEN_SELECTION_NOT_ORIGINAL')

    def report(self, **kwargs):
        return diagnostics.build_theme_observation_diagnostics(self.bundle, self.context,
            cutoff=self.cutoff, selected_ids=self.selected_ids,
            _theme_context_validator=self.verify_context,
            _selection_validator=self.verify_selection, **kwargs)


def native_case(kind='confirmed'):
    """Run both genuine native producers on four explicit fixture paths."""
    original, original_cutoff = strength_bundle()
    value = original.model_dump(mode='json')
    remap = dict(zip((START, CONTINUATION, PULLBACK, WEAK), IDS))
    full_calendar = list(value['sessions'])
    if kind in ('first', 'emerging_rs5_unknown'):
        value['sessions'] = full_calendar[:-1]
    observation = value['sessions'][-1]
    value['observation_date'] = observation
    value['bars'] = [row for row in value['bars'] if row['session'] in value['sessions']]
    for row in value['bars']:
        row['instrument_id'] = remap[row['instrument_id']]
        active_tail = full_calendar[-3:] if kind == 'divergence' else full_calendar[-2:]
        if kind != 'inactive' and row['instrument_id'] == CONT_ID and row['session'] in active_tail:
            row['amount_cny'] = '900000000'
        if kind == 'divergence' and row['instrument_id'] == CONT_ID and row['session'] == observation:
            # Three genuine active days establish the previous STRENGTHENING
            # state. A still-positive latest price slows relative momentum,
            # letting the original FSM diagnose divergence without stage edits.
            prior, close = Decimal('14.641'), Decimal('15.37305')
            for key, amount in (('open', prior), ('high', close), ('low', prior), ('close', close)):
                row[key] = str(amount)
                row['comparable_' + key] = str(amount * 2)
            row['reference_prev_close'] = str(prior)
        if kind == 'divergence' and row['instrument_id'] == WEAK_ID and row['session'] == observation:
            # The explicitly artificial market peer advances faster than the
            # still-qualified theme leader. This changes native relative
            # momentum, while the leader still passes the original 5% arm.
            prior, close = Decimal('10.10'), Decimal('12.12')
            for key, amount in (('open', prior), ('high', close), ('low', prior), ('close', close)):
                row[key] = str(amount)
                row['comparable_' + key] = str(amount * 2)
            row['reference_prev_close'] = str(prior)
    for index, security in enumerate(value['universe']['securities']):
        iid = IDS[index]
        security.update(instrument_id=iid, exchange='SSE' if iid.endswith('.SH') else 'SZSE',
            board=('MAIN', 'MAIN', 'STAR', 'CHINEXT')[index])
    for receipt in value['receipts']:
        receipt['instrument_ids'] = list(IDS)
        if receipt['receipt_id'].endswith('BARS'):
            receipt.update(business_sessions=value['sessions'], row_count=len(value['bars']),
                content_hash=digest(value['bars']))
        else:
            receipt.update(content_hash=digest(value['universe']['securities']))
    clock = {key: value['receipts'][0][key] for key in theme.Clocked.model_fields}
    first_day = full_calendar[0]
    snapshot = dict(theme_id='TEST_FIXTURE_ACTIVE_CONT', name='TEST_FIXTURE_CONT_THEME',
        theme_type='EVENT', member_ids=[CONT_ID], effective_from=first_day,
        effective_to=None, definition_version='TEST_FIXTURE_THEME_CONTEXT_V1',
        source_refs=['TEST_FIXTURE_MASTER'], **clock)
    value['themes'] = [] if kind == 'missing_themes' else [snapshot]
    if kind == 'overlap':
        value['themes'].append({**snapshot, 'theme_id': 'TEST_FIXTURE_OVERLAP',
            'name': 'TEST_FIXTURE_OVERLAP', 'member_ids': [CONT_ID, IDS[0]]})
        value['themes'].append({**snapshot, 'theme_id': 'TEST_FIXTURE_NOT_SELECTED',
            'name': 'TEST_FIXTURE_NOT_SELECTED', 'member_ids': [WEAK_ID]})
    if kind == 'zero_members':
        value['themes'] = [{**snapshot, 'member_ids': []}]
    bundle = MarketInput.model_validate(value)
    cutoff = original_cutoff.replace(year=bundle.observation_date.year,
        month=bundle.observation_date.month, day=bundle.observation_date.day)
    rows_by_day = {str(day): [] for day in bundle.sessions}
    for bar in bundle.bars:
        rows_by_day[str(bar.session)].append(bar.model_dump(mode='json'))
    panels = [dict(session=day, scope=theme.MARKET_SCOPE, expected_instrument_ids=IDS,
        rows=rows, source_refs=['TEST_FIXTURE_BARS'],
        clocks={key: rows[0][key] for key in theme.Clocked.model_fields},
        status='COMPLETE', pagination_complete=True, return_basis=theme.CONFIG['reference_return_basis'])
        for day, rows in rows_by_day.items()]
    pack = dict(schema_version='THEME_MARKET_PACK_V1', source_input_hash=digest_market_input(bundle),
        observation_date=observation, decision_cutoff=cutoff.isoformat(),
        coverage_type='TEST_FIXTURE', receipts=[row.model_dump(mode='json') for row in bundle.receipts],
        sessions=panels, themes=[])
    if kind == 'short_baseline':
        # Full native stock qualification remains verifiable. Only the theme
        # source layer lacks the twentieth previous whole-market panel.
        pack['sessions'] = panels[-20:]
    elif kind == 'missing_markets':
        pack['sessions'] = []
    elif kind == 'emerging_rs5_unknown':
        # A legal comparable-price day immediately before the latest three
        # reference-return days breaks only the five-day basis chain.
        pack['sessions'][-4]['return_basis'] = None
    original_pack_hash = digest_json_stream(pack)
    def authenticate_pack(candidate, *, bundle: MarketInput, cutoff):
        # This capability is explicitly restricted to this isolated fixture's
        # original native input and immutable complete market panel document.
        if (bundle.coverage_type != 'TEST_FIXTURE' or digest_market_input(bundle) != pack['source_input_hash']
                or cutoff.isoformat() != pack['decision_cutoff']
                or candidate is not pack or digest_json_stream(candidate) != original_pack_hash):
            raise ValueError('TEST_FIXTURE_ORIGINAL_PANEL_DOCUMENT_NOT_BOUND')
    context = theme.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
        _market_pack_validator=authenticate_pack, output_history=True,
        history_scope='ALL_OBSERVED_SESSIONS_RESEARCH')
    stock_parent = r1.run_short_horizon_strength(bundle, cutoff=cutoff)
    return NativeCase(bundle, cutoff, context, stock_parent, pack)


@pytest.fixture(scope='module')
def cases():
    return {name: native_case(name) for name in ('first', 'confirmed', 'missing_themes',
        'short_baseline', 'overlap', 'inactive', 'emerging_rs5_unknown',
        'zero_members', 'missing_markets', 'divergence')}


def test_absent_native_membership_requires_unknown_banner_not_no_mainline(cases):
    case = cases['missing_themes']
    report = case.report()
    assert case.context['items'] == []
    assert report['mainline_identification_status'] == 'UNKNOWN'
    assert report['report_banner_required'] is True
    assert '主线识别不可用' in report['report_banner']
    assert '不能证明主线覆盖' in report['report_banner']
    assert 'MAINLINE_IDENTIFICATION_UNAVAILABLE_NOT_NO_MAINLINE' in report['reason_codes']
    assert report['confirmed_mainlines'] == report['fermentation_watch'] == []
    assert report['model_only_selected_ids'] == list(case.selected_ids)
    assert report['frozen_selected_ids'] == list(case.selected_ids)


def test_native_first_day_pass_pending_is_early_watch_not_confirmed(cases):
    case = cases['first']
    row = case.context['items'][-1]
    assert CONT_ID in case.selected_ids
    assert row['candidate_states']['strengthening'] == 'PASS'
    assert row['stage'] == 'STRENGTHENING_PENDING'
    assert row['confirmation_states']['strengthening'] == 'FAIL'
    report = case.report()
    assert report['confirmed_mainlines'] == []
    assert len(report['fermentation_watch']) == 1
    assert report['fermentation_watch'][0]['display_role'] == 'ACTIVE_THEME_FERMENTATION_WATCH_UNCONFIRMED'
    assert report['fermentation_watch'][0]['selected_ids'] == [CONT_ID]
    assert report['report_banner_required'] is False


def test_native_second_day_confirmation_is_mainline_without_selection_change(cases):
    case = cases['confirmed']
    before_context, before_stock = digest_json_stream(case.context), digest_json_stream(case.stock_parent)
    row = case.context['items'][-1]
    assert row['stage'] == 'STRENGTHENING'
    assert row['confirmation_states']['strengthening'] == 'PASS'
    report = case.report()
    assert len(report['confirmed_mainlines']) == 1
    assert report['fermentation_watch'] == []
    assert report['confirmed_mainlines'][0]['selected_ids'] == [CONT_ID]
    assert report['stock_selection_changed'] is False
    assert report['stage_or_threshold_changed'] is False
    assert report['frozen_selected_ids'] == list(case.selected_ids)
    assert digest_json_stream(case.context) == before_context
    assert digest_json_stream(case.stock_parent) == before_stock


def test_native_active_divergence_remains_visible_without_becoming_strengthening(cases):
    case = cases['divergence']
    before_context, before_stock = digest_json_stream(case.context), digest_json_stream(case.stock_parent)
    previous, row = case.context['items'][-2:]
    stock_row = next(item for item in case.stock_parent['items'] if item['instrument_id'] == CONT_ID)
    assert previous['stage'] == 'STRENGTHENING'
    assert row['stage'] == 'DIVERGENCE'
    assert theme.exact(row['rs5']) < theme.exact(previous['rs5'])
    assert row['candidate_states']['emerging'] == row['candidate_states']['strengthening'] == 'PASS'
    assert row['confirmation_states']['emerging'] == row['confirmation_states']['strengthening'] == 'PASS'
    assert stock_row['arms']['STRONG_CONTINUATION']['status'] == 'PASS'
    assert stock_row['exact_features']['returns']['1'] == {'numerator': 1, 'denominator': 20}
    assert CONT_ID in case.selected_ids

    report = case.report()
    assert report['confirmed_mainlines'] == report['fermentation_watch'] == []
    assert len(report['active_divergence_mainlines']) == 1
    shown = report['active_divergence_mainlines'][0]
    assert shown['display_role'] == 'ACTIVE_THEME_DIVERGENCE_CONFIRMED'
    assert shown['stage'] == 'DIVERGENCE'
    assert shown['selected_ids'] == [CONT_ID]
    assert shown['candidate_states'] == row['candidate_states']
    assert shown['confirmation_states'] == row['confirmation_states']
    assert shown['high_risk'] == row['high_risk']
    assert shown['high_risk'] is not row['high_risk']
    assert shown['source_feature_axes'] == {key: row[key] for key in
        ('theme_amount_cny', 'market_amount_cny', 'share', 'expansion', 'breadth', 'rs3', 'rs5')}
    assert shown['source_refs'] == row['source_refs']
    assert CONT_ID not in report['model_only_selected_ids']
    assert report['stock_selection_changed'] is report['stage_or_threshold_changed'] is False
    assert report['frozen_selected_ids'] == list(case.selected_ids)
    assert digest_json_stream(case.context) == before_context
    assert digest_json_stream(case.stock_parent) == before_stock


def test_overlap_groups_do_not_duplicate_or_expand_frozen_stock_set(cases):
    case = cases['overlap']
    report = case.report()
    named = (*report['confirmed_mainlines'], *report['fermentation_watch'])
    assert len(named) >= 1
    assert 'TEST_FIXTURE_NOT_SELECTED' not in {row['theme_id'] for row in named}
    assert report['frozen_selected_count'] == len(case.selected_ids)
    shown = {iid for row in named for iid in row['selected_ids']}
    assert shown.issubset(case.selected_ids)
    assert shown | set(report['model_only_selected_ids']) == set(case.selected_ids)
    assert report['frozen_selected_ids'] == list(case.selected_ids)


def test_native_inactive_selected_topic_cannot_be_promoted(cases):
    case = cases['inactive']
    assert CONT_ID in case.selected_ids
    row = case.context['items'][-1]
    assert row['candidate_states']['emerging'] == 'FAIL'
    report = case.report()
    assert report['confirmed_mainlines'] == report['fermentation_watch'] == []
    assert report['model_only_selected_ids'] == list(case.selected_ids)


def test_native_emerging_pass_does_not_require_irrelevant_rs5(cases):
    case = cases['emerging_rs5_unknown']
    row = case.context['items'][-1]
    assert CONT_ID in case.selected_ids
    assert row['rs3'] is not None and row['rs5'] is None
    assert row['candidate_states']['emerging'] == 'PASS'
    assert row['candidate_states']['strengthening'] == 'UNKNOWN'
    assert row['stage'] == 'EMERGING_PENDING'
    report = case.report()
    assert len(report['fermentation_watch']) == 1
    assert report['fermentation_watch'][0]['stage'] == 'EMERGING_PENDING'
    assert report['qualified_theme_data_incomplete'] == []
    assert report['confirmed_mainlines'] == []


@pytest.mark.parametrize('kind,code', [
    ('zero_members', 'THEME_OBSERVATION_VISIBLE_MEMBERSHIP_UNAVAILABLE'),
    ('missing_markets', 'THEME_OBSERVATION_COMPLETE_MARKET_SOURCE_UNAVAILABLE')])
def test_all_observation_membership_or_market_missing_requires_unknown_banner(cases, kind, code):
    case = cases[kind]
    report = case.report()
    assert report['mainline_identification_status'] == 'UNKNOWN'
    assert report['report_banner_required'] is True
    assert code in report['reason_codes']
    assert report['confirmed_mainlines'] == report['fermentation_watch'] == []
    assert report['model_only_selected_ids'] == list(case.selected_ids)


def test_missing_twentieth_prior_session_is_data_gap_not_weak_theme(cases):
    case = cases['short_baseline']
    row = case.context['items'][-1]
    assert row['share_prior20_median'] is None
    assert row['expansion'] is None
    assert row['stage'] == 'UNKNOWN'
    report = case.report()
    assert report['confirmed_mainlines'] == report['fermentation_watch'] == []
    assert len(report['qualified_theme_data_incomplete']) == 1
    incomplete = report['qualified_theme_data_incomplete'][0]
    assert incomplete['stage'] == 'UNKNOWN'
    assert incomplete['display_role'] == 'DATA_INCOMPLETE_NOT_WEAK_THEME'
    assert 'THEME_PRIOR20_SHARE_BASELINE_UNAVAILABLE' in incomplete['reason_codes']
    assert incomplete['partial_facts_are_not_confirmed_mainline'] is True


@pytest.mark.parametrize('missing', ['parent', 'selection', 'both'])
def test_both_native_capabilities_required_not_json_flags(cases, missing):
    case = cases['confirmed']
    with pytest.raises(ValueError, match='NATIVE_PARENT_AND_SELECTION_CAPABILITIES_REQUIRED'):
        diagnostics.build_theme_observation_diagnostics(case.bundle, case.context,
            cutoff=case.cutoff, selected_ids=case.selected_ids,
            _theme_context_validator=None if missing in ('parent', 'both') else case.verify_context,
            _selection_validator=None if missing in ('selection', 'both') else case.verify_selection)


@pytest.mark.parametrize('kind', ['cutoff', 'input', 'hash', 'observation', 'coverage', 'focus'])
def test_forged_native_parent_binding_is_rejected(cases, kind):
    case = cases['confirmed']
    parent = deepcopy(case.context)
    changes = {'cutoff': ('decision_cutoff', (case.cutoff + timedelta(minutes=1)).isoformat()),
        'input': ('source_input_hash', '0' * 64), 'hash': ('context_content_hash', '0' * 64),
        'observation': ('observation_date', '2026-01-01'), 'coverage': ('coverage_type', 'PROSPECTIVE_CAPTURED'),
        'focus': ('formal_focus', True)}
    key, value = changes[kind]
    parent[key] = value
    with pytest.raises(ValueError, match='PARENT_BINDING_MISMATCH'):
        diagnostics.build_theme_observation_diagnostics(case.bundle, parent,
            cutoff=case.cutoff, selected_ids=case.selected_ids,
            _theme_context_validator=case.verify_context, _selection_validator=case.verify_selection)


def test_self_rehashed_parent_copy_is_not_native_fresh_capability(cases):
    case = cases['confirmed']
    parent = deepcopy(case.context)
    parent['items'][-1]['stage'] = 'EMERGING'
    parent['context_content_hash'] = digest_json_stream({key: value for key, value in parent.items()
                                                       if key != 'context_content_hash'})
    with pytest.raises(ValueError, match='NATIVE_THEME_PARENT_NOT_ORIGINAL'):
        diagnostics.build_theme_observation_diagnostics(case.bundle, parent,
            cutoff=case.cutoff, selected_ids=case.selected_ids,
            _theme_context_validator=case.verify_context, _selection_validator=case.verify_selection)


@pytest.mark.parametrize('different', ['not_selected', 'missing_selected', 'duplicate', 'outside_universe'])
def test_native_frozen_selection_cannot_be_replaced_or_expanded(cases, different):
    case = cases['confirmed']
    values = {'not_selected': (*case.selected_ids, WEAK_ID),
        'missing_selected': tuple(iid for iid in case.selected_ids if iid != CONT_ID),
        'duplicate': (*case.selected_ids, CONT_ID), 'outside_universe': (*case.selected_ids, '600999.SH')}
    with pytest.raises(ValueError, match='FROZEN_SELECTION_(?:INVALID|NOT_ORIGINAL)'):
        diagnostics.build_theme_observation_diagnostics(case.bundle, case.context,
            cutoff=case.cutoff, selected_ids=values[different],
            _theme_context_validator=case.verify_context, _selection_validator=case.verify_selection)


def test_valid_report_calls_both_capabilities_and_records_research_only_hash(cases):
    case = cases['confirmed']
    before = dict(case.calls)
    report = case.report()
    assert case.calls == {key: before[key] + 1 for key in before}
    assert report['formal_focus'] is False
    assert report['official_pit_validation_count'] == 0
    assert report['historical_mainline_coverage_claimed'] is False
    assert report['market_edge'] == 'NOT_ESTABLISHED'
    assert report['membership_coverage_scope'] == 'SOURCE_DEFINED_COHORT_ONLY'
    assert report['economic_theme_taxonomy_completeness_status'] == 'UNKNOWN'
    assert report['cohort_transport_completeness_is_not_economic_theme_completeness'] is True
    assert report['absence_from_provider_cohort_does_not_disprove_theme_link'] is True
    assert report['diagnostic_content_hash'] == digest_json_stream({
        key: value for key, value in report.items() if key != 'diagnostic_content_hash'})
