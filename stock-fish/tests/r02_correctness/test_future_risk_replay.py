"""Paired future mutations preserve old decisions and next-open paper orders."""
from datetime import timedelta

import pytest

from stockfish_core.backtest import PolicyReplayAdapter, PolicyReplayInput
from stockfish_core.common import digest
from stockfish_core.data import SecurityMaster, TradabilityService
from stockfish_core.recommendation import Candidate, RecommendationContext, RecommendationEngine
from stockfish_core.risk import RiskInput
from tests.backtest.test_cash_nav import setup_case, DAYS, ts


def replay(veto):
    engine, request, bars = setup_case()
    second = '600001.SH'
    states = engine.tradability.security_master.states
    expanded = tuple(states) + tuple(s.model_copy(update={'instrument_id': second, 'industry': second}) for s in states)
    master = SecurityMaster(expanded)
    engine.tradability = TradabilityService(engine.calendar, master)
    bars = bars + tuple(b.model_copy(update={'instrument_id': second, 'symbol': '600001'}) for b in bars)
    policy_engine = RecommendationEngine()
    def builder(at, visible, account):
        latest = {bar.instrument_id: bar for bar in visible}
        context = RecommendationContext(run_id='TEST_FIXTURE', as_of=at, decision_time=at,
            earliest_trade_at=at + timedelta(days=1), model_id='fixture', model_version='fixture',
            checkpoint_hash='fixture', feature_schema_hash='fixture', data_snapshot_id='fixture',
            data_version='fixture', universe_version='fixture', quant_quality='VALID',
            model_validation='CLEAN_SPLIT', mode='REPLAY')
        candidates = tuple(Candidate(instrument_id=i, raw_score=2-n, quant_rank=n+1)
                           for n, i in enumerate(sorted(latest)))
        inputs = {}
        for n, candidate in enumerate(candidates):
            mark = latest[candidate.instrument_id]
            inputs[candidate.instrument_id] = RiskInput(instrument_id=candidate.instrument_id, decision_time=at,
                data_available_at=at + timedelta(days=1) if n == 0 else mark.available_at,
                price_timestamp=mark.timestamp, reference_price=10, price_adjustment='RAW',
                quality='VERIFIED', provider='TEST_FIXTURE', industry=candidate.instrument_id,
                tradability='ACCEPT', exit_tradability='ACCEPT', lot_size=100, min_buy_shares=100,
                hard_veto=veto if n == 0 else False, veto_reasons=('LATE_NOTICE',) if veto and n == 0 else ())
        return PolicyReplayInput(context=context, candidates=candidates, risk_inputs=inputs)
    adapter = PolicyReplayAdapter(policy_engine, builder)
    request = request.model_copy(update={'policy_version': policy_engine.policy.version,
        'execution_risk_policy': None, 'data_version': digest([b.model_dump(mode='json') for b in bars]),
        'universe_version': master.version})
    result = engine.run(request, bars, adapter)
    return adapter.decisions, result.orders


def test_future_veto_pair_preserves_every_old_decision_and_paper_order():
    decisions_a, orders_a = replay(False)
    decisions_b, orders_b = replay(True)
    assert decisions_a == decisions_b
    assert orders_a == orders_b == ()
    assert all(item.status == 'UNAVAILABLE' for item in decisions_a)


@pytest.mark.parametrize('mutation', ['identity', 'nested_time'])
def test_outer_authoritative_identity_and_time_cannot_be_self_reported(mutation):
    from tests.recommendation.test_canonical_policy import context, risk, NOW
    from stockfish_core.risk import PortfolioSnapshot
    engine = RecommendationEngine()
    candidate = Candidate(instrument_id='600000.SH', raw_score=1, quant_rank=1)
    value = risk(candidate.instrument_id).model_copy(update={
        'instrument_id': '600001.SH'} if mutation == 'identity' else {'decision_time': NOW + timedelta(days=1)})
    for hard_veto in (False, True):
        with pytest.raises(ValueError, match='identity or decision time mismatch'):
            engine.evaluate(context(), (candidate,), {candidate.instrument_id: value.model_copy(update={'hard_veto': hard_veto})},
                            PortfolioSnapshot(as_of=NOW, cash=100000))
