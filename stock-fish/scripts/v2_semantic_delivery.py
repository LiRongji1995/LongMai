"""Generate V2 semantic/data/replay/prospective delivery artifacts.

The script consumes a MarketInput and the active STAGE_AWARE_MOMENTUM_V2
runner. It writes summaries and compact research rows only; raw provider
payloads, SQLite files, tokens and caches stay outside the review bundle.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stockfish_core.observed_data import exchange_calendar_2026
from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu.data_pipeline import stage_aware_history_sessions_required
from stockfish_core.yaogu.stage_aware_momentum import (
    CONFIG,
    IMPLEMENTATION_VERSION,
    MA_WINDOWS,
    PRICE_WINDOWS,
    RS_WINDOWS,
    SECTOR_WINDOWS,
    SPEC_REVISION,
    STAGE_AWARE_ID,
    _bar,
    _bar_limit_event,
    _ma_snapshot,
    _market_benchmarks,
    _prepare_context,
    _price_return,
    _relative_strength,
    _session_index,
    _trend,
    _visible_stage_themes,
    run_stage_aware_momentum,
    stage_aware_source_identity,
)

SH = ZoneInfo("Asia/Shanghai")


def dump_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding="utf-8")


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def latest_market_input():
    candidates = sorted((ROOT / "reports" / "codex").glob("**/market-input.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit("NO_MARKET_INPUT_FOUND")
    return candidates[0]


def load_bundle(path):
    return MarketInput.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def load_scheduler_state(path):
    if path is None:
        return {
            "source": "ACTUAL_SCHEDULER_STATE_NOT_PROVIDED",
            "status": "NOT_CHECKED_ACTUAL_STATE_REQUIRED",
            "scheduler_status_trust": "NOT_ASSERTED_FROM_CLI_TEXT",
            "market_edge": CONFIG["market_edge"],
        }
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    state = value.get("prospective_validation", value)
    if state.get("source") != "ACTUAL_CANDIDATE_SCHEDULER_STATE_R10":
        raise SystemExit("SCHEDULER_STATE_JSON_MUST_COME_FROM_ACTUAL_CANDIDATE_SCHEDULER_STATE_R10")
    return state


def fraction(observed, expected):
    return round(observed / expected, 6) if expected else None


def family(name, expected, observed, *, source, pit_type, completeness, session_range, reasons=()):
    status = "COMPLETE" if expected and observed == expected else "PARTIAL" if observed else "UNKNOWN"
    return {
        "family": name,
        "expected_instruments": expected,
        "observed_instruments": observed,
        "coverage_fraction": fraction(observed, expected),
        "session_range": session_range,
        "source": source,
        "source_identity": "MarketInput receipts resolved by EvidenceResolver",
        "pit_type": pit_type,
        "completeness": completeness,
        "status": status,
        "failure_reasons": list(reasons),
    }


def data_readiness(bundle, cutoff, result, *, ctx=None):
    ctx = ctx or _prepare_context(bundle, cutoff)
    end = _session_index(ctx)
    expected = len(ctx.eligible_ids)
    session_range = [str(ctx.sessions[0]) if ctx.sessions else None, str(ctx.sessions[-1]) if ctx.sessions else None]
    rows = []
    for window in MA_WINDOWS:
        observed = 0
        reasons = Counter()
        for iid in ctx.eligible_ids:
            _, missing = _ma_snapshot(ctx, iid, end, (window,))
            if missing:
                reasons.update(missing)
            else:
                observed += 1
        rows.append(family(f"MA{window}", expected, observed, source="daily comparable bars", pit_type=bundle.coverage_type, completeness="per-instrument contiguous window", session_range=session_range, reasons=reasons))
    slope_lookback = CONFIG["research_seed_parameters"]["trend_state"]["slope_lookback_sessions"]
    observed_slope = 0
    slope_reasons = Counter()
    for iid in ctx.eligible_ids:
        trend = _trend(ctx, iid, end)
        if trend.get("ma_slopes") and all(trend["ma_slopes"].get(str(window)) is not None for window in CONFIG["research_seed_parameters"]["trend_state"].get("long_term_windows", (60, 120, 250))):
            observed_slope += 1
        else:
            slope_reasons.update(trend.get("reason_codes") or ["SAM_MA_SLOPE_UNAVAILABLE"])
    rows.append(family("MA_SLOPE_LONG", expected, observed_slope, source="daily comparable bars", pit_type=bundle.coverage_type, completeness=f"MA long windows plus {slope_lookback} session slope lookback", session_range=session_range, reasons=slope_reasons))
    for window in PRICE_WINDOWS:
        observed = sum(_price_return(ctx, iid, end, window) is not None for iid in ctx.eligible_ids)
        rows.append(family(f"RET{window}", expected, observed, source="daily comparable bars", pit_type=bundle.coverage_type, completeness="same comparable basis at both endpoints", session_range=session_range))
    market = _market_benchmarks(ctx, end)
    for window in RS_WINDOWS:
        observed = 0
        for iid in ctx.eligible_ids:
            returns = {str(w): _price_return(ctx, iid, end, w) for w in PRICE_WINDOWS}
            rs = _relative_strength(ctx, iid, end, returns, market)
            observed += ((rs.get("windows") or {}).get(str(window)) or {}).get("rs_market_pct") is not None
        rows.append(family(f"RS_MARKET_{window}", expected, observed, source="mainboard equal-weight benchmark diagnostic", pit_type=bundle.coverage_type, completeness="stock return plus benchmark coverage", session_range=session_range))
    known_limits = 0
    limit_reasons = Counter()
    for iid in ctx.eligible_ids:
        event = _bar_limit_event(_bar(ctx, ctx.observation_date, iid), iid, ctx.observation_date)
        known = event.close_limit_up in ("TRUE", "FALSE") and event.close_limit_down in ("TRUE", "FALSE")
        known_limits += known
        if not known:
            limit_reasons.update([event.quality or "LIMIT_EVENT_UNKNOWN"])
    rows.append(family("LIMIT_EVENTS_CURRENT_SESSION", expected, known_limits, source="verified provider limit fields when present", pit_type=bundle.coverage_type, completeness="no OHLC-derived verification fallback", session_range=session_range, reasons=limit_reasons))
    visible_themes, rejected_themes = _visible_stage_themes(ctx)
    linked_ids = set()
    for theme in visible_themes:
        linked_ids.update(set(theme.get("member_ids") or ()) & set(ctx.eligible_ids))
    rows.append(family("PIT_THEME_LINKAGE", expected, len(linked_ids), source="MarketInput themes", pit_type=bundle.coverage_type, completeness=f"{len(visible_themes)} visible themes; {len(rejected_themes)} rejected rows", session_range=session_range))
    counts = result.get("counts", {})
    sector_counts = counts.get("condition_counts", {}).get("sector_context", {})
    funds_counts = counts.get("condition_counts", {}).get("capital_linkage", {})
    rows.append(family("SECTOR_PEER_CONTEXT", expected, sector_counts.get("PASS", 0), source="visible PIT theme peers and daily bars", pit_type=bundle.coverage_type, completeness="per-window peer coverage independent", session_range=session_range, reasons=["UNKNOWN_OR_FAIL_COUNT=" + str(sector_counts.get("UNKNOWN", 0) + sector_counts.get("FAIL", 0))]))
    rows.append(family("CAPITAL_LHB_OR_CLASSIFIED_FUNDS", expected, funds_counts.get("PASS", 0), source="MarketInput funds", pit_type=bundle.coverage_type, completeness="COMPLETE_NO_RECORD requires explicit query completeness evidence", session_range=session_range, reasons=["UNKNOWN_OR_FAIL_COUNT=" + str(funds_counts.get("UNKNOWN", 0) + funds_counts.get("FAIL", 0))]))
    rows.append(family("AUCTION_FINAL_0925", expected, 0, source="not present in DailyBar MarketInput", pit_type=bundle.coverage_type, completeness="not captured by current input", session_range=session_range, reasons=["SAM_AUCTION_PHASES_NOT_IN_MARKET_INPUT"]))
    rows.append(family("INTRADAY_TIMESTAMPS", expected, 0, source="not present in DailyBar MarketInput", pit_type=bundle.coverage_type, completeness="first touch/seal/re-seal/turn-positive timestamps unavailable", session_range=session_range, reasons=["SAM_INTRADAY_TIMESTAMPS_NOT_IN_MARKET_INPUT"]))
    receipt_counts = Counter((receipt.provider, receipt.dataset, receipt.status) for receipt in bundle.receipts)
    return {
        "strategy_id": STAGE_AWARE_ID,
        "implementation_version": IMPLEMENTATION_VERSION,
        "spec_revision": SPEC_REVISION,
        "market_edge": CONFIG["market_edge"],
        "input_hash": digest(bundle),
        "observation_date": str(bundle.observation_date),
        "decision_cutoff": cutoff.isoformat(),
        "coverage_type": bundle.coverage_type,
        "eligible_instruments": expected,
        "session_range": session_range,
        "required_history_sessions_for_v2_60_session_replay": stage_aware_history_sessions_required(),
        "families": rows,
        "receipt_summary": [{"provider": p, "dataset": d, "status": s, "count": c} for (p, d, s), c in sorted(receipt_counts.items())],
    }


def compact_item(row, observation_date):
    return {
        "observation_date": observation_date,
        "instrument_id": row.get("instrument_id"),
        "name": row.get("name"),
        "decision_status": row.get("decision_status"),
        "primary_stage": row.get("primary_stage"),
        "observation_bucket": row.get("observation_bucket"),
        "rank_scope": row.get("rank_scope"),
        "setup_rank": row.get("setup_rank"),
        "focus_rank": row.get("focus_rank"),
        "watch_rank": row.get("watch_rank"),
        "trend_state": row.get("trend_state"),
        "strength_level": row.get("strength_level"),
        "strength_momentum": row.get("strength_momentum"),
        "sector_level": row.get("primary_sector_level_state"),
        "sector_momentum": row.get("primary_sector_momentum_state"),
        "sector_breadth": row.get("primary_sector_breadth_state"),
        "sector_absolute_level": row.get("primary_sector_absolute_level"),
        "sector_relative_level": row.get("primary_sector_relative_level"),
        "sector_market_rs_pct": row.get("primary_sector_market_rs_pct"),
        "stock_role": row.get("primary_sector_stock_role"),
        "stock_role_evidence_coverage": row.get("primary_sector_stock_role_evidence_coverage"),
        "funds_status": row.get("funds_status"),
        "market_context_state": row.get("market_context_state"),
        "behavioral_status": row.get("behavioral_confirmation_status"),
        "same_theme_context_status": row.get("same_theme_context_status"),
        "reason_codes": "|".join(row.get("reason_codes") or []),
        "ret1_pct": row.get("ret1_pct"),
        "ret3_pct": row.get("ret3_pct"),
        "ret5_pct": row.get("ret5_pct"),
        "A": row.get("A"),
    }


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def next_session_after(day):
    cal = exchange_calendar_2026()
    return cal.next_session(day + timedelta(days=1), inclusive=True)


def _active_on(start, end, session):
    return start <= session and (end is None or session < end)


def _fund_session(row):
    for key in ("session", "trade_date", "date", "business_date"):
        value = row.get(key) if isinstance(row, dict) else None
        if value:
            try:
                return date.fromisoformat(str(value)[:10])
            except ValueError:
                return None
    return None


def replay_bundle_for_session(bundle, session):
    sessions = tuple(s for s in bundle.sessions if s <= session)
    bars = tuple(b for b in bundle.bars if b.session <= session)
    securities = tuple(
        row for row in bundle.universe.securities
        if _active_on(row.effective_from, row.effective_to, session)
    )
    universe = bundle.universe.model_copy(update={"securities": securities})
    themes = tuple(
        row for row in bundle.themes
        if _active_on(row.effective_from, row.effective_to, session)
    )
    previous_emotions = tuple(row for row in bundle.previous_emotions if row.session <= session)
    funds = tuple(row for row in bundle.funds if (_fund_session(row) or session) <= session)
    return bundle.model_copy(update={
        "observation_date": session,
        "sessions": sessions,
        "universe": universe,
        "bars": bars,
        "themes": themes,
        "previous_emotions": previous_emotions,
        "funds": funds,
    })


def run_replay(bundle, cutoff, output, *, max_sessions):
    visible_sessions = tuple(s for s in bundle.sessions if s <= bundle.observation_date)
    end = len(visible_sessions) - 1 if bundle.observation_date in visible_sessions else None
    warmup = max(CONFIG["research_seed_parameters"]["trend_state"]["ma_windows"]) + CONFIG["research_seed_parameters"]["trend_state"]["slope_lookback_sessions"]
    if end is None:
        sessions = []
    else:
        start = max(warmup, end - max_sessions + 1)
        sessions = visible_sessions[start:end + 1]
    if bundle.coverage_type != "RECONSTRUCTED_HISTORICAL":
        attempts = [{
            "observation_date": str(session),
            "status": "BLOCKED_INPUT_NOT_RECONSTRUCTED_HISTORICAL",
            "focus": 0,
            "watch": 0,
            "rejected": 0,
            "unknown": 0,
            "eligible": 0,
            "blocker": "PROSPECTIVE_CAPTURED_INPUT_CANNOT_BE_REPLAYED_AS_HISTORICAL_SOURCE_TIME_PROOF",
        } for session in sessions]
        write_csv(output / "v2_historical_replay_items_compact.csv", [])
        return {
            "strategy_id": STAGE_AWARE_ID,
            "coverage_type": bundle.coverage_type,
            "audit_mode": "BLOCKED_REQUIRES_RECONSTRUCTED_HISTORICAL_INPUT",
            "requested_max_sessions": max_sessions,
            "warmup_sessions_required_before_first_signal": warmup,
            "replay_sessions": [str(s) for s in sessions],
            "attempt_count": len(attempts),
            "success_count": 0,
            "blocked_count": len(attempts),
            "focus_total": 0,
            "watch_total": 0,
            "rejected_total": 0,
            "unknown_total": 0,
            "eligible_total": 0,
            "attempts": attempts,
            "compact_items_csv": str((output / "v2_historical_replay_items_compact.csv").resolve()),
            "objective_blocker": "No RECONSTRUCTED_HISTORICAL MarketInput or verified historical source-policy supply directory is available in this workspace; using the 2026-09-07 PROSPECTIVE_CAPTURED input for past cutoffs would correctly hide all late-captured rows.",
            "market_edge": CONFIG["market_edge"],
        }
    attempts, rows = [], []
    for session in sessions:
        replay_bundle = replay_bundle_for_session(bundle, session)
        replay_cutoff = datetime.combine(date.fromisoformat(str(session)), time(20, 30), SH)
        try:
            result = run_stage_aware_momentum(replay_bundle, cutoff=replay_cutoff)
            counts = result.get("counts", {})
            condition_counts = counts.get("condition_counts", {})
            sector_counts = condition_counts.get("sector_context", {})
            funds_counts = condition_counts.get("capital_linkage", {})
            attempts.append({
                "observation_date": str(session),
                "status": result.get("status"),
                "focus": counts.get("focus", 0),
                "watch": counts.get("watch", 0),
                "rejected": counts.get("rejected", 0),
                "unknown": counts.get("unknown", 0),
                "eligible": counts.get("eligible", 0),
                "price_trend_replay_scope_status": "EVALUATED" if counts.get("eligible", 0) else "NO_ELIGIBLE_PRICE_TREND_ROWS",
                "full_context_replay_scope_status": "EVALUATED" if sector_counts.get("PASS", 0) and funds_counts.get("PASS", 0) else "UNKNOWN_OR_PARTIAL_CONTEXT",
                "sector_pass": sector_counts.get("PASS", 0),
                "capital_pass": funds_counts.get("PASS", 0),
            })
            rows.extend(compact_item(row, str(session)) for row in result.get("items", []))
        except Exception as exc:
            attempts.append({"observation_date": str(session), "status": "FAILED", "error_type": type(exc).__name__, "error": str(exc)})
    write_csv(output / "v2_historical_replay_items_compact.csv", rows)
    price_scope = [row for row in attempts if row.get("price_trend_replay_scope_status") == "EVALUATED"]
    full_context_scope = [row for row in attempts if row.get("full_context_replay_scope_status") == "EVALUATED"]
    return {
        "strategy_id": STAGE_AWARE_ID,
        "coverage_type": bundle.coverage_type,
        "audit_mode": "RECONSTRUCTED_HISTORICAL" if bundle.coverage_type == "RECONSTRUCTED_HISTORICAL" else "INPUT_REPLAY_WITH_ORIGINAL_COVERAGE_TYPE",
        "requested_max_sessions": max_sessions,
        "warmup_sessions_required_before_first_signal": warmup,
        "replay_sessions": [str(s) for s in sessions],
        "attempt_count": len(attempts),
        "success_count": sum(row.get("status") != "FAILED" for row in attempts),
        "focus_total": sum(row.get("focus", 0) for row in attempts),
        "watch_total": sum(row.get("watch", 0) for row in attempts),
        "rejected_total": sum(row.get("rejected", 0) for row in attempts),
        "unknown_total": sum(row.get("unknown", 0) for row in attempts),
        "eligible_total": sum(row.get("eligible", 0) for row in attempts),
        "scope_summary": {
            "PRICE_TREND_REPLAY_SCOPE": {
                "success_count": len(price_scope),
                "eligible_total": sum(row.get("eligible", 0) for row in price_scope),
                "coverage_rule": "long daily price/comparable basis/dated qualification sufficient; Sector/Capital may remain UNKNOWN",
            },
            "FULL_CONTEXT_REPLAY_SCOPE": {
                "success_count": len(full_context_scope),
                "eligible_total": sum(row.get("eligible", 0) for row in full_context_scope),
                "coverage_rule": "requires visible PIT theme/sector and classified capital context, not imputed from current membership",
            },
        },
        "attempts": attempts,
        "compact_items_csv": str((output / "v2_historical_replay_items_compact.csv").resolve()),
        "market_edge": CONFIG["market_edge"],
    }


def protocol(start_session, cutoff):
    return {
        "protocol_id": "VALIDATION_PROTOCOL_V2",
        "strategy_id": STAGE_AWARE_ID,
        "implementation_version": IMPLEMENTATION_VERSION,
        "spec_revision": SPEC_REVISION,
        "stage_aware_source_identity": stage_aware_source_identity(),
        "start_session": str(start_session) if start_session else None,
        "start_session_policy": "Set only after the first completed 20:30 EVENING_FINAL primary run; not prefilled from preliminary or reconstructed data.",
        "primary_signal_cutoff": "20:30 Asia/Shanghai EVENING_FINAL",
        "capture_stages": ["PRELIMINARY 16:10", "EVENING_FINAL 20:30", "AUCTION_REVIEW next session 09:26"],
        "eligible_universe": CONFIG["hard_requirements"]["qualified_universe"],
        "focus_definition": "FOCUS requires trend/setup/required behavior/market/same-theme funds and no critical counterevidence",
        "watch_definition": "WATCH is setup-specific pending, failed required behavior, or review-needed evidence; it is not a formal recommendation",
        "outcome_definitions": CONFIG["research_seed_parameters"]["tracking_outcomes"],
        "maturity_windows": [1, 2, 3, 5, 10, 20],
        "review_checkpoints_signal_sessions": [5, 20, 60, 120],
        "duplicate_policy": "append revisions; do not rewrite original signal",
        "historical_vs_prospective_separation": "historical reconstructed replay cannot be treated as prospective validation",
        "missing_data_handling": "UNKNOWN/NOT_MATURED/UNAVAILABLE remain distinct from FAIL",
        "no_tuning_rule": True,
        "parameter_status": CONFIG["parameter_status"],
        "market_edge": CONFIG["market_edge"],
        "created_at": cutoff.isoformat(),
    }


def research_list(result, signal_date, target_session, output):
    focus = [row for row in result.get("items", []) if row.get("decision_status") == "FOCUS"]
    watch = [row for row in result.get("items", []) if row.get("decision_status") == "WATCH"]
    selected = focus or watch
    rows = [compact_item(row, signal_date) for row in selected[:100]]
    write_csv(output / "v2_next_session_research_list.csv", rows)
    return {
        "signal_date": signal_date,
        "target_session": str(target_session),
        "selection_basis": "FOCUS_FIRST_ELSE_WATCH_RESEARCH_QUEUE",
        "focus_count": len(focus),
        "watch_count": len(watch),
        "selection_candidate_count": len(selected),
        "listed_count": len(rows),
        "items": rows,
        "csv": str((output / "v2_next_session_research_list.csv").resolve()),
        "market_edge": CONFIG["market_edge"],
    }


def case_diagnostics(result):
    wanted = {"601086.SH": "国芳集团", "600127.SH": "金健米业"}
    rows = {}
    by_id = {row.get("instrument_id"): row for row in result.get("items", [])}
    for iid, name in wanted.items():
        row = by_id.get(iid)
        if row is None:
            rows[iid] = {"instrument_id": iid, "name": name, "status": "NOT_IN_ELIGIBLE_POOL_OR_INPUT", "acceptance_policy": "diagnostic_only_not_focus_requirement"}
            continue
        rows[iid] = {
            "instrument_id": iid,
            "name": row.get("name") or name,
            "decision_status": row.get("decision_status"),
            "primary_stage": row.get("primary_stage"),
            "trend_state": row.get("trend_state"),
            "strength_level": row.get("strength_level"),
            "strength_momentum": row.get("strength_momentum"),
            "acceleration_state": ((row.get("stage_candidates") or {}).get("ACCELERATION") or {}).get("setup_state"),
            "hlr_state": ((row.get("stage_candidates") or {}).get("HIGH_LEVEL_REPAIR") or {}).get("setup_state"),
            "funds_status": row.get("funds_status"),
            "sector_status": row.get("sector_status"),
            "same_theme_context_status": row.get("same_theme_context_status"),
            "supply_pressure": row.get("supply_pressure"),
            "reason_codes": row.get("reason_codes"),
            "acceptance_policy": "diagnostic_only_not_focus_requirement",
        }
    return rows


def md_table(rows):
    lines = ["| 数据族 | 覆盖 | 状态 | 缺口 |", "|---|---:|---|---|"]
    for row in rows:
        gap = ", ".join(map(str, row.get("failure_reasons") or []))[:160]
        lines.append(f"| {row['family']} | {row.get('observed_instruments')}/{row.get('expected_instruments')} ({row.get('coverage_fraction')}) | {row.get('status')} | {gap} |")
    return "\n".join(lines)


def write_reports(output, docs_dir, *, readiness, replay, proto, prospective, research, cases):
    semantic_md = f"""# V2 Semantic Completion Report

- strategy_id: `{STAGE_AWARE_ID}`
- implementation_version: `{IMPLEMENTATION_VERSION}`
- spec_revision: `{SPEC_REVISION}`
- parameter_status: `{CONFIG['parameter_status']}`
- MARKET_EDGE: `{CONFIG['market_edge']}`

Completed semantic fixes:

- Behavioral FAIL blocks FOCUS only for setups that require that behavior.
- ACCELERATION uses shared equal-window strength momentum instead of positive ret/RS booleans.
- WEAKENING_HIGH_ACTIVITY is ACCELERATION-specific and no longer globally suppresses HLR/START/TREND_PULLBACK.
- Sector evidence now separates Level, Momentum, Breadth, Stock Role and setup compatibility.
- Sector coverage is saved independently per window.
- HLR high-level background uses T-1/pre-event facts.
- Trend State is separated from pullback-specific short-term health.
- Supply pressure records ever_reclaimed, first_reclaim_date, reclaimed_then_failed and unresolved_supply.
- Funds COMPLETE_NO_RECORD requires explicit query completeness evidence.
- WATCH rows receive decision-tier/setup-scoped research ranks.
- Market REPAIR requires transition evidence.
- Market EXPANSION cannot mask major broken-board/limit-down divergence, and market momentum is UNKNOWN when evidence coverage is not comparable.
- Sector Level stores absolute peer return and market-relative strength separately; Sector Momentum uses matched peers; Stock Role saves known/unknown feature coverage.
- V2 outcomes append next-session reversal, repair latency, T+3 new-high, breakout and supply labels without rewriting T signal.

Unresolved data gaps are documented in `V2_DATA_READINESS.md` and `V2_PROSPECTIVE_READINESS.md`.
"""
    data_md = f"""# V2 Data Readiness

- input_hash: `{readiness['input_hash']}`
- observation_date: `{readiness['observation_date']}`
- coverage_type: `{readiness['coverage_type']}`
- required_history_sessions_for_v2_60_session_replay: `{readiness['required_history_sessions_for_v2_60_session_replay']}`
- MARKET_EDGE: `{readiness['market_edge']}`

{md_table(readiness['families'])}
"""
    replay_blocker = replay.get("objective_blocker")
    replay_note = (
        f"Historical replay was not executed because: {replay_blocker}"
        if replay_blocker else
        "This replay is for coverage and outcome description only. It was not used for parameter search or promotion."
    )
    replay_compact_csv = Path(replay["compact_items_csv"]).name if replay.get("compact_items_csv") else None
    replay_md = f"""# V2 Historical Replay Report

- audit_mode: `{replay['audit_mode']}`
- coverage_type: `{replay['coverage_type']}`
- replay_input_hash: `{replay.get('replay_input_hash')}`
- replay_input_observation_date: `{replay.get('replay_input_observation_date')}`
- replay_range: `{replay['replay_sessions'][0] if replay['replay_sessions'] else None}` to `{replay['replay_sessions'][-1] if replay['replay_sessions'] else None}`
- attempt_count: `{replay['attempt_count']}`
- success_count: `{replay['success_count']}`
- Focus/Watch/Rejected/Unknown: `{replay['focus_total']}` / `{replay['watch_total']}` / `{replay['rejected_total']}` / `{replay['unknown_total']}`
- eligible_total: `{replay['eligible_total']}`
- PRICE_TREND_REPLAY_SCOPE successful days: `{(replay.get('scope_summary') or {}).get('PRICE_TREND_REPLAY_SCOPE', {}).get('success_count')}`
- FULL_CONTEXT_REPLAY_SCOPE successful days: `{(replay.get('scope_summary') or {}).get('FULL_CONTEXT_REPLAY_SCOPE', {}).get('success_count')}`
- compact_items_csv: `{replay_compact_csv}`
- MARKET_EDGE: `{replay['market_edge']}`
- blocker: `{replay_blocker}`

{replay_note}
"""
    protocol_md = f"""# V2 Prospective Validation Protocol

- protocol_id: `{proto['protocol_id']}`
- strategy_id: `{proto['strategy_id']}`
- implementation_version: `{proto['implementation_version']}`
- spec_revision: `{proto['spec_revision']}`
- start_session: `{proto['start_session']}`
- primary_signal_cutoff: `{proto['primary_signal_cutoff']}`
- checkpoints: `{proto['review_checkpoints_signal_sessions']}`
- MARKET_EDGE: `{proto['market_edge']}`

Rules: append-only outcomes, no historical-to-prospective backfill, UNKNOWN remains separate from FAIL, and no tuning without explicit new authorization.
"""
    prospective_md = f"""# V2 Prospective Readiness

- status: `{prospective['status']}`
- source: `{prospective['scheduler_state_source']}`
- next_real_primary_capture: `{prospective.get('next_real_primary_capture')}`
- next_target_session: `{prospective['next_target_session']}`
- signal_input_observation_date: `{prospective['signal_input_observation_date']}`
- scheduler_status: `{prospective['scheduler_status']}`
- scheduler_job_id: `{prospective.get('scheduler_job_id')}`
- service_identity: `{prospective.get('service_identity')}`
- official_primary_signal_count: `{prospective.get('official_primary_signal_count')}`
- first_official_validation_signal_date: `{prospective.get('first_official_validation_signal_date')}`
- blocker: `{prospective.get('blocker')}`
- minimal_manual_action: `{prospective.get('minimal_manual_action')}`
- MARKET_EDGE: `{prospective['market_edge']}`
"""
    for path, text in {
        "V2_SEMANTIC_COMPLETION_REPORT.md": semantic_md,
        "V2_DATA_READINESS.md": data_md,
        "V2_HISTORICAL_REPLAY_REPORT.md": replay_md,
        "V2_PROSPECTIVE_VALIDATION_PROTOCOL.md": protocol_md,
        "V2_PROSPECTIVE_READINESS.md": prospective_md,
    }.items():
        write_text(output / path, text)
        if docs_dir:
            write_text(Path(docs_dir) / path, text)
    write_text(output / "V2_NEXT_SESSION_RESEARCH_LIST.md", "\n".join([
        "# V2 Next-Session Research List",
        "",
        f"- signal_date: `{research['signal_date']}`",
        f"- target_session: `{research['target_session']}`",
        f"- focus_count: `{research['focus_count']}`",
        f"- watch_count: `{research['watch_count']}`",
        f"- listed_count: `{research['listed_count']}`",
        f"- MARKET_EDGE: `{research['market_edge']}`",
        "",
        "| rank_scope | code | name | stage | status | ret5_pct | A | reason |",
        "|---|---|---|---|---|---:|---:|---|",
        *[
            f"| {row.get('rank_scope')} | {row.get('instrument_id')} | {row.get('name')} | {row.get('primary_stage')} | {row.get('decision_status')} | {row.get('ret5_pct')} | {row.get('A')} | {(row.get('reason_codes') or '')[:120]} |"
            for row in research["items"]
        ],
    ]))
    write_text(output / "V2_CASE_DIAGNOSTICS.md", "\n".join([
        "# V2 Case Diagnostics",
        "",
        *[
            f"## {iid} {row.get('name')}\n\n- status: `{row.get('decision_status', row.get('status'))}`\n- primary_stage: `{row.get('primary_stage')}`\n- acceleration_state: `{row.get('acceleration_state')}`\n- hlr_state: `{row.get('hlr_state')}`\n- funds_status: `{row.get('funds_status')}`\n- sector_status: `{row.get('sector_status')}`\n- diagnostic_only: `{row.get('acceptance_policy')}`\n"
            for iid, row in cases.items()
        ],
    ]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--replay-input", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cutoff", default=None)
    parser.add_argument("--replay-sessions", type=int, default=20)
    parser.add_argument("--docs-dir", type=Path, default=None)
    parser.add_argument("--scheduler-state-json", type=Path, default=None)
    args = parser.parse_args()
    input_path = args.input or latest_market_input()
    bundle = load_bundle(input_path)
    cutoff = datetime.fromisoformat(args.cutoff) if args.cutoff else datetime.now(SH)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=SH)
    ctx = _prepare_context(bundle, cutoff)
    result = run_stage_aware_momentum(bundle, cutoff=cutoff, _context=ctx)
    scheduler_state = load_scheduler_state(args.scheduler_state_json)
    signal_date = str(bundle.observation_date)
    target_session = next_session_after(bundle.observation_date)
    replay_input_path = args.replay_input or input_path
    replay_bundle = load_bundle(replay_input_path)
    replay_cutoff = cutoff
    if replay_bundle.observation_date != bundle.observation_date:
        replay_cutoff = datetime.combine(replay_bundle.observation_date, time(20, 30), SH)
    args.output.mkdir(parents=True, exist_ok=True)
    dump_json(args.output / "v2_signal_result_summary.json", {
        "input_path": str(input_path.resolve()),
        "input_sha256": file_sha256(input_path),
        "input_hash": digest(bundle),
        "replay_input_path": str(replay_input_path.resolve()),
        "replay_input_sha256": file_sha256(replay_input_path),
        "replay_input_hash": digest(replay_bundle),
        "observation_date": signal_date,
        "decision_cutoff": cutoff.isoformat(),
        "strategy_id": STAGE_AWARE_ID,
        "implementation_version": IMPLEMENTATION_VERSION,
        "spec_revision": SPEC_REVISION,
        "counts": result.get("counts"),
        "market_edge": CONFIG["market_edge"],
    })
    write_csv(args.output / "v2_signal_items_compact.csv", [compact_item(row, signal_date) for row in result.get("items", [])])
    readiness = data_readiness(bundle, cutoff, result, ctx=ctx)
    replay = run_replay(replay_bundle, replay_cutoff, args.output, max_sessions=args.replay_sessions)
    replay.update({
        "replay_input_path": str(replay_input_path.resolve()),
        "replay_input_sha256": file_sha256(replay_input_path),
        "replay_input_hash": digest(replay_bundle),
        "replay_input_observation_date": str(replay_bundle.observation_date),
        "replay_decision_cutoff": replay_cutoff.isoformat(),
    })
    epoch = scheduler_state.get("validation_epoch") or {}
    start_session = epoch.get("start_session")
    proto = protocol(start_session, cutoff)
    official_primary_signal_count = 1 if epoch.get("first_primary_run_id") else 0
    prospective = {
        "status": scheduler_state.get("status", "UNKNOWN"),
        "scheduler_state_source": scheduler_state.get("source"),
        "next_real_primary_capture": scheduler_state.get("next_real_primary_capture"),
        "next_target_session": scheduler_state.get("expected_target_session") or str(target_session),
        "signal_input_observation_date": signal_date,
        "signal_validation_role": "PROSPECTIVE_PRELIMINARY" if bundle.coverage_type == "PROSPECTIVE_CAPTURED" else "NOT_PROSPECTIVE_SIGNAL_INPUT",
        "official_primary_signal_count": official_primary_signal_count,
        "first_official_validation_signal_date": epoch.get("first_signal_observation_date"),
        "scheduler_status": scheduler_state.get("status", "UNKNOWN"),
        "scheduler_job_id": scheduler_state.get("scheduler_job_id"),
        "scheduler_job_state": scheduler_state.get("scheduler_job_state"),
        "scheduler_event_state": scheduler_state.get("scheduler_event_state"),
        "service_identity": scheduler_state.get("service_identity"),
        "validation_epoch": epoch or None,
        "blocker": None if scheduler_state.get("status") not in {"NOT_CHECKED_ACTUAL_STATE_REQUIRED", "SCHEDULER_NOT_ATTACHED_TO_SERVICE"} else scheduler_state.get("status"),
        "minimal_manual_action": None if scheduler_state.get("status") not in {"NOT_CHECKED_ACTUAL_STATE_REQUIRED", "SCHEDULER_NOT_ATTACHED_TO_SERVICE"} else "Start the StockFish local service with CandidateScheduler attached before the next scheduled capture.",
        "market_edge": CONFIG["market_edge"],
    }
    research = research_list(result, signal_date, target_session, args.output)
    cases = case_diagnostics(result)
    dump_json(args.output / "V2_DATA_READINESS.json", readiness)
    dump_json(args.output / "V2_HISTORICAL_REPLAY_REPORT.json", replay)
    dump_json(args.output / "V2_PROSPECTIVE_VALIDATION_PROTOCOL.json", proto)
    dump_json(args.output / "V2_PROSPECTIVE_READINESS.json", prospective)
    dump_json(args.output / "V2_NEXT_SESSION_RESEARCH_LIST.json", research)
    dump_json(args.output / "V2_CASE_DIAGNOSTICS.json", cases)
    write_reports(args.output, args.docs_dir, readiness=readiness, replay=replay, proto=proto, prospective=prospective, research=research, cases=cases)
    print(json.dumps({
        "output": str(args.output.resolve()),
        "input": str(input_path.resolve()),
        "replay_input": str(replay_input_path.resolve()),
        "observation_date": signal_date,
        "target_session": str(target_session),
        "counts": result.get("counts"),
        "replay": {key: replay[key] for key in ("attempt_count", "success_count", "focus_total", "watch_total", "rejected_total", "unknown_total")},
        "market_edge": CONFIG["market_edge"],
    }, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
