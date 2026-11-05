"""Common active-runtime write boundary; frozen executors contain no new algorithms.

The builder inserts these hooks at responsibility layers. No environment flag is
a fixture bypass. Every write needs an existing authority and active capability;
unsupported historical writes fail closed. Control-state operations are scoped
internally and never exposed as a business command.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

from scripts import business_authority_v1 as authority_module

_CURRENT = ContextVar("stockfish_business", default=None)
_CONTROL = ContextVar("stockfish_control", default=False)
_JOURNAL = ContextVar("stockfish_journal", default=None)
_NETWORK = ContextVar("stockfish_admitted_transport", default=False)
_INSTALLED = False
_PROCESS_LOCK = threading.RLock()
_MANIFEST = None
_MANIFEST_FP = None
_EPOCH_ATTESTATION = None
_SOURCE_ROOT = Path(__file__).resolve().parents[1]


def need(ok, code):
    if not ok:
        raise authority_module.AuthorityError(code)


@contextmanager
def _control():
    token = _CONTROL.set(True)
    try:
        yield
    finally:
        _CONTROL.reset(token)


def current(stage="storage"):
    value = _CURRENT.get()
    with _control():
        if value is None:
            owner = authority_module.authority_from_environment()
            capability = os.environ.get("STOCKFISH_WORKER_CAPABILITY")
            claim = owner.verify_capability(capability, stage=stage,
                       bid=os.environ.get("STOCKFISH_BUSINESS_ID"))
            value = dict(owner=owner, claim=claim, capability=capability)
            _CURRENT.set(value)
        else:
            value["owner"].verify_capability(value["capability"], stage=stage,
                                           bid=value["claim"]["business_id"])
        _bound_authority(value["owner"])
    return value


def _manifest():
    global _MANIFEST, _MANIFEST_FP
    path = _SOURCE_ROOT / "ACTIVE_RUNTIME_MANIFEST.json"
    need(path.is_file() and not path.is_symlink(), "ACTIVE_DEPLOYMENT_MANIFEST_REQUIRED")
    info=path.stat()
    fingerprint=tuple(getattr(info,k,0) for k in ("st_dev","st_ino","st_mode","st_size","st_mtime_ns","st_ctime_ns","st_flags"))
    if _MANIFEST is not None and fingerprint==_MANIFEST_FP:
        return _MANIFEST[1]
    raw = path.read_bytes()
    value = json.loads(raw)
    if _MANIFEST is not None:
        need(_MANIFEST[0] == hashlib.sha256(raw).hexdigest(), "ACTIVE_DEPLOYMENT_MANIFEST_CHANGED")
    else:
        _MANIFEST = (hashlib.sha256(raw).hexdigest(), value)
    _MANIFEST_FP=fingerprint
    return value


def _bound_authority(owner):
    global _EPOCH_ATTESTATION
    binding = _manifest()["authority_binding"]
    need(binding == {"database":str(owner.path), "workspace_id":owner.workspace_id,
                     "physical":list(owner.physical)}, "ACTIVE_AUTHORITY_DEPLOYMENT_MISMATCH")
    epoch = owner.active_epoch()
    need(epoch is not None, "ACTIVE_RECOVERY_EPOCH_REQUIRED")
    cached=(epoch["epoch_id"],hashlib.sha256(epoch["manifest"].encode()).hexdigest(),_MANIFEST_FP)
    if _EPOCH_ATTESTATION==cached:
        return
    plan = json.loads(epoch["manifest"])
    need(Path(plan["profile"]["source_root"]).resolve() == _SOURCE_ROOT,
         "ACTIVE_EPOCH_SOURCE_TREE_MISMATCH")
    from scripts.codex_runtime_recovery_v2 import reference
    need(plan.get("active_source_manifest") == reference(_SOURCE_ROOT/"ACTIVE_RUNTIME_MANIFEST.json"),
         "ACTIVE_MANIFEST_NOT_ATTESTED_BY_RECOVERY")
    _EPOCH_ATTESTATION=cached


def require_write():
    return current("storage")


def memory_schema_oracle():
    """A fixed health-only in-memory oracle, with disk attachment prohibited."""
    import sqlite3
    with _control():
        connection=sqlite3.connect(":memory:")
        connection.execute("PRAGMA temp_store=MEMORY")
    def authorize(action,first,second,database,trigger):
        if action in {sqlite3.SQLITE_ATTACH,sqlite3.SQLITE_DETACH}:
            return sqlite3.SQLITE_DENY
        if action==sqlite3.SQLITE_FUNCTION and str(second or first).lower()=="load_extension":
            return sqlite3.SQLITE_DENY
        if action==sqlite3.SQLITE_PRAGMA and first in {"temp_store","temp_store_directory","data_store_directory"} and second is not None:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    connection.set_authorizer(authorize)
    return connection



def readonly_authority(database=None, workspace_id=None):
    """Exact native Authority with read-only connections and no business grant.

    Only opening/closing the manifest-bound original SQLite carrier receives the
    control exemption. The caller's audit body runs outside that exemption.
    """
    import sqlite3
    binding = _manifest()["authority_binding"]
    need(database is None or str(Path(database).absolute()) == binding["database"],
         "ACTIVE_READ_AUTHORITY_DATABASE_MISMATCH")
    need(workspace_id is None or workspace_id == binding["workspace_id"],
         "ACTIVE_READ_AUTHORITY_WORKSPACE_MISMATCH")
    with _control():
        owner = authority_module.Authority(binding["database"], binding["workspace_id"])
        _bound_authority(owner)
    def authorize(action, first, second, database_name, trigger):
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                   sqlite3.SQLITE_RECURSIVE}
        if action not in allowed:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and str(second or first).lower() == "load_extension":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    @contextmanager
    def connection(*, readonly=False):
        need(readonly is True, "ACTIVE_READ_AUTHORITY_MUTATION_FORBIDDEN")
        need(_manifest()["authority_binding"] == {"database": str(owner.path),
             "workspace_id": owner.workspace_id, "physical": list(owner.physical)},
             "ACTIVE_READ_AUTHORITY_BINDING_CHANGED")
        manager = authority_module.Authority._connect(owner, readonly=True)
        with _control():
            selected = manager.__enter__()
            selected.execute("PRAGMA query_only=ON")
            selected.set_authorizer(authorize)
        try:
            yield selected
        except BaseException:
            failure = sys.exc_info()
            with _control():
                manager.__exit__(*failure)
            raise
        else:
            with _control():
                manager.__exit__(None, None, None)
    owner._connect = connection
    return owner


def child_environment(environment=None):
    value = current("worker")
    result = dict(os.environ if environment is None else environment)
    for key in ("PYTHONHOME","PYTHONPATH","PYTHONUSERBASE","PYTHONSTARTUP","PYTHONINSPECT"):
        result.pop(key,None)
    result.update(STOCKFISH_AUTHORITY_DATABASE=str(value["owner"].path),
        STOCKFISH_AUTHORITY_WORKSPACE=value["owner"].workspace_id,
        STOCKFISH_WORKER_CAPABILITY=value["capability"],
        STOCKFISH_BUSINESS_ID=value["claim"]["business_id"],
        PYTHONDONTWRITEBYTECODE="1")
    return result


def process_call(function, *args, **kwargs):
    # All subprocess sites in the active closure inherit the same claimed
    # request. A new child cannot silently create a fresh request or authority.
    # Frozen adapters validate/rewrite their own original vector before reaching
    # their real subprocess call. Their declared source has that same hook.
    code=getattr(function,"__code__",None)
    if code is not None and getattr(function,"__name__","")=="Popen":
        path=Path(code.co_filename).resolve()
        if path.is_relative_to(_SOURCE_ROOT) and path.relative_to(_SOURCE_ROOT).as_posix() in _manifest()["output_sha256"]:
            return function(*args,**kwargs)
    command=args[0] if args else kwargs.get("args")
    need(isinstance(command,(list,tuple)) and command and all(isinstance(p,str) for p in command)
         and not kwargs.get("shell"),"ACTIVE_SUBPROCESS_VECTOR_REQUIRED")
    command=list(command)
    claimed=_CURRENT.get() is not None or bool(os.environ.get("STOCKFISH_WORKER_CAPABILITY"))
    if command[0] in {"ps","/bin/ps"}:
        need(command[1:2]==["-axo"] and len(command)==3 and
             command[2] in {"pid=,ppid=,rss=","pid=,pgid=,stat="},"ACTIVE_NATIVE_TOOL_NOT_DECLARED")
        command[0]="/bin/ps"
    else:
        need(Path(command[0]).absolute()==Path(sys.executable).absolute(),"ACTIVE_INTERPRETER_MISMATCH")
        index=1
        interpreter_options=["-I","-B"]
        while index<len(command) and command[index] in {"-I","-B","-u","-X"}:
            if command[index]=="-X":
                need(index+1<len(command) and command[index+1].startswith("pycache_prefix="),"ACTIVE_PYTHON_OPTION_REJECTED")
                interpreter_options.extend(command[index:index+2])
                index+=2
            else:
                index+=1
        need(index<len(command),"ACTIVE_PYTHON_ENTRY_REQUIRED")
        if command[index]=="-m":
            need(claimed and index+1<len(command),"ACTIVE_MODULE_REQUIRES_CLAIM")
            relative=command[index+1].replace(".","/")+".py"
            need(relative.startswith(("stockfish_core/","scripts/")) and relative in _manifest()["output_sha256"],
                 "ACTIVE_UNDECLARED_WORKER_MODULE")
            bootstrap_arguments=["--module",command[index+1],"--",*command[index+2:]]
        else:
            need(command[index] not in {"-c","-"},"ACTIVE_INLINE_WORKER_NOT_DECLARED")
            path=Path(command[index]).resolve()
            need(path.is_relative_to(_SOURCE_ROOT) and path.relative_to(_SOURCE_ROOT).as_posix() in _manifest()["output_sha256"],
                 "ACTIVE_EXTERNAL_COMPATIBILITY_WORKER_FORBIDDEN")
            if not claimed:
                remaining=command[index+1:]
                # Parse the known global locator options before the read verb.
                while remaining and remaining[0] in {"--runtime","--registry-root"}:
                    need(len(remaining)>=2,"ACTIVE_READ_LOCATOR_VALUE_REQUIRED")
                    remaining=remaining[2:]
                need(remaining and remaining[0] in {"premarket-plan","show","status","verify","inspect"},
                     "ACTIVE_UNCLAIMED_SUBPROCESS_WRITE_FORBIDDEN")
            bootstrap_arguments=["--script",str(path),"--",*command[index+1:]]
        # Intercept the real frozen workflow launch, after the shared adapter
        # has fixed its engine and dates. Native supervision owns the actual
        # worker PID, whose journal becomes the terminal authority receipt.
        entry = ("original" if bootstrap_arguments[:2] == ["--module", "stockfish_core.yaogu.premarket_workflow_v1"]
                 else "shared" if bootstrap_arguments[:2] == ["--script", str(_SOURCE_ROOT/"scripts/codex_premarket_shared_v1.py")]
                 else None)
        worker_args = bootstrap_arguments[3:]
        if entry and worker_args[:1] == ["_worker"]:
            need(claimed and "--job" in worker_args,"ACTIVE_WORKER_JOB_REQUIRED")
            from scripts.active_delivery_bridge_v1 import supervisor_command
            job=worker_args[worker_args.index("--job")+1]
            reserve_workflow_supervisor(job,entry=entry,worker_args=worker_args)
            command=supervisor_command(_SOURCE_ROOT, job,
                entry=entry, worker_args=worker_args)
        elif bootstrap_arguments[:2] == ["--script",str(_SOURCE_ROOT/"scripts/active_worker_bootstrap_v1.py")]:
            # The native supervisor hashes this exact vector. Do not wrap the
            # already isolated bootstrap a second time and invalidate its ticket.
            command=[sys.executable,*interpreter_options,*command[index:]]
        else:
            command=[sys.executable,*interpreter_options,str(_SOURCE_ROOT/"scripts/active_worker_bootstrap_v1.py"),*bootstrap_arguments]
        kwargs["cwd"]=str(_SOURCE_ROOT)
    if claimed:
        kwargs["env"] = child_environment(kwargs.get("env"))
    if args:
        args=(command,*args[1:])
    else:
        kwargs["args"]=command
    return function(*args, **kwargs)


def _audit(event, args):
    if event in {"sqlite3.load_extension","sqlite3.enable_load_extension"}:
        raise authority_module.AuthorityError("ACTIVE_SQLITE_EXTENSION_LOADING_FORBIDDEN")
    if _CONTROL.get():
        return
    if event == "exec":
        filename=getattr(args[0],"co_filename","")
        if filename and not filename.startswith("<"):
            path=Path(filename).absolute()
            if path.is_relative_to(_SOURCE_ROOT):
                with _control():
                    expected=_manifest()["output_sha256"].get(path.relative_to(_SOURCE_ROOT).as_posix())
                    need(expected is not None and hashlib.sha256(path.read_bytes()).hexdigest()==expected,
                         "ACTIVE_UNDECLARED_DYNAMIC_DEPENDENCY")
        return
    if event == "open":
        _, mode, flags = args
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)):
            require_write()
    elif event == "sqlite3.connect":
        # The audit event omits uri=True. Even a file: URI-looking filename
        # cannot establish read-only mode. Byte-only historical reading uses
        # restricted_history_v1; active database access is capability-bound.
        require_write()
    elif event in {"os.mkdir", "os.remove", "os.rename", "os.rmdir", "os.truncate"}:
        require_write()
    elif event in {"socket.connect", "socket.getaddrinfo"}:
        context=_CURRENT.get()
        need(context is None or context["claim"]["business"]["operation"]!="MAINTENANCE",
             "ACTIVE_MAINTENANCE_NETWORK_FORBIDDEN")
        # Loopback is used for read-only health/export. POST has its own bound
        # request header and server-side check; remote providers need admission.
        address = args[1] if event == "socket.connect" else args[0]
        host = address[0] if isinstance(address, tuple) else address
        if host not in {"127.0.0.1", "::1", "localhost"}:
            current("network")
            need(_NETWORK.get(), "ACTIVE_NETWORK_WITHOUT_TRANSPORT_RESERVATION")


def install(filename=None):
    global _INSTALLED
    if not _INSTALLED:
        sys.dont_write_bytecode = True
        sys.addaudithook(_audit)
        _INSTALLED = True
    if filename is not None:
        with _control():
            path=Path(filename).resolve()
            relative=path.relative_to(_SOURCE_ROOT).as_posix()
            need(_manifest()["output_sha256"].get(relative)==hashlib.sha256(path.read_bytes()).hexdigest(),
                 "ACTIVE_UNDECLARED_OR_CHANGED_IMPORT")


@contextmanager
def registry_lock(path):
    """Only control-lock acquisition is exempt; the yielded body stays guarded."""
    import fcntl
    import stat
    path=Path(path).absolute()
    with _control():
        from scripts.codex_workspace import _directory_fd, _file_stat
        owner=authority_module.Authority(_manifest()["authority_binding"]["database"],
                                         _manifest()["authority_binding"]["workspace_id"])
        _bound_authority(owner)
        plan=json.loads(owner.active_epoch()["manifest"])
        roots={Path(p) for p in plan["inventory"]["registry_roots"]}
        need(path.parent.name=="resources" and path.parent.parent in roots and path.name.endswith(".lock"),
             "ACTIVE_CONTROL_LOCK_OUTSIDE_REGISTRY")
        directory=_directory_fd(path.parent,create=True)
        parent=directory.__enter__()
        flags=os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK
        try:
            fd=os.open(path.name,flags|os.O_CREAT|os.O_EXCL,0o600,dir_fd=parent)
        except FileExistsError:
            fd=os.open(path.name,flags,dir_fd=parent)
        item=os.fstat(fd)
        need(stat.S_ISREG(item.st_mode) and item.st_nlink==1,"ACTIVE_LOCK_NOT_REGULAR")
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            directory.__exit__(*sys.exc_info())
            raise
        now=_file_stat(parent,path.name)
        need((now.st_dev,now.st_ino)==(item.st_dev,item.st_ino),"ACTIVE_LOCK_CHANGED")
    try:
        yield
    finally:
        with _control():
            fcntl.flock(fd,fcntl.LOCK_UN)
            os.close(fd)
            directory.__exit__(None,None,None)


def load_runtime(name=None, registry_root=None):
    from scripts import codex_workspace as legacy
    from scripts import codex_runtime_recovery_v2 as recovery
    root = Path(registry_root or legacy.default_registry_root()).expanduser().absolute()
    name = name or os.environ.get("STOCKFISH_CHAT_RUNTIME")
    need(name is not None, "ACTIVE_EXPLICIT_V2_REGISTRATION_REQUIRED")
    descriptor=os.environ.get("STOCKFISH_ACTIVE_SERVICE_DESCRIPTOR")
    if descriptor:
        from scripts.active_service_host_v1 import load_api_runtime
        with _control():
            runtime=load_api_runtime(descriptor)
            need(runtime.profile["name"]==name,"ACTIVE_SERVICE_NAME_MISMATCH")
    else:
        with _control():
            runtime=recovery.load_runtime(name, root)
            _bound_authority(runtime.authority)
    original_check=runtime.check_binding
    def checked():
        with _control():
            original_check()
            _bound_authority(runtime.authority)
    runtime.check_binding=checked
    return runtime


def reject_legacy_registration():
    raise authority_module.AuthorityError("ACTIVE_REGISTRATION_REQUIRES_COMPLETE_V2_RECOVERY_PLAN")


def reject_retry():
    raise authority_module.AuthorityError("ACTIVE_RETRY_REQUIRES_EXPLICIT_AUTHORITY_RECONCILIATION")


def mkdir_path(path,*args,**kwargs):
    path=Path(path)
    from scripts.active_source_bootstrap_v1 import virtual_mkdir
    if virtual_mkdir(path,kwargs):
        return None
    if _CURRENT.get() is None and not os.environ.get("STOCKFISH_WORKER_CAPABILITY"):
        need(kwargs.get("exist_ok") is True and path.is_dir() and not path.is_symlink(),
             "ACTIVE_DIAGNOSTIC_CANNOT_CREATE_CACHE")
        return None  # Do not call mkdir: avoid even a check/create race.
    return path.mkdir(*args,**kwargs)


def business_existing(runtime,identity):
    need(hasattr(runtime,"authority"),"ACTIVE_V2_REGISTERED_AUTHORITY_REQUIRED")
    with _control():
        _bound_authority(runtime.authority)
        value=runtime.authority.inspect(authority_module.business_id(runtime.authority.workspace_id,request_business(identity)))
    need(value is None or value["state"]=="RECORDED","AUTHORITY_EXISTING_REQUEST_RECONCILE_NO_RETRY")
    return value is not None


def session_result(result):
    if _CURRENT.get() is not None or os.environ.get("STOCKFISH_WORKER_CAPABILITY"):
        business=current("worker")["claim"]["business"]
        if business["operation"]=="PREMARKET":
            need(result.get("observation_date")==business["observation_date"] and
                 result.get("target_session")==business["target_session"],"ACTIVE_ACTUAL_SESSIONS_DIFFER_FROM_CLAIM")
    return result


def identity_return(legacy, filename, kind):
    from scripts.active_identity_v2 import bind_identity
    return bind_identity(legacy,root=Path(filename).resolve().parents[2],kind=kind)


def request_business(identity):
    if identity.get("backend") == "premarket":
        return authority_module.canonical_business(identity, operation="PREMARKET")
    if identity.get("backend") == "active-outcome":
        return authority_module.canonical_business(identity, operation="OUTCOME")
    path, payload = identity.get("path"), identity.get("payload")
    need(isinstance(payload, dict), "ACTIVE_EXPLICIT_BUSINESS_IDENTITY_REQUIRED")
    if path == "/api/strategy-experiments/run":
        return authority_module.canonical_business(payload, operation="CORE_COMPUTE")
    if isinstance(path, str) and path.startswith("/api/daily-research/runs/") and path.endswith("/outcomes"):
        return authority_module.canonical_business({**payload, "run_id":path.split("/")[-2]}, operation="OUTCOME")
    # The old empty ON_DEMAND_CAPTURE body has no business date/coverage. Never
    # mint an alias from the caller key to make such an operation writable.
    raise authority_module.AuthorityError("ACTIVE_BUSINESS_OPERATION_NOT_MAPPED")


def run_once(runtime, identity, action, validate):
    need(hasattr(runtime, "authority") and hasattr(runtime, "epoch_id"),
         "ACTIVE_V2_REGISTERED_AUTHORITY_REQUIRED")
    runtime.check_binding()
    started=time.monotonic()
    owner = runtime.authority
    business = request_business(identity)
    with _control():
        _bound_authority(owner)
        from scripts.active_runtime_v1 import verify
        verify(_SOURCE_ROOT)
        capacity=runtime.preflight()
        need(capacity["allowed"], "CAPACITY_PREFLIGHT_BLOCKED")
        existing=owner.inspect(authority_module.business_id(owner.workspace_id,business))
        need(existing is None or existing["state"]=="RECORDED","AUTHORITY_EXISTING_REQUEST_RECONCILE_NO_RETRY")
        if existing is None and business["operation"] in {"PREMARKET","CAPTURE"}:
            need(owner.source_scope_status()=="FRESH_EMPTY_WORKSPACE_NO_LEGACY_SOURCE_EFFECTS",
                 "LEGACY_SOURCE_SCOPES_UNRECONCILED")
        if existing is None and business["operation"]=="PREMARKET":
            from scripts.codex_premarket_preflight_v1 import plan
            from scripts.active_source_bootstrap_v1 import admission_allowed
            admission=plan(runtime)
            need(admission_allowed(admission,business),"ACTIVE_FULL_PREFLIGHT_OR_BOUNDED_SOURCE_BOOTSTRAP_REQUIRED")
        if "config" in runtime.profile:
            from stockfish_core.yaogu.premarket_workflow_v1 import load_config
            config=load_config(runtime.profile["config"])
        else:
            config={}
        limits = config.get("limits", {})
        counters = {name: limits[key] for name, key in (
            ("http_attempts", "max_http_attempts"), ("sdk_calls", "max_sdk_calls"),
            ("provider_requests", "max_requests")) if key in limits}
        counters.update({key: value for key, value in limits.items() if key.endswith("_read_bytes")})
        if identity.get("backend") == "active-outcome" and type(limits.get("max_seconds")) in (int, float):
            # Only this native pipeline measures its cumulative wall clock.
            # A legacy API family cannot silently adopt different limits;
            # the common authority rejects that change instead of resetting it.
            counters["outcome_wall_seconds"] = limits["max_seconds"]
        claim = owner.claim(business, request_metadata={
            "request_key":identity.get("request_key"), "engine":identity.get("execution_engine"),
            "entry_version":identity.get("schema_version")}, limits=counters, epoch_id=runtime.epoch_id)
    if claim["status"] == "REUSED":
        saved=claim["receipt"]
        original=saved.get("gateway_receipt",saved)
        validation=runtime._validated(validate,original)
        return dict(status="REUSED",operation_id=claim["business_id"],receipt=original,
            reused=True,business_id=claim["business_id"],validation=validation,new_action_calls=0,
            new_data_copy_bytes=0,elapsed_seconds=time.monotonic()-started)
    token = None
    try:
        with _control():
            owner.start(claim)
            capability = owner.derive_capability(claim, sorted(authority_module.ALLOWED_STAGES[business["operation"]]))
        value = dict(owner=owner, claim=claim, capability=capability)
        token = _CURRENT.set(value)
        result = action()
        need(isinstance(result, dict), "ACTIVE_RECEIPT_VALIDATION_REQUIRED")
        validation=runtime._validated(validate,result)
        runtime.check_binding()
        with _control():
            verify(_SOURCE_ROOT)
            after=runtime.preflight(expected_growth_bytes=0)
        # API acceptance is not terminal execution. The worker owns the final
        # authority transition, so an accepted request cannot be retried.
        if "job_id" not in result:
            terminal=result
            if business["operation"]=="PREMARKET":
                from scripts.active_delivery_bridge_v1 import prepare_authority_receipt
                terminal=prepare_authority_receipt(result,claim)
                with _control():
                    owner.charge(claim,{"supervisor_read_bytes":terminal["authority_receipt_validation_resources"]["returned_read_bytes"]})
            with _control():
                owner.finish(claim, success=True, receipt=terminal)
        return dict(status="ACCEPTED" if "job_id" in result else "RECORDED",operation_id=claim["business_id"],
            receipt=result,reused=False,business_id=claim["business_id"],validation=validation,
            new_action_calls=1,elapsed_seconds=time.monotonic()-started,action_seconds=time.monotonic()-started,
            capacity_after_allowed=after["allowed"])
    except BaseException:
        with _control():
            state=owner.inspect(claim["business_id"])
            if state and state["attempt_id"]==claim["attempt_id"] and state["state"] in authority_module.ACTIVE:
                owner.finish(claim, success=False, budget_known=False)
        raise
    finally:
        if token is not None:
            _CURRENT.reset(token)


def preflight_result(runtime, result):
    need(isinstance(result,dict),"ACTIVE_PREFLIGHT_RESULT_REQUIRED")
    with _control():
        owner=getattr(runtime,"authority",None)
        status=owner.source_scope_status() if owner is not None else "ACTIVE_V2_REGISTERED_AUTHORITY_REQUIRED"
    result={**result,"legacy_source_scope_status":status}
    if status!="FRESH_EMPTY_WORKSPACE_NO_LEGACY_SOURCE_EFFECTS":
        result["execution_admitted"]=False
        result["active_authority_blocker"]=status
        result["status"]="BLOCKED"
        result["reasons"]=list(dict.fromkeys([*result.get("reasons",[]),status]))
    from scripts.active_source_bootstrap_v1 import annotate_preflight
    from stockfish_core.yaogu.premarket_workflow_v1 import load_config
    with _control():
        config=load_config(runtime.profile["config"])
    return annotate_preflight(result,config,status)


_WORKFLOW_TICKET = ContextVar("stockfish_owned_workflow_ticket", default=None)


def _workflow_process_identity():
    import psutil
    from scripts.codex_sdk_tree_supervisor_v3 import _identity
    return _identity(psutil.Process())


def _workflow_launch_binding(job, entry, worker_args):
    from scripts.active_delivery_bridge_v1 import checked_worker_args
    from scripts.codex_runtime_recovery_v2 import reference
    path = Path(job).absolute()
    need(path.resolve(strict=True) == path and path.is_dir(), "ACTIVE_WORKFLOW_JOB_IDENTITY_REQUIRED")
    info = path.stat()
    return dict(job=str(path), device=info.st_dev, inode=info.st_ino, entry=entry,
        worker_args=checked_worker_args(entry, path, worker_args), configuration=reference(path/"configuration.json"))


def reserve_workflow_supervisor(job, *, entry, worker_args):
    """Reserve the sole intermediate supervisor before its actual Popen."""
    value = current("worker")
    need(value["claim"]["business"]["operation"] == "PREMARKET", "ACTIVE_PREMARKET_WORKER_OPERATION_REQUIRED")
    with _control():
        binding = dict(workflow=_workflow_launch_binding(job,entry,worker_args), admitted_by=_workflow_process_identity())
        return value["owner"].consume_once(value["capability"], stage="worker", effect_kind="PUBLISH",
            stable_effect_id="WORKFLOW_SUPERVISOR:"+value["claim"]["attempt_id"], binding=binding)


def reserve_workflow_worker(job, *, entry, worker_args):
    """The admitted supervisor reserves the real worker before native Popen."""
    import psutil
    from scripts.codex_sdk_tree_supervisor_v3 import _identity
    value = current("worker")
    need(value["claim"]["business"]["operation"] == "PREMARKET", "ACTIVE_PREMARKET_WORKER_OPERATION_REQUIRED")
    with _control():
        launch = _workflow_launch_binding(job,entry,worker_args)
        admitted = value["owner"].verify_effect(value["capability"], stage="worker", effect_kind="PUBLISH",
            stable_effect_id="WORKFLOW_SUPERVISOR:"+value["claim"]["attempt_id"])
        need(admitted["binding"]["workflow"] == launch and admitted["binding"]["admitted_by"] == _identity(psutil.Process(os.getppid())),
             "ACTIVE_WORKFLOW_SUPERVISOR_ADMISSION_CHANGED")
        binding = dict(workflow=launch, supervisor=_workflow_process_identity(), admission_effect=admitted["stable_effect_id"])
        return value["owner"].consume_once(value["capability"], stage="worker", effect_kind="WORKER_START",
            stable_effect_id="BUSINESS_WORKER", binding=binding)


def validate_workflow_worker(job, *, entry, worker_args, context=None):
    """Consume the real V3 live pipe once; a saved JSON ticket cannot substitute."""
    need(context is None, "ACTIVE_WORKFLOW_SAVED_CONTEXT_NOT_ACCEPTED")
    from scripts.codex_sdk_tree_supervisor_v3 import validate_worker_context
    value = current("worker")
    with _control():
        launch = _workflow_launch_binding(job,entry,worker_args)
        reservation = value["owner"].verify_effect(value["capability"], stage="worker", effect_kind="WORKER_START",
            stable_effect_id="BUSINESS_WORKER:"+value["claim"]["attempt_id"])
        need(reservation["binding"]["workflow"] == launch, "ACTIVE_WORKFLOW_WORKER_ADMISSION_CHANGED")
    context = validate_worker_context(Path(job)/"authority-supervisor")
    need(context["parent"] == reservation["binding"]["supervisor"] and context["child"] == _workflow_process_identity(),
         "ACTIVE_WORKFLOW_NATIVE_PROCESS_CHANGED")
    _WORKFLOW_TICKET.set(dict(reservation=reservation, context=context))
    return context


def complete_workflow_supervisor(job, *, entry, worker_args, report):
    value = current("worker")
    need(isinstance(report,dict) and report.get("status")=="COMPLETE" and report.get("root_reaped") is True
         and report.get("all_observed_descendants_gone") is True, "ACTIVE_WORKFLOW_NATIVE_CLEANUP_REQUIRED")
    with _control():
        reservation = value["owner"].verify_effect(value["capability"],stage="worker",effect_kind="PUBLISH",
            stable_effect_id="WORKFLOW_SUPERVISOR:"+value["claim"]["attempt_id"])
        need(reservation["binding"]["workflow"]==_workflow_launch_binding(job,entry,worker_args),
             "ACTIVE_WORKFLOW_SUPERVISOR_ADMISSION_CHANGED")
        value["owner"].complete_effect(value["capability"],reservation,
            receipt={"native_cleanup":"COMPLETE","root_reaped":True,"all_observed_descendants_gone":True})


@contextmanager
def effect(kind, semantic, *, global_effect=False, charge=None, source_scope=None):
    value = current("worker")
    with _control():
        key = authority_module.digest({"kind":kind, "semantic":semantic,
            "business_id":None if global_effect else value["claim"]["business_id"]})
        effect_kind = "PROVIDER_REQUEST" if global_effect else "PUBLISH"
        if kind == "JOB_WORKER" and value["claim"]["business"]["operation"] != "PREMARKET":
            effect_kind = "WORKER_START"
        if kind == "WORKFLOW_WORKER":
            ticket = _WORKFLOW_TICKET.get()
            need(ticket is not None and ticket["context"]["child"] == _workflow_process_identity(),
                 "ACTIVE_WORKFLOW_PREFORK_RESERVATION_REQUIRED")
            receipt = value["owner"].verify_effect(value["capability"], stage="worker", effect_kind="WORKER_START",
                stable_effect_id="BUSINESS_WORKER:"+value["claim"]["attempt_id"], expected_binding=ticket["reservation"]["binding"])
        else:
            receipt = value["owner"].consume_once(value["capability"], stage="worker",
                effect_kind=effect_kind, stable_effect_id=key, charge=charge, source_scope=source_scope)
    try:
        yield
    except BaseException:
        # Reservation remains STARTED/UNKNOWN. There is no retryable failure
        # state and no manufactured successful transport completion.
        raise
    else:
        with _control():
            value["owner"].complete_effect(value["capability"], receipt,
                receipt={"kind":kind,"completion":"CALL_RETURNED; SOURCE_REVALIDATION_SEPARATE"})


@contextmanager
def workflow_scope(role, config, job_dir=None):
    value = current("worker")
    from scripts.durable_stage_metrics_v1 import StageJournal
    root = Path(job_dir) if job_dir is not None else Path(config["state_root"])
    root.mkdir(parents=True, exist_ok=True)
    directory = root / ("authority-metrics-" + role.lower() + "-" + value["claim"]["attempt_id"])
    with effect("WORKFLOW_" + role, "primary"):
        journal = StageJournal(directory, value["claim"], role=role,
            test_fixture=config.get("capture", {}).get("allow_test_fixture") is True)
        token = _JOURNAL.set(journal)
        try:
            with journal.stage("workflow"):
                yield
        except BaseException:
            receipt=journal.finish("FAILED")
            if role=="WORKER":
                from scripts.active_delivery_bridge_v1 import publish_writer_receipt
                publish_writer_receipt(root,receipt)
            raise
        else:
            receipt=journal.finish(getattr(journal,"workflow_status","UNKNOWN"))
            if role=="WORKER":
                from scripts.active_delivery_bridge_v1 import publish_writer_receipt
                publish_writer_receipt(root,receipt)
        finally:
            _JOURNAL.reset(token)


def observed_result(result):
    journal=_JOURNAL.get()
    need(journal is not None,"ACTIVE_RESULT_JOURNAL_REQUIRED")
    if isinstance(result,tuple) and len(result)==2:
        code,payload=result
        complete=code in {0,2} and isinstance(payload,dict) and payload.get("status") in {"COMPLETE","COMPLETED_WITH_UNKNOWNS"}
    else:
        payload=result
        complete=isinstance(payload,dict) and payload.get("status") in {"COMPLETE","COMPLETED_WITH_UNKNOWNS"}
    journal.workflow_status="COMPLETE" if complete else "FAILED"
    journal.record("METRICS_SNAPSHOT",details={"workflow_result_status":payload.get("status") if isinstance(payload,dict) else "UNKNOWN",
        "source_authority_restored":False})
    return result


def budget_heartbeat(budget):
    journal=_JOURNAL.get()
    if journal is None or getattr(budget,"_active_telemetry",False):
        return
    now=time.monotonic()
    if now-getattr(budget,"_active_last_heartbeat",0)<2:
        return
    budget._active_last_heartbeat=now
    budget._active_telemetry=True
    try:
        journal.metrics_snapshot(budget.metrics)
        _charge_read_snapshot(budget)
    finally:
        budget._active_telemetry=False


@contextmanager
def stage(budget, name):
    journal = _JOURNAL.get()
    if journal is None and budget.job_dir is None:
        # Original source/cache diagnosis has no output directory and grants no
        # writer/network authority. Its own unchanged Budget still applies.
        yield
        return
    need(journal is not None, "ACTIVE_DURABLE_STAGE_JOURNAL_REQUIRED")
    try:
        with journal.stage(name, counters=lambda: budget.metrics):
            yield
    finally:
        _charge_read_snapshot(budget)


@contextmanager
def cleanup():
    journal = _JOURNAL.get()
    need(journal is not None, "ACTIVE_CLEANUP_JOURNAL_REQUIRED")
    with journal.stage("process_cleanup", cleanup=True):
        yield


def charge_read(budget, count):
    if _CONTROL.get() or not count:
        return
    # Reads themselves cannot create a business effect. Do not reopen SQLite
    # for every decoder buffer: phase entry, durable heartbeat, provider send,
    # every actual write and finalization still recheck the live capability.
    need(budget.job_dir is None or _JOURNAL.get() is not None,
         "ACTIVE_WRITER_BUDGET_REQUIRES_DURABLE_JOURNAL")


def _charge_read_snapshot(budget):
    value=current("metrics")
    reads=budget.metrics["returned_read_bytes"]
    previous=getattr(budget,"_authority_charged_reads",{})
    delta={key+"_read_bytes":count-previous.get(key,0) for key,count in reads.items() if count>previous.get(key,0)}
    if delta:
        with _control():
            value["owner"].charge(value["claim"],delta)
        budget._authority_charged_reads=dict(reads)


def reserve_transport(request, kind, ordinal):
    value = current("network")
    need(isinstance(request, dict) and request.get("endpoint") and isinstance(request.get("parameters"), dict),
         "ACTIVE_TRANSPORT_SEMANTICS_REQUIRED")
    semantic,scope=source_semantics(value["claim"]["business"],request["endpoint"],request["parameters"],
                                   provider=request.get("provider"))
    semantic.update(kind=kind, ordinal=ordinal)
    with _control():
        value["owner"].consume_once(value["capability"], stage="network",
            effect_kind="PROVIDER_REQUEST", stable_effect_id="transport:"+authority_module.digest(semantic),
            charge={"http_attempts" if kind == "HTTP" else "sdk_calls":1}, source_scope=scope)
    _NETWORK.set(True)


@contextmanager
def network_scope():
    # urllib3's nested pool/make_request/putrequest share the original token.
    # Inherit that admission; the outermost scope still restores False.
    token=_NETWORK.set(_NETWORK.get())
    try:
        yield
    finally:
        _NETWORK.reset(token)


@contextmanager
def provider_scope(endpoint, params):
    value = current("provider")
    semantic,scope=source_semantics(value["claim"]["business"],endpoint,params)
    with _control():
        value["owner"].consume_once(value["capability"],stage="provider",effect_kind="PROVIDER_REQUEST",
            stable_effect_id=authority_module.digest({"kind":"PROVIDER_REQUEST","semantic":semantic,"business_id":None}),
            charge={"provider_requests":1},source_scope=scope)
    # A normal Python return may be PARTIAL/FETCH_FAILED. Only the actual
    # complete provider result may close its transport reservations.
    yield


def transport_result(request, result):
    value=current("network")
    need(isinstance(result,dict),"ACTIVE_TRANSPORT_RESULT_REQUIRED")
    if result.get("status")!="COMPLETE" or result.get("pagination_complete") is not True:
        return result
    _,scope=source_semantics(value["claim"]["business"],request["endpoint"],request["parameters"],
                             provider=request.get("provider"))
    with _control():
        value["owner"].complete_source_request(value["capability"],stage="network",source_scope=scope,
            receipt={"status":"COMPLETE","pagination_complete":True,"source_authority_restored":False,
                     "completion":"PROVIDER_RETURNED; SOURCE_REVALIDATION_SEPARATE"})
    return result


def provider_result(answer):
    need(isinstance(answer,dict),"ACTIVE_PROVIDER_RESULT_REQUIRED")
    return transport_result(answer,answer)


def source_semantics(business, endpoint, params, *, provider=None):
    from stockfish_core.yaogu.provider_requests import ENDPOINTS,validate_request
    from stockfish_core.yaogu.request_queue import normalized_parameters
    normalized=normalized_parameters(validate_request(endpoint,params))
    actual_provider=ENDPOINTS[endpoint]["provider"]
    need(provider is None or provider==actual_provider,"ACTIVE_PROVIDER_IDENTITY_MISMATCH")
    window=source_window(business,normalized)
    dates=sorted(window.values())
    codes=normalized.get("codes",[])
    if normalized.get("ts_code"):
        codes=[normalized["ts_code"]]
    elif normalized.get("symbol"):
        # A theme is an independent source query, not every other theme on the
        # same session. Namespace its query identity, never a security fact.
        codes=["THEME:"+normalized["symbol"]]
    elif normalized.get("stock"):
        codes=[normalized["stock"]+"."+normalized["market"].upper()]
    semantic=dict(provider=actual_provider,endpoint=endpoint,parameters=normalized,source_window=window)
    scope=dict(schema="SOURCE_REQUEST_SCOPE_V1",provider=actual_provider,endpoint=endpoint,
        window_start=dates[0],window_end=dates[-1],codes=codes,
        request_semantic_id=authority_module.digest(semantic))
    return semantic,scope


def source_window(business, parameters):
    dated={key:parameters[key] for key in ("date","trade_date","start_date","end_date") if parameters.get(key)}
    if dated:
        return dated
    if business["operation"]=="PREMARKET":
        return {"start_date":business["observation_date"],"end_date":business["observation_date"]}
    window=business.get("parameters",{})
    need(window.get("window_start") and window.get("window_end"), "ACTIVE_UNDATED_SOURCE_WINDOW_REQUIRED")
    return {"start_date":window["window_start"],"end_date":window["window_end"]}


def job_create(service, kind, payload, job_id, retry_of):
    value = current("worker")
    need(retry_of is None, "ACTIVE_JOB_RETRY_FORBIDDEN")
    need(isinstance(payload, dict), "ACTIVE_JOB_PAYLOAD_REQUIRED")
    if value["claim"]["business"]["operation"] == "PREMARKET":
        # Native premarket is a direct workflow and needs no arbitrary HTTP
        # training/data-update jobs. Add a reviewed child contract if needed.
        need(False, "ACTIVE_PREMARKET_JOB_KIND_NOT_DECLARED")
    else:
        mapped = {"strategy_experiments":"CORE_COMPUTE", "daily_research_outcomes":"OUTCOME"}.get(kind)
        need(mapped is not None and authority_module.canonical_business(payload, operation=mapped) == value["claim"]["business"],
             "ACTIVE_JOB_BUSINESS_MISMATCH")
    stable = "job_" + authority_module.digest({"business_id":value["claim"]["business_id"], "kind":kind,"payload":payload})
    selected=job_id or stable
    with service._lock:
        if not hasattr(service, "_authority_contexts"):
            service._authority_contexts = {}
        if service.get(selected) is None:
            with _control():
                value["owner"].consume_once(value["capability"],stage="worker",
                    effect_kind="JOB_CREATE",stable_effect_id=stable)
        service._authority_contexts[selected] = value
    return selected


def job_dispatch(service, job_id, handler):
    value=current("worker")
    created=getattr(service,"_authority_contexts",{}).get(job_id)
    need(created is not None and created["claim"]["business_id"]==value["claim"]["business_id"]
         and created["claim"]["attempt_id"]==value["claim"]["attempt_id"],"ACTIVE_JOB_NOT_CREATED_BY_THIS_ATTEMPT")
    job=service.get(job_id)
    operation={"strategy_experiments":"CORE_COMPUTE","daily_research_outcomes":"OUTCOME"}.get(job["kind"])
    need(operation is not None and authority_module.canonical_business(job["payload"],operation=operation)==value["claim"]["business"],
         "ACTIVE_DISPATCH_BUSINESS_MISMATCH")
    with _control():
        reservation=value["owner"].consume_once(value["capability"],stage="worker",
            effect_kind="WORKER_START",stable_effect_id="BUSINESS_WORKER")
    service._authority_contexts[job_id]={**value,"worker_reservation":reservation}
    return service._executor.submit(service._run,job_id,handler)


@contextmanager
def job_worker(service, job_id):
    """Journal the actual JobService thread without changing its saved outcome.

    CPU/RSS belong to the shared service process; SQLite/OS bytes are unknown.
    Declared authority budgets and completeness of performance measurement are
    separate contracts. Missing metrics cannot be presented as zero resource use.
    """
    from scripts import durable_stage_metrics_v1 as metrics
    from scripts import codex_p0 as io
    value = getattr(service, "_authority_contexts", {}).get(job_id)
    need(value is not None, "ACTIVE_JOB_WITHOUT_BOUND_REQUEST")
    token = _CURRENT.set(value)
    journal_token = None
    journal = None
    failure = None
    job = None
    receipt = {"job_id": job_id, "state": "UNKNOWN",
        "performance_measurement_complete": False,
        "process_measurement_scope": "PROCESS_SHARED_WITH_OTHER_SERVICE_THREADS",
        "sql_returned_read_bytes": None, "kernel_io_bytes": None,
        "read_measurement_status": "UNKNOWN_NOT_INSTRUMENTED",
        "native_process_exit_and_tree_cleanup": "NOT_APPLICABLE_THREAD_IN_LIVE_SERVICE",
        "writer_receipt_publication_tail": "UNKNOWN_NOT_OBSERVED"}

    class _SavedUnsuccessfulJob(Exception):
        pass

    try:
        need(value.get("worker_reservation") is not None,"ACTIVE_WORKER_NOT_RESERVED_BEFORE_DISPATCH")
        current("worker")
        host_runtime = getattr(service.store, "host_runtime", None)
        if host_runtime is not None:
            with _control():
                host_runtime.check_binding()
        parent = Path(service.store.path).parent / "authority-job-metrics"
        parent.mkdir(mode=0o700, exist_ok=True)
        attempt = parent / metrics.identity(value["claim"])["attempt_id"]
        attempt.mkdir(mode=0o700)  # Never reuse or overwrite an interrupted attempt.
        journal = metrics.StageJournal(attempt / "events", value["claim"],
            test_fixture=any(part.startswith("TEST_FIXTURE") for part in attempt.parts))
        journal_token = _JOURNAL.set(journal)
        receipt["journal_directory"] = str(journal.root)
        journal.record("METRICS_SNAPSHOT", details={key: receipt[key] for key in
            ("process_measurement_scope", "sql_returned_read_bytes", "kernel_io_bytes",
             "read_measurement_status", "performance_measurement_complete")})
        try:
            with journal.stage("job_execute"):
                yield
                job = service.get(job_id)
                receipt["state"] = job["state"]
                if job["state"] != "SUCCEEDED":
                    raise _SavedUnsuccessfulJob()
        except _SavedUnsuccessfulJob:
            pass  # The native JobService already saved the precise error/cancellation.
    except BaseException as error:
        failure = error
        receipt["metrics_or_worker_error_type"] = type(error).__name__
    finally:
        telemetry_complete = False
        if journal is not None:
            try:
                status = "COMPLETE" if failure is None and receipt["state"] == "SUCCEEDED" else "FAILED"
                writer = journal.finish(status)
                receipt["writer_receipt"] = writer
                path = attempt / "writer-receipt.json"
                io.publish_new(path, writer)
                reader = metrics.delivery._Reader(2*1024**2, 10)
                saved_writer, reference = reader.read(path, control=True)
                need(saved_writer == writer, "ACTIVE_JOB_METRICS_WRITER_RECEIPT_CHANGED")
                reader.terminal()
                receipt["writer_receipt_reference"] = reference
                receipt["writer_receipt_verification_read_bytes"] = (
                    reader.returned_bytes + 2*len(metrics.encoded(writer)))
                receipt["writer_receipt_verification_scope"] = "PINNED_PUBLISHER_TWO_READS_PLUS_FINAL_NATIVE_READER"
                telemetry_complete = True
            except BaseException as error:
                receipt["metrics_terminal_error_type"] = type(error).__name__
                if failure is None:
                    failure = error
                elif hasattr(failure, "add_note"):
                    failure.add_note("Job metrics terminal failure retained: " + type(error).__name__)
        try:
            if value["claim"]["business"]["operation"] != "PREMARKET":
                successful = failure is None and telemetry_complete and receipt["state"] == "SUCCEEDED"
                with _control():
                    host_runtime = getattr(service.store, "host_runtime", None)
                    if host_runtime is not None:
                        try:
                            host_runtime.check_binding()
                        except BaseException as error:
                            successful = False
                            receipt["terminal_binding_error_type"] = type(error).__name__
                            if failure is None:
                                failure = error
                    if successful:
                        value["owner"].complete_effect(value["capability"],value["worker_reservation"],
                            receipt={"job_id":job_id,"state":receipt["state"]})
                    # True describes only the already-declared authority limits.
                    # SQL/OS I/O stays explicitly unknown in the durable receipt.
                    value["owner"].finish(value["claim"], success=successful,
                        receipt=receipt, budget_known=successful)
        except BaseException as error:
            if failure is None:
                failure = error
            elif hasattr(failure, "add_note"):
                failure.add_note("Authority terminal failure retained: " + type(error).__name__)
        finally:
            if journal_token is not None:
                _JOURNAL.reset(journal_token)
            _CURRENT.reset(token)
    if failure is not None:
        raise failure


def queue_attempt(row):
    current("provider")
    need(row["state"] in {"COMPLETE","BLOCKED"} or row["attempts"]==0,
         "ACTIVE_HISTORICAL_QUEUE_ATTEMPT_REQUIRES_RECONCILIATION")


def http_headers(headers, payload):
    if payload is not None:
        value = current("worker")
        headers["X-Stockfish-Worker-Capability"] = value["capability"]
        headers["X-Stockfish-Business-Id"] = value["claim"]["business_id"]
    return headers


def http_post(client, path, payload):
    from scripts.active_service_host_v1 import attested_post
    return attested_post(client,path,payload)


def bind_http(app):
    from flask import request, g
    @app.before_request
    def authority_request():
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            with _control():
                host=app.extensions.get("stockfish_active_host")
                if host:
                    host["runtime"].check_binding()
                    owner=host["runtime"].authority
                else:
                    owner = authority_module.authority_from_environment()
                capability = request.headers.get("X-Stockfish-Worker-Capability")
                claim = owner.verify_capability(capability, stage="worker",
                    bid=request.headers.get("X-Stockfish-Business-Id"))
                _bound_authority(owner)
                payload=request.get_json(silent=True)
                if request.path.startswith("/api/jobs/") and request.path.endswith("/cancel"):
                    job_id=request.path.split("/")[-2]
                    context=getattr(app.extensions["stockfish_core"].jobs,"_authority_contexts",{}).get(job_id)
                    need(context is not None and context["claim"]["business_id"]==claim["business_id"],
                         "ACTIVE_CANCELLATION_BUSINESS_MISMATCH")
                else:
                    business=request_business({"path":request.path,"payload":payload})
                    need(business==claim["business"],"ACTIVE_HTTP_BUSINESS_MISMATCH")
            g.stockfish_authority_token = _CURRENT.set(dict(owner=owner,claim=claim,capability=capability))
    @app.teardown_request
    def authority_teardown(_error):
        if hasattr(g, "stockfish_authority_token"):
            _CURRENT.reset(g.stockfish_authority_token)
    return app
