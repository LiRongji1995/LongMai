"""Availability index preserves the original fail-closed replay semantics."""
import asyncio
from unittest.mock import Mock

import pytest

from stockfish_live.common import LiveError, canonical_json
from stockfish_live.contracts.market import MarketSnapshot
from stockfish_live.pit import ReplayClock
from stockfish_live.providers.replay import ReplayProvider
from test_replay_pit import DAY, at, environment, memory_day, observation


def legacy_snapshot(provider, cutoff, since=None):
    visible = []
    for bar in provider._bars:
        parent = provider._receipts.get(bar.source_id)
        if parent is None:
            raise LiveError("SOURCE_RECEIPT_MISSING")
        if any(value is not None and value > cutoff for value in (
            bar.timestamp, bar.source_timestamp, bar.available_at,
            parent.source_timestamp, parent.received_at, parent.available_at,
        )):
            continue
        provider._guard.check(bar, provider._receipts, cutoff)
        if since is None or bar.available_at > since:
            visible.append(bar)
    refs = sorted({bar.source_id for bar in visible})
    return MarketSnapshot(as_of=cutoff, data_cutoff=max((bar.available_at for bar in visible), default=None),
        provider="replay", bars=tuple(visible), receipts=tuple(provider._receipts[identity] for identity in refs),
        quality="VALID" if visible and all(bar.quality == "VALID" for bar in visible) else "PARTIAL")


@pytest.mark.parametrize("since", [None, at(9, 44), at(9, 45), at(9, 30)])
def test_bisect_snapshot_is_canonical_byte_identical_to_legacy(since):
    calendar, _ = environment()
    bars, receipts = memory_day(calendar)
    provider = ReplayProvider(bars, receipts, ReplayClock(at(15, 0)))
    assert provider._availability_index_safe
    actual = asyncio.run(provider.get_market_snapshot(at(), since=since))
    assert canonical_json(actual) == canonical_json(legacy_snapshot(provider, at(), since))


def test_incremental_snapshot_guards_only_the_candidate_interval():
    calendar, _ = environment()
    bars, receipts = memory_day(calendar)
    provider = ReplayProvider(bars, receipts, ReplayClock(at(15, 0)))
    provider._guard.check = Mock(wraps=provider._guard.check)
    actual = asyncio.run(provider.get_market_snapshot(at(), since=at(9, 44)))
    assert [bar.timestamp for bar in actual.bars] == [at()]
    assert provider._guard.check.call_count == 1
    provider._guard.check.reset_mock()
    assert len(asyncio.run(provider.get_market_snapshot(at())).bars) == 15
    assert provider._guard.check.call_count == 15


def test_future_missing_parent_keeps_legacy_fail_closed_behavior():
    past, receipt = observation(at(9, 31))
    future, _ = observation(at(9, 46))
    provider = ReplayProvider((past, future), {receipt.source_id: receipt}, ReplayClock(at()))
    assert not provider._availability_index_safe
    with pytest.raises(LiveError, match="SOURCE_RECEIPT_MISSING"):
        asyncio.run(provider.get_market_snapshot(at(), since=at(9, 44)))


def test_old_bad_parent_is_not_silently_skipped_by_since_index():
    old, receipt = observation(at(9, 31))
    bad = receipt.model_copy(update={"provider": "TEST_FIXTURE_CONFLICT"})
    provider = ReplayProvider((old,), {bad.source_id: bad}, ReplayClock(at()))
    assert not provider._availability_index_safe
    with pytest.raises(LiveError, match="SOURCE_RECEIPT_MISMATCH"):
        asyncio.run(provider.get_market_snapshot(at(), since=at(9, 44)))


def test_invalid_future_parent_keeps_hidden_then_raises_at_its_own_cutoff():
    bar, receipt = observation(at(9, 44))
    bad = receipt.model_copy(update={"provider": "TEST_FIXTURE_CONFLICT", "available_at": at(9, 46)})
    clock = ReplayClock(at())
    provider = ReplayProvider((bar,), {bad.source_id: bad}, clock)
    assert not provider._availability_index_safe
    assert asyncio.run(provider.get_market_snapshot(at())).bars == ()
    clock.advance(at(9, 46))
    with pytest.raises(LiveError, match="SOURCE_RECEIPT_MISMATCH"):
        asyncio.run(provider.get_market_snapshot(at(9, 46), since=at()))


def test_future_source_clock_is_still_hidden_inside_availability_candidates():
    bar, receipt = observation(at(9, 44))
    # Static row-parent relationships can be valid while a bypassed row clock
    # is later than its own availability. Six-clock gating still hides it.
    bar = bar.model_copy(update={"source_timestamp": at(9, 46)})
    provider = ReplayProvider((bar,), {receipt.source_id: receipt}, ReplayClock(at()))
    assert provider._availability_index_safe
    assert asyncio.run(provider.get_market_snapshot(at(), since=at(9, 43))).bars == ()


def test_same_availability_boundary_returns_all_ties_and_excludes_since_ties():
    first, first_parent = observation(at(9, 31), available=at(9, 45))
    second, second_parent = observation(at(9, 32), available=at(9, 45))
    provider = ReplayProvider((second, first), {row.source_id: row for row in (first_parent, second_parent)}, ReplayClock(at()))
    assert len(asyncio.run(provider.get_market_snapshot(at(), since=at(9, 44))).bars) == 2
    assert asyncio.run(provider.get_market_snapshot(at(), since=at())).bars == ()


def test_all_cached_endpoints_keep_future_request_guard():
    provider = ReplayProvider((), {}, ReplayClock(at()))
    with pytest.raises(LiveError, match="FUTURE_ACCESS_DENIED"):
        asyncio.run(provider.get_market_snapshot(at(9, 46), since=at()))
    with pytest.raises(LiveError, match="INVALID_TIME_RANGE"):
        asyncio.run(provider.get_market_snapshot(at(9, 44), since=at()))
