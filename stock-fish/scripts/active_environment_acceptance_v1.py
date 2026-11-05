"""OS-isolated frozen-runtime acceptance plus exact active-source verification.

The unchanged seven-step verifier runs against a private, byte-exact frozen
package. This does not grant business capabilities or pretend the active HTTP
service was started. A separate receipt binds that runtime evidence to every
input/output of one active tree and its original authority carrier.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

ROOT=Path(__file__).resolve().parents[1]
VERSION="ACTIVE_ENVIRONMENT_ACCEPTANCE_V1"
STEPS=("native-identity","pip-check","installed-identity","doctor-rules",
       "doctor-daily","doctor-review","app-cli-database-smoke")
LIMITS=dict(max_seconds=900,tree_rss_bytes=3*1024**3,self_rss_bytes=3*1024**3,
            output_bytes=64*1024**2,supervisor_read_bytes=256*1024**2,
            sample_interval_seconds=.5)


def need(value,code):
    if not value:
        raise ValueError(code)


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()


def _modules():
    if str(ROOT) not in sys.path:
        sys.path.insert(0,str(ROOT))
    from scripts import active_runtime_v1 as build
    from scripts import business_authority_v1 as authority
    from scripts import codex_runtime_recovery_v1 as legacy
    return build,authority,legacy


def _read(path):
    _,_,legacy=_modules()
    before=legacy.reference(Path(path),maximum=64*1024**2)
    value=json.loads(Path(path).read_bytes())
    need(legacy.reference(Path(path),maximum=64*1024**2)==before,"ENVIRONMENT_CARRIER_CHANGED")
    return value,before


def _write(path,value):
    with Path(path).open("xb") as stream:
        stream.write(canonical(value));stream.flush();os.fsync(stream.fileno())


def _canonical_directory(path):
    path=Path(path).absolute()
    need(path.resolve(strict=True)==path and path.is_dir(),"ENVIRONMENT_CANONICAL_DIRECTORY_REQUIRED")
    return path


def _python(python):
    path=Path(python).absolute()
    need(path==Path(sys.executable).absolute() and path==Path(sys.prefix)/"bin/python"
         and (Path(sys.prefix)/"pyvenv.cfg").is_file() and sys.flags.isolated,
         "ENVIRONMENT_CURRENT_ISOLATED_REGISTERED_PYTHON_REQUIRED")
    return path


def sandbox_profile(owned,python):
    """OS rules apply before Python/site startup and survive all descendants."""
    need(sys.platform=="darwin" and Path("/usr/bin/sandbox-exec").is_file(),
         "ENVIRONMENT_OS_SANDBOX_UNAVAILABLE")
    import pwd
    owned=_canonical_directory(owned);_python(python)
    home=Path(pwd.getpwuid(os.getuid()).pw_dir);prefixes={Path(sys.prefix).resolve(),Path(sys.base_prefix).resolve()}
    read_roots=prefixes|{owned}
    # The original fd-based verifier opens each ancestor directory. Only those
    # directory objects, not their other children, are exceptions to home reads.
    # The new owned report/verifier tree may itself live below the real home;
    # this exact subtree does not grant access to its private siblings.
    parents={p for root in read_roots for p in root.parents if p==home or p.is_relative_to(home)}
    quote=lambda value:json.dumps(str(value))
    exceptions=" ".join("(require-not (subpath "+quote(p)+"))" for p in sorted(read_roots,key=str))
    exceptions+=" "+" ".join("(require-not (literal "+quote(p)+"))" for p in sorted(parents,key=str))
    return ("(version 1)\n(allow default)\n(deny network*)\n"
        "(deny file-write* (require-not (subpath "+quote(owned)+")))\n"
        "(deny file-read-data (require-all (subpath "+quote(home)+") "+exceptions+"))\n"
        "(deny file-read-data (regex "+json.dumps(r"/\.env(\.|$)")+"))\n"
        "(deny file-read-data (require-all (regex "+json.dumps(r"\.(db|sqlite|sqlite3)(-wal|-shm)?$")+" )"
        " (require-not (subpath "+quote(owned)+"))))\n")


def _clean_environment(owned):
    # No ambient provider credentials, proxy, application configuration or
    # business capability crosses the environment-verification boundary.
    return dict(PATH="/usr/bin:/bin",TMPDIR=str(owned/"tmp"),
        MPLCONFIGDIR=str(owned/"mpl"),XDG_CACHE_HOME=str(owned/"cache"),
        NUMBA_CACHE_DIR=str(owned/"numba"),PYTHONDONTWRITEBYTECODE="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",PIP_CONFIG_FILE=os.devnull)


def _sandbox_probe(owned):
    """Refuse an unsandboxed private child before importing any project code."""
    observed={}
    stream=socket.socket()
    try:
        stream.connect(("127.0.0.1",9))
    except OSError as error:
        observed["network_denied"]=error.errno==1
    finally:
        stream.close()
    foreign=owned.parent/(owned.name+"-FORBIDDEN-WRITE")
    try:
        with foreign.open("xb") as stream:
            stream.write(b"TEST_FIXTURE_UNSANDBOXED_PROBE")
    except PermissionError:
        observed["foreign_write_denied"]=True
    else:
        foreign.unlink();observed["foreign_write_denied"]=False
    try:
        (owned/".env.TEST_FIXTURE_DENIED").read_bytes()
    except PermissionError:
        observed["env_file_denied"]=True
    else:
        observed["env_file_denied"]=False
    need(observed==dict(network_denied=True,foreign_write_denied=True,env_file_denied=True),
         "ENVIRONMENT_OS_ISOLATION_NOT_ESTABLISHED")
    _write(owned/"sandbox-probe.json",dict(schema="ENVIRONMENT_OS_PROBE_V1",**observed))


def _child(owned,control_sha):
    owned=_canonical_directory(owned)
    need(any(part.startswith("TEST_FIXTURE") for part in owned.parts),"ENVIRONMENT_ISOLATED_TEST_FIXTURE_REQUIRED")
    control_path=owned/"control.json";raw=control_path.read_bytes()
    need(hashlib.sha256(raw).hexdigest()==control_sha,"ENVIRONMENT_CONTROL_CHANGED")
    control=json.loads(raw)
    need(control==dict(schema="ENVIRONMENT_CONTROL_V1",owned=str(owned),
        frozen_root=str(owned/"frozen_source"),python=sys.executable),"ENVIRONMENT_CHILD_FIXED_CONTRACT_REQUIRED")
    _sandbox_probe(owned)
    # This is the exact frozen package; no active gateway is loaded or exempted.
    sys.path.insert(0,str(owned/"frozen_source"))
    from scripts import codex_runtime_recovery_v1 as legacy
    result=legacy.verify_environment(Path(sys.executable),Path(sys.executable).parent.parent,
        owned/"reports",owned/"frozen_source")
    _write(owned/"legacy-result-reference.json",result)
    return 0


def _frozen_contract(build,active,contract):
    expected=set(active["input_sha256"])-set(build.EXTENSIONS)
    need(contract.get("schema")=="ACTIVE_RUNTIME_BASE_V1"
         and contract["published_commit"]==active["frozen_commit"]
         and {row["path"] for row in contract["files"]}==expected
         and all(active["input_sha256"][row["path"]]==row["sha256"] for row in contract["files"]),
         "ENVIRONMENT_FROZEN_ACTIVE_INPUT_CLOSURE_MISMATCH")


def _command(owned,python):
    control=dict(schema="ENVIRONMENT_CONTROL_V1",owned=str(owned),
                 frozen_root=str(owned/"frozen_source"),python=str(python))
    return ["/usr/bin/sandbox-exec","-f",str(owned/"sandbox.sb"),str(python),"-I","-B",
            str(owned/"acceptance-child.py"),"_child","--owned-root",str(owned),
            "--control-sha256",hashlib.sha256(canonical(control)).hexdigest()]


def accept_environment(python,active_root,base_root,destination,*,authority_database,workspace_id):
    build,authority,legacy=_modules();python=_python(python)
    # Fail before creating output on platforms without the required OS guard.
    need(sys.platform=="darwin" and Path("/usr/bin/sandbox-exec").is_file(),"ENVIRONMENT_OS_SANDBOX_UNAVAILABLE")
    source=_canonical_directory(active_root);base=_canonical_directory(base_root)
    owner=authority.Authority(authority_database,workspace_id)
    active=build.verify(source)
    binding=dict(database=str(owner.path),workspace_id=workspace_id,physical=list(owner.physical))
    need(active["authority_binding"]==binding,"ENVIRONMENT_ACTIVE_AUTHORITY_MISMATCH")
    name="scripts/active_environment_acceptance_v1.py"
    need(active["output_sha256"].get(name)==hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         "ENVIRONMENT_ACTIVE_ACCEPTANCE_IMPLEMENTATION_REQUIRED")
    contract=json.loads(build.BASE_MANIFEST.read_bytes());_frozen_contract(build,active,contract)
    owned=Path(destination).absolute()
    need(not owned.exists() and not owned.is_symlink() and owned.parent.resolve(strict=True)==owned.parent
         and any(part.startswith("TEST_FIXTURE") for part in owned.parts),"ENVIRONMENT_NEW_OWNED_TEST_FIXTURE_REQUIRED")
    owned.mkdir(mode=0o700);(owned/"tmp").mkdir()
    _write(owned/"frozen-contract.json",contract)
    frozen=owned/"frozen_source";frozen.mkdir()
    for item in contract["files"]:
        raw=build._safe(base,item["path"]).read_bytes()
        need(len(raw)==item["bytes"] and hashlib.sha256(raw).hexdigest()==item["sha256"],
             "ENVIRONMENT_FROZEN_VERIFIER_PREIMAGE_CHANGED")
        path=frozen/item["path"];path.parent.mkdir(parents=True,exist_ok=True)
        with path.open("xb") as stream:stream.write(raw)
    runner=owned/"acceptance-child.py"
    with runner.open("xb") as stream:stream.write(Path(__file__).read_bytes())
    (owned/".env.TEST_FIXTURE_DENIED").write_text("TEST_FIXTURE_NOT_A_CREDENTIAL\n")
    control=dict(schema="ENVIRONMENT_CONTROL_V1",owned=str(owned),frozen_root=str(frozen),python=str(python))
    _write(owned/"control.json",control)
    profile=owned/"sandbox.sb";profile.write_text(sandbox_profile(owned,python))
    command=_command(owned,python)
    from scripts.codex_sdk_tree_supervisor_v3 import run_supervised
    result=run_supervised(command,cwd=owned,env=_clean_environment(owned),limits=LIMITS,
                          control_dir=owned/"supervisor")
    _write(owned/"supervisor-result.json",result)
    need(result["status"]=="COMPLETE" and result["cleanup"]["root_reaped"]
         and result["cleanup"]["all_observed_descendants_gone"],"ENVIRONMENT_VERIFICATION_PROCESS_FAILED")
    legacy_ref,_=_read(owned/"legacy-result-reference.json")
    value=dict(schema_version=VERSION,status="ACCEPTED",source_root=str(source),python=str(python),
        authority_binding=binding,active_manifest=legacy.reference(source/"ACTIVE_RUNTIME_MANIFEST.json"),
        owned_root=str(owned),frozen_root=str(frozen),frozen_contract=legacy.reference(owned/"frozen-contract.json"),
        legacy_environment=legacy_ref,os_profile=legacy.reference(profile),
        os_probe=legacy.reference(owned/"sandbox-probe.json"),runner=legacy.reference(runner),
        control=legacy.reference(owned/"control.json"),supervisor=legacy.reference(owned/"supervisor-result.json"),
        runtime_smoke_scope="EXACT_FROZEN_PREIMAGE_ORIGINAL_SEVEN_STEPS",
        active_source_scope="ALL_DECLARED_ACTIVE_INPUT_AND_OUTPUT_BYTES",
        active_overlay_smoke_executed=False,active_service_started=False,
        packages_installed=False,network_authorized=False,source_authority_restored=False,historical_pit_restored=False)
    _write(owned/"acceptance.json",value)
    ref=legacy.reference(owned/"acceptance.json")
    verify_acceptance(ref,dict(source_root=str(source),python=str(python)),owner)
    return ref


def verify_acceptance(environment,profile,owner):
    build,authority,legacy=_modules()
    need(isinstance(owner,authority.Authority),"ENVIRONMENT_ORIGINAL_AUTHORITY_REQUIRED")
    value,ref=_read(environment["path"])
    need(ref==environment and value.get("schema_version")==VERSION and value.get("status")=="ACCEPTED",
         "ENVIRONMENT_ACCEPTED_LAYERED_RECEIPT_REQUIRED")
    need(value["source_root"]==profile["source_root"] and value["python"]==profile["python"]
         and value["authority_binding"]==dict(database=str(owner.path),workspace_id=owner.workspace_id,physical=list(owner.physical)),
         "ENVIRONMENT_PROFILE_OR_AUTHORITY_CHANGED")
    need(value.get("runtime_smoke_scope")=="EXACT_FROZEN_PREIMAGE_ORIGINAL_SEVEN_STEPS"
         and value.get("active_source_scope")=="ALL_DECLARED_ACTIVE_INPUT_AND_OUTPUT_BYTES",
         "ENVIRONMENT_EXECUTION_SCOPE_CHANGED")
    owner.source_scope_status()  # Recheck the original carrier; no capability.
    need(all(value.get(key) is False for key in ("active_overlay_smoke_executed","active_service_started",
         "packages_installed","network_authorized","source_authority_restored","historical_pit_restored")),
         "ENVIRONMENT_RECEIPT_CANNOT_GRANT_BUSINESS_AUTHORITY")
    owned=_canonical_directory(value["owned_root"]);source=_canonical_directory(profile["source_root"])
    active=build.verify(source)
    need(legacy.reference(source/"ACTIVE_RUNTIME_MANIFEST.json")==value["active_manifest"]
         and active["authority_binding"]==value["authority_binding"],"ENVIRONMENT_ACTIVE_SOURCE_CHANGED")
    for name in ("frozen_contract","os_profile","os_probe","runner","control","supervisor"):
        need(Path(value[name]["path"]).is_relative_to(owned),"ENVIRONMENT_PROOF_OUTSIDE_OWNED_ROOT")
        legacy._verify_refs([value[name]])
    contract,_=_read(value["frozen_contract"]["path"]);_frozen_contract(build,active,contract)
    frozen=_canonical_directory(value["frozen_root"])
    need(frozen==owned/"frozen_source","ENVIRONMENT_FROZEN_ROOT_CHANGED")
    entries=list(frozen.rglob("*"))
    need(not any(path.is_symlink() for path in entries) and
         {path.relative_to(frozen).as_posix() for path in entries if path.is_file()}=={row["path"] for row in contract["files"]},
         "ENVIRONMENT_FROZEN_PACKAGE_CLOSURE_CHANGED")
    for item in contract["files"]:
        raw=build._safe(frozen,item["path"]).read_bytes()
        need(len(raw)==item["bytes"] and hashlib.sha256(raw).hexdigest()==item["sha256"],"ENVIRONMENT_FROZEN_VERIFIER_PREIMAGE_CHANGED")
    need(Path(value["os_profile"]["path"]).read_text()==sandbox_profile(owned,Path(profile["python"])),
         "ENVIRONMENT_OS_POLICY_CHANGED")
    need(value["runner"]["sha256"]==hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),"ENVIRONMENT_RUNNER_CHANGED")
    probe,_=_read(value["os_probe"]["path"])
    need(probe==dict(schema="ENVIRONMENT_OS_PROBE_V1",network_denied=True,foreign_write_denied=True,env_file_denied=True),
         "ENVIRONMENT_OS_ISOLATION_NOT_ESTABLISHED")
    supervisor,_=_read(value["supervisor"]["path"])
    need(supervisor["status"]=="COMPLETE" and supervisor["cleanup"]["root_reaped"]
         and supervisor["cleanup"]["all_observed_descendants_gone"],"ENVIRONMENT_SUPERVISOR_NOT_COMPLETE")
    from scripts import codex_sdk_tree_supervisor_v3 as native
    claim_raw,_=native.reference(supervisor["claim"]["path"],supervisor["claim"])
    claim=json.loads(claim_raw)
    need(claim["argv_sha256"]==native.digest(_command(owned,Path(profile["python"])))
         and claim["cwd"]==str(owned) and claim["limits"]==LIMITS
         and claim["source_capability_issued"] is False and claim["retry_authorized"] is False,
         "ENVIRONMENT_FIXED_PROCESS_CONTRACT_CHANGED")
    control,_=_read(value["control"]["path"])
    need(control==dict(schema="ENVIRONMENT_CONTROL_V1",owned=str(owned),frozen_root=str(frozen),python=profile["python"]),
         "ENVIRONMENT_CHILD_FIXED_CONTRACT_REQUIRED")
    legacy_profile=dict(python=profile["python"],source_root=str(frozen))
    legacy._environment(value["legacy_environment"],legacy_profile)
    accepted,_=_read(value["legacy_environment"]["path"])
    need(accepted.get("test_fixture") is False and accepted["executable"]==legacy.reference(Path(profile["python"]).resolve(strict=True))
         and [Path(step["path"]).stem for step in accepted["steps"]]==list(STEPS),
         "ENVIRONMENT_REAL_FROZEN_STEPS_REQUIRED")
    expected=[legacy.reference(frozen/"scripts"/name) for name in
        ("bootstrap_daily_runtime.py","runtime_doctor.py","runtime_environment_smoke.py","codex_runtime_recovery_v1.py")]
    need(accepted["source_refs"]==expected,"ENVIRONMENT_FROZEN_EXECUTION_SOURCE_CHANGED")
    smoke,_=_read(owned/"reports/TEST_FIXTURE_smoke/result.json")
    need(smoke.get("status")=="PASSED" and all(smoke.get(key) is True for key in
        ("cli_help","flask_in_process","durable_atomic_job","database_reopened","immutability"))
         and all(smoke.get(key) is False for key in ("http_listener_started","scheduler_started","real_market_data_used")),
         "ENVIRONMENT_ORIGINAL_SMOKE_ASSERTIONS_REQUIRED")
    return dict(verified=True,schema_version=VERSION,source_authority_restored=False,active_service_started=False)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    sub=parser.add_subparsers(dest="command",required=True)
    child=sub.add_parser("_child",allow_abbrev=False)
    child.add_argument("--owned-root",type=Path,required=True);child.add_argument("--control-sha256",required=True)
    accept=sub.add_parser("accept",allow_abbrev=False)
    for name in ("python","active-root","base-root","destination","authority-database"):
        accept.add_argument("--"+name,type=Path,required=True)
    accept.add_argument("--workspace-id",required=True)
    check=sub.add_parser("verify",allow_abbrev=False)
    for name in ("receipt","active-root","python","authority-database"):
        check.add_argument("--"+name,type=Path,required=True)
    check.add_argument("--workspace-id",required=True)
    args=parser.parse_args(argv)
    if args.command=="_child":
        return _child(args.owned_root,args.control_sha256)
    if args.command=="verify":
        _,authority,legacy=_modules()
        owner=authority.Authority(args.authority_database,args.workspace_id)
        value=verify_acceptance(legacy.reference(args.receipt),
            dict(source_root=str(args.active_root),python=str(args.python)),owner)
        print(json.dumps(value));return 0
    result=accept_environment(args.python,args.active_root,args.base_root,args.destination,
        authority_database=args.authority_database,workspace_id=args.workspace_id)
    print(json.dumps(dict(status="ENVIRONMENT_ACCEPTED_NOT_ACTIVATED",receipt=result,source_authority_restored=False)))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
