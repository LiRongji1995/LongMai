"""Independent TEST_FIXTURE price paths for the short-horizon research strategy."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from stockfish_core.yaogu.contracts import MarketInput, digest

TZ = ZoneInfo("Asia/Shanghai")
START, CONTINUATION, PULLBACK, WEAK = ("TEST_START", "TEST_CONT", "TEST_PULL", "TEST_WEAK")


def clocks(day, at=time(15)):
    value = datetime.combine(day, at, TZ)
    return dict(event_time=value, source_published_at=value, first_seen_at=value, ingested_at=value)


def strength_bundle():
    """Four complete paths; exact expected returns can be computed from these tails."""
    days, day = [], date(2026, 2, 2)
    while len(days) < 85:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    tails = {
        START: ("10", "10", "10", "10", "10", "11"),
        CONTINUATION: ("10", "11", "12.1", "13.31", "14.641", "16.1051"),
        PULLBACK: ("10", "11", "12.1", "13.31", "14.641", "13.8"),
        WEAK: ("10", "10.02", "10.03", "10.05", "10.10", "10.2"),
    }
    securities, bars = [], []
    for iid, tail in tails.items():
        securities.append(dict(instrument_id=iid, name="TEST_FIXTURE " + iid,
            exchange="SSE", board="MAIN", security_type="A_COMMON", listed_on=days[0],
            effective_from=days[0], st=False, suspended=False,
            source_refs=["TEST_FIXTURE_MASTER"], **clocks(days[0], time(9))))
        closes = [Decimal("10")] * (len(days) - len(tail)) + list(map(Decimal, tail))
        for index, day in enumerate(days):
            close = closes[index]
            previous = closes[index - 1] if index else close
            open_, high, low = previous, max(previous, close), min(previous, close)
            # A nonzero range for flat prior days avoids deriving CLV from 0/0.
            if high == low:
                high, low = close + Decimal(".1"), close - Decimal(".1")
            amount = Decimal("300000000") if iid != START else Decimal("150000000") if day == days[-1] else Decimal("100000000")
            # Comparable prices deliberately differ from RAW; comparisons must not mix bases.
            factor = Decimal("2")
            bars.append(dict(instrument_id=iid, session=day, open=open_, high=high, low=low,
                close=close, reference_prev_close=previous, amount_cny=amount,
                volume_shares=Decimal("1000000"), turnover_fraction=.004,
                turnover_basis="TEST_FIXTURE_KNOWN_LOW_TURNOVER", quality="VALID",
                comparable_open=open_ * factor, comparable_high=high * factor,
                comparable_low=low * factor, comparable_close=close * factor,
                comparable_basis="TEST_FIXTURE_FIXED_HFQ_FACTOR_2", limit_applicable=None,
                limit_verified=False, source_refs=["TEST_FIXTURE_BARS"], **clocks(day)))
    receipts = [dict(receipt_id=rid, provider="TEST_FIXTURE", dataset=rid,
        content_hash=digest(rows), field_version="TEST_FIXTURE_SHORT_STRENGTH_V1",
        status="COMPLETE", pagination_complete=True, row_count=len(rows),
        instrument_ids=list(tails), business_sessions=days if rid.endswith("BARS") else [],
        **clocks(days[0], time(9)))
        for rid, rows in (("TEST_FIXTURE_MASTER", securities), ("TEST_FIXTURE_BARS", bars))]
    bundle = MarketInput.model_validate(dict(observation_date=days[-1], sessions=days,
        universe=dict(snapshot_id="TEST_FIXTURE_SHORT_STRENGTH", securities=securities,
            complete=True, expected_count=len(tails), source_refs=["TEST_FIXTURE_MASTER"],
            **clocks(days[0], time(9))), bars=bars, receipts=receipts,
        themes=[], funds=[], coverage_type="TEST_FIXTURE"))
    return bundle, datetime.combine(days[-1], time(20, 30), TZ)


def payload(bundle):
    value = bundle.model_dump(mode="python")
    for field in ("sessions", "bars", "receipts", "themes", "funds"):
        value[field] = list(value[field])
    value["universe"]["securities"] = list(value["universe"]["securities"])
    return value


def current_row(value, iid=START):
    return next(bar for bar in value["bars"] if bar["instrument_id"] == iid
        and bar["session"] == value["observation_date"])
