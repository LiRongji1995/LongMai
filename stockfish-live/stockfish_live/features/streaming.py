"""Append-only MARKET_FEATURES_V2 with bounded per-security minute memory.

V1 remains the stateless reference. V2 binds the actual sequence of observed
cutoffs, deltas and context captures in a rolling chain; old views are immutable.
"""
from __future__ import annotations

from collections import deque
from copy import copy
from dataclasses import dataclass, field
from datetime import datetime
from math import sqrt
from statistics import mean

from ..common import LiveError, aware, digest
from ..contracts.market import MinuteBar, SourceReceipt, validate_receipt
from ..contracts.quality import effective_pit_status
from .breadth import calculate_breadth
from .market import MarketStateEngine
from .sector import calculate_sectors


@dataclass
class _Accumulator:
    count: int = 0
    first_open: float | None = None
    last_index: int = 0
    latest: MinuteBar | None = None
    high: float | None = None
    low: float | None = None
    observed_max: float | None = None
    observed_min: float | None = None
    ohlc_complete: bool = True
    turnover: float = 0.0
    turnover_complete: bool = True
    sumsq: float = 0.0
    change_count: int = 0
    recent: deque = field(default_factory=lambda: deque(maxlen=31))
    changes: deque = field(default_factory=lambda: deque(maxlen=15))

    def staged(self):
        result = copy(self)
        result.recent, result.changes = deque(self.recent, maxlen=31), deque(self.changes, maxlen=15)
        return result

    def append(self, index, bar):
        if index <= self.last_index:
            raise LiveError("STREAM_LATE_OR_DUPLICATE_BAR")
        if index == 1:
            self.first_open = bar.open
        if (self.latest is not None and self.last_index == index - 1
            and self.latest.close is not None and bar.close is not None):
            change = (bar.close / self.latest.close - 1) * 100
            self.changes.append((bar.timestamp.isoformat(), change))
            self.sumsq += change * change
            self.change_count += 1
        self.count += 1
        self.last_index, self.latest = index, bar
        self.recent.append((index, bar))
        observed = [value for value in (bar.open, bar.high, bar.low, bar.close) if value is not None]
        if observed:
            self.observed_max = max(self.observed_max, max(observed)) if self.observed_max is not None else max(observed)
            self.observed_min = min(self.observed_min, min(observed)) if self.observed_min is not None else min(observed)
        self.ohlc_complete &= bar.high is not None and bar.low is not None
        if bar.high is not None:
            self.high = max(self.high, bar.high) if self.high is not None else bar.high
        if bar.low is not None:
            self.low = min(self.low, bar.low) if self.low is not None else bar.low
        if bar.turnover is None:
            self.turnover_complete = False
        else:
            self.turnover += bar.turnover

    def metrics(self, identity, minute_index, reference, historical, daily):
        price = self.latest.close if self.latest is not None and self.last_index == minute_index else None
        opening = self.first_open
        previous_close = reference.previous_close if reference is not None else None
        if reference is not None and (
            (reference.limit_up is not None and self.observed_max is not None and self.observed_max > reference.limit_up + 1e-8)
            or (reference.limit_down is not None and self.observed_min is not None and self.observed_min < reference.limit_down - 1e-8)
        ):
            raise LiveError("PRICE_OUTSIDE_CONTEXT_LIMITS")
        complete = minute_index > 0 and self.count == minute_index
        high, low = (self.high, self.low) if complete and self.ohlc_complete else (None, None)
        turnover = self.turnover if complete and self.turnover_complete else None
        baseline = mean(record.cumulative_turnover for record in historical) if historical else None
        features = {
            "gap_pct": (opening / previous_close - 1) * 100 if opening is not None and previous_close is not None else None,
            "return_pct": (price / previous_close - 1) * 100 if price is not None and previous_close is not None else None,
            "return_from_open_pct": (price / opening - 1) * 100 if price is not None and opening is not None else None,
            "range_pct": (high - low) / previous_close * 100 if high is not None and previous_close is not None else None,
            "drawdown_pct": (price / high - 1) * 100 if price is not None and high is not None else None,
            "range_position": (price - low) / (high - low) if price is not None and high is not None and high > low else None,
            "turnover_ratio": turnover / baseline if turnover is not None and baseline is not None and baseline > 0 else None,
            "realized_volatility_pct": sqrt(self.sumsq) if complete and self.change_count == minute_index - 1 and self.change_count else None,
            "long_return_20d_pct": daily.long_return_20d_pct if daily is not None else None,
            "medium_return_5d_pct": daily.medium_return_5d_pct if daily is not None else None,
        }
        recent = {index: bar.close for index, bar in self.recent}
        for horizon in (5, 15, 30):
            first = minute_index - horizon
            valid = first > 0 and all(recent.get(index) is not None for index in range(first, minute_index + 1))
            features[f"return_{horizon}m_pct"] = (price / recent[first] - 1) * 100 if price is not None and valid else None
        return {"instrument_id": identity, "status": "AVAILABLE" if price is not None else "UNAVAILABLE",
            "current_price": price, "last_event_time": self.latest.timestamp.isoformat() if self.latest is not None else None,
            "previous_close": previous_close, "limit_up": reference.limit_up if reference is not None else None,
            "limit_down": reference.limit_down if reference is not None else None,
            "cumulative_turnover": turnover, "historical_turnover": baseline, "historical_turnover_days": len(historical),
            "path_complete": complete, "day_high": high, "day_low": low,
            "minute_returns": dict(self.changes), **features, "features": features}


class StreamingMarketStateEngine(MarketStateEngine):
    """Consume deltas exactly once, using monotonically increasing actual cutoffs.

    Previously accepted bars/parents passed the guard at an earlier cutoff;
    monotonic time proves they stay visible. New records are always revalidated.
    Features require observed prior snapshots for marginal changes; startup in
    the middle of a session cannot invent previously published state.
    """

    feature_version = "MARKET_FEATURES_V2"

    def __init__(self, calendar, scope, *, context=None, config=None):
        super().__init__(calendar, scope, context=context, config=config)
        self.config_hash = digest({"feature_version": self.feature_version, "base_config": self.config,
            "computation": "APPEND_ONLY_31_BAR_15_RETURN_CHAIN_V1", "past_views": "FIRST_OBSERVED_CUTOFF"})
        self._types = {item.instrument_id: item.asset_type for item in scope.instruments}
        self._stock_ids = tuple(identity for identity, kind in self._types.items() if kind == "STOCK")
        self._accumulators = {identity: _Accumulator() for identity in self._types}
        self._parents = {}
        self._last_as_of = None
        self._session_date = None
        self._minutes = ()
        self._positions = {}
        self._views = {}
        self._context_known = set()
        self._context_consumed = set()
        self._context_parent_hashes = {}
        self._pit_inputs = {scope.pit_status, calendar.pit_status}
        self._data_cutoff = None
        self._chain = digest({"feature_version": self.feature_version, "config_hash": self.config_hash,
                              "scope": scope, "calendar_version": calendar.version})

    def build(self, as_of: datetime, delta_bars, delta_receipts, *, context=None):
        now = aware(as_of)
        if self._last_as_of is not None and now <= self._last_as_of:
            raise LiveError("STREAM_CLOCK_NOT_ADVANCING")
        if self._session_date is not None and now.date() != self._session_date:
            raise LiveError("STREAM_SESSION_MISMATCH")
        if self.scope.available_at > now:
            raise LiveError("SCOPE_NOT_YET_AVAILABLE")
        if self.scope.calendar_version != self.calendar.version:
            raise LiveError("SCOPE_CALENDAR_MISMATCH")
        self.calendar.require_session(now.date(), now, tuple(identity[-2:] for identity in self._types))
        minutes = self._minutes or self.calendar.minutes(now.date())
        positions = self._positions or {minute: index for index, minute in enumerate(minutes, 1)}
        minute_index = sum(minute <= now for minute in minutes)
        bundle = self._validated_context(context if context is not None else self.context)
        context_hashes = {self._context_record(row)[1]: row for row in bundle.records()}
        if not self._context_consumed.issubset(context_hashes):
            raise LiveError("STREAM_CONTEXT_HISTORY_REMOVED")
        if self._last_as_of is not None:
            for key, row in context_hashes.items():
                if key not in self._context_known and row.available_at <= self._last_as_of and row.effective(self._last_as_of):
                    raise LiveError("STREAM_RETROACTIVE_CONTEXT_INPUT")
        ctx = self._context_view(bundle, now, minute_index)
        parents = dict(self._parents)
        new_parents = []
        supplied = delta_receipts.values() if hasattr(delta_receipts, "values") else delta_receipts
        for external in supplied:
            receipt = SourceReceipt.model_validate(external.model_dump() if isinstance(external, SourceReceipt) else external)
            if any(value is not None and value > now for value in (
                receipt.available_at, receipt.received_at, receipt.source_timestamp
            )):
                raise LiveError("FUTURE_ACCESS_DENIED")
            previous = parents.get(receipt.source_id)
            if previous is not None and previous != receipt:
                raise LiveError("SOURCE_RECEIPT_CONFLICT")
            parents[receipt.source_id] = receipt
            new_parents.append(receipt)
        staged_bars = [MinuteBar.model_validate(bar.model_dump() if isinstance(bar, MinuteBar) else bar) for bar in delta_bars]
        staged_bars.sort(key=lambda bar: (bar.timestamp, bar.instrument_id))
        accumulators = dict(self._accumulators)
        staged_instruments = set()
        for bar in staged_bars:
            if self._types.get(bar.instrument_id) != bar.asset_type:
                raise LiveError("INSTRUMENT_TYPE_MISMATCH")
            if bar.timestamp.date() != now.date():
                raise LiveError("STREAM_SESSION_MISMATCH")
            if bar.timestamp not in positions:
                raise LiveError("INVALID_BAR_TIME")
            if bar.price_adjustment != "RAW":
                raise LiveError("FEATURE_REQUIRES_RAW")
            if bar.quality == "INVALID":
                raise LiveError("INVALID_FEATURE_INPUT")
            parent = parents.get(bar.source_id)
            if parent is None:
                raise LiveError("SOURCE_RECEIPT_MISSING")
            validate_receipt(bar, parent, now)
            if bar.instrument_id not in staged_instruments:
                accumulators[bar.instrument_id] = accumulators[bar.instrument_id].staged()
                staged_instruments.add(bar.instrument_id)
            accumulators[bar.instrument_id].append(positions[bar.timestamp], bar)
        metrics = {identity: accumulator.metrics(identity, minute_index, ctx["references"].get(identity),
            ctx["historical"].get(identity, ()), ctx["daily"].get(identity)) for identity, accumulator in accumulators.items()}
        breadth = calculate_breadth(metrics, self._stock_ids, ctx["statuses"], universe=ctx["universe"], exclude_st=self.config.exclude_st)
        past_sectors = {horizon: self._views.get(minute_index - horizon, {}).get("sectors", {}) for horizon in (5, 15, 30)}
        sectors = calculate_sectors(metrics, self._stock_ids, ctx["statuses"], ctx["memberships"], breadth,
            past=past_sectors, exclude_st=self.config.exclude_st, change_threshold=self.config.sector_marginal_change_threshold_pct,
            strong_threshold=self.config.sector_strong_relative_threshold_pct)
        previous, prior = self._views.get(minute_index - 5, {}), self._views.get(minute_index - 10, {})
        indices, sentiment, environments, features, warnings = self._assemble(ctx, metrics, breadth, sectors,
            previous.get("breadth"), prior.get("breadth"), previous.get("index_return"))
        used_hashes = {self._context_record(row)[1] for row in ctx["used_records"]}
        used_ids = {row.source_id for row in ctx["used_records"]}
        context_parents = [row for row in bundle.receipts if row.source_id in used_ids]
        context_parent_hashes = dict(self._context_parent_hashes)
        for row in context_parents:
            parent_hash = digest(row)
            if row.source_id in context_parent_hashes and context_parent_hashes[row.source_id] != parent_hash:
                raise LiveError("STREAM_CONTEXT_RECEIPT_CONFLICT")
            context_parent_hashes[row.source_id] = parent_hash
        pit_inputs = self._pit_inputs | {bar.pit_status for bar in staged_bars} | {parent.pit_status for parent in new_parents}
        pit_inputs |= {row.pit_status for row in ctx["used_records"]} | {row.pit_status for row in context_parents}
        pit_status = effective_pit_status(pit_inputs)
        context_view_hash = digest({"records": sorted(used_hashes), "receipts": sorted(digest(row) for row in context_parents)})
        chain = digest({"previous": self._chain, "as_of": now, "bars": staged_bars,
            "receipts": sorted(new_parents, key=lambda row: row.source_id), "context_view_hash": context_view_hash})
        cutoff = max((value for value in (self._data_cutoff, *(bar.available_at for bar in staged_bars),
            *(row.available_at for row in ctx["used_records"])) if value is not None), default=None)
        state = {"schema_version": "MARKET_STATE_V2", "feature_version": self.feature_version,
            "as_of": now.isoformat(), "data_cutoff": cutoff.isoformat() if cutoff else None,
            "input_hash": chain, "input_hash_kind": "APPEND_ONLY_DELTA_CONTEXT_CHAIN_V1", "config_hash": self.config_hash,
            "pit_status": pit_status, "validation_status": self.config.validation_status, "market_edge": "NOT_ESTABLISHED",
            "scope": "DECLARED_SCOPE_ONLY", "completed_trading_minutes": minute_index,
            "indices": indices, "breadth": breadth, "sentiment": sentiment, "sectors": sectors,
            "environments": environments, "features": features,
            "unavailable_features": sorted(key for key, value in features.items() if value is None), "warnings": sorted(warnings)}
        state["state_id"] = digest(state)
        # Commit only after all contracts, context, features and provenance pass.
        self._accumulators, self._parents = accumulators, parents
        self._last_as_of, self._session_date = now, now.date()
        self._minutes, self._positions = minutes, positions
        self._chain, self._pit_inputs, self._data_cutoff = chain, pit_inputs, cutoff
        self._context_known, self._context_consumed = set(context_hashes), self._context_consumed | used_hashes
        self._context_parent_hashes = context_parent_hashes
        if minute_index not in self._views:
            self._views[minute_index] = {"breadth": dict(breadth),
                "sectors": {(row["classification_version"], row["sector_id"]): {
                    "members": list(row["members"]), "relative_strength_vs_market_pct": row["relative_strength_vs_market_pct"],
                    "advance_ratio": row["advance_ratio"]} for row in sectors["sectors"]},
                "index_return": indices["000001.SH"]["features"]["return_pct"]}
        self._views = {index: value for index, value in self._views.items() if index >= minute_index - 30}
        return state
