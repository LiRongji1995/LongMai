"""Serial bounded cold/warm/corrected increment/bad-input stress acceptance.

Each increment starts from an exact Day-T cache backup. A failed/incomplete suite
is preserved and never silently relabelled as cold or restarted over its cache.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import math
import os
import sqlite3
import statistics
import sys
import time
import psutil

from r16_r1_supervise import supervise, write_json, sha256, source_metadata, now

ROOT = Path(__file__).resolve().parents[2]


def backup(source, destination, *, deadline=None, progress=lambda value: None):
    destination = Path(destination)
    if destination.exists():
        raise ValueError('CACHE_BACKUP_DESTINATION_EXISTS')
    with sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(destination) as dst:
        src.execute('PRAGMA query_only=ON')
        def report(status, remaining, total):
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('BENCHMARK_PREPARATION_BUDGET_EXCEEDED')
            progress({'stage': 'SQLITE_BACKUP', 'pages_done': total-remaining, 'pages_total': total})
        src.backup(dst, pages=4096, progress=report)
    return {'source': str(Path(source).resolve()), 'source_sha256': sha256(source),
            'target': str(destination.resolve()), 'target_sha256': sha256(destination),
            'method': 'SQLITE_CONSISTENT_READ_ONLY_BACKUP'}


def run(args):
    if not all(math.isfinite(value) and value > 0 for value in (args.per_run_budget, args.preparation_budget, args.total_budget)):
        raise ValueError('FINITE_POSITIVE_BUDGETS_REQUIRED')
    baseline_root = Path(args.baseline_source_root).resolve()
    binding = json.loads(Path(args.baseline_binding).read_text(encoding='utf-8'))
    if baseline_root == ROOT or binding.get('commit') != 'e2855d695f8818bb13103997e2ba1f1552538d99' or binding.get('tree') != '81da2f17ea2adbc1e9488a1b53e2be1da2b31c8e':
        raise ValueError('EXPECTED_R16_BASELINE_ARCHIVE_REQUIRED')
    suite_deadline = time.monotonic()+args.total_budget
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = json.loads(Path(args.corrected_build).read_text(encoding='utf-8'))
    common = source['common_information_cutoff']
    db = Path(source['target_db']).resolve()
    suite = {'version': 'R16_R1_SERIAL_BENCHMARK_SUITE_V1', 'start': now(),
        'source': source_metadata(ROOT), 'command_argv': sys.argv,
        'pid': os.getpid(), 'process_create_time': psutil.Process().create_time(),
        'total_budget_seconds': args.total_budget, 'preparation_budget_seconds': args.preparation_budget,
        'corrected_build_path': str(Path(args.corrected_build).resolve()), 'corrected_build_sha256': sha256(args.corrected_build),
        'rows': [], 'cache_backups': [], 'status': 'RUNNING',
        'profile_policy': 'ALL_ROWS_CPROFILE_SAME_WORKLOAD; API_E2E_SEPARATELY_UNPROFILED',
        'sample_policy': 'COLD_ONCE_WARM_THREE_PER_IMPLEMENTATION; MEDIAN_RANGE_ONLY_NO_P95',
        'cold_definition': 'NEW_PROCESS_AND_EMPTY_DERIVED_CACHE; OS_PAGE_CACHE_NOT_CLEARED; FIXED_BASELINE_THEN_FINAL_ORDER'}
    def save():
        write_json(output/'suite-checkpoint.json', suite)
    def prepared_backup(source, target):
        deadline = min(suite_deadline, time.monotonic()+args.preparation_budget)
        last = [0.0]
        def progress(value):
            if time.monotonic()-last[0] >= 10:
                suite['preparation_progress'] = value; save()
                print(json.dumps(value), flush=True); last[0] = time.monotonic()
        return backup(source, target, deadline=deadline, progress=progress)
    def one(label, input_id, cache_db, source_db=db, cutoff=common, implementation=ROOT, binding=None):
        command = [sys.executable, '-X', 'utf8', str(ROOT/'stock-fish/scripts/r16_r1_benchmark_worker.py'),
            '--db', str(source_db), '--input-id', input_id, '--cutoff', cutoff,
            '--cache-db', str(cache_db), '--output', str(output/label), '--label', label,
            '--source-root', str(implementation), '--cprofile']
        if binding:
            command += ['--source-binding', str(binding)]
        suite['current'] = label; save()
        run = supervise(command, output/(label+'-process'), cwd=ROOT, source_root=ROOT,
            registry=args.registry, budget_seconds=min(args.per_run_budget, suite_deadline-time.monotonic()), input_manifest=args.corrected_build, label=label)
        if run['exit_code'] != 0:
            suite.update(status='FAILED', failed_label=label); save()
            raise RuntimeError('BENCHMARK_FAILED:'+label)
        report = json.loads((output/label/'benchmark.json').read_text(encoding='utf-8'))
        workload_peak = None
        with (output/(label+'-process')/'rss.jsonl').open(encoding='utf-8') as rss:
            measured = [sample['rss_family_bytes'] for line in rss if
                report['workload_perf_counter_start'] <= (sample := json.loads(line))['perf_counter'] <= report['workload_perf_counter_end']]
        if measured:
            workload_peak = max(measured)
        suite['rows'].append({'label': label, 'report': str(output/label/'benchmark.json'),
            'report_sha256': sha256(output/label/'benchmark.json'), 'elapsed_seconds': report['elapsed_seconds'],
            'rss_family_sampled_peak_bytes': run['rss_family_sampled_peak_bytes'],
            'rss_scope': 'ENTIRE_WORKER_INCLUDING_PROFILE_AND_NODE_DBSTAT',
            'workload_rss_family_sampled_peak_bytes': workload_peak,
            'business_result_hash': report['business_result_hash'], 'input_id': input_id,
            'cache_hits': report['derived_feature_store']['cache_hits'],
            'cache_misses': report['derived_feature_store']['cache_misses'],
            'new_rows': report['derived_feature_store']['new_rows'],
            'invalidated_rows': report['derived_feature_store']['invalidated_rows'],
            'cache_footprint_after_checkpoint': report['checkpoints']['cache']['after']})
        save()
    try:
        # The archived implementation may initialize its source schema. It gets
        # its own exact isolated input copy; it never opens production to write.
        baseline_db = output/'baseline-input.sqlite3'
        suite['source_backup'] = prepared_backup(db, baseline_db); save()
        baseline_cache = output/'baseline-cache.sqlite3'
        for label in ['baseline-cold','baseline-warm-1','baseline-warm-2','baseline-warm-3']:
            one(label, source['input_t_corrected'], baseline_cache, source_db=baseline_db,
                implementation=Path(args.baseline_source_root).resolve(), binding=args.baseline_binding)
        cache = output/'final-cache.sqlite3'
        one('final-cold', source['input_t_corrected'], cache)
        day_t = output/'day-t-cache.sqlite3'
        suite['cache_backups'].append(prepared_backup(cache, day_t)); save()
        for index in range(1,4):
            one('final-warm-'+str(index), source['input_t_corrected'], cache)
        increment = output/'corrected-increment-cache.sqlite3'
        suite['cache_backups'].append(prepared_backup(day_t, increment)); save()
        one('corrected-t-to-t1', source['input_t_plus_1_corrected'], increment)
        if args.old_bad_source_db:
            from r16_r1_evidence_assets import copy_input_closure
            old_db = output/'old-bad-input.sqlite3'
            suite['current'] = 'OLD_BAD_STRESS_INPUT_PREPARATION'; save()
            prepare_deadline = min(suite_deadline, time.monotonic()+args.preparation_budget)
            def closure_progress(event):
                suite['preparation_progress'] = event; save()
                print(json.dumps(event), flush=True)
                if time.monotonic() >= prepare_deadline:
                    raise TimeoutError('OLD_BAD_CLOSURE_PREPARATION_BUDGET_EXCEEDED')
            # This is isolation preparation, outside every measured benchmark.
            suite['old_bad_closure'] = copy_input_closure(args.old_bad_source_db, old_db,
                [source['input_t_plus_1_old_bad']], progress=closure_progress)
            stress = output/'old-bad-stress-cache.sqlite3'
            suite['cache_backups'].append(prepared_backup(day_t, stress)); save()
            one('old-bad-invalidation-stress', source['input_t_plus_1_old_bad'], stress,
                source_db=old_db, cutoff=args.old_bad_cutoff)
        summary = {}
        for implementation in ('baseline','final'):
            warm = [row['elapsed_seconds'] for row in suite['rows'] if row['label'].startswith(implementation+'-warm-')]
            summary[implementation] = {'cold_seconds': next(row['elapsed_seconds'] for row in suite['rows'] if row['label'] == implementation+'-cold'),
                'warm_samples': warm, 'warm_median_seconds': statistics.median(warm), 'warm_range_seconds': [min(warm),max(warm)]}
        hashes = {row['business_result_hash'] for row in suite['rows'] if row['label'].startswith(('baseline-','final-'))}
        summary['same_corrected_t_business_hashes_match'] = len(hashes) == 1
        summary['warm_ratio_final_over_baseline'] = summary['final']['warm_median_seconds']/summary['baseline']['warm_median_seconds']
        incremental = next(row for row in suite['rows'] if row['label'] == 'corrected-t-to-t1')
        summary['corrected_increment_full_row_reuse_ratio'] = incremental['cache_hits']/(incremental['cache_hits']+incremental['cache_misses'])
        summary['corrected_increment_has_no_invalidated_rows'] = incremental['invalidated_rows'] == 0
        suite.update(status='SUCCESS' if len(hashes)==1 and incremental['invalidated_rows']==0 else 'FAIL_REQUIRES_REVIEW',
            end=now(), summary=summary, current=None)
        save(); write_json(output/'benchmark-summary.json', suite)
        print(json.dumps(suite['summary'], ensure_ascii=False, indent=2), flush=True)
        return 0 if suite['status']=='SUCCESS' else 2
    except BaseException:
        suite.update(status='FAILED', end=now()); save()
        raise


def main():
    parser = argparse.ArgumentParser()
    for name in ('corrected-build','baseline-source-root','baseline-binding','output','registry'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--per-run-budget', type=float, default=900)
    parser.add_argument('--preparation-budget', type=float, default=600)
    parser.add_argument('--total-budget', type=float, default=10800)
    parser.add_argument('--old-bad-source-db')
    parser.add_argument('--old-bad-cutoff', default='2026-09-10T03:09:25.613319+00:00')
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == '__main__':
    main()
