"""Bounded primary-site reported materials; never directions or EVENT authority.

Discovery uses only real homepage anchors. Publication, literal business date,
capture time and economic attribution are separate. Missing dates stay UNKNOWN.
The owner calls this in its cumulative capture phase; no real network is used
by this module's TEST_FIXTURE tests.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
from html.parser import HTMLParser
from http.client import HTTPException
import json
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .premarket_runtime_v1 import json_bytes, utc_now

VERSION = 'PREMARKET_REPORTED_NEWS_V1'
HOMES = ('https://www.cls.cn/', 'https://www.stcn.com/')
HOSTS = {'www.cls.cn': 'CLS', 'cls.cn': 'CLS',
         'www.stcn.com': 'STCN', 'stcn.com': 'STCN'}
KEYWORDS = ('焦点复盘', '每日收评', '涨停揭秘', '揭秘涨停')
DEFAULTS = dict(offline=False, max_articles=8, max_gets=10,
                request_timeout_seconds=90, max_body_bytes=2*1024*1024,
                max_redirects=2)


def _allowed(url, *, article=False):
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == 'https' and parsed.hostname in HOSTS and
                 parsed.port in (None, 443) and parsed.username is None and
                 parsed.password is None and not parsed.query and not parsed.fragment)
    except (TypeError, ValueError):
        return False
    if not valid:
        return False
    if article:
        return bool(re.fullmatch(r'/detail/[1-9][0-9]*/?', parsed.path) if
                    HOSTS[parsed.hostname] == 'CLS' else
                    re.fullmatch(r'/article/detail/[1-9][0-9]*\.html', parsed.path))
    return parsed.path in ('', '/') or _allowed(url, article=True)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # The caller explicitly admits and counts each allowed redirect GET.
        return None


def _make_opener():
    return build_opener(_NoRedirect())


class _Anchors(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current, self.rows = None, []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            links = [value for key, value in attrs if key == 'href']
            titles = [value for key, value in attrs if key == 'title']
            self.current = dict(href=links[0], title=titles[0] if titles else '', parts=[]) if len(links) == 1 else None

    def handle_data(self, value):
        if self.current is not None:
            self.current['parts'].append(value)

    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            row = self.current
            self.rows.append(dict(href=row['href'], title=''.join(row['parts']).strip() or row['title']))
            self.current = None


def _discover(raw, home, names, *, candidates_only=False):
    parsed = _Anchors()
    parsed.feed(raw.decode('utf-8', errors='strict'))
    parsed.close()
    rows = []
    for anchor in parsed.rows:
        url = urljoin(home, anchor['href'])
        if not _allowed(url, article=True) or HOSTS[urlsplit(url).hostname] != HOSTS[urlsplit(home).hostname]:
            continue
        title = anchor['title']
        if candidates_only and not any(name in title for name in names):
            continue
        score = sum(word in title for word in KEYWORDS) * 2 + sum(name in title for name in names)
        if score:
            rows.append(dict(url=url, discovered_title=title, discovery_home=home,
                             priority=score, role='HOMEPAGE_ANCHOR_NOT_BUSINESS_ATTRIBUTION'))
    return rows


def _archive(output, budget, ordinal, raw, receipt):
    path = output / f'http-{ordinal:02d}.html'
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        import os
        os.fsync(stream.fileno())
    _, rawref = budget.read(path, expected=hashlib.sha256(raw).hexdigest())
    receipt.update(raw=rawref, ingested_at=utc_now(),
                   integrity_scope='LOCAL_SHA256_NOT_PUBLISHER_AUTHENTICATION',
                   receipt_is_not_SOURCE_or_EVENT_capability=True)
    receiptref = budget.write_json(output / f'http-{ordinal:02d}.receipt.json', receipt)
    return dict(raw=rawref, receipt=receiptref, capture=receipt)


def _fetch(url, output, budget, config, state, opener):
    redirects = 0
    original = url
    while state['gets'] < config['max_gets']:
        if not _allowed(url):
            raise ValueError('NEWS_URL_OUTSIDE_EXACT_PRIMARY_ALLOWLIST')
        admission = budget.preclaim_http_request(url, max_attempts=1)
        state['gets'] += 1
        started = utc_now()
        response = None
        raw = bytearray()
        try:
            request = Request(url, method='GET', headers={
                'User-Agent': 'StockFish-Research/1.0', 'Accept': 'text/html',
                'Accept-Encoding': 'identity'})
            try:
                response = opener.open(request, timeout=config['request_timeout_seconds'])
            except HTTPError as error:
                response = error
            status = response.getcode()
            complete = True
            while True:
                chunk = response.read(min(65536, config['max_body_bytes'] + 1 - len(raw)))
                if not chunk:
                    break
                budget.charge_read(len(chunk))  # actual HTTP body returned to Python
                raw.extend(chunk)
                if len(raw) > config['max_body_bytes']:
                    complete = False
                    break
            header_items = list(response.headers.items())
            headers = {key.lower(): value for key, value in header_items
                       if key.lower() in ('content-type', 'content-length', 'date', 'last-modified', 'location')}
            lengths = [value.strip() for key, value in header_items if key.lower() == 'content-length']
            length_status = ('NOT_DECLARED' if not lengths else 'VALID_MATCH' if
                len(lengths) == 1 and re.fullmatch('[0-9]+', lengths[0]) and int(lengths[0]) == len(raw)
                else 'DECLARED_LENGTH_INVALID_OR_INCOMPLETE')
            body_limit = len(raw) > config['max_body_bytes']
            complete = complete and length_status != 'DECLARED_LENGTH_INVALID_OR_INCOMPLETE'
            body = bytes(raw[:config['max_body_bytes']])
            receipt = dict(schema_version=VERSION + '_HTTP_CAPTURE', method='GET',
                original_requested_url=original, requested_url=url, http_status=status,
                request_started_at=started, first_seen_at=utc_now(),
                captured_response_headers=headers, actual_body_bytes_returned=len(raw),
                content_length_status=length_status,
                raw_capture_complete=complete, archived_prefix_only=not complete,
                status='COMPLETE_HTTP_BODY' if complete and status == 200 else
                    'BODY_LIMIT_EXCEEDED' if body_limit else 'CONTENT_LENGTH_MISMATCH_OR_INVALID' if not complete
                    else 'HTTP_STATUS_NOT_200',
                source_published_at=None, publication_not_inferred_from_capture=True)
        except (URLError, TimeoutError, OSError, HTTPException) as error:
            body = bytes(raw[:config['max_body_bytes']])
            receipt = dict(schema_version=VERSION + '_HTTP_CAPTURE', method='GET',
                original_requested_url=original, requested_url=url, http_status=None,
                request_started_at=started, first_seen_at=utc_now(),
                status='FETCH_FAILED', error_type=type(error).__name__,
                error=str(error)[:512], raw_capture_complete=False,
                archived_prefix_only=bool(raw), actual_body_bytes_returned=len(raw),
                source_published_at=None, publication_not_inferred_from_capture=True)
        finally:
            try:
                if response is not None:
                    response.close()
            finally:
                budget.record_http_request(admission, attempts=1)
        archived = _archive(output, budget, state['gets'], body, receipt)
        state['captures'].append(archived)
        location = receipt.get('captured_response_headers', {}).get('location')
        if receipt.get('http_status') in (301, 302, 303, 307, 308) and location:
            target = urljoin(url, location)
            if not _allowed(target) or HOSTS[urlsplit(target).hostname] != HOSTS[urlsplit(url).hostname]:
                archived['redirect_status'] = 'DENIED_OUTSIDE_SAME_PRIMARY_FAMILY'
                return None
            if redirects >= config['max_redirects']:
                archived['redirect_status'] = 'REDIRECT_BOUND_REACHED_NOT_RETRIED'
                return None
            archived['redirect_status'] = 'FOLLOWED_WITH_SEPARATE_GET_ADMISSION'
            redirects += 1
            url = target
            continue
        return dict(url=url, body=body, archived=archived) if receipt['status'] == 'COMPLETE_HTTP_BODY' else None
    return None


def _parse_material(raw, url, observed, coverage):
    from .cls_optional_byline_v2 import parse_cls_article_v2, _date_proof
    from .theme_event_evidence import _parse_article
    family = HOSTS[urlsplit(url).hostname]
    article = (parse_cls_article_v2(raw, source_url=url) if family == 'CLS' else
               _parse_article(raw, 'STCN_HTML_V1', SimpleNamespace(source_url=url), coverage))
    lower, upper = article['publication_lower'], article['publication_upper']
    lower = lower.isoformat() if isinstance(lower, datetime) else lower
    upper = upper.isoformat() if isinstance(upper, datetime) else upper
    status, reasons, basis = _date_proof(article['body'], observed,
                                        datetime.fromisoformat(lower).year)
    return dict(title=article['title'], body=article['body'],
        body_sha256=hashlib.sha256(article['body'].encode('utf-8')).hexdigest(),
        parser='CLS_NEXT_DATA_OPTIONAL_BYLINE_V2' if family == 'CLS' else 'STCN_HTML_V1',
        publisher_family=article['publisher_family'], origin_family=article['origin_family'],
        publication_lower=lower, publication_upper=upper,
        publication_precision=article['publication_precision'],
        business_date_status=status, business_date_reason_codes=reasons,
        business_date_basis=basis, requested_business_date=observed.isoformat(),
        economic_attribution='UNKNOWN', EVENT_confirmation='UNKNOWN_NOT_ISSUED',
        publication_is_not_business_date_proof=True)


def collect_reported_news(*, core_run, output_dir, budget, config=None, target_ids=None):
    """Archive bounded reported material, returning ``{summary, metadata: ref4}``.

    The summary contains no stock names or article text and is safe to link in
    a filtered display. Offline means NOT_COLLECTED/UNKNOWN, never legal EMPTY.
    """
    if config is not None and not isinstance(config, dict):
        raise ValueError('NEWS_CONFIGURATION_MAPPING_REQUIRED')
    registered_timeout = getattr(budget, 'limits', {}).get('request_timeout_seconds', 90)
    config = {**DEFAULTS, 'request_timeout_seconds': min(90, registered_timeout), **(config or {})}
    if set(config) != set(DEFAULTS) or type(config['offline']) is not bool:
        raise ValueError('UNKNOWN_NEWS_CONFIGURATION')
    for key, maximum in (('max_articles', 8), ('max_gets', 10),
                         ('max_body_bytes', 2*1024*1024), ('max_redirects', 2)):
        minimum = 0 if key in ('max_articles', 'max_redirects') else 1
        if type(config[key]) is not int or not minimum <= config[key] <= maximum:
            raise ValueError('BOUNDED_NEWS_CONFIGURATION_REQUIRED:' + key)
    timeout = config['request_timeout_seconds']
    if type(timeout) not in (int, float) or not 0 < timeout <= min(90, registered_timeout):
        raise ValueError('NEWS_SOCKET_TIMEOUT_0_TO_90_REQUIRED')
    observed = date.fromisoformat(core_run['observation_date'])
    strategy = core_run.get('strategy_id') or 'SHORT_HORIZON_STRENGTH_R2'
    selected = [row for row in core_run['results'][strategy]['items'] if row.get('research_selected') is True]
    if target_ids is not None:
        pool = core_run['results'][strategy]['items']
        ids = {row['instrument_id'] for row in pool}
        if (not isinstance(target_ids, (list, tuple)) or
                any(type(iid) is not str for iid in target_ids) or
                len(target_ids) != len(set(target_ids)) or not set(target_ids) <= ids):
            raise ValueError('NEWS_TARGET_IDS_MUST_BE_UNIQUE_CORE_POOL_SUBSET')
        selected = [row for row in pool if row['instrument_id'] in set(target_ids)]
    names = [row['name'] for row in selected if isinstance(row.get('name'), str) and row['name']]
    output = Path(output_dir).expanduser().absolute()
    output.mkdir(parents=True, exist_ok=False)
    state = dict(gets=0, captures=[])
    discovered, articles, home_errors = [], [], []
    completed_homes, planned_articles = 0, None
    no_targets = target_ids is not None and not names
    if not config['offline'] and not no_targets:
        opener = _make_opener()
        for home in HOMES:
            fetched = _fetch(home, output, budget, config, state, opener)
            if fetched is not None:
                try:
                    discovered.extend(_discover(fetched['body'], home, names,
                                                candidates_only=target_ids is not None))
                    completed_homes += 1
                except (ValueError, UnicodeError) as error:
                    home_errors.append(dict(home=home, error_type=type(error).__name__))
        deduplicated = {}
        for row in discovered:
            parsed = urlsplit(row['url'])
            identity = (HOSTS[parsed.hostname], re.search(r'[0-9]+', parsed.path)[0])
            if identity not in deduplicated or row['priority'] > deduplicated[identity]['priority']:
                deduplicated[identity] = row
        candidates = sorted(deduplicated.values(), key=lambda row: -row['priority'])
        planned_articles = len(candidates[:config['max_articles']])
        for candidate in candidates[:config['max_articles']]:
            if state['gets'] >= config['max_gets']:
                break
            fetched = _fetch(candidate['url'], output, budget, config, state, opener)
            record = dict(discovery=candidate, status='FETCH_FAILED_OR_INCOMPLETE',
                          economic_attribution='UNKNOWN', EVENT_confirmation='UNKNOWN_NOT_ISSUED')
            if fetched is not None:
                record.update(url=fetched['url'], capture=fetched['archived'], status='PARSE_UNKNOWN')
                try:
                    record['parsed'] = _parse_material(fetched['body'], fetched['url'], observed,
                                                       core_run.get('coverage_type', 'UNVERIFIABLE_AS_OF'))
                    record['status'] = 'PARSED_REPORTED_MATERIAL_NOT_DIRECTION_AUTHORITY'
                except (ValueError, KeyError, TypeError, UnicodeError) as error:
                    record['parse_error'] = dict(type=type(error).__name__, reason=str(error)[:512])
            articles.append(record)
    parsed_count = sum('parsed' in row for row in articles)
    date_counts = {}
    for row in articles:
        status = row.get('parsed', {}).get('business_date_status', 'NOT_PARSED')
        date_counts[status] = date_counts.get(status, 0) + 1
    bodies_complete = all(row['capture']['raw_capture_complete'] is True and
        (row['capture']['status'] == 'COMPLETE_HTTP_BODY' or
         row.get('redirect_status') == 'FOLLOWED_WITH_SEPARATE_GET_ADMISSION')
        for row in state['captures'])
    acquisition_complete = None
    if not config['offline'] and not no_targets:
        if (completed_homes != len(HOMES) or home_errors or not bodies_complete or
                len(articles) != planned_articles or parsed_count != len(articles)):
            acquisition_complete = False
        elif parsed_count > 0 and date_counts == {'AVAILABLE': parsed_count}:
            acquisition_complete = True
    summary = dict(status='OFFLINE_NOT_COLLECTED_UNKNOWN' if config['offline'] else
                   'NOT_REQUESTED_NO_PRESCREEN_TARGET_NAMES' if no_targets else
                   'REPORTED_MATERIALS_ONLY' if parsed_count else 'UNKNOWN_NO_PARSED_REPORTED_MATERIAL',
        HTTP_GET_attempts=state['gets'], articles_saved=sum('capture' in row for row in articles),
        article_records=len(articles), parsed_articles=parsed_count,
        acquisition_complete=acquisition_complete,
        acquisition_scope='DECLARED_TWO_PRIMARY_HOMEPAGES_AND_BOUNDED_DISCOVERED_ARTICLES_NOT_ALL_MARKET_NEWS',
        homepage_requests_declared=len(HOMES), homepages_completed=completed_homes,
        article_requests_planned=planned_articles, business_date_status_counts=date_counts,
        observation_business_date=observed.isoformat(), source_coverage='UNKNOWN',
        economic_attribution='UNKNOWN', EVENT_confirmation='UNKNOWN_NOT_ISSUED',
        network_metric_scope='ACTUAL_URLLIB_GET_INVOCATIONS_NOT_CONFIRMED_WIRE_OR_SERVER_ARRIVALS',
        automatic_retries=0)
    summary.update(target_scope='PRESCREEN_ADMITTED_ONLY' if target_ids is not None else 'ORIGINAL_SELECTED_AND_MARKET_NEWS',
                   target_count=None if target_ids is None else len(target_ids),
                   candidate_name_count=len(names),
                   skipped_by_prescreen=no_targets,
                   general_market_keyword_articles_included=target_ids is None)
    for capture in state['captures']:
        for role in ('raw', 'receipt'):
            budget.read(capture[role]['path'], expected=capture[role])
    metadata = dict(schema_version=VERSION, run_id=core_run['run_id'],
        target_ids=None if target_ids is None else list(target_ids),
        observation_date=core_run['observation_date'], target_session=core_run['target_session'],
        actual_generated_at=utc_now(), summary=summary, config=config,
        original_HTTP_captures=state['captures'], discovered_candidates=discovered,
        articles=articles, homepage_parse_errors=home_errors,
        raw_and_receipt_initial_terminal_SHA7FP_verified=True,
        no_SOURCE_or_EVENT_capability_issued=True, no_direction_mapping_or_company_business_guess=True)
    reference = budget.write_json(output / 'reported-materials.json', metadata)
    validation = [capture[role] for capture in state['captures'] for role in ('raw', 'receipt')]
    return dict(summary=summary, metadata=reference,
                validation_references=validation + [reference])
