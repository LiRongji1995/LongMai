"""One explicit entry for saved results, dependency plans and fresh computation.

Saved views verify their local seal and original exports, and retain the last
full authentication time. They never mint source authority. Audit and compute
remain explicit operations. Existing entrypoints and historical source bytes
are not changed by this version.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import signal
import re
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_workspace as workspace
from scripts import codex_premarket as premarket

VERSION = "CODEX_DAILY_EXECUTION_PIPELINE_V1"
MIB = 1024 ** 2
THEME_KEYS = frozenset(("schema_version", "run_path", "run_manifest_path", "input_path",
                        "sources_manifest_path", "context_cutoff", "membership_mode"))


def need(condition, reason):
    if not condition:
        raise workspace.WorkspaceError(reason)


def _registered(runtime, argv):
    """Use the registered interpreter/source and an absent bytecode lookup."""
    prepared = premarket._prepared(runtime.profile)
    script = prepared["source"] / "scripts" / "codex_daily.py"
    premarket._fp(script)
    if (ROOT == prepared["source"] and Path(sys.executable).absolute() == prepared["python"]
            and sys.pycache_prefix is not None and not Path(sys.pycache_prefix).exists()):
        return None
    with tempfile.TemporaryDirectory(prefix="stockfish-daily-bytecode-") as directory:
        command = [str(prepared["python"]), "-I", "-B", "-X",
                   "pycache_prefix=" + str(Path(directory) / "absent"), str(script), *argv]
        env = os.environ.copy()
        for key in ("PYTHONPATH", "PYTHONHOME"):
            env.pop(key, None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # Preserve full formal exports without capturing them in a console-size buffer.
        return subprocess.call(command, cwd=prepared["source"], env=env)


def _views():
    from scripts import codex_result_view_v1
    return codex_result_view_v1


def _select_view(runtime, view_id):
    if view_id is not None:
        return view_id
    listing = _views().list_views(runtime)
    rows = listing["views"]
    need(bool(rows), "DAILY_NO_SEALED_RESULT_PUBLISH_ONCE_REQUIRED")
    # Listing is a signed locator only. read_view verifies every original export.
    return max(rows, key=lambda row: (row["observation_date"],
        row["decision_cutoff"], row["view_id"]))["view_id"]


def _capacity(runtime):
    from scripts.codex_storage_growth_v1 import plan
    return plan(runtime)


def _plan(runtime, args):
    from scripts import codex_daily_plan_v1 as planner
    view_id = _select_view(runtime, args.view_id)
    view = _views().read_view(runtime, view_id)
    arguments = planner.saved_view_plan_request(view,
        target_session=str(args.target_session) if args.target_session else None)
    arguments["scope_id"] = "daily-plan-" + uuid.uuid4().hex
    capacity = _capacity(runtime)
    arguments["capacity_observation"] = capacity["inventory"]
    arguments["policy"] = {**runtime.profile["storage"],
        "target_roots": [row["name"] for row in capacity["destinations"]]}
    # This callback only vouches for the signed saved-result carrier. The plan
    # explicitly distinguishes it from fresh full-source authentication.
    def verify_result(manifest):
        return (manifest["artifact"] == view["artifacts"]["observation.json"]
                and manifest.get("details", {}).get("view_id") == view_id)
    arguments["verify_result"] = verify_result
    result = planner.build_plan(**arguments)
    # One final result check covers the whole plan, rather than reopening the
    # same five exports for each metadata node.
    final = _views().read_view(runtime, view_id)
    need(final["summary"] == view["summary"] and final["artifacts"] == view["artifacts"],
         "DAILY_SAVED_VIEW_CHANGED_DURING_PLAN")
    result.update(entry_version=VERSION, view_id=view_id, source_reauthenticated_now=False)
    return result


def _theme_request(path):
    before = premarket._reference(Path(path).absolute(), maximum=MIB)
    value = workspace._read(path)
    need(premarket._reference(Path(path).absolute(), maximum=MIB) == before,
         "DAILY_THEME_REQUEST_CHANGED_DURING_READ")
    need(type(value) is dict and set(value) == THEME_KEYS
         and value["schema_version"] == "CODEX_DAILY_THEME_REQUEST_V1", "DAILY_THEME_REQUEST_SCHEMA")
    need(value["membership_mode"] in ("DATED_ONLY", "CURRENT_COHORT_BACKVIEW"),
         "DAILY_THEME_MEMBERSHIP_MODE")
    for key in ("run_path", "run_manifest_path", "input_path", "sources_manifest_path"):
        p = Path(value[key])
        need(p.is_absolute() and p.resolve(strict=True) == p and not p.is_symlink(),
             "DAILY_THEME_UNALIASED_ORIGINAL_REQUIRED")
        premarket._fp(p)
    return value


def _theme_limits(runtime):
    from stockfish_core.yaogu.premarket_runtime_v1 import DEFAULT_LIMITS
    value = workspace._read(runtime.profile["config"], limit=65536)
    supplied = value.get("limits", {})
    need(value.get("state_root") == runtime.profile["state_root"] and type(supplied) is dict
         and not set(supplied) - set(DEFAULT_LIMITS), "DAILY_THEME_REGISTERED_LIMITS_REQUIRED")
    limits = dict(DEFAULT_LIMITS)
    limits.update(max_seconds=2400, output_bytes=128*MIB)
    for key, item in supplied.items():
        if key == "max_seconds" and item is None:
            continue
        need(type(item) in (int, float) and item > 0, "DAILY_THEME_INVALID_REGISTERED_LIMIT")
        limits[key] = min(limits[key], item)
    return limits


def _theme_child(runtime, request_path, output, request_sha):
    """Fresh computation; source capabilities live only in this process."""
    from stockfish_core.yaogu import saved_r2_stream_delivery_v3 as delivery
    from scripts import codex_daily_session_v1 as pipeline
    from stockfish_core.yaogu.saved_r2_sources_v2 import issue_saved_r2_source_reader
    from stockfish_core.yaogu.premarket_runtime_v1 import Budget
    from stockfish_core.yaogu.visibility import stamp
    source_references = [premarket._reference(Path(path), maximum=4*MIB) for path in (
        __file__, workspace.__file__, premarket.__file__, pipeline.__file__, delivery.__file__,
        ROOT / "stockfish_core/yaogu/saved_r2_sources_v2.py",
        ROOT / "stockfish_core/yaogu/premarket_runtime_v1.py")]
    output = Path(output).absolute()
    parent = Path(runtime.profile["report_root"]) / "daily-execution-v1"
    need(output.parent == parent and output.name.startswith("theme-") and not output.exists(),
         "DAILY_THEME_OWNED_OUTPUT_REQUIRED")
    request_reference = premarket._reference(Path(request_path).absolute(), maximum=MIB)
    need(request_reference["sha256"] == request_sha, "DAILY_THEME_REQUEST_CHANGED")
    request = _theme_request(request_path)
    need(premarket._reference(Path(request_path).absolute(), maximum=MIB) == request_reference,
         "DAILY_THEME_REQUEST_CHANGED")
    config_reference = premarket._reference(Path(runtime.profile["config"]), maximum=65536)
    limits = _theme_limits(runtime)
    budget = Budget(limits, job_dir=output)
    with runtime.exclusive():
        admission = runtime.preflight(expected_growth_bytes=limits["output_bytes"]+2*MIB, destination=parent)
        need(admission["allowed"], "DAILY_THEME_CAPACITY_BLOCKED")
        output.mkdir(mode=0o700)
        cutoff = stamp(request["context_cutoff"])
        registry = delivery._Registry()
        physical_reads, original_read = {}, registry.read
        def guarded_read(path, *args, **kwargs):
            selected_path = Path(path).absolute()
            need(selected_path.resolve(strict=True) == selected_path,
                 "DAILY_THEME_FROZEN_SOURCE_ALIAS")
            before = premarket._fp(selected_path)
            need(physical_reads.get(str(selected_path), before) == before,
                 "DAILY_THEME_FROZEN_SOURCE_CHANGED")
            value = original_read(path, *args, **kwargs)
            need(premarket._fp(selected_path) == before, "DAILY_THEME_FROZEN_SOURCE_CHANGED")
            physical_reads[str(selected_path)] = before
            return value
        # This fresh registry is invocation-owned; original modules/classes and
        # all existing full-byte terminal rereads remain unchanged.
        registry.read = guarded_read
        with budget.phase("core"), budget.meter_files():
            bundle, binding, selected, exports, visible, calendar = delivery._freeze_run(registry,
                request["run_path"], request["run_manifest_path"], request["input_path"],
                request["sources_manifest_path"], cutoff)
            need(bundle.coverage_type != "TEST_FIXTURE", "DAILY_PRODUCTION_FIXTURE_FORBIDDEN")
            # Only the genuine issuer can grant this invocation a live reader.
            capability = issue_saved_r2_source_reader(sources_manifest_path=request["sources_manifest_path"],
                                                       cutoff=cutoff)
            reader = capability.reader
            del exports
            themes = (reader.build_current_theme_context(cutoff=cutoff)["themes"]
                      if request["membership_mode"] == "CURRENT_COHORT_BACKVIEW" else
                      ())
        with budget.phase("theme"), budget.meter_files():
            result = pipeline.run_daily_session_pipeline(bundle, reader=reader, cutoff=cutoff,
                directory=output / "session", source_terminal_validator=capability.validate_binding,
                themes=themes, membership_mode=request["membership_mode"],
                limits=pipeline.PipelineLimits(max_seconds=limits["max_seconds"],
                    max_read_bytes=limits["theme_read_bytes"], max_output_bytes=limits["output_bytes"],
                    max_rss_bytes=limits["self_rss_bytes"]),
                checkpoint=lambda: budget.check("shared-session", force=True))
            registry.terminal()
            capability.validate_binding()
            raw = workspace._canonical(result["theme"])
            need(len(raw) <= limits["output_bytes"], "DAILY_THEME_RESULT_SIZE_LIMIT")
            with (output / "theme-result.json").open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            budget.check("completed", force=True)
            registry.terminal()
            capability.validate_binding()
        need(premarket._reference(Path(request_path).absolute(), maximum=MIB) == request_reference
             and premarket._reference(Path(runtime.profile["config"]), maximum=65536) == config_reference,
             "DAILY_THEME_CONTROL_CHANGED_DURING_EXECUTION")
        need(all(premarket._reference(Path(ref["path"]), maximum=4*MIB) == ref
                 for ref in source_references), "DAILY_THEME_EXECUTION_SOURCE_CHANGED")
        receipt = dict(schema_version=VERSION, status="COMPUTED_WITH_EXPLICIT_UNKNOWNS",
            source_reauthenticated_now=True, source_capability_serialized=False,
            frozen_run_binding=binding, output_directory=str(output),
            frozen_source_physical_identity=physical_reads,
            result=premarket._reference(output / "theme-result.json"),
            manifest=premarket._reference(output / "session" / "manifest.json"),
            request_reference=request_reference, config_reference=config_reference,
            execution_source_references=source_references,
            shared_session_metrics=result["metrics"], execution_metrics=budget.metrics,
            currentness_recomputed=False, historical_pit_upgraded=False,
            complete_daily_delivery=False, market_edge="NOT_ESTABLISHED")
        workspace._write(output / "completion.json", receipt)
        budget.check("receipt-completed", force=True)
        return receipt


def _owned_group_exists(process):
    try:
        os.killpg(process.pid, 0)
        return True
    except ProcessLookupError:
        return False


def _stop_owned(process):
    # start_new_session grants ownership of this one group, including children
    # left after the leader exits. Do not infer group completion from poll().
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    deadline = time.monotonic() + 5
    while _owned_group_exists(process) and time.monotonic() < deadline:
        time.sleep(.05)
    if _owned_group_exists(process):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)
    return not _owned_group_exists(process)


def _safe_reason(error):
    reason = str(error)
    return reason if re.fullmatch(r"[A-Z][A-Z0-9_]{0,200}", reason) else type(error).__name__


def _console_reason(console):
    console.seek(0)
    data = console.read(MIB + 1)
    if len(data) > MIB:
        return "DAILY_THEME_CONSOLE_LIMIT"
    try:
        reason = json.loads(data.decode("utf-8").splitlines()[-1]).get("reason", "")
        return reason if isinstance(reason, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,200}", reason) else None
    except (ValueError, IndexError, AttributeError):
        return None


def _theme_run(runtime, request):
    """Bound and reap only the newly owned child. Never retry failed work."""
    import psutil
    from stockfish_core.yaogu.premarket_runtime_v1 import directory_bytes
    need(os.name == "posix", "DAILY_THEME_POSIX_SUPERVISION_REQUIRED")
    request_reference = premarket._reference(Path(request).absolute(), maximum=MIB)
    _theme_request(request)
    need(premarket._reference(Path(request).absolute(), maximum=MIB) == request_reference,
         "DAILY_THEME_REQUEST_CHANGED")
    limits = _theme_limits(runtime)
    prepared = premarket._prepared(runtime.profile)
    entry_reference = premarket._reference(prepared["source"] / "scripts/codex_daily.py", maximum=4*MIB)
    parent = Path(runtime.profile["report_root"]) / "daily-execution-v1"
    with runtime.exclusive():
        admission = runtime.preflight(expected_growth_bytes=limits["output_bytes"]+2*MIB, destination=parent)
        need(admission["allowed"], "DAILY_THEME_CAPACITY_BLOCKED")
        workspace._directory(parent, create=True)
        invocation = "theme-" + uuid.uuid4().hex
        output = parent / invocation
        control = parent / (invocation + ".json")
        workspace._write(control, dict(schema_version=VERSION, phase="STARTING", request=str(request),
            request_reference=request_reference, output=str(output)))
    started, peak, process, code, child_reason = time.monotonic(), 0, None, None, None
    try:
        with tempfile.TemporaryDirectory(prefix="stockfish-daily-worker-") as temporary:
            command = [str(prepared["python"]), "-I", "-B", "-X",
                "pycache_prefix=" + str(Path(temporary)/"absent"),
                str(prepared["source"] / "scripts/codex_daily.py"), "--runtime", runtime.profile["name"],
                "--registry-root", str(runtime.registry_root), "_theme-worker", "--request", str(request),
                "--output", str(output), "--request-sha256", request_reference["sha256"]]
            with tempfile.TemporaryFile() as console:
                process = subprocess.Popen(command, cwd=prepared["source"], stdout=console,
                    stderr=subprocess.STDOUT, start_new_session=True)
                last_output_check = 0
                while process.poll() is None:
                    try:
                        owner = psutil.Process(process.pid)
                        peak = max(peak, sum(p.memory_info().rss for p in [owner,*owner.children(recursive=True)]
                                            if p.is_running()))
                    except psutil.NoSuchProcess:
                        need(process.poll() is not None, "DAILY_THEME_PROCESS_SAMPLE_UNAVAILABLE")
                    except psutil.AccessDenied:
                        raise workspace.WorkspaceError("DAILY_THEME_PROCESS_SAMPLE_DENIED")
                    need(time.monotonic()-started <= limits["max_seconds"]+10,
                         "DAILY_THEME_EXTERNAL_TIME_LIMIT")
                    need(peak <= limits["tree_rss_bytes"], "DAILY_THEME_EXTERNAL_TREE_RSS_LIMIT")
                    need(console.tell() <= MIB, "DAILY_THEME_CONSOLE_LIMIT")
                    if time.monotonic() - last_output_check >= .5:
                        need(directory_bytes(output) <= limits["output_bytes"],
                             "DAILY_THEME_EXTERNAL_OUTPUT_LIMIT")
                        last_output_check = time.monotonic()
                    time.sleep(.1)
                code = process.wait()
                child_reason = _console_reason(console)
                need(os.fstat(console.fileno()).st_size <= MIB, "DAILY_THEME_CONSOLE_LIMIT")
                need(not _owned_group_exists(process), "DAILY_THEME_CHILDREN_REMAINED")
            need(code == 0 and (output/"completion.json").is_file(), "DAILY_THEME_WORKER_FAILED")
        result = workspace._read(output/"completion.json")
        need(result.get("schema_version") == VERSION
             and result.get("status") == "COMPUTED_WITH_EXPLICIT_UNKNOWNS"
             and result.get("output_directory") == str(output)
             and result.get("result", {}).get("path") == str(output / "theme-result.json")
             and result.get("manifest", {}).get("path") == str(output / "session" / "manifest.json"),
             "DAILY_THEME_COMPLETION_BINDING_INVALID")
        need(result.get("request_reference") == request_reference
             and premarket._reference(Path(request).absolute(), maximum=MIB) == request_reference,
             "DAILY_THEME_WORKER_REQUEST_CHANGED")
        need(entry_reference in result.get("execution_source_references", [])
             and premarket._reference(Path(entry_reference["path"]), maximum=4*MIB) == entry_reference,
             "DAILY_THEME_WORKER_ENTRY_CHANGED")
        for key in ("result", "manifest"):
            need(premarket._reference(Path(result[key]["path"])) == result[key],
                 "DAILY_THEME_WORKER_RESULT_CHANGED")
        runtime.check_binding()
        need(time.monotonic()-started <= limits["max_seconds"]+10, "DAILY_THEME_EXTERNAL_TIME_LIMIT")
        need(directory_bytes(output) <= limits["output_bytes"], "DAILY_THEME_EXTERNAL_OUTPUT_LIMIT")
        result["external_supervision"] = dict(exit_code=code, reaped=True,
            elapsed_seconds=time.monotonic()-started, peak_tree_rss_bytes=peak)
        workspace._write(control, dict(schema_version=VERSION, phase="COMPLETED",
            request_reference=request_reference, result=result), replace=True)
        return result
    except BaseException as error:
        reaped = True
        if process is not None:
            reaped = _stop_owned(process)
            code = process.returncode
        workspace._write(control, dict(schema_version=VERSION, phase="FAILED_OR_UNKNOWN", output=str(output),
            request_reference=request_reference, reason=_safe_reason(error), worker_reason=child_reason,
            exit_code=code, owned_group_reaped=reaped, elapsed_seconds=time.monotonic()-started,
            peak_tree_rss_bytes=peak, retry_authorized=False), replace=True)
        raise


def parser():
    result = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    result.add_argument("--runtime")
    result.add_argument("--registry-root", type=Path)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="运行定位、已保存视图与一次容量盘点", allow_abbrev=False)
    commands.add_parser("views", help="查看已签名成果定位", allow_abbrev=False)
    for name in ("publish", "audit"):
        sub = commands.add_parser(name, allow_abbrev=False)
        sub.add_argument("--archive-id", required=True)
        sub.add_argument("--profile", choices=("FULL_CORE_ARCHIVE_READONLY_V1", "FULL_CORE_ARCHIVE_READONLY_V2"), required=True)
    sub = commands.add_parser("show", help="快速读取原成果；不声称来源本次重新认证", allow_abbrev=False)
    sub.add_argument("--view-id")
    sub.add_argument("--role", choices=("summary", "observation.json", "observation.csv", "all.csv", "focus.csv", "unselected.csv"), default="summary")
    sub.add_argument("--observation-date", type=date.fromisoformat)
    sub.add_argument("--target-session", type=date.fromisoformat)
    sub = commands.add_parser("plan", help="当前成果复用与下一会话缺失依赖", allow_abbrev=False)
    sub.add_argument("--view-id")
    sub.add_argument("--target-session", type=date.fromisoformat)
    sub = commands.add_parser("theme-run", help="同次共享事实、原67会话主题计算", allow_abbrev=False)
    sub.add_argument("--request", type=Path, required=True)
    sub = commands.add_parser("_theme-worker", help=argparse.SUPPRESS, allow_abbrev=False)
    sub.add_argument("--request", type=Path, required=True)
    sub.add_argument("--output", type=Path, required=True)
    sub.add_argument("--request-sha256", required=True)
    sub = commands.add_parser("outcomes", help="显式原后验入口，保留原预算和认证", allow_abbrev=False)
    sub.add_argument("arguments", nargs=argparse.REMAINDER)
    sub = commands.add_parser("outcomes-shared", help="同次计划与后验共用完整验证会话", allow_abbrev=False)
    sub.add_argument("arguments", nargs=argparse.REMAINDER)
    return result


def execute(runtime, args):
    need(runtime is not None and runtime.profile.get("backend") == "premarket", "DAILY_REGISTERED_RUNTIME_REQUIRED")
    if args.command == "status":
        return 0, dict(schema_version=VERSION, runtime=workspace.runtime_status(runtime.profile["name"], runtime.registry_root),
            views=_views().list_views(runtime), capacity=_capacity(runtime),
            business_source_readiness="NOT_INFERRED_FROM_RUNTIME_OR_STORAGE")
    if args.command == "views":
        return 0, _views().list_views(runtime)
    if args.command == "publish":
        return 0, _views().publish_view(runtime, args.archive_id, profile=args.profile)
    if args.command == "show":
        return 0, _views().read_view(runtime, _select_view(runtime, args.view_id), role=args.role,
            observation_date=str(args.observation_date) if args.observation_date else None,
            target_session=str(args.target_session) if args.target_session else None)
    if args.command == "audit":
        from scripts import codex_core_archive, codex_core_archive_v2
        module = codex_core_archive if args.profile.endswith("_V1") else codex_core_archive_v2
        return 0, module.reuse_archive(runtime, args.archive_id, profile=args.profile)
    if args.command == "plan":
        return 0, _plan(runtime, args)
    if args.command == "theme-run":
        return 0, _theme_run(runtime, args.request.absolute())
    if args.command == "_theme-worker":
        need(os.name == "posix" and os.getpid() == os.getpgrp(), "DAILY_OWNED_WORKER_PROCESS_REQUIRED")
        return 0, _theme_child(runtime, args.request.absolute(), args.output, args.request_sha256)
    if args.command == "outcomes":
        from stockfish_core.yaogu import r2_closure_cli
        need(bool(args.arguments), "DAILY_EXPLICIT_OUTCOME_COMMAND_REQUIRED")
        return r2_closure_cli.execute(r2_closure_cli.parser().parse_args(args.arguments))
    if args.command == "outcomes-shared":
        from scripts import codex_shared_outcomes_v1
        need(bool(args.arguments), "DAILY_EXPLICIT_OUTCOME_COMMAND_REQUIRED")
        return codex_shared_outcomes_v1.execute_cli(args.arguments)
    raise workspace.WorkspaceError("DAILY_UNKNOWN_COMMAND")


def main(argv=None):
    selected = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(selected)
    try:
        runtime = workspace.load_runtime(args.runtime, args.registry_root)
        need(runtime is not None, "DAILY_REGISTERED_RUNTIME_REQUIRED")
        dispatched = _registered(runtime, selected)
        if dispatched is not None:
            return dispatched
        code, result = execute(runtime, args)
    except KeyboardInterrupt:
        code, result = 130, dict(status="INTERRUPTED", reason="USER_INTERRUPTED")
    except Exception as error:
        safe = _safe_reason(error)
        code, result = 2, dict(status="BLOCKED_OR_FAILED", reason=safe)
    print(json.dumps({**result, "entry_version": VERSION, "command_exit_code": code},
                     ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
