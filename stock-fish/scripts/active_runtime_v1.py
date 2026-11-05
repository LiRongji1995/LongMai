"""Build a new owned active tree from exact frozen inputs, without deployment.

Only reviewed AST hooks are applied to the existing executors. The input tree,
historical registries and source timestamps are never changed. A failed build
is retained as incomplete evidence and is not an activatable runtime.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys

VERSION = "ACTIVE_RUNTIME_V1"
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BASE_MANIFEST = Path(__file__).with_name("contracts") / "active_runtime_base_v1.json"
EXTENSIONS = (
    "scripts/active_business_gateway_v1.py", "scripts/active_runtime_v1.py",
    "scripts/active_worker_bootstrap_v1.py", "scripts/active_delivery_bridge_v1.py",
    "scripts/active_source_bootstrap_v1.py",
    "scripts/active_outcome_pipeline_v1.py",
    "scripts/active_outcome_input_v1.py",
    "scripts/active_environment_acceptance_v1.py",
    "scripts/active_read_control_v1.py",
    "scripts/active_identity_v2.py", "scripts/active_service_host_v1.py",
    "scripts/codex_active_v1.py",
    "scripts/business_authority_v1.py", "scripts/codex_runtime_recovery_v2.py",
    "scripts/restricted_history_v1.py", "scripts/durable_stage_metrics_v1.py",
    "scripts/daily_delivery_contract_v1.py", "scripts/codex_research_protocol_v2.py",
    "scripts/research_trial_ledger_v1.py", "scripts/execution_evidence_v1.py",
)
# Explicit responsibility-layer inventory. Every selector must match exactly
# once; a changed upstream shape is a build failure, not a best-effort patch.
HOOKS = {
    "scripts/codex_workspace.py": {
        "Runtime.run_once": ("replace", "return _active.run_once(self, identity, action, validate)"),
        "load_runtime": ("replace", "return _active.load_runtime(name, registry_root)"),
        "register_profile": ("prepend", "_active.reject_legacy_registration()"),
        "_lock": ("replace", "with _active.registry_lock(path):\n    yield"),
    },
    "stockfish_core/storage.py": {
        "SQLiteStore._migrate": ("prepend", "_active.require_write()"),
        "SQLiteStore.transaction": ("prepend", "if write: _active.require_write()"),
        "SQLiteStore._health_checked": ("memory_oracle", ""),
    },
    "stockfish_core/jobs.py": {
        "JobService.create": ("prepend", "job_id = _active.job_create(self, kind, payload, job_id, retry_of)"),
        "JobService.start": ("prepend", "_active.current('worker')"),
        "JobService._run": ("wrap", "_active.job_worker(self, job_id)"),
        "JobService.retry": ("prepend", "_active.reject_retry()"),
        "JobService.recover_interrupted": ("prepend", "_active.require_write()"),
    },
    "stockfish_core/yaogu/premarket_workflow_v1.py": {
        "supervise": ("wrap", "_active.workflow_scope('SUPERVISOR', config)"),
        "execute_workflow": ("wrap", "_active.workflow_scope('WORKER', config, job_dir)"),
        "_stop_owned": ("wrap", "_active.cleanup()"),
        "_latest_gate": ("return_session", ""),
    },
    "stockfish_core/yaogu/premarket_capture_cache_v1.py": {
        "CaptureCache.__init__": ("diagnostic_mkdir", ""),
        "CaptureCache.capture_one": ("prepend", "_active_source.guard_capture(self, spec)"),
        "capture_collect": ("source_collect", ""),
    },
    "stockfish_core/yaogu/short_horizon_archive_bridge_v1.py": {
        "authenticate_archive": ("archive_schema", ""),
        "_source_map": ("archive_source_paths", ""),
    },
    "scripts/codex_daily_v2.py": {"_premarket": ("authority_existing", "")},
    "scripts/codex_premarket_preflight_v1.py": {"plan": ("preflight_result", "")},
    "stockfish_core/yaogu/strategy_experiments.py": {"source_identity": ("identity", "EXPERIMENTS")},
    "stockfish_core/yaogu/short_horizon_strength.py": {"short_horizon_source_identity": ("identity", "R1")},
    "stockfish_core/yaogu/short_horizon_participation.py": {"short_horizon_source_identity": ("identity", "R2")},
    "stockfish_core/yaogu/premarket_runtime_v1.py": {
        "Budget.phase": ("wrap", "_active.stage(self, name)"),
        "Budget.charge_read": ("prepend", "_active.charge_read(self, count)"),
        "Budget.check": ("append", "_active.budget_heartbeat(self)"),
    },
    "stockfish_core/yaogu/provider_requests.py": {
        "execute_request": ("wrap", "_active.provider_scope(endpoint, params)"),
    },
    "stockfish_core/yaogu/provider_requests_worker.py": {
        "main": ("transport_result", ""),
        "reserve_attempt": ("prepend", "_active.reserve_transport(_transport, kind, _attempt_counts[kind])"),
        "http_attempt_admission.urlopen": ("wrap", "_active.network_scope()"),
        "http_attempt_admission.putrequest": ("wrap", "_active.network_scope()"),
        "http_attempt_admission.make_request": ("wrap", "_active.network_scope()"),
        "http_attempt_admission.tunnel_wrapper.tunnel": ("wrap", "_active.network_scope()"),
        "baostock_request": ("wrap", "_active.network_scope()"),
    },
    "stockfish_core/yaogu/request_queue.py": {
        "RequestQueue.__init__": ("prepend", "_active.require_write()"),
        "RequestQueue.retry_blocked": ("prepend", "_active.reject_retry()"),
        "RequestQueue.drain": ("queue_guard", ""),
    },
    "stockfish_core/api.py": {"create_app": ("return_app", "")},
    "scripts/codex_chat.py": {"Client.raw": ("http_headers", "")},
    "scripts/codex_research.py": {"Client.raw": ("http_headers", "")},
}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def need(value, code):
    if not value:
        raise ValueError(code)


def _safe(root, relative):
    path = PurePosixPath(relative)
    need(not path.is_absolute() and ".." not in path.parts and "\\" not in relative, "ACTIVE_RELATIVE_PATH_REQUIRED")
    result = root / relative
    need(not result.is_symlink() and result.resolve(strict=True).is_relative_to(root.resolve(strict=True)), "ACTIVE_INPUT_PATH_ALIAS")
    return result


class Overlay(ast.NodeTransformer):
    def __init__(self, relative):
        self.relative = relative
        self.rules = HOOKS.get(relative, {})
        self.stack, self.matches = [], {}

    def visit_ClassDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()
        return node

    def visit_FunctionDef(self, node):
        name = ".".join([*self.stack, node.name])
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()
        if name not in self.rules:
            return node
        self.matches[name] = self.matches.get(name, 0) + 1
        kind, code = self.rules[name]
        doc = node.body[:1] if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str) else []
        body = node.body[len(doc):]
        if kind == "replace":
            body = ast.parse(code).body
        elif kind == "prepend":
            body = ast.parse(code).body + body
        elif kind == "append":
            body = body + ast.parse(code).body
        elif kind == "wrap":
            if self.relative=="stockfish_core/yaogu/provider_requests.py" and name=="execute_request":
                found=0
                for item in ast.walk(node):
                    if isinstance(item,ast.Return) and isinstance(item.value,ast.Name) and item.value.id=="answer":
                        item.value=ast.parse("_active.provider_result(answer)",mode="eval").body
                        found+=1
                need(found==1,"ACTIVE_PROVIDER_RESULT_RETURN_CHANGED")
            if name in {"supervise", "execute_workflow"}:
                class ObserveReturn(ast.NodeTransformer):
                    def visit_FunctionDef(self, child):
                        return child
                    def visit_Return(self, child):
                        if child.value is not None:
                            child.value=ast.Call(func=ast.parse("_active.observed_result",mode="eval").body,args=[child.value],keywords=[])
                        return child
                observer=ObserveReturn()
                body=[observer.visit(item) for item in body]
            body = [ast.With(items=[ast.withitem(context_expr=ast.parse(code, mode="eval").body)], body=body)]
        elif kind == "transport_result":
            found=0
            for index,item in enumerate(body):
                if isinstance(item,ast.Assign) and isinstance(item.value,ast.Dict) and not item.value.keys and any(
                        isinstance(target,ast.Name) and target.id=="_transport" for target in item.targets):
                    body.insert(index,ast.parse("_active.transport_result(request, result)").body[0])
                    found+=1
                    break
            need(found==1,"ACTIVE_TRANSPORT_RESULT_BOUNDARY_CHANGED")
        elif kind == "archive_source_paths":
            found = 0
            for item in ast.walk(node):
                if isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == "_require" \
                        and len(item.args) == 2 and isinstance(item.args[1], ast.Constant) \
                        and item.args[1].value == "CODE_REGISTRY_PATH_INVALID":
                    item.args[0] = ast.BoolOp(op=ast.Or(), values=[item.args[0],
                        ast.parse("_active_outcome.archive_source_path_allowed(session, registry, name, value, root)", mode="eval").body])
                    found += 1
            need(found == 1, "ACTIVE_ARCHIVE_SOURCE_ASSERTION_CHANGED")
        elif kind == "archive_schema":
            found = 0
            for item in ast.walk(node):
                if isinstance(item, ast.Compare) and len(item.ops) == 1 and isinstance(item.ops[0], ast.Eq) \
                        and len(item.comparators) == 1 and isinstance(item.comparators[0], ast.Constant) \
                        and item.comparators[0].value == "STANDALONE_PREMARKET_WORKFLOW_V1":
                    item.comparators[0] = ast.Name(id="_active_archive_schema", ctx=ast.Load())
                    found += 1
            need(found == 2, "ACTIVE_ARCHIVE_ENVELOPE_ASSERTIONS_CHANGED")
            initialized = 0
            for index, item in enumerate(body):
                if isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "session" for t in item.targets):
                    body.insert(index+1, ast.parse("_active_archive_schema = _active_outcome.archive_admission(job_dir, expected_final_sha256, session)").body[0])
                    initialized += 1
                    break
            need(initialized == 1, "ACTIVE_ARCHIVE_SESSION_BOUNDARY_CHANGED")
        elif kind == "source_collect":
            found=0
            for item in ast.walk(node):
                if isinstance(item,ast.Return) and isinstance(item.value,ast.Call) \
                        and isinstance(item.value.func,ast.Name) and item.value.func.id=="CaptureCollection":
                    item.value=ast.Call(func=ast.parse("_active_source.collection_result",mode="eval").body,
                        args=[ast.Name(id="cache",ctx=ast.Load()),item.value],keywords=[])
                    found+=1
            need(found==1,"ACTIVE_SOURCE_COLLECTION_RETURN_CHANGED")
            body=ast.parse("_active_source.bootstrap_capture(cache, now, config)").body+body
        elif kind == "preflight_result":
            found=0
            for item in ast.walk(node):
                if isinstance(item,ast.Return) and isinstance(item.value,ast.Name) and item.value.id=="result":
                    item.value=ast.parse("_active.preflight_result(runtime, result)",mode="eval").body
                    found+=1
            need(found==1,"ACTIVE_PREFLIGHT_RETURN_CHANGED")
            replaced=0
            for item in ast.walk(node):
                if isinstance(item,ast.Call) and isinstance(item.func,ast.Attribute) and item.func.attr=="_existing_cache" \
                        and isinstance(item.func.value,ast.Name) and item.func.value.id=="sources":
                    item.func=ast.parse("_active_source.existing_cache",mode="eval").body
                    replaced+=1
            need(replaced==1,"ACTIVE_PREFLIGHT_CACHE_BOUNDARY_CHANGED")
            body=[ast.With(items=[ast.withitem(context_expr=ast.parse("_active_source.readonly_plan(runtime)",mode="eval").body)],body=body)]
        elif kind == "return_app":
            found = 0
            for item in ast.walk(node):
                if isinstance(item, ast.Return) and isinstance(item.value, ast.Name) and item.value.id == "app":
                    item.value = ast.parse("_active.bind_http(app)", mode="eval").body
                    found += 1
            need(found == 1, "ACTIVE_HTTP_APP_RETURN_CHANGED")
        elif kind == "http_headers":
            body.insert(0, ast.parse("if payload is not None: return _active.http_post(self, path, payload)").body[0])
            found = 0
            for index, item in enumerate(body):
                if isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "headers" for t in item.targets):
                    body.insert(index+1, ast.parse("_active.http_headers(headers, payload)").body[0])
                    found += 1
                    break
            need(found == 1, "ACTIVE_HTTP_HEADERS_CHANGED")
        elif kind == "queue_guard":
            found=0
            for item in ast.walk(node):
                if isinstance(item,ast.For) and isinstance(item.target,ast.Name) and item.target.id=="row":
                    # Preserve COMPLETE cache reuse, while never recycling a
                    # prior failed/partial/running attempt under a new authority.
                    item.body.insert(0,ast.parse("_active.queue_attempt(row)").body[0])
                    found+=1
            need(found==1,"ACTIVE_QUEUE_LOOP_CHANGED")
        elif kind == "diagnostic_mkdir":
            class Mkdir(ast.NodeTransformer):
                def visit_Call(self, child):
                    self.generic_visit(child)
                    if isinstance(child.func,ast.Attribute) and child.func.attr=="mkdir":
                        return ast.Call(func=ast.parse("_active.mkdir_path",mode="eval").body,
                            args=[child.func.value,*child.args],keywords=child.keywords)
                    if isinstance(child.func,ast.Attribute) and child.func.attr=="resolve" \
                            and isinstance(child.func.value,ast.Attribute) and child.func.value.attr=="root" \
                            and isinstance(child.func.value.value,ast.Name) and child.func.value.value.id=="self":
                        return ast.Call(func=ast.parse("_active_source.cache_resolve",mode="eval").body,
                            args=[child.func.value],keywords=[])
                    return child
            body=[Mkdir().visit(item) for item in body]
        elif kind == "authority_existing":
            found=0
            for item in ast.walk(node):
                if isinstance(item,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="already_claimed" for t in item.targets):
                    item.value=ast.BoolOp(op=ast.Or(),values=[item.value,ast.parse("_active.business_existing(runtime,identity)",mode="eval").body])
                    found+=1
            need(found==1,"ACTIVE_DAILY_CLAIM_LOOKUP_CHANGED")
            admissions=0
            sessions=0
            for item in ast.walk(node):
                if isinstance(item,ast.Call) and isinstance(item.func,ast.Attribute) and item.func.attr=="get" \
                        and isinstance(item.func.value,ast.Name) and item.func.value.id in {"plan","fresh_plan"} \
                        and len(item.args)==1 and isinstance(item.args[0],ast.Constant) and item.args[0].value=="execution_admitted":
                    selected=item.func.value.id
                    item.func=ast.parse("_active_source.admitted",mode="eval").body
                    item.args=[ast.Name(id=selected,ctx=ast.Load())]
                    admissions+=1
                if isinstance(item,ast.Call) and isinstance(item.func,ast.Name) and item.func.id=="need" \
                        and len(item.args)==2 and isinstance(item.args[1],ast.Constant) \
                        and item.args[1].value=="DAILY_SHARED_REQUEST_NOT_LATEST_SESSION":
                    names={n.id for n in ast.walk(item.args[0]) if isinstance(n,ast.Name)}
                    selected="fresh_plan" if "fresh_plan" in names else "plan"
                    need(selected in names,"ACTIVE_DAILY_SESSION_GUARD_CHANGED")
                    item.args[0]=ast.parse(f"_active_source.sessions_match({selected}, observation, target)",mode="eval").body
                    sessions+=1
            need(admissions==2 and sessions==2,"ACTIVE_DAILY_SOURCE_ADMISSION_BOUNDARY_CHANGED")
        elif kind == "return_session":
            for item in ast.walk(node):
                if isinstance(item,ast.Return) and item.value is not None:
                    item.value=ast.Call(func=ast.parse("_active.session_result",mode="eval").body,args=[item.value],keywords=[])
        elif kind == "identity":
            for item in ast.walk(node):
                if isinstance(item,ast.Return) and item.value is not None:
                    item.value=ast.Call(func=ast.parse("_active.identity_return",mode="eval").body,
                        args=[item.value,ast.Name(id="__file__",ctx=ast.Load()),ast.Constant(value=code)],keywords=[])
        elif kind == "memory_oracle":
            found=0
            for item in ast.walk(node):
                if (isinstance(item,ast.Call) and isinstance(item.func,ast.Attribute) and item.func.attr=="connect"
                        and isinstance(item.func.value,ast.Name) and item.func.value.id=="sqlite3"
                        and len(item.args)==1 and isinstance(item.args[0],ast.Constant) and item.args[0].value==":memory:"):
                    item.func=ast.parse("_active.memory_schema_oracle",mode="eval").body
                    item.args=[]
                    found+=1
            need(found==1,"ACTIVE_MEMORY_SCHEMA_ORACLE_CHANGED")
        node.body = doc + body
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        target = node.func
        if (self.stack == ["JobService","start"] and isinstance(target,ast.Attribute) and target.attr=="submit"
                and isinstance(target.value,ast.Attribute) and target.value.attr=="_executor"):
            return ast.copy_location(ast.parse("_active.job_dispatch(self, job_id, handler)",mode="eval").body,node)
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "subprocess" and target.attr in {"Popen", "run", "call", "check_call", "check_output"}:
            return ast.copy_location(ast.Call(func=ast.parse("_active.process_call", mode="eval").body,
                args=[target, *node.args], keywords=node.keywords), node)
        return node


def transform(relative, raw):
    tree = ast.parse(raw, filename=relative)
    overlay = Overlay(relative)
    tree = overlay.visit(tree)
    need(overlay.matches == dict.fromkeys(overlay.rules, 1), "ACTIVE_HOOK_SHAPE_CHANGED:" + relative)
    index = 0
    if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant) and isinstance(tree.body[0].value.value, str):
        index = 1
    while index < len(tree.body) and isinstance(tree.body[index], ast.ImportFrom) and tree.body[index].module == "__future__":
        index += 1
    depth = len(PurePosixPath(relative).parts)-1
    bootstrap = ("import sys as _active_sys\nfrom pathlib import Path as _ActivePath\n"
                 f"_active_source_root = str(_ActivePath(__file__).resolve().parents[{depth}])\n"
                 "if _active_source_root not in _active_sys.path: _active_sys.path.insert(0, _active_source_root)\n"
                 "from scripts import active_business_gateway_v1 as _active\n_active.install(__file__)\n")
    if relative == "app.py":
        # Kept as a frozen verifier input; the historical shell must not load
        # .env/config or start an unbound service in the generated active tree.
        bootstrap += "_active.reject_legacy_registration()\n"
    if relative in {"scripts/codex_daily_v2.py", "scripts/codex_premarket_preflight_v1.py",
                    "stockfish_core/yaogu/premarket_capture_cache_v1.py"}:
        bootstrap += "from scripts import active_source_bootstrap_v1 as _active_source\n"
    if relative == "stockfish_core/yaogu/short_horizon_archive_bridge_v1.py":
        bootstrap += "from scripts import active_outcome_pipeline_v1 as _active_outcome\n"
    tree.body[index:index] = ast.parse(bootstrap).body
    ast.fix_missing_locations(tree)
    compiled = (ast.unparse(tree) + "\n").encode()
    compile(compiled, relative, "exec")
    return compiled


def dependency_inventory(files):
    modules = {p[:-3].replace("/", ".").removesuffix(".__init__"): p for p in files if p.endswith(".py")}
    graph, dynamic = {}, {}
    for module, path in modules.items():
        tree = ast.parse(files[path], filename=path)
        deps, calls = set(), []
        package = module if path.endswith("/__init__.py") else module.rsplit(".", 1)[0]
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ""
                if node.level:
                    prefix = ".".join(package.split(".")[:len(package.split("."))-node.level+1] + ([prefix] if prefix else []))
                names = [prefix, *[prefix + "." + alias.name for alias in node.names]]
            for name in names:
                if name in modules:
                    deps.add(modules[name])
            if isinstance(node, ast.Call) and (
                    isinstance(node.func, ast.Name) and node.func.id in {"__import__", "eval", "exec", "FunctionType"}
                    or isinstance(node.func, ast.Attribute) and node.func.attr in {"import_module", "run_path", "spec_from_file_location", "exec_module"}):
                calls.append({"line":node.lineno,"expression":ast.unparse(node)})
        graph[path] = sorted(deps)
        if calls:
            dynamic[path] = calls
    return graph, dynamic


def component_identities(files):
    graph, dynamic = dependency_inventory(files)
    from scripts.active_identity_v2 import component_identities as explicit_identities
    return explicit_identities(files), graph, dynamic


def build(base_root, destination, *, extension_root=ROOT, authority_binding, base_manifest=BASE_MANIFEST):
    base_root, extension_root, destination = Path(base_root).absolute(), Path(extension_root).absolute(), Path(destination).absolute()
    need(not destination.exists() and not destination.is_symlink() and destination.parent.is_dir(), "ACTIVE_DESTINATION_MUST_BE_NEW")
    manifest = json.loads(Path(base_manifest).read_bytes())
    need(manifest["schema"] == "ACTIVE_RUNTIME_BASE_V1", "ACTIVE_BASE_SCHEMA")
    need(set(authority_binding) == {"database", "workspace_id"}, "ACTIVE_AUTHORITY_BINDING_FIELDS")
    from scripts.business_authority_v1 import Authority
    authority = Authority(authority_binding["database"], authority_binding["workspace_id"])
    files, inputs, originals = {}, {}, {}
    for item in manifest["files"]:
        name = item["path"]
        raw = _safe(base_root, name).read_bytes()
        need(len(raw)==item["bytes"] and sha(raw)==item["sha256"], "ACTIVE_FROZEN_PREIMAGE_MISMATCH:"+name)
        originals[name] = raw
        inputs[name] = item["sha256"]
        # Pure numerical/evidence modules retain byte identity. Package imports
        # install the common audit boundary; direct scripts and actual writer/
        # subprocess responsibility layers need explicit hooks.
        subprocess_sites = name.endswith(".py") and b"subprocess." in raw
        overlay_needed = name in HOOKS or name in {"app.py", "stockfish_core/__init__.py"} or name.startswith("scripts/") or subprocess_sites
        files[name] = transform(name, raw) if name.endswith(".py") and overlay_needed else raw
    for name in EXTENSIONS:
        need(name not in files, "ACTIVE_EXTENSION_COLLIDES_WITH_FROZEN_SOURCE")
        raw = _safe(extension_root, name).read_bytes()
        files[name], inputs[name] = raw, sha(raw)
    identities, graph, dynamic = component_identities(files)
    output = dict(schema=VERSION, frozen_commit=manifest["published_commit"],
        input_sha256=inputs, output_sha256={p:sha(raw) for p,raw in files.items()},
        hooks=HOOKS, component_identities=identities, dependency_graph=graph,
        declared_dynamic_dependencies=dynamic, authority_binding={**authority_binding,"physical":list(authority.physical)},
        activation_authorized=False, source_authority_restored=False, market_edge="NOT_ESTABLISHED")
    destination.mkdir(mode=0o700)
    marker = destination / "BUILD_INCOMPLETE.json"
    marker.write_bytes(canonical({"schema":VERSION,"state":"BUILDING"}))
    for name,raw in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    with (destination / "ACTIVE_RUNTIME_MANIFEST.json").open("xb") as stream:
        stream.write(canonical(output))
        stream.flush()
        os.fsync(stream.fileno())
    marker.unlink()
    verify(destination)
    return output


def verify(root, *, read_meter=None):
    root = Path(root).absolute()
    def read(path):
        raw=path.read_bytes()
        if read_meter is not None:
            read_meter(len(raw))
        return raw
    need(not (root/"BUILD_INCOMPLETE.json").exists(), "ACTIVE_BUILD_INCOMPLETE")
    manifest = json.loads(read(root/"ACTIVE_RUNTIME_MANIFEST.json"))
    need(manifest.get("schema")==VERSION, "ACTIVE_MANIFEST_SCHEMA")
    expected = manifest["output_sha256"]
    entries=list(root.rglob("*"))
    need(not any(p.is_symlink() for p in entries),"ACTIVE_SOURCE_ALIAS")
    actual = {p.relative_to(root).as_posix() for p in entries if p.is_file()}
    need(actual == set(expected)|{"ACTIVE_RUNTIME_MANIFEST.json"}, "ACTIVE_UNDECLARED_SOURCE_DEPENDENCY")
    for name,digest in expected.items():
        need(sha(read(_safe(root,name))) == digest, "ACTIVE_SOURCE_CHANGED:"+name)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("verify")
    check.add_argument("root", type=Path)
    create = sub.add_parser("build")
    create.add_argument("--base-root", type=Path, required=True)
    create.add_argument("--destination", type=Path, required=True)
    create.add_argument("--authority-database", required=True)
    create.add_argument("--workspace-id", required=True)
    args = parser.parse_args(argv)
    value = verify(args.root) if args.command=="verify" else build(args.base_root,args.destination,
        authority_binding={"database":args.authority_database,"workspace_id":args.workspace_id})
    print(json.dumps({"schema":VERSION,"files":len(value["output_sha256"]),
        "execution_sha256":value["component_identities"]["execution"]["sha256"],"activated":False,"market_edge":"NOT_ESTABLISHED"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
