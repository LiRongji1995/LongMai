"""One isolated TEST_FIXTURE ensure run; compare in separate supervised processes.

Only --implementation-file changes the derived store module. All other imported
source is shared and individually hashed. No real DB/provider/Strategy run.
"""
from __future__ import annotations
import argparse
import cProfile
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import traceback


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                      default=str, allow_nan=False).encode('utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def ordered_hash(rows):
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        digest.update(canonical(row) + b'\n')
        count += 1
    return {'sha256': digest.hexdigest(), 'row_count': count,
            'encoding': 'CANONICAL_JSON_UTF8_EACH_ROW_THEN_LF'}


def run(args):
    root = Path(args.source_root).resolve()
    source = root/'stock-fish'
    sys.path[:0] = [str(source), str(source/'scripts')]
    from stockfish_core.storage import SQLiteStore
    from stockfish_core.yaogu.strategy_experiments import _prepare_context
    from stockfish_core.yaogu.history_artifacts import digest_market_input, digest_json_stream
    from tests.yaogu.fixtures import make_bundle
    from r16_r1_supervise import source_metadata

    implementation = Path(args.implementation_file).resolve() if args.implementation_file else source/'stockfish_core/yaogu/derived_feature_store.py'
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('stockfish_core.yaogu._r16_r1_memory_probe_impl', implementation)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    shared = ['stockfish_core/storage.py', 'stockfish_core/yaogu/source_resolution.py',
              'stockfish_core/yaogu/history_artifacts.py', 'stockfish_core/yaogu/visibility.py',
              'stockfish_core/yaogu/contracts.py', 'stockfish_core/yaogu/strategy_experiments.py',
              'stockfish_core/yaogu/stage_aware_momentum.py', 'tests/yaogu/fixtures.py']
    report = {'probe_version': 'R16_R1_CONTROLLED_ENSURE_MEMORY_BATCH_PROBE_V1',
              'input_role': 'TEST_FIXTURE', 'fixture_function': 'tests.yaogu.fixtures.make_bundle',
              'fixture_requested_count': args.instruments, 'source_metadata': source_metadata(root),
              'implementation_file': str(implementation), 'implementation_sha256': sha(implementation),
              'shared_source_sha256': {path: sha(source/path) for path in shared},
              'script_sha256': sha(__file__), 'command_argv': sys.argv, 'python': sys.version,
              'started_at': now(), 'phase': 'FIXTURE_PREPARATION', 'status': 'RUNNING',
              'profiling': 'CPROFILE_ENSURE_ONLY' if args.cprofile else 'DISABLED',
              'measurement_policy': 'ONE_COLD_ENSURE; SUPERVISOR_PROCESS_RSS_INCLUDES_IDENTICAL_FIXTURE_PREPARATION; HASHING_AFTER_ENSURE_EXCLUDED_FROM_ENSURE_TIME',
              'comparison_scope': 'ONLY_DERIVED_STORE_IMPLEMENTATION_SWAPPED_SHARED_MODULES_HASHED_NO_WHOLE_CHECKOUT_CLAIM',
              'db': str(output/'TEST_FIXTURE.sqlite3')}
    write(output/'checkpoint.json', report)
    whole_started = time.perf_counter()
    profiler = cProfile.Profile() if args.cprofile else None
    try:
        prepare_started = time.perf_counter()
        bundle, cutoff = make_bundle(args.instruments)
        if bundle.coverage_type != 'TEST_FIXTURE':
            raise ValueError('TEST_FIXTURE_ONLY')
        ctx = _prepare_context(bundle, cutoff)
        input_hash = digest_market_input(bundle)
        store = SQLiteStore(output/'TEST_FIXTURE.sqlite3')
        derived = module.DerivedFeatureStore(store)
        report.update(cutoff=cutoff.isoformat(), input_hash=input_hash,
                      eligible_count=len(ctx.eligible_ids), session_count=len(ctx.sessions),
                      fixture_bar_count=len(bundle.bars), prepare_seconds=time.perf_counter()-prepare_started,
                      phase='ENSURE', ensure_started_at=now())
        write(output/'checkpoint.json', report)
        print(json.dumps({k: report[k] for k in ('phase','fixture_requested_count','eligible_count','fixture_bar_count')}), flush=True)
        ensure_started = time.perf_counter()
        if profiler:
            profiler.enable()
        try:
            stats = module.ensure_stage_aware_derived_features(ctx, derived, input_hash=input_hash)
        finally:
            if profiler:
                profiler.disable()
                profiler.dump_stats(str(output/'ensure.cprofile'))
        report.update(ensure_ended_at=now(), ensure_elapsed_seconds=time.perf_counter()-ensure_started,
                      phase='HASH_VERIFICATION', phase_stats=stats)
        write(output/'checkpoint.json', report)
        # Actual persisted column bytes ordered by immutable cache identity. Only
        # created_at is excluded; do not compare file/page layout or WAL size.
        with store.transaction(write=False) as con:
            columns = [row['name'] for row in con.execute('PRAGMA table_info(yaogu_derived_features_v2)') if row['name'] != 'created_at']
            names = ','.join('"'+name+'"' for name in columns)
            rows = con.execute('SELECT '+names+' FROM yaogu_derived_features_v2 ORDER BY cache_key')
            cache_proof = ordered_hash(list(row) for row in rows)
        runtime = ctx.scalar_cache[module.CONTEXT_KEY]
        runtime_proof = ordered_hash([list(key), value] for key, value in sorted(runtime.items()))
        dependencies = ctx.resolver.effective_dependencies()
        rejected = ctx.rejected_evidence
        report.update(status='PASS', phase='DONE', persisted_cache_columns=columns,
            persisted_cache_rows=cache_proof, runtime_features=runtime_proof,
            effective_dependencies_sha256=digest_json_stream(dependencies),
            effective_dependencies_counts={'sources':len(dependencies.get('sources', ())),
                                           'records':len(dependencies.get('records', ())),
                                           'immutable_history':dependencies.get('immutable_history')},
            pit_rejected_ordered=ordered_hash(rejected),
            pit_rejected_multiset=ordered_hash(sorted(rejected, key=canonical)),
            retained_canonical_views=sum(isinstance(key, tuple) and bool(key) and key[0]=='R16_CANONICAL_PRICE_SOURCE_VIEW' for key in ctx.scalar_cache),
            resolver_receipt_cache_entries=len(ctx.resolver._receipt_resolution_cache),
            limits=['SYNTHETIC_FIXTURE_NOT_REAL_MARKET', 'NO_STRATEGY_RESULT_CLAIM',
                    'RSS_AND_EXIT_CODE_OWNED_BY_OUTER_SUPERVISOR', 'COMPARE_ONLY_RUNS_WITH_IDENTICAL_PROFILE_SETTING_AND_SHARED_SOURCE_HASHES'])
    except BaseException as exc:
        report.update(status='FAIL', error_type=type(exc).__name__, traceback=traceback.format_exc())
        raise
    finally:
        report.update(ended_at=now(), total_elapsed_seconds=time.perf_counter()-whole_started)
        write(output/'result.json', report)
        write(output/'checkpoint.json', {'status':report['status'], 'phase':report['phase'],
              'result_sha256':sha(output/'result.json'), 'ended_at':report['ended_at']})
        print(json.dumps({k:report.get(k) for k in ('status','ensure_elapsed_seconds','total_elapsed_seconds','persisted_cache_rows','runtime_features','error_type')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', default=str(Path.cwd()))
    parser.add_argument('--implementation-file')
    parser.add_argument('--output', required=True)
    parser.add_argument('--instruments', type=int, default=300)
    parser.add_argument('--cprofile', action='store_true')
    args = parser.parse_args()
    if args.instruments < 1:
        parser.error('positive instrument count required')
    run(args)
