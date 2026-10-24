"""Offline TEST_FIXTURE only. Never open real cache, DB, or network."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest

from stockfish_core.yaogu import premarket_capture_cache_v1 as cache_module
from stockfish_core.yaogu import provider_requests

SOURCE = Path(__file__).resolve().parents[2]
if Path(provider_requests.__file__).resolve().parents[2] != SOURCE:
    raise RuntimeError("TEST_SOURCE_PACKAGE_ROOT_MISMATCH")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bundle_tool = load("TEST_FIXTURE_source_bundle", SOURCE / "scripts/build_premarket_source_bundle_v2.py")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("TEST_FIXTURE_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.fixture(scope="module")
def prepared():
    # Owned system temp reduces synchronized-directory interference.
    # Keep all seven identity assertions.
    with tempfile.TemporaryDirectory(prefix="TEST_FIXTURE_bundle_") as temporary:
        root = Path(temporary).resolve()
        result = bundle_tool.create(SOURCE, root / "runtime")
        yield root, result


@pytest.fixture
def sandbox():
    with tempfile.TemporaryDirectory(prefix="TEST_FIXTURE_partial_") as temporary:
        yield Path(temporary).resolve()


@pytest.fixture
def candidate(prepared):
    root, _ = prepared
    return load("stockfish_core.yaogu.TEST_FIXTURE_candidate_worker",
                root / "runtime" / bundle_tool.WORKER)


class StopSDK(BaseException):
    """Simulate process termination without changing Exception handling."""


class Cursor:
    error_code = "0"
    fields = ("code", "dividOperateDate", "backAdjustFactor")

    def __init__(self, code):
        self.code, self.remaining = code, 1

    def next(self):
        if self.remaining:
            self.remaining -= 1
            return True
        return False

    def get_row_data(self):
        return [self.code, "2000-01-01", "1"]


class FakeSDK:
    __version__ = "TEST_FIXTURE"

    def __init__(self, stop_query=None, stop_logout=False, failed_query=None):
        self.stop_query, self.stop_logout, self.failed_query = stop_query, stop_logout, failed_query
        self.calls = []

    def login(self):
        self.calls.append("login")
        return SimpleNamespace(error_code="0")

    def query_adjust_factor(self, *, code, start_date, end_date):
        self.calls.append(code)
        if code == self.stop_query:
            raise StopSDK("TEST_FIXTURE_TIMEOUT")
        if code == self.failed_query:
            raise ValueError("BAOSTOCK_QUERY_1001")
        assert start_date == "1990-01-01" and end_date == "2026-10-20"
        return Cursor(code)

    def logout(self):
        self.calls.append("logout")
        if self.stop_logout:
            raise StopSDK("TEST_FIXTURE_LOGOUT_TIMEOUT")


PARAMS = {"codes": ["sh.600001", "sh.600002", "sz.000001"],
          "start_date": "1990-01-01", "end_date": "2026-10-20"}


def execute_fixture_request(monkeypatch, prepared, candidate, output, sdk):
    requests = load("stockfish_core.yaogu.TEST_FIXTURE_candidate_requests",
                    prepared[0] / "runtime/stockfish_core/yaogu/provider_requests.py")
    monkeypatch.setitem(sys.modules, "baostock", sdk)
    admissions = []
    monkeypatch.setattr(candidate, "reserve_attempt", admissions.append)

    def run(argv, *, input, **kwargs):
        request = json.loads(input)
        assert argv[-1] == "stockfish_core.yaogu.provider_requests_worker"
        assert Path(kwargs["cwd"]) == prepared[0] / "runtime"
        try:
            result = candidate._baostock_session_request(request["endpoint"],
                request["parameters"], request["partial_path"])
        except StopSDK:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        result["first_seen_at"] = datetime.now(timezone.utc).isoformat()
        return SimpleNamespace(returncode=0, stdout=json.dumps(result))

    monkeypatch.setattr(requests.subprocess, "run", run)
    result = requests.execute_request("adjustment_factors", PARAMS, output, timeout=90)
    return result, admissions


class FixtureBudget:
    limits = dict(max_requests=10, max_sdk_calls=100, max_http_attempts=100,
                  request_http_attempts=64, request_timeout_seconds=90)

    def __init__(self):
        self.read_bytes = 0

    def check(self, _stage):
        assert self.read_bytes < 32 * 1024**2, "TEST_FIXTURE_READ_BOUND"

    def read(self, path, expected=None, limit=None):
        raw, reference = bundle_tool.read(Path(path), limit or 16 * 1024**2)
        self.read_bytes += len(raw)
        self.check("read")
        if expected is not None:
            assert reference["sha256"] == expected
        return raw, reference


def seed_for(output, result, producer_root):
    receipt = output / "raw" / (result["request_receipt_id"] + ".receipt.json")
    physical = json.loads(receipt.read_bytes())
    wrapper, = output.glob("request-*.json")
    # Matches the native archive grammar: ARCHIVE/raw/ATTEMPT/raw/<hash>.
    item = dict(archive_root=str(output.parent.parent), result=str(wrapper),
                raw=str(output / "raw" / (physical["content_hash"] + ".raw.json")),
                receipt=str(receipt),
                producer_files=[bundle_tool.read(producer_root / name)[1]
                                for name in cache_module.PRODUCERS])
    return item


def test_exact_source_patch_and_complete_readonly_bundle(prepared):
    root, result = prepared
    assert result["status"] == "CREATED_AND_VERIFIED_NOT_EXECUTED"
    manifest_path = root / "runtime" / bundle_tool.MANIFEST
    manifest = json.loads(manifest_path.read_bytes())
    assert manifest["capture_executed"] is False
    assert len(manifest["normalizer_hashes"]) == 9
    for relative, expected in bundle_tool.UNCHANGED.items():
        assert bundle_tool.sha((root / "runtime" / relative).read_bytes()) == expected
    assert manifest["files"][bundle_tool.WORKER]["sha256"] != bundle_tool.ORIGINAL_WORKER_SHA256
    assert {path.relative_to(SOURCE).as_posix() for path in
            (SOURCE / "stockfish_core").rglob("*.py")} <= set(manifest["files"])
    assert all(not (Path(ref["path"]).stat().st_mode & 0o222)
               for ref in manifest["files"].values())
    assert bundle_tool.verify(root / "runtime", result["manifest"]["sha256"])["snapshot_contents_unchanged"]


@pytest.mark.parametrize("mutation", [b"\n", b"# changed\n"])
def test_worker_pin_rejects_any_unreviewed_original(mutation):
    raw = (SOURCE / bundle_tool.WORKER).read_bytes()
    with pytest.raises(ValueError, match="ORIGINAL_WORKER_SHA256_MISMATCH"):
        bundle_tool.patch_worker(raw + mutation)


def test_existing_destination_and_bad_manifest_hash_rejected(prepared):
    root, _ = prepared
    with pytest.raises(ValueError, match="DESTINATION_ALREADY_EXISTS"):
        bundle_tool.create(SOURCE, root / "runtime")
    with pytest.raises(ValueError, match="EXPLICIT_MANIFEST_SHA256_MISMATCH"):
        bundle_tool.verify(root / "runtime", "0" * 64)


def test_bundle_corruption_rejected_without_touching_original(prepared, sandbox):
    # This is a fresh TEST_FIXTURE copy, never the reviewed bundle or a source.
    runtime = sandbox / "copied"
    shutil.copytree(prepared[0] / "runtime", runtime)
    with pytest.raises(ValueError, match="BUNDLE_IDENTITY_MISMATCH"):
        bundle_tool.verify(runtime, prepared[1]["manifest"]["sha256"])


@pytest.mark.parametrize("kind", ["empty", "corrupt", "old_missing_clock", "directory", "symlink", "dangling_symlink"])
def test_any_existing_factor_checkpoint_rejected_before_sdk(candidate, sandbox, monkeypatch, kind):
    target = sandbox / "worker-partial.json"
    if kind == "directory":
        target.mkdir()
    elif kind in ("symlink", "dangling_symlink"):
        other = sandbox / "other.json"
        if kind == "symlink":
            other.write_text("{}")
        target.symlink_to(other)
    else:
        target.write_text({"empty": "", "corrupt": "{bad",
                          "old_missing_clock": '{"rows":[{"code":"sh.600001"}]}' }[kind])
    sdk = FakeSDK()
    monkeypatch.setitem(sys.modules, "baostock", sdk)
    def admission(_kind):
        raise AssertionError("SDK_ADMISSION_MUST_NOT_HAPPEN")
    monkeypatch.setattr(candidate, "reserve_attempt", admission)
    with pytest.raises(ValueError, match="FACTOR_CHECKPOINT_RESUME_NOT_AUTHORIZED_V1"):
        candidate._baostock_session_request("adjustment_factors", PARAMS, target)
    assert sdk.calls == []


@pytest.mark.parametrize("stop_query,stop_logout,complete", [
    ("sh.600002", False, 1), (None, True, 3)])
def test_timeout_raw_clocks_and_exact_factor_coverage(prepared, candidate, sandbox,
        monkeypatch, stop_query, stop_logout, complete):
    started = datetime.now(timezone.utc)
    output = sandbox / "archive/raw/attempt"
    result, admissions = execute_fixture_request(monkeypatch, prepared, candidate, output,
        FakeSDK(stop_query=stop_query, stop_logout=stop_logout))
    assert result["status"] == "PARTIAL" and result["pagination_complete"] is False
    assert result["error_code"] == "REQUEST_TIMEOUT_AFTER_PARTIAL"
    assert result["coverage"]["requested_codes"] == PARAMS["codes"]
    statuses = result["coverage"]["security_statuses"]
    assert [row["code"] for row in statuses] == PARAMS["codes"]
    assert sum(row["status"] == "COMPLETE" for row in statuses) == complete
    for row in statuses[complete:]:
        assert row == dict(code=row["code"], status="UNKNOWN", row_count=0,
                           cursor_exhausted=False, reason="NOT_COMPLETED_BEFORE_CHECKPOINT")
    item = seed_for(output, result, prepared[0] / "runtime/stockfish_core/yaogu")
    payload = json.loads(Path(item["raw"]).read_bytes())
    receipt = json.loads(Path(item["receipt"]).read_bytes())
    assert payload["checkpoint_contract"] == "FACTOR_PARTIAL_CHECKPOINT_V1_NO_RESUME"
    assert payload["first_seen_at"] == receipt["first_seen_at"]
    assert started <= datetime.fromisoformat(payload["first_seen_at"]) <= datetime.fromisoformat(receipt["ingested_at"])
    envelope = cache_module.factor_contract(result)
    assert envelope["independent_complete_codes"] == PARAMS["codes"][:complete]
    assert envelope["parent_status"] == "PARTIAL"
    assert admissions and set(admissions) == {"SDK"}
    cache = cache_module.CaptureCache(sandbox / "cache", FixtureBudget())
    seed = sandbox / "TEST_FIXTURE_seed.json"
    seed.write_text(json.dumps({"sources": [item]}))
    assert cache.import_seed(seed)["captures"] == 1
    spec = cache_module.RequestSpec("adjustment_factors", PARAMS, "2026-10-20")
    saved = cache.lookup(spec)
    assert saved.status == "PARTIAL"
    assert cache.verify(saved)["rows"] == result["rows"]
    cache.terminal()
    assert not list(sandbox.rglob("*.sqlite*"))


def test_completed_fresh_request_still_complete(candidate, sandbox, monkeypatch):
    sdk = FakeSDK()
    monkeypatch.setitem(sys.modules, "baostock", sdk)
    monkeypatch.setattr(candidate, "reserve_attempt", lambda _kind: None)
    checkpoint = sandbox / "worker-partial.json"
    result = candidate._baostock_session_request("adjustment_factors", PARAMS, checkpoint)
    assert result["status"] == "COMPLETE" and result["pagination_complete"] is True
    assert cache_module.factor_contract(dict(result, parameters=PARAMS))["independent_complete_codes"] == PARAMS["codes"]
    saved = candidate.read_partial(checkpoint)
    assert saved["status"] == "PARTIAL" and saved["pagination_complete"] is False
    assert len(saved["coverage"]["security_statuses"]) == 3


def test_failure_status_and_unknown_are_not_complete(candidate, sandbox, monkeypatch):
    sdk = FakeSDK(failed_query="sh.600002", stop_query="sz.000001")
    monkeypatch.setitem(sys.modules, "baostock", sdk)
    monkeypatch.setattr(candidate, "reserve_attempt", lambda _kind: None)
    checkpoint = sandbox / "worker-partial.json"
    with pytest.raises(StopSDK):
        candidate._baostock_session_request("adjustment_factors", PARAMS, checkpoint)
    value = candidate.read_partial(checkpoint)
    assert [row["status"] for row in value["coverage"]["security_statuses"]] == [
        "COMPLETE", "FETCH_FAILED", "UNKNOWN"]
    assert "captured_at" not in value["coverage"]["security_statuses"][2]
    assert cache_module.factor_contract(dict(value, parameters=PARAMS))["independent_complete_codes"] == ["sh.600001"]


def test_old_missing_raw_clock_still_rejected_by_original_cache(sandbox):
    from stockfish_core.yaogu.providers import archive_receipt
    output = sandbox / "archive/raw/attempt"
    now = datetime.now(timezone.utc).isoformat()
    payload = dict(status="PARTIAL", rows=[dict(code="sh.600001", dividOperateDate="2000-01-01",
                   backAdjustFactor="1")], pagination_complete=False, field_version="TEST_FIXTURE")
    receipt = archive_receipt(output / "raw", "baostock", "adjustment_factors", PARAMS,
        payload, status="PARTIAL", field_version="TEST_FIXTURE", first_seen_at=now)
    result = dict(provider="baostock", endpoint="adjustment_factors", parameters=PARAMS,
        rows=payload["rows"], status="PARTIAL", pagination_complete=False, field_version="TEST_FIXTURE",
        receipts=[receipt], request_receipt_id=receipt["receipt_id"], started_at=now,
        completed_at=datetime.now(timezone.utc).isoformat(), implementation_source_hashes={
            name: bundle_tool.sha(Path(provider_requests.__file__).with_name(name).read_bytes())
            for name in cache_module.PRODUCERS})
    (output / "request-TEST_FIXTURE.json").write_text(json.dumps(result))
    item = seed_for(output, result, Path(provider_requests.__file__).parent)
    cache = cache_module.CaptureCache(sandbox / "cache", FixtureBudget())
    with pytest.raises(ValueError, match="ORIGINAL_RAW_FIRST_SEEN_REQUIRED_NOT_BACKFILLED"):
        cache._authenticate(item, item["archive_root"], item["producer_files"])


def test_old_and_new_producer_refs_coexist_without_rebinding(prepared, candidate, sandbox, monkeypatch):
    from stockfish_core.yaogu.providers import archive_receipt
    output = sandbox / "old-archive/raw/attempt"
    now = datetime.now(timezone.utc).isoformat()
    payload = dict(status="COMPLETE", rows=[dict(code="sh.600001", dividOperateDate="2000-01-01",
        backAdjustFactor="1")], pagination_complete=True, field_version="TEST_FIXTURE",
        first_seen_at=now, coverage=dict(requested_codes=PARAMS["codes"],
        security_statuses=[dict(code=code, status="COMPLETE", row_count=1 if index == 0 else 0,
                               cursor_exhausted=True) for index, code in enumerate(PARAMS["codes"])]))
    physical = archive_receipt(output / "raw", "baostock", "adjustment_factors", PARAMS, payload,
        status="COMPLETE", pagination_complete=True, field_version="TEST_FIXTURE", first_seen_at=now)
    result = dict(payload, provider="baostock", endpoint="adjustment_factors", parameters=PARAMS,
        receipts=[physical], request_receipt_id=physical["receipt_id"], started_at=now,
        completed_at=datetime.now(timezone.utc).isoformat(), implementation_source_hashes={
            name: bundle_tool.sha(Path(provider_requests.__file__).with_name(name).read_bytes())
            for name in cache_module.PRODUCERS})
    (output / "request-TEST_FIXTURE.json").write_text(json.dumps(result))
    old = seed_for(output, result, Path(provider_requests.__file__).parent)
    new_output = sandbox / "new-archive/raw/attempt"
    new_result, _ = execute_fixture_request(monkeypatch, prepared, candidate, new_output,
                                            FakeSDK(stop_logout=True))
    new = seed_for(new_output, new_result, prepared[0] / "runtime/stockfish_core/yaogu")
    cache = cache_module.CaptureCache(sandbox / "cache", FixtureBudget())
    seed = sandbox / "TEST_FIXTURE_two_sources.json"
    seed.write_text(json.dumps({"sources": [old, new]}))
    assert cache.import_seed(seed)["captures"] == 2
    spec = cache_module.RequestSpec("adjustment_factors", PARAMS, "2026-10-20")
    values = list(cache._entries(spec.key))
    assert len(values) == 2
    assert {Path(row["producer_files"][1]["path"]).parent for row in values} == {
        Path(provider_requests.__file__).parent, prepared[0] / "runtime/stockfish_core/yaogu"}
    assert all(cache.verify(row)["rows"] for row in values)
    assert cache.lookup(spec).receipt_id == new_result["request_receipt_id"]
    assert old["producer_files"] == seed_for(output, result, Path(provider_requests.__file__).parent)["producer_files"]
    cache.terminal()


def test_verify_does_not_depend_on_original_checkout_after_creation(prepared, monkeypatch):
    root, receipt = prepared
    original_read = bundle_tool.read
    def only_bundle(path, *args, **kwargs):
        assert Path(path).is_relative_to(root / "runtime"), "VERIFY_OPENED_OLD_CHECKOUT"
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(bundle_tool, "read", only_bundle)
    monkeypatch.setattr(bundle_tool, "inventory", lambda *_: pytest.fail("VERIFY_ENUMERATED_OLD_CHECKOUT"))
    result = bundle_tool.verify(root / "runtime", receipt["manifest"]["sha256"])
    assert result["current_original_source_revalidated"] is False
    assert result["source_reference_scope"] == "CREATION_TIME_SOURCE_GUARD_ONLY_NOT_LIVE_DEPENDENCY"
    assert result["snapshot_contents_unchanged"] is True


@pytest.mark.parametrize("name", ["../escaped.py", "/absolute.py", "a//b.py", "a/./b.py", "a\\b.py"])
def test_verifier_rejects_unsafe_manifest_names_before_file_access(prepared, sandbox, name):
    value = json.loads((prepared[0] / "runtime" / bundle_tool.MANIFEST).read_bytes())
    runtime = sandbox / "bad-manifest"
    runtime.mkdir()
    value["bundle_root"] = str(runtime)
    value["files"][name] = {}
    raw = bundle_tool.canonical(value)
    (runtime / bundle_tool.MANIFEST).write_bytes(raw)
    with pytest.raises(ValueError, match="SAFE_RELATIVE_BUNDLE_PATH_REQUIRED"):
        bundle_tool.verify(runtime, bundle_tool.sha(raw))


@pytest.mark.parametrize("mutation", ["extra_file", "worker_bytes", "source_symlink"])
def test_bundle_late_mutation_is_rejected(sandbox, mutation):
    runtime = sandbox / "runtime"
    created = bundle_tool.create(SOURCE, runtime)
    if mutation == "extra_file":
        (runtime / "not-registered.txt").write_text("TEST_FIXTURE")
    else:
        target = runtime / bundle_tool.WORKER
        target.chmod(0o600)
        if mutation == "worker_bytes":
            target.write_bytes(target.read_bytes() + b"\n")
        else:
            target.unlink()
            target.symlink_to(SOURCE / bundle_tool.WORKER)
    with pytest.raises(ValueError):
        bundle_tool.verify(runtime, created["manifest"]["sha256"])


def test_bundle_offline_entry_and_frozen_strategy_identity(prepared, sandbox):
    from stockfish_core.yaogu import (short_horizon_participation, saved_r2_delivery,
        saved_r2_sources_v2, saved_r2_stream_delivery_v3, industry_sina_context_v1,
        theme_evolution_streaming_v2)
    expected_r2 = short_horizon_participation.short_horizon_source_identity()
    expected_delivery = saved_r2_delivery.source_identity()
    expected_sources = saved_r2_sources_v2.source_identity()
    expected_stream_delivery = saved_r2_stream_delivery_v3.source_identity()
    expected_stream = theme_evolution_streaming_v2.source_identity()
    expected_industry = industry_sina_context_v1.source_identity()
    runtime = prepared[0] / "runtime"
    # The worker is the builder's sole authorized source patch. Industry source
    # identity includes that producer; no other runtime identity may drift.
    expected_industry['source_file_hashes'][bundle_tool.WORKER] = prepared[1]['verification']['worker_sha256']
    expected_industry['source_hash'] = industry_sina_context_v1._digest(
        {key: value for key, value in expected_industry.items() if key != 'source_hash'})
    # Child isolation prevents an already imported original package from hiding
    # a missing resource. It denies network/DB before importing product modules.
    code = r'''
import contextlib,io,json,runpy,socket,sqlite3,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()
def denied(*args,**kwargs): raise AssertionError("TEST_FIXTURE_NETWORK_OR_DB_FORBIDDEN")
socket.socket.connect=socket.socket.connect_ex=socket.create_connection=socket.getaddrinfo=denied
sqlite3.connect=denied
sys.path.insert(0,str(root))
sys.argv=[str(root/"scripts/stockfish.py"),"--help"]
text=io.StringIO()
with contextlib.redirect_stdout(text):
    try: runpy.run_path(str(root/"scripts/stockfish.py"),run_name="__main__")
    except SystemExit as error: assert error.code==0
assert "premarket" in text.getvalue()
from stockfish_core.yaogu import (short_horizon_participation,saved_r2_delivery,
    saved_r2_sources_v2,saved_r2_stream_delivery_v3,industry_sina_context_v1,
    theme_evolution_streaming_v2)
identities={"r2":short_horizon_participation.short_horizon_source_identity(),
    "delivery":saved_r2_delivery.source_identity(),
    "sources":saved_r2_sources_v2.source_identity(),
    "stream_delivery":saved_r2_stream_delivery_v3.source_identity(),
    "industry":industry_sina_context_v1.source_identity(),
    "theme_stream":theme_evolution_streaming_v2.source_identity()}
manifest=json.loads((root/"source-bundle-manifest.json").read_bytes())
for identity in identities.values():
    for name,sha in identity['source_file_hashes'].items():
        assert manifest['files'][name]['sha256']==sha
for name,module in tuple(sys.modules.items()):
    if name=="stockfish_core" or name.startswith("stockfish_core."):
        if getattr(module,"__file__",None): assert Path(module.__file__).resolve().is_relative_to(root)
print(json.dumps(identities))
'''
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(runtime)],
                            cwd=sandbox, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual == dict(r2=expected_r2, delivery=expected_delivery,
        sources=expected_sources, stream_delivery=expected_stream_delivery,
        industry=expected_industry, theme_stream=expected_stream)
    assert list(sandbox.iterdir()) == []


@pytest.mark.parametrize('resource,module', [
    ('SAVED_R2_COMPLETE_STREAM_DELIVERY_V3.md', 'saved_r2_sources_v2'),
    ('SAVED_R2_COMPLETE_STREAM_DELIVERY_V3.md', 'saved_r2_stream_delivery_v3'),
    ('INDUSTRY_SINA_CONTEXT_V1.md', 'industry_sina_context_v1'),
    ('THEME_EVOLUTION_STREAMING_V2.md', 'theme_evolution_streaming_v2'),
])
def test_native_theme_identity_requires_each_runtime_specification(sandbox, resource, module):
    # Reproduce the original omission only in a fresh owned TEST_FIXTURE tree.
    # A passing --help or import cannot replace this actual identity operation.
    runtime = sandbox / 'runtime'
    created = bundle_tool.create(SOURCE, runtime)
    target = runtime / 'docs' / resource
    assert target.read_bytes() == (SOURCE / 'docs' / resource).read_bytes()
    target.unlink()
    with pytest.raises(ValueError, match='UNREGISTERED_OR_MISSING_BUNDLE_FILE'):
        bundle_tool.verify(runtime, created['manifest']['sha256'])
    code = r'''
import importlib,socket,sqlite3,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()
def denied(*args,**kwargs): raise AssertionError('TEST_FIXTURE_NETWORK_OR_DB_FORBIDDEN')
socket.socket.connect=socket.socket.connect_ex=socket.create_connection=socket.getaddrinfo=denied
sqlite3.connect=denied
sys.path.insert(0,str(root))
module=importlib.import_module('stockfish_core.yaogu.'+sys.argv[2])
assert Path(module.__file__).resolve().is_relative_to(root)
try:
    module.source_identity()
except FileNotFoundError as error:
    assert Path(error.filename)==root/'docs'/sys.argv[3]
else:
    raise AssertionError('MISSING_RUNTIME_SPEC_WAS_ACCEPTED')
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(runtime), module, resource],
        cwd=sandbox, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("known_clock", [False, True])
def test_parent_timeout_before_worker_guard_does_not_authenticate_old_checkpoint(
        prepared, sandbox, monkeypatch, known_clock):
    # The unchanged parent can timeout before the worker's guard. Its legacy
    # fallback is NOT claimed to be removed. The original cache must reject
    # missing clocks or clocks from before this new request, without backfill.
    requests = load("stockfish_core.yaogu.TEST_FIXTURE_early_timeout_requests",
                    prepared[0] / "runtime/stockfish_core/yaogu/provider_requests.py")
    from stockfish_core.yaogu import provider_requests_worker as old_worker
    checkpoint_dir = sandbox / "preexisting"
    checkpoint = checkpoint_dir / "worker-partial.json"
    value = dict(status="PARTIAL", rows=[dict(code="sh.600001", dividOperateDate="2000-01-01",
                 backAdjustFactor="1")], pagination_complete=False, field_version="TEST_FIXTURE",
        coverage=dict(requested_codes=PARAMS["codes"], security_statuses=[
            dict(code=code, status="COMPLETE" if index == 0 else "UNKNOWN",
                 row_count=1 if index == 0 else 0, cursor_exhausted=index == 0)
            for index, code in enumerate(PARAMS["codes"])]))
    if known_clock:
        value["first_seen_at"] = "2026-01-01T00:00:00+00:00"
    old_worker.write_partial(checkpoint, value)
    before = checkpoint.read_bytes()
    def timed_out(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    monkeypatch.setattr(requests.subprocess, "run", timed_out)
    output = sandbox / "archive/raw/attempt"
    result = requests.execute_request("adjustment_factors", PARAMS, output, timeout=90,
                                     checkpoint_dir=checkpoint_dir)
    assert result["status"] == "PARTIAL" and result["pagination_complete"] is False
    item = seed_for(output, result, prepared[0] / "runtime/stockfish_core/yaogu")
    raw = json.loads(Path(item["raw"]).read_bytes())
    assert raw.get("first_seen_at") == value.get("first_seen_at")
    cache = cache_module.CaptureCache(sandbox / "cache", FixtureBudget())
    reason = "ORIGINAL_CAPTURE_CLOCK_ORDER" if known_clock else "ORIGINAL_RAW_FIRST_SEEN_REQUIRED_NOT_BACKFILLED"
    with pytest.raises(ValueError, match=reason):
        cache._authenticate(item, item["archive_root"], item["producer_files"])
    assert checkpoint.read_bytes() == before


@pytest.mark.parametrize("replacement", ["symlink", "regular", "fifo"])
def test_reader_pins_opened_file_and_cannot_block_on_swapped_fifo(sandbox, monkeypatch, replacement):
    target = sandbox / "source.txt"
    target.write_bytes(b"TEST_FIXTURE_original")
    other = sandbox / "other.txt"
    other.write_bytes(b"TEST_FIXTURE_replacement")
    original_open = os.open
    swapped = False
    def replace_then_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if Path(path) == target and not swapped:
            swapped = True
            target.unlink()
            if replacement == "symlink":
                target.symlink_to(other)
            elif replacement == "fifo":
                os.mkfifo(target)
            else:
                target.write_bytes(other.read_bytes())
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(bundle_tool.os, "open", replace_then_open)
    with pytest.raises((ValueError, OSError)):
        bundle_tool.read(target)
    assert swapped
