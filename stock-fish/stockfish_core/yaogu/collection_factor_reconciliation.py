"""Present-day reconciliation of one reviewed ancestor UNKNOWN, never a retry.

The existing complete original is reauthenticated. The uncommitted attempt
stays UNKNOWN. Only a claimed continuation may allow its original observation
day's new full-anchor requests to overlap the exact reviewed code set.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path

from stockfish_core.file_integrity import content_equal, content_reference_equal


VERSION = 'PREMARKET_FACTOR_RECONCILIATION_CANDIDATE_V1'
RECORD = 'PREMARKET_FACTOR_RECONCILIATION_RECORD_V1'
INVENTORY = 'INVENTORY_RECONCILED_UNKNOWN_RETAINED'


def _portable(reference):
    return {key: reference[key] for key in ('path', 'sha256', 'bytes')}


def case_from_review(review, review_ref):
    from .premarket_capture_cache_v1 import _need
    _need(review.get('schema_version') == 'PRIVATE_INHERITED_UNKNOWN_INVENTORY_REVIEW_V1'
          and len(review.get('unknown_attempts', [])) == 1, 'RECONCILIATION_EXACT_ONE_REVIEWED_UNKNOWN')
    item = review['unknown_attempts'][0]
    complete = item['existing_complete_counterparts_verified_now']
    _need(len(complete) == 1, 'RECONCILIATION_EXACT_ONE_REVIEWED_COMPLETE')
    return dict(request_key=item['source_request_key'], unknown_attempt_dir=item['attempt'],
        unknown_attempt_refs=[_portable(ref) for ref in item['directory_manifest'] if ref['type'] == 'FILE'],
        complete_receipt_id=complete[0]['receipt_id'], codes=item['request_spec']['parameters']['codes'],
        review_ref=_portable(review_ref))


def prepare_factor_reconciliation(cache, *, operation_ref, operation, scope, scope_directory,
                                  source_job, expected_case):
    from .collection_inherited_scope import inherited_attempt_scope
    from .premarket_capture_cache_v1 import RequestSpec, _canonical, _json, _need, _stamp

    references = {}

    def bind(source, *, document=False):
        raw, ref = cache._read(source, limit=16 * 1024**2)
        references[ref['path']] = _portable(ref)
        return _json(raw) if document else _portable(ref)

    actual = bind(operation_ref, document=True)
    directory = Path(scope_directory)
    operation_id = operation.get('operation_id')
    _need(actual == operation and operation.get('scope') == scope
          and operation.get('schema_version') == 'CODEX_CHAT_WORKSPACE_V1'
          and operation.get('phase') == 'FAILED_OR_UNKNOWN'
          and operation_id == hashlib.sha256(_canonical(dict(scope=scope, identity=operation['identity']))).hexdigest()
          and directory.is_absolute() and directory.resolve(strict=True) == directory
          and directory.name == hashlib.sha256(_canonical(scope)).hexdigest()
          and operation_ref['path'] == str(directory / 'operations' / (operation_id + '.json')),
          'RECONCILIATION_ORIGINAL_OPERATION_BINDING')
    identity = operation['identity']
    observed, target = identity['observation_date'], identity['target_session']
    _need(cache.root == Path(identity['state_root']) / 'cache'
          and Path(source_job).parent == cache.root.parent / 'jobs'
          and Path(source_job).is_dir(), 'RECONCILIATION_ORIGINAL_RUNTIME_ONLY')
    case = dict(expected_case)
    _need(set(case) == {'request_key', 'unknown_attempt_dir', 'unknown_attempt_refs', 'complete_receipt_id',
                       'codes', 'review_ref'}, 'RECONCILIATION_REVIEW_CASE_SCHEMA')
    codes = case['codes']
    _need(isinstance(codes, list) and len(codes) == len(set(codes)) == 50
          and len(case['unknown_attempt_refs']) == 6, 'RECONCILIATION_EXACT_FIFTY_CODES_AND_SIX_FILES')
    entries = list(cache._entries(case['request_key']))
    _need(len(entries) == 1, 'RECONCILIATION_SINGLE_IMPORTED_ORIGINAL_REQUIRED')
    current = entries[0]
    prior = RequestSpec(**current['request_spec'])
    _need(prior.key == case['request_key'] and prior.endpoint == 'adjustment_factors'
          and prior.current is False and prior.parameters['start_date'] == '1990-01-01'
          and prior.parameters['end_date'] < observed < target
          and set(prior.parameters['codes']) == set(codes)
          and current['status'] == 'COMPLETE' and current['pagination_complete'] is True
          and current['receipt_id'] == case['complete_receipt_id']
          and current.get('capture_attempt_dir') is None and current.get('verification_locator') is None,
          'RECONCILIATION_EXACT_EXISTING_COMPLETE_SCOPE')
    folder = cache.root / 'raw' / prior.key
    marker = bind(folder / 'inherited-original-attempt' / 'request-claim.json', document=True)
    source_attempt = Path(marker['source_attempt_directory'])
    unknown = Path(case['unknown_attempt_dir'])
    _need(unknown.parent == source_attempt / 'raw', 'RECONCILIATION_REVIEWED_ANCESTOR_ATTEMPT_ONLY')
    proof = inherited_attempt_scope(cache, folder, entries=entries, retain_known_terminal_failures=True,
        retained_unknown_attempts=[dict(attempt_dir=str(unknown), references=case['unknown_attempt_refs'])])
    _need(proof is not None and proof.get('status') == INVENTORY
          and proof['receipt_ids'] == [case['complete_receipt_id']],
          'RECONCILIATION_ANCESTOR_INVENTORY_NOT_EXACT')
    # Every source/control byte used below is retained in the candidate and the
    # cache's normal terminal registry. The old review itself is a locator: its
    # original UTF-8 bytes are preserved by the coordinator's sealed record.
    bind(cache.root / 'epoch-claim.json')
    bind(cache.root / 'epoch-complete.json')
    bind(current['source_provenance']['seed'])
    bind(cache.root / 'index' / prior.key / (hashlib.sha256(_canonical(current)).hexdigest() + '.json'))
    for role in ('raw', 'receipt', 'result'):
        bind(current[role])
    for reference in current['producer_files']:
        bind(reference)
    old_indices = sorted((source_attempt.parent.parent / 'index' / prior.key).glob('*.json'))
    _need(len(old_indices) == 1, 'RECONCILIATION_ANCESTOR_SINGLE_COMPLETE_INDEX')
    old = bind(old_indices[0], document=True)
    complete_attempt = Path(old['capture_attempt_dir'])
    complete_claim = bind(complete_attempt / 'request-claim.json', document=True)
    bind(complete_attempt / 'committed.json')
    for reference in case['unknown_attempt_refs']:
        bind(reference)
    unknown_claim = bind(unknown / 'request-claim.json', document=True)
    _need(RequestSpec(**unknown_claim['request_spec']).key == prior.key
          and _stamp(unknown_claim['actual_started_at']) < _stamp(complete_claim['actual_started_at'])
          <= _stamp(current['started_at']) <= _stamp(current['completed_at'])
          < _stamp(operation['started_at']), 'RECONCILIATION_DECLARED_SOURCE_CLOCK_ORDER')
    cache.verify(current)
    return dict(schema_version=VERSION, scope=scope, scope_directory=str(directory), operation_id=operation_id,
        original_operation_ref=_portable(operation_ref), source_job=str(source_job), cache_root=str(cache.root),
        original_operation_finished_at=operation['finished_at'],
        observation_date=observed, target_session=target, allowed_start_date='1990-01-01', allowed_end_date=observed,
        authorized_codes=sorted(codes), old_request_key=prior.key, review_ref=_portable(case['review_ref']),
        expected_case={**case, 'review_ref': _portable(case['review_ref']),
                       'unknown_attempt_refs': [_portable(ref) for ref in case['unknown_attempt_refs']]},
        existing_complete_receipt_id=case['complete_receipt_id'], inventory=proof,
        references=[references[key] for key in sorted(references)],
        old_request_retry_authorized=False, unknown_source_completion='UNKNOWN',
        historical_preservation_proven=False, historical_authorization_proven=False,
        source_capability_issued=False, budget_credit=0)


def verify_factor_reconciliation(cache, evidence, *, operation_ref=None, operation=None,
                                 scope=None, scope_directory=None, source_job=None):
    from .premarket_capture_cache_v1 import _json, _need
    _need(evidence.get('schema_version') == VERSION, 'RECONCILIATION_CANDIDATE_SCHEMA')
    for reference in evidence['references']:
        cache._read(reference, limit=16 * 1024**2)
    operation_ref = operation_ref or evidence['original_operation_ref']
    if operation is None:
        raw, _ = cache._read(operation_ref, limit=16 * 1024**2)
        operation = _json(raw)
    rebuilt = prepare_factor_reconciliation(cache, operation_ref=operation_ref, operation=operation,
        scope=scope if scope is not None else evidence['scope'],
        scope_directory=scope_directory or evidence['scope_directory'], source_job=source_job or evidence['source_job'],
        expected_case=evidence['expected_case'])
    _need(content_equal(rebuilt, evidence), 'RECONCILIATION_EVIDENCE_CHANGED')
    return rebuilt


def reconciled_inherited_scope(cache, folder, evidence):
    from .premarket_capture_cache_v1 import _need
    value = verify_factor_reconciliation(cache, evidence)
    _need(Path(folder) == cache.root / 'raw' / value['old_request_key'], 'RECONCILIATION_INVENTORY_SCOPE_CHANGED')
    return value['inventory']


def verify_claimed_factor_reconciliation(cache):
    """Authenticate the sealed relationship before any authorized source call."""
    from .premarket_capture_cache_v1 import SH, _json, _need, _stamp
    context = cache.continuation_context
    _need(isinstance(context, dict) and context.get('schema_version') == 'PREMARKET_CONTINUATION_AUTHORIZATION_V1',
          'RECONCILIATION_CONTINUATION_CONTEXT_REQUIRED')
    directory = Path(context['scope_directory'])
    slot = directory / 'operations' / context['operation_id'] / 'continuation'

    def read(reference, path):
        _need(reference['path'] == str(path), 'RECONCILIATION_FIXED_RECORD_PATH_REQUIRED')
        raw, ref = cache._read(reference, limit=16 * 1024**2)
        return _json(raw), ref

    claim, _ = read(context['claim'], slot / 'claim.json')
    proposal, proposal_ref = read(context['proposal'], slot / 'proposal.json')
    _need(claim.get('schema_version') == 'PREMARKET_CONTINUATION_CLAIM_V1'
          and all(claim.get(key) == context.get(key) for key in ('scope', 'operation_id'))
          and content_reference_equal(claim['proposal_ref'], proposal_ref),
          'RECONCILIATION_CLAIM_BINDING_CHANGED')
    record, _ = read(claim['factor_reconciliation_ref'], slot / 'reconciliation.json')
    evidence = record['reconciliation']
    _need(record.get('schema_version') == RECORD
          and content_equal(proposal.get('factor_reconciliation'), evidence)
          and evidence.get('scope') == context['scope']
          and evidence.get('scope_directory') == str(directory)
          and evidence.get('operation_id') == context['operation_id']
          and content_reference_equal(evidence['original_operation_ref'], claim['original_operation_ref'])
          and evidence.get('source_job') == claim.get('source_job'),
          'RECONCILIATION_REGISTERED_OPERATION_BINDING_CHANGED')
    review_ref = evidence['review_ref']
    review_bytes = record['review_json'].encode('utf-8')
    _need(hashlib.sha256(review_bytes).hexdigest() == review_ref['sha256']
          and len(review_bytes) == review_ref['bytes'], 'RECONCILIATION_ORIGINAL_REVIEW_BYTES_CHANGED')
    review = _json(review_bytes)
    _need(max(_stamp(evidence['original_operation_finished_at']), _stamp(review['checked_at']))
          <= _stamp(record['registered_at']) <= _stamp(claim['claimed_at']) <= datetime.now(timezone.utc)
          and _stamp(record['registered_at']).astimezone(SH).date().isoformat() ==
              claim['source_retry']['request_spec']['business_session'],
          'RECONCILIATION_REGISTRATION_CLOCK_BINDING_CHANGED')
    if Path(review_ref['path']).exists() or Path(review_ref['path']).is_symlink():
        cache._read(review_ref, limit=16 * 1024**2)
    _need(content_equal(case_from_review(review, review_ref), evidence['expected_case']),
          'RECONCILIATION_REVIEW_CASE_CHANGED')
    return verify_factor_reconciliation(cache, evidence)


def factor_reconciliation_allows(cache, folder, spec):
    """Grant only this retained old blocker's original observation-day overlap."""
    from .premarket_capture_cache_v1 import _need
    if cache.continuation_context is None or not cache._continuation_ready:
        return None
    denied = dict(status='RECONCILIATION_NOT_AUTHORIZED_UNKNOWN_RETAINED', retry_authorized=False)
    try:
        value = verify_claimed_factor_reconciliation(cache)
        params = spec.parameters
        _need(Path(folder) == cache.root / 'raw' / value['old_request_key']
              and spec.endpoint == 'adjustment_factors' and spec.key != value['old_request_key']
              and spec.current is False and spec.business_session == value['observation_date']
              and params['start_date'] == '1990-01-01' and params['end_date'] == value['observation_date']
              and bool(set(params['codes']) & set(value['authorized_codes'])),
              'RECONCILIATION_ONLY_ORIGINAL_OBSERVATION_NEW_FULL_ANCHOR_OVERLAP')
        return dict(status='AUTHORIZED_NEW_OBSERVATION_OVERLAP_ONLY', request_key=value['old_request_key'],
                    unknown_source_completion='UNKNOWN', old_request_retry_authorized=False, budget_credit=0)
    except (ValueError, TypeError, KeyError, OSError) as error:
        return dict(denied, reason=str(error)[:256])
