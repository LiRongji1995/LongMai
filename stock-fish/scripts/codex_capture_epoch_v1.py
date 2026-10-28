"""Explicit current recertification of original captures into a new metadata epoch.

Original bytes, recorded clocks, old indexes and failed attempts stay untouched.
A new epoch is not old physical continuity, a data refresh, or retry permission.
Every original trio passes the mature import_seed verifier; no network is used.
The caller owns shared-runtime coordination and total storage admission.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stockfish_core.yaogu import premarket_capture_cache_v1 as native
from stockfish_core.yaogu.premarket_runtime_v1 import Budget

VERSION = "CODEX_CAPTURE_CURRENT_RECERTIFICATION_EPOCH_V1"
MIB = 1024**2
DEFAULT_LIMITS = dict(max_seconds=900, capture_read_bytes=4*1024**3,
    cache_bytes=256*MIB, output_bytes=32*MIB, self_rss_bytes=1024*MIB)
MAX_ENTRIES = 65536
MAX_FILES = 100000


def _need(condition, reason):
    if not condition:
        raise ValueError(reason)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _directory(value):
    path = Path(value).expanduser().absolute()
    _need(".." not in path.parts and path.resolve(strict=True) == path
          and stat.S_ISDIR(path.lstat().st_mode), "EPOCH_CANONICAL_DIRECTORY_REQUIRED")
    return path


@contextmanager
def _source_lock(root):
    # The original cache writer uses this same flock. Read-only opening does
    # not update its contents or establish a replacement lock identity.
    _need(os.name == "posix", "EPOCH_READONLY_SOURCE_LOCK_REQUIRES_POSIX")
    import fcntl
    path = root / "writer.lock"
    before = native._fp(path)
    with path.open("rb") as stream:
        _need(os.fstat(stream.fileno()).st_ino == before["st_ino"], "EPOCH_LOCK_CHANGED")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("EPOCH_SOURCE_WRITER_ACTIVE") from None
        try:
            _need(native._fp(path) == before, "EPOCH_LOCK_CHANGED")
            yield
            _need(native._fp(path) == before, "EPOCH_LOCK_CHANGED")
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _tree(root, budget):
    values, pending = [], [(root, 0)]
    while pending:
        directory, depth = pending.pop()
        budget.check("EPOCH_SOURCE_INVENTORY")
        _need(depth <= 32, "EPOCH_SOURCE_DEPTH_LIMIT")
        info = directory.lstat()
        values.append(dict(path=str(directory), kind="directory",
            fp={key: int(getattr(info, key, 0)) for key in ("st_dev", "st_ino", "st_mode")}))
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                _need(len(values)+len(pending) < MAX_FILES, "EPOCH_SOURCE_FILE_LIMIT")
                path = Path(entry.path)
                info = entry.stat(follow_symlinks=False)
                _need(not getattr(info, "st_flags", 0) & 0x40000000,
                      "EPOCH_NONRESIDENT_SOURCE")
                if stat.S_ISDIR(info.st_mode):
                    pending.append((path, depth+1))
                elif stat.S_ISREG(info.st_mode):
                    values.append(dict(path=str(path), kind="file", fp=native._fp(path)))
                elif stat.S_ISLNK(info.st_mode):
                    values.append(dict(path=str(path), kind="symlink",
                        fp=native._fp(path, link=True), target=os.readlink(path)))
                else:
                    raise ValueError("EPOCH_SPECIAL_SOURCE_FILE")
    return sorted(values, key=lambda row: row["path"])


def _read_ref(declaration, budget, known, *, limit=16*MIB):
    _need(isinstance(declaration, dict) and isinstance(declaration.get("path"), str),
          "EPOCH_ORIGINAL_REFERENCE_REQUIRED")
    path = Path(declaration["path"])
    _need(path.is_absolute() and path.resolve(strict=True) == path,
          "EPOCH_ORIGINAL_CANONICAL_PATH_REQUIRED")
    expected = declaration.get("sha256")
    _need(isinstance(expected, str) and re.fullmatch("[0-9a-f]{64}", expected),
          "EPOCH_ORIGINAL_FULL_SHA_REQUIRED")
    if str(path) in known:
        ref = known[str(path)]
        _need(ref["sha256"] == expected and ("bytes" not in declaration or
              declaration["bytes"] == ref["bytes"]), "EPOCH_ORIGINAL_DECLARATION_CONFLICT")
        _need(native._fp(path) == ref["fp"], "EPOCH_SOURCE_CHANGED_DURING_PLAN")
        return ref
    raw, ref = budget.read(path, expected=expected, limit=limit)
    _need("bytes" not in declaration or declaration["bytes"] == len(raw),
          "EPOCH_ORIGINAL_SIZE_CHANGED")
    # The old fp is deliberately not asserted to remain current. The complete
    # immutable content and original path are re-authenticated in this epoch.
    known[str(path)] = ref
    return ref


def _plan(root, budget, fixture):
    for name in ("index", "raw", "raw-index", "locators"):
        _need(_directory(root/name) == root/name, "EPOCH_ORIGINAL_CACHE_REQUIRED")
    tree = _tree(root, budget)
    sources, recipes, indexes, known, old_keys = [], [], [], {}, set()
    for folder, raw_only in (("index", False), ("raw-index", True)):
        paths = sorted((root/folder).glob("*/*.json"))
        _need(len(paths) <= MAX_ENTRIES, "EPOCH_INDEX_COUNT_LIMIT")
        for path in paths:
            raw, index_ref = budget.read(path, limit=2*MIB)
            value = native._json(raw)
            _need(path.stem == hashlib.sha256(raw).hexdigest()
                  and value.get("schema_version") == native.VERSION
                  and value.get("request_key") == path.parent.name,
                  "EPOCH_ORIGINAL_INDEX_CONTENT_ADDRESS_CHANGED")
            _need(re.fullmatch("[0-9a-f]{64}", path.parent.name), "EPOCH_INVALID_REQUEST_KEY")
            item = dict(raw=_read_ref(value["raw"], budget, known),
                        receipt=_read_ref(value["receipt"], budget, known))
            if raw_only:
                item["typed_receipt"] = value["typed_receipt"]
                recipes.append(item)
            else:
                spec = native.RequestSpec(**value["request_spec"])
                _need(spec.key == value["request_key"], "EPOCH_ORIGINAL_REQUEST_CHANGED")
                item.update(result=_read_ref(value["result"], budget, known),
                    archive_root=str(_directory(value["archive_root"])),
                    producer_files=[_read_ref(ref, budget, known) for ref in value["producer_files"]])
                sources.append(item)
            old_keys.add(value["request_key"])
            indexes.append(dict(reference=index_ref, request_key=value["request_key"],
                receipt_id=value["receipt_id"], endpoint=value["endpoint"], status=value["status"],
                raw_only=raw_only))
    inherited = []
    for directory in sorted((root/"raw").iterdir()):
        _need(not directory.is_symlink() and directory.is_dir()
              and re.fullmatch("[0-9a-f]{64}", directory.name), "EPOCH_INVALID_ATTEMPT_DIRECTORY")
        if any(directory.iterdir()):
            inherited.append(dict(request_key=directory.name, source_attempt_directory=str(directory),
                indexed=directory.name in old_keys))
    # Current physical references are pinned before import. Legacy timestamps
    # and receipts stay as originally recorded, including missing/failed ones.
    return dict(schema_version=VERSION, source_cache=str(root), allow_test_fixture=fixture,
        source_tree=tree, source_indexes=indexes, sources=sources, raw_receipt_sources=recipes,
        original_references=list(known.values()), inherited_attempts=inherited,
        network_calls=0, source_authority_restored=False, historical_physical_continuity_restored=False,
        old_records_rewritten=False, new_market_data=False)


def _budget(limits):
    _need(limits is None or isinstance(limits, dict), "EPOCH_LIMITS_MAPPING_REQUIRED")
    selected = dict(DEFAULT_LIMITS, **(limits or {}))
    _need(selected.get("max_seconds") is not None, "EPOCH_FINITE_TIME_LIMIT_REQUIRED")
    return Budget(selected)


def plan(source_cache, *, limits=None, allow_test_fixture=False):
    _need(type(allow_test_fixture) is bool, "EPOCH_BOOLEAN_FIXTURE_POLICY_REQUIRED")
    root, budget = _directory(source_cache), _budget(limits)
    with _source_lock(root):
        value = _plan(root, budget, allow_test_fixture)
        _need(_tree(root, budget) == value["source_tree"], "EPOCH_SOURCE_TREE_CHANGED")
        value["plan_sha256"] = _digest(value)
    return value


def _create_epoch(source_cache, destination, *, expected_plan_sha256, limits=None,
                  allow_test_fixture=False, _owned):
    """Create one new metadata-only cache; caller admits capacity beforehand.

    Failure retains the destination and is never automatically retried. The
    original cache writer is locked throughout. Newly recorded physical refs
    are valid only after the complete original import and terminal checks.
    """
    _need(type(allow_test_fixture) is bool, "EPOCH_BOOLEAN_FIXTURE_POLICY_REQUIRED")
    _need(isinstance(expected_plan_sha256, str) and
          re.fullmatch("[0-9a-f]{64}", expected_plan_sha256), "EPOCH_EXPLICIT_PLAN_SHA_REQUIRED")
    root, budget = _directory(source_cache), _budget(limits)
    target = Path(destination).expanduser().absolute()
    _need(".." not in target.parts and not target.exists() and not target.is_symlink(),
          "EPOCH_NEW_DESTINATION_REQUIRED")
    _need(_directory(target.parent) == target.parent and not target.is_relative_to(root)
          and not root.is_relative_to(target), "EPOCH_INDEPENDENT_CANONICAL_DESTINATION_REQUIRED")
    with _source_lock(root):
        value = _plan(root, budget, allow_test_fixture)
        _need(_digest(value) == expected_plan_sha256, "EPOCH_PLAN_CHANGED")
        target.mkdir()
        _owned.append(target)
        def save(path, obj):
            with path.open("xb") as stream:
                stream.write(_canonical(obj)); stream.flush(); os.fsync(stream.fileno())
        save(target/"epoch-claim.json", dict(schema_version=VERSION,
            plan_sha256=expected_plan_sha256, source_cache=str(root),
            actual_started_at=datetime.now(timezone.utc).isoformat(), status="STARTED_NO_IMPLICIT_RETRY"))
        budget.cache_dir = target
        cache = native.CaptureCache(target, budget, config=dict(offline=True,
            retry_failed=False, allow_test_fixture=allow_test_fixture))
        seed = target/"epoch-seed.json"
        save(seed, dict(schema_version=VERSION, sources=value["sources"],
                       raw_receipt_sources=value["raw_receipt_sources"]))
        result = cache.import_seed(seed, hashlib.sha256(seed.read_bytes()).hexdigest())
        for attempt in value["inherited_attempts"]:
            folder = target/"raw"/attempt["request_key"]/"inherited-original-attempt"
            folder.mkdir(parents=True)
            save(folder/"request-claim.json", dict(schema_version=VERSION, **attempt,
                source_plan_sha256=expected_plan_sha256,
                status="INHERITED_ORIGINAL_ATTEMPT_NO_IMPLICIT_RETRY"))
        # Importing calendar sources can use the mature importer's derived
        # business session. Record mappings rather than inventing old identity.
        new_indexes = list(cache._entries()) + list(cache._entries(raw_only=True))
        by_receipt = {}
        for row in new_indexes:
            cache.verify(row) if not row.get("raw_only") else cache._authenticate_recipe(row)
            by_receipt.setdefault(row["receipt_id"], []).append(row["request_key"])
        for ref in value["original_references"]:
            budget.read(ref["path"], expected=ref, limit=max(ref["bytes"], 1))
        for index in value["source_indexes"]:
            ref = index["reference"]
            budget.read(ref["path"], expected=ref, limit=2*MIB)
        _need(_tree(root, budget) == value["source_tree"], "EPOCH_SOURCE_TREE_CHANGED")
        cache.terminal()
        budget.check("EPOCH_COMPLETE", force=True)
        receipt = dict(schema_version=VERSION, status="CURRENT_ORIGINALS_RECERTIFIED_NEW_METADATA_EPOCH",
            source_cache=str(root), cache_root=str(target), plan_sha256=expected_plan_sha256,
            original_capture_count=result["captures"], raw_recipe_count=result["raw_recipes"],
            inherited_attempt_count=len(value["inherited_attempts"]),
            request_key_mappings=[dict(old_request_key=row["request_key"], receipt_id=row["receipt_id"],
                current_request_keys=by_receipt.get(row["receipt_id"], [])) for row in value["source_indexes"]],
            actual_completed_at=datetime.now(timezone.utc).isoformat(), network_calls=0,
            source_authority_restored=False, historical_physical_continuity_restored=False,
            old_records_rewritten=False, new_market_data=False, source_clocks_rewritten=False,
            metrics=budget.metrics)
        save(target/"epoch-complete.json", receipt)
        budget.check("EPOCH_FINAL_PUBLICATION", force=True)
        return receipt


def create_epoch(source_cache, destination, *, expected_plan_sha256, limits=None,
                 allow_test_fixture=False):
    """Publish only a successful new epoch, retaining failed publication evidence.

    The protected scope includes the final budget check and source-lock exit.
    A failure retracts this invocation's completion filename before recording
    FAILED; it never edits an existing epoch or retries an incomplete import.
    """
    owned = []
    try:
        return _create_epoch(source_cache, destination,
            expected_plan_sha256=expected_plan_sha256, limits=limits,
            allow_test_fixture=allow_test_fixture, _owned=owned)
    except BaseException as error:
        if owned:
            target = owned[0]
            complete = target / "epoch-complete.json"
            rejected = target / "epoch-rejected-completion.json"
            try:
                if complete.exists() or complete.is_symlink():
                    _need(not complete.is_symlink() and not rejected.exists()
                          and not rejected.is_symlink(), "EPOCH_REJECTION_PATH_CHANGED")
                    # Keep the candidate bytes for audit, but revoke the only
                    # successful-completion path even if failure logging fails.
                    complete.rename(rejected)
                failure = dict(schema_version=VERSION,
                    status="FAILED_RETAINED_NO_IMPLICIT_RETRY",
                    completion_accepted=False, cache_root=str(target),
                    plan_sha256=expected_plan_sha256,
                    failed_at=datetime.now(timezone.utc).isoformat(),
                    error_type=type(error).__name__[:128],
                    reason="EPOCH_IMPORT_OR_FINAL_PUBLICATION_DID_NOT_COMPLETE",
                    rejected_completion="epoch-rejected-completion.json" if rejected.exists() else None,
                    source_authority_restored=False, old_records_rewritten=False)
                with (target / "epoch-failed.json").open("xb") as stream:
                    stream.write(_canonical(failure)); stream.flush(); os.fsync(stream.fileno())
                descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except BaseException as recording_error:
                error.add_note("EPOCH_FAILURE_RECORDING_FAILED:" + type(recording_error).__name__)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "create"))
    parser.add_argument("--source-cache", required=True)
    parser.add_argument("--destination")
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--limits-json", type=Path)
    parser.add_argument("--allow-test-fixture", action="store_true")
    args = parser.parse_args(argv)
    limits = native._json(args.limits_json.read_bytes()) if args.limits_json else None
    options = dict(limits=limits, allow_test_fixture=args.allow_test_fixture)
    if args.operation == "plan":
        result = plan(args.source_cache, **options)
    else:
        _need(args.destination is not None, "EPOCH_DESTINATION_REQUIRED")
        result = create_epoch(args.source_cache, args.destination,
            expected_plan_sha256=args.expected_plan_sha256, **options)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
