"""SESSION_PAYLOAD_FUSION_V1 isolated TEST_FIXTURE; no real sources or issuer."""
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json

import pytest

from stockfish_core.yaogu import short_horizon_participation as r2
from stockfish_core.yaogu.contracts import SourceReceipt
from stockfish_core.yaogu.history_artifacts import digest_json_stream
from stockfish_core.yaogu.research_context_artifacts import SourceArtifactReader, RETURN_BASIS
from stockfish_core.yaogu.session_payload_fusion_v1 import project_session_v1, source_identity
from stockfish_core.yaogu.visibility import EvidenceResolver


FIELD = 'BAOSTOCK_00.9.30_R03_V1'
CODES = ('sh.600001', 'sh.688001', 'sz.000001', 'sz.300001')


def calendar_days(count):
    days = []
    day = date(2026, 1, 5)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(str(day))
        day += timedelta(days=1)
    return days


def fixture_sources(tmp_path, count=1, mutate=None):
    """Business dates and clocks are synthetic and explicitly TEST_FIXTURE."""
    days = calendar_days(count)
    cutoff = datetime.fromisoformat(days[-1] + 'T23:59:00+00:00')
    receipts, paths = [], {}
    for index, day in enumerate(days):
        clock = datetime.fromisoformat(day + 'T15:00:00+00:00')
        master = [{'code': code, 'tradeStatus': '0' if index % 7 == 0 and i == 3 else '1'}
                  for i, code in enumerate(CODES)]
        # All master rows, including identities outside the four-board denominator,
        # must be checked before filtering. They are not invented market rows.
        master += [{'code': 'bj.920001', 'tradeStatus': '1'},
                   {'code': 'sh.000001', 'tradeStatus': '1'}]
        market = [{'code': row['code'], 'date': day, 'adjustflag': '3',
                   'tradestatus': row['tradeStatus'],
                   'amount': '0' if row['tradeStatus'] == '0' else str(100000 + index * 100 + i),
                   'volume': '0' if row['tradeStatus'] == '0' else '100',
                   'open': '10.00', 'high': '11.00', 'low': '9.00',
                   'close': '10.50', 'preclose': '10.00'}
                  for i, row in enumerate(master[:4])]
        payloads = {dataset: {'status': 'COMPLETE', 'pagination_complete': True,
                             'field_version': FIELD, 'first_seen_at': clock.isoformat(), 'rows': rows}
                    for dataset, rows in [('master_all', master), ('market_daily', market)]}
        if mutate:
            mutate(index, payloads)
        for dataset, payload in payloads.items():
            raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
            rid = f'TEST_FIXTURE_{dataset}_{day}'
            path = tmp_path / (rid + '.json')
            path.write_bytes(raw)
            paths[rid] = path
            receipts.append(SourceReceipt(receipt_id=rid, provider='baostock', dataset=dataset,
                content_hash=hashlib.sha256(raw).hexdigest(), field_version=FIELD,
                status='COMPLETE', pagination_complete=True, row_count=len(payload['rows']),
                parameters={'date': day}, business_sessions=(date.fromisoformat(day),),
                event_time=clock, source_published_at=clock, first_seen_at=clock, ingested_at=clock))
    reader = SourceArtifactReader(receipts, paths)
    return reader, receipts, paths, days, cutoff


def resolver(receipts, days, cutoff):
    return EvidenceResolver(receipts, cutoff=cutoff, coverage_type='TEST_FIXTURE',
                            sessions=days, observation_date=days[-1], record_rows=False)


def outcome(call):
    try:
        return ('OK', call())
    except (ValueError, TypeError, KeyError) as error:
        return (type(error).__name__, str(error))


def old_projection(reader, receipts, days, cutoff, day, basis=RETURN_BASIS):
    observation = r2._market_observation(resolver(receipts, days, cutoff), day, reader)
    panel = reader._session(resolver(receipts, days, cutoff), day, basis)
    return observation, panel


def test_two_complete_67_session_rounds_match_every_original_projection_and_hash(tmp_path, monkeypatch):
    reader, receipts, paths, days, cutoff = fixture_sources(tmp_path, 67)
    expected = {day: old_projection(reader, receipts, days, cutoff, day) for day in days}
    original = type(next(iter(paths.values()))).read_bytes
    counter = Counter()

    def counted(path):
        if path in paths.values():
            counter[path.name.split('_')[2]] += 1
        return original(path)

    monkeypatch.setattr(type(next(iter(paths.values()))), 'read_bytes', counted)
    for _ in range(2):
        for day in days:
            actual = project_session_v1(reader, resolver(receipts, days, cutoff), day, RETURN_BASIS)
            assert actual['schema_version'] == actual['adapter_id'] == 'SESSION_PAYLOAD_FUSION_V1'
            observation, panel = expected[day]
            assert actual['observation'] == observation
            assert actual['panel'] == panel
            assert digest_json_stream(actual['observation']) == digest_json_stream(observation)
            assert digest_json_stream(actual['panel']) == digest_json_stream(panel)
            assert actual['panel']['rows'] == panel['rows']
            assert actual['panel']['source_refs'] == observation['source_refs']
            assert actual['panel']['clocks'] == observation['clocks']
    assert counter == {'market': 134, 'master': 134}
    counter.clear()
    for _ in range(2):
        for day in days:
            reader._session(resolver(receipts, days, cutoff), day, RETURN_BASIS)
    assert counter == {'market': 268, 'master': 134}


def mutate_case(kind, payloads):
    market = payloads['market_daily']['rows']
    master = payloads['master_all']['rows']
    if kind == 'master_row': master[0] = 'invalid'
    elif kind == 'master_identity': master[-1]['code'] = 'not-an-id'
    elif kind == 'master_duplicate': master[-1]['code'] = master[0]['code']
    elif kind == 'master_status': master[0]['tradeStatus'] = 'unknown'
    elif kind == 'empty_scope': master[:] = master[-2:]
    elif kind == 'raw_row': market[0] = 'invalid'
    elif kind == 'raw_identity': market[0]['code'] = 'sh.000001'
    elif kind == 'raw_duplicate': market[1]['code'] = market[0]['code']
    elif kind == 'date': market[0]['date'] = '2026-01-06'
    elif kind == 'adjustflag': market[0]['adjustflag'] = '2'
    elif kind == 'missing_scope': market.pop()
    elif kind == 'extra_scope': market.append({**market[0], 'code': 'sh.600002'})
    elif kind == 'trade_mismatch': market[0]['tradestatus'] = '0'
    elif kind == 'trade_unknown': market[0]['tradestatus'] = 'unknown'
    elif kind.startswith('nontrade_'):
        field, value = kind[len('nontrade_'):].split(':')
        market[-1][field] = value
    elif kind == 'negative_amount': market[0]['amount'] = '-1'
    elif kind == 'missing_amount': market[0].pop('amount')
    elif kind == 'bad_amount': market[0]['amount'] = 'not numeric'
    elif kind == 'price_order': market[0]['low'] = '10.25'
    elif kind == 'missing_price': market[0].pop('preclose')
    elif kind == 'negative_price': market[0]['close'] = '-1'
    elif kind == 'bad_volume': market[0]['volume'] = '-1'
    elif kind == 'pagination': payloads['market_daily']['pagination_complete'] = False
    elif kind == 'payload_status': payloads['market_daily']['status'] = 'PARTIAL'
    elif kind == 'firstseen': payloads['market_daily']['first_seen_at'] = '2026-01-05T15:01:00+00:00'
    elif kind == 'payload_version': payloads['market_daily']['field_version'] = 'UNSUPPORTED'
    else: raise AssertionError(kind)


@pytest.mark.parametrize('kind', [
    'master_row', 'master_identity', 'master_duplicate', 'master_status', 'empty_scope',
    'raw_row', 'raw_identity', 'raw_duplicate', 'date', 'adjustflag', 'missing_scope', 'extra_scope',
    'trade_mismatch', 'trade_unknown', 'nontrade_amount:1', 'nontrade_amount:-1',
    'nontrade_volume:1', 'nontrade_volume:-1', 'negative_amount', 'missing_amount', 'bad_amount',
    'price_order', 'missing_price', 'negative_price', 'bad_volume', 'pagination', 'payload_status',
    'firstseen', 'payload_version'])
def test_all_row_and_envelope_failures_preserve_original_error_boundary(tmp_path, kind):
    reader, receipts, paths, days, cutoff = fixture_sources(
        tmp_path, mutate=lambda _, payloads: mutate_case(kind, payloads))
    old = outcome(lambda: reader._session(resolver(receipts, days, cutoff), days[0], RETURN_BASIS))
    new = outcome(lambda: project_session_v1(reader, resolver(receipts, days, cutoff), days[0], RETURN_BASIS))
    assert old[0] != 'OK'
    assert new == old


@pytest.mark.parametrize('kind', ['missing_market', 'missing_master', 'late_event', 'late_publication',
    'late_firstseen', 'late_ingested', 'business_scope', 'partial', 'receipt_version', 'row_count', 'same_clock_revision'])
def test_parent_missing_unknown_and_invalid_receipts_match_original_without_compressing_67(tmp_path, kind):
    reader, receipts, paths, _, _ = fixture_sources(tmp_path)
    days = calendar_days(67)
    cutoff = datetime.fromisoformat(days[-1] + 'T23:59:00+00:00')
    day = days[0]
    selected = next(r for r in receipts if r.dataset == 'market_daily' and r.parameters['date'] == day)
    if kind.startswith('missing_'):
        dataset = 'market_daily' if kind == 'missing_market' else 'master_all'
        receipts = [r for r in receipts if not (r.dataset == dataset and r.parameters['date'] == day)]
    else:
        update = {}
        fields = {'late_event': 'event_time', 'late_publication': 'source_published_at',
                  'late_firstseen': 'first_seen_at', 'late_ingested': 'ingested_at'}
        if kind in fields:
            update[fields[kind]] = cutoff + timedelta(seconds=1)
            if kind == 'late_firstseen':
                update['ingested_at'] = cutoff + timedelta(seconds=1)
        elif kind == 'business_scope': update['business_sessions'] = (date.fromisoformat(days[1]),)
        elif kind == 'partial': update['status'] = 'PARTIAL'
        elif kind == 'receipt_version': update['field_version'] = 'UNSUPPORTED'
        elif kind == 'row_count': update['row_count'] = selected.row_count + 1
        elif kind == 'same_clock_revision':
            other = selected.model_copy(update={'receipt_id': selected.receipt_id + '_conflict',
                                                'content_hash': '0' * 64})
            receipts.append(other)
            paths[other.receipt_id] = paths[selected.receipt_id]
        if update:
            receipts = [r.model_copy(update=update) if r.receipt_id == selected.receipt_id else r for r in receipts]
    reader = SourceArtifactReader(receipts, paths)
    old = outcome(lambda: reader._session(resolver(receipts, days, cutoff), day, RETURN_BASIS))
    new = outcome(lambda: project_session_v1(reader, resolver(receipts, days, cutoff), day, RETURN_BASIS))
    assert old[0] != 'OK' and new == old
    if kind.startswith('missing_') or kind.startswith('late_') or kind in ('partial', 'business_scope'):
        assert 'SOURCE_UNAVAILABLE' in new[1]
        assert len(days) == 67 and day in days


def test_two_67_rounds_retain_missing_parent_unknown_at_its_original_calendar_position(tmp_path):
    reader, receipts, paths, days, cutoff = fixture_sources(tmp_path, 67)
    gap = days[33]
    receipts = [r for r in receipts if not (r.dataset == 'master_all' and r.parameters['date'] == gap)]
    reader = SourceArtifactReader(receipts, paths)
    for _ in range(2):
        states = []
        for day in days:
            old = outcome(lambda: reader._session(resolver(receipts, days, cutoff), day, RETURN_BASIS))
            new = outcome(lambda: project_session_v1(reader, resolver(receipts, days, cutoff), day, RETURN_BASIS))
            if day == gap:
                assert old == new and 'SOURCE_UNAVAILABLE' in old[1]
                states.append('UNKNOWN')
            else:
                assert old[0] == new[0] == 'OK' and old[1] == new[1]['panel']
                states.append('COMPLETE')
        assert len(states) == 67 and states[33] == 'UNKNOWN' and states.count('COMPLETE') == 66


@pytest.mark.parametrize('value', [None, '', '0', '0.000'])
def test_source_confirmed_nontrading_keeps_original_zero_or_missing_transaction_semantics(tmp_path, value):
    def change(_, payloads):
        payloads['market_daily']['rows'][-1].update(amount=value, volume=value)
    reader, receipts, paths, days, cutoff = fixture_sources(tmp_path, mutate=change)
    actual = project_session_v1(reader, resolver(receipts, days, cutoff), days[0])
    observation, panel = old_projection(reader, receipts, days, cutoff, days[0], None)
    assert (actual['observation'], actual['panel']) == (observation, panel)
    assert actual['panel']['nontrading_instrument_ids'] == ['300001.SZ']


@pytest.mark.parametrize('basis', [None, RETURN_BASIS, 'unsupported'])
def test_basis_keeps_original_output_or_rejection(tmp_path, basis):
    reader, receipts, paths, days, cutoff = fixture_sources(tmp_path)
    old = outcome(lambda: reader._session(resolver(receipts, days, cutoff), days[0], basis))
    new = outcome(lambda: project_session_v1(reader, resolver(receipts, days, cutoff), days[0], basis))
    assert new == old if old[0] != 'OK' else new[1]['panel'] == old[1]


def test_exact_objects_and_registration_cannot_restore_source_authority(tmp_path):
    reader, receipts, paths, days, cutoff = fixture_sources(tmp_path)
    class FakeReader(SourceArtifactReader):
        pass
    for false_reader in ({'verified': True}, lambda _: b'{}', FakeReader(receipts, paths)):
        with pytest.raises(ValueError, match='EXACT_SOURCE_ARTIFACT_READER_REQUIRED'):
            project_session_v1(false_reader, resolver(receipts, days, cutoff), days[0])
    with pytest.raises(ValueError, match='EXACT_EVIDENCE_RESOLVER_REQUIRED'):
        project_session_v1(reader, {'verified': True}, days[0])
    registration = source_identity()
    assert registration['schema_version'] == 'SESSION_PAYLOAD_FUSION_V1'
    assert registration['source_files']['session_payload_fusion_v1.py']
    assert all(name in registration['source_files'] for name in (
        'config/short_horizon_strength_r1.yaml', 'config/short_horizon_strength_r2.yaml',
        'docs/SHORT_HORIZON_STRENGTH_R1.md', 'docs/SHORT_HORIZON_STRENGTH_R2.md'))
    assert registration['registry_scope'] == 'DIRECT_PROJECTION_AND_POLICY_FILES_NOT_COMPLETE_CALLER_RUNTIME_UNION'
    assert registration['source_hash'] != r2.IMPLEMENTATION_VERSION
    assert registration['requires_fresh_outer_terminal_whole_sha_and_7fp'] is True
    assert registration['intermediate_second_market_read_removed'] is True
    with pytest.raises(ValueError, match='EXACT_SOURCE_ARTIFACT_READER_REQUIRED'):
        project_session_v1(registration, resolver(receipts, days, cutoff), days[0])


def test_old_and_new_first_authentication_and_fresh_round_reject_real_byte_tampering(tmp_path):
    reader, receipts, paths, days, cutoff = fixture_sources(tmp_path)
    projection = project_session_v1(reader, resolver(receipts, days, cutoff), days[0])
    path = paths[projection['panel']['source_refs'][0]]
    path.write_bytes(path.read_bytes() + b' ')
    for call in (
        lambda: reader._session(resolver(receipts, days, cutoff), days[0]),
        lambda: project_session_v1(reader, resolver(receipts, days, cutoff), days[0]),
        # A fresh whole-source terminal read is mandatory for the real caller;
        # this isolated fixture demonstrates it rejects a post-projection change.
        lambda: [reader(r.receipt_id) for r in receipts],
    ):
        with pytest.raises(ValueError, match='SHA_MISMATCH'):
            call()


def test_removed_intermediate_observation_point_is_explicit_not_hidden(tmp_path, monkeypatch):
    reader, receipts, paths, days, cutoff = fixture_sources(tmp_path)
    market = next(r for r in receipts if r.dataset == 'market_daily')
    path = paths[market.receipt_id]
    original_bytes = path.read_bytes()
    original_read = type(path).read_bytes
    reads = Counter()

    def changing_read(current):
        raw = original_read(current)
        if current == path:
            reads[current] += 1
            if reads[current] == 1:
                current.write_bytes(raw + b' ')
        return raw

    monkeypatch.setattr(type(path), 'read_bytes', changing_read)
    with pytest.raises(ValueError, match='SHA_MISMATCH'):
        reader._session(resolver(receipts, days, cutoff), days[0])
    assert reads[path] == 2
    path.write_bytes(original_bytes)
    reads.clear()
    result = project_session_v1(reader, resolver(receipts, days, cutoff), days[0])
    assert result['panel']['status'] == 'COMPLETE' and reads[path] == 1
    # No claim of full source-owner terminal PASS is present in a projection.
    assert 'verified' not in result and 'terminal_pass' not in result
    with pytest.raises(ValueError, match='SHA_MISMATCH'):
        reader(market.receipt_id)


def test_trading_optional_volume_and_no_adjusted_fields_are_not_new_hard_gates(tmp_path):
    def change(_, payloads):
        payloads['market_daily']['rows'][0]['volume'] = None
    reader, receipts, paths, days, cutoff = fixture_sources(tmp_path, mutate=change)
    actual = project_session_v1(reader, resolver(receipts, days, cutoff), days[0])
    assert actual['panel'] == reader._session(resolver(receipts, days, cutoff), days[0])
    assert actual['panel']['rows'][0]['comparable_close'] is None


def test_fusion_does_not_silently_change_legacy_unicode_digit_identity_policy(tmp_path):
    # The old master regex uses Unicode \d. Official ASCII/security qualification
    # is an outer contract; this fusion must not masquerade as its repair.
    def change(_, payloads):
        payloads['master_all']['rows'][-1]['code'] = 'sh.０００００１'
    reader, receipts, paths, days, cutoff = fixture_sources(tmp_path, mutate=change)
    actual = project_session_v1(reader, resolver(receipts, days, cutoff), days[0])
    assert actual['panel'] == reader._session(resolver(receipts, days, cutoff), days[0])
