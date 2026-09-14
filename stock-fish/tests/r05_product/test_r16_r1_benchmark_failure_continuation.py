"""TEST_FIXTURE: suite control flow only; no real worker, process or SQLite DB.

Can be copied to stock-fish/tests/r05_product without changing its imports.
The nested prepared_backup helper is exercised with its module-level backup
dependency replaced by a tiny byte-copy stub; sqlite3.connect is forbidden.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


FINAL_LABELS = [
    "final-cold", "final-warm-1", "final-warm-2", "final-warm-3",
    "corrected-t-to-t1",
]


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture
def suite_fixture(tmp_path, monkeypatch):
    root = next(parent for parent in Path(__file__).resolve().parents
                if (parent / "stock-fish/scripts/r16_r1_benchmark_suite.py").is_file())
    scripts = root / "stock-fish/scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "_test_r16_suite_failure_continuation", scripts / "r16_r1_benchmark_suite.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def forbidden_sqlite(*args, **kwargs):
        raise AssertionError("TEST_FIXTURE forbids opening any SQLite database")

    monkeypatch.setattr(module.sqlite3, "connect", forbidden_sqlite)
    monkeypatch.setattr(module.psutil, "Process", lambda *args, **kwargs:
                        SimpleNamespace(create_time=lambda: 1234567890.0))
    monkeypatch.setattr(module, "source_metadata", lambda root: {
        "status": "TEST_FIXTURE", "commit": "TEST_FIXTURE_FINAL",
        "tree": "TEST_FIXTURE_TREE", "tracked_dirty": False,
    })
    monkeypatch.setattr(module, "now", lambda: "2026-09-14T00:00:00+00:00")

    def fake_sha(path):
        path = Path(path).resolve()
        assert path.is_relative_to(tmp_path.resolve())
        return hashlib.sha256(path.read_bytes()).hexdigest()

    monkeypatch.setattr(module, "sha256", fake_sha)
    source = tmp_path / "not-a-database.fixture"
    source.write_bytes(b"TEST_FIXTURE_SOURCE_BYTES_NOT_SQLITE")
    build = tmp_path / "corrected-build.json"
    module.write_json(build, {
        "common_information_cutoff": "2026-09-14T00:00:00+00:00",
        "target_db": str(source), "input_t_corrected": "TEST_FIXTURE_T",
        "input_t_plus_1_corrected": "TEST_FIXTURE_T1",
    })
    binding = tmp_path / "baseline-binding.json"
    module.write_json(binding, {
        "commit": "e2855d695f8818bb13103997e2ba1f1552538d99",
        "tree": "81da2f17ea2adbc1e9488a1b53e2be1da2b31c8e",
        "note": "TEST_FIXTURE: only suite baseline identity gate; no source imported",
    })
    args = SimpleNamespace(
        baseline_source_root=str(tmp_path / "baseline-source"),
        baseline_binding=str(binding), corrected_build=str(build),
        output=str(tmp_path / "suite"), registry=str(tmp_path / "registry.json"),
        per_run_budget=10.0, preparation_budget=10.0, total_budget=60.0,
        old_bad_source_db=None, old_bad_cutoff=None,
    )
    state = SimpleNamespace(
        module=module, args=args, output=Path(args.output), calls=[], backups=[],
        failures={}, final_day_t=b"TEST_FIXTURE_COMPLETE_FINAL_DAY_T",
        sha=fake_sha,
    )

    def fake_backup(source_path, destination_path, *, deadline=None, progress=lambda value: None):
        source_path, destination_path = Path(source_path), Path(destination_path)
        assert source_path.exists()
        assert not destination_path.exists()
        assert deadline is not None
        content = source_path.read_bytes()
        destination_path.write_bytes(content)
        state.backups.append((source_path.name, destination_path.name, content))
        progress({"stage": "TEST_FIXTURE_BACKUP", "pages_done": 1, "pages_total": 1})
        return {"method": "TEST_FIXTURE_BYTE_COPY_NOT_SQLITE",
                "source": str(source_path), "target": str(destination_path),
                "source_sha256": fake_sha(source_path), "target_sha256": fake_sha(destination_path)}

    monkeypatch.setattr(module, "backup", fake_backup)

    def fake_supervise(command, output, **kwargs):
        label = kwargs["label"]
        assert 0 < kwargs["budget_seconds"] <= args.per_run_budget
        assert label not in state.calls, "No implicit retries of failed/partial cache"
        state.calls.append(label)
        output = Path(output)
        output.mkdir(parents=True)
        worker_output = Path(command[command.index("--output") + 1])
        cache = Path(command[command.index("--cache-db") + 1])
        worker_output.mkdir(parents=True)
        (output / "stdout.log").write_text("TEST_FIXTURE\n", encoding="utf-8")
        (output / "stderr.log").write_text("", encoding="utf-8")
        (output / "rss.jsonl").write_text("\n".join(json.dumps(row) for row in [
            {"perf_counter": 5, "rss_family_bytes": 9000},
            {"perf_counter": 12, "rss_family_bytes": 250},
            {"perf_counter": 18, "rss_family_bytes": 300},
            {"perf_counter": 22, "rss_family_bytes": 12000},
        ]) + "\n", encoding="utf-8")
        status = state.failures.get(label)
        run = {"label": label, "status": status or "SUCCESS",
               "exit_code": 1 if status else 0, "elapsed_seconds": 10.0,
               "rss_family_sampled_peak_bytes": 12000,
               "stdout": str(output / "stdout.log"), "stderr": str(output / "stderr.log")}
        if status == "ORPHANED_ACTIVE":
            run.update(exit_code=125, live_descendants=[{
                "pid": 99999999, "create_time": 0, "note": "TEST_FIXTURE_NOT_A_REAL_PROCESS",
            }])
        module.write_json(output / "run.json", run)
        if status:
            # Leave an explicit partial cache; the suite must neither reuse it
            # for skipped baseline warm runs nor overwrite it as a fresh run.
            cache.write_bytes(b"TEST_FIXTURE_PARTIAL_FAILED_BASELINE")
            (output / "stderr.log").write_text(
                "MemoryError: TEST_FIXTURE\n" if status == "FAILED" else "TEST_FIXTURE_ORPHAN\n",
                encoding="utf-8")
            module.write_json(worker_output / "failed-trace.json", {"status": status})
            return run

        if label == "final-cold":
            assert not cache.exists(), "Final cold must have its own empty cache"
            cache.write_bytes(state.final_day_t)
        elif label.startswith("final-warm-"):
            assert cache.exists()
            cache.write_bytes(cache.read_bytes() + label.encode("ascii"))
        elif label == "corrected-t-to-t1":
            assert cache.read_bytes() == state.final_day_t, "Increment must clone complete cold Day T"
        elif label == "baseline-cold":
            assert not cache.exists()
            cache.write_bytes(b"TEST_FIXTURE_COMPLETE_BASELINE_DAY_T")
        else:
            assert cache.exists()
        durations = {"baseline-cold": 9.0, "baseline-warm-1": 8.0,
                     "baseline-warm-2": 6.0, "baseline-warm-3": 7.0,
                     "final-cold": 7.0, "final-warm-1": 4.0,
                     "final-warm-2": 2.0, "final-warm-3": 3.0,
                     "corrected-t-to-t1": 5.0}
        module.write_json(worker_output / "benchmark.json", {
            "status": "SUCCESS", "elapsed_seconds": durations[label],
            "workload_perf_counter_start": 10, "workload_perf_counter_end": 20,
            "business_result_hash": "TEST_FIXTURE_T1_HASH" if label == "corrected-t-to-t1" else "TEST_FIXTURE_T_HASH",
            "derived_feature_store": {"cache_hits": 9, "cache_misses": 1,
                                      "new_rows": 1, "invalidated_rows": 0},
            "checkpoints": {"cache": {"after": {"bytes": 123}}},
        })
        return run

    monkeypatch.setattr(module, "supervise", fake_supervise)
    return state


def _assert_partial_final_report(state, failed_label, skipped):
    report = _read(state.output / "benchmark-summary.json")
    assert report["status"] == "FAIL_REQUIRES_REVIEW"
    assert [row["label"] for row in report["failed_runs"]] == [failed_label]
    failure = report["failed_runs"][0]
    assert failure["successful_workload_seconds"] is None
    assert failure["rss_family_sampled_peak_bytes"] == 12000
    assert failure["process_report_sha256"] == state.sha(failure["process_report"])
    assert "MemoryError" in (state.output / (failed_label + "-process") / "stderr.log").read_text()
    assert not (state.output / failed_label / "benchmark.json").exists()
    assert [row["label"] for row in report["skipped_runs"]] == skipped
    assert all(row["failed_dependency"] == failed_label and row["status"] == "SKIPPED_DEPENDENCY_FAILED"
               for row in report["skipped_runs"])
    assert [row["label"] for row in report["rows"] if row["label"] in FINAL_LABELS] == FINAL_LABELS
    assert all(row["workload_rss_family_sampled_peak_bytes"] == 300 for row in report["rows"])
    summary = report["summary"]
    assert summary["baseline"]["measurement_status"] == "NOT_VERIFIED_INCOMPLETE"
    assert summary["baseline"]["warm_median_seconds"] is None
    assert summary["baseline"]["warm_range_seconds"] is None
    assert summary["final"]["measurement_status"] == "COMPLETE"
    assert summary["final"]["warm_samples"] == [4.0, 2.0, 3.0]
    assert summary["final"]["warm_median_seconds"] == 3.0
    assert summary["same_corrected_t_business_hashes_match"] is None
    assert summary["warm_ratio_final_over_baseline"] is None
    assert summary["cross_implementation_comparison"] == "NOT_VERIFIED_BASELINE_INCOMPLETE"
    assert summary["corrected_increment_full_row_reuse_ratio"] == pytest.approx(0.9)
    assert summary["corrected_increment_has_no_invalidated_rows"] is True
    assert state.backups[-2:] == [
        ("final-cache.sqlite3", "day-t-cache.sqlite3", state.final_day_t),
        ("day-t-cache.sqlite3", "corrected-increment-cache.sqlite3", state.final_day_t),
    ]
    assert (state.output / "baseline-cache.sqlite3").read_bytes() == b"TEST_FIXTURE_PARTIAL_FAILED_BASELINE"
    return report


def test_baseline_cold_failure_preserves_raw_and_continues_independent_final(suite_fixture):
    state = suite_fixture
    state.failures["baseline-cold"] = "FAILED"
    assert state.module.run(state.args) == 2
    assert state.calls == ["baseline-cold"] + FINAL_LABELS
    report = _assert_partial_final_report(
        state, "baseline-cold", ["baseline-warm-1", "baseline-warm-2", "baseline-warm-3"])
    assert report["summary"]["baseline"]["cold_seconds"] is None
    assert report["summary"]["baseline"]["warm_samples"] == []


def test_baseline_warm_two_failure_skips_warm_three_and_continues_final(suite_fixture):
    state = suite_fixture
    state.failures["baseline-warm-2"] = "FAILED"
    assert state.module.run(state.args) == 2
    assert state.calls == ["baseline-cold", "baseline-warm-1", "baseline-warm-2"] + FINAL_LABELS
    report = _assert_partial_final_report(state, "baseline-warm-2", ["baseline-warm-3"])
    assert report["summary"]["baseline"]["cold_seconds"] == 9.0
    assert report["summary"]["baseline"]["warm_samples"] == [8.0]


def test_baseline_orphaned_process_stops_before_any_final_work(suite_fixture):
    state = suite_fixture
    state.failures["baseline-cold"] = "ORPHANED_ACTIVE"
    with pytest.raises(RuntimeError, match="BENCHMARK_FAILED:baseline-cold"):
        state.module.run(state.args)
    assert state.calls == ["baseline-cold"]
    assert len(state.backups) == 1  # source isolation only; no Day-T clone
    assert not (state.output / "final-cache.sqlite3").exists()
    assert not (state.output / "benchmark-summary.json").exists()
    checkpoint = _read(state.output / "suite-checkpoint.json")
    assert checkpoint["status"] == "FAILED"
    assert checkpoint["failed_label"] == "baseline-cold"
    assert checkpoint["rows"] == []
    failure = checkpoint["failed_runs"][0]
    assert failure["status"] == "ORPHANED_ACTIVE"
    assert failure["exit_code"] == 125
    assert _read(failure["process_report"])["live_descendants"]
