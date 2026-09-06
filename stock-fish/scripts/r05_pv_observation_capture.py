"""R05 量价观察控制组真实采集。

只采集 PV_OBSERVATION_V1 必需的公开数据：当前主板目录、交易日历、
近 6 个交易日 RAW 日线、当前状态、退市整理排除集和全池复权因子。
题材、资金、竞价和后验不在本脚本中伪造，缺口由实验结果标注。
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stockfish_core.yaogu.config import StrategyConfig
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.data_pipeline import _write_once, master_ids
from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report
from stockfish_core.yaogu.provider_requests import FIELD_VERSION, execute_request
from stockfish_core.yaogu.request_queue import RequestBudget, RequestQueue
from stockfish_core.yaogu.strategy_experiments import run_strategy_experiments


SH = ZoneInfo("Asia/Shanghai")


def progress(value):
    print(json.dumps({
        "消息": "R05量价采集进度",
        "已登记请求": value["requested"],
        "状态": value["states"],
        "本轮网络请求": value["network_requests_this_run"],
        "本轮缓存命中": value["cache_hits_this_run"],
        "已用秒": round(value["elapsed_seconds"], 2),
    }, ensure_ascii=False), flush=True)


def csv_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["rank", "instrument_id", "name", "S_PV", "A", "R", "H", "PR_A", "PR_R", "PR_H", "comparable_basis"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fields})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--observation-date")
    parser.add_argument("--max-requests", type=int, default=96)
    parser.add_argument("--max-seconds", type=float, default=900)
    parser.add_argument("--timeout-seconds", type=float, default=45)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("输出目录必须是新目录；续跑请复用 cache 并指定新 output")
    now = datetime.now(SH)
    from stockfish_core.observed_data import exchange_calendar_2026
    registered = tuple(sorted(exchange_calendar_2026().sessions))
    day = datetime.fromisoformat(args.observation_date).date() if args.observation_date else max(d for d in registered if d <= now.date())
    args.output.mkdir(parents=True, exist_ok=True)
    stages = []
    with RequestQueue(args.cache, budget=RequestBudget(max_requests=args.max_requests, max_seconds=args.max_seconds,
                                                       timeout_seconds=args.timeout_seconds)) as queue:
        def add(endpoint, parameters, stage, *, current=False):
            return queue.enqueue(endpoint, parameters, stage=stage, definition_version=FIELD_VERSION,
                                 cache_epoch=now.strftime("%Y-%m-%dT%H") if current else None)

        def drain(stage, ids):
            before = queue.calls
            queue.drain(execute_request, ids=ids, progress=progress)
            rows = queue.rows(ids)
            stages.append({"stage": stage, "request_ids": list(ids), "requested": len(rows),
                           "complete": sum(r["state"] == "COMPLETE" for r in rows),
                           "network_calls": queue.calls - before,
                           "status": "COMPLETE" if rows and all(r["state"] == "COMPLETE" for r in rows) else "PARTIAL"})

        masters = [add(endpoint, {}, "R05MainBoardMaster", current=True) for endpoint in ("mainboard_sse", "master_szse")]
        calendar_id = add("calendar", {"start_date": str(day - timedelta(days=800)), "end_date": str(day)}, "R05Calendar")
        drain("R05IdentityCalendar", masters + [calendar_id])
        initial = queue.results()
        ids = master_ids(initial)
        calendar_rows = [row for result in initial if result.get("endpoint") == "calendar" and result.get("status") == "COMPLETE"
                         for row in result.get("rows", ()) if str(row.get("is_trading_day", row.get("isTradingDay", "1"))) == "1"]
        sessions = tuple(sorted({datetime.fromisoformat(str(row.get("calendar_date", row.get("calendarDate")))).date()
                                 for row in calendar_rows if row.get("calendar_date", row.get("calendarDate"))}))
        sessions = tuple(session for session in sessions if session <= day)
        calendar_source = "CAPTURED_PROVIDER_CALENDAR" if len(sessions) >= 60 and sessions[-1] == day else "REGISTERED_2026_FALLBACK"
        if calendar_source != "CAPTURED_PROVIDER_CALENDAR":
            sessions = tuple(d for d in registered if d <= day)
        else:
            sessions = tuple(d for d in sessions if d <= day)
        pv_sessions = sessions[-6:]
        plan = {"capture_version": "R05_PV_OBSERVATION_CAPTURE_V1", "captured_at": now.isoformat(),
                "observation_date": str(day), "master_observed_count": len(ids),
                "calendar_source": calendar_source, "calendar_sessions": len(sessions),
                "pv_price_sessions": [str(d) for d in pv_sessions],
                "scope": "PV_OBSERVATION_REQUIRED_DATA_ONLY", "market_edge": "NOT_ESTABLISHED"}
        _write_once(args.output / "plan.json", plan)
        followups = [add("market_daily", {"date": str(session)}, "R05RawDaily", current=session == day) for session in pv_sessions]
        followups.append(add("master_all", {"date": str(day)}, "R05CurrentStatus", current=True))
        followups.append(add("delisting_sse", {}, "R05CurrentStatus", current=True))
        followups.extend(add("adjustment_factors", {"codes": ids[i:i + 100], "start_date": "1990-01-01", "end_date": str(day)},
                             "R05ComparablePrice") for i in range(0, len(ids), 100))
        drain("R05PVRequiredData", followups)
        results = queue.results()
        normalized = normalize_capture_with_report(results, observation_date=day, now=datetime.now(SH), sessions=sessions)
        bundle, normalization = normalized
        experiments = run_strategy_experiments(bundle, cutoff=datetime.now(SH), config=StrategyConfig())
        pv = experiments["results"]["PV_OBSERVATION_V1"]
        report = {"capture_version": "R05_PV_OBSERVATION_CAPTURE_V1", "started_at": now.isoformat(),
                  "completed_at": datetime.now(SH).isoformat(), "observation_date": str(day),
                  "input_id": "market_" + digest(bundle), "input_hash": digest(bundle),
                  "market_input_path": "market-input.json", "normalization": normalization,
                  "stages": stages, "queue": queue.summary(),
                  "strategy_experiment_path": "strategy-experiments.json",
                  "pv_status": pv["status"], "pv_ranked": pv["counts"]["ranked"],
                  "pv_missing_required_data": pv["counts"]["missing_required_data"],
                  "market_edge": "NOT_ESTABLISHED",
                  "remaining_requests": [{k: r[k] for k in ("request_id", "state", "attempts", "failure_category", "payload")}
                                         for r in queue.rows() if r["state"] != "COMPLETE"]}
        _write_once(args.output / "market-input.json", bundle.model_dump(mode="json"))
        _write_once(args.output / "normalization-report.json", normalization)
        _write_once(args.output / "strategy-experiments.json", experiments)
        _write_once(args.output / "capture-report.json", report)
        csv_rows(args.output / "pv-observation-top.csv", pv.get("items", []))
        print(json.dumps({"消息": "R05量价观察采集完成", "观察日": str(day), "PV状态": pv["status"],
                          "PV入围": pv["counts"]["ranked"], "输出": str(args.output),
                          "市场边际": "NOT_ESTABLISHED"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
