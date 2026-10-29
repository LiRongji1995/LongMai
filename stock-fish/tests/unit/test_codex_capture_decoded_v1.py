"""TEST_FIXTURE complete-verifier equivalence; no network or business database."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import socket
import sqlite3
import threading
from unittest.mock import patch

import pytest

from scripts import codex_capture_decoded_v1 as candidate
from stockfish_core.yaogu import premarket_capture_cache_v1 as original
from stockfish_core.yaogu import providers
from tests.yaogu.test_premarket_capture_cache_v1 import FixtureBudget, FixtureExecutor
from tests.yaogu import test_premarket_capture_cache_v1 as cache_contracts


@contextmanager
def _test_fixture_clock(instant):
    """Only this explicit TEST_FIXTURE scope shares a deterministic source clock."""
    class FixtureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz is not None else instant.astimezone().replace(tzinfo=None)

    with patch.object(cache_contracts, "datetime", FixtureDateTime), \
            patch.object(original, "datetime", FixtureDateTime), \
            patch.object(providers, "datetime", FixtureDateTime):
        yield


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("TEST_FIXTURE_NETWORK_DATABASE_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)


@pytest.fixture
def capture(tmp_path):
    executor = FixtureExecutor()
    cache = original.CaptureCache(tmp_path / "TEST_FIXTURE_cache", FixtureBudget(),
        config={"allow_test_fixture": True}, executor=executor)
    spec = original.RequestSpec("market_daily", {"date": "2026-10-15"}, "2026-10-15")
    record = cache.capture_one(spec)
    return cache, record, spec, executor


def new_cache(capture, cls=candidate.CaptureCache, budget=None):
    cache = capture[0]
    return cls(cache.root, budget or FixtureBudget(),
        config={"allow_test_fixture": True, "offline": True}, executor=capture[3])


def test_complete_authentication_and_all_fresh_reads_equal_reference(capture):
    reference = new_cache(capture, original.CaptureCache)
    optimized = new_cache(capture)
    before = (Path.read_bytes, Path.read_text,
              original.historical_replay.verify_capture, original.CaptureCache._authenticate)
    old = reference.verify(capture[1])
    new = optimized.verify(capture[1])
    assert new == old
    assert optimized.budget.read_bytes == reference.budget.read_bytes
    assert optimized._known == reference._known
    assert optimized.decoded_metrics["strict_decodes"] == 3
    assert optimized.decoded_metrics["native_json_reuses"] == 2
    assert optimized.decoded_metrics["fresh_native_reads"] == 4
    assert optimized.decoded_metrics["native_fallback_decodes"] == 0
    assert optimized.terminal() == reference.terminal()
    assert optimized.budget.read_bytes == reference.budget.read_bytes
    assert before == (Path.read_bytes, Path.read_text,
                      original.historical_replay.verify_capture, original.CaptureCache._authenticate)


def test_returned_nested_result_is_not_cached_between_authentications(capture):
    cache = new_cache(capture)
    first = cache.verify(capture[1])
    expected = deepcopy(first)
    first["rows"][0]["close"] = "999999"
    first["receipts"][0]["parameters"]["date"] = "2099-01-01"
    second = cache.verify(capture[1])
    assert second == expected and second is not first
    assert cache.decoded_metrics["native_json_reuses"] == 4
    assert cache.decoded_metrics["authentication_calls"] == 2


def test_lookup_keeps_full_verifier_and_does_not_redownload(capture):
    cache = new_cache(capture)
    first = cache.lookup(capture[2])
    reads = cache.budget.read_bytes
    second = cache.capture_one(capture[2])
    assert first.to_dict() == second.to_dict() == capture[1].to_dict()
    assert cache.budget.read_bytes > reads
    assert cache.decoded_metrics["authentication_calls"] == 2
    assert len(capture[3].calls) == 1


@pytest.mark.parametrize("role", ["raw", "receipt", "result"])
def test_changed_source_between_calls_rejected(capture, role):
    cache = new_cache(capture)
    cache.verify(capture[1])
    path = Path(capture[1].to_dict()[role]["path"])
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        cache.verify(capture[1])
    assert len(capture[3].calls) == 1


@pytest.mark.parametrize("role", ["raw", "receipt", "result"])
def test_same_content_replacement_inode_rejected(capture, role):
    cache = new_cache(capture)
    cache.verify(capture[1])
    path = Path(capture[1].to_dict()[role]["path"])
    replacement = path.with_suffix(".TEST_FIXTURE_replacement")
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
    with pytest.raises(ValueError):
        cache.verify(capture[1])


def test_change_at_native_boundary_fails_and_path_hooks_restore(capture):
    class MutatingCache(candidate.CaptureCache):
        def _producer_files(self, result, declared=()):
            value = super()._producer_files(result, declared)
            raw = Path(capture[1].to_dict()["raw"]["path"])
            raw.write_bytes(raw.read_bytes() + b" ")
            return value
    cache = new_cache(capture, MutatingCache)
    before = (Path.read_bytes, Path.read_text)
    with pytest.raises(ValueError):
        cache.verify(capture[1])
    assert before == (Path.read_bytes, Path.read_text)


def test_original_reader_failure_remains_primary(capture):
    class BrokenCache(candidate.CaptureCache):
        reads = 0
        def _read(self, *args, **kwargs):
            self.reads += 1
            if self.reads == 7:
                raise OSError("TEST_FIXTURE_NATIVE_READ_FAILED")
            return super()._read(*args, **kwargs)
    cache = new_cache(capture, BrokenCache)
    before = (Path.read_bytes, Path.read_text)
    with pytest.raises(OSError, match="NATIVE_READ_FAILED"):
        cache.verify(capture[1])
    assert before == (Path.read_bytes, Path.read_text)


def test_read_budget_still_charges_native_reads(capture):
    reference = new_cache(capture, original.CaptureCache)
    reference.verify(capture[1])
    maximum = reference.budget.read_bytes - 1
    class LimitedBudget(FixtureBudget):
        def check(self, stage):
            if self.read_bytes > maximum:
                raise RuntimeError("TEST_FIXTURE_STICKY_READ_LIMIT")
    cache = new_cache(capture, budget=LimitedBudget())
    with pytest.raises(RuntimeError, match="STICKY_READ_LIMIT"):
        cache.verify(capture[1])
    assert cache.budget.read_bytes == reference.budget.read_bytes


def test_original_owner_thread_guard_remains(capture):
    cache = new_cache(capture)
    errors = []
    def call():
        try:
            cache.verify(capture[1])
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=call)
    thread.start()
    thread.join()
    assert len(errors) == 1 and "CACHE_OWNER_PROCESS_THREAD_CHANGED" in str(errors[0])


def test_unknown_native_collaborator_is_not_given_decoded_values(capture):
    cache = new_cache(capture)
    with patch.object(original.historical_replay, "verify_capture",
                      side_effect=ValueError("TEST_FIXTURE_NATIVE_DENIED")):
        with pytest.raises(ValueError, match="NATIVE_DENIED"):
            cache.verify(capture[1])
    assert cache.decoded_metrics["native_json_reuses"] == 0


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'])
def test_original_strict_json_rejections_are_preserved(capture, raw):
    cache = new_cache(capture)
    scope = candidate._DecodeScope(cache)
    scope.pending_outer = (raw, {"path": "TEST_FIXTURE"})
    try:
        with pytest.raises(ValueError):
            scope.decode(raw)
    finally:
        scope.close()


@pytest.mark.parametrize("raw", [
    b'{\r\n"x": [1, 2], "s": "\\u4e2d\\ud800"\r\n}',
    b'{"x": 1e400, "y": -0.0, "s":"\\r\\n"}',
    '{"s":"真实TEST_FIXTURE"}'.encode(),
])
def test_native_decode_matches_utf8_text_semantics(capture, raw):
    cache = new_cache(capture)
    scope = candidate._DecodeScope(cache)
    value = original._json(raw)
    scope.pending_native = (raw, value, {"path": "TEST_FIXTURE"})
    try:
        text = scope.text_wrapper(candidate.io.BytesIO(raw), encoding="utf-8", errors=None).read()
        assert scope.native_loads(text) == json.loads(text)
        assert cache.decoded_metrics["native_json_reuses"] == 1
    finally:
        scope.close()


def test_bom_keeps_native_str_decoder_rejection(capture):
    cache = new_cache(capture)
    scope = candidate._DecodeScope(cache)
    raw = b'\xef\xbb\xbf{"x": 1}'
    scope.pending_native = (raw, original._json(raw), {"path": "TEST_FIXTURE"})
    try:
        text = scope.text_wrapper(candidate.io.BytesIO(raw), encoding="utf-8", errors=None).read()
        with pytest.raises(json.JSONDecodeError):
            scope.native_loads(text)
        assert cache.decoded_metrics["native_json_reuses"] == 0
    finally:
        scope.close()


def test_closed_scope_drops_values_and_cannot_mint_reuse(capture):
    cache = new_cache(capture)
    scope = candidate._DecodeScope(cache)
    scope.entries["raw"] = (b"{}", {}, {})
    scope.close()
    assert scope.entries == {} and scope.witness is None and scope.pending_native is None
    with pytest.raises(ValueError, match="DECODE_SCOPE_EXPIRED"):
        scope.native_loads("{}")


def test_original_runtime_meter_includes_all_verifier_reads(capture, tmp_path):
    from stockfish_core.yaogu.premarket_runtime_v1 import Budget
    job = tmp_path / "TEST_FIXTURE_job"
    job.mkdir()
    budget = Budget(job_dir=job, cache_dir=capture[0].root)
    cache = new_cache(capture, budget=budget)
    before = (Path.read_bytes, Path.read_text)
    with budget.phase("capture"), budget.meter_files():
        cache.verify(capture[1])
        cache.terminal()
    assert budget.metrics["returned_read_bytes"]["capture"] > 0
    assert cache.decoded_metrics["native_json_reuses"] == 2
    assert before == (Path.read_bytes, Path.read_text)


class OriginalCaptureContractTests(cache_contracts.CaptureCacheTests):
    """Run the original unchanged cache contract against the new constructor."""
    def cache(self, executor=None, **config):
        return candidate.CaptureCache(self.root / "cache", FixtureBudget(),
            config=dict(allow_test_fixture=True, **config),
            executor=executor or FixtureExecutor())

    def test_collection_complete_window_fallback_and_next_session(self):
        # The inherited test advances the plan by one minute. Noon keeps that
        # fixture on its source day even when the real test run crosses midnight.
        with _test_fixture_clock(datetime(2026, 10, 15, 12, tzinfo=original.SH)):
            super().test_collection_complete_window_fallback_and_next_session()


@pytest.mark.parametrize("cache_class", [original.CaptureCache, candidate.CaptureCache],
                         ids=["original", "candidate"])
def test_previous_day_first_seen_cannot_authenticate_next_current_session(tmp_path, cache_class):
    instant = datetime(2026, 10, 15, 23, 59, 30, tzinfo=original.SH)
    executor = FixtureExecutor()
    with _test_fixture_clock(instant):
        cache = cache_class(tmp_path / "TEST_FIXTURE_midnight_cache", FixtureBudget(),
            config={"allow_test_fixture": True}, executor=executor)
        spec = original.RequestSpec("stock_basic", {"codes": ["sh.600001"]},
            business_session="2026-10-16", current=True)
        record = cache.capture_one(spec)
        assert datetime.fromisoformat(record.to_dict()["first_seen_at"]).astimezone(original.SH) == instant
        with pytest.raises(ValueError, match="^CURRENT_CACHE_SOURCE_ACTUAL_FIRST_SEEN_SESSION_CHANGED$"):
            cache.verify(record)
        with pytest.raises(ValueError, match="^CURRENT_CACHE_SOURCE_ACTUAL_FIRST_SEEN_SESSION_CHANGED$"):
            cache.lookup(spec)
    assert len(executor.calls) == 1
