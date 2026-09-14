"""Real CLI -> Flask API -> job commit -> saved export bytes, in isolated state.

Data-ready and local recorded replay are separate modes. Preparation is measured
explicitly, never called provider time. No scheduler is constructed. External
TCP, agent scheduling and user receipt remain NOT_MEASURED.
"""
from __future__ import annotations
import argparse
from datetime import datetime, time as day_time
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from stockfish_core.api import create_app
from stockfish_core.common import utc_now
from stockfish_core.service import ApplicationService
from stockfish_core.storage import SQLiteStore, ReadOnlySQLiteStore
from stockfish_core.yaogu.e2e_tracing import DailyPrimaryTrace
from r16_r1_evidence_assets import copy_input_closure, load_bundle
from r16_r1_supervise import write_json, sha256


def isolated_backup(source, target):
    """Only clone an already isolated source; production uses explicit closure."""
    ro = ReadOnlySQLiteStore(source)
    with ro.transaction(write=False) as con:
        namespaces = {row[0] for row in con.execute('SELECT DISTINCT namespace FROM documents')}
        jobs = con.execute('SELECT count(*) FROM jobs').fetchone()[0]
        allowed = {'candidate_inputs','candidate_input_indexes','history_artifact_manifests',
                   'strategy_configs','strategy_config_activations'}
        if namespaces - allowed or jobs:
            return False
    with sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
        src.execute('PRAGMA query_only=ON')
        src.backup(dst)
    return True


def run(*, output, db=None, input_id=None, mode='data-ready', inventory=None,
        budget_seconds=600, fixture=False):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('OUTPUT_EXISTS_USE_NEW_DIRECTORY')
    output.mkdir(parents=True)
    trace = DailyPrimaryTrace(input_id=input_id, command=sys.argv,
        boundary='CLI_AFTER_ARGUMENT_PARSE_TO_REAL_FLASK_RESPONSE_BYTES', benchmark_version='R16_R1_OUTER_API')
    trace.update(execution_mode='TEST_FIXTURE' if fixture else 'E2E_OFFLINE_REPLAY' if mode == 'offline-replay' else 'E2E_DATA_READY',
        live_provider_refresh=False, actual_network_requests=0,
        transport='IN_PROCESS_FLASK_TEST_CLIENT_CALLING_UNMODIFIED_REAL_ROUTES')
    core, response = None, None
    started = time.perf_counter()
    try:
        with trace.activate():
            if mode == 'offline-replay':
                if not db or not inventory or fixture:
                    raise ValueError('OFFLINE_REPLAY_REQUIRES_REAL_SOURCE_DB_AND_INVENTORY')
                from r16_r1_build_corrected_history import build
                with trace.stage('OFFLINE_RECORDED_DELTA_BUILD', metadata={'actual_network_requests': 0}):
                    built = build(db, inventory, output/'corrected-build', budget_seconds=budget_seconds)
                db, input_id = built['target_db'], built['input_t_plus_1_corrected']
            target = output/'service.sqlite3'
            with trace.stage('ISOLATED_INPUT_PREPARATION', metadata={'purpose': 'EVIDENCE_ISOLATION; NOT_PROVIDER_REFRESH'}) as span:
                if fixture:
                    from tests.yaogu.fixtures import make_bundle
                    from r16_r1_evidence_assets import register_isolated_input
                    bundle, _ = make_bundle(7)
                    input_id = register_isolated_input(SQLiteStore(target), bundle)
                    preparation = {'mode': 'EXPLICIT_TEST_FIXTURE', 'input_id': input_id}
                else:
                    if not db or not input_id:
                        raise ValueError('EXPLICIT_DB_AND_INPUT_REQUIRED')
                    load_bundle(db, input_id)  # Actual hash/PIT history reference verification.
                    if isolated_backup(db, target):
                        preparation = {'mode': 'VERIFIED_NON_OFFICIAL_SOURCE_SQLITE_BACKUP',
                            'source_db': str(Path(db).resolve()), 'source_db_sha256': sha256(db), 'input_id': input_id}
                    else:
                        preparation = copy_input_closure(db, target, [input_id],
                            progress=lambda event: print(json.dumps(event, ensure_ascii=False), flush=True))
                preparation['target_db_sha256_after_preparation'] = sha256(target)
                write_json(output/'input-preparation.json', preparation)
                span['metadata'].update(preparation_mode=preparation.get('mode'), input_id=input_id)
            trace.update(input_id=input_id)
            with trace.stage('APPLICATION_SERVICE_INITIALIZATION', metadata={'scheduler_started': False}):
                core = ApplicationService(root=output/'state', store=SQLiteStore(target),
                    allow_test_fixture=fixture, data_dir=output/'no_model')
                app = create_app(core)
                client = app.test_client()
            accepted = client.post('/api/daily-primary/run', json={'input_id': input_id})
            write_json(output/'accepted-response.json', {'status': accepted.status_code,
                'headers': dict(accepted.headers), 'body': accepted.get_json()})
            if accepted.status_code != 202:
                raise RuntimeError('DAILY_PRIMARY_REQUEST_NOT_ACCEPTED')
            job_id = accepted.get_json()['job_id']
            with trace.stage('ASYNC_JOB_WAIT', metadata={'poll_seconds': .1}) as span:
                while True:
                    job = core.jobs.get(job_id)
                    if job['state'] in {'FAILED','CANCELLED','INTERRUPTED'}:
                        write_json(output/'job.json', job)
                        raise RuntimeError('DAILY_PRIMARY_JOB_' + job['state'])
                    if job['state'] == 'SUCCEEDED' and trace.trace['status'] == 'READY_FOR_EXPORT':
                        break
                    if time.perf_counter()-started > budget_seconds:
                        raise TimeoutError('OUTER_E2E_BUDGET_EXCEEDED')
                    time.sleep(.1)
                span['metadata']['job_id'] = job_id
            write_json(output/'job.json', job)
            run_id = job['result']['run_id']
            response = client.get('/api/strategy-experiments/' + run_id + '/export')
            if response.status_code != 200:
                raise RuntimeError('SAVED_EXPORT_FAILED')
            raw = response.get_data()
            (output/'result.json').write_bytes(raw)
            write_json(output/'result-reference.json', {'run_id': run_id, 'input_id': input_id,
                'path': str(output/'result.json'), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                'saved_business_result_hash': trace.trace.get('result_hash'),
                'persisted_db_retained': str(target), 'file_write_after_api_result_ready': True})
            report = core.candidates.daily_traces.report(trace)
            request_span = next(row for row in report['trace']['stages'] if row['name'] == 'USER_REQUEST_COMMAND_START')
            report['api_route_entry_to_response_bytes_ms'] = (
                report['trace']['end_monotonic_seconds'] - request_span['start_monotonic_seconds']) * 1000
            report['api_elapsed_policy'] = 'DIFFERENCE_OF_ACTUALLY_MEASURED_ENDPOINTS; DOES_NOT_SUM_CHILD_SPANS'
            report['command_argv'] = sys.argv
            report['input_preparation'] = preparation
            write_json(output/'trace.json', report)
            print(json.dumps({'run_id': run_id, 'input_id': input_id, 'e2e_verified': report['e2e_verified'],
                'cli_elapsed_ms': trace.trace['elapsed_ms'], 'api_elapsed_ms': report['api_route_entry_to_response_bytes_ms']}, ensure_ascii=False), flush=True)
            if not report['e2e_verified']:
                raise RuntimeError('TRACE_ARTIFACT_NOT_VERIFIED')
            return report
    except BaseException:
        trace.fail('CLI_FAILED')
        write_json(output/'failed-trace.json', trace.snapshot())
        raise
    finally:
        if core is not None:
            core.close()


def live_attempt(output, *, timeout=45):
    """One bounded real provider operation. Failure is retained, never replayed."""
    from stockfish_core.observed_data import exchange_calendar_2026
    from stockfish_core.yaogu.provider_requests import execute_request
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('LIVE_OUTPUT_EXISTS')
    output.mkdir(parents=True)
    trace = DailyPrimaryTrace(command=sys.argv, boundary='LIVE_PROVIDER_ATTEMPT_ONLY_PARTIAL')
    trace.update(execution_mode='LIVE_PROVIDER_ATTEMPT', max_http_requests=1, provider_timeout_seconds=timeout)
    result = None
    try:
        with trace.activate():
            with trace.stage('CALENDAR_TRADING_SESSION_RESOLUTION') as span:
                local = utc_now().astimezone(ZoneInfo('Asia/Shanghai'))
                days = [day for day in exchange_calendar_2026().sessions if day < local.date() or
                    day == local.date() and local.time().replace(tzinfo=None) >= day_time(15)]
                day = max(days)
                span['metadata'].update(last_closed_session=str(day), checked_at=local.isoformat())
            with trace.stage('PROVIDER_REFRESH_INCREMENTAL_INGESTION', metadata={'provider': 'baostock', 'endpoint': 'market_daily'}) as span:
                result = execute_request('market_daily', {'date': str(day)}, output/'provider',
                    timeout=timeout, max_http_requests=1)
                write_json(output/'provider-result.json', result)
                span['item_count'] = len(result.get('rows', []))
                span['metadata'].update(provider_status=result['status'], error_code=result.get('error_code'),
                    request_receipt_id=result.get('request_receipt_id'))
                if result['status'] != 'COMPLETE' or not result.get('pagination_complete'):
                    raise RuntimeError('BOUNDED_PROVIDER_REFRESH_NOT_COMPLETE')
        trace.finish()
        code = 0
    except Exception:
        trace.fail('LIVE_PROVIDER_ATTEMPT_FAILED')
        code = 2
    write_json(output/'trace.json', {'trace': trace.snapshot(), 'exit_code': code,
        'normal_incremental_live_e2e': 'NOT_VERIFIED', 'actual_provider_request_result_recorded': result is not None})
    return code


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument('--db')
    parser.add_argument('--input-id')
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--mode', choices=('data-ready','offline-replay','live-attempt'), default='data-ready')
    parser.add_argument('--inventory')
    parser.add_argument('--budget-seconds', type=float, default=600)
    parser.add_argument('--fixture', action='store_true')
    args = parser.parse_args()
    if args.mode == 'live-attempt':
        raise SystemExit(live_attempt(args.output_dir, timeout=min(args.budget_seconds,45)))
    run(output=args.output_dir, db=args.db, input_id=args.input_id, mode=args.mode,
        inventory=args.inventory, budget_seconds=args.budget_seconds, fixture=args.fixture)


if __name__ == '__main__':
    main()
