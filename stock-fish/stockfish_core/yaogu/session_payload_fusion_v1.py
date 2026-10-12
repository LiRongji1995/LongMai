"""Explicit SESSION_PAYLOAD_FUSION_V1 prototype; no default service wiring.

One genuinely read/SHA-authenticated market payload supplies both observation
and bars. It deliberately removes the old _session's intervening second market
read: a concurrent change at that removed observation point has no equivalent
local check. A caller MUST retain its genuine source owner's complete fresh
terminal whole-SHA/7FP validation; this projection and its registration JSON are
not a source capability, an owner, or a replacement terminal verdict. No cross
panel cache, saved-run restoration, selector, or normalizer is introduced.
"""
from __future__ import annotations

from datetime import date
from fractions import Fraction
import hashlib
from pathlib import Path
import re

from .contracts import DailyBar, digest
from .research_context_artifacts import SourceArtifactReader, MARKET_SCOPE, RETURN_BASIS, _decimal, _iid
from .visibility import EvidenceResolver, stamp
from . import short_horizon_participation as r2
from . import short_horizon_strength as r1

SCHEMA_VERSION = ADAPTER_ID = 'SESSION_PAYLOAD_FUSION_V1'


def source_identity():
    """Registration metadata only: caller must physically register these files."""
    here = Path(__file__).resolve().parent
    names = ('session_payload_fusion_v1.py', 'research_context_artifacts.py',
             'short_horizon_participation.py', 'short_horizon_strength.py',
             'contracts.py', 'visibility.py', 'history_artifacts.py')
    files = {name: hashlib.sha256((here / name).read_bytes()).hexdigest() for name in names}
    for name in ('config/short_horizon_strength_r1.yaml', 'config/short_horizon_strength_r2.yaml',
                 'docs/SHORT_HORIZON_STRENGTH_R1.md', 'docs/SHORT_HORIZON_STRENGTH_R2.md'):
        files[name] = hashlib.sha256((here.parents[1] / name).read_bytes()).hexdigest()
    return {'schema_version': SCHEMA_VERSION, 'adapter_id': ADAPTER_ID,
            'source_files': files, 'source_hash': digest(files),
            'role': 'IMPLEMENTATION_REGISTRATION_METADATA_NOT_SOURCE_CAPABILITY',
            'registry_scope': 'DIRECT_PROJECTION_AND_POLICY_FILES_NOT_COMPLETE_CALLER_RUNTIME_UNION',
            'requires_fresh_outer_terminal_whole_sha_and_7fp': True,
            'intermediate_second_market_read_removed': True}


def _market_code(code):
    return r2._market_code(code)


def _market_values(session, market_payload, master_payload):
    """Pure row projection, preserving the complete original R2 guard order."""
    expected = {}
    all_codes = set()
    for row in master_payload['rows']:
        if not isinstance(row, dict):
            raise ValueError('SHR2_MARKET_MASTER_ROW_INVALID')
        code = row.get('code')
        if not isinstance(code, str) or re.fullmatch(r'(sh|sz|bj)\.\d{6}', code) is None or code in all_codes:
            raise ValueError('SHR2_MARKET_MASTER_IDENTITY_MALFORMED_OR_DUPLICATE')
        all_codes.add(code)
        if _market_code(code):
            if str(row.get('tradeStatus')) not in ('0', '1'):
                raise ValueError('SHR2_MARKET_MASTER_TRADE_STATUS_UNKNOWN')
            expected[code] = row
    if not expected:
        raise ValueError('SHR2_MARKET_MASTER_A_SHARE_SCOPE_EMPTY')
    rows = {}
    for row in market_payload['rows']:
        if not isinstance(row, dict):
            raise ValueError('SHR2_MARKET_RAW_ROW_INVALID')
        code = row.get('code')
        if code in rows or not _market_code(code) or str(row.get('date')) != session or str(row.get('adjustflag')) != '3':
            raise ValueError('SHR2_MARKET_RAW_IDENTITY_DATE_BASIS_OR_DUPLICATE_INVALID')
        rows[code] = row
    if set(rows) != set(expected):
        raise ValueError('SHR2_MARKET_ALL_FOUR_BOARD_SCOPE_MISMATCH')
    total = Fraction(0)
    trading = nontrading = 0
    for code, row in rows.items():
        status = str(row.get('tradestatus'))
        if status not in ('0', '1') or status != str(expected[code]['tradeStatus']):
            raise ValueError('SHR2_MARKET_RAW_MASTER_TRADE_STATUS_MISMATCH')
        if status == '0':
            # A source-confirmed nontrading identity is excluded. It does not
            # become a fabricated zero-amount row. Contradictory positive
            # transaction values would make this exclusion unsafe.
            for field in ('amount', 'volume'):
                value = row.get(field)
                if value not in (None, ''):
                    try:
                        valid_zero = r1.exact(value) == 0
                    except (ValueError, TypeError, ZeroDivisionError, KeyError):
                        valid_zero = False
                    if not valid_zero:
                        raise ValueError('SHR2_MARKET_NONTRADING_TRANSACTION_VALUE_CONFLICT')
            nontrading += 1
            continue
        try:
            amount = r1.exact(row.get('amount'))
        except (ValueError, TypeError, ZeroDivisionError, KeyError):
            amount = None
        if amount is None or amount < 0:
            raise ValueError('SHR2_MARKET_TRADING_AMOUNT_UNAVAILABLE_OR_INVALID')
        trading += 1
        total += amount
    return total, len(expected), len(rows), trading, nontrading, digest(sorted(expected))


def _observation(resolver, session, market, master, values):
    total, expected_count, observed_count, trading, nontrading, membership_hash = values
    clocks = {key: max(getattr(market, key), getattr(master, key)) for key in
              ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')}
    refs = (market.receipt_id, master.receipt_id)
    observation = {'session': session, **clocks, 'source_refs': refs}
    proof = resolver.resolve(observation, session=session, require_session=True, require_complete=True)
    if not proof.allowed:
        raise ValueError('SHR2_MARKET_DERIVED_VISIBILITY_INVALID:' + ','.join(proof.reason_codes))
    return {'session': session, 'status': 'COMPLETE', 'amount_cny': r1.encode(total), 'unit': 'CNY',
            'expected_count': expected_count, 'observed_count': observed_count, 'trading_count': trading,
            'known_nontrading_excluded_count': nontrading, 'missing_trading_amount_count': 0,
            'membership_hash': membership_hash, 'source_refs': list(refs),
            'source_content_hashes': {receipt.receipt_id: receipt.content_hash for receipt in (market, master)},
            'clocks': {key: value.isoformat() for key, value in clocks.items()},
            'historical_reconstruction_used': proof.reconstruction_used, 'reason_codes': []}


def _panel(day, market, master, observation, raw, return_basis):
    rows, nontrading = [], []
    clocks = {key: stamp(value) for key, value in observation['clocks'].items()}
    for item in raw:
        iid = _iid(item['code'])
        if str(item['tradestatus']) == '0':
            nontrading.append(iid)
            continue
        # No adjusted/comparable prices are invented. The context's
        # explicit source reference-return basis uses close/preclose.
        bar = DailyBar(instrument_id=iid, session=date.fromisoformat(day),
            open=_decimal(item.get('open')), high=_decimal(item.get('high')),
            low=_decimal(item.get('low')), close=_decimal(item.get('close')),
            reference_prev_close=_decimal(item.get('preclose')),
            volume_shares=_decimal(item.get('volume')),
            amount_cny=_decimal(item.get('amount')),
            source_refs=(market.receipt_id, master.receipt_id), **clocks)
        if (any(getattr(bar, field) is None for field in
                ('open', 'high', 'low', 'close', 'reference_prev_close'))
                or not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high):
            raise ValueError('CONTEXT_RAW_PRICE_INTEGRITY_UNVERIFIED')
        rows.append(bar.model_dump(mode='json'))
    result = {'session': day, 'scope': MARKET_SCOPE,
        'expected_instrument_ids': sorted(_iid(item['code']) for item in raw),
        'rows': sorted(rows, key=lambda row: row['instrument_id']),
        'source_refs': observation['source_refs'], 'clocks': observation['clocks'],
        'status': 'COMPLETE', 'pagination_complete': True,
        'nontrading_instrument_ids': sorted(nontrading),
        'nontrading_policy': 'NONTRADING_EXCLUDED'}
    if return_basis is not None:
        if return_basis != RETURN_BASIS:
            raise ValueError('CONTEXT_RETURN_BASIS_UNRECOGNIZED')
        result['return_basis'] = return_basis
    return result


def project_session_v1(reader, resolver, day, return_basis=None):
    """Return ordinary versioned projections from exact live reader objects.

    Parent selection, original byte authentication and visibility stay in their
    existing APIs. The result is never authority and cannot restore a reader.
    """
    if type(reader) is not SourceArtifactReader:
        raise ValueError('SESSION_FUSION_EXACT_SOURCE_ARTIFACT_READER_REQUIRED')
    if type(resolver) is not EvidenceResolver:
        raise ValueError('SESSION_FUSION_EXACT_EVIDENCE_RESOLVER_REQUIRED')
    market = r2._source_parent(resolver, day, 'market_daily')
    master = r2._source_parent(resolver, day, 'master_all')
    market_payload = r2._source_payload(market, reader)
    master_payload = r2._source_payload(master, reader)
    values = _market_values(day, market_payload, master_payload)
    observation = _observation(resolver, day, market, master, values)
    panel = _panel(day, market, master, observation, market_payload['rows'], return_basis)
    return {'schema_version': SCHEMA_VERSION, 'adapter_id': ADAPTER_ID,
            'observation': observation, 'panel': panel}
