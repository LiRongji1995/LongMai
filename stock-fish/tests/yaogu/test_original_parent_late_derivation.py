"""Independent archived TEST_FIXTURE clocks; no actual market/DB/selector."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json

import pytest

from stockfish_core.yaogu import original_parent_late_derivation as producer
from stockfish_core.yaogu import providers
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.history_artifacts import digest_market_input
from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report
from stockfish_core.yaogu.provider_requests import FIELD_VERSION
from stockfish_core.yaogu.visibility import EvidenceResolver

DAY = date(2026, 9, 30)
CAPTURED = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
CUTOFF = CAPTURED + timedelta(hours=1)
SESSIONS = (date(2026, 9, 28), date(2026, 9, 29), DAY)
VERSION = 'TEST_FIXTURE_ORIGINAL_PROVIDER_V1'


def write_capture(root, monkeypatch, endpoint, params, rows, *, status='COMPLETE', page=True,
                  seen=CAPTURED, coverage=None, omitted_version=False, raw_seen=None):
    class FixtureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (seen+timedelta(seconds=1)).astimezone(tz or timezone.utc)
    monkeypatch.setattr(providers, 'datetime', FixtureClock)
    payload = {'rows': rows, 'status': status, 'pagination_complete': page,
               'coverage': coverage or {'evidence_class': 'TEST_FIXTURE_ONLY'}, 'gaps': [],
               'first_seen_at': (raw_seen or seen).isoformat()}
    if not omitted_version:
        payload['field_version'] = VERSION
    else:
        payload['error_code'] = 'TEST_FIXTURE_FAILURE'
    receipt = providers.archive_receipt(root/'cache'/'raw', 'baostock', endpoint, params, payload,
        status=status, pagination_complete=page, field_version=FIELD_VERSION if omitted_version else VERSION,
        first_seen_at=seen.isoformat(), source_url='https://fixture.invalid/original')
    result = {**payload, 'provider': 'baostock', 'endpoint': endpoint, 'parameters': params,
              'request_receipt_id': receipt['receipt_id'], 'receipts': [receipt],
              'field_version': receipt['field_version'], 'request_id': 'TEST_FIXTURE_'+receipt['receipt_id'],
              'started_at': (seen-timedelta(seconds=1)).isoformat(),
              'completed_at': (seen+timedelta(seconds=2)).isoformat()}
    target = root/'results'/(receipt['receipt_id']+'.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, sort_keys=True), encoding='utf-8')
    artifact = producer.OriginalCaptureArtifact(root, target, hashlib.sha256(target.read_bytes()).hexdigest())
    return artifact, result


def capture_set(root, monkeypatch, *, factors=True, factor_status='COMPLETE', factor_value='1.5'):
    artifacts, results = [], []
    def add(*args, **kwargs):
        artifact, result = write_capture(root, monkeypatch, *args, **kwargs)
        artifacts.append(artifact); results.append(result)
    add('calendar', {'start_date': str(SESSIONS[0]), 'end_date': str(DAY)},
        [{'calendar_date': str(session), 'is_trading_day': '1'} for session in SESSIONS])
    add('mainboard_sse', {}, [{'证券代码': '600001', '证券简称': 'TEST_FIXTURE_SH', '上市日期': '2010-01-01'}])
    add('master_szse', {}, [{'A股代码': '000001', 'A股简称': 'TEST_FIXTURE_SZ', 'A股上市日期': '2010-01-01', '板块': '主板'}])
    add('master_all', {'date': str(DAY)}, [{'code': code, 'tradeStatus': '1', 'code_name': 'TEST_FIXTURE_DATED'}
                                          for code in ('sh.600001', 'sz.000001')])
    add('market_daily', {'date': str(DAY), 'adjustflag': '3'},
        [{'code': code, 'date': str(DAY), 'adjustflag': '3', 'open': '10', 'high': '11', 'low': '9',
          'close': '10.5', 'preclose': '10', 'volume': '1000', 'amount': '10300', 'turn': '3',
          'isST': '0', 'tradestatus': '1'} for code in ('sh.600001', 'sz.000001')])
    add('delisting_sse', {}, [])
    if factors:
        add('adjustment_factors', {'codes': ['sh.600001', 'sz.000001'], 'start_date': '1990-01-01', 'end_date': str(DAY)},
            [{'code': code, 'dividOperateDate': '2010-01-01', 'backAdjustFactor': factor_value}
             for code in ('sh.600001', 'sz.000001')], status=factor_status, page=factor_status=='COMPLETE',
            coverage={'evidence_class': 'TEST_FIXTURE_ONLY', 'security_statuses': [
                {'code': code, 'status': 'COMPLETE', 'cursor_exhausted': True, 'row_count': 1}
                for code in ('sh.600001', 'sz.000001')]})
    return artifacts, results


def resolver(bundle):
    return EvidenceResolver(bundle.receipts, cutoff=CUTOFF, coverage_type=bundle.coverage_type,
                            sessions=bundle.sessions, observation_date=bundle.observation_date)


def repin(artifact, result):
    artifact.result_path.write_text(json.dumps(result, sort_keys=True), encoding='utf-8')
    return replace(artifact, result_sha256=hashlib.sha256(artifact.result_path.read_bytes()).hexdigest())


def test_original_parent_and_late_derivation_have_distinct_real_clocks_and_identity(tmp_path, monkeypatch):
    artifacts, results = capture_set(tmp_path, monkeypatch)
    files_before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    old, _ = normalize_capture_with_report(results, DAY, CUTOFF, SESSIONS)
    old_hash = digest_market_input(old)
    started = datetime.now(timezone.utc)
    new, audit = producer.normalize_original_parent_asof(artifacts, DAY, CUTOFF)
    finished = datetime.now(timezone.utc)
    assert new.coverage_type == 'RECONSTRUCTED_HISTORICAL'
    assert audit['input_hash'] == digest_market_input(new) != old_hash
    assert audit['normalizer_call_count'] == 1
    assert audit['producer_version'] == producer.VERSION
    assert old_hash == digest_market_input(old)
    assert {path: path.read_bytes() for path in files_before} == files_before
    assert old.receipts == new.receipts
    assert all(started <= row.first_seen_at <= row.ingested_at <= finished
               for row in (*new.bars, *new.universe.securities, new.universe))
    assert all(max(parent.first_seen_at, parent.ingested_at) <= CUTOFF for parent in new.receipts)
    assert new.universe.event_time == max(parent.event_time for parent in new.receipts
                                        if parent.receipt_id in new.universe.source_refs)
    assert resolver(new).resolve(new.universe, session=DAY, require_complete=True).reconstruction_used is True
    for bar in new.bars:
        proof = resolver(new).resolve(bar, instrument_id=bar.instrument_id, session=DAY, require_complete=True)
        assert proof.allowed and proof.reconstruction_used
        assert bar.close == Decimal('10.5') and bar.comparable_close == Decimal('15.75')
    universe_audit = next(row for row in audit['derived_rows'] if row['kind'] == 'UNIVERSE')
    assert universe_audit['derived_row_hash'] == digest(new.universe)
    assert universe_audit['publication_clock_role'] == producer.PUBLICATION_ROLE
    assert not audit['history_bound_or_written'] and not audit['historical_prediction_established']
    assert audit['numeric_basis_link_to_original_history'] == 'NOT_VERIFIED_HERE'
    security_audit = next(row for row in audit['derived_rows'] if row['kind'] == 'SECURITY')
    assert security_audit['field_sources']['board']['role'].endswith('NOT_HISTORICAL_BOARD_REGISTRY')


def test_original_normalizer_default_and_fact_values_remain_unchanged(tmp_path, monkeypatch):
    artifacts, results = capture_set(tmp_path, monkeypatch)
    at = datetime.now(timezone.utc)
    baseline, report_before = normalize_capture_with_report(results, DAY, at, SESSIONS)
    snapshot = digest_market_input(baseline)
    new, _ = producer.normalize_original_parent_asof(artifacts, DAY, CUTOFF)
    repeated, report_after = normalize_capture_with_report(results, DAY, at, SESSIONS)
    assert digest_market_input(repeated) == snapshot and report_after == report_before
    assert all(getattr(baseline.universe, field) == at for field in producer.CLOCKS)
    assert baseline.coverage_type == 'PROSPECTIVE_CAPTURED'
    for old, derived in zip(baseline.bars, new.bars):
        assert old.model_dump(exclude=set(producer.CLOCKS)) == derived.model_dump(exclude=set(producer.CLOCKS))


def test_partial_factor_ancestry_keeps_numeric_fragment_but_strict_parent_unknown(tmp_path, monkeypatch):
    artifacts, _ = capture_set(tmp_path, monkeypatch, factor_status='PARTIAL')
    new, audit = producer.normalize_original_parent_asof(artifacts, DAY, CUTOFF)
    partial = next(parent for parent in new.receipts if parent.dataset == 'adjustment_factors')
    assert partial.status == 'PARTIAL' and partial.pagination_complete is False
    assert all(partial.receipt_id in row.source_refs for row in new.bars)
    assert all(row.comparable_close is not None for row in new.bars)
    assert all(resolver(new).resolve(row, instrument_id=row.instrument_id, session=DAY,
                                    require_complete=True).reason_codes == ('SOURCE_NOT_COMPLETE',) for row in new.bars)
    assert all(not row['parent_status_promoted'] for row in audit['derived_rows'])


def test_missing_comparability_stays_unknown_not_factor_one(tmp_path, monkeypatch):
    artifacts, _ = capture_set(tmp_path, monkeypatch, factors=False)
    new, _ = producer.normalize_original_parent_asof(artifacts, DAY, CUTOFF)
    assert all(row.comparable_basis is None and row.comparable_close is None for row in new.bars)
    assert all(row.limit_verified is False and row.limit_applicable is None for row in new.bars)
    assert new.themes == () and new.funds == ()


def test_conflicting_real_factor_snapshots_keep_unknown_prices(tmp_path, monkeypatch):
    artifacts, _ = capture_set(tmp_path, monkeypatch)
    extra, _ = write_capture(tmp_path, monkeypatch, 'adjustment_factors',
        {'codes': ['sh.600001', 'sz.000001'], 'start_date': '1990-01-01', 'end_date': str(DAY)},
        [{'code': code, 'dividOperateDate': '2010-01-01', 'backAdjustFactor': '2'}
         for code in ('sh.600001', 'sz.000001')], seen=CAPTURED+timedelta(minutes=1))
    new, audit = producer.normalize_original_parent_asof(artifacts+[extra], DAY, CUTOFF)
    assert all(row.comparable_close is None for row in new.bars)
    assert len([row for row in new.receipts if row.dataset=='adjustment_factors']) == 2
    assert any(row['reason']=='FACTOR_REVISION_CONFLICT_REQUIRES_REVIEW' for row in audit['normalization']['gaps'])


def test_late_original_parent_stops_before_normalizer(tmp_path, monkeypatch):
    artifacts, _ = capture_set(tmp_path, monkeypatch)
    late, _ = write_capture(tmp_path, monkeypatch, 'delisting_sse', {}, [], seen=CUTOFF+timedelta(seconds=1))
    monkeypatch.setattr(producer, 'normalize_capture_with_report', lambda *_a, **_k: pytest.fail('late parent reached normalizer'))
    with pytest.raises(ValueError, match='PARENT_CAPTURE_AFTER_CUTOFF'):
        producer.normalize_original_parent_asof(artifacts+[late], DAY, CUTOFF)


@pytest.mark.parametrize('change', ['wrapper_rows', 'field_version', 'raw_byte', 'receipt_clock', 'expected_hash'])
def test_saved_json_claim_cannot_replace_actual_archived_bytes(tmp_path, monkeypatch, change):
    artifacts, results = capture_set(tmp_path, monkeypatch)
    index = 4
    changed = deepcopy(results[index])
    if change == 'wrapper_rows':
        changed['rows'][0]['close'] = '99'
        artifacts[index] = repin(artifacts[index], changed)
    elif change == 'field_version':
        changed['field_version'] = 'TEST_FIXTURE_FORGED_SCHEMA'
        artifacts[index] = repin(artifacts[index], changed)
    elif change in ('raw_byte', 'receipt_clock'):
        receipt = changed['receipts'][0]
        name = receipt['content_hash']+'.raw.json' if change=='raw_byte' else receipt['receipt_id']+'.receipt.json'
        path = tmp_path/'cache'/'raw'/name
        value = json.loads(path.read_text())
        if change=='raw_byte': value['rows'][0]['amount'] = '0'
        else: value['first_seen_at'] = (CAPTURED-timedelta(days=1)).isoformat()
        path.write_text(json.dumps(value), encoding='utf-8')
    else:
        artifacts[index] = replace(artifacts[index], result_sha256='0'*64)
    with pytest.raises(ValueError):
        producer.normalize_original_parent_asof(artifacts, DAY, CUTOFF)


def test_receipt_conflict_and_duplicate_sources_are_not_silent_last_wins(tmp_path, monkeypatch):
    artifacts, results = capture_set(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='DUPLICATE_CAPTURE'):
        producer.normalize_original_parent_asof(artifacts+[artifacts[0]], DAY, CUTOFF)
    altered = deepcopy(results[0]); altered['diagnostics'] = {'TEST_FIXTURE_CONFLICT': True}
    new_path = tmp_path/'results'/'conflict.json'; new_path.write_text(json.dumps(altered), encoding='utf-8')
    extra = replace(artifacts[0], result_path=new_path, result_sha256=hashlib.sha256(new_path.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match='RECEIPT_CONFLICT'):
        producer.normalize_original_parent_asof(artifacts+[extra], DAY, CUTOFF)


def test_calendar_not_inferred_extended_or_filled_through_missing_days(tmp_path, monkeypatch):
    artifacts, _ = capture_set(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='COMPLETE_ORIGINAL_CALENDAR_REQUIRED'):
        producer.normalize_original_parent_asof(artifacts[1:], DAY, CUTOFF)
    short, _ = write_capture(tmp_path, monkeypatch, 'calendar',
        {'start_date': str(SESSIONS[0]), 'end_date': str(DAY)},
        [{'calendar_date': str(SESSIONS[0]), 'is_trading_day': '1'}, {'calendar_date': str(DAY), 'is_trading_day': '1'}])
    with pytest.raises(ValueError, match='CALENDAR_FULL_DATE_SCOPE_REQUIRED'):
        producer.normalize_original_parent_asof([short]+artifacts[1:], DAY, CUTOFF)
    new, _ = producer.normalize_original_parent_asof(artifacts, DAY, CUTOFF)
    assert new.sessions == SESSIONS and date(2026, 10, 1) not in new.sessions


def test_future_business_payload_and_unknown_date_are_not_cutoff_compatible(tmp_path, monkeypatch):
    artifacts, results = capture_set(tmp_path, monkeypatch)
    artifact, _ = write_capture(tmp_path, monkeypatch, 'market_daily', {'date': str(DAY), 'adjustflag':'3'},
                                [{**results[4]['rows'][0], 'date':'2026-10-01'}])
    with pytest.raises(ValueError, match='RAW_ROW_BUSINESS_SCOPE_MISMATCH'):
        producer.normalize_original_parent_asof(artifacts+[artifact], DAY, CUTOFF)
    with pytest.raises(ValueError, match='EARLIER_CUTOFF_REQUIRED'):
        producer.normalize_original_parent_asof(artifacts, DAY, datetime.now(timezone.utc)+timedelta(hours=1))


def test_future_publication_or_event_still_rejected_by_ordinary_consumer(tmp_path, monkeypatch):
    artifacts, _ = capture_set(tmp_path, monkeypatch)
    new, _ = producer.normalize_original_parent_asof(artifacts, DAY, CUTOFF)
    row = new.bars[0]
    for field in ('event_time', 'source_published_at'):
        invalid = row.model_copy(update={field:CUTOFF+timedelta(seconds=1)})
        assert resolver(new).resolve(invalid, instrument_id=row.instrument_id, session=DAY).reason_codes == ('EVENT_OR_PUBLICATION_NOT_VISIBLE',)
    prospective = new.model_copy(update={'coverage_type':'PROSPECTIVE_CAPTURED'})
    assert resolver(prospective).resolve(row, instrument_id=row.instrument_id, session=DAY).reason_codes == ('CAPTURE_AFTER_CUTOFF',)


def test_failed_empty_schema_default_is_negative_only(tmp_path, monkeypatch):
    artifacts, _ = capture_set(tmp_path, monkeypatch)
    failed, _ = write_capture(tmp_path, monkeypatch, 'history', {'date': str(DAY)}, [],
                              status='FETCH_FAILED', page=False, omitted_version=True)
    new, audit = producer.normalize_original_parent_asof(artifacts+[failed], DAY, CUTOFF)
    negative = next(row for row in audit['original_captures'] if row['receipt_id']==json.loads(failed.result_path.read_text())['request_receipt_id'])
    assert negative['failed_empty_schema_label_defaulted'] is True
    assert negative['schema_default_role'] == 'NEGATIVE_ENVELOPE_METADATA_NOT_NUMERIC_AUTHORITY'
    assert all(negative['receipt_id'] not in row.source_refs for row in new.bars)


def test_source_change_during_derivation_and_resource_bounds_fail_closed(tmp_path, monkeypatch):
    artifacts, _ = capture_set(tmp_path, monkeypatch)
    native = producer.normalize_capture_with_report
    def changing(*args, **kwargs):
        result = native(*args, **kwargs)
        artifacts[0].result_path.write_bytes(artifacts[0].result_path.read_bytes()+b' ')
        return result
    monkeypatch.setattr(producer, 'normalize_capture_with_report', changing)
    with pytest.raises(ValueError, match='SOURCE_CHANGED'):
        producer.normalize_original_parent_asof(artifacts, DAY, CUTOFF)
    with pytest.raises(ValueError, match='CAPTURE_BUDGET_EXCEEDED'):
        producer.normalize_original_parent_asof(artifacts, DAY, CUTOFF, limits=producer.DerivationLimits(max_captures=1))
    with pytest.raises(ValueError, match='FILE_BYTE_BUDGET_EXCEEDED'):
        producer.normalize_original_parent_asof(artifacts, DAY, CUTOFF, limits=producer.DerivationLimits(per_file_bytes=1))


def test_equivalent_raw_decimal_fields_and_extra_metadata_are_not_revisions(tmp_path, monkeypatch):
    artifacts, results = capture_set(tmp_path, monkeypatch)
    duplicate = deepcopy(results[4]['rows'][0])
    duplicate.update(open='10.0000', close='10.5000', amount='10300.00', pctChg='TEST_FIXTURE_METADATA')
    history, _ = write_capture(tmp_path, monkeypatch, 'history',
        {'codes': [duplicate['code']], 'start_date': str(DAY), 'end_date': str(DAY)},
        [duplicate], seen=CAPTURED+timedelta(minutes=1))
    new, audit = producer.normalize_original_parent_asof(artifacts+[history], DAY, CUTOFF)
    assert len(new.bars) == 2
    assert len(audit['same_raw_business_observations']) == 1
    assert next(row for row in new.bars if row.instrument_id=='600001.SH').amount_cny == Decimal('10300')


@pytest.mark.parametrize('field,value', [('close','10.6'), ('amount','10300.01'),
    ('volume','1001'), ('tradestatus','1.0'), ('isST','1')])
def test_authentic_but_conflicting_raw_parents_do_not_silently_replace_facts(tmp_path, monkeypatch, field, value):
    artifacts, results = capture_set(tmp_path, monkeypatch)
    changed = {**results[4]['rows'][0], field:value}
    extra, _ = write_capture(tmp_path, monkeypatch, 'history',
        {'codes': [changed['code']], 'start_date': str(DAY), 'end_date': str(DAY)},
        [changed], seen=CAPTURED+timedelta(minutes=1))
    monkeypatch.setattr(producer, 'normalize_capture_with_report', lambda *_a, **_k: pytest.fail('conflict reached normalizer'))
    with pytest.raises(ValueError, match='RAW_BUSINESS_REVISION_CONFLICT'):
        producer.normalize_original_parent_asof(artifacts+[extra], DAY, CUTOFF)


def test_explicit_raw_and_wrapper_capture_clocks_cannot_disagree_with_receipt(tmp_path, monkeypatch):
    artifacts, results = capture_set(tmp_path, monkeypatch)
    raw_conflict, _ = write_capture(tmp_path, monkeypatch, 'history',
        {'codes':['sh.600001'], 'start_date':str(DAY), 'end_date':str(DAY)},
        [results[4]['rows'][0]], raw_seen=CAPTURED-timedelta(seconds=1))
    with pytest.raises(ValueError, match='RAW_FIRST_SEEN_CONFLICT'):
        producer.normalize_original_parent_asof(artifacts+[raw_conflict], DAY, CUTOFF)
    changed = deepcopy(results[4]); changed['first_seen_at']=(CAPTURED-timedelta(seconds=1)).isoformat()
    replaced = repin(artifacts[4], changed)
    with pytest.raises(ValueError, match='RESULT_FIRST_SEEN_CONFLICT'):
        producer.normalize_original_parent_asof(artifacts[:4]+[replaced]+artifacts[5:], DAY, CUTOFF)


def test_source_wrapper_completion_after_cutoff_does_not_authorize_earlier_capture(tmp_path, monkeypatch):
    artifacts, results = capture_set(tmp_path, monkeypatch)
    changed=deepcopy(results[4]); changed['completed_at']=(CUTOFF+timedelta(seconds=1)).isoformat()
    artifacts[4]=repin(artifacts[4], changed)
    with pytest.raises(ValueError, match='CAPTURE_COMPLETE_AFTER_CUTOFF'):
        producer.normalize_original_parent_asof(artifacts, DAY, CUTOFF)


def factor_resume_pair(root, monkeypatch):
    params={'codes':['sh.600001','sz.000001'], 'start_date':'1990-01-01', 'end_date':str(DAY)}
    rows=[{'code':code,'dividOperateDate':'2010-01-01','backAdjustFactor':'1.5'}
          for code in params['codes']]
    old={'code':'sh.600001','status':'COMPLETE','cursor_exhausted':True,'row_count':1,
         'captured_at':(CAPTURED-timedelta(microseconds=500000)).isoformat()}
    prior, _ = write_capture(root, monkeypatch, 'adjustment_factors', params, rows[:1],
        status='PARTIAL', page=False, coverage={'security_statuses':[old]})
    later_seen=CAPTURED+timedelta(minutes=3)
    recent={**old,'code':'sz.000001','captured_at':(later_seen-timedelta(microseconds=500000)).isoformat()}
    final, _ = write_capture(root, monkeypatch, 'adjustment_factors', params, rows,
        seen=later_seen, coverage={'security_statuses':[old,recent]})
    return prior, final


def test_checkpoint_inherited_fragment_requires_whole_original_and_keeps_partial_parent(tmp_path, monkeypatch):
    artifacts, _ = capture_set(tmp_path, monkeypatch, factors=False)
    prior, final = factor_resume_pair(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='INHERITED_FACTOR_ORIGINAL_ANCESTOR_REQUIRED'):
        producer.normalize_original_parent_asof(artifacts+[final], DAY, CUTOFF)
    new, audit = producer.normalize_original_parent_asof(artifacts+[prior, final], DAY, CUTOFF)
    assert len(audit['explicit_factor_checkpoint_ancestry'])==1
    lineage=audit['explicit_factor_checkpoint_ancestry'][0]
    assert list(lineage['ancestor_statuses'].values())==['PARTIAL']
    inherited=next(row for row in new.bars if row.instrument_id=='600001.SH')
    assert inherited.comparable_close==Decimal('15.75')
    assert resolver(new).resolve(inherited,instrument_id=inherited.instrument_id,session=DAY,
                                require_complete=True).reason_codes==('SOURCE_NOT_COMPLETE',)
    assert audit['capture_inventory_completeness']=='NOT_INFERRED_FROM_LOCATOR_LIST'


def test_checkpoint_same_clock_and_row_count_cannot_hide_changed_inherited_rows(tmp_path, monkeypatch):
    artifacts, _ = capture_set(tmp_path, monkeypatch, factors=False)
    prior, final = factor_resume_pair(tmp_path, monkeypatch)
    # A second genuine fixture archive has the same claimed fragment clock/count
    # but different original values. It cannot attest the final inherited rows.
    original=json.loads(prior.result_path.read_text())
    changed, _ = write_capture(tmp_path, monkeypatch, 'adjustment_factors', original['parameters'],
        [{**original['rows'][0],'backAdjustFactor':'2'}], status='PARTIAL', page=False,
        coverage=original['coverage'])
    with pytest.raises(ValueError, match='INHERITED_FACTOR_ORIGINAL_ANCESTOR_REQUIRED'):
        producer.normalize_original_parent_asof(artifacts+[changed,final], DAY, CUTOFF)


def test_capture_count_guard_does_not_materialize_an_unbounded_source_iterator(tmp_path, monkeypatch):
    artifacts, _ = capture_set(tmp_path, monkeypatch)
    emitted=[]
    def unlimited():
        while True:
            emitted.append(1)
            if len(emitted)>2:
                pytest.fail('input source iterator exceeded the declared capture bound')
            yield artifacts[0]
    with pytest.raises(ValueError, match='CAPTURE_BUDGET_EXCEEDED'):
        producer.normalize_original_parent_asof(unlimited(),DAY,CUTOFF,
            limits=producer.DerivationLimits(max_captures=1))
    assert len(emitted)==2
