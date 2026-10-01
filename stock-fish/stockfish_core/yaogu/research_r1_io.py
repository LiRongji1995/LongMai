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


def load_input(path, *, history_db=None):
    path = Path(path)
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('BOUNDED_EXPORTED_INPUT_REQUIRED_MAX_64MB')
    payload = json.loads(path.read_text())
    bundle = MarketInput.model_validate(payload['input'])
    if bundle.history_artifact_ref:
        if history_db is None:
            raise ValueError('EXPLICIT_READ_ONLY_HISTORY_DB_REQUIRED')
        from ..storage import ReadOnlySQLiteStore
        from .history_artifacts import HistoryArtifactStore, bind_history
        store = ReadOnlySQLiteStore(history_db)
        owner = HistoryArtifactStore(store)
        manifest = owner.manifest(bundle.history_artifact_ref, bundle.history_artifact_hash)
        if sum(part['row_count'] for part in manifest['partitions']) > 100000:
            raise ValueError('BOUNDED_HISTORY_MAX_100000_ROWS')
        bind_history(bundle, owner)
    return payload, bundle


def freeze_manifest(payloads, groups):
    spec = r1.config()
    return dict(contract='R1_EXPERIMENT_MANIFEST_V1', created_at=datetime.now(timezone.utc).isoformat(),
        **r1.identity(), protocol=spec, groups={g: dict(definition=spec['groups'][g], options=r1.group_options(g),
            hash=digest(r1.group_options(g))) for g in groups},
        inputs=[dict(hash=digest(p['input']), cutoff=p['cutoff'], observation_date=p['input']['observation_date'],
                     execution_hash=digest(p.get('execution_tape')), publication=p.get('publication')) for p in payloads],
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
        common = [r['instrument_id'] for r in group['items'] if r.get('candidate_status') != 'CORE_UNKNOWN'
                  and old[r['instrument_id']]['decision_status'] not in ('UNKNOWN', 'WATCH')]
        intersections, independent = Counter(), Counter()
        for row in group['items']:
            keys = []
            for setup, data in row.get('setups', {}).items():
                for condition, evidence in data.get('conditions', {}).items():
                    if evidence['status'] in ('FAIL', 'UNKNOWN'):
                        key = setup + ':' + condition + ':' + evidence['status']
                        independent[key] += 1
                        keys.append(key)
            intersections['|'.join(sorted(keys)) or 'NO_SETUP_FAILURE_OR_NOT_EVALUATED'] += 1
        report[name] = dict(pool=len(group['items']), counts=group['counts'], added=sorted(selected-baseline), removed=sorted(baseline-selected),
            jaccard=len(selected & baseline)/len(selected | baseline) if selected | baseline else None,
            common_evaluable_ids=sorted(common), newly_evaluable_ids=sorted(r['instrument_id'] for r in group['items']
                if r.get('candidate_status') in ('CORE_PASS', 'CORE_FAIL') and old[r['instrument_id']]['decision_status'] in ('UNKNOWN', 'WATCH')),
            independent_condition_counts=dict(independent), condition_intersections=dict(intersections),
            condition_count_policy='OVERLAPPING_COUNTS_DO_NOT_SUM')
    return report


def reference_ids(result, count):
    # Exactly the same dated eligible pool; amount is the only extra common reference gate.
    pool = [r for r in result['v2_control']['items'] if r.get('amount_cny') is not None and float(r['amount_cny']) >= 500000000]
    ranked = sorted((r for r in pool if r.get('ret5_pct') is not None), key=lambda r: (-r['ret5_pct'], r['instrument_id']))
    ids = sorted(r['instrument_id'] for r in pool)
    rng = random.Random(r1.config()['seed'])
    return dict(RANDOM_SAME_COUNT=sorted(rng.sample(ids, min(count, len(ids)))), RET5_SAME_COUNT=[r['instrument_id'] for r in ranked[:count]])


def signals_for(result, ids, publication):
    themes = {r['instrument_id']: sorted({x['theme_id'] for x in r.get('sector_candidates', [])}) or None
              for r in result['v2_control']['items']}
    return [dict(instrument_id=iid, signal_session=result['observation_date'], themes=themes.get(iid),
                 published_at=(publication or {}).get('published_at'),
                 evidence_available_at=(publication or {}).get('evidence_available_at'),
                 evidence_type=result.get('signal_evidence_type', 'RECONSTRUCTED_HISTORICAL')) for iid in ids]


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


def run_local(input_paths, output, *, groups=None, frozen_manifest=None, start=None, end=None, history_db=None):
    groups = list(groups or r1.config()['groups'])
    for group in groups:
        r1.group_options(group)
    loaded = [load_input(p, history_db=history_db) for p in input_paths]
    loaded = [(p,b) for p,b in loaded if (start is None or str(b.observation_date) >= str(start)) and (end is None or str(b.observation_date) <= str(end))]
    if not loaded:
        raise ValueError('NO_INPUT_IN_DATE_RANGE')
    dates = [str(b.observation_date) for p,b in loaded]
    if len(dates) != len(set(dates)):
        raise ValueError('ONE_FROZEN_INPUT_PER_DATE_REQUIRED')
    output = Path(output).resolve()
    repo = r1.ROOT.parent
    if output.is_relative_to(repo):
        checked = subprocess.run(['git', 'check-ignore', '-q', str(output)], cwd=repo, capture_output=True)
        if checked.returncode:
            raise ValueError('PRIVATE_IGNORED_OUTPUT_REQUIRED')
    manifest = freeze_manifest([p for p,b in loaded], groups)
    if frozen_manifest:
        saved = json.loads(Path(frozen_manifest).read_text())
        if {k:v for k,v in saved.items() if k != 'created_at'} != {k:v for k,v in manifest.items() if k != 'created_at'}:
            raise ValueError('FROZEN_MANIFEST_IDENTITY_MISMATCH')
        manifest = saved
    output.mkdir(parents=True, exist_ok=False)
    write_new(output/'manifest.json', manifest)  # before outcomes or strategy evaluation
    results, comparisons, execution_signals, tapes = [], [], {}, []
    for payload, bundle in sorted(loaded, key=lambda x: x[1].observation_date):
        result = r1.run_research(bundle, cutoff=payload['cutoff'], groups=groups)
        result.update(signal_evidence_type='TEST_FIXTURE' if bundle.coverage_type == 'TEST_FIXTURE' else 'RECONSTRUCTED_HISTORICAL', generated_at=datetime.now(timezone.utc).isoformat(), publication=payload.get('publication'),
                      manifest_hash=digest(manifest))
        results.append(result)
        comparisons.append(selection_comparison(result))
        if payload.get('execution_tape'):
            tapes.append(payload['execution_tape'])
        for group, data in result['groups'].items():
            execution_signals.setdefault(group, []).extend(signals_for(result, data['selected_ids'], payload.get('publication')))
            for ref, ids in reference_ids(result, len(data['selected_ids'])).items():
                execution_signals.setdefault(group+'__'+ref, []).extend(signals_for(result, ids, payload.get('publication')))
    # Multiple input dates must share one frozen tape for overlapping positions.
    tape = tapes[0] if tapes and all(digest(t) == digest(tapes[0]) for t in tapes) and len(tapes) == len(loaded) else None
    execution = {g: {str(bps): simulate(signals, tape, slippage_bps=bps) if tape else dict(status='UNAVAILABLE',
        reason='ONE_SHARED_FROZEN_EXECUTION_TAPE_REQUIRED', signal_count=len(signals), records=[dict(s,status='UNAVAILABLE') for s in signals])
        for bps in (0,10,30)} for g,signals in execution_signals.items()}
    signal_execution = {}
    for group, signals in execution_signals.items():
        signal_execution[group] = {}
        for bps in (0, 10, 30):
            records = [simulate([signal], tape, slippage_bps=bps)['records'][0] for signal in signals] if tape else [dict(signal, status='UNAVAILABLE') for signal in signals]
            values = [record['net_return'] for record in records if record.get('status') == 'CLOSED']
            signal_execution[group][str(bps)] = dict(records=records, denominator=len(records), complete=len(values)==len(records),
                mean_net_return=sum(values)/len(values) if values and len(values)==len(records) else None,
                policy='INDEPENDENT_HYPOTHETICAL_SIGNAL_20PCT_BUDGET_NOT_ACCOUNT_NAV')
    for group, scenarios in execution.items():
        base_group = group.split('__')[0]
        unknown_dates = [result['observation_date'] for result in results if result['groups'][base_group]['status']=='INSUFFICIENT_EVIDENCE']
        if unknown_dates:
            for value in scenarios.values():
                value.update(status='INCOMPLETE_SIGNAL_COVERAGE', unevaluable_dates=unknown_dates,
                             account_performance_status='NOT_EVALUABLE_AS_COMPLETE_STRATEGY',
                             account_return=None, max_drawdown=None, mean_net_return=None, median_net_return=None, worst_net_return=None)
    report = dict(signal_execution=signal_execution, manifest_hash=digest(manifest), results=results, selection_comparisons=comparisons, execution=execution,
        engineering_status='PARTIAL_VERIFIED_SEE_MAIN_REPORT', market_validation_status='INCONCLUSIVE',
        statistics=dict(status='UNAVAILABLE', reason='INDEPENDENT_MARKET_SAMPLE_AND_PAIRED_COMPLETE_DATE_BLOCKS_REQUIRED',
                        block_length=20, correction='HOLM'),
        scope_limitations=['Real execution tape adapter unavailable', 'Market split/holdout absent', 'No automatic production promotion'])
    report['run_id'] = 'r1_' + digest({k:v for k,v in manifest.items() if k != 'created_at'})
    if r1.identity()['source_hash'] != manifest['source_hash']:
        raise ValueError('SOURCE_CHANGED_DURING_RESEARCH_REJECTED')
    write_new(output/'result.json', report)
    lines = ['# R1 研究结果', '', 'MARKET_VALIDATION_STATUS=INCONCLUSIVE；不证明市场优势。', '',
             f"run_id: {report['run_id']}", f"manifest_hash: {report['manifest_hash']}", '',
             '|日期|组|选择数|状态|', '|---|---|---:|---|']
    for result in results:
        for group,data in result['groups'].items():
            lines.append(f"|{result['observation_date']}|{group}|{len(data['selected_ids'])}|{data['status']}|")
    lines += ['', '执行与成本记录见 result.json；缺失、未成交、未退出保留分母。合法零候选信号收益为N/A。',
              '本轮没有可声明的独立市场检验或完整真实收益；不输出显著性结论。',
              'ENGINEERING_STATUS=PARTIAL_VERIFIED；真实执行适配、完整日期块统计仍未交付。']
    with (output/'report.md').open('x') as f:
        f.write('\n'.join(lines)+'\n')
    return dict(run_id=report['run_id'], output=str(output), manifest_hash=digest(manifest), status='SUCCEEDED', market_validation_status='INCONCLUSIVE')


def paired_block_comparisons(series, *, seed=20261001, block_length=20, repetitions=2000):
    """Paired mean differences over consecutive exchange-session indexes.

    Missing/unresolved outcomes reject inference rather than complete-case
    selection. A minimum of two full blocks is required. Circular moving-block
    percentile CIs and centered bootstrap p-values, Holm over the whole family.
    Delay beyond the registered block needs separate analysis, not this estimate.
    """
    effects = {}
    for name, pairs in sorted(series.items()):
        if len(pairs) < 2*block_length or any(x.get('difference') is None or x.get('exit_delay', 0) >= block_length for x in pairs):
            effects[name] = dict(status='UNAVAILABLE', reason='INSUFFICIENT_BLOCKS_MISSING_OUTCOMES_OR_LONG_EXIT_DELAY', n=len(pairs))
            continue
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
