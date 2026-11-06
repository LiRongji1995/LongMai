"""Read retained explicit-recovery evidence without restoring an executor.

Runtime-supplied references only locate old evidence. A completed record does
not itself authorize retrying: the exact failed attempt and later same-request
original must be bound by the preserved plan, claim and complete event chain.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re

from stockfish_core.file_integrity import content_equal


SCHEMA = 'EXPLICIT_HISTORICAL_FAILURE_RECOVERY_V1'
FACTOR_SCHEMA = 'CODEX_FULL_FACTOR_HISTORICAL_RECOVERY_V1'
ROLE = 'HISTORICAL_LATE_SOURCE_RECOVERY'
COMPLETE = 'COMPLETE_SOURCE_RECOVERY_LATE'
SHA = re.compile(r'[0-9a-f]{64}', re.ASCII)


def explicit_recovered_attempt(cache, attempt, prior, *, entries=None):
    """Prove one already recovered failed attempt; never grant retry authority."""
    from .premarket_capture_cache_v1 import RequestSpec, VERSION, _canonical, _json, _need, _stamp

    context = cache.recovery_context
    if context is None:
        return None
    unknown = dict(status='UNKNOWN_RETAINED_NO_IMPLICIT_RETRY', source_capability_issued=False,
                   retry_authorized=False)

    def digest(value):
        return hashlib.sha256(_canonical(value)).hexdigest()

    def read(reference, *, path=None):
        _need(isinstance(reference, dict) and isinstance(reference.get('path'), str)
              and SHA.fullmatch(reference.get('sha256', ''))
              and type(reference.get('bytes')) is int and reference['bytes'] >= 0,
              'EXPLICIT_RECOVERY_CONTENT_REFERENCE_REQUIRED')
        if path is not None:
            _need(reference['path'] == str(path), 'EXPLICIT_RECOVERY_REFERENCE_PATH_CHANGED')
        raw, actual = cache._read(reference, limit=8 * 1024**2)
        return _json(raw), actual

    def canonical_directory(path):
        path = Path(path)
        _need(path.is_absolute() and path.is_dir() and not path.is_symlink()
              and path.resolve(strict=True) == path, 'EXPLICIT_RECOVERY_CANONICAL_DIRECTORY_REQUIRED')
        return path

    try:
        _need(isinstance(context, dict) and set(context) == {'scope', 'scope_directory', 'completions'},
              'EXPLICIT_RECOVERY_RUNTIME_CONTEXT_REQUIRED')
        scope, references = context['scope'], context['completions']
        directory = canonical_directory(context['scope_directory'])
        _need(isinstance(scope, dict) and scope.get('backend') == 'premarket'
              and directory.name == digest(scope), 'EXPLICIT_RECOVERY_REGISTERED_SCOPE_REQUIRED')
        _need(isinstance(references, list) and len(references) <= 64
              and len({ref['path'] for ref in references}) == len(references),
              'EXPLICIT_RECOVERY_COMPLETION_REFERENCE_BOUND')
        attempt = canonical_directory(attempt)
        request_root = cache.root / 'raw' / prior.key
        _need(prior.endpoint == 'adjustment_factors' and attempt.parent == request_root / 'raw',
              'EXPLICIT_RECOVERY_EXACT_FACTOR_ATTEMPT_REQUIRED')
        for reference in references:
            completion_path = Path(reference['path'])
            slot = canonical_directory(completion_path.parent)
            _need(slot.parent == directory / 'explicit-failure-recoveries'
                  and SHA.fullmatch(slot.name) and completion_path.name == 'completion.json'
                  and not (slot / 'failure.json').exists()
                  and not (slot / 'failure.json').is_symlink()
                  and not any(slot.glob('*rejected*')),
                  'EXPLICIT_RECOVERY_REGISTERED_COMPLETE_SLOT_REQUIRED')
            completion, completion_ref = read(reference, path=slot / 'completion.json')
            plan, _ = read(completion['plan'], path=slot / 'plan.json')
            claim, _ = read(completion['claim'], path=slot / 'claim.json')
            plan_id = plan.get('plan_id')
            for row in (plan, claim, completion):
                _need(row.get('schema_version') == SCHEMA and row.get('source_role') == ROLE
                      and row.get('plan_id') == plan_id and row.get('request_key') == plan.get('request_key')
                      and row.get('source_job') == plan.get('source_job')
                      and row.get('complete_daily_delivery') is False,
                      'EXPLICIT_RECOVERY_CONTROL_BINDING_CHANGED')
            _need(plan_id == digest({k: v for k, v in plan.items() if k != 'plan_id'})
                  and plan.get('original_scope') == claim.get('original_scope') == scope
                  and plan.get('slot') == str(slot) and plan.get('operation_id') == slot.name
                  and plan.get('retry_scope') == 'ONE_EXPLICIT_ATTEMPT_FOR_EXACT_SOURCE_PLAN'
                  and plan.get('test_fixture') is cache.config.get('allow_test_fixture', False)
                  and completion.get('status') == COMPLETE and claim.get('retry_authorized') is False
                  and all(row.get(key) is False for row in (plan, completion) for key in
                          ('old_operation_rewritten', 'historical_pit_restored', 'source_capability_issued')),
                  'EXPLICIT_RECOVERY_PLAN_OR_AUTHORITY_CHANGED')
            original_plan, _ = read(claim['explicit_plan_file'])
            _need(original_plan == plan, 'EXPLICIT_RECOVERY_SELECTED_PLAN_CHANGED')
            operation, _ = read(claim['original_operation'], path=directory / 'operations' / (slot.name + '.json'))
            _need(operation.get('schema_version') == 'CODEX_CHAT_WORKSPACE_V1'
                  and operation.get('scope') == scope and operation.get('operation_id') == slot.name
                  and digest(dict(scope=scope, identity=operation['identity'])) == slot.name
                  and operation.get('phase') == 'FAILED_OR_UNKNOWN' and 'receipt' not in operation
                  and operation['identity'].get('request_key') == plan['request_key'],
                  'EXPLICIT_RECOVERY_ORIGINAL_FAILED_OPERATION_CHANGED')
            source = plan['source_plan']
            _need(source.get('schema_version') == FACTOR_SCHEMA
                  and source.get('plan_hash') == digest({k: v for k, v in source.items() if k != 'plan_hash'})
                  and claim.get('source_plan_hash') == source['plan_hash']
                  and source.get('cache_root') == str(cache.root)
                  and source.get('strategy_executed') is False and source.get('source_capability_issued') is False,
                  'EXPLICIT_RECOVERY_SOURCE_PLAN_CHANGED')
            batches = [row for row in source['batches'] if row.get('request_key') == prior.key]
            if not batches:
                continue
            _need(len(batches) == 1 and prior.key in source['retry_keys'],
                  'EXPLICIT_RECOVERY_EXACT_RETRY_KEY_REQUIRED')
            batch = batches[0]
            _need(batch.get('action') == 'RETRY_UNAUTHENTICATED_ONCE'
                  and RequestSpec(**batch['request_spec']).key == prior.key
                  and batch['request_spec'] == prior.to_dict(),
                  'EXPLICIT_RECOVERY_EXACT_FAILED_REQUEST_REQUIRED')
            result = completion['result']
            _need(result.get('schema_version') == FACTOR_SCHEMA and result.get('status') == COMPLETE
                  and result.get('source_complete') is True and result.get('plan_hash') == source['plan_hash']
                  and result.get('job') == plan['source_job'] and result.get('strategy_executed') is False
                  and result.get('source_capability_issued') is False
                  and completion['metrics']['limits'] == claim['execution_limits']
                  and all(completion['metrics']['calls'].get(key) == value
                          for key, value in source['planned_new_calls'].items()),
                  'EXPLICIT_RECOVERY_RESULT_OR_BUDGET_BINDING_CHANGED')
            started, finished = _stamp(completion['started_at']), _stamp(completion['completed_at'])
            _need(started <= _stamp(claim['claimed_at']) <= finished <= datetime.now(timezone.utc),
                  'EXPLICIT_RECOVERY_CONTROL_CLOCK_CHANGED')
            event_refs = completion['events']
            _need(isinstance(event_refs, list) and 1 <= len(event_refs) <= 4096,
                  'EXPLICIT_RECOVERY_EVENT_COUNT_BOUND')
            event_directory = canonical_directory(slot / 'request-events')
            _need(set(event_directory.iterdir()) == {Path(ref['path']) for ref in event_refs},
                  'EXPLICIT_RECOVERY_EVENT_INVENTORY_CHANGED')
            selected, selected_times, last = [], [], started
            for ordinal, event_ref in enumerate(event_refs):
                event, _ = read(event_ref, path=event_directory / (f'{ordinal:04d}.json'))
                clock = _stamp(event['recorded_at'])
                _need(last <= clock <= finished and all(event.get(key) == completion.get(key)
                      for key in ('schema_version', 'source_role', 'plan_id', 'request_key', 'source_job')),
                      'EXPLICIT_RECOVERY_EVENT_PARENT_OR_CLOCK_CHANGED')
                last = clock
                if event['event'].get('request_key') == prior.key:
                    selected.append(event['event'])
                    selected_times.append(clock)
            _need([row.get('event') for row in selected] ==
                  ['BEFORE_CAPTURE_ATTEMPT', 'BEFORE_REQUEST', 'AFTER_RESULT'],
                  'EXPLICIT_RECOVERY_EXACT_THREE_REQUEST_EVENTS_REQUIRED')
            for event in selected[:2]:
                _need(event.get('action') == batch['action'] and event.get('request_spec') == batch['request_spec'],
                      'EXPLICIT_RECOVERY_REQUEST_EVENT_SCOPE_CHANGED')
            old_refs = batch['old_attempt_references']
            _need(isinstance(old_refs, list) and 1 <= len(old_refs) <= cache.max_entries
                  and len({ref['path'] for ref in old_refs}) == len(old_refs),
                  'EXPLICIT_RECOVERY_OLD_ATTEMPT_REFERENCE_BOUND')
            covered = {}
            for old_ref in old_refs:
                path = Path(old_ref['path'])
                _need(path.is_relative_to(request_root / 'raw'), 'EXPLICIT_RECOVERY_OLD_ATTEMPT_SCOPE_CHANGED')
                document, _ = read(old_ref)
                if path.is_relative_to(attempt):
                    covered[path] = document
            paths = list(attempt.rglob('*'))
            _need(len(paths) <= cache.max_entries and not any(path.is_symlink() for path in paths)
                  and {path for path in paths if path.is_file()} == set(covered)
                  and attempt / 'request-claim.json' in covered and attempt / 'attempt-failed.json' in covered,
                  'EXPLICIT_RECOVERY_FAILED_ATTEMPT_NOT_EXACTLY_COVERED')
            old_claim = covered[attempt / 'request-claim.json']
            _need(old_claim.get('schema_version') == VERSION
                  and RequestSpec(**old_claim['request_spec']).key == prior.key
                  and _stamp(old_claim['actual_started_at']) < started
                  and covered[attempt / 'attempt-failed.json'].get('status') == 'FAILED_RETAINED_NO_RETRY',
                  'EXPLICIT_RECOVERY_OLD_FAILURE_BINDING_CHANGED')
            latest = cache.lookup(prior)
            _need(latest is not None and latest.status == 'COMPLETE'
                  and latest.metadata['pagination_complete'], 'EXPLICIT_RECOVERY_LATEST_ORIGINAL_NOT_COMPLETE')
            current = latest.metadata
            new_records = [row for row in result['new_records'] if row.get('request_key') == prior.key]
            _need(len(new_records) == 1 and content_equal(new_records[0], current)
                  and content_equal(selected[2].get('original_record'), current)
                  and selected[2].get('status') == 'COMPLETE'
                  and selected[2].get('receipt_id') == current['receipt_id'],
                  'EXPLICIT_RECOVERY_NEW_ORIGINAL_BINDING_CHANGED')
            new_attempt = canonical_directory(current['capture_attempt_dir'])
            _need(new_attempt.parent == request_root / 'raw' and new_attempt != attempt
                  and selected[1].get('output') == str(new_attempt)
                  and not (new_attempt / 'attempt-failed.json').exists()
                  and not (new_attempt / 'attempt-failed.json').is_symlink()
                  and started <= _stamp(current['started_at']) <= _stamp(current['completed_at']) <= finished,
                  'EXPLICIT_RECOVERY_NEW_ATTEMPT_OR_CLOCK_CHANGED')
            commit_refs = [ref for ref in result['source_references']
                           if ref.get('path') == str(new_attempt / 'committed.json')]
            _need(len(commit_refs) == 1, 'EXPLICIT_RECOVERY_ORIGINAL_COMMIT_REFERENCE_REQUIRED')
            commit, _ = read(commit_refs[0], path=new_attempt / 'committed.json')
            _need(commit.get('request_key') == prior.key and commit.get('receipt_id') == current['receipt_id'],
                  'EXPLICIT_RECOVERY_COMMIT_BINDING_CHANGED')
            new_claim_refs = [ref for ref in result['source_references']
                              if ref.get('path') == str(new_attempt / 'request-claim.json')]
            _need(len(new_claim_refs) == 1, 'EXPLICIT_RECOVERY_NEW_CLAIM_REFERENCE_REQUIRED')
            new_claim, _ = read(new_claim_refs[0], path=new_attempt / 'request-claim.json')
            _need(new_claim.get('schema_version') == VERSION
                  and RequestSpec(**new_claim['request_spec']).key == prior.key
                  and selected_times[0] <= _stamp(new_claim['actual_started_at'])
                  <= _stamp(selected[1]['actual_started_at']) <= selected_times[1]
                  <= _stamp(current['started_at']) <= _stamp(current['completed_at']) <= selected_times[2],
                  'EXPLICIT_RECOVERY_REQUEST_CHRONOLOGY_CHANGED')
            return dict(status='VERIFIED_EXPLICITLY_RECOVERED_ATTEMPT', request_key=prior.key,
                old_attempt=str(attempt), completed_receipt_id=current['receipt_id'], completion=completion_ref,
                source_capability_issued=False, retry_authorized=False,
                purpose='READ_EXISTING_RECOVERY_ONLY_NOT_NEW_RETRY_AUTHORITY')
        return dict(unknown, reason='NO_REGISTERED_EXPLICIT_RECOVERY_COVERS_ATTEMPT')
    except (ValueError, TypeError, KeyError, OSError) as error:
        return dict(unknown, reason=str(error)[:256])
