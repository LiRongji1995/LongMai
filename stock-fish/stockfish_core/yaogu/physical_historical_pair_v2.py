"""Explicit April/May SH/SZ physical historical research, preserving V1 runs.

The registered official code allocation and two identical delisting-marker rules
extend only the dated input factory. Native R1/R2 rules remain unchanged.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import ast
import hashlib
import io
import os
from pathlib import Path
import re
import weakref
from xml.etree import ElementTree
import zipfile

from . import physical_historical_research as physical
from . import physical_historical_pair_v1 as frozen_pair
from .contracts import DailyBar, MarketInput, SecurityRecord, UniverseSnapshot
from .history_artifacts import digest_json_stream, digest_market_input
from .provider_comparability import comparable_rows, factor_series
from .saved_r2_delivery import _Registry, _json
from .saved_r2_sources_v2 import AuthenticatedSourceArtifactsV2, issue_saved_r2_source_reader
from .research_context_artifacts import SourceArtifactReader
from .short_horizon_participation import _market_code, _market_observation, _source_parent
from .visibility import EvidenceResolver, SH, stamp
from . import strategy_experiments as se

VERSION = 'PHYSICAL_ORIGINAL_HISTORICAL_R1_R2_PAIR_V2'
FEATURE_VERSION = 'DATED_SH_SZ_APRIL_MAY_EXACT_PRICE_V2'
SOURCE_POLICY_VERSION = 'SSE_MARCH13_APRIL_MAY_BOARD_AND_DELISTING_RULE_V1'
SCHEMA = 'PHYSICAL_HISTORICAL_R1_R2_PAIR_ARCHIVE_V2'
AUTHORITY_MANIFEST = 'SSE_MARCH13_APR24_AUTHORITY_MANIFEST_V1'
R1, R2 = frozen_pair.R1, frozen_pair.R2
_TOKEN = object()
_USED_JOBS, _FACTORY_JOBS, _BINDINGS = set(), set(), {}
FIELD_VERSION = physical.FIELD_VERSION
frozen = physical.frozen
_calendar, _payload = physical._calendar, physical._payload
_factor_sources, _number = physical._factor_sources, physical._number
_turn, TURNOVER_BASIS = physical._turn, physical.TURNOVER_BASIS
_validate_native, _compare = frozen_pair._validate_native, frozen_pair._compare
_SSE_START, _SSE_END = date(2026, 4, 1), date(2026, 6, 1)
_SSE_PREFIXES = ('600', '601', '603', '605')
_NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}

# Exact, reviewed originals and actual capture receipts. No personal paths,
# current roster, caller-defined hash, publication time or validator override.
# These SHA256 identities attest local bytes, not an exchange signature or PIT.
_AUTHORITY_PINS = {
    'code_notice': (
        'ba68b1d411d5776463231af6d30b5a3e942642c4eef52efe2a7e9bcdad29f287',
        '441ace1cbc7a42af2b43bb48da9bdb156ee2999f00bf80de6fee156b36891083',
        'https://dataclouds.cninfo.com.cn/shxs/laws/spider/2026-03-16/ec4f1dbd34-0d59e1ff7bce462d34e93573ec2969ed_2.pdf'),
    'code_guide': (
        'b653be70211f5cf6337c4d966f9d306d337fa933ae557557ce83e132bb6bbd9b',
        '5f64d6544d10f740400d685513cf72fe2171ee7210f39b0c366255cccff1c806',
        'https://www.sse.com.cn/lawandrules/guide/stock/jyglywznylc/zn/c/10811730/files/4ff5228c2e6948dbbda588efa472be53.docx'),
    'listing_notice': (
        'd7957afd2f87a47f9e5d25b8411d400e9c9c80349c842d08e6296f8a80f5e466',
        '904397287a6b80cb83970259508490f6389bbcbd4264494b86889878df99fb44',
        'https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/mainipo/c/c_20260424_10816589.shtml'),
    'listing_rules': (
        '988941c6c98214908aa9438e3ea359881022d192fc3b48aa49fff95523dce627',
        '50657362a9447b328e3913dec44e95cb27a788f0ba3ac6498f45a41f4f5e959f',
        'https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/mainipo/c/10816589/files/71afbf134309400cbc54d179c05f670d.docx'),
    'listing_2025_notice': (
        'c6b5c27c4dfe0437b0248c5d84311b2e9dc87c8e775b61b510c2835bb5c27860',
        'b0e13bc004e448cfc1ffe3bac8bb7080733158cd72b9e8b29d0bbbab0a4e18bb',
        'https://www.sse.com.cn/lawandrules/sselawsrules2025/repeal/rules/c/c_20250515_10816590.shtml'),
    'listing_2025_rules': (
        '0cae84ac62ac72c1f935f0ba3c4b23a3f763d444f5706dfb59c37765576e0155',
        'e7cf8ab641911cdab8c11cdc491f27c4059851b17df0e1847f2e87d42fa3ab71',
        'https://www.sse.com.cn/lawandrules/sselawsrules2025/repeal/rules/c/10816590/files/4c610ded661a4f0aa5d29ccf614a0d82.docx'),
}


def _require(value, reason):
    if not value:
        raise ValueError('PHYSICAL_HISTORICAL_PAIR_V2_' + reason)


def source_identity():
    dependency = frozen_pair.source_identity()
    root = Path(__file__).resolve().parents[2]
    files = dict(dependency['source_file_hashes'])
    for path in (Path(__file__), root / 'docs/PHYSICAL_HISTORICAL_PAIR_V2.md'):
        files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    body = dict(implementation_version=VERSION, feature_version=FEATURE_VERSION,
        source_policy_version=SOURCE_POLICY_VERSION,
        frozen_pair_source_hash=dependency['source_hash'],
        native_strategy_source_hashes=dependency['native_strategy_source_hashes'],
        native_strategy_identities=dependency['native_strategy_identities'],
        native_parameter_states=dependency['native_parameter_states'],
        source_file_hashes=files, parameter_status='UNVALIDATED', market_edge='NOT_ESTABLISHED')
    return dict(body, source_hash=digest_json_stream(body))


def _docx_xml(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        _require(archive.namelist().count('word/document.xml') == 1,
                 'OFFICIAL_DOCX_MAIN_PART_REQUIRED')
        part = archive.getinfo('word/document.xml')
        _require(0 < part.file_size <= 2 * 1024 * 1024, 'OFFICIAL_DOCX_MAIN_PART_SIZE')
        return ElementTree.fromstring(archive.read(part))


def _authority_policy(manifest_path, registry, verified_at):
    path = Path(manifest_path).resolve(strict=True)
    manifest_raw = registry.read(path, limit=64 * 1024)
    manifest = _json(manifest_raw)
    _require(type(manifest) is dict and set(manifest) == {'schema_version', 'sources'}
        and manifest['schema_version'] == AUTHORITY_MANIFEST
        and type(manifest['sources']) is dict and set(manifest['sources']) == set(_AUTHORITY_PINS),
        'EXACT_OFFICIAL_AUTHORITY_MANIFEST_REQUIRED')
    bodies, documents, clocks = {}, {}, {}
    publication_days = dict(code_notice=date(2026, 3, 13), code_guide=date(2026, 3, 13),
        listing_notice=date(2026, 4, 24), listing_rules=date(2026, 4, 24),
        listing_2025_notice=date(2025, 4, 25), listing_2025_rules=date(2025, 4, 25))
    for name, (body_sha, receipt_sha, url) in _AUTHORITY_PINS.items():
        item = manifest['sources'][name]
        _require(type(item) is dict and set(item) == {'body', 'receipt'}, 'OFFICIAL_SOURCE_PAIR_SCHEMA')
        parts = {}
        for part, sha in (('body', body_sha), ('receipt', receipt_sha)):
            entry = item[part]
            _require(type(entry) is dict and set(entry) == {'path', 'sha256'}
                and entry['sha256'] == sha and isinstance(entry['path'], str),
                'PINNED_OFFICIAL_SOURCE_REQUIRED:' + name + ':' + part)
            value = Path(entry['path'])
            value = value if value.is_absolute() else path.parent / value
            parts[part] = registry.read(value, sha, limit=1024 * 1024)
            documents[name + '_' + part] = dict(path=str(value.resolve(strict=True)), sha256=sha)
        receipt = _json(parts['receipt'])
        meta = receipt.get('curl_metadata', {})
        claim_field = 'historical_first_seen_claimed' if name == 'code_guide' else 'historical_receipt_claimed'
        _require(receipt.get('url') == url and meta.get('url_effective') == url
            and str(meta.get('http_code')) == '200'
            and receipt.get(claim_field) is False
            and receipt.get('body', {}).get('sha256') == body_sha
            and receipt['body'].get('bytes') == len(parts['body']), 'ORIGINAL_CAPTURE_RECEIPT_CHANGED:' + name)
        if name == 'code_guide':
            _require(receipt.get('parent_sha256') == _AUTHORITY_PINS['code_notice'][0]
                and receipt.get('parent_wholeSHA_unchanged') is True
                and receipt.get('observed_in') == 'PDF_PAGE_1_LINK_ANNOTATION'
                and receipt.get('observed_literal_uri') == url.replace('https:', 'http:', 1)
                and receipt.get('routing_change') == 'HTTP_TO_HTTPS_SCHEME_ONLY',
                'OFFICIAL_NOTICE_GUIDE_ATTACHMENT_BINDING_UNPROVEN')
        _require(receipt.get('curl_exit_code', receipt.get('actual_exit_code')) == 0,
                 'ORIGINAL_CAPTURE_NOT_SUCCESSFUL:' + name)
        started = stamp(receipt['request_started_at'])
        seen, ingested = stamp(receipt['first_seen_at']), stamp(receipt['ingested_at'])
        completed = stamp(receipt.get('completed_at', receipt.get('request_ended_at',
                           receipt.get('response_completed_at'))))
        _require(seen.astimezone(SH).date() >= publication_days[name],
                 'CAPTURE_PRECEDES_DOCUMENT_BUSINESS_PUBLICATION_DAY:' + name)
        # March and Apr24 captures write completion after ingestion; this new
        # 2025 source receipt records completed HTTP response before body read.
        if 'response_completed_at' in receipt:
            _require(started <= completed <= seen <= ingested <= verified_at
                and receipt.get('body_complete') is True, 'ORIGINAL_CAPTURE_CLOCK_ORDER:' + name)
        else:
            _require(started <= seen <= ingested <= completed <= verified_at,
                     'ORIGINAL_CAPTURE_CLOCK_ORDER:' + name)
        clocks[name] = dict(request_started_at=started.isoformat(), first_seen_at=seen.isoformat(),
            ingested_at=ingested.isoformat(), original_completion_at=completed.isoformat(),
            original_historical_claim_field=claim_field, original_historical_claim=False,
            source_publication_clock='UNKNOWN_NOT_REPLACED_WITH_CAPTURE_TIME')
        bodies[name] = parts['body']
    _require(bodies['code_notice'].startswith(b'%PDF-'), 'OFFICIAL_CODE_NOTICE_PDF_REQUIRED')
    xml = _docx_xml(bodies['code_guide'])
    rows = [tuple(''.join(cell.itertext()) for cell in row.findall('w:tc', _NS))
            for row in xml.findall('.//w:tr', _NS)]
    six = [row for row in rows if row and row[0] == '6']
    expected = [('6', '00', 'A股', '主板A股股票'), ('6', '01', 'A股', '主板A股股票'),
        ('6', '03', 'A股', '主板A股股票'), ('6', '05', 'A股', '主板A股股票'),
        ('6', '88', 'A股', '科创板股票'), ('6', '89', '存托凭证', '科创板存托凭证')]
    _require(six == expected, 'COMPLETE_SSE_CODE_TABLE_CHANGED')
    rule = '在股票简称前冠以“退市”标识'
    for name in ('listing_2025_rules', 'listing_rules'):
        text = ''.join(_docx_xml(bodies[name]).itertext())
        start, end = text.find('9.6.1 '), text.find('9.6.2 ')
        _require(0 <= start < end and rule in text[start:end], 'SSE_9_6_1_PREFIX_RULE_UNPROVEN:' + name)
    notice = re.sub(r'<[^>]+>', '', bodies['listing_notice'].decode('utf-8'))
    predecessor = re.sub(r'<[^>]+>', '', bodies['listing_2025_notice'].decode('utf-8'))
    _require(all(value in notice for value in ('上证发〔2026〕42号', '2026年4月24日',
        '自发布之日起施行', '2025年4月25日', '上证发〔2025〕59号', '同时废止')),
        'NEW_LISTING_RULE_TRANSITION_UNPROVEN')
    _require(all(value in predecessor for value in ('上证发〔2025〕59号',
        '2025年04月25日', '生效日期', '2025年4月25日', '自发布之日起施行')),
        'PREDECESSOR_EFFECTIVE_DATE_UNPROVEN')
    registry.terminal()
    terminal = datetime.now(timezone.utc)
    return dict(policy_id=SOURCE_POLICY_VERSION, documents=documents, original_capture_clocks=clocks,
        manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        validation_started_at=verified_at.isoformat(), terminal_verified_at=terminal.isoformat(),
        board_authority=dict(effective_publication_day='2026-03-13',
            publication_intraday_clock='UNKNOWN', supported_observation_start=str(_SSE_START),
            supported_observation_end_exclusive=str(_SSE_END), prefixes=list(_SSE_PREFIXES),
            exact_code_rows=[list(row) for row in six], July_revision_continuity='UNKNOWN_NOT_REQUIRED_BEFORE_JULY'),
        delisting_rule_authority=dict(section='9.6.1', marker='PREFIX_退市',
            predecessor_effective_day='2025-04-25', successor_effective_day='2026-04-24',
            transition_intraday_clock='UNKNOWN', marker_clause_identical_in_both_versions=True,
            authority_scope='ONLY_OBSERVED_APRIL_MAY_MANDATORY_DELISTING_PERIOD_MARKER'),
        source_publication_clock='UNKNOWN', historical_PIT_count=0,
        historical_receipt_claimed=False, integrity_scope='LOCAL_SHA256_NOT_EXCHANGE_SIGNATURE')


def _source_policy(base_directory, authority_manifest_path, registry, verified_at):
    policy = frozen.source_policy(base_directory)
    _require(policy['board_authority']['SZSE']['range'] == ['000001', '004999']
        and policy['board_authority']['SZSE']['effective_from'] == '2021-04-06', 'SZSE_POLICY_SCOPE_CHANGED')
    for item in policy['source_documents'].values():
        registry.read(item['path'], item['sha256'])
    authority = _authority_policy(authority_manifest_path, registry, verified_at)
    policy['source_documents'].update(authority['documents'])
    policy.update(policy_id=SOURCE_POLICY_VERSION, scope_status='LIMITED_DATED_SH_SZ_SCOPE',
        physical_authority=authority, unknown_scope=['SSE_OUTSIDE_2026_APRIL_MAY_NOT_AUTHORIZED',
            'OTHER_SECURITIES_OR_MISSING_DATED_IDENTITY_UNKNOWN'],
        price_normalization_scope='DATED_SZSE_000001_004999_AND_SSE_600_601_603_605_WHEN_OBSERVATION_IN_APPROVED_APRIL_MAY')
    policy['board_authority']['SSE'] = authority['board_authority']
    return policy


def _in_price_scope(code, observation_day, policy):
    if _iid(code) is None:
        return False
    if code.startswith('sz.'):
        return '000001' <= code[3:] <= '004999'
    return bool(code.startswith('sh.') and _SSE_START <= observation_day < _SSE_END
        and policy.get('policy_id') == SOURCE_POLICY_VERSION
        and code[3:6] in _SSE_PREFIXES)


def _main_board(code, observation_day, stock_type, raw, policy):
    return bool(_in_price_scope(code, observation_day, policy) and stock_type == '1' and raw
        and (code.startswith('sh.') or observation_day >= date(2021, 4, 6)))


def _delisting(code, name, main, policy):
    if not main or not name:
        return None
    if code.startswith('sz.'):
        return name.endswith('退')
    authority = policy.get('physical_authority', {}).get('delisting_rule_authority', {})
    if authority.get('marker_clause_identical_in_both_versions') is not True:
        return None
    return name.startswith('退市')


def _iid(code):
    # Python isdigit()/regex \d include Unicode numerals. Security codes have
    # six ASCII digits; never normalize a different literal into an exchange ID.
    if not isinstance(code, str) or re.fullmatch(r'(?:sz|sh|bj)\.[0-9]{6}', code) is None:
        return None
    return code[3:] + '.' + code[:2].upper()


def _validate_ascii_security_identities(issued, deadline):
    """Authenticate whole registered code-bearing originals before any amounts.

    Invalid records are refused, including historical/unselected rows. Dropping
    them would make a complete source or denominator appear falsely complete.
    """
    checked = defaultdict(int)
    reader = issued.reader
    reads, returned_bytes = 0, 0
    started = datetime.now(timezone.utc)
    def measured_reader(receipt_id):
        nonlocal reads, returned_bytes
        raw = reader(receipt_id)
        reads += 1
        returned_bytes += len(raw)
        return raw
    for parent in issued.receipts:
        if parent.dataset not in ('market_daily', 'master_all', 'stock_basic_all',
                                  'stock_basic', 'adjustment_factors'):
            continue
        deadline.check()
        body = _payload(parent, measured_reader)
        for row in body['rows']:
            _require(isinstance(row, dict) and _iid(row.get('code')) is not None,
                     'NON_ASCII_OR_MALFORMED_SECURITY_IDENTITY:' + parent.dataset)
            checked[parent.dataset] += 1
        if parent.dataset == 'adjustment_factors':
            codes = parent.parameters.get('codes')
            if isinstance(codes, str):
                codes = ast.literal_eval(codes)
            _require(isinstance(codes, list) and all(_iid(code) is not None for code in codes),
                     'NON_ASCII_OR_MALFORMED_FACTOR_REQUEST_IDENTITY')
            coverage = body.get('coverage', {})
            _require(isinstance(coverage, dict), 'FACTOR_COVERAGE_MAPPING_REQUIRED')
            if 'requested_codes' in coverage:
                reported = coverage['requested_codes']
                _require(isinstance(reported, list) and all(_iid(code) is not None for code in reported),
                         'NON_ASCII_OR_MALFORMED_FACTOR_COVERAGE_REQUEST_IDENTITY')
            statuses = coverage.get('security_statuses', [])
            _require(isinstance(statuses, list), 'FACTOR_SECURITY_COVERAGE_LIST_REQUIRED')
            for record in statuses:
                _require(isinstance(record, dict) and _iid(record.get('code')) is not None,
                         'NON_ASCII_OR_MALFORMED_FACTOR_COVERAGE_IDENTITY')
    issued.validate_binding()
    return dict(policy='EXACT_EXCHANGE_PREFIX_AND_SIX_ASCII_DIGITS_NO_NORMALIZATION',
        checked_row_counts=dict(checked), rows_dropped=0,
        actual_authenticated_payload_reads=reads, actual_payload_bytes_returned=returned_bytes,
        preflight_started_at=started.isoformat(),
        preflight_completed_at=datetime.now(timezone.utc).isoformat(),
        measurement_scope='ASCII_PREFLIGHT_PAYLOAD_ONLY_OUTER_MUST_COUNT_ALL_REGISTRY_AND_NATIVE_READS')


def _build(issued, policy, day, history_sessions, derived, deadline):
    _require(type(issued) is AuthenticatedSourceArtifactsV2, 'GENUINE_PHYSICAL_SOURCE_ISSUER_REQUIRED')
    issued.validate_binding()
    reader, receipts = issued.reader, issued.receipts
    identity_audit = _validate_ascii_security_identities(issued, deadline)
    calendar = _calendar(receipts, reader)
    _require(day in calendar, 'OBSERVATION_NOT_VERIFIED_TRADING_SESSION')
    window = tuple(d for d in calendar if d <= day)[-history_sessions:]
    _require(len(window) == history_sessions, 'ACTUAL_CALENDAR_WINDOW_INCOMPLETE')
    resolver = EvidenceResolver(receipts, cutoff=derived, coverage_type='RECONSTRUCTED_HISTORICAL',
        sessions=tuple(map(str, calendar)), observation_date=str(day), record_rows=False)
    gaps, raw_by, masters = [], defaultdict(list), {}
    for session in window:
        deadline.check()
        try:
            _market_observation(resolver, str(session), reader)
        except ValueError as error:
            if not str(error).startswith(('SHR2_MARKET_MARKET_DAILY_SOURCE_UNAVAILABLE',
                                          'SHR2_MARKET_MASTER_ALL_SOURCE_UNAVAILABLE')):
                raise
            gaps.append(dict(session=str(session), field='DAILY_RAW_AND_MASTER', reason=str(error)))
            # A missing price parent cannot erase an independently complete
            # dated directory into an apparent empty qualification pool.
            try:
                master = _source_parent(resolver, str(session), 'master_all')
            except ValueError as missing_master:
                if not str(missing_master).startswith('SHR2_MARKET_MASTER_ALL_SOURCE_UNAVAILABLE'):
                    raise
            else:
                master_rows = _payload(master, reader)['rows']
                master_codes = [row.get('code') for row in master_rows]
                _require(len(master_codes) == len(set(master_codes))
                    and all(_iid(code) for code in master_codes)
                    and all(str(row.get('tradeStatus')) in ('0','1') for row in master_rows),
                    'DATED_MASTER_IDENTITY_OR_STATUS_CONFLICT')
                _require(any(_market_code(code) for code in master_codes), 'DATED_MASTER_A_SHARE_SCOPE_EMPTY')
                masters[session] = (master, master_rows)
            continue
        market = _source_parent(resolver, str(session), 'market_daily')
        master = _source_parent(resolver, str(session), 'master_all')
        masters[session] = (master, _payload(master, reader)['rows'])
        for row in _payload(market, reader)['rows']:
            code = row['code']
            if _in_price_scope(code, day, policy):
                raw_by[code].append((session, (row, market.receipt_id)))
    factors, inheritance = _factor_sources(receipts, reader, issued.audits, deadline)
    bars, numeric = [], []
    for code, rows in sorted(raw_by.items()):
        deadline.check()
        iid = _iid(code)
        all_fragments, authority = [], []
        for capture in factors:
            if code not in capture['fragments']:
                continue
            parent = capture['parent']
            fragment = capture['fragments'][code][0]
            scoped = [row for row in fragment if frozen._day(row.get('dividOperateDate'))
                      and frozen._day(row['dividOperateDate']) <= day]
            value = (scoped, parent.receipt_id, str(min(capture['end'], day)), str(capture['start']))
            all_fragments.append(value)
            if (parent.status == 'COMPLETE' and parent.pagination_complete
                    and capture['raw_first_seen_present']):
                authority.append(value)
        # Audit fragments in chronological order so insertion/omission revisions
        # cannot be hidden by choosing the newest successful batch alone.
        order = {r.receipt_id: (r.first_seen_at, r.ingested_at, r.receipt_id) for r in receipts}
        all_fragments.sort(key=lambda c: order[c[1]])
        authority.sort(key=lambda c: order[c[1]])
        oracle, oracle_quality = comparable_rows(iid, rows, {}, all_fragments)
        _, _, conflict = factor_series(all_fragments)
        values, quality = comparable_rows(iid, rows, {}, authority)
        authority_series, _, _ = factor_series(authority)
        if not authority and any(code in item['fragments'] and item['parent'].status == 'COMPLETE'
                and not item['raw_first_seen_present'] for item in factors):
            quality = 'COMPLETE_FACTOR_RAW_CAPTURE_CLOCK_UNKNOWN'
        if conflict or oracle_quality == 'FACTOR_REVISION_CONFLICT_REQUIRES_REVIEW':
            values, quality = {}, 'ALL_ATTEMPT_FACTOR_REVISION_CONFLICT'
        for session, (raw, raw_ref) in rows:
            comparable, refs = values.get(session, ({}, (raw_ref,)))
            if comparable and (session not in oracle or oracle[session][0] != comparable):
                comparable, refs, quality = {}, (raw_ref,), 'ALL_ATTEMPT_AUTHORITY_EXACT_LINK_UNPROVEN'
            if comparable:
                factor = next((value for effective,value in reversed(authority_series)
                               if effective <= str(session)), None)
                if factor is None or any(Fraction(comparable['comparable_'+key]) !=
                        Fraction(Decimal(str(raw[key]))) * Fraction(factor)
                        for key in ('open','high','low','close')):
                    comparable, refs, quality = {}, (raw_ref,), 'SHARED_DECIMAL_EXACT_PRODUCT_UNPROVEN'
            values_raw = {field: _number(raw.get(source), positive=field not in ('volume_shares', 'amount_cny'))
                for field, source in (('open', 'open'), ('high', 'high'), ('low', 'low'),
                    ('close', 'close'), ('reference_prev_close', 'preclose'),
                    ('volume_shares', 'volume'), ('amount_cny', 'amount'))}
            if (any(values_raw[key] is None for key in ('open','high','low','close','reference_prev_close'))
                or not values_raw['low'] <= min(values_raw['open'], values_raw['close'])
                       <= max(values_raw['open'], values_raw['close']) <= values_raw['high']):
                gaps.append(dict(instrument_id=iid, session=str(session), field='RAW_PRICE', reason='INVALID_OR_NONTRADING_RAW'))
                continue
            turnover, exact_turn, turn_reason = _turn(raw.get('turn'))
            clocks = dict(event_time=datetime.combine(session, time(15), SH),
                source_published_at=max(resolver.receipts[ref].source_published_at for ref in refs),
                first_seen_at=derived, ingested_at=derived)
            bars.append(DailyBar(instrument_id=iid, session=session, **values_raw, **comparable,
                turnover_fraction=turnover, turnover_basis=TURNOVER_BASIS if turnover is not None else None,
                source_refs=refs, **clocks))
            numeric.append(dict(instrument_id=iid, session=str(session), row_hash=digest_json_stream(raw),
                raw_source_ref=raw_ref, comparable_status=quality, source_refs=list(refs),
                turnover_exact=dict(numerator=exact_turn.numerator,denominator=exact_turn.denominator)
                    if exact_turn is not None else None, turnover_reason=turn_reason))
    basic, conflicts = {}, set()
    for parent in receipts:
        if parent.dataset not in ('stock_basic_all', 'stock_basic') or parent.provider != 'baostock':
            continue
        if parent.status != 'COMPLETE' or not parent.pagination_complete:
            continue
        _require(parent.field_version == FIELD_VERSION, 'STOCK_BASIC_SCHEMA_UNRECOGNIZED')
        for row in _payload(parent, reader)['rows']:
            code = row.get('code')
            _require(_iid(code) is not None, 'STOCK_BASIC_IDENTITY_INVALID')
            fact = (row.get('ipoDate'), row.get('type'))
            if code in basic and basic[code][0] != fact:
                conflicts.add(code)
            basic[code] = (fact, parent.receipt_id)
    _require(day in masters, 'CURRENT_DATED_MASTER_UNAVAILABLE_DENOMINATOR_UNKNOWN')
    securities, current_master_ref, expected = [], (), None
    if day in masters:
        master, master_rows = masters[day]
        current_master_ref, expected = (master.receipt_id,), len(master_rows)
        current_raw = {code: (raw, rid) for code, rows in raw_by.items() for session, (raw,rid) in rows if session == day}
        for row in master_rows:
            code, iid = row['code'], _iid(row['code'])
            fact, basic_ref = basic.get(code, ((None,None), None))
            ipo = frozen._day(fact[0])
            if ipo is None or code in conflicts or ipo > day:
                gaps.append(dict(instrument_id=iid, session=str(day), field='HISTORICAL_IDENTITY', reason='IPO_OR_TYPE_UNKNOWN_OR_CONFLICT'))
                continue
            raw, raw_ref = current_raw.get(code, ({}, None))
            main = _main_board(code, day, fact[1], raw, policy)
            a, b = str(raw.get('tradestatus')), str(row.get('tradeStatus'))
            name = str(row.get('code_name') or '')
            refs = (master.receipt_id, basic_ref, *((raw_ref,) if raw_ref else ()))
            securities.append(SecurityRecord(instrument_id=iid,
                exchange={'sz':'SZSE','sh':'SSE','bj':'BSE'}[code[:2]], board='MAIN' if main else 'UNKNOWN',
                security_type='A_COMMON' if main else 'UNKNOWN', listed_on=ipo,
                effective_from=day, effective_to=day+timedelta(days=1),
                st=raw.get('isST') == '1' if raw.get('isST') in ('0','1') else None,
                suspended=a == '0' if a in ('0','1') and a == b else None,
                delisting_period=_delisting(code, name, main, policy),
                name=name, source_refs=refs, risk_flags=('CURRENT_VENDOR_HISTORICAL_REVISION_RISK',),
                event_time=datetime.combine(day,time(15),SH),
                source_published_at=max(resolver.receipts[ref].source_published_at for ref in refs),
                first_seen_at=derived, ingested_at=derived))
    universe = UniverseSnapshot(snapshot_id='physical_historical_'+digest_json_stream([s.model_dump(mode='json') for s in securities]),
        securities=tuple(securities), source_refs=current_master_ref, complete=False, expected_count=expected,
        event_time=datetime.combine(day,time(15),SH), source_published_at=derived,
        first_seen_at=derived, ingested_at=derived)
    bundle = MarketInput(observation_date=day, sessions=calendar, universe=universe, bars=tuple(bars),
        receipts=receipts, coverage_type='RECONSTRUCTED_HISTORICAL')
    # The whole newly constructed view is first recorded only after construction;
    # the source receipts keep their original clocks without alteration.
    completed = datetime.now(timezone.utc)
    securities = tuple(s.model_copy(update={'first_seen_at':completed, 'ingested_at':completed})
                       for s in securities)
    universe = universe.model_copy(update={'securities':securities,
        'source_published_at':completed, 'first_seen_at':completed, 'ingested_at':completed})
    bundle = bundle.model_copy(update={'universe':universe, 'bars':tuple(
        b.model_copy(update={'first_seen_at':completed, 'ingested_at':completed}) for b in bars)})
    issued.validate_binding()
    return bundle, dict(source_version=VERSION, feature_version=FEATURE_VERSION,
        ascii_security_identity_audit=identity_audit,
        source_policy_version=SOURCE_POLICY_VERSION,
        observation_date=str(day), sessions_26=list(map(str,window)),
        derivation_started_at=derived.isoformat(), actual_derived_at=completed.isoformat(),
        derived_clock_role='FIRST_RECORD_AFTER_VIEW_CONSTRUCTION_NOT_SOURCE_CAPTURE_TIME',
        policy=policy, original_source_audits=[dict(row,
            original_result_wrapper_status='UNKNOWN_NOT_REQUIRED_BY_PHYSICAL_SOURCE_MODE'
                if row.get('authentication_mode') == 'WHOLE_RAW_AND_PHYSICAL_RECEIPT'
                else 'AUTHENTICATED_ORIGINAL_TRIO') for row in issued.audits], factor_inheritance=inheritance,
        numeric_observations=numeric, gaps=gaps, raw_price_scope='DATED_SZSE_000001_004999_AND_APPROVED_SSE_600_601_603_605',
        scope='LIMITED_DATED_SH_SZ_MAINBOARD_OTHER_HISTORICAL_IDENTITY_UNKNOWN',
        original_result_wrappers_reconstructed=False, original_source_clocks_modified=False,
        historical_PIT_count=0, complete_independent_cycles=0, market_edge='NOT_ESTABLISHED')


class _PairBinding:
    def __new__(cls, *_args, **_kwargs):
        raise ValueError('PHYSICAL_HISTORICAL_PAIR_FACTORY_ONLY')

    def __copy__(self):
        raise ValueError('PHYSICAL_HISTORICAL_PAIR_LIVE_BINDING_NOT_COPYABLE')

    def __deepcopy__(self, _memo):
        raise ValueError('PHYSICAL_HISTORICAL_PAIR_LIVE_BINDING_NOT_COPYABLE')

    def validate(self):
        held = _BINDINGS.get(id(self))
        _require(type(self) is _PairBinding and self._token is _TOKEN and held is not None
            and held['owner']() is self and held['pid'] == os.getpid()
            and held['job'] == self.job and self.job in _USED_JOBS and self.job in _FACTORY_JOBS
            and held['issued_id'] == id(self.issued) and held['bundle_id'] == id(self.bundle)
            and held['reader_id'] == id(self.reader) and held['policy_registry_id'] == id(self.policy_registry)
            and held['deadline_id'] == id(self.deadline) and held['deadline_end'] == self.deadline.end
            and held['input_hash'] == self.input_hash and held['cutoff'] == self.cutoff
            and held['identity_hash'] == digest_json_stream(self.identity)
            and held['policy_registry_hash'] == self._policy_registry_hash
            and held['receipts_hash'] == digest_json_stream([row.model_dump(mode='json') for row in self.receipts])
            and held['state'] == self.state and held['attempts'] == self.attempts,
            'GENUINE_PAIR_FACTORY_BINDING_CHANGED')
        _require(source_identity() == self.identity, 'IMPLEMENTATION_CHANGED')
        _require(digest_market_input(self.bundle) == self.input_hash, 'DERIVED_INPUT_CHANGED')
        _require(type(self.issued) is AuthenticatedSourceArtifactsV2
            and type(self.reader) is SourceArtifactReader
            and self.issued._reader is self.reader and self.issued.receipts == self.receipts,
            'LIVE_ISSUER_READER_CHANGED')
        self.issued.validate_binding()
        _require(digest_json_stream(self.policy_registry.files) == self._policy_registry_hash,
            'POLICY_REGISTRY_CHANGED')
        self.policy_registry.terminal()
        _require(set(self.results) == set(held['results']), 'SEALED_NATIVE_RUN_SET_CHANGED')
        for strategy, record in held['results'].items():
            _require(id(self.results[strategy]) == record['object_id']
                and digest_json_stream(self.results[strategy]) == record['content_hash'],
                'SEALED_NATIVE_RUN_CHANGED:' + strategy)
        self.deadline.check()

    def _state(self, value):
        held = _BINDINGS[id(self)]
        permitted = {'READY': {'CONSUMING'}, 'CONSUMING': {'COMPLETE', 'FAILED'},
                     'COMPLETE': set(), 'FAILED': set()}
        _require(value in permitted[held['state']], 'ILLEGAL_PAIR_STATE_TRANSITION')
        _require(value == 'FAILED' or self.state == held['state'], 'PAIR_STATE_CHANGED')
        held['state'] = self.state = value

    def _arm(self, strategy):
        self.validate()
        _require(self.state == 'CONSUMING' and self.attempts[strategy] == 0, 'NATIVE_ARM_ALREADY_ATTEMPTED')
        self.attempts[strategy] = _BINDINGS[id(self)]['attempts'][strategy] = 1
        arguments = dict(cutoff=self.cutoff, input_id='physical_historical_pair_' + self.input_hash,
                         strategy_id=strategy)
        if strategy == R2:
            # Genuine SourceArtifactReader, never a supplied lambda or JSON decoder.
            arguments.update(_source_payload_reader=self.reader, _source_receipts=self.receipts)
        run = se.run_short_horizon_strategy(self.bundle, **arguments)
        _validate_native(run, strategy, self.bundle, self.input_hash, self.cutoff,
            self.identity['native_strategy_identities'][strategy])
        self.results[strategy] = run
        _BINDINGS[id(self)]['results'][strategy] = dict(object_id=id(run), content_hash=digest_json_stream(run))
        self.validate()
        return run

    def consume(self, cutoff):
        self.validate()
        _require(stamp(cutoff) == self.cutoff, 'NATIVE_EXACT_CUTOFF_CHANGED')
        _require(self.state == 'READY', 'NATIVE_PAIR_ALREADY_CONSUMED')
        self._state('CONSUMING')
        try:
            left, right = self._arm(R1), self._arm(R2)
            comparison = _compare(left, right, self.bundle, self.input_hash, self.cutoff,
                self.identity['native_strategy_identities'])
            self.validate()
        except BaseException:
            self._state('FAILED')
            raise
        self._state('COMPLETE')
        return self.results, comparison


def _new_pair_binding(issued, bundle, identity, policy_registry, job, cutoff, deadline):
    _require(job in _USED_JOBS and job not in _FACTORY_JOBS, 'REGISTERED_FACTORY_ALREADY_ISSUED')
    _require(type(issued) is AuthenticatedSourceArtifactsV2, 'GENUINE_PHYSICAL_SOURCE_ISSUER_REQUIRED')
    issued.validate_binding()
    _FACTORY_JOBS.add(job)
    owner = object.__new__(_PairBinding)
    owner._token, owner.issued, owner.bundle, owner.identity = _TOKEN, issued, bundle, identity
    owner.reader, owner.receipts = issued.reader, issued.receipts
    owner.policy_registry, owner.deadline = policy_registry, deadline
    owner._policy_registry_hash = digest_json_stream(policy_registry.files)
    owner.input_hash, owner.cutoff, owner.job = digest_market_input(bundle), stamp(cutoff), job
    owner.state, owner.attempts, owner.results = 'READY', {R1:0,R2:0}, {}
    key = id(owner)
    _BINDINGS[key] = dict(owner=weakref.ref(owner, lambda _: _BINDINGS.pop(key, None)),
        pid=os.getpid(), job=job, issued_id=id(issued), bundle_id=id(bundle), reader_id=id(owner.reader),
        policy_registry_id=id(policy_registry), policy_registry_hash=owner._policy_registry_hash,
        deadline_id=id(deadline), deadline_end=deadline.end,
        input_hash=owner.input_hash, cutoff=owner.cutoff, identity_hash=digest_json_stream(identity),
        receipts_hash=digest_json_stream([row.model_dump(mode='json') for row in owner.receipts]),
        state='READY', attempts={R1:0,R2:0}, results={})
    return owner


def run_physical_historical_pair(sources_manifest_path, source_policy, authority_manifest_path, observation_date,
        registered_worker_deadline, history_sessions=26):
    """Fresh original issuance and one same-input/cutoff native R1/R2 comparison.

    No external input, callback, saved base run, selector override, or history DB.
    Only the new module's explicitly invoked research entry is affected.
    """
    _require(isinstance(sources_manifest_path, (str, Path)) and isinstance(source_policy, (str, Path))
        and isinstance(authority_manifest_path, (str, Path)),
        'REAL_SOURCE_AND_POLICY_PATHS_REQUIRED')
    _require(type(history_sessions) is int and history_sessions == 26, 'REGISTERED_26_SESSION_WINDOW_REQUIRED')
    day = date.fromisoformat(str(observation_date))
    deadline = physical._Deadline(registered_worker_deadline)
    path = Path(sources_manifest_path).resolve(strict=True)
    job = (str(path), str(day), deadline.end.isoformat())
    _require(job not in _USED_JOBS, 'REGISTERED_JOB_ALREADY_ATTEMPTED')
    _USED_JOBS.add(job)
    with deadline:
        identity = source_identity()
        policy_registry = _Registry()
        policy_verified_at = datetime.now(timezone.utc)
        policy = _source_policy(source_policy, authority_manifest_path, policy_registry, policy_verified_at)
        issued_at = datetime.now(timezone.utc)
        issued = issue_saved_r2_source_reader(sources_manifest_path=path, cutoff=issued_at)
        _require(type(issued) is AuthenticatedSourceArtifactsV2, 'GENUINE_PHYSICAL_SOURCE_ISSUER_REQUIRED')
        started_at = datetime.now(timezone.utc)
        bundle, audit = _build(issued, policy, day, history_sessions, started_at, deadline)
        derived_at = stamp(audit['actual_derived_at'])
        cutoff = datetime.now(timezone.utc)
        _require(cutoff >= derived_at >= started_at >= issued_at, 'ACTUAL_CLOCK_ORDER_INVALID')
        binding = _new_pair_binding(issued, bundle, identity, policy_registry, job, cutoff, deadline)
        native_runs, comparison = binding.consume(cutoff)
        binding.validate()
        terminal_at = datetime.now(timezone.utc)
        deadline.check()
        return dict(schema_version=SCHEMA, source_identity=identity,
            native_strategy_identities=identity['native_strategy_identities'],
            issued_at=issued_at.isoformat(), derived_at=derived_at.isoformat(), decision_cutoff=cutoff.isoformat(),
            terminal_verified_at=terminal_at.isoformat(), registered_worker_deadline=deadline.end.isoformat(),
            native_attempts=dict(binding.attempts), native_r1_evaluations_expected=2,
            native_R2_recomputes_R1_by_frozen_design=True, selector_rule_changed=False,
            source_policy_version=SOURCE_POLICY_VERSION, frozen_V1_runs_modified=False,
            normalization_view_role='ONE_NEW_PHYSICAL_DERIVED_INPUT_SAME_CUTOFF',
            input_hash=binding.input_hash, input=bundle.model_dump(mode='json'),
            native_runs=native_runs, native_run_hashes={key:digest_json_stream(value) for key,value in native_runs.items()},
            comparison=comparison, source_registry=issued.registry.files, policy_registry=policy_registry.files,
            audit=audit, historical_qualification_scope=audit['scope'], historical_theme_membership='UNKNOWN',
            historical_PIT_count=0, complete_independent_cycles=0, market_edge='NOT_ESTABLISHED',
            default_service_connected=False, archive_role='ARCHIVE_ONLY_NOT_RESTORABLE_SOURCE_CAPABILITY')
