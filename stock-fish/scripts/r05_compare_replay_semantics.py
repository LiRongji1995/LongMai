"""独立逐字段比较旧/新 R05 实际回放；仅允许列明的身份/凭证派生差异。"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gc
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import traceback
from uuid import UUID

CORE_FILE = 'stockfish_core/yaogu/strategy_experiments.py'
OLD_IMPL = 'R05_STRATEGY_EXPERIMENTS_V3_SPEC_RECTIFIED'
NEW_IMPL = 'R05_STRATEGY_EXPERIMENTS_V4_CANONICAL_IDENTITY'
HEX = re.compile(r'^[0-9a-f]{64}$')
SOURCE_KEYS = ('original_receipt_id', 'original_capture_result_hash', 'normalized_content_hash', 'business_date', 'clock_interpretation')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def compare_day(day, old_root, new_root):
    old_path, new_path = old_root / day / 'rank.json', new_root / day / 'rank.json'
    old_sha, new_sha = file_hash(old_path), file_hash(new_path)
    before, after = read(old_path), read(new_path)
    old_ref, new_ref = read(old_root / day / 'input_reference.json'), read(new_root / day / 'input_reference.json')
    errors, allowed, samples = [], Counter(), {}
    same_leaf_count = 0

    def fail(path, reason, old=None, new=None):
        errors.append({'path': '/' + '/'.join(map(str, path)), 'reason': reason,
                       'old': old if not isinstance(old, (dict, list)) else digest(old),
                       'new': new if not isinstance(new, (dict, list)) else digest(new)})

    def accept(category, path, old, new):
        allowed[category] += 1
        samples.setdefault(category, {'path': '/' + '/'.join(map(str, path)),
                                     'old_value_or_hash': digest(old) if isinstance(old, (list, dict)) else old,
                                     'new_value_or_hash': digest(new) if isinstance(new, (list, dict)) else new})

    old_audit, new_audit = old_ref['normalization_audit'], new_ref['normalization_audit']
    if before['results']['DAILY_V1'] != read(old_root / day / 'daily_attempt.json') or after['results']['DAILY_V1'] != read(new_root / day / 'daily_attempt.json'):
        fail(('results', 'DAILY_V1'), 'DAILY_ACTUAL_ATTEMPT_BINDING_MISMATCH')
    old_sources, new_sources = old_audit['sources'], new_audit['sources']
    old_origin = Counter(tuple(s[k] for k in SOURCE_KEYS) for s in old_sources)
    new_origin = Counter(tuple(s[k] for k in SOURCE_KEYS) for s in new_sources)
    if old_origin != new_origin:
        fail(('input_reference', 'normalization_audit', 'sources'), 'ORIGINAL_RECEIPT_CAPTURE_CONTENT_BUSINESS_DATE_CHANGED')
    new_by_origin = {tuple(s[k] for k in SOURCE_KEYS): s['normalized_receipt_id'] for s in new_sources}
    if len(new_by_origin) != len(new_sources):
        fail(('input_reference', 'normalization_audit', 'sources'), 'AMBIGUOUS_SOURCE_MAPPING')
    source_map = {s['normalized_receipt_id']: new_by_origin.get(tuple(s[k] for k in SOURCE_KEYS)) for s in old_sources}
    if len(source_map) != len(old_sources) or any(v is None for v in source_map.values()):
        fail(('input_reference', 'normalization_audit', 'sources'), 'SOURCE_MAPPING_NOT_BIJECTIVE')
    audit_mutable = {'sources', 'source_policy_hash', 'actual_generated_at', 'input_hash'}
    if {k: v for k, v in old_audit.items() if k not in audit_mutable} != {k: v for k, v in new_audit.items() if k not in audit_mutable}:
        fail(('input_reference', 'normalization_audit'), 'NON_IDENTITY_NORMALIZATION_AUDIT_CHANGED')

    def valid_hex_pair(x, y):
        return isinstance(x, str) and isinstance(y, str) and bool(HEX.fullmatch(x)) and bool(HEX.fullmatch(y))

    def explicit_identity_difference(path, x, y):
        key = path[-1]
        if key == 'implementation_version':
            return (x, y) == (OLD_IMPL, NEW_IMPL), 'IMPLEMENTATION_REVISION'
        if key == CORE_FILE and len(path) > 1 and path[-2] == 'source_file_hashes':
            return (x, y) == (before['source_file_hashes'][CORE_FILE], after['source_file_hashes'][CORE_FILE]), 'CORE_SOURCE_FILE_HASH'
        if key == 'source_hash':
            if path == ('results', 'DAILY_V1', 'source_hash'):
                return valid_hex_pair(x, y), 'DAILY_SOURCE_RECEIPT_DERIVED_HASH'
            return (x, y) == (before['source_hash'], after['source_hash']), 'CORE_SOURCE_IDENTITY'
        if key == 'input_hash':
            return (x, y) == (old_ref['canonical_input_hash'], new_ref['canonical_input_hash']), 'NORMALIZED_INPUT_HASH'
        if path == ('input_id',):
            return (x, y) == ('market_input_' + old_ref['canonical_input_hash'][:16], 'market_input_' + new_ref['canonical_input_hash'][:16]), 'NORMALIZED_INPUT_ID'
        if path == ('universe_hash',):
            return (x, y) == (digest(before['qualification']), digest(after['qualification'])), 'QUALIFICATION_AGGREGATE_HASH'
        if key == 'selected_security_hash' and path[-2] == 'qualification':
            return valid_hex_pair(x, y), 'CANONICAL_SELECTED_SECURITY_HASH'
        if key == 'record_hash' and 'qualification' in path and 'selected_records' in path:
            return valid_hex_pair(x, y), 'DATED_SECURITY_RECORD_WITH_REMAPPED_RECEIPTS'
        if key == 'security_record_hash' and any(k in path for k in ('items', 'eligible_feature_items', 'selected_items')):
            return valid_hex_pair(x, y), 'ITEM_SECURITY_RECORD_WITH_REMAPPED_RECEIPTS'
        if path in (('effective_input_hash',), ('replay_plan_hash',),
                    ('results', 'DAILY_V1', 'original_candidate_run_hash'), ('results', 'DAILY_V1', 'coverage', 'source_receipt_hash')):
            return valid_hex_pair(x, y), 'DECLARED_DERIVED_HASH:' + '/'.join(path)
        if path == ('run_id',):
            keys = ('input_hash', 'observation_date', 'decision_cutoff', 'spec_hash', 'source_hash', 'implementation_version', 'universe_hash')
            return (x, y) == tuple('strategy_experiment_' + digest({k: r[k] for k in keys})[:24] for r in (before, after)), 'CANONICAL_RUN_ID'
        if path == ('results', 'DAILY_V1', 'run_id'):
            try:
                valid = all(str(UUID(v)) == v and UUID(v).version == 4 for v in (x, y))
            except (ValueError, TypeError, AttributeError):
                valid = False
            return valid, 'DAILY_ACTUAL_ATTEMPT_UUID'
        if path == ('generated_at',):
            try:
                a, b = datetime.fromisoformat(x), datetime.fromisoformat(y)
                return a.utcoffset() is not None and b.utcoffset() is not None and a <= b, 'ACTUAL_GENERATION_TIME'
            except (ValueError, TypeError):
                return False, 'ACTUAL_GENERATION_TIME'
        return False, None

    def walk(x, y, path=()):
        nonlocal same_leaf_count
        if type(x) is not type(y):
            fail(path, 'TYPE_CHANGED', x, y)
            return
        if isinstance(x, dict):
            if x.keys() != y.keys():
                fail(path, 'FIELD_SET_CHANGED', sorted(x), sorted(y))
            for key in sorted(x.keys() & y.keys()):
                walk(x[key], y[key], path + (key,))
        elif isinstance(x, list):
            if path and path[-1] == 'source_refs' and x != y:
                # Receipt IDs are mapped by unchanged original receipt/capture/content/business-date tuple.
                mapped = [source_map.get(v) for v in x]
                if any(v is None for v in mapped) or Counter(mapped) != Counter(y):
                    fail(path, 'SOURCE_REFERENCE_ORIGIN_CHANGED', x, y)
                else:
                    accept('BIJECTIVE_SOURCE_REFERENCE_REMAP', path, x, y)
                return
            if path and path[-1] == 'price_record_hashes' and x != y:
                if len(x) == len(y) == 6 and all(valid_hex_pair(a, b) for a, b in zip(x, y)):
                    accept('SIX_PRICE_RECORD_HASHES_WITH_REMAPPED_RECEIPTS', path, x, y)
                else:
                    fail(path, 'PRICE_RECORD_HASH_SHAPE_CHANGED', x, y)
                return
            if len(x) != len(y):
                fail(path, 'LIST_LENGTH_CHANGED', len(x), len(y))
            for i, (a, b) in enumerate(zip(x, y)):
                walk(a, b, path + (i,))
        elif x != y:
            valid, category = explicit_identity_difference(path, x, y)
            if valid:
                accept(category, path, x, y)
            else:
                fail(path, 'UNAUTHORIZED_DIFFERENCE', x, y)
        else:
            same_leaf_count += 1

    if (before['implementation_version'], after['implementation_version']) != (OLD_IMPL, NEW_IMPL):
        fail(('implementation_version',), 'UNEXPECTED_COMPARISON_VERSIONS')
    if before['spec_hash'] != after['spec_hash']:
        fail(('spec_hash',), 'R05_SPEC_CHANGED')
    walk(before, after)
    if old_sha != file_hash(old_path) or new_sha != file_hash(new_path):
        fail((), 'RANK_CHANGED_DURING_COMPARISON')
    pv, focus = after['results']['PV_OBSERVATION_V1'], after['selection_results']['PV_FOCUS_PREVIEW_V0.1']
    return {'date': day, 'status': 'PASS' if not errors else 'FAIL', 'old_rank_sha256': old_sha, 'new_rank_sha256': new_sha,
            'exact_unchanged_leaf_count': same_leaf_count, 'original_source_record_count': len(old_sources),
            'original_source_multiset_hash': digest(sorted([list(k) + [v] for k, v in old_origin.items()])),
            'source_mapping_count': len(source_map), 'allowed_difference_counts': dict(allowed),
            'allowed_difference_first_examples': samples, 'failure_count': len(errors), 'failures': errors,
            'pv_count': len(pv['items']), 'complete_feature_count': len(pv['eligible_feature_items']),
            'focus_count': len(focus['selected_ids']), 'eligible_count': after['qualification']['eligible'],
            'canonical_strategy_statuses': {k: v.get('status') for k, v in after['results'].items() if not v.get('compatibility_alias')}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old', type=Path, required=True)
    parser.add_argument('--new', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--completed-only', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    start, timestamp = time.perf_counter(), datetime.now(timezone.utc).isoformat()
    old_plan, new_plan = read(args.old / 'replay_plan.json'), read(args.new / 'replay_plan.json')
    policy_a, policy_b = old_plan['historical_source_policy'], new_plan['historical_source_policy']
    policy_same = {k: v for k, v in policy_a.items() if k != 'verified_at'} == {k: v for k, v in policy_b.items() if k != 'verified_at'}
    days = old_plan['attempt_sessions']
    plan_same = days == new_plan['attempt_sessions'] and old_plan['primary_sessions'] == new_plan['primary_sessions']
    dates = [d for d in days if (args.new / d / 'attempt.json').exists() and (args.new / d / 'rank.json').exists()] if args.completed_only else days
    rows = []
    for day in dates:
        try:
            row = compare_day(day, args.old, args.new)
        except Exception:
            row = {'date': day, 'status': 'ERROR', 'exception': traceback.format_exc()}
        (args.output / (day + '.json')).write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
        rows.append(row)
        print(day, row['status'], 'business-diffs', row.get('failure_count'), flush=True)
        gc.collect()
    complete = dates == days and len(days) == 20
    summary = {'status': 'PASS' if policy_same and plan_same and all(r['status'] == 'PASS' for r in rows) and (complete or args.completed_only) else 'FAIL',
               'audit_extent': 'FINAL_ALL_20_DATES' if complete else 'PARTIAL_COMPLETED_DATES_ONLY',
               'started_at': timestamp, 'finished_at': datetime.now(timezone.utc).isoformat(), 'duration_seconds': time.perf_counter() - start,
               'script_sha256': file_hash(__file__), 'command_argv': sys.argv, 'plan_dates_unchanged': plan_same,
               'policy_changed_only_verified_at': policy_same, 'old_policy_verified_at': policy_a['verified_at'],
               'new_policy_verified_at': policy_b['verified_at'], 'old_policy_hash': digest(policy_a), 'new_policy_hash': digest(policy_b),
               'expected_dates': days, 'audited_dates': dates, 'daily_results': rows,
               'boundaries': ['旧新归一化凭证ID仅按不变原始receipt/capturehash/contenthash/businessday的双射核对。',
                              '全部其他rank字段递归严格相等，包括资格分区、完整exact因子、价格、排名、焦点与各策略状态。',
                              '不读取未来收益，不改变任何原始或重跑产物；原20日记录保留。']}
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print('FINAL', summary['status'], summary['audit_extent'], flush=True)
    return 0 if summary['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
