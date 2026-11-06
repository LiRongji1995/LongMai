"""TEST_FIXTURE: optional enrichment cannot issue provider requests."""
from datetime import datetime, timedelta
from unittest.mock import patch

from stockfish_core.yaogu import premarket_capture_cache_v1 as capture
from tests.yaogu.test_premarket_capture_cache_v1 import FixtureBudget, FixtureExecutor


def setup_context(tmp_path):
    cache = capture.CaptureCache(tmp_path/'TEST_FIXTURE-cache', FixtureBudget(),
        config={'allow_test_fixture': True}, executor=FixtureExecutor())
    observed = datetime.now(capture.SH).date()-timedelta(days=1)
    sessions = tuple(str(observed-timedelta(days=66-i)) for i in range(67))
    collection = capture.CaptureCollection(str(observed), str(observed+timedelta(days=1)),
        sessions, sessions[-25:], (), (), (), (), (), {}, True)
    ids = ['600001.SH', '000001.SZ']
    run = {'results': {'SHORT_HORIZON_STRENGTH_R2': dict(eligible_pool_ids=ids,
        items=[dict(instrument_id=iid, research_selected=True) for iid in ids],
        research_selected_ids=ids)}}
    return cache, collection, run


def seed_nodes(cache):
    day = str(datetime.now(capture.SH).date())
    for endpoint in ('industry_list_sina', 'concept_list_sina'):
        parent = cache.capture_one(capture.RequestSpec(endpoint, {}, day, current=True))
        for row in cache.verify(parent)['rows']:
            cache.capture_one(capture.RequestSpec(endpoint.replace('list', 'members'),
                {'symbol': row['label']}, day, current=True))


def test_zero_admitted_selection_does_not_even_read_optional_sources(tmp_path):
    cache, collection, run = setup_context(tmp_path)
    with patch.object(cache, 'lookup', side_effect=AssertionError('TEST_FIXTURE_NO_CONTEXT_READ')):
        result = capture.capture_theme_sources(cache, core_run=run, collection=collection, target_ids=[])
    assert result.parent_acquisition['original_selected_count'] == 2
    assert result.parent_acquisition['context_ready'] is False
    assert result.parent_acquisition['missing_source_is_not_empty'] is True
    assert cache.executor.calls == []


def test_cold_context_is_unknown_without_directory_or_member_requests(tmp_path):
    cache, collection, run = setup_context(tmp_path)
    result = capture.capture_theme_sources(cache, core_run=run, collection=collection,
        target_ids=['600001.SH'])
    assert cache.executor.calls == []
    assert result.parent_acquisition['network_collection_allowed'] is False
    assert result.parent_acquisition['context_ready'] is False
    assert [row['status'] for row in result.parent_acquisition['records']] == ['UNKNOWN_NOT_COLLECTED']*2


def test_cached_nodes_only_trigger_history_reads_for_admitted_members(tmp_path):
    cache, collection, run = setup_context(tmp_path)
    seed_nodes(cache)
    calls = list(cache.executor.calls)
    result = capture.capture_theme_sources(cache, core_run=run, collection=collection,
        target_ids=['000001.SZ'])
    assert result.requested_industry_nodes == result.requested_concept_symbols == ()
    assert len(result.parent_acquisition['records']) == 3
    result = capture.capture_theme_sources(cache, core_run=run, collection=collection,
        target_ids=['600001.SH'])
    assert len(result.requested_industry_nodes) == 2
    assert len(result.requested_concept_symbols) == 1
    missing = [row for row in result.parent_acquisition['records'] if row['status'] == 'UNKNOWN_NOT_COLLECTED']
    assert len(missing) == 84
    assert result.parent_acquisition['context_ready'] is False
    assert cache.executor.calls == calls


def test_complete_cached_history_keeps_whole_nodes_and_original_denominator(tmp_path):
    cache, collection, run = setup_context(tmp_path)
    seed_nodes(cache)
    for day in collection.sessions[:-25]:
        for endpoint in ('market_daily', 'master_all'):
            cache.capture_one(capture.RequestSpec(endpoint, {'date': day}, day))
    calls = list(cache.executor.calls)
    result = capture.capture_theme_sources(cache, core_run=run, collection=collection,
        target_ids=['600001.SH'])
    assert cache.executor.calls == calls
    assert result.parent_acquisition['context_ready'] is True
    assert result.parent_acquisition['original_selected_count'] == 2
    assert result.parent_acquisition['admitted_selected_count'] == 1
    assert result.parent_acquisition['status'] == 'CACHE_ONLY_PARTIAL_OR_UNKNOWN'
    assert len(result.context_captures) == 89
    assert run['results']['SHORT_HORIZON_STRENGTH_R2']['research_selected_ids'] == ['600001.SH', '000001.SZ']
    for row in result.context_captures:
        if row['endpoint'].endswith('members_sina'):
            assert len(cache.verify(row)['rows']) == 1
