"""Public facts tests, all temporary databases explicitly TEST_FIXTURE."""
from datetime import date, datetime, timedelta, timezone
import json

import pytest

from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.evidence_service import EvidenceService, SeatAliasEvidence, RECORDS

DAY = date(2026, 9, 4)
AT = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
IID = '605398.SH'
OTHER = '600000.SH'


def clocks(at=AT):
    return {k: at.isoformat() for k in ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')}


def receipt(ref='R1', symbol=IID, at=AT, **changes):
    return dict(receipt_id=ref, provider='TEST_FIXTURE', dataset='public_seats',
        content_hash=digest(ref), field_version='TEST_FIXTURE', row_count=1,
        pagination_complete=True, status='COMPLETE', parameters={'symbol': symbol, 'date': '20260904'},
        **clocks(at)) | changes


def record(iid=IID, ref='R1', **changes):
    return dict(instrument_id=iid, provider='TEST_FIXTURE', seat_id='TEST_FIXTURE_SEAT', seat_name='TEST_FIXTURE营业部',
        window_start=DAY.isoformat(), window_end=DAY.isoformat(), window_status='VERIFIED',
        buy_cny=100, sell_cny=40, reason='TEST_FIXTURE原因', source_refs=[ref], **clocks()) | changes


@pytest.fixture
def service(tmp_path):
    return EvidenceService(SQLiteStore(tmp_path / 'TEST_FIXTURE.sqlite3'), allow_test_fixture=True)


def register(service, rows=None, sources=None):
    return service.register_public_records(rows if rows is not None else [record()],
        sources if sources is not None else [receipt()], coverage_type='TEST_FIXTURE')


def alias(at=AT, **changes):
    return SeatAliasEvidence(seat_id='TEST_FIXTURE_SEAT', provider='TEST_FIXTURE', alias='来源别名线索',
        valid_from=DAY, valid_to=DAY, sample_count=3, source_refs=('R1',),
        evidence_url='https://example.org/TEST_FIXTURE', coverage_type='TEST_FIXTURE', **clocks(at), **changes)


def test_restart_idempotency_and_late_bound_receipt_session(tmp_path):
    service = EvidenceService(SQLiteStore(tmp_path / 'TEST_FIXTURE.sqlite3'), allow_test_fixture=True)
    first = register(service)
    assert register(service) == first
    output = service.instrument(IID, AT, DAY)
    assert output['records'][0]['session'] == DAY.isoformat()
    assert output['records'][0]['session_basis'] == 'RECEIPT_REQUEST_DATE'
    assert output['nonoverlapping_complete_disclosures'][0]['disclosed_net_cny'] == 60
    restarted = EvidenceService(SQLiteStore(tmp_path / 'TEST_FIXTURE.sqlite3'), allow_test_fixture=True)
    assert restarted.instrument(IID, AT, DAY) == output
    assert len(output['records']) == 1


def test_all_post_outcome_fields_are_removed_before_persistence_and_llm(service):
    forbidden = {'上榜后1日': 999, '上榜后10日': 999, 'next_day_return': .50, 'outcomes': {'profit': 999}}
    register(service, [record(**forbidden)])
    result = service.instrument(IID, AT, DAY)
    serialized = json.dumps(result, ensure_ascii=False)
    persisted = json.dumps(service.store.list_documents(RECORDS), ensure_ascii=False)
    assert all(key not in serialized and key not in persisted for key in forbidden)
    assert result['llm_context'][0]['buy_cny'] == 100
    assert result['S_funds'] is None and result['F'] is None


def test_future_alias_and_record_do_not_change_prior_evidence_hash(service):
    register(service)
    original = service.instrument(IID, AT, DAY)
    service.register_aliases([alias(AT + timedelta(hours=1))])
    register(service, [record(seat_id='FUTURE', **clocks(AT + timedelta(hours=1)))], [])
    assert service.instrument(IID, AT, DAY) == original
    assert not service.seat('TEST_FIXTURE_SEAT', AT, DAY)['aliases']
    later = service.instrument(IID, AT + timedelta(hours=1), DAY)
    assert later['aliases'][0]['sample_count'] == 3
    assert later['aliases'][0]['score_bonus'] == 0


def test_alias_inclusive_valid_to_and_typed_receipt_provenance(service):
    register(service)
    service.register_aliases([alias()])
    assert service.seat('TEST_FIXTURE_SEAT', AT, DAY)['aliases'][0]['valid_to_convention'] == 'INCLUSIVE'
    assert not service.seat('TEST_FIXTURE_SEAT', AT + timedelta(days=1), DAY + timedelta(days=1))['aliases']
    with pytest.raises(TypeError, match='TYPED'):
        service.register_aliases([alias().model_dump()])
    with pytest.raises(ValueError):
        SeatAliasEvidence.model_validate(alias().model_dump() | {'sample_count': -1})


def test_receipt_future_clock_overrides_earlier_row_self_claim(service):
    later=(AT+timedelta(hours=1)).isoformat()
    register(service, [record(first_seen_at=later,ingested_at=later)], [receipt(at=AT + timedelta(hours=1))])
    assert not service.instrument(IID, AT, DAY)['records']
    assert len(service.instrument(IID, AT + timedelta(hours=1), DAY)['records']) == 1


def test_identity_and_window_queries_do_not_cross_stocks_or_sessions(service):
    register(service, [record(), record(OTHER, 'R2')], [receipt(), receipt('R2', OTHER)])
    assert {x['instrument_id'] for x in service.instrument(IID, AT, DAY)['records']} == {IID}
    assert {x['instrument_id'] for x in service.seat('TEST_FIXTURE_SEAT', AT, DAY)['records']} == {IID, OTHER}
    assert not service.instrument(IID, AT + timedelta(days=1), DAY + timedelta(days=1))['records']
    theme = service.theme('TEST_FIXTURE_THEME', [IID, OTHER], AT, DAY)
    assert theme['coappearance'][0]['instruments'] == sorted([IID, OTHER])
    assert theme['coappearance'][0]['source_refs'] == ['R1', 'R2']
    assert service.theme('TEST_FIXTURE_THEME', [IID], AT, DAY)['coappearance'][0]['instruments'] == [IID]


def test_unknown_windows_partial_buy_are_facts_never_zero_sell(service):
    register(service, [record(window_start=None, window_end=None, window_status='UNAVAILABLE', sell_cny=None)])
    result = service.instrument(IID, AT, DAY)
    assert result['status'] == 'AVAILABLE_EVIDENCE'
    assert result['records'][0]['buy_cny'] == 100
    assert result['records'][0]['sell_cny'] is None
    assert result['records'][0]['amount_status'] == 'PARTIAL'
    assert not result['nonoverlapping_complete_disclosures']
    assert result['score_status'] == 'UNAVAILABLE'
    assert not service.theme('TEST_FIXTURE_THEME', [IID], AT, DAY)['coappearance']


def test_legacy_generic_seat_label_without_provider_identity_cannot_create_linkage(service):
    register(service, [record(seat_name='机构专用')])
    evidence = service.instrument(IID, AT, DAY)
    assert evidence['records'][0]['seat_identity_status'] == 'UNRESOLVED_GROUP_LABEL'
    assert not evidence['nonoverlapping_complete_disclosures']
    assert not service.theme('TEST_FIXTURE_THEME', [IID], AT, DAY)['coappearance']


def test_missing_receipt_does_not_claim_verified_fact_or_not_disclosed(service):
    register(service, [record(session=DAY.isoformat())], [])
    result = service.instrument(IID, AT, DAY)
    assert result['status'] == 'UNAVAILABLE'
    assert result['records'][0]['source_status'] == 'SOURCE_RECEIPT_UNAVAILABLE'
    assert 'buy_cny' not in result['records'][0]
    assert not result['llm_context']


def test_unknown_session_preserved_separately_without_guessing_capture_day(service):
    register(service, [record()], [receipt(parameters={'symbol': IID})])
    result = service.instrument(IID, AT, DAY)
    assert not result['records']
    assert len(result['undated_records']) == 1
    assert result['undated_records'][0]['session'] is None


def test_dedup_reasons_same_seat_and_nonoverlap_three_day_window(service):
    day = record(reason='日理由', buy_cny=90, sell_cny=30)
    three = record(reason='三日理由1', window_start='2026-09-02')
    duplicate = record(reason='三日理由2', window_start='2026-09-02')
    register(service, [day, three, duplicate])
    result = service.instrument(IID, AT, DAY)
    assert len(result['records']) == 3
    assert len(result['nonoverlapping_complete_disclosures']) == 1
    assert result['nonoverlapping_complete_disclosures'][0]['disclosed_net_cny'] == 60
    assert result['nonoverlapping_complete_disclosures'][0]['reasons'] == ['三日理由1', '三日理由2']


def test_source_identity_conflict_rejects_atomic_batch_and_fixture_gate(tmp_path, service):
    with pytest.raises(ValueError, match='INSTRUMENT_MISMATCH'):
        register(service, [record()], [receipt(symbol=OTHER)])
    assert not service.store.list_documents(RECORDS)
    real = EvidenceService(SQLiteStore(tmp_path / 'REAL.sqlite3'))
    with pytest.raises(ValueError, match='ISOLATED'):
        register(real)
    with pytest.raises(ValueError):
        register(service, [record(buy_cny=float('nan'))])


def test_future_outer_envelope_skips_malformed_nested_business_fields(service):
    register(service)
    original = service.instrument(IID, AT, DAY)
    payload = dict(record_id='TEST_FIXTURE_POISON', record={'buy_cny': 'malformed'},
        instrument_id=IID, seat_id='TEST_FIXTURE_SEAT', session=DAY.isoformat(), source_refs=['R1'],
        **clocks(AT + timedelta(hours=1)))
    service.store.put_document(RECORDS, payload['record_id'], payload)
    assert service.instrument(IID, AT, DAY) == original
