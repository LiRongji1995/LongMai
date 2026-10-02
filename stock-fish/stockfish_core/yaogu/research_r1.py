"""Explicit research adapter. Never imported by the active daily strategy."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from . import stage_aware_momentum as v2
from .contracts import digest
from .research_r1_contracts import coverage

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / 'config/stage_aware_research_r1.json'
SPEC_PATH = ROOT / 'docs/R1_REPAIR_SPEC_V3.md'


def config():
    return json.loads(CONFIG_PATH.read_text())


def group_options(name):
    groups = config()['groups']
    if name not in groups:
        raise ValueError('UNKNOWN_RESEARCH_GROUP')
    result = dict(acceleration=False, independent=False, turnover=True,
                  setup_trend=False, market=True, supply=True, theme=True, funds=True, unknown_limit_guard=False)
    chain = []
    while name:
        chain.append(groups[name]['changes'])
        name = groups[name]['parent']
    for changes in reversed(chain):
        result.update(changes)
    return result


def identity():
    paths = [CONFIG_PATH, SPEC_PATH, ROOT / 'docs/STAGE_AWARE_RESEARCH_R1.md', Path(__file__),
             Path(__file__).with_name('execution_research_r1.py'),
             Path(__file__).with_name('research_r1_io.py'), *sorted(Path(__file__).parent.glob('research_r1_*.py')), Path(__file__).parents[1] / 'storage.py']
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    hashes.update(v2.stage_aware_source_identity()['stage_aware_source_file_hashes'])
    hashes['scripts/codex_research.py'] = hashlib.sha256((ROOT / 'scripts/codex_research.py').read_bytes()).hexdigest()
    return dict(strategy_id=config()['strategy_id'], source_hash=digest(hashes), source_files=hashes,
                config_hash=digest(config()), spec_hash=hashes[str(SPEC_PATH.relative_to(ROOT))])


def condition(status, code, **details):
    return dict(status=status, reason_codes=[] if status == 'PASS' else [code], **details)


def compare(value, threshold, code, *, strict=False):
    if value is None:
        return condition('UNKNOWN', code + '_UNAVAILABLE')
    value, threshold = Fraction(str(value)), Fraction(str(threshold))
    return condition('PASS' if (value > threshold if strict else value >= threshold) else 'FAIL', code,
                     value=str(value), threshold=str(threshold))


def all_of(parts):
    return condition(v2._combine(parts.values()), 'R1_REQUIRED_CONDITIONS', conditions=parts)


def any_of(parts):
    statuses = [x['status'] for x in parts.values()]
    return condition('PASS' if 'PASS' in statuses else 'UNKNOWN' if 'UNKNOWN' in statuses or not statuses else 'FAIL',
                     'R1_ALTERNATIVES', conditions=parts)


def acceleration(returns, rs, momentum, high_turnover_days):
    parts = {'absolute_strength': any_of({
        'ret5': compare(returns.get('5'), '.15', 'RET5'),
        'ret10': compare(returns.get('10'), '.25', 'RET10')}),
        'rs5': compare(rs.get('5'), 0, 'RS5', strict=True),
        'persistence': compare(high_turnover_days, 3, 'TURNOVER_5_INCLUDING_T'),
        'momentum': condition('UNKNOWN' if momentum == 'UNKNOWN' else 'PASS' if momentum == 'RISING' else 'FAIL', 'MOMENTUM_NOT_RISING')}
    for window in ('1', '3'):
        parts['ret' + window] = compare(returns.get(window), 0, 'RET' + window, strict=True)
        parts['rs' + window] = compare(rs.get(window), 0, 'RS' + window, strict=True)
    result = all_of(parts)
    result.update(setup_id='ACCELERATION', setup_state='ACCELERATION_CONFIRMED' if result['status'] == 'PASS' else 'ACCELERATION_NOT_CONFIRMED')
    return result


def ma20_trend(close, current, previous):
    return all_of({'close_above_ma20': compare(None if close is None or current is None else close-current, 0, 'C_GT_MA20', strict=True),
                   'ma20_slope': compare(None if current is None or previous is None else current-previous, 0, 'MA20_NONDECREASING')})


def theme_gate(setup, row, funds_rows, *, theme_hard, funds_hard):
    if theme_hard and funds_hard:
        return v2._same_theme_context(setup, row['conditions']['sector_context'], row['conditions']['capital_linkage'], row['sector_candidates'], row['funds_candidates'])
    if not theme_hard and not funds_hard:
        return condition('PASS', 'BACKGROUND_TAG_ONLY')
    if funds_hard:
        return any_of({x['theme_id']: x['funds_condition'] for x in funds_rows})
    return any_of({x['theme_id']: condition({'SUPPORTIVE': 'PASS', 'NEGATIVE': 'FAIL'}.get(
        x.get('setup_compatibility', {}).get(setup, {}).get('context_status'), 'UNKNOWN'), 'SECTOR_NOT_SUPPORTIVE',
        evidence=x.get('setup_compatibility', {}).get(setup, {})) for x in row['sector_candidates']})


def adapt_row(row, facts, name, *, display_order=None):
    options = group_options(name)
    if name == 'V2_CONTROL':
        return row
    stages = deepcopy(row['conditions']['setup_identification'])
    if options['acceleration']:
        stages['stage_candidates']['ACCELERATION'] = facts['acceleration']
    passing = [s for s in v2.PRIMARY_STAGE_PRECEDENCE if stages['stage_candidates'][s]['status'] == 'PASS']
    stages.update(primary_stage=passing[0] if passing else None,
                  status='PASS' if passing else 'UNKNOWN' if any(x['status'] == 'UNKNOWN' for x in stages['stage_candidates'].values()) else 'FAIL')
    stages['alternate_stages'] = passing[1:]
    stages['reason_codes'] = [] if passing else ['R1_NO_PASSING_SETUP' if stages['status'] == 'FAIL' else 'R1_SETUP_UNKNOWN']
    if not options['independent']:
        primary = stages['primary_stage']
        decision = v2._decision(row['conditions']['liquidity_bottom'], row['trend_strength'], stages,
            v2._behavioral_confirmation(primary, facts['path'], stages),
            v2._market_compatibility(primary, row['market_status']),
            theme_gate(primary, row, facts['funds'], theme_hard=True, funds_hard=True),
            v2._critical_counterevidence(facts['path'], stages, row['supply_pressure'], row['trend_strength']))
        conditions = deepcopy(row['conditions'])
        conditions.update(setup_identification=stages,
            behavioral_confirmation=v2._behavioral_confirmation(primary, facts['path'], stages),
            market_compatibility=v2._market_compatibility(primary, row['market_status']),
            same_theme_context=theme_gate(primary, row, facts['funds'], theme_hard=True, funds_hard=True),
            critical_counterevidence=v2._critical_counterevidence(facts['path'], stages, row['supply_pressure'], row['trend_strength']))
        adapted = dict(instrument_id=row['instrument_id'], strategy_id=config()['strategy_id'], research_group=name,
            status=decision['decision_status'], decision_status=decision['decision_status'], primary_stage=primary,
            primary_setup=primary, decision_layer=decision, stage_candidates=stages['stage_candidates'],
            alternate_stages=[setup for setup in passing if setup != primary], conditions=conditions,
            condition_statuses={key:value['status'] for key,value in conditions.items()},
            reason_codes=sorted(set(decision['reason_codes'] + v2._condition_reasons(conditions.values()))),
            watch_reasons=decision.get('watch_reasons', []),
            setup_rank_features=v2._setup_rank_features(dict(row, primary_stage=primary)))
        return adapted
    common = {'liquidity': row['conditions']['liquidity_bottom'] if options['turnover'] else facts['amount']}
    # Preserve original safety semantics in one-change controls. FULL adds the requested unknown guard explicitly.
    flag = facts['path'].get('close_limit_down')
    common['close_limit_down'] = condition('FAIL' if flag == v2.TRUE else 'UNKNOWN' if options['unknown_limit_guard'] and flag != v2.FALSE else 'PASS', 'CLOSE_LIMIT_DOWN_UNKNOWN_OR_TRUE')
    common_result = all_of(common)
    setups = {}
    for setup in v2.STAGE_IDS:
        if common_result['status'] == 'FAIL':
            setups[setup] = condition('NOT_EVALUATED', 'COMMON_HARD_FAIL')
            continue
        trend = facts['ma20'] if options['setup_trend'] and setup != 'TREND_PULLBACK' else row['trend_strength']
        critical = v2._critical_counterevidence(facts['path'], stages,
            row['supply_pressure'] if options['supply'] else condition('PASS', 'SUPPLY_TAG_ONLY'),
            trend if not options['setup_trend'] or setup == 'TREND_PULLBACK' else {})
        parts = {'common': common_result, 'trend': trend, 'identification': stages['stage_candidates'][setup],
                 'behavior': v2._behavioral_confirmation(setup, facts['path'], stages), 'critical': critical}
        if options['market']:
            parts['market'] = v2._market_compatibility(setup, row['market_status'])
        if options['theme'] or options['funds']:
            parts['theme_funds'] = theme_gate(setup, row, facts['funds'], theme_hard=options['theme'], funds_hard=options['funds'])
        setups[setup] = all_of(parts)
    state = 'FAIL' if common_result['status'] == 'FAIL' else any_of(setups)['status']
    matched = sorted(s for s, r in setups.items() if r['status'] == 'PASS')
    order = tuple(display_order or v2.STAGE_IDS)
    if set(order) != set(v2.STAGE_IDS) or len(order) != len(v2.STAGE_IDS):
        raise ValueError('INVALID_DISPLAY_ORDER')
    return dict(instrument_id=row['instrument_id'], candidate_status='CORE_' + state,
        common=common_result, setups=setups, matched_setups=matched,
        primary_setup=next((s for s in order if s in matched), None),
        background={k: row[k] for k in ('market_status', 'sector_candidates', 'funds_candidates', 'supply_pressure', 'trend_strength')},
        independent_theme_funds=facts['funds'], turnover_fraction=row['turnover_fraction'],
        setup_rank_features={setup: v2._setup_rank_features(dict(row, primary_stage=setup)) for setup in v2.STAGE_IDS}, evidence_basis='DAILY_OHLC_ONLY')


def run_research(bundle, *, cutoff, groups=None, display_order=None, include_diagnostics=False):
    names = list(groups or config()['groups'])
    for name in names:
        group_options(name)
    from .research_r1_storage import bounded_context
    ctx, control = v2._evaluate_rows(bundle, cutoff=cutoff, _context=bounded_context(bundle,cutoff) if bundle.history_artifact_ref else None)
    index = v2._session_index(ctx)
    visible_themes, rejected = v2._visible_stage_themes(ctx)
    results = {name: [] for name in names}
    for row in control['items']:
        iid = row['instrument_id']
        returns = v2._return_map(ctx, iid, index)
        rs = {str(w): v2._rs_at(ctx, iid, index, w)[0] for w in (1, 3, 5)}
        current, _ = v2._ma_value(ctx, iid, index, 20)
        previous, _ = v2._ma_value(ctx, iid, index-5, 20) if index >= 5 else (None, None)
        funds = []
        for theme in visible_themes:
            if iid in theme.get('member_ids', []):
                cond, metrics = v2._funds_for_theme(ctx, iid, theme, index)
                funds.append(dict(theme_id=theme['theme_id'], funds_condition=cond, funds_metrics=metrics))
        facts = dict(path=v2._daily_path(ctx, iid), funds=funds,
            amount=compare(v2._amount_fraction(v2._bar(ctx, ctx.observation_date, iid)), 500000000, 'AMOUNT_500M'),
            ma20=ma20_trend(v2._close_value(ctx, iid, index), current, previous),
            acceleration=acceleration(returns, rs, row['shared_strength_momentum']['strength_momentum'], row['high_turnover_days_5_including_T']))
        for name in names:
            results[name].append(adapt_row(row, facts, name, display_order=display_order))
    assembled = {}
    for name, rows in results.items():
        if name == 'V2_ACCEL_ONLY':
            v2._rank_rows(rows)
        selected = sorted(r['instrument_id'] for r in rows if r.get('candidate_status') == 'CORE_PASS' or r.get('decision_status') == 'FOCUS')
        dimensions = coverage(rows, ctx.qualification, ctx.universe_complete)
        counts = Counter(r.get('candidate_status', r.get('decision_status')) for r in rows)
        assembled[name] = dict(coverage=dimensions, items=rows, selected_ids=selected, counts=dict(counts),
            status='CANDIDATES' if selected else 'INSUFFICIENT_EVIDENCE' if counts['CORE_UNKNOWN'] or counts['UNKNOWN'] or counts['WATCH'] else 'INSUFFICIENT_EVIDENCE' if not ctx.universe_complete else 'EMPTY',
            options=group_options(name), group_config_hash=digest(group_options(name)))
    from .research_r1_diagnostics import diagnose
    diagnostics=diagnose(ctx) if include_diagnostics else None
    return v2._public(dict(coverage_diagnostics=diagnostics, **identity(), input_hash=digest(bundle.model_dump(mode='json')),
        observation_date=str(ctx.observation_date), decision_cutoff=ctx.cutoff.isoformat(),
        coverage_type=str(bundle.coverage_type), product_role='RESEARCH_ONLY',
        parameter_status=config()['parameter_status'], market_edge='NOT_ESTABLISHED', production_promotion='DISABLED',
        groups=assembled, v2_control=control, rejected_themes=rejected,
        run_status='SUCCEEDED', qualification=ctx.qualification, rejected_evidence=ctx.rejected_evidence, scope_label=control['scope_label']))
