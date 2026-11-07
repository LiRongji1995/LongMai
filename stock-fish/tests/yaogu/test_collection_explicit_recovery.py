"""TEST_FIXTURE proofs for retained, explicitly recovered source failures."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from stockfish_core.yaogu.collection_explicit_recovery import SCHEMA, FACTOR_SCHEMA, ROLE, COMPLETE
from stockfish_core.yaogu.premarket_capture_cache_v1 import CaptureCache, RequestSpec, _canonical
from tests.yaogu.test_premarket_capture_cache_v1 import FixtureBudget, FixtureExecutor


def digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def reference(path):
    raw = path.read_bytes()
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value))
    return reference(path)


class ClockedExecutor(FixtureExecutor):
    def __call__(self, *args, **kwargs):
        self.before_request = now()
        return super().__call__(*args, **kwargs)


def make_recovery(tmp_path, *, action='RETRY_UNAUTHENTICATED_ONCE', omit_old_failure=False,
                  wrong_event=False, omit_commit_reference=False, complete_status=COMPLETE):
    root = tmp_path / 'TEST_FIXTURE_cache'
    prior = RequestSpec('adjustment_factors', dict(codes=['sh.600001', 'sh.600002'],
        start_date='1990-01-01', end_date='2026-10-09'), '2026-10-09', role='TEST_FIXTURE')
    first = CaptureCache(root, FixtureBudget(), config=dict(allow_test_fixture=True),
        executor=FixtureExecutor(crash=True))
    with pytest.raises(RuntimeError, match='TEST_FIXTURE_EXECUTOR_DIED'):
        first.capture_one(prior)
    attempt = next((root / 'raw' / prior.key / 'raw').iterdir())
    old_refs = [reference(path) for path in sorted(attempt.rglob('*')) if path.is_file()]
    if omit_old_failure:
        old_refs = [ref for ref in old_refs if not ref['path'].endswith('attempt-failed.json')]
    started = now()
    scope = dict(backend='premarket', device=1, inode=42)
    directory = tmp_path / 'TEST_FIXTURE_registry' / 'scopes' / digest(scope)
    identity = dict(backend='premarket', request_key='TEST_FIXTURE_original_failed_research')
    operation_id = digest(dict(scope=scope, identity=identity))
    slot = directory / 'explicit-failure-recoveries' / operation_id
    operation_ref = save(directory / 'operations' / (operation_id + '.json'),
        dict(schema_version='CODEX_CHAT_WORKSPACE_V1', scope=scope, identity=identity,
             operation_id=operation_id, phase='FAILED_OR_UNKNOWN'))
    source = dict(schema_version=FACTOR_SCHEMA, cache_root=str(root), retry_keys=[prior.key],
        strategy_executed=False, source_capability_issued=False,
        planned_new_calls=dict(requests=1, sdk_reserved=0, http_reserved=0),
        batches=[dict(request_key=prior.key, request_spec=prior.to_dict(), action=action,
                      old_attempt_references=old_refs)])
    source['plan_hash'] = digest(source)
    plan = dict(schema_version=SCHEMA, source_role=ROLE, request_key=identity['request_key'],
        source_job=str(tmp_path / 'TEST_FIXTURE_source_job'), original_scope=scope,
        slot=str(slot), operation_id=operation_id, source_plan=source,
        retry_scope='ONE_EXPLICIT_ATTEMPT_FOR_EXACT_SOURCE_PLAN', test_fixture=True,
        complete_daily_delivery=False, old_operation_rewritten=False,
        historical_pit_restored=False, source_capability_issued=False)
    plan['plan_id'] = digest(plan)
    selected_plan_ref = save(tmp_path / 'TEST_FIXTURE_selected_plan.json', plan)
    plan_ref = save(slot / 'plan.json', plan)
    budget = FixtureBudget()
    claim = dict(schema_version=SCHEMA, source_role=ROLE, plan_id=plan['plan_id'],
        request_key=plan['request_key'], source_job=plan['source_job'], original_scope=scope,
        source_plan_hash=source['plan_hash'], explicit_plan_file=selected_plan_ref,
        original_operation=operation_ref, retry_authorized=False, complete_daily_delivery=False,
        claimed_at=now(), execution_limits=budget.limits)
    claim_ref = save(slot / 'claim.json', claim)
    before_capture = now()
    executor = ClockedExecutor()
    writer = CaptureCache(root, budget, config=dict(allow_test_fixture=True, retry_failed=True),
        executor=executor)
    record = writer.capture_one(prior)
    after_result = now()
    current = record.metadata
    payloads = [dict(event='BEFORE_CAPTURE_ATTEMPT', action=action, request_key=prior.key,
                    request_spec=prior.to_dict()),
        dict(event='BEFORE_REQUEST', action='WRONG_TEST_FIXTURE_ACTION' if wrong_event else action,
             request_key=prior.key, request_spec=prior.to_dict(),
             actual_started_at=executor.before_request, output=current['capture_attempt_dir']),
        dict(event='AFTER_RESULT', request_key=prior.key, status='COMPLETE',
             receipt_id=record.receipt_id, original_record=current)]
    events = []
    for ordinal, (payload, clock) in enumerate(zip(payloads,
            (before_capture, executor.before_request, after_result))):
        events.append(save(slot / 'request-events' / (f'{ordinal:04d}.json'),
            dict(schema_version=SCHEMA, source_role=ROLE, plan_id=plan['plan_id'],
                 request_key=plan['request_key'], source_job=plan['source_job'], event=payload,
                 recorded_at=clock)))
    new_attempt = Path(current['capture_attempt_dir'])
    source_refs = [reference(new_attempt / 'request-claim.json')]
    if not omit_commit_reference:
        source_refs.append(reference(new_attempt / 'committed.json'))
    result = dict(schema_version=FACTOR_SCHEMA, status=complete_status, source_complete=True,
        plan_hash=source['plan_hash'], job=plan['source_job'], strategy_executed=False,
        source_capability_issued=False, new_records=[current], source_references=source_refs)
    completion = dict(schema_version=SCHEMA, source_role=ROLE, status=complete_status,
        plan_id=plan['plan_id'], request_key=plan['request_key'], source_job=plan['source_job'],
        claim=claim_ref, plan=plan_ref, events=events, result=result, started_at=started,
        completed_at=now(), metrics=dict(limits=budget.limits, calls=budget.calls),
        complete_daily_delivery=False, old_operation_rewritten=False, historical_pit_restored=False,
        source_capability_issued=False)
    completion_ref = save(slot / 'completion.json', completion)
    context = dict(scope=scope, scope_directory=str(directory), completions=[completion_ref])
    observer = CaptureCache(root, FixtureBudget(), config=dict(allow_test_fixture=True),
                            executor=FixtureExecutor(), recovery_context=context)
    new = RequestSpec('adjustment_factors', dict(codes=['sh.600002'],
        start_date='1990-01-01', end_date='2026-10-12'), '2026-10-12')
    return dict(cache=observer, prior=prior, new=new, attempt=attempt, slot=slot, context=context,
                record=record, operation_ref=operation_ref)


def test_registered_complete_recovery_reads_original_evidence_without_retry(tmp_path):
    f = make_recovery(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    proof = f['cache'].explicit_recovered_attempt(f['attempt'], f['prior'])
    assert proof['status'] == 'VERIFIED_EXPLICITLY_RECOVERED_ATTEMPT'
    assert proof['retry_authorized'] is False and proof['source_capability_issued'] is False
    assert f['cache'].factor_overlap_blockers(f['new']) == []
    assert f['cache'].lookup(f['prior']).receipt_id == f['record'].receipt_id
    assert f['cache'].budget.calls['requests'] == 0 and f['cache'].executor.calls == []
    f['cache'].terminal()
    assert {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()} == before


def test_no_runtime_context_keeps_existing_failure_guard(tmp_path):
    f = make_recovery(tmp_path)
    cache = CaptureCache(f['cache'].root, FixtureBudget(), config=dict(allow_test_fixture=True))
    assert cache.explicit_recovered_attempt(f['attempt'], f['prior']) is None
    assert cache.factor_overlap_blockers(f['new'])[0]['request_key'] == f['prior'].key


@pytest.mark.parametrize('field', ['completion', 'plan', 'claim', 'event', 'operation', 'old_failure',
                                  'old_claim', 'new_commit', 'new_claim', 'new_raw'])
def test_every_recovery_link_is_content_authenticated(tmp_path, field):
    f = make_recovery(tmp_path)
    new_attempt = Path(f['record'].metadata['capture_attempt_dir'])
    paths = dict(completion=f['slot'] / 'completion.json', plan=f['slot'] / 'plan.json',
        claim=f['slot'] / 'claim.json', event=f['slot'] / 'request-events/0001.json',
        operation=Path(f['operation_ref']['path']), old_failure=f['attempt'] / 'attempt-failed.json',
        old_claim=f['attempt'] / 'request-claim.json', new_commit=new_attempt / 'committed.json',
        new_claim=new_attempt / 'request-claim.json', new_raw=Path(f['record'].metadata['raw']['path']))
    paths[field].write_bytes(paths[field].read_bytes() + b' ')
    proof = f['cache'].explicit_recovered_attempt(f['attempt'], f['prior'])
    assert proof['status'] == 'UNKNOWN_RETAINED_NO_IMPLICIT_RETRY'
    assert f['cache'].factor_overlap_blockers(f['new'])[0]['request_key'] == f['prior'].key
    assert f['cache'].budget.calls['requests'] == 0


@pytest.mark.parametrize('changes', [dict(action='RETRY_PARTIAL_ONCE'), dict(omit_old_failure=True),
    dict(wrong_event=True), dict(omit_commit_reference=True), dict(complete_status='INCOMPLETE_SOURCE_RECOVERY_LATE')])
def test_valid_hashes_do_not_replace_exact_completed_recovery_semantics(tmp_path, changes):
    f = make_recovery(tmp_path, **changes)
    assert f['cache'].explicit_recovered_attempt(f['attempt'], f['prior'])['status'] == \
        'UNKNOWN_RETAINED_NO_IMPLICIT_RETRY'
    assert f['cache'].factor_overlap_blockers(f['new'])[0]['request_key'] == f['prior'].key


@pytest.mark.parametrize('extra', ['old_file', 'event', 'revocation', 'revocation_link',
                                  'new_failure_link', 'new_attempt'])
def test_added_unknown_evidence_remains_blocked_after_successful_read(tmp_path, extra):
    f = make_recovery(tmp_path)
    assert f['cache'].factor_overlap_blockers(f['new']) == []
    if extra == 'old_file':
        save(f['attempt'] / 'TEST_FIXTURE_unknown.json', dict(unknown=True))
    elif extra == 'event':
        save(f['slot'] / 'request-events/0003.json', dict(unknown=True))
    elif extra == 'revocation':
        save(f['slot'] / 'failure.json', dict(status='FAILED'))
    elif extra == 'revocation_link':
        (f['slot'] / 'failure.json').symlink_to(tmp_path / 'TEST_FIXTURE_missing_revocation')
    elif extra == 'new_failure_link':
        (Path(f['record'].metadata['capture_attempt_dir']) / 'attempt-failed.json').symlink_to(
            tmp_path / 'TEST_FIXTURE_missing_failure')
    else:
        other = f['attempt'].parent / 'TEST_FIXTURE_new_uncommitted_attempt'
        save(other / 'request-claim.json', dict(schema_version='PREMARKET_CAPTURE_CACHE_V1',
            request_spec=f['prior'].to_dict(), actual_started_at=now(), retries=0))
        save(other / 'attempt-failed.json', dict(status='FAILED_RETAINED_NO_RETRY'))
    assert f['cache'].factor_overlap_blockers(f['new'])[0]['request_key'] == f['prior'].key
    assert f['cache'].budget.calls['requests'] == 0


def test_registered_context_is_copied_and_other_scope_is_not_accepted(tmp_path):
    f = make_recovery(tmp_path)
    f['context']['completions'].clear()
    assert f['cache'].factor_overlap_blockers(f['new']) == []
    other = deepcopy(f['cache'].recovery_context)
    other['scope']['inode'] += 1
    cache = CaptureCache(f['cache'].root, FixtureBudget(), config=dict(allow_test_fixture=True),
                         recovery_context=other)
    assert cache.factor_overlap_blockers(f['new'])[0]['request_key'] == f['prior'].key


def test_terminal_rehash_catches_change_after_recovery_was_recognized(tmp_path):
    f = make_recovery(tmp_path)
    assert f['cache'].factor_overlap_blockers(f['new']) == []
    original = f['attempt'] / 'attempt-failed.json'
    raw = original.read_bytes()
    changed = raw.replace(b'FAILED_RETAINED', b'FAILED_RETAINE_', 1)
    assert len(changed) == len(raw) and changed != raw
    original.write_bytes(changed)
    with pytest.raises(ValueError, match='SHA_CHANGED|ORIGINAL|FILE_CONTENT'):
        f['cache'].terminal()


def test_optional_factor_context_does_not_change_existing_explicit_source_authority(tmp_path):
    f = make_recovery(tmp_path)
    context = dict(f['context'], factor_reconciliations=[dict(path=str(tmp_path / 'TEST_FIXTURE_unrelated_policy.json'),
        sha256='a' * 64, bytes=1)])
    cache = CaptureCache(f['cache'].root, FixtureBudget(), config=dict(allow_test_fixture=True),
                         recovery_context=context, executor=FixtureExecutor())
    assert cache.explicit_recovered_attempt(f['attempt'], f['prior'])['status'] == 'VERIFIED_EXPLICITLY_RECOVERED_ATTEMPT'
    assert cache.factor_overlap_blockers(f['new']) == []
    context['completions'] = []
    cache = CaptureCache(cache.root, FixtureBudget(), config=dict(allow_test_fixture=True),
                         recovery_context=context, executor=FixtureExecutor())
    assert cache.explicit_recovered_attempt(f['attempt'], f['prior'])['status'] == 'UNKNOWN_RETAINED_NO_IMPLICIT_RETRY'
    assert cache.factor_overlap_blockers(f['new'])
    assert cache.executor.calls == []
