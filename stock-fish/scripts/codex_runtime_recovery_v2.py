"""Explicit multi-generation control recovery with complete legacy inventory.

Plans are read-only. Applying a plan publishes an inert PREPARED registration,
then atomically imports old requests and activates one epoch in the authority.
An interrupted publication cannot produce two active epochs or grant market
source/PIT capability. No real registration is migrated on module import.
"""
from __future__ import annotations

from copy import deepcopy
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from scripts import business_authority_v1 as a
from scripts import codex_runtime_recovery_v1 as old
from scripts import codex_workspace as w

VERSION = "CODEX_RUNTIME_RECOVERY_V2"
need = a.need
MAX_CONTROL_FILES = 100000
MAX_CONTROL_BYTES = 512 * 1024**2


def reference(path):
    return old.reference(Path(path))


def _document(path):
    before = reference(path)
    value = w._read(Path(path),limit=64*1024**2)
    need(reference(path) == before, "RECOVERY_V2_CONTROL_CHANGED_WHILE_READING")
    return value, before


def _paths(registry):
    registry = Path(registry).absolute()
    w._directory(registry)
    found = []
    for directory in ("runtimes", "recovery-epochs", "scopes"):
        base = registry / directory
        if base.exists():
            w._directory(base)
            for item in base.rglob("*"):
                need(not item.is_symlink(), "RECOVERY_V2_CONTROL_ALIAS_REJECTED")
                if item.is_file() and item.suffix == ".json":
                    found.append(item)
                    need(len(found) <= MAX_CONTROL_FILES, "RECOVERY_V2_CONTROL_COUNT_LIMIT")
    default = registry / "default.json"
    if default.exists() or default.is_symlink():
        found.append(default)
    return sorted(found)


def inventory(registry_roots, *, mappings=None, budget_evidence=None, exclude=(), exclude_prefix=()):
    """Pin every control branch in the declared complete registry set.

    Operation mappings are explicit migration inputs for formerly ambiguous
    operations. Unknown shapes never silently disappear from the import.
    """
    need(isinstance(registry_roots, (list, tuple)) and registry_roots, "RECOVERY_V2_REGISTRIES_REQUIRED")
    roots = sorted({str(Path(p).absolute()) for p in registry_roots})
    need(len(roots) == len(registry_roots) and len(roots) <= 32, "RECOVERY_V2_REGISTRY_SET_INVALID")
    mappings, budget_evidence = mappings or {}, budget_evidence or {}
    excludes = {str(Path(p).absolute()) for p in exclude}
    excluded_prefixes = [Path(p).absolute() for p in exclude_prefix]
    files, operations, registrations, total = [], [], [], 0
    used_mappings, used_budgets = set(), set()
    for root in roots:
        for path in _paths(root):
            if str(path) in excludes or any(path.is_relative_to(prefix) for prefix in excluded_prefixes):
                continue
            value, ref = _document(path)
            total += ref["fp"][3]
            need(total <= MAX_CONTROL_BYTES, "RECOVERY_V2_CONTROL_BYTE_LIMIT")
            files.append(ref)
            relative = path.relative_to(root)
            if relative.parts[0] == "runtimes":
                need(value.get("schema_version") in {w.VERSION, old.VERSION, VERSION},
                     "RECOVERY_V2_UNKNOWN_RUNTIME_SCHEMA")
                registrations.append(dict(reference=ref, name=value["profile"]["name"],
                    profile=deepcopy(value["profile"]), binding=deepcopy(value["binding"]),
                    parent_epoch=value.get("epoch_id"), old_epoch=deepcopy(value.get("epoch"))))
            if len(relative.parts) >= 4 and relative.parts[-2] == "operations":
                identity = value.get("identity")
                need(value.get("schema_version") == w.VERSION and isinstance(identity, dict)
                     and value.get("operation_id") == path.stem
                     and path.stem == w._digest({"scope": value.get("scope"), "identity": identity}),
                     "RECOVERY_V2_LEGACY_OPERATION_BINDING_INVALID")
                if ref["sha256"] in mappings:
                    business = a.canonical_business(mappings[ref["sha256"]])
                    used_mappings.add(ref["sha256"])
                else:
                    business = a.canonical_business(identity)
                phase = value.get("phase")
                need(phase in {"SUBMITTING", "FAILED_OR_UNKNOWN", "RECORDED"},
                     "RECOVERY_V2_LEGACY_OPERATION_STATE_UNKNOWN")
                budget = dict(known=False, limits={}, spent={}, proof=None)
                if ref["sha256"] in budget_evidence:
                    proof, proof_ref = _document(budget_evidence[ref["sha256"]])
                    need(proof.get("schema") == "LEGACY_BUDGET_EVIDENCE_V1"
                         and proof.get("operation_sha256") == ref["sha256"]
                         and proof.get("counters_complete") is True,
                         "RECOVERY_V2_BUDGET_EVIDENCE_BINDING_INVALID")
                    limits, spent = a._numbers(proof["limits"]), a._numbers(proof["spent"])
                    need(set(spent) == set(limits) and all(spent[k] <= limits[k] for k in limits),
                         "RECOVERY_V2_BUDGET_EVIDENCE_COUNTERS_INVALID")
                    budget = dict(known=True, limits=limits, spent=spent, proof=proof_ref)
                    files.append(proof_ref)
                    used_budgets.add(ref["sha256"])
                operations.append(dict(reference=ref, business=business, phase=phase, budget=budget))
    need(used_mappings == set(mappings), "RECOVERY_V2_UNUSED_OR_UNKNOWN_MAPPING")
    need(used_budgets == set(budget_evidence), "RECOVERY_V2_UNUSED_BUDGET_EVIDENCE")
    return dict(schema="LEGACY_CONTROL_INVENTORY_V2", registry_roots=roots,
                files=files, registrations=registrations, operations=operations,
                control_bytes=total, mappings=deepcopy(mappings),
                budget_evidence={k:str(Path(v).absolute()) for k,v in budget_evidence.items()})


def _heads(connection):
    rows = list(connection.execute("SELECT epoch_id,parents,state FROM epochs"))
    ids = {row["epoch_id"] for row in rows if row["state"] != "PREPARED"}
    parents = {p for row in rows if row["state"] != "PREPARED" for p in json.loads(row["parents"])}
    return sorted(ids - parents)


def _existing_import(connection, authority, operation):
    ref=operation["reference"]
    existing=connection.execute("SELECT reference,business_id FROM imported WHERE reference_sha=?",(ref["sha256"],)).fetchone()
    if existing is not None:
        need(existing["reference"]==a.canonical(ref).decode(),"RECOVERY_V2_IMPORTED_REFERENCE_CHANGED")
        need(existing["business_id"]==a.business_id(authority.workspace_id,operation["business"]),
             "RECOVERY_V2_IMPORTED_BUSINESS_MAPPING_CHANGED")
    return existing


def plan_recovery(authority, registry_roots, new_profile, environment_receipt, *,
                  parents=None, mappings=None, budget_evidence=None,
                  purpose, allow_test_fixture=False):
    need(isinstance(authority, a.Authority), "RECOVERY_V2_AUTHORITY_REQUIRED")
    profile = w.validate_profile(new_profile)
    scan = inventory(registry_roots, mappings=mappings, budget_evidence=budget_evidence)
    registry = Path(scan["registry_roots"][0])
    _, config_ref = _document(profile["config"])
    environment = deepcopy(environment_receipt)
    binding = w._binding(profile)
    active_path=Path(profile["source_root"])/"ACTIVE_RUNTIME_MANIFEST.json"
    active_source_manifest=reference(active_path) if active_path.exists() or active_path.is_symlink() else None
    value = dict(schema_version=VERSION, workspace_id=authority.workspace_id,
        authority_database=str(authority.path), authority_physical=list(authority.physical),
        registry_root=str(registry), profile=profile, binding=binding, config=config_ref,
        environment=environment, inventory=scan, parents=sorted(parents or []),
        active_source_manifest=active_source_manifest,
        purpose=a._text(purpose,"RECOVERY_V2_PURPOSE_REQUIRED",512),
        planned_at=a.stamp(), test_fixture=bool(allow_test_fixture),
        source_authority_restored=False, historical_pit_restored=False, default_changed=False)
    value["legacy_source_scope_status"]=authority.source_scope_status()
    value["epoch_id"] = "epoch_" + a.digest(value)
    # A generated plan and an externally supplied plan use exactly the same
    # semantic verifier. A caller can recompute a digest; it is not permission.
    _check(authority,value,phase="PLAN",allow_test_fixture=allow_test_fixture)
    return value


def _registration(authority, plan):
    return dict(schema_version=VERSION,profile=plan["profile"],binding=plan["binding"],
        epoch_id=plan["epoch_id"],workspace_id=authority.workspace_id,
        authority_database=str(authority.path),authority="LOCATION_ONLY_NOT_SOURCE_AUTHORITY")


def _environment_binding(environment, profile, *, allow_test_fixture, authority=None):
    value,ref=_document(environment["path"])
    if value.get("schema_version")=="ACTIVE_ENVIRONMENT_ACCEPTANCE_V1":
        from scripts.active_environment_acceptance_v1 import verify_acceptance
        need(ref==environment,"RECOVERY_V2_ENVIRONMENT_EXECUTION_BINDING_CHANGED")
        verify_acceptance(environment,profile,authority)
        return
    old._environment(environment,profile,allow_test_fixture=allow_test_fixture)
    source=Path(profile["source_root"])
    expected=[reference(source/"scripts"/name) for name in
        ("bootstrap_daily_runtime.py","runtime_doctor.py","runtime_environment_smoke.py")]
    verifier=reference(Path(old.__file__).resolve())
    verifier_sha=verifier["sha256"]
    manifest_path=source/"ACTIVE_RUNTIME_MANIFEST.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        manifest,_=_document(manifest_path)
        relative="scripts/codex_runtime_recovery_v1.py"
        verifier_sha=manifest.get("input_sha256",{}).get(relative,verifier_sha)
        if Path(old.__file__).resolve().is_relative_to(source.resolve()):
            need(manifest.get("output_sha256",{}).get(relative)==verifier["sha256"],
                 "RECOVERY_V2_ENVIRONMENT_VERIFIER_SOURCE_CHANGED")
        else:
            # Activation from the frozen control plane authenticates the
            # preimage. Later active-tree reads retain that exact original
            # verifier reference instead of replacing it with overlay bytes.
            need(verifier_sha==verifier["sha256"],"RECOVERY_V2_ENVIRONMENT_VERIFIER_SOURCE_CHANGED")
    refs=value["source_refs"]
    need(ref==environment and value["executable"]==reference(Path(profile["python"]).resolve(strict=True))
         and value["destination"]==str(Path(profile["python"]).parent.parent)
         and len(refs)==4 and refs[:3]==expected
         and Path(refs[3]["path"]).name=="codex_runtime_recovery_v1.py" and refs[3]["sha256"]==verifier_sha,
         "RECOVERY_V2_ENVIRONMENT_EXECUTION_BINDING_CHANGED")


def _check(authority, plan, *, exclude=(), phase="APPLY", allow_test_fixture=False):
    need(isinstance(authority,a.Authority) and isinstance(plan,dict),"RECOVERY_V2_AUTHORITY_AND_PLAN_REQUIRED")
    need(phase in {"PLAN","APPLY","CURRENT"},"RECOVERY_V2_CHECK_PHASE_REQUIRED")
    need(plan.get("legacy_source_scope_status")==authority.source_scope_status(),
         "RECOVERY_V2_SOURCE_SCOPE_STATUS_CHANGED")
    need(plan.get("schema_version") == VERSION and plan.get("workspace_id") == authority.workspace_id
         and plan.get("authority_database") == str(authority.path)
         and plan.get("authority_physical") == list(authority.physical)
         and plan.get("epoch_id") == "epoch_" + a.digest({k:v for k,v in plan.items() if k != "epoch_id"}),
         "RECOVERY_V2_PLAN_IDENTITY_CHANGED")
    need(not plan.get("test_fixture") or allow_test_fixture, "RECOVERY_V2_TEST_FIXTURE_NOT_PRODUCTION")
    need(type(plan.get("test_fixture")) is bool and all(plan.get(key) is False for key in
         ("source_authority_restored","historical_pit_restored","default_changed")),
         "RECOVERY_V2_PLAN_CANNOT_GRANT_SOURCE_OR_DEFAULT_AUTHORITY")
    profile=plan["profile"]
    need(w.validate_profile(profile)==profile and profile["backend"]=="premarket",
         "RECOVERY_V2_PREMARKET_PROFILE_REQUIRED")
    scan = plan["inventory"]
    need(scan["registrations"] or not scan["operations"],"RECOVERY_V2_ORPHAN_LEGACY_OPERATIONS")
    need(plan["registry_root"]==scan["registry_roots"][0],"RECOVERY_V2_PRIMARY_REGISTRY_MISMATCH")
    with authority._connect(readonly=True) as connection:
        onboarding=json.loads(connection.execute("SELECT value FROM meta WHERE key='onboarding'").fetchone()[0])
        if onboarding["mode"]=="FULL_LEGACY_IMPORT_PENDING":
            need(sorted(str(Path(p).absolute()) for p in onboarding["registry_roots"])==scan["registry_roots"],
                 "RECOVERY_V2_COMPLETE_ONBOARDING_REGISTRIES_REQUIRED")
        else:
            # Even a workspace that started empty must retain every registry
            # observed by its previous committed generations.
            ancestor_roots=set()
            for row in connection.execute("SELECT manifest FROM epochs WHERE state!='PREPARED' AND epoch_id!=?",(plan["epoch_id"],)):
                ancestor_roots.update(json.loads(row["manifest"])["inventory"]["registry_roots"])
            need(ancestor_roots<=set(scan["registry_roots"]),"RECOVERY_V2_ANCESTOR_REGISTRIES_OMITTED")
        parents=plan["parents"]
        need(isinstance(parents,list) and all(isinstance(p,str) for p in parents)
             and parents==sorted(set(parents)),"RECOVERY_V2_PARENT_SET_INVALID")
        prior=connection.execute("SELECT * FROM epochs WHERE epoch_id=?",(plan["epoch_id"],)).fetchone()
        if phase=="CURRENT":
            need(prior is not None and prior["state"]=="ACTIVE"
                 and prior["manifest"]==a.canonical(plan).decode()
                 and json.loads(prior["parents"])==parents and _heads(connection)==[plan["epoch_id"]],
                 "RECOVERY_V2_CURRENT_EPOCH_MANIFEST_CHANGED")
        else:
            need(parents==_heads(connection),"RECOVERY_V2_ALL_BRANCH_HEADS_REQUIRED")
            need(prior is None or phase=="APPLY" and prior["state"]=="PREPARED"
                 and prior["manifest"]==a.canonical(plan).decode(),"RECOVERY_V2_EPOCH_ALREADY_APPLIED_OR_CHANGED")
        for operation in scan["operations"]:
            _existing_import(connection,authority,operation)
    need(inventory(scan["registry_roots"], mappings=scan["mappings"],
                   budget_evidence=scan["budget_evidence"], exclude=exclude) == scan,
         "RECOVERY_V2_COMPLETE_INVENTORY_CHANGED")
    declared_roots={Path(p).absolute() for p in onboarding["state_roots"]}
    state=Path(profile["state_root"])
    need(any(state==root or state.is_relative_to(root) for root in declared_roots),"RECOVERY_V2_NEW_STATE_OUTSIDE_WORKSPACE")
    for registration in scan["registrations"]:
        previous=Path(registration["profile"]["state_root"])
        need(any(previous==root or previous.is_relative_to(root) for root in declared_roots),
             "RECOVERY_V2_WORKSPACE_BRANCH_OUTSIDE_ONBOARDING")
        need(state!=previous,"RECOVERY_V2_NEW_OWNED_STATE_REQUIRED")
    need(w._binding(profile)==plan["binding"],"RECOVERY_V2_NEW_BINDING_CHANGED")
    config,config_ref=_document(profile["config"])
    need(config_ref==plan["config"] and config.get("state_root")==profile["state_root"],
         "RECOVERY_V2_CONFIG_STATE_MISMATCH")
    destination=Path(plan["registry_root"])/"runtimes"/(profile["name"]+".json")
    if phase=="PLAN":
        need(not destination.exists() and not destination.is_symlink(),"RECOVERY_V2_NEW_RUNTIME_REQUIRED")
    elif destination.exists() or destination.is_symlink():
        need(prior is not None and _document(destination)[0]==_registration(authority,plan),
             "RECOVERY_V2_PREPARED_REGISTRATION_CHANGED")
    else:
        need(phase=="APPLY","RECOVERY_V2_CURRENT_REGISTRATION_MISSING")
    if phase!="CURRENT":
        need(not any(state.iterdir()),"RECOVERY_V2_NEW_EMPTY_OWNED_STATE_REQUIRED")
        scope=w.Runtime(Path(plan["registry_root"]),profile,plan["binding"]).directory
        need(not scope.exists() and not scope.is_symlink(),"RECOVERY_V2_EXISTING_SCOPE_REQUIRES_RECONCILIATION")
    _environment_binding(plan["environment"],profile,allow_test_fixture=allow_test_fixture,authority=authority)
    active_path=Path(profile["source_root"])/"ACTIVE_RUNTIME_MANIFEST.json"
    active_ref=reference(active_path) if active_path.exists() or active_path.is_symlink() else None
    need(active_ref==plan.get("active_source_manifest"),"RECOVERY_V2_ACTIVE_SOURCE_MANIFEST_CHANGED")
    if active_ref is not None:
        manifest,_=_document(active_path)
        need(manifest.get("schema")=="ACTIVE_RUNTIME_V1" and manifest.get("authority_binding")==dict(
            database=str(authority.path),workspace_id=authority.workspace_id,physical=list(authority.physical)),
            "RECOVERY_V2_ACTIVE_MANIFEST_AUTHORITY_MISMATCH")


def _import(connection, authority, operation):
    ref = operation["reference"]
    # Pin the first imported business meaning as well as the original bytes.
    # Reclassifying an old failure requires a separate reconciliation protocol,
    # never a new plan that silently skips the already imported reference.
    if _existing_import(connection,authority,operation) is not None:
        return
    business = operation["business"]
    bid = a.business_id(authority.workspace_id, business)
    row = connection.execute("SELECT * FROM requests WHERE business_id=?", (bid,)).fetchone()
    budget = operation["budget"]
    state = "IMPORTED_COMPLETED_UNVERIFIED" if operation["phase"] == "RECORDED" else "FAILED_OR_UNKNOWN"
    attempt = row["attempt_id"] if row else "legacy_" + ref["sha256"]
    known, limits, spent = budget["known"], budget["limits"], budget["spent"]
    if row:
        need(row["state"] not in a.ACTIVE, "RECOVERY_V2_ACTIVE_REQUEST_MUST_BE_RECONCILED")
        previous_limits, previous_spent = json.loads(row["limits_json"]), json.loads(row["spent_json"])
        # An unknown branch cannot be erased by a known or successful sibling.
        known = known and bool(row["budget_known"]) and set(limits) == set(previous_limits)
        if known:
            limits = {k:min(v,previous_limits[k]) for k,v in limits.items()}
            spent = {k:spent.get(k,0)+previous_spent.get(k,0) for k in limits}
        else:
            limits, spent = previous_limits, previous_spent
        if row["state"] == "FAILED_OR_UNKNOWN":
            state = "FAILED_OR_UNKNOWN"
    sequence = authority._event(connection,bid,attempt,"LEGACY_IMPORTED",
        dict(reference=ref,phase=operation["phase"],budget=budget))
    connection.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(business_id) DO UPDATE SET "
        "state=excluded.state,sequence=excluded.sequence,limits_json=excluded.limits_json,spent_json=excluded.spent_json,"
        "budget_known=excluded.budget_known,receipt=NULL,updated_at=excluded.updated_at",
        (bid,a.canonical(business).decode(),state,attempt,sequence,a.canonical(limits).decode(),
         a.canonical(spent).decode(),int(known),None,a.stamp()))
    connection.execute("INSERT INTO imported VALUES (?,?,?)",(ref["sha256"],bid,a.canonical(ref).decode()))


def apply_recovery(authority, plan, *, activation_authorization, allow_test_fixture=False, _test_hook=None):
    a._text(activation_authorization,"RECOVERY_V2_EXPLICIT_ACTIVATION_AUTHORIZATION_REQUIRED",512)
    need(_test_hook is None or allow_test_fixture and plan.get("test_fixture"),
         "RECOVERY_V2_TEST_HOOK_FORBIDDEN")
    destination=Path(plan["registry_root"])/"runtimes"/(plan["profile"]["name"]+".json")
    saved=_registration(authority,plan)
    # Reject an untrusted destination before acquiring/creating its lock. The
    # same semantic check runs again under the lock before any publication.
    _check(authority,plan,exclude=[destination],allow_test_fixture=allow_test_fixture)
    with w._lock(Path(plan["registry_root"])/"registry.lock"):
        _check(authority,plan,exclude=[destination],allow_test_fixture=allow_test_fixture)
        with authority._connect() as connection:
            need(_heads(connection)==plan["parents"],"RECOVERY_V2_BRANCH_HEADS_CHANGED")
            active = connection.execute("SELECT COUNT(*) FROM requests WHERE state IN ('CLAIMED','RUNNING')").fetchone()[0]
            need(active == 0,"RECOVERY_V2_ACTIVE_REQUESTS_BLOCK_MIGRATION")
            prior=connection.execute("SELECT * FROM epochs WHERE epoch_id=?",(plan["epoch_id"],)).fetchone()
            if prior:
                need(prior["state"]=="PREPARED" and prior["manifest"]==a.canonical(plan).decode(),
                     "RECOVERY_V2_EPOCH_ALREADY_APPLIED_OR_CHANGED")
            else:
                connection.execute("INSERT INTO epochs VALUES (?,?,?,?,?)",(plan["epoch_id"],
                    a.canonical(plan["parents"]).decode(),a.canonical(plan).decode(),"PREPARED",a.stamp()))
        if _test_hook:
            _test_hook("AFTER_PREPARE")
        if destination.exists() or destination.is_symlink():
            need(w._read(destination)==saved,"RECOVERY_V2_PREPARED_REGISTRATION_CHANGED")
        else:
            w._write(destination,saved)
        if _test_hook:
            _test_hook("AFTER_REGISTRY_PUBLICATION")
        _check(authority,plan,exclude=[destination],allow_test_fixture=allow_test_fixture)
        with authority._connect() as connection:
            need(_heads(connection)==plan["parents"],"RECOVERY_V2_BRANCH_HEADS_CHANGED")
            need(connection.execute("SELECT COUNT(*) FROM requests WHERE state IN ('CLAIMED','RUNNING')").fetchone()[0]==0,
                 "RECOVERY_V2_ACTIVE_REQUESTS_BLOCK_MIGRATION")
            for operation in plan["inventory"]["operations"]:
                _import(connection,authority,operation)
            # The unique partial index is the final defence against two active
            # epochs; old runtime files remain exact historical records.
            connection.execute("UPDATE epochs SET state='RETIRED' WHERE state='ACTIVE'")
            connection.execute("UPDATE epochs SET state='ACTIVE' WHERE epoch_id=? AND state='PREPARED'",(plan["epoch_id"],))
            authority._event(connection,None,None,"EPOCH_ACTIVATED",
                             dict(epoch_id=plan["epoch_id"],parents=plan["parents"],inventory_sha256=a.digest(plan["inventory"]),
                                  activation_authorization=activation_authorization))
            if _test_hook:
                _test_hook("BEFORE_ATOMIC_COMMIT")
    return dict(status="REGISTERED_NEW_RECOVERY_EPOCH",epoch_id=plan["epoch_id"],
                active=True,source_authority_restored=False,historical_pit_restored=False,
                default_changed=False,registry=reference(destination),legacy_source_scope_status=authority.source_scope_status())


class RecoveryRuntimeV2(w.Runtime):
    def __init__(self, registry_root, saved, registry_ref, *, allow_test_fixture=False):
        super().__init__(registry_root,saved["profile"],saved["binding"])
        self.authority=a.Authority(saved["authority_database"],saved["workspace_id"])
        self.epoch_id=saved["epoch_id"]
        self.registry_ref=registry_ref
        self.allow_test_fixture=allow_test_fixture
        self.check_binding()

    def check_binding(self):
        super().check_binding()
        need(reference(self.registry_ref["path"])==self.registry_ref,"RECOVERY_V2_REGISTRY_CHANGED")
        epoch=self.authority.active_epoch()
        need(epoch is not None and epoch["epoch_id"]==self.epoch_id,"RECOVERY_V2_EPOCH_NOT_ACTIVE")
        plan=json.loads(epoch["manifest"])
        self.active_source_attested=plan.get("active_source_manifest") is not None
        need(not plan["test_fixture"] or self.allow_test_fixture,"RECOVERY_V2_TEST_FIXTURE_NOT_PRODUCTION")
        # New journals live under the owned job/state directories, not legacy
        # scope controls. A new legacy operation in the current scope is also
        # an inventory change; it must not hide a failure from this authority.
        _check(self.authority,plan,exclude=[self.registry_ref["path"]],phase="CURRENT",
               allow_test_fixture=self.allow_test_fixture)

    def guard_ancestor_request(self, identity):
        bid=a.business_id(self.authority.workspace_id,a.canonical_business(identity))
        saved=self.authority.inspect(bid)
        need(saved is None or saved["state"]=="RECORDED","AUTHORITY_EXISTING_REQUEST_RECONCILE_NO_RETRY")


def load_runtime(name, registry_root, *, allow_test_fixture=False):
    registry=Path(registry_root).absolute()
    saved,ref=_document(registry/"runtimes"/(w._name(name)+".json"))
    need(saved.get("schema_version")==VERSION,"RECOVERY_V2_RUNTIME_REQUIRED")
    return RecoveryRuntimeV2(registry,saved,ref,allow_test_fixture=allow_test_fixture)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    commands=parser.add_subparsers(dest="command",required=True)
    for name in ("plan","apply","read"):
        sub=commands.add_parser(name,allow_abbrev=False)
        sub.add_argument("--authority-database",type=Path,required=True)
        sub.add_argument("--workspace-id",required=True)
        if name=="plan":
            sub.add_argument("--registry-root",type=Path,action="append",required=True)
            sub.add_argument("--profile",type=Path,required=True)
            sub.add_argument("--environment-receipt",type=Path,required=True)
            sub.add_argument("--parent-epoch",action="append",default=[])
            sub.add_argument("--mappings",type=Path)
            sub.add_argument("--budget-evidence",type=Path)
            sub.add_argument("--purpose",required=True)
            sub.add_argument("--output",type=Path,required=True)
        elif name=="apply":
            sub.add_argument("--plan",type=Path,required=True)
            sub.add_argument("--plan-sha256",required=True)
            sub.add_argument("--authorize-activation",action="store_true")
            sub.add_argument("--authorization-id",required=True)
        else:
            sub.add_argument("--business-id")
    args=parser.parse_args(argv)
    authority=a.Authority(args.authority_database,args.workspace_id)
    if args.command=="plan":
        profile,_=_document(args.profile)
        mappings=_document(args.mappings)[0] if args.mappings else None
        budgets=_document(args.budget_evidence)[0] if args.budget_evidence else None
        proposal=plan_recovery(authority,args.registry_root,profile,reference(args.environment_receipt),
            parents=args.parent_epoch,mappings=mappings,budget_evidence=budgets,purpose=args.purpose)
        # A private plan is a new file, not a registry/epoch activation.
        w._write(args.output,proposal)
        result=dict(status="PLAN_ONLY_NO_ACTIVATION",plan=reference(args.output),
                    epoch_id=proposal["epoch_id"],activation_authorized=False,source_authority_restored=False)
    elif args.command=="apply":
        need(args.authorize_activation,"RECOVERY_V2_EXPLICIT_ACTIVATION_SWITCH_REQUIRED")
        proposal,ref=_document(args.plan)
        need(ref["sha256"]==args.plan_sha256,"RECOVERY_V2_PLAN_CARRIER_SHA_MISMATCH")
        result=apply_recovery(authority,proposal,activation_authorization=args.authorization_id)
    else:
        result=authority.inspect(args.business_id) if args.business_id else authority.active_epoch()
        if result and not args.business_id:
            result={k:v for k,v in result.items() if k!="manifest"}
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
