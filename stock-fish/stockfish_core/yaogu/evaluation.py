"""Frozen-parameter historical studies; labels are never returned to features.

Comparisons are within a strategy family and registered score profile. The
study's Top K is an explicit comparison budget, not display or portfolio size.
No routine here approves a model, edits a strategy, or establishes market edge.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, time
import io
import json
from pathlib import Path
import random
import statistics
from collections import Counter, defaultdict
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator

from .contracts import CandidateRun, Frozen, digest
from .config import StrategyConfig, weighted_score
from .outcomes import OutcomeObservation, HORIZONS
from ..common import utc_now, canonical_json
from ..model_registry import file_hash, write_once

SH = ZoneInfo('Asia/Shanghai')
METHODS = ('EQUAL_WEIGHT_ALL', 'RULE_SCORE_TOP_K', 'STREAK_ONLY', 'RET5_ONLY', 'AMOUNT_RATIO_ONLY',
           'DISPLAY_SELECTED', 'FOCUS_SELECTED', 'FIXED_RANDOM')


class StudyConfig(Frozen):
    version: str = 'YAOGU_HISTORICAL_STUDY_V1'
    top_k: int = Field(default=5, ge=1, le=500, strict=True)
    random_seed: int = Field(default=20260905, strict=True)
    random_repetitions: int = Field(default=100, ge=100, le=100, strict=True)
    bootstrap_seed: int = Field(default=20260905, strict=True)
    bootstrap_repetitions: int = Field(default=1000, ge=100, le=10000, strict=True)
    confidence_level: float = Field(default=.95, gt=0, lt=1)
    minimum_date_clusters: int = Field(default=10, ge=2, strict=True)
    sensitivity_changes: tuple[float, ...] = (-.10, .10)

    @field_validator('sensitivity_changes')
    @classmethod
    def declared_sensitivity(cls, value):
        if value != (-.10, .10):
            raise ValueError('V1 sensitivity is the preregistered minus/plus 10 percent analysis')
        return value


def _quantile(values, probability):
    values = sorted(values)
    if not values:
        return None
    index = (len(values) - 1) * probability
    lo, hi = int(index), min(int(index) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (index - lo)


def _feature(item, key):
    feature = item.features.get(key)
    value = feature.value if feature and feature.quality in ('VALID', 'EXACT', 'COMPLETE') else None
    return float(value) if type(value) in (int, float) else None


def _ripe(observation, cutoff):
    return (observation.status != 'NOT_MATURED' and observation.label_end is not None
            and datetime.combine(observation.label_end, time(15), SH) <= min(observation.evaluated_at, cutoff))


def _latest_observations(observations, cutoff):
    latest = {}
    for observation in observations:
        if observation.schema_version != 'TRADING_SESSION_OBSERVATION_V1':
            raise ValueError('unsupported outcome schema for exchange-session study')
        if observation.evaluated_at.tzinfo is None:
            raise ValueError('observation timestamp must be timezone-aware')
        if observation.evaluated_at > cutoff:
            continue
        key = (observation.run_id, observation.instrument_id, observation.horizon)
        previous = latest.get(key)
        if previous is None or previous.evaluated_at < observation.evaluated_at:
            latest[key] = observation
        elif previous.evaluated_at == observation.evaluated_at and previous != observation:
            raise ValueError('conflicting same-time outcome observations')
    return latest


def _next_limit(observation, cutoff):
    if observation is None or not _ripe(observation, cutoff):
        return None
    return observation.session_states[0].get('close_limit_up') if observation.session_states else None


def _sample(run, item, latest, cutoff):
    horizons = {h: latest.get((run.run_id, item.instrument_id, h)) for h in HORIZONS}
    if any(o and (o.family != item.primary_family or o.observation_date != run.observation_date
                  or o.evaluated_at < run.generated_at) for o in horizons.values()):
        raise ValueError('outcome family or original observation identity mismatch')
    label_horizon = 1 if item.primary_family in ('ONE_TO_TWO', 'TWO_TO_THREE', 'HIGH_BOARD') else 3
    label = horizons[label_horizon]
    # An h1 mature price record for a 3-session family is not a mature structure
    # label. Likewise a censored price path may still have a known TRUE event.
    known = label is not None and _ripe(label, cutoff)
    structure = label.structure_success if known else None
    h1 = horizons[1]
    one_price = None
    if h1 and _ripe(h1, cutoff) and h1.session_states:
        state = h1.session_states[0]
        one_price = state.get('one_price_up')
        if one_price is None and state.get('close_limit_up') is False:
            one_price = False
    return {'sample_id': run.run_id + '/' + item.instrument_id, 'run_id': run.run_id,
        'observation_date': run.observation_date.isoformat(), 'month': run.observation_date.strftime('%Y-%m'),
        'family': item.primary_family, 'instrument_id': item.instrument_id, 'strategy_id': run.strategy_id,
        'config_hash': run.config_hash, 'profile': run.profile, 'market_session': run.market_session,
        'revision': run.revision, 'coverage_type': run.coverage_type, 'emotion_state': run.emotion.state,
        'score': item.score, 'tier': item.tier, 'rank': item.family_rank,
        'components': item.components, 'weights': item.weights, 'contributions': item.contributions,
        'raw_features': {k:v.model_dump(mode='json') for k,v in item.features.items()},
        'eligible_for_score_comparison': item.score is not None and item.tier not in ('UNAVAILABLE','EXCLUDED','DATA_PENDING'),
        'selected_display': item.instrument_id in run.display_ids, 'selected_focus': item.instrument_id in run.focus_ids,
        'reason_codes': item.reason_codes, 'missing': item.missing, 'risk_flags': item.risk_flags,
        'structure_label_horizon': label_horizon, 'structure_success': structure,
        'structure_status': label.status if label else 'UNAVAILABLE',
        'structure_label_end': label.label_end.isoformat() if label and label.label_end else None,
        'structure_mature': known, 'next_session_close_limit_up': _next_limit(h1, cutoff),
        'one_price_next_session': one_price, 'execution_unconfirmed': h1 is None or h1.execution_status != 'VERIFIED',
        'observations': {str(h): o.model_dump(mode='json') if o else None for h,o in horizons.items()},
        'price_metrics': {str(h): dict(o.metrics) if o and _ripe(o,cutoff) else {} for h,o in horizons.items()},
        'price_status': {str(h): o.status if o else 'UNAVAILABLE' for h,o in horizons.items()}}


def _cluster_interval(rows, field, cfg, *, seed_suffix):
    grouped = defaultdict(list)
    for row in rows:
        if row[field] is not None:
            grouped[row['observation_date']].append(float(row[field]))
    days = sorted(grouped)
    values = [value for day in days for value in grouped[day]]
    if not values:
        return {'estimate':None, 'lower':None, 'upper':None, 'date_clusters':0,
                'status':'NO_MATURE_OBSERVATIONS', 'method':'RESAMPLE_TRADING_DATE_CLUSTERS'}
    estimate = statistics.mean(values)
    if len(days) < 2:
        return {'estimate':estimate, 'lower':None, 'upper':None, 'date_clusters':len(days),
                'status':'INSUFFICIENT_DATE_CLUSTERS', 'method':'RESAMPLE_TRADING_DATE_CLUSTERS'}
    seed = cfg.bootstrap_seed ^ int(digest(seed_suffix)[:16],16)
    generator, replicates = random.Random(seed), []
    # All securities/revisions and all fixed random repetitions of a selected
    # date travel together. Neither stocks nor overlapping themes are iid draws.
    totals = {day:(sum(grouped[day]),len(grouped[day])) for day in days}
    for _ in range(cfg.bootstrap_repetitions):
        sample = generator.choices(days,k=len(days))
        numerator = sum(totals[day][0] for day in sample)
        denominator = sum(totals[day][1] for day in sample)
        replicates.append(numerator/denominator)
    alpha = (1-cfg.confidence_level)/2
    return {'estimate':estimate, 'lower':_quantile(replicates,alpha), 'upper':_quantile(replicates,1-alpha),
        'date_clusters':len(days), 'status':'SMALL_DATE_SAMPLE' if len(days)<cfg.minimum_date_clusters else 'ESTIMATED',
        'method':'RESAMPLE_TRADING_DATE_CLUSTERS', 'bootstrap_repetitions':cfg.bootstrap_repetitions,
        'seed':seed, 'base_seed':cfg.bootstrap_seed, 'confidence_level':cfg.confidence_level,
        'degenerate_empirical_interval':min(replicates)==max(replicates),
        'interpretation':'Empirical date resampling cannot infer unobserved market outcomes; a degenerate interval is not certainty.'}


def _aggregate(rows, cfg, identity, repetitions=1):
    counts = Counter(row['structure_status'] for row in rows)
    structures = [row['structure_success'] for row in rows if row['structure_success'] is not None]
    next_boards = [row['next_session_close_limit_up'] for row in rows if row['next_session_close_limit_up'] is not None]
    one_prices = [row['one_price_next_session'] for row in rows if row['one_price_next_session'] is not None]
    price = {}
    for horizon in HORIZONS:
        metrics = [row['price_metrics'][str(horizon)] for row in rows]
        returns = [v['close_reference_change'] for v in metrics if v.get('close_reference_change') is not None]
        adverse = [v['mae_bound'] for v in metrics if v.get('mae_bound') is not None]
        price[str(horizon)] = {'known_price_exposures':len(returns)/repetitions,
            'median_close_reference_change':statistics.median(returns) if returns else None,
            'lower_5pct_close_reference_change':_quantile(returns,.05),
            'lower_5pct_adverse_excursion_bound':_quantile(adverse,.05),
            'price_status_exposures':{k:v/repetitions for k,v in Counter(row['price_status'][str(horizon)] for row in rows).items()},
            'method':'MARKED_REFERENCE_PRICE_CHANGE_NOT_REALIZED_TRADING_RETURN'}
    return {'selected_exposures':len(rows)/repetitions, 'unique_samples':len({r['sample_id'] for r in rows}),
        'date_clusters':len({r['observation_date'] for r in rows}),
        'structure_status_exposures':{k:v/repetitions for k,v in counts.items()},
        'structure_known_exposures':len(structures)/repetitions,
        'structure_success_exposures':sum(structures)/repetitions,
        'structure_hit_rate':statistics.mean(structures) if structures else None,
        'structure_interval':_cluster_interval(rows,'structure_success',cfg,seed_suffix=(identity,'structure')),
        'next_limit_known_exposures':len(next_boards)/repetitions,
        'next_session_limit_up_rate':statistics.mean(next_boards) if next_boards else None,
        'next_limit_interval':_cluster_interval(rows,'next_session_close_limit_up',cfg,seed_suffix=(identity,'next_limit')),
        'one_price_known_exposures':len(one_prices)/repetitions,
        'one_price_next_session_fraction':statistics.mean(one_prices) if one_prices else None,
        'execution_unconfirmed_fraction':statistics.mean(r['execution_unconfirmed'] for r in rows) if rows else None,
        'price':price, 'random_repetitions_included':repetitions,
        'denominator_note':'Each metric keeps its own known/mature denominator; price censoring does not delete a known structure label.'}


def _comparison_selections(runs, samples, cfg):
    by_id = {row['sample_id']:row for row in samples}
    result, missing_factors = [], []
    for run in runs:
        families = sorted({item.primary_family for item in run.items})
        for family in families:
            group = [item for item in run.items if item.primary_family==family and
                     by_id[run.run_id+'/'+item.instrument_id]['eligible_for_score_comparison']]
            group = sorted(group,key=lambda x:(-x.score,-(x.components.get('T') or 0),-(x.components.get('V') or 0),x.instrument_id))
            if not group:
                continue
            budget = min(cfg.top_k,len(group))
            identity = dict(run_id=run.run_id, observation_date=run.observation_date.isoformat(), family=family,
                profile=run.profile, config_hash=run.config_hash, strategy_id=run.strategy_id,
                market_session=run.market_session, coverage_type=run.coverage_type, emotion_state=run.emotion.state,
                eligible_count=len(group), comparison_budget=budget)
            def save(method, items, repetition=None, seed=None):
                result.append({**identity,'method':method,'repetition':repetition,'seed':seed,
                    'selected_ids':[item.instrument_id for item in items],
                    'selected_sample_ids':[run.run_id+'/'+item.instrument_id for item in items]})
            save('EQUAL_WEIGHT_ALL',group)
            save('RULE_SCORE_TOP_K',group[:budget])
            for method,key in [('STREAK_ONLY','streak_exchange'),('RET5_ONLY','ret5'),('AMOUNT_RATIO_ONLY','amount_ratio_5')]:
                absent = [item.instrument_id for item in group if _feature(item,key) is None]
                if absent:
                    missing_factors.append({**identity,'method':method,'status':'UNAVAILABLE',
                        'reason':'FACTOR_MISSING_IN_FROZEN_ELIGIBLE_COHORT','missing_ids':absent})
                else:
                    save(method,sorted(group,key=lambda x:(-_feature(x,key),x.instrument_id))[:budget])
            save('DISPLAY_SELECTED',[item for item in group if item.instrument_id in run.display_ids])
            save('FOCUS_SELECTED',[item for item in group if item.instrument_id in run.focus_ids])
            canonical = sorted(group,key=lambda x:x.instrument_id)
            # Cohort seed does not depend on submitted order, outcomes, selected
            # winners or run UUID. All 100 draws, including repeats, are retained.
            cohort_seed = cfg.random_seed ^ int(digest((run.observation_date.isoformat(),family,run.profile,
                run.config_hash,run.market_session,run.decision_cutoff.isoformat()))[:16],16)
            generator = random.Random(cohort_seed)
            for repetition in range(cfg.random_repetitions):
                save('FIXED_RANDOM',generator.sample(canonical,budget),repetition,cohort_seed)
    return result, missing_factors


def _summaries(selections,samples,cfg):
    by_id = {row['sample_id']:row for row in samples}
    axes = ((),('month',),('emotion_state',),('coverage_type',))
    groups = defaultdict(list)
    for selection in selections:
        for extra in axes:
            dims = ('family','profile','config_hash','market_session',*extra)
            values = tuple(selection.get(k,selection['observation_date'][:7] if k=='month' else None) for k in dims)
            groups[(dims,values,selection['method'])].extend(by_id[k] for k in selection['selected_sample_ids'])
    summaries = []
    for (dims,values,method),rows in sorted(groups.items(),key=lambda x:str(x[0])):
        key = dict(zip(dims,values))
        repetition_count = cfg.random_repetitions if method=='FIXED_RANDOM' else 1
        summaries.append({'dimensions':key,'method':method,
            **_aggregate(rows,cfg,(key,method),repetition_count)})
    baselines = {digest(s['dimensions']):s for s in summaries if s['method']=='EQUAL_WEIGHT_ALL'}
    for summary in summaries:
        baseline = baselines.get(digest(summary['dimensions']))
        for field,prefix in [('structure_hit_rate','structure'),('next_session_limit_up_rate','next_session_limit_up')]:
            a,b = summary[field],baseline[field] if baseline else None
            summary[prefix+'_lift_pp_vs_family_all'] = (a-b)*100 if a is not None and b is not None else None
    return summaries


def sensitivity_analysis(runs, cfg, strategy_configs=None):
    """Re-score frozen components; never deploy/select a best-return variant."""
    from .engine import tier_for
    strategy_configs = strategy_configs or {}
    default = StrategyConfig()
    values = []
    for run in runs:
        strategy = strategy_configs.get(run.config_hash,default if run.config_hash==default.config_hash else None)
        if run.profile!='DAILY_V1' or strategy is None:
            values.append({'run_id':run.run_id,'status':'UNAVAILABLE',
                'reason':'REQUIRES_REGISTERED_DAILY_V1_CONFIG','deployed':False})
            continue
        strategy = StrategyConfig.model_validate(strategy)
        if strategy.config_hash != run.config_hash:
            raise ValueError('sensitivity configuration hash mismatch')
        for family in sorted({item.primary_family for item in run.items}):
            group = [item for item in run.items if item.primary_family==family and item.daily_score is not None]
            if not group:
                continue
            base = sorted(group,key=lambda i:(-i.daily_score,-(i.components.get('T') or 0),-(i.components.get('V') or 0),i.instrument_id))
            baseline = [i.instrument_id for i in base[:cfg.top_k]]
            for key in ('B','V','T','L','G','M'):
                for change in cfg.sensitivity_changes:
                    weights = strategy.weights[family].model_dump()
                    weights[key] *= 1+change
                    total = sum(weights.values())
                    weights = {k:v*100/total for k,v in weights.items()}
                    scores = {i.instrument_id:sum(i.components[k]*w/100 for k,w in weights.items()) for i in group}
                    ordered = sorted(group,key=lambda i:(-scores[i.instrument_id],-(i.components.get('T') or 0),-(i.components.get('V') or 0),i.instrument_id))
                    selected = [i.instrument_id for i in ordered[:cfg.top_k]]
                    values.append({'run_id':run.run_id,'family':family,'status':'EXECUTED','parameter':key,'relative_change':change,
                        'method':'MULTIPLY_ONE_WEIGHT_THEN_RENORMALIZE_ROW_TO_100','weights':weights,
                        'scores':scores,'baseline_selected_ids':baseline,'selected_ids':selected,
                        'top_k_retained_fraction':len(set(selected)&set(baseline))/len(baseline),
                        'deployed':False,'market_edge':'NOT_ESTABLISHED'})
            for key in ('focus_score','watch_score','focus_v','focus_t'):
                for change in cfg.sensitivity_changes:
                    original = getattr(strategy.thresholds,key)
                    proposed = min(100,max(0,original*(1+change)))
                    changed = strategy.model_dump(mode='json')
                    changed['thresholds'][key] = proposed
                    if changed['thresholds']['watch_score'] > changed['thresholds']['focus_score']:
                        values.append({'run_id':run.run_id,'family':family,'status':'INVALID_CONFIGURATION',
                                       'parameter':key,'relative_change':change,'deployed':False})
                        continue
                    alternative = StrategyConfig.model_validate(changed)
                    tiers = {i.instrument_id:tier_for(i.daily_score,i.components,run.emotion.state,i.risk_flags,alternative) for i in group}
                    values.append({'run_id':run.run_id,'family':family,'status':'EXECUTED','parameter':key,
                        'relative_change':change,'original_value':original,'analyzed_value':proposed,
                        'tier_changes':{i.instrument_id:{'before':i.tier,'after':tiers[i.instrument_id]} for i in group if tiers[i.instrument_id]!=i.tier},
                        'deployed':False,'market_edge':'NOT_ESTABLISHED'})
    return values


def walk_forward_boundaries(sessions, observations, *, train_sessions=120, valid_sessions=40,
                           test_sessions=20, step_sessions=20, evaluated_at=None):
    """Executable rolling experiment partitions; purge by actual label_end.

    No model is fit and no return-based parameter selection occurs here. A
    caller can preregister new-version trials using the returned exact IDs.
    """
    calendar = tuple(date.fromisoformat(s) if isinstance(s,str) else s for s in sessions)
    if calendar != tuple(sorted(set(calendar))):
        raise ValueError('unique ascending exchange sessions required')
    if any(type(v) is not int or v<=0 for v in (train_sessions,valid_sessions,test_sessions,step_sessions)):
        raise ValueError('positive integer fold lengths required')
    cutoff = evaluated_at or utc_now()
    if cutoff.tzinfo is None:
        raise ValueError('aware evaluation cutoff required')
    observations = [OutcomeObservation.model_validate(o) for o in observations]
    raw_observation_count = len(observations)
    future_observation_ids = [o.observation_id for o in observations if o.evaluated_at > cutoff]
    observations = list(_latest_observations(observations,cutoff).values())
    folds = []
    width = train_sessions+valid_sessions+test_sessions
    for start in range(0,max(0,len(calendar)-width+1),step_sessions):
        train = calendar[start:start+train_sessions]
        valid = calendar[start+train_sessions:start+train_sessions+valid_sessions]
        test = calendar[start+train_sessions+valid_sessions:start+width]
        partitions = {}
        for name,days,boundary,strict in [('train',train,valid[0],True),('valid',valid,test[0],True),('test',test,test[-1],False)]:
            kept,purged = [],[]
            for o in observations:
                if o.observation_date not in days:
                    continue
                reason = None
                if o.label_end is None:
                    reason='LABEL_END_UNKNOWN'
                elif not (o.label_end<boundary if strict else o.label_end<=boundary):
                    reason='LABEL_CROSSES_NEXT_SPLIT' if strict else 'LABEL_AFTER_TEST_WINDOW'
                elif not _ripe(o,cutoff) or o.evaluated_at>cutoff:
                    reason='LABEL_NOT_VISIBLE_AT_EVALUATION'
                elif o.structure_success is None:
                    reason='STRUCTURE_LABEL_UNKNOWN'
                if reason:
                    purged.append({'observation_id':o.observation_id,'label_end':o.label_end.isoformat() if o.label_end else None,'reason':reason})
                else:
                    kept.append(o.observation_id)
            partitions[name]={'start':days[0].isoformat(),'end':days[-1].isoformat(),
                'label_boundary':boundary.isoformat(),'strict_before_boundary':strict,
                'kept_ids':kept,'purged':purged}
        folds.append({'fold':len(folds),'partitions':partitions})
    return {'method':'ROLLING_120_40_20_ACTUAL_LABEL_END_PURGE','train_sessions':train_sessions,
        'valid_sessions':valid_sessions,'test_sessions':test_sessions,'step_sessions':step_sessions,
        'evaluated_at':cutoff.isoformat(),'folds':folds,'trials_fitted':0,'deployed':False,
        'raw_observation_count':raw_observation_count,'latest_visible_observation_count':len(observations),
        'future_observation_ids_excluded':future_observation_ids,
        'status':'PARTITIONS_EXECUTED' if folds else 'INSUFFICIENT_CALENDAR',
        'limitation':'Partition execution is not calibration, model fitting or evidence of a market edge.'}


def evaluate_study(runs, observations, config=None, evaluated_at=None, *, strategy_configs=None):
    cfg = StudyConfig.model_validate(config) if isinstance(config,dict) else config or StudyConfig()
    cutoff = evaluated_at or utc_now()
    if cutoff.tzinfo is None:
        raise ValueError('timezone-aware study cutoff required')
    runs = tuple(CandidateRun.model_validate(r) for r in runs)
    if len({r.run_id for r in runs}) != len(runs):
        raise ValueError('duplicate candidate run')
    if any(r.generated_at.tzinfo is None for r in runs):
        raise ValueError('candidate generation time must be timezone-aware')
    future_runs = [r.run_id for r in runs if r.generated_at>cutoff]
    runs = tuple(sorted((r for r in runs if r.generated_at<=cutoff),key=lambda r:(r.observation_date,r.generated_at,r.run_id)))
    observations = tuple(OutcomeObservation.model_validate(o) for o in observations)
    latest = _latest_observations(observations,cutoff)
    samples = [_sample(run,item,latest,cutoff) for run in runs for item in run.items]
    selections, missing = _comparison_selections(runs,samples,cfg)
    summaries = _summaries(selections,samples,cfg)
    by_sample = {r['sample_id']:r for r in samples}
    random_repetitions = []
    for repetition in range(cfg.random_repetitions):
        groups = defaultdict(list)
        for selection in selections:
            if selection['method']=='FIXED_RANDOM' and selection['repetition']==repetition:
                groups[(selection['family'],selection['profile'],selection['config_hash'],selection['market_session'])].extend(
                    by_sample[key] for key in selection['selected_sample_ids'])
        for key,rows in sorted(groups.items()):
            known = [r['structure_success'] for r in rows if r['structure_success'] is not None]
            random_repetitions.append({'repetition':repetition,'family':key[0],'profile':key[1],
                'config_hash':key[2],'market_session':key[3], 'structure_known_count':len(known),
                'structure_hit_rate':statistics.mean(known) if known else None,'selected_count':len(rows)})
    parameter = cfg.model_dump(mode='json')
    report = {'study_version':cfg.version,'evaluated_at':cutoff.isoformat(),'config':parameter,
        'study_config_hash':digest(parameter),'input_hash':digest({'runs':[r.model_dump(mode='json') for r in runs],
            'observations':[o.model_dump(mode='json') for o in observations]}),
        'status':'EXECUTED' if any(r['structure_success'] is not None for r in samples) else
            'PARTIAL' if any(any(m.get('close_reference_change') is not None for m in r['price_metrics'].values()) for r in samples) else 'UNAVAILABLE',
        'market_edge':'NOT_ESTABLISHED','market_edge_established':False,
        'run_count':len(runs),'full_candidate_sample_count':len(samples),
        'date_start':min((r.observation_date for r in runs),default=None).isoformat() if runs else None,
        'date_end':max((r.observation_date for r in runs),default=None).isoformat() if runs else None,
        'coverage_type_sample_counts':dict(Counter(r['coverage_type'] for r in samples)),
        'not_eligible_for_score_comparison':sum(not r['eligible_for_score_comparison'] for r in samples),
        'future_run_ids_excluded':future_runs, 'all_outcome_revisions_received':len(observations),
        'latest_outcome_rows_visible':len(latest),'group_summaries':summaries,
        'missing_factor_comparisons':missing,'random_repetition_summaries':random_repetitions,
        'sensitivity':sensitivity_analysis(runs,cfg,strategy_configs),
        'comparison_budget_semantics':'MODIFIABLE_RESEARCH_TOP_K_NOT_DISPLAY_OR_PORTFOLIO_COUNT',
        'parameter_status':'INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED',
        'limitations':['NO_CROSS_FAMILY_TOTAL_WIN_RATE','MARKED_REFERENCE_PRICES_ARE_NOT_ASSUMED_FILLS',
            'NO_INTRADAY_PATH_ORDER_FROM_DAILY_OHLC','CLUSTERED_DATES_DO_NOT_CREATE_MISSING_HISTORICAL_PIT',
            'ALL_RANDOM_REPETITIONS_RETAINED_NONE_CHOSEN_AS_BEST','NO_SENSITIVITY_VARIANT_DEPLOYED',
            'REVISIONS_AND_PROFILES_RETAINED_WITH_DATE_CLUSTER_DEPENDENCE']}
    return {'report':report,'samples':samples,'comparison_selections':selections,
            'outcome_revisions':[o.model_dump(mode='json') for o in observations]}


def _write_csv(path, rows):
    fields = sorted({key for row in rows for key in row})
    with path.open('x',encoding='utf-8-sig',newline='') as out:
        writer = csv.DictWriter(out,fieldnames=fields or ['status'])
        writer.writeheader()
        for row in rows:
            converted = {key:canonical_json(value) if isinstance(value,(dict,list,tuple)) else value for key,value in row.items()}
            writer.writerow({key:"'"+value if isinstance(value,str) and value[:1] in '=+-@' else value for key,value in converted.items()})


def export_study(result, destination):
    root = Path(destination).resolve()
    root.mkdir(parents=True,exist_ok=False)
    for name in ('report','samples','comparison_selections','outcome_revisions'):
        write_once(root/(name+'.json'),result[name])
    _write_csv(root/'samples.csv',result['samples'])
    _write_csv(root/'comparison_selections.csv',result['comparison_selections'])
    files = [{'path':p.name,'size':p.stat().st_size,'sha256':file_hash(p)} for p in sorted(root.iterdir()) if p.is_file()]
    manifest = {'version':'YAOGU_STUDY_EXPORT_V1','files':files,
                'market_edge':'NOT_ESTABLISHED','generated_at':utc_now().isoformat()}
    write_once(root/'manifest.json',manifest)
    return manifest
