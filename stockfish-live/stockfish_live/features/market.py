"""Deterministic, PIT-first features over the collector's visible minute history."""
from __future__ import annotations

from datetime import datetime, time
import hashlib
from math import sqrt
from statistics import mean, pstdev
from typing import Literal, Mapping, Sequence

from pydantic import Field

from ..common import FrozenModel, LiveError, aware, canonical_json, digest
from ..contracts.context import ContextBundle, latest_records
from ..contracts.market import DatasetScope, MinuteBar, SourceReceipt, validate_receipt
from ..contracts.quality import effective_pit_status
from .breadth import calculate_breadth
from .sector import calculate_sectors
from .sentiment import SentimentConfig, calculate_sentiment

INDEX_IDS = ("000001.SH", "000300.SH", "000905.SH", "000852.SH", "399006.SZ")
INDEX_NAMES = ("上证指数", "沪深300", "中证500", "中证1000", "创业板指")


def _signature(value):
    # Fields here are immutable scalars. Type guards keep True distinct from
    # 1.0 when a caller bypasses Pydantic's frozen model with object internals.
    fields = value.__dict__
    return tuple(fields), tuple(fields.values()), tuple(map(type, fields.values()))


class MarketFeatureConfig(FrozenModel):
    feature_version: Literal["MARKET_FEATURES_V1"] = "MARKET_FEATURES_V1"
    validation_status: Literal["INITIAL_UNVALIDATED"] = "INITIAL_UNVALIDATED"
    exclude_st: bool = True
    turnover_history_min_days: int = Field(default=5, ge=1, le=120)
    long_trend_threshold_pct: float = Field(default=3.0, gt=0)
    medium_trend_threshold_pct: float = Field(default=1.0, gt=0)
    sector_marginal_change_threshold_pct: float = Field(default=0.1, gt=0)
    sector_strong_relative_threshold_pct: float = Field(default=0.3, gt=0)
    sentiment: SentimentConfig = Field(default_factory=SentimentConfig)

    @property
    def config_hash(self):
        return digest(self)


def _instrument_metrics(instrument_id, rows, expected, reference, historical, daily):
    by_time = {bar.timestamp: bar for bar in rows}
    ordered = [by_time[timestamp] for timestamp in expected if timestamp in by_time]
    current = by_time.get(expected[-1]) if expected else None
    price = current.close if current is not None else None
    first = by_time.get(expected[0]) if expected else None
    opening = first.open if first is not None else None
    previous_close = reference.previous_close if reference is not None else None
    if reference is not None:
        for bar in rows:
            for observed in (bar.open, bar.high, bar.low, bar.close):
                if observed is not None and (
                    (reference.limit_up is not None and observed > reference.limit_up + 1e-8)
                    or (reference.limit_down is not None and observed < reference.limit_down - 1e-8)
                ):
                    raise LiveError("PRICE_OUTSIDE_CONTEXT_LIMITS")
    full_path = bool(expected) and len(ordered) == len(expected)
    ohlc_complete = full_path and all(bar.high is not None and bar.low is not None for bar in ordered)
    high = max(bar.high for bar in ordered) if ohlc_complete else None
    low = min(bar.low for bar in ordered) if ohlc_complete else None
    turnover = sum(bar.turnover for bar in ordered) if full_path and all(bar.turnover is not None for bar in ordered) else None
    historical_turnover = mean([record.cumulative_turnover for record in historical]) if historical else None
    minute_returns = {}
    for previous, bar in zip(expected, expected[1:]):
        before, after = by_time.get(previous), by_time.get(bar)
        if before is not None and after is not None and before.close is not None and after.close is not None:
            minute_returns[bar.isoformat()] = (after.close / before.close - 1) * 100
    changes = list(minute_returns.values())
    features = {
        "gap_pct": (opening / previous_close - 1) * 100 if opening is not None and previous_close is not None else None,
        "return_pct": (price / previous_close - 1) * 100 if price is not None and previous_close is not None else None,
        "return_from_open_pct": (price / opening - 1) * 100 if price is not None and opening is not None else None,
        "range_pct": (high - low) / previous_close * 100 if high is not None and previous_close is not None else None,
        "drawdown_pct": (price / high - 1) * 100 if price is not None and high is not None else None,
        "range_position": (price - low) / (high - low) if price is not None and high is not None and high > low else None,
        "turnover_ratio": turnover / historical_turnover if turnover is not None and historical_turnover is not None and historical_turnover > 0 else None,
        "realized_volatility_pct": sqrt(sum(value * value for value in changes)) if full_path and len(changes) == len(expected) - 1 and changes else None,
        "long_return_20d_pct": daily.long_return_20d_pct if daily is not None else None,
        "medium_return_5d_pct": daily.medium_return_5d_pct if daily is not None else None,
    }
    for horizon in (5, 15, 30):
        baseline = by_time.get(expected[-1 - horizon]) if len(expected) > horizon else None
        window = expected[-1 - horizon:] if baseline is not None else ()
        window_complete = bool(window) and all(timestamp in by_time and by_time[timestamp].close is not None for timestamp in window)
        features[f"return_{horizon}m_pct"] = (price / baseline.close - 1) * 100 if price is not None and window_complete else None
    return {"instrument_id": instrument_id, "status": "AVAILABLE" if current is not None and price is not None else "UNAVAILABLE",
            "current_price": price, "last_event_time": rows[-1].timestamp.isoformat() if rows else None,
            "previous_close": previous_close, "limit_up": reference.limit_up if reference is not None else None,
            "limit_down": reference.limit_down if reference is not None else None,
            "cumulative_turnover": turnover, "historical_turnover": historical_turnover,
            "historical_turnover_days": len(historical), "path_complete": full_path,
            "day_high": high, "day_low": low, "minute_returns": minute_returns,
            **features, "features": features}


class MarketStateEngine:
    """Stateless rebuild: the same visible inputs produce the same state.

    ``bars`` is the accepted cumulative history for one session, not only the
    newest provider batch. Missing completed minutes remain missing.
    """

    def __init__(self, calendar, scope: DatasetScope, *, context: ContextBundle | None = None,
                 config: MarketFeatureConfig | None = None):
        self.calendar, self.scope = calendar, scope
        self.context = context or ContextBundle()
        self.config = config or MarketFeatureConfig()
        self._bar_cache = {}
        self._receipt_cache = {}
        self._context_cache = None
        self._context_record_cache = {}

    @staticmethod
    def _validated(external, model, cache):
        if not isinstance(external, model):
            validated = model.model_validate(external)
            return validated, canonical_json(validated)
        identity, signature = id(external), _signature(external)
        previous = cache.get(identity)
        if previous is None or previous[0] is not external or previous[1] != signature:
            validated = model.model_validate(external.model_dump())
            previous = (external, signature, validated, canonical_json(validated))
            cache[identity] = previous
        return previous[2], previous[3]

    def _validated_context(self, bundle):
        if not isinstance(bundle, ContextBundle):
            return ContextBundle.model_validate(bundle)
        if any(field != "schema_version" and (not isinstance(value, tuple)
            or any(not isinstance(item, FrozenModel) for item in value)) for field, value in bundle.__dict__.items()):
            # model_construct can bypass tuple typing with caller-mutable lists.
            # Such inputs are validated afresh; they never enter the cache.
            return ContextBundle.model_validate(bundle.model_dump())
        signature = tuple((field, tuple((type(item), _signature(item)) for item in value))
            if isinstance(value, tuple) else (field, type(value), value) for field, value in bundle.__dict__.items())
        if self._context_cache is None or self._context_cache[:2] != (id(bundle), signature):
            self._context_cache = (id(bundle), signature, ContextBundle.model_validate(bundle.model_dump()), bundle)
            self._context_record_cache.clear()
        return self._context_cache[2]

    def _context_record(self, row):
        # These are the engine's own validated copies, never caller-owned rows.
        cached = self._context_record_cache.get(id(row))
        if cached is None or cached[0] is not row:
            encoded = canonical_json(row)
            cached = (row, hashlib.sha256(encoded.encode()).hexdigest(), encoded)
            self._context_record_cache[id(row)] = cached
        return cached

    @staticmethod
    def _input_digest(values, bar_json, receipt_json, context_json):
        """Exactly canonical_json(values), with immutable row encodings cached."""
        hasher = hashlib.sha256()
        hasher.update(b"{")
        for position, key in enumerate(sorted(values)):
            if position:
                hasher.update(b",")
            hasher.update(canonical_json(key).encode())
            hasher.update(b":")
            if key in ("bars", "receipts", "context", "context_receipts"):
                encoded = bar_json if key == "bars" else receipt_json if key == "receipts" else context_json
                hasher.update(b"[")
                for row_no, row in enumerate(values[key]):
                    if row_no:
                        hasher.update(b",")
                    hasher.update(encoded[id(row)].encode())
                hasher.update(b"]")
            else:
                hasher.update(canonical_json(values[key]).encode())
        hasher.update(b"}")
        return hasher.hexdigest()

    def _context_view(self, bundle, now, minute_index):
        # _validated_context already checked every immutable row-parent pair:
        # parent clocks <= row.available_at. Filtering row availability thus
        # proves ALL parent clocks <= this cutoff, including newly visible rows.
        # Caller-owned model_copy/model_construct bundles never enter here.
        def visible(records):
            return tuple(row for row in records if row.available_at <= now and row.effective(now))
        day = now.date()
        reference_rows = tuple(row for row in visible(bundle.reference_prices) if row.trading_date == day)
        security_rows = visible(bundle.security_statuses)
        member_rows = visible(bundle.sector_memberships)
        history_rows = visible(bundle.turnover_history)
        daily_rows = visible(bundle.daily_context)
        universe_rows = tuple(row for row in visible(bundle.universe_coverage) if row.trading_date == day)
        if any(row.observation_date >= day for row in history_rows):
            raise LiveError("TURNOVER_HISTORY_NOT_HISTORICAL")
        if any(row.based_on_date >= day for row in daily_rows):
            raise LiveError("DAILY_CONTEXT_NOT_HISTORICAL")
        preopen = datetime.combine(day, time(9, 30), now.tzinfo)
        daily_rows = tuple(row for row in daily_rows if row.available_at <= preopen)
        history_rows = tuple(row for row in history_rows if row.minute_index == minute_index)
        histories = latest_records(history_rows, key=lambda row: (row.instrument_id, row.observation_date))
        historical = {}
        for row in histories.values():
            historical.setdefault(row.instrument_id, []).append(row)
        historical = {identity: sorted(rows, key=lambda row: row.observation_date)
                      for identity, rows in historical.items() if len(rows) >= self.config.turnover_history_min_days}
        universe = latest_records(universe_rows, key=lambda row: "universe").get("universe")
        return {"references": latest_records(reference_rows), "statuses": latest_records(security_rows),
                "memberships": latest_records(member_rows), "historical": historical,
                "daily": latest_records(daily_rows), "universe": universe,
                "used_records": (*reference_rows, *security_rows, *member_rows, *history_rows, *daily_rows, *universe_rows)}

    def _view(self, now, grouped, bundle, stock_ids):
        expected = tuple(timestamp for timestamp in self.calendar.minutes(now.date()) if timestamp <= now)
        context = self._context_view(bundle, now, len(expected))
        metrics = {}
        for item in self.scope.instruments:
            rows = [bar for bar in grouped[item.instrument_id] if bar.timestamp <= now and bar.available_at <= now]
            metrics[item.instrument_id] = _instrument_metrics(item.instrument_id, rows, expected,
                context["references"].get(item.instrument_id), context["historical"].get(item.instrument_id, ()),
                context["daily"].get(item.instrument_id))
        breadth = calculate_breadth(metrics, stock_ids, context["statuses"], universe=context["universe"], exclude_st=self.config.exclude_st)
        return expected, context, metrics, breadth

    def _assemble(self, ctx, metrics, breadth, sectors, past_breadth=None, prior_breadth=None, previous_index_return=None):
        types = {item.instrument_id: item.asset_type for item in self.scope.instruments}
        indices = {}
        warnings = set(breadth["warnings"]) | set(sectors["warnings"])
        for identity, name in zip(INDEX_IDS, INDEX_NAMES):
            item = metrics.get(identity) if types.get(identity) == "INDEX" else None
            if item is None:
                indices[identity] = {"instrument_id": identity, "name": name, "status": "UNAVAILABLE", "current_price": None,
                                     "features": {key: None for key in _instrument_metrics(identity, [], (), None, (), None)["features"]}}
                warnings.add("INDEX_NOT_IN_DECLARED_SCOPE")
            else:
                indices[identity] = {key: value for key, value in item.items() if key != "minute_returns"}
                indices[identity]["name"] = name
                if item["status"] != "AVAILABLE":
                    warnings.add("INDEX_DATA_UNAVAILABLE")
                if item["previous_close"] is None:
                    warnings.add("INDEX_REFERENCE_PRICE_UNAVAILABLE")
        sentiment = calculate_sentiment(breadth, previous=past_breadth,
            index_return_pct=indices["000001.SH"]["features"]["return_pct"], config=self.config.sentiment)
        sentiment["previous_state"] = calculate_sentiment(past_breadth, previous=prior_breadth,
            index_return_pct=previous_index_return, config=self.config.sentiment)["state"] if past_breadth is not None else "UNKNOWN"
        sentiment["previous_state_horizon_trading_minutes"] = 5
        yesterday = ctx["daily"].get("000001.SH")
        sentiment["yesterday_state"] = yesterday.yesterday_sentiment if yesterday is not None else "UNKNOWN"
        sentiment["next_state_probabilities"] = None
        sentiment["next_state_status"] = "UNAVAILABLE_NO_MATURE_LABELS"
        environments = {}
        for horizon, field, threshold in (("long", "long_return_20d_pct", self.config.long_trend_threshold_pct),
                                          ("medium", "medium_return_5d_pct", self.config.medium_trend_threshold_pct)):
            values = {identity: item["features"][field] for identity, item in indices.items()}
            anchor = values["000001.SH"]
            environments[horizon] = {"state": "UNKNOWN" if anchor is None else "UP" if anchor >= threshold
                else "DOWN" if anchor <= -threshold else "RANGE", "anchor_index": "000001.SH",
                "observed_returns_pct": values, "rule_threshold_pct": threshold,
                "validation_status": "INITIAL_UNVALIDATED", "probabilities": None,
                "probability_status": "UNAVAILABLE_NO_MATURE_LABELS", "source": "PREOPEN_DAILY_CONTEXT"}
        features = {}
        for identity, index in indices.items():
            features.update({f"index_{identity}_{key}": value for key, value in index["features"].items()})
        for key in ("advance_ratio", "decline_ratio", "mean_return_pct", "median_return_pct", "market_turnover_ratio", "small_vs_large_pct"):
            features[f"breadth_{key}"] = breadth[key] if breadth["status"] == "AVAILABLE" else None
        for state in ("WEAK", "REPAIR", "EXPANSION", "STRONG", "DIVERGENCE", "RETREAT"):
            features[f"sentiment_{state.lower()}"] = float(sentiment["state"] == state) if sentiment["state"] != "UNKNOWN" else None
        strengths = [item["relative_strength_vs_market_pct"] for item in sectors["sectors"]
                     if item["relative_strength_vs_market_pct"] is not None]
        features["sector_relative_strength_dispersion_pct"] = pstdev(strengths) if len(strengths) >= 2 else None
        return indices, sentiment, environments, features, warnings

    def build(self, as_of: datetime, bars: Sequence[MinuteBar], receipts: Mapping[str, SourceReceipt], *,
              context: ContextBundle | None = None) -> dict:
        now = aware(as_of)
        if self.scope.available_at > now:
            raise LiveError("SCOPE_NOT_YET_AVAILABLE")
        if self.scope.calendar_version != self.calendar.version:
            raise LiveError("SCOPE_CALENDAR_MISMATCH")
        self.calendar.require_session(now.date(), now, tuple(item.instrument_id[-2:] for item in self.scope.instruments))
        # Revalidate at the boundary, including model_construct/model_copy inputs.
        bundle = context if context is not None else self.context
        bundle = self._validated_context(bundle)
        parents, receipt_json = {}, {}
        for identity, parent in receipts.items():
            validated, encoded = self._validated(parent, SourceReceipt, self._receipt_cache)
            parents[identity], receipt_json[id(validated)] = validated, encoded
        types = {item.instrument_id: item.asset_type for item in self.scope.instruments}
        grouped = {identity: [] for identity in types}
        accepted = []
        bar_json = {}
        seen = set()
        for external in bars:
            bar, encoded = self._validated(external, MinuteBar, self._bar_cache)
            bar_json[id(bar)] = encoded
            if types.get(bar.instrument_id) != bar.asset_type:
                raise LiveError("INSTRUMENT_TYPE_MISMATCH")
            if bar.timestamp.date() != now.date():
                raise LiveError("FEATURE_SESSION_MISMATCH")
            self.calendar.validate_bar_time(bar.timestamp)
            if bar.price_adjustment != "RAW":
                raise LiveError("FEATURE_REQUIRES_RAW")
            if bar.quality == "INVALID":
                raise LiveError("INVALID_FEATURE_INPUT")
            parent = parents.get(bar.source_id)
            if parent is None:
                raise LiveError("SOURCE_RECEIPT_MISSING")
            validate_receipt(bar, parent, now)
            identity = (bar.instrument_id, bar.timestamp)
            if identity in seen:
                raise LiveError("DUPLICATE_FEATURE_BAR")
            seen.add(identity)
            accepted.append(bar)
            grouped[bar.instrument_id].append(bar)
        for rows in grouped.values():
            rows.sort(key=lambda bar: bar.timestamp)
        stock_ids = tuple(identity for identity in types if types[identity] == "STOCK")
        expected, ctx, metrics, breadth = self._view(now, grouped, bundle, stock_ids)
        consumed_context = {self._context_record(row)[1]: row for row in ctx["used_records"]}
        past_breadth = None
        prior_breadth = None
        previous_index_return = None
        past_sectors = {}
        for horizon in (5, 10, 15, 30):
            if len(expected) <= horizon:
                continue
            past_now = expected[-1 - horizon]
            _, previous_context, previous_metrics, previous_breadth = self._view(past_now, grouped, bundle, stock_ids)
            consumed_context.update({self._context_record(row)[1]: row for row in previous_context["used_records"]})
            if horizon == 5:
                past_breadth = previous_breadth
                previous_index_return = previous_metrics.get("000001.SH", {}).get("return_pct")
            if horizon == 10:
                prior_breadth = previous_breadth
                continue
            previous_sectors = calculate_sectors(previous_metrics, stock_ids, previous_context["statuses"],
                previous_context["memberships"], previous_breadth, exclude_st=self.config.exclude_st,
                change_threshold=self.config.sector_marginal_change_threshold_pct,
                strong_threshold=self.config.sector_strong_relative_threshold_pct)
            past_sectors[horizon] = {(item["classification_version"], item["sector_id"]): item for item in previous_sectors["sectors"]}
        pit_status = effective_pit_status([self.scope.pit_status, self.calendar.pit_status,
            *(bar.pit_status for bar in accepted), *(row.pit_status for row in consumed_context.values())])
        sectors = calculate_sectors(metrics, stock_ids, ctx["statuses"], ctx["memberships"], breadth,
            past=past_sectors, exclude_st=self.config.exclude_st, change_threshold=self.config.sector_marginal_change_threshold_pct,
            strong_threshold=self.config.sector_strong_relative_threshold_pct)
        indices, sentiment, environments, features, warnings = self._assemble(
            ctx, metrics, breadth, sectors, past_breadth, prior_breadth, previous_index_return)
        input_parents = [parents[identity] for identity in sorted({bar.source_id for bar in accepted})]
        context_ids = {row.source_id for row in consumed_context.values()}
        context_parents = sorted((parent for parent in bundle.receipts if parent.source_id in context_ids), key=lambda parent: parent.source_id)
        context_json = {id(row): self._context_record(row)[2] for row in (*consumed_context.values(), *context_parents)}
        input_hash = self._input_digest({"bars": sorted(accepted, key=lambda bar: (bar.timestamp, bar.instrument_id)),
            "receipts": input_parents, "context": [consumed_context[key] for key in sorted(consumed_context)],
            "context_receipts": context_parents, "scope": self.scope, "calendar": self.calendar.version,
            "config_hash": self.config.config_hash}, bar_json, receipt_json, context_json)
        cutoff = max((row.available_at for row in (*accepted, *consumed_context.values())), default=None)
        state = {"schema_version": "MARKET_STATE_V1", "feature_version": self.config.feature_version,
                 "as_of": now.isoformat(), "data_cutoff": cutoff.isoformat() if cutoff else None,
                 "input_hash": input_hash, "config_hash": self.config.config_hash, "pit_status": pit_status,
                 "validation_status": self.config.validation_status, "market_edge": "NOT_ESTABLISHED",
                 "scope": "DECLARED_SCOPE_ONLY", "completed_trading_minutes": len(expected),
                 "indices": indices, "breadth": breadth, "sentiment": sentiment, "sectors": sectors,
                 "environments": environments,
                 "features": features, "unavailable_features": sorted(key for key, value in features.items() if value is None),
                 "warnings": sorted(warnings)}
        state["state_id"] = digest(state)
        return state
