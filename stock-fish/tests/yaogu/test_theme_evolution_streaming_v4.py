"""Isolated native numerical/source-boundary checks; no real market run."""
import ast
import hashlib
import inspect

import pytest

from stockfish_core.yaogu import theme_evolution as base
from stockfish_core.yaogu import theme_evolution_streaming_v2 as frozen
from stockfish_core.yaogu import theme_evolution_streaming_v3 as old
from stockfish_core.yaogu import theme_evolution_streaming_v4 as new
from stockfish_core.yaogu import evidence_audit_spool_v4 as journal
from stockfish_core.yaogu.history_artifacts import digest_market_input
from tests.yaogu.test_theme_evolution_streaming import FixtureMarketStream
from tests.yaogu.test_theme_evolution_r1 import fixture_market


def _run(module, root, bundle, cutoff, pack, **kwargs):
    return module.run_theme_evolution_stream(bundle, cutoff=cutoff,
        market_stream=FixtureMarketStream(bundle, cutoff, pack),
        evidence_directory=root, **kwargs)


def _assert_native_equal(actual, expected):
    for key in ('items', 'effective_input_hash', 'source_refs', 'source_input_hash',
        'computed_exchange_sessions', 'observation_date', 'decision_cutoff',
        'coverage_type', 'requested_output_scope', 'requested_history_scope',
        'membership_mode', 'data_role', 'historical_membership_verified',
        'official_pit_validation_countable', 'status', 'formal_focus', 'core_pass',
        'parameter_status', 'market_edge'):
        assert actual[key] == expected[key], key
    assert actual['strategy_id'] != expected['strategy_id']
    audit = actual['execution_provenance']['audit_manifest']
    assert audit['complete_allowed_row_audit']
    assert audit['row_count'] == expected['execution_provenance']['audit_manifest']['row_count'] > 0


def test_original_per_session_numeric_and_fsm_ast_is_frozen():
    def daily_loop(module):
        tree = ast.parse(inspect.getsource(module.run_theme_evolution_stream))
        loops = [node for node in ast.walk(tree) if isinstance(node, ast.For)
            and isinstance(node.iter, ast.Call) and getattr(node.iter.func, 'id', None) == 'enumerate']
        assert len(loops) == 1
        return ast.dump(loops[0], include_attributes=False)
    assert daily_loop(new) == daily_loop(old)


@pytest.mark.parametrize('cache_bytes,cache_entries', [(0, 0), (4096, 8)])
def test_full67_exact_fsm_risk_unknown_and_dependencies_match_v2(tmp_path, cache_bytes, cache_entries):
    bundle, cutoff, pack = fixture_market(67)
    assert bundle.coverage_type == 'TEST_FIXTURE'
    original_input = digest_market_input(bundle)
    expected = _run(frozen, tmp_path/'v2', bundle, cutoff, pack, history_scope='LATEST_67')
    actual = _run(new, tmp_path/'v4', bundle, cutoff, pack, history_scope='LATEST_67',
        cache_max_bytes=cache_bytes, cache_max_entries=cache_entries)
    _assert_native_equal(actual, expected)
    assert len(actual['items']) == len(actual['computed_exchange_sessions']) == 67
    assert actual['items'][-1]['stage'] == 'STRENGTHENING'
    assert actual['items'][-1]['high_risk']['prior60_share_p90'] is not None
    assert digest_market_input(bundle) == original_input
    assert actual['execution_provenance']['cache_limits'] == {
        'max_bytes': cache_bytes, 'max_entries': cache_entries}
    assert actual['execution_provenance']['complete_audit_cache_policy'] == 'UNCACHED_ALL_CANONICAL_ROWS'


@pytest.mark.parametrize('mode', ['DATED_ONLY', 'CURRENT_COHORT_BACKVIEW'])
def test_dated_and_current_modes_keep_native_full_rows_and_window_unknown(tmp_path, mode):
    bundle, cutoff, pack = fixture_market(8)
    expected = _run(frozen, tmp_path/'v2', bundle, cutoff, pack, membership_mode=mode)
    actual = _run(new, tmp_path/'v4', bundle, cutoff, pack,
        membership_mode=mode, cache_max_bytes=32768, cache_max_entries=32)
    _assert_native_equal(actual, expected)
    assert actual['items'][-1]['expansion'] is None
    assert actual['items'][-1]['high_risk']['prior60_share_p90'] is None
    # Other complete short-window conditions already disprove high risk;
    # an unknown 60-day axis does not replace that original known FAIL.
    assert actual['items'][-1]['high_risk']['status'] == 'FAIL'


def test_missing_calendar_day_is_retained_and_breaks_confirmation(tmp_path):
    bundle, cutoff, pack = fixture_market(8)
    missing = pack['sessions'][-2]['session']
    pack['sessions'].pop(-2)
    expected = _run(frozen, tmp_path/'v2', bundle, cutoff, pack)
    actual = _run(new, tmp_path/'v4', bundle, cutoff, pack,
        cache_max_bytes=32768, cache_max_entries=32)
    _assert_native_equal(actual, expected)
    yesterday, today = actual['items'][-2:]
    assert yesterday['session'] == today['previous_exchange_session'] == missing
    assert yesterday['stage'] == today['stage'] == 'UNKNOWN'
    assert today['confirmation_states']['strengthening'] == 'UNKNOWN'


def test_actual_decoder_bytes_are_charged_and_terminal_is_live(tmp_path):
    bundle, cutoff, pack = fixture_market(4)
    returned, events = [], []
    def charge(raw):
        assert type(raw) is bytes and raw
        returned.append(len(raw))
    actual = _run(new, tmp_path/'v4', bundle, cutoff, pack,
        cache_max_bytes=32768, cache_max_entries=32,
        _logical_read_callback=charge, _source_terminal_validator=lambda: events.append('live'))
    metrics = actual['execution_provenance']['journal_read_metrics']
    assert events == ['live']
    assert sum(returned) == metrics['actual_logical_decompressed_returned_bytes'] > 0
    assert metrics['actual_returned_read_bytes'] == (
        metrics['actual_physical_returned_read_bytes'] + sum(returned))
    assert actual['execution_provenance']['outer_original_registry_authentication'] == (
        'CALLER_RESPONSIBILITY_NOT_CERTIFIED_BY_THIS_MODULE')


def test_valueerror_decoder_budget_preserves_original_primary_and_partial(tmp_path):
    bundle, cutoff, pack = fixture_market(4)
    primary = ValueError('TEST_FIXTURE_REAL_LOGICAL_BUDGET')
    charged = []
    def charge(raw):
        charged.append(len(raw))
        raise primary
    with pytest.raises(ValueError) as caught:
        _run(new, tmp_path/'partial', bundle, cutoff, pack,
            cache_max_bytes=32768, cache_max_entries=32, _logical_read_callback=charge)
    assert caught.value is primary
    assert primary.theme_stream_journal_read_metrics['actual_logical_decompressed_returned_bytes'] == sum(charged) > 0
    assert any(row['phase'] == 'THEME_STREAM_PRIMARY' for row in primary.theme_stream_failure_provenance)
    assert (tmp_path/'partial').is_dir()


def test_live_terminal_source_mutation_is_rejected_after_joint(tmp_path):
    bundle, cutoff, pack = fixture_market(4)
    stream = FixtureMarketStream(bundle, cutoff, pack)
    def terminal():
        # The fixture owns a deep copy, just as a source owns its originals.
        stream.pack['sessions'][-1]['rows'][0]['close'] = '999'
    with pytest.raises(ValueError, match='ORIGINAL_BYTES_CHANGED'):
        new.run_theme_evolution_stream(bundle, cutoff=cutoff, market_stream=stream,
            evidence_directory=tmp_path/'v4', cache_max_bytes=32768, cache_max_entries=32,
            _source_terminal_validator=terminal)


@pytest.mark.parametrize('slot', ['_source_terminal_validator', '_logical_read_callback'])
def test_serialized_verified_flag_cannot_replace_live_callback(tmp_path, slot):
    bundle, cutoff, pack = fixture_market(4)
    with pytest.raises(ValueError, match='REQUIRED'):
        _run(new, tmp_path/'never', bundle, cutoff, pack, cache_max_bytes=32768,
            cache_max_entries=32, **{slot: {'verified': True}})
    assert not (tmp_path/'never').exists()


def test_non_fixture_requires_live_terminal_and_logical_accounting(tmp_path, monkeypatch):
    bundle, cutoff, pack = fixture_market(4)
    stream = FixtureMarketStream(bundle, cutoff, pack)
    fixture_binding = new._binding(bundle, cutoff, stream, digest_market_input(bundle))
    production = bundle.model_copy(update={'coverage_type': 'PROSPECTIVE_CAPTURED'})
    # A negative check ends before journal/panel access; it issues no capability.
    monkeypatch.setattr(new, '_binding', lambda *a, **kw: fixture_binding)
    with pytest.raises(ValueError, match='PHYSICAL_SOURCE_TERMINAL_REQUIRED'):
        new.run_theme_evolution_stream(production, cutoff=cutoff, market_stream=stream,
            evidence_directory=tmp_path/'no-terminal', cache_max_bytes=32768, cache_max_entries=32,
            _logical_read_callback=lambda raw: None)
    with pytest.raises(ValueError, match='LIVE_LOGICAL_READ_ACCOUNTING_REQUIRED'):
        new.run_theme_evolution_stream(production, cutoff=cutoff, market_stream=stream,
            evidence_directory=tmp_path/'no-accounting', cache_max_bytes=32768, cache_max_entries=32,
            _source_terminal_validator=lambda: None)
    assert stream.calls == []
    assert not (tmp_path/'no-terminal').exists() and not (tmp_path/'no-accounting').exists()


def test_cache_limits_have_no_default_and_invalid_bool_never_creates_journal(tmp_path):
    params = inspect.signature(new.run_theme_evolution_stream).parameters
    assert params['cache_max_bytes'].default is params['cache_max_entries'].default is inspect.Parameter.empty
    bundle, cutoff, pack = fixture_market(4)
    with pytest.raises(ValueError, match='CACHE_BYTES_INVALID'):
        _run(new, tmp_path/'never', bundle, cutoff, pack, cache_max_bytes=True, cache_max_entries=32)
    assert not (tmp_path/'never').exists()


def test_new_spec_journal_and_frozen_numeric_source_identity_are_explicit():
    identity = new.source_identity()
    for name in ('theme_evolution_streaming_v4.py', 'THEME_EVOLUTION_STREAMING_V4.md',
                 'evidence_audit_spool_v4.py', 'EVIDENCE_AUDIT_SPOOL_V4.md',
                 'evidence_audit_spool_v3.py', 'evidence_audit_spool_v2.py'):
        matches = [path for path in identity['source_file_hashes'] if path.endswith(name)]
        assert len(matches) == 1
        assert hashlib.sha256((base.ROOT/matches[0]).read_bytes()).hexdigest() == identity['source_file_hashes'][matches[0]]
    assert identity['numeric_rule_source_identity'] == base.source_identity()
    assert identity['journal_source_identity'] == journal.source_identity()
    assert identity['journal_schema_version'] == journal.VERSION
