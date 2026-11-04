"""Select isolated engineering gates from Git paths; no business or deployment actions."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
DAILY_TESTS = (
    "tests/unit/test_engineering_ci_scope_v1.py",
    "tests/unit/test_release_profile_manifest_v1.py",
    "tests/unit/test_recovery_drill_v1.py",
    "tests/unit/test_codex_operational_report_v1.py",
    "tests/unit/test_benchmark_artifact_layout_v1.py",
    "tests/unit/test_codex_research_protocol_v1.py",
    "tests/contracts/test_http_security_capacity_v1.py",
    "tests/contracts/test_tushare_transport_security_v1.py",
    "tests/integration/test_health_contract_separation.py",
    "tests/risk/test_deterministic_risk.py",
    "tests/unit/test_research_contract.py",
    "tests/r02_correctness/test_backup_restore.py",
    "tests/unit/test_codex_sdk_tree_supervisor_v2.py",
    "tests/unit/test_sdk_tree_supervisor_v2_reference.py",
    "tests/unit/test_codex_sdk_tree_supervisor_v3.py",
    "tests/unit/test_sdk_tree_supervisor_v3_reference.py",
    "tests/unit/test_codex_supervised_premarket_v3.py",
    "tests/unit/test_supervised_premarket_v3_reference.py",
    "tests/unit/test_codex_metered_bundle_v2.py",
    "tests/unit/test_codex_metered_bundle_v1.py",
)


def classify(paths):
    result = {"daily": False, "go": False, "go_core": False, "live": False}
    tests = set(DAILY_TESTS)
    for raw in paths:
        path = PurePosixPath(raw)
        if path.is_absolute() or ".." in path.parts or "\\" in raw or "\n" in raw or "\r" in raw:
            raise ValueError("CI_PATH_REJECTED")
        name = path.name.lower()
        if (name == ".env" or name.startswith(".env.") and not name.endswith((".example", ".template", ".sample"))
                or name.endswith((".sqlite", ".sqlite3", ".db", ".pem", ".key", ".pkl", ".pth"))
                or any(part in {".venv", "site-packages", "node_modules"} for part in path.parts)):
            raise ValueError("CI_PRIVATE_OR_RUNTIME_FILE_REJECTED:" + raw)
        if raw.startswith(".github/"):
            result = dict.fromkeys(result, True)
        if raw.startswith("stock-fish/") and not raw.endswith(".md"):
            result["daily"] = True
        if raw.startswith("stock-fish-go/"):
            result["go"] = result["daily"] = True
        if raw.startswith("go-core/"):
            result["go_core"] = True
        if raw.startswith("stockfish-live/"):
            result["live"] = True
        if raw.startswith("stock-fish/tests/") and path.name.startswith("test_") and path.suffix == ".py":
            tests.add(raw.removeprefix("stock-fish/"))
        # A Python strategy or evidence change must exercise the independent Go oracle.
        if raw.startswith(("stock-fish/stockfish_core/yaogu/", "stock-fish/config/stage_aware", "stock-fish/config/research_r1")):
            result["go"] = True
    return {"schema": "ENGINEERING_CI_SCOPE_V1", "components": result,
            "daily_tests": sorted(tests), "validation_class": "TEST_FIXTURE_ENGINEERING_ONLY",
            "deployment_authorized": False, "legacy_full_suite": "SEPARATE_NOT_CLAIMED"}


def changed_paths(root, base, head):
    if not re.fullmatch(r"[0-9a-f]{40}", head or "") or not re.fullmatch(r"[0-9a-f]{40}", base or ""):
        raise ValueError("CI_COMMIT_SHA_REQUIRED")
    argv = ["git", "ls-tree", "-r", "--name-only", "-z", head] if base == "0" * 40 else ["git", "diff", "--name-only", "--diff-filter=ACMRD", "-z", base, head, "--"]
    return [value.decode("utf-8") for value in subprocess.check_output(argv, cwd=root).split(b"\0") if value]


def run_tests(scope, root, basetemp, report):
    if scope.get("schema") != "ENGINEERING_CI_SCOPE_V1":
        raise ValueError("CI_SCOPE_SCHEMA_REQUIRED")
    selected = scope["daily_tests"]
    if not set(DAILY_TESTS).issubset(selected):
        raise ValueError("CI_MANDATORY_GATES_MISSING")
    for relative in selected:
        path = PurePosixPath(relative)
        if (path.is_absolute() or ".." in path.parts or not relative.startswith("tests/")
                or path.suffix != ".py" or not (root / "stock-fish" / relative).is_file()):
            raise ValueError("CI_TEST_PATH_REJECTED")
    if basetemp.exists() or report.exists():
        raise ValueError("CI_OUTPUT_MUST_BE_NEW")
    return subprocess.call([sys.executable, "-I", "-B", str(root / "stock-fish/scripts/run_isolated_tests.py"),
                            *selected, "-q", "--basetemp", str(basetemp), "--junitxml", str(report)], cwd=root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--run-scope", type=Path)
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument("--basetemp", type=Path)
    parser.add_argument("--junitxml", type=Path)
    args = parser.parse_args(argv)
    if args.run_live:
        if args.basetemp is None or args.junitxml is None:
            parser.error("--run-live requires explicit new --basetemp and --junitxml")
        if args.basetemp.exists() or args.junitxml.exists():
            raise ValueError("CI_OUTPUT_MUST_BE_NEW")
        runpy.run_path(str(args.root / "stock-fish/scripts/bootstrap_daily_runtime.py"))["installed_identity"]()
        os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        sys.path.insert(0, str(args.root / "stockfish-live"))
        os.chdir(args.root / "stockfish-live")
        import pytest
        return pytest.main(["-p", "no:cacheprovider", "tests", "-q", "--basetemp", str(args.basetemp), "--junitxml", str(args.junitxml)])
    if args.run_scope:
        if args.basetemp is None or args.junitxml is None:
            parser.error("--run-scope requires explicit new --basetemp and --junitxml")
        return run_tests(json.loads(args.run_scope.read_bytes()), args.root, args.basetemp, args.junitxml)
    if not re.fullmatch(r"[0-9a-f]{40}", args.base or "") or not re.fullmatch(r"[0-9a-f]{40}", args.head or ""):
        raise ValueError("CI_COMMIT_SHA_REQUIRED")
    # Manual dispatch has no diff base. Run all component gates without relabeling
    # retained upstream assets/tests as newly published private data or new tests.
    paths = [] if args.base == "0" * 40 else changed_paths(args.root, args.base, args.head)
    for relative in ("THIRD_PARTY_NOTICES.md", "stock-fish/LICENSE", "qlib/LICENSE", "stock-fish/MiroFish/LICENSE"):
        if not (args.root / relative).is_file():
            raise ValueError("CI_REQUIRED_LICENSE_MISSING:" + relative)
    value = classify(paths if args.base != "0" * 40 else [".github/workflows/engineering-gates.yml"])
    value["private_path_check"] = "CHANGED_PATHS_CHECKED" if args.base != "0" * 40 else "NOT_CHECKED_NO_DIFF_BASE"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            for name, enabled in value["components"].items():
                stream.write(name + "=" + str(enabled).lower() + "\n")
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
