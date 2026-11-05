"""Explicit active-tree dispatcher; no fallback, copied strategy, or implicit retry."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
VERSION = "CODEX_ACTIVE_V1"
ROUTES = {
    "research-v2": "codex_research_protocol_v2", "trial-ledger": "research_trial_ledger_v1",
    "execution-evidence": "execution_evidence_v1", "restricted-history": "restricted_history_v1",
    "metrics": "durable_stage_metrics_v1",
    "recovery": "codex_runtime_recovery_v2", "build": "active_runtime_v1", "verify": "active_runtime_v1",
}


def need(value, code):
    if not value:
        raise ValueError(code)


def _module(name):
    return importlib.import_module("scripts." + name)


def _premarket(command, argv):
    parser = argparse.ArgumentParser(prog="codex_active_v1 " + command, allow_abbrev=False)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--registry-root", type=Path, required=True)
    if command == "premarket":
        parser.add_argument("--observation-date", required=True)
        parser.add_argument("--target-session", required=True)
        parser.add_argument("--request-key", required=True)
        parser.add_argument("--engine", choices=("original", "shared"), default="original")
    outcome_fields = ("parent-business-id", "signal-job", "signal-final-sha256", "outcome-job",
                      "outcome-final-sha256", "destination", "request-key")
    if command == "outcomes":
        for field in outcome_fields:
            parser.add_argument("--"+field, required=True)
        parser.add_argument("--outcome-business-id")
        parser.add_argument("--daily-archives")
        parser.add_argument("--daily-archives-sha256")
    # Reject duplicate identity flags rather than silently choosing the last.
    for flag in ("--source-root", "--runtime", "--registry-root", "--request-key", "--engine",
                 "--observation-date", "--target-session", "--outcome-business-id", "--daily-archives", "--daily-archives-sha256", *["--"+field for field in outcome_fields if field != "request-key"]):
        need(sum(item == flag or item.startswith(flag + "=") for item in argv) <= 1,
             "ACTIVE_DISPATCH_DUPLICATE_IDENTITY_ARGUMENT")
    args = parser.parse_args(argv)
    source, registry = args.source_root.absolute(), args.registry_root.absolute()
    need(source.resolve(strict=True) == source and registry.resolve(strict=True) == registry,
         "ACTIVE_DISPATCH_CANONICAL_ROOTS_REQUIRED")
    active = _module("active_runtime_v1")
    manifest = active.verify(source)
    if (ROOT / "ACTIVE_RUNTIME_MANIFEST.json").exists():
        need(source == ROOT, "ACTIVE_DISPATCH_CROSS_ACTIVE_TREE_FORBIDDEN")
        # Generated trees already enforce the process audit boundary. Reuse its
        # narrow, authority-bound control read, never exempt the child worker.
        runtime = _module("active_business_gateway_v1").load_runtime(args.runtime, registry)
    else:
        runtime = _module("codex_runtime_recovery_v2").load_runtime(args.runtime, registry)
    need(runtime.profile.get("backend") == "premarket" and Path(runtime.profile["source_root"]) == source,
         "ACTIVE_DISPATCH_REGISTERED_SOURCE_MISMATCH")
    need(runtime.active_source_attested is True, "ACTIVE_DISPATCH_ACTIVE_SOURCE_ATTESTATION_REQUIRED")
    relative_entry = "scripts/active_outcome_pipeline_v1.py" if command == "outcomes" else "scripts/codex_daily_v2.py"
    entry = source / relative_entry
    need(relative_entry in manifest["output_sha256"], "ACTIVE_DISPATCH_ENTRY_NOT_IN_MANIFEST")
    selected = ["--runtime", args.runtime, "--registry-root", str(registry)]
    if command != "outcomes":
        selected.append(command)
    else:
        for field in outcome_fields:
            selected += ["--"+field, getattr(args, field.replace("-", "_"))]
        if args.outcome_business_id is not None:
            selected += ["--outcome-business-id", args.outcome_business_id]
        need((args.daily_archives is None) == (args.daily_archives_sha256 is None),
             "ACTIVE_DISPATCH_DAILY_DESCRIPTOR_PAIR_REQUIRED")
        if args.daily_archives is not None:
            selected += ["--daily-archives", args.daily_archives, "--daily-archives-sha256", args.daily_archives_sha256]
    if command == "premarket":
        selected += ["--observation-date", args.observation_date, "--target-session", args.target_session,
                     "--request-key", args.request_key, "--engine", args.engine]
    runtime.check_binding()
    need(active.verify(source) == manifest, "ACTIVE_DISPATCH_SOURCE_CHANGED_BEFORE_START")
    # The original entry owns supervision, business claims, budgets and cleanup.
    # A nonzero exit is returned once, never retried with another tree or key.
    result = subprocess.run([runtime.profile["python"], "-I", "-B", str(entry), *selected], cwd=source)
    runtime.check_binding()
    need(active.verify(source) == manifest, "ACTIVE_DISPATCH_SOURCE_CHANGED_AFTER_RUN")
    return result.returncode


def delivery_main(argv):
    """Only compose existing plan/audit/freeze contracts; never fetch a calendar."""
    parser = argparse.ArgumentParser(prog="codex_active_v1 delivery-protocol", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    plan_parser = commands.add_parser("plan", allow_abbrev=False)
    plan_parser.add_argument("--first-target-session", required=True)
    plan_parser.add_argument("--strategy-id", default="SHORT_HORIZON_STRENGTH_R2")
    plan_parser.add_argument("--sessions", type=int, default=20)
    plan_parser.add_argument("--deadline-local", default="09:00")
    plan_parser.add_argument("--acceptance-scope", choices=("STRICT_PROSPECTIVE_PIT", "PROSPECTIVE_OPERATIONAL_DELIVERY"))
    def calendar_options(sub):
        sub.add_argument("--calendar-state-root", type=Path)
        sub.add_argument("--calendar-record")
        sub.add_argument("--calendar-record-sha256")
        sub.add_argument("--max-read-bytes", type=int, default=512*1024**2)
        sub.add_argument("--max-seconds", type=float, default=180)
    calendar_options(plan_parser)
    for name in ("inspect-sequence", "freeze"):
        sub = commands.add_parser(name, allow_abbrev=False)
        sub.add_argument("--protocol", required=True)
        sub.add_argument("--protocol-sha256", required=True)
        sub.add_argument("--authority-database", type=Path, required=True)
        sub.add_argument("--workspace-id", required=True)
        if name == "freeze":
            sub.add_argument("--epoch-id", required=True)
            sub.add_argument("--authorize-freeze", action="store_true")
            sub.add_argument("--authorization-id", required=True)
        else:
            sub.add_argument("--requests", required=True)
            sub.add_argument("--requests-sha256", required=True)
            sub.add_argument("--protocol-business-id")
            calendar_options(sub)
    for sub in commands.choices.values():
        sub.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    io, contract = _module("codex_p0"), _module("daily_delivery_contract_v1")
    refs = []
    calendar_budget = None
    def native_calendar():
        nonlocal calendar_budget
        supplied = [args.calendar_state_root is not None, args.calendar_record is not None, args.calendar_record_sha256 is not None]
        need(all(supplied) or not any(supplied), "ACTIVE_DELIVERY_COMPLETE_NATIVE_CALENDAR_LOCATOR_REQUIRED")
        if not any(supplied):
            return {}
        # Validate explicit budgets before constructing even a read-only handle.
        contract._AuditBudget(args.max_read_bytes, args.max_seconds)
        current = _module("codex_current_calendar_v2")
        record, reference = io.read_snapshot(args.calendar_record, args.calendar_record_sha256)
        refs.append(reference)
        calendar_budget = current._budget(dict(capture_read_bytes=args.max_read_bytes, max_seconds=args.max_seconds))
        cache = current._cache(SimpleNamespace(profile={"state_root": str(args.calendar_state_root)}), calendar_budget, readonly=True)
        return dict(calendar_cache=cache, calendar_record=record)
    need(not args.output.exists() and not args.output.is_symlink(), "ACTIVE_DELIVERY_NEW_OUTPUT_REQUIRED")
    if args.command == "plan":
        axis, evidence, reference = _module("codex_research_protocol_v1")._registered_axis()
        rows, day = [], axis.coverage_start
        while day <= axis.coverage_end:
            rows.append(dict(calendar_date=str(day), is_trading_day="1" if day in axis.sessions else "0"))
            day += timedelta(days=1)
        refs.append(reference)
        native = native_calendar()
        if native:
            _module("codex_current_calendar_v2").validate_calendar(native["calendar_cache"], native["calendar_record"], now=datetime.now(timezone.utc))
            rows = native["calendar_cache"].verify(native["calendar_record"])["rows"]
            reference = dict(reference, native_record_sha256=args.calendar_record_sha256)
        result = contract.plan_protocol(rows, first_target_session=args.first_target_session,
            strategy_id=args.strategy_id, calendar_reference=dict(reference, official_evidence=evidence),
            session_count=args.sessions, deadline_local=args.deadline_local, acceptance_scope=args.acceptance_scope)
        pairs = result["expected_sessions"]
        need(all(axis.coverage_start.isoformat() <= p["observation_date"] < p["target_session"] <= axis.coverage_end.isoformat() for p in pairs),
             "ACTIVE_DELIVERY_FINITE_OFFICIAL_CALENDAR_REQUIRED")
        official = [str(d) for d in axis.sessions]
        start = official.index(pairs[0]["target_session"])
        need(start > 0 and official[start-1] == pairs[0]["observation_date"]
             and official[start:start+len(pairs)] == [p["target_session"] for p in pairs], "ACTIVE_DELIVERY_NATIVE_OFFICIAL_CALENDAR_MISMATCH")
    else:
        protocol, reference = io.read_snapshot(args.protocol, args.protocol_sha256)
        refs.append(reference)
        if protocol.get("entry_version") == VERSION:
            protocol = protocol["result"]
        owner = _module("business_authority_v1").Authority(args.authority_database, args.workspace_id)
        if args.command == "freeze":
            need(args.authorize_freeze and args.authorization_id.strip(), "ACTIVE_DELIVERY_EXPLICIT_FREEZE_AUTHORIZATION_REQUIRED")
            io.verify_snapshots(refs)
            result = contract.freeze_protocol(owner, protocol, epoch_id=args.epoch_id)
            result = dict(result, cli_authorization_id=args.authorization_id)
        else:
            batch, reference = io.read_snapshot(args.requests, args.requests_sha256)
            refs.append(reference)
            need(type(batch) is dict and set(batch) == {"requests"} and type(batch["requests"]) is list,
                 "ACTIVE_DELIVERY_REQUEST_DESCRIPTOR_LIST_REQUIRED")
            calendar_args = native_calendar()
            result = contract.audit_sequence(owner, protocol, batch["requests"],
                protocol_business_id=args.protocol_business_id, max_read_bytes=args.max_read_bytes,
                max_seconds=args.max_seconds, **calendar_args)
    io.verify_snapshots(refs)
    io.publish_new(args.output, dict(entry_version=VERSION, command="delivery-protocol " + args.command,
        result=result, input_carriers=refs, calendar_native_resources=calendar_budget.metrics if calendar_budget else None,
        collection_started=False, market_edge="NOT_ESTABLISHED"),
        validate=lambda: io.verify_snapshots(refs))
    print(json.dumps(dict(status=result.get("status", "PLAN_ONLY" if args.command == "plan" else "EXPLICITLY_FROZEN"),
        output=str(args.output), collection_started=False, market_edge="NOT_ESTABLISHED")))
    return 4 if args.command == "inspect-sequence" and result["status"] != "REAL_DAILY_ACCEPTANCE_ESTABLISHED" else 0


def main(argv=None):
    selected = list(sys.argv[1:] if argv is None else argv)
    commands = [*ROUTES, "premarket-plan", "premarket", "outcomes", "delivery-protocol", "delivery-audit"]
    if not selected or selected[0] in {"--help", "-h"}:
        print("codex_active_v1 <command> [arguments]\ncommands: " + ", ".join(commands))
        return 0 if selected else 2
    command, arguments = selected[0], selected[1:]
    try:
        if command in {"premarket-plan", "premarket", "outcomes"}:
            return _premarket(command, arguments)
        if command == "delivery-audit":
            return _module("active_read_control_v1").dispatch("delivery-audit", arguments)
        if command == "delivery-protocol":
            if arguments[:1] == ["inspect-sequence"]:
                return _module("active_read_control_v1").dispatch("inspect-sequence", arguments[1:])
            return delivery_main(arguments)
        need(command in ROUTES, "ACTIVE_DISPATCH_UNKNOWN_COMMAND")
        forwarded = [command, *arguments] if command in {"build", "verify"} else arguments
        return _module(ROUTES[command]).main(forwarded)
    except KeyboardInterrupt:
        print(json.dumps(dict(status="INTERRUPTED", retry_authorized=False)))
        return 130
    except Exception as error:
        reason = str(error)
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,160}", reason) is None:
            reason = "ACTIVE_DISPATCH_REJECTED_OR_UNAVAILABLE"
        print(json.dumps(dict(status="BLOCKED_OR_FAILED", reason=reason, retry_authorized=False,
            mutation_may_have_committed="INSPECT_ORIGINAL_AUTHORITY_OR_LEDGER; DO_NOT_RETRY")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
