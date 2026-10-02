"""Explicit local research orchestration; no network or production store access."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import subprocess

from .contracts import MarketInput, digest
from . import research_r1 as r1
from .execution_research_r1 import simulate


def write_new(path, payload):
    import tempfile
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # atomic publish, fails if destination already exists
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_input(path):
    path = Path(path)
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('BOUNDED_EXPORTED_INPUT_REQUIRED_MAX_64MB')
    payload = json.loads(path.read_text())
    bundle = MarketInput.model_validate(payload['input'])
    return payload, bundle


def load_input(path, *, history_db=None, history_seconds=300):
    from .research_r1_storage import validate_history_seconds
    validate_history_seconds(history_seconds)
    payload, bundle = _read_input(path)
    if bundle.history_artifact_ref:
        if history_db is None:
            raise ValueError('EXPLICIT_READ_ONLY_HISTORY_DB_REQUIRED')
        from ..storage import ReadOnlySQLiteStore
        from .history_artifacts import HistoryArtifactStore, bind_history
        store = ReadOnlySQLiteStore(history_db)
        from .research_r1_storage import BudgetHistory
        owner = BudgetHistory(store, seconds=history_seconds)
        try:
            owner.manifest(bundle.history_artifact_ref, bundle.history_artifact_hash)
            bind_history(bundle, owner)
        except BaseException:
            owner.close()
            raise
    return payload, bundle


def freeze_manifest(payloads, groups, overrides=None):
    spec = r1.config()
    from .research_r1_contracts import effective_spec
    effective = effective_spec(spec)
    if overrides:
        if set(overrides)-{'account_evaluation_end','initial_nav_cny','single_name_cap','theme_cap','entry_offset','exit_offset','holding_session_intervals','slippage_bps_per_side','primary_slippage_bps','order_cutoff','seed','block_length_sessions','bootstrap_repetitions'}:
            raise ValueError('UNSUPPORTED_EXECUTION_OVERRIDE')
        spec.update({k:v for k,v in overrides.items() if k!='account_evaluation_end'})
        effective=effective_spec(spec)
    from .research_r1_statistics import preregistered_family, freeze_windows
    tapes=[p.get('_window_tape',p.get('execution_tape')) for p in payloads]
    tape=tapes[0] if tapes and all(t==tapes[0] for t in tapes) else None
    windows=freeze_windows([p['input']['observation_date'] for p in payloads],tape,effective['exit_offset'],registered_end=(overrides or {}).get('account_evaluation_end'))
    windows.pop('contract')
    return dict(**windows,contract='R1_EXPERIMENT_MANIFEST_V3',comparison_family=preregistered_family(spec),evaluation_contract='R1_EVALUATION_V3', effective_spec=effective, effective_spec_hash=digest(effective), created_at=datetime.now(timezone.utc).isoformat(),
        **r1.identity(), protocol=spec, effective_execution_specs={digest(effective_spec(spec,p['_cost_scenario'])):effective_spec(spec,p['_cost_scenario']) for p in payloads if p.get('_cost_scenario') is not None}, groups={g: dict(definition=spec['groups'][g], options=r1.group_options(g),
            hash=digest(r1.group_options(g))) for g in groups},
        inputs=[dict(hash=p.get('_input_hash') or digest(p['input']), cutoff=p['cutoff'], observation_date=p['input']['observation_date'],
                     execution_hash=p.get('_execution_hash') or digest({'tape':p.get('execution_tape'),'evidence':p.get('execution_evidence')}), publication=p.get('publication')) for p in payloads],
        observed_interval=[min(p['input']['observation_date'] for p in payloads), max(p['input']['observation_date'] for p in payloads)],
        market_split_status='UNAVAILABLE_NO_INDEPENDENT_HOLDOUT_REGISTERED',
        selection_definition='V2_CONTROL/V2_ACCEL_ONLY: FOCUS; all others: CORE_PASS',
        safety_difference='R1_FULL additionally retains UNKNOWN close-limit-down as common UNKNOWN; V2 controls preserve existing behavior')


def selection_comparison(result):
    control = result['v2_control']
    baseline = set(control['qualified_ids'])
    old = {r['instrument_id']: r for r in control['items']}
    report = {}
    for name, group in result['groups'].items():
        selected = set(group['selected_ids'])
        from .research_r1_contracts import evaluability, funnel, CONTRACT
        base_support={iid:evaluability(row) for iid,row in old.items()}
        support={row['instrument_id']:evaluability(row) for row in group['items']}
        common=sorted(iid for iid,x in support.items() if x['selection_decidable'] and base_support[iid]['selection_decidable'])
        newly=sorted(iid for iid,x in support.items() if x['selection_decidable'] and not base_support[iid]['selection_decidable'])
        report[name] = dict(pool=len(group['items']), counts=group['counts'], added=sorted(selected-baseline), removed=sorted(baseline-selected),
            jaccard=len(selected & baseline)/len(selected | baseline) if selected | baseline else None,
            contract=CONTRACT, common_evaluable_definition='SELECTION_DECIDABILITY_NOT_RETURNS',
            common_evaluable_ids=common, common_selection_decidable_ids=common,
            common_core_fact_ids=sorted(iid for iid,x in support.items() if x['core_facts_evaluable'] and base_support[iid]['core_facts_evaluable']),
            newly_evaluable_ids=newly, newly_selection_decidable_ids=newly,
            newly_evaluable_reasons={iid:'CHANGED_REQUIRED_CONDITIONS_SAME_FROZEN_INPUT' for iid in newly},
            newly_collected_evidence_ids=[], support=support, **funnel(group['items']))
    return report


def reference_ids(result, count, *, seed=None):
    # Exactly the same dated eligible pool; amount is the only extra common reference gate.
    pool = [r for r in result['v2_control']['items'] if r.get('amount_cny') is not None and float(r['amount_cny']) >= 500000000]
    ranked = sorted((r for r in pool if r.get('ret5_pct') is not None), key=lambda r: (-r['ret5_pct'], r['instrument_id']))
    ids = sorted(r['instrument_id'] for r in pool)
    rng = random.Random(r1.config()['seed'] if seed is None else seed)
    return dict(RANDOM_SAME_COUNT=sorted(rng.sample(ids, count if count<=len(ids) else 0)), RET5_SAME_COUNT=[r['instrument_id'] for r in ranked[:count]] if len(ranked)>=count else [])


def signals_for(result, ids, publication):
    from .execution_research_r1 import stamp
    themes = {r['instrument_id']: sorted({x['theme_id'] for x in r.get('sector_candidates', [])}) or None
              for r in result['v2_control']['items']}
    publication=publication or {}
    fixture=result.get('signal_evidence_type')=='TEST_FIXTURE'
    published=publication.get('published_at') if fixture else publication.get('assumed_published_at') if publication.get('mode')=='HISTORICAL_ASSUMPTION' else None
    available=publication.get('evidence_available_at')
    try:
        if stamp(published)<stamp(result['decision_cutoff']) or stamp(available)>stamp(published):published=None
    except (ValueError,TypeError):published=None
    return [dict(instrument_id=iid, signal_session=result['observation_date'], themes=themes.get(iid),
                 published_at=published, evidence_available_at=available,
                 evidence_type='TEST_FIXTURE' if fixture else 'RECONSTRUCTED_HISTORICAL_ASSUMED_PUBLICATION',
                 publication_policy='TEST_FIXTURE' if fixture else 'EXPLICIT_HISTORICAL_ASSUMPTION_NOT_ACTUAL_RELEASE',
                 signal_cutoff=result['decision_cutoff']) for iid in ids]


def persist_isolated(store, result):
    """Tested persistence adapter: caller owns isolated SQLiteStore, never auto-wired."""
    if result.get('coverage_type') != 'TEST_FIXTURE':
        raise ValueError('PRODUCTION_PERSISTENCE_DISABLED')
    run_id = 'r1_' + digest(result)
    namespace = 'research_r1_isolated'
    with store.transaction(write=True) as conn:
        found = conn.execute('SELECT 1 FROM documents WHERE namespace=? AND document_id=?', (namespace, run_id)).fetchone()
        if not found:
            store._put_document(conn, namespace, run_id, result)
    return run_id


def run_local(input_paths, output, *, groups=None, frozen_manifest=None, start=None, end=None, history_db=None, mode='run', execution_spec=None, history_seconds=300):
    from .research_r1_contracts import effective_spec
    from .research_r1_storage import SharedWriter
    from .research_r1_statistics import assemble
    from .research_r1_storage import validate_history_seconds
    validate_history_seconds(history_seconds)
    groups=list(groups or r1.config()['groups'])
    for group in groups:r1.group_options(group)
    # Retain descriptors only. No all-date Pydantic inputs or twelve full results.
    descriptors=[]
    for path in input_paths:
        payload,bundle=_read_input(path)
        day=str(bundle.observation_date)
        resolved_tape=payload.get('execution_tape')
        if payload.get('execution_evidence'):
            from .research_r1_adapter import adapt_execution
            resolved_tape,_=adapt_execution(payload['execution_evidence'],base=Path(path).parent)
        if resolved_tape and resolved_tape.get('coverage_type')!=bundle.coverage_type:raise ValueError('MIXED_EXECUTION_AND_SIGNAL_EVIDENCE_TYPES')
        if (start is None or day>=str(start)) and (end is None or day<=str(end)):
            descriptors.append(dict(path=str(path),input={'observation_date':day},cutoff=payload['cutoff'],
                _window_tape={k:resolved_tape[k] for k in ('sessions','as_of')} if resolved_tape else None,_coverage_type=bundle.coverage_type,_input_hash=digest(payload['input']),_envelope_hash=digest(payload),_cost_scenario=resolved_tape.get('cost_scenario') if resolved_tape else None,
                _execution_hash=digest({'tape':payload.get('execution_tape'),'evidence':payload.get('execution_evidence')}),publication=payload.get('publication')))
        if bundle._history_store is not None:bundle._history_store.close()
        del payload,bundle
    if not descriptors:raise ValueError('NO_INPUT_IN_DATE_RANGE')
    if len({p['_coverage_type'] for p in descriptors})!=1:raise ValueError('MIXED_SIGNAL_EVIDENCE_TYPES_REJECTED')
    if len({p['input']['observation_date'] for p in descriptors})!=len(descriptors):raise ValueError('ONE_FROZEN_INPUT_PER_DATE_REQUIRED')
    output=Path(output).resolve();repo=r1.ROOT.parent
    if output.is_relative_to(repo) and subprocess.run(['git','check-ignore','-q',str(output)],cwd=repo,capture_output=True).returncode:
        raise ValueError('PRIVATE_IGNORED_OUTPUT_REQUIRED')
    overrides=json.loads(Path(execution_spec).read_text()) if execution_spec else None
    manifest=freeze_manifest(descriptors,groups,overrides)
    if frozen_manifest:
        saved=json.loads(Path(frozen_manifest).read_text())
        if {k:v for k,v in saved.items() if k!='created_at'}!={k:v for k,v in manifest.items() if k!='created_at'}:raise ValueError('FROZEN_MANIFEST_IDENTITY_MISMATCH')
        manifest=saved
    output.mkdir(parents=True,exist_ok=False);write_new(output/'manifest.json',manifest)
    writer=SharedWriter(output,write_new)
    results=[];comparisons=[];execution_signals={};tape=None; tape_identity=None; tape_consistent=True;adapter_reports=[]
    for descriptor in sorted(descriptors,key=lambda x:x['input']['observation_date']):
        payload,bundle=load_input(descriptor['path'],history_db=history_db,history_seconds=history_seconds)
        if digest(payload)!=descriptor['_envelope_hash']:raise ValueError('INPUT_CHANGED_DURING_RESEARCH')
        result=r1.run_research(bundle,cutoff=payload['cutoff'],groups=groups,include_diagnostics=True)
        result.update(signal_evidence_type='TEST_FIXTURE' if bundle.coverage_type=='TEST_FIXTURE' else 'RECONSTRUCTED_HISTORICAL',
            generated_at=datetime.now(timezone.utc).isoformat(),publication=payload.get('publication'),manifest_hash=digest(manifest))
        comparison=selection_comparison(result)
        current_tape=payload.get('execution_tape')
        if not current_tape and not payload.get('execution_evidence'):
            adapter_reports.append(dict(status='BLOCKED_DATA',observation_date=result['observation_date'],reason='NO_ARCHIVED_EXECUTION_EVIDENCE',required=['forward exchange calendar','dated raw opens and trade restrictions','precutoff legal order limits','complete corporate action coverage','dated rule and tax/transfer evidence','explicit hypothetical commission']))
        if payload.get('execution_evidence'):
            from .research_r1_adapter import adapt_execution
            current_tape,diagnostic=adapt_execution(payload['execution_evidence'],base=Path(descriptor['path']).parent)
            adapter_reports.append(diagnostic)
        identity=digest(current_tape)
        if tape_identity is None:tape_identity=identity;tape=current_tape
        elif identity!=tape_identity:tape_consistent=False
        for group,data in result['groups'].items():
            execution_signals.setdefault(group,[]).extend(signals_for(result,data['selected_ids'],payload.get('publication')))
            data['setup_selected_ids']={setup:sorted(row['instrument_id'] for row in data['items'] if setup in row.get('matched_setups',[]) or (row.get('primary_stage')==setup and row['instrument_id'] in data['selected_ids'])) for setup in r1.v2.STAGE_IDS}
            for ref,ids in reference_ids(result,len(data['selected_ids']),seed=manifest['effective_spec']['seed']).items():
                data.setdefault('reference_coverage',{})[ref]=dict(requested=len(data['selected_ids']),actual=len(ids),status='COMPLETE' if len(ids)==len(data['selected_ids']) else 'INSUFFICIENT_SAME_M_POOL',random_policy='ONE_FROZEN_BASKET_NOT_A_RANDOM_DISTRIBUTION')
                execution_signals.setdefault(group+'__'+ref,[]).extend(signals_for(result,ids,payload.get('publication')))
        # Full evidence remains reconstructible; only compact day summaries survive.
        details=writer.pack(result)
        summary={k:v for k,v in result.items() if k not in ('groups','v2_control','rejected_evidence','qualification','rejected_themes','coverage_diagnostics')}
        summary.update(coverage_diagnostics={k:(writer.pack(v) if k=='items' else v) for k,v in result['coverage_diagnostics'].items()},detail=details,qualification=result['qualification'],rejected_evidence_count=len(result['rejected_evidence']),
            groups={name:{k:v for k,v in data.items() if k!='items'} for name,data in result['groups'].items()})
        if bundle._history_store is not None:summary['history_read_metrics']=dict(bundle._history_store.metrics)
        for data in comparison.values():
            data['condition_details_ref']=writer.pack(data['condition_details'])
            data['condition_detail_count']=len(data['condition_details']);data['condition_detail_samples']=data['condition_details'][:5]
            del data['condition_details']
        results.append(summary);comparisons.append(comparison)
        if bundle._history_store is not None:bundle._history_store.close()
        del result,payload,bundle
    if not tape_consistent:tape=None
    if tape and digest(effective_spec(manifest['effective_spec'],tape['cost_scenario'])) not in manifest['effective_execution_specs']:raise ValueError('EFFECTIVE_EXECUTION_SPEC_IDENTITY_MISMATCH')
    scenarios=manifest['effective_spec']['slippage_bps_per_side']
    execution={};signal_execution={}
    for group,signals in execution_signals.items():
        execution[group]={};signal_execution[group]={}
        for bps in scenarios:
            value=simulate(signals,tape,slippage_bps=bps,spec=manifest['effective_spec'],evaluation_window=manifest['account_evaluation_window']) if tape and mode!='diagnose' else dict(status='UNAVAILABLE',reason='EXECUTION_NOT_REQUESTED_OR_SHARED_EVIDENCE_UNAVAILABLE',signal_count=len(signals),records=[dict(s,status='UNAVAILABLE') for s in signals])
            execution[group][str(bps)]=value
            records=[simulate([signal],tape,slippage_bps=bps,spec=manifest['effective_spec'],evaluation_window=manifest['account_evaluation_window'])['records'][0] for signal in signals] if tape and mode!='diagnose' else [dict(s,status='UNAVAILABLE') for s in signals]
            values=[rec['net_return'] for rec in records if rec.get('status')=='CLOSED']
            signal_execution[group][str(bps)]=dict(records=records,denominator=len(records),complete=len(values)==len(records),
                mean_net_return=sum(values)/len(values) if values and len(values)==len(records) else None,
                policy='CONDITIONAL_SELECTED_SIGNALS_INDEPENDENT_HYPOTHETICAL_BUDGET; ZERO_SIGNALS_NA')
            base_group=group.split('__')[0]
            signal_start,signal_end=manifest['signal_window']
            missing_dates=[d for d in (tape or {}).get('sessions',[]) if signal_start<=d<=signal_end and d not in {r['observation_date'] for r in results}]
            unknown_dates=missing_dates+[r['observation_date'] for r in results if r['groups'][base_group]['coverage']['selection_evaluation_status']!='EVALUABLE' or ('__' in group and r['groups'][base_group]['reference_coverage'][group.split('__')[1]]['status']!='COMPLETE')]
            value['execution_completion']=value['status']
            value['strategy_evaluation_status']='EVALUABLE' if not unknown_dates and value['status']=='COMPLETE' and value.get('account_return') is not None else 'NOT_EVALUABLE_AS_COMPLETE_STRATEGY'
            signal_execution[group][str(bps)]['selection_evaluation_status']='EVALUABLE' if not unknown_dates else 'OBSERVED_SUBSET_ONLY'
            if unknown_dates:
                value['observed_subset']={k:v for k,v in value.items() if k not in ('records','account_curve')}
                value.update(status='INCOMPLETE_SIGNAL_COVERAGE',unevaluable_dates=unknown_dates,account_performance_status='NOT_EVALUABLE_AS_COMPLETE_STRATEGY',account_return=None,max_drawdown=None,mean_net_return=None,median_net_return=None,worst_net_return=None)
    storage=writer.finish()
    statistics=assemble(results,execution,signal_execution,manifest,tape,paired_block_comparisons)
    report=dict(contract='R1_SHARED_RESULT_V3',windows={k:manifest[k] for k in ('signal_window','planned_completion_end','account_evaluation_window','evidence_as_of','tail_policy')},signal_execution=signal_execution,manifest_hash=digest(manifest),results=results,selection_comparisons=comparisons,execution=execution,
        execution_adapter=adapter_reports,engineering_status='SEE_R1_REPAIR_RESULT',market_validation_status='INCONCLUSIVE',statistics=statistics,
        storage=dict(**storage,policy='ONE_DATE_CONTEXT_PLUS_COMPACT_SERIES; HASH_VERIFIED_SHARED_DAG'),
        scope_limitations=['No independent holdout registered','No automatic production promotion','V2 retains full-pool dependency-window context; not constant memory'])
    report['run_id']='r1_'+digest({k:v for k,v in manifest.items() if k!='created_at'})
    if r1.identity()['source_hash']!=manifest['source_hash']:raise ValueError('SOURCE_CHANGED_DURING_RESEARCH_REJECTED')
    write_new(output/'result.json',report)
    write_new(output/'completion.json',dict(result_hash=digest(report),manifest_hash=digest(manifest)))
    lines=['# R1 研究结果','','MARKET_VALIDATION_STATUS=INCONCLUSIVE；MARKET_EDGE=NOT_ESTABLISHED。','',f"run_id: {report['run_id']}",'','|日期|组|选择数|总体|选择可判定|','|---|---|---:|---|---|']
    for result in results:
        for group,data in result['groups'].items():
            cov=data['coverage'];lines.append(f"|{result['observation_date']}|{group}|{len(data['selected_ids'])}|{cov['universe_coverage']}|{cov['selection_decidability']}|")
    for name,description in statistics['descriptions'].items():
        if description.get('window'):
            lines+=['', name+'：'+json.dumps(description['window'],ensure_ascii=False)]
    lines+=['', '冻结窗口：'+json.dumps(report['windows'],ensure_ascii=False),f"统计状态：{statistics['status']}；区分账户、共同可判定范围与条件信号。",'完整证据按 detail 引用重建；未知、未成交、未退出保留，合法零候选信号均值为 N/A。']
    with (output/'report.md').open('x') as f:f.write('\n'.join(lines)+'\n')
    return dict(run_id=report['run_id'],output=str(output),manifest_hash=digest(manifest),status='SUCCEEDED',statistics_status=statistics['status'],market_validation_status='INCONCLUSIVE')


def summarize_saved(source, output):
    """Read-only saved-result consumer; preserve historical identities."""
    source=Path(source);report=json.loads((source/'result.json').read_text());manifest=json.loads((source/'manifest.json').read_text())
    if report['manifest_hash']!=digest(manifest):raise ValueError('SAVED_MANIFEST_CONFLICT')
    if report.get('contract') in ('R1_SHARED_RESULT_V2','R1_SHARED_RESULT_V3'):
        completion=json.loads((source/'completion.json').read_text())
        if completion!=dict(result_hash=digest(report),manifest_hash=digest(manifest)):raise ValueError('INCOMPLETE_OR_CHANGED_RESULT')
        from .research_r1_storage import verify_shared_report
        verify_shared_report(source,report)
    output=Path(output).resolve();repo=r1.ROOT.parent
    if output.is_relative_to(repo) and subprocess.run(['git','check-ignore','-q',str(output)],cwd=repo,capture_output=True).returncode:raise ValueError('PRIVATE_IGNORED_OUTPUT_REQUIRED')
    output.mkdir(parents=True,exist_ok=False)
    summary=dict(contract='R1_SAVED_COMPARISON_SUMMARY_V2',run_id=report['run_id'],manifest_hash=report['manifest_hash'],windows=report.get('windows'),statistics=report['statistics'],selection_comparisons=report['selection_comparisons'],market_validation_status='INCONCLUSIVE')
    write_new(output/'summary.json',summary)
    return dict(status='SUCCEEDED',output=str(output),run_id=report['run_id'])


def paired_block_comparisons(series, *, seed=20261001, block_length=20, repetitions=2000):
    """Paired mean differences over consecutive exchange-session indexes.

    Missing/unresolved outcomes reject inference rather than complete-case
    selection. A minimum of two full blocks is required. Circular moving-block
    percentile CIs and centered bootstrap p-values, Holm over the whole family.
    Delay beyond the registered block needs separate analysis, not this estimate.
    """
    if type(block_length) is not int or block_length<1 or type(repetitions) is not int or repetitions<1:raise ValueError('INVALID_BOOTSTRAP_SPEC')
    import math
    effects = {}
    for name, pairs in sorted(series.items()):
        if len(pairs) < 2*block_length or any(x.get('inference_eligible') is False or x.get('difference') is None or x.get('exit_delay', 0) >= block_length for x in pairs):
            effects[name] = dict(status='UNAVAILABLE', reason='INSUFFICIENT_BLOCKS_MISSING_OUTCOMES_OR_LONG_EXIT_DELAY', n=len(pairs))
            continue
        if any(type(x.get('session_index')) is not int or not math.isfinite(float(x['difference'])) for x in pairs):
            effects[name]=dict(status='UNAVAILABLE',reason='INVALID_SESSION_INDEX_OR_NONFINITE_OUTCOME',n=len(pairs));continue
        ordered = sorted(pairs,key=lambda x:x['session_index'])
        if any(b['session_index'] != a['session_index']+1 for a,b in zip(ordered,ordered[1:])):
            effects[name] = dict(status='UNAVAILABLE', reason='NONCONTIGUOUS_PAIRED_SESSION_SUPPORT', n=len(pairs))
            continue
        values = [float(x['difference']) for x in ordered]
        n=len(values);mean=sum(values)/n;rng=random.Random(seed);draws=[]
        for _ in range(repetitions):
            sample=[]
            while len(sample)<n:
                start=rng.randrange(n)
                sample.extend(values[(start+j)%n] for j in range(block_length))
            draws.append(sum(sample[:n])/n)
        draws.sort()
        p=(1+sum(abs(x-mean)>=abs(mean) for x in draws))/(repetitions+1)
        effects[name]=dict(status='ESTIMATED', n=n, effect=mean, ci95=[draws[int(.025*repetitions)],draws[min(repetitions-1,int(.975*repetitions))]],
            p_value=p, block_length=block_length, exit_delay_warning='BLOCKS_DO_NOT_ELIMINATE_ALL_DEPENDENCE')
    ordered=sorted((v['p_value'],name) for name,v in effects.items() if v['status']=='ESTIMATED')
    previous=0
    # Include every pre-registered comparison in family size, even unavailable ones.
    for index,(p,name) in enumerate(ordered):
        previous=max(previous,min(1,p*(len(series)-index)))
        effects[name]['holm_adjusted_p']=previous
    return effects
