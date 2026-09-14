"""Reconstruct an isolated R16-R1 delta through the real provider normalizer.

No provider calls are made. All original result files and receipt clocks are
verified and preserved. Failed captures are retained as failed receipts, never
as empty observations or retractions. Source and target databases must differ.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from r16_r1_evidence_assets import (ReadOnlySQLiteStore, SQLiteStore, HistoryArtifactStore,
    load_bundle, copy_input_closure, load_inventory_results, register_isolated_input)
from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report
from stockfish_core.yaogu.history_base_selection import validate_history_base, PRICE_DATASETS

DEFAULT_T = 'market_63f83798f5098898debce30e0810a0c54f6a0daffdc75ac6404d47d5f8488a23'
DEFAULT_TP1 = 'market_04d95df6f6f699c7f88353d181308159526865877a666a7c3efb84be01b9c08d'


def git_metadata():
    root = Path(__file__).resolve().parents[2]
    def run(*args):
        return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
    return {'commit':run('rev-parse','HEAD'),'tree':run('rev-parse','HEAD^{tree}'),
            'dirty':bool(run('status','--short')),'branch':run('branch','--show-current')}


class BuildProgress:
    def __init__(self, output, budget_seconds):
        self.output = Path(output)
        self.started = time.monotonic()
        self.budget = budget_seconds
        self.last_print = 0

    def __call__(self, event):
        elapsed = time.monotonic()-self.started
        if elapsed > self.budget:
            raise TimeoutError('CORRECTED_HISTORY_BUILD_BUDGET_EXCEEDED')
        try:
            import psutil
            rss = psutil.Process().memory_info().rss
        except ImportError:
            rss = None
        value = {**event,'elapsed_seconds':elapsed,'rss_bytes':rss,'eta_seconds':None,
                 'process_id':os.getpid(),'recorded_at':datetime.now(timezone.utc).isoformat()}
        (self.output/'checkpoint.json').write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        if elapsed-self.last_print >= 10 or event.get('stage','').endswith(('START','DONE','FAILED')):
            print(json.dumps(value,ensure_ascii=False),flush=True)
            self.last_print = elapsed


def selected_delta_results(inventory, base_bundle, next_bundle, *, progress, current_session_only=False):
    old = {r.receipt_id:r for r in base_bundle.receipts}
    kept, skipped, price_delta = [], [], []
    for receipt in next_bundle.receipts:
        if receipt.dataset == 'market_daily' and receipt.receipt_id in old:
            if receipt != old[receipt.receipt_id]:
                raise ValueError('UNCHANGED_RECEIPT_METADATA_CONFLICT')
            skipped.append({'receipt_id':receipt.receipt_id,'reason':'EXACT_RECEIPT_ALREADY_IN_VERIFIED_BASE',
                            'status':receipt.status,'content_hash':receipt.content_hash})
            continue
        if receipt.dataset == 'market_daily':
            session = receipt.parameters.get('date')
            usable = receipt.status in {'COMPLETE','PARTIAL'}
            category = ('FAILED_CAPTURE_NO_OBSERVATION_NOT_EMPTY_NOT_RETRACTION' if not usable else
                        'APPENDED_NEW_SESSION' if session > str(base_bundle.observation_date) else
                        'HISTORICAL_SOURCE_ADDITION_OR_REVISION_REQUIRES_AUDIT')
            price_delta.append({'receipt_id':receipt.receipt_id,'session':session,'status':receipt.status,
                                'row_count':receipt.row_count,'classification':category})
            if current_session_only and usable and session != str(next_bundle.observation_date):
                skipped.append({'receipt_id':receipt.receipt_id,'reason':'EXPLICIT_SINGLE_SESSION_SMOKE_SCOPE'})
                continue
        kept.append(receipt.receipt_id)
    kept.extend(r.receipt_id for r in base_bundle.receipts if r.dataset == 'adjustment_factors')
    results,assets = load_inventory_results(inventory,kept,progress=progress)
    return results,{'selected_assets':assets,'skipped':skipped,'price_receipt_delta':price_delta,
                    'price_delta_classification_counts':dict(Counter(r['classification'] for r in price_delta)),
                    'policy':'DELTA_INGEST_EXACT_SHARED_RAW_RECEIPTS_RETAINED_IN_BASE_ALL_OTHER_ORIGINAL_RECEIPTS_PRESERVED'}


def base_current_price_receipt_ids(base):
    ids = [r.receipt_id for r in base.receipts if r.dataset=='market_daily'
           and r.parameters.get('date')==str(base.observation_date)
           and r.status=='COMPLETE' and r.pagination_complete and r.row_count>0]
    if not ids:
        raise ValueError('COMPLETE_ORIGINAL_T_CURRENT_PRICE_CAPTURE_REQUIRED_FOR_DATED_ELIGIBILITY')
    return ids


def preserve_t_envelope_with_normalized_history(base,normalized_history):
    """Augment real normalized history while preserving the immutable T facts.

    Regenerating T prices merely to rebuild security flags can introduce a new
    factor reference at unchanged revision clocks. Keep the already verified T
    envelope, current bars and universe byte-for-byte instead of creating that
    ambiguous recapture. Only genuinely ingested historical rows are new.
    """
    if not normalized_history.history_artifact_ref:
        raise ValueError('NORMALIZED_HISTORY_REFERENCE_REQUIRED')
    receipts = {r.receipt_id:r for r in base.receipts}
    for receipt in normalized_history.receipts:
        if receipt.receipt_id in receipts and receipts[receipt.receipt_id]!=receipt:
            raise ValueError('CORRECTED_T_RECEIPT_IDENTITY_CONFLICT')
        receipts[receipt.receipt_id]=receipt
    corrected = base.model_copy(update={
        'history_artifact_ref':normalized_history.history_artifact_ref,
        'history_artifact_hash':normalized_history.history_artifact_hash,
        'receipts':tuple(receipts.values()),'coverage_type':'RECONSTRUCTED_HISTORICAL'})
    corrected._history_store = normalized_history._history_store
    return corrected


def verify_corrected_t_eligibility(base,corrected,cutoff):
    from stockfish_core.yaogu.strategy_experiments import _prepare_context
    from stockfish_core.yaogu.source_resolution import canonical_price_source_view,source_token
    original = _prepare_context(base,cutoff)
    repaired = _prepare_context(corrected,cutoff)
    ids = sorted(original.eligible_ids)
    if not ids or set(ids)!=set(repaired.eligible_ids):
        raise ValueError('CORRECTED_T_ELIGIBILITY_DIFFERS_FROM_ORIGINAL_DATED_INPUT')
    fields = ('st','suspended','delisting_period','exchange','board','security_type')
    mismatches = [iid for iid in ids if any(getattr(original.securities[iid],key)!=getattr(repaired.securities[iid],key) for key in fields)]
    day = str(base.observation_date)
    left = canonical_price_source_view(original,[day],ids)
    right = canonical_price_source_view(repaired,[day],ids)
    raw_fields = ('open','high','low','close','reference_prev_close','volume_shares','amount_cny','turnover_fraction')
    for iid in ids:
        old = source_token(left,iid,day); new = source_token(right,iid,day)
        if old['status'] != new['status'] or any(old.get('values',{}).get(key)!=new.get('values',{}).get(key) for key in raw_fields):
            mismatches.append(iid)
    if mismatches:
        raise ValueError('CORRECTED_T_DATED_FLAGS_OR_RAW_VALUES_CHANGED:'+','.join(sorted(set(mismatches))[:16]))
    return {'status':'PASS','original_eligible':len(ids),'corrected_eligible':len(repaired.eligible_ids),
            'checked_instruments':len(ids),'checked_flags':fields,'checked_raw_fields':raw_fields,
            'day':day,'cutoff':cutoff.isoformat(),
            'provenance_policy':'ORIGINAL_T_CURRENT_RAW_AND_FLAGS_PRESERVED_WITH_ORIGINAL_PROVENANCE'}


def build(source_db, inventory_path, output, *, base_input_id=DEFAULT_T,next_input_id=DEFAULT_TP1,
          budget_seconds=600,resume=False,current_session_only=False,progress_callback=None):
    output = Path(output).resolve()
    source_db = Path(source_db).resolve()
    if output == source_db.parent or output.is_relative_to(source_db.parent):
        raise ValueError('OUTPUT_MUST_BE_SEPARATE_FROM_PRODUCTION_DATA_DIRECTORY')
    if output.exists() and not resume:
        raise ValueError('OUTPUT_EXISTS_USE_NEW_DIRECTORY_OR_EXPLICIT_RESUME')
    output.mkdir(parents=True,exist_ok=True)
    inventory_bytes = Path(inventory_path).read_bytes()
    inventory = json.loads(inventory_bytes)
    identity = {'source_db':str(source_db),'input_t':base_input_id,'input_t_plus_1_old':next_input_id,
                'inventory_sha256':hashlib.sha256(inventory_bytes).hexdigest(),'current_session_only':current_session_only}
    registry_path = output/'run_registry.json'
    if resume and any(output.iterdir()) and not registry_path.exists():
        raise ValueError('UNREGISTERED_EXISTING_OUTPUT_CANNOT_RESUME')
    if registry_path.exists():
        previous = json.loads(registry_path.read_text(encoding='utf-8'))
        if previous['identity'] != identity:
            raise ValueError('RESUME_IDENTITY_MISMATCH')
        try:
            import psutil
            if previous['state']=='RUNNING' and psutil.pid_exists(previous['pid']) and previous['pid'] != os.getpid():
                process = psutil.Process(previous['pid'])
                if abs(process.create_time()-previous['process_create_time']) < 1:
                    raise ValueError('MATCHING_BUILD_PROCESS_ALREADY_RUNNING')
        except ImportError:
            if previous['state']=='RUNNING':
                raise ValueError('CANNOT_PROVE_PREVIOUS_BUILD_PROCESS_STOPPED')
    import psutil
    registry = {'identity':identity,'state':'RUNNING','pid':os.getpid(),
                'process_create_time':psutil.Process().create_time(),'command':sys.argv,
                'started_at':datetime.now(timezone.utc).isoformat(),'git':git_metadata(),
                'python':sys.version,'budget_seconds':budget_seconds,'execution_mode':'E2E_OFFLINE_REPLAY'}
    registry_path.write_text(json.dumps(registry,ensure_ascii=False,indent=2),encoding='utf-8')
    reporter = BuildProgress(output,budget_seconds)
    def progress(event):
        reporter(event)
        if progress_callback:
            progress_callback(event)
    try:
        progress({'stage':'CORRECTED_BUILD_START','source_db_access':'READ_ONLY_URI'})
        target_db = output/'corrected.sqlite3'
        closure = copy_input_closure(source_db,target_db,[base_input_id],progress=progress)
        store = SQLiteStore(target_db)
        base = load_bundle(store,base_input_id)
        old_next = load_bundle(source_db,next_input_id)
        actual_cutoff = datetime.now(timezone.utc)
        source_results,selection = selected_delta_results(inventory,base,old_next,progress=progress,
                                                          current_session_only=current_session_only)
        allowed = {r['provider'] for r in source_results if r['endpoint'] in PRICE_DATASETS and r['status'] in {'COMPLETE','PARTIAL'}}
        base_proof = validate_history_base(base,HistoryArtifactStore(store),observation_date=old_next.observation_date,
                                            cutoff=actual_cutoff,allowed_price_providers=allowed,
                                            incoming_price_receipts=source_results)
        base_proof['selection_reason'] = 'EXPLICIT_USER_FROZEN_R16_DAY_T_BASE_VALIDATED_NOT_HASH_ORDER'
        calendars = [r for r in source_results if r['endpoint']=='calendar' and r['status']=='COMPLETE' and r['pagination_complete']]
        from datetime import date
        sessions = tuple(sorted({date.fromisoformat(str(row.get('calendar_date',row.get('calendarDate',row.get('session')))))
            for r in calendars for row in r['rows'] if str(row.get('is_trading_day',row.get('isTradingDay','1'))) == '1'}))
        sessions = tuple(day for day in sessions if day <= old_next.observation_date)
        if sessions != old_next.sessions or not sessions or sessions[-1] != old_next.observation_date:
            raise ValueError('RECORDED_CALENDAR_DOES_NOT_MATCH_FROZEN_NEXT_INPUT')
        wrapper = base.model_copy(update={'coverage_type':'RECONSTRUCTED_HISTORICAL'})
        wrapper_id = register_isolated_input(store,wrapper,metadata={
            'parent_input_id':base_input_id,'rectification_policy':'COVERAGE_LABEL_ONLY_HISTORY_ROWS_UNCHANGED'})
        # First repair Day T at the same actual information cutoff. This keeps
        # historical additions separate from the subsequent ordinary new day.
        historical_prices = [r for r in source_results if r['endpoint']=='market_daily'
            and r['status'] in {'COMPLETE','PARTIAL'} and str(r['parameters']['date']) <= str(base.observation_date)]
        old_metadata_ids = [r.receipt_id for r in base.receipts
            if r.dataset not in {'market_daily','history','history_adjusted','adjustment_factors'}]
        old_metadata,old_metadata_assets = load_inventory_results(inventory,old_metadata_ids,progress=progress)
        factors = [r for r in source_results if r['endpoint']=='adjustment_factors']
        current_t_ids = base_current_price_receipt_ids(base)
        current_t_prices,current_t_assets = load_inventory_results(inventory,current_t_ids,progress=progress)
        corrected_t_sources = old_metadata+historical_prices+factors
        progress({'stage':'CORRECTED_T_NORMALIZATION_START','historical_price_captures':len(historical_prices)})
        normalized_t_history,normalization_t = normalize_capture_with_report(corrected_t_sources,base.observation_date,
            actual_cutoff,base.sessions,history_store=HistoryArtifactStore(store),
            history_base_ref=base.history_artifact_ref,coverage_type='RECONSTRUCTED_HISTORICAL',progress=progress)
        corrected_t = preserve_t_envelope_with_normalized_history(base,normalized_t_history)
        corrected_t_id = register_isolated_input(store,corrected_t,metadata={
            'parent_input_id':base_input_id,'rectification_policy':'RECONSTRUCTED_T_HISTORICAL_ADDITIONS_COMMON_INFORMATION_CUTOFF'})
        progress({'stage':'CORRECTED_T_DATED_ELIGIBILITY_CHECK_START'})
        eligibility_t = verify_corrected_t_eligibility(base,corrected_t,actual_cutoff)
        (output/'corrected-t-eligibility-check.json').write_text(json.dumps(eligibility_t,ensure_ascii=False,indent=2),encoding='utf-8')
        progress({'stage':'CORRECTED_T_DATED_ELIGIBILITY_CHECK_DONE',**eligibility_t})
        progress({'stage':'CORRECTED_T_NORMALIZATION_DONE','input_id':corrected_t_id})
        historical_ids = {r['request_receipt_id'] for r in historical_prices}
        next_delta = [r for r in source_results if r['request_receipt_id'] not in historical_ids]
        selection['corrected_t_selected_assets'] = old_metadata_assets
        selection['corrected_t_current_price_assets'] = current_t_assets
        selection['corrected_t_current_price_receipt_ids'] = current_t_ids
        selection['corrected_t_current_price_policy'] = 'VERIFIED_ORIGINAL_CURRENT_CAPTURE_PRESERVED_IN_T_ENVELOPE_NOT_RENORMALIZED'
        selection['corrected_t_envelope_policy'] = 'ONLY_REAL_NORMALIZED_HISTORY_REF_HASH_RECEIPT_UNION_AND_RECON_COVERAGE_CHANGE'
        selection['corrected_t_historical_price_receipt_ids'] = sorted(historical_ids)
        selection['comparison_policy'] = 'ORIGINAL_T_TO_CORRECTED_T_IS_REPAIR; CORRECTED_T_TO_T_PLUS_1_IS_NEW_DAY_AT_COMMON_ACTUAL_CUTOFF'
        progress({'stage':'NORMALIZATION_START','source_results':len(next_delta),'base_ref':corrected_t.history_artifact_ref})
        corrected,normalization = normalize_capture_with_report(next_delta,old_next.observation_date,actual_cutoff,sessions,
            history_store=HistoryArtifactStore(store),history_base_ref=corrected_t.history_artifact_ref,
            coverage_type='RECONSTRUCTED_HISTORICAL',progress=progress)
        progress({'stage':'NORMALIZATION_DONE','history_artifact_ref':corrected.history_artifact_ref})
        corrected_id = register_isolated_input(store,corrected,metadata={
            'parent_input_id':base_input_id,'old_bad_input_id':next_input_id,
            'rectification_policy':'R16_R1_VERIFIED_LOCAL_RAW_DELTA_REAL_NORMALIZER_RECONSTRUCTED'})
        progress({'stage':'MARKET_INPUT_REGISTER_DONE','input_id':corrected_id})
        normalization_path = output/'normalization-report.json'
        normalization_path.write_text(json.dumps(normalization,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        normalization_t_path = output/'normalization-t-report.json'
        normalization_t_path.write_text(json.dumps(normalization_t,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        (output/'source-selection.json').write_text(json.dumps(selection,ensure_ascii=False,indent=2),encoding='utf-8')
        result = {'build_version':'R16_R1_CORRECTED_HISTORY_LOCAL_DELTA_V1','identity':identity,
            'git':registry['git'],'command':sys.argv,'started_at':registry['started_at'],
            'finished_at':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':time.monotonic()-reporter.started,
            'cutoff':actual_cutoff.isoformat(),'target_db':str(target_db),'closure':closure,'base_proof':base_proof,
            'input_t_original':base_input_id,'input_t_reconstructed_policy_wrapper':wrapper_id,
            'input_t_corrected':corrected_t_id,'history_t_corrected_ref':corrected_t.history_artifact_ref,
            'input_t_plus_1_old_bad':next_input_id,'input_t_plus_1_corrected':corrected_id,
            'history_t_ref':base.history_artifact_ref,'history_t_plus_1_old_bad_ref':old_next.history_artifact_ref,
            'history_t_plus_1_corrected_ref':corrected.history_artifact_ref,
            'coverage_type':corrected.coverage_type,'input_payload_sha256':corrected_id.removeprefix('market_'),
            'source_selection_sha256':hashlib.sha256((output/'source-selection.json').read_bytes()).hexdigest(),
            'normalization_report_sha256':hashlib.sha256(normalization_path.read_bytes()).hexdigest(),
            'normalization_t_report_sha256':hashlib.sha256(normalization_t_path.read_bytes()).hexdigest(),
            'comparison_policy':selection['comparison_policy'],
            'common_information_cutoff':actual_cutoff.isoformat(),
            'corrected_t_dated_eligibility':eligibility_t,
            'prospective_as_of_day_t_claim':False,
            'input_t_wrapper_policy_effect':'COVERAGE_TYPE_CHANGED_SO_PIT_FINGERPRINTS_MAY_CHANGE; BASE_HISTORY_ROWS_IDENTICAL',
            'price_delta_classification_counts':selection['price_delta_classification_counts'],
            'actual_network_requests':0,'execution_mode':'E2E_OFFLINE_REPLAY',
            'historical_source_stability':'NOT_VERIFIED_PENDING_SEPARATE_AUDIT','readiness':'NOT_VERIFIED_PENDING_SEPARATE_AUDIT',
            'official_records_written':False,'market_edge':'NOT_ESTABLISHED'}
        (output/'build-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        registry.update(state='SUCCEEDED',finished_at=result['finished_at'],exit_code=0)
        progress({'stage':'CORRECTED_BUILD_DONE','input_id':corrected_id,'target_db':str(target_db)})
        return result
    except BaseException as exc:
        registry.update(state='FAILED',finished_at=datetime.now(timezone.utc).isoformat(),
                        error_type=type(exc).__name__,error=str(exc),exit_code=1)
        raise
    finally:
        registry_path.write_text(json.dumps(registry,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-db',required=True)
    parser.add_argument('--inventory',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--base-input-id',default=DEFAULT_T)
    parser.add_argument('--next-input-id',default=DEFAULT_TP1)
    parser.add_argument('--budget-seconds',type=float,default=600)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--current-session-only-smoke',action='store_true')
    args = parser.parse_args()
    result = build(args.source_db,args.inventory,args.output,base_input_id=args.base_input_id,
        next_input_id=args.next_input_id,budget_seconds=args.budget_seconds,resume=args.resume,
        current_session_only=args.current_session_only_smoke)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
