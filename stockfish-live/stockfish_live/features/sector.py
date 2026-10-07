"""PIT SW level-one membership and marginal strength, never current backfills."""
from __future__ import annotations

from math import sqrt
from statistics import fmean, mean, median, pstdev

from ..common import digest
from .breadth import eligible


def _correlation(series):
    if len(series) < 2:
        return None
    keys = sorted(series[0])[-15:]
    if len(keys) < 5 or any(sorted(item)[-15:] != keys for item in series[1:]):
        return None  # pair-specific time windows are not one coherent statistic
    sums = [0.0] * len(keys)
    squared_norms = 0.0
    for item in series:
        values = [item[key] for key in keys]
        center = fmean(values)
        deviations = [value - center for value in values]
        norm = sqrt(sum(value * value for value in deviations))
        if norm == 0:
            return None
        for position, deviation in enumerate(deviations):
            standardized = deviation / norm
            sums[position] += standardized
            squared_norms += standardized * standardized
    # Expand ||sum(z_i)||²: every off-diagonal dot product is Pearson r.
    # This equals the mean of all n*(n-1)/2 pair correlations in O(n*t).
    count = len(series)
    correlation = (sum(value * value for value in sums) - squared_norms) / (count * (count - 1))
    return max(-1.0, min(1.0, correlation))


def marginal_state(relative, change, *, change_threshold=0.1, strong_threshold=0.3):
    if relative is None or change is None:
        return "UNKNOWN", "PIT_MARGINAL_SUPPORT_UNAVAILABLE"
    previous = relative - change
    if (previous <= 0 < relative or previous >= 0 > relative) and abs(change) >= change_threshold:
        return "REVERSING", "RELATIVE_STRENGTH_CROSSED_ZERO"
    if change >= change_threshold:
        return "STRENGTHENING", "RELATIVE_STRENGTH_5M_IMPROVING"
    if change <= -change_threshold:
        return "WEAKENING", "RELATIVE_STRENGTH_5M_DECLINING"
    if relative >= strong_threshold:
        return "STABLE_STRONG", "POSITIVE_RELATIVE_STRENGTH_STABLE"
    return "NEUTRAL", "NO_DIRECTIONAL_MARGINAL_RULE_CONFIRMED"


def calculate_sectors(metrics, stock_ids, statuses, memberships, market_breadth, *, past=None, exclude_st=True,
                      change_threshold=0.1, strong_threshold=0.3):
    qualified = [item for item in stock_ids if eligible(statuses.get(item), exclude_st=exclude_st) is True]
    groups = {}
    missing = []
    for item in qualified:
        member = memberships.get(item)
        if member is None:
            missing.append(item)
            continue
        identity = (member.classification_version, member.sector_id)
        groups.setdefault(identity, {"name": member.sector_name, "members": []})["members"].append(item)
    results = []
    for (classification_version, sector_id), group in sorted(groups.items()):
        members = sorted(group["members"])
        observed = [metrics[item]["return_pct"] for item in members if metrics[item]["return_pct"] is not None]
        complete = len(observed) == len(members)
        central = mean(observed) if complete else None
        market_mean = market_breadth["mean_return_pct"] if market_breadth["status"] == "AVAILABLE" else None
        relative = central - market_mean if central is not None and market_mean is not None else None
        advance = sum(value > 0 for value in observed) / len(observed) if complete else None
        turnovers = [metrics[item]["cumulative_turnover"] for item in members]
        historical = [metrics[item]["historical_turnover"] for item in members]
        turnover_ratio = (sum(turnovers) / sum(historical)
                          if all(value is not None for value in turnovers + historical) and sum(historical) > 0 else None)
        limits_known = all(metrics[item]["current_price"] is not None and metrics[item]["limit_up"] is not None for item in members)
        result = {"sector_id": sector_id, "sector_name": group["name"], "classification": "SW_LEVEL1",
                  "classification_version": classification_version,
                  "scope": "DECLARED_SCOPE_PIT_MEMBERS_ONLY", "status": "AVAILABLE" if complete else "PARTIAL",
                  "member_count": len(members), "members": members, "return_observed_count": len(observed),
                  "sector_return_pct": central, "sector_median_return_pct": median(observed) if complete else None,
                  "advance_ratio": advance, "volume_ratio": turnover_ratio,
                  "relative_strength_vs_market_pct": relative,
                  "leader_return_pct": max(observed) if observed else None,
                  "limit_up_count": sum(metrics[item]["current_price"] >= metrics[item]["limit_up"] - 1e-8 for item in members) if limits_known else None,
                  "dispersion_pct": pstdev(observed) if complete and len(observed) >= 2 else None,
                  "internal_correlation": _correlation([metrics[item]["minute_returns"] for item in members]) if complete else None}
        result["continuation_probabilities"] = None
        result["continuation_status"] = "UNAVAILABLE_NO_MATURE_LABELS"
        for horizon in (5, 15, 30):
            momentum = [metrics[item][f"return_{horizon}m_pct"] for item in members]
            result[f"momentum_{horizon}m_pct"] = mean(momentum) if all(value is not None for value in momentum) else None
            previous = (past or {}).get(horizon, {}).get((classification_version, sector_id))
            # Membership revisions and newly captured classifications cannot be
            # retroactively treated as a stable historical cohort.
            same_members = previous is not None and previous["members"] == members
            previous_relative = previous["relative_strength_vs_market_pct"] if same_members else None
            previous_advance = previous["advance_ratio"] if same_members else None
            result[f"relative_strength_change_{horizon}m_pct"] = (
                relative - previous_relative if relative is not None and previous_relative is not None else None)
            result[f"breadth_change_{horizon}m"] = (
                advance - previous_advance if advance is not None and previous_advance is not None else None)
        result["marginal_state"], result["marginal_reason"] = marginal_state(relative,
            result["relative_strength_change_5m_pct"], change_threshold=change_threshold, strong_threshold=strong_threshold)
        results.append(result)
    rules = {"version": "SECTOR_MARGINAL_RULES_V1", "change_threshold_pct": change_threshold,
             "strong_relative_threshold_pct": strong_threshold, "validation_status": "INITIAL_UNVALIDATED"}
    return {"version": "SECTOR_ENGINE_V1", "rule_config": rules, "rule_config_hash": digest(rules),
            "status": "AVAILABLE" if qualified and not missing and all(item["status"] == "AVAILABLE" for item in results)
            else "PARTIAL" if results else "UNAVAILABLE",
            "scope": "DECLARED_SCOPE_PIT_MEMBERS_ONLY", "qualified_count": len(qualified),
            "membership_observed_count": len(qualified) - len(missing), "membership_missing_count": len(missing),
            "sectors": results,
            "warnings": ["PIT_SECTOR_MEMBERSHIP_UNAVAILABLE"] if missing or not qualified else []}
