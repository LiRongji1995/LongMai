"""Only owned TEST_FIXTURE sources/state; no network or real archive access."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import codex_factor_capture_v3 as patcher

ROOT = Path(__file__).resolve().parents[2]
PARAMS = dict(codes=["sh.600001", "sz.000001", "sh.600002"],
              start_date="1990-01-01", end_date="2026-10-28")
FIELDS = ["code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", "adjustFactor"]


class StopSDK(BaseException):
    pass


class Cursor:
    error_code = "0"
    fields = FIELDS

    def __init__(self, code, *, empty=False, error=None, row=None):
        self.code, self.empty, self.error, self.row = code, empty, error, row
        self.position = 0

    def next(self):
        self.position += 1
        if self.error == "tail" and self.position == 2:
            self.error_code = "10002007"
        return not self.empty and self.position == 1

    def get_row_data(self):
        return self.row or [self.code, "2000-01-01", "1", "1", "1"]


class SDK:
    __version__ = "TEST_FIXTURE"

    def __init__(self, *, stop=None, fail=None, empty=False, logout_failure=False, row=None):
        self.stop, self.fail, self.empty = stop, fail, empty
        self.logout_failure, self.row, self.calls = logout_failure, row, []

    def login(self):
        self.calls.append("login")
        return SimpleNamespace(error_code="0")

    def query_adjust_factor(self, *, code, start_date, end_date):
        self.calls.append(code)
        assert start_date == PARAMS["start_date"] and end_date == PARAMS["end_date"]
        if code == self.stop:
            raise StopSDK("TEST_FIXTURE_PROCESS_TIMEOUT")
        if code == self.fail:
            return SimpleNamespace(error_code="10002007")
        return Cursor(code, empty=self.empty, row=self.row)

    def logout(self):
        self.calls.append("logout")
        if self.logout_failure:
            raise ValueError("TEST_FIXTURE_LOGOUT_FAILED")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("TEST_FIXTURE_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    directory = tmp_path / "TEST_FIXTURE_runtime/stockfish_core/yaogu"
    directory.mkdir(parents=True)
    original = {}
    for name, patch in ((patcher.WORKER, patcher.patch_worker), (patcher.PARENT, patcher.patch_parent)):
        original[name] = (ROOT / name).read_bytes()
        changed, diff = patch(original[name])
        assert changed != original[name] and b"--- original/" in diff
        (directory / Path(name).name).write_bytes(changed)
    (directory / "provider_budget.py").write_bytes((ROOT / "stockfish_core/yaogu/provider_budget.py").read_bytes())
    worker = load("stockfish_core.yaogu.TEST_FIXTURE_factor_worker", directory / "provider_requests_worker.py")
    parent = load("stockfish_core.yaogu.TEST_FIXTURE_factor_parent", directory / "provider_requests.py")
    admissions = []

    def reserve(kind):
        admissions.append(kind)
        worker._attempt_counts[kind] += 1
    monkeypatch.setattr(worker, "reserve_attempt", reserve)
    yield SimpleNamespace(worker=worker, parent=parent, directory=directory, admissions=admissions,
                          root=tmp_path, original=original)
    assert all((ROOT / name).read_bytes() == raw for name, raw in original.items())
    assert not list(tmp_path.rglob("*.sqlite*"))


def install(candidate, monkeypatch, sdk, *, mutate=None):
    monkeypatch.setitem(sys.modules, "baostock", sdk)
    worker = candidate.worker

    def run(command, *, input, **kwargs):
        assert command[-1] == "stockfish_core.yaogu.provider_requests_worker"
        request = json.loads(input)
        worker._transport = request
        worker._attempt_counts = {"HTTP": 0, "SDK": 0}
        try:
            result = worker._baostock_session_request(request["endpoint"], request["parameters"], request["partial_path"])
        except StopSDK:
            if mutate:
                mutate(request)
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        except Exception as error:
            result = dict(status="FETCH_FAILED", error_code=type(error).__name__, rows=[],
                          pagination_complete=False, coverage={})
        result["http_responses"] = []
        result.setdefault("first_seen_at", datetime.now(timezone.utc).isoformat())
        result.setdefault("coverage", {}).update(http_attempts=worker._attempt_counts["HTTP"],
                                                sdk_operations=worker._attempt_counts["SDK"])
        return SimpleNamespace(returncode=0, stdout=json.dumps(result))
    monkeypatch.setattr(candidate.parent.subprocess, "run", run)


def capture(candidate, *, params=None, checkpoint_dir=None):
    return candidate.parent.execute_request("adjustment_factors", params or PARAMS,
        candidate.root / "TEST_FIXTURE_archive", timeout=90, checkpoint_dir=checkpoint_dir)


def payload(candidate, result):
    receipt = result["receipts"][-1]
    return json.loads((candidate.root / "TEST_FIXTURE_archive/raw" / (receipt["content_hash"] + ".raw.json")).read_bytes())


def test_complete_fresh_full_window_and_main_clock_preserved(candidate, monkeypatch):
    sdk = SDK()
    install(candidate, monkeypatch, sdk)
    result = capture(candidate)
    raw = payload(candidate, result)
    assert result["status"] == "COMPLETE" and result["pagination_complete"] is True
    assert sdk.calls == ["login", *PARAMS["codes"], "logout"]
    assert candidate.admissions == ["SDK"] * 5
    assert raw["first_seen_at"] == result["receipts"][-1]["first_seen_at"]
    assert result["coverage"]["sdk_operations"] == 5
    assert raw["checkpoint_binding"]["producer_hashes"] == result["implementation_source_hashes"]
    assert all(s["captured_at"] <= raw["first_seen_at"] for s in result["coverage"]["security_statuses"])


@pytest.mark.parametrize("empty", [False, True])
def test_timeout_retains_complete_neighbor_and_unknown_without_fake_clock(candidate, monkeypatch, empty):
    sdk = SDK(stop=PARAMS["codes"][1], empty=empty)
    install(candidate, monkeypatch, sdk)
    result = capture(candidate)
    assert result["status"] == "PARTIAL" and result["pagination_complete"] is False
    assert result["error_code"] == "REQUEST_TIMEOUT_AFTER_PARTIAL"
    states = result["coverage"]["security_statuses"]
    assert [s["status"] for s in states] == ["COMPLETE", "UNKNOWN", "UNKNOWN"]
    assert states[0]["row_count"] == (0 if empty else 1)
    assert all("captured_at" not in s for s in states[1:])
    assert "sdk_operations" not in result["coverage"]
    assert payload(candidate, result)["first_seen_at"] == result["receipts"][-1]["first_seen_at"]


def test_failed_security_is_once_and_successful_neighbors_retained(candidate, monkeypatch):
    sdk = SDK(fail=PARAMS["codes"][1])
    install(candidate, monkeypatch, sdk)
    result = capture(candidate)
    assert result["status"] == "PARTIAL"
    assert [s["status"] for s in result["coverage"]["security_statuses"]] == ["COMPLETE", "FETCH_FAILED", "COMPLETE"]
    assert result["coverage"]["security_statuses"][1]["error_code"] == "BAOSTOCK_ERROR_10002007"
    assert sdk.calls.count(PARAMS["codes"][1]) == 1


def test_logout_exception_recovers_complete_fragments_with_partial_parent(candidate, monkeypatch):
    install(candidate, monkeypatch, SDK(logout_failure=True))
    result = capture(candidate)
    assert result["status"] == "PARTIAL" and result["error_code"] == "WORKER_FAILED_AFTER_PARTIAL"
    assert all(s["status"] == "COMPLETE" for s in result["coverage"]["security_statuses"])
    assert result["pagination_complete"] is False


@pytest.mark.parametrize("kind", ["file", "empty", "directory", "symlink", "fifo"])
def test_existing_checkpoint_rejected_before_any_sdk_or_parent_process(candidate, monkeypatch, kind):
    root = candidate.root / "TEST_FIXTURE_old_checkpoint"
    root.mkdir()
    path = root / "worker-partial.json"
    if kind == "file":
        path.write_text('{"old":true}')
    elif kind == "empty":
        path.touch()
    elif kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        path.symlink_to(root / "missing")
    else:
        os.mkfifo(path)
    sdk = SDK()
    install(candidate, monkeypatch, sdk)
    with pytest.raises(ValueError, match="EXISTING_CHECKPOINT_FORBIDDEN"):
        capture(candidate, checkpoint_dir=root)
    with pytest.raises(ValueError, match="EXISTING_CHECKPOINT_FORBIDDEN"):
        candidate.worker._baostock_session_request("adjustment_factors", PARAMS, path)
    assert not sdk.calls and not candidate.admissions


@pytest.mark.parametrize("change", ["binding", "clock", "unknown_clock", "count", "hash", "duplicate"])
def test_timeout_refuses_corrupt_or_cross_attempt_snapshot(candidate, monkeypatch, change):
    def mutate(request):
        path = Path(request["partial_path"])
        value = json.loads(path.read_bytes())
        value.pop("checkpoint_hash")
        if change == "binding":
            value["checkpoint_binding"]["attempt_id"] = "a" * 32
        elif change == "clock":
            value["first_seen_at"] = "2000-01-01T00:00:00+00:00"
        elif change == "unknown_clock":
            value["coverage"]["security_statuses"][1]["captured_at"] = value["first_seen_at"]
        elif change == "count":
            value["coverage"]["security_statuses"][0]["row_count"] = 0
        value["checkpoint_hash"] = hashlib.sha256(candidate.worker._factor_v3_canonical(value)).hexdigest()
        if change == "hash":
            value["checkpoint_hash"] = "0" * 64
        raw = json.dumps(value)
        if change == "duplicate":
            raw = raw[:-1] + ',"first_seen_at":"2000-01-01T00:00:00+00:00"}'
        path.write_text(raw)
    install(candidate, monkeypatch, SDK(stop=PARAMS["codes"][1]), mutate=mutate)
    with pytest.raises(ValueError, match="FACTOR_V3"):
        capture(candidate)
    assert not list((candidate.root / "TEST_FIXTURE_archive").glob("raw/*.raw.json"))


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "ancestor"])
def test_safe_reader_rejects_aliases_special_files(candidate, kind):
    root = candidate.root / "TEST_FIXTURE_read"
    root.mkdir()
    source = root / "source"
    source.write_text("{}")
    target = root / "target"
    if kind == "symlink":
        target.symlink_to(source)
    elif kind == "hardlink":
        os.link(source, target)
    elif kind == "fifo":
        os.mkfifo(target)
    else:
        target.symlink_to(root, target_is_directory=True)
        target = target / "source"
    with pytest.raises((ValueError, OSError)):
        candidate.parent._factor_v3_read(target)


def test_budget_admission_failure_stops_and_preserves_previous_snapshot(candidate, monkeypatch):
    sdk = SDK()
    install(candidate, monkeypatch, sdk)
    old = candidate.worker.reserve_attempt
    def bounded(kind):
        if len(candidate.admissions) == 2:
            raise RuntimeError("TEST_FIXTURE_ADMISSION_STOP")
        old(kind)
    monkeypatch.setattr(candidate.worker, "reserve_attempt", bounded)
    result = capture(candidate)
    assert result["status"] == "PARTIAL"
    assert sdk.calls == ["login", PARAMS["codes"][0]]
    assert [s["status"] for s in result["coverage"]["security_statuses"]] == ["COMPLETE", "UNKNOWN", "UNKNOWN"]


def test_early_timeout_retains_claim_and_second_call_is_blocked(candidate, monkeypatch):
    calls = []
    def timeout(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(candidate.parent.subprocess, "run", timeout)
    result = capture(candidate)
    assert result["status"] == "FETCH_FAILED" and result["error_code"] == "REQUEST_TIMEOUT"
    assert len(list(candidate.root.rglob(".factor-v3-attempt.json"))) == 1
    assert not list(candidate.root.rglob("worker-partial.json"))
    with pytest.raises(ValueError, match="EXISTING_ATTEMPT_CLAIM_NO_RETRY"):
        capture(candidate)
    assert len(calls) == 1


def test_claim_atomic_competition_one_owner(candidate):
    from concurrent.futures import ThreadPoolExecutor
    parent = candidate.parent
    root = candidate.root / "TEST_FIXTURE_claim_competition"
    root.mkdir()
    path = root / "worker-partial.json"
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in candidate.directory.glob("*.py")}
    bindings = [parent._factor_v3_new_binding(PARAMS, hashes, datetime.now(timezone.utc)) for _ in range(2)]
    def attempt(binding):
        try:
            return parent._factor_v3_claim(path, binding)
        except ValueError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, bindings))
    assert sum(isinstance(v, dict) for v in results) == 1
    assert results.count("FACTOR_V3_EXISTING_ATTEMPT_CLAIM_NO_RETRY") == 1
    saved = json.loads((root / ".factor-v3-attempt.json").read_bytes())
    assert saved["binding"] == bindings[next(i for i, v in enumerate(results) if isinstance(v, dict))]


def test_foreign_claim_replacement_rejected_after_timeout(candidate, monkeypatch):
    def mutate(request):
        path = Path(request["partial_path"]).parent / ".factor-v3-attempt.json"
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
    install(candidate, monkeypatch, SDK(stop=PARAMS["codes"][1]), mutate=mutate)
    with pytest.raises(ValueError, match="ATTEMPT_CLAIM_CHANGED"):
        capture(candidate)


def test_checkpoint_io_failure_stops_and_keeps_last_committed_neighbor(candidate, monkeypatch):
    sdk = SDK()
    install(candidate, monkeypatch, sdk)
    original = candidate.worker._factor_v3_write
    calls = []
    def write(*args):
        calls.append(args)
        if len(calls) == 2:
            raise OSError("TEST_FIXTURE_WRITE_FAILURE")
        return original(*args)
    monkeypatch.setattr(candidate.worker, "_factor_v3_write", write)
    result = capture(candidate)
    assert result["status"] == "PARTIAL"
    assert [s["status"] for s in result["coverage"]["security_statuses"]] == ["COMPLETE", "UNKNOWN", "UNKNOWN"]
    assert sdk.calls == ["login", *PARAMS["codes"][:2], "logout"]


def test_producer_changed_during_capture_rejected(candidate, monkeypatch):
    def mutate(_request):
        path = candidate.directory / "provider_budget.py"
        path.write_bytes(path.read_bytes() + b"\n# TEST_FIXTURE changed producer\n")
    install(candidate, monkeypatch, SDK(stop=PARAMS["codes"][1]), mutate=mutate)
    with pytest.raises(ValueError, match="PRODUCER_SOURCE_CHANGED"):
        capture(candidate)


def test_old_missing_raw_clock_is_still_rejected(candidate):
    binding = candidate.parent._factor_v3_new_binding(PARAMS,
        {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in candidate.directory.glob("*.py")},
        datetime.now(timezone.utc))
    old = dict(status="PARTIAL", rows=[], pagination_complete=False, coverage={})
    with pytest.raises(ValueError, match="SNAPSHOT_BINDING_MISMATCH"):
        candidate.parent._factor_v3_validate(old, binding)


@pytest.mark.parametrize("interrupted", [False, True])
def test_original_capture_cache_authenticates_saved_wrapper_without_reclocking(candidate, monkeypatch, interrupted):
    from scripts import build_premarket_source_bundle_v2 as original_reader
    from stockfish_core.yaogu import premarket_capture_cache_v1 as cache_module
    install(candidate, monkeypatch, SDK(stop=PARAMS["codes"][1] if interrupted else None))
    result = capture(candidate)
    output = candidate.root / "TEST_FIXTURE_archive"
    wrapper, = output.glob("request-*.json")
    assert json.loads(wrapper.read_bytes()) == result
    receipt = result["receipts"][-1]
    sources = dict(archive_root=str(output), result=str(wrapper),
        raw=str(output / "raw" / (receipt["content_hash"] + ".raw.json")),
        receipt=str(output / "raw" / (receipt["receipt_id"] + ".receipt.json")),
        producer_files=[original_reader.read(candidate.directory / name)[1] for name in cache_module.PRODUCERS])
    seed = candidate.root / "TEST_FIXTURE_seed.json"
    seed.write_text(json.dumps(dict(sources=[sources])))
    class Budget:
        limits = dict(max_requests=10, max_sdk_calls=100, max_http_attempts=100,
                      request_http_attempts=64, request_timeout_seconds=90)
        def check(self, _stage):
            pass
        def read(self, path, expected=None, limit=None):
            raw, ref = original_reader.read(Path(path), limit or 16 * 1024**2)
            assert expected is None or ref["sha256"] == expected
            return raw, ref
    cache = cache_module.CaptureCache(candidate.root / "TEST_FIXTURE_cache", Budget())
    assert cache.import_seed(seed)["captures"] == 1
    record = cache.lookup(cache_module.RequestSpec("adjustment_factors", PARAMS, PARAMS["end_date"]))
    assert cache.verify(record) == result
    assert record.status == ("PARTIAL" if interrupted else "COMPLETE")
    cache.terminal()


def test_source_change_during_archive_publication_fails_terminal_guard(candidate, monkeypatch):
    install(candidate, monkeypatch, SDK())
    original = candidate.parent.archive_receipt
    def archive(*args, **kwargs):
        result = original(*args, **kwargs)
        path = candidate.directory / "provider_budget.py"
        path.write_bytes(path.read_bytes() + b"\n# TEST_FIXTURE publication change\n")
        return result
    monkeypatch.setattr(candidate.parent, "archive_receipt", archive)
    with pytest.raises(ValueError, match="PRODUCER_SOURCE_CHANGED"):
        capture(candidate)
    # Rejected output remains evidence; no native cache commit was performed.
    assert list((candidate.root / "TEST_FIXTURE_archive/raw").glob("*.raw.json"))


def test_patches_are_exact_and_never_accept_already_patched_sources(candidate):
    for name, patch in ((patcher.WORKER, patcher.patch_worker), (patcher.PARENT, patcher.patch_parent)):
        with pytest.raises(ValueError, match="ORIGINAL_PRODUCER_SHA_MISMATCH"):
            patch((candidate.directory / Path(name).name).read_bytes())


def test_worker_main_retains_supplied_first_seen(candidate, monkeypatch):
    # This directly exercises patched main's timestamp assignment, not the fixture shim.
    seen = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    monkeypatch.setattr(candidate.worker, "baostock_request", lambda *_args: dict(
        status="PARTIAL", rows=[], first_seen_at=seen, pagination_complete=False, coverage={}))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(dict(provider="baostock",
        endpoint="adjustment_factors", parameters=PARAMS))))
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    candidate.worker.main()
    assert json.loads(stream.getvalue())["first_seen_at"] == seen
