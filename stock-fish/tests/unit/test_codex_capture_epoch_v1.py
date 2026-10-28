"""TEST_FIXTURE current reauthentication; no network, DB, or real source writes."""
from datetime import datetime
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import codex_capture_epoch_v1 as epoch
from stockfish_core.yaogu import premarket_capture_cache_v1 as native
from stockfish_core.jobs import _ServiceLock
from tests.yaogu.test_premarket_capture_cache_v1 import FixtureBudget, FixtureExecutor


@pytest.fixture(autouse=True)
def deny_io(monkeypatch):
    import socket
    import sqlite3
    def forbidden(*args, **kwargs):
        raise AssertionError("TEST_FIXTURE_NETWORK_DB_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)


@pytest.fixture
def sample(tmp_path):
    cache = native.CaptureCache(tmp_path/"original", FixtureBudget(),
        config=dict(allow_test_fixture=True), executor=FixtureExecutor())
    spec = native.RequestSpec("market_daily", dict(date="2026-10-23"), "2026-10-23")
    record = cache.capture_one(spec)
    return cache, spec, record, tmp_path/"current-epoch"


def make_plan(cache):
    return epoch.plan(cache.root, allow_test_fixture=True)


def build(cache, destination, value=None, **kwargs):
    value = value or make_plan(cache)
    return epoch.create_epoch(cache.root, destination,
        expected_plan_sha256=value["plan_sha256"], allow_test_fixture=True, **kwargs)


def snapshot(root):
    return {str(path): (native._fp(path), hashlib.sha256(path.read_bytes()).hexdigest())
            for path in root.rglob("*") if path.is_file() and not path.is_symlink()}


def current_cache(path):
    return native.CaptureCache(path, FixtureBudget(),
        config=dict(offline=True, allow_test_fixture=True),
        executor=lambda *a, **k: pytest.fail("NO_CAPTURE_ALLOWED"))


def test_complete_recertification_keeps_original_bytes_and_clocks(sample):
    cache, spec, record, target = sample
    before = snapshot(cache.root)
    plan = make_plan(cache)
    assert snapshot(cache.root) == before
    with patch.object(native.CaptureCache, "capture_one", side_effect=AssertionError("NO_NEW_CAPTURE")):
        receipt = build(cache, target, plan)
    assert snapshot(cache.root) == before
    now = current_cache(target).lookup(spec).metadata
    assert receipt["network_calls"] == 0
    assert not receipt["historical_physical_continuity_restored"]
    assert not receipt["source_authority_restored"]
    assert now["raw"] == record.metadata["raw"]
    for key in ("first_seen_at", "ingested_at", "started_at", "completed_at", "receipt_id"):
        assert now[key] == record.metadata[key]
    assert not list(target.rglob("*.raw.json"))
    assert not list(target.rglob("*.receipt.json"))


def test_explicit_recertification_accepts_current_same_content_new_inode(sample):
    cache, spec, record, target = sample
    path = Path(record.metadata["raw"]["path"])
    replacement = path.with_name("TEST_FIXTURE-new-physical-file")
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
    with pytest.raises(ValueError, match="ORIGINAL_FP_CHANGED"):
        cache.verify(record)
    before = snapshot(cache.root)
    receipt = build(cache, target)
    assert snapshot(cache.root) == before
    now = current_cache(target).lookup(spec).metadata
    assert now["raw"]["sha256"] == record.metadata["raw"]["sha256"]
    assert now["raw"]["fp"] != record.metadata["raw"]["fp"]
    assert receipt["status"] == "CURRENT_ORIGINALS_RECERTIFIED_NEW_METADATA_EPOCH"


def test_change_after_plan_rejected_before_destination_creation(sample):
    cache, _, record, target = sample
    plan = make_plan(cache)
    path = Path(record.metadata["raw"]["path"])
    replacement = path.with_name("TEST_FIXTURE-replacement")
    replacement.write_bytes(path.read_bytes()); replacement.replace(path)
    with pytest.raises(ValueError, match="EPOCH_PLAN_CHANGED"):
        build(cache, target, plan)
    assert not target.exists()


def test_changed_source_bytes_are_not_relabelled(sample):
    cache, _, record, target = sample
    path = Path(record.metadata["raw"]["path"])
    path.write_bytes(path.read_bytes()+b" ")
    with pytest.raises(Exception, match="FILE_SHA256_CHANGED"):
        make_plan(cache)
    assert not target.exists()


def test_unfinished_request_inherited_and_never_retried(sample):
    cache, _, _, target = sample
    spec = native.RequestSpec("master_all", dict(date="2026-10-22"), "2026-10-22")
    directory = cache.root/"raw"/spec.key/"TEST_FIXTURE-unfinished"
    directory.mkdir(parents=True)
    (directory/"request-claim.json").write_text('{"status":"UNKNOWN"}')
    build(cache, target)
    fresh = current_cache(target)
    with pytest.raises(ValueError, match="UNFINISHED_CAPTURE_NO_IMPLICIT_RETRY"):
        fresh.lookup(spec)
    with pytest.raises(ValueError, match="UNFINISHED_CAPTURE_NO_IMPLICIT_RETRY"):
        fresh.capture_one(spec)


def test_failed_index_not_dropped_or_promoted(sample):
    cache, _, _, target = sample
    cache.executor = FixtureExecutor(basic_failure=True)
    day = datetime.now(native.SH).date().isoformat()
    spec = native.RequestSpec("stock_basic_all", {}, day, current=True)
    failed = cache.capture_one(spec)
    assert failed.status == "FETCH_FAILED"
    build(cache, target)
    copied = current_cache(target).lookup(spec)
    assert copied.status == "FETCH_FAILED"
    assert copied.metadata["receipt_id"] == failed.metadata["receipt_id"]
    assert copied.metadata["raw_first_seen_present"] is False


def test_fixture_requires_explicit_flag(sample):
    cache, _, _, target = sample
    plan = epoch.plan(cache.root)
    with pytest.raises(ValueError, match="EXPLICIT_SUPPORTED_PROVIDER_OR_TEST_FIXTURE_REQUIRED"):
        epoch.create_epoch(cache.root, target, expected_plan_sha256=plan["plan_sha256"])
    assert (target/"epoch-claim.json").exists()
    assert not (target/"epoch-complete.json").exists()


def test_no_reuse_of_existing_or_failed_destination(sample):
    cache, _, _, target = sample
    target.mkdir()
    with pytest.raises(ValueError, match="EPOCH_NEW_DESTINATION_REQUIRED"):
        build(cache, target)


def test_source_writer_lock_is_respected_without_modification(sample):
    cache, _, _, _ = sample
    before = snapshot(cache.root)
    lock = _ServiceLock(cache.root/"writer.lock")
    try:
        with pytest.raises(ValueError, match="EPOCH_SOURCE_WRITER_ACTIVE"):
            make_plan(cache)
    finally:
        lock.close()
    assert snapshot(cache.root) == before


def test_content_addressed_index_tampering_rejected(sample):
    cache, _, _, _ = sample
    path = next((cache.root/"index").glob("*/*.json"))
    path.write_bytes(path.read_bytes()+b" ")
    with pytest.raises(ValueError, match="EPOCH_ORIGINAL_INDEX_CONTENT_ADDRESS_CHANGED"):
        make_plan(cache)


def test_source_replaced_during_import_prevents_completion(sample):
    cache, _, record, target = sample
    original = native.CaptureCache.import_seed
    def replace(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        path = Path(record.metadata["raw"]["path"])
        other = path.with_name("TEST_FIXTURE-late-replacement")
        other.write_bytes(path.read_bytes()); other.replace(path)
        return result
    with patch.object(native.CaptureCache, "import_seed", replace):
        with pytest.raises(ValueError, match="ORIGINAL_FP_CHANGED"):
            build(cache, target)
    assert not (target/"epoch-complete.json").exists()


def test_finite_budget_failure_leaves_no_complete_epoch(sample):
    cache, _, _, target = sample
    value = make_plan(cache)
    with pytest.raises(Exception, match="READ_LIMIT_EXCEEDED"):
        build(cache, target, value, limits=dict(capture_read_bytes=64))
    assert not (target/"epoch-complete.json").exists()


def test_calendar_import_records_mature_request_identity_mapping(sample):
    cache, _, _, target = sample
    spec = native.RequestSpec("calendar", dict(start_date="2026-10-01", end_date="2026-10-28"))
    calendar = cache.capture_one(spec)
    receipt = build(cache, target)
    row = next(row for row in receipt["request_key_mappings"] if row["receipt_id"] == calendar.receipt_id)
    assert row["old_request_key"] == spec.key
    assert row["current_request_keys"] and row["current_request_keys"] != [spec.key]
    with pytest.raises(ValueError, match="UNFINISHED_CAPTURE_NO_IMPLICIT_RETRY"):
        current_cache(target).lookup(spec)


def test_actual_final_budget_failure_retracts_completion_and_preserves_evidence(sample):
    cache, _, _, target = sample
    before = snapshot(cache.root)
    original_check = epoch.Budget.check
    seen = []
    def fail_after_publication(self, stage, *, force=False):
        if stage == "EPOCH_FINAL_PUBLICATION":
            assert (target / "epoch-complete.json").is_file()
            seen.append(stage)
            self._fail("TEST_FIXTURE_FINAL_PUBLICATION_BUDGET_EXHAUSTED")
        return original_check(self, stage, force=force)
    with patch.object(epoch.Budget, "check", fail_after_publication):
        with pytest.raises(Exception, match="TEST_FIXTURE_FINAL_PUBLICATION_BUDGET_EXHAUSTED"):
            build(cache, target)
    assert seen == ["EPOCH_FINAL_PUBLICATION"]
    assert snapshot(cache.root) == before
    assert not (target / "epoch-complete.json").exists()
    failure = native._json((target / "epoch-failed.json").read_bytes())
    assert failure["status"] == "FAILED_RETAINED_NO_IMPLICIT_RETRY"
    assert failure["completion_accepted"] is False
    rejected = target / failure["rejected_completion"]
    assert rejected.is_file()
    retained = snapshot(target)
    with pytest.raises(ValueError, match="EPOCH_NEW_DESTINATION_REQUIRED"):
        build(cache, target)
    assert snapshot(target) == retained


def test_failure_outside_import_does_not_modify_existing_successful_epoch(sample):
    cache, _, _, target = sample
    build(cache, target)
    before = snapshot(target)
    with pytest.raises(ValueError, match="EPOCH_NEW_DESTINATION_REQUIRED"):
        build(cache, target)
    assert snapshot(target) == before
    assert (target / "epoch-complete.json").exists()
    assert not (target / "epoch-failed.json").exists()
