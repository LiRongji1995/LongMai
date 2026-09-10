"""R16 deterministic historical source stability audit.

Reads two registered MarketInput documents and compares the canonical resolved
source view for sessions <= Day T.  It does not backfill, refresh providers, or
mutate production data.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, time
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from stockfish_core.storage import SQLiteStore  # noqa: E402
from stockfish_core.yaogu.contracts import MarketInput, digest  # noqa: E402
from stockfish_core.yaogu.history_artifacts import bind_history, digest_market_input  # noqa: E402
from stockfish_core.yaogu.derived_feature_store import MATERIALIZED_FAMILIES, _source_session_range  # noqa: E402
from stockfish_core.yaogu.source_resolution import canonical_price_source_view, source_token  # noqa: E402
from stockfish_core.yaogu.strategy_experiments import _prepare_context  # noqa: E402


SH = ZoneInfo("Asia/Shanghai")
DEFAULT_T = "market_63f83798f5098898debce30e0810a0c54f6a0daffdc75ac6404d47d5f8488a23"
DEFAULT_TP1 = "market_04d95df6f6f699c7f88353d181308159526865877a666a7c3efb84be01b9c08d"


def _load_bundle(store, input_id):
    raw = store.get_document_bytes("candidate_inputs", input_id, expected_hash=input_id[7:])
    if raw is None:
        raise RuntimeError(f"candidate input missing or corrupt: {input_id}")
    return bind_history(MarketInput.model_validate_json(raw), store)


def _cutoff(bundle, value):
    if value:
        return datetime.fromisoformat(value)
    receipt_times = [receipt.ingested_at for receipt in bundle.receipts if receipt.ingested_at is not None]
    if receipt_times:
        return max(receipt_times)
    return datetime.combine(bundle.observation_date, time(20, 30), SH)


def _date_text(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)[:10]


def _target_source_sessions(ctx):
    sessions = tuple(ctx.sessions)
    target_sessions = sessions[-min(26, len(sessions)) :]
    positions = {session: index for index, session in enumerate(sessions)}
    source = sorted({
        source_session
        for session in target_sessions
        for family in MATERIALIZED_FAMILIES
        for source_session in _source_session_range(ctx, session, family)
    }, key=positions.__getitem__)
    return target_sessions, source


def _after_cutoff(value, cutoff):
    if not value:
        return False
    try:
        return datetime.fromisoformat(str(value)) > cutoff
    except ValueError:
        return False


def _has_newer_visible_revision(token, cutoff):
    revision = token.get("source_revision") or {}
    return any(_after_cutoff(revision.get(field), cutoff) for field in ("source_published_at", "ingested_at", "first_seen_at"))


def _lost_comparable_payload(t_token, tp1_token):
    left = t_token.get("values") or {}
    right = tp1_token.get("values") or {}
    comparable_was_known = left.get("comparable_close") is not None and left.get("comparable_basis") is not None
    comparable_now_missing = right.get("comparable_close") is None or right.get("comparable_basis") is None
    raw_fields = ("open", "high", "low", "close", "reference_prev_close", "volume_shares", "amount_cny", "turnover_fraction")
    raw_same = all(left.get(field) == right.get(field) for field in raw_fields)
    return comparable_was_known and comparable_now_missing and raw_same


def _classify(t_token, tp1_token, session, day_t, day_tp1, cutoff_t):
    session = _date_text(session)
    day_t = _date_text(day_t)
    day_tp1 = _date_text(day_tp1)
    if session > day_t and session <= day_tp1:
        return "APPENDED_NEW_SESSION"
    if t_token.get("status") == "MISSING" and tp1_token.get("status") == "MISSING":
        return "UNCHANGED"
    if t_token.get("status") == "SELECTED" and tp1_token.get("status") == "SELECTED":
        if t_token.get("row_hash") == tp1_token.get("row_hash"):
            return "UNCHANGED"
        if t_token.get("value_hash") == tp1_token.get("value_hash"):
            return "LEGITIMATE_SOURCE_REVISION"
        if _lost_comparable_payload(t_token, tp1_token):
            return "BUG_IN_ARTIFACT_BUILD"
        if _has_newer_visible_revision(tp1_token, cutoff_t):
            return "LEGITIMATE_SOURCE_REVISION"
        if t_token.get("source_kind") != tp1_token.get("source_kind"):
            return "SOURCE_REPLACED"
        return "SOURCE_REPLACED"
    if t_token.get("status") == "SELECTED" and tp1_token.get("status") == "MISSING":
        return "SOURCE_DISAPPEARED"
    if t_token.get("status") == "MISSING" and tp1_token.get("status") == "SELECTED":
        return "LEGITIMATE_SOURCE_REVISION" if _has_newer_visible_revision(tp1_token, cutoff_t) else "BUG_IN_ARTIFACT_BUILD"
    if "CONFLICT" in {t_token.get("status"), tp1_token.get("status")}:
        return "BUG_IN_MERGE/RESOLUTION"
    return "UNKNOWN"


def audit(db_path, input_id_t, input_id_tp1, cutoff_t=None, cutoff_tp1=None, instrument_limit=None):
    store = SQLiteStore(db_path)
    bundle_t = _load_bundle(store, input_id_t)
    bundle_tp1 = _load_bundle(store, input_id_tp1)
    ctx_t = _prepare_context(bundle_t, _cutoff(bundle_t, cutoff_t))
    ctx_tp1 = _prepare_context(bundle_tp1, _cutoff(bundle_tp1, cutoff_tp1))
    target_sessions_t, source_sessions_t = _target_source_sessions(ctx_t)
    _target_sessions_tp1, source_sessions_tp1 = _target_source_sessions(ctx_tp1)
    common_sessions = tuple(session for session in source_sessions_t if session in set(source_sessions_tp1) and session <= ctx_t.observation_date)
    common_instruments = sorted(set(ctx_t.eligible_ids) & set(ctx_tp1.eligible_ids))
    if instrument_limit:
        common_instruments = common_instruments[:instrument_limit]
    view_t = canonical_price_source_view(ctx_t, common_sessions, common_instruments)
    view_tp1 = canonical_price_source_view(ctx_tp1, common_sessions, common_instruments)
    samples = []
    counts = Counter()
    changed_by_session = Counter()
    changed_by_instrument = Counter()
    for iid in common_instruments:
        for session in common_sessions:
            token_t = source_token(view_t, iid, session)
            token_tp1 = source_token(view_tp1, iid, session)
            category = _classify(token_t, token_tp1, session, ctx_t.observation_date, ctx_tp1.observation_date, ctx_t.cutoff)
            counts[category] += 1
            if category != "UNCHANGED":
                changed_by_session[session] += 1
                changed_by_instrument[iid] += 1
                if len(samples) < 100:
                    samples.append({
                        "instrument": iid,
                        "session": session,
                        "category": category,
                        "day_t": token_t,
                        "day_t_plus_1": token_tp1,
                    })
    source_disappeared = counts.get("SOURCE_DISAPPEARED", 0)
    violation_count = (
        source_disappeared
        + counts.get("SOURCE_REPLACED", 0)
        + counts.get("BUG_IN_ARTIFACT_BUILD", 0)
        + counts.get("BUG_IN_MERGE/RESOLUTION", 0)
        + counts.get("UNKNOWN", 0)
    )
    artifact_build_bug = counts.get("BUG_IN_ARTIFACT_BUILD", 0)
    if artifact_build_bug:
        root_cause = "HISTORY_ARTIFACT_REBUILT_WITHOUT_PRIOR_COMPARABLE_FACTOR_COMPOSITE_ROWS"
    elif source_disappeared:
        root_cause = "CANONICAL_RESOLVER_CONFIRMED_SOURCE_DISAPPEARANCE_IN_T_PLUS_1"
    else:
        root_cause = "CANONICAL_RESOLVER_NO_HISTORICAL_SOURCE_DISAPPEARANCE_FOR_COMPARED_RANGE"
    if source_disappeared or artifact_build_bug:
        fix_status = (
            "R16 fixes future artifact builds by carrying the prior registered history artifact as the normalization "
            "base_ref, and fixes cache dependency completeness by sharing the canonical resolved source view."
        )
    else:
        fix_status = (
            "Append-only invariant holds for the compared historical range under the canonical resolved source view."
        )
    return {
        "audit_version": "R16_HISTORY_STABILITY_AUDIT_V1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "db_path": str(Path(db_path).resolve()),
        "input_t": {
            "input_id": input_id_t,
            "observation_date": str(bundle_t.observation_date),
            "input_hash": digest_market_input(bundle_t),
            "history_artifact_ref": bundle_t.history_artifact_ref,
            "history_artifact_hash": bundle_t.history_artifact_hash,
            "eligible": len(ctx_t.eligible_ids),
        },
        "input_t_plus_1": {
            "input_id": input_id_tp1,
            "observation_date": str(bundle_tp1.observation_date),
            "input_hash": digest_market_input(bundle_tp1),
            "history_artifact_ref": bundle_tp1.history_artifact_ref,
            "history_artifact_hash": bundle_tp1.history_artifact_hash,
            "eligible": len(ctx_tp1.eligible_ids),
        },
        "comparison_scope": {
            "sessions_compared": list(common_sessions),
            "instrument_count": len(common_instruments),
            "target_sessions_t": list(target_sessions_t),
            "instrument_limit": instrument_limit,
        },
        "classification_counts": dict(counts),
        "changed_by_session_top": changed_by_session.most_common(20),
        "changed_by_instrument_top": changed_by_instrument.most_common(20),
        "samples": samples,
        "append_only_invariant": {
            "policy": "for session <= Day T, resolved historical source(T+1) must equal resolved historical source(T) unless a legitimate revision is proven",
            "violations": violation_count,
            "status": "PASS" if violation_count == 0 else "FAIL",
        },
        "root_cause": root_cause,
        "fix_status": fix_status,
        "market_edge": "NOT_ESTABLISHED",
    }


def write_md(path, result):
    counts = result["classification_counts"]
    lines = [
        "# R16 History Stability Audit",
        "",
        f"- generated_at: `{result['generated_at']}`",
        f"- input_t: `{result['input_t']['input_id']}` / `{result['input_t']['observation_date']}`",
        f"- input_t_plus_1: `{result['input_t_plus_1']['input_id']}` / `{result['input_t_plus_1']['observation_date']}`",
        f"- sessions_compared: `{len(result['comparison_scope']['sessions_compared'])}`",
        f"- instruments_compared: `{result['comparison_scope']['instrument_count']}`",
        f"- append_only_invariant: `{result['append_only_invariant']['status']}`",
        f"- root_cause: `{result['root_cause']}`",
        f"- fix_status: {result['fix_status']}",
        "",
        "## Classification Counts",
        "",
    ]
    for key in (
        "UNCHANGED",
        "APPENDED_NEW_SESSION",
        "LEGITIMATE_SOURCE_REVISION",
        "SOURCE_REPLACED",
        "SOURCE_DISAPPEARED",
        "SOURCE_BECAME_UNAVAILABLE",
        "BUG_IN_ARTIFACT_BUILD",
        "BUG_IN_MERGE/RESOLUTION",
        "UNKNOWN",
    ):
        lines.append(f"- {key}: `{counts.get(key, 0)}`")
    lines.extend(["", "## Sample Differences", ""])
    for sample in result["samples"][:20]:
        lines.append(
            f"- `{sample['instrument']}` `{sample['session']}` `{sample['category']}`: "
            f"T `{sample['day_t'].get('row_hash')}` -> T+1 `{sample['day_t_plus_1'].get('row_hash')}`"
        )
    lines.extend([
        "",
        "The audit reads existing immutable inputs only. It performs no provider refresh and no historical backfill.",
    ])
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "stockfish.sqlite3"))
    parser.add_argument("--input-id-t", default=DEFAULT_T)
    parser.add_argument("--input-id-t-plus-1", default=DEFAULT_TP1)
    parser.add_argument("--cutoff-t", default=None)
    parser.add_argument("--cutoff-t-plus-1", default=None)
    parser.add_argument("--instrument-limit", type=int, default=None)
    parser.add_argument("--output-json", default=str(REPO_ROOT / "r16_history_stability_audit.json"))
    parser.add_argument("--output-md", default=str(REPO_ROOT / "R16_HISTORY_STABILITY_AUDIT.md"))
    args = parser.parse_args(argv)
    result = audit(args.db, args.input_id_t, args.input_id_t_plus_1, args.cutoff_t, args.cutoff_t_plus_1, args.instrument_limit)
    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    output_md = Path(args.output_md)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    write_md(output_md, result)
    print(json.dumps({
        "status": result["append_only_invariant"]["status"],
        "root_cause": result["root_cause"],
        "classification_counts": result["classification_counts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
