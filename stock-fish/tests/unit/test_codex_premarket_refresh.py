"""Independent TEST_FIXTURE refresh transactions; no real provider requests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone, time as wall_time
import json
from pathlib import Path

import pytest

from scripts import codex_premarket_refresh as refresh
from scripts import codex_premarket_continuation as continuation
from stockfish_core.yaogu import premarket_capture_cache_v1 as native
from stockfish_core.yaogu.premarket_runtime_v1 import DEFAULT_LIMITS, Budget, BudgetExceeded
from tests.yaogu import test_collection_factor_reconciliation as inherited
from tests.yaogu.test_collection_source_continuation import save, now
from tests.yaogu.test_premarket_capture_cache_v1 import FixtureExecutor


def fixture(tmp_path, monkeypatch):
    today = datetime.now(continuation.SH).date()
    target = today + timedelta(days=1)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    observed = target - timedelta(days=1)
    while observed.weekday() >= 5:
        observed -= timedelta(days=1)
    monkeypatch.setattr(inherited, "OBSERVED", str(observed))
    monkeypatch.setattr(inherited, "TARGET", str(target))
    f = inherited.completed_fixture(tmp_path)
    limits = {**DEFAULT_LIMITS, "max_seconds": 10800, "max_requests": 1024,
              "capture_read_bytes": 32 * 1024**3, "supervisor_read_bytes": 8 * 1024**3}
    original = deepcopy(limits)
    limits["max_seconds"] -= 100
    limits["max_requests"] -= 7
    limits["max_sdk_calls"] -= 9
    limits["max_http_attempts"] -= 256
    f["proposal"].update(original_limits=original, remaining_limits=limits,
        parent_provider_counts=dict(requests=7, sdk_reserved=9, http_reserved=256, sdk_actual=9, http_actual=5))
    f["proposal"]["proposal_id"] = continuation._proposal_digest(f["proposal"])
    f["claim"].update(proposal_id=f["proposal"]["proposal_id"], remaining_limits=limits)
    inherited.seal(f)
    config = dict(state_root=str(f["cache"].root.parent), capture=dict(retry_failed=False),
                  limits=limits, continuation_context=f["context"])
    config_ref = save(f["job"] / "configuration.json", config)
    metrics = Budget(limits).metrics
    metrics["elapsed_seconds"] = 10
    completion = dict(schema_version="CODEX_PREMARKET_SHARED_WORKFLOW_V1", status="COMPLETED_WITH_UNKNOWNS",
        core_verified=True, observation_date=str(observed), target_session=str(target), completed_at=now(),
        expected_sessions=f["proposal"]["expected_sessions"], metrics=metrics)
    completion_ref = save(f["job"] / "worker-completion.json", completion)
    final = json.loads((f["job"] / "final-control.json").read_bytes())
    final.update(configuration=config_ref, completion=completion_ref, final_sealed_at=now())
    final_ref = save(f["job"] / "final-control.json", final)
    f["terminal"].update(proposal_id=f["proposal"]["proposal_id"], claim_ref=f["context"]["claim"],
        receipt=dict(job=str(f["job"]), final=final_ref, completion=completion_ref), completed_at=now(),
        new_accounting=dict(worker_final_ref=final_ref, worker_completion_ref=completion_ref, worker_metrics=metrics,
            coordinator_metrics=metrics, wrapper_preparation_metrics=metrics,
            cumulative_calls=deepcopy(f["proposal"]["parent_provider_counts"])),
        parent_metrics=dict(elapsed_seconds=11.2))
    f["terminal_ref"] = save(f["slot"] / "terminal.json", f["terminal"])
    cache = native.CaptureCache(f["cache"].root, inherited.AccountingBudget(),
        config=dict(allow_test_fixture=True), executor=FixtureExecutor())
    cache.capture_one(native.RequestSpec("calendar", dict(start_date=str(target - timedelta(days=365)),
        end_date=str(target + timedelta(days=30))), str(today)))
    f["instant"] = datetime.combine(target, wall_time(9), continuation.SH)
    f["arguments"] = dict(operation_path=f["operation_ref"]["path"],
        expected_terminal_sha256=f["terminal_ref"]["sha256"], allow_test_fixture=True,
        _now_value=f["instant"].isoformat())
    return f


def callbacks(f, *, after=None):
    calls = []
    def action(limits, authorization):
        calls.append(deepcopy(authorization))
        job = f["job"].parent / "TEST_FIXTURE_refresh_job"
        job.mkdir()
        metrics = Budget(limits).metrics
        metrics["elapsed_seconds"] = 0.1
        completion = dict(status="COMPLETED_WITH_UNKNOWNS", metrics=metrics,
            expected_sessions=[*f["proposal"]["expected_sessions"][:2], f["instant"].date().isoformat()])
        completion_ref = save(job / "worker-completion.json", completion)
        completion_ref["fp"] = native._fp(job / "worker-completion.json")
        final_ref = save(job / "final-control.json", dict(job=str(job), status=completion["status"],
            completion=completion_ref, external_worker_reaped=True))
        final_ref["fp"] = native._fp(job / "final-control.json")
        receipt = dict(job=str(job), final=final_ref, completion=completion_ref, refresh_authorization=authorization)
        if after is not None:
            after(receipt)
        return receipt
    def validate(receipt, proposal):
        budget = Budget(proposal["remaining_limits"])
        # An actual independent meter catches coordinator/callback nesting.
        with budget.meter_files():
            budget.read(receipt["final"]["path"], expected=receipt["final"])
        return dict(verified=True, parent_verification_metrics=budget.metrics,
                    wrapper_preparation_metrics=Budget(proposal["remaining_limits"]).metrics)
    return calls, action, validate


def test_prepare_is_readonly_and_conservatively_debits_parent_and_deadline(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kw: (_ for _ in ()).throw(AssertionError("TEST_FIXTURE_WRITE")))
    proposal = refresh.prepare(f["runtime"], **f["arguments"])
    assert proposal["remaining_limits"]["max_seconds"] == 1440
    assert proposal["remaining_limits"]["max_requests"] == 1017
    assert proposal["budget_debits"]["old_controller_seconds_debit"] == 12
    assert proposal["budget_credit"] == 0 and proposal["retry_authorized"] is False
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_single_append_success_then_readonly_reuse_preserves_originals(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    proposal = refresh.prepare(f["runtime"], **f["arguments"])
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    calls, action, validate = callbacks(f)
    result = refresh.execute(f["runtime"], proposal, action=action, validate=validate)
    assert result["status"] == "RECORDED" and result["new_action_calls"] == 1
    assert all(path.read_bytes() == raw for path, raw in before.items())
    slot = f["slot"].parent / "refresh"
    assert {p.name for p in slot.iterdir()} == {"claim.json", "proposal.json", "terminal.json"}
    terminal_raw = (slot / "terminal.json").read_bytes()
    assert json.loads(terminal_raw)["new_accounting"]["cumulative_calls"] == f["proposal"]["parent_provider_counts"]
    reused = refresh.execute(f["runtime"], proposal, action=action, validate=validate)
    assert reused["status"] == "REUSED" and len(calls) == 1
    assert (slot / "terminal.json").read_bytes() == terminal_raw


@pytest.mark.parametrize("fault", ["action_error", "bare_claim", "forged_authorization", "late_return",
    "large_old_artifact", "new_ancestor_attempt"])
def test_failed_or_uncertain_refresh_never_repeats_action(tmp_path, monkeypatch, fault):
    f = fixture(tmp_path, monkeypatch)
    large = f["job"] / "TEST_FIXTURE_large.bin"
    if fault == "large_old_artifact":
        large.write_bytes(b"a" * (2 * 1024**2))
    proposal = refresh.prepare(f["runtime"], **f["arguments"])
    clock = [f["instant"]]
    original_now = refresh._now
    monkeypatch.setattr(refresh, "_now", lambda proposal=None, **kw:
        clock[0] if proposal is not None else original_now(**kw))
    def after(receipt):
        if fault in ("action_error", "bare_claim"):
            raise RuntimeError("TEST_FIXTURE_ACTION_FAILED")
        if fault == "forged_authorization":
            receipt["refresh_authorization"]["operation_id"] = "f" * 64
        if fault == "late_return":
            clock[0] = datetime.combine(f["instant"].date(), wall_time(9, 30), continuation.SH)
        if fault == "large_old_artifact":
            large.write_bytes(b"b" * (2 * 1024**2))
        if fault == "new_ancestor_attempt":
            save(f["unknown"].parent / "TEST_FIXTURE_extra" / "request-claim.json", {})
    calls, action, validate = callbacks(f, after=after)
    reason = {"action_error": "TEST_FIXTURE_ACTION_FAILED", "bare_claim": "TEST_FIXTURE_ACTION_FAILED",
        "forged_authorization": "RECEIPT_AUTHORIZATION_CHANGED", "late_return": "PREMARKET_DEADLINE_PASSED",
        "large_old_artifact": "PARENT_REFERENCE_CHANGED",
        "new_ancestor_attempt": "RECONCILIATION_ANCESTOR_INVENTORY_NOT_EXACT"}[fault]
    with pytest.raises((ValueError, RuntimeError), match=reason):
        refresh.execute(f["runtime"], proposal, action=action, validate=validate)
    slot = f["slot"].parent / "refresh"
    terminal = slot / "terminal.json"
    assert json.loads(terminal.read_bytes())["status"] == "FAILED_OR_UNKNOWN"
    if fault == "bare_claim":
        terminal.unlink()  # TEST_FIXTURE simulates a crash before failure seal.
    with pytest.raises(ValueError, match="CLAIM_EXISTS_NO_RETRY"):
        refresh.execute(f["runtime"], proposal, action=action, validate=validate)
    assert len(calls) == 1


@pytest.mark.parametrize("fault", ["wrong_sha", "wrong_scope", "wrong_day", "after_open", "unknown_counts", "budget_exhausted"])
def test_prepare_rejects_invalid_parent_or_closed_window_without_slot(tmp_path, monkeypatch, fault):
    f = fixture(tmp_path, monkeypatch)
    args = dict(f["arguments"])
    if fault == "wrong_sha":
        args["expected_terminal_sha256"] = "a" * 64
    elif fault == "wrong_scope":
        f["runtime"].scope = dict(f["runtime"].scope, inode=999)
    elif fault == "wrong_day":
        args["_now_value"] = (f["instant"] - timedelta(days=1)).isoformat()
    elif fault == "after_open":
        args["_now_value"] = (f["instant"] + timedelta(minutes=30)).isoformat()
    else:
        if fault == "unknown_counts":
            f["terminal"]["new_accounting"]["coordinator_metrics"]["calls"]["actual_unknown"] = 1
        else:
            f["terminal"]["parent_metrics"]["elapsed_seconds"] = 99999
        args["expected_terminal_sha256"] = save(f["slot"] / "terminal.json", f["terminal"])["sha256"]
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises((ValueError, BudgetExceeded)):
        refresh.prepare(f["runtime"], **args)
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert not (f["slot"].parent / "refresh").exists()


def test_parent_changed_between_prepare_and_claim_stops_without_action(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    proposal = refresh.prepare(f["runtime"], **f["arguments"])
    (f["job"] / "TEST_FIXTURE_added.bin").write_bytes(b"changed")
    calls, action, validate = callbacks(f)
    with pytest.raises(ValueError, match="ADMISSION_CHANGED_BEFORE_CLAIM"):
        refresh.execute(f["runtime"], proposal, action=action, validate=validate)
    assert calls == [] and not (f["slot"].parent / "refresh" / "claim.json").exists()
