"""One exact source attempt under an already claimed operation continuation.

This reader does not create an authorization, budget, operation or retry policy.
It binds the worker to the original failed capture and the fixed claim; the
ordinary cache request transaction records consumption in its native claim.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re

from stockfish_core.file_integrity import content_equal, content_reference_equal


AUTHORIZATION = 'PREMARKET_CONTINUATION_AUTHORIZATION_V1'
CLAIM = 'PREMARKET_CONTINUATION_CLAIM_V1'
SHA = re.compile(r'[0-9a-f]{64}', re.ASCII)


def require_known_accounting(cache):
    from .premarket_capture_cache_v1 import _need
    metrics = cache.budget.metrics
    calls = metrics.get('calls', {})
    _need(metrics.get('pending_request_results', 0) == 0
          and calls.get('actual_unknown', 0) == 0
          and calls.get('sdk_actual_unknown_requests', 0) == 0
          and calls.get('http_actual_unknown_requests', 0) == 0,
          'CONTINUATION_PENDING_OR_UNKNOWN_REQUEST_ACCOUNTING')


def require_active_session(cache):
    from .premarket_capture_cache_v1 import SH, _need
    _need(cache._continuation_session == datetime.now(SH).date().isoformat(),
          'CONTINUATION_CURRENT_SESSION_CHANGED_NO_MORE_REQUESTS')


def require_complete_source(cache, record, result):
    from .premarket_capture_cache_v1 import RequestSpec, SH, _need, _stamp
    require_known_accounting(cache)
    require_active_session(cache)
    value = record.metadata
    spec = RequestSpec(**value['request_spec'])
    coverage = result.get('coverage', {})
    _need(record.status == 'COMPLETE' and value['pagination_complete'],
          'CONTINUATION_NEW_SOURCE_NOT_COMPLETE_NO_MORE_REQUESTS')
    _need(all(type(coverage.get(key)) is int and coverage[key] >= 0
              for key in ('sdk_operations', 'http_attempts')),
          'CONTINUATION_NEW_SOURCE_ACTUAL_COUNTS_UNKNOWN')
    _need(_stamp(value['completed_at']) <= datetime.now(timezone.utc)
          and (not spec.current or spec.business_session ==
               _stamp(value['first_seen_at']).astimezone(SH).date().isoformat()),
          'CONTINUATION_NEW_SOURCE_CLOCK_OR_CURRENT_SESSION_CHANGED')


def authorized_source_attempt(cache):
    """Return exact request plus bound claim, or an already complete consumption."""
    from .premarket_capture_cache_v1 import RequestSpec, VERSION, SH, _canonical, _json, _need, _stamp

    context = cache.continuation_context
    _need(isinstance(context, dict) and set(context) ==
          {'schema_version', 'scope', 'scope_directory', 'operation_id', 'claim', 'proposal'}
          and context['schema_version'] == AUTHORIZATION,
          'CONTINUATION_EXPLICIT_AUTHORIZATION_REQUIRED')
    _need(cache.config.get('retry_failed', False) is False, 'CONTINUATION_GENERAL_RETRY_POLICY_FORBIDDEN')

    def read(reference, expected_path=None):
        _need(isinstance(reference, dict) and isinstance(reference.get('path'), str)
              and SHA.fullmatch(reference.get('sha256', ''))
              and type(reference.get('bytes')) is int and reference['bytes'] >= 0,
              'CONTINUATION_BOUND_CONTENT_REFERENCE_REQUIRED')
        if expected_path is not None:
            _need(reference['path'] == str(expected_path), 'CONTINUATION_BOUND_REFERENCE_PATH_CHANGED')
        raw, actual = cache._read(reference, limit=16 * 1024**2)
        return _json(raw), actual

    def digest(value):
        return hashlib.sha256(_canonical(value)).hexdigest()

    scope, operation_id = context['scope'], context['operation_id']
    directory = Path(context['scope_directory'])
    _need(isinstance(scope, dict) and scope.get('backend') == 'premarket'
          and isinstance(operation_id, str) and SHA.fullmatch(operation_id)
          and directory.is_absolute() and directory.resolve(strict=True) == directory
          and directory.name == digest(scope), 'CONTINUATION_REGISTERED_SCOPE_REQUIRED')
    slot = directory / 'operations' / operation_id / 'continuation'
    _need(slot.is_dir() and slot.resolve(strict=True) == slot,
          'CONTINUATION_FIXED_OPERATION_SLOT_REQUIRED')
    claim, claim_ref = read(context['claim'], slot / 'claim.json')
    proposal, proposal_ref = read(context['proposal'], slot / 'proposal.json')
    _need(claim.get('schema_version') == CLAIM and claim.get('scope') == scope
          and claim.get('operation_id') == operation_id
          and content_reference_equal(claim['proposal_ref'], proposal_ref)
          and proposal.get('schema_version') == 'PREMARKET_CONTINUATION_PROPOSAL_V1'
          and proposal.get('status') == 'ADMITTED_ONE_EXACT_SOURCE_RECOVERY_AND_CONTINUATION'
          and proposal.get('scope') == scope and proposal.get('operation_id') == operation_id
          and proposal.get('request_key') == claim.get('request_key')
          and proposal.get('source_job') == claim.get('source_job')
          and proposal.get('remaining_limits') == claim.get('remaining_limits')
          and content_equal(proposal.get('source_retry'), claim.get('source_retry')),
          'CONTINUATION_CLAIM_AND_PROPOSAL_BINDING_CHANGED')
    _need(_stamp(claim['claimed_at']) <= datetime.now(timezone.utc), 'CONTINUATION_CLAIM_CLOCK_INVALID')
    if proposal.get('factor_reconciliation') is not None or claim.get('factor_reconciliation_ref') is not None:
        _need(isinstance(proposal.get('factor_reconciliation'), dict)
              and isinstance(claim.get('factor_reconciliation_ref'), dict),
              'CONTINUATION_RECONCILIATION_RECORD_REQUIRED')
        from .collection_factor_reconciliation import verify_claimed_factor_reconciliation
        verify_claimed_factor_reconciliation(cache)
    operation, _ = read(claim['original_operation_ref'], directory / 'operations' / (operation_id + '.json'))
    _need(operation.get('schema_version') == 'CODEX_CHAT_WORKSPACE_V1'
          and operation.get('scope') == scope and operation.get('operation_id') == operation_id
          and digest(dict(scope=scope, identity=operation['identity'])) == operation_id
          and operation.get('phase') == 'FAILED_OR_UNKNOWN'
          and operation['identity'].get('request_key') == claim['request_key'],
          'CONTINUATION_ORIGINAL_FAILED_OPERATION_CHANGED')
    read(claim['snapshot_proof_ref'])
    remaining = claim['remaining_limits']
    _need(isinstance(remaining, dict) and bool(remaining)
          and all(key in remaining and type(value) in (int, float)
                  and 0 < value <= remaining[key] for key, value in cache.budget.limits.items()),
          'CONTINUATION_WORKER_LIMITS_EXCEED_ORIGINAL_REMAINDER')
    retry = claim['source_retry']
    spec = RequestSpec(**retry['request_spec'])
    _need(retry.get('authorization') == 'ONE_EXACT_SOURCE_ATTEMPT'
          and spec.endpoint == 'master_szse' and spec.parameters == {} and spec.current is True
          and spec.key == retry.get('request_key')
          and spec.business_session == datetime.now(SH).date().isoformat(),
          'CONTINUATION_EXACT_CURRENT_MASTER_SZSE_ONLY')
    original_ref = retry['prior_index_ref']
    original_path = cache.root / 'index' / spec.key / (original_ref['sha256'] + '.json')
    old, _ = read(original_ref, original_path)
    _need(old.get('schema_version') == VERSION and old.get('request_key') == spec.key
          and old.get('request_spec') == spec.to_dict()
          and old.get('receipt_id') == retry.get('prior_receipt_id')
          and old.get('status') == 'FETCH_FAILED' and old.get('pagination_complete') is False,
          'CONTINUATION_EXACT_PRIOR_FAILED_RECEIPT_REQUIRED')
    old_result = cache.verify(old)
    _need(old_result.get('failure_category') == 'NETWORK'
          and old_result.get('error_code') == 'UPSTREAM_TIMEOUT' and old_result.get('rows') == []
          and _stamp(old['completed_at']) <= _stamp(claim['claimed_at']),
          'CONTINUATION_ONLY_AUTHORIZED_UPSTREAM_TIMEOUT')
    prior_attempt = Path(retry['prior_attempt_dir'])
    request_root = cache.root / 'raw' / spec.key / 'raw'
    _need(old.get('capture_attempt_dir') == str(prior_attempt) and prior_attempt.parent == request_root
          and prior_attempt.is_dir() and prior_attempt.resolve(strict=True) == prior_attempt,
          'CONTINUATION_PRIOR_ATTEMPT_BINDING_CHANGED')
    refs = retry['prior_attempt_refs']
    _need(isinstance(refs, list) and 1 <= len(refs) <= cache.max_entries
          and len({ref['path'] for ref in refs}) == len(refs), 'CONTINUATION_PRIOR_REFERENCE_BOUND')
    paths = list(prior_attempt.rglob('*'))
    _need(len(paths) <= cache.max_entries and not any(path.is_symlink() for path in paths)
          and {str(path) for path in paths if path.is_file()} == {ref['path'] for ref in refs},
          'CONTINUATION_PRIOR_ATTEMPT_INVENTORY_CHANGED')
    for ref in refs:
        cache._read(ref, limit=16 * 1024**2)
    authorization = dict(claim=claim_ref, proposal=proposal_ref, request_key=spec.key,
                         prior_receipt_id=retry['prior_receipt_id'])
    attempts = sorted(request_root.iterdir())
    _need(len(attempts) <= cache.max_entries, 'CONTINUATION_ATTEMPT_COUNT_BOUND')
    consumed = []
    for attempt in attempts:
        _need(attempt.is_dir() and not attempt.is_symlink(), 'CONTINUATION_CANONICAL_ATTEMPT_REQUIRED')
        if attempt == prior_attempt:
            continue
        raw, _ = cache._read(attempt / 'request-claim.json', limit=2 * 1024**2)
        other = _json(raw)
        _need(other.get('schema_version') == VERSION and other.get('request_spec') == spec.to_dict()
              and content_equal(other.get('continuation_authorization'), authorization),
              'CONTINUATION_UNAUTHORIZED_OR_UNKNOWN_ADDITIONAL_ATTEMPT')
        consumed.append((attempt, other))
    _need(len(consumed) <= 1, 'CONTINUATION_EXACTLY_ONE_SOURCE_ATTEMPT')
    current = cache.lookup(spec)
    if consumed:
        attempt, request_claim = consumed[0]
        _need(current is not None and current.status == 'COMPLETE' and current.metadata['pagination_complete']
              and current.metadata.get('capture_attempt_dir') == str(attempt)
              and not (attempt / 'attempt-failed.json').exists()
              and not (attempt / 'attempt-failed.json').is_symlink(),
              'CONTINUATION_AUTHORIZED_ATTEMPT_ALREADY_CONSUMED_NO_RETRY')
        _need(_stamp(claim['claimed_at']) <= _stamp(request_claim['actual_started_at'])
              <= _stamp(current.metadata['started_at']) <= _stamp(current.metadata['completed_at'])
              <= datetime.now(timezone.utc), 'CONTINUATION_CONSUMPTION_CLOCK_BINDING_CHANGED')
        raw, _ = cache._read(attempt / 'committed.json', limit=2 * 1024**2)
        committed = _json(raw)
        _need(committed.get('request_key') == spec.key and committed.get('receipt_id') == current.receipt_id,
              'CONTINUATION_COMPLETED_ATTEMPT_COMMIT_CHANGED')
        require_known_accounting(cache)
        cache._continuation_session = spec.business_session
        require_complete_source(cache, current, cache.verify(current))
        return spec, authorization, current
    _need(current is not None and current.receipt_id == retry['prior_receipt_id']
          and current.status == 'FETCH_FAILED', 'CONTINUATION_ORIGINAL_REQUEST_STATE_CHANGED')
    return spec, authorization, None
