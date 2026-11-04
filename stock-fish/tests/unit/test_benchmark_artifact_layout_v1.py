"""TEST_FIXTURE: representation equivalence, accounting, and corruption refusal."""
import gzip
import hashlib
import json

import pytest

from scripts import benchmark_artifact_layout_v1 as layout


def test_abba_preserves_every_row_and_unknown_and_counts_all_storage(tmp_path):
    destination = tmp_path/"TEST_FIXTURE_layout"
    report = layout.benchmark(destination, count=2049)
    assert [s["layout"] for s in report["samples"]] == ["monolithic", "chunked", "chunked", "monolithic"]
    assert len({s["logical_sha256"] for s in report["samples"]}) == 1
    assert report["production_format_adopted"] is False
    assert report["real_market_end_to_end_speedup"] is None
    assert report["os_rss_not_measured"] is True
    checks = [json.loads(line) for line in (destination/"publication-validation.jsonl").read_text().splitlines()]
    assert len(checks) == 2 and checks == report["publication_costs"]["checks"]
    stored_total = sum(sample["total_stored_bytes"] for sample in report["samples"])
    assert report["prepublication_terminal_cost"]["returned_read_bytes"] == stored_total
    assert all(value["returned_read_bytes"] == stored_total for value in checks)
    assert report["publication_costs"]["artifact_returned_read_bytes_total"] == 5*stored_total
    assert report["publication_costs"]["report_file_read_bytes"] == 2*(destination/"report.json").stat().st_size
    for index, sample in enumerate(report["samples"]):
        directory = destination/f"{index}-{sample['layout']}"
        assert sample["total_stored_bytes"] == sum(p.stat().st_size for p in directory.iterdir())
        assert sample["returned_read_bytes"] == 2*sample["total_stored_bytes"]
        reconstructed = b"".join(gzip.decompress(p.read_bytes()) for p in sorted(directory.glob("*.gz")))
        values = [json.loads(line) for line in reconstructed.splitlines()]
        assert len(values) == 2049
        assert [v["row"] for v in values] == list(range(2049))
        assert all(v["value"] is None for v in values if v["state"] == "UNKNOWN")
    before = (destination/"report.json").read_bytes()
    with pytest.raises(FileExistsError):
        layout.benchmark(destination, count=2049)
    assert (destination/"report.json").read_bytes() == before


@pytest.mark.parametrize("corruption", ["missing", "bytes", "order", "fake-row", "extra"])
def test_full_reconstruction_refuses_truncation_tampering_or_wrong_oracle(tmp_path, corruption):
    directory = tmp_path/"TEST_FIXTURE_arm"
    result = layout.arm(directory, 2049, 1024)
    manifest = json.loads((directory/"manifest.json").read_text())
    first = directory/manifest["chunks"][0]["name"]
    if corruption == "missing":
        first.unlink()
    elif corruption == "bytes":
        first.write_bytes(b"INVALID_TEST_FIXTURE")
    elif corruption == "order":
        manifest["chunks"].reverse()
        (directory/"manifest.json").write_bytes(layout.canonical(manifest))
    elif corruption == "extra":
        (directory/"undeclared").write_text("TEST_FIXTURE")
    else:
        raw = gzip.decompress(first.read_bytes()).replace(b'"row":0,', b'"row":9,', 1)
        stored = gzip.compress(raw, compresslevel=6, mtime=0)
        first.write_bytes(stored)
        manifest["chunks"][0].update(stored_sha256=hashlib.sha256(stored).hexdigest(),
            stored_bytes=len(stored), logical_sha256=hashlib.sha256(raw).hexdigest())
        (directory/"manifest.json").write_bytes(layout.canonical(manifest))
    with pytest.raises((ValueError, OSError)):
        layout.verify(directory, maximum_logical_bytes=result["logical_bytes"],
            expected_rows=2049, expected_sha256=result["logical_sha256"])


def test_fixture_scope_and_budgets_cannot_be_relaxed(tmp_path):
    with pytest.raises(ValueError, match="EXPLICIT_TEST_FIXTURE"):
        layout.benchmark(tmp_path/"production", count=2049)
    with pytest.raises(ValueError, match="BOUNDED_FIXTURE_ROWS"):
        layout.benchmark(tmp_path/"TEST_FIXTURE", count=layout.MAX_ROWS+1)
    directory = tmp_path/"TEST_FIXTURE_arm"
    result = layout.arm(directory, 2049, 1024)
    with pytest.raises(ValueError, match="LOGICAL_BUDGET"):
        layout.verify(directory, maximum_logical_bytes=1,
            expected_rows=2049, expected_sha256=result["logical_sha256"])


def test_late_extra_file_during_terminal_verification_is_rejected(tmp_path, monkeypatch):
    directory = tmp_path/"TEST_FIXTURE_arm"
    result = layout.arm(directory, 2049, 1024)
    read = layout.report_io.files._read_file
    inserted = False
    def add_during_terminal(path, **kwargs):
        nonlocal inserted
        value = read(path, **kwargs)
        if kwargs.get("expected") is not None and not inserted:
            (directory/"unexpected-late-file").write_text("TEST_FIXTURE")
            inserted = True
        return value
    monkeypatch.setattr(layout.report_io.files, "_read_file", add_during_terminal)
    with pytest.raises(ValueError, match="UNDECLARED_OR_MISSING"):
        layout.verify(directory, maximum_logical_bytes=result["logical_bytes"],
                      expected_rows=2049, expected_sha256=result["logical_sha256"])


@pytest.mark.parametrize("mutation", ["extra", "same-byte-replacement"])
@pytest.mark.parametrize("when", ["before-publication", "after-first-callback"])
def test_all_arms_are_bound_through_report_publication(tmp_path, monkeypatch, mutation, when):
    destination = tmp_path/"TEST_FIXTURE_layout"
    publish = layout.report_io.publish_new
    def mutate():
        directory = destination/"0-monolithic"
        if mutation == "extra":
            (directory/"late-extra").write_text("TEST_FIXTURE")
        else:
            original = directory/"chunk-0000.jsonl.gz"
            replacement = directory/"replacement"
            replacement.write_bytes(original.read_bytes())
            replacement.replace(original)
    def intercept(path, value, **kwargs):
        if when == "before-publication":
            mutate()
        else:
            validate = kwargs["validate"]
            calls = 0
            def mutate_after_first():
                nonlocal calls
                validate()
                calls += 1
                if calls == 1:
                    mutate()
            kwargs["validate"] = mutate_after_first
        return publish(path, value, **kwargs)
    monkeypatch.setattr(layout.report_io, "publish_new", intercept)
    with pytest.raises(ValueError):
        layout.benchmark(destination, count=2049)
    assert not (destination/"report.json").exists()
