"""One explicitly scoped continuation of a retained failed daily operation.

The public daily entry supplies authorization. This module never calls a
provider, changes an old record, chooses another request key, or retries a
claim. Its small journal lives under the original operation in the same scope.
After sealed success, a separate explicit metadata append can carry the exact
reviewed factor intersection into later legal sessions, without new retry or
collection budget authority.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import math
from pathlib import Path
import re
import time

from scripts import codex_workspace as workspace
from stockfish_core.file_integrity import content_equal, content_reference_equal, _content
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, DEFAULT_LIMITS, strict_json
from stockfish_core.yaogu.premarket_capture_cache_v1 import CaptureCache, RequestSpec
from stockfish_core.yaogu.collection_inherited_scope import inherited_attempt_scope
from stockfish_core.yaogu.provider_requests import ENDPOINTS

VERSION = "PREMARKET_CONTINUATION_PROPOSAL_V1"
CLAIM_VERSION = "PREMARKET_CONTINUATION_CLAIM_V1"
TERMINAL_VERSION = "PREMARKET_CONTINUATION_TERMINAL_V1"
AUTHORIZATION_VERSION = "PREMARKET_CONTINUATION_AUTHORIZATION_V1"
RECONCILIATION_VERSION = "PREMARKET_FACTOR_RECONCILIATION_RECORD_V1"
PROOF_VERSION = "ENGINEERING_ORIGINAL_SNAPSHOT_PROOF_V1"
ADMITTED = "ADMITTED_ONE_EXACT_SOURCE_RECOVERY_AND_CONTINUATION"
SH = timezone(timedelta(hours=8))
MIB = 1024**2
MAX_ITEMS = 8192
# Audited legacy accounting branch, not a permit for arbitrary old programs.
# Unknown versions require another reviewed accounting implementation.
LEGACY_PARENT_HASHES = {
    "scripts/codex_daily_v2.py": "4e248200a765d106c715942d7104f3ecefbc1f3e41bafe01bfc3c9318ef768e4",
    "scripts/codex_premarket.py": "6949e364ddc16e73887f8678ceb3f04ca80fe973bf84b7cf8a61b4014e5e4010",
    "scripts/codex_premarket_shared_v1.py": "8f1bc1f45c8cc0319ccba749b6bbac298edce0dbc95b977e4c97870906f69c40",
    "scripts/codex_workspace.py": "cef793f1edbadbad65ed1c60079b755bb4db029b489f692de5a70f325dfdd129",
    "stockfish_core/yaogu/premarket_runtime_v1.py": "bce430eb83f8135965fbac80e0745d7da19f80a236e6cbe4a1ee9277b6b517ac",
    "stockfish_core/yaogu/premarket_workflow_v1.py": "707a922d6654a3b7a0042009586610e374b9a5e4f4a2ed57908db232ba881a64",
}
# Explicitly reserved from the old remainder before writing a claim. The first
# readonly prepare, execute's locked prepare, and final receipt validation each
# have their own finite envelope. None can donate unused room to the worker.
PREPARE_READ_BYTES = 1024 * MIB
PREPARE_SECONDS = 300
CONTROL_READ_BYTES = 256 * MIB
CONTROL_SECONDS = 120
WRAPPER_PREPARE_READ_BYTES = 64 * MIB
WRAPPER_PREPARE_SECONDS = 60
JOURNAL_OUTPUT_BYTES = MIB
OLD_OUTER_RESERVE_SECONDS = 60
FUTURE_PROPOSAL_VERSION = "FUTURE_FACTOR_AUTHORIZATION_PROPOSAL_V1"
FUTURE_READ_BYTES = 128 * MIB
FUTURE_SECONDS = 120
FUTURE_RECORD_BYTES = 64 * 1024


def _need(condition, reason):
    if not condition:
        raise ValueError("CONTINUATION_" + reason)


def _clock(value):
    value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    _need(value.utcoffset() is not None, "AWARE_CLOCK_REQUIRED")
    return value


def _now(value, fixture):
    _need(value is None or fixture, "REAL_CLOCK_OVERRIDE_FORBIDDEN")
    return _clock(value) if value is not None else datetime.now(timezone.utc)


def _path(value, *, directory=False):
    path = Path(value).expanduser().absolute()
    _need(".." not in path.parts and path.resolve(strict=True) == path,
          "CANONICAL_EXISTING_PATH_REQUIRED")
    _need(path.is_dir() if directory else path.is_file(), "PATH_TYPE_REQUIRED")
    # Reject aliases in ancestors as well as the final component.
    with workspace._directory_fd(path if directory else path.parent):
        pass
    _need(not path.is_symlink(), "PATH_ALIAS_FORBIDDEN")
    return path


def _reader_limits(seconds=PREPARE_SECONDS, byte_limit=PREPARE_READ_BYTES):
    return {**DEFAULT_LIMITS, "max_seconds": seconds, "capture_read_bytes": byte_limit}


class _Reader:
    def __init__(self, budget):
        self.budget = budget
        self.refs = {}

    def read(self, source, *, limit=16 * MIB):
        expected = source if isinstance(source, dict) else None
        path = _path(expected["path"] if expected else source)
        raw, reference = self.budget.read(path, expected=expected, limit=limit)
        prior = self.refs.setdefault(str(path), reference)
        _need(content_reference_equal(prior, reference), "REFERENCE_CHANGED_DURING_READ")
        return strict_json(raw), reference

    def reference(self, path, *, limit=16 * MIB):
        path = _path(path)
        _, reference = self.budget.read(path, limit=limit)
        prior = self.refs.setdefault(str(path), reference)
        _need(content_reference_equal(prior, reference), "REFERENCE_CHANGED_DURING_READ")
        return reference

    def verify_all(self):
        for reference in list(self.refs.values()):
            self.budget.read(reference["path"], expected=reference, limit=max(reference["bytes"], 1))


def _slot(runtime, operation_id):
    _need(isinstance(operation_id, str) and re.fullmatch("[0-9a-f]{64}", operation_id),
          "ORIGINAL_OPERATION_ID_REQUIRED")
    return Path(runtime.directory) / "operations" / operation_id / "continuation"


def _identity(runtime, operation_path, reader):
    runtime.check_binding()
    _need(runtime.profile.get("backend") == "premarket", "ORIGINAL_RUNTIME_REQUIRED")
    path = _path(operation_path)
    _need(path.parent == Path(runtime.directory) / "operations", "ORIGINAL_SCOPE_REQUIRED")
    operation, reference = reader.read(path)
    identity = operation.get("identity", {})
    _need(operation.get("schema_version") == workspace.VERSION
          and operation.get("phase") == "FAILED_OR_UNKNOWN"
          and operation.get("scope") == runtime.scope
          and operation.get("operation_id") == path.stem
          and path.stem == workspace._digest({"scope": runtime.scope, "identity": identity}),
          "ORIGINAL_FAILED_OPERATION_REQUIRED")
    _need(identity.get("schema_version") == "CODEX_DAILY_EXECUTION_PIPELINE_V2"
          and identity.get("backend") == "premarket"
          and identity.get("execution_engine") == "original"
          and identity.get("explicit_missing_collection") is True
          and identity.get("state_root") == runtime.profile["state_root"]
          and identity.get("source_root") == runtime.profile["source_root"]
          and identity.get("python_path") == runtime.profile["python"]
          and isinstance(identity.get("request_key"), str), "ORIGINAL_IDENTITY_REQUIRED")
    _need(_clock(operation["started_at"]) < _clock(operation["finished_at"]), "OPERATION_CLOCK_ORDER")
    return operation, reference


def _parent(runtime, operation, source_job, proof, reader):
    identity = operation["identity"]
    job = _path(source_job, directory=True)
    _need(job.parent == Path(identity["state_root"]) / "jobs", "ORIGINAL_JOB_LOCATION")
    final, final_ref = reader.read(job / "final-control.json")
    _need(final.get("schema_version") == "CODEX_PREMARKET_SHARED_WORKFLOW_V1"
          and final.get("status") == "FAILED" and final.get("job") == str(job)
          and final.get("external_worker_reaped") is True
          and final.get("external_worker_exit_code") == 3
          and final.get("core_verified") is False
          and final.get("source_terminal_binding_verified") is False,
          "SEALED_FAILED_PARENT_REQUIRED")
    completion, completion_ref = reader.read(final["completion"])
    configuration, configuration_ref = reader.read(final["configuration"])
    registry, registry_ref = reader.read(final["code_registry"])
    for name, reference in (("completion", completion_ref), ("configuration", configuration_ref),
                            ("code_registry", registry_ref)):
        _need(Path(reference["path"]) == job / ("worker-completion.json" if name == "completion"
              else name.replace("_", "-") + ".json"), "PARENT_CONTROL_LOCATION")
        _need(content_reference_equal(proof[name], reference), "SNAPSHOT_PARENT_BINDING")
    _need(content_reference_equal(proof["final"], final_ref)
          and proof.get("original_job") == str(job), "SNAPSHOT_PARENT_BINDING")
    reason = (final.get("error") or {}).get("reason", "")
    _need(re.fullmatch("FACTOR_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY:[0-9a-f]{64}", reason)
          and final.get("error") == completion.get("error")
          and completion.get("status") == "FAILED"
          and completion.get("schema_version") == final["schema_version"]
          and completion.get("business_database_written") is False
          and completion.get("core_verified") is False
          and not completion.get("core_artifacts") and not completion.get("display")
          and completion.get("phases") == dict(capture="FAILED", core="NOT_RUN", news="NOT_RUN",
              theme_sources="NOT_RUN", theme="NOT_RUN", presentation="NOT_RUN"),
          "AUDITED_PRECORE_FAILURE_BRANCH_REQUIRED")
    expected = completion.get("expected_sessions")
    _need(isinstance(expected, list) and len(expected) == 3
          and expected[:2] == [identity["observation_date"], identity["target_session"]],
          "PARENT_BUSINESS_SESSIONS")
    started, finished = _clock(operation["started_at"]), _clock(operation["finished_at"])
    native = final.get("native_inner_supervision") or {}
    _need(native.get("root_reaped") is True and native.get("failure") is None
          and native.get("cleanup") is None
          and (native.get("root_reap_evidence") or {}).get("exit_code_proven") is True,
          "PARENT_PROCESS_TERMINATION_UNPROVEN")
    created = datetime.fromtimestamp(native["root_identity"]["create_time"], timezone.utc)
    _need(started <= created <= _clock(completion["completed_at"])
          <= _clock(final["final_sealed_at"]) <= finished,
          "PARENT_PROCESS_CLOCK_ORDER")
    # The old failure record lacks a job pointer. The audited code creates one
    # supervisor job per operation; require the only job in its closed interval.
    candidates = []
    jobs = sorted(job.parent.iterdir())
    _need(len(jobs) <= MAX_ITEMS, "JOB_SCAN_BOUND")
    for other in jobs:
        _path(other, directory=True)
        control = other / "final-control.json"
        if not control.exists():
            # A date-named unfinished job in this interval cannot disappear.
            match = re.match(r"(\d{8}T\d{6}Z)-", other.name)
            if match:
                stamp = datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                _need(not started.replace(microsecond=0) <= stamp <= finished, "UNKNOWN_JOB_IN_PARENT_INTERVAL")
            continue
        value, _ = reader.read(control)
        clock = ((value.get("native_inner_supervision") or {}).get("root_identity") or {}).get("create_time")
        if isinstance(clock, (int, float)) and not isinstance(clock, bool):
            if started <= datetime.fromtimestamp(clock, timezone.utc) <= finished:
                candidates.append(str(other))
    _need(candidates == [str(job)], "PARENT_JOB_NOT_UNIQUE")
    metrics = completion.get("metrics") or {}
    _need(metrics.get("pending_request_results") == 0 and metrics.get("failures") == []
          and (metrics.get("calls") or {}).get("actual_unknown") == 0,
          "PARENT_PENDING_OR_UNKNOWN_ACCOUNTING")
    registered, _ = reader.read(identity["configuration"])
    from stockfish_core.yaogu.premarket_workflow_v1 import load_config
    normalized = load_config(overrides=registered)
    _need(content_equal(configuration, normalized) and metrics.get("limits") == normalized["limits"],
          "ORIGINAL_REGISTERED_LIMITS_CHANGED")
    return job, final, completion, registry, normalized


def _snapshot(runtime, operation, proof, registry, reader):
    _need(proof.get("schema_version") == PROOF_VERSION, "SNAPSHOT_PROOF_SCHEMA")
    snapshot = proof.get("snapshot")
    _need(isinstance(snapshot, dict)
          and workspace._digest(snapshot) == operation["identity"]["execution_sources_sha256"]
          and proof.get("snapshot_sha256") == workspace._digest(snapshot), "ORIGINAL_SNAPSHOT_HASH")
    _need(content_reference_equal(snapshot["configuration"], operation["identity"]["configuration"])
          and content_reference_equal(snapshot["python"], operation["identity"]["python"])
          and content_equal(snapshot["shared_execution_sources"], registry), "ORIGINAL_SNAPSHOT_BINDING")
    root = Path(operation["identity"]["source_root"])
    by_name = {str(Path(reference["path"]).relative_to(root)): reference for reference in registry}
    for name, digest in LEGACY_PARENT_HASHES.items():
        _need(by_name.get(name, {}).get("sha256") == digest, "UNKNOWN_PARENT_ACCOUNTING_IMPLEMENTATION")
    expected_sources = [reference for reference in registry if
        Path(reference["path"]).is_relative_to(root / "stockfish_core")
        or Path(reference["path"]) == root / "scripts/codex_daily_v2.py"]
    _need(content_equal(snapshot["source_references"], expected_sources), "SNAPSHOT_PACKAGE_CLOSURE_CHANGED")
    for group in (snapshot["source_references"], snapshot["strategy_support"], registry):
        _need(isinstance(group, list) and len(group) <= 4096
              and len({r["path"] for r in group}) == len(group)
              and all(type(r["bytes"]) is int and r["bytes"] >= 0 for r in group),
              "SNAPSHOT_REFERENCE_SCHEMA")
    # The bound is recomputed; proof.accounting is descriptive, never trusted.
    source_bytes = sum(r["bytes"] for r in snapshot["source_references"])
    support_bytes = sum(r["bytes"] for r in snapshot["strategy_support"])
    script_bytes = sum(r["bytes"] for r in registry if Path(r["path"]).parent == root / "scripts")
    per_snapshot = (2 * source_bytes + support_bytes + snapshot["configuration"]["bytes"]
                    + snapshot["python"]["bytes"] + 2 * script_bytes)
    ledger_refs = proof.get("operation_ledger_references")
    _need(isinstance(ledger_refs, list) and len(ledger_refs) <= MAX_ITEMS, "PARENT_LEDGER_PROOF_REQUIRED")
    dirs = {Path(runtime.directory) / "operations"}
    dirs.update(Path(path) for path, _ in getattr(runtime, "ancestor_ledgers", ()))
    observed = {str(path) for directory in dirs for path in directory.glob("*.json")}
    _need(observed == {r["path"] for r in ledger_refs} and len(observed) == len(ledger_refs),
          "PARENT_LEDGER_SET_CHANGED")
    for reference in ledger_refs:
        value, _ = reader.read(reference)
        other = value.get("identity") or {}
        if value.get("operation_id") != operation["operation_id"]:
            _need(other.get("request_key") != operation["identity"]["request_key"]
                  and not (other.get("backend") == "premarket"
                    and all(other.get(k) == operation["identity"][k]
                            for k in ("observation_date", "target_session"))),
                  "ANOTHER_OPERATION_FOR_SAME_BUSINESS")
    return per_snapshot, sum(r["bytes"] for r in ledger_refs)


def _factor_review(selection, operation, cache_root, reader):
    """Bind the one reviewed case, without treating the review as authority."""
    if selection is None:
        return None
    _need(isinstance(selection, dict) and set(selection) == {"path", "sha256"}
          and isinstance(selection["sha256"], str)
          and re.fullmatch("[0-9a-f]{64}", selection["sha256"]), "EXACT_RECONCILIATION_REVIEW_REQUIRED")
    review, reference = reader.read(selection["path"])
    _need(reference["sha256"] == selection["sha256"], "RECONCILIATION_REVIEW_SHA_CHANGED")
    _need(review.get("schema_version") == "PRIVATE_INHERITED_UNKNOWN_INVENTORY_REVIEW_V1"
          and review.get("original_operation_id") == operation["operation_id"]
          and review.get("current_cache") == str(cache_root)
          and review.get("other_inventory_anomalies") == []
          and review.get("provider_requests") == 0
          and review.get("source_request_execution") is False
          and review.get("continuation_claim_created") is False
          and review.get("original_failed_job_retained") is True,
          "RECONCILIATION_REVIEW_SCOPE_CHANGED")
    unknown = review.get("unknown_attempts")
    _need(isinstance(unknown, list) and len(unknown) == 1, "ONE_REVIEWED_UNKNOWN_ATTEMPT_REQUIRED")
    old = unknown[0]
    _need(old.get("current_observation_only") is True
          and old.get("predating_parent_content_proof_found") is False
          and old.get("retry_authorized") is False and old.get("source_capability_issued") is False,
          "CURRENT_RECONCILIATION_NOT_HISTORICAL_PROOF")
    manifest = old.get("directory_manifest")
    _need(isinstance(manifest, list) and len(manifest) <= 64
          and all(isinstance(row, dict) and row.get("type") in ("FILE", "DIRECTORY") for row in manifest),
          "RECONCILIATION_EXACT_FILE_INVENTORY_REQUIRED")
    refs = [{key: row[key] for key in ("path", "sha256", "bytes")}
            for row in manifest if row["type"] == "FILE"]
    _need(len(refs) == 6 and len({ref["path"] for ref in refs}) == 6,
          "RECONCILIATION_EXACT_SIX_FILES_REQUIRED")
    complete = old.get("existing_complete_counterparts_verified_now")
    _need(isinstance(complete, list) and len(complete) == 1, "ONE_EXISTING_COMPLETE_COUNTERPART_REQUIRED")
    spec = RequestSpec(**old["request_spec"])
    _need(spec.key == old.get("source_request_key") and spec.endpoint == "adjustment_factors"
          and complete[0].get("request_key") == spec.key
          and complete[0].get("request_spec") == spec.to_dict()
          and complete[0].get("status") == "COMPLETE" and complete[0].get("pagination_complete") is True,
          "RECONCILIATION_REVIEW_REQUEST_CHANGED")
    return dict(request_key=spec.key, unknown_attempt_dir=old["attempt"], unknown_attempt_refs=refs,
        complete_receipt_id=complete[0]["receipt_id"], codes=deepcopy(spec.parameters["codes"]),
        review_ref=_content(reference))


def _attempts(cache_root, operation, completion, reader, *, allowed_key, allowed_receipt, fixture, now,
              reconciliation_case=None, operation_ref=None, runtime=None, source_job=None):
    for name in ("index", "raw", "raw-index", "locators"):
        _path(cache_root / name, directory=True)
    cache = CaptureCache(cache_root, reader.budget,
        config=dict(offline=True, retry_failed=False, allow_test_fixture=fixture),
        executor=lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("CONTINUATION_NETWORK_FORBIDDEN")))
    reconciliation = None
    if reconciliation_case is not None:
        from stockfish_core.yaogu.collection_factor_reconciliation import prepare_factor_reconciliation
        reconciliation = prepare_factor_reconciliation(cache, operation_ref=operation_ref, operation=operation,
            scope=runtime.scope, scope_directory=str(runtime.directory), source_job=str(source_job),
            expected_case=reconciliation_case)
    indices, inventory, index_values = {}, [], []
    paths = sorted((cache_root / "index").glob("*/*.json"))
    _need(len(paths) <= MAX_ITEMS, "INDEX_SCAN_BOUND")
    for path in paths:
        value, reference = reader.read(path, limit=2 * MIB)
        spec = RequestSpec(**value["request_spec"])
        _need(path.stem == reference["sha256"] and path.parent.name == spec.key == value["request_key"],
              "INDEX_CONTENT_ADDRESS_OR_KEY_CHANGED")
        indices.setdefault(value.get("capture_attempt_dir"), []).append((value, reference))
        index_values.append(value)
        inventory.append(reference)
    start, finish = _clock(operation["started_at"]), _clock(operation["finished_at"])
    parent, source_retry, historical_inventory = [], None, []
    counts = dict(requests=0, sdk_reserved=0, http_reserved=0, sdk_actual=0, http_actual=0)
    folders = sorted((cache_root / "raw").iterdir())
    _need(len(folders) <= MAX_ITEMS, "ATTEMPT_SCAN_BOUND")
    total = 0
    for folder in folders:
        _path(folder, directory=True)
        raw = folder / "raw"
        if not raw.is_dir():
            # Only the existing fully authenticated inherited marker grammar
            # is understood. Merely finding an index never excuses unknown
            # files, an uncommitted attempt, or an invented seed directory.
            if reconciliation is not None and folder.name == reconciliation_case["request_key"]:
                from stockfish_core.yaogu.collection_factor_reconciliation import reconciled_inherited_scope
                inherited = reconciled_inherited_scope(cache, folder, reconciliation)
            else:
                inherited = inherited_attempt_scope(cache, folder, entries=index_values,
                                                    retain_known_terminal_failures=True)
            _need(inherited is not None and inherited.get("status") in
                  ("VERIFIED_COMPLETE_ORIGINAL", "VERIFIED_RETAINED_ORIGINAL", "INVENTORY_RECONCILED_UNKNOWN_RETAINED"),
                  "UNKNOWN_RAW_FOLDER")
            related = [value for value in index_values if value["receipt_id"] in inherited["receipt_ids"]]
            _need({v["receipt_id"] for v in related} == set(inherited["receipt_ids"])
                  and all(max(_clock(v[field]) for field in ("first_seen_at", "ingested_at", "completed_at")) < start
                          for v in related), "INHERITED_REQUEST_NOT_BEFORE_PARENT")
            historical_inventory.append(dict(request_key=folder.name, status=inherited["status"],
                receipt_ids=deepcopy(inherited["receipt_ids"]), statuses=deepcopy(inherited.get("statuses", [])),
                actual_counts_status=inherited.get("actual_counts_status", "UNKNOWN_RETAINED"),
                actual_counts_affect_parent_budget=False, source_capability_issued=False, retry_authorized=False))
            continue
        _path(raw, directory=True)
        for attempt in sorted(raw.iterdir()):
            _path(attempt, directory=True)
            total += 1
            _need(total <= MAX_ITEMS and (attempt / "request-claim.json").is_file(),
                  "UNCLAIMED_OR_EXCESS_ATTEMPT")
            claim, claim_ref = reader.read(attempt / "request-claim.json")
            spec = RequestSpec(**claim["request_spec"])
            _need(spec.key == folder.name and claim.get("schema_version") == "PREMARKET_CAPTURE_CACHE_V1"
                  and claim.get("retries") == 0, "ATTEMPT_CLAIM_IDENTITY")
            clock = _clock(claim["actual_started_at"])
            inventory.append(claim_ref)
            _need(clock <= finish, "REQUEST_AFTER_PARENT_NO_NEW_CLAIM")
            if clock < start:
                continue
            records = indices.get(str(attempt), ())
            _need(len(records) == 1 and (attempt / "committed.json").is_file()
                  and not (attempt / "attempt-failed.json").exists(), "PARENT_ATTEMPT_NOT_COMMITTED")
            value, index_ref = records[0]
            commit, commit_ref = reader.read(attempt / "committed.json")
            _need(commit == dict(request_key=spec.key, receipt_id=value["receipt_id"])
                  and spec.to_dict() == value["request_spec"], "PARENT_ATTEMPT_COMMIT_BINDING")
            result = cache.verify(value)
            _need(clock <= _clock(value["started_at"]) <= _clock(value["completed_at"]) <= finish,
                  "PARENT_PROVIDER_CLOCK_OUTSIDE_OPERATION")
            refs = [reader.reference(path) for path in sorted(attempt.rglob("*")) if path.is_file()]
            _need(all(not path.is_symlink() for path in attempt.rglob("*")), "ATTEMPT_ALIAS")
            for reference in (index_ref, claim_ref, commit_ref, *refs):
                reader.refs.setdefault(reference["path"], reference)
            if spec.key == allowed_key:
                _need(source_retry is None and value["receipt_id"] == allowed_receipt
                      and spec.endpoint == "master_szse" and spec.parameters == {}
                      and spec.current is True and spec.role == "CURRENT_OFFICIAL"
                      and value["status"] == result["status"] == "FETCH_FAILED"
                      and value["pagination_complete"] is False
                      and result.get("failure_category") == "NETWORK"
                      and result.get("error_code") == "UPSTREAM_TIMEOUT",
                      "EXACT_ONE_TIMEOUT_SOURCE_REQUIRED")
                source_retry = dict(authorization="ONE_EXACT_SOURCE_ATTEMPT", request_key=spec.key,
                    request_spec=spec.to_dict(), prior_receipt_id=value["receipt_id"],
                    prior_index_ref=index_ref, prior_attempt_dir=str(attempt), prior_attempt_refs=refs)
            else:
                _need(value["status"] == "COMPLETE" and value["pagination_complete"] is True,
                      "OTHER_PARENT_SOURCE_NOT_COMPLETE")
            counts["requests"] += 1
            if ENDPOINTS[spec.endpoint]["provider"] == "baostock":
                counts["sdk_reserved"] += (len(spec.parameters.get("codes", ())) + 2 if spec.endpoint in
                    ("adjustment_factors", "stock_basic", "history", "history_adjusted") else 3)
            else:
                counts["http_reserved"] += completion["metrics"]["limits"]["request_http_attempts"]
            coverage = result.get("coverage") or {}
            for target, field in (("sdk_actual", "sdk_operations"), ("http_actual", "http_attempts")):
                _need(type(coverage.get(field)) is int and coverage[field] >= 0,
                      "UNKNOWN_PROVIDER_ACTUAL_COUNTS")
                counts[target] += coverage[field]
            parent.append(dict(request_key=spec.key, receipt_id=value["receipt_id"],
                status=value["status"], index=index_ref, request_claim=claim_ref, commit=commit_ref))
    _need(source_retry is not None, "AUTHORIZED_PARENT_TIMEOUT_NOT_FOUND")
    calls = completion["metrics"]["calls"]
    _need(all(calls.get(key) == value for key, value in counts.items())
          and all(calls.get(key) == 0 for key in ("actual_unknown", "sdk_actual_unknown_requests",
                                                "http_actual_unknown_requests", "direct_http_get_attempts")),
          "PARENT_PROVIDER_METRICS_DO_NOT_RECONCILE")
    parent_index_paths = {item["index"]["path"] for item in parent}
    for records in indices.values():
        for value, reference in records:
            if _clock(value["started_at"]) >= start:
                _need(reference["path"] in parent_index_paths, "UNOWNED_INDEX_AFTER_PARENT_STARTED")
    calendar = cache.calendar_reference(now)
    _need(calendar is not None, "AUTHENTICATED_CURRENT_CALENDAR_REQUIRED")
    calendar_result = cache.verify(calendar)
    calendar_refs = [reader.reference(calendar.metadata[key]["path"])
                     for key in ("raw", "receipt", "result")]
    # One terminal check for this whole scan, never one cumulative re-read per
    # inherited directory. All authenticated originals remain terminal-bound.
    for reference in cache.terminal():
        prior = reader.refs.setdefault(reference["path"], reference)
        _need(content_reference_equal(prior, reference), "CACHE_REFERENCE_CHANGED_DURING_SCAN")
        inventory.append(reference)
    return source_retry, parent, workspace._digest(sorted(
        {(r["path"], r["sha256"], r["bytes"]) for r in inventory})), counts, calendar_result["rows"], calendar_refs, historical_inventory, reconciliation


def _remaining(operation, job, final, completion, original, per_snapshot, ledger_bytes):
    limits = deepcopy(original["limits"])
    metrics = completion["metrics"]
    _need(metrics.get("returned_read_bytes", {}).get("core") == 0
          and metrics["returned_read_bytes"].get("theme") == 0
          and metrics["returned_read_bytes"].get("presentation") == 0, "PRECORE_READ_PHASES_REQUIRED")
    supervisor = final.get("supervisor_returned_read_bytes")
    _need(type(supervisor) is int and supervisor >= 0, "PARENT_SUPERVISOR_READ_REQUIRED")
    upper = 2 * per_snapshot + operation["identity"]["configuration"]["bytes"] + supervisor + 2 * ledger_bytes
    parent_debit = math.ceil(upper / (256 * MIB)) * 256 * MIB
    elapsed = math.ceil((_clock(operation["finished_at"]) - _clock(operation["started_at"])).total_seconds())
    _need(elapsed >= final["elapsed_seconds"] and elapsed >= metrics["elapsed_seconds"],
          "CLOCK_CANNOT_BOUND_PARENT_ELAPSED")
    output_bytes = 0
    for path in job.rglob("*"):
        _need(not path.is_symlink(), "PARENT_JOB_ALIAS")
        if path.is_file():
            output_bytes += path.stat().st_size
    debits = dict(max_requests=metrics["calls"]["requests"], max_sdk_calls=metrics["calls"]["sdk_reserved"],
        max_http_attempts=metrics["calls"]["http_reserved"],
        capture_read_bytes=metrics["returned_read_bytes"]["capture"],
        supervisor_read_bytes=parent_debit + 2 * PREPARE_READ_BYTES + CONTROL_READ_BYTES + WRAPPER_PREPARE_READ_BYTES,
        output_bytes=output_bytes + JOURNAL_OUTPUT_BYTES,
        max_seconds=elapsed + OLD_OUTER_RESERVE_SECONDS + 2 * PREPARE_SECONDS + CONTROL_SECONDS + WRAPPER_PREPARE_SECONDS)
    for name, debit in debits.items():
        _need(type(debit) is int and debit >= 0 and isinstance(limits[name], (int, float))
              and limits[name] > debit, "ORIGINAL_BUDGET_EXHAUSTED:" + name)
        limits[name] -= debit
    Budget(limits)
    return limits, dict(debits=debits, original_elapsed_seconds_debit=elapsed,
        original_outer_reserve_seconds=OLD_OUTER_RESERVE_SECONDS,
        original_parent_static_upper_bound_bytes=upper, original_parent_read_debit_bytes=parent_debit,
        parent_read_is_measured=False, original_output_bytes=output_bytes,
        prepare_read_reserve_bytes=2 * PREPARE_READ_BYTES, prepare_seconds_reserve=2 * PREPARE_SECONDS,
        terminal_control_read_reserve_bytes=CONTROL_READ_BYTES, terminal_control_seconds_reserve=CONTROL_SECONDS,
        wrapper_prepare_read_reserve_bytes=WRAPPER_PREPARE_READ_BYTES,
        wrapper_prepare_seconds_reserve=WRAPPER_PREPARE_SECONDS,
        journal_output_reserve_bytes=JOURNAL_OUTPUT_BYTES,
        rss_policy="ORIGINAL_PEAK_CAP_MAX_NOT_ADDITIVE", cache_policy="ORIGINAL_GLOBAL_OCCUPANCY_CAP",
        prior_worker_peak_rss_bytes=metrics["peak_self_rss_bytes"], prior_tree_peak_rss_bytes=final["peak_tree_rss_bytes"])


def _verify_references(proposal, reader):
    for reference in proposal["validation_references"]:
        if reference["path"] == proposal["snapshot_proof_ref"]["path"] and not Path(reference["path"]).exists():
            # The original external proof is retained when present. The exact
            # document is also sealed in the fixed slot, so /tmp is not its
            # only recoverable copy after a successful continuation.
            local = Path(proposal["scope_directory"]) / "operations" / proposal["operation_id"] / "continuation" / "parent-snapshot-proof.json"
            document, _ = reader.read(local)
            _need(document == proposal["parent_snapshot_proof"], "OWNED_SNAPSHOT_PROOF_CHANGED")
            continue
        reconciliation = proposal.get("factor_reconciliation")
        if (reconciliation is not None and reference["path"] == reconciliation["review_ref"]["path"]
                and not Path(reference["path"]).exists()):
            _reconciliation_record(proposal, reader)
            continue
        reader.budget.read(reference["path"], expected=reference, limit=max(reference["bytes"], 1))
    if proposal.get("factor_reconciliation") is not None:
        # Content references alone cannot detect a newly appended ancestor
        # attempt. Recheck this one reviewed inventory at both admission and
        # terminal delivery, without rescanning unrelated historical sources.
        from stockfish_core.yaogu.collection_factor_reconciliation import verify_factor_reconciliation
        cache = CaptureCache(Path(proposal["identity"]["state_root"]) / "cache", reader.budget,
            config=dict(offline=True, retry_failed=False,
                allow_test_fixture=proposal["preparation_arguments"].get("allow_test_fixture", False)),
            executor=lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("CONTINUATION_NETWORK_FORBIDDEN")))
        verify_factor_reconciliation(cache, proposal["factor_reconciliation"])
        for reference in cache.terminal():
            prior = reader.refs.setdefault(reference["path"], reference)
            _need(content_reference_equal(prior, reference), "RECONCILIATION_TERMINAL_REFERENCE_CHANGED")


def _references(value):
    if isinstance(value, dict):
        if {"path", "sha256", "bytes"}.issubset(value):
            yield value
        else:
            for nested in value.values():
                yield from _references(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _references(nested)


def _reconciliation_record(proposal, reader, expected=None, claimed_at=None):
    slot = Path(proposal["scope_directory"]) / "operations" / proposal["operation_id"] / "continuation"
    path = slot / "reconciliation.json"
    if expected is not None:
        _need(expected.get("path") == str(path), "RECONCILIATION_FIXED_SLOT_REQUIRED")
    record, reference = reader.read(expected if expected is not None else path)
    _need(record.get("schema_version") == RECONCILIATION_VERSION
          and content_equal(record.get("reconciliation"), proposal.get("factor_reconciliation")),
          "SEALED_RECONCILIATION_CHANGED")
    review_ref = proposal["factor_reconciliation"]["review_ref"]
    raw = record.get("review_json")
    _need(isinstance(raw, str), "OWNED_RECONCILIATION_REVIEW_REQUIRED")
    raw = raw.encode("utf-8")
    _need(len(raw) == review_ref["bytes"] and hashlib.sha256(raw).hexdigest() == review_ref["sha256"],
          "OWNED_RECONCILIATION_REVIEW_CHANGED")
    parent, _ = reader.read(proposal["original_operation_ref"])
    reviewed = strict_json(raw)
    registered = _clock(record["registered_at"])
    _need(max(_clock(parent["finished_at"]), _clock(reviewed["checked_at"])) <= registered
          <= datetime.now(timezone.utc)
          and str(registered.astimezone(SH).date()) == proposal["source_retry"]["request_spec"]["business_session"]
          and (claimed_at is None or registered <= _clock(claimed_at)),
          "RECONCILIATION_CURRENT_REGISTRATION_CLOCK_REQUIRED")
    return record, reference


def _proposal_digest(proposal):
    return workspace._digest(_content({k: v for k, v in proposal.items()
                                      if k not in ("proposal_id", "preparation_metrics")}))


def prepare(runtime, *, operation_path, source_job, snapshot_proof_path,
            expected_snapshot_proof_sha256, allowed_source_key, allowed_receipt_id,
            allow_test_fixture=False, factor_reconciliation=None, _now_value=None):
    """Authenticate originals and return a bounded proposal; write nothing."""
    now = _now(_now_value, allow_test_fixture)
    if allow_test_fixture:
        _need("TEST_FIXTURE" in str(runtime.directory) and "TEST_FIXTURE" in str(source_job),
              "EXPLICIT_ISOLATED_TEST_FIXTURE_REQUIRED")
    budget = Budget(_reader_limits())
    with budget.meter_files():
        reader = _Reader(budget)
        operation, operation_ref = _identity(runtime, operation_path, reader)
        slot = _slot(runtime, operation["operation_id"])
        if (slot / "claim.json").exists():
            stored, _ = reader.read(slot / "proposal.json")
            claim, _ = reader.read(slot / "claim.json")
            _need((slot / "terminal.json").is_file(), "CLAIM_EXISTS_NO_RETRY")
            terminal, _ = reader.read(slot / "terminal.json")
            _need(terminal.get("status") == "SUCCEEDED" and terminal.get("proposal_id") == stored.get("proposal_id")
                  and claim.get("proposal_id") == stored.get("proposal_id")
                  and stored.get("source_job") == str(source_job)
                  and stored.get("source_retry", {}).get("request_key") == allowed_source_key
                  and stored["source_retry"].get("prior_receipt_id") == allowed_receipt_id
                  and stored.get("snapshot_proof_ref", {}).get("sha256") == expected_snapshot_proof_sha256
                  and stored.get("preparation_arguments", {}).get("factor_reconciliation") == factor_reconciliation,
                  "CLAIM_EXISTS_NO_RETRY")
            _verify_references(stored, reader)
            if stored.get("factor_reconciliation") is not None:
                _need(isinstance(claim.get("factor_reconciliation_ref"), dict), "BOUND_RECONCILIATION_REFERENCE_REQUIRED")
                _reconciliation_record(stored, reader, claim["factor_reconciliation_ref"], claim["claimed_at"])
            return stored
        proof, proof_ref = reader.read(snapshot_proof_path)
        _need(proof_ref["sha256"] == expected_snapshot_proof_sha256
              and content_reference_equal(proof["original_operation"], operation_ref), "EXPECTED_SNAPSHOT_PROOF_CHANGED")
        job, final, completion, registry, original = _parent(runtime, operation, source_job, proof, reader)
        per_snapshot, ledger_bytes = _snapshot(runtime, operation, proof, registry, reader)
        cache_root = Path(original["state_root"]) / "cache"
        case = _factor_review(factor_reconciliation, operation, cache_root, reader)
        retry, parent, inventory, counts, calendar, calendar_refs, historical, reconciliation = _attempts(cache_root, operation,
            completion, reader, allowed_key=allowed_source_key, allowed_receipt=allowed_receipt_id,
            fixture=allow_test_fixture, now=now, reconciliation_case=case, operation_ref=operation_ref,
            runtime=runtime, source_job=job)
        _need(retry["request_spec"]["business_session"] == str(now.astimezone(SH).date())
              == completion["expected_sessions"][2], "CURRENT_SOURCE_DAY_CHANGED")
        from stockfish_core.yaogu.premarket_workflow_v1 import _latest_gate
        _latest_gate(calendar, operation["identity"]["observation_date"], operation["identity"]["target_session"], now)
        limits, accounting = _remaining(operation, job, final, completion, original, per_snapshot, ledger_bytes)
        # Keep only immutable parent evidence in the proposal. The full cache
        # inventory is re-enumerated and compared by execute before its claim.
        parent_paths = {r["path"] for item in parent for key in ("index", "request_claim", "commit") for r in [item[key]]}
        parent_paths.update(r["path"] for r in retry["prior_attempt_refs"])
        for item in parent:
            value, _ = reader.read(item["index"])
            parent_paths.update(value[key]["path"] for key in ("raw", "receipt", "result"))
        keep = {str(operation_path), str(snapshot_proof_path), operation["identity"]["configuration"]["path"]}
        keep.update(str(job / name) for name in ("final-control.json", "worker-completion.json", "configuration.json", "code-registry.json"))
        keep.update(r["path"] for r in proof["operation_ledger_references"])
        keep.update(parent_paths)
        keep.update(r["path"] for r in calendar_refs)
        if reconciliation is not None:
            _need(content_reference_equal(reconciliation.get("review_ref"), case["review_ref"]),
                  "RECONCILIATION_REVIEW_BINDING_CHANGED")
            for reference in _references(reconciliation):
                _, actual = reader.budget.read(_path(reference["path"]), expected=reference["sha256"],
                                               limit=max(reference["bytes"], 1))
                _need(content_reference_equal(actual, reference), "RECONCILIATION_REFERENCE_CHANGED")
                prior = reader.refs.setdefault(reference["path"], actual)
                _need(content_reference_equal(prior, actual), "RECONCILIATION_REFERENCE_CHANGED")
                keep.add(reference["path"])
        proposal = dict(schema_version=VERSION, status=ADMITTED, operation_id=operation["operation_id"],
            scope=deepcopy(runtime.scope), scope_directory=str(runtime.directory), identity=deepcopy(operation["identity"]),
            request_key=operation["identity"]["request_key"], source_job=str(job),
            original_operation_ref=operation_ref, snapshot_proof_ref=proof_ref, parent_snapshot_proof=proof,
            original_limits=original["limits"], remaining_limits=limits, budget_debits=accounting,
            source_retry=retry, parent_provider_requests=parent, parent_provider_counts=counts,
            retained_historical_inventory=historical,
            parent_cache_inventory_sha256=inventory, expected_sessions=completion["expected_sessions"],
            validation_references=[reader.refs[name] for name in sorted(keep)],
            request_claimed=False, original_failure_rewritten=False,
            preparation_arguments=dict(operation_path=str(operation_path), source_job=str(source_job),
                snapshot_proof_path=str(snapshot_proof_path), expected_snapshot_proof_sha256=expected_snapshot_proof_sha256,
                allowed_source_key=allowed_source_key, allowed_receipt_id=allowed_receipt_id,
                allow_test_fixture=allow_test_fixture))
        if reconciliation is not None:
            proposal["factor_reconciliation"] = reconciliation
            proposal["preparation_arguments"]["factor_reconciliation"] = deepcopy(factor_reconciliation)
        reader.verify_all()
        proposal["proposal_id"] = _proposal_digest(proposal)
    proposal["preparation_metrics"] = budget.metrics
    return proposal


def _authorization(runtime, proposal, claim_ref, proposal_ref):
    return dict(schema_version=AUTHORIZATION_VERSION, scope=deepcopy(runtime.scope),
        scope_directory=str(runtime.directory), operation_id=proposal["operation_id"],
        claim=claim_ref, proposal=proposal_ref)


def authorization(runtime, proposal):
    """Read the already persisted fixed claim for receipt validation/reuse."""
    slot = _slot(runtime, proposal["operation_id"])
    reader = _Reader(Budget(_reader_limits()))
    stored, proposal_ref = reader.read(slot / "proposal.json")
    claim, claim_ref = reader.read(slot / "claim.json")
    _need(stored.get("proposal_id") == proposal.get("proposal_id") == _proposal_digest(stored)
          and claim.get("schema_version") == CLAIM_VERSION
          and claim.get("operation_id") == proposal["operation_id"]
          and claim.get("scope") == runtime.scope
          and claim.get("proposal_id") == proposal["proposal_id"]
          and content_reference_equal(claim["proposal_ref"], proposal_ref)
          and content_equal(claim["source_retry"], proposal["source_retry"])
          and claim["remaining_limits"] == proposal["remaining_limits"], "FIXED_AUTHORIZATION_CHANGED")
    if stored.get("factor_reconciliation") is not None:
        _need(isinstance(claim.get("factor_reconciliation_ref"), dict), "BOUND_RECONCILIATION_REFERENCE_REQUIRED")
        _reconciliation_record(stored, reader, claim["factor_reconciliation_ref"], claim["claimed_at"])
    else:
        _need("factor_reconciliation_ref" not in claim, "UNEXPECTED_RECONCILIATION_AUTHORIZATION")
    return _authorization(runtime, stored, claim_ref, proposal_ref)


def _terminal_metrics(reader, receipt, validation, proposal):
    """Record independently bound worker and coordinator consumption."""
    parent = validation.get("parent_verification_metrics")
    wrapper = validation.get("wrapper_preparation_metrics")
    _need(isinstance(parent, dict) and isinstance(wrapper, dict), "COORDINATOR_ACCOUNTING_REQUIRED")
    final, final_ref = reader.read(receipt["final"])
    completion, completion_ref = reader.read(receipt["completion"])
    worker = completion.get("metrics")
    job = _path(receipt["job"], directory=True)
    _need(job.parent == Path(proposal["identity"]["state_root"]) / "jobs"
          and str(job) != proposal["source_job"] and Path(final_ref["path"]) == job / "final-control.json"
          and Path(completion_ref["path"]) == job / "worker-completion.json"
          and content_reference_equal(final["completion"], completion_ref)
          and final.get("status") in ("COMPLETE", "COMPLETED_WITH_UNKNOWNS")
          and completion.get("status") == final["status"] and final.get("external_worker_reaped") is True
          and completion.get("expected_sessions") == proposal["expected_sessions"]
          and isinstance(worker, dict) and worker.get("limits") == proposal["remaining_limits"],
          "NEW_WORKER_METRICS_BINDING")
    for value in (worker, parent, wrapper):
        _need(value.get("pending_request_results") == 0 and value.get("failures") == []
              and value.get("calls", {}).get("actual_unknown") == 0, "NEW_ACCOUNTING_UNKNOWN_OR_FAILED")
        for field in ("requests", "sdk_reserved", "http_reserved", "sdk_actual", "http_actual"):
            _need(type(value.get("calls", {}).get(field)) is int and value["calls"][field] >= 0,
                  "NEW_COUNTER_SCHEMA")
        _need(isinstance(value.get("elapsed_seconds"), (int, float))
              and math.isfinite(value["elapsed_seconds"]) and value["elapsed_seconds"] >= 0,
              "NEW_ELAPSED_SCHEMA")
    limits = proposal["remaining_limits"]
    for counter, cap in (("requests", "max_requests"), ("sdk_reserved", "max_sdk_calls"),
                         ("http_reserved", "max_http_attempts")):
        _need(worker["calls"][counter] <= limits[cap], "NEW_WORKER_CALL_BUDGET_EXCEEDED")
    for phase in ("capture", "core", "theme", "presentation"):
        used = worker.get("returned_read_bytes", {}).get(phase)
        _need(type(used) is int and 0 <= used <= limits[phase + "_read_bytes"], "NEW_WORKER_READ_BUDGET_EXCEEDED")
    _need(parent.get("returned_read_bytes", {}).get("capture", -1) >= 0
          and parent["returned_read_bytes"]["capture"] <= limits["supervisor_read_bytes"]
          and parent["elapsed_seconds"] <= limits["max_seconds"]
          and worker["elapsed_seconds"] <= limits["max_seconds"]
          and wrapper.get("returned_read_bytes", {}).get("capture", -1) >= 0
          and wrapper["returned_read_bytes"]["capture"] <= WRAPPER_PREPARE_READ_BYTES
          and wrapper["elapsed_seconds"] <= WRAPPER_PREPARE_SECONDS, "COORDINATOR_BUDGET_EXCEEDED")
    return dict(worker_metrics=worker, worker_final_ref=final_ref, worker_completion_ref=completion_ref,
        coordinator_metrics=parent, wrapper_preparation_metrics=wrapper,
        cumulative_calls={name: proposal["parent_provider_counts"][name] + worker["calls"][name]
                          for name in ("requests", "sdk_reserved", "http_reserved", "sdk_actual", "http_actual")})


def execute(runtime, proposal, *, action, validate, authorize_existing_factor_reconciliation=False):
    """Append one claim; action(limits, authorization) is never repeated.

    validate(receipt, proposal) must authenticate the new sealed job, effective
    limits and success contract. An exception leaves the old operation intact
    and appends a terminal failure when possible; a bare claim is equally sticky.
    """
    _need(callable(action) and callable(validate), "CALLBACKS_REQUIRED")
    _need(type(authorize_existing_factor_reconciliation) is bool
          and authorize_existing_factor_reconciliation == (proposal.get("factor_reconciliation") is not None),
          "EXPLICIT_FACTOR_RECONCILIATION_AUTHORIZATION_REQUIRED")
    _need(proposal.get("schema_version") == VERSION and proposal.get("status") == ADMITTED
          and proposal.get("proposal_id") == _proposal_digest(proposal), "PROPOSAL_CHANGED")
    _need(proposal.get("scope") == runtime.scope
          and proposal.get("scope_directory") == str(runtime.directory), "PROPOSAL_SCOPE_CHANGED")
    slot = _slot(runtime, proposal["operation_id"])
    started = time.monotonic()
    utc_started = datetime.now(timezone.utc)
    with runtime.exclusive():
        control = Budget(_reader_limits(seconds=proposal["remaining_limits"]["max_seconds"] + PREPARE_SECONDS + CONTROL_SECONDS,
                                         byte_limit=CONTROL_READ_BYTES))
        reader = _Reader(control)
        with control.meter_files():
            _verify_references(proposal, reader)
            _identity(runtime, proposal["original_operation_ref"]["path"], reader)
        if (slot / "claim.json").exists():
            with control.meter_files():
                stored, proposal_ref = reader.read(slot / "proposal.json")
                claim, claim_ref = reader.read(slot / "claim.json")
                _need(stored.get("proposal_id") == proposal["proposal_id"]
                      and claim.get("proposal_id") == proposal["proposal_id"], "CLAIM_PROPOSAL_CHANGED")
                _need((slot / "terminal.json").is_file(), "CLAIM_EXISTS_NO_RETRY")
                terminal, terminal_ref = reader.read(slot / "terminal.json")
                _need(terminal.get("schema_version") == TERMINAL_VERSION
                      and terminal.get("status") == "SUCCEEDED"
                      and content_reference_equal(terminal["claim_ref"], claim_ref)
                      and content_reference_equal(claim["proposal_ref"], proposal_ref), "CLAIM_EXISTS_NO_RETRY")
                if stored.get("factor_reconciliation") is not None:
                    _need(isinstance(claim.get("factor_reconciliation_ref"), dict),
                          "BOUND_RECONCILIATION_REFERENCE_REQUIRED")
                    _reconciliation_record(stored, reader, claim["factor_reconciliation_ref"], claim["claimed_at"])
            validation_started = time.monotonic()
            validation = validate(deepcopy(terminal["receipt"]), deepcopy(stored))
            with control.meter_files():
                _need(isinstance(validation, dict) and validation.get("verified") is True, "RECEIPT_NOT_VERIFIED")
                _need(time.monotonic() - validation_started <= CONTROL_SECONDS, "TERMINAL_VALIDATION_TIME_EXCEEDED")
                _terminal_metrics(reader, terminal["receipt"], validation, proposal)
                _verify_references(proposal, reader)
                reader.verify_all()
            return dict(status="REUSED", operation_id=proposal["operation_id"], receipt=terminal["receipt"],
                validation=validation, authorization=_authorization(runtime, stored, claim_ref, proposal_ref),
                terminal_ref=terminal_ref, new_action_calls=0, parent_metrics=control.metrics)
        # Re-enumerate requests under the shared lock. A stale preview cannot
        # authorize a request that another process has already attempted.
        fresh = prepare(runtime, **proposal["preparation_arguments"])
        _need(fresh["proposal_id"] == proposal["proposal_id"], "ADMISSION_CHANGED_BEFORE_CLAIM")
        _need(time.monotonic() - started <= PREPARE_SECONDS, "LOCKED_PREPARATION_TIME_EXCEEDED")
        claim_ref = proposal_ref = None
        try:
            with control.meter_files():
                reconciliation_ref = None
                reconciliation = proposal.get("factor_reconciliation")
                if reconciliation is not None:
                    reconciliation_path = slot / "reconciliation.json"
                    if reconciliation_path.exists():
                        record, reconciliation_ref = _reconciliation_record(proposal, reader)
                    else:
                        review_ref = reconciliation["review_ref"]
                        raw, actual_review = reader.budget.read(_path(review_ref["path"]), expected=review_ref["sha256"],
                                                   limit=max(review_ref["bytes"], 1))
                        _need(content_reference_equal(actual_review, review_ref), "RECONCILIATION_REVIEW_CHANGED")
                        record = dict(schema_version=RECONCILIATION_VERSION, reconciliation=reconciliation,
                            registered_at=datetime.now(timezone.utc).isoformat(), review_json=raw.decode("utf-8"))
                        fixed_bytes = sum(len(workspace._canonical(value)) for value in
                                          (proposal, proposal["parent_snapshot_proof"], record))
                        _need(fixed_bytes + 64 * 1024 <= JOURNAL_OUTPUT_BYTES - workspace.MAX_RECEIPT,
                              "RECONCILIATION_OUTPUT_RESERVE_INSUFFICIENT")
                        workspace._write(reconciliation_path, record)
                        _, reconciliation_ref = _reconciliation_record(proposal, reader)
                if (slot / "proposal.json").exists():
                    stored, proposal_ref = reader.read(slot / "proposal.json")
                    _need(stored.get("proposal_id") == proposal["proposal_id"], "EXISTING_PROPOSAL_CHANGED")
                else:
                    workspace._write(slot / "proposal.json", proposal)
                    _, proposal_ref = reader.read(slot / "proposal.json")
                snapshot_path = slot / "parent-snapshot-proof.json"
                if not snapshot_path.exists():
                    workspace._write(snapshot_path, proposal["parent_snapshot_proof"])
                document, snapshot_ref = reader.read(snapshot_path)
                _need(document == proposal["parent_snapshot_proof"], "OWNED_SNAPSHOT_PROOF_CHANGED")
                claim = dict(schema_version=CLAIM_VERSION, proposal_id=proposal["proposal_id"],
                    scope=deepcopy(runtime.scope), scope_directory=str(runtime.directory),
                    operation_id=proposal["operation_id"], request_key=proposal["request_key"],
                    source_job=proposal["source_job"], original_operation_ref=proposal["original_operation_ref"],
                    snapshot_proof_ref=snapshot_ref, original_snapshot_proof_ref=proposal["snapshot_proof_ref"], proposal_ref=proposal_ref,
                    source_retry=deepcopy(proposal["source_retry"]), remaining_limits=deepcopy(proposal["remaining_limits"]),
                    claimed_at=datetime.now(timezone.utc).isoformat(), original_failure_rewritten=False)
                if reconciliation_ref is not None:
                    claim["factor_reconciliation_ref"] = reconciliation_ref
                _need(len(workspace._canonical(proposal)) + len(workspace._canonical(claim)) < JOURNAL_OUTPUT_BYTES // 2,
                      "CONTROL_OUTPUT_RESERVE_INSUFFICIENT")
                _need(str(datetime.now(SH).date()) == proposal["source_retry"]["request_spec"]["business_session"],
                      "CURRENT_SOURCE_DAY_CHANGED_BEFORE_CLAIM")
                workspace._write(slot / "claim.json", claim)
                _, claim_ref = reader.read(slot / "claim.json")
            authorization = _authorization(runtime, proposal, claim_ref, proposal_ref)
            receipt = action(deepcopy(proposal["remaining_limits"]), authorization)
            validation_started = time.monotonic()
            with control.meter_files():
                _need(isinstance(receipt, dict) and len(workspace._canonical(receipt)) <= workspace.MAX_RECEIPT,
                      "BOUNDED_RECEIPT_REQUIRED")
            validation = validate(deepcopy(receipt), deepcopy(proposal))
            with control.meter_files():
                _need(isinstance(validation, dict) and validation.get("verified") is True, "RECEIPT_NOT_VERIFIED")
                measured = _terminal_metrics(reader, receipt, validation, proposal)
                _verify_references(proposal, reader)
                reader.verify_all()
                runtime.check_binding()
                _need(time.monotonic() - validation_started <= CONTROL_SECONDS, "TERMINAL_VALIDATION_TIME_EXCEEDED")
                _need(str(datetime.now(SH).date()) == proposal["source_retry"]["request_spec"]["business_session"],
                      "CURRENT_SOURCE_DAY_CHANGED_AT_TERMINAL")
                total_bound = proposal["remaining_limits"]["max_seconds"] + PREPARE_SECONDS + CONTROL_SECONDS
                _need(time.monotonic() - started <= total_bound
                      and 0 <= (datetime.now(timezone.utc) - utc_started).total_seconds() <= total_bound,
                      "CUMULATIVE_EXECUTION_CLOCK_LIMIT")
                control.check("continuation_terminal", force=True)
                terminal = dict(schema_version=TERMINAL_VERSION, status="SUCCEEDED", proposal_id=proposal["proposal_id"],
                    operation_id=proposal["operation_id"], claim_ref=claim_ref, receipt=receipt, validation=validation,
                    completed_at=datetime.now(timezone.utc).isoformat(), original_failure_rewritten=False,
                    new_accounting=measured,
                    parent_metrics=dict(control=control.metrics, locked_preparation=fresh["preparation_metrics"],
                        elapsed_seconds=time.monotonic() - started,
                        accounting="RESERVED_CONTROL_ENVELOPES_NOT_WORKER_CREDITS"))
                _need(sum(p.stat().st_size for p in slot.iterdir() if p.is_file())
                      + len(workspace._canonical(terminal)) <= JOURNAL_OUTPUT_BYTES, "CONTROL_OUTPUT_RESERVE_EXCEEDED")
                _need(control.metrics["returned_read_bytes"]["capture"] + len(workspace._canonical(terminal))
                      <= CONTROL_READ_BYTES, "TERMINAL_READ_RESERVE_EXCEEDED")
                workspace._write(slot / "terminal.json", terminal)
                _, terminal_ref = reader.read(slot / "terminal.json")
            return dict(status="RECORDED", operation_id=proposal["operation_id"], receipt=receipt,
                validation=validation, authorization=authorization, terminal_ref=terminal_ref,
                new_action_calls=1, parent_metrics=terminal["parent_metrics"])
        except BaseException as error:
            if claim_ref is not None and not (slot / "terminal.json").exists():
                try:
                    failure = getattr(error, "continuation_failure", None)
                    if failure is not None:
                        _need(isinstance(failure, dict) and set(failure) <=
                              {"parent_verification_metrics", "worker_final", "job"}
                              and len(workspace._canonical(failure)) <= workspace.MAX_RECEIPT,
                              "FAILURE_METRICS_SCHEMA")
                        failure = deepcopy(failure)
                        if failure.get("worker_final") is not None:
                            value, reference = reader.read(failure["worker_final"])
                            expected_job = _path(failure["job"], directory=True)
                            _need(expected_job.parent == Path(proposal["identity"]["state_root"]) / "jobs"
                                  and Path(reference["path"]) == expected_job / "final-control.json"
                                  and value.get("job") == str(expected_job), "FAILURE_WORKER_REFERENCE")
                    # Never persist provider exception text or overwrite an outcome.
                    workspace._write(slot / "terminal.json", dict(schema_version=TERMINAL_VERSION,
                        status="FAILED_OR_UNKNOWN", operation_id=proposal["operation_id"],
                        proposal_id=proposal["proposal_id"], claim_ref=claim_ref,
                        reason="ACTION_OR_TERMINAL_VALIDATION_FAILED_NO_RETRY", error_type=type(error).__name__,
                        completed_at=datetime.now(timezone.utc).isoformat(), parent_metrics=control.metrics,
                        continuation_failure=failure, original_failure_rewritten=False))
                except BaseException:
                    # The immutable claim already blocks every new action.
                    # An audit-write problem must not replace the primary error.
                    pass
            raise


def _future_proposal_id(proposal):
    return workspace._digest(_content({key: value for key, value in proposal.items()
        if key not in ("proposal_id", "read_only_metrics")}))


def _future_readonly_cache(root, budget, allow_test_fixture):
    from scripts.codex_prescreen_view import _ReadOnlyCaptureCache
    cache = _ReadOnlyCaptureCache(root, budget, allow_test_fixture=allow_test_fixture)
    # The inherited inventory reader additionally decodes bounded saved index
    # entries. No constructor, writer, import or provider method is enabled.
    cache.max_entries = 8192
    cache.recovery_context = cache.continuation_context = None
    return cache


def prepare_future_factor_authorization(runtime, *, operation_path, expected_terminal_sha256,
                                        allow_test_fixture=False):
    """Prepare one exact future-date policy from an immutable successful slot."""
    from stockfish_core.yaogu.collection_factor_reconciliation import (
        CONTINUATION_FILES, FUTURE_FILE, future_factor_authorization_basis, verify_future_factor_authorization)
    _need(type(allow_test_fixture) is bool and isinstance(expected_terminal_sha256, str)
          and re.fullmatch("[0-9a-f]{64}", expected_terminal_sha256), "FUTURE_EXACT_TERMINAL_SHA_REQUIRED")
    _need(not allow_test_fixture or "TEST_FIXTURE" in str(runtime.directory), "EXPLICIT_ISOLATED_TEST_FIXTURE_REQUIRED")
    budget = Budget(_reader_limits(seconds=FUTURE_SECONDS, byte_limit=FUTURE_READ_BYTES))
    with budget.meter_files():
        runtime.check_binding()
        reader = _Reader(budget)
        path = _path(operation_path)
        operation, operation_ref = reader.read(path)
        _need(runtime.profile.get("backend") == "premarket" and path.parent == Path(runtime.directory) / "operations"
              and operation.get("scope") == runtime.scope and operation.get("phase") == "FAILED_OR_UNKNOWN"
              and operation.get("operation_id") == path.stem
              and path.stem == workspace._digest(dict(scope=runtime.scope, identity=operation["identity"]))
              and operation["identity"].get("state_root") == runtime.profile["state_root"],
              "FUTURE_ORIGINAL_OPERATION_SCOPE_REQUIRED")
        slot = _slot(runtime, path.stem)
        _, terminal_ref = reader.read(slot / "terminal.json")
        _need(terminal_ref["sha256"] == expected_terminal_sha256, "FUTURE_EXPECTED_TERMINAL_CHANGED")
        cache_root = _path(Path(runtime.profile["state_root"]) / "cache", directory=True)
        for name in ("index", "raw", "raw-index", "locators"):
            _path(cache_root / name, directory=True)
        cache = _future_readonly_cache(cache_root, budget, allow_test_fixture)
        basis = future_factor_authorization_basis(cache, scope=runtime.scope,
            scope_directory=str(runtime.directory), operation_id=path.stem, terminal_reference=terminal_ref)
        existing = None
        if (slot / FUTURE_FILE).exists() or (slot / FUTURE_FILE).is_symlink():
            _, existing = reader.read(slot / FUTURE_FILE)
            verify_future_factor_authorization(cache, existing, context=dict(scope=runtime.scope,
                scope_directory=str(runtime.directory)))
        for reference in cache.terminal():
            previous = reader.refs.setdefault(reference["path"], reference)
            _need(content_reference_equal(previous, reference), "FUTURE_REFERENCE_CHANGED_DURING_PREPARATION")
        _need(len(workspace._canonical({**basis, "registered_at": datetime.now(timezone.utc).isoformat()}))
              <= FUTURE_RECORD_BYTES, "FUTURE_METADATA_BOUND")
        old_refs = [reader.refs[str(slot / name)] for name in sorted(CONTINUATION_FILES)]
        result = dict(schema_version=FUTURE_PROPOSAL_VERSION, status="PREPARED_EXACT_FUTURE_FACTOR_AUTHORIZATION",
            scope=deepcopy(runtime.scope), scope_directory=str(runtime.directory), operation_id=path.stem,
            original_operation_ref=operation_ref, original_slot_references=old_refs,
            authorization=basis, existing_authorization_ref=existing,
            validation_references=[reader.refs[key] for key in sorted(reader.refs)],
            preparation_arguments=dict(operation_path=str(path), expected_terminal_sha256=expected_terminal_sha256,
                allow_test_fixture=allow_test_fixture), provider_requests=0, strategy_executions=0,
            original_records_rewritten=False, collection_budget_changed=False, budget_credit=0,
            accounting_scope="METADATA_AUTHORIZATION_ONLY_NOT_COLLECTION_ALLOCATION")
        reader.verify_all()
        runtime.check_binding()
        result["proposal_id"] = _future_proposal_id(result)
    result["read_only_metrics"] = budget.metrics
    return result


def register_future_factor_authorization(runtime, proposal, *, authorize_future_observations=False):
    """Append only the approved policy; no provider or old continuation executes."""
    from stockfish_core.yaogu.collection_factor_reconciliation import (
        CONTINUATION_FILES, FUTURE_FILE, verify_future_factor_authorization)
    _need(authorize_future_observations is True, "FUTURE_EXPLICIT_AUTHORIZATION_REQUIRED")
    _need(proposal.get("schema_version") == FUTURE_PROPOSAL_VERSION
          and proposal.get("proposal_id") == _future_proposal_id(proposal)
          and proposal.get("scope") == runtime.scope and proposal.get("scope_directory") == str(runtime.directory),
          "FUTURE_PREPARED_AUTHORIZATION_CHANGED")
    with runtime.exclusive():
        fresh = prepare_future_factor_authorization(runtime, **proposal["preparation_arguments"])
        _need(fresh["operation_id"] == proposal["operation_id"]
              and content_reference_equal(fresh["original_operation_ref"], proposal["original_operation_ref"])
              and content_equal(fresh["authorization"], proposal["authorization"])
              and content_equal(fresh["original_slot_references"], proposal["original_slot_references"]),
              "FUTURE_ADMISSION_CHANGED_BEFORE_REGISTRATION")
        budget = Budget(_reader_limits(seconds=FUTURE_SECONDS, byte_limit=FUTURE_READ_BYTES))
        with budget.meter_files():
            reader = _Reader(budget)
            slot = _slot(runtime, proposal["operation_id"])
            expected_names = set(CONTINUATION_FILES)
            before = set(p.name for p in slot.iterdir())
            _need(before in (expected_names, expected_names | {FUTURE_FILE}), "FUTURE_SLOT_INVENTORY_CHANGED")
            for reference in fresh["validation_references"]:
                _, actual = budget.read(reference["path"], expected=reference, limit=max(reference["bytes"], 1))
                reader.refs[reference["path"]] = actual
            path = slot / FUTURE_FILE
            reused = path.exists()
            if not reused:
                record = {**proposal["authorization"], "registered_at": datetime.now(timezone.utc).isoformat()}
                _need(len(workspace._canonical(record)) <= FUTURE_RECORD_BYTES, "FUTURE_METADATA_BOUND")
                workspace._write(path, record)
            _, reference = reader.read(path)
            cache = _future_readonly_cache(Path(runtime.profile["state_root"]) / "cache", budget,
                proposal["preparation_arguments"]["allow_test_fixture"])
            verified = verify_future_factor_authorization(cache, reference,
                context=dict(scope=runtime.scope, scope_directory=str(runtime.directory)))
            _need(content_equal(verified, proposal["authorization"]), "FUTURE_REGISTERED_AUTHORIZATION_CHANGED")
            for item in cache.terminal():
                previous = reader.refs.setdefault(item["path"], item)
                _need(content_reference_equal(previous, item), "FUTURE_TERMINAL_REFERENCE_CHANGED")
            _need(set(p.name for p in slot.iterdir()) == expected_names | {FUTURE_FILE},
                  "FUTURE_SLOT_APPEND_NOT_EXACT")
            reader.verify_all()
            runtime.check_binding()
            budget.check("future_factor_registration", force=True)
        return dict(status="REUSED" if reused else "RECORDED", operation_id=proposal["operation_id"],
            authorization_id=verified["authorization_id"], authorization_ref=reference,
            original_records_rewritten=False, provider_requests=0, strategy_executions=0,
            collection_budget_changed=False, budget_credit=0,
            locked_preparation_metrics=fresh["read_only_metrics"], registration_metrics=budget.metrics)
