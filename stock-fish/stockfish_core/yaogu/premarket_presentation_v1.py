"""Saved R2 presentation only: no source authority, selection, DB or network.

``core_run`` is the JSON-shaped original run. ``theme_context`` is the explicit
PREMARKET_THEME_CONTEXT_V1 backend result, never a recovered live capability.
The owner authenticates both inputs and retains the canonical run and five
official exports. This module links those originals, preserves the entire
selection hash/order, and writes three *display* artifacts in a fresh directory.
It does not claim currentness, historical PIT, source verification or a new run.
"""
from __future__ import annotations

from copy import deepcopy
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import os
from pathlib import Path


VERSION = 'PREMARKET_PRESENTATION_V1'
THEME_SCHEMA = 'PREMARKET_THEME_CONTEXT_V1'
CATEGORIES = ('confirmed_mainlines', 'fermentation_watch',
              'active_divergence_mainlines', 'qualified_theme_data_incomplete')
CATEGORY_LABELS = dict(confirmed_mainlines='确认', fermentation_watch='发酵观察，尚未确认',
                       active_divergence_mainlines='活跃分歧',
                       qualified_theme_data_incomplete='诊断资料不完整')
STAGE_LABELS = dict(STRONG_START='强势启动', STRONG_CONTINUATION='强势延续',
                    FIRST_PULLBACK='首次回撤')
OFFICIAL_EXPORTS = {'all', 'observation', 'focus', 'unselected', 'observation_json'}
FP_FIELDS = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns',
             'st_ctime_ns', 'st_flags')


def _need(value, reason):
    if not value:
        raise ValueError(reason)


def _json(value, *, sort_keys=True):
    return json.dumps(value, ensure_ascii=False, sort_keys=sort_keys,
                      separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _clock(value):
    instant = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    _need(instant.tzinfo is not None and instant.utcoffset() is not None,
          'AWARE_CLOCK_REQUIRED')
    return instant


def _money(value):
    if value is None:
        return None
    _need(type(value) is not bool, 'AMOUNT_MUST_NOT_BE_BOOLEAN')
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError('INVALID_ORIGINAL_AMOUNT') from error
    _need(number.is_finite() and number >= 0, 'FINITE_NONNEGATIVE_AMOUNT_REQUIRED')
    return number


def _selection(core):
    _need(isinstance(core, dict), 'ORIGINAL_CORE_RUN_REQUIRED')
    strategy = core.get('strategy_id') or 'SHORT_HORIZON_STRENGTH_R2'
    _need(strategy == 'SHORT_HORIZON_STRENGTH_R2', 'SAVED_R2_PRESENTATION_ONLY')
    arm = core['results'][strategy]
    _need(isinstance(arm['items'], list), 'ORIGINAL_ITEMS_LIST_REQUIRED')
    selected = [deepcopy(row) for row in arm['items'] if row.get('research_selected') is True]
    ids = [row['instrument_id'] for row in selected]
    _need(all(isinstance(iid, str) and iid for iid in ids) and len(ids) == len(set(ids)),
          'UNIQUE_ORIGINAL_SELECTED_IDENTITIES_REQUIRED')
    _need(all(isinstance(row.get('name'), str) and row['name'] for row in selected),
          'ORIGINAL_SELECTED_NAMES_REQUIRED')
    _need(isinstance(core.get('run_id'), str) and core['run_id'], 'ORIGINAL_RUN_ID_REQUIRED')
    observed = date.fromisoformat(core['observation_date'])
    target = date.fromisoformat(core['target_session'])
    _need(target > observed, 'ORIGINAL_TARGET_MUST_FOLLOW_OBSERVATION')
    counts = deepcopy(arm.get('counts') or {})
    focus = counts.get('focus')
    _need(focus is None or type(focus) is int and focus >= 0, 'ORIGINAL_FOCUS_COUNT_REQUIRED')
    return strategy, selected, counts


def _risk(value):
    if value is None:
        return None
    _need(isinstance(value, dict), 'ORIGINAL_RISK_OBJECT_OR_NULL_REQUIRED')
    flag = value.get('flag')
    _need(flag is None or type(flag) is bool, 'RISK_FLAG_MUST_REMAIN_THREE_VALUED')
    return deepcopy(value)


def _risk_label(value):
    flag = None if value is None else value.get('flag')
    label = '高风险' if flag is True else '本模型高风险未触发' if flag is False else '高风险未知'
    if value is not None:
        label += '；状态 ' + str(value.get('status')) + '；条件 ' + _json(value.get('conditions'))
    return label


def _acquisition_summary(value):
    """Keep parent failure facts; the complete records remain in its artifact.

    Capture facts may contain literal intersections with the *full* original
    selection. They must not be republished in a threshold-filtered report.
    """
    if value is None:
        return None
    _need(isinstance(value, dict), 'PARENT_ACQUISITION_METADATA_OR_NULL_REQUIRED')
    keys = ('schema_version', 'status', 'parent_capture_status', 'parent_exit_code',
            'source_terminal_verified', 'COMPLETE', 'PARTIAL', 'FETCH_FAILED',
            'requested_members', 'original_selected_count', 'cleanup_error')
    result = {key:deepcopy(value[key]) for key in keys if key in value}
    result['complete_original_metadata_hash'] = _digest(value)
    result['role'] = 'PARENT_ACQUISITION_METADATA_SUMMARY_NOT_SOURCE_AUTHORITY'
    result['full_records_not_embedded_in_display'] = True
    if isinstance(value.get('records'), list):
        states = {}
        for record in value['records']:
            status = record.get('status')
            label = 'UNKNOWN' if status is None else str(status)
            states[label] = states.get(label, 0) + 1
        result['record_status_counts'] = states
    return result


def _theme_data(context, core, selected, displayed_ids):
    """Project a backend result; an ordinary dict never gains source capability."""
    empty = dict(source_verified=None, status='UNKNOWN', context_cutoff=None,
                 counts=None, unknown=None, parent_acquisition=None,
                 diagnostic_status='UNKNOWN', category_counts=None,
                 displayed_category_counts=None, summaries=[], provided_summaries=[],
                 report_banner_required=None, report_banner=None)
    if context is None:
        return {}, {row['instrument_id']: [] for row in selected}, empty
    _need(isinstance(context, dict) and context.get('schema_version') == THEME_SCHEMA,
          'EXPLICIT_THEME_BACKEND_SCHEMA_REQUIRED')
    _need(all(context.get(key) == core.get(key) for key in
              ('run_id', 'observation_date', 'target_session')), 'THEME_CORE_RUN_DATE_BINDING')
    verified = context.get('source_verified')
    _need(verified is None or type(verified) is bool, 'SOURCE_VERIFICATION_THREE_VALUES')
    cutoff = context.get('context_cutoff')
    if cutoff is not None:
        _clock(cutoff)
        if core.get('decision_cutoff') is not None:
            _need(_clock(cutoff) >= _clock(core['decision_cutoff']), 'CONTEXT_PRECEDES_ORIGINAL_PRICE_CUTOFF')
    _need(verified is not True or cutoff is not None, 'VERIFIED_CONTEXT_CUTOFF_REQUIRED')
    source = context.get('source_context') or {}
    themes = source.get('themes') or []
    _need(isinstance(themes, list), 'BACKEND_THEME_LIST_REQUIRED')
    by_theme = {}
    for theme in themes:
        tid = theme['theme_id']
        _need(tid not in by_theme and isinstance(theme.get('member_ids'), list),
              'UNIQUE_COMPLETE_BACKEND_THEME_REQUIRED')
        _need(len(theme['member_ids']) == len(set(theme['member_ids'])),
              'DUPLICATE_THEME_MEMBER_ID')
        by_theme[tid] = theme
    original_ids = {row['instrument_id'] for row in selected}
    scope = context.get('scope')
    _need(isinstance(scope, dict) and scope.get('kind') == 'INTERSECTION_WITH_FULL_ORIGINAL_SELECTION'
          and scope.get('selected_ids') == [row['instrument_id'] for row in selected],
          'COMPLETE_ORIGINAL_THEME_SCOPE_BINDING')
    relations = {iid: [] for iid in original_ids}
    diagnostic = context.get('diagnostic')
    summaries, provided = [], []
    counts = {key: 0 for key in CATEGORIES} if verified is True and diagnostic is not None else None
    shown_counts = deepcopy(counts)
    if diagnostic is not None:
        _need(isinstance(diagnostic, dict), 'BACKEND_DIAGNOSTIC_OR_NULL_REQUIRED')
        _need(all(isinstance(diagnostic.get(category), list) for category in CATEGORIES),
              'COMPLETE_DIAGNOSTIC_CATEGORY_LISTS_REQUIRED_NOT_UNKNOWN_AS_ZERO')
        for category in CATEGORIES:
            for row in diagnostic[category]:
                ids = row['selected_ids']
                _need(isinstance(ids, list) and len(ids) == len(set(ids)) and
                      set(ids) <= original_ids, 'DIAGNOSTIC_MUST_STAY_WITHIN_ORIGINAL_SELECTION')
                shown = [iid for iid in ids if iid in displayed_ids]
                summary = dict(theme_id=row['theme_id'], name=row['name'],
                               category=category, stage=deepcopy(row.get('stage')),
                               high_risk=_risk(row.get('high_risk')),
                               original_selected_count=len(ids),
                               displayed_selected_count=len(shown), displayed_selected_ids=shown)
                if verified is True:
                    _need(row['theme_id'] in by_theme, 'DIAGNOSTIC_CURRENT_SOURCE_PARENT_REQUIRED')
                    _need(set(ids) <= set(by_theme[row['theme_id']]['member_ids']),
                          'DIAGNOSTIC_SELECTED_IDS_MUST_BELONG_TO_SOURCE_COHORT')
                    if ids:
                        counts[category] += 1
                    if shown:
                        shown_counts[category] += 1
                        summaries.append(summary)
                    for iid in ids:
                        relations[iid].append(deepcopy(summary))
                elif shown:
                    # Preserve supplied None/False risk facts without promoting an
                    # unverified source into an actionable model relationship.
                    provided.append(dict(summary, status='UNVERIFIED_NOT_USED_FOR_GROUPING'))
    usable = {}
    if verified is True:
        for tid, theme in by_theme.items():
            if theme.get('source_verified', True) is True:
                usable[tid] = theme
    result = dict(source_verified=verified, status=deepcopy(context.get('status')),
                  context_cutoff=cutoff, counts=deepcopy(source.get('counts')),
                  unknown=deepcopy(source.get('unknown')),
                  parent_acquisition=_acquisition_summary(context.get('parent_acquisition')),
                  diagnostic_status='COPIED_BACKEND_DIAGNOSTIC' if counts is not None else 'UNKNOWN',
                  category_counts=counts, displayed_category_counts=shown_counts,
                  summaries=summaries, provided_summaries=provided,
                  report_banner_required=None if diagnostic is None else deepcopy(diagnostic.get('report_banner_required')),
                  report_banner=None if diagnostic is None else deepcopy(diagnostic.get('report_banner')))
    return usable, relations, result


def _write(path, payload):
    with path.open('xb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    info = path.lstat()
    return dict(path=str(path), sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload),
                fp={key: getattr(info, key, 0) for key in FP_FIELDS})


def render_premarket(core_run: dict, theme_context: dict | None, output_dir,
                     minimum_amount_cny=300000000, provenance=None):
    """Write TXT/JSON/CSV display refs and counts; never modify original inputs.

    ``provenance.original_exports`` links the existing five official files and
    ``canonical_run_ref`` links the full original run. They are retained as
    supplied references, not re-encoded, read or authenticated by this formatter.
    Unknown amount is omitted with its own count; it is never treated as zero.
    The owner must verify its calendar/currentness gate before and after calling.
    """
    strategy, selected, original_counts = _selection(core_run)
    threshold = _money(minimum_amount_cny)
    _need(threshold is not None, 'EXPLICIT_DISPLAY_THRESHOLD_REQUIRED')
    displayed, excluded, unknown_amount = [], [], []
    for row in selected:
        amount = _money(row.get('amount_cny'))
        (unknown_amount if amount is None else displayed if amount >= threshold else excluded).append(row)
    shown_ids = {row['instrument_id'] for row in displayed}
    themes, relations, context = _theme_data(theme_context, core_run, selected, shown_ids)
    groups, indexed = [], {}
    for row in displayed:
        iid = row['instrument_id']
        candidates = [themes[r['theme_id']] for r in relations[iid] if r['theme_id'] in themes]
        candidates.extend(theme for theme in themes.values()
                          if theme['theme_type'] == 'INDUSTRY_PROXY' and iid in theme['member_ids']
                          and theme not in candidates)
        theme = candidates[0] if candidates else None
        tid = theme['theme_id'] if theme else None
        if tid not in indexed:
            group = dict(theme_id=tid, name=theme['name'] if theme else '方向未知',
                         basis_kind=('CURRENT_VERIFIED_INDUSTRY_PROXY' if theme['theme_type'] == 'INDUSTRY_PROXY'
                                     else 'CURRENT_VERIFIED_CONCEPT_COHORT') if theme else 'UNKNOWN',
                         source_refs=deepcopy(theme.get('source_refs')) if theme else None,
                         effective_from=deepcopy(theme.get('effective_from')) if theme else None,
                         economic_mainline_membership='UNKNOWN', items=[])
            groups.append(group)
            indexed[tid] = group
        indexed[tid]['items'].append(dict(original_values=deepcopy(row),
                                          native_relations=deepcopy(relations[iid]),
                                          EVENT_confirmation='UNKNOWN'))
    groups = [group for group in groups if group['theme_id'] is not None] + [
        group for group in groups if group['theme_id'] is None]
    union = [item['original_values']['instrument_id'] for group in groups for item in group['items']]
    _need(len(union) == len(set(union)) == len(displayed) and set(union) == shown_ids,
          'EXACT_DISPLAY_UNION_REQUIRED')
    for group in groups:
        group_ids = {item['original_values']['instrument_id'] for item in group['items']}
        _need([item['original_values'] for item in group['items']] ==
              [row for row in displayed if row['instrument_id'] in group_ids],
              'ORIGINAL_WITHIN_GROUP_VALUES_AND_ORDER_REQUIRED')
    generated = datetime.now(timezone.utc)
    if context['context_cutoff'] is not None:
        _need(_clock(context['context_cutoff']) <= generated, 'CONTEXT_CUTOFF_CANNOT_BE_FUTURE')
    beijing = generated.astimezone(timezone(timedelta(hours=8)))
    opened = datetime.fromisoformat(core_run['target_session'] + 'T09:30:00+08:00')
    timing = ('开盘后生成' if beijing >= opened else '开盘前生成') + '，仍用' + core_run['observation_date'] + '收盘基准'
    provenance = deepcopy(provenance or {})
    retained = {key:deepcopy(provenance.get(key)) for key in
                ('canonical_run_ref', 'original_exports', 'core_final_ref',
                 'source_role_summary', 'calendar_currentness',
                 'news_metadata_ref', 'news_summary', 'theme_metadata_ref')}
    exports = retained['original_exports']
    linked_exports = isinstance(exports, dict) and OFFICIAL_EXPORTS <= set(exports)
    counts = dict(original_selected=len(selected), displayed=len(displayed),
                  below_threshold=len(excluded), unknown_amount=len(unknown_amount),
                  FOCUS=original_counts.get('focus'),
                  direction_unknown=sum(len(group['items']) for group in groups
                                        if group['basis_kind'] == 'UNKNOWN'))
    projection = dict(schema_version=VERSION, role='DISPLAY_ONLY_NOT_NEW_SELECTION_OR_SOURCE_AUTHORITY',
        run_id=core_run['run_id'], strategy_id=strategy,
        observation_date=core_run['observation_date'], target_session=core_run['target_session'],
        original_decision_cutoff=deepcopy(core_run.get('decision_cutoff')),
        original_generated_at=deepcopy(core_run.get('generated_at')),
        original_run_content_hash=deepcopy(core_run.get('content_hash', core_run.get('view_content_hash'))),
        actual_generated_at=generated.isoformat(), actual_generated_at_beijing=beijing.isoformat(),
        context_cutoff=context['context_cutoff'], generation_timing_disclosure=timing,
        generated_at_or_after_target_open=beijing >= opened, preopen_publication_proven=False,
        coverage_type=deepcopy(core_run.get('coverage_type')), historical_PIT_claimed=False,
        counts=counts, original_counts=original_counts,
        original_selected_items_hash=_digest(selected),
        original_selected_order_hash=_digest([row['instrument_id'] for row in selected]),
        original_selection_retention='LINKED_CANONICAL_AND_OFFICIAL_EXPORTS' if linked_exports and retained['canonical_run_ref'] else 'UNKNOWN_NOT_AUTHENTICATED_BY_FORMATTER',
        retained_originals=retained, display_filter=dict(comparison='>=',
            minimum_original_amount_cny=str(threshold), strategy_unchanged=True,
            below_threshold_and_unknown_names_omitted=True), groups=groups,
        theme_context=context, native_summary_scope='INTERSECTION_WITH_FULL_ORIGINAL_SELECTION',
        public_native_rows_scope='DISPLAYED_SELECTION_ONLY',
        full_economic_mainline_coverage='UNKNOWN', EVENT_confirmation='UNKNOWN',
        parameter_status=deepcopy(core_run.get('parameter_status')),
        parameter_validation='尚未验证', MARKET_EDGE=deepcopy(core_run.get('market_edge')),
        DAILY_V1=deepcopy(provenance.get('DAILY_V1', 'UNKNOWN')),
        no_news_cooccurrence_direction_inference=True,
        industry_proxy_is_not_refined_economic_topic=True,
        formatter_authenticates_sources=False,
        calls=dict(selector=0, normalizer=0, source_issuer=0, network=0, database=0))
    lines = [core_run['target_session'] + ' 研究观察清单｜观察日 ' + core_run['observation_date'],
             timing + '；实际生成（北京）' + beijing.isoformat(),
             f'原始入选{len(selected)}只；原始成交额 ≥{threshold}元展示{len(displayed)}只；低于门槛{len(excluded)}只、成交额未知{len(unknown_amount)}只仅计数；FOCUS {counts["FOCUS"] if counts["FOCUS"] is not None else "未知"}。',
             '当前行业代理或概念篮子不等于完整经济主线；精细题材缺证据保留未知，不从同篇新闻共现推断方向。',
             '原生阶段、高风险和来源完整性分别报告；以下诊断仅统计与原始入选股相交的候选，0不代表全市场。']
    if context['diagnostic_status'] == 'UNKNOWN':
        lines.append('原生主线确认未知；未取得可用诊断不计为0。')
    else:
        if context['report_banner_required'] is True:
            _need(isinstance(context['report_banner'], str) and context['report_banner'], 'REQUIRED_BACKEND_BANNER')
            lines.append(context['report_banner'])
        for category, count in context['category_counts'].items():
            lines.append(CATEGORY_LABELS[category] + '（与原始入选相交）：' + str(count))
        for summary in context['summaries']:
            lines.append(summary['name'] + '｜阶段 ' + str(summary['stage']) + '｜' +
                         _risk_label(summary['high_risk']) + '｜展示关联' + str(summary['displayed_selected_count']) + '只')
    if context['parent_acquisition'] is not None:
        lines.append('原成员父采集状态保留：' + _json(context['parent_acquisition']) + '；后续计算通过不升级失败采集。')
    if retained['news_summary'] is not None:
        news = retained['news_summary']
        lines.append('独立报道材料：' + str(news.get('status')) + '；解析' +
                     str(news.get('parsed_articles')) + '篇。报道材料不认证经济归因或EVENT，不用于同篇共现分组。')
    for group in groups:
        basis = '当前行业代理' if group['basis_kind'] == 'CURRENT_VERIFIED_INDUSTRY_PROXY' else '当前概念篮子' if group['theme_id'] else '未知'
        lines.extend(['', group['name'] + '｜' + basis + '（' + str(len(group['items'])) + '）'])
        for item in group['items']:
            row = item['original_values']
            model = '；'.join(str(rel['name']) + '：' + str(rel['stage']) + '·' +
                             _risk_label(rel['high_risk']) for rel in item['native_relations'])
            if not model:
                model = ('原生诊断关联未知' if context['diagnostic_status'] == 'UNKNOWN'
                         or context['report_banner_required'] is True else '当前未列入活跃诊断候选')
            repair = ('｜修复未确认' if row.get('repair_status') == 'UNCONFIRMED' else
                      '｜修复 ' + str(row.get('repair_status'))) if row.get('primary_stage') == 'FIRST_PULLBACK' else ''
            lines.append(row['name'] + ' ' + row['instrument_id'] + '｜' +
                         STAGE_LABELS.get(row.get('primary_stage'), str(row.get('primary_stage'))) +
                         '｜成交' + format(_money(row['amount_cny']) / Decimal(100000000), '.2f') +
                         '亿元' + repair + '｜原生关联：' + model)
    lines.extend(['', '原价格/选择资料截止：' + str(core_run.get('decision_cutoff')) +
                  '；当前情境截止：' + str(context['context_cutoff']) + '。',
                  '参数尚未验证；MARKET_EDGE=' + str(projection['MARKET_EDGE']) +
                  '；DAILY_V1=' + str(projection['DAILY_V1']) + '；历史PIT=false。',
                  '来源角色：' + _json(retained['source_role_summary']), 'run_id：' + core_run['run_id']])
    text = '\n'.join(lines) + '\n'
    json_text = _json(projection)
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=('group', 'instrument_id', 'name', 'primary_stage',
        'amount_cny', 'decision_status', 'native_relations_json', 'original_values_json'))
    writer.writeheader()
    for group in groups:
        for item in group['items']:
            row = item['original_values']
            writer.writerow(dict(group=group['name'], instrument_id=row['instrument_id'], name=row['name'],
                primary_stage=row.get('primary_stage'), amount_cny=row.get('amount_cny'),
                decision_status=row.get('decision_status'), native_relations_json=_json(item['native_relations']),
                original_values_json=_json(row, sort_keys=False)))
    csv_text = stream.getvalue()
    forbidden = {str(row[key]) for row in excluded + unknown_amount for key in ('instrument_id', 'name')}
    _need(all(value not in payload for value in forbidden for payload in (text, json_text, csv_text)),
          'BELOW_THRESHOLD_OR_UNKNOWN_AMOUNT_NAMES_MUST_NOT_LEAK')
    output = Path(output_dir).expanduser().absolute()
    output.mkdir(parents=True, exist_ok=False)
    artifacts = {role: _write(output / filename, payload.encode('utf-8')) for role, filename, payload in
                 (('text', 'premarket.txt', text), ('json', 'premarket.json', json_text),
                  ('csv', 'premarket.csv', csv_text))}
    return dict(schema_version=VERSION, status='SAVED_DISPLAY_ONLY_NOT_SOURCE_AUTHORITY',
                run_id=core_run['run_id'], artifacts=artifacts, counts=counts,
                original_selected_items_hash=projection['original_selected_items_hash'],
                original_selected_order_hash=projection['original_selected_order_hash'],
                original_exports=deepcopy(exports), canonical_run_ref=retained['canonical_run_ref'])
