"""Deterministic risk and portfolio constraints. No LLM inputs or Qlib objects."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from math import floor

from pydantic import Field, field_validator, model_validator

from .common import CoreModel, digest


class RiskAction(str, Enum):
    ACCEPT = "ACCEPT"
    REDUCE = "REDUCE"
    REJECT = "REJECT"
    UNAVAILABLE = "UNAVAILABLE"


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return value


class Holding(CoreModel):
    instrument_id: str
    shares: int = Field(ge=0, strict=True)
    sellable_shares: int = Field(ge=0, strict=True)
    mark_price: float = Field(gt=0, strict=True)
    industry: str = Field(min_length=1)
    marked_at: datetime

    _time = field_validator("marked_at")(aware)

    @model_validator(mode="after")
    def validate_shares(self):
        if self.sellable_shares > self.shares:
            raise ValueError("sellable shares exceed held shares")
        return self


class PortfolioSnapshot(CoreModel):
    as_of: datetime
    cash: float = Field(ge=0, strict=True)
    holdings: tuple[Holding, ...] = ()
    currency: str = "CNY"

    _time = field_validator("as_of")(aware)

    @model_validator(mode="after")
    def validate_holdings(self):
        ids = [h.instrument_id for h in self.holdings]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate holdings")
        if any(h.marked_at > self.as_of for h in self.holdings):
            raise ValueError("future holding mark")
        if self.currency != "CNY":
            raise ValueError("A-share portfolio must be denominated in CNY")
        return self

    @property
    def nav(self) -> float:
        return self.cash + sum(h.shares * h.mark_price for h in self.holdings)


class RiskPolicy(CoreModel):
    """Engineering limits, explicitly versioned; not an optimized investment policy."""
    version: str = "risk-v2"
    max_positions: int = Field(default=5, ge=1, le=100, strict=True)
    max_stock_weight: float = Field(default=0.20, gt=0, le=1)
    max_industry_weight: float = Field(default=0.40, gt=0, le=1)
    minimum_order_value: float = Field(default=1000.0, ge=0)
    # Reservation only. Actual execution costs come from the simulator FeeSchedule.
    buy_cost_reserve_rate: float = Field(default=0.001, ge=0, le=0.1)
    minimum_cost_reserve: float = Field(default=5.0, ge=0)
    max_price_age_seconds: int = Field(default=7 * 86400, ge=0, strict=True)

    @property
    def policy_hash(self) -> str:
        return digest(self)


class RiskInput(CoreModel):
    instrument_id: str
    decision_time: datetime
    data_available_at: datetime
    price_timestamp: datetime
    reference_price: float | None = Field(default=None, gt=0)
    price_adjustment: str
    currency: str = "CNY"
    quality: str
    provider: str
    industry: str | None = None
    tradability: RiskAction = RiskAction.UNAVAILABLE
    tradability_reasons: tuple[str, ...] = ()
    exit_tradability: RiskAction = RiskAction.UNAVAILABLE
    exit_reasons: tuple[str, ...] = ()
    lot_size: int | None = Field(default=None, ge=1, strict=True)
    min_buy_shares: int | None = Field(default=None, ge=1, strict=True)
    hard_veto: bool = False
    veto_reasons: tuple[str, ...] = ()

    _time = field_validator("decision_time", "data_available_at", "price_timestamp")(aware)


class RiskDecision(CoreModel):
    decision: RiskAction
    reason_codes: tuple[str, ...]
    policy_version: str
    policy_hash: str
    approved_target_shares: int = Field(default=0, ge=0, strict=True)
    incremental_cash: float = Field(default=0, ge=0)
    target_weight: float = Field(default=0, ge=0, le=1)


class RiskEngine:
    def __init__(self, policy: RiskPolicy | None = None):
        self.policy = policy or RiskPolicy()

    def evaluate_exit(self, item: RiskInput, portfolio: PortfolioSnapshot) -> RiskDecision:
        """A full exit is subject to trusted fresh marks, but not BUY-side rules.

        ST or a buy limit can prohibit a new buy without prohibiting a sale.
        Conversely a stale snapshot cannot authorize liquidation just because
        the account says shares are sellable.
        """
        p = self.policy
        reasons = []
        held = next((h for h in portfolio.holdings if h.instrument_id == item.instrument_id and h.shares), None)
        if held is None:
            reasons.append("EXIT_HOLDING_UNAVAILABLE")
        if item.data_available_at > item.decision_time or item.price_timestamp > item.decision_time:
            reasons.append("FUTURE_DATA")
        if portfolio.as_of > item.decision_time:
            reasons.append("FUTURE_PORTFOLIO")
        if any((item.decision_time - h.marked_at).total_seconds() > p.max_price_age_seconds for h in portfolio.holdings):
            reasons.append("STALE_PORTFOLIO_MARK")
        if item.reference_price is None or item.price_adjustment != "RAW" or item.currency != "CNY":
            reasons.append("RAW_PRICE_UNAVAILABLE")
        if item.quality not in ("VERIFIED", "VALID") or item.provider.lower() in ("mock", "unknown", ""):
            reasons.append("UNTRUSTED_MARKET_DATA")
        if (item.decision_time - item.price_timestamp).total_seconds() > p.max_price_age_seconds:
            reasons.append("STALE_PRICE")
        if held and (not item.industry or item.industry != held.industry or item.reference_price != held.mark_price):
            reasons.append("INCONSISTENT_PORTFOLIO_MARK")
        if held and held.sellable_shares < held.shares:
            reasons.append("EXIT_NOT_SELLABLE")
        if item.exit_tradability != RiskAction.ACCEPT:
            reasons.extend(("EXIT_TRADABILITY_UNAVAILABLE", *item.exit_reasons))
        return RiskDecision(decision=RiskAction.UNAVAILABLE if reasons else RiskAction.ACCEPT,
            reason_codes=tuple(dict.fromkeys(reasons)) or ("EXIT_RISK_CHECKS_PASSED",),
            policy_version=p.version, policy_hash=p.policy_hash)

    def evaluate(self, item: RiskInput, portfolio: PortfolioSnapshot, *,
                 reserved_cash: float = 0, planned_positions: tuple[str, ...] = (),
                 industry_additions: dict[str, float] | None = None) -> RiskDecision:
        p = self.policy

        def result(action, *reasons, shares=0, cash=0.0, weight=0.0):
            return RiskDecision(decision=action, reason_codes=tuple(reasons), policy_version=p.version,
                                policy_hash=p.policy_hash, approved_target_shares=shares,
                                incremental_cash=cash, target_weight=weight)

        if reserved_cash < 0 or reserved_cash > portfolio.cash + 1e-8:
            raise ValueError("invalid reserved cash")
        if item.hard_veto:
            return result(RiskAction.REJECT, "HARD_VETO", *item.veto_reasons)
        if item.data_available_at > item.decision_time or item.price_timestamp > item.decision_time:
            return result(RiskAction.UNAVAILABLE, "FUTURE_DATA")
        if portfolio.as_of > item.decision_time:
            return result(RiskAction.UNAVAILABLE, "FUTURE_PORTFOLIO")
        if any((item.decision_time - h.marked_at).total_seconds() > p.max_price_age_seconds for h in portfolio.holdings):
            return result(RiskAction.UNAVAILABLE, "STALE_PORTFOLIO_MARK")
        if item.reference_price is None or item.price_adjustment != "RAW" or item.currency != "CNY":
            return result(RiskAction.UNAVAILABLE, "RAW_PRICE_UNAVAILABLE")
        if item.quality not in ("VERIFIED", "VALID") or item.provider.lower() in ("mock", "unknown", ""):
            return result(RiskAction.UNAVAILABLE, "UNTRUSTED_MARKET_DATA")
        if (item.decision_time - item.price_timestamp).total_seconds() > p.max_price_age_seconds:
            return result(RiskAction.UNAVAILABLE, "STALE_PRICE")
        if item.tradability == RiskAction.REJECT:
            return result(RiskAction.REJECT, *item.tradability_reasons or ("NOT_TRADABLE",))
        if item.tradability not in (RiskAction.ACCEPT, RiskAction.REDUCE):
            return result(RiskAction.UNAVAILABLE, *item.tradability_reasons or ("TRADABILITY_UNAVAILABLE",))
        if not item.industry or not item.lot_size or not item.min_buy_shares:
            return result(RiskAction.UNAVAILABLE, "SECURITY_TERMS_UNAVAILABLE")
        if portfolio.nav <= 0:
            return result(RiskAction.REJECT, "NO_CAPITAL")

        holdings = {h.instrument_id: h for h in portfolio.holdings if h.shares}
        if len(holdings) > p.max_positions:
            return result(RiskAction.UNAVAILABLE, "EXISTING_POSITION_COUNT_EXCEEDS_LIMIT")
        held = holdings.get(item.instrument_id)
        price = item.reference_price
        existing_shares = held.shares if held else 0
        existing_value = existing_shares * price
        active = set(holdings) | set(planned_positions)
        if not held and len(active) >= p.max_positions:
            return result(RiskAction.REJECT, "MAX_POSITIONS")
        if held and (held.industry != item.industry or abs(held.mark_price - price) > 1e-7):
            return result(RiskAction.UNAVAILABLE, "INCONSISTENT_PORTFOLIO_MARK")
        industry_value = sum(h.shares * h.mark_price for h in holdings.values() if h.industry == item.industry)
        additions = industry_additions or {}
        if any(v < 0 for v in additions.values()):
            raise ValueError("negative industry reservation")
        industry_room = max(0.0, portfolio.nav * p.max_industry_weight - industry_value - additions.get(item.industry, 0.0))
        stock_cap = portfolio.nav * p.max_stock_weight
        # Existing over-limit positions receive a reduction target; never add to them.
        if existing_value > stock_cap + 1e-8 or industry_value > portfolio.nav * p.max_industry_weight + 1e-8:
            cap = min(stock_cap, max(0.0, portfolio.nav * p.max_industry_weight - (industry_value - existing_value)))
            target = max(0, floor(cap / price / item.lot_size) * item.lot_size)
            if held and existing_shares - target > held.sellable_shares:
                return result(RiskAction.UNAVAILABLE, "REDUCTION_NOT_SELLABLE")
            if item.exit_tradability != RiskAction.ACCEPT:
                return result(RiskAction.UNAVAILABLE, "REDUCTION_TRADABILITY_UNAVAILABLE", *item.exit_reasons)
            return result(RiskAction.REDUCE, "EXPOSURE_LIMIT", shares=target, weight=target * price / portfolio.nav)
        cash_room = max(0.0, portfolio.cash - reserved_cash)
        spendable = max(0.0, (cash_room - p.minimum_cost_reserve) / (1 + p.buy_cost_reserve_rate))
        room = min(max(0.0, stock_cap - existing_value), industry_room, spendable)
        added = floor((room + 1e-9) / price / item.lot_size) * item.lot_size
        if added and (added < item.min_buy_shares or added * price < p.minimum_order_value):
            added = 0
        if not added:
            if existing_shares:
                return result(RiskAction.ACCEPT, "HOLD_WITHIN_LIMITS", shares=existing_shares,
                              weight=existing_value / portfolio.nav)
            reason = "INSUFFICIENT_CASH" if spendable <= 0 else "MINIMUM_ORDER_OR_EXPOSURE"
            return result(RiskAction.REJECT, reason)
        cost = added * price + max(p.minimum_cost_reserve, added * price * p.buy_cost_reserve_rate)
        target = existing_shares + added
        reduced = room < stock_cap - existing_value - 1e-8
        return result(RiskAction.REDUCE if reduced else RiskAction.ACCEPT,
                      "PORTFOLIO_LIMITED" if reduced else "RISK_CHECKS_PASSED", shares=target,
                      cash=cost, weight=target * price / portfolio.nav)


def protective_prices(price: float, var_percent: float, stop_multiple: float = 1.5,
                      profit_multiple: float = 2.0) -> tuple[float, float]:
    """VaR is a loss magnitude here; negative percentile returns cannot invert stops."""
    import math
    if not all(math.isfinite(v) for v in (price, var_percent, stop_multiple, profit_multiple)):
        raise ValueError("finite values required")
    if price <= 0 or var_percent == 0 or stop_multiple <= 0 or profit_multiple <= 0:
        raise ValueError("positive price and nonzero risk magnitude required")
    loss = abs(var_percent) / 100
    if loss * stop_multiple >= 1:
        raise ValueError("risk magnitude would imply nonpositive stop")
    stop, profit = round(price * (1 - loss * stop_multiple), 2), round(price * (1 + loss * profit_multiple), 2)
    if not 0 < stop < price < profit:
        raise ValueError("tick rounding produces invalid protection levels")
    return stop, profit
