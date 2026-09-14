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
