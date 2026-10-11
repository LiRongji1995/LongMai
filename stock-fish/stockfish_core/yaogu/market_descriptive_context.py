"""Explicit original-byte market facts, independent of every stock selector.

Only a live native SourceMarketStream grants access. Saved JSON is an artifact,
not a source capability. RAW decimals, complete exchange-session windows and
actual capture clocks remain authoritative; unknown values are never zero fill.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from .contracts import MarketInput, digest
from .evidence_audit_spool import _RowJournal, _file_hash
from .history_artifacts import digest_json_stream, digest_market_input
from .research_context_artifacts import SourceArtifactReader, MARKET_SCOPE, RETURN_BASIS
from .short_horizon_participation import _source_parent, _source_payload
from .theme_evolution import encoded, median, quantile
from .theme_market_stream import SourceMarketStream
from .visibility import CLOCKS, stamp

VERSION = 'MARKET_DESCRIPTIVE_CONTEXT_V1'
FEATURE_VERSION = 'WHOLE_MARKET_DESCRIPTIVE_TREND_EXACT_NATIVE_V1'
SPEC_REVISION = 'MARKET_DESCRIPTIVE_CONTEXT_SPEC_1'
PROVIDER_FIELD_VERSION = 'BAOSTOCK_00.9.30_R03_V1'
TURNOVER_BASIS = 'BAOSTOCK_TURN_PERCENT_FLOAT_SHARES'
FORMULA_SPEC = {
    'market_scope': MARKET_SCOPE, 'return_basis': RETURN_BASIS,
    'return_1d': 'RAW close / RAW preclose - 1; all trading rows required',
    'rising_fraction': 'count(return_1d > 0) / trading_count',
    'quantile': 'TYPE7 exact linear interpolation p * (N - 1)',
    'top10_share': 'sum(largest min(10, N) trading RAW amounts) / total',
    'prior20': 'exact median of T-20 through T-1 observed exchange sessions',
    'amount_growth': 'M(T) / M(previous exchange session) - 1',
    'other_daily_changes': 'current value - previous exchange session value',
    'turn_percent': 'registered same-parent BaoStock turn; 0 <= percent <= 1000',
    'turn5': 'descriptive count(turn percent >= 5), not an R2 gate',
    'decimal_guard': 'finite decimal, text <= 256 characters, abs(exponent) <= 128',
    'numeric_authority': 'authenticated RAW JSON exact decimals; panel identity separately checked',
}
_ROOT = Path(__file__).resolve().parents[2]
_SPEC = _ROOT / 'docs' / 'MARKET_DESCRIPTIVE_CONTEXT_V1.md'
_ISSUED = object()


def source_identity():
    """Bind the new formula/spec/implementation without changing old identities."""
    paths = [Path(__file__), _SPEC]
    paths.extend(Path(__file__).with_name(name) for name in (
        'theme_market_stream.py', 'research_context_artifacts.py',
        'short_horizon_participation.py', 'short_horizon_strength.py',
        'theme_evolution.py', 'evidence_audit_spool.py', 'history_artifacts.py',
        'contracts.py', 'visibility.py', 'provider_normalization.py',
        'provider_requests.py', 'provider_requests_worker.py'))
    hashes = {str(path.relative_to(_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in paths}
    identity = {'implementation_version': VERSION, 'feature_version': FEATURE_VERSION,
                'spec_revision': SPEC_REVISION, 'formula_spec': deepcopy(FORMULA_SPEC),
                'capability_integrity_version': 'ISSUED_SOURCE_AND_FILE_REGISTRY_DIGESTS_V1',
                'provider_field_version': PROVIDER_FIELD_VERSION,
                'source_file_hashes': hashes}
    return {**identity, 'source_hash': digest_json_stream(identity)}


def _number(value):
    """Parse bounded finite decimal input without floats or global digit changes."""
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    text = str(value)
    if not text.strip() or len(text) > 256:
        return None
    try:
        decimal = Decimal(text)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not decimal.is_finite() or abs(decimal.as_tuple().exponent) > 128:
        return None
    return Fraction(decimal)


def _decoded(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _decoded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decoded(item) for item in value]
    return value


def _original_payload(reader, receipt):
    """Keep frozen envelope/SHA guards, then decode actual numeric bytes exactly."""
    standard = _source_payload(receipt, reader)
    raw = SourceArtifactReader.__call__(reader, receipt.receipt_id)
    def invalid_constant(value):
        raise ValueError('MARKET_CONTEXT_NONFINITE_JSON_NUMBER:' + value)
    exact_payload = json.loads(raw, parse_float=Decimal, parse_constant=invalid_constant)
    if stamp(exact_payload['first_seen_at']) != receipt.first_seen_at:
        raise ValueError('MARKET_CONTEXT_ORIGINAL_CAPTURE_CLOCK_MISMATCH')
    return standard, exact_payload


def _axis(value, reasons=()):
    return {'status': 'AVAILABLE' if value is not None else 'UNKNOWN',
            'reason_codes': [] if value is not None else list(reasons)}


def _unknown(day, reasons):
    fields = ('market_amount_cny', 'rising_count', 'falling_count', 'unchanged_count',
              'rising_fraction', 'median_return_1d', 'return_q25', 'return_q75',
              'return_iqr', 'median_trading_amount_cny', 'top10_trading_amount_share',
              'median_turn_percent', 'turn_at_least_5_percent_count')
    return {'business_session': day, 'status': 'UNKNOWN', 'reason_codes': list(reasons),
            'source_panel_hash': None, 'source_refs': [], 'source_content_hashes': {},
            'source_clocks': {}, 'panel_clocks': None, 'market_scope': MARKET_SCOPE,
            'expected_instrument_count': None, 'trading_count': None,
            'known_nontrading_count': None, 'expected_population_hash': None,
            'trading_population_hash': None, 'nontrading_population_hash': None,
            'turn_valid_count': None, 'turn_missing_count': None,
            'turn_coverage_complete': False, 'turn_basis': TURNOVER_BASIS,
            **{key: None for key in fields},
            'axis_status': {key: _axis(None, reasons) for key in fields}}


def _session_facts(stream, reader, resolver, day, descriptor, journal, sources):
    panel = SourceMarketStream.panel(stream, day)
    if panel is None:
        reason = descriptor['missing_sessions'].get(day, {}).get('reason_codes')
        return _unknown(day, reason or ['MARKET_CONTEXT_SESSION_NOT_REQUESTED_IN_SOURCE_CAPABILITY'])
    expected_panel = descriptor['panels'][day]
    if (panel.scope != MARKET_SCOPE or panel.return_basis != RETURN_BASIS
            or str(panel.session) != day or panel.status != 'COMPLETE'
            or not panel.pagination_complete or panel.nontrading_policy != 'NONTRADING_EXCLUDED'
            or digest_json_stream(panel.model_dump(mode='json')) != expected_panel['panel_hash']):
        raise ValueError('MARKET_CONTEXT_PANEL_SCOPE_OR_IDENTITY_MISMATCH')
    parents = tuple(_source_parent(resolver, day, dataset) for dataset in ('market_daily', 'master_all'))
    if tuple(parent.receipt_id for parent in parents) != panel.source_refs:
        raise ValueError('MARKET_CONTEXT_PARENT_IDENTITY_MISMATCH')
    payloads = {}
    for parent in parents:
        if (parent.provider.lower() != 'baostock' or parent.field_version != PROVIDER_FIELD_VERSION
                or str(parent.parameters.get('date')) != day
                or ('adjustflag' in parent.parameters and str(parent.parameters['adjustflag']) != '3')):
            raise ValueError('MARKET_CONTEXT_PROVIDER_DATE_OR_ADJUSTMENT_UNRECOGNIZED')
        standard, exact_payload = _original_payload(reader, parent)
        payloads[parent.dataset] = (standard, exact_payload)
        original = {'receipt': parent.model_dump(mode='json'),
                    'original_path': str(reader._paths[parent.receipt_id].resolve()),
                    'raw_sha256': parent.content_hash}
        if parent.receipt_id in sources and sources[parent.receipt_id] != original:
            raise ValueError('MARKET_CONTEXT_SOURCE_IDENTITY_CHANGED')
        sources[parent.receipt_id] = original
        for index, row in enumerate(exact_payload['rows']):
            audit = {'role': 'AUTHENTICATED_RAW_DECODED_DECIMAL_EXACT_NOT_SELECTOR_ALLOWED_ROW',
                     'dataset': parent.dataset, 'business_session': day,
                     'source_ref': parent.receipt_id, 'source_content_hash': parent.content_hash,
                     'raw_row_index': index, 'raw_row': _decoded(row)}
            journal[digest(audit)] = audit
    clocks = {key: max(getattr(parent, key) for parent in parents) for key in CLOCKS}
    if any(getattr(panel.clocks, key) != value for key, value in clocks.items()):
        raise ValueError('MARKET_CONTEXT_PANEL_CLOCK_MISMATCH')
    standard, exact_payload = payloads['market_daily']
    raw_rows, standard_rows = exact_payload['rows'], standard['rows']
    bars = {bar.instrument_id: bar for bar in panel.rows}
    if len(bars) != len(panel.rows):
        raise ValueError('MARKET_CONTEXT_TRADING_POPULATION_DUPLICATE')
    seen, trading, nontrading = set(), set(), set()
    amounts, returns, turns = [], [], []
    amount_unknown, price_unknown, turn_unknown = [], [], []
    price_fields = {'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close',
                    'reference_prev_close': 'preclose', 'volume_shares': 'volume', 'amount_cny': 'amount'}
    for raw, native_raw in zip(raw_rows, standard_rows, strict=True):
        code = raw['code']; iid = code[3:] + '.' + code[:2].upper()
        if (code != native_raw['code'] or str(raw.get('date')) != day
                or str(raw.get('adjustflag')) != '3' or iid in seen):
            raise ValueError('MARKET_CONTEXT_RAW_ROW_IDENTITY_DATE_BASIS_MISMATCH')
        seen.add(iid)
        if str(raw.get('tradestatus')) == '0':
            for field in ('amount', 'volume'):
                if raw.get(field) not in (None, '') and _number(raw[field]) != 0:
                    raise ValueError('MARKET_CONTEXT_NONTRADING_TRANSACTION_DECIMAL_CONFLICT')
            nontrading.add(iid)
            continue
        if str(raw.get('tradestatus')) != '1' or iid not in bars:
            raise ValueError('MARKET_CONTEXT_RAW_TRADING_IDENTITY_MISMATCH')
        trading.add(iid); bar = bars[iid]
        if (str(bar.session) != day or bar.source_refs != panel.source_refs
                or any(getattr(bar, key) != value for key, value in clocks.items())):
            raise ValueError('MARKET_CONTEXT_BAR_PARENT_DATE_CLOCK_MISMATCH')
        lost_fields = set()
        for field, raw_field in price_fields.items():
            original, native = _number(raw.get(raw_field)), _number(native_raw.get(raw_field))
            if _number(getattr(bar, field)) != native:
                raise ValueError('MARKET_CONTEXT_PANEL_RAW_PRICE_IDENTITY_MISMATCH:' + field)
            if original != native:
                lost_fields.add(raw_field)
        amount = _number(raw.get('amount'))
        prices = {field: _number(raw.get(field)) for field in ('open', 'high', 'low', 'close', 'preclose')}
        if amount is None or amount < 0 or 'amount' in lost_fields:
            amount_unknown.append('MARKET_CONTEXT_RAW_AMOUNT_DECIMAL_UNAVAILABLE_OR_NATIVE_PRECISION_LOSS')
        else:
            amounts.append(amount)
        if (any(value is None or value <= 0 for value in prices.values())
                or lost_fields.intersection(prices)
                or not prices['low'] <= min(prices['open'], prices['close'])
                    <= max(prices['open'], prices['close']) <= prices['high']):
            price_unknown.append('MARKET_CONTEXT_RAW_RETURN_DECIMAL_UNAVAILABLE_OR_NATIVE_PRECISION_LOSS')
        else:
            returns.append(prices['close'] / prices['preclose'] - 1)
        turn = _number(raw.get('turn'))
        if turn is None or not 0 <= turn <= 1000:
            turn_unknown.append('MARKET_CONTEXT_REGISTERED_TURN_PERCENT_MISSING_OR_INVALID')
        else:
            turns.append(turn)
    if (seen != set(panel.expected_instrument_ids) or trading != set(bars)
            or nontrading != set(panel.nontrading_instrument_ids)
            or trading & nontrading or seen != trading | nontrading):
        raise ValueError('MARKET_CONTEXT_RAW_PANEL_POPULATION_MISMATCH')
    empty = not trading
    empty_reason = ['MARKET_CONTEXT_EMPTY_TRADING_POPULATION']
    amount_reasons = sorted(set(amount_unknown)) or empty_reason
    return_reasons = sorted(set(price_unknown)) or empty_reason
    turn_reasons = sorted(set(turn_unknown)) or empty_reason
    total = sum(amounts, Fraction(0)) if not empty and not amount_unknown else None
    known_returns = bool(trading) and not price_unknown
    pos = sum(value > 0 for value in returns) if known_returns else None
    neg = sum(value < 0 for value in returns) if known_returns else None
    flat = sum(value == 0 for value in returns) if known_returns else None
    q25 = quantile(returns, Fraction(1, 4)) if known_returns else None
    q75 = quantile(returns, Fraction(3, 4)) if known_returns else None
    top10 = sum(sorted(amounts, reverse=True)[:10], Fraction(0)) / total if total is not None and total > 0 else None
    values = {'market_amount_cny': total, 'rising_count': pos, 'falling_count': neg,
              'unchanged_count': flat, 'rising_fraction': Fraction(pos, len(trading)) if known_returns else None,
              'median_return_1d': median(returns) if known_returns else None,
              'return_q25': q25, 'return_q75': q75,
              'return_iqr': q75 - q25 if known_returns else None,
              'median_trading_amount_cny': median(amounts) if total is not None else None,
              'top10_trading_amount_share': top10,
              'median_turn_percent': median(turns) if trading and not turn_unknown else None,
              'turn_at_least_5_percent_count': sum(value >= 5 for value in turns) if trading and not turn_unknown else None}
    axes = {}
    for key, value in values.items():
        reasons = (turn_reasons if key.startswith(('turn_', 'median_turn_')) else amount_reasons
                   if key in ('market_amount_cny', 'median_trading_amount_cny', 'top10_trading_amount_share') else return_reasons)
        if key == 'top10_trading_amount_share' and total == 0:
            reasons = ['MARKET_CONTEXT_ZERO_AMOUNT_DENOMINATOR']
        axes[key] = _axis(value, reasons)
    return {'business_session': day, 'status': 'AVAILABLE' if total is not None and known_returns else 'PARTIAL',
            'reason_codes': sorted(set(amount_unknown + price_unknown + (empty_reason if empty else []))),
            'source_panel_hash': expected_panel['panel_hash'], 'source_refs': list(panel.source_refs),
            'source_content_hashes': dict(expected_panel['source_content_hashes']),
            'source_clocks': {p.receipt_id: {key: getattr(p, key).isoformat() for key in CLOCKS} for p in parents},
            'panel_clocks': {key: value.isoformat() for key, value in clocks.items()},
            'market_scope': MARKET_SCOPE, 'expected_instrument_count': len(seen),
            'trading_count': len(trading), 'known_nontrading_count': len(nontrading),
            'expected_population_hash': digest_json_stream(sorted(seen)),
            'trading_population_hash': digest_json_stream(sorted(trading)),
            'nontrading_population_hash': digest_json_stream(sorted(nontrading)),
            'turn_valid_count': len(turns), 'turn_missing_count': len(turn_unknown),
            'turn_coverage_complete': bool(trading) and not turn_unknown, 'turn_basis': TURNOVER_BASIS,
            **values, 'axis_status': axes}


def _with_trends(day, facts, calendar):
    current = deepcopy(facts[day]); index = calendar.index(day)
    previous_day = calendar[index - 1] if index else None
    previous = facts.get(previous_day)
    window = list(calendar[max(0, index - 20):index])
    complete = len(window) == 20 and all(facts[d]['market_amount_cny'] is not None for d in window)
    baseline = median([facts[d]['market_amount_cny'] for d in window]) if complete else None
    amount = current['market_amount_cny']
    ratio = amount / baseline if amount is not None and baseline is not None and baseline > 0 else None
    current.update(previous_exchange_session=previous_day, prior20_sessions=window,
        prior20_available_count=sum(facts[d]['market_amount_cny'] is not None for d in window),
        prior20_window_complete=complete, prior20_excludes_observation=True,
        prior20_market_amount_median_cny=baseline, amount_ratio_to_prior20_median=ratio,
        trading_population_changed_from_previous=(current['trading_population_hash'] != previous['trading_population_hash'])
            if previous and current['trading_population_hash'] and previous['trading_population_hash'] else None)
    reasons = ['MARKET_CONTEXT_PRIOR20_LEFT_CENSORED' if len(window) < 20 else 'MARKET_CONTEXT_PRIOR20_MISSING_SESSION']
    current['axis_status']['prior20_market_amount_median_cny'] = _axis(baseline, reasons)
    current['axis_status']['amount_ratio_to_prior20_median'] = _axis(ratio,
        ['MARKET_CONTEXT_ZERO_PRIOR20_DENOMINATOR'] if baseline == 0 else
        reasons if baseline is None else current['axis_status']['market_amount_cny']['reason_codes'])
    growth = (amount / previous['market_amount_cny'] - 1 if previous and amount is not None
              and previous['market_amount_cny'] is not None and previous['market_amount_cny'] > 0 else None)
    current['amount_growth_one_exchange_session'] = growth
    current['axis_status']['amount_growth_one_exchange_session'] = _axis(growth,
        ['MARKET_CONTEXT_ADJACENT_SESSION_OR_POSITIVE_AMOUNT_DENOMINATOR_UNAVAILABLE'])
    for key in ('rising_fraction', 'top10_trading_amount_share', 'median_return_1d', 'return_iqr'):
        value = current[key] - previous[key] if previous and current[key] is not None and previous[key] is not None else None
        change = key + '_change_one_exchange_session'
        current[change] = value
        current['axis_status'][change] = _axis(value, ['MARKET_CONTEXT_ADJACENT_SESSION_AXIS_UNAVAILABLE'])
    return current


def _terminal_validate(stream, bundle, cutoff, descriptor, identity, sources):
    """Re-read every consumed RAW original, not just the result JSON."""
    if type(stream) is not SourceMarketStream or type(stream._reader) is not SourceArtifactReader:
        raise ValueError('MARKET_CONTEXT_AUTHENTICATED_NATIVE_SOURCE_REQUIRED')
    SourceMarketStream.validate_binding(stream, bundle, cutoff)
    if stream.descriptor != descriptor or source_identity() != identity:
        raise ValueError('MARKET_CONTEXT_SOURCE_OR_IMPLEMENTATION_CHANGED')
    reader = stream._reader
    receipt_hash = digest_json_stream([r.model_dump(mode='json') for r in reader.receipts])
    if receipt_hash != descriptor['source_receipt_identity_hash']:
        raise ValueError('MARKET_CONTEXT_RECEIPT_INVENTORY_CHANGED')
    for ref, original in sources.items():
        parent = reader._receipts.get(ref)
        if (parent is None or parent.model_dump(mode='json') != original['receipt']
                or str(reader._paths[ref].resolve()) != original['original_path']):
            raise ValueError('MARKET_CONTEXT_CONSUMED_RECEIPT_OR_PATH_CHANGED')
        _original_payload(reader, parent)
    SourceMarketStream.validate_binding(stream, bundle, cutoff)
    if source_identity() != identity:
        raise ValueError('MARKET_CONTEXT_IMPLEMENTATION_CHANGED_AT_TERMINAL')


def _write(path, document):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(document, handle, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)
        handle.write('\n')
    return {'path': str(path.resolve()), 'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


@dataclass(frozen=True, repr=False)
class MarketDescriptiveContext:
    """Issued native output capability; a saved manifest cannot construct one."""
    _context: dict
    _hash: str
    _stream: SourceMarketStream
    _bundle: MarketInput
    _cutoff: datetime
    _sources: dict
    _files: dict
    _source_registry_hash: str
    _file_registry_hash: str

    def __init__(self, token, *, context, stream, bundle, cutoff, sources, files):
        if token is not _ISSUED:
            raise ValueError('MARKET_CONTEXT_NATIVE_PRODUCER_CAPABILITY_REQUIRED')
        issued_sources, issued_files = deepcopy(sources), deepcopy(files)
        if issued_sources != context['consumed_sources']:
            raise ValueError('MARKET_CONTEXT_ISSUED_SOURCE_REGISTRY_MISMATCH')
        for name, value in {'_context': deepcopy(context), '_hash': digest_json_stream(context),
                '_stream': stream, '_bundle': bundle, '_cutoff': cutoff,
                '_sources': issued_sources, '_files': issued_files,
                '_source_registry_hash': digest_json_stream(issued_sources),
                '_file_registry_hash': digest_json_stream(issued_files)}.items():
            object.__setattr__(self, name, value)
        self._assert_registries()

    def _assert_registries(self):
        """Frozen attributes do not freeze nested registries; seal them explicitly."""
        if (digest_json_stream(self._sources) != self._source_registry_hash
                or self._sources != self._context['consumed_sources']):
            raise ValueError('MARKET_CONTEXT_CONSUMED_SOURCE_REGISTRY_CHANGED')
        audit_files = self._context['raw_field_audit_manifest']['files']
        expected = {'context', 'source_descriptor', 'consumed_sources', 'manifest',
                    'raw-field-audit/manifest.json'} | {'raw-field-audit/' + name for name in audit_files}
        if digest_json_stream(self._files) != self._file_registry_hash or set(self._files) != expected:
            raise ValueError('MARKET_CONTEXT_SAVED_FILE_REGISTRY_CHANGED')
        for name, original in audit_files.items():
            issued = self._files['raw-field-audit/' + name]
            if issued['sha256'] != original['sha256'] or issued['bytes'] != original['bytes']:
                raise ValueError('MARKET_CONTEXT_JOURNAL_REGISTRY_CONTENT_MISMATCH')

    @property
    def context(self):
        return deepcopy(self._context)

    @property
    def context_hash(self):
        return self._hash

    @property
    def source_identity(self):
        return deepcopy(self._context['source_identity'])

    @property
    def artifact_files(self):
        return deepcopy(self._files)

    def validate_binding(self, bundle, cutoff):
        if (digest_market_input(bundle) != self._context['source_input_hash']
                or stamp(cutoff) != self._cutoff or digest_json_stream(self._context) != self._hash):
            raise ValueError('MARKET_CONTEXT_OUTPUT_BINDING_MISMATCH')
        self._assert_registries()
        _terminal_validate(self._stream, bundle, cutoff, self._context['market_stream_descriptor'],
                           self._context['source_identity'], self._sources)
        for meta in self._files.values():
            path = Path(meta['path'])
            if path.stat().st_size != meta['bytes'] or _file_hash(path) != meta['sha256']:
                raise ValueError('MARKET_CONTEXT_SAVED_ARTIFACT_CHANGED')
        self._assert_registries()
        # A large journal may take time to hash. Originals can change during
        # that work, so file verification must not be the last source check.
        _terminal_validate(self._stream, bundle, cutoff, self._context['market_stream_descriptor'],
                           self._context['source_identity'], self._sources)
        self._assert_registries()
        if digest_json_stream(self._context) != self._hash:
            raise ValueError('MARKET_CONTEXT_OUTPUT_BINDING_MISMATCH')


def validate_market_descriptive_context(context, *, bundle, cutoff):
    if type(context) is not MarketDescriptiveContext:
        raise ValueError('MARKET_CONTEXT_NATIVE_OUTPUT_CAPABILITY_REQUIRED')
    context.validate_binding(bundle, cutoff)


def run_market_descriptive_context(bundle: MarketInput, *, cutoff, market_stream,
                                   sessions, evidence_directory):
    """Save independent exact descriptive facts and return their native capability."""
    if type(market_stream) is not SourceMarketStream or type(market_stream._reader) is not SourceArtifactReader:
        raise ValueError('MARKET_CONTEXT_AUTHENTICATED_NATIVE_SOURCE_REQUIRED')
    cutoff = stamp(cutoff)
    SourceMarketStream.validate_binding(market_stream, bundle, cutoff)
    descriptor, identity = market_stream.descriptor, source_identity()
    if descriptor['return_basis'] != RETURN_BASIS:
        raise ValueError('MARKET_CONTEXT_REGISTERED_REFERENCE_RETURN_BASIS_REQUIRED')
    calendar = tuple(descriptor['full_observed_calendar'])
    requested = tuple(str(day) for day in sessions)
    if (not requested or len(set(requested)) != len(requested)
            or any(day not in calendar for day in requested)
            or requested != tuple(day for day in calendar if day in set(requested))):
        raise ValueError('MARKET_CONTEXT_REQUEST_NOT_ORDERED_OBSERVED_CALENDAR')
    needed = set()
    for day in requested:
        index = calendar.index(day); needed.update(calendar[max(0, index - 20):index + 1])
    needed = tuple(day for day in calendar if day in needed)
    reader = market_stream._reader
    resolver = reader._resolver(bundle, cutoff)
    directory = Path(evidence_directory)
    directory.mkdir(parents=True, exist_ok=False)
    journal = _RowJournal(directory / 'raw-field-audit')
    sources, facts, files = {}, {}, {}
    try:
        for day in needed:
            facts[day] = _session_facts(market_stream, reader, resolver, day, descriptor, journal, sources)
        rows = [_with_trends(day, facts, calendar) for day in requested]
        journal.close()
        _terminal_validate(market_stream, bundle, cutoff, descriptor, identity, sources)
        context = {'schema_version': VERSION, 'feature_version': FEATURE_VERSION,
            'spec_revision': SPEC_REVISION, 'source_identity': identity,
            'source_input_hash': digest_market_input(bundle), 'observation_date': str(bundle.observation_date),
            'decision_cutoff': cutoff.isoformat(), 'coverage_type': bundle.coverage_type,
            'market_stream_descriptor': descriptor, 'market_stream_descriptor_hash': market_stream.descriptor_hash,
            'full_observed_calendar': list(calendar), 'requested_sessions': list(requested),
            'dependency_sessions': list(needed), 'dependency_session_facts': encoded([facts[d] for d in needed]),
            'rows': encoded(rows), 'consumed_sources': sources, 'raw_field_audit_manifest': journal.sealed,
            'raw_field_audit_role': 'COMPLETE_AUTHENTICATED_DECODED_RAW_ROWS_NOT_SELECTOR_ALLOWED_ROWS',
            'formula_spec': deepcopy(FORMULA_SPEC), 'parameter_status': 'INITIAL_UNVALIDATED_DESCRIPTIVE_NOT_PREDICTIVE_RULE',
            'units': {'market_amount_cny': 'CNY', 'median_trading_amount_cny': 'CNY',
                'prior20_market_amount_median_cny': 'CNY', 'rising_count': 'INSTRUMENT_COUNT',
                'falling_count': 'INSTRUMENT_COUNT', 'unchanged_count': 'INSTRUMENT_COUNT',
                'median_turn_percent': 'PERCENT', 'turn_at_least_5_percent_count': 'INSTRUMENT_COUNT',
                'rising_fraction': 'FRACTION', 'top10_trading_amount_share': 'FRACTION',
                'median_return_1d': 'RETURN_FRACTION', 'return_q25': 'RETURN_FRACTION',
                'return_q75': 'RETURN_FRACTION', 'return_iqr': 'RETURN_FRACTION',
                'amount_ratio_to_prior20_median': 'RATIO', 'amount_growth_one_exchange_session': 'RETURN_FRACTION'},
            'scope_role': 'FOUR_BOARD_CODE_FAMILY_MARKET_NOT_HISTORICAL_STRATEGY_ELIGIBILITY',
            'return_role': 'RAW_REFERENCE_DAILY_RETURN_DISTRIBUTION_NOT_INDEX_OR_ADJUSTED_BENCHMARK',
            'capture_role': 'ACTUAL_CAPTURE_CLOCKS_NOT_HISTORICAL_FIRST_SEEN',
            'historical_strategy_qualification': 'UNKNOWN', 'official_pit_validation_count': 0,
            'authenticated_emotion_cycle_count': 0, 'market_edge': 'NOT_ESTABLISHED',
            'selection_gate': False, 'old_runs_changed': False, 'database_opened': False, 'network_calls': 0}
        context_hash = digest_json_stream(context)
        files['context'] = _write(directory / 'context.json', context)
        files['source_descriptor'] = _write(directory / 'source-descriptor.json', descriptor)
        files['consumed_sources'] = _write(directory / 'consumed-sources.json', sources)
        for name, meta in journal.sealed['files'].items():
            files['raw-field-audit/' + name] = {**meta, 'path': str((journal.directory / name).resolve())}
        path = journal.directory / 'manifest.json'
        files['raw-field-audit/manifest.json'] = {'path': str(path.resolve()), 'bytes': path.stat().st_size,
                                                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        files['manifest'] = _write(directory / 'manifest.json', {
            'schema_version': VERSION, 'status': 'SAVED_NATIVE_DESCRIPTIVE_CONTEXT',
            'context_hash': context_hash, 'source_identity': identity,
            'market_stream_descriptor_hash': market_stream.descriptor_hash, 'files': deepcopy(files),
            'actual_cutoff': cutoff.isoformat(), 'requested_count': len(requested),
            'dependency_count': len(needed), 'consumed_source_count': len(sources),
            'official_pit_validation_count': 0, 'historical_strategy_qualification': 'UNKNOWN',
            'market_edge': 'NOT_ESTABLISHED', 'selection_gate': False})
        capability = MarketDescriptiveContext(_ISSUED, context=context, stream=market_stream,
            bundle=bundle, cutoff=cutoff, sources=sources, files=files)
        capability.validate_binding(bundle, cutoff)  # Actual RAW terminal check after saving.
        return capability
    except BaseException as error:
        close_error = None
        try:
            journal.close()
        except Exception as audit_error:
            close_error = str(audit_error)
        _write(directory / 'failure.json', {'status': 'FAILED', 'error_type': type(error).__name__,
            'reason': str(error), 'completed_dependency_sessions': list(facts), 'files': files,
            'audit_close_error': close_error, 'output_capability_issued': False})
        raise
