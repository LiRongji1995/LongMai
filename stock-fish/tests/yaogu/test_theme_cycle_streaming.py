"""Independent TEST_FIXTURE streaming execution; no real-market edge claim."""
from copy import deepcopy
from datetime import timedelta
from fractions import Fraction as F
import hashlib
import json

import pytest

from stockfish_core.yaogu import theme_cycle_streaming as streaming
from stockfish_core.yaogu import theme_cycle_validation as legacy
from stockfish_core.yaogu import theme_evolution as theme
from stockfish_core.yaogu.history_artifacts import digest_json_stream, digest_market_input
from stockfish_core.yaogu.research_context_artifacts import SourceArtifactReader, RETURN_BASIS
from stockfish_core.yaogu.theme_market_stream import SourceMarketStream
from tests.yaogu.test_theme_cycle_validation import authentic_panel, _cycle_verifier

PAIR = [['TEST_THEME_A', 'TEST_THEME_B']]
ELIGIBLE = ['TEST_THEME_A', 'TEST_THEME_B']
PROVENANCE = {'scope_basis': 'TEST_FIXTURE_INDEPENDENT_RESEARCH_PAIR_SCOPE'}


def _parent_capability(parent, bundle, cutoff, stream, archive):
    """Fixture-only live object, native code, original archive and input bind."""
    from stockfish_core.yaogu import theme_evolution_streaming as producer
    original_identity = producer.source_identity()
    original_hash = parent['context_content_hash']
    original_archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    original_input_hash = digest_market_input(bundle)
    def verify(value, **binding):
        if (value is not parent or value['context_content_hash'] != original_hash
                or digest_json_stream({key: item for key, item in value.items()
                                       if key != 'context_content_hash'}) != original_hash
                or producer.source_identity() != original_identity
                or binding['market_stream'] is not stream
                or binding['cutoff'] != cutoff
                or digest_market_input(binding['bundle']) != original_input_hash
                or hashlib.sha256(archive.read_bytes()).hexdigest() != original_archive_hash):
            raise ValueError('TEST_FIXTURE_FRESH_NATIVE_PARENT_ARCHIVE_BINDING_FAILED')
    return verify


def _scope_capability(parent, stream):
    def verify(scope, **binding):
        # This fixture proves only its explicit display scope. It deliberately
        # cannot establish any historical mainline opportunity set.
        if (binding['theme_result'] is not parent or binding['market_stream'] is not stream
                or scope != {'eligible_theme_ids': ELIGIBLE, 'background_theme_ids': [],
                    'theme_pairs': PAIR, 'pair_scope_provenance': PROVENANCE}):
            raise ValueError('TEST_FIXTURE_CURRENT_SCOPE_BINDING_FAILED')
    return verify


def _native(data, directory, *, reader=None):
    from stockfish_core.yaogu import theme_evolution_streaming as producer
    bundle, cutoff, original_reader, pack, _, _ = data
    reader = reader or original_reader
    stream = SourceMarketStream.from_reader(reader, bundle, cutoff=cutoff,
        sessions=bundle.sessions, themes=pack['themes'], return_basis=RETURN_BASIS)
    parent = producer.run_theme_evolution_stream(bundle, cutoff=cutoff,
        market_stream=stream, evidence_directory=directory / 'parent-audit',
        membership_mode='CURRENT_COHORT_BACKVIEW', output_history=True,
        history_scope='ALL_OBSERVED_SESSIONS_RESEARCH')
    archive = directory / 'TEST_FIXTURE-native-parent.json'
    archive.write_text(json.dumps(parent, sort_keys=True, separators=(',', ':'), default=str), encoding='utf-8')
    return stream, parent, _parent_capability(parent, bundle, cutoff, stream, archive)


@pytest.fixture(scope='module')
def native_stream(authentic_panel, tmp_path_factory):
    directory = tmp_path_factory.mktemp('TEST_FIXTURE_THEME_STREAMING_CYCLE')
    stream, parent, verifier = _native(authentic_panel, directory)
    return authentic_panel, stream, parent, verifier


def _run(data, directory, **changes):
    fixture, stream, parent, verifier = data
    bundle, cutoff, _, _, _, registry = fixture
    arguments = dict(cutoff=cutoff, market_stream=stream, evidence_directory=directory / 'cycle-audit',
        _theme_result_validator=verifier, _pair_scope_validator=_scope_capability(parent, stream),
        theme_pairs=PAIR, eligible_theme_ids=ELIGIBLE, pair_scope_provenance=PROVENANCE,
        cycle_registry=registry, analysis_at=cutoff + timedelta(minutes=1),
        _cycle_registry_validator=_cycle_verifier)
    arguments.update(changes)
    return streaming.run_theme_cycle_streaming(bundle, parent, **arguments)


def test_streamed_native_cycle_matches_frozen_algorithm_exactly(native_stream, tmp_path):
    fixture, stream, parent, _ = native_stream
    bundle, cutoff, reader, pack, original_parent, registry = fixture
    # The old producer remains unchanged. Its stage labels are authenticated
    # using the original native fixture object, independent of the new parent.
    def original_parent_verifier(value, **binding):
        assert value is original_parent and binding['market_pack'] is pack
        assert binding['bundle'] is bundle and binding['cutoff'] == cutoff
    original = legacy.run_theme_cycle_validation(bundle, original_parent, cutoff=cutoff,
        market_pack=pack, _market_pack_validator=reader.validate_theme_market_pack,
        theme_pairs=PAIR, eligible_theme_ids=ELIGIBLE, pair_scope_provenance=PROVENANCE,
        cycle_registry=registry, analysis_at=cutoff + timedelta(minutes=1),
        _cycle_registry_validator=_cycle_verifier, _theme_result_validator=original_parent_verifier)
    result = _run(native_stream, tmp_path)
    assert result['pairs'] == original['pairs']
    assert result['computed_exchange_sessions'] == original['computed_exchange_sessions']
    assert result['full_observed_calendar'] == list(stream.calendar)
    assert result['parent_fsm_authentication_status'] == 'AUTHENTICATED_BY_NATIVE_CAPABILITY'
    assert result['historical_mainline_eligibility_status'] == 'UNKNOWN'
    assert result['official_pit_validation_count'] == 0 and result['market_edge'] == 'NOT_ESTABLISHED'
    assert result['execution_provenance']['allowed_source_rows_audit']['row_count'] >= len(bundle.sessions) * 4
    assert result['execution_provenance']['price_object_retention'] == 'CURRENT_AND_PRECEDING_SESSION_ONLY'
    assert result['report_content_hash'] == digest_json_stream({key: value for key, value in result.items()
                                                              if key != 'report_content_hash'})


def test_full_market_price_objects_are_released_after_preceding_session(native_stream, tmp_path, monkeypatch):
    import weakref
    stream = native_stream[1]
    original_panel = stream.panel
    held, calls = [], []
    def audited_panel(day):
        # Only preceding selected bars may still be live when a new day starts.
        if len(held) > 1:
            assert all(reference() is None for reference in held[-2])
        panel = original_panel(day)
        calls.append(day)
        held.append([weakref.ref(row) for row in panel.rows] if panel is not None else [])
        return panel
    monkeypatch.setattr(stream, 'panel', audited_panel)
    result = _run(native_stream, tmp_path)
    assert calls == result['computed_exchange_sessions']
    assert all(reference() is None for references in held for reference in references)


def _math_fixture(sessions=100):
    days = ['TEST_FIXTURE_SESSION_%03d' % index for index in range(sessions)]
    rows, markets, returns = {}, {}, {}
    for index, day in enumerate(days):
        for identifier, member in [('A', '600001.SH'), ('B', '000001.SZ')]:
            rows[(identifier, day)] = {'member_ids': [member], 'eligible_member_ids': [member],
                'source_refs': ['TEST_FIXTURE_' + day], 'stage': 'TEST_FIXTURE_STAGE'}
        markets[day] = {'status': 'COMPLETE', 'expected_ids': {'600001.SH', '000001.SZ', '300001.SZ'},
            'trading_ids': {'600001.SH', '000001.SZ', '300001.SZ'}, 'nontrading_ids': set(),
            'amount_cny': F(1000 + index), 'amounts': {'600001.SH': F(200 + index * 2 + index % 11),
                '000001.SZ': F(300 + index * 3 + index % 7), '300001.SZ': F(500 + index * 4 + index % 13)},
            'return_basis': RETURN_BASIS}
        returns[day] = {'600001.SH': F(index % 7, 1000), '000001.SZ': F(index % 11, 1000),
                        '300001.SZ': F(index % 13, 1000)}
    return days, rows, markets, returns


@pytest.mark.parametrize('break_kind', ['none', 'missing_day', 'cohort_change', 'basis_change', 'new_market_ipo'])
def test_exact_window_equivalence_and_uncompressed_breaks(break_kind):
    from collections import deque
    days, rows, markets, returns = _math_fixture()
    broken = days[85]
    if break_kind == 'missing_day':
        markets.pop(broken)
    elif break_kind == 'cohort_change':
        rows[('A', broken)]['member_ids'] = ['300001.SZ']
        rows[('A', broken)]['eligible_member_ids'] = ['300001.SZ']
    elif break_kind == 'basis_change':
        markets[broken]['return_basis'] = 'TEST_FIXTURE_DIFFERENT_BASIS'
    elif break_kind == 'new_market_ipo':
        for day in days[85:]:
            markets[day]['expected_ids'].add('688001.SH')
            markets[day]['trading_ids'].add('688001.SH')
            markets[day]['amounts']['688001.SH'] = F(50)
            returns[day]['688001.SH'] = F(1, 100)
    expected = legacy._daily_pair('A', 'B', days, rows, markets, returns,
                                  retain_complement_ids=False, parent_fsm_authenticated=True)
    observed, prior = [], deque(maxlen=60)
    for day in days:
        row = legacy._daily_pair('A', 'B', [day], rows, {day: markets.get(day)}, {day: returns[day]},
                                 retain_complement_ids=False, parent_fsm_authenticated=True)[0]
        streaming._window_fields(row, prior)
        observed.append(row)
        prior.append(row)
    assert observed == expected
    assert observed[86]['training_sessions'] == days[26:86]  # The missing date was not skipped.
    if break_kind in ('missing_day', 'cohort_change', 'basis_change'):
        assert observed[85]['left_amount_market_residual']['status'] == 'UNKNOWN'
        assert observed[86]['left_amount_market_residual']['status'] == 'UNKNOWN'
    if break_kind == 'new_market_ipo':
        assert observed[85]['market_complement_membership_changed'] is True
        assert observed[85]['left_amount_market_residual']['status'] == 'AVAILABLE'


@pytest.mark.parametrize('field', ['_theme_result_validator', '_pair_scope_validator'])
def test_json_flags_do_not_replace_required_capabilities(native_stream, tmp_path, field):
    with pytest.raises(ValueError, match='CAPABILITY_REQUIRED'):
        _run(native_stream, tmp_path, **{field: None})


@pytest.mark.parametrize('change', ['wrong_identity', 'wrong_cutoff', 'compressed_calendar', 'wrong_members'])
def test_parent_binding_or_native_capability_rejects_forgery(native_stream, tmp_path, change):
    fixture, stream, parent, verifier = native_stream
    forged = deepcopy(parent)
    if change == 'wrong_identity':
        forged['feature_version'] = 'TEST_FIXTURE_WRONG_FEATURE'
    elif change == 'wrong_cutoff':
        forged['decision_cutoff'] = (fixture[1] + timedelta(seconds=1)).isoformat()
    elif change == 'compressed_calendar':
        forged['computed_exchange_sessions'].pop(85)
    else:
        forged['items'][0]['member_ids'] = ['600999.SH']
    forged['context_content_hash'] = digest_json_stream({key: value for key, value in forged.items()
                                                       if key != 'context_content_hash'})
    with pytest.raises(ValueError, match='BINDING_MISMATCH|ARCHIVE_BINDING_FAILED'):
        _run((fixture, stream, forged, verifier), tmp_path)


@pytest.mark.parametrize('source_break', ['missing', 'late_capture'])
def test_authenticated_missing_source_date_stays_unknown_and_calendar_preserved(authentic_panel, tmp_path, source_break):
    bundle, cutoff, reader, _, _, _ = authentic_panel
    missing = str(bundle.sessions[85])
    receipts = []
    for row in reader.receipts:
        if row.dataset == 'market_daily' and row.parameters.get('date') == missing:
            if source_break == 'missing':
                continue
            row = row.model_copy(update={'first_seen_at': cutoff + timedelta(seconds=1),
                                         'ingested_at': cutoff + timedelta(seconds=1)})
        receipts.append(row)
    subset = SourceArtifactReader(receipts, reader._paths)
    stream, parent, verifier = _native(authentic_panel, tmp_path, reader=subset)
    result = _run((authentic_panel, stream, parent, verifier), tmp_path / 'output')
    rows = result['pairs'][0]['observations']
    assert result['computed_exchange_sessions'] == [str(day) for day in bundle.sessions]
    assert rows[85]['session'] == missing and rows[85]['left_amount_cny'] is None
    assert rows[86]['training_sessions'] == [str(day) for day in bundle.sessions[26:86]]
    assert rows[86]['left_amount_market_residual']['status'] == 'UNKNOWN'
    assert result['pairs'][0]['valid_authenticated_emotion_cycle_count'] < 3


def test_illegal_actual_source_clock_cannot_become_current_cohort(authentic_panel, tmp_path):
    bundle, cutoff, reader, pack, _, _ = authentic_panel
    receipts = list(reader.receipts)
    index = next(index for index, row in enumerate(receipts) if row.dataset == 'concept_members')
    receipts[index] = receipts[index].model_copy(update={'first_seen_at': cutoff + timedelta(seconds=1),
                                                       'ingested_at': cutoff + timedelta(seconds=1)})
    altered = SourceArtifactReader(receipts, reader._paths)
    with pytest.raises(ValueError):
        SourceMarketStream.from_reader(altered, bundle, cutoff=cutoff, sessions=bundle.sessions,
            themes=pack['themes'], return_basis=RETURN_BASIS)


def test_calendar_registry_never_becomes_an_emotion_cycle(native_stream, tmp_path):
    registry = deepcopy(native_stream[0][5])
    for row in registry:
        row['interval_kind'] = 'CALENDAR_WINDOW'
    result = _run(native_stream, tmp_path, cycle_registry=registry, _cycle_registry_validator=None)
    assert result['pairs'][0]['valid_authenticated_emotion_cycle_count'] == 0
    assert all(row['independent_emotion_label_authentication_status'] == 'UNKNOWN'
               for row in result['pairs'][0]['intervals'])


def test_source_identity_is_explicit_new_version_and_binds_frozen_formula():
    identity = streaming.source_identity()
    assert identity['strategy_id'] != legacy.STRATEGY_ID
    assert identity['numeric_rule_source_identity'] == legacy.source_identity()
    assert identity['parameters'] == legacy.PARAMETERS
    assert {'stockfish_core/yaogu/theme_cycle_streaming.py', 'stockfish_core/yaogu/theme_cycle_validation.py',
            'stockfish_core/yaogu/theme_evolution_streaming.py', 'stockfish_core/yaogu/theme_market_stream.py',
            'stockfish_core/yaogu/evidence_audit_spool.py'}.issubset(identity['source_file_hashes'])
