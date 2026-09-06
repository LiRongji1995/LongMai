"""One immutable candidate publication for API, UI, exports, and observations."""
from __future__ import annotations

from datetime import date, datetime, time
import csv
import io
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
        bundle = MarketInput.model_validate(bundle.model_dump(mode='json'))
        if bundle.coverage_type == 'TEST_FIXTURE' and not self.allow_test_fixture:
            raise ValueError('test fixtures require an explicitly isolated service')
        input_id = 'market_' + digest(bundle)
        self.store.put_document('candidate_inputs', input_id, bundle.model_dump(mode='json'))
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
        if bundle.coverage_type == 'TEST_FIXTURE' and not self.allow_test_fixture:
            raise UnavailableError('FIXTURE_NOT_ALLOWED_IN_USER_HISTORY', 'candidate')
        if observation_date is not None and bundle.observation_date != observation_date:
            raise ValueError('registered input observation date mismatch')
        return input_id, bundle

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
        ctx.update(.9, '规则计算完成；原子保存候选事实与任务终态')
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
        def publish(connection):
            self.store._put_document(connection, 'candidate_runs', result.run_id, value)
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
                     'activated': activation_allowed, 'consumer_status': result.status})
        return ctx.complete(terminal, publish)

    def get_run(self, run_id):
        value = self.store.get_document('candidate_runs', run_id)
        authority = self.store.get_document('candidate_publications', run_id)
        if value is None:
            raise KeyError(run_id)
        if authority is None or authority['run_hash'] != digest(value):
            raise UnavailableError('CANDIDATE_PUBLICATION_AUTHORITY_MISSING', 'candidate')
        job = self.jobs.get(authority['job_id'])
        if job is None or job['state'] != 'SUCCEEDED' or (job['result'] or {}).get('candidate_run_id') != run_id:
            raise UnavailableError('CANDIDATE_SUCCESSFUL_JOB_REQUIRED', 'candidate')
        return CandidateRun.model_validate(value)

    def list_runs(self, limit=100):
        if not 1 <= limit <= 10000:
            raise ValueError('invalid result page size')
        rows = self.store.list_document_metadata('candidate_runs', limit=limit, descending=True)
        return [self.get_run(r['document_id']) for r in rows]

    def family(self, run_id, family):
        if family not in FAMILIES:
            raise ValueError('unknown strategy family')
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
