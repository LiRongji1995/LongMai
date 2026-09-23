"""Read-only operational view of existing daily delivery, snapshots and outcomes.

No strategy execution, schedule registration, capture, persistence or price math.
"""
from __future__ import annotations

from datetime import date
import json

from .daily_research_service import PENDING, SNAPSHOTS, SH, _instant
from .daily_research_outcomes import snapshot_available_as_of

VERSION = 'DAILY_OBSERVATION_V1'
DECISIONS = ('FOCUS', 'WATCH', 'UNKNOWN', 'REJECTED')
NO_RECORD = {'NO_REGISTERED_DAILY_PRIMARY_PLAN', 'VERIFIED_TARGET_CALENDAR_UNAVAILABLE'}
COUNT_KEYS = ('signals', 'matured', 'recorded', 'normal', 'pending', 'not_matured',
              'pending_capture', 'missing', 'suspended', 'unavailable', 'calendar_unavailable')


def _tracking(service, snapshot, now):
    run_id = snapshot['identity']['run_id']
    selected = {row['instrument_id'] for row in snapshot['items']
                if row['decision_status'] in {'FOCUS', 'WATCH'}}
    pending = service.store.get_document(PENDING, run_id)
    registered = (pending is not None and pending.get('run_id') == run_id
                  and pending.get('snapshot_hash') == snapshot['snapshot_hash']
                  and _instant(pending.get('created_at')) is not None
                  and _instant(pending['created_at']) <= now)
    enrolled = selected.intersection(pending.get('instrument_ids') or []) if registered else set()
    extra_ids = set(pending.get('instrument_ids') or []) - selected if registered else set()
    original = service.daily_recommendation_outcomes(run_id, as_of=now)
    horizons = {str(h): {key: 0 for key in COUNT_KEYS} for h in range(1, 21)}
    for group in original['summary']['groups']:
        counts = group['counts']
        # A single frozen run/offset has one observation date and target session.
        # Do not treat all missing labels (including future ones) as overdue.
        if counts['matured'] not in (0, counts['signals']):
            raise ValueError('observation monitor requires single-run maturity groups')
        overdue_unrecorded = counts['missing_labels'] if counts['matured'] else 0
        pending_capture = overdue_unrecorded + counts['stale_not_matured_labels']
        status = counts['status_counts']
        values = {'signals': counts['signals'], 'matured': counts['matured'],
                  'recorded': counts['signals'] - counts['missing_labels'],
                  'normal': status['NORMAL'], 'not_matured': counts['not_matured'],
                  'pending_capture': pending_capture,
                  'pending': counts['not_matured'] + pending_capture,
                  'missing': status['MISSING'], 'suspended': status['SUSPENDED'],
                  'unavailable': status['UNAVAILABLE'],
                  'calendar_unavailable': counts['calendar_unavailable']}
        for key, value in values.items():
            horizons[str(group['offset'])][key] += value
    totals = {key: sum(row[key] for row in horizons.values()) for key in COUNT_KEYS}
    return {'status': original['tracking_status'], 'eligible_recommendations': len(selected),
            'enrollment_status': 'SCOPE_MISMATCH' if extra_ids else
                                 'NO_TRACKED_RECOMMENDATIONS' if not selected else
                                 'REGISTERED' if enrolled == selected else
                                 'PARTIAL' if enrolled else 'NOT_AVAILABLE',
            'registered_recommendations': len(enrolled),
            'horizons': horizons, 'totals': totals,
            'sample_unit': 'RECOMMENDATION_X_TRADING_SESSION',
            'maturity_policy': original['summary']['maturity_policy'],
            'pending_policy': 'NOT_MATURED_OR_DUE_AWAITING_CAPTURE',
            'missing_policy': 'EXPLICIT_MISSING_STATUS_ONLY'}


def _day(service, target, now):
    # This remains the sole authority for READY and failure/plan semantics.
    delivery = service.daily_research_delivery(str(target), as_of=now)
    state = delivery['execution_status']
    status = 'NOT_AVAILABLE' if delivery.get('reason') in NO_RECORD else state
    run_id = delivery.get('run_id')
    snapshot = service.store.get_document(SNAPSHOTS, run_id) if run_id else None
    snapshot_status = 'NOT_AVAILABLE'
    counts = {state: None for state in DECISIONS}
    recommendations = {'FOCUS': [], 'WATCH': []}
    other_reasons = {}
    tracking = {'status': 'NOT_AVAILABLE', 'reason': 'NO_SAVED_SNAPSHOT',
                'eligible_recommendations': None, 'registered_recommendations': None,
                'enrollment_status': 'NOT_AVAILABLE', 'horizons': None, 'totals': None}
    if snapshot is not None:
        if snapshot['identity']['run_id'] != run_id:
            raise ValueError('observation snapshot run mismatch')
        publication = service._daily_publication_time(snapshot)
        if not snapshot_available_as_of(snapshot, now) or (publication and publication > now):
            snapshot_status = 'NOT_AVAILABLE_AT_CUTOFF'
        else:
            # Only an actually persisted snapshot can supply original explanations.
            snapshot_status = 'SAVED'
            counts = {decision: sum(row['decision_status'] == decision for row in snapshot['items'])
                      for decision in DECISIONS}
            for row in snapshot['items']:
                decision = row['decision_status']
                if decision not in DECISIONS:
                    raise ValueError('unknown frozen decision')
                if decision in recommendations:
                    recommendations[decision].append({key: row.get(key) for key in
                        ('instrument_id', 'primary_setup', 'reason_summary')})
                else:
                    reason = (decision, row.get('reason_summary'))
                    other_reasons[reason] = other_reasons.get(reason, 0) + 1
            tracking = _tracking(service, snapshot, now)
    if status == 'NOT_AVAILABLE':
        activity = 'NOT_AVAILABLE'
    elif state in {'RUNNING', 'QUEUED'}:
        activity = state
    elif delivery.get('attempts') or state in {'READY', 'FAILED'}:
        activity = 'ATTEMPT_RECORDED'
    elif state in {'PLANNED', 'MISSED'}:
        activity = 'NOT_STARTED'
    else:
        activity = 'NOT_AVAILABLE'
    empty = snapshot_status == 'SAVED' and counts['FOCUS'] + counts['WATCH'] == 0
    message = ('正常交付：冻结结果没有 FOCUS/WATCH 推荐。' if state == 'READY' and empty else
               '已交付冻结推荐。' if state == 'READY' and snapshot_status == 'SAVED' else
               '有交付记录，但原持久化 snapshot 不可用；不能视为合法空结果。' if run_id else
               '尚无可发布 run：' + str(delivery.get('reason') or state))
    return {**{key: delivery.get(key) for key in
               ('target_session', 'observation_date', 'run_id', 'strategy_version', 'config_version',
                'data_cutoff', 'execution_status', 'reason', 'blockers', 'missing_evidence', 'error',
                'stage', 'job_id', 'timestamp', 'latest_attempt_status', 'previous_attempt_status',
                'previous_blockers', 'delivery_deadline')},
            'status': status, 'daily_primary_activity': activity,
            'no_run_reason': (delivery.get('reason') or state) if not run_id else None,
            'message': message, 'snapshot_status': snapshot_status,
            'snapshot_hash': snapshot.get('snapshot_hash') if snapshot_status == 'SAVED' else None,
            'counts': counts, 'recommendations': recommendations,
            'non_recommendation_reasons': [{'decision_status': key[0], 'reason_summary': key[1], 'count': count}
                                           for key, count in other_reasons.items()],
            'outcome': tracking}


def observation_report(service, *, target_session=None, days=20, as_of=None):
    """Bounded projection; every day uses the same visibility cutoff."""
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 20:
        raise ValueError('observation days must be 1..20')
    now = _instant(as_of) or service._daily_research_now()
    if now > service._daily_research_now():
        raise ValueError('observation cutoff cannot be in the future')
    sessions = service._daily_research_sessions()
    requested = date.fromisoformat(str(target_session)) if target_session else now.astimezone(SH).date()
    within = bool(sessions and sessions[0] <= requested <= sessions[-1])
    eligible = [day for day in sessions if day <= requested] if within else []
    target = requested if target_session or not eligible else eligible[-1]
    dates = list(reversed([day for day in eligible if day <= target][-days:])) if target in sessions else []
    current = _day(service, target, now)
    details = [current] + [_day(service, day, now) for day in dates if day != target] if dates else []
    history = [{key: row[key] for key in ('target_session', 'observation_date', 'status',
                'execution_status', 'run_id', 'reason', 'snapshot_status', 'counts')} |
               {'outcome': row['outcome']} for row in details]
    result = {'schema_version': VERSION, 'report_as_of': now.isoformat(), 'read_only': True,
              'target_session': str(target), 'requested_days': days, 'available_days': len(dates),
              'calendar_status': 'AVAILABLE' if len(dates) == days else
                                 'PARTIAL' if dates else 'NOT_AVAILABLE',
              'history_order': 'TARGET_SESSION_DESCENDING', 'current': current, 'history': history}
    result['markdown'] = render_observation(result)
    return result


def _text(value):
    if value is None:
        return 'NOT_AVAILABLE'
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return str(value).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')


def render_observation(report):
    current = report['current']
    lines = ['# Daily Observation Monitor', '',
             '【事实】只读取原交付、冻结 snapshot 和已有后验；不评价收益。', '',
             '| 字段 | 状态 |', '| --- | --- |']
    for key in ('target_session', 'observation_date', 'status', 'execution_status', 'daily_primary_activity',
                'run_id', 'strategy_version', 'config_version', 'data_cutoff', 'snapshot_status',
                'no_run_reason', 'reason', 'blockers', 'missing_evidence', 'error', 'stage',
                'latest_attempt_status', 'previous_attempt_status', 'previous_blockers'):
        lines.append(f'| {key} | {_text(current.get(key))} |')
    lines += ['', current['message'], '', '## 冻结推荐', '']
    for decision in DECISIONS:
        lines += [f"{decision}：{_text(current['counts'][decision])}", '']
        for item in current['recommendations'].get(decision, []):
            lines.append('- ' + '；'.join(_text(item[key]) for key in
                         ('instrument_id', 'primary_setup', 'reason_summary')))
        if current['recommendations'].get(decision):
            lines.append('')
    for reason in current['non_recommendation_reasons']:
        lines.append(f"- {reason['decision_status']} × {reason['count']}：{_text(reason['reason_summary'])}")
    lines.append('')
    outcome = current['outcome']
    lines += ['## Outcome 状态', '', f"跟踪：{outcome['status']}；登记：{outcome['enrollment_status']}；"
              f"推荐数：{_text(outcome['eligible_recommendations'])}；已登记：{_text(outcome['registered_recommendations'])}。", '',
              '已成熟仅指交易日收盘已到；recorded 可以包含未成熟或异常标签。',
              'pending = 未到期 + 已到期等待采集；missing 只计显式 MISSING，停牌和不可用另列。', '',
              '日历未知可能与 UNAVAILABLE 标签重叠；展示列不是互斥分区，不能全部相加。', '',
              '| 期限 | 推荐数 | 已成熟 | 已记录 | NORMAL | pending | missing | suspended | unavailable | 日历未知 |',
              '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for horizon in (1, 5, 20):
        counts = (outcome['horizons'] or {}).get(str(horizon), {})
        lines.append(f'| T+{horizon} | ' + ' | '.join(_text(counts.get(key)) for key in
                     ('signals', 'matured', 'recorded', 'normal', 'pending', 'missing', 'suspended',
                      'unavailable', 'calendar_unavailable')) + ' |')
    lines += ['', '全部 T+1..T+20 汇总（单位：推荐 × 交易日）：' + _text(outcome['totals']), '',
              '## 最近交易日', '', f"覆盖 {report['available_days']}/{report['requested_days']} 个交易日；"
              f"日历状态：{report['calendar_status']}；查询截止：{report['report_as_of']}。", '',
              '| target_session | 状态 | FOCUS | WATCH | Snapshot | Outcome | 原因 |',
              '| --- | --- | --- | --- | --- | --- | --- |']
    for row in report['history']:
        lines.append('| ' + ' | '.join(_text(value) for value in
                     (row['target_session'], row['status'], row['counts']['FOCUS'], row['counts']['WATCH'],
                      row['snapshot_status'], row['outcome']['status'], row['reason'])) + ' |')
    lines += ['', '【需要验证】持续运行与数据覆盖仍须逐日观察；无记录不是合法空推荐，也不自动算漏跑。', '']
    return '\n'.join(lines)
