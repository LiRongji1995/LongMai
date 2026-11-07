"""Bounded read-only rerendering of an archived collection routing decision.

The saved core ranking remains the input. Only its original calendar and LHB
capture are reauthenticated; no strategy, source collection or journal runs.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import threading

from scripts import codex_premarket_shared_v1 as shared
from stockfish_core.file_integrity import content_equal, content_reference_equal
from stockfish_core.yaogu import collection_prescreen as routing
from stockfish_core.yaogu import premarket_capture_cache_v1 as captures
from stockfish_core.yaogu.premarket_native_core_v1 import _calendar_sessions, _typed
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, DEFAULT_LIMITS, strict_json


VERSION = "READONLY_ARCHIVED_COLLECTION_PRESCREEN_VIEW_V1"
READ_BYTES = 1024**3
SECONDS = 180
MAX_COMPRESSED = 64 * 1024**2
MAX_EXPANDED = 256 * 1024**2
STATES = ("ADMITTED", "NOT_PRIORITIZED", "UNKNOWN")


def _need(condition, reason):
    if not condition:
        raise ValueError("PRESCREEN_VIEW_" + reason)


def _clock(value):
    clock = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    _need(clock.utcoffset() is not None, "AWARE_SOURCE_CLOCK_REQUIRED")
    return clock


def _path(value):
    path = Path(value).expanduser().absolute()
    _need(path.resolve(strict=True) == path and not path.is_symlink(), "CANONICAL_PATH_REQUIRED")
    return path


class _Reader:
    def __init__(self, budget):
        self.budget, self.references = budget, {}

    def read(self, source, *, maximum=16 * 1024**2):
        expected = source if isinstance(source, dict) else None
        path = _path(expected["path"] if expected else source)
        raw, ref = self.budget.read(path, expected=expected["sha256"] if expected else None, limit=maximum)
        _need(expected is None or content_reference_equal(ref, expected), "CONTENT_REFERENCE_CHANGED")
        prior = self.references.setdefault(str(path), ref)
        _need(content_reference_equal(prior, ref), "CONTENT_CHANGED_DURING_VIEW")
        return raw, ref

    def document(self, source, *, maximum=16 * 1024**2):
        raw, ref = self.read(source, maximum=maximum)
        return strict_json(raw), ref

    def terminal(self):
        for reference in list(self.references.values()):
            self.read(reference, maximum=max(reference["bytes"], 1))


class _ReadOnlyCaptureCache(captures.CaptureCache):
    """Use native verification without the cache constructor's write setup."""
    def __init__(self, root, budget, *, allow_test_fixture=False):
        self.root, self.budget = _path(root), budget
        _need(all(_path(self.root / name).is_dir() for name in ("index", "raw", "raw-index", "locators")),
              "EXISTING_CAPTURE_CACHE_REQUIRED")
        self.config = dict(offline=True, retry_failed=False, allow_test_fixture=allow_test_fixture)
        self._owner = (os.getpid(), threading.get_ident())
        self._known = {}

    def _mkdir_owned(self, *args, **kwargs):
        raise ValueError("PRESCREEN_VIEW_EXISTING_NATIVE_LOCATOR_REQUIRED_NO_WRITE")

    def _save(self, *args, **kwargs):
        raise ValueError("PRESCREEN_VIEW_WRITE_FORBIDDEN")

    def _capture_new(self, *args, **kwargs):
        raise ValueError("PRESCREEN_VIEW_PROVIDER_FORBIDDEN")

    capture_one = _capture_new
    capture_authorized_once = _capture_new
    import_seed = _save


def _hash(value, budget):
    digest, since_check = hashlib.sha256(), 0
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                               default=str, allow_nan=False)
    for chunk in encoder.iterencode(value):
        raw = chunk.encode("utf-8")
        digest.update(raw)
        since_check += len(raw)
        if since_check >= 1024**2:
            budget.check("prescreen_view_hash")
            since_check = 0
    return digest.hexdigest()


def _run(reader, reference):
    raw, _ = reader.read(reference, maximum=MAX_COMPRESSED)
    total, parts = 0, []
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as handle:
        while part := handle.read(1024**2):
            total += len(part)
            _need(total <= MAX_EXPANDED, "DECOMPRESSED_RUN_BOUND")
            parts.append(part)
            reader.budget.check("prescreen_view_decompress")
    return strict_json(b"".join(parts))


def _counts(screen, eligible):
    _need(screen.get("eligible_pool_ids") == eligible, "SCREEN_ELIGIBLE_POOL_CHANGED")
    items = screen.get("items")
    _need(isinstance(items, list) and len(items) == len(eligible)
          and {item["instrument_id"] for item in items} == set(eligible), "SCREEN_ITEMS_CHANGED")
    counts = Counter(item.get("status") for item in items)
    _need(set(counts) <= set(STATES), "SCREEN_UNKNOWN_STATUS")
    groups = dict(ADMITTED="admitted_ids", NOT_PRIORITIZED="not_prioritized_ids", UNKNOWN="unknown_ids")
    for state, key in groups.items():
        _need(screen.get(key) == [item["instrument_id"] for item in items if item["status"] == state],
              "SCREEN_GROUP_CHANGED")
    result = {state: counts[state] for state in STATES}
    _need(screen.get("counts") == result and sum(result.values()) == len(eligible), "SCREEN_COUNTS_CHANGED")
    return result


def _bound_source(reader, cache, metadata, sealed, cutoff, *, expected_originals=None):
    _need(isinstance(metadata, dict), "SOURCE_LOCATOR_REQUIRED")
    for role in ("raw", "receipt", "result"):
        ref = metadata[role]
        _need(ref["path"] in sealed and content_reference_equal(sealed[ref["path"]], ref),
              "SOURCE_NOT_BOUND_BY_ORIGINAL_COMPLETION")
        if expected_originals is not None:
            _need(content_reference_equal(ref, expected_originals[role]), "ORIGINAL_LHB_BYTES_CHANGED")
    result = cache.verify(metadata)
    _need(result["request_receipt_id"] == metadata["receipt_id"], "SOURCE_RECEIPT_CHANGED")
    _need(max(_clock(metadata[key]) for key in ("first_seen_at", "ingested_at", "completed_at")) <= cutoff,
          "SOURCE_NOT_VISIBLE_AT_ORIGINAL_CUTOFF")
    return result


def read_prescreen(runtime, job, *, allow_test_fixture=False, observation_date=None, target_session=None):
    """Return a new view of sealed originals; never save or claim a new run."""
    _need(type(allow_test_fixture) is bool, "EXPLICIT_FIXTURE_FLAG_REQUIRED")
    _need(runtime.profile.get("backend") == "premarket", "REGISTERED_PREMARKET_RUNTIME_REQUIRED")
    state = _path(runtime.profile["state_root"])
    job = _path(job)
    _need(job.is_dir() and job.parent == state / "jobs", "JOB_OUTSIDE_REGISTERED_STATE")
    _need(not allow_test_fixture or "TEST_FIXTURE" in str(job), "ISOLATED_TEST_FIXTURE_REQUIRED")
    budget = Budget({**DEFAULT_LIMITS, "max_seconds": SECONDS, "capture_read_bytes": READ_BYTES})
    with budget.phase("capture"), budget.meter_files():
        if hasattr(runtime, "check_binding"):
            runtime.check_binding()
        reader = _Reader(budget)
        code = {ref["path"]: ref for ref in shared.code_refs()}
        for path in (Path(__file__).resolve(), Path(routing.__file__).resolve()):
            if str(path) not in code:
                _, code[str(path)] = reader.read(path)
        current_code = []
        for name in sorted(code):
            _, ref = reader.read(code[name])
            current_code.append(ref)
        final, final_ref = reader.document(job / "final-control.json")
        _need(final.get("schema_version") == shared.VERSION
              and final.get("status") in ("COMPLETE", "COMPLETED_WITH_UNKNOWNS")
              and final.get("core_verified") is True
              and final.get("source_terminal_binding_verified") is True
              and final.get("external_worker_reaped") is True, "SEALED_VERIFIED_CORE_REQUIRED")
        verified = shared.verify_job(job)
        _need(verified.get("archived_bytes_verified") is True
              and verified.get("owned_derivatives_verified") is True, "ARCHIVED_JOB_VERIFICATION_REQUIRED")
        completion, completion_ref = reader.document(final["completion"])
        _need(completion_ref["path"] == str(job / "worker-completion.json")
              and completion.get("status") == final["status"] and completion.get("core_verified") is True,
              "COMPLETION_BINDING_CHANGED")
        observed, target = completion["observation_date"], completion["target_session"]
        _need(observation_date is None or str(observation_date) == observed, "OBSERVATION_SESSION_MISMATCH")
        _need(target_session is None or str(target_session) == target, "TARGET_SESSION_MISMATCH")
        sealed = {}
        for reference in completion.get("validation_references", []):
            prior = sealed.setdefault(reference["path"], reference)
            _need(content_reference_equal(prior, reference), "SEALED_REFERENCE_CONFLICT")
        _need(final.get("source_reference_count") == len(completion.get("validation_references", [])),
              "SEALED_SOURCE_COUNT_CHANGED")
        artifacts = completion["core_artifacts"]
        for ref in artifacts.values():
            _need(_path(ref["path"]).parent == job / "core", "CORE_ARTIFACT_OUTSIDE_ORIGINAL_JOB")
        manifest, manifest_ref = reader.document(artifacts["manifest.json"])
        _need(manifest.get("schema_version") == "PREMARKET_NATIVE_CORE_V1" and manifest.get("status") == "PASS"
              and content_equal(manifest.get("artifacts"), {k: v for k, v in artifacts.items() if k != "manifest.json"}),
              "NATIVE_CORE_MANIFEST_CHANGED")
        run = _run(reader, artifacts["canonical-run.json.gz"])
        _need(run.get("content_hash") == manifest.get("canonical_content_hash")
              == _hash({key: value for key, value in run.items() if key != "content_hash"}, budget),
              "CANONICAL_CORE_HASH_CHANGED")
        _need(run.get("run_id") == manifest.get("run_id") == completion.get("run_id")
              and run.get("observation_date") == manifest.get("observation_date") == observed
              and run.get("target_session") == manifest.get("target_session") == target
              and run.get("decision_cutoff") == manifest.get("decision_cutoff"), "ORIGINAL_CORE_SCOPE_CHANGED")
        cutoff = _clock(run["decision_cutoff"])
        _need(cutoff <= _clock(run["generated_at"]) <= _clock(completion["completed_at"])
              <= _clock(final["final_sealed_at"]) <= datetime.now(timezone.utc), "ORIGINAL_CORE_CLOCK_ORDER")
        sidecar_ref = completion["collection_prescreen_ref"]
        _need(sidecar_ref["path"] == str(job / "collection-prescreen.json")
              and sidecar_ref["path"] in sealed
              and content_reference_equal(sidecar_ref, sealed[sidecar_ref["path"]]), "ORIGINAL_PRESCREEN_NOT_SEALED")
        old, _ = reader.document(sidecar_ref)
        result = run["results"][routing.STRATEGY]
        eligible = result["eligible_pool_ids"]
        _need(isinstance(eligible, list) and len(eligible) == len(set(eligible))
              and all(isinstance(iid, str) and re.fullmatch(r"[0-9]{6}\.(?:SH|SZ|BJ)", iid) for iid in eligible)
              and len({iid.split(".")[0] for iid in eligible}) == len(eligible), "UNAMBIGUOUS_ORIGINAL_SECURITY_POOL_REQUIRED")
        _need(old.get("version") == routing.VERSION and old.get("core_run_id") == run["run_id"]
              and old.get("r2_source_hash") == result["source_hash"] and old.get("observation_date") == observed
              and _clock(old["decision_cutoff"]) == cutoff
              and old.get("original_r2_selected_ids") == result["research_selected_ids"], "OLD_PRESCREEN_CORE_BINDING_CHANGED")
        old_counts = _counts(old, eligible)
        _need(completion.get("collection_prescreen_counts") ==
              {key: len(old[key]) for key in ("admitted_ids", "not_prioritized_ids", "unknown_ids")},
              "ORIGINAL_COMPLETION_COUNTS_CHANGED")
        # This mutable summary locates captures; only sealed original references
        # and the native verifier confer authority to the selected two parents.
        locator, _ = reader.document(job / "capture-summary.json")
        cache_root = state / "cache"
        cache = _ReadOnlyCaptureCache(cache_root, budget, allow_test_fixture=allow_test_fixture)
        calendars = [item for item in locator.get("core_records", []) if item.get("endpoint") == "calendar"]
        declared_calendars = [item for item in manifest["source_receipts"] if item.get("dataset") == "calendar"]
        _need(len(calendars) == len(declared_calendars) == 1, "ONE_ORIGINAL_CALENDAR_REQUIRED")
        calendar = calendars[0]
        _need(calendar["receipt_id"] == declared_calendars[0]["receipt_id"], "ORIGINAL_CALENDAR_RECEIPT_CHANGED")
        calendar_result = _bound_source(reader, cache, calendar, sealed, cutoff)
        physical, _ = reader.document(calendar["receipt"])
        _need(content_equal(_typed(calendar_result, physical).model_dump(mode="json"), declared_calendars[0]),
              "ORIGINAL_TYPED_CALENDAR_CHANGED")
        sessions = list(map(str, _calendar_sessions([calendar_result], date.fromisoformat(observed))))
        _need(len(sessions) >= routing.WINDOW and old.get("window_sessions") == sessions[-routing.WINDOW:],
              "ORIGINAL_FIVE_SESSION_WINDOW_CHANGED")
        sources = old.get("original_lhb_sources")
        lhb = locator.get("prescreen_records")
        _need(isinstance(sources, list) and isinstance(lhb, list) and len(sources) == len(lhb) == 1,
              "ONE_ORIGINAL_LHB_SOURCE_REQUIRED")
        _need(lhb[0]["receipt_id"] == sources[0]["receipt_id"]
              and old.get("lhb_source_refs") == [sources[0]["receipt_id"]], "ORIGINAL_LHB_RECEIPT_CHANGED")
        lhb_result = _bound_source(reader, cache, lhb[0], sealed, cutoff, expected_originals=sources[0])
        expected = captures.lhb_request_spec(sessions)
        _need(captures.RequestSpec(**lhb[0]["request_spec"]).key == expected.key
              and lhb_result["endpoint"] == expected.endpoint and lhb_result["parameters"] == expected.parameters,
              "ORIGINAL_LHB_WINDOW_SCOPE_CHANGED")
        summary = routing.project_lhb_summary(eligible_pool_ids=eligible,
            window_sessions=sessions[-routing.WINDOW:], authenticated_sources=[(lhb_result, lhb[0])])
        screen = routing.build_collection_prescreen(run, calendar_sessions=sessions, lhb_summary=summary)
        counts = _counts(screen, eligible)
        screen_hash = _hash(screen, budget)
        for reference in cache.terminal():
            prior = reader.references.setdefault(reference["path"], reference)
            _need(content_reference_equal(prior, reference), "SOURCE_CHANGED_DURING_VIEW")
        reader.terminal()
        runtime.check_binding() if hasattr(runtime, "check_binding") else None
        total, admitted = len(eligible), counts["ADMITTED"]
        viewed = dict(schema_version=VERSION, status="VERIFIED_READONLY_RECOMPUTED_PRESCREEN",
            role="RECOMPUTED_COLLECTION_ROUTING_VIEW_NOT_ORIGINAL_COLLECTION_RECORD", job=str(job),
            core_run_id=run["run_id"], r2_source_hash=result["source_hash"], observation_date=observed,
            target_session=target, decision_cutoff=run["decision_cutoff"],
            summary=dict(eligible_pool=total, counts=counts, original_counts=old_counts,
                collection_target_reduction=dict(numerator=total-admitted, denominator=total,
                    ratio=(total-admitted)/total if total else None, status="DEFINED" if total else "N/A"),
                lhb_window_complete=screen["lhb_window_complete"], lhb_original_rows=len(lhb_result["rows"])),
            recomputed_content_hash=screen_hash, original_prescreen_ref=sidecar_ref,
            original_final_ref=final_ref, original_completion_ref=completion_ref, core_manifest_ref=manifest_ref,
            original_lhb_sources=sources, original_calendar_receipt_id=calendar["receipt_id"],
            current_projection_code=current_code, source_reauthenticated_now=True,
            reauthenticated_source_roles=["calendar", "lhb_summary"],
            full_market_sources_reauthenticated=False, strategy_executions=0, provider_requests=0,
            original_sidecar_rewritten=False, original_unknowns_preserved=True, original_operation_reexecuted=False,
            registered_budget_changed=False, source_authority_restored=False, strategy_selection_unchanged=True,
            market_edge="NOT_ESTABLISHED", original_collection_savings_recomputed=False,
            read_only_accounting_scope="THIS_QUERY_ONLY_NO_COLLECTION_ALLOCATION_OR_REFUND")
        budget.check("prescreen_view_return", force=True)
    viewed["read_only_metrics"] = budget.metrics
    return viewed
