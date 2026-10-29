"""TEST_FIXTURE exact theme/index parity; no network, DB or market claims."""
from copy import deepcopy
from datetime import timedelta
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import codex_theme_grouped_v1 as grouped
from stockfish_core.yaogu import theme_evolution_streaming_v2 as original
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.history_artifacts import digest_market_input
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, BudgetExceeded
from tests.yaogu.test_theme_evolution_r1 import fixture_market, fixture_reference_chain
from tests.yaogu.test_theme_evolution_streaming import FixtureMarketStream


@pytest.fixture(autouse=True)
def forbid_external_io(monkeypatch):
    import socket
    import sqlite3
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_NETWORK_DB_FORBIDDEN')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)


def _pair(tmp_path, bundle, cutoff, pack, **options):
    traces, resolvers = [], []
    class Recording(original._RecordingResolver):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.trace = []
            traces.append(self.trace)
            resolvers.append(self)
        def resolve(self, obj, **kwargs):
            value = super().resolve(obj, **kwargs)
            self.trace.append((digest(obj), deepcopy(kwargs), value))
            return value
    before = dict(original.run_theme_evolution_stream.__globals__)
    with patch.object(original, '_RecordingResolver', Recording):
        expected = original.run_theme_evolution_stream(bundle, cutoff=cutoff,
            market_stream=FixtureMarketStream(bundle, cutoff, pack),
            evidence_directory=tmp_path/'reference', **options)
        actual = grouped.run_theme_evolution_stream(bundle, cutoff=cutoff,
            market_stream=FixtureMarketStream(bundle, cutoff, pack),
            evidence_directory=tmp_path/'grouped', **options)
    assert original.run_theme_evolution_stream.__globals__ == before
    assert len(traces) == 2 and traces[0] == traces[1]
    for key in ('items', 'source_input_hash', 'effective_input_hash', 'source_refs',
                'computed_exchange_sessions', 'observation_date', 'decision_cutoff',
                'coverage_type', 'requested_output_scope', 'requested_history_scope',
                'membership_mode', 'historical_membership_verified', 'status',
                'official_pit_validation_countable', 'parameter_status', 'market_edge'):
        assert actual[key] == expected[key], key
    assert actual['source_hash'] != expected['source_hash']
    assert actual['implementation_version'] == grouped.VERSION
    assert actual['context_content_hash'] != expected['context_content_hash']
    for key in ('complete_allowed_row_audit', 'joint_validation', 'journal_read_metrics',
                'source_terminal_callback_status', 'missing_sessions_not_compressed'):
        assert actual['execution_provenance'][key] == expected['execution_provenance'][key]
    assert list(resolvers[0].effective_dependencies()['records']) == list(resolvers[1].effective_dependencies()['records'])
    assert resolvers[0].effective_dependencies()['sources'] == resolvers[1].effective_dependencies()['sources']
    return expected, actual


@pytest.mark.parametrize('mode', ['DATED_ONLY', 'CURRENT_COHORT_BACKVIEW'])
def test_full_67_exact_values_and_resolver_side_effect_order(tmp_path, mode):
    bundle, cutoff, pack = fixture_reference_chain()
    templates = bundle.themes
    themes = tuple(row.model_copy(update={'theme_id': name, 'name': 'TEST_FIXTURE'})
                   for name in ('ZZZ_TEST_FIXTURE', 'AAA_TEST_FIXTURE', 'MMM_TEST_FIXTURE')
                   for row in templates)
    bundle = bundle.model_copy(update={'themes': themes})
    pack['source_input_hash'] = digest_market_input(bundle)
    _, result = _pair(tmp_path, bundle, cutoff, pack, membership_mode=mode, history_scope='LATEST_67')
    assert len(result['computed_exchange_sessions']) == 67
    assert len(result['items']) == 201
    metrics = result['execution_provenance']['theme_candidate_index']
    assert metrics['group_count'] == 3
    assert metrics['candidate_group_lookups'] == 201
    assert metrics['candidate_rows_visited'] == 67 * len(themes)


@pytest.mark.parametrize('case', ['missing', 'late', 'partial', 'conflicting_price', 'late_cohort'])
def test_unknown_and_late_scopes_preserve_all_dependencies(tmp_path, case):
    bundle, cutoff, pack = fixture_market(6)
    if case == 'missing':
        pack['sessions'].pop(-2)
    elif case == 'late':
        pack['receipts'][-1]['first_seen_at'] = (cutoff + timedelta(minutes=1)).isoformat()
        pack['receipts'][-1]['ingested_at'] = pack['receipts'][-1]['first_seen_at']
    elif case == 'partial':
        pack['receipts'][-1]['status'] = 'PARTIAL'
    elif case == 'conflicting_price':
        row = deepcopy(pack['sessions'][-1]['rows'][0])
        row['comparable_close'] = '999'
        pack['sessions'][-1]['rows'].append(row)
    else:
        bundle = bundle.model_copy(update={'themes': tuple(row.model_copy(update={
            'effective_from': bundle.observation_date + timedelta(days=1)}) for row in bundle.themes)})
        pack['source_input_hash'] = digest_market_input(bundle)
    _pair(tmp_path, bundle, cutoff, pack)


def test_unrecognized_reference_source_rejected_before_output(tmp_path):
    source = Path(original.__file__).read_bytes()
    with pytest.raises(ValueError, match='UNSUPPORTED_REFERENCE_EXECUTION'):
        grouped._compiled(source + b'\n')
    assert not list(tmp_path.iterdir())


def test_reference_comprehension_guard_rejects_another_shape(monkeypatch):
    source = Path(original.__file__).read_bytes().replace(b'row.theme_id == theme_id', b'row.theme_id != theme_id')
    monkeypatch.setattr(grouped, 'REFERENCE_SOURCE_SHA256', hashlib.sha256(source).hexdigest())
    with pytest.raises(ValueError, match='REFERENCE_CANDIDATE_SHAPE'):
        grouped._compiled(source)


def test_structural_index_keeps_original_objects_order_and_detects_keys():
    bundle, _, _ = fixture_market(3)
    index = grouped._ThemeGroups(bundle.themes)
    key = bundle.themes[0].theme_id
    assert index.for_id(key) == bundle.themes
    assert all(a is b for a, b in zip(index.for_id(key), bundle.themes))
    object.__setattr__(bundle.themes[0], 'theme_id', 'TEST_FIXTURE_CHANGED')
    with pytest.raises(ValueError, match='THEME_INDEX_KEYS_CHANGED'):
        index.check()
    with pytest.raises(ValueError, match='THEME_INDEX_KEYS_CHANGED'):
        index.for_id(key)


def test_source_same_bytes_different_inode_fails_full_terminal(tmp_path):
    source = tmp_path/'TEST_FIXTURE_source.py'
    source.write_bytes(b'TEST_FIXTURE')
    _, reference = grouped._read(source)
    replacement = tmp_path/'TEST_FIXTURE_replacement.py'
    replacement.write_bytes(source.read_bytes())
    replacement.replace(source)
    with pytest.raises(ValueError, match='SOURCE_TERMINAL_IDENTITY_CHANGED'):
        grouped._read(source, expected=reference)


def test_group_key_mutation_from_panel_callback_is_not_accepted(tmp_path):
    bundle, cutoff, pack = fixture_market(3)
    class Mutating(FixtureMarketStream):
        def panel(self, day):
            value = super().panel(day)
            if len(self.calls) == 1:
                object.__setattr__(bundle.themes[0], 'theme_id', 'TEST_FIXTURE_MOVED')
            return value
    with pytest.raises(ValueError, match='THEME_INDEX_KEYS_CHANGED|CHANGED'):
        grouped.run_theme_evolution_stream(bundle, cutoff=cutoff,
            market_stream=Mutating(bundle, cutoff, pack), evidence_directory=tmp_path/'grouped')


def test_native_genuine_owner_complete_path_and_late_artifact_mutation(tmp_path):
    from tests.yaogu.test_codex_native_theme_shared_v1 import scenario, read_parent
    from tests.yaogu.test_premarket_native_core_v1 import FixtureBudget
    from stockfish_core.yaogu import premarket_theme_context_v1 as native
    owner, records, sources, cutoff, options, _ = scenario(tmp_path)
    expected = native.attach_theme_context(owner, context_captures=sources, context_cutoff=cutoff,
        output_dir=tmp_path/'reference', budget=FixtureBudget(), **options)
    budget = Budget(dict(max_seconds=180, theme_read_bytes=512*1024**2,
        output_bytes=128*1024**2, self_rss_bytes=2*1024**3), job_dir=tmp_path)
    with budget.phase('theme'), budget.meter_files():
        result = grouped.attach_theme_context_grouped(owner, context_captures=sources, context_cutoff=cutoff,
            output_dir=tmp_path/'grouped', budget=budget, **options)
        assert result.to_dict()['status'] == expected.to_dict()['status'] == 'AVAILABLE', result.to_dict()
        first, second = read_parent(expected), read_parent(result)
        for key in ('items', 'source_refs', 'effective_input_hash', 'source_input_hash', 'computed_exchange_sessions'):
            assert first[key] == second[key]
        assert result.validate() is True
        # Native result validation does not certify the saved parent artifact;
        # the outer supervisor verifies it. Exercise an actual owned journal.
        journal = next(ref for ref in result.validation_references
                       if '/price-journal/' in ref['path'] and not ref['path'].endswith('manifest.json'))
        victim = Path(journal['path'])
        replacement = victim.with_suffix('.TEST_FIXTURE_replacement')
        replacement.write_bytes(victim.read_bytes())
        replacement.replace(victim)
        with pytest.raises((ValueError, BudgetExceeded), match='CHANGED|MISMATCH'):
            result.validate()


def test_native_requires_original_budget_and_genuine_owner(tmp_path):
    with pytest.raises(ValueError, match='ORIGINAL_EXECUTION_BUDGET_REQUIRED'):
        grouped.attach_theme_context_grouped({}, budget=object(), output_dir=tmp_path/'never', context_cutoff='2026-01-01T00:00:00+00:00')
    assert not (tmp_path/'never').exists()
