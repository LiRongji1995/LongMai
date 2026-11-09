"""The typed clock fast path preserves the original JSON clock predicate."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from stockfish_core.yaogu.contracts import Clocked, DailyBar, SourceReceipt, UniverseSnapshot
from stockfish_core.yaogu.visibility import CLOCKS, clock_visible, mapping, stamp


def original_clock_visible(obj, cutoff, *, allow_late_capture=False):
    try:
        row = mapping(obj)
        clocks = tuple(stamp(row[k]) for k in CLOCKS)
        cutoff = stamp(cutoff)
        if clocks[2] > clocks[3]:
            return False
        return max(clocks[:2] if allow_late_capture else clocks) <= cutoff
    except (KeyError, ValueError, TypeError):
        return False


class ClockProjectionTests(unittest.TestCase):
    def test_typed_and_mapping_inputs_equal_original_across_clock_boundaries(self):
        clocks = [datetime(2026, 11, 9, 1, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 9, 9, 0, tzinfo=ZoneInfo('Asia/Shanghai')),
            datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo('America/New_York'), fold=0),
            datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo('America/New_York'), fold=1)]
        for at in clocks:
            values = dict(event_time=at, source_published_at=at,
                first_seen_at=at + timedelta(minutes=1), ingested_at=at + timedelta(minutes=2))
            evidence = [Clocked(**values), SourceReceipt(**values, receipt_id='TEST_FIXTURE',
                provider='TEST_FIXTURE', dataset='market', content_hash='1' * 64,
                field_version='TEST_FIXTURE', row_count=1, status='COMPLETE', pagination_complete=True,
                parameters={'date': '2026-11-09'}, instrument_ids=('000001.SZ',)),
                DailyBar(**values, instrument_id='000001.SZ', session=date(2026, 11, 9),
                    close=Decimal('10.001'), source_refs=('TEST_FIXTURE',)),
                UniverseSnapshot(**values, snapshot_id='TEST_FIXTURE', securities=())]
            for typed in evidence:
                for obj in (typed, typed.model_dump(), typed.model_dump(mode='json')):
                    for offset in (-1, 0, 1, 2, 3):
                        cutoff = at + timedelta(minutes=offset)
                        for late in (False, True):
                            with self.subTest(kind=type(obj).__name__, at=str(at), offset=offset, late=late):
                                self.assertEqual(clock_visible(obj, cutoff, allow_late_capture=late),
                                    original_clock_visible(obj, cutoff, allow_late_capture=late))

    def test_no_clock_cache_hides_changed_or_invalid_inputs(self):
        at = datetime(2026, 11, 9, 1, tzinfo=timezone.utc)
        row = dict.fromkeys(CLOCKS, at)
        cutoff = at + timedelta(minutes=1)
        self.assertTrue(clock_visible(row, cutoff))
        row['first_seen_at'] = at + timedelta(minutes=2)
        self.assertFalse(clock_visible(row, cutoff))
        base = Clocked(**dict.fromkeys(CLOCKS, at))
        for changed in (None, at.replace(tzinfo=None), at + timedelta(days=1)):
            mutated = base.model_copy(update={'source_published_at': changed})
            self.assertEqual(clock_visible(mutated, cutoff), original_clock_visible(mutated, cutoff))
        self.assertFalse(clock_visible({}, cutoff))
        self.assertFalse(clock_visible(base, cutoff.replace(tzinfo=None)))

    def test_only_typed_clocks_use_projection_and_generic_model_protocol_still_works(self):
        at = datetime(2026, 11, 9, 1, tzinfo=timezone.utc)
        row = Clocked(**dict.fromkeys(CLOCKS, at))
        native, seen = Clocked.model_dump, []
        def counted(obj, *args, **kwargs):
            seen.append(kwargs)
            return native(obj, *args, **kwargs)
        with patch.object(Clocked, 'model_dump', counted):
            self.assertTrue(clock_visible(row, at))
        self.assertEqual(seen, [{'mode': 'json', 'include': frozenset(CLOCKS)}])
        class Generic:
            def model_dump(self, *, mode):
                return dict.fromkeys(CLOCKS, at.isoformat())
        self.assertTrue(clock_visible(Generic(), at))
