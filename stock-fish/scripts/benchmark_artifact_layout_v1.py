"""Bounded TEST_FIXTURE comparison of monolithic and chunked immutable artifacts.

This experiment never reads a business database, imports source authority,
changes production storage, deletes source data or claims market speedups.
All headers, chunk hashes, manifests and full reconstruction reads are counted.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_p0 as report_io
from scripts import codex_workspace as workspace

VERSION = "ARTIFACT_LAYOUT_TEST_FIXTURE_V1"
MAX_ROWS = 100000
CHUNK_ROWS = 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def rows(count):
    for index in range(count):
        yield canonical(dict(coverage_type="TEST_FIXTURE", row=index,
            security="TEST_FIXTURE_"+str(index % 3194), session_index=index//3194,
            value=None if index % 11 == 0 else index % 101,
            state="UNKNOWN" if index % 11 == 0 else "KNOWN"))+b"\n"


def write_new(path, raw):
    # Every path belongs to the new private experiment directory.
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def members(directory, expected_names, expected_identity=None):
    with workspace._directory_fd(directory) as descriptor:
        value = os.fstat(descriptor)
        identity = dict(device=value.st_dev, inode=value.st_ino, mode=value.st_mode, uid=value.st_uid)
        if expected_identity is not None and identity != expected_identity:
            raise ValueError("LAYOUT_DIRECTORY_IDENTITY_CHANGED")
        if set(os.listdir(descriptor)) != set(expected_names):
            raise ValueError("LAYOUT_UNDECLARED_OR_MISSING_FILE")
    return identity


def terminal_sample(directory, sample):
    evidence = sample["artifact_evidence"]
    names = [Path(ref["path"]).name for ref in evidence["references"]]
    members(directory, names, evidence["directory_identity"])
    returned = 0
    for ref in evidence["references"]:
        report_io.files._read_file(ref["path"], maximum=ref["bytes"], expected=ref, retain=False)
        returned += ref["bytes"]
    members(directory, names, evidence["directory_identity"])
    return returned


def verify(directory, *, maximum_logical_bytes, expected_rows, expected_sha256):
    """Full reconstruction, an independent streaming oracle, and bounded reads."""
    directory = Path(directory).absolute()
    if directory.resolve(strict=True) != directory or directory.is_symlink():
        raise ValueError("LAYOUT_CANONICAL_DIRECTORY_REQUIRED")
    raw, manifest_ref = report_io.files._read_file(directory/"manifest.json", maximum=2*1024**2)
    manifest = report_io.files._json(raw)
    if (manifest.get("schema_version") != VERSION or manifest.get("rows") != expected_rows
            or manifest.get("logical_sha256") != expected_sha256
            or manifest.get("coverage_type") != "TEST_FIXTURE"):
        raise ValueError("LAYOUT_MANIFEST_IDENTITY")
    items = manifest.get("chunks")
    if type(items) is not list or not 1 <= len(items) <= 100:
        raise ValueError("LAYOUT_CHUNK_COUNT")
    expected_names = [f"chunk-{i:04d}.jsonl.gz" for i in range(len(items))]
    if [item.get("name") for item in items] != expected_names:
        raise ValueError("LAYOUT_ORDERED_COMPLETE_CHUNKS_REQUIRED")
    directory_identity = members(directory, {*expected_names, "manifest.json"})
    count = total = 0
    returned = len(raw)
    digest = hashlib.sha256()
    refs = [manifest_ref]
    # The independent oracle is regenerated, not copied from manifest hashes.
    expected = iter(rows(expected_rows))
    for item in items:
        path = directory/item["name"]
        stored, ref = report_io.files._read_file(path, maximum=16*1024**2)
        if ref["sha256"] != item.get("stored_sha256") or ref["bytes"] != item.get("stored_bytes"):
            raise ValueError("LAYOUT_CHUNK_STORED_HASH")
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(stored)) as stream:
            logical = stream.read(min(maximum_logical_bytes-total, 16*1024**2)+1)
        total += len(logical)
        returned += len(stored)
        if total > maximum_logical_bytes or len(logical) > 16*1024**2:
            raise ValueError("LAYOUT_LOGICAL_BUDGET")
        if (hashlib.sha256(logical).hexdigest() != item.get("logical_sha256")
                or len(logical) != item.get("logical_bytes")):
            raise ValueError("LAYOUT_CHUNK_LOGICAL_HASH")
        lines = logical.splitlines(keepends=True)
        if len(lines) != item.get("rows"):
            raise ValueError("LAYOUT_CHUNK_ROWS")
        for line in lines:
            if next(expected, None) != line:
                raise ValueError("LAYOUT_INDEPENDENT_ROW_ORACLE")
        count += len(lines)
        digest.update(logical)
        refs.append(ref)
    if (next(expected, None) is not None or count != expected_rows
            or digest.hexdigest() != expected_sha256 or total != manifest.get("logical_bytes")):
        raise ValueError("LAYOUT_COMPLETE_RECONSTRUCTION_REQUIRED")
    for ref in refs:
        report_io.files._read_file(ref["path"], maximum=ref["bytes"], expected=ref, retain=False)
        returned += ref["bytes"]
    members(directory, {*expected_names, "manifest.json"}, directory_identity)
    return dict(rows=count, logical_bytes=total, logical_sha256=digest.hexdigest(),
                returned_read_bytes=returned, source_authority_restored=False,
                references=refs, directory_identity=directory_identity)


def arm(directory, count, chunk_rows):
    directory.mkdir(mode=0o700)
    tracemalloc.start()
    wall, cpu = time.perf_counter(), time.process_time()
    digest, logical_bytes, chunks, buffer = hashlib.sha256(), 0, [], []

    def flush():
        nonlocal buffer, logical_bytes
        if not buffer:
            return
        raw = b"".join(buffer)
        stored = gzip.compress(raw, compresslevel=6, mtime=0)
        name = f"chunk-{len(chunks):04d}.jsonl.gz"
        write_new(directory/name, stored)
        chunks.append(dict(name=name, rows=len(buffer), logical_bytes=len(raw),
            logical_sha256=hashlib.sha256(raw).hexdigest(), stored_bytes=len(stored),
            stored_sha256=hashlib.sha256(stored).hexdigest()))
        digest.update(raw)
        logical_bytes += len(raw)
        buffer = []

    for line in rows(count):
        buffer.append(line)
        if len(buffer) == chunk_rows:
            flush()
    flush()
    manifest = dict(schema_version=VERSION, coverage_type="TEST_FIXTURE", rows=count,
        logical_bytes=logical_bytes, logical_sha256=digest.hexdigest(), chunks=chunks)
    write_new(directory/"manifest.json", canonical(manifest))
    checked = verify(directory, maximum_logical_bytes=logical_bytes,
                     expected_rows=count, expected_sha256=digest.hexdigest())
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return dict(layout="monolithic" if chunk_rows == count else "chunked", rows=count,
        wall_seconds=time.perf_counter()-wall, cpu_seconds=time.process_time()-cpu,
        python_tracemalloc_peak_bytes=peak, os_rss_bytes=None,
        total_stored_bytes=sum(p.stat().st_size for p in directory.iterdir()),
        manifest_bytes=(directory/"manifest.json").stat().st_size,
        logical_bytes=logical_bytes, logical_sha256=digest.hexdigest(),
        returned_read_bytes=checked["returned_read_bytes"], chunk_count=len(chunks),
        artifact_evidence=dict(references=checked["references"], directory_identity=checked["directory_identity"]),
        scope="GENERATION_COMPRESSION_WRITE_FSYNC_MANIFEST_FULL_VERIFY_AND_TERMINAL_READS")


def benchmark(destination, *, count=12000):
    if type(count) is not int or not CHUNK_ROWS < count <= MAX_ROWS:
        raise ValueError("LAYOUT_BOUNDED_FIXTURE_ROWS_REQUIRED")
    destination = Path(destination).absolute()
    if "TEST_FIXTURE" not in destination.name:
        raise ValueError("LAYOUT_EXPLICIT_TEST_FIXTURE_DIRECTORY_REQUIRED")
    with workspace._directory_fd(destination.parent) as parent:
        os.mkdir(destination.name, 0o700, dir_fd=parent)
    samples = []
    # Fixed ABBA ordering makes both implementations pay both early/late costs.
    for index, name in enumerate(("monolithic", "chunked", "chunked", "monolithic")):
        samples.append(arm(destination/f"{index}-{name}", count, count if name == "monolithic" else CHUNK_ROWS))
    if len({sample["logical_sha256"] for sample in samples}) != 1:
        raise ValueError("LAYOUT_LOGICAL_EQUIVALENCE_FAILED")
    summary = {}
    for name in ("monolithic", "chunked"):
        group = [s for s in samples if s["layout"] == name]
        summary[name] = {metric: statistics.median(s[metric] for s in group) for metric in
            ("wall_seconds", "cpu_seconds", "python_tracemalloc_peak_bytes",
             "total_stored_bytes", "returned_read_bytes")}
    def terminal():
        started, cpu = time.perf_counter(), time.process_time()
        returned = sum(terminal_sample(destination/f"{index}-{sample['layout']}", sample)
                       for index, sample in enumerate(samples))
        return dict(wall_seconds=time.perf_counter()-started, cpu_seconds=time.process_time()-cpu,
                    returned_read_bytes=returned, scope="ALL_ARMS_FULL_MEMBER_SET_AND_ORIGINAL_HASH_PHYSICAL_IDENTITY")

    final_check = terminal()
    result = dict(schema_version=VERSION, generated_at=datetime.now(timezone.utc).isoformat(),
        coverage_type="TEST_FIXTURE", samples=samples, medians=summary,
        numerical_and_unknown_equivalence=True, production_format_adopted=False,
        real_market_end_to_end_speedup=None, source_authority_restored=False,
        same_process_and_filesystem_cache_effects="NOT_ELIMINATED",
        os_rss_not_measured=True, original_files_deleted=0, business_database_opened=False,
        no_source_capability=True, market_edge="NOT_ESTABLISHED",
        prepublication_terminal_cost=final_check,
        publication_metrics=dict(path="publication-validation.jsonl",
            role="MEASURED_DURING_PUBLICATION_NOT_PREWRITTEN_IN_THIS_REPORT",
            report_file_io_scope="PUBLISHER_REPORT_READS_SEPARATE_FROM_ARTIFACT_READS"))
    measured = []
    journal = destination/"publication-validation.jsonl"
    with journal.open("xb") as stream:
        os.chmod(journal, 0o600)
        def validate():
            value = terminal()
            value["validation_index"] = len(measured)+1
            stream.write(canonical(value)+b"\n")
            stream.flush()
            os.fsync(stream.fileno())
            measured.append(value)
        report_io.publish_new(destination/"report.json", result, validate=validate)
    # This addendum is returned after both publication callbacks have completed.
    # The saved immutable report points to the durable measured journal instead.
    result["publication_costs"] = dict(checks=measured,
        artifact_returned_read_bytes_total=sum(s["returned_read_bytes"] for s in samples)
            + final_check["returned_read_bytes"] + sum(v["returned_read_bytes"] for v in measured),
        report_file_read_bytes=2*(destination/"report.json").stat().st_size,
        measurement_scope="ARTIFACT_READS_PLUS_PUBLISHER_TWO_FULL_REPORT_VERIFICATION_READS",
        addendum_role="RETURN_VALUE_AND_JOURNAL_NOT_PREWRITTEN_IN_REPORT")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--fixture-rows", type=int, default=12000)
    args = parser.parse_args(argv)
    try:
        value = benchmark(args.destination, count=args.fixture_rows)
        print(json.dumps(value, ensure_ascii=False))
        return 0
    except Exception as error:
        # Preserve any owned incomplete experiment for diagnosis; never retry.
        import re
        reason = str(error)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,120}", reason):
            reason = "LAYOUT_EXPERIMENT_FAILED"
        print(json.dumps(dict(schema_version=VERSION, status="FAILED", reason=reason)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
