"""Explicit backup-to-new-directory drill; never opens the original operational DB.

The legacy backup verifier/restorer preserves bytes. This additional layer probes
selected saved documents and records measured restoration time. It neither grants
source capability nor recreates historical PIT or an original physical owner.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def _clock():
    return datetime.now(timezone.utc)


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular(path, root):
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("DRILL_PATH_OUTSIDE_BACKUP")
    current = path
    while current != root.parent:
        if current.is_symlink():
            raise ValueError("DRILL_SYMLINK_REJECTED")
        current = current.parent
    if not path.is_file():
        raise ValueError("DRILL_REGULAR_FILE_REQUIRED")


def _fingerprint(path):
    value = path.stat()
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns, value.st_mode, value.st_uid)


def inventory(backup, max_bytes):
    if backup.is_symlink() or not backup.is_dir():
        raise ValueError("DRILL_BACKUP_DIRECTORY_REQUIRED")
    manifest_path = backup / "manifest.json"
    _regular(manifest_path, backup)
    if manifest_path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("DRILL_MANIFEST_TOO_LARGE")
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("version") != "wal-artifact-backup-v1" or manifest.get("database") != "database.sqlite3":
        raise ValueError("DRILL_BACKUP_CONTRACT_REJECTED")
    names, total = set(), manifest_path.stat().st_size
    paths = [manifest_path]
    for record in manifest["files"]:
        relative = PurePosixPath(record["path"])
        if (relative.is_absolute() or ".." in relative.parts or "\\" in record["path"]
                or relative.as_posix() != record["path"] or record["path"] in names):
            raise ValueError("DRILL_MANIFEST_PATH_REJECTED")
        if (type(record["size"]) is not int or record["size"] < 0
                or not re.fullmatch("[0-9a-f]{64}", record["sha256"])):
            raise ValueError("DRILL_MANIFEST_RECORD_INVALID")
        path = backup.joinpath(*relative.parts)
        _regular(path, backup)
        if path.stat().st_size != record["size"]:
            raise ValueError("DRILL_BACKUP_SIZE_MISMATCH")
        total += record["size"]
        if total > max_bytes:
            raise ValueError("DRILL_INPUT_BYTE_LIMIT")
        paths.append(path)
        names.add(record["path"])
    if "database.sqlite3" not in names:
        raise ValueError("DRILL_DATABASE_NOT_IN_MANIFEST")
    # Legacy verification may leave an empty WAL and SHM. Pin their bytes and
    # identity, never open source SQLite and never remove/checkpoint sidecars.
    # Nonempty WAL/journal cannot be represented by a main-file-only manifest.
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = backup / ("database.sqlite3" + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            _regular(sidecar, backup)
            if suffix != "-shm" and sidecar.stat().st_size:
                raise ValueError("DRILL_NONEMPTY_BACKUP_SIDECAR_REJECTED")
            paths.append(sidecar)
            total += sidecar.stat().st_size
    if total > max_bytes:
        raise ValueError("DRILL_INPUT_BYTE_LIMIT")
    return manifest, paths, total


def _date(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("DRILL_TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def validate_probe(probe):
    if (probe.get("schema") != "RECOVERY_PROBE_V1"
            or probe.get("data_class") not in {"TEST_FIXTURE", "PRIVATE_OPERATIONAL"}
            or not isinstance(probe.get("documents"), list) or not probe["documents"]):
        raise ValueError("DRILL_EXPLICIT_DOCUMENT_PROBE_REQUIRED")
    keys = set()
    for row in probe["documents"]:
        key = (row["namespace"], row["document_id"])
        if (not all(isinstance(value, str) and 0 < len(value) <= 256 for value in key)
                or key in keys or not re.fullmatch("[0-9a-f]{64}", row["expected_content_hash"])):
            raise ValueError("DRILL_DOCUMENT_PROBE_INVALID")
        keys.add(key)


def run_drill(backup, destination, probe_path, *, max_bytes=1024 ** 3):
    backup, destination, probe_path = map(lambda value: Path(value).absolute(), (backup, destination, probe_path))
    if (destination.exists() or destination.is_symlink() or destination.resolve().is_relative_to(backup.resolve())
            or backup.resolve().is_relative_to(destination.resolve())):
        raise ValueError("DRILL_DESTINATION_MUST_BE_NEW_AND_DISJOINT")
    if any(parent.is_symlink() for parent in destination.parents):
        raise ValueError("DRILL_DESTINATION_PARENT_SYMLINK")
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("DRILL_POSITIVE_BYTE_LIMIT_REQUIRED")
    _regular(probe_path, probe_path.parent)
    if probe_path.stat().st_size > 1024 ** 2:
        raise ValueError("DRILL_PROBE_TOO_LARGE")
    probe = json.loads(probe_path.read_bytes())
    validate_probe(probe)
    started, tick = _clock(), time.monotonic()
    manifest, paths, total = inventory(backup, max_bytes)
    original = {str(path): (_fingerprint(path), _sha(path)) for path in [*paths, probe_path]}
    sys.path.insert(0, str(ROOT)) if str(ROOT) not in sys.path else None
    from stockfish_core.snapshot_backup import restore_state
    from stockfish_core.storage import checked_payload
    from scripts.codex_p0 import publish_new
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = {"schema": "RECOVERY_DRILL_REPORT_V1", "data_class": probe["data_class"],
              "status": "FAILED", "started_at": started.isoformat(),
              "backup_manifest_sha256": original[str(backup / "manifest.json")][1],
              "probe_sha256": original[str(probe_path)][1], "input_logical_bytes": total,
              "source_database_opened": False, "services_started": False,
              "source_capability": "REAUTHENTICATION_REQUIRED", "historical_pit_restored": False,
              "original_physical_identity_restored": False,
              "rpo": {"status": "NOT_ESTABLISHED", "seconds": None,
                      "reason": "NO_INDEPENDENT_SOURCE_TRANSACTION_WATERMARK"}}
    def terminal():
        for path in [*paths, probe_path]:
            _regular(path, backup if path != probe_path else probe_path.parent)
            if original[str(path)] != (_fingerprint(path), _sha(path)):
                raise ValueError("DRILL_SOURCE_CHANGED_DURING_VERIFICATION")
    try:
        # Only an owned same-byte staging copy is opened by the legacy verifier.
        # This avoids its read-only SQLite connection changing source SHM files.
        staged = destination / "staged-backup"
        staged.mkdir(mode=0o700)
        for relative in ["manifest.json", *(row["path"] for row in manifest["files"])]:
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with (backup / relative).open("rb") as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            if _sha(target) != original[str(backup / relative)][1]:
                raise ValueError("DRILL_STAGED_COPY_MISMATCH")
        report["byte_verification"] = restore_state(staged, destination / "restored")
        report["source_backup_sqlite_opened"] = False
        results = []
        database = destination / "restored/database.sqlite3"
        with sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            for expected in probe["documents"]:
                row = connection.execute("SELECT payload, content_hash FROM documents WHERE namespace=? AND document_id=?",
                                         (expected["namespace"], expected["document_id"])).fetchone()
                status = "MISSING"
                if row is not None:
                    checked_payload(row)
                    status = "READABLE_HASH_MATCH" if row["content_hash"] == expected["expected_content_hash"] else "HASH_MISMATCH"
                results.append({"namespace": expected["namespace"], "document_id": expected["document_id"], "status": status})
        report["document_probes"] = results
        report["saved_result_readability"] = "VERIFIED_FOR_REQUESTED_DOCUMENTS" if all(row["status"] == "READABLE_HASH_MATCH" for row in results) else "INCOMPLETE_OR_MISMATCH"
        # Backup age is observed metadata, not an estimate of lost transactions.
        created = _date(manifest["created_at"])
        if created > started:
            raise ValueError("DRILL_BACKUP_FUTURE_TIMESTAMP")
        report["backup_age_seconds"] = (started - created).total_seconds()
        terminal()
        report["original_backup_unchanged"] = True
        report["status"] = "COMPLETE_FOR_DECLARED_SCOPE" if report["saved_result_readability"] == "VERIFIED_FOR_REQUESTED_DOCUMENTS" else "FAILED"
    except Exception as exc:
        report["failure_type"] = type(exc).__name__
        report["failure_reason"] = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("DRILL_") else "RESTORE_OR_PROBE_FAILED"
        raise
    finally:
        ended = _clock()
        report["finished_at"] = ended.isoformat()
        report["rto"] = {"measurement": "BACKUP_VERIFICATION_RESTORE_AND_REQUESTED_DOCUMENT_READS",
                         "elapsed_seconds": time.monotonic() - tick,
                         "utc_elapsed_seconds": (ended - started).total_seconds(),
                         "operational_service_recovery": "NOT_ESTABLISHED"}
        try:
            publish_new(destination / "drill-report.json", report,
                        validate=terminal if report["status"] == "COMPLETE_FOR_DECLARED_SCOPE" else None)
        except Exception:
            # The publisher removes only its own unsuccessful report. Preserve
            # an explicit diagnostic without reclassifying changed evidence.
            report.update(status="FAILED", original_backup_unchanged=False,
                          failure_reason="DRILL_PUBLICATION_OR_TERMINAL_CHECK_FAILED")
            publish_new(destination / "drill-failure.json", report)
            raise
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True,
                        help="RECOVERY_PROBE_V1 JSON: data_class and documents with namespace/document_id/expected_content_hash")
    parser.add_argument("--max-bytes", type=int, default=1024 ** 3,
                        help="Logical input ceiling; not a hard OS I/O or runtime quota")
    args = parser.parse_args(argv)
    report = run_drill(args.backup, args.destination, args.probe, max_bytes=args.max_bytes)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["status"] == "COMPLETE_FOR_DECLARED_SCOPE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
