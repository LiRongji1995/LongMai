"""Independent, serial R05 artifact audit using only the standard library.

This script does not import the application, its evaluators, or its statistics.
Run only after the replay process has finished. Each invocation requires a new
evidence directory; failed audits and their command records are retained.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timezone
from decimal import Decimal, localcontext
import gc
import gzip
import hashlib
import json
from pathlib import Path
import sys
import traceback
from zoneinfo import ZoneInfo

PV = 'PV_OBSERVATION_V1'
FOCUS = 'PV_FOCUS_PREVIEW_V0.1'
BASELINE = 'ELIGIBLE_UNIVERSE_EQUAL_WEIGHT_PRICE_DIAGNOSTIC'
AUDITED_STRATEGIES = (PV, FOCUS, BASELINE)
METRIC_POLICY = 'R05_COMPARABLE_PRICE_AND_LIMIT_EVENT_LABEL_V1'
LOGICAL_FIELDS = ('run_id', 'strategy_id', 'instrument_id', 'observation_date', 'horizon',
                  'spec_hash', 'config_hash', 'parameter_version', 'implementation_version',
                  'implementation_source_hash', 'metric_policy_id', 'label_kind')
SH = ZoneInfo('Asia/Shanghai')


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('Timestamp lacks an explicit timezone')
    return result


def digest(value):
    result = hashlib.sha256()
    for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).iterencode(value):
        result.update(chunk.encode('utf-8'))
    return result.hexdigest()


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def close_complete(day, cutoff):
    return day is not None and datetime.combine(datetime.fromisoformat(day).date(), time(15), SH) <= cutoff


def endpoint_return(reference, endpoint):
    with localcontext() as context:
        context.prec = 28
        return Decimal(str(endpoint)) / Decimal(str(reference)) - 1


@dataclass(frozen=True, slots=True)
class Point:
    run_id: str
    strategy: str
    instrument: str
    day: str
    horizon: int
    value: Decimal | None
    status: str
    focus: bool


def arithmetic(points, dates):
    valid = sorted(point.value for point in points if point.value is not None)
    by_day = defaultdict(list)
    for point in points:
        if point.value is not None:
            by_day[point.day].append(point.value)
    n = len(valid)
    middle = None if not n else valid[n // 2] if n % 2 else (valid[n // 2 - 1] + valid[n // 2]) / 2
    means = [sum(values) / len(values) for values in by_day.values()]
    up_rates = [Decimal(sum(value > 0 for value in values)) / len(values) for values in by_day.values()]
    pending = sum(point.status == 'NOT_MATURED' for point in points)
    return {'signals': len(points), 'unique_instruments': len({point.instrument for point in points}),
            'signal_dates': len({point.day for point in points}), 'expected_observation_dates': len(dates),
            'mature_valid': n, 'not_matured': pending, 'missing_or_incomparable': len(points) - n - pending,
            'positive': sum(value > 0 for value in valid), 'zero': sum(value == 0 for value in valid),
            'negative': sum(value < 0 for value in valid),
            'up_rate': Decimal(sum(value > 0 for value in valid)) / n if n else None,
            'mean_return': sum(valid) / n if n else None, 'median_return': middle,
            'p10_return': valid[int((n - 1) * .1)] if n else None,
            'date_equal_up_rate': sum(up_rates) / len(up_rates) if up_rates else None,
            'date_equal_mean_return': sum(means) / len(means) if means else None}


class Audit:
    def __init__(self, replay, output, scope):
        self.replay, self.output, self.scope = replay.resolve(), output.resolve(), scope
        self.checks = Counter()
        self.failed = 0
        self.files = {}
        self.failure_log = (self.output / 'failures.jsonl').open('x', encoding='utf-8')

    def check(self, condition, code, **detail):
        self.checks[code] += 1
        if not condition:
            self.failed += 1
            self.failure_log.write(json.dumps({'check': code, **detail}, ensure_ascii=False, default=str) + '\n')

    def registered(self, path):
        path = Path(path)
        key = str(path.resolve())
        if key not in self.files:
            self.files[key] = file_hash(path)
        return path

    def read(self, path):
        with self.registered(path).open(encoding='utf-8-sig') as source:
            return json.load(source)

    def lines(self, path):
        path = self.registered(path)
        opener = gzip.open if path.suffix == '.gz' else open
        with opener(path, 'rt', encoding='utf-8-sig') as source:
            for line in source:
                if line.strip():
                    yield json.loads(line)

    def equal(self, actual, expected, code, **detail):
        if isinstance(expected, Decimal):
            condition = actual is not None and abs(Decimal(str(actual)) - expected) <= Decimal('1e-12')
        else:
            condition = actual == expected
        self.check(condition, code, actual=actual, expected=expected, **detail)

    def visible_bar_index(self, bundle, cutoff):
        """Independently check immutable receipt clocks/scope before price use."""
        receipts = {}
        conflicts = set()
        for receipt in bundle['receipts']:
            key = receipt['receipt_id']
            if key in receipts and receipts[key] != receipt:
                conflicts.add(key)
            receipts[key] = receipt
        selected, conflicting_bars = {}, set()
        calendar = set(bundle['sessions'])
        for bar in bundle['bars']:
            day, iid = bar['session'], bar['instrument_id']
            visible = (bar.get('quality') != 'INVALID' and day in calendar and
                       day <= cutoff.astimezone(SH).date().isoformat() and bool(bar.get('source_refs')))
            clocks = [stamp(bar[name]) for name in ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')]
            visible &= clocks[0] <= cutoff and clocks[1] <= cutoff and clocks[2] <= clocks[3]
            visible &= min(value.astimezone(SH).date().isoformat() for value in clocks[:2]) >= day
            for ref in bar.get('source_refs', []):
                receipt = receipts.get(ref)
                if receipt is None or ref in conflicts:
                    visible = False
                    continue
                rc = [stamp(receipt[name]) for name in ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')]
                visible &= receipt['status'] in ('COMPLETE', 'PARTIAL') and max(rc[:2]) <= cutoff and rc[2] <= rc[3]
                visible &= not receipt.get('instrument_ids') or iid in receipt['instrument_ids']
                visible &= not receipt.get('business_sessions') or day in receipt['business_sessions']
                visible &= clocks[2] >= rc[2] and clocks[3] >= rc[3]
                if max(rc) > cutoff:
                    proof = receipt.get('historical_time_proof') or {}
                    visible &= (bundle['coverage_type'] == 'RECONSTRUCTED_HISTORICAL' and
                                proof.get('applies_from', '9999') <= day <= proof.get('applies_to', '') and
                                len(proof.get('evidence_hash', '')) == 64 and bool(proof.get('evidence_refs')))
            if max(clocks) > cutoff:
                visible &= bundle['coverage_type'] == 'RECONSTRUCTED_HISTORICAL'
                # All late parents were checked above; an already captured parent
                # can support later local normalization without inventing a receipt.
            if not visible:
                continue
            key = (iid, day)
            priority = tuple(clocks[index] for index in (1, 3, 2))
            old = selected.get(key)
            if old is None or priority > old[0]:
                selected[key] = (priority, bar)
                conflicting_bars.discard(key)
            elif priority == old[0] and old[1] != bar:
                conflicting_bars.add(key)
        self.checks['visible_price_rows'] = len(selected) - len(conflicting_bars)
        self.checks['conflicting_price_keys_excluded'] = len(conflicting_bars)
        return {key: value[1] for key, value in selected.items() if key not in conflicting_bars}

    def run(self):
        plan = self.read(self.replay / 'replay_plan.json')
        reference = self.read(self.replay / 'outcome_input_reference.json')
        outcome_plan = self.read(self.replay / 'outcome_execution_plan.json')
        self.equal(reference['outcome_execution_plan_hash'], digest(outcome_plan), 'OUTCOME_PLAN_BINDING')
        self.equal(outcome_plan['parent_signal_plan_hash'], digest(plan), 'SIGNAL_PLAN_BINDING')
        self.equal(reference['actual_execution_source_manifest'], outcome_plan['source_tree_manifest'], 'OUTCOME_INPUT_SOURCE_MANIFEST_BINDING')
        source_root = Path(__file__).resolve().parents[1]
        for relative, expected_hash in outcome_plan['source_tree_manifest'].items():
            path = self.registered(source_root / relative)
            self.equal(self.files[str(path.resolve())], expected_hash, 'FROZEN_EXECUTION_SOURCE_FILE', relative_path=relative)
        cutoff = stamp(plan['execution_cutoff'])
        input_path = self.registered(reference['path'])
        self.equal(self.files[str(input_path.resolve())], reference['sha256'], 'CANONICAL_GZIP_FILE_HASH')
        with gzip.open(input_path, 'rt', encoding='utf-8') as source:
            bundle = json.load(source)
        self.equal(digest(bundle), reference['canonical_input_hash'], 'CANONICAL_INPUT_CONTENT_HASH')
        calendar = bundle['sessions']
        self.equal(calendar, sorted(set(calendar)), 'UNIQUE_ORDERED_EXCHANGE_CALENDAR')
        bars = self.visible_bar_index(bundle, cutoff)
        del bundle
        gc.collect()
        primary = plan['primary_sessions']
        dates = plan['attempt_sessions'] if self.scope == 'all20' else primary
        horizons = plan['horizons']
        self.equal(horizons, [1, 2, 3, 4, 5, 10, 20], 'FROZEN_HORIZONS')
        ranks, episodes, signal_episodes, run_dates = {}, {}, {}, {}
        for day in plan['attempt_sessions']:
            run = self.read(self.replay / day / 'rank.json')
            attempt = self.read(self.replay / day / 'attempt.json')
            self.equal(self.files[str((self.replay / day / 'rank.json').resolve())], attempt['rank_file_sha256'], 'FROZEN_RANK_FILE_HASH', day=day)
            self.equal(run['replay_plan_hash'], digest(plan), 'RANK_PLAN_BINDING', day=day)
            run_dates[run['run_id']] = day
            focus = set(run['selection_results'][FOCUS]['selected_ids'])
            items = {PV: {row['instrument_id']: row for row in run['results'][PV]['items'] if row.get('rank') is not None},
                     BASELINE: {row['instrument_id']: row for row in run['evaluation_baselines'][BASELINE]['items'] if row.get('rank') is not None}}
            self.check(focus <= set(items[PV]), 'FOCUS_IS_SAVED_PV_SUBSET', day=day)
            if day in dates:
                identity = {'spec_hash': run.get('spec_hash') or run.get('strategy_spec_hash'),
                            'config_hash': run.get('config_hash'),
                            'parameter_version': run.get('parameter_version') or run.get('config_hash') or run.get('spec_hash') or run.get('strategy_spec_hash'),
                            'implementation_version': run.get('implementation_version'),
                            'implementation_source_hash': run.get('source_hash')}
                ranks[day] = (run['run_id'], items, focus, identity)
            track = self.read(self.replay / day / 'tracking.json')
            for episode in track.get('episodes', []):
                eid = episode['episode_id']
                if eid in episodes:
                    self.equal((episode['observation_date'], episode['run_id']),
                               (episodes[eid]['observation_date'], episodes[eid]['run_id']), 'IMMUTABLE_EPISODE_TRUE_START', episode=eid)
                episodes[eid] = {key: episode.get(key) for key in ('observation_date', 'run_id', 'instrument_id')}
            for signal in track.get('signals', []):
                signal_episodes[(signal['run_id'], signal['instrument_id'])] = signal['episode_id']
            del track, run

        revisions = {}
        for day in dates:
            run_id, items, focus, identity = ranks[day]
            index = calendar.index(day)
            seen, seen_revisions = set(), set()
            for label in self.lines(self.replay / day / 'canonical_outcomes.jsonl.gz'):
                sid, iid, horizon = label['strategy_id'], label['instrument_id'], label['horizon']
                if sid not in items:
                    continue
                detail = {'day': day, 'strategy': sid, 'instrument': iid, 'horizon': horizon}
                self.check(iid in items[sid] and horizon in horizons, 'LABEL_BELONGS_TO_SAVED_RANK', **detail)
                if iid not in items[sid] or horizon not in horizons:
                    continue
                self.check(label['observation_id'] not in seen_revisions, 'CANONICAL_REVISION_NOT_DUPLICATED', **detail)
                seen_revisions.add(label['observation_id'])
                saved = items[sid][iid]
                exact = (saved.get('exact_prices') or {}).get('comparable_close')
                if sid == PV:
                    self.check(exact is not None, 'PV_FROZEN_EXACT_REFERENCE_PRESENT', **detail)
                reference_close = exact if exact is not None else saved.get('comparable_close')
                target = calendar[index + horizon] if index + horizon < len(calendar) else None
                mature = close_complete(target, cutoff)
                endpoint = bars.get((iid, target))
                basis = saved.get('comparable_basis')
                valid = (mature and reference_close is not None and Decimal(str(reference_close)) > 0 and
                         endpoint is not None and endpoint.get('comparable_close') is not None and
                         bool(basis) and endpoint.get('comparable_basis') == basis)
                value = endpoint_return(reference_close, endpoint['comparable_close']) if valid else None
                status = 'MATURE_VALID' if valid else 'UNAVAILABLE' if mature else 'NOT_MATURED'
                direction = None if value is None else 'UP' if value > 0 else 'DOWN' if value < 0 else 'FLAT'
                self.equal(label['label_end'], target, 'PRICE_TARGET_T_PLUS_H', **detail)
                self.equal(label['price_label_status'], status, 'ENDPOINT_MATURITY_AND_MISSINGNESS', **detail)
                self.equal(label['metric_states']['close_reference_change']['status'], status, 'INDEPENDENT_PRICE_METRIC_STATE', **detail)
                self.equal(label['metrics']['close_reference_change'], float(value) if value is not None else None, 'PRICE_METRIC_NUMERIC_VALUE', **detail)
                self.check((label['return_exact'] is None and value is None) or
                           (label['return_exact'] is not None and value is not None and Decimal(label['return_exact']) == value),
                           'EXACT_DECIMAL_RETURN', actual=label['return_exact'], expected=value, **detail)
                self.equal(label['price_direction'], direction, 'EXACT_PRICE_DIRECTION', **detail)
                self.equal(label['selected_focus'], iid in focus, 'FROZEN_FOCUS_MEMBERSHIP', **detail)
                self.equal(label['run_id'], run_id, 'LABEL_RUN_IDENTITY', **detail)
                self.equal(label['observation_date'], day, 'LABEL_SIGNAL_DATE_IDENTITY', **detail)
                self.equal(label['label_kind'], 'SIGNAL', 'CANONICAL_SIGNAL_LABEL_KIND', **detail)
                for field, expected_identity in identity.items():
                    self.equal(label.get(field), expected_identity, 'LABEL_RANK_IDENTITY_' + field.upper(), **detail)
                self.equal(label['input_hash'], reference['canonical_input_hash'], 'LABEL_OUTCOME_INPUT_BINDING', **detail)
                self.equal(label['metric_policy_id'], METRIC_POLICY, 'EXPLICIT_METRIC_POLICY', **detail)
                logical = 'strategy_label_' + digest({key: label.get(key) for key in LOGICAL_FIELDS})
                self.equal(label['logical_label_id'], logical, 'LOGICAL_KEY_INCLUDES_RUN_AND_POLICY', **detail)
                self.equal(label['observation_id'], 'strategy_outcome_' + digest({key: val for key, val in label.items() if key != 'observation_id'}), 'FULL_REVISION_CONTENT_HASH', **detail)
                point = Point(run_id, sid, iid, day, horizon, value, status, iid in focus)
                compact_hash = digest({key: val for key, val in label.items() if key != 'session_states'})
                candidate = (value is not None, stamp(label['evaluated_at']), label['observation_id'], point, compact_hash)
                if candidate[1] <= cutoff and (logical not in revisions or candidate[:2] >= revisions[logical][:2]):
                    revisions[logical] = candidate
                seen.add((sid, iid, horizon))
            expected = {(sid, iid, h) for sid, rows in items.items() for iid in rows for h in horizons}
            self.equal(len(seen), len(expected), 'ALL_SAVED_RANK_HORIZONS_HAVE_LABELS', day=day)
            self.check(seen == expected, 'EXACT_LABEL_COHORT_MEMBERSHIP', day=day,
                       missing=len(expected - seen), extra=len(seen - expected))
            self.paper_day(day, run_id, items, focus, calendar, cutoff, bars, horizons)
            print(json.dumps({'audited_signal_day': day, 'expected_labels': len(expected)}), flush=True)

        selected_points, selected_ids, selected_revisions, all_ids = [], set(), {}, set()
        for label in self.lines(self.replay / 'outcomes.jsonl'):
            logical = label['logical_label_id']
            self.check(logical not in all_ids, 'FINAL_LOGICAL_LABEL_NOT_DOUBLE_COUNTED', logical_label_id=logical)
            all_ids.add(logical)
            selected_revisions[logical] = label['observation_id']
            if logical not in revisions:
                continue
            expected = revisions[logical]
            self.equal(label['observation_id'], expected[2], 'LATEST_VALID_REVISION_SELECTED', logical_label_id=logical)
            self.equal(digest(label), expected[4], 'SELECTED_PAYLOAD_MATCHES_FULL_CANONICAL_REVISION', logical_label_id=logical)
            selected_ids.add(logical)
            selected_points.append(expected[3])
        self.equal(selected_ids, set(revisions), 'ALL_AUDITED_LOGICAL_LABELS_SELECTED')
        selection_ids = set()
        for row in self.lines(self.replay / 'revision_selection.jsonl'):
            logical = row['logical_label_id']
            self.check(logical not in selection_ids, 'REVISION_SELECTION_ONE_ROW_PER_LOGICAL_LABEL', logical_label_id=logical)
            selection_ids.add(logical)
            self.equal(row['policy'], 'LATEST_VALID_AS_OF_COMMON_CUTOFF_ONE_PER_LOGICAL_LABEL', 'EXPLICIT_REVISION_SELECTION_POLICY', logical_label_id=logical)
            self.equal(row['observation_id'], selected_revisions.get(logical), 'REVISION_SELECTION_MATCHES_FINAL_ARTIFACT', logical_label_id=logical)
        self.equal(selection_ids, all_ids, 'REVISION_SELECTION_COMPLETE')
        windows = [('PRIMARY_5', primary, self.read(self.replay / 'statistics_primary_5_sessions.json'))]
        if self.scope == 'all20':
            windows.append(('ATTEMPT_20', dates, self.read(self.replay / 'statistics_attempt_20_sessions.json')))
        for day_result in self.read(self.replay / 'statistics_by_date.json'):
            if day_result['observation_date'] in dates:
                windows.append((day_result['observation_date'], [day_result['observation_date']], day_result['statistics']))
        independently_computed = []
        for name, window_days, table in windows:
            independently_computed.extend(self.aggregate_window(name, window_days, table, selected_points, episodes, signal_episodes, horizons))
        self.paper_summary(dates, primary, horizons)
        (self.output / 'independent_aggregate_checks.json').write_text(json.dumps(independently_computed, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
        return {'scope': self.scope, 'audited_sessions': dates, 'audited_strategies': list(AUDITED_STRATEGIES),
                'horizons': horizons, 'canonical_logical_labels_checked': len(revisions),
                'all_final_logical_labels_checked_for_duplicates': len(all_ids),
                'numeric_policy': 'DECIMAL_28_DIGIT_DIVISION_AND_SUBTRACTION; DIRECTION_BEFORE_DISPLAY_ROUNDING',
                'aggregate_numeric_comparison_absolute_tolerance': '1e-12',
                'formal_episode_start_source': 'IMMUTABLE_TRACKING_EPISODE_OBSERVATION_DATE_AND_ORIGINAL_RUN_ID',
                'scope_limit': 'Price endpoints, aggregation, paper calendar/unknown-limit/closed-return invariants; not proof of real executable fills.'}

    def paper_day(self, day, run_id, items, focus, calendar, cutoff, bars, horizons):
        seen = set()
        index = calendar.index(day)
        target_sets = {PV: set(items[PV]), FOCUS: set(focus), BASELINE: set(items[BASELINE])}
        for trade in self.lines(self.replay / day / 'paper_trades.jsonl'):
            sid, iid, h = trade['strategy_id'], trade['instrument_id'], trade['holding_horizon']
            if sid not in target_sets:
                continue
            key = (sid, iid, h)
            detail = {'day': day, 'strategy': sid, 'instrument': iid, 'horizon': h}
            self.check(key not in seen, 'PAPER_TRADE_NOT_DUPLICATED', **detail)
            seen.add(key)
            self.check(iid in target_sets[sid] and h in horizons, 'PAPER_COHORT_MATCHES_SAVED_SELECTION', **detail)
            entry = calendar[index + 1] if index + 1 < len(calendar) else None
            price_target = calendar[index + h] if index + h < len(calendar) else None
            paper_target = calendar[index + 1 + h] if index + 1 + h < len(calendar) else None
            self.equal(trade['run_id'], run_id, 'PAPER_RUN_IDENTITY', **detail)
            self.equal(trade['entry_attempt_date'], entry, 'PAPER_ENTRY_E_IS_T_PLUS_ONE', **detail)
            self.equal(trade['signal_price_target_date'], price_target, 'PAPER_DISCLOSES_SIGNAL_PRICE_T_PLUS_H', **detail)
            self.equal(trade['planned_exit_date'], paper_target, 'PAPER_EXIT_TARGET_IS_E_PLUS_H', **detail)
            self.equal(trade['attempted'], close_complete(entry, cutoff), 'MATURE_ENTRY_REMAINS_AN_ATTEMPT', **detail)
            entry_bar = bars.get((iid, entry))
            limits_unknown = (entry_bar is None or not entry_bar.get('limit_verified') or
                              entry_bar.get('limit_up') is None or entry_bar.get('limit_down') is None or
                              entry_bar.get('limit_applicable') is not True)
            if trade['attempted'] and limits_unknown:
                self.check(not trade['closed'] and not trade['entry_confirmed'], 'UNKNOWN_ENTRY_LIMIT_NEVER_BECOMES_CLOSED_FILL', **detail)
                self.check(trade['status'] in ('FILL_UNVERIFIED', 'NO_FILL'), 'UNKNOWN_ENTRY_LIMIT_RETAINS_EXPLICIT_STATUS', **detail)
            if trade['closed']:
                exit_bar = bars.get((iid, trade['exit_date']))
                self.check(close_complete(paper_target, cutoff) and trade['exit_date'] >= paper_target,
                           'CLOSED_TRADE_RESPECTS_HOLDING_WINDOW', **detail)
                self.check(exit_bar is not None and exit_bar.get('limit_verified') and exit_bar.get('limit_down') is not None,
                           'CLOSED_TRADE_HAS_VERIFIED_EXIT_LIMIT', **detail)
                if entry_bar and exit_bar:
                    ratio = Decimal(exit_bar['comparable_close']) / Decimal(entry_bar['comparable_open'])
                    self.check(Decimal(trade['gross_return']) == ratio - 1, 'CLOSED_GROSS_RETURN_FROM_INPUT_PRICES', **detail)
                    for bps in (0, 30, 60):
                        cost = Decimal(bps) / 20000
                        self.check(Decimal(trade['net_return_by_cost_bps'][str(bps)]) == ratio * (1 - cost) / (1 + cost) - 1,
                                   'CLOSED_COST_SCENARIO_FROM_INPUT_PRICES', bps=bps, **detail)
                    if trade.get('mark_session'):
                        self.check(trade['mark_session'] <= trade['exit_date'], 'CLOSED_MARK_STOPS_AT_EXIT', **detail)
        expected = {(sid, iid, h) for sid, ids in target_sets.items() for iid in ids for h in horizons}
        self.check(seen == expected, 'PAPER_ALL_SAVED_SIGNAL_ATTEMPTS_RETAINED', day=day, missing=len(expected - seen), extra=len(seen - expected))

    def aggregate_window(self, name, dates, table, points, episodes, signal_episodes, horizons):
        lookup = {(row['strategy_id'], row['horizon']): row for row in table}
        computed = []
        for sid in AUDITED_STRATEGIES:
            for h in horizons:
                selected = [point for point in points if point.day in dates and point.horizon == h and
                            (point.strategy == sid or sid == FOCUS and point.strategy == PV and point.focus)]
                expected = arithmetic(selected, dates)
                saved = lookup[(sid, h)]
                for field, value in expected.items():
                    self.equal(saved['pooled_signals'].get(field), value, 'INDEPENDENT_AGGREGATE_' + field.upper(), window=name, strategy=sid, horizon=h)
                touched, window_first, true_first, unlinked = set(), {}, [], 0
                for point in sorted(selected, key=lambda item: (item.day, item.run_id)):
                    eid = signal_episodes.get((point.run_id, point.instrument)) if sid in (PV, FOCUS) else None
                    if eid is None:
                        unlinked += 1
                        continue
                    touched.add(eid)
                    window_first.setdefault(eid, point)
                    episode = episodes[eid]
                    if point.day == episode['observation_date'] and point.run_id == episode['run_id']:
                        true_first.append(point)
                before = sum(episodes[eid]['observation_date'] < min(dates) for eid in touched) if dates else 0
                started = sum(episodes[eid]['observation_date'] in dates for eid in touched)
                for field, value in {'episode_count': len(touched), 'signals_without_episode': unlinked,
                                     'continuing_episode_count': before, 'started_before_window_episode_count': before,
                                     'started_in_window_episode_count': started}.items():
                    self.equal(saved[field], value, 'INDEPENDENT_EPISODE_' + field.upper(), window=name, strategy=sid, horizon=h)
                for field, values in (('episode_first_signal', true_first), ('episode_window_first_signal', list(window_first.values()))):
                    result = arithmetic(values, dates)
                    for key, value in result.items():
                        self.equal(saved[field].get(key), value, 'INDEPENDENT_' + field.upper() + '_' + key.upper(), window=name, strategy=sid, horizon=h)
                computed.append({'window': name, 'strategy_id': sid, 'horizon': h, 'independent_pooled': expected,
                                 'true_episode_first_count': len(true_first), 'window_episode_first_count': len(window_first),
                                 'continuing_episode_count': before})
        return computed

    def paper_summary(self, dates, primary, horizons):
        groups = defaultdict(lambda: {'total_signals': 0, 'attempted': 0, 'evaluable': 0, 'closed': 0, 'states': Counter(), 'costs': defaultdict(list)})
        for day in dates:
            for trade in self.lines(self.replay / day / 'paper_trades.jsonl'):
                if trade['strategy_id'] not in AUDITED_STRATEGIES:
                    continue
                for window in ('PRIMARY_5', 'ATTEMPT_20'):
                    if window == 'PRIMARY_5' and day not in primary or window == 'ATTEMPT_20' and self.scope != 'all20':
                        continue
                    group = groups[(window, trade['strategy_id'], trade['holding_horizon'])]
                    group['total_signals'] += 1
                    for field, trade_field in (('attempted', 'attempted'), ('evaluable', 'entry_confirmed'), ('closed', 'closed')):
                        group[field] += bool(trade[trade_field])
                    group['states'][trade['status']] += 1
                    if trade['closed']:
                        for cost, value in trade['net_return_by_cost_bps'].items():
                            group['costs'][cost].append(Decimal(value))
        for saved in self.read(self.replay / 'paper_execution_summary.json'):
            key = (saved['window'], saved['strategy_id'], saved['horizon'])
            if saved['strategy_id'] not in AUDITED_STRATEGIES or saved['window'] == 'ATTEMPT_20' and self.scope != 'all20':
                continue
            group = groups[key]
            for field in ('total_signals', 'attempted', 'evaluable', 'closed', 'states'):
                self.equal(saved[field], dict(group[field]) if field == 'states' else group[field], 'PAPER_AGGREGATE_' + field.upper(), group=key)
            for cost in ('0', '30', '60'):
                values = group['costs'][cost]
                result = saved['cost_scenarios'][cost]
                self.equal(result['denominator_closed'], len(values), 'PAPER_WIN_DENOMINATOR_CLOSED_ONLY', group=key, bps=cost)
                self.equal(result['win_rate'], Decimal(sum(value > 0 for value in values)) / len(values) if values else None, 'PAPER_WIN_RATE', group=key, bps=cost)
                self.equal(result['mean_net_return'], sum(values) / len(values) if values else None, 'PAPER_MEAN_NET_RETURN', group=key, bps=cost)


def self_test():
    values = [Point('a', PV, 'A', '2026-08-31', 1, Decimal(0), 'MATURE_VALID', True),
              Point('b', PV, 'B', '2026-09-01', 1, Decimal('.2'), 'MATURE_VALID', True),
              Point('b', PV, 'C', '2026-09-01', 1, Decimal('-.2'), 'MATURE_VALID', False)]
    result = arithmetic(values, ['2026-08-31', '2026-09-01'])
    assert result['mature_valid'] == 3 and result['zero'] == 1 and result['positive'] == 1
    assert result['up_rate'] == Decimal(1) / 3 and result['mean_return'] == 0
    assert result['date_equal_up_rate'] == Decimal('.25')
    assert endpoint_return('10', '10.000000001') == Decimal('.0000000001')
    assert arithmetic([], ['2026-09-01'])['mean_return'] is None
    print('5 independent arithmetic self-checks passed; no replay artifacts read.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replay', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--scope', choices=('primary5', 'all20'), default='all20')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.replay is None or args.output is None:
        parser.error('--replay and --output are required for a real artifact audit')
    args.output.mkdir(parents=True, exist_ok=False)
    start = datetime.now(timezone.utc).isoformat()
    audit = Audit(args.replay, args.output, args.scope)
    result, error, exit_code = {}, None, 1
    try:
        result = audit.run()
        exit_code = 0 if not audit.failed else 1
    except Exception:
        error = traceback.format_exc()
        (args.output / 'exception.txt').write_text(error, encoding='utf-8')
        print(error, file=sys.stderr)
    finally:
        audit.failure_log.close()
        summary = {'status': 'PASS' if exit_code == 0 else 'FAIL', 'failed_checks': audit.failed,
                   'checks_by_name': dict(audit.checks), 'fatal_exception': bool(error), **result}
        (args.output / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        record = {'command': [sys.executable, *sys.argv], 'cwd': str(Path.cwd()),
                  'started_at': start, 'finished_at': datetime.now(timezone.utc).isoformat(), 'exit_code': exit_code,
                  'python': sys.version, 'audit_source_sha256': file_hash(__file__),
                  'independence': 'STDLIB_AND_DECIMAL_ONLY_NO_PRODUCTION_IMPORTS',
                  'replay_artifacts_modified': False, 'input_file_hashes': audit.files,
                  'output_hashes': {path.name: file_hash(path) for path in args.output.iterdir() if path.is_file()}}
        (args.output / 'execution.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'status': summary['status'], 'failed_checks': audit.failed, 'fatal_exception': bool(error)}))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
