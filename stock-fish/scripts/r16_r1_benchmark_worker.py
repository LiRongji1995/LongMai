"""One controlled Python benchmark, with retained isolated cache/persistence.

The same worker drives an archived R16 implementation and final source. It does
not claim API E2E; use r16_service_path_trace for that separate measurement.
"""
from __future__ import annotations
import argparse
import cProfile
from datetime import datetime
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import pstats
import sqlite3
import subprocess
import sys
import time

from r16_r1_supervise import now, write_json, sha256, source_metadata

HARNESS_ROOT = Path(__file__).resolve().parents[2]


def footprint(path):
    path = Path(path)
    return {suffix or 'db': Path(str(path)+suffix).stat().st_size if Path(str(path)+suffix).exists() else 0
            for suffix in ('','-wal','-shm')}


def store_audit(path, output, label):
    path = Path(path).resolve()
    value = {'path': str(path), 'files_before_read': footprint(path)}
    if not path.exists():
        value['status'] = 'NOT_CREATED'
        return value
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA query_only=ON')
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        value['page_size'] = con.execute('PRAGMA page_size').fetchone()[0]
        value['page_count'] = con.execute('PRAGMA page_count').fetchone()[0]
        value['free_pages'] = con.execute('PRAGMA freelist_count').fetchone()[0]
        if 'documents' in tables:
            value['documents_by_namespace'] = [dict(row) for row in con.execute(
                'SELECT namespace,count(*) AS rows,sum(length(CAST(payload AS BLOB))) AS payload_bytes,'
                'sum(length(CAST(document_id AS BLOB))+length(CAST(content_hash AS BLOB))+length(CAST(created_at AS BLOB))) AS identity_metadata_text_bytes '
                'FROM documents GROUP BY namespace ORDER BY namespace')]
        if 'yaogu_derived_features_v2' in tables:
            value['derived_by_family'] = [dict(row) for row in con.execute(
                'SELECT feature_family,count(*) AS rows,sum(length(CAST(payload AS BLOB))) AS payload_bytes,'
                'sum(length(CAST(dependency_identity AS BLOB))) AS dependency_identity_json_bytes,'
                'sum(length(CAST(cache_key AS BLOB))+length(CAST(family_spec_id AS BLOB))+length(CAST(family_spec_hash AS BLOB))+'
                'length(CAST(instrument_id AS BLOB))+length(CAST(feature_session AS BLOB))+length(CAST(required_source_start AS BLOB))+'
                'length(CAST(required_source_end AS BLOB))+length(CAST(dependency_fingerprint AS BLOB))+length(CAST(comparable_basis_identity AS BLOB))+'
                'length(CAST(pit_policy_identity AS BLOB))+length(CAST(provider_revision_identity AS BLOB))+length(CAST(payload_hash AS BLOB))+'
                'length(CAST(created_at AS BLOB))+length(CAST(feature_family AS BLOB))) AS other_identity_metadata_text_bytes '
                'FROM yaogu_derived_features_v2 GROUP BY feature_family')]
    command = ['node', str(HARNESS_ROOT/'stock-fish/scripts/r16_r1_sqlite_pages.cjs'), str(path)]
    measured = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=90)
    (output/(label+'-dbstat.stdout.json')).write_text(measured.stdout, encoding='utf-8')
    (output/(label+'-dbstat.stderr.log')).write_text(measured.stderr, encoding='utf-8')
    value['physical_page_audit'] = {'command_argv': command, 'exit_code': measured.returncode,
        'status': 'MEASURED' if measured.returncode == 0 else 'NOT_VERIFIED',
        'report_path': str(output/(label+'-dbstat.stdout.json'))}
    value['logical_byte_policy'] = 'UTF8_FIELD_CONTENT_BYTES; PHYSICAL_PAGE_PAYLOAD_AND_RECORD_ENCODING_ARE_SEPARATE'
    value['files_after_read'] = footprint(path)
    return value


def checkpoint(path):
    before = footprint(path)
    with sqlite3.connect(path) as con:
        result = list(con.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone())
    return {'before': before, 'pragma_result_busy_log_checkpointed': result, 'after': footprint(path)}


def worker(args):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    output, implementation = Path(args.output).resolve(), Path(args.source_root).resolve()
    output.mkdir(parents=True, exist_ok=False)
    source_db, cache_db = Path(args.db).resolve(), Path(args.cache_db).resolve()
    if source_db.is_relative_to(HARNESS_ROOT/'stock-fish/data') or cache_db.is_relative_to(HARNESS_ROOT/'stock-fish/data'):
        raise ValueError('BENCHMARK_REQUIRES_ISOLATED_INPUT_AND_CACHE_PATHS')
    if not source_db.exists() or source_db == cache_db:
        raise ValueError('EXISTING_SOURCE_AND_SEPARATE_CACHE_REQUIRED')
    binding = json.loads(Path(args.source_binding).read_text(encoding='utf-8')) if args.source_binding else None
    if implementation != HARNESS_ROOT:
        if binding is None or Path(binding['source_root']).resolve() != implementation:
            raise ValueError('ARCHIVED_SOURCE_BINDING_REQUIRED')
        if not binding.get('files') or binding.get('verification') != 'RECREATED_GIT_ARCHIVE_EXACT_BYTES_AND_EVERY_EXTRACTED_FILE':
            raise ValueError('COMPLETE_VERIFIED_ARCHIVE_BINDING_REQUIRED')
        for relative, wanted in binding['files'].items():
            if sha256(implementation/relative) != wanted:
                raise ValueError('ARCHIVED_SOURCE_BYTES_CHANGED:'+relative)
    sys.path.insert(0, str(implementation/'stock-fish'))
    from stockfish_core import storage
    from stockfish_core.yaogu.strategy_experiments import run_active_daily_strategy
    from stockfish_core.yaogu.derived_feature_store import DerivedFeatureStore
    from stockfish_core.yaogu.contracts import MarketInput
    from stockfish_core.yaogu.config import StrategyConfig
    from stockfish_core.yaogu.history_artifacts import bind_history, digest_json_stream
    from stockfish_core.service import ApplicationService
    # Tracing uses the final measurement code for both implementations. The
    # archived algorithm does not emit nested cache spans; those remain unmeasured.
    trace_spec = importlib.util.spec_from_file_location('stockfish_core.yaogu._r16_benchmark_trace',
        HARNESS_ROOT/'stock-fish/stockfish_core/yaogu/e2e_tracing.py')
    trace_module = importlib.util.module_from_spec(trace_spec); trace_spec.loader.exec_module(trace_module)
    if implementation == HARNESS_ROOT:
        from stockfish_core.yaogu import e2e_tracing as trace_module
    before_cache = store_audit(cache_db, output, 'cache-before')
    before_source = footprint(source_db)
    trace = trace_module.DailyPrimaryTrace(input_id=args.input_id, command=sys.argv, boundary='INTERNAL_ONLY')
    trace.update(algorithm_source=source_metadata(implementation), archived_source_binding=binding,
                 measurement_source=source_metadata(HARNESS_ROOT))
    core, result = None, None
    profiler = cProfile.Profile() if args.cprofile else None
    started, t0 = now(), time.perf_counter()
    if profiler:
        profiler.enable()
    try:
        with trace.activate():
            with trace.stage('MARKET_INPUT_CREATION_OR_LOAD') as span:
                ro_class = getattr(storage, 'ReadOnlySQLiteStore', None)
                source_store = (ro_class or storage.SQLiteStore)(source_db)
                raw = source_store.get_document_bytes('candidate_inputs', args.input_id, expected_hash=args.input_id.removeprefix('market_'))
                bundle = bind_history(MarketInput.model_validate_json(raw), source_store)
                span['bytes_read'] = len(raw)
                span['metadata']['source_access'] = 'READ_ONLY_URI' if ro_class else 'LEGACY_INIT_ON_ISOLATED_COPY_ONLY'
                configs = source_store.list_documents('strategy_configs', limit=1000)
                activations = source_store.list_documents('strategy_config_activations', limit=1000)
                wanted = activations[-1]['payload']['config_hash'] if activations else configs[-1]['payload']['config_hash']
                config = next(StrategyConfig.model_validate(row['payload']['config']) for row in configs if row['payload']['config_hash'] == wanted)
                assert config.config_hash == wanted
            with trace.stage('CACHE_STORE_INITIALIZATION'):
                cache = DerivedFeatureStore(storage.SQLiteStore(cache_db))
            with trace.stage('STRATEGY_COMPUTE'):
                result = run_active_daily_strategy(bundle, cutoff=datetime.fromisoformat(args.cutoff),
                    config=config, input_id=args.input_id, derived_store=cache, execution_profile='DAILY_PRIMARY_FAST')
            stage = result['results']['STAGE_AWARE_MOMENTUM_V2']
            business = {'counts': stage.get('counts'), 'top_ids': stage.get('top_ids'), 'items': stage.get('items')}
            with trace.stage('BUSINESS_RESULT_SERIALIZATION') as span:
                business_hash = digest_json_stream(business)
                with (output/'business-result.json').open('w', encoding='utf-8', newline='\n') as stream:
                    json.dump(business, stream, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str, allow_nan=False)
                span['bytes_written'] = (output/'business-result.json').stat().st_size
            with trace.stage('PERSISTENCE_SERVICE_INITIALIZATION'):
                core = ApplicationService(root=output/'state', store=storage.SQLiteStore(output/'persistence.sqlite3'),
                    allow_test_fixture=bundle.coverage_type == 'TEST_FIXTURE', data_dir=output/'no_model')
            with trace.stage('PERSISTENCE', metadata={'includes_transaction_commit': True}):
                with core.store.transaction() as con:
                    core.candidates._write_strategy_result(con, result, created_at=args.cutoff)
            with trace.stage('SAVED_RUN_HEADER_VERIFICATION') as span:
                saved = core.store.get_document_bytes('strategy_experiment_runs', result['run_id'])
                span['bytes_read'] = len(saved)
            elapsed = time.perf_counter()-t0
            workload_end = now()
            workload_end_perf = time.perf_counter()
        trace.finish(run_id=result['run_id'], result_hash=business_hash)
    except BaseException:
        trace.fail('BENCHMARK_WORKLOAD_FAILED')
        write_json(output/'failed-trace.json', trace.snapshot())
        raise
    finally:
        if profiler:
            profiler.disable(); profiler.dump_stats(str(output/'profile.cprofile'))
            with (output/'profile-top.txt').open('w', encoding='utf-8') as stream:
                pstats.Stats(profiler, stream=stream).sort_stats('cumulative').print_stats(100)
        if core is not None:
            core.close()
    # Auditing/checkpointing is outside timed workload and remains fully recorded.
    after_cache = store_audit(cache_db, output, 'cache-after-before-checkpoint')
    persistence = store_audit(output/'persistence.sqlite3', output, 'persistence-before-checkpoint')
    checkpoints = {'cache': checkpoint(cache_db), 'persistence': checkpoint(output/'persistence.sqlite3')}
    algorithms = {}
    for name, module in sorted(sys.modules.items()):
        file = getattr(module, '__file__', None)
        if name.startswith('stockfish_core') and file and Path(file).suffix == '.py':
            resolved = Path(file).resolve()
            if name == 'stockfish_core.yaogu._r16_benchmark_trace':
                continue  # explicitly separate measurement code
            if not resolved.is_relative_to(implementation/'stock-fish'):
                raise ValueError('ALGORITHM_IMPORTED_OUTSIDE_BOUND_IMPLEMENTATION:'+name)
            if binding is not None and binding['files'].get(resolved.relative_to(implementation).as_posix()) != sha256(resolved):
                raise ValueError('LOADED_ALGORITHM_MISSING_FROM_BINDING:'+name)
            algorithms[name] = {'path': str(Path(file).resolve()), 'sha256': sha256(file)}
    report = {'benchmark_version': 'R16_R1_CONTROLLED_PYTHON_WORKER_V1', 'label': args.label,
        'start': started, 'end': workload_end, 'postprocessing_end': now(),
        'elapsed_seconds': elapsed, 'command_argv': sys.argv,
        'workload_perf_counter_start': t0, 'workload_perf_counter_end': workload_end_perf,
        'root_trace_scope': 'INCLUDES_SOURCE_IDENTITY_PREPARATION; USE_WORKLOAD_SPANS_FOR_CONTROLLED_TIMING',
        'algorithm_source': source_metadata(implementation), 'archived_source_binding': binding,
        'measurement_source': source_metadata(HARNESS_ROOT), 'loaded_source_modules': algorithms,
        'input_id': args.input_id, 'input_payload_sha256': hashlib.sha256(raw).hexdigest(),
        'history_artifact_ref': bundle.history_artifact_ref, 'cutoff': args.cutoff, 'config_hash': config.config_hash,
        'observation_date': str(bundle.observation_date), 'coverage_type': bundle.coverage_type,
        'profile_mode': 'CPROFILE' if profiler else 'UNPROFILED', 'trace': trace.snapshot(),
        'business_result_hash': business_hash, 'business_result_file_sha256': sha256(output/'business-result.json'),
        'business_result_bytes': (output/'business-result.json').stat().st_size,
        'run_id': result['run_id'], 'saved_run_content_hash': result['content_hash'], 'main_run_document_bytes': len(saved),
        'derived_feature_store': result.get('derived_feature_store'), 'stage_aware_counts': stage.get('counts'),
        'data_readiness': result.get('data_readiness'), 'data_coverage_diagnostics': result.get('data_coverage_diagnostics'),
        'source_files_before': before_source, 'source_files_after': footprint(source_db),
        'cache_before': before_cache, 'cache_after_before_checkpoint': after_cache,
        'persistence_before_checkpoint': persistence, 'checkpoints': checkpoints,
        'status': 'SUCCESS', 'official_records_written': False, 'market_edge': 'NOT_ESTABLISHED',
        'boundary': 'ISOLATED_INPUT_LOAD_THROUGH_STRATEGY_BUSINESS_SERIALIZATION_AND_DOMAIN_COMMIT; NOT_API_E2E',
        'audit_and_checkpoint_outside_elapsed': True}
    write_json(output/'benchmark.json', report)
    print(json.dumps({'label': args.label, 'elapsed_seconds': elapsed, 'business_hash': business_hash,
        'cache_hits': result['derived_feature_store']['cache_hits'], 'built_rows': result['derived_feature_store']['built_rows']}, ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser()
    for option in ('db','input-id','cutoff','cache-db','output','label'):
        parser.add_argument('--'+option, required=True)
    parser.add_argument('--source-root', default=str(HARNESS_ROOT))
    parser.add_argument('--source-binding')
    parser.add_argument('--cprofile', action='store_true')
    worker(parser.parse_args())


if __name__ == '__main__':
    main()
