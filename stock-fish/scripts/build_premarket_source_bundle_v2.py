"""Create/verify an explicitly versioned source-only runtime; never run capture.

The manifest proves this fresh copy and its exact patch. It does NOT restore
an old file's physical identity, cache authority, or historical visibility.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import stat
from pathlib import PurePosixPath

VERSION = "PREMARKET_FACTOR_CHECKPOINT_SOURCE_BUNDLE_V2"
WORKER = "stockfish_core/yaogu/provider_requests_worker.py"
ORIGINAL_WORKER_SHA256 = "5300a98b9a9feec102c87364b19772e949644def477f24a47643f78c594683ec"
UNCHANGED = {
    "stockfish_core/yaogu/provider_normalization.py": "5b96d6f23591c5714b1e772f59504572012a3cfb02b8d354582ab36e9b10ab28",
    "stockfish_core/yaogu/provider_comparability.py": "82ec4f884da76bcc3fbd4f0a78399fb6d804bdd5ce2e128553f46118e8cda526",
    "stockfish_core/yaogu/contracts.py": "55e4f8f796914d1fe33f318b2f64ad2916cc6c9a3a3acc7f6d33bce51e25bef3",
    "stockfish_core/yaogu/rules.py": "b4a0f701df533d9a13afd795ea5ba9461b31d6710eb8f3a12a9fc3d20880815c",
    "stockfish_core/yaogu/provider_funds.py": "4a6154ace083d4b2124bcd7ed81ecf1311e3c0590691b2f9953d0ae458695870",
    "stockfish_core/yaogu/e2e_tracing.py": "85bd30c464e4769efe4c17afee8a332e930660009a9ce581801df8a33f3cb234",
    "stockfish_core/yaogu/provider_requests.py": "8d765a88db5187387064b551d87695bb9a7e3be849bd14818933c8681b0c11f1",
    "stockfish_core/yaogu/history_artifacts.py": "09c1778d7996add2b16ac42145f6b2d680b2cd71959d2430028eafb0768fd04d",
    "stockfish_core/yaogu/premarket_native_core_v1.py": "b5c8d79fd1f81e0165ac53d0dc1b734a97867e5e01e1849b4d2025106b130a32",
    "stockfish_core/yaogu/provider_budget.py": "ebafd7d94d2f94cc2703790eb499f515ac214e0056e8e5c31fc9181e1d457b71",
}
FP_KEYS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_flags")
MAX_FILE_BYTES = 16 * 1024**2
MAX_SOURCE_BYTES = 64 * 1024**2
MANIFEST = "source-bundle-manifest.json"
PATCH = "worker-change.patch"
PREIMAGE = "_source_provenance/provider_requests_worker.original.txt"
PUBLIC_RESOURCES = (
    "scripts/stockfish.py", "scripts/codex_research.py", "premarket", "premarket.ps1", "LICENSE",
    "config/r05_source_lock.json", "config/r05_strategy_experiments.yaml",
    "config/r05_completion_requirements.yaml", "config/yaogu_v1.defaults.yaml",
    "config/short_horizon_strength_r1.yaml", "config/short_horizon_strength_r2.yaml",
    "config/theme_evolution_r1.yaml", "config/theme_capital_trend_v1.yaml",
    "config/stage_aware_momentum_v2.yaml",
    "docs/SHORT_HORIZON_STRENGTH_R1.md", "docs/SHORT_HORIZON_STRENGTH_R2.md",
    "docs/THEME_EVOLUTION_R1.md", "docs/THEME_OBSERVATION_DIAGNOSTICS_V1.md",
    "docs/ECONOMIC_DIRECTION_EXPLANATION_V1.md", "docs/ECONOMIC_DIRECTION_ARCHIVED_DISPLAY_V1.md",
    "docs/SAVED_R2_COMPLETE_DELIVERY_V2.md",
)

# These are exact old-source fragments, not a fuzzy patch or arbitrary command.
HELPER = '''
FACTOR_PARTIAL_CHECKPOINT_CONTRACT = 'FACTOR_PARTIAL_CHECKPOINT_V1_NO_RESUME'


def _factor_partial_checkpoint(params, rows, security_statuses, sdk):
    """Snapshot only this fresh request; unattempted instruments stay unknown."""
    requested = list(params['codes'])
    if len(set(requested)) != len(requested):
        raise ValueError('FACTOR_CHECKPOINT_DUPLICATE_REQUEST_CODE')
    by_code = {}
    for status in security_statuses:
        code = status['code']
        if code not in requested or code in by_code:
            raise ValueError('FACTOR_CHECKPOINT_STATUS_IDENTITY')
        by_code[code] = dict(status)
    counts = {}
    for row in rows:
        code = row['code']
        if code not in by_code or by_code[code]['status'] != 'COMPLETE':
            raise ValueError('FACTOR_CHECKPOINT_ROW_WITHOUT_COMPLETE_FRAGMENT')
        counts[code] = counts.get(code, 0) + 1
    for code, status in by_code.items():
        if status['status'] == 'COMPLETE' and (status.get('cursor_exhausted') is not True
                or type(status.get('row_count')) is not int
                or status['row_count'] != counts.get(code, 0)):
            raise ValueError('FACTOR_CHECKPOINT_INCOMPLETE_FRAGMENT')
    statuses = [by_code[code] if code in by_code else dict(code=code,
        status='UNKNOWN', row_count=0, cursor_exhausted=False,
        reason='NOT_COMPLETED_BEFORE_CHECKPOINT') for code in requested]
    return dict(status='PARTIAL', rows=rows, pagination_complete=False,
        checkpoint_contract=FACTOR_PARTIAL_CHECKPOINT_CONTRACT,
        checkpoint_request=dict(endpoint='adjustment_factors', parameters=dict(params)),
        field_version=baostock_field_version(sdk), source_url='https://baostock.com/',
        coverage=dict(requested_codes=requested, returned_rows=len(rows),
            returned_codes=sorted(counts), cursor_exhausted=False,
            security_statuses=statuses),
        http_responses=[], first_seen_at=datetime.now(timezone.utc).isoformat())

'''

REPLACEMENTS = (
    ("\ndef _baostock_session_request(endpoint,params,partial_path=None):\n    import baostock as bs\n",
     HELPER + "\ndef _baostock_session_request(endpoint,params,partial_path=None):\n"
     "    # No old rows may receive this request's new clock. Even empty, corrupt,\n"
     "    # or dangling-symlink checkpoints require a separate source contract.\n"
     "    if endpoint == 'adjustment_factors' and partial_path and os.path.lexists(partial_path):\n"
     "        raise ValueError('FACTOR_CHECKPOINT_RESUME_NOT_AUTHORIZED_V1')\n"
     "    import baostock as bs\n"),
    ("            recovered=read_partial(partial_path) or {}\n"
     "            rows=list(recovered.get('rows',[]))\n"
     "            security_statuses=[r for r in recovered.get('coverage',{}).get('security_statuses',[]) if r.get('status')=='COMPLETE']\n"
     "            done={r['code'] for r in security_statuses}\n"
     "            for code in params['codes']:\n"
     "                if code in done:continue\n",
     "            rows=[]\n"
     "            for code in params['codes']:\n"),
    ("                write_partial(partial_path,dict(status='PARTIAL',rows=rows,pagination_complete=False,\n"
     "                    field_version=baostock_field_version(bs),\n"
     "                    source_url='https://baostock.com/',coverage={'security_statuses':security_statuses},http_responses=[]))\n",
     "                write_partial(partial_path,_factor_partial_checkpoint(params,rows,security_statuses,bs))\n"),
)


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def strict_json(raw):
    def pairs(items):
        result = dict(items)
        need(len(result) == len(items), "DUPLICATE_JSON_KEY")
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: need(False, "NONFINITE_JSON"))


def fingerprint(path):
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode), "REGULAR_FILE_REQUIRED")
    need(not getattr(info, "st_flags", 0) & 0x40000000, "RESIDENT_FILE_REQUIRED")
    return {key: getattr(info, key, 0) for key in FP_KEYS}


def read(path, limit=MAX_FILE_BYTES):
    path = Path(path).absolute()
    need(path.resolve(strict=True) == path, "CANONICAL_NON_SYMLINK_PATH_REQUIRED")
    before = fingerprint(path)
    need(before["st_size"] <= limit, "SOURCE_FILE_TOO_LARGE")
    # Pin the opened descriptor too: a path swap must not follow a symlink or
    # block on a substituted FIFO between lstat and open.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        need(stat.S_ISREG(opened.st_mode)
             and {key: getattr(opened, key, 0) for key in FP_KEYS} == before,
             "OPENED_SOURCE_IDENTITY_CHANGED")
        raw = handle.read(limit + 1)
        after_read = os.fstat(handle.fileno())
        need({key: getattr(after_read, key, 0) for key in FP_KEYS} == before,
             "OPENED_SOURCE_CHANGED_DURING_READ")
    need(len(raw) <= limit and fingerprint(path) == before, "SOURCE_FILE_CHANGED")
    return raw, dict(path=str(path), bytes=len(raw), sha256=sha(raw), fp=before)


def patch_worker(raw):
    need(sha(raw) == ORIGINAL_WORKER_SHA256, "ORIGINAL_WORKER_SHA256_MISMATCH")
    original = raw.decode("utf-8")
    changed = original
    for before, after in REPLACEMENTS:
        need(changed.count(before) == 1, "EXACT_WORKER_PATCH_CONTEXT_MISMATCH")
        changed = changed.replace(before, after, 1)
    compile(changed, WORKER, "exec")  # Syntax only; no product import or execution.
    diff = "".join(difflib.unified_diff(original.splitlines(True), changed.splitlines(True),
                                      fromfile="original/" + WORKER, tofile="bundle/" + WORKER))
    return changed.encode("utf-8"), diff.encode("utf-8")


def inventory(source_root):
    root = Path(source_root).absolute()
    need(root.resolve(strict=True) == root and root.is_dir(), "CANONICAL_SOURCE_ROOT_REQUIRED")
    package = root / "stockfish_core"
    need(package.is_dir() and not package.is_symlink(), "PACKAGE_REQUIRED")
    selected = {}
    for directory, directories, files in os.walk(package, followlinks=False):
        for name in directories:
            need(not Path(directory, name).is_symlink(), "SOURCE_DIRECTORY_SYMLINK")
        for name in files:
            path = Path(directory, name)
            if path.suffix in (".py", ".json"):
                selected[path.relative_to(root).as_posix()] = path
    for name in PUBLIC_RESOURCES:
        selected[name] = root / name
    selected["THIRD_PARTY_NOTICES.md"] = root.parent / "THIRD_PARTY_NOTICES.md"
    need({WORKER, *UNCHANGED, "stockfish_core/exchange_calendar_2026.json",
          "stockfish_core/provider_classification.json"} <= set(selected), "SOURCE_CLOSURE_MISSING")
    return selected


def write_new(path, raw, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    # Final permissions precede the physical fingerprint. Never chmod a source.
    path.chmod(0o555 if executable else 0o444)
    return read(path)[1]


def create(source_root, destination):
    root = Path(source_root).absolute()
    destination = Path(destination).absolute()
    need(destination.parent.resolve(strict=True) == destination.parent, "CANONICAL_DESTINATION_PARENT_REQUIRED")
    need(not destination.is_relative_to(root) and not root.is_relative_to(destination),
         "BUNDLE_MUST_BE_SEPARATE_FROM_SOURCE")
    need(not os.path.lexists(destination), "DESTINATION_ALREADY_EXISTS")
    selected = inventory(root)
    destination.mkdir(mode=0o700, exist_ok=False)
    references, source_refs, total, patch, preimage = {}, {}, 0, None, None
    for name, path in sorted(selected.items()):
        raw, source_refs[name] = read(path)
        total += len(raw)
        need(total <= MAX_SOURCE_BYTES, "SOURCE_BUNDLE_TOO_LARGE")
        if name in UNCHANGED:
            need(sha(raw) == UNCHANGED[name], "FROZEN_DEPENDENCY_MISMATCH:" + name)
        if name == WORKER:
            preimage = raw
            raw, patch = patch_worker(raw)
        references[name] = write_new(destination / name, raw, executable=name == "premarket")
    references[PATCH] = write_new(destination / PATCH, patch)
    references[PREIMAGE] = write_new(destination / PREIMAGE, preimage)
    need(inventory(root) == selected, "SOURCE_INVENTORY_CHANGED")
    for name, path in selected.items():
        need(read(path)[1] == source_refs[name], "SOURCE_CHANGED_DURING_BUILD:" + name)
    manifest = dict(schema_version=VERSION, created_at=datetime.now(timezone.utc).isoformat(),
        source_root=str(root), bundle_root=str(destination), builder_sha256=sha(Path(__file__).read_bytes()),
        original_worker_sha256=ORIGINAL_WORKER_SHA256,
        worker_sha256=references[WORKER]["sha256"], sources=source_refs, files=references,
        source_bytes=total, source_changed_during_creation=False, original_physical_owner_restored=False,
        source_reference_scope="CREATION_TIME_SOURCE_GUARD_ONLY_NOT_LIVE_DEPENDENCY",
        capture_executed=False, production_switched=False, dependency_install_executed=False,
        contract="FRESH_CHECKPOINT_ONLY; EXISTING_FACTOR_CHECKPOINT_ALWAYS_REJECTED_BEFORE_SDK",
        normalizer_hashes={k: v for k, v in UNCHANGED.items() if not k.endswith("provider_budget.py")})
    manifest_ref = write_new(destination / MANIFEST, canonical(manifest))
    verified = verify(destination, manifest_ref["sha256"])
    return dict(status="CREATED_AND_VERIFIED_NOT_EXECUTED", manifest=manifest_ref, verification=verified)


def relative_name(value):
    need(type(value) is str and value and "\\" not in value
         and not PurePosixPath(value).is_absolute()
         and all(part not in ("", ".", "..") for part in value.split("/"))
         and PurePosixPath(value).as_posix() == value, "SAFE_RELATIVE_BUNDLE_PATH_REQUIRED")
    return value


def verify(destination, manifest_sha256):
    """Verify the pinned snapshot, without re-opening or re-binding its origin."""
    destination = Path(destination).absolute()
    need(destination.resolve(strict=True) == destination, "CANONICAL_BUNDLE_ROOT_REQUIRED")
    raw, manifest_ref = read(destination / MANIFEST)
    need(type(manifest_sha256) is str and sha(raw) == manifest_sha256, "EXPLICIT_MANIFEST_SHA256_MISMATCH")
    manifest = strict_json(raw)
    need(manifest["schema_version"] == VERSION and manifest["bundle_root"] == str(destination),
         "BUNDLE_IDENTITY_MISMATCH")
    need(manifest["builder_sha256"] == sha(Path(__file__).read_bytes()), "BUILDER_IDENTITY_MISMATCH")
    need(manifest["source_reference_scope"] == "CREATION_TIME_SOURCE_GUARD_ONLY_NOT_LIVE_DEPENDENCY"
         and manifest["source_changed_during_creation"] is False
         and manifest["original_physical_owner_restored"] is False, "SOURCE_GUARD_SCOPE_MISMATCH")
    sources, files = manifest["sources"], manifest["files"]
    need(type(sources) is dict and type(files) is dict, "BUNDLE_MAPPING_REQUIRED")
    for name in set(sources) | set(files):
        relative_name(name)
    need(set(files) == set(sources) | {PATCH, PREIMAGE}, "BUNDLE_FILE_SET_MISMATCH")
    need({WORKER, *UNCHANGED, *PUBLIC_RESOURCES, "THIRD_PARTY_NOTICES.md",
          "stockfish_core/exchange_calendar_2026.json", "stockfish_core/provider_classification.json"}
         <= set(sources), "SOURCE_CLOSURE_MISSING")
    expected_files, actual, total = set(files) | {MANIFEST}, set(), 0
    for directory, directories, names in os.walk(destination, followlinks=False):
        for name in directories:
            need(not Path(directory, name).is_symlink(), "BUNDLE_DIRECTORY_SYMLINK")
        for name in names:
            actual.add(Path(directory, name).relative_to(destination).as_posix())
    need(actual == expected_files, "UNREGISTERED_OR_MISSING_BUNDLE_FILE")
    for name, expected in files.items():
        need(expected["path"] == str(destination / name), "BUNDLE_FILE_PATH_MISMATCH")
        raw, reference = read(destination / name)
        total += len(raw)
        need(total <= MAX_SOURCE_BYTES, "SOURCE_BUNDLE_TOO_LARGE")
        need(reference == expected, "BUNDLE_REFERENCE_CHANGED:" + name)
        if name in sources and name != WORKER:
            need(reference["sha256"] == sources[name]["sha256"]
                 and reference["bytes"] == sources[name]["bytes"], "UNAUTHORIZED_SOURCE_CHANGE:" + name)
        if name in UNCHANGED:
            need(reference["sha256"] == UNCHANGED[name], "FROZEN_DEPENDENCY_MISMATCH:" + name)
    preimage = read(destination / PREIMAGE)[0]
    expected_worker, expected_patch = patch_worker(preimage)
    need(sources[WORKER]["sha256"] == ORIGINAL_WORKER_SHA256
         and manifest["original_worker_sha256"] == ORIGINAL_WORKER_SHA256, "WORKER_ORIGIN_PIN_MISMATCH")
    need(read(destination / WORKER)[0] == expected_worker
         and manifest["worker_sha256"] == sha(expected_worker), "WORKER_PATCH_BYTES_DIFFER")
    need(read(destination / PATCH)[0] == expected_patch, "PATCH_EVIDENCE_CHANGED")
    need(manifest["normalizer_hashes"] == {k: v for k, v in UNCHANGED.items()
         if not k.endswith("provider_budget.py")}, "NORMALIZER_DECLARATION_MISMATCH")
    return dict(status="VERIFIED_NOT_EXECUTED", schema_version=VERSION,
                manifest_sha256=manifest_ref["sha256"], files=len(files),
                worker_sha256=sha(expected_worker), snapshot_contents_unchanged=True,
                source_reference_scope=manifest["source_reference_scope"],
                current_original_source_revalidated=False, original_physical_owner_restored=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    creation = sub.add_parser("create")
    creation.add_argument("--source-root", required=True, type=Path)
    creation.add_argument("--destination", required=True, type=Path)
    verification = sub.add_parser("verify")
    verification.add_argument("--destination", required=True, type=Path)
    verification.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args(argv)
    value = create(args.source_root, args.destination) if args.action == "create" else verify(
        args.destination, args.manifest_sha256)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
