"""TEST_FIXTURE actual OS-isolated original environment verification, no epoch.

On unsupported operating systems the contract is explicit fail-closed. The
positive seven-step verifier is exercised on the supported Darwin runtime;
there is no mock verifier, lowered assertion, network access or service.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from scripts import active_environment_acceptance_v1 as env
from scripts import active_runtime_v1 as build
from scripts import business_authority_v1 as authority
from scripts import codex_runtime_recovery_v1 as legacy
from scripts import codex_runtime_recovery_v2 as recovery


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    root=tmp_path_factory.mktemp("TEST_FIXTURE_environment").resolve()
    state=root/"state";state.mkdir()
    owner=authority.Authority.initialize(root/"authority.sqlite3","TEST_FIXTURE_ENVIRONMENT",
        onboarding=dict(mode="EMPTY_NEW_WORKSPACE",state_roots=[str(state)]))
    base=root/"frozen_source";base.mkdir()
    publication=build.ROOT.parents[1]/"publish"/build.ROOT.parent.name/"stock-fish"
    for item in json.loads(build.BASE_MANIFEST.read_bytes())["files"]:
        raw=None
        for candidate in (build.ROOT,publication):
            path=candidate/item["path"]
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==item["sha256"]:
                raw=path.read_bytes();break
        assert raw is not None,"frozen preimage missing: "+item["path"]
        path=base/item["path"];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    source=root/"active"
    build.build(base,source,authority_binding=dict(database=str(owner.path),workspace_id=owner.workspace_id))
    assert owner.active_epoch() is None
    return dict(root=root,owner=owner,base=base,source=source,
                profile=dict(source_root=str(source),python=sys.executable))


@pytest.fixture(scope="module")
def accepted(prepared):
    if sys.platform!="darwin":
        return None
    c=prepared;owner=c["owner"]
    before=legacy.reference(owner.path)
    ref=env.accept_environment(Path(sys.executable),c["source"],c["base"],c["root"]/"TEST_FIXTURE_acceptance",
        authority_database=owner.path,workspace_id=owner.workspace_id)
    assert legacy.reference(owner.path)==before
    assert owner.active_epoch() is None and not list(c["root"].glob("registry*"))
    return ref


def test_os_sandbox_denies_network_private_environment_and_outside_writes_before_project_import(tmp_path):
    root=(tmp_path/"TEST_FIXTURE_os_guard").resolve();root.mkdir()
    if sys.platform!="darwin":
        with pytest.raises(ValueError,match="OS_SANDBOX_UNAVAILABLE"):
            env.sandbox_profile(root,Path(sys.executable))
        return
    runner=root/"runner.py";runner.write_bytes(Path(env.__file__).read_bytes())
    (root/".env.TEST_FIXTURE_DENIED").write_text("TEST_FIXTURE_NO_CREDENTIAL")
    profile=root/"sandbox.sb";profile.write_text(env.sandbox_profile(root,Path(sys.executable)))
    descendant="import socket\ns=socket.socket()\ntry:s.connect(('127.0.0.1',9))\nexcept OSError as e:assert e.errno==1\nelse:raise AssertionError('descendant escaped OS network isolation')"
    code="import runpy,subprocess,sys;from pathlib import Path;runpy.run_path("+repr(str(runner))+")[\"_sandbox_probe\"](Path("+repr(str(root))+"))\n"
    code+="p=subprocess.run([sys.executable,'-I','-B','-c',"+repr(descendant)+"],capture_output=True,text=True,timeout=10);assert p.returncode==0,p.stderr\n"
    result=subprocess.run(["/usr/bin/sandbox-exec","-f",str(profile),sys.executable,"-I","-B","-c",code],
        cwd=root,env=env._clean_environment(root),capture_output=True,text=True,timeout=30)
    (root/"stdout.txt").write_text(result.stdout);(root/"stderr.txt").write_text(result.stderr)
    assert result.returncode==0,result.stderr
    assert json.loads((root/"sandbox-probe.json").read_bytes())==dict(schema="ENVIRONMENT_OS_PROBE_V1",
        network_denied=True,foreign_write_denied=True,env_file_denied=True)


def test_unsupported_platform_cannot_create_acceptance_or_unlock_epoch(prepared,monkeypatch):
    c=prepared;destination=c["root"]/"TEST_FIXTURE_platform_rejection"
    with monkeypatch.context() as context:
        context.setattr(env.sys,"platform","TEST_FIXTURE_UNSUPPORTED")
        with pytest.raises(ValueError,match="OS_SANDBOX_UNAVAILABLE"):
            env.accept_environment(Path(sys.executable),c["source"],c["base"],destination,
                authority_database=c["owner"].path,workspace_id=c["owner"].workspace_id)
    assert not destination.exists() and c["owner"].active_epoch() is None


def test_home_owned_directory_is_readable_but_siblings_env_and_external_database_stay_denied(tmp_path):
    if sys.platform!="darwin":
        with pytest.raises(ValueError,match="OS_SANDBOX_UNAVAILABLE"):
            env.sandbox_profile(tmp_path,Path(sys.executable))
        return
    import pwd
    home=Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(strict=True)
    parent=Path(tempfile.mkdtemp(prefix="TEST_FIXTURE_environment_home_",dir=home))
    owned=parent/"nested"/"TEST_FIXTURE_owned";owned.mkdir(parents=True)
    neighbor=owned.with_name(owned.name+"_sibling");neighbor.mkdir()
    private=neighbor/"TEST_FIXTURE_private.txt";private.write_text("TEST_FIXTURE_NOT_PRIVATE_USER_DATA")
    database=parent/"TEST_FIXTURE_external.sqlite3";database.write_bytes(b"TEST_FIXTURE_NOT_A_REAL_DATABASE")
    runner=owned/"runner.py";runner.write_bytes(Path(env.__file__).read_bytes())
    environment=owned/".env.TEST_FIXTURE_DENIED";environment.write_text("TEST_FIXTURE_NOT_A_CREDENTIAL")
    alias=owned/"TEST_FIXTURE_neighbor_alias";alias.symlink_to(private)
    profile=owned/"sandbox.sb";profile.write_text(env.sandbox_profile(owned,Path(sys.executable)))
    code="""import sys,runpy,json
from pathlib import Path
root=Path(sys.argv[1])
module=runpy.run_path(str(root/'runner.py'))
module['_sandbox_probe'](root)
denied=[]
for name in sys.argv[2:]:
 try:Path(name).read_bytes()
 except PermissionError:denied.append(name)
 else:raise AssertionError('private neighbor read was permitted')
assert len(denied)==4
print(json.dumps({'owned_runner_read':True,'private_sibling_reads_denied':len(denied)}))
"""
    result=subprocess.run(["/usr/bin/sandbox-exec","-f",str(profile),sys.executable,"-I","-B","-c",code,
        str(owned),str(private),str(database),str(environment),str(alias)],cwd=owned,
        env=env._clean_environment(owned),capture_output=True,text=True,timeout=30)
    (parent/"stdout.txt").write_text(result.stdout);(parent/"stderr.txt").write_text(result.stderr)
    (tmp_path/"TEST_FIXTURE_home_evidence.json").write_text(json.dumps(dict(directory=str(parent),exit_code=result.returncode)))
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==dict(owned_runner_read=True,private_sibling_reads_denied=4)


def test_actual_seven_original_steps_and_active_manifest_without_business_epoch(prepared,accepted):
    c=prepared
    if sys.platform!="darwin":
        with pytest.raises(ValueError,match="OS_SANDBOX_UNAVAILABLE"):
            env.accept_environment(Path(sys.executable),c["source"],c["base"],c["root"]/"TEST_FIXTURE_unsupported",
                authority_database=c["owner"].path,workspace_id=c["owner"].workspace_id)
        assert not (c["root"]/"TEST_FIXTURE_unsupported").exists()
        return
    result=env.verify_acceptance(accepted,c["profile"],c["owner"])
    assert result==dict(verified=True,schema_version=env.VERSION,source_authority_restored=False,active_service_started=False)
    value=json.loads(Path(accepted["path"]).read_bytes())
    actual=json.loads(Path(value["legacy_environment"]["path"]).read_bytes())
    assert not actual["test_fixture"] and len(actual["steps"])==7
    assert [Path(step["path"]).stem for step in actual["steps"]]==list(env.STEPS)
    recovery._environment_binding(accepted,c["profile"],allow_test_fixture=False,authority=c["owner"])
    assert c["owner"].active_epoch() is None
    with c["owner"]._connect(readonly=True) as db:
        assert db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]==0


@pytest.mark.parametrize("field",["network_authorized","active_service_started","source_authority_restored","active_overlay_smoke_executed"])
def test_receipt_cannot_claim_extra_authority(prepared,accepted,field):
    if accepted is None:
        assert sys.platform!="darwin";return
    c=prepared;value=json.loads(Path(accepted["path"]).read_bytes());value[field]=True
    path=c["root"]/("TEST_FIXTURE_false_"+field+".json");path.write_bytes(env.canonical(value))
    with pytest.raises(ValueError,match="CANNOT_GRANT_BUSINESS_AUTHORITY"):
        env.verify_acceptance(legacy.reference(path),c["profile"],c["owner"])


def test_wrong_profile_and_copied_authority_fail_closed(prepared,accepted):
    if accepted is None:
        assert sys.platform!="darwin";return
    c=prepared;wrong={**c["profile"],"python":"/TEST_FIXTURE/wrong/python"}
    with pytest.raises(ValueError,match="PROFILE_OR_AUTHORITY_CHANGED"):
        env.verify_acceptance(accepted,wrong,c["owner"])
    clone=c["root"]/"TEST_FIXTURE_clone.sqlite3";clone.write_bytes(c["owner"].path.read_bytes())
    with pytest.raises(authority.AuthorityError,match="ORIGINAL_CARRIER_MISMATCH"):
        copied=authority.Authority(clone,c["owner"].workspace_id)
        env.verify_acceptance(accepted,c["profile"],copied)


def test_receipt_is_bound_to_exact_frozen_verifier_package(prepared,accepted):
    if accepted is None:
        assert sys.platform!="darwin";return
    c=prepared;value=json.loads(Path(accepted["path"]).read_bytes())
    # Preserve accepted artifacts; an additional undeclared file alone is enough
    # to disqualify this package. It cannot be silently excluded as test data.
    path=Path(value["frozen_root"])/"TEST_FIXTURE_UNDECLARED.json";path.write_text("{}")
    try:
        with pytest.raises(ValueError,match="FROZEN_PACKAGE_CLOSURE_CHANGED") as failure:
            env.verify_acceptance(accepted,c["profile"],c["owner"])
        (c["root"]/"TEST_FIXTURE_undeclared_source_rejection.json").write_text(json.dumps(
            dict(error=str(failure.value),undeclared_path=str(path))))
    finally:
        path.unlink()  # Only this test's new file; accepted original bytes stay intact.
    assert env.verify_acceptance(accepted,c["profile"],c["owner"])["verified"]


def test_real_layered_receipt_loads_from_actual_generated_recovery_tree(prepared,accepted):
    if accepted is None:
        assert sys.platform!="darwin";return
    from scripts import codex_workspace as workspace
    c=prepared;owner=c["owner"]
    assert owner.active_epoch() is None
    root=c["root"];state=root/"state";reports=root/"reports";reports.mkdir()
    config=root/"TEST_FIXTURE_config.json";config.write_text(json.dumps(dict(state_root=str(state))))
    registry=root/"registry";registry.mkdir()
    profile={**c["profile"],"schema_version":workspace.PROFILE_VERSION,"name":"TEST_FIXTURE_layered",
        "backend":"premarket","state_root":str(state),"report_root":str(reports),"config":str(config),
        "storage":dict(roots=[dict(name="fixture",path=str(root),role="runtime")],target_root="fixture",
            expected_growth_bytes=1024,min_free_bytes=0,max_managed_bytes=256*1024**2)}
    plan=recovery.plan_recovery(owner,[registry],profile,accepted,purpose="TEST_FIXTURE explicit layered admission")
    assert owner.active_epoch() is None
    result=recovery.apply_recovery(owner,plan,activation_authorization="TEST_FIXTURE_AUTHORIZED")
    assert result["active"] and not result["source_authority_restored"]
    recovery.load_runtime(profile["name"],registry).check_binding()
    owned=Path(json.loads(Path(accepted["path"]).read_bytes())["owned_root"])
    code="""import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from scripts import codex_workspace
runtime=codex_workspace.load_runtime('TEST_FIXTURE_layered',Path(sys.argv[2]))
runtime.check_binding()
import runpy
assert 'config' not in sys.modules
try:runpy.run_path(str(Path(sys.argv[1])/'app.py'),run_name='__main__')
except ValueError as error:assert 'ACTIVE_REGISTRATION_REQUIRES_COMPLETE_V2_RECOVERY_PLAN' in str(error),str(error)
else:raise AssertionError('legacy app started without explicit service bootstrap')
assert 'config' not in sys.modules
print('TEST_FIXTURE_LAYERED_ACTIVE_RECOVERY_VERIFIED_NO_BUSINESS')
"""
    clean=env._clean_environment(owned)
    clean.update(STOCKFISH_AUTHORITY_DATABASE=str(owner.path),STOCKFISH_AUTHORITY_WORKSPACE=owner.workspace_id)
    # This separate read-only integration must open the explicitly constructed
    # fixture authority, outside the narrower seven-step verifier directory.
    # Do not relax the actual environment acceptance policy or its receipt.
    integration_policy=root/"TEST_FIXTURE_active_recovery.sb"
    integration_policy.write_text(env.sandbox_profile(root,Path(sys.executable)))
    process=subprocess.run(["/usr/bin/sandbox-exec","-f",str(integration_policy),sys.executable,"-I","-B","-c",code,
        str(c["source"]),str(registry)],cwd=owned,env=clean,capture_output=True,text=True,timeout=60)
    (root/"active-recovery-stdout.txt").write_text(process.stdout)
    (root/"active-recovery-stderr.txt").write_text(process.stderr)
    assert process.returncode==0,process.stderr
    assert "TEST_FIXTURE_LAYERED_ACTIVE_RECOVERY_VERIFIED_NO_BUSINESS" in process.stdout
    with owner._connect(readonly=True) as db:
        assert db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM epochs WHERE state='ACTIVE'").fetchone()[0]==1
