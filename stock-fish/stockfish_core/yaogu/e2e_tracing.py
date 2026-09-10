"""Lightweight Daily Primary E2E tracing.

The trace is intentionally file/JSON friendly so it can be attached to review
bundles and later emitted by a Go core without sharing Python internals.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import os
import time

from ..common import utc_now


TRACE_VERSION = "DAILY_PRIMARY_E2E_TRACE_V1"


def _rss_bytes():
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss
    except Exception:
        return None


class DailyPrimaryTrace:
    def __init__(self, *, run_id=None, input_id=None, snapshot_id=None, as_of_session=None,
                 command=None, git_commit=None, benchmark_version=None):
        self.started_at = utc_now()
        self.trace = {
            "trace_version": TRACE_VERSION,
            "run_id": run_id,
            "input_id": input_id,
            "snapshot_id": snapshot_id,
            "as_of_session": str(as_of_session) if as_of_session is not None else None,
            "command": command,
            "git_commit": git_commit,
            "benchmark_version": benchmark_version,
            "created_at": self.started_at.isoformat(),
            "process_id": os.getpid(),
            "stages": [],
            "status": "RUNNING",
            "rss_start_bytes": _rss_bytes(),
            "rss_peak_bytes": _rss_bytes(),
        }

    @contextmanager
    def stage(self, name, *, item_count=None, snapshot_id=None, run_id=None, metadata=None):
        started_perf = time.perf_counter()
        started_at = utc_now()
        rss_start = _rss_bytes()
        row = {
            "name": name,
            "start": started_at.isoformat(),
            "end": None,
            "elapsed_ms": None,
            "success": None,
            "item_count": item_count,
            "snapshot_id": snapshot_id,
            "run_id": run_id,
            "rss_start_bytes": rss_start,
            "rss_end_bytes": None,
            "rss_peak_bytes": rss_start,
            "bytes_read": None,
            "bytes_written": None,
            "cache_stats": None,
            "metadata": dict(metadata or {}),
        }
        try:
            yield row
        except BaseException as exc:
            row["success"] = False
            row["error_type"] = type(exc).__name__
            row["error"] = str(exc)
            raise
        else:
            row["success"] = True
        finally:
            ended = utc_now()
            rss_end = _rss_bytes()
            row["end"] = ended.isoformat()
            row["elapsed_ms"] = (time.perf_counter() - started_perf) * 1000
            row["rss_end_bytes"] = rss_end
            row["rss_peak_bytes"] = max(value for value in (rss_start, rss_end) if value is not None) if any(
                value is not None for value in (rss_start, rss_end)
            ) else None
            self.trace["stages"].append(row)
            peaks = [self.trace.get("rss_peak_bytes"), row.get("rss_peak_bytes")]
            self.trace["rss_peak_bytes"] = max(value for value in peaks if value is not None) if any(
                value is not None for value in peaks
            ) else None

    def annotate_last(self, **values):
        if not self.trace["stages"]:
            return
        stage = self.trace["stages"][-1]
        for key, value in values.items():
            if key == "metadata":
                stage.setdefault("metadata", {}).update(value or {})
            else:
                stage[key] = value

    def record_stage(self, name, *, elapsed_ms=0.0, success=True, item_count=None,
                     snapshot_id=None, run_id=None, cache_stats=None, bytes_read=None,
                     bytes_written=None, metadata=None):
        rss = _rss_bytes()
        now = utc_now().isoformat()
        row = {
            "name": name,
            "start": now,
            "end": now,
            "elapsed_ms": float(elapsed_ms or 0.0),
            "success": bool(success),
            "item_count": item_count,
            "snapshot_id": snapshot_id,
            "run_id": run_id,
            "rss_start_bytes": rss,
            "rss_end_bytes": rss,
            "rss_peak_bytes": rss,
            "bytes_read": bytes_read,
            "bytes_written": bytes_written,
            "cache_stats": cache_stats,
            "metadata": dict(metadata or {}),
        }
        self.trace["stages"].append(row)
        peaks = [self.trace.get("rss_peak_bytes"), rss]
        self.trace["rss_peak_bytes"] = max(value for value in peaks if value is not None) if any(
            value is not None for value in peaks
        ) else None

    def finish(self, *, status="SUCCEEDED", run_id=None, result_hash=None, metadata=None):
        ended = utc_now()
        self.trace["status"] = status
        self.trace["finished_at"] = ended.isoformat()
        self.trace["elapsed_ms"] = (ended - self.started_at).total_seconds() * 1000
        if run_id:
            self.trace["run_id"] = run_id
        if result_hash:
            self.trace["result_hash"] = result_hash
        if metadata:
            self.trace.setdefault("metadata", {}).update(metadata)
        return self.trace


def stage_names():
    return (
        "USER_REQUEST_COMMAND_START",
        "CALENDAR_TRADING_SESSION_RESOLUTION",
        "DATA_FRESHNESS_STATUS_CHECK",
        "PROVIDER_REFRESH_INCREMENTAL_INGESTION",
        "HISTORY_ARTIFACT_UPDATE",
        "MARKET_INPUT_CREATION_OR_LOAD",
        "SOURCE_RESOLUTION",
        "DEPENDENCY_PLANNING",
        "CACHE_LOOKUP",
        "FEATURE_COMPUTATION",
        "STRATEGY_COMPUTE",
        "PERSISTENCE",
        "CANDIDATE_EXPORT",
        "REPORT_RENDER_API_RESPONSE",
        "RESULT_READY",
    )
