"""Private cross-chat runtime locators and durable, bounded operation coordination.

Only metadata is written here. No strategy imports, DB opens, service changes,
source copying, deletion, or source-authority reconstruction.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from scripts.codex_capacity import preflight, storage_report, validate_roots

VERSION = "CODEX_CHAT_WORKSPACE_V1"
PROFILE_VERSION = "CODEX_CHAT_RUNTIME_V1"
MAX_CONTROL = 1024 * 1024
MAX_RECEIPT = 256 * 1024
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", re.ASCII)


class WorkspaceError(ValueError):
    pass


def _need(value, code):
    if not value:
        raise WorkspaceError(code)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _name(value):
    _need(isinstance(value, str) and NAME.fullmatch(value), "INVALID_RUNTIME_NAME")
    return value


def _stamp():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _directory_fd(path, create=False):
    """Walk from / using directory handles; never follow a redirecting ancestor."""
    _need(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "SAFE_CONTROL_IO_UNAVAILABLE")
    path = Path(path).expanduser().absolute()
    _need(".." not in path.parts, "CONTROL_PARENT_TRAVERSAL_FORBIDDEN")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _directory(path, create=False):
    with _directory_fd(path, create):
        pass
    return Path(path).expanduser().absolute()


def _file_stat(parent, name):
    return os.stat(name, dir_fd=parent, follow_symlinks=False)


def _read(path, limit=MAX_CONTROL):
    path = Path(path)
    with _directory_fd(path.parent) as parent:
        initial = _file_stat(parent, path.name)
        _need(stat.S_ISREG(initial.st_mode) and initial.st_nlink == 1,
              "CONTROL_FILE_TYPE_OR_LINKS")
        _need(not getattr(initial, "st_flags", 0) & 0x40000000,
              "NONRESIDENT_CONTROL_FORBIDDEN")
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            _need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
                  "CONTROL_FILE_TYPE_OR_LINKS")
            _need(before.st_size <= limit, "CONTROL_FILE_SIZE_LIMIT")
            _need(not getattr(before, "st_flags", 0) & 0x40000000,
                  "NONRESIDENT_CONTROL_FORBIDDEN")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(limit + 1)
            after = os.fstat(fd)
            current = _file_stat(parent, path.name)
            def fp(s):
                return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns
            _need(len(raw) <= limit and fp(initial) == fp(before) == fp(after) == fp(current),
                  "CONTROL_CHANGED_DURING_READ")
        finally:
            os.close(fd)
    def pairs(items):
        out = {}
        for key, value in items:
            _need(key not in out, "DUPLICATE_CONTROL_KEY")
            out[key] = value
        return out
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              WorkspaceError("NONFINITE_CONTROL")))
    except (UnicodeError, json.JSONDecodeError):
        raise WorkspaceError("INVALID_CONTROL_JSON") from None


def _write(path, value, *, replace=False):
    path = Path(path)
    raw = _canonical(value)
    _need(len(raw) <= MAX_CONTROL, "CONTROL_FILE_SIZE_LIMIT")
    with _directory_fd(path.parent, create=True) as parent:
        try:
            old = _file_stat(parent, path.name)
        except FileNotFoundError:
            old = None
        if old is not None:
            _need(replace and stat.S_ISREG(old.st_mode) and old.st_nlink == 1,
                  "CONTROL_EXISTS_PRESERVED")
        temp = "." + path.name + "." + uuid.uuid4().hex + ".tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                os.replace(temp, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            else:
                try:
                    os.link(temp, path.name, src_dir_fd=parent, dst_dir_fd=parent,
                            follow_symlinks=False)
                except FileExistsError:
                    raise WorkspaceError("CONTROL_EXISTS_PRESERVED") from None
                os.unlink(temp, dir_fd=parent)
            os.fsync(parent)
        finally:
            # Only our uncommitted, uniquely named metadata temporary file.
            try:
                os.unlink(temp, dir_fd=parent)
            except FileNotFoundError:
                pass


@contextmanager
def _lock(path):
    import fcntl
    path = Path(path)
    with _directory_fd(path.parent, create=True) as parent:
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            try:
                fd = os.open(path.name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
            except FileExistsError:
                fd = os.open(path.name, flags, dir_fd=parent)
        except OSError:
            # Filesystem creation/removal races never authorize business work.
            raise WorkspaceError("RUNTIME_LOCK_UNAVAILABLE_NO_ACTION") from None
        locked = False
        try:
            value = os.fstat(fd)
            _need(stat.S_ISREG(value.st_mode) and value.st_nlink == 1,
                  "LOCK_FILE_TYPE_OR_LINKS")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise WorkspaceError("RUNTIME_BUSY_FOLLOW_EXISTING_OPERATION") from None
            locked = True
            current = _file_stat(parent, path.name)
            _need((current.st_dev, current.st_ino) == (value.st_dev, value.st_ino),
                  "LOCK_PHYSICAL_IDENTITY_CHANGED")
            yield
        finally:
            if locked:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def default_registry_root():
    explicit = os.environ.get("STOCKFISH_CHAT_STATE")
    if explicit:
        return Path(explicit).expanduser().absolute()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "stockfish" / "codex-chat"


def _existing(value, *, directory):
    _need(isinstance(value, str) and Path(value).expanduser().is_absolute(),
          "ABSOLUTE_RUNTIME_PATH_REQUIRED")
    try:
        path = Path(value).expanduser().resolve(strict=True)
        info = path.stat()
    except OSError:
        raise WorkspaceError("RUNTIME_PATH_UNAVAILABLE") from None
    _need(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
          "RUNTIME_PATH_TYPE")
    _need(not getattr(info, "st_flags", 0) & 0x40000000,
          "NONRESIDENT_RUNTIME_PATH")
    return str(path)


def _under(path, root):
    return path == root or root in path.parents


def validate_profile(value):
    _need(isinstance(value, dict), "RUNTIME_PROFILE_OBJECT_REQUIRED")
    common = {"schema_version", "name", "backend", "state_root", "report_root", "storage"}
    backend = value.get("backend")
    extra = {"base_url", "db_path", "env_file"} if backend == "api" else {
        "source_root", "python", "config"} if backend == "premarket" else set()
    _need(backend in {"api", "premarket"} and
          common <= set(value) <= common | extra, "RUNTIME_PROFILE_FIELDS")
    _need(value["schema_version"] == PROFILE_VERSION, "RUNTIME_PROFILE_VERSION")
    result = deepcopy(value)
    _name(result["name"])
    for key in ("state_root", "report_root"):
        result[key] = _existing(result[key], directory=True)
    if backend == "api":
        from urllib.parse import urlsplit
        _need({"base_url", "db_path"} <= set(result), "API_LOCATOR_REQUIRED")
        try:
            parts = urlsplit(result["base_url"])
            valid = (parts.scheme == "http" and parts.hostname in {"127.0.0.1", "::1"}
                     and not parts.username and not parts.password
                     and parts.path in {"", "/"} and not parts.query and not parts.fragment
                     and (parts.port is None or 1 <= parts.port <= 65535))
        except (TypeError, ValueError):
            valid = False
        _need(valid, "LOCAL_LOOPBACK_HTTP_URL_REQUIRED")
        result["base_url"] = result["base_url"].rstrip("/")
        result["db_path"] = _existing(result["db_path"], directory=False)
        if result.get("env_file") is not None:
            result["env_file"] = _existing(result["env_file"], directory=False)
    else:
        _need({"source_root", "python", "config"} <= set(result), "PREMARKET_LOCATOR_REQUIRED")
        result["source_root"] = _existing(result["source_root"], directory=True)
        _existing(result["python"], directory=False)
        # A venv's python is commonly a symlink. Invoke its original path so
        # Python finds pyvenv.cfg; bind the resolved executable separately.
        invocation = Path(result["python"]).expanduser().absolute()
        result["python"] = str(invocation.parent.resolve(strict=True) / invocation.name)
        result["config"] = _existing(result["config"], directory=False)
    policy = result["storage"]
    required = {"roots", "target_root", "expected_growth_bytes", "min_free_bytes", "max_managed_bytes"}
    _need(isinstance(policy, dict) and required <= set(policy) <= required | {"max_entries", "max_depth"},
          "STORAGE_POLICY_FIELDS")
    for name in required - {"roots", "target_root"}:
        _need(type(policy[name]) is int and policy[name] >= 0, "STORAGE_POLICY_INTEGER_REQUIRED")
    _need(policy["max_managed_bytes"] > 0, "STORAGE_POLICY_POSITIVE_LIMIT_REQUIRED")
    for key in ("max_entries", "max_depth"):
        if key in policy:
            _need(type(policy[key]) is int and policy[key] > 0, "STORAGE_POLICY_INTEGER_REQUIRED")
    # Validate without opening any business files or making directories.
    validate_roots(policy["roots"])
    roots = [Path(r["path"]).expanduser().absolute() for r in policy["roots"]]
    for path in (Path(result["state_root"]), Path(result["report_root"])):
        _need(any(_under(path, root) for root in roots), "RUNTIME_PATH_OUTSIDE_STORAGE_POLICY")
    if backend == "api":
        _need(any(_under(Path(result["db_path"]), root) for root in roots),
              "DATABASE_OUTSIDE_STORAGE_POLICY")
    _need(policy["target_root"] in {r["name"] for r in policy["roots"]},
          "STORAGE_TARGET_ROOT_UNKNOWN")
    for name in ("state_root", "report_root") + (("db_path",) if backend == "api" else ()):
        target = next(r for r in policy["roots"] if _under(Path(result[name]), Path(r["path"])))
        _need(target["role"] != "protected", "RUNTIME_WRITE_DESTINATION_PROTECTED")
    return result


def interpreter_path(value):
    """Canonicalize the directory while preserving a virtualenv executable name."""
    path = Path(value).expanduser().absolute()
    return path.parent.resolve(strict=True) / path.name


def _binding(profile):
    """Check usable locators and snapshot metadata for legacy journal naming.

    Saved device/inode values are historical coordination keys, not a condition
    for opening an existing runtime on this machine.
    """
    names = ["state_root", "report_root"]
    names += ["db_path"] if profile["backend"] == "api" else ["source_root", "python"]
    output = {}
    for name in names:
        path = Path(profile[name])
        if name == "python":
            _need(path.parent.resolve(strict=True) == path.parent, "REGISTERED_PYTHON_PARENT_ALIAS_CHANGED")
            target = path.resolve(strict=True)
            value = target.lstat()
            _need(stat.S_ISREG(value.st_mode), "REGISTERED_PYTHON_TARGET_TYPE_CHANGED")
            output[name] = {"path": str(path), "target": str(target), "device": value.st_dev,
                            "inode": value.st_ino, "kind": "file"}
            continue
        _need(path.resolve(strict=True) == path, "REGISTERED_RUNTIME_PATH_ALIAS_CHANGED")
        value = path.lstat()
        _need(not stat.S_ISLNK(value.st_mode) and
              (stat.S_ISDIR(value.st_mode) if name.endswith("root") else stat.S_ISREG(value.st_mode)),
              "REGISTERED_RUNTIME_PATH_TYPE_CHANGED")
        output[name] = {"path": str(path), "device": value.st_dev, "inode": value.st_ino,
                        "kind": "directory" if stat.S_ISDIR(value.st_mode) else "file"}
    for root in profile["storage"]["roots"]:
        path = Path(root["path"])
        _need(path.resolve(strict=True) == path, "REGISTERED_STORAGE_ROOT_ALIAS_CHANGED")
        value = path.lstat()
        _need(stat.S_ISDIR(value.st_mode), "REGISTERED_STORAGE_ROOT_TYPE_CHANGED")
        output["storage:" + root["name"]] = {"path": str(path), "device": value.st_dev,
            "inode": value.st_ino, "kind": "directory"}
    return output


def _resources(binding):
    # Keep historical lock names so existing runtimes and receipts stay together.
    return sorted({_digest({"device": value["device"], "inode": value["inode"]})
                   for name, value in binding.items()
                   if name in {"state_root", "report_root", "db_path"} or name.startswith("storage:")})


def _resource_paths(profile):
    paths = [profile[name] for name in ("state_root", "report_root", "db_path") if name in profile]
    paths.extend(root["path"] for root in profile["storage"]["roots"])
    return paths


def _path_resources(profile):
    """Coordinate the configured locations across file replacement and epochs."""
    paths = _resource_paths(profile)
    return sorted({_digest({"resource_path": str(Path(path).resolve(strict=True))}) for path in paths})


def _same_location(left, right):
    if left == right:
        return True
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _registered_ledger_binding(root, profile, binding):
    """A new name for an existing resource must find its original operation log.

    This is location lookup, not physical authentication. A file restored at the
    same path inherits its saved journal key instead of appearing as fresh work.
    Multiple historical journals require reconciliation, never an arbitrary pick.
    """
    primary = "db_path" if profile["backend"] == "api" else "state_root"
    paths = sorted((root / "runtimes").glob("*.json"))
    _need(len(paths) <= 10000, "RUNTIME_REGISTRATION_SCAN_LIMIT")
    candidates = []
    policy = {"report_root": profile["report_root"], "storage": profile["storage"]}
    locations = _resource_paths(profile)
    for path in paths:
        saved = _read(path)
        previous = saved.get("profile", {})
        old_binding = saved.get("binding", {})
        # Legacy registrations predate path-policy claims. Compare their saved
        # policies too, so a remount cannot create a second budget for one root.
        if any(_same_location(left, right) for left in locations for right in _resource_paths(previous)):
            _need(policy == {"report_root": previous["report_root"], "storage": previous["storage"]},
                  "SHARED_RESOURCE_POLICY_CONFLICT")
        if previous.get("backend") != profile["backend"] or primary not in old_binding:
            continue
        if _same_location(profile[primary], previous.get(primary, "")):
            candidates.append(old_binding)
    if not candidates:
        return binding
    ledger_keys = {(item[primary]["device"], item[primary]["inode"]) for item in candidates}
    _need(len(ledger_keys) == 1, "EXISTING_OPERATION_LEDGERS_REQUIRE_RECONCILIATION")
    result = deepcopy(binding)
    for name, current in result.items():
        for previous in candidates:
            old = previous.get(name)
            if old and _same_location(current["path"], old["path"]):
                current.update(device=old["device"], inode=old["inode"])
                break
    return result


def register_profile(config_path, registry_root=None, make_default=False):
    profile = validate_profile(_read(Path(config_path).expanduser().resolve(strict=True)))
    root = Path(registry_root or default_registry_root()).expanduser().absolute()
    _directory(root, create=True)
    # Metadata control must not live beneath a source/config directory that is
    # fingerprinted as a historical producer. A separate private root is used.
    if profile["backend"] == "premarket":
        _need(not _under(root, Path(profile["source_root"])), "REGISTRY_INSIDE_SOURCE_FORBIDDEN")
    with _lock(root / "registry.lock"):
        destination = root / "runtimes" / (profile["name"] + ".json")
        binding = _binding(profile)
        existing = destination.exists() or destination.is_symlink()
        if existing:
            saved = _read(destination)
            _need(saved.get("schema_version") == VERSION and saved.get("profile") == profile,
                  "REGISTERED_RUNTIME_EXISTS_PRESERVED")
            binding = saved["binding"]
        else:
            binding = _registered_ledger_binding(root, profile, binding)
        resources = sorted(set(_resources(binding)) | set(_path_resources(profile)))
        policy = {"report_root": profile["report_root"], "storage": profile["storage"]}
        for resource in resources:
            claim = root / "resources" / (resource + ".json")
            if claim.exists() or claim.is_symlink():
                _need(_read(claim) == policy, "SHARED_RESOURCE_POLICY_CONFLICT")
        for resource in resources:
            claim = root / "resources" / (resource + ".json")
            if not claim.exists():
                _write(claim, policy)
        if not existing:
            _write(destination, {"schema_version": VERSION, "profile": profile,
                                 "binding": binding, "registered_at": _stamp(),
                                 "authority": "LOCATION_ONLY_NOT_SOURCE_AUTHORITY"})
        if make_default:
            default = root / "default.json"
            wanted = {"schema_version": VERSION, "name": profile["name"]}
            if default.exists() or default.is_symlink():
                _need(_read(default) == wanted, "DEFAULT_RUNTIME_EXISTS_PRESERVED")
            else:
                _write(default, wanted)
    return {"status": "REGISTERED", "name": profile["name"], "backend": profile["backend"],
            "registry_root": str(root), "default": bool(make_default),
            "authority": "LOCATION_ONLY_NOT_SOURCE_AUTHORITY", "data_copied_bytes": 0}


def load_runtime(name=None, registry_root=None, *, allow_test_fixture=False):
    root = Path(registry_root or default_registry_root()).expanduser().absolute()
    name = name or os.environ.get("STOCKFISH_CHAT_RUNTIME")
    if name is None:
        default = root / "default.json"
        if not default.exists() and not default.is_symlink():
            return None
        value = _read(default)
        _need(value.get("schema_version") == VERSION, "DEFAULT_RUNTIME_SCHEMA")
        name = value.get("name")
    _name(name)
    path = root / "runtimes" / (name + ".json")
    if not path.exists() and not path.is_symlink():
        raise WorkspaceError("RUNTIME_NOT_REGISTERED")
    saved = _read(path)
    _need(saved.get("schema_version") in {VERSION, "CODEX_RUNTIME_RECOVERY_V1"}, "RUNTIME_REGISTRY_VERSION")
    profile = validate_profile(saved.get("profile"))
    _need(profile["name"] == name, "RUNTIME_NAME_MISMATCH")
    runtime = Runtime(root, profile, saved.get("binding"))
    if saved["schema_version"] == "CODEX_RUNTIME_RECOVERY_V1":
        from stockfish_core.file_integrity import content_fingerprint, verify_content_reference
        reference = saved.get("epoch", {})
        _need(verify_content_reference(reference), "RECOVERY_EPOCH_CONTENT_CHANGED")
        plan = _read(Path(reference["path"]), limit=16 * MAX_CONTROL)
        _need(plan.get("schema_version") == "CODEX_RUNTIME_RECOVERY_V1"
              and plan.get("registry_root") == str(root)
              and plan.get("profile") == profile and plan.get("binding") == runtime.binding
              and plan.get("epoch_id") == "runtime_recovery_" + _digest({k: v for k, v in plan.items() if k != "epoch_id"})
              and plan.get("source_authority_restored") is False,
              "RECOVERY_REGISTERED_EPOCH_MISMATCH")
        _need(not plan.get("test_fixture") or allow_test_fixture, "RECOVERY_TEST_FIXTURE_NOT_PRODUCTION")
        history = plan["history"]
        parent_scope = history["scope"]
        directory = root / "scopes" / _digest(parent_scope) / "operations"
        _need(str(directory) == history["directory"], "RECOVERY_ANCESTOR_LEDGER_LOCATION_CHANGED")
        runtime.ancestor_ledgers = [(directory, parent_scope)]
        runtime.control_references = [dict(path=str(path), **content_fingerprint(path)), reference,
            plan["config"], history["registry"], history["config"],
            *([history["default"]] if history.get("default") else []),
            *[item["reference"] for item in history["operations"]]]
        runtime.resources = sorted(set(runtime.resources) | set(history["resources"]))
        runtime.recovery_epoch = plan["epoch_id"]
    runtime.check_binding()
    return runtime


class Runtime:
    def __init__(self, registry_root, profile, binding):
        self.registry_root = Path(registry_root)
        self.profile = deepcopy(profile)
        self.binding = deepcopy(binding)
        self.control_references = []
        self.ancestor_ledgers = []
        self.recovery_epoch = None
        # Saved keys locate the original journal even after filesystem metadata
        # changes. Never recompute them from today's device/inode.
        primary = binding["db_path" if profile["backend"] == "api" else "state_root"]
        self.scope = {"backend": profile["backend"], "device": primary["device"], "inode": primary["inode"]}
        self.resources = sorted(set(_resources(binding)) | set(_path_resources(profile)))
        self.directory = self.registry_root / "scopes" / _digest(self.scope)

    def check_binding(self):
        # Paths must remain usable, but machine/file identity is not admission.
        _binding(self.profile)
        if self.control_references:
            from stockfish_core.file_integrity import verify_content_reference
            _need(all(verify_content_reference(ref) for ref in self.control_references),
                  "RECOVERY_LEDGER_OR_CONFIGURATION_CONTENT_CHANGED")

    def capture_recovery_context(self):
        """Locate existing completed recoveries in this exact durable scope.

        References are read-only control locators, not retry authorization.
        The capture reader independently authenticates each referenced recovery
        and its old/new originals before it may recognize an existing result.
        No registry/profile configuration, source or operation is written.
        """
        from stockfish_core.file_integrity import content_fingerprint, verify_content_reference
        self.check_binding()
        directory = Path(self.directory)
        _need(directory.is_absolute() and directory.name == _digest(self.scope),
              "CAPTURE_RECOVERY_SCOPE_DIRECTORY_BINDING")
        root = directory / "explicit-failure-recoveries"
        if not root.exists() and not root.is_symlink():
            return None
        _need(directory.resolve(strict=True) == directory and root.resolve(strict=True) == root,
              "CAPTURE_RECOVERY_SCOPE_ALIAS_FORBIDDEN")
        with _directory_fd(root):
            operations = sorted(root.iterdir())
        _need(len(operations) <= 10000, "CAPTURE_RECOVERY_OPERATION_COUNT_BOUND")
        references = []
        for operation in operations:
            _need(re.fullmatch(r"[0-9a-f]{64}", operation.name) is not None,
                  "CAPTURE_RECOVERY_OPERATION_ID_REQUIRED")
            with _directory_fd(operation):
                path = operation / "completion.json"
                if not path.exists() and not path.is_symlink():
                    continue
                _need(len(references) < 64, "CAPTURE_RECOVERY_COMPLETION_COUNT_BOUND")
                # Read through the original bounded no-follow control reader,
                # then bind the precise bytes passed to the capture helper.
                _read(path, limit=2 * MAX_CONTROL)
                reference = dict(path=str(path), **content_fingerprint(path))
                _read(path, limit=2 * MAX_CONTROL)
                verify_content_reference(reference)
                references.append(reference)
        self.check_binding()
        return dict(scope=deepcopy(self.scope), scope_directory=str(directory),
                    completions=references)

    def guard_ancestor_request(self, identity):
        for directory, scope in getattr(self, "ancestor_ledgers", ()) :
            _need(not directory.is_symlink(), "RECOVERY_ANCESTOR_LEDGER_ALIAS")
            paths = sorted(directory.glob("*.json"))
            _need(len(paths) <= 10000, "RECOVERY_ANCESTOR_OPERATION_LIMIT")
            for path in paths:
                record = _read(path)
                other = record.get("identity", {})
                _need(record.get("scope") == scope and record.get("operation_id") == path.stem
                      and path.stem == _digest({"scope": scope, "identity": other}),
                      "RECOVERY_ANCESTOR_OPERATION_INVALID")
                same_key = bool(identity.get("request_key")) and identity.get("request_key") == other.get("request_key")
                same_session = (identity.get("backend") == other.get("backend") == "premarket"
                    and bool(identity.get("observation_date")) and bool(identity.get("target_session"))
                    and all(identity.get(k) == other.get(k) for k in ("observation_date", "target_session")))
                same_local = (identity.get("backend") == other.get("backend") == "local-research"
                    and bool(identity.get("input_id"))
                    and all(identity.get(k) == other.get(k) for k in ("database", "command", "input_id", "run_id")))
                same_outcome = (identity.get("backend") == other.get("backend") == "local-outcomes"
                    and bool(identity.get("business")) and identity["business"] == other.get("business"))
                _need(other != identity and not same_key and not same_session and not same_local and not same_outcome,
                      "RECOVERY_ANCESTOR_REQUEST_RECONCILE_NO_RETRY")

    @contextmanager
    def exclusive(self):
        self.check_binding()
        with ExitStack() as stack:
            for resource in self.resources:
                stack.enter_context(_lock(self.registry_root / "resources" / (resource + ".lock")))
            self.check_binding()
            yield

    def storage_report(self):
        options = {k: v for k, v in self.profile["storage"].items() if k in {"max_entries", "max_depth"}}
        return storage_report(self.profile["storage"]["roots"], **options)

    def preflight(self, expected_growth_bytes=None, destination=None):
        self.check_binding()
        policy = deepcopy(self.profile["storage"])
        if expected_growth_bytes is not None:
            policy["expected_growth_bytes"] = expected_growth_bytes
        locations = [Path(destination).absolute()] if destination is not None else [
            Path(self.profile["state_root"]), Path(self.profile["report_root"])] + (
                [Path(self.profile["db_path"]).parent] if self.profile["backend"] == "api" else [])
        targets = set()
        for location in locations:
            _need(location.resolve() == location, "WRITE_DESTINATION_ALIAS_FORBIDDEN")
            candidates = [r for r in policy["roots"] if _under(location, Path(r["path"]))]
            _need(len(candidates) == 1, "WRITE_DESTINATION_OUTSIDE_STORAGE_POLICY")
            targets.add(candidates[0]["name"])
        checks = [preflight(**{**policy, "target_root": target}) for target in sorted(targets)]
        result = checks[0]
        result["allowed"] = all(c["allowed"] for c in checks)
        result["reason_codes"] = sorted({reason for c in checks for reason in c["reason_codes"]})
        result["destination_checks"] = [{k: c[k] for k in (
            "target_root", "allowed", "projected_free_bytes", "reason_codes")} for c in checks]
        return result

    @staticmethod
    def _validated(validate, receipt):
        result = validate(receipt)
        _need(result is True or (isinstance(result, dict) and result.get("verified") is True),
              "OPERATION_RECEIPT_VALIDATION_FAILED")
        result = {"verified": True} if result is True else result
        _need(len(_canonical(result)) <= MAX_RECEIPT, "VALIDATION_METADATA_TOO_LARGE")
        return result

    @staticmethod
    def _capacity_summary(value):
        return {key: value[key] for key in (
            "allowed", "reason_codes", "expected_growth_bytes", "min_free_bytes",
            "max_managed_bytes", "projected_unique_logical_bytes", "destination_checks")}

    def run_once(self, identity, action, validate):
        _need(isinstance(identity, dict) and identity, "OPERATION_IDENTITY_REQUIRED")
        _need(callable(action) and callable(validate), "OPERATION_CALLBACKS_REQUIRED")
        # A finite, metadata-only identity: never store whole inputs or API keys.
        encoded = _canonical(identity)
        _need(len(encoded) <= 16384, "OPERATION_IDENTITY_TOO_LARGE")
        identity = json.loads(encoded)
        key = _digest({"scope": self.scope, "identity": identity})
        path = self.directory / "operations" / (key + ".json")
        started = time.monotonic()
        with self.exclusive():
            Runtime.guard_ancestor_request(self, identity)
            if path.exists() or path.is_symlink():
                record = _read(path)
                _need(record.get("schema_version") == VERSION and record.get("scope") == self.scope
                      and record.get("identity") == identity and record.get("operation_id") == key,
                      "OPERATION_RECORD_IDENTITY_MISMATCH")
                _need(record.get("phase") == "RECORDED", "OPERATION_STATE_UNKNOWN_RECONCILE_NO_RETRY")
                receipt = deepcopy(record["receipt"])
                validation = self._validated(validate, receipt)
                self.check_binding()
                return {"status": "REUSED", "operation_id": key, "reused": True,
                        "receipt": receipt, "validation": validation, "elapsed_seconds": time.monotonic() - started,
                        "original_action_seconds": record["action_seconds"],
                        "new_data_copy_bytes": 0, "new_action_calls": 0,
                        "note": "Original operation retained; reuse is not a new capture or source certification."}
            capacity = self.preflight()
            _need(capacity["allowed"], "CAPACITY_PREFLIGHT_BLOCKED")
            record = {"schema_version": VERSION, "scope": self.scope, "identity": identity,
                      "operation_id": key, "phase": "SUBMITTING", "started_at": _stamp(),
                      "capacity_before": self._capacity_summary(capacity)}
            _write(path, record)
            action_started = time.monotonic()
            try:
                receipt = action()
                _need(isinstance(receipt, dict) and len(_canonical(receipt)) <= MAX_RECEIPT,
                      "OPERATION_RECEIPT_TOO_LARGE_OR_INVALID")
                validation = self._validated(validate, receipt)
                self.check_binding()
                elapsed = time.monotonic() - action_started
                after = self.preflight(expected_growth_bytes=0)
                record.update(phase="RECORDED", receipt=receipt, completed_at=_stamp(),
                              action_seconds=elapsed, capacity_after=self._capacity_summary(after))
                _write(path, record, replace=True)
            except BaseException:
                # Error text / provider payloads may contain credentials. Keep
                # only a fixed code, and never clear an uncertain submit.
                record.update(phase="FAILED_OR_UNKNOWN", finished_at=_stamp(),
                              reason="ACTION_DID_NOT_RETURN_VALIDATED_RECEIPT")
                _write(path, record, replace=True)
                raise
            return {"status": "RECORDED", "operation_id": key, "reused": False,
                    "receipt": deepcopy(receipt), "validation": validation, "elapsed_seconds": time.monotonic() - started,
                    "action_seconds": elapsed, "new_action_calls": 1,
                    "capacity_after_allowed": after["allowed"]}


def runtime_status(name=None, registry_root=None, *, allow_test_fixture=False):
    runtime = load_runtime(name, registry_root, allow_test_fixture=allow_test_fixture)
    if runtime is None:
        return {"status": "UNCONFIGURED", "registry_root": str(registry_root or default_registry_root()),
                "note": "Register an explicitly identified existing runtime; no default service or database was opened."}
    return {"status": "REGISTERED_LOCATION", "name": runtime.profile["name"],
            "backend": runtime.profile["backend"], "state_root": runtime.profile["state_root"],
            "report_root": runtime.profile["report_root"], "scope": runtime.scope,
            "authority": "LOCATION_ONLY_NOT_SOURCE_AUTHORITY",
            "physical_binding_verified": False,
            "coordination_policy": "SAVED_LEDGER_KEYS_AND_LOCATION_LOCKS",
            "recovery_epoch": runtime.recovery_epoch, "ancestor_ledger_count": len(runtime.ancestor_ledgers)}
