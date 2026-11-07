"""TEST_FIXTURE request reduction and durable failure preservation, offline."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
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


def completed_recovery(tmp_path, *, completion_executor=None):
    """An already completed explicit retry, confined to TEST_FIXTURE state."""
    prior = spec([A, B])
    failed = cache_at(tmp_path, FixtureExecutor(factor_partial=True)).capture_one(prior)
    writer = CaptureCache(tmp_path / 'TEST_FIXTURE_cache', FixtureBudget(),
        config=dict(allow_test_fixture=True, retry_failed=True),
        executor=completion_executor or FixtureExecutor())
    complete = writer.capture_one(prior)
    assert failed.status == 'PARTIAL' and complete.status == 'COMPLETE'
    return cache_at(tmp_path), prior, failed, complete


def import_fixture_original(cache, record, seed_path):
    value = record.metadata
    seed_path.write_text(json.dumps(dict(trios=[dict(
        {role: value[role] for role in ('raw', 'receipt', 'result')},
        archive_root=value['archive_root'], producer_files=value['producer_files'])])))
    cache.import_seed(seed_path)


class FixedCompletionExecutor(FixtureExecutor):
    def __init__(self, completed_at, **kwargs):
        super().__init__(**kwargs)
        self.completed_at = completed_at

    def __call__(self, endpoint, params, output, **kwargs):
        result = super().__call__(endpoint, params, output, **kwargs)
        result['completed_at'] = self.completed_at
        Path(output, 'request-TEST_FIXTURE.json').write_text(json.dumps(result))
        return result


@pytest.mark.parametrize('new', [spec([B]), spec([A, B], end='2026-10-12')])
def test_authenticated_completed_recovery_clears_overlap_without_rewriting_failure(tmp_path, new):
    cache, prior, failed, complete = completed_recovery(tmp_path)
    retained = {path: path.read_bytes() for path in cache.root.rglob('*') if path.is_file()}
    assert cache.factor_overlap_blockers(new) == []
    assert cache.lookup(prior).receipt_id == complete.receipt_id
    assert cache.lookup(prior).status == 'COMPLETE'
    assert {value['receipt_id'] for value in cache._entries(prior.key)} == {
        failed.receipt_id, complete.receipt_id}
    assert {path: path.read_bytes() for path in cache.root.rglob('*') if path.is_file()} == retained
    assert cache.budget.calls['requests'] == 0 and cache.executor.calls == []
    assert cache.config.get('retry_failed', False) is False
    cache.terminal()


@pytest.mark.parametrize('which', ['partial', 'complete'])
def test_recovery_authenticates_both_old_and_new_originals(tmp_path, which):
    cache, _, failed, complete = completed_recovery(tmp_path)
    record = failed if which == 'partial' else complete
    raw = Path(record.metadata['raw']['path'])
    raw.write_bytes(raw.read_bytes() + b' ')
    with pytest.raises(ValueError, match='SHA_CHANGED|ORIGINAL'):
        cache.factor_overlap_blockers(spec([B]))
    assert cache.budget.calls['requests'] == 0


@pytest.mark.parametrize('which', ['partial', 'complete'])
@pytest.mark.parametrize('damage', ['missing', 'wrong_receipt', 'failed_marker'])
def test_recovery_requires_every_terminal_attempt_binding(tmp_path, which, damage):
    cache, prior, failed, complete = completed_recovery(tmp_path)
    attempt = Path((failed if which == 'partial' else complete).metadata['capture_attempt_dir'])
    commit = attempt / 'committed.json'
    if damage == 'missing':
        commit.unlink()
    elif damage == 'wrong_receipt':
        commit.write_text(json.dumps(dict(request_key=prior.key, receipt_id='0' * 64)))
    else:
        (attempt / 'attempt-failed.json').write_text(json.dumps(dict(status='TEST_FIXTURE_FAILURE')))
    blockers = cache.factor_overlap_blockers(spec([B]))
    assert blockers[0]['request_key'] == prior.key
    assert blockers[0]['status'] == 'OVERLAPPING_UNFINISHED_NO_IMPLICIT_RETRY'
    assert cache.budget.calls['requests'] == 0


def test_new_unknown_attempt_after_completed_recovery_is_still_blocked(tmp_path):
    cache, prior, _, _ = completed_recovery(tmp_path)
    assert cache.factor_overlap_blockers(spec([B])) == []
    extra = cache.root / 'raw' / prior.key / 'raw' / 'TEST_FIXTURE_uncommitted_after_recovery'
    extra.mkdir()
    (extra / 'unknown.bin').write_bytes(b'TEST_FIXTURE')
    assert cache.factor_overlap_blockers(spec([B]))[0]['request_key'] == prior.key
    assert cache.budget.calls['requests'] == 0


def test_complete_index_without_attempt_binding_cannot_resolve_partial(tmp_path):
    cache, prior, _, complete = completed_recovery(tmp_path)
    declaration = complete.metadata
    declaration.pop('capture_attempt_dir')
    cache._commit(declaration)
    assert cache.factor_overlap_blockers(spec([B]))[0]['request_key'] == prior.key
    assert cache.budget.calls['requests'] == 0


def test_a_later_partial_after_complete_remains_blocked(tmp_path):
    cache, prior, _, complete = completed_recovery(tmp_path)
    later = cache_at(tmp_path / 'TEST_FIXTURE_later', FixtureExecutor(factor_partial=True)).capture_one(prior)
    import_fixture_original(cache, later, tmp_path / 'TEST_FIXTURE_later_seed.json')
    assert cache.lookup(prior).status == 'PARTIAL'
    assert cache.lookup(prior).receipt_id != complete.receipt_id
    assert cache.factor_overlap_blockers(spec([B]))[0]['request_key'] == prior.key
    assert cache.budget.calls['requests'] == 0


def test_future_complete_does_not_resolve_prior_partial(tmp_path):
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    cache, _, _, _ = completed_recovery(tmp_path,
        completion_executor=FixedCompletionExecutor(future))
    with pytest.raises(ValueError, match='CACHE_FACTOR_RECOVERY_FUTURE_ORIGINAL'):
        cache.factor_overlap_blockers(spec([B]))
    assert cache.budget.calls['requests'] == 0


def test_same_clock_conflict_does_not_resolve_prior_partial(tmp_path):
    clock = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    prior = spec([A, B])
    cache_at(tmp_path, FixedCompletionExecutor(clock, factor_partial=True)).capture_one(prior)
    writer = CaptureCache(tmp_path / 'TEST_FIXTURE_cache', FixtureBudget(),
        config=dict(allow_test_fixture=True, retry_failed=True), executor=FixedCompletionExecutor(clock))
    writer.capture_one(prior)
    cache = cache_at(tmp_path)
    with pytest.raises(ValueError, match='SAME_CLOCK_CAPTURE_CONFLICT'):
        cache.factor_overlap_blockers(spec([B]))
    assert cache.budget.calls['requests'] == 0


@pytest.mark.parametrize('other', [spec([B]), spec([A, B], end='2026-10-12')])
def test_complete_with_different_batch_or_date_cannot_resolve_old_failure(tmp_path, other):
    cache = cache_at(tmp_path, FixtureExecutor(factor_partial=True))
    prior = spec([A, B])
    cache.capture_one(prior)
    later = cache_at(tmp_path / 'TEST_FIXTURE_other').capture_one(other)
    import_fixture_original(cache, later, tmp_path / 'TEST_FIXTURE_other_seed.json')
    assert cache.factor_overlap_blockers(spec([B], end='2026-10-13'))[0]['request_key'] == prior.key
    assert cache.lookup(prior).status == 'PARTIAL'
    assert cache.budget.calls['requests'] == 1


def inherited_same_key(tmp_path, *, endpoint='adjustment_factors', unknown=True):
    """TEST_FIXTURE complete import with a retained ancestor attempt inventory."""
    from stockfish_core.yaogu.collection_inherited_scope import EPOCH_SCHEMA
    from tests.yaogu.test_collection_inherited_scope import write

    if endpoint == 'adjustment_factors':
        old = spec([A, B])
    elif endpoint == 'calendar':
        old = RequestSpec(endpoint, dict(start_date='2026-10-01', end_date=DAY), DAY)
    else:
        old = RequestSpec(endpoint, {}, datetime.now(timezone(timedelta(hours=8))).date().isoformat(), current=True)
    source_root = tmp_path / 'TEST_FIXTURE_ancestor_cache'
    source = CaptureCache(source_root, FixtureBudget(), config=dict(allow_test_fixture=True),
                          executor=FixtureExecutor(crash=True))
    if unknown:
        with pytest.raises(RuntimeError, match='TEST_FIXTURE_EXECUTOR_DIED'):
            source.capture_one(old)
    source = CaptureCache(source_root, FixtureBudget(),
        config=dict(allow_test_fixture=True, retry_failed=True), executor=FixtureExecutor())
    original = source.capture_one(old)
    target = CaptureCache(tmp_path / 'TEST_FIXTURE_imported_cache', FixtureBudget(),
        config=dict(allow_test_fixture=True), executor=FixtureExecutor())
    value = original.metadata
    write(target.root / 'epoch-seed.json', dict(schema_version=EPOCH_SCHEMA, sources=[dict(
        {role: value[role] for role in ('raw', 'receipt', 'result')}, archive_root=value['archive_root'])]))
    target.import_seed(target.root / 'epoch-seed.json')
    imported = next(target._entries())
    assert imported['request_key'] == old.key and imported['request_spec']['role'] == 'IMPORTED_ORIGINAL'
    clock, plan = datetime.now(timezone.utc).isoformat(), 'f' * 64
    write(target.root / 'epoch-claim.json', dict(schema_version=EPOCH_SCHEMA, source_cache=str(source.root),
        plan_sha256=plan, status='STARTED_NO_IMPLICIT_RETRY', actual_started_at=clock))
    write(target.root / 'epoch-complete.json', dict(schema_version=EPOCH_SCHEMA, source_cache=str(source.root),
        cache_root=str(target.root), plan_sha256=plan, actual_completed_at=clock,
        status='CURRENT_ORIGINALS_RECERTIFIED_NEW_METADATA_EPOCH', network_calls=0, new_market_data=False,
        old_records_rewritten=False, source_clocks_rewritten=False, request_key_mappings=[dict(
            old_request_key=old.key, current_request_keys=[old.key], receipt_id=original.receipt_id)]))
    folder = target.root / 'raw' / old.key
    write(folder / 'inherited-original-attempt/request-claim.json', dict(schema_version=EPOCH_SCHEMA,
        request_key=old.key, indexed=True, source_attempt_directory=str(source.root / 'raw' / old.key),
        source_plan_sha256=plan, status='INHERITED_ORIGINAL_ATTEMPT_NO_IMPLICIT_RETRY'))
    target = CaptureCache(target.root, FixtureBudget(), config=dict(allow_test_fixture=True),
                          executor=FixtureExecutor())
    return target, source, old, original, folder


@pytest.mark.parametrize('new', [spec([B]), spec([A, B], end='2026-10-12'),
                               spec([B], start='2026-10-01', end='2026-10-12')])
def test_imported_complete_same_key_cannot_hide_ancestor_unknown_for_new_overlap(tmp_path, new):
    cache, source, old, original, folder = inherited_same_key(tmp_path)
    before = {path: path.read_bytes() for root in (cache.root, source.root)
              for path in root.rglob('*') if path.is_file()}
    # Reusing this exact authenticated complete capture makes no request.
    assert cache.capture_one(old).receipt_id == original.receipt_id
    blockers = cache.factor_overlap_blockers(new)
    assert len(blockers) == 1 and blockers[0]['request_key'] == old.key
    assert blockers[0]['status'] == 'OVERLAPPING_UNFINISHED_NO_IMPLICIT_RETRY'
    assert RequestSpec(**blockers[0]['request_spec']).key == old.key
    assert blockers[0]['inherited_scope']['status'] == 'UNKNOWN_RETAINED_NO_IMPLICIT_RETRY'
    with pytest.raises(ValueError, match='FACTOR_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY'):
        _preflight_requests(cache, [new])
    with pytest.raises(ValueError, match='FACTOR_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY'):
        cache.capture_one(new)
    assert cache.budget.calls['requests'] == 0 and cache.executor.calls == []
    assert {path: path.read_bytes() for path in before} == before
    cache.terminal()


@pytest.mark.parametrize('new', [spec([C], end='2026-10-12'),
    spec([A], start='2026-10-10', end='2026-10-12')])
def test_known_ancestor_scope_does_not_block_disjoint_codes_or_dates(tmp_path, new):
    cache, _, _, _, _ = inherited_same_key(tmp_path)
    assert cache.factor_overlap_blockers(new) == []
    assert cache.budget.calls['requests'] == 0


@pytest.mark.parametrize('endpoint', ['calendar', 'mainboard_sse'])
def test_known_nonfactor_ancestor_unknown_is_not_a_global_factor_blocker(tmp_path, endpoint):
    cache, _, _, _, _ = inherited_same_key(tmp_path, endpoint=endpoint)
    assert cache.factor_overlap_blockers(spec([A], end='2026-10-12')) == []
    assert cache.budget.calls['requests'] == 0


def test_inventory_only_nonfactor_scope_does_not_clear_unknown_guard(tmp_path):
    from stockfish_core.yaogu.collection_inherited_scope import inherited_attempt_scope
    from tests.yaogu.test_collection_inherited_scope import make_epoch
    cache, _, old, _, _, folder = make_epoch(tmp_path, endpoint='lhb_summary', failed=True)
    inventory = inherited_attempt_scope(cache, folder, retain_known_terminal_failures=True)
    assert inventory['status'] == 'VERIFIED_RETAINED_ORIGINAL'
    assert inventory['inventory_only'] is True and inventory['retry_authorized'] is False
    blockers = cache.factor_overlap_blockers(spec([A], end='2026-10-12'))
    assert blockers[0]['request_key'] == old.key
    assert blockers[0]['status'] == 'UNKNOWN_PRIOR_REQUEST_SCOPE_NO_IMPLICIT_RETRY'
    assert blockers[0]['request_spec'] is None
    assert cache.budget.calls['requests'] == 0


def test_complete_inherited_inventory_is_allowed_but_new_ancestor_attempt_is_seen(tmp_path):
    from tests.yaogu.test_collection_inherited_scope import write
    cache, source, old, _, _ = inherited_same_key(tmp_path, unknown=False)
    new = spec([A], end='2026-10-12')
    assert cache.factor_overlap_blockers(new) == []
    extra = source.root / 'raw' / old.key / 'raw' / 'TEST_FIXTURE_later_unknown'
    write(extra / 'request-claim.json', dict(schema_version='PREMARKET_CAPTURE_CACHE_V1',
        request_spec=old.to_dict(), actual_started_at=datetime.now(timezone.utc).isoformat(), retries=0))
    assert cache.factor_overlap_blockers(new)[0]['request_key'] == old.key
    assert cache.budget.calls['requests'] == 0
