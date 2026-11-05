"""Transactional authority for business effects, independent of execution versions.

This database is control state, never a source of market facts. Creation is an
explicit onboarding operation; ordinary callers cannot create or reset it.
Historical journals are imported before activation. Worker capabilities are
opaque, narrowly scoped, persisted hashes and do not authorize a second claim.
The original authority carrier is pinned at initialization. Copying or moving
its bytes cannot create another writable authority; carrier migration needs a
separate protocol that fences the original owner and is not provided here.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import stat

VERSION = "BUSINESS_AUTHORITY_V1"
IDENTITY_VERSION = "BUSINESS_IDENTITY_V1"
RECEIPT_VERSION = "BUSINESS_AUTHORITY_RECEIPT_V1"
ACTIVE = {"CLAIMED", "RUNNING"}
TERMINAL = {"RECORDED", "FAILED_OR_UNKNOWN", "IMPORTED_COMPLETED_UNVERIFIED"}
OPERATIONS = {"PREMARKET", "CAPTURE", "CORE_COMPUTE", "OUTCOME", "THEME",
              "EXPORT", "RESEARCH", "ARCHIVE", "MAINTENANCE"}
ALLOWED_STAGES = {
    "PREMARKET": frozenset({"storage","worker","network","provider","metrics","capture"}),
    "CAPTURE": frozenset({"storage","worker","network","provider","metrics","capture"}),
    "CORE_COMPUTE": frozenset({"storage","worker","metrics"}),
    "OUTCOME": frozenset({"storage","worker","metrics"}),
    "THEME": frozenset({"storage","worker","metrics"}),
    "RESEARCH": frozenset({"storage","worker","metrics"}),
    "ARCHIVE": frozenset({"storage","worker","metrics"}),
    "EXPORT": frozenset({"storage","metrics"}),
    "MAINTENANCE": frozenset({"storage","metrics"}),
}
_SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE requests(business_id TEXT PRIMARY KEY, identity TEXT NOT NULL,
 state TEXT NOT NULL, attempt_id TEXT NOT NULL UNIQUE, sequence INTEGER NOT NULL,
 limits_json TEXT NOT NULL, spent_json TEXT NOT NULL, budget_known INTEGER NOT NULL,
 receipt TEXT, updated_at TEXT NOT NULL);
CREATE TABLE events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 business_id TEXT, attempt_id TEXT, event TEXT NOT NULL, data TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE capabilities(token_hash TEXT PRIMARY KEY, business_id TEXT NOT NULL,
 attempt_id TEXT NOT NULL, stages TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE effects(business_id TEXT NOT NULL, effect_kind TEXT NOT NULL,
 stable_effect_id TEXT NOT NULL, attempt_id TEXT NOT NULL, state TEXT NOT NULL,
 receipt TEXT, PRIMARY KEY(business_id,effect_kind,stable_effect_id));
CREATE UNIQUE INDEX global_provider_effect ON effects(effect_kind,stable_effect_id)
 WHERE effect_kind='PROVIDER_REQUEST';
CREATE TABLE source_scopes(business_id TEXT NOT NULL,effect_kind TEXT NOT NULL,stable_effect_id TEXT NOT NULL,
 attempt_id TEXT NOT NULL,provider TEXT NOT NULL,endpoint TEXT NOT NULL,window_start TEXT NOT NULL,
 window_end TEXT NOT NULL,codes_json TEXT NOT NULL,request_semantic_id TEXT NOT NULL,
 PRIMARY KEY(business_id,effect_kind,stable_effect_id));
CREATE INDEX source_scope_overlap ON source_scopes(provider,endpoint,window_start,window_end);
CREATE TABLE service_hosts(role TEXT PRIMARY KEY,host_id TEXT NOT NULL,epoch_id TEXT NOT NULL,
 business_id TEXT NOT NULL,attempt_id TEXT NOT NULL,attestation_secret TEXT NOT NULL);
CREATE TABLE imported(reference_sha TEXT PRIMARY KEY, business_id TEXT NOT NULL,
 reference TEXT NOT NULL);
CREATE TABLE epochs(epoch_id TEXT PRIMARY KEY, parents TEXT NOT NULL,
 manifest TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX one_active_epoch ON epochs(state) WHERE state='ACTIVE';
"""


class AuthorityError(ValueError):
    pass


def need(condition, reason):
    if not condition:
        raise AuthorityError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def _text(value, code, maximum=256):
    need(isinstance(value, str) and 0 < len(value) <= maximum and
         all(ord(c) >= 32 for c in value), code)
    return value


def _ids(values, field):
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    need(isinstance(values, (list, tuple)) and len(values) <= 10000, "BUSINESS_INPUT_IDS_INVALID")
    output = [_text(v, "BUSINESS_INPUT_ID_INVALID") for v in values]
    need(len(output) == len(set(output)), "BUSINESS_DUPLICATE_INPUT_ID")
    return sorted(output)


def canonical_source_scope(value):
    fields={"schema","provider","endpoint","window_start","window_end","codes","request_semantic_id"}
    need(isinstance(value,dict) and set(value)==fields and value["schema"]=="SOURCE_REQUEST_SCOPE_V1",
         "AUTHORITY_SOURCE_SCOPE_REQUIRED")
    result=deepcopy(value)
    _text(result["provider"],"AUTHORITY_SOURCE_PROVIDER_REQUIRED",64)
    _text(result["endpoint"],"AUTHORITY_SOURCE_ENDPOINT_REQUIRED",128)
    for key in ("window_start","window_end"):
        need(isinstance(result[key],str) and date.fromisoformat(result[key]).isoformat()==result[key],
             "AUTHORITY_SOURCE_WINDOW_INVALID")
    need(result["window_start"]<=result["window_end"],"AUTHORITY_SOURCE_WINDOW_INVALID")
    need(isinstance(result["codes"],list),"AUTHORITY_SOURCE_CODES_REQUIRED")
    result["codes"]=sorted(set(code.upper() for code in _ids(result["codes"],"codes")))
    semantic=result["request_semantic_id"]
    need(isinstance(semantic,str) and len(semantic)==64 and all(c in "0123456789abcdef" for c in semantic),
         "AUTHORITY_SOURCE_SEMANTIC_HASH_REQUIRED")
    return result


def canonical_business(identity, *, operation=None):
    """Select economic/operation semantics, never caller-controlled retry aliases.

    Unknown legacy shapes fail closed. Callers must supply an operation and the
    actual input/run identities. A human request key is audit metadata only.
    """
    need(isinstance(identity, dict), "BUSINESS_IDENTITY_REQUIRED")
    if identity.get("schema") == IDENTITY_VERSION:
        expected = {"schema", "operation", "observation_date", "target_session", "strategy_id",
                    "input_ids", "run_ids", "subject", "parameters"}
        need(set(identity) == expected, "BUSINESS_IDENTITY_FIELDS_CHANGED")
    selected = operation or identity.get("operation") or identity.get("business_operation")
    if selected is None and identity.get("backend") == "premarket":
        selected = "PREMARKET"
    need(isinstance(selected, str) and selected.upper() in OPERATIONS, "BUSINESS_OPERATION_EXPLICIT_REQUIRED")
    selected = selected.upper()
    observed = identity.get("observation_date") or identity.get("session_date")
    target = identity.get("target_session")
    for value in (observed, target):
        if value is not None:
            need(isinstance(value, str) and date.fromisoformat(value).isoformat() == value,
                 "BUSINESS_SESSION_INVALID")
    if selected == "PREMARKET":
        need(observed is not None and target is not None and observed < target,
             "BUSINESS_PREMARKET_SESSIONS_REQUIRED")
    inputs = _ids(identity.get("input_ids", identity.get("input_id")), "input")
    runs = _ids(identity.get("run_ids", identity.get("run_id")), "run")
    strategy = identity.get("strategy_id")
    if selected == "PREMARKET":
        strategy = strategy or "SHORT_HORIZON_STRENGTH_R2"
        # Full daily work is the same business request, irrespective of a source
        # bundle, parameter version, stage subset, or newly minted input alias.
        inputs, runs = [], []
    if strategy is not None:
        _text(strategy, "BUSINESS_STRATEGY_INVALID")
    subject = identity.get("subject")
    parameters = identity.get("parameters", {})
    if selected == "PREMARKET":
        subject, parameters = None, {}
    elif selected == "CAPTURE":
        # Dataset and window come from the actual provider request, never a
        # caller's arbitrary subject/semantic hash or human request identifier.
        if identity.get("schema") == IDENTITY_VERSION:
            need(isinstance(subject,str) and isinstance(parameters,dict),"BUSINESS_CAPTURE_FIELDS_REQUIRED")
            provider,dataset = subject.split(":",1) if ":" in subject else (None,None)
            start,end,codes = parameters.get("window_start"),parameters.get("window_end"),parameters.get("codes")
        else:
            provider,dataset=identity.get("provider"),identity.get("dataset")
            start,end,codes=identity.get("window_start"),identity.get("window_end"),identity.get("codes",[])
        _text(provider,"BUSINESS_CAPTURE_PROVIDER_REQUIRED",64)
        _text(dataset,"BUSINESS_CAPTURE_DATASET_REQUIRED",128)
        need(":" not in provider and ":" not in dataset,"BUSINESS_CAPTURE_NAME_INVALID")
        need(isinstance(start,str) and isinstance(end,str) and date.fromisoformat(start).isoformat()==start
             and date.fromisoformat(end).isoformat()==end and start<=end,"BUSINESS_CAPTURE_WINDOW_REQUIRED")
        subject=provider+":"+dataset
        parameters=dict(window_start=start,window_end=end,codes=_ids(codes,"codes"))
        observed,target,strategy,inputs,runs=None,None,None,[],[]
    elif selected in {"CORE_COMPUTE","OUTCOME","THEME","ARCHIVE","EXPORT"}:
        need(bool(inputs or runs),"BUSINESS_SEMANTIC_INPUT_REQUIRED")
        subject,parameters=None,{}
        if selected=="CORE_COMPUTE":
            need(bool(inputs) and bool(strategy),"BUSINESS_CORE_INPUT_AND_STRATEGY_REQUIRED")
    else:
        need(subject is not None or bool(inputs or runs), "BUSINESS_SEMANTIC_SUBJECT_REQUIRED")
        if subject is not None:
            _text(subject, "BUSINESS_SUBJECT_INVALID")
        need(isinstance(parameters, dict) and len(canonical(parameters)) <= 16384,
             "BUSINESS_PARAMETERS_INVALID")
        forbidden = {"request_key", "engine", "source_root", "python", "inode", "device", "version"}
        need(not forbidden.intersection(parameters), "BUSINESS_EXECUTION_ALIAS_IN_PARAMETERS")
    if selected == "OUTCOME":
        need(bool(inputs) and bool(runs), "BUSINESS_OUTCOME_RUN_AND_INPUT_REQUIRED")
    result = dict(schema=IDENTITY_VERSION, operation=selected, observation_date=observed,
                  target_session=target, strategy_id=strategy, input_ids=inputs,
                  run_ids=runs, subject=subject, parameters=deepcopy(parameters))
    need(len(canonical(result)) <= 32768, "BUSINESS_IDENTITY_TOO_LARGE")
    return result


def business_id(workspace_id, business):
    _text(workspace_id, "AUTHORITY_WORKSPACE_ID_REQUIRED")
    return "business_" + digest({"workspace_id": workspace_id,
                                  "business": canonical_business(business)})


def _numbers(value):
    need(isinstance(value, dict) and len(value) <= 64, "AUTHORITY_BUDGET_INVALID")
    output = {}
    for key, number in value.items():
        _text(key, "AUTHORITY_BUDGET_NAME_INVALID", 128)
        need(type(number) in (int, float) and math.isfinite(number) and number >= 0,
             "AUTHORITY_BUDGET_VALUE_INVALID")
        output[key] = number
    return output


def _safe_file(path):
    path = Path(path).expanduser().absolute()
    need(path.resolve(strict=True) == path, "AUTHORITY_PATH_ALIAS_REJECTED")
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "AUTHORITY_REGULAR_SINGLE_LINK_REQUIRED")
    return path, (info.st_dev, info.st_ino)


class Authority:
    """Open existing control state. No method implicitly initializes or retries."""
    def __init__(self, database, workspace_id):
        self.path, self.physical = _safe_file(database)
        self.workspace_id = _text(workspace_id, "AUTHORITY_WORKSPACE_ID_REQUIRED")
        with self._connect(readonly=True):
            pass  # Opening validates the persisted original carrier as well.

    def _check_connection_identity(self, connection):
        values = dict(connection.execute("SELECT key,value FROM meta"))
        need(values.get("schema") == VERSION and values.get("workspace_id") == self.workspace_id,
             "AUTHORITY_DATABASE_IDENTITY_MISMATCH")
        expected = dict(schema="AUTHORITY_CARRIER_V1", path=str(self.path),
                        device=self.physical[0], inode=self.physical[1])
        need(values.get("original_carrier") == canonical(expected).decode(),
             "AUTHORITY_ORIGINAL_CARRIER_MISMATCH")

    @classmethod
    def initialize(cls, database, workspace_id, *, onboarding):
        """Explicit control-store creation; activation separately needs an epoch.

        onboarding must record EMPTY_NEW_WORKSPACE or FULL_LEGACY_IMPORT_PENDING.
        A nonempty historical workspace may never use the former declaration.
        Actual emptiness/reference verification belongs to the onboarding plan.
        """
        _text(workspace_id, "AUTHORITY_WORKSPACE_ID_REQUIRED")
        need(isinstance(onboarding, dict) and onboarding.get("mode") in
             {"EMPTY_NEW_WORKSPACE", "FULL_LEGACY_IMPORT_PENDING"}, "AUTHORITY_ONBOARDING_REQUIRED")
        roots = onboarding.get("state_roots")
        need(isinstance(roots, list) and roots, "AUTHORITY_STATE_ROOTS_REQUIRED")
        for root in roots:
            directory = Path(root).absolute()
            need(directory.resolve(strict=True) == directory and directory.is_dir(),
                 "AUTHORITY_STATE_ROOT_INVALID")
            if onboarding["mode"] == "EMPTY_NEW_WORKSPACE":
                need(not any(directory.iterdir()), "AUTHORITY_NONEMPTY_STATE_REQUIRES_IMPORT")
        if onboarding["mode"] == "FULL_LEGACY_IMPORT_PENDING":
            registries=onboarding.get("registry_roots")
            need(isinstance(registries,list) and registries,"AUTHORITY_COMPLETE_REGISTRY_SET_REQUIRED")
            for registry in registries:
                directory=Path(registry).absolute()
                need(directory.resolve(strict=True)==directory and directory.is_dir(),
                     "AUTHORITY_REGISTRY_ROOT_INVALID")
        path = Path(database).absolute()
        need(path.parent.resolve(strict=True) == path.parent and path.parent.is_dir(),
             "AUTHORITY_PARENT_REQUIRED")
        with path.open("xb") as stream:
            os.chmod(path, 0o600)
            created = os.fstat(stream.fileno())
            physical = (created.st_dev, created.st_ino)
            stream.flush()
            os.fsync(stream.fileno())
        connection = sqlite3.connect(path)
        try:
            need(_safe_file(path)[1] == physical, "AUTHORITY_DATABASE_REPLACED")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(_SCHEMA)
            connection.executemany("INSERT INTO meta(key,value) VALUES (?,?)", [
                ("schema", VERSION), ("workspace_id", workspace_id),
                ("original_carrier", canonical(dict(schema="AUTHORITY_CARRIER_V1", path=str(path),
                    device=physical[0], inode=physical[1])).decode()),
                ("onboarding", canonical(onboarding).decode()), ("created_at", stamp())])
            connection.commit()
            need(_safe_file(path)[1] == physical, "AUTHORITY_DATABASE_REPLACED")
        finally:
            connection.close()
        return cls(path, workspace_id)

    @contextmanager
    def _connect(self, readonly=False):
        need(_safe_file(self.path)[1] == self.physical, "AUTHORITY_DATABASE_REPLACED")
        uri = self.path.as_uri() + ("?mode=ro" if readonly else "?mode=rw")
        connection = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA foreign_keys=ON")
            need(_safe_file(self.path)[1] == self.physical, "AUTHORITY_DATABASE_REPLACED")
            self._check_connection_identity(connection)
            if not readonly:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                self._check_connection_identity(connection)
            yield connection
            need(_safe_file(self.path)[1] == self.physical, "AUTHORITY_DATABASE_REPLACED")
            self._check_connection_identity(connection)
            if not readonly:
                connection.commit()
        except BaseException:
            if not readonly:
                connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _event(connection, bid, attempt, event, data):
        cursor = connection.execute("INSERT INTO events(business_id,attempt_id,event,data,at) VALUES (?,?,?,?,?)",
                                    (bid, attempt, event, canonical(data).decode(), stamp()))
        return cursor.lastrowid

    def _receipt(self, row, *, reused=False):
        limits, spent = json.loads(row["limits_json"]), json.loads(row["spent_json"])
        return dict(schema=RECEIPT_VERSION, workspace_id=self.workspace_id,
            business_id=row["business_id"], attempt_id=row["attempt_id"], sequence=row["sequence"],
            status="REUSED" if reused else row["state"], state=row["state"],
            business=json.loads(row["identity"]), budget_known=bool(row["budget_known"]),
            remaining_limits={key:max(0, value-spent.get(key, 0)) for key,value in limits.items()},
            remaining_limits_scope="THIS_BUSINESS",outcome_family_constraint=json.loads(row["identity"])["operation"]=="OUTCOME",
            cumulative_spent=spent, receipt=json.loads(row["receipt"]) if row["receipt"] else None,
            authority_updated_at=row["updated_at"],source_authority_restored=False)

    def inspect(self, bid):
        with self._connect(readonly=True) as connection:
            row = connection.execute("SELECT * FROM requests WHERE business_id=?", (bid,)).fetchone()
            return self._receipt(row) if row else None

    def active_epoch(self):
        with self._connect(readonly=True) as connection:
            row = connection.execute("SELECT * FROM epochs WHERE state='ACTIVE'").fetchone()
            return dict(row) if row else None

    @staticmethod
    def _source_scope_status(connection):
        row=connection.execute("SELECT value FROM meta WHERE key='onboarding'").fetchone()
        mode=json.loads(row[0])["mode"] if row is not None else None
        if mode=="EMPTY_NEW_WORKSPACE":
            return "FRESH_EMPTY_WORKSPACE_NO_LEGACY_SOURCE_EFFECTS"
        return "LEGACY_SOURCE_SCOPES_UNRECONCILED"

    def source_scope_status(self):
        with self._connect(readonly=True) as connection:
            return self._source_scope_status(connection)

    @staticmethod
    def _outcome_family_rows(connection, run_id):
        # The original schema already contains the immutable run identities.
        # No side database, caller-supplied family key, or migration is needed.
        rows=list(connection.execute("SELECT * FROM requests WHERE json_extract(identity,'$.operation')='OUTCOME' "
            "AND EXISTS(SELECT 1 FROM json_each(requests.identity,'$.run_ids') WHERE value=?) LIMIT 10001",(run_id,)))
        need(len(rows)<=10000,"AUTHORITY_OUTCOME_FAMILY_ROW_LIMIT")
        return rows

    @classmethod
    def _outcome_family_guard(cls, connection, business, limits, delta, *, current_bid=None, new_revision=False,
                              require_headroom=False):
        if business["operation"]!="OUTCOME":
            return
        runs=business["run_ids"]
        need(len(runs)==1,"AUTHORITY_OUTCOME_SINGLE_RUN_REQUIRED")
        rows=cls._outcome_family_rows(connection,runs[0])
        previous_inputs=set()
        spent={key:0 for key in limits}
        for row in rows:
            prior=json.loads(row["identity"])
            need(prior["run_ids"]==runs,"AUTHORITY_OUTCOME_LEGACY_BATCH_RECONCILIATION_REQUIRED")
            need(row["business_id"]==current_bid or row["state"]=="RECORDED",
                 "AUTHORITY_OUTCOME_FAMILY_RECONCILE_NO_RETRY")
            need(row["budget_known"],"AUTHORITY_OUTCOME_FAMILY_UNKNOWN_BUDGET")
            need(_numbers(json.loads(row["limits_json"]))==limits,"AUTHORITY_OUTCOME_FAMILY_LIMITS_CHANGED")
            values=_numbers(json.loads(row["spent_json"]))
            need(set(values)<=set(limits),"AUTHORITY_OUTCOME_FAMILY_UNDECLARED_COUNTER")
            for key,value in values.items():
                spent[key]+=value
            previous_inputs.update(prior["input_ids"])
        if new_revision and rows:
            # Reordering, subsetting or recombining existing inputs is not a
            # new observation. Native writers must independently authenticate
            # the actual raw inputs represented by each newly supplied ID.
            need(set(business["input_ids"])-previous_inputs,"AUTHORITY_OUTCOME_FAMILY_NEW_INPUT_REQUIRED")
        need(set(delta)<=set(limits),"AUTHORITY_OUTCOME_FAMILY_UNDECLARED_COUNTER")
        need(all(spent[key]+delta.get(key,0)<=limit for key,limit in limits.items()),
             "AUTHORITY_OUTCOME_FAMILY_CUMULATIVE_BUDGET_EXCEEDED")
        if require_headroom or new_revision and rows:
            need(all(spent[key]<limit for key,limit in limits.items() if limit>0),
                 "AUTHORITY_OUTCOME_FAMILY_BUDGET_EXHAUSTED")

    def outcome_family_budget(self, run_id):
        """Read-only total of recorded row costs, never a new spending permit.

        Unknown costs, conflicting historical ceilings, or legacy batches keep
        remaining_family_limits unknown. Row balances retain their older
        THIS_BUSINESS meaning; all new execution uses the atomic family guard.
        """
        _text(run_id,"BUSINESS_INPUT_ID_INVALID")
        with self._connect(readonly=True) as connection:
            rows=self._outcome_family_rows(connection,run_id)
            limits=_numbers(json.loads(rows[0]["limits_json"])) if rows else None
            spent={}
            consistent=bool(rows)
            known=bool(rows)
            states={}
            for row in rows:
                identity=json.loads(row["identity"])
                values=_numbers(json.loads(row["spent_json"]))
                current_limits=_numbers(json.loads(row["limits_json"]))
                consistent=consistent and current_limits==limits and identity["run_ids"]==[run_id]
                known=known and bool(row["budget_known"]) and set(values)<=set(current_limits)
                for key,value in values.items():spent[key]=spent.get(key,0)+value
                states[row["state"]]=states.get(row["state"],0)+1
            known=known and consistent and all(spent.get(key,0)<=value for key,value in (limits or {}).items())
            return dict(scope="OUTCOME_FAMILY_BY_SINGLE_IMMUTABLE_RUN",run_id=run_id,business_count=len(rows),
                limits=limits if consistent else None,recorded_spent=spent,budget_known=bool(known),
                remaining_family_limits={key:value-spent.get(key,0) for key,value in limits.items()} if known else None,
                states=states,execution_authorized=False)

    @classmethod
    def _require_source_scope_inventory(cls,connection):
        # No operator boolean, new epoch or environment receipt can turn
        # unknown historical provider effects into an empty scope inventory.
        need(cls._source_scope_status(connection)=="FRESH_EMPTY_WORKSPACE_NO_LEGACY_SOURCE_EFFECTS",
             "LEGACY_SOURCE_SCOPES_UNRECONCILED")

    def claim(self, business, *, request_metadata=None, limits=None, epoch_id=None):
        business = canonical_business(business)
        bid = business_id(self.workspace_id, business)
        limits = _numbers(limits or {})
        metadata = deepcopy(request_metadata or {})
        need(set(metadata) <= {"request_key", "engine", "entry_version", "source_sha256"}
             and len(canonical(metadata)) <= 2048, "AUTHORITY_METADATA_NOT_ALLOWED")
        with self._connect() as connection:
            epoch = connection.execute("SELECT epoch_id FROM epochs WHERE state='ACTIVE'").fetchone()
            need(epoch is not None and epoch_id == epoch["epoch_id"], "AUTHORITY_ACTIVE_EPOCH_REQUIRED")
            row = connection.execute("SELECT * FROM requests WHERE business_id=?", (bid,)).fetchone()
            if row:
                need(row["identity"] == canonical(business).decode(), "AUTHORITY_BUSINESS_COLLISION")
                need(row["state"] == "RECORDED", "AUTHORITY_EXISTING_REQUEST_RECONCILE_NO_RETRY")
                return self._receipt(row, reused=True)
            self._outcome_family_guard(connection,business,limits,{},new_revision=True)
            attempt = "attempt_" + secrets.token_hex(24)
            sequence = self._event(connection, bid, attempt, "CLAIMED", {"metadata":metadata,"epoch_id":epoch_id})
            connection.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?)",
                (bid, canonical(business).decode(), "CLAIMED", attempt, sequence,
                 canonical(limits).decode(), "{}", 1, None, stamp()))
            return self._receipt(connection.execute("SELECT * FROM requests WHERE business_id=?", (bid,)).fetchone())

    def _claimed(self, connection, claim):
        need(claim.get("schema") == RECEIPT_VERSION and claim.get("workspace_id") == self.workspace_id,
             "AUTHORITY_CLAIM_IDENTITY_INVALID")
        row = connection.execute("SELECT * FROM requests WHERE business_id=?", (claim["business_id"],)).fetchone()
        need(row is not None and row["attempt_id"] == claim.get("attempt_id") and row["state"] in ACTIVE,
             "AUTHORITY_CLAIM_NOT_ACTIVE")
        return row

    def start(self, claim):
        with self._connect() as connection:
            row = self._claimed(connection, claim)
            need(row["state"] == "CLAIMED", "AUTHORITY_WORKER_ALREADY_STARTED")
            self._outcome_family_guard(connection,json.loads(row["identity"]),
                _numbers(json.loads(row["limits_json"])),{},current_bid=row["business_id"],require_headroom=True)
            sequence = self._event(connection,row["business_id"],row["attempt_id"],"RUNNING",{})
            connection.execute("UPDATE requests SET state='RUNNING',sequence=?,updated_at=? WHERE business_id=?",
                               (sequence,stamp(),row["business_id"]))

    def charge(self, claim, delta):
        delta = _numbers(delta)
        with self._connect() as connection:
            row = self._claimed(connection,claim)
            need(row["budget_known"], "AUTHORITY_UNKNOWN_CUMULATIVE_BUDGET")
            limits, spent = json.loads(row["limits_json"]), json.loads(row["spent_json"])
            need(set(delta) <= set(limits), "AUTHORITY_UNDECLARED_BUDGET_COUNTER")
            updated = {**spent, **{key:spent.get(key,0)+value for key,value in delta.items()}}
            need(all(updated[key] <= limits[key] for key in updated), "AUTHORITY_CUMULATIVE_BUDGET_EXCEEDED")
            self._outcome_family_guard(connection,json.loads(row["identity"]),limits,delta,current_bid=row["business_id"])
            sequence = self._event(connection,row["business_id"],row["attempt_id"],"CHARGED",delta)
            connection.execute("UPDATE requests SET spent_json=?,sequence=?,updated_at=? WHERE business_id=?",
                               (canonical(updated).decode(),sequence,stamp(),row["business_id"]))

    def finish(self, claim, *, success, receipt=None, budget_known=True):
        need(type(success) is bool and type(budget_known) is bool, "AUTHORITY_FINISH_FLAGS_INVALID")
        need(receipt is None or isinstance(receipt,dict) and len(canonical(receipt)) <= 256*1024,
             "AUTHORITY_RECEIPT_INVALID")
        need(not success or receipt is not None and budget_known, "AUTHORITY_SUCCESS_REQUIRES_RECEIPT_AND_BUDGET")
        with self._connect() as connection:
            row = self._claimed(connection,claim)
            state = "RECORDED" if success else "FAILED_OR_UNKNOWN"
            sequence = self._event(connection,row["business_id"],row["attempt_id"],state,
                                   {"receipt_sha256":digest(receipt) if receipt is not None else None})
            connection.execute("UPDATE requests SET state=?,receipt=?,budget_known=?,sequence=?,updated_at=? WHERE business_id=?",
                (state,canonical(receipt).decode() if receipt is not None else None,
                 int(budget_known and row["budget_known"]),sequence,stamp(),row["business_id"]))
            connection.execute("UPDATE capabilities SET revoked=1 WHERE business_id=?",(row["business_id"],))
            connection.execute("UPDATE effects SET state='UNKNOWN' WHERE business_id=? AND state='STARTED'",
                               (row["business_id"],))
            return self._receipt(connection.execute("SELECT * FROM requests WHERE business_id=?",(row["business_id"],)).fetchone())

    def authorize_resume(self, bid, *, previous_attempt_id, authorization_id, reconciliation_sha256):
        """Explicit reconciliation only; never raises ceilings or clears history."""
        _text(authorization_id,"AUTHORITY_RESUME_AUTHORIZATION_REQUIRED")
        need(isinstance(reconciliation_sha256,str) and len(reconciliation_sha256)==64 and
             all(c in "0123456789abcdef" for c in reconciliation_sha256), "AUTHORITY_RECONCILIATION_HASH_REQUIRED")
        with self._connect() as connection:
            row=connection.execute("SELECT * FROM requests WHERE business_id=?",(bid,)).fetchone()
            need(row is not None and row["attempt_id"]==previous_attempt_id and row["state"]=="FAILED_OR_UNKNOWN",
                 "AUTHORITY_RESUME_PARENT_INVALID")
            need(row["budget_known"],"AUTHORITY_UNKNOWN_CUMULATIVE_BUDGET")
            limits,spent=json.loads(row["limits_json"]),json.loads(row["spent_json"])
            need(all(spent.get(k,0)<v for k,v in limits.items()),"AUTHORITY_NO_REMAINING_BUDGET")
            self._outcome_family_guard(connection,json.loads(row["identity"]),limits,{},current_bid=row["business_id"],
                require_headroom=True)
            attempt="attempt_"+secrets.token_hex(24)
            sequence=self._event(connection,bid,attempt,"EXPLICIT_RESUME_AUTHORIZED",
                dict(previous_attempt_id=previous_attempt_id,authorization_id=authorization_id,
                     reconciliation_sha256=reconciliation_sha256))
            connection.execute("UPDATE requests SET state='CLAIMED',attempt_id=?,sequence=?,receipt=NULL,updated_at=? WHERE business_id=?",
                               (attempt,sequence,stamp(),bid))
            return self._receipt(connection.execute("SELECT * FROM requests WHERE business_id=?",(bid,)).fetchone())

    def begin_service_restart(self, *, previous_attempt_id, previous_receipt_sha256,
                              maintenance_sequence, authorization_id, epoch_id):
        """Explicit successor of a successful fixed service-maintenance attempt.

        A failed/unknown bootstrap is never a restart parent. Its only possible
        recovery remains explicit reconciliation, with known cumulative budget.
        The previous complete receipt is retained in the append-only event.
        """
        _text(authorization_id,"AUTHORITY_SERVICE_RESTART_AUTHORIZATION_REQUIRED",512)
        business=canonical_business(dict(operation="MAINTENANCE",subject="ACTIVE_SERVICE_BOOTSTRAP",
            parameters=dict(service_role="LOCAL_RESEARCH_HTTP")))
        bid=business_id(self.workspace_id,business)
        with self._connect() as connection:
            epoch=connection.execute("SELECT epoch_id FROM epochs WHERE state='ACTIVE'").fetchone()
            need(epoch is not None and epoch["epoch_id"]==epoch_id,"AUTHORITY_ACTIVE_EPOCH_REQUIRED")
            row=connection.execute("SELECT * FROM requests WHERE business_id=?",(bid,)).fetchone()
            need(row is not None and row["state"]=="RECORDED" and row["attempt_id"]==previous_attempt_id
                 and row["budget_known"] and row["receipt"] is not None,"AUTHORITY_SERVICE_RESTART_PARENT_NOT_SUCCESSFUL")
            previous=json.loads(row["receipt"])
            need(digest(previous)==previous_receipt_sha256 and type(maintenance_sequence) is int
                 and maintenance_sequence==previous.get("maintenance_sequence",0)+1,
                 "AUTHORITY_SERVICE_RESTART_CHAIN_CHANGED")
            attempt="attempt_"+secrets.token_hex(24)
            sequence=self._event(connection,bid,attempt,"SERVICE_RESTART_AUTHORIZED",dict(
                previous_attempt_id=previous_attempt_id,previous_receipt=previous,
                previous_receipt_sha256=previous_receipt_sha256,maintenance_sequence=maintenance_sequence,
                authorization_id=authorization_id,epoch_id=epoch_id))
            connection.execute("UPDATE requests SET state='CLAIMED',attempt_id=?,sequence=?,receipt=NULL,updated_at=? WHERE business_id=?",
                (attempt,sequence,stamp(),bid))
            return self._receipt(connection.execute("SELECT * FROM requests WHERE business_id=?",(bid,)).fetchone())

    def finish_service_bootstrap(self, claim, *, receipt, epoch_id):
        """Atomically record a successful host and rotate its private proof key."""
        fixed=canonical_business(dict(operation="MAINTENANCE",subject="ACTIVE_SERVICE_BOOTSTRAP",
            parameters=dict(service_role="LOCAL_RESEARCH_HTTP")))
        need(isinstance(receipt,dict) and isinstance(receipt.get("host_id"),str) and
             type(receipt.get("maintenance_sequence")) is int and receipt["maintenance_sequence"]>0
             and len(canonical(receipt))<=16384,"AUTHORITY_SERVICE_RECEIPT_INVALID")
        with self._connect() as connection:
            row=self._claimed(connection,claim)
            epoch=connection.execute("SELECT epoch_id FROM epochs WHERE state='ACTIVE'").fetchone()
            need(epoch is not None and epoch["epoch_id"]==epoch_id,"AUTHORITY_ACTIVE_EPOCH_REQUIRED")
            need(json.loads(row["identity"])==fixed and row["budget_known"],"AUTHORITY_SERVICE_BOOTSTRAP_CLAIM_REQUIRED")
            sequence=self._event(connection,row["business_id"],row["attempt_id"],"RECORDED",
                {"receipt_sha256":digest(receipt),"private_attestation_key_rotated":True})
            connection.execute("UPDATE requests SET state='RECORDED',receipt=?,sequence=?,updated_at=? WHERE business_id=?",
                (canonical(receipt).decode(),sequence,stamp(),row["business_id"]))
            connection.execute("UPDATE capabilities SET revoked=1 WHERE business_id=?",(row["business_id"],))
            connection.execute("UPDATE effects SET state='UNKNOWN' WHERE business_id=? AND state='STARTED'",(row["business_id"],))
            connection.execute("INSERT INTO service_hosts VALUES (?,?,?,?,?,?) ON CONFLICT(role) DO UPDATE SET "
                "host_id=excluded.host_id,epoch_id=excluded.epoch_id,business_id=excluded.business_id,"
                "attempt_id=excluded.attempt_id,attestation_secret=excluded.attestation_secret",
                ("LOCAL_RESEARCH_HTTP",receipt["host_id"],epoch_id,row["business_id"],row["attempt_id"],secrets.token_hex(32)))
            return self._receipt(connection.execute("SELECT * FROM requests WHERE business_id=?",(row["business_id"],)).fetchone())

    def service_attestation_secret(self, *, host_id, epoch_id):
        """Private local verifier material; never include this result in reports."""
        with self._connect(readonly=True) as connection:
            host=connection.execute("SELECT h.*,r.state,r.receipt FROM service_hosts h JOIN requests r USING(business_id,attempt_id) "
                "WHERE role='LOCAL_RESEARCH_HTTP'").fetchone()
            epoch=connection.execute("SELECT epoch_id FROM epochs WHERE state='ACTIVE'").fetchone()
            need(host is not None and host["host_id"]==host_id and host["epoch_id"]==epoch_id and epoch is not None
                 and epoch["epoch_id"]==epoch_id and host["state"]=="RECORDED"
                 and json.loads(host["receipt"])["host_id"]==host_id,"AUTHORITY_CURRENT_SERVICE_PROOF_KEY_REQUIRED")
            return host["attestation_secret"]

    def derive_capability(self, claim, stages):
        need(isinstance(stages,(list,tuple)) and stages and len(stages)<=32,"AUTHORITY_STAGES_REQUIRED")
        stages=sorted(set(_text(v,"AUTHORITY_STAGE_INVALID",64) for v in stages))
        with self._connect() as connection:
            row=self._claimed(connection,claim)
            operation=json.loads(row["identity"])["operation"]
            need(set(stages)<=ALLOWED_STAGES[operation],"AUTHORITY_OPERATION_STAGE_NOT_ALLOWED")
            if set(stages)&{"network","provider","capture"}:
                self._require_source_scope_inventory(connection)
            token=secrets.token_hex(32)
            connection.execute("INSERT INTO capabilities VALUES (?,?,?,?,0)",
                (hashlib.sha256(token.encode()).hexdigest(),row["business_id"],row["attempt_id"],canonical(stages).decode()))
            return token

    def verify_capability(self, token, *, stage, bid=None):
        need(isinstance(token,str) and len(token)==64,"AUTHORITY_WORKER_CAPABILITY_REQUIRED")
        with self._connect(readonly=True) as connection:
            row=connection.execute("SELECT c.*,r.state,r.identity FROM capabilities c JOIN requests r USING(business_id,attempt_id) WHERE token_hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            need(row is not None and not row["revoked"] and row["state"] in ACTIVE and
                 stage in json.loads(row["stages"]) and (bid is None or bid==row["business_id"]),
                 "AUTHORITY_WORKER_CAPABILITY_REJECTED")
            need(stage in ALLOWED_STAGES[json.loads(row["identity"])["operation"]],
                 "AUTHORITY_OPERATION_STAGE_NOT_ALLOWED")
            if stage in {"network","provider","capture"}:
                self._require_source_scope_inventory(connection)
            request=connection.execute("SELECT * FROM requests WHERE business_id=?",(row["business_id"],)).fetchone()
            return self._receipt(request)

    def consume_once(self, token, *, stage, effect_kind, stable_effect_id, charge=None, binding=None, source_scope=None):
        """Reserve one actual effect and its budget atomically, before executing.

        The ledger key survives attempts. A completed or uncertain effect may
        not be repeated by minting another worker token or resuming the request.
        Each provider request uses its genuine endpoint/parameters/ordinal ID.
        """
        need(effect_kind in {"WORKER_START","PROVIDER_REQUEST","JOB_CREATE","PUBLISH","REGISTRATION"},
             "AUTHORITY_EFFECT_KIND_INVALID")
        _text(stable_effect_id,"AUTHORITY_EFFECT_ID_REQUIRED",512)
        need(isinstance(token,str) and len(token)==64,"AUTHORITY_WORKER_CAPABILITY_REQUIRED")
        need(binding is None or isinstance(binding,dict) and len(canonical(binding))<=16384,
             "AUTHORITY_EFFECT_BINDING_INVALID")
        need(effect_kind=="PROVIDER_REQUEST" or source_scope is None,"AUTHORITY_SOURCE_SCOPE_ONLY_FOR_PROVIDER")
        scope=canonical_source_scope(source_scope) if effect_kind=="PROVIDER_REQUEST" else None
        delta=_numbers(charge or {})
        with self._connect() as connection:
            cap=connection.execute("SELECT * FROM capabilities WHERE token_hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            need(cap is not None and not cap["revoked"] and stage in json.loads(cap["stages"]),
                 "AUTHORITY_WORKER_CAPABILITY_REJECTED")
            row=connection.execute("SELECT * FROM requests WHERE business_id=?",(cap["business_id"],)).fetchone()
            need(row is not None and row["attempt_id"]==cap["attempt_id"] and row["state"] in ACTIVE,
                 "AUTHORITY_CLAIM_NOT_ACTIVE")
            operation=json.loads(row["identity"])["operation"]
            need(stage in ALLOWED_STAGES[operation],"AUTHORITY_OPERATION_STAGE_NOT_ALLOWED")
            need(effect_kind!="REGISTRATION","AUTHORITY_REGISTRATION_NOT_A_BUSINESS_EFFECT")
            if effect_kind=="PROVIDER_REQUEST":
                need(operation in {"PREMARKET","CAPTURE"},"AUTHORITY_OPERATION_PROVIDER_EFFECT_NOT_ALLOWED")
                self._require_source_scope_inventory(connection)
                need(stage in {"network","provider","capture"},"AUTHORITY_PROVIDER_EFFECT_STAGE_REQUIRED")
            if effect_kind in {"WORKER_START","JOB_CREATE"}:
                need("worker" in ALLOWED_STAGES[operation],"AUTHORITY_OPERATION_WORKER_EFFECT_NOT_ALLOWED")
            # A top-level worker is unique for its business operation. Calling
            # it a different worker ID must not create a second execution.
            # A new attempt can only exist after explicit authorize_resume;
            # preserve that separate permission without erasing the old effect.
            if effect_kind=="WORKER_START":
                stable_effect_id="BUSINESS_WORKER:"+row["attempt_id"]
            if effect_kind=="PROVIDER_REQUEST":
                exists=connection.execute("SELECT state FROM effects WHERE effect_kind=? AND stable_effect_id=?",
                    (effect_kind,stable_effect_id)).fetchone()
            else:
                exists=connection.execute("SELECT state FROM effects WHERE business_id=? AND effect_kind=? AND stable_effect_id=?",
                    (row["business_id"],effect_kind,stable_effect_id)).fetchone()
            need(exists is None,"AUTHORITY_EFFECT_ALREADY_RESERVED_NO_RETRY")
            if scope is not None:
                candidates=connection.execute("SELECT s.*,e.state AS effect_state,r.state AS business_state FROM source_scopes s "
                    "JOIN effects e USING(business_id,effect_kind,stable_effect_id) JOIN requests r USING(business_id) "
                    "WHERE s.provider=? AND s.endpoint=? AND s.window_start<=? AND s.window_end>=?",
                    (scope["provider"],scope["endpoint"],scope["window_end"],scope["window_start"]))
                codes=set(scope["codes"])
                for prior in candidates:
                    old_codes=set(json.loads(prior["codes_json"]))
                    if codes and old_codes and codes.isdisjoint(old_codes):
                        continue
                    same_request=(prior["business_id"]==row["business_id"] and prior["attempt_id"]==row["attempt_id"]
                                  and prior["request_semantic_id"]==scope["request_semantic_id"])
                    if same_request:
                        need(prior["window_start"]==scope["window_start"] and prior["window_end"]==scope["window_end"]
                             and old_codes==codes,"AUTHORITY_SOURCE_SEMANTIC_COVERAGE_CHANGED")
                        continue
                    need(prior["business_state"]=="RECORDED" and prior["effect_state"]=="COMPLETED",
                         "AUTHORITY_SOURCE_SCOPE_OVERLAPS_FAILED_OR_UNKNOWN_NO_RETRY")
            limits,spent=json.loads(row["limits_json"]),json.loads(row["spent_json"])
            need(row["budget_known"] and set(delta)<=set(limits),"AUTHORITY_EFFECT_BUDGET_NOT_DECLARED")
            updated={**spent,**{k:spent.get(k,0)+v for k,v in delta.items()}}
            need(all(updated[k]<=limits[k] for k in updated),"AUTHORITY_CUMULATIVE_BUDGET_EXCEEDED")
            self._outcome_family_guard(connection,json.loads(row["identity"]),limits,delta,current_bid=row["business_id"],
                require_headroom=effect_kind in {"WORKER_START","JOB_CREATE"})
            sequence=self._event(connection,row["business_id"],row["attempt_id"],"EFFECT_RESERVED",
                dict(effect_kind=effect_kind,stable_effect_id=stable_effect_id,charge=delta,binding=binding,source_scope=scope))
            connection.execute("INSERT INTO effects VALUES (?,?,?,?,?,?)",
                (row["business_id"],effect_kind,stable_effect_id,row["attempt_id"],"STARTED",
                 canonical({"reservation_binding":binding}).decode() if binding is not None else None))
            if scope is not None:
                connection.execute("INSERT INTO source_scopes VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (row["business_id"],effect_kind,stable_effect_id,row["attempt_id"],scope["provider"],scope["endpoint"],
                     scope["window_start"],scope["window_end"],canonical(scope["codes"]).decode(),scope["request_semantic_id"]))
            connection.execute("UPDATE requests SET spent_json=?,sequence=?,updated_at=? WHERE business_id=?",
                (canonical(updated).decode(),sequence,stamp(),row["business_id"]))
            return dict(workspace_id=self.workspace_id,business_id=row["business_id"],attempt_id=row["attempt_id"],
                        effect_kind=effect_kind,stable_effect_id=stable_effect_id,state="STARTED",sequence=sequence)

    def verify_effect(self, token, *, stage, effect_kind, stable_effect_id, expected_binding=None):
        """Read an existing live reservation; this never grants another effect."""
        need(isinstance(token,str) and len(token)==64,"AUTHORITY_WORKER_CAPABILITY_REQUIRED")
        with self._connect(readonly=True) as connection:
            row=connection.execute("SELECT c.*,r.state,r.identity FROM capabilities c JOIN requests r USING(business_id,attempt_id) WHERE token_hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            need(row is not None and not row["revoked"] and row["state"] in ACTIVE and stage in json.loads(row["stages"])
                 and stage in ALLOWED_STAGES[json.loads(row["identity"])["operation"]],"AUTHORITY_WORKER_CAPABILITY_REJECTED")
            if stage in {"network","provider","capture"} or effect_kind=="PROVIDER_REQUEST":
                self._require_source_scope_inventory(connection)
            effect=connection.execute("SELECT * FROM effects WHERE business_id=? AND attempt_id=? AND effect_kind=? AND stable_effect_id=?",
                (row["business_id"],row["attempt_id"],effect_kind,stable_effect_id)).fetchone()
            need(effect is not None and effect["state"]=="STARTED","AUTHORITY_LIVE_EFFECT_RESERVATION_REQUIRED")
            binding=json.loads(effect["receipt"])["reservation_binding"] if effect["receipt"] is not None else None
            need(expected_binding is None or binding==expected_binding,"AUTHORITY_EFFECT_BINDING_CHANGED")
            return dict(workspace_id=self.workspace_id,business_id=row["business_id"],attempt_id=row["attempt_id"],
                effect_kind=effect_kind,stable_effect_id=stable_effect_id,state=effect["state"],binding=binding)

    def complete_effect(self, token, effect, *, receipt):
        need(isinstance(receipt,dict) and len(canonical(receipt))<=16384,"AUTHORITY_EFFECT_RECEIPT_INVALID")
        with self._connect() as connection:
            cap=connection.execute("SELECT * FROM capabilities WHERE token_hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            need(cap is not None and not cap["revoked"] and cap["business_id"]==effect.get("business_id")
                 and cap["attempt_id"]==effect.get("attempt_id"),"AUTHORITY_WORKER_CAPABILITY_REJECTED")
            count=connection.execute("UPDATE effects SET state='COMPLETED',receipt=? WHERE business_id=? AND effect_kind=? "
                "AND stable_effect_id=? AND attempt_id=? AND state='STARTED'",
                (canonical(receipt).decode(),effect["business_id"],effect["effect_kind"],effect["stable_effect_id"],effect["attempt_id"])).rowcount
            need(count==1,"AUTHORITY_EFFECT_NOT_OWNED_OR_COMPLETED")
            self._event(connection,effect["business_id"],effect["attempt_id"],"EFFECT_COMPLETED",
                        dict(effect_kind=effect["effect_kind"],stable_effect_id=effect["stable_effect_id"],receipt_sha256=digest(receipt)))

    def complete_source_request(self, token, *, stage, source_scope, receipt):
        """Close only this fully returned request's own transport reservations.

        This is transport completion, not a market-source/PIT certification.
        Partial results and exceptions retain their unfinished reservations.
        """
        scope=canonical_source_scope(source_scope)
        need(isinstance(token,str) and len(token)==64,"AUTHORITY_WORKER_CAPABILITY_REQUIRED")
        need(isinstance(receipt,dict) and receipt.get("status")=="COMPLETE"
             and receipt.get("pagination_complete") is True and receipt.get("source_authority_restored",False) is False
             and len(canonical(receipt))<=16384,
             "AUTHORITY_SOURCE_COMPLETION_NOT_COMPLETE")
        with self._connect() as connection:
            cap=connection.execute("SELECT c.*,r.state,r.identity FROM capabilities c JOIN requests r USING(business_id,attempt_id) WHERE token_hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            need(cap is not None and not cap["revoked"] and cap["state"] in ACTIVE and stage in json.loads(cap["stages"])
                 and stage in {"network","provider","capture"}
                 and json.loads(cap["identity"])["operation"] in {"PREMARKET","CAPTURE"},"AUTHORITY_WORKER_CAPABILITY_REJECTED")
            self._require_source_scope_inventory(connection)
            rows=connection.execute("SELECT s.*,e.state AS effect_state FROM source_scopes s JOIN effects e "
                "USING(business_id,effect_kind,stable_effect_id) WHERE s.business_id=? AND s.attempt_id=? AND s.request_semantic_id=?",
                (cap["business_id"],cap["attempt_id"],scope["request_semantic_id"])).fetchall()
            completed=0
            for row in rows:
                need(row["provider"]==scope["provider"] and row["endpoint"]==scope["endpoint"]
                     and row["window_start"]==scope["window_start"] and row["window_end"]==scope["window_end"]
                     and json.loads(row["codes_json"])==scope["codes"],"AUTHORITY_SOURCE_SEMANTIC_COVERAGE_CHANGED")
                need(row["effect_state"] in {"STARTED","COMPLETED"},"AUTHORITY_SOURCE_UNKNOWN_EFFECT_REQUIRES_RECONCILIATION")
                if row["effect_state"]=="STARTED":
                    connection.execute("UPDATE effects SET state='COMPLETED',receipt=? WHERE business_id=? AND effect_kind=? AND stable_effect_id=?",
                        (canonical(dict(completion="SOURCE_REQUEST_RETURNED_COMPLETE",evidence=receipt,
                                        source_authority_restored=False)).decode(),row["business_id"],row["effect_kind"],row["stable_effect_id"]))
                    completed+=1
            self._event(connection,cap["business_id"],cap["attempt_id"],"SOURCE_REQUEST_COMPLETED",
                dict(request_semantic_id=scope["request_semantic_id"],completed_effects=completed,receipt_sha256=digest(receipt)))
            return dict(completed_effects=completed,source_authority_restored=False)

    def inspect_provider_effect(self, stable_effect_id):
        """Only a locator for explicit source revalidation, never source proof."""
        _text(stable_effect_id,"AUTHORITY_EFFECT_ID_REQUIRED",512)
        with self._connect(readonly=True) as connection:
            row=connection.execute("SELECT * FROM effects WHERE effect_kind='PROVIDER_REQUEST' AND stable_effect_id=?",
                                   (stable_effect_id,)).fetchone()
            if row is None:
                return None
            return dict(business_id=row["business_id"],attempt_id=row["attempt_id"],state=row["state"],
                        stable_effect_id=row["stable_effect_id"],
                        receipt=json.loads(row["receipt"]) if row["receipt"] else None,
                        source_authority_restored=False,revalidation_required=True)


def authority_from_environment():
    database=os.environ.get("STOCKFISH_AUTHORITY_DATABASE")
    workspace=os.environ.get("STOCKFISH_AUTHORITY_WORKSPACE")
    need(bool(database and workspace),"AUTHORITY_EXPLICIT_BOUND_ENVIRONMENT_REQUIRED")
    return Authority(database,workspace)


def require_worker_capability(stage):
    authority=authority_from_environment()
    return authority.verify_capability(os.environ.get("STOCKFISH_WORKER_CAPABILITY"),stage=stage,
                                      bid=os.environ.get("STOCKFISH_BUSINESS_ID"))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    commands=parser.add_subparsers(dest="command",required=True)
    for name in ("initialize","read"):
        sub=commands.add_parser(name,allow_abbrev=False)
        sub.add_argument("--database",type=Path,required=True)
        sub.add_argument("--workspace-id",required=True)
        if name=="initialize":
            sub.add_argument("--onboarding",type=Path,required=True)
            sub.add_argument("--onboarding-sha256",required=True)
            sub.add_argument("--authorize-control-initialization",action="store_true")
        else:
            sub.add_argument("--business-id")
    args=parser.parse_args(argv)
    if args.command=="initialize":
        need(args.authorize_control_initialization,"AUTHORITY_EXPLICIT_INITIALIZATION_SWITCH_REQUIRED")
        path,_=_safe_file(args.onboarding)
        need(path.stat().st_size<=1024**2,"AUTHORITY_ONBOARDING_BYTE_LIMIT")
        raw=path.read_bytes()
        need(hashlib.sha256(raw).hexdigest()==args.onboarding_sha256,"AUTHORITY_ONBOARDING_SHA_MISMATCH")
        onboarding=json.loads(raw)
        owner=Authority.initialize(args.database,args.workspace_id,onboarding=onboarding)
        result=dict(status="CONTROL_INITIALIZED_NOT_ACTIVATED",workspace_id=owner.workspace_id,
                    active_epoch=None,source_authority_restored=False)
    else:
        owner=Authority(args.database,args.workspace_id)
        result=owner.inspect(args.business_id) if args.business_id else owner.active_epoch()
        if result and not args.business_id:
            result={k:v for k,v in result.items() if k!="manifest"}
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
