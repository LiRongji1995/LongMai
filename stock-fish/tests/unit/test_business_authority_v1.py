"""TEST_FIXTURE: cross-version business admission and durable effect oracles."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from scripts import business_authority_v1 as a


@pytest.fixture
def authority(tmp_path):
    state=tmp_path/"TEST_FIXTURE_state"
    state.mkdir()
    authority=a.Authority.initialize(tmp_path/"TEST_FIXTURE_authority.sqlite3","TEST_FIXTURE_workspace",
        onboarding=dict(mode="EMPTY_NEW_WORKSPACE",state_roots=[str(state)]))
    with authority._connect() as connection:
        connection.execute("INSERT INTO epochs VALUES (?,?,?,?,?)",("TEST_FIXTURE_epoch","[]","{}","ACTIVE",a.stamp()))
    return authority


def business():
    return dict(backend="premarket",observation_date="2026-11-04",target_session="2026-11-05")


def claim(authority, **kwargs):
    return authority.claim(business(),epoch_id="TEST_FIXTURE_epoch",**kwargs)


def source_scope(codes=("TEST000001",),start="2026-11-04",end="2026-11-04",**updates):
    value=dict(schema="SOURCE_REQUEST_SCOPE_V1",provider="TEST_FIXTURE",endpoint="RAW",
               window_start=start,window_end=end,codes=list(codes))
    value.update(updates)
    value.setdefault("request_semantic_id",a.digest(value))
    return value


def outcome(authority, inputs, *, run="TEST_FIXTURE_run_A", limits=None, **aliases):
    return authority.claim(dict(operation="OUTCOME",run_id=run,input_ids=inputs,**aliases),
        epoch_id="TEST_FIXTURE_epoch",limits=dict(bytes=10) if limits is None else limits)


@pytest.mark.parametrize("budget_known",[True,False])
def test_outcome_failed_family_cannot_escape_by_genuine_new_input(authority,budget_known):
    first=outcome(authority,["TEST_FIXTURE_B"])
    authority.charge(first,dict(bytes=2))
    authority.finish(first,success=False,budget_known=budget_known)
    with pytest.raises(a.AuthorityError,match="OUTCOME_FAMILY_RECONCILE_NO_RETRY"):
        outcome(authority,["TEST_FIXTURE_B","TEST_FIXTURE_C"],request_key="renamed",engine="shared")
    assert outcome(authority,["TEST_FIXTURE_C"],run="TEST_FIXTURE_independent_run")["state"]=="CLAIMED"


def test_outcome_family_requires_new_fact_and_frozen_limits(authority):
    first=outcome(authority,["TEST_FIXTURE_A","TEST_FIXTURE_B"])
    authority.finish(first,success=True,receipt=dict(TEST_FIXTURE="first"))
    assert outcome(authority,["TEST_FIXTURE_B","TEST_FIXTURE_A"],request_key="new")["status"]=="REUSED"
    with pytest.raises(a.AuthorityError,match="OUTCOME_FAMILY_NEW_INPUT_REQUIRED"):
        outcome(authority,["TEST_FIXTURE_A"])
    with pytest.raises(a.AuthorityError,match="OUTCOME_FAMILY_LIMITS_CHANGED"):
        outcome(authority,["TEST_FIXTURE_C"],limits=dict(bytes=20))
    second=outcome(authority,["TEST_FIXTURE_C"])
    authority.finish(second,success=True,receipt=dict(TEST_FIXTURE="genuine new source"))
    with pytest.raises(a.AuthorityError,match="OUTCOME_FAMILY_NEW_INPUT_REQUIRED"):
        outcome(authority,["TEST_FIXTURE_A","TEST_FIXTURE_C"])


@pytest.mark.parametrize("method",["charge","effect"])
def test_outcome_family_actual_cost_is_aggregated_without_copying_prior_spend(authority,method):
    first=outcome(authority,["TEST_FIXTURE_B"])
    authority.charge(first,dict(bytes=7))
    authority.finish(first,success=True,receipt=dict(TEST_FIXTURE="cost 7"))
    second=outcome(authority,["TEST_FIXTURE_B","TEST_FIXTURE_C"])
    token=authority.derive_capability(second,["storage"])
    with authority._connect(readonly=True) as connection:
        before=connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    with pytest.raises(a.AuthorityError,match="OUTCOME_FAMILY_CUMULATIVE_BUDGET_EXCEEDED"):
        if method=="charge":authority.charge(second,dict(bytes=4))
        else:authority.consume_once(token,stage="storage",effect_kind="PUBLISH",
            stable_effect_id="TEST_FIXTURE_PRODUCT",charge=dict(bytes=4))
    with authority._connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]==before
        assert connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0]==0
    assert authority.inspect(first["business_id"])["cumulative_spent"]==dict(bytes=7)
    assert authority.inspect(second["business_id"])["cumulative_spent"]=={}
    authority.charge(second,dict(bytes=3))
    assert authority.inspect(second["business_id"])["cumulative_spent"]==dict(bytes=3)


def test_outcome_batch_cannot_bridge_families(authority):
    with pytest.raises(a.AuthorityError,match="OUTCOME_SINGLE_RUN_REQUIRED"):
        authority.claim(dict(operation="OUTCOME",run_ids=["TEST_FIXTURE_A","TEST_FIXTURE_B"],input_id="TEST_FIXTURE_input"),
            epoch_id="TEST_FIXTURE_epoch",limits=dict(bytes=10))


def test_legacy_batch_stays_readable_but_blocks_each_intersecting_family(authority):
    legacy=a.canonical_business(dict(operation="OUTCOME",run_ids=["TEST_FIXTURE_run_A","TEST_FIXTURE_run_B"],
        input_id="TEST_FIXTURE_legacy_input"))
    bid=a.business_id(authority.workspace_id,legacy)
    with authority._connect() as connection:
        sequence=authority._event(connection,bid,"TEST_FIXTURE_legacy_attempt","RECORDED",{})
        connection.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?)",(bid,a.canonical(legacy).decode(),
            "RECORDED","TEST_FIXTURE_legacy_attempt",sequence,'{"bytes":10}','{"bytes":1}',1,
            '{"TEST_FIXTURE":"original legacy batch"}',a.stamp()))
    assert authority.inspect(bid)["business"]["run_ids"]==legacy["run_ids"]
    assert authority.claim(legacy,epoch_id="TEST_FIXTURE_epoch")["status"]=="REUSED"
    for run in legacy["run_ids"]:
        with pytest.raises(a.AuthorityError,match="LEGACY_BATCH_RECONCILIATION_REQUIRED"):
            outcome(authority,["TEST_FIXTURE_new_input"],run=run)
        view=authority.outcome_family_budget(run)
        assert view["budget_known"] is False and view["remaining_family_limits"] is None


def test_family_observation_and_explicit_resume_keep_actual_row_costs(authority):
    first=outcome(authority,["TEST_FIXTURE_B"])
    authority.charge(first,dict(bytes=4))
    authority.finish(first,success=True,receipt=dict(TEST_FIXTURE="first"))
    second=outcome(authority,["TEST_FIXTURE_B","TEST_FIXTURE_C"])
    authority.charge(second,dict(bytes=2))
    authority.finish(second,success=False,budget_known=True)
    resumed=authority.authorize_resume(second["business_id"],previous_attempt_id=second["attempt_id"],
        authorization_id="TEST_FIXTURE_EXPLICIT_RECONCILIATION",reconciliation_sha256="a"*64)
    with pytest.raises(a.AuthorityError,match="FAMILY_CUMULATIVE_BUDGET_EXCEEDED"):
        authority.charge(resumed,dict(bytes=5))
    authority.charge(resumed,dict(bytes=4))
    saved=authority.inspect(second["business_id"])
    assert saved["cumulative_spent"]==dict(bytes=6) and saved["remaining_limits"]==dict(bytes=4)
    assert saved["remaining_limits_scope"]=="THIS_BUSINESS" and saved["outcome_family_constraint"] is True
    reopened=a.Authority(authority.path,authority.workspace_id)
    view=reopened.outcome_family_budget("TEST_FIXTURE_run_A")
    assert view["recorded_spent"]==dict(bytes=10) and view["remaining_family_limits"]==dict(bytes=0)
    assert view["budget_known"] is True and view["execution_authorized"] is False
    assert reopened.inspect(first["business_id"])["cumulative_spent"]==dict(bytes=4)
    authority.finish(resumed,success=False,budget_known=False)
    assert reopened.outcome_family_budget("TEST_FIXTURE_run_A")["remaining_family_limits"] is None
    assert outcome(authority,["TEST_FIXTURE_B"])["status"]=="REUSED"


def test_exhausted_outcome_family_rejects_new_revision_before_claim(authority):
    first=outcome(authority,["TEST_FIXTURE_B"],limits=dict(bytes=10,unused=0))
    authority.charge(first,dict(bytes=10))
    authority.finish(first,success=True,receipt=dict(TEST_FIXTURE="all budget spent"))
    with pytest.raises(a.AuthorityError,match="OUTCOME_FAMILY_BUDGET_EXHAUSTED"):
        outcome(authority,["TEST_FIXTURE_B","TEST_FIXTURE_C"],limits=dict(bytes=10,unused=0))
    with authority._connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0]==1
        assert connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0]==0
    assert outcome(authority,["TEST_FIXTURE_B"])["status"]=="REUSED"


@pytest.mark.parametrize("entry",["start","worker_effect","job_effect"])
def test_admission_cost_exhaustion_cannot_start_outcome_worker(authority,entry):
    current=outcome(authority,["TEST_FIXTURE_B"])
    token=authority.derive_capability(current,["worker"])
    authority.charge(current,dict(bytes=10))
    with pytest.raises(a.AuthorityError,match="OUTCOME_FAMILY_BUDGET_EXHAUSTED"):
        if entry=="start":authority.start(current)
        else:authority.consume_once(token,stage="worker",effect_kind="JOB_CREATE" if entry=="job_effect" else "WORKER_START",
                                    stable_effect_id="renamed")
    assert authority.inspect(current["business_id"])["state"]=="CLAIMED"
    with authority._connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0]==0


def test_exhausted_family_cannot_mint_even_an_explicit_resume_attempt(authority):
    first=outcome(authority,["TEST_FIXTURE_B"])
    authority.charge(first,dict(bytes=4))
    authority.finish(first,success=True,receipt=dict(TEST_FIXTURE="known original cost"))
    second=outcome(authority,["TEST_FIXTURE_C"])
    authority.charge(second,dict(bytes=6))
    authority.finish(second,success=False,budget_known=True)
    with pytest.raises(a.AuthorityError,match="OUTCOME_FAMILY_BUDGET_EXHAUSTED"):
        authority.authorize_resume(second["business_id"],previous_attempt_id=second["attempt_id"],
            authorization_id="TEST_FIXTURE_EXPLICIT_RECONCILIATION",reconciliation_sha256="a"*64)
    saved=authority.inspect(second["business_id"])
    assert saved["attempt_id"]==second["attempt_id"] and saved["state"]=="FAILED_OR_UNKNOWN"


def test_actual_processes_different_outcome_revisions_have_one_family_winner(authority):
    first=outcome(authority,["TEST_FIXTURE_B"])
    authority.finish(first,success=True,receipt=dict(TEST_FIXTURE="known parent"))
    root=Path(__file__).resolve().parents[2]
    code="""import sys
sys.path.insert(0,sys.argv[1])
from scripts.business_authority_v1 import Authority,AuthorityError
owner=Authority(sys.argv[2],sys.argv[3])
try:
 owner.claim(dict(operation='OUTCOME',run_id='TEST_FIXTURE_run_A',input_ids=['TEST_FIXTURE_B',sys.argv[4]]),
  epoch_id='TEST_FIXTURE_epoch',limits=dict(bytes=10))
except AuthorityError as error:
 assert str(error)=='AUTHORITY_OUTCOME_FAMILY_RECONCILE_NO_RETRY',str(error)
 raise SystemExit(17)
"""
    children=[subprocess.Popen([sys.executable,"-I","-B","-c",code,str(root),str(authority.path),
        authority.workspace_id,name],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        for name in ("TEST_FIXTURE_C","TEST_FIXTURE_D")]
    outputs=[child.communicate(timeout=15) for child in children]
    assert sorted(child.returncode for child in children)==[0,17],outputs
    with authority._connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM requests WHERE state='CLAIMED'").fetchone()[0]==1


def test_source_inventory_status_is_original_onboarding_not_operator_assertion(tmp_path):
    state=tmp_path/"TEST_FIXTURE_legacy_state";state.mkdir()
    (state/"TEST_FIXTURE_existing_partial.json").write_text('{"status":"UNKNOWN"}')
    registry=tmp_path/"TEST_FIXTURE_legacy_registry";registry.mkdir()
    authority=a.Authority.initialize(tmp_path/"TEST_FIXTURE_legacy_authority.sqlite3","TEST_FIXTURE_workspace",
        onboarding=dict(mode="FULL_LEGACY_IMPORT_PENDING",state_roots=[str(state)],registry_roots=[str(registry)],
                        source_scopes_reconciled=True,source_authority_restored=True))
    assert authority.source_scope_status()=="LEGACY_SOURCE_SCOPES_UNRECONCILED"
    with authority._connect() as connection:
        connection.execute("INSERT INTO epochs VALUES (?,?,?,?,?)",("TEST_FIXTURE_epoch","[]","{}","ACTIVE",a.stamp()))
    current=claim(authority)
    with pytest.raises(a.AuthorityError,match="LEGACY_SOURCE_SCOPES_UNRECONCILED"):
        authority.derive_capability(current,["provider","network"])


def test_provider_effect_cannot_use_storage_only_stage(authority):
    current=claim(authority)
    token=authority.derive_capability(current,["storage"])
    with pytest.raises(a.AuthorityError,match="PROVIDER_EFFECT_STAGE_REQUIRED"):
        authority.consume_once(token,stage="storage",effect_kind="PROVIDER_REQUEST",
                               stable_effect_id="TEST_FIXTURE_storage_provider",source_scope=source_scope())
    with authority._connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0]==0


def test_key_engine_version_and_inode_cannot_change_business_identity():
    base=a.canonical_business(business())
    for extra in [dict(request_key="different"),dict(engine="shared"),dict(source_root="new",inode=123),
                  dict(schema_version="FUTURE",configuration={"version":"new"})]:
        assert a.canonical_business({**business(),**extra})==base
    assert a.business_id("TEST_FIXTURE_workspace",base)==a.business_id("TEST_FIXTURE_workspace",business())


def test_same_day_distinct_actual_operations_and_inputs_are_legal(authority):
    identities=[business(),dict(operation="CAPTURE",provider="TEST_FIXTURE",dataset="RAW",
        window_start="2026-11-04",window_end="2026-11-04",codes=["000001"]),
        dict(operation="CORE_COMPUTE",input_id="input_A",strategy_id="R2"),
        dict(operation="CORE_COMPUTE",input_id="input_B",strategy_id="R2"),
        dict(operation="OUTCOME",run_id="run_A",input_id="input_A")]
    claims=[authority.claim(value,epoch_id="TEST_FIXTURE_epoch") for value in identities]
    assert len({value["business_id"] for value in claims})==5


def test_capture_alias_and_order_not_new_business():
    value=dict(operation="CAPTURE",provider="TEST_FIXTURE",dataset="RAW",window_start="2026-01-01",
               window_end="2026-01-02",codes=["b","a"])
    first=a.canonical_business(value)
    second=a.canonical_business({**value,"codes":["a","b"],"subject":"invented","request_key":"new"})
    assert first==second
    assert a.canonical_business(first)==first
    with pytest.raises(ValueError,match="PROVIDER_REQUIRED"):
        a.canonical_business(dict(operation="CAPTURE",subject="invented"))


def test_unknown_shape_not_silently_new_operation():
    with pytest.raises(ValueError,match="OPERATION_EXPLICIT_REQUIRED"):
        a.canonical_business(dict(request_key="TEST_FIXTURE"))
    with pytest.raises(ValueError,match="OUTCOME_RUN_AND_INPUT_REQUIRED"):
        a.canonical_business(dict(operation="OUTCOME",run_id="one"))


def test_failed_and_unknown_claim_refuse_every_alias(authority):
    original=claim(authority)
    authority.finish(original,success=False,budget_known=False)
    for extra in [dict(request_key="new"),dict(engine="shared"),dict(source_root="new")]:
        with pytest.raises(a.AuthorityError,match="RECONCILE_NO_RETRY"):
            authority.claim({**business(),**extra},epoch_id="TEST_FIXTURE_epoch")
    with pytest.raises(a.AuthorityError,match="UNKNOWN_CUMULATIVE"):
        authority.authorize_resume(original["business_id"],previous_attempt_id=original["attempt_id"],
            authorization_id="explicit",reconciliation_sha256="a"*64)


def test_exact_completed_reuse_retains_receipt_and_never_starts(authority):
    original=claim(authority)
    authority.start(original)
    authority.finish(original,success=True,receipt=dict(TEST_FIXTURE="verified-original"))
    reused=claim(authority,request_metadata=dict(request_key="new",engine="shared"))
    assert reused["status"]=="REUSED" and reused["receipt"]==dict(TEST_FIXTURE="verified-original")
    with pytest.raises(a.AuthorityError,match="NOT_ACTIVE"):
        authority.start(reused)


def test_concurrent_claim_has_one_winner(authority):
    def contender(index):
        try:
            other=a.Authority(authority.path,authority.workspace_id)
            return other.claim({**business(),"request_key":str(index)},epoch_id="TEST_FIXTURE_epoch")["state"]
        except a.AuthorityError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=8) as executor:
        result=list(executor.map(contender,range(8)))
    assert result.count("CLAIMED")==1
    assert result.count("AUTHORITY_EXISTING_REQUEST_RECONCILE_NO_RETRY")==7


def test_crashed_process_claim_stays_unknown_and_unretryable(authority):
    root=Path(__file__).resolve().parents[2]
    code="""import sys,os
sys.path.insert(0,sys.argv[1])
from scripts.business_authority_v1 import Authority
a=Authority(sys.argv[2],sys.argv[3])
a.claim(dict(backend='premarket',observation_date='2026-11-04',target_session='2026-11-05'),epoch_id='TEST_FIXTURE_epoch')
os._exit(37)
"""
    result=subprocess.run([sys.executable,"-I","-B","-c",code,str(root),str(authority.path),authority.workspace_id],
                          capture_output=True,text=True)
    assert result.returncode==37,result.stderr
    with pytest.raises(a.AuthorityError,match="RECONCILE_NO_RETRY"):
        claim(authority)


def test_explicit_resume_retains_cumulative_limits(authority):
    original=claim(authority,limits=dict(requests=3,bytes=100))
    authority.charge(original,dict(requests=2,bytes=40))
    authority.finish(original,success=False)
    resumed=authority.authorize_resume(original["business_id"],previous_attempt_id=original["attempt_id"],
        authorization_id="TEST_FIXTURE_EXPLICIT_AUTHORIZATION",reconciliation_sha256="a"*64)
    assert resumed["remaining_limits"]==dict(requests=1,bytes=60)
    with pytest.raises(a.AuthorityError,match="EXCEEDED"):
        authority.charge(resumed,dict(requests=2))
    assert authority.inspect(original["business_id"])["cumulative_spent"]==dict(requests=2,bytes=40)
    authority.charge(resumed,dict(requests=1))


def test_capability_stage_revocation_and_unique_worker(authority):
    original=claim(authority,limits=dict(requests=2))
    token=authority.derive_capability(original,["worker","capture"])
    assert authority.verify_capability(token,stage="worker")["business_id"]==original["business_id"]
    first=authority.consume_once(token,stage="worker",effect_kind="WORKER_START",stable_effect_id="worker_A")
    with pytest.raises(a.AuthorityError,match="ALREADY_RESERVED"):
        authority.consume_once(token,stage="worker",effect_kind="WORKER_START",stable_effect_id="worker_B")
    with pytest.raises(a.AuthorityError,match="CAPABILITY_REJECTED"):
        authority.verify_capability(token,stage="publish")
    authority.complete_effect(token,first,receipt=dict(exit_code=0))
    authority.finish(original,success=True,receipt=dict(TEST_FIXTURE=True))
    with pytest.raises(a.AuthorityError,match="CAPABILITY_REJECTED"):
        authority.verify_capability(token,stage="worker")


def test_provider_reservation_and_charge_are_atomic(authority):
    original=claim(authority,limits=dict(requests=1))
    token=authority.derive_capability(original,["capture"])
    event=authority.consume_once(token,stage="capture",effect_kind="PROVIDER_REQUEST",
        stable_effect_id="TEST_FIXTURE_provider_RAW_000001_1",charge=dict(requests=1),source_scope=source_scope())
    with pytest.raises(a.AuthorityError,match="ALREADY_RESERVED"):
        authority.consume_once(token,stage="capture",effect_kind="PROVIDER_REQUEST",
            stable_effect_id=event["stable_effect_id"],charge=dict(requests=1),source_scope=source_scope())
    with pytest.raises(a.AuthorityError,match="EXCEEDED"):
        authority.consume_once(token,stage="capture",effect_kind="PROVIDER_REQUEST",
            stable_effect_id="TEST_FIXTURE_provider_RAW_000002_2",charge=dict(requests=1),source_scope=source_scope())
    assert authority.inspect(original["business_id"])["cumulative_spent"]==dict(requests=1)
    with authority._connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0]==1


def test_new_database_not_implicitly_created_and_wrong_workspace_rejected(authority,tmp_path):
    with pytest.raises(FileNotFoundError):
        a.Authority(tmp_path/"absent.sqlite3",authority.workspace_id)
    with pytest.raises(a.AuthorityError,match="IDENTITY_MISMATCH"):
        a.Authority(authority.path,"another-workspace")
    with pytest.raises(FileExistsError):
        a.Authority.initialize(authority.path,authority.workspace_id,
            onboarding=dict(mode="FULL_LEGACY_IMPORT_PENDING",state_roots=[str(tmp_path)],registry_roots=[str(tmp_path)]))


def test_nonempty_state_cannot_claim_empty_onboarding(tmp_path):
    state=tmp_path/"TEST_FIXTURE_state";state.mkdir();(state/"old.json").write_text("failure")
    with pytest.raises(a.AuthorityError,match="REQUIRES_IMPORT"):
        a.Authority.initialize(tmp_path/"new.sqlite3","TEST_FIXTURE",
            onboarding=dict(mode="EMPTY_NEW_WORKSPACE",state_roots=[str(state)]))
    assert not (tmp_path/"new.sqlite3").exists()


def test_inactive_epoch_blocks_claim(authority):
    with pytest.raises(a.AuthorityError,match="ACTIVE_EPOCH_REQUIRED"):
        authority.claim(business(),epoch_id="invented")


def test_replaced_database_and_alias_refused(authority,tmp_path):
    alias=tmp_path/"alias";alias.symlink_to(authority.path)
    with pytest.raises(a.AuthorityError,match="ALIAS"):
        a.Authority(alias,authority.workspace_id)
    raw=authority.path.read_bytes();authority.path.rename(authority.path.with_suffix(".preserved"))
    authority.path.write_bytes(raw)
    with pytest.raises(a.AuthorityError,match="REPLACED"):
        authority.active_epoch()


def test_copied_authority_cannot_form_second_active_writer(authority,tmp_path):
    copied=tmp_path/"TEST_FIXTURE_copied_authority.sqlite3"
    shutil.copyfile(authority.path,copied)
    before=hashlib.sha256(copied.read_bytes()).hexdigest()
    with pytest.raises(a.AuthorityError,match="ORIGINAL_CARRIER_MISMATCH"):
        other=a.Authority(copied,authority.workspace_id)
        other.claim(business(),epoch_id="TEST_FIXTURE_epoch")
    assert hashlib.sha256(copied.read_bytes()).hexdigest()==before
    # A legitimate reopen retains the one original authority and the same
    # logical business identity; the carrier is not an input to business_id.
    reopened=a.Authority(authority.path,authority.workspace_id)
    first=claim(authority)
    assert reopened.inspect(first["business_id"])["attempt_id"]==first["attempt_id"]
    with pytest.raises(a.AuthorityError,match="RECONCILE_NO_RETRY"):
        claim(reopened,request_metadata=dict(request_key="TEST_FIXTURE_changed"))


def test_new_object_cannot_accept_same_path_replacement(authority):
    original=authority.path
    saved=original.with_suffix(".TEST_FIXTURE_preserved")
    original.rename(saved)
    shutil.copyfile(saved,original)
    assert original.stat().st_ino!=authority.physical[1]
    with pytest.raises(a.AuthorityError,match="DATABASE_REPLACED"):
        authority.active_epoch()
    with pytest.raises(a.AuthorityError,match="ORIGINAL_CARRIER_MISMATCH"):
        a.Authority(original,authority.workspace_id)


def test_renamed_original_carrier_cannot_become_new_authority(authority,tmp_path):
    moved=tmp_path/"TEST_FIXTURE_moved_original.sqlite3"
    authority.path.rename(moved)
    assert moved.stat().st_ino==authority.physical[1]
    with pytest.raises(a.AuthorityError,match="ORIGINAL_CARRIER_MISMATCH"):
        a.Authority(moved,authority.workspace_id)


def test_missing_original_binding_never_uses_implicit_current_file_identity(authority):
    # Simulate a pre-binding carrier in an isolated fixture. The absence of
    # evidence must not be backfilled with whatever path/inode was just opened.
    with sqlite3.connect(authority.path) as connection:
        connection.execute("DELETE FROM meta WHERE key='original_carrier'")
    with pytest.raises(a.AuthorityError,match="ORIGINAL_CARRIER_MISMATCH"):
        a.Authority(authority.path,authority.workspace_id)
    with pytest.raises(a.AuthorityError,match="ORIGINAL_CARRIER_MISMATCH"):
        authority.active_epoch()


def test_original_binding_cannot_be_changed_inside_authority_transaction(authority):
    with pytest.raises(a.AuthorityError,match="ORIGINAL_CARRIER_MISMATCH"):
        with authority._connect() as connection:
            connection.execute("UPDATE meta SET value='{}' WHERE key='original_carrier'")
    assert a.Authority(authority.path,authority.workspace_id).active_epoch()["epoch_id"]=="TEST_FIXTURE_epoch"


def test_provider_effect_unique_across_business_types(authority):
    premarket=claim(authority,limits=dict(requests=2))
    capture=authority.claim(dict(operation="CAPTURE",provider="TEST_FIXTURE",dataset="RAW",
        window_start="2026-11-04",window_end="2026-11-04",codes=["000001"]),
        epoch_id="TEST_FIXTURE_epoch",limits=dict(requests=2))
    first_token=authority.derive_capability(premarket,["capture"])
    second_token=authority.derive_capability(capture,["capture"])
    effect_id="TEST_FIXTURE_provider_RAW_000001_20261104_page0"
    event=authority.consume_once(first_token,stage="capture",effect_kind="PROVIDER_REQUEST",
        stable_effect_id=effect_id,charge=dict(requests=1),source_scope=source_scope())
    with pytest.raises(a.AuthorityError,match="ALREADY_RESERVED"):
        authority.consume_once(second_token,stage="capture",effect_kind="PROVIDER_REQUEST",
            stable_effect_id=effect_id,charge=dict(requests=1),source_scope=source_scope())
    assert authority.inspect(capture["business_id"])["cumulative_spent"]=={}
    authority.complete_effect(first_token,event,receipt=dict(TEST_FIXTURE_source_sha256="a"*64))
    locator=authority.inspect_provider_effect(effect_id)
    assert locator["state"]=="COMPLETED" and locator["revalidation_required"]
    assert not locator["source_authority_restored"]


def test_real_processes_reserve_one_global_provider_effect(authority):
    first=claim(authority,limits=dict(requests=2))
    second=authority.claim(dict(operation="CAPTURE",provider="TEST_FIXTURE",dataset="RAW",
        window_start="2026-11-04",window_end="2026-11-04",codes=["000001"]),
        epoch_id="TEST_FIXTURE_epoch",limits=dict(requests=2))
    tokens=[authority.derive_capability(value,["capture"]) for value in (first,second)]
    root=Path(__file__).resolve().parents[2]
    code="""import sys,json
sys.path.insert(0,sys.argv[1])
from scripts.business_authority_v1 import Authority,AuthorityError
a=Authority(sys.argv[2],sys.argv[3])
try:
 a.consume_once(sys.argv[4],stage='capture',effect_kind='PROVIDER_REQUEST',stable_effect_id='TEST_FIXTURE_global_effect',charge={'requests':1},source_scope=json.loads(sys.argv[5]))
except AuthorityError as error:
 assert str(error)=='AUTHORITY_EFFECT_ALREADY_RESERVED_NO_RETRY'
 raise SystemExit(17)
"""
    processes=[subprocess.Popen([sys.executable,"-I","-B","-c",code,str(root),str(authority.path),
        authority.workspace_id,token,json.dumps(source_scope())],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for token in tokens]
    outputs=[process.communicate(timeout=10) for process in processes]
    assert sorted(process.returncode for process in processes)==[0,17],outputs
    assert sum(authority.inspect(value["business_id"])["cumulative_spent"].get("requests",0)
               for value in (first,second))==1


def test_authorized_resume_has_one_new_worker_without_erasing_old_effect(authority):
    first=claim(authority,limits=dict(requests=2))
    old_token=authority.derive_capability(first,["worker"])
    authority.consume_once(old_token,stage="worker",effect_kind="WORKER_START",stable_effect_id="first")
    authority.finish(first,success=False)
    resumed=authority.authorize_resume(first["business_id"],previous_attempt_id=first["attempt_id"],
        authorization_id="TEST_FIXTURE_EXPLICIT",reconciliation_sha256="b"*64)
    token=authority.derive_capability(resumed,["worker"])
    authority.consume_once(token,stage="worker",effect_kind="WORKER_START",stable_effect_id="renamed")
    with pytest.raises(a.AuthorityError,match="ALREADY_RESERVED"):
        authority.consume_once(token,stage="worker",effect_kind="WORKER_START",stable_effect_id="another")
    with authority._connect(readonly=True) as connection:
        states=[row[0] for row in connection.execute("SELECT state FROM effects ORDER BY state")]
    assert states==["STARTED","UNKNOWN"]


def test_initialize_cli_requires_explicit_switch(tmp_path):
    state=tmp_path/"TEST_FIXTURE_empty";state.mkdir()
    onboarding=tmp_path/"TEST_FIXTURE_onboarding.json"
    onboarding.write_text(json.dumps(dict(mode="EMPTY_NEW_WORKSPACE",state_roots=[str(state)])))
    with pytest.raises(a.AuthorityError,match="INITIALIZATION_SWITCH"):
        a.main(["initialize","--database",str(tmp_path/"authority.sqlite3"),"--workspace-id","TEST_FIXTURE",
            "--onboarding",str(onboarding),"--onboarding-sha256",hashlib.sha256(onboarding.read_bytes()).hexdigest()])
    assert not (tmp_path/"authority.sqlite3").exists()


@pytest.mark.parametrize("operation,identity",[
    ("CORE_COMPUTE",dict(input_id="input_A",strategy_id="R2")),
    ("OUTCOME",dict(input_id="input_A",run_id="run_A")),
    ("RESEARCH",dict(input_id="input_A",subject="TEST_FIXTURE_study")),
    ("EXPORT",dict(run_id="run_A")),
    ("MAINTENANCE",dict(subject="DAILY_DELIVERY_PROTOCOL",parameters=dict(protocol_hash="a"*64)))])
def test_non_capture_operation_cannot_gain_provider_or_registration_stage(authority,operation,identity):
    claim=authority.claim(dict(operation=operation,**identity),epoch_id="TEST_FIXTURE_epoch")
    for stage in ("network","provider","capture","registration"):
        with pytest.raises(a.AuthorityError,match="STAGE_NOT_ALLOWED"):
            authority.derive_capability(claim,[stage])
    stage="worker" if "worker" in a.ALLOWED_STAGES[operation] else "storage"
    token=authority.derive_capability(claim,[stage])
    with pytest.raises(a.AuthorityError,match="PROVIDER_EFFECT_NOT_ALLOWED"):
        authority.consume_once(token,stage=stage,effect_kind="PROVIDER_REQUEST",stable_effect_id="invented",source_scope=source_scope())
    with pytest.raises(a.AuthorityError,match="REGISTRATION_NOT_A_BUSINESS_EFFECT"):
        authority.consume_once(token,stage=stage,effect_kind="REGISTRATION",stable_effect_id="invented")


def test_service_restart_follows_successful_chain_and_retains_previous_receipt(authority):
    business=dict(operation="MAINTENANCE",subject="ACTIVE_SERVICE_BOOTSTRAP",
                  parameters=dict(service_role="LOCAL_RESEARCH_HTTP"))
    first=authority.claim(business,epoch_id="TEST_FIXTURE_epoch")
    receipt=dict(host_id="TEST_FIXTURE_first",maintenance_sequence=1,listener_started=False)
    authority.finish(first,success=True,receipt=receipt)
    next_claim=authority.begin_service_restart(previous_attempt_id=first["attempt_id"],
        previous_receipt_sha256=a.digest(receipt),maintenance_sequence=2,
        authorization_id="TEST_FIXTURE explicit restart",epoch_id="TEST_FIXTURE_epoch")
    assert next_claim["business_id"]==first["business_id"] and next_claim["attempt_id"]!=first["attempt_id"]
    with pytest.raises(a.AuthorityError,match="PARENT_NOT_SUCCESSFUL"):
        authority.begin_service_restart(previous_attempt_id=first["attempt_id"],
            previous_receipt_sha256=a.digest(receipt),maintenance_sequence=2,
            authorization_id="changed authorization",epoch_id="TEST_FIXTURE_epoch")
    with authority._connect(readonly=True) as db:
        event=db.execute("SELECT data FROM events WHERE event='SERVICE_RESTART_AUTHORIZED'").fetchone()
    assert json.loads(event[0])["previous_receipt"]==receipt
    authority.finish(next_claim,success=False,budget_known=False)
    with pytest.raises(a.AuthorityError,match="PARENT_NOT_SUCCESSFUL"):
        authority.begin_service_restart(previous_attempt_id=next_claim["attempt_id"],
            previous_receipt_sha256=a.digest(receipt),maintenance_sequence=3,
            authorization_id="new registration/new authorization",epoch_id="TEST_FIXTURE_epoch")


def test_service_restart_cannot_change_receipt_hash_or_sequence(authority):
    first=authority.claim(dict(operation="MAINTENANCE",subject="ACTIVE_SERVICE_BOOTSTRAP",
        parameters=dict(service_role="LOCAL_RESEARCH_HTTP")),epoch_id="TEST_FIXTURE_epoch")
    receipt=dict(host_id="TEST_FIXTURE",maintenance_sequence=1)
    authority.finish(first,success=True,receipt=receipt)
    for receipt_hash,sequence in (("0"*64,2),(a.digest(receipt),3)):
        with pytest.raises(a.AuthorityError,match="CHAIN_CHANGED"):
            authority.begin_service_restart(previous_attempt_id=first["attempt_id"],
                previous_receipt_sha256=receipt_hash,maintenance_sequence=sequence,
                authorization_id="TEST_FIXTURE",epoch_id="TEST_FIXTURE_epoch")
    assert authority.inspect(first["business_id"])["state"]=="RECORDED"


def capture_claim(authority,scope):
    value=authority.claim(dict(operation="CAPTURE",provider=scope["provider"],dataset=scope["endpoint"],
        window_start=scope["window_start"],window_end=scope["window_end"],codes=scope["codes"]),
        epoch_id="TEST_FIXTURE_epoch",limits=dict(requests=4))
    return value,authority.derive_capability(value,["capture"])


@pytest.mark.parametrize("old_codes,new_codes,new_start,new_end",[
    (("A","B"),("A",),"2026-11-02","2026-11-04"),
    (("A","B"),("A","C"),"2026-11-01","2026-11-05"),
    ((),("A",),"2026-11-04","2026-11-06"),
    (("A","B"),(),"2026-11-01","2026-11-02")])
@pytest.mark.parametrize("terminal",[False,True])
def test_changed_batch_or_window_cannot_overlap_active_or_failed_source(authority,old_codes,new_codes,new_start,new_end,terminal):
    original=claim(authority,limits=dict(requests=4));token=authority.derive_capability(original,["capture"])
    old=source_scope(old_codes,"2026-11-02","2026-11-04")
    authority.consume_once(token,stage="capture",effect_kind="PROVIDER_REQUEST",stable_effect_id="old",
                           source_scope=old,charge=dict(requests=1))
    if terminal:authority.finish(original,success=False,budget_known=False)
    changed=source_scope(new_codes,new_start,new_end)
    new,new_token=capture_claim(authority,changed)
    with pytest.raises(a.AuthorityError,match="SCOPE_OVERLAPS_FAILED_OR_UNKNOWN_NO_RETRY"):
        authority.consume_once(new_token,stage="capture",effect_kind="PROVIDER_REQUEST",stable_effect_id="new_hash",
                               source_scope=changed,charge=dict(requests=1))
    assert authority.inspect(new["business_id"])["cumulative_spent"]=={}
    with authority._connect(readonly=True) as db:
        assert db.execute("SELECT COUNT(*) FROM source_scopes").fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM effects").fetchone()[0]==1


def test_nonoverlap_and_completed_source_do_not_block_unrelated_work(authority):
    first=claim(authority,limits=dict(requests=4));token=authority.derive_capability(first,["capture"])
    original=source_scope(("A","B"))
    event=authority.consume_once(token,stage="capture",effect_kind="PROVIDER_REQUEST",stable_effect_id="old",source_scope=original)
    for index,scope in enumerate((source_scope(("C",)),source_scope(("A",),"2026-11-05","2026-11-05"))):
        _,other=capture_claim(authority,scope)
        authority.consume_once(other,stage="capture",effect_kind="PROVIDER_REQUEST",stable_effect_id=str(index),source_scope=scope)
    authority.complete_effect(token,event,receipt={"TEST_FIXTURE":"verified carrier remains separate"})
    authority.finish(first,success=True,receipt={"TEST_FIXTURE":True})
    _,subset=capture_claim(authority,source_scope(("A",)))
    authority.consume_once(subset,stage="capture",effect_kind="PROVIDER_REQUEST",stable_effect_id="successful_subset",
                           source_scope=source_scope(("A",)))


def test_one_request_can_reserve_provider_transport_and_pages_but_not_change_coverage(authority):
    original=claim(authority,limits=dict(requests=4));token=authority.derive_capability(original,["capture"])
    scope=source_scope(("A","B"))
    for effect in ("provider","http_page_0","http_page_1"):
        authority.consume_once(token,stage="capture",effect_kind="PROVIDER_REQUEST",stable_effect_id=effect,source_scope=scope)
    with pytest.raises(a.AuthorityError,match="SEMANTIC_COVERAGE_CHANGED"):
        authority.consume_once(token,stage="capture",effect_kind="PROVIDER_REQUEST",stable_effect_id="changed",
            source_scope={**scope,"codes":["A"]})
    with pytest.raises(a.AuthorityError,match="SCOPE_OVERLAPS_FAILED_OR_UNKNOWN"):
        authority.consume_once(token,stage="capture",effect_kind="PROVIDER_REQUEST",stable_effect_id="changed_hash",
            source_scope=source_scope(("A",)))
    with pytest.raises(a.AuthorityError,match="SOURCE_SCOPE_REQUIRED"):
        authority.consume_once(token,stage="capture",effect_kind="PROVIDER_REQUEST",stable_effect_id="missing")


def test_real_concurrent_overlapping_batches_reserve_all_or_nothing(authority):
    scopes=[source_scope(("A","B")),source_scope(("A","C"))]
    pairs=[capture_claim(authority,scope) for scope in scopes]
    root=Path(__file__).resolve().parents[2]
    code="""import sys,json
sys.path.insert(0,sys.argv[1])
from scripts.business_authority_v1 import Authority,AuthorityError
owner=Authority(sys.argv[2],sys.argv[3])
try:
 owner.consume_once(sys.argv[4],stage='capture',effect_kind='PROVIDER_REQUEST',stable_effect_id=sys.argv[5],
  source_scope=json.loads(sys.argv[6]),charge={'requests':1})
except AuthorityError as error:
 assert str(error)=='AUTHORITY_SOURCE_SCOPE_OVERLAPS_FAILED_OR_UNKNOWN_NO_RETRY',str(error)
 raise SystemExit(17)
"""
    processes=[subprocess.Popen([sys.executable,"-I","-B","-c",code,str(root),str(authority.path),
        authority.workspace_id,token,"TEST_FIXTURE_effect_"+str(index),json.dumps(scopes[index])],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for index,(_,token) in enumerate(pairs)]
    outputs=[process.communicate(timeout=10) for process in processes]
    assert sorted(process.returncode for process in processes)==[0,17],outputs
    assert sum(authority.inspect(value["business_id"])["cumulative_spent"].get("requests",0) for value,_ in pairs)==1
    with authority._connect(readonly=True) as db:
        assert db.execute("SELECT COUNT(*) FROM source_scopes").fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM effects").fetchone()[0]==1


def test_complete_source_closes_only_same_request_transport_and_future_overlap_is_legal(authority):
    original=claim(authority,limits=dict(requests=4));token=authority.derive_capability(original,["capture"])
    scope=source_scope(("A","B"),"2026-11-01","2026-11-04")
    for effect in ("provider","HTTP_0","SDK_0"):
        authority.consume_once(token,stage="capture",effect_kind="PROVIDER_REQUEST",stable_effect_id=effect,source_scope=scope)
    unrelated=source_scope(("C",))
    authority.consume_once(token,stage="capture",effect_kind="PROVIDER_REQUEST",stable_effect_id="unrelated",source_scope=unrelated)
    completed=authority.complete_source_request(token,stage="capture",source_scope=scope,
        receipt=dict(status="COMPLETE",pagination_complete=True,TEST_FIXTURE=True))
    assert completed==dict(completed_effects=3,source_authority_restored=False)
    authority.finish(original,success=True,receipt={"TEST_FIXTURE":True})
    with authority._connect(readonly=True) as db:
        states=dict(db.execute("SELECT stable_effect_id,state FROM effects"))
    assert states==dict(provider="COMPLETED",HTTP_0="COMPLETED",SDK_0="COMPLETED",unrelated="UNKNOWN")
    future=source_scope(("A",),"2026-11-02","2026-11-05")
    _,new=capture_claim(authority,future)
    authority.consume_once(new,stage="capture",effect_kind="PROVIDER_REQUEST",stable_effect_id="future",source_scope=future)
    blocked=source_scope(("C",),"2026-11-04","2026-11-05")
    _,other=capture_claim(authority,blocked)
    with pytest.raises(a.AuthorityError,match="SCOPE_OVERLAPS_FAILED_OR_UNKNOWN"):
        authority.consume_once(other,stage="capture",effect_kind="PROVIDER_REQUEST",stable_effect_id="uncertain",source_scope=blocked)


@pytest.mark.parametrize("status,complete",[("PARTIAL",False),("FETCH_FAILED",False),("COMPLETE",False)])
def test_partial_source_cannot_close_transport(authority,status,complete):
    original=claim(authority,limits=dict(requests=4));token=authority.derive_capability(original,["capture"])
    scope=source_scope()
    authority.consume_once(token,stage="capture",effect_kind="PROVIDER_REQUEST",stable_effect_id="HTTP_0",source_scope=scope)
    with pytest.raises(a.AuthorityError,match="SOURCE_COMPLETION_NOT_COMPLETE"):
        authority.complete_source_request(token,stage="capture",source_scope=scope,
            receipt=dict(status=status,pagination_complete=complete))
    with authority._connect(readonly=True) as db:
        assert db.execute("SELECT state FROM effects").fetchone()[0]=="STARTED"
