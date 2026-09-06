"""Frozen evening theses and 09:25 facts; never an execution or probability model."""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from .contracts import Clocked, Frozen, CandidateRun, MarketInput, digest

SH = ZoneInfo('Asia/Shanghai')
RELAY = {'ONE_TO_TWO', 'TWO_TO_THREE', 'HIGH_BOARD'}


class ThesisCondition(Frozen):
    condition_id: str
    version: str = 'AUCTION_THESIS_V1'
    instrument_id: str
    primary_family: str
    expected_session: date | None
    primary_theme: str | None
    peer_ids: tuple[str, ...]
    previous_session_amount: Decimal | None
    previous_run_end_close: Decimal | None
    reference_comparable_close: Decimal | None
    frozen_at: datetime
    evidence_refs: tuple[str, ...]
    thresholds: dict[str, float]
    formulae: dict[str, str]
    calendar_ref: str | None = None


class AuctionSnapshot(Clocked):
    instrument_id: str
    session: date
    matched_price: Decimal = Field(gt=0)
    matched_amount_cny: Decimal = Field(ge=0)
    reference_prev_close: Decimal = Field(gt=0)
    limit_up: Decimal | None = Field(default=None, gt=0)
    tick_size: Decimal = Field(default=Decimal('.01'), gt=0)
    source_ref: str = Field(min_length=1)
    capture_kind: Literal['AUCTION_0925']
    quality: Literal['VALID', 'PARTIAL', 'INVALID']

    @model_validator(mode='after')
    def matching_session(self):
        if self.event_time.astimezone(SH).date() != self.session:
            raise ValueError('auction event/session mismatch')
        for price in (self.matched_price, self.reference_prev_close, self.limit_up):
            if price is not None and price / self.tick_size != (price / self.tick_size).to_integral_value():
                raise ValueError('auction price off tick')
        return self


class ThesisEvaluation(Frozen):
    condition_id: str
    condition_hash: str
    instrument_id: str
    session: date | None
    evaluated_at: datetime
    original_score: float | None
    original_rank: int | None
    original_tier: str
    reviewed_tier: str
    conditions: dict[str, dict]
    flags: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    status: Literal['EVALUATED', 'PARTIAL', 'UNAVAILABLE']


DEFAULT_THRESHOLDS = {'gap_weak': -.03, 'amount_weak': .005, 'amount_confirmed': .02,
                      'high_gap': .08, 'theme_positive_fraction': .30, 'theme_median_gap': -.02}
FORMULAE = {
    'GAP_WEAK': 'matched_price / verified_reference_prev_close - 1 <= gap_weak; relay families only',
    'AUCTION_PARTICIPATION_WEAK': 'matched_amount_cny / previous_session_amount < amount_weak',
    'PARTICIPATION_CONFIRMED': 'amount_ratio >= amount_confirmed and gap > gap_weak',
    'HIGH_GAP': 'gap >= high_gap',
    'ENTRY_UNCERTAIN': 'matched_price == verified_limit_up or HIGH_GAP',
    'THEME_THESIS_WEAKENED': 'frozen_peer_positive_fraction < theme_positive_fraction and frozen_peer_median_gap < theme_median_gap',
}


def freeze_theses(run: CandidateRun, bundle: MarketInput, thresholds: dict | None = None, condition_version='AUCTION_CONDITIONS_V1',
                  *, known_sessions=(), calendar_ref=None) -> CandidateRun:
    limits = DEFAULT_THRESHOLDS if thresholds is None else thresholds
    if set(limits) != set(DEFAULT_THRESHOLDS):
        raise ValueError('unknown or missing auction threshold')
    next_dates = [s for s in bundle.sessions if s > run.observation_date]
    if not next_dates and run.observation_date in known_sessions:
        next_dates = [s for s in known_sessions if s > run.observation_date]
    target = next_dates[0] if next_dates else None
    reconstructed = bundle.coverage_type == 'RECONSTRUCTED_HISTORICAL'
    refs = {r.receipt_id for r in bundle.receipts if r.visible(run.decision_cutoff, reconstructed) and r.status in {'COMPLETE', 'PARTIAL'}}
    items = []
    for item in run.items:
        chosen = next((t for t in item.theme_evidence if t.get('theme_id') == item.primary_theme), {})
        peers = tuple(sorted(set(chosen.get('peer_ids', ())) - {item.instrument_id}))
        def feature(*names):
            value = next((item.features[n].value for n in names if n in item.features), None)
            return Decimal(str(value)) if value is not None else None
        visible = [b for b in bundle.bars if b.instrument_id == item.instrument_id
                   and b.visible(run.decision_cutoff, reconstructed) and b.source_refs and set(b.source_refs) <= refs]
        current = max((b for b in visible if b.session == run.observation_date), key=lambda b: b.ingested_at, default=None)
        previous_day = item.features.get('previous_run_end_session')
        previous = max((b for b in visible if previous_day is not None and str(b.session) == previous_day.value),
                       key=lambda b: b.ingested_at, default=None)
        payload = dict(instrument_id=item.instrument_id, primary_family=item.primary_family,
                       expected_session=target, primary_theme=item.primary_theme, peer_ids=peers,
                       previous_session_amount=current.amount_cny if current else feature('amount_cny', 'amount'),
                       previous_run_end_close=previous.comparable_close if previous else feature('previous_run_end_close', 'last_run_end_close'),
                       reference_comparable_close=current.comparable_close if current else feature('comparable_close'), frozen_at=run.generated_at,
                       evidence_refs=item.evidence_refs, thresholds=dict(limits), formulae=dict(FORMULAE),
                       calendar_ref=calendar_ref)
        condition = ThesisCondition(condition_id='pending', version=condition_version, **payload)
        condition = condition.model_copy(update={'condition_id': 'thesis_' + digest(condition.model_dump(mode='json', exclude={'condition_id', 'version'}))})
        items.append(item.model_copy(update={'thesis_conditions': (condition.model_dump(mode='json'),)}))
    return run.model_copy(update={'items': tuple(items)})


def _valid_quote(snapshot: AuctionSnapshot | None, target: date | None, cutoff: datetime,resolver=None):
    if snapshot is None or snapshot.quality != 'VALID' or snapshot.session != target:
        return False
    if not (resolver.is_visible(snapshot,source_refs=(snapshot.source_ref,)) if resolver else snapshot.visible(cutoff)):
        return False
    # A 09:30 quote cannot be relabeled as a 09:25 auction, even on the same date.
    return snapshot.event_time.astimezone(SH).time().replace(tzinfo=None) == time(9, 25)


def evaluate_auction(run: CandidateRun, quotes: tuple[AuctionSnapshot, ...], evaluated_at: datetime,
                     *,receipts=(),sessions=(),coverage_type=None) -> tuple[ThesisEvaluation, ...]:
    if evaluated_at.tzinfo is None or evaluated_at < run.generated_at:
        raise ValueError('auction review time precedes published evening facts')
    import statistics
    from .visibility import EvidenceResolver
    resolver=EvidenceResolver(receipts,cutoff=evaluated_at,coverage_type=coverage_type or run.coverage_type,sessions=sessions)
    by_id = {}
    for quote in quotes:
        if not resolver.is_visible(quote,instrument_id=quote.instrument_id,session=quote.session,
                source_refs=(quote.source_ref,),require_complete=True,require_session=True,source_types={'AUCTION_0925'}):
            continue
        parent=resolver.receipts[quote.source_ref]
        if quote.instrument_id not in parent.instrument_ids or quote.session not in parent.business_sessions:
            continue
        if quote.event_time.astimezone(SH).time().replace(tzinfo=None) != time(9, 25):
            continue
        key = (quote.instrument_id, quote.session)
        previous = by_id.get(key)
        if previous is None or quote.ingested_at > previous.ingested_at:
            by_id[key] = quote
        elif quote.ingested_at == previous.ingested_at and quote != previous:
            raise ValueError('conflicting auction revision')
    results = []
    for item in run.items:
        if len(item.thesis_conditions) != 1:
            raise ValueError('candidate requires frozen thesis before review')
        frozen = ThesisCondition.model_validate(item.thesis_conditions[0])
        if frozen.instrument_id != item.instrument_id or frozen.primary_family != item.primary_family:
            raise ValueError('thesis identity mismatch')
        if frozen.condition_id != 'thesis_' + digest(frozen.model_dump(mode='json', exclude={'condition_id', 'version'})):
            raise ValueError('thesis content changed')
        outcomes = {name: {'state': 'NOT_EVALUATED', 'reason': 'AUCTION_SNAPSHOT_UNAVAILABLE',
                           'formula': formula, 'value': None} for name, formula in frozen.formulae.items()}
        refs = []
        def record(name, truth, value, source_refs, reason=None):
            outcomes[name] = {'state': 'TRUE' if truth else 'FALSE', 'value': value,
                              'formula': frozen.formulae[name], 'source_refs': source_refs,
                              'reason': reason}
        q = by_id.get((item.instrument_id, frozen.expected_session))
        limits = {k: Decimal(str(v)) for k, v in frozen.thresholds.items()}
        if _valid_quote(q, frozen.expected_session, evaluated_at,resolver):
            refs.append(q.source_ref)
            gap = q.matched_price / q.reference_prev_close - 1
            ratio = q.matched_amount_cny / frozen.previous_session_amount if frozen.previous_session_amount and frozen.previous_session_amount > 0 else None
            common = {'gap': float(gap), 'matched_price': str(q.matched_price), 'source_event_time': q.event_time.isoformat(),
                      'first_seen_at': q.first_seen_at.isoformat()}
            if item.primary_family in RELAY:
                record('GAP_WEAK', gap <= limits['gap_weak'], common, [q.source_ref])
            else:
                outcomes['GAP_WEAK'].update(reason='NOT_APPLICABLE_TO_NON_RELAY_FAMILY', value=common)
            if ratio is not None:
                value = {**common, 'auction_amount_ratio': float(ratio)}
                record('AUCTION_PARTICIPATION_WEAK', ratio < limits['amount_weak'], value, [q.source_ref])
                record('PARTICIPATION_CONFIRMED', ratio >= limits['amount_confirmed'] and gap > limits['gap_weak'], value, [q.source_ref])
            record('HIGH_GAP', gap >= limits['high_gap'], common, [q.source_ref])
            if q.limit_up is not None or gap >= limits['high_gap']:
                record('ENTRY_UNCERTAIN', gap >= limits['high_gap'] or q.matched_price == q.limit_up, common, [q.source_ref])
        peer_quotes = [by_id.get((iid, frozen.expected_session)) for iid in frozen.peer_ids]
        if frozen.peer_ids and all(_valid_quote(q, frozen.expected_session, evaluated_at,resolver) for q in peer_quotes):
            peer_gaps = [q.matched_price / q.reference_prev_close - 1 for q in peer_quotes]
            positive = Decimal(sum(g > 0 for g in peer_gaps)) / len(peer_gaps)
            median = statistics.median(peer_gaps)
            peer_refs = [q.source_ref for q in peer_quotes]
            refs.extend(peer_refs)
            record('THEME_THESIS_WEAKENED', positive < limits['theme_positive_fraction'] and median < limits['theme_median_gap'],
                   {'positive_fraction': float(positive), 'median_gap': float(median), 'n_expected': len(frozen.peer_ids), 'n_observed': len(peer_quotes)}, peer_refs)
        else:
            outcomes['THEME_THESIS_WEAKENED']['reason'] = 'FROZEN_THEME_AUCTION_COVERAGE_INCOMPLETE'
        flags = tuple(k for k, v in outcomes.items() if v['state'] == 'TRUE')
        tier = 'WATCH' if item.tier == 'FOCUS' and 'GAP_WEAK' in flags else item.tier
        count = sum(v['state'] != 'NOT_EVALUATED' for v in outcomes.values())
        results.append(ThesisEvaluation(condition_id=frozen.condition_id, condition_hash=digest(frozen),
            instrument_id=item.instrument_id, session=frozen.expected_session, evaluated_at=evaluated_at,
            original_score=item.score, original_rank=item.family_rank, original_tier=item.tier, reviewed_tier=tier,
            conditions=outcomes, flags=flags, evidence_refs=tuple(sorted(set(refs))),
            status='UNAVAILABLE' if count == 0 else 'EVALUATED' if count == len(outcomes) else 'PARTIAL'))
    return tuple(results)
