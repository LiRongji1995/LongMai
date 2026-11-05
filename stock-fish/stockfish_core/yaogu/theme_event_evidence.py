"""Authenticated, typed reported-event attachments; never a selection producer.

The caller supplies real native parent/selection capabilities. Bound methods
are an interface restriction, not a defence against malicious Python callers.
Publication intervals never replace actual capture clocks or historical PIT.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator

from .contracts import Frozen, SourceReceipt
from .history_artifacts import digest_json_stream, digest_market_input
from .research_context_artifacts import SourceArtifactReader
from .visibility import SH, stamp

VERSION = 'EVENT_REPORTED_CONTEXT_V1'
FEATURE_VERSION = 'AUTHENTICATED_REPORTED_THEME_EVENTS_V1'
MAX_ARTICLE_BYTES = 2 * 1024 * 1024


class ArticleArtifact(Frozen):
    receipt_id: str = Field(min_length=1)
    parser: str = Field(min_length=1)
    request_started_at: datetime
    actual_received_at: datetime
    ingested_at: datetime

    @model_validator(mode='after')
    def ordered_capture(self):
        if not self.request_started_at <= self.actual_received_at <= self.ingested_at:
            raise ValueError('EVENT_ACTUAL_CAPTURE_CLOCK_ORDER_INVALID')
        return self


class ReportedSecurity(Frozen):
    reported_name: str = Field(min_length=1, max_length=100)
    instrument_id: str | None = None


class EventClaim(Frozen):
    claim_id: str = Field(min_length=1)
    receipt_id: str = Field(min_length=1)
    kind: Literal['CATALYST_REPORTED', 'LADDER_REPORTED', 'DIVERGENCE_REPORTED']
    theme_ids: tuple[str, ...] = ()
    event_date: date
    snapshot: Literal['ANNOUNCEMENT', 'INTRADAY', 'CLOSE', 'MULTI_SESSION', 'UNKNOWN'] = 'UNKNOWN'
    evidence_text: str = Field(min_length=1, max_length=512)
    body_occurrence: int = Field(default=0, ge=0)
    metric: Literal['LIMIT_UP_COUNT', 'FAILED_SEAL_COUNT', 'SEALING_RATE_PERCENT',
                    'TOKEN_CALLS', 'PRICE_CHANGE_RANGE_PERCENT',
                    'CONSECUTIVE_BOARD_COUNT', 'WINDOW_BOARD_COUNT'] | None = None
    comparison_key: str | None = None
    declared_population: str | None = None
    upstream_origin: str | None = None
    securities: tuple[ReportedSecurity, ...] = ()

    @field_validator('theme_ids')
    @classmethod
    def unique_themes(cls, value):
        if len(set(value)) != len(value) or any(not item for item in value):
            raise ValueError('EVENT_THEME_SCOPE_INVALID')
        return value


def source_identity():
    root = Path(__file__).resolve().parents[2]
    paths = (Path(__file__),
             Path(__file__).with_name('contracts.py'), Path(__file__).with_name('history_artifacts.py'),
             Path(__file__).with_name('visibility.py'), Path(__file__).with_name('research_context_artifacts.py'))
    hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in paths}
    return {'implementation_version': VERSION, 'feature_version': FEATURE_VERSION,
            'source_file_hashes': hashes, 'source_hash': digest_json_stream(hashes)}


class _HTML(HTMLParser):
    """Fixed text/field extraction; no scripts, assets, or network execution."""
    def __init__(self, target=None):
        super().__init__(convert_charrefs=True)
        self.target, self.active, self.depth = target, target is None, 0
        self.target_count = 0
        self.skip, self.parts, self.next_scripts = 0, [], []
        self.next_data = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('id') == '__NEXT_DATA__':
            self.next_data = []
        if self.target and tag == 'div' and self.target in attrs.get('class', '').split() and not self.active:
            self.target_count += 1
            self.active, self.depth = True, 1
            return
        if self.active:
            if self.target and tag == 'div':
                self.depth += 1
            if tag in ('script', 'style'):
                self.skip += 1

    def handle_endtag(self, tag):
        if tag == 'script' and self.next_data is not None:
            self.next_scripts.append(''.join(self.next_data))
            self.next_data = None
        if self.active:
            if tag in ('script', 'style'):
                self.skip = max(0, self.skip - 1)
            if self.target and tag == 'div':
                self.depth -= 1
                if self.depth == 0:
                    self.active = False

    def handle_data(self, value):
        if self.next_data is not None:
            self.next_data.append(value)
        if self.active and not self.skip and value.strip():
            self.parts.append(value.strip())


def _text(value, target=None):
    parser = _HTML(target)
    parser.feed(value)
    if target is not None and parser.target_count != 1:
        raise ValueError('EVENT_FIXED_PARSER_SINGLE_FIELD_REQUIRED')
    return '\n'.join(parser.parts)


def _next_data(value):
    parser = _HTML()
    parser.feed(value)
    if len(parser.next_scripts) != 1:
        raise ValueError('EVENT_NEXT_DATA_SINGLE_ORIGINAL_REQUIRED')
    return json.loads(parser.next_scripts[0])


def _publication(value, precision=None):
    """Conservative bounds. A day is [day start, next day start), not 00:00 publication."""
    if precision == 'SECOND_EPOCH':
        lower = datetime.fromtimestamp(value, SH)
        return lower, lower, 'SECOND_VENDOR_EPOCH'
    if not isinstance(value, str):
        raise ValueError('EVENT_PUBLICATION_FIELD_INVALID')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        lower = datetime.combine(date.fromisoformat(value), datetime.min.time(), SH)
        return lower, lower + timedelta(days=1), 'DAY_HALF_OPEN_INTERVAL'
    if re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}', value):
        lower = datetime.strptime(value, '%Y-%m-%d %H:%M').replace(tzinfo=SH)
        return lower, lower + timedelta(minutes=1), 'MINUTE_HALF_OPEN_INTERVAL'
    if re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', value):
        lower = datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=SH)
        return lower, lower, 'SECOND_VENDOR_FIELD'
    raise ValueError('EVENT_PUBLICATION_FORMAT_UNSUPPORTED')


def _parse_article(raw, parser_name, receipt, coverage_type):
    value = raw.decode('utf-8', errors='strict')
    host = urlparse(receipt.source_url or '').hostname
    if parser_name == 'TEST_FIXTURE_JSON_V1':
        if coverage_type != 'TEST_FIXTURE':
            raise ValueError('EVENT_FIXTURE_PARSER_FORBIDDEN_FOR_REAL_COVERAGE')
        row = json.loads(value)
        if set(row) != {'title', 'body', 'publisher', 'source', 'author', 'publication'}:
            raise ValueError('EVENT_FIXTURE_JSON_SCHEMA_INVALID')
        if not all(isinstance(v, str) for v in row.values()):
            raise ValueError('EVENT_FIXTURE_JSON_FIELDS_MUST_BE_TEXT')
        publisher = row.pop('publisher')
        if publisher != 'TEST_FIXTURE':
            raise ValueError('EVENT_FIXTURE_JSON_REAL_PUBLISHER_FORBIDDEN')
    elif parser_name == 'CLS_NEXT_DATA_V1':
        if host not in ('cls.cn', 'www.cls.cn'):
            raise ValueError('EVENT_ORIGINAL_PARSER_DOMAIN_MISMATCH')
        data = _next_data(value)['props']['pageProps']['articleDetail']
        if type(data['ctime']) is not int:
            raise ValueError('EVENT_CLS_ORIGINAL_EPOCH_MUST_BE_INTEGER')
        if str(data['id']) != (urlparse(receipt.source_url).path.rstrip('/').split('/')[-1]):
            raise ValueError('EVENT_ORIGINAL_ARTICLE_ID_MISMATCH')
        row = {'title': data['title'], 'body': _text(data['content']), 'source': '财联社',
               'author': data['author']['name'], 'publication': data['ctime']}
        publisher = 'CLS'
    elif parser_name == 'STCN_HTML_V1':
        if host not in ('stcn.com', 'www.stcn.com'):
            raise ValueError('EVENT_ORIGINAL_PARSER_DOMAIN_MISMATCH')
        info = _text(value, 'detail-info')
        clocks = re.findall(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}', info)
        source = re.search(r'来源：([^\n]+)', info)
        author = re.search(r'作者：([^\n]+)', info)
        if len(clocks) != 1 or not source or not author:
            raise ValueError('EVENT_STCN_ORIGINAL_FIELDS_UNAVAILABLE')
        row = {'title': _text(value, 'detail-title'), 'body': _text(value, 'detail-content'),
               'source': source[1], 'author': author[1], 'publication': clocks[0]}
        publisher = 'STCN'
    elif parser_name == 'CNSTOCK_PAPER_V1':
        if host != 'paper.cnstock.com':
            raise ValueError('EVENT_ORIGINAL_PARSER_DOMAIN_MISMATCH')
        body = _text(value, 'content_article')
        info = _text(value, 'time-from')
        clock = re.search(r'\d{4}-\d{2}-\d{2}', info)
        author = re.search(r'记者\s*([^\n]+)', body)
        if not clock or not author or '来源：上海证券报' not in info:
            raise ValueError('EVENT_CNSTOCK_PAPER_FIELDS_UNAVAILABLE')
        title = re.search(r'<title>(.*?)</title>', value, re.S)
        if not title:
            raise ValueError('EVENT_CNSTOCK_PAPER_TITLE_UNAVAILABLE')
        row = {'title': _text(title[1]).split('|')[0], 'body': _text(value, 'content_article'),
               'source': '上海证券报', 'author': author[1], 'publication': clock[0]}
        publisher = 'CNSTOCK'
    elif parser_name == 'CNSTOCK_NEXT_DATA_V1':
        if host not in ('news.cnstock.com', 'www.cnstock.com', 'cnstock.com'):
            raise ValueError('EVENT_ORIGINAL_PARSER_DOMAIN_MISMATCH')
        data = _next_data(value)['props']['pageProps']['data']
        path = urlparse(receipt.source_url).path
        article_id = (re.fullmatch(r'/commonDetail/(\d+)/?', path)
                      or re.fullmatch(r'/news,bwkx-\d{6}-(\d+)\.htm', path))
        if (article_id is None or str(data['contId']) != article_id[1]
                or type(data['original']) is not int or data['original'] not in (0, 1)):
            raise ValueError('EVENT_CNSTOCK_ORIGINAL_ID_OR_FLAG_INVALID')
        row = {'title': data['title'], 'body': _text(data['textInfo']['content']),
               'source': data['source'], 'author': data['author'], 'publication': data['pubTime']}
        publisher = 'CNSTOCK'
    elif parser_name == 'GOV_PRESS_RELEASE_V1':
        if host not in ('www.gov.cn', 'gov.cn'):
            raise ValueError('EVENT_ORIGINAL_PARSER_DOMAIN_MISMATCH')
        clocks = re.findall(r'<meta\s+name=[\"\']firstpublishedtime[\"\']\s+content=[\"\']([^\"\']+)', value)
        title = re.search(r'<title>(.*?)</title>', value, re.S)
        if len(clocks) != 1 or not title or '国务院新闻办网站' not in _text(value, 'pages-date'):
            raise ValueError('EVENT_OFFICIAL_PRESS_RELEASE_FIELDS_UNAVAILABLE')
        row = {'title': _text(title[1]), 'body': _text(value, 'pages_content'), 'source': '国务院新闻办',
               'author': '国务院新闻办公开发布', 'publication': clocks[0][:10] + ' ' + clocks[0][11:]}
        publisher = 'SCIO'
    else:
        return None
    if not all(isinstance(row[k], str) and row[k].strip() for k in ('title', 'body', 'source')):
        raise ValueError('EVENT_ORIGINAL_REQUIRED_TEXT_FIELDS_UNAVAILABLE')
    lower, upper, precision = _publication(row['publication'], 'SECOND_EPOCH' if publisher == 'CLS' else None)
    source_label = row['source'].strip()
    own = {'CLS': ('财联社',), 'STCN': ('证券时报网', '证券时报'),
           'CNSTOCK': ('上海证券报', '上海证券报·中国证券网'), 'SCIO': ('国务院新闻办',)}
    original = publisher == 'SCIO' or source_label in own.get(publisher, ())
    canonical_origins = {'财联社': 'CLS', '证券时报': 'STCN', '证券时报网': 'STCN',
                         '上海证券报': 'CNSTOCK', '上海证券报·中国证券网': 'CNSTOCK',
                         '国务院新闻办': 'SCIO', '国务院新闻办网站': 'SCIO'}
    if parser_name == 'TEST_FIXTURE_JSON_V1':
        family = 'TEST_FIXTURE_ORIGIN:' + source_label
        original = source_label == row.get('source') and publisher == 'TEST_FIXTURE'
    else:
        family = canonical_origins.get(source_label, 'SOURCE_ORIGIN:' + source_label)
    originality = 'PUBLISHER_ORIGINAL_LABEL_NOT_INDEPENDENT_DATA_FEED' if original else 'REPRINT_ONE_UPSTREAM_FAMILY'
    if parser_name == 'CNSTOCK_NEXT_DATA_V1' and data['original'] == 0 and original:
        originality = 'SOURCE_FAMILY_LABEL_KNOWN_ORIGINALITY_UNCONFIRMED'
    return {**row, 'publication_lower': lower, 'publication_upper': upper, 'publication_precision': precision,
            'publisher_family': publisher, 'origin_family': family,
            'originality': originality}


def _original(artifact, reader, cutoff, coverage_type):
    receipt = next((row for row in reader.receipts if row.receipt_id == artifact.receipt_id), None)
    if receipt is None:
        raise ValueError('EVENT_ORIGINAL_RECEIPT_UNRESOLVED')
    receipt = SourceReceipt.model_validate(receipt)
    if artifact.actual_received_at != receipt.first_seen_at or artifact.ingested_at != receipt.ingested_at:
        raise ValueError('EVENT_ACTUAL_CAPTURE_RECEIPT_CLOCK_MISMATCH')
    path = reader._paths.get(artifact.receipt_id)
    if path is None or path.stat().st_size > MAX_ARTICLE_BYTES:
        raise ValueError('EVENT_ORIGINAL_FILE_SIZE_LIMIT_EXCEEDED')
    raw = reader(artifact.receipt_id)
    if not isinstance(raw, bytes) or len(raw) > MAX_ARTICLE_BYTES:
        raise ValueError('EVENT_ORIGINAL_BYTES_TYPE_OR_SIZE_INVALID')
    if hashlib.sha256(raw).hexdigest() != receipt.content_hash:
        raise ValueError('EVENT_ORIGINAL_BYTES_SHA_MISMATCH')
    reasons = []
    if receipt.status != 'COMPLETE' or not receipt.pagination_complete or receipt.row_count < 1:
        reasons.append('EVENT_ORIGINAL_SOURCE_NOT_COMPLETE')
    article = None if reasons else _parse_article(raw, artifact.parser, receipt, coverage_type)
    # Deliberately no RECONSTRUCTED_HISTORICAL exception or dated-page proof.
    if max(artifact.request_started_at, artifact.actual_received_at, artifact.ingested_at,
           receipt.event_time, receipt.source_published_at, receipt.first_seen_at, receipt.ingested_at) > cutoff:
        reasons.append('EVENT_ACTUAL_CAPTURE_OR_RECEIPT_CLOCK_AFTER_CUTOFF')
    if article is None:
        reasons.append('EVENT_ARTICLE_NOT_PARSED_FROM_INCOMPLETE_SOURCE' if
                       'EVENT_ORIGINAL_SOURCE_NOT_COMPLETE' in reasons else 'EVENT_ARTICLE_PARSER_UNSUPPORTED')
    elif article['publication_upper'] > cutoff:
        reasons.append('EVENT_PUBLICATION_INTERVAL_NOT_FULLY_BEFORE_CUTOFF')
    meta = {'receipt_id': receipt.receipt_id, 'raw_sha256': receipt.content_hash, 'raw_bytes': len(raw),
            'source_url': receipt.source_url, 'parser': artifact.parser,
            'source_receipt_identity_hash': digest_json_stream(receipt.model_dump(mode='json')),
            'actual_request_started_at': artifact.request_started_at.isoformat(),
            'actual_received_at': artifact.actual_received_at.isoformat(), 'ingested_at': artifact.ingested_at.isoformat(),
            'status': 'UNKNOWN' if reasons else 'AVAILABLE_REPORTED_SOURCE', 'reason_codes': reasons,
            'historical_first_seen_proven': False, 'official_pit_validation_countable': False}
    if article:
        meta.update({key: article[key] for key in ('title', 'source', 'author', 'origin_family', 'publisher_family', 'originality', 'publication_precision')})
        meta.update(publication_lower=article['publication_lower'].isoformat(),
                    publication_upper=article['publication_upper'].isoformat(),
                    publication_bounds_role='BOUNDS_NOT_FILLED_PRECISE_PUBLICATION_CLOCK')
    return article, meta


def _number(value):
    number = Fraction(Decimal(value))
    return {'numerator': number.numerator, 'denominator': number.denominator}


def _metric(kind, value):
    patterns = {'LIMIT_UP_COUNT': r'(\d+)\s*(?:股|只(?:个股)?)\s*涨停',
                'FAILED_SEAL_COUNT': r'(\d+)\s*股炸板',
                'SEALING_RATE_PERCENT': r'封板率(?:为)?\s*(\d+(?:\.\d+)?)%',
                'TOKEN_CALLS': r'(\d+(?:\.\d+)?)\s*(万亿|亿)\s*(元)?',
                'PRICE_CHANGE_RANGE_PERCENT': r'(\d+(?:\.\d+)?)%\s*[-—–至]\s*(\d+(?:\.\d+)?)%',
                'CONSECUTIVE_BOARD_COUNT': r'(?:连续第)?(\d+)(?:连板|日涨停)',
                'WINDOW_BOARD_COUNT': r'(\d+)(?:天|日)(\d+)板'}
    if kind is None:
        return None, []
    matches = re.findall(patterns[kind], value)
    if len(matches) != 1:
        return None, ['EVENT_METRIC_SPAN_MISSING_OR_AMBIGUOUS']
    found = matches[0]
    if kind == 'TOKEN_CALLS':
        if 'Token' not in value and '词元' not in value:
            return None, ['EVENT_TOKEN_QUANTITY_SEMANTICS_UNAVAILABLE']
        amount, scale, money = found
        number = Fraction(Decimal(amount)) * (10**12 if scale == '万亿' else 10**8)
        return {'value': {'numerator': number.numerator, 'denominator': number.denominator},
                'unit': 'CNY' if money else 'TOKEN', 'reported_scale': scale,
                'market_numeric_authority': False}, ['EVENT_TOKEN_MONETARY_UNIT_CONFLICT'] if money else []
    if kind == 'PRICE_CHANGE_RANGE_PERCENT':
        return {'lower': _number(found[0]), 'upper': _number(found[1]), 'unit': 'PERCENT', 'market_numeric_authority': False}, []
    if kind == 'WINDOW_BOARD_COUNT':
        return {'window_sessions_reported': int(found[0]), 'board_count_reported': int(found[1]),
                'unit': 'COUNT', 'market_numeric_authority': False}, []
    metric = {'value': _number(found), 'unit': 'PERCENT' if kind == 'SEALING_RATE_PERCENT' else 'COUNT',
              'market_numeric_authority': False}
    if kind == 'SEALING_RATE_PERCENT':
        places = len(found.split('.')[1]) if '.' in found else 0
        precision = Fraction(1, 2 * 10**places)
        metric['reported_rounding_half_unit'] = {'numerator': precision.numerator, 'denominator': precision.denominator}
    return metric, []


def _bound_method(value):
    if not callable(value) or getattr(value, '__self__', None) is None:
        raise ValueError('EVENT_NATIVE_BOUND_PARENT_AND_SELECTION_CAPABILITIES_REQUIRED')


def _scope(bundle, parent, selection, selected, qualified, cutoff, parent_validator, selection_validator):
    _bound_method(parent_validator)
    _bound_method(selection_validator)
    input_hash = digest_market_input(bundle)
    if (parent.get('source_input_hash') != input_hash or parent.get('coverage_type') != bundle.coverage_type
            or stamp(parent.get('decision_cutoff')) != cutoff or str(parent.get('observation_date')) != str(bundle.observation_date)
            or parent.get('formal_focus') is not False or parent.get('official_pit_validation_countable') is not False
            or digest_json_stream({key: value for key, value in parent.items() if key != 'context_content_hash'}) != parent.get('context_content_hash')):
        raise ValueError('EVENT_NATIVE_THEME_PARENT_BINDING_MISMATCH')
    if (len(set(selected)) != len(selected) or len(set(qualified)) != len(qualified)
            or not set(selected) <= set(qualified) <= {row.instrument_id for row in bundle.universe.securities}
            or set(selection.get('eligible_pool_ids', ())) != set(qualified)
            or set(selection.get('research_selected_ids', ())) != set(selected)
            or stamp(selection.get('decision_cutoff')) != cutoff
            or str(selection.get('observation_date')) != str(bundle.observation_date)
            or selection.get('full_eligible_pool_retained') is not True
            or (selection.get('counts') or {}).get('focus') != 0):
        raise ValueError('EVENT_FROZEN_SELECTION_OR_QUALIFICATION_BINDING_MISMATCH')
    if parent_validator(parent, bundle=bundle, cutoff=cutoff) is not None:
        raise ValueError('EVENT_NATIVE_THEME_PARENT_AUTHENTICATION_FAILED')
    if selection_validator(selection, selected_ids=selected, qualified_ids=qualified, bundle=bundle, cutoff=cutoff) is not None:
        raise ValueError('EVENT_NATIVE_SELECTION_AUTHENTICATION_FAILED')
    return input_hash


_CONTEXT_TOKEN = object()


class _EventContext(dict):
    """Producer-issued in-process attachment capability, JSON serializable.

    A reloaded JSON archive needs its caller's verified native archive adapter;
    it cannot acquire this capability by adding a self-computed hash.
    """
    def __init__(self, value, token, revalidate):
        if token is not _CONTEXT_TOKEN:
            raise ValueError('EVENT_PRODUCER_CAPABILITY_REQUIRED')
        super().__init__(value)
        self._sealed_hash = digest_json_stream(value)
        self._revalidate = revalidate

    def validate(self):
        if digest_json_stream(self) != self._sealed_hash:
            raise ValueError('EVENT_PRODUCER_CONTEXT_CHANGED_AFTER_AUTHENTICATION')
        self._revalidate()


def build_theme_event_context(bundle, native_theme_parent, native_selection_parent, *, cutoff,
        selected_ids, qualified_ids, artifact_reader, artifacts, claims,
        _theme_parent_validator, _selection_parent_validator):
    """Review reported claims inside a trusted, frozen native research scope.

    Validators must be real caller-owned native/verified-archive capabilities.
    Neither an authenticated flag nor this module's self-hash certifies parents.
    """
    cutoff = stamp(cutoff)
    selected, qualified = tuple(sorted(selected_ids)), tuple(sorted(qualified_ids))
    input_hash = _scope(bundle, native_theme_parent, native_selection_parent, selected, qualified,
                        cutoff, _theme_parent_validator, _selection_parent_validator)
    if not isinstance(artifact_reader, SourceArtifactReader):
        raise ValueError('EVENT_REAL_ORIGINAL_ARTIFACT_CAPABILITY_REQUIRED')
    artifacts = tuple(ArticleArtifact.model_validate(item) for item in artifacts)
    claims = tuple(EventClaim.model_validate(item) for item in claims)
    if len({a.receipt_id for a in artifacts}) != len(artifacts) or len({c.claim_id for c in claims}) != len(claims):
        raise ValueError('EVENT_DUPLICATE_ARTIFACT_OR_CLAIM_ID')
    originals = {a.receipt_id: _original(a, artifact_reader, cutoff, bundle.coverage_type) for a in artifacts}
    theme_ids = {row['theme_id'] for row in native_theme_parent.get('items', ())}
    identities = {row.instrument_id: row for row in bundle.universe.securities}
    items, common_names, comparisons, arithmetic = [], defaultdict(list), defaultdict(list), defaultdict(dict)
    for claim in claims:
        if claim.receipt_id not in originals:
            raise ValueError('EVENT_CLAIM_ORIGINAL_PARENT_UNRESOLVED')
        article, meta = originals[claim.receipt_id]
        reasons = list(meta['reason_codes'])
        locator, metric, resolved, unresolved = None, None, [], []
        if set(claim.theme_ids) - theme_ids:
            reasons.append('EVENT_THEME_NOT_IN_AUTHENTICATED_NATIVE_PARENT')
        last_event_day = cutoff.astimezone(SH).date() if claim.kind == 'CATALYST_REPORTED' else bundle.observation_date
        if claim.event_date > last_event_day:
            reasons.append('EVENT_BUSINESS_DATE_AFTER_ALLOWED_OBSERVATION_OR_CONTEXT')
        if article is not None and not meta['reason_codes']:
            positions = [m.start() for m in re.finditer(re.escape(claim.evidence_text), article['body'])]
            if claim.body_occurrence >= len(positions):
                raise ValueError('EVENT_CLAIM_SPAN_NOT_IN_AUTHENTICATED_BODY')
            locator = {'field': 'FIXED_PARSER_ARTICLE_BODY', 'start': positions[claim.body_occurrence],
                       'length': len(claim.evidence_text), 'span_sha256': hashlib.sha256(claim.evidence_text.encode()).hexdigest()}
            metric, numeric_reasons = _metric(claim.metric, claim.evidence_text)
            reasons.extend(numeric_reasons)
            publication_date = article['publication_lower'].astimezone(SH).date()
            dated_text = (str(claim.event_date) in claim.evidence_text or
                f'{claim.event_date.year}年{claim.event_date.month}月{claim.event_date.day}日' in claim.evidence_text or
                (claim.event_date.year == publication_date.year and
                 f'{claim.event_date.month}月{claim.event_date.day}日' in claim.evidence_text))
            if claim.event_date != publication_date and not dated_text:
                reasons.append('EVENT_REPORTED_DATE_NOT_ESTABLISHED_BY_SOURCE_SPAN')
            for security in claim.securities:
                if security.reported_name not in claim.evidence_text:
                    raise ValueError('EVENT_SECURITY_NAME_NOT_IN_AUTHENTICATED_SPAN')
                record = identities.get(security.instrument_id)
                known = bool(record and security.instrument_id in selected and record.name == security.reported_name
                             and record.effective_from <= claim.event_date
                             and (record.effective_to is None or claim.event_date < record.effective_to)
                             and claim.event_date == bundle.observation_date)
                if known:
                    resolved.append(security.instrument_id)
                else:
                    unresolved.append({'reported_name': security.reported_name, 'requested_instrument_id': security.instrument_id,
                                       'identity_status': 'UNKNOWN', 'reason': 'DATED_NAME_IDENTITY_OR_FROZEN_SELECTION_UNVERIFIED'})
                common_names[security.reported_name].append((claim.claim_id, claim.theme_ids, security.instrument_id if known else None))
        row = {'claim_id': claim.claim_id, 'receipt_id': claim.receipt_id, 'kind': claim.kind,
               'theme_ids': list(claim.theme_ids), 'event_date': str(claim.event_date), 'snapshot': claim.snapshot,
               'snapshot_role': 'DECLARED_REPORTED_TEXT_SCOPE_NOT_NATIVE_TRADING_PATH',
               'status': 'UNKNOWN' if reasons else 'AUTHENTICATED_REPORTED_CLAIM', 'reason_codes': reasons,
               'locator': locator, 'metric_type': claim.metric, 'reported_metric': metric,
               'declared_population': claim.declared_population, 'population_verified': False,
               'comparison_key': claim.comparison_key, 'upstream_origin_declared': claim.upstream_origin,
               'origin_family': (article or {}).get('origin_family'), 'resolved_selected_ids': sorted(set(resolved)),
               'unresolved_securities': unresolved, 'numeric_market_authority': False,
               'strict_limit_event_verified': False, 'historical_qualification_verified': False,
               'extraction_semantics': 'INITIAL_TYPED_REPORT_REVIEW_NOT_MARKET_EVENT_CERTIFICATE'}
        items.append(row)
        if row['status'] == 'AUTHENTICATED_REPORTED_CLAIM' and metric:
            if claim.comparison_key:
                comparisons[claim.comparison_key].append(row)
            if claim.declared_population and claim.metric in ('LIMIT_UP_COUNT', 'FAILED_SEAL_COUNT', 'SEALING_RATE_PERCENT'):
                key = (claim.receipt_id, str(claim.event_date), claim.snapshot, claim.declared_population)
                if claim.metric in arithmetic[key]:
                    arithmetic[key][claim.metric] = None
                else:
                    arithmetic[key][claim.metric] = metric
    conflicts = []
    for row in items:
        if 'EVENT_TOKEN_MONETARY_UNIT_CONFLICT' in row['reason_codes']:
            conflicts.append({'type': 'TOKEN_MONETARY_DIMENSION_CONFLICT', 'claim_ids': [row['claim_id']],
                              'resolved_monetary_amount': None, 'reported_metric': row['reported_metric']})
    for key, rows in comparisons.items():
        # Different clocks/types remain a path, not an automatic contradiction.
        groups = defaultdict(list)
        for row in rows:
            groups[(row['event_date'], row['snapshot'], row['metric_type'])].append(row)
        for scope, group in groups.items():
            semantic_values = ({k: v for k, v in row['reported_metric'].items()
                if k not in ('reported_rounding_half_unit', 'reported_scale', 'market_numeric_authority')}
                for row in group)
            if len({digest_json_stream(value) for value in semantic_values}) > 1:
                conflicts.append({'type': 'REPORTED_VALUE_CONFLICT_OR_SCOPE_UNKNOWN', 'comparison_key': key,
                    'date_snapshot_metric': list(scope), 'claim_ids': [row['claim_id'] for row in group],
                    'resolved_value': None, 'averaging_or_scope_relabel_allowed': False})
        if len({row['snapshot'] for row in rows}) > 1:
            conflicts.append({'type': 'DIFFERENT_SNAPSHOT_PATH_REQUIRES_SEPARATE_CLOCKS', 'comparison_key': key,
                              'claim_ids': [row['claim_id'] for row in rows], 'same_clock_contradiction_proven': False})
    for key, values in arithmetic.items():
        if set(values) == {'LIMIT_UP_COUNT', 'FAILED_SEAL_COUNT', 'SEALING_RATE_PERCENT'} and all(values.values()):
            up, failed, rate = (Fraction(values[k]['value']['numerator'], values[k]['value']['denominator'])
                                for k in ('LIMIT_UP_COUNT', 'FAILED_SEAL_COUNT', 'SEALING_RATE_PERCENT'))
            rounding = values['SEALING_RATE_PERCENT']['reported_rounding_half_unit']
            tolerance = Fraction(rounding['numerator'], rounding['denominator'])
            if up + failed > 0 and abs(up / (up + failed) * 100 - rate) > tolerance:
                conflicts.append({'type': 'DECLARED_SAME_POOL_SEAL_RATE_ARITHMETIC_CONFLICT',
                    'receipt_id': key[0], 'declared_population': key[3], 'population_verified': False,
                    'same_pool_assumption_only': True, 'resolved_market_value': None})
    shared = []
    for name, values in common_names.items():
        themes = {theme for _, ids, _ in values for theme in ids}
        if len(themes) > 1:
            shared.append({'reported_name': name, 'theme_ids': sorted(themes), 'claim_ids': sorted({v[0] for v in values}),
                           'resolved_selected_ids': sorted({v[2] for v in values if v[2]}),
                           'independent_synergy_countable': False, 'complete_dated_membership_status': 'UNKNOWN'})
    available = sum(row['status'] == 'AUTHENTICATED_REPORTED_CLAIM' for row in items)
    result = {'schema_version': VERSION, **source_identity(), 'source_input_hash': input_hash,
        'observation_date': str(bundle.observation_date), 'decision_cutoff': cutoff.isoformat(),
        'native_theme_parent_hash': digest_json_stream(native_theme_parent),
        'native_selection_parent_hash': digest_json_stream(native_selection_parent),
        'native_theme_context_hash': native_theme_parent['context_content_hash'],
        'frozen_selected_ids': list(selected), 'frozen_qualified_ids': list(qualified),
        'frozen_scope_hash': digest_json_stream({'selected': selected, 'qualified': qualified}),
        'original_artifacts': [originals[key][1] for key in sorted(originals)], 'items': items,
        'conflicts': conflicts, 'cross_theme_shared_reported_names': shared,
        'artifact_manifest_hash': digest_json_stream([a.model_dump(mode='json') for a in artifacts]),
        'claim_manifest_hash': digest_json_stream([c.model_dump(mode='json') for c in claims]),
        'source_refs': sorted(key for key, (_, meta) in originals.items() if meta['status'] == 'AVAILABLE_REPORTED_SOURCE'),
        'reviewed_source_refs': sorted(originals),
        'status': 'AVAILABLE_WITH_EXPLICIT_UNKNOWN' if available else 'UNKNOWN',
        'reason_codes': ([] if available else ['EVENT_CHANNEL_HAS_NO_USABLE_REPORTED_CLAIMS']) if items else
                        ['EVENT_CHANNEL_HAS_NO_ARTICLES_NOT_NO_CATALYST'],
        'counts': {'reported_claims': len(items), 'authenticated_reported': available, 'unknown': len(items) - available},
        'report_banner_required': not bool(available),
        'report_banner': None if available else '事件报道来源不足或不能通过真实时钟认证；不能据此断言无催化。',
        'reported_origin_family_count': len({row['origin_family'] for row in items if row['origin_family']}),
        'origin_count_is_not_independent_catalyst_or_market_data_count': True,
        'stock_selection_changed': False, 'stage_or_threshold_changed': False,
        'formal_focus': False, 'official_pit_validation_countable': False,
        'historical_mainline_qualification_status': 'UNKNOWN', 'historical_emotion_cycles_verified': 0,
        'parameter_status': 'INITIAL_UNVALIDATED_REPORT_SEMANTICS', 'market_edge': 'NOT_ESTABLISHED',
        'default_strategy_or_service_integration': 'NOT_CONNECTED'}
    result['event_context_content_hash'] = digest_json_stream(result)
    def revalidate():
        _scope(bundle, native_theme_parent, native_selection_parent, selected, qualified, cutoff,
               _theme_parent_validator, _selection_parent_validator)
        for artifact in artifacts:
            _, current = _original(artifact, artifact_reader, cutoff, bundle.coverage_type)
            if current != originals[artifact.receipt_id][1]:
                raise ValueError('EVENT_ORIGINAL_CAPABILITY_CHANGED_AFTER_AUTHENTICATION')
    return _EventContext(result, _CONTEXT_TOKEN, revalidate)


def annotate_theme_observation_diagnostics(native_diagnostics, event_context):
    """Return an attachment envelope; original diagnostics and their hash stay intact."""
    if not isinstance(event_context, _EventContext):
        raise ValueError('EVENT_NATIVE_PRODUCER_ATTACHMENT_CAPABILITY_REQUIRED')
    event_context.validate()
    if (digest_json_stream({k: v for k, v in event_context.items() if k != 'event_context_content_hash'})
            != event_context.get('event_context_content_hash')):
        raise ValueError('EVENT_CONTEXT_CONTENT_HASH_MISMATCH')
    if (digest_json_stream({k: v for k, v in native_diagnostics.items() if k != 'diagnostic_content_hash'})
            != native_diagnostics.get('diagnostic_content_hash')
            or native_diagnostics.get('source_input_hash') != event_context['source_input_hash']
            or native_diagnostics.get('theme_context_hash') != event_context['native_theme_context_hash']
            or stamp(native_diagnostics.get('decision_cutoff')) != stamp(event_context['decision_cutoff'])
            or native_diagnostics.get('frozen_selected_ids') != event_context['frozen_selected_ids']):
        raise ValueError('EVENT_DIAGNOSTIC_PARENT_OR_FROZEN_SCOPE_MISMATCH')
    # Only pre-existing native display groups can receive event cards.
    visible = {row['theme_id'] for key in ('confirmed_mainlines', 'fermentation_watch',
        'active_divergence_mainlines', 'qualified_theme_data_incomplete') for row in native_diagnostics.get(key, ())}
    cards = [row['claim_id'] for row in event_context['items'] if set(row['theme_ids']) & visible]
    result = {'schema_version': VERSION, 'native_diagnostics': deepcopy(native_diagnostics),
              'reported_event_context': deepcopy(dict(event_context)), 'display_claim_ids': cards,
              'news_created_mainlines': False, 'stock_selection_changed': False,
              'stage_or_threshold_changed': False, 'formal_focus': False,
              'official_pit_validation_countable': False}
    result['attachment_content_hash'] = digest_json_stream(result)
    return result
