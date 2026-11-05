"""Dated theme evolution context; no new database or selection policy.

An additional market pack is a source layer, not a replacement MarketInput.
The native caller supplies a validator capability that authenticates original
payload bytes and dated security scope. A JSON ``verified`` flag grants none.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from fractions import Fraction
import hashlib
import math
from pathlib import Path
import re

import yaml
from pydantic import model_validator

from .contracts import Clocked, DailyBar, Frozen, MarketInput, SourceReceipt, ThemeSnapshot, digest
from .history_artifacts import digest_json_stream, digest_market_input
from .visibility import EvidenceResolver, SH, clock_visible, stamp

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / 'config/theme_evolution_r1.yaml'
CONFIG = yaml.safe_load(CONFIG_PATH.read_text(encoding='utf-8'))
STRATEGY_ID = CONFIG['strategy_id']
FEATURE_VERSION = CONFIG['feature_version']
MARKET_SCOPE = CONFIG['market_scope']
DECIMAL_CHUNK_DIGITS = 1000
INTEGER_JSON_SAFE_BITS = 12000
EXACT_NUMERIC_ENCODING = 'FRACTION_INTS_OR_DECIMAL_STRINGS_V2'


class ThemeMarketSession(Frozen):
    session: date
    scope: str
    expected_instrument_ids: tuple[str, ...]
    rows: tuple[DailyBar, ...]
    source_refs: tuple[str, ...]
    clocks: Clocked
    status: str
    pagination_complete: bool
    return_basis: str | None = None
    nontrading_instrument_ids: tuple[str, ...] = ()
    nontrading_policy: str | None = None
    amount_cny: str | int | float | None = None  # Never used as numeric authority.


class ThemeMarketPack(Frozen):
    schema_version: str
    source_input_hash: str
    observation_date: date
    decision_cutoff: str
    coverage_type: str
    receipts: tuple[SourceReceipt, ...]
    sessions: tuple[ThemeMarketSession, ...]
    themes: tuple[ThemeSnapshot, ...] = ()

    @model_validator(mode='after')
    def unique_sessions(self):
        if len({row.session for row in self.sessions}) != len(self.sessions):
            raise ValueError('THEME_MARKET_PACK_DUPLICATE_SESSION')
        return self


def _decimal_integer_text(value):
    """Canonical decimal output without str() on an unbounded integer."""
    sign, remaining = ('-' if value < 0 else ''), abs(value)
    base, pieces = 10 ** DECIMAL_CHUNK_DIGITS, []
    while remaining:
        remaining, piece = divmod(remaining, base)
        pieces.append(str(piece))
    if not pieces:
        return '0'
    return sign + pieces[-1] + ''.join(piece.zfill(DECIMAL_CHUNK_DIGITS) for piece in reversed(pieces[:-1]))


def _decimal_integer(value):
    """Parse canonical integer fields in bounded decimal chunks."""
    if isinstance(value, bool):
        raise ValueError('BOOLEAN_IS_NOT_EXACT_INTEGER')
    if isinstance(value, int):
        return value
    if not isinstance(value, str) or re.fullmatch(r'-?(?:0|[1-9][0-9]*)', value) is None:
        raise ValueError('CANONICAL_DECIMAL_INTEGER_REQUIRED')
    sign, digits = (-1, value[1:]) if value.startswith('-') else (1, value)
    result = 0
    for index in range(0, len(digits), DECIMAL_CHUNK_DIGITS):
        chunk = digits[index:index + DECIMAL_CHUNK_DIGITS]
        result = result * (10 ** len(chunk)) + int(chunk)
    return sign * result


def _encoded_integer(value):
    # 12000 bits is fewer than 3614 decimal digits, below Python's default
    # 4300-digit conversion limit. Larger values use exact text, not rounding.
    return value if abs(value).bit_length() <= INTEGER_JSON_SAFE_BITS else _decimal_integer_text(value)


def exact(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, Fraction):
            return value
        if isinstance(value, dict):
            n, d = value['numerator'], value['denominator']
            return Fraction(_decimal_integer(n), _decimal_integer(d))
        return Fraction(str(value))
    except (ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError):
        return None


def encoded(value):
    if isinstance(value, Fraction):
        return {'numerator': _encoded_integer(value.numerator), 'denominator': _encoded_integer(value.denominator)}
    if isinstance(value, int) and not isinstance(value, bool):
        return _encoded_integer(value)
    if isinstance(value, dict):
        return {key: encoded(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [encoded(item) for item in value]
    return value


def median(values):
    ordered = sorted(values)
    size = len(ordered)
    return (ordered[size // 2] if size % 2 else
            (ordered[size // 2 - 1] + ordered[size // 2]) / 2) if size else None


def quantile(values, probability):
    """Exact linear interpolation at p*(N-1); no rounded display input."""
    ordered = sorted(values)
    if not ordered:
        return None
    index = exact(probability) * (len(ordered) - 1)
    left = index.numerator // index.denominator
    weight = index - left
    return ordered[left] + weight * (ordered[min(left + 1, len(ordered) - 1)] - ordered[left])


def _condition(*checks):
    return 'FAIL' if False in checks else 'UNKNOWN' if None in checks else 'PASS'


def _a_common_id(iid):
    return isinstance(iid, str) and re.fullmatch(r'(?:(?:60|68)\d{4}\.SH|(?:00|30)\d{4}\.SZ)', iid) is not None


def _compare(value, threshold, operator):
    value, threshold = exact(value), exact(threshold)
    return None if value is None else {'ge': value >= threshold, 'gt': value > threshold,
        'le': value <= threshold, 'lt': value < threshold}[operator]


def evaluate_day(features, previous=None, history=(), previous_stage=None):
    """Pure exact state/risk evaluation of already source-qualified facts."""
    previous = previous or {}
    e, b = features.get('expansion'), features.get('breadth')
    rs3, rs5 = features.get('rs3'), features.get('rs5')
    em, st, wk = CONFIG['emerging'], CONFIG['strengthening'], CONFIG['weakening']
    states = {
        'emerging': _condition(_compare(e, em['expansion_min_inclusive'], 'ge'),
            _compare(rs3, em['rs3_min_exclusive'], 'gt'), _compare(b, em['breadth_min_inclusive'], 'ge')),
        'strengthening': _condition(_compare(e, st['expansion_min_inclusive'], 'ge'),
            _compare(rs5, st['rs5_min_inclusive'], 'ge'), _compare(b, st['breadth_min_inclusive'], 'ge')),
        'weakening': _condition(_compare(rs3, wk['rs3_max_exclusive'], 'lt'),
            _compare(b, wk['breadth_max_inclusive'], 'le')),
    }
    prior_states = previous.get('candidate_states') or {}
    continuous = bool(features.get('previous_exchange_session') and
                      previous.get('session') == features['previous_exchange_session'])
    confirmed = {key: _condition(state == 'PASS' if state != 'UNKNOWN' else None,
        prior_states.get(key) == 'PASS' if continuous and prior_states.get(key) in ('PASS', 'FAIL') else None)
        for key, state in states.items()}
    breadth, prior_b = exact(b), exact(previous.get('breadth'))
    momentum, prior_m = exact(rs5), exact(previous.get('rs5'))
    weakening_after_strength = (continuous and previous_stage in ('STRENGTHENING', 'DIVERGENCE')
        and breadth is not None and prior_b is not None and momentum is not None and prior_m is not None
        and (breadth < prior_b or momentum < prior_m))
    if confirmed['weakening'] == 'PASS':
        stage = 'WEAKENING'
    elif weakening_after_strength:
        stage = 'DIVERGENCE'
    elif confirmed['strengthening'] == 'PASS':
        stage = 'STRENGTHENING'
    elif confirmed['emerging'] == 'PASS':
        stage = 'EMERGING'
    elif states['weakening'] == 'PASS':
        stage = 'WEAKENING_PENDING'
    elif states['strengthening'] == 'PASS':
        stage = 'STRENGTHENING_PENDING'
    elif states['emerging'] == 'PASS':
        stage = 'EMERGING_PENDING'
    else:
        stage = 'UNKNOWN' if any(exact(features.get(key)) is None for key in
                                ('expansion', 'breadth', 'rs3', 'rs5')) else 'MIXED_PENDING'

    risk = CONFIG['high_risk']
    prior60 = list(history)[-risk['share_prior_sessions']:]
    prior5 = list(history)[-risk['breadth_peak_prior_sessions']:]
    shares = [exact(row.get('share')) for row in prior60]
    breadths = [exact(row.get('breadth')) for row in prior5]
    p90 = quantile(shares, risk['share_quantile']) if len(shares) == risk['share_prior_sessions'] and all(x is not None for x in shares) else None
    peak = max(breadths) if len(breadths) == risk['breadth_peak_prior_sessions'] and all(x is not None for x in breadths) else None
    drop = peak - breadth if peak is not None and breadth is not None else None
    price_weak = _compare(features.get('theme_return_1d'), risk['price_daily_return_max_inclusive'], 'le')
    current_rs3, prior_rs3 = exact(rs3), exact(previous.get('rs3'))
    rs_weak = current_rs3 < prior_rs3 if continuous and current_rs3 is not None and prior_rs3 is not None else None
    weak_price_or_rs = True if True in (price_weak, rs_weak) else None if None in (price_weak, rs_weak) else False
    risk_parts = {'high_share': _compare(features.get('share'), p90, 'ge') if p90 is not None else None,
        'breadth_decline': _compare(drop, risk['breadth_drop_min_inclusive'], 'ge'),
        'price_or_rs_weakening': weak_price_or_rs}
    risk_status = _condition(*risk_parts.values())
    return {'session': features.get('session'), 'stage': stage, 'candidate_states': states,
        'confirmation_states': confirmed, 'confirmation_exchange_sessions': 2,
        'high_risk': {'status': risk_status, 'flag': True if risk_status == 'PASS' else False if risk_status == 'FAIL' else None,
            'conditions': risk_parts, 'prior60_share_p90': p90, 'prior5_breadth_peak': peak,
            'breadth_decline': drop, 'independent_of_stage': True},
        'parameter_status': CONFIG['parameter_status'], 'market_edge': CONFIG['market_edge']}


def source_identity():
    paths = (Path(__file__), CONFIG_PATH)
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    return {'implementation_version': CONFIG['implementation_version'], 'feature_version': FEATURE_VERSION,
        'spec_revision': CONFIG['spec_revision'], 'config_hash': digest(CONFIG),
        'source_file_hashes': hashes, 'source_hash': digest(hashes)}


def _revision(row):
    return tuple(stamp(getattr(row, key)) for key in ('source_published_at', 'ingested_at', 'first_seen_at'))


def _select(rows):
    if not rows:
        return None
    latest = max(_revision(row) for row in rows)
    choices = [row for row in rows if _revision(row) == latest]
    return choices[0] if len({digest(row) for row in choices}) == 1 else None


def _validate_pack(bundle, cutoff, pack, validator):
    if not callable(validator):
        raise ValueError('THEME_MARKET_PACK_VALIDATOR_CAPABILITY_REQUIRED')
    # The trusted native adapter validates original bytes, SHA and dated master
    # identities. Returning a flag inside the supplied JSON is never sufficient.
    validator(pack, bundle=bundle, cutoff=cutoff)
    parsed = pack if isinstance(pack, ThemeMarketPack) else ThemeMarketPack.model_validate(pack)
    if (parsed.schema_version != 'THEME_MARKET_PACK_V1'
            or parsed.source_input_hash != digest_market_input(bundle)
            or parsed.observation_date != bundle.observation_date
            or stamp(parsed.decision_cutoff) != cutoff or parsed.coverage_type != bundle.coverage_type):
        raise ValueError('THEME_MARKET_PACK_INPUT_BINDING_MISMATCH')
    return parsed


def _session_market(panel, resolver, calendar):
    day = str(panel.session)
    reasons = []
    expected = set(panel.expected_instrument_ids)
    nontrading = set(panel.nontrading_instrument_ids)
    if (panel.scope != MARKET_SCOPE or not expected or len(expected) != len(panel.expected_instrument_ids)
            or not nontrading.issubset(expected) or any(not _a_common_id(iid) for iid in expected)):
        reasons.append('THEME_MARKET_SCOPE_OR_IDENTITY_UNVERIFIED')
    if panel.status != 'COMPLETE' or not panel.pagination_complete:
        reasons.append('THEME_MARKET_SESSION_NOT_COMPLETE')
    if nontrading and panel.nontrading_policy != CONFIG['evidence']['known_nontrading_policy']:
        reasons.append('THEME_NONTRADING_EXCLUSION_NOT_BOUND')
    envelope = {**panel.clocks.model_dump(mode='json'), 'session': day, 'source_refs': panel.source_refs}
    proof = resolver.resolve(envelope, session=day, require_session=True, require_complete=True)
    reasons.extend(proof.reason_codes)
    grouped = defaultdict(list)
    for row in panel.rows:
        if row.session != panel.session or row.instrument_id not in expected:
            reasons.append('THEME_MARKET_ROW_SCOPE_MISMATCH')
            continue
        authority = resolver.resolve(row, instrument_id=row.instrument_id, session=day,
                                     require_session=True, require_complete=True)
        if not authority.allowed:
            reasons.extend(authority.reason_codes)
            continue
        grouped[row.instrument_id].append(row)
    selected = {iid: _select(rows) for iid, rows in grouped.items()}
    amounts = {}
    for iid in sorted(expected - nontrading):
        row = selected.get(iid)
        value = exact(row.amount_cny) if row is not None else None
        if row is None or row.quality == 'INVALID' or value is None or value < 0:
            reasons.append('THEME_MARKET_AMOUNT_MISSING_INVALID_OR_CONFLICTING')
        else:
            amounts[iid] = value
    total = sum(amounts.values(), Fraction(0)) if not reasons else None
    if total is not None and total <= 0:
        reasons.append('THEME_MARKET_AMOUNT_NONPOSITIVE')
        total = None
    return {'session': day, 'status': 'UNKNOWN' if reasons else 'COMPLETE', 'scope': MARKET_SCOPE,
        'amount_cny': total, 'amounts': amounts, 'expected_ids': expected,
        'trading_ids': expected - nontrading, 'nontrading_ids': nontrading,
        'selected': selected, 'source_refs': list(panel.source_refs), 'return_basis': panel.return_basis,
        'reason_codes': sorted(set(reasons)), 'historical_reconstruction_used': proof.reconstruction_used}


def _return1(iid, day, prior_day, bars, resolver, *, market=None):
    # A declared source reference chain is a separate price feature. Its pack
    # row was authenticated against RAW bytes by the native validator; never
    # borrow an inline row or silently populate comparable_close from RAW.
    if market is not None and market.get('return_basis') is not None:
        if market['return_basis'] != CONFIG['reference_return_basis']:
            return None
        current = market['selected'].get(iid)
        if current is None or current.quality != 'VALID':
            return None
        if not resolver.resolve(current, instrument_id=iid, session=day,
                require_session=True, require_complete=True).allowed:
            return None
        opening, high, low, close, reference = map(exact,
            (current.open, current.high, current.low, current.close, current.reference_prev_close))
        if (any(value is None or value <= 0 for value in (opening, high, low, close, reference))
                or not low <= opening <= high or not low <= close <= high):
            return None
        return close / reference - 1
    current, previous = bars.get((iid, day)), bars.get((iid, prior_day))
    if current is None or previous is None or current.quality != 'VALID' or previous.quality != 'VALID':
        return None
    for row in (current, previous):
        if not resolver.resolve(row, instrument_id=iid, session=str(row.session), require_session=True, require_complete=True).allowed:
            return None
    a, b = exact(current.comparable_close), exact(previous.comparable_close)
    if (a is None or b is None or a <= 0 or b <= 0 or not current.comparable_basis
            or current.comparable_basis != previous.comparable_basis):
        return None
    return a / b - 1


def _return_reader(bars, resolver, markets):
    """Memoize only within one run with fixed rows, market panels and cutoff.

    The first call follows all ordinary price/source checks and records its
    dependencies. Reusing its exact result, including None, adds no authority.
    """
    cache = {}
    def read(iid, day, prior_day):
        market = markets.get(day)
        basis = market.get('return_basis') if market is not None else None
        key = (iid, day, prior_day, basis)
        if key not in cache:
            cache[key] = _return1(iid, day, prior_day, bars, resolver, market=market)
        return cache[key]
    return read


def _compound(rows, key, count):
    window = rows[-count:]
    values = [exact(row.get(key)) for row in window]
    if (len(values) != count or any(value is None or value <= -1 for value in values)
            or len({row.get('price_return_basis') for row in window}) != 1):
        return None
    result = Fraction(1)
    for value in values:
        result *= 1 + value
    return result - 1


def _market_compound_reader():
    """Share one exact market chain per window within the fixed run only."""
    cache = {}
    def read(rows, count):
        window = rows[-count:]
        # All themes in this run receive the identical source-qualified daily
        # market return. Dates and every daily basis preserve missing/warm-up
        # boundaries, including a cached None; no theme chain is cached here.
        key = (count, tuple((row['session'], row.get('price_return_basis')) for row in window))
        if key not in cache:
            cache[key] = _compound(rows, 'market_return_1d', count)
        return cache[key]
    return read


def describe_theme_association(observations):
    """Describe market-adjusted returns after removing shared constituents.

    Each already qualified observation supplies session, left/right member
    return mappings and market_return. Shares are deliberately not an input:
    their closed-denominator relationships cannot establish substitution.
    """
    pairs, excluded, seen = [], [], set()
    for row in observations:
        session = row.get('session')
        if not session or session in seen:
            return {'status': 'UNKNOWN', 'reason_codes': ['THEME_ASSOCIATION_DUPLICATE_OR_MISSING_SESSION'],
                    'causal_interpretation': 'NOT_ESTABLISHED'}
        seen.add(session)
        left, right = row.get('left_member_returns') or {}, row.get('right_member_returns') or {}
        overlap = set(left) & set(right)
        a = [exact(value) for iid, value in left.items() if iid not in overlap]
        b = [exact(value) for iid, value in right.items() if iid not in overlap]
        market = exact(row.get('market_return'))
        if market is None or not a or not b or any(x is None for x in (*a, *b)):
            excluded.append(session)
            continue
        pairs.append((sum(a, Fraction(0)) / len(a) - market,
                      sum(b, Fraction(0)) / len(b) - market, len(overlap)))
    correlation = None
    if len(pairs) >= 3:
        a_mean = sum(x[0] for x in pairs) / len(pairs)
        b_mean = sum(x[1] for x in pairs) / len(pairs)
        cross = sum((x[0] - a_mean) * (x[1] - b_mean) for x in pairs)
        va = sum((x[0] - a_mean) ** 2 for x in pairs)
        vb = sum((x[1] - b_mean) ** 2 for x in pairs)
        if va > 0 and vb > 0:
            # Convert a bounded dimensionless ratio, avoiding float overflow
            # or underflow from otherwise legal exact source values.
            correlation = math.sqrt(float(cross * cross / (va * vb))) * (-1 if cross < 0 else 1)
    return {'status': 'AVAILABLE' if correlation is not None else 'UNKNOWN',
        'market_adjusted_ex_overlap_return_correlation': correlation,
        'valid_session_count': len(pairs), 'excluded_sessions': excluded,
        'overlapping_member_observations_removed': sum(x[2] for x in pairs),
        'market_adjustment': 'EQUAL_WEIGHT_RETURN_MINUS_SAME_DATE_MARKET_RETURN_FIXED_UNIT_EXPOSURE',
        'share_closure_used': False, 'causal_interpretation': 'NOT_ESTABLISHED',
        'product_role': 'DESCRIPTIVE_ASSOCIATION_NOT_SELECTION'}


def run_theme_evolution(bundle: MarketInput, *, cutoff, market_pack=None, _market_pack_validator=None,
                        membership_mode='DATED_ONLY', output_history=False, history_scope='LATEST_67'):
    """Build bounded context on the same input, without store reads or selection.

    Persist this result within the caller's ordinary run. Its context hash and
    effective dependencies must participate in that run's identity.
    """
    cutoff = stamp(cutoff)
    if membership_mode not in ('DATED_ONLY', 'CURRENT_COHORT_BACKVIEW'):
        raise ValueError('THEME_MEMBERSHIP_MODE_UNSUPPORTED')
    if not isinstance(output_history, bool):
        raise ValueError('THEME_OUTPUT_HISTORY_MUST_BE_BOOLEAN')
    if history_scope not in CONFIG['allowed_history_scopes']:
        raise ValueError('THEME_HISTORY_SCOPE_UNSUPPORTED')
    if bundle.observation_date > cutoff.astimezone(SH).date():
        raise ValueError('THEME_FUTURE_OBSERVATION_DATE')
    input_hash = digest_market_input(bundle)
    sessions = [str(day) for day in bundle.sessions if day <= bundle.observation_date]
    if history_scope == 'LATEST_67':
        sessions = sessions[-CONFIG['history_exchange_sessions']:]
    pack = _validate_pack(bundle, cutoff, market_pack, _market_pack_validator) if market_pack is not None else None
    receipts = tuple(bundle.receipts) + (tuple(pack.receipts) if pack is not None else ())
    resolver = EvidenceResolver(receipts, cutoff=cutoff, coverage_type=bundle.coverage_type,
        sessions=sessions, observation_date=bundle.observation_date)
    panels = {str(row.session): row for row in pack.sessions if str(row.session) in sessions} if pack else {}
    markets = {day: _session_market(panel, resolver, sessions) for day, panel in panels.items()}
    grouped_bars = defaultdict(list)
    for row in bundle.bars:
        if str(row.session) in sessions:
            grouped_bars[(row.instrument_id, str(row.session))].append(row)
    for market in markets.values():
        for iid, row in market['selected'].items():
            if row is not None:
                grouped_bars[(iid, market['session'])].append(row)
    # Visibility precedes selecting a revision. Late rows cannot hide a legal
    # earlier revision; same-clock different payloads remain conflicting.
    bars = {}
    for key, choices in grouped_bars.items():
        visible = [row for row in choices if resolver.resolve(row, instrument_id=key[0], session=key[1],
                    require_session=True, require_complete=True).allowed]
        bars[key] = _select(visible)
    daily_return = _return_reader(bars, resolver, markets)
    market_compound = _market_compound_reader()
    # The identical whole-market benchmark belongs to the session, not to a
    # theme. Authenticate/calculate it once instead of once per concept.
    for index, day in enumerate(sessions):
        market = markets.get(day)
        if market is None:
            continue
        prior_day = sessions[index - 1] if index else None
        values = [daily_return(iid, day, prior_day)
                  for iid in sorted(market['trading_ids'])] if market['status'] == 'COMPLETE' else []
        market['market_return_1d'] = (sum(values, Fraction(0)) / len(values)
            if values and all(value is not None for value in values) else None)
    themes = tuple(bundle.themes) + (tuple(pack.themes) if pack is not None else ())
    theme_ids = sorted({row.theme_id for row in themes if row.theme_type in CONFIG['allowed_theme_types']})
    backview_cohorts = {}
    if membership_mode == 'CURRENT_COHORT_BACKVIEW':
        decision_day = cutoff.astimezone(SH).date()
        for theme_id in theme_ids:
            # Resolve the actual snapshot on its declared effective date, never
            # forge its effective_from into an earlier price observation day.
            choices = [row for row in themes if row.theme_id == theme_id
                and row.theme_type in CONFIG['allowed_theme_types'] and row.effective_from <= decision_day
                and clock_visible(row, cutoff)
                and all(clock_visible(resolver.receipts.get(ref), cutoff) for ref in row.source_refs)
                and resolver.resolve(row, session=row.effective_from, require_complete=True).allowed]
            backview_cohorts[theme_id] = _select(choices)
    result_rows = []
    for theme_id in theme_ids:
        history, prior_state = [], None
        for index, day in enumerate(sessions):
            prior_day = sessions[index - 1] if index else None
            candidates = [row for row in themes if row.theme_id == theme_id and row.theme_type in CONFIG['allowed_theme_types']
                and row.effective_from <= date.fromisoformat(day)
                and (row.effective_to is None or date.fromisoformat(day) < row.effective_to)
                and resolver.resolve(row, session=day, require_complete=True).allowed]
            theme = _select(candidates) if membership_mode == 'DATED_ONLY' else backview_cohorts.get(theme_id)
            market = markets.get(day)
            reasons = []
            members = sorted(set(theme.member_ids)) if theme is not None else []
            if theme is None:
                reasons.append('THEME_DATED_MEMBERSHIP_MISSING_OR_CONFLICTING')
            if market is None or market['status'] != 'COMPLETE':
                reasons.append('THEME_COMPLETE_MARKET_AMOUNT_UNAVAILABLE')
            dated_eligibility = (membership_mode == 'CURRENT_COHORT_BACKVIEW'
                and market is not None and market['status'] == 'COMPLETE'
                and bool(members) and all(_a_common_id(iid) for iid in members))
            if membership_mode == 'CURRENT_COHORT_BACKVIEW' and members and not all(_a_common_id(iid) for iid in members):
                reasons.append('THEME_CURRENT_COHORT_SECURITY_SCOPE_UNVERIFIED')
            if market and members and not set(members).issubset(market['expected_ids']) and not dated_eligibility:
                reasons.append('THEME_MEMBER_OUTSIDE_VERIFIED_A_COMMON_SCOPE')
            eligible = sorted(set(members) & market['expected_ids']) if dated_eligibility else members
            absent = sorted(set(members) - market['expected_ids']) if dated_eligibility else None
            active = sorted(set(eligible) & market['trading_ids']) if market else eligible
            if theme is not None and not active:
                reasons.append('THEME_NO_ACTIVE_DATED_ELIGIBLE_MEMBERS')
            amounts = [market['amounts'].get(iid) for iid in active] if market else []
            total = sum(amounts, Fraction(0)) if active and amounts and all(x is not None for x in amounts) and not reasons else None
            share = total / market['amount_cny'] if total is not None else None
            concentration = ((len(active) * sum((x / total) ** 2 for x in amounts) - 1) / (len(active) - 1)
                if total is not None and total > 0 and len(active) > 1 else None)
            returns = [daily_return(iid, day, prior_day) for iid in active] if theme else []
            mean = sum(returns, Fraction(0)) / len(returns) if returns and all(x is not None for x in returns) and not reasons else None
            breadth = Fraction(sum(x > 0 for x in returns), len(returns)) if mean is not None else None
            market_mean = market['market_return_1d'] if market else None
            feature = {'session': day, 'previous_exchange_session': prior_day, 'theme_id': theme_id,
                'name': theme.name if theme else None, 'theme_type': theme.theme_type if theme else None,
                'membership_mode': membership_mode,
                'data_role': 'DAILY_DATED_MEMBERSHIP_HISTORY' if membership_mode == 'DATED_ONLY'
                    else 'CURRENT_COHORT_BACKVIEW_ASOF_DECISION',
                'cohort_captured_at': max(theme.first_seen_at, theme.ingested_at).isoformat() if theme else None,
                'cohort_effective_from': str(theme.effective_from) if theme else None,
                'historical_membership_verified': theme is not None and membership_mode == 'DATED_ONLY',
                'official_pit_validation_countable': False,
                'membership_count': len(members), 'active_membership_count': len(active), 'member_ids': members,
                'membership_scope': 'CURRENT_COHORT_WITH_DATED_ELIGIBILITY'
                    if membership_mode == 'CURRENT_COHORT_BACKVIEW' else 'DAILY_DATED_MEMBERSHIP_HISTORY',
                'dated_eligibility_verified': dated_eligibility,
                'eligible_member_ids': eligible, 'eligible_membership_count': len(eligible),
                'absent_from_dated_market_ids': absent,
                'absent_from_dated_market_count': len(absent) if absent is not None else None,
                'theme_amount_cny': total, 'market_amount_cny': market['amount_cny'] if market else None,
                'share': share, 'concentration_adjusted_hhi': concentration, 'breadth': breadth,
                'theme_return_1d': mean, 'market_return_1d': market_mean,
                'price_return_basis': (market.get('return_basis') if market else None)
                    or 'SAME_COMPARABLE_BASIS_DAILY_RETURN',
                'source_refs': sorted(set((theme.source_refs if theme else ()) + tuple(market['source_refs'] if market else ()))),
                'reason_codes': reasons}
            prior_shares = [row['share'] for row in history[-CONFIG['expansion_prior_sessions']:]]
            base = median(prior_shares) if len(prior_shares) == CONFIG['expansion_prior_sessions'] and all(x is not None for x in prior_shares) else None
            feature['share_prior20_median'] = base
            feature['expansion'] = share / base if share is not None and base is not None and base > 0 else None
            for count in (3, 5):
                theme_return = _compound([*history, feature], 'theme_return_1d', count)
                market_return = market_compound([*history, feature], count)
                feature[f'theme_return_{count}d'] = theme_return
                feature[f'rs{count}'] = theme_return - market_return if theme_return is not None and market_return is not None else None
            if mean is None:
                reasons.append('THEME_MEMBER_RETURN_UNAVAILABLE_OR_BASIS_UNVERIFIED')
            if market_mean is None:
                reasons.append('THEME_SAME_SCOPE_MARKET_RETURN_UNAVAILABLE')
            if feature['expansion'] is None:
                reasons.append('THEME_PRIOR20_SHARE_BASELINE_UNAVAILABLE')
            previous = {**history[-1], 'candidate_states': prior_state['candidate_states']} if history and prior_state else None
            state = evaluate_day(feature, previous, history, prior_state['stage'] if prior_state else None)
            history.append(feature)
            prior_state = state
            if output_history or index >= len(sessions) - CONFIG['output_exchange_sessions']:
                result_rows.append({**feature, **state, 'market_scope': MARKET_SCOPE,
                    'return_basis': CONFIG['reference_return_role']
                        if feature['price_return_basis'] == CONFIG['reference_return_basis']
                        else 'DAILY_REBALANCED_DATED_COHORT_EQUAL_WEIGHT'
                        if membership_mode == 'DATED_ONLY' else 'FIXED_CURRENT_COHORT_EQUAL_WEIGHT_BACKVIEW',
                    'benchmark_scope': MARKET_SCOPE})
    identity = source_identity()
    pack_hash = digest_json_stream(market_pack) if market_pack is not None else None
    dependencies = resolver.effective_dependencies()
    result = {'strategy_id': STRATEGY_ID, **identity, 'source_input_hash': input_hash,
        'observation_date': str(bundle.observation_date), 'decision_cutoff': cutoff.isoformat(),
        'coverage_type': bundle.coverage_type, 'market_pack_hash': pack_hash,
        'exact_numeric_encoding': EXACT_NUMERIC_ENCODING,
        'requested_output_scope': 'ALL_BOUNDED_HISTORY' if output_history else 'LAST_TWO_EXCHANGE_SESSIONS',
        'requested_history_scope': history_scope,
        'analysis_role': 'RETROSPECTIVE_CONTEXT_ANALYSIS'
            if history_scope == 'ALL_OBSERVED_SESSIONS_RESEARCH' else CONFIG['product_role'],
        'computed_exchange_sessions': sessions,
        'membership_mode': membership_mode,
        'data_role': 'DAILY_DATED_MEMBERSHIP_HISTORY' if membership_mode == 'DATED_ONLY'
            else 'CURRENT_COHORT_BACKVIEW_ASOF_DECISION',
        'historical_membership_verified': bool(result_rows) and all(row['historical_membership_verified'] for row in result_rows),
        'official_pit_validation_countable': False,
        'source_refs': sorted(resolver.used), 'effective_input_hash': digest_json_stream(dependencies),
        'items': encoded(result_rows), 'status': 'UNKNOWN' if not result_rows else 'AVAILABLE_WITH_PER_AXIS_UNKNOWN',
        'formal_focus': False, 'core_pass': False,
        'selection_role': 'RETROSPECTIVE_CONTEXT_ANALYSIS'
            if history_scope == 'ALL_OBSERVED_SESSIONS_RESEARCH' else CONFIG['product_role'],
        'parameter_status': CONFIG['parameter_status'], 'market_edge': CONFIG['market_edge'],
        'source_policy': 'ORIGINAL_INPUT_PLUS_NATIVE_VALIDATED_CONTEXT_LAYER_NO_DB_READS',
        'association_policy': CONFIG['evidence']['association_policy']}
    result['context_content_hash'] = digest_json_stream(result)
    return result
