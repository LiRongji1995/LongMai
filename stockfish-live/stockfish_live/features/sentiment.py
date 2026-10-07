"""Live-only, versioned, explainable initial rules; no established edge."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ..common import FrozenModel, digest


class SentimentConfig(FrozenModel):
    version: Literal["SENTIMENT_TIDE_V1"] = "SENTIMENT_TIDE_V1"
    validation_status: Literal["INITIAL_UNVALIDATED"] = "INITIAL_UNVALIDATED"
    change_cohort_policy: Literal["SAME_QUALIFIED_INVENTORY"] = "SAME_QUALIFIED_INVENTORY"
    minimum_coverage: float = Field(default=1.0, gt=0, le=1)
    weak_advance_ratio: float = Field(default=0.35, ge=0, le=1)
    expansion_advance_ratio: float = Field(default=0.65, ge=0, le=1)
    strong_advance_ratio: float = Field(default=0.75, ge=0, le=1)
    positive_median_pct: float = 0.3
    strong_median_pct: float = 0.8
    negative_median_pct: float = -0.3
    repair_change: float = Field(default=0.1, gt=0)
    retreat_change: float = Field(default=0.15, gt=0)
    divergence_index_pct: float = Field(default=0.3, gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if not self.weak_advance_ratio < self.expansion_advance_ratio <= self.strong_advance_ratio:
            raise ValueError("sentiment advance thresholds must be ordered")
        if not self.negative_median_pct < self.positive_median_pct <= self.strong_median_pct:
            raise ValueError("sentiment median thresholds must be ordered")
        return self

    @property
    def config_hash(self):
        return digest(self)


def calculate_sentiment(breadth, *, previous=None, index_return_pct=None, config=None):
    config = config or SentimentConfig()
    ratio, central = breadth["advance_ratio"], breadth["median_return_pct"]
    coverage = breadth["return_coverage_ratio"]
    previous_ratio = previous["advance_ratio"] if previous is not None else None
    previous_complete = (previous is not None and previous["status"] == "AVAILABLE"
        and previous.get("qualified_inventory_hash") == breadth.get("qualified_inventory_hash"))
    change = ratio - previous_ratio if ratio is not None and previous_ratio is not None and previous_complete else None
    reasons = []
    state = "UNKNOWN"
    if (ratio is None or central is None or coverage is None or coverage < config.minimum_coverage
        or breadth["eligibility_unknown_count"]):
        reasons.append("BREADTH_SUPPORT_INSUFFICIENT")
    elif change is not None and change <= -config.retreat_change:
        state, reasons = "RETREAT", ["ADVANCE_RATIO_5M_DECLINING"]
    elif (index_return_pct is not None and index_return_pct >= config.divergence_index_pct
          and ratio <= config.weak_advance_ratio):
        state, reasons = "DIVERGENCE", ["INDEX_UP_BREADTH_WEAK"]
    elif ratio >= config.strong_advance_ratio and central >= config.strong_median_pct:
        state, reasons = "STRONG", ["BROAD_ADVANCE_AND_POSITIVE_MEDIAN"]
    elif ratio >= config.expansion_advance_ratio and central >= config.positive_median_pct:
        state, reasons = "EXPANSION", ["ADVANCE_RATIO_AND_MEDIAN_EXPANDING"]
    elif change is not None and change >= config.repair_change:
        state, reasons = "REPAIR", ["ADVANCE_RATIO_5M_IMPROVING"]
    elif ratio <= config.weak_advance_ratio or central <= config.negative_median_pct:
        state, reasons = "WEAK", ["LOW_ADVANCE_RATIO_OR_NEGATIVE_MEDIAN"]
    else:
        reasons.append("NO_RULE_CONFIRMED")
    fields = ("limit_up_count", "limit_down_count", "broken_limit_count", "max_board_height",
              "advance_ratio", "median_return_pct", "high_momentum_return_pct",
              "previous_limit_up_return_pct", "market_turnover_ratio", "small_vs_large_pct")
    return {"version": config.version, "config_hash": config.config_hash,
            "validation_status": config.validation_status, "scope": breadth["scope"],
            "state": state, "reasons": reasons, "advance_ratio_change_5m": change,
            "inputs": {key: breadth[key] for key in fields},
            "unavailable_inputs": [key for key in fields if breadth[key] is None]}
