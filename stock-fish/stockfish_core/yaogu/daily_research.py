"""Read-only Phase 1 projections of frozen decisions; no strategy evaluation.

The caller owns persistence and expands the original run's immutable detail
documents. Missing historical identity/evidence is never filled from current
code, configuration, market data, or a language model.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from typing import Any

from .contracts import digest


STRATEGY_ID = "STAGE_AWARE_MOMENTUM_V2"
SNAPSHOT_VERSION = "DAILY_RECOMMENDATION_SNAPSHOT_V1"
REPORT_VERSION = "DAILY_RESEARCH_REPORT_V1"
DECISIONS = ("FOCUS", "WATCH", "UNKNOWN", "REJECTED")
CONDITION_LABELS = {
    "liquidity_bottom": "流动性", "trend_state": "趋势", "setup_identification": "setup",
    "behavioral_confirmation": "行为确认", "market_compatibility": "市场兼容",
    "sector_context": "题材", "capital_linkage": "资金", "same_theme_context": "同题材情境",
    "critical_counterevidence": "关键反证核对",
}
CONDITION_FALLBACKS = {
    "liquidity_bottom": "liquidity_status",
    "behavioral_confirmation": "behavioral_confirmation_status",
    "market_compatibility": "market_compatibility_status", "sector_context": "sector_status",
    "capital_linkage": "capital_evidence_status", "same_theme_context": "same_theme_context_status",
    "critical_counterevidence": "critical_counterevidence_status",
}
REASON_TEXT = {
    "SAM_FOCUS_BY_STAGE_AWARE_V2_REQUIREMENTS": "原 V2 规则判定满足 FOCUS 条件",
    "SAM_WATCH_BEHAVIORAL_CONFIRMATION_PENDING": "行为确认尚待补全",
    "SAM_WATCH_BEHAVIORAL_CONFIRMATION_FAILED_FOR_SETUP": "该 setup 的行为确认未通过",
    "SAM_WATCH_MARKET_CONTEXT_INCOMPLETE": "市场情境证据尚不完整",
    "SAM_WATCH_SAME_THEME_CONTEXT_INCOMPLETE": "同题材情境证据尚不完整",
    "SAM_WATCH_COUNTEREVIDENCE_REVIEW_PENDING": "关键反证核对尚未完成",
    "SAM_WATCH_WEAKENING_HIGH_ACTIVITY": "原规则记录高活跃度下的走弱观察状态",
    "SAM_MARKET_CONTEXT_UNKNOWN": "市场情境未知",
    "SAM_MARKET_CONTEXT_INCOMPATIBLE_WITH_SETUP": "市场情境与该 setup 不兼容",
    "SAM_AMOUNT_UNAVAILABLE": "成交额证据缺失",
    "SAM_TURNOVER_UNAVAILABLE": "换手率证据缺失",
}
FACT_KEYS = (
    "comparable_close", "comparable_basis", "amount_cny", "turnover_fraction",
    "A", "ret1_pct", "ret3_pct", "ret5_pct", "ret10_pct", "ret20_pct", "CLV",
    "trend_state", "market_context_state", "RS_market_1d_pct", "RS_sector_1d_pct",
)
NESTED_EVIDENCE_KEYS = ("value", "threshold", "ma_values", "ma_slopes", "dist_ma", "matched_ma",
                        "supporting_evidence", "counter_evidence", "critical_counter_evidence",
                        "divergence_evidence", "repair_evidence", "compatible_states", "source_refs")


def _first(*values):
    return next((value for value in values if value is not None and value != ""), None)


def research_identity(run: dict[str, Any], attempt: dict[str, Any] | None = None) -> dict[str, Any]:
    """Project captured identity only. Null means it was not captured."""
    attempt = attempt or {}
    if attempt.get("run_id") and attempt["run_id"] != run.get("run_id"):
        raise ValueError("DAILY_RESEARCH_ATTEMPT_RUN_MISMATCH")
    stage = (run.get("results") or {}).get(STRATEGY_ID) or {}
    prospective = run.get("prospective_validation") or {}
    execution = _first(attempt.get("final_status"), run.get("execution_status"))
    if execution is None and prospective.get("signal_run_persisted") is True:
        # A legacy saved experiment need not have crossed today's readiness
        # barrier. Only an explicit matching attempt can assert READY.
        execution = "SAVED"
    target = _first(prospective.get("target_session"), run.get("target_session"), attempt.get("target_session"))
    # Only consume an explicitly supplied, already verified exchange calendar.
    if target is None and run.get("observation_date") and run.get("sessions"):
        later = sorted({str(session) for session in run["sessions"] if str(session) > str(run["observation_date"])})
        target = later[0] if later else None
    return {
        "run_id": run.get("run_id"), "observation_date": run.get("observation_date"),
        "target_session": target,
        "strategy_version": _first(run.get("signal_implementation_version"), stage.get("stage_aware_implementation_version"), stage.get("implementation_version")),
        "config_version": _first(run.get("signal_config_hash"), stage.get("stage_aware_config_hash"), stage.get("config_hash")),
        "data_cutoff": _first(prospective.get("actual_decision_cutoff"), run.get("decision_cutoff"), run.get("cutoff")),
        "generated_at": run.get("generated_at"), "execution_status": execution or "UNKNOWN",
        "strategy_id": STRATEGY_ID,
        "spec_revision": _first(run.get("signal_spec_revision"), stage.get("stage_aware_spec_revision"), stage.get("spec_revision")),
    }


def _reason_summary(row):
    codes = row.get("reason_codes") or []
    explanations = [REASON_TEXT.get(code, "原规则原因：" + str(code)) for code in codes]
    decision = row.get("decision_status") or row.get("status")
    heading = "原程序判定 " + str(decision) + "；setup=" + str(row.get("primary_stage") or "UNKNOWN")
    return heading + ("；" + "；".join(explanations) if explanations else "；原运行未保存额外原因代码")


def _evidence_values(condition, primary_setup):
    # The current V2 flattens details; retain compatibility with a captured
    # nested details object. Select original values, never recompute metrics.
    source = {**(condition.get("details") or {}), **condition}
    values = {name: deepcopy(value) for name, value in source.items()
              if name not in {"status", "reason_codes"} and isinstance(value, (str, int, float, bool, type(None)))}
    for key in NESTED_EVIDENCE_KEYS:
        if key in source:
            values[key] = deepcopy(source[key])
    if primary_setup and primary_setup in (source.get("stage_candidates") or {}):
        selected = source["stage_candidates"][primary_setup]
        values["selected_setup_evidence"] = {key: deepcopy(value) for key, value in selected.items()
                                             if isinstance(value, (str, int, float, bool, type(None))) or key in NESTED_EVIDENCE_KEYS or key == "reason_codes"}
    required = source.get("required_events")
    if isinstance(required, list):
        values["required_events"] = deepcopy(required)
        values["event_evidence"] = {key: deepcopy(source.get("events", {}).get(key)) for key in required}
    return values


def _item_projection(run_id, frozen, detail):
    decision = frozen.get("decision_status") or frozen.get("status")
    if decision not in DECISIONS:
        raise ValueError("DAILY_RESEARCH_INVALID_FROZEN_DECISION")
    iid = frozen.get("instrument_id")
    if not iid or detail.get("instrument_id") != iid:
        raise ValueError("DAILY_RESEARCH_DETAIL_INSTRUMENT_MISMATCH")
    # Never accept a differently classified or re-priced detail as the original.
    for key in ("decision_status", "primary_stage", "comparable_close", "comparable_basis", "reason_codes"):
        if key in frozen and key in detail and frozen[key] != detail[key]:
            raise ValueError("DAILY_RESEARCH_DETAIL_FROZEN_FIELD_MISMATCH:" + key)
    conditions = detail.get("conditions") or {}
    statuses = frozen.get("condition_statuses") or detail.get("condition_statuses") or {}
    evidence, risks, unknowns = [], [], []
    for key, label in CONDITION_LABELS.items():
        condition = conditions.get(key) or {}
        status = _first(condition.get("status"), statuses.get(key), frozen.get(CONDITION_FALLBACKS.get(key, ""))) or "UNKNOWN"
        values = _evidence_values(condition, frozen.get("primary_stage"))
        codes = list(condition.get("reason_codes") or [])
        evidence.append({"key": key, "label": label, "status": status, "values": values, "reason_codes": codes,
                         "source_path": "conditions." + key})
        if status in {"FAIL", "REJECTED", "NEGATIVE"}:
            risks.append(label + "=" + str(status) + ("：" + "、".join(codes) if codes else ""))
        if status in {"UNKNOWN", "NOT_EVALUATED", "INSUFFICIENT_EVIDENCE", "UNAVAILABLE"}:
            unknowns.append(label + "=" + str(status) + ("：" + "、".join(codes) if codes else ""))
    risks.extend(str(flag) for flag in detail.get("risk_flags") or [])
    if frozen.get("detail_ref") and not conditions:
        unknowns.append("原 detail 未展开；仅展示已保存摘要证据")
    if not frozen.get("comparable_basis") or frozen.get("comparable_close") is None:
        unknowns.append("原冻结可比价格基准缺失；不能用后验价格补写")
    return {
        "run_id": run_id, "instrument_id": iid, "name": frozen.get("name"),
        "decision_status": decision, "primary_setup": frozen.get("primary_stage"),
        "alternate_setups": deepcopy(detail.get("alternate_stages") or []),
        "reason_codes": deepcopy(frozen.get("reason_codes") or []), "reason_summary": _reason_summary(frozen),
        "watch_reasons": deepcopy(frozen.get("watch_reasons") or []),
        "key_evidence": evidence, "facts": {key: deepcopy(frozen.get(key)) for key in FACT_KEYS},
        "risk_summary": risks, "unknowns": unknowns,
        "comparable_close": deepcopy(frozen.get("comparable_close")), "comparable_basis": frozen.get("comparable_basis"),
        "detail_ref": frozen.get("detail_ref"), "detail_hash": frozen.get("detail_hash"),
        "source_evidence_ref": {"run_id": run_id, "instrument_id": iid,
                                "detail_ref": frozen.get("detail_ref"), "detail_hash": frozen.get("detail_hash"),
                                "source_path": "results." + STRATEGY_ID + ".items"},
        "source_refs": deepcopy(detail.get("source_refs") or []),
        "display_order": frozen.get("display_order"), "setup_rank": frozen.get("setup_rank"),
    }


def _market_projection(stage, details):
    contexts = []
    for row in details:
        context = row.get("market_context") or row.get("market_status")
        if context:
            contexts.append({key: deepcopy(context.get(key)) for key in ("status", "ecosystem_state", "market_width", "reason_codes")})
    if contexts and all(context == contexts[0] for context in contexts):
        market = contexts[0]
        market["status"] = market.get("status") or "UNKNOWN"
        market["ecosystem_state"] = market.get("ecosystem_state") or "UNKNOWN"
        market["reason_codes"] = market.get("reason_codes") or []
    else:
        market = {"status": "UNKNOWN", "ecosystem_state": "UNKNOWN", "market_width": None,
                  "reason_codes": ["FROZEN_MARKET_CONTEXT_CONFLICT" if contexts else "FROZEN_MARKET_CONTEXT_UNAVAILABLE"]}
    market["market_benchmarks"] = deepcopy(stage.get("market_benchmarks") or {})
    market["source"] = "FROZEN_RUN_EVIDENCE_ONLY"
    return market


def recommendation_snapshot(run: dict[str, Any], *, expanded_items=None, attempt=None) -> dict[str, Any]:
    """Derive all four decision groups without filtering, re-ranking, or I/O."""
    stage = (run.get("results") or {}).get(STRATEGY_ID)
    if not run.get("run_id") or not isinstance(stage, dict) or not isinstance(stage.get("items"), list):
        raise ValueError("DAILY_RESEARCH_FROZEN_V2_RUN_REQUIRED")
    rows = stage["items"]
    details = list(expanded_items) if expanded_items is not None else rows
    ids = [row.get("instrument_id") for row in rows]
    detail_ids = [row.get("instrument_id") for row in details]
    if len(set(ids)) != len(ids) or len(set(detail_ids)) != len(detail_ids) or set(ids) != set(detail_ids):
        raise ValueError("DAILY_RESEARCH_DETAIL_SCOPE_MISMATCH")
    detail_index = {row["instrument_id"]: row for row in details}
    items = [_item_projection(run["run_id"], row, detail_index[row["instrument_id"]]) for row in rows]
    counts = {state: sum(row["decision_status"] == state for row in items) for state in DECISIONS}
    result = {
        "snapshot_version": SNAPSHOT_VERSION, "identity": research_identity(run, attempt),
        "run_id": run["run_id"], "source_run_hash": run.get("content_hash"),
        **{key: deepcopy(run.get(key)) for key in ("input_id", "input_hash", "validation_epoch_id", "signal_source_hash", "data_watermark", "coverage_type")},
        "published_at": _first(run.get("published_at"), (attempt or {}).get("published_at")),
        "source_refs": deepcopy(run.get("source_refs") or []),
        "prospective_validation": deepcopy(run.get("prospective_validation") or {}),
        "source_strategy_status": stage.get("status"), "scope_label": stage.get("scope_label"),
        "coverage": deepcopy(run.get("data_readiness") or stage.get("coverage_scope") or {}),
        "source_counts": deepcopy(stage.get("counts") or {}), "counts": counts, "items": items,
        "market_evidence": _market_projection(stage, details),
        "parameter_status": _first(stage.get("parameter_status"), run.get("parameter_status")) or "UNKNOWN", "market_edge": "NOT_ESTABLISHED",
    }
    result["snapshot_hash"] = digest(result)
    return result


def _text(value):
    if value is None:
        return "UNKNOWN"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _format_item(item):
    lines = ["- **" + _text(item["instrument_id"]) + " " + _text(item.get("name")) + "**；setup=" + _text(item.get("primary_setup")),
             "  原判定：" + _text(item.get("reason_summary")) + "。",
             "  关键证据：" + "；".join(_text(row["label"]) + "=" + _text(row["status"]) + ("（" + _text(row["values"]) + "）" if row.get("values") else "") for row in item.get("key_evidence") or []) + "。",
             "  冻结价格/量价事实：" + _text(item.get("facts")) + "。",
             "  原始证据引用：" + _text(item.get("source_evidence_ref")) + "；证券凭据=" + _text(item.get("source_refs") or "未保存逐股引用；见原 detail 与 run 来源") + "。",
             "  风险：" + ("；".join(_text(value) for value in item.get("risk_summary") or []) or "原投影未列出额外风险项；不代表没有风险") + "。",
             "  未知：" + ("；".join(_text(value) for value in item.get("unknowns") or []) or "上述已保存证据没有额外未知项") + "。"]
    return lines


def render_daily_report(snapshot: dict[str, Any] | None, *, validation_summary=None, delivery=None) -> str:
    """Render saved system facts; an absent snapshot is never an empty signal."""
    delivery = delivery or {}
    identity = (snapshot or {}).get("identity") or delivery.get("identity") or {}
    lines = ["# StockRecommendation 每日研究报告", "", "【事实】本报告展示原冻结程序判断及已保存后验；不生成投资建议。", "",
             "- 报告版本：" + REPORT_VERSION + "；交付状态：" + _text(delivery.get("execution_status") or delivery.get("status") or identity.get("execution_status")) + "。"]
    for key, label in (("run_id", "原运行"), ("observation_date", "观察日"), ("target_session", "目标交易日"),
                       ("strategy_version", "策略版本"), ("config_version", "配置版本"), ("data_cutoff", "资料截止"),
                       ("generated_at", "生成时间"), ("execution_status", "执行状态")):
        lines.append("- " + label + "：" + _text(identity.get(key, delivery.get(key))) + "。")
    for key, label in (("reason", "交付原因"), ("stage", "执行阶段"), ("timestamp", "状态时间"),
                       ("blockers", "阻塞项"), ("missing_evidence", "缺失证据"), ("error", "保存错误")):
        if delivery.get(key) is not None:
            lines.append("- " + label + "：" + _text(delivery[key]) + "。")
    if snapshot is None:
        lines += ["", "【事实】当前没有可交付的冻结推荐快照；这不是合法空名单。",
                  "- 原因：" + _text(delivery.get("reason") or delivery.get("reason_codes") or delivery.get("blockers") or delivery.get("blocking_requirements") or "UNKNOWN") + "。"]
    else:
        market = snapshot.get("market_evidence") or {}
        lines += ["", "## 市场状态与范围", "", "【事实】原市场判断：" + _text(market.get("ecosystem_state")) + "；证据状态=" + _text(market.get("status")) + "。",
                  "- 市场宽度证据：" + _text(market.get("market_width")) + "。",
                  "- 市场未知/原因：" + _text(market.get("reason_codes")) + "。",
                  "- 原策略状态：" + _text(snapshot.get("source_strategy_status")) + "；范围=" + _text(snapshot.get("scope_label")) + "；资料性质=" + _text(snapshot.get("coverage_type")) + "。",
                  "- 原快照哈希：" + _text(snapshot.get("snapshot_hash")) + "；原 run 哈希=" + _text(snapshot.get("source_run_hash")) + "。",
                  "- 原 run 来源凭据（非逐股归因）：" + _text(snapshot.get("source_refs")) + "。",
                  "- 四类数量：" + "；".join(state + "=" + str(snapshot.get("counts", {}).get(state, "UNKNOWN")) for state in DECISIONS) + "。"]
        for state in ("FOCUS", "WATCH"):
            lines += ["", "## " + state + " 完整列表", ""]
            rows = [row for row in snapshot.get("items") or [] if row["decision_status"] == state]
            if not rows:
                lines.append("【事实】原冻结运行 " + state + " 数量为 0；不补充股票。")
            for row in rows:
                lines += _format_item(row)
        lines += ["", "## UNKNOWN / REJECTED 摘要", ""]
        for state in ("UNKNOWN", "REJECTED"):
            rows = [row for row in snapshot.get("items") or [] if row["decision_status"] == state]
            reasons = Counter(code for row in rows for code in row.get("reason_codes") or [])
            lines.append("- " + state + "：" + str(len(rows)) + " 只；原因计数=" + _text(dict(reasons)) + "。")
        lines.append("完整四类决策均保留在同一原快照；原因计数可在同一证券重复，不是独立样本数。")
    lines += ["", "## 历史验证与成熟覆盖", ""]
    if not validation_summary:
        lines.append("【事实】NO_OUTCOME_SUMMARY；T+1..T+20 尚无可用的保存统计，不计零收益或零样本。")
    else:
        lines += _validation_lines(validation_summary)
    lines += ["", "【需要验证】初始研究参数尚未验证；MARKET_EDGE=NOT_ESTABLISHED。后验价格变化不代表可成交收益，setup 标签不代表市场事实。", ""]
    return "\n".join(lines)


def _validation_lines(summary):
    # The outcome consumer owns metrics and cohorts; this renderer never derives
    # win rates, fills missing values, or blends version groups.
    lines = ["【事实】统计状态：" + _text(summary.get("validation_status") or summary.get("status")) + "；资料截止=" + _text(summary.get("as_of")) + "。"]
    if summary.get("counts") is not None:
        lines.append("- 成熟/覆盖计数：" + _text(summary["counts"]) + "。")
    if summary.get("snapshot_availability") is not None:
        lines.append("- 历史可见性覆盖：" + _text(summary["snapshot_availability"]) + "；未来或时钟未知的快照不进入本时点统计。")
    groups = summary.get("groups") or []
    if isinstance(groups, dict):
        groups = [{"group": key, **value} for key, value in groups.items()]
    previous_identity = None
    for group in groups:
        frozen_identity = {key: group.get(key) for key in ("strategy_version", "config_version", "validation_epoch_id", "coverage_type", "evidence_type", "decision_status", "primary_setup")}
        if frozen_identity != previous_identity:
            lines += ["", "冻结分组：" + _text(frozen_identity), "",
                      "| 会话 | 信号数 | 到期 | 有效终点 | 完整路径 | 成熟率 | 有效覆盖 | 上涨比例 | 收益中位数 | MFE中位数 | MAE中位数 | 缺失/异常 |",
                      "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
            previous_identity = frozen_identity
        counts = group.get("counts") or {}
        values = ["T+" + _text(group.get("offset")), counts.get("signals"), counts.get("matured"),
                  counts.get("valid_return"), counts.get("valid_path"), group.get("maturity_rate"),
                  group.get("valid_outcome_rate"), group.get("up_rate"),
                  (group.get("return_distribution") or {}).get("median"),
                  (group.get("mfe_distribution") or {}).get("median"),
                  (group.get("mae_distribution") or {}).get("median"),
                  {key: counts.get(key) for key in ("not_matured", "calendar_unavailable", "missing_labels", "status_counts")}]
        lines.append("| " + " | ".join(_text(value) for value in values) + " |")
    if not groups:
        lines.append("- 暂无可展示的 setup / 决策层级 / T+1..T+20 统计分组；缺失不补零。")
    else:
        lines.append("比例与收益以原统计小数口径展示；分母逐会话保留，重复证券/相邻日期不视为独立样本。")
    return lines
