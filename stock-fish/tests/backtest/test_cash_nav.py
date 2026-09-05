from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from stockfish_core.backtest import BacktestEngine, BacktestRequest, CorporateAction, FeeSchedule
from stockfish_core.common import UnavailableError, digest
from stockfish_core.data import CanonicalMarketBar, SecurityMaster, SecurityState, TradingCalendar, TradabilityService
from stockfish_core.risk import Holding, PortfolioSnapshot, RiskPolicy

SH = ZoneInfo('Asia/Shanghai')
DAYS = (date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 8))
ID = '600000.SH'
def ts(day, hour=15, minute=0):
    return datetime.combine(day, time(hour, minute), SH)


def setup_case(*, prices=(10., 10., 10.), opens=None, state_changes=None, initial=None, actions=(), days=DAYS):
    calendar = TradingCalendar(days)
    states = tuple(SecurityState(instrument_id=ID, effective_from=day,
        effective_to=day + timedelta(days=1), available_at=ts(day, 9), provider='fixture-explicit', verified=True,
        listing_date=days[0], board='MAIN', is_st=False, suspended=False, lot_size=100, min_buy_shares=100,
        industry='bank', limits_date=day, limit_up=100., limit_down=1., no_price_limit=False,
        **{}) for day in days)
    if state_changes:
        states = tuple(state.model_copy(update=state_changes.get(state.effective_from, {})) for state in states)
    master = SecurityMaster(states)
    bars = tuple(CanonicalMarketBar(instrument_id=ID, exchange='SH', symbol='600000', timestamp=ts(day),
        open=(opens or prices)[i], high=max(prices[i], (opens or prices)[i]), low=min(prices[i], (opens or prices)[i]),
        close=prices[i], volume=100000., turnover=100000. * prices[i], currency='CNY', price_adjustment='RAW',
        provider='fixture-explicit', source_timestamp=ts(day), available_at=ts(day), quality='VERIFIED',
        snapshot_id=f'fixture-{day}') for i, day in enumerate(days))
    request = BacktestRequest(start_date=days[0], end_date=days[-1],
        initial_portfolio=initial or PortfolioSnapshot(as_of=ts(days[0], 8), cash=10000), policy_version='same-live-policy-v1',
        data_version=digest([b.model_dump(mode='json') for b in bars]), universe_version=master.version,
        benchmark_id=ID, corporate_actions_complete=True, corporate_actions=actions,
        execution_risk_policy=RiskPolicy(max_stock_weight=1., max_industry_weight=1.))
    return BacktestEngine(calendar, TradabilityService(calendar, master)), request, bars


def test_golden_buy_sell_fees_empty_target_and_next_open(tmp_path):
    engine, request, bars = setup_case()
    engine.root = tmp_path
    observed = []
    def policy(at, visible, account):
        observed.append((at, visible, account))
        return {ID: .5} if at.date() == DAYS[0] else {}
    result = engine.run(request, bars, policy)
    assert [(o.side, o.filled_quantity, o.fee) for o in result.orders] == [('BUY', 500, 5), ('SELL', 500, 7.5)]
    assert [d.nav for d in result.daily] == [10000, 9995, 9987.5]
    assert result.final_portfolio.cash == 9987.5
    assert result.final_portfolio.holdings == ()
    assert result.total_fees == 12.5
    assert result.total_return == -.00125
    assert result.benchmark_return == 0
    assert result.max_drawdown == -.00125
    for order in result.orders:
        assert order.data_available_time <= order.decision_time < order.earliest_trade_time <= order.execution_time
        assert order.execution_time.time() == time(9, 30)
    assert all(bar.available_at <= at and bar.timestamp <= at for at, seen, _ in observed for bar in seen)
    assert observed[1][2].holdings[0].sellable_shares == 0  # purchased today, T+1 lock
    assert len(observed[0][1]) == 1


def test_future_bar_change_cannot_change_earlier_decision_or_requested_quantity():
    a, ar, ab = setup_case()
    b, br, bb = setup_case(prices=(10., 20., 20.), opens=(10., 10., 20.))
    seen_a, seen_b = [], []
    def callback(seen):
        def policy(at, bars, account):
            seen.append((at, tuple((x.timestamp, x.close) for x in bars), account.model_dump()))
            return {ID: .5} if at.date() == DAYS[0] else None
        return policy
    ra, rb = a.run(ar, ab, callback(seen_a)), b.run(br, bb, callback(seen_b))
    assert seen_a[0] == seen_b[0]
    assert ra.orders[0].quantity == rb.orders[0].quantity == 500
    assert ra.orders[0].execution_price == rb.orders[0].execution_price == 10


@pytest.mark.parametrize('change,reason', [({'suspended': True}, 'SUSPENDED'),
    ({'limit_up': 10.}, 'LIMIT_UP'), ({'available_at': ts(DAYS[1], 12)}, 'SECURITY_STATE_UNAVAILABLE')])
def test_execution_state_fails_closed(change, reason):
    engine, request, bars = setup_case(state_changes={DAYS[1]: change})
    result = engine.run(request, bars, lambda at, *_: {ID: .5} if at.date() == DAYS[0] else None)
    assert result.orders[0].filled_quantity == 0
    assert reason in result.orders[0].reason_codes
    assert result.final_portfolio.cash == 10000


def test_budget_includes_minimum_fee_and_minimum_buy():
    engine, request, bars = setup_case()
    result = engine.run(request, bars, lambda at, *_: {ID: 1.} if at.date() == DAYS[0] else None)
    assert result.orders[0].filled_quantity == 900
    assert result.final_portfolio.cash == 995
    assert 'INSUFFICIENT_CASH' in result.orders[0].reason_codes
    engine, request, bars = setup_case(state_changes={DAYS[1]: {'min_buy_shares': 600}})
    result = engine.run(request, bars, lambda at, *_: {ID: .5} if at.date() == DAYS[0] else None)
    assert result.orders[0].filled_quantity == 0
    assert 'MINIMUM_BUY_QUANTITY' in result.orders[0].reason_codes


def test_raw_prices_and_corporate_action_coverage_required():
    engine, request, bars = setup_case()
    altered = tuple(b.model_copy(update={'price_adjustment': 'QFQ'}) for b in bars)
    req = request.model_copy(update={'data_version': digest([b.model_dump(mode='json') for b in altered])})
    with pytest.raises(UnavailableError, match='RAW_PRICE_UNAVAILABLE'):
        engine.run(req, altered, lambda *_: {})
    with pytest.raises(ValueError, match='CORPORATE_ACTION_COVERAGE_UNAVAILABLE'):
        BacktestRequest(**(request.model_dump() | {'corporate_actions_complete': False}))


def test_split_and_dividend_nav_are_not_false_price_losses():
    holding = Holding(instrument_id=ID, shares=100, sellable_shares=100, mark_price=10,
        industry='bank', marked_at=ts(DAYS[0], 8))
    initial = PortfolioSnapshot(as_of=ts(DAYS[0], 8), cash=0, holdings=(holding,))
    action = CorporateAction(instrument_id=ID, ex_date=DAYS[1], pay_date=DAYS[2],
        available_at=ts(DAYS[0]), split_ratio=2., net_cash_dividend_per_share=1., source_id='fixture-action')
    engine, request, bars = setup_case(prices=(10., 4.5, 4.5), initial=initial, actions=(action,))
    result = engine.run(request, bars, lambda *_: None)
    assert [d.nav for d in result.daily] == [1000., 1000., 1000.]
    assert [d.receivables for d in result.daily] == [0., 100., 0.]
    assert result.final_portfolio.cash == 100.
    assert result.final_portfolio.holdings[0].shares == 200
    assert result.total_return == 0.
    assert result.benchmark_return == pytest.approx(-.1)  # excludes dividend, never treats 2:1 split as -50%


def test_future_publication_is_not_visible_to_policy_and_unknown_marks_fail():
    engine, request, bars = setup_case()
    delayed = bars[0].model_copy(update={'available_at': ts(DAYS[2])})
    changed = (delayed, *bars[1:])
    request = request.model_copy(update={'data_version': digest([b.model_dump(mode='json') for b in changed]), 'benchmark_id': None})
    seen = []
    engine.run(request, changed, lambda at, visible, account: seen.append(visible))
    assert seen[0] == ()
    holding = Holding(instrument_id=ID, shares=100, sellable_shares=100, mark_price=10,
        industry='bank', marked_at=ts(DAYS[0], 8))
    request = request.model_copy(update={'initial_portfolio': PortfolioSnapshot(as_of=ts(DAYS[0], 8), cash=9000, holdings=(holding,))})
    with pytest.raises(UnavailableError, match='DATA_NOT_YET_AVAILABLE'):
        engine.run(request, changed, lambda *_: None)


def test_action_unknown_at_ex_open_fails_instead_of_fabricating_adjustment():
    holding = Holding(instrument_id=ID, shares=100, sellable_shares=100, mark_price=10,
        industry='bank', marked_at=ts(DAYS[0], 8))
    action = CorporateAction(instrument_id=ID, ex_date=DAYS[1], pay_date=DAYS[2],
        available_at=ts(DAYS[1], 10), split_ratio=2., source_id='fixture-late')
    engine, request, bars = setup_case(initial=PortfolioSnapshot(as_of=ts(DAYS[0], 8), cash=0, holdings=(holding,)), actions=(action,))
    with pytest.raises(UnavailableError, match='CORPORATE_ACTION_NOT_AVAILABLE'):
        engine.run(request, bars, lambda *_: None)


def test_partial_fill_remainder_is_cancelled_and_none_does_not_resubmit():
    engine, request, bars = setup_case()
    request = request.model_copy(update={'max_prior_volume_participation': .001})
    result = engine.run(request, bars, lambda at, *_: {ID: .5} if at.date() == DAYS[0] else None)
    assert len(result.orders) == 1
    assert result.orders[0].quantity == 500
    assert result.orders[0].filled_quantity == 100
    assert result.orders[0].status == 'PARTIAL'
    assert result.orders[0].reason_codes == ('PRIOR_VOLUME_CAPACITY_LIMIT',)
    assert result.final_portfolio.holdings[0].shares == 100


def test_same_recommendation_policy_is_executed_and_persisted(tmp_path):
    from stockfish_core.backtest import PolicyReplayAdapter, PolicyReplayInput
    from stockfish_core.recommendation import Candidate, RecommendationContext, RecommendationEngine
    from stockfish_core.risk import RiskEngine, RiskInput, RiskPolicy
    live = RecommendationEngine(risk=RiskEngine(RiskPolicy(max_stock_weight=.2, max_industry_weight=.4)))
    four_days = (*DAYS, DAYS[-1] + timedelta(days=1))
    simulator, request, bars = setup_case(prices=(10.,) * 4, days=four_days)
    request = request.model_copy(update={'policy_version': live.policy.version, 'execution_risk_policy': None})
    simulator.root = tmp_path
    comparisons = []
    def build(at, visible, portfolio):
        latest = visible[-1]
        context = RecommendationContext(run_id='replay-' + at.date().isoformat(), as_of=latest.timestamp,
            decision_time=at, earliest_trade_at=datetime.combine(at.date() + timedelta(days=1), time(9, 30), SH),
            model_id='explicit-fixture', model_version='v1', checkpoint_hash='fixture', feature_schema_hash='fixture',
            data_snapshot_id=latest.snapshot_id, data_version=request.data_version, universe_version=request.universe_version,
            quant_quality='VALID', model_validation='CLEAN_SPLIT', mode='REPLAY')
        candidates = (Candidate(instrument_id=ID, raw_score=.4, quant_rank=1),) if at.date() == DAYS[0] else ()
        entry = simulator.tradability.evaluate(ID, at, at.date(), 'BUY', latest)
        exit_status = simulator.tradability.evaluate(ID, at, at.date(), 'SELL', latest)
        inputs = {ID: RiskInput(instrument_id=ID, decision_time=at, data_available_at=latest.available_at,
            price_timestamp=latest.timestamp, reference_price=latest.close, price_adjustment='RAW', quality='VERIFIED',
            provider='fixture-explicit', industry='bank', tradability=entry.decision,
            exit_tradability=exit_status.decision, exit_reasons=exit_status.reason_codes,
            lot_size=100, min_buy_shares=100)}
        result = PolicyReplayInput(context=context, candidates=candidates, risk_inputs=inputs)
        comparisons.append(live.evaluate(context, candidates, inputs, portfolio))
        return result
    replay = PolicyReplayAdapter(live, build)
    result = simulator.run(request, bars, replay)
    assert [r.model_dump() for r in replay.decisions] == [r.model_dump() for r in comparisons]
    assert replay.decisions[0].items[0].target_shares == 200
    assert result.orders[0].filled_quantity == 100  # post-fee NAV cap, rounded to legal 100-share lot
    assert 'EXECUTION_STOCK_EXPOSURE_LIMIT' in result.orders[0].reason_codes
    assert replay.decisions[1].status == 'UNAVAILABLE'  # actual day-of-buy shares remain T+1 locked
    assert result.orders[1].side == 'SELL'
    assert result.orders[1].execution_time.date() == four_days[3]
    assert result.final_portfolio.holdings == ()
    assert (tmp_path / result.run_id / 'policy_decisions.json').is_file()
