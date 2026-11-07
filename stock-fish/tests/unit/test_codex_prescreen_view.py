"""Native TEST_FIXTURE originals for the read-only prescreen view."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts import codex_prescreen_view as view
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from stockfish_core.yaogu import collection_prescreen as routing
from stockfish_core.yaogu.history_artifacts import digest_json_stream
from tests.yaogu import test_premarket_capture_cache_v1 as fixtures


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return workflow._ref(path)


@pytest.fixture
def saved(tmp_path):
    root = (tmp_path / "TEST_FIXTURE_prescreen_view").resolve()
    state, job = root / "state", root / "state/jobs/TEST_FIXTURE_saved"
    job.mkdir(parents=True)
    observed = datetime.now(timezone.utc).date() - timedelta(days=1)
    while observed.weekday() >= 5:
        observed -= timedelta(days=1)
    target = observed + timedelta(days=1)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    executor = fixtures.FixtureExecutor()
    cache = view.captures.CaptureCache(state / "cache", fixtures.FixtureBudget(),
        config=dict(allow_test_fixture=True), executor=executor)
    calendar = cache.capture_one(view.captures.RequestSpec("calendar", dict(
        start_date=str(observed - timedelta(days=30)), end_date=str(target)), str(observed)))
    calendar_result = cache.verify(calendar)
    sessions = list(map(str, view._calendar_sessions([calendar_result], observed)))
    original_archive = fixtures.archive_receipt
    def archive(*args, **kwargs):
        if args[2] == "lhb_summary":
            args[4]["rows"].append({"代码": "600001", "上榜日": str(observed) + "T00:00:00.000"})
        return original_archive(*args, **kwargs)
    with patch.object(fixtures, "archive_receipt", archive):
        lhb = cache.capture_one(view.captures.lhb_request_spec(sessions))
    cache.verify(lhb)
    cutoff = datetime.now(timezone.utc).isoformat()
    ids = ["600001.SH", "000001.SZ"]
    rows = [dict(instrument_id=iid, research_selected=False, decision_status="REJECTED",
        liquidity=dict(status="AVAILABLE", value="1", reference_cny="300000000",
            evidence=dict(evidence_status="VALID", session=str(observed), source_refs=["TEST_FIXTURE_AMOUNT"])),
        participation=dict(status="FAIL", observations=[])) for iid in ids]
    result = dict(strategy_id=routing.STRATEGY, source_hash="a" * 64, observation_date=str(observed),
        decision_cutoff=cutoff, eligible_pool_ids=ids, full_eligible_pool_retained=True,
        research_selected_ids=[], items=rows, market_turnover_context=dict(source_refs=["TEST_FIXTURE_MARKET"]))
    run = dict(run_id="TEST_FIXTURE_SEALED_R2", observation_date=str(observed), target_session=str(target),
        decision_cutoff=cutoff, generated_at=datetime.now(timezone.utc).isoformat(),
        results={routing.STRATEGY: result})
    run["content_hash"] = digest_json_stream(run)
    core = job / "core"
    core.mkdir()
    run_path = core / "canonical-run.json.gz"
    run_path.write_bytes(gzip.compress(json.dumps(run).encode(), mtime=0))
    artifacts = {"canonical-run.json.gz": workflow._ref(run_path)}
    physical = json.loads(Path(calendar.metadata["receipt"]["path"]).read_bytes())
    manifest = dict(schema_version="PREMARKET_NATIVE_CORE_V1", status="PASS", run_id=run["run_id"],
        observation_date=str(observed), target_session=str(target), decision_cutoff=cutoff,
        canonical_content_hash=run["content_hash"], artifacts=deepcopy(artifacts),
        source_receipts=[view._typed(calendar_result, physical).model_dump(mode="json")])
    artifacts["manifest.json"] = save(core / "manifest.json", manifest)
    summary = dict(status="UNKNOWN", pagination_complete=True, window_sessions=sessions[-5:],
        source_refs=[lhb.receipt_id], visible_at=lhb.metadata["completed_at"], records=[])
    old = routing.build_collection_prescreen(run, calendar_sessions=sessions, lhb_summary=summary)
    old["original_lhb_sources"] = [dict(receipt_id=lhb.receipt_id,
        **{key: lhb.metadata[key] for key in ("raw", "receipt", "result")})]
    screen_ref = save(job / "collection-prescreen.json", old)
    capture = dict(core_records=[calendar.metadata], prescreen_records=[lhb.metadata])
    save(job / "capture-summary.json", capture)
    references = [*cache.terminal(), screen_ref]
    completion = dict(status="COMPLETED_WITH_UNKNOWNS", core_verified=True, run_id=run["run_id"],
        observation_date=str(observed), target_session=str(target), completed_at=datetime.now(timezone.utc).isoformat(),
        core_artifacts=artifacts, validation_references=references, collection_prescreen_ref=screen_ref,
        collection_prescreen_counts={key: len(old[key]) for key in ("admitted_ids", "not_prioritized_ids", "unknown_ids")})
    completion_ref = save(job / "worker-completion.json", completion)
    final = dict(schema_version=view.shared.VERSION, status=completion["status"], job=str(job),
        core_verified=True, source_terminal_binding_verified=True, external_worker_reaped=True,
        completion=completion_ref, source_reference_count=len(references), final_sealed_at=datetime.now(timezone.utc).isoformat())
    save(job / "final-control.json", final)
    return SimpleNamespace(root=root, state=state, job=job, run=run, manifest=manifest, old=old,
        calendar=calendar, lhb=lhb, capture=capture, completion=completion, final=final,
        runtime=SimpleNamespace(profile=dict(backend="premarket", state_root=str(state))),
        executor=executor, source_calls=deepcopy(executor.calls))


def reseal(saved):
    saved.final["completion"] = save(saved.job / "worker-completion.json", saved.completion)
    saved.final["source_reference_count"] = len(saved.completion["validation_references"])
    saved.final["final_sealed_at"] = datetime.now(timezone.utc).isoformat()
    save(saved.job / "final-control.json", saved.final)


def read(saved, **kwargs):
    with patch.object(view.captures.CaptureCache, "capture_one", side_effect=AssertionError("TEST_FIXTURE_NO_CAPTURE")), \
         patch.object(view.captures.CaptureCache, "_capture_new", side_effect=AssertionError("TEST_FIXTURE_NO_NEW_REQUEST")), \
         patch.object(view.Budget, "write_json", side_effect=AssertionError("TEST_FIXTURE_NO_WRITE")):
        return view.read_prescreen(saved.runtime, saved.job, allow_test_fixture=True, **kwargs)


def test_recompute_authenticates_same_originals_without_changing_saved_unknowns(saved):
    before = {str(p): p.read_bytes() for p in saved.root.rglob("*") if p.is_file()}
    output = read(saved, observation_date=saved.run["observation_date"], target_session=saved.run["target_session"])
    assert output["summary"]["counts"] == dict(ADMITTED=1, NOT_PRIORITIZED=1, UNKNOWN=0)
    assert output["summary"]["original_counts"] == dict(ADMITTED=0, NOT_PRIORITIZED=0, UNKNOWN=2)
    assert output["summary"]["collection_target_reduction"]["ratio"] == .5
    assert output["decision_cutoff"] == saved.run["decision_cutoff"]
    assert output["source_reauthenticated_now"] is True
    assert output["full_market_sources_reauthenticated"] is False
    assert output["original_sidecar_rewritten"] is False and output["strategy_executions"] == 0
    assert output["read_only_metrics"]["calls"]["requests"] == 0
    assert output["read_only_metrics"]["returned_read_bytes"]["capture"] > 0
    assert output["summary"]["lhb_original_rows"] == 1
    assert saved.executor.calls == saved.source_calls
    assert {str(p): p.read_bytes() for p in saved.root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("which", ["raw", "receipt", "result"])
def test_changed_lhb_original_fails_even_if_locator_and_old_sidecar_survive(saved, which):
    path = Path(saved.lhb.metadata[which]["path"])
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises((ValueError, RuntimeError)):
        read(saved)


def test_locator_cannot_substitute_another_receipt_or_calendar(saved):
    saved.capture["prescreen_records"][0]["receipt_id"] = "f" * 64
    save(saved.job / "capture-summary.json", saved.capture)
    with pytest.raises(ValueError, match="ORIGINAL_LHB_RECEIPT_CHANGED"):
        read(saved)


def test_original_cutoff_excludes_later_metadata_even_for_valid_saved_raw(saved):
    saved.capture["prescreen_records"][0]["completed_at"] = "2099-01-01T00:00:00+00:00"
    save(saved.job / "capture-summary.json", saved.capture)
    with pytest.raises((ValueError, RuntimeError)):
        read(saved)


def test_wrong_requested_business_date_refuses_view(saved):
    with pytest.raises(ValueError, match="OBSERVATION_SESSION_MISMATCH"):
        read(saved, observation_date="2000-01-01")


def test_unsealed_job_refuses_read_only_recomputation(saved):
    saved.final["status"] = "UNSEALED"
    save(saved.job / "final-control.json", saved.final)
    with pytest.raises(ValueError, match="SEALED_VERIFIED_CORE_REQUIRED"):
        read(saved)


def test_old_sidecar_pool_binding_cannot_be_changed_even_with_new_local_hash(saved):
    saved.old["r2_source_hash"] = "f" * 64
    ref = save(saved.job / "collection-prescreen.json", saved.old)
    saved.completion["collection_prescreen_ref"] = ref
    saved.completion["validation_references"] = [ref if r["path"] == ref["path"] else r
                                                  for r in saved.completion["validation_references"]]
    reseal(saved)
    with pytest.raises(ValueError, match="OLD_PRESCREEN_CORE_BINDING_CHANGED"):
        read(saved)


def test_current_projection_code_changed_during_view_is_rejected(saved, monkeypatch):
    # Isolated copy supplies the code reference; the implementation still runs
    # normally, and terminal verification must notice its changed bytes.
    code = saved.root / "TEST_FIXTURE_projection.py"
    code.write_bytes(b"# TEST_FIXTURE_A\n")
    original = routing.project_lhb_summary
    def project(**kwargs):
        code.write_bytes(b"# TEST_FIXTURE_B\n")
        return original(**kwargs)
    monkeypatch.setattr(routing, "__file__", str(code))
    monkeypatch.setattr(routing, "project_lhb_summary", project)
    with pytest.raises((ValueError, RuntimeError)):
        read(saved)


def test_expanded_run_bound_is_enforced_before_parsing(saved, monkeypatch):
    monkeypatch.setattr(view, "MAX_EXPANDED", 32)
    with pytest.raises(ValueError, match="DECOMPRESSED_RUN_BOUND"):
        read(saved)


def test_unusable_native_layout_refuses_locator_creation_and_keeps_state_unchanged(saved):
    loose = saved.root / "TEST_FIXTURE_loose_originals"
    loose.mkdir()
    item = saved.capture["prescreen_records"][0]
    replacements = {}
    for role in ("raw", "receipt", "result"):
        original = item[role]
        path = loose / Path(original["path"]).name
        path.write_bytes(Path(original["path"]).read_bytes())
        item[role] = workflow._ref(path)
        replacements[original["path"]] = item[role]
        saved.old["original_lhb_sources"][0][role] = item[role]
    item.update(archive_root=str(loose), verification_locator=None)
    save(saved.job / "capture-summary.json", saved.capture)
    sidecar = save(saved.job / "collection-prescreen.json", saved.old)
    replacements[sidecar["path"]] = sidecar
    saved.completion["collection_prescreen_ref"] = sidecar
    saved.completion["validation_references"] = [replacements.get(ref["path"], ref)
                                                  for ref in saved.completion["validation_references"]]
    reseal(saved)
    before = {str(p): p.read_bytes() for p in saved.state.rglob("*") if p.is_file()}
    before_paths = {str(p) for p in saved.state.rglob("*")}
    with pytest.raises(ValueError, match="EXISTING_NATIVE_LOCATOR_REQUIRED_NO_WRITE"):
        read(saved)
    assert {str(p): p.read_bytes() for p in saved.state.rglob("*") if p.is_file()} == before
    assert {str(p) for p in saved.state.rglob("*")} == before_paths


def test_runtime_binding_checks_are_in_the_read_only_meter(saved):
    original = saved.root / "TEST_FIXTURE_runtime_binding.json"
    original.write_bytes(b'"TEST_FIXTURE"')
    calls = []
    def check():
        from stockfish_core.yaogu import premarket_runtime_v1
        assert premarket_runtime_v1._ACTIVE_METER is not None
        original.read_bytes()
        calls.append(1)
    saved.runtime.check_binding = check
    output = read(saved)
    assert calls == [1, 1]
    assert output["read_only_metrics"]["returned_read_bytes"]["capture"] >= 2 * original.stat().st_size
