"""Explicit, retrospective factor-resume lineage bridge; no default dispatch.

Actual final RAW clocks pass the frozen turnover gate. An older PARTIAL trio
can prove an inherited per-security fragment, without supplying its missing
RAW clock, upgrading that PARTIAL batch or authorizing an April cutoff.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

from .contracts import DailyBar, MarketInput, SourceReceipt, digest
from . import historical_replay as frozen
from . import historical_replay_turnover as turnover

VERSION = 'R12_DATED_REGISTERED_IDENTITY_FACTOR_LINEAGE_TURNOVER_RECONSTRUCTION_V1'
MAX_CAPTURE_BYTES = 8 * 1024 * 1024
_ROOT = Path(__file__).resolve().parents[2]
_SPEC = _ROOT / 'docs' / 'HISTORICAL_FACTOR_RESUME_LINEAGE_V1.md'


@dataclass(frozen=True)
class AncestorCapture:
    """Paths to original archived files, never to a saved research result."""
    capture_root: Path
    result_path: Path


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _binding(path):
    path = Path(path).resolve()
    before = path.stat()
    sha = _sha(path)
    after = path.stat()
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns')
    if any(getattr(before, key) != getattr(after, key) for key in fields):
        raise ValueError('LINEAGE_SOURCE_CHANGED_DURING_HASH')
    return {'path': str(path), 'sha256': sha,
            'stat': {key: getattr(after, key) for key in fields}}


def _load(path):
    if Path(path).stat().st_size > MAX_CAPTURE_BYTES:
        raise ValueError('LINEAGE_CAPTURE_SIZE_LIMIT')
    before = _binding(path)
    value = json.loads(Path(path).read_bytes())
    if _binding(path) != before:
        raise ValueError('LINEAGE_SOURCE_CHANGED_DURING_READ')
    return value, before


def _identity_hash(value):
    # The original provider uses the default json separators/ensure_ascii.
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _exact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _row_multiset(rows):
    return Counter(_exact(row) for row in rows)


def _count(value, actual):
    if value is not None and (type(value) is not int or value != actual):
        raise ValueError('LINEAGE_ORIGINAL_ROW_COUNT_CONFLICT')


def adapter_source_identity():
    parent = turnover.adapter_source_identity()
    hashes = dict(parent['source_file_hashes'])
    for path in (Path(__file__), _SPEC, Path(__file__).with_name('provider_budget.py')):
        hashes[str(path.relative_to(_ROOT))] = _sha(path)
    value = {'normalizer_version': VERSION, 'base_adapter_version': turnover.VERSION,
             'base_adapter_source_hash': parent['source_hash'],
             'source_file_hashes': hashes,
             'scope': 'CURRENT_CUTOFF_RETROSPECTIVE_RESEARCH_ONLY'}
    return {**value, 'source_hash': digest(value)}


def _authenticate(root, original, *, result_binding, final, identity):
    """Fresh whole-trio identity, protocol, scope and archive-clock checks."""
    integrity = frozen.verify_capture(root, original)
    raw, raw_file = _load(integrity['raw_path'])
    receipt, receipt_file = _load(integrity['receipt_path'])
    if raw_file['sha256'] != receipt['content_hash']:
        raise ValueError('LINEAGE_RAW_PARENT_HASH_CONFLICT')
    if original['receipts'] != [receipt] or original['request_receipt_id'] != receipt['receipt_id']:
        raise ValueError('LINEAGE_EMBEDDED_RECEIPT_CONFLICT')
    if _identity_hash({k: v for k, v in receipt.items() if k != 'receipt_id'}) != receipt['receipt_id']:
        raise ValueError('LINEAGE_RECEIPT_SELF_HASH_CONFLICT')
    params = original['parameters']
    if (original['provider'] != 'baostock' or original['endpoint'] != 'adjustment_factors'
            or receipt['provider'] != original['provider']
            or receipt['dataset'] != original['endpoint'] or receipt['parameters'] != params):
        raise ValueError('LINEAGE_ORIGINAL_PROVIDER_OR_SCOPE_CONFLICT')
    request = {key: original[key] for key in ('endpoint', 'parameters', 'provider')}
    if original['request_id'] != _identity_hash(request):
        raise ValueError('LINEAGE_REQUEST_SELF_HASH_CONFLICT')
    if receipt.get('integrity_scope') != 'LOCAL_SHA256_NOT_SECURITY_AUTHENTICATION':
        raise ValueError('LINEAGE_INTEGRITY_SCOPE_CONFLICT')
    if (Path(raw_file['path']).name != receipt['content_hash'] + '.raw.json'
            or Path(receipt_file['path']).name != receipt['receipt_id'] + '.receipt.json'):
        raise ValueError('LINEAGE_ARCHIVE_FILENAME_CONFLICT')
    if not final and Path(result_binding['path']).name != (
            'request-' + original['request_id'] + '-' + receipt['receipt_id'][:16] + '.json'):
        raise ValueError('LINEAGE_ANCESTOR_ARCHIVE_WRAPPER_REQUIRED')
    implementations = original.get('implementation_source_hashes', {})
    for name in ('provider_requests.py', 'provider_requests_worker.py', 'provider_budget.py'):
        if implementations.get(name) != identity['source_file_hashes']['stockfish_core/yaogu/' + name]:
            raise ValueError('LINEAGE_PROVIDER_IMPLEMENTATION_CONFLICT:' + name)
    if raw.get('provider', original['provider']) != original['provider']:
        raise ValueError('LINEAGE_RAW_PROVIDER_CONFLICT')
    for key in ('status', 'field_version', 'pagination_complete'):
        if key not in raw or raw[key] != receipt[key] or raw[key] != original[key]:
            raise ValueError('LINEAGE_THREE_PARENT_FIELD_CONFLICT:' + key)
    if receipt['field_version'] != turnover.PROVIDER_FIELD_VERSION:
        raise ValueError('LINEAGE_UNREGISTERED_PROVIDER_FIELD_VERSION')
    if type(raw['pagination_complete']) is not bool:
        raise ValueError('LINEAGE_PAGINATION_NOT_BOOLEAN')
    defaults = {'rows': [], 'coverage': {}, 'gaps': [], 'failure_category': None,
                'error_code': None, 'diagnostics': {}}
    for key, default in defaults.items():
        if key not in original or _exact(original[key]) != _exact(raw.get(key, default)):
            raise ValueError('LINEAGE_RAW_WRAPPER_PROTOCOL_CONFLICT:' + key)
        if key in receipt and _exact(receipt[key]) != _exact(raw.get(key, default)):
            raise ValueError('LINEAGE_RECEIPT_PROTOCOL_CONFLICT:' + key)
    if (receipt['source_url'] != raw.get('source_url')
            or ('source_url' in original and original['source_url'] != raw.get('source_url'))):
        raise ValueError('LINEAGE_SOURCE_URL_CONFLICT')
    codes = params.get('codes')
    if (not isinstance(codes, list) or not 1 <= len(codes) <= 100
            or any(not frozen._iid(code) for code in codes) or len(set(codes)) != len(codes)):
        raise ValueError('LINEAGE_REQUESTED_CODE_SCOPE_CONFLICT')
    start, end = date.fromisoformat(params['start_date']), date.fromisoformat(params['end_date'])
    if start > end or not isinstance(raw['rows'], list):
        raise ValueError('LINEAGE_ORIGINAL_DATE_SCOPE_CONFLICT')
    rows = defaultdict(list)
    required = {'code', 'dividOperateDate', 'adjustFactor', 'backAdjustFactor', 'foreAdjustFactor'}
    for row in raw['rows']:
        if (not isinstance(row, dict) or not required <= row.keys()
                or any(not isinstance(row[key], str) for key in required)
                or row['code'] not in codes
                or not start <= date.fromisoformat(row['dividOperateDate']) <= end):
            raise ValueError('LINEAGE_FACTOR_ROW_SCHEMA_OR_SCOPE_CONFLICT')
        rows[row['code']].append(row)
    for obj in (raw, receipt, original):
        if 'row_count' in obj:
            _count(obj['row_count'], len(raw['rows']))
    coverage = raw['coverage']
    if not isinstance(coverage, dict):
        raise ValueError('LINEAGE_COVERAGE_NOT_OBJECT')
    if 'returned_rows' in coverage:
        _count(coverage['returned_rows'], len(raw['rows']))
    for key, target in (('requested_codes', codes), ('returned_codes', sorted(rows))):
        if key in coverage:
            values = coverage[key]
            if (not isinstance(values, list) or len(values) != len(set(values))
                    or (values != target if key == 'requested_codes' else set(values) != set(target))):
                raise ValueError('LINEAGE_COVERAGE_CODE_SCOPE_CONFLICT')
    statuses = {}
    values = coverage.get('security_statuses', [])
    if not isinstance(values, list):
        raise ValueError('LINEAGE_SECURITY_STATUSES_NOT_LIST')
    for value in values:
        if not isinstance(value, dict) or value.get('code') not in codes or value['code'] in statuses:
            raise ValueError('LINEAGE_SECURITY_STATUS_SCOPE_OR_UNIQUENESS_CONFLICT')
        _count(value.get('row_count'), len(rows[value['code']]))
        statuses[value['code']] = value
    started = turnover._capture_clock(original['started_at'], 'RESULT.started_at')
    completed = turnover._capture_clock(original['completed_at'], 'RESULT.completed_at')
    seen = turnover._capture_clock(receipt['first_seen_at'], 'RECEIPT.first_seen_at')
    ingested = turnover._capture_clock(receipt['ingested_at'], 'RECEIPT.ingested_at')
    if not started <= seen <= ingested <= completed:
        raise ValueError('LINEAGE_ARCHIVE_WRAPPER_CLOCK_ORDER_CONFLICT')
    if 'first_seen_at' in original and turnover._capture_clock(original['first_seen_at'], 'RESULT.first_seen_at') != seen:
        raise ValueError('LINEAGE_WRAPPER_RECEIPT_CLOCK_CONFLICT')
    if 'first_seen_at' in raw and turnover._capture_clock(raw['first_seen_at'], 'RAW.first_seen_at') != seen:
        raise ValueError('LINEAGE_RAW_RECEIPT_CLOCK_CONFLICT')
    if final:
        turnover._verify_clock_contract(original, raw)  # Frozen gate, unchanged.
        if (raw['status'] != 'COMPLETE' or not raw['pagination_complete']
                or coverage.get('cursor_exhausted') is not True or raw['gaps']
                or raw.get('error_code') is not None or raw.get('failure_category') is not None
                or set(statuses) != set(codes)):
            raise ValueError('LINEAGE_FINAL_COMPLETE_SCOPE_REQUIRED')
    elif raw['status'] != 'PARTIAL' or raw['pagination_complete']:
        raise ValueError('LINEAGE_ANCESTOR_MUST_RETAIN_PARTIAL_STATUS')
    return {'original': original, 'receipt': receipt, 'raw': raw, 'rows': dict(rows),
            'statuses': statuses, 'started': started, 'completed': completed,
            'files': (raw_file, receipt_file, result_binding),
            'raw_clock_status': ('OBSERVED_RAW_RECEIPT_CONSISTENT' if 'first_seen_at' in raw
                                 else 'UNKNOWN_MISSING_RAW_FIRST_SEEN_AT'),
            'archive_clock_role': 'ACTUAL_ARCHIVE_PROXY_NOT_SOURCE_PUBLICATION_OR_PIT'}


def _complete_fragment(status, rows):
    return (status is not None and status.get('status') == 'COMPLETE'
            and set(status) == {'code', 'status', 'cursor_exhausted', 'row_count', 'captured_at'}
            and status.get('cursor_exhausted') is True
            and type(status.get('row_count')) is int and status['row_count'] == len(rows)
            and isinstance(status.get('captured_at'), str))


class HistoricalReplayFactorLineageTurnoverAssembler:
    """Opt-in inline research adapter; original price/qualification/turn math."""
    def __init__(self, capture_dir, policy, *, ancestor_captures=()):
        self._identity = adapter_source_identity()
        self._adapter = turnover.HistoricalReplayTurnoverAssembler(capture_dir, policy)
        self._ancestor_specs = tuple(ancestor_captures)
        self._ancestors = []
        for spec in self._ancestor_specs:
            original, binding = _load(spec.result_path)
            self._ancestors.append(_authenticate(spec.capture_root, original,
                result_binding=binding, final=False, identity=self._identity))
        self._finals = {}
        result_paths = {}
        for folder in ('checkpoints', 'results'):
            for path in sorted((self._adapter._base.root / folder).glob('*.json')):
                value, binding = _load(path)
                result_paths.setdefault(value.get('request_receipt_id'), binding)
        for original in self._adapter._base.results:
            if original['endpoint'] == 'adjustment_factors':
                rid = original['request_receipt_id']
                self._finals[rid] = _authenticate(self._adapter._base.root, original,
                    result_binding=result_paths[rid], final=True, identity=self._identity)
        self._files = {item['path']: item for parent in (*self._finals.values(), *self._ancestors)
                       for item in parent['files']}
        # Bind every actual active parent, including MD/master/calendar/IPO,
        # rather than only factor parents summarized by the lineage proof.
        for integrity in self._adapter._base.integrity.values():
            for key in ('raw_path', 'receipt_path'):
                item = _binding(integrity[key])
                self._files[item['path']] = item
        for item in result_paths.values():
            self._files[item['path']] = item
        self._context_hash = digest([self._finals, self._ancestors])
        self._assert_fixed()

    def _assert_fixed(self):
        if adapter_source_identity() != self._identity:
            raise ValueError('LINEAGE_ADAPTER_SOURCE_CHANGED')
        self._adapter._assert_fixed_inputs()
        if digest([self._finals, self._ancestors]) != self._context_hash:
            raise ValueError('LINEAGE_CACHED_ORIGINAL_CONTEXT_CHANGED')
        for binding in self._files.values():
            if _binding(binding['path']) != binding:
                raise ValueError('LINEAGE_ORIGINAL_SOURCE_CHANGED')

    def _proofs(self):
        proofs = {}
        for rid, final in self._finals.items():
            for code in final['original']['parameters']['codes']:
                status, rows = final['statuses'].get(code), final['rows'].get(code, [])
                proof = {'original_final_receipt_id': rid, 'code': code,
                         'original_per_security_status': status,
                         'original_final_wrapper_interval': {
                             'started_at': final['original']['started_at'],
                             'completed_at': final['original']['completed_at']},
                         'exact_rows_multiset_hash': digest(sorted(_row_multiset(rows).items())),
                         'status': 'UNKNOWN', 'reason': 'PER_SECURITY_COMPLETE_CAPTURE_NOT_ESTABLISHED',
                         'ancestor_receipt_ids': [], 'original_out_of_parent_gap': None}
                proofs[(rid, code)] = proof
                if not _complete_fragment(status, rows):
                    continue
                captured = turnover._capture_clock(status['captured_at'], 'SECURITY.captured_at')
                if final['started'] <= captured <= final['completed']:
                    proof.update(status='PROVEN_FINAL_PARENT_FRAGMENT', reason=None)
                    continue
                proof['original_out_of_parent_gap'] = 'PER_SECURITY_CAPTURE_OUTSIDE_FINAL_PARENT_CLOCK'
                proof['reason'] = 'GENUINE_PARTIAL_ANCESTOR_FRAGMENT_NOT_ESTABLISHED'
                for ancestor in self._ancestors:
                    old = ancestor['original']
                    oldstatus = ancestor['statuses'].get(code)
                    oldrows = ancestor['rows'].get(code, [])
                    if (old['parameters'] == final['original']['parameters']
                            and old['provider'] == final['original']['provider']
                            and old['field_version'] == final['original']['field_version']
                            and ancestor['completed'] <= final['started']
                            and ancestor['started'] <= captured <= ancestor['completed']
                            and _complete_fragment(oldstatus, oldrows)
                            and _exact(oldstatus) == _exact(status)
                            and _row_multiset(oldrows) == _row_multiset(rows)):
                        proof['ancestor_receipt_ids'].append(old['request_receipt_id'])
                if proof['ancestor_receipt_ids']:
                    proof.update(status='PROVEN_GENUINE_PARTIAL_ANCESTOR_FRAGMENT', reason=None)
        return proofs

    def build(self, observation_date, *, history_sessions=26,
              include_security_history=False, history_store=None):
        if history_store is not None:
            raise ValueError('LINEAGE_INLINE_ONLY_HISTORY_STORE_UNSUPPORTED')
        self._assert_fixed()
        base, audit = self._adapter.build(observation_date, history_sessions=history_sessions,
                                         include_security_history=include_security_history)
        proofs = self._proofs()
        issued = datetime.now(timezone.utc)
        for parent in (*self._finals.values(), *self._ancestors):
            if parent['completed'] > issued:
                raise ValueError('LINEAGE_ORIGINAL_ARCHIVE_AFTER_ACTUAL_ISSUANCE')
        for receipt in base.receipts:
            if max(receipt.first_seen_at, receipt.ingested_at) > issued:
                raise ValueError('LINEAGE_BASE_ARCHIVE_AFTER_ACTUAL_ISSUANCE')
        for clocks in audit['original_capture_clock_contract'].values():
            completed = clocks['result_completed_at']
            if completed is not None and turnover._capture_clock(completed, 'RESULT.completed_at') > issued:
                raise ValueError('LINEAGE_BASE_ARCHIVE_AFTER_ACTUAL_ISSUANCE')
        source_by = {value['normalized_receipt_id']: value for value in audit['sources']}
        receipts = {value.receipt_id: value for value in base.receipts}
        new_receipts = {rid: value for rid, value in receipts.items() if value.dataset != 'adjustment_factors'}
        validation_identity = {'version': VERSION, 'source_bindings': list(self._files.values()),
                               'proofs': list(proofs.values()), 'issued_at': issued.isoformat(),
                               'base_adapter_input_hash': audit['input_hash'],
                               'base_reconstruction_audit_hash': digest(audit),
                               'adapter_source_hash': self._identity['source_hash']}
        validation_id = 'factor_lineage_validation_' + digest(validation_identity)
        new_receipts[validation_id] = SourceReceipt(receipt_id=validation_id,
            provider='stockfish_local_research_validation', dataset='factor_resume_lineage_validation',
            content_hash=digest(validation_identity), field_version=VERSION,
            row_count=len(proofs), pagination_complete=True, status='COMPLETE',
            source_type='current_cutoff_lineage_validation_not_factor_batch_completeness',
            parameters={'research_only': True, 'original_clocks_changed': False},
            instrument_ids=tuple(sorted({bar.instrument_id for bar in base.bars})),
            business_sessions=tuple(sorted({bar.session for bar in base.bars})),
            measurement_definition='PROTOCOL_VALIDATION_WITH_UNKNOWN_RESULTS_RETAINED',
            event_time=issued, source_published_at=issued, first_seen_at=issued, ingested_at=issued)
        mappings, new_sources = {}, [value for value in audit['sources']
                                     if receipts[value['normalized_receipt_id']].dataset != 'adjustment_factors']
        ancestor_by = {value['original']['request_receipt_id']: value for value in self._ancestors}
        ancestor_refs = {}
        for oldrid, receipt in receipts.items():
            if receipt.dataset != 'adjustment_factors':
                continue
            source = source_by[oldrid]
            origin = source['original_receipt_id']
            if len(receipt.instrument_ids) != 1:
                raise ValueError('LINEAGE_SINGLE_SECURITY_NORMALIZED_FACTOR_REQUIRED')
            iid = receipt.instrument_ids[0]
            code = iid.split('.')[1].lower() + '.' + iid.split('.')[0]
            proof = proofs[(origin, code)]
            if proof['status'] == 'UNKNOWN':
                mappings[oldrid] = ()
                continue
            parents = []
            for ancestor_id in proof['ancestor_receipt_ids']:
                if ancestor_id not in ancestor_refs:
                    ancestor = ancestor_by[ancestor_id]
                    parent = ancestor['receipt']
                    seen = turnover._capture_clock(parent['first_seen_at'], 'RECEIPT.first_seen_at')
                    ingested = turnover._capture_clock(parent['ingested_at'], 'RECEIPT.ingested_at')
                    normalized_id = 'factor_lineage_parent_' + digest([VERSION, ancestor_id, ancestor['files']])
                    new_receipts[normalized_id] = SourceReceipt(receipt_id=normalized_id,
                        provider=parent['provider'], dataset='adjustment_factors_ancestor_archive',
                        content_hash=parent['content_hash'], field_version=parent['field_version'],
                        row_count=len(ancestor['raw']['rows']), pagination_complete=parent['pagination_complete'],
                        status=parent['status'], source_url=parent['source_url'],
                        source_type='original_partial_factor_archive_proxy',
                        parameters={'original_receipt_id': ancestor_id,
                                    'original_receipt_row_count': parent['row_count'],
                                    'RAW_first_seen_status': ancestor['raw_clock_status']},
                        instrument_ids=tuple(frozen._iid(c) for c in ancestor['original']['parameters']['codes']),
                        business_sessions=receipt.business_sessions,
                        measurement_definition='ARCHIVE_PROXY_NOT_SOURCE_PUBLICATION_OR_PIT',
                        event_time=seen, source_published_at=seen, first_seen_at=seen, ingested_at=ingested)
                    ancestor_refs[ancestor_id] = normalized_id
                else:
                    normalized_id = ancestor_refs[ancestor_id]
                    # A single genuine ancestor may support several normalized
                    # fragments. Preserve the union of real business sessions.
                    parent = new_receipts[normalized_id]
                    new_receipts[normalized_id] = SourceReceipt.model_validate({**parent.model_dump(mode='python'),
                        'business_sessions': tuple(sorted(set(parent.business_sessions) | set(receipt.business_sessions)))})
                parents.append(ancestor_refs[ancestor_id])
            identity = {'version': VERSION, 'old_normalized_receipt_id': oldrid,
                        'proof': proof, 'final_files': self._finals[origin]['files'],
                        'ancestor_files': [ancestor_by[value]['files'] for value in proof['ancestor_receipt_ids']],
                        'adapter_source_hash': self._identity['source_hash'], 'issued_at': issued.isoformat()}
            newrid = 'factor_lineage_fragment_' + digest(identity)
            new_receipts[newrid] = SourceReceipt.model_validate({**receipt.model_dump(mode='python'),
                'receipt_id': newrid, 'field_version': VERSION, 'definition_version': VERSION,
                'source_type': 'current_cutoff_validated_per_security_factor_fragment',
                'parameters': {**receipt.parameters, 'lineage_proof_hash': digest(identity),
                               'original_final_receipt_id': origin, 'research_only': True},
                'measurement_definition': 'CURRENT_VALIDATION_NOT_ARCHIVED_PIT_OR_BATCH_UPGRADE',
                'source_published_at': issued, 'first_seen_at': issued, 'ingested_at': issued,
                'historical_time_proof': None})
            mappings[oldrid] = (newrid, *parents)
            new_sources.append({**source, 'normalized_receipt_id': newrid,
                'clock_interpretation': 'CURRENT_ACTUAL_VALIDATION_AFTER_ALL_ORIGINAL_ARCHIVES',
                'lineage_proof': identity, 'original_parent_statuses': {
                    'final': self._finals[origin]['receipt']['status'],
                    'ancestors': {value: ancestor_by[value]['receipt']['status'] for value in proof['ancestor_receipt_ids']}}})
        bars, unknown = [], []
        comparable = ('comparable_open', 'comparable_high', 'comparable_low', 'comparable_close', 'comparable_basis')
        for bar in base.bars:
            factor_refs = [ref for ref in bar.source_refs if ref in mappings]
            refs = tuple(dict.fromkeys((*(item for ref in bar.source_refs for item in mappings.get(ref, (ref,))),
                                       validation_id)))
            fields = bar.model_dump(mode='python')
            fields['source_refs'] = refs
            if any(not mappings[ref] for ref in factor_refs):
                fields.update({key: None for key in comparable})
                fields['quality'] = 'PARTIAL'
                unknown.append({'instrument_id': bar.instrument_id, 'session': str(bar.session),
                                'field': 'COMPARABLE_PRICE', 'reason': 'FACTOR_RESUME_LINEAGE_UNKNOWN'})
            if refs:
                fields.update(source_published_at=max(new_receipts[ref].source_published_at for ref in refs),
                              first_seen_at=max(new_receipts[ref].first_seen_at for ref in refs),
                              ingested_at=max(new_receipts[ref].ingested_at for ref in refs))
            bars.append(DailyBar.model_validate(fields))
        bundle = MarketInput.model_validate({**base.model_dump(mode='python'),
                                            'bars': tuple(bars), 'receipts': tuple(new_receipts.values())})
        new_audit = {**audit, 'normalizer_version': VERSION,
            'base_adapter_version': turnover.VERSION, 'base_adapter_input_hash': audit['input_hash'],
            'input_hash': digest(bundle), 'adapter_source_identity': self._identity,
            'sources': new_sources, 'gaps': [*audit['gaps'], *unknown],
            'factor_lineage': {'issued_at': issued.isoformat(),
                'validation_receipt_id': validation_id,
                'scope': 'CURRENT_CUTOFF_RETROSPECTIVE_RESEARCH_ONLY',
                'proofs': list(proofs.values()), 'counts': dict(Counter(p['status'] for p in proofs.values())),
                'original_out_of_parent_gap_count': sum(p['original_out_of_parent_gap'] is not None for p in proofs.values()),
                'original_out_of_parent_gaps_modified': False, 'old_capability_restored': False,
                'ancestor_archives': [{'receipt_id': value['original']['request_receipt_id'],
                    'status': value['receipt']['status'], 'pagination_complete': value['receipt']['pagination_complete'],
                    'RAW_clock_status': value['raw_clock_status'], 'clock_role': value['archive_clock_role'],
                    'files': value['files']} for value in self._ancestors],
                'source_bindings': list(self._files.values()),
                'lineage_unknown_comparable_bars': len(unknown),
                'base_unknown_comparable_bars': sum(bar.comparable_close is None for bar in base.bars),
                'total_unknown_comparable_bars': sum(bar.comparable_close is None for bar in bars)},
            'archived_point_in_time': False, 'original_capture_clocks_modified': False,
            'unchanged_numeric_authorities': ['RAW_PRICE', 'FROZEN_RAW_X_BACK_FACTOR', 'DATED_QUALIFICATION', 'EXACT_TURN_PERCENT'],
            'research_cutoff_requirement': 'ACTUAL_CUTOFF_AT_OR_AFTER_ISSUED_AT'}
        self._assert_fixed()
        return bundle, new_audit
