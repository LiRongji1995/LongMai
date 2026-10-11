"""Native market fact boundaries; synthetic TEST_FIXTURE, tmp originals only.

No network, business DB, real market rows, historical qualification or selector
changes. Expected mathematics is independently calculated from fixture decimals.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction as F
import hashlib
import json
from pathlib import Path

import pytest

from stockfish_core.yaogu import market_descriptive_context as native
from stockfish_core.yaogu.contracts import SourceReceipt
from stockfish_core.yaogu.history_artifacts import digest_json_stream
from stockfish_core.yaogu.research_context_artifacts import SourceArtifactReader, RETURN_BASIS
from stockfish_core.yaogu.theme_market_stream import SourceMarketStream
from tests.yaogu.test_short_horizon_strength_r2 import fixture_bundle


def fixture(tmp_path, *, alter=None, omit=(), capture_after=False, raw_seen=None,
            numeric_literal=None, stream_tail=26, basis=RETURN_BASIS):
    bundle, cutoff = fixture_bundle()
    receipts, paths = [], {}
    codes = ['sh.600001', 'sh.688001', 'sz.000001', 'sz.300001',
             'sh.600002', 'sh.688002', 'sz.000002', 'sz.300002',
             'sh.600003', 'sh.688003', 'sz.000003', 'sz.300003']
    for index, day in enumerate(bundle.sessions[-26:]):
        seen = cutoff + timedelta(seconds=1) if capture_after else next(
            bar.first_seen_at for bar in bundle.bars if bar.session == day)
        master = [{'code': code, 'code_name': 'TEST_FIXTURE', 'tradeStatus': '1'} for code in codes]
        master += [{'code': 'sh.000001', 'tradeStatus': '1'}, {'code': 'bj.830001', 'tradeStatus': '1'}]
        market = []
        for offset, code in enumerate(codes):
            close = Decimal('10') + Decimal(offset - 5) / 10
            market.append({'code': code, 'date': str(day), 'adjustflag': '3', 'tradestatus': '1',
                'open': '10', 'high': '12', 'low': '9', 'close': str(close), 'preclose': '10',
                'amount': str(1000 * (index + 1) * (offset + 1)), 'volume': '100',
                'turn': str(offset), 'isST': '0'})
        if alter:
            alter(index, day, master, market)
        for dataset, rows in (('market_daily', market), ('master_all', master)):
            if (str(day), dataset) in omit:
                continue
            rid = 'TEST_FIXTURE_MARKET_FACTS_' + dataset + '_' + str(day)
            payload = {'status': 'COMPLETE', 'pagination_complete': True,
                'field_version': native.PROVIDER_FIELD_VERSION,
                'first_seen_at': raw_seen(seen) if raw_seen else seen.isoformat(), 'rows': rows}
            raw = json.dumps(payload, sort_keys=True).encode()
            if numeric_literal and dataset == 'market_daily' and index == 25:
                field, literal = numeric_literal
                payload['rows'][0][field] = 'TEST_FIXTURE_NUMERIC_LITERAL'
                raw = json.dumps(payload, sort_keys=True).encode().replace(
                    b'"TEST_FIXTURE_NUMERIC_LITERAL"', literal.encode())
            path = tmp_path / (rid + '.json'); path.write_bytes(raw); paths[rid] = path
            receipts.append(SourceReceipt(receipt_id=rid, provider='baostock', dataset=dataset,
                content_hash=hashlib.sha256(raw).hexdigest(), field_version=native.PROVIDER_FIELD_VERSION,
                row_count=len(rows), pagination_complete=True, status='COMPLETE', parameters={'date': str(day)},
                business_sessions=(day,), event_time=next(bar.event_time for bar in bundle.bars if bar.session == day),
                source_published_at=seen, first_seen_at=seen, ingested_at=seen))
    reader = SourceArtifactReader(receipts, paths)
    stream = SourceMarketStream(reader, bundle, cutoff=cutoff, sessions=bundle.sessions[-stream_tail:], return_basis=basis)
    return bundle, cutoff, stream, paths


def run(tmp_path, bundle, cutoff, stream, *, sessions=None, name='evidence'):
    return native.run_market_descriptive_context(bundle, cutoff=cutoff, market_stream=stream,
        sessions=sessions or (bundle.observation_date,), evidence_directory=tmp_path / name)


def value(row, key):
    encoded = row[key]
    return F(encoded['numerator'], encoded['denominator']) if isinstance(encoded, dict) else encoded


def test_native_exact_math_source_identity_complete_raw_audit_and_immutable_input(tmp_path):
    bundle, cutoff, stream, paths = fixture(tmp_path)
    original = bundle.model_dump(mode='json'); original_receipts = [r.model_dump(mode='json') for r in stream.receipts]
    source_before = {rid: path.read_bytes() for rid, path in paths.items()}
    cap = run(tmp_path, bundle, cutoff, stream)
    native.validate_market_descriptive_context(cap, bundle=bundle, cutoff=cutoff)
    context = cap.context; row = context['rows'][0]
    assert value(row, 'market_amount_cny') == F(78000 * 26)
    assert (row['rising_count'], row['falling_count'], row['unchanged_count']) == (6, 5, 1)
    assert value(row, 'rising_fraction') == F(1, 2)
    assert value(row, 'median_return_1d') == F(1, 200)
    assert value(row, 'return_q25') == F(-9, 400)
    assert value(row, 'return_q75') == F(13, 400)
    assert value(row, 'return_iqr') == F(11, 200)
    assert value(row, 'top10_trading_amount_share') == F(25, 26)
    assert value(row, 'prior20_market_amount_median_cny') == F(78000 * 31, 2)
    assert value(row, 'amount_ratio_to_prior20_median') == F(52, 31)
    assert value(row, 'amount_growth_one_exchange_session') == F(1, 25)
    assert value(row, 'rising_fraction_change_one_exchange_session') == 0
    assert value(row, 'median_turn_percent') == F(11, 2)
    assert row['turn_at_least_5_percent_count'] == 7
    assert row['trading_population_changed_from_previous'] is False
    assert row['expected_instrument_count'] == row['trading_count'] == 12
    assert row['known_nontrading_count'] == 0
    assert row['prior20_sessions'] == [str(d) for d in bundle.sessions[-21:-1]]
    assert row['business_session'] not in row['prior20_sessions']
    assert len(context['dependency_sessions']) == 21 and len(context['full_observed_calendar']) == 85
    assert context['raw_field_audit_manifest']['row_count'] == 21 * (12 + 14)
    assert len(context['consumed_sources']) == 42
    assert context['market_stream_descriptor_hash'] == stream.descriptor_hash
    assert context['source_identity'] == native.source_identity()
    assert native._SPEC.relative_to(native._ROOT).as_posix() in context['source_identity']['source_file_hashes']
    assert context['selection_gate'] is False and context['official_pit_validation_count'] == 0
    assert context['historical_strategy_qualification'] == 'UNKNOWN'
    assert context['market_edge'] == 'NOT_ESTABLISHED'
    assert bundle.model_dump(mode='json') == original
    assert [r.model_dump(mode='json') for r in stream.receipts] == original_receipts
    assert {rid: path.read_bytes() for rid, path in paths.items()} == source_before
    context['rows'][0]['trading_count'] = 999
    assert cap.context['rows'][0]['trading_count'] == 12
    manifest = json.loads((tmp_path / 'evidence/manifest.json').read_bytes())
    assert manifest['context_hash'] == cap.context_hash == digest_json_stream(cap.context)
    assert cap.source_identity == context['source_identity']
    assert context['units']['market_amount_cny'] == 'CNY'
    assert context['units']['median_turn_percent'] == 'PERCENT'
    assert all(meta['sha256'] == hashlib.sha256(Path(meta['path']).read_bytes()).hexdigest()
               for meta in cap.artifact_files.values())


def test_prior20_excludes_current_spike_and_uses_exact_type7_even_population(tmp_path):
    def alter(index, day, master, market):
        if index == 25:
            for row in market: row['amount'] = str(int(row['amount']) * 100)
    bundle, cutoff, stream, _ = fixture(tmp_path, alter=alter)
    row = run(tmp_path, bundle, cutoff, stream).context['rows'][0]
    assert value(row, 'prior20_market_amount_median_cny') == F(78000 * 31, 2)
    assert value(row, 'amount_ratio_to_prior20_median') == F(5200, 31)


def test_missing_middle_calendar_day_is_unknown_without_window_compression(tmp_path):
    bundle, _ = fixture_bundle(); missing = str(bundle.sessions[-4]); after = bundle.sessions[-3]
    bundle, cutoff, stream, _ = fixture(tmp_path, omit={(missing, 'master_all')})
    cap = run(tmp_path, bundle, cutoff, stream, sessions=(after, bundle.observation_date))
    first, last = cap.context['rows']
    assert first['previous_exchange_session'] == missing
    assert first['amount_growth_one_exchange_session'] is None
    assert last['market_amount_cny'] is not None
    assert last['prior20_market_amount_median_cny'] is None
    assert last['prior20_available_count'] == 19
    assert missing in last['prior20_sessions']
    dependency = next(row for row in cap.context['dependency_session_facts'] if row['business_session'] == missing)
    assert dependency['status'] == 'UNKNOWN' and dependency['market_amount_cny'] is None


def test_unrequested_history_is_unknown_not_sourced_from_inline_prices(tmp_path):
    bundle, cutoff, stream, _ = fixture(tmp_path, stream_tail=1)
    row = run(tmp_path, bundle, cutoff, stream).context['rows'][0]
    assert value(row, 'market_amount_cny') == 78000 * 26
    assert row['prior20_market_amount_median_cny'] is None and row['prior20_available_count'] == 0
    assert row['amount_growth_one_exchange_session'] is None


def test_short_calendar_left_boundary_is_not_complete_prior20(tmp_path):
    bundle, cutoff, stream, _ = fixture(tmp_path)
    day = bundle.sessions[-26]
    row = run(tmp_path, bundle, cutoff, stream, sessions=(day,)).context['rows'][0]
    assert len(row['prior20_sessions']) == 20
    assert row['prior20_available_count'] == 0
    # Actual complete calendar existed earlier, but original source coverage did not.
    assert row['axis_status']['prior20_market_amount_median_cny']['reason_codes'] == ['MARKET_CONTEXT_PRIOR20_MISSING_SESSION']
    day = bundle.sessions[0]
    row = run(tmp_path, bundle, cutoff, stream, sessions=(day,), name='left-boundary').context['rows'][0]
    assert row['prior20_sessions'] == []
    assert row['axis_status']['prior20_market_amount_median_cny']['reason_codes'] == ['MARKET_CONTEXT_PRIOR20_LEFT_CENSORED']


def test_empty_trading_population_is_unknown_not_fabricated_zero_amount(tmp_path):
    def alter(index, day, master, market):
        if index == 25:
            for row in market: row.update(tradestatus='0', amount='0', volume='0')
            for row in master[:12]: row['tradeStatus'] = '0'
    bundle, cutoff, stream, _ = fixture(tmp_path, alter=alter)
    row = run(tmp_path, bundle, cutoff, stream).context['rows'][0]
    assert row['trading_count'] == 0 and row['known_nontrading_count'] == 12
    assert row['market_amount_cny'] is None and row['rising_count'] is None
    assert row['median_return_1d'] is None and row['median_turn_percent'] is None
    assert row['trading_population_changed_from_previous'] is True
    assert 'EMPTY_TRADING_POPULATION' in row['reason_codes'][0]


def test_reported_zero_amount_kept_but_zero_denominators_are_unknown(tmp_path):
    def alter(index, day, master, market):
        if index < 25:
            for row in market: row['amount'] = '0'
    bundle, cutoff, stream, _ = fixture(tmp_path, alter=alter)
    cap = run(tmp_path, bundle, cutoff, stream, sessions=bundle.sessions[-2:])
    previous, current = cap.context['rows']
    assert value(previous, 'market_amount_cny') == 0
    assert previous['top10_trading_amount_share'] is None
    assert value(current, 'prior20_market_amount_median_cny') == 0
    assert current['amount_ratio_to_prior20_median'] is None
    assert current['amount_growth_one_exchange_session'] is None
    assert current['axis_status']['amount_ratio_to_prior20_median']['reason_codes'] == ['MARKET_CONTEXT_ZERO_PRIOR20_DENOMINATOR']


def test_high_precision_raw_decimal_strings_and_threshold_are_not_rounded(tmp_path):
    amount = '26000.123456789012345678901234567890'
    close = '10.000000000000000000000000000001'
    def alter(index, day, master, market):
        if index == 25:
            market[0].update(amount=amount, close=close, turn='4.999999999999999999999999999999')
            market[1]['turn'] = '5.000000000000000000000000000001'
    bundle, cutoff, stream, _ = fixture(tmp_path, alter=alter)
    row = run(tmp_path, bundle, cutoff, stream).context['rows'][0]
    assert value(row, 'market_amount_cny') == 78000 * 26 - 26000 + F(Decimal(amount))
    assert row['rising_count'] == 7
    assert row['turn_at_least_5_percent_count'] == 8  # raw near-5 below does not count; second above counts.
    assert row['axis_status']['market_amount_cny']['status'] == 'AVAILABLE'


@pytest.mark.parametrize('field,unknown_axis,unaffected', [
    ('amount', 'market_amount_cny', 'median_return_1d'),
    ('close', 'median_return_1d', 'market_amount_cny'),
])
def test_json_number_precision_loss_is_unknown_in_affected_axis(tmp_path, field, unknown_axis, unaffected):
    literal = '26000.12345678901234567890123456789' if field == 'amount' else '10.000000000000000000000000000001'
    bundle, cutoff, stream, _ = fixture(tmp_path, numeric_literal=(field, literal))
    row = run(tmp_path, bundle, cutoff, stream).context['rows'][0]
    assert row[unknown_axis] is None and row[unaffected] is not None
    assert 'PRECISION_LOSS' in row['axis_status'][unknown_axis]['reason_codes'][0]
    assert row['status'] == 'PARTIAL'


def test_exact_json_numeric_roundtrip_is_available(tmp_path):
    bundle, cutoff, stream, _ = fixture(tmp_path, numeric_literal=('amount', '0.1'))
    row = run(tmp_path, bundle, cutoff, stream).context['rows'][0]
    assert value(row, 'market_amount_cny') == 78000 * 26 - 26000 + F(1, 10)


def test_precision_underflow_cannot_hide_nontrading_positive_transactions(tmp_path):
    def alter(index, day, master, market):
        if index == 25:
            master[0]['tradeStatus'] = '0'; market[0].update(tradestatus='0', amount='0', volume='0')
    bundle, cutoff, stream, _ = fixture(tmp_path, alter=alter, numeric_literal=('amount', '1e-400'))
    # Frozen standard JSON decoding sees zero. Exact original bytes are nonzero.
    with pytest.raises(ValueError, match='NONTRADING_TRANSACTION_DECIMAL_CONFLICT'):
        run(tmp_path, bundle, cutoff, stream)


@pytest.mark.parametrize('number', ['1.' + '1' * 256, '1e129'])
def test_bounded_decimal_representation_is_unknown_not_rounded_or_zero(tmp_path, number):
    def alter(index, day, master, market):
        if index == 25: market[0]['amount'] = number
    bundle, cutoff, stream, _ = fixture(tmp_path, alter=alter)
    row = run(tmp_path, bundle, cutoff, stream).context['rows'][0]
    assert row['market_amount_cny'] is None and row['median_return_1d'] is not None


@pytest.mark.parametrize('turn', [None, '', '-1', '1000.0001', 'NaN', 'Infinity', True, '1e-1000000'])
def test_missing_invalid_or_unbounded_turn_stays_unknown_without_zero_fill(tmp_path, turn):
    def alter(index, day, master, market):
        if index == 25: market[0]['turn'] = turn
    bundle, cutoff, stream, _ = fixture(tmp_path, alter=alter)
    row = run(tmp_path, bundle, cutoff, stream).context['rows'][0]
    assert row['market_amount_cny'] is not None
    assert row['turn_valid_count'] == 11 and row['turn_missing_count'] == 1
    assert row['median_turn_percent'] is None and row['turn_at_least_5_percent_count'] is None
    assert row['turn_coverage_complete'] is False


def test_late_source_capture_stays_unknown_at_original_cutoff(tmp_path):
    bundle, cutoff, stream, _ = fixture(tmp_path, capture_after=True)
    row = run(tmp_path, bundle, cutoff, stream).context['rows'][0]
    assert row['status'] == 'UNKNOWN' and row['market_amount_cny'] is None
    assert 'CAPTURE_AFTER_CUTOFF' in row['reason_codes'][0]


@pytest.mark.parametrize('seen', [lambda clock: '2099-01-01T00:00:00+00:00', lambda clock: clock.replace(tzinfo=None).isoformat()])
def test_raw_capture_clock_mismatch_or_naive_is_rejected(tmp_path, seen):
    with pytest.raises(ValueError, match='CAPTURE_CLOCK_MISMATCH'):
        fixture(tmp_path, raw_seen=seen)


def test_equivalent_timezone_offset_keeps_same_actual_receipt_clock(tmp_path):
    from datetime import timezone
    bundle, cutoff, stream, _ = fixture(tmp_path, raw_seen=lambda clock: clock.astimezone(timezone.utc).isoformat())
    cap = run(tmp_path, bundle, cutoff, stream)
    native.validate_market_descriptive_context(cap, bundle=bundle, cutoff=cutoff)


@pytest.mark.parametrize('kind', ['raw_scope', 'raw_date', 'raw_adjust', 'trade_status', 'nontrading_transaction'])
def test_native_source_guards_reject_bad_scope_date_adjust_and_status(tmp_path, kind):
    def alter(index, day, master, market):
        if index != 25: return
        if kind == 'raw_scope': market.pop()
        elif kind == 'raw_date': market[0]['date'] = str(day - timedelta(days=1))
        elif kind == 'raw_adjust': market[0]['adjustflag'] = '2'
        elif kind == 'trade_status': market[0]['tradestatus'] = '0'
        else:
            market[0]['tradestatus'] = '0'; master[0]['tradeStatus'] = '0'
    with pytest.raises(ValueError, match='SCOPE_MISMATCH|IDENTITY_DATE_BASIS|TRADE_STATUS_MISMATCH|TRANSACTION_VALUE_CONFLICT'):
        fixture(tmp_path, alter=alter)


@pytest.mark.parametrize('kind', ['dict', 'duck', 'stream_subclass', 'reader_subclass'])
def test_json_or_lookalike_is_not_native_source_capability(tmp_path, kind):
    bundle, cutoff, stream, paths = fixture(tmp_path)
    if kind == 'dict': fake = stream.descriptor
    elif kind == 'duck':
        class Duck:
            descriptor = stream.descriptor
            validate_binding = stream.validate_binding
            panel = stream.panel
        fake = Duck()
    elif kind == 'stream_subclass':
        class Derived(SourceMarketStream): pass
        fake = Derived(stream._reader, bundle, cutoff=cutoff, sessions=bundle.sessions[-26:])
    else:
        class DerivedReader(SourceArtifactReader): pass
        fake = SourceMarketStream(DerivedReader(stream.receipts, paths), bundle, cutoff=cutoff, sessions=bundle.sessions[-26:])
    with pytest.raises(ValueError, match='AUTHENTICATED_NATIVE_SOURCE_REQUIRED'):
        run(tmp_path, bundle, cutoff, fake)
    assert not (tmp_path / 'evidence').exists()


def test_explicit_nonraw_request_adjustment_cannot_grant_registered_facts(tmp_path):
    bundle, cutoff, stream, paths = fixture(tmp_path)
    receipts = [r.model_copy(update={'parameters': {**r.parameters, 'adjustflag': '2'}})
                if r.dataset == 'market_daily' else r for r in stream.receipts]
    wrong = SourceMarketStream(SourceArtifactReader(receipts, paths), bundle,
        cutoff=cutoff, sessions=bundle.sessions[-26:])
    with pytest.raises(ValueError, match='PROVIDER_DATE_OR_ADJUSTMENT_UNRECOGNIZED'):
        run(tmp_path, bundle, cutoff, wrong)


@pytest.mark.parametrize('kind', ['duplicate', 'reverse', 'future', 'empty', 'changed_cutoff', 'changed_bundle', 'wrong_basis'])
def test_request_and_input_binding_cannot_create_ready_empty(tmp_path, kind):
    bundle, cutoff, stream, _ = fixture(tmp_path, basis=None if kind == 'wrong_basis' else RETURN_BASIS)
    days = [bundle.observation_date]
    if kind == 'duplicate': days *= 2
    elif kind == 'reverse': days = list(reversed(bundle.sessions[-2:]))
    elif kind == 'future': days.append(bundle.observation_date + timedelta(days=1))
    elif kind == 'empty': days = []
    elif kind == 'changed_cutoff': cutoff += timedelta(seconds=1)
    elif kind == 'changed_bundle': bundle = bundle.model_copy(update={'coverage_type': 'PROSPECTIVE_CAPTURED'})
    with pytest.raises(ValueError, match='REQUEST_NOT_ORDERED|INPUT_BINDING_MISMATCH|REFERENCE_RETURN_BASIS_REQUIRED'):
        native.run_market_descriptive_context(bundle, cutoff=cutoff, market_stream=stream,
            sessions=days, evidence_directory=tmp_path / 'evidence')


@pytest.mark.parametrize('kind', ['scope', 'close', 'clock', 'source_ref'])
def test_even_native_capability_panel_must_match_original_descriptor(tmp_path, monkeypatch, kind):
    bundle, cutoff, stream, _ = fixture(tmp_path)
    original = SourceMarketStream.panel
    def changed(owner, day):
        panel = original(owner, day)
        if kind == 'scope': return panel.model_copy(update={'scope': 'SSE_ONLY'})
        if kind == 'clock': return panel.model_copy(update={'clocks': panel.clocks.model_copy(update={'first_seen_at': cutoff})})
        if kind == 'source_ref': return panel.model_copy(update={'source_refs': ('FAKE',)})
        bars = list(panel.rows); bars[0] = bars[0].model_copy(update={'close': Decimal('11')})
        return panel.model_copy(update={'rows': tuple(bars)})
    monkeypatch.setattr(SourceMarketStream, 'panel', changed)
    with pytest.raises(ValueError, match='PANEL_SCOPE_OR_IDENTITY_MISMATCH'):
        run(tmp_path, bundle, cutoff, stream)
    assert json.loads((tmp_path / 'evidence/failure.json').read_bytes())['output_capability_issued'] is False


@pytest.mark.parametrize('kind', ['raw', 'receipt_nested', 'original_path', 'formula_spec'])
def test_terminal_reauth_detects_during_build_mutation_of_consumed_originals(tmp_path, monkeypatch, kind):
    bundle, cutoff, stream, paths = fixture(tmp_path)
    original = native._with_trends; mutated = False
    def alter_after_consumption(day, facts, calendar):
        nonlocal mutated
        result = original(day, facts, calendar)
        if not mutated:
            mutated = True
            ref = stream.descriptor['panels'][str(bundle.sessions[-21])]['source_refs'][0]
            if kind == 'raw': paths[ref].write_bytes(paths[ref].read_bytes() + b' ')
            elif kind == 'receipt_nested': stream._reader._receipts[ref].parameters['date'] = '2099-01-01'
            elif kind == 'original_path':
                replacement = tmp_path / 'TEST_FIXTURE_same_bytes_new_path.json'; replacement.write_bytes(paths[ref].read_bytes())
                stream._reader._paths[ref] = replacement
            else: monkeypatch.setitem(native.FORMULA_SPEC, 'top10_share', 'TEST_FIXTURE_CHANGED_FORMULA')
        return result
    monkeypatch.setattr(native, '_with_trends', alter_after_consumption)
    with pytest.raises(ValueError, match='SHA_MISMATCH|RECEIPT_INVENTORY_CHANGED|CONSUMED_RECEIPT_OR_PATH_CHANGED|IMPLEMENTATION_CHANGED'):
        run(tmp_path, bundle, cutoff, stream)
    assert (tmp_path / 'evidence/failure.json').exists()


def test_return_terminal_reauth_rechecks_raw_after_context_saved(tmp_path, monkeypatch):
    bundle, cutoff, stream, paths = fixture(tmp_path)
    original = native._write
    def changed(path, document):
        result = original(path, document)
        if path.name == 'manifest.json':
            ref = stream.descriptor['panels'][str(bundle.observation_date)]['source_refs'][0]
            paths[ref].write_bytes(paths[ref].read_bytes() + b' ')
        return result
    monkeypatch.setattr(native, '_write', changed)
    with pytest.raises(ValueError, match='SHA_MISMATCH'):
        run(tmp_path, bundle, cutoff, stream)
    failure = json.loads((tmp_path / 'evidence/failure.json').read_bytes())
    assert failure['output_capability_issued'] is False and 'context' in failure['files']


@pytest.mark.parametrize('kind', ['context', 'journal', 'raw'])
def test_issued_context_validator_rechecks_originals_and_every_saved_artifact(tmp_path, kind):
    bundle, cutoff, stream, paths = fixture(tmp_path)
    cap = run(tmp_path, bundle, cutoff, stream)
    if kind == 'context':
        path = tmp_path / 'evidence/context.json'; path.write_bytes(path.read_bytes() + b' ')
    elif kind == 'journal':
        path = tmp_path / 'evidence/raw-field-audit/rows-000000.ndjson'; path.write_bytes(path.read_bytes() + b' ')
    else:
        ref = stream.descriptor['panels'][str(bundle.observation_date)]['source_refs'][0]
        paths[ref].write_bytes(paths[ref].read_bytes() + b' ')
    with pytest.raises(ValueError, match='SAVED_ARTIFACT_CHANGED|SHA_MISMATCH'):
        native.validate_market_descriptive_context(cap, bundle=bundle, cutoff=cutoff)


def test_saved_self_hashed_json_never_grants_output_capability_or_overwrite(tmp_path):
    bundle, cutoff, stream, _ = fixture(tmp_path)
    cap = run(tmp_path, bundle, cutoff, stream)
    with pytest.raises(ValueError, match='NATIVE_OUTPUT_CAPABILITY_REQUIRED'):
        native.validate_market_descriptive_context(cap.context, bundle=bundle, cutoff=cutoff)
    with pytest.raises(ValueError, match='NATIVE_PRODUCER_CAPABILITY_REQUIRED'):
        native.MarketDescriptiveContext(None, context=cap.context, stream=stream, bundle=bundle,
            cutoff=cutoff, sources={}, files={})
    before = (tmp_path / 'evidence/context.json').read_bytes()
    with pytest.raises(FileExistsError): run(tmp_path, bundle, cutoff, stream)
    assert (tmp_path / 'evidence/context.json').read_bytes() == before


def test_output_capability_is_frozen_and_private_fact_mutation_is_detected(tmp_path):
    from dataclasses import FrozenInstanceError
    bundle, cutoff, stream, _ = fixture(tmp_path)
    cap = run(tmp_path, bundle, cutoff, stream)
    with pytest.raises(FrozenInstanceError): cap._hash = 'FAKE'
    cap._context['rows'][0]['trading_count'] = 999
    with pytest.raises(ValueError, match='OUTPUT_BINDING_MISMATCH'):
        native.validate_market_descriptive_context(cap, bundle=bundle, cutoff=cutoff)


@pytest.mark.parametrize('kind', ['clear', 'delete', 'replace', 'nested_receipt', 'add'])
def test_internal_source_registry_cannot_be_weakened_to_skip_tampered_raw(tmp_path, kind):
    bundle, cutoff, stream, paths = fixture(tmp_path)
    cap = run(tmp_path, bundle, cutoff, stream)
    issued_hash = cap.context_hash
    ref = stream.descriptor['panels'][str(bundle.observation_date)]['source_refs'][0]
    paths[ref].write_bytes(paths[ref].read_bytes() + b' ')
    if kind == 'clear': cap._sources.clear()
    elif kind == 'delete': del cap._sources[ref]
    elif kind == 'replace': cap._sources[ref] = {}
    elif kind == 'nested_receipt': cap._sources[ref]['receipt']['content_hash'] = '0' * 64
    else: cap._sources['TEST_FIXTURE_UNISSUED_SOURCE'] = deepcopy(cap._sources[ref])
    assert cap.context_hash == issued_hash
    assert cap.context['consumed_sources'] != cap._sources
    with pytest.raises(ValueError, match='CONSUMED_SOURCE_REGISTRY_CHANGED'):
        native.validate_market_descriptive_context(cap, bundle=bundle, cutoff=cutoff)


@pytest.mark.parametrize('kind', ['clear', 'delete_segment', 'delete_index', 'delete_all_journal',
                                 'replace', 'nested_sha', 'nested_size', 'add'])
def test_internal_file_registry_cannot_skip_or_rebind_changed_journal(tmp_path, kind):
    bundle, cutoff, stream, _ = fixture(tmp_path)
    cap = run(tmp_path, bundle, cutoff, stream)
    issued_hash = cap.context_hash
    segment = 'raw-field-audit/rows-000000.ndjson'
    path = Path(cap._files[segment]['path']); path.write_bytes(path.read_bytes() + b' ')
    if kind == 'clear': cap._files.clear()
    elif kind == 'delete_segment': del cap._files[segment]
    elif kind == 'delete_index': del cap._files['raw-field-audit/record-index.ndjson']
    elif kind == 'delete_all_journal':
        for key in tuple(cap._files):
            if key.startswith('raw-field-audit/'): del cap._files[key]
    elif kind == 'replace': cap._files[segment] = deepcopy(cap._files['context'])
    elif kind == 'nested_sha': cap._files[segment]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif kind == 'nested_size': cap._files[segment]['bytes'] = path.stat().st_size
    else: cap._files['TEST_FIXTURE_UNISSUED_FILE'] = deepcopy(cap._files['context'])
    assert cap.context_hash == issued_hash
    with pytest.raises(ValueError, match='SAVED_FILE_REGISTRY_CHANGED'):
        native.validate_market_descriptive_context(cap, bundle=bundle, cutoff=cutoff)


def test_registry_seals_are_frozen_and_sources_must_match_hash_bound_context(tmp_path):
    from dataclasses import FrozenInstanceError
    bundle, cutoff, stream, _ = fixture(tmp_path)
    cap = run(tmp_path, bundle, cutoff, stream)
    with pytest.raises(FrozenInstanceError): cap._source_registry_hash = 'SELF_HASH'
    with pytest.raises(FrozenInstanceError): cap._file_registry_hash = 'SELF_HASH'
    assert cap._source_registry_hash == digest_json_stream(cap.context['consumed_sources'])
    assert cap._file_registry_hash == digest_json_stream(cap.artifact_files)
    # Even a deliberately rehashed private registry does not match the issued
    # context. object.__setattr__ is used only to exercise that second guard.
    cap._sources.clear()
    object.__setattr__(cap, '_source_registry_hash', digest_json_stream(cap._sources))
    with pytest.raises(ValueError, match='CONSUMED_SOURCE_REGISTRY_CHANGED'):
        native.validate_market_descriptive_context(cap, bundle=bundle, cutoff=cutoff)


def test_rehashing_a_reduced_journal_registry_does_not_replace_context_manifest(tmp_path):
    bundle, cutoff, stream, _ = fixture(tmp_path)
    cap = run(tmp_path, bundle, cutoff, stream)
    del cap._files['raw-field-audit/rows-000000.ndjson']
    object.__setattr__(cap, '_file_registry_hash', digest_json_stream(cap._files))
    with pytest.raises(ValueError, match='SAVED_FILE_REGISTRY_CHANGED'):
        native.validate_market_descriptive_context(cap, bundle=bundle, cutoff=cutoff)


def test_rehashing_journal_metadata_does_not_replace_hash_bound_journal_content(tmp_path):
    bundle, cutoff, stream, _ = fixture(tmp_path)
    cap = run(tmp_path, bundle, cutoff, stream)
    segment = 'raw-field-audit/rows-000000.ndjson'
    path = Path(cap._files[segment]['path']); path.write_bytes(path.read_bytes() + b' ')
    cap._files[segment].update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    object.__setattr__(cap, '_file_registry_hash', digest_json_stream(cap._files))
    with pytest.raises(ValueError, match='JOURNAL_REGISTRY_CONTENT_MISMATCH'):
        native.validate_market_descriptive_context(cap, bundle=bundle, cutoff=cutoff)


def test_final_raw_reauthentication_catches_changes_during_saved_file_hashing(tmp_path, monkeypatch):
    bundle, cutoff, stream, paths = fixture(tmp_path)
    cap = run(tmp_path, bundle, cutoff, stream)
    ref = stream.descriptor['panels'][str(bundle.observation_date)]['source_refs'][0]
    original = native._file_hash; changed = False
    def change_raw_during_actual_file_hash(path):
        nonlocal changed
        result = original(path)
        if not changed:
            changed = True
            paths[ref].write_bytes(paths[ref].read_bytes() + b' ')
        return result
    monkeypatch.setattr(native, '_file_hash', change_raw_during_actual_file_hash)
    issued_hash = cap.context_hash
    with pytest.raises(ValueError, match='SHA_MISMATCH'):
        native.validate_market_descriptive_context(cap, bundle=bundle, cutoff=cutoff)
    assert changed and cap.context_hash == issued_hash


def test_final_context_digest_catches_private_fact_change_during_file_audit(tmp_path, monkeypatch):
    bundle, cutoff, stream, _ = fixture(tmp_path)
    cap = run(tmp_path, bundle, cutoff, stream)
    original = native._file_hash; changed = False
    def change_fact_during_actual_file_hash(path):
        nonlocal changed
        result = original(path)
        if not changed:
            changed = True
            cap._context['rows'][0]['trading_count'] = 999
        return result
    monkeypatch.setattr(native, '_file_hash', change_fact_during_actual_file_hash)
    with pytest.raises(ValueError, match='OUTPUT_BINDING_MISMATCH'):
        native.validate_market_descriptive_context(cap, bundle=bundle, cutoff=cutoff)
    assert changed
