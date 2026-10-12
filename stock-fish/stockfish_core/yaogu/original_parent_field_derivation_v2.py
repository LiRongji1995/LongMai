"""Fresh field provenance over authenticated, earlier-visible original trios.

The frozen normalizer, resolver and R1/R2 formulas are unchanged. Archived
documents never restore this invocation's single-use input capability.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from fractions import Fraction
import hashlib
from itertools import islice
import os
from pathlib import Path
import weakref

from . import original_parent_late_derivation as original
from . import physical_historical_research as physical
from . import strategy_experiments as se
from .contracts import DailyBar
from .history_artifacts import digest_json_stream, digest_market_input
from .historical_replay_turnover import _turn, TURNOVER_BASIS
from .provider_comparability import comparable_rows, factor_series
from .provider_normalization import _iid
from .research_context_artifacts import SourceArtifactReader
from .saved_r2_delivery import _Registry, _json
from .short_horizon_profiles import short_horizon_module
from .visibility import clock_visible, stamp

VERSION = 'ORIGINAL_PARENT_FIELD_DERIVATION_V2'
FEATURE_VERSION = 'FACTOR_AUDIT_SEPARATED_FIELDS_V1'
SCHEMA = 'ORIGINAL_PARENT_FIELD_R2_RESEARCH_ARCHIVE_V2'
RAW_DATASETS = frozenset(('market_daily', 'history'))
RAW_FIELDS = dict(open='open', high='high', low='low', close='close',
    reference_prev_close='preclose', volume_shares='volume', amount_cny='amount')
COMPARABLE_FIELDS = ('comparable_open', 'comparable_high', 'comparable_low',
    'comparable_close', 'comparable_basis')
_NORMALIZER = original.normalize_original_parent_asof
_NATIVE = se.run_short_horizon_strategy
_TOKEN = object()
_HELD = {}
_JOBS = set()
_FACTORIES = set()


def _require(value, reason):
    if not value:
        raise ValueError('ORIGINAL_PARENT_FIELD_' + reason)


def source_identity():
    root = Path(__file__).resolve().parents[2]
    paths = {Path(__file__), Path(__file__).with_name('original_parent_late_derivation.py'),
        root / 'docs/ORIGINAL_PARENT_FIELD_DERIVATION_V2.md'}
    original_identity = original.source_identity()
    native_identity = short_horizon_module('SHORT_HORIZON_STRENGTH_R2').short_horizon_source_identity()
    inherited = physical.source_identity()['source_file_hashes']
    paths.update(root / name for name in inherited)
    paths.update(root / name for name in original_identity['source_file_hashes'])
    paths.update(root / name for name in native_identity['source_file_hashes'])
    paths.add(Path(__file__).with_name('research_context_artifacts.py'))
    files = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(paths)}
    body = dict(source_version=VERSION, feature_version=FEATURE_VERSION,
        source_file_hashes=files, original_producer_identity=original_identity,
        native_strategy_identity=native_identity, parameter_status='UNVALIDATED',
        MARKET_EDGE='NOT_ESTABLISHED', default_integration=False)
    return dict(body, source_hash=digest_json_stream(body))


def _exact(value):
    if value in (None, ''):
        return None
    text = str(value)
    _require(len(text) <= 128, 'RAW_NUMERIC_REPRESENTATION_UNSUPPORTED')
    number = Decimal(text)
    _require(number.is_finite() and abs(number.as_tuple().exponent) <= 128,
        'RAW_NUMERIC_REPRESENTATION_UNSUPPORTED')
    return Fraction(number)


def _field(value, refs, reason=None, **extra):
    return dict(status='UNKNOWN' if reason or value is None else 'AVAILABLE',
        value_exact=({'numerator': value.numerator, 'denominator': value.denominator}
                     if isinstance(value, Fraction) else value),
        numerical_source_refs=list(refs), reason_codes=[reason] if reason else [], **extra)


def _project(baseline, reader, audits, original_checks, deadline):
    """Recompute authority; the old row's reference union is never a whitelist."""
    _require(not baseline.history_artifact_ref and not baseline.history_artifact_hash,
        'INLINE_ORIGINALS_REQUIRED_HISTORY_OWNER_NOT_ISSUED')
    receipts = {row.receipt_id: row for row in baseline.receipts}
    checks = {check['receipt_id']: check for check in original_checks}
    negative_refs = set()
    for parent in baseline.receipts:
        if parent.status != 'FETCH_FAILED':
            continue
        deadline.check()
        body = original._json(reader(parent.receipt_id))
        check = checks[parent.receipt_id]
        # The original producer has already authenticated error identity and
        # the exact negative-envelope metadata default. No value is issued.
        _require(check['original_status'] == 'FETCH_FAILED' and parent.row_count == 0
            and body.get('status') == 'FETCH_FAILED' and body.get('rows') == []
            and body.get('pagination_complete') is False and not parent.pagination_complete
            and (body.get('field_version') == parent.field_version
                 or ('field_version' not in body and check['failed_empty_schema_label_defaulted']
                     and check['schema_default_role'] == 'NEGATIVE_ENVELOPE_METADATA_NOT_NUMERIC_AUTHORITY')),
            'FAILED_ATTEMPT_NOT_AUTHENTICATED_NEGATIVE_ENVELOPE')
        negative_refs.add(parent.receipt_id)
    # Zero-row failures stay in the sealed source audit/registry/reader. They
    # contain no factor fragment or RAW value to send to the numeric oracle.
    value_candidates = tuple(parent for parent in baseline.receipts if parent.receipt_id not in negative_refs)
    captures, inheritance = physical._factor_sources(value_candidates, reader, audits, deadline)
    payloads = {rid: physical._payload(parent, reader) for rid, parent in receipts.items()
                if parent.dataset in RAW_DATASETS and rid not in negative_refs}
    raw_index = {}
    for rid, body in payloads.items():
        parent = receipts[rid]
        _require(parent.provider == 'baostock' and parent.field_version == physical.FIELD_VERSION,
            'RAW_SOURCE_SCHEMA_UNSUPPORTED')
        for raw in body['rows']:
            key = rid, _iid(raw.get('code')), date.fromisoformat(str(raw['date']))
            _require(key not in raw_index and str(raw.get('adjustflag')) == '3', 'RAW_ROW_SCOPE_CONFLICT')
            raw_index[key] = raw
    by_iid = defaultdict(list)
    for row in baseline.bars:
        deadline.check()
        parents = [ref for ref in row.source_refs if receipts[ref].dataset in RAW_DATASETS]
        _require(len(parents) == 1, 'EXACT_RAW_PARENT_REQUIRED')
        rid = parents[0]
        raw = raw_index.get((rid, row.instrument_id, row.session))
        _require(raw is not None, 'RAW_ROW_NOT_IN_AUTHENTICATED_PARENT')
        for field, key in RAW_FIELDS.items():
            _require(_exact(getattr(row, field)) == _exact(raw.get(key)), 'RAW_FIELD_EXACT_LINK_CHANGED')
        by_iid[row.instrument_id].append((row, raw, rid))
    projected, fields = [], []
    order = {r.receipt_id: (r.first_seen_at, r.ingested_at, r.receipt_id) for r in baseline.receipts}
    for iid, originals in sorted(by_iid.items()):
        code = iid[-2:].lower() + '.' + iid[:6]
        fragments, authorities = [], []
        for capture in captures:
            if code not in capture['fragments']:
                continue
            parent = capture['parent']
            rows = [row for row in capture['fragments'][code][0]
                    if date.fromisoformat(row['dividOperateDate']) <= baseline.observation_date]
            view = (rows, parent.receipt_id, str(min(capture['end'], baseline.observation_date)),
                    str(capture['start']))
            fragments.append(view)
            if parent.status == 'COMPLETE' and parent.pagination_complete and capture['raw_first_seen_present']:
                authorities.append(view)
        fragments.sort(key=lambda item: order[item[1]])
        authorities.sort(key=lambda item: order[item[1]])
        raw_rows = [(row.session, (raw, rid)) for row, raw, rid in originals]
        oracle, oracle_status = comparable_rows(iid, raw_rows, {}, fragments)
        _, _, conflict = factor_series(fragments)
        _require(not conflict and oracle_status != 'FACTOR_REVISION_CONFLICT_REQUIRES_REVIEW',
            'ALL_ATTEMPT_FACTOR_REVISION_CONFLICT')
        # One genuinely whole COMPLETE snapshot must independently carry its
        # anchor and requested window. Several incomplete/disjoint windows
        # cannot collectively acquire a new numerical authority.
        authority = authorities[-1:] if authorities else []
        authority_refs = {item[1] for item in authority}
        converted, status = comparable_rows(iid, raw_rows, {}, authority)
        authority_series, _, _ = factor_series(authority)
        for row, raw, rid in originals:
            deadline.check()
            parent, body = receipts[rid], payloads[rid]
            raw_ready = parent.status == 'COMPLETE' and parent.pagination_complete and 'first_seen_at' in body
            comparable, numeric_refs = converted.get(row.session, ({}, (rid,)))
            if not raw_ready:
                comparable, numeric_refs = {}, (rid,)
            if comparable:
                _require(row.session in oracle and oracle[row.session][0] == comparable,
                    'COMPLETE_AUTHORITY_ORACLE_EXACT_LINK_CHANGED')
                _require(rid in numeric_refs and set(numeric_refs) - {rid} == authority_refs
                    and all(receipts[ref].dataset == 'adjustment_factors'
                        and receipts[ref].status == 'COMPLETE' and receipts[ref].pagination_complete
                        for ref in authority_refs), 'NONCOMPLETE_NUMERICAL_FACTOR_PARENT')
                factor = next((value for effective, value in reversed(authority_series)
                    if effective <= str(row.session)), None)
                _require(factor is not None and all(_exact(comparable['comparable_' + name]) ==
                    _exact(raw[name]) * Fraction(factor) for name in ('open', 'high', 'low', 'close')),
                    'COMPARABLE_DECIMAL_EXACT_ROUNDTRIP_UNPROVEN')
            numeric_refs = tuple(numeric_refs) if comparable else (rid,)
            turnover, turn_exact, turn_reason = _turn(raw.get('turn'))
            update = {field: comparable.get(field) for field in COMPARABLE_FIELDS}
            update.update(source_refs=numeric_refs, turnover_fraction=turnover if raw_ready else None,
                turnover_basis=TURNOVER_BASIS if raw_ready and turnover is not None else None,
                # This version has no field authority for a mixed limit union.
                limit_up=None, limit_down=None, limit_applicable=None, limit_verified=False, rule_id=None,
                quality=row.quality if raw_ready else 'PARTIAL')
            if not raw_ready:
                update.update({field: None for field in RAW_FIELDS})
            projected_row = DailyBar.model_validate({**row.model_dump(mode='python'), **update})
            role = {field: _field(_exact(getattr(projected_row, field)), (rid,),
                        None if raw_ready else 'RAW_SOURCE_COMPLETENESS_OR_CLOCK_UNKNOWN')
                    for field in RAW_FIELDS}
            role['turnover_fraction'] = _field(turn_exact if raw_ready and turnover is not None else None,
                (rid,), turn_reason if raw_ready else 'RAW_SOURCE_COMPLETENESS_OR_CLOCK_UNKNOWN',
                turnover_basis=projected_row.turnover_basis)
            role['comparable_price'] = _field({key: str(comparable[key]) for key in COMPARABLE_FIELDS}
                if comparable else None, numeric_refs,
                None if comparable else 'COMPARABLE_PRICE_UNKNOWN', comparator_status=status)
            role['limits'] = _field(None, (), 'FIELD_LIMIT_AUTHORITY_NOT_ISSUED')
            audit_refs = sorted({item[1] for item in fragments})
            fields.append(dict(instrument_id=iid, session=str(row.session),
                raw_row_hash=digest_json_stream(raw), original_normalized_row_hash=digest_json_stream(row),
                projected_row_hash=digest_json_stream(projected_row), field_bindings=role,
                audit_dependency_refs=audit_refs, audit_dependencies_role='HARD_REGISTRY_DEPENDENCIES_NOT_VALUE_PARENTS'))
            projected.append(projected_row)
    completed = datetime.now(timezone.utc)
    bars = tuple(row.model_copy(update={'first_seen_at': completed, 'ingested_at': completed,
        'source_published_at': max(receipts[ref].source_published_at for ref in row.source_refs)})
        for row in projected)
    # Row content hashes bind the actual completed-clock projection.
    for record, row in zip(fields, bars):
        record['projected_row_hash'] = digest_json_stream(row)
    bundle = baseline.model_copy(update={'bars': bars})
    return bundle, fields, inheritance, completed


class _FieldInput:
    def __new__(cls, *_args, **_kwargs):
        raise ValueError('ORIGINAL_PARENT_FIELD_GENUINE_FACTORY_REQUIRED')

    def __copy__(self):
        raise ValueError('ORIGINAL_PARENT_FIELD_CAPABILITY_NOT_COPYABLE')

    def __deepcopy__(self, memo):
        raise ValueError('ORIGINAL_PARENT_FIELD_CAPABILITY_NOT_COPYABLE')

    def validate(self):
        held = _HELD.get(id(self))
        _require(type(self) is _FieldInput and held is not None and held['owner']() is self
            and self.token is _TOKEN and held['pid'] == os.getpid() and held['job'] == self.job
            and held['state'] == self.state and held['cutoff'] == self.cutoff
            and held['bundle'] is self.bundle and held['reader'] is self.reader
            and held['registry'] is self.registry and held['identity_hash'] == digest_json_stream(self.identity)
            and held['input_hash'] == digest_market_input(self.bundle)
            and held['fields_hash'] == digest_json_stream(self.fields)
            and held['audit_hash'] == digest_json_stream(self.audit)
            and held['registry_hash'] == digest_json_stream(self.registry.files), 'FACTORY_SEAL_CHANGED')
        _require(source_identity() == self.identity and original.normalize_original_parent_asof is _NORMALIZER
            and se.run_short_horizon_strategy is _NATIVE, 'IMPLEMENTATION_CHANGED')
        _require(type(self.reader) is SourceArtifactReader
            and self.reader.receipts == tuple(sorted(self.bundle.receipts, key=lambda row: row.receipt_id))
            and self.reader._receipts == {r.receipt_id:r for r in self.bundle.receipts}
            and {key:str(value.resolve(strict=True)) for key,value in self.reader._paths.items()} == held['paths'],
            'READER_BINDING_CHANGED')
        self.registry.terminal()

    def consume(self):
        self.validate()
        _require(self.state == 'READY', 'FACTORY_ALREADY_CONSUMED')
        self.state = _HELD[id(self)]['state'] = 'CONSUMING'
        try:
            result = _NATIVE(self.bundle, cutoff=self.cutoff,
                input_id='original_parent_field_'+digest_market_input(self.bundle),
                strategy_id='SHORT_HORIZON_STRENGTH_R2',
                _source_payload_reader=self.reader, _source_receipts=self.bundle.receipts)
            self.validate()
        except BaseException:
            self.state = _HELD[id(self)]['state'] = 'FAILED'
            raise
        self.state = _HELD[id(self)]['state'] = 'COMPLETE'
        return result


def _factory(artifacts, observation_date, cutoff, limits, deadline, job):
    _require(job in _JOBS and job not in _FACTORIES, 'REGISTERED_FACTORY_ALREADY_ISSUED')
    _FACTORIES.add(job)
    identity, issued_at = source_identity(), datetime.now(timezone.utc)
    _require(original.normalize_original_parent_asof is _NORMALIZER and se.run_short_horizon_strategy is _NATIVE,
        'IMPLEMENTATION_CHANGED')
    baseline, audit = _NORMALIZER(artifacts, observation_date, cutoff, limits=limits)
    registry = _Registry()
    for record in audit['source_file_bindings']:
        registry.read(record['path'], record['sha256'], limits.per_file_bytes)
    checks = audit['original_captures']
    paths = {check['receipt_id']: Path(check['archive_integrity']['raw_path']) for check in checks}
    _require(set(paths) == {r.receipt_id for r in baseline.receipts}, 'COMPLETE_RECEIPT_REGISTRY_REQUIRED')
    reader = SourceArtifactReader(baseline.receipts, paths)
    clock_audits = [dict(check['capture_clock_audit'], receipt_id=check['receipt_id']) for check in checks]
    bundle, fields, inheritance, derived_at = _project(baseline, reader, clock_audits, checks, deadline)
    _require(all(clock_visible(parent, cutoff) for parent in bundle.receipts), 'PARENT_AFTER_ORIGINAL_CUTOFF')
    audit = dict(audit, field_factor_inheritance=inheritance,
        original_input_hash=digest_market_input(baseline), field_derivation_completed_at=derived_at.isoformat())
    registry.terminal()
    owner = object.__new__(_FieldInput)
    owner.token, owner.bundle, owner.reader, owner.registry = _TOKEN, bundle, reader, registry
    owner.identity, owner.fields, owner.audit = identity, fields, audit
    owner.job, owner.cutoff, owner.state = job, stamp(cutoff), 'READY'
    key = id(owner)
    _HELD[key] = dict(owner=weakref.ref(owner, lambda _: _HELD.pop(key, None)), pid=os.getpid(), job=job,
        bundle=bundle, reader=reader, registry=registry, cutoff=owner.cutoff, state='READY',
        input_hash=digest_market_input(bundle), fields_hash=digest_json_stream(fields), audit_hash=digest_json_stream(audit),
        identity_hash=digest_json_stream(identity), registry_hash=digest_json_stream(registry.files),
        paths={rid:str(path.resolve(strict=True)) for rid,path in paths.items()})
    owner.validate()
    return owner, issued_at, derived_at


def run_original_parent_field_r2(artifacts, observation_date, original_parent_as_of,
        registered_worker_deadline, *, requested_target_date=None, limits=None):
    """Fresh original trios -> sealed inline fields -> exactly one native R2.

    No supplied bundle, base JSON, reader or callback is accepted. An immutable
    history store requires a separate genuine source-owner integration; this
    version does not manufacture that missing capability.
    """
    limits = limits or original.DerivationLimits()
    _require(type(limits) is original.DerivationLimits, 'EXACT_LIMITS_TYPE_REQUIRED')
    limits.validate()
    artifacts = tuple(islice(artifacts, limits.max_captures+1))
    _require(artifacts and len(artifacts) <= limits.max_captures
        and all(type(item) is original.OriginalCaptureArtifact for item in artifacts), 'ORIGINAL_LOCATORS_REQUIRED')
    day, cutoff = date.fromisoformat(str(observation_date)), stamp(original_parent_as_of)
    target = date.fromisoformat(str(requested_target_date)) if requested_target_date is not None else None
    _require(target is None or target >= day, 'TARGET_PRECEDES_OBSERVATION')
    deadline = physical._Deadline(registered_worker_deadline)
    job = (tuple((str(Path(item.result_path).resolve(strict=True)), item.result_sha256) for item in artifacts),
        str(day), cutoff.isoformat(), deadline.end.isoformat())
    _require(job not in _JOBS, 'REGISTERED_JOB_ALREADY_ATTEMPTED')
    _JOBS.add(job)
    with deadline:
        owner, issued_at, derived_at = _factory(artifacts, day, cutoff, limits, deadline, job)
        native = owner.consume()
        deadline.check()
        _require(native['input_hash'] == digest_market_input(owner.bundle)
            and native['observation_date'] == str(day)
            and set(native['results']) == {'SHORT_HORIZON_STRENGTH_R2'}, 'NATIVE_RESULT_BINDING_CHANGED')
        owner.validate()
        terminal_at = datetime.now(timezone.utc)
        return dict(schema_version=SCHEMA, source_identity=owner.identity,
            native_strategy_identity={key:native[key] for key in ('implementation_version','feature_version',
                'source_hash','config_hash','spec_hash','spec_revision')},
            source_asof_cutoff=cutoff.isoformat(), actual_issued_at=issued_at.isoformat(),
            actual_derived_at=derived_at.isoformat(), terminal_verified_at=terminal_at.isoformat(),
            requested_target_date=str(target) if target is not None else None,
            target_calendar_status='AVAILABLE' if target in owner.bundle.sessions else 'UNKNOWN',
            target_calendar_role='ORIGINAL_INPUT_AXIS_ONLY_NOT_EXTENDED',
            normalizer_calls=1, native_R2_attempts=1, selector_rule_changed=False,
            input_hash=digest_market_input(owner.bundle), input=owner.bundle.model_dump(mode='json'),
            native_run=native, field_bindings=deepcopy(owner.fields), original_source_audit=deepcopy(owner.audit),
            source_registry=deepcopy(owner.registry.files), original_receipts_modified=False,
            resource_semantics=dict(source_byte_limit_role='UNIQUE_ORIGINAL_FILE_SIZE_NOT_CUMULATIVE_IO',
                unique_source_size_limit_bytes=limits.total_source_bytes,
                per_original_file_size_limit_bytes=limits.per_file_bytes,
                cumulative_physical_read_bytes=None, cumulative_physical_read_guard=False,
                OS_RSS_peak_bytes=None, OS_RSS_guard=False,
                registered_absolute_deadline=deadline.end.isoformat(),
                real_consumer_supervisor_required=True),
            PARTIAL_ancestors_retained=True, historical_PIT_count=0, complete_independent_cycles=0,
            MARKET_EDGE='NOT_ESTABLISHED', archive_role='ARCHIVE_ONLY_NOT_RESTORABLE_CAPABILITY')
