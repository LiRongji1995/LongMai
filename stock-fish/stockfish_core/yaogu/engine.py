"""Deterministic five-head rule engine. No account, model, broker, or LLM dependency."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo
from .contracts import *
from .config import StrategyConfig, R, Trap, weighted_score
from .events import limit_event, annotate_streaks, TRUE, FALSE
from .features import build_features
from .themes import compute_theme, value
from .emotion import compute_emotion
from .emotion_history import context as emotion_context, select_history, effective_history
from .visibility import EvidenceResolver

def security_at(records, instrument_id, day, cutoff, reconstructed=False):
    # Clocks and identity are checked before status/risk business fields.
    visible=[s for s in records if s.instrument_id==instrument_id and s.visible(cutoff,reconstructed) and s.effective_from<=day and (s.effective_to is None or day<s.effective_to)]
    return max(visible,key=lambda s:(s.effective_from,s.source_published_at,s.ingested_at)) if visible else None


class HistoricalSecurityIndex:
    """Prevalidated source clocks once; index only matching identity intervals.

    R03 real capture exposed the former O(instruments² × sessions) full-master
    scan. Per-record source/session scopes remain checked at every lookup.
    """
    def __init__(self,records,resolver):
        self.by_id={};self.resolver=resolver;self._registered=set();self.scope_checks={}
        for s in records:self.by_id.setdefault(s.instrument_id,[]).append(s)
        for iid,rows in self.by_id.items():
            self.by_id[iid]=tuple(sorted(rows,key=lambda s:(s.effective_from,s.source_published_at,s.ingested_at),reverse=True))
            for s in rows:
                parents=[resolver.receipts[r] for r in s.source_refs]
                self.scope_checks[id(s)]=(tuple(frozenset(r.business_sessions) for r in parents if r.business_sessions),
                    tuple(r.historical_time_proof for r in parents if max(r.first_seen_at,r.ingested_at)>resolver.cutoff))

    def at(self,iid,day):
        if day>self.resolver.cutoff.astimezone(ZoneInfo('Asia/Shanghai')).date():return None
        for s in self.by_id.get(iid,()):
            if s.effective_from<=day and (s.effective_to is None or day<s.effective_to):
                scopes,proofs=self.scope_checks[id(s)]
                if any(day not in scope for scope in scopes) or any(p is None or not p.applies_from<=day<=p.applies_to for p in proofs):return None
                if id(s) not in self._registered:
                    if not self.resolver.resolve(s,instrument_id=iid,session=day).allowed:return None
                    self._registered.add(id(s))
                return s
        return None

def qualification(s, day, sessions, config, allow_suspension=False):
    if s is None:return 'UNKNOWN_IDENTITY'
    if 'UNKNOWN' in (s.exchange,s.board,s.security_type):return 'UNKNOWN_IDENTITY'
    if s.exchange not in ('SSE','SZSE') or s.board!='MAIN' or s.security_type!='A_COMMON':return 'OUTSIDE_MAIN_BOARD_A_COMMON'
    if s.st is None or s.suspended is None or s.delisting_period is None:return 'UNKNOWN_HISTORICAL_STATUS'
    if s.delisting_period or s.delisted_on is not None and s.delisted_on<=day:return 'DELISTING_OR_DELISTED'
    if s.st and not config.universe.include_st:return 'ST_POOL_DISABLED'
    if config.universe.include_st and not s.st:return 'OUTSIDE_INDEPENDENT_ST_POOL'
    if s.listed_on>day:return 'NOT_YET_LISTED'
    age=sum(s.listed_on<=d<=day for d in sessions)
    if age<config.universe.min_listing_sessions and sessions and s.listed_on<sessions[0]:return 'UNKNOWN_LISTING_HISTORY'
    if config.universe.include_recent_ipo and age>=config.universe.min_listing_sessions:return 'OUTSIDE_INDEPENDENT_RECENT_IPO_POOL'
    if age<config.universe.min_listing_sessions:
        if not config.universe.include_recent_ipo or age<config.universe.recent_ipo_min_sessions:return 'INSUFFICIENT_LISTING_SESSIONS'
    if s.suspended and not allow_suspension:return 'SUSPENDED_CURRENT_SESSION'
    return None


def partition_universe(states, day, sessions, config, expected_count=None):
    """One historical partition: known exclusions never count as missing data."""
    eligible,excluded,unknown=[],[],[]
    for iid,state in sorted(states.items()):
        reason=qualification(state,day,sessions,config,True)
        if reason is None:eligible.append(iid)
        elif reason.startswith('UNKNOWN_'):unknown.append({'instrument_id':iid,'reason':reason})
        else:excluded.append({'instrument_id':iid,'reason':reason})
    expected=max(expected_count or 0,len(states))
    absent=max(0,expected-len(states))
    suspended=[i for i in eligible if states[i].suspended]
    return {'partition_version':'MAIN_NON_ST_PARTITION_V1','master_expected':expected,
            'identity_observed':len(states),'identity_absent':absent,
            'eligible':len(eligible),'eligible_ids':eligible,
            'known_excluded':len(excluded),'known_excluded_records':excluded,
            'eligibility_unknown':len(unknown)+absent,'eligibility_unknown_records':unknown,
            'known_suspended':len(suspended),'known_suspended_ids':suspended,
            'price_required':len(eligible)-len(suspended),
            'main_a_identity_observed':sum(s.exchange in ('SSE','SZSE') and s.board=='MAIN' and s.security_type=='A_COMMON' for s in states.values()),
            'known_st_identity_count':sum(s.st is True and s.exchange in ('SSE','SZSE') and s.board=='MAIN' and s.security_type=='A_COMMON' for s in states.values())}


def business_semantic_hash(result):
    """R03 deterministic business facts; generation clocks and IDs are metadata."""
    value=result.model_dump(mode='json') if hasattr(result,'model_dump') else result
    def canonical(obj):
        if isinstance(obj,dict):
            return {k:canonical(v) for k,v in obj.items() if k not in
                    {'as_of','frozen_at','condition_id','rejected','document_id','document_hash','published_at','decision_cutoff','effective_input_hash','publication_authority','available_as_of','research_available_as_of'}}
        if isinstance(obj,(tuple,list)):return [canonical(v) for v in obj]
        return obj
    return digest(canonical({k:value[k] for k in ('observation_date','items','emotion','display_ids','focus_ids','profile','config_hash','excluded','unavailable','status')}))

def match_families(features):
    v=lambda k:value(features,k)
    c=v('streak_exchange'); closed=v('close_limit_up'); hits=[]
    if closed==TRUE and c is not None:
        if c>=3:hits.append('HIGH_BOARD')
        if c==2:hits.append('TWO_TO_THREE')
        if c==1:hits.append('ONE_TO_TWO')
    elif closed==TRUE and v('streak_lower_bound') is not None and v('streak_lower_bound')>=3:
        hits.append('HIGH_BOARD')
    trend=closed==FALSE and ((v('M5') is not None and v('M5')>=2) or (v('M10') is not None and v('M10')>=3)) and v('MA5') is not None and v('comparable_close') is not None and v('comparable_close')>=v('MA5') and v('ret3') is not None and v('ret3')>=0
    rebound=closed==FALSE and v('sessions_since_run_end') is not None and 1<=v('sessions_since_run_end')<=3 and v('previous_run_streak') is not None and v('previous_run_streak')>=2 and v('MA5') is not None and v('comparable_close') is not None and v('comparable_close')>=v('MA5') and v('close') is not None and v('open') is not None and v('close')>=v('open') and v('close_location') is not None and v('close_location')>=.6
    if rebound:hits.append('REBOUND_SETUP')
    if trend:hits.append('TREND_NM')
    if not hits:return None,()
    primary=hits[0]; secondary=hits[1:]
    if primary=='ONE_TO_TWO' and v('previous_run_end_close') is not None and v('comparable_close') is not None and v('previous_run_streak') is not None and v('previous_run_streak')>=2 and v('comparable_close')>=v('previous_run_end_close'):
        secondary.append('REBOUND_COMPLETED')
    return primary,tuple(secondary)

def sum_terms(*pairs):
    return None if any(v is None for _,v in pairs) else sum(w*v for w,v in pairs)

def score_components(family, features, t, l, m, config):
    v=lambda k:value(features,k)
    if family=='ONE_TO_TWO':b=sum_terms((.40,R(v('M10'),0,3)),(.30,R(v('M20'),0,5)),(.30,R(v('cmax60'),0,4)))
    elif family=='TWO_TO_THREE':b=sum_terms((.40,R(v('M10'),1,5)),(.30,R(v('M20'),2,7)),(.30,R(v('cmax60'),0,5)))
    elif family=='HIGH_BOARD':b=sum_terms((.40,R(v('streak_exchange'),3,8)),(.30,R(v('M10'),3,7)),(.30,R(v('M20'),3,10)))
    elif family=='TREND_NM':b=sum_terms((.50,R(v('M5'),0,3)),(.30,R(v('M10'),0,5)),(.20,R(v('ret10'),0,.30)))
    else:
        ratio=v('comparable_close')/v('previous_run_end_close') if v('comparable_close') is not None and v('previous_run_end_close') else None
        b=sum_terms((.40,R(v('previous_run_streak'),2,6)),(.30,R(v('M10'),1,5)),(.30,R(ratio,.90,1.02)))
    pv=sum_terms((.30,Trap(v('amount_ratio_5'),*config.amount_traps[family])),(.25,Trap(v('turnover_fraction'),*config.turnover_trap)),(.25,100*v('close_location') if v('close_location') is not None else None),(.20,R(v('ret3'),-.05,.15)))
    g=sum_terms((.50,R(v('limit_up_count_last60'),0,8)),(.50,100*v('G_smoothed_rate') if v('G_smoothed_rate') is not None else None))
    return {'B':b,'V':pv,'T':t,'L':l,'G':g,'M':m}

def tier_for(score,components,emotion_state,risks,config):
    if score is None:return 'DATA_PENDING'
    observe='SERIOUS_ABNORMAL_VOLATILITY' in risks or 'OBSERVE_ONLY' in risks
    if score>=config.thresholds.focus_score and components['V']>=config.thresholds.focus_v and components['T']>=config.thresholds.focus_t and emotion_state not in ('COLD','RETREAT','UNKNOWN') and not observe:return 'FOCUS'
    if score>=config.thresholds.watch_score or observe:return 'WATCH'
    return 'STRUCTURAL_MATCH'

def round_robin(items, order, limit, tier=None):
    groups={f:[i for i in items if i.primary_family==f and i.score is not None and (tier is None or i.tier==tier)] for f in order}
    answer=[];index=0
    while len(answer)<limit:
        changed=False
        for family in order:
            if index<len(groups[family]):answer.append(groups[family][index].instrument_id);changed=True
            if len(answer)>=limit:break
        if not changed:break
        index+=1
    return tuple(answer)

def run(bundle: MarketInput | dict, config=None, cutoff=None, profile='DAILY_V1', run_id=None, revision=1, parent_run_id=None, generated_at=None, market_session='EVENING', raw_bundle_hash=None,history_mode='PUBLISHED_CANDIDATE'):
    bundle=MarketInput.model_validate(bundle)
    config=StrategyConfig.model_validate(config) if isinstance(config,dict) else config or StrategyConfig()
    if config.feature_definition_version != 'YAOGU_FEATURES_V1_R03':
        raise ValueError('LEGACY_STRATEGY_READ_ONLY_REGISTER_R03_VERSION')
    if profile not in ('DAILY_V1','FUNDS_V1','PRICE_ONLY'):raise ValueError('unregistered profile')
    cutoff=datetime.fromisoformat(cutoff) if isinstance(cutoff,str) else cutoff
    if cutoff is None or cutoff.tzinfo is None or cutoff.utcoffset() is None:raise ValueError('explicit timezone-aware decision cutoff required')
    generated_at=generated_at or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:raise ValueError('generated_at must be timezone-aware')
    if bundle.observation_date>cutoff.date():raise ValueError('observation is after cutoff')
    reconstructed=bundle.coverage_type=='RECONSTRUCTED_HISTORICAL'
    day=bundle.observation_date
    sessions=tuple(d for d in bundle.sessions if d<=day)
    previous_day=sessions[-2] if len(sessions)>1 else None
    from .history_artifacts import history_resolver, iter_bars, iter_history_securities, history_identity
    resolver=history_resolver(bundle,cutoff=cutoff)
    probe=history_resolver(bundle,cutoff=cutoff)
    envelope=bundle.universe.model_dump(mode='json',exclude={'securities'})
    envelope_visible=resolver.is_visible(envelope)
    expected_count=bundle.universe.expected_count if envelope_visible else None
    records=tuple(s for s in bundle.universe.securities if probe.is_visible(s,instrument_id=s.instrument_id)) if envelope_visible else ()
    # OUTCOME_ONLY rows preserve delisted/absent prior candidates for outcomes;
    # they are not members of today's official discovery master denominator.
    records=tuple(s for s in records if 'OUTCOME_ONLY' not in s.risk_flags)
    identity=HistoricalSecurityIndex(records,resolver)
    all_ids=sorted({s.instrument_id for s in records})
    current_states={i:s for i in all_ids if (s:=identity.at(i,day)) is not None}
    previous_states={i:s for i in all_ids if previous_day is not None and (s:=identity.at(i,previous_day)) is not None}
    # An already-listed security with no effective yesterday identity is unknown,
    # not proof of exclusion from yesterday's cohort. Never shrink that denominator.
    previous_identity_missing=tuple(i for i in all_ids if previous_day is not None and i not in previous_states and any(
        s.instrument_id==i and s.exchange in ('SSE','SZSE') and s.board=='MAIN' and s.security_type=='A_COMMON'
        and sum(s.listed_on<=d<=previous_day for d in sessions)>=config.universe.min_listing_sessions
        and (s.delisted_on is None or previous_day<s.delisted_on) for s in identity.by_id[i]))
    # ST/recent-IPO switches name an independent research pool; default emotion always excludes both.
    default_config=config.model_copy(update={'universe':config.universe.model_copy(update={'include_st':False,'include_recent_ipo':False})})
    previous_identity_missing=tuple(sorted(set(previous_identity_missing)|{i for i,s in previous_states.items() if qualification(s,previous_day,sessions,default_config,True) is not None and qualification(s,previous_day,sessions,default_config,True).startswith('UNKNOWN_')}))
    if (expected_count or 0)>len(all_ids):previous_identity_missing=(*previous_identity_missing,'UNIDENTIFIED_MASTER_RECORDS')
    partition=partition_universe(current_states,day,sessions,default_config,expected_count)
    current_pool=[i for i,s in current_states.items() if qualification(s,day,sessions,default_config,True) is None]
    previous_pool=[i for i,s in previous_states.items() if qualification(s,previous_day,sessions,default_config,True) is None]
    research_pool=[i for i,s in current_states.items() if qualification(s,day,sessions,config,True) is None]
    dependency_ids=set(current_pool)|set(previous_pool)|set(research_pool)
    bars_by={}
    visible_bars=sorted((b for b in (() if bundle.history_artifact_ref else bundle.bars) if b.session<=day and b.instrument_id in dependency_ids and probe.is_visible(b,instrument_id=b.instrument_id,session=b.session,require_session=True)),key=lambda b:(b.session,b.instrument_id,b.ingested_at))
    for b in visible_bars:bars_by[(b.instrument_id,b.session)]=b
    for b in bars_by.values():resolver.resolve(b,instrument_id=b.instrument_id,session=b.session,require_session=True)
    features={};event_series={};excluded=[];unavailable=[]
    for iid in all_ids:
        state=current_states.get(iid)
        reason=qualification(state,day,sessions,config)
        if reason:excluded.append({'instrument_id':iid,'reason_codes':[reason]})
        if iid not in dependency_ids:continue
        reference_state=state or previous_states.get(iid)
        stock_sessions=tuple(d for d in sessions if reference_state is not None and d>=reference_state.listed_on)
        if not stock_sessions:continue
        item_identity=identity
        if bundle.history_artifact_ref:
            selected={}
            for b in sorted(iter_bars(bundle,instrument_id=iid,end=day),key=lambda b:(b.session,b.ingested_at)):
                if probe.is_visible(b,instrument_id=iid,session=b.session,require_session=True):selected[b.session]=b
            for b in selected.values():resolver.resolve(b,instrument_id=iid,session=b.session,require_session=True)
            bars=[selected.get(d) for d in stock_sessions]
            for d in (day,previous_day):
                if d in selected:bars_by[(iid,d)]=selected[d]
            item_identity=HistoricalSecurityIndex(tuple(s for s in iter_history_securities(bundle,instrument_id=iid,end=day) if 'OUTCOME_ONLY' not in s.risk_flags and probe.is_visible(s,instrument_id=iid)),resolver)
        else:bars=[bars_by.get((iid,d)) for d in stock_sessions]
        events=[]
        for d,b in zip(stock_sessions,bars):
            status=item_identity.at(iid,d)
            event=limit_event(b,iid,d,bool(status and status.suspended))
            if status is None or status.suspended is None or status.st is None:
                event=event.model_copy(update={**{k:'UNKNOWN' for k in ('close_limit_up','close_limit_down','touched_limit_up','touched_not_closed','one_price_up')},'quality':'HISTORICAL_STATUS_UNKNOWN'})
            events.append(event)
        events=annotate_streaks(events)
        # Features consume the full local event sequence. Cross-market emotion
        # only needs t/t-1; retaining every event for every instrument multiplies
        # memory without adding any decision dependency.
        event_series[iid]={d:e for d,e in zip(stock_sessions,events) if d in (day,previous_day)}
        features[iid]={k:v.model_copy(update={'definition_version':config.feature_definition_version}) for k,v in build_features(iid,stock_sessions,bars,events,cutoff).items()}
        if not reason and (len(stock_sessions)<config.universe.required_history_sessions or value(features[iid],'close_limit_up')=='UNKNOWN'):
            unavailable.append({'instrument_id':iid,'reason_codes':['INSUFFICIENT_REQUIRED_HISTORY' if len(stock_sessions)<60 else 'CURRENT_EVENT_UNKNOWN']})
    events_now={i:e[day] for i,e in event_series.items() if day in e}
    # EMPTY asserts that the whole eligible tradable pool was actually screened.
    # Ordinary non-matches need trigger history, not 250-day G or theme scoring.
    structural_unknown=[]
    for iid in research_pool:
        if current_states[iid].suspended:continue
        f=features.get(iid,{})
        closed=value(f,'close_limit_up')
        if closed==TRUE:
            ready=value(f,'streak_exchange') is not None or (value(f,'streak_lower_bound') or 0)>=3
        elif closed==FALSE:
            ready=all(value(f,k) is not None for k in ('M5','M10','MA5','ret3','comparable_close','close','open','close_location'))
            distance=value(f,'sessions_since_run_end')
            if distance is not None and distance<=3 and value(f,'previous_run_streak') is None:ready=False
        else:ready=False
        if not ready:
            structural_unknown.append(iid)
            unavailable.append({'instrument_id':iid,'reason_codes':['STRUCTURAL_TRIGGER_HISTORY_UNAVAILABLE']})
    events_prev={i:e[previous_day] for i,e in event_series.items() if previous_day in e}
    bars_now={i:b for (i,d),b in bars_by.items() if d==day}
    bars_prev={i:b for (i,d),b in bars_by.items() if d==previous_day}
    universe_visible=resolver.is_visible(envelope,require_complete=True)
    history,history_selection=select_history(bundle.previous_emotions,day=day,sessions=sessions,cutoff=cutoff,config=config,coverage_type=bundle.coverage_type,authorization_mode=history_mode)
    emotion=compute_emotion(day,cutoff,current_pool,previous_pool,current_states,previous_states,bars_now,bars_prev,events_now,events_prev,history,universe_visible and bundle.universe.complete,expected_count,config,previous_identity_missing,partition=partition,history_selection=history_selection)
    emotion=emotion.model_copy(update={**emotion_context(config,bundle.coverage_type,history_mode),'source_complete':emotion.score is not None and universe_visible})
    if bundle.coverage_type=='UNVERIFIABLE_AS_OF':
        emotion=emotion.model_copy(update={'score':None,'state':'UNKNOWN','reasons':(*emotion.reasons,'UNVERIFIABLE_AS_OF')})
    # Current after-close research may combine the latest close with memberships
    # first known tonight; freeze tonight's membership date, never relabel it as
    # observation-day PIT history. Historical cutoffs still reject future data.
    membership_day=cutoff.astimezone(ZoneInfo('Asia/Shanghai')).date()
    themes=[t for t in bundle.themes if t.effective_from<=membership_day and (t.effective_to is None or membership_day<t.effective_to) and probe.is_visible(t,session=membership_day,require_complete=True)]
    # Freeze one latest visible membership revision per theme ID before ranking.
    theme_versions={}
    for t in sorted(themes,key=lambda t:(t.theme_id,t.effective_from,t.ingested_at)):theme_versions[t.theme_id]=t
    for t in theme_versions.values():resolver.resolve(t,session=membership_day,require_complete=True)
    items=[]
    for iid in all_ids:
        state=current_states.get(iid)
        if qualification(state,day,sessions,config) is not None or iid not in features:continue
        f=features[iid]
        family,secondary=match_families(f)
        if family is None:continue
        t,l,primary_theme,theme_evidence=compute_theme(iid,theme_versions.values(),research_pool,features,config)
        theme_evidence=tuple({**e,'membership_as_of':membership_day.isoformat(),
            'membership_semantics':'CURRENT_MEMBERSHIP_AT_DECISION' if membership_day!=day else 'OBSERVATION_DAY_VISIBLE_MEMBERSHIP'} for e in theme_evidence)
        components=score_components(family,f,t,l,emotion.score,config)
        if sum(state.listed_on<=d<=day for d in sessions)<config.universe.required_history_sessions:
            components['G']=None
        score=weighted_score(components,config.weights[family])
        missing=tuple(k for k,v in components.items() if v is None)
        risks=list(state.risk_flags)
        if value(f,'streak_exchange') is not None and value(f,'streak_exchange')>=config.thresholds.high_board_warning:risks.append('HIGH_BOARD_RISK')
        elif value(f,'streak_lower_bound') is not None and value(f,'streak_lower_bound')>=config.thresholds.high_board_warning:risks.append('HIGH_BOARD_RISK_LOWER_BOUND')
        if value(f,'one_price_up')==TRUE:risks.append('ONE_PRICE_EXECUTION_UNCONFIRMED')
        if state.st:risks.append('INDEPENDENT_ST_RESEARCH_POOL')
        if sum(state.listed_on<=d<=day for d in sessions)<60:risks.append('INDEPENDENT_RECENT_IPO_POOL')
        tier=tier_for(score,components,emotion.state,risks,config)
        if profile=='PRICE_ONLY':
            # This diagnostic never masquerades as a six-component DAILY score.
            required=('B','V','G')
            score=sum(components[k]*w for k,w in (('B',.40),('V',.50),('G',.10))) if all(components[k] is not None for k in required) else None
            tier='DIAGNOSTIC' if score is not None else 'DATA_PENDING'
        conditions=({'condition_id':'GAP_WEAK','formula':'auction_matched_price/reference_prev_close-1 <= gap_weak','threshold':config.auction.gap_weak,'families':['ONE_TO_TWO','TWO_TO_THREE','HIGH_BOARD'],'version':config.auction.condition_version},
                    {'condition_id':'AUCTION_PARTICIPATION_WEAK','formula':'auction_matched_amount/previous_session_amount < participation_weak','threshold':config.auction.participation_weak,'version':config.auction.condition_version},
                    {'condition_id':'PARTICIPATION_CONFIRMED','amount_threshold':config.auction.participation_confirmed,'gap_threshold':config.auction.gap_weak,'version':config.auction.condition_version},
                    {'condition_id':'HIGH_GAP','threshold':config.auction.high_gap,'version':config.auction.condition_version},
                    {'condition_id':'THEME_THESIS_WEAKENED','positive_fraction_threshold':config.auction.theme_positive_fraction,'median_gap_threshold':config.auction.theme_median_gap,'theme_id':primary_theme,'peer_ids':next((e['peer_ids'] for e in theme_evidence if e['theme_id']==primary_theme),[]),'version':config.auction.condition_version})
        weights=config.weights[family].model_dump()
        items.append(CandidateItem(instrument_id=iid,name=state.name,primary_family=family,secondary_tags=secondary,features=f,components=components,weights=weights,contributions={k:components[k]*weights[k]/100 if components[k] is not None else None for k in weights},score=score,daily_score=weighted_score(components,config.weights[family]),family_rank=None,tier=tier,primary_theme=primary_theme,theme_evidence=theme_evidence,reason_codes=(family,'RULE_MATCH_SCORE_NOT_PROBABILITY',*('MISSING_'+k for k in missing)),risk_flags=tuple(risks),missing=missing,thesis_conditions=conditions,evidence_refs=tuple(sorted({r for fv in f.values() for r in fv.source_refs}))))
    # Funds is a whole-family comparable shadow profile, never opportunistic per-stock weights.
    funds_dependencies={}
    if bundle.funds or profile=='FUNDS_V1':
        from .funds import score_family_funds
        for family in FAMILIES:
            group=[i for i in items if i.primary_family==family]
            if not group:continue
            funding=score_family_funds(bundle.funds,{i.instrument_id:i.daily_score for i in group},cutoff,day,sessions=bundle.sessions,receipts=bundle.receipts,coverage_type=bundle.coverage_type)
            funds_dependencies[family]={'status':funding['status'],'reason_codes':funding.get('reason_codes',[]),'effective_dependencies':funding.get('effective_dependencies',{})}
            updated=[]
            for item in items:
                if item.primary_family!=family:updated.append(item);continue
                evidence=funding.get('items',{}).get(item.instrument_id,{})
                fs=evidence.get('S_funds') if funding['status']=='AVAILABLE' else None
                update={'funds_score':fs,'funds_evidence':{**evidence,'family_status':funding['status'],'family_reason_codes':funding.get('reason_codes',[])}}
                if profile=='FUNDS_V1':update.update(score=fs,tier=tier_for(fs,item.components,emotion.state,item.risk_flags,config))
                updated.append(item.model_copy(update=update))
            items=updated
    ordered=[]
    for family in FAMILIES:
        group=sorted((i for i in items if i.primary_family==family),key=lambda i:(i.score is None,-(i.score or 0),-(i.components['T'] or 0),-(i.components['V'] or 0),i.instrument_id))
        rank=0
        for item in group:
            if item.score is not None:rank+=1
            ordered.append(item.model_copy(update={'family_rank':rank if item.score is not None else None}))
    items=tuple(ordered)
    display=round_robin(items,config.display.order,config.display.observe_count)
    focus=round_robin(items,config.display.order,config.display.focus_count,'FOCUS')
    coverage={**emotion.coverage,'structural_count':len(items),'structural_screening_unknown_ids':structural_unknown,'structural_screening_complete':not structural_unknown,'fully_scored_count':sum(i.daily_score is not None for i in items),'profile_scored_count':sum(i.score is not None for i in items),'theme_snapshots_visible':len(themes),'history_sessions_available':len(sessions),'price_only_diagnostic':profile=='PRICE_ONLY'}
    coverage['research_pool']='ST_INDEPENDENT' if config.universe.include_st else 'RECENT_IPO_INDEPENDENT' if config.universe.include_recent_ipo else 'MAIN_NON_ST'
    coverage['membership_as_of']=membership_day.isoformat()
    coverage['membership_semantics']='CURRENT_MEMBERSHIP_AT_DECISION' if membership_day!=day else 'OBSERVATION_DAY_VISIBLE_MEMBERSHIP'
    evidence=resolver.effective_dependencies()
    emotion=emotion.model_copy(update={'source_complete':emotion.source_complete and all(r['status']=='COMPLETE' and r['pagination_complete'] for r in evidence['sources'])})
    used_sources={r['receipt_id']:r for r in evidence['sources']}
    for family in funds_dependencies.values():
        for item_dependencies in family['effective_dependencies'].get('items',{}).values():
            for r in item_dependencies.get('sources',[]):used_sources[r['receipt_id']]=r
    receipt_hash=digest([used_sources[k] for k in sorted(used_sources)])
    package=Path(__file__).parent
    source_hash=digest({p.relative_to(package).as_posix():digest(p.read_text(encoding='utf-8')) for p in sorted(package.rglob('*.py'))})
    universe_hash=digest({'envelope':envelope if envelope_visible else None,'states':{i:s.model_dump(mode='json') for i,s in current_states.items()},'partition':emotion.coverage})
    dependencies={'contract_version':'R03_EFFECTIVE_INPUT_V1','observation_date':day.isoformat(),
        'evidence':evidence,'calendar_sessions':[d.isoformat() for d in sessions],**history_identity(bundle),
        'configuration':config.model_dump(mode='json'),'coverage_type':bundle.coverage_type,
        'universe_partition':emotion.coverage,'emotion_history':effective_history(history_selection),
        'funds':funds_dependencies,'profile':profile,'membership_as_of':membership_day.isoformat(),
        'membership_semantics':coverage['membership_semantics']}
    input_hash=digest(dependencies)
    coverage['source_receipt_hash']=receipt_hash
    if bundle.coverage_type=='UNVERIFIABLE_AS_OF':status='UNAVAILABLE'
    elif structural_unknown:status='PARTIAL' if items else 'UNAVAILABLE'
    elif emotion.score is None:status='PARTIAL' if items else 'UNAVAILABLE'
    elif any(i.score is None for i in items):status='PARTIAL'
    else:status='COMPLETE' if items else 'EMPTY'
    result=CandidateRun(run_id=run_id or str(uuid4()),revision=revision,parent_run_id=parent_run_id,generated_at=generated_at,observation_date=day,decision_cutoff=cutoff,market_session=market_session,strategy_id=config.strategy_id,config_hash=config.config_hash,feature_definition_version=config.feature_definition_version,source_hash=source_hash,universe_hash=universe_hash,input_hash=input_hash,semantic_hash='PENDING_CANONICAL_BUSINESS_FACTS',profile=profile,coverage_type=bundle.coverage_type,coverage=coverage,status=status,items=items,display_ids=display,focus_ids=focus,excluded=tuple(excluded),unavailable=tuple(unavailable),emotion=emotion,source_refs=tuple(sorted(used_sources)),hash_contract_version='R03_RAW_EFFECTIVE_INPUT_V1',raw_bundle_hash=raw_bundle_hash or digest(bundle),effective_input_hash=input_hash,semantic_hash_version='R03_BUSINESS_SEMANTIC_V1',effective_dependencies=dependencies)
    return result.model_copy(update={'semantic_hash':business_semantic_hash(result)})

class StrategyHead:
    match=staticmethod(match_families)
    score=staticmethod(score_components)

class CandidateEngine:
    run=staticmethod(run)
