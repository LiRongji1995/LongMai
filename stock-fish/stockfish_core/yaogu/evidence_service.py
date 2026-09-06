"""Local-admin public seat evidence archive; never a candidate scoring authority.

Source receipts and a whitelist are enforced before projecting evidence to API
or LLM consumers. Missing windows/amounts remain missing. Alias validity is an
inclusive [valid_from, valid_to] convention, not the security master convention.
"""
from __future__ import annotations

from datetime import date, datetime
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .contracts import Clocked, CoverageType, SourceReceipt, digest
from .funds import (feature_whitelist, visible_seat_aliases, coappearance,
                    deduplicate_disclosures, _dt, _day, _visible)
from .visibility import EvidenceResolver, clock_visible

VERSION = 'PUBLIC_SEAT_EVIDENCE_V1'
RECORDS = 'yaogu_public_seat_records_v1'
RECEIPTS = 'yaogu_public_source_receipts_v1'
ALIASES = 'yaogu_public_seat_aliases_v1'
CLOCKS = ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')


class SeatAliasEvidence(Clocked):
    seat_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    alias: str = Field(min_length=1)
    valid_from: date
    valid_to: date | None = None
    sample_count: int = Field(ge=0, strict=True)
    source_refs: tuple[str, ...] = Field(min_length=1)
    evidence_url: str | None = None
    coverage_type: CoverageType
    relationship: Literal['SOURCED_ALIAS_LABEL_NOT_CONFIRMED_PERSON'] = 'SOURCED_ALIAS_LABEL_NOT_CONFIRMED_PERSON'

    @model_validator(mode='after')
    def interval(self):
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError('INVALID_ALIAS_INTERVAL')
        if any(not ref for ref in self.source_refs):
            raise ValueError('SOURCE_REFERENCE_REQUIRED')
        return self


class PublicSeatRecord(Clocked):
    instrument_id: str
    provider: str = Field(min_length=1)
    seat_id: str = Field(min_length=1)
    seat_name: str = Field(min_length=1)
    session: date | None = None
    session_basis: Literal['EXPLICIT_SOURCE_SESSION', 'RECEIPT_REQUEST_DATE', 'UNVERIFIED']
    window_start: date | None = None
    window_end: date | None = None
    buy_cny: float | None = Field(default=None, ge=0)
    sell_cny: float | None = Field(default=None, ge=0)
    reason: str = ''
    disclosure_id: str | None = None
    source_refs: tuple[str, ...] = Field(min_length=1)
    seat_identity_status: Literal['PROVIDER_ID', 'VENUE_NAME_LABEL', 'UNRESOLVED_GROUP_LABEL'] = 'VENUE_NAME_LABEL'
    window_status: Literal['VERIFIED', 'UNAVAILABLE']
    amount_status: Literal['COMPLETE', 'PARTIAL', 'UNAVAILABLE']
    coverage_type: CoverageType

    @field_validator('instrument_id')
    @classmethod
    def instrument(cls, value):
        # Canonical identity syntax only: eligibility still belongs to the PIT
        # universe. No code-prefix classification occurs in this evidence store.
        if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', value):
            raise ValueError('CANONICAL_INSTRUMENT_ID_REQUIRED')
        return value

    @model_validator(mode='after')
    def consistent(self):
        if any(not ref for ref in self.source_refs):
            raise ValueError('SOURCE_REFERENCE_REQUIRED')
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError('BOTH_WINDOW_ENDPOINTS_REQUIRED')
        if self.window_start is not None:
            if self.window_start > self.window_end or (self.session is not None and self.window_end > self.session):
                raise ValueError('INVALID_DISCLOSURE_WINDOW')
        return self


def _receipt(value):
    if isinstance(value, SourceReceipt):
        value = value.model_dump(mode='json')
    # Captures have a first-seen timestamp but may not know actual publication.
    # Using capture time as its visibility bound is conservative and documented.
    allowed = set(SourceReceipt.model_fields)
    row = {k: v for k, v in value.items() if k in allowed}
    for key in ('event_time', 'source_published_at'):
        row.setdefault(key, row.get('first_seen_at'))
    row['parameters'] = {k: v for k, v in row.get('parameters', {}).items()
                         if k in ('symbol', 'date', 'start_date', 'end_date', 'flag')}
    return SourceReceipt.model_validate(row)


class EvidenceService:
    def __init__(self, store, *, allow_test_fixture=False):
        self.store = store
        self.allow_test_fixture = allow_test_fixture

    def _coverage(self, value):
        if value not in ('PROSPECTIVE_CAPTURED', 'RECONSTRUCTED_HISTORICAL', 'UNVERIFIABLE_AS_OF', 'TEST_FIXTURE'):
            raise ValueError('UNKNOWN_COVERAGE_TYPE')
        if value == 'TEST_FIXTURE' and not self.allow_test_fixture:
            raise ValueError('TEST_FIXTURE_REQUIRES_ISOLATED_SERVICE')

    def _all(self, namespace):
        offset = 0
        while True:
            page = self.store.list_documents(namespace, limit=100, offset=offset)
            for item in page:
                yield item['payload']
            if len(page) < 100:
                break
            offset += len(page)

    def register_public_records(self, records, receipts=(), *, coverage_type):
        """Local administrative import only; do not expose this through HTTP."""
        self._coverage(coverage_type)
        parsed_receipts = tuple(_receipt(x) for x in receipts)
        sources = {x.receipt_id: x for x in parsed_receipts}
        if len(sources) != len(parsed_receipts):
            raise ValueError('DUPLICATE_RECEIPT_ID')
        prepared = []
        for raw in records:
            # Whitelist before constructing any business object. Outcome fields
            # are not persisted here, not just hidden by a frontend.
            row = feature_whitelist(raw)
            row = {k: v for k, v in row.items() if k in PublicSeatRecord.model_fields}
            row['coverage_type'] = coverage_type
            default_identity = ('UNRESOLVED_GROUP_LABEL' if row.get('seat_name') in
                ('机构专用', '沪股通专用', '深股通专用') else 'VENUE_NAME_LABEL')
            row['seat_identity_status'] = raw.get('seat_identity_status', default_identity)
            refs = tuple(row.get('source_refs', ()))
            resolved = []
            for ref in refs:
                source = sources.get(ref)
                if source is None:
                    payload = self.store.get_document(RECEIPTS, ref)
                    source = SourceReceipt.model_validate(payload['receipt']) if payload else None
                if source is not None:
                    resolved.append(source)
            request_days = set()
            for source in resolved:
                symbol = source.parameters.get('symbol')
                if symbol is not None and str(symbol) not in (row.get('instrument_id'), str(row.get('instrument_id', '')).split('.')[0]):
                    raise ValueError('RECEIPT_INSTRUMENT_MISMATCH')
                requested = source.parameters.get('date')
                if requested is not None:
                    text = str(requested)
                    request_days.add(date.fromisoformat(text if '-' in text else f'{text[:4]}-{text[4:6]}-{text[6:]}'))
            if len(request_days) > 1:
                raise ValueError('RECEIPT_SESSION_CONFLICT')
            if row.get('session') is not None:
                if request_days and _day(row['session']) not in request_days:
                    raise ValueError('RECEIPT_SESSION_MISMATCH')
                row['session_basis'] = 'EXPLICIT_SOURCE_SESSION'
            elif request_days:
                row['session'] = next(iter(request_days))
                row['session_basis'] = 'RECEIPT_REQUEST_DATE'
            else:
                row['session_basis'] = 'UNVERIFIED'
            known_window = row.get('window_start') is not None and row.get('window_end') is not None
            # Merely supplying endpoints does not grant verified status.
            row['window_status'] = 'VERIFIED' if known_window and raw.get('window_status') == 'VERIFIED' else 'UNAVAILABLE'
            count = sum(row.get(k) is not None for k in ('buy_cny', 'sell_cny'))
            row['amount_status'] = ('UNAVAILABLE', 'PARTIAL', 'COMPLETE')[count]
            typed = PublicSeatRecord.model_validate(row)
            payload = typed.model_dump(mode='json')
            prepared.append(dict(record_id=digest(payload), record=payload,
                                 **{k: payload[k] for k in CLOCKS},
                                 instrument_id=typed.instrument_id, seat_id=typed.seat_id,
                                 session=payload['session'], source_refs=payload['source_refs'],coverage_type=coverage_type))
        # Importing a batch and its receipts is one SQLite transaction. Repeated
        # byte-identical imports are idempotent; contradictory receipts fail.
        with self.store.transaction() as connection:
            for source in parsed_receipts:
                prior=self.store.get_document(RECEIPTS,source.receipt_id)
                value=prior if prior and SourceReceipt.model_validate(prior['receipt'])==source else {
                    'receipt':source.model_dump(mode='json'),'clock_policy':'ALL_FOUR_CLOCKS; MISSING_PUBLICATION_USES_CAPTURE_BOUND'}
                self.store._put_document(connection, RECEIPTS, source.receipt_id,value)
            for payload in prepared:
                prior=self.store.get_document(RECORDS,payload['record_id'])
                self.store._put_document(connection, RECORDS, payload['record_id'],
                    prior if prior and prior['record']==payload['record'] else payload)
        return {'version': VERSION, 'record_ids': sorted({x['record_id'] for x in prepared}),
                'receipt_ids': sorted(sources), 'coverage_type': coverage_type,
                'scoring_authority': False}

    def register_aliases(self, aliases):
        prepared = []
        for alias in aliases:
            if not isinstance(alias, SeatAliasEvidence):
                raise TypeError('TYPED_SEAT_ALIAS_EVIDENCE_REQUIRED')
            self._coverage(alias.coverage_type)
            value = alias.model_dump(mode='json')
            prepared.append(dict(alias_id=digest(value), alias=value,
                                 seat_id=alias.seat_id, source_refs=value['source_refs'],
                                 coverage_type=alias.coverage_type,**{k: value[k] for k in CLOCKS}))
        with self.store.transaction() as connection:
            for payload in prepared:
                prior=self.store.get_document(ALIASES,payload['alias_id'])
                self.store._put_document(connection, ALIASES, payload['alias_id'],
                    prior if prior and prior['alias']==payload['alias'] else payload)
        return {'alias_ids': sorted({x['alias_id'] for x in prepared}),
                'valid_to_convention': 'INCLUSIVE', 'score_bonus': 0}

    def _resolver(self,cutoff,coverage_type):
        return EvidenceResolver([x['receipt'] for x in self._all(RECEIPTS)],cutoff=cutoff,coverage_type=coverage_type)

    def _source_state(self, refs, cutoff,resolver=None):
        if not refs:
            return 'SOURCE_RECEIPT_UNAVAILABLE'
        partial = False
        for ref in refs:
            document = self.store.get_document(RECEIPTS, ref)
            if document is None:
                return 'SOURCE_RECEIPT_UNAVAILABLE'
            source = SourceReceipt.model_validate(document['receipt'])
            authority=(resolver or self._resolver(cutoff,'PROSPECTIVE_CAPTURED')).receipt_resolution(source)
            if not authority.allowed and authority.reason_codes[0] not in ('SOURCE_STATUS_FETCH_FAILED','SOURCE_STATUS_NOT_PUBLISHED','SOURCE_STATUS_INVALID'):
                return 'NOT_VISIBLE'
            if source.status in ('FETCH_FAILED', 'NOT_PUBLISHED', 'INVALID'):
                return 'SOURCE_' + source.status
            partial |= source.status != 'COMPLETE' or not source.pagination_complete
        return 'SOURCE_PARTIAL' if partial else 'SOURCE_COMPLETE'

    def _records(self, *, cutoff, session, instrument_ids=None, seat_id=None):
        cutoff, session = _dt(cutoff), _day(session)
        selected, undated = [], []
        for envelope in self._all(RECORDS):
            # Do not inspect the nested business object until outer identity,
            # time, session, and its authoritative source visibility pass.
            coverage_type=envelope.get('coverage_type','PROSPECTIVE_CAPTURED')
            if not clock_visible(envelope, cutoff,allow_late_capture=coverage_type=='RECONSTRUCTED_HISTORICAL'):
                continue
            if instrument_ids is not None and envelope['instrument_id'] not in instrument_ids:
                continue
            if seat_id is not None and envelope['seat_id'] != seat_id:
                continue
            if envelope['session'] is not None and _day(envelope['session']) != session:
                continue
            resolver=self._resolver(cutoff,coverage_type)
            source_status = self._source_state(envelope['source_refs'], cutoff,resolver)
            if source_status == 'NOT_VISIBLE':
                continue
            typed = PublicSeatRecord.model_validate(envelope['record'])
            if typed.instrument_id != envelope['instrument_id'] or typed.seat_id != envelope['seat_id']:
                raise ValueError('PUBLIC_EVIDENCE_ENVELOPE_IDENTITY_MISMATCH')
            value = typed.model_dump(mode='json')
            if source_status in ('SOURCE_COMPLETE','SOURCE_PARTIAL') and not resolver.is_visible(
                    value,instrument_id=typed.instrument_id,session=typed.session):
                continue
            value.update(record_id=envelope['record_id'], source_status=source_status,
                         score_ready=False, score_status='EVIDENCE_ONLY_REQUIRES_FAMILY_FUNDS_INPUTS')
            if source_status not in ('SOURCE_COMPLETE', 'SOURCE_PARTIAL'):
                # No source confirmation: expose the absence, not claimed facts.
                value = {k: value[k] for k in ('record_id', 'instrument_id', 'seat_id', 'session',
                         'source_refs', 'source_status', 'score_ready', 'score_status', 'coverage_type')}
            (undated if envelope['session'] is None else selected).append(value)
        order = lambda x: (x['instrument_id'], x['seat_id'], x['record_id'])
        return sorted(selected, key=order), sorted(undated, key=order)

    def _aliases(self, seat_ids, cutoff, session):
        result = []
        for envelope in self._all(ALIASES):
            coverage_type=envelope.get('coverage_type','PROSPECTIVE_CAPTURED')
            if not clock_visible(envelope,cutoff,allow_late_capture=coverage_type=='RECONSTRUCTED_HISTORICAL') or envelope['seat_id'] not in seat_ids:
                continue
            resolver=self._resolver(cutoff,coverage_type)
            if self._source_state(envelope['source_refs'], cutoff,resolver) not in ('SOURCE_COMPLETE', 'SOURCE_PARTIAL'):
                continue
            typed = SeatAliasEvidence.model_validate(envelope['alias'])
            if typed.seat_id != envelope['seat_id']:
                raise ValueError('ALIAS_ENVELOPE_IDENTITY_MISMATCH')
            matches = visible_seat_aliases([typed.model_dump(mode='json')], cutoff, session,resolver=resolver)
            if matches:
                result.append(matches[0] | {'alias_id': envelope['alias_id'],
                    'evidence_url': typed.evidence_url, 'relationship': typed.relationship,
                    'coverage_type': typed.coverage_type, 'valid_to_convention': 'INCLUSIVE', 'score_bonus': 0})
        return sorted(result, key=lambda x: (x['seat_id'], x['alias_id']))

    def _response(self, records, undated, cutoff, session, *, scope):
        trusted = [x for x in records if x.get('source_status') in ('SOURCE_COMPLETE', 'SOURCE_PARTIAL')]
        dedup, conflicts = [], []
        # Reuse the fixed funds engine for non-overlapping disclosure windows.
        # Partial one-sided values are facts but are never subtracted from zero.
        for iid in sorted({x['instrument_id'] for x in trusted}):
            for provider in sorted({x['provider'] for x in trusted if x['instrument_id'] == iid}):
                rows = [x for x in trusted if x['instrument_id'] == iid and x['provider'] == provider
                        and x['window_status'] == 'VERIFIED' and x['amount_status'] == 'COMPLETE'
                        and x['seat_identity_status'] != 'UNRESOLVED_GROUP_LABEL']
                try:
                    selected = deduplicate_disclosures(rows, cutoff, instrument_id=iid,
                        resolver=self._resolver(cutoff,'RECONSTRUCTED_HISTORICAL'))
                except ValueError:
                    conflicts.append({'instrument_id': iid, 'provider': provider, 'reason': 'CONFLICTING_DISCLOSURE_FACTS'})
                    continue
                for row in selected:
                    copies = [x for x in rows if all(x[k] == row[k] for k in ('seat_id', 'window_start', 'window_end'))]
                    row['source_refs'] = sorted({ref for x in copies for ref in x['source_refs']})
                    row['disclosed_net_cny'] = row['buy_cny'] - row['sell_cny']
                    row['direction'] = 'POSITIVE' if row['disclosed_net_cny'] > 0 else 'NEGATIVE' if row['disclosed_net_cny'] < 0 else 'ZERO_DISCLOSED_NET'
                    dedup.append(row)
        aliases = self._aliases({x['seat_id'] for x in records + undated}, cutoff, session)
        result = dict(version=VERSION, scope=scope, cutoff=_dt(cutoff).isoformat(), session=_day(session).isoformat(),
            status='AVAILABLE_EVIDENCE' if trusted else 'UNAVAILABLE', records=records,
            undated_records=undated, nonoverlapping_complete_disclosures=dedup, conflicts=conflicts,
            aliases=aliases, source_refs=sorted({ref for x in records + undated + aliases for ref in x['source_refs']}),
            score_status='UNAVAILABLE', F=None, S_funds=None,
            score_reason='SEAT_FACTS_ALONE_DO_NOT_ESTABLISH_COMPLETE_COMPARABLE_FAMILY_FUNDS_PROFILE',
            identity_note='PUBLIC_SEAT_OR_SOURCED_ALIAS_NOT_CONFIRMED_NATURAL_PERSON; COAPPEARANCE_NOT_COMMON_CONTROL',
            llm_context=[feature_whitelist(x) for x in trusted])
        result['semantic_hash'] = digest(result)
        return result

    def instrument(self, iid, cutoff, session):
        rows, undated = self._records(cutoff=cutoff, session=session, instrument_ids={iid})
        return self._response(rows, undated, cutoff, session, scope={'instrument_id': iid})

    def seat(self, seatid, cutoff, session):
        rows, undated = self._records(cutoff=cutoff, session=session, seat_id=seatid)
        result = self._response(rows, undated, cutoff, session, scope={'seat_id': seatid})
        # An evidence-backed alias may be queryable even on a day on which this
        # seat has no disclosure. No undisclosed trading activity is inferred.
        result['aliases'] = self._aliases({seatid}, cutoff, session)
        result['source_refs'] = sorted(set(result['source_refs']) | {ref for x in result['aliases'] for ref in x['source_refs']})
        result.pop('semantic_hash')
        result['semantic_hash'] = digest(result)
        return result

    def theme(self, theme_id, members, cutoff, session):
        # Caller supplies its already PIT-validated and frozen theme membership.
        members = tuple(sorted(set(members)))
        rows, undated = self._records(cutoff=cutoff, session=session, instrument_ids=set(members))
        result = self._response(rows, undated, cutoff, session,
            scope={'theme_id': theme_id, 'member_ids': list(members), 'member_snapshot_hash': digest(members),
                   'membership_authority': 'CALLER_FROZEN_PIT_THEME'})
        peers = []
        known = [x for x in rows if x.get('source_status') in ('SOURCE_COMPLETE', 'SOURCE_PARTIAL')
                 and x.get('window_status') == 'VERIFIED' and x.get('seat_identity_status') != 'UNRESOLVED_GROUP_LABEL']
        for provider in sorted({x['provider'] for x in known}):
            source_rows = [x for x in known if x['provider'] == provider]
            for group in coappearance(source_rows, {theme_id: members}, cutoff,
                                       resolver=self._resolver(cutoff,'RECONSTRUCTED_HISTORICAL')):
                group['provider'] = provider
                group['source_refs'] = sorted({ref for x in source_rows if x['seat_id'] == group['seat_id']
                    and x['window_start'] == group['window_start'] and x['window_end'] == group['window_end'] for ref in x['source_refs']})
                peers.append(group)
        result['coappearance'] = peers
        result.pop('semantic_hash')
        result['semantic_hash'] = digest(result)
        return result
