"""Observed public raw data: actual receipt time, immutable payload, no PIT claims.

Baostock does not publish authoritative daily limit/exception flags. Those facts
remain unavailable unless the caller supplies independently verified evidence.
"""
from __future__ import annotations
import json
import math
import subprocess
import sys
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Mapping
from pydantic import Field

from .common import CoreModel, UnavailableError, digest, utc_now
from .data import (SHANGHAI, CanonicalMarketBar, SecurityState, TradingCalendar,
                   aware, canonical_instrument, normalize_market_bar)


class DailyLimitEvidence(CoreModel):
    """Independent evidence that the normal main-board limit applies that day.

    The API caller must supply a traceable official/vendor observation confirming
    no relisting, delisting first-day or other exemption. Symbol-prefix guesses
    and Baostock isST alone cannot establish this fact.
    """
    instrument_id: str
    session_date: date
    available_at: datetime
    source: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    normal_main_board_limit_verified: bool = False


class ProviderPayload(CoreModel):
    provider: str
    observed_at: datetime
    payload_sha256: str
    payload_json: str  # public market data only; never tokens/request headers


def captured_payload(provider, payload, observed_at):
    value = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",",":"), allow_nan=False)
    import hashlib
    return ProviderPayload(provider=provider, observed_at=observed_at,
        payload_sha256=hashlib.sha256(value.encode("utf-8")).hexdigest(), payload_json=value)


class MarketObservation(CoreModel):
    snapshot_id: str
    session_date: date
    observed_at: datetime
    bars: tuple[CanonicalMarketBar, ...]
    security_states: tuple[SecurityState, ...]
    source_payloads: tuple[ProviderPayload, ...] = ()
    quality: str = "RESEARCH_ONLY"
    production_classification: str = "REVIEW_REQUIRED"
    reason_codes: tuple[str, ...] = ("EXECUTION_RECHECK_REQUIRED", "ORIGINAL_AVAILABILITY_UNKNOWN")


class ObservedDailyLimits(CoreModel):
    instrument_id: str
    session_date: date
    source_timestamp: datetime
    available_at: datetime
    limit_up: float = Field(gt=0)
    limit_down: float = Field(gt=0)
    raw_close: float = Field(gt=0)
    provider: str = "eastmoney"
    evidence_id: str
    source_url: str = "https://push2.eastmoney.com/api/qt/stock/get"
    source_payload: ProviderPayload


class EastmoneyDailyLimitAdapter:
    """Read actual vendor-supplied f51/f52; never derive fixed percentage limits.

    Mapping verified against AKShare's maintained stock_ask_bid_em.py and the
    actual Eastmoney public quote response (2026-09-05). fltt=2 means yuan.
    f86 is retained as provider-reported epoch time; wrong/absent day fails.
    This is an unofficial public endpoint and stays REVIEW_REQUIRED.
    """
    def fetch(self, session_date: date, instrument_id: str, *, timeout: float = 8) -> ObservedDailyLimits:
        import requests
        identity = canonical_instrument(instrument_id)
        if identity[-2:] not in ("SH", "SZ") or not 0 < timeout <= 15:
            raise ValueError("unsupported exchange or timeout")
        market = "1" if identity.endswith(".SH") else "0"
        # Small bounded retry only; preserve the caller's configured proxy.
        # No public-proxy discovery, credential relay or global request patch.
        payload = None
        for attempt in range(2):
            try:
                response = requests.get("https://push2.eastmoney.com/api/qt/stock/get", params={
                    "fltt":"2", "invt":"2", "secid":f"{market}.{identity[:6]}",
                    "fields":"f43,f51,f52,f57,f59,f86"}, timeout=(5, timeout))
                response.raise_for_status()
                payload = response.json()
                break
            except (requests.RequestException, ValueError):
                if attempt == 1:
                    raise UnavailableError("EASTMONEY_LIMITS_UNAVAILABLE", "data") from None
        if payload is None:
            raise UnavailableError("EASTMONEY_LIMITS_UNAVAILABLE", "data")
        return self.from_payload(payload, session_date=session_date, instrument_id=identity, observed_at=utc_now())

    @staticmethod
    def from_payload(payload: dict, *, session_date: date, instrument_id: str, observed_at: datetime) -> ObservedDailyLimits:
        identity = canonical_instrument(instrument_id)
        aware(observed_at)
        row = payload.get("data") or {}
        if payload.get("rc") != 0 or row.get("f57") != identity[:6]:
            raise UnavailableError("EASTMONEY_LIMIT_IDENTITY_MISMATCH", "data")
        try:
            source_time = datetime.fromtimestamp(float(row["f86"]), timezone.utc)
            up, down, close = (float(row[n]) for n in ("f51", "f52", "f43"))
        except (KeyError, TypeError, ValueError, OSError, OverflowError) as exc:
            raise UnavailableError("EASTMONEY_LIMIT_FIELDS_MISSING", "data") from exc
        if source_time > observed_at or source_time.astimezone(SHANGHAI).date() != session_date:
            raise UnavailableError("EASTMONEY_LIMIT_DATE_MISMATCH", "data")
        if not all(math.isfinite(n) and n > 0 for n in (up, down, close)) or not down <= close <= up or down == up:
            raise UnavailableError("EASTMONEY_LIMITS_INVALID", "data")
        return ObservedDailyLimits(instrument_id=identity, session_date=session_date, source_timestamp=source_time,
            available_at=observed_at, limit_up=up, limit_down=down, raw_close=close,
            evidence_id=digest({"payload":payload,"observed_at":observed_at.isoformat(),"instrument_id":identity}),
            source_payload=captured_payload("eastmoney",payload,observed_at))


def attach_observed_limits(observation: MarketObservation, limits) -> MarketObservation:
    """Join independently observed actual limits only on exact symbol/date/close.

    Cross-provider disagreements fail closed; no extrapolation to the next day.
    Returned snapshot versions both input observations and preserves receipt time.
    """
    values = tuple(limits)
    mapped = {v.instrument_id:v for v in values}
    if len(mapped) != len(values):
        raise ValueError("duplicate daily limit evidence")
    bars = {b.instrument_id:b for b in observation.bars}
    if set(mapped) - set(bars):
        raise ValueError("limit evidence outside observation")
    states = []
    for state in observation.security_states:
        evidence = mapped.get(state.instrument_id)
        if not evidence:
            states.append(state)
            continue
        bar = bars[state.instrument_id]
        if evidence.session_date != observation.session_date or abs(bar.require_raw_price()-evidence.raw_close) > 0.005:
            raise UnavailableError("CROSS_PROVIDER_PRICE_OR_DATE_MISMATCH", "data")
        fields = state.model_dump()
        fields.update(available_at=max(state.available_at,evidence.available_at), limit_up=evidence.limit_up,
            limit_down=evidence.limit_down, no_price_limit=False, limits_date=evidence.session_date,
            limit_provider=evidence.provider, limit_source_timestamp=evidence.source_timestamp,
            limit_available_at=evidence.available_at, limit_evidence_id=evidence.evidence_id)
        states.append(SecurityState(**fields))
    sid = digest({"observation":observation.snapshot_id, "limits":[v.model_dump(mode="json") for v in sorted(values,key=lambda v:v.instrument_id)]})
    timestamp = max([observation.observed_at]+[v.available_at for v in values])
    fields = observation.model_dump()
    fields.update(snapshot_id=sid,observed_at=timestamp,security_states=tuple(states),
        source_payloads=observation.source_payloads + tuple(v.source_payload for v in values),
        bars=tuple(CanonicalMarketBar(**(b.model_dump() | {"snapshot_id":sid})) for b in observation.bars))
    return MarketObservation(**fields)


def exchange_calendar_2026() -> TradingCalendar:
    """SH/SZ only, finite official schedule verified 2026-09-05.

    Weekday rule and explicit exchange holidays are both sourced; no extension
    beyond 2026, and government weekend make-up workdays are never trading days.
    Source links and revision metadata are in exchange_calendar_2026.json.
    """
    config = json.loads(Path(__file__).with_name("exchange_calendar_2026.json").read_text(encoding="utf-8"))
    closed = set()
    for begin, end in config["closed_ranges"]:
        start, stop = date.fromisoformat(begin), date.fromisoformat(end)
        closed.update(start + timedelta(days=n) for n in range((stop-start).days + 1))
    start, stop = date(2026, 1, 1), date(2026, 12, 31)
    days = [start + timedelta(days=n) for n in range((stop-start).days + 1)]
    return TradingCalendar([d for d in days if d.weekday() < 5 and d not in closed], start, stop)


class BaostockRawDataAdapter:
    """Fetch <=10 symbols/day in an isolated bounded subprocess; never auto-run."""
    def fetch(self, session_date: date, instruments, *, timeout: float = 45,
              limit_evidence: Mapping[str, DailyLimitEvidence] | None = None) -> MarketObservation:
        identities = tuple(canonical_instrument(i) for i in instruments)
        if not 0 < len(identities) <= 10 or len(set(identities)) != len(identities):
            raise ValueError("provide 1..10 unique explicit instruments")
        if any(i[-2:] not in ("SH", "SZ") for i in identities):
            raise UnavailableError("BAOSTOCK_MARKET_UNSUPPORTED", "data")
        if not 0 < timeout <= 90:
            raise ValueError("timeout must be 0..90 seconds")
        codes = [f"{i[-2:].lower()}.{i[:6]}" for i in identities]
        try:
            result = subprocess.run([sys.executable, "-m", "stockfish_core.baostock_worker"],
                input=json.dumps({"date": session_date.isoformat(), "codes": codes}),
                text=True, capture_output=True, timeout=timeout,
                cwd=str(Path(__file__).resolve().parent.parent))
            if result.returncode != 0:
                raise UnavailableError("BAOSTOCK_OBSERVATION_FAILED", "data")
            payload = json.loads(result.stdout)
        except subprocess.TimeoutExpired as exc:
            raise UnavailableError("BAOSTOCK_TIMEOUT", "data") from exc
        except (OSError, ValueError) as exc:
            raise UnavailableError("BAOSTOCK_RESPONSE_INVALID", "data") from exc
        return self.from_payload(payload, observed_at=utc_now(), limit_evidence=limit_evidence)

    @staticmethod
    def from_payload(payload: dict, *, observed_at: datetime,
                     limit_evidence: Mapping[str, DailyLimitEvidence] | None = None) -> MarketObservation:
        """Normalize a captured vendor payload. observed_at is actual ingestion.

        Offline fixtures may inject the clock; production callers use fetch().
        source_timestamp is response receipt, not reconstructed publication time.
        """
        aware(observed_at)
        day = date.fromisoformat(payload["date"])
        timestamp = datetime.combine(day, time(15), SHANGHAI)
        if timestamp > observed_at:
            raise UnavailableError("INCOMPLETE_DAILY_BAR", "data")
        sid = digest({"payload": payload, "observed_at": observed_at.isoformat()})
        bars, states, seen = [], [], set()
        for record in payload["records"]:
            identity = canonical_instrument(record["code"])
            if identity in seen:
                raise UnavailableError("DUPLICATE_VENDOR_INSTRUMENT", "data")
            seen.add(identity)
            if len(record["daily"]) != 1 or len(record["basic"]) != 1:
                raise UnavailableError("BAOSTOCK_DAILY_OR_BASIC_MISSING", "data")
            row, basic = record["daily"][0], record["basic"][0]
            if canonical_instrument(row["code"]) != identity or canonical_instrument(basic["code"]) != identity or row["date"] != day.isoformat():
                raise UnavailableError("BAOSTOCK_IDENTITY_DATE_MISMATCH", "data")
            if row.get("adjustflag") != "3" or basic.get("type") != "1":
                raise UnavailableError("BAOSTOCK_RAW_STOCK_CONTRACT_MISSING", "data")
            if row.get("tradestatus") not in ("0", "1") or row.get("isST") not in ("0", "1"):
                raise UnavailableError("BAOSTOCK_STATUS_MISSING", "data")
            def number(name):
                value = row.get(name)
                if value in (None, ""):
                    return None
                value = float(value)
                if not math.isfinite(value):
                    raise UnavailableError("BAOSTOCK_NONFINITE", "data")
                return value
            values = {name: number(name) for name in ("open", "high", "low", "close", "volume")}
            suspended = row["tradestatus"] == "0"
            # Vendor suspended rows can repeat prior close with zero volume;
            # do not describe these as executable verified bars.
            bars.append(normalize_market_bar(instrument_id=identity, exchange=identity[-2:], symbol=identity[:6],
                timestamp=timestamp, **values, turnover=number("amount"), volume_unit="SHARE", turnover_unit="CNY",
                price_adjustment="RAW", adjustment_factor=1.0, provider="baostock", source_timestamp=observed_at,
                available_at=observed_at, quality="PARTIAL" if suspended or any(v is None for v in values.values()) or number("amount") is None else "VERIFIED",
                quality_notes=("SOURCE_TIMESTAMP_IS_RECEIPT", "ORIGINAL_AVAILABILITY_UNKNOWN", "EXECUTION_RECHECK_REQUIRED"), snapshot_id=sid))
            ipo = date.fromisoformat(basic["ipoDate"]) if basic.get("ipoDate") else None
            delisted = date.fromisoformat(basic["outDate"]) if basic.get("outDate") else None
            board = "MAIN" if (identity.endswith(".SH") and identity.startswith("60")) or (identity.endswith(".SZ") and identity.startswith("00")) else None
            industries = [i for i in record.get("industry", []) if canonical_instrument(i["code"]) == identity and i.get("industry")]
            industry = industries[0]["industry"] if len(industries) == 1 else None
            limits = {}
            evidence = (limit_evidence or {}).get(identity)
            if evidence:
                aware(evidence.available_at)
                # This version only implements the verified 2026-07-06 rule.
                if evidence.instrument_id != identity or evidence.session_date != day or evidence.available_at > observed_at:
                    raise UnavailableError("LIMIT_EVIDENCE_MISMATCH", "data")
                if evidence.normal_main_board_limit_verified and board == "MAIN" and day >= date(2026, 7, 6) and ipo and (day-ipo).days > 60 and not delisted:
                    preclose = number("preclose")
                    if preclose and preclose > 0:
                        tick = Decimal("0.01")
                        limits = {"limit_up": float((Decimal(str(preclose))*Decimal("1.10")).quantize(tick, rounding=ROUND_HALF_UP)),
                                  "limit_down": float((Decimal(str(preclose))*Decimal("0.90")).quantize(tick, rounding=ROUND_HALF_UP)), "no_price_limit": False}
            states.append(SecurityState(instrument_id=identity, effective_from=day, effective_to=day+timedelta(days=1),
                available_at=observed_at, provider="baostock", verified=True, listing_date=ipo, delisting_date=delisted,
                board=board, is_st=row["isST"] == "1", suspended=suspended, lot_size=100 if board == "MAIN" else None,
                min_buy_shares=100 if board == "MAIN" else None, industry=industry, limits_date=day, **limits))
        if not bars:
            raise UnavailableError("BAOSTOCK_EMPTY_OBSERVATION", "data")
        return MarketObservation(snapshot_id=sid, session_date=day, observed_at=observed_at, bars=tuple(bars), security_states=tuple(states),
            source_payloads=(captured_payload("baostock",payload,observed_at),))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Observe public RAW daily data; no future eligibility or PIT claims")
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--with-observed-limits", action="store_true")
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise UnavailableError("OBSERVATION_OUTPUT_ALREADY_EXISTS", "data")
        observation = BaostockRawDataAdapter().fetch(args.date, args.symbols)
        if args.with_observed_limits:
            limits = [EastmoneyDailyLimitAdapter().fetch(args.date, i) for i in args.symbols]
            observation = attach_observed_limits(observation, limits)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents replacing historical snapshots.
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(observation.model_dump_json(indent=2))
        print(json.dumps({"success":True,"snapshot_id": observation.snapshot_id, "bars": len(observation.bars), "output": str(args.output)}))
        return 0
    except UnavailableError as exc:
        print(json.dumps({"success":False,"quality":"UNAVAILABLE","component":exc.component,"reason":exc.reason}))
        return 1
    except Exception as exc:
        # Provider/OS exception strings may contain proxy credentials. Never
        # print them or a chained traceback from this public command interface.
        print(json.dumps({"success":False,"quality":"UNAVAILABLE","reason":"OBSERVATION_FAILED","error_type":type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
