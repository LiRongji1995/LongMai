"""New native adapter boundary tests; all captures/docs are synthetic TEST_FIXTURE.

These tests do not use DB, network, real market rows or the frozen R12 CLI.
"""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path

import pytest

from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.historical_replay import HistoricalReplayAssembler, SH
from stockfish_core.yaogu import historical_replay_turnover as adapter
from stockfish_core.yaogu import short_horizon_strength as r1
from stockfish_core.yaogu import short_horizon_participation as r2
from tests.r05_product.test_historical_replay_adapter import (
    checkpoint, fixture_capture, move_capture_to_request_queue_layout,
    move_result_to_request_queue_results, write_capture,
)


def add_turns(root, turns, *, day='2026-09-02', provider='baostock', field_version=None):
    path, original = checkpoint(root, 'market_daily', day)
    rows = [dict(row) for row in original['rows']]
    for index, row in enumerate(rows):
        if index < len(turns):
            row['turn'] = turns[index]
    if field_version is None:
        return write_capture(root, 'market_daily', original['parameters'], rows, provider=provider)
    # Archive a genuinely different fixture schema, not a hand-edited receipt.
    from stockfish_core.yaogu.providers import archive_receipt
    raw = {key: original[key] for key in ('status', 'pagination_complete', 'coverage',
           'first_seen_at', 'gaps', 'failure_category', 'error_code', 'diagnostics', 'source_url')}
    raw.update(rows=rows, field_version=field_version)
    receipt = archive_receipt(root/'cache/raw', provider, 'market_daily', original['parameters'], raw,
        status='COMPLETE', pagination_complete=True, field_version=field_version,
        first_seen_at=raw['first_seen_at'])
    result = {**raw, 'provider': provider, 'endpoint': 'market_daily',
              'parameters': original['parameters'], 'request_id': original['request_id'],
              'receipts': [receipt], 'request_receipt_id': receipt['receipt_id'],
              'completed_at': receipt['ingested_at']}
    path.write_text(json.dumps(result), encoding='utf-8')
    return path, result


def unchanged_except_turn(base, enriched):
    left, right = base.model_dump(mode='json'), enriched.model_dump(mode='json')
    for rows in (left['bars'], right['bars']):
        for row in rows:
            row.pop('turnover_fraction'); row.pop('turnover_basis')
    assert left == right


def native_base_and_enriched(root, policy, day, **kwargs):
    assembly = adapter.HistoricalReplayTurnoverAssembler(root, policy)
    base, base_audit = HistoricalReplayAssembler.build(assembly._base, day, **kwargs)
    def same_build(owner, actual_day, **actual_kwargs):
        assert owner is assembly._base and actual_day == day
        assert actual_kwargs['history_sessions'] == kwargs.get('history_sessions',26)
        assert actual_kwargs['include_security_history'] == kwargs.get('include_security_history',False)
        return base, base_audit
    # The fully verified native object is held constant for exact before/after
    # equality, including its newly generated proof.verified_at. Other tests
    # below exercise an unpatched real assembler.build as well.
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(HistoricalReplayAssembler,'build',same_build)
        enriched, audit = assembly.build(day, **kwargs)
    return base, enriched, audit


@pytest.mark.parametrize('source,expected', [('0', Fraction(0)), ('5.000000', Fraction(1,20)),
    ('4.999999', Fraction(4999999,100000000)), ('5.000001', Fraction(5000001,100000000)),
    ('5.709700', Fraction(57097,1000000)), ('1000', Fraction(10))])
def test_exact_source_percent_and_native_threshold_boundary(tmp_path, source, expected):
    policy = fixture_capture(tmp_path)
    add_turns(tmp_path, [source])
    base, bundle, audit = native_base_and_enriched(tmp_path, policy, '2026-09-02')
    row = next(b for b in bundle.bars if b.instrument_id == '000001.SZ' and str(b.session) == '2026-09-02')
    assert Fraction(str(row.turnover_fraction)) == expected
    assert row.turnover_basis == adapter.TURNOVER_BASIS
    observed = next(r for r in audit['turnover_observations']
                    if r['instrument_id'] == row.instrument_id and r['session'] == str(row.session))
    assert observed['exact_fraction'] == {'numerator': str(expected.numerator), 'denominator': str(expected.denominator)}
    outcome = r2.evaluate_participation({'window_sessions': [str(row.session)], 'observations': [
        {'session': str(row.session), 'status': 'VALID', 'turnover_fraction': row.turnover_fraction}]})
    assert outcome['status'] == ('PASS' if expected >= Fraction(1,20) else 'UNKNOWN')
    unchanged_except_turn(base, bundle)


@pytest.mark.parametrize('source,reason', [(None,'MISSING'), ('','MISSING'), ('NaN','INVALID'),
    ('Infinity','INVALID'), ('-1','INVALID'), ('1000.000001','INVALID'), ('true','INVALID'),
    ('4.999999999999999999999999','ROUNDTRIP_NOT_EXACT'), ('1e-1000000','REPRESENTATION_UNSUPPORTED')])
def test_missing_invalid_or_lossy_turn_is_unknown_never_imputed_zero(tmp_path, source, reason):
    policy = fixture_capture(tmp_path)
    add_turns(tmp_path, [source])
    bundle, audit = adapter.HistoricalReplayTurnoverAssembler(tmp_path, policy).build('2026-09-02')
    row = next(b for b in bundle.bars if b.instrument_id == '000001.SZ' and str(b.session) == '2026-09-02')
    observed = next(r for r in audit['turnover_observations']
                    if r['instrument_id'] == row.instrument_id and r['session'] == str(row.session))
    assert row.turnover_fraction is None and row.turnover_basis is None
    assert observed['status'] == 'UNKNOWN' and reason in observed['reason_code']
    if 'ROUNDTRIP' in reason:
        assert observed['exact_fraction'] is not None


@pytest.mark.parametrize('provider,version', [('other_vendor',None), ('baostock','TEST_FIXTURE_UNREGISTERED_V2')])
def test_unknown_provider_or_field_schema_does_not_grant_turnover_basis(tmp_path, provider, version):
    policy = fixture_capture(tmp_path)
    add_turns(tmp_path, ['5'], provider=provider, field_version=version)
    bundle, audit = adapter.HistoricalReplayTurnoverAssembler(tmp_path, policy).build('2026-09-02')
    observed = [r for r in audit['turnover_observations'] if r['session'] == '2026-09-02']
    assert observed and all(r['reason_code'] == 'TURNOVER_PROVIDER_FIELD_BASIS_UNREGISTERED' for r in observed)
    assert all(b.turnover_fraction is None for b in bundle.bars)


@pytest.mark.parametrize('mutation', ['raw','receipt','result','new_result','document','cached_rows'])
def test_original_integrity_changes_after_constructor_are_rejected(tmp_path, mutation):
    policy = fixture_capture(tmp_path)
    path, original = add_turns(tmp_path, ['5'])
    assembly = adapter.HistoricalReplayTurnoverAssembler(tmp_path, policy)
    receipt = original['receipts'][0]
    if mutation == 'raw':
        (tmp_path/'cache/raw'/(receipt['content_hash']+'.raw.json')).write_text('{}')
    elif mutation == 'receipt':
        (tmp_path/'cache/raw'/(receipt['receipt_id']+'.receipt.json')).write_text('{}')
    elif mutation == 'result':
        original['rows'][0]['turn'] = '99'; path.write_text(json.dumps(original))
    elif mutation == 'new_result':
        (tmp_path/'results').mkdir(); (tmp_path/'results/new.json').write_text('{}')
    elif mutation == 'document':
        Path(policy['source_documents']['baostock-DailyUpdates.md']['path']).write_text('changed TEST_FIXTURE')
    else:
        assembly._base.raw[date(2026,9,2)]['rows'][0]['turn'] = '99'
    with pytest.raises((ValueError, KeyError), match='MISMATCH|CHANGED'):
        assembly.build('2026-09-02')


@pytest.mark.parametrize('mutation', ['wrong_date','wrong_code','wrong_adjustment','request_adjustment'])
def test_security_business_day_adjustment_cannot_be_borrowed_from_another_parent(tmp_path, mutation):
    policy = fixture_capture(tmp_path)
    _, original = checkpoint(tmp_path, 'market_daily', '2026-09-02')
    rows = [dict(r, turn='5') for r in original['rows']]
    parameters = dict(original['parameters'])
    if mutation == 'wrong_date': rows[0]['date'] = '2026-09-01'
    elif mutation == 'wrong_adjustment': rows[0]['adjustflag'] = '2'
    elif mutation == 'wrong_code': rows[0]['code'] = rows[1]['code']
    else: parameters['adjustflag'] = '2'
    # Same business-date result replaces only its original result file.
    path, _ = checkpoint(tmp_path, 'market_daily', '2026-09-02'); path.unlink()
    write_capture(tmp_path, 'market_daily', parameters, rows)
    with pytest.raises(ValueError, match='MISMATCH|DUPLICATE'):
        adapter.HistoricalReplayTurnoverAssembler(tmp_path, policy).build('2026-09-02')


def test_old_base_receipts_clocks_scope_and_factor_guards_are_unchanged(tmp_path):
    policy = fixture_capture(tmp_path, factor_end='2026-09-01')
    add_turns(tmp_path, ['5'])
    base, bundle, after = native_base_and_enriched(tmp_path,policy,'2026-09-02',include_security_history=True)
    unchanged_except_turn(base,bundle)
    assert all(b.comparable_close is None and b.comparable_basis is None for b in bundle.bars)
    assert any(row['reason']=='FACTOR_CAPTURE_END_BEFORE_SIGNAL' for row in after['gaps'])
    assert bundle.universe.complete is False
    assert {s.instrument_id:s.board for s in bundle.universe.securities}['600000.SH'] == 'UNKNOWN'
    assert all(b.limit_verified is False and b.limit_up is None and b.limit_down is None for b in bundle.bars)
    assert after['normalizer_version'] == adapter.VERSION
    assert after['base_normalizer_version'] == adapter.BASE_VERSION
    assert after['archived_point_in_time'] is False
    assert after['adapter_source_identity']['source_hash'] == adapter.adapter_source_identity()['source_hash']


def test_durable_archive_layout_is_authenticated_without_DB(tmp_path):
    policy = fixture_capture(tmp_path)
    path, original = add_turns(tmp_path, ['5'])
    move_capture_to_request_queue_layout(tmp_path, original)
    move_result_to_request_queue_results(tmp_path, path)
    bundle, audit = adapter.HistoricalReplayTurnoverAssembler(tmp_path, policy).build('2026-09-02')
    assert any(r['status']=='VALID' for r in audit['turnover_observations'])
    assert bundle.history_artifact_ref is None


def test_persistent_history_requires_separate_version_before_writer_invocation(tmp_path):
    policy = fixture_capture(tmp_path)
    class ForbiddenWriter:
        def writer(self):
            raise AssertionError('must not reach a DB/artifact writer')
    with pytest.raises(ValueError, match='INLINE_ONLY'):
        adapter.HistoricalReplayTurnoverAssembler(tmp_path, policy).build(
            '2026-09-02', history_store=ForbiddenWriter())


def strength_capture(root, *, coherent_factors=True):
    """85 source-calendar sessions and 25 complete, independently archived tails."""
    policy = fixture_capture(root)
    if coherent_factors:
        # The initial synthetic helper's factor=1 must not conflict with the
        # explicit factor=2 tail below. Preserve the old conflict as a separate
        # negative test, using genuine archived fixture rows in both cases.
        _, old = checkpoint(root,'adjustment_factors')
        write_capture(root,'adjustment_factors',old['parameters'],[
            {**row,'backAdjustFactor':'2','foreAdjustFactor':'1','adjustFactor':'2'}
            for row in old['rows']])
    sessions, day = [], date(2026,5,11)
    while len(sessions) < 85:
        if day.weekday() < 5: sessions.append(day)
        day += timedelta(days=1)
    codes = ['sz.000001','sz.000002','sz.000003','sz.000004']
    write_capture(root, 'calendar', {'start_date':'2010-01-01','end_date':str(sessions[-1])},
                  [{'calendar_date':str(day),'is_trading_day':'1'} for day in sessions])
    write_capture(root, 'stock_basic_all', {}, [{'code':code,'ipoDate':'2010-01-01',
        'outDate':'','type':'1','status':'1','code_name':'TEST_FIXTURE_CURRENT_UNUSED'} for code in codes])
    tails = [('10','10','10','10','10','11'), ('10','11','12.1','13.31','14.641','16.1051'),
             ('10','11','12.1','13.31','14.641','13.8'), ('10','10.02','10.03','10.05','10.10','10.2')]
    closes = [[Decimal('10')]*79 + list(map(Decimal, tail)) for tail in tails]
    for index, day in enumerate(sessions[-25:], start=60):
        write_capture(root, 'master_all', {'date':str(day)}, [{'code':code,
            'code_name':'TEST_FIXTURE_DATED','tradeStatus':'1'} for code in codes])
        rows = []
        for offset, code in enumerate(codes):
            close, previous = closes[offset][index], closes[offset][index-1]
            high, low = max(previous,close), min(previous,close)
            if high == low: high,low = close+Decimal('.1'),close-Decimal('.1')
            turn = '5.000000' if offset == 0 and index == 84 else '1.000000'
            if offset == 2 and index >= 80: turn = ''
            rows.append({'code':code,'date':str(day),'adjustflag':'3','open':str(previous),
                'high':str(high),'low':str(low),'close':str(close),'preclose':str(previous),
                'amount':'150000000' if offset == 0 and index == 84 else '100000000' if offset == 0 else '300000000',
                'volume':'1000000','turn':turn,'isST':'0','tradestatus':'1'})
        write_capture(root, 'market_daily', {'date':str(day),'adjustflag':'3'}, rows)
    write_capture(root,'adjustment_factors',{'codes':codes,'start_date':'1990-01-01','end_date':str(sessions[-1])},
                  [{'code':code,'dividOperateDate':event,'backAdjustFactor':'2',
                    'foreAdjustFactor':'1','adjustFactor':'2'}
                   for code in codes for event in ('2010-01-01',str(sessions[-1]))])
    return policy, str(sessions[-1])


def test_native_r1_selection_is_unchanged_r2_uses_only_new_verified_turn(tmp_path):
    policy, day = strength_capture(tmp_path)
    assembly = adapter.HistoricalReplayTurnoverAssembler(tmp_path, policy)
    # Pin one native base object for an exact unchanged-output comparison.
    base, base_audit = HistoricalReplayAssembler.build(assembly._base, day, history_sessions=25)
    original_build = HistoricalReplayAssembler.build
    def same_build(owner, *args, **kwargs):
        assert owner is assembly._base
        return base, base_audit
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(HistoricalReplayAssembler, 'build', same_build)
        bundle, audit = assembly.build(day, history_sessions=25)
    unchanged_except_turn(base, bundle)
    assert audit['base_input_hash'] == digest(base) and audit['input_hash'] == digest(bundle)
    cutoff = datetime.combine(date.fromisoformat(day), time(20,30), SH)
    left, right = r1.run_short_horizon_strength(base, cutoff=cutoff), r1.run_short_horizon_strength(bundle, cutoff=cutoff)
    assert left['research_selected_ids'] == right['research_selected_ids']
    assert left['research_selected_ids'] == ['000001.SZ','000002.SZ','000003.SZ']
    assert left['qualification'] == right['qualification']
    assert left['market_benchmarks'] == right['market_benchmarks']
    for a,b in zip(left['items'],right['items']):
        a,b = dict(a),dict(b)
        a.pop('turnover_fraction'); b.pop('turnover_fraction')
        assert a == b
    new_r2 = r2.run_short_horizon_strength(bundle, cutoff=cutoff)
    old_r2 = r2.run_short_horizon_strength(base, cutoff=cutoff)
    assert old_r2['research_selected_ids'] == []
    assert new_r2['research_selected_ids'] == ['000001.SZ']
    items = {row['instrument_id']:row for row in new_r2['items']}
    assert items['000001.SZ']['participation']['status'] == 'PASS'
    assert items['000002.SZ']['participation']['status'] == 'FAIL'
    assert items['000003.SZ']['participation']['status'] == 'UNKNOWN'
    assert new_r2['formal_readiness'] == 'NOT_FORMAL_READY'
    assert new_r2['market_edge'] == 'NOT_ESTABLISHED'
    assert new_r2['top_ids'] == []
    assert HistoricalReplayAssembler.build is original_build


def test_conflicting_authenticated_factor_originals_stay_unknown_after_turnover_enrichment(tmp_path):
    policy,day = strength_capture(tmp_path,coherent_factors=False)
    base,bundle,audit = native_base_and_enriched(tmp_path,policy,day,history_sessions=25)
    unchanged_except_turn(base,bundle)
    rows = [b for b in bundle.bars if b.instrument_id=='000001.SZ']
    assert rows and all(b.comparable_close is None and b.comparable_basis is None for b in rows)
    assert any(row['instrument_id']=='000001.SZ' and 'CONFLICT' in row['reason'] for row in audit['gaps'])
    cutoff = datetime.combine(date.fromisoformat(day),time(20,30),SH)
    result = r1.run_short_horizon_strength(bundle,cutoff=cutoff)
    assert '000001.SZ' not in result['research_selected_ids']
    assert result['research_selected_ids']==['000002.SZ','000003.SZ']


@pytest.mark.parametrize('changed',['historical_replay.py','historical_replay_turnover.py'])
def test_actual_source_bytes_and_version_binding_cannot_be_replaced_by_JSON_claims(tmp_path,monkeypatch,changed):
    policy = fixture_capture(tmp_path)
    assembly = adapter.HistoricalReplayTurnoverAssembler(tmp_path,policy)
    from stockfish_core.yaogu import historical_replay as frozen
    actual_hash = frozen.file_hash
    def changed_hash(path):
        return '0'*64 if Path(path).name==changed else actual_hash(path)
    monkeypatch.setattr(frozen,'file_hash',changed_hash)
    with pytest.raises(ValueError,match='SOURCE_HASH_MISMATCH|SOURCE_CHANGED'):
        assembly.build('2026-09-02')


@pytest.mark.parametrize('changed',['raw','receipt'])
def test_consumed_original_bytes_changed_during_build_are_rejected_at_return(tmp_path,monkeypatch,changed):
    policy = fixture_capture(tmp_path)
    _,original = add_turns(tmp_path,['5'])
    assembly = adapter.HistoricalReplayTurnoverAssembler(tmp_path,policy)
    receipt = original['receipts'][0]
    target = tmp_path/'cache/raw'/((receipt['content_hash']+'.raw.json') if changed=='raw'
                                 else (receipt['receipt_id']+'.receipt.json'))
    actual_assert = assembly._assert_fixed_inputs
    calls = []
    def mutate_at_terminal_context_check():
        actual_assert(); calls.append(True)
        if len(calls)==2:
            # Result JSON and all in-memory rows remain unchanged. This mutation
            # follows the authenticated RAW read but precedes returning output.
            target.write_text('{}')
    monkeypatch.setattr(assembly,'_assert_fixed_inputs',mutate_at_terminal_context_check)
    with pytest.raises(ValueError,match='ORIGINAL_RAW_HASH_MISMATCH|ORIGINAL_RECEIPT_IDENTITY_MISMATCH'):
        assembly.build('2026-09-02')
    assert len(calls)==2


def test_native_real_build_preserves_publication_visibility_gate(tmp_path):
    policy,day = strength_capture(tmp_path)
    bundle,audit = adapter.HistoricalReplayTurnoverAssembler(tmp_path,policy).build(day,history_sessions=25)
    assert len(bundle.bars)==100 and audit['turnover_counts']['valid']==95
    before_release = datetime.combine(date.fromisoformat(day),time(17,29),SH)
    before = r2.run_short_horizon_strength(bundle,cutoff=before_release)
    assert before['eligible_pool_ids']==[] and before['research_selected_ids']==[]
    after_release = datetime.combine(date.fromisoformat(day),time(20,30),SH)
    after = r2.run_short_horizon_strength(bundle,cutoff=after_release)
    assert after['research_selected_ids']==['000001.SZ']
    assert after['top_ids']==[] and after['market_edge']=='NOT_ESTABLISHED'


def archive_header_fixture(root, *, mutate=None, result_seen=False, request_adjustment=None):
    """Actual R03 envelope shape: date-only request, RAW seen, result start/end.

    All bytes and receipt semantic IDs are freshly archived TEST_FIXTURE. Header
    counterexamples are initial inconsistencies, not unbound post-hash edits.
    """
    from stockfish_core.yaogu.providers import archive_receipt
    path, old = checkpoint(root,'market_daily','2026-09-02')
    seen = datetime.now(timezone.utc)-timedelta(seconds=1)
    payload = {key:old[key] for key in ('status','pagination_complete','coverage',
        'field_version','gaps','failure_category','error_code','diagnostics','source_url')}
    payload.update(rows=[dict(row,turn='5') for row in old['rows']],first_seen_at=seen.isoformat())
    envelope = {'started_at':(seen-timedelta(milliseconds=30)).isoformat()}
    if result_seen: envelope['first_seen_at']=seen.astimezone(timezone(timedelta(hours=9))).isoformat()
    receipt_seen = seen.isoformat()
    if mutate:
        receipt_seen = mutate('before_archive',payload,envelope,receipt_seen) or receipt_seen
    parameters = {'date':'2026-09-02'}
    if request_adjustment is not None: parameters['adjustflag']=request_adjustment
    receipt = archive_receipt(root/'cache/raw','baostock','market_daily',parameters,payload,
        status='COMPLETE',pagination_complete=True,field_version=payload['field_version'],first_seen_at=receipt_seen)
    envelope['completed_at'] = (datetime.fromisoformat(receipt['ingested_at'])+timedelta(milliseconds=3)).isoformat()
    if mutate: mutate('after_archive',payload,envelope,receipt)
    result = {**envelope,'status':payload['status'],'pagination_complete':payload['pagination_complete'],
        'rows':payload['rows'],'coverage':payload['coverage'],'field_version':payload['field_version'],
        'gaps':payload['gaps'],'failure_category':payload['failure_category'],'error_code':payload['error_code'],
        'diagnostics':payload['diagnostics'],'provider':'baostock','endpoint':'market_daily',
        'parameters':parameters,'request_id':old['request_id'],'receipts':[receipt],
        'request_receipt_id':receipt['receipt_id']}
    path.write_text(json.dumps(result),encoding='utf-8')
    return result,payload


@pytest.mark.parametrize('result_seen',[False,True])
def test_real_date_only_capture_shape_accepts_completion_after_ingest_and_equivalent_offsets(tmp_path,result_seen):
    policy=fixture_capture(tmp_path)
    def offset_raw(stage,payload,envelope,clock):
        if stage=='before_archive':
            payload['first_seen_at']=datetime.fromisoformat(payload['first_seen_at']).astimezone(
                timezone(timedelta(hours=8))).isoformat()
    original,payload=archive_header_fixture(tmp_path,mutate=offset_raw,result_seen=result_seen)
    base,bundle,audit=native_base_and_enriched(tmp_path,policy,'2026-09-02')
    unchanged_except_turn(base,bundle)
    observed=[b for b in bundle.bars if str(b.session)=='2026-09-02']
    assert observed and all(b.turnover_fraction==.05 and b.turnover_basis==adapter.TURNOVER_BASIS for b in observed)
    clocks=audit['original_capture_clock_contract'][original['request_receipt_id']]
    assert clocks['raw_first_seen_at'].endswith('+08:00')
    assert clocks['receipt_first_seen_at'].endswith('+00:00')
    assert clocks['result_first_seen_present'] is result_seen
    assert datetime.fromisoformat(clocks['result_completed_at'])>datetime.fromisoformat(clocks['receipt_ingested_at'])
    assert clocks['source_clocks_modified'] is False


@pytest.mark.parametrize('mutation', ['raw_result_future_seen','result_future_seen','raw_naive',
    'result_naive','raw_missing','raw_null','result_null','start_after_seen','complete_before_ingest',
    'start_naive','complete_naive','start_null','complete_invalid'])
def test_initial_self_consistent_archive_clock_conflicts_or_invalid_capture_order_are_rejected(tmp_path,mutation):
    policy=fixture_capture(tmp_path)
    def mutate(stage,payload,envelope,clock):
        if stage=='before_archive':
            if mutation=='raw_result_future_seen':
                payload['first_seen_at']='2099-01-01T00:00:00+00:00'
                envelope['first_seen_at']=payload['first_seen_at']
            elif mutation=='result_future_seen':envelope['first_seen_at']='2099-01-01T00:00:00+00:00'
            elif mutation=='raw_naive':payload['first_seen_at']='2026-10-10T09:00:00'
            elif mutation=='result_naive':envelope['first_seen_at']='2026-10-10T09:00:00'
            elif mutation=='raw_missing':payload.pop('first_seen_at')
            elif mutation=='raw_null':payload['first_seen_at']=None
            elif mutation=='result_null':envelope['first_seen_at']=None
            elif mutation=='start_after_seen':
                envelope['started_at']=(datetime.fromisoformat(payload['first_seen_at'])+timedelta(milliseconds=10)).isoformat()
            elif mutation=='start_naive':envelope['started_at']='2026-10-10T09:00:00'
            elif mutation=='start_null':envelope['started_at']=None
        else:
            if mutation=='complete_before_ingest':
                envelope['completed_at']=(datetime.fromisoformat(clock['ingested_at'])-timedelta(milliseconds=1)).isoformat()
            elif mutation=='complete_naive':envelope['completed_at']='2099-01-01T00:00:00'
            elif mutation=='complete_invalid':envelope['completed_at']='not-an-ISO-clock'
    original,payload=archive_header_fixture(tmp_path,mutate=mutate)
    # Demonstrate archive SHA, rows and receipt semantic ID are internally
    # valid; the new independent header check closes the former counterexample.
    from stockfish_core.yaogu.historical_replay import verify_capture
    assert verify_capture(tmp_path,original)['raw_file_sha256']==original['receipts'][0]['content_hash']
    with pytest.raises(ValueError,match='TURNOVER_ORIGINAL_CAPTURE_CLOCK'):
        adapter.HistoricalReplayTurnoverAssembler(tmp_path,policy).build('2026-09-02')


def test_explicit_null_completion_differs_from_missing_legacy_completion(tmp_path):
    fixture_capture(tmp_path)
    original,payload=archive_header_fixture(tmp_path)
    original.pop('completed_at')
    assert adapter._verify_clock_contract(original,payload)['result_completed_at'] is None
    original['completed_at']=None
    with pytest.raises(ValueError,match='CLOCK_INVALID:RESULT.completed_at'):
        adapter._verify_clock_contract(original,payload)


@pytest.mark.parametrize('request_adjustment',['2','1','',False])
def test_date_only_support_does_not_accept_explicit_conflicting_request_adjustment(tmp_path,request_adjustment):
    policy=fixture_capture(tmp_path)
    archive_header_fixture(tmp_path,request_adjustment=request_adjustment)
    with pytest.raises(ValueError,match='RAW_PARENT_SCOPE_MISMATCH'):
        adapter.HistoricalReplayTurnoverAssembler(tmp_path,policy).build('2026-09-02')
