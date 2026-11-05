"""Independent exact short-horizon research strategy; no orders or tuning.

Every identity is resolved before collecting facts. Predicates see numeric facts
and evidence states only, never a security identity, old strategy label or target.
"""
from __future__ import annotations
from collections import Counter
from fractions import Fraction
import hashlib
from pathlib import Path
import yaml
from .contracts import MarketInput, digest
from .visibility import stamp

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / 'config/short_horizon_strength_r1.yaml'
CONFIG = yaml.safe_load(CONFIG_PATH.read_text(encoding='utf-8'))
SHORT_HORIZON_ID = STRATEGY_ID = CONFIG['strategy_id']
IMPLEMENTATION_VERSION = CONFIG['implementation_version']
SPEC_REVISION = CONFIG['spec_revision']
FEATURE_VERSION = CONFIG['feature_version']
PARAMETER_STATUS = CONFIG['parameter_status']
SELECTION_POLICY_ID = CONFIG['selection_policy_id']

def exact(value):
    if value is None:
        return None
    if isinstance(value, Fraction):
        return value
    if isinstance(value, dict):
        return Fraction(value['numerator'], value['denominator'])
    return Fraction(str(value))


def encode(value):
    value = exact(value)
    return {'numerator': value.numerator, 'denominator': value.denominator} if value is not None else None


def condition(status, code, *, value=None, threshold=None):
    return {'status': status, 'reason_codes': [] if status == 'PASS' else [code],
            'value': encode(value), 'threshold': encode(threshold)}


def compare(value, threshold, code, *, operator='ge'):
    value, threshold = exact(value), exact(threshold)
    if value is None:
        return condition('UNKNOWN', code + '_UNAVAILABLE', threshold=threshold)
    passed = {'ge': value >= threshold, 'gt': value > threshold,
              'le': value <= threshold, 'lt': value < threshold}[operator]
    return condition('PASS' if passed else 'FAIL', code, value=value, threshold=threshold)


def combine(parts, *, any_pass=False):
    states = [value['status'] for value in parts.values()]
    if any_pass:
        state = 'PASS' if 'PASS' in states else 'UNKNOWN' if 'UNKNOWN' in states or not states else 'FAIL'
    else:
        state = 'FAIL' if 'FAIL' in states else 'UNKNOWN' if 'UNKNOWN' in states or not states else 'PASS'
    return {'status': state, 'conditions': parts,
            'reason_codes': sorted({code for value in parts.values() if value['status'] != 'PASS'
                                    for code in value.get('reason_codes', [])})}


def difference(left, right):
    left, right = exact(left), exact(right)
    return left-right if left is not None and right is not None else None


def evaluate_exact_features(f, config=None):
    """No identity, name, old stage, old HIGH/momentum or target labels read."""
    config = CONFIG if config is None else config
    raw = f.get('raw', {})
    values = [exact(raw.get(key)) for key in ('open', 'high', 'low', 'close', 'reference_prev_close')]
    visible = (f.get('current_raw_present') is True and f.get('current_quality') == 'VALID'
               and f.get('current_source_bound') is True and f.get('current_visibility_valid') is True)
    if f.get('current_quality') == 'INVALID':
        source = condition('FAIL', 'SHR_CURRENT_RAW_KNOWN_INVALID_QUALITY')
    elif any(value is None for value in values) or not visible:
        source = condition('UNKNOWN', 'SHR_CURRENT_RAW_SOURCE_OR_VISIBILITY_UNAVAILABLE')
    else:
        open_, high, low, close, previous = values
        valid = all(value > 0 for value in values) and low <= min(open_, close) <= max(open_, close) <= high
        source = condition('PASS' if valid else 'FAIL', 'SHR_CURRENT_RAW_OHLC_INTEGRITY')
    flat = condition('UNKNOWN', 'SHR_FLAT_RAW_EVIDENCE_UNAVAILABLE')
    if source['status'] != 'UNKNOWN':
        flat_pass = (source['status'] == 'PASS' and values[0] == values[1] == values[2] == values[3]
                     and values[3] > values[4])
        flat = combine({'price': condition('PASS' if flat_pass else 'FAIL', 'SHR_POSITIVE_FLAT_RAW_OHLC'),
                        'actual_amount': compare(f.get('amount'), 0, 'SHR_ACTUAL_POSITIVE_AMOUNT', operator='gt')})
    ma = f.get('ma', {})
    def short_health(windows, slope=False):
        parts = {f'C_GT_MA{w}': compare(difference(f.get('close_comparable'), ma.get(str(w))),
                                     0, f'SHR_C_GT_MA{w}', operator='gt') for w in windows}
        if slope:
            parts['MA20_NONDECREASING'] = compare(difference(ma.get('20'), f.get('previous_ma20')),
                                                0, 'SHR_MA20_NONDECREASING')
        return combine(parts)
    def price_support(seed):
        parts = {'CLV': compare(f.get('clv'), seed['clv_min'], 'SHR_CLOSE_LOCATION')}
        if seed.get('positive_flat_raw_alternative') is True:
            parts['positive_flat_raw'] = flat
        return combine(parts, any_pass=True)
    stages = config['stages']; returns = f.get('returns', {}); rs = f.get('rs', {})
    start = stages['STRONG_START']
    continuation = stages['STRONG_CONTINUATION']
    pullback = stages['FIRST_PULLBACK']
    arms = {'STRONG_START': combine({
        'ret1': compare(returns.get('1'), start['ret1_min'], 'SHR_START_ABSOLUTE_RET1'),
        'rs1': compare(rs.get('1'), start['rs1_min'], 'SHR_START_RS1'),
        'amount': compare(f.get('amount'), start['amount_min_cny'], 'SHR_START_AMOUNT'),
        'activity_expansion': compare(f.get('A'), start['amount_ratio_prior5_min'], 'SHR_START_ACTIVITY_EXPANSION'),
        'daily_price_confirmation': price_support(start),
        'short_trend': short_health(start['close_above_ma_windows']),
        'prior5_close_breakout': compare(difference(f.get('close_comparable'), f.get('prior5_close_high')),
                                       0, 'SHR_START_PRIOR5_CLOSE_BREAKOUT', operator='gt'),
        'prior_state': compare(f.get('previous_ret5'), start['prior_ret5_T_minus_1_max'],
                               'SHR_START_PRIOR_STATE_ALREADY_EXPANDED', operator='le'),
    }), 'STRONG_CONTINUATION': combine({
        'amount': compare(f.get('amount'), continuation['amount_min_cny'], 'SHR_CONTINUATION_AMOUNT'),
        'absolute_strength': combine({
            'ret5': compare(returns.get('5'), continuation['ret5_min'], 'SHR_CONTINUATION_RET5'),
            'ret10': compare(returns.get('10'), continuation['ret10_min'], 'SHR_CONTINUATION_RET10'),
        }, any_pass=True),
        'ret1': compare(returns.get('1'), continuation['ret1_min'], 'SHR_CONTINUATION_POSITIVE_RET1'),
        'ret3': compare(returns.get('3'), continuation['ret3_min'], 'SHR_CONTINUATION_RET3'),
        'rs5': compare(rs.get('5'), continuation['rs5_min_exclusive'], 'SHR_CONTINUATION_RS5', operator='gt'),
        'daily_price_confirmation': price_support(continuation),
        'short_trend': short_health(continuation['close_above_ma_windows'], slope=True),
    })}
    count = pullback['prior_positive_sessions']
    previous = f.get('prior_daily_returns', [])[-count:]
    sequence = {str(index): compare(value, 0, 'SHR_PRIOR_DAILY_RETURN', operator='gt')
                for index, value in enumerate(previous)}
    if len(previous) != count:
        sequence['window'] = condition('UNKNOWN', 'SHR_PRIOR_POSITIVE_WINDOW_INCOMPLETE')
    arms['FIRST_PULLBACK'] = combine({
        'amount': compare(f.get('amount'), pullback['amount_min_cny'], 'SHR_PULLBACK_AMOUNT'),
        'ret5': compare(returns.get('5'), pullback['ret5_min'], 'SHR_PULLBACK_PRIOR_STRENGTH_RET5'),
        'ret3': compare(returns.get('3'), pullback['ret3_min'], 'SHR_PULLBACK_STRENGTH_RET3'),
        'day_lower_bound': compare(returns.get('1'), pullback['ret1_min'], 'SHR_PULLBACK_DEPTH'),
        'first_negative_after_positive_sequence': compare(returns.get('1'), pullback['ret1_max_exclusive'],
                                                          'SHR_PULLBACK_DAY_NEGATIVE', operator='lt'),
        'prior_positive_sequence': combine(sequence),
        'short_trend': short_health(pullback['close_above_ma_windows'], slope=True),
    })
    pattern = combine({'current_price_evidence': source, 'stage_alternatives': combine(arms, any_pass=True)})
    flag = f.get('close_limit_down', 'UNKNOWN')
    limit_safety = condition('FAIL' if flag == 'TRUE' else 'PASS' if flag == 'FALSE' else 'UNKNOWN',
                             'SHR_VERIFIED_CLOSE_LIMIT_DOWN_SAFETY')
    selected = pattern['status'] == 'PASS' and limit_safety['status'] != 'FAIL'
    decision = 'REJECTED' if limit_safety['status'] == 'FAIL' or pattern['status'] == 'FAIL' else 'WATCH' if selected else 'UNKNOWN'
    return {'status': 'REJECTED_VERIFIED_LIMIT_DOWN' if limit_safety['status'] == 'FAIL' else
            'RESEARCH_OBSERVATION' if selected else 'UNKNOWN_PATTERN' if pattern['status'] == 'UNKNOWN' else 'NO_PATTERN_MATCH',
            'decision_status': decision, 'research_selected': selected,
            'pattern_status': pattern['status'], 'arms': arms,
            'matched_arms': [stage for stage in config['stage_order'] if arms[stage]['status'] == 'PASS'],
            'current_price_evidence': source, 'limit_safety': limit_safety,
            'positive_flat_raw_observation': flat['status'] == 'PASS',
            'repair_confirmation': 'UNCONFIRMED' if arms['FIRST_PULLBACK']['status'] == 'PASS' else 'NOT_ASSESSED',
            'repair_status': 'UNCONFIRMED' if arms['FIRST_PULLBACK']['status'] == 'PASS' else 'NOT_REQUIRED',
            'formal_focus': False, 'core_pass': False, 'executability': 'UNCONFIRMED', 'market_edge': 'NOT_ESTABLISHED'}


def _shared():
    # Lazy import: strategy registry imports this module during initialization.
    from . import stage_aware_momentum as sam
    from . import strategy_experiments as se
    return sam, se


def _r1_market_return_at(ctx, end_index, lookback):
    """Independent full-window peer qualification, preserving equal weighting.

    This cache must never consume SAM's endpoint-only population cache. The
    eligible denominator and the original 60% benchmark threshold are retained.
    """
    sam, se = _shared()
    eligible = tuple(ctx.eligible_ids)
    key = ('SHORT_HORIZON_MARKET_RETURN_AT', FEATURE_VERSION, eligible, end_index, lookback)
    cache = ctx.scalar_cache
    if key in cache:
        value, metadata = cache[key]
        return value, {**metadata, 'reason_codes': list(metadata['reason_codes'])}
    minimum = Fraction(str(sam.CONFIG['research_seed_parameters']['market_context']
                           ['min_benchmark_coverage_fraction_inclusive']))
    valid_returns = []
    excluded = []
    if end_index is not None and end_index-lookback >= 0 and end_index < len(ctx.sessions):
        for peer in eligible:
            rows, reasons = [], set()
            for index in range(end_index-lookback, end_index+1):
                session = ctx.sessions[index]
                row = se._bar(ctx, session, peer)
                if row is None:
                    reasons.add('SHR_MARKET_PEER_WINDOW_BAR_UNAVAILABLE')
                    continue
                resolution = ctx.resolver.resolve(row, instrument_id=peer, session=session,
                                                  require_session=True, require_complete=True)
                if not resolution.allowed:
                    reasons.update(resolution.reason_codes)
                if row.get('quality') != 'VALID':
                    reasons.add('SHR_MARKET_PEER_WINDOW_QUALITY_UNAVAILABLE_OR_INVALID')
                close = exact(row.get('comparable_close'))
                if close is None or close <= 0:
                    reasons.add('SHR_MARKET_PEER_WINDOW_COMPARABLE_CLOSE_UNAVAILABLE_OR_NONPOSITIVE')
                if not row.get('comparable_basis'):
                    reasons.add('SHR_MARKET_PEER_WINDOW_COMPARABLE_BASIS_UNAVAILABLE')
                rows.append(row)
            if len(rows) != lookback+1:
                reasons.add('SHR_MARKET_PEER_WINDOW_INCOMPLETE')
            if rows and len({row.get('comparable_basis') for row in rows}) != 1:
                reasons.add('SHR_MARKET_PEER_WINDOW_COMPARABLE_BASIS_MISMATCH')
            if reasons:
                excluded.append({'instrument_id':peer, 'reason_codes':sorted(reasons)})
                continue
            # The unchanged endpoint formula is evaluated only after the whole
            # peer window is source-qualified, positive and on one basis.
            valid_returns.append(exact(rows[-1]['comparable_close']) /
                                 exact(rows[0]['comparable_close']) - 1)
    else:
        excluded = [{'instrument_id':peer, 'reason_codes':['SHR_MARKET_WINDOW_UNAVAILABLE']}
                    for peer in eligible]
    coverage = Fraction(len(valid_returns), len(eligible)) if eligible else Fraction(0)
    status = ('COMPLETE' if valid_returns and coverage >= minimum else
              'INSUFFICIENT_COVERAGE' if valid_returns else 'UNKNOWN')
    value = sum(valid_returns, Fraction(0))/len(valid_returns) if status == 'COMPLETE' else None
    reason_counts = Counter(code for row in excluded for code in row['reason_codes'])
    metadata = {'status':status, 'valid_count':len(valid_returns), 'expected_count':len(eligible),
                'coverage':coverage, 'minimum_coverage':minimum, 'benchmark_return':value,
                'window_sessions':lookback, 'peer_window_rows_required':lookback+1,
                'membership_basis':'DATED_ELIGIBLE_MAINBOARD_A_COMMON_AT_OBSERVATION_DATE',
                'qualification_policy':'SHORT_HORIZON_FULL_WINDOW_EQUAL_WEIGHT_PIT_V2',
                'excluded_peer_count':len(excluded), 'excluded_peers':excluded,
                'exclusion_reason_counts':dict(reason_counts),
                'reason_codes':[] if status == 'COMPLETE' else
                    ['SHR_MARKET_BENCHMARK_COVERAGE_INSUFFICIENT'] if valid_returns else
                    ['SHR_MARKET_BENCHMARK_RETURNS_UNAVAILABLE']}
    cache[key] = (value, metadata)
    return value, {**metadata, 'reason_codes':list(metadata['reason_codes'])}


def collect_exact_features(ctx, iid):
    """Collect through the original PIT resolver; never accept archived JSON.

    Windows carry their own completeness/basis checks. A missing unused long
    window does not become a common hard gate for the independent START arm.
    """
    sam, se = _shared()
    end = sam._session_index(ctx)
    current = se._bar(ctx, ctx.observation_date, iid)
    proof = ctx.resolver.resolve(current, instrument_id=iid, session=ctx.observation_date,
                                 require_session=True, require_complete=True) if current else None
    # The source-filtered cache remains authoritative; no second raw-row picker.
    source_valid = proof is not None and proof.allowed
    raw = {key: exact((current or {}).get(key))
           for key in ('open', 'high', 'low', 'close', 'reference_prev_close')}
    close = raw['close']; high = raw['high']; low = raw['low']
    clv = (close-low)/(high-low) if close is not None and low is not None and high is not None and high > low else None
    first = max(0, end-24) if end is not None else 0
    selected = {}
    allowed = {}
    for index in range(first, end+1 if end is not None else 0):
        session = ctx.sessions[index]
        row = se._bar(ctx, session, iid)
        selected[index] = row
        resolution = ctx.resolver.resolve(row, instrument_id=iid, session=session,
                                          require_session=True, require_complete=True) if row else None
        allowed[index] = bool(row and row.get('quality') == 'VALID' and resolution and resolution.allowed
                              and row.get('source_refs') and row.get('comparable_basis')
                              and exact(row.get('comparable_close')) is not None
                              and exact(row.get('comparable_close')) > 0)
    def complete(start, stop):
        if start is None or stop is None or start < 0 or stop >= len(ctx.sessions):
            return False
        rows = [selected.get(index) for index in range(start, stop+1)]
        return bool(rows and all(allowed.get(index) for index in range(start, stop+1))
                    and len({row.get('comparable_basis') for row in rows}) == 1)
    def price_return(anchor, window):
        return sam._price_return(ctx, iid, anchor, window) if complete(anchor-window, anchor) else None
    returns = {str(window): price_return(end, window) if end is not None else None
               for window in (1, 3, 5, 10, 20)}
    rs, benchmark_coverage = {}, {}
    for window in (1, 3, 5):
        benchmark, metadata = _r1_market_return_at(ctx, end, window)
        stock_return = returns[str(window)]
        rs[str(window)] = stock_return-benchmark if stock_return is not None and benchmark is not None else None
        # Full peer exclusions are retained once on the strategy result; each
        # row keeps compact coverage facts without duplicating every peer list.
        benchmark_coverage[str(window)] = {key:value for key,value in metadata.items() if key != 'excluded_peers'}
    ma = {str(window): sam._ma_value(ctx, iid, end, window)[0]
          if end is not None and complete(end-window+1, end) else None for window in (5, 10, 20)}
    previous_ma20 = sam._ma_value(ctx, iid, end-5, 20)[0] if end is not None and complete(end-24, end-5) else None
    prior = [selected.get(index) for index in range(end-5, end)] if end is not None and end >= 5 else []
    prior_amounts = [sam._amount_fraction(row) for row in prior]
    amount = sam._amount_fraction(current)
    valid_prior = end is not None and complete(end-5, end-1)
    ratio = (amount*5/sum(prior_amounts, Fraction(0))
             if valid_prior and amount is not None and len(prior_amounts) == 5
             and all(value is not None and value >= 0 for value in prior_amounts)
             and sum(prior_amounts, Fraction(0)) > 0 else None)
    prior_high = max(sam._close_fraction(row) for row in prior) if valid_prior else None
    # Include current basis in the breakout comparison, independently of MAs.
    if prior_high is not None and (not current or current.get('comparable_basis') != prior[0].get('comparable_basis')):
        prior_high = None
    previous_ret5 = price_return(end-1, 5) if end is not None else None
    prior_returns = [price_return(index, 1) for index in range(end-5, end)] if end is not None and end >= 5 else []
    path = sam._daily_path(ctx, iid) if source_valid else {}
    return {'amount': amount, 'A': ratio, 'returns': returns, 'rs': rs, 'ma': ma,
            'market_benchmark_coverage':benchmark_coverage,
            'previous_ma20': previous_ma20, 'close_comparable': sam._close_fraction(current),
            'raw': raw, 'clv': clv, 'previous_ret5': previous_ret5, 'prior5_close_high': prior_high,
            'prior_daily_returns': prior_returns, 'current_raw_present': current is not None,
            'current_quality': (current or {}).get('quality'),
            'current_source_bound': bool((current or {}).get('source_refs')),
            'current_visibility_valid': source_valid,
            'close_limit_down': path.get('close_limit_down', 'UNKNOWN'),
            'limit_event_facts': {key:path.get(key, 'UNKNOWN') for key in
                                  ('close_limit_up', 'close_limit_down', 'touched_limit_up', 'touched_limit_down', 'limit_event_quality')},
            'current_source_refs': list((current or {}).get('source_refs', ())),
            'current_source_reason_codes': list(proof.reason_codes) if proof is not None else ['CURRENT_BAR_UNAVAILABLE'],
            'comparable_basis': (current or {}).get('comparable_basis'),
            'window_coverage': {'selected_25_count': sum(allowed.values()), 'requested_25_count': 25,
                                'complete_25': end is not None and complete(end-24, end)}}


def _encoded_features(value):
    if isinstance(value, Fraction):
        return encode(value)
    if isinstance(value, dict):
        return {key:_encoded_features(item) for key,item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_encoded_features(item) for item in value]
    return value


def short_horizon_source_identity():
    _, se = _shared()
    hashes = dict(se.source_identity()['source_file_hashes'])
    paths = [Path(__file__), CONFIG_PATH, se.SPEC_PATH, se.COMPLETION_PATH,
             ROOT/'config/theme_capital_trend_v1.yaml', ROOT/'config/stage_aware_momentum_v2.yaml',
             ROOT/'stockfish_core/storage.py', ROOT/'scripts/codex_daily_v2.py']
    paths += [Path(__file__).with_name(name) for name in
              ('data_pipeline.py', 'derived_feature_store.py', 'candidate_service.py')]
    hashes.update({str(path.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in paths})
    policy = {'id':SELECTION_POLICY_ID, 'research_only':True, 'formal_focus':False, 'core_pass':False,
              'top_n_truncation':False, 'stable_order':'research_selected then instrument_id',
              'known_limit_down':'REJECT', 'unknown_limit':'PRESERVE_UNKNOWN'}
    return {'implementation_version':IMPLEMENTATION_VERSION, 'spec_revision':SPEC_REVISION,
            'feature_version':FEATURE_VERSION, 'source_hash':digest(hashes), 'source_file_hashes':hashes,
            'config_hash':digest(CONFIG), 'spec_hash':hashes[str(CONFIG_PATH.relative_to(ROOT))],
            'selection_policy_hash':digest(policy)}


def short_horizon_strategy_specs():
    return [{'strategy_id':SHORT_HORIZON_ID, 'feature_version':FEATURE_VERSION,
             'parameter_status':PARAMETER_STATUS, 'parameter_status_label_zh':CONFIG['parameter_status_label_zh'],
             'formal_role':'RESEARCH_OBSERVATION_ONLY', 'market_edge':'NOT_ESTABLISHED',
             'spec':CONFIG, **short_horizon_source_identity()}]


def run_short_horizon_strength(bundle: MarketInput, *, cutoff=None, _context=None):
    sam, se = _shared()
    ctx = _context or se._prepare_context(bundle, cutoff)
    if ctx.bundle is not bundle and ctx.bundle != bundle:
        raise ValueError('SHORT_HORIZON_CONTEXT_INPUT_MISMATCH')
    if cutoff is not None and stamp(cutoff) != ctx.cutoff:
        raise ValueError('SHORT_HORIZON_CONTEXT_CUTOFF_MISMATCH')
    end = sam._session_index(ctx)
    short = ctx.sessions[max(0, end-24):end+1] if end is not None else []
    se._preload_bars(ctx, short, ctx.eligible_ids)
    rows = []
    source_unknown = complete25 = 0
    feature_unknown = Counter()
    for iid in ctx.eligible_ids:
        facts = collect_exact_features(ctx, iid)
        result = evaluate_exact_features(facts)
        source_unknown += result['current_price_evidence']['status'] == 'UNKNOWN'
        complete25 += facts['window_coverage']['complete_25']
        for group in ('returns', 'rs', 'ma'):
            for name,value in facts[group].items():
                feature_unknown[group + '.' + name] += value is None
        for name in ('A','previous_ma20','previous_ret5','prior5_close_high','clv'):
            feature_unknown[name] += facts[name] is None
        context = {key:condition('UNKNOWN', 'SHR_CONTEXT_NOT_EVALUATED_IN_PRICE_VOLUME_RESEARCH')
                   for key in ('market_compatibility','same_theme_context','sector_context','capital_linkage','behavioral_confirmation')}
        matched = result['matched_arms']
        result.update(strategy_id=SHORT_HORIZON_ID, implementation_version=IMPLEMENTATION_VERSION,
                      spec_revision=SPEC_REVISION, feature_version=FEATURE_VERSION, instrument_id=iid,
                      name=ctx.securities[iid].name, observation_date=ctx.observation_date,
                      status=result['decision_status'], research_status=result['status'],
                      formal_status='UNKNOWN' if result['decision_status'] in ('WATCH','UNKNOWN') else 'FAIL',
                      primary_stage=matched[0] if matched else None, alternate_stages=matched[1:],
                      parameter_status=PARAMETER_STATUS, parameter_status_label_zh=CONFIG['parameter_status_label_zh'],
                      amount_cny=sam._money(facts['amount']), turnover_fraction=sam._number(sam._turnover_fraction(se._bar(ctx,ctx.observation_date,iid))),
                      close=sam._number(facts['raw']['close'],places=8), comparable_close=sam._number(facts['close_comparable'],places=8),
                      comparable_basis=facts['comparable_basis'], A=sam._number(facts['A']), CLV=sam._number(facts['clv']),
                      current_source_refs=facts['current_source_refs'], limit_event_facts=facts['limit_event_facts'],
                      exact_features=_encoded_features(facts), context=context,
                      conditions={'current_price_evidence':result['current_price_evidence'], 'limit_safety':result['limit_safety'], **context},
                      reason_codes=sorted(set(result['current_price_evidence'].get('reason_codes',[])+result['limit_safety'].get('reason_codes',[]))),
                      watch_reasons=['SHR_RESEARCH_ONLY_EXECUTION_UNCONFIRMED'] if result['research_selected'] else [],
                      **{f'ret{w}_pct':sam._pct(value) for w,value in facts['returns'].items()})
        rows.append(result)
    rows.sort(key=lambda row:(not row['research_selected'],row['instrument_id']))
    for rank,row in enumerate(rows,1):
        row['rank']=row['display_rank']=rank
    decisions=Counter(row['decision_status'] for row in rows)
    research_ids=[row['instrument_id'] for row in rows if row['research_selected']]
    arm_counts={stage:Counter(row['arms'][stage]['status'] for row in rows) for stage in CONFIG['stage_order']}
    benchmarks={str(window):_encoded_features(_r1_market_return_at(ctx,end,window)[1]) for window in (1,3,5)}
    scope='FULL_SCOPE' if ctx.universe_complete else 'LIMITED_SCOPE'
    return {'strategy_id':SHORT_HORIZON_ID, **short_horizon_source_identity(),
            'parameter_status':PARAMETER_STATUS, 'parameter_status_label_zh':CONFIG['parameter_status_label_zh'],
            'status':'INSUFFICIENT_EVIDENCE' if research_ids or decisions['UNKNOWN'] or not ctx.universe_complete else 'EMPTY',
            'scope_label':scope, 'observation_date':ctx.observation_date, 'decision_cutoff':ctx.cutoff.isoformat(),
            'formal_list_policy':'RESEARCH_ONLY_NO_FORMAL_FOCUS_OR_CORE_PASS', 'market_edge':'NOT_ESTABLISHED',
            'production_promotion':'DISABLED', 'selection_policy_id':SELECTION_POLICY_ID,
            'research_readiness':'RESEARCH_MATCHES_AVAILABLE' if research_ids and ctx.universe_complete else 'LIMITED_OR_UNAVAILABLE_RESEARCH_EVIDENCE',
            'formal_readiness':'NOT_FORMAL_READY', 'items':rows,
            'diagnostics_reference':'items',
            'diagnostics_detail_policy':'ALL_ELIGIBLE_ROW_DETAILS_RETAINED_ONCE_IN_ITEMS',
            'top_ids':[], 'qualified_ids':[], 'research_selected_ids':research_ids,
            'eligible_pool_ids':list(ctx.eligible_ids), 'full_eligible_pool_retained':True,
            'counts':{'eligible':len(ctx.eligible_ids), 'evaluated':len(rows), 'focus':0, 'watch':decisions['WATCH'],
                      'rejected':decisions['REJECTED'], 'unknown':decisions['UNKNOWN'], 'passed':0,
                      'failed':decisions['REJECTED'], 'research_observation':len(research_ids),
                      'decision_counts':{state:decisions[state] for state in ('FOCUS','WATCH','REJECTED','UNKNOWN')},
                      'stage_counts':{stage:{state:counts[state] for state in ('PASS','FAIL','UNKNOWN')} for stage,counts in arm_counts.items()},
                      'current_source_unknown':source_unknown, 'complete_25_window':complete25,
                      'feature_unknown_counts':dict(feature_unknown)},
            'fact_coverage':{'eligible':len(rows),'current_source_unknown':source_unknown,
                             'complete_25_window':complete25,'feature_unknown_counts':dict(feature_unknown),
                             'qualification_does_not_prove_feature_readiness':True},
            'qualification':ctx.qualification, 'rejected_evidence':ctx.rejected_evidence,
            'market_benchmarks':benchmarks,
            'rank_semantics':'RESEARCH_MATCH_DISPLAY_ORDER_NOT_INVESTMENT_RANK',
            'tie_break':'research_selected then instrument_id asc',
            'traceability':{'product_role':'RESEARCH_OBSERVATION_ONLY','feature_version':FEATURE_VERSION,
                            'original_strategies_preserved':True,'old_run_overwrite':False,'labels_in_predicate':False,
                            'initial_parameters_are_not_verified':True,'future_prices_or_outcomes_used':False}}
