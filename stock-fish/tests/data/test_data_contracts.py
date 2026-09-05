from datetime import date, datetime, timedelta
import struct
import pytest
from pydantic import ValidationError
from stockfish_core.common import UnavailableError
from stockfish_core.data import (SHANGHAI, CanonicalMarketBar, normalize_market_bar, canonical_instrument,
    SecurityState, SecurityMaster, TradingCalendar, TradabilityService,
    FundamentalRecord, FundamentalValue, PITFundamentals, LocalQlibDataAdapter, provider_classification)
from stockfish_core.observed_data import BaostockRawDataAdapter, exchange_calendar_2026

DAY = date(2026, 9, 4)
NOW = datetime(2026, 9, 5, 12, tzinfo=SHANGHAI)


def bar(**changes):
    fields = dict(instrument_id="000001.SZ", exchange="SZ", symbol="000001",
        timestamp=datetime(2026, 9, 4, 15, tzinfo=SHANGHAI), open=10., high=10.5, low=9.9,
        close=10.2, volume=10000., turnover=102000., price_adjustment="RAW", provider="fixture",
        source_timestamp=datetime(2026, 9, 4, 15, tzinfo=SHANGHAI), available_at=NOW,
        quality="VERIFIED", snapshot_id="fixture")
    fields.update(changes)
    return CanonicalMarketBar(**fields)


def state(**changes):
    values = dict(instrument_id="000001.SZ", effective_from=DAY, effective_to=DAY+timedelta(days=1),
        available_at=datetime(2026, 9, 4, 8, tzinfo=SHANGHAI), provider="fixture", verified=True,
        listing_date=date(1991, 4, 3), board="MAIN", is_st=False, suspended=False, lot_size=100,
        industry="银行", limits_date=DAY, limit_up=11., limit_down=9., no_price_limit=False)
    values.update(changes)
    return SecurityState(**values)


def service(**changes):
    return TradabilityService(exchange_calendar_2026(), SecurityMaster([state(**changes)]))


def test_explicit_identity_only():
    assert canonical_instrument("sh.600519") == "600519.SH"
    assert canonical_instrument("SH600519") == "600519.SH"
    with pytest.raises(ValueError): canonical_instrument("600519")
    with pytest.raises(ValueError): canonical_instrument("600519.SH", "SZ")


def test_explicit_units_and_no_missing_synthesis():
    raw = bar().model_dump()
    raw.update(volume=123., turnover=2.)
    actual = normalize_market_bar(**raw, volume_unit="LOT", lot_size=100, turnover_unit="CNY_1K")
    assert actual.volume == 12300 and actual.turnover == 2000
    with pytest.raises(ValueError): normalize_market_bar(**raw, volume_unit="LOT", turnover_unit="CNY")
    partial = bar(open=None, quality="PARTIAL")
    assert partial.open is None and "open" in partial.missing_fields
    with pytest.raises(UnavailableError): partial.require_raw_price()


@pytest.mark.parametrize("change", [{"close": float("nan")}, {"high": 9.}, {"volume": -1.},
    {"open": None}, {"timestamp": datetime(2026, 9, 4, 15)}, {"adjustment_factor": 0.5}])
def test_invalid_bars_rejected(change):
    with pytest.raises(ValidationError): bar(**change)


def test_frozen_and_adjusted_raw_price_unavailable():
    with pytest.raises(ValidationError): bar().close = 20
    with pytest.raises(UnavailableError): bar(price_adjustment="QFQ").require_raw_price()
    with pytest.raises(UnavailableError): bar().assert_visible(NOW-timedelta(seconds=1))
    with pytest.raises(ValidationError): bar(provider="mock")
    with pytest.raises(UnavailableError): bar(provider="unknown").require_raw_price()


def test_official_finite_calendar_and_earliest_entry():
    cal = exchange_calendar_2026()
    assert cal.earliest_trade_at(NOW) == datetime(2026,9,7,9,30,tzinfo=SHANGHAI)
    assert not cal.is_session(date(2026,2,23))
    assert not cal.is_session(date(2026,2,28))  # govt make-up Saturday is closed
    assert cal.next_session(date(2026,9,24)) == date(2026,9,28)
    with pytest.raises(UnavailableError): cal.next_session(date(2026,12,31))
    with pytest.raises(UnavailableError): cal.is_session(date(2027,1,4))


@pytest.mark.parametrize("changes,decision,reason", [({}, "ACCEPT", "TRADABLE"),
    ({"is_st": True}, "REJECT", "ST_EXCLUDED"), ({"suspended": True}, "REJECT", "SUSPENDED"),
    ({"lot_size": None}, "UNAVAILABLE", "SECURITY_FACTS_MISSING"),
    ({"limit_up": None}, "UNAVAILABLE", "PRICE_LIMITS_MISSING"),
    ({"limit_up": 10.2}, "REJECT", "LIMIT_UP")])
def test_historical_tradability(changes, decision, reason):
    result = service(**changes).evaluate("000001.SZ", NOW, DAY, "BUY", bar())
    assert result.decision == decision and reason in result.reason_codes


def test_friday_state_never_implies_monday_status():
    result = service().evaluate("000001.SZ", NOW, date(2026,9,7), "BUY", bar())
    assert result.decision == "UNAVAILABLE" and result.reason_codes == ("SECURITY_STATE_UNAVAILABLE",)


def test_execution_uses_available_state_without_future_daily_bar():
    execution = datetime(2026,9,4,9,30,tzinfo=SHANGHAI)
    assert service().evaluate_execution("000001.SZ", execution, "BUY", 10.).decision == "ACCEPT"
    assert service(available_at=NOW).evaluate_execution("000001.SZ", execution, "BUY", 10.).decision == "UNAVAILABLE"
    assert service().evaluate("000001.SZ", execution, DAY, "BUY", bar()).decision == "UNAVAILABLE"
    assert service().evaluate_execution("000001.SZ", execution.replace(hour=12), "BUY", 10.).decision == "REJECT"


def test_initial_buy_minimum_is_preserved_separately_from_increment():
    result = service(board="STAR",lot_size=1,min_buy_shares=200).evaluate("000001.SZ",NOW,DAY,"BUY",bar())
    assert result.decision == "ACCEPT" and result.lot_size == 1 and result.min_buy_shares == 200
    missing = service(board="STAR",lot_size=1).evaluate("000001.SZ",NOW,DAY,"BUY",bar())
    assert missing.decision == "UNAVAILABLE" and missing.min_buy_shares is None
    assert service().evaluate("000001.SZ",NOW,DAY,"BUY",bar()).min_buy_shares == 100


def test_pit_publication_ingestion_and_visible_revision():
    def record(revision, when, value):
        return FundamentalRecord(instrument_id="000001.SZ", period_end=date(2026,6,30),
            published_at=when, available_at=when, revision=revision, provider="fixture", snapshot_id=str(revision),
            pit_verified=True, values=(FundamentalValue(name="net_income", value=value, unit="CNY"),))
    old = record(0, NOW-timedelta(days=2), 100.)
    future = record(1, NOW+timedelta(days=1), 999.)
    pit = PITFundamentals([future, old])
    assert pit.visible("000001.SZ", NOW) == (old,)
    assert pit.visible("000001.SZ", NOW+timedelta(days=2)) == (future,)
    with pytest.raises(UnavailableError): pit.visible("000001.SZ", NOW-timedelta(days=3))


def qlib_fixture(tmp_path):
    (tmp_path/"calendars").mkdir(); (tmp_path/"instruments").mkdir()
    (tmp_path/"calendars/day.txt").write_text("2026-09-03\n2026-09-04\n")
    for name in ["all", "csi300"]:
        (tmp_path/f"instruments/{name}.txt").write_text("SZ000001\t2026-09-03\t2026-09-04\n")
    features = tmp_path/"features/sz000001"; features.mkdir(parents=True)
    for field in ["open", "high", "low", "close", "volume", "factor"]:
        (features/f"{field}.day.bin").write_bytes(struct.pack("<fff", 0., 1., 2.))
    return LocalQlibDataAdapter(tmp_path)


def test_qlib_manifest_stable_and_prices_research_only(tmp_path):
    adapter = qlib_fixture(tmp_path)
    first, second = adapter.manifest(), adapter.manifest()
    assert first.digest == second.digest
    observed = adapter.snapshot(DAY, ["000001.SZ"])[0]
    assert observed.close == 2. and observed.volume is None
    assert observed.quality == "RESEARCH_ONLY" and observed.source_timestamp is None
    with pytest.raises(UnavailableError): observed.require_raw_price()
    with pytest.raises(UnavailableError): adapter.snapshot(datetime(2026,9,4,10,tzinfo=SHANGHAI), ["000001.SZ"])
    (tmp_path/"features/sz000001/close.day.bin").write_bytes(struct.pack("<fff",0.,1.,3.))
    assert adapter.manifest().digest != first.digest


def payload():
    return {"date":"2026-09-04", "records":[{"code":"sz.000001", "daily":[{
        "date":"2026-09-04", "code":"sz.000001", "open":"10", "high":"10.5", "low":"9.9", "close":"10.2",
        "volume":"10000", "amount":"102000", "adjustflag":"3", "tradestatus":"1", "isST":"0", "preclose":"10"}],
        "basic":[{"code":"sz.000001","type":"1","ipoDate":"1991-04-03","outDate":"","status":"1"}],
        "industry":[{"code":"sz.000001","industry":"银行"}]}]}


def test_observed_raw_adapter_preserves_receipt_time_and_unknown_limits():
    observed = BaostockRawDataAdapter.from_payload(payload(), observed_at=NOW)
    assert observed.bars[0].require_raw_price() == 10.2
    assert observed.bars[0].available_at == NOW
    assert observed.security_states[0].limit_up is None
    assert observed.security_states[0].effective_to == date(2026,9,5)
    assert "EXECUTION_RECHECK_REQUIRED" in observed.reason_codes
    assert provider_classification("baostock")["classification"] == "REVIEW_REQUIRED"
    assert provider_classification("new-provider")["classification"] == "REVIEW_REQUIRED"


def test_observed_raw_adapter_rejects_misadjusted_and_future_data():
    data = payload(); data["records"][0]["daily"][0]["adjustflag"] = "2"
    with pytest.raises(UnavailableError): BaostockRawDataAdapter.from_payload(data, observed_at=NOW)
    with pytest.raises(UnavailableError): BaostockRawDataAdapter.from_payload(payload(), observed_at=NOW-timedelta(days=2))


def test_observed_actual_limits_join_only_matching_market_day_and_price():
    from stockfish_core.observed_data import EastmoneyDailyLimitAdapter, attach_observed_limits
    epoch = datetime(2026,9,4,15,34,tzinfo=SHANGHAI).timestamp()
    fields = {"rc":0, "data":{"f57":"000001", "f43":10.2,"f51":11.,"f52":9.,"f86":epoch}}
    evidence = EastmoneyDailyLimitAdapter.from_payload(fields, session_date=DAY, instrument_id="000001.SZ", observed_at=NOW)
    observation = BaostockRawDataAdapter.from_payload(payload(), observed_at=NOW-timedelta(seconds=1))
    joined = attach_observed_limits(observation,[evidence])
    assert joined.security_states[0].limit_provider == "eastmoney" and joined.security_states[0].limit_up == 11.
    assert joined.security_states[0].available_at == NOW and len(joined.source_payloads) == 2
    assert joined.snapshot_id != observation.snapshot_id
    assert TradabilityService(exchange_calendar_2026(),SecurityMaster(joined.security_states)).evaluate("000001.SZ",NOW,DAY,"BUY",joined.bars[0]).decision == "ACCEPT"
    with pytest.raises(UnavailableError): EastmoneyDailyLimitAdapter.from_payload(fields, session_date=date(2026,9,7), instrument_id="000001.SZ", observed_at=NOW)
    mismatch = evidence.model_copy(update={"raw_close":20.})
    with pytest.raises(UnavailableError): attach_observed_limits(observation,[mismatch])


def test_missing_limit_timestamp_or_unlimited_marker_not_guessed():
    from stockfish_core.observed_data import EastmoneyDailyLimitAdapter
    fields = {"rc":0,"data":{"f57":"000001","f43":10.2,"f51":"-","f52":"-"}}
    with pytest.raises(UnavailableError): EastmoneyDailyLimitAdapter.from_payload(fields, session_date=DAY, instrument_id="000001.SZ", observed_at=NOW)
