"""Explicit bounded first delivery after a separately authenticated recovery.

This command authorizes collection of missing public originals under one new
configuration. It does not declare the missing graph authenticated beforehand.
Original collectors admit each newly discovered scope before contacting it.
Old daily entrypoints, source contracts and failed journals remain unchanged.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_daily_v2 as daily
from scripts import codex_premarket as original
from scripts import codex_runtime_recovery_v1 as recovery
from scripts import codex_premarket_shared_v1 as workflow
from stockfish_core.yaogu import premarket_workflow_v1 as frozen
from stockfish_core.yaogu.premarket_runtime_v1 import Budget

VERSION = "CODEX_RECOVERED_PREMARKET_V1"
PROFILE = "REAL_PREMARKET_RECOVERY_3H_V1"
GIB = 1024**3
CAPS = dict(max_requests=1024, max_sdk_calls=10000, max_http_attempts=32768,
    request_http_attempts=64, request_timeout_seconds=90, max_seconds=10800,
    cache_bytes=GIB, output_bytes=768*1024**2, capture_read_bytes=32*GIB,
    core_read_bytes=32*GIB, theme_read_bytes=32*GIB,
    presentation_read_bytes=4*GIB, supervisor_read_bytes=8*GIB,
    self_rss_bytes=int(2.5*GIB), tree_rss_bytes=3*GIB)
need = original._need


def configuration(runtime, profile):
    need(profile == PROFILE, "RECOVERED_EXPLICIT_COLLECTION_PROFILE_REQUIRED")
    value = frozen.load_config(runtime.profile["config"])
    need(set(value["limits"]) == set(CAPS), "RECOVERED_LIMIT_FIELDS")
    for key, cap in CAPS.items():
        limit = value["limits"][key]
        need(type(limit) in (int, float) and 0 < limit <= cap,
             "RECOVERED_PROFILE_LIMIT_EXCEEDED:" + key)
    need(value["capture"].get("allow_test_fixture", False) is False,
         "RECOVERED_REAL_SOURCE_REQUIRED")
    need(value["capture"].get("retry_failed", False) is False,
         "RECOVERED_AUTOMATIC_RETRY_FORBIDDEN")
    need(not value["seed_sources"], "RECOVERED_SEEDS_REQUIRE_SEPARATE_AUTHENTICATION")
    need(value["state_root"] == runtime.profile["state_root"], "RECOVERED_STATE_BINDING")
    return value


def calendar_gate(prepared, config, observation, target, budget):
    """Authenticate an existing calendar before any request is claimed."""
    from stockfish_core.yaogu.premarket_capture_cache_v1 import CaptureCache
    root = prepared["state"] / "cache"
    for name in ("index", "raw", "raw-index", "locators"):
        child = root / name
        need(child.is_dir() and not child.is_symlink(), "RECOVERED_EXISTING_CACHE_REQUIRED")
    def no_network(*args, **kwargs):
        raise original.PremarketError("RECOVERED_CALENDAR_GATE_NETWORK_FORBIDDEN")
    options = dict(config["capture"], offline=True, retry_failed=False)
    with budget.phase("capture"), budget.meter_files():
        cache = CaptureCache(root, budget, config=options, executor=no_network)
        now = datetime.now(timezone.utc)
        record = cache.calendar_reference(now)
        need(record is not None, "RECOVERED_AUTHENTICATED_CALENDAR_REQUIRED")
        result = cache.verify(record)
        need(result["status"] == "COMPLETE" and result["pagination_complete"],
             "RECOVERED_COMPLETE_CALENDAR_REQUIRED")
        gate = frozen._latest_gate(result["rows"], observation, target, now)
        cache.terminal()
        budget.check("recovered_calendar_gate", force=True)
        return gate


def run(runtime, args):
    need(isinstance(runtime, recovery.RecoveryRuntime), "RECOVERED_ATTESTED_RUNTIME_REQUIRED")
    observation, target = str(args.observation_date), str(args.target_session)
    need(date.fromisoformat(observation).isoformat() == observation
         and date.fromisoformat(target).isoformat() == target and observation < target,
         "RECOVERED_ORDERED_SESSIONS_REQUIRED")
    need(re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", args.request_key) is not None,
         "RECOVERED_REQUEST_KEY_REQUIRED")
    prepared = original._prepared(runtime.profile)
    need(prepared["source"] == ROOT and Path(sys.executable).absolute() == prepared["python"],
         "RECOVERED_REGISTERED_PYTHON_AND_SOURCE_REQUIRED")
    config = configuration(runtime, args.profile)
    snapshot = daily._snapshot(prepared)
    extra = [recovery.reference(Path(path).resolve()) for path in
             (__file__, recovery.__file__)]
    identity = dict(schema_version=VERSION, backend="premarket", request_key=args.request_key,
        observation_date=observation, target_session=target,
        configuration=snapshot["configuration"], execution_engine="original",
        source_closure_sha256=hashlib.sha256(original._canonical(snapshot)).hexdigest(),
        recovery_entry_sha256=hashlib.sha256(original._canonical(extra)).hexdigest(),
        explicit_collection_profile=args.profile)
    parent = None

    def unchanged():
        daily._same(prepared, snapshot)
        recovery._verify_refs(extra)
        runtime.check_binding()

    def budget():
        limits = config["limits"]
        return Budget({**limits, "capture_read_bytes": min(
            limits["capture_read_bytes"], limits["supervisor_read_bytes"])})

    def action():
        nonlocal parent
        parent = budget()
        with parent.phase("capture"), parent.meter_files():
            unchanged()
            daily._prior_request(runtime, identity)
        calendar_gate(prepared, config, observation, target, parent)
        day = str(datetime.now(timezone.utc).astimezone(frozen.SH).date())
        expected = (observation, target, day)
        workflow._expected_gate(expected)
        # The original collector verifies the real calendar, dates and each
        # exact stage's finite request cost. No UNKNOWN is relabelled admitted.
        code, final = workflow.supervise(config, progress=True, started_at=parent.started,
                                        expected_sessions=expected, engine="original")
        parent.charge_read(final.get("supervisor_returned_read_bytes", 0))
        parent.check("recovered_supervisor_return", force=True)
        with parent.phase("capture"), parent.meter_files():
            unchanged()
            need(code in (0, 2) and final.get("status") in original.SUCCESS
                 and code == original.SUCCESS[final["status"]],
                 "RECOVERED_EXECUTION_FAILED_RETAINED_NO_RETRY")
            job = original._job_path(prepared, final["job"])
            saved, final_ref = original._control(job / "final-control.json")
            need(saved == final, "RECOVERED_RETURNED_FINAL_CHANGED")
            completion, completion_ref = original._control(Path(final["completion"]["path"]))
            return dict(schema_version=daily.VERSION, status=final["status"], job=str(job),
                execution_engine="original", expected_sessions=list(expected),
                observation_date=observation, target_session=target, run_id=completion["run_id"],
                strategy_id=completion.get("strategy_id"), theme_status=completion.get("theme_status"),
                final=final_ref, completion=completion_ref, execution_exit_code=code,
                execution_seconds=time.monotonic()-parent.started,
                display_artifacts={key: ref["path"] for key, ref in final["display"]["artifacts"].items()},
                source_authority_restored=False, result_role="ARCHIVED_REUSE_NOT_NEW_CAPTURE")

    def validate(receipt):
        nonlocal parent
        if parent is None:
            parent = budget()
        with parent.phase("capture"), parent.meter_files():
            unchanged()
            result = daily._validate(prepared, snapshot, receipt, observation, target, "original")
            unchanged()
        result["parent_metrics"] = parent.metrics
        return result

    # RecoveredRuntime holds both ancestor and current resource locks and
    # rejects prior completed/failed/uncertain requests before any new action.
    with runtime.exclusive():
        runtime.guard_ancestor_request(identity)
        daily._prior_request(runtime, identity)
        calendar_gate(prepared, config, observation, target, budget())
    result = runtime.run_once(identity, action, validate)
    parent.check("recovered_coordinator_return", force=True)
    result.update(entry_version=VERSION, explicit_collection_profile=args.profile,
                  complete_daily_delivery=True, daily_v1_readiness="UNKNOWN_NOT_EXECUTED",
                  market_edge="NOT_ESTABLISHED")
    return result["receipt"]["execution_exit_code"], result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--registry-root", type=Path)
    parser.add_argument("--profile", choices=[PROFILE], required=True)
    parser.add_argument("--observation-date", type=date.fromisoformat, required=True)
    parser.add_argument("--target-session", type=date.fromisoformat, required=True)
    parser.add_argument("--request-key", required=True)
    selected = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(selected)
    try:
        runtime = recovery.load_runtime(args.runtime, args.registry_root)
        prepared = original._prepared(runtime.profile)
        if (ROOT != prepared["source"] or Path(sys.executable).absolute() != prepared["python"]
                or sys.pycache_prefix is None or Path(sys.pycache_prefix).exists()):
            with tempfile.TemporaryDirectory(prefix="stockfish-recovered-premarket-") as directory:
                command = [str(prepared["python"]), "-I", "-B", "-X", "pycache_prefix=" +
                    str(Path(directory)/"absent"), str(prepared["source"] / "scripts" / Path(__file__).name), *selected]
                environment = dict(os.environ)
                for key in ("PYTHONPATH", "PYTHONHOME"):
                    environment.pop(key, None)
                return subprocess.call(command, cwd=prepared["source"], env=environment)
        code, result = run(runtime, args)
    except KeyboardInterrupt:
        code, result = 130, dict(status="INTERRUPTED", reason="USER_INTERRUPTED")
    except Exception as error:
        code, result = 2, dict(status="BLOCKED_OR_FAILED", reason=daily.legacy._safe_reason(error))
    print(json.dumps({**result, "entry_version": VERSION, "command_exit_code": code},
                     ensure_ascii=False, allow_nan=False), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
