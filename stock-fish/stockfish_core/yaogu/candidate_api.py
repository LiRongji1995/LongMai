"""Candidate-only HTTP routes. Trust and file ingestion remain local-admin operations."""
from flask import jsonify, request, Response
from datetime import datetime
from zoneinfo import ZoneInfo
from ..common import utc_now, UnavailableError

from .candidate_service import CandidateRequest
from .config import StrategyConfig


def register_candidate_routes(app, service):
    def body():
        value = request.get_json()
        if not isinstance(value, dict):
            raise ValueError('JSON object required')
        return value

    def accepted(job):
        return jsonify(job_id=job['job_id'], task_id=job['job_id'], state=job['state'], status='pending'), 202

    @app.get('/api/strategy-configs/current')
    def current_strategy_config():
        config = service.current_config()
        return jsonify(config=config.model_dump(mode='json'), config_hash=config.config_hash,
                       schema=StrategyConfig.model_json_schema(), market_edge='NOT_ESTABLISHED')

    @app.get('/api/strategy-configs')
    def strategy_configs():
        return jsonify(service._all('strategy_configs'))

    @app.post('/api/strategy-configs')
    def new_strategy_config():
        return jsonify(service.register_config(StrategyConfig.model_validate(body()))), 201

    @app.get('/api/universe/coverage')
    def candidate_coverage():
        return jsonify(service.coverage())

    @app.get('/api/candidate-data-pipeline')
    def data_pipeline_status():
        values = service._all('candidate_pipeline_attempts')
        return jsonify(latest=values[-1] if values else None, service_required=True,
                       pipeline_version='MAINBOARD_DATA_DAG_R03_V1', scope='FULL_MAINBOARD',
                       resume_policy='PERSISTENT_REQUEST_QUEUE_FINITE_BUDGET_NO_FALSE_COMPLETION')

    @app.post('/api/candidate-data-pipeline/capture')
    def capture_candidate_data():
        if body():
            raise ValueError('capture uses registered sources and authoritative server time; no override fields')
        scheduler = getattr(service, 'scheduler', None)
        if scheduler is None:
            from .scheduler import CandidateScheduler
            scheduler = CandidateScheduler(service)
        return accepted(scheduler.submit_capture())

    @app.get('/api/candidate-runs')
    def candidate_runs():
        values = service.list_runs(int(request.args.get('limit', 100)))
        return jsonify([{'run_id': r.run_id, 'revision': r.revision, 'parent_run_id': r.parent_run_id,
            'observation_date': r.observation_date.isoformat(), 'generated_at': r.generated_at.isoformat(),
            'decision_cutoff': r.decision_cutoff.isoformat(), 'profile': r.profile, 'status': r.status,
            'coverage_type': r.coverage_type, 'count': len(r.items), 'config_hash': r.config_hash,
            'market_session': r.market_session} for r in values])

    @app.post('/api/candidate-runs')
    def start_candidate_run():
        return accepted(service.submit(CandidateRequest.model_validate(body())))

    @app.get('/api/candidate-runs/<run_id>')
    def get_candidate_run(run_id):
        return jsonify(service.get_run(run_id).model_dump(mode='json'))

    @app.get('/api/candidate-runs/<run_id>/family/<family>')
    def candidate_family(run_id, family):
        return jsonify([x.model_dump(mode='json') for x in service.family(run_id, family)])

    @app.get('/api/candidate-runs/<run_id>/export')
    def candidate_export(run_id):
        if request.args.get('format', 'json') == 'csv':
            return Response('\ufeff' + service.export_csv(run_id), mimetype='text/csv; charset=utf-8',
                            headers={'Content-Disposition': 'attachment; filename=candidate-samples.csv'})
        return Response(service.get_run(run_id).model_dump_json(indent=2), mimetype='application/json',
                        headers={'Content-Disposition': 'attachment; filename=candidate-run.json'})

    @app.post('/api/candidate-runs/<run_id>/auction-review')
    def start_auction_review(run_id):
        value = body()
        if set(value) - {'snapshot_id'} or value.get('snapshot_id') is not None and not isinstance(value['snapshot_id'], str):
            raise ValueError('select an existing auction snapshot ID')
        return accepted(service.submit_auction(run_id, value.get('snapshot_id')))

    @app.get('/api/candidate-runs/<run_id>/auction-review')
    def candidate_auction_review(run_id):
        return jsonify(service.auction_reviews(run_id))

    @app.get('/api/candidate-runs/<run_id>/outcomes')
    def candidate_outcomes(run_id):
        return jsonify(service.outcomes(run_id))

    @app.post('/api/candidate-runs/<run_id>/outcomes')
    def start_candidate_outcomes(run_id):
        value = body()
        if set(value) - {'input_id'} or value.get('input_id') is not None and not isinstance(value['input_id'], str):
            raise ValueError('select an existing outcome input ID')
        return accepted(service.submit_outcomes(run_id, value.get('input_id')))

    @app.get('/api/market/emotion')
    def market_emotion():
        runs = service.list_runs(1)
        return jsonify(runs[0].emotion.model_dump(mode='json') if runs else
                       {'state': 'UNKNOWN', 'score': None, 'reasons': ['NO_SAVED_CANDIDATE_RUN']})

    @app.get('/api/themes/<theme_id>/snapshot')
    def theme_snapshot(theme_id):
        _, bundle = service.input(request.args.get('input_id'))
        from .visibility import EvidenceResolver
        cutoff=utc_now()
        resolver=EvidenceResolver(bundle.receipts,cutoff=cutoff,coverage_type=bundle.coverage_type,sessions=bundle.sessions)
        current_date=cutoff.astimezone(ZoneInfo('Asia/Shanghai')).date()
        return jsonify([t.model_dump(mode='json') for t in bundle.themes if t.theme_id == theme_id
                        and t.effective_from<=current_date and (t.effective_to is None or current_date<t.effective_to)
                        and resolver.is_visible(t,require_complete=True)])

    @app.get('/api/instruments/<instrument_id>/evidence')
    def instrument_evidence(instrument_id):
        runs = [service.get_run(request.args['run_id'])] if request.args.get('run_id') else service.list_runs(100)
        matches = [{'run_id': r.run_id, 'observation_date': r.observation_date.isoformat(),
                    'candidate': item.model_dump(mode='json')} for r in runs for item in r.items if item.instrument_id == instrument_id]
        if request.args.get('run_id') or request.args.get('input_id'):
            matches.append(service.raw_instrument_evidence(instrument_id, run_id=request.args.get('run_id'),
                                                          input_id=request.args.get('input_id')))
        cutoff = runs[0].decision_cutoff if runs and request.args.get('run_id') else utc_now()
        session = runs[0].observation_date if runs else cutoff.astimezone(ZoneInfo('Asia/Shanghai')).date()
        matches.append({'record_type': 'PUBLIC_DISCLOSURE_EVIDENCE',
                        **service.public_evidence.instrument(instrument_id, cutoff, session)})
        return jsonify(matches)

    @app.get('/api/seats/<seat_id>/evidence')
    def seat_evidence(seat_id):
        runs = [service.get_run(request.args['run_id'])] if request.args.get('run_id') else service.list_runs(1)
        cutoff = runs[0].decision_cutoff if runs and request.args.get('run_id') else utc_now()
        session = runs[0].observation_date if runs else cutoff.astimezone(ZoneInfo('Asia/Shanghai')).date()
        return jsonify(service.public_evidence.seat(seat_id, cutoff, session))

    @app.get('/api/themes/<theme_id>/evidence')
    def theme_public_evidence(theme_id):
        if request.args.get('run_id'):
            saved = service.get_run(request.args['run_id'])
            members = sorted({iid for item in saved.items for theme in item.theme_evidence
                              if theme['theme_id'] == theme_id for iid in theme.get('member_ids', ())})
            cutoff, session = saved.decision_cutoff, saved.observation_date
        else:
            _, bundle = service.input(request.args.get('input_id'))
            cutoff, session = utc_now(), bundle.observation_date
            from .visibility import EvidenceResolver
            resolver=EvidenceResolver(bundle.receipts,cutoff=cutoff,coverage_type=bundle.coverage_type,sessions=bundle.sessions)
            membership_date=cutoff.astimezone(ZoneInfo('Asia/Shanghai')).date()
            themes = [t for t in bundle.themes if t.theme_id == theme_id and resolver.is_visible(t,require_complete=True)
                      and t.theme_type in {'INDUSTRY', 'EVENT'}
                      and t.effective_from <= membership_date and (t.effective_to is None or membership_date < t.effective_to)]
            members = list(max(themes, key=lambda t: t.ingested_at).member_ids) if themes else []
            from .engine import security_at, qualification
            config = service.current_config()
            records = [s for s in bundle.universe.securities if resolver.is_visible(s,require_complete=True)] if (
                resolver.is_visible(bundle.universe,require_complete=True)) else []
            members = [iid for iid in members if qualification(security_at(records, iid, session, cutoff),
                       session, bundle.sessions, config) is None]
        if not members:
            raise UnavailableError('VISIBLE_REGISTERED_THEME_MEMBERS_REQUIRED', 'candidate')
        return jsonify(service.public_evidence.theme(theme_id, members, cutoff, session))

    @app.get('/api/candidate-schedule')
    def candidate_schedule():
        return jsonify(plans=service._all('candidate_observation_plans'), events=service._all('candidate_schedule_events')[-100:],
                       service_required=True, missed_capture_policy='NO_BACKDATED_SNAPSHOT')
