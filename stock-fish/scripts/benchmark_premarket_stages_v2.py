"""Core-only historical benchmark with a bounded comparison-object lifetime.

V1 and its failed evidence stay unchanged. The native stage, timing boundary,
source checks, full comparison and fail-closed publication are reused verbatim.
After native completion its owner is destroyed before decoding comparison JSON;
ordinary file references never restore native source authority.
"""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from dataclasses import dataclass
import gc
import hashlib
from pathlib import Path
import sys
from types import FunctionType
import weakref

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import benchmark_premarket_stages_v1 as v1

# Bind the reviewed orchestration to this independent entry point. No V1
# module/global or original native implementation is patched by this module.
for _name, _value in vars(v1).items():
    if not _name.startswith("__"):
        globals().setdefault(_name, _value)

VERSION = "HISTORICAL_PREMARKET_STAGE_BENCHMARK_V2"
V1_SOURCE_SHA256 = "3a546b876b755f8833d9b5aef247d46e4f05f80ab5c43e8d556fb79a28db5197"
COMPARISON_ROLE = "ORDINARY_COMPARISON_FILE_REFERENCES_NOT_NATIVE_SOURCE_AUTHORITY"


def _clone(function):
    value = FunctionType(function.__code__, globals(), function.__name__,
                         function.__defaults__, function.__closure__)
    value.__kwdefaults__ = function.__kwdefaults__
    return value


for _name in ("_need", "_save", "_configuration", "_script_refs", "compare", "_worker_guard",
              "_receipt_paths", "_arm_output_bytes", "_reserve_failure_receipts",
              "_check_parent_budget", "_publish_process_result", "_run_process", "benchmark"):
    globals()[_name] = _clone(getattr(v1, _name))


def _collections(capture, themes, scope, cutoff):
    _need(scope == "core" and themes is None, "V2_CORE_ONLY_SCOPE_REQUIRED")
    return v1._collections(capture, themes, scope, cutoff)


@dataclass(frozen=True)
class ComparisonFiles:
    """Plain comparison input; no validate method, owner, reader or capability."""
    artifacts: dict
    validation_references: list
    role: str = COMPARISON_ROLE


def _comparison_snapshot(owner):
    from stockfish_core.yaogu.premarket_native_core_v1 import NativeCore
    _need(type(owner) is NativeCore, "GENUINE_OWNER_REQUIRED_BEFORE_RELEASE")
    # Both properties check membership in the original WeakKeyDictionary.
    artifacts = owner.artifacts
    references = _references(artifacts, owner.validation_references)
    return ComparisonFiles(artifacts, references)


def _confirm_released(owner_reference, charge):
    charge(0)
    started = time.perf_counter()
    collected = gc.collect()
    _need(owner_reference() is None, "LIVE_OWNER_SURVIVED_COMPARISON_HANDOFF")
    charge(0)
    return dict(schema_version="CORE_COMPARISON_OBJECT_LIFETIME_V1", role=COMPARISON_ROLE,
        native_owner_released=True, native_source_authority_restored=False,
        garbage_collected_objects=collected, release_wall_seconds=time.perf_counter()-started,
        release_is_outside_native_stage_timing=True, all_original_limits_unchanged=True)


_OLD_HANDOFF = """
proof_started = time.perf_counter()
result["comparison_proof"] = _proof(owner, theme, capture, themes, scope, cutoff,
    actual_started, result["completed_at"], charge)
result["artifact_references"] = _references(owner.artifacts, theme.artifacts if theme else None,
                                           theme.validation_references if theme else None)
"""
_NEW_HANDOFF = """
proof_started = time.perf_counter()
comparison_files = _comparison_snapshot(owner)
owner_reference = weakref.ref(owner)
del owner
result["comparison_lifetime"] = _confirm_released(owner_reference, charge)
result["comparison_proof"] = _proof(comparison_files, None, capture, None, scope, cutoff,
    actual_started, result["completed_at"], charge)
result["artifact_references"] = comparison_files.validation_references
"""


def _build_worker(raw):
    """One exact AST substitution; all native work and stage timers are intact."""
    _need(hashlib.sha256(raw).hexdigest() == V1_SOURCE_SHA256, "FROZEN_V1_SOURCE_CHANGED")
    tree = ast.parse(raw)
    workers = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_worker"]
    _need(len(workers) == 1, "V1_WORKER_REQUIRED")
    worker = workers[0]
    old = ast.parse(_OLD_HANDOFF).body
    expected = [ast.dump(node, include_attributes=False) for node in old]
    replacements = [0]
    class Handoff(ast.NodeTransformer):
        def visit_Try(self, node):
            self.generic_visit(node)
            for index in range(len(node.body)-len(old)+1):
                actual = [ast.dump(part, include_attributes=False) for part in node.body[index:index+len(old)]]
                if actual == expected:
                    replacement = ast.parse(_NEW_HANDOFF).body
                    for part in replacement:
                        ast.copy_location(part, node.body[index])
                    node.body[index:index+len(old)] = replacement
                    replacements[0] += 1
                    break
            return node
    worker = Handoff().visit(worker)
    _need(replacements == [1], "EXACT_V1_COMPARISON_HANDOFF_REQUIRED")
    module = ast.fix_missing_locations(ast.Module(body=[worker], type_ignores=[]))
    namespace = {}
    exec(compile(module, str(Path(v1.__file__).resolve())+":V2-comparison-handoff", "exec"), globals(), namespace)
    return namespace["_worker"]


_worker = _build_worker(Path(v1.__file__).read_bytes())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", allow_abbrev=False)
    run.add_argument("--config", required=True)
    run.add_argument("--capture-summary", required=True)
    run.add_argument("--scope", choices=("core",), default="core")
    run.add_argument("--order", choices=PROFILES, nargs="+", default=["reference", "canonical", "canonical", "reference"])
    run.add_argument("--output", required=True)
    run.add_argument("--arm-output-mib", type=int,
                     help="Explicit per-arm output cap; may only tighten the original config limit")
    worker = commands.add_parser("_worker", allow_abbrev=False)
    worker.add_argument("--request-reference", required=True)
    worker.add_argument("--directory", required=True)
    worker.add_argument("--profile", choices=PROFILES, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "_worker":
            return _worker(strict_json(args.request_reference), Path(args.directory), args.profile)
        source = original._ref(Path(args.config).expanduser().resolve(strict=True))
        result = benchmark(_document(source, lambda _: None), args.capture_summary, None,
            args.scope, args.order, args.output, configuration_source=source, arm_output_mib=args.arm_output_mib)
        print(json_bytes(dict(output=str(Path(args.output).absolute()),
            stage_latency_reduction_percent=result.get("stage_latency_reduction_percent"),
            completed_arms=sum(row["status"] == "COMPLETE" for row in result["results"]),
            unrun_profiles=result["unrun_profiles"], scope=SCOPE)).decode())
        return 0 if not result["unrun_profiles"] and all(row["status"] == "COMPLETE" for row in result["results"]) \
            and all(row["comparable"] for row in result["comparisons"]) else 4
    except Exception as error:
        print(json_bytes(dict(status="FAILED", scope=SCOPE, error=type(error).__name__, reason=str(error)[:2000])).decode())
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
