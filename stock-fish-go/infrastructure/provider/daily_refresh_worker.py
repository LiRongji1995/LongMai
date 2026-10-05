"""Fixed public capture only. Never evaluates a strategy or connects to an LLM."""
import contextlib
import importlib
import importlib.util
import hashlib
from importlib import metadata
import io
import json
import socket
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "python"))


# These are the existing locked daily/rules SDK identities, not an upgrade
# request. A usable distribution must both match and execute its local path.
SDK_IDENTITIES = (("pydantic", "pydantic", "2.13.5"),
                  ("numpy", "numpy", "2.5.2"),
                  ("pandas", "pandas", "3.0.5"),
                  ("requests", "requests", "2.34.2"),
                  ("yaml", "PyYAML", "6.0.3"),
                  ("baostock", "baostock", "0.9.3"),
                  ("akshare", "akshare", "1.18.94"))


class OfflineNetworkForbidden(RuntimeError):
    pass


@contextlib.contextmanager
def no_network():
    """Even import-time SDK networking is forbidden in doctor/--check."""
    names = ("create_connection", "getaddrinfo")
    original = {name: getattr(socket, name) for name in names}
    original_socket = socket.socket

    def forbidden(*args, **kwargs):
        raise OfflineNetworkForbidden("OFFLINE_DEPENDENCY_NETWORK_FORBIDDEN")

    class OfflineSocket(original_socket):
        connect = forbidden
        connect_ex = forbidden
        sendto = forbidden

    try:
        socket.socket = OfflineSocket
        for name in names:
            setattr(socket, name, forbidden)
        yield
    finally:
        socket.socket = original_socket
        for name, value in original.items():
            setattr(socket, name, value)


def minimal_call(name, module, importer):
    if name == "pydantic":
        # A top-level package can be present with its native core absent.
        core = importer("pydantic_core._pydantic_core")
        assert core.SchemaValidator({"type": "int"}).validate_python(7) == 7
        class Probe(module.BaseModel):
            value: int
        assert Probe.model_validate({"value": 7}).model_dump() == {"value": 7}
    elif name == "numpy":
        importer("numpy._core._multiarray_umath")
        assert int(module.array([1, 2], dtype="int64").sum()) == 3
    elif name == "pandas":
        importer("pandas._libs.lib")
        assert module.DataFrame({"value": [1, 2]}).value.sum() == 3
    elif name == "requests":
        assert module.Request("GET", "https://example.invalid/offline").prepare().method == "GET"
    elif name == "yaml":
        assert module.safe_load("value: 7") == {"value": 7}
    elif name == "baostock":
        assert importer("baostock.util.stringutil").is_valid_date("2026-01-01")
    elif name == "akshare":
        assert importer("akshare.utils.demjson").decode('{"value":7}') == {"value": 7}


def dependency_check(importer=None, version_reader=None):
    importer = importer or importlib.import_module
    version_reader = version_reader or metadata.version
    records = []
    with no_network(), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        for name, distribution, expected in SDK_IDENTITIES:
            record = {"module": name, "distribution": distribution, "expected_version": expected}
            try:
                record["phase"] = "IMPORT"
                module = importer(name)
                record["phase"] = "VERSION_IDENTITY"
                actual = version_reader(distribution)
                record["actual_version"] = actual
                if actual != expected:
                    record["status"] = "VERSION_MISMATCH"
                else:
                    record["phase"] = "LOCAL_CALL"
                    minimal_call(name, module, importer)
                    record["status"] = "IMPORTED_AND_LOCAL_CALL_VERIFIED"
            except OfflineNetworkForbidden:
                record["status"] = "NETWORK_ATTEMPT_REJECTED"
            except metadata.PackageNotFoundError:
                record["status"] = "DISTRIBUTION_IDENTITY_MISSING"
            except (ModuleNotFoundError, ImportError) as exc:
                missing = getattr(exc, "name", "") or ""
                record["status"] = ("DEPENDENCY_MISSING" if missing == name else
                                    "COMPILED_EXTENSION_OR_IMPORT_DEPENDENCY_MISSING")
            except Exception:
                record["status"] = "DEPENDENCY_IMPORT_FAILED" if record["phase"] == "IMPORT" else "LOCAL_CALL_FAILED"
            records.append(record)
        if all(row["status"] == "IMPORTED_AND_LOCAL_CALL_VERIFIED" for row in records):
            record = {"module": "stockfish_core.yaogu.data_pipeline"}
            try:
                config = importer("stockfish_core.yaogu.config")
                pipeline = importer(record["module"])
                queue = importer("stockfish_core.yaogu.request_queue")
                assert config.StrategyConfig().model_dump()
                assert pipeline.stage_aware_history_sessions_required() >= 255
                assert queue.RequestBudget(max_requests=1, max_seconds=1).max_requests == 1
                record["status"] = "IMPORTED_AND_LOCAL_CALL_VERIFIED"
            except OfflineNetworkForbidden:
                record["status"] = "NETWORK_ATTEMPT_REJECTED"
            except Exception:
                record["status"] = "FIXED_PIPELINE_IMPORT_OR_LOCAL_CALL_FAILED"
            records.append(record)
    failures = [row for row in records if row["status"] != "IMPORTED_AND_LOCAL_CALL_VERIFIED"]
    return {"check_contract": "OFFLINE_ACTUAL_IMPORT_AND_LOCAL_CALL_V1",
            "status": "BLOCKED_DEPENDENCY_CHECK" if failures else "INSTALLED_NOT_ONLINE_VERIFIED",
            "network_probe": False, "network_calls_forbidden": True,
            "dependencies": records,
            "missing_dependencies": [row["module"] for row in failures if row["status"] == "DEPENDENCY_MISSING"],
            "failure_categories": sorted({row["status"] for row in failures}),
            "history_reuse": {"status": "GO_VERIFIED_TRANSPORT_READER_REQUIRES_SELECTED_BASE_PROOF",
                              "policy": "RAW_WINDOW_REUSE_ONLY_FRESH_COMPLETE_FACTOR_PROOF_AND_CURRENT_QUALIFICATION_REQUIRED"}}


def main():
    wall, mono = datetime.now(timezone.utc), time.monotonic()
    request = json.load(sys.stdin)
    if request.get("contract") != "FIXED_MAINBOARD_PUBLIC_V1":
        raise ValueError("UNREGISTERED_PROVIDER_CONTRACT")
    root = Path(request["output"])
    if not root.is_dir() or (root / "capture").exists():
        raise ValueError("NEW_CAPTURE_DIRECTORY_REQUIRED")
    checked = dependency_check()
    if checked["status"] != "INSTALLED_NOT_ONLINE_VERIFIED":
        print(json.dumps({"status": "FAILED", "reason": "DEPENDENCY_CHECK_FAILED", "dependency_check": checked}))
        raise SystemExit(1)
    bridge = None
    if request.get("go_history_base"):
        module_path = Path(__file__).with_name("go_history_bridge.py")
        module_raw = module_path.read_bytes()
        if hashlib.sha256(module_raw).hexdigest() != request.get("history_bridge_sha256"):
            raise ValueError("GO_HISTORY_BRIDGE_IMPLEMENTATION_IDENTITY_MISMATCH")
        spec = importlib.util.spec_from_file_location("stockfish_verified_go_history_bridge", module_path)
        module = importlib.util.module_from_spec(spec)
        # Execute precisely the bytes just authenticated, without resolving a
        # similarly named import from the packaged Python search path.
        exec(compile(module_raw, str(module_path), "exec"), module.__dict__)
        bridge = module.VerifiedGoHistory(root, request["go_history_base"])
    elif request.get("history_reuse"):
        reason = ("GO_V2_HISTORY_ADAPTER_UNAVAILABLE" if request["history_reuse"].get("history_present") is True else
                  "HISTORY_STATUS_NOT_INSPECTED_COLD_CAPTURE_REJECTED")
        print(json.dumps({"status": "FAILED", "reason": reason, "history_reuse": request["history_reuse"]}))
        raise SystemExit(1)
    if bridge is None and request.get("allow_cold_capture") is not True:
        print(json.dumps({"status": "FAILED", "reason": "COLD_HISTORY_CAPTURE_EXPLICIT_OPT_IN_REQUIRED"}))
        raise SystemExit(1)
    from stockfish_core.yaogu.config import StrategyConfig
    from stockfish_core.yaogu.data_pipeline import MainboardPipeline, CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL
    from stockfish_core.yaogu.request_queue import RequestBudget
    def checkpoint():
        if sum(path.stat().st_size for path in root.rglob("*") if path.is_file()) > (1 << 30):
            raise ValueError("REFRESH_ARTIFACT_LIMIT")
        waited = (datetime.now(timezone.utc) - wall).total_seconds()
        elapsed = time.monotonic() - mono
        if waited < -2 or abs(waited - elapsed) > 2:
            raise ValueError("CLOCK_OR_SUSPEND_ANOMALY")
        if max(waited, elapsed) >= 600:
            raise ValueError("REFRESH_WAIT_BUDGET_EXCEEDED")

    if bridge is not None:
        bridge.checkpoint = checkpoint
    pipeline = MainboardPipeline(root / "queue", config=StrategyConfig(),
        budget=RequestBudget(max_requests=100, max_seconds=600,
                             timeout_seconds=40, max_attempts=2, minimum_interval_seconds=.25),
        **({"normalizer": bridge.normalize, "history_coverage_plan": bridge} if bridge is not None else {}))
    # The critical path returns normalized input/readiness, before background
    # strategy preview. Its existing persistent HTTP ledger includes retries.
    report = pipeline.capture(root / "capture", datetime.now(timezone.utc),
        observation_date=date.fromisoformat(request["observation_date"]),
        purpose=CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL,
        checkpoint=checkpoint, fast_decision=False)
    checkpoint()
    raw = json.loads((root / "capture" / "market-input.json").read_text())
    cutoff = datetime.now(timezone.utc).isoformat()
    envelope = {"purpose": "PROSPECTIVE_CAPTURED", "input": raw, "cutoff": cutoff}
    frozen = root / "frozen.json"
    with frozen.open("x") as handle:
        json.dump(envelope, handle, ensure_ascii=False, allow_nan=False)
    print(json.dumps({"contract": request["contract"], "status": "COMPLETE" if report.get("actual_daily_ready") else "PARTIAL",
                      "frozen_file": "frozen.json", "started_at": wall.isoformat(), "completed_at": cutoff,
                      "elapsed_seconds": time.monotonic() - mono,
                      "report_file": "capture/capture-report.json", "ai_dependencies": [],
                      "history_reuse": bridge.audit() if bridge is not None else {**checked["history_reuse"], "cold_capture": True}}))


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        checked = dependency_check()
        print(json.dumps(checked))
        raise SystemExit(checked["status"] != "INSTALLED_NOT_ONLINE_VERIFIED")
    try:
        main()
    except Exception as error:
        # SDK details can contain secrets. Persistent receipts remain private;
        # the parent records a failure attempt, never a fictitious frozen input.
        reason = str(error).split(":", 1)[0]
        if not reason.startswith("GO_HISTORY_"):
            reason = "FIXED_CAPTURE_FAILED_OR_DEPENDENCY_MISSING"
        print(json.dumps({"status": "FAILED", "reason": reason}))
        raise SystemExit(1)
