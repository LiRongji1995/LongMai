"""Explicit original-byte issuance for saved R2 stream delivery.

Physical receipts bind a local capture, not historical PIT or security
authentication. Complete market scope is separately checked by the unchanged
native reader/stream consumer. No normalized result wrapper is fabricated.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

from .contracts import SourceReceipt
from .history_artifacts import digest_json_stream
from .provider_normalization import _clock
from .research_context_artifacts import SourceArtifactReader
from . import saved_r2_delivery as legacy
from .visibility import stamp

SOURCES_SCHEMA = 'SAVED_R2_DELIVERY_SOURCES_V2'
VERSION = 'ORIGINAL_RAW_PHYSICAL_RECEIPT_SOURCE_ISSUANCE_V1'
_ISSUER = object()
_PHYSICAL_KEYS = ('provider', 'dataset', 'parameters', 'field_version',
    'row_count', 'pagination_complete', 'status', 'first_seen_at', 'ingested_at',
    'content_hash', 'source_url', 'integrity_scope', 'receipt_id')


def _require(condition, reason):
    if not condition:
        raise ValueError('SAVED_R2_SOURCES_V2_' + reason)


def source_identity():
    root = Path(__file__).resolve().parents[2]
    paths = [root / 'stockfish_core/file_integrity.py', Path(__file__), Path(__file__).with_name('providers.py'),
        Path(__file__).with_name('provider_normalization.py'),
        Path(__file__).with_name('saved_r2_delivery.py'),
        Path(__file__).with_name('historical_replay.py'),
        Path(__file__).with_name('research_context_artifacts.py'),
        Path(__file__).with_name('short_horizon_participation.py'),
        Path(__file__).with_name('short_horizon_strength.py'),
        Path(__file__).with_name('contracts.py'),
        Path(__file__).with_name('history_artifacts.py'),
        Path(__file__).with_name('visibility.py')]
    hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths}
    return dict(version=VERSION, schema_version=SOURCES_SCHEMA,
        source_file_hashes=hashes, source_hash=digest_json_stream(hashes))


def _authenticate_physical(registry, registration, base):
    legacy._strict(registration, ('raw', 'receipt', 'typed_receipt'), 'RAW_RECEIPT_SCHEMA_INVALID')
    files = {name: registry.registered(registration[name], base) for name in ('raw', 'receipt')}
    raw = registry.read(files['raw'].path, files['raw'].sha256)
    payload = legacy._json(raw)
    physical = legacy._json(registry.read(files['receipt'].path, files['receipt'].sha256))
    legacy._strict(physical, _PHYSICAL_KEYS, 'PHYSICAL_RECEIPT_SCHEMA_INVALID')
    receipt_body = {key: value for key, value in physical.items() if key != 'receipt_id'}
    # Exactly the existing providers.archive_receipt identity algorithm.
    rid = hashlib.sha256(json.dumps(receipt_body, sort_keys=True).encode()).hexdigest()
    _require(physical['receipt_id'] == rid, 'PHYSICAL_RECEIPT_ID_MISMATCH')
    _require(physical['integrity_scope'] == 'LOCAL_SHA256_NOT_SECURITY_AUTHENTICATION',
        'PHYSICAL_INTEGRITY_SCOPE_UNRECOGNIZED')
    _require(physical['provider'] == 'baostock' and physical['dataset'] in
        ('market_daily', 'master_all'), 'DATED_BAOSTOCK_MARKET_OR_MASTER_REQUIRED')
    parameters = physical['parameters']
    _require(isinstance(parameters, dict) and set(parameters) == {'date'}
        and isinstance(parameters['date'], str), 'DATED_REQUEST_SCOPE_REQUIRED')
    day = date.fromisoformat(parameters['date'])
    _require(str(day) == parameters['date'], 'REQUEST_DATE_NOT_CANONICAL')
    _require(physical['field_version'] == 'BAOSTOCK_00.9.30_R03_V1', 'FIELD_VERSION_UNRECOGNIZED')
    _require(isinstance(payload, dict) and isinstance(payload.get('rows'), list), 'WHOLE_RAW_ROWS_REQUIRED')
    _require(physical['content_hash'] == files['raw'].sha256 == hashlib.sha256(raw).hexdigest(),
        'WHOLE_RAW_CONTENT_HASH_MISMATCH')
    _require(type(physical['pagination_complete']) is bool
        and type(payload.get('pagination_complete')) is bool
        and payload['pagination_complete'] == physical['pagination_complete']
        and payload.get('status') == physical['status']
        and payload.get('field_version') == physical['field_version'], 'RAW_PHYSICAL_ENVELOPE_MISMATCH')
    row_count = len(payload['rows'])
    _require(physical['row_count'] is None or (type(physical['row_count']) is int
        and physical['row_count'] == row_count), 'PHYSICAL_ROW_COUNT_CONFLICT')
    seen, ingested = stamp(physical['first_seen_at']), stamp(physical['ingested_at'])
    _require(seen <= ingested, 'PHYSICAL_CAPTURE_CLOCK_ORDER_INVALID')
    _require(stamp(payload.get('first_seen_at')) == seen, 'RAW_FIRST_SEEN_CONFLICT')
    coverage = payload.get('coverage', {})
    _require(isinstance(coverage, dict), 'RAW_COVERAGE_SCHEMA_INVALID')
    _require(isinstance(coverage.get('returned_instrument_ids', []), (list, tuple))
        and isinstance(coverage.get('business_sessions', []), (list, tuple)), 'RAW_COVERAGE_SCOPE_INVALID')
    expected = SourceReceipt(receipt_id=rid, provider=physical['provider'], dataset=physical['dataset'],
        content_hash=physical['content_hash'], field_version=physical['field_version'],
        row_count=row_count, pagination_complete=physical['pagination_complete'],
        status=physical['status'], parameters=parameters, source_url=physical['source_url'],
        instrument_ids=tuple(sorted(str(value) for value in coverage.get('returned_instrument_ids', ()) if value)),
        business_sessions=tuple(sorted({date.fromisoformat(str(value))
            for value in coverage.get('business_sessions', ())})), **_clock(physical, day))
    legacy._strict(registration['typed_receipt'], SourceReceipt.model_fields, 'FULL_TYPED_RECEIPT_DECLARATION_REQUIRED')
    declared = SourceReceipt.model_validate(registration['typed_receipt'])
    _require(declared == expected, 'TYPED_RECEIPT_NOT_ORIGINAL_RAW_AND_PHYSICAL_RECEIPT')
    return expected, files['raw'].path, dict(receipt_id=rid, dataset=expected.dataset,
        status=expected.status, authentication_mode='WHOLE_RAW_AND_PHYSICAL_RECEIPT',
        original_physical_row_count=physical['row_count'], actual_whole_raw_row_count=row_count,
        typed_declared_scope_not_expanded=True,
        business_scope_status='PENDING_GENUINE_NATIVE_MARKET_STREAM_VALIDATION',
        source_capture_completed_at=None, source_capture_completion_status='UNKNOWN_NOT_IN_PHYSICAL_RECEIPT',
        completed_at=None, issued_at=datetime.now(timezone.utc).isoformat(),
        original_four_clocks={key: getattr(expected, key).isoformat() for key in
            ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')},
        publication_clock_role='ORIGINAL_CAPTURE_VISIBILITY_ALIAS_NOT_INDEPENDENT_PUBLICATION',
        local_hash_is_not_security_authentication=True)


class AuthenticatedSourceArtifactsV2:
    """This invocation's issuer, reader, and terminal original-byte registry."""
    def __init__(self, token, *, registry, reader, receipts, audits, businesses,
            nodes, extra, reported, cutoff, identity):
        _require(token is _ISSUER, 'GENUINE_ISSUER_REQUIRED')
        self._issuer = token
        self._registry, self._reader, self._cutoff = registry, reader, cutoff
        self._receipts, self._identity = receipts, identity
        self._receipt_hash = digest_json_stream([row.model_dump(mode='json') for row in receipts])
        self._registry_hash = digest_json_stream(registry.files)
        self._paths = {key: str(value.resolve(strict=True)) for key, value in reader._paths.items()}
        self._audits = deepcopy(audits)
        self.businesses, self.nodes, self.extra, self.reported = businesses, nodes, extra, reported
        self._registration_snapshot = deepcopy((businesses, nodes, extra, reported))

    def __deepcopy__(self, memo):
        raise ValueError('SAVED_R2_SOURCES_V2_LIVE_CAPABILITY_NOT_COPYABLE')

    @property
    def reader(self):
        self.validate_binding()
        return self._reader

    @property
    def receipts(self):
        return self._receipts

    @property
    def audits(self):
        return deepcopy(self._audits)

    @property
    def registry(self):
        return self._registry

    def validate_binding(self):
        _require(type(self) is AuthenticatedSourceArtifactsV2 and self._issuer is _ISSUER,
            'GENUINE_ISSUER_REQUIRED')
        _require(source_identity() == self._identity, 'SOURCE_IMPLEMENTATION_CHANGED')
        _require(type(self._reader) is SourceArtifactReader
            and self._reader.receipts == self._receipts
            and self._reader._receipts == {row.receipt_id: row for row in self._receipts}
            and digest_json_stream([row.model_dump(mode='json') for row in self._reader.receipts]) == self._receipt_hash,
            'LIVE_READER_BINDING_CHANGED')
        _require(digest_json_stream(self._registry.files) == self._registry_hash
            and {key: str(value.resolve(strict=True)) for key, value in self._reader._paths.items()} == self._paths,
            'LIVE_REGISTRY_OR_PATH_BINDING_CHANGED')
        _require((self.businesses, self.nodes, self.extra, self.reported) == self._registration_snapshot,
            'ORIGINAL_EXPLANATION_REGISTRATIONS_CHANGED')
        self._registry.terminal()
        return True


def issue_saved_r2_source_reader(*, sources_manifest_path, cutoff):
    """Authenticate originals and freshly issue a reader; never restore JSON."""
    decision = stamp(cutoff)
    _require(decision <= datetime.now(timezone.utc), 'FUTURE_CONTEXT_CUTOFF')
    path = Path(sources_manifest_path).resolve(strict=True)
    registry, identity = legacy._Registry(), source_identity()
    document = legacy._json(registry.read(path))
    legacy._strict(document, ('schema_version', 'captures', 'raw_receipt_sources',
        'business_sources', 'counted_nodes', 'reported_scope', 'extra_registered_files'), 'SOURCES_V2_SCHEMA_INVALID')
    _require(document['schema_version'] == SOURCES_SCHEMA, 'SOURCE_MANIFEST_VERSION_INVALID')
    _require(isinstance(document['raw_receipt_sources'], list)
        and isinstance(document['captures'], list)
        and len(document['captures']) + len(document['raw_receipt_sources']) <= legacy.MAX_CAPTURES,
        'SOURCE_COUNT_BUDGET_EXCEEDED')
    # Preserve the exact existing trio verifier and business/node registrations.
    old_document = {key: value for key, value in document.items() if key != 'raw_receipt_sources'}
    old_document['schema_version'] = legacy.SOURCES_SCHEMA
    receipts, paths, audits, businesses, nodes, extra, reported = legacy._source_inputs(registry, old_document, path.parent)
    by_id = {row.receipt_id: row for row in receipts}
    for registration in document['raw_receipt_sources']:
        receipt, raw_path, audit = _authenticate_physical(registry, registration, path.parent)
        _require(receipt.receipt_id not in by_id, 'DUPLICATE_SOURCE_RECEIPT')
        by_id[receipt.receipt_id], paths[receipt.receipt_id] = receipt, raw_path
        audits.append(audit)
    complete = {row['receipt_id']: row.get('completed_at') for row in audits}
    visible = tuple(by_id[key] for key in sorted(by_id) if all(getattr(by_id[key], name) <= decision
        for name in ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at'))
        and (complete[key] is None or stamp(complete[key]) <= decision))
    for audit in audits:
        audit['visible_at_context_cutoff'] = audit['receipt_id'] in {row.receipt_id for row in visible}
    reader = SourceArtifactReader(visible, {row.receipt_id: paths[row.receipt_id] for row in visible})
    registry.terminal()
    return AuthenticatedSourceArtifactsV2(_ISSUER, registry=registry, reader=reader,
        receipts=visible, audits=audits, businesses=businesses, nodes=nodes, extra=extra,
        reported=reported, cutoff=decision, identity=identity)
