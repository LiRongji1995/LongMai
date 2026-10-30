"""Explicit P0 diagnostics over unchanged saved evidence; never starts research.

Local byte integrity is not source authority. Native R2 execution is exposed by
codex_r2_execution_v1.evaluate_r2, not reconstructed from this JSON interface.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import codex_result_view_v1 as files
from scripts import codex_workspace as workspace

VERSION = "CODEX_P0_RESEARCH_DIAGNOSTICS_V1"
MAX_STORED_BYTES = 64 * 1024**2
MAX_JSON_BYTES = 256 * 1024**2
MAX_REPORT_BYTES = 16 * 1024**2


class DiagnosticError(ValueError):
    pass


def need(condition, reason):
    if not condition:
        raise DiagnosticError(reason)


def read_snapshot(path, expected_sha256, *, max_stored=MAX_STORED_BYTES,
                  max_json=MAX_JSON_BYTES):
    """Read a whole JSON/gzip carrier with fixed bounds and no-follow identity."""
    need(isinstance(expected_sha256, str) and
         re.fullmatch(r"[0-9a-f]{64}", expected_sha256), "P0_FILE_SHA256_REQUIRED")
    selected = Path(path).expanduser().absolute()
    raw, reference = files._read_file(selected, maximum=max_stored)
    need(reference["sha256"] == expected_sha256, "P0_INPUT_FILE_HASH_MISMATCH")
    compressed = raw[:2] == b"\x1f\x8b"
    need(selected.suffix != ".gz" or compressed, "P0_GZIP_HEADER_REQUIRED")
    if compressed:
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                decoded = stream.read(max_json + 1)
        except (OSError, EOFError) as error:
            raise DiagnosticError("P0_INVALID_GZIP") from error
    else:
        decoded = raw
    need(len(decoded) <= max_json, "P0_DECODED_JSON_SIZE_LIMIT")
    value = files._json(decoded)
    need(type(value) is dict, "P0_FULL_JSON_OBJECT_REQUIRED")
    return value, reference


def verify_snapshots(references):
    for reference in references:
        files._read_file(reference["path"], maximum=MAX_STORED_BYTES,
                         expected=reference, retain=False)


def declared_session_coverage(report, sessions):
    """Explicit expectations are not an exchange calendar or evidence of absence."""
    sessions = list(sessions)
    need(len(sessions) <= 366 and len(sessions) == len(set(sessions)),
         "P0_UNIQUE_BOUNDED_EXPECTED_SESSIONS_REQUIRED")
    for session in sessions:
        need(type(session) is str and date.fromisoformat(session).isoformat() == session,
             "P0_ISO_SESSION_REQUIRED")
    rows = report.get("reports", [])
    coverage = []
    for session in sorted(sessions):
        matching = [row for row in rows if row.get("observation_date") == session]
        state = ("NO_SUPPORTED_DELIVERY_OPERATION_IN_SELECTED_SCOPE" if not matching else
                 "MULTIPLE_OPERATIONS_RECONCILIATION_REQUIRED" if len(matching) > 1 else
                 matching[0]["workflow_status"])
        coverage.append(dict(observation_date=session, status=state,
                             operation_ids=[row["operation_id"] for row in matching]))
    return dict(denominator_role="CALLER_DECLARED_SESSIONS_NOT_CALENDAR_AUTHENTICATION",
                declared_session_count=len(sessions), sessions=coverage,
                unsupported_or_other_operation_count=report.get("unsupported_or_other_operation_count", 0),
                inventory_support_complete=report.get("unsupported_or_other_operation_count", 0) == 0,
                status_counts=dict(sorted(Counter(row["status"] for row in coverage).items())),
                complete_data_delivery_rate=None, user_delivery_rate=None,
                rate_status="UNAVAILABLE_WITHOUT_COMPLETE_DATA_AND_USER_DELIVERY_EVIDENCE",
                absence_does_not_prove_no_job=True, continuous_delivery_accepted=False)


def publish_new(path, value, *, validate=None):
    """Publish only a new private report; an existing destination is never replaced."""
    path = Path(path).expanduser().absolute()
    need(path.suffix == ".json", "P0_JSON_REPORT_PATH_REQUIRED")
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    need(len(raw) <= MAX_REPORT_BYTES, "P0_REPORT_SIZE_LIMIT")
    with workspace._directory_fd(path.parent) as parent:
        temporary = ".p0-" + uuid.uuid4().hex + ".tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             os.O_NOFOLLOW, 0o600, dir_fd=parent)
        owned = os.fstat(descriptor)
        parent_identity = os.fstat(parent)
        published = False

        def is_owned(name):
            try:
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return False
            return (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino)

        def remove_owned(name):
            if is_owned(name):
                os.unlink(name, dir_fd=parent)
                return True
            return False

        def verify_parent():
            with workspace._directory_fd(path.parent) as current_parent:
                current = os.fstat(current_parent)
                need((current.st_dev, current.st_ino) ==
                     (parent_identity.st_dev, parent_identity.st_ino),
                     "P0_REPORT_PARENT_IDENTITY_CHANGED")

        def verify_report(expected=None):
            # The held directory can remain writable after its pathname moves.
            # Reopen the requested pathname as well as verifying the new inode.
            verify_parent()
            _, reference = files._read_file(path, maximum=MAX_REPORT_BYTES,
                                            expected=expected, retain=False, private=True)
            verify_parent()
            need((reference["fp"]["st_dev"], reference["fp"]["st_ino"]) ==
                 (owned.st_dev, owned.st_ino) and
                 reference["sha256"] == hashlib.sha256(raw).hexdigest(),
                 "P0_PUBLISHED_REPORT_IDENTITY_OR_HASH_CHANGED")
            return reference

        try:
            # Keep the descriptor open through cleanup, so an unlinked inode
            # cannot be reused while it remains this invocation's identity.
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if validate is not None:
                validate()
            need(is_owned(temporary), "P0_TEMPORARY_IDENTITY_CHANGED")
            try:
                os.link(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent,
                        follow_symlinks=False)
                published = True
            except FileExistsError as error:
                raise DiagnosticError("P0_EXISTING_REPORT_PRESERVED") from error
            need(is_owned(path.name), "P0_PUBLISHED_REPORT_IDENTITY_OR_HASH_CHANGED")
            need(remove_owned(temporary), "P0_TEMPORARY_IDENTITY_CHANGED")
            os.fsync(parent)
            reference = verify_report()
            if validate is not None:
                validate()
            verify_report(reference)
        except BaseException:
            if published and remove_owned(path.name):
                os.fsync(parent)
            raise
        finally:
            try:
                remove_owned(temporary)
            finally:
                os.close(descriptor)
    return str(path)


def parser():
    result = argparse.ArgumentParser(description="P0交付、覆盖及执行证据诊断；不采集或重试",
                                     allow_abbrev=False)
    commands = result.add_subparsers(dest="command", required=True)
    delivery = commands.add_parser("delivery", allow_abbrev=False)
    choice = delivery.add_mutually_exclusive_group(required=True)
    for name in ("job", "operation", "preflight"):
        choice.add_argument("--" + name)
    delivery.add_argument("--state-root")
    delivery.add_argument("--deadline", help="调用方提供的有时区目标；不证明此前已登记")
    ledger = commands.add_parser("ledger", allow_abbrev=False)
    ledger.add_argument("--runtime", required=True)
    ledger.add_argument("--registry-root")
    ledger.add_argument("--recovered-runtime", action="store_true")
    ledger.add_argument("--expected-session", action="append", default=[])
    for command in ("coverage", "execution-readiness"):
        sub = commands.add_parser(command, allow_abbrev=False)
        sub.add_argument("--run", required=True, help="完整原canonical run JSON或gzip；不是筛选导出")
        sub.add_argument("--run-file-sha256", required=True, help="保存载体的精确SHA256")
        if command == "coverage":
            sub.add_argument("--outcomes")
            sub.add_argument("--outcomes-file-sha256")
    for sub in commands.choices.values():
        sub.add_argument("--output", help="新私有JSON文件；父目录必须存在，不覆盖")
    return result


def execute(args):
    references = []
    if args.command == "delivery":
        from scripts import codex_delivery_audit_v1 as audit
        if args.job:
            value = audit.audit_job(args.job, deadline=args.deadline)
        elif args.operation:
            need(args.state_root, "P0_OPERATION_STATE_ROOT_REQUIRED")
            value = audit.audit_operation(args.operation, state_root=args.state_root,
                                          deadline=args.deadline)
        else:
            need(args.deadline is None, "P0_PREFLIGHT_HAS_NO_DELIVERY_DEADLINE")
            value = audit.audit_preflight(args.preflight)
    elif args.command == "ledger":
        from scripts import codex_delivery_audit_v1 as audit
        if args.recovered_runtime:
            from scripts import codex_runtime_recovery_v1 as recovery
            runtime = recovery.load_runtime(args.runtime, args.registry_root)
        else:
            runtime = workspace.load_runtime(args.runtime, args.registry_root)
        need(runtime is not None, "P0_EXISTING_REGISTERED_RUNTIME_REQUIRED")
        value = audit.audit_runtime(runtime)
        value["declared_session_coverage"] = declared_session_coverage(value, args.expected_session)
    else:
        if args.command == "coverage":
            need(bool(args.outcomes) == bool(args.outcomes_file_sha256),
                 "P0_OUTCOMES_FILE_AND_HASH_REQUIRED_TOGETHER")
        run, reference = read_snapshot(args.run, args.run_file_sha256)
        references.append(reference)
        if args.command == "coverage":
            from scripts.codex_research_coverage_v1 import build_coverage_report
            outcomes = None
            if args.outcomes:
                outcomes, reference = read_snapshot(args.outcomes, args.outcomes_file_sha256)
                references.append(reference)
            value = build_coverage_report(run, outcomes)
        else:
            from scripts.codex_r2_execution_v1 import readiness
            value = readiness(run, expected_content_hash=run.get("content_hash"))
        verify_snapshots(references)
    return dict(entry_version=VERSION, command=args.command,
                inspected_at=datetime.now(timezone.utc).isoformat(),
                input_carriers=references, source_reauthenticated_now=False,
                source_authority_restored=False, network_calls=0,
                business_database_opened=False, retry_authorized=False,
                market_edge="NOT_ESTABLISHED", result=value)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        value = execute(args)
        # A valid readiness diagnosis is not a completed execution evaluation.
        code = 4 if value["result"].get("status") == "BLOCKED_DATA" else 0
        value["command_exit_code"] = code
        if args.output:
            location = publish_new(args.output, value,
                                   validate=lambda: verify_snapshots(value["input_carriers"]))
            print(json.dumps(dict(entry_version=VERSION, command=args.command,
                                  report=location, command_exit_code=code,
                                  source_reauthenticated_now=False), ensure_ascii=False))
        else:
            print(json.dumps(value, ensure_ascii=False, allow_nan=False))
        return code
    except Exception as error:
        # Never echo provider payloads, arbitrary file contents or private paths.
        message = str(error)
        reason = message if re.fullmatch(r"[A-Z][A-Z0-9_]{2,160}", message) else "P0_EVIDENCE_REJECTED_OR_UNAVAILABLE"
        print(json.dumps(dict(entry_version=VERSION, command=args.command,
                              status="DIAGNOSTIC_FAILED", reason=reason, command_exit_code=2),
                         ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
