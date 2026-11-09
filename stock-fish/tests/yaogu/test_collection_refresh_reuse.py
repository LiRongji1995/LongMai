"""TEST_FIXTURE refresh reuse without new source-retry capabilities or SDKs."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from stockfish_core.yaogu import collection_factor_reconciliation as reconciliation
from stockfish_core.yaogu import premarket_capture_cache_v1 as native
from stockfish_core.yaogu import providers
from stockfish_core.yaogu.collection_factor_scope import plan_factor_requests
from tests.yaogu import test_premarket_capture_cache_v1 as captures
from tests.yaogu import test_collection_source_continuation as continuation
from tests.yaogu.test_collection_factor_reconciliation import completed_fixture


OLD_DAY, NEW_DAY, OBSERVED = '2026-11-07', '2026-11-09', '2026-11-06'
OFFICIAL = ('mainboard_sse', 'master_szse', 'delisting_sse')


@contextmanager
def fixture_clock(monkeypatch, day):
    instant = datetime.fromisoformat(day + 'T01:00:00+00:00')

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz or timezone.utc)

    with monkeypatch.context() as patch:
        for module in (native, providers, captures, continuation):
            patch.setattr(module, 'datetime', Clock)
        yield instant


def cache(root, executor=None):
    return native.CaptureCache(root, captures.FixtureBudget(),
        config=dict(allow_test_fixture=True, retry_failed=False,
                    calendar_lookback_days=110, calendar_lookahead_days=7),
        executor=executor or captures.FixtureExecutor(), continuation_context=None)


def official(endpoint, day):
    return native.RequestSpec(endpoint, {}, day, role='CURRENT_OFFICIAL', current=True)


def factor(codes):
    return native.RequestSpec('adjustment_factors',
        dict(codes=codes, start_date='1990-01-01', end_date=OBSERVED), OBSERVED,
        role='FULL_ANCHORED_FACTOR_WINDOW')


def original_bytes(root):
    return {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}


def assert_preserved(before):
    assert {path: path.read_bytes() for path in before} == before


def test_ordinary_collection_new_day_only_fetches_three_official_snapshots(tmp_path, monkeypatch):
    root = tmp_path / 'TEST_FIXTURE_cache'
    with fixture_clock(monkeypatch, OLD_DAY) as instant:
        initial = native.capture_collect(cache(root), now=instant)
    before = original_bytes(root)
    original = {record.receipt_id: record.to_dict()
        for record in initial.core_records + initial.prescreen_records
        if record.endpoint not in OFFICIAL}
    executor = captures.FixtureExecutor()
    with fixture_clock(monkeypatch, NEW_DAY) as instant:
        refreshed_cache = cache(root, executor)
        refreshed = native.capture_collect(refreshed_cache, now=instant)
        refreshed_cache.terminal()
    assert (initial.observation_date, initial.target_session) == (OBSERVED, NEW_DAY)
    assert (refreshed.observation_date, refreshed.target_session) == (OBSERVED, NEW_DAY)
    assert executor.calls == [(endpoint, {}) for endpoint in OFFICIAL]
    assert refreshed_cache.budget.calls['requests'] == 3
    assert refreshed_cache.continuation_context is None
    assert refreshed.ready_for_core and refreshed.completeness == 'COMPLETE'
    assert len([record for record in refreshed.core_records
                if record.endpoint in ('market_daily', 'master_all')]) == 50
    assert {record.receipt_id: record.to_dict()
        for record in refreshed.core_records + refreshed.prescreen_records
        if record.endpoint not in OFFICIAL} == original
    assert refreshed.identity_coverage['factor_query_codes'] == initial.identity_coverage['factor_query_codes']
    assert refreshed.identity_coverage['missing_positive_listing_codes'] == []
    assert refreshed.identity_coverage['prior_basic_reference_clocks_preserved'] is True
    assert refreshed.to_dict()['selector_calls'] == 0
    for record in refreshed.core_records:
        if record.endpoint in OFFICIAL:
            assert record.to_dict()['request_spec']['business_session'] == NEW_DAY
            assert native._stamp(record.to_dict()['first_seen_at']).astimezone(native.SH).date().isoformat() == NEW_DAY
    assert_preserved(before)


def test_new_official_date_does_not_retry_old_failed_or_unknown_requests(tmp_path, monkeypatch):
    root = tmp_path / 'TEST_FIXTURE_cache'
    old_failed, old_unknown = official('master_szse', OLD_DAY), official('mainboard_sse', OLD_DAY)
    with fixture_clock(monkeypatch, OLD_DAY):
        failed = cache(root, continuation.Executor(status='FETCH_FAILED')).capture_one(old_failed)
        with pytest.raises(RuntimeError, match='TEST_FIXTURE_EXECUTOR_DIED'):
            cache(root, captures.FixtureExecutor(crash=True)).capture_one(old_unknown)
    before = original_bytes(root)
    executor = captures.FixtureExecutor()
    with fixture_clock(monkeypatch, NEW_DAY):
        current = cache(root, executor)
        for endpoint in OFFICIAL:
            result = current.capture_one(official(endpoint, NEW_DAY))
            assert result.status == 'COMPLETE'
            current.verify(result)
        assert current.capture_one(old_failed).receipt_id == failed.receipt_id
        assert current.lookup(old_failed).status == 'FETCH_FAILED'
        with pytest.raises(ValueError, match='UNFINISHED_CAPTURE_NO_IMPLICIT_RETRY'):
            current.capture_one(old_unknown)
        with pytest.raises(ValueError, match='CONTINUATION_EXPLICIT_AUTHORIZATION_REQUIRED'):
            current.capture_authorized_once()
        current.terminal()
    assert executor.calls == [(endpoint, {}) for endpoint in OFFICIAL]
    assert current.budget.calls['requests'] == 3
    assert current.config['retry_failed'] is False
    assert len(list((root / 'raw' / old_failed.key / 'raw').iterdir())) == 1
    assert len(list((root / 'raw' / old_unknown.key / 'raw').iterdir())) == 1
    unknown_marker = next((root / 'raw' / old_unknown.key).glob('raw/*/attempt-failed.json'))
    assert json.loads(unknown_marker.read_bytes())['source_completion'] == 'UNKNOWN'
    assert_preserved(before)


def test_new_official_snapshot_cannot_clear_same_observation_factor_failure(tmp_path, monkeypatch):
    root = tmp_path / 'TEST_FIXTURE_cache'
    old = factor(['sh.600001', 'sh.600002'])
    with fixture_clock(monkeypatch, OLD_DAY):
        partial = cache(root, captures.FixtureExecutor(factor_partial=True)).capture_one(old)
    before = original_bytes(root)
    executor = captures.FixtureExecutor()
    with fixture_clock(monkeypatch, NEW_DAY):
        current = cache(root, executor)
        current.capture_one(official('mainboard_sse', NEW_DAY))
        plan = plan_factor_requests(current, codes=['sh.600002'], observed=OBSERVED, batch_size=1)
        assert [item.key for item in plan] == [old.key]
        assert current.capture_one(plan[0]).receipt_id == partial.receipt_id
        assert current.lookup(old).status == 'PARTIAL'
        with pytest.raises(ValueError, match='FACTOR_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY'):
            current.capture_one(factor(['sh.600002']))
        current.terminal()
    assert executor.calls == [('mainboard_sse', {})]
    assert current.budget.calls['requests'] == 1
    assert_preserved(before)


def test_old_official_original_cannot_be_relabelled_as_current_day(tmp_path, monkeypatch):
    root = tmp_path / 'TEST_FIXTURE_cache'
    with fixture_clock(monkeypatch, OLD_DAY):
        original = cache(root).capture_one(official('master_szse', OLD_DAY))
    before = original_bytes(root)
    relabelled = original.to_dict()
    new_spec = official('master_szse', NEW_DAY)
    relabelled.update(request_spec=new_spec.to_dict(), request_key=new_spec.key)
    with fixture_clock(monkeypatch, NEW_DAY):
        current = cache(root, captures.FixtureExecutor(crash=True))
        with pytest.raises(ValueError, match='CURRENT_CACHE_SOURCE_ACTUAL_FIRST_SEEN_SESSION_CHANGED'):
            current.verify(relabelled)
    assert current.budget.calls['requests'] == 0
    assert_preserved(before)


def test_successful_basis_survives_live_daily_update_but_keeps_old_registry_sealed(tmp_path):
    f = completed_fixture(tmp_path)
    live_daily = tmp_path / 'TEST_FIXTURE_application/scripts/codex_daily_v2.py'
    live_daily.parent.mkdir(parents=True)
    live_daily.write_text('# TEST_FIXTURE old published daily\n')
    registry_ref = continuation.save(f['job'] / 'code-registry.json', [continuation.reference(live_daily)])
    final = json.loads((f['job'] / 'final-control.json').read_bytes())
    final['code_registry'] = registry_ref
    final_ref = continuation.save(f['job'] / 'final-control.json', final)
    terminal = deepcopy(f['terminal'])
    terminal['receipt']['final'] = final_ref
    terminal['new_accounting']['worker_final_ref'] = final_ref
    terminal_ref = continuation.save(f['slot'] / 'terminal.json', terminal)

    def basis():
        ordinary = cache(f['cache'].root, captures.FixtureExecutor(crash=True))
        value = reconciliation.future_factor_authorization_basis(ordinary,
            scope=f['context']['scope'], scope_directory=f['context']['scope_directory'],
            operation_id=f['context']['operation_id'], terminal_reference=terminal_ref)
        ordinary.terminal()
        assert ordinary.budget.calls['requests'] == 0
        return value

    original = basis()
    live_daily.write_text('# TEST_FIXTURE new incremental-refresh daily\n')
    assert basis() == original
    assert original['source_capability_issued'] is False
    assert original['budget_credit'] == 0
    registry = f['job'] / 'code-registry.json'
    registry.write_bytes(registry.read_bytes() + b' ')
    with pytest.raises(ValueError):
        basis()
