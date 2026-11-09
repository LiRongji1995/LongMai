"""R03 authoritative evidence resolution shared by every data consumer.

Late capture is distinct from future publication. Historical-source proof is a
reviewable local statement with source/hash/scope, not cryptographic identity.
No business date alone grants permission to bypass actual receipt clocks.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo
from .contracts import Clocked, SourceReceipt, digest

POLICY_ID = 'R03_EVIDENCE_VISIBILITY_V1'
SH = ZoneInfo('Asia/Shanghai')
CLOCKS = ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')
_CLOCK_FIELDS = frozenset(CLOCKS)


def mapping(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else dict(value)


def stamp(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('EXPLICIT_TIMEZONE_REQUIRED')
    return result


def day(value):
    return value if isinstance(value, date) and not isinstance(value, datetime) else date.fromisoformat(str(value))


def clock_visible(obj, cutoff, *, allow_late_capture=False):
    """Clock predicate only; it cannot authorize an unresolved source."""
    try:
        # Typed evidence can contain a whole universe or a large receipt scope.
        # Preserve Pydantic's JSON clock semantics without serializing unrelated
        # business fields on every clock predicate. Nothing is cached across calls.
        row = (obj.model_dump(mode='json', include=_CLOCK_FIELDS)
               if isinstance(obj, Clocked) else mapping(obj))
        clocks = tuple(stamp(row[k]) for k in CLOCKS)
        cutoff = stamp(cutoff)
        if clocks[2] > clocks[3]:
            return False
        return max(clocks[:2] if allow_late_capture else clocks) <= cutoff
    except (KeyError, ValueError, TypeError):
        return False


@dataclass(frozen=True)
class Resolution:
    allowed: bool
    reason_codes: tuple[str, ...]
    source_ids: tuple[str, ...] = ()
    used_sources: tuple[dict, ...] = ()
    policy_id: str = POLICY_ID
    reconstruction_used: bool = False


class EvidenceResolver:
    def __init__(self, receipts, *, cutoff, coverage_type, sessions=(), observation_date=None, record_rows=True):
        self.cutoff = stamp(cutoff)
        self.coverage_type = coverage_type
        self.sessions = tuple(day(x) for x in sessions)
        self.observation_date = day(observation_date) if observation_date is not None else None
        self.record_rows = record_rows
        self.receipts = {}
        self.conflicts = set()
        self.used = {}
        self.rows = {}
        self._receipt_resolution_cache = {}
        for value in (receipts.values() if isinstance(receipts, dict) else receipts or ()):
            parsed = value if isinstance(value, SourceReceipt) else SourceReceipt.model_validate(value)
            if parsed.receipt_id in self.receipts and self.receipts[parsed.receipt_id] != parsed:
                self.conflicts.add(parsed.receipt_id)
            self.receipts[parsed.receipt_id] = parsed

    def _clock(self, row, proofs=(), business_day=None,prior_source_captured=False):
        if not clock_visible(row, self.cutoff, allow_late_capture=True):
            return 'EVENT_OR_PUBLICATION_NOT_VISIBLE', False
        if clock_visible(row, self.cutoff):
            return None, False
        if self.coverage_type != 'RECONSTRUCTED_HISTORICAL':
            return 'CAPTURE_AFTER_CUTOFF', False
        if prior_source_captured:
            return None, True
        if not proofs or any(proof is None for proof in proofs):
            return 'HISTORICAL_SOURCE_TIME_PROOF_REQUIRED', False
        event_day = business_day or stamp(mapping(row)['event_time']).astimezone(SH).date()
        if any(not p.applies_from <= event_day <= p.applies_to for p in proofs):
            return 'HISTORICAL_TIME_PROOF_OUTSIDE_SCOPE', False
        return None, True

    def receipt_resolution(self, receipt, *, require_complete=False, business_day=None):
        r = receipt if isinstance(receipt, SourceReceipt) else self.receipts.get(receipt)
        if r is None:
            return Resolution(False, ('SOURCE_REFERENCE_UNRESOLVED',))
        cache_key = (r.receipt_id, require_complete, str(business_day) if business_day is not None else None)
        cached = self._receipt_resolution_cache.get(cache_key)
        if cached is not None:
            return cached
        if r.receipt_id in self.conflicts:
            return Resolution(False, ('SOURCE_REFERENCE_CONFLICT',))
        if r.status not in ('COMPLETE', 'PARTIAL'):
            return Resolution(False, ('SOURCE_STATUS_'+r.status,))
        if require_complete and (r.status != 'COMPLETE' or not r.pagination_complete):
            return Resolution(False, ('SOURCE_NOT_COMPLETE',))
        reason, late = self._clock(r, (r.historical_time_proof,), business_day)
        result = Resolution(not reason, (reason,) if reason else (), (r.receipt_id,),
                            (r.model_dump(mode='json'),) if not reason else (), reconstruction_used=late)
        self._receipt_resolution_cache[cache_key] = result
        return result

    def resolve(self, obj, *, instrument_id=None, session=None, source_types=None, definition_version=None,
                require_complete=False, require_session=False, source_refs=None, record_hash=None):
        def reject(code):
            return Resolution(False, (code,))
        try:
            if self.coverage_type not in ('PROSPECTIVE_CAPTURED','RECONSTRUCTED_HISTORICAL','TEST_FIXTURE'):
                return reject('UNVERIFIABLE_AS_OF')
            row = mapping(obj)
            # Reject unavailable outer clocks before evaluating business fields.
            if not clock_visible(row, self.cutoff, allow_late_capture=True):
                return reject('EVENT_OR_PUBLICATION_NOT_VISIBLE')
            if instrument_id is not None and row.get('instrument_id') != instrument_id:
                return reject('INSTRUMENT_ID_MISMATCH')
            observed = day(session) if session is not None else day(row['session']) if row.get('session') is not None else None
            if session is not None and row.get('session') is not None and day(row['session']) != observed:
                return reject('BUSINESS_SESSION_MISMATCH')
            if observed is not None and observed>self.cutoff.astimezone(SH).date():
                return reject('FUTURE_BUSINESS_SESSION')
            if observed is not None and row.get('session') is not None and min(
                    stamp(row[k]).astimezone(SH).date() for k in ('event_time','source_published_at'))<observed:
                return reject('EVENT_OR_PUBLICATION_PRECEDES_BUSINESS_SESSION')
            if require_session:
                if observed is None or row.get('session') is None:
                    return reject('BUSINESS_SESSION_REQUIRED')
                if not self.sessions or observed not in self.sessions:
                    return reject('BUSINESS_SESSION_NOT_IN_EXCHANGE_CALENDAR')
                if observed > self.cutoff.astimezone(SH).date() or (self.observation_date and observed > self.observation_date):
                    return reject('FUTURE_BUSINESS_SESSION')
                if min(stamp(row[k]).astimezone(SH).date() for k in ('event_time', 'source_published_at')) < observed:
                    return reject('EVENT_OR_PUBLICATION_PRECEDES_BUSINESS_SESSION')
            if observed is not None and row.get('effective_from') is not None:
                if day(row['effective_from']) > observed or (row.get('effective_to') is not None and observed >= day(row['effective_to'])):
                    return reject('OUTSIDE_EFFECTIVE_INTERVAL')
            refs = tuple(source_refs if source_refs is not None else row.get('source_refs', ()))
            if not refs:
                return reject('SOURCE_REFERENCE_REQUIRED')
            parents, late = [], False
            for ref in refs:
                authority = self.receipt_resolution(ref, require_complete=require_complete, business_day=observed)
                if not authority.allowed:
                    return authority
                parent = self.receipts[ref]
                if source_types and parent.source_type not in source_types:
                    return reject('SOURCE_TYPE_MISMATCH')
                if definition_version is not None and (parent.field_version != definition_version or parent.definition_version != definition_version):
                    return reject('SOURCE_DEFINITION_VERSION_MISMATCH')
                if instrument_id is not None and parent.instrument_ids and instrument_id not in parent.instrument_ids:
                    return reject('SOURCE_INSTRUMENT_SCOPE_MISMATCH')
                if observed is not None and parent.business_sessions and observed not in parent.business_sessions:
                    return reject('SOURCE_SESSION_SCOPE_MISMATCH')
                if stamp(row['first_seen_at']) < parent.first_seen_at or stamp(row['ingested_at']) < parent.ingested_at:
                    return reject('ROW_CAPTURE_PRECEDES_PARENT_RECEIPT')
                parents.append(parent)
                late |= authority.reconstruction_used
            late_parents=tuple(r for r in parents if not clock_visible(r,self.cutoff))
            reason, row_late = self._clock(row, tuple(r.historical_time_proof for r in late_parents), observed,
                                          prior_source_captured=not late_parents)
            if reason:
                return reject(reason)
            used = []
            for parent in parents:
                source = self.used.get(parent.receipt_id)
                if source is None:
                    source = parent.model_dump(mode='json')
                    self.used[source['receipt_id']] = source
                used.append(source)
            used = tuple(used)
            if self.record_rows:
                self.rows[digest(row)] = row
            return Resolution(True, (), tuple(sorted(set(refs))), used, reconstruction_used=late or row_late)
        except (TypeError, ValueError, KeyError):
            return reject('INVALID_EVIDENCE_ENVELOPE')

    def is_visible(self, obj, **kwargs):
        return self.resolve(obj, **kwargs).allowed

    def effective_dependencies(self):
        return {'policy_id': POLICY_ID, 'sources': [self.used[k] for k in sorted(self.used)],
                'records': [self.rows[k] for k in sorted(self.rows)]}
