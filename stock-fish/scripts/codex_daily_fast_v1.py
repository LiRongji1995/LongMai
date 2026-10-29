"""Explicit registered gateway for original-engine canonical execution.

The V2 default remains unchanged. This version uses its original admission,
run_once, receipt verification, resource locks and budgets. A changed version,
profile or request key cannot bypass another request for the same sessions.
"""
from __future__ import annotations

import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
from types import FunctionType

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import codex_daily_v2 as daily
from scripts import codex_premarket_fast_v1 as fast
from scripts import codex_runtime_recovery_v1 as recovery

VERSION = "CODEX_DAILY_ORIGINAL_CANONICAL_V1"


def _registered(runtime, argv):
    prepared = daily.original._prepared(runtime.profile)
    script = prepared["source"] / "scripts/codex_daily_fast_v1.py"
    daily.original._fp(script)
    if (ROOT == prepared["source"] and Path(sys.executable).absolute() == prepared["python"]
            and sys.flags.isolated and sys.dont_write_bytecode
            and sys.pycache_prefix is not None and not Path(sys.pycache_prefix).exists()):
        return None
    with tempfile.TemporaryDirectory(prefix="stockfish-daily-canonical-") as directory:
        command = [str(prepared["python"]), "-I", "-B", "-X",
            "pycache_prefix=" + str(Path(directory) / "absent"), str(script), *argv]
        environment = os.environ.copy()
        for name in ("PYTHONHOME", "PYTHONPATH"):
            environment.pop(name, None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.call(command, cwd=prepared["source"], env=environment)


def _prior_request(runtime, identity):
    daily._prior_request(runtime, identity)
    if isinstance(runtime, recovery.RecoveryRuntime):
        runtime.guard_ancestor_request(identity)
    directory = runtime.directory / "operations"
    if not directory.exists():
        return
    daily.need(not directory.is_symlink(), "DAILY_FAST_OPERATION_DIRECTORY_ALIAS")
    paths = sorted(directory.glob("*.json"))
    daily.need(len(paths) <= 10000, "DAILY_FAST_OPERATION_SCAN_LIMIT")
    for path in paths:
        other = daily.workspace._read(path).get("identity", {})
        if other == identity:
            continue
        if (other.get("backend") == identity.get("backend") == "premarket"
                and (other.get("observation_date"), other.get("target_session")) ==
                    (identity.get("observation_date"), identity.get("target_session"))):
            raise daily.original.PremarketError("DAILY_FAST_EXISTING_SESSION_RECONCILE_NO_RETRY")


def _bindings():
    overrides = dict(VERSION=VERSION, _workflow=lambda: fast,
                     _registered=_registered, _prior_request=_prior_request)
    namespace = {**vars(daily), **overrides}
    for name, function in vars(daily).items():
        if name not in overrides and isinstance(function, FunctionType) and function.__module__ == daily.__name__:
            clone = FunctionType(function.__code__, namespace, function.__name__,
                                 function.__defaults__, function.__closure__)
            clone.__kwdefaults__ = function.__kwdefaults__
            namespace[name] = clone
    return namespace


def parser():
    value = daily.parser()
    value.description = "显式original精确编码执行；原准入、日期、请求防重及预算保持"
    value.add_argument("--recovered-runtime", action="store_true",
                       help="显式加载既有恢复登记；不创建epoch、不修改default")
    return value


def execute(runtime, args):
    if args.command == "premarket":
        daily.need(args.engine == "original", "DAILY_FAST_ORIGINAL_ENGINE_REQUIRED")
    return _bindings()["execute"](runtime, args)


def main(argv=None):
    selected = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(selected)
    try:
        runtime = (recovery.load_runtime(args.runtime, args.registry_root) if args.recovered_runtime
                   else daily.workspace.load_runtime(args.runtime, args.registry_root))
        daily.need(runtime is not None, "DAILY_REGISTERED_RUNTIME_REQUIRED")
        dispatched = _registered(runtime, selected)
        if dispatched is not None:
            return dispatched
        code, result = execute(runtime, args)
    except KeyboardInterrupt:
        code, result = 130, dict(status="INTERRUPTED", reason="USER_INTERRUPTED")
    except Exception as error:
        code, result = 2, dict(status="BLOCKED_OR_FAILED", reason=daily.legacy._safe_reason(error))
    print(json.dumps({**result, "entry_version": VERSION, "command_exit_code": code},
                     ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
