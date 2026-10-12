"""Isolated full batch/V1/V2/V3 parity; never a real market-budget proof."""
from copy import deepcopy
from datetime import timedelta
from fractions import Fraction as F
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from stockfish_core.yaogu import theme_evolution as base
from stockfish_core.yaogu import theme_evolution_streaming as legacy
from stockfish_core.yaogu import theme_evolution_streaming_v2 as previous
from stockfish_core.yaogu import theme_evolution_streaming_v3 as streamed
from stockfish_core.yaogu import evidence_audit_spool_v3 as journal_module
from stockfish_core.yaogu.contracts import SourceReceipt, ThemeSnapshot
from stockfish_core.yaogu.history_artifacts import digest_json_stream, digest_market_input
from stockfish_core.yaogu.visibility import EvidenceResolver
from tests.yaogu.test_theme_evolution_streaming import FixtureMarketStream
from tests.yaogu.test_theme_evolution_r1 import (IDS, fixture_capability,
    fixture_market, fixture_reference_chain)


def paired(tmp_path, bundle, cutoff, pack, **kwargs):
    plain, first, second = [], [], []
    class Plain(EvidenceResolver):
        def __init__(self, *args, **kw):
            super().__init__(*args, **kw); plain.append(self)
    class V1(legacy.SpoolingEvidenceResolver):
        def __init__(self, *args, **kw):
            super().__init__(*args, **kw); first.append(self)
    class V2(streamed._RecordingResolver):
        def __init__(self, *args, **kw):
            super().__init__(*args, **kw); second.append(self)
    with patch.object(base, 'EvidenceResolver', Plain):
        old = base.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
            _market_pack_validator=fixture_capability,
            output_history=kwargs.get('output_history', True),
            membership_mode=kwargs.get('membership_mode', 'CURRENT_COHORT_BACKVIEW'),
            history_scope=kwargs.get('history_scope', 'ALL_OBSERVED_SESSIONS_RESEARCH'))
    with patch.object(legacy, 'SpoolingEvidenceResolver', V1):
        original_stream = legacy.run_theme_evolution_stream(bundle, cutoff=cutoff,
            market_stream=FixtureMarketStream(bundle, cutoff, pack),
            evidence_directory=tmp_path/'original-v1-journal', **kwargs)
    stream = FixtureMarketStream(bundle, cutoff, pack)
    with patch.object(streamed, '_RecordingResolver', V2):
        new = streamed.run_theme_evolution_stream(bundle, cutoff=cutoff, market_stream=stream,
            evidence_directory=tmp_path/'complete-v3-journal', **kwargs)
    prior = previous.run_theme_evolution_stream(bundle, cutoff=cutoff,
        market_stream=FixtureMarketStream(bundle, cutoff, pack),
        evidence_directory=tmp_path/'frozen-v2-journal', **kwargs)
    assert prior['items'] == new['items']
    assert prior['effective_input_hash'] == new['effective_input_hash']
    assert len(plain) == len(first) == len(second) == 1
    expected = plain[0].effective_dependencies()
    # Batch memory envelopes may contain tuple source_refs. Compare every
    # complete canonical JSON row, the actual disk/identity contract; never
    # treat Python tuple-vs-list representation as an evidence difference.
    canonical_expected = json.loads(json.dumps(expected, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False))
    assert list(first[0].effective_dependencies()['records']) == canonical_expected['records']
    assert list(second[0].effective_dependencies()['records']) == canonical_expected['records']
    assert first[0].effective_dependencies()['sources'] == second[0].effective_dependencies()['sources'] == canonical_expected['sources']
    assert new['items'] == original_stream['items'] == old['items']
    assert new['effective_input_hash'] == original_stream['effective_input_hash'] == old['effective_input_hash'] == digest_json_stream(expected)
    assert new['source_refs'] == original_stream['source_refs'] == old['source_refs']
    for key in ('source_input_hash', 'computed_exchange_sessions', 'observation_date', 'decision_cutoff',
                'coverage_type', 'requested_output_scope', 'requested_history_scope', 'membership_mode',
                'data_role', 'historical_membership_verified', 'official_pit_validation_countable',
                'status', 'formal_focus', 'core_pass', 'parameter_status', 'market_edge'):
        assert new[key] == original_stream[key] == old[key], key
    assert stream.calls == new['computed_exchange_sessions']
    assert new['strategy_id'] not in (old['strategy_id'], original_stream['strategy_id'])
    assert new['feature_version'] not in (old['feature_version'], original_stream['feature_version'])
    assert new['context_content_hash'] not in (old['context_content_hash'], original_stream['context_content_hash'])
    assert new['market_pack_hash'] is None
    assert new['market_stream_descriptor_hash'] == stream.descriptor_hash
    audit = new['execution_provenance']['audit_manifest']
    assert audit['complete_allowed_row_audit'] and audit['row_count'] == len(expected['records']) > 0
    assert new['execution_provenance']['joint_validation'] == 'PRE_POST_WHOLE_SHA_7FP_AND_ALL_CANONICAL_FRAMES_CRC_EOF_EXACT_INDEX'
    assert new['execution_provenance']['outer_original_registry_authentication'] == 'CALLER_RESPONSIBILITY_NOT_CERTIFIED_BY_THIS_MODULE'
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


@pytest.mark.parametrize('full_count', [529, 530])
def test_original_calendar_not_compressed_into_available_tail(tmp_path, full_count):
    bundle, cutoff, pack = fixture_reference_chain()
    calendar = list(bundle.sessions)
    cursor = calendar[0] - timedelta(days=1)
    prefix = []
    while len(prefix) + len(calendar) < full_count:
        if cursor.weekday() < 5:
            prefix.append(cursor)
        cursor -= timedelta(days=1)
    bundle = bundle.model_copy(update={'sessions': (*reversed(prefix), *calendar)})
    pack['source_input_hash'] = digest_market_input(bundle)
    _, result, _ = paired(tmp_path, bundle, cutoff, pack)
    assert len(result['computed_exchange_sessions']) == len(result['items']) == full_count
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


def capture_resolver(monkeypatch):
    instances = []
    original = streamed._RecordingResolver
    class Captured(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            instances.append(self)
    monkeypatch.setattr(streamed, '_RecordingResolver', Captured)
    return instances


def mutate_same_size(path):
    before = path.stat()
    raw = path.read_bytes()
    index = next(index for index, value in enumerate(raw) if 48 <= value <= 57)
    path.write_bytes(raw[:index] + (b'7' if raw[index:index+1] != b'7' else b'8') + raw[index+1:])
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


def test_valueerror_unique_write_budget_is_fatal_original_not_unknown(tmp_path, monkeypatch):
    bundle, cutoff, pack = fixture_market(4)
    instances = capture_resolver(monkeypatch)
    primary = ValueError('TEST_FIXTURE_ACTUAL_RETURNED_BUDGET')
    original = journal_module._RowJournal._append
    def budget(journal, path, handle, raw):
        if path.name.startswith('rows-'):
            raise primary
        return original(journal, path, handle, raw)
    monkeypatch.setattr(journal_module._RowJournal, '_append', budget)
    with pytest.raises(ValueError) as caught:
        streamed.run_theme_evolution_stream(bundle, cutoff=cutoff,
            market_stream=FixtureMarketStream(bundle, cutoff, pack),
            evidence_directory=tmp_path/'partial-unique')
    assert caught.value is primary
    assert instances[0].rows.closed
    assert any(row['phase'] == 'THEME_STREAM_PRIMARY' for row in primary.theme_stream_failure_provenance)
    assert (tmp_path/'partial-unique/record-index.ndjson').exists()


@pytest.mark.parametrize('mutate', [False, True])
def test_duplicate_read_valueerror_budget_primary_survives_cleanup(tmp_path, monkeypatch, mutate):
    bundle, cutoff, pack = fixture_market(4)
    instances = capture_resolver(monkeypatch)
    primary = ValueError('TEST_FIXTURE_DUPLICATE_READ_BUDGET')
    original = journal_module._RowJournal._read
    calls = []
    def budget(journal, source, size, **kwargs):
        raw = original(journal, source, size, **kwargs)
        if not calls and raw:
            calls.append(len(raw))
            if mutate:
                mutate_same_size(journal.segments[0])
            raise primary
        return raw
    monkeypatch.setattr(journal_module._RowJournal, '_read', budget)
    with pytest.raises(ValueError) as caught:
        streamed.run_theme_evolution_stream(bundle, cutoff=cutoff,
            market_stream=FixtureMarketStream(bundle, cutoff, pack),
            evidence_directory=tmp_path/'partial-duplicate')
    assert caught.value is primary
    assert instances[0].rows.closed and calls
    assert primary.theme_stream_journal_read_metrics['actual_returned_read_bytes'] >= calls[0]
    phases = {row['phase'] for row in primary.theme_stream_failure_provenance}
    assert 'THEME_STREAM_PRIMARY' in phases
    if mutate:
        assert {'READER_PRIMARY', 'READER_CLEANUP', 'THEME_STREAM_CLEANUP'} <= phases
        assert not (tmp_path/'partial-duplicate/manifest.json').exists()


def test_primary_panel_failure_and_close_failure_are_separate(tmp_path, monkeypatch):
    bundle, cutoff, pack = fixture_market(4)
    instances = capture_resolver(monkeypatch)
    primary = ValueError('TEST_FIXTURE_ORIGINAL_PANEL_BUDGET')
    cleanup = RuntimeError('TEST_FIXTURE_CLOSE_FAILED_AFTER_REAL_CLOSE')
    stream = FixtureMarketStream(bundle, cutoff, pack)
    original_panel = stream.panel
    def panel(day):
        if day == stream.requested_sessions[-1]:
            raise primary
        return original_panel(day)
    stream.panel = panel
    original_close = streamed._RecordingResolver.close
    def close(resolver):
        original_close(resolver)
        raise cleanup
    monkeypatch.setattr(streamed._RecordingResolver, 'close', close)
    with pytest.raises(ValueError) as caught:
        streamed.run_theme_evolution_stream(bundle, cutoff=cutoff, market_stream=stream,
            evidence_directory=tmp_path/'partial-panel')
    assert caught.value is primary
    assert instances[0].rows.closed
    assert [row['phase'] for row in primary.theme_stream_failure_provenance][-2:] == [
        'THEME_STREAM_PRIMARY', 'THEME_STREAM_CLEANUP']
    assert (tmp_path/'partial-panel/manifest.json').exists()


def test_joint_runs_once_without_ordinary_digest_or_audit_rescan(tmp_path, monkeypatch):
    bundle, cutoff, pack = fixture_market(7)
    instances = capture_resolver(monkeypatch)
    calls = []
    original = streamed._RecordingResolver.joint_dependency_digest_and_audit_manifest
    def joint(resolver):
        calls.append('joint')
        return original(resolver)
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_REDUNDANT_ORDINARY_AUDIT_PATH_USED')
    monkeypatch.setattr(streamed._RecordingResolver, 'joint_dependency_digest_and_audit_manifest', joint)
    monkeypatch.setattr(streamed._RecordingResolver, 'effective_dependencies', forbidden)
    monkeypatch.setattr(streamed._RecordingResolver, 'audit_manifest', forbidden)
    monkeypatch.setattr(streamed._RecordingResolver, 'close', forbidden)
    result = streamed.run_theme_evolution_stream(bundle, cutoff=cutoff,
        market_stream=FixtureMarketStream(bundle, cutoff, pack), evidence_directory=tmp_path/'joint-only')
    assert calls == ['joint'] and instances[0].rows.closed
    assert result['execution_provenance']['audit_manifest']['complete_allowed_row_audit']


@pytest.mark.parametrize('kind', ['payload', 'index'])
def test_joint_same_size_restored_mtime_tamper_fails_without_success(tmp_path, monkeypatch, kind):
    bundle, cutoff, pack = fixture_market(4)
    instances = capture_resolver(monkeypatch)
    original = journal_module._JointRecords.__iter__
    def tampered(records):
        iterator = original(records)
        first = next(iterator)
        yield first
        path = (records._journal.segments[0] if kind == 'payload' else records._journal.index_path)
        mutate_same_size(path)
        yield from iterator
    monkeypatch.setattr(journal_module._JointRecords, '__iter__', tampered)
    with pytest.raises(journal_module.EvidenceAuditIntegrityError) as caught:
        streamed.run_theme_evolution_stream(bundle, cutoff=cutoff,
            market_stream=FixtureMarketStream(bundle, cutoff, pack), evidence_directory=tmp_path/'joint-tamper')
    assert instances[0].rows.closed
    assert {'JOINT', 'THEME_STREAM_PRIMARY', 'THEME_STREAM_CLEANUP'} <= {
        row['phase'] for row in caught.value.theme_stream_failure_provenance}
    # A pre-seal manifest is retained. It never certifies the failed post epoch.
    assert (tmp_path/'joint-tamper/manifest.json').exists()


def test_joint_hash_budget_failure_preserves_primary_and_partial(tmp_path, monkeypatch):
    bundle, cutoff, pack = fixture_market(4)
    instances = capture_resolver(monkeypatch)
    primary = ValueError('TEST_FIXTURE_JOINT_HASH_READ_BUDGET')
    original = journal_module.digest_json_stream
    def budget(value):
        if isinstance(value, dict) and isinstance(value.get('records'), journal_module._JointRecords):
            raise primary
        return original(value)
    monkeypatch.setattr(journal_module, 'digest_json_stream', budget)
    with pytest.raises(ValueError) as caught:
        streamed.run_theme_evolution_stream(bundle, cutoff=cutoff,
            market_stream=FixtureMarketStream(bundle, cutoff, pack), evidence_directory=tmp_path/'joint-budget')
    assert caught.value is primary and instances[0].rows.closed
    assert (tmp_path/'joint-budget/manifest.json').exists()
    assert {'JOINT', 'THEME_STREAM_PRIMARY'} <= {
        row['phase'] for row in primary.theme_stream_failure_provenance}


def test_post_joint_live_terminal_runs_before_final_binding(tmp_path, monkeypatch):
    bundle, cutoff, pack = fixture_market(4)
    instances = capture_resolver(monkeypatch)
    stream = FixtureMarketStream(bundle, cutoff, pack)
    calls = []
    def terminal():
        assert instances[0].rows.closed and instances[0].rows.sealed is not None
        calls.append('terminal')
        # A return dict is not source authority or a new capability.
        return {'verified': True}
    result = streamed.run_theme_evolution_stream(bundle, cutoff=cutoff, market_stream=stream,
        evidence_directory=tmp_path/'post-terminal', _source_terminal_validator=terminal)
    assert calls == ['terminal']
    assert result['execution_provenance']['source_terminal_callback_status'] == 'LIVE_CALLBACK_COMPLETED'
    assert result['execution_provenance']['outer_original_registry_authentication'] == 'CALLER_RESPONSIBILITY_NOT_CERTIFIED_BY_THIS_MODULE'


def test_live_terminal_failure_is_original_and_never_unknown(tmp_path):
    bundle, cutoff, pack = fixture_market(4)
    primary = ValueError('TEST_FIXTURE_OUTER_WHOLE_7FP_BUDGET')
    def terminal():
        raise primary
    with pytest.raises(ValueError) as caught:
        streamed.run_theme_evolution_stream(bundle, cutoff=cutoff,
            market_stream=FixtureMarketStream(bundle, cutoff, pack), evidence_directory=tmp_path/'terminal-failure',
            _source_terminal_validator=terminal)
    assert caught.value is primary
    assert any(row['phase'] == 'THEME_STREAM_PRIMARY' for row in primary.theme_stream_failure_provenance)
    assert (tmp_path/'terminal-failure/manifest.json').exists()


def test_terminal_mutation_cannot_hide_after_joint(tmp_path):
    bundle, cutoff, pack = fixture_market(4)
    stream = FixtureMarketStream(bundle, cutoff, pack)
    def terminal():
        stream.pack['TEST_FIXTURE_MUTATION'] = True
    with pytest.raises(ValueError, match='TEST_FIXTURE_STREAM_BINDING_OR_ORIGINAL_BYTES_CHANGED'):
        streamed.run_theme_evolution_stream(bundle, cutoff=cutoff, market_stream=stream,
            evidence_directory=tmp_path/'terminal-mutation', _source_terminal_validator=terminal)


def test_terminal_serialized_flag_cannot_replace_live_callable(tmp_path):
    bundle, cutoff, pack = fixture_market(4)
    with pytest.raises(ValueError, match='SOURCE_TERMINAL_CAPABILITY_REQUIRED'):
        streamed.run_theme_evolution_stream(bundle, cutoff=cutoff,
            market_stream=FixtureMarketStream(bundle, cutoff, pack), evidence_directory=tmp_path/'never-written',
            _source_terminal_validator={'verified': True})
    assert not (tmp_path/'never-written').exists()


def test_new_spec_and_old_helper_whole_identity_are_both_explicit():
    identity = streamed.source_identity()
    for suffix in ('theme_evolution_streaming_v3.py', 'THEME_EVOLUTION_STREAMING_V3.md',
                   'evidence_audit_spool_v3.py', 'theme_evolution_streaming.py'):
        names = [name for name in identity['source_file_hashes'] if name.endswith(suffix)]
        assert len(names) == 1
        assert hashlib.sha256((base.ROOT/names[0]).read_bytes()).hexdigest() == identity['source_file_hashes'][names[0]]
    assert identity['legacy_helper_source_identity'] == legacy.source_identity()
    assert identity['journal_source_identity'] == journal_module.source_identity()
    for suffix in ('EVIDENCE_AUDIT_SPOOL_V3.md', 'EVIDENCE_AUDIT_SPOOL_V2.md',
                   'evidence_audit_spool_v2.py'):
        assert any(key.endswith(suffix) for key in identity['source_file_hashes'])
    assert identity['journal_schema_version'] == journal_module.VERSION


def test_post_joint_bundle_mutation_is_rejected(tmp_path):
    bundle, cutoff, pack = fixture_market(4)
    stream = FixtureMarketStream(bundle, cutoff, pack)
    def terminal():
        object.__setattr__(bundle, 'observation_date', bundle.observation_date-timedelta(days=1))
    with pytest.raises(ValueError, match='TEST_FIXTURE_STREAM_BINDING_OR_ORIGINAL_BYTES_CHANGED'):
        streamed.run_theme_evolution_stream(bundle, cutoff=cutoff, market_stream=stream,
            evidence_directory=tmp_path/'terminal-bundle-mutation', _source_terminal_validator=terminal)


def test_post_joint_source_identity_mutation_is_rejected(tmp_path, monkeypatch):
    bundle, cutoff, pack = fixture_market(4)
    original = streamed.source_identity
    def terminal():
        changed = deepcopy(original())
        changed['source_hash'] = '0'*64
        monkeypatch.setattr(streamed, 'source_identity', lambda: changed)
    with pytest.raises(ValueError, match='INPUT_SOURCE_OR_DESCRIPTOR_CHANGED_DURING_TERMINAL'):
        streamed.run_theme_evolution_stream(bundle, cutoff=cutoff,
            market_stream=FixtureMarketStream(bundle, cutoff, pack), evidence_directory=tmp_path/'terminal-code-mutation',
            _source_terminal_validator=terminal)


def test_all_envelope_format_rejections_stay_original_unknown(tmp_path):
    bundle, cutoff, _ = fixture_market(4)
    kwargs = dict(cutoff=cutoff, coverage_type='TEST_FIXTURE', sessions=bundle.sessions,
        observation_date=bundle.observation_date)
    plain = EvidenceResolver(bundle.receipts, **kwargs)
    resolver = streamed._RecordingResolver(bundle.receipts, evidence_directory=tmp_path/'envelope-only', **kwargs)
    malformed = bundle.bars[0].model_dump(mode='json')
    malformed['session'] = 'INVALID_DATE'
    assert resolver.resolve(malformed, require_session=True) == plain.resolve(malformed, require_session=True)
    assert resolver.resolve(malformed, require_session=True).reason_codes == ('INVALID_EVIDENCE_ENVELOPE',)
    assert len(resolver.rows) == 0
    resolver.close()


def test_current_cohort_outside_model_remains_full_members_unknown(tmp_path):
    bundle, cutoff, pack = fixture_reference_chain()
    theme = bundle.themes[-1].model_copy(update={'member_ids': (*IDS[:2], '900923.SH', '200026.SZ', '920030.BJ')})
    bundle = bundle.model_copy(update={'themes': (theme,)})
    pack['source_input_hash'] = digest_market_input(bundle)
    _, result, _ = paired(tmp_path, bundle, cutoff, pack)
    today = result['items'][-1]
    assert today['member_ids'] == sorted(theme.member_ids)
    assert today['membership_count'] == 5
    assert today['stage'] == 'UNKNOWN'
    assert 'THEME_CURRENT_COHORT_SECURITY_SCOPE_UNVERIFIED' in today['reason_codes']


def test_measured_full67_joint_reads_reduce_old_close_digest_audit(tmp_path, monkeypatch):
    bundle, cutoff, pack = fixture_market()
    assert len(bundle.sessions) == 67 and bundle.coverage_type == 'TEST_FIXTURE'
    meter = {'bytes': 0, 'calls': 0}
    old_root = tmp_path/'metered-v1'
    original_open = Path.open
    class ReadMeter:
        def __init__(self, handle):
            self.handle = handle
        def read(self, *args):
            raw = self.handle.read(*args); meter['bytes'] += len(raw); meter['calls'] += 1
            return raw
        def __getattr__(self, name):
            return getattr(self.handle, name)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return self.handle.__exit__(*args)
    def measured(path, mode='r', *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        return ReadMeter(handle) if path.parent == old_root and 'r' in mode else handle
    monkeypatch.setattr(Path, 'open', measured)
    old = legacy.run_theme_evolution_stream(bundle, cutoff=cutoff,
        market_stream=FixtureMarketStream(bundle, cutoff, pack), evidence_directory=old_root)
    new = streamed.run_theme_evolution_stream(bundle, cutoff=cutoff,
        market_stream=FixtureMarketStream(bundle, cutoff, pack), evidence_directory=tmp_path/'metered-v3')
    actual = new['execution_provenance']['journal_read_metrics']['actual_returned_read_bytes']
    assert old['items'] == new['items'] and old['effective_input_hash'] == new['effective_input_hash']
    assert meter['bytes'] > actual > 0
    print('TEST_FIXTURE_FULL67_JOURNAL_READS=' + json.dumps({'v1_actual_returned_read_bytes': meter['bytes'],
        'v3_physical_and_logical_actual_returned_read_bytes': actual, 'rows': new['execution_provenance']['audit_manifest']['row_count'],
        'dependency_hash': new['effective_input_hash'], 'scope': 'JOURNAL_ONLY_NOT_REAL_MARKET_WORKER_BUDGET'}))


def test_serialized_logical_budget_flag_cannot_replace_live_callback(tmp_path):
    bundle, cutoff, pack = fixture_market(4)
    with pytest.raises(ValueError, match='LOGICAL_READ_CALLBACK_REQUIRED'):
        streamed.run_theme_evolution_stream(bundle, cutoff=cutoff,
            market_stream=FixtureMarketStream(bundle, cutoff, pack),
            evidence_directory=tmp_path/'never-logical', _logical_read_callback={'verified': True})
    assert not (tmp_path/'never-logical').exists()


def test_non_fixture_logical_accounting_required_before_journal(tmp_path, monkeypatch):
    bundle, cutoff, pack = fixture_market(4)
    stream = FixtureMarketStream(bundle, cutoff, pack)
    fixture_binding = streamed._binding(bundle, cutoff, stream, digest_market_input(bundle))
    production = bundle.model_copy(update={'coverage_type': 'PROSPECTIVE_CAPTURED'})
    # Isolated negative only: no capability is issued or accepted for a run.
    # Stop before creating a journal or reading a panel.
    monkeypatch.setattr(streamed, '_binding', lambda *a, **k: fixture_binding)
    with pytest.raises(ValueError, match='LIVE_LOGICAL_READ_ACCOUNTING_REQUIRED'):
        streamed.run_theme_evolution_stream(production, cutoff=cutoff, market_stream=stream,
            evidence_directory=tmp_path/'never-production', _source_terminal_validator=lambda: None)
    assert not (tmp_path/'never-production').exists()
    assert stream.calls == []


def test_actual_logical_callback_bytes_complete_and_not_source_authority(tmp_path, monkeypatch):
    bundle, cutoff, pack = fixture_market(4)
    instances = capture_resolver(monkeypatch)
    actual = {'bytes': 0, 'calls': 0}
    decoded_sizes = []
    original = journal_module._RowJournal._logical_returned
    def decoded(journal, raw):
        decoded_sizes.append(len(raw))
        return original(journal, raw)
    monkeypatch.setattr(journal_module._RowJournal, '_logical_returned', decoded)
    def charge(raw):
        assert isinstance(raw, bytes) and raw
        actual['bytes'] += len(raw)
        actual['calls'] += 1
    result = streamed.run_theme_evolution_stream(bundle, cutoff=cutoff,
        market_stream=FixtureMarketStream(bundle, cutoff, pack),
        evidence_directory=tmp_path/'actual-logical', _logical_read_callback=charge)
    metrics = result['execution_provenance']['journal_read_metrics']
    assert actual['bytes'] == metrics['actual_logical_decompressed_returned_bytes'] > 0
    assert actual['bytes'] == sum(decoded_sizes)
    assert actual['calls'] == sum(size > 0 for size in decoded_sizes) > 0
    assert metrics['actual_logical_decode_calls'] == len(decoded_sizes)
    assert metrics['actual_returned_read_bytes'] == (metrics['actual_physical_returned_read_bytes'] + actual['bytes'])
    assert result['execution_provenance']['logical_read_callback_role'] == 'ACCOUNTING_ONLY_NOT_SOURCE_CAPABILITY'
    assert result['execution_provenance']['logical_read_accounting_status'] == 'LIVE_CALLBACK_COMPLETED'
    assert result['execution_provenance']['outer_original_registry_authentication'] == 'CALLER_RESPONSIBILITY_NOT_CERTIFIED_BY_THIS_MODULE'
    assert instances[0].rows.closed


def test_logical_valueerror_budget_original_fatal_through_resolver(tmp_path, monkeypatch):
    bundle, cutoff, pack = fixture_market(4)
    instances = capture_resolver(monkeypatch)
    primary = ValueError('TEST_FIXTURE_ACTUAL_LOGICAL_READ_BUDGET')
    calls = []
    def charge(raw):
        calls.append(len(raw))
        raise primary
    with pytest.raises(ValueError) as caught:
        streamed.run_theme_evolution_stream(bundle, cutoff=cutoff,
            market_stream=FixtureMarketStream(bundle, cutoff, pack),
            evidence_directory=tmp_path/'partial-logical', _logical_read_callback=charge)
    assert caught.value is primary and calls
    assert instances[0].rows.closed
    assert primary.theme_stream_journal_read_metrics['actual_logical_decompressed_returned_bytes'] == sum(calls)
    assert any(row['phase'] == 'THEME_STREAM_PRIMARY' for row in primary.theme_stream_failure_provenance)
    assert (tmp_path/'partial-logical/record-index.ndjson').exists()
    assert list((tmp_path/'partial-logical').glob('rows-*.ndjson.gzframes'))
    # A fatal decoder budget cannot be resealed as a complete audit.
    manifest = tmp_path/'partial-logical/manifest.json'
    if manifest.exists():
        assert not json.loads(manifest.read_bytes())['complete_allowed_row_audit']
