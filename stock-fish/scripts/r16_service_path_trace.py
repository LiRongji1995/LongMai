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
import os
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


def last_closed_session(now):
    from stockfish_core.observed_data import exchange_calendar_2026
    local = now.astimezone(ZoneInfo('Asia/Shanghai'))
    days = [day for day in exchange_calendar_2026().sessions if day < local.date() or
            day == local.date() and local.time().replace(tzinfo=None) >= day_time(15)]
    if not days:
        raise ValueError('REGISTERED_LAST_CLOSED_SESSION_UNAVAILABLE')
    return max(days)


def validate_live_daily_capture(result, archive, day, cutoff):
    """Bind real request result to its immutable raw payload and receipt."""
    from stockfish_core.yaogu.provider_normalization import _iid
    if (result.get('provider') != 'baostock' or result.get('endpoint') != 'market_daily' or
            result.get('parameters') != {'date':str(day)} or result.get('status') != 'COMPLETE' or
            result.get('pagination_complete') is not True or not result.get('rows')):
        raise ValueError('LIVE_SINGLE_SESSION_COMPLETE_MARKET_DAILY_REQUIRED')
    rid = result.get('request_receipt_id')
    matches = [r for r in result.get('receipts',()) if r.get('receipt_id') == rid]
    if len(matches) != 1:
        raise ValueError('LIVE_REQUEST_RECEIPT_NOT_UNIQUE')
    receipt = matches[0]
    expected_id = hashlib.sha256(json.dumps({k:v for k,v in receipt.items() if k!='receipt_id'},sort_keys=True).encode()).hexdigest()
    if expected_id != rid:
        raise ValueError('LIVE_REQUEST_RECEIPT_IDENTITY_MISMATCH')
    for field in ('first_seen_at','ingested_at'):
        stamp = datetime.fromisoformat(receipt[field])
        if stamp.tzinfo is None or stamp > cutoff:
            raise ValueError('LIVE_RECEIPT_NOT_VISIBLE_AT_CUTOFF')
    if any(receipt.get(k) != result.get(v) for k,v in (
            ('provider','provider'),('dataset','endpoint'),('parameters','parameters'),
            ('field_version','field_version'),('status','status'),('pagination_complete','pagination_complete'))):
        raise ValueError('LIVE_RECEIPT_RESULT_METADATA_MISMATCH')
    archive = Path(archive)
    raw_path = archive/'raw'/(receipt['content_hash']+'.raw.json')
    receipt_path = archive/'raw'/(rid+'.receipt.json')
    raw = raw_path.read_bytes()
    payload = json.loads(raw)
    if hashlib.sha256(raw).hexdigest() != receipt['content_hash'] or json.loads(receipt_path.read_bytes()) != receipt:
        raise ValueError('LIVE_ARCHIVE_CONTENT_MISMATCH')
    if any(payload.get(k) != result.get(k) for k in ('rows','status','pagination_complete','field_version','coverage')):
        raise ValueError('LIVE_ARCHIVE_RESULT_PAYLOAD_MISMATCH')
    seen = set()
    for row in result['rows']:
        iid = _iid(row.get('code'))
        if not iid or row.get('date') != str(day) or str(row.get('adjustflag')) != '3':
            raise ValueError('LIVE_RAW_IDENTITY_SESSION_OR_BASIS_MISMATCH')
        key = (iid,str(day))
        if key in seen:
            raise ValueError('LIVE_DUPLICATE_PRICE_KEY')
        seen.add(key)
    return {'receipt_id':rid,'row_count':len(seen),'raw_path':str(raw_path.resolve()),
        'raw_sha256':receipt['content_hash'],'receipt_path':str(receipt_path.resolve()),
        'receipt_file_sha256':sha256(receipt_path),'first_seen_at':receipt['first_seen_at'],
        'ingested_at':receipt['ingested_at'],'status':'VERIFIED_COMPLETE_SINGLE_SESSION_CAPTURE'}


def build_live_incremental(target, base_input_id, inventory_path, output, trace, *, started, budget_seconds, fixture=False):
    """One real market_daily call; retained identity stays outside new-day proof."""
    from stockfish_core.observed_data import exchange_calendar_2026
    from stockfish_core.yaogu.provider_requests import execute_request
    from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report
    from stockfish_core.yaogu.history_artifacts import HistoryArtifactStore
    from stockfish_core.yaogu.history_base_selection import validate_history_base
    from r16_r1_evidence_assets import load_inventory_results, register_isolated_input
    output = Path(output)
    last_print = [0.0]
    def progress(event):
        elapsed = time.perf_counter()-started
        if elapsed > budget_seconds:
            raise TimeoutError('LIVE_INCREMENTAL_E2E_BUDGET_EXCEEDED')
        value = {**event,'elapsed_seconds':elapsed,'budget_seconds':budget_seconds,
                 'recorded_at':utc_now().isoformat(),'process_id':os.getpid(),'eta_seconds':None}
        write_json(output/'live-checkpoint.json',value)
        if elapsed-last_print[0] >= 10 or event.get('stage','').endswith(('START','DONE')):
            print(json.dumps(value,ensure_ascii=False),flush=True)
            last_print[0] = elapsed
    store = SQLiteStore(target)
    base = load_bundle(store,base_input_id)
    if (base.coverage_type == 'TEST_FIXTURE') != bool(fixture):
        raise ValueError('LIVE_MODE_AND_EXPLICIT_TEST_FIXTURE_SCOPE_MUST_NOT_MIX')
    if not fixture and base.coverage_type not in {'PROSPECTIVE_CAPTURED','RECONSTRUCTED_HISTORICAL'}:
        raise ValueError('LIVE_BASE_COVERAGE_NOT_VERIFIABLE')
    with trace.stage('CALENDAR_TRADING_SESSION_RESOLUTION',metadata={'phase':'LIVE_INCREMENTAL_TARGET'}) as span:
        checked_at = utc_now()
        day = last_closed_session(checked_at)
        registered = set(exchange_calendar_2026().sessions)
        if any(s.year==2026 and s not in registered for s in base.sessions):
            raise ValueError('BASE_CALENDAR_CONFLICTS_WITH_REGISTERED_2026_CALENDAR')
        if not base.observation_date < day:
            raise ValueError('LIVE_TARGET_MUST_ADVANCE_EXPLICIT_BASE_SESSION')
        sessions = tuple(sorted(set(base.sessions) | {s for s in registered if base.observation_date < s <= day}))
        calendar_file = ROOT/'stockfish_core/exchange_calendar_2026.json'
        calendar = {'registered_file':str(calendar_file),'sha256':sha256(calendar_file),
            'base_session_count':len(base.sessions),'target_session_count':len(sessions),
            'new_sessions':[str(s) for s in sessions if s > base.observation_date],
            'missing_intervening_price_sessions':[str(s) for s in sessions if base.observation_date < s < day],
            'checked_at':checked_at.isoformat(),'last_closed_session':str(day)}
        span['metadata'].update(calendar)
    progress({'stage':'LIVE_SOURCE_PREPARATION_START'})
    inventory = json.loads(Path(inventory_path).read_bytes())
    retained_datasets = {'mainboard_sse','master_szse','delisting_sse','stock_basic','stock_basic_all',
                         'tushare_stock_basic','adjustment_factors'}
    retained_ids = [r.receipt_id for r in base.receipts if r.dataset in retained_datasets]
    retained, assets = load_inventory_results(inventory,retained_ids,progress=progress)
    # Only retained source material and the one actual new market response enter
    # the normalizer. No RequestQueue, historical request plan or scheduler.
    archive = output/'provider'
    with trace.stage('PROVIDER_REFRESH_INCREMENTAL_INGESTION',metadata={
            'phase':'LIVE_INCREMENTAL_ACTUAL_REQUEST','provider':'baostock','endpoint':'market_daily',
            'parameters':{'date':str(day)},'max_http_requests':1,'timeout_seconds':45}) as span:
        progress({'stage':'LIVE_PROVIDER_REQUEST_START'})
        trace.update(live_provider_refresh=True,provider_request_invocations=1,
            actual_network_requests=None,actual_http_attempts=None,actual_sdk_operations=None,
            network_count_policy='TOTAL_UNDERLYING_NETWORK_REQUESTS_NOT_MEASURED; HTTP_ATTEMPTS_AND_SDK_OPERATIONS_HAVE_SEPARATE_SCOPES')
        captured = execute_request('market_daily',{'date':str(day)},archive,timeout=45,max_http_requests=1)
        write_json(output/'provider-result.json',captured)
        counters = captured.get('coverage',{})
        trace.update(actual_http_attempts=counters.get('http_attempts'),actual_sdk_operations=counters.get('sdk_operations'))
        span['item_count'] = len(captured.get('rows',()))
        span['metadata'].update(provider_status=captured.get('status'),error_code=captured.get('error_code'),
            request_receipt_id=captured.get('request_receipt_id'),coverage=captured.get('coverage',{}) | {'returned_codes':'OMITTED_FROM_TRACE_USE_RAW_RESULT'})
        cutoff = utc_now()
        captured_proof = validate_live_daily_capture(captured,archive,day,cutoff)
    counters = captured.get('coverage',{})
    progress({'stage':'LIVE_PROVIDER_REQUEST_DONE','receipt_id':captured['request_receipt_id']})
    with trace.stage('RECORDED_SOURCE_VALIDATION',metadata={'phase':'LIVE_BASE_AND_INCOMING_SCHEMA'}):
        proof = validate_history_base(base,HistoryArtifactStore(store),observation_date=day,cutoff=cutoff,
            allowed_price_providers={'baostock','tushare'},incoming_price_receipts=retained+[captured])
    progress({'stage':'LIVE_NORMALIZATION_START'})
    bundle, normalization = normalize_capture_with_report(retained+[captured],day,cutoff,sessions,
        history_store=HistoryArtifactStore(store),history_base_ref=base.history_artifact_ref,
        coverage_type='TEST_FIXTURE' if fixture else 'RECONSTRUCTED_HISTORICAL',progress=progress,retained_identity_base=base,
        review_factor_conflicts=True,factor_review_base=base,
        factor_review_limits={'max_instruments':1,'max_rows':1000,'budget_seconds':180})
    current_bars = [bar for bar in bundle.bars if bar.session==day]
    if not current_bars or any(captured['request_receipt_id'] not in bar.source_refs for bar in current_bars):
        raise ValueError('LIVE_CAPTURE_NOT_BOUND_TO_NORMALIZED_CURRENT_FACTS')
    from stockfish_core.yaogu.provider_normalization import _iid
    raw_ids = {_iid(row['code']) for row in captured['rows']}
    normalized_ids = {bar.instrument_id for bar in current_bars}
    new_input = register_isolated_input(store,bundle,metadata={'parent_input_id':base_input_id,
        'ingestion_mode':'ONE_LIVE_MARKET_DAILY_WITH_RETAINED_REFERENCE_IDENTITY',
        'identity_scope_policy':'PRIOR_INPUT_NOT_NEW_SESSION_IDENTITY_PROOF'})
    after = HistoryArtifactStore(store).manifest(bundle.history_artifact_ref,bundle.history_artifact_hash)
    before = HistoryArtifactStore(store).manifest(base.history_artifact_ref,base.history_artifact_hash)
    old_parts = {p['session']:p for p in before['partitions']}
    new_parts = {p['session']:p for p in after['partitions']}
    if any(new_parts.get(s) != p for s,p in old_parts.items()):
        raise ValueError('LIVE_DELTA_CHANGED_PRIOR_HISTORY_PARTITIONS')
    manifest = {'execution_mode':'TEST_FIXTURE' if fixture else 'E2E_LIVE_INCREMENTAL','source_input_id':base_input_id,'input_id':new_input,
        'base_history_artifact_ref':base.history_artifact_ref,'history_artifact_ref':bundle.history_artifact_ref,
        'history_artifact_hash':bundle.history_artifact_hash,'base_validation':proof,
        'prior_partitions_unchanged':True,'new_partition_sessions':sorted(set(new_parts)-set(old_parts)),
        'calendar':calendar,'live_capture':captured_proof,'retained_assets':assets,
        'inventory_sha256':sha256(inventory_path),'actual_normalization_cutoff':cutoff.isoformat(),
        'coverage_type':bundle.coverage_type,'provider_request_invocations':1,
        'normalized_current_bars':len(current_bars),'provider_raw_instruments':len(raw_ids),
        'unmapped_raw_identity_ids':sorted(raw_ids-normalized_ids),
        'raw_scope_policy':'COMPLETE_PROVIDER_RESPONSE_RETAINED; NORMALIZER_REQUIRES_SOURCE_BACKED_IDENTITY; NO_TICKER_PREFIX_INFERENCE',
        'http_attempts':counters.get('http_attempts'),'sdk_operations':counters.get('sdk_operations'),
        'qualification_policy':'UNKNOWN_WHEN_RETAINED_IDENTITY_LACKS_NEW_SESSION_PROOF',
        'official_activation_requested':False,'normalization_report':'live-normalization-report.json'}
    write_json(output/'live-normalization-report.json',normalization)
    write_json(output/'live-incremental-build.json',manifest)
    progress({'stage':'LIVE_NORMALIZATION_DONE','input_id':new_input,
              'new_rows':normalization.get('history_artifact',{}).get('new_rows')})
    return new_input,manifest


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


def live_incremental_summary(build, job, report):
    trace = report['trace']
    sources = [s for s in trace['stages'] if s['name']=='SOURCE_RESOLUTION' and s['status']=='SUCCESS']
    verified = (report.get('e2e_verified') is True and trace.get('status')=='SUCCESS'
                and trace.get('business_job_state')=='SUCCEEDED' and job.get('state')=='SUCCEEDED')
    return {**build,'eligible':job['result'].get('stage_aware_counts',{}).get('eligible'),
        'nonempty_feature_path':'EXERCISED' if any((s.get('item_count') or 0)>0 for s in sources) else 'NOT_EXERCISED_NO_ELIGIBLE_FACT_POPULATION',
        'normal_incremental_live_e2e':'VERIFIED_ENGINEERING_PATH_WITH_EXPLICIT_DATA_GAPS' if verified else 'FAILED_TRACE_VALIDATION',
        'data_readiness_not_inferred_from_success':True}


def run(*, output, db=None, input_id=None, mode='data-ready', inventory=None,
        budget_seconds=600, fixture=False):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('OUTPUT_EXISTS_USE_NEW_DIRECTORY')
    output.mkdir(parents=True)
    trace = DailyPrimaryTrace(input_id=input_id, command=sys.argv,
        boundary='CLI_AFTER_ARGUMENT_PARSE_TO_REAL_FLASK_RESPONSE_BYTES', benchmark_version='R16_R1_OUTER_API')
    trace.update(execution_mode='TEST_FIXTURE' if fixture else 'E2E_LIVE_INCREMENTAL' if mode == 'live-incremental' else 'E2E_OFFLINE_REPLAY' if mode == 'offline-replay' else 'E2E_DATA_READY',
        live_provider_refresh=False, actual_network_requests=0,
        transport='IN_PROCESS_FLASK_TEST_CLIENT_CALLING_UNMODIFIED_REAL_ROUTES')
    core, response = None, None
    started = time.perf_counter()
    try:
        with trace.activate():
            if mode == 'live-incremental' and (not db or not input_id or not inventory or fixture):
                raise ValueError('LIVE_INCREMENTAL_REQUIRES_REAL_BASE_DB_INPUT_AND_INVENTORY')
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
                        if mode == 'live-incremental':
                            raise ValueError('LIVE_INCREMENTAL_REQUIRES_VERIFIED_NON_OFFICIAL_SOURCE_ONLY')
                        preparation = copy_input_closure(db, target, [input_id],
                            progress=lambda event: print(json.dumps(event, ensure_ascii=False), flush=True))
                preparation['target_db_sha256_after_preparation'] = sha256(target)
                write_json(output/'input-preparation.json', preparation)
                span['metadata'].update(preparation_mode=preparation.get('mode'), input_id=input_id)
            live_build = None
            if mode == 'live-incremental':
                # Do not leave the offline zero-network label on a live attempt.
                trace.update(actual_network_requests=None,
                    network_count_policy='TOTAL_UNDERLYING_NETWORK_REQUESTS_NOT_MEASURED; HTTP_AND_SDK_COUNTS_RECORDED_AFTER_REQUEST')
                input_id,live_build = build_live_incremental(target,input_id,inventory,output,trace,
                    started=started,budget_seconds=budget_seconds)
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
            if live_build is not None:
                report['live_incremental'] = live_incremental_summary(live_build,job,report)
            write_json(output/'trace.json', report)
            print(json.dumps({'run_id': run_id, 'input_id': input_id, 'e2e_verified': report['e2e_verified'],
                'cli_elapsed_ms': trace.trace['elapsed_ms'], 'api_elapsed_ms': report['api_route_entry_to_response_bytes_ms']}, ensure_ascii=False), flush=True)
            if not report['e2e_verified'] or report.get('live_incremental',{}).get('normal_incremental_live_e2e')=='FAILED_TRACE_VALIDATION':
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
    parser.add_argument('--mode', choices=('data-ready','offline-replay','live-attempt','live-incremental'), default='data-ready')
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
