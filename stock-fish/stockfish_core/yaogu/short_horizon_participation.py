"""R2 research: unchanged native R1 stages plus source-qualified participation.

The frozen-result entry point preserves the saved R1 evidence. It requires an
explicit provenance binding and never relaxes the original history capability.
"""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

import yaml

from .contracts import MarketInput, digest
from .history_artifacts import digest_json_stream, digest_market_input, history_receipts
from .visibility import EvidenceResolver, stamp
from . import short_horizon_strength as r1

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / 'config/short_horizon_strength_r2.yaml'
SPEC_PATH = ROOT / 'docs/SHORT_HORIZON_STRENGTH_R2.md'
CONFIG = yaml.safe_load(CONFIG_PATH.read_text(encoding='utf-8'))
SHORT_HORIZON_ID = STRATEGY_ID = CONFIG['strategy_id']
IMPLEMENTATION_VERSION = CONFIG['implementation_version']
SPEC_REVISION = CONFIG['spec_revision']
FEATURE_VERSION = CONFIG['feature_version']
PARAMETER_STATUS = CONFIG['parameter_status']
SELECTION_POLICY_ID = CONFIG['selection_policy_id']


def _prepare_context(bundle, cutoff, supplied):
    _, se = r1._shared()
    ctx = supplied or se._prepare_context(bundle, cutoff)
    if ctx.bundle is not bundle and ctx.bundle != bundle:
        raise ValueError('SHORT_HORIZON_R2_CONTEXT_INPUT_MISMATCH')
    if cutoff is not None and stamp(cutoff) != ctx.cutoff:
        raise ValueError('SHORT_HORIZON_R2_CONTEXT_CUTOFF_MISMATCH')
    if (str(bundle.observation_date) != ctx.observation_date
            or ctx.sessions != [str(s) for s in bundle.sessions if s <= bundle.observation_date]
            or ctx.resolver.cutoff != ctx.cutoff
            or ctx.resolver.coverage_type != bundle.coverage_type):
        raise ValueError('SHORT_HORIZON_R2_CONTEXT_SCOPE_MISMATCH')
    # This invokes the same bound-store/manifest assertion as native R1. An
    # injected context cannot turn an unavailable history artifact into inline
    # authority or bypass its immutable ref/hash and parent identity checks.
    if bundle.history_artifact_ref:
        history_receipts(bundle)
    return ctx


def collect_participation_features(ctx, iid):
    """Read exactly the recent exchange sessions via R1's selected PIT rows."""
    sam, se = r1._shared()
    end = sam._session_index(ctx)
    count = CONFIG['participation']['window_exchange_sessions']
    sessions = ctx.sessions[max(0, end-count+1):end+1] if end is not None else []
    recognized = set(CONFIG['participation']['recognized_production_turnover_basis'])
    if ctx.bundle.coverage_type == 'TEST_FIXTURE':
        recognized.update(CONFIG['participation']['recognized_test_fixture_turnover_basis'])
    observations = []
    for session in sessions:
        row = se._bar(ctx, session, iid)
        reasons = []
        value = None
        reconstruction_used = False
        if row is None:
            reasons.append('SHR2_PARTICIPATION_BAR_UNAVAILABLE')
        else:
            proof = ctx.resolver.resolve(row, instrument_id=iid, session=session,
                                         require_session=True, require_complete=True)
            reconstruction_used = proof.reconstruction_used
            if not proof.allowed:
                reasons.extend(proof.reason_codes)
            if row.get('quality') != 'VALID':
                reasons.append('SHR2_PARTICIPATION_QUALITY_NOT_VALID')
            if row.get('turnover_basis') not in recognized:
                reasons.append('SHR2_PARTICIPATION_TURNOVER_BASIS_UNRECOGNIZED')
            try:
                value = r1.exact(row.get('turnover_fraction'))
            except (ValueError, TypeError, ZeroDivisionError, KeyError):
                value = None
            if value is None or not Fraction(0) <= value <= Fraction(10):
                reasons.append('SHR2_PARTICIPATION_TURNOVER_UNAVAILABLE_OR_INVALID')
        observations.append({'session': session, 'status': 'UNKNOWN' if reasons else 'VALID',
            'turnover_fraction': value if not reasons else None,
            'turnover_basis': (row or {}).get('turnover_basis'),
            'source_refs': list((row or {}).get('source_refs') or ()),
            'selected_row_hash': digest(row) if row is not None else None,
            'historical_reconstruction_used': reconstruction_used,
            'clocks': {key: str(row[key]) for key in
                ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at') if row and key in row},
            'reason_codes': sorted(set(reasons))})
    return {'window_sessions': sessions, 'requested_window_sessions': count,
            'observations': observations, 'unit': 'FRACTION_OF_PROVIDER_RECOGNIZED_SHARE_BASIS',
            'including_observation_session': True, 'feature_version': FEATURE_VERSION}


def evaluate_participation(features, config=None):
    """Three-valued existential rule; predicates never read names or IDs."""
    seed = (CONFIG if config is None else config)['participation']
    count = seed['window_exchange_sessions']
    threshold = r1.exact(seed['minimum_turnover_fraction_inclusive'])
    sessions = list(features.get('window_sessions') or [])
    observations = list(features.get('observations') or [])
    occurrences = Counter(row.get('session') for row in observations)
    valid = []
    for row in observations:
        try:
            value = r1.exact(row.get('turnover_fraction'))
        except (ValueError, TypeError, ZeroDivisionError, KeyError):
            value = None
        if (row.get('status') == 'VALID' and value is not None and 0 <= value <= 10
                and row.get('session') in sessions and occurrences[row.get('session')] == 1
                and len(sessions) <= count and len(set(sessions)) == len(sessions)):
            valid.append((row.get('session'), value))
    active = [(session, value) for session, value in valid if value >= threshold]
    complete = (len(sessions) == count and len(set(sessions)) == count
                and len(observations) == count and len(valid) == count
                and [row.get('session') for row in observations] == sessions)
    state = 'PASS' if active else 'FAIL' if complete else 'UNKNOWN'
    reason = ('SHR2_RECENT_PARTICIPATION_ALL_FIVE_BELOW_THRESHOLD' if state == 'FAIL'
              else 'SHR2_RECENT_PARTICIPATION_WINDOW_UNVERIFIED' if state == 'UNKNOWN' else None)
    return {'status': state, 'threshold': r1.encode(threshold),
            'window_sessions': sessions, 'requested_window_sessions': count,
            'verified_session_count': len(valid), 'verified_active_session_count': len(active),
            'active_sessions': [session for session, _ in active],
            'maximum_verified_turnover_fraction': r1.encode(max((v for _, v in valid), default=None)),
            'window_complete': complete, 'reason_codes': [reason] if reason else [],
            'observations': r1._encoded_features(observations),
            'feature_version': FEATURE_VERSION}


def collect_liquidity_features(ctx, iid):
    """Current RAW amount, separately qualified without rounded displays."""
    _, se = r1._shared()
    row = se._bar(ctx, ctx.observation_date, iid)
    reasons = []
    amount = None
    reconstruction_used = False
    if row is None:
        reasons.append('SHR2_CURRENT_LIQUIDITY_RAW_BAR_UNAVAILABLE')
    else:
        proof = ctx.resolver.resolve(row, instrument_id=iid, session=ctx.observation_date,
                                     require_session=True, require_complete=True)
        reconstruction_used = proof.reconstruction_used
        if not proof.allowed:
            reasons.extend(proof.reason_codes)
        if row.get('quality') != 'VALID':
            reasons.append('SHR2_CURRENT_LIQUIDITY_QUALITY_NOT_VALID')
        try:
            prices = [r1.exact(row.get(key)) for key in ('open', 'high', 'low', 'close', 'reference_prev_close')]
            amount = r1.exact(row.get('amount_cny'))
        except (ValueError, TypeError, ZeroDivisionError, KeyError):
            prices, amount = [None] * 5, None
        if any(value is None or value <= 0 for value in prices):
            reasons.append('SHR2_CURRENT_LIQUIDITY_RAW_OHLC_UNAVAILABLE_OR_NONPOSITIVE')
        elif not prices[2] <= min(prices[0], prices[3]) <= max(prices[0], prices[3]) <= prices[1]:
            reasons.append('SHR2_CURRENT_LIQUIDITY_RAW_OHLC_INTEGRITY_UNCONFIRMED')
        if amount is None or amount < 0:
            reasons.append('SHR2_CURRENT_LIQUIDITY_AMOUNT_UNAVAILABLE_OR_INVALID')
    return {'session': ctx.observation_date, 'evidence_status': 'UNKNOWN' if reasons else 'VALID',
            'amount_cny': amount if not reasons else None, 'unit': 'CNY',
            'source_refs': list((row or {}).get('source_refs') or ()),
            'selected_row_hash': digest(row) if row is not None else None,
            'historical_reconstruction_used': reconstruction_used,
            'clocks': {key: str(row[key]) for key in
                ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at') if row and key in row},
            'reason_codes': sorted(set(reasons))}


def evaluate_liquidity(features, market_context=None, config=None):
    """Dynamic amount reference is explanatory and never a selection gate."""
    seed = (CONFIG if config is None else config)['liquidity']
    market_context = market_context or {}
    try:
        median = r1.exact(market_context.get('median_amount_cny'))
    except (ValueError, TypeError, ZeroDivisionError, KeyError):
        median = None
    reference = (r1.exact(seed['reference_base_cny']) + r1.exact(seed['market_amount_coefficient']) * median
                 if market_context.get('status') == 'AVAILABLE' and median is not None and median >= 0 else None)
    try:
        value = r1.exact(features.get('amount_cny'))
    except (ValueError, TypeError, ZeroDivisionError, KeyError):
        value = None
    known = features.get('evidence_status') == 'VALID' and value is not None and value >= 0
    available = known and reference is not None
    position = ('UNKNOWN' if not available else 'ABOVE_REFERENCE' if value > reference
                else 'AT_REFERENCE' if value == reference else 'BELOW_REFERENCE')
    ratio = value / reference if available and reference > 0 else None
    score = 100 * value / (value + reference) if available and value + reference > 0 else None
    reasons = list(features.get('reason_codes', []))
    if not known:
        reasons.append('SHR2_CURRENT_LIQUIDITY_EVIDENCE_UNVERIFIED')
    if reference is None:
        reasons.extend(market_context.get('reason_codes', []))
        reasons.append('SHR2_DYNAMIC_MARKET_REFERENCE_UNAVAILABLE')
    return {'status': 'AVAILABLE' if available else 'UNKNOWN', 'position': position,
            'value': r1.encode(value) if known else None, 'reference_cny': r1.encode(reference),
            'market_median_cny': r1.encode(median), 'ratio_to_reference': r1.encode(ratio),
            'soft_score_exact': r1.encode(score), 'unit': 'CNY', 'selection_gate': False,
            'reference_formula': '100000000 + 0.0002 * median(last5 completed HUSHEN_A RAW amounts)',
            'reason_codes': sorted(set(reasons)),
            'evidence': r1._encoded_features(features), 'feature_version': FEATURE_VERSION}


def _market_code(code):
    return isinstance(code, str) and re.fullmatch(r'(?:sh\.(?:60|68)|sz\.(?:00|30))\d{4}', code) is not None


def _source_payload(receipt, reader):
    """Authenticate original bytes before inspecting business fields."""
    if reader is None:
        raise ValueError('SHR2_MARKET_SOURCE_PAYLOAD_READER_UNAVAILABLE')
    try:
        raw_bytes = reader(receipt.receipt_id)
    except (FileNotFoundError, PermissionError, IsADirectoryError) as error:
        raise ValueError('SHR2_MARKET_SOURCE_PAYLOAD_UNAVAILABLE') from error
    if not isinstance(raw_bytes, bytes):
        raise ValueError('SHR2_MARKET_ORIGINAL_BYTES_REQUIRED')
    if hashlib.sha256(raw_bytes).hexdigest() != receipt.content_hash:
        raise ValueError('SHR2_MARKET_SOURCE_PAYLOAD_SHA_MISMATCH')
    try:
        payload = json.loads(raw_bytes)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError('SHR2_MARKET_SOURCE_PAYLOAD_JSON_INVALID') from error
    if (not isinstance(payload, dict) or payload.get('status') != 'COMPLETE'
            or payload.get('pagination_complete') is not True
            or payload.get('field_version') != receipt.field_version
            or not isinstance(payload.get('rows'), list) or len(payload['rows']) != receipt.row_count):
        raise ValueError('SHR2_MARKET_SOURCE_PAYLOAD_ENVELOPE_MISMATCH')
    try:
        same_clock = stamp(payload.get('first_seen_at')) == receipt.first_seen_at
    except (ValueError, TypeError):
        same_clock = False
    if not same_clock:
        raise ValueError('SHR2_MARKET_SOURCE_PAYLOAD_CAPTURE_CLOCK_MISMATCH')
    return payload


def _source_parent(resolver, session, dataset):
    candidates = [receipt for receipt in resolver.receipts.values()
        if receipt.dataset == dataset and str(receipt.parameters.get('date')) == session
        and str(receipt.provider).lower() == 'baostock']
    visible = []
    rejected = set()
    for receipt in candidates:
        if max(receipt.event_time, receipt.source_published_at, receipt.first_seen_at,
               receipt.ingested_at) > resolver.cutoff:
            rejected.add('CAPTURE_AFTER_CUTOFF')
            continue
        if receipt.business_sessions and session not in {str(day) for day in receipt.business_sessions}:
            rejected.add('SOURCE_BUSINESS_SESSION_SCOPE_MISMATCH')
            continue
        proof = resolver.receipt_resolution(receipt, require_complete=True, business_day=session)
        if proof.allowed:
            visible.append(receipt)
        else:
            rejected.update(proof.reason_codes)
    if not visible:
        raise ValueError('SHR2_MARKET_' + dataset.upper() + '_SOURCE_UNAVAILABLE' +
                         (':' + ','.join(sorted(rejected)) if rejected else ''))
    latest = max((r.source_published_at, r.ingested_at, r.first_seen_at) for r in visible)
    chosen = [r for r in visible if (r.source_published_at, r.ingested_at, r.first_seen_at) == latest]
    if len({r.content_hash for r in chosen}) != 1:
        raise ValueError('SHR2_MARKET_' + dataset.upper() + '_SOURCE_REVISION_CONFLICT')
    receipt = min(chosen, key=lambda r: r.receipt_id)
    if receipt.field_version != 'BAOSTOCK_00.9.30_R03_V1':
        raise ValueError('SHR2_MARKET_SOURCE_FIELD_DEFINITION_UNRECOGNIZED')
    return receipt


def _market_observation(resolver, session, reader):
    market = _source_parent(resolver, session, 'market_daily')
    master = _source_parent(resolver, session, 'master_all')
    market_payload, master_payload = _source_payload(market, reader), _source_payload(master, reader)
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
    clocks = {key: max(getattr(market, key), getattr(master, key)) for key in
              ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')}
    refs = (market.receipt_id, master.receipt_id)
    observation = {'session': session, **clocks, 'source_refs': refs}
    proof = resolver.resolve(observation, session=session, require_session=True, require_complete=True)
    if not proof.allowed:
        raise ValueError('SHR2_MARKET_DERIVED_VISIBILITY_INVALID:' + ','.join(proof.reason_codes))
    return {'session': session, 'status': 'COMPLETE', 'amount_cny': r1.encode(total), 'unit': 'CNY',
            'expected_count': len(expected), 'observed_count': len(rows), 'trading_count': trading,
            'known_nontrading_excluded_count': nontrading, 'missing_trading_amount_count': 0,
            'membership_hash': digest(sorted(expected)), 'source_refs': list(refs),
            'source_content_hashes': {receipt.receipt_id: receipt.content_hash for receipt in (market, master)},
            'clocks': {key: value.isoformat() for key, value in clocks.items()},
            'historical_reconstruction_used': proof.reconstruction_used, 'reason_codes': []}


def collect_market_turnover_context(ctx, *, _source_payload_reader=None, _source_receipts=None):
    """Whole-market denominator from original authenticated source bytes.

Extra source receipts are a separate context layer. They do not mutate or
replace the immutable R1 input, and remain subject to the same actual cutoff.
    """
    from .contracts import SourceReceipt
    extra = (_source_receipts if _source_receipts is not None else
             getattr(_source_payload_reader, 'receipts', ())) or ()
    receipts = tuple(ctx.bundle.receipts) + tuple(SourceReceipt.model_validate(r) if isinstance(r, dict) else r for r in extra)
    resolver = EvidenceResolver(receipts, cutoff=ctx.cutoff, coverage_type=ctx.bundle.coverage_type,
        sessions=ctx.sessions, observation_date=ctx.observation_date, record_rows=False)
    if resolver.conflicts:
        raise ValueError('SHR2_MARKET_CONTEXT_SOURCE_RECEIPT_CONFLICT:' + ','.join(sorted(resolver.conflicts)))
    sam, _ = r1._shared()
    end = sam._session_index(ctx)
    count = CONFIG['liquidity']['market_window_exchange_sessions']
    sessions = ctx.sessions[max(0, end-count+1):end+1] if end is not None else []
    observations = []
    for session in sessions:
        try:
            observation = _market_observation(resolver, session, _source_payload_reader)
        except ValueError as error:
            observation = {'session': session, 'status': 'UNKNOWN', 'amount_cny': None,
                           'source_refs': [], 'source_content_hashes': {}, 'reason_codes': [str(error)]}
        observations.append(observation)
    complete = len(sessions) == count and all(row['status'] == 'COMPLETE' for row in observations)
    amounts = sorted(r1.exact(row['amount_cny']) for row in observations if row['status'] == 'COMPLETE')
    median = amounts[count // 2] if complete else None
    reasons = sorted({code for row in observations for code in row['reason_codes']})
    if len(sessions) != count:
        reasons.append('SHR2_MARKET_FIVE_SESSION_CALENDAR_WINDOW_UNAVAILABLE')
    source_refs = sorted({ref for row in observations for ref in row['source_refs']})
    selected_receipts = [resolver.receipts[ref].model_dump(mode='json') for ref in source_refs]
    considered_receipts = sorted((receipt.model_dump(mode='json') for receipt in resolver.receipts.values()
        if receipt.dataset in ('market_daily', 'master_all')
        and str(receipt.parameters.get('date')) in sessions), key=lambda receipt: receipt['receipt_id'])
    result = {'definition_version': CONFIG['liquidity']['market_measurement_definition'],
        'scope': CONFIG['liquidity']['market_scope'], 'nontrading_policy': CONFIG['liquidity']['nontrading_policy'],
        'status': 'AVAILABLE' if complete else 'UNKNOWN', 'observation_date': ctx.observation_date,
        'decision_cutoff': ctx.cutoff.isoformat(), 'window_sessions': sessions,
        'requested_window_sessions': count, 'observations': observations,
        'median_amount_cny': r1.encode(median), 'source_refs': source_refs,
        'source_receipts': selected_receipts, 'source_receipts_hash': digest(selected_receipts),
        'considered_source_receipts': considered_receipts,
        'considered_source_receipts_hash': digest(considered_receipts),
        'selection_gate': False, 'reason_codes': reasons,
        'source_policy': 'ORIGINAL_BYTES_SHA_PIT_COMPLETE_DATED_FULL_MARKET_MASTER_AND_RAW',
        'input_hash_unchanged_by_extra_context_sources': True}
    result['context_content_hash'] = digest_json_stream(result)
    return result


def short_horizon_source_identity():
    base = r1.short_horizon_source_identity()
    hashes = dict(base['source_file_hashes'])
    for path in (Path(__file__), CONFIG_PATH, SPEC_PATH, Path(__file__).with_name('short_horizon_profiles.py'),
                 Path(__file__).with_name('theme_evolution.py'), ROOT / 'config/theme_evolution_r1.yaml',
                 ROOT / 'docs/THEME_EVOLUTION_R1.md', Path(__file__).with_name('research_context_artifacts.py')):
        hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    policy = {'id': SELECTION_POLICY_ID, 'base_strategy_id': r1.STRATEGY_ID,
              'participation': CONFIG['participation'], 'liquidity': CONFIG['liquidity'],
              'style_combination': CONFIG['style_combination'], 'research_only': True,
              'formal_focus': False, 'core_pass': False, 'top_n_truncation': False,
              'stable_order': 'research_selected then instrument_id',
              'frozen_result_policy': 'VERIFIED_BOUND_R1_RESULT_WITH_ORIGINAL_PIT_STYLE_COLLECTOR'}
    return {'implementation_version': IMPLEMENTATION_VERSION, 'spec_revision': SPEC_REVISION,
            'feature_version': FEATURE_VERSION, 'source_hash': digest(hashes),
            'source_file_hashes': hashes, 'config_hash': digest({'r2': CONFIG, 'base_r1': r1.CONFIG}),
            'spec_hash': hashes[str(SPEC_PATH.relative_to(ROOT))],
            'selection_policy_hash': digest(policy), 'base_r1_source_identity': base}


def short_horizon_strategy_specs():
    return [{'strategy_id': STRATEGY_ID, 'feature_version': FEATURE_VERSION,
             'parameter_status': PARAMETER_STATUS, 'parameter_status_label_zh': CONFIG['parameter_status_label_zh'],
             'formal_role': 'RESEARCH_OBSERVATION_ONLY', 'market_edge': 'NOT_ESTABLISHED',
             'spec': CONFIG, **short_horizon_source_identity()}]


def _validate_base(bundle, ctx, result, provenance, base_run=None):
    if r1.STRATEGY_ID != CONFIG['base_strategy_id'] or r1.FEATURE_VERSION != CONFIG['base_feature_version']:
        raise ValueError('SHORT_HORIZON_R2_BASE_IMPLEMENTATION_VERSION_MISMATCH')
    if result.get('strategy_id') != r1.STRATEGY_ID or result.get('feature_version') != CONFIG['base_feature_version']:
        raise ValueError('SHORT_HORIZON_R2_BASE_RESULT_VERSION_MISMATCH')
    if (str(result.get('observation_date')) != ctx.observation_date
            or stamp(result.get('decision_cutoff')) != ctx.cutoff):
        raise ValueError('SHORT_HORIZON_R2_BASE_DATE_OR_CUTOFF_MISMATCH')
    ids = [row.get('instrument_id') for row in result.get('items') or []]
    expected = list(ctx.eligible_ids)
    if (result.get('eligible_pool_ids') != expected or len(ids) != len(set(ids))
            or set(ids) != set(expected) or result.get('full_eligible_pool_retained') is not True
            or digest(result.get('qualification')) != digest(ctx.qualification)):
        raise ValueError('SHORT_HORIZON_R2_BASE_QUALIFICATION_MISMATCH')
    if (result.get('counts', {}).get('eligible') != len(expected)
            or result.get('counts', {}).get('evaluated') != len(expected)):
        raise ValueError('SHORT_HORIZON_R2_BASE_POOL_COUNTS_MISMATCH')
    if (result.get('top_ids') or result.get('qualified_ids')
            or result.get('counts', {}).get('focus') != 0 or result.get('counts', {}).get('passed') != 0
            or any(row.get('decision_status') == 'FOCUS' or row.get('formal_focus') is not False
                   or row.get('core_pass') is not False for row in result['items'])):
        raise ValueError('SHORT_HORIZON_R2_BASE_RESEARCH_SAFETY_MISMATCH')
    selected = [row['instrument_id'] for row in result['items'] if row.get('research_selected') is True]
    if result.get('research_selected_ids') != selected:
        raise ValueError('SHORT_HORIZON_R2_BASE_SELECTION_ID_MISMATCH')
    if not isinstance(provenance, dict):
        raise ValueError('SHORT_HORIZON_R2_BASE_PROVENANCE_REQUIRED')
    input_hash = digest_market_input(bundle)
    result_hash = digest_json_stream(result)
    if (provenance.get('mode') not in ('NATIVE_R1_EVALUATION', 'VERIFIED_FROZEN_R1_INLINE_STYLE')
            or not provenance.get('base_run_id') or not provenance.get('input_id')
            or provenance.get('base_result_hash') != result_hash or provenance.get('input_hash') != input_hash
            or str(provenance.get('observation_date')) != ctx.observation_date
            or stamp(provenance.get('decision_cutoff')) != ctx.cutoff
            or provenance.get('base_source_hash') != result.get('source_hash')):
        raise ValueError('SHORT_HORIZON_R2_BASE_PROVENANCE_MISMATCH')
    for key, actual in (('input_hash', input_hash), ('input_id', provenance['input_id'])):
        if key in result and result[key] != actual:
            raise ValueError('SHORT_HORIZON_R2_BASE_INPUT_IDENTITY_MISMATCH')
    if result.get('input_hash') != input_hash:
        raise ValueError('SHORT_HORIZON_R2_BASE_INPUT_HASH_REQUIRED')
    for key in ('history_artifact_ref', 'history_artifact_hash'):
        if result.get(key) != getattr(bundle, key):
            raise ValueError('SHORT_HORIZON_R2_BASE_HISTORY_IDENTITY_MISMATCH')
    if provenance['mode'] == 'VERIFIED_FROZEN_R1_INLINE_STYLE':
        _validate_frozen_run(bundle, result, provenance, input_hash, base_run)
    return {key: value for key, value in {
            **{k: v for k, v in provenance.items() if k != 'base_run'},
            'base_result_hash': result_hash, 'input_hash': input_hash,
            'base_feature_version': result['feature_version'], 'base_strategy_id': result['strategy_id'],
            'base_qualification_hash': digest(ctx.qualification),
            'history_artifact_ref': bundle.history_artifact_ref,
            'history_artifact_hash': bundle.history_artifact_hash,
            'original_r1_result_preserved': True,
            'original_r1_history_database_reverified_in_this_application': provenance['mode'] == 'NATIVE_R1_EVALUATION',
            'style_source_scope': 'ONLY_ORIGINAL_INPUT_INLINE_BARS_AND_RECEIPTS'
                if provenance['mode'] == 'VERIFIED_FROZEN_R1_INLINE_STYLE' else 'ORIGINAL_NATIVE_HISTORY_AND_INLINE_RESOLVER',
        }.items()}


def _validate_frozen_run(bundle, result, provenance, input_hash, parent):
    """Bind the entire previously authenticated native producer document."""
    _, se = r1._shared()
    if not isinstance(parent, dict) or digest_json_stream(parent) != provenance.get('base_run_content_hash'):
        raise ValueError('SHORT_HORIZON_R2_FROZEN_FULL_RUN_HASH_MISMATCH')
    file_sha = provenance.get('base_run_sha256')
    if not isinstance(file_sha, str) or len(file_sha) != 64 or any(c not in '0123456789abcdef' for c in file_sha):
        raise ValueError('SHORT_HORIZON_R2_FROZEN_ARCHIVE_SHA_REQUIRED')
    stable_fields = ('input_hash', 'observation_date', 'decision_cutoff', 'spec_hash', 'source_hash',
                     'implementation_version', 'universe_hash', 'execution_profile', 'feature_version')
    try:
        stable_id = se._stable_id('strategy_experiment', {key: parent[key] for key in stable_fields})
    except KeyError as error:
        raise ValueError('SHORT_HORIZON_R2_FROZEN_NATIVE_RUN_FIELDS_REQUIRED') from error
    if (parent.get('run_id') != stable_id or parent['run_id'] != provenance['base_run_id']
            or parent.get('strategy_id') != r1.STRATEGY_ID
            or parent.get('execution_profile') != 'SHORT_HORIZON_RESEARCH_SINGLE'
            or set(parent.get('results', {})) != {r1.STRATEGY_ID}
            or digest_json_stream(parent['results'][r1.STRATEGY_ID]) != provenance['base_result_hash']
            or parent.get('input_hash') != input_hash or parent.get('input_id') != provenance['input_id']
            or str(parent.get('observation_date')) != str(bundle.observation_date)
            or stamp(parent.get('decision_cutoff')) != stamp(provenance['decision_cutoff'])
            or parent.get('universe_hash') != digest(result.get('qualification'))):
        raise ValueError('SHORT_HORIZON_R2_FROZEN_NATIVE_RUN_BINDING_MISMATCH')
    for key in ('implementation_version', 'spec_revision', 'feature_version', 'source_hash',
                'source_file_hashes', 'config_hash', 'spec_hash', 'selection_policy_hash'):
        if parent.get(key) != result.get(key):
            raise ValueError('SHORT_HORIZON_R2_FROZEN_SOURCE_IDENTITY_MISMATCH')
    if result.get('source_hash') != digest(result.get('source_file_hashes')):
        raise ValueError('SHORT_HORIZON_R2_FROZEN_SOURCE_HASH_MISMATCH')
    if result.get('config_hash') != digest(r1.CONFIG):
        raise ValueError('SHORT_HORIZON_R2_FROZEN_BASE_CONFIG_MISMATCH')
    for key in ('history_artifact_ref', 'history_artifact_hash'):
        if parent.get(key) != getattr(bundle, key):
            raise ValueError('SHORT_HORIZON_R2_FROZEN_HISTORY_IDENTITY_MISMATCH')
    selection = parent.get('selection_results', {}).get(r1.SELECTION_POLICY_ID, {})
    if selection.get('selected_ids') != result.get('research_selected_ids'):
        raise ValueError('SHORT_HORIZON_R2_FROZEN_NATIVE_SELECTION_MISMATCH')


def _inline_style_context(bundle, cutoff, supplied):
    """An explicit new-feature view, never an alternate R1 history loader."""
    _, se = r1._shared()
    view = bundle.model_copy(update={'history_artifact_ref': None, 'history_artifact_hash': None})
    view._history_store = None
    if supplied is not None and supplied.bundle != view:
        raise ValueError('SHORT_HORIZON_R2_FROZEN_INLINE_CONTEXT_MISMATCH')
    return _prepare_context(view, cutoff, supplied)


def prepare_inline_style_context(bundle: MarketInput, cutoff=None):
    """Prepare only the original input's inline new-feature source view.

This is deliberately separate from native R1's bound historical context and
does not authenticate a frozen parent. apply_style_to_r1_result does that.
    """
    return _inline_style_context(bundle, cutoff, None)


def _validate_frozen_context_extension(base_ctx, ctx):
    """Later context cannot revise the original qualification or inline prices."""
    if (ctx.cutoff < base_ctx.cutoff or ctx.observation_date != base_ctx.observation_date
            or ctx.sessions != base_ctx.sessions or ctx.eligible_ids != base_ctx.eligible_ids
            or digest(ctx.qualification) != digest(base_ctx.qualification)
            or digest_market_input(ctx.bundle) != digest_market_input(base_ctx.bundle)):
        raise ValueError('SHORT_HORIZON_R2_FROZEN_CONTEXT_EXTENSION_SCOPE_MISMATCH')
    _, se = r1._shared()
    # R1's longest stage comparison uses 25 sessions. Missing inline history
    # remains missing in both views; no unavailable historical store is opened.
    for session in ctx.sessions[-25:]:
        for iid in ctx.eligible_ids:
            if digest(se._bar(base_ctx, session, iid)) != digest(se._bar(ctx, session, iid)):
                raise ValueError('SHORT_HORIZON_R2_FROZEN_CONTEXT_EXTENSION_PRICE_REVISION')


def apply_style_to_r1_result(bundle: MarketInput, r1_result, *, cutoff=None, _context=None,
                            base_provenance=None, base_run=None,
                            _source_payload_reader=None, _source_receipts=None):
    """Add participation to a bound native R1 result without mutating it.

The caller verifying an archived saved run supplies its ID and canonical result
hash, exact input identity and cutoff. Unchanged large R1 evidence mappings are
shared read-only; only modified row/result containers are copied.
    """
    frozen = isinstance(base_provenance, dict) and base_provenance.get('mode') == 'VERIFIED_FROZEN_R1_INLINE_STYLE'
    ctx = _inline_style_context(bundle, cutoff, _context) if frozen else _prepare_context(bundle, cutoff, _context)
    if frozen:
        base_cutoff = stamp(base_provenance['decision_cutoff'])
        base_ctx = ctx if ctx.cutoff == base_cutoff else _inline_style_context(bundle, base_cutoff, None)
        provenance = _validate_base(bundle, base_ctx, r1_result, base_provenance, base_run)
        if ctx.cutoff != base_cutoff:
            _validate_frozen_context_extension(base_ctx, ctx)
    else:
        provenance = _validate_base(bundle, ctx, r1_result, base_provenance, base_run)
    provenance = {**provenance, 'base_decision_cutoff': r1_result['decision_cutoff'],
                  'new_context_cutoff': ctx.cutoff.isoformat(),
                  'new_context_does_not_revise_base_price_or_qualification': True}
    _, se = r1._shared()
    sam, _ = r1._shared()
    end = sam._session_index(ctx)
    count = CONFIG['participation']['window_exchange_sessions']
    sessions = ctx.sessions[max(0, end-count+1):end+1] if end is not None else []
    se._preload_bars(ctx, sessions, ctx.eligible_ids)
    market_context = collect_market_turnover_context(ctx,
        _source_payload_reader=_source_payload_reader, _source_receipts=_source_receipts)
    rows = []
    for original in r1_result['items']:
        facts = collect_participation_features(ctx, original['instrument_id'])
        participation = evaluate_participation(facts)
        liquidity_facts = collect_liquidity_features(ctx, original['instrument_id'])
        liquidity = evaluate_liquidity(liquidity_facts, market_context)
        style = r1.combine({'recent_participation': participation})
        base = original['decision_status']
        selected = original['research_selected'] is True and style['status'] == 'PASS'
        decision = ('REJECTED' if base == 'REJECTED' or style['status'] == 'FAIL'
                    else 'WATCH' if selected else 'UNKNOWN')
        row = {**original, 'strategy_id': STRATEGY_ID, 'implementation_version': IMPLEMENTATION_VERSION,
               'spec_revision': SPEC_REVISION, 'feature_version': FEATURE_VERSION,
               'r1_decision_status': base, 'r1_research_selected': original['research_selected'],
               'participation': participation, 'participation_status': participation['status'],
               'participation_peak_turnover_fraction': sam._number(r1.exact(participation['maximum_verified_turnover_fraction'])),
               'liquidity': liquidity, 'liquidity_status': liquidity['status'],
               'liquidity_position': liquidity['position'],
               'liquidity_reference_cny': sam._number(r1.exact(liquidity['reference_cny'])),
               'liquidity_ratio_to_reference': sam._number(r1.exact(liquidity['ratio_to_reference'])),
               'liquidity_soft_score': sam._number(r1.exact(liquidity['soft_score_exact'])),
               'liquidity_selection_gate': False, 'style_status': style['status'],
               'conditions': {**original.get('conditions', {}), 'recent_participation': participation,
                              'current_liquidity': liquidity},
               'exact_features': {**original.get('exact_features', {}),
                                  'recent_participation': r1._encoded_features(facts),
                                  'current_liquidity': r1._encoded_features(liquidity_facts)},
               'research_selected': selected, 'decision_status': decision, 'status': decision,
               'research_status': 'RESEARCH_OBSERVATION' if selected else
                   'RECENT_PARTICIPATION_REJECTED' if style['status'] == 'FAIL' else
                   'UNKNOWN_PARTICIPATION' if style['status'] == 'UNKNOWN' and base != 'REJECTED'
                   else original.get('research_status'),
               'formal_status': 'FAIL' if decision == 'REJECTED' else 'UNKNOWN',
               'formal_focus': False, 'core_pass': False, 'executability': 'UNCONFIRMED',
               'market_edge': 'NOT_ESTABLISHED',
               'reason_codes': sorted(set(original.get('reason_codes', []) + style['reason_codes'])),
               'watch_reasons': ['SHR2_RESEARCH_ONLY_EXECUTION_UNCONFIRMED'] if selected else []}
        rows.append(row)
    rows.sort(key=lambda row: (not row['research_selected'], row['instrument_id']))
    for rank, row in enumerate(rows, 1):
        row['rank'] = row['display_rank'] = rank
        if 'display_order' in row:
            row['display_order'] = rank
    decisions = Counter(row['decision_status'] for row in rows)
    styles = Counter(row['participation_status'] for row in rows)
    liquidity_counts = Counter(row['liquidity_status'] for row in rows)
    liquidity_positions = Counter(row['liquidity_position'] for row in rows)
    combined_counts = Counter(row['style_status'] for row in rows)
    selected = [row['instrument_id'] for row in rows if row['research_selected']]
    result = {**r1_result, 'strategy_id': STRATEGY_ID, **short_horizon_source_identity(),
        'selection_policy_id': SELECTION_POLICY_ID, 'items': rows,
        'top_ids': [], 'qualified_ids': [], 'research_selected_ids': selected,
        'base_run_provenance': provenance, 'base_r1_result_hash': provenance['base_result_hash'],
        'decision_cutoff': ctx.cutoff.isoformat(),
        'base_decision_cutoff': r1_result['decision_cutoff'], 'new_context_cutoff': ctx.cutoff.isoformat(),
        'market_turnover_context': market_context,
        'parameter_status': PARAMETER_STATUS, 'parameter_status_label_zh': CONFIG['parameter_status_label_zh'],
        'status': 'INSUFFICIENT_EVIDENCE' if selected or decisions['UNKNOWN'] or not ctx.universe_complete else 'EMPTY',
        'research_readiness': 'RESEARCH_MATCHES_AVAILABLE' if selected and ctx.universe_complete
            else 'LIMITED_OR_UNAVAILABLE_RESEARCH_EVIDENCE',
        'counts': {**r1_result['counts'], 'eligible': len(rows), 'evaluated': len(rows), 'focus': 0, 'passed': 0,
            'watch': decisions['WATCH'], 'rejected': decisions['REJECTED'], 'unknown': decisions['UNKNOWN'],
            'failed': decisions['REJECTED'], 'research_observation': len(selected),
            'decision_counts': {state: decisions[state] for state in ('FOCUS', 'WATCH', 'REJECTED', 'UNKNOWN')},
            'participation_counts': {state: styles[state] for state in ('PASS', 'FAIL', 'UNKNOWN')},
            'liquidity_counts': {state: liquidity_counts[state] for state in ('AVAILABLE', 'UNKNOWN')},
            'liquidity_position_counts': {state: liquidity_positions[state] for state in
                ('ABOVE_REFERENCE', 'AT_REFERENCE', 'BELOW_REFERENCE', 'UNKNOWN')},
            'style_counts': {state: combined_counts[state] for state in ('PASS', 'FAIL', 'UNKNOWN')}},
        'fact_coverage': {**r1_result.get('fact_coverage', {}),
            'participation_counts': {state: styles[state] for state in ('PASS', 'FAIL', 'UNKNOWN')},
            'liquidity_counts': {state: liquidity_counts[state] for state in ('AVAILABLE', 'UNKNOWN')},
            'market_turnover_context_status': market_context['status'],
            'style_counts': {state: combined_counts[state] for state in ('PASS', 'FAIL', 'UNKNOWN')}},
        'traceability': {**r1_result.get('traceability', {}), 'feature_version': FEATURE_VERSION,
            'base_strategy_id': r1.STRATEGY_ID, 'base_r1_rules_unchanged': True,
            'participation_uses_original_pit_resolver': True,
            'market_amount_from_original_full_scope_bytes': True,
            'liquidity_reference_role': 'SOFT_CONTEXT_ONLY_NO_SELECTION_GATE',
            'base_decision_cutoff': r1_result['decision_cutoff'],
            'new_context_cutoff': ctx.cutoff.isoformat(), 'old_run_overwrite': False}}
    # A previously exported view is not a native full-pool producer result.
    # Optional scope summaries from the normal wrapper are updated, not trusted
    # as price/participation completeness.
    if 'coverage_scope' in result:
        result['coverage_scope'] = {**result['coverage_scope'], 'feature_unknown_instrument_count': decisions['UNKNOWN'],
                                    'fact_coverage': result['fact_coverage']}
    return result


def run_short_horizon_strength(bundle: MarketInput, *, cutoff=None, _context=None,
                               _source_payload_reader=None, _source_receipts=None):
    """Production entry point always runs the original native R1 collector."""
    ctx = _prepare_context(bundle, cutoff, _context)
    identity = short_horizon_source_identity()
    base = r1.run_short_horizon_strength(bundle, cutoff=ctx.cutoff, _context=ctx)
    input_hash = digest_market_input(bundle)
    base = {**base, 'input_hash': input_hash,
            'history_artifact_ref': bundle.history_artifact_ref, 'history_artifact_hash': bundle.history_artifact_hash}
    result_hash = digest_json_stream(base)
    provenance = {'mode': 'NATIVE_R1_EVALUATION',
        'base_run_id': 'native_r1_' + digest({'input_hash': input_hash, 'cutoff': ctx.cutoff.isoformat(),
                                          'base_result_hash': result_hash}),
        'base_run_id_role': 'IN_MEMORY_NATIVE_R1_RESULT_NOT_SAVED_RUN',
        'base_result_hash': result_hash, 'base_source_hash': base['source_hash'],
        'input_id': 'market_' + input_hash, 'input_hash': input_hash,
        'observation_date': ctx.observation_date, 'decision_cutoff': ctx.cutoff.isoformat()}
    result = apply_style_to_r1_result(bundle, base, cutoff=ctx.cutoff, _context=ctx, base_provenance=provenance,
        _source_payload_reader=_source_payload_reader, _source_receipts=_source_receipts)
    if short_horizon_source_identity() != identity:
        raise ValueError('SHORT_HORIZON_R2_IDENTITY_CHANGED_DURING_EVALUATION')
    return result


run_short_horizon_strength_r2 = run_short_horizon_strength
