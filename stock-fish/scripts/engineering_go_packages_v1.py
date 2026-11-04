"""Run one complete Go package partition in an independent TEST_FIXTURE CI job."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

MODULE = "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go"
GROUPS = ("research", "core", "remaining")
HEAVY = {"research": MODULE + "/application/research", "core": MODULE + "/core"}
ROOT = Path(__file__).resolve().parents[2]


def decode_package_inventory(raw):
    """Decode go list's concatenated JSON without inferring packages from filenames."""
    decoder = json.JSONDecoder()
    values = []
    offset = 0
    while offset < len(raw):
        if raw[offset].isspace():
            offset += 1
            continue
        value, offset = decoder.raw_decode(raw, offset)
        if not isinstance(value, dict) or value.get("Error") or value.get("Incomplete"):
            raise ValueError("GO_PACKAGE_DISCOVERY_ERROR")
        name = value.get("ImportPath", "")
        if (not re.fullmatch(r"[A-Za-z0-9._/-]+", name)
                or not (name == MODULE or name.startswith(MODULE + "/"))
                or any(part in {"", ".", ".."} for part in name.split("/"))):
            raise ValueError("GO_PACKAGE_NAME_REJECTED")
        tests = value.get("TestGoFiles", []) + value.get("XTestGoFiles", [])
        if not all(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.-]+_test\.go", name)
                   for name in tests) or len(tests) != len(set(tests)):
            raise ValueError("GO_TEST_FILE_INVENTORY_REJECTED")
        values.append({"package": name, "test_files": sorted(tests)})
    if not values:
        raise ValueError("GO_PACKAGE_DISCOVERY_EMPTY")
    names = [value["package"] for value in values]
    if len(names) != len(set(names)):
        raise ValueError("GO_PACKAGE_DISCOVERY_DUPLICATE")
    return sorted(values, key=lambda value: value["package"])


def validate_partition(inventory, partitions):
    names = [value["package"] for value in inventory]
    if not names or len(names) != len(set(names)) or set(partitions) != set(GROUPS):
        raise ValueError("GO_PARTITION_INVALID")
    for group, name in HEAVY.items():
        if names.count(name) != 1 or partitions[group] != [name]:
            raise ValueError("GO_HEAVY_PACKAGE_REQUIRED")
    flattened = [name for group in GROUPS for name in partitions[group]]
    if (not partitions["remaining"] or len(flattened) != len(set(flattened))
            or set(flattened) != set(names)):
        raise ValueError("GO_PARTITION_MEMBERSHIP_MISMATCH")


def make_plan(inventory, group):
    if group not in GROUPS:
        raise ValueError("GO_GROUP_REJECTED")
    partitions = {name: [package] for name, package in HEAVY.items()}
    partitions["remaining"] = [value["package"] for value in inventory
                               if value["package"] not in HEAVY.values()]
    validate_partition(inventory, partitions)
    return {"schema": "ENGINEERING_GO_PACKAGES_V1", "group": group,
            "inventory": inventory, "partitions": partitions,
            "selected_packages": partitions[group],
            "package_timeout": "30m" if group in HEAVY else "10m_GO_DEFAULT",
            "package_lifecycle": "WHOLE_PACKAGE_SINGLE_TEST_BINARY",
            "job_wall_limit_seconds": 2700,
            "all_three_jobs_allocated_limit_seconds": 8100,
            "resource_scope": "THREE_INDEPENDENT_RUNNERS_NOT_A_SINGLE_45_MINUTE_BUDGET",
            "validation_class": "TEST_FIXTURE_ENGINEERING_ONLY"}


def validate_race_events(path, plan):
    expected = {value["package"]: bool(value["test_files"]) for value in plan["inventory"]
                if value["package"] in plan["selected_packages"]}
    terminal = {}
    test_terminal_count = 0
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            if not isinstance(event, dict) or event.get("Package") not in expected:
                raise ValueError("GO_RACE_UNEXPECTED_PACKAGE_EVENT")
            action = event.get("Action")
            if action == "fail" or action == "skip" and event.get("Test"):
                raise ValueError("GO_RACE_FAILURE_OR_TEST_SKIP")
            if event.get("Test"):
                test_terminal_count += action == "pass"
            elif action in {"pass", "skip"}:
                package = event["Package"]
                if package in terminal:
                    raise ValueError("GO_RACE_DUPLICATE_PACKAGE_TERMINAL")
                if action != ("pass" if expected[package] else "skip"):
                    raise ValueError("GO_RACE_PACKAGE_TERMINAL_MISMATCH")
                terminal[package] = {"action": action, "elapsed_seconds": event.get("Elapsed")}
    if set(terminal) != set(expected):
        raise ValueError("GO_RACE_PACKAGE_TERMINAL_MISSING")
    return {"package_terminals": terminal, "passed_test_events_including_subtests": test_terminal_count,
            "no_test_packages": sorted(name for name, has_tests in expected.items() if not has_tests)}


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")


def run_command(argv, cwd, environment, output, stage, journal):
    """Stream native stdout, retain stderr separately, and never mask the exit code."""
    stdout = output / (stage + ".stdout.log")
    stderr = output / (stage + ".stderr.log")
    started = datetime.now(timezone.utc).isoformat()
    before = time.monotonic()
    record = {"stage": stage, "argv": argv, "started_at": started,
              "stdout": stdout.name, "stderr": stderr.name}
    journal.write(json.dumps({**record, "event": "started"}) + "\n")
    journal.flush()
    with stdout.open("x", encoding="utf-8") as out, stderr.open("x", encoding="utf-8") as err:
        child = subprocess.Popen(argv, cwd=cwd, env=environment, stdout=subprocess.PIPE,
                                 stderr=err, text=True, encoding="utf-8")
        assert child.stdout is not None
        try:
            for line in child.stdout:
                out.write(line)
                out.flush()
                sys.stdout.write(line)
                sys.stdout.flush()
            returncode = child.wait()
        finally:
            child.stdout.close()
    record.update(returncode=returncode, elapsed_seconds=time.monotonic() - before,
                  completed_at=datetime.now(timezone.utc).isoformat())
    journal.write(json.dumps({**record, "event": "completed"}) + "\n")
    journal.flush()
    return record


def shell_exit(returncode):
    return returncode if returncode >= 0 else 128 - returncode


def run_group(module_root, destination, group, go="go"):
    if group not in GROUPS:
        raise ValueError("GO_GROUP_REJECTED")
    module_root = module_root.resolve(strict=True)
    if os.environ.get("STOCKFISH_FORMAL_ACCEPTANCE") != "1":
        raise ValueError("GO_FORMAL_ACCEPTANCE_REQUIRED")
    oracle = Path(os.environ.get("STOCKFISH_R1_ORACLE", ""))
    if not os.environ.get("STOCKFISH_R1_ORACLE") or not oracle.is_dir():
        raise ValueError("GO_INDEPENDENT_ORACLE_REQUIRED")
    # Caller selects a fresh fixture output. A prior result is never overwritten.
    destination.mkdir(mode=0o700, exist_ok=False)
    environment = dict(os.environ, GOTOOLCHAIN="local", GOFLAGS="-mod=readonly")
    records = []
    started = time.monotonic()
    result = {"schema": "ENGINEERING_GO_EXECUTION_V1", "group": group,
              "status": "FAILED", "commands": records,
              "github_sha": os.environ.get("GITHUB_SHA"),
              "github_run_id": os.environ.get("GITHUB_RUN_ID"),
              "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT")}
    exitcode = 2
    with (destination / "execution-events.jsonl").open("x", encoding="utf-8") as journal:
        try:
            def execute(stage, args):
                record = run_command([go, *args], module_root, environment, destination, stage, journal)
                records.append(record)
                return record

            identity = execute("toolchain", ["env", "-json", "GOVERSION", "GOOS", "GOARCH", "GOTOOLCHAIN"])
            if identity["returncode"]:
                raise ValueError("GO_TOOLCHAIN_DISCOVERY_FAILED")
            toolchain = json.loads((destination / identity["stdout"]).read_text())
            if toolchain != {"GOVERSION": "go1.27.1", "GOOS": "darwin", "GOARCH": "amd64", "GOTOOLCHAIN": "local"}:
                raise ValueError("GO_REGISTERED_NATIVE_TOOLCHAIN_REQUIRED")
            discovery = execute("list", ["list", "-json", "./..."])
            if discovery["returncode"]:
                raise ValueError("GO_PACKAGE_DISCOVERY_FAILED")
            inventory = decode_package_inventory((destination / discovery["stdout"]).read_text())
            plan = make_plan(inventory, group)
            plan.update(toolchain=toolchain, github_sha=result["github_sha"],
                        go_mod_sha256=hashlib.sha256((module_root / "go.mod").read_bytes()).hexdigest(),
                        go_sum_sha256=hashlib.sha256((module_root / "go.sum").read_bytes()).hexdigest(),
                        oracle_directory=str(oracle.resolve()),
                        goflags=environment["GOFLAGS"])
            write_json(destination / "plan.json", plan)
            selected = plan["selected_packages"]
            test_args = ["test", "-race", "-p", "1", "-json", "-count=1"]
            if group in HEAVY:
                test_args.append("-timeout=30m")
            race = execute("race", [*test_args, *selected])
            exitcode = shell_exit(race["returncode"]) if race["returncode"] else 2
            # Vet is an independent check, even after race failure. The original
            # race status still takes precedence over vet or event validation.
            vet = execute("vet", ["vet", "-p", "1", "-json", *selected])
            try:
                result["race_membership"] = validate_race_events(destination / race["stdout"], plan)
            except (ValueError, OSError) as exc:
                result["race_event_error"] = str(exc)
            exitcode = (shell_exit(race["returncode"]) if race["returncode"]
                        else shell_exit(vet["returncode"]) if vet["returncode"]
                        else 2 if "race_event_error" in result else 0)
            if exitcode == 0:
                result["status"] = "COMPLETE_PACKAGE_PARTITION_RACE_AND_VET_PASSED"
        except (OSError, ValueError, TypeError) as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            result.update(exit_code=exitcode, elapsed_seconds=time.monotonic() - started)
            write_json(destination / "execution.json", result)
    print(json.dumps({"group": group, "status": result["status"], "exit_code": exitcode,
                      "elapsed_seconds": result["elapsed_seconds"]}))
    return exitcode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", required=True, choices=GROUPS)
    parser.add_argument("--module-root", type=Path, default=ROOT / "stock-fish-go")
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    return run_group(args.module_root, args.destination, args.group)


if __name__ == "__main__":
    raise SystemExit(main())
