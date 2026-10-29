"""V2 lifetime handoff uses isolated TEST_FIXTURE originals, never real history."""
from copy import deepcopy
from datetime import datetime, timezone
import gc
import hashlib
from pathlib import Path
import time
from types import FunctionType, SimpleNamespace
import weakref

import pytest

from scripts import benchmark_premarket_stages_v1 as v1
from scripts import benchmark_premarket_stages_v2 as bench
from stockfish_core.yaogu import premarket_native_core_v1 as native
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from tests.unit import test_benchmark_premarket_stages_v1 as controls
from tests.unit.test_benchmark_premarket_stages_v1 import config, no_network
from tests.yaogu.test_premarket_native_core_v1 import execute


def test_v1_source_and_derived_worker_are_frozen_without_mutation():
    raw = Path(v1.__file__).read_bytes()
    before = v1._worker
    assert hashlib.sha256(raw).hexdigest() == bench.V1_SOURCE_SHA256
    assert bench._worker is not v1._worker and bench._worker.__globals__ is vars(bench)
    assert "_comparison_snapshot" in bench._worker.__code__.co_names
    assert "_confirm_released" in bench._worker.__code__.co_names
    assert bench._proof is v1._proof
    with pytest.raises(ValueError, match="FROZEN_V1_SOURCE_CHANGED"):
        bench._build_worker(raw+b"\n")
    assert v1._worker is before and Path(v1.__file__).read_bytes() == raw


def test_v2_closure_contains_v1_v2_and_actual_encoding_scope():
    names = {Path(row["path"]).name for row in bench._script_refs(lambda _: None)}
    assert {"benchmark_premarket_stages_v2.py", "benchmark_premarket_stages_v1.py",
            "codex_premarket_fast_v1.py", "benchmark_real_premarket_v1.py"} <= names


def test_v2_original_limits_and_single_core_scope(config):
    before = deepcopy(config)
    assert bench._configuration(config) == v1._configuration(config)
    assert bench._configuration(config, 128) == v1._configuration(config, 128)
    assert config == before
    for scope, theme in (("core-theme", None), ("core", {})):
        with pytest.raises(ValueError, match="V2_CORE_ONLY_SCOPE"):
            bench._collections(controls._collection(), theme, scope, "2026-10-29T12:00:00+00:00")
    with pytest.raises(ValueError, match="ONLY_TIGHTEN"):
        bench._configuration(config, 512)


def test_actual_native_owner_and_bundle_die_before_identical_whole_proof(tmp_path):
    started = datetime.now(timezone.utc).isoformat()
    owner, captures, cutoff = execute(tmp_path)
    owner.validate()
    finished = datetime.now(timezone.utc).isoformat()
    capture = dict(core_records=captures, coverage_type="TEST_FIXTURE", observation_date="2026-09-30", target_session="2026-10-01")
    expected = v1._proof(owner, None, capture, None, "core", cutoff.isoformat(), started, finished, lambda _: None)
    owner_reference = weakref.ref(owner)
    bundle_reference = weakref.ref(native._owner_state(owner)["bundle"])
    comparison = bench._comparison_snapshot(owner)
    assert comparison.role == bench.COMPARISON_ROLE and not hasattr(comparison, "validate")
    with pytest.raises(ValueError, match="GENUINE_LIVE_OWNER_REQUIRED"):
        native._owner_state(comparison)
    del owner
    lifetime = bench._confirm_released(owner_reference, lambda _: None)
    assert owner_reference() is None and bundle_reference() is None
    assert lifetime["native_owner_released"] is True and lifetime["native_source_authority_restored"] is False
    actual = bench._proof(comparison, None, capture, None, "core", cutoff.isoformat(), started, finished, lambda _: None)
    assert actual == expected  # Every full hash, metadata guard and excluded value is identical.
    bench._terminal(comparison.validation_references, lambda _: None)
    assert {ref["path"] for ref in comparison.artifacts.values()} <= {ref["path"] for ref in comparison.validation_references}


def test_retained_owner_refuses_comparison_instead_of_claiming_release(tmp_path):
    owner, _, _ = execute(tmp_path)
    reference = weakref.ref(owner)
    with pytest.raises(ValueError, match="LIVE_OWNER_SURVIVED"):
        bench._confirm_released(reference, lambda _: None)
    assert owner.validate() is True
    with pytest.raises(ValueError, match="GENUINE_OWNER_REQUIRED"):
        bench._comparison_snapshot(SimpleNamespace(artifacts=owner.artifacts))
    del owner
    assert bench._confirm_released(reference, lambda _: None)["native_owner_released"] is True


def test_artifact_change_after_owner_destruction_still_fails_full_proof(tmp_path):
    started = datetime.now(timezone.utc).isoformat()
    owner, captures, cutoff = execute(tmp_path)
    finished = datetime.now(timezone.utc).isoformat()
    comparison, reference = bench._comparison_snapshot(owner), weakref.ref(owner)
    del owner
    bench._confirm_released(reference, lambda _: None)
    path = Path(comparison.artifacts["input.json.gz"]["path"])
    replacement = path.with_name("TEST_FIXTURE_replaced.json.gz")
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
    capture = dict(core_records=captures, coverage_type="TEST_FIXTURE", observation_date="2026-09-30", target_session="2026-10-01")
    with pytest.raises(ValueError, match="ARCHIVED_REFERENCE_CHANGED"):
        bench._proof(comparison, None, capture, None, "core", cutoff.isoformat(), started, finished, lambda _: None)


@pytest.mark.parametrize("limit", ("supervisor_read_bytes", "max_seconds", "self_rss_bytes"))
def test_release_phase_keeps_original_read_time_rss_caps(config, limit):
    class Temporary:
        pass
    owner = Temporary()
    reference = weakref.ref(owner)
    del owner
    started = time.perf_counter()
    charge, reads = bench._meter(config, started, initial=2)
    config["limits"][limit] = 1 if limit != "max_seconds" else 1e-12
    with pytest.raises(ValueError, match="COMPARISON_(READ|TIME|RSS)_LIMIT"):
        bench._confirm_released(reference, charge)
    assert reads[0] == 2


@pytest.mark.parametrize("stage", ("first-candidate", "second-candidate", "last-rename", "last-quota-check"))
@pytest.mark.parametrize("target", ("source", "artifact"))
def test_v2_preserves_all_v1_publication_race_guards(config, tmp_path, monkeypatch, stage, target):
    # Run the exact existing adversarial test against V2's cloned publisher.
    source = controls.test_parent_publication_races_retract_both_positive_receipts
    check = FunctionType(source.__code__, {**vars(controls), "bench": bench}, source.__name__, source.__defaults__)
    check(config, tmp_path, monkeypatch, stage, target)


def test_v2_actual_fresh_worker_rejects_fixture_and_keeps_state(config, tmp_path):
    source = controls.test_actual_fresh_worker_refuses_fixture_before_native_and_keeps_state
    check = FunctionType(source.__code__, {**vars(controls), "bench": bench}, source.__name__, source.__defaults__)
    check(config, tmp_path)


def test_v2_parent_budget_failure_preserves_failed_receipts(config, tmp_path, monkeypatch):
    source = controls.test_parent_output_aggregate_and_receipt_failure_never_publish_speed
    check = FunctionType(source.__code__, {**vars(controls), "bench": bench}, source.__name__, source.__defaults__)
    check(config, tmp_path, monkeypatch)
