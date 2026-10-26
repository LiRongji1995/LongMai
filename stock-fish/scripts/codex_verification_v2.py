"""Explicit V2 verification adapters; no persisted authority or mutable hash cache.

Old validators execute with their original code objects in a private globals
dictionary. No imported module is patched. The source files of both versions
must be included in the calling coordinator's execution identity.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import cProfile
import gzip
import io
from pathlib import Path
import sys
import time
from types import FunctionType, SimpleNamespace

VERSION = "CODEX_VERIFICATION_EXECUTION_V2"
MIB = 1024 ** 2
_ACTIVE = ContextVar("codex_verification_v2_metrics", default=None)


def bind_functions(module, names, replacements):
    """Reuse reviewed code without mutating its module or any original function."""
    namespace = dict(vars(module))
    namespace.update(replacements)
    for name in names:
        function = getattr(module, name)
        clone = FunctionType(function.__code__, namespace, function.__name__,
                             function.__defaults__, function.__closure__)
        clone.__kwdefaults__ = function.__kwdefaults__
        namespace[name] = clone
    return {name: namespace[name] for name in names}


def isolated_subprocess(module, script_path):
    """Private supervisor adapter: preserve venv path and reject cached bytecode.

    The original supervisor has already created its exclusive owned control
    directory. The new lookup path must not exist and -B keeps it absent.
    No environment-only setting is used: -I would ignore such a setting.
    """
    script_path = Path(script_path).resolve(strict=True)
    original_popen = module.Popen

    def popen(command, *args, **kwargs):
        from stockfish_core.yaogu.short_horizon_archive_bridge_v1 import _require
        _require(type(command) is list and len(command) > 4 and command[1:3] == ["-I", "-B"]
            and Path(command[0]).absolute() == Path(sys.executable).absolute()
            and Path(command[3]).absolute() == script_path and command.count("--worker-result") == 1,
            "V2_SUPERVISED_PYTHON_SCRIPT_IDENTITY")
        index = command.index("--worker-result")
        _require(index + 1 < len(command), "V2_WORKER_RESULT_REQUIRED")
        output = Path(command[index + 1])
        _require(output.is_absolute() and output.name == "result.json"
            and output.parent.resolve(strict=True) == output.parent and output.parent.is_dir(),
            "V2_OWNED_CONTROL_DIRECTORY_REQUIRED")
        lookup = output.parent / "empty-pycache-lookup"
        _require(not lookup.exists() and not lookup.is_symlink(), "V2_EMPTY_PYCACHE_LOOKUP_REQUIRED")
        command = command[:3] + ["-X", "pycache_prefix=" + str(lookup)] + command[3:]
        return original_popen(command, *args, **kwargs)

    result = SimpleNamespace(**vars(module))
    result.Popen = popen
    return result


class Metrics:
    def __init__(self):
        self.stages = {}

    @contextmanager
    def stage(self, name):
        started, cpu = time.monotonic(), time.process_time()
        row = self.stages.setdefault(name, dict(calls=0, wall_seconds=0.0,
            cpu_seconds=0.0, failures=0))
        row["calls"] += 1
        try:
            yield
        except BaseException:
            row["failures"] += 1
            raise
        finally:
            row["wall_seconds"] += time.monotonic() - started
            row["cpu_seconds"] += time.process_time() - cpu

    def count(self, name, amount):
        row = self.stages.setdefault(name, dict(calls=0, bytes=0))
        row["calls"] += 1
        row["bytes"] += amount

    def report(self):
        return dict(schema_version=VERSION, stages={key: dict(value) for key, value in self.stages.items()},
            timing_role="INCLUSIVE_STAGES_MAY_OVERLAP_DO_NOT_SUM",
            bytes_role="PYTHON_RETURNED_BYTES_NOT_KERNEL_IO_OR_NETWORK",
            cached_source_authority=False)


@contextmanager
def metrics_scope():
    metrics = Metrics()
    token = _ACTIVE.set(metrics)
    try:
        yield metrics
    finally:
        _ACTIVE.reset(token)


@contextmanager
def stage(name):
    metrics = _ACTIVE.get()
    if metrics is None:
        yield
    else:
        with metrics.stage(name):
            yield


def timed(name, function):
    def call(*args, **kwargs):
        with stage(name):
            return function(*args, **kwargs)
    return call


def decompress_document(raw, session, *, charge=None, maximum=256 * MIB):
    """Exact old JSON rejection semantics with one growing decompressed buffer.

    json.loads accepts bytearray directly. There is no list of all chunks and
    no joined full bytes copy; the decoded Unicode text and JSON object still
    allocate, so this is explicitly not a constant-memory JSON parser.
    """
    from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
    with stage("gzip_decode_json"):
        data = bytearray()
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as stream:
            while True:
                session.check()
                part = stream.read(min(MIB, maximum - len(data) + 1))
                if not part:
                    break
                bridge._require(len(data) + len(part) <= maximum,
                                "DECOMPRESSED_DOCUMENT_BUDGET_EXCEEDED")
                if charge is not None:
                    charge(len(part))
                data.extend(part)
        session.check()
        metrics = _ACTIVE.get()
        if metrics is not None:
            metrics.count("decompressed_json_bytes", len(data))
        value = bridge._json(data)
        session.check()
        return value


def trio_with_clock(session, item, *args, **kwargs):
    """Run every frozen trio assertion, retaining only its decoded RAW clock.

    The private reader tags the exact RAW bytes by object identity only for the
    duration of the original immediate decoder call. Nothing is cached between
    files, calls, terminal checks or requests.
    """
    from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
    pending, projection = {}, {}

    class Reader:
        def __getattr__(self, name):
            return getattr(session, name)

        def read(self, path, *read_args, **read_kwargs):
            raw = session.read(path, *read_args, **read_kwargs)
            if str(path) == item["raw"]["path"]:
                bridge._require(not pending, "V2_RAW_DECODER_ORDER")
                pending["raw"] = raw
            return raw

    def decode(raw):
        value = bridge._json(raw)
        if pending.get("raw") is raw:
            bridge._require(isinstance(value, dict), "V2_RAW_OBJECT_REQUIRED")
            projection.update(present="first_seen_at" in value, value=value.get("first_seen_at"))
            pending.clear()
        return value

    validator = bind_functions(bridge, ("_trio",), {"_json": decode})["_trio"]
    with stage("original_trio_with_clock"):
        result = validator(Reader(), item, *args, **kwargs)
    bridge._require(not pending and set(projection) == {"present", "value"}, "V2_RAW_CLOCK_NOT_CONSUMED")
    return (*result, projection)


def single_pass_archive_class(compact):
    """Only the new coordinator's semantic-validation reader uses this class.

    The original compact_owned still uses its unchanged storage validator and
    all original cleanup/terminal guards. No source file is ever removed here.
    """
    class SinglePassArchive(compact.CompactArchive):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._consumed = set()
            self._pending = False
            self._finished = False

        def verify_all(self):
            compact._need(not self._pending and not self._finished, "V2_VALIDATION_ONCE_REQUIRED")
            self._pending = True
            # This is only a sizing ticket. It is never a successful validation.
            return dict(status="PENDING_COMPLETE_SEMANTIC_CONSUMPTION",
                artifact_count=len(compact.CORE_FILES), logical_bytes=self._manifest["logical_bytes"],
                stored_bytes=self._manifest["stored_bytes"], source_capability_restored=False)

        def read_artifact(self, role, *, max_bytes):
            compact._need(self._pending and not self._finished, "V2_PENDING_CONSUMPTION_REQUIRED")
            with stage("compact_logical_consume"):
                raw = super().read_artifact(role, max_bytes=max_bytes)
            self._consumed.add(role)  # only after EOF + both complete hashes
            return raw

        def finish(self):
            compact._need(self._pending and not self._finished and self._consumed == compact.CORE_FILES,
                          "V2_COMPLETE_EIGHT_SEMANTIC_ROLES_REQUIRED")
            # Re-read every physical byte AFTER semantics. Matching the full
            # stored SHA proves the previously decoded logical bytes still
            # belong to the exact artifact, without another gzip expansion.
            with stage("compact_final_physical_sha"):
                references = [item["stored_reference"] for item in self._manifest["artifacts"].values()]
                references.append(self.reference)
                for reference in references:
                    maximum = self.meter.limits.manifest_bytes if reference is self.reference else self.meter.limits.stored_bytes
                    for _ in compact._chunks(reference["path"], self.meter, expected=reference, maximum=maximum):
                        pass
            self._finished = True
            return dict(verified=True, artifact_count=len(self._consumed), physical_terminal_sha=True)

    return SinglePassArchive


def outcome_diagnostics(function, *args, **kwargs):
    """Explicit caller-owned posterior diagnostic; never submits work itself.

    Call only around an independently authorized isolated plan/worker. The
    original function, budgets, exceptions and transaction semantics remain.
    cProfile records CPU and adds overhead; its results are not speed claims.
    The caller can retain failure diagnostics from ``error.verification_v2``.
    """
    selected = {"normalize_capture_with_report", "digest_market_input", "digest_json_stream",
                "_stored_matches", "terminal", "_reconstruct", "evaluate", "_authenticated"}
    profiler = cProfile.Profile(timer=time.process_time)
    started = time.monotonic()
    error = None
    try:
        result = profiler.runcall(function, *args, **kwargs)
    except BaseException as caught:
        error = caught
    rows = []
    for entry in profiler.getstats():
        code = entry.code
        if hasattr(code, "co_name") and code.co_name in selected:
            rows.append(dict(module=Path(code.co_filename).name, function=code.co_name,
                line=code.co_firstlineno, calls=entry.callcount,
                cpu_self_seconds=entry.inlinetime, cpu_inclusive_seconds=entry.totaltime))
    report = dict(schema_version=VERSION, wall_seconds=time.monotonic() - started,
        status="FAILED" if error is not None else "RETURNED", stages=sorted(rows, key=lambda row:(row["module"],row["line"])),
        diagnostic_role="CPROFILE_CPU_WITH_OVERHEAD_INCLUSIVE_TIMES_OVERLAP",
        execution_or_budget_changed=False)
    if error is not None:
        try:
            error.verification_v2 = report
        except (AttributeError, TypeError):
            pass
        raise error
    return result, report
