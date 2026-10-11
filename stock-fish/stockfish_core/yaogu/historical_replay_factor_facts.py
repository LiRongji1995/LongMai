"""Explicit current-cutoff factor value facts with mandatory original audit parents.

This independent version retains the frozen V1/turnover/value functions. Its
fresh, process-local evidence owner is not a SQLite history artifact or a
capability reconstructed from saved JSON. No default dispatch is changed.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import weakref

from .contracts import DailyBar, MarketInput, SourceReceipt, digest
from .history_artifacts import digest_market_input
from . import historical_replay as frozen
from . import historical_replay_turnover as turnover
from . import historical_replay_factor_lineage as lineage
from . import short_horizon_strength as r1

VERSION = 'R12_FACTOR_FRAGMENT_VALUE_FACTS_AND_MANDATORY_AUDIT_V2'
OWNER_VERSION = 'CURRENT_FACTOR_FACT_INLINE_EVIDENCE_OWNER_V1'
AUTHORITY_VERSION = 'FACTOR_FACT_SAME_PROCESS_AUTHORITY_V1'
_ROOT = Path(__file__).resolve().parents[2]
_SPEC = _ROOT / 'docs' / 'HISTORICAL_FACTOR_VALUE_FACTS_V2.md'
_FACTORY = object()
_OWNERS = weakref.WeakKeyDictionary()
_PROVIDER_FILES = ('provider_requests.py', 'provider_requests_worker.py', 'provider_budget.py')


@dataclass(frozen=True)
class CaptureImplementationFiles:
    """Actual code bytes corresponding to original capture implementation hashes.

    Original Git snapshots may differ from current validator code. These files
    are authenticated, not imported or executed. A JSON hash table is not this
    source identity and cannot substitute for the original code bytes.
    """
    provider_requests: Path
    provider_requests_worker: Path
    provider_budget: Path

    def paths(self):
        return (self.provider_requests, self.provider_requests_worker, self.provider_budget)


class _Authority:
    __slots__ = ('__weakref__',)

    def __new__(cls, token=None):
        if token is not _FACTORY:
            raise ValueError('FACTOR_FACT_AUTHORITY_FACTORY_REQUIRED')
        return super().__new__(cls)

    def __reduce__(self):
        raise TypeError('FACTOR_FACT_AUTHORITY_NOT_SERIALIZABLE')


@dataclass(frozen=True, eq=False)
class FactorFactContext:
    """Only a factory-registered instance is usable; public construction grants nothing."""
    bundle: MarketInput
    actual_cutoff: datetime
    issued_at: datetime
    registered_worker_deadline: datetime
    _audit_json: str
    _graph_json: str
    _authority: _Authority

    @property
    def audit(self):
        return json.loads(self._audit_json)

    @property
    def audit_graph(self):
        return json.loads(self._graph_json)


def _now():
    return datetime.now(timezone.utc)


def source_identity():
    base = turnover.adapter_source_identity()
    hashes = dict(base['source_file_hashes'])
    r1_identity = r1.short_horizon_source_identity()
    hashes.update(r1_identity['source_file_hashes'])
    paths = (Path(__file__), Path(lineage.__file__), _SPEC,
             Path(__file__).with_name('factor_fact_consumer.py'),
             Path(__file__).with_name('provider_budget.py'),
             Path(__file__).with_name('strategy_experiments.py'),
             Path(__file__).with_name('history_artifacts.py'),
             Path(__file__).with_name('source_resolution.py'),
             Path(__file__).with_name('short_horizon_strength.py'))
    for path in paths:
        hashes[str(path.relative_to(_ROOT))] = lineage._sha(path)
    value = {'normalizer_version': VERSION, 'owner_version': OWNER_VERSION,
             'authority_version': AUTHORITY_VERSION,
             'base_adapter_version': turnover.VERSION,
             'base_adapter_source_hash': base['source_hash'],
             'unchanged_R1_identity': r1_identity,
             'source_file_hashes': hashes,
             'scope': 'CURRENT_ACTUAL_CUTOFF_RETROSPECTIVE_ONLY'}
    return {**value, 'source_hash': digest(value)}


def _aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('FACTOR_FACT_AWARE_ACTUAL_CLOCK_REQUIRED')
    return value


def _record(context, *, consuming=False):
    if type(context) is not FactorFactContext or type(context._authority) is not _Authority:
        raise ValueError('FACTOR_FACT_GENUINE_CONTEXT_REQUIRED')
    record = _OWNERS.get(context._authority)
    if (record is None or record['context']() is not context or record['pid'] != os.getpid()
            or record['assembler']() is None):
        raise ValueError('FACTOR_FACT_OWNER_OR_PROCESS_MISMATCH')
    allowed = ('CONSUMING',) if consuming else ('ISSUED', 'CONSUMING')
    if record['state'] not in allowed:
        raise ValueError('FACTOR_FACT_AUTHORITY_CLOSED_OR_REUSED')
    if _now() >= record['deadline']:
        raise ValueError('FACTOR_FACT_AUTHORITY_EXPIRED')
    if (context.actual_cutoff != record['cutoff'] or context.issued_at != record['issued_at']
            or context.registered_worker_deadline != record['deadline']
            or context._graph_json is not record['graph_text']
            or context._audit_json is not record['audit_text']):
        raise ValueError('FACTOR_FACT_CONTEXT_DESCRIPTION_CHANGED')
    return record


class _InlineFactReader:
    """New version, actual content hash, inline scope; no DB/partitions or writer."""
    def __init__(self, token, manifest, bundle_rows, securities, authority):
        if token is not _FACTORY:
            raise ValueError('FACTOR_FACT_READER_FACTORY_REQUIRED')
        self._manifest = manifest
        self._hash = digest(manifest)
        self._ref = 'history_' + self._hash  # Existing MarketInput field syntax only.
        self._authority = authority
        self._rows = defaultdict(list)
        for row in bundle_rows:
            self._rows[row.instrument_id].append(row)
        self._securities = defaultdict(list)
        for row in securities:
            self._securities[row.instrument_id].append(row)

    def _check(self, ref, expected_hash):
        record = _OWNERS.get(self._authority)
        if record is None or record.get('reader')() is not self:
            raise ValueError('FACTOR_FACT_READER_OWNER_MISMATCH')
        context = record['context']()
        _record(context, consuming=True)
        if context.bundle._history_store is not self:
            raise ValueError('FACTOR_FACT_READER_REPLACED')
        if ref != self._ref or expected_hash is not None and expected_hash != self._hash:
            raise ValueError('FACTOR_FACT_INLINE_MANIFEST_IDENTITY_MISMATCH')

    def manifest(self, ref, expected_hash=None):
        self._check(ref, expected_hash)
        return self._manifest

    def selected_rows(self, wanted_sessions, wanted_ids):
        """Original typed inline candidates for the frozen R1 revision selector.

        Empty real partitions describe the absence of SQLite. This bridge does
        not choose revisions or bypass the ordinary evidence resolver.
        """
        self._check(self._ref, self._hash)
        record = _OWNERS[self._authority]
        context = record['context']()
        if (digest_market_input(context.bundle) != record['bundle_hash']
                or digest(self._manifest) != record['manifest_hash']):
            raise ValueError('FACTOR_FACT_INLINE_CANDIDATE_IDENTITY_CHANGED')
        wanted = {str(value) for value in wanted_sessions}
        for iid in sorted(wanted_ids):
            for row in self._rows.get(iid, ()):
                if str(row.session) not in wanted:
                    continue
                self._check(self._ref, self._hash)
                if tuple(record['bar_refs'].get(digest(row), ())) != row.source_refs:
                    raise ValueError('FACTOR_FACT_VALUE_ROW_OR_SOURCE_REFS_CHANGED')
                yield row.model_dump(mode='json')
        self._check(self._ref, self._hash)
        if digest_market_input(context.bundle) != record['bundle_hash']:
            raise ValueError('FACTOR_FACT_INLINE_CANDIDATE_IDENTITY_CHANGED')

    def iter_rows(self, ref, kind, *, instrument_id=None, start=None, end=None, expected_hash=None):
        self._check(ref, expected_hash)
        if kind not in ('bar', 'security'):
            raise ValueError('FACTOR_FACT_INLINE_ROW_KIND_UNSUPPORTED')
        rows = self._rows if kind == 'bar' else self._securities
        ids = (instrument_id,) if instrument_id is not None else sorted(rows)
        for iid in ids:
            for row in rows.get(iid, ()):
                day = str(row.session if kind == 'bar' else row.effective_from)
                if end is not None and day > str(end)[:10]:
                    continue
                if kind == 'bar' and start is not None and day < str(start)[:10]:
                    continue
                yield row


class HistoricalReplayFactorFactsAssembler:
    def __init__(self, capture_dir, policy, *, capture_implementation,
                 ancestor_captures=()):
        if type(capture_implementation) is not CaptureImplementationFiles:
            raise ValueError('FACTOR_FACT_REAL_CAPTURE_IMPLEMENTATION_FILES_REQUIRED')
        self._identity = source_identity()
        self._implementation_files = tuple(lineage._binding(p) for p in capture_implementation.paths())
        if any(value['stat']['st_size'] > lineage.MAX_CAPTURE_BYTES for value in self._implementation_files):
            raise ValueError('FACTOR_FACT_CAPTURE_IMPLEMENTATION_SIZE_LIMIT')
        self._capture_identity = {'source_file_hashes': {
            'stockfish_core/yaogu/' + name: value['sha256']
            for name, value in zip(_PROVIDER_FILES, self._implementation_files)},
            'files': self._implementation_files, 'role': 'REAL_ORIGINAL_CAPTURE_CODE_BYTES_NOT_EXECUTED'}
        self._adapter = turnover.HistoricalReplayTurnoverAssembler(capture_dir, policy)
        self._ancestor_specs = tuple(ancestor_captures)
        self._ancestors = []
        for spec in self._ancestor_specs:
            original, binding = lineage._load(spec.result_path)
            self._ancestors.append(lineage._authenticate(spec.capture_root, original,
                result_binding=binding, final=False, identity=self._capture_identity))
        self._finals = {}
        self._result_paths = {}
        for folder in ('checkpoints', 'results'):
            for path in sorted((self._adapter._base.root / folder).glob('*.json')):
                value, binding = lineage._load(path)
                rid = value.get('request_receipt_id')
                if rid in self._result_paths and self._result_paths[rid] != binding:
                    prior, _ = lineage._load(self._result_paths[rid]['path'])
                    if lineage._exact(prior) != lineage._exact(value):
                        raise ValueError('FACTOR_FACT_CONFLICTING_ORIGINAL_RESULT')
                self._result_paths.setdefault(rid, binding)
        for original in self._adapter._base.results:
            if original['endpoint'] == 'adjustment_factors':
                rid = original['request_receipt_id']
                self._finals[rid] = lineage._authenticate(self._adapter._base.root, original,
                    result_binding=self._result_paths[rid], final=True, identity=self._capture_identity)
        self._files = {value['path']: value for parent in (*self._finals.values(), *self._ancestors)
                       for value in parent['files']}
        for integrity in self._adapter._base.integrity.values():
            for key in ('raw_path', 'receipt_path'):
                value = lineage._binding(integrity[key]); self._files[value['path']] = value
        for value in self._result_paths.values():
            self._files[value['path']] = value
        for value in self._implementation_files:
            self._files[value['path']] = value
        for name in self._identity['source_file_hashes']:
            value = lineage._binding(_ROOT/name); self._files[value['path']] = value
        for document in self._adapter._policy['source_documents'].values():
            value = lineage._binding(document['path']); self._files[value['path']] = value
        self._files_digest = digest(self._files)
        self._capture_identity_digest = digest(self._capture_identity)
        self._parents_digest = digest([self._finals, self._ancestors])
        self._assert_fixed()

    def _assert_fixed(self):
        if source_identity() != self._identity:
            raise ValueError('FACTOR_FACT_CURRENT_VALIDATOR_SOURCE_CHANGED')
        self._adapter._assert_fixed_inputs()
        if digest(self._files) != self._files_digest or digest(self._capture_identity) != self._capture_identity_digest:
            raise ValueError('FACTOR_FACT_COMPLETE_SOURCE_BINDING_SCOPE_CHANGED')
        if digest([self._finals, self._ancestors]) != self._parents_digest:
            raise ValueError('FACTOR_FACT_ORIGINAL_CONTEXT_CHANGED')
        for value in self._files.values():
            if lineage._binding(value['path']) != value:
                raise ValueError('FACTOR_FACT_ORIGINAL_WHOLE_SOURCE_CHANGED')

    def _fresh_authenticate(self):
        self._assert_fixed()
        finals, ancestors = {}, []
        for rid, old in self._finals.items():
            original, binding = lineage._load(old['files'][2]['path'])
            finals[rid] = lineage._authenticate(self._adapter._base.root, original,
                result_binding=binding, final=True, identity=self._capture_identity)
        for spec in self._ancestor_specs:
            original, binding = lineage._load(spec.result_path)
            ancestors.append(lineage._authenticate(spec.capture_root, original,
                result_binding=binding, final=False, identity=self._capture_identity))
        if digest([finals, ancestors]) != self._parents_digest:
            raise ValueError('FACTOR_FACT_FRESH_PARENT_AUTHENTICATION_CHANGED')
        for rid, original in self._adapter._base.original_refs.items():
            if frozen.verify_capture(self._adapter._base.root, original) != self._adapter._base.integrity[rid]:
                raise ValueError('FACTOR_FACT_ACTIVE_ORIGINAL_AUTHENTICATION_CHANGED')
        return lineage.HistoricalReplayFactorLineageTurnoverAssembler._proofs(self)

    def build_context(self, observation_date, *, registered_worker_deadline,
                      history_sessions=26, include_security_history=False, history_store=None):
        if history_store is not None:
            raise ValueError('FACTOR_FACT_EXTERNAL_OR_PERSISTENT_STORE_UNSUPPORTED')
        deadline = _aware(registered_worker_deadline)
        if _now() >= deadline:
            raise ValueError('FACTOR_FACT_AUTHORITY_EXPIRED')
        proofs = self._fresh_authenticate()
        base, audit = self._adapter.build(observation_date, history_sessions=history_sessions,
                                          include_security_history=include_security_history)
        if base.history_artifact_ref or base._history_store is not None:
            raise ValueError('FACTOR_FACT_PREEXISTING_HISTORY_CAPABILITY_FORBIDDEN')
        issued = _now()
        for parent in (*self._finals.values(), *self._ancestors):
            if parent['completed'] > issued:
                raise ValueError('FACTOR_FACT_ORIGINAL_ARCHIVE_AFTER_ACTUAL_ISSUANCE')
        for receipt in base.receipts:
            if max(receipt.first_seen_at, receipt.ingested_at) > issued:
                raise ValueError('FACTOR_FACT_BASE_ARCHIVE_AFTER_ACTUAL_ISSUANCE')
        for clocks in audit['original_capture_clock_contract'].values():
            value = clocks['result_completed_at']
            if value is not None and turnover._capture_clock(value, 'RESULT.completed_at') > issued:
                raise ValueError('FACTOR_FACT_BASE_ARCHIVE_AFTER_ACTUAL_ISSUANCE')
        original_receipts = {r.receipt_id: r for r in base.receipts}
        original_sources = {r['normalized_receipt_id']: r for r in audit['sources']}
        public_receipts = {rid: r for rid, r in original_receipts.items() if r.dataset != 'adjustment_factors'}
        private_receipts, mappings, links, fact_sources = {}, {}, {}, []
        ancestors_by = {p['original']['request_receipt_id']: p for p in self._ancestors}
        nodes = []
        proxy_refs = {}
        sessions = tuple(sorted({bar.session for bar in base.bars}))
        for role, parents in (('FINAL', self._finals.values()), ('ANCESTOR', self._ancestors)):
            for parent in parents:
                original, receipt = parent['original'], parent['receipt']
                rid = original['request_receipt_id']
                proxy = 'factor_fact_original_archive_' + digest([VERSION, rid, parent['files']])
                proxy_refs[rid] = proxy
                seen = turnover._capture_clock(receipt['first_seen_at'], 'RECEIPT.first_seen_at')
                ingested = turnover._capture_clock(receipt['ingested_at'], 'RECEIPT.ingested_at')
                public_receipts[proxy] = SourceReceipt(receipt_id=proxy,
                    provider=receipt['provider'], dataset='adjustment_factors_original_archive',
                    content_hash=receipt['content_hash'], field_version=receipt['field_version'],
                    row_count=len(parent['raw']['rows']), pagination_complete=receipt['pagination_complete'],
                    status=receipt['status'], source_url=receipt['source_url'],
                    source_type='original_factor_archive_audit_proxy',
                    parameters={'original_receipt_id': rid, 'original_receipt_row_count': receipt['row_count'],
                                'RAW_first_seen_status': parent['raw_clock_status'], 'parent_role': role},
                    instrument_ids=tuple(frozen._iid(c) for c in original['parameters']['codes']),
                    business_sessions=sessions, measurement_definition=parent['archive_clock_role'],
                    event_time=seen, source_published_at=seen, first_seen_at=seen, ingested_at=ingested)
                nodes.append({'role': role, 'original_receipt_id': rid, 'audit_receipt_ref': proxy,
                    'original_receipt_literal': receipt, 'original_status': receipt['status'],
                    'original_pagination_complete': receipt['pagination_complete'],
                    'original_row_count': receipt['row_count'], 'RAW_clock_status': parent['raw_clock_status'],
                    'clock_role': parent['archive_clock_role'], 'files': parent['files'],
                    'wrapper_interval': {k: original[k] for k in ('started_at', 'completed_at')},
                    'parameters': original['parameters'], 'field_version': original['field_version']})
        graph_core = {'version': VERSION, 'source_bindings': list(self._files.values()),
                      'capture_implementation_identity': self._capture_identity,
                      'current_validator_identity': self._identity, 'original_nodes': nodes,
                      'proofs': list(proofs.values()), 'issued_at': issued.isoformat(),
                      'base_adapter_input_hash': audit['input_hash'],
                      'scope': 'CURRENT_ACTUAL_CUTOFF_INLINE_RETROSPECTIVE_ONLY'}
        core_hash = digest(graph_core)
        validation_id = 'factor_fact_validation_' + core_hash
        private_receipts[validation_id] = SourceReceipt(receipt_id=validation_id,
            provider='stockfish_local_research_validation', dataset='factor_fact_original_audit_validation',
            content_hash=core_hash, field_version=VERSION, definition_version=VERSION,
            row_count=len(proofs), pagination_complete=True, status='COMPLETE',
            source_type='current_factor_fact_audit_validation_not_batch_completeness',
            parameters={'audit_graph_core_hash': core_hash, 'research_only': True},
            instrument_ids=tuple(sorted({b.instrument_id for b in base.bars})),
            business_sessions=sessions, measurement_definition='PROTOCOL_VALIDATION_WITH_UNKNOWN_RETAINED',
            event_time=issued, source_published_at=issued, first_seen_at=issued, ingested_at=issued)
        for oldrid, receipt in original_receipts.items():
            if receipt.dataset != 'adjustment_factors':
                continue
            source = original_sources[oldrid]; origin = source['original_receipt_id']
            if len(receipt.instrument_ids) != 1:
                raise ValueError('FACTOR_FACT_SINGLE_SECURITY_NORMALIZED_FACTOR_REQUIRED')
            iid = receipt.instrument_ids[0]; code = iid.split('.')[1].lower()+'.'+iid.split('.')[0]
            proof = proofs[(origin, code)]
            if proof['status'] == 'UNKNOWN':
                mappings[oldrid] = ()
                continue
            dependencies = (proxy_refs[origin], *(proxy_refs[r] for r in proof['ancestor_receipt_ids']))
            identity = {'version': VERSION, 'proof': proof, 'audit_graph_core_hash': core_hash,
                        'base_normalized_receipt_id': oldrid, 'base_normalized_receipt': receipt.model_dump(mode='json'),
                        'audit_parent_refs': dependencies, 'issued_at': issued.isoformat()}
            fact_id = 'factor_value_fact_' + digest(identity)
            private_receipts[fact_id] = SourceReceipt.model_validate({**receipt.model_dump(mode='python'),
                'receipt_id': fact_id, 'field_version': VERSION, 'definition_version': VERSION,
                'status': 'COMPLETE', 'pagination_complete': True,
                'source_type': 'current_validated_per_security_factor_value_fact',
                'parameters': {**receipt.parameters, 'audit_graph_core_hash': core_hash,
                    'proof_hash': digest(identity), 'original_final_receipt_id': origin,
                    'original_capture_row_count': len(self._finals[origin]['rows'].get(code, ())),
                    'research_only': True},
                'measurement_definition': 'COMPLETE_CODE_CAPTURE_NOT_BATCH_UPGRADE_OR_PIT',
                'source_published_at': issued, 'first_seen_at': issued, 'ingested_at': issued,
                'historical_time_proof': None})
            mappings[oldrid] = (fact_id,)
            links[fact_id] = dependencies
            fact_sources.append({**source, 'normalized_receipt_id': fact_id,
                                 'lineage_proof': identity, 'audit_parent_refs': dependencies})
        bars, gaps = [], []
        for old in base.bars:
            refs = tuple(dict.fromkeys((*(ref for oldref in old.source_refs
                for ref in mappings.get(oldref, (oldref,))), validation_id)))
            fields = old.model_dump(mode='python'); fields['source_refs'] = refs
            if any(not mappings[ref] for ref in old.source_refs if ref in mappings):
                fields.update({key: None for key in (
                    'comparable_open', 'comparable_high', 'comparable_low', 'comparable_close', 'comparable_basis')})
                fields['quality'] = 'PARTIAL'
                gaps.append({'instrument_id': old.instrument_id, 'session': str(old.session),
                             'field': 'COMPARABLE_PRICE', 'reason': 'FACTOR_RESUME_LINEAGE_UNKNOWN'})
            fields.update(source_published_at=max(issued, old.source_published_at),
                          first_seen_at=max(issued, old.first_seen_at), ingested_at=max(issued, old.ingested_at))
            bars.append(DailyBar.model_validate(fields))
        links[validation_id] = tuple(proxy_refs.values())
        graph = {**graph_core, 'fact_to_audit_parent_refs': links,
                 'bar_to_value_refs': {digest(bar): bar.source_refs for bar in bars},
                 'value_receipts': [r.model_dump(mode='json') for r in private_receipts.values()]}
        graph_hash = digest(graph)
        manifest = {'artifact_version': OWNER_VERSION,
            'reader_contract': 'FRESH_SAME_PROCESS_INLINE_FACT_OWNER_NO_SQLITE_OR_PARTITIONS',
            'receipts': [r.model_dump(mode='json') for r in private_receipts.values()],
            'audit_graph_hash': graph_hash, 'audit_graph_core_hash': core_hash,
            'current_validator_source_hash': self._identity['source_hash'],
            'capture_implementation_identity': self._capture_identity,
            'inline_bar_scope': {'count': len(bars), 'sessions': [str(s) for s in sessions],
                'observation_date': str(base.observation_date), 'records_hash': digest([digest(b) for b in bars])},
            'history_completeness': 'INLINE_ROWS_ONLY_NOT_FULL_HISTORY',
            'partitions': [], 'no_SQLite_partitions': True, 'issued_at': issued.isoformat()}
        manifest_hash = digest(manifest)
        bundle = MarketInput.model_validate({**base.model_dump(mode='python'), 'bars': tuple(bars),
            'receipts': tuple(public_receipts.values()), 'history_artifact_ref': 'history_'+manifest_hash,
            'history_artifact_hash': manifest_hash})
        cutoff = _now()
        if cutoff < issued or cutoff >= deadline:
            raise ValueError('FACTOR_FACT_ACTUAL_CUTOFF_OUTSIDE_ISSUED_DEADLINE')
        new_audit = {**audit, 'normalizer_version': VERSION, 'base_adapter_input_hash': audit['input_hash'],
            'input_hash': digest_market_input(bundle), 'adapter_source_identity': self._identity,
            'capture_implementation_identity': self._capture_identity,
            'sources': [s for s in audit['sources'] if original_receipts[s['normalized_receipt_id']].dataset != 'adjustment_factors'] + fact_sources,
            'gaps': [*audit['gaps'], *gaps], 'factor_facts': {'issued_at': issued.isoformat(),
                'actual_cutoff': cutoff.isoformat(), 'registered_worker_deadline': deadline.isoformat(),
                'proof_counts': dict(Counter(p['status'] for p in proofs.values())),
                'original_out_of_parent_gap_count': sum(p['original_out_of_parent_gap'] is not None for p in proofs.values()),
                'original_gaps_modified': False, 'old_capability_restored': False,
                'audit_graph_hash': graph_hash, 'audit_graph_core_hash': core_hash,
                'owner_manifest': manifest, 'owner_manifest_hash': manifest_hash,
                'lineage_unknown_comparable_bars': len(gaps),
                'base_unknown_comparable_bars': sum(b.comparable_close is None for b in base.bars),
                'total_unknown_comparable_bars': sum(b.comparable_close is None for b in bars)},
            'archived_point_in_time': False, 'original_capture_clocks_modified': False,
            'history_mode': 'NEW_SAME_PROCESS_INLINE_OWNER_NOT_SQLITE_HISTORY_V1',
            'unchanged_numeric_authorities': ['RAW_PRICE', 'FROZEN_RAW_X_BACK_FACTOR', 'DATED_QUALIFICATION', 'EXACT_TURN_PERCENT']}
        authority = _Authority(_FACTORY)
        context = FactorFactContext(bundle, cutoff, issued, deadline,
            lineage._exact(new_audit), lineage._exact(graph), authority)
        reader = _InlineFactReader(_FACTORY, manifest, bars, bundle.universe.securities, authority)
        bundle._history_store = reader
        _OWNERS[authority] = {'context': weakref.ref(context), 'assembler': weakref.ref(self),
            'pid': os.getpid(), 'state': 'ISSUED', 'deadline': deadline, 'cutoff': cutoff,
            'issued_at': issued, 'bundle_hash': digest_market_input(bundle),
            'graph_digest': digest(context._graph_json), 'audit_digest': digest(context._audit_json),
            'graph_text': context._graph_json, 'audit_text': context._audit_json,
            'manifest_hash': manifest_hash, 'graph_hash': graph_hash,
            'value_receipts_hash': digest(manifest['receipts']),
            'bar_refs': graph['bar_to_value_refs'], 'fact_links': links,
            'private_receipt_ids': tuple(private_receipts), 'reader': weakref.ref(reader),
            'source_identity': self._identity, 'proofs_digest': digest(list(proofs.values()))}
        self._assert_fixed()
        return context


def _validate_context(context, *, consuming=False, fresh_sources=False):
    record = _record(context, consuming=consuming)
    assembler = record['assembler']()
    graph = context.audit_graph
    if (digest(context._graph_json) != record['graph_digest']
            or digest(context._audit_json) != record['audit_digest']
            or digest(record['fact_links']) != digest(graph['fact_to_audit_parent_refs'])
            or digest(record['bar_refs']) != digest(graph['bar_to_value_refs'])
            or digest(graph) != record['graph_hash']):
        raise ValueError('FACTOR_FACT_COMPLETE_AUDIT_GRAPH_CHANGED')
    if context.bundle.coverage_type not in ('RECONSTRUCTED_HISTORICAL', 'TEST_FIXTURE'):
        raise ValueError('FACTOR_FACT_CURRENT_RESEARCH_COVERAGE_REQUIRED')
    if context.bundle._history_store is not record['reader']():
        raise ValueError('FACTOR_FACT_READER_REPLACED')
    if (digest_market_input(context.bundle) != record['bundle_hash']
            or context.bundle.history_artifact_hash != record['manifest_hash']
            or context.bundle.history_artifact_ref != 'history_'+record['manifest_hash']
            or digest(record['reader']()._manifest) != record['manifest_hash']):
        raise ValueError('FACTOR_FACT_BUNDLE_OR_MANIFEST_CHANGED')
    if record['source_identity'] != assembler._identity or source_identity() != assembler._identity:
        raise ValueError('FACTOR_FACT_CURRENT_SOURCE_IDENTITY_CHANGED')
    if fresh_sources:
        proofs = assembler._fresh_authenticate()
        if digest(list(proofs.values())) != record['proofs_digest']:
            raise ValueError('FACTOR_FACT_FRESH_PER_SECURITY_PROOF_CHANGED')
    return record
