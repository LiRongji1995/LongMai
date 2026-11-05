"""Explicit API host bootstrap, bound to a V2 execution epoch.

The old API profile remains a locator; it does not acquire source authority.
Planning is read-only. Bootstrap is a separate, explicit maintenance operation,
whose capability is revoked before this function returns a WSGI application.
No import, plan, or bootstrap function starts a listener or retries an old job.
"""
from __future__ import annotations

from copy import deepcopy
import argparse
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import sqlite3
import secrets
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import business_authority_v1 as a
from scripts import codex_workspace as w
from scripts.codex_runtime_recovery_v2 import reference

VERSION = "ACTIVE_SERVICE_HOST_V1"
need = a.need


def _gateway():
    from scripts import active_business_gateway_v1 as gateway
    return gateway


def _business(_descriptor=None):
    return a.canonical_business(dict(operation="MAINTENANCE", subject="ACTIVE_SERVICE_BOOTSTRAP",
        parameters={"service_role": "LOCAL_RESEARCH_HTTP"}))


def _host_binding(execution, api_profile, authorization_id):
    need(isinstance(authorization_id, str) and authorization_id.strip(), "HOST_EXPLICIT_AUTHORIZATION_ID_REQUIRED")
    execution.check_binding()
    profile = w.validate_profile(api_profile)
    need(profile["backend"] == "api", "HOST_API_LOCATOR_REQUIRED")
    root = Path(execution.profile["state_root"])
    need(all(Path(profile[k]).is_relative_to(root) for k in ("state_root", "report_root", "db_path")),
         "HOST_DESTINATIONS_OUTSIDE_EXECUTION_STATE")
    need("env_file" not in profile, "HOST_ENV_FILE_NOT_AN_EXECUTION_IDENTITY")
    manifest = reference(Path(execution.profile["source_root"]) / "ACTIVE_RUNTIME_MANIFEST.json")
    epoch = execution.authority.active_epoch()
    need(epoch is not None and epoch["epoch_id"] == execution.epoch_id, "HOST_EXECUTION_EPOCH_NOT_ACTIVE")
    plan = json.loads(epoch["manifest"])
    need(plan.get("active_source_manifest") == manifest, "HOST_SOURCE_NOT_ATTESTED_BY_EPOCH")
    return dict(schema_version=VERSION, epoch_id=execution.epoch_id,
        authority_database=str(execution.authority.path), workspace_id=execution.authority.workspace_id,
        authority_physical=list(execution.authority.physical), execution_registration=deepcopy(execution.registry_ref),
        execution_profile=deepcopy(execution.profile), source_manifest=manifest,
        profile=profile, binding=w._binding(profile), authorization_id=authorization_id,
        action="BOOTSTRAP_ONLY_NO_LISTENER_NO_COLLECTION_NO_RETRY")


def plan_host(execution, api_profile, *, authorization_id):
    """Bind profiles and the exact successful maintenance parent, without writes."""
    descriptor = _host_binding(execution, api_profile, authorization_id)
    previous = execution.authority.inspect(a.business_id(execution.authority.workspace_id, _business()))
    need(previous is None or previous["state"] == "RECORDED", "HOST_PREVIOUS_BOOTSTRAP_RECONCILE_NO_RETRY")
    descriptor["bootstrap_parent"] = None if previous is None else dict(
        attempt_id=previous["attempt_id"], receipt_sha256=a.digest(previous["receipt"]))
    descriptor["maintenance_sequence"] = 1 if previous is None else previous["receipt"]["maintenance_sequence"] + 1
    descriptor["host_id"] = a.digest(descriptor)
    return descriptor


class ApiRuntime(w.Runtime):
    """Original API locator plus independently checked V2 execution binding."""
    def __init__(self, execution, descriptor, *, descriptor_reference=None):
        expected = _host_binding(execution, descriptor["profile"], descriptor["authorization_id"])
        parent = descriptor.get("bootstrap_parent")
        need(parent is None or isinstance(parent, dict) and set(parent) == {"attempt_id", "receipt_sha256"},
             "HOST_BOOTSTRAP_PARENT_INVALID")
        need(type(descriptor.get("maintenance_sequence")) is int and descriptor["maintenance_sequence"] > 0 and
             (parent is not None or descriptor["maintenance_sequence"] == 1), "HOST_MAINTENANCE_SEQUENCE_INVALID")
        expected.update(bootstrap_parent=parent, maintenance_sequence=descriptor["maintenance_sequence"])
        expected["host_id"] = a.digest(expected)
        need(descriptor == expected,
             "HOST_DESCRIPTOR_NOT_CANONICAL_OR_BOUND")
        self.execution = execution
        self.descriptor = deepcopy(descriptor)
        self.descriptor_reference = descriptor_reference
        self.authority, self.epoch_id = execution.authority, execution.epoch_id
        self.service_started = False
        super().__init__(execution.registry_root, descriptor["profile"], descriptor["binding"])
        self.check_binding()

    def check_binding(self):
        self.execution.check_binding()
        super().check_binding()
        d = self.descriptor
        need(d.get("schema_version") == VERSION and d.get("host_id") == a.digest({k:v for k,v in d.items() if k != "host_id"}),
             "HOST_DESCRIPTOR_CHANGED")
        need(d["epoch_id"] == self.epoch_id and d["workspace_id"] == self.authority.workspace_id and
             d["authority_database"] == str(self.authority.path) and d["authority_physical"] == list(self.authority.physical),
             "HOST_AUTHORITY_BINDING_CHANGED")
        need(d["execution_registration"] == self.execution.registry_ref and
             d["execution_profile"] == self.execution.profile, "HOST_EXECUTION_PROFILE_CHANGED")
        need(reference(d["source_manifest"]["path"]) == d["source_manifest"], "HOST_SOURCE_MANIFEST_CHANGED")
        if self.descriptor_reference is not None:
            need(reference(self.descriptor_reference["path"]) == self.descriptor_reference, "HOST_SAVED_DESCRIPTOR_CHANGED")
        self._check_current_generation()

    def _check_current_generation(self):
        if self.service_started:
            current = self.authority.inspect(a.business_id(self.authority.workspace_id, _business()))
            need(current is not None and current["state"] == "RECORDED" and current["receipt"] and
                 current["receipt"].get("host_id") == self.descriptor["host_id"], "HOST_SERVICE_GENERATION_NOT_CURRENT")

    def check_database_binding(self):
        # Full ancestor/source/environment verification is at every HTTP
        # admission and asynchronous job finalization. Each connection still
        # checks its actual writable physical resource and active generation.
        super().check_binding()
        epoch = self.authority.active_epoch()
        need(epoch is not None and epoch["epoch_id"] == self.epoch_id, "HOST_EXECUTION_EPOCH_NOT_ACTIVE")
        self._check_current_generation()


def load_api_runtime(path, *, allow_test_fixture=False, require_bootstrapped=True):
    from scripts.codex_runtime_recovery_v2 import _document, load_runtime
    descriptor, ref = _document(path)
    registration = Path(descriptor["execution_registration"]["path"])
    execution = load_runtime(descriptor["execution_profile"]["name"], registration.parent.parent,
                             allow_test_fixture=allow_test_fixture)
    runtime = ApiRuntime(execution, descriptor, descriptor_reference=ref)
    if require_bootstrapped:
        receipt = runtime.authority.inspect(a.business_id(runtime.authority.workspace_id, _business(descriptor)))
        need(receipt is not None and receipt["state"] == "RECORDED" and
             receipt["receipt"].get("host_id") == descriptor["host_id"], "HOST_BOOTSTRAP_NOT_RECORDED")
        runtime.service_started = True
    return runtime


def _host_store_class():
    from stockfish_core.storage import SQLiteStore

    class HostBoundStore(SQLiteStore):
        def __init__(self, runtime):
            self.host_runtime = runtime
            super().__init__(runtime.profile["db_path"])

        def _connect(self):
            gateway = _gateway()
            with gateway._control():
                self.host_runtime.check_database_binding()
                gateway._bound_authority(self.host_runtime.authority)
                need(self.path == Path(self.host_runtime.profile["db_path"]), "HOST_DATABASE_CHANGED")
            # Explicit business capabilities keep the original writable path.
            # A revoked/foreign capability is never silently downgraded to read.
            if gateway._CURRENT.get() is not None or os.environ.get("STOCKFISH_WORKER_CAPABILITY"):
                return super()._connect()
            # The only control exemption is a fixed, bound read-only connection;
            # no callback or application SQL executes in this exemption.
            with gateway._control():
                connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True,
                    timeout=self.timeout, isolation_level=None)
                try:
                    connection.row_factory = sqlite3.Row
                    connection.execute("PRAGMA query_only=ON")
                except BaseException:
                    connection.close()
                    raise
            return connection

    return HostBoundStore


def bootstrap_app(runtime, *, authorization_id, authorize_bootstrap=False, allow_test_fixture=False):
    """Construct the actual service without listening; maintenance cannot start workers."""
    need(authorize_bootstrap is True and authorization_id == runtime.descriptor["authorization_id"],
         "HOST_BOOTSTRAP_EXPLICIT_AUTHORIZATION_REQUIRED")
    gateway = _gateway()
    need(gateway._CURRENT.get() is None and not os.environ.get("STOCKFISH_WORKER_CAPABILITY") and
         not os.environ.get("STOCKFISH_BUSINESS_ID"), "HOST_INHERITED_BUSINESS_CAPABILITY_FORBIDDEN")
    core, jobs = None, None
    with gateway._control():
        runtime.check_binding()
        gateway._bound_authority(runtime.authority)
        from scripts.active_runtime_v1 import verify
        verify(runtime.execution.profile["source_root"])
        need(runtime.preflight()["allowed"], "HOST_CAPACITY_PREFLIGHT_BLOCKED")
        parent = runtime.descriptor["bootstrap_parent"]
        if parent is None:
            claim = runtime.authority.claim(_business(), epoch_id=runtime.epoch_id,
                request_metadata={"request_key":authorization_id, "entry_version":VERSION}, limits={})
            need(claim["status"] != "REUSED", "HOST_BOOTSTRAP_ALREADY_EXECUTED_EXPLICIT_NEW_PLAN_REQUIRED")
        else:
            claim = runtime.authority.begin_service_restart(previous_attempt_id=parent["attempt_id"],
                previous_receipt_sha256=parent["receipt_sha256"], maintenance_sequence=runtime.descriptor["maintenance_sequence"],
                authorization_id=authorization_id, epoch_id=runtime.epoch_id)
    token = None
    try:
        with gateway._control():
            runtime.authority.start(claim)
            capability = runtime.authority.derive_capability(claim, ["storage", "metrics"])
            effect = runtime.authority.consume_once(capability, stage="storage", effect_kind="PUBLISH",
                stable_effect_id="HOST_BOOTSTRAP:" + claim["attempt_id"])
        token = gateway._CURRENT.set(dict(owner=runtime.authority, claim=claim, capability=capability))
        from stockfish_core.service import ApplicationService
        from stockfish_core.api import create_app
        from stockfish_core.jobs import JobService
        store = _host_store_class()(runtime)
        jobs = JobService(store, max_workers=1, max_pending=20)
        core = ApplicationService(root=runtime.profile["state_root"], store=store,
                                  jobs=jobs, allow_test_fixture=allow_test_fixture)
        app = create_app(core)
        install_attestation_endpoint(app, runtime)
        # Merely mark interrupted jobs. Original JobService construction does
        # that recovery; it never schedules their handlers or clears authority.
        need(not core.jobs._futures and core.candidate_scheduler is None, "HOST_BOOTSTRAP_STARTED_WORK")
        runtime.check_binding()
        with gateway._control():
            runtime.authority.complete_effect(capability, effect, receipt={"host_id":runtime.descriptor["host_id"]})
            runtime.authority.finish_service_bootstrap(claim, epoch_id=runtime.epoch_id, receipt=dict(host_id=runtime.descriptor["host_id"],
                maintenance_sequence=runtime.descriptor["maintenance_sequence"],
                service_initialized=True, listener_started=False, old_job_retry=False))
        app.extensions["stockfish_active_host"] = dict(runtime=runtime, bootstrap_claim=claim,
            bootstrap_capability_revoked=True)
        runtime.service_started = True
        return app
    except BaseException:
        with gateway._control():
            saved = runtime.authority.inspect(claim["business_id"])
            if saved and saved["attempt_id"] == claim["attempt_id"] and saved["state"] in a.ACTIVE:
                runtime.authority.finish(claim, success=False, budget_known=False)
        if jobs is not None:
            jobs.shutdown(wait=True, cancel_pending=False)
        raise
    finally:
        if token is not None:
            gateway._CURRENT.reset(token)


def close_app(app):
    """Join already-owned workers; do not mint a maintenance/cancellation token."""
    core = app.extensions["stockfish_core"]
    need(core.candidate_scheduler is None, "HOST_UNEXPECTED_SCHEDULER")
    core.jobs.shutdown(wait=True, cancel_pending=False)


def _attestation_fields(runtime, nonce):
    need(isinstance(nonce,str) and len(nonce)==64 and all(c in "0123456789abcdef" for c in nonce),
         "HOST_ATTESTATION_NONCE_INVALID")
    return dict(schema="ACTIVE_SERVICE_ATTESTATION_V1",nonce=nonce,host_id=runtime.descriptor["host_id"],
        epoch_id=runtime.epoch_id,workspace_id=runtime.authority.workspace_id,
        authority_physical=list(runtime.authority.physical),source_manifest_sha256=runtime.descriptor["source_manifest"]["sha256"])


def install_attestation_endpoint(app, runtime):
    from flask import request, jsonify
    @app.before_request
    def host_read_admission():
        # Write methods already receive this full check in bind_http, before
        # their capability is installed. Reads cannot skip ancestor checks.
        if request.method in {"GET","HEAD","OPTIONS"}:
            with _gateway()._control():
                runtime.check_binding()
                _gateway()._bound_authority(runtime.authority)
    @app.get("/api/active-runtime/attestation")
    def active_runtime_attestation():
        need(set(request.args)=={"nonce"} and len(request.args.getlist("nonce"))==1,"HOST_ATTESTATION_NONCE_REQUIRED")
        fields=_attestation_fields(runtime,request.args["nonce"])
        with _gateway()._control():
            runtime.check_binding()
            _gateway()._bound_authority(runtime.authority)
            secret=runtime.authority.service_attestation_secret(host_id=runtime.descriptor["host_id"],epoch_id=runtime.epoch_id)
        signature=hmac.new(bytes.fromhex(secret),a.canonical(fields),hashlib.sha256).hexdigest()
        return jsonify({**fields,"signature":signature})


def attested_post(client, path, payload):
    """Prove the current host over the same live socket before sending a POST.

    A server that closes the attestation connection cannot receive a fallback
    POST. No request, redirect, reconnection, or response failure is retried.
    """
    from scripts.codex_research import ResearchError, RESPONSE_MAX_BYTES
    gateway=_gateway()
    runtime=getattr(client,"runtime",None)
    need(isinstance(runtime,ApiRuntime) and runtime.service_started,"HOST_REGISTERED_ACTIVE_API_REQUIRED")
    need(isinstance(path,str) and path.startswith("/api/") and ".." not in path and "\\" not in path,
         "HOST_INVALID_API_PATH")
    need(isinstance(payload,dict),"HOST_POST_OBJECT_REQUIRED")
    need(type(client.timeout) in (int,float) and 0<client.timeout<=60,"HOST_HTTP_TIMEOUT_INVALID")
    need(client.base_url==runtime.profile["base_url"],"HOST_POST_LOCATOR_CHANGED")
    parts=urlsplit(client.base_url)
    need(parts.scheme=="http" and parts.hostname in {"127.0.0.1","::1"} and not parts.username and not parts.password
         and parts.path in {"","/"} and not parts.query and not parts.fragment,"HOST_LOOPBACK_URL_REQUIRED")
    body=json.dumps(payload,allow_nan=False).encode("utf-8")
    need(len(body)<=2*1024**2,"HOST_POST_SIZE_LIMIT")
    current=gateway.current("worker")
    need(gateway.request_business({"path":path,"payload":payload})==current["claim"]["business"],
         "HOST_POST_BUSINESS_MISMATCH")
    with gateway._control():
        runtime.check_binding()
        need(runtime.authority.path==current["owner"].path and runtime.authority.workspace_id==current["owner"].workspace_id,
             "HOST_POST_AUTHORITY_MISMATCH")
        secret=runtime.authority.service_attestation_secret(host_id=runtime.descriptor["host_id"],epoch_id=runtime.epoch_id)
    nonce=secrets.token_hex(32)
    fields=_attestation_fields(runtime,nonce)
    expected=hmac.new(bytes.fromhex(secret),a.canonical(fields),hashlib.sha256).hexdigest()
    headers={"Accept":"application/json","Content-Type":"application/json"}
    if client.key:headers["Authorization"]="Bearer "+client.key
    connection=http.client.HTTPConnection(parts.hostname,parts.port or 80,timeout=client.timeout)
    posted=False
    try:
        connection.request("GET","/api/active-runtime/attestation?nonce="+nonce,headers=headers)
        response=connection.getresponse()
        raw=response.read(16*1024+1)
        need(response.status==200 and len(raw)<=16*1024 and not response.will_close and connection.sock is not None,
             "HOST_LIVE_ATTESTED_CONNECTION_REQUIRED")
        proof=json.loads(raw)
        need(isinstance(proof,dict) and {k:v for k,v in proof.items() if k!="signature"}==fields
             and isinstance(proof.get("signature"),str) and hmac.compare_digest(proof["signature"],expected),
             "HOST_SERVER_ATTESTATION_REJECTED")
        live_socket=connection.sock
        with gateway._control():
            runtime.check_binding()
            need(runtime.authority.service_attestation_secret(host_id=runtime.descriptor["host_id"],epoch_id=runtime.epoch_id)==secret,
                 "HOST_SERVER_GENERATION_CHANGED")
        gateway.current("worker")
        headers=gateway.http_headers(headers,payload)
        need(connection.sock is live_socket,"HOST_ATTESTED_SOCKET_CHANGED")
        # HTTPConnection cannot reconnect while this same non-None socket is
        # retained. A peer closing it causes an unknown POST, never a retry.
        posted=True
        connection.request("POST",path,body=body,headers=headers)
        need(connection.sock is live_socket,"HOST_POST_SOCKET_CHANGED")
        response=connection.getresponse()
        content=response.read(RESPONSE_MAX_BYTES+1)
        if response.status>=400:raise ResearchError("HTTP_"+str(response.status))
        need(200<=response.status<300,"HOST_REDIRECT_OR_UNEXPECTED_STATUS_REJECTED")
        need(len(content)<=RESPONSE_MAX_BYTES,"HOST_RESPONSE_SIZE_LIMIT_EXCEEDED")
        return content
    except (OSError,http.client.HTTPException) as error:
        raise ResearchError("NETWORK_FAILED_POST_STATE_UNKNOWN" if posted else "LOCAL_SERVICE_UNATTESTED") from None
    finally:
        connection.close()


def load_wsgi_app(descriptor_path, *, authorization_id, authorize_bootstrap=False):
    """Explicit factory for a separately authorized WSGI host, never a listener."""
    with _gateway()._control():
        runtime = load_api_runtime(descriptor_path, require_bootstrapped=False)
    return bootstrap_app(runtime, authorization_id=authorization_id,
                         authorize_bootstrap=authorize_bootstrap)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", allow_abbrev=False)
    plan.add_argument("--execution-name", required=True)
    plan.add_argument("--registry-root", type=Path, required=True)
    plan.add_argument("--api-profile", type=Path, required=True)
    plan.add_argument("--authorization-id", required=True)
    plan.add_argument("--output", type=Path, required=True)
    check = sub.add_parser("read", allow_abbrev=False)
    check.add_argument("--descriptor", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "plan":
        from scripts.codex_runtime_recovery_v2 import load_runtime
        with _gateway()._control():
            execution = load_runtime(args.execution_name, args.registry_root)
            result = plan_host(execution, w._read(args.api_profile), authorization_id=args.authorization_id)
            # Only a new control descriptor is published; no epoch or default
            # registration changes and no application callback runs here.
            with args.output.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, sort_keys=True)
        print(json.dumps(dict(status="PLANNED_NOT_STARTED", host_id=result["host_id"])))
    else:
        with _gateway()._control():
            runtime = load_api_runtime(args.descriptor, require_bootstrapped=False)
        print(json.dumps(dict(status="BOUND_NOT_STARTED", host_id=runtime.descriptor["host_id"], epoch_id=runtime.epoch_id)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
