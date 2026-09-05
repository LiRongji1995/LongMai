from datetime import timedelta
from decimal import Decimal

import pytest

from stockfish_core.backtest import BacktestEngine, PolicyReplayAdapter, PolicyReplayInput
from stockfish_core.common import digest
from stockfish_core.data import SecurityMaster, TradabilityService
from stockfish_core.recommendation import Candidate, RecommendationContext, RecommendationEngine
from stockfish_core.risk import Holding, PortfolioSnapshot, RiskEngine, RiskInput, RiskPolicy
from tests.backtest.test_cash_nav import DAYS, ID, setup_case, ts

B = '600001.SH'


def live_adapter(simulator, request, ids, risk):
    live = RecommendationEngine(risk=RiskEngine(risk))
    def build(at, visible, account):
        latest = {bar.instrument_id: bar for bar in visible}
        context = RecommendationContext(run_id='execution-fixture-' + at.date().isoformat(),
            as_of=max(b.timestamp for b in latest.values()), decision_time=at, earliest_trade_at=at + timedelta(days=1),
            model_id='explicit-fixture', model_version='v1', checkpoint_hash='fixture', feature_schema_hash='fixture',
            data_snapshot_id='fixture', data_version=request.data_version, universe_version=request.universe_version,
            quant_quality='VALID', model_validation='CLEAN_SPLIT', mode='REPLAY')
        risk_inputs = {}
        for identity, bar in latest.items():
            entry = simulator.tradability.evaluate(identity, at, at.date(), 'BUY', bar)
            exit_ = simulator.tradability.evaluate(identity, at, at.date(), 'SELL', bar)
            risk_inputs[identity] = RiskInput(instrument_id=identity, decision_time=at, data_available_at=bar.available_at,
                price_timestamp=bar.timestamp, reference_price=bar.close, price_adjustment='RAW', quality='VERIFIED',
                provider='fixture', industry=entry.industry, tradability=entry.decision, exit_tradability=exit_.decision,
                exit_reasons=exit_.reason_codes, lot_size=entry.lot_size, min_buy_shares=entry.min_buy_shares)
        return PolicyReplayInput(context=context, candidates=tuple(Candidate(instrument_id=i, raw_score=float(len(ids)-n),
            quant_rank=n+1) for n, i in enumerate(ids)), risk_inputs=risk_inputs)
    return PolicyReplayAdapter(live, build)


def test_real_policy_gap_is_limited_at_raw_open_after_fees():
    initial = PortfolioSnapshot(as_of=ts(DAYS[0], 8), cash=100000.)
    sim, request, bars = setup_case(prices=(10., 10.5, 10.5), initial=initial)
    policy = RiskPolicy()
    request = request.model_copy(update={'policy_version': 'quant-hard-risk-v2', 'execution_risk_policy': None})
    replay = live_adapter(sim, request, (ID,), policy)
    result = sim.run(request, bars, replay)
    order = result.orders[0]
    assert replay.decisions[0].items[0].target_shares == 2000
    assert order.filled_quantity == 1900
    assert 'EXECUTION_STOCK_EXPOSURE_LIMIT' in order.reason_codes
    assert order.execution_price == 10.5
    assert order.fee == 9.98
    assert 1900 * 10.5 / result.daily[1].nav <= policy.max_stock_weight
    assert result.execution_risk_policy_hash == policy.policy_hash


def industry_case():
    holding = Holding(instrument_id=B, shares=1000, sellable_shares=1000, mark_price=10., industry='bank', marked_at=ts(DAYS[0], 8))
    initial = PortfolioSnapshot(as_of=ts(DAYS[0], 8), cash=90000., holdings=(holding,))
    sim, request, bars = setup_case(prices=(10., 10.5, 10.5), initial=initial)
    extra = tuple(bar.model_copy(update={'instrument_id': B, 'symbol': B[:6], 'open': 10. if n == 0 else 10.8,
        'close': 10. if n == 0 else 10.8, 'high': 10. if n == 0 else 10.8, 'low': 10. if n == 0 else 10.8})
        for n, bar in enumerate(bars))
    master = SecurityMaster((*sim.tradability.security_master.states,
        *(s.model_copy(update={'instrument_id': B}) for s in sim.tradability.security_master.states)))
    sim = BacktestEngine(sim.calendar, TradabilityService(sim.calendar, master))
    all_bars = bars + extra
    request = request.model_copy(update={'data_version': digest([b.model_dump(mode='json') for b in all_bars]),
        'universe_version': master.version, 'policy_version': 'quant-hard-risk-v2', 'execution_risk_policy': None})
    return sim, request, all_bars


def test_execution_industry_cap_uses_all_existing_open_marks():
    sim, request, bars = industry_case()
    policy = RiskPolicy(max_industry_weight=.3)
    replay = live_adapter(sim, request, (ID, B), policy)
    result = sim.run(request, bars, replay)
    assert replay.decisions[0].items[0].target_shares == 2000
    buy = next(o for o in result.orders if o.side == 'BUY')
    assert buy.filled_quantity == 1800
    assert 'EXECUTION_INDUSTRY_EXPOSURE_LIMIT' in buy.reason_codes
    assert (1800 * 10.5 + 1000 * 10.8) / result.daily[1].nav <= .3


def test_execution_limit_does_not_read_future_close_or_current_day_volume():
    a, request, bars = industry_case()
    risk = RiskPolicy(max_industry_weight=.3)
    first = a.run(request, bars, live_adapter(a, request, (ID, B), risk))
    altered = tuple(b.model_copy(update={'close': b.close + .05, 'high': b.high + .05, 'volume': 1e9})
                    if b.timestamp.date() == DAYS[1] else b for b in bars)
    other_request = request.model_copy(update={'data_version': digest([b.model_dump(mode='json') for b in altered])})
    second = a.run(other_request, altered, live_adapter(a, other_request, (ID, B), risk))
    assert (first.orders[0].filled_quantity, first.orders[0].execution_price) == (second.orders[0].filled_quantity, second.orders[0].execution_price)


def test_execution_rechecks_max_positions():
    sim, request, bars = industry_case()
    strict = RiskPolicy(max_positions=1, max_stock_weight=.2, max_industry_weight=.3)
    request = request.model_copy(update={'execution_risk_policy': strict})
    result = sim.run(request, bars, lambda at, *_: {B: .1, ID: .2} if at.date() == DAYS[0] else None)
    assert result.orders[0].filled_quantity == 0
    assert result.orders[0].reason_codes == ('EXECUTION_MAX_POSITIONS', 'NO_EXECUTABLE_QUANTITY')
    assert not any(o.side == 'SELL' for o in result.orders)
    assert result.final_portfolio.holdings[0].shares == 1000


def test_gap_drifted_existing_holding_is_not_forced_sold_and_cannot_receive_more():
    sim, request, bars = industry_case()
    old = request.initial_portfolio.holdings[0].model_copy(update={'shares': 1900, 'sellable_shares': 1900})
    initial = request.initial_portfolio.model_copy(update={'cash': 81000., 'holdings': (old,)})
    request = request.model_copy(update={'initial_portfolio': initial, 'end_date': DAYS[1]})
    replay = live_adapter(sim, request, (B,), RiskPolicy())
    result = sim.run(request, bars, replay)
    assert replay.decisions[0].items[0].target_shares == 2000
    assert result.orders[0].quantity == 100
    assert result.orders[0].filled_quantity == 0
    assert 'EXECUTION_STOCK_EXPOSURE_LIMIT' in result.orders[0].reason_codes
    assert not any(o.side == 'SELL' for o in result.orders)
    assert result.final_portfolio.holdings[0].shares == 1900


def test_missing_held_open_mark_cannot_authorize_any_buy():
    sim, request, bars = industry_case()
    quantity, reasons = sim._limit_buy(identity=ID, quantity=2000, lot=100, price=10.5, raw_open=10.5,
        cash=Decimal('90000'), positions={B: 1000}, receivables=Decimal(0), by_key={}, day=DAYS[1],
        execution_time=ts(DAYS[1], 9, 30), fees=request.fees, risk_policy=RiskPolicy())
    assert quantity == 0
    assert reasons == ['EXECUTION_PORTFOLIO_MARK_UNAVAILABLE']
