"""R05 experimental strategy variants layered on top of the frozen market input."""
from __future__ import annotations

from datetime import datetime, timezone
from math import sqrt
from statistics import median
from zoneinfo import ZoneInfo

from .config import StrategyConfig
from .contracts import MarketInput, digest
from .engine import HistoricalSecurityIndex, qualification
from .events import TRUE, limit_event
from .visibility import EvidenceResolver


SH = ZoneInfo("Asia/Shanghai")
PARAMETER_STATUS = "INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED"

STRATEGY_SPECS = (
    {
        "strategy_id": "DAILY_V1",
        "role": "原五族主策略",
        "parameter_status": PARAMETER_STATUS,
        "status": "REGISTERED_EXISTING",
        "description": "首板、2进3、N天M板、高标接力、断板恢复五族 DAILY 研究排序。",
        "required_capabilities": [
            "MAINBOARD_ELIGIBILITY",
            "RAW_DAILY_PRICE_AMOUNT",
            "COMPARABLE_PRICE",
            "CURRENT_LIMIT_EVENT",
            "THEME_MEMBERSHIP",
            "MARKET_EMOTION",
        ],
    },
    {
        "strategy_id": "PV_OBSERVATION_V1",
        "role": "量价观察控制组",
        "parameter_status": PARAMETER_STATUS,
        "status": "EXPERIMENTAL",
        "formula": "A=amount_t/mean(amount_t-5..t-1); R=comparable_close_t/comparable_close_t-5-1; H=(high_t-low_t)/reference_prev_close_t; S=0.45*PR(A)+0.35*PR(R)+0.20*PR(H)",
        "entry_rule": "A>=1.2 && R>0",
        "required_capabilities": [
            "MAINBOARD_ELIGIBILITY",
            "RAW_AMOUNT_T_AND_PREVIOUS_5_SESSIONS",
            "COMPARABLE_CLOSE_T_AND_T_MINUS_5_SAME_BASIS",
            "RAW_HIGH_LOW_REFERENCE_PREV_CLOSE_T",
        ],
    },
    {
        "strategy_id": "DNA_RERANK_V1",
        "role": "股性复排实验",
        "parameter_status": PARAMETER_STATUS,
        "status": "EXPERIMENTAL",
        "description": "仅对 PV_OBSERVATION_V1 入围项做历史涨停/触板后的次日表现复排，使用 Wilson95 下界与均值收缩。",
        "required_capabilities": [
            "PV_OBSERVATION_V1_ENTRANTS",
            "HISTORICAL_LIMIT_EVENTS",
            "NEXT_SESSION_COMPARABLE_OUTCOMES",
        ],
    },
    {
        "strategy_id": "THEME_CONTEXT_V1",
        "role": "题材上下文实验",
        "parameter_status": PARAMETER_STATUS,
        "status": "EXPERIMENTAL",
        "description": "仅对 PV_OBSERVATION_V1 入围项检查冻结主题成员与同伴行情；缺主题不影响 PV 排名。",
        "required_capabilities": [
            "PV_OBSERVATION_V1_ENTRANTS",
            "FROZEN_THEME_MEMBERSHIP",
            "PEER_CURRENT_AND_PREVIOUS_COMPARABLE_PRICE",
            "PEER_CURRENT_LIMIT_EVENT",
        ],
    },
)


def percentile_rank_midpoint(value, values):
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    if len(vals) == 1:
        return 50.0
    x = float(value)
    lower = sum(v < x for v in vals)
    equal = sum(v == x for v in vals)
    return 100.0 * (lower + 0.5 * equal) / len(vals)


def _as_float(value):
    if value is None:
        return None
    result = float(value)
    return result if result == result and result not in (float("inf"), float("-inf")) else None


def _visible_context(bundle: MarketInput | dict, cutoff=None, config=None):
    bundle = MarketInput.model_validate(bundle)
    cutoff = datetime.fromisoformat(cutoff) if isinstance(cutoff, str) else cutoff
    cutoff = cutoff or datetime.now(timezone.utc)
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("explicit timezone-aware decision cutoff required")
    config = StrategyConfig.model_validate(config) if isinstance(config, dict) else config or StrategyConfig()
    day = bundle.observation_date
    sessions = tuple(d for d in bundle.sessions if d <= day)
    resolver = EvidenceResolver(
        bundle.receipts,
        cutoff=cutoff,
        coverage_type=bundle.coverage_type,
        sessions=bundle.sessions,
        observation_date=day,
    )
    envelope = bundle.universe.model_dump(mode="json", exclude={"securities"})
    envelope_visible = resolver.is_visible(envelope)
    records = (
        tuple(s for s in bundle.universe.securities if resolver.is_visible(s, instrument_id=s.instrument_id))
        if envelope_visible else ()
    )
    records = tuple(s for s in records if "OUTCOME_ONLY" not in s.risk_flags)
    identity = HistoricalSecurityIndex(records, resolver)
    states = {iid: state for iid in sorted(identity.by_id) if (state := identity.at(iid, day)) is not None}
    default_config = config.model_copy(update={"universe": config.universe.model_copy(update={"include_st": False, "include_recent_ipo": False})})
    eligible, excluded, unknown = [], [], []
    for iid, state in states.items():
        reason = qualification(state, day, sessions, default_config, allow_suspension=False)
        if reason is None:
            eligible.append(iid)
        elif reason.startswith("UNKNOWN_"):
            unknown.append({"instrument_id": iid, "reason_codes": [reason]})
        else:
            excluded.append({"instrument_id": iid, "reason_codes": [reason]})
    expected = bundle.universe.expected_count if envelope_visible else None
    absent = max(0, (expected or 0) - len(states))
    if absent:
        unknown.append({"instrument_id": "UNIDENTIFIED_MASTER_RECORDS", "reason_codes": ["IDENTITY_ABSENT"], "count": absent})
    bars = {}
    for bar in sorted(bundle.bars, key=lambda b: (b.session, b.instrument_id, b.ingested_at)):
        if bar.session <= day and resolver.is_visible(bar, instrument_id=bar.instrument_id, session=bar.session, require_session=True):
            bars[(bar.instrument_id, bar.session)] = bar
    return {
        "bundle": bundle,
        "cutoff": cutoff,
        "config": config,
        "resolver": resolver,
        "sessions": sessions,
        "states": states,
        "eligible_ids": tuple(eligible),
        "excluded": tuple(excluded),
        "unknown": tuple(unknown),
        "expected_count": expected,
        "bars": bars,
        "universe_visible": envelope_visible,
        "universe_complete": bool(envelope_visible and bundle.universe.complete and not absent),
    }


def _basis_is_same(rows):
    bases = {row.comparable_basis for row in rows}
    return len(bases) == 1 and None not in bases


def run_pv_observation(bundle: MarketInput | dict, *, cutoff=None, config=None, top_n=20):
    ctx = _visible_context(bundle, cutoff, config)
    sessions = ctx["sessions"]
    day = ctx["bundle"].observation_date
    if len(sessions) < 6:
        return {
            "strategy_id": "PV_OBSERVATION_V1",
            "parameter_status": PARAMETER_STATUS,
            "status": "UNAVAILABLE",
            "observation_date": day.isoformat(),
            "reason_codes": ["LESS_THAN_SIX_EXCHANGE_SESSIONS"],
            "counts": {"eligible": len(ctx["eligible_ids"]), "qualification_unknown": len(ctx["unknown"])},
            "items": [],
            "top_ids": [],
            "missing": [],
            "excluded": list(ctx["excluded"]),
            "unknown": list(ctx["unknown"]),
        }
    window = sessions[-6:]
    rows, missing = [], []
    complete_window_count = 0
    for iid in ctx["eligible_ids"]:
        bars = [ctx["bars"].get((iid, session)) for session in window]
        fields = []
        if any(bar is None for bar in bars):
            fields.append("six_session_daily_bars")
        else:
            current = bars[-1]
            prior = bars[:-1]
            if any(_as_float(bar.amount_cny) is None for bar in bars):
                fields.append("raw_amount_t_and_previous_5")
            if any(_as_float(bar.comparable_close) is None for bar in bars) or not _basis_is_same(bars):
                fields.append("same_basis_comparable_close_window")
            for field in ("high", "low", "reference_prev_close"):
                if _as_float(getattr(current, field)) is None:
                    fields.append("raw_" + field + "_t")
        if fields:
            missing.append({"instrument_id": iid, "reason_codes": ["PV_REQUIRED_FIELD_MISSING"], "missing_fields": fields})
            continue
        current = bars[-1]
        amount_mean = sum(_as_float(bar.amount_cny) for bar in prior) / 5
        if amount_mean <= 0:
            missing.append({"instrument_id": iid, "reason_codes": ["PV_DENOMINATOR_INVALID"], "missing_fields": ["mean_amount_t_minus_5_to_t_minus_1"]})
            continue
        amount_ratio = _as_float(current.amount_cny) / amount_mean
        comparable_return = _as_float(current.comparable_close) / _as_float(bars[0].comparable_close) - 1
        high_low_range = (_as_float(current.high) - _as_float(current.low)) / _as_float(current.reference_prev_close)
        complete_window_count += 1
        if amount_ratio >= 1.2 and comparable_return > 0:
            rows.append({
                "instrument_id": iid,
                "name": ctx["states"][iid].name,
                "A": amount_ratio,
                "R": comparable_return,
                "H": high_low_range,
                "comparable_basis": current.comparable_basis,
                "source_refs": tuple(sorted({ref for bar in bars for ref in bar.source_refs})),
            })
    for row in rows:
        row["PR_A"] = percentile_rank_midpoint(row["A"], [x["A"] for x in rows])
        row["PR_R"] = percentile_rank_midpoint(row["R"], [x["R"] for x in rows])
        row["PR_H"] = percentile_rank_midpoint(row["H"], [x["H"] for x in rows])
        row["S_PV"] = round(0.45 * row["PR_A"] + 0.35 * row["PR_R"] + 0.20 * row["PR_H"], 6)
    ranked = sorted(rows, key=lambda row: (-row["S_PV"], -row["A"], -row["R"], -row["H"], row["instrument_id"]))
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
        row["parameter_status"] = PARAMETER_STATUS
    if ranked:
        status = "PARTIAL" if missing or ctx["unknown"] or not ctx["universe_complete"] else "COMPLETE"
    else:
        status = "PARTIAL" if missing or ctx["unknown"] or not ctx["universe_complete"] else "EMPTY"
    return {
        "strategy_id": "PV_OBSERVATION_V1",
        "parameter_status": PARAMETER_STATUS,
        "status": status,
        "observation_date": day.isoformat(),
        "cutoff": ctx["cutoff"].isoformat(),
        "coverage_type": ctx["bundle"].coverage_type,
        "entry_rule": "A>=1.2 && R>0",
        "score_formula": "0.45*PR(A)+0.35*PR(R)+0.20*PR(H)",
        "percentile_rank_definition": "100*(count_less + 0.5*count_equal)/N; N=1 gives 50",
        "counts": {
            "universe_expected": ctx["expected_count"],
            "identity_observed": len(ctx["states"]),
            "eligible": len(ctx["eligible_ids"]),
            "known_excluded": len(ctx["excluded"]),
            "qualification_unknown": len(ctx["unknown"]),
            "price_window_complete": complete_window_count,
            "screened_before_entry_filter": complete_window_count,
            "filter_passed": len(ranked),
            "ranked": len(ranked),
            "missing_required_data": len(missing),
        },
        "items": ranked,
        "top_ids": [row["instrument_id"] for row in ranked[:top_n]],
        "missing": missing,
        "excluded": list(ctx["excluded"]),
        "unknown": list(ctx["unknown"]),
    }


def _wilson_lower_bound(successes, n, z=1.96):
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def run_dna_rerank(bundle: MarketInput | dict, pv_result: dict | None = None, *, cutoff=None, config=None, min_samples=3):
    pv_result = pv_result or run_pv_observation(bundle, cutoff=cutoff, config=config)
    ctx = _visible_context(bundle, cutoff, config)
    entrants = [item["instrument_id"] for item in pv_result.get("items", [])]
    if not entrants:
        return {
            "strategy_id": "DNA_RERANK_V1",
            "parameter_status": PARAMETER_STATUS,
            "status": "UNAVAILABLE",
            "reason_codes": ["PV_OBSERVATION_ENTRANTS_REQUIRED"],
            "base_strategy_id": "PV_OBSERVATION_V1",
            "items": [],
        }
    index = {day: idx for idx, day in enumerate(ctx["sessions"])}
    items, unavailable = [], []
    for iid in entrants:
        samples = []
        for session in ctx["sessions"]:
            if session >= ctx["bundle"].observation_date or session not in index:
                continue
            next_index = index[session] + 1
            if next_index >= len(ctx["sessions"]):
                continue
            next_session = ctx["sessions"][next_index]
            bar = ctx["bars"].get((iid, session))
            nxt = ctx["bars"].get((iid, next_session))
            if bar is None or nxt is None:
                continue
            event = limit_event(bar, iid, session, suspended=False)
            if event.close_limit_up != TRUE and event.touched_limit_up != TRUE:
                continue
            cur = _as_float(bar.comparable_close)
            end = _as_float(nxt.comparable_close)
            if cur is None or end is None or cur <= 0 or not _basis_is_same([bar, nxt]):
                continue
            result = end / cur - 1
            samples.append({"session": session.isoformat(), "next_session": next_session.isoformat(),
                            "next_return": result, "success": result > 0,
                            "event": event.model_dump(mode="json")})
        n = len(samples)
        successes = sum(1 for sample in samples if sample["success"])
        if n < min_samples:
            unavailable.append({"instrument_id": iid, "reason_codes": ["DNA_MIN_SAMPLE_NOT_MET"], "sample_count": n, "required": min_samples})
            items.append({"instrument_id": iid, "status": "INSUFFICIENT_COVERAGE", "sample_count": n,
                          "success_count": successes, "parameter_status": PARAMETER_STATUS})
            continue
        mean_return = sum(sample["next_return"] for sample in samples) / n
        shrunk_mean_return = n * mean_return / (n + 20)
        wilson = _wilson_lower_bound(successes, n)
        score = round(100 * (0.70 * wilson + 0.30 * max(0, min(1, (shrunk_mean_return + 0.10) / 0.20))), 6)
        items.append({"instrument_id": iid, "status": "EVALUATED", "sample_count": n,
                      "success_count": successes, "wilson95_success_lower": wilson,
                      "mean_next_return": mean_return, "shrunk_mean_next_return": shrunk_mean_return,
                      "S_DNA": score, "parameter_status": PARAMETER_STATUS,
                      "sample_preview": samples[-10:]})
    ranked = sorted(items, key=lambda row: (row.get("status") != "EVALUATED", -row.get("S_DNA", -1), row["instrument_id"]))
    for rank, row in enumerate([r for r in ranked if r.get("status") == "EVALUATED"], 1):
        row["rank"] = rank
    status = "COMPLETE" if ranked and all(row.get("status") == "EVALUATED" for row in ranked) else "PARTIAL" if any(row.get("status") == "EVALUATED" for row in ranked) else "INSUFFICIENT_COVERAGE"
    return {
        "strategy_id": "DNA_RERANK_V1",
        "parameter_status": PARAMETER_STATUS,
        "status": status,
        "base_strategy_id": "PV_OBSERVATION_V1",
        "minimum_samples": min_samples,
        "items": ranked,
        "unavailable": unavailable,
        "counts": {
            "pv_entrants": len(entrants),
            "evaluated": sum(row.get("status") == "EVALUATED" for row in ranked),
            "insufficient_coverage": sum(row.get("status") != "EVALUATED" for row in ranked),
        },
    }


def run_theme_context(bundle: MarketInput | dict, pv_result: dict | None = None, *, cutoff=None, config=None):
    pv_result = pv_result or run_pv_observation(bundle, cutoff=cutoff, config=config)
    ctx = _visible_context(bundle, cutoff, config)
    entrants = [item["instrument_id"] for item in pv_result.get("items", [])]
    if not entrants:
        return {
            "strategy_id": "THEME_CONTEXT_V1",
            "parameter_status": PARAMETER_STATUS,
            "status": "UNAVAILABLE",
            "reason_codes": ["PV_OBSERVATION_ENTRANTS_REQUIRED"],
            "base_strategy_id": "PV_OBSERVATION_V1",
            "items": [],
        }
    if len(ctx["sessions"]) < 2:
        return {
            "strategy_id": "THEME_CONTEXT_V1",
            "parameter_status": PARAMETER_STATUS,
            "status": "UNAVAILABLE",
            "reason_codes": ["PREVIOUS_SESSION_REQUIRED"],
            "base_strategy_id": "PV_OBSERVATION_V1",
            "items": [],
        }
    membership_day = ctx["cutoff"].astimezone(SH).date()
    theme_versions = {}
    for theme in sorted(ctx["bundle"].themes, key=lambda t: (t.theme_id, t.effective_from, t.ingested_at)):
        if theme.theme_type not in {"EVENT"}:
            continue
        if theme.effective_from <= membership_day and (theme.effective_to is None or membership_day < theme.effective_to):
            if ctx["resolver"].is_visible(theme, require_complete=True):
                theme_versions[theme.theme_id] = theme
    if not theme_versions:
        return {
            "strategy_id": "THEME_CONTEXT_V1",
            "parameter_status": PARAMETER_STATUS,
            "status": "UNAVAILABLE",
            "reason_codes": ["NO_VISIBLE_EVENT_THEME_MEMBERSHIP"],
            "base_strategy_id": "PV_OBSERVATION_V1",
            "items": [],
            "counts": {"pv_entrants": len(entrants), "visible_themes": 0},
        }
    current, previous = ctx["sessions"][-1], ctx["sessions"][-2]
    eligible = set(ctx["eligible_ids"])
    rows, unavailable = [], []
    for iid in entrants:
        choices = []
        for theme in theme_versions.values():
            if iid not in theme.member_ids:
                continue
            peer_returns = []
            peer_limit_events = 0
            peers = sorted((set(theme.member_ids) & eligible) - {iid})
            for peer in peers:
                now_bar = ctx["bars"].get((peer, current))
                old_bar = ctx["bars"].get((peer, previous))
                if now_bar is None or old_bar is None:
                    continue
                now_close = _as_float(now_bar.comparable_close)
                old_close = _as_float(old_bar.comparable_close)
                if now_close is None or old_close is None or old_close <= 0 or not _basis_is_same([now_bar, old_bar]):
                    continue
                peer_returns.append(now_close / old_close - 1)
                event = limit_event(now_bar, peer, current, suspended=False)
                if event.close_limit_up == TRUE or event.touched_limit_up == TRUE:
                    peer_limit_events += 1
            if peer_returns:
                choices.append({
                    "theme_id": theme.theme_id,
                    "theme_name": theme.name,
                    "peer_observed": len(peer_returns),
                    "peer_expected": len(peers),
                    "peer_positive_fraction": sum(x > 0 for x in peer_returns) / len(peer_returns),
                    "peer_median_return": median(peer_returns),
                    "peer_limit_event_count": peer_limit_events,
                    "source_refs": theme.source_refs,
                })
        if not choices:
            unavailable.append({"instrument_id": iid, "reason_codes": ["NO_THEME_PEER_PRICE_CONTEXT"]})
            rows.append({"instrument_id": iid, "status": "INSUFFICIENT_COVERAGE", "parameter_status": PARAMETER_STATUS})
            continue
        selected = max(choices, key=lambda row: (row["peer_positive_fraction"], row["peer_median_return"], row["peer_limit_event_count"], row["theme_id"]))
        selected["instrument_id"] = iid
        selected["status"] = "EVALUATED"
        selected["parameter_status"] = PARAMETER_STATUS
        rows.append(selected)
    evaluated = [row for row in rows if row.get("status") == "EVALUATED"]
    for row in evaluated:
        pr_positive = percentile_rank_midpoint(row["peer_positive_fraction"], [x["peer_positive_fraction"] for x in evaluated])
        pr_return = percentile_rank_midpoint(row["peer_median_return"], [x["peer_median_return"] for x in evaluated])
        pr_limit = percentile_rank_midpoint(row["peer_limit_event_count"], [x["peer_limit_event_count"] for x in evaluated])
        row["PR_peer_positive_fraction"] = pr_positive
        row["PR_peer_median_return"] = pr_return
        row["PR_peer_limit_event_count"] = pr_limit
        row["S_THEME_CONTEXT"] = round(0.50 * pr_positive + 0.30 * pr_return + 0.20 * pr_limit, 6)
    ranked = sorted(rows, key=lambda row: (row.get("status") != "EVALUATED", -row.get("S_THEME_CONTEXT", -1), row["instrument_id"]))
    for rank, row in enumerate([r for r in ranked if r.get("status") == "EVALUATED"], 1):
        row["rank"] = rank
    status = "COMPLETE" if ranked and len(evaluated) == len(ranked) else "PARTIAL" if evaluated else "INSUFFICIENT_COVERAGE"
    return {
        "strategy_id": "THEME_CONTEXT_V1",
        "parameter_status": PARAMETER_STATUS,
        "status": status,
        "base_strategy_id": "PV_OBSERVATION_V1",
        "membership_as_of": membership_day.isoformat(),
        "items": ranked,
        "unavailable": unavailable,
        "counts": {
            "pv_entrants": len(entrants),
            "visible_themes": len(theme_versions),
            "evaluated": len(evaluated),
            "insufficient_coverage": len(ranked) - len(evaluated),
        },
    }


def run_strategy_experiments(bundle: MarketInput | dict, *, cutoff=None, config=None, top_n=20):
    bundle = MarketInput.model_validate(bundle)
    cutoff = datetime.fromisoformat(cutoff) if isinstance(cutoff, str) else cutoff
    cutoff = cutoff or datetime.now(timezone.utc)
    pv = run_pv_observation(bundle, cutoff=cutoff, config=config, top_n=top_n)
    dna = run_dna_rerank(bundle, pv, cutoff=cutoff, config=config)
    theme = run_theme_context(bundle, pv, cutoff=cutoff, config=config)
    comparison = strategy_comparison(None, pv, dna, theme)
    payload = {
        "run_id": "strategy_experiment_" + digest({
            "observation_date": bundle.observation_date.isoformat(),
            "cutoff": cutoff.isoformat(),
            "input_hash": digest(bundle),
            "pv": pv,
            "dna": {k: dna.get(k) for k in ("status", "counts")},
            "theme": {k: theme.get(k) for k in ("status", "counts")},
        })[:32],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "observation_date": bundle.observation_date.isoformat(),
        "cutoff": cutoff.isoformat(),
        "coverage_type": bundle.coverage_type,
        "input_hash": digest(bundle),
        "parameter_status": PARAMETER_STATUS,
        "specs": list(STRATEGY_SPECS),
        "results": {
            "PV_OBSERVATION_V1": pv,
            "DNA_RERANK_V1": dna,
            "THEME_CONTEXT_V1": theme,
        },
        "comparison": comparison,
    }
    payload["content_hash"] = digest(payload)
    return payload


def strategy_comparison(daily_summary, pv_result, dna_result, theme_result):
    daily_status = (daily_summary or {}).get("status", "NOT_RUN")
    return {
        "comparison_version": "R05_STRATEGY_COMPARISON_V1",
        "parameter_status": PARAMETER_STATUS,
        "strategies": [
            {
                "strategy_id": "DAILY_V1",
                "role": "原五族主策略",
                "status": daily_status,
                "item_count": (daily_summary or {}).get("item_total"),
                "market_edge": "NOT_ESTABLISHED",
                "notes": "保持原名和原语义；没有因新增控制组而降级。",
            },
            {
                "strategy_id": "PV_OBSERVATION_V1",
                "role": "量价观察控制组",
                "status": pv_result.get("status"),
                "item_count": len(pv_result.get("items", ())),
                "top_ids": pv_result.get("top_ids", ()),
                "market_edge": "NOT_ESTABLISHED",
            },
            {
                "strategy_id": "DNA_RERANK_V1",
                "role": "股性复排实验",
                "status": dna_result.get("status"),
                "item_count": dna_result.get("counts", {}).get("evaluated", 0),
                "market_edge": "NOT_ESTABLISHED",
            },
            {
                "strategy_id": "THEME_CONTEXT_V1",
                "role": "题材上下文实验",
                "status": theme_result.get("status"),
                "item_count": theme_result.get("counts", {}).get("evaluated", 0),
                "market_edge": "NOT_ESTABLISHED",
            },
        ],
        "shared_constraints": [
            "新增策略均为初始假设，未做市场有效性验证。",
            "S2/S3 只复排 S1 入围项；缺股性或缺题材不回写 S1 分数。",
            "涨停缺板事件保持未核验，不补 0 板。",
        ],
    }
