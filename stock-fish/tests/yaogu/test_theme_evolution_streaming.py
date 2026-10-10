"""Exact execution parity on isolated TEST_FIXTURE data, not market validation."""
from copy import deepcopy
from datetime import timedelta
from fractions import Fraction as F
import json
import weakref

import pytest

from stockfish_core.yaogu import theme_evolution as base
from stockfish_core.yaogu import theme_evolution_streaming as streamed
from stockfish_core.yaogu.contracts import SourceReceipt, ThemeSnapshot
from stockfish_core.yaogu.history_artifacts import digest_json_stream, digest_market_input
from stockfish_core.yaogu.theme_market_stream import VERSION as STREAM_SCHEMA
from tests.yaogu.test_theme_evolution_r1 import (IDS, fixture_capability,
    fixture_market, fixture_reference_chain)


class FixtureMarketStream:
    """Authenticated immutable fixture pack; no production source authority."""
    def __init__(self, bundle, cutoff, pack, *, requested=None):
        assert bundle.coverage_type == pack['coverage_type'] == 'TEST_FIXTURE'
        self.pack = deepcopy(pack)
        self.original_hash = digest_json_stream(self.pack)
        self.original_input_hash = digest_market_input(bundle)
        self.cutoff = cutoff
        self.calendar = tuple(str(day) for day in bundle.sessions if day <= bundle.observation_date)
        self.requested_sessions = tuple(requested or self.calendar)
        self._panels = {row['session']: row for row in self.pack['sessions']}
        self.available_sessions = tuple(day for day in self.requested_sessions if day in self._panels)
        self.receipts = tuple(SourceReceipt.model_validate(row) for row in self.pack['receipts'])
        self.themes = tuple(ThemeSnapshot.model_validate(row) for row in self.pack.get('themes', ()))
        self.calls = []
        self.prior_panel = None
        self._descriptor = {'schema_version': STREAM_SCHEMA,
            'source_input_hash': digest_market_input(bundle),
            'observation_date': str(bundle.observation_date), 'decision_cutoff': cutoff.isoformat(),
            'coverage_type': 'TEST_FIXTURE', 'full_observed_calendar': list(self.calendar),
            'requested_sessions': list(self.requested_sessions),
            'available_sessions': list(self.available_sessions),
            'missing_sessions': {day: {'status': 'UNKNOWN', 'reason_codes': ['TEST_FIXTURE_SOURCE_UNAVAILABLE']}
                for day in self.requested_sessions if day not in self.available_sessions},
            'panels': {day: {'panel_hash': digest_json_stream(base.ThemeMarketSession.model_validate(
                self._panels[day]).model_dump(mode='json'))} for day in self.available_sessions},
            'source_receipt_identity_hash': digest_json_stream([row.model_dump(mode='json') for row in self.receipts]),
            'theme_snapshots': [row.model_dump(mode='json') for row in self.themes],
            'return_basis': None, 'source_identity': {'role': 'EXPLICIT_ISOLATED_TEST_FIXTURE'},
            'data_role': 'TEST_FIXTURE_NOT_HISTORICAL_MARKET_EVIDENCE',
            'official_pit_validation_countable': False}
        self.descriptor_hash = digest_json_stream(self._descriptor)

    @property
    def descriptor(self):
        return deepcopy(self._descriptor)

    def validate_binding(self, bundle, cutoff):
        if (bundle.coverage_type != 'TEST_FIXTURE' or cutoff != self.cutoff
                or digest_market_input(bundle) != self.original_input_hash
                or digest_json_stream(self.pack) != self.original_hash):
            raise ValueError('TEST_FIXTURE_STREAM_BINDING_OR_ORIGINAL_BYTES_CHANGED')

    def panel(self, day):
        # The producer must release the preceding panel object. Keeping its
        # selected DailyBars for comparable prices does not retain the panel.
        assert self.prior_panel is None or self.prior_panel() is None
        self.calls.append(day)
        if day not in self.available_sessions:
            return None
        panel = base.ThemeMarketSession.model_validate(self._panels[day])
        self.prior_panel = weakref.ref(panel)
        return panel


def paired(tmp_path, bundle, cutoff, pack, **kwargs):
    old = base.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
        _market_pack_validator=fixture_capability, output_history=kwargs.get('output_history', True),
        membership_mode=kwargs.get('membership_mode', 'CURRENT_COHORT_BACKVIEW'),
        history_scope=kwargs.get('history_scope', 'ALL_OBSERVED_SESSIONS_RESEARCH'))
    stream = FixtureMarketStream(bundle, cutoff, pack)
    new = streamed.run_theme_evolution_stream(bundle, cutoff=cutoff, market_stream=stream,
        evidence_directory=tmp_path / 'complete-native-evidence', **kwargs)
    assert new['items'] == old['items']
    assert new['effective_input_hash'] == old['effective_input_hash']
    assert new['source_refs'] == old['source_refs']
    for key in ('source_input_hash', 'computed_exchange_sessions', 'observation_date', 'decision_cutoff',
                'coverage_type', 'requested_output_scope', 'requested_history_scope', 'membership_mode',
                'data_role', 'historical_membership_verified', 'official_pit_validation_countable',
                'status', 'formal_focus', 'core_pass', 'parameter_status', 'market_edge'):
        assert new[key] == old[key], key
    assert stream.calls == new['computed_exchange_sessions']
    assert new['strategy_id'] != old['strategy_id']
    assert new['feature_version'] != old['feature_version']
    assert new['context_content_hash'] != old['context_content_hash']
    assert new['market_pack_hash'] is None
    assert new['market_stream_descriptor_hash'] == stream.descriptor_hash
    audit = new['execution_provenance']['audit_manifest']
    assert audit['complete_allowed_row_audit'] and audit['row_count'] > 0
    return old, new, stream


@pytest.mark.parametrize('mode', ['DATED_ONLY', 'CURRENT_COHORT_BACKVIEW'])
def test_full_exact_features_fsm_risk_and_dependencies_match_batch(tmp_path, mode):
    bundle, cutoff, pack = fixture_market()
    _, result, _ = paired(tmp_path, bundle, cutoff, pack, membership_mode=mode)
    assert len(result['items']) == 67
    assert result['items'][-1]['stage'] == 'STRENGTHENING'
    assert result['items'][-1]['confirmation_states']['strengthening'] == 'PASS'
    assert result['items'][-1]['high_risk']['prior60_share_p90'] is not None
    assert result['execution_provenance']['retained_feature_sessions_per_theme_limit'] == 60


def test_original_529_calendar_not_compressed_into_available_tail(tmp_path):
    bundle, cutoff, pack = fixture_reference_chain()
    calendar = list(bundle.sessions)
    cursor = calendar[0] - timedelta(days=1)
    prefix = []
    while len(prefix) + len(calendar) < 529:
        if cursor.weekday() < 5:
            prefix.append(cursor)
        cursor -= timedelta(days=1)
    bundle = bundle.model_copy(update={'sessions': (*reversed(prefix), *calendar)})
    pack['source_input_hash'] = digest_market_input(bundle)
    _, result, _ = paired(tmp_path, bundle, cutoff, pack)
    assert len(result['computed_exchange_sessions']) == len(result['items']) == 529
    assert result['items'][0]['market_amount_cny'] is None
    assert result['items'][0]['high_risk']['status'] == 'UNKNOWN'
    assert result['items'][-1]['stage'] == 'STRENGTHENING'
    assert result['execution_provenance']['missing_sessions_not_compressed'] is True
    assert result['execution_provenance']['window_fsm_stitching_used'] is False


@pytest.mark.parametrize('kind', ['missing_panel', 'late', 'partial', 'missing_star', 'missing_amount',
                                 'conflicting_price', 'unrecognized_reference', 'bad_ohlc'])
def test_missing_or_invalid_axis_preserves_exact_unknown_and_audit(tmp_path, kind):
    bundle, cutoff, pack = fixture_reference_chain()
    panel = pack['sessions'][-1]
    if kind == 'missing_panel':
        pack['sessions'].pop()
    elif kind == 'late':
        pack['receipts'][-1]['first_seen_at'] = (cutoff + timedelta(minutes=1)).isoformat()
        pack['receipts'][-1]['ingested_at'] = pack['receipts'][-1]['first_seen_at']
    elif kind == 'partial':
        pack['receipts'][-1]['status'] = 'PARTIAL'
    elif kind == 'missing_star':
        panel['rows'] = [row for row in panel['rows'] if row['instrument_id'] != IDS[2]]
    elif kind == 'missing_amount':
        panel['rows'][0]['amount_cny'] = None
    elif kind == 'conflicting_price':
        other = deepcopy(panel['rows'][0])
        other['close'] = '999'
        panel['rows'].append(other)
    elif kind == 'unrecognized_reference':
        panel['return_basis'] = 'NOT_RECOGNIZED'
    else:
        panel['rows'][0]['high'] = '0.0001'
    _, result, _ = paired(tmp_path, bundle, cutoff, pack)
    assert result['items'][-1]['stage'] == 'UNKNOWN'
    assert result['items'][-1]['rs3'] is None
    assert result['items'][-1]['confirmation_states']['strengthening'] == 'UNKNOWN'


def test_missing_previous_exchange_day_does_not_confirm_across_gap(tmp_path):
    bundle, cutoff, pack = fixture_reference_chain()
    missing = pack['sessions'][-2]['session']
    pack['sessions'].pop(-2)
    _, result, _ = paired(tmp_path, bundle, cutoff, pack)
    yesterday, today = result['items'][-2:]
    assert yesterday['session'] == today['previous_exchange_session'] == missing
    assert yesterday['stage'] == today['stage'] == 'UNKNOWN'
    assert today['confirmation_states']['strengthening'] == 'UNKNOWN'
    assert today['expansion'] is None


@pytest.mark.parametrize('history_scope', ['LATEST_67', 'ALL_OBSERVED_SESSIONS_RESEARCH'])
def test_last_two_display_scope_keeps_same_full_history_state(tmp_path, history_scope):
    bundle, cutoff, pack = fixture_market(70)
    _, result, _ = paired(tmp_path, bundle, cutoff, pack,
        output_history=False, history_scope=history_scope)
    assert len(result['items']) == 2
    # This fixture's finite Decimal source prices contain a tiny exact RS
    # decline on day 70. Both producers retain the original divergence rule.
    assert result['items'][-1]['stage'] == 'DIVERGENCE'
    assert len(result['computed_exchange_sessions']) == (67 if history_scope == 'LATEST_67' else 70)


def test_multiple_original_and_stream_themes_preserve_order_and_complete_scope(tmp_path):
    bundle, cutoff, pack = fixture_market()
    # Original membership still contributes even if the stream adds an earlier
    # sorting theme. No caller-selected themes silently replace the five-scope.
    pack['themes'] = [row.model_copy(update={'theme_id': 'AAA_TEST_FIXTURE_STREAM',
        'name': 'TEST_FIXTURE_STREAM'}).model_dump(mode='json') for row in bundle.themes]
    _, result, _ = paired(tmp_path, bundle, cutoff, pack)
    assert len(result['items']) == 134
    assert [row['theme_id'] for row in result['items'][:67]] == ['AAA_TEST_FIXTURE_STREAM'] * 67
    assert [row['theme_id'] for row in result['items'][67:]] == ['TEST_FIXTURE_THEME'] * 67
    assert result['items'][66]['stage'] == result['items'][-1]['stage']


def test_all_five_original_themes_survive_explicit_additional_stream_theme(tmp_path):
    bundle, cutoff, pack = fixture_market(6)
    original = tuple(row.model_copy(update={'theme_id': f'TEST_FIXTURE_ORIGINAL_{index}'})
        for index in range(5) for row in bundle.themes)
    extra = [row.model_copy(update={'theme_id': 'TEST_FIXTURE_ADDITIONAL'}).model_dump(mode='json')
        for row in bundle.themes]
    bundle = bundle.model_copy(update={'themes': original})
    pack.update(source_input_hash=digest_market_input(bundle), themes=extra)
    _, result, _ = paired(tmp_path, bundle, cutoff, pack)
    assert len(result['items']) == 6 * 6
    assert {row['theme_id'] for row in result['items']} == {
        'TEST_FIXTURE_ADDITIONAL', *(f'TEST_FIXTURE_ORIGINAL_{index}' for index in range(5))}


def test_known_nontrading_exclusion_and_dated_absent_member_match_batch(tmp_path):
    bundle, cutoff, pack = fixture_reference_chain()
    absent = '600999.SH'
    bundle = bundle.model_copy(update={'themes': (bundle.themes[-1].model_copy(
        update={'member_ids': (*IDS[:2], absent)}),)})
    pack['source_input_hash'] = digest_market_input(bundle)
    panel = pack['sessions'][-1]
    panel['nontrading_instrument_ids'] = [IDS[1]]
    panel['nontrading_policy'] = 'NONTRADING_EXCLUDED'
    panel['rows'] = [row for row in panel['rows'] if row['instrument_id'] != IDS[1]]
    _, result, _ = paired(tmp_path, bundle, cutoff, pack)
    today = result['items'][-1]
    assert today['membership_count'] == 3
    assert today['eligible_membership_count'] == 2
    assert today['active_membership_count'] == 1
    assert today['absent_from_dated_market_ids'] == [absent]
    assert base.exact(today['market_amount_cny']) == 170
    assert base.exact(today['theme_amount_cny']) == 30
    assert today['concentration_adjusted_hhi'] is None


def test_current_snapshot_captured_after_price_day_remains_retrospective(tmp_path):
    bundle, cutoff, pack = fixture_market()
    later = cutoff + timedelta(days=2)
    receipt = deepcopy(pack['receipts'][-1])
    receipt.update(receipt_id='TEST_FIXTURE_LATER_COHORT', business_sessions=[str(later.date())],
        **{key: later.isoformat() for key in base.Clocked.model_fields})
    theme = bundle.themes[-1].model_copy(update={
        'effective_from': later.date(), 'effective_to': None,
        'source_refs': (receipt['receipt_id'],), **{key: later for key in base.Clocked.model_fields}})
    bundle = bundle.model_copy(update={'themes': ()})
    pack.update(source_input_hash=digest_market_input(bundle), decision_cutoff=later.isoformat(),
        receipts=[*pack['receipts'], receipt], themes=[theme.model_dump(mode='json')])
    _, result, _ = paired(tmp_path, bundle, later, pack)
    assert result['items'][-1]['stage'] == 'STRENGTHENING'
    assert result['items'][-1]['cohort_effective_from'] == str(later.date())
    assert result['historical_membership_verified'] is False
    assert result['official_pit_validation_countable'] is False
    assert result['selection_role'] == 'RETROSPECTIVE_CONTEXT_ANALYSIS'
    assert result['market_edge'] == 'NOT_ESTABLISHED'


def test_revision_visibility_precedes_selection_without_borrowing_late_price(tmp_path):
    bundle, cutoff, pack = fixture_market()
    late = deepcopy(pack['sessions'][-1]['rows'][0])
    late.update(comparable_close='999', first_seen_at=(cutoff + timedelta(minutes=1)).isoformat(),
                ingested_at=(cutoff + timedelta(minutes=1)).isoformat())
    pack['sessions'][-1]['rows'].append(late)
    _, result, _ = paired(tmp_path, bundle, cutoff, pack)
    # Panel completeness becomes UNKNOWN despite a legal earlier revision.
    assert result['items'][-1]['share'] is None
    assert result['items'][-1]['stage'] == 'UNKNOWN'


def test_reference_chain_basis_change_breaks_three_five_session_windows(tmp_path):
    bundle, cutoff, pack = fixture_market()
    pack['sessions'][-1]['return_basis'] = base.CONFIG['reference_return_basis']
    _, result, _ = paired(tmp_path, bundle, cutoff, pack)
    assert result['items'][-1]['theme_return_1d'] is not None
    assert result['items'][-1]['rs3'] is result['items'][-1]['rs5'] is None


def test_descriptor_flag_alone_and_production_ducktype_cannot_grant_authority(tmp_path):
    bundle, cutoff, pack = fixture_market(5)
    with pytest.raises(ValueError, match='VALIDATOR_CAPABILITY_REQUIRED'):
        streamed.run_theme_evolution_stream(bundle, cutoff=cutoff,
            market_stream={'verified': True}, evidence_directory=tmp_path / 'never-written')
    fixture = FixtureMarketStream(bundle, cutoff, pack)
    production = bundle.model_copy(update={'coverage_type': 'PROSPECTIVE_CAPTURED'})
    with pytest.raises(ValueError, match='AUTHENTICATED_NATIVE_CAPABILITY_REQUIRED'):
        streamed.run_theme_evolution_stream(production, cutoff=cutoff,
            market_stream=fixture, evidence_directory=tmp_path / 'also-never-written')


@pytest.mark.parametrize('kind', ['hash', 'calendar', 'input_hash', 'receipt', 'available', 'snapshot'])
def test_independent_descriptor_and_identity_binding_rejects_mismatch(tmp_path, kind):
    bundle, cutoff, pack = fixture_market(5)
    stream = FixtureMarketStream(bundle, cutoff, pack)
    if kind == 'hash':
        stream.descriptor_hash = '0' * 64
    else:
        key, value = {'calendar': ('full_observed_calendar', list(reversed(stream.calendar))),
            'input_hash': ('source_input_hash', '0' * 64),
            'receipt': ('source_receipt_identity_hash', '0' * 64),
            'available': ('available_sessions', []), 'snapshot': ('theme_snapshots', [{}])}[kind]
        stream._descriptor[key] = value
        stream.descriptor_hash = digest_json_stream(stream._descriptor)
    with pytest.raises(ValueError, match='INPUT_OR_DESCRIPTOR_BINDING_MISMATCH'):
        streamed.run_theme_evolution_stream(bundle, cutoff=cutoff, market_stream=stream,
            evidence_directory=tmp_path / 'never-written')


def test_changed_panel_fails_and_preserves_partial_real_journal(tmp_path):
    bundle, cutoff, pack = fixture_market(5)
    stream = FixtureMarketStream(bundle, cutoff, pack)
    original = stream.panel
    def changed(day):
        if day == stream.available_sessions[-1]:
            stream._panels[day]['rows'][0]['amount_cny'] = '999'
        return original(day)
    stream.panel = changed
    # Mutate only after the initial capability check. Per-panel hash catches
    # the changed original bytes before their numeric values are consumed.
    with pytest.raises(ValueError, match='PANEL_ORIGINAL_SOURCE_MISMATCH'):
        streamed.run_theme_evolution_stream(bundle, cutoff=cutoff, market_stream=stream,
            evidence_directory=tmp_path / 'partial-audit')
    manifest = json.loads((tmp_path / 'partial-audit/manifest.json').read_text())
    assert manifest['row_count'] > 0
    assert (tmp_path / 'partial-audit/record-index.ndjson').stat().st_size > 0


def test_end_binding_rejects_source_mutation_after_last_panel(tmp_path):
    bundle, cutoff, pack = fixture_market(5)
    stream = FixtureMarketStream(bundle, cutoff, pack)
    original = stream.panel
    def changed(day):
        value = original(day)
        if day == stream.calendar[-1]:
            stream.pack['amount_only_fake_scalar'] = '999'
        return value
    stream.panel = changed
    with pytest.raises(ValueError, match='TEST_FIXTURE_STREAM_BINDING_OR_ORIGINAL_BYTES_CHANGED'):
        streamed.run_theme_evolution_stream(bundle, cutoff=cutoff, market_stream=stream,
            evidence_directory=tmp_path / 'partial-audit')
    assert (tmp_path / 'partial-audit/manifest.json').exists()


def test_numeric_encoding_and_base_rule_identity_are_explicit_new_version(tmp_path):
    bundle, cutoff, pack = fixture_reference_chain()
    _, result, _ = paired(tmp_path, bundle, cutoff, pack)
    assert result['numeric_rule_source_identity'] == base.source_identity()
    assert result['exact_numeric_encoding'] == base.EXACT_NUMERIC_ENCODING
    assert result['schema_version'] == streamed.SCHEMA_VERSION
    assert result['spec_revision'] == streamed.SPEC_REVISION
    assert result['source_hash'] == base.digest(result['source_file_hashes'])
    for suffix in ('theme_evolution.py', 'theme_evolution_streaming.py', 'evidence_audit_spool.py',
                   'theme_market_stream.py', 'visibility.py', 'theme_evolution_r1.yaml'):
        assert any(key.endswith(suffix) for key in result['source_file_hashes'])
    fraction = F(10 ** 5000 + 1, 10 ** 5000 - 1)
    assert base.exact(base.encoded(fraction)) == fraction
    view = {key: value for key, value in result.items() if key != 'context_content_hash'}
    assert digest_json_stream(view) == result['context_content_hash']


def test_native_five_day_compound_keeps_huge_exact_denominators_as_text(tmp_path):
    bundle, cutoff, pack = fixture_reference_chain()
    for index, panel in enumerate(pack['sessions'][-5:]):
        # Separate references prevent an artificial telescoping product.
        reference = 10 ** 1000 + 7 * index + 1
        close = reference + 1
        for row in panel['rows'][:2]:
            row.update(open=str(close), high=str(close), low=str(close), close=str(close),
                       reference_prev_close=str(reference))
    _, result, _ = paired(tmp_path, bundle, cutoff, pack, output_history=False)
    chain = result['items'][-1]['theme_return_5d']
    assert isinstance(chain['denominator'], str)
    value = F(1)
    for index in range(5):
        reference = 10 ** 1000 + 7 * index + 1
        value *= F(reference + 1, reference)
    assert base.exact(chain) == value - 1


@pytest.mark.parametrize('changes,error', [({'output_history': 1}, 'OUTPUT_HISTORY_MUST_BE_BOOLEAN'),
    ({'history_scope': 'BEST_WINDOW'}, 'HISTORY_SCOPE_UNSUPPORTED'),
    ({'membership_mode': 'TODAY_IS_PAST'}, 'MEMBERSHIP_MODE_UNSUPPORTED')])
def test_explicit_research_scope_controls_reject_invalid_values(tmp_path, changes, error):
    bundle, cutoff, pack = fixture_market(5)
    with pytest.raises(ValueError, match=error):
        streamed.run_theme_evolution_stream(bundle, cutoff=cutoff,
            market_stream=FixtureMarketStream(bundle, cutoff, pack),
            evidence_directory=tmp_path / 'never-written', **changes)
