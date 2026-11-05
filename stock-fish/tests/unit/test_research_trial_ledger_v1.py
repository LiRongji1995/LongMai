"""TEST_FIXTURE immutable cross-plan attempt ledger, including stale writers."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts import research_trial_ledger_v1 as ledger


def attempt(name="TEST_FIXTURE_A"):
    return dict(attempt_id=name, configuration_hash="a" * 64, status="PLANNED",
                registered_at="2026-01-01T00:00:00Z", result_hash=None)


@pytest.fixture
def root(tmp_path):
    path = (tmp_path / "TEST_FIXTURE_ledger").resolve()
    ledger.initialize(path, "TEST_FIXTURE_scope")
    return path


def test_cross_plan_cannot_drop_prior_failed_attempt(root):
    initial = ledger.inspect(root)
    a = ledger.append_attempt(root, attempt(), expected_head=initial["head"])
    failed = ledger.record_result(root, "TEST_FIXTURE_A", "FAILED", "b" * 64, expected_head=a["content_hash"])
    b = ledger.append_attempt(root, attempt("TEST_FIXTURE_B"), expected_head=failed["content_hash"])
    with pytest.raises(ValueError, match="ALL_SCOPE_ATTEMPTS"):
        ledger.register_plan(root, "TEST_FIXTURE_NEW_PLAN", "c" * 64, [attempt("TEST_FIXTURE_B")], expected_head=b["content_hash"], active_attempt_id="TEST_FIXTURE_B")
    current = ledger.inspect(root)
    event = ledger.register_plan(root, "TEST_FIXTURE_NEW_PLAN", "c" * 64, current["attempts"], expected_head=current["head"], active_attempt_id="TEST_FIXTURE_B")
    assert event["sequence"] == 4
    assert ledger.inspect(root)["attempts"][0]["status"] == "FAILED"
    assert ledger.inspect(root)["offline_or_other_scope_attempts"] == "UNKNOWN"


def test_stale_head_and_duplicate_registration_never_retry(root):
    head = ledger.inspect(root)["head"]
    a = ledger.append_attempt(root, attempt(), expected_head=head)
    with pytest.raises(ValueError, match="STALE_HEAD"):
        ledger.append_attempt(root, attempt("TEST_FIXTURE_B"), expected_head=head)
    with pytest.raises(ValueError, match="ALREADY_REGISTERED"):
        ledger.append_attempt(root, attempt(), expected_head=a["content_hash"])
    assert ledger.inspect(root)["event_count"] == 1


def test_terminal_result_append_does_not_rewrite_original(root):
    a = ledger.append_attempt(root, attempt(), expected_head=ledger.inspect(root)["head"])
    raw = (root / "000001.json").read_bytes()
    b = ledger.record_result(root, "TEST_FIXTURE_A", "COMPLETED", "c" * 64, expected_head=a["content_hash"])
    assert (root / "000001.json").read_bytes() == raw
    with pytest.raises(ValueError, match="TERMINAL_ATTEMPT"):
        ledger.record_result(root, "TEST_FIXTURE_A", "FAILED", "d" * 64, expected_head=b["content_hash"])


@pytest.mark.parametrize("fault", ["gap", "changed", "symlink", "unrecognized", "permissions"])
def test_tampered_or_incomplete_chain_fails_closed(root, fault, tmp_path):
    ledger.append_attempt(root, attempt(), expected_head=ledger.inspect(root)["head"])
    event = root / "000001.json"
    if fault == "gap": event.rename(root / "000002.json")
    elif fault == "changed":
        value = json.loads(event.read_text())
        value["payload"]["status"] = "UNKNOWN"
        event.write_text(json.dumps(value))
    elif fault == "symlink":
        other = tmp_path / "TEST_FIXTURE_other"
        event.rename(other)
        event.symlink_to(other)
    elif fault == "unrecognized": (root / "crashed.tmp").write_text("TEST_FIXTURE")
    else: event.chmod(0o644)
    with pytest.raises((ValueError, OSError)):
        ledger.inspect(root)


def test_failed_publication_does_not_create_retryable_empty_slot(root, monkeypatch):
    original = ledger.io.publish_new
    def failed(path, value, **kwargs):
        if Path(path).name == "000001.json":
            raise OSError("TEST_FIXTURE_IO_FAILURE")
        return original(path, value, **kwargs)
    monkeypatch.setattr(ledger.io, "publish_new", failed)
    head = ledger.inspect(root)["head"]
    with pytest.raises(OSError):
        ledger.append_attempt(root, attempt(), expected_head=head)
    assert ledger.inspect(root)["event_count"] == 0


def test_new_scope_is_explicit_not_a_global_completeness_claim(root, tmp_path):
    ledger.append_attempt(root, attempt(), expected_head=ledger.inspect(root)["head"])
    other = (tmp_path / "TEST_FIXTURE_other_scope").resolve()
    report = ledger.initialize(other, "TEST_FIXTURE_second")
    assert report["attempts"] == []
    assert report["scope"]["completeness"] == "SELECTED_LOCAL_SCOPE_ONLY"
    assert report["external_preregistration"] is False


def test_concurrent_writers_same_head_have_one_winner(root):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(2)
    head = ledger.inspect(root)["head"]
    def writer(name):
        barrier.wait(timeout=10)
        try:
            ledger.append_attempt(root, attempt(name), expected_head=head)
            return "APPENDED"
        except ValueError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(writer, ["TEST_FIXTURE_A", "TEST_FIXTURE_B"]))
    assert outcomes.count("APPENDED") == 1
    assert outcomes.count("LEDGER_STALE_HEAD_NO_AUTOMATIC_RETRY") == 1
    assert ledger.inspect(root)["event_count"] == 1
