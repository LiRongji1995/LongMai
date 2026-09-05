"""Small paper exchange with explicit decision/fill separation and cash accounting.

The callback is the live RecommendationEngine policy adapter. This module does
not rank securities or choose an alternative research strategy.
"""
from __future__ import annotations

import math
import uuid
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Literal, Mapping
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from .common import CoreModel, UnavailableError, digest
from .data import CanonicalMarketBar, TradingCalendar, TradabilityService
from .model_registry import safe_id, write_once, implementation_identity
from .risk import Holding, PortfolioSnapshot, RiskInput, RiskPolicy, aware
from .recommendation import Candidate, FinalRecommendation, RecommendationContext, RecommendationEngine

SH = ZoneInfo('Asia/Shanghai')


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


class FeeSchedule(CoreModel):
    version: str = 'paper-fees-v1'
    commission_rate: float = Field(default=.0005, ge=0, le=.1)
    minimum_commission: float = Field(default=5., ge=0)
    sell_stamp_rate: float = Field(default=.0005, ge=0, le=.1)
    slippage_bps: float = Field(default=0., ge=0, le=1000)

    def cost(self, side: str, quantity: int, price: float) -> Decimal:
        gross = Decimal(quantity) * Decimal(str(price))
        commission = max(gross * Decimal(str(self.commission_rate)), Decimal(str(self.minimum_commission)))
        tax = gross * Decimal(str(self.sell_stamp_rate)) if side == 'SELL' else Decimal(0)
        return money(commission + tax) if quantity else Decimal(0)


class CorporateAction(CoreModel):
    instrument_id: str
    ex_date: date
    pay_date: date
    available_at: datetime
    split_ratio: float = Field(default=1., gt=0)
    net_cash_dividend_per_share: float = Field(default=0., ge=0)
    source_id: str

    _time = field_validator('available_at')(aware)

    @model_validator(mode='after')
    def pay_after_ex(self):
        if self.pay_date < self.ex_date:
            raise ValueError('pay date cannot precede ex date')
        return self


class BacktestRequest(CoreModel):
    start_date: date
    end_date: date
    initial_portfolio: PortfolioSnapshot
    policy_version: str
    data_version: str
    universe_version: str
    benchmark_id: str | None = None
    run_id: str | None = None
    fees: FeeSchedule = FeeSchedule()
    # A canonical adapter always uses its own live RiskPolicy. Standalone
    # accounting callbacks must explicitly choose their execution limits.
    execution_risk_policy: RiskPolicy | None = None
    corporate_actions_complete: bool = False
    corporate_actions: tuple[CorporateAction, ...] = ()
    # A declared capacity assumption based only on prior, visible volume.
    max_prior_volume_participation: float = Field(default=.05, gt=0, le=1)

    @model_validator(mode='after')
    def ordered(self):
        if (self.start_date > self.end_date
                or self.initial_portfolio.as_of > datetime.combine(self.start_date, time(9, 30), SH)):
            raise ValueError('invalid backtest start/end or initial portfolio time')
        if any(h.marked_at > self.initial_portfolio.as_of for h in self.initial_portfolio.holdings):
            raise ValueError('future initial holding marks')
        keys = [(a.instrument_id, a.ex_date, a.source_id) for a in self.corporate_actions]
        if len(keys) != len(set(keys)):
            raise ValueError('duplicate corporate action')
        if not self.policy_version or not self.data_version or not self.universe_version:
            raise ValueError('backtest versions required')
        if not self.corporate_actions_complete:
            raise ValueError('CORPORATE_ACTION_COVERAGE_UNAVAILABLE')
        return self


class Order(CoreModel):
    order_id: str
    instrument_id: str
    side: Literal['BUY', 'SELL']
    quantity: int
    decision_time: datetime
    data_available_time: datetime
    earliest_trade_time: datetime
    execution_time: datetime
    filled_quantity: int
    execution_price: float | None
    fee: float
    status: Literal['FILLED', 'PARTIAL', 'REJECTED']
    reason_codes: tuple[str, ...]


class DailyAccount(CoreModel):
    date: date
    cash: float
    receivables: float
    positions: dict[str, int]
    nav: float
    daily_return: float
    cumulative_return: float
    benchmark_return: float | None
    excess_return: float | None
    drawdown: float
    turnover: float
    fees: float


class BacktestResult(CoreModel):
    run_id: str
    policy_version: str
    data_version: str
    universe_version: str
    calendar_version: str
    security_version: str
    fee_version: str
    policy_adapter: Literal['CANONICAL_RECOMMENDATION', 'CALLBACK_RESEARCH_ONLY']
    policy_hash: str | None = None
    risk_policy_hash: str | None = None
    execution_risk_policy_hash: str | None = None
    source_code_hash: str | None = None
    git_commit: str | None = None
    orders: tuple[Order, ...]
    daily: tuple[DailyAccount, ...]
    final_portfolio: PortfolioSnapshot
    final_receivables: float = 0.
    total_return: float
    benchmark_return: float | None
    excess_return: float | None
    max_drawdown: float
    total_fees: float
    method: str = 'paper-next-open-cash-nav-v1'
    limitations: tuple[str, ...] = ('PAPER_EXECUTION_NOT_LIVE_FILLS', 'BENCHMARK_PRICE_RETURN',)
    artifact_path: str | None = None


PolicyCallback = Callable[[datetime, tuple[CanonicalMarketBar, ...], PortfolioSnapshot], Mapping[str, float] | None]


class PolicyReplayInput(CoreModel):
    context: RecommendationContext
    candidates: tuple[Candidate, ...]
    risk_inputs: dict[str, RiskInput]


class PolicyReplayAdapter:
    """Replay calls the exact live policy; builders supply only timestamped inputs.

    A builder may replay precomputed versioned model scores or call QuantEngine.
    It must never compute scores from future returns. The simulator gives it only
    bars already visible at the decision time and the real simulated account.
    """
    def __init__(self, engine: RecommendationEngine,
                 input_builder: Callable[[datetime, tuple[CanonicalMarketBar, ...], PortfolioSnapshot], PolicyReplayInput]):
        self.engine, self.input_builder = engine, input_builder
        self.decisions: list[FinalRecommendation] = []
        self.inputs: list[PolicyReplayInput] = []

    def __call__(self, decision_time, visible_bars, portfolio):
        inputs = self.input_builder(decision_time, visible_bars, portfolio)
        if inputs.context.decision_time != decision_time or inputs.context.mode != 'REPLAY':
            raise ValueError('replay policy context must use the simulator decision and REPLAY mode')
        result = self.engine.evaluate(inputs.context, inputs.candidates, inputs.risk_inputs, portfolio)
        self.inputs.append(inputs)
        self.decisions.append(result)
        return result.target_weights


class BacktestEngine:
    def __init__(self, calendar: TradingCalendar, tradability: TradabilityService, root: str | Path | None = None):
        self.calendar, self.tradability = calendar, tradability
        self.root = Path(root) if root else None

    def _limit_buy(self, *, identity, quantity, lot, price, raw_open, cash, positions,
                   receivables, by_key, day, execution_time, fees, risk_policy):
        """Final BUY limit using open quotes only; never today's close/volume.

        Previously held exposure may drift above a cap. We leave those holdings
        untouched, but additional shares must satisfy every post-fee exposure
        limit. Unknown opening marks/industries cannot authorize new exposure.
        """
        active = {i: q for i, q in positions.items() if q > 0}
        if len(active) > risk_policy.max_positions or (identity not in active and len(active) >= risk_policy.max_positions):
            return 0, ['EXECUTION_MAX_POSITIONS']
        try:
            candidate = self.tradability.security_master.resolve(identity, day, execution_time)
            if not candidate.industry:
                return 0, ['EXECUTION_PORTFOLIO_TERMS_UNAVAILABLE']
            nav = cash + receivables
            same_industry = Decimal(0)
            stock_value = Decimal(0)
            for held_id, shares in active.items():
                mark_bar = by_key.get((day, held_id))
                if mark_bar is None or mark_bar.open is None:
                    return 0, ['EXECUTION_PORTFOLIO_MARK_UNAVAILABLE']
                mark = Decimal(str(mark_bar.open))
                if mark <= 0:
                    return 0, ['EXECUTION_PORTFOLIO_MARK_UNAVAILABLE']
                state = self.tradability.security_master.resolve(held_id, day, execution_time)
                if not state.industry:
                    return 0, ['EXECUTION_PORTFOLIO_TERMS_UNAVAILABLE']
                value = shares * mark
                nav += value
                if state.industry == candidate.industry:
                    same_industry += value
                if held_id == identity:
                    stock_value = value
        except UnavailableError:
            return 0, ['EXECUTION_PORTFOLIO_TERMS_UNAVAILABLE']
        quote = Decimal(str(raw_open))
        stock_limit = Decimal(str(risk_policy.max_stock_weight))
        industry_limit = Decimal(str(risk_policy.max_industry_weight))

        def breaches(shares):
            # Mark the purchased shares at the contemporaneous raw opening
            # quote; slippage and actual rounded fees reduce NAV immediately.
            after = nav + shares * quote - money(Decimal(shares) * Decimal(str(price))) - fees.cost('BUY', shares, price)
            if after <= 0:
                return ['EXECUTION_FEES_EXCEED_NAV']
            reasons = []
            if stock_value + shares * quote > stock_limit * after + Decimal('.00000001'):
                reasons.append('EXECUTION_STOCK_EXPOSURE_LIMIT')
            if same_industry + shares * quote > industry_limit * after + Decimal('.00000001'):
                reasons.append('EXECUTION_INDUSTRY_EXPOSURE_LIMIT')
            return reasons

        reasons = breaches(quantity)
        if not reasons:
            return quantity, []
        # Monotone bounded search over legal lots avoids a huge per-share loop.
        low, high = 0, quantity // lot
        while low < high:
            middle = (low + high + 1) // 2
            if breaches(middle * lot):
                high = middle - 1
            else:
                low = middle
        return low * lot, reasons

    def run(self, request: BacktestRequest, bars: tuple[CanonicalMarketBar, ...], policy: PolicyCallback) -> BacktestResult:
        run_id = safe_id(request.run_id) if request.run_id else 'backtest-' + uuid.uuid4().hex
        directory = self.root / run_id if self.root else None
        implementation = implementation_identity()
        if directory:
            directory.mkdir(parents=True, exist_ok=False)
            write_once(directory / 'request.json', request)
            write_once(directory / 'market_bars.json', bars)
            write_once(directory / 'implementation.json', implementation)
            write_once(directory / 'market_rules.json', {
                'sessions': [s.isoformat() for s in self.calendar.sessions],
                'coverage_start': self.calendar.coverage_start.isoformat(),
                'coverage_end': self.calendar.coverage_end.isoformat(), 'securities': self.tradability.security_master.states,
                'min_listing_sessions': self.tradability.min_listing_sessions, 'allow_st': self.tradability.allow_st,
            })
        try:
            result = self._run(request, bars, policy, run_id, directory)
            if implementation_identity()['source_code_hash'] != implementation['source_code_hash']:
                raise UnavailableError('IMPLEMENTATION_CHANGED_DURING_BACKTEST', 'backtest')
            result = result.model_copy(update=implementation)
            if directory:
                write_once(directory / 'result.json', result)
                if isinstance(policy, PolicyReplayAdapter):
                    write_once(directory / 'policy_inputs.json', policy.inputs)
                    write_once(directory / 'policy_decisions.json', policy.decisions)
            return result
        except Exception as exc:
            if directory:
                write_once(directory / 'failure.json', {'reason': getattr(exc, 'reason', type(exc).__name__)})
            raise

    def _run(self, request, bars, policy, run_id, directory):
        self.calendar.is_session(request.start_date)
        self.calendar.is_session(request.end_date)
        if isinstance(policy, PolicyReplayAdapter) and (policy.inputs or policy.decisions):
            raise ValueError('POLICY_REPLAY_ADAPTER_ALREADY_USED')
        if isinstance(policy, PolicyReplayAdapter) and request.policy_version != policy.engine.policy.version:
            raise UnavailableError('BACKTEST_POLICY_VERSION_MISMATCH', 'backtest')
        execution_policy = policy.engine.risk.policy if isinstance(policy, PolicyReplayAdapter) else (request.execution_risk_policy or RiskPolicy())
        if (isinstance(policy, PolicyReplayAdapter) and request.execution_risk_policy is not None
                and request.execution_risk_policy != execution_policy):
            raise UnavailableError('BACKTEST_EXECUTION_RISK_POLICY_MISMATCH', 'backtest')
        expected_digest = digest([bar.model_dump(mode='json') for bar in bars])
        if expected_digest != request.data_version:
            raise UnavailableError('BACKTEST_DATA_HASH_MISMATCH', 'backtest')
        if request.universe_version != self.tradability.security_master.version:
            raise UnavailableError('BACKTEST_SECURITY_VERSION_MISMATCH', 'backtest')
        by_key = {}
        for bar in bars:
            bar.require_raw_price()
            day = bar.timestamp.astimezone(SH).date()
            key = (day, bar.instrument_id)
            if key in by_key:
                raise ValueError('duplicate session/instrument bars')
            by_key[key] = bar
        sessions = [s for s in self.calendar.sessions if request.start_date <= s <= request.end_date]
        if not sessions:
            raise UnavailableError('BACKTEST_CALENDAR_EMPTY', 'backtest')
        cash = money(request.initial_portfolio.cash)
        positions = {h.instrument_id: h.shares for h in request.initial_portfolio.holdings if h.shares}
        acquisition = {h.instrument_id: [(request.start_date, h.shares - h.sellable_shares),
                                       (date.min, h.sellable_shares)] for h in request.initial_portfolio.holdings}
        marks = {h.instrument_id: h.mark_price for h in request.initial_portfolio.holdings}
        industries = {h.instrument_id: h.industry for h in request.initial_portfolio.holdings}
        receivables = []
        initial_nav = money(request.initial_portfolio.nav)
        if initial_nav <= 0:
            raise ValueError('positive initial NAV required')
        previous_nav = initial_nav
        peak = initial_nav
        pending, orders, daily = None, [], []
        total_fees = Decimal(0)
        benchmark_base = None
        benchmark_units = 1.
        account = request.initial_portfolio
        for day in sessions:
            execution_time = datetime.combine(day, time(9, 30), SH)
            close_time = datetime.combine(day, time(15, 1), SH)
            session_fees, session_gross = Decimal(0), Decimal(0)
            # Explicit corporate-action coverage is required. Cash dividends are
            # receivable from ex-date and cash only from the declared pay date.
            for action in request.corporate_actions:
                if action.ex_date != day or action.instrument_id not in positions:
                    continue
                if action.available_at > execution_time:
                    raise UnavailableError('CORPORATE_ACTION_NOT_AVAILABLE', 'backtest')
                identity = action.instrument_id
                before = positions[identity]
                split = Decimal(before) * Decimal(str(action.split_ratio))
                if split != split.to_integral_value():
                    raise UnavailableError('FRACTIONAL_CORPORATE_ACTION_UNSUPPORTED', 'backtest')
                positions[identity] = int(split)
                acquisition[identity] = [(d, int(Decimal(q) * Decimal(str(action.split_ratio))))
                                         for d, q in acquisition[identity]]
                receivables.append((action.pay_date, money(Decimal(before) * Decimal(str(action.net_cash_dividend_per_share)))))
                if pending and identity in pending['target']:
                    pending['target'][identity] = int(Decimal(pending['target'][identity]) * Decimal(str(action.split_ratio)))
            for pay_date, amount in tuple(receivables):
                if pay_date <= day:
                    cash += amount
                    receivables.remove((pay_date, amount))
            if pending and pending['earliest'] <= execution_time:
                target = pending['target']
                for side in ('SELL', 'BUY'):
                    for identity in sorted(set(positions) | set(target)):
                        delta = target.get(identity, 0) - positions.get(identity, 0)
                        if not delta or (delta > 0) != (side == 'BUY'):
                            continue
                        requested = abs(delta)
                        bar = by_key.get((day, identity))
                        reasons, quantity, price, fee = [], 0, None, Decimal(0)
                        if bar is None:
                            reasons = ['EXECUTION_BAR_UNAVAILABLE']
                        else:
                            raw_price = float(bar.open)
                            status = self.tradability.evaluate_execution(identity, execution_time, side, raw_price)
                            if status.decision != 'ACCEPT':
                                reasons = list(status.reason_codes)
                            else:
                                lot = status.lot_size
                                industries[identity] = status.industry
                                quantity = requested
                                if side == 'SELL':
                                    sellable = sum(q for d, q in acquisition.get(identity, []) if d < day)
                                    quantity = min(quantity, sellable)
                                    if quantity < requested:
                                        reasons.append('T_PLUS_ONE_OR_SELLABLE_LIMIT')
                                    if target.get(identity, 0) != 0:
                                        quantity = quantity // lot * lot
                                else:
                                    quantity = quantity // lot * lot
                                    state = self.tradability.security_master.resolve(identity, day, execution_time)
                                    minimum_buy = state.min_buy_shares or lot
                                    if quantity < minimum_buy:
                                        quantity = 0
                                        reasons.append('MINIMUM_BUY_QUANTITY')
                                capacity = int(pending['volumes'].get(identity, 0) * request.max_prior_volume_participation)
                                if quantity > capacity:
                                    quantity = capacity // lot * lot
                                    reasons.append('PRIOR_VOLUME_CAPACITY_LIMIT')
                                slipped = raw_price * (1 + (1 if side == 'BUY' else -1) * request.fees.slippage_bps / 10000)
                                # The slipped execution price must itself satisfy exchange limits.
                                slipped_status = self.tradability.evaluate_execution(identity, execution_time, side, slipped)
                                if slipped_status.decision != 'ACCEPT':
                                    quantity = 0
                                    reasons.extend(slipped_status.reason_codes)
                                price = float(Decimal(str(slipped)).quantize(Decimal('.0001'), rounding=ROUND_HALF_UP))
                                if side == 'BUY':
                                    def affordable(q):
                                        return money(Decimal(q) * Decimal(str(price))) + request.fees.cost(side, q, price) <= cash
                                    if not affordable(quantity):
                                        low, high = 0, quantity // lot
                                        while low < high:
                                            middle = (low + high + 1) // 2
                                            if affordable(middle * lot):
                                                low = middle
                                            else:
                                                high = middle - 1
                                        quantity = low * lot
                                        reasons.append('INSUFFICIENT_CASH')
                                    if quantity < minimum_buy:
                                        quantity = 0
                                    if quantity:
                                        quantity, exposure_reasons = self._limit_buy(identity=identity, quantity=quantity,
                                            lot=lot, price=price, raw_open=raw_price, cash=cash, positions=positions,
                                            receivables=sum((amount for _, amount in receivables), Decimal(0)),
                                            by_key=by_key, day=day, execution_time=execution_time, fees=request.fees,
                                            risk_policy=execution_policy)
                                        reasons.extend(exposure_reasons)
                                        if quantity < minimum_buy:
                                            quantity = 0
                                if quantity:
                                    fee = request.fees.cost(side, quantity, price)
                                    gross = money(Decimal(quantity) * Decimal(str(price)))
                                    if side == 'BUY':
                                        cash -= gross + fee
                                        positions[identity] = positions.get(identity, 0) + quantity
                                        acquisition.setdefault(identity, []).append((day, quantity))
                                    else:
                                        cash += gross - fee
                                        positions[identity] -= quantity
                                        remaining = quantity
                                        lots = []
                                        for bought, amount in acquisition[identity]:
                                            sold = min(amount, remaining) if bought < day else 0
                                            remaining -= sold
                                            if amount - sold:
                                                lots.append((bought, amount - sold))
                                        acquisition[identity] = lots
                                        if not positions[identity]:
                                            del positions[identity]
                                    session_fees += fee
                                    session_gross += gross
                                else:
                                    reasons.append('NO_EXECUTABLE_QUANTITY')
                        orders.append(Order(order_id=f'{run_id}-{len(orders)+1}', instrument_id=identity, side=side,
                            quantity=requested, decision_time=pending['decision'], data_available_time=pending['available'],
                            earliest_trade_time=pending['earliest'], execution_time=execution_time,
                            filled_quantity=quantity, execution_price=price if quantity else None, fee=float(fee),
                            status='FILLED' if quantity == requested else 'PARTIAL' if quantity else 'REJECTED',
                            reason_codes=tuple(reasons or ['EXECUTED'])))
                pending = None
            total_fees += session_fees
            if cash < 0:
                raise AssertionError('negative cash after fills')
            for identity in positions:
                bar = by_key.get((day, identity))
                if bar is None:
                    raise UnavailableError('HELD_SECURITY_MARK_UNAVAILABLE', 'backtest')
                bar.assert_visible(close_time)
                marks[identity] = bar.require_raw_price()
            receivable_total = sum((amount for _, amount in receivables), Decimal(0))
            nav = money(cash + receivable_total + sum(Decimal(q) * Decimal(str(marks[i])) for i, q in positions.items()))
            peak = max(peak, nav)
            benchmark_return = None
            if request.benchmark_id:
                benchmark = by_key.get((day, request.benchmark_id))
                if benchmark is None:
                    raise UnavailableError('BENCHMARK_UNAVAILABLE', 'backtest')
                benchmark.assert_visible(close_time)
                if benchmark_base is None:
                    benchmark_base = benchmark.close
                else:
                    for action in request.corporate_actions:
                        if action.instrument_id == request.benchmark_id and action.ex_date == day:
                            if action.available_at > close_time:
                                raise UnavailableError('BENCHMARK_ACTION_NOT_AVAILABLE', 'backtest')
                            benchmark_units *= action.split_ratio
                # A split changes units, never benchmark wealth. Cash dividends
                # remain excluded because the published benchmark is price return.
                benchmark_return = benchmark.close * benchmark_units / benchmark_base - 1
            cumulative = float(nav / initial_nav - 1)
            daily.append(DailyAccount(date=day, cash=float(cash), receivables=float(receivable_total), positions=positions.copy(),
                nav=float(nav), daily_return=float(nav / previous_nav - 1), cumulative_return=cumulative,
                benchmark_return=benchmark_return, excess_return=cumulative - benchmark_return if benchmark_return is not None else None,
                drawdown=float(nav / peak - 1), turnover=float(session_gross / previous_nav), fees=float(session_fees)))
            previous_nav = nav
            account = PortfolioSnapshot(as_of=close_time, cash=float(cash), holdings=tuple(
                Holding(instrument_id=i, shares=q, sellable_shares=sum(n for d, n in acquisition[i] if d < day),
                        mark_price=marks[i], industry=industries[i], marked_at=datetime.combine(day, time(15), SH))
                for i, q in sorted(positions.items())))
            visible = tuple(sorted((b for b in bars if b.available_at <= close_time and b.timestamp <= close_time),
                                   key=lambda b: (b.timestamp, b.instrument_id)))
            weights = policy(close_time, visible, account)
            if weights is None:
                continue
            if any(not math.isfinite(float(w)) or w < 0 or w > 1 for w in weights.values()) or sum(weights.values()) > 1 + 1e-9:
                raise ValueError('invalid target weights')
            # At the last session, keep the final decision as unexecuted, not a same-bar fill.
            if day == sessions[-1]:
                continue
            latest = {}
            for bar in visible:
                latest[bar.instrument_id] = bar
            targets, volumes = {}, {}
            for identity, weight in sorted(weights.items()):
                if weight <= 0:
                    continue
                if identity not in latest:
                    raise UnavailableError('TARGET_PRICE_UNAVAILABLE', 'backtest')
                mark = latest[identity].require_raw_price()
                state = self.tradability.security_master.resolve(identity, day, close_time)
                if not state.lot_size:
                    raise UnavailableError('LOT_SIZE_UNAVAILABLE', 'backtest')
                shares = Decimal(str(nav)) * Decimal(str(weight)) / Decimal(str(mark))
                targets[identity] = int(shares + Decimal('0.00000001')) // state.lot_size * state.lot_size
            for identity in set(positions) | set(targets):
                volumes[identity] = latest[identity].volume if identity in latest else 0.
            pending = {'decision': close_time, 'available': max((b.available_at for b in visible), default=close_time),
                       'earliest': self.calendar.earliest_trade_at(close_time), 'target': targets, 'volumes': volumes}
        last = daily[-1]
        return BacktestResult(run_id=run_id, policy_version=request.policy_version, data_version=request.data_version,
            universe_version=request.universe_version, calendar_version=self.calendar.version,
            security_version=self.tradability.security_master.version, fee_version=request.fees.version,
            policy_adapter='CANONICAL_RECOMMENDATION' if isinstance(policy, PolicyReplayAdapter) else 'CALLBACK_RESEARCH_ONLY',
            policy_hash=digest(policy.engine.policy) if isinstance(policy, PolicyReplayAdapter) else None,
            risk_policy_hash=policy.engine.risk.policy.policy_hash if isinstance(policy, PolicyReplayAdapter) else None,
            execution_risk_policy_hash=execution_policy.policy_hash,
            orders=tuple(orders), daily=tuple(daily), final_portfolio=account, final_receivables=last.receivables,
            total_return=last.cumulative_return,
            benchmark_return=last.benchmark_return, excess_return=last.excess_return,
            max_drawdown=min(d.drawdown for d in daily), total_fees=float(total_fees),
            artifact_path=str(directory.resolve()) if directory else None)
