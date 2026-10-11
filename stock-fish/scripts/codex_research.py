"""Codex 的轻量日常入口：读取正式保存结果，显式触发现有采集/跟踪作业。

不创建服务、不实现策略算法、不调用模型。默认只读；完整名单取自正式导出。
使用项目 Python：python scripts/codex_research.py --help
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, time as day_time, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib import error, parse, request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SH = ZoneInfo("Asia/Shanghai")
PV = "PV_OBSERVATION_V1"
FOCUS = "PV_FOCUS_PREVIEW_V0.1"
THEME_CAPITAL = "THEME_CAPITAL_TREND_V1"
THEME_CAPITAL_TECHNICAL = "THEME_CAPITAL_TREND_TECHNICAL_ONLY_V1"
SIMPLE_TREND = "SIMPLE_TREND_TEMPLATE_REFERENCE_V1"
STAGE_AWARE = "STAGE_AWARE_MOMENTUM_V2"
SHORT_HORIZON_LEGACY = "SHORT_HORIZON_STRENGTH_R1"
SHORT_HORIZON = "SHORT_HORIZON_STRENGTH_R2"
SHORT_HORIZON_PROFILES = (SHORT_HORIZON_LEGACY, SHORT_HORIZON)
DECISION_PROFILES = (STAGE_AWARE, *SHORT_HORIZON_PROFILES)
RESEARCH_PROFILES = (PV, THEME_CAPITAL, THEME_CAPITAL_TECHNICAL, SIMPLE_TREND, STAGE_AWARE, *SHORT_HORIZON_PROFILES)
READ_ONLY_LEGACY_PV_SOURCE_HASHES = {
    # R05 V4 before the additive THEME_CAPITAL_TREND_V1 experiment arms.
    "cde229340f46f123246c7ce7fe9b15331dc62f2a516b19e9524390ff4858031c",
    # R06 before the additive STAGE_AWARE_MOMENTUM_V1 experiment arm.
    "0cb20a15af9347785a73e005714e02030e8606e2b31c812472e0a3cc6daeb897",
}
REAL_COVERAGE = {"PROSPECTIVE_CAPTURED", "RECONSTRUCTED_HISTORICAL"}
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}
RESPONSE_MAX_BYTES = 64 * 1024 * 1024
SAVED_REPORT_MAX_BYTES = 256 * 1024 * 1024


class ResearchError(Exception):
    """Only controlled, credential-free error codes reach the console."""


def identifier(value):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", value):
        raise ResearchError("INVALID_IDENTIFIER")
    return value


def api_key(env_file=None):
    """Read one named variable; never execute .env contents or load other secrets."""
    value = os.environ.get("STOCKFISH_API_KEY", "")
    if not value and env_file:
        values = []
        try:
            for line in Path(env_file).read_text(encoding="utf-8-sig").splitlines():
                name, sep, raw = line.strip().partition("=")
                if sep and name.strip() == "STOCKFISH_API_KEY":
                    raw = raw.strip()
                    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
                        raw = raw[1:-1]
                    values.append(raw)
        except OSError:
            raise ResearchError("PRIVATE_ENV_FILE_UNREADABLE") from None
        if len(values) > 1:
            raise ResearchError("AMBIGUOUS_API_KEY_CONFIGURATION")
        value = values[0] if values else ""
    if any(ord(char) < 32 or ord(char) > 126 for char in value):
        raise ResearchError("INVALID_API_KEY_CONFIGURATION")
    return value


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url="http://127.0.0.1:8000", key="", timeout=30, export_timeout=180):
        parts = parse.urlsplit(base_url)
        try:
            valid_port = parts.port is None or 1 <= parts.port <= 65535
        except ValueError:
            valid_port = False
        if (parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "::1"}
                or parts.username or parts.password or parts.path not in {"", "/"}
                or parts.query or parts.fragment or not valid_port):
            raise ResearchError("LOCAL_LOOPBACK_HTTP_URL_REQUIRED")
        if not 0 < timeout <= 60:
            raise ResearchError("HTTP_TIMEOUT_MUST_BE_0_TO_60_SECONDS")
        if not 0 < export_timeout <= 180:
            raise ResearchError("EXPORT_TIMEOUT_MUST_BE_0_TO_180_SECONDS")
        self.base_url, self.key, self.timeout = base_url.rstrip("/"), key, timeout
        self.export_timeout = export_timeout
        self.opener = request.build_opener(request.ProxyHandler({}), NoRedirect())

    def raw(self, path, payload=None):
        if not path.startswith("/api/") or ".." in path or "\\" in path:
            raise ResearchError("INVALID_API_PATH")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = request.Request(self.base_url + path, data=data, headers=headers)
        # Full-market V2 includes every diagnostic row and its evidence. Keep a
        # bounded larger read only for saved exports / daily reports, never POSTs.
        is_export = payload is None and (parse.urlsplit(path).path == "/api/daily-research/report" or re.fullmatch(
            r"/api/strategy-experiments/[A-Za-z0-9_.-]{1,160}/export", parse.urlsplit(path).path))
        limit = SAVED_REPORT_MAX_BYTES if is_export else RESPONSE_MAX_BYTES
        try:
            with self.opener.open(req, timeout=self.export_timeout if is_export else self.timeout) as response:
                # No automatic retry: a timed-out POST may already have succeeded.
                content = response.read(limit + 1)
        except error.HTTPError as exc:
            raise ResearchError("HTTP_" + str(exc.code)) from None
        except (error.URLError, TimeoutError, OSError):
            raise ResearchError("NETWORK_FAILED_POST_STATE_UNKNOWN" if payload is not None
                                else "LOCAL_SERVICE_UNREACHABLE") from None
        if len(content) > limit:
            raise ResearchError("RESPONSE_SIZE_LIMIT_EXCEEDED")
        return content

    def get(self, path, payload=None):
        try:
            return json.loads(self.raw(path, payload))
        except (ValueError, UnicodeError):
            raise ResearchError("INVALID_JSON_RESPONSE") from None


def calendar_context(target="next-session", now=None):
    # Reuse the project's finite exchange calendar, including exchange closures.
    from stockfish_core.observed_data import exchange_calendar_2026

    now = (now or datetime.now(SH)).astimezone(SH)
    cal = exchange_calendar_2026()
    try:
        cal.is_session(now.date())
        if target == "next-session":
            requested = cal.earliest_trade_at(now).date()
        else:
            requested = now.date() + timedelta(days=1) if target == "tomorrow" else date.fromisoformat(target)
        chosen = cal.next_session(requested, inclusive=True)
    except (ValueError, RuntimeError):
        raise ResearchError("EXCHANGE_CALENDAR_OUT_OF_COVERAGE_OR_INVALID_DATE") from None
    previous = [day for day in cal.sessions if day < chosen]
    completed = [day for day in cal.sessions if datetime.combine(day, day_time(15), SH) <= now]
    if not previous or not completed:
        raise ResearchError("PREVIOUS_COMPLETED_EXCHANGE_SESSION_UNAVAILABLE")
    return {"requested_at": now.isoformat(), "market_timezone": "Asia/Shanghai",
            "requested_date": str(requested), "target_session": str(chosen),
            "converted_non_session": chosen != requested, "expected_observation_date": str(previous[-1]),
            "latest_completed_session": str(completed[-1]), "calendar_version": cal.version,
            "calendar_source": "stockfish_core/exchange_calendar_2026.json"}


def saved_runs(client):
    rows, offset = [], 0
    while True:
        page = client.get("/api/strategy-experiments?" + parse.urlencode({"limit": 1000, "offset": offset}))
        if not isinstance(page, list):
            raise ResearchError("INVALID_RUN_LIST_RESPONSE")
        rows.extend(page)
        if len(page) < 1000:
            return rows
        offset += len(page)


def prefix(run_id):
    return "/api/strategy-experiments/" + identifier(run_id)


def summary(client, run_id, page=1, page_size=10, strategy_id=PV, selection="all"):
    return client.get(prefix(run_id) + "/summary?" + parse.urlencode(
        {"strategy_id": identifier(strategy_id), "selection": selection, "page": page, "page_size": page_size}))


def concise(saved, strategy_id=PV):
    fields = ("run_id", "input_id", "observation_date", "decision_cutoff", "generated_at",
              "coverage_type", "implementation_version", "spec_hash", "source_run_hash",
              "parameter_status", "status", "counts", "filtered_total", "implementation_assessment")
    value = {key: saved.get(key) for key in fields if key in saved}
    result = saved.get("results", {}).get(strategy_id, {})
    pv_result = saved.get("results", {}).get(PV, {})
    value.update(research_profile=strategy_id, formal_primary_strategy="DAILY_V1",
                 profile_status=result.get("status", saved.get("status")), profile_count=result.get("item_count"),
                 pv_status=pv_result.get("status", saved.get("status")), pv_count=pv_result.get("item_count"),
                 focus_count=len(saved.get("focus_ids") or []),
                 strategy_states={key: row.get("status") for key, row in saved.get("results", {}).items()},
                 excerpt=saved.get("items", [])[:10], excerpt_is_complete=(saved.get("filtered_total", 11) <= 10))
    if strategy_id in SHORT_HORIZON_PROFILES:
        value.update(research_observation_count=len(result.get("research_selected_ids") or []),
                     formal_focus_count=(result.get("counts") or {}).get("focus", 0),
                     selection_role="RESEARCH_OBSERVATION_NOT_FORMAL_FOCUS")
    return value


def pick(value, fields):
    return {key: value[key] for key in fields if key in value}


def scalars(value):
    """Metadata only: never echo an embedded whole-universe record collection."""
    return {key: val for key, val in value.items() if not isinstance(val, (dict, list))}


def review_summary(review):
    result = pick(review, ("run_id", "review_id", "observation_date", "evaluated_at", "status",
                           "market_edge", "counts", "by_horizon", "uncertainty", "reason_codes"))
    result["count_unit"] = "逻辑后验标签（跨策略、窗口、证券），不是股票只数"
    result["rolling_windows"] = {}
    for key, window in review.get("rolling_windows", {}).items():
        stats = {}
        for row in window.get("statistics", []):
            if row.get("strategy_id") == PV:
                stats[str(row["horizon"])] = pick(row.get("pooled_signals", {}), (
                    "signals", "unique_instruments", "mature_valid", "not_matured", "missing_or_incomparable", "up_rate", "mean_return"))
        result["rolling_windows"][key] = {
            **pick(window, ("requested_exchange_sessions", "available_calendar_sessions", "relative_benchmark")),
            "observed_run_date_count": len(window.get("observed_run_dates", [])),
            "missing_run_date_count": len(window.get("missing_run_dates", [])),
            "pv_horizon_statistics": stats}
    result["full_review_api"] = prefix(review["run_id"]) + "/review?full=1"
    result["full_review_file"] = "report/select 输出目录中的 review.json（服务端默认复盘投影）"
    return result


def detail_summary(item):
    fields = ("instrument_id", "name", "rank", "A", "R", "H", "S_PV", "S_DNA", "S_THEME",
              "amount_cny", "mean_prior5_amount_cny", "close", "comparable_close", "comparable_close_T_minus_5",
              "rank_percentiles", "board_state", "limit_event_status", "risk_flags", "risk_review_status",
              "status", "formal_status", "primary_stage", "alternate_stages", "liquidity_status", "sector_status",
              "funds_status", "funds_grade", "relative_strength_status", "ret1_pct", "ret3_pct", "ret5_pct",
              "ret10_pct", "CLV", "amplitude_pct", "low_return_pct", "close_return_pct", "recovery_points_pct",
              "RS_market_1d_pct", "RS_sector_1d_pct", "reason", "reason_codes", "selected", "close_location",
              "parent_rank", "parent_score")
    result = pick(item, ("run_id", "instrument_id", "observation_date", "market_edge"))
    result["strategy_results"] = {sid: [pick(row, fields) for row in rows]
                                  for sid, rows in item.get("strategy_results", {}).items()
                                  if sid not in {"DNA_REVERSAL_V1", "THEME_LIMITUP_V1"}}
    result["focus_selection"] = pick(item.get("focus_selection") or {}, fields)
    result["outcomes"] = [pick(row, ("strategy_id", "horizon", "horizon_unit", "label_end", "label_kind", "status",
                                            "price_label_status", "metrics", "reason_codes", "evaluated_at"))
                          for row in item.get("outcomes", [])[-20:]]
    result["outcome_observation_count"] = len(item.get("outcomes", []))
    result["outcome_excerpt_policy"] = "原 API 次序末 20 条观察记录；不是去重统计，完整记录见详情 API"
    result["tracking"] = [pick(row, ("episode_id", "observation_date", "evaluated_at", "status", "state", "reason_codes"))
                          for row in item.get("tracking", [])[-10:]]
    result["tracking_excerpt_policy"] = "原 API 次序末 10 条研究周期观察"
    result["full_detail_api"] = prefix(item["run_id"]) + "/items/" + identifier(item["instrument_id"])
    result["note"] = "按需摘要省略重复哈希、原始来源列表和逐会话状态；原详情与完整名单未改写。"
    return result


def official_exports(client, run_id, strategy_id=PV, selection="all"):
    """Verify official JSON, CSV, and every saved summary page without reranking."""
    base = prefix(run_id)
    strategy_id = identifier(strategy_id)
    if selection not in {"all", "focus", "unselected"} and not (selection == "observation" and strategy_id in SHORT_HORIZON_PROFILES):
        raise ResearchError("UNKNOWN_STRATEGY_SELECTION")
    query = "&strategy_id=" + parse.quote(strategy_id) + "&selection=" + selection
    json_bytes = client.raw(base + "/export?format=json" + query)
    csv_bytes = client.raw(base + "/export?format=csv" + query)
    try:
        saved = json.loads(json_bytes)
        rows = saved["results"][strategy_id]["items"]
        csv_rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
        view = saved["export_view"]
        if saved["run_id"] != run_id or view["strategy_id"] != strategy_id or view["selection"] != selection:
            raise ResearchError("EXPORT_IDENTITY_MISMATCH")
        def saved_identity(row):
            rank = row.get("rank") if strategy_id in DECISION_PROFILES else row["rank"]
            return str(row["instrument_id"]), "" if rank is None else str(rank)
        expected = [saved_identity(row) for row in rows]
        actual = [(row["instrument_id"], row["rank"]) for row in csv_rows]
        if actual != expected or len(set(iid for iid, _ in expected)) != len(expected):
            raise ResearchError("CSV_JSON_SAVED_ORDER_MISMATCH")
        if any(row["run_id"] != run_id or row["strategy_id"] != strategy_id or
               row["observation_date"] != saved["observation_date"] for row in csv_rows):
            raise ResearchError("CSV_METADATA_MISMATCH")
        first = summary(client, run_id, page_size=500, strategy_id=strategy_id, selection=selection)
        if first["source_run_hash"] != view["source_content_hash"] or first["filtered_total"] != len(rows):
            raise ResearchError("SUMMARY_EXPORT_IDENTITY_MISMATCH")
        shown = list(first["items"])
        page = first
        while page["has_next"]:
            page = summary(client, run_id, page=page["page"] + 1, page_size=500, strategy_id=strategy_id, selection=selection)
            if page["source_run_hash"] != first["source_run_hash"]:
                raise ResearchError("SUMMARY_VERSION_CHANGED")
            shown.extend(page["items"])
        if [saved_identity(row) for row in shown] != expected:
            raise ResearchError("SUMMARY_EXPORT_SAVED_ORDER_MISMATCH")
        ranked_total = len(rows)
        if strategy_id in DECISION_PROFILES:
            decisions = [row["decision_status"] for row in rows]
            if any(value not in {"FOCUS", "WATCH", "REJECTED", "UNKNOWN"} for value in decisions):
                raise ResearchError("EXPORT_DECISION_STATUS_MISMATCH")
            if [row["decision_status"] for row in csv_rows] != decisions:
                raise ResearchError("CSV_JSON_SAVED_DECISION_MISMATCH")
            if [row["decision_status"] for row in shown] != decisions:
                raise ResearchError("SUMMARY_EXPORT_SAVED_DECISION_MISMATCH")
            ranked_total = decisions.count("FOCUS")
            if selection != "all":
                ranked_total = (saved["results"][strategy_id].get("counts") or {}).get("focus", ranked_total)
        if strategy_id in SHORT_HORIZON_PROFILES:
            result = saved["results"][strategy_id]
            selected_ids = result.get("research_selected_ids") or []
            flags = [row.get("research_selected") for row in rows]
            if any(type(value) is not bool for value in flags) or ranked_total != 0:
                raise ResearchError("RESEARCH_SELECTION_FORMAL_FOCUS_MISMATCH")
            if [row.get("research_selected") for row in shown] != flags:
                raise ResearchError("SUMMARY_RESEARCH_SELECTION_MISMATCH")
            if [row.get("research_selected") for row in csv_rows] != [str(value) for value in flags]:
                raise ResearchError("CSV_RESEARCH_SELECTION_MISMATCH")
            if selection == "all" and [row["instrument_id"] for row in rows if row["research_selected"]] != selected_ids:
                raise ResearchError("SAVED_RESEARCH_SELECTION_IDS_MISMATCH")
            if selection == "observation" and (not all(flags) or [row["instrument_id"] for row in rows] != selected_ids):
                raise ResearchError("OBSERVATION_SELECTION_IDS_MISMATCH")
            if len(selected_ids) != (result.get("counts") or {}).get("research_observation"):
                raise ResearchError("RESEARCH_OBSERVATION_COUNT_MISMATCH")
        if view["filtered_total"] != len(rows) or view["ranked_total"] != ranked_total:
            raise ResearchError("EXPORT_COUNT_MISMATCH")
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise ResearchError("INVALID_SAVED_EXPORT_SCHEMA") from None
    return saved, json_bytes, csv_bytes, first


def verify_reusable(saved, strategy_id=PV):
    from stockfish_core.yaogu.strategy_experiments import implementation_conformance

    if saved.get("coverage_type") not in REAL_COVERAGE:
        raise ResearchError("NON_REAL_OR_UNVERIFIABLE_INPUT_NOT_FOR_DAILY_RESEARCH")
    assessment = implementation_conformance(saved)
    legacy_pv_ok = strategy_id == PV and saved.get("source_hash") in READ_ONLY_LEGACY_PV_SOURCE_HASHES
    if assessment["status"] != "SPEC_CONFORMANT" and not legacy_pv_ok:
        raise ResearchError("SAVED_IMPLEMENTATION_NOT_CONFORMANT_WITH_LOCAL_CODE")
    if saved.get("input_id") != "market_" + str(saved.get("input_hash")):
        raise ResearchError("SAVED_INPUT_HASH_IDENTITY_MISMATCH")
    for field in ("effective_input_hash", "universe_hash", "source_hash", "config_hash"):
        if not re.fullmatch("[0-9a-f]{64}", str(saved.get(field, ""))):
            raise ResearchError("SAVED_REUSE_METADATA_INCOMPLETE")
    result = saved.get("results", {}).get(strategy_id, {})
    if result.get("status") not in {"COMPLETE", "EMPTY", "LIMITED_SCOPE", "INSUFFICIENT_EVIDENCE"}:
        raise ResearchError("PROFILE_DATA_UNAVAILABLE_NOT_A_ZERO_CANDIDATE_RESULT")


def select_saved(client, context, input_id=None, strategy_id=PV):
    rows = saved_runs(client)
    candidates = [row for row in rows if row.get("coverage_type") in REAL_COVERAGE
                  and row.get("observation_date", "9999") < context["target_session"]
                  and row.get("observation_date", "9999") <= context["latest_completed_session"]
                  and (input_id is None or row.get("input_id") == input_id)]
    # Saved run selection only; candidate ordering inside each run never changes.
    candidates.sort(key=lambda row: (row["observation_date"], row.get("decision_cutoff") or ""), reverse=True)
    skipped = []
    for row in candidates:
        if strategy_id not in (row.get("results") or {}):
            skipped.append({"run_id": row["run_id"], "reason": "PROFILE_NOT_IN_SAVED_RUN"})
            continue
        bundle = official_exports(client, row["run_id"], strategy_id)
        try:
            verify_reusable(bundle[0], strategy_id)
        except ResearchError as exc:
            skipped.append({"run_id": row["run_id"], "reason": str(exc)})
            continue
        return bundle, skipped
    raise ResearchError("NO_REUSABLE_REAL_RUN_INPUT_VERIFICATION_REQUIRED")


def report_text(saved, review, context=None, strategy_id=PV):
    result = saved["results"][strategy_id]
    counts = result.get("counts") or {}
    focus = saved.get("selection_results", {}).get(FOCUS, {})
    ranked = ([row for row in result.get("items", []) if row.get("research_selected") is True]
              if strategy_id in SHORT_HORIZON_PROFILES else [row for row in result.get("items", []) if row.get("rank") is not None])
    profile_title = (
        "沪深主板量价研究简报" if strategy_id == PV
        else "沪深主板阶段感知强势股研究简报" if strategy_id == STAGE_AWARE
        else "沪深主板短线强势研究观察简报" if strategy_id in SHORT_HORIZON_PROFILES
        else "沪深主板新版板块资金趋势研究简报"
    )
    lines = ["# " + profile_title, "", "本报告由正式保存结果派生；范围仅为输入实际覆盖的沪深主板。",
             "事实与计算结果：", "",
             "- 原运行：`" + saved["run_id"] + "`；输入：`" + saved["input_id"] + "`。",
             "- 行情观察日：" + saved["observation_date"] + "；原资料决策截止：" + str(saved.get("decision_cutoff")) + "。",
             "- 原运行生成时间：" + str(saved.get("generated_at")) + "。",
             "- 资料水位：" + str(saved.get("data_watermark")) + "；生成时间不代表行情新鲜度。",
             "- 策略：" + strategy_id + "；实现：" + str(saved.get("implementation_version")) + "；规格：" + str(saved.get("spec_hash")) + "。",
             "- 覆盖类型：" + str(saved.get("coverage_type")) + "；策略状态：" + str(result.get("status")) + "。",
             "- 实际名单 " + str(len(ranked)) + " 只；旧焦点 " + str(len(focus.get("selected_ids", []))) + " 只；数量来自正式条件。",
             "- 日期化合格池 " + str(counts.get("eligible")) + "；字段可评估 " + str(counts.get("evaluated")) + "；缺数 " + str(counts.get("missing")) + "。",
             "- 阶段感知 V2 为当前主动核心强势股策略；旧 DAILY/PV/FOCUS 仍保持历史身份，不重写历史。"]
    if strategy_id == THEME_CAPITAL:
        lines.append("- 新版初始研究参数状态：**尚未验证**；用户硬性要求与本版初始参数必须分开理解。")
        lines.append("- 正式名单要求成交额、换手率、均线/形态、题材联动和同题材活跃资金全部 `PASS`；`UNKNOWN` 不填补。")
    if strategy_id == STAGE_AWARE:
        lines.append("- 阶段感知 V2 初始研究参数状态：**尚未验证**；setup 标签是研究分类，不是市场事实；`MARKET_EDGE=NOT_ESTABLISHED`。")
        lines.append("- 正式 FOCUS 要求趋势、setup、行为确认、市场兼容、同题材板块/资金与关键反证全部达标；证据不足输出 WATCH/UNKNOWN。")
    if strategy_id in SHORT_HORIZON_PROFILES:
        lines += ["- 本版为显式短线研究策略；强启动、强延续、强势首次回撤分别评价，研究观察不代表正式 FOCUS。",
                  "- 完整合格池保存 " + str(counts.get("eligible")) + " 只；研究形态入选 "
                  + str(counts.get("research_observation")) + " 只；正式 FOCUS 为0；展示序号不表示推荐优先级。",
                  "- 规则应用角色：" + str(saved.get("rule_application_role")) + "；生成时间不证明当时已经存在这些规则。",
                  "- 初始参数状态：**尚未验证**；`MARKET_EDGE=NOT_ESTABLISHED`；限价、市场、题材与资金未知保持未知。"]
    if context:
        lines += ["- 目标交易日：" + context["target_session"] + "；该目标前一交易日：" + context["expected_observation_date"] + "。",
                  "- 请求时最后收盘交易日：" + context["latest_completed_session"] + "；当前报告使用最后可复用的原保存结果。"]
        if saved["observation_date"] != context["expected_observation_date"]:
            lines += ["- 目标前一交易日数据尚未体现在本名单中；这是截至上述观察日的最后可用结果，未伪称新名单。"]
    lines += ["", "## 原排序节选（最多 10 只，完整名单见同目录 JSON / CSV）", ""]
    if not ranked:
        lines.append("- 本策略视图没有实际排名行；若状态为 `INSUFFICIENT_EVIDENCE`，完整缺口见 source.json 的 coverage_diagnostics。")
    for row in ranked[:10]:
        detail = "- " + str(row.get("rank")) + ". " + row["instrument_id"] + " " + str(row.get("name", ""))
        if strategy_id == PV:
            detail += "：A=" + str(row.get("A")) + "，R=" + str(row.get("R")) + "，H=" + str(row.get("H")) + "，S_PV=" + str(row.get("S_PV"))
            detail += "；风险标记=" + json.dumps(row.get("risk_flags", []), ensure_ascii=False) + "，风险核对状态=" + str(row.get("risk_review_status", "未提供额外结论"))
        else:
            detail += "：状态=" + str(row.get("status")) + "，参考分=" + str(row.get("reference_score"))
            if strategy_id in DECISION_PROFILES:
                detail += "，阶段=" + str(row.get("primary_stage")) + "，流动性/题材/资金=" + "/".join(
                    str(row.get(k)) for k in ("liquidity_status", "sector_status", "funds_status"))
            else:
                detail += "，技术/题材/资金=" + "/".join(str(row.get(k)) for k in ("technical_status", "theme_status", "capital_status"))
                detail += "，形态=" + str(row.get("shape_type"))
        lines.append(detail + "。")
    maturity = review.get("counts", {})
    lines += ["", "## 跟踪与限制", "",
              "- 保存复盘状态：" + str(review.get("status", "见 review.json 的分窗口结果")) + "；市场优势：" + str(review.get("market_edge", "NOT_ESTABLISHED")) + "。",
              "- 保存的逻辑后验标签共 " + str(maturity.get("outcome_observations", 0)) + " 条；成熟 " + str(maturity.get("mature", 0))
              + " 条；未成熟 " + str(maturity.get("not_matured", 0)) + " 条；删失或不可用 " + str(maturity.get("censored_or_unavailable", 0)) + " 条。计数跨策略、窗口和证券，**不是股票只数**。",
              "- 已有焦点研究周期 " + str(maturity.get("focus_episode_count", 0)) + " 个；这些计数来自保存复盘，尚未执行的复盘不代表真实零收益。",
              "- 后验成熟度、缺数及不可计算项目逐窗口见 review.json；未成熟不妨碍读取研究名单。",
              "- 排名分不是上涨概率；价格上涨率不是模拟交易胜率。历史重建不等同于前瞻记录。",
              "- 本次没有新增采集或模型调用，未取得新的板块、龙虎榜、公告资料；不据焦点数量变化推断全市场情绪。",
              "- 模型推断：本程序未生成。未知：投资优势与当前可成交性未由本报告建立。",
              "- source.json / candidates.csv 为同一原运行的正式完整导出；manifest.json 记录来源与字节校验。", ""]
    for length, window in review.get("rolling_windows", {}).items():
        lines.append("- " + length + " 交易日复盘窗口：实际有运行的日期 " + str(len(window.get("observed_run_dates", [])))
                     + " 天；缺运行日期 " + str(len(window.get("missing_run_dates", []))) + " 天。")
    if review.get("uncertainty", {}).get("status"):
        lines.append("- 稳定推断状态：" + review["uncertainty"]["status"] + "；有限样本日数不能证明投资优势。")
    return "\n".join(lines)


def reusable_local_bundle(root, manifest):
    """Reuse verified local derivatives; never overwrite a prior report directory."""
    def target_key(value):
        return pick(value or {}, ("target_session", "expected_observation_date", "latest_completed_session", "calendar_version"))

    if not root.is_dir():
        return None
    permitted = {"source.json", "candidates.csv", "review.json", "report.md"}
    for candidate in sorted(root.iterdir(), reverse=True):
        if not candidate.is_dir():
            continue
        try:
            old = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
            if (old.get("run_id") != manifest["run_id"] or old.get("source_content_hash") != manifest["source_content_hash"]
                    or old.get("strategy_id", PV) != manifest.get("strategy_id", PV)
                    or target_key(old.get("target")) != target_key(manifest.get("target"))):
                continue
            old_files = old["files"]
            if not set(manifest["files"]).issubset(old_files) or not set(old_files).issubset(permitted):
                continue
            # Compare source and current review. Existing prose remains an immutable
            # report of its creation time; wording-only changes do not copy 25 MB.
            if any(old_files[name] != expected for name, expected in manifest["files"].items() if name != "report.md"):
                continue
            verified = True
            for name, expected in old_files.items():
                path = candidate / name
                if path.stat().st_size != expected["bytes"]:
                    verified = False
                    break
                with path.open("rb") as handle:
                    if hashlib.file_digest(handle, "sha256").hexdigest() != expected["sha256"]:
                        verified = False
                        break
            if verified:
                return candidate
        except (OSError, KeyError, TypeError, ValueError):
            continue
    return None


def save_bundle(client, bundle, output=None, context=None, with_report=True, strategy_id=PV,
                market_explanation=None, attachment_as_of=None):
    saved, json_bytes, csv_bytes, first = bundle
    if saved.get("coverage_type") not in REAL_COVERAGE:
        raise ResearchError("NON_REAL_OR_UNVERIFIABLE_INPUT_NOT_FOR_DAILY_RESEARCH")
    # Finish all remote reads/validation before creating any new output directory.
    review = client.get(prefix(saved["run_id"]) + "/review") if with_report else None
    now = datetime.now(SH)
    directory = Path(output) if output else ROOT / "reports" / "codex" / saved["run_id"] / now.strftime("%Y%m%dT%H%M%S%f")
    files = {"source.json": json_bytes, "candidates.csv": csv_bytes}
    if with_report:
        files["review.json"] = json.dumps(review, ensure_ascii=False, indent=2).encode("utf-8")
        rendered = report_text(saved, review, context, strategy_id)
        if market_explanation:
            from stockfish_core.yaogu.market_explanation_artifacts import archived_explanation
            snapshot = {"identity": {key: saved.get(key) for key in ("run_id", "observation_date")},
                        "input_hash": saved.get("input_hash"), "source_run_hash": saved.get("content_hash")}
            attachment = archived_explanation(market_explanation, snapshot=snapshot,
                as_of=attachment_as_of or datetime.now(SH))
            files["market-explanation-display.json"] = json.dumps(attachment, ensure_ascii=False, indent=2).encode("utf-8")
            rendered += "\n\n## 独立市场趋势解释附件\n\n" + attachment["markdown"]
        files["report.md"] = rendered.encode("utf-8")
    manifest = {"created_at": now.isoformat(), "api_base_url": client.base_url, "run_id": saved["run_id"],
                "input_id": saved["input_id"], "input_hash": saved.get("input_hash"),
                "decision_cutoff": saved.get("decision_cutoff"), "data_watermark": saved.get("data_watermark"),
                "observation_date": saved["observation_date"], "coverage_type": saved.get("coverage_type"),
                "spec_hash": saved.get("spec_hash"), "source_hash": saved.get("source_hash"),
                "source_content_hash": saved["export_view"]["source_content_hash"],
                "ranking_count": len(saved["results"][strategy_id]["items"]), "strategy_id": strategy_id,
                "selection": saved["export_view"]["selection"], "target": context,
                "verification": "OFFICIAL_JSON_CSV_ALL_SUMMARY_PAGES_SAME_IDS_RANKS_COUNT_SOURCE_HASH",
                "files": {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in files.items()}}
    if strategy_id in SHORT_HORIZON_PROFILES:
        manifest.update(qualified_pool_count=(saved["results"][strategy_id].get("counts") or {}).get("eligible"),
            research_observation_count=(saved["results"][strategy_id].get("counts") or {}).get("research_observation"),
            formal_focus_count=0, ranking_role="RESEARCH_DISPLAY_ORDER_NOT_RECOMMENDATION_PRIORITY")
    reused = reusable_local_bundle(directory.parent, manifest) if output is None else None
    result = concise(first, strategy_id)
    result.update(saved_directory=str((reused or directory).resolve()), files=[*files, "manifest.json"], target=context,
                  reused_saved_run=True, reused_local_bundle=reused is not None,
                  newly_collected_data=False, newly_computed_ranking=False)
    if reused is not None:
        return result
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise ResearchError("OUTPUT_DIRECTORY_EXISTS_PRESERVED") from None
    for name, data in files.items():
        with (directory / name).open("xb") as handle:
            handle.write(data)
    return result


def await_job(client, accepted, wait_seconds):
    if not 0 <= wait_seconds <= 60:
        raise ResearchError("WAIT_SECONDS_MUST_BE_0_TO_60")
    job_id = identifier(accepted["job_id"])
    deadline = time.monotonic() + wait_seconds
    while True:
        job = client.get("/api/jobs/" + job_id)
        state = job["state"]
        if state in TERMINAL:
            if state != "SUCCEEDED":
                raise ResearchError("JOB_" + state + ":" + job_id)
            return {"job_id": job_id, "state": state, "result": job.get("result"),
                    "note": "作业成功不表示数据完整或投资有效；继续核对数据及策略状态。"}
        if time.monotonic() >= deadline:
            return {"job_id": job_id, "state": state, "wait_expired": True,
                    "note": "有限等待已结束；任务可能继续执行，用 job 查询，不重复提交。"}
        time.sleep(min(1, max(0, deadline - time.monotonic())))


def submit_or_reuse_job(client, kind, path, body, wait_seconds, run_id=None, payload_match=None):
    jobs = client.get("/api/jobs?limit=100")
    if not isinstance(jobs, list):
        raise ResearchError("INVALID_JOB_LIST_RESPONSE")
    matches = [job for job in jobs if job.get("kind") == kind and job.get("state") not in TERMINAL
               and (run_id is None or job.get("payload", {}).get("run_id") == run_id)
               and all(job.get("payload", {}).get(key) == value for key, value in (payload_match or {}).items())]
    if matches:
        result = await_job(client, matches[0], wait_seconds)
        result["reused_inflight_job"] = True
        return result
    if len(jobs) >= 100:
        raise ResearchError("JOB_WINDOW_FULL_CHECK_INFLIGHT_REQUIRED")
    result = await_job(client, client.get(path, body), wait_seconds)
    result["reused_inflight_job"] = False
    return result


def observation_session(value):
    """Keep the observation target explicit; never convert a holiday locally."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise argparse.ArgumentTypeError("TARGET_SESSION_REQUIRES_YYYY_MM_DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("INVALID_TARGET_SESSION") from None


def daily_observation(args, client):
    """Read one server projection; optional files are a private local copy only."""
    if not 1 <= args.days <= 20:
        raise ResearchError("OBSERVATION_DAYS_MUST_BE_1_TO_20")
    query = {"days": args.days}
    if args.target_session is not None:
        query["target_session"] = args.target_session.isoformat()
    if args.as_of:
        try:
            instant = datetime.fromisoformat(args.as_of)
        except ValueError:
            raise ResearchError("INVALID_OBSERVATION_CUTOFF") from None
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ResearchError("OBSERVATION_CUTOFF_REQUIRES_TIMEZONE")
        query["as_of"] = instant.isoformat()
    path = "/api/daily-research/observation?" + parse.urlencode(query)
    report = client.get(path)
    if (not isinstance(report, dict) or report.get("schema_version") != "DAILY_OBSERVATION_V1"
            or not isinstance(report.get("current"), dict) or not isinstance(report.get("history"), list)
            or not isinstance(report.get("markdown"), str)
            or not {"report_as_of", "target_session", "requested_days", "available_days", "calendar_status"}.issubset(report)):
        raise ResearchError("INVALID_OBSERVATION_RESPONSE")
    if args.output is None:
        return report
    contents = {"report.json": json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
                "report.md": report["markdown"].encode("utf-8")}
    manifest = {"schema_version": report["schema_version"], "api_path": path,
                "source": query, "report_as_of": report["report_as_of"],
                "files": {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                          for name, raw in contents.items()}}
    contents["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    directory = Path(args.output)
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise ResearchError("OUTPUT_DIRECTORY_EXISTS_PRESERVED") from None
    for name, raw in contents.items():
        with (directory / name).open("xb") as handle:
            handle.write(raw)
    return {**report, "report_directory": str(directory.resolve())}


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--base-url", default="http://127.0.0.1:8000")
    result.add_argument("--env-file", type=Path, help="可选私有配置；只读取 STOCKFISH_API_KEY")
    result.add_argument("--timeout", type=float, default=30)
    commands = result.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("short-horizon-r2", help="原生R2只读研究：认证保存R1与原始来源，另存新产物；不写业务库")
    for flag in ("input", "base-run", "provenance", "source-receipts", "source-map", "output"):
        sub.add_argument("--" + flag, type=Path, required=True)
    sub.add_argument("--cutoff", required=True, help="真实资料截止，带时区；不会改写原R1 cutoff")
    sub.add_argument("--target-session", type=date.fromisoformat, required=True)
    sub.add_argument("--theme-market-pack", type=Path)
    sub.add_argument("--membership-mode", choices=("DATED_ONLY", "CURRENT_COHORT_BACKVIEW"), default="DATED_ONLY")
    sub = commands.add_parser("market-explanation", help="显式原生市场趋势解释：认证原件，绑定已保存run，另存附件；不重排名单")
    for flag in ("input", "run", "source-receipts", "source-map", "output"):
        sub.add_argument("--" + flag, type=Path, required=True)
    sub.add_argument("--cutoff", required=True, help="新情境真实截止，带时区；原run截止单列")
    sub.add_argument("--as-of", help="附件显示截止，带时区；早于附件或来源可见时间则不显示事实")
    sub = commands.add_parser("research-r1", help="显式离线R1研究；只读取自包含导出输入，不连接生产库")
    sub.add_argument("--input", type=Path, action="append", help="导出JSON：input/cutoff/publication/可选execution_tape")
    sub.add_argument("--mode", choices=("run", "diagnose", "summarize"), default="run")
    sub.add_argument("--saved-result", type=Path)
    sub.add_argument("--execution-spec", type=Path, help="冻结执行/统计参数覆盖JSON；不接受选股规则修改")
    sub.add_argument("--history-seconds", type=float, default=300, help="离线历史读取时间预算：0 < 秒数 <= 7200；默认300，不改变字节/RSS及证据校验")
    sub.add_argument("--history-db", type=Path, help="显式只读历史证据库；按依赖窗口读取，不迁移")
    sub.add_argument("--group", action="append", help="默认全部12组；R1_FULL / V2_CONTROL 或消融组名")
    sub.add_argument("--manifest", type=Path, help="核验冻结manifest身份后执行")
    sub.add_argument("--start", type=date.fromisoformat)
    sub.add_argument("--end", type=date.fromisoformat)
    sub.add_argument("--output", type=Path, required=True, help="不存在的私有目录")
    for name in ("status", "rules"):
        commands.add_parser(name)
    sub = commands.add_parser("list")
    sub.add_argument("--limit", type=int, default=20, help="历史运行摘要数量；不是股票候选显示上限")
    for name in ("select", "run"):
        sub = commands.add_parser(name, help="只读复用保存的研究视图，不重复计算")
        sub.add_argument("--profile", choices=RESEARCH_PROFILES, required=name == "select")
        sub.add_argument("--target", default="next-session", help="tomorrow / next-session / YYYY-MM-DD")
        sub.add_argument("--output", type=Path)
        if name == "run":
            sub.add_argument("--input-id", required=True, help="精确复用已保存输入；无匹配时关闭执行并报告")
            sub.add_argument("--strategy", choices=SHORT_HORIZON_PROFILES, help="显式执行独立新策略；只用已登记输入，不采集/切换主动策略")
            sub.add_argument("--wait-seconds", type=float, default=30, help="显式新策略作业的有界等待：0..60秒")
    for name in ("detail", "export", "report", "track"):
        sub = commands.add_parser(name)
        sub.add_argument("run_id")
        if name == "detail":
            sub.add_argument("instrument_id")
        if name in {"export", "report"}:
            sub.add_argument("--output", type=Path)
            sub.add_argument("--profile", choices=RESEARCH_PROFILES, default=PV)
            sub.add_argument("--selection", choices=("all", "focus", "unselected", "observation"), default="all")
        if name == "report":
            sub.add_argument("--market-explanation", type=Path, help="只读已保存独立附件；必须绑定同一原run/input/观察日")
            sub.add_argument("--attachment-as-of", help="附件显示截止，带时区；默认当前报告时点")
        if name == "track":
            sub.add_argument("--update", action="store_true", help="显式追加正式后验和研究周期，不重算原名单")
            sub.add_argument("--input-id", help="可选已登记后续资料 input_id")
            sub.add_argument("--wait-seconds", type=float, default=30)
    sub = commands.add_parser("capture", help="按现有服务器来源/预算采集或续采；不增大预算")
    sub.add_argument("--wait-seconds", type=float, default=30)
    sub = commands.add_parser("job", help="读取已有作业，不重试或重提")
    sub.add_argument("job_id")
    sub = commands.add_parser("deliver-saved-r2", help="完整交付冻结R2：原件认证后生成同run分组和业务解释；不采集、不重选")
    for flag in ("run", "run-manifest", "input", "sources-manifest", "output"):
        sub.add_argument("--" + flag, type=Path, required=True)
    sub.add_argument("--context-cutoff", required=True, help="新的真实情境截止，带时区；原run截止保持")
    sub.add_argument("--membership-mode", choices=("DATED_ONLY", "CURRENT_COHORT_BACKVIEW"), default="DATED_ONLY")
    sub.add_argument("--as-of", help="显示截止；缺省在全部新附件生成后取实际当前时间")
    sub = commands.add_parser("economic-direction-display", help="只读经济方向解释及同一完整分组父文件；不采集、不重选")
    sub.add_argument("--attachment", type=Path, required=True)
    sub.add_argument("--grouped-parent", type=Path, required=True, help="原完整分组文件；核对原SHA、全部观察项及子题材角色")
    sub.add_argument("--as-of", help="带时区的显示截止；晚到解释在旧截止隐藏")
    sub.add_argument("--output", type=Path, required=True, help="新的私有交付目录；不覆盖旧报告")
    sub = commands.add_parser("daily-report", help="只读 Phase 1 日报；包含失败日状态与 T+1..T+20 统计")
    target = sub.add_mutually_exclusive_group(required=True)
    target.add_argument("--target-session", type=date.fromisoformat)
    target.add_argument("--run-id")
    sub.add_argument("--as-of", help="可选带时区的历史报告截止")
    sub.add_argument("--output", type=Path, help="不存在的私有报告目录；不覆盖旧报告")
    sub.add_argument("--market-explanation", type=Path, help="只读独立附件；必须绑定同一冻结日报run/input/观察日")
    sub = commands.add_parser("daily-observation", help="只读每日交付状态与最近 20 交易日观察；不触发作业")
    sub.add_argument("--target-session", type=observation_session, help="YYYY-MM-DD；默认由服务选择今日或最近交易日")
    sub.add_argument("--days", type=int, default=20, help="交易日数量，1..20；默认 20")
    sub.add_argument("--as-of", help="可选带时区的历史观察截止")
    sub.add_argument("--output", type=Path, help="仅显式指定时保存到新的私有目录；不覆盖旧报告")
    sub = commands.add_parser("daily-outcomes", help="读取原推荐 T+1..T+20 价格观察；显式 --update 才提交追加任务")
    sub.add_argument("run_id")
    sub.add_argument("--instrument-id")
    sub.add_argument("--update", action="store_true")
    sub.add_argument("--input-id")
    sub.add_argument("--wait-seconds", type=float, default=30)
    return result


def execute(args, client):
    if args.command == "daily-observation":
        return daily_observation(args, client)
    if args.command == "daily-report":
        query = {"run_id": identifier(args.run_id)} if args.run_id else {"target_session": str(args.target_session)}
        if args.as_of:
            instant = datetime.fromisoformat(args.as_of)
            if instant.tzinfo is None or instant.utcoffset() is None:
                raise ResearchError("REPORT_CUTOFF_REQUIRES_TIMEZONE")
            query["as_of"] = instant.isoformat()
        report = client.get("/api/daily-research/report?" + parse.urlencode(query))
        if getattr(args, "market_explanation", None):
            from stockfish_core.yaogu.market_explanation_artifacts import archived_explanation
            attachment = archived_explanation(args.market_explanation, snapshot=report.get("snapshot"),
                                              as_of=report["report_as_of"])
            existing = report.get("research_attachments") or []
            if not isinstance(existing, list):
                raise ValueError("MARKET_EXPLANATION_EXISTING_ATTACHMENT_SCHEMA_INVALID")
            report = {**report, "research_attachments": [*existing, attachment],
                      "markdown": report["markdown"] + "\n\n## 独立市场趋势解释附件\n\n" + attachment["markdown"]}
        directory = args.output or ROOT / "reports" / "codex" / "daily-loop" / datetime.now(SH).strftime("%Y%m%dT%H%M%S%f")
        directory.mkdir(parents=True, exist_ok=False)
        contents = {"report.json": json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
                    "report.md": report["markdown"].encode("utf-8")}
        for name, raw in contents.items():
            (directory / name).write_bytes(raw)
        (directory / "manifest.json").write_text(json.dumps({"source": query,
            "report_as_of": report["report_as_of"],
            "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in contents.items()}},
            ensure_ascii=False, indent=2), encoding="utf-8")
        return {"report_directory": str(directory.resolve()), "delivery": report.get("delivery"),
                "identity": (report.get("snapshot") or {}).get("identity"), "report_as_of": report["report_as_of"]}
    if args.command == "daily-outcomes":
        path = "/api/daily-research/runs/" + identifier(args.run_id) + "/outcomes"
        if args.update:
            if not 0 <= args.wait_seconds <= 60:
                raise ResearchError("WAIT_SECONDS_OUT_OF_RANGE")
            body = {"input_id": identifier(args.input_id)} if args.input_id else {}
            return await_job(client, client.get(path, body), args.wait_seconds)
        if args.instrument_id:
            path += "?" + parse.urlencode({"instrument_id": identifier(args.instrument_id)})
        return client.get(path)
    if args.command == "status":
        coverage = client.get("/api/universe/coverage")
        pipeline = client.get("/api/candidate-data-pipeline")
        latest = pipeline.get("latest") or {}
        pipeline_summary = {key: latest.get(key) for key in (
            "capture_job_id", "started_at", "completed_at", "observation_date", "input_id",
            "coverage_type", "consumer_status", "actual_daily_ready", "candidate_count")}
        pipeline_summary["queue"] = {key: val for key, val in latest.get("queue", {}).items()
                                     if key != "pending_request_ids"}
        gaps = latest.get("field_level_gaps") or []
        pipeline_summary["field_gap_record_count"] = len(gaps)
        pipeline_summary["status"] = latest.get("queue", {}).get("status", "NO_SAVED_CAPTURE")
        health = client.get("/api/health")
        return {"health": {**scalars(health),
                           "components": {key: scalars(val) for key, val in health.get("components", {}).items()},
                           "candidate_research": health.get("candidate_research")},
                "coverage": {**scalars(coverage), "reason_codes": coverage.get("reason_codes", []),
                             "coverage_counts": scalars(coverage.get("coverage") or {})},
                "pipeline": pipeline_summary,
                "detail_apis": ["/api/universe/coverage", "/api/candidate-data-pipeline"],
                "formal_primary_strategy": "DAILY_V1", "research_profiles_require_explicit_selection": list(RESEARCH_PROFILES)}
    if args.command == "rules":
        return {"current_formal_config": pick(client.get("/api/strategy-configs/current"), ("config", "config_hash", "market_edge")),
                "registered_experiments": client.get("/api/strategy-experiments/specs")}
    if args.command == "list":
        if not 1 <= args.limit <= 1000:
            raise ResearchError("RUN_LIST_LIMIT_MUST_BE_1_TO_1000")
        return [concise(row) for row in client.get("/api/strategy-experiments?limit=" + str(args.limit))]
    if args.command in {"select", "run"}:
        context = calendar_context(args.target)
        explicit_strategy = getattr(args, "strategy", None)
        if explicit_strategy:
            if args.profile is not None and args.profile != explicit_strategy:
                raise ResearchError("PROFILE_AND_EXPLICIT_STRATEGY_MISMATCH")
            if not 0 <= args.wait_seconds <= 60:
                raise ResearchError("WAIT_SECONDS_OUT_OF_RANGE")
            try:
                bundle, skipped = select_saved(client, context, args.input_id, explicit_strategy)
            except ResearchError as exc:
                if str(exc) != "NO_REUSABLE_REAL_RUN_INPUT_VERIFICATION_REQUIRED":
                    raise
                body = {"input_id": identifier(args.input_id), "strategy_id": explicit_strategy}
                job = submit_or_reuse_job(client, "strategy_experiments", "/api/strategy-experiments/run",
                    body, args.wait_seconds, payload_match=body)
                if job.get("state") != "SUCCEEDED":
                    return {**job, "target": context, "research_profile": explicit_strategy}
                run_id = identifier((job.get("result") or {})["run_id"])
                bundle = official_exports(client, run_id, explicit_strategy)
                verify_reusable(bundle[0], explicit_strategy)
                skipped = []
                result = save_bundle(client, bundle, args.output, context, strategy_id=explicit_strategy)
                result.update(newly_computed_ranking=True, reused_saved_run=False, job_id=job["job_id"])
                return result
            result = save_bundle(client, bundle, args.output, context, strategy_id=explicit_strategy)
            result["skipped_non_reusable_runs"] = skipped
            return result
        if args.profile is None:
            raise ResearchError("EXPLICIT_RESEARCH_PROFILE_REQUIRED")
        bundle, skipped = select_saved(client, context, getattr(args, "input_id", None), args.profile)
        result = save_bundle(client, bundle, args.output, context, strategy_id=args.profile)
        result["skipped_non_reusable_runs"] = skipped
        return result
    if args.command in {"export", "report"}:
        return save_bundle(client, official_exports(client, args.run_id, args.profile, getattr(args, "selection", "all")), args.output,
                           with_report=args.command == "report", strategy_id=args.profile,
                           market_explanation=getattr(args, "market_explanation", None),
                           attachment_as_of=getattr(args, "attachment_as_of", None))
    if args.command == "detail":
        item = client.get(prefix(args.run_id) + "/items/" + identifier(args.instrument_id))
        if item.get("run_id") != args.run_id or item.get("instrument_id") != args.instrument_id:
            raise ResearchError("DETAIL_IDENTITY_MISMATCH")
        return detail_summary(item)
    if args.command == "track":
        if not args.update:
            return review_summary(client.get(prefix(args.run_id) + "/review"))
        if not 0 <= args.wait_seconds <= 60:
            raise ResearchError("WAIT_SECONDS_MUST_BE_0_TO_60")
        body = {"input_id": identifier(args.input_id)} if args.input_id else {}
        return submit_or_reuse_job(client, "strategy_tracking", prefix(args.run_id) + "/tracking", body,
                                  args.wait_seconds, run_id=args.run_id)
    if args.command == "capture":
        if not 0 <= args.wait_seconds <= 60:
            raise ResearchError("WAIT_SECONDS_MUST_BE_0_TO_60")
        return submit_or_reuse_job(client, "scheduled_candidate_capture", "/api/candidate-data-pipeline/capture", {}, args.wait_seconds)
    if args.command == "job":
        value = client.get("/api/jobs/" + identifier(args.job_id))
        return {key: value.get(key) for key in ("job_id", "state", "progress", "result")}
    raise ResearchError("UNKNOWN_COMMAND")


def main(argv=None):
    args = parser().parse_args(argv)
    key = ""
    try:
        if args.command == "short-horizon-r2":
            from stockfish_core.yaogu.research_context_artifacts import run_saved_context
            result = run_saved_context(input_path=args.input, base_run_path=args.base_run,
                provenance_path=args.provenance, source_receipts_path=args.source_receipts,
                source_map_path=args.source_map, output=args.output, cutoff=args.cutoff,
                target_session=args.target_session, theme_market_pack_path=args.theme_market_pack,
                membership_mode=args.membership_mode)
        elif args.command == "market-explanation":
            from stockfish_core.yaogu.market_explanation_artifacts import run_market_explanation
            result = run_market_explanation(input_path=args.input, run_path=args.run,
                source_receipts_path=args.source_receipts, source_map_path=args.source_map,
                output=args.output, cutoff=args.cutoff, as_of=args.as_of)
        elif args.command == "deliver-saved-r2":
            from stockfish_core.yaogu.saved_r2_delivery import deliver_saved_r2
            result = deliver_saved_r2(run_path=args.run, run_manifest_path=args.run_manifest,
                input_path=args.input, sources_manifest_path=args.sources_manifest,
                output=args.output, context_cutoff=args.context_cutoff,
                membership_mode=args.membership_mode, as_of=args.as_of)
        elif args.command == "economic-direction-display":
            from stockfish_core.yaogu.economic_direction_display import save_archived_economic_direction_display
            result = save_archived_economic_direction_display(args.attachment,
                grouped_parent=args.grouped_parent, output=args.output, as_of=args.as_of)
        elif args.command == "research-r1":
            from stockfish_core.yaogu.research_r1_io import run_local, summarize_saved
            if args.mode == "summarize":
                if not args.saved_result: raise ValueError("SAVED_RESULT_REQUIRED")
                result = summarize_saved(args.saved_result, args.output)
            else:
                if not args.input: raise ValueError("INPUT_REQUIRED")
                result = run_local(args.input, args.output, groups=args.group, frozen_manifest=args.manifest, start=args.start, end=args.end, history_db=args.history_db, mode=args.mode, execution_spec=args.execution_spec, history_seconds=args.history_seconds)
        else:
            key = api_key(args.env_file)
            result = execute(args, Client(args.base_url, key, args.timeout))
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        print(rendered.replace(key, "[REDACTED]") if key else rendered)
        return 0
    except ResearchError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        if args.command in {"research-r1", "short-horizon-r2", "market-explanation", "economic-direction-display", "deliver-saved-r2", "daily-report", "report"} and isinstance(exc, ValueError) and re.fullmatch(r"[A-Z][A-Z0-9_]+", str(exc).split(":")[0]):
            print(json.dumps({"status":"BLOCKED", "reason":str(exc).split(":")[0]}))
            return 2
        # No tracebacks or raw provider payloads that could contain credentials.
        print('{"status":"BLOCKED","reason":"LOCAL_DEPENDENCY_OR_RESPONSE_SCHEMA_ERROR"}')
        return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
