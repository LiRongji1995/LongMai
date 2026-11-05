"""Dependency-directed engineering gates; the frozen V1 safety floor is retained."""
from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path, PurePosixPath
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
_legacy = runpy.run_path(str(Path(__file__).with_name("engineering_ci_scope_v1.py")))
VERSION = "ENGINEERING_CI_SCOPE_V2"
# These regressions are compulsory even when only another implementation changes.
MANDATORY = tuple(_legacy["DAILY_TESTS"]) + (
    "tests/unit/test_engineering_ci_scope_v2.py",
    "tests/unit/test_business_authority_v1.py",
    "tests/unit/test_codex_runtime_recovery_v2.py",
    "tests/unit/test_codex_research_protocol_v2.py",
    "tests/unit/test_active_runtime_v1.py",
    "tests/unit/test_active_gateway_security_v1.py",
    "tests/unit/test_active_service_host_v1.py",
    "tests/unit/test_active_delivery_bridge_v1.py",
    "tests/unit/test_active_identity_v2.py",
    "tests/unit/test_active_source_bootstrap_v1.py",
    "tests/unit/test_active_outcome_pipeline_v1.py",
    "tests/unit/test_active_outcome_input_v1.py",
    "tests/unit/test_active_environment_acceptance_v1.py",
    "tests/unit/test_active_read_control_v1.py",
    "tests/unit/test_daily_outcome_authority_v1.py",
    "tests/unit/test_codex_active_v1.py",
)

# Groups select entire test modules, never just a convenient individual assertion.
GROUPS = {
    "entry": ("tests/unit/test_codex_daily*.py", "tests/unit/test_codex_workspace*.py",
              "tests/unit/test_codex_premarket*.py", "tests/yaogu/test_premarket*workflow*.py",
              "tests/yaogu/test_premarket_native_core*.py", "tests/yaogu/test_premarket_presentation*.py",
              "tests/yaogu/test_premarket_news*.py"),
    "source": ("tests/r03_sources/test_*.py", "tests/r02_sources/test_*.py",
               "tests/contracts/test_*transport*.py", "tests/yaogu/test_premarket*source*.py",
               "tests/yaogu/test_dated_identity*.py", "tests/r05_product/test_daily_primary*.py",
               "tests/yaogu/test_premarket_capture_cache*.py", "tests/unit/test_premarket_source_bundle*.py",
               "tests/unit/test_codex_current_calendar*.py", "tests/unit/test_codex_core_sources.py",
               "tests/unit/test_codex_latest_capture.py", "tests/unit/test_codex_capture*.py",
               "tests/unit/test_codex_factor_capture*.py", "tests/unit/test_factor_capture*_reference.py",
               "tests/r02_product/test_calendar_freeze.py", "tests/yaogu/test_short_horizon_archive_bridge*.py"),
    "outcome": ("tests/yaogu/test_*outcome*.py", "tests/r05_product/test_*outcome*.py",
                "tests/r02_product/test_scheduled_outcomes.py", "tests/unit/test_codex_research_protocol*.py",
                "tests/unit/test_research_trial_ledger*.py"),
    "recovery": ("tests/unit/test_*recovery*.py", "tests/unit/test_codex_workspace*.py",
                 "tests/r02_correctness/test_backup_restore.py", "tests/r02_correctness/test_publication_recovery.py",
                 "tests/unit/test_business_authority*.py", "tests/unit/test_restricted_history*.py"),
    "strategy": ("tests/yaogu/test_strategy*.py", "tests/r05_product/test_stage_aware*.py",
                 "tests/r02_independent_strategy/test_*.py", "tests/r03_strategy/test_*.py",
                 "tests/r05_product/test_strategy_experiments_and_tushare.py"),
    "execution": ("tests/backtest/test_*.py", "tests/risk/test_*.py", "tests/integration/test_final_paired_accounting_gate.py",
                  "tests/r05_product/test_weekly_paper_execution.py", "tests/unit/test_execution_evidence*.py"),
    "runtime": ("tests/unit/test_*supervis*.py", "tests/unit/test_*meter*.py",
                "tests/unit/test_durable_stage*.py", "tests/yaogu/test_premarket_runtime*.py",
                "tests/unit/test_active_*.py", "tests/unit/test_daily_delivery*.py"),
}
RULES = (
    (("scripts/codex_daily*", "scripts/codex_chat*", "scripts/codex_workspace*", "stockfish_core/api.py", "stockfish_core/jobs.py"), ("entry", "recovery", "runtime")),
    (("scripts/codex_runtime_recovery*", "scripts/business_authority*", "scripts/restricted_history*"), ("entry", "recovery", "runtime")),
    (("scripts/codex_research_protocol*", "scripts/research_trial_ledger*", "stockfish_core/yaogu/*outcome*"), ("outcome", "execution")),
    (("stockfish_core/yaogu/*provider*", "stockfish_core/yaogu/*source*", "stockfish_core/yaogu/*capture*", "stockfish_core/yaogu/*identity*"), ("source", "entry", "outcome")),
    (("scripts/codex_current_calendar*", "scripts/codex_capture*", "scripts/codex_factor_capture*",
      "scripts/codex_core_sources*", "scripts/codex_latest_capture*"), ("source", "entry", "runtime")),
    (("config/stage_aware*", "config/research_r1*", "stockfish_core/yaogu/*strategy*", "stockfish_core/yaogu/stage_aware*"), ("strategy", "outcome", "execution")),
    (("scripts/execution_evidence*", "stockfish_core/*account*", "stockfish_core/*risk*", "stockfish_core/*backtest*"), ("execution",)),
    (("scripts/durable_stage*", "scripts/daily_delivery*", "scripts/active_*", "stockfish_active/*", "stockfish_core/yaogu/premarket_runtime*"), tuple(GROUPS)),
    (("scripts/engineering_ci_scope*", "scripts/contracts/*", "scripts/active_runtime*"), tuple(GROUPS)),
)


def classify(paths, *, root=ROOT):
    paths = sorted(set(paths))
    result = _legacy["classify"](paths)
    selected, groups, mapping, fallback = set(MANDATORY), set(), {}, []
    available = sorted(p.relative_to(root / "stock-fish").as_posix()
                       for p in (root / "stock-fish/tests").rglob("test_*.py"))
    for path in paths:
        names = set()
        relative = path.removeprefix("stock-fish/")
        if path.startswith(".github/"):
            names.update(GROUPS)
        elif path.startswith("stock-fish/"):
            for patterns, targets in RULES:
                if any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns):
                    names.update(targets)
            if not names and relative.startswith(("stockfish_core/", "scripts/", "config/")) and not relative.endswith(".md"):
                # Unknown implementation has no evidence of independence. Run the
                # complete bounded specialist union, including fixed safety gates.
                names.update(GROUPS)
                fallback.append(path)
        if names:
            mapping[path] = sorted(names)
            groups.update(names)
    for group in groups:
        selected.update(p for p in available if any(fnmatch.fnmatchcase(p, pat) for pat in GROUPS[group]))
    selected.update(result["daily_tests"])
    result.update(schema=VERSION, daily_tests=sorted(selected), changed_paths=paths,
                  dependency_mapping=mapping, selected_groups=sorted(groups),
                  conservative_fallback=fallback, mapping_version=VERSION)
    return result


def partition_tests(selected, index=0, count=1):
    if type(index) is not int or type(count) is not int or not 1 <= count <= 8 or not 0 <= index < count:
        raise ValueError("CI_PARTITION_BOUNDS_REQUIRED")
    if selected != sorted(set(selected)) or not set(MANDATORY).issubset(selected):
        raise ValueError("CI_COMPLETE_SAFETY_SET_REQUIRED")
    groups = [selected[position::count] for position in range(count)]
    if sorted(item for group in groups for item in group) != selected or any(not group for group in groups):
        raise ValueError("CI_PARTITION_MUST_COVER_EACH_MODULE_ONCE")
    return {"schema": "ENGINEERING_CI_PARTITION_V1", "index": index, "count": count,
            "complete_tests": selected, "selected_tests": groups[index],
            "all_partitions": groups, "mandatory_union_verified": True}


def run_tests(scope, root, basetemp, report, *, shard_index=0, shard_count=1, partition_output=None):
    if scope.get("schema") != VERSION:
        raise ValueError("CI_SCOPE_SCHEMA_REQUIRED")
    expected = classify(scope["changed_paths"], root=root)
    for field in ("daily_tests", "dependency_mapping", "selected_groups", "conservative_fallback", "components"):
        if scope.get(field) != expected[field]:
            raise ValueError("CI_SCOPE_RECOMPUTATION_MISMATCH:" + field)
    partition = partition_tests(expected["daily_tests"], shard_index, shard_count)
    for relative in partition["complete_tests"]:
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or not relative.startswith("tests/") or path.suffix != ".py" or not (root / "stock-fish" / relative).is_file():
            raise ValueError("CI_TEST_PATH_REJECTED")
    if basetemp.exists() or report.exists():
        raise ValueError("CI_OUTPUT_MUST_BE_NEW")
    partition.update(base_sha=scope.get("base_sha"), head_sha=scope.get("head_sha"))
    if partition_output is not None:
        with partition_output.open("x", encoding="utf-8") as stream:
            json.dump(partition, stream, sort_keys=True, indent=2)
    print(json.dumps(partition, sort_keys=True), flush=True)
    return subprocess.call([sys.executable, "-I", "-B", str(root / "stock-fish/scripts/run_isolated_tests.py"),
                            *partition["selected_tests"], "-q", "--basetemp", str(basetemp),
                            "--junitxml", str(report)], cwd=root)


def main(argv=None):
    args_list = sys.argv[1:] if argv is None else argv
    if "--run-live" in args_list:
        return _legacy["main"](args_list)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--run-scope", type=Path)
    parser.add_argument("--basetemp", type=Path)
    parser.add_argument("--junitxml", type=Path)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--partition-output", type=Path)
    args = parser.parse_args(args_list)
    if args.run_scope:
        if args.basetemp is None or args.junitxml is None:
            parser.error("explicit new --basetemp and --junitxml required")
        return run_tests(json.loads(args.run_scope.read_bytes()), args.root, args.basetemp, args.junitxml,
                         shard_index=args.shard_index, shard_count=args.shard_count,
                         partition_output=args.partition_output)
    for relative in ("THIRD_PARTY_NOTICES.md", "stock-fish/LICENSE", "qlib/LICENSE", "stock-fish/MiroFish/LICENSE"):
        if not (args.root / relative).is_file():
            raise ValueError("CI_REQUIRED_LICENSE_MISSING:" + relative)
    paths = _legacy["changed_paths"](args.root, args.base, args.head)
    manual = args.base == "0" * 40
    value = classify([".github/workflows/engineering-gates.yml"] if manual else paths, root=args.root)
    value.update(base_sha=args.base, head_sha=args.head,
                 private_path_check="NOT_CHECKED_NO_DIFF_BASE" if manual else "CHANGED_PATHS_CHECKED")
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
