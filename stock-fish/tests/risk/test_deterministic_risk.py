from datetime import datetime, timedelta, timezone

import pytest

from stockfish_core.risk import Holding, PortfolioSnapshot, RiskAction, RiskEngine, RiskInput, RiskPolicy, protective_prices

NOW = datetime(2026, 9, 4, 8, tzinfo=timezone.utc)


def trusted(iid="600519.SH", price=10, industry="consumer", **kw):
    data = dict(instrument_id=iid, decision_time=NOW, data_available_at=NOW,
                price_timestamp=NOW, reference_price=price, price_adjustment="RAW", quality="VERIFIED",
                provider="fixture-explicit", industry=industry, tradability="ACCEPT", lot_size=100, min_buy_shares=100)
    return RiskInput(**(data | kw))


def test_negative_var_cannot_invert_protective_prices():
    assert protective_prices(1000, -3) == (955, 1060)
    assert protective_prices(1000, 3) == (955, 1060)
    for bad in (0, float("nan"), float("inf"), 100):
        with pytest.raises(ValueError):
            protective_prices(1000, bad)


@pytest.mark.parametrize("change,reason", [
    ({"quality": "MOCK"}, "UNTRUSTED_MARKET_DATA"),
    ({"provider": "mock"}, "UNTRUSTED_MARKET_DATA"),
    ({"reference_price": None}, "RAW_PRICE_UNAVAILABLE"),
    ({"price_adjustment": "QLIB_NORMALIZED"}, "RAW_PRICE_UNAVAILABLE"),
    ({"data_available_at": NOW + timedelta(seconds=1)}, "FUTURE_DATA"),
    ({"tradability": "UNAVAILABLE"}, "TRADABILITY_UNAVAILABLE"),
    ({"industry": None}, "SECURITY_TERMS_UNAVAILABLE"),
    ({"price_timestamp": NOW - timedelta(days=8)}, "STALE_PRICE"),
])
def test_unknown_or_untrusted_fails_closed(change, reason):
    decision = RiskEngine().evaluate(trusted(**change), PortfolioSnapshot(as_of=NOW, cash=100000))
    assert decision.decision == RiskAction.UNAVAILABLE
    assert reason in decision.reason_codes
    assert decision.approved_target_shares == 0


def test_hard_veto_survives_even_bullish_context():
    result = RiskEngine().evaluate(trusted(hard_veto=True, veto_reasons=("SUSPENDED",)),
                                   PortfolioSnapshot(as_of=NOW, cash=100000))
    assert result.decision == "REJECT"
    assert result.reason_codes == ("HARD_VETO", "SUSPENDED")


def test_existing_positions_count_before_new_buys():
    holdings = tuple(Holding(instrument_id=f"60000{i}.SH", shares=100, sellable_shares=100,
                             mark_price=10, industry="bank", marked_at=NOW) for i in range(5))
    result = RiskEngine().evaluate(trusted(), PortfolioSnapshot(as_of=NOW, cash=100000, holdings=holdings))
    assert result.reason_codes == ("MAX_POSITIONS",)


def test_lot_cash_and_industry_reservations():
    engine = RiskEngine(RiskPolicy(max_stock_weight=0.3, max_industry_weight=0.4))
    account = PortfolioSnapshot(as_of=NOW, cash=100000)
    first = engine.evaluate(trusted(), account)
    assert first.approved_target_shares == 3000
    second = engine.evaluate(trusted("600000.SH"), account, reserved_cash=first.incremental_cash,
                             planned_positions=("600519.SH",), industry_additions={"consumer": 30000})
    assert second.approved_target_shares == 1000
    assert second.decision == "REDUCE"
    assert first.incremental_cash + second.incremental_cash <= account.cash


def test_exposure_reduction_respects_sellable_shares():
    holding = Holding(instrument_id="600519.SH", shares=10000, sellable_shares=0,
                      mark_price=10, industry="consumer", marked_at=NOW)
    account = PortfolioSnapshot(as_of=NOW, cash=0, holdings=(holding,))
    assert RiskEngine().evaluate(trusted(), account).reason_codes == ("REDUCTION_NOT_SELLABLE",)


def test_minimum_order_cannot_force_a_purchase():
    result = RiskEngine().evaluate(trusted(price=1330), PortfolioSnapshot(as_of=NOW, cash=10000))
    assert result.decision == "REJECT"
    assert result.approved_target_shares == 0


def test_exposure_reduction_requires_sell_tradability_not_just_buy():
    holding = Holding(instrument_id="600519.SH", shares=10000, sellable_shares=10000,
                      mark_price=10, industry="consumer", marked_at=NOW)
    account = PortfolioSnapshot(as_of=NOW, cash=0, holdings=(holding,))
    engine = RiskEngine()
    for status in ("UNAVAILABLE", "REJECT"):
        result = engine.evaluate(trusted(exit_tradability=status), account)
        assert result.decision == "UNAVAILABLE"
        assert result.reason_codes[0] == "REDUCTION_TRADABILITY_UNAVAILABLE"
    accepted = engine.evaluate(trusted(exit_tradability="ACCEPT"), account)
    assert accepted.decision == "REDUCE" and accepted.approved_target_shares == 2000


def test_existing_overfull_portfolio_cannot_be_increased():
    holdings = tuple(Holding(instrument_id=f"60000{i}.SH", shares=100, sellable_shares=100,
                             mark_price=10, industry=f"industry-{i}", marked_at=NOW) for i in range(7))
    account = PortfolioSnapshot(as_of=NOW, cash=93000, holdings=holdings)
    result = RiskEngine().evaluate(trusted("600000.SH", industry="industry-0"), account)
    assert result.decision == "UNAVAILABLE"
    assert result.reason_codes == ("EXISTING_POSITION_COUNT_EXCEEDS_LIMIT",)


@pytest.mark.parametrize("change,reason", [
    ({"price_adjustment": "QLIB_NORMALIZED"}, "RAW_PRICE_UNAVAILABLE"),
    ({"quality": "MOCK"}, "UNTRUSTED_MARKET_DATA"),
    ({"reference_price": 11}, "INCONSISTENT_PORTFOLIO_MARK"),
    ({"data_available_at": NOW + timedelta(seconds=1)}, "FUTURE_DATA"),
])
def test_exit_also_requires_trusted_market_inputs(change, reason):
    account = PortfolioSnapshot(as_of=NOW, cash=10000, holdings=(Holding(instrument_id="600519.SH",
        shares=100, sellable_shares=100, mark_price=10, industry="consumer", marked_at=NOW),))
    result = RiskEngine().evaluate_exit(trusted(exit_tradability="ACCEPT", **change), account)
    assert result.decision == "UNAVAILABLE" and reason in result.reason_codes


def test_exit_uses_sell_rules_even_when_buy_is_rejected():
    account = PortfolioSnapshot(as_of=NOW, cash=0, holdings=(Holding(instrument_id="600519.SH",
        shares=100, sellable_shares=100, mark_price=10, industry="consumer", marked_at=NOW),))
    result = RiskEngine().evaluate_exit(trusted(tradability="REJECT", exit_tradability="ACCEPT"), account)
    assert result.decision == "ACCEPT"
