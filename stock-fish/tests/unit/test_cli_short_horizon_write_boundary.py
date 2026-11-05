"""TEST_FIXTURE: generic outcomes cannot bypass authenticated daily closure."""
import pytest
from argparse import Namespace

from scripts import codex_workspace as workspace
from stockfish_core.jobs import JobService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.r2_closure_cli import service_context
from tests.unit.test_durable_jobs import settled
from tests.unit.test_personal_cli_workflow import command, documents, research


@pytest.mark.parametrize('strategy', ['SHORT_HORIZON_STRENGTH_R1', 'SHORT_HORIZON_STRENGTH_R2'])
@pytest.mark.parametrize('prior_failed', [False, True])
def test_generic_outcome_update_refuses_short_horizon_but_saved_read_remains(research, strategy, prior_failed):
    root, registry, state, db, input_id = research
    run_id = 'TEST_FIXTURE_saved_' + strategy
    store = SQLiteStore(db)
    store.put_document('strategy_experiment_runs', run_id, {
        'run_id': run_id, 'strategy_id': strategy, 'input_id': input_id,
        'coverage_type': 'TEST_FIXTURE', 'execution_profile': 'SHORT_HORIZON_RESEARCH_SINGLE'})
    with JobService(store, recover=False) as jobs:
        if prior_failed:
            def fail(_context):
                raise ValueError('TEST_FIXTURE_ORIGINAL_CLOSURE_FAILED')
            job = jobs.submit('short_horizon_daily_closure_v1', {
                'run_ids': [run_id], 'input_id': input_id, 'evaluation_session': '2026-11-05'},
                fail, job_id='TEST_FIXTURE_previous_closure')
            assert settled(jobs, job['job_id'])['state'] == 'FAILED'
        before_jobs = jobs.list()
        before_events = {job['job_id']: jobs.events(job['job_id']) for job in before_jobs}
    before_documents = documents(db)
    common = ['--db', db, '--state', state, '--run-id', run_id]
    code, rejected = command(registry, 'research-outcomes', *common, '--update',
        '--input-id', input_id, '--request-key', 'TEST_FIXTURE_alternative_route')
    assert code == 2
    assert rejected['reason'] == 'SHORT_HORIZON_UPDATE_REQUIRES_OUTCOMES_R2_DAILY_RUN'
    code, viewed = command(registry, 'research-outcomes', *common)
    assert code == 0 and viewed['read_only'] is True
    assert viewed['outcomes']['run_id'] == run_id
    with JobService(store, recover=False) as jobs:
        assert jobs.list() == before_jobs
        assert {job['job_id']: jobs.events(job['job_id']) for job in before_jobs} == before_events
    assert documents(db) == before_documents
    runtime = workspace.load_runtime(registry_root=registry, allow_test_fixture=True)
    assert not (runtime.directory / 'operations').exists()


def test_prior_tracking_of_different_input_does_not_block_new_observation(research):
    root, registry, state, db, input_id = research
    common = ['--db', db, '--state', state]
    code, result = command(registry, 'research', *common, '--input-id', input_id,
                          '--request-key', 'TEST_FIXTURE_signal')
    assert code == 0, result
    run_id = result['receipt']['run_id']
    store = SQLiteStore(db)
    jobs = JobService(store, recover=False)
    try:
        old = jobs.create('strategy_tracking', {'run_id': run_id,
            'input_id': 'TEST_FIXTURE_previous_observation_input'}, job_id='TEST_FIXTURE_old_tracking')
        events = jobs.events(old['job_id'])
    finally:
        jobs.shutdown(cancel_pending=False)
    code, outcome = command(registry, 'research-outcomes', *common, '--run-id', run_id,
        '--input-id', input_id, '--update', '--request-key', 'TEST_FIXTURE_new_observation')
    assert code == 0, outcome
    with service_context(Namespace(db=db, state=state, allow_test_fixture=True)) as service:
        assert service.jobs.get(old['job_id']) == old
        assert service.jobs.events(old['job_id']) == events
        assert service.jobs.get(outcome['receipt']['job_id'])['state'] == 'SUCCEEDED'


@pytest.mark.parametrize('legacy_kind', ['strategy_outcomes', 'strategy_tracking'])
@pytest.mark.parametrize('legacy_state', ['QUEUED', 'FAILED', 'SUCCEEDED'])
def test_generic_outcomes_preserve_prior_tracking_or_outcome_attempt(research, legacy_kind, legacy_state):
    root, registry, state, db, input_id = research
    run_id = 'TEST_FIXTURE_saved_PV_SAM'
    store = SQLiteStore(db)
    store.put_document('strategy_experiment_runs', run_id, {
        'run_id': run_id, 'input_id': input_id, 'coverage_type': 'TEST_FIXTURE',
        'results': {'PV_OBSERVATION_V1': {}, 'STAGE_AWARE_MOMENTUM_V2': {}}})
    jobs = JobService(store, recover=False)
    try:
        payload = {'run_id': run_id, 'input_id': input_id}
        if legacy_state in {'FAILED', 'SUCCEEDED'}:
            def finish(context):
                if legacy_state == 'FAILED':
                    raise ValueError('TEST_FIXTURE_PREVIOUS_OBSERVATION_FAILED')
                return context.complete({'run_id': run_id, 'test_fixture': True}, lambda connection: None)
            job = jobs.submit(legacy_kind, payload, finish, job_id='TEST_FIXTURE_previous_observation')
            old = settled(jobs, job['job_id'])
        else:
            old = jobs.create(legacy_kind, payload, job_id='TEST_FIXTURE_previous_observation')
        assert old['state'] == legacy_state
        before_events = jobs.events(old['job_id'])
    finally:
        jobs.shutdown(cancel_pending=False)
    before_documents = documents(db)
    code, rejected = command(registry, 'research-outcomes', '--db', db, '--state', state,
        '--run-id', run_id, '--update', '--input-id', input_id, '--request-key', 'TEST_FIXTURE_new_route')
    assert code == 2 and rejected['reason'] == 'RESEARCH_EXISTING_JOB_USE_HISTORY_NO_RETRY'
    with service_context(Namespace(db=db, state=state, allow_test_fixture=True)) as service:
        assert service.jobs.list() == [old]
        assert service.jobs.events(old['job_id']) == before_events
    assert documents(db) == before_documents
