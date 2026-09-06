"""只读复算实际 R05 主五日；不导入生产策略、归一化器或回放实现。"""
from __future__ import annotations

import argparse
import bisect
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
import gc
import gzip
import hashlib
import json
from pathlib import Path
import platform
import sys
import time
import traceback


DATES = ('2026-08-31', '2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04')
PV_ID = 'PV_OBSERVATION_V1'
FOCUS_ID = 'PV_FOCUS_PREVIEW_V0.1'
WEIGHTS = {'A': Fraction(45, 100), 'R': Fraction(35, 100), 'H': Fraction(20, 100)}


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def canonical_hash(value):
    h = hashlib.sha256()
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    for part in encoder.iterencode(value):
        h.update(part.encode('utf-8'))
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def ratio(value):
    if isinstance(value, dict):
        return Fraction(value['numerator'], value['denominator'])
    return Fraction(str(value))


def display(value):
    with localcontext() as context:
        context.prec = 80
        return float((Decimal(value.numerator) / Decimal(value.denominator)).quantize(Decimal('0.000001')))


def pair(value):
    return {'numerator': value.numerator, 'denominator': value.denominator}


def native_bar_record_hash(bar):
    # The published record hash covers a Python model_dump dictionary. Its four datetime
    # values pass through json default=str (space separator), unlike canonical input JSON.
    native = dict(bar)
    for key in ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at'):
        native[key] = str(datetime.fromisoformat(native[key]))
    return canonical_hash(native)


def audit_day(day, replay, source_root):
    failures = []
    checks = 0

    def check(condition, code, detail=None):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append({'code': code, 'detail': detail})

    folder = replay / day
    rank_path, ref_path = folder / 'rank.json', folder / 'input_reference.json'
    rank_sha, ref_sha = file_hash(rank_path), file_hash(ref_path)
    rank, ref = read_json(rank_path), read_json(ref_path)
    pv, focus = rank['results'][PV_ID], rank['selection_results'][FOCUS_ID]
    result = {'date': day, 'rank_file_sha256': rank_sha, 'input_reference_file_sha256': ref_sha,
              'canonical_input_hash': ref['canonical_input_hash'], 'source_hash': rank['source_hash'],
              'spec_hash': rank['spec_hash'], 'source_file_hashes': rank['source_file_hashes'],
              'spec_source_hashes': rank['spec_source_hashes'], 'decision_cutoff': rank['decision_cutoff'],
              'observed_pv_count': len(pv['items']), 'observed_focus_count': len(focus['selected_ids'])}
    check(day == rank['observation_date'] == pv['observation_date'] == focus['observation_date'], 'SIGNAL_DATE')
    check(rank['strategy_family'] == 'R05', 'STRATEGY_FAMILY')
    check(rank['input_hash'] == ref['canonical_input_hash'] == pv['input_hash'], 'BOUND_INPUT_HASH')
    check(rank['source_hash'] == pv['source_hash'] and rank['spec_hash'] == pv['spec_hash'], 'BOUND_IDENTITY')
    for relative, expected in rank['source_file_hashes'].items():
        check(file_hash(source_root / relative) == expected, 'SOURCE_FILE_HASH', relative)
    for relative, expected in rank['spec_source_hashes'].items():
        check(file_hash(source_root / 'config' / relative) == expected, 'SPEC_FILE_HASH', relative)
    check(rank['source_hash'] == canonical_hash(rank['source_file_hashes']), 'SOURCE_AGGREGATE_HASH')
    check(file_hash(ref['path']) == ref['sha256'], 'COMPRESSED_INPUT_SHA256')
    with gzip.open(ref['path'], 'rt', encoding='utf-8') as stream:
        bundle = json.load(stream)
    check(canonical_hash(bundle) == ref['canonical_input_hash'], 'CANONICAL_INPUT_SHA256')
    check(bundle['observation_date'] == day, 'INPUT_OBSERVATION_DATE')
    sessions = bundle['sessions']
    check(sessions == sorted(set(sessions)), 'CALENDAR_UNIQUE_SORTED')
    dates = [s for s in sessions if s <= DATES[-1]][-5:]
    check(dates == list(DATES), 'LAST_FIVE_ENDED_SESSIONS', dates)
    six = [s for s in sessions if s <= day][-6:]
    check(len(six) == 6 and six[-1] == day, 'SIX_CONSECUTIVE_SESSIONS')
    cutoff = datetime.fromisoformat(rank['decision_cutoff'])
    future_bars = [b for b in bundle['bars'] if b['session'] > day]
    check(not future_bars, 'FUTURE_BAR_ZERO', len(future_bars))
    late_business_clocks = sum(datetime.fromisoformat(b[k]) > cutoff for b in bundle['bars']
                               for k in ('event_time', 'source_published_at'))
    check(late_business_clocks == 0, 'BUSINESS_CLOCKS_BEFORE_CUTOFF', late_business_clocks)
    check(ref['normalization_audit']['future_price_rows_in_signal'] == len(future_bars), 'AUDIT_FUTURE_COUNT')
    securities = bundle['universe']['securities']
    counts = Counter(s['instrument_id'] for s in securities)
    check(all(n == 1 for n in counts.values()), 'INPUT_SECURITY_UNIQUE')
    selected = {s['instrument_id']: s for s in securities}
    q = rank['qualification']
    eligible, excluded, unknown = (set(q['eligible_ids']),
                                  {r['instrument_id'] for r in q['known_excluded_records']},
                                  {r['instrument_id'] for r in q['eligibility_unknown_records']})
    check(len(eligible) == len(q['eligible_ids']) == q['eligible'], 'ELIGIBLE_UNIQUE')
    check(not (eligible & excluded or eligible & unknown or excluded & unknown), 'QUALIFICATION_DISJOINT')
    check(eligible | excluded | unknown == set(selected), 'QUALIFICATION_PARTITION_EXACT')
    check(q['master_expected'] == len(selected) == q['eligible'] + q['known_excluded'] + q['eligibility_unknown'],
          'QUALIFICATION_COUNTS')
    direct_eligible = set()
    for instrument, security in selected.items():
        active = security['effective_from'] <= day and (not security['effective_to'] or day < security['effective_to'])
        age = sum(security['listed_on'] <= s <= day for s in sessions) if security['listed_on'] else 0
        permitted = (security['exchange'] in ('SSE', 'SZSE') and security['board'] == 'MAIN'
                     and security['security_type'] == 'A_COMMON' and age >= 60
                     and all(security[k] is False for k in ('st', 'suspended', 'delisting_period'))
                     and (not security['delisted_on'] or day < security['delisted_on']) and active)
        if permitted:
            direct_eligible.add(instrument)
    check(direct_eligible == eligible, 'INDEPENDENT_DATED_QUALIFICATION',
          {'missing': sorted(direct_eligible - eligible), 'unexpected': sorted(eligible - direct_eligible)})

    # The six-session source map is constructed from raw canonical input, independently of rank references.
    bar_map = {}
    duplicate_keys = []
    for bar in bundle['bars']:
        if bar['session'] in six and bar['instrument_id'] in eligible:
            key = (bar['instrument_id'], bar['session'])
            if key in bar_map:
                duplicate_keys.append(key)
            bar_map[key] = bar
    check(not duplicate_keys, 'SIX_DAY_BAR_UNIQUE', duplicate_keys[:10])
    features = pv['eligible_feature_items']
    complete_ids = [row['instrument_id'] for row in features]
    check(len(complete_ids) == len(set(complete_ids)), 'COMPLETE_FEATURE_UNIQUE')
    check(set(complete_ids).issubset(eligible), 'COMPLETE_FEATURE_QUALIFIED')
    recomputed = {}
    for row in features:
        instrument = row['instrument_id']
        missing = [s for s in six if (instrument, s) not in bar_map]
        check(not missing, 'RAW_SIX_DAYS_PRESENT', {'instrument': instrument, 'missing': missing})
        if missing:
            continue
        raw = [bar_map[(instrument, s)] for s in six]
        now = raw[-1]
        factors = {'A': 5 * ratio(now['amount_cny']) / sum(ratio(b['amount_cny']) for b in raw[:-1]),
                   'R': ratio(now['comparable_close']) / ratio(raw[0]['comparable_close']) - 1,
                   'H': (ratio(now['high']) - ratio(now['low'])) / ratio(now['reference_prev_close'])}
        recomputed[instrument] = factors
        check(len({b['comparable_basis'] for b in raw}) == 1 and bool(now['comparable_basis']),
              'COMPARABLE_BASIS_SIX_DAY', instrument)
        for factor, value in factors.items():
            check(value == ratio(row['exact_factors'][factor]), 'EXACT_FACTOR_FROM_RAW', [instrument, factor])
            check(display(value) == row[factor], 'DISPLAY_FACTOR_FROM_RAW', [instrument, factor])
        for key in ('open', 'high', 'low', 'close', 'reference_prev_close', 'comparable_close'):
            check(ratio(now[key]) == ratio(row['exact_prices'][key]), 'EXACT_PRICE_FROM_RAW', [instrument, key])
        check(row['price_record_hashes'] == [native_bar_record_hash(b) for b in raw], 'SIX_RAW_RECORD_HASHES', instrument)
        check(row['security_record_hash'] == canonical_hash(selected[instrument]), 'SECURITY_RECORD_HASH', instrument)

    passes = {i: factors for i, factors in recomputed.items() if factors['A'] >= Fraction(6, 5) and factors['R'] > 0}
    observed_ids = [row['instrument_id'] for row in pv['items']]
    check(set(passes) == set(observed_ids) and len(observed_ids) == len(set(observed_ids)), 'PV_EXACT_GATE_AND_UNIQUE')
    n = len(passes)
    sorted_values = {key: sorted(f[key] for f in passes.values()) for key in WEIGHTS}
    percentiles, scores = {}, {}
    for instrument, factors in passes.items():
        percentiles[instrument] = {}
        for key in WEIGHTS:
            ordered, value = sorted_values[key], factors[key]
            lo, hi = bisect.bisect_left(ordered, value), bisect.bisect_right(ordered, value)
            percentiles[instrument][key] = Fraction(100 * (lo + hi), 2 * n)
        scores[instrument] = sum(WEIGHTS[key] * percentiles[instrument][key] for key in WEIGHTS)
    expected_order = sorted(passes, key=lambda instrument: (-scores[instrument], instrument))
    check(expected_order == observed_ids, 'FULL_EXACT_MIDRANK_ORDER')
    for position, row in enumerate(pv['items'], start=1):
        instrument = row['instrument_id']
        if instrument not in passes:
            continue
        check(row['rank'] == position, 'RANK_POSITION', instrument)
        check(display(scores[instrument]) == row['S_PV'], 'WEIGHTED_SCORE', instrument)
        for key in WEIGHTS:
            check(row['exact_factors'][key] == pair(passes[instrument][key]), 'PV_ITEM_EXACT_FACTOR', [instrument, key])
            check(display(percentiles[instrument][key]) == row['rank_percentiles'][key], 'MIDRANK_PERCENTILE', [instrument, key])
    expected_focus = []
    for instrument in expected_order:
        factor, now = passes[instrument], bar_map[(instrument, day)]
        high, low, close, opening = (ratio(now[k]) for k in ('high', 'low', 'close', 'open'))
        if (factor['A'] >= 2 and factor['R'] >= Fraction(1, 10) and close >= opening
                and high > low and (close - low) / (high - low) >= Fraction(7, 10)):
            expected_focus.append(instrument)
    check(expected_focus == focus['selected_ids'], 'FULL_FOCUS_SET_AND_PARENT_ORDER')
    check(expected_focus == [row['instrument_id'] for row in focus['selected_items']], 'FOCUS_SELECTED_ITEMS')
    check(expected_focus == [row['instrument_id'] for row in focus['items'] if row['selected']], 'FOCUS_ITEMS_SELECTED_FLAGS')
    check(observed_ids == [row['instrument_id'] for row in focus['items']], 'FOCUS_REVIEW_PRESERVES_FULL_PARENT')
    check(expected_focus == pv['focus_ids'], 'PV_FOCUS_LINK')
    check(focus['count'] == len(expected_focus), 'FOCUS_COUNT')
    check(all(focus[k] is None for k in ('quota_min', 'quota_max', 'rank_window')), 'NO_QUOTA_CAP')

    # Predeclared deterministic sample rule: first, middle (floor N/2), last PV position; no return-based choice.
    sample_positions = sorted({0, n // 2, n - 1}) if n else []
    samples = []
    for position in sample_positions:
        instrument = expected_order[position]
        samples.append({'instrument_id': instrument, 'rank': position + 1,
                        'exact_factors_recomputed': {k: pair(v) for k, v in passes[instrument].items()},
                        'exact_score_recomputed': pair(scores[instrument]), 'is_focus': instrument in expected_focus,
                        'six_sessions': [{'session': s, **{k: bar_map[(instrument, s)][k] for k in
                            ('amount_cny', 'open', 'high', 'low', 'close', 'reference_prev_close', 'comparable_close')}} for s in six]})
    result.update({'status': 'PASS' if not failures else 'FAIL', 'checks': checks, 'failures': failures,
                   'raw_factor_rows_checked': len(recomputed), 'pv_rows_checked': len(passes),
                   'future_bar_count': len(future_bars), 'eligible_count': len(eligible),
                   'expected_focus_count': len(expected_focus), 'sample_rule': 'PV_POSITIONS_1_FLOOR_N_OVER_2_PLUS_1_N',
                   'samples': samples, 'scope': rank['replay_scope'], 'input_bar_count': len(bundle['bars']),
                   'input_original_capture_clocks_modified': ref['normalization_audit']['original_capture_clocks_modified']})
    check(file_hash(rank_path) == rank_sha and file_hash(ref_path) == ref_sha, 'ARTIFACT_STABLE_DURING_AUDIT')
    result.update(status='PASS' if not failures else 'FAIL', checks=checks, failures=failures)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replay', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    clock = time.perf_counter()
    reports = []
    for day in DATES:
        try:
            report = audit_day(day, args.replay, args.source_root)
        except Exception:
            report = {'date': day, 'status': 'ERROR', 'exception': traceback.format_exc()}
        (args.output / (day + '.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        reports.append({k: v for k, v in report.items() if k != 'samples'})
        print(day, report['status'], 'checks=', report.get('checks'), 'failures=', len(report.get('failures', [])), flush=True)
        del report
        gc.collect()
    identities = {(r.get('source_hash'), r.get('spec_hash')) for r in reports}
    same_identity = len(identities) == 1 and all(identities.pop())
    all_pass = all(r['status'] == 'PASS' for r in reports) and same_identity
    summary = {'status': 'PASS' if all_pass else 'FAIL', 'dates': list(DATES), 'started_at': started,
               'finished_at': datetime.now(timezone.utc).isoformat(), 'duration_seconds': time.perf_counter() - clock,
               'python': sys.version, 'platform': platform.platform(), 'script_sha256': file_hash(__file__),
               'production_strategy_imported': False, 'simultaneous_days_loaded': 1,
               'same_source_and_spec_hash_across_five_days': same_identity, 'daily_results': reports,
               'limitations': ['验证原始输入与计算一致性，不独立认证供应商事实真实性或原始历史归档可见性。',
                               '投资优势未建立；本审计不涵盖后续价格结果、模拟成交与策略优势统计。',
                               '仅核验已输出完整特征集合；未重新定义或替代缺字段证券的缺失原因。']}
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print('FINAL', summary['status'], 'seconds=', round(summary['duration_seconds'], 3), flush=True)
    return 0 if all_pass else 1


if __name__ == '__main__':
    raise SystemExit(main())
