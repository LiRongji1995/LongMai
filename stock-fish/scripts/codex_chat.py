"""Codex cross-chat coordination; frozen codex_research remains byte-identical.

Imports all research selection and validation from the existing entry point.
Only location binding, request receipts, and private output storage live here.
"""
from __future__ import annotations
from contextlib import nullcontext
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_research as legacy
from scripts.codex_research import *
from scripts.codex_capacity import CapacityError
from scripts.codex_workspace import WorkspaceError, load_runtime, register_profile, runtime_status

def _stream_sha256(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


class Client(legacy.Client):
    def raw(self, path, payload=None):
        readiness = path == "/health/ready" and payload is None
        if (not path.startswith("/api/") and not readiness) or ".." in path or "\\" in path:
            raise ResearchError("INVALID_API_PATH")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = request.Request(self.base_url + path, data=data, headers=headers)
        # Full-market V2 includes every diagnostic row and its evidence. Keep a
        # bounded larger read only for saved exports / daily reports, never POSTs.
        is_export = payload is None and (parse.urlsplit(path).path == "/api/daily-research/report" or re.fullmatch(
            r"/api/strategy-experiments/[A-Za-z0-9_.-]{1,160}/export", parse.urlsplit(path).path))
        limit = SAVED_REPORT_MAX_BYTES if is_export else RESPONSE_MAX_BYTES
        try:
            with self.opener.open(req, timeout=self.export_timeout if is_export else self.timeout) as response:
                # No automatic retry: a timed-out POST may already have succeeded.
                content = response.read(limit + 1)
        except error.HTTPError as exc:
            # Readiness is an identity handshake, not a claim of data readiness.
            # A service can truthfully report its identity while not ready.
            if readiness and exc.code == 503:
                with exc:
                    content = exc.read(limit + 1)
            else:
                raise ResearchError("HTTP_" + str(exc.code)) from None
        except (error.URLError, TimeoutError, OSError):
            raise ResearchError("NETWORK_FAILED_POST_STATE_UNKNOWN" if payload is not None
                                else "LOCAL_SERVICE_UNREACHABLE") from None
        if len(content) > limit:
            raise ResearchError("RESPONSE_SIZE_LIMIT_EXCEEDED")
        return content


    def get(self, path, payload=None):
        raw = self.raw(path, payload)
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise ResearchError("INVALID_JSON_RESPONSE") from None



class RegisteredClient(Client):
    """Bind the chat to one local instance and durable request receipts."""

    def __init__(self, runtime, key="", timeout=30, request_key=None):
        self.runtime = runtime
        self.report_root = Path(runtime.profile["report_root"])
        self.request_key = identifier(request_key) if request_key else None
        super().__init__(runtime.profile["base_url"], key, timeout)
        self.verify_instance()

    def verify_instance(self):
        self.runtime.check_binding()
        value = json.loads(Client.raw(self, "/health/ready"))
        identity = value.get("service_identity") if isinstance(value, dict) else None
        if not isinstance(identity, dict):
            raise ResearchError("REGISTERED_SERVICE_IDENTITY_MISSING")
        for field in ("db_path", "state_root"):
            actual = identity.get(field)
            if (not isinstance(actual, str) or not Path(actual).is_absolute()
                    or Path(actual).resolve() != Path(self.runtime.profile[field]).resolve()):
                raise ResearchError("REGISTERED_SERVICE_IDENTITY_MISMATCH")
        return value

    @staticmethod
    def job_contract(path, payload):
        if not isinstance(payload, dict):
            raise ResearchError("REGISTERED_POST_OBJECT_REQUIRED")
        if path == "/api/candidate-data-pipeline/capture" and not payload:
            return "scheduled_candidate_capture", {"market_session": "ON_DEMAND_CAPTURE"}
        if path == "/api/strategy-experiments/run":
            if not payload.get("input_id") or not payload.get("strategy_id"):
                raise ResearchError("REGISTERED_STRATEGY_EXPLICIT_INPUT_REQUIRED")
            return "strategy_experiments", dict(payload)
        match = re.fullmatch(r"/api/strategy-experiments/([A-Za-z0-9_.-]{1,160})/tracking", path)
        if match:
            return "strategy_tracking", {**payload, "run_id": match[1]}
        match = re.fullmatch(r"/api/daily-research/runs/([A-Za-z0-9_.-]{1,160})/outcomes", path)
        if match:
            return "daily_research_outcomes", {**payload, "run_id": match[1]}
        raise ResearchError("REGISTERED_POST_PATH_NOT_SUPPORTED")

    def raw(self, path, payload=None):
        if payload is None:
            return super().raw(path)
        kind, expected = self.job_contract(path, payload)
        if not payload.get("input_id") and not self.request_key:
            raise ResearchError("EXPLICIT_REQUEST_KEY_REQUIRED")
        identity = {"method": "POST", "path": path, "payload": payload,
                    "request_key": self.request_key}

        def checked_job(job_id, payload_hash=None):
            job = json.loads(Client.raw(self, "/api/jobs/" + identifier(job_id)))
            frozen = job.get("payload")
            if (job.get("job_id") != job_id or job.get("kind") != kind
                    or not isinstance(frozen, dict)
                    or any(frozen.get(key) != value for key, value in expected.items())):
                raise ResearchError("REGISTERED_JOB_BINDING_MISMATCH")
            digest = hashlib.sha256(json.dumps(frozen, sort_keys=True, separators=(",", ":"),
                                                 ensure_ascii=False).encode("utf-8")).hexdigest()
            if payload_hash is not None and digest != payload_hash:
                raise ResearchError("REGISTERED_JOB_PAYLOAD_CHANGED")
            return {"job_id": job_id, "kind": kind, "payload_sha256": digest}

        def action():
            self.verify_instance()
            jobs = json.loads(Client.raw(self, "/api/jobs?limit=100"))
            if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
                raise ResearchError("INVALID_JOB_LIST_RESPONSE")
            active = [job for job in jobs if job.get("state") not in TERMINAL]
            matching = [job for job in active if job.get("kind") == kind
                        and isinstance(job.get("payload"), dict)
                        and all(job["payload"].get(key) == value for key, value in expected.items())]
            # A new chat/request key must not bypass an existing worker. Its
            # actual completion and storage growth remain owned by that worker.
            if len(active) == len(matching) == 1:
                return {**checked_job(matching[0]["job_id"]), "submitted_new_job": False}
            if active:
                raise ResearchError("ACTIVE_JOB_REQUIRES_FOLLOW_NO_NEW_SUBMISSION")
            if len(jobs) >= 100:
                raise ResearchError("JOB_WINDOW_FULL_CHECK_INFLIGHT_REQUIRED")
            accepted = json.loads(Client.raw(self, path, payload))
            return {**checked_job(accepted["job_id"]), "submitted_new_job": True}

        def validate(receipt):
            self.verify_instance()
            if receipt.get("kind") != kind:
                raise ResearchError("REGISTERED_JOB_BINDING_MISMATCH")
            checked_job(receipt["job_id"], receipt["payload_sha256"])
            return True

        result = self.runtime.run_once(identity, action, validate)
        return json.dumps({"job_id": result["receipt"]["job_id"],
                           "reused_request": result["reused"],
                           "reused_inflight_job": not result["receipt"].get("submitted_new_job", True),
                           "capacity_observation": "ACCEPTANCE_ONLY_WORKER_MAY_STILL_GROW"}).encode("utf-8")


def local_export_pair(client, run_id, strategy_id, selection):
    """Reuse the one saved export after remote identity and local byte checks."""
    if not isinstance(client, RegisteredClient):
        return None
    with client.runtime.exclusive():
        return _local_export_pair(client, run_id, strategy_id, selection)


def _local_export_pair(client, run_id, strategy_id, selection):
    root = client.report_root / identifier(run_id)
    if not root.exists():
        return None
    if root.is_symlink() or not root.is_dir():
        raise ResearchError("SHARED_REPORT_PATH_INVALID")
    remote = None
    permitted = {"source.json", "candidates.csv", "review.json", "report.md", "market-explanation-display.json"}
    for directory in sorted(root.iterdir(), reverse=True):
        if directory.is_symlink():
            raise ResearchError("SHARED_REPORT_PATH_INVALID")
        if not directory.is_dir():
            continue
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            raise ResearchError("SHARED_REPORT_INCOMPLETE_PRESERVED")
        try:
            if manifest_path.is_symlink() or manifest_path.stat().st_size > 1024 * 1024:
                raise ResearchError("SHARED_REPORT_MANIFEST_INVALID")
            manifest = json.loads(manifest_path.read_bytes())
            if (manifest.get("run_id") != run_id or manifest.get("strategy_id", PV) != strategy_id
                    or manifest.get("selection", "all") != selection):
                continue
            remote = remote or summary(client, run_id, page_size=1, strategy_id=strategy_id, selection=selection)
            if manifest.get("source_content_hash") != remote.get("source_run_hash"):
                raise ResearchError("SHARED_REPORT_SOURCE_IDENTITY_CHANGED")
            files = manifest["files"]
            if (not isinstance(files, dict) or not {"source.json", "candidates.csv"}.issubset(files)
                    or not set(files).issubset(permitted)):
                raise ResearchError("SHARED_REPORT_MANIFEST_INVALID")
            pair = {}
            for name, metadata in files.items():
                path = directory / name
                if (path.is_symlink() or not path.is_file() or path.stat().st_size != metadata["bytes"]
                        or path.stat().st_size > SAVED_REPORT_MAX_BYTES):
                    raise ResearchError("SHARED_REPORT_BYTES_CHANGED")
                if name in {"source.json", "candidates.csv"}:
                    raw = path.read_bytes()
                    digest = hashlib.sha256(raw).hexdigest()
                    pair[name] = raw
                else:
                    with path.open("rb") as stream:
                        digest = _stream_sha256(stream)
                if digest != metadata["sha256"]:
                    raise ResearchError("SHARED_REPORT_BYTES_CHANGED")
            return pair["source.json"], pair["candidates.csv"]
        except (OSError, KeyError, TypeError, ValueError):
            raise ResearchError("SHARED_REPORT_INVALID_PRESERVED") from None
    return None


def reusable_local_bundle(root, manifest, strict=False):
    """Reuse verified local derivatives; never overwrite a prior report directory."""
    def target_key(value):
        return pick(value or {}, ("target_session", "expected_observation_date", "latest_completed_session", "calendar_version"))

    if not root.is_dir():
        return None
    permitted = {"source.json", "candidates.csv", "review.json", "report.md", "market-explanation-display.json"}
    for candidate in sorted(root.iterdir(), reverse=True):
        if not candidate.is_dir():
            continue
        try:
            old = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
            if (old.get("run_id") != manifest["run_id"] or old.get("source_content_hash") != manifest["source_content_hash"]
                    or old.get("strategy_id", PV) != manifest.get("strategy_id", PV)
                    or old.get("selection", "all") != manifest.get("selection", "all")
                    or target_key(old.get("target")) != target_key(manifest.get("target"))):
                continue
            old_files = old["files"]
            if not set(manifest["files"]).issubset(old_files) or not set(old_files).issubset(permitted):
                continue
            # Compare source and current review. Existing prose remains an immutable
            # report of its creation time; wording-only changes do not copy 25 MB.
            if any(old_files[name] != expected for name, expected in manifest["files"].items() if name != "report.md"):
                continue
            verified = True
            for name, expected in old_files.items():
                path = candidate / name
                if path.stat().st_size != expected["bytes"]:
                    verified = False
                    break
                with path.open("rb") as handle:
                    if _stream_sha256(handle) != expected["sha256"]:
                        verified = False
                        break
            if verified:
                return candidate
            if strict:
                raise ResearchError("SHARED_REPORT_BYTES_CHANGED")
        except (OSError, KeyError, TypeError, ValueError):
            if strict:
                raise ResearchError("SHARED_REPORT_INVALID_PRESERVED") from None
            continue
    return None


def save_bundle(client, bundle, output=None, context=None, with_report=True, strategy_id=PV,
                market_explanation=None, attachment_as_of=None):
    runtime = getattr(client, "runtime", None)
    with runtime.exclusive() if runtime else nullcontext():
        return _save_bundle(client, bundle, output, context, with_report, strategy_id,
                            market_explanation, attachment_as_of)


def _save_bundle(client, bundle, output=None, context=None, with_report=True, strategy_id=PV,
                 market_explanation=None, attachment_as_of=None):
    saved, json_bytes, csv_bytes, first = bundle
    if saved.get("coverage_type") not in REAL_COVERAGE:
        raise ResearchError("NON_REAL_OR_UNVERIFIABLE_INPUT_NOT_FOR_DAILY_RESEARCH")
    # Finish all remote reads/validation before creating any new output directory.
    review = client.get(prefix(saved["run_id"]) + "/review") if with_report else None
    now = datetime.now(SH)
    report_root = Path(getattr(client, "report_root", ROOT / "reports" / "codex"))
    directory = Path(output) if output else report_root / identifier(saved["run_id"]) / now.strftime("%Y%m%dT%H%M%S%f")
    files = {"source.json": json_bytes, "candidates.csv": csv_bytes}
    if with_report:
        files["review.json"] = json.dumps(review, ensure_ascii=False, indent=2).encode("utf-8")
        rendered = report_text(saved, review, context, strategy_id)
        if market_explanation:
            from stockfish_core.yaogu.market_explanation_artifacts import archived_explanation
            snapshot = {"identity": {key: saved.get(key) for key in ("run_id", "observation_date")},
                        "input_hash": saved.get("input_hash"), "source_run_hash": saved.get("content_hash")}
            attachment = archived_explanation(market_explanation, snapshot=snapshot,
                as_of=attachment_as_of or datetime.now(SH))
            files["market-explanation-display.json"] = json.dumps(attachment, ensure_ascii=False, indent=2).encode("utf-8")
            rendered += "\n\n## 独立市场趋势解释附件\n\n" + attachment["markdown"]
        files["report.md"] = rendered.encode("utf-8")
    manifest = {"created_at": now.isoformat(), "api_base_url": client.base_url, "run_id": saved["run_id"],
                "input_id": saved["input_id"], "input_hash": saved.get("input_hash"),
                "decision_cutoff": saved.get("decision_cutoff"), "data_watermark": saved.get("data_watermark"),
                "observation_date": saved["observation_date"], "coverage_type": saved.get("coverage_type"),
                "spec_hash": saved.get("spec_hash"), "source_hash": saved.get("source_hash"),
                "source_content_hash": saved["export_view"]["source_content_hash"],
                "ranking_count": len(saved["results"][strategy_id]["items"]), "strategy_id": strategy_id,
                "selection": saved["export_view"]["selection"], "target": context,
                "verification": "OFFICIAL_JSON_CSV_ALL_SUMMARY_PAGES_SAME_IDS_RANKS_COUNT_SOURCE_HASH",
                "files": {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in files.items()}}
    if strategy_id in SHORT_HORIZON_PROFILES:
        manifest.update(qualified_pool_count=(saved["results"][strategy_id].get("counts") or {}).get("eligible"),
            research_observation_count=(saved["results"][strategy_id].get("counts") or {}).get("research_observation"),
            formal_focus_count=0, ranking_role="RESEARCH_DISPLAY_ORDER_NOT_RECOMMENDATION_PRIORITY")
    reused = reusable_local_bundle(directory.parent, manifest, strict=isinstance(client, RegisteredClient)) if output is None else None
    result = concise(first, strategy_id)
    result.update(saved_directory=str((reused or directory).resolve()), files=[*files, "manifest.json"], target=context,
                  reused_saved_run=True, reused_local_bundle=reused is not None,
                  newly_collected_data=False, newly_computed_ranking=False)
    if reused is not None:
        return result
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    report_preflight(client, directory, files)
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise ResearchError("OUTPUT_DIRECTORY_EXISTS_PRESERVED") from None
    for name, data in files.items():
        with (directory / name).open("xb") as handle:
            handle.write(data)
    return result


def report_preflight(client, directory, files):
    runtime = getattr(client, "runtime", None)
    if runtime is None:
        return
    directory = Path(directory).absolute()
    if not directory.resolve().is_relative_to(Path(runtime.profile["report_root"])):
        raise ResearchError("REGISTERED_REPORT_ROOT_REQUIRED")
    for path in [directory, *directory.parents]:
        if path.is_symlink():
            raise ResearchError("SHARED_REPORT_PATH_INVALID")
    result = runtime.preflight(expected_growth_bytes=sum(len(raw) for raw in files.values()),
                               destination=directory)
    if not result["allowed"]:
        raise ResearchError("CAPACITY_PREFLIGHT_BLOCKED")


def save_small_report(client, contents, output=None, group="daily-loop"):
    """Serialize report writes, and reuse exactly equal bytes across chats."""
    runtime = getattr(client, "runtime", None)
    root = Path(getattr(client, "report_root", ROOT / "reports" / "codex")) / group
    directory = Path(output) if output else root / datetime.now(SH).strftime("%Y%m%dT%H%M%S%f")
    with runtime.exclusive() if runtime else nullcontext():
        if runtime and output is None and root.is_dir():
            for old in sorted(root.iterdir(), reverse=True):
                if old.is_symlink():
                    raise ResearchError("SHARED_REPORT_PATH_INVALID")
                if not old.is_dir():
                    continue
                if all((old / name).is_file() and not (old / name).is_symlink()
                       and (old / name).stat().st_size == len(raw)
                       and (old / name).read_bytes() == raw for name, raw in contents.items()):
                    return old, True
        report_preflight(client, directory, contents)
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            raise ResearchError("OUTPUT_DIRECTORY_EXISTS_PRESERVED") from None
        for name, raw in contents.items():
            with (directory / name).open("xb") as stream:
                stream.write(raw)
        return directory, False


def daily_observation(args, client):
    """Read one server projection; optional files are a private local copy only."""
    if not 1 <= args.days <= 20:
        raise ResearchError("OBSERVATION_DAYS_MUST_BE_1_TO_20")
    query = {"days": args.days}
    if args.target_session is not None:
        query["target_session"] = args.target_session.isoformat()
    if args.as_of:
        try:
            instant = datetime.fromisoformat(args.as_of)
        except ValueError:
            raise ResearchError("INVALID_OBSERVATION_CUTOFF") from None
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ResearchError("OBSERVATION_CUTOFF_REQUIRES_TIMEZONE")
        query["as_of"] = instant.isoformat()
    path = "/api/daily-research/observation?" + parse.urlencode(query)
    report = client.get(path)
    if (not isinstance(report, dict) or report.get("schema_version") != "DAILY_OBSERVATION_V1"
            or not isinstance(report.get("current"), dict) or not isinstance(report.get("history"), list)
            or not isinstance(report.get("markdown"), str)
            or not {"report_as_of", "target_session", "requested_days", "available_days", "calendar_status"}.issubset(report)):
        raise ResearchError("INVALID_OBSERVATION_RESPONSE")
    if args.output is None:
        return report
    contents = {"report.json": json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
                "report.md": report["markdown"].encode("utf-8")}
    manifest = {"schema_version": report["schema_version"], "api_path": path,
                "source": query, "report_as_of": report["report_as_of"],
                "files": {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                          for name, raw in contents.items()}}
    contents["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    directory, _ = save_small_report(client, contents, args.output, "daily-observation")
    return {**report, "report_directory": str(directory.resolve())}


def official_exports(client, run_id, strategy_id=PV, selection="all"):
    cached = local_export_pair(client, run_id, strategy_id, selection)
    if cached is None:
        return legacy.official_exports(client, run_id, strategy_id, selection)
    # All official JSON/CSV/summary-page checks remain in the frozen entry.
    class ExportView:
        base_url = client.base_url
        def get(self, path, payload=None):
            return client.get(path, payload)
        def raw(self, path, payload=None):
            expected = prefix(run_id) + "/export?format="
            if payload is not None or not path.startswith(expected):
                raise ResearchError("SHARED_EXPORT_READ_PATH_MISMATCH")
            return cached[1] if path.startswith(expected + "csv&") else cached[0]
    return legacy.official_exports(ExportView(), run_id, strategy_id, selection)


def select_saved(client, context, input_id=None, strategy_id=PV):
    # Legacy selection calls its own official_exports; provide already verified
    # shared bytes through a narrow transport view without monkeypatching globals.
    class SavedView:
        base_url = client.base_url
        def __init__(self):
            self.pairs = {}
        def get(self, path, payload=None):
            return client.get(path, payload)
        def raw(self, path, payload=None):
            parsed = parse.urlsplit(path)
            match = re.fullmatch(r"/api/strategy-experiments/([A-Za-z0-9_.-]{1,160})/export", parsed.path)
            if match and payload is None:
                query = parse.parse_qs(parsed.query)
                strategy = query.get("strategy_id", [PV])[0]
                selection = query.get("selection", ["all"])[0]
                key = (match[1], strategy, selection)
                if key not in self.pairs:
                    self.pairs[key] = local_export_pair(client, *key)
                pair = self.pairs[key]
                if pair:
                    return pair[1] if query.get("format") == ["csv"] else pair[0]
            return client.raw(path, payload)
    return legacy.select_saved(SavedView(), context, input_id, strategy_id)


def submit_or_reuse_job(client, kind, path, body, wait_seconds, run_id=None, payload_match=None):
    if not isinstance(client, RegisteredClient):
        return legacy.submit_or_reuse_job(client, kind, path, body, wait_seconds, run_id, payload_match)
    accepted = client.get(path, body)
    result = await_job(client, accepted, wait_seconds)
    result["reused_request"] = accepted["reused_request"]
    return result


def shared_local_command(runtime, args):
    """Dispatch bounded units with the registered interpreter and shared roots."""
    from scripts.codex_premarket import _prepared, _run
    if args.command not in {"core-run", "market-plan", "market-sync", "core-run-compact",
                            "core-archive-register", "core-archive-reuse", "core-archive-status"}:
        raise ResearchError("UNKNOWN_SHARED_COMMAND")
    if runtime is None:
        raise ResearchError("RUNTIME_REGISTRATION_REQUIRED")
    if runtime.profile["backend"] != "premarket":
        raise ResearchError("PREMARKET_BACKEND_REQUIRED")
    runtime.check_binding()
    prepared = _prepared(runtime.profile)
    common = ["--runtime", runtime.profile["name"], "--registry-root", str(runtime.registry_root)]
    if args.command in {"core-run", "core-run-compact"}:
        script = "codex_core.py" if args.command == "core-run" else "codex_compact_core.py"
        arguments = [*common, "--manifest", str(args.manifest.expanduser().absolute())]
        if args.command == "core-run-compact":
            arguments += ["--profile", args.profile]
    elif args.command.startswith("core-archive-"):
        script = "codex_core_archive.py"
        operation = args.command.removeprefix("core-archive-")
        arguments = [*common, operation]
        if operation != "status":
            arguments += ["--profile", args.profile]
        if operation == "register":
            arguments += ["--manifest", str(args.manifest.expanduser().absolute()),
                          "--worker-dir", str(args.worker_dir.expanduser().absolute()),
                          "--supervisor-dir", str(args.supervisor_dir.expanduser().absolute())]
        elif operation == "reuse":
            arguments += ["--archive-id", args.archive_id]
            for name in ("observation_date", "target_session", "cutoff", "manifest"):
                value = getattr(args, name, None)
                if value is not None:
                    arguments += ["--" + name.replace("_", "-"), str(value)]
    else:
        script = "codex_incremental.py"
        arguments = ["plan" if args.command == "market-plan" else "sync", *common,
                     "--codes", *args.codes, "--sessions", *args.sessions]
        if args.command == "market-sync":
            arguments += ["--calendar-record", str(args.calendar_record.expanduser().absolute())]
            if args.offline:
                arguments.append("--offline")
    command = [str(prepared["python"]), "-I", "-B", str(prepared["source"] / "scripts" / script), *arguments]
    # The selected child owns its physical resource lock and bounded worker.
    # Its budget/worker timeout is authoritative; do not orphan SDK workers by
    # killing their parent using an independent shorter wrapper timer.
    code, raw, _ = _run(command, prepared)
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ResearchError("SHARED_COMMAND_INVALID_RESPONSE") from None
    if not isinstance(result, dict):
        raise ResearchError("SHARED_COMMAND_INVALID_RESPONSE")
    return {**result, "command_exit_code": code, "runtime": runtime.profile["name"]}


def shared_first_run_command(runtime, args):
    """Use independently versioned, bounded first-run workers in the shared environment."""
    from scripts.codex_premarket import _prepared, _run
    if runtime is None or runtime.profile["backend"] != "premarket":
        raise ResearchError("REGISTERED_PREMARKET_RUNTIME_REQUIRED")
    runtime.check_binding()
    prepared = _prepared(runtime.profile)
    common = ["--runtime", runtime.profile["name"], "--registry-root", str(runtime.registry_root)]
    if args.command == "core-first-run":
        script = "codex_first_core.py"
        arguments = [*common, "--manifest", str(args.manifest.expanduser().absolute()),
                     "--profile", args.profile]
        if args.force_first:
            arguments += ["--force-first", args.force_first]
    elif args.command in {"core-sources-plan", "core-sources-build"}:
        script = "codex_core_sources.py"
        arguments = [*common, args.command.removeprefix("core-sources-"),
                     "--profile", args.profile, "--identity-policy", args.identity_policy]
        if args.as_of:
            arguments += ["--as-of", args.as_of]
    elif args.command in {"latest-plan", "latest-sync"}:
        script = "codex_latest_capture.py"
        arguments = [args.command.removeprefix("latest-"), *common, "--mode", args.mode,
                     "--profile", args.profile, "--calendar-record", str(args.calendar_record.expanduser().absolute())]
        for name in ("codes", "sessions"):
            value = getattr(args, name, None)
            if value:
                arguments += ["--" + name, *value]
        if getattr(args, "execute_network", False):
            arguments.append("--execute-network")
    else:
        raise ResearchError("UNKNOWN_FIRST_RUN_COMMAND")
    # Each child owns its lock, admission and supervisor. A second wrapper
    # timeout could orphan the original provider's SDK worker.
    code, raw, _ = _run([str(prepared["python"]), "-I", "-B",
                         str(prepared["source"] / "scripts" / script), *arguments], prepared)
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ResearchError("FIRST_RUN_COMMAND_INVALID_RESPONSE") from None
    if not isinstance(result, dict):
        raise ResearchError("FIRST_RUN_COMMAND_INVALID_RESPONSE")
    return {**result, "command_exit_code": code, "runtime": runtime.profile["name"]}


def shared_factor_command(runtime, args):
    """A registered resource lock and write policy surround every factor call."""
    import math
    from scripts.codex_premarket import _prepared, _control
    from scripts import codex_factor_incremental as factor
    if runtime is None or runtime.profile.get("backend") != "premarket":
        raise ResearchError("REGISTERED_PREMARKET_RUNTIME_REQUIRED")
    prepared = _prepared(runtime.profile)
    if prepared["source"] != ROOT or Path(sys.executable).absolute() != prepared["python"]:
        raise ResearchError("FACTOR_REGISTERED_SOURCE_AND_INTERPRETER_REQUIRED")
    operation = args.command.removeprefix("factor-")
    if operation not in {"plan", "sync", "reuse"}:
        raise ResearchError("UNKNOWN_FACTOR_COMMAND")
    network = operation == "sync" and args.execute_network
    expected_profile = "FACTORS_DELTA_DIAGNOSTIC_V1" if operation == "sync" else "FACTORS_READONLY_V1"
    if args.profile != expected_profile:
        raise ResearchError("EXPLICIT_FACTOR_PROFILE_REQUIRED")
    cache = prepared["state"] / "cache"
    with runtime.exclusive():
        configuration, _ = _control(prepared["config"])
        capture = configuration.get("capture", {})
        configured_limits = configuration.get("limits", {})
        if not isinstance(capture, dict) or not isinstance(configured_limits, dict):
            raise ResearchError("FACTOR_CONFIGURED_LIMIT_INVALID")
        if type(capture.get("offline", False)) is not bool:
            raise ResearchError("FACTOR_CONFIGURED_OFFLINE_INVALID")
        limits = dict(factor.CAPS)
        if operation == "sync":
            if args.purpose != "DELTA_FACTS_ONLY":
                raise ResearchError("EXPLICIT_FACTOR_DIAGNOSTIC_PURPOSE_REQUIRED")
            if network and capture.get("offline", False):
                raise ResearchError("FACTOR_REGISTERED_OFFLINE_POLICY")
            # An explicit diagnostic is still bounded by the registered writer
            # limits. A readonly profile never enlarges the write allowance.
            for key, value in configured_limits.items():
                if key in limits:
                    if value is None and key == "max_seconds":
                        continue
                    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                        raise ResearchError("FACTOR_CONFIGURED_LIMIT_INVALID")
                    limits[key] = min(limits[key], value)
            if "request_timeout_seconds" in capture:
                value = capture["request_timeout_seconds"]
                if type(value) not in (int, float) or not math.isfinite(value) or value < 1:
                    raise ResearchError("FACTOR_CONFIGURED_LIMIT_INVALID")
                limits["request_timeout_seconds"] = int(min(limits["request_timeout_seconds"], value))
        result = getattr(factor, operation)(source_root=prepared["source"], cache_root=cache,
            baseline_record=factor._read_control(args.baseline_record),
            calendar_record=factor._read_control(args.calendar_record), target_session=str(args.target_session),
            overlap_days=args.overlap_days, limits=limits, offline=not network,
            purpose="DELTA_FACTS_ONLY" if operation == "sync" else None,
            admit=lambda growth: runtime.preflight(expected_growth_bytes=growth, destination=cache))
    return {**result, "runtime": runtime.profile["name"], "profile": args.profile,
            "registered_writer_limits_unchanged": True}


def parser():
    result = legacy.parser()
    result.description = "Codex 跨聊天入口：先登记已有实例，再复用任务和报告；不复制数据库。离线研究保留 codex_research.py 入口。"
    result.set_defaults(base_url=None, env_file=None)
    for action in result._actions:
        if action.dest in {"base_url", "env_file"}:
            action.help = "仅旧入口支持；本入口的连接信息必须来自已登记runtime"
    result.add_argument("--runtime", help="已登记的跨聊天实例；默认读取环境或默认登记")
    result.add_argument("--registry-root", type=Path, help="私有跨聊天登记目录")
    result.add_argument("--request-key", help="同一逻辑请求的稳定标识；采集和无input更新必需")
    commands = next(action for action in result._actions if isinstance(action, argparse._SubParsersAction))
    sub = commands.add_parser("runtime-register", help="登记已有实例；不复制库、不启动服务")
    sub.add_argument("--config", type=Path, required=True)
    sub.add_argument("--default", action="store_true")
    commands.add_parser("runtime-status", help="只读当前登记；未配置不连接默认8000")
    commands.add_parser("storage-status", help="只读容量账本；不删除原证据")
    sub = commands.add_parser("premarket", help="跨聊天协调既有独立盘前入口")
    sub.add_argument("--observation-date", type=observation_session, required=True)
    sub.add_argument("--target-session", type=observation_session, required=True)
    for name in ("market-plan", "market-sync"):
        sub = commands.add_parser(name, help="同一共享cache中的有界逐日RAW增量；不执行选股")
        sub.add_argument("--codes", nargs="+", required=True)
        sub.add_argument("--sessions", nargs="+", required=True)
        if name == "market-sync":
            sub.add_argument("--calendar-record", type=Path, required=True)
            sub.add_argument("--offline", action="store_true")
    sub = commands.add_parser("core-run", help="复用相同真实来源与截止的原盘前核心计算")
    sub.add_argument("--manifest", type=Path, required=True)
    sub = commands.add_parser("core-run-compact", help="显式运行原核心并以新版本压缩保存；旧产物不改写")
    sub.add_argument("--manifest", type=Path, required=True)
    sub.add_argument("--profile", choices=["FULL_CORE_COMPACT_V1"], required=True)
    sub = commands.add_parser("core-first-run", help="显式首次计算编码档位；冻结策略及全部证据校验保持")
    sub.add_argument("--manifest", type=Path, required=True)
    sub.add_argument("--profile", choices=["FIRST_CORE_REFERENCE_V1", "FIRST_CORE_CANONICAL_128_V1"], required=True)
    sub.add_argument("--force-first", help="仅有界工程对照使用的独立首次ID；同ID失败不自动重试")
    for operation in ("plan", "build"):
        sub = commands.add_parser("core-sources-" + operation, help="离线核对25日核心来源；题材67日缺口独立报告")
        sub.add_argument("--profile", choices=["CORE_SOURCES_FIRST_V1"], required=True)
        sub.add_argument("--identity-policy", choices=["NATIVE_CORE_UNKNOWN_PRESERVED_V1"], required=True)
        sub.add_argument("--as-of", help="显式历史回放截止；不传则核对当前最新已闭市日期")
    for operation in ("plan", "sync"):
        sub = commands.add_parser("latest-" + operation, help="最新已闭市会话的有界原始采集；连续缺口合并")
        sub.add_argument("--mode", choices=["market-session", "scoped-history"], required=True)
        sub.add_argument("--profile", choices=["LATEST_MARKET_SESSION_V1", "LATEST_SCOPED_HISTORY_V1"], required=True)
        sub.add_argument("--calendar-record", type=Path, required=True)
        sub.add_argument("--codes", nargs="+")
        sub.add_argument("--sessions", nargs="+")
        if operation == "sync":
            sub.add_argument("--execute-network", action="store_true")
    sub = commands.add_parser("core-archive-register", help="鉴证完整原核心并登记共享归档，不重新选股")
    sub.add_argument("--manifest", type=Path, required=True)
    sub.add_argument("--worker-dir", type=Path, required=True)
    sub.add_argument("--supervisor-dir", type=Path, required=True)
    sub.add_argument("--profile", choices=["FULL_CORE_ARCHIVE_READONLY_V1"], required=True)
    sub = commands.add_parser("core-archive-reuse", help="完整核验已登记原结果，零重算复用")
    sub.add_argument("--archive-id", required=True)
    sub.add_argument("--profile", choices=["FULL_CORE_ARCHIVE_READONLY_V1"], required=True)
    sub.add_argument("--observation-date", type=observation_session)
    sub.add_argument("--target-session", type=observation_session)
    sub.add_argument("--cutoff")
    sub.add_argument("--manifest", type=Path)
    commands.add_parser("core-archive-status", help="只读归档定位；展示名单前仍须reuse验真")
    for operation in ("plan", "reuse", "sync"):
        sub = commands.add_parser("factor-" + operation, help="复权原件复用及显式尾窗事实；不合成策略输入")
        sub.add_argument("--baseline-record", type=Path, required=True)
        sub.add_argument("--calendar-record", type=Path, required=True)
        sub.add_argument("--target-session", type=observation_session, required=True)
        sub.add_argument("--overlap-days", type=int, default=7)
        sub.add_argument("--profile", choices=["FACTORS_DELTA_DIAGNOSTIC_V1" if operation == "sync"
                                               else "FACTORS_READONLY_V1"], required=True)
        if operation == "sync":
            sub.add_argument("--purpose", choices=["DELTA_FACTS_ONLY"], required=True)
            sub.add_argument("--execute-network", action="store_true")
    return result


def execute(args, client):
    if args.command == "daily-observation":
        return daily_observation(args, client)
    if args.command == "daily-report":
        query = {"run_id": identifier(args.run_id)} if args.run_id else {"target_session": str(args.target_session)}
        if args.as_of:
            instant = datetime.fromisoformat(args.as_of)
            if instant.tzinfo is None or instant.utcoffset() is None:
                raise ResearchError("REPORT_CUTOFF_REQUIRES_TIMEZONE")
            query["as_of"] = instant.isoformat()
        report = client.get("/api/daily-research/report?" + parse.urlencode(query))
        if getattr(args, "market_explanation", None):
            from stockfish_core.yaogu.market_explanation_artifacts import archived_explanation
            attachment = archived_explanation(args.market_explanation, snapshot=report.get("snapshot"),
                                              as_of=report["report_as_of"])
            existing = report.get("research_attachments") or []
            if not isinstance(existing, list):
                raise ValueError("MARKET_EXPLANATION_EXISTING_ATTACHMENT_SCHEMA_INVALID")
            report = {**report, "research_attachments": [*existing, attachment],
                      "markdown": report["markdown"] + "\n\n## 独立市场趋势解释附件\n\n" + attachment["markdown"]}
        contents = {"report.json": json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
                    "report.md": report["markdown"].encode("utf-8")}
        contents["manifest.json"] = json.dumps({"source": query,
            "report_as_of": report["report_as_of"],
            "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in contents.items()}},
            ensure_ascii=False, indent=2).encode("utf-8")
        directory, reused = save_small_report(client, contents, args.output)
        return {"report_directory": str(directory.resolve()), "delivery": report.get("delivery"),
                "identity": (report.get("snapshot") or {}).get("identity"), "report_as_of": report["report_as_of"],
                "reused_local_bundle": reused}
    if args.command == "daily-outcomes":
        path = "/api/daily-research/runs/" + identifier(args.run_id) + "/outcomes"
        if args.update:
            if not 0 <= args.wait_seconds <= 60:
                raise ResearchError("WAIT_SECONDS_OUT_OF_RANGE")
            body = {"input_id": identifier(args.input_id)} if args.input_id else {}
            return await_job(client, client.get(path, body), args.wait_seconds)
        if args.instrument_id:
            path += "?" + parse.urlencode({"instrument_id": identifier(args.instrument_id)})
        return client.get(path)
    if args.command in {"select", "run"}:
        context = calendar_context(args.target)
        explicit_strategy = getattr(args, "strategy", None)
        if explicit_strategy:
            if args.profile is not None and args.profile != explicit_strategy:
                raise ResearchError("PROFILE_AND_EXPLICIT_STRATEGY_MISMATCH")
            if not 0 <= args.wait_seconds <= 60:
                raise ResearchError("WAIT_SECONDS_OUT_OF_RANGE")
            try:
                bundle, skipped = select_saved(client, context, args.input_id, explicit_strategy)
            except ResearchError as exc:
                if str(exc) != "NO_REUSABLE_REAL_RUN_INPUT_VERIFICATION_REQUIRED":
                    raise
                body = {"input_id": identifier(args.input_id), "strategy_id": explicit_strategy}
                job = submit_or_reuse_job(client, "strategy_experiments", "/api/strategy-experiments/run",
                    body, args.wait_seconds, payload_match=body)
                if job.get("state") != "SUCCEEDED":
                    return {**job, "target": context, "research_profile": explicit_strategy}
                run_id = identifier((job.get("result") or {})["run_id"])
                bundle = official_exports(client, run_id, explicit_strategy)
                verify_reusable(bundle[0], explicit_strategy)
                skipped = []
                result = save_bundle(client, bundle, args.output, context, strategy_id=explicit_strategy)
                result.update(newly_computed_ranking=True, reused_saved_run=False, job_id=job["job_id"])
                return result
            result = save_bundle(client, bundle, args.output, context, strategy_id=explicit_strategy)
            result["skipped_non_reusable_runs"] = skipped
            return result
        if args.profile is None:
            raise ResearchError("EXPLICIT_RESEARCH_PROFILE_REQUIRED")
        bundle, skipped = select_saved(client, context, getattr(args, "input_id", None), args.profile)
        result = save_bundle(client, bundle, args.output, context, strategy_id=args.profile)
        result["skipped_non_reusable_runs"] = skipped
        return result
    if args.command in {"export", "report"}:
        return save_bundle(client, official_exports(client, args.run_id, args.profile, getattr(args, "selection", "all")), args.output,
                           with_report=args.command == "report", strategy_id=args.profile,
                           market_explanation=getattr(args, "market_explanation", None),
                           attachment_as_of=getattr(args, "attachment_as_of", None))
    if args.command == "track":
        if not args.update:
            return review_summary(client.get(prefix(args.run_id) + "/review"))
        if not 0 <= args.wait_seconds <= 60:
            raise ResearchError("WAIT_SECONDS_MUST_BE_0_TO_60")
        body = {"input_id": identifier(args.input_id)} if args.input_id else {}
        return submit_or_reuse_job(client, "strategy_tracking", prefix(args.run_id) + "/tracking", body,
                                  args.wait_seconds, run_id=args.run_id)
    if args.command == "capture":
        if not 0 <= args.wait_seconds <= 60:
            raise ResearchError("WAIT_SECONDS_MUST_BE_0_TO_60")
        return submit_or_reuse_job(client, "scheduled_candidate_capture", "/api/candidate-data-pipeline/capture", {}, args.wait_seconds)
    return legacy.execute(args, client)

def main(argv=None):
    args = parser().parse_args(argv)
    key = ""
    try:
        if args.base_url is not None or args.env_file is not None:
            raise ResearchError("REGISTERED_RUNTIME_LOCATOR_MUST_COME_FROM_PROFILE")
        if args.command == "runtime-register":
            result = register_profile(args.config, args.registry_root, make_default=args.default)
        elif args.command == "runtime-status":
            result = runtime_status(args.runtime, args.registry_root)
        elif args.command == "storage-status":
            runtime = load_runtime(args.runtime, args.registry_root)
            result = runtime.storage_report() if runtime else runtime_status(None, args.registry_root)
        elif args.command in {"market-plan", "market-sync", "core-run", "core-run-compact",
                              "core-archive-register", "core-archive-reuse", "core-archive-status"}:
            result = shared_local_command(load_runtime(args.runtime, args.registry_root), args)
        elif args.command in {"factor-plan", "factor-reuse", "factor-sync"}:
            result = shared_factor_command(load_runtime(args.runtime, args.registry_root), args)
        elif args.command in {"core-first-run", "core-sources-plan", "core-sources-build", "latest-plan", "latest-sync"}:
            result = shared_first_run_command(load_runtime(args.runtime, args.registry_root), args)
        elif args.command == "premarket":
            from scripts.codex_premarket import execute_premarket
            runtime = load_runtime(args.runtime, args.registry_root)
            if runtime is None:
                raise ResearchError("PREMARKET_RUNTIME_REGISTRATION_REQUIRED")
            if runtime.profile["backend"] != "premarket":
                raise ResearchError("PREMARKET_BACKEND_REQUIRED")
            if not args.request_key:
                raise ResearchError("EXPLICIT_REQUEST_KEY_REQUIRED")
            result = execute_premarket(runtime.profile, observation_date=str(args.observation_date),
                                       target_session=str(args.target_session),
                                       request_key=identifier(args.request_key), coordinator=runtime)
        elif args.command in {"short-horizon-r2", "market-explanation", "deliver-saved-r2",
                              "deliver-saved-r2-stream", "economic-direction-display", "research-r1"}:
            raise ResearchError("OFFLINE_RESEARCH_USE_FROZEN_CODEX_RESEARCH_ENTRY")
        else:
            runtime = load_runtime(args.runtime, args.registry_root)
            if runtime is None:
                raise ResearchError("RUNTIME_REGISTRATION_REQUIRED")
            if runtime.profile["backend"] != "api":
                raise ResearchError("API_BACKEND_REQUIRED_USE_PREMARKET_COMMAND")
            key = api_key(runtime.profile.get("env_file"))
            client = RegisteredClient(runtime, key, args.timeout, args.request_key)
            result = execute(args, client)
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        print(rendered.replace(key, "[REDACTED]") if key else rendered)
        return result.get("command_exit_code", 0)
    except (ResearchError, WorkspaceError, CapacityError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        code = str(exc).split(":")[0]
        reason = code if isinstance(exc, ValueError) and re.fullmatch(r"[A-Z][A-Z0-9_]+", code) else "LOCAL_DEPENDENCY_OR_RESPONSE_SCHEMA_ERROR"
        print(json.dumps({"status": "BLOCKED", "reason": reason}))
        return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
