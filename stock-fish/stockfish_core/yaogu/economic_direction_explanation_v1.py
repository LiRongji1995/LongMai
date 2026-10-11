"""Opt-in archived explanation; no selector, theme or source capability issued.

Business relations, counted provider nodes and saved native subtheme facts have
different meanings. They remain separate even when displayed under one heading.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from .history_artifacts import digest_json_stream

VERSION = 'ECONOMIC_DIRECTION_EXPLANATION_V1'
FEATURE_VERSION = 'FINITE_OFFICIAL_BUSINESS_RELATIONS_PRESERVED_NATIVE_SUBTHEMES_V1'
NODE_CONTRACT = 'CURRENT_WHOLE_COUNTED_PROVIDER_NODE_V2'
MAX_FILE_BYTES = 8 * 1024**2
ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / 'docs' / 'ECONOMIC_DIRECTION_EXPLANATION_V1.md'
DIRECTIONS = ('BATTERY', 'RETAIL_CONSUMPTION')
CATEGORIES = ('confirmed_mainlines', 'fermentation_watch', 'active_divergence_mainlines')
CAPTURE_CLOCKS = ('request_started_at', 'response_header_seen_at', 'body_saved_at', 'request_completed_at')


@dataclass(frozen=True)
class RegisteredFile:
    """A pinned file location, not an authenticity or capability declaration."""
    path: Path
    sha256: str


@dataclass(frozen=True)
class OfficialBusinessRegistration:
    instrument_id: str
    name: str
    direction: str
    relation: str
    summary: str
    boundary: str
    covered_period: str
    publication_date_locator: str
    business_page: int
    literals: tuple[str, ...]
    original: RegisteredFile
    receipt: RegisteredFile
    derived_text: RegisteredFile
    transform: RegisteredFile


@dataclass(frozen=True)
class CountedNodeRegistration:
    node: str
    literal_name: str
    endpoint: str
    capture_root: Path
    result: RegisteredFile
    # The original whole receipt/RAW pairs, including the normalized parent.
    originals: tuple[tuple[RegisteredFile, RegisteredFile], ...]
    directory_receipt: RegisteredFile
    directory_raw: RegisteredFile


def _aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('ECONOMIC_EXPLANATION_AWARE_CLOCK_REQUIRED')
    return value


def _object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('ECONOMIC_EXPLANATION_DUPLICATE_JSON_KEY')
        out[key] = value
    return out


def _json(body):
    return json.loads(body, object_pairs_hook=_object,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))


def _read(file):
    if not isinstance(file, RegisteredFile) or not re.fullmatch(r'[0-9a-f]{64}', file.sha256):
        raise ValueError('ECONOMIC_EXPLANATION_REGISTERED_ORIGINAL_REQUIRED')
    path = Path(file.path)
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('ECONOMIC_EXPLANATION_SOURCE_UNAVAILABLE_OR_TOO_LARGE')
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != file.sha256:
        raise ValueError('ECONOMIC_EXPLANATION_ORIGINAL_SHA_MISMATCH')
    return body


def _fp(file):
    _read(file)
    s = Path(file.path).stat()
    return dict(sha256=file.sha256, bytes=s.st_size, mtime_ns=s.st_mtime_ns,
                inode=s.st_ino, device=s.st_dev, mode=s.st_mode)


def source_identity():
    names = ('economic_direction_explanation_v1.py', 'history_artifacts.py',
             'historical_replay.py', 'historical_replay_turnover.py')
    files = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in (Path(__file__).with_name(name) for name in names)}
    files[str(SPEC.relative_to(ROOT))] = hashlib.sha256(SPEC.read_bytes()).hexdigest()
    identity = dict(implementation_version=VERSION, feature_version=FEATURE_VERSION,
                    counted_node_contract=NODE_CONTRACT, source_file_hashes=files,
                    source_capability_issued=False, explicit_opt_in=True)
    return {**identity, 'source_hash': digest_json_stream(identity)}


def _receipt_identity(receipt):
    body = {key: value for key, value in receipt.items() if key != 'receipt_id'}
    if hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() != receipt.get('receipt_id'):
        raise ValueError('ECONOMIC_EXPLANATION_RECEIPT_ID_MISMATCH')


def _official_clocks(receipt):
    if (receipt.get('status') != 'CAPTURED_PDF_UNREVIEWED'
            or receipt.get('http_status', receipt.get('HTTP_status')) != 200):
        raise ValueError('ECONOMIC_EXPLANATION_OFFICIAL_HTTP_CAPTURE_NOT_COMPLETE')
    if 'actual_first_seen_at' in receipt:
        values = (receipt['actual_started_at'], receipt['actual_first_seen_at'],
                  receipt['actual_ingested_at'], receipt['actual_completed_at'])
        claimed, url = receipt['original']['sha256'], receipt['source_url']
    else:
        values = tuple(receipt[key] for key in CAPTURE_CLOCKS)
        claimed = receipt.get('original_sha256', receipt.get('body_sha256'))
        url = receipt.get('url', receipt.get('URL'))
    clocks = tuple(_aware(value) for value in values)
    if tuple(sorted(clocks)) != clocks:
        raise ValueError('ECONOMIC_EXPLANATION_OFFICIAL_CAPTURE_CLOCK_ORDER')
    parsed = urlparse(url or '')
    if parsed.scheme != 'https' or parsed.hostname not in ('static.cninfo.com.cn', 'www.sse.com.cn', 'www.szse.cn'):
        raise ValueError('ECONOMIC_EXPLANATION_OFFICIAL_DOCUMENT_URL_UNREGISTERED')
    return claimed, url, dict(zip(CAPTURE_CLOCKS, (value.isoformat() for value in clocks)))


def _business(registration, cutoff):
    if (not isinstance(registration, OfficialBusinessRegistration)
            or registration.direction not in DIRECTIONS or not registration.relation
            or not registration.summary or not registration.boundary
            or re.fullmatch(r'[0-9]{6}\.(SH|SZ)', registration.instrument_id) is None
            or not registration.name or type(registration.business_page) is not int
            or registration.business_page < 1 or not registration.literals):
        raise ValueError('ECONOMIC_EXPLANATION_BUSINESS_REGISTRATION_INVALID')
    original = _read(registration.original)
    if not original.startswith(b'%PDF-'):
        raise ValueError('ECONOMIC_EXPLANATION_ORIGINAL_NOT_PDF')
    receipt = _json(_read(registration.receipt))
    claimed, url, clocks = _official_clocks(receipt)
    if claimed != registration.original.sha256 or max(map(_aware, clocks.values())) > cutoff:
        raise ValueError('ECONOMIC_EXPLANATION_OFFICIAL_SHA_OR_VISIBILITY_MISMATCH')
    text = _read(registration.derived_text).decode('utf-8')
    transform = _json(_read(registration.transform))
    if (transform.get('exit_code') != 0 or transform.get('original_sha256') != claimed
            or transform.get('derived_sha256') != registration.derived_text.sha256):
        raise ValueError('ECONOMIC_EXPLANATION_DERIVED_TEXT_PARENT_MISMATCH')
    pages = text.split('\f')
    if registration.business_page > len(pages):
        raise ValueError('ECONOMIC_EXPLANATION_BUSINESS_PAGE_MISSING')
    section = ''.join(pages[registration.business_page - 1].split())
    if (registration.instrument_id[:6] not in text or registration.name not in text
            or any(literal not in section for literal in registration.literals)):
        raise ValueError('ECONOMIC_EXPLANATION_IDENTITY_OR_BUSINESS_LITERAL_MISMATCH')
    return dict(instrument_id=registration.instrument_id, name=registration.name,
        direction=registration.direction, relation=registration.relation,
        business_summary=registration.summary, boundary=registration.boundary,
        covered_period=registration.covered_period, publication_date_locator=registration.publication_date_locator,
        exact_original_publication_time='UNKNOWN', official_url=url,
        original_pdf_sha256=claimed, original_pdf_path=str(registration.original.path),
        business_page_1indexed=registration.business_page, literals_checked=list(registration.literals),
        actual_capture_clocks=clocks, current_money_attribution='UNKNOWN',
        economic_direction_complete_membership='UNKNOWN',
        relation_role='FINITE_REPORTED_BUSINESS_RELATION_NOT_SAME_DAY_FUND_ATTRIBUTION')


def _coded_rows(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError('ECONOMIC_EXPLANATION_COUNTED_NODE_EMPTY_OR_INVALID')
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('ECONOMIC_EXPLANATION_PROVIDER_CODE_NAME_INVALID')
        code, symbol, name = (row.get(key) for key in ('code', 'symbol', 'name'))
        if (not isinstance(code, str) or re.fullmatch(r'[0-9]{6}', code) is None
                or not isinstance(symbol, str) or re.fullmatch(r'(sh|sz|bj)[0-9]{6}', symbol) is None
                or symbol[2:] != code or not isinstance(name, str) or not name):
            raise ValueError('ECONOMIC_EXPLANATION_PROVIDER_CODE_NAME_INVALID')
        expected = 'sh' if code.startswith(('60', '68', '900')) else 'sz' if code.startswith(('00', '30', '200')) else None
        if expected and symbol[:2] != expected:
            raise ValueError('ECONOMIC_EXPLANATION_PROVIDER_EXCHANGE_CONFLICT')
        kind = ('A_PRICE_NAMESPACE_ONLY_NOT_QUALIFICATION' if code.startswith(('60', '68', '00', '30'))
                else 'B_CODED_NAMESPACE_RETAINED' if code.startswith(('900', '200'))
                else 'BJ_CODED_NAMESPACE_RETAINED' if symbol.startswith('bj') else 'OTHER_NAMESPACE_UNKNOWN_RETAINED')
        out.append(dict(code=code, symbol=symbol, name=name,
                        instrument_id=code + '.' + symbol[:2].upper(), namespace_role=kind))
    if len({row['code'] for row in out}) != len(out) or len({row['symbol'] for row in out}) != len(out):
        raise ValueError('ECONOMIC_EXPLANATION_DUPLICATE_PROVIDER_MEMBER')
    return out


def _node(registration, cutoff):
    from .historical_replay import verify_capture
    from .historical_replay_turnover import _verify_clock_contract
    if (not isinstance(registration, CountedNodeRegistration)
            or registration.endpoint not in ('concept_members_sina', 'industry_members_sina')
            or re.fullmatch(r'(gn_|hangye_)[A-Za-z0-9_]+', registration.node) is None):
        raise ValueError('ECONOMIC_EXPLANATION_COUNTED_NODE_REGISTRATION_INVALID')
    result = _json(_read(registration.result))
    fixture = result.get('provider') == 'TEST_FIXTURE' and result.get('field_version') == 'TEST_FIXTURE_COUNTED_NODE_V2'
    registered_version = (result.get('provider') == 'akshare' and result.get('field_version') in
        ('AKSHARE_1.18.94_R03_V1', 'AKSHARE_1.18.94_SINA_INDUSTRY_ORIGINAL_COUNT_PAGES_V1'))
    if (result.get('status') != 'COMPLETE' or result.get('pagination_complete') is not True
            or not (fixture or registered_version) or result.get('endpoint') != registration.endpoint
            or result.get('parameters') != {'symbol': registration.node}):
        raise ValueError('ECONOMIC_EXPLANATION_COUNTED_NODE_CAPTURE_SCOPE_INVALID')
    integrity = verify_capture(registration.capture_root, result)
    pairs = {}
    for rfile, rawfile in registration.originals:
        receipt = _json(_read(rfile)); _receipt_identity(receipt)
        if receipt['receipt_id'] in pairs or receipt.get('content_hash') != rawfile.sha256:
            raise ValueError('ECONOMIC_EXPLANATION_NODE_ORIGINAL_REGISTRY_INVALID')
        pairs[receipt['receipt_id']] = (receipt, _json(_read(rawfile)))
    embedded = result.get('receipts')
    if (not isinstance(embedded, list) or len({r['receipt_id'] for r in embedded}) != len(embedded)
            or set(pairs) != {r['receipt_id'] for r in embedded}
            or any(pairs[r['receipt_id']][0] != r for r in embedded)):
        raise ValueError('ECONOMIC_EXPLANATION_WHOLE_NODE_ORIGINAL_SCOPE_MISMATCH')
    main = pairs.get(result['request_receipt_id'])
    if main is None:
        raise ValueError('ECONOMIC_EXPLANATION_NODE_MAIN_RAW_MISSING')
    if main[0].get('field_version') != result['field_version'] or main[1].get('field_version') != result['field_version']:
        raise ValueError('ECONOMIC_EXPLANATION_NODE_PARENT_FIELD_VERSION_MISMATCH')
    clocks = _verify_clock_contract(result, main[1])
    start, finish = _aware(result['started_at']), _aware(result['completed_at'])
    if finish > cutoff:
        raise ValueError('ECONOMIC_EXPLANATION_NODE_AFTER_CUTOFF')
    counts, pages = [], {}
    for rid, (receipt, raw) in pairs.items():
        if rid == result['request_receipt_id']:
            continue
        if (receipt.get('status') != 'COMPLETE' or receipt.get('dataset') != registration.endpoint
                or receipt.get('provider') != result['provider']
                or not start <= _aware(receipt['first_seen_at']) <= _aware(receipt['ingested_at']) <= finish):
            raise ValueError('ECONOMIC_EXPLANATION_NODE_UPSTREAM_CLOCK_OR_SCOPE_INVALID')
        url, params = receipt.get('source_url'), receipt.get('parameters')
        if url == 'http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount':
            if (params != {'node': registration.node} or not isinstance(raw, str)
                    or len(raw) > 16 or re.fullmatch(r'[1-9][0-9]*', raw) is None):
                raise ValueError('ECONOMIC_EXPLANATION_NODE_COUNT_SOURCE_INVALID')
            counts.append(int(raw))
        elif url == 'http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData':
            if not isinstance(params, dict) or not isinstance(params.get('page'), str) or re.fullmatch(r'[1-9][0-9]*', params['page']) is None:
                raise ValueError('ECONOMIC_EXPLANATION_NODE_PAGE_INVALID')
            number = int(params['page'])
            expected = dict(_s_r_a='page', asc='1', node=registration.node, num='80', page=str(number), sort='symbol', symbol='')
            if params != expected or number in pages or not isinstance(raw, list) or receipt.get('row_count') != len(raw):
                raise ValueError('ECONOMIC_EXPLANATION_NODE_PAGE_INVALID')
            pages[number] = raw
        else:
            raise ValueError('ECONOMIC_EXPLANATION_EXTRA_UPSTREAM_SOURCE_UNREGISTERED')
    if (len(counts) != 1 or not isinstance(result.get('rows'), list)
            or counts[0] != len(result['rows'])):
        raise ValueError('ECONOMIC_EXPLANATION_NODE_COUNT_NORMALIZED_LENGTH_MISMATCH')
    if set(pages) != set(range(1, (counts[0] + 79) // 80 + 1)):
        raise ValueError('ECONOMIC_EXPLANATION_ALL_NODE_PAGES_REQUIRED')
    total = counts[0]; combined = []
    for page in sorted(pages):
        if len(pages[page]) != min(80, total - (page-1)*80):
            raise ValueError('ECONOMIC_EXPLANATION_NODE_PAGE_ROW_COUNT_MISMATCH')
        combined.extend(pages[page])
    ids = _coded_rows(result.get('rows'))
    if len(ids) != total or _coded_rows(combined) != ids:
        raise ValueError('ECONOMIC_EXPLANATION_NORMALIZED_IDENTITIES_DIFFER_FROM_WHOLE_UPSTREAM')
    directory = _json(_read(registration.directory_raw))
    dreceipt = _json(_read(registration.directory_receipt)); _receipt_identity(dreceipt)
    dataset = 'concept_list_sina' if registration.endpoint == 'concept_members_sina' else 'industry_list_sina'
    if (dreceipt.get('dataset') != dataset or dreceipt.get('parameters') != {}
            or dreceipt.get('content_hash') != registration.directory_raw.sha256
            or dreceipt.get('status') != 'COMPLETE' or directory.get('status') != 'COMPLETE'
            or dreceipt.get('field_version') != directory.get('field_version')
            or directory.get('pagination_complete') is not True
            or _aware(directory['first_seen_at']) != _aware(dreceipt['first_seen_at'])
            or _aware(dreceipt['first_seen_at']) > _aware(dreceipt['ingested_at'])
            or _aware(dreceipt['ingested_at']) > cutoff):
        raise ValueError('ECONOMIC_EXPLANATION_DIRECTORY_SOURCE_INVALID')
    rows = [r for r in directory['rows'] if r.get('label') == registration.node]
    if len(rows) != 1 or rows[0].get('板块') != registration.literal_name:
        raise ValueError('ECONOMIC_EXPLANATION_DIRECTORY_NODE_NAME_MISMATCH')
    value = rows[0].get('公司家数')
    if type(value) is not int or value < 0:
        raise ValueError('ECONOMIC_EXPLANATION_DIRECTORY_COUNT_INVALID')
    if verify_capture(registration.capture_root, result) != integrity:
        raise ValueError('ECONOMIC_EXPLANATION_NODE_TERMINAL_CAPTURE_CHANGED')
    return dict(contract=NODE_CONTRACT, node=registration.node, literal_name=registration.literal_name,
        original_field_version=result['field_version'], original_coverage_preserved=deepcopy(result['coverage']),
        original_receipt_ids=sorted(pairs), original_result_sha256=registration.result.sha256,
        all_member_identities=ids, whole_member_count=total,
        pages=[dict(page=n, rows=len(pages[n])) for n in sorted(pages)],
        directory_company_count=value, directory_vs_technical_count='MATCH' if value == total else 'CONFLICT',
        directory_economic_definition='UNKNOWN', technical_node_pages_complete=True,
        actual_capture_clocks=clocks, current_membership_not_historical=True,
        economic_direction_complete_membership='UNKNOWN', source_capability_issued=False,
        undated_quote_fields_unused=True, non_A_rows_not_removed=True)


def _fraction(value):
    if not isinstance(value, dict) or set(value) != {'numerator', 'denominator'}:
        return None
    n, d = value['numerator'], value['denominator']
    if type(n) is not int or type(d) is not int or d <= 0:
        return None
    return Fraction(n, d)


def _encoded(value):
    return None if value is None else dict(numerator=value.numerator, denominator=value.denominator)


def _finite_subset(ids, items):
    amounts = [_fraction(items[i].get('exact_features', {}).get('amount')) for i in ids]
    returns = [_fraction(items[i].get('exact_features', {}).get('returns', {}).get('1')) for i in ids]
    amount_known = bool(ids) and all(x is not None and x >= 0 for x in amounts)
    breadth_known = bool(ids) and all(x is not None for x in returns)
    return dict(scope_role='THIS_FROZEN_SELECTED_BUSINESS_RELATION_SUBSET_ONLY_NOT_WHOLE_DIRECTION',
        instrument_ids=list(ids), N=len(ids), amount_known_count=sum(x is not None and x >= 0 for x in amounts),
        return_known_count=sum(x is not None for x in returns),
        amount_status='AVAILABLE' if amount_known else 'UNKNOWN',
        amount_cny=_encoded(sum(amounts, Fraction())) if amount_known else None,
        breadth_status='AVAILABLE' if breadth_known else 'UNKNOWN',
        rising_fraction=_encoded(Fraction(sum(x > 0 for x in returns), len(ids))) if breadth_known else None,
        rising_count=sum(x > 0 for x in returns) if breadth_known else None,
        falling_count=sum(x < 0 for x in returns) if breadth_known else None,
        unchanged_count=sum(x == 0 for x in returns) if breadth_known else None,
        broad_direction_amount_cny=None, broad_direction_breadth=None, broad_direction_strength='UNKNOWN',
        missing_values_not_zero_filled=True, values_role='SAVED_R2_EXACT_LITERALS_NOT_RAW_REISSUED_CAPABILITY')


def _reported_scope(file, registry, cutoff, observation_date):
    """Reuse a pinned prior body review, while rereading every original file.

    This is archived review reuse, not a new parser or source issuer. Topics do
    not create members, and publisher/origin families do not prove independence.
    """
    audit = _json(_read(file))
    if (audit.get('actual_exit_code') != 0 or audit.get('source_terminal_equal') is not True
            or _aware(audit['created_at']) > cutoff
            or audit.get('historical_Oct9_PIT_attachment_allowed') is not False):
        raise ValueError('ECONOMIC_EXPLANATION_PRIOR_TEXT_REVIEW_STATE_INVALID')
    for source in audit.get('source_registry', []):
        key = str(Path(source['path']).resolve())
        if key not in registry or registry[key].sha256 != source['sha256']:
            raise ValueError('ECONOMIC_EXPLANATION_PRIOR_TEXT_WHOLE_SOURCE_REGISTRY_REQUIRED')
    reports = []
    for row in audit.get('sources', []):
        for key in ('raw', 'receipt'):
            actual = registry.get(str(Path(row[key]['path']).resolve()))
            if actual is None or actual.sha256 != row[key]['sha256']:
                raise ValueError('ECONOMIC_EXPLANATION_PRIOR_TEXT_ORIGINAL_BINDING_INVALID')
        receipt = _json(_read(registry[str(Path(row['receipt']['path']).resolve())]))
        seen = _aware(receipt['received_at'])
        if (receipt.get('status') != 'COMPLETE_HTTP_ORIGINAL'
                or receipt.get('url') != row['url'] or receipt.get('source_id') != row['source_id']
                or receipt.get('raw', {}).get('sha256') != row['raw']['sha256']
                or seen != _aware(row['actual_received_at']) or seen > cutoff
                or _aware(receipt['request_started_at']) > seen):
            raise ValueError('ECONOMIC_EXPLANATION_PRIOR_TEXT_CAPTURE_MISMATCH')
        # Publication day is explicitly supplied by the already audited body;
        # do not infer a stock/event window from headline or date-free keyword.
        lower, upper = _aware(row['publication_lower']), _aware(row['publication_upper'])
        if lower > upper or lower.date().isoformat() != observation_date or upper > cutoff:
            raise ValueError('ECONOMIC_EXPLANATION_PRIOR_TEXT_BUSINESS_DATE_MISMATCH')
        reports.append(dict(source_id=row['source_id'], title=row['title'], url=row['url'],
            publisher_family=row['publisher_family'], origin_family=row['origin_family'],
            publication_lower=lower.isoformat(), publication_upper=upper.isoformat(),
            actual_received_at=seen.isoformat(), raw_sha256=row['raw']['sha256'],
            body_sha256=row['body_sha256'], reported_topics=deepcopy(row['reported_topics']),
            body_semantics_role='SAVED_PRIOR_ORIGINAL_TEXT_REVIEW_REUSED_NOT_REPARSED',
            independent_data_family_verified='UNKNOWN', topic_membership_authority=False))
    if not reports:
        raise ValueError('ECONOMIC_EXPLANATION_PRIOR_TEXT_REVIEW_HAS_NO_ORIGINALS')
    return dict(audit_sha256=file.sha256, reports=reports, current_reported_direction_scope='AVAILABLE_WITH_STATED_SOURCE_LIMITS',
                native_activity_confirmation='UNKNOWN', stock_named_relationships_not_inferred=True)


def build_economic_direction_explanation(*, frozen_grouped, business_sources,
        counted_nodes, cutoff, extra_registered_files=(), reported_scope=None):
    """Read pinned originals and project unchanged selected rows into explanations.

    This API authenticates local archive bytes/relationships only. In particular
    it does not reissue the saved native theme parent or authenticate a selector.
    """
    cutoff = _aware(cutoff); identity = source_identity()
    business_sources, counted_nodes = tuple(business_sources), tuple(counted_nodes)
    files = [frozen_grouped, *extra_registered_files]
    if reported_scope is not None: files.append(reported_scope)
    for row in business_sources:
        if not isinstance(row, OfficialBusinessRegistration):
            raise ValueError('ECONOMIC_EXPLANATION_BUSINESS_REGISTRATION_REQUIRED')
        files.extend((row.original, row.receipt, row.derived_text, row.transform))
    for row in counted_nodes:
        if not isinstance(row, CountedNodeRegistration):
            raise ValueError('ECONOMIC_EXPLANATION_COUNTED_NODE_REGISTRATION_REQUIRED')
        files.extend((row.result, row.directory_receipt, row.directory_raw))
        for pair in row.originals: files.extend(pair)
    registry = {}
    for file in files:
        key = str(Path(file.path).resolve())
        if key in registry and registry[key] != file:
            raise ValueError('ECONOMIC_EXPLANATION_SAME_PATH_SOURCE_REGISTRATION_CONFLICT')
        registry[key] = file
    before = {key: _fp(file) for key, file in registry.items()}
    parent = _json(_read(frozen_grouped))
    if (parent.get('stock_selection_changed') is not False
            or parent.get('score_or_FOCUS_changed') is not False
            or parent.get('primary_group_union_exact') is not True
            or _aware(parent['new_context_cutoff']) > cutoff or _aware(parent['created_at']) > cutoff):
        raise ValueError('ECONOMIC_EXPLANATION_FROZEN_GROUP_BINDING_INVALID')
    binding = parent.get('frozen_run_binding')
    if (not isinstance(binding, dict) or not binding.get('run_id') or not binding.get('input_hash')
            or binding.get('observation_date') != parent.get('observation_date')
            or _aware(binding['original_decision_cutoff']) > cutoff):
        raise ValueError('ECONOMIC_EXPLANATION_FROZEN_RUN_BINDING_INVALID')
    ids = parent.get('frozen_selected_ids')
    groups = parent.get('groups'); items, original_group_by_id = {}, {}
    if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids) or not isinstance(groups, list):
        raise ValueError('ECONOMIC_EXPLANATION_FROZEN_SELECTION_SCOPE_INVALID')
    for group in groups:
        rows = group.get('items'); gids = group.get('primary_selected_ids')
        if not isinstance(rows, list) or gids != [r.get('instrument_id') for r in rows]:
            raise ValueError('ECONOMIC_EXPLANATION_FROZEN_GROUP_ITEMS_INVALID')
        for row in rows:
            iid = row['instrument_id']
            if iid in items or iid not in ids:
                raise ValueError('ECONOMIC_EXPLANATION_DUPLICATE_OR_ADDED_SELECTED_STOCK')
            items[iid] = deepcopy(row); original_group_by_id[iid] = group['group_id']
    if set(items) != set(ids) or digest_json_stream([items[i] for i in ids]) != parent.get('selected_original_items_hash'):
        raise ValueError('ECONOMIC_EXPLANATION_SELECTED_ITEMS_OR_HASH_MISMATCH')
    businesses = [_business(row, cutoff) for row in business_sources]
    if len({r['instrument_id'] for r in businesses}) != len(businesses):
        raise ValueError('ECONOMIC_EXPLANATION_DUPLICATE_BUSINESS_RELATION')
    for row in businesses:
        if row['instrument_id'] not in items or items[row['instrument_id']].get('name') != row['name']:
            raise ValueError('ECONOMIC_EXPLANATION_BUSINESS_ADDS_OR_RENAMES_STOCK')
    nodes = [_node(row, cutoff) for row in counted_nodes]
    if len({r['node'] for r in nodes}) != len(nodes):
        raise ValueError('ECONOMIC_EXPLANATION_DUPLICATE_COUNTED_NODE')
    for node in nodes:
        node['same_run_matches'] = [row for row in node['all_member_identities'] if row['instrument_id'] in items]
        if any(items[row['instrument_id']].get('name') != row['name'] for row in node['same_run_matches']):
            raise ValueError('ECONOMIC_EXPLANATION_PROVIDER_AND_SELECTED_NAME_CONFLICT')
    by_business = {r['instrument_id']: r for r in businesses}
    reports = (_reported_scope(reported_scope, registry, cutoff, parent['observation_date'])
               if reported_scope is not None else None)
    directions, primary = [], []
    for direction in DIRECTIONS:
        selected = [iid for iid in ids if by_business.get(iid, {}).get('direction') == direction]
        report_rows = [row for row in reports['reports'] if any(topic.get('direction') == direction
                       for topic in row['reported_topics'])] if reports else []
        directions.append(dict(direction=direction,
            name_zh='电池：已证业务关系观察子集' if direction == 'BATTERY' else '消费：零售经营及资产关系观察子集',
            selected_ids=selected, business_relations=[by_business[i] for i in selected],
            selected_subset_metrics=_finite_subset(selected, items),
            activity_confirmation='UNKNOWN_FOR_BROAD_ECONOMIC_DIRECTION',
            strength_status='UNKNOWN_NOT_A_NEW_NATIVE_THEME_SCORE',
            reported_scope_status='REPORTED_OBSERVATION_DAY_TOPIC_WITHOUT_MEMBER_OR_MONEY_AUTHORITY' if report_rows else 'UNKNOWN',
            reported_direction_sources=report_rows))
        if selected:
            primary.append(dict(group_id='ECONOMIC_BUSINESS:'+direction, group_type='BUSINESS_RELATION_ONLY_NOT_MAINLINE',
                name=directions[-1]['name_zh'], primary_selected_ids=selected, original_items=[items[i] for i in selected],
                economic_strength='UNKNOWN', original_model_and_group_preserved=True))
    assigned = set(by_business)
    for group in groups:
        selected = [iid for iid in group['primary_selected_ids'] if iid not in assigned]
        if selected:
            primary.append(dict(group_id=group['group_id'], group_type=group['group_type'], name=group['name'],
                stage=group.get('stage'), primary_selected_ids=selected, original_items=[items[i] for i in selected],
                original_V4_observation=deepcopy(group.get('original_V4_observation')),
                economic_business_relation='UNKNOWN_PROVIDER_LABEL_OR_MODEL_ONLY'))
    union = [iid for row in primary for iid in row['primary_selected_ids']]
    if len(union) != len(set(union)) or set(union) != set(ids):
        raise ValueError('ECONOMIC_EXPLANATION_PRIMARY_UNION_MISMATCH')
    v4 = parent.get('V4') or {}
    native_layers = {key: deepcopy(v4.get(key, [])) for key in CATEGORIES}
    after = {key: _fp(file) for key, file in registry.items()}
    if before != after or source_identity() != identity:
        raise ValueError('ECONOMIC_EXPLANATION_SOURCES_CHANGED_DURING_PROJECTION')
    created = datetime.now(timezone.utc)
    out = dict(schema_version=VERSION, feature_version=FEATURE_VERSION, source_identity=identity,
        created_at=created.isoformat(), new_evidence_cutoff=cutoff.isoformat(),
        attachment_visible_at=max(created, cutoff).isoformat(), observation_date=parent['observation_date'],
        target_session=parent.get('target_session'), frozen_run_binding=deepcopy(binding),
        original_selection_cutoff=binding['original_decision_cutoff'], original_native_context_cutoff=parent['new_context_cutoff'],
        original_grouped_file_sha256=frozen_grouped.sha256,
        selected_original_items_hash=parent['selected_original_items_hash'], frozen_selected_ids=deepcopy(ids),
        frozen_original_groups=deepcopy(groups), original_group_by_selected_id=original_group_by_id,
        economic_directions=directions, primary_display_groups=primary, primary_group_union_exact=True,
        preserved_native_subtheme_layers=native_layers,
        native_strength_order='UNCHANGED_SAVED_V4_WITHIN_EACH_CONFIRMATION_ROLE',
        economic_direction_strength_order='UNKNOWN_NO_NEW_WEIGHT_OR_BROAD_THEME_RANK',
        counted_provider_nodes=nodes, registered_original_files=before, all_original_terminal_equal=True,
        reported_observation_day_scope=reports,
        registered_business_interpretations_not_provider_membership_authority=True,
        saved_native_parent_reauthenticated=False, selector_reauthenticated=False,
        source_capability_issued=False, saved_JSON_grants_source_capability=False,
        stock_selection_changed=False, score_or_FOCUS_changed=False,
        complete_economic_mainline_coverage='UNKNOWN', full_selected_pool_is_battery=False,
        actual_optional_LHB_limit_and_funds_scope=deepcopy(parent.get('optional_limits_LHB_funds')),
        historical_membership='UNKNOWN_CURRENT_COHORT_OR_REPORT_PERIOD_ONLY', PIT=False,
        complete_cycles=0, market_edge='NOT_ESTABLISHED',
        limitations=['Official business relation is not observation-day capital attribution',
            'Native child-theme amounts are not added into a broad economic direction total',
            'Provider whole-node count/pages do not certify economic classification or historical membership',
            'Finite selected-subset amount/breadth are not whole-sector liquidity or strength',
            'Missing business relation is UNKNOWN, never evidence of no relationship'])
    out['attachment_content_hash'] = digest_json_stream(out)
    return out


def _display(value, scale=1, suffix=''):
    value = _fraction(value)
    if value is None: return '未知'
    with localcontext() as ctx:
        ctx.prec = max(32, len(str(abs(value.numerator))) + len(str(value.denominator)) + 8)
        decimal = Decimal(value.numerator) / Decimal(value.denominator) * Decimal(scale)
        return format(decimal.quantize(Decimal('0.01')), 'f') + suffix


def render_economic_direction_explanation(attachment, *, as_of):
    """Saved display only; no numeric contents before actual attachment visibility."""
    expected = attachment.get('attachment_content_hash')
    if digest_json_stream({k: v for k, v in attachment.items() if k != 'attachment_content_hash'}) != expected:
        raise ValueError('ECONOMIC_EXPLANATION_SAVED_ATTACHMENT_HASH_MISMATCH')
    visible = max(_aware(attachment[key]) for key in ('created_at', 'new_evidence_cutoff', 'attachment_visible_at'))
    if visible > _aware(as_of):
        return 'NOT_YET_VISIBLE：经济方向解释附件在此显示截止尚未取得或生成。'
    lines = ['经济方向解释附件（原观察名单保持不变）',
        f"观察日 {attachment['observation_date']}；目标 {attachment.get('target_session')}；run {attachment['frozen_run_binding']['run_id']}",
        f"原选股截止 {attachment['original_selection_cutoff']}；新证据截止 {attachment['new_evidence_cutoff']}；实际生成 {attachment['created_at']}",
        '经济方向整体强度未知；原生子题材状态与业务关系分列，全部个股仍是原run观察项。', '']
    roles = {'confirmed_mainlines': '已确认活跃子题材', 'fermentation_watch': '发酵观察，尚未确认',
             'active_divergence_mainlines': '活跃子题材，出现分歧'}
    for category in CATEGORIES:
        for row in attachment['preserved_native_subtheme_layers'][category]:
            lines.append(f"{roles[category]}：{row.get('name')}｜原阶段 {row.get('stage')}｜覆盖为供应商子范围，金额不得跨子题材相加。")
    lines.append('')
    for direction in attachment['economic_directions']:
        metric = direction['selected_subset_metrics']
        lines.append(direction['name_zh'])
        lines.append('仅本组已证业务关系观察股：成交额 '+_display(metric['amount_cny'], Decimal('0.00000001'), '亿元')
            +'；上涨广度 '+_display(metric['rising_fraction'], 100, '%')+'。整体方向金额/广度/排名未知。')
        if direction['reported_direction_sources']:
            lines.append('当日报道涉及此方向：'+'；'.join(row['title'] for row in direction['reported_direction_sources'])
                +'。报道范围不授予成员、主线强度或资金评分。')
        for row in direction['business_relations']:
            original = next(item for group in attachment['frozen_original_groups'] for item in group['items']
                            if item['instrument_id'] == row['instrument_id'])
            lines.append(f"{row['instrument_id']} {row['name']}｜{row['business_summary']}")
            lines.append(f"  原模型 {original.get('primary_stage', 'UNKNOWN')}；原决策 {original.get('decision_status', 'UNKNOWN')}；"
                +'个股成交额 '+_display(original.get('exact_features', {}).get('amount'), Decimal('0.00000001'), '亿元')+'。')
            lines.append(f"  材料 {row['covered_period']}，披露日期定位 {row['publication_date_locator']}，PDF第{row['business_page_1indexed']}页；{row['boundary']}")
        lines.append('')
    lines.append('其余原生子题材及模型观察项（业务关系未知仍完整保留）')
    for group in attachment['primary_display_groups']:
        if group['group_type'] == 'BUSINESS_RELATION_ONLY_NOT_MAINLINE': continue
        lines.append(group['name']+'：'+'、'.join(row.get('name', row['instrument_id'])+'('+row['instrument_id']+')' for row in group['original_items']))
    lines += ['', f"完整观察项 {len(attachment['frozen_selected_ids'])}，主显示组并集精确；未新增选股、评分或FOCUS。",
        '当前成员及报告期业务回看不认证过去主线、旧盘前可见或完整情绪周期。仅供应商标签且缺正式业务原件的关系保持未知。']
    return '\n'.join(lines)+'\n'
