"""Small, read-only daily dependency plans and immutable result snapshots.

Typical graph: market/day -> facts/day -> core/session -> result/view ->
outcomes/session. A checked archived reference can identify an existing result;
it cannot authorize a new calculation. This module never opens a business DB,
scans storage, authenticates RAW sources, invokes a worker, or grants authority.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.codex_workspace import _read, _write

VERSION = "CODEX_DAILY_PLAN_V1"
RESULT_VERSION = "CODEX_DAILY_RESULT_SNAPSHOT_V1"
MAX_CONTROL_BYTES = 1024 * 1024
MAX_NODES = 1024
SHA = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,159}")


class PlanError(ValueError):
    pass


def _need(condition, code):
    if not condition:
        raise PlanError(code)


def _canonical(value):
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise PlanError("INVALID_PLAN_JSON") from None
    _need(len(raw) <= MAX_CONTROL_BYTES, "PLAN_CONTROL_TOO_LARGE")
    return raw


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _name(value):
    _need(type(value) is str and NAME.fullmatch(value), "INVALID_NODE_ID")
    return value


def _sha(value):
    _need(type(value) is str and SHA.fullmatch(value), "INVALID_CONTENT_HASH")
    return value


def _clock(value):
    try:
        stamp = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise PlanError("INVALID_AWARE_CUTOFF") from None
    _need(stamp.tzinfo is not None and stamp.utcoffset() is not None,
          "INVALID_AWARE_CUTOFF")
    return stamp


def _context(value):
    _need(type(value) is dict, "REQUEST_OBJECT_REQUIRED")
    for key in ("business_date", "cutoff", "strategy_id", "strategy_version", "source_version"):
        _need(type(value.get(key)) is str and value[key], "REQUEST_IDENTITY_INCOMPLETE")
    try:
        _need(date.fromisoformat(value["business_date"]).isoformat() == value["business_date"],
              "INVALID_BUSINESS_DATE")
    except ValueError:
        raise PlanError("INVALID_BUSINESS_DATE") from None
    _clock(value["cutoff"])
    _need(type(value.get("config")) is dict, "REQUEST_CONFIG_REQUIRED")
    _canonical(value)
    return deepcopy(value)


def _artifact(value):
    _need(type(value) is dict, "RESULT_ARTIFACT_REQUIRED")
    _sha(value.get("sha256"))
    _need(type(value.get("bytes")) is int and value["bytes"] >= 0, "INVALID_ARTIFACT_BYTES")
    _need(type(value.get("path")) is str and bool(value["path"]), "ARTIFACT_PATH_REQUIRED")
    _canonical(value)
    return deepcopy(value)


def validate_result_manifest(value):
    """Validate small control bytes/identity only; artifact bytes need a verifier."""
    _need(type(value) is dict and value.get("schema_version") == RESULT_VERSION,
          "RESULT_SNAPSHOT_SCHEMA")
    _need(value.get("source_capability") == "NOT_GRANTED", "RESULT_NOT_SOURCE_AUTHORITY")
    _name(value.get("node_id"))
    identity = value.get("dependency_identity")
    _need(type(identity) is dict and identity.get("node", {}).get("id") == value["node_id"],
          "RESULT_DEPENDENCY_IDENTITY")
    _need(value.get("dependency_key") == _hash(identity), "RESULT_DEPENDENCY_HASH_MISMATCH")
    _need(value.get("status") in {"SUCCEEDED", "FAILED", "UNKNOWN"}, "RESULT_STATUS")
    _need(type(value.get("scope_id")) is str and value["scope_id"], "RESULT_SCOPE_REQUIRED")
    _clock(value.get("recorded_at"))
    if value["status"] == "SUCCEEDED":
        _artifact(value.get("artifact"))
    else:
        _need(value.get("artifact") is None, "FAILED_RESULT_CANNOT_PUBLISH_ARTIFACT")
    expected = _hash({key: item for key, item in value.items() if key != "manifest_hash"})
    _need(value.get("manifest_hash") == expected, "RESULT_MANIFEST_HASH_MISMATCH")
    return deepcopy(value)


def make_result_manifest(node_plan, *, status, scope_id, artifact=None, recorded_at=None, details=None):
    """Record the executor's outcome; this is not execution or source attestation."""
    _need(type(node_plan) is dict and node_plan.get("dependency_identity") is not None,
          "RESOLVED_NODE_IDENTITY_REQUIRED")
    value = {"schema_version": RESULT_VERSION, "node_id": node_plan["node_id"],
             "dependency_identity": deepcopy(node_plan["dependency_identity"]),
             "dependency_key": node_plan["dependency_key"], "status": status,
             "scope_id": scope_id, "recorded_at": recorded_at or datetime.now(timezone.utc).isoformat(),
             "artifact": deepcopy(artifact), "details": deepcopy(details or {}),
             "source_capability": "NOT_GRANTED"}
    value["manifest_hash"] = _hash(value)
    return validate_result_manifest(value)


def record_result_snapshot(destination, manifest):
    """Durably create one new small manifest. Never replace a prior result."""
    manifest = validate_result_manifest(manifest)
    _write(Path(destination), manifest)
    return {"path": str(Path(destination).absolute()), "manifest_hash": manifest["manifest_hash"],
            "bytes": len(_canonical(manifest)), "source_capability": "NOT_GRANTED"}


def load_result_snapshots(paths):
    _need(type(paths) in (list, tuple) and len(paths) <= MAX_NODES, "RESULT_PATH_LIMIT")
    return [validate_result_manifest(_read(Path(path))) for path in paths]


def _source(value, scope_id):
    _need(type(value) is dict, "SOURCE_OBJECT_REQUIRED")
    _name(value.get("id"))
    status = value.get("reference_status")
    _need(status in {"VERIFIED", "UNKNOWN", "FAILED"}, "SOURCE_REFERENCE_STATUS")
    identity = value.get("identity")
    if status == "VERIFIED":
        _need(type(identity) is dict, "SOURCE_IDENTITY_REQUIRED")
        _sha(identity.get("content_hash"))
        for key in ("origin_id", "source_version", "business_date"):
            _need(type(identity.get(key)) is str and identity[key], "SOURCE_IDENTITY_INCOMPLETE")
        try:
            date.fromisoformat(identity["business_date"])
        except ValueError:
            raise PlanError("INVALID_SOURCE_BUSINESS_DATE") from None
        pit = identity.get("pit")
        _need(type(pit) is dict and type(pit.get("coverage_type")) is str and pit["coverage_type"],
              "SOURCE_PIT_IDENTITY_REQUIRED")
        _clock(pit.get("actual_visible_at"))
        _sha(pit.get("receipt_hash"))
    authentication = value.get("authentication", "ARCHIVED_FACTS")
    _need(authentication in {"ARCHIVED_FACTS", "CURRENT_SCOPE_FULL", "NONE"}, "SOURCE_AUTHENTICATION_KIND")
    current = (status == "VERIFIED" and authentication == "CURRENT_SCOPE_FULL"
               and value.get("verified_scope_id") == scope_id)
    return {"status": status, "identity": deepcopy(identity), "current_authentication": current}


def _budget(nodes, policy, observation, cache_admission):
    """Consume one caller-owned metadata observation; never initiate a scan."""
    result = {"status": "UNKNOWN", "allowed": False, "metadata_scans_performed": 0,
              "cache_gate": "NOT_REQUIRED", "reason_codes": [], "reservation_created": False}
    if type(policy) is not dict or any(type(policy.get(key)) is not int or policy[key] < 0
                                     for key in ("max_managed_bytes", "min_free_bytes")):
        result["reason_codes"].append("EXPLICIT_RUNTIME_CAPACITY_POLICY_REQUIRED")
        return result
    result.update(max_managed_bytes=policy["max_managed_bytes"], min_free_bytes=policy["min_free_bytes"])
    report = observation.get("report", observation) if type(observation) is dict else {}
    used = report.get("totals", {}).get("unique_logical_bytes")
    if report.get("scan_complete") is not True or type(used) is not int or used < 0:
        result["reason_codes"].append("COMPLETE_CAPACITY_OBSERVATION_REQUIRED")
        return result
    builds = [node for node in nodes if node["status"] == "BUILD"]
    if any(type(node.get("estimate")) is not dict or any(
            type(node["estimate"].get(key)) is not int or node["estimate"][key] < 0
            for key in ("output_bytes", "temporary_peak_bytes", "cache_growth_bytes")) for node in builds):
        result["reason_codes"].append("BUILD_SIZE_ESTIMATE_REQUIRED")
        return result
    growth = sum(node["estimate"]["output_bytes"] + node["estimate"]["cache_growth_bytes"] for node in builds)
    temporary = max((node["estimate"]["temporary_peak_bytes"] for node in builds), default=0)
    result.update(current_unique_logical_bytes=used, output_and_cache_growth_bytes=growth,
                  temporary_peak_reserved_bytes=temporary, projected_peak_logical_bytes=used + growth + temporary,
                  schedule_assumption="SEQUENTIAL_EXECUTION_ONLY")
    reasons = result["reason_codes"]
    if used + growth + temporary > policy["max_managed_bytes"]:
        reasons.append("MANAGED_PEAK_BUDGET_EXCEEDED")
    roots = report.get("roots")
    if type(roots) is not list or not roots:
        reasons.append("TARGET_FREE_SPACE_UNKNOWN")
    else:
        targets = policy.get("target_roots")
        if targets is None and policy.get("target_root") is not None:
            targets = [policy["target_root"]]
        if targets is not None and (type(targets) is not list or not targets
                                    or any(type(name) is not str for name in targets)):
            result["reason_codes"].append("EXPLICIT_TARGET_ROOT_REQUIRED")
            result["status"] = "BLOCKED"
            return result
        selected = roots if targets is None else [row for row in roots if row.get("name") in targets]
        if targets is not None and ({row.get("name") for row in selected} != set(targets) or not selected):
            reasons.append("CAPACITY_TARGET_ROOT_UNKNOWN")
        for row in selected:
            if targets is not None and row.get("role") == "protected":
                reasons.append("TARGET_ROOT_PROTECTED")
            free = (row.get("filesystem") or {}).get("free_bytes")
            if type(free) is not int or free < 0:
                reasons.append("TARGET_FREE_SPACE_UNKNOWN")
            elif free - growth - temporary < policy["min_free_bytes"]:
                reasons.append("MINIMUM_FREE_SPACE_WOULD_BE_BREACHED")
    if any(node["estimate"]["cache_growth_bytes"] for node in builds):
        allowed = cache_admission.get("allowed") if type(cache_admission) is dict else None
        result["cache_gate"] = "PASSED" if allowed is True else "BLOCKED" if allowed is False else "UNKNOWN"
        if allowed is not True:
            reasons.append("ORIGINAL_CACHE_GATE_REQUIRED" if allowed is None else "ORIGINAL_CACHE_GATE_BLOCKED")
    result["reason_codes"] = sorted(set(reasons))
    result["allowed"] = not reasons
    result["status"] = "READY" if not reasons else "BLOCKED"
    return result


def build_plan(request, nodes, *, prior_results=(), source_observations=(), verify_result=None,
               scope_id, capacity_observation=None, policy=None, cache_admission=None):
    """Plan exact dependencies, with no execution or implicit retries.

    Nodes declare id/kind/version/config/parents and optional context overrides
    for business_date/cutoff. Historical daily facts can therefore retain their
    own context when a later session adds another branch. Parent order is not
    semantic, but every parent identity is. A BUILD parent has unknown output
    bytes: its child remains BUILD/ready=False until completion and replanning.
    """
    request = _context(request)
    _need(type(scope_id) is str and bool(scope_id), "CURRENT_SCOPE_REQUIRED")
    _need(type(nodes) in (list, tuple) and len(nodes) <= MAX_NODES, "NODE_LIMIT")
    _need(type(source_observations) in (list, tuple) and len(source_observations) <= MAX_NODES, "SOURCE_LIMIT")
    _need(type(prior_results) in (list, tuple) and len(prior_results) <= MAX_NODES, "RESULT_LIMIT")
    _canonical({"nodes": nodes, "sources": source_observations, "prior": prior_results})
    sources = {}
    for source in source_observations:
        parsed = _source(source, scope_id)
        _need(source["id"] not in sources, "DUPLICATE_SOURCE_ID")
        sources[source["id"]] = parsed
    definitions = {}
    for value in nodes:
        _need(type(value) is dict, "NODE_OBJECT_REQUIRED")
        node_id = _name(value.get("id"))
        _need(node_id not in definitions and node_id not in sources, "DUPLICATE_NODE_ID")
        for key in ("kind", "version"):
            _need(type(value.get(key)) is str and value[key], "NODE_VERSION_REQUIRED")
        _need(type(value.get("config")) is dict, "NODE_CONFIG_REQUIRED")
        parents = value.get("parents")
        _need(type(parents) is list and parents and all(type(item) is str for item in parents), "EXPLICIT_PARENTS_REQUIRED")
        _need(len(parents) == len(set(parents)), "DUPLICATE_PARENT_ID")
        for parent in parents:
            _name(parent)
        context = value.get("context", {})
        _need(type(context) is dict and set(context) <= {"business_date", "cutoff"}, "NODE_CONTEXT_FIELDS")
        definitions[node_id] = deepcopy(value)
    previous = {}
    for value in prior_results:
        value = validate_result_manifest(value)
        previous.setdefault(value["dependency_key"], []).append(value)
    planned = {}
    remaining = set(definitions)
    while remaining:
        available = sorted(node_id for node_id in remaining if not
                           (set(definitions[node_id]["parents"]) & remaining))
        _need(available, "DEPENDENCY_CYCLE")
        for node_id in available:
            definition = definitions[node_id]
            context = _context({**request, **definition.get("context", {})})
            parent_values, reasons = [], []
            unknown = blocked = awaiting_build = False
            authenticated = True
            for parent_id in sorted(definition["parents"]):
                if parent_id in sources:
                    parent = sources[parent_id]
                    if parent["status"] != "VERIFIED":
                        blocked |= parent["status"] == "FAILED"
                        unknown |= parent["status"] == "UNKNOWN"
                        reasons.append("SOURCE_" + parent["status"] + ":" + parent_id)
                    elif (parent["identity"].get("reference_kind") != "ARCHIVED_RESULT_REFERENCE" and
                          _clock(parent["identity"]["pit"]["actual_visible_at"]) > _clock(context["cutoff"])):
                        unknown = True
                        reasons.append("SOURCE_VISIBLE_AFTER_CUTOFF:" + parent_id)
                    else:
                        parent_values.append({"id": parent_id, "type": "SOURCE", "identity": parent["identity"]})
                    authenticated &= parent["current_authentication"]
                elif parent_id in planned:
                    parent = planned[parent_id]
                    authenticated &= not parent["needs_authentication"]
                    if parent["status"] == "REUSE":
                        parent_values.append({"id": parent_id, "type": "RESULT",
                                              "dependency_key": parent["dependency_key"],
                                              "artifact": parent["artifact"]})
                    else:
                        blocked |= parent["status"] == "BLOCKED"
                        unknown |= parent["status"] == "UNKNOWN"
                        awaiting_build |= parent["status"] == "BUILD"
                        reasons.append("PARENT_" + parent["status"] + ":" + parent_id)
                else:
                    unknown = True
                    authenticated = False
                    reasons.append("MISSING_PARENT:" + parent_id)
            status = "BLOCKED" if blocked else "UNKNOWN" if unknown else "BUILD"
            item = {"node_id": node_id, "status": status, "ready": False,
                    "dependency_identity": None, "dependency_key": None,
                    "needs_authentication": not authenticated, "reason_codes": reasons,
                    "estimate": deepcopy(definition.get("estimate")), "source_capability": "NOT_GRANTED"}
            if not blocked and not unknown and not awaiting_build:
                identity = {"schema_version": VERSION, "request": context,
                            "node": {key: definition[key] for key in ("id", "kind", "version", "config")},
                            "parents": parent_values}
                key = _hash(identity)
                item.update(dependency_identity=identity, dependency_key=key, ready=True)
                matches = previous.get(key, [])
                if any(value["status"] != "SUCCEEDED" for value in matches):
                    item.update(status="BLOCKED", ready=False, reason_codes=["PRIOR_FAILED_OR_UNKNOWN_RECONCILE_NO_RETRY"])
                elif matches:
                    artifacts = {_hash(value["artifact"]) for value in matches}
                    _need(len(artifacts) == 1, "CONFLICTING_RESULTS_FOR_EXACT_DEPENDENCIES")
                    saved = sorted(matches, key=lambda value: (value["recorded_at"], value["manifest_hash"]))[-1]
                    checked = verify_result(deepcopy(saved)) if callable(verify_result) else None
                    valid = checked is True or (type(checked) is dict and checked.get("verified") is True)
                    if valid:
                        item.update(status="REUSE", artifact=deepcopy(saved["artifact"]),
                                    result_manifest_hash=saved["manifest_hash"], result_scope_id=saved["scope_id"],
                                    reason_codes=["EXACT_DEPENDENCIES_AND_ARTIFACT_VERIFIED"])
                    else:
                        item.update(status="UNKNOWN", ready=False, reason_codes=["RESULT_ARTIFACT_VERIFICATION_REQUIRED"])
                else:
                    item["reason_codes"] = ["NO_EXACT_RESULT", "NEW_EXECUTION_REQUIRES_AUTHENTICATED_SCOPE"]
            planned[node_id] = item
            remaining.remove(node_id)
    values = list(planned.values())
    plan = {"schema_version": VERSION, "scope_id": scope_id, "request": request,
            "nodes": values, "budget": _budget(values, policy, capacity_observation, cache_admission),
            "source_authentication": "NOT_PERFORMED_BY_PLANNER", "execution_authorized": False,
            "new_authentication_scope_count": 0, "source_capability": "NOT_GRANTED"}
    plan["plan_id"] = _hash(plan)
    return plan


def saved_view_plan_request(view, target_session=None):
    """Build the useful default graph from the result-view reader's checked output.

    facts/core are *references inside the saved result*, not claims that their
    underlying input/RAW files were reopened. Their artifact carrier is the
    verified observation export. An unobserved target never inherits readiness.
    The caller must still supply its current scope and artifact verifier.
    """
    _need(type(view) is dict and view.get("status") == "VERIFIED_SAVED_RESULT_VIEW"
          and view.get("local_seal_authenticated_now") is True
          and view.get("all_five_exports_verified_now") is True,
          "VERIFIED_SAVED_VIEW_REQUIRED")
    view_id, archive_id = _sha(view.get("view_id")), _sha(view.get("archive_id"))
    summary = view.get("summary")
    _need(type(summary) is dict, "SAVED_VIEW_SUMMARY_REQUIRED")
    for key in ("run_id", "input_hash", "canonical_content_hash", "observation_date",
                "target_session", "decision_cutoff", "strategy_id", "coverage_type"):
        _need(key in summary, "SAVED_VIEW_IDENTITY_INCOMPLETE")
    _sha(summary["input_hash"])
    _sha(summary["canonical_content_hash"])
    export_roles = {"observation.json", "observation.csv", "all.csv", "focus.csv", "unselected.csv"}
    _need(type(view.get("artifacts")) is dict and set(view["artifacts"]) == export_roles,
          "EXACT_FIVE_SAVED_EXPORTS_REQUIRED")
    for reference in view["artifacts"].values():
        _artifact(reference)
    observed, original_target = summary["observation_date"], summary["target_session"]
    target = target_session or original_target
    try:
        _need(date.fromisoformat(target).isoformat() == target, "INVALID_TARGET_SESSION")
    except (TypeError, ValueError):
        raise PlanError("INVALID_TARGET_SESSION") from None
    artifact = _artifact(view.get("artifacts", {}).get("observation.json"))
    authenticated_at = view.get("last_full_authenticated_at")
    _clock(authenticated_at)
    request = {"business_date": observed, "cutoff": summary["decision_cutoff"],
               "strategy_id": summary["strategy_id"],
               "strategy_version": summary["canonical_content_hash"],
               "source_version": str(view.get("archive_profile")) + ":" + archive_id,
               "config": {"saved_target_session": original_target, "saved_run_id": summary["run_id"]}}
    source_id = "market/" + observed
    source = {"id": source_id, "reference_status": "VERIFIED", "authentication": "ARCHIVED_FACTS",
              "identity": {"content_hash": summary["input_hash"], "origin_id": archive_id,
                           "source_version": request["source_version"], "business_date": observed,
                           "reference_kind": "ARCHIVED_RESULT_REFERENCE",
                           "pit": {"coverage_type": summary["coverage_type"],
                                   "actual_visible_at": authenticated_at, "receipt_hash": view_id,
                                   "original_decision_cutoff": summary["decision_cutoff"]},
                           "signed_view_id": view_id, "saved_summary": deepcopy(summary),
                           "verified_export_set_hash": _hash(view["artifacts"]),
                           "authority": "ARCHIVED_RESULT_REFERENCE_NOT_RAW_CAPABILITY"}}
    zero = {"output_bytes": 0, "temporary_peak_bytes": 0, "cache_growth_bytes": 0}
    facts_id, core_id, result_id = "facts/" + observed, "core/" + original_target, "result/" + view_id
    nodes = [
        {"id": facts_id, "kind": "SAVED_INPUT_REFERENCE", "version": VERSION,
         "config": {"input_hash": summary["input_hash"]}, "parents": [source_id], "estimate": zero},
        {"id": core_id, "kind": "SAVED_CORE_REFERENCE", "version": VERSION,
         "config": {"run_id": summary["run_id"], "canonical_content_hash": summary["canonical_content_hash"]},
         "parents": [facts_id], "estimate": zero},
        {"id": result_id, "kind": "SAVED_RESULT_VIEW", "version": VERSION,
         "config": {"view_id": view_id, "target_session": original_target}, "parents": [core_id], "estimate": zero},
    ]
    scope = "saved-view-plan:" + view_id
    snapshots = []
    for node in nodes:
        plan = build_plan(request, nodes[:len(snapshots) + 1], source_observations=[source],
                          prior_results=snapshots, verify_result=lambda _: True, scope_id=scope)
        item = next(item for item in plan["nodes"] if item["node_id"] == node["id"])
        snapshots.append(make_result_manifest(item, status="SUCCEEDED", scope_id="archive:" + archive_id,
                         artifact=artifact, recorded_at=authenticated_at,
                         details={"view_id": view_id, "representation": "SAVED_REFERENCE_NOT_RECOMPUTED_PRODUCT",
                                  "carrier_role": "observation.json", "input_bytes_reauthenticated_now": False}))
    if target != original_target:
        new_facts, new_core = "facts/target-" + target, "core/" + target
        nodes.extend([
            {"id": new_facts, "kind": "NEW_SESSION_FACTS", "version": VERSION,
             "config": {"target_session": target}, "parents": ["market/target-" + target]},
            {"id": new_core, "kind": "NEW_SESSION_CORE", "version": VERSION,
             "config": {"target_session": target}, "parents": [new_facts]},
            {"id": "result/target-" + target, "kind": "NEW_SESSION_RESULT", "version": VERSION,
             "config": {"target_session": target}, "parents": [new_core]},
        ])
    nodes.append({"id": "outcomes/" + target, "kind": "SESSION_OUTCOMES", "version": VERSION,
                  "config": {"target_session": target}, "parents": [result_id, "outcome-market/" + target]})
    return {"request": request, "nodes": nodes, "source_observations": [source],
            "prior_results": snapshots, "scope_id": scope}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("plan")
    command.add_argument("--input", type=Path, required=True, help="Small JSON object matching build_plan parameters")
    command = commands.add_parser("record")
    command.add_argument("--manifest", type=Path, required=True)
    command.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            value = _read(args.input)
            _need(type(value) is dict and "verify_result" not in value, "PLANNER_INPUT_FIELDS")
            # CLI cannot import an arbitrary verifier; existing artifacts remain UNKNOWN.
            result = build_plan(**value)
        else:
            result = record_result_snapshot(args.destination, _read(args.manifest))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, OSError, TypeError):
        print(json.dumps({"schema_version": VERSION, "status": "FAILED", "reason": "PLAN_OR_MANIFEST_INVALID"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
