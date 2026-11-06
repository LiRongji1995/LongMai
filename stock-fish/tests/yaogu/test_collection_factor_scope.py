"""TEST_FIXTURE request reduction and durable failure preservation, offline."""
from copy import deepcopy
import os
from pathlib import Path

import pytest

from stockfish_core.yaogu.collection_factor_scope import plan_factor_requests
from stockfish_core.yaogu.premarket_capture_cache_v1 import (
    CaptureCache, RequestSpec, _preflight_requests)
from tests.yaogu.test_premarket_capture_cache_v1 import FixtureBudget, FixtureExecutor


DAY = '2026-10-09'
A, B, C = 'sh.600001', 'sh.600002', 'sz.000001'


def spec(codes, end=DAY, start='1990-01-01'):
    return RequestSpec('adjustment_factors', dict(codes=codes, start_date=start, end_date=end),
                       end, role='TEST_FIXTURE_FACTOR_REQUEST')


def cache_at(tmp_path, executor=None):
    return CaptureCache(tmp_path / 'TEST_FIXTURE_cache', FixtureBudget(),
        config=dict(allow_test_fixture=True), executor=executor or FixtureExecutor())


def test_reuse_original_superset_and_request_only_uncovered_security(tmp_path):
    executor = FixtureExecutor()
    cache = cache_at(tmp_path, executor)
    old = spec([A, B])
    original = cache.capture_one(old)
    planned = plan_factor_requests(cache, codes=[A, C], observed=DAY, batch_size=1)
    assert [row.key for row in planned] == [old.key, spec([C]).key]
    assert cache.capture_one(planned[0]).receipt_id == original.receipt_id
    cache.capture_one(planned[1])
    assert executor.calls == [('adjustment_factors', old.parameters),
                              ('adjustment_factors', spec([C]).parameters)]


def test_original_partial_batch_is_not_regrouped_or_retried(tmp_path):
    executor = FixtureExecutor(factor_partial=True)
    cache = cache_at(tmp_path, executor)
    old = spec([A, B])
    original = cache.capture_one(old)
    planned = plan_factor_requests(cache, codes=[B], observed=DAY, batch_size=1)
    assert [row.key for row in planned] == [old.key]
    assert cache.capture_one(planned[0]).receipt_id == original.receipt_id
    assert cache.lookup(old).status == 'PARTIAL'
    assert len(executor.calls) == 1
    calls = dict(cache.budget.calls)
    with pytest.raises(ValueError, match='FACTOR_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY'):
        cache.capture_one(spec([B]))
    assert cache.budget.calls == calls
    assert len(executor.calls) == 1


@pytest.mark.parametrize('new', [spec([B]), spec([A, B], end='2026-10-12'),
                                spec([B], start='2026-10-01')])
def test_batch_or_date_change_cannot_retry_partial_request(tmp_path, new):
    executor = FixtureExecutor(factor_partial=True)
    cache = cache_at(tmp_path, executor)
    old = spec([A, B])
    cache.capture_one(old)
    assert cache.factor_overlap_blockers(new)[0]['request_key'] == old.key
    with pytest.raises(ValueError, match='FACTOR_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY'):
        _preflight_requests(cache, [new])
    with pytest.raises(ValueError, match='FACTOR_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY'):
        cache.capture_one(new)
    assert len(executor.calls) == 1


def test_uncommitted_claim_blocks_new_batch_and_preserves_old_failure(tmp_path):
    executor = FixtureExecutor(crash=True)
    cache = cache_at(tmp_path, executor)
    old = spec([A, B])
    with pytest.raises(RuntimeError, match='TEST_FIXTURE_EXECUTOR_DIED'):
        cache.capture_one(old)
    executor.crash = False
    new = spec([B], end='2026-10-12')
    assert cache.factor_overlap_blockers(new)[0]['request_key'] == old.key
    with pytest.raises(ValueError, match='FACTOR_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY'):
        cache.capture_one(new)
    with pytest.raises(ValueError, match='UNFINISHED_CAPTURE_NO_IMPLICIT_RETRY'):
        cache.lookup(old)
    assert len(executor.calls) == 1


def test_unknown_prior_attempt_is_not_assumed_unrelated(tmp_path):
    cache = cache_at(tmp_path)
    attempt = cache.root / 'raw' / ('a' * 64) / 'raw' / 'TEST_FIXTURE_unidentified'
    attempt.mkdir(parents=True)
    (attempt / 'unknown.bin').write_bytes(b'TEST_FIXTURE')
    assert cache.factor_overlap_blockers(spec([A]))[0]['status'] == \
        'UNKNOWN_PRIOR_REQUEST_SCOPE_NO_IMPLICIT_RETRY'
    with pytest.raises(ValueError, match='FACTOR_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY'):
        cache.capture_one(spec([A]))
    assert cache.budget.calls['requests'] == 0


def test_successful_legacy_original_without_claim_is_reused(tmp_path):
    executor = FixtureExecutor()
    cache = cache_at(tmp_path, executor)
    old = spec([A, B])
    record = cache.capture_one(old)
    (Path(record.metadata['capture_attempt_dir']) / 'request-claim.json').unlink()
    reopened = cache_at(tmp_path, executor)
    planned = plan_factor_requests(reopened, codes=[A], observed=DAY, batch_size=1)
    assert [row.key for row in planned] == [old.key]
    assert reopened.factor_overlap_blockers(spec([A])) == []
    assert reopened.capture_one(planned[0]).receipt_id == record.receipt_id
    assert len(executor.calls) == 1


def test_disjoint_codes_or_nonoverlapping_dates_do_not_block(tmp_path):
    executor = FixtureExecutor(factor_partial=True)
    cache = cache_at(tmp_path, executor)
    cache.capture_one(spec([A, B]))
    executor.factor_partial = False
    for new in (spec([C]), spec([A], start='2026-10-10', end='2026-10-12')):
        assert cache.factor_overlap_blockers(new) == []
        assert cache.capture_one(new).status == 'COMPLETE'
    assert len(executor.calls) == 3


def test_prior_success_for_different_end_never_becomes_tail_composition(tmp_path):
    cache = cache_at(tmp_path)
    cache.capture_one(spec([A], end='2026-10-08'))
    planned = plan_factor_requests(cache, codes=[A], observed=DAY, batch_size=50)
    assert [row.parameters for row in planned] == [spec([A]).parameters]


def test_future_original_is_not_hidden_by_new_request_key(tmp_path):
    cache = cache_at(tmp_path)
    record = cache.capture_one(spec([A, B]))
    entries = [deepcopy(record.to_dict())]
    entries[0]['first_seen_at'] = entries[0]['completed_at'] = '2099-01-01T00:00:00+00:00'
    planned = plan_factor_requests(cache, codes=[A], observed=DAY, batch_size=1, entries=entries)
    assert [row.key for row in planned] == [record.to_dict()['request_key']]


def test_empty_factor_scope_creates_no_requests(tmp_path):
    cache = cache_at(tmp_path)
    assert plan_factor_requests(cache, codes=[], observed=DAY, batch_size=50) == []
    assert cache.budget.calls['requests'] == 0


def test_many_factor_batches_read_index_bytes_once_and_terminal_rehashes(tmp_path):
    seeded = cache_at(tmp_path)
    for codes in ([A], [B], [C]):
        seeded.capture_one(spec(codes))
    cache = cache_at(tmp_path)
    index_bytes = sum(path.stat().st_size for path in (cache.root / 'index').glob('*/*.json'))
    assert cache.factor_overlap_blockers(spec([A, B, C])) == []
    assert cache.budget.read_bytes == index_bytes
    for offset in range(60):
        # Distinct new batch scopes still inspect the complete ledger/attempt
        # directory inventory; already authenticated index bytes are reused.
        assert cache.factor_overlap_blockers(spec(['sh.' + str(600100+offset)])) == []
        plan_factor_requests(cache, codes=[A, B], observed=DAY, batch_size=1)
    assert cache.budget.read_bytes == index_bytes
    cache.terminal()
    assert cache.budget.read_bytes == 2 * index_bytes
    reopened = cache_at(tmp_path)
    assert reopened.factor_overlap_blockers(spec([A, B, C])) == []
    assert reopened.budget.read_bytes == index_bytes


def test_same_bytes_index_replacement_rereads_and_accepts_content(tmp_path):
    cache = cache_at(tmp_path)
    cache.capture_one(spec([A]))
    cache._factor_ledger_entries()
    index = next((cache.root / 'index').glob('*/*.json'))
    data = index.read_bytes()
    replacement = tmp_path / 'TEST_FIXTURE_replacement.json'
    replacement.write_bytes(data)
    os.replace(replacement, index)
    before = cache.budget.read_bytes
    assert cache.factor_overlap_blockers(spec([A, B])) == []
    assert cache.budget.read_bytes == before + len(data)
    cache.terminal()


def test_changed_or_deleted_memoized_index_is_not_reused(tmp_path):
    cache = cache_at(tmp_path)
    cache.capture_one(spec([A]))
    cache._factor_ledger_entries()
    index = next((cache.root / 'index').glob('*/*.json'))
    data = index.read_bytes()
    index.write_bytes(data.replace(b'COMPLETE', b'PARTIAL_', 1))
    with pytest.raises(ValueError, match='ORIGINAL_7FP_OR_SHA_CHANGED'):
        cache.factor_overlap_blockers(spec([A, B]))
    index.unlink()
    with pytest.raises(ValueError, match='CACHE_FACTOR_LEDGER_BOUND_ENTRY_REMOVED'):
        cache.factor_overlap_blockers(spec([A, B]))


def test_new_failure_after_ledger_memo_is_discovered(tmp_path):
    observer = cache_at(tmp_path)
    assert observer.factor_overlap_blockers(spec([B])) == []
    writer = cache_at(tmp_path, FixtureExecutor(factor_partial=True))
    prior = spec([A, B])
    writer.capture_one(prior)
    assert observer.factor_overlap_blockers(spec([B]))[0]['request_key'] == prior.key
    with pytest.raises(ValueError, match='FACTOR_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY'):
        observer.capture_one(spec([B]))
    assert observer.budget.calls['requests'] == 0


def test_new_uncommitted_claim_after_ledger_memo_is_discovered(tmp_path):
    observer = cache_at(tmp_path)
    assert observer.factor_overlap_blockers(spec([B])) == []
    writer = cache_at(tmp_path, FixtureExecutor(crash=True))
    prior = spec([A, B])
    with pytest.raises(RuntimeError, match='TEST_FIXTURE_EXECUTOR_DIED'):
        writer.capture_one(prior)
    assert observer.factor_overlap_blockers(spec([B]))[0]['request_key'] == prior.key
    assert observer.budget.calls['requests'] == 0


def test_factor_memo_does_not_replace_original_source_terminal_hash(tmp_path):
    cache = cache_at(tmp_path)
    record = cache.capture_one(spec([A]))
    cache._factor_ledger_entries()
    raw = Path(record.metadata['raw']['path'])
    raw.write_bytes(raw.read_bytes() + b' ')
    assert cache.factor_overlap_blockers(spec([A, B])) == []
    with pytest.raises(ValueError, match='ORIGINAL|SHA_CHANGED|FILE_BOUND'):
        cache.terminal()
