"""Recover provider identifiers from anchored canonical response archives.

This explicit research adapter does not fetch, infer windows or monetary units,
identify people, change selection, or grant FUNDS_V1 authority.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
import hashlib
import inspect
import json
from pathlib import Path
import re

from .history_artifacts import digest_json_stream, digest_market_input


VERSION = 'LHB_REPORTED_SEAT_CONTEXT_V1'
FEATURE_VERSION = 'EASTMONEY_UPSTREAM_DEPARTMENT_IDENTITY_V1'
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_NUMERIC_DIGITS = 128
MAX_DECIMAL_EXPONENT = 128
SOURCE_URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
ENDPOINT = 'stock_lhb_stock_detail_em'
SIDES = {'BUY': ('买入', 'RPT_BILLBOARD_DAILYDETAILSBUY'),
         'SELL': ('卖出', 'RPT_BILLBOARD_DAILYDETAILSSELL')}
EXCLUDED_FIELDS = ('RISE_PROBABILITY_3DAY', 'TOTAL_BUYER_SALESTIMES_3DAY',
                   'TOTAL_BUYRIO', 'TOTAL_SELLRIO', 'CHANGE_RATE', 'CLOSE_PRICE')


def _stamp(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('LHB_AWARE_ACTUAL_CLOCK_REQUIRED')
    return result


def _sha(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('LHB_EXTERNAL_SHA256_ANCHOR_REQUIRED')
    return value


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('LHB_DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError('LHB_NONFINITE_JSON_NUMBER:' + value)


def _bounded_int(text):
    if len(text.lstrip('-')) > MAX_NUMERIC_DIGITS:
        raise ValueError('LHB_NUMERIC_PARSER_RESOURCE_BOUND')
    return int(text)


def _bounded_decimal(text):
    if len(text) > 256:
        raise ValueError('LHB_NUMERIC_PARSER_RESOURCE_BOUND')
    result = Decimal(text)
    digits = result.as_tuple()
    if (not result.is_finite() or len(digits.digits) > MAX_NUMERIC_DIGITS
            or abs(digits.exponent) > MAX_DECIMAL_EXPONENT
            or abs(result.adjusted()) > MAX_DECIMAL_EXPONENT):
        raise ValueError('LHB_NUMERIC_PARSER_RESOURCE_BOUND')
    return result


def _decode(raw):
    try:
        return json.loads(raw, parse_float=_bounded_decimal, parse_int=_bounded_int, object_pairs_hook=_pairs,
                          parse_constant=_bad_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('LHB_CANONICAL_ARCHIVE_JSON_INVALID') from error


def _read(path, expected=None):
    path = Path(path)
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError('LHB_ARTIFACT_SIZE_BOUND_EXCEEDED')
    raw = path.read_bytes()
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError('LHB_ARTIFACT_SIZE_BOUND_EXCEEDED')
    actual = {'path': str(path.resolve()), 'bytes': len(raw),
              'sha256': hashlib.sha256(raw).hexdigest()}
    if expected is not None and actual != expected:
        raise ValueError('LHB_ANCHORED_ARTIFACT_CHANGED')
    return _decode(raw), actual


def _exact(value):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ValueError('LHB_REPORTED_NUMERIC_FIELD_INVALID')
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError('LHB_REPORTED_NUMERIC_FIELD_INVALID')
    if isinstance(value, Decimal):
        _bounded_decimal(str(value))
    elif abs(value).bit_length() > 426:
        raise ValueError('LHB_NUMERIC_PARSER_RESOURCE_BOUND')
    return Fraction(value)


def _encoded(value):
    return {'numerator': value.numerator, 'denominator': value.denominator}


def _text(value, label, *, digits=False):
    if (not isinstance(value, str) or not value or value != value.strip()
            or (digits and re.fullmatch('[0-9]+', value) is None)):
        raise ValueError('LHB_' + label + '_INVALID')
    return value


def source_identity():
    root = Path(__file__).resolve().parents[2]
    paths = (Path(__file__), root / 'docs/LHB_SEAT_EVIDENCE_V1.md',
             Path(__file__).with_name('history_artifacts.py'), Path(__file__).with_name('contracts.py'))
    files = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in paths}
    return {'schema_version': VERSION, 'feature_version': FEATURE_VERSION,
            'source_file_hashes': files, 'source_hash': digest_json_stream(files),
            'parameter_status': 'INITIAL_UNVALIDATED_SOURCE_SEMANTICS',
            'default_integration': 'NOT_CONNECTED'}


class ArchivedLhbSeatSource:
    """Local originals capability anchored by the caller's external manifest SHA.

    A SHA proves the supplied bytes stayed fixed, not their publisher's security
    authenticity. The native capture/archive caller remains the trust boundary.
    No SourceReceipt clocks or row counts are invented to fit another schema.
    """

    def __init__(self, manifest_path, expected_manifest_sha256, *, coverage_type='CURRENT_CAPTURE_RESEARCH'):
        if coverage_type not in ('CURRENT_CAPTURE_RESEARCH', 'TEST_FIXTURE'):
            raise ValueError('LHB_SOURCE_COVERAGE_TYPE_UNRECOGNIZED')
        self.coverage_type = coverage_type
        manifest, info = _read(manifest_path)
        if info['sha256'] != _sha(expected_manifest_sha256):
            raise ValueError('LHB_EXTERNAL_MANIFEST_SHA_MISMATCH')
        self._root = Path(info['path']).parent
        self._manifest_info = info
        if not isinstance(manifest, dict) or not isinstance(manifest.get('result'), dict):
            raise ValueError('LHB_CAPTURE_MANIFEST_RESULT_REQUIRED')
        result_path = self._within(manifest['result']['path'])
        report, report_info = _read(result_path, manifest['result'])
        if (not isinstance(report, dict) or not isinstance(report.get('source_artifacts'), list)
                or report.get('schema_version') != 'EXISTING_LHB_SEAT_RESEARCH_PROBE_RESULT_V1'
                or report.get('status') not in ('ADAPTER_RETURNED', 'ADAPTER_EXCEPTION_PRESERVED')
                or manifest.get('status') != report['status']):
            raise ValueError('LHB_CAPTURE_REPORT_ARTIFACTS_REQUIRED')
        self._report_info = report_info
        self._report = report
        if report.get('test_fixture') is True and coverage_type != 'TEST_FIXTURE':
            raise ValueError('LHB_DECLARED_FIXTURE_MUST_REMAIN_FIXTURE')
        self._artifacts = {}
        for declared in report['source_artifacts']:
            path = self._within(declared['path'])
            _, actual = _read(path, declared)
            if actual['path'] in self._artifacts:
                raise ValueError('LHB_DUPLICATE_ARTIFACT_REFERENCE')
            self._artifacts[actual['path']] = actual
        if (type(manifest.get('source_artifact_count')) is not int
                or manifest['source_artifact_count'] != len(self._artifacts)):
            raise ValueError('LHB_CAPTURE_MANIFEST_ARTIFACT_COUNT_MISMATCH')
        self.instrument_id = _text(report.get('instrument_id'), 'SECURITY_SCOPE')
        self.business_session = date.fromisoformat(report['observation_date']).isoformat()
        self.batch_started_at = _stamp(report['batch_started_at'])
        self.batch_completed_at = _stamp(report['batch_completed_at'])
        if self.batch_started_at > self.batch_completed_at:
            raise ValueError('LHB_BATCH_CLOCK_ORDER_INVALID')
        self._receipts = []
        for path, info in self._artifacts.items():
            if path.endswith('.receipt.json'):
                receipt, _ = _read(path, info)
                if (not isinstance(receipt, dict) or receipt.get('dataset') != ENDPOINT
                        or receipt.get('provider') not in ('AKShare', 'AKShare_upstream')
                        or receipt.get('source_url') not in (SOURCE_URL, None)):
                    raise ValueError('LHB_ARCHIVE_RECEIPT_DEFINITION_UNRECOGNIZED')
                rid = receipt.get('receipt_id')
                identity = {key: value for key, value in receipt.items() if key != 'receipt_id'}
                if hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest() != rid:
                    raise ValueError('LHB_LEGACY_RECEIPT_ID_MISMATCH')
                if Path(path).name != str(rid) + '.receipt.json':
                    raise ValueError('LHB_RECEIPT_FILENAME_ID_MISMATCH')
                first, ingest = _stamp(receipt['first_seen_at']), _stamp(receipt['ingested_at'])
                if not self.batch_started_at <= first <= ingest <= self.batch_completed_at:
                    raise ValueError('LHB_RECEIPT_OUTSIDE_ACTUAL_BATCH_CLOCKS')
                raw_path = Path(path).parent / (_sha(receipt['content_hash']) + '.raw.json')
                raw_info = self._artifacts.get(str(raw_path.resolve()))
                if raw_info is None or raw_info['sha256'] != receipt['content_hash']:
                    raise ValueError('LHB_RECEIPT_ORIGINAL_BYTES_NOT_ANCHORED')
                self._receipts.append((receipt, raw_info, info))
        expected_raw = {raw['path'] for _, raw, _ in self._receipts}
        actual_raw = {path for path in self._artifacts if path.endswith('.raw.json')}
        if expected_raw != actual_raw:
            raise ValueError('LHB_UNREGISTERED_RAW_ARCHIVE')
        self._source_hash = digest_json_stream({'manifest': self._manifest_info,
            'report': self._report_info, 'artifacts': self._artifacts, 'coverage_type': coverage_type})
        self._binding = (self.instrument_id, self.business_session, self.batch_started_at,
                         self.batch_completed_at, self.coverage_type)
        self._receipt_state_hash = digest_json_stream(self._receipts)

    def _within(self, path):
        actual = Path(path).resolve()
        if not actual.is_relative_to(self._root):
            raise ValueError('LHB_ARTIFACT_PATH_OUTSIDE_CAPTURE_ARCHIVE')
        return actual

    def validate_unchanged(self):
        if ((self.instrument_id, self.business_session, self.batch_started_at,
             self.batch_completed_at, self.coverage_type) != self._binding
                or digest_json_stream(self._receipts) != self._receipt_state_hash):
            raise ValueError('LHB_SOURCE_CAPABILITY_BINDING_CHANGED')
        _read(self._manifest_info['path'], self._manifest_info)
        _read(self._report_info['path'], self._report_info)
        for info in self._artifacts.values():
            _read(info['path'], info)

    def original_receipts(self):
        self.validate_unchanged()
        return tuple((deepcopy(receipt), _read(raw['path'], raw)[0], deepcopy(raw), deepcopy(info))
                     for receipt, raw, info in self._receipts)

    def audit(self):
        self.validate_unchanged()
        return {'manifest': deepcopy(self._manifest_info), 'capture_report': deepcopy(self._report_info),
                'source_artifacts': [deepcopy(self._artifacts[key]) for key in sorted(self._artifacts)],
                'source_archive_hash': self._source_hash, 'coverage_type': self.coverage_type,
                'batch_started_at': self.batch_started_at.isoformat(),
                'batch_completed_at': self.batch_completed_at.isoformat(),
                'archive_representation': 'CANONICAL_PARSED_RESPONSE_BODY_NOT_WIRE_BYTES',
                'independent_disclosure_published_at': None,
                'per_http_request_started_at': None,
                'local_sha_is_not_publisher_security_authentication': True}


class _SeatEvidence(dict):
    """Process-local provenance handle; JSON alone cannot reactivate it."""


def _side(receipt, instrument_id, business_day):
    params = receipt.get('parameters')
    if not isinstance(params, dict):
        raise ValueError('LHB_REQUEST_PARAMETERS_REQUIRED')
    if receipt['provider'] == 'AKShare':
        choices = [side for side, (flag, _) in SIDES.items() if params.get('flag') == flag]
        if (len(choices) != 1 or params.get('symbol') != instrument_id[:6]
                or params.get('date') != business_day.replace('-', '')):
            raise ValueError('LHB_NORMALIZED_REQUEST_SCOPE_MISMATCH')
        return choices[0]
    choices = [side for side, (_, report) in SIDES.items() if params.get('reportName') == report]
    expected_filter = '(TRADE_DATE=\'' + business_day + '\')(SECURITY_CODE="' + instrument_id[:6] + '")'
    if (len(choices) != 1 or params.get('filter') != expected_filter or params.get('pageNumber') != '1'
            or params.get('columns') != 'ALL' or params.get('sortColumns') != choices[0]
            or params.get('sortTypes') != '-1' or params.get('pageSize') != '500'
            or params.get('source') != 'WEB' or params.get('client') != 'WEB'):
        raise ValueError('LHB_UPSTREAM_REQUEST_SCOPE_MISMATCH')
    return choices[0]


def _row(row, instrument_id, business_day, side, receipt, raw_info, index):
    if not isinstance(row, dict):
        raise ValueError('LHB_UPSTREAM_SEAT_ROW_INVALID')
    if row.get('SECURITY_CODE') != instrument_id[:6] or row.get('SECUCODE') != instrument_id:
        raise ValueError('LHB_UPSTREAM_SECURITY_IDENTITY_MISMATCH')
    if row.get('TRADE_DATE') != business_day + ' 00:00:00':
        raise ValueError('LHB_UPSTREAM_BUSINESS_DATE_UNRECOGNIZED')
    disclosure = _text(row.get('TRADE_ID'), 'REPORTED_DISCLOSURE_ID', digits=True)
    reason_code = _text(row.get('CHANGE_TYPE'), 'REPORTED_REASON_CODE', digits=True)
    reason, name = _text(row.get('EXPLANATION'), 'REPORTED_REASON'), _text(row.get('OPERATEDEPT_NAME'), 'DEPARTMENT_NAME')
    code = row.get('OPERATEDEPT_CODE')
    if code not in (None, ''):
        _text(code, 'DEPARTMENT_CODE', digits=True)
    valid_code = code is not None and code != '' and any(character != '0' for character in code)
    old_code = row.get('OPERATEDEPT_CODE_OLD')
    if old_code not in (None, ''):
        _text(old_code, 'OLD_DEPARTMENT_CODE', digits=True)
    values = {key: _exact(row.get(key)) for key in ('BUY', 'SELL', 'NET', 'ACCUM_AMOUNT', 'ACCUM_VOLUME')}
    if any(values[key] < 0 for key in ('BUY', 'SELL', 'ACCUM_AMOUNT', 'ACCUM_VOLUME')):
        raise ValueError('LHB_NEGATIVE_REPORTED_TRANSACTION_VALUE')
    if values['NET'] != values['BUY'] - values['SELL']:
        raise ValueError('LHB_REPORTED_NET_ARITHMETIC_CONFLICT')
    is_channel = name in ('深股通专用', '沪股通专用')
    is_group = is_channel or name == '机构专用'
    identity = ('PROVIDER_CHANNEL_IDENTIFIER' if is_channel else 'PROVIDER_GROUP_IDENTIFIER' if is_group
                else 'PROVIDER_DEPARTMENT_IDENTIFIER') if valid_code else ('UNRESOLVED_GROUP_LABEL' if is_group else 'UNRESOLVED_VENUE_LABEL')
    facts = {'instrument_id': instrument_id, 'business_session': business_day,
             'reported_disclosure_id': disclosure, 'reported_reason_code': reason_code,
             'reported_reason': reason, 'reported_department_name': name,
             'provider_department_code': code if valid_code else None,
             'raw_department_code': code, 'raw_old_department_code': old_code,
             'old_code_is_not_dated_alias_authority': True,
             'source_entity_id': 'EASTMONEY_DEPARTMENT:' + code if valid_code else None,
             'identity_status': identity, 'natural_person_identity': 'UNKNOWN',
             'reported_buy_amount_exact': _encoded(values['BUY']),
             'reported_sell_amount_exact': _encoded(values['SELL']),
             'reported_net_amount_exact': _encoded(values['NET']),
             'reported_security_window_amount_exact': _encoded(values['ACCUM_AMOUNT']),
             'reported_security_window_volume_exact': _encoded(values['ACCUM_VOLUME']),
             'currency': None, 'amount_unit': None, 'measurement_definition': None,
             'amount_definition_status': 'UNKNOWN', 'window_start': None, 'window_end': None,
             'window_status': 'UNKNOWN', 'independent_disclosure_published_at': None,
             'business_midnight_is_not_publication_clock': True,
             'reported_security_amount_is_not_per_seat_amount': True,
             'eligible_for_funds_scoring': False}
    appearance = {'side': side, 'receipt_id': receipt['receipt_id'], 'raw_sha256': raw_info['sha256'],
                  'row_index': index, 'field_path': 'result.data[' + str(index) + ']',
                  'first_seen_at': receipt['first_seen_at'], 'ingested_at': receipt['ingested_at']}
    appearance['appearance_id'] = digest_json_stream(appearance)
    return facts, appearance


def parse_archived_lhb_seats(source, *, instrument_id, business_session, cutoff):
    if not isinstance(source, ArchivedLhbSeatSource):
        raise ValueError('LHB_ACTUAL_ARCHIVED_ORIGINALS_CAPABILITY_REQUIRED')
    if not isinstance(instrument_id, str) or re.fullmatch(r'[0-9]{6}\.(SH|SZ)', instrument_id) is None:
        raise ValueError('LHB_INSTRUMENT_ID_UNRECOGNIZED')
    day, decision = date.fromisoformat(str(business_session)).isoformat(), _stamp(cutoff)
    if source.instrument_id != instrument_id or source.business_session != day:
        raise ValueError('LHB_ANCHORED_CAPTURE_SCOPE_MISMATCH')
    normalized, upstream, reasons = {}, {}, set()
    for receipt, payload, raw_info, _ in source.original_receipts():
        side = _side(receipt, instrument_id, day)
        if max(_stamp(receipt['first_seen_at']), _stamp(receipt['ingested_at'])) > decision:
            reasons.add('ACTUAL_CAPTURE_AFTER_CONTEXT_CUTOFF')
            continue
        if receipt.get('status') != 'COMPLETE':
            reasons.add('SIDE_CAPTURE_UNAVAILABLE:' + side)
            continue
        target = normalized if receipt['provider'] == 'AKShare' else upstream
        if side in target:
            raise ValueError('LHB_MULTIPLE_SAME_SIDE_SOURCE_REVISIONS')
        target[side] = (receipt, payload, raw_info)
    groups, appearances = {}, 0
    available = []
    for side in SIDES:
        if side not in upstream or side not in normalized:
            reasons.add('SIDE_CANONICAL_OR_NORMALIZED_SOURCE_UNAVAILABLE:' + side)
            continue
        receipt, payload, raw_info = upstream[side]
        legacy_receipt, legacy, _ = normalized[side]
        if max(_stamp(receipt['first_seen_at']), _stamp(receipt['ingested_at'])) > _stamp(legacy_receipt['first_seen_at']):
            raise ValueError('LHB_UPSTREAM_NORMALIZATION_CLOCK_ORDER_INVALID')
        if receipt.get('field_version') != 'JSON_UPSTREAM_RESPONSE' or receipt.get('source_url') != SOURCE_URL:
            raise ValueError('LHB_UPSTREAM_RESPONSE_DEFINITION_UNRECOGNIZED')
        result = payload.get('result') if isinstance(payload, dict) else None
        if (not isinstance(payload, dict) or payload.get('success') is not True
                or type(payload.get('code')) is not int or payload['code'] != 0
                or not isinstance(result, dict) or not isinstance(result.get('data'), list)):
            raise ValueError('LHB_UPSTREAM_RESPONSE_ENVELOPE_INVALID')
        rows = result['data']
        if (type(result.get('pages')) is not int or result['pages'] != 1
                or type(result.get('count')) is not int or result['count'] != len(rows)):
            reasons.add('PROVIDER_FIRST_PAGE_LIST_COVERAGE_UNVERIFIED:' + side)
            continue
        if (legacy_receipt.get('field_version') != '1.18.94'
                or legacy_receipt.get('pagination_complete') is not True
                or type(legacy_receipt.get('row_count')) is not int
                or legacy_receipt['row_count'] != len(rows) or not isinstance(legacy, list)
                or len(legacy) != len(rows)):
            raise ValueError('LHB_NORMALIZED_LIST_SCOPE_CONFLICT')
        parsed = [_row(row, instrument_id, day, side, receipt, raw_info, index)
                  for index, row in enumerate(rows)]
        def canonical_projection(pair):
            fact, _ = pair
            return (fact['reported_department_name'], fact['reported_reason'],
                    *[Fraction(fact[key]['numerator'], fact[key]['denominator']) for key in
                      ('reported_buy_amount_exact', 'reported_sell_amount_exact', 'reported_net_amount_exact')])
        def legacy_projection(row):
            if not isinstance(row, dict):
                raise ValueError('LHB_SDK_PROJECTION_ROW_INVALID')
            return (_text(row.get('交易营业部名称'), 'SDK_DEPARTMENT_NAME'),
                    _text(row.get('类型'), 'SDK_REASON'),
                    *[_exact(row.get(key)) for key in ('买入金额', '卖出金额', '净额')])
        projected = [legacy_projection(row) for row in legacy]
        if sorted(projected) != sorted(canonical_projection(pair) for pair in parsed):
            raise ValueError('LHB_SDK_PROJECTION_CONFLICT_WITH_ORIGINAL')
        available.append(side)
        for facts, appearance in parsed:
            appearances += 1
            key = (instrument_id, day, facts['reported_disclosure_id'], facts['reported_reason_code'],
                   facts['provider_department_code']) if facts['source_entity_id'] is not None else ('UNRESOLVED_APPEARANCE', appearance['appearance_id'])
            if key in groups:
                if groups[key]['facts'] != facts:
                    raise ValueError('LHB_SAME_PROVIDER_ID_DISCLOSURE_FACT_CONFLICT')
                groups[key]['appearances'].append(appearance)
            else:
                groups[key] = {'facts': facts, 'appearances': [appearance]}
    rows = [{**item['facts'], 'appearance_count': len(item['appearances']),
             'appearances': item['appearances'],
             'source_refs': sorted({p['receipt_id'] for p in item['appearances']}),
             'appearance_group_id': digest_json_stream({'facts': item['facts'], 'appearances': item['appearances']})}
            for item in groups.values()]
    rows.sort(key=lambda row: (row['reported_disclosure_id'], row['reported_reason_code'],
                               row['provider_department_code'] or '', row['appearance_group_id']))
    reasons.update(('AMOUNT_CURRENCY_UNIT_AND_MEASUREMENT_UNVERIFIED', 'DISCLOSURE_WINDOW_UNVERIFIED',
                    'INDEPENDENT_DISCLOSURE_PUBLICATION_CLOCK_UNKNOWN'))
    if not rows:
        reasons.add('EMPTY_OR_UNAVAILABLE_NOT_EVIDENCE_OF_NO_PARTICIPANT')
    result = _SeatEvidence({**source_identity(), 'instrument_id': instrument_id,
        'business_session': day, 'context_cutoff': decision.isoformat(),
        'availability': 'AVAILABLE' if rows and len(available) == 2 else 'UNKNOWN',
        'decision_readiness': 'UNKNOWN', 'data_role': 'REPORTED_PROVIDER_SEAT_IDENTITIES_RESEARCH_CONTEXT',
        'source_audit': source.audit(), 'rows': rows, 'available_appearance_lists': available,
        'appearance_count': appearances, 'appearance_group_count': len(rows),
        'known_provider_identifier_count': len({row['source_entity_id'] for row in rows if row['source_entity_id']}),
        'unresolved_identity_appearance_count': sum(row['appearance_count'] for row in rows if row['source_entity_id'] is None),
        'collapsed_provider_id_appearance_count': appearances - len(rows),
        'appearance_lists_are_not_distinct_people_or_all_trader_universe': True,
        'page_coverage_role': 'PROVIDER_RESPONSE_FIRST_LIST_SELF_REPORTED_PAGE_COUNT_CONSISTENT_ONLY',
        'same_disclosure_appearance_grouping_is_not_verified_window_funds_dedup': True,
        'amounts_summed_for_scoring': False, 'securities_window_amounts_summed': False,
        'currency': None, 'amount_unit': None, 'window_status': 'UNKNOWN',
        'independent_disclosure_published_at': None, 'excluded_feature_fields': list(EXCLUDED_FIELDS),
        'reason_codes': sorted(reasons), 'formal_focus': False, 'funds_score_authority': False,
        'official_pit_validation_count': 0, 'authenticated_emotion_cycle_count': 0,
        'market_edge': 'NOT_ESTABLISHED', 'selection_changed': False,
        'native_context_binding_status': 'NOT_BOUND'})
    result._source = source
    result._hash = digest_json_stream(result)
    return result


def _validator(function, *args, **kwargs):
    if not inspect.ismethod(function) or function.__self__ is None:
        raise ValueError('LHB_NATIVE_BOUND_VALIDATOR_REQUIRED')
    if function(*args, **kwargs) is not None:
        raise ValueError('LHB_NATIVE_VALIDATOR_MUST_VALIDATE_OR_RAISE')


def bind_lhb_seat_context(evidence, *, bundle, native_parent, selected_ids, cutoff,
                          _parent_validator, _selection_validator):
    """Explain one frozen selected stock at a new explicit context cutoff.

    The caller owns genuine native archive capabilities. Bound-method shape is
    an interface requirement, not a defence against malicious Python code.
    """
    if not isinstance(evidence, _SeatEvidence) or digest_json_stream(evidence) != evidence._hash:
        raise ValueError('LHB_NATIVE_PARSER_CAPABILITY_NOT_INTACT')
    if evidence.get('source_hash') != source_identity()['source_hash']:
        raise ValueError('LHB_PARSER_IMPLEMENTATION_CHANGED')
    evidence._source.validate_unchanged()
    decision = _stamp(cutoff)
    if _stamp(evidence['context_cutoff']) != decision:
        raise ValueError('LHB_NEW_CONTEXT_CUTOFF_MISMATCH')
    if (not isinstance(native_parent, dict)
            or native_parent.get('source_input_hash') != digest_market_input(bundle)
            or str(native_parent.get('observation_date')) != str(bundle.observation_date)
            or native_parent.get('formal_focus') is not False
            or native_parent.get('official_pit_validation_countable') is not False):
        raise ValueError('LHB_NATIVE_PARENT_INPUT_OR_SAFETY_MISMATCH')
    if ((evidence['source_audit']['coverage_type'] == 'TEST_FIXTURE')
            != (bundle.coverage_type == 'TEST_FIXTURE')):
        raise ValueError('LHB_FIXTURE_AND_REAL_PARENT_MUST_NOT_MIX')
    base_cutoff = _stamp(native_parent['decision_cutoff'])
    if decision < base_cutoff:
        raise ValueError('LHB_CONTEXT_CUTOFF_BEFORE_FROZEN_PARENT')
    selected = tuple(selected_ids)
    if any(not isinstance(iid, str) for iid in selected) or len(set(selected)) != len(selected):
        raise ValueError('LHB_FROZEN_SELECTION_SCOPE_INVALID')
    _validator(_parent_validator, native_parent, bundle=bundle, cutoff=base_cutoff)
    _validator(_selection_validator, selected, bundle=bundle, cutoff=base_cutoff)
    if evidence['instrument_id'] not in selected:
        raise ValueError('LHB_SECURITY_NOT_IN_FROZEN_SELECTION')
    if evidence['business_session'] != str(bundle.observation_date):
        raise ValueError('LHB_OBSERVATION_DATE_NOT_FROZEN_SCOPE')
    _validator(_parent_validator, native_parent, bundle=bundle, cutoff=base_cutoff)
    output = {**deepcopy(dict(evidence)), 'native_context_binding_status': 'VERIFIED_NATIVE_PARENT_AND_FROZEN_SELECTION',
        'source_input_hash': digest_market_input(bundle),
        'native_parent_object_hash': digest_json_stream(native_parent),
        'native_parent_context_hash': native_parent.get('context_content_hash'),
        'base_decision_cutoff': base_cutoff.isoformat(), 'new_context_cutoff': decision.isoformat(),
        'coverage_type': bundle.coverage_type,
        'selected_scope_hash': digest_json_stream(list(selected)), 'frozen_selected_count': len(selected),
        'attachment_stock_id': evidence['instrument_id'], 'source_role': 'NEW_CAPTURED_RESEARCH_ATTACHMENT',
        'original_parent_and_selection_modified': False, 'source_database_reverified': False,
        'late_context_is_not_historical_pit': True, 'default_selector_or_fsm_invoked': False}
    output['context_content_hash'] = digest_json_stream(output)
    return output
