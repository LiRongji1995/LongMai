"""Versioned, inline-only turnover enrichment of the frozen R12 replay.

The old assembler remains the authority for prices, qualification, clocks and
comparability. This adapter reads the same authenticated RAW parent again and
adds only its registered BaoStock ``turn`` field. Local SHA integrity is not
vendor authentication or archived point-in-time evidence.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from .contracts import DailyBar, MarketInput, digest
from . import historical_replay as frozen

VERSION = 'R12_DATED_REGISTERED_IDENTITY_TURNOVER_RECONSTRUCTION_V1'
BASE_VERSION = frozen.VERSION
BASE_SOURCE_SHA256 = '8ec0a5915ac0fe735778499d9cee3be5415fb87178ecb6fdea1f72a85700d281'
PROVIDER_FIELD_VERSION = 'BAOSTOCK_00.9.30_R03_V1'
TURNOVER_BASIS = 'BAOSTOCK_TURN_PERCENT_FLOAT_SHARES'
_ROOT = Path(__file__).resolve().parents[2]
_SPEC = _ROOT / 'docs' / 'HISTORICAL_REPLAY_TURNOVER_V1.md'


def adapter_source_identity():
    """Bind this new semantic version independently of the frozen selectors."""
    paths = [Path(__file__), Path(frozen.__file__), _SPEC]
    paths.extend(Path(__file__).with_name(name) for name in (
        'contracts.py', 'provider_normalization.py', 'provider_comparability.py',
        'provider_requests.py', 'provider_requests_worker.py', 'visibility.py'))
    hashes = {str(path.relative_to(_ROOT)): frozen.file_hash(path) for path in paths}
    if hashes['stockfish_core/yaogu/historical_replay.py'] != BASE_SOURCE_SHA256:
        raise ValueError('TURNOVER_FROZEN_BASE_SOURCE_HASH_MISMATCH')
    identity = {'normalizer_version': VERSION, 'base_normalizer_version': BASE_VERSION,
                'provider_field_version': PROVIDER_FIELD_VERSION,
                'turnover_basis': TURNOVER_BASIS, 'source_file_hashes': hashes}
    return {**identity, 'source_hash': digest(identity)}


def _capture_clock(value, field):
    if not isinstance(value, str):
        raise ValueError('TURNOVER_ORIGINAL_CAPTURE_CLOCK_INVALID:'+field)
    try:
        clock = datetime.fromisoformat(value.replace('Z','+00:00'))
    except (ValueError, TypeError):
        raise ValueError('TURNOVER_ORIGINAL_CAPTURE_CLOCK_INVALID:'+field) from None
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError('TURNOVER_ORIGINAL_CAPTURE_CLOCK_NOT_AWARE:'+field)
    return clock


def _verify_clock_contract(original, payload):
    """Validate actual R03 capture headers, without altering any source clock.

    The worker RAW and archived receipt both carry first_seen. The result
    envelope normally omits it; an explicitly present value must still agree.
    Request start/finish are optional for legacy captures and need ordering,
    not equality to receipt timestamps. Missing and explicit null differ.
    """
    embedded = next(r for r in original['receipts']
                    if r['receipt_id'] == original['request_receipt_id'])
    for field in ('first_seen_at','ingested_at'):
        if field not in embedded:
            raise ValueError('TURNOVER_ORIGINAL_CAPTURE_CLOCK_MISSING:RECEIPT.'+field)
    if 'first_seen_at' not in payload:
        raise ValueError('TURNOVER_ORIGINAL_CAPTURE_CLOCK_MISSING:RAW.first_seen_at')
    seen = _capture_clock(embedded['first_seen_at'],'RECEIPT.first_seen_at')
    ingested = _capture_clock(embedded['ingested_at'],'RECEIPT.ingested_at')
    raw_seen = _capture_clock(payload['first_seen_at'],'RAW.first_seen_at')
    if raw_seen != seen:
        raise ValueError('TURNOVER_ORIGINAL_CAPTURE_CLOCK_MISMATCH:RAW_RECEIPT_FIRST_SEEN')
    if 'first_seen_at' in original:
        result_seen = _capture_clock(original['first_seen_at'],'RESULT.first_seen_at')
        if result_seen != seen:
            raise ValueError('TURNOVER_ORIGINAL_CAPTURE_CLOCK_MISMATCH:RESULT_RECEIPT_FIRST_SEEN')
    if seen > ingested:
        raise ValueError('TURNOVER_ORIGINAL_CAPTURE_CLOCK_ORDER_INVALID:SEEN_AFTER_INGESTED')
    if 'started_at' in original:
        started = _capture_clock(original['started_at'],'RESULT.started_at')
        if started > seen:
            raise ValueError('TURNOVER_ORIGINAL_CAPTURE_CLOCK_ORDER_INVALID:START_AFTER_SEEN')
    if 'completed_at' in original:
        completed = _capture_clock(original['completed_at'],'RESULT.completed_at')
        if ingested > completed:
            raise ValueError('TURNOVER_ORIGINAL_CAPTURE_CLOCK_ORDER_INVALID:INGESTED_AFTER_COMPLETE')
    return {'raw_first_seen_at':payload['first_seen_at'],
            'receipt_first_seen_at':embedded['first_seen_at'],
            'receipt_ingested_at':embedded['ingested_at'],
            'result_first_seen_present':'first_seen_at' in original,
            'result_first_seen_at':original.get('first_seen_at'),
            'result_started_at':original.get('started_at'),
            'result_completed_at':original.get('completed_at'),
            'comparison':'TIMEZONE_AWARE_INSTANT_EQUALITY_AND_CAPTURE_ORDER',
            'source_clocks_modified':False}


def _turn(value):
    """Exact decimal percent -> fraction, with lossless DailyBar float shape.

    R2 reconstitutes ``Fraction(str(float))``. We therefore refuse a value when
    that contract would round the source decimal, including near thresholds.
    Extremely long/exponential values remain UNKNOWN before constructing large
    integers; there is no global integer-string limit or zero substitution.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None, 'TURNOVER_SOURCE_VALUE_MISSING'
    text = str(value)
    if len(text) > 128:
        return None, None, 'TURNOVER_DECIMAL_REPRESENTATION_UNSUPPORTED'
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError, TypeError):
        return None, None, 'TURNOVER_SOURCE_VALUE_INVALID'
    if not number.is_finite() or not 0 <= number <= 1000:
        return None, None, 'TURNOVER_SOURCE_VALUE_INVALID'
    if abs(number.as_tuple().exponent) > 128:
        return None, None, 'TURNOVER_DECIMAL_REPRESENTATION_UNSUPPORTED'
    exact = Fraction(number) / 100
    value_float = float(exact)
    if Fraction(str(value_float)) != exact:
        return None, exact, 'TURNOVER_DAILYBAR_DECIMAL_ROUNDTRIP_NOT_EXACT'
    return value_float, exact, None


class HistoricalReplayTurnoverAssembler:
    """New adapter; never mutate the frozen assembler or a previously saved run.

    Only inline history is supported. A persistent history writer requires its
    own versioned artifact integration and is rejected before any writer call.
    """
    def __init__(self, capture_dir, policy):
        self._identity = adapter_source_identity()
        self._policy = deepcopy(policy)
        self._policy_hash = digest(self._policy)
        self._base = frozen.HistoricalReplayAssembler(capture_dir, self._policy)
        self._result_files = self._result_file_hashes()
        self._results_hash = digest(self._base.results)

    def _result_file_hashes(self):
        return {str(path.relative_to(self._base.root)): frozen.file_hash(path)
                for folder in ('checkpoints', 'results')
                for path in sorted((self._base.root / folder).glob('*.json'))}

    def _assert_fixed_inputs(self):
        if adapter_source_identity() != self._identity:
            raise ValueError('TURNOVER_ADAPTER_SOURCE_CHANGED_DURING_BUILD')
        if digest(self._policy) != self._policy_hash or digest(self._base.results) != self._results_hash:
            raise ValueError('TURNOVER_BASE_CAPTURE_CONTEXT_CHANGED')
        if self._result_file_hashes() != self._result_files:
            raise ValueError('TURNOVER_CAPTURE_RESULT_FILES_CHANGED')
        for document in self._policy['source_documents'].values():
            if frozen.file_hash(document['path']) != document['sha256']:
                raise ValueError('SOURCE_DOCUMENT_HASH_MISMATCH')

    def build(self, observation_date, *, history_sessions=26,
              include_security_history=False, history_store=None):
        if history_store is not None:
            raise ValueError('TURNOVER_INLINE_ONLY_HISTORY_STORE_UNSUPPORTED')
        self._assert_fixed_inputs()
        base, base_audit = frozen.HistoricalReplayAssembler.build(
            self._base, observation_date, history_sessions=history_sessions,
            include_security_history=include_security_history)
        receipts = {receipt.receipt_id: receipt for receipt in base.receipts}
        sources = {}
        verified = {}
        capture_clocks = {}
        for source in base_audit['sources']:
            rid, origin = source['normalized_receipt_id'], source['original_receipt_id']
            if rid in sources:
                raise ValueError('TURNOVER_DUPLICATE_NORMALIZED_PARENT')
            original = self._base.original_refs[origin]
            if source['original_capture_result_hash'] != digest(original):
                raise ValueError('TURNOVER_ORIGINAL_CAPTURE_RESULT_HASH_MISMATCH')
            if origin not in verified:
                integrity = frozen.verify_capture(self._base.root, original)
                if integrity != self._base.integrity[origin]:
                    raise ValueError('TURNOVER_ORIGINAL_CAPTURE_CHANGED')
                raw_bytes = Path(integrity['raw_path']).read_bytes()
                # Bind this second read as well; a concurrent replacement cannot
                # provide a different RAW document after verify_capture.
                if hashlib.sha256(raw_bytes).hexdigest() != integrity['raw_file_sha256']:
                    raise ValueError('TURNOVER_ORIGINAL_RAW_CHANGED_DURING_READ')
                payload = json.loads(raw_bytes)
                if payload.get('rows') != original['rows']:
                    raise ValueError('TURNOVER_ORIGINAL_ROWS_CHANGED_DURING_READ')
                capture_clocks[origin] = _verify_clock_contract(original,payload)
                verified[origin] = (original, payload, integrity)
            sources[rid] = source

        bars, observations = [], []
        raw_fields = (('open', 'open'), ('high', 'high'), ('low', 'low'),
                      ('close', 'close'), ('reference_prev_close', 'preclose'),
                      ('volume_shares', 'volume'), ('amount_cny', 'amount'))
        parent_rows = {}
        for bar in base.bars:
            parents = [rid for rid in bar.source_refs if rid in receipts
                       and receipts[rid].dataset == 'market_daily']
            if len(parents) != 1 or parents[0] not in sources:
                raise ValueError('TURNOVER_SINGLE_RAW_PARENT_REQUIRED')
            rid = parents[0]
            source, receipt = sources[rid], receipts[rid]
            original, payload, integrity = verified[source['original_receipt_id']]
            session = str(bar.session)
            if (original['endpoint'] != 'market_daily'
                    or original['parameters'].get('date') != session
                    or ('adjustflag' in original['parameters']
                        and str(original['parameters']['adjustflag']) != '3')
                    or source['business_date'] != session
                    or receipt.business_sessions != (bar.session,)
                    or receipt.parameters.get('business_date') != session
                    or receipt.parameters.get('original_parameters_hash') != digest(original['parameters'])
                    or receipt.parameters.get('reconstruction_policy_hash') != base_audit['source_policy_hash']
                    or receipt.field_version != BASE_VERSION
                    or receipt.provider != original['provider']):
                raise ValueError('TURNOVER_RAW_PARENT_SCOPE_MISMATCH')
            if rid not in parent_rows:
                if (receipt.content_hash != digest(payload['rows'])
                        or source['normalized_content_hash'] != receipt.content_hash):
                    raise ValueError('TURNOVER_RAW_PARENT_CONTENT_MISMATCH')
                rows = {}
                for raw in payload['rows']:
                    if raw.get('date') != session or str(raw.get('adjustflag')) != '3':
                        raise ValueError('TURNOVER_SOURCE_SECURITY_DATE_OR_ADJUSTMENT_MISMATCH')
                    key = (frozen._iid(raw.get('code')), raw.get('date'))
                    if key in rows:
                        raise ValueError('TURNOVER_DUPLICATE_SOURCE_SECURITY_DATE')
                    rows[key] = raw
                parent_rows[rid] = rows
            raw = parent_rows[rid].get((bar.instrument_id, session))
            if raw is None or str(raw.get('adjustflag')) != '3':
                raise ValueError('TURNOVER_SOURCE_SECURITY_DATE_OR_ADJUSTMENT_MISMATCH')
            if any(getattr(bar, field) != frozen._number(raw.get(provider_field))
                   for field, provider_field in raw_fields):
                raise ValueError('TURNOVER_BASE_RAW_PRICE_IDENTITY_MISMATCH')
            embedded = next(r for r in original['receipts']
                            if r['receipt_id'] == original['request_receipt_id'])
            registered = (original['provider'] == 'baostock'
                          and embedded['provider'] == 'baostock'
                          and original.get('field_version') == PROVIDER_FIELD_VERSION
                          and embedded.get('field_version') == PROVIDER_FIELD_VERSION
                          and payload.get('field_version') == PROVIDER_FIELD_VERSION)
            value, exact, reason = (_turn(raw.get('turn')) if registered else
                                   (None, None, 'TURNOVER_PROVIDER_FIELD_BASIS_UNREGISTERED'))
            bars.append(DailyBar.model_validate({**bar.model_dump(mode='python'),
                'turnover_fraction': value, 'turnover_basis': TURNOVER_BASIS if reason is None else None}))
            observations.append({'instrument_id': bar.instrument_id, 'session': session,
                'status': 'VALID' if reason is None else 'UNKNOWN', 'reason_code': reason,
                'source_percent': raw.get('turn'),
                'exact_fraction': {'numerator': str(exact.numerator), 'denominator': str(exact.denominator)}
                                  if exact is not None else None,
                'turnover_fraction': value, 'turnover_basis': TURNOVER_BASIS if reason is None else None,
                'normalized_receipt_id': rid, 'original_receipt_id': source['original_receipt_id'],
                'raw_row_hash': digest(raw), 'raw_file_sha256': integrity['raw_file_sha256'],
                'receipt_file_sha256': integrity['receipt_file_sha256']})

        bundle = MarketInput.model_validate({**base.model_dump(mode='python'), 'bars': tuple(bars)})
        counts = Counter(row['reason_code'] for row in observations if row['status'] == 'UNKNOWN')
        audit = {**base_audit, 'normalizer_version': VERSION,
                 'base_normalizer_version': BASE_VERSION, 'base_input_hash': base_audit['input_hash'],
                 'base_reconstruction_audit_hash': digest(base_audit), 'input_hash': digest(bundle),
                 'adapter_source_identity': self._identity,
                 'original_capture_clock_contract': capture_clocks,
                 'turnover_unit': 'EXACT_PROVIDER_PERCENT_DIVIDED_BY_100',
                 'turnover_observations': observations,
                 'turnover_counts': {'bars': len(bars), 'valid': len(bars)-sum(counts.values()),
                                     'unknown': sum(counts.values()), 'unknown_reasons': dict(counts)},
                 'unchanged_authorities': ['RAW_AND_COMPARABLE_PRICE', 'SOURCE_RECEIPTS_AND_FOUR_CLOCKS',
                     'BUSINESS_CALENDAR', 'DATED_SECURITY_QUALIFICATION', 'LIMIT_SAFETY', 'SCOPE'],
                 'history_mode': 'INLINE_ONLY', 'vendor_authentication': 'NOT_ESTABLISHED',
                 'historical_trading_qualification': 'ORIGINAL_BASE_ONLY_NOT_EXPANDED',
                 'archived_point_in_time': False}
        self._assert_fixed_inputs()
        for origin, (original, _, initial_integrity) in verified.items():
            if frozen.verify_capture(self._base.root, original) != initial_integrity:
                raise ValueError('TURNOVER_ORIGINAL_CAPTURE_CHANGED_BEFORE_RETURN')
        return bundle, audit
