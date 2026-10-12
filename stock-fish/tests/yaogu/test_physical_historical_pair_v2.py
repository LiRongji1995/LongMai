"""Isolated TEST_FIXTURE originals and native arms; no live market, SDK or DB."""
from copy import copy, deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
from xml.etree import ElementTree
import zipfile

import pytest

from stockfish_core.yaogu import physical_historical_pair_v2 as subject
from stockfish_core.yaogu.history_artifacts import digest_market_input
from test_physical_historical_research import capture, write


def docx(text=None, rows=None):
    ns = subject._NS['w']
    root = ElementTree.Element('{' + ns + '}document')
    body = ElementTree.SubElement(root, '{' + ns + '}body')
    if text is not None:
        ElementTree.SubElement(body, '{' + ns + '}t').text = text
    if rows is not None:
        table = ElementTree.SubElement(body, '{' + ns + '}tbl')
        for cells in rows:
            row = ElementTree.SubElement(table, '{' + ns + '}tr')
            for value in cells:
                cell = ElementTree.SubElement(row, '{' + ns + '}tc')
                ElementTree.SubElement(cell, '{' + ns + '}t').text = value
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w') as archive:
        archive.writestr('word/document.xml', ElementTree.tostring(root, encoding='utf-8'))
    return target.getvalue()


def authority_fixture(tmp_path, monkeypatch, *, edit=None):
    rows = [('6', '00', 'A股', '主板A股股票'), ('6', '01', 'A股', '主板A股股票'),
        ('6', '03', 'A股', '主板A股股票'), ('6', '05', 'A股', '主板A股股票'),
        ('6', '88', 'A股', '科创板股票'), ('6', '89', '存托凭证', '科创板存托凭证')]
    rule = '9.6.1 上市公司进入退市整理期，并在股票简称前冠以“退市”标识。9.6.2 其他规则'
    bodies = dict(code_notice=b'%PDF-TEST_FIXTURE-20260313-835', code_guide=docx(rows=rows),
        listing_notice='上证发〔2026〕42号 2026年4月24日 自发布之日起施行 2025年4月25日 上证发〔2025〕59号 同时废止'.encode(),
        listing_rules=docx(text=rule),
        listing_2025_notice='上证发〔2025〕59号 发布日期2025年04月25日 生效日期2025年04月25日 2025年4月25日 自发布之日起施行'.encode(),
        listing_2025_rules=docx(text=rule))
    if edit:
        edit(bodies)
    manifest = dict(schema_version=subject.AUTHORITY_MANIFEST, sources={})
    pins = {}
    for name, body in bodies.items():
        url = 'https://TEST_FIXTURE.invalid/authority/' + name
        seen = '2026-09-01T00:00:01+00:00'
        receipt = dict(url=url, historical_receipt_claimed=False, curl_exit_code=0,
            curl_metadata=dict(url_effective=url, http_code=200),
            request_started_at='2026-09-01T00:00:00+00:00', first_seen_at=seen,
            ingested_at=seen, completed_at='2026-09-01T00:00:02+00:00',
            body=dict(sha256=hashlib.sha256(body).hexdigest(), bytes=len(body)),
            TEST_FIXTURE=True)
        if name == 'code_guide':
            receipt.update(historical_first_seen_claimed=False,
                parent_sha256=hashlib.sha256(bodies['code_notice']).hexdigest(),
                parent_wholeSHA_unchanged=True, observed_in='PDF_PAGE_1_LINK_ANNOTATION',
                observed_literal_uri=url.replace('https:', 'http:', 1),
                routing_change='HTTP_TO_HTTPS_SCHEME_ONLY')
        body_ref = write(tmp_path / 'TEST_FIXTURE-policy-originals' / (name + '.bin'), body)
        receipt_ref = write(tmp_path / 'TEST_FIXTURE-policy-originals' / (name + '.receipt.json'), receipt)
        manifest['sources'][name] = dict(body=body_ref, receipt=receipt_ref)
        pins[name] = (body_ref['sha256'], receipt_ref['sha256'], url)
    # Only isolated fixture hashes are installed by pytest. The public factory
    # has no hash override or validator parameter and production pins stay fixed.
    monkeypatch.setattr(subject, '_AUTHORITY_PINS', pins)
    path = tmp_path / 'TEST_FIXTURE-authority-manifest.json'
    write(path, manifest)
    return path


def fixture(tmp_path, monkeypatch, *, day=date(2026, 5, 20), current_edit=None,
        basic_edit=None, codes_edit=None, factor_edit=None, historical_edit=None, missing_day=None,
        missing_master=False, factor_mode=None):
    start = date(2025, 11, 3)
    sessions, calendar_rows = [], []
    for index in range((day - start).days + 1):
        session = start + timedelta(days=index)
        trading = session.weekday() < 5
        calendar_rows.append(dict(calendar_date=str(session), is_trading_day=str(int(trading))))
        if trading:
            sessions.append(session)
    window = sessions[-26:]
    assert window[-1] == day
    root = tmp_path / 'TEST_FIXTURE-physical-originals'
    seen = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    sources = dict(schema_version='SAVED_R2_DELIVERY_SOURCES_V2', captures=[],
        raw_receipt_sources=[], business_sources=[], counted_nodes=[],
        reported_scope=None, extra_registered_files=[])
    sources['captures'].append(capture(root, 'calendar', calendar_rows,
        dict(start_date=str(start), end_date=str(day)), seen, trio=True))
    codes = ['sz.000001', 'sz.000002', 'sh.600001', 'sh.601001', 'sh.603001',
        'sh.605001', 'sh.606001', 'sh.688001', 'sh.689001', 'sz.300001']
    if codes_edit:
        codes_edit(codes)
    basic = [dict(code=code, ipoDate='2000-01-01', type='1') for code in codes]
    if basic_edit:
        basic_edit(basic)
    sources['captures'].append(capture(root, 'stock_basic_all', basic, {}, seen, trio=True))
    for index, session in enumerate(window):
        rows = [dict(code=code, date=str(session), adjustflag='3', tradestatus='1', isST='0',
            open=str(Decimal(10) + Decimal(index) / 10),
            high=str(Decimal(11) + Decimal(index) / 10),
            low=str(Decimal(9) + Decimal(index) / 10),
            close=str(Decimal('10.5') + Decimal(index) / 10),
            preclose=str(Decimal('10.4') + Decimal(index) / 10),
            volume='10000', amount='100000000', turn='5.1234') for code in codes]
        master = [dict(code=code, code_name='TEST_FIXTURE_' + code, tradeStatus='1') for code in codes]
        master.append(dict(code='sh.000001', code_name='TEST_FIXTURE_INDEX', tradeStatus='1'))
        if historical_edit:
            historical_edit(session, rows, master)
        if current_edit and session == day:
            current_edit(rows, master)
        if not missing_master or session != day:
            sources['raw_receipt_sources'].append(capture(root, 'master_all', master,
                dict(date=str(session)), seen))
        if missing_day != session:
            sources['raw_receipt_sources'].append(capture(root, 'market_daily', rows,
                dict(date=str(session)), seen))
    scoped = codes[:6]
    rows = [dict(code=code, dividOperateDate='2000-01-01', backAdjustFactor='1.2345') for code in scoped]
    parameters = dict(codes=scoped, start_date='1990-01-01', end_date=str(day))
    statuses = [dict(code=code, status='COMPLETE', row_count=1, cursor_exhausted=True,
        captured_at=seen.isoformat()) for code in scoped]
    coverage = dict(security_statuses=statuses, cursor_exhausted=True, requested_codes=scoped)
    if factor_edit:
        factor_edit(rows, parameters, coverage)
    if factor_mode in ('INHERITED', 'MISSING_ANCESTOR', 'REVISION'):
        if factor_mode != 'MISSING_ANCESTOR':
            sources['captures'].append(capture(root, 'adjustment_factors', rows, parameters, seen,
                coverage=dict(coverage, cursor_exhausted=False), trio=True, status='PARTIAL', page=False))
        if factor_mode == 'REVISION':
            rows = deepcopy(rows)
            rows[2]['backAdjustFactor'] = '2'
            coverage = deepcopy(coverage)
            for status in coverage['security_statuses']:
                status['captured_at'] = (seen + timedelta(seconds=5)).isoformat()
        factor_seen = seen + timedelta(seconds=5)
    else:
        factor_seen = seen
    partial = factor_mode == 'PARTIAL_ONLY'
    sources['captures'].append(capture(root, 'adjustment_factors', rows, parameters, factor_seen,
        trio=True, coverage=dict(coverage, cursor_exhausted=not partial),
        status='PARTIAL' if partial else 'COMPLETE', page=not partial,
        raw_clock=factor_mode != 'MISSING_RAW_CLOCK'))
    manifest = tmp_path / 'TEST_FIXTURE-sources.json'
    write(manifest, sources)
    base = tmp_path / 'TEST_FIXTURE-base-policy'
    for name in ('baostock-home.md', 'baostock-DailyUpdates.md', 'baostock-StockBasicInfoAPI.md',
        'baostock-stockBasic.md', 'baostock-factorInfo.md', 'baostock-adjustment.pdf',
        'szse-mainboard-merger.html', 'szse-stock-rules.pdf'):
        write(base / name, b'TEST_FIXTURE 17:30 18:00 000001-004999')
    authority = authority_fixture(tmp_path, monkeypatch)
    return manifest, base, authority, day, sources, window


def run(data, **kwargs):
    return subject.run_physical_historical_pair(*data[:4],
        datetime.now(timezone.utc) + timedelta(seconds=60), **kwargs)


@pytest.mark.parametrize('day', [date(2026, 4, 15), date(2026, 4, 24), date(2026, 5, 20)])
def test_all_four_supported_prefixes_real_fixture_native_same_input_pair(tmp_path, monkeypatch, day):
    data = fixture(tmp_path, monkeypatch, day=day)
    original = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = run(data)
    assert result['source_identity']['implementation_version'] == subject.VERSION
    assert result['native_attempts'] == {subject.R1: 1, subject.R2: 1}
    expected = {'000001.SZ', '000002.SZ', '600001.SH', '601001.SH', '603001.SH', '605001.SH'}
    for native in result['native_runs'].values():
        assert set(native['qualification']['eligible_ids']) == expected
        assert native['qualification']['eligible'] == 6
        assert native['input_hash'] == result['input_hash']
        assert native['decision_cutoff'] == result['decision_cutoff']
        assert native['results'][native['strategy_id']]['counts']['focus'] == 0
    assert result['input']['universe']['complete'] is False
    assert result['audit']['ascii_security_identity_audit']['rows_dropped'] == 0
    assert result['audit']['ascii_security_identity_audit']['checked_row_counts']['market_daily'] == 260
    identity_audit = result['audit']['ascii_security_identity_audit']
    assert identity_audit['actual_authenticated_payload_reads'] == 54
    assert identity_audit['actual_payload_bytes_returned'] > 0
    factor = next(row for row in data[4]['captures'] if row['typed_receipt']['dataset'] == 'adjustment_factors')
    assert isinstance(factor['typed_receipt']['parameters']['codes'], str)
    assert result['historical_PIT_count'] == result['complete_independent_cycles'] == 0
    assert result['selector_rule_changed'] is False
    assert result['frozen_V1_runs_modified'] is False
    assert result['default_service_connected'] is False
    assert result['market_edge'] == 'NOT_ESTABLISHED'
    assert result['archive_role'] == 'ARCHIVE_ONLY_NOT_RESTORABLE_SOURCE_CAPABILITY'
    assert {p: p.read_bytes() for p in original} == original
    bar = next(row for row in result['input']['bars'] if row['instrument_id'] == '600001.SH')
    assert Fraction(bar['comparable_close']) == Fraction(bar['close']) * Fraction('1.2345')
    assert Fraction(str(bar['turnover_fraction'])) == Fraction('5.1234') / 100
    assert subject.stamp(result['issued_at']) <= subject.stamp(result['derived_at']) \
        <= subject.stamp(result['decision_cutoff']) <= subject.stamp(result['terminal_verified_at'])
    securities = {row['instrument_id']: row for row in result['input']['universe']['securities']}
    assert all(securities[iid]['delisting_period'] is False for iid in expected)
    assert all(securities[iid]['board'] == 'UNKNOWN' for iid in ('606001.SH', '688001.SH', '689001.SH', '300001.SZ'))


@pytest.mark.parametrize('day', [date(2026, 3, 12), date(2026, 6, 1), date(2026, 7, 13)])
def test_outside_window_never_extends_sh_identity_or_prices(tmp_path, monkeypatch, day):
    result = run(fixture(tmp_path, monkeypatch, day=day))
    assert result['native_runs'][subject.R1]['qualification']['eligible_ids'] == ['000001.SZ', '000002.SZ']
    assert all(not bar['instrument_id'].endswith('.SH') for bar in result['input']['bars'])
    assert all(row['board'] == 'UNKNOWN' and row['delisting_period'] is None
        for row in result['input']['universe']['securities'] if row['exchange'] == 'SSE')


def test_early_price_window_can_precede_march_guide_without_backdating_qualification(tmp_path, monkeypatch):
    result = run(fixture(tmp_path, monkeypatch, day=date(2026, 4, 1)))
    sh = [row for row in result['input']['bars'] if row['instrument_id'] == '600001.SH']
    assert len(sh) == 26 and sh[0]['session'] < '2026-03-13'
    assert '600001.SH' in result['native_runs'][subject.R1]['qualification']['eligible_ids']
    assert all(subject.stamp(row['first_seen_at']).date() > date(2026, 4, 1) for row in sh)


def test_sh_prefix_delisting_and_sz_suffix_differ_without_fake_false(tmp_path, monkeypatch):
    def edit(rows, master):
        master[2]['code_name'] = '退市TEST_FIXTURE'
        master[3]['code_name'] = 'TEST_FIXTURE退'
        master[0]['code_name'] = 'TEST_FIXTURE退'
        master[4]['code_name'] = ''
    result = run(fixture(tmp_path, monkeypatch, current_edit=edit))
    securities = {row['instrument_id']: row for row in result['input']['universe']['securities']}
    assert securities['600001.SH']['delisting_period'] is True
    assert securities['601001.SH']['delisting_period'] is False
    assert securities['000001.SZ']['delisting_period'] is True
    assert securities['603001.SH']['delisting_period'] is None
    ids = result['native_runs'][subject.R1]['qualification']['eligible_ids']
    assert '600001.SH' not in ids and '000001.SZ' not in ids and '603001.SH' not in ids


@pytest.mark.parametrize('kind', ['IPO_MISSING', 'IPO_FUTURE', 'TYPE_2', 'ST_TRUE', 'ST_UNKNOWN'])
def test_sh_dated_risk_and_original_ipo_or_type_are_never_relaxed(tmp_path, monkeypatch, kind):
    def basic(rows):
        if kind == 'IPO_MISSING': rows[2]['ipoDate'] = ''
        if kind == 'IPO_FUTURE': rows[2]['ipoDate'] = '2026-05-21'
        if kind == 'TYPE_2': rows[2]['type'] = '2'
    def edit(rows, master):
        if kind == 'ST_TRUE': rows[2]['isST'] = '1'
        if kind == 'ST_UNKNOWN': rows[2]['isST'] = '?'
    result = run(fixture(tmp_path, monkeypatch, basic_edit=basic, current_edit=edit))
    assert '600001.SH' not in result['native_runs'][subject.R1]['qualification']['eligible_ids']
    if kind == 'ST_UNKNOWN':
        assert next(row for row in result['input']['universe']['securities'] if row['instrument_id'] == '600001.SH')['st'] is None


def test_sh_known_suspension_excludes_and_does_not_invent_transaction(tmp_path, monkeypatch):
    def edit(rows, master):
        rows[2].update(tradestatus='0', amount='0', volume='0')
        master[2]['tradeStatus'] = '0'
    result = run(fixture(tmp_path, monkeypatch, current_edit=edit))
    security = next(row for row in result['input']['universe']['securities'] if row['instrument_id'] == '600001.SH')
    assert security['suspended'] is True
    assert '600001.SH' not in result['native_runs'][subject.R1]['qualification']['eligible_ids']


@pytest.mark.parametrize('mutation', [
    lambda rows, master: rows.pop(),
    lambda rows, master: rows.append(deepcopy(rows[2])),
    lambda rows, master: rows[2].update(adjustflag='1'),
    lambda rows, master: rows[2].update(date='2026-05-19'),
    lambda rows, master: master[2].update(tradeStatus='0'),
    lambda rows, master: master.append(deepcopy(master[2])),
])
def test_sh_whole_scope_date_basis_status_conflicts_reject_before_native(tmp_path, monkeypatch, mutation):
    calls = []
    monkeypatch.setattr(subject.se, 'run_short_horizon_strategy', lambda *args, **kwargs: calls.append(1))
    with pytest.raises(ValueError):
        run(fixture(tmp_path, monkeypatch, current_edit=mutation))
    assert calls == []


def test_current_master_absent_preserves_unknown_denominator_and_no_native(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='CURRENT_DATED_MASTER_UNAVAILABLE_DENOMINATOR_UNKNOWN'):
        run(fixture(tmp_path, monkeypatch, missing_master=True))


@pytest.mark.parametrize('mode', ['PARTIAL_ONLY', 'MISSING_RAW_CLOCK', 'REVISION'])
def test_sh_exact_factor_parent_unknowns_preserved(tmp_path, monkeypatch, mode):
    result = run(fixture(tmp_path, monkeypatch, factor_mode=mode))
    bars = [row for row in result['input']['bars'] if row['instrument_id'] == '600001.SH']
    assert bars and all(row.get('comparable_close') is None for row in bars)
    status = next(row for row in result['native_runs'][subject.R1]['results'][subject.R1]['items'] if row['instrument_id'] == '600001.SH')
    assert status['decision_status'] == 'UNKNOWN'
    assert result['historical_PIT_count'] == 0


def test_inherited_sh_factor_requires_same_original_authenticated_ancestor(tmp_path, monkeypatch):
    result = run(fixture(tmp_path, monkeypatch, factor_mode='INHERITED'))
    assert any(row['code'] == 'sh.600001' for row in result['audit']['factor_inheritance'])
    assert any(row['status'] == 'PARTIAL' for row in result['input']['receipts'])
    assert next(row for row in result['input']['bars'] if row['instrument_id'] == '600001.SH')['comparable_close'] is not None


def test_missing_sh_ancestor_rejects_not_fabricated_from_retry(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='INHERITED_FACTOR_AUTHENTICATED_ANCESTOR_MISSING'):
        run(fixture(tmp_path, monkeypatch, factor_mode='MISSING_ANCESTOR'))


def test_missing_history_day_not_compressed_and_bar_window_stays_unknown(tmp_path, monkeypatch):
    day = date(2026, 5, 20)
    data = fixture(tmp_path, monkeypatch, day=day, missing_day=day - timedelta(days=7))
    result = run(data)
    assert len(result['audit']['sessions_26']) == 26
    assert len([row for row in result['input']['bars'] if row['instrument_id'] == '600001.SH']) == 25
    assert any(row['session'] == str(day - timedelta(days=7)) for row in result['audit']['gaps'])


@pytest.mark.parametrize('bad_part', ['code_notice', 'code_guide', 'listing_rules', 'listing_2025_rules'])
def test_source_body_changed_rejects_pinned_whole_identity(tmp_path, monkeypatch, bad_part):
    path = authority_fixture(tmp_path, monkeypatch)
    manifest = json.loads(path.read_text())
    body = Path(manifest['sources'][bad_part]['body']['path'])
    body.write_bytes(body.read_bytes() + b'TEST_FIXTURE_EDIT')
    with pytest.raises(ValueError, match='FILE_SHA_MISMATCH'):
        subject._authority_policy(path, subject._Registry(), datetime.now(timezone.utc))


@pytest.mark.parametrize('edit', [
    lambda bodies: bodies.update(code_guide=docx(rows=[('6', '00', 'A股', '主板A股股票')])),
    lambda bodies: bodies.update(listing_2025_rules=docx(text='9.6.1 后缀退规则 9.6.2 其他')),
    lambda bodies: bodies.update(listing_notice='2026年4月24日 无生效或衔接证据'.encode()),
])
def test_whole_pinned_fixture_identity_cannot_replace_required_semantics(tmp_path, monkeypatch, edit):
    path = authority_fixture(tmp_path, monkeypatch, edit=edit)
    with pytest.raises(ValueError):
        subject._authority_policy(path, subject._Registry(), datetime.now(timezone.utc))


def test_no_old_factory_or_saved_archive_capability_is_accepted(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='FACTORY_ONLY'):
        subject._PairBinding()
    with pytest.raises(ValueError, match='GENUINE_PHYSICAL_SOURCE_ISSUER_REQUIRED'):
        subject._build({}, {}, date(2026, 5, 20), 26, datetime.now(timezone.utc), None)
    data = fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='REGISTERED_26_SESSION_WINDOW_REQUIRED'):
        run(data, history_sessions=25)


def test_new_identity_binds_frozen_pair_and_separate_spec():
    identity = subject.source_identity()
    assert identity['frozen_pair_source_hash'] == subject.frozen_pair.source_identity()['source_hash']
    assert 'docs/PHYSICAL_HISTORICAL_PAIR_V2.md' in identity['source_file_hashes']
    assert identity['native_strategy_identities'] == subject.frozen_pair.source_identity()['native_strategy_identities']
    assert identity['source_policy_version'] == subject.SOURCE_POLICY_VERSION


def retained_binding(monkeypatch):
    held = []
    actual = subject._new_pair_binding
    def remember(*args, **kwargs):
        binding = actual(*args, **kwargs)
        held.append(binding)
        return binding
    monkeypatch.setattr(subject, '_new_pair_binding', remember)
    return held


def test_v2_live_seal_copy_forgery_and_cutoff_or_state_reset_cannot_consume(tmp_path, monkeypatch):
    held = retained_binding(monkeypatch)
    run(fixture(tmp_path, monkeypatch))
    binding = held[0]
    assert binding.state == 'COMPLETE'
    for action in (copy, deepcopy):
        with pytest.raises(ValueError, match='NOT_COPYABLE'):
            action(binding)
    forged = object.__new__(subject._PairBinding)
    forged.__dict__.update(binding.__dict__)
    with pytest.raises(ValueError, match='GENUINE_PAIR_FACTORY_BINDING_CHANGED'):
        forged.consume(forged.cutoff)
    with pytest.raises(ValueError, match='NATIVE_PAIR_ALREADY_CONSUMED'):
        binding.consume(binding.cutoff)
    cutoff = binding.cutoff
    binding.cutoff += timedelta(microseconds=1)
    with pytest.raises(ValueError, match='GENUINE_PAIR_FACTORY_BINDING_CHANGED'):
        binding.validate()
    binding.cutoff = cutoff
    binding.state = 'READY'
    with pytest.raises(ValueError, match='GENUINE_PAIR_FACTORY_BINDING_CHANGED'):
        binding.consume(cutoff)
    binding.state = 'COMPLETE'
    binding.validate()
    assert binding.attempts == {subject.R1: 1, subject.R2: 1}


def test_v2_consumed_native_result_and_policy_original_remain_bound(tmp_path, monkeypatch):
    held = retained_binding(monkeypatch)
    data = fixture(tmp_path, monkeypatch)
    run(data)
    binding = held[0]
    original = binding.results[subject.R1]['run_id']
    binding.results[subject.R1]['run_id'] = 'TEST_FIXTURE_FORGED_RUN'
    with pytest.raises(ValueError, match='SEALED_NATIVE_RUN_CHANGED'):
        binding.validate()
    binding.results[subject.R1]['run_id'] = original
    binding.validate()
    policy = json.loads(data[2].read_text())
    file = Path(policy['sources']['listing_2025_rules']['body']['path'])
    file.write_bytes(file.read_bytes() + b'TEST_FIXTURE_POLICY_CHANGED')
    with pytest.raises(ValueError, match='FILE_SHA_MISMATCH|REGISTERED_SOURCE_CHANGED'):
        binding.validate()


def test_v2_native_first_arm_failure_consumes_attempt_and_never_calls_second(tmp_path, monkeypatch):
    held = retained_binding(monkeypatch)
    calls = []
    def fail(*args, **kwargs):
        calls.append(kwargs['strategy_id'])
        raise RuntimeError('TEST_FIXTURE_FIRST_ARM_FAILURE')
    monkeypatch.setattr(subject.se, 'run_short_horizon_strategy', fail)
    with pytest.raises(RuntimeError, match='TEST_FIXTURE_FIRST_ARM_FAILURE'):
        run(fixture(tmp_path, monkeypatch))
    binding = held[0]
    assert binding.state == 'FAILED'
    assert binding.attempts == {subject.R1: 1, subject.R2: 0}
    with pytest.raises(ValueError, match='NATIVE_PAIR_ALREADY_CONSUMED'):
        binding.consume(binding.cutoff)
    assert calls == [subject.R1]


def test_v2_same_job_refuses_second_issuance_even_after_failure(tmp_path, monkeypatch):
    data = fixture(tmp_path, monkeypatch)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=60)
    calls = []
    actual = subject.issue_saved_r2_source_reader
    def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError('TEST_FIXTURE_ISSUER_FAILURE')
    monkeypatch.setattr(subject, 'issue_saved_r2_source_reader', fail)
    with pytest.raises(RuntimeError, match='TEST_FIXTURE_ISSUER_FAILURE'):
        subject.run_physical_historical_pair(*data[:4], deadline)
    monkeypatch.setattr(subject, 'issue_saved_r2_source_reader', actual)
    with pytest.raises(ValueError, match='REGISTERED_JOB_ALREADY_ATTEMPTED'):
        subject.run_physical_historical_pair(*data[:4], deadline)
    assert calls == [1]


@pytest.mark.parametrize('kind', ['FUTURE', 'IMPOSSIBLE_PUBLICATION', 'INVERTED', 'PIT_CLAIM'])
def test_official_receipt_clock_order_or_false_pit_rejects_even_pinned_fixture(tmp_path, monkeypatch, kind):
    manifest_path = authority_fixture(tmp_path, monkeypatch)
    manifest = json.loads(manifest_path.read_text())
    entry = manifest['sources']['listing_rules']['receipt']
    path = Path(entry['path'])
    receipt = json.loads(path.read_text())
    if kind == 'FUTURE':
        receipt.update(first_seen_at='2099-01-01T00:00:00+00:00',
            ingested_at='2099-01-01T00:00:00+00:00', completed_at='2099-01-01T00:00:01+00:00')
    if kind == 'IMPOSSIBLE_PUBLICATION':
        receipt.update(request_started_at='2025-01-01T00:00:00+00:00',
            first_seen_at='2025-01-01T00:00:01+00:00',
            ingested_at='2025-01-01T00:00:01+00:00', completed_at='2025-01-01T00:00:02+00:00')
    if kind == 'INVERTED':
        receipt['ingested_at'] = '2026-08-31T00:00:00+00:00'
    if kind == 'PIT_CLAIM':
        receipt['historical_receipt_claimed'] = True
    ref = write(path, receipt)
    pins = dict(subject._AUTHORITY_PINS)
    old = pins['listing_rules']
    pins['listing_rules'] = (old[0], ref['sha256'], old[2])
    monkeypatch.setattr(subject, '_AUTHORITY_PINS', pins)
    entry['sha256'] = ref['sha256']
    write(manifest_path, manifest)
    with pytest.raises(ValueError):
        subject._authority_policy(manifest_path, subject._Registry(), datetime.now(timezone.utc))


_INVALID_CODES = ('sh.600００１', 'sz.000００１', 'sh.600٠٠١', 'sz.000٠٠١',
                  'sh.600۰۱۱', 'sz.000१२३', 'sh.６００００１', 'sh.600001 ')


@pytest.mark.parametrize('code', _INVALID_CODES)
def test_unicode_digits_or_malformed_literal_never_become_main_or_normalized_id(code):
    policy = {'policy_id': subject.SOURCE_POLICY_VERSION}
    assert subject._iid(code) is None
    assert subject._in_price_scope(code, date(2026, 4, 15), policy) is False
    assert subject._main_board(code, date(2026, 4, 15), '1', {'amount': '100'}, policy) is False


@pytest.mark.parametrize('code', _INVALID_CODES)
def test_complete_consistent_unicode_security_source_refuses_whole_input_before_native(tmp_path, monkeypatch, code):
    calls = []
    monkeypatch.setattr(subject.se, 'run_short_horizon_strategy', lambda *args, **kwargs: calls.append(1))
    def edit(codes):
        codes[0 if code.startswith('sz.') else 2] = code
    with pytest.raises(ValueError, match='NON_ASCII_OR_MALFORMED'):
        run(fixture(tmp_path, monkeypatch, codes_edit=edit))
    assert calls == []


@pytest.mark.parametrize('dataset', ['RAW', 'MASTER', 'BASIC', 'FACTOR_ROW', 'FACTOR_REQUEST', 'FACTOR_COVERAGE', 'FACTOR_COVERAGE_REQUESTED'])
def test_each_original_identity_field_cannot_hide_illegal_unselected_or_auxiliary_rows(tmp_path, monkeypatch, dataset):
    calls = []
    monkeypatch.setattr(subject.se, 'run_short_horizon_strategy', lambda *args, **kwargs: calls.append(1))
    def current(rows, master):
        # Unsupported STAR rows also belong to the whole source denominator.
        if dataset == 'RAW': rows[7]['code'] = 'sh.688００１'
        if dataset == 'MASTER': master[7]['code'] = 'sh.688００１'
    def basic(rows):
        if dataset == 'BASIC': rows[7]['code'] = 'sh.688００１'
    def factor(rows, parameters, coverage):
        if dataset == 'FACTOR_ROW': rows[2]['code'] = 'sh.600００１'
        if dataset == 'FACTOR_REQUEST': parameters['codes'][2] = 'sh.600００１'
        if dataset == 'FACTOR_COVERAGE': coverage['security_statuses'][2]['code'] = 'sh.600００１'
        if dataset == 'FACTOR_COVERAGE_REQUESTED': coverage['requested_codes'] = ['sh.600００１']
    with pytest.raises(ValueError, match='NON_ASCII_OR_MALFORMED'):
        run(fixture(tmp_path, monkeypatch, current_edit=current, basic_edit=basic, factor_edit=factor))
    assert calls == []


@pytest.mark.parametrize('code', ['sz.000001', 'sz.004999', 'sh.600001', 'sh.601001', 'sh.603001', 'sh.605001'])
def test_ascii_literal_still_maps_exactly_within_approved_date(code):
    assert subject._iid(code) == code[3:] + '.' + code[:2].upper()
    assert subject._main_board(code, date(2026, 4, 15), '1', {'amount': '100'},
        {'policy_id': subject.SOURCE_POLICY_VERSION}) is True


@pytest.mark.parametrize('dataset', ['RAW', 'MASTER'])
def test_historical_unselected_illegal_identity_cannot_be_erased_before_replay(tmp_path, monkeypatch, dataset):
    calls = []
    monkeypatch.setattr(subject.se, 'run_short_horizon_strategy', lambda *args, **kwargs: calls.append(1))
    day = date(2026, 5, 20)
    def historical(session, rows, master):
        if session < day:
            if dataset == 'RAW': rows[7]['code'] = 'sh.688٠٠١'
            if dataset == 'MASTER': master[7]['code'] = 'sh.688٠٠١'
    with pytest.raises(ValueError, match='NON_ASCII_OR_MALFORMED'):
        run(fixture(tmp_path, monkeypatch, historical_edit=historical))
    assert calls == []


@pytest.mark.parametrize('value', ["['sh.600００１']", "['sh.600٠٠١']", "[1]", '{}'])
def test_literal_factor_request_string_rejects_non_ascii_or_wrong_scope_type(tmp_path, monkeypatch, value):
    calls = []
    monkeypatch.setattr(subject.se, 'run_short_horizon_strategy', lambda *args, **kwargs: calls.append(1))
    def factor(rows, parameters, coverage):
        parameters['codes'] = value
    with pytest.raises(ValueError, match='NON_ASCII_OR_MALFORMED_FACTOR_REQUEST_IDENTITY'):
        run(fixture(tmp_path, monkeypatch, factor_edit=factor))
    assert calls == []
