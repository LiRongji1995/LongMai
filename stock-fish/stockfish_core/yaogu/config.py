"""Versioned initial hypotheses. No executable formula strings or silent coercion."""
from pathlib import Path
from typing import Literal, get_origin, get_args
import math
import yaml
from pydantic import Field, field_validator, model_validator
from .contracts import Frozen, FAMILIES, digest

class StrictHypothesis(Frozen):
    @model_validator(mode='before')
    @classmethod
    def scalar_types(cls, data):
        if not isinstance(data,dict):return data
        for name,value in data.items():
            if name not in cls.model_fields:continue
            annotation=cls.model_fields[name].annotation
            if annotation is bool and type(value) is not bool:
                raise ValueError(f'{name} must be a boolean, without coercion')
            if annotation is int and type(value) is not int:
                raise ValueError(f'{name} must be an integer, without coercion')
            if annotation is float and (type(value) not in (int,float) or not math.isfinite(value)):
                raise ValueError(f'{name} must be a finite number, without coercion')
            if get_origin(annotation) is tuple and get_args(annotation) and get_args(annotation)[0] in (int,float):
                expected=get_args(annotation)[0]
                if not isinstance(value,(list,tuple)) or any(type(v) not in ((int,) if expected is int else (int,float)) or not math.isfinite(v) for v in value):
                    raise ValueError(f'{name} requires numeric values of the declared unit/type')
        return data

class Weights(StrictHypothesis):
    B: float = Field(ge=0, le=100)
    V: float = Field(ge=0, le=100)
    T: float = Field(ge=0, le=100)
    L: float = Field(ge=0, le=100)
    G: float = Field(ge=0, le=100)
    M: float = Field(ge=0, le=100)

    @model_validator(mode='after')
    def total(self):
        if abs(sum(self.model_dump().values())-100) > 1e-9:
            raise ValueError('weights must sum to 100')
        return self

class UniverseConfig(StrictHypothesis):
    include_st: bool = False
    include_recent_ipo: bool = False
    min_listing_sessions: int = Field(default=60, ge=6, le=500)
    recent_ipo_min_sessions: int = Field(default=6, ge=6, le=59)
    required_history_sessions: int = Field(default=60, ge=60, le=250)

    @model_validator(mode='after')
    def independent_pools(self):
        if self.include_st and self.include_recent_ipo:
            raise ValueError('ST and recent IPO are separate research pools; select one per config revision')
        return self

class Thresholds(StrictHypothesis):
    focus_score: float = Field(default=70, ge=0, le=100)
    watch_score: float = Field(default=55, ge=0, le=100)
    focus_v: float = Field(default=45, ge=0, le=100)
    focus_t: float = Field(default=50, ge=0, le=100)
    high_board_warning: int = Field(default=7, ge=3)
    market_coverage_fraction: float = Field(default=.98, ge=.98, le=1)
    theme_coverage_fraction: float = Field(default=.95, ge=.95, le=1)
    theme_min_members: int = Field(default=5, ge=5)

    @model_validator(mode='after')
    def tiers(self):
        if self.watch_score > self.focus_score:
            raise ValueError('watch threshold exceeds focus')
        return self

class AuctionConfig(StrictHypothesis):
    gap_weak: float = Field(default=-.03, ge=-1, le=0)
    participation_weak: float = Field(default=.005, ge=0, le=1)
    participation_confirmed: float = Field(default=.02, ge=0, le=1)
    high_gap: float = Field(default=.08, ge=0, le=1)
    theme_positive_fraction: float = Field(default=.30, ge=0, le=1)
    theme_median_gap: float = Field(default=-.02, ge=-1, le=0)
    condition_version: str = 'AUCTION_CONDITIONS_V1'

    @model_validator(mode='after')
    def ordered(self):
        if self.participation_weak >= self.participation_confirmed:
            raise ValueError('auction amount intervals reversed')
        return self

class ScheduleConfig(StrictHypothesis):
    timezone: Literal['Asia/Shanghai'] = 'Asia/Shanghai'
    preliminary: str = '16:10'
    evening: str = '20:30'
    auction_review: str = '09:26'
    auction_match: str = '09:25'
    prospective_sessions: int = Field(default=20, ge=1, le=252)

    @field_validator('preliminary', 'evening', 'auction_review', 'auction_match')
    @classmethod
    def clock(cls, v):
        import re
        if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', v):
            raise ValueError('time must be HH:MM')
        return v

class DisplayConfig(StrictHypothesis):
    observe_count: int = Field(default=20, ge=1, le=500)
    focus_count: int = Field(default=5, ge=0, le=100)
    order: tuple[str, ...] = ('ONE_TO_TWO', 'TWO_TO_THREE', 'TREND_NM', 'HIGH_BOARD', 'REBOUND_SETUP')

    @field_validator('order')
    @classmethod
    def all_families(cls, v):
        if len(v) != 5 or set(v) != set(FAMILIES):
            raise ValueError('display order must contain five unique families')
        return v

class EvaluationConfig(StrictHypothesis):
    horizons: tuple[int, ...] = (1, 2, 3, 5, 10)
    history_sessions: int = Field(default=252, ge=1, le=504)
    warmup_sessions: int = Field(default=60, ge=60, le=250)
    max_request_sessions: int = Field(default=504, ge=60, le=504)
    random_seed: int = 20260905
    random_repetitions: int = Field(default=100, ge=1, le=1000)
    bootstrap_repetitions: int = Field(default=1000, ge=1, le=10000)
    bootstrap_seed: int = 20260905
    train_sessions: int = 120
    valid_sessions: int = 40
    test_sessions: int = 20
    step_sessions: int = 20

    @field_validator('horizons')
    @classmethod
    def horizon_contract(cls, v):
        if v != (1, 2, 3, 5, 10):
            raise ValueError('V1 observation horizons are trading sessions 1/2/3/5/10')
        return v

def default_weights():
    rows = ((20,25,25,10,5,15),(25,20,25,10,5,15),(20,15,25,15,5,20),(15,30,25,10,5,15),(20,30,20,10,5,15))
    return {f: Weights(**dict(zip(('B','V','T','L','G','M'), row))) for f,row in zip(FAMILIES, rows)}

class StrategyConfig(StrictHypothesis):
    strategy_id: str = 'YAOGU_V1.1.0'
    parameter_status: Literal['INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED'] = 'INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED'
    feature_definition_version: Literal['YAOGU_FEATURES_V1', 'YAOGU_FEATURES_V1_R03'] = 'YAOGU_FEATURES_V1_R03'
    units: Literal['fraction_CNY_shares'] = 'fraction_CNY_shares'
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    auction: AuctionConfig = Field(default_factory=AuctionConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    weights: dict[str, Weights] = Field(default_factory=default_weights)
    amount_traps: dict[str, tuple[float,float,float,float]] = Field(default_factory=lambda: {
        'ONE_TO_TWO':(.3,.8,2.5,5), 'TWO_TO_THREE':(.1,.35,1.5,4), 'HIGH_BOARD':(.1,.35,1.5,4),
        'TREND_NM':(.3,.8,2,4), 'REBOUND_SETUP':(.3,.8,2,4)})
    turnover_trap: tuple[float,float,float,float] = (0,.03,.20,.45)
    funds_primary: bool = False

    @model_validator(mode='after')
    def strategy(self):
        if set(self.weights) != set(FAMILIES) or set(self.amount_traps) != set(FAMILIES):
            raise ValueError('exactly five registered families required')
        for row in [self.turnover_trap, *self.amount_traps.values()]:
            if not all(math.isfinite(v) for v in row) or not row[0] < row[1] <= row[2] < row[3]:
                raise ValueError('invalid trapezoid bounds')
        return self

    @field_validator('amount_traps', mode='before')
    @classmethod
    def trap_numbers(cls, value):
        if not isinstance(value,dict) or any(not isinstance(row,(tuple,list)) or any(type(v) not in (int,float) for v in row) for row in value.values()):
            raise ValueError('amount traps require numbers without coercion')
        return value

    @property
    def config_hash(self):
        return digest(self)

def load_config(path=None):
    if path is None:
        return StrategyConfig()
    return StrategyConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding='utf-8')))

def R(x, a, b):
    if not all(math.isfinite(v) for v in (a,b)) or a >= b:
        raise ValueError('R requires finite a < b')
    if x is None or not math.isfinite(float(x)):
        return None
    return 100 * min(1, max(0, (float(x)-a)/(b-a)))

def Trap(x,a,b,c,d):
    if not all(math.isfinite(v) for v in (a,b,c,d)) or not a < b <= c < d:
        raise ValueError('Trap requires finite a < b <= c < d')
    if x is None or not math.isfinite(float(x)):
        return None
    x=float(x)
    if x <= a or x >= d: return 0.0
    if x < b: return 100*(x-a)/(b-a)
    if x <= c: return 100.0
    return 100*(d-x)/(d-c)

def weighted_score(components, weights):
    weights = weights.model_dump() if isinstance(weights, Weights) else Weights.model_validate(weights).model_dump()
    if any(components.get(k) is None for k in weights): return None
    if any(not math.isfinite(float(components[k])) or not 0 <= components[k] <= 100 for k in weights):
        raise ValueError('components must be finite scores in [0,100]')
    return sum(weights[k]*components[k] for k in weights)/100
