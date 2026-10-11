"""Isolated synthetic TEST_FIXTURE originals; no SDK/network/DB/model calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from stockfish_core.yaogu import historical_replay_factor_lineage as bridge
from stockfish_core.yaogu import historical_replay_turnover as turnover
from stockfish_core.yaogu.providers import archive_receipt
from stockfish_core.yaogu.visibility import EvidenceResolver
from tests.r05_product.test_historical_replay_adapter import fixture_capture, checkpoint
from tests.r05_product.test_historical_replay_turnover_adapter import add_turns


def fixture_factor(root, rows, params, statuses, *, partial=False, missing_raw_clock=False,
                   started=None, missing_statuses=False, raw_status=None, wrapper_changes=None,
                   nonnull_count=False, future_seen=False):
    started = started or datetime.now(timezone.utc) - timedelta(seconds=1)
    seen = datetime.now(timezone.utc) + (timedelta(days=1) if future_seen else timedelta())
    state = 'PARTIAL' if partial else 'COMPLETE'
    coverage = {'requested_codes': params['codes'], 'returned_codes': sorted({r['code'] for r in rows}),
                'returned_rows': len(rows), 'cursor_exhausted': not partial,
                'evidence_class': 'TEST_FIXTURE_ENGINEERING_ONLY'}
    if not missing_statuses:
        coverage['security_statuses'] = statuses
    raw = {'status': raw_status or state, 'pagination_complete': not partial,
           'field_version': turnover.PROVIDER_FIELD_VERSION, 'rows': rows,
           'coverage': coverage, 'gaps': ['TEST_FIXTURE_PENDING_OTHER_CODES'] if partial else [],
           'failure_category': None, 'error_code': None, 'diagnostics': {},
           'source_url': 'https://baostock.com/TEST_FIXTURE_ONLY'}
    if not missing_raw_clock:
        raw['first_seen_at'] = seen.isoformat()
    receipt = archive_receipt(root/'cache/raw', 'baostock', 'adjustment_factors', params, raw,
        status=state, pagination_complete=not partial, field_version=raw['field_version'],
        source_url=raw['source_url'], first_seen_at=seen.isoformat())
    if nonnull_count or future_seen:
        # New real fixture receipt identity, never patch an authenticated object.
        oldpath = root/'cache/raw'/(receipt['receipt_id']+'.receipt.json')
        receipt.pop('receipt_id')
        if nonnull_count:
            receipt['row_count'] = len(rows)
        if future_seen:
            receipt['ingested_at'] = (seen+timedelta(microseconds=1)).isoformat()
        receipt['receipt_id'] = bridge._identity_hash(receipt)
        (root/'cache/raw'/(receipt['receipt_id']+'.receipt.json')).write_text(json.dumps(receipt))
        oldpath.unlink()
    request = {'provider': 'baostock', 'endpoint': 'adjustment_factors', 'parameters': params}
    result = {**raw, **request, 'request_id': bridge._identity_hash(request),
              'receipts': [receipt], 'request_receipt_id': receipt['receipt_id'],
              'started_at': started.isoformat(),
              'completed_at': (datetime.fromisoformat(receipt['ingested_at'])+timedelta(microseconds=1)).isoformat(),
              'implementation_source_hashes': {name: bridge._sha(Path(bridge.__file__).with_name(name))
                  for name in ('provider_requests.py','provider_requests_worker.py','provider_budget.py')}}
    if wrapper_changes:
        result.update(wrapper_changes)
    folder = root/'archive'; folder.mkdir(parents=True, exist_ok=True)
    path = folder/('request-'+result['request_id']+'-'+receipt['receipt_id'][:16]+'.json')
    path.write_text(json.dumps(result))
    return path, result


def setup_fixture(tmp_path, *, ancestor_rows_change=False, ancestor_status_change=False,
                  ancestor_clock_outside=False, final_missing_raw_clock=False, factor_end=None,
                  contradictory_complete_status=False):
    active = tmp_path/'TEST_FIXTURE_ACTIVE'; prior = tmp_path/'TEST_FIXTURE_ANCESTOR'
    policy = fixture_capture(active)
    add_turns(active, ['5.000001'])
    path, original = checkpoint(active,'adjustment_factors'); path.unlink()
    rows = original['rows']; params = dict(original['parameters'])
    if factor_end:
        params['end_date'] = factor_end
        rows = [r for r in rows if r['dividOperateDate'] <= factor_end]
    old_time = datetime.now(timezone.utc) - timedelta(seconds=4)
    statuses = [{'code': code, 'status': 'COMPLETE', 'cursor_exhausted': True,
                 'row_count': sum(r['code']==code for r in rows),
                 'captured_at': old_time.isoformat()} for code in params['codes']]
    if contradictory_complete_status:
        statuses[0]['error_code']='TEST_FIXTURE_FAILED'
    oldrows, oldstatuses = deepcopy(rows), deepcopy(statuses)
    if ancestor_rows_change:
        oldrows[0]['backAdjustFactor']='3'
    if ancestor_status_change:
        oldstatuses[0]['captured_at'] = (old_time + timedelta(microseconds=1)).isoformat()
    begin = old_time + timedelta(seconds=1) if ancestor_clock_outside else old_time-timedelta(seconds=1)
    priorpath, priorresult = fixture_factor(prior, oldrows, params, oldstatuses, partial=True,
        missing_raw_clock=True, started=begin)
    # Real fixture prior archive receipt is completed now; advance the final
    # start just after it, while inherited per-code time stays in the past.
    finalstart = datetime.fromisoformat(priorresult['completed_at']) + timedelta(microseconds=1)
    finalpath, finalresult = fixture_factor(active, rows, params, statuses,
        missing_raw_clock=final_missing_raw_clock, started=finalstart)
    finaltarget = active/'results'/'TEST_FIXTURE_FINAL.json'; finaltarget.parent.mkdir()
    finalpath.replace(finaltarget)
    spec = bridge.AncestorCapture(prior, priorpath)
    return active, policy, spec, finaltarget, finalresult


def bar(bundle):
    return next(r for r in bundle.bars if r.instrument_id=='000001.SZ' and str(r.session)=='2026-09-02')


def test_real_partial_trio_fragment_retains_raw_clock_unknown_and_fresh_capability(tmp_path):
    active, policy, spec, _, _ = setup_fixture(tmp_path)
    old, oldaudit = turnover.HistoricalReplayTurnoverAssembler(active,policy).build('2026-09-02')
    bundle, audit = bridge.HistoricalReplayFactorLineageTurnoverAssembler(active,policy,
        ancestor_captures=(spec,)).build('2026-09-02')
    assert bundle.universe == old.universe
    left, right = bar(old), bar(bundle)
    for field in ('open','high','low','close','reference_prev_close','amount_cny','volume_shares',
                  'turnover_fraction','turnover_basis','comparable_open','comparable_high',
                  'comparable_low','comparable_close','comparable_basis','limit_verified'):
        assert getattr(left,field) == getattr(right,field)
    assert audit['turnover_observations'] == oldaudit['turnover_observations']
    assert audit['factor_lineage']['counts'] == {'PROVEN_GENUINE_PARTIAL_ANCESTOR_FRAGMENT':3}
    assert audit['factor_lineage']['original_out_of_parent_gap_count']==3
    assert audit['factor_lineage']['old_capability_restored'] is False
    assert audit['archived_point_in_time'] is False
    parent = audit['factor_lineage']['ancestor_archives'][0]
    assert parent['status']=='PARTIAL' and parent['pagination_complete'] is False
    assert parent['RAW_clock_status']=='UNKNOWN_MISSING_RAW_FIRST_SEEN_AT'
    assert 'ARCHIVE_PROXY' in parent['clock_role']
    fragments = [r for r in bundle.receipts if r.field_version==bridge.VERSION]
    assert fragments and all(r.historical_time_proof is None for r in fragments)
    assert all(r.first_seen_at == datetime.fromisoformat(audit['factor_lineage']['issued_at']) for r in fragments)
    archived = [r for r in bundle.receipts if r.source_type=='original_partial_factor_archive_proxy']
    assert archived and all(r.status=='PARTIAL' and r.parameters['original_receipt_row_count'] is None for r in archived)
    resolver = EvidenceResolver(bundle.receipts, cutoff=datetime.now(timezone.utc),
        coverage_type=bundle.coverage_type, sessions=bundle.sessions, observation_date=bundle.observation_date)
    assert resolver.resolve(right,instrument_id=right.instrument_id,session=right.session,require_session=True).allowed
    assert resolver.receipt_resolution(archived[0], require_complete=True).reason_codes == ('SOURCE_NOT_COMPLETE',)
    historical = EvidenceResolver(bundle.receipts, cutoff=datetime(2026,9,2,22,tzinfo=timezone.utc),
        coverage_type=bundle.coverage_type, sessions=bundle.sessions, observation_date=bundle.observation_date)
    assert historical.resolve(right,instrument_id=right.instrument_id,session=right.session,require_session=True).allowed is False


@pytest.mark.parametrize('kind',['absent','different_rows','different_status','outside_clock'])
def test_unproven_lineage_retains_full_pool_raw_turn_and_unknown_comparable(tmp_path,kind):
    active,policy,spec,_,_ = setup_fixture(tmp_path,
        ancestor_rows_change=kind=='different_rows', ancestor_status_change=kind=='different_status',
        ancestor_clock_outside=kind=='outside_clock')
    old,_ = turnover.HistoricalReplayTurnoverAssembler(active,policy).build('2026-09-02')
    bundle,audit = bridge.HistoricalReplayFactorLineageTurnoverAssembler(active,policy,
        ancestor_captures=() if kind=='absent' else (spec,)).build('2026-09-02')
    assert bundle.universe == old.universe and len(bundle.bars)==len(old.bars)
    assert bar(bundle).close == bar(old).close and bar(bundle).amount_cny == bar(old).amount_cny
    assert bar(bundle).turnover_fraction == bar(old).turnover_fraction
    assert bar(bundle).comparable_close is None and bar(bundle).comparable_basis is None
    assert bar(bundle).quality=='PARTIAL'
    assert audit['factor_lineage']['counts']['UNKNOWN'] >= 1
    old_cutoff = EvidenceResolver(bundle.receipts, cutoff=datetime(2026,9,2,22,tzinfo=timezone.utc),
        coverage_type=bundle.coverage_type, sessions=bundle.sessions, observation_date=bundle.observation_date)
    assert not old_cutoff.resolve(bar(bundle),instrument_id='000001.SZ',
                                  session=bar(bundle).session,require_session=True).allowed


def test_final_missing_raw_first_seen_still_rejected_by_frozen_gate(tmp_path):
    active,policy,spec,_,_ = setup_fixture(tmp_path,final_missing_raw_clock=True)
    with pytest.raises(ValueError,match='MISSING:RAW.first_seen_at'):
        bridge.HistoricalReplayFactorLineageTurnoverAssembler(active,policy,ancestor_captures=(spec,))


@pytest.mark.parametrize('mutation',['raw','receipt','wrapper','request_id','implementation','pagination','field_version'])
def test_genuine_ancestor_whole_contract_tamper_is_rejected(tmp_path,mutation):
    active,policy,spec,_,_ = setup_fixture(tmp_path)
    value=json.loads(spec.result_path.read_text()); receipt=value['receipts'][0]
    if mutation in ('raw','receipt'):
        key='content_hash' if mutation=='raw' else 'receipt_id'
        suffix='.raw.json' if mutation=='raw' else '.receipt.json'
        (spec.capture_root/'cache/raw'/(receipt[key]+suffix)).write_text('{}')
    else:
        if mutation=='wrapper':value['coverage']['security_statuses'][0]['row_count']+=1
        elif mutation=='request_id':value['request_id']='0'*64
        elif mutation=='implementation':value['implementation_source_hashes']['provider_requests.py']='0'*64
        elif mutation=='pagination':value['pagination_complete']=True
        else:value['field_version']='TEST_FIXTURE_UNREGISTERED_VERSION'
        spec.result_path.write_text(json.dumps(value))
    with pytest.raises((ValueError,KeyError)):
        bridge.HistoricalReplayFactorLineageTurnoverAssembler(active,policy,ancestor_captures=(spec,))


def test_changed_original_after_constructor_is_rejected(tmp_path):
    active,policy,spec,_,_ = setup_fixture(tmp_path)
    instance=bridge.HistoricalReplayFactorLineageTurnoverAssembler(active,policy,ancestor_captures=(spec,))
    spec.result_path.write_text(spec.result_path.read_text()+' ')
    with pytest.raises(ValueError,match='SOURCE_CHANGED'):
        instance.build('2026-09-02')


def test_factor_scope_end_and_inline_only_guards_are_unchanged(tmp_path):
    active,policy,spec,_,_ = setup_fixture(tmp_path,factor_end='2026-09-01')
    instance=bridge.HistoricalReplayFactorLineageTurnoverAssembler(active,policy,ancestor_captures=(spec,))
    bundle,audit=instance.build('2026-09-02')
    assert bar(bundle).comparable_close is None
    assert any(g['reason']=='FACTOR_CAPTURE_END_BEFORE_SIGNAL' for g in audit['gaps'])
    with pytest.raises(ValueError,match='INLINE_ONLY'):
        instance.build('2026-09-02',history_store=object())


def test_current_parent_fragments_do_not_require_ancestors(tmp_path):
    active,policy,_,path,original=setup_fixture(tmp_path)
    statuses=deepcopy(original['coverage']['security_statuses'])
    begin=datetime.now(timezone.utc)-timedelta(seconds=1)
    for status in statuses:status['captured_at']=begin.isoformat()
    path.unlink()
    newpath,_=fixture_factor(active,original['rows'],original['parameters'],statuses,
                            started=begin,nonnull_count=True)
    newpath.replace(path)
    bundle,audit=bridge.HistoricalReplayFactorLineageTurnoverAssembler(active,policy).build('2026-09-02')
    assert bar(bundle).comparable_close is not None
    assert audit['factor_lineage']['counts']=={'PROVEN_FINAL_PARENT_FRAGMENT':3}
    assert audit['factor_lineage']['original_out_of_parent_gap_count']==0


def test_future_original_archive_cannot_be_lowered_to_current_issuance(tmp_path):
    active,policy,_,path,original=setup_fixture(tmp_path)
    statuses=deepcopy(original['coverage']['security_statuses'])
    begin=datetime.now(timezone.utc)+timedelta(hours=23)
    for status in statuses:status['captured_at']=begin.isoformat()
    path.unlink()
    newpath,_=fixture_factor(active,original['rows'],original['parameters'],statuses,
                            started=begin,future_seen=True)
    newpath.replace(path)
    instance=bridge.HistoricalReplayFactorLineageTurnoverAssembler(active,policy)
    with pytest.raises(ValueError,match='ARCHIVE_AFTER_ACTUAL_ISSUANCE'):
        instance.build('2026-09-02')


def test_saved_checkpoint_alias_is_not_an_ancestor_archive(tmp_path):
    active,policy,spec,_,_=setup_fixture(tmp_path)
    copied=spec.capture_root/'TEST_FIXTURE_worker-partial.json'
    copied.write_bytes(spec.result_path.read_bytes())
    with pytest.raises(ValueError,match='ANCESTOR_ARCHIVE_WRAPPER_REQUIRED'):
        bridge.HistoricalReplayFactorLineageTurnoverAssembler(active,policy,
            ancestor_captures=(bridge.AncestorCapture(spec.capture_root,copied),))


def test_complete_per_security_label_with_failure_field_is_not_proof(tmp_path):
    active,policy,spec,_,_=setup_fixture(tmp_path,contradictory_complete_status=True)
    bundle,audit=bridge.HistoricalReplayFactorLineageTurnoverAssembler(active,policy,
        ancestor_captures=(spec,)).build('2026-09-02')
    assert bar(bundle).comparable_close is None
    assert audit['factor_lineage']['counts']['UNKNOWN']==1


def test_mutating_cached_ancestor_cannot_replace_authenticated_bytes(tmp_path):
    active,policy,spec,_,_=setup_fixture(tmp_path)
    instance=bridge.HistoricalReplayFactorLineageTurnoverAssembler(active,policy,ancestor_captures=(spec,))
    instance._ancestors[0]['statuses']['sz.000001']['captured_at']='2099-01-01T00:00:00+00:00'
    with pytest.raises(ValueError,match='CACHED_ORIGINAL_CONTEXT_CHANGED'):
        instance.build('2026-09-02')
