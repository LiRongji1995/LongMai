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
REAL_COVERAGE = {"PROSPECTIVE_CAPTURED", "RECONSTRUCTED_HISTORICAL"}
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}


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
    def __init__(self, base_url="http://127.0.0.1:8000", key="", timeout=30):
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
        self.base_url, self.key, self.timeout = base_url.rstrip("/"), key, timeout
        self.opener = request.build_opener(request.ProxyHandler({}), NoRedirect())

    def raw(self, path, payload=None):
        if not path.startswith("/api/") or ".." in path or "\\" in path:
            raise ResearchError("INVALID_API_PATH")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = request.Request(self.base_url + path, data=data, headers=headers)
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                # No automatic retry: a timed-out POST may already have succeeded.
                content = response.read(64 * 1024 * 1024 + 1)
        except error.HTTPError as exc:
            raise ResearchError("HTTP_" + str(exc.code)) from None
        except (error.URLError, TimeoutError, OSError):
            raise ResearchError("NETWORK_FAILED_POST_STATE_UNKNOWN" if payload is not None
                                else "LOCAL_SERVICE_UNREACHABLE") from None
        if len(content) > 64 * 1024 * 1024:
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


def summary(client, run_id, page=1, page_size=10):
    return client.get(prefix(run_id) + "/summary?" + parse.urlencode(
        {"strategy_id": PV, "selection": "all", "page": page, "page_size": page_size}))


def concise(saved):
    fields = ("run_id", "input_id", "observation_date", "decision_cutoff", "generated_at",
              "coverage_type", "implementation_version", "spec_hash", "source_run_hash",
              "parameter_status", "status", "counts", "filtered_total", "implementation_assessment")
    value = {key: saved.get(key) for key in fields if key in saved}
    result = saved.get("results", {}).get(PV, {})
    value.update(research_profile=PV, formal_primary_strategy="DAILY_V1",
                 pv_status=result.get("status", saved.get("status")), pv_count=result.get("item_count"),
                 focus_count=len(saved.get("focus_ids") or []),
                 strategy_states={key: row.get("status") for key, row in saved.get("results", {}).items()},
                 excerpt=saved.get("items", [])[:10], excerpt_is_complete=(saved.get("filtered_total", 11) <= 10))
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
              "status", "reason", "reason_codes", "selected", "close_location", "parent_rank", "parent_score")
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


def official_exports(client, run_id):
    """Verify official JSON, CSV, and every saved summary page without reranking."""
    base = prefix(run_id)
    query = "&strategy_id=" + PV + "&selection=all"
    json_bytes = client.raw(base + "/export?format=json" + query)
    csv_bytes = client.raw(base + "/export?format=csv" + query)
    try:
        saved = json.loads(json_bytes)
        rows = saved["results"][PV]["items"]
        csv_rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
        view = saved["export_view"]
        if saved["run_id"] != run_id or view["strategy_id"] != PV or view["selection"] != "all":
            raise ResearchError("EXPORT_IDENTITY_MISMATCH")
        expected = [(str(row["instrument_id"]), str(row["rank"])) for row in rows]
        actual = [(row["instrument_id"], row["rank"]) for row in csv_rows]
        if actual != expected or len(set(iid for iid, _ in expected)) != len(expected):
            raise ResearchError("CSV_JSON_SAVED_ORDER_MISMATCH")
        if any(row["run_id"] != run_id or row["strategy_id"] != PV or
               row["observation_date"] != saved["observation_date"] for row in csv_rows):
            raise ResearchError("CSV_METADATA_MISMATCH")
        first = summary(client, run_id, page_size=500)
        if first["source_run_hash"] != view["source_content_hash"] or first["filtered_total"] != len(rows):
            raise ResearchError("SUMMARY_EXPORT_IDENTITY_MISMATCH")
        shown = list(first["items"])
        page = first
        while page["has_next"]:
            page = summary(client, run_id, page=page["page"] + 1, page_size=500)
            if page["source_run_hash"] != first["source_run_hash"]:
                raise ResearchError("SUMMARY_VERSION_CHANGED")
            shown.extend(page["items"])
        if [(str(row["instrument_id"]), str(row["rank"])) for row in shown] != expected:
            raise ResearchError("SUMMARY_EXPORT_SAVED_ORDER_MISMATCH")
        if view["filtered_total"] != len(rows) or view["ranked_total"] != len(rows):
            raise ResearchError("EXPORT_COUNT_MISMATCH")
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise ResearchError("INVALID_SAVED_EXPORT_SCHEMA") from None
    return saved, json_bytes, csv_bytes, first


def verify_reusable(saved):
    from stockfish_core.yaogu.strategy_experiments import implementation_conformance

    if saved.get("coverage_type") not in REAL_COVERAGE:
        raise ResearchError("NON_REAL_OR_UNVERIFIABLE_INPUT_NOT_FOR_DAILY_RESEARCH")
    if implementation_conformance(saved)["status"] != "SPEC_CONFORMANT":
        raise ResearchError("SAVED_IMPLEMENTATION_NOT_CONFORMANT_WITH_LOCAL_CODE")
    if saved.get("input_id") != "market_" + str(saved.get("input_hash")):
        raise ResearchError("SAVED_INPUT_HASH_IDENTITY_MISMATCH")
    for field in ("effective_input_hash", "universe_hash", "source_hash", "config_hash"):
        if not re.fullmatch("[0-9a-f]{64}", str(saved.get(field, ""))):
            raise ResearchError("SAVED_REUSE_METADATA_INCOMPLETE")
    if saved.get("results", {}).get(PV, {}).get("status") not in {"COMPLETE", "EMPTY", "LIMITED_SCOPE"}:
        raise ResearchError("PV_DATA_UNAVAILABLE_NOT_A_ZERO_CANDIDATE_RESULT")


def select_saved(client, context, input_id=None):
    rows = saved_runs(client)
    candidates = [row for row in rows if row.get("coverage_type") in REAL_COVERAGE
                  and row.get("observation_date", "9999") < context["target_session"]
                  and row.get("observation_date", "9999") <= context["latest_completed_session"]
                  and (input_id is None or row.get("input_id") == input_id)]
    # Saved run selection only; candidate ordering inside each run never changes.
    candidates.sort(key=lambda row: (row["observation_date"], row.get("decision_cutoff") or ""), reverse=True)
    skipped = []
    for row in candidates:
        bundle = official_exports(client, row["run_id"])
        try:
            verify_reusable(bundle[0])
        except ResearchError as exc:
            skipped.append({"run_id": row["run_id"], "reason": str(exc)})
            continue
        return bundle, skipped
    raise ResearchError("NO_REUSABLE_REAL_RUN_INPUT_VERIFICATION_REQUIRED")


def report_text(saved, review, context=None):
    result = saved["results"][PV]
    counts = result.get("counts") or {}
    focus = saved.get("selection_results", {}).get(FOCUS, {})
    lines = ["# 沪深主板量价研究简报", "", "本报告由正式保存结果派生；范围仅为输入实际覆盖的沪深主板。",
             "事实与计算结果：", "",
             "- 原运行：`" + saved["run_id"] + "`；输入：`" + saved["input_id"] + "`。",
             "- 行情观察日：" + saved["observation_date"] + "；原资料决策截止：" + str(saved.get("decision_cutoff")) + "。",
             "- 原运行生成时间：" + str(saved.get("generated_at")) + "。",
             "- 资料水位：" + str(saved.get("data_watermark")) + "；生成时间不代表行情新鲜度。",
             "- 策略：" + PV + "；实现：" + str(saved.get("implementation_version")) + "；规格：" + str(saved.get("spec_hash")) + "。",
             "- 覆盖类型：" + str(saved.get("coverage_type")) + "；PV 状态：" + str(result.get("status")) + "。",
             "- 完整候选 " + str(len(result["items"])) + " 只；焦点 " + str(len(focus.get("selected_ids", []))) + " 只；数量来自正式条件。",
             "- 日期化合格池 " + str(counts.get("eligible")) + "；字段可评估 " + str(counts.get("evaluated")) + "；缺数 " + str(counts.get("missing")) + "。",
             "- DAILY_V1 保持正式主策略身份；本报告是已明确选择的独立 PV 量价研究。"]
    if context:
        lines += ["- 目标交易日：" + context["target_session"] + "；该目标前一交易日：" + context["expected_observation_date"] + "。",
                  "- 请求时最后收盘交易日：" + context["latest_completed_session"] + "；当前报告使用最后可复用的原保存结果。"]
        if saved["observation_date"] != context["expected_observation_date"]:
            lines += ["- 目标前一交易日数据尚未体现在本名单中；这是截至上述观察日的最后可用结果，未伪称新名单。"]
    lines += ["", "## 原排序节选（最多 10 只，完整名单见同目录 JSON / CSV）", ""]
    for row in result["items"][:10]:
        lines.append("- " + str(row.get("rank")) + ". " + row["instrument_id"] + " " + str(row.get("name", ""))
                     + "：A=" + str(row.get("A")) + "，R=" + str(row.get("R")) + "，H=" + str(row.get("H"))
                     + "，S_PV=" + str(row.get("S_PV")) + "；风险标记=" + json.dumps(row.get("risk_flags", []), ensure_ascii=False)
                     + "，风险核对状态=" + str(row.get("risk_review_status", "未提供额外结论")) + "。")
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


def save_bundle(client, bundle, output=None, context=None, with_report=True):
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
        files["report.md"] = report_text(saved, review, context).encode("utf-8")
    manifest = {"created_at": now.isoformat(), "api_base_url": client.base_url, "run_id": saved["run_id"],
                "input_id": saved["input_id"], "input_hash": saved.get("input_hash"),
                "decision_cutoff": saved.get("decision_cutoff"), "data_watermark": saved.get("data_watermark"),
                "observation_date": saved["observation_date"], "coverage_type": saved.get("coverage_type"),
                "spec_hash": saved.get("spec_hash"), "source_hash": saved.get("source_hash"),
                "source_content_hash": saved["export_view"]["source_content_hash"],
                "ranking_count": len(saved["results"][PV]["items"]), "selection": "all", "target": context,
                "verification": "OFFICIAL_JSON_CSV_ALL_SUMMARY_PAGES_SAME_IDS_RANKS_COUNT_SOURCE_HASH",
                "files": {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in files.items()}}
    reused = reusable_local_bundle(directory.parent, manifest) if output is None else None
    result = concise(first)
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


def submit_or_reuse_job(client, kind, path, body, wait_seconds, run_id=None):
    jobs = client.get("/api/jobs?limit=100")
    if not isinstance(jobs, list):
        raise ResearchError("INVALID_JOB_LIST_RESPONSE")
    matches = [job for job in jobs if job.get("kind") == kind and job.get("state") not in TERMINAL
               and (run_id is None or job.get("payload", {}).get("run_id") == run_id)]
    if matches:
        result = await_job(client, matches[0], wait_seconds)
        result["reused_inflight_job"] = True
        return result
    if len(jobs) >= 100:
        raise ResearchError("JOB_WINDOW_FULL_CHECK_INFLIGHT_REQUIRED")
    result = await_job(client, client.get(path, body), wait_seconds)
    result["reused_inflight_job"] = False
    return result


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--base-url", default="http://127.0.0.1:8000")
    result.add_argument("--env-file", type=Path, help="可选私有配置；只读取 STOCKFISH_API_KEY")
    result.add_argument("--timeout", type=float, default=30)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("status", "rules"):
        commands.add_parser(name)
    sub = commands.add_parser("list")
    sub.add_argument("--limit", type=int, default=20, help="历史运行摘要数量；不是股票候选显示上限")
    for name in ("select", "run"):
        sub = commands.add_parser(name, help="只读复用保存的 PV 研究，不重复计算")
        sub.add_argument("--profile", choices=[PV], required=True)
        sub.add_argument("--target", default="next-session", help="tomorrow / next-session / YYYY-MM-DD")
        sub.add_argument("--output", type=Path)
        if name == "run":
            sub.add_argument("--input-id", required=True, help="精确复用已保存输入；无匹配时关闭执行并报告")
    for name in ("detail", "export", "report", "track"):
        sub = commands.add_parser(name)
        sub.add_argument("run_id")
        if name == "detail":
            sub.add_argument("instrument_id")
        if name in {"export", "report"}:
            sub.add_argument("--output", type=Path)
        if name == "track":
            sub.add_argument("--update", action="store_true", help="显式追加正式后验和研究周期，不重算原名单")
            sub.add_argument("--input-id", help="可选已登记后续资料 input_id")
            sub.add_argument("--wait-seconds", type=float, default=30)
    sub = commands.add_parser("capture", help="按现有服务器来源/预算采集或续采；不增大预算")
    sub.add_argument("--wait-seconds", type=float, default=30)
    sub = commands.add_parser("job", help="读取已有作业，不重试或重提")
    sub.add_argument("job_id")
    return result


def execute(args, client):
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
                "formal_primary_strategy": "DAILY_V1", "research_profile_requires_explicit_selection": PV}
    if args.command == "rules":
        return {"current_formal_config": pick(client.get("/api/strategy-configs/current"), ("config", "config_hash", "market_edge")),
                "registered_experiments": client.get("/api/strategy-experiments/specs")}
    if args.command == "list":
        if not 1 <= args.limit <= 1000:
            raise ResearchError("RUN_LIST_LIMIT_MUST_BE_1_TO_1000")
        return [concise(row) for row in client.get("/api/strategy-experiments?limit=" + str(args.limit))]
    if args.command in {"select", "run"}:
        context = calendar_context(args.target)
        bundle, skipped = select_saved(client, context, getattr(args, "input_id", None))
        result = save_bundle(client, bundle, args.output, context)
        result["skipped_non_reusable_runs"] = skipped
        return result
    if args.command in {"export", "report"}:
        return save_bundle(client, official_exports(client, args.run_id), args.output,
                           with_report=args.command == "report")
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
        key = api_key(args.env_file)
        result = execute(args, Client(args.base_url, key, args.timeout))
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        print(rendered.replace(key, "[REDACTED]") if key else rendered)
        return 0
    except ResearchError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    except (OSError, KeyError, TypeError, ValueError, RuntimeError):
        # No tracebacks or raw provider payloads that could contain credentials.
        print('{"status":"BLOCKED","reason":"LOCAL_DEPENDENCY_OR_RESPONSE_SCHEMA_ERROR"}')
        return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
