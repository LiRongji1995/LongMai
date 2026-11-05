"""Fixed TEST_FIXTURE market inputs shared by the retained PV and stage-aware tests."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from stockfish_core.yaogu.contracts import MarketInput, digest
from tests.yaogu.fixtures import TZ


def _days(count):
    result, day = [], date(2025, 8, 18)
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day)
        day += timedelta(days=1)
    return result


def _clocks(day, at=time(15)):
    stamp = datetime.combine(day, at, TZ)
    return {"event_time": stamp, "source_published_at": stamp, "first_seen_at": stamp, "ingested_at": stamp}


def _receipt(receipt_id, payload, day):
    return {
        "receipt_id": receipt_id,
        "provider": "TEST_FIXTURE",
        "dataset": receipt_id,
        "content_hash": digest(payload),
        "field_version": "TEST_FIXTURE_THEME_CAPITAL_TREND_V1",
        "row_count": len(payload),
        "pagination_complete": True,
        "status": "COMPLETE",
        **_clocks(day, time(9)),
    }


def _series(index, days, *, strong_peer=False, pullback_days=0):
    base = [Decimal("10") + Decimal(k) * Decimal("0.05") + Decimal(index) * Decimal("0.01") for k in range(len(days))]
    if strong_peer:
        start = base[-6]
        for offset in range(6):
            base[-6 + offset] = start * (Decimal("1.00") + Decimal(offset) * Decimal("0.025"))
    if pullback_days:
        for offset in range(1, pullback_days + 1):
            base[-offset] = base[-pullback_days - 1] * (Decimal("0.985") - Decimal(offset - 1) * Decimal("0.002"))
    return [value.quantize(Decimal("0.0001")) for value in base]


def trend_bundle(*, amount=Decimal("600000000"), turnover=Decimal("0.08"), sessions=280, pullback_days=0, funds=None, theme_members=None, extra_themes=()):
    days = _days(sessions)
    ids = [f"TEST{i:03d}" for i in range(8)]
    securities, bars = [], []
    for number, iid in enumerate(ids):
        securities.append({
            "instrument_id": iid,
            "exchange": "SSE" if number % 2 == 0 else "SZSE",
            "board": "MAIN",
            "security_type": "A_COMMON",
            "listed_on": days[0],
            "effective_from": days[0],
            "st": False,
            "suspended": False,
            "name": iid,
            "source_refs": ["fixture-master"],
            **_clocks(days[0], time(9)),
        })
        closes = _series(number, days, strong_peer=1 <= number <= 6, pullback_days=pullback_days if number == 0 else 0)
        for index, day in enumerate(days):
            close = closes[index]
            prev = closes[index - 1] if index else close / Decimal("1.01")
            opened = prev
            high = max(opened, close) * Decimal("1.01")
            low = min(opened, close) * Decimal("0.99")
            current_amount = amount if number == 0 else Decimal("700000000")
            prior_amount = Decimal("1000000000") if number == 0 else Decimal("500000000")
            day_amount = current_amount if day == days[-1] else prior_amount if day in days[-6:-1] else Decimal("300000000")
            current_turnover = turnover if number == 0 else Decimal("0.08")
            bars.append({
                "instrument_id": iid,
                "session": day,
                "open": opened.quantize(Decimal("0.0001")),
                "high": high.quantize(Decimal("0.0001")),
                "low": low.quantize(Decimal("0.0001")),
                "close": close,
                "reference_prev_close": prev.quantize(Decimal("0.0001")),
                "volume_shares": Decimal("8000000"),
                "amount_cny": day_amount,
                "limit_up": (prev * Decimal("1.10")).quantize(Decimal("0.01")),
                "limit_down": (prev * Decimal("0.90")).quantize(Decimal("0.01")),
                "limit_applicable": True,
                "limit_verified": True,
                "rule_id": "TEST_FIXTURE_10_PERCENT",
                "comparable_open": opened.quantize(Decimal("0.0001")),
                "comparable_high": high.quantize(Decimal("0.0001")),
                "comparable_low": low.quantize(Decimal("0.0001")),
                "comparable_close": close,
                "comparable_basis": "TEST_FIXTURE_NO_ACTION",
                "float_shares": Decimal("100000000"),
                "turnover_fraction": float(current_turnover if day == days[-1] else Decimal("0.08")),
                "turnover_basis": "TEST_FIXTURE_VERIFIED_FLOAT",
                "source_refs": ["fixture-bars"],
                **_clocks(day),
            })
    members = tuple(theme_members or ids[:7])
    theme = {
        "theme_id": "THEME_A",
        "name": "Fixture active theme",
        "theme_type": "EVENT",
        "member_ids": members,
        "effective_from": days[0],
        "definition_version": "TEST_FIXTURE_THEME_CAPITAL_TREND_V1",
        "source_refs": ["fixture-theme"],
        **_clocks(days[0], time(9)),
    }
    themes = [theme, *extra_themes]
    default_funds = [
        _fund("TEST001", days[-1], Decimal("60000000"), Decimal("40000000"), seat="S1"),
        _fund("TEST002", days[-2], Decimal("60000000"), Decimal("30000000"), seat="S2"),
    ]
    selected_funds = default_funds if funds is None else funds
    payloads = [("fixture-master", securities), ("fixture-bars", bars), ("fixture-theme", themes), ("fixture-funds", selected_funds)]
    return MarketInput.model_validate({
        "observation_date": days[-1],
        "sessions": days,
        "universe": {
            "snapshot_id": "TEST_FIXTURE_THEME_CAPITAL_UNIVERSE",
            "securities": securities,
            "complete": True,
            "expected_count": len(ids),
            "source_refs": ["fixture-master"],
            **_clocks(days[0], time(9)),
        },
        "bars": bars,
        "themes": themes,
        "receipts": [_receipt(rid, payload, days[0]) for rid, payload in payloads],
        "funds": selected_funds,
        "coverage_type": "TEST_FIXTURE",
    }), datetime.combine(days[-1], time(20, 30), TZ)


def _fund(iid, day, buy, net, *, theme_id="THEME_A", layer="SUPPLIER_CLASSIFIED_ACTIVE_SEAT", seat="S1", window_start=None, window_end=None, dedupe_key=None):
    return {
        "instrument_id": iid,
        "theme_id": theme_id,
        "session": day,
        "window_start": window_start or day,
        "window_end": window_end or day,
        "seat_id": seat,
        "buy_amount_cny": buy,
        "net_amount_cny": net,
        "source_layer": layer,
        "source_refs": ["fixture-funds"],
        **_clocks(day, time(16)),
        **({"dedupe_key": dedupe_key} if dedupe_key else {}),
    }
