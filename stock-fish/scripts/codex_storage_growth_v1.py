"""Capacity headroom and immutable growth observations for a registered runtime.

Metadata observations never authorize deleting evidence or changing the existing
capacity policy. No market payload, database, or cloud placeholder is opened.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_capacity as capacity
from scripts import codex_workspace as workspace

VERSION = "CODEX_STORAGE_GROWTH_V1"
MIB = 1024 ** 2
MAX_SAMPLES = 256
MAX_SAMPLE_BYTES = 64 * 1024
SAMPLE_RESERVE = MAX_SAMPLE_BYTES + 8192
OPERATION_RESERVES = {"FULL_CORE_COMPACT_VERIFY_V2": 260 * MIB}
SUPERVISOR_CONTROL_RESERVE = 2 * MIB


def _need(condition, reason):
    if not condition:
        raise workspace.WorkspaceError(reason)


def _identity(runtime):
    return workspace._digest({"scope": runtime.scope, "binding": runtime.binding,
                              "storage_policy": runtime.profile["storage"]})


def plan(runtime, *, operation="FULL_CORE_COMPACT_VERIFY_V2", growth_per_run_bytes=None):
    """One complete original inventory, with all destination checks derived from it."""
    _need(runtime is not None, "GROWTH_REGISTERED_RUNTIME_REQUIRED")
    _need(operation in OPERATION_RESERVES, "GROWTH_UNKNOWN_OPERATION")
    if growth_per_run_bytes is not None:
        _need(type(growth_per_run_bytes) is int and growth_per_run_bytes > 0,
              "GROWTH_POSITIVE_SCENARIO_BYTES_REQUIRED")
    runtime.check_binding()
    policy = runtime.profile["storage"]
    report = runtime.storage_report()
    runtime.check_binding()
    worker_reserve = OPERATION_RESERVES[operation]
    # run_once performs the registered generic preflight before the worker's
    # own 260 MiB check; control files may already exist at those checks.
    reserve = max(worker_reserve, policy["expected_growth_bytes"]) + SUPERVISOR_CONTROL_RESERVE
    complete = report["scan_complete"]
    used = report["totals"]["unique_logical_bytes"]
    managed_headroom = policy["max_managed_bytes"] - used if complete else None
    targets = []
    destinations = [Path(runtime.profile[key]) for key in ("state_root", "report_root")]
    if runtime.profile["backend"] == "api":
        destinations.append(Path(runtime.profile["db_path"]).parent)
    for item in report["roots"]:
        root = Path(item["path"])
        if not any(path == root or root in path.parents for path in destinations):
            continue
        free = item["filesystem"]["free_bytes"] if item["filesystem"] is not None else None
        available = free - policy["min_free_bytes"] if free is not None else None
        reasons = []
        if not complete:
            reasons.append("STORAGE_INVENTORY_INCOMPLETE")
        if item["role"] == "protected":
            reasons.append("TARGET_ROOT_PROTECTED")
        if free is None:
            reasons.append("TARGET_FREE_SPACE_UNKNOWN")
        elif available < reserve:
            reasons.append("MINIMUM_FREE_SPACE_WOULD_BE_BREACHED")
        if managed_headroom is not None and managed_headroom < reserve:
            reasons.append("MANAGED_LOGICAL_BUDGET_EXCEEDED")
        targets.append(dict(name=item["name"], free_bytes=free,
                            available_above_disk_floor_bytes=available, reason_codes=reasons))
    _need(targets, "GROWTH_WRITE_DESTINATION_NOT_COVERED")
    available = None
    if complete and all(row["available_above_disk_floor_bytes"] is not None for row in targets):
        available = min(managed_headroom, *(row["available_above_disk_floor_bytes"] for row in targets))
    net_headroom = available - reserve if available is not None else None
    reasons = sorted({reason for row in targets for reason in row["reason_codes"]})
    return dict(schema_version=VERSION, status="COMPLETE" if complete else "PARTIAL",
                observed_at=report["observed_at"], runtime_identity=_identity(runtime),
                runtime_name=runtime.profile["name"], operation=operation,
                read_only=True, snapshot_consistent=False, deletion_authorized=False,
                capacity_policy_changed=False, reservation_created=False,
                managed_logical_bytes=used if complete else None,
                allocated_bytes=report["totals"]["allocated_bytes"] if complete else None,
                managed_limit_bytes=policy["max_managed_bytes"],
                managed_headroom_bytes=managed_headroom, peak_reserve_bytes=worker_reserve,
                generic_preflight_bytes=policy["expected_growth_bytes"],
                supervisor_control_reserve_bytes=SUPERVISOR_CONTROL_RESERVE,
                admission_reserve_bytes=reserve, admission_is_advisory=True,
                net_growth_before_next_operation_blocks_bytes=net_headroom,
                next_operation_admissible=not reasons, reason_codes=reasons,
                destinations=targets,
                scenario=None if growth_per_run_bytes is None else dict(
                    growth_per_run_bytes=growth_per_run_bytes,
                    further_full_growth_steps_before_next_operation_blocks=(
                        max(0, net_headroom // growth_per_run_bytes) if net_headroom is not None else None),
                    prediction=False, includes_new_raw_attachments=False),
                retention_policy=[
                    dict(kind="RAW_RECEIPT_PRODUCER_OLD_RESULT_RUN", action="RETAIN_PINNED_EVIDENCE"),
                    dict(kind="ENVIRONMENT", action="RETAIN_PROTECTED_RUNTIME"),
                    dict(kind="FAILED_UNKNOWN_OR_UNREFERENCED", action="REVIEW_DEPENDENCIES_NO_DELETE"),
                    dict(kind="NEW_OWNED_UNPUBLISHED_STAGING", action="ORIGINAL_SUCCESSFUL_OWNER_CLEANUP_ONLY")],
                inventory=report)


def _directory(runtime):
    return Path(runtime.profile["report_root"]) / "storage-growth-v1"


def _envelope(payload):
    return dict(schema_version=VERSION, payload=payload,
                payload_sha256=hashlib.sha256(workspace._canonical(payload)).hexdigest())


def _payload(value, runtime):
    _need(type(value) is dict and set(value) == {"schema_version", "payload", "payload_sha256"}
          and value["schema_version"] == VERSION, "GROWTH_SAMPLE_SCHEMA")
    payload = value["payload"]
    _need(hashlib.sha256(workspace._canonical(payload)).hexdigest() == value["payload_sha256"],
          "GROWTH_SAMPLE_BYTES_CHANGED")
    _need(payload["runtime_identity"] == _identity(runtime), "GROWTH_SAMPLE_DIFFERENT_RUNTIME_OR_POLICY")
    _need(payload["schema_version"] == VERSION and payload["status"] == "COMPLETE"
          and payload["deletion_authorized"] is False and payload["snapshot_consistent"] is False,
          "GROWTH_SAMPLE_NOT_COMPLETE_METADATA_OBSERVATION")
    return payload


def _samples(runtime):
    directory = _directory(runtime)
    if not directory.exists() and not directory.is_symlink():
        return []
    with workspace._directory_fd(directory) as fd:
        import os
        names = os.listdir(fd)
    _need(len(names) <= MAX_SAMPLES, "GROWTH_SAMPLE_COUNT_LIMIT")
    _need(all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\.json", name) for name in names),
          "GROWTH_UNEXPECTED_LEDGER_MEMBER")
    result = [_payload(workspace._read(directory / name, limit=MAX_SAMPLE_BYTES), runtime) for name in names]
    return sorted(result, key=lambda row: (row["observed_at"], row["sample_id"]))


def growth_history(runtime):
    """Signed deltas are observations, not a forecast or attribution to one worker."""
    runtime.check_binding()
    rows = _samples(runtime)
    runtime.check_binding()
    deltas = []
    for before, after in zip(rows, rows[1:]):
        deltas.append(dict(before=before["sample_id"], after=after["sample_id"],
            observed_from=before["observed_at"], observed_to=after["observed_at"],
            net_logical_bytes=after["managed_logical_bytes"] - before["managed_logical_bytes"],
            per_root_net_logical_bytes={name: size - before["root_logical_bytes"][name]
                for name, size in after["root_logical_bytes"].items()},
            attribution="ALL_CHANGES_BETWEEN_OBSERVATIONS_NOT_EXCLUSIVE_OPERATION_GROWTH"))
    return dict(schema_version=VERSION, status="OBSERVATIONS_ONLY", samples=len(rows),
                deltas=deltas, deletion_authorized=False, prediction=False)


def record(runtime, sample_id):
    _need(type(sample_id) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", sample_id),
          "GROWTH_SAMPLE_ID_REQUIRED")
    with runtime.exclusive():
        directory = _directory(runtime)
        existing = _samples(runtime)
        for payload in existing:
            if payload["sample_id"] == sample_id:
                return dict(status="REUSED_ORIGINAL_OBSERVATION", sample=payload,
                            sample_path=str(directory / (sample_id + ".json")), new_files=0)
        _need(len(existing) < MAX_SAMPLES, "GROWTH_SAMPLE_COUNT_LIMIT")
        _need(runtime.preflight(expected_growth_bytes=SAMPLE_RESERVE,
                                destination=Path(runtime.profile["report_root"]))["allowed"],
              "GROWTH_SAMPLE_CAPACITY_BLOCKED")
        value = plan(runtime)
        _need(value["status"] == "COMPLETE", "GROWTH_INCOMPLETE_SCAN_NOT_RECORDED")
        # Keep only aggregate metadata; never persist original paths or payloads.
        payload = {key: deepcopy(value[key]) for key in (
            "schema_version", "status", "observed_at", "runtime_identity", "runtime_name",
            "snapshot_consistent", "deletion_authorized", "managed_logical_bytes", "allocated_bytes")}
        payload.update(sample_id=sample_id, observation_precedes_own_metadata_write=True,
                       root_logical_bytes={row["name"]: row["totals"]["unique_logical_bytes"]
                                           for row in value["inventory"]["roots"]},
                       authority="METADATA_OBSERVATION_NOT_SOURCE_OR_CLEANUP_AUTHORITY")
        envelope = _envelope(payload)
        _need(len(workspace._canonical(envelope)) <= MAX_SAMPLE_BYTES, "GROWTH_SAMPLE_SIZE_LIMIT")
        workspace._directory(directory, create=True)
        destination = directory / (sample_id + ".json")
        workspace._write(destination, envelope)
        _need(_payload(workspace._read(destination, limit=MAX_SAMPLE_BYTES), runtime) == payload,
              "GROWTH_SAMPLE_READBACK_FAILED")
        return dict(status="RECORDED", sample=payload, sample_path=str(destination), new_files=1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime")
    parser.add_argument("--registry-root", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    planning = sub.add_parser("plan")
    planning.add_argument("--growth-per-run-bytes", type=int)
    sampling = sub.add_parser("record")
    sampling.add_argument("--sample-id", required=True)
    sub.add_parser("history")
    args = parser.parse_args(argv)
    try:
        runtime = workspace.load_runtime(args.runtime, args.registry_root)
        _need(runtime is not None, "GROWTH_REGISTERED_RUNTIME_REQUIRED")
        value = (plan(runtime, growth_per_run_bytes=args.growth_per_run_bytes) if args.command == "plan"
                 else record(runtime, args.sample_id) if args.command == "record" else growth_history(runtime))
        print(json.dumps(value, ensure_ascii=False, allow_nan=False))
        return 0 if value.get("status") != "PARTIAL" else 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        reason = str(exc) if isinstance(exc, (workspace.WorkspaceError, capacity.CapacityError)) else "GROWTH_LOCAL_IO_OR_SCHEMA_ERROR"
        print(json.dumps(dict(status="BLOCKED", reason=reason), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
