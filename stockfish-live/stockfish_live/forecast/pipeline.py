"""Shared replay/live feature and forecasting pipeline, loaded before open."""
from __future__ import annotations

from datetime import datetime, time

from ..common import LiveError, aware, digest
from ..contracts.context import ContextBundle
from ..contracts.forecast import ForecastConfig
from ..features.market import INDEX_IDS, MarketStateEngine
from ..features.streaming import StreamingMarketStateEngine
from .engine import ForecastEngine, state_from_market_state
from .revision import PreparedRevisionHistory
from .warnings import RULES, market_warnings

SENTIMENTS = ("WEAK", "REPAIR", "EXPANSION", "STRONG", "DIVERGENCE", "RETREAT")


class MarketResearchPipeline:
    def __init__(self, calendar, scope, *, context: ContextBundle | None = None,
                 history=(), outcomes=(), config: ForecastConfig | None = None,
                 early_forecasts=False, minimum_transition_samples=10):
        if minimum_transition_samples < 1:
            raise ValueError("positive transition denominator required")
        config = config or ForecastConfig()
        if config.feature_version not in ("MARKET_FEATURES_V1", "MARKET_FEATURES_V2"):
            raise LiveError("FEATURE_VERSION_MISMATCH")
        self.consumes_deltas = config.feature_version == "MARKET_FEATURES_V2"
        self.market = (StreamingMarketStateEngine if self.consumes_deltas else MarketStateEngine)(calendar, scope, context=context)
        self.feature_version = self.market.feature_version if self.consumes_deltas else self.market.config.feature_version
        self.market_config_hash = self.market.config_hash if self.consumes_deltas else self.market.config.config_hash
        self.forecast = ForecastEngine(calendar, config)
        self.revision = PreparedRevisionHistory()
        from ..contracts.forecast import HistoricalState
        self.history = tuple(HistoricalState.model_validate(item.model_dump()) for item in history)
        self.prepared = self.forecast.prepare_history(self.history, outcomes)
        self.early = early_forecasts
        self.minimum_transition_samples = minimum_transition_samples
        self.latest, self.revisions = {}, {}
        self.feature_states, self.forecasts = [], []
        self._future_states = {}
        for item in self.history:
            key = (item.instrument_id, item.decision_at, item.feature_version, item.context.get("configuration_hash"))
            if key in self._future_states and self._future_states[key] != item:
                raise LiveError("AMBIGUOUS_HISTORY_FEATURE_STATE")
            self._future_states[key] = item
        self.config_hash = digest({"schema_version": "LIVE_PIPELINE_V1",
            "features": self.market.config, "feature_engine_config_hash": self.market_config_hash,
            "forecast": self.forecast.config, "warnings": RULES,
            "early_forecasts": early_forecasts, "minimum_transition_samples": minimum_transition_samples,
            "prepared_history_hash": self.prepared.history_hash})

    def _transitions(self, state, as_of):
        forecast = self.latest.get("000001.SH")
        horizon = next((h for h in forecast.horizons if h.horizon == "30m" and h.status == "AVAILABLE"), None) if forecast else None
        if horizon is None:
            return
        next_states = []
        for neighbor in horizon.neighbors:
            target = neighbor.outcome.target_at
            future = self._future_states.get(("000001.SH", target, self.feature_version,
                self.market_config_hash))
            if future is not None and future.available_at <= as_of and target < forecast.decision_at:
                if (future.pit_status == "TEST_FIXTURE") != (forecast.pit_status == "TEST_FIXTURE"):
                    raise LiveError("DATA_PROVENANCE_MIXED")
                next_states.append((neighbor, future))
        sentiment = state["sentiment"]
        values = [(n, future.context.get("sentiment", {}).get("state", "UNKNOWN")) for n, future in next_states
                  if n.context.get("sentiment", {}).get("state") == sentiment["state"]]
        valid = [(n, value) for n, value in values if value in SENTIMENTS]
        sentiment.update({"next_state_horizon_trading_minutes": 30, "next_state_sample_size": len(valid),
            "next_state_unknown_count": len(values) - len(valid), "next_state_confidence": "LOW",
            "next_state_method": "TOP10_SIMILAR_STATE_MATURE_30M_TRANSITIONS",
            "next_state_source_ids": [n.state_id for n, _ in valid]})
        if len(valid) >= self.minimum_transition_samples:
            sentiment["next_state_probabilities"] = {name: sum(value == name for _, value in valid) / len(valid) for name in SENTIMENTS}
            sentiment["next_state_status"] = "AVAILABLE_INITIAL_UNVALIDATED"
        for sector in state["sectors"]["sectors"]:
            current = sector["relative_strength_vs_market_pct"]
            labels, ids = [], []
            if current is None:
                continue
            for neighbor, future in next_states:
                def match(context):
                    return next((item for item in context.get("sectors", {}).get("sectors", ())
                        if item["sector_id"] == sector["sector_id"] and item["classification_version"] == sector["classification_version"]), None)
                before, after = match(neighbor.context), match(future.context)
                if before is None or after is None:
                    continue
                a, b = before["relative_strength_vs_market_pct"], after["relative_strength_vs_market_pct"]
                if a is None or b is None:
                    continue
                label = "WEAKENING" if b <= 0 else "STRONGER" if b > a + .1 else "DIVERGENCE" if b < a - .1 else "CONTINUE"
                labels.append(label)
                ids.append(neighbor.state_id)
            sector.update({"continuation_sample_size": len(labels), "continuation_horizon_trading_minutes": 30,
                "continuation_confidence": "LOW", "continuation_source_ids": ids,
                "continuation_method": "TOP10_SIMILAR_STATE_MATURE_30M_RELATIVE_STRENGTH",
                "continuation_threshold_pct": .1, "validation_status": "INITIAL_UNVALIDATED"})
            if len(labels) >= self.minimum_transition_samples:
                sector["continuation_probabilities"] = {name: labels.count(name) / len(labels)
                    for name in ("STRONGER", "CONTINUE", "DIVERGENCE", "WEAKENING")}
                sector["continuation_status"] = "AVAILABLE_INITIAL_UNVALIDATED"

    def process(self, now, bars, receipts, quality):
        now = aware(now)
        state = self.market.build(now, bars, receipts)
        state["warnings"] = sorted(set(state["warnings"]) | set(quality.get("warnings", ())))
        # Hash any collector quality annotation before constructing model inputs.
        state["state_id"] = digest({key: value for key, value in state.items() if key != "state_id"})
        scheduled = now.minute % 5 == 0 and (time(9, 35) if self.early else time(9, 45)) <= now.time() < time(15)
        created = {}
        if scheduled and quality["quality"] != "INVALID":
            for identity in INDEX_IDS:
                query = state_from_market_state(state, identity, now.replace(second=0, microsecond=0))
                if query is None:
                    continue
                prediction = self.forecast.predict(query, self.prepared, as_of=now)
                self.feature_states.append(query)
                self.revisions[identity] = self.revision.accept(prediction, as_of=now)
                self.latest[identity] = prediction
                created[identity] = prediction
                self.forecasts.append(prediction)
        base_id = state["state_id"]
        self._transitions(state, now)
        state["base_feature_state_id"] = base_id
        state["probability_lineage_forecast_id"] = self.latest.get("000001.SH").forecast_id if "000001.SH" in self.latest else None
        state["state_id"] = digest({key: value for key, value in state.items() if key != "state_id"})
        available = any(f.status in ("AVAILABLE", "PARTIAL") for f in self.latest.values())
        status = "AVAILABLE" if available else "INSUFFICIENT_HISTORY" if self.latest else "WAITING_FOR_DECISION"
        if quality["quality"] in ("INVALID", "STALE", "GAPPED"):
            status = "DATA_QUALITY_LIMITED"
        return {"market_state": state, "forecasts": {key: item.model_dump(mode="json") for key, item in created.items()},
            "forecast_record_semantics": "APPEND_ONLY_DECISION_DELTAS",
            "latest_forecast_ids": {key: item.forecast_id for key, item in self.latest.items()},
            "forecast_revisions": self.revisions.copy(), "forecast_status": status,
            "warnings": market_warnings(state, self.latest, self.revisions, quality)}

    def persist(self, store):
        store.append("features", self.feature_states)
        store.append("forecasts", self.forecasts)
