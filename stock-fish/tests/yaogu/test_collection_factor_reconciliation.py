"""TEST_FIXTURE exact present-day reconciliation, with no real provider access."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from stockfish_core.yaogu import premarket_capture_cache_v1 as native
from stockfish_core.yaogu.collection_factor_reconciliation import (
    case_from_review, prepare_factor_reconciliation, verify_factor_reconciliation,
    verify_claimed_factor_reconciliation, reconciled_inherited_scope)
from stockfish_core.yaogu.collection_inherited_scope import EPOCH_SCHEMA, inherited_attempt_scope
from tests.yaogu.test_premarket_capture_cache_v1 import FixtureBudget, FixtureExecutor
from tests.yaogu.test_collection_source_continuation import (
    AccountingBudget, Executor, digest, reference, save, now, fixture as master_fixture)


CODES = ['sh.' + str(600100 + i) for i in range(50)]
OBSERVED, TARGET, OLD_END = '2026-11-06', '2026-11-09', '2026-10-23'


def factor(codes=None, end=OBSERVED, start='1990-01-01', **kw):
    return native.RequestSpec('adjustment_factors', dict(codes=codes or [CODES[0]],
        start_date=start, end_date=end), kw.pop('business_session', end), **kw)


def fixture(tmp_path):
    source_root = tmp_path / 'TEST_FIXTURE_ancestor_cache'
    source = native.CaptureCache(source_root, FixtureBudget(), config=dict(allow_test_fixture=True),
                                executor=FixtureExecutor(crash=True))
    old_spec = factor(CODES, OLD_END)
    with pytest.raises(RuntimeError, match='TEST_FIXTURE_EXECUTOR_DIED'):
        source.capture_one(old_spec)
    unknown = next((source.root / 'raw' / old_spec.key / 'raw').iterdir())
    # These four retained bytes are explicitly unauthenticated inventory. Their
    # declared PARTIAL result never becomes source authority or known accounting.
    for filename in ('worker-checkpoint.json', 'raw/retained.json', 'receipt.json', 'result.json'):
        save(unknown / filename, dict(schema_version='TEST_FIXTURE_UNAUTHENTICATED', status='PARTIAL'))
    source = native.CaptureCache(source.root, FixtureBudget(),
        config=dict(allow_test_fixture=True, retry_failed=True), executor=FixtureExecutor())
    complete = source.capture_one(old_spec)
    root = tmp_path / 'TEST_FIXTURE_state' / 'cache'
    target = native.CaptureCache(root, FixtureBudget(), config=dict(allow_test_fixture=True), executor=FixtureExecutor())
    value = complete.metadata
    save(root / 'epoch-seed.json', dict(schema_version=EPOCH_SCHEMA, sources=[dict(
        {role: value[role] for role in ('raw', 'receipt', 'result')}, archive_root=value['archive_root'])]))
    target.import_seed(root / 'epoch-seed.json')
    clock, plan = now(), 'f' * 64
    save(root / 'epoch-claim.json', dict(schema_version=EPOCH_SCHEMA, source_cache=str(source.root),
        plan_sha256=plan, status='STARTED_NO_IMPLICIT_RETRY', actual_started_at=clock))
    save(root / 'epoch-complete.json', dict(schema_version=EPOCH_SCHEMA, source_cache=str(source.root),
        cache_root=str(root), plan_sha256=plan, actual_completed_at=clock,
        status='CURRENT_ORIGINALS_RECERTIFIED_NEW_METADATA_EPOCH', network_calls=0, new_market_data=False,
        old_records_rewritten=False, source_clocks_rewritten=False, request_key_mappings=[dict(
            old_request_key=old_spec.key, current_request_keys=[old_spec.key], receipt_id=complete.receipt_id)]))
    folder = root / 'raw' / old_spec.key
    save(folder / 'inherited-original-attempt/request-claim.json', dict(schema_version=EPOCH_SCHEMA,
        request_key=old_spec.key, indexed=True, source_attempt_directory=str(source.root / 'raw' / old_spec.key),
        source_plan_sha256=plan, status='INHERITED_ORIGINAL_ATTEMPT_NO_IMPLICIT_RETRY'))
    f = master_fixture(tmp_path, cache_name='TEST_FIXTURE_state/cache')
    scope, directory = f['context']['scope'], Path(f['context']['scope_directory'])
    job = root.parent / 'jobs' / 'TEST_FIXTURE_original_job'
    job.mkdir(parents=True)
    identity = dict(backend='premarket', request_key='TEST_FIXTURE_original_operation', state_root=str(root.parent),
                    observation_date=OBSERVED, target_session=TARGET)
    operation_id = digest(dict(scope=scope, identity=identity))
    operation = dict(schema_version='CODEX_CHAT_WORKSPACE_V1', scope=scope, operation_id=operation_id,
                     identity=identity, phase='FAILED_OR_UNKNOWN', started_at=now(), finished_at=now())
    operation_ref = save(directory / 'operations' / (operation_id + '.json'), operation)
    review = dict(schema_version='PRIVATE_INHERITED_UNKNOWN_INVENTORY_REVIEW_V1', checked_at=now(),
        unknown_attempts=[dict(source_request_key=old_spec.key, attempt=str(unknown),
            directory_manifest=[dict(type='FILE', **reference(path)) for path in sorted(unknown.rglob('*')) if path.is_file()],
            request_spec=old_spec.to_dict(), existing_complete_counterparts_verified_now=[dict(receipt_id=complete.receipt_id)])])
    review_ref = save(tmp_path / 'TEST_FIXTURE_review.json', review)
    cache = native.CaptureCache(root, AccountingBudget(), config=dict(allow_test_fixture=True), executor=f['executor'])
    candidate = prepare_factor_reconciliation(cache, operation_ref=operation_ref, operation=operation,
        scope=scope, scope_directory=str(directory), source_job=str(job), expected_case=case_from_review(review, review_ref))
    slot = directory / 'operations' / operation_id / 'continuation'
    proposal = json.loads(Path(f['context']['proposal']['path']).read_bytes())
    proposal.update(operation_id=operation_id, request_key=identity['request_key'], source_job=str(job),
                    factor_reconciliation=candidate)
    claim = json.loads(Path(f['context']['claim']['path']).read_bytes())
    record = dict(schema_version='PREMARKET_FACTOR_RECONCILIATION_RECORD_V1', reconciliation=candidate,
                  registered_at=now(), review_json=Path(review_ref['path']).read_text())
    claim.update(operation_id=operation_id, request_key=identity['request_key'], source_job=str(job),
                 original_operation_ref=operation_ref)
    f.update(cache=cache, source=source, folder=folder, unknown=unknown, factor_spec=old_spec,
             factor_complete=complete, candidate=candidate, operation=operation, operation_ref=operation_ref,
             review=review, review_ref=review_ref, proposal=proposal, claim=claim, record=record, slot=slot,
             context=dict(f['context'], operation_id=operation_id))
    seal(f)
    return f


def seal(f):
    f['claim'].update(factor_reconciliation_ref=save(f['slot'] / 'reconciliation.json', f['record']),
                      proposal_ref=save(f['slot'] / 'proposal.json', f['proposal']), claimed_at=now())
    f['context'].update(claim=save(f['slot'] / 'claim.json', f['claim']), proposal=f['claim']['proposal_ref'])
    f['cache'] = native.CaptureCache(f['cache'].root, AccountingBudget(), config=dict(allow_test_fixture=True),
        executor=f['executor'], continuation_context=f['context'])


def ready(f):
    f['cache'].capture_authorized_once()
    assert f['executor'].calls == [('master_szse', {})]
    return f['cache']


def test_exact_present_day_relation_keeps_unknown_and_old_original(tmp_path):
    f = fixture(tmp_path)
    before = {p: p.read_bytes() for p in f['source'].root.rglob('*') if p.is_file()}
    candidate = verify_factor_reconciliation(f['cache'], f['candidate'])
    assert candidate['authorized_codes'] == CODES and len(candidate['references']) < 30
    assert candidate['unknown_source_completion'] == 'UNKNOWN'
    assert candidate['old_request_retry_authorized'] is False and candidate['budget_credit'] == 0
    assert candidate['historical_preservation_proven'] is False
    inventory = reconciled_inherited_scope(f['cache'], f['folder'], candidate)
    assert inventory['status'] == 'INVENTORY_RECONCILED_UNKNOWN_RETAINED'
    assert inventory['actual_counts_status'] == 'UNKNOWN_RETAINED'
    assert inventory['retained_unknown_attempts'][0]['actual_counts'] == dict(sdk_operations=None, http_attempts=None)
    assert inherited_attempt_scope(f['cache'], f['folder'])['status'] == 'UNKNOWN_RETAINED_NO_IMPLICIT_RETRY'
    assert f['cache'].factor_overlap_blockers(factor())
    cache = ready(f)
    assert cache.factor_overlap_blockers(factor()) == []
    assert cache.capture_one(f['factor_spec']).receipt_id == f['factor_complete'].receipt_id
    assert f['executor'].calls == [('master_szse', {})]
    assert before == {p: p.read_bytes() for p in before}
    cache.terminal()


@pytest.mark.parametrize('new', [factor(end=OLD_END), factor(start='2000-01-01'),
    factor(end='2026-11-05'), factor(business_session='2026-11-05'), factor(current=True)])
def test_reconciliation_never_permits_old_request_or_other_window(tmp_path, new):
    f = fixture(tmp_path)
    cache = ready(f)
    assert cache.factor_overlap_blockers(new)
    assert cache.factor_reconciliation_allows(f['folder'], new)['status'] != 'AUTHORIZED_NEW_OBSERVATION_OVERLAP_ONLY'
    with pytest.raises(ValueError, match='FACTOR_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY'):
        cache.capture_one(new)
    assert f['executor'].calls == [('master_szse', {})]


def test_exact_code_set_intersection_and_other_unknown_stays_blocked(tmp_path):
    f = fixture(tmp_path)
    cache = ready(f)
    outside = factor(['sh.600150'])
    assert cache.factor_reconciliation_allows(f['folder'], outside)['status'] != 'AUTHORIZED_NEW_OBSERVATION_OVERLAP_ONLY'
    assert cache.factor_overlap_blockers(outside) == []
    other = cache.root / 'raw' / ('e' * 64) / 'raw' / 'TEST_FIXTURE_new_unknown'
    save(other / 'unidentified.json', dict(schema_version='TEST_FIXTURE_UNKNOWN'))
    blockers = cache.factor_overlap_blockers(factor())
    assert len(blockers) == 1 and blockers[0]['request_key'] == 'e' * 64
    assert f['executor'].calls == [('master_szse', {})]


@pytest.mark.parametrize('change', ['unknown_bytes', 'unknown_added_file', 'unknown_empty_dir', 'unknown_removed',
    'extra_attempt', 'complete_commit', 'complete_raw', 'epoch_mapping'])
def test_changed_or_added_ancestor_evidence_stops_before_master_request(tmp_path, change):
    f = fixture(tmp_path)
    if change == 'unknown_bytes':
        path = f['unknown'] / 'result.json'
        path.write_bytes(path.read_bytes() + b' ')
    elif change == 'unknown_added_file':
        save(f['unknown'] / 'TEST_FIXTURE_added.json', {})
    elif change == 'unknown_empty_dir':
        (f['unknown'] / 'TEST_FIXTURE_added').mkdir()
    elif change == 'unknown_removed':
        (f['unknown'] / 'result.json').unlink()
    elif change == 'extra_attempt':
        save(f['unknown'].parent / 'TEST_FIXTURE_extra_attempt' / 'request-claim.json', {})
    elif change == 'complete_commit':
        save(Path(f['factor_complete'].metadata['capture_attempt_dir']) / 'committed.json', {})
    elif change == 'complete_raw':
        path = Path(f['factor_complete'].metadata['raw']['path'])
        path.write_bytes(path.read_bytes() + b' ')
    else:
        save(f['cache'].root / 'epoch-complete.json', {})
    with pytest.raises((ValueError, OSError)):
        f['cache'].capture_authorized_once()
    assert f['executor'].calls == [] and f['cache'].budget.calls['requests'] == 0


@pytest.mark.parametrize('clock', ['before_review', 'future', 'wrong_day'])
def test_registration_clock_is_present_day_and_after_review(tmp_path, clock):
    f = fixture(tmp_path)
    if clock == 'before_review':
        f['record']['registered_at'] = f['operation']['started_at']
    elif clock == 'future':
        f['record']['registered_at'] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    else:
        f['claim']['source_retry']['request_spec']['business_session'] = '2000-01-01'
        f['proposal']['source_retry'] = deepcopy(f['claim']['source_retry'])
    seal(f)
    with pytest.raises(ValueError, match='RECONCILIATION_REGISTRATION_CLOCK_BINDING_CHANGED'):
        f['cache'].capture_authorized_once()
    assert f['executor'].calls == []


def test_original_review_can_be_removed_only_after_exact_bytes_are_sealed(tmp_path):
    f = fixture(tmp_path)
    Path(f['review_ref']['path']).unlink()
    assert verify_claimed_factor_reconciliation(f['cache']) == f['candidate']
    ready(f).terminal()


@pytest.mark.parametrize('change', ['external', 'owned', 'scope', 'codes'])
def test_changed_review_or_registered_scope_is_not_authorization(tmp_path, change):
    f = fixture(tmp_path)
    if change == 'external':
        Path(f['review_ref']['path']).write_text('{}')
    elif change == 'owned':
        f['record']['review_json'] = json.dumps(f['review'])
        seal(f)
    else:
        candidate = f['record']['reconciliation']
        if change == 'scope':
            candidate['operation_id'] = 'e' * 64
        else:
            candidate['authorized_codes'] = [*CODES[:-1], 'sh.600999']
        f['proposal']['factor_reconciliation'] = deepcopy(candidate)
        seal(f)
    with pytest.raises((ValueError, OSError)):
        f['cache'].capture_authorized_once()
    assert f['executor'].calls == []


def test_after_authorized_source_new_ancestor_attempt_still_blocks_and_terminal_rehashes(tmp_path):
    f = fixture(tmp_path)
    cache = ready(f)
    assert cache.factor_overlap_blockers(factor()) == []
    save(f['unknown'].parent / 'TEST_FIXTURE_later_unknown' / 'request-claim.json', {})
    assert cache.factor_overlap_blockers(factor())
    with pytest.raises(ValueError, match='FACTOR_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY'):
        cache.capture_one(factor())
    path = f['unknown'] / 'result.json'
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError):
        cache.terminal()
    assert f['executor'].calls == [('master_szse', {})]
