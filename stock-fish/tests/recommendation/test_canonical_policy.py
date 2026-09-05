from datetime import datetime, timedelta, timezone

import pytest

from stockfish_core.recommendation import Candidate, RecommendationContext, RecommendationEngine
from stockfish_core.risk import PortfolioSnapshot, RiskInput

NOW = datetime(2026, 9, 4, 8, tzinfo=timezone.utc)


def context(**changes):
    return RecommendationContext(**(dict(run_id="quant-fixture", as_of=NOW, decision_time=NOW,
        earliest_trade_at=NOW + timedelta(days=3), model_id="test", model_version="v1", checkpoint_hash="abc",
        feature_schema_hash="def", data_snapshot_id="fixture", data_version="v1", universe_version="v1",
        quant_quality="VALID", model_validation="RESEARCH_ONLY", mode="REPLAY") | changes))


def risk(iid):
    return RiskInput(instrument_id=iid, decision_time=NOW, data_available_at=NOW, price_timestamp=NOW,
        reference_price=10, price_adjustment="RAW", quality="VERIFIED", provider="fixture", industry=iid,
        tradability="ACCEPT", lot_size=100, min_buy_shares=100)


def test_same_input_replays_exactly_and_every_view_is_derived():
    candidates = tuple(Candidate(instrument_id=f"60000{i}.SH", raw_score=1 - i / 10, quant_rank=i + 1) for i in range(7))
    inputs = {c.instrument_id: risk(c.instrument_id) for c in candidates}
    engine = RecommendationEngine()
    final = engine.evaluate(context(), candidates, inputs, PortfolioSnapshot(as_of=NOW, cash=100000))
    replay = engine.evaluate(context(), tuple(reversed(candidates)), inputs, PortfolioSnapshot(as_of=NOW, cash=100000))
    assert final == replay
    assert len(final.items) == 5
    assert final.top20 == final.top5 == final.items
    assert final.best_stock == final.items[0]
    assert final.status == "RESEARCH_ONLY"
    assert all(i.confidence is None and i.probability is None and i.expected_return is None for i in final.items)
    assert sum(i.risk_decision.incremental_cash for i in final.items) <= 100000
    assert all(r["status"] == "RESEARCH_ONLY" for r in engine.records(final)[:5])


def test_ties_have_canonical_identifier_order():
    candidates = (Candidate(instrument_id="600001.SH", raw_score=1, quant_rank=2),
                  Candidate(instrument_id="600000.SH", raw_score=1, quant_rank=1))
    result = RecommendationEngine().evaluate(context(), candidates, {c.instrument_id: risk(c.instrument_id) for c in candidates},
                                             PortfolioSnapshot(as_of=NOW, cash=100000))
    assert result.best_stock.instrument_id == "600000.SH"


def test_no_candidates_is_empty_target():
    final = RecommendationEngine().evaluate(context(), (), {}, PortfolioSnapshot(as_of=NOW, cash=100000))
    assert final.status == "EMPTY"
    assert final.target_weights == {}
    assert final.best_stock is None


def test_missing_risk_never_becomes_implicit_liquidation_or_partial_buy():
    candidates = (Candidate(instrument_id="600000.SH", raw_score=1, quant_rank=1),)
    final = RecommendationEngine().evaluate(context(), candidates, {}, PortfolioSnapshot(as_of=NOW, cash=100000))
    assert final.status == "UNAVAILABLE"
    assert final.target_weights is None
    assert not final.top20


def test_unvalidated_model_cannot_publish_production():
    candidate = Candidate(instrument_id="600000.SH", raw_score=1, quant_rank=1)
    final = RecommendationEngine().evaluate(context(mode="PRODUCTION"), (candidate,), {candidate.instrument_id: risk(candidate.instrument_id)},
                                             PortfolioSnapshot(as_of=NOW, cash=100000))
    assert final.status == "UNAVAILABLE"
    assert "MODEL_NOT_VALIDATED" in final.reason_codes


def test_policy_hash_and_input_hash_cover_portfolio_and_risk_configuration():
    engine = RecommendationEngine()
    one = engine.evaluate(context(), (), {}, PortfolioSnapshot(as_of=NOW, cash=100))
    two = engine.evaluate(context(), (), {}, PortfolioSnapshot(as_of=NOW, cash=200))
    assert one.input_hash != two.input_hash


def test_bad_rank_and_same_bar_trade_are_errors():
    with pytest.raises(ValueError):
        context(earliest_trade_at=NOW)
    with pytest.raises(ValueError):
        RecommendationEngine().evaluate(context(), (Candidate(instrument_id="600000.SH", raw_score=1, quant_rank=2),), {},
                                          PortfolioSnapshot(as_of=NOW, cash=100))
