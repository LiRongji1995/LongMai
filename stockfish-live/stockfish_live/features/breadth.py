"""Breadth with explicit eligibility and separately evidenced denominators."""
from __future__ import annotations

from statistics import mean, median

from ..common import digest

def eligible(status, *, exclude_st=True):
    if status is None:
        return None
    if status.trading_eligible is False or status.suspended is True or (exclude_st and status.is_st is True):
        return False
    if status.trading_eligible is None or status.suspended is None:
        return None
    if exclude_st and status.is_st is None:
        return None
    return status.trading_eligible and not status.suspended and (not exclude_st or not status.is_st)


def calculate_breadth(metrics, stock_ids, statuses, *, universe=None, exclude_st=True):
    stock_ids = tuple(sorted(stock_ids))
    qualified = [item for item in stock_ids if eligible(statuses.get(item), exclude_st=exclude_st) is True]
    unknown = [item for item in stock_ids if eligible(statuses.get(item), exclude_st=exclude_st) is None]
    returns = [metrics[item]["return_pct"] for item in qualified if metrics[item]["return_pct"] is not None]
    up = sum(value > 0 for value in returns)
    down = sum(value < 0 for value in returns)
    known_limits = [item for item in qualified if metrics[item]["current_price"] is not None
                    and metrics[item]["limit_up"] is not None and metrics[item]["limit_down"] is not None]
    limits_complete = bool(qualified) and len(known_limits) == len(qualified)
    turnover_values = [metrics[item]["cumulative_turnover"] for item in qualified]
    turnover_known = bool(qualified) and all(value is not None for value in turnover_values)
    turnover = sum(turnover_values) if turnover_known else None
    historical = [metrics[item]["historical_turnover"] for item in qualified]
    historical_known = turnover_known and all(value is not None for value in historical)
    baseline = sum(historical) if historical_known else None
    group_means = {}
    for bucket in ("LARGE", "SMALL"):
        values = [metrics[item]["return_pct"] for item in qualified
                  if statuses[item].size_bucket == bucket and metrics[item]["return_pct"] is not None]
        group_means[bucket] = mean(values) if values else None
    previous_limit = [metrics[item]["return_pct"] for item in qualified
                      if statuses[item].previous_limit_up is True and metrics[item]["return_pct"] is not None]
    previous_limit_complete = bool(qualified) and all(statuses[item].previous_limit_up is not None
        and (statuses[item].previous_limit_up is False or metrics[item]["return_pct"] is not None) for item in qualified)
    size_cohort_complete = bool(qualified) and all(statuses[item].size_bucket in ("LARGE", "SMALL")
        and metrics[item]["return_pct"] is not None for item in qualified)
    full_limit_path = limits_complete and all(metrics[item]["day_high"] is not None for item in qualified)
    board_history_complete = bool(qualified) and all(statuses[item].previous_board_height is not None for item in qualified)
    high_momentum_ids = [item for item in qualified if statuses[item].previous_board_height is not None
                         and statuses[item].previous_board_height >= 2]
    high_momentum = [metrics[item]["return_pct"] for item in high_momentum_ids]
    denominator = len(qualified)
    complete = not unknown and denominator > 0 and len(returns) == denominator
    full_inventory = universe is not None and universe.coverage == "COMPLETE_MARKET"
    full_complete = full_inventory and set(universe.instrument_ids) == set(stock_ids) and complete
    warnings = []
    if unknown:
        warnings.append("HISTORICAL_ELIGIBILITY_UNAVAILABLE")
    if not complete:
        warnings.append("BREADTH_INCOMPLETE")
    if not full_complete:
        warnings.append("FULL_MARKET_BREADTH_UNAVAILABLE")
    return {
        "status": "AVAILABLE" if complete else "PARTIAL" if returns else "UNAVAILABLE",
        "scope": "DECLARED_SCOPE_ONLY",
        "universe_declared_count": len(stock_ids), "qualified_count": denominator,
        "qualified_inventory_hash": digest(qualified),
        "eligibility_unknown_count": len(unknown), "return_observed_count": len(returns),
        "return_coverage_ratio": len(returns) / denominator if denominator else None,
        "denominator": "OBSERVED_QUALIFIED_WITH_REFERENCE_PRICE",
        "advance_count": up if returns else None, "decline_count": down if returns else None,
        "unchanged_count": len(returns) - up - down if returns else None,
        "advance_ratio": up / len(returns) if returns else None,
        "decline_ratio": down / len(returns) if returns else None,
        "mean_return_pct": mean(returns) if returns else None,
        "median_return_pct": median(returns) if returns else None,
        "limit_price_observed_count": len(known_limits),
        "limit_up_count": sum(metrics[item]["current_price"] >= metrics[item]["limit_up"] - 1e-8 for item in known_limits) if limits_complete else None,
        "limit_down_count": sum(metrics[item]["current_price"] <= metrics[item]["limit_down"] + 1e-8 for item in known_limits) if limits_complete else None,
        "broken_limit_count": sum(metrics[item]["day_high"] >= metrics[item]["limit_up"] - 1e-8
            and metrics[item]["current_price"] < metrics[item]["limit_up"] - 1e-8 for item in qualified) if full_limit_path else None,
        "broken_limit_definition": "RAW_INTRADAY_HIGH_TOUCHED_SOURCED_UP_LIMIT_AND_CURRENT_PRICE_BELOW",
        "max_board_height": max(statuses[item].previous_board_height + 1
            if metrics[item]["current_price"] >= metrics[item]["limit_up"] - 1e-8 else 0 for item in qualified)
            if board_history_complete and limits_complete else None,
        "high_momentum_return_pct": mean(high_momentum) if board_history_complete and high_momentum
            and all(value is not None for value in high_momentum) else None,
        "high_momentum_definition": "EXPLICIT_PREVIOUS_BOARD_HEIGHT_GE_2_INITIAL_UNVALIDATED",
        "previous_limit_up_return_pct": mean(previous_limit) if previous_limit_complete and previous_limit else None,
        "cumulative_turnover": turnover,
        "market_turnover_ratio": turnover / baseline if baseline is not None and baseline > 0 else None,
        "small_vs_large_pct": group_means["SMALL"] - group_means["LARGE"]
            if size_cohort_complete and all(value is not None for value in group_means.values()) else None,
        "full_market_status": "AVAILABLE" if full_complete else "UNAVAILABLE",
        "full_market_denominator": denominator if full_complete else None,
        "full_market_advance_ratio": up / denominator if full_complete else None,
        "full_market_median_return_pct": median(returns) if full_complete else None,
        "warnings": warnings,
    }
