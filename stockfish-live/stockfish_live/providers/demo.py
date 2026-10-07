"""Explicit deterministic synthetic demo, routed through the production contracts.

This is never a provider fallback and makes no claims about actual market
sessions, historical instruments, exchange membership or investment edge.
"""
from __future__ import annotations

import hashlib
import math
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import perf_counter

from ..calendar import TradingCalendar
from ..common import LiveError, SHANGHAI, canonical_json
from ..contracts.context import (ContextBundle, DailyContext, ReferencePrice, SectorMembership,
                                 SecurityStatus, UniverseCoverage)
from ..contracts.forecast import ForecastConfig
from ..contracts.market import DatasetScope, Instrument, MinuteBar, SourceReceipt
from ..features.market import MarketStateEngine
from ..features.streaming import StreamingMarketStateEngine
from ..forecast.engine import mature_outcomes, state_from_market_state
from ..storage.parquet_store import ParquetStore
from .mock import INDICES, STOCKS

SEED = 20261007
LABEL = "TEST_FIXTURE"
PATTERNS = ("TREND_UP", "TREND_DOWN", "RANGE", "HIGH_OPEN_FADE", "REPAIR", "DIVERGENCE")


def _moment(day, hour=9, minute=0):
    return datetime.combine(day, time(hour, minute), SHANGHAI)


def _day_context(day, previous_close, index):
    """Preopen synthetic publications; business validity is not visibility."""
    preopen = _moment(day)
    common = dict(provider="mock", source_timestamp=preopen.isoformat(), source_timestamp_basis="PUBLISHED",
        available_at=preopen.isoformat(), pit_status=LABEL,
        effective_from=_moment(day, 0).isoformat(), effective_to=_moment(day + timedelta(days=1), 0).isoformat())
    references, statuses, memberships, daily = [], [], [], []
    for offset, instrument_id in enumerate(INDICES + STOCKS):
        previous = previous_close[instrument_id]
        is_stock = instrument_id in STOCKS
        is_st = is_stock and instrument_id == STOCKS[1] and index % 11 == 7
        limit_fraction = .05 if is_st else .2 if instrument_id.startswith("300") and is_stock else .1
        references.append(dict(**common, instrument_id=instrument_id, trading_date=day.isoformat(),
            previous_close=previous, limit_up=previous * (1 + limit_fraction) if is_stock else None,
            limit_down=previous * (1 - limit_fraction) if is_stock else None, price_adjustment="RAW"))
        # These are explicit TEST_FIXTURE daily observations supplied preopen,
        # not returns claimed to have been reconstructed from minute archives.
        daily.append(dict(**common, instrument_id=instrument_id, based_on_date=(day - timedelta(days=1)).isoformat(),
            long_return_20d_pct=4. * math.sin((index + offset) / 8.),
            medium_return_5d_pct=1.5 * math.sin((index + offset) / 3.),
            yesterday_sentiment=("WEAK", "RETREAT", "UNKNOWN", "STRONG", "REPAIR", "DIVERGENCE")[index % 6]))
        if is_stock:
            statuses.append(dict(**common, instrument_id=instrument_id, trading_eligible=True, is_st=is_st,
                suspended=False, size_bucket="SMALL" if instrument_id == STOCKS[2] else "LARGE",
                previous_limit_up=False, previous_board_height=0))
            sector_id = "TEST_BANKING" if instrument_id in STOCKS[:2] else "TEST_INDUSTRIAL"
            memberships.append(dict(**common, instrument_id=instrument_id, classification="SW_LEVEL1",
                classification_version="TEST_FIXTURE_SW_LEVEL1_SYNTHETIC_V1", sector_id=sector_id,
                sector_name="合成银行样本" if instrument_id in STOCKS[:2] else "合成工业样本"))
    coverage = [dict(**common, trading_date=day.isoformat(), coverage="DECLARED_SCOPE_ONLY", instrument_ids=list(STOCKS))]
    body = {"label": LABEL, "source": "SYNTHETIC_PREOPEN_CONTEXT_NOT_ACTUAL_MARKET_PUBLICATION",
        "date": day.isoformat(), "reference_prices": references, "security_statuses": statuses,
        "sector_memberships": memberships, "daily_context": daily, "universe_coverage": coverage}
    raw = canonical_json(body).encode()
    source_id = hashlib.sha256(raw).hexdigest()
    receipt = SourceReceipt(source_id=source_id, provider="mock", source_timestamp=preopen,
        source_timestamp_basis="PUBLISHED", received_at=preopen, available_at=preopen, pit_status=LABEL)
    def records(model, rows):
        return tuple(model(source_id=source_id, **row) for row in rows)
    bundle = ContextBundle(receipts=(receipt,), reference_prices=records(ReferencePrice, references),
        security_statuses=records(SecurityStatus, statuses), sector_memberships=records(SectorMembership, memberships),
        daily_context=records(DailyContext, daily), universe_coverage=records(UniverseCoverage, coverage))
    return bundle, {source_id: raw}


def _path_return(pattern, progress, offset, *, stock=False):
    phase = offset * .31
    wobble = .0004 * math.sin(progress * math.pi * 12 + phase)
    if pattern == "TREND_UP":
        return .010 * progress + wobble
    if pattern == "TREND_DOWN":
        return -.010 * progress + wobble
    if pattern == "RANGE":
        return .0025 * math.sin(progress * math.pi * 4 + phase) + wobble
    if pattern == "HIGH_OPEN_FADE":
        return -.013 * progress + wobble
    if pattern == "REPAIR":
        return .010 * progress - .002 * math.sin(progress * math.pi) + wobble
    slope = (.016, -.009, -.014)[offset - len(INDICES)] if stock else .002 + offset * .0003
    return slope * progress + .001 * math.sin(progress * math.pi * 3 + phase)


def _day_bars(calendar, scope, day, previous_close, day_index, rng):
    """One receipt at each completed minute, shared by the eight declared streams."""
    pattern = PATTERNS[day_index % len(PATTERNS)]
    gap = .008 if pattern == "HIGH_OPEN_FADE" else -.004 if pattern == "REPAIR" else .0005 * math.sin(day_index)
    last = {identity: price * (1 + gap) for identity, price in previous_close.items()}
    bars, receipts, batches = [], {}, []
    for minute_index, timestamp in enumerate(calendar.minutes(day), 1):
        rows = []
        for offset, item in enumerate(scope.instruments):
            identity = item.instrument_id
            opening = previous_close[identity] * (1 + gap)
            movement = _path_return(pattern, minute_index / 240., offset, stock=item.asset_type == "STOCK")
            price = opening * (1 + movement + rng.uniform(-.00003, .00003))
            spread = opening * .00004
            volume = float(round((120000 if item.asset_type == "INDEX" else 5000) *
                (1.2 + .6 * math.cos(minute_index / 240. * math.pi * 2)) * (1 + offset / 20.)))
            rows.append(dict(instrument_id=identity, exchange=identity[-2:], symbol=identity[:6],
                asset_type=item.asset_type, timestamp=timestamp.isoformat(), open=last[identity],
                high=max(last[identity], price) + spread, low=min(last[identity], price) - spread, close=price,
                volume=volume, turnover=volume * price, provider="mock", quality="VALID",
                source_timestamp=timestamp.isoformat(), source_timestamp_basis="PUBLISHED",
                available_at=timestamp.isoformat(), pit_status=LABEL))
            last[identity] = price
        raw = canonical_json({"label": LABEL, "source": "DETERMINISTIC_SYNTHETIC_MINUTE_V1", "rows": rows}).encode()
        source_id = hashlib.sha256(raw).hexdigest()
        batch = tuple(MinuteBar(source_id=source_id, **row) for row in rows)
        receipt = SourceReceipt(source_id=source_id, provider="mock", source_timestamp=timestamp,
            source_timestamp_basis="PUBLISHED", received_at=timestamp, available_at=timestamp, pit_status=LABEL)
        bars.extend(batch)
        receipts[source_id] = receipt
        batches.append((batch, receipt, raw))
    return bars, receipts, batches, last, pattern


def create_demo(root: Path, day: date, *, history_sessions: int = 60, feature_version="MARKET_FEATURES_V2") -> dict:
    """Build a new isolated fixture with real feature/label processing paths.

    Dates are explicitly synthesized consecutive calendar dates, including
    weekends. They are NOT an official exchange calendar. Sixty sessions are
    an engineering demonstration and cannot establish a prediction advantage.
    """
    if not isinstance(day, date) or isinstance(day, datetime):
        raise LiveError("DEMO_DATE_INVALID")
    if isinstance(history_sessions, bool) or not isinstance(history_sessions, int) or history_sessions < 1:
        raise LiveError("DEMO_HISTORY_SESSIONS_INVALID")
    if feature_version not in ("MARKET_FEATURES_V1", "MARKET_FEATURES_V2"):
        raise LiveError("FEATURE_VERSION_MISMATCH")
    root = Path(root)
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise LiveError("FIXTURE_DESTINATION_EXISTS") from exc
    # Delayed imports let the generator use the project's immutable stores;
    # it never creates an alternate JSON label database.
    from ..storage.context_store import ContextStore
    from ..storage.research_store import ResearchStore
    started = perf_counter()
    first = day - timedelta(days=history_sessions)
    sessions = tuple(first + timedelta(days=i) for i in range(history_sessions + 1))
    calendar = TradingCalendar(schema_version="LIVE_CALENDAR_V1", sessions=sessions,
        coverage_start=first, coverage_end=day, exchanges=("SH", "SZ"), available_at=_moment(first),
        source="TEST_FIXTURE_EXPLICIT_SYNTHETIC_CONSECUTIVE_DATES_NOT_OFFICIAL_EXCHANGE_CALENDAR", pit_status=LABEL)
    scope = DatasetScope(calendar_version=calendar.version, available_at=_moment(first), pit_status=LABEL,
        instruments=tuple(Instrument(instrument_id=identity, asset_type="INDEX" if identity in INDICES else "STOCK")
                          for identity in INDICES + STOCKS))
    (root / "calendar.json").write_text(canonical_json(calendar) + "\n", encoding="utf-8")
    (root / "dataset.json").write_text(canonical_json(scope) + "\n", encoding="utf-8")
    config = ForecastConfig(feature_version=feature_version)
    (root / "research-config.json").write_text(canonical_json({"schema_version": "LIVE_RESEARCH_CONFIG_V1",
        "forecast": config.model_dump(mode="json")}) + "\n", encoding="utf-8")
    minute_store, research_store = ParquetStore(root), ResearchStore(root)
    previous_close = {identity: float((3000, 4000, 6000, 6500, 2500, 10, 15, 20)[i])
                      for i, identity in enumerate(INDICES + STOCKS)}
    rng = random.Random(SEED)
    bundles, raw_context, daily_resources = [], {}, []
    total_bars = total_states = total_outcomes = total_path_points = 0
    try:
        for day_index, session in enumerate(sessions):
            day_started = perf_counter()
            bundle, raw = _day_context(session, previous_close, day_index)
            bundles.append(bundle)
            raw_context.update(raw)
            bars, receipts, batches, previous_close, pattern = _day_bars(calendar, scope, session, previous_close, day_index, rng)
            minute_store.append_many(batches)
            total_bars += len(bars)
            states = []
            if session < day:
                engine = (StreamingMarketStateEngine if feature_version == "MARKET_FEATURES_V2" else MarketStateEngine)(calendar, scope, context=bundle)
                for tick in calendar.ticks(session):
                    if feature_version == "MARKET_FEATURES_V2":
                        delta = [bar for bar in bars if bar.timestamp == tick]
                        market_state = engine.build(tick, delta, {bar.source_id: receipts[bar.source_id] for bar in delta})
                    if tick.minute % 5 or tick.time() < time(9, 45) or tick.time() > time(14, 55):
                        continue
                    if feature_version == "MARKET_FEATURES_V1":
                        prefix = [bar for bar in bars if bar.timestamp <= tick and bar.available_at <= tick]
                        prefix_parents = {bar.source_id: receipts[bar.source_id] for bar in prefix}
                        market_state = engine.build(tick, prefix, prefix_parents)
                    for identity in INDICES:
                        item = state_from_market_state(market_state, identity, tick)
                        if item is not None:
                            states.append(item)
                outcomes = mature_outcomes(states, bars, tuple(receipts.values()), calendar, as_of=_moment(session, 15))
                research_store.append("features", states)
                research_store.append("outcomes", outcomes)
                total_states += len(states)
                total_outcomes += len(outcomes)
                points = sum(len(outcome.future_path) for outcome in outcomes)
                total_path_points += points
            else:
                outcomes, points = (), 0
            daily_resources.append({"date": session.isoformat(), "pattern": pattern,
                "minute_bars": len(bars), "historical_states": len(states), "mature_outcomes": len(outcomes),
                "future_path_points": points, "generation_seconds": round(perf_counter() - day_started, 3)})
        combined = ContextBundle(**{name: tuple(record for bundle in bundles for record in getattr(bundle, name))
                                    for name in ContextBundle.model_fields if name != "schema_version"})
        ContextStore(root).save(combined, raw_context)
        report = {"schema_version": "LIVE_DEMO_REPORT_V1", "label": LABEL, "date": day.isoformat(), "feature_version": feature_version,
            "seed": SEED, "history_sessions": history_sessions, "synthetic_sessions": len(sessions),
            "calendar_status": "SYNTHETIC_EXPLICIT_DATES_NOT_OFFICIAL", "scope": "DECLARED_SCOPE_ONLY",
            "instruments": len(scope.instruments), "rows": total_bars, "historical_states": total_states,
            "mature_outcomes": total_outcomes, "future_path_points": total_path_points,
            "target_day_historical_labels": 0, "turnover_history_status": "UNAVAILABLE_NOT_GENERATED",
            "investment_evaluation": "NOT_EVALUATED", "market_edge": "NOT_ESTABLISHED",
            "parameters_status": "UNVALIDATED", "generation_seconds": round(perf_counter() - started, 3),
            "daily_resources": daily_resources, "data_root": str(root.absolute())}
        (root / "demo-report.json").write_text(canonical_json(report) + "\n", encoding="utf-8")
        return report
    except BaseException:
        try:
            (root / "FAILED_DEMO").write_text("TEST_FIXTURE_DEMO_INCOMPLETE\n", encoding="utf-8")
        except OSError:
            pass
        raise
