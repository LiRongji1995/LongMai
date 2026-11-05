"""TEST_FIXTURE: complete-history migration, two generations and crash atomicity."""
from copy import deepcopy
import json
from pathlib import Path
import runpy

import pytest

from scripts import business_authority_v1 as a
from scripts import codex_runtime_recovery_v2 as r


@pytest.fixture
def setup(tmp_path):
    namespace=runpy.run_path(str(Path(__file__).with_name("test_codex_runtime_recovery_v1.py")))
    fixture=namespace["RecoveryFixture"]();fixture.setUp()
    authority=a.Authority.initialize(fixture.root/"TEST_FIXTURE_authority.sqlite3","TEST_FIXTURE_workspace",
        onboarding=dict(mode="FULL_LEGACY_IMPORT_PENDING",state_roots=[fixture.old_profile["state_root"]],
                        registry_roots=[str(fixture.registry)]))
    try:
        yield fixture,authority
    finally:
        fixture.doCleanups()


def plan(fixture,authority,**kwargs):
    return r.plan_recovery(authority,[fixture.registry],fixture.new_profile,fixture.environment,
                          purpose="TEST_FIXTURE explicit migration",allow_test_fixture=True,**kwargs)


def test_plan_readonly_and_import_preserves_failed_unknown(setup):
    fixture,authority=setup
    before={str(p):r.reference(p) for p in fixture.registry.rglob("*.json")}
    proposal=plan(fixture,authority)
    assert before=={str(p):r.reference(p) for p in fixture.registry.rglob("*.json")}
    result=r.apply_recovery(authority,proposal,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    assert result["active"] and not result["source_authority_restored"]
    for path,reference in before.items():
        assert r.reference(path)==reference
    runtime=r.load_runtime(fixture.new_profile["name"],fixture.registry,allow_test_fixture=True)
    assert runtime.epoch_id==proposal["epoch_id"]
    with pytest.raises(a.AuthorityError,match="RECONCILE_NO_RETRY"):
        authority.claim({**fixture.existing,"request_key":"new"},epoch_id=proposal["epoch_id"])
    saved=authority.inspect(a.business_id(authority.workspace_id,fixture.existing))
    assert saved["state"]=="FAILED_OR_UNKNOWN" and not saved["budget_known"]
    fresh=authority.claim(fixture.fresh_identity(),epoch_id=proposal["epoch_id"])
    assert fresh["state"]=="CLAIMED"


def test_two_generations_and_single_active_epoch(setup):
    fixture,authority=setup
    first=plan(fixture,authority);r.apply_recovery(authority,first,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    next_profile=deepcopy(fixture.new_profile)
    next_profile["name"]="TEST_FIXTURE-second-generation"
    state=Path(next_profile["state_root"])/"epochs/second";state.mkdir(parents=True)
    config=fixture.root/"data/second-config.json"
    config.write_text(json.dumps(dict(state_root=str(state),limits=dict(max_requests=100))))
    next_profile.update(state_root=str(state),config=str(config))
    second=r.plan_recovery(authority,[fixture.registry],next_profile,fixture.environment,
        parents=[first["epoch_id"]],purpose="TEST_FIXTURE second physical epoch",allow_test_fixture=True)
    r.apply_recovery(authority,second,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    assert authority.active_epoch()["epoch_id"]==second["epoch_id"]
    with authority._connect(readonly=True) as connection:
        assert dict(connection.execute("SELECT epoch_id,state FROM epochs"))=={
            first["epoch_id"]:"RETIRED",second["epoch_id"]:"ACTIVE"}
        assert connection.execute("SELECT COUNT(*) FROM imported").fetchone()[0]==1
    with pytest.raises(a.AuthorityError,match="EPOCH_NOT_ACTIVE"):
        r.load_runtime(fixture.new_profile["name"],fixture.registry,allow_test_fixture=True)
    with pytest.raises(a.AuthorityError,match="RECONCILE_NO_RETRY"):
        authority.claim(fixture.existing,epoch_id=second["epoch_id"])


def test_unreconciled_legacy_source_scopes_block_new_windows_across_epochs(setup):
    fixture,authority=setup
    first=plan(fixture,authority)
    unresolved="LEGACY_SOURCE_SCOPES_UNRECONCILED"
    assert first["legacy_source_scope_status"]==unresolved
    first_result=r.apply_recovery(authority,first,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    assert first_result["legacy_source_scope_status"]==unresolved
    network_calls=[]

    def exercise(epoch,day):
        value=dict(operation="CAPTURE",provider="TEST_FIXTURE",dataset="RAW",
                   window_start=day,window_end=day,codes=["TEST000001"])
        current=authority.claim(value,epoch_id=epoch)
        for stage in ("network","provider","capture"):
            with pytest.raises(a.AuthorityError,match=unresolved):
                authority.derive_capability(current,[stage])
                network_calls.append(stage)
        # A legitimate no-network token must not acquire a provider effect by
        # calling the lower-level ledger directly with a different stage.
        token=authority.derive_capability(current,["storage","worker","metrics"])
        assert authority.verify_capability(token,stage="storage")["business_id"]==current["business_id"]
        scope=dict(schema="SOURCE_REQUEST_SCOPE_V1",provider="TEST_FIXTURE",endpoint="RAW",
                   window_start=day,window_end=day,codes=["TEST000001"],request_semantic_id=a.digest(value))
        with pytest.raises(a.AuthorityError,match=unresolved):
            authority.consume_once(token,stage="storage",effect_kind="PROVIDER_REQUEST",
                                   stable_effect_id="TEST_FIXTURE:"+day,source_scope=scope)
            network_calls.append("direct-transport")
        authority.finish(current,success=False,budget_known=False)
        # Source uncertainty does not disable independent, saved-input work.
        core=authority.claim(dict(operation="CORE_COMPUTE",input_id="TEST_FIXTURE_"+day,strategy_id="R2"),epoch_id=epoch)
        core_token=authority.derive_capability(core,["storage","worker","metrics"])
        assert authority.verify_capability(core_token,stage="worker")["business_id"]==core["business_id"]
        authority.finish(core,success=True,receipt=dict(TEST_FIXTURE="no-network-compute"))

    exercise(first["epoch_id"],"2026-11-06")
    profile=deepcopy(fixture.new_profile);profile["name"]="TEST_FIXTURE_scope_second_epoch"
    state=Path(profile["state_root"])/"epochs/source-scope-second";state.mkdir(parents=True)
    config=fixture.root/"data/TEST_FIXTURE_scope_second.json"
    config.write_text(json.dumps(dict(state_root=str(state),limits=dict(max_requests=100))))
    profile.update(state_root=str(state),config=str(config))
    second=r.plan_recovery(authority,[fixture.registry],profile,fixture.environment,
        parents=[first["epoch_id"]],purpose="TEST_FIXTURE scope unknown survives epoch",allow_test_fixture=True)
    assert second["legacy_source_scope_status"]==unresolved
    result=r.apply_recovery(authority,second,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    assert result["legacy_source_scope_status"]==unresolved
    exercise(second["epoch_id"],"2026-12-01")
    assert not network_calls
    with authority._connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_scopes").fetchone()[0]==0
        assert connection.execute("SELECT COUNT(*) FROM effects WHERE effect_kind='PROVIDER_REQUEST'").fetchone()[0]==0
    with pytest.raises(a.AuthorityError,match="RECONCILE_NO_RETRY"):
        authority.claim(fixture.existing,epoch_id=second["epoch_id"])


@pytest.mark.parametrize("failure_phase",["AFTER_PREPARE","AFTER_REGISTRY_PUBLICATION","BEFORE_ATOMIC_COMMIT"])
def test_interruption_never_grants_half_committed_epoch(setup,failure_phase):
    fixture,authority=setup;proposal=plan(fixture,authority)
    def fail(phase):
        if phase==failure_phase:
            raise RuntimeError("TEST_FIXTURE crash")
    with pytest.raises(RuntimeError,match="crash"):
        r.apply_recovery(authority,proposal,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True,_test_hook=fail)
    assert authority.active_epoch() is None
    with authority._connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM imported").fetchone()[0]==0
    with pytest.raises(a.AuthorityError,match="ACTIVE_EPOCH_REQUIRED"):
        authority.claim(fixture.fresh_identity(),epoch_id=proposal["epoch_id"])
    result=r.apply_recovery(authority,proposal,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    assert result["active"]


def test_all_legacy_branches_and_states_imported(setup):
    fixture,authority=setup
    fixture.add_old({**fixture.existing,"request_key":"TEST_FIXTURE-sibling-completed"},"RECORDED")
    fixture.add_old({**fixture.fresh_identity(),"request_key":"TEST_FIXTURE-sibling-unknown"},"SUBMITTING")
    proposal=plan(fixture,authority)
    assert len(proposal["inventory"]["operations"])==3
    r.apply_recovery(authority,proposal,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    assert authority.inspect(a.business_id(authority.workspace_id,fixture.existing))["state"]=="FAILED_OR_UNKNOWN"
    assert authority.inspect(a.business_id(authority.workspace_id,fixture.fresh_identity()))["state"]=="FAILED_OR_UNKNOWN"


def test_added_legacy_branch_after_plan_refused(setup):
    fixture,authority=setup;proposal=plan(fixture,authority)
    fixture.add_old(fixture.fresh_identity(),"SUBMITTING")
    with pytest.raises(a.AuthorityError,match="INVENTORY_CHANGED"):
        r.apply_recovery(authority,proposal,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    assert authority.active_epoch() is None


def test_unknown_operation_shape_requires_exact_explicit_mapping(setup):
    fixture,authority=setup
    fixture.add_old(dict(backend="local",operation="unrecognized",request_key="TEST_FIXTURE"),"FAILED_OR_UNKNOWN")
    with pytest.raises(a.AuthorityError,match="OPERATION_EXPLICIT_REQUIRED"):
        plan(fixture,authority)
    files=list((fixture.old.directory/"operations").glob("*.json"))
    ambiguous=next(p for p in files if json.loads(p.read_text())["identity"]["backend"]=="local")
    mappings={r.reference(ambiguous)["sha256"]:dict(operation="CORE_COMPUTE",input_id="input_A",strategy_id="R2")}
    proposal=plan(fixture,authority,mappings=mappings)
    assert len(proposal["inventory"]["operations"])==2


def test_second_generation_cannot_remap_already_imported_failed_operation(setup):
    fixture,authority=setup
    fixture.add_old(dict(backend="local",operation="TEST_FIXTURE_ambiguous_core",request_key="TEST_FIXTURE_old"),"FAILED_OR_UNKNOWN")
    ambiguous=next(path for path in (fixture.old.directory/"operations").glob("*.json")
                   if json.loads(path.read_text())["identity"].get("backend")=="local")
    ref=r.reference(ambiguous)
    original=dict(operation="CORE_COMPUTE",input_id="TEST_FIXTURE_original_input",strategy_id="R2")
    changed={**original,"input_id":"TEST_FIXTURE_relabelled_input"}
    first=plan(fixture,authority,mappings={ref["sha256"]:original})
    r.apply_recovery(authority,first,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    failed=authority.inspect(a.business_id(authority.workspace_id,original))
    assert failed["state"]=="FAILED_OR_UNKNOWN"
    profile=deepcopy(fixture.new_profile);profile["name"]="TEST_FIXTURE_mapping_second_generation"
    state=Path(profile["state_root"])/"epochs/remapping";state.mkdir(parents=True)
    config=fixture.root/"data/TEST_FIXTURE_remapping.json"
    config.write_text(json.dumps(dict(state_root=str(state),limits=dict(max_requests=100))))
    profile.update(state_root=str(state),config=str(config))
    with pytest.raises(a.AuthorityError,match="IMPORTED_BUSINESS_MAPPING_CHANGED"):
        second=r.plan_recovery(authority,[fixture.registry],profile,fixture.environment,
            parents=[first["epoch_id"]],mappings={ref["sha256"]:changed},
            purpose="TEST_FIXTURE changed old semantic mapping",allow_test_fixture=True)
        r.apply_recovery(authority,second,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
        authority.claim(changed,epoch_id=second["epoch_id"])
    assert authority.active_epoch()["epoch_id"]==first["epoch_id"]
    assert authority.inspect(failed["business_id"])["attempt_id"]==failed["attempt_id"]
    assert authority.inspect(a.business_id(authority.workspace_id,changed)) is None
    assert not (fixture.registry/"runtimes"/(profile["name"]+".json")).exists()
    # Also exercise the transactional import boundary independently of plan.
    operation=next(row for row in first["inventory"]["operations"] if row["reference"]["sha256"]==ref["sha256"])
    with pytest.raises(a.AuthorityError,match="IMPORTED_BUSINESS_MAPPING_CHANGED"):
        with authority._connect() as connection:
            r._import(connection,authority,{**operation,"business":a.canonical_business(changed)})
    assert authority.inspect(failed["business_id"])["state"]=="FAILED_OR_UNKNOWN"


def test_all_prior_budget_evidence_is_bound_and_accumulated(setup):
    fixture,authority=setup
    fixture.add_old({**fixture.existing,"request_key":"TEST_FIXTURE-branch-two"},"FAILED_OR_UNKNOWN")
    evidence={}
    for index,path in enumerate(sorted((fixture.old.directory/"operations").glob("*.json"))):
        sha=r.reference(path)["sha256"]
        proof=fixture.root/("TEST_FIXTURE_budget_"+str(index)+".json")
        proof.write_text(json.dumps(dict(schema="LEGACY_BUDGET_EVIDENCE_V1",operation_sha256=sha,
            counters_complete=True,limits=dict(requests=10),spent=dict(requests=index+2))))
        evidence[sha]=proof
    proposal=plan(fixture,authority,budget_evidence=evidence)
    r.apply_recovery(authority,proposal,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    saved=authority.inspect(a.business_id(authority.workspace_id,fixture.existing))
    assert saved["budget_known"] and saved["cumulative_spent"]==dict(requests=5)
    assert saved["remaining_limits"]==dict(requests=5)


def test_no_migration_with_active_request(setup):
    fixture,authority=setup;first=plan(fixture,authority)
    r.apply_recovery(authority,first,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    authority.claim(fixture.fresh_identity(),epoch_id=first["epoch_id"])
    # Existing applied epoch cannot be replayed to clear its active request.
    with pytest.raises(a.AuthorityError):
        r.apply_recovery(authority,first,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    assert authority.active_epoch()["epoch_id"]==first["epoch_id"]


def test_fixture_cannot_activate_production(setup):
    fixture,authority=setup;proposal=plan(fixture,authority)
    with pytest.raises(a.AuthorityError,match="TEST_FIXTURE_NOT_PRODUCTION"):
        r.apply_recovery(authority,proposal,activation_authorization="TEST_FIXTURE_AUTHORIZED")


def test_symlink_and_replaced_original_control_refused(setup):
    fixture,authority=setup;proposal=plan(fixture,authority)
    path=Path(proposal["inventory"]["operations"][0]["reference"]["path"])
    data=path.read_bytes();path.rename(path.with_suffix(".preserved"));path.write_bytes(data)
    with pytest.raises(a.AuthorityError,match="INVENTORY_CHANGED"):
        r.apply_recovery(authority,proposal,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)


@pytest.mark.parametrize("mutation",["old_operation","new_sibling_registration"])
def test_active_runtime_rechecks_complete_ancestor_inventory(setup,mutation):
    fixture,authority=setup;proposal=plan(fixture,authority)
    r.apply_recovery(authority,proposal,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    runtime=r.load_runtime(fixture.new_profile["name"],fixture.registry,allow_test_fixture=True)
    if mutation=="old_operation":
        fixture.add_old(fixture.fresh_identity(),"SUBMITTING")
    else:
        sibling=deepcopy(fixture.old_profile);sibling["name"]="TEST_FIXTURE_bypass_sibling"
        profile=fixture.root/"TEST_FIXTURE_sibling.json";profile.write_text(json.dumps(sibling))
        fixture_ns=runpy.run_path(str(Path(__file__).with_name("test_codex_runtime_recovery_v1.py")))
        fixture_ns["w"].register_profile(profile,fixture.registry)
    with pytest.raises(a.AuthorityError,match="INVENTORY_CHANGED"):
        runtime.check_binding()


def test_current_epoch_scope_cannot_hide_new_legacy_failure(setup):
    fixture,authority=setup;proposal=plan(fixture,authority)
    r.apply_recovery(authority,proposal,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    runtime=r.load_runtime(fixture.new_profile["name"],fixture.registry,allow_test_fixture=True)
    identity=fixture.fresh_identity()
    key=r.w._digest({"scope":runtime.scope,"identity":identity})
    path=runtime.directory/"operations"/(key+".json")
    legacy=r.w.Runtime(fixture.registry,runtime.profile,runtime.binding)
    def fail():
        raise RuntimeError("TEST_FIXTURE old writer failure")
    with pytest.raises(RuntimeError,match="TEST_FIXTURE old writer failure"):
        legacy.run_once(identity,fail,lambda _:True)
    assert json.loads(path.read_text())["phase"]=="FAILED_OR_UNKNOWN"
    original=r.reference(path)
    with pytest.raises(a.AuthorityError,match="COMPLETE_INVENTORY_CHANGED"):
        runtime.check_binding()
    with pytest.raises(a.AuthorityError,match="COMPLETE_INVENTORY_CHANGED"):
        r.load_runtime(fixture.new_profile["name"],fixture.registry,allow_test_fixture=True)
    assert r.reference(path)==original
    assert authority.active_epoch()["epoch_id"]==proposal["epoch_id"]


def test_declared_second_registry_cannot_be_omitted(setup):
    fixture,authority=setup
    second=fixture.root/"TEST_FIXTURE_second_registry";second.mkdir()
    with authority._connect() as connection:
        onboarding=json.loads(connection.execute("SELECT value FROM meta WHERE key='onboarding'").fetchone()[0])
        onboarding["registry_roots"].append(str(second))
        connection.execute("UPDATE meta SET value=? WHERE key='onboarding'",(json.dumps(onboarding),))
    with pytest.raises(a.AuthorityError,match="COMPLETE_ONBOARDING_REGISTRIES"):
        plan(fixture,authority)


@pytest.mark.parametrize("mutation,reason",[
    ("registry_inventory","COMPLETE_ONBOARDING_REGISTRIES_REQUIRED"),
    ("primary_registry","PRIMARY_REGISTRY_MISMATCH"),
    ("outside_state","NEW_STATE_OUTSIDE_WORKSPACE"),
    ("nonempty_state","NEW_EMPTY_OWNED_STATE_REQUIRED"),
    ("config_state","CONFIG_STATE_MISMATCH"),
    ("config_reference","CONFIG_STATE_MISMATCH"),
    ("environment_executable","ENVIRONMENT_EXECUTION_BINDING_CHANGED"),
    ("environment_sources","ENVIRONMENT_EXECUTION_BINDING_CHANGED"),
    ("parents","ALL_BRANCH_HEADS_REQUIRED"),
    ("source_authority","CANNOT_GRANT_SOURCE_OR_DEFAULT_AUTHORITY"),
])
def test_external_plan_digest_is_not_semantic_authorization(setup,mutation,reason):
    fixture,authority=setup;proposal=plan(fixture,authority)
    before={str(path):r.reference(path) for path in fixture.registry.rglob("*.json")}
    alternate=fixture.root/"TEST_FIXTURE_undeclared_registry";alternate.mkdir()
    if mutation=="registry_inventory":
        proposal["inventory"]=r.inventory([alternate]);proposal["registry_root"]=str(alternate)
    elif mutation=="primary_registry":
        proposal["registry_root"]=str(alternate)
    elif mutation=="outside_state":
        state=fixture.root/"TEST_FIXTURE_foreign_state";state.mkdir()
        config=fixture.root/"data/TEST_FIXTURE_foreign_state_config.json"
        config.write_text(json.dumps(dict(state_root=str(state),limits=dict(max_requests=100))))
        proposal["profile"].update(state_root=str(state),config=str(config))
        proposal["profile"]["storage"]["roots"].append(dict(name="TEST_FIXTURE_foreign",path=str(state),role="runtime"))
        proposal["binding"]=r.w._binding(proposal["profile"]);proposal["config"]=r.reference(config)
    elif mutation=="nonempty_state":
        (Path(proposal["profile"]["state_root"])/"TEST_FIXTURE_existing.json").write_text('{}')
    elif mutation=="config_state":
        path=Path(proposal["profile"]["config"])
        path.write_text(json.dumps(dict(state_root=fixture.old_profile["state_root"],limits=dict(max_requests=100))))
        proposal["config"]=r.reference(path)
    elif mutation=="config_reference":
        path=fixture.root/"data/TEST_FIXTURE_different_config_carrier.json"
        path.write_bytes(Path(proposal["profile"]["config"]).read_bytes())
        proposal["config"]=r.reference(path)
    elif mutation in {"environment_executable","environment_sources"}:
        value=json.loads(Path(proposal["environment"]["path"]).read_text())
        if mutation=="environment_executable":
            value["executable"]=r.reference(proposal["profile"]["config"])
        else:
            value["source_refs"]=value["source_refs"][1:]
        path=fixture.root/"TEST_FIXTURE_modified_environment.json";path.write_text(json.dumps(value))
        proposal["environment"]=r.reference(path)
    elif mutation=="parents":
        proposal["parents"]=["TEST_FIXTURE_invented_parent"]
    else:
        proposal["source_authority_restored"]=True
    proposal["epoch_id"]="epoch_"+a.digest({key:value for key,value in proposal.items() if key!="epoch_id"})
    with pytest.raises(a.AuthorityError,match=reason):
        r.apply_recovery(authority,proposal,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    assert authority.active_epoch() is None
    with authority._connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM epochs").fetchone()[0]==0
        assert connection.execute("SELECT COUNT(*) FROM imported").fetchone()[0]==0
    assert before=={str(path):r.reference(path) for path in fixture.registry.rglob("*.json")}
    assert list(alternate.iterdir())==[]


def test_changed_interpreter_requires_new_acceptance_then_second_epoch(setup):
    fixture,authority=setup
    first=plan(fixture,authority);r.apply_recovery(authority,first,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    runtime=r.load_runtime(fixture.new_profile["name"],fixture.registry,allow_test_fixture=True)
    # Only the fixture launcher is replaced. Its original symlink target (the
    # real selected interpreter) is never opened for writing or changed.
    launcher=fixture.venv/"bin/python"
    assert launcher.is_symlink()
    launcher.unlink();launcher.write_text("TEST_FIXTURE nonexecuted replacement interpreter")
    launcher.chmod(0o700)
    with pytest.raises(ValueError,match="PHYSICAL_IDENTITY_CHANGED"):
        runtime.check_binding()
    new_acceptance=r.old.verify_environment(launcher,fixture.venv,fixture.root/"TEST_FIXTURE_second_acceptance",
        Path(fixture.old_profile["source_root"]),_fixture_verifier=lambda result,report:{"TEST_FIXTURE":True},
        allow_test_fixture=True)
    profile=deepcopy(fixture.new_profile);profile["name"]="TEST_FIXTURE_second_interpreter_epoch"
    state=Path(profile["state_root"])/"epochs/changed-python";state.mkdir(parents=True)
    config=fixture.root/"data/TEST_FIXTURE_changed_python_config.json"
    config.write_text(json.dumps(dict(state_root=str(state),limits=dict(max_requests=100))))
    profile.update(state_root=str(state),config=str(config))
    proposal=r.plan_recovery(authority,[fixture.registry],profile,new_acceptance,
        parents=[first["epoch_id"]],purpose="TEST_FIXTURE explicit second environment acceptance",allow_test_fixture=True)
    r.apply_recovery(authority,proposal,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    accepted=r.load_runtime(profile["name"],fixture.registry,allow_test_fixture=True)
    assert accepted.epoch_id==proposal["epoch_id"]
    with pytest.raises(a.AuthorityError,match="RECONCILE_NO_RETRY"):
        authority.claim(fixture.existing,epoch_id=accepted.epoch_id)


def test_active_source_manifest_is_pinned_to_authority_and_rechecked(setup):
    fixture,authority=setup
    source=fixture.root/"TEST_FIXTURE_active_source";(source/"scripts").mkdir(parents=True)
    for name in ("bootstrap_daily_runtime.py","runtime_doctor.py","runtime_environment_smoke.py"):
        (source/"scripts"/name).write_text("# TEST_FIXTURE source, never executed\n")
    manifest=source/"ACTIVE_RUNTIME_MANIFEST.json"
    value=dict(schema="ACTIVE_RUNTIME_V1",authority_binding=dict(database=str(authority.path),
        workspace_id=authority.workspace_id,physical=list(authority.physical)),output_sha256={})
    manifest.write_text(json.dumps(value))
    profile=deepcopy(fixture.new_profile);profile["source_root"]=str(source)
    environment=r.old.verify_environment(fixture.venv/"bin/python",fixture.venv,
        fixture.root/"TEST_FIXTURE_active_source_acceptance",source,
        _fixture_verifier=lambda result,report:{"TEST_FIXTURE":True},allow_test_fixture=True)
    proposal=r.plan_recovery(authority,[fixture.registry],profile,environment,
        purpose="TEST_FIXTURE active manifest",allow_test_fixture=True)
    assert proposal["active_source_manifest"]==r.reference(manifest)
    r.apply_recovery(authority,proposal,activation_authorization="TEST_FIXTURE_AUTHORIZED",allow_test_fixture=True)
    runtime=r.load_runtime(profile["name"],fixture.registry,allow_test_fixture=True)
    assert runtime.active_source_attested
    value["output_sha256"]["changed.py"]="a"*64
    manifest.write_text(json.dumps(value))
    with pytest.raises(a.AuthorityError,match="ACTIVE_SOURCE_MANIFEST_CHANGED"):
        runtime.check_binding()


def test_apply_python_and_cli_need_explicit_activation_authorization(setup):
    fixture,authority=setup;proposal=plan(fixture,authority)
    with pytest.raises(a.AuthorityError,match="EXPLICIT_ACTIVATION_AUTHORIZATION"):
        r.apply_recovery(authority,proposal,activation_authorization="",allow_test_fixture=True)
    with pytest.raises(a.AuthorityError,match="EXPLICIT_ACTIVATION_SWITCH"):
        r.main(["apply","--authority-database",str(authority.path),"--workspace-id",authority.workspace_id,
            "--plan",str(fixture.root/"TEST_FIXTURE_missing_plan.json"),"--plan-sha256","a"*64,
            "--authorization-id","TEST_FIXTURE_AUTHORIZED"])
    assert authority.active_epoch() is None
