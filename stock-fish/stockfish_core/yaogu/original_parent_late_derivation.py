"""Explicit late derivation over originals already visible at an earlier cutoff.

This producer authenticates archived captures again. It does not recover a
capability from a proof JSON, alter receipts, certify historical membership, or
relax the ordinary EvidenceResolver. Normalization arithmetic is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from collections import Counter, defaultdict
from itertools import islice
import hashlib
import json
from pathlib import Path
import re
from time import monotonic

from .contracts import DailyBar, MarketInput, digest
from .historical_replay import verify_capture
from .history_artifacts import digest_market_input
from .provider_normalization import normalize_capture_with_report, TZ, _iid
from .provider_requests import FIELD_VERSION
from .visibility import clock_visible, stamp

VERSION = 'ORIGINAL_PARENT_ASOF_LATE_DERIVATION_V1'
PUBLICATION_ROLE = 'PARENT_VISIBILITY_BOUND_NOT_INDEPENDENT_PUBLICATION'
CLOCKS = ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')
ENDPOINTS = frozenset({'mainboard_sse', 'master_szse', 'calendar', 'master_all',
    'market_daily', 'history', 'history_adjusted', 'adjustment_factors',
    'delisting_sse', 'stock_basic', 'stock_basic_all'})


@dataclass(frozen=True)
class OriginalCaptureArtifact:
    """Paths and expected byte hash are locators, not source authority."""
    archive_root: Path
    result_path: Path
    result_sha256: str


@dataclass(frozen=True)
class DerivationLimits:
    max_captures: int = 128
    per_file_bytes: int = 8 * 1024**2
    total_source_bytes: int = 256 * 1024**2
    budget_seconds: float = 60

    def validate(self):
        import math
        for value, maximum in ((self.max_captures, 128), (self.per_file_bytes, 256*1024**2),
                               (self.total_source_bytes, 2*1024**3)):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
                raise ValueError('LATE_DERIVATION_INVALID_SIZE_BUDGET')
        if (isinstance(self.budget_seconds, bool) or not isinstance(self.budget_seconds, (int, float))
                or not math.isfinite(self.budget_seconds) or not 0 < self.budget_seconds <= 7200):
            raise ValueError('LATE_DERIVATION_INVALID_TIME_BUDGET')


def source_identity():
    root = Path(__file__).resolve().parents[2]
    paths = (Path(__file__), Path(__file__).with_name('provider_normalization.py'),
        Path(__file__).with_name('provider_comparability.py'), Path(__file__).with_name('contracts.py'),
        Path(__file__).with_name('visibility.py'), Path(__file__).with_name('historical_replay.py'),
        Path(__file__).with_name('history_artifacts.py'), Path(__file__).with_name('provider_requests.py'),
        Path(__file__).with_name('provider_requests_worker.py'), Path(__file__).with_name('provider_budget.py'),
        Path(__file__).with_name('rules.py'), Path(__file__).with_name('provider_funds.py'),
        Path(__file__).with_name('e2e_tracing.py'),
        root / 'docs' / (VERSION + '.md'))
    hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    return {'producer_version': VERSION, 'source_file_hashes': hashes, 'source_hash': digest(hashes),
            'publication_clock_role': PUBLICATION_ROLE, 'MARKET_EDGE': 'NOT_ESTABLISHED',
            'default_integration': 'NOT_CONNECTED'}


def _day(value):
    text = str(value)
    if re.fullmatch(r'\d{8}', text):
        text = text[:4] + '-' + text[4:6] + '-' + text[6:]
    parsed = date.fromisoformat(text)
    if text != parsed.isoformat():
        raise ValueError('LATE_DERIVATION_EXACT_BUSINESS_DATE_REQUIRED')
    return parsed


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('LATE_DERIVATION_DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))


def _clock_dict(row):
    return {key: getattr(row, key).isoformat() for key in CLOCKS}


def _raw_signature(row):
    """Only fields consumed by the unchanged BaoStock RAW normalizer.

    Decimal formatting and extra provider metadata cannot invent a revision.
    Flags retain literal semantics (``'1.0'`` is not ``'1'``). Invalid values
    remain distinct evidence; they are not silently equated to missing data.
    """
    values = []
    for field in ('open', 'high', 'low', 'close', 'preclose', 'volume', 'amount', 'turn'):
        if field not in row:
            values.append((field, 'MISSING'))
            continue
        try:
            value = Decimal(str(row[field]))
            if value.is_finite():
                values.append((field, 'EXACT', Fraction(value)))
                continue
        except (InvalidOperation, ValueError, TypeError):
            pass
        values.append((field, 'INVALID', digest(row[field])))
    values.extend((field, str(row.get(field))) for field in ('adjustflag', 'tradestatus', 'isST'))
    return tuple(values)


def _capture_clock_audit(result, payload, receipt, cutoff):
    seen, ingested = stamp(receipt['first_seen_at']), stamp(receipt['ingested_at'])
    if seen > ingested:
        raise ValueError('LATE_DERIVATION_PARENT_CLOCK_ORDER_INVALID')
    for name, obj in (('RAW', payload), ('RESULT', result)):
        if 'first_seen_at' in obj and stamp(obj['first_seen_at']) != seen:
            raise ValueError('LATE_DERIVATION_'+name+'_FIRST_SEEN_CONFLICT')
    if 'started_at' in result and stamp(result['started_at']) > seen:
        raise ValueError('LATE_DERIVATION_CAPTURE_START_AFTER_SEEN')
    if 'completed_at' in result:
        completed = stamp(result['completed_at'])
        if ingested > completed:
            raise ValueError('LATE_DERIVATION_CAPTURE_COMPLETE_BEFORE_INGESTED')
        if completed > cutoff:
            raise ValueError('LATE_DERIVATION_CAPTURE_COMPLETE_AFTER_CUTOFF')
    return {'raw_first_seen_status': ('OBSERVED_RAW_RECEIPT_CONSISTENT' if 'first_seen_at' in payload
                                      else 'UNKNOWN_MISSING_RAW_FIRST_SEEN_AT'),
            'result_first_seen_present': 'first_seen_at' in result,
            'started_at': result.get('started_at'), 'completed_at': result.get('completed_at'),
            'receipt_first_seen_at': receipt['first_seen_at'], 'receipt_ingested_at': receipt['ingested_at'],
            'clock_role': 'ORIGINAL_ARCHIVE_CLOCK_NOT_VENDOR_PUBLICATION_PROOF'}


def _factor_checkpoint_ancestry(results, checkpoint):
    """Require originals for explicitly inherited per-security checkpoint data.

    This source guard neither rewrites rows/statuses nor grants lineage
    capability. All authenticated ancestors stay in the normalizer arguments.
    The original comparator/resolver decide numeric availability unchanged.
    """
    factors, inherited = [], []
    for result in results:
        if result['endpoint'] != 'adjustment_factors' or result['status'] not in {'COMPLETE', 'PARTIAL'}:
            continue
        checkpoint()
        groups = defaultdict(list)
        for row in result['rows']:
            groups[row.get('code')].append(row)
        statuses = {}
        for value in result.get('coverage', {}).get('security_statuses', []):
            code = value.get('code')
            if code in statuses:
                raise ValueError('LATE_DERIVATION_FACTOR_SECURITY_STATUS_DUPLICATE')
            statuses[code] = value
        factors.append((result, groups, statuses))
    for result, groups, statuses in factors:
        for code, status in statuses.items():
            checkpoint()
            if status.get('status') != 'COMPLETE' or 'captured_at' not in status:
                continue
            captured = stamp(status['captured_at'])
            receipt = next(value for value in result['receipts']
                           if value['receipt_id'] == result['request_receipt_id'])
            if captured > stamp(receipt['first_seen_at']):
                raise ValueError('LATE_DERIVATION_FACTOR_FRAGMENT_AFTER_ARCHIVE')
            if 'started_at' not in result:
                raise ValueError('LATE_DERIVATION_FACTOR_FRAGMENT_START_UNKNOWN')
            if captured >= stamp(result['started_at']):
                continue
            if (status.get('cursor_exhausted') is not True or type(status.get('row_count')) is not int
                    or status['row_count'] != len(groups[code])):
                raise ValueError('LATE_DERIVATION_INHERITED_FACTOR_FRAGMENT_SCOPE_UNVERIFIED')
            multiset = Counter(digest(row) for row in groups[code])
            ancestors = []
            for prior, prior_groups, prior_statuses in factors:
                prior_receipt = next(value for value in prior['receipts']
                                     if value['receipt_id'] == prior['request_receipt_id'])
                old = prior_statuses.get(code)
                if (prior['request_receipt_id'] == result['request_receipt_id'] or old is None
                        or prior['parameters'] != result['parameters'] or prior['provider'] != result['provider']
                        or old != status or 'completed_at' not in prior or 'started_at' not in prior):
                    continue
                if (stamp(prior['started_at']) <= captured <= stamp(prior_receipt['first_seen_at'])
                        <= stamp(prior['completed_at']) <= stamp(result['started_at'])
                        and Counter(digest(row) for row in prior_groups[code]) == multiset):
                    ancestors.append(prior['request_receipt_id'])
            if not ancestors:
                raise ValueError('LATE_DERIVATION_INHERITED_FACTOR_ORIGINAL_ANCESTOR_REQUIRED')
            inherited.append({'receipt_id': result['request_receipt_id'], 'code': code,
                'captured_at': status['captured_at'], 'ancestor_receipt_ids': sorted(ancestors),
                'row_multiset_hash': digest(sorted(multiset.items())), 'row_count': len(groups[code]),
                'role': 'EXPLICIT_CHECKPOINT_FRAGMENT_ORIGINALS_PRESENT_NOT_NEW_SOURCE_CAPABILITY',
                'ancestor_statuses': {prior['request_receipt_id']: prior['status'] for prior, _, _ in factors
                                      if prior['request_receipt_id'] in ancestors}})
    return inherited


def normalize_original_parent_asof(artifacts, observation_date, cutoff, *, limits=None, progress=None):
    """Return a new input and audit, using the actual clock of this invocation.

    A complete original calendar supplies the input axis. All supplied primary
    captures, including failed attempts, must precede the earlier cutoff. The
    caller must separately authenticate/bind any immutable history and exact
    comparable basis; this API never opens/writes a business database.
    """
    limits = limits or DerivationLimits()
    limits.validate()
    started = monotonic()
    identity = source_identity()
    day, decision = _day(observation_date), stamp(cutoff)
    wall_start = datetime.now(timezone.utc)
    if decision >= wall_start or day > decision.astimezone(TZ).date():
        raise ValueError('LATE_DERIVATION_EARLIER_CUTOFF_REQUIRED')
    artifacts = tuple(islice(artifacts, limits.max_captures+1))
    if not artifacts or len(artifacts) > limits.max_captures:
        raise ValueError('LATE_DERIVATION_CAPTURE_BUDGET_EXCEEDED')
    source_files, results, checks, ids, calendars = {}, [], [], {}, []
    raw_observations, repeated_raw = {}, []

    def checkpoint(*args, **kwargs):
        if monotonic() - started > limits.budget_seconds:
            raise TimeoutError('LATE_DERIVATION_TIME_BUDGET_EXCEEDED')
        if progress is not None and (args or kwargs):
            progress(*args, **kwargs)

    def checked_bytes(path):
        checkpoint()
        path = Path(path).resolve(strict=True)
        st = path.stat()
        if st.st_size > limits.per_file_bytes:
            raise ValueError('LATE_DERIVATION_FILE_BYTE_BUDGET_EXCEEDED')
        raw = path.read_bytes()
        record = {'path': str(path), 'size': st.st_size, 'mtime_ns': st.st_mtime_ns,
                  'inode': st.st_ino, 'device': st.st_dev, 'sha256': hashlib.sha256(raw).hexdigest()}
        after = path.stat()
        if (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev) != (
                st.st_size, st.st_mtime_ns, st.st_ino, st.st_dev):
            raise ValueError('LATE_DERIVATION_SOURCE_CHANGED_DURING_READ')
        previous = source_files.get(str(path))
        if previous is not None and previous != record:
            raise ValueError('LATE_DERIVATION_SOURCE_CHANGED')
        source_files[str(path)] = record
        if sum(row['size'] for row in source_files.values()) > limits.total_source_bytes:
            raise ValueError('LATE_DERIVATION_TOTAL_SOURCE_BYTE_BUDGET_EXCEEDED')
        return raw

    for artifact in artifacts:
        if not isinstance(artifact, OriginalCaptureArtifact) or not re.fullmatch('[0-9a-f]{64}', artifact.result_sha256):
            raise ValueError('LATE_DERIVATION_ORIGINAL_CAPTURE_LOCATOR_REQUIRED')
        raw_result = checked_bytes(artifact.result_path)
        if hashlib.sha256(raw_result).hexdigest() != artifact.result_sha256:
            raise ValueError('LATE_DERIVATION_RESULT_BYTE_HASH_MISMATCH')
        result = _json(raw_result)
        if not isinstance(result, dict) or result.get('endpoint') not in ENDPOINTS:
            raise ValueError('LATE_DERIVATION_UNSUPPORTED_ENDPOINT')
        if type(result.get('pagination_complete')) is not bool:
            raise ValueError('LATE_DERIVATION_PAGINATION_BOOLEAN_REQUIRED')
        rid = result['request_receipt_id']
        root = Path(artifact.archive_root).resolve(strict=True)
        candidates = [root/'cache'/'raw'/(rid+'.receipt.json')]
        candidates.extend(sorted((root/'raw').glob('**/raw/'+rid+'.receipt.json')))
        receipt_path = next((path for path in candidates if path.is_file()), None)
        if receipt_path is None:
            raise FileNotFoundError('ORIGINAL_RECEIPT_ARCHIVE_MISSING:' + rid)
        original = _json(checked_bytes(receipt_path))
        payload_path = receipt_path.parent/(original['content_hash']+'.raw.json')
        payload = _json(checked_bytes(payload_path))
        verified = verify_capture(root, result)
        if (Path(verified['receipt_path']) != receipt_path.resolve() or
                Path(verified['raw_path']) != payload_path.resolve()):
            raise ValueError('LATE_DERIVATION_VERIFIER_PATH_MISMATCH')
        negative = (result.get('status') == 'FETCH_FAILED' and result.get('rows') == []
                    and result.get('pagination_complete') is False
                    and isinstance(payload.get('error_code'), str) and bool(payload['error_code'])
                    and payload['error_code'] == result.get('error_code'))
        field_version_defaulted = negative and 'field_version' not in payload
        expected_version = FIELD_VERSION if field_version_defaulted else payload.get('field_version')
        if not expected_version or expected_version != result.get('field_version') or expected_version != original['field_version']:
            raise ValueError('LATE_DERIVATION_ORIGINAL_FIELD_VERSION_MISMATCH')
        if stamp(original['first_seen_at']) > decision or stamp(original['ingested_at']) > decision:
            raise ValueError('LATE_DERIVATION_PARENT_CAPTURE_AFTER_CUTOFF')
        capture_clock_audit = _capture_clock_audit(result, payload, original, decision)
        if rid in ids and ids[rid] != digest(result):
            raise ValueError('LATE_DERIVATION_RECEIPT_CONFLICT')
        if rid in ids:
            raise ValueError('LATE_DERIVATION_DUPLICATE_CAPTURE')
        ids[rid] = digest(result)
        parameters = result['parameters']
        for key in ('date', 'trade_date', 'end_date'):
            if parameters.get(key) and _day(parameters[key]) > day:
                raise ValueError('LATE_DERIVATION_SOURCE_BUSINESS_SCOPE_AFTER_OBSERVATION')
        if result['endpoint'] in {'market_daily', 'history', 'history_adjusted'}:
            in_parent = set()
            for row in result['rows']:
                checkpoint()
                session = _day(row['date'])
                if (session > day or (parameters.get('date') and session != _day(parameters['date'])) or
                        (parameters.get('start_date') and session < _day(parameters['start_date'])) or
                        (parameters.get('end_date') and session > _day(parameters['end_date']))):
                    raise ValueError('LATE_DERIVATION_RAW_ROW_BUSINESS_SCOPE_MISMATCH')
                expected_flag = '1' if result['endpoint'] == 'history_adjusted' else '3'
                if str(row.get('adjustflag')) != expected_flag:
                    raise ValueError('LATE_DERIVATION_RAW_ADJUSTMENT_FLAG_MISMATCH')
                if parameters.get('codes') is not None and row.get('code') not in parameters['codes']:
                    raise ValueError('LATE_DERIVATION_RAW_SECURITY_SCOPE_MISMATCH')
                iid = _iid(row.get('code'))
                if iid is not None:
                    key = (expected_flag, iid, session)
                    if key in in_parent:
                        raise ValueError('LATE_DERIVATION_RAW_DUPLICATE_WITHIN_PARENT')
                    in_parent.add(key)
                    signature = _raw_signature(row)
                    prior = raw_observations.get(key)
                    if prior is not None and prior[0] != signature:
                        raise ValueError('LATE_DERIVATION_RAW_BUSINESS_REVISION_CONFLICT')
                    if prior is not None:
                        repeated_raw.append({'instrument_id': iid, 'session': str(session),
                            'adjustflag': expected_flag, 'source_refs': [prior[1], rid],
                            'role': 'SAME_NORMALIZER_BUSINESS_VALUES_NOT_EXTRA_SOURCE_AUTHORITY'})
                    raw_observations[key] = (signature, rid)
        if result['endpoint'] == 'adjustment_factors':
            for row in result['rows']:
                if row.get('dividOperateDate') and _day(row['dividOperateDate']) > day:
                    raise ValueError('LATE_DERIVATION_FACTOR_EVENT_AFTER_OBSERVATION')
        if result['endpoint'] == 'calendar' and result['status'] == 'COMPLETE' and result['pagination_complete']:
            if result['provider'].lower() != 'baostock':
                raise ValueError('LATE_DERIVATION_CALENDAR_PROVIDER_UNRECOGNIZED')
            first, last = _day(parameters['start_date']), _day(parameters['end_date'])
            days = {}
            for row in result['rows']:
                session, flag = _day(row['calendar_date']), row['is_trading_day']
                if session in days or flag not in ('0', '1'):
                    raise ValueError('LATE_DERIVATION_CALENDAR_ROWS_INVALID')
                days[session] = flag
            expected = {first+timedelta(days=i) for i in range((last-first).days+1)}
            if first > last or set(days) != expected:
                raise ValueError('LATE_DERIVATION_CALENDAR_FULL_DATE_SCOPE_REQUIRED')
            calendars.append((rid, days))
        results.append(result)
        checks.append({'receipt_id': rid, 'endpoint': result['endpoint'], 'provider': result['provider'],
            'result_path': str(Path(artifact.result_path).resolve()), 'result_byte_sha256': artifact.result_sha256,
            'canonical_result_hash': digest(result), 'archive_integrity': verified,
            'original_status': result['status'], 'original_pagination_complete': result['pagination_complete'],
            'original_first_seen_at': original['first_seen_at'], 'original_ingested_at': original['ingested_at'],
            'capture_clock_audit': capture_clock_audit,
            'failed_empty_schema_label_defaulted': field_version_defaulted,
            'schema_default_role': 'NEGATIVE_ENVELOPE_METADATA_NOT_NUMERIC_AUTHORITY' if field_version_defaulted else None})
    factor_ancestry = _factor_checkpoint_ancestry(results, checkpoint)
    if not calendars:
        raise ValueError('LATE_DERIVATION_COMPLETE_ORIGINAL_CALENDAR_REQUIRED')
    days = {}
    for _, incoming in calendars:
        for session, flag in incoming.items():
            if session in days and days[session] != flag:
                raise ValueError('LATE_DERIVATION_CALENDAR_CONFLICT')
            days[session] = flag
    if set(days) != {min(days)+timedelta(days=i) for i in range((max(days)-min(days)).days+1)}:
        raise ValueError('LATE_DERIVATION_CALENDAR_DEPENDENCY_GAP')
    if days.get(day) != '1':
        raise ValueError('LATE_DERIVATION_OBSERVATION_NOT_VERIFIED_TRADING_SESSION')
    sessions = tuple(sorted(session for session, flag in days.items() if flag == '1' and session <= day))
    first_seen = datetime.now(timezone.utc)
    baseline, normalization = normalize_capture_with_report(results, day, first_seen, sessions,
        history_store=None, coverage_type='RECONSTRUCTED_HISTORICAL', progress=checkpoint)
    checkpoint()
    receipts = {row.receipt_id: row for row in baseline.receipts}
    for parent in receipts.values():
        if not clock_visible(parent, decision):
            raise ValueError('LATE_DERIVATION_PARENT_EVENT_OR_PUBLICATION_AFTER_CUTOFF')
    if baseline.history_artifact_ref or baseline.themes or baseline.funds:
        raise ValueError('LATE_DERIVATION_UNEXPECTED_HISTORY_OR_CONTEXT')
    ingested = datetime.now(timezone.utc)
    row_audit = []

    def derive(row, kind, replacement_securities=None):
        refs = tuple(row.source_refs)
        if not refs or any(ref not in receipts for ref in refs):
            raise ValueError('LATE_DERIVATION_PARENT_REFERENCE_REQUIRED')
        parents = [receipts[ref] for ref in refs]
        if isinstance(row, DailyBar) and row.event_time != datetime.combine(row.session, time(15), TZ):
            raise ValueError('LATE_DERIVATION_RAW_BUSINESS_EVENT_UNVERIFIED')
        event = max(parent.event_time for parent in parents) if kind == 'UNIVERSE' else row.event_time
        publication = max(parent.source_published_at for parent in parents)
        if event > decision or publication > decision:
            raise ValueError('LATE_DERIVATION_DERIVED_EVENT_OR_PUBLICATION_AFTER_CUTOFF')
        if first_seen < max(parent.first_seen_at for parent in parents) or ingested < max(parent.ingested_at for parent in parents):
            raise ValueError('LATE_DERIVATION_ACTUAL_CLOCK_PRECEDES_PARENT')
        value = row.model_dump(mode='python')
        value.update(event_time=event, source_published_at=publication, first_seen_at=first_seen, ingested_at=ingested)
        if replacement_securities is not None:
            value['securities'] = replacement_securities
        derived = type(row).model_validate(value)
        if row.model_dump(exclude=set(CLOCKS)|({'securities'} if kind == 'UNIVERSE' else set())) != derived.model_dump(
                exclude=set(CLOCKS)|({'securities'} if kind == 'UNIVERSE' else set())):
            raise ValueError('LATE_DERIVATION_FACT_OR_AUTHORITY_CHANGED')
        def refs_for(*datasets):
            return [parent.receipt_id for parent in parents if parent.dataset in datasets]
        price = refs_for('market_daily', 'history', 'tushare_daily')
        directory = refs_for('mainboard_sse', 'master_szse', 'stock_basic', 'stock_basic_all')
        field_roles = {}
        if kind == 'BAR':
            for field in ('open', 'high', 'low', 'close', 'reference_prev_close', 'volume_shares', 'amount_cny'):
                field_roles[field] = {'source_refs': price, 'role': 'UNCHANGED_NORMALIZER_RAW_VALUE'}
            for field in ('comparable_open', 'comparable_high', 'comparable_low', 'comparable_close', 'comparable_basis'):
                field_roles[field] = {'source_refs': list(refs), 'role': 'UNCHANGED_NORMALIZER_COMPARABILITY_WITH_ALL_ORIGINAL_DEPENDENCIES'}
            for field in ('turnover_fraction', 'turnover_basis'):
                field_roles[field] = {'source_refs': price+refs_for('tushare_daily_basic'), 'role': 'UNCHANGED_NORMALIZER_DATED_TURNOVER'}
            for field in ('limit_up', 'limit_down', 'limit_applicable', 'limit_verified', 'rule_id'):
                field_roles[field] = {'source_refs': list(refs), 'role': 'UNCHANGED_NORMALIZER_LIMIT_VALUE_OR_UNKNOWN_NO_NEW_AUTHORITY'}
        elif kind == 'SECURITY':
            for field in ('instrument_id', 'exchange', 'board', 'security_type', 'name', 'listed_on'):
                field_roles[field] = {'source_refs': directory, 'role': 'CURRENT_ROSTER_OR_ORIGINAL_IPO_NOT_HISTORICAL_BOARD_REGISTRY'}
            for field in ('st', 'suspended'):
                field_roles[field] = {'source_refs': price+refs_for('master_all'), 'role': 'SOURCE_DATED_STATE_OR_UNCHANGED_UNKNOWN'}
            field_roles['delisting_period'] = {'source_refs': directory+refs_for('delisting_sse'),
                'role': 'UNCHANGED_CURRENT_EXCLUSION_OR_NAME_RULE_NOT_BACKFILLED_HISTORY'}
            for field in ('effective_from', 'effective_to'):
                field_roles[field] = {'source_refs': list(refs), 'role': 'UNCHANGED_NORMALIZER_APPLICABILITY_DECLARATION_NOT_EXTRA_HISTORICAL_AUTHORITY'}
        else:
            for field in ('snapshot_id', 'complete', 'expected_count', 'securities'):
                field_roles[field] = {'source_refs': list(refs), 'role': 'CURRENT_DECISION_ROSTER_OR_DERIVED_SECURITY_RECORDS_NOT_HISTORICAL_BOARD_REGISTRY'}
        row_audit.append({'kind': kind, 'instrument_id': getattr(row, 'instrument_id', None),
            'session': str(row.session) if isinstance(row, DailyBar) else None,
            'original_normalized_row_hash': digest(row), 'derived_row_hash': digest(derived),
            'original_normalizer_clocks': _clock_dict(row), 'derived_clocks': _clock_dict(derived),
            'event_time_role': 'ORIGINAL_PARENT_OBSERVATION_BOUND_CURRENT_ROSTER' if kind == 'UNIVERSE' else 'UNCHANGED_NORMALIZER_BUSINESS_EVENT',
            'publication_clock_role': PUBLICATION_ROLE,
            'source_refs': list(refs), 'parents': [parent.model_dump(mode='json') for parent in parents],
            'field_sources': field_roles,
            'parent_dataset_groups': {dataset: [parent.receipt_id for parent in parents if parent.dataset == dataset]
                                  for dataset in sorted({parent.dataset for parent in parents})},
            'field_role_limit': 'EXISTING_NORMALIZER_PARENT_DATASETS_NOT_NEW_FIELD_SCOPE_AUTHORITY',
            'historical_board_certification_added': False, 'parent_status_promoted': False})
        return derived

    securities = tuple(derive(row, 'SECURITY') for row in baseline.universe.securities)
    bars = tuple(derive(row, 'BAR') for row in baseline.bars)
    universe = derive(baseline.universe, 'UNIVERSE', securities)
    new_input = MarketInput.model_validate({**baseline.model_dump(exclude={'bars', 'universe'}),
                                           'bars': bars, 'universe': universe})
    if new_input.receipts != baseline.receipts:
        raise ValueError('LATE_DERIVATION_ORIGINAL_RECEIPTS_CHANGED')
    for path, record in tuple(source_files.items()):
        checked_bytes(path)
        if source_files[path] != record:
            raise ValueError('LATE_DERIVATION_SOURCE_TERMINAL_CHANGED')
    if source_identity() != identity:
        raise ValueError('LATE_DERIVATION_IMPLEMENTATION_CHANGED')
    checkpoint()
    input_hash = digest_market_input(new_input)
    return new_input, {'producer_version': VERSION, 'producer_identity': identity,
        'input_id': 'market_'+input_hash, 'input_hash': input_hash, 'observation_date': str(day),
        'source_asof_cutoff': decision.isoformat(), 'derived_first_seen_at': first_seen.isoformat(),
        'derived_ingested_at': ingested.isoformat(), 'publication_clock_role': PUBLICATION_ROLE,
        'normalization': normalization, 'original_captures': checks, 'derived_rows': row_audit,
        'same_raw_business_observations': repeated_raw,
        'explicit_factor_checkpoint_ancestry': factor_ancestry,
        'capture_inventory_completeness': 'NOT_INFERRED_FROM_LOCATOR_LIST',
        'source_file_bindings': list(source_files.values()), 'source_terminal_equal': True,
        'calendar_source_refs': [rid for rid, _ in calendars], 'calendar_axis_sha256': digest(list(map(str, sessions))),
        'normalizer_call_count': 1, 'history_bound_or_written': False, 'original_receipts_modified': False,
        'original_input_or_run_overwritten': False, 'numeric_basis_link_to_original_history': 'NOT_VERIFIED_HERE',
        'qualification_semantics': 'CURRENT_DECISION_TIME_ROSTER_WITH_SOURCE_DATED_RISK_OBSERVATIONS_NOT_HISTORICAL_BOARD_REGISTRY',
        'historical_prediction_established': False, 'MARKET_EDGE': 'NOT_ESTABLISHED',
        'default_strategy_or_service_integration': 'NOT_CONNECTED', 'wall_seconds': monotonic()-started}
