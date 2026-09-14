"""TEST_FIXTURE: exact identities and ownership after bounded memoization."""
from copy import deepcopy
import json

from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu import derived_feature_store as dft
from stockfish_core.yaogu import source_resolution as source
from stockfish_core.yaogu.strategy_experiments import _prepare_context
from tests.yaogu.fixtures import make_bundle


def test_overlapping_windows_keep_original_byte_stream_with_gaps_and_conflicts():
    bundle, cutoff = make_bundle(2)
    ctx = _prepare_context(bundle, cutoff)
    ids, targets = list(ctx.eligible_ids), ctx.sessions[-3:]
    view = source.canonical_price_source_view(ctx, ctx.sessions, ids)
    # Unusual provenance is part of the hash bytes, including Unicode.
    for token in view['tokens'].values():
        token['provenance'] = {**token['provenance'], 'TEST_FIXTURE_中文': '来源 é'}
    keys = sorted(view['tokens'])
    view['tokens'][keys[-2]].update(status='MISSING', row_hash='MISSING_VISIBLE_BAR')
    view['tokens'][keys[-3]].update(status='CONFLICT', conflicting_row_hashes=['a', 'b'])
    actual = dft.dependency_plan_for_context(ctx, targets, ids, source_view=view)
    expected = {}
    for iid in ids:
        for session in targets:
            text = str(session)
            expected[(iid, text)] = {family: dft._dependency_identity(ctx, family, iid, text,
                dft._source_session_range(ctx, text, family), view, view['source_mode'])
                for family in dft.MATERIALIZED_FAMILIES}
    assert actual == expected
    assert dft._json(list(actual.values())) == dft._json(list(expected.values()))
    work = view['dependency_work_counters']
    assert work['token_serializations'] < work['window_token_visits']


def test_cleanup_releases_context_ownership_without_erasing_retained_view(tmp_path, monkeypatch):
    bundle, cutoff = make_bundle(2)
    ctx = _prepare_context(bundle, cutoff)
    retained = []
    original = source.canonical_price_source_view
    def remember(*args, **kwargs):
        view = original(*args, **kwargs)
        retained.append((view, deepcopy(view['tokens'])))
        return view
    monkeypatch.setattr(source, 'canonical_price_source_view', remember)
    stats = dft.ensure_stage_aware_derived_features(ctx, dft.DerivedFeatureStore(SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3')), input_hash='TEST_FIXTURE')
    assert stats['built_rows'] > 0
    view, before = retained[0]
    assert view['tokens'] == before and view['selected']
    assert all(value is not view for value in ctx.scalar_cache.values())
    assert stats['source_work_counters']['provenance_reuses'] > 0


def test_same_receipt_id_changed_content_does_not_reuse_another_context_provenance():
    bundle, cutoff = make_bundle(1)
    ctx = _prepare_context(bundle, cutoff)
    first = source.canonical_price_source_view(ctx, ctx.sessions, ctx.eligible_ids)
    receipts = tuple(receipt.model_copy(update={'content_hash': 'f'*64}) for receipt in bundle.receipts)
    changed = bundle.model_copy(update={'receipts': receipts})
    other = _prepare_context(changed, cutoff)
    second = source.canonical_price_source_view(other, other.sessions, other.eligible_ids)
    key = next(key for key, token in first['tokens'].items() if token['status'] == 'SELECTED')
    assert first['tokens'][key]['provenance'] != second['tokens'][key]['provenance']


def test_chunk_selection_only_copies_requested_scope():
    bundle, cutoff = make_bundle(4)
    ctx = _prepare_context(bundle, cutoff)
    view = source.canonical_price_source_view(ctx, ctx.sessions, ctx.eligible_ids)
    iid, session = ctx.eligible_ids[0], ctx.sessions[-1]
    selected = dft._selected_compact_bars(ctx, [session], [iid], source_view=view)
    assert set(selected) == {(iid, str(session))}
    assert selected[(iid, str(session))] == view['selected'][(iid, str(session))]


def test_batched_pipeline_keeps_exact_cache_rows_and_lifecycle_across_cold_warm_revision(tmp_path, monkeypatch):
    """Compare the unbatched primitive and bounded pipeline, including real keys."""
    bundle, cutoff = make_bundle(5)
    ids = _prepare_context(bundle, cutoff).eligible_ids
    stores = [dft.DerivedFeatureStore(SQLiteStore(tmp_path / (name + '.sqlite3')))
              for name in ('TEST_FIXTURE_whole', 'TEST_FIXTURE_batched')]
    monkeypatch.setattr(dft, 'SOURCE_BATCH_INSTRUMENTS', 2)
    original = source.canonical_price_source_view
    scopes = []
    def record(ctx, sessions, instruments):
        scopes.append(tuple(instruments))
        return original(ctx, sessions, instruments)
    monkeypatch.setattr(source, 'canonical_price_source_view', record)
    changed_bars = list(bundle.bars)
    changed_bars[-1] = changed_bars[-1].model_copy(update={'amount_cny': changed_bars[-1].amount_cny + 100})
    revised = bundle.model_copy(update={'bars': tuple(changed_bars)})
    for run, current in enumerate((bundle, bundle, revised)):
        left, right = (_prepare_context(current, cutoff) for _ in range(2))
        before = dft._ensure_stage_aware_derived_features_batch(
            left, stores[0], input_hash='TEST_FIXTURE', mode='DAILY_INCREMENTAL', instrument_ids=ids)
        scopes.clear()
        after = dft.ensure_stage_aware_derived_features(right, stores[1], input_hash='TEST_FIXTURE')
        assert list(map(len, scopes)) == [2, 2, 1]
        assert left.scalar_cache[dft.CONTEXT_KEY] == right.scalar_cache[dft.CONTEXT_KEY]
        assert left.resolver.effective_dependencies() == right.resolver.effective_dependencies()
        assert left.rejected_evidence == right.rejected_evidence
        for key in ('family_cache', 'row_lifecycle', 'requested_feature_rows', 'cache_hits',
                    'cache_misses', 'built_rows', 'cache_hit_rate', 'reuse_ratio'):
            assert before[key] == after[key]
        assert not any(isinstance(key, tuple) and key[0] == 'R16_CANONICAL_PRICE_SOURCE_VIEW'
                       for key in right.scalar_cache)
        dumps = []
        for store in stores:
            with store.store.transaction(write=False) as con:
                rows = [dict(row) for row in con.execute('SELECT * FROM yaogu_derived_features_v2 ORDER BY cache_key')]
                for row in rows:
                    row.pop('created_at')
                dumps.append(rows)
        assert dumps[0] == dumps[1]
        if run == 0:
            assert after['new_rows'] == after['requested_feature_rows'] > 0
        elif run == 1:
            assert after['cache_hits'] == after['requested_feature_rows'] and after['built_rows'] == 0
        else:
            assert after['invalidated_rows'] > 0 and after['full_row_reused'] > 0


def test_same_strategy_consumes_identical_features_across_batch_boundaries(tmp_path, monkeypatch):
    from stockfish_core.yaogu.strategy_experiments import run_active_daily_strategy
    bundle, cutoff = make_bundle(5)
    results = []
    for size in (10, 2, 1):
        monkeypatch.setattr(dft, 'SOURCE_BATCH_INSTRUMENTS', size)
        store = dft.DerivedFeatureStore(SQLiteStore(tmp_path / f'TEST_FIXTURE_strategy_{size}.sqlite3'))
        result = run_active_daily_strategy(bundle, cutoff=cutoff, derived_store=store,
                                           execution_profile='DAILY_PRIMARY_FAST')
        results.append({key: result[key] for key in ('effective_input_hash', 'results', 'qualification',
                                                    'data_coverage_diagnostics')})
    assert results[0] == results[1] == results[2]
    assert results[0]['results']['STAGE_AWARE_MOMENTUM_V2']['items']


def test_later_batch_failure_propagates_without_publishing_partial_context(tmp_path, monkeypatch):
    import pytest
    bundle, cutoff = make_bundle(5)
    ctx = _prepare_context(bundle, cutoff)
    monkeypatch.setattr(dft, 'SOURCE_BATCH_INSTRUMENTS', 2)
    original = source.canonical_price_source_view
    calls = []
    def fail_later(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError('TEST_FIXTURE_LATER_BATCH_FAILED')
        return original(*args, **kwargs)
    monkeypatch.setattr(source, 'canonical_price_source_view', fail_later)
    store = dft.DerivedFeatureStore(SQLiteStore(tmp_path / 'TEST_FIXTURE_failure.sqlite3'))
    with pytest.raises(RuntimeError, match='LATER_BATCH_FAILED'):
        dft.ensure_stage_aware_derived_features(ctx, store, input_hash='TEST_FIXTURE')
    assert dft.CONTEXT_KEY not in ctx.scalar_cache
