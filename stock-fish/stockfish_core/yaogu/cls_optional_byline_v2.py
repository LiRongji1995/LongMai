"""SHA-bound CLS originals with an optional byline and dated text witnesses.

This independent V2 never registers itself in the frozen V1 parser, selector,
CLI, or historical runs. Process seals are interface checks, not protection
against a malicious Python caller. Publication is not a business-date proof.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import weakref

from pydantic import Field, field_validator

from .contracts import CoverageType, Frozen, SourceReceipt
from .history_artifacts import digest_json_stream
from .research_context_artifacts import SourceArtifactReader
from .theme_event_evidence import ArticleArtifact, _text
from .visibility import SH, stamp

VERSION = 'CLS_NEXT_DATA_OPTIONAL_BYLINE_V2'
FEATURE_VERSION = 'AUTHENTICATED_CLS_OPTIONAL_BYLINE_REPORTED_TEXT_V2'
MAX_ARTICLE_BYTES = 2 * 1024 * 1024
_TOKEN = object()
_ISSUED = weakref.WeakValueDictionary()


def source_identity():
    root = Path(__file__).resolve().parents[2]
    paths = (Path(__file__), root / 'docs/CLS_NEXT_DATA_OPTIONAL_BYLINE_V2.md',
             Path(__file__).with_name('theme_event_evidence.py'),
             Path(__file__).with_name('contracts.py'),
             Path(__file__).with_name('history_artifacts.py'),
             Path(__file__).with_name('research_context_artifacts.py'),
             Path(__file__).with_name('visibility.py'))
    hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    return {'implementation_version': VERSION, 'feature_version': FEATURE_VERSION,
            'source_file_hashes': hashes, 'source_hash': digest_json_stream(hashes)}


class _NextData(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts = []
        self.active = None

    def handle_starttag(self, tag, attrs):
        if tag != 'script':
            return
        ids = [v for k, v in attrs if k == 'id']
        if '__NEXT_DATA__' not in ids:
            return
        if self.active is not None or ids != ['__NEXT_DATA__']:
            raise ValueError('CLS_V2_NEXT_DATA_SCRIPT_ATTRIBUTES_INVALID')
        types = [v for k, v in attrs if k == 'type']
        if types != ['application/json']:
            raise ValueError('CLS_V2_NEXT_DATA_SCRIPT_TYPE_INVALID')
        self.active = []

    def handle_data(self, value):
        if self.active is not None:
            self.active.append(value)

    def handle_endtag(self, tag):
        if tag == 'script' and self.active is not None:
            self.scripts.append(''.join(self.active))
            self.active = None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('CLS_V2_DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError('CLS_V2_NONFINITE_JSON_CONSTANT')


def parse_cls_article_v2(raw: bytes, *, source_url: str):
    """Extract one fixed original payload; this alone issues no capability."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_ARTICLE_BYTES:
        raise ValueError('CLS_V2_ORIGINAL_BYTES_TYPE_OR_SIZE_INVALID')
    if not isinstance(source_url, str):
        raise ValueError('CLS_V2_ORIGINAL_URL_INVALID')
    try:
        url = urlsplit(source_url)
        valid_url = (url.scheme == 'https' and url.hostname in ('cls.cn', 'www.cls.cn')
                     and url.port in (None, 443) and url.username is None
                     and url.password is None and not url.query and not url.fragment)
    except ValueError as exc:
        raise ValueError('CLS_V2_ORIGINAL_URL_INVALID') from exc
    article_id = re.fullmatch(r'/detail/([1-9]\d*)/?', url.path)
    if not valid_url or article_id is None:
        raise ValueError('CLS_V2_ORIGINAL_DOMAIN_OR_PATH_INVALID')
    try:
        value = raw.decode('utf-8', errors='strict')
    except UnicodeDecodeError as exc:
        raise ValueError('CLS_V2_ORIGINAL_UTF8_INVALID') from exc
    html = _NextData()
    html.feed(value)
    html.close()
    if html.active is not None or len(html.scripts) != 1:
        raise ValueError('CLS_V2_NEXT_DATA_SINGLE_CLOSED_ORIGINAL_REQUIRED')
    try:
        data = json.loads(html.scripts[0], object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('CLS_V2_NEXT_DATA_JSON_INVALID') from exc
    for key in ('props', 'pageProps', 'articleDetail'):
        if not isinstance(data, dict) or key not in data:
            raise ValueError('CLS_V2_FIXED_ARTICLE_SCHEMA_INVALID')
        data = data[key]
    if not isinstance(data, dict):
        raise ValueError('CLS_V2_FIXED_ARTICLE_SCHEMA_INVALID')
    if type(data.get('id')) is not int or data['id'] <= 0 or str(data['id']) != article_id[1]:
        raise ValueError('CLS_V2_ORIGINAL_ARTICLE_ID_MISMATCH')
    if type(data.get('ctime')) is not int or data['ctime'] <= 0:
        raise ValueError('CLS_V2_ORIGINAL_EPOCH_MUST_BE_POSITIVE_INTEGER')
    try:
        published = datetime.fromtimestamp(data['ctime'], SH)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError('CLS_V2_ORIGINAL_EPOCH_OUT_OF_RANGE') from exc
    if not all(isinstance(data.get(k), str) and data[k].strip() for k in ('title', 'content')):
        raise ValueError('CLS_V2_ORIGINAL_TITLE_OR_CONTENT_INVALID')
    body = _text(data['content'])
    if not body:
        raise ValueError('CLS_V2_ORIGINAL_BODY_EMPTY')
    author = data.get('author')
    name = None
    author_reason = 'CLS_V2_AUTHOR_ABSENT_OR_NULL'
    if author is not None:
        if not isinstance(author, dict):
            raise ValueError('CLS_V2_AUTHOR_OBJECT_TYPE_INVALID')
        if 'name' in author:
            if not isinstance(author['name'], str):
                raise ValueError('CLS_V2_AUTHOR_NAME_TYPE_INVALID')
            name = author['name'].strip() or None
            author_reason = 'CLS_V2_AUTHOR_NAME_EMPTY' if name is None else None
        else:
            author_reason = 'CLS_V2_AUTHOR_NAME_ABSENT'
    return {'parser': VERSION, 'article_id': data['id'], 'title': data['title'].strip(),
            'body': body, 'body_sha256': hashlib.sha256(body.encode()).hexdigest(),
            'author': name, 'author_status': 'AVAILABLE' if name is not None else 'UNKNOWN',
            'author_reason_codes': [] if author_reason is None else [author_reason],
            'source': '财联社', 'publisher_family': 'CLS', 'origin_family': 'CLS',
            'upstream_origin_status': 'UNKNOWN', 'independent_information_source_verified': False,
            'publication': data['ctime'], 'publication_lower': published.isoformat(),
            'publication_upper': published.isoformat(), 'publication_precision': 'SECOND_VENDOR_EPOCH'}


class _BoundEvidence(dict):
    __slots__ = ('_sealed_hash', '_revalidate', '_kind', '__weakref__')

    def __init__(self, value, token, revalidate, kind):
        if token is not _TOKEN:
            raise ValueError('CLS_V2_PRODUCER_CAPABILITY_REQUIRED')
        super().__init__(value)
        self._sealed_hash = digest_json_stream(value)
        self._revalidate, self._kind = revalidate, kind
        _ISSUED[id(self)] = self

    def validate(self):
        if _ISSUED.get(id(self)) is not self:
            raise ValueError('CLS_V2_ISSUED_PROCESS_CAPABILITY_REQUIRED')
        if digest_json_stream(self) != self._sealed_hash:
            raise ValueError('CLS_V2_EVIDENCE_CHANGED_AFTER_AUTHENTICATION')
        self._revalidate()


def _read_original(reader, artifact, cutoff, coverage_type):
    if not isinstance(reader, SourceArtifactReader):
        raise ValueError('CLS_V2_REAL_ARTIFACT_READER_REQUIRED')
    if coverage_type not in ('TEST_FIXTURE', 'PROSPECTIVE_CAPTURED',
                              'RECONSTRUCTED_HISTORICAL', 'UNVERIFIABLE_AS_OF'):
        raise ValueError('CLS_V2_COVERAGE_TYPE_INVALID')
    if artifact.parser != VERSION:
        raise ValueError('CLS_V2_ARTIFACT_PARSER_VERSION_MISMATCH')
    rows = [r for r in reader.receipts if r.receipt_id == artifact.receipt_id]
    if len(rows) != 1:
        raise ValueError('CLS_V2_ORIGINAL_RECEIPT_UNRESOLVED')
    receipt = SourceReceipt.model_validate(rows[0])
    if reader._receipts.get(artifact.receipt_id) != receipt:
        raise ValueError('CLS_V2_READER_RECEIPT_BINDING_MISMATCH')
    if (artifact.actual_received_at != receipt.first_seen_at
            or artifact.ingested_at != receipt.ingested_at):
        raise ValueError('CLS_V2_ACTUAL_CAPTURE_RECEIPT_CLOCK_MISMATCH')
    path = reader._paths.get(artifact.receipt_id)
    if path is None or not path.is_file() or path.stat().st_size > MAX_ARTICLE_BYTES:
        raise ValueError('CLS_V2_ORIGINAL_FILE_SIZE_OR_PATH_INVALID')
    raw = reader(artifact.receipt_id)
    if not isinstance(raw, bytes) or len(raw) > MAX_ARTICLE_BYTES:
        raise ValueError('CLS_V2_ORIGINAL_BYTES_TYPE_OR_SIZE_INVALID')
    if hashlib.sha256(raw).hexdigest() != receipt.content_hash:
        raise ValueError('CLS_V2_ORIGINAL_BYTES_SHA_MISMATCH')
    reasons = []
    if receipt.status != 'COMPLETE' or not receipt.pagination_complete or receipt.row_count < 1:
        reasons.append('CLS_V2_ORIGINAL_SOURCE_NOT_COMPLETE')
    article = parse_cls_article_v2(raw, source_url=receipt.source_url) if not reasons else None
    clocks = (artifact.request_started_at, artifact.actual_received_at, artifact.ingested_at,
              receipt.event_time, receipt.source_published_at, receipt.first_seen_at, receipt.ingested_at)
    if max(clocks) > cutoff:
        reasons.append('CLS_V2_ACTUAL_CAPTURE_OR_RECEIPT_CLOCK_AFTER_CUTOFF')
    if article and stamp(article['publication_upper']) > cutoff:
        reasons.append('CLS_V2_PUBLICATION_AFTER_CUTOFF')
    result = {'schema_version': VERSION, **source_identity(), 'receipt_id': receipt.receipt_id,
              'coverage_type': coverage_type, 'decision_cutoff': cutoff.isoformat(),
              'source_url': receipt.source_url, 'raw_sha256': receipt.content_hash, 'raw_bytes': len(raw),
              'source_receipt_identity_hash': digest_json_stream(receipt.model_dump(mode='json')),
              'source_receipt_clocks': {k: getattr(receipt, k).isoformat() for k in
                                      ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')},
              'actual_request_started_at': artifact.request_started_at.isoformat(),
              'actual_received_at': artifact.actual_received_at.isoformat(),
              'ingested_at': artifact.ingested_at.isoformat(),
              'status': 'UNKNOWN' if reasons else 'AVAILABLE_REPORTED_SOURCE', 'reason_codes': reasons,
              'article': article, 'historical_first_seen_proven': False,
              'official_pit_validation_countable': False, 'default_strategy_or_service_integration': 'NOT_CONNECTED'}
    return result


def authenticate_cls_original_v2(*, artifact_reader, artifact, cutoff,
                                  coverage_type: CoverageType):
    artifact = ArticleArtifact.model_validate(artifact)
    cutoff = stamp(cutoff)
    result = _read_original(artifact_reader, artifact, cutoff, coverage_type)
    expected = digest_json_stream(result)

    def revalidate():
        current = _read_original(artifact_reader, artifact, cutoff, coverage_type)
        if digest_json_stream(current) != expected:
            raise ValueError('CLS_V2_ORIGINAL_OR_IMPLEMENTATION_CHANGED')

    evidence = _BoundEvidence(result, _TOKEN, revalidate, 'ORIGINAL')
    evidence.validate()
    return evidence


class CLSNamedWitnessClaim(Frozen):
    witness_id: str = Field(min_length=1)
    receipt_id: str = Field(min_length=1)
    reported_business_date: date
    reported_name: str = Field(min_length=1, max_length=100)
    evidence_text: str = Field(min_length=1, max_length=2048)
    body_occurrence: int = Field(default=0, ge=0, strict=True)
    date_context_text: str | None = Field(default=None, min_length=1, max_length=8192)
    date_context_occurrence: int = Field(default=0, ge=0, strict=True)
    requested_instrument_id: str | None = None

    @field_validator('reported_name')
    @classmethod
    def exact_name(cls, value):
        if value != value.strip() or not value:
            raise ValueError('CLS_V2_REPORTED_NAME_INVALID')
        return value


def _locator(body, text, occurrence):
    starts = [m.start() for m in re.finditer(re.escape(text), body)]
    if occurrence >= len(starts):
        raise ValueError('CLS_V2_WITNESS_SPAN_NOT_IN_ORIGINAL_BODY')
    return {'field': '__NEXT_DATA__.props.pageProps.articleDetail.content->HTML_TEXT',
            'start': starts[occurrence], 'length': len(text),
            'span_sha256': hashlib.sha256(text.encode()).hexdigest()}


def _date_proof(text, business_date, publication_year):
    full = list(re.finditer(r'(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)', text))
    full += list(re.finditer(r'(?<!\d)(\d{4})年(\d{1,2})月(\d{1,2})日', text))
    month_days = [m for m in re.finditer(r'(?<!\d)(\d{1,2})月(\d{1,2})日', text)
                  if not any(a.start() <= m.start() < a.end() for a in full)]
    # A literal day is not necessarily in the publication year. Do not silently
    # turn "last year May13" or an unbound "2025's May13" into this year's day.
    if month_days:
        relative_year = re.search(r'去年|前年|上年|明年|后年|來年|来年|翌年|次年|同年|当年|往年|历年|那年', text)
        unbound_year = any(not any(a.start() <= m.start() < a.end() for a in full)
                           for m in re.finditer(r'(?<!\d)\d{4}年', text))
        if relative_year or unbound_year:
            return 'UNKNOWN', ['CLS_V2_REPORTED_DATE_YEAR_CONTEXT_UNRESOLVED'], None
    found, inferred = set(), False
    try:
        for m in full:
            found.add(date(*(int(m[i]) for i in (1, 2, 3))))
        for m in month_days:
            found.add(date(publication_year, int(m[1]), int(m[2])))
            inferred = True
    except ValueError:
        return 'UNKNOWN', ['CLS_V2_REPORTED_DATE_LITERAL_INVALID'], None
    if not found:
        return 'UNKNOWN', ['CLS_V2_REPORTED_BUSINESS_DATE_NOT_IN_ORIGINAL_TEXT'], None
    if len(found) != 1 or business_date not in found:
        return 'UNKNOWN', ['CLS_V2_REPORTED_DATE_CONTEXT_AMBIGUOUS_OR_MISMATCH'], None
    return 'AVAILABLE', [], ('ORIGINAL_MONTH_DAY_WITH_CTIME_YEAR' if inferred else 'ORIGINAL_FULL_DATE_LITERAL')


def build_cls_named_witness_v2(original, claim):
    if (not isinstance(original, _BoundEvidence) or original._kind != 'ORIGINAL'
            or _ISSUED.get(id(original)) is not original):
        raise ValueError('CLS_V2_ISSUED_ORIGINAL_CAPABILITY_REQUIRED')
    original.validate()
    claim = CLSNamedWitnessClaim.model_validate(claim)
    if claim.receipt_id != original['receipt_id']:
        raise ValueError('CLS_V2_WITNESS_RECEIPT_BINDING_MISMATCH')
    reasons = list(original['reason_codes'])
    locator, date_locator, date_basis = None, None, None
    date_status = 'UNKNOWN'
    article = original['article']
    if not reasons and article:
        body = article['body']
        locator = _locator(body, claim.evidence_text, claim.body_occurrence)
        if claim.reported_name not in claim.evidence_text:
            raise ValueError('CLS_V2_REPORTED_NAME_NOT_IN_WITNESS_SPAN')
        context = claim.date_context_text or claim.evidence_text
        date_locator = _locator(body, context, claim.date_context_occurrence if claim.date_context_text else claim.body_occurrence)
        if not (date_locator['start'] <= locator['start'] and
                locator['start'] + locator['length'] <= date_locator['start'] + date_locator['length']):
            raise ValueError('CLS_V2_DATE_CONTEXT_DOES_NOT_CONTAIN_BOUND_WITNESS')
        publication_day = stamp(article['publication_lower']).astimezone(SH).date()
        date_status, date_reasons, date_basis = _date_proof(context, claim.reported_business_date, publication_day.year)
        reasons.extend(date_reasons)
        if (claim.reported_business_date > publication_day or
                claim.reported_business_date > stamp(original['decision_cutoff']).astimezone(SH).date()):
            reasons.append('CLS_V2_REPORTED_BUSINESS_DATE_AFTER_PUBLICATION_OR_CUTOFF')
            date_status = 'UNKNOWN'
    value = {'schema_version': VERSION, **source_identity(), 'witness_id': claim.witness_id,
             'receipt_id': claim.receipt_id, 'source_original_evidence_hash': digest_json_stream(original),
             'raw_sha256': original['raw_sha256'], 'reported_business_date': str(claim.reported_business_date),
             'reported_name': claim.reported_name, 'status': 'UNKNOWN' if reasons else 'AUTHENTICATED_DATED_NAMED_REPORTED_WITNESS',
             'reason_codes': reasons, 'body_locator': locator, 'date_context_locator': date_locator,
             'reported_business_date_status': date_status, 'date_proof_basis': date_basis,
             'date_context_association_role': 'EXPLICIT_REVIEWED_CONTIGUOUS_SOURCE_CONTEXT_NOT_SEMANTIC_CERTIFICATE',
             'publisher_family': 'CLS', 'origin_family': 'CLS',
             'author': article['author'] if article else None,
             'author_status': article['author_status'] if article else 'UNKNOWN',
             'requested_instrument_id': claim.requested_instrument_id,
             'resolved_instrument_id': None, 'security_identity_status': 'UNKNOWN',
             'historical_qualification_status': 'UNKNOWN', 'complete_theme_membership_status': 'UNKNOWN',
             'source_population': 'EXPLICIT_REPORTED_NAMES_ONLY_NOT_COMPLETE_COHORT',
             'dated_reported_name_countable': not reasons, 'independent_information_source_verified': False,
             'numeric_market_authority': False, 'strict_limit_event_verified': False,
             'official_pit_validation_countable': False, 'historical_first_seen_proven': False,
             'selection_or_focus_effect': 'NONE'}

    def revalidate():
        original.validate()

    witness = _BoundEvidence(value, _TOKEN, revalidate, 'WITNESS')
    witness.validate()
    return witness


def validate_cls_evidence_v2(value):
    if not isinstance(value, _BoundEvidence) or _ISSUED.get(id(value)) is not value:
        raise ValueError('CLS_V2_ISSUED_PROCESS_CAPABILITY_REQUIRED')
    value.validate()
    return None
