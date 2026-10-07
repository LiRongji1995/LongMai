"""Explainable V1 warnings. Thresholds are initial, unvalidated parameters."""
from ..common import digest

RULES = {"version": "LIVE_WARNING_RULES_V1", "validation_status": "INITIAL_UNVALIDATED",
    "index_up_pct": .3, "weak_advance_ratio": .35, "low_turnover_ratio": .8,
    "sector_leader_pct": 2., "sector_weak_breadth": .5}


def market_warnings(state, forecasts, revisions, quality):
    warnings = []
    def add(code, evidence, severity="WATCH"):
        warnings.append({"code": code, "severity": severity, "rule_version": RULES["version"],
            "configuration_hash": digest(RULES), "validation_status": RULES["validation_status"],
            "evidence": evidence})
    breadth = state["breadth"]
    for identity, index in state["indices"].items():
        change = index.get("features", {}).get("return_pct")
        ratio = index.get("features", {}).get("turnover_ratio")
        if change is not None and change >= RULES["index_up_pct"]:
            if breadth["status"] == "AVAILABLE" and breadth["advance_ratio"] <= RULES["weak_advance_ratio"]:
                add("INDEX_BREADTH_DIVERGENCE", {"instrument_id": identity, "index_return_pct": change,
                    "advance_ratio": breadth["advance_ratio"], "scope": breadth["scope"]})
            if ratio is not None and ratio < RULES["low_turnover_ratio"]:
                add("LOW_VOLUME_RALLY", {"instrument_id": identity, "return_pct": change, "turnover_ratio": ratio})
    for sector in state["sectors"]["sectors"]:
        leader, advance = sector["leader_return_pct"], sector["advance_ratio"]
        if leader is not None and advance is not None and leader >= RULES["sector_leader_pct"] and advance < RULES["sector_weak_breadth"]:
            add("SECTOR_LEADER_WITHOUT_BREADTH", {"sector_id": sector["sector_id"],
                "leader_return_pct": leader, "advance_ratio": advance})
    for code in quality.get("warnings", ()):
        if code in ("DATA_STALE", "DATA_GAP", "DATA_PARTIAL"):
            add(code, {"quality": quality["quality"], "gap_count": quality.get("gap_count")}, "DATA")
    for identity, forecast in forecasts.items():
        for code in ("MODEL_LOW_CONFIDENCE", "REGIME_DISAGREEMENT", "PREDICTION_INTERVAL_WIDE", "SIMILARITY_DISTANCE_HIGH"):
            if code in forecast.warnings:
                add(code, {"instrument_id": identity, "forecast_id": forecast.forecast_id,
                    "confidence": forecast.confidence}, "MODEL")
    for identity, revision in revisions.items():
        for code in revision["warnings"]:
            add(code, {"instrument_id": identity, "revision_id": revision["revision_id"]})
    return warnings
