"""Explicit capture-day identity projection; this module grants no authority.

The caller must freshly authenticate each original archive, retain its complete
source and terminal checks, and bind this deterministic projection to a new
input hash, ID and knowledge cutoff. A MarketInput (including a minimal dated
projection) or a returned manifest is data, never a source capability.

Only board/security_type are supplemented. Prices, calendar, selection, risk
states and old inputs remain unchanged. Capture-day exchange directories are
not historical-close or historical-PIT evidence and confer no limit/event or
execution authority. The old normalizers are intentionally not called here.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .contracts import MarketInput, SecurityRecord, SourceReceipt, UniverseSnapshot, digest
from .visibility import EvidenceResolver

VERSION = 'DATED_IDENTITY_COMPOSITE_INPUT_V1'
SH = ZoneInfo('Asia/Shanghai')
_MASTER = {
    'SSE': ('mainboard_sse', 'https://query.sse.com.cn/sseQuery/commonQuery.do'),
    'SZSE': ('master_szse', 'https://www.szse.cn/api/report/ShowReport'),
}
_CLOCKS = ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')


def _require(ok, reason):
    if not ok:
        raise ValueError('DATED_IDENTITY_' + reason)


def _inline(value):
    _require(type(value) is MarketInput, 'EXACT_MARKET_INPUT_REQUIRED')
    _require(value.history_artifact_ref is None and value.history_artifact_hash is None
             and value._history_store is None, 'INLINE_INPUT_REQUIRED')
    _require(type(value.universe) is UniverseSnapshot
             and type(value.universe.securities) is tuple
             and all(type(row) is SecurityRecord for row in value.universe.securities)
             and all(type(row) is SourceReceipt for row in value.receipts), 'EXACT_ROW_TYPES_REQUIRED')


def _receipts(values):
    result = {}
    for receipt in values:
        previous = result.get(receipt.receipt_id)
        _require(previous is None or previous == receipt, 'PARENT_RECEIPT_CONFLICT')
        result[receipt.receipt_id] = receipt
    return result


def _visible_universe(bundle, resolver):
    return bundle.universe.complete is True and resolver.is_visible(bundle.universe.model_dump(mode='json', exclude={'securities'}),
                               require_complete=True)


def _dated_rows(bundle, day, checkpoint):
    result = {}
    for index, row in enumerate(bundle.universe.securities):
        if index % 64 == 0:
            checkpoint()
        if row.effective_from <= day and (row.effective_to is None or day < row.effective_to):
            previous = result.get(row.instrument_id)
            # Freshly authenticated originals may still contain conflicting
            # dated assertions. Never resolve those by silently choosing latest.
            _require(previous is None, 'DATED_STATE_CONFLICT')
            result[row.instrument_id] = row
    return result


def _identity_reason(row, receipts, resolver, day, derived_at):
    if row.exchange not in _MASTER or row.board != 'MAIN' or row.security_type != 'A_COMMON':
        return 'NOT_VERIFIED_MAIN_A_COMMON'
    _require(row.effective_from == day and row.effective_to == day + timedelta(days=1),
             'IDENTITY_INTERVAL_MUST_BE_CAPTURE_DAY_ONLY')
    _require(all(getattr(row, name) <= derived_at for name in _CLOCKS), 'PARENT_AFTER_DERIVATION')
    if row.st is None or row.suspended is None:
        return 'DATED_RISK_STATE_UNKNOWN'
    if row.listed_on > day or (row.delisted_on is not None and row.delisted_on <= day):
        return 'NOT_LISTED_ON_CAPTURE_DAY'
    if not resolver.is_visible(row, instrument_id=row.instrument_id, session=day, require_complete=True):
        return 'IDENTITY_PARENT_NOT_COMPLETE_OR_VISIBLE'
    parents = [receipts[ref] for ref in row.source_refs]
    dataset, url = _MASTER[row.exchange]
    master = [p for p in parents if p.provider == 'akshare' and p.dataset == dataset
              and p.field_version == 'AKSHARE_1.18.94_R03_V1' and p.source_url == url]
    if len(master) != 1:
        return 'CAPTURE_DAY_OFFICIAL_MASTER_REQUIRED'
    if (any(getattr(master[0], name).astimezone(SH).date() != day for name in _CLOCKS)
            or master[0].row_count <= 0):
        return 'OFFICIAL_MASTER_NOT_CAPTURED_ON_IDENTITY_DAY'
    market = [p for p in parents if p.provider == 'baostock' and p.dataset == 'market_daily'
              and p.field_version == 'BAOSTOCK_00.9.30_R03_V1'
              and p.parameters.get('date') == str(day)]
    if len(market) != 1 or market[0].row_count <= 0:
        return 'DATED_MARKET_PARENT_REQUIRED'
    if (market[0].event_time.astimezone(SH).date() != day
            or market[0].first_seen_at.astimezone(SH).date() != day
            or market[0].ingested_at.astimezone(SH).date() != day):
        return 'MARKET_NOT_CAPTURED_ON_IDENTITY_DAY'
    return None


def project_dated_identity(base, identities, *, derived_at, checkpoint=None):
    """Return a new inline input and complete field lineage, without authority.

    ``identities`` may contain minimal MarketInputs retaining only each original
    observation day's authenticated states and its complete receipt set. Their
    original full-input hashes remain the authenticating caller's responsibility.
    The operation is deterministic for the same arguments and never mutates an
    argument. A later evaluation cutoff must include ``derived_at``.
    """
    check = checkpoint or (lambda: None)
    check()
    _inline(base)
    _require(type(identities) is tuple and bool(identities), 'IDENTITY_TUPLE_REQUIRED')
    _require(type(derived_at) is datetime and derived_at.tzinfo is not None
             and derived_at.utcoffset() is not None, 'AWARE_DERIVATION_TIME_REQUIRED')
    _require(derived_at.astimezone(SH).date() >= base.observation_date, 'DERIVATION_PRECEDES_BASE_DAY')
    _require(all(getattr(base.universe, name) <= derived_at for name in _CLOCKS), 'BASE_AFTER_DERIVATION')
    base_receipts = _receipts(base.receipts)
    base_resolver = EvidenceResolver(base.receipts, cutoff=derived_at,
        coverage_type='PROSPECTIVE_CAPTURED', sessions=base.sessions, observation_date=base.observation_date,
        record_rows=False)
    _require(_visible_universe(base, base_resolver), 'BASE_UNIVERSE_NOT_COMPLETE_OR_VISIBLE')
    days = set()
    rows_to_replace = {}
    lineage = []
    unknown = Counter()
    identity_metadata = []
    needed_receipts = {}
    for identity in identities:
        _inline(identity)
        day = identity.observation_date
        _require(day not in days, 'DUPLICATE_IDENTITY_DAY')
        days.add(day)
        _require(day < base.observation_date and day in base.sessions
                 and day in identity.sessions, 'IDENTITY_DAY_OUTSIDE_BASE_HISTORY')
        _require(identity.coverage_type == base.coverage_type, 'COVERAGE_TYPE_MISMATCH')
        own_receipts = _receipts(identity.receipts)
        # Reject conflicting same-ID sources even if the conflicting row would
        # later be unavailable; a merged parent graph must be unambiguous.
        _receipts(tuple(base_receipts.values()) + tuple(own_receipts.values()))
        own_resolver = EvidenceResolver(identity.receipts, cutoff=derived_at,
            coverage_type='PROSPECTIVE_CAPTURED', sessions=identity.sessions,
            observation_date=day, record_rows=False)
        _require(_visible_universe(identity, own_resolver), 'IDENTITY_UNIVERSE_NOT_COMPLETE_OR_VISIBLE')
        dated = _dated_rows(identity, day, check)
        candidates = _dated_rows(base, day, check)
        for iid, row in sorted(dated.items()):
            check()
            reason = _identity_reason(row, own_receipts, own_resolver, day, derived_at)
            if reason:
                unknown[reason] += 1
                continue
            old = candidates.get(iid)
            if old is None:
                unknown['BASE_DATED_STATE_MISSING'] += 1
                continue
            _require(old.effective_from == day and old.effective_to == day + timedelta(days=1),
                     'BASE_INTERVAL_MUST_BE_SINGLE_DAY')
            _require(base_resolver.is_visible(old, instrument_id=iid, session=day, require_complete=True),
                     'BASE_DATED_PARENT_NOT_COMPLETE_OR_VISIBLE')
            _require(old.exchange == row.exchange and old.listed_on == row.listed_on
                     and old.delisted_on == row.delisted_on, 'LIFECYCLE_OR_EXCHANGE_CONFLICT')
            if old.st is None or old.suspended is None:
                unknown['BASE_DATED_RISK_STATE_UNKNOWN'] += 1
                continue
            _require(old.st == row.st and old.suspended == row.suspended, 'DATED_RISK_STATE_CONFLICT')
            _require(old.board in ('UNKNOWN', row.board)
                     and old.security_type in ('UNKNOWN', row.security_type), 'KNOWN_IDENTITY_CONFLICT')
            refs = tuple(sorted(set(old.source_refs + row.source_refs)))
            for ref in row.source_refs:
                previous = needed_receipts.get(ref)
                _require(previous is None or previous == own_receipts[ref], 'PARENT_RECEIPT_CONFLICT')
                needed_receipts[ref] = own_receipts[ref]
            key = (iid, day)
            rows_to_replace[key] = (old, row, refs)
            lineage.append(dict(instrument_id=iid, session=str(day), base_state_hash=digest(old),
                identity_state_hash=digest(row), parent_source_refs=list(refs),
                changed_fields={name: {'old': getattr(old, name), 'new': getattr(row, name)}
                    for name in ('board', 'security_type') if getattr(old, name) != getattr(row, name)}))
        identity_metadata.append(dict(observation_date=str(day), snapshot_id=identity.universe.snapshot_id,
                                      dated_state_count=len(dated)))
    _require(bool(rows_to_replace), 'NO_SUPPORTED_DATED_IDENTITY_ROWS')
    recipe = dict(schema_version=VERSION, derived_at=derived_at.isoformat(),
        base_observation_date=str(base.observation_date), base_snapshot_id=base.universe.snapshot_id,
        identity_projections=identity_metadata, field_lineage=lineage,
        unchanged_fields='ALL_EXCEPT_BOARD_SECURITY_TYPE_AND_NEW_DERIVATION_PROVENANCE',
        historical_pit_restored=False, historical_close_identity_claim=False,
        event_or_limit_or_execution_authority_issued=False)
    recipe_hash = digest(recipe)
    rid = 'dated_identity_' + recipe_hash
    receipt = SourceReceipt(receipt_id=rid, provider='stockfish_explicit_derivation',
        dataset='dated_security_identity_projection', content_hash=recipe_hash,
        field_version=VERSION, row_count=len(rows_to_replace), pagination_complete=True, status='COMPLETE',
        source_type='DERIVED_CAPTURE_DAY_IDENTITY', definition_version=VERSION,
        instrument_ids=tuple(sorted({iid for iid, _ in rows_to_replace})),
        business_sessions=tuple(sorted(days)), parameters={'historical_pit_restored': False},
        **{name: derived_at for name in _CLOCKS})
    merged = _receipts(tuple(base_receipts.values()) + tuple(needed_receipts.values()) + (receipt,))
    securities = []
    new_hashes = {}
    for index, old in enumerate(base.universe.securities):
        if index % 64 == 0:
            check()
        replacement = rows_to_replace.get((old.instrument_id, old.effective_from))
        if replacement is None:
            securities.append(old)
            continue
        original, row, refs = replacement
        _require(old == original, 'BASE_REPLACEMENT_NOT_UNIQUE')
        data = old.model_dump()
        data.update(board=row.board, security_type=row.security_type,
            source_refs=refs + (rid,), **{name: derived_at for name in _CLOCKS})
        new = SecurityRecord.model_validate(data)
        securities.append(new)
        new_hashes[(old.instrument_id, str(old.effective_from))] = digest(new)
    universe = UniverseSnapshot(snapshot_id='DATED_IDENTITY_COMPOSITE_' + recipe_hash[:24],
        securities=tuple(securities), source_refs=tuple(sorted(set(base.universe.source_refs + (rid,)))),
        complete=base.universe.complete, expected_count=base.universe.expected_count,
        **{name: derived_at for name in _CLOCKS})
    projected = MarketInput(observation_date=base.observation_date, sessions=base.sessions, universe=universe,
        bars=base.bars, themes=base.themes, receipts=tuple(merged.values()), coverage_type=base.coverage_type,
        previous_emotions=base.previous_emotions, funds=base.funds)
    manifest = {**recipe, 'recipe_hash': recipe_hash, 'projection_receipt': receipt.model_dump(mode='json'),
        'replacement_count': len(rows_to_replace), 'unchanged_unavailable_counts': dict(sorted(unknown.items())),
        'new_state_hashes': [dict(instrument_id=iid, session=day, state_hash=value)
            for (iid, day), value in sorted(new_hashes.items())],
        'source_capability_issued': False,
        'caller_must_bind_authenticated_original_inputs_and_full_projected_input_hash': True}
    check()
    return projected, manifest
