"""Versioned market explanation from an issued native fact capability only.

This attachment binds frozen run content without authenticating its selector.
The renderer reads saved facts; it cannot reconstruct a source capability.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
from pathlib import Path

from .history_artifacts import digest_json_stream, digest_market_input
from . import market_descriptive_context as native
from .theme_evolution import encoded, exact
from .visibility import CLOCKS, stamp

VERSION = 'MARKET_EXPLANATION_V1'
FEATURE_VERSION = 'NATIVE_MARKET_LEVEL_WINDOW_CHANGE_AND_COUNTEREVIDENCE_V1'
SPEC_REVISION = 'MARKET_EXPLANATION_SPEC_1'
_ROOT = Path(__file__).resolve().parents[2]
_SPEC = _ROOT / 'docs' / 'MARKET_EXPLANATION_V1.md'
AXES = ('market_amount_cny', 'rising_fraction', 'top10_trading_amount_share',
        'median_return_1d', 'median_turn_percent', 'return_iqr')
LEVEL_FIELDS = AXES + ('rising_count', 'falling_count', 'unchanged_count',
    'return_q25', 'return_q75', 'median_trading_amount_cny',
    'turn_at_least_5_percent_count', 'turn_valid_count', 'turn_missing_count',
    'turn_coverage_complete', 'turn_basis', 'expected_instrument_count',
    'trading_count', 'known_nontrading_count', 'expected_population_hash',
    'trading_population_hash', 'nontrading_population_hash')
FORMULA_SPEC = {
    'horizons': [1, 3, 5], 'window': 'T-h through T inclusive, h+1 actual exchange sessions',
    'completeness': 'Every intermediate session and axis required; no compression or zero fill',
    'amount_change': 'M(T)/M(T-h)-1, positive initial denominator required',
    'other_change': 'level(T)-level(T-h)',
    'level_slope': 'exact OLS sum((i-mean(i))*(level_i-mean(level)))/sum((i-mean(i))**2)',
    'counterevidence': ['AMOUNT_UP_BREADTH_DOWN', 'CONCENTRATION_UP_BREADTH_DOWN', 'MEDIAN_DAILY_RETURN_IMPROVING_AMOUNT_DOWN'],
    'visibility': 'Consumed original four-clock maxima, context cutoff and actual attachment creation <= as_of',
}


def source_identity():
    paths = (Path(__file__), _SPEC)
    hashes = {str(p.relative_to(_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    identity = {'implementation_version': VERSION, 'feature_version': FEATURE_VERSION,
        'spec_revision': SPEC_REVISION, 'formula_spec': deepcopy(FORMULA_SPEC),
        'source_file_hashes': hashes, 'native_dependency_source_hash': native.source_identity()['source_hash']}
    return {**identity, 'source_hash': digest_json_stream(identity)}


def _run_binding(bundle, run, cutoff):
    if not isinstance(run, dict) or not isinstance(run.get('run_id'), str) or not run['run_id']:
        raise ValueError('MARKET_EXPLANATION_FROZEN_RUN_REQUIRED')
    if (run.get('input_hash') != digest_market_input(bundle)
            or str(run.get('observation_date')) != str(bundle.observation_date)
            or stamp(run.get('decision_cutoff')) > cutoff):
        raise ValueError('MARKET_EXPLANATION_FROZEN_RUN_BINDING_MISMATCH')
    results = run.get('results')
    if not isinstance(results, dict) or not results:
        raise ValueError('MARKET_EXPLANATION_FROZEN_RUN_ITEMS_REQUIRED')
    items = {}
    for strategy, result in results.items():
        rows = result.get('items') if isinstance(result, dict) else None
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('MARKET_EXPLANATION_FROZEN_RUN_ITEMS_REQUIRED')
        ids = [row.get('instrument_id') for row in rows]
        if any(not isinstance(iid, str) or not iid for iid in ids) or len(ids) != len(set(ids)):
            raise ValueError('MARKET_EXPLANATION_FROZEN_RUN_ITEM_SCOPE_INVALID')
        items[strategy] = {'item_count': len(rows), 'items_content_hash': digest_json_stream(rows),
            'decision_counts': {state: sum(row.get('decision_status') == state for row in rows)
                                for state in ('FOCUS', 'WATCH', 'REJECTED', 'UNKNOWN')},
            'research_selected_ids': deepcopy(result.get('research_selected_ids'))}
    return {'run_id': run['run_id'], 'run_content_hash': digest_json_stream(run),
        'declared_run_content_hash': deepcopy(run.get('content_hash')),
        'input_hash': run['input_hash'], 'observation_date': str(run['observation_date']),
        'decision_cutoff': stamp(run['decision_cutoff']).isoformat(),
        'original_decision_cutoff': stamp(run['decision_cutoff']).isoformat(),
        'strategy_items': items, 'selection_results_content_hash': digest_json_stream(run.get('selection_results')),
        'binding_role': 'FROZEN_RUN_CONTENT_BOUND_NOT_SELECTOR_REAUTHENTICATED'}


def _unknown(reasons):
    return {'status': 'UNKNOWN', 'change': None, 'level_ols_slope_per_exchange_session': None,
            'reason_codes': sorted(set(reasons))}


def _window_axis(window, facts, axis, expected_count):
    if len(window) != expected_count:
        return _unknown(['MARKET_EXPLANATION_WINDOW_LEFT_CENSORED'])
    values, reasons = [], []
    for day in window:
        row = facts.get(day)
        if row is None:
            reasons.append('MARKET_EXPLANATION_SESSION_FACT_UNAVAILABLE:' + day)
            continue
        status = (row.get('axis_status') or {}).get(axis) or {}
        value = exact(row.get(axis))
        if status.get('status') != 'AVAILABLE' or value is None:
            reasons.append('MARKET_EXPLANATION_AXIS_UNAVAILABLE:' + day + ':' + axis)
            reasons.extend(status.get('reason_codes') or [])
        elif axis == 'median_turn_percent' and row.get('turn_coverage_complete') is not True:
            reasons.append('MARKET_EXPLANATION_TURN_COVERAGE_INCOMPLETE:' + day)
        else:
            values.append(value)
    if reasons:
        return _unknown(reasons)
    # Amount level slope remains known when an initial amount is zero; only
    # the relative-change denominator is unknown in that case.
    n = len(values); mean_x = Fraction(n - 1, 2); mean_y = sum(values, Fraction()) / n
    slope = sum(((Fraction(i) - mean_x) * (value - mean_y) for i, value in enumerate(values)), Fraction())
    slope /= sum(((Fraction(i) - mean_x) ** 2 for i in range(n)), Fraction())
    relative = axis == 'market_amount_cny'
    change = (values[-1] / values[0] - 1 if values[0] > 0 else None) if relative else values[-1] - values[0]
    return {'status': 'AVAILABLE' if change is not None else 'PARTIAL',
        'change': change, 'change_status': 'AVAILABLE' if change is not None else 'UNKNOWN',
        'change_role': 'RELATIVE_LEVEL_CHANGE' if relative else 'ENDPOINT_LEVEL_DIFFERENCE',
        'level_ols_slope_per_exchange_session': slope, 'slope_status': 'AVAILABLE',
        'initial_level': values[0], 'final_level': values[-1],
        'reason_codes': [] if change is not None else ['MARKET_EXPLANATION_NONPOSITIVE_AMOUNT_DENOMINATOR']}


def _counterevidence(axes):
    combinations = [('AMOUNT_UP_BREADTH_DOWN', 'market_amount_cny', 1, 'rising_fraction', -1),
        ('CONCENTRATION_UP_BREADTH_DOWN', 'top10_trading_amount_share', 1, 'rising_fraction', -1),
        ('MEDIAN_DAILY_RETURN_IMPROVING_AMOUNT_DOWN', 'median_return_1d', 1, 'market_amount_cny', -1)]
    out = []
    for code, left, ls, right, rs in combinations:
        a, b = axes[left]['change'], axes[right]['change']
        known = a is not None and b is not None
        matches = (a * ls > 0 and b * rs > 0) if known else None
        label = {'AMOUNT_UP_BREADTH_DOWN': '较窗口起点放量但上涨广度下降',
            'CONCENTRATION_UP_BREADTH_DOWN': '较窗口起点集中度增加但上涨广度下降',
            'MEDIAN_DAILY_RETURN_IMPROVING_AMOUNT_DOWN': '当日收益中位数改善但较窗口起点缩量'}[code]
        out.append({'code': code, 'label_zh': label, 'status': 'PRESENT' if matches is True else 'NOT_PRESENT' if matches is False else 'UNKNOWN',
            'axis_fields': [left, right], 'role': 'DESCRIPTIVE_COMBINATION_NOT_PREDICTIVE_SIGNAL',
            'reason_codes': [] if known else ['MARKET_EXPLANATION_COUNTEREVIDENCE_AXIS_UNKNOWN']})
    return out


def build_market_explanation(bundle, cap, *, cutoff, run):
    """Bind native market facts to unchanged run content, never select stocks."""
    cutoff = stamp(cutoff)
    native.validate_market_descriptive_context(cap, bundle=bundle, cutoff=cutoff)
    run_hash = digest_json_stream(run); identity = source_identity()
    binding = _run_binding(bundle, run, cutoff); context = cap.context
    day = str(bundle.observation_date)
    rows = [r for r in context['rows'] if r['business_session'] == day]
    if len(rows) != 1 or day not in context['requested_sessions']:
        raise ValueError('MARKET_EXPLANATION_OBSERVATION_ROW_REQUIRED')
    row = rows[0]; calendar = context['full_observed_calendar']; index = calendar.index(day)
    facts = {r['business_session']: r for r in context['dependency_session_facts']}
    if len(facts) != len(context['dependency_session_facts']):
        raise ValueError('MARKET_EXPLANATION_DUPLICATE_DEPENDENCY_SESSION')
    source_clocks = {ref: {key: stamp(meta['receipt'][key]).isoformat() for key in CLOCKS}
                     for ref, meta in context['consumed_sources'].items()}
    maxima = {key: max((stamp(c[key]) for c in source_clocks.values()), default=None) for key in CLOCKS}
    visible = max((clock for clock in maxima.values() if clock is not None), default=None)
    if visible is not None and visible > cutoff:
        raise ValueError('MARKET_EXPLANATION_ORIGINAL_SOURCE_AFTER_CONTEXT_CUTOFF')
    windows = []
    for h in (1, 3, 5):
        sessions = calendar[max(0, index - h):index + 1]
        axes = {axis: _window_axis(sessions, facts, axis, h + 1) for axis in AXES}
        populations = [facts.get(d, {}).get('trading_population_hash') for d in sessions]
        population_known = len(sessions) == h + 1 and all(populations)
        windows.append({'horizon_exchange_sessions': h, 'sessions': sessions,
            'expected_session_count': h + 1, 'axes': axes,
            'population_changed': len(set(populations)) > 1 if population_known else None,
            'population_comparison_status': 'AVAILABLE' if population_known else 'UNKNOWN',
            'trading_population_hashes': populations, 'counterevidence': _counterevidence(axes)})
    attachment = encoded({'schema_version': VERSION, 'feature_version': FEATURE_VERSION,
        'spec_revision': SPEC_REVISION, 'source_identity': identity, 'formula_spec': FORMULA_SPEC,
        'source_input_hash': digest_market_input(bundle), 'observation_date': day,
        'coverage_type': bundle.coverage_type, 'context_cutoff': cutoff.isoformat(),
        'frozen_run_binding': binding, 'native_context_hash': cap.context_hash,
        'native_source_identity': cap.source_identity, 'native_artifact_files': cap.artifact_files,
        'market_stream_descriptor_hash': context['market_stream_descriptor_hash'],
        'source_clock_maxima': {k: v.isoformat() if v is not None else None for k, v in maxima.items()},
        'actual_source_visible_at': visible.isoformat() if visible is not None else None,
        'consumed_source_four_clocks': source_clocks,
        'observation_levels': {k: deepcopy(row.get(k)) for k in LEVEL_FIELDS},
        'observation_axis_status': deepcopy(row['axis_status']),
        'prior20': {k: deepcopy(row[k]) for k in ('prior20_sessions', 'prior20_available_count',
            'prior20_window_complete', 'prior20_excludes_observation', 'prior20_market_amount_median_cny',
            'amount_ratio_to_prior20_median')},
        'source_panel_hash': row['source_panel_hash'], 'source_refs': row['source_refs'],
        'source_content_hashes': row['source_content_hashes'], 'windows': windows,
        'parameter_status': 'INITIAL_UNVALIDATED_DESCRIPTIVE_NOT_PREDICTIVE_RULE',
        'scope_role': context['scope_role'], 'return_role': context['return_role'],
        'selector_reauthenticated': False, 'stock_selection_changed': False, 'score_changed': False,
        'formal_focus_changed': False, 'selection_gate': False, 'historical_strategy_qualification': 'UNKNOWN',
        'official_pit_validation_count': 0, 'authenticated_emotion_cycle_count': 0,
        'market_edge': 'NOT_ESTABLISHED', 'saved_JSON_grants_source_capability': False,
        'research_limits': ['All-board daily trading population, not historical qualified stock/theme pool',
            'Population changes preserved; no fixed-member interpretation',
            'Endpoint market-distribution changes and level slopes, not holding returns or index performance',
            'No net fund inflow, theme competition, emotion cycle or predictive-edge inference']})
    native.validate_market_descriptive_context(cap, bundle=bundle, cutoff=cutoff)
    if (digest_json_stream(run) != run_hash or source_identity() != identity
            or cap.context_hash != attachment['native_context_hash']):
        raise ValueError('MARKET_EXPLANATION_BINDING_CHANGED_DURING_BUILD')
    created = datetime.now(timezone.utc)
    attachment['generated_at'] = attachment['first_created_at'] = created.isoformat()
    attachment['attachment_visible_at'] = max(created, cutoff, visible or created).isoformat()
    attachment['attachment_content_hash'] = digest_json_stream(attachment)
    return attachment


_DISPLAY_AXES = {
    'market_amount_cny': ('成交额', Fraction(1, 100000000), '亿元', Fraction(100), '%',
                          Fraction(1, 100000000), '亿元/交易日'),
    'rising_fraction': ('上涨广度', Fraction(100), '%', Fraction(100), 'pp', Fraction(100), 'pp/交易日'),
    'top10_trading_amount_share': ('Top10成交集中度', Fraction(100), '%', Fraction(100), 'pp', Fraction(100), 'pp/交易日'),
    'median_return_1d': ('当日收益中位数', Fraction(100), '%', Fraction(100), 'pp', Fraction(100), 'pp/交易日'),
    # Provider turn is already expressed in percent, rather than a fraction.
    'median_turn_percent': ('流通股换手中位数', Fraction(1), '%', Fraction(1), 'pp', Fraction(1), 'pp/交易日'),
    'return_iqr': ('收益分布IQR', Fraction(100), '%', Fraction(100), 'pp', Fraction(100), 'pp/交易日'),
}


def _display_decimal(value, *, scale=Fraction(1), unit='', signed=False, places=2):
    """Round exact facts for display without floats or unbounded int-to-str.

    Resolve a half-even tie in the exact rational before the Decimal conversion;
    finite Decimal precision alone could erase the side of an extremely close tie.
    The resulting display never replaces the saved exact fraction.
    """
    value = exact(value)
    if value is None:
        return 'UNKNOWN'
    scaled = abs(value * scale) * (10 ** places)
    integral, remainder = divmod(scaled.numerator, scaled.denominator)
    if 2 * remainder > scaled.denominator or (2 * remainder == scaled.denominator and integral % 2):
        integral += 1
    decimal = Decimal(integral)  # Accepts an int directly, without str(huge_int).
    if decimal.adjusted() - places > 12:
        return '数值超出显示范围（精确值见JSON）'
    with localcontext() as ctx:
        ctx.prec = 40
        decimal = decimal.scaleb(-places)
        sign = '-' if value < 0 else '+' if signed else ''
        return sign + format(decimal, '.' + str(places) + 'f') + unit


def _display_count(value, known):
    value = exact(value) if known else None
    return (_display_decimal(value, places=0)
            if value is not None and value.denominator == 1 and value >= 0 else 'UNKNOWN')


def _display_level(attachment, axis, known):
    states = attachment.get('observation_axis_status') or {}
    levels = attachment.get('observation_levels') or {}
    if (not known or (states.get(axis) or {}).get('status') != 'AVAILABLE'
            or (axis == 'median_turn_percent' and levels.get('turn_coverage_complete') is not True)):
        return 'UNKNOWN'
    _, scale, unit, *_ = _DISPLAY_AXES[axis]
    return _display_decimal(levels.get(axis), scale=scale, unit=unit)


def _display_window_cell(window, axis, known):
    data = ((window or {}).get('axes') or {}).get(axis) or {}
    if not known or data.get('status') not in ('AVAILABLE', 'PARTIAL'):
        return 'UNKNOWN'
    _, _, _, change_scale, change_unit, slope_scale, slope_unit = _DISPLAY_AXES[axis]
    change = _display_decimal(data.get('change'), scale=change_scale, unit=change_unit, signed=True)
    slope = _display_decimal(data.get('level_ols_slope_per_exchange_session'),
        scale=slope_scale, unit=slope_unit, signed=True) if data.get('slope_status') == 'AVAILABLE' else 'UNKNOWN'
    return change + '；斜率 ' + slope


def _display_prior20(attachment, known):
    prior = attachment.get('prior20') or {}
    states = attachment.get('observation_axis_status') or {}
    complete = known and prior.get('prior20_window_complete') is True
    amount_known = complete and (states.get('prior20_market_amount_median_cny') or {}).get('status') == 'AVAILABLE'
    ratio_known = complete and (states.get('amount_ratio_to_prior20_median') or {}).get('status') == 'AVAILABLE'
    amount = _display_decimal(prior.get('prior20_market_amount_median_cny') if amount_known else None,
        scale=Fraction(1, 100000000), unit='亿元')
    ratio = _display_decimal(prior.get('amount_ratio_to_prior20_median') if ratio_known else None, unit='倍')
    exclusion = '不含观察日' if prior.get('prior20_excludes_observation') is True else '是否排除观察日UNKNOWN'
    return ('前20日（' + exclusion + '）：可用 ' + _display_count(prior.get('prior20_available_count'), known)
        + '/20；成交额中位数 ' + amount + '；当前成交额/前20日中位数 ' + ratio + '。')


def render_market_explanation(attachment, *, as_of):
    """Display hash-bound saved facts only, with explicit late-visibility status."""
    if (not isinstance(attachment, dict) or attachment.get('schema_version') != VERSION
            or digest_json_stream({k: v for k, v in attachment.items() if k != 'attachment_content_hash'})
                != attachment.get('attachment_content_hash')):
        raise ValueError('MARKET_EXPLANATION_SAVED_ATTACHMENT_HASH_MISMATCH')
    as_of = stamp(as_of)
    clocks = attachment.get('consumed_source_four_clocks')
    if not isinstance(clocks, dict):
        raise ValueError('MARKET_EXPLANATION_SAVED_CLOCKS_REQUIRED')
    actual = {key: max((stamp(c[key]) for c in clocks.values()), default=None) for key in CLOCKS}
    for key in CLOCKS:
        expected = stamp(attachment['source_clock_maxima'][key]) if attachment['source_clock_maxima'][key] else None
        if actual[key] != expected:
            raise ValueError('MARKET_EXPLANATION_SAVED_CLOCK_MAXIMUM_MISMATCH')
    visible = max((v for v in actual.values() if v is not None), default=None)
    declared = stamp(attachment['actual_source_visible_at']) if attachment['actual_source_visible_at'] else None
    if visible != declared:
        raise ValueError('MARKET_EXPLANATION_SAVED_VISIBILITY_MISMATCH')
    prefix = ['市场解释附件 ' + VERSION, '观察日：' + attachment['observation_date'],
        '原run截止：' + attachment['frozen_run_binding']['original_decision_cutoff'],
        '附件截止：' + attachment['context_cutoff'],
        '附件实际生成：' + attachment['generated_at'],
        '附件首次创建：' + attachment['first_created_at'],
        '原件四时钟最大值：' + '；'.join(label + ' ' + (actual[key].isoformat() if actual[key] else 'UNKNOWN')
            for key, label in zip(CLOCKS, ('事件', '发布', '首次见到', '入库'))),
        '保存事实展示；本次不恢复原生来源能力、不重新认证selector。']
    created = max(stamp(attachment['generated_at']), stamp(attachment['first_created_at']))
    effective_visibility = max(created, stamp(attachment['context_cutoff']), visible or created)
    if stamp(attachment['attachment_visible_at']) != effective_visibility:
        raise ValueError('MARKET_EXPLANATION_SAVED_ATTACHMENT_VISIBILITY_MISMATCH')
    if effective_visibility > as_of:
        return '\n'.join(prefix + ['NOT_YET_VISIBLE：原件四时钟、附件截止或实际生成晚于报告as_of；不展示数值。'])
    if visible is None:
        prefix.append('UNKNOWN：没有可用消费来源四时钟；市场事实与窗口仍逐轴展示UNKNOWN。')
    known = visible is not None
    windows = {w['horizon_exchange_sessions']: w for w in attachment['windows']}
    prefix += ['', '| 指标 | 当前水平 | 1日变化 | 3日变化 | 5日变化 |',
        '| --- | --- | --- | --- | --- |']
    for axis in AXES:
        cells = [_DISPLAY_AXES[axis][0], _display_level(attachment, axis, known)]
        cells.extend(_display_window_cell(windows.get(h), axis, known) for h in (1, 3, 5))
        prefix.append('| ' + ' | '.join(cells) + ' |')
    levels = attachment.get('observation_levels') or {}
    count = lambda key: _display_count(levels.get(key), known)
    coverage = ({True: '完整', False: '不完整'}.get(levels.get('turn_coverage_complete'), 'UNKNOWN')
                if known else 'UNKNOWN')
    prefix += ['', '交易家数：' + count('trading_count') + '；上涨：' + count('rising_count')
        + '；下跌：' + count('falling_count') + '；平盘：' + count('unchanged_count')
        + '；来源总体：' + count('expected_instrument_count') + '；已知未交易：' + count('known_nontrading_count') + '。',
        '换手完整性：' + coverage + '；有效 ' + count('turn_valid_count') + '/' + count('trading_count')
        + '；缺失 ' + count('turn_missing_count') + '；换手≥5% ' + count('turn_at_least_5_percent_count') + '家。',
        _display_prior20(attachment, known),
        '显示保留两位小数；pp为百分点。斜率描述窗口内每日水平，精确值与逐轴未知原因保存在JSON。', '']
    labels = {'AMOUNT_UP_BREADTH_DOWN': '较窗口起点放量但上涨广度下降',
        'CONCENTRATION_UP_BREADTH_DOWN': '较窗口起点集中度增加但上涨广度下降',
        'MEDIAN_DAILY_RETURN_IMPROVING_AMOUNT_DOWN': '当日收益中位数改善但较窗口起点缩量'}
    for h in (1, 3, 5):
        window = windows.get(h) or {}
        changed = window.get('population_changed')
        population = ('交易总体有变化，不能按固定成员解释' if changed is True else '交易总体未变化' if changed is False else 'UNKNOWN')
        if not known or window.get('population_comparison_status') != 'AVAILABLE':
            population = 'UNKNOWN'
        evidence = {r.get('code'): r.get('status') for r in window.get('counterevidence') or []}
        descriptions = [label + '：' + ({'PRESENT': '观察到', 'NOT_PRESENT': '本窗口未出现'}.get(evidence.get(code), 'UNKNOWN')
                                      if known else 'UNKNOWN') for code, label in labels.items()]
        prefix += [str(h) + '交易日窗（' + ' → '.join(window.get('sessions') or []) + '）：' + population + '。',
            '描述反证：' + '；'.join(descriptions) + '。']
    return '\n'.join(prefix + ['', '上述变化使用完整交易日窗，不是持仓收益；总体变化、四板市场事实不能认证历史合格股池。',
        '初始描述参数尚未验证；不推断资金净流入、题材竞争、游资、情绪周期或预测能力；MARKET_EDGE=NOT_ESTABLISHED。'])
