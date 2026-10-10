"""Isolated TEST_FIXTURE archives and genuine native parents, no network/DB."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from stockfish_core.yaogu import lhb_seat_evidence as seats
from stockfish_core.yaogu.history_artifacts import digest_json_stream, digest_market_input
from tests.yaogu.test_theme_observation_diagnostics import native_case


DAY = '2026-10-09'
IID = '002733.SZ'
CUTOFF = datetime(2026, 10, 10, 23, tzinfo=timezone.utc)


def info(path):
    raw = path.read_bytes()
    return {'path': str(path.resolve()), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                               allow_nan=False).encode())
    return info(path)


def row(department, name=None, *, iid=IID, day=DAY, buy=111.1, sell=11.1):
    return {'SECURITY_CODE': iid[:6], 'SECUCODE': iid, 'TRADE_DATE': day + ' 00:00:00',
        'TRADE_ID': '100422521', 'CHANGE_TYPE': '137001002001002',
        'EXPLANATION': 'TEST_FIXTURE 连续三个交易日内涨幅偏离',
        'OPERATEDEPT_CODE': department, 'OPERATEDEPT_CODE_OLD': '9' + department if department else None,
        'OPERATEDEPT_NAME': name or 'TEST_FIXTURE_VENUE_' + str(department),
        'BUY': buy, 'SELL': sell, 'NET': float(Decimal(str(buy)) - Decimal(str(sell))),
        'ACCUM_AMOUNT': 1234567, 'ACCUM_VOLUME': 12345,
        'RISE_PROBABILITY_3DAY': 99.123, 'TOTAL_BUYER_SALESTIMES_3DAY': 888,
        'TOTAL_BUYRIO': 2.33456789, 'TOTAL_SELLRIO': 1.98765432,
        'CHANGE_RATE': 99.1, 'CLOSE_PRICE': 15.5}


def source(tmp_path, *, iid=IID, day=DAY, started=None, mutate=None, packets=None,
           legacy_mutate=None, receipt_mutate=None, missing_side=None, failed=False):
    """Write local synthetic copies; never present them as real capture history."""
    root = tmp_path / 'TEST_FIXTURE_CAPTURE'
    root.mkdir()
    started = started or CUTOFF - timedelta(minutes=5)
    buy = [row(code, '深股通专用' if code == '102' else None, iid=iid, day=day)
           for code in ('101', '102', '103', '104', '105')]
    sell = [row(code, '深股通专用' if code == '102' else '机构专用' if code == '0' else None, iid=iid, day=day)
            for code in ('102', '106', '0', '107', '108')]
    if mutate:
        mutate(buy, sell)
    artifacts = {}
    raw_paths = {}

    def receipt(provider, side, payload, parameters, offset, status='COMPLETE'):
        directory = root / 'archive' / ('upstream' if provider == 'AKShare_upstream' else '')
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        sha = hashlib.sha256(data).hexdigest()
        p = directory / (sha + '.raw.json')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        artifacts[str(p.resolve())] = info(p)
        raw_paths[provider, side] = p
        fields = {'provider': provider, 'dataset': seats.ENDPOINT, 'parameters': parameters,
            'field_version': 'JSON_UPSTREAM_RESPONSE' if provider == 'AKShare_upstream' else '1.18.94',
            'row_count': len(payload) if isinstance(payload, list) else None,
            'pagination_complete': provider == 'AKShare' and status == 'COMPLETE',
            'status': status, 'first_seen_at': (started + timedelta(seconds=offset)).isoformat(),
            'ingested_at': (started + timedelta(seconds=offset + 1)).isoformat(), 'content_hash': sha,
            'source_url': seats.SOURCE_URL,
            'integrity_scope': 'LOCAL_SHA256_NOT_SECURITY_AUTHENTICATION'}
        if receipt_mutate:
            receipt_mutate(provider, side, fields)
        fields['receipt_id'] = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
        p = directory / (fields['receipt_id'] + '.receipt.json')
        put(p, fields)
        artifacts[str(p.resolve())] = info(p)

    for side, rows in (('BUY', buy), ('SELL', sell)):
        if side == missing_side:
            continue
        flag, report_name = seats.SIDES[side]
        normalized_parameters = {'symbol': iid[:6], 'date': day.replace('-', ''), 'flag': flag}
        if failed:
            receipt('AKShare', side, {'error': 'FETCH_FAILED'}, normalized_parameters, 5, status='FETCH_FAILED')
            continue
        packet = {'success': True, 'code': 0, 'message': 'ok',
                  'result': {'pages': 1, 'count': len(rows), 'data': rows}}
        if packets:
            packets(side, packet)
        upstream_parameters = {'reportName': report_name, 'columns': 'ALL',
            'filter': '(TRADE_DATE=\'' + day + '\')(SECURITY_CODE="' + iid[:6] + '")',
            'pageNumber': '1', 'pageSize': '500', 'sortTypes': '-1', 'sortColumns': side,
            'source': 'WEB', 'client': 'WEB'}
        receipt('AKShare_upstream', side, packet, upstream_parameters, 2 if side == 'BUY' else 5)
        legacy = [{'交易营业部名称': x['OPERATEDEPT_NAME'], '类型': x['EXPLANATION'],
            '买入金额': x.get('BUY'), '卖出金额': x.get('SELL'), '净额': x.get('NET'),
            '买入金额-占总成交比例': 2.3346, '卖出金额-占总成交比例': 1.9877} for x in rows]
        if legacy_mutate:
            legacy_mutate(side, legacy)
        receipt('AKShare', side, legacy, normalized_parameters, 3 if side == 'BUY' else 6)
    report = {'schema_version': 'EXISTING_LHB_SEAT_RESEARCH_PROBE_RESULT_V1',
        'instrument_id': iid, 'observation_date': day, 'status': 'ADAPTER_RETURNED',
        'batch_started_at': started.isoformat(), 'batch_completed_at': (started + timedelta(seconds=10)).isoformat(),
        'source_artifacts': list(artifacts.values()), 'test_fixture': True}
    report_info = put(root / 'probe-result.json', report)
    manifest = put(root / 'manifest.json', {'status': report['status'], 'result': report_info,
                                            'source_artifact_count': len(artifacts)})
    return seats.ArchivedLhbSeatSource(root / 'manifest.json', manifest['sha256'], coverage_type='TEST_FIXTURE'), raw_paths


def parse(archive, *, iid=IID, day=DAY, cutoff=CUTOFF):
    return seats.parse_archived_lhb_seats(archive, instrument_id=iid, business_session=day, cutoff=cutoff)


@pytest.fixture(scope='module')
def native():
    return native_case('overlap')


def bind(evidence, case, *, cutoff, **kwargs):
    args = dict(bundle=case.bundle, native_parent=case.context, selected_ids=case.selected_ids,
                cutoff=cutoff, _parent_validator=case.verify_context, _selection_validator=case.verify_selection)
    args.update(kwargs)
    return seats.bind_lhb_seat_context(evidence, **args)


def test_recovers_eight_provider_ids_collapses_only_one_duplicate_and_keeps_unknowns(tmp_path):
    archive, _ = source(tmp_path)
    value = parse(archive)
    assert value['availability'] == 'AVAILABLE' and value['decision_readiness'] == 'UNKNOWN'
    assert value['appearance_count'] == 10 and value['appearance_group_count'] == 9
    assert value['known_provider_identifier_count'] == 8
    assert value['unresolved_identity_appearance_count'] == 1
    assert value['collapsed_provider_id_appearance_count'] == 1
    channel = next(row for row in value['rows'] if row['provider_department_code'] == '102')
    assert channel['identity_status'] == 'PROVIDER_CHANNEL_IDENTIFIER'
    assert channel['appearance_count'] == 2 and len(channel['source_refs']) == 2
    assert {x['side'] for x in channel['appearances']} == {'BUY', 'SELL'}
    assert channel['reported_buy_amount_exact'] == {'numerator': 1111, 'denominator': 10}
    assert channel['reported_sell_amount_exact'] == {'numerator': 111, 'denominator': 10}
    unresolved = next(row for row in value['rows'] if row['raw_department_code'] == '0')
    assert unresolved['provider_department_code'] is None and unresolved['source_entity_id'] is None
    assert unresolved['identity_status'] == 'UNRESOLVED_GROUP_LABEL'
    for row in value['rows']:
        assert row['currency'] is None and row['amount_unit'] is None
        assert row['window_status'] == 'UNKNOWN' and row['window_start'] is None
        assert row['natural_person_identity'] == 'UNKNOWN' and row['independent_disclosure_published_at'] is None
        assert row['eligible_for_funds_scoring'] is False
    assert value['funds_score_authority'] is False and value['official_pit_validation_count'] == 0
    assert value['source_audit']['archive_representation'] == 'CANONICAL_PARSED_RESPONSE_BODY_NOT_WIRE_BYTES'
    assert 'RISE_PROBABILITY_3DAY' not in value['rows'][0]
    assert value['source_audit']['coverage_type'] == 'TEST_FIXTURE'


def test_no_integer_cast_or_old_code_substitution(tmp_path):
    def change(buy, sell):
        buy[0]['OPERATEDEPT_CODE'] = '0000123'
        sell[2]['OPERATEDEPT_CODE'] = None
        sell[2]['OPERATEDEPT_CODE_OLD'] = '998877'
    value = parse(source(tmp_path, mutate=change)[0])
    assert any(x['source_entity_id'] == 'EASTMONEY_DEPARTMENT:0000123' for x in value['rows'])
    missing = next(x for x in value['rows'] if x['raw_old_department_code'] == '998877')
    assert missing['provider_department_code'] is None and missing['source_entity_id'] is None


def test_zero_code_same_institution_names_not_merged(tmp_path):
    def change(buy, sell):
        buy.append(deepcopy(sell[2]))
    value = parse(source(tmp_path, mutate=change)[0])
    assert value['appearance_count'] == 11 and value['appearance_group_count'] == 10
    assert value['unresolved_identity_appearance_count'] == 2
    assert len([x for x in value['rows'] if x['reported_department_name'] == '机构专用']) == 2


@pytest.mark.parametrize('field,replacement', [('BUY', 222.2), ('OPERATEDEPT_NAME', 'TEST_FIXTURE_RENAMED'),
                                              ('EXPLANATION', 'TEST_FIXTURE_DIFFERENT_REASON')])
def test_same_provider_same_disclosure_conflict_is_rejected(tmp_path, field, replacement):
    def change(buy, sell):
        sell[0][field] = replacement
        if field == 'BUY':
            sell[0]['NET'] = float(Decimal(str(replacement)) - Decimal(str(sell[0]['SELL'])))
    archive, _ = source(tmp_path, mutate=change)
    with pytest.raises(ValueError, match='FACT_CONFLICT'):
        parse(archive)


@pytest.mark.parametrize('field', ['CHANGE_TYPE', 'TRADE_ID'])
def test_different_reported_reason_or_disclosure_is_not_merged(tmp_path, field):
    def change(buy, sell):
        sell[0][field] = '99999'
    value = parse(source(tmp_path, mutate=change)[0])
    assert value['appearance_group_count'] == 10 and value['collapsed_provider_id_appearance_count'] == 0


@pytest.mark.parametrize('field,value', [('BUY', None), ('BUY', True), ('SELL', -1),
                                       ('NET', 12345), ('OPERATEDEPT_CODE', 123),
                                       ('SECUCODE', '002733.SH'), ('SECURITY_CODE', '999999'),
                                       ('TRADE_DATE', '2026-10-08 00:00:00')])
def test_raw_business_and_numeric_integrity_rejected(tmp_path, field, value):
    def change(buy, sell):
        buy[0][field] = value
    with pytest.raises(ValueError):
        parse(source(tmp_path, mutate=change)[0])


def test_sdk_projection_cannot_override_original(tmp_path):
    def change(side, legacy):
        if side == 'BUY':
            legacy[0]['买入金额'] = 99999
    with pytest.raises(ValueError, match='PROJECTION_CONFLICT'):
        parse(source(tmp_path, legacy_mutate=change)[0])


@pytest.mark.parametrize('variant', ['clock', 'upstream_filter', 'normalized_date', 'sdk_version'])
def test_actual_source_parent_clocks_parameters_and_definitions_are_consistent(tmp_path, variant):
    started = CUTOFF - timedelta(minutes=5)
    def change(provider, side, fields):
        if side != 'BUY':
            return
        if variant == 'clock' and provider == 'AKShare':
            fields['first_seen_at'] = (started + timedelta(seconds=1)).isoformat()
            fields['ingested_at'] = (started + timedelta(seconds=2)).isoformat()
        if variant == 'upstream_filter' and provider == 'AKShare_upstream':
            fields['parameters']['filter'] = '(TRADE_DATE=\'2026-10-08\')(SECURITY_CODE="002733")'
        if variant == 'normalized_date' and provider == 'AKShare':
            fields['parameters']['date'] = '20261008'
        if variant == 'sdk_version' and provider == 'AKShare':
            fields['field_version'] = 'UNRECOGNIZED_SDK'
    with pytest.raises(ValueError):
        parse(source(tmp_path, started=started, receipt_mutate=change)[0])


@pytest.mark.parametrize('variant', ['missing_side', 'failed', 'pages', 'count', 'empty', 'late'])
def test_unavailable_sources_and_page_claims_are_unknown_not_absence(tmp_path, variant):
    kwargs = {}
    if variant == 'missing_side':
        kwargs['missing_side'] = 'SELL'
    elif variant == 'failed':
        kwargs['failed'] = True
    elif variant in ('pages', 'count'):
        def change(side, packet):
            packet['result'][variant] = 2 if variant == 'pages' else 99
        kwargs['packets'] = change
    elif variant == 'empty':
        kwargs['mutate'] = lambda buy, sell: (buy.clear(), sell.clear())
    elif variant == 'late':
        kwargs['started'] = CUTOFF + timedelta(seconds=1)
    value = parse(source(tmp_path, **kwargs)[0])
    assert value['availability'] == 'UNKNOWN'
    assert value['funds_score_authority'] is False
    if variant != 'missing_side':
        assert value['appearance_count'] == 0
        assert 'EMPTY_OR_UNAVAILABLE_NOT_EVIDENCE_OF_NO_PARTICIPANT' in value['reason_codes']


def test_external_anchor_source_bytes_and_scope_are_required(tmp_path):
    archive, paths = source(tmp_path)
    path = Path(archive.audit()['manifest']['path'])
    with pytest.raises(ValueError, match='EXTERNAL_MANIFEST_SHA'):
        seats.ArchivedLhbSeatSource(path, '0' * 64, coverage_type='TEST_FIXTURE')
    with pytest.raises(ValueError, match='EXTERNAL_SHA256'):
        seats.ArchivedLhbSeatSource(path, None, coverage_type='TEST_FIXTURE')
    with pytest.raises(ValueError, match='ARCHIVED_ORIGINALS_CAPABILITY'):
        parse({'authenticated': True})
    with pytest.raises(ValueError, match='CAPTURE_SCOPE_MISMATCH'):
        parse(archive, iid='002733.SH')
    raw = paths['AKShare_upstream', 'BUY']
    raw.write_bytes(raw.read_bytes() + b' ')
    with pytest.raises(ValueError, match='ANCHORED_ARTIFACT_CHANGED'):
        parse(archive)


def test_archive_path_escape_rejected(tmp_path):
    archive, _ = source(tmp_path)
    old = archive.audit()['manifest']
    document = json.loads(Path(old['path']).read_bytes())
    external = tmp_path / 'TEST_FIXTURE_OUTSIDE.json'
    external.write_bytes(Path(document['result']['path']).read_bytes())
    document['result'] = info(external)
    current = put(Path(old['path']), document)
    with pytest.raises(ValueError, match='PATH_OUTSIDE'):
        seats.ArchivedLhbSeatSource(old['path'], current['sha256'], coverage_type='TEST_FIXTURE')


@pytest.mark.parametrize('raw', [b'{"BUY":1,"BUY":2}', b'{"BUY":NaN}', b'{"BUY":Infinity}',
                               b'{"BUY":1e999999999}', b'{"BUY":1e-999999999}',
                               b'{"BUY":' + b'9' * 129 + b'}'])
def test_json_duplicate_nonfinite_and_exponent_resources_rejected(raw):
    with pytest.raises(ValueError):
        seats._decode(raw)


def test_size_bound_before_file_read(tmp_path):
    p = tmp_path / 'TEST_FIXTURE_OVERSIZE.json'
    with p.open('wb') as handle:
        handle.truncate(seats.MAX_ARTIFACT_BYTES + 1)
    with pytest.raises(ValueError, match='SIZE_BOUND'):
        seats._read(p)


def test_capability_mutation_and_naive_cutoff_rejected(tmp_path):
    archive, _ = source(tmp_path)
    with pytest.raises(ValueError, match='AWARE_ACTUAL_CLOCK'):
        parse(archive, cutoff=CUTOFF.replace(tzinfo=None))
    archive.coverage_type = 'CURRENT_CAPTURE_RESEARCH'
    with pytest.raises(ValueError, match='CAPABILITY_BINDING_CHANGED'):
        parse(archive)


def test_declared_fixture_cannot_be_reopened_as_current_capture(tmp_path):
    archive, _ = source(tmp_path)
    original = archive.audit()['manifest']
    with pytest.raises(ValueError, match='DECLARED_FIXTURE'):
        seats.ArchivedLhbSeatSource(original['path'], original['sha256'])


def test_genuine_native_parent_new_cutoff_and_frozen_selection_unchanged(tmp_path, native):
    iid = native.selected_ids[0]
    new_cutoff = native.cutoff + timedelta(hours=2)
    archive, _ = source(tmp_path, iid=iid, day=str(native.bundle.observation_date),
                        started=new_cutoff - timedelta(minutes=5))
    evidence = parse(archive, iid=iid, day=str(native.bundle.observation_date), cutoff=new_cutoff)
    before = digest_json_stream(native.context), digest_json_stream(native.stock_parent)
    result = bind(evidence, native, cutoff=new_cutoff)
    assert result['native_context_binding_status'] == 'VERIFIED_NATIVE_PARENT_AND_FROZEN_SELECTION'
    assert result['base_decision_cutoff'] == native.cutoff.isoformat()
    assert result['new_context_cutoff'] == new_cutoff.isoformat()
    assert result['attachment_stock_id'] == iid and result['frozen_selected_count'] == len(native.selected_ids)
    assert result['source_input_hash'] == digest_market_input(native.bundle)
    assert result['funds_score_authority'] is False and result['selection_changed'] is False
    assert before == (digest_json_stream(native.context), digest_json_stream(native.stock_parent))
    assert native.calls['context'] >= 2 and native.calls['selection'] >= 1


@pytest.mark.parametrize('variant', ['parent', 'selection', 'lambda', 'boolean_success', 'json', 'mutation', 'real_fixture_mix'])
def test_untrusted_parent_selection_or_parser_capability_rejected(tmp_path, native, variant):
    iid = native.selected_ids[0]
    new_cutoff = native.cutoff + timedelta(hours=2)
    archive, _ = source(tmp_path, iid=iid, day=str(native.bundle.observation_date), started=new_cutoff - timedelta(minutes=5))
    evidence = parse(archive, iid=iid, day=str(native.bundle.observation_date), cutoff=new_cutoff)
    kwargs = {}
    if variant == 'parent':
        kwargs['native_parent'] = deepcopy(native.context)
    elif variant == 'selection':
        kwargs['selected_ids'] = native.selected_ids + ('999999.SH',)
    elif variant == 'lambda':
        kwargs['_parent_validator'] = lambda *a, **k: None
    elif variant == 'boolean_success':
        class FalseCapability:
            def verify(self, *a, **k):
                return True
        kwargs['_parent_validator'] = FalseCapability().verify
    elif variant == 'json':
        evidence = json.loads(json.dumps(evidence))
    elif variant == 'mutation':
        evidence['rows'][0]['window_status'] = 'VERIFIED'
    elif variant == 'real_fixture_mix':
        bundle = native.bundle.model_copy(update={'coverage_type': 'PROSPECTIVE_CAPTURED'})
        parent = deepcopy(native.context)
        parent['source_input_hash'] = digest_market_input(bundle)
        kwargs.update(bundle=bundle, native_parent=parent)
    with pytest.raises(ValueError):
        bind(evidence, native, cutoff=new_cutoff, **kwargs)


def test_old_cutoff_keeps_new_sources_unknown_and_no_fake_window_argument(tmp_path, native):
    iid = native.selected_ids[0]
    archive, _ = source(tmp_path, iid=iid, day=str(native.bundle.observation_date),
                        started=native.cutoff + timedelta(minutes=5))
    evidence = parse(archive, iid=iid, day=str(native.bundle.observation_date), cutoff=native.cutoff)
    result = bind(evidence, native, cutoff=native.cutoff)
    assert result['availability'] == 'UNKNOWN' and result['rows'] == []
    assert 'ACTUAL_CAPTURE_AFTER_CONTEXT_CUTOFF' in result['reason_codes']
    with pytest.raises(TypeError):
        seats.parse_archived_lhb_seats(archive, instrument_id=iid, business_session=native.bundle.observation_date,
                                      cutoff=native.cutoff, windows_by_reason={'THREE': ('2026-10-07', '2026-10-09')})


def test_capture_source_tamper_after_parse_blocks_binding(tmp_path, native):
    iid = native.selected_ids[0]
    new_cutoff = native.cutoff + timedelta(hours=2)
    archive, paths = source(tmp_path, iid=iid, day=str(native.bundle.observation_date), started=new_cutoff - timedelta(minutes=5))
    evidence = parse(archive, iid=iid, day=str(native.bundle.observation_date), cutoff=new_cutoff)
    p = paths['AKShare_upstream', 'BUY']
    p.write_bytes(p.read_bytes() + b' ')
    with pytest.raises(ValueError, match='ANCHORED_ARTIFACT_CHANGED'):
        bind(evidence, native, cutoff=new_cutoff)


def test_parser_source_identity_change_blocks_existing_capability_binding(tmp_path, native, monkeypatch):
    iid = native.selected_ids[0]
    new_cutoff = native.cutoff + timedelta(hours=2)
    archive, _ = source(tmp_path, iid=iid, day=str(native.bundle.observation_date), started=new_cutoff - timedelta(minutes=5))
    evidence = parse(archive, iid=iid, day=str(native.bundle.observation_date), cutoff=new_cutoff)
    changed = {**seats.source_identity(), 'source_hash': '0' * 64}
    monkeypatch.setattr(seats, 'source_identity', lambda: changed)
    with pytest.raises(ValueError, match='IMPLEMENTATION_CHANGED'):
        bind(evidence, native, cutoff=new_cutoff)
