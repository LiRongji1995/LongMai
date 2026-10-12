"""TEST_FIXTURE calendars/trios/readonly history and unchanged native R2."""
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json

import pytest

from stockfish_core.yaogu import original_parent_history_field_derivation_v1 as frozen
from stockfish_core.yaogu import original_parent_history_field_derivation_v2 as subject
from stockfish_core.yaogu.contracts import MarketInput
from tests.yaogu import test_original_parent_history_field_derivation_v1 as prior


@pytest.fixture(autouse=True)
def v2_fixture_subject(monkeypatch):
    # Reuse the genuine fixture and its original assertions; no production
    # function or saved source capability is replaced.
    monkeypatch.setattr(prior, 'subject', subject)


def archive(tmp_path, monkeypatch, case='prefix'):
    capture = prior.fixture.capture

    def shifted(root, patch, endpoint, parameters, rows, **kwargs):
        if endpoint == 'calendar' and parameters['end_date'] == str(prior.DAY):
            parameters = dict(parameters)
            first = date.fromisoformat(parameters['start_date']) + timedelta(days=7)
            if case == '24_sessions':
                first = sorted(date.fromisoformat(r['calendar_date']) for r in rows
                    if r['is_trading_day'] == '1')[-24]
            parameters['start_date'] = str(first)
            rows = [dict(r) for r in rows if r['calendar_date'] >= str(first)]
            if case == 'overlap_outside_window':
                next(r for r in rows if r['calendar_date'] == '2026-06-15')['is_trading_day'] = '0'
            elif case == 'middle_hole':
                rows = [r for r in rows if r['calendar_date'] != '2026-06-15']
            elif case == 'window_hole':
                next(r for r in rows if r['calendar_date'] == '2026-09-15')['is_trading_day'] = '0'
            elif case == 'partial':
                kwargs.update(status='PARTIAL', page=False)
            elif case == 'incomplete_page':
                kwargs['page'] = False
            elif case == 'late_clock':
                kwargs['seen'] = prior.CUTOFF + timedelta(seconds=1)
        return capture(root, patch, endpoint, parameters, rows, **kwargs)

    monkeypatch.setattr(prior.fixture, 'capture', shifted)
    return prior.archive(tmp_path, monkeypatch)


def test_old_left_prefix_accepts_actual_native_r2_and_frozen_v1_still_refuses(tmp_path, monkeypatch):
    data = archive(tmp_path, monkeypatch)
    binding, inventory, items = data
    old_binding = frozen.OriginalHistoryArtifact(**asdict(binding))
    old_inventory = frozen.OriginalAttemptInventory(**asdict(inventory))
    with pytest.raises(ValueError, match='ORIGINAL_CALENDAR_OR_OBSERVATION_CONFLICT'):
        frozen.run_original_parent_history_field_r2(old_binding, old_inventory, items,
            prior.DAY, prior.CUTOFF, prior.datetime.now(prior.timezone.utc)+timedelta(seconds=90))
    before = {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()}
    result = prior.run(data)
    native = result['native_run']['results']['SHORT_HORIZON_STRENGTH_R2']
    assert native['counts']['complete_25_window'] == 2
    assert native['counts']['current_source_unknown'] == 0
    assert all(i['exact_features']['window_coverage']['selected_25_count'] == 25 for i in native['items'])
    assert Decimal(prior.current(result)['amount_cny']) == 10300
    assert result['normalizer_calls'] == result['native_R2_attempts'] == 1
    assert result['target_calendar_status'] == 'UNKNOWN'
    proof = result['original_source_audit']['history_window']['calendar_axis_scope']
    assert proof['old_extra_sessions'] == ['2026-05-01', '2026-05-04', '2026-05-05', '2026-05-06', '2026-05-07']
    cal = next(json.loads(item.result_path.read_bytes()) for item in items
        if json.loads(item.result_path.read_bytes())['endpoint'] == 'calendar')
    oracle = [r['calendar_date'] for r in cal['rows'] if r['is_trading_day'] == '1']
    assert result['input']['sessions'] == oracle
    assert proof['actual_last25_sessions'] == oracle[-25:]
    assert not set(proof['old_extra_sessions']) & set(result['input']['sessions'])
    assert proof['role'] == 'TYPED_CALENDAR_AXIS_SCOPE_NOT_TWO_RAW_FLAG_AUTHENTICATION'
    assert proof['two_original_RAW_flags_verified'] is False
    assert proof['old_RAW_calendar_authority_issued'] is False
    assert proof['target_calendar_extended'] is False
    assert result['historical_PIT_count'] == result['complete_independent_cycles'] == 0
    assert {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in before} == before


@pytest.mark.parametrize('case', ['overlap_outside_window', 'middle_hole', 'window_hole',
    '24_sessions', 'partial', 'incomplete_page', 'late_clock'])
def test_genuine_calendar_counterexamples_do_not_run_native(tmp_path, monkeypatch, case):
    data = archive(tmp_path, monkeypatch, case)
    reason = 'LATE_DERIVATION_PARENT_CAPTURE_AFTER_CUTOFF' if case == 'late_clock' else 'CALENDAR'
    with pytest.raises(ValueError, match=reason):
        prior.run(data)


def calendar_pair(tmp_path, monkeypatch):
    data = archive(tmp_path, monkeypatch)
    base = MarketInput.model_validate(json.loads(data[0].input_path.read_bytes())['input'])
    fresh, _ = subject.original.normalize_original_parent_asof(data[2], prior.DAY, prior.CUTOFF)
    return base, fresh


@pytest.mark.parametrize('case', ['partial', 'page', 'late', 'publication_after_seen',
    'duplicate', 'scope_count', 'scope_missing', 'provider', 'old_right', 'inside_window'])
def test_typed_base_declaration_does_not_get_widened_authority(tmp_path, monkeypatch, case):
    base, fresh = calendar_pair(tmp_path, monkeypatch)
    cal = next(r for r in base.receipts if r.dataset == 'calendar')
    if case == 'old_right':
        base = base.model_copy(update={'observation_date':fresh.observation_date})
    elif case == 'inside_window':
        fresh = fresh.model_copy(update={'sessions':tuple(s for s in fresh.sessions if s != date(2026, 9, 15))})
    else:
        if case == 'partial':changed = cal.model_copy(update={'status':'PARTIAL'})
        elif case == 'page':changed = cal.model_copy(update={'pagination_complete':False})
        elif case == 'late':changed = cal.model_copy(update={'ingested_at':prior.CUTOFF+timedelta(seconds=1)})
        elif case == 'publication_after_seen':changed = cal.model_copy(update={'source_published_at':cal.first_seen_at+timedelta(seconds=1)})
        elif case == 'scope_count':changed = cal.model_copy(update={'row_count':cal.row_count-1})
        elif case == 'scope_missing':changed = cal.model_copy(update={'parameters':{'end_date':str(prior.BASE_DAY)}})
        elif case == 'provider':changed = cal.model_copy(update={'provider':'TEST_FIXTURE_UNKNOWN'})
        else:changed = cal
        receipts = tuple(changed if r is cal else r for r in base.receipts)
        if case == 'duplicate':receipts += (cal,)
        base = base.model_copy(update={'receipts':receipts})
    with pytest.raises(ValueError, match='CALENDAR'):
        subject._calendar_consistency(base, fresh, prior.CUTOFF)


def test_calendar_audit_mutation_invalidates_live_owner(tmp_path, monkeypatch):
    owner = prior.private_owner(archive(tmp_path, monkeypatch))
    try:
        owner.audit['history_window']['calendar_axis_scope']['old_extra_sessions'] = []
        with pytest.raises(ValueError, match='FACTORY_SEAL_CHANGED'):
            owner.consume()
    finally:
        owner.history.close()


def test_new_identity_retains_frozen_v1_reference_without_reusing_its_authority():
    identity = subject.source_identity()
    assert identity['source_version'] == subject.VERSION
    assert identity['feature_version'] == subject.FEATURE_VERSION
    assert identity['frozen_history_field_v1_identity'] == frozen.source_identity()
    assert identity['native_strategy_identity'] == frozen.source_identity()['native_strategy_identity']
    assert set(frozen.source_identity()['source_file_hashes']) <= set(identity['source_file_hashes'])
    assert identity['MARKET_EDGE'] == 'NOT_ESTABLISHED'
    assert identity['parameter_status'] == 'UNVALIDATED' and identity['default_integration'] is False


# The original 38 genuine pipeline, partial-history, price, inventory, owner,
# mutation and once-consumption assertions also exercise the V2 subject.
for _name in dir(prior):
    if _name.startswith('test_'):
        globals()['test_v2_regression_' + _name[5:]] = getattr(prior, _name)
