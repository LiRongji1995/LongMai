"""Immutable, account-free R02 candidate contracts. All evidence clocks are explicit."""
from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import hashlib
import json
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CoverageType = Literal['PROSPECTIVE_CAPTURED', 'RECONSTRUCTED_HISTORICAL', 'UNVERIFIABLE_AS_OF', 'TEST_FIXTURE']
FAMILIES = ('ONE_TO_TWO', 'TWO_TO_THREE', 'HIGH_BOARD', 'TREND_NM', 'REBOUND_SETUP')
DISPLAY_ORDER = ('ONE_TO_TWO', 'TWO_TO_THREE', 'TREND_NM', 'HIGH_BOARD', 'REBOUND_SETUP')

def digest(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str, allow_nan=False).encode()).hexdigest()

class Frozen(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)

    @field_validator('*')
    @classmethod
    def aware_datetimes(cls,value):
        if isinstance(value,datetime) and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError('all evidence, feature, run and evaluation timestamps require an explicit timezone')
        return value

class Clocked(Frozen):
    event_time: datetime
    source_published_at: datetime
    first_seen_at: datetime
    ingested_at: datetime

    @field_validator('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('timezone-aware evidence clock required')
        return value

    @model_validator(mode='after')
    def clock_order(self):
        if self.ingested_at < self.first_seen_at:
            raise ValueError('ingested_at precedes first_seen_at')
        return self

    def visible(self, cutoff: datetime, reconstructed=False):
        # Primitive row clocks only. Consumers must also use EvidenceResolver
        # for authoritative parent receipts and historical reconstruction proof.
        from .visibility import clock_visible
        return clock_visible(self, cutoff, allow_late_capture=reconstructed)

class HistoricalTimeProof(Frozen):
    policy_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    evidence_hash: str
    event_time_basis: Literal['SOURCE_DATED_EVENT_RECORD', 'SOURCE_VERIFIED_EVENT_TIMESTAMP']
    publication_time_basis: Literal['SOURCE_PUBLICATION_TIMESTAMP', 'SOURCE_DOCUMENTED_RELEASE_RULE']
    applies_from: date
    applies_to: date
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    verified_at: datetime

    @model_validator(mode='after')
    def proof_scope(self):
        import re
        if not re.fullmatch('[0-9a-f]{64}', self.evidence_hash):
            raise ValueError('historical time proof requires SHA256 evidence')
        if self.applies_to < self.applies_from or any(not x for x in self.evidence_refs):
            raise ValueError('invalid historical proof scope')
        if not self.source_url.startswith(('https://', 'http://')):
            raise ValueError('historical source proof URL required')
        return self

class SourceReceipt(Clocked):
    receipt_id: str
    provider: str
    dataset: str
    content_hash: str
    field_version: str
    row_count: int = Field(ge=0)
    pagination_complete: bool
    status: Literal['COMPLETE', 'PARTIAL', 'INVALID', 'FETCH_FAILED', 'NOT_PUBLISHED']
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    source_url: str | None = None
    source_type: str | None = None
    instrument_ids: tuple[str, ...] = ()
    business_sessions: tuple[date, ...] = ()
    definition_version: str | None = None
    measurement_definition: str | None = None
    currency: str | None = None
    amount_unit: str | None = None
    historical_time_proof: HistoricalTimeProof | None = None

    @field_validator('content_hash')
    @classmethod
    def sha256(cls,value):
        import re
        if not re.fullmatch('[0-9a-f]{64}',value):raise ValueError('content_hash must be lowercase SHA256')
        return value

class SecurityRecord(Clocked):
    instrument_id: str
    exchange: Literal['SSE', 'SZSE', 'BSE', 'UNKNOWN']
    board: Literal['MAIN', 'STAR', 'CHINEXT', 'BJ', 'UNKNOWN']
    security_type: Literal['A_COMMON', 'B_SHARE', 'FUND', 'BOND', 'OTHER', 'UNKNOWN']
    listed_on: date
    effective_from: date
    effective_to: date | None = None
    delisted_on: date | None = None
    st: bool | None
    suspended: bool | None
    delisting_period: bool | None = False
    name: str = ''
    source_refs: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()

class UniverseSnapshot(Clocked):
    snapshot_id: str
    securities: tuple[SecurityRecord, ...]
    source_refs: tuple[str, ...] = ()
    complete: bool = False
    expected_count: int | None = Field(default=None, ge=0)

class DailyBar(Clocked):
    instrument_id: str
    session: date
    open: Decimal | None = Field(default=None, gt=0)
    high: Decimal | None = Field(default=None, gt=0)
    low: Decimal | None = Field(default=None, gt=0)
    close: Decimal | None = Field(default=None, gt=0)
    reference_prev_close: Decimal | None = Field(default=None, gt=0)
    volume_shares: Decimal | None = Field(default=None, ge=0)
    amount_cny: Decimal | None = Field(default=None, ge=0)
    limit_up: Decimal | None = Field(default=None, gt=0)
    limit_down: Decimal | None = Field(default=None, gt=0)
    limit_applicable: bool | None = None
    limit_verified: bool = False
    rule_id: str | None = None
    tick_size: Decimal = Field(default=Decimal('.01'), gt=0)
    comparable_open: Decimal | None = Field(default=None, gt=0)
    comparable_high: Decimal | None = Field(default=None, gt=0)
    comparable_low: Decimal | None = Field(default=None, gt=0)
    comparable_close: Decimal | None = Field(default=None, gt=0)
    comparable_basis: str | None = None
    float_shares: Decimal | None = Field(default=None, gt=0)
    float_shares_as_of: datetime | None = None
    turnover_fraction: float | None = Field(default=None, ge=0, le=10)
    turnover_basis: str | None = None
    source_refs: tuple[str, ...] = ()
    quality: Literal['VALID', 'PARTIAL', 'INVALID'] = 'VALID'

    @field_validator('float_shares_as_of')
    @classmethod
    def float_clock(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError('float shares clock must be timezone-aware')
        return value

class ThemeSnapshot(Clocked):
    theme_id: str
    name: str
    theme_type: Literal['INDUSTRY', 'EVENT', 'INDUSTRY_PROXY', 'QUALIFICATION', 'UNKNOWN']
    member_ids: tuple[str, ...]
    effective_from: date
    effective_to: date | None = None
    definition_version: str
    source_refs: tuple[str, ...] = ()

class FeatureValue(Frozen):
    value: float | int | str | bool | None
    unit: str
    window: str
    as_of: datetime
    source_refs: tuple[str, ...] = ()
    definition_version: str = 'YAOGU_FEATURES_V1'
    quality: str = 'VALID'

class LimitEvent(Frozen):
    instrument_id: str
    session: date
    close_limit_up: str
    touched_limit_up: str
    touched_not_closed: str
    one_price_up: str
    close_limit_down: str
    quality: str
    streak_exchange: int | None = None
    streak_lower_bound: int | None = None
    streak_quality: str = 'UNKNOWN'
    source_refs: tuple[str, ...] = ()

class MarketEmotionSnapshot(Frozen):
    session: date
    as_of: datetime
    score: float | None
    state: str
    components: dict[str, float | None] = Field(default_factory=dict)
    readings: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    # Absent fields identify immutable R02 documents; they are readable but do
    # not silently become authorized R03 three-session history.
    emotion_definition_version: str = 'LEGACY_R02_D1'
    strategy_id: str | None = None
    config_hash: str | None = None
    feature_definition_version: str | None = None
    pool_profile: str = 'MAIN_NON_ST'
    coverage_policy: str = 'LEGACY_R02_COVERAGE'
    coverage_type: CoverageType | None = None
    source_complete: bool = False
    document_id: str | None = None
    published_at: datetime | None = None
    effective_input_hash: str | None = None
    history_selection: dict[str, Any] = Field(default_factory=dict)
    publication_authority: dict[str, Any] = Field(default_factory=dict)
    authorization_mode: str = 'PUBLISHED_CANDIDATE'
    research_available_as_of: datetime | None = None

class MarketInput(Frozen):
    observation_date: date
    sessions: tuple[date, ...]
    universe: UniverseSnapshot
    bars: tuple[DailyBar, ...]
    themes: tuple[ThemeSnapshot, ...] = ()
    receipts: tuple[SourceReceipt, ...] = ()
    coverage_type: CoverageType
    previous_emotions: tuple[MarketEmotionSnapshot, ...] = ()
    funds: tuple[dict[str, Any], ...] = ()

    @model_validator(mode='after')
    def calendar(self):
        if tuple(sorted(set(self.sessions))) != self.sessions:
            raise ValueError('sessions must be unique ascending exchange sessions')
        if self.observation_date not in self.sessions:
            raise ValueError('observation_date absent from exchange calendar')
        keys = [(x.instrument_id, x.session, x.ingested_at) for x in self.bars]
        if len(set(keys)) != len(keys):
            raise ValueError('duplicate bar revision')
        return self

class CandidateItem(Frozen):
    instrument_id: str
    name: str
    primary_family: str
    secondary_tags: tuple[str, ...]
    features: dict[str, FeatureValue]
    components: dict[str, float | None]
    weights: dict[str, float]
    contributions: dict[str, float | None]
    score: float | None
    daily_score: float | None
    funds_score: float | None = None
    funds_evidence: dict[str, Any] = Field(default_factory=dict)
    family_rank: int | None
    tier: str
    primary_theme: str | None
    theme_evidence: tuple[dict[str, Any], ...] = ()
    reason_codes: tuple[str, ...]
    risk_flags: tuple[str, ...]
    missing: tuple[str, ...]
    thesis_conditions: tuple[dict[str, Any], ...] = ()
    evidence_refs: tuple[str, ...] = ()

class CandidateRun(Frozen):
    run_id: str
    revision: int
    parent_run_id: str | None
    generated_at: datetime
    observation_date: date
    decision_cutoff: datetime
    market_session: str
    strategy_id: str
    config_hash: str
    feature_definition_version: str
    source_hash: str
    universe_hash: str
    input_hash: str
    semantic_hash: str
    model_id: None = None
    profile: str
    parameter_status: str = 'INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED'
    coverage_type: CoverageType
    coverage: dict[str, Any]
    status: str
    items: tuple[CandidateItem, ...]
    display_ids: tuple[str, ...]
    focus_ids: tuple[str, ...]
    excluded: tuple[dict[str, Any], ...]
    unavailable: tuple[dict[str, Any], ...]
    emotion: MarketEmotionSnapshot
    source_refs: tuple[str, ...]
    hash_contract_version: str = 'LEGACY_R02_NARROW_INPUT_V1'
    raw_bundle_hash: str | None = None
    effective_input_hash: str | None = None
    semantic_hash_version: str = 'LEGACY_R02_SEMANTIC_V1'
    effective_dependencies: dict[str, Any] = Field(default_factory=dict)
