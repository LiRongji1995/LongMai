"""Native TEST_FIXTURE R2 equivalence when only ineligible factor calls vanish."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from stockfish_core.yaogu import premarket_native_core_v1 as core
from stockfish_core.yaogu import providers
from stockfish_core.yaogu.collection_qualification import factor_query_scope
from tests.support.saved_r2_v3_fixture import physical_source, write
from tests.yaogu.test_premarket_native_core_v1 import captures, FixtureBudget


STRATEGY = 'SHORT_HORIZON_STRENGTH_R2'


def _per_security_factor(root, code, observed, seen):
    """Use separate original receipts, preserving eligible stock references."""
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return seen.astimezone(tz or timezone.utc)
    parameters = dict(codes=[code], start_date='1990-01-01', end_date=str(observed))
    rows = [dict(code=code, dividOperateDate='2010-01-01', backAdjustFactor='1',
                 foreAdjustFactor='1', adjustFactor='1')]
    payload = dict(status='COMPLETE', pagination_complete=True,
        field_version='BAOSTOCK_00.9.30_R03_V1', first_seen_at=seen.isoformat(),
        rows=rows, coverage={}, gaps=[])
    with patch.object(providers, 'datetime', Clock):
        receipt = providers.archive_receipt(root/'cache/raw', 'baostock',
            'adjustment_factors', parameters, payload, status='COMPLETE',
            pagination_complete=True, field_version=payload['field_version'],
            first_seen_at=seen.isoformat(), source_url='https://TEST_FIXTURE.invalid/original')
    rid = receipt['receipt_id']
    result = dict(**payload, provider='baostock', endpoint='adjustment_factors',
        parameters=parameters, request_receipt_id=rid, receipts=[receipt],
        started_at=seen.isoformat(), completed_at=seen.isoformat())
    item = dict(archive_root=str(root),
        result=write(root/'results'/(rid+'.json'), result),
        raw=dict(path=str(root/'cache/raw'/(receipt['content_hash']+'.raw.json')),
                 sha256=receipt['content_hash']),
        receipt=dict(path=str(root/'cache/raw'/(rid+'.receipt.json')),
            sha256=hashlib.sha256((root/'cache/raw'/(rid+'.receipt.json')).read_bytes()).hexdigest()))
    return item, result


def _identity_projection(run, removed_receipt):
    """Exclude exact derived identity paths, never row facts or unknown states.

    Input hashes necessarily change when one source and its unused comparable
    bars are absent. Strategy/code identity, full qualification, every item,
    every numeric feature, ranking and evidence reason remain in comparison.
    """
    value = deepcopy(run)
    result = value['results'][STRATEGY]
    # These are concrete root identities generated from the changed input or
    # from its nested context identity, plus the current artifact creation time.
    for key in ('input_id', 'input_hash', 'effective_input_hash', 'research_context_hash',
                'run_id', 'content_hash', 'generated_at'):
        assert key in value
        value.pop(key)
    for key in ('input_hash', 'base_r1_result_hash'):
        assert key in result
        result.pop(key)
    for key in ('base_run_id', 'base_result_hash', 'input_id', 'input_hash'):
        assert key in result['base_run_provenance']
        result['base_run_provenance'].pop(key)
    for key in ('source_input_hash', 'effective_input_hash', 'context_content_hash'):
        assert key in result['theme_evolution']
        result['theme_evolution'].pop(key)
    # The resolver also authenticates ineligible inline bars. Only the precise
    # removed source may disappear from the root reference set.
    value['source_refs'] = [rid for rid in value['source_refs'] if rid != removed_receipt]
    result['theme_evolution']['source_refs'] = [rid for rid in
        result['theme_evolution']['source_refs'] if rid != removed_receipt]
    return value


def test_native_r2_all_business_output_survives_known_exclusion_factor_pruning(tmp_path, monkeypatch):
    import socket
    import sqlite3
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_NO_NETWORK_OR_BUSINESS_DB')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    original_items, observed, target, cutoff = captures(tmp_path/'TEST_FIXTURE-base',
                                                       sessions=67, history=True)
    seen = cutoff.replace(hour=12)
    shared, qualification_results = [], []
    for item in original_items:
        result = json.loads(Path(item['result']['path']).read_text())
        if result['endpoint'] == 'adjustment_factors':
            continue
        if result['endpoint'] in ('market_daily', 'master_all', 'master_szse'):
            rows = deepcopy(result['rows'])
            extra = deepcopy(rows[-1])
            if result['endpoint'] == 'master_szse':
                extra['A股代码'] = '000003'
            else:
                extra['code'] = 'sz.000003'
                if result['endpoint'] == 'market_daily':
                    extra['isST'] = '1'
            rows.append(extra)
            item = physical_source(tmp_path/'TEST_FIXTURE-status', result['endpoint'], rows, seen,
                day=(datetime.fromisoformat(result['parameters']['date']).date()
                     if 'date' in result['parameters'] else None),
                parameters=result['parameters'], include_result=True)
            result = json.loads(Path(item['result']['path']).read_text())
        shared.append(item)
        qualification_results.append(result)
    calendar = next(row for row in qualification_results if row['endpoint'] == 'calendar')
    sessions = [row['calendar_date'] for row in calendar['rows']
                if row['is_trading_day'] == '1' and row['calendar_date'] <= str(observed)]
    codes = ['sh.600001', 'sz.000001', 'sz.000002', 'sz.000003']
    scope = factor_query_scope(qualification_results, requested_codes=codes,
        observation_date=observed, cutoff=cutoff, sessions=sessions, coverage_type='TEST_FIXTURE')
    assert scope['factor_query_codes'] == ['sh.600001', 'sz.000001', 'sz.000002']
    assert scope['skipped_factor_codes'] == ['sz.000003']
    factors = {code: _per_security_factor(tmp_path/'TEST_FIXTURE-factor', code, observed, seen)
               for code in codes}
    removed_receipt = factors['sz.000003'][1]['request_receipt_id']
    full = core.run_core(captures=[*shared, *[factors[code][0] for code in codes]],
        observation_date=observed, target_session=target, cutoff=cutoff,
        output_dir=tmp_path/'TEST_FIXTURE-full-core', budget=FixtureBudget(), coverage_type='TEST_FIXTURE')
    pruned = core.run_core(captures=[*shared, *[factors[code][0] for code in scope['factor_query_codes']]],
        observation_date=observed, target_session=target, cutoff=cutoff,
        output_dir=tmp_path/'TEST_FIXTURE-pruned-core', budget=FixtureBudget(), coverage_type='TEST_FIXTURE')
    before, after = full.run, pruned.run
    assert before['input_hash'] != after['input_hash']
    assert set(before['source_refs']) - set(after['source_refs']) == {removed_receipt}
    assert not set(after['source_refs']) - set(before['source_refs'])
    before_theme = before['results'][STRATEGY]['theme_evolution']
    after_theme = after['results'][STRATEGY]['theme_evolution']
    assert set(before_theme['source_refs']) - set(after_theme['source_refs']) == {removed_receipt}
    assert not set(after_theme['source_refs']) - set(before_theme['source_refs'])
    for run in (before, after):
        result = run['results'][STRATEGY]
        parent = result['base_run_provenance']
        assert run['input_hash'] == result['input_hash'] == parent['input_hash']
        assert result['theme_evolution']['source_input_hash'] == run['input_hash']
        assert run['input_id'] == parent['input_id'] == 'market_' + run['input_hash']
        assert result['base_r1_result_hash'] == parent['base_result_hash']
    assert before['qualification'] == after['qualification'] == scope['qualification']
    arm = after['results'][STRATEGY]
    assert arm['eligible_pool_ids'] == ['000001.SZ', '000002.SZ', '600001.SH']
    assert arm['research_selected_ids'] == ['000001.SZ', '600001.SH']
    assert arm['market_turnover_context']['status'] == 'AVAILABLE'
    assert arm['counts']['complete_25_window'] == 3
    assert _identity_projection(before, removed_receipt) == _identity_projection(after, removed_receipt)
    assert full.validate() is True and pruned.validate() is True
