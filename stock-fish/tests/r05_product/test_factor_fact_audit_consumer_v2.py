"""Explicit isolated TEST_FIXTURE facts/audit/authority; no real market sources."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import pickle
from pathlib import Path

import pytest

from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu import historical_replay_factor_facts as facts
from stockfish_core.yaogu import factor_fact_consumer as consumer
from stockfish_core.yaogu import historical_replay_factor_lineage as frozen_lineage
from stockfish_core.yaogu import historical_replay_turnover as turnover
from stockfish_core.yaogu import short_horizon_strength as r1
from stockfish_core.yaogu.visibility import EvidenceResolver
from tests.r05_product.test_historical_replay_factor_lineage_adapter import setup_fixture, bar
from tests.r05_product.test_historical_replay_adapter import checkpoint, write_capture


def fixture_implementation(root):
    folder = root/'TEST_FIXTURE_CAPTURE_CODE_BYTES'; folder.mkdir()
    values = []
    for name in ('provider_requests.py','provider_requests_worker.py','provider_budget.py'):
        source = Path(facts.__file__).with_name(name)
        target = folder/name; target.write_bytes(source.read_bytes())
        assert frozen_lineage._sha(source) == frozen_lineage._sha(target)
        values.append(target)
    return facts.CaptureImplementationFiles(*values)


def fixture_eligible_calendar(active):
    # Genuine-format synthetic source, enough listing sessions for the original
    # qualification function; no modified eligibility threshold or stock list.
    path, original = checkpoint(active,'calendar'); path.unlink()
    end = datetime(2026,9,4,tzinfo=timezone.utc).date()
    days = [end-timedelta(days=x) for x in range(130,-1,-1)]
    rows = [{'calendar_date':str(d),'is_trading_day':'1' if d.weekday()<5 else '0'} for d in days]
    write_capture(active,'calendar',{'start_date':str(days[0]),'end_date':str(end)},rows)


def build(tmp_path, *, kind=None, factor_end=None):
    active, policy, ancestor, _, _ = setup_fixture(tmp_path,
        ancestor_rows_change=kind=='different_rows', ancestor_status_change=kind=='different_status',
        ancestor_clock_outside=kind=='outside_clock', final_missing_raw_clock=kind=='missing_final_clock',
        contradictory_complete_status=kind=='extra_failure', factor_end=factor_end)
    fixture_eligible_calendar(active)
    implementation = fixture_implementation(tmp_path)
    owner = facts.HistoricalReplayFactorFactsAssembler(active, policy,
        capture_implementation=implementation,
        ancestor_captures=() if kind=='absent' else (ancestor,))
    context = owner.build_context('2026-09-02', registered_worker_deadline=datetime.now(timezone.utc)+timedelta(seconds=90))
    return owner, context, active, policy, ancestor, implementation


def run(context):
    return consumer.run_short_horizon_with_factor_fact_context(context,cutoff=context.actual_cutoff)


def test_genuine_partial_ancestor_supports_strict_R1_fact_and_remains_partial(tmp_path):
    owner, context, active, policy, ancestor, _ = build(tmp_path)
    old, audit = turnover.HistoricalReplayTurnoverAssembler(active,policy).build('2026-09-02')
    assert context.bundle.universe == old.universe
    assert len(context.bundle.bars) == len(old.bars)
    for field in ('open','high','low','close','reference_prev_close','amount_cny','volume_shares',
                  'turnover_fraction','turnover_basis','comparable_open','comparable_high',
                  'comparable_low','comparable_close','comparable_basis','limit_verified'):
        assert getattr(bar(context.bundle),field) == getattr(bar(old),field)
    assert context.audit['turnover_observations'] == audit['turnover_observations']
    assert context.audit['factor_facts']['proof_counts'] == {'PROVEN_GENUINE_PARTIAL_ANCESTOR_FRAGMENT':3}
    assert context.audit['factor_facts']['original_out_of_parent_gap_count'] == 3
    parents = [x for x in context.bundle.receipts if x.parameters.get('parent_role')=='ANCESTOR']
    assert parents and all(p.status=='PARTIAL' and not p.pagination_complete for p in parents)
    assert all(p.parameters['RAW_first_seen_status']=='UNKNOWN_MISSING_RAW_FIRST_SEEN_AT' for p in parents)
    assert all(p.parameters['original_receipt_row_count'] is None for p in parents)
    assert not set(p.receipt_id for p in parents).intersection(bar(context.bundle).source_refs)
    # Strict original resolver still rejects a PARTIAL archive as a value source.
    resolver = EvidenceResolver(context.bundle.receipts,cutoff=context.actual_cutoff,
        coverage_type=context.bundle.coverage_type,sessions=context.bundle.sessions)
    assert resolver.receipt_resolution(parents[0],require_complete=True).reason_codes == ('SOURCE_NOT_COMPLETE',)
    assert context.audit['factor_facts']['owner_manifest']['artifact_version']==facts.OWNER_VERSION
    assert 'CONTENT_ADDRESSED_HISTORY_ARTIFACT_V1' != facts.OWNER_VERSION
    assert context.audit['factor_facts']['owner_manifest']['history_completeness']=='INLINE_ROWS_ONLY_NOT_FULL_HISTORY'
    assert context.audit['factor_facts']['owner_manifest']['partitions']==[]
    assert context.audit['factor_facts']['owner_manifest']['inline_bar_scope']['count']==len(old.bars)==5
    assert context.bundle.history_artifact_hash == digest(context.audit['factor_facts']['owner_manifest'])
    reference = r1.run_short_horizon_strength(old,cutoff=context.actual_cutoff)
    wrapped = run(context)
    assert wrapped['all_original_source_SHA_stat_terminal_equal'] is True
    assert wrapped['old_capability_restored'] is False and wrapped['archived_point_in_time'] is False
    result = wrapped['result']
    assert result['eligible_pool_ids']==['000001.SZ']
    assert len(result['items'])==1
    exact = result['items'][0]['exact_features']
    assert exact['current_raw_present'] is True
    assert exact['current_visibility_valid'] is True
    assert 'SOURCE_NOT_COMPLETE' not in exact['current_source_reason_codes']
    baseline = reference['items'][0]['exact_features']
    for field in ('current_raw_present','current_quality','current_source_bound',
                  'current_visibility_valid','window_coverage','amount','raw','clv',
                  'returns','ma','A','market_benchmark_coverage'):
        assert exact[field]==baseline[field]
    assert exact['window_coverage']=={'selected_25_count':5,'requested_25_count':25,'complete_25':False}
    assert result['fact_coverage']['current_source_unknown']==reference['fact_coverage']['current_source_unknown']==0
    assert result['fact_coverage']['complete_25_window']==reference['fact_coverage']['complete_25_window']==0
    assert wrapped['original_R1_source_identity']==r1.short_horizon_source_identity()
    with pytest.raises(ValueError,match='CLOSED_OR_REUSED'):
        run(context)


@pytest.mark.parametrize('kind',['absent','different_rows','different_status','outside_clock','extra_failure'])
def test_unproven_code_keeps_eligible_denominator_raw_turn_and_unknown_basis(tmp_path,kind):
    owner, context, active, policy, _, _ = build(tmp_path,kind=kind)
    old,_ = turnover.HistoricalReplayTurnoverAssembler(active,policy).build('2026-09-02')
    assert context.bundle.universe==old.universe and len(context.bundle.bars)==len(old.bars)
    assert bar(context.bundle).close==bar(old).close
    assert bar(context.bundle).amount_cny==bar(old).amount_cny
    assert bar(context.bundle).turnover_fraction==bar(old).turnover_fraction
    assert bar(context.bundle).comparable_close is None and bar(context.bundle).quality=='PARTIAL'
    assert context.audit['factor_facts']['proof_counts']['UNKNOWN']>=1
    wrapped=run(context)
    assert wrapped['result']['eligible_pool_ids']==['000001.SZ']
    assert len(wrapped['result']['items'])==1
    assert wrapped['result']['items'][0]['exact_features']['window_coverage']['complete_25'] is False


def test_missing_final_RAW_clock_cannot_use_ancestor_archive_proxy(tmp_path):
    with pytest.raises(ValueError,match='MISSING:RAW.first_seen_at'):
        build(tmp_path,kind='missing_final_clock')


def test_saved_JSON_and_removed_history_ref_cannot_restore_fact_permission(tmp_path):
    owner, context, _, _, _, _ = build(tmp_path)
    saved=context.bundle.model_dump(mode='json')
    restored=MarketInput.model_validate(saved)
    assert restored._history_store is None
    with pytest.raises(Exception,match='HISTORY_ARTIFACT_STORE_NOT_BOUND'):
        r1.run_short_horizon_strength(restored,cutoff=context.actual_cutoff)
    saved['history_artifact_ref']=None; saved['history_artifact_hash']=None
    detached=MarketInput.model_validate(saved)
    resolver=EvidenceResolver(detached.receipts,cutoff=context.actual_cutoff,
        coverage_type=detached.coverage_type,sessions=detached.sessions,observation_date=detached.observation_date)
    proof=resolver.resolve(bar(detached),instrument_id='000001.SZ',session=bar(detached).session,
                           require_session=True,require_complete=True)
    assert proof.allowed is False and proof.reason_codes==('SOURCE_REFERENCE_UNRESOLVED',)
    old_direct=r1.run_short_horizon_strength(detached,cutoff=context.actual_cutoff)
    assert old_direct['eligible_pool_ids']==['000001.SZ']
    assert old_direct['items'][0]['exact_features']['current_visibility_valid'] is False
    # A serialized manifest/description is also not a factory context.
    with pytest.raises(ValueError,match='GENUINE_CONTEXT'):
        consumer.run_short_horizon_with_factor_fact_context(context.audit,cutoff=context.actual_cutoff)


@pytest.mark.parametrize('mutation',['copied_context','bundle','cutoff','graph','audit','reader'])
def test_copied_replaced_context_and_descriptions_are_not_authority(tmp_path,mutation):
    owner, context, _, _, _, _ = build(tmp_path)
    if mutation=='copied_context': changed=replace(context)
    elif mutation=='bundle': changed=replace(context,bundle=context.bundle.model_copy(update={'bars':()}))
    elif mutation=='cutoff': changed=replace(context,actual_cutoff=context.actual_cutoff+timedelta(seconds=1))
    elif mutation=='graph': changed=replace(context,_graph_json='{}')
    elif mutation=='audit': changed=replace(context,_audit_json='{}')
    else:
        context.bundle._history_store=object(); changed=context
    with pytest.raises(ValueError,match='OWNER|READER|CONTEXT'):
        run(changed)


def test_authority_has_no_public_constructor_or_pickle(tmp_path):
    owner, context, _, _, _, _ = build(tmp_path)
    with pytest.raises(ValueError,match='FACTORY_REQUIRED'): facts._Authority()
    with pytest.raises(TypeError,match='NOT_SERIALIZABLE'): pickle.dumps(context._authority)


def test_owner_cannot_read_or_direct_R1_before_activation(tmp_path):
    owner, context, _, _, _, _ = build(tmp_path)
    with pytest.raises(ValueError,match='CLOSED_OR_REUSED'):
        r1.run_short_horizon_strength(context.bundle,cutoff=context.actual_cutoff)
    assert facts._OWNERS[context._authority]['state']=='ISSUED'


def test_expired_registered_deadline_rejects_without_extending_it(tmp_path,monkeypatch):
    owner, context, _, _, _, _ = build(tmp_path)
    # Explicit synthetic clock exercise; never changes real archive clocks.
    monkeypatch.setattr(facts,'_now',lambda:context.registered_worker_deadline+timedelta(microseconds=1))
    with pytest.raises(ValueError,match='EXPIRED'): run(context)


def test_wrong_cutoff_invalidates_single_attempt(tmp_path):
    owner, context, _, _, _, _ = build(tmp_path)
    with pytest.raises(ValueError,match='EXACT_FACTORY_CUTOFF'):
        consumer.run_short_horizon_with_factor_fact_context(context,cutoff=context.actual_cutoff-timedelta(microseconds=1))
    with pytest.raises(ValueError,match='CLOSED_OR_REUSED'): run(context)


@pytest.mark.parametrize('mutation',['ancestor_wrapper','capture_code_snapshot','manifest','drop_archive_parent'])
def test_original_or_audit_owner_mutation_cannot_seal_result(tmp_path,mutation):
    owner, context, _, _, ancestor, implementation = build(tmp_path)
    if mutation=='ancestor_wrapper': ancestor.result_path.write_text(ancestor.result_path.read_text()+' ')
    elif mutation=='capture_code_snapshot': implementation.provider_requests.write_text('TEST_FIXTURE_WRONG_BYTES')
    elif mutation=='manifest': facts._OWNERS[context._authority]['reader']()._manifest['receipts'].pop()
    else:
        removed=tuple(r for r in context.bundle.receipts if r.parameters.get('parent_role')!='ANCESTOR')
        context.bundle.__dict__['receipts']=removed  # Explicit tamper, frozen field public API would refuse.
    with pytest.raises(ValueError,match='SOURCE|MANIFEST|BUNDLE'):
        run(context)
    assert facts._OWNERS[context._authority]['state']=='INVALIDATED'


def test_end_scope_unknown_price_basis_is_not_repaired_by_complete_fact(tmp_path):
    owner, context, _, _, _, _=build(tmp_path,factor_end='2026-09-01')
    assert bar(context.bundle).comparable_close is None
    assert any(g['reason']=='FACTOR_CAPTURE_END_BEFORE_SIGNAL' for g in context.audit['gaps'])
    assert run(context)['result']['eligible_pool_ids']==['000001.SZ']


def test_declared_capture_implementation_requires_real_matching_bytes(tmp_path):
    active,policy,ancestor,_,_=setup_fixture(tmp_path)
    implementation=fixture_implementation(tmp_path)
    implementation.provider_requests.write_text('TEST_FIXTURE_DIFFERENT_IMPLEMENTATION_BYTES')
    with pytest.raises(ValueError,match='PROVIDER_IMPLEMENTATION_CONFLICT'):
        facts.HistoricalReplayFactorFactsAssembler(active,policy,capture_implementation=implementation,
            ancestor_captures=(ancestor,))
    with pytest.raises(ValueError,match='REAL_CAPTURE_IMPLEMENTATION_FILES'):
        facts.HistoricalReplayFactorFactsAssembler(active,policy,capture_implementation={'SHA':'0'*64})


def test_external_or_expired_owner_budget_is_not_accepted(tmp_path):
    owner, context, _, _, _, _ = build(tmp_path)
    with pytest.raises(ValueError,match='STORE_UNSUPPORTED'):
        owner.build_context('2026-09-02',registered_worker_deadline=datetime.now(timezone.utc)+timedelta(seconds=90),history_store=object())
    with pytest.raises(ValueError,match='EXPIRED'):
        owner.build_context('2026-09-02',registered_worker_deadline=datetime.now(timezone.utc)-timedelta(seconds=1))


def test_omitting_declared_whole_source_is_not_allowed(tmp_path):
    owner,context,_,_,ancestor,_=build(tmp_path)
    owner._files.pop(str(ancestor.result_path.resolve()))
    with pytest.raises(ValueError,match='COMPLETE_SOURCE_BINDING_SCOPE_CHANGED'): run(context)
    assert facts._OWNERS[context._authority]['state']=='INVALIDATED'


def test_original_drift_during_unchanged_R1_fails_terminal_seal(tmp_path,monkeypatch):
    owner,context,_,_,ancestor,_=build(tmp_path)
    original=r1.run_short_horizon_strength
    def changed_after_math(*args,**kwargs):
        result=original(*args,**kwargs)
        ancestor.result_path.write_text(ancestor.result_path.read_text()+' ')
        return result
    monkeypatch.setattr(r1,'run_short_horizon_strength',changed_after_math)
    with pytest.raises(ValueError,match='ORIGINAL_WHOLE_SOURCE_CHANGED'): run(context)
    assert facts._OWNERS[context._authority]['state']=='INVALIDATED'
