"""One immutable candidate publication for API, UI, exports, and observations."""
from __future__ import annotations

from datetime import date, datetime, time
import csv
import io
import json
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator

from ..common import UnavailableError, utc_now
from ..storage import SQLiteStore, ImmutableConflict, checked_payload
from .contracts import Frozen, MarketInput, MarketEmotionSnapshot, SourceReceipt, CandidateRun, FAMILIES, digest
from .config import StrategyConfig, load_config
from .auction import AuctionSnapshot, evaluate_auction, freeze_theses
from .outcomes import evaluate_outcomes


class CandidateRequest(Frozen):
    observation_date: date | None = None
    input_id: str | None = None
    strategy_id: str | None = None
    config_hash: str | None = None
    profile: Literal['DAILY_V1', 'FUNDS_V1', 'PRICE_ONLY'] | None = None
    parent_run_id: str | None = None

    @field_validator('input_id', 'strategy_id', 'config_hash', 'parent_run_id')
    @classmethod
    def identifiers(cls, value):
        if value is not None and (not value or len(value) > 160 or any(c in value for c in '/\\:')):
            raise ValueError('registered identifier required')
        return value


def _diff(old, new, prefix=''):
    result = []
    for key in sorted(set(old) | set(new)):
        path = prefix + key
        a, b = old.get(key), new.get(key)
        if isinstance(a, dict) and isinstance(b, dict):
            result.extend(_diff(a, b, path + '.'))
        elif a != b:
            result.append({'field': path, 'before': a, 'after': b})
    return result


class CandidateService:
    def __init__(self, store: SQLiteStore, jobs, root: Path, *, allow_test_fixture=False):
        self.store, self.jobs, self.root = store, jobs, Path(root)
        self.allow_test_fixture = allow_test_fixture
        self._coverage_lock = RLock()
        self._coverage_projection_cache = None
        from .evidence_service import EvidenceService
        self.public_evidence = EvidenceService(store, allow_test_fixture=allow_test_fixture)
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs.register('candidate_run', self._candidate_job)
        self.jobs.register('auction_review', self._auction_job)
        self.jobs.register('candidate_outcomes', self._outcome_job)
        self.jobs.register('strategy_experiments', self._strategy_experiment_job)
        self.jobs.register('strategy_outcomes', self._strategy_outcome_job)
        self.jobs.register('strategy_tracking', self._strategy_tracking_job)
        if not self._all('strategy_configs'):
            self.register_config(load_config(Path(__file__).resolve().parents[2] / 'config/yaogu_v1.defaults.yaml'))
        elif self.current_config().strategy_id == 'YAOGU_V1.0.0':
            # One explicit new registration/activation; immutable R02 documents
            # and user-created active versions remain untouched.
            previous=self.current_config()
            registered=self.register_config(load_config(Path(__file__).resolve().parents[2] / 'config/yaogu_v1.defaults.yaml'))
            if self.current_config().strategy_id=='YAOGU_V1.0.0':
                # R03 may already have been registered without activation.
                migration={'config_hash':registered['config_hash'],'activated_at':utc_now().isoformat(),
                    'reason':'R03_SEMANTICS_MIGRATION','previous_config_hash':previous.config_hash}
                self.store.put_document('strategy_config_activations','config_activation_'+digest(migration),migration)
        # One-time append-only projection migration keeps existing saved history usable.
        self._ensure_strategy_observation_indexes()
        self.rebuild_strategy_projections()

    def _ensure_strategy_observation_indexes(self):
        # SQLite maintains this derived lookup automatically for every new revision.
        with self.store.transaction() as connection:
            connection.execute("CREATE INDEX IF NOT EXISTS strategy_item_observation_lookup "
                               "ON documents(namespace,json_extract(payload,'$.run_id'),"
                               "json_extract(payload,'$.instrument_id'),created_at,document_id)")

    def _all(self, namespace):
        rows, offset = [], 0
        while True:
            page = self.store.list_documents(namespace, limit=1000, offset=offset)
            rows.extend(p['payload'] for p in page)
            if len(page) < 1000:
                return rows
            offset += len(page)

    def current_config(self):
        configs = self._all('strategy_configs')
        activations = self._all('strategy_config_activations')
        wanted = activations[-1]['config_hash'] if activations else configs[-1]['config_hash']
        return self.config(wanted)

    def config(self, config_hash=None, strategy_id=None):
        if config_hash is None and strategy_id is None:
            return self.current_config()
        configs = self._all('strategy_configs')
        matches = [c for c in configs if (config_hash is None or c['config_hash'] == config_hash)
                   and (strategy_id is None or c['config']['strategy_id'] == strategy_id)]
        if len(matches) != 1:
            raise UnavailableError('REGISTERED_STRATEGY_VERSION_REQUIRED', 'candidate')
        value = StrategyConfig.model_validate(matches[0]['config'])
        if value.config_hash != matches[0]['config_hash']:
            raise UnavailableError('STRATEGY_CONFIG_HASH_MISMATCH', 'candidate')
        return value

    def register_config(self, config: StrategyConfig, *, activate=True):
        prior = self._all('strategy_configs')
        same_version = [p for p in prior if p['config']['strategy_id'] == config.strategy_id]
        if same_version:
            if same_version[0]['config_hash'] != config.config_hash:
                raise ImmutableConflict('configuration edits require a new strategy_id')
            return same_version[0]
        previous = self.current_config() if prior else None
        value = {'config_hash': config.config_hash, 'config': config.model_dump(mode='json'),
                 'registered_at': utc_now().isoformat(), 'parent_config_hash': previous.config_hash if previous else None,
                 'diff': _diff(previous.model_dump(mode='json'), config.model_dump(mode='json')) if previous else [],
                 'market_edge': 'NOT_ESTABLISHED'}
        with self.store.transaction() as connection:
            # The name binding serializes competing registrations; an advisory
            # read before this transaction cannot enforce version uniqueness.
            self.store._put_document(connection, 'strategy_name_bindings', config.strategy_id, {'config_hash': config.config_hash})
            self.store._put_document(connection, 'strategy_configs', config.config_hash, value)
            if activate:
                activation = {'config_hash': config.config_hash, 'activated_at': utc_now().isoformat()}
                self.store._put_document(connection, 'strategy_config_activations', 'config_activation_' + digest(activation), activation)
        return value

    def register_input(self, bundle: MarketInput, *, activate=False):
        """Local administrator only; no HTTP route accepts evidence or trust labels."""
        from .history_artifacts import compact_input, bind_history
        bundle = compact_input(bundle, self.store)
        bundle = MarketInput.model_validate(bundle.model_dump(mode='json'))
        bundle = bind_history(bundle, self.store)
        if bundle.coverage_type == 'TEST_FIXTURE' and not self.allow_test_fixture:
            raise ValueError('test fixtures require an explicitly isolated service')
        input_id = 'market_' + digest(bundle)
        self.store.put_document('candidate_inputs', input_id, bundle.model_dump(mode='json'))
        self.store.put_document('candidate_input_indexes', input_id, {'input_id': input_id, 'observation_date': str(bundle.observation_date),
            'coverage_type': bundle.coverage_type, 'max_session': str(max(bundle.sessions)) if bundle.sessions else None})
        if activate:
            from .engine import run
            validation = run(bundle, self.current_config(), cutoff=utc_now(), profile='DAILY_V1')
            if validation.status not in {'COMPLETE', 'EMPTY'}:
                raise UnavailableError('DAILY_CONSUMER_INPUT_NOT_ACTIVATABLE_' + validation.status, 'candidate')
            value = {'input_id': input_id, 'observation_date': bundle.observation_date.isoformat(),
                     'activated_at': utc_now().isoformat(), 'coverage_type': bundle.coverage_type,
                     'consumer_profile': 'DAILY_V1', 'consumer_status': validation.status,
                     'validation_config_hash': validation.config_hash}
            self.store.put_document('candidate_input_activations', 'activation_' + digest(value), value)
        return input_id

    def input(self, input_id=None, observation_date=None):
        if input_id is None:
            selected = [v for v in self._all('candidate_input_activations')
                        if observation_date is None or v['observation_date'] == observation_date.isoformat()]
            if not selected:
                raise UnavailableError('VERIFIED_RULE_INPUT_NOT_SELECTED', 'candidate')
            input_id = selected[-1]['input_id']
        if not input_id.startswith('market_') or len(input_id) != 71:
            raise UnavailableError('RULE_INPUT_MISSING_OR_CORRUPT', 'candidate')
        # The immutable ID and storage payload use the same canonical JSON.
        # Read/hash these current bytes once, then let Pydantic parse JSON
        # directly; avoid a second full Python dict and dump/hash copy.
        raw = self.store.get_document_bytes('candidate_inputs', input_id, expected_hash=input_id[7:])
        if raw is None:
            raise UnavailableError('RULE_INPUT_MISSING_OR_CORRUPT', 'candidate')
        bundle = MarketInput.model_validate_json(raw)
        from .history_artifacts import bind_history
        bundle = bind_history(bundle, self.store)
        if bundle.coverage_type == 'TEST_FIXTURE' and not self.allow_test_fixture:
            raise UnavailableError('FIXTURE_NOT_ALLOWED_IN_USER_HISTORY', 'candidate')
        if observation_date is not None and bundle.observation_date != observation_date:
            raise ValueError('registered input observation date mismatch')
        return input_id, bundle

    def strategy_input(self, input_id=None, observation_date=None):
        """Select an immutable market input for R05 research experiments.

        Unlike DAILY_V1 publication, experiments may run against the latest
        registered input even when it has not been activated for production
        candidate publication. The input remains explicitly marked by its
        coverage_type and consumer activation status.
        """
        if input_id is not None:
            return self.input(input_id, observation_date)
        wanted = observation_date.isoformat() if observation_date is not None else None
        candidates = []
        offset = 0
        while True:
            rows = self.store.list_document_metadata('candidate_inputs', limit=1000, offset=offset, descending=True)
            for row in rows:
                index = self.store.get_document('candidate_input_indexes', row['document_id'])
                if index is None:
                    value = self.store.get_document('candidate_inputs', row['document_id'])
                    if value is None:
                        continue
                    index = {'input_id': row['document_id'], 'observation_date': value['observation_date'],
                             'coverage_type': value.get('coverage_type'), 'max_session': max(value.get('sessions') or [''])}
                    self.store.put_document('candidate_input_indexes', row['document_id'], index)
                if (wanted is None or index['observation_date'] == wanted) and (self.allow_test_fixture or index.get('coverage_type') != 'TEST_FIXTURE'):
                    candidates.append((index['observation_date'], -len(candidates), row['document_id']))
            if len(rows) < 1000:
                break
            offset += len(rows)
        if not candidates:
            raise UnavailableError('VERIFIED_RESEARCH_INPUT_NOT_REGISTERED', 'candidate')
        selected = max(candidates)[2]
        return self.input(selected, observation_date)

    def submit(self, request: CandidateRequest, *, job_id=None, market_session='ON_DEMAND', cutoff=None):
        input_id, bundle = self.input(request.input_id, request.observation_date)
        if request.input_id is None and request.observation_date is None:
            freshness=self.input_freshness(bundle)
            if freshness['stale'] or freshness['expected_observation_date'] is None:
                raise UnavailableError('ACTIVE_INPUT_STALE_SELECT_EXPLICIT_HISTORICAL_INPUT', 'candidate')
        config = self.config(request.config_hash, request.strategy_id)
        if config.feature_definition_version != 'YAOGU_FEATURES_V1_R03':
            raise UnavailableError('LEGACY_STRATEGY_READ_ONLY_REGISTER_R03_VERSION', 'candidate')
        profile = request.profile or ('FUNDS_V1' if config.funds_primary else 'DAILY_V1')
        now = utc_now()
        # Only scheduler/administrative code can supply its authoritative capture
        # boundary. HTTP request has no cutoff, backfill or generation-time field.
        cutoff = cutoff or now
        if cutoff > now:
            raise ValueError('decision cutoff cannot be in the future')
        parent = self.get_run(request.parent_run_id) if request.parent_run_id else None
        if parent and (parent.observation_date != bundle.observation_date or cutoff < parent.decision_cutoff):
            raise ValueError('revision cannot change observation date or move cutoff backwards')
        payload = {'input_id': input_id, 'config_hash': config.config_hash, 'profile': profile,
                   'decision_cutoff': cutoff.isoformat(), 'market_session': market_session,
                   'parent_run_id': parent.run_id if parent else None, 'revision': parent.revision + 1 if parent else 1}
        return self.jobs.submit('candidate_run', payload, job_id=job_id)

    def input_freshness(self,bundle):
        from zoneinfo import ZoneInfo
        from ..observed_data import exchange_calendar_2026
        now=utc_now().astimezone(ZoneInfo('Asia/Shanghai'))
        known=exchange_calendar_2026().sessions
        sessions=[d for d in known if d<now.date() or d==now.date() and now.time().replace(tzinfo=None)>=time(15)]
        expected=max(sessions) if sessions and now.year==2026 else None
        age=sum(bundle.observation_date<d<=expected for d in known) if expected else None
        return {'stale':expected is None or bundle.observation_date!=expected,
                'expected_observation_date':str(expected) if expected else None,
                'observation_age_sessions':age,'freshness_policy':'LATEST_VERIFIED_EXCHANGE_SESSION_CLOSED_AT_1500_SH_V1'}

    def authorized_emotion_history(self, bundle, config, cutoff):
        """Resolve durable market_emotions through the same run/job authority.

        Raw input.previous_emotions never authorizes ordinary product history.
        This is also the replay entry point: pass its authoritative cutoff and
        registered historical calendar; no natural-day or future-revision fill.
        """
        expected=set(d for d in bundle.sessions if d<bundle.observation_date)
        expected=set(sorted(expected)[-3:])
        result=[]
        for raw in self._all('market_emotions'):
            if date.fromisoformat(raw['session']) not in expected or not raw.get('document_id'):
                continue
            snapshot=MarketEmotionSnapshot.model_validate(raw)
            if snapshot.published_at is None or snapshot.published_at>cutoff or snapshot.as_of>cutoff:
                continue
            try:
                published=self.get_run(snapshot.document_id)
            except (KeyError,UnavailableError):
                continue
            if published.emotion != snapshot:
                continue
            authority=self.store.get_document('candidate_publications',snapshot.document_id)
            job=self.jobs.get(authority['job_id'])
            finished=datetime.fromisoformat(job['finished_at']) if job.get('finished_at') else None
            if finished is None or finished>cutoff:
                continue
            result.append(snapshot.model_copy(update={'published_at':max(snapshot.published_at,finished),
                'publication_authority':{'job_id':authority['job_id'],'finished_at':finished.isoformat(),
                    'run_hash':authority['run_hash'],'emotion_hash':digest(raw)}}))
        return tuple(result)

    def _candidate_job(self, ctx, payload=None, activation=None):
        from .engine import run, business_semantic_hash
        payload = payload or ctx.payload
        _, bundle = self.input(payload['input_id'])
        config = self.config(payload['config_hash'])
        if config.feature_definition_version != 'YAOGU_FEATURES_V1_R03':
            raise UnavailableError('LEGACY_STRATEGY_READ_ONLY_REGISTER_R03_VERSION', 'candidate')
        cutoff=datetime.fromisoformat(payload['decision_cutoff'])
        raw_hash=payload['input_id'][7:]  # input() just verified this exact stored byte address.
        bundle=bundle.model_copy(update={'previous_emotions':self.authorized_emotion_history(bundle,config,cutoff)})
        ctx.update(.05, '核验沪深主板历史资格、收盘数据和证据时点')
        result = run(bundle, config, cutoff=datetime.fromisoformat(payload['decision_cutoff']),
                     profile=payload['profile'], run_id='candidate_' + ctx.job_id,
                     revision=payload['revision'], parent_run_id=payload['parent_run_id'], generated_at=utc_now(),raw_bundle_hash=raw_hash)
        result = result.model_copy(update={'market_session': payload['market_session']})
        auction = config.auction
        known_sessions, calendar_ref = (), None
        if bundle.coverage_type != 'TEST_FIXTURE':
            from ..observed_data import exchange_calendar_2026
            calendar = exchange_calendar_2026()
            known_sessions = tuple(sorted(calendar.sessions))
            calendar_file = Path(__file__).resolve().parents[1] / 'exchange_calendar_2026.json'
            import hashlib
            calendar_ref = 'EXCHANGE_CALENDAR_2026_SHA256:' + hashlib.sha256(calendar_file.read_bytes()).hexdigest()
        result = freeze_theses(result, bundle, {'gap_weak': auction.gap_weak,
            'amount_weak': auction.participation_weak, 'amount_confirmed': auction.participation_confirmed,
            'high_gap': auction.high_gap, 'theme_positive_fraction': auction.theme_positive_fraction,
            'theme_median_gap': auction.theme_median_gap}, condition_version=auction.condition_version,
            known_sessions=known_sessions, calendar_ref=calendar_ref)
        result = result.model_copy(update={'emotion':result.emotion.model_copy(update={
            'document_id':result.run_id,'published_at':utc_now(),
            'effective_input_hash':result.effective_input_hash})})
        result = result.model_copy(update={'semantic_hash':business_semantic_hash(result)})
        ctx.check_cancelled()
        ctx.update(.85, '规则计算完成；准备原子保存候选事实与任务终态')
        value = result.model_dump(mode='json')
        terminal = {'candidate_run_id': result.run_id, 'run_id': result.run_id,
                    'quality': result.status, 'status': 'complete', 'count': len(result.items)}
        activation_validation = result
        if activation is not None and result.profile != 'DAILY_V1':
            activation_validation = run(bundle, config, cutoff=result.decision_cutoff, profile='DAILY_V1')
            ctx.check_cancelled()
        activation_allowed = activation is not None and activation_validation.status in {'COMPLETE', 'EMPTY'}
        if activation is not None:
            terminal['activated'] = activation_allowed
        strategy_primary_result = None
        if payload.get('market_session') == 'EVENING':
            ctx.update(.9, '生成 STAGE_AWARE_MOMENTUM_V2 primary；保留实际资料截止')
            strategy_primary_result = self._build_strategy_experiment_result(
                payload['input_id'], bundle, cutoff, config,
                market_session='EVENING',
                validation_role='PROSPECTIVE_PRIMARY',
                primary_signal_cutoff='SCHEDULED_TRIGGER_20_30_ACTUAL_DECISION_CUTOFF',
                scheduler_job_id=ctx.job_id,
                capture_timing=payload.get('capture_timing'),
            )
            terminal['strategy_primary_run_id'] = strategy_primary_result['run_id']
            terminal['stage_aware_primary_run_id'] = strategy_primary_result['run_id']
            terminal['stage_aware_primary_validation_role'] = 'PROSPECTIVE_PRIMARY'
        def publish(connection):
            self.store._put_document(connection, 'candidate_runs', result.run_id, value)
            self.store._put_document(connection, 'candidate_run_summaries', result.run_id,
                self._run_summary_projection(result, source_run_hash=digest(value)))
            self.store._put_document(connection, 'candidate_publications', result.run_id,
                {'job_id': ctx.job_id, 'run_hash': digest(value), 'input_id': payload['input_id'], 'config_hash': config.config_hash})
            self.store._put_document(connection, 'market_emotions', result.run_id, result.emotion.model_dump(mode='json'))
            if activation is not None:
                if activation_allowed:
                    self.store._put_document(connection, 'candidate_input_activations', 'activation_' + ctx.job_id,
                        {**activation, 'consumer_profile': 'DAILY_V1', 'consumer_status': activation_validation.status,
                         'validation_config_hash': result.config_hash})
                self.store._put_document(connection, 'candidate_capture_results', ctx.job_id,
                    {'candidate_run_id': result.run_id, 'input_id': payload['input_id'], 'actual_cutoff': payload['decision_cutoff'],
                     'coverage_type': bundle.coverage_type, 'snapshot_is_backdated': False,
                     'activated': activation_allowed, 'consumer_status': result.status,
                     **(payload.get('capture_timing') or {})})
            if strategy_primary_result is not None:
                self._write_strategy_result(connection, strategy_primary_result, created_at=payload['decision_cutoff'])
        return ctx.complete(terminal, publish)

    def _publication_authority(self, run_id):
        authority = self.store.get_document('candidate_publications', run_id)
        if authority is None:
            raise UnavailableError('CANDIDATE_PUBLICATION_AUTHORITY_MISSING', 'candidate')
        job = self.jobs.get(authority['job_id'])
        if job is None or job['state'] != 'SUCCEEDED' or (job['result'] or {}).get('candidate_run_id') != run_id:
            raise UnavailableError('CANDIDATE_SUCCESSFUL_JOB_REQUIRED', 'candidate')
        return authority

    def get_run(self, run_id):
        value = self.store.get_document('candidate_runs', run_id)
        if value is None:
            raise KeyError(run_id)
        authority = self._publication_authority(run_id)
        if authority['run_hash'] != digest(value):
            raise UnavailableError('CANDIDATE_PUBLICATION_AUTHORITY_MISSING', 'candidate')
        return CandidateRun.model_validate(value)

    def list_runs(self, limit=100):
        if not 1 <= limit <= 10000:
            raise ValueError('invalid result page size')
        rows = self.store.list_document_metadata('candidate_runs', limit=limit, descending=True)
        return [self.get_run(r['document_id']) for r in rows]

    @staticmethod
    def _reason_counts(records):
        counts={}
        for record in records:
            for reason in record.get('reason_codes',()):
                counts[reason]=counts.get(reason,0)+1
        return dict(sorted(counts.items()))

    @staticmethod
    def _compact_coverage(coverage):
        skip={'eligible_ids','known_excluded_records','eligibility_unknown_records',
              'known_suspended_ids','structural_screening_unknown_ids'}
        result={k:v for k,v in coverage.items() if k not in skip}
        for key in skip:
            value=coverage.get(key)
            if isinstance(value,(list,tuple)):
                result[key[:-4]+'_count' if key.endswith('_ids') else key+'_count']=len(value)
        return result

    @staticmethod
    def _item_summary(item):
        feature_keys=('streak_exchange','streak_lower_bound','close_limit_up','touched_limit_up',
            'M5','M10','M20','cmax60','amount_ratio_5','turnover_fraction','ret3','ret5',
            'ret10','close_location','open','close','reference_prev_close','comparable_close')
        features={}
        for key in feature_keys:
            value=item.features.get(key)
            if value is not None:
                features[key]={'value':value.value,'quality':value.quality,'unit':value.unit,'window':value.window}
        selected_theme=next((e for e in item.theme_evidence if e.get('theme_id')==item.primary_theme),None)
        return {'instrument_id':item.instrument_id,'name':item.name,'primary_family':item.primary_family,
            'secondary_tags':item.secondary_tags,'score':item.score,'daily_score':item.daily_score,
            'funds_score':item.funds_score,'family_rank':item.family_rank,'tier':item.tier,
            'primary_theme':item.primary_theme,'components':item.components,'weights':item.weights,
            'contributions':item.contributions,'reason_codes':item.reason_codes,
            'risk_flags':item.risk_flags,'missing':item.missing,'features':features,
            'theme_summary':{'theme_id':item.primary_theme,'selection_reason':selected_theme.get('selection_reason') if selected_theme else None,
                'peer_expected':selected_theme.get('peer_expected') if selected_theme else None,
                'peer_observed':selected_theme.get('peer_observed') if selected_theme else None,
                'coverage':selected_theme.get('coverage') if selected_theme else None,
                'theme_evidence_count':len(item.theme_evidence)},
            'funds_status':item.funds_evidence.get('family_status') if item.funds_evidence else None,
            'has_detail':True}

    def _run_summary_projection(self, run, *, source_run_hash):
        family_counts={f:{'total':sum(i.primary_family==f for i in run.items),
            'scored':sum(i.primary_family==f and i.score is not None for i in run.items),
            'focus':sum(i.primary_family==f and i.tier=='FOCUS' for i in run.items),
            'watch':sum(i.primary_family==f and i.tier=='WATCH' for i in run.items)} for f in FAMILIES}
        return {'view':'SUMMARY_V1','projection_version':'CANDIDATE_RUN_SUMMARY_PROJECTION_R05_V1',
            'source_run_hash':source_run_hash,'run_id':run.run_id,'revision':run.revision,
            'parent_run_id':run.parent_run_id,'generated_at':run.generated_at.isoformat(),
            'observation_date':run.observation_date.isoformat(),
            'decision_cutoff':run.decision_cutoff.isoformat(),'market_session':run.market_session,
            'strategy_id':run.strategy_id,'config_hash':run.config_hash,
            'feature_definition_version':run.feature_definition_version,
            'source_hash':run.source_hash,'universe_hash':run.universe_hash,'input_hash':run.input_hash,
            'semantic_hash':run.semantic_hash,'semantic_hash_version':run.semantic_hash_version,
            'hash_contract_version':run.hash_contract_version,'raw_bundle_hash':run.raw_bundle_hash,
            'effective_input_hash':run.effective_input_hash,'model_id':run.model_id,'profile':run.profile,
            'parameter_status':run.parameter_status,'coverage_type':run.coverage_type,'status':run.status,
            'item_total':len(run.items),'display_ids':run.display_ids,'focus_ids':run.focus_ids,
            'family_counts':family_counts,'items':[self._item_summary(i) for i in run.items],
            'coverage_summary':self._compact_coverage(run.coverage),
            'emotion':{'session':run.emotion.session.isoformat(),'as_of':run.emotion.as_of.isoformat(),
                'score':run.emotion.score,'state':run.emotion.state,'components':run.emotion.components,
                'readings':{k:v for k,v in run.emotion.readings.items() if k in {'delta_status','d','missing_history_sessions','cohort_n','cohort_s','cohort_raw_p','cohort_m','cohort_p_status','cohort_m_status'}},
                'coverage':self._compact_coverage(run.emotion.coverage),'reasons':run.emotion.reasons,
                'source_complete':run.emotion.source_complete},
            'excluded_summary':{'count':len(run.excluded),'reason_counts':self._reason_counts(run.excluded)},
            'unavailable_summary':{'count':len(run.unavailable),'reason_counts':self._reason_counts(run.unavailable)},
            'source_ref_count':len(run.source_refs),'detail_endpoint':'/api/candidate-runs/'+run.run_id+'/items/{instrument_id}'}

    def run_summary(self, run_id, *, page=1, page_size=50, family=None, selection=None):
        if family is not None and family not in FAMILIES:
            raise ValueError('未知策略族')
        if selection not in (None,'display','focus'):
            raise ValueError('未知候选选择')
        if not 1<=page_size<=50 or page<1:
            raise ValueError('summary 分页大小必须为 1..50')
        projection=self.store.get_document('candidate_run_summaries',run_id)
        if projection is not None:
            authority=self._publication_authority(run_id)
            if projection.get('source_run_hash')!=authority['run_hash']:
                raise UnavailableError('CANDIDATE_SUMMARY_PROJECTION_MISMATCH','candidate')
            items=list(projection['items'])
            display_ids=tuple(projection['display_ids'])
            focus_ids=tuple(projection['focus_ids'])
            item_total=projection['item_total']
        else:
            run=self.get_run(run_id)
            projection=self._run_summary_projection(run,source_run_hash=digest(run))
            items=list(projection['items'])
            display_ids=tuple(projection['display_ids'])
            focus_ids=tuple(projection['focus_ids'])
            item_total=projection['item_total']
        if family is not None:
            items=[i for i in items if i['primary_family']==family]
        if selection=='display':
            wanted=set(display_ids);items=[i for i in items if i['instrument_id'] in wanted]
        elif selection=='focus':
            wanted=set(focus_ids);items=[i for i in items if i['instrument_id'] in wanted]
        total=len(items);start=(page-1)*page_size;page_items=items[start:start+page_size]
        return {k:v for k,v in projection.items() if k not in {'items'}}|{'filtered_total':total,
            'page':page,'page_size':page_size,'has_next':start+page_size<total,
            'item_total':item_total,'items':page_items}

    def strategy_specs(self):
        from .strategy_experiments import STRATEGY_SPECS
        return list(STRATEGY_SPECS)

    def submit_strategy_experiments(self, *, input_id=None, observation_date=None, job_id=None):
        resolved,_=self.strategy_input(input_id,observation_date)
        return self.jobs.submit('strategy_experiments', {'input_id': resolved,
            'cutoff': utc_now().isoformat()}, job_id=job_id)

    def _stage_aware_target_session(self, bundle):
        sessions = sorted(date.fromisoformat(str(day)[:10]) for day in bundle.sessions)
        later = [day for day in sessions if day > bundle.observation_date]
        if later:
            return later[0]
        try:
            from ..observed_data import exchange_calendar_2026
            later = [day for day in sorted(exchange_calendar_2026().sessions) if day > bundle.observation_date]
            return later[0] if later else None
        except Exception:
            return None

    def _build_strategy_experiment_result(self, input_id, bundle, cutoff, config, *, market_session=None,
                                          validation_role='MANUAL_OR_REPLAY', primary_signal_cutoff=None,
                                          scheduler_job_id=None, capture_timing=None):
        from .strategy_experiments import run_strategy_experiments, strategy_comparison
        result=run_strategy_experiments(bundle,cutoff=cutoff,config=config)
        result['input_id']=input_id
        result['generated_at']=utc_now().isoformat()
        latest=self._latest_input_publication(input_id)
        daily_summary=self.run_summary(latest.run_id,page_size=1) if latest is not None else None
        result['comparison']=strategy_comparison(daily_summary,result['results']['PV_OBSERVATION_V1'],
            result['results']['DNA_RERANK_V1'],result['results']['THEME_CONTEXT_V1'],
            result.get('selection_results',{}).get('PV_FOCUS_PREVIEW_V0.1'),
            [value for key,value in result.get('results',{}).items()
             if key in {'THEME_CAPITAL_TREND_TECHNICAL_ONLY_V1','THEME_CAPITAL_TREND_V1',
                        'SIMPLE_TREND_TEMPLATE_REFERENCE_V1','STAGE_AWARE_MOMENTUM_V1',
                        'STAGE_AWARE_MOMENTUM_V2'}])
        from .strategy_tracking import _visible_bars
        frozen_bars = _visible_bars(bundle, cutoff)
        if latest is not None:
            result['market_state'] = latest.emotion.model_dump(mode='json')
            daily_items = []
            for item in latest.items:
                bar = frozen_bars.get((item.instrument_id, bundle.observation_date))
                daily_items.append({**item.model_dump(mode='json'), 'rank': item.family_rank,
                    'comparable_close': str(bar.comparable_close) if bar and bar.comparable_close else None,
                    'comparable_basis': bar.comparable_basis if bar else None})
            result['results'][latest.strategy_id] = {'strategy_id': latest.strategy_id, 'status': latest.status,
                'items': daily_items, 'source_run_id': latest.run_id}
        baseline_items = []
        for iid in result.get('qualification', {}).get('eligible_ids', []):
            bar = frozen_bars.get((iid, bundle.observation_date))
            if bar and bar.comparable_close is not None and bar.comparable_basis:
                baseline_items.append({'instrument_id':iid, 'rank':len(baseline_items)+1,
                    'comparable_close':str(bar.comparable_close), 'comparable_basis':bar.comparable_basis,
                    'source_refs':list(bar.source_refs)})
        result['evaluation_baselines'] = {'ELIGIBLE_UNIVERSE_EQUAL_WEIGHT_PRICE_DIAGNOSTIC': {
            'strategy_id':'ELIGIBLE_UNIVERSE_EQUAL_WEIGHT_PRICE_DIAGNOSTIC', 'status':'COMPLETE' if baseline_items else 'UNAVAILABLE',
            'rank_semantics':'INSTRUMENT_ID_ORDER_NOT_SELECTION_RANK', 'items':baseline_items,
            'cohort_rule':'FROZEN_DATED_ELIGIBLE_UNIVERSE_WITH_SIGNAL_COMPARABLE_CLOSE',
            'input_id':input_id, 'decision_cutoff':cutoff.isoformat()}}
        stage_aware = result.get('results', {}).get('STAGE_AWARE_MOMENTUM_V2') or {}
        counts = stage_aware.get('counts') or {}
        official_primary = (
            validation_role == 'PROSPECTIVE_PRIMARY'
            and market_session == 'EVENING'
            and bundle.coverage_type == 'PROSPECTIVE_CAPTURED'
        )
        result['prospective_validation'] = {
            'protocol_id': 'SCHEDULED_TRIGGER_20_30_ACTUAL_DECISION_CUTOFF',
            'primary_cutoff_policy_id': 'SCHEDULED_2030_ACTUAL_COMPLETION',
            'validation_role': validation_role,
            'official_primary_capture': official_primary,
            'official_primary_signal_countable': False,
            'official_validation_signal_countable': False,
            # Every caller already loaded this ID through input(), which checks
            # immutable stored bytes. Authorized emotion enrichment is separate.
            'input_integrity_valid': True,
            'signal_run_persisted': False,
            **(capture_timing or {}),
            'actual_decision_cutoff': cutoff.isoformat(),
            'preliminary_not_counted': validation_role == 'PROSPECTIVE_PRELIMINARY',
            'market_session': market_session,
            'primary_signal_cutoff': primary_signal_cutoff,
            'scheduler_job_id': scheduler_job_id,
            'signal_observation_date': str(bundle.observation_date),
            'target_session': str(self._stage_aware_target_session(bundle)) if self._stage_aware_target_session(bundle) else None,
            'coverage_type': bundle.coverage_type,
            'stage_aware_strategy_id': 'STAGE_AWARE_MOMENTUM_V2',
            'stage_aware_counts': {
                'focus': counts.get('focus', 0),
                'watch': counts.get('watch', 0),
                'rejected': counts.get('rejected', 0),
                'unknown': counts.get('unknown', 0),
                'eligible': counts.get('eligible', 0),
            },
            'historical_vs_prospective_separation': 'RECONSTRUCTED_HISTORICAL_NEVER_COUNTS_AS_PROSPECTIVE_PRIMARY',
            'market_edge': 'NOT_ESTABLISHED',
        }
        from .validation_identity import validation_identity_for_run, signal_data_quality
        identity = validation_identity_for_run(result)
        scheduled_epoch = (capture_timing or {}).get('scheduled_validation_epoch_id')
        if scheduled_epoch and identity['validation_epoch_id'] != scheduled_epoch:
            raise ValueError('SCHEDULED_VALIDATION_EPOCH_CHANGED_DURING_SIGNAL_EVALUATION')
        result.update(identity)
        result['prospective_validation'].update(identity)
        # Different capture roles/protocols cannot collide with a manual run.
        result['run_id'] = 'strategy_experiment_' + digest({
            'signal_id': result['run_id'], 'validation_epoch_id': identity['validation_epoch_id'],
            'validation_role': validation_role, 'market_session': market_session,
            'scheduler_job_id': scheduler_job_id,
        })
        result['signal_data_quality'] = signal_data_quality(result)
        result['content_hash']=digest({k:v for k,v in result.items() if k!='content_hash'})
        return result

    def _write_strategy_result(self, connection, result, *, created_at):
        from .validation_identity import (validation_identity_for_run, signal_data_quality,
            official_primary_capture_for_run, official_signal_countable)
        prospective = result.get('prospective_validation') or {}
        prospective['signal_run_persisted'] = True
        result['prospective_validation'] = prospective
        prospective['official_primary_capture'] = official_primary_capture_for_run(result)
        prospective['official_validation_signal_countable'] = (
            prospective['official_primary_capture'] and signal_data_quality(result)['signal_evaluable'])
        prospective['official_validation_signal_countable'] = official_signal_countable(result)
        prospective['official_primary_signal_countable'] = prospective['official_validation_signal_countable']
        result['signal_data_quality'] = signal_data_quality(result)
        result['content_hash'] = digest({k:v for k,v in result.items() if k != 'content_hash'})
        # These flags are visible only if this transaction and job success commit.
        self.store._put_document(connection,'strategy_experiment_runs',result['run_id'],result)
        self._write_strategy_projection(connection, result)
        self.store._put_document(connection, 'strategy_research_pending', result['run_id'],
            {'run_id': result['run_id'], 'observation_date': result['observation_date'], 'horizons': [1,2,3,4,5,10,20],
             'created_at': created_at, 'selection_policy_id': 'PV_FOCUS_PREVIEW_V0.1',
             'stage_aware_primary_role': (result.get('prospective_validation') or {}).get('validation_role')})
        prospective = result.get('prospective_validation') or {}
        if prospective.get('official_primary_capture'):
            primary_identity = validation_identity_for_run(result)
            primary = {
                'run_id': result['run_id'],
                'input_id': result.get('input_id'),
                'strategy_id': 'STAGE_AWARE_MOMENTUM_V2',
                'implementation_version': primary_identity['signal_implementation_version'],
                'spec_revision': primary_identity['signal_spec_revision'],
                'signal_observation_date': prospective.get('signal_observation_date'),
                'target_session': prospective.get('target_session'),
                'decision_cutoff': result.get('decision_cutoff') or result.get('cutoff'),
                'market_session': prospective.get('market_session'),
                'primary_signal_cutoff': prospective.get('primary_signal_cutoff'),
                'scheduler_job_id': prospective.get('scheduler_job_id'),
                'coverage_type': prospective.get('coverage_type'),
                'counts': prospective.get('stage_aware_counts'),
                'official_primary_capture': True,
                'official_primary_signal_countable': prospective['official_primary_signal_countable'],
                'official_validation_signal_countable': prospective['official_validation_signal_countable'],
                'signal_data_quality': result['signal_data_quality'],
                **primary_identity,
                **{key: prospective.get(key) for key in ('scheduled_at','capture_started_at','capture_completed_at',
                    'actual_decision_cutoff','start_latency_seconds','capture_latency_seconds','decision_latency_seconds',
                    'scheduled_validation_epoch_id')},
                'market_edge': 'NOT_ESTABLISHED',
                'append_only_policy': 'OUTCOMES_APPEND_ONLY_SIGNAL_RUN_IMMUTABLE',
            }
            self.store._put_document(connection, 'stage_aware_prospective_primary_runs', result['run_id'], primary)
            epoch_id = result['validation_epoch_id']
            existing = connection.execute("SELECT * FROM documents WHERE namespace=? AND document_id=?",
                                          ('stage_aware_prospective_validation_epochs', epoch_id)).fetchone()
            if existing is None:
                epoch = {
                    'epoch_id': epoch_id,
                    'strategy_id': 'STAGE_AWARE_MOMENTUM_V2',
                    'status': 'REGISTERED_IMMUTABLE_IDENTITY',
                    **validation_identity_for_run(result),
                    'first_primary_capture_run_id': result['run_id'],
                    'first_signal_observation_date': prospective.get('signal_observation_date'),
                    'start_session': prospective.get('target_session'),
                    'started_at': utc_now().isoformat(),
                    'primary_signal_cutoff': prospective.get('primary_signal_cutoff'),
                    'checkpoints_signal_sessions': [5,20,60,120],
                    'no_tuning_rule': True,
                    'historical_vs_prospective_separation': 'RECONSTRUCTED_HISTORICAL_NOT_COUNTED',
                    'market_edge': 'NOT_ESTABLISHED',
                }
                self.store._put_document(connection, 'stage_aware_prospective_validation_epochs', epoch_id, epoch)

    def validation_state(self, validation_epoch_id=None):
        from .validation_identity import current_validation_identity
        current = current_validation_identity()
        selected_id = validation_epoch_id or current['validation_epoch_id']
        registered = self.store.get_document('stage_aware_prospective_validation_epochs', selected_id)
        identity = current if selected_id == current['validation_epoch_id'] else registered
        rows = [row for row in self._all('stage_aware_prospective_primary_runs')
                if row.get('validation_epoch_id') == selected_id and row.get('official_primary_capture') is True]
        attempted = {row['signal_observation_date'] for row in rows}
        failed_capture_days = set()
        # A failed capture has no signal row, but remains operational evidence.
        from .scheduler import SH
        for job in self._all('candidate_schedule_events'):
            if job.get('validation_epoch_id') == selected_id and job.get('market_session') == 'EVENING' and job.get('status') != 'MISSED_SERVICE_OFFLINE':
                attempt_day = datetime.fromisoformat(job['scheduled_at']).astimezone(SH).date().isoformat()
                attempted.add(attempt_day)
                saved_job = self.jobs.get(job.get('job_id') or job['event_id'])
                if saved_job and saved_job['state'] in {'FAILED','CANCELLED','INTERRUPTED'}:
                    failed_capture_days.add(attempt_day)
        evaluable = {row['signal_observation_date'] for row in rows if row.get('official_validation_signal_countable') is True}
        no_focus = {row['signal_observation_date'] for row in rows if row.get('official_validation_signal_countable') is True and (row.get('counts') or {}).get('focus') == 0}
        unavailable = ({row['signal_observation_date'] for row in rows if row.get('official_validation_signal_countable') is not True} | failed_capture_days) - evaluable
        return {'validation_epoch_id': selected_id, 'current_validation_epoch_id': current['validation_epoch_id'],
            'identity': identity, 'epoch': registered, 'read_only': True,
            'primary_capture_attempted_days': len(attempted), 'evaluable_official_signal_days': len(evaluable),
            'data_unavailable_primary_days': len(unavailable), 'valid_no_focus_days': len(no_focus),
            'failed_primary_capture_days': len(failed_capture_days),
            'capture_without_persisted_signal_days': len(attempted - {row['signal_observation_date'] for row in rows}),
            'latest_primary_capture': max(rows, key=lambda row: (row['signal_observation_date'], row.get('decision_cutoff') or ''), default=None)}

    def _strategy_experiment_job(self, ctx):
        payload=ctx.payload
        _,bundle=self.input(payload['input_id'])
        config=self.current_config()
        cutoff=datetime.fromisoformat(payload['cutoff'])
        ctx.update(.25,'运行量价观察控制组、股性复排和题材上下文实验')
        result = self._build_strategy_experiment_result(payload['input_id'], bundle, cutoff, config,
            market_session=payload.get('market_session'), validation_role=payload.get('validation_role', 'MANUAL_OR_REPLAY'),
            primary_signal_cutoff=payload.get('primary_signal_cutoff'), scheduler_job_id=payload.get('scheduler_job_id'))
        terminal={'strategy_experiment_run_id':result['run_id'],'run_id':result['run_id'],
            'status':'complete','quality':result['results']['PV_OBSERVATION_V1']['status'],
            'pv_count':len(result['results']['PV_OBSERVATION_V1'].get('items',()))}
        def write(connection):
            self._write_strategy_result(connection, result, created_at=payload['cutoff'])
        return ctx.complete(terminal, write)

    def _write_strategy_projection(self, connection, run):
        from .strategy_tracking import authoritative_strategy_items
        from .strategy_experiments import implementation_conformance
        assessment = implementation_conformance(run)
        self.store._put_document(connection, 'strategy_implementation_assessments', run['run_id'], assessment)
        focus = (run.get('selection_results') or {}).get('PV_FOCUS_PREVIEW_V0.1') or {}
        focus_ids = set(focus.get('selected_ids') or [])
        scalar_fields = {'instrument_id','name','rank','rank_percentiles','S_PV','S_DNA','S_THEME','S_PV_REFERENCE','reference_score','reference_rank',
                         'A','R','H','status','technical_status','theme_status','capital_status','hard_requirements_status','condition_statuses',
                          'shape_type','primary_stage','alternate_stages','liquidity_status','sector_status','funds_status','funds_grade',
                          'decision_status','watch_reasons','observation_bucket','setup_rank','display_order','rank_semantics','setup_rank_features',
                          'trend_state','market_context_state','market_compatibility_status','same_theme_context_status',
                         'behavioral_confirmation_status','critical_counterevidence_status','capital_evidence_status',
                         'formal_status','relative_strength_status','ret1_pct','ret3_pct','ret5_pct','ret10_pct','ret20_pct','CLV',
                         'amplitude_pct','low_return_pct','high_return_pct','close_return_pct','recovery_points_pct',
                         'low_to_close_rebound_pct','RS_market_1d_pct','RS_sector_1d_pct',
                         'amount_cny','turnover_fraction','close','open','high','low','comparable_close','comparable_basis',
                          'risk_flags','risk_review_status','board_state','limit_event_status','reason_codes','parameter_status_label_zh',
                          'primary_sector_absolute_level','primary_sector_relative_level','primary_sector_market_rs_pct',
                          'primary_sector_stock_role_known_feature_count','primary_sector_stock_role_unknown_feature_count',
                          'primary_sector_stock_role_evidence_coverage'}
        header = {k: run.get(k) for k in ('run_id','input_id','observation_date','implementation_version','parameter_status','generated_at','decision_cutoff','cutoff','spec_hash','strategy_spec_hash','coverage_type','spec_conformance',
            'validation_epoch_id','signal_implementation_version','signal_spec_revision','signal_config_hash','signal_source_hash','primary_cutoff_policy_id','prospective_validation','signal_data_quality')}
        header.update(source_run_hash=run.get('content_hash') or digest(run), comparison=run.get('comparison'), implementation_assessment=assessment,
                      focus_ids=list(focus.get('selected_ids') or []), selection_policy=focus.get('selection_policy'), results={}, selections={})
        for sid, rows in authoritative_strategy_items(run):
            result = ({**(run.get('results') or {}), **(run.get('evaluation_baselines') or {})}).get(sid) or {}
            header['results'][sid] = {k: result.get(k) for k in ('strategy_id','status','counts','scope_label','reason_codes','spec_hash','top_ids')}
            header['results'][sid]['item_count'] = len(rows)
            header['selections'][sid] = {}
            for selection in ('all', 'focus', 'unselected'):
                selected = self._selected_strategy_rows(rows, focus_ids, selection)
                header['selections'][sid][selection] = len(selected)
                for start in range(0, len(selected), 50):
                    page_id = run['run_id'] + '_' + digest({'strategy_id': sid, 'selection': selection, 'chunk': start//50})
                    self.store._put_document(connection, 'strategy_experiment_summary_pages', page_id,
                        {'run_id': run['run_id'], 'source_run_hash': header['source_run_hash'],
                         'items': [{k:v for k,v in row.items() if k in scalar_fields} for row in selected[start:start+50]]})
        self.store._put_document(connection, 'strategy_experiment_summaries', run['run_id'], header)

    def rebuild_strategy_projections(self):
        """Explicit local migration; normal list/summary never loads full rankings."""
        total, offset = 0, 0
        while True:
            rows = self.store.list_document_metadata('strategy_experiment_runs', limit=1000, offset=offset)
            for row in rows:
                if self.store.get_document('strategy_experiment_summaries', row['document_id']) is None:
                    run = self.strategy_experiment(row['document_id'])
                    with self.store.transaction() as connection:
                        self._write_strategy_projection(connection, run)
                    total += 1
            if len(rows) < 1000:
                break
            offset += len(rows)
        offset = 0
        while True:
            rows = self.store.list_document_metadata('strategy_research_reviews',limit=1000,offset=offset)
            for row in rows:
                if self.store.get_document('strategy_research_review_indexes',row['document_id']) is None:
                    value = self.store.get_document('strategy_research_reviews',row['document_id'])
                    with self.store.transaction() as connection:
                        self._write_research_review(connection,value)
            if len(rows)<1000:
                break
            offset += len(rows)
        return {'rebuilt': total}

    def list_strategy_experiments(self, limit=20, offset=0):
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError('invalid result page size')
        rows = self.store.list_document_metadata('strategy_experiment_runs', limit=limit, offset=offset, descending=True)
        return [self.store.get_document('strategy_experiment_summaries', row['document_id']) or
                {'run_id': row['document_id'], 'status': 'PROJECTION_REBUILD_REQUIRED'} for row in rows]

    def strategy_experiment(self, run_id):
        value = self.store.get_document('strategy_experiment_runs',run_id)
        if value is None:
            raise KeyError(run_id)
        return value

    def _strategy_result_summaries(self, run):
        return {sid: {k:v for k,v in result.items() if k not in {'items','incomplete','missing'}}
                for sid,result in (run.get('results') or {}).items()}

    def strategy_experiment_summary(self, run_id, *, page=1, page_size=50, strategy_id='PV_OBSERVATION_V1', selection=None):
        if not 1 <= page_size <= 500 or page < 1:
            raise ValueError('summary 分页大小必须为 1..500')
        selection = selection or 'all'
        if selection not in {'all','focus','unselected'}:
            raise ValueError('unknown strategy experiment selection')
        projection = self.store.get_document('strategy_experiment_summaries', run_id)
        if projection is None:
            raise UnavailableError('STRATEGY_SUMMARY_PROJECTION_REBUILD_REQUIRED', 'candidate')
        if strategy_id not in projection['results']:
            raise KeyError(strategy_id)
        total = projection['selections'][strategy_id][selection]
        start, stop = (page-1)*page_size, min(page*page_size, total)
        items = []
        if start < stop:
            for chunk in range(start//50, (stop-1)//50+1):
                key = run_id + '_' + digest({'strategy_id': strategy_id, 'selection': selection, 'chunk': chunk})
                data = self.store.get_document('strategy_experiment_summary_pages', key)
                if data is None or data['source_run_hash'] != projection['source_run_hash']:
                    raise UnavailableError('STRATEGY_SUMMARY_PROJECTION_MISMATCH', 'candidate')
                items.extend(data['items'][max(0,start-chunk*50):min(50,stop-chunk*50)])
        return {**projection, 'strategy_id': strategy_id, 'selection': selection,
                'status': projection['results'][strategy_id].get('status'), 'counts': projection['results'][strategy_id].get('counts'),
                'filtered_total': total, 'page': page, 'page_size': page_size, 'has_next': stop < total, 'items': items}

    def strategy_experiment_item_detail(self, run_id, instrument_id):
        run = self.strategy_experiment(run_id)
        matches = {}
        for strategy_id, result in (run.get('results') or {}).items():
            found = [row for row in result.get('items', []) if row.get('instrument_id') == instrument_id]
            if found:
                matches[strategy_id] = found
        focus = (run.get('selection_results') or {}).get('PV_FOCUS_PREVIEW_V0.1') or {}
        focus_matches = [row for row in focus.get('items', []) if row.get('instrument_id') == instrument_id]
        if not matches and not focus_matches:
            raise KeyError(instrument_id)
        return {
            'run_id': run_id,
            'instrument_id': instrument_id,
            'observation_date': run.get('observation_date'),
            'strategy_results': matches,
            'focus_selection': focus_matches[0] if focus_matches else None,
            'selection_policy': focus.get('selection_policy'),
            'outcomes': self._strategy_item_observations('strategy_trading_session_observations', run_id, instrument_id),
            'tracking': self._strategy_item_observations('research_episode_observations', run_id, instrument_id, latest=100),
            'market_edge': 'NOT_ESTABLISHED',
        }

    def _strategy_item_observations(self, namespace, run_id, instrument_id, *, latest=None):
        # Filter before deserializing; unrelated historical labels never enter memory.
        order = 'DESC' if latest is not None else 'ASC'
        sql = ('SELECT * FROM documents WHERE namespace=? '
               "AND json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.instrument_id')=? "
               f'ORDER BY created_at {order}, document_id {order}')
        parameters = [namespace, run_id, instrument_id]
        if latest is not None:
            sql += ' LIMIT ?'
            parameters.append(latest)
        with self.store.transaction(write=False) as connection:
            rows = [checked_payload(row) for row in connection.execute(sql, parameters)]
        return list(reversed(rows)) if latest is not None else rows

    @staticmethod
    def _selected_strategy_rows(rows, focus_ids, selection):
        selection = selection or 'all'
        if selection not in {'all', 'focus', 'unselected'}:
            raise ValueError('unknown strategy experiment selection')
        return [row for row in rows if selection == 'all' or
                (row['instrument_id'] in focus_ids) == (selection == 'focus')]

    def _strategy_export_rows(self, run, strategy_id, selection):
        from .strategy_tracking import authoritative_strategy_items
        focus = (run.get('selection_results') or {}).get('PV_FOCUS_PREVIEW_V0.1') or {}
        focus_ids = set(focus.get('selected_ids') or [])
        results = {**(run.get('results') or {}), **(run.get('evaluation_baselines') or {})}
        result = results.get(strategy_id)
        if result is None:
            result = next((value for key, value in results.items()
                           if (value.get('canonical_strategy_id') or value.get('strategy_id') or key) == strategy_id), None)
        if result is None:
            raise KeyError(strategy_id)
        if result.get('strategy_id') == 'STAGE_AWARE_MOMENTUM_V2' or result.get('full_eligible_pool_retained'):
            all_rows = list(result.get('items') or [])
            selection = selection or 'all'
            if selection not in {'all', 'focus', 'unselected'}:
                raise ValueError('unknown strategy experiment selection')
            rows = [row for row in all_rows if selection == 'all' or (row.get('decision_status') == 'FOCUS') == (selection == 'focus')]
            ranked = [row for row in all_rows if row.get('decision_status') == 'FOCUS']
            return result, rows, ranked, focus, focus_ids
        ranked = dict(authoritative_strategy_items(run))
        if strategy_id not in ranked:
            raise KeyError(strategy_id)
        rows = self._selected_strategy_rows(ranked[strategy_id], focus_ids, selection)
        return result, rows, ranked[strategy_id], focus, focus_ids

    def export_strategy_experiment_csv(self, run_id, *, strategy_id=None, selection=None):
        run = self.strategy_experiment(run_id)
        strategy_id = strategy_id or 'PV_OBSERVATION_V1'
        result, items, _, focus, focus_ids = self._strategy_export_rows(run, strategy_id, selection)
        focus_by_id = {row['instrument_id']: row for row in focus.get('items', [])}
        stream = io.StringIO(newline='')
        fields = ['run_id', 'observation_date', 'strategy_id', 'instrument_id', 'rank', 'S_PV', 'S_DNA', 'S_THEME', 'S_PV_REFERENCE',
                  'reference_score', 'reference_rank', 'A', 'R', 'H', 'status', 'technical_status', 'theme_status', 'capital_status',
                  'hard_requirements_status', 'formal_status', 'primary_stage', 'alternate_stages', 'liquidity_status', 'sector_status',
                  'funds_status', 'funds_grade', 'decision_status', 'watch_reasons', 'setup_rank', 'display_order', 'rank_semantics',
                  'trend_state', 'market_context_state', 'market_compatibility_status', 'same_theme_context_status',
                  'behavioral_confirmation_status', 'critical_counterevidence_status', 'capital_evidence_status',
                  'relative_strength_status', 'ret1_pct', 'ret3_pct', 'ret5_pct', 'ret10_pct', 'ret20_pct',
                  'CLV', 'amplitude_pct', 'low_return_pct', 'high_return_pct', 'close_return_pct', 'recovery_points_pct',
                  'low_to_close_rebound_pct', 'RS_market_1d_pct', 'RS_sector_1d_pct',
                  'shape_type', 'amount_cny', 'turnover_fraction', 'reason', 'reason_codes', 'selected_focus',
                  'focus_reason_codes', 'close_location', 'scope_label', 'parameter_status', 'parameter_status_label_zh']
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for item in items:
            focus_row = focus_by_id.get(item['instrument_id'], {})
            row = {
                'run_id': run_id,
                'observation_date': run.get('observation_date'),
                'strategy_id': strategy_id,
                'instrument_id': item.get('instrument_id'),
                'rank': item.get('rank'),
                'S_PV': item.get('S_PV'),
                'S_DNA': item.get('S_DNA'),
                'S_THEME': item.get('S_THEME'),
                'S_PV_REFERENCE': item.get('S_PV_REFERENCE'),
                'reference_score': item.get('reference_score'),
                'reference_rank': item.get('reference_rank'),
                'A': item.get('A'),
                'R': item.get('R'),
                'H': item.get('H'),
                'status': item.get('status'),
                'technical_status': item.get('technical_status'),
                'theme_status': item.get('theme_status'),
                'capital_status': item.get('capital_status'),
                'hard_requirements_status': item.get('hard_requirements_status'),
                'formal_status': item.get('formal_status'),
                'primary_stage': item.get('primary_stage'),
                'alternate_stages': '|'.join(item.get('alternate_stages') or []),
                'liquidity_status': item.get('liquidity_status'),
                'sector_status': item.get('sector_status'),
                'funds_status': item.get('funds_status'),
                'funds_grade': item.get('funds_grade'),
                'decision_status': item.get('decision_status'),
                'watch_reasons': '|'.join(item.get('watch_reasons') or []),
                'setup_rank': item.get('setup_rank'),
                'display_order': item.get('display_order'),
                'rank_semantics': item.get('rank_semantics'),
                'trend_state': item.get('trend_state'),
                'market_context_state': item.get('market_context_state'),
                'market_compatibility_status': item.get('market_compatibility_status'),
                'same_theme_context_status': item.get('same_theme_context_status'),
                'behavioral_confirmation_status': item.get('behavioral_confirmation_status'),
                'critical_counterevidence_status': item.get('critical_counterevidence_status'),
                'capital_evidence_status': item.get('capital_evidence_status'),
                'relative_strength_status': item.get('relative_strength_status'),
                'ret1_pct': item.get('ret1_pct'),
                'ret3_pct': item.get('ret3_pct'),
                'ret5_pct': item.get('ret5_pct'),
                'ret10_pct': item.get('ret10_pct'),
                'ret20_pct': item.get('ret20_pct'),
                'CLV': item.get('CLV'),
                'amplitude_pct': item.get('amplitude_pct'),
                'low_return_pct': item.get('low_return_pct'),
                'high_return_pct': item.get('high_return_pct'),
                'close_return_pct': item.get('close_return_pct'),
                'recovery_points_pct': item.get('recovery_points_pct'),
                'low_to_close_rebound_pct': item.get('low_to_close_rebound_pct'),
                'RS_market_1d_pct': item.get('RS_market_1d_pct'),
                'RS_sector_1d_pct': item.get('RS_sector_1d_pct'),
                'shape_type': item.get('shape_type'),
                'amount_cny': item.get('amount_cny'),
                'turnover_fraction': item.get('turnover_fraction'),
                'reason': item.get('reason'),
                'reason_codes': '|'.join(item.get('reason_codes') or []),
                'selected_focus': item['instrument_id'] in focus_ids,
                'focus_reason_codes': '|'.join(focus_row.get('reason_codes') or []),
                'close_location': focus_row.get('close_location'),
                'scope_label': result.get('scope_label'),
                'parameter_status': run.get('parameter_status'),
                'parameter_status_label_zh': item.get('parameter_status_label_zh'),
            }
            writer.writerow({k: "'" + v if isinstance(v, str) and v[:1] in '=+-@' else v for k, v in row.items()})
        return stream.getvalue()

    def export_strategy_experiment_json(self, run_id, *, strategy_id=None, selection=None):
        run = self.strategy_experiment(run_id)
        if strategy_id is not None or selection is not None:
            import copy
            strategy_id = strategy_id or 'PV_OBSERVATION_V1'
            original_result, items, ranked, _, focus_ids = self._strategy_export_rows(run, strategy_id, selection)
            source_content_hash = run.get('content_hash') or digest(run)
            run = copy.deepcopy(run)
            result = copy.deepcopy(original_result)
            ranked_ids = {row['instrument_id'] for row in ranked}
            diagnostics = [row for row in original_result.get('items', [])
                           if row.get('instrument_id') not in ranked_ids]
            result['coverage_diagnostics'] = copy.deepcopy(self._selected_strategy_rows(diagnostics, focus_ids, selection))
            result['items'] = copy.deepcopy(items)
            run['results'] = {strategy_id: result}
            run.pop('evaluation_baselines', None)
            run.pop('content_hash', None)
            run['export_view'] = {'strategy_id': strategy_id, 'selection': selection or 'all',
                                  'source_content_hash': source_content_hash,
                                  'payload_role': 'SAVED_RANKING_SELECTION_VIEW',
                                  'ranked_total': len(ranked), 'filtered_total': len(items)}
            run['view_content_hash'] = digest(run)
        return json.dumps(run, ensure_ascii=False, indent=2, allow_nan=False)

    def submit_strategy_outcomes(self, run_id, input_id=None, *, horizons=None, job_id=None):
        run = self.strategy_experiment(run_id)
        resolved, _ = self.strategy_input(input_id)
        horizons = tuple(horizons or (1, 2, 3, 4, 5, 10, 20))
        if any(type(h) is not int or h <= 0 for h in horizons):
            raise ValueError('unsupported research horizon')
        self.store.put_document('strategy_research_horizon_requests', run_id + '_' + digest(horizons),
            {'run_id':run_id, 'horizons':list(horizons)})
        return self.jobs.submit('strategy_outcomes', {'run_id': run_id, 'input_id': resolved,
            'horizons': list(horizons), 'evaluated_at': utc_now().isoformat()}, job_id=job_id)

    def _strategy_outcome_job(self, ctx):
        payload = ctx.payload
        run = self.strategy_experiment(payload['run_id'])
        _, bundle = self.input(payload['input_id'])
        from .strategy_tracking import evaluate_strategy_experiment_outcomes, build_strategy_feedback_report
        from .recommendation_scoring import build_score_revision
        evaluated_at = datetime.fromisoformat(payload['evaluated_at'])
        values = evaluate_strategy_experiment_outcomes(run, bundle, datetime.fromisoformat(payload['evaluated_at']),
                                                       horizons=tuple(payload.get('horizons') or (1, 2, 3, 4, 5, 10, 20)))
        all_outcomes = self._all('strategy_trading_session_observations') + values
        report = build_strategy_feedback_report(run, all_outcomes,
            self._all('strategy_tracking_runs'), evaluated_at,
            runs=self._all('strategy_experiment_runs'), sessions=bundle.sessions)
        score, score_error = None, None
        try:
            score = build_score_revision(run, all_outcomes, evaluated_at, existing_revisions=self._all('recommendation_score_revisions'))
        except Exception as exc:
            score_error = {'code': 'RECOMMENDATION_SCORE_FAILED', 'type': type(exc).__name__}
        def write(connection):
            for value in values:
                self.store._put_document(connection, 'strategy_trading_session_observations', value['observation_id'], value)
            self._write_research_review(connection, report)
            if score is not None:
                self.store._put_document(connection, 'recommendation_score_revisions', score['score_run_id'], score)
        return ctx.complete({'run_id': run['run_id'], 'observations': len(values),
                             'horizon_unit': 'exchange_trading_sessions',
                             'recommendation_score_run_id': score.get('score_run_id') if score else None,
                             'recommendation_score_error': score_error}, write)

    def strategy_outcomes(self, run_id):
        self.strategy_experiment(run_id)
        return [v for v in self._all('strategy_trading_session_observations') if v.get('run_id') == run_id]

    def recommendation_scores(self, *, limit=20, offset=0, tier=None, setup=None, horizon=None, evidence_type=None, validation_epoch_id=None):
        from .recommendation_scoring import flatten_score_documents, summarize_score_history, OFFICIAL_PROSPECTIVE
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError('invalid score page size')
        horizon_value = int(horizon) if horizon not in (None, '') else None
        validation = self.validation_state(validation_epoch_id)
        validation_epoch_id = validation['validation_epoch_id']
        documents = sorted(self._all('recommendation_score_revisions'),
                           key=lambda row: (row.get('signal_observation_date') or '', row.get('revision') or 0, row.get('generated_at') or '', row.get('score_run_id') or ''),
                           reverse=True)
        filtered = []
        for document in documents:
            if document.get('validation_epoch_id') != validation_epoch_id:
                continue
            if evidence_type and document.get('evidence_type') != evidence_type:
                continue
            rows = flatten_score_documents([document], tier=tier, setup=setup, horizon=horizon_value)
            if tier or setup or horizon_value is not None:
                if not rows:
                    continue
                filtered.append({**document, 'records': rows})
            else:
                filtered.append(document)
        page = filtered[offset:offset + limit]
        return {
            'schema_version': 'R12_RECOMMENDATION_SCORE_LIST_API_V1',
            'score_spec_id': 'DAILY_RECOMMENDATION_SCORE_V1',
            'items': page,
            'total_matching_documents': len(filtered),
            'limit': limit,
            'offset': offset,
            'has_next': offset + limit < len(filtered),
            'filters': {'tier': tier, 'setup': setup, 'horizon': horizon_value, 'evidence_type': evidence_type, 'validation_epoch_id': validation_epoch_id},
            'validation': validation,
            'official_history': summarize_score_history(documents, evidence_type=OFFICIAL_PROSPECTIVE, validation_epoch_id=validation_epoch_id),
            'market_edge': 'NOT_ESTABLISHED',
        }

    def latest_recommendation_scores(self, *, tier=None, setup=None, horizon=None, evidence_type=None, validation_epoch_id=None):
        from .recommendation_scoring import summarize_score_history, OFFICIAL_PROSPECTIVE, RECONSTRUCTED_HISTORICAL
        values = self.recommendation_scores(limit=1, tier=tier, setup=setup, horizon=horizon, evidence_type=evidence_type, validation_epoch_id=validation_epoch_id)
        validation_epoch_id = values['filters']['validation_epoch_id']
        documents = self._all('recommendation_score_revisions')
        return {
            'schema_version': 'R12_RECOMMENDATION_SCORE_LATEST_API_V1',
            'latest': values['items'][0] if values['items'] else None,
            'filters': values['filters'],
            'validation': values['validation'],
            'official_prospective_score': summarize_score_history(documents, evidence_type=OFFICIAL_PROSPECTIVE, validation_epoch_id=validation_epoch_id),
            'reconstructed_historical_score': summarize_score_history(documents, evidence_type=RECONSTRUCTED_HISTORICAL, validation_epoch_id=validation_epoch_id),
            'score_spec_id': 'DAILY_RECOMMENDATION_SCORE_V1',
            'authority': 'SAVED_SCORE_REVISION_DOCUMENTS_ONLY_NO_CLIENT_RECOMPUTATION',
            'market_edge': 'NOT_ESTABLISHED',
        }

    def recommendation_scores_for_run(self, run_id, *, tier=None, setup=None, horizon=None, evidence_type=None, validation_epoch_id=None):
        run = self.strategy_experiment(run_id)
        from .validation_identity import validation_identity_for_run
        validation_epoch_id = validation_epoch_id or validation_identity_for_run(run)['validation_epoch_id']
        result = self.recommendation_scores(limit=1000, tier=tier, setup=setup, horizon=horizon, evidence_type=evidence_type, validation_epoch_id=validation_epoch_id)
        result['items'] = [row for row in result['items'] if row.get('signal_run_id') == run_id]
        result['total_matching_documents'] = len(result['items'])
        result['run_id'] = run_id
        result['has_next'] = False
        return result

    def submit_strategy_tracking(self, run_id, input_id=None, *, horizons=None, job_id=None):
        run = self.strategy_experiment(run_id)
        resolved, _ = self.strategy_input(input_id)
        horizons = tuple(horizons or (1, 2, 3, 4, 5, 10, 20))
        if any(type(h) is not int or h <= 0 for h in horizons):
            raise ValueError('unsupported research horizon')
        self.store.put_document('strategy_research_horizon_requests', run_id + '_' + digest(horizons),
            {'run_id':run_id, 'horizons':list(horizons)})
        return self.jobs.submit('strategy_tracking', {'run_id': run_id, 'input_id': resolved,
            'horizons': list(horizons), 'evaluated_at': utc_now().isoformat()}, job_id=job_id)

    def _strategy_tracking_job(self, ctx):
        payload = ctx.payload
        run = self.strategy_experiment(payload['run_id'])
        _, bundle = self.input(payload['input_id'])
        from .strategy_tracking import OutcomeContext, build_strategy_feedback_report, build_strategy_tracking
        from .recommendation_scoring import build_score_revision
        evaluated_at = datetime.fromisoformat(payload['evaluated_at'])
        context = OutcomeContext(bundle, evaluated_at)
        tracking = build_strategy_tracking(run, bundle, evaluated_at,
                                           horizons=tuple(payload.get('horizons') or (1, 2, 3, 4, 5, 10, 20)),
                                           existing_episodes=self._all('research_episodes'),
                                           prior_observations=self._all('research_episode_observations'),
                                           episode_events=self._all('research_episode_events'), _context=context)
        # One tracking task computes labels first; feedback cannot race an earlier outcome task.
        from .strategy_tracking import evaluate_strategy_experiment_outcomes
        new_outcomes = evaluate_strategy_experiment_outcomes(run, bundle, evaluated_at,
                                                           horizons=tuple(payload.get('horizons') or (1,2,3,4,5,10,20)), _context=context)
        outcomes = self._all('strategy_trading_session_observations') + new_outcomes + tracking['shadow_outcomes']
        review = build_strategy_feedback_report(run, outcomes, self._all('strategy_tracking_runs') + [tracking],
                                                datetime.fromisoformat(payload['evaluated_at']),
                                                runs=self._all('strategy_experiment_runs'), sessions=bundle.sessions)
        score, score_error = None, None
        try:
            score = build_score_revision(run, outcomes, evaluated_at, existing_revisions=self._all('recommendation_score_revisions'))
        except Exception as exc:
            score_error = {'code': 'RECOMMENDATION_SCORE_FAILED', 'type': type(exc).__name__}
        def write(connection):
            for value in new_outcomes + tracking['shadow_outcomes']:
                self.store._put_document(connection, 'strategy_trading_session_observations', value['observation_id'], value)
            for value in tracking['signals']:
                self.store._put_document(connection, 'research_episode_signals', value['signal_record_id'], value)
            for value in tracking['episodes']:
                self.store._put_document(connection, 'research_episodes', value['episode_id'], value)
            for value in tracking['observations']:
                obs_id = value['observation_id']
                self.store._put_document(connection, 'research_episode_observations', obs_id, value)
            self.store._put_document(connection, 'strategy_tracking_runs', tracking['tracking_run_id'], tracking)
            self._write_research_review(connection, review)
            if score is not None:
                self.store._put_document(connection, 'recommendation_score_revisions', score['score_run_id'], score)
        return ctx.complete({'run_id': run['run_id'], 'tracking_run_id': tracking['tracking_run_id'],
                             'episodes': tracking['episode_count'], 'observations': tracking['observation_count'],
                             'review_id': review['review_id'],
                             'recommendation_score_run_id': score.get('score_run_id') if score else None,
                             'recommendation_score_error': score_error}, write)

    def strategy_tracking(self, run_id):
        self.strategy_experiment(run_id)
        return [v for v in self._all('strategy_tracking_runs') if v.get('run_id') == run_id]

    def _write_research_review(self, connection, review):
        self.store._put_document(connection, 'strategy_research_reviews', review['review_id'], review)
        projection = {k:v for k,v in review.items() if k not in {'revision_selection','rolling_windows'}}
        projection['rolling_windows'] = {key:{k:v for k,v in window.items() if k not in {'date_diagnostics','same_date_common_eligibility'}}
                                         for key,window in review.get('rolling_windows',{}).items()}
        projection['full_report_available'] = True
        self.store._put_document(connection, 'strategy_research_review_summaries', review['review_id'], projection)
        self.store._put_document(connection, 'strategy_research_review_indexes', review['review_id'],
            {'review_id':review['review_id'],'run_id':review['run_id'],'evaluated_at':review['evaluated_at']})

    def strategy_review(self, run_id, *, full=False):
        projection = self.store.get_document('strategy_experiment_summaries', run_id)
        if projection is None:
            raise KeyError(run_id)
        indexes = [r for r in self._all('strategy_research_review_indexes') if r['run_id']==run_id]
        if indexes:
            latest = max(enumerate(indexes),key=lambda pair:(pair[1]['evaluated_at'],pair[0]))[1]
            namespace = 'strategy_research_reviews' if full else 'strategy_research_review_summaries'
            return self.store.get_document(namespace,latest['review_id'])
        return {'schema_version':'R05_RESEARCH_FEEDBACK_V2','run_id':run_id,
                'observation_date':projection.get('observation_date'),'status':'PENDING_MARKET_VALIDATION',
                'market_edge':'NOT_ESTABLISHED','rolling_windows':{},'counts':{'mature':0,'outcome_observations':0},
                'reason_codes':['OUTCOME_OR_TRACKING_REVIEW_NOT_RUN'], 'full_report_available':False}

    def latest_research_feedback(self):
        rows = self.store.list_document_metadata('strategy_research_review_indexes',limit=1,descending=True)
        if rows:
            return self.store.get_document('strategy_research_review_summaries',rows[0]['document_id'])
        latest = self.store.list_document_metadata('strategy_experiment_summaries', limit=1, descending=True)
        if latest:
            return self.strategy_review(latest[0]['document_id'])
        return {'schema_version':'R05_RESEARCH_FEEDBACK_V2','status':'UNAVAILABLE',
                'reason_codes':['NO_STRATEGY_EXPERIMENT_RUN_SAVED'],'market_edge':'NOT_ESTABLISHED'}

    def latest_strategy_comparison(self):
        rows=self.store.list_document_metadata('strategy_experiment_runs',limit=1,descending=True)
        if not rows:
            return {'comparison_version':'R05_STRATEGY_COMPARISON_V1','status':'UNAVAILABLE',
                'reason_codes':['NO_STRATEGY_EXPERIMENT_RUN_SAVED'],
                'parameter_status':'INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED'}
        return (self.store.get_document('strategy_experiment_summaries',rows[0]['document_id']) or {}).get('comparison', {'status':'PROJECTION_REBUILD_REQUIRED'})

    def pending_strategy_research(self):
        """Durable work inventory, including pre-index runs; no latest-N cutoff."""
        rows, offset = [], 0
        requests = self._all('strategy_research_horizon_requests')
        while True:
            page = self.store.list_document_metadata('strategy_experiment_runs', limit=1000, offset=offset)
            for item in page:
                pending = self.store.get_document('strategy_research_pending', item['document_id'])
                if pending is None:
                    run = self.strategy_experiment(item['document_id'])
                    pending = {'run_id': run['run_id'], 'observation_date': run['observation_date'],
                               'horizons': [1,2,3,4,5,10,20], 'selection_policy_id': 'PV_FOCUS_PREVIEW_V0.1'}
                    self.store.put_document('strategy_research_pending', run['run_id'], pending)
                custom = {h for r in requests if r['run_id'] == pending['run_id'] for h in r['horizons']}
                rows.append({**pending, 'horizons': sorted(set(pending['horizons']) | custom)})
            if len(page) < 1000:
                return sorted(rows, key=lambda r: (r['observation_date'],r['run_id']))
            offset += len(page)

    def experiment_outcome_demand(self, observation_date, sessions):
        from .strategy_tracking import authoritative_strategy_items, select_label_revisions
        labels = select_label_revisions(self._all('strategy_trading_session_observations'), utc_now())
        indexed = {}
        for label in labels:
            indexed.setdefault((label['run_id'], label['instrument_id']), []).append(label)
        demand = []
        days = sorted(sessions)
        for pending in self.pending_strategy_research():
            if date.fromisoformat(pending['observation_date']) > observation_date:
                continue
            run = self.strategy_experiment(pending['run_id'])
            ids = {item['instrument_id'] for _,items in authoritative_strategy_items(run) for item in items}
            for iid in sorted(ids):
                saved = indexed.get((run['run_id'],iid), [])
                fields = {}
                for horizon in pending['horizons']:
                    candidates = [r for r in saved if r['horizon'] == horizon]
                    missing = []
                    for field in ('close_reference_change','next_open_gap','mfe_bound','mae_bound','any_close_limit_up','any_close_limit_down'):
                        if not candidates or any(r.get('metrics',{}).get(field) is None for r in candidates):
                            missing.append('metrics.' + field)
                    if missing:
                        fields[str(horizon)] = missing
                focus = iid in set(((run.get('selection_results') or {}).get('PV_FOCUS_PREVIEW_V0.1') or {}).get('selected_ids') or [])
                if fields or focus:
                    start = date.fromisoformat(run['observation_date'])
                    prior = [d for d in days if d <= start]
                    demand.append({'run_id': run['run_id'], 'instrument_id': iid, 'observation_date': str(start),
                                   'horizons': [int(h) for h in fields], 'pending_fields': fields,
                                   'start_date': str(prior[-20]) if focus and len(prior)>=20 else str(start),
                                   'end_date': str(observation_date), 'selection_reason': 'R05_PENDING_LABEL_OR_EPISODE_INCLUDING_EXIT_SHADOW'})
        # Episodes survive discovery changes and keep actual-exit shadow requirements.
        for episode in self._all('research_episodes'):
            iid = episode['instrument_id']
            history = [r for r in self._all('research_episode_observations') if r['episode_id'] == episode['episode_id']]
            exits = [r['session'] for r in history if r.get('state') in {'THESIS_INVALIDATED','ARCHIVED'}]
            exits += [r['effective_session'] for r in self._all('research_episode_events') if r['episode_id'] == episode['episode_id'] and r.get('state') == 'ARCHIVED']
            start = min(exits) if exits else episode['observation_date']
            saved = [r for r in labels if r.get('episode_id') == episode['episode_id'] and r.get('label_kind') == 'POST_EXIT_SHADOW']
            horizons = [h for h in (5,10) if not any(r['horizon']==h and r.get('metrics',{}).get('close_reference_change') is not None for r in saved)] if exits else [5,10]
            if not exits or horizons:
                demand.append({'run_id': episode['run_id'], 'episode_id': episode['episode_id'], 'instrument_id':iid,
                               'observation_date':start, 'horizons':horizons, 'pending_fields':{str(h):['EPISODE_OR_EXIT_SHADOW_PENDING'] for h in horizons},
                               'start_date':start, 'end_date':str(observation_date), 'selection_reason':'R05_PERSISTED_EPISODE_OR_ACTUAL_EXIT_SHADOW'})
        return demand

    def archive_strategy_episode(self, episode_id, reason):
        episode = self.store.get_document('research_episodes', episode_id)
        if episode is None:
            raise KeyError(episode_id)
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ValueError('archive reason required, at most 500 characters')
        _, bundle = self.strategy_input()
        from zoneinfo import ZoneInfo
        local = utc_now().astimezone(ZoneInfo('Asia/Shanghai'))
        dates = [d for d in bundle.sessions if d < local.date() or d == local.date() and local.time().replace(tzinfo=None) >= time(15)]
        if not dates:
            raise UnavailableError('ARCHIVE_EXCHANGE_SESSION_UNAVAILABLE', 'candidate')
        value = {'episode_id':episode_id, 'state':'ARCHIVED', 'effective_session':str(max(dates)),
                 'recorded_at':utc_now().isoformat(), 'reason':reason.strip(), 'outcomes_retained':True}
        value['event_id'] = 'episode_archive_' + digest(value)
        self.store.put_document('research_episode_events', value['event_id'], value)
        return value

    def item_detail(self, run_id, instrument_id):
        run=self.get_run(run_id)
        matches=[item for item in run.items if item.instrument_id==instrument_id]
        if len(matches)!=1:
            raise KeyError(instrument_id)
        return {'run_id':run.run_id,'observation_date':run.observation_date.isoformat(),
            'decision_cutoff':run.decision_cutoff.isoformat(),'semantic_hash':run.semantic_hash,
            'item':matches[0].model_dump(mode='json')}

    def family(self, run_id, family):
        if family not in FAMILIES:
            raise ValueError('未知策略族')
        result = self.get_run(run_id)
        return [x for x in result.items if x.primary_family == family]

    def register_auction(self, quotes: tuple[AuctionSnapshot, ...], *, coverage_type: str, receipts=(), sessions=()):
        if coverage_type == 'TEST_FIXTURE' and not self.allow_test_fixture:
            raise ValueError('fixture auction must use isolated state')
        allowed = {'PROSPECTIVE_CAPTURED', 'RECONSTRUCTED_HISTORICAL', 'UNVERIFIABLE_AS_OF', 'TEST_FIXTURE'}
        if coverage_type not in allowed:
            raise ValueError('unknown auction coverage type')
        value = {'quotes': [AuctionSnapshot.model_validate(q.model_dump(mode='json')).model_dump(mode='json') for q in quotes],
                 'coverage_type': coverage_type,
                 'receipts':[SourceReceipt.model_validate(r).model_dump(mode='json') for r in receipts],
                 'sessions':[str(d) for d in sessions]}
        snapshot_id = 'auction_' + digest(value)
        self.store.put_document('auction_inputs', snapshot_id, value)
        return snapshot_id

    def submit_auction(self, run_id, snapshot_id=None, *, job_id=None):
        run = self.get_run(run_id)
        if snapshot_id is not None and self.store.get_document('auction_inputs', snapshot_id) is None:
            raise KeyError(snapshot_id)
        return self.jobs.submit('auction_review', {'run_id': run.run_id, 'snapshot_id': snapshot_id,
                                                 'evaluated_at': utc_now().isoformat()}, job_id=job_id)

    def _auction_job(self, ctx):
        payload = ctx.payload
        run = self.get_run(payload['run_id'])
        raw = self.store.get_document('auction_inputs', payload['snapshot_id']) if payload['snapshot_id'] else None
        quotes = tuple(AuctionSnapshot.model_validate(q) for q in raw['quotes']) if raw else ()
        result = evaluate_auction(run, quotes, datetime.fromisoformat(payload['evaluated_at']),
            receipts=raw.get('receipts',()) if raw else (),sessions=raw.get('sessions',()) if raw else (),
            coverage_type=raw.get('coverage_type') if raw else None)
        value = {'review_id': 'auction_review_' + ctx.job_id, 'run_id': run.run_id,
                 'snapshot_id': payload['snapshot_id'], 'coverage_type': raw['coverage_type'] if raw else 'UNAVAILABLE',
                 'evaluations': [e.model_dump(mode='json') for e in result], 'original_semantic_hash': run.semantic_hash,
                 'status': 'UNAVAILABLE' if not result or all(e.status == 'UNAVAILABLE' for e in result)
                           else 'EVALUATED' if all(e.status == 'EVALUATED' for e in result) else 'PARTIAL',
                 'evaluated_at': payload['evaluated_at']}
        terminal = {'review_id': value['review_id'], 'candidate_run_id': run.run_id, 'quality': value['status']}
        return ctx.complete(terminal, lambda connection: self.store._put_document(connection, 'auction_reviews', value['review_id'], value))

    def submit_outcomes(self, run_id, input_id=None, *, job_id=None):
        self.get_run(run_id)
        resolved, _ = self.input(input_id)
        return self.jobs.submit('candidate_outcomes', {'run_id': run_id, 'input_id': resolved,
            'evaluated_at': utc_now().isoformat()}, job_id=job_id)

    def _outcome_job(self, ctx):
        payload = ctx.payload
        run = self.get_run(payload['run_id'])
        _, bundle = self.input(payload['input_id'])
        values = evaluate_outcomes(run, bundle, datetime.fromisoformat(payload['evaluated_at']))
        def write(connection):
            for value in values:
                self.store._put_document(connection, 'trading_session_observations', value.observation_id, value.model_dump(mode='json'))
        return ctx.complete({'candidate_run_id': run.run_id, 'observations': len(values), 'horizon_unit': 'exchange_trading_sessions'}, write)

    def outcomes(self, run_id):
        self.get_run(run_id)
        return [v for v in self._all('trading_session_observations') if v['run_id'] == run_id]

    def auction_reviews(self, run_id):
        self.get_run(run_id)
        return [v for v in self._all('auction_reviews') if v['run_id'] == run_id]

    def _coverage_selection(self):
        activations=self._all('candidate_input_activations')
        if activations:
            return activations[-1]['input_id'], True
        rows=self.store.list_document_metadata('candidate_inputs',limit=1,descending=True)
        if not rows:
            raise UnavailableError('VERIFIED_RULE_INPUT_NOT_SELECTED','candidate')
        return rows[0]['document_id'], False

    def _latest_input_publication(self,input_id):
        # Search authority records, not the last N runs of unrelated inputs.
        # Routing metadata is never sufficient authority: get_run verifies the
        # full selected publication, immutable run, and successful job.
        offset=0
        while True:
            rows=self.store.list_document_metadata('candidate_publications',limit=1000,offset=offset,descending=True)
            for row in rows:
                authority=self.store.get_document('candidate_publications',row['document_id'])
                if authority and authority['input_id']==input_id:
                    return self.get_run(row['document_id'])
            if len(rows)<1000:
                return None
            offset+=len(rows)

    def health(self, *, storage_health=None):
        """Lightweight routing state; full readiness is the coverage endpoint.

        Application health supplies the store's freshly checked payload scope.
        No cached candidate readiness or market rows are passed off as checked.
        """
        storage_health=storage_health or {}
        try:
            input_id,active=self._coverage_selection()
            state={'status':'REGISTERED_INPUT_PRESENT','input_id':input_id,'active':active}
        except UnavailableError as exc:
            state={'status':'UNAVAILABLE','reason_codes':[exc.reason]}
        return {**state,'coverage_check':'NOT_PERFORMED_USE_UNIVERSE_COVERAGE',
                'payload_integrity_status':storage_health.get('status','NOT_CHECKED'),
                'payload_integrity_checked_at':storage_health.get('checked_at'),
                'market_edge':'NOT_ESTABLISHED','account_required':False,'qlib_required':False,'llm_required':False}

    def coverage(self):
        # One compact projection, never a retained full-history MarketInput.
        # Concurrent page polls do not decode the same large input in parallel.
        with self._coverage_lock:
            return self._coverage_checked()

    def _coverage_checked(self):
        try:
            input_id,active=self._coverage_selection()
            cached=self._coverage_projection_cache
            if cached is not None and cached[0]==input_id:
                verified=self.store.verified_document_metadata('candidate_inputs',input_id,expected_hash=input_id[7:])
                if verified is None:
                    raise UnavailableError('RULE_INPUT_MISSING_OR_CORRUPT','candidate')
                _,bundle,bar_rows,theme_snapshots,security_rows=cached
            else:
                _,full_input=self.input(input_id)
                bar_rows,theme_snapshots=len(full_input.bars),len(full_input.themes)
                security_rows=len(full_input.universe.securities)
                if full_input.history_artifact_ref:
                    manifest = full_input._history_store.manifest(full_input.history_artifact_ref, full_input.history_artifact_hash)
                    bar_rows = sum(part['bar_count'] for part in manifest['partitions'])
                    security_rows = sum(part['row_count'] - part['bar_count'] for part in manifest['partitions'])
                bundle=full_input.model_copy(update={
                    'bars':tuple(b for b in full_input.bars if b.session==full_input.observation_date),
                    'universe':full_input.universe.model_copy(update={'securities':()}),
                    'themes':(), 'funds':(), 'previous_emotions':()})
                self._coverage_projection_cache=(input_id,bundle,bar_rows,theme_snapshots,security_rows)
                del full_input
            latest_run=self._latest_input_publication(input_id)
            runs=[latest_run] if latest_run is not None else []
            from .events import limit_event
            from .visibility import EvidenceResolver
            cutoff = utc_now()
            resolver=EvidenceResolver(bundle.receipts,cutoff=cutoff,coverage_type=bundle.coverage_type,sessions=bundle.sessions,observation_date=bundle.observation_date)
            preview_bars = {}
            for bar in sorted(bundle.bars, key=lambda b: b.ingested_at):
                if bar.session == bundle.observation_date and resolver.is_visible(bar,instrument_id=bar.instrument_id,session=bar.session,require_session=True):
                    preview_bars[bar.instrument_id] = bar
            previews = [{'instrument_id': iid, 'close': str(bar.close) if bar.close is not None else None,
                         'event': limit_event(bar).model_dump(mode='json'), 'source_refs': bar.source_refs}
                        for iid, bar in sorted(preview_bars.items())]
            freshness=self.input_freshness(bundle)
            return {**freshness,'status': 'STALE' if active and freshness['stale'] else (runs[0].status if runs else 'REGISTERED_NOT_YET_COMPUTED') if active else 'UNAVAILABLE',
                    'input_id': input_id, 'active': active, 'consumer_status': runs[0].status if runs else 'NOT_COMPUTED',
                    'reason_codes': [] if active else ['DIAGNOSTIC_INPUT_NOT_ACTIVATED_FOR_DAILY_CONSUMER'],
                    'observation_date': bundle.observation_date.isoformat(), 'coverage_type': bundle.coverage_type,
                    'universe_declared_complete': bundle.universe.complete, 'universe_declared_count': bundle.universe.expected_count,
                    'security_rows': security_rows, 'bar_rows': bar_rows, 'theme_snapshots': theme_snapshots,
                    'history_artifact_ref': bundle.history_artifact_ref, 'history_artifact_hash': bundle.history_artifact_hash,
                    'current_preview_bar_rows': len(bundle.bars), 'row_count_scope': 'EFFECTIVE_HISTORY_PLUS_RECENT_PIT_DEDUPLICATED',
                    'current_event_previews': previews, 'preview_semantics': 'RAW_FACTS_NOT_FULL_STRATEGY_CANDIDATES',
                    'receipts': [r.model_dump(mode='json') for r in bundle.receipts],
                    'coverage': runs[0].coverage if runs else None,
                    'market_edge': 'NOT_ESTABLISHED', 'account_required': False, 'qlib_required': False, 'llm_required': False}
        except UnavailableError as exc:
            return {'status': 'UNAVAILABLE', 'reason_codes': [exc.reason], 'coverage': None,
                    'market_edge': 'NOT_ESTABLISHED', 'account_required': False, 'qlib_required': False, 'llm_required': False}

    def raw_instrument_evidence(self, instrument_id, *, run_id=None, input_id=None):
        from .events import limit_event
        cutoff = utc_now()
        if run_id:
            saved = self.get_run(run_id)
            cutoff = saved.decision_cutoff
            authority = self.store.get_document('candidate_publications', run_id)
            if input_id is not None and input_id != authority['input_id']:
                raise ValueError('run evidence must use its frozen input')
            input_id = authority['input_id']
        resolved, bundle = self.input(input_id)
        from .visibility import EvidenceResolver
        resolver=EvidenceResolver(bundle.receipts,cutoff=cutoff,coverage_type=bundle.coverage_type,sessions=bundle.sessions,observation_date=bundle.observation_date)
        bars = [b for b in bundle.bars if b.instrument_id == instrument_id and resolver.is_visible(b,instrument_id=instrument_id,session=b.session,require_session=True)]
        revisions = {}
        for bar in sorted(bars, key=lambda b: b.ingested_at):
            revisions[bar.session] = bar
        current = revisions.get(bundle.observation_date)
        envelope=bundle.universe.model_dump(mode='json',exclude={'securities'})
        records = [s for s in bundle.universe.securities if s.instrument_id == instrument_id and resolver.is_visible(s,instrument_id=instrument_id)] if resolver.is_visible(envelope) else []
        receipts=resolver.receipts
        refs = set(ref for b in revisions.values() for ref in b.source_refs) | set(ref for s in records for ref in s.source_refs)
        return {'record_type': 'MARKET_EVIDENCE', 'instrument_id': instrument_id, 'input_id': resolved,
                'decision_cutoff': cutoff.isoformat(), 'coverage_type': bundle.coverage_type,
                'status': 'FACT_PREVIEW_NOT_FULL_STRATEGY_SCORE',
                'security_records': [s.model_dump(mode='json') for s in records],
                'daily_bars': [revisions[d].model_dump(mode='json') for d in sorted(revisions)],
                'current_limit_event': limit_event(current).model_dump(mode='json') if current else None,
                'source_receipts': [receipts[ref].model_dump(mode='json') for ref in sorted(refs)]}

    def export_csv(self, run_id):
        run = self.get_run(run_id)
        stream = io.StringIO(newline='')
        fields = ['run_id', 'observation_date', 'coverage_type', 'profile', 'instrument_id', 'family', 'rank', 'tier', 'score',
                  'B', 'V', 'T', 'L', 'G', 'M', 'selected_display', 'selected_focus', 'reason_codes', 'missing']
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for item in run.items:
            row = dict(run_id=run.run_id, observation_date=run.observation_date, coverage_type=run.coverage_type, profile=run.profile,
                instrument_id=item.instrument_id, family=item.primary_family, rank=item.family_rank, tier=item.tier, score=item.score,
                selected_display=item.instrument_id in run.display_ids, selected_focus=item.instrument_id in run.focus_ids,
                reason_codes='|'.join(item.reason_codes), missing='|'.join(item.missing))
            row.update({key: item.components.get(key) for key in ('B', 'V', 'T', 'L', 'G', 'M')})
            # Text derived from provider fields must not become a spreadsheet formula.
            writer.writerow({k: "'" + v if isinstance(v, str) and v[:1] in '=+-@' else v for k, v in row.items()})
        return stream.getvalue()
