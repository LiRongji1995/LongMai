"""Bounded TEST_FIXTURE benchmark for fresh-process reuse, not market speed.

Run with the selected Python: python scripts/benchmark_codex_chat_fixture.py
Everything is created in an isolated temporary directory and removed afterward.
The fake database is an 8 MiB byte fixture, never opened as SQLite. A deliberate
0.2 second wait stands in for computation; no provider or service is contacted.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.codex_workspace import PROFILE_VERSION, load_runtime, register_profile


VERSION = "CODEX_CHAT_BENCHMARK_TEST_FIXTURE_V1"
FIXTURE_BYTES = 8 * 1024 * 1024
SIMULATED_COMPUTE_SECONDS = 0.2
NAME = "TEST_FIXTURE"


def _no_network(event, args):
    if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "socket.bind"}:
        raise RuntimeError("TEST_FIXTURE_NETWORK_FORBIDDEN")


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _copy(source, destination):
    # Exclusive creation: an unexpected prior result is never overwritten.
    with source.open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer, length=1024 * 1024)
        writer.flush()
        os.fsync(writer.fileno())


def _inventory(path):
    logical, allocated, files = 0, 0, 0
    if not path.exists():
        return {"logical_bytes": 0, "allocated_bytes": 0, "files": 0}
    for item in path.rglob("*"):
        value = item.lstat()
        if stat.S_ISREG(value.st_mode):
            logical += value.st_size
            allocated += value.st_blocks * 512
            files += 1
        elif not stat.S_ISDIR(value.st_mode):
            raise RuntimeError("TEST_FIXTURE_UNEXPECTED_NONREGULAR_ENTRY")
    return {"logical_bytes": logical, "allocated_bytes": allocated, "files": files}


def _worker(fixture_root, mode, iteration):
    root = Path(fixture_root).resolve(strict=True)
    if (not root.name.startswith("codex-chat-TEST_FIXTURE-")
            or (root / "TEST_FIXTURE.marker").read_text(encoding="utf-8") != VERSION
            or mode not in {"baseline", "shared"} or iteration not in {1, 2}):
        raise RuntimeError("ISOLATED_TEST_FIXTURE_REQUIRED")
    source = root / "runtime" / "TEST_FIXTURE_NOT_SQLITE.bin"
    expected_hash = _hash(source)
    if source.stat().st_size != FIXTURE_BYTES:
        raise RuntimeError("TEST_FIXTURE_SIZE_CHANGED")
    simulated_calls = 0
    started = time.perf_counter()

    def simulated_compute(output):
        nonlocal simulated_calls
        simulated_calls += 1
        time.sleep(SIMULATED_COMPUTE_SECONDS)
        _copy(source, output)

    def validate_file(output):
        if output.stat().st_size != FIXTURE_BYTES or _hash(output) != expected_hash:
            raise RuntimeError("TEST_FIXTURE_OUTPUT_NOT_EQUIVALENT")

    if mode == "baseline":
        destination = root / "baseline" / str(iteration)
        destination.mkdir()
        _copy(source, destination / "TEST_FIXTURE-database-copy.bin")
        output = destination / "TEST_FIXTURE-output.bin"
        simulated_compute(output)
        validate_file(output)
        status, database_copy_bytes, action_calls = "EXECUTED", FIXTURE_BYTES, 1
    else:
        runtime = load_runtime(NAME, root / "registry")
        if runtime is None:
            raise RuntimeError("TEST_FIXTURE_RUNTIME_NOT_REGISTERED")
        output = root / "runtime" / "reports" / "TEST_FIXTURE-output.bin"

        def action():
            simulated_compute(output)
            return {"fixture_schema": VERSION, "output_name": output.name,
                    "bytes": FIXTURE_BYTES, "sha256": expected_hash}

        def validate(receipt):
            if receipt != {"fixture_schema": VERSION, "output_name": output.name,
                           "bytes": FIXTURE_BYTES, "sha256": expected_hash}:
                raise RuntimeError("TEST_FIXTURE_RECEIPT_CHANGED")
            validate_file(output)
            return {"verified": True, "fixture_schema": VERSION}

        result = runtime.run_once({"fixture_schema": VERSION, "source_sha256": expected_hash},
                                  action, validate)
        status, database_copy_bytes, action_calls = result["status"], 0, result["new_action_calls"]
    return {"fixture_schema": VERSION, "mode": mode, "iteration": iteration,
            "process_id": os.getpid(), "status": status, "action_calls": action_calls,
            "simulated_compute_wait_calls": simulated_calls,
            "simulated_compute_wait_seconds": simulated_calls * SIMULATED_COMPUTE_SECONDS,
            "database_copy_bytes": database_copy_bytes, "provider_calls": 0,
            "output_verified": True, "worker_seconds": round(time.perf_counter() - started, 6)}


def _fresh_process(root, mode, iteration):
    started = time.perf_counter()
    completed = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                                "_worker", str(root), mode, str(iteration)],
                               cwd=root, capture_output=True, text=True, timeout=30, check=False)
    elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        raise RuntimeError("TEST_FIXTURE_CHILD_FAILED_" + str(completed.returncode))
    result = json.loads(completed.stdout)
    if result.get("fixture_schema") != VERSION or not result.get("output_verified"):
        raise RuntimeError("TEST_FIXTURE_CHILD_RESULT_INVALID")
    result["wall_seconds_including_process_start"] = round(elapsed, 6)
    return result


def benchmark():
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="codex-chat-TEST_FIXTURE-") as temporary:
        root = Path(temporary).resolve(strict=True)
        (root / "TEST_FIXTURE.marker").write_text(VERSION, encoding="utf-8")
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        (runtime_root / "reports").mkdir()
        (root / "baseline").mkdir()
        source = runtime_root / "TEST_FIXTURE_NOT_SQLITE.bin"
        block = (b"TEST_FIXTURE_NOT_REAL_MARKET_DATA_NOT_SQLITE\n" * 2048)[:65536]
        with source.open("xb") as stream:
            remaining = FIXTURE_BYTES
            while remaining:
                piece = block[:remaining]
                stream.write(piece)
                remaining -= len(piece)
            stream.flush()
            os.fsync(stream.fileno())
        source_identity = (source.stat().st_ino, source.stat().st_size, _hash(source))
        profile = {"schema_version": PROFILE_VERSION, "name": NAME, "backend": "api",
                   "state_root": str(runtime_root), "report_root": str(runtime_root / "reports"),
                   "db_path": str(source), "base_url": "http://127.0.0.1:8000",
                   "storage": {"roots": [{"name": "TEST_FIXTURE-runtime", "path": str(runtime_root),
                                           "role": "runtime"}],
                               "target_root": "TEST_FIXTURE-runtime", "expected_growth_bytes": FIXTURE_BYTES,
                               "min_free_bytes": 32 * 1024 * 1024, "max_managed_bytes": 64 * 1024 * 1024}}
        profile_path = root / "TEST_FIXTURE-profile.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        register_profile(profile_path, root / "registry")
        control_before = _inventory(root / "registry")
        baseline = [_fresh_process(root, "baseline", iteration) for iteration in (1, 2)]
        shared = [_fresh_process(root, "shared", iteration) for iteration in (1, 2)]
        control_after = _inventory(root / "registry")
        baseline_bytes = _inventory(root / "baseline")
        shared_output_bytes = _inventory(runtime_root / "reports")
        control_growth = {key: control_after[key] - control_before[key]
                          for key in ("logical_bytes", "allocated_bytes", "files")}
        if source_identity != (source.stat().st_ino, source.stat().st_size, _hash(source)):
            raise RuntimeError("TEST_FIXTURE_BASE_CHANGED")
        if (sum(row["action_calls"] for row in baseline) != 2
                or sum(row["action_calls"] for row in shared) != 1
                or shared[1]["status"] != "REUSED"
                or len({row["process_id"] for row in [*baseline, *shared]}) != 4
                or baseline_bytes["logical_bytes"] != 4 * FIXTURE_BYTES
                or shared_output_bytes["logical_bytes"] != FIXTURE_BYTES):
            raise RuntimeError("TEST_FIXTURE_REUSE_INVARIANT_FAILED")
        baseline_wall = sum(row["wall_seconds_including_process_start"] for row in baseline)
        shared_wall = sum(row["wall_seconds_including_process_start"] for row in shared)
        result = {"schema_version": VERSION, "validation_kind": "ISOLATED_ENGINEERING_TEST_FIXTURE",
                  "python_version": sys.version.split()[0], "network_forbidden": True, "provider_calls": 0,
                  "fixture_directory_is_temporary": True, "business_data_opened": False,
                  "common_base_bytes_excluded_from_savings": FIXTURE_BYTES, "common_base_preserved": True,
                  "simulated_compute_wait_seconds_per_action": SIMULATED_COMPUTE_SECONDS,
                  "timing_scope": "Two sequential fresh processes per mode; includes startup, copy, artificial wait, validation and coordination; excludes fixture setup and one-time registration",
                  "baseline": {"runs": baseline, "generated_files": baseline_bytes,
                               "wall_seconds": round(baseline_wall, 6), "action_calls": 2},
                  "shared": {"runs": shared, "output_files": shared_output_bytes,
                             "initial_registration_files": control_before,
                             "total_coordination_files": control_after,
                             "input_profile_logical_bytes": profile_path.stat().st_size,
                             "coordination_growth": control_growth,
                             "wall_seconds": round(shared_wall, 6), "action_calls": 1},
                  "savings": {"database_copy_logical_bytes_avoided": 2 * FIXTURE_BYTES,
                              "duplicate_output_logical_bytes_avoided": FIXTURE_BYTES,
                              "net_generated_logical_bytes_avoided": baseline_bytes["logical_bytes"] -
                                  shared_output_bytes["logical_bytes"] - control_growth["logical_bytes"],
                              "net_generated_allocated_bytes_avoided": baseline_bytes["allocated_bytes"] -
                                  shared_output_bytes["allocated_bytes"] - control_growth["allocated_bytes"],
                              "net_logical_bytes_avoided_including_registration_and_profile": baseline_bytes["logical_bytes"] -
                                  shared_output_bytes["logical_bytes"] - control_after["logical_bytes"] - profile_path.stat().st_size,
                              "incremental_space_scope": "net_generated metrics exclude the separately reported one-time registration and input profile",
                              "wall_seconds": round(baseline_wall - shared_wall, 6),
                              "wall_percent": round(100 * (baseline_wall - shared_wall) / baseline_wall, 3)},
                  "limitations": ["Artificial wait is not a measured strategy or provider workload.",
                                  "Single local filesystem sample; no claim about full-market or live-runtime speed.",
                                  "Allocated blocks are metadata measurements, not proven reclaimable APFS space.",
                                  "Temporary fixture outputs are removed; no persistent artifact path is advertised."]}
    result["total_benchmark_seconds"] = round(time.perf_counter() - started, 6)
    return result


def main():
    sys.addaudithook(_no_network)
    if len(sys.argv) == 5 and sys.argv[1] == "_worker":
        result = _worker(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    elif len(sys.argv) == 1:
        result = benchmark()
    else:
        raise SystemExit("Usage: python scripts/benchmark_codex_chat_fixture.py")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
