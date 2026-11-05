"""TEST_FIXTURE pending projections; no real archives, data or admission.

The existing daily fixture replaces only archive authentication. This suite
tests orchestration, real SQLite transactions and JobService cancellation;
private projection authority has separate real verifier fixture tests.
"""
from copy import deepcopy

import pytest

from stockfish_core.yaogu import short_horizon_daily_closure_v1 as daily
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.history_artifacts import digest_json_stream
from stockfish_core.yaogu.short_horizon_archive_bridge_v1 import ArchiveVerificationSession
from stockfish_core.yaogu.short_horizon_outcomes_v1 import HORIZONS, NAMESPACES
from tests.yaogu.test_short_horizon_daily_closure_v1 import (
    setup as daily_setup, finished, capture_worker_errors,
)


def pending_run(setup, *, empty=False, legal=False):
    run = deepcopy(setup.run)
    run.pop('content_hash')
    run.update(run_id=run['run_id'] + '_TEST_FIXTURE_CURRENT', input_id=setup.input_id,
        observation_date=str(setup.bundle.observation_date),
        decision_cutoff=setup.evaluated.isoformat(), generated_at=setup.evaluated.isoformat())
    if empty:
        result = run['results'][run['strategy_id']]
        result['items'] = []
        if legal:
            result.update(status='EMPTY', full_eligible_pool_retained=True, eligible_pool_ids=[],
                          counts={'eligible': 0, 'evaluated': 0})
            run['data_readiness']['SHORT_HORIZON_QUALIFICATION'].update(
                status='COMPLETE', universe_complete=True, eligible=0, evaluated=0)
    run['content_hash'] = digest_json_stream(run)
    setup.store.put_document('strategy_experiment_runs', run['run_id'], run)
    setup.bind(run)
    return run


@pytest.fixture
def projected(daily_setup, monkeypatch):
    setup = daily_setup
    policy = {**daily._policy_identity(),
              'pending_run_materialization': daily.PENDING_RUN_MATERIALIZATION}
    monkeypatch.setattr(daily, '_policy_identity', lambda: deepcopy(policy))
    original = setup.bridge.authenticated_bridge_view
    setup.projection_calls, setup.projection_policy = [], policy
    setup.projection_transform = lambda view: view

    def view(svc, *, session, run_id=None, input_id=None, borrow_run=False,
             borrow_if_empty=False, **kwargs):
        assert not (borrow_run and borrow_if_empty)
        if not borrow_if_empty:
            return original(svc, session=session, run_id=run_id, input_id=input_id,
                            borrow_run=borrow_run, **kwargs)
        assert run_id is not None and input_id is None
        setup.projection_calls.append(run_id)
        metadata = original(svc, session=session, run_id=run_id, **kwargs)
        saved = svc.store.get_document('strategy_experiment_runs', run_id)
        nonempty = bool(saved['results'][saved['strategy_id']]['items'])
        if not nonempty:
            metadata = original(svc, session=session, run_id=run_id, borrow_run=True, **kwargs)
        return setup.projection_transform({**metadata, 'pool_nonempty': nonempty})

    monkeypatch.setattr(setup.bridge, 'authenticated_bridge_view', view)
    return setup


def completed(setup, run_ids):
    job = finished(setup.service, setup.cycle.submit(run_ids, setup.input_id)['job_id'])
    return job, setup.cycle.status(job['job_id'])['ledger']


def assert_no_products(setup):
    assert all(setup.store.list_documents(namespace) == [] for namespace in NAMESPACES.values())
    assert setup.store.list_documents(daily.LEDGERS) == []


def test_production_policy_explicitly_binds_pending_projection():
    assert daily._policy_identity()['pending_run_materialization'] == 'AUTHENTICATED_NONEMPTY_V1'


def test_two_runs_keep_sorted_records_and_old_products_but_only_borrow_due_run(projected, monkeypatch):
    setup = projected
    current = pending_run(setup)
    ids = sorted([current['run_id'], setup.run['run_id']])
    before = {key: setup.store.get_document_bytes('strategy_experiment_runs', key) for key in ids}
    calls, evaluate = [], daily.evaluate
    def observed(run, *args, **kwargs):
        calls.append(run['run_id'])
        return evaluate(run, *args, **kwargs)
    monkeypatch.setattr(daily, 'evaluate', observed)
    job, ledger = completed(setup, list(reversed(ids)))
    assert job['state'] == 'SUCCEEDED', job
    assert setup.projection_calls == [current['run_id']]
    assert calls == [setup.run['run_id']]
    assert [run['run_id'] for run in setup.verification['borrowed']] == [setup.run['run_id']]
    assert ledger['run_ids'] == ids and ledger['run_count'] == 2
    assert [row['run_id'] for row in ledger['runs']] == ids
    current_record = next(row for row in ledger['runs'] if row['run_id'] == current['run_id'])
    assert current_record['status'] == 'PENDING'
    assert current_record['due_horizons'] == current_record['unknown_horizons'] == []
    assert [row['horizon'] for row in current_record['horizons']] == list(HORIZONS)
    assert all(row['maturity_state'] == 'PENDING' for row in current_record['horizons'])
    assert current_record['counts'] == {} and current_record['revision_id'] is None
    assert current_record['product_revision_available'] is False
    # Future sessions are not yet in today's input; projection cannot invent
    # a complete registered axis or remove the original reason codes.
    assert all(row['registered_axis_complete'] is False
               and row['reason_codes'] == ['REGISTERED_CALENDAR_HORIZON_UNAVAILABLE']
               for row in current_record['horizons'])
    assert ledger['status'] == 'COMPLETED_PENDING_MATURITY'
    assert all(len(setup.store.documents_for_run(namespace, setup.run['run_id'])) == 1
               and setup.store.documents_for_run(namespace, current['run_id']) == []
               for namespace in NAMESPACES.values())
    assert {key: setup.store.get_document_bytes('strategy_experiment_runs', key) for key in ids} == before
    assert setup.verification['terminal'] == 3  # plan, prepublish, post-ledger


@pytest.mark.parametrize('legal', [False, True])
def test_pending_empty_pool_still_borrows_and_requires_original_qualification(projected, legal):
    run = pending_run(projected, empty=True, legal=legal)
    job, ledger = completed(projected, [run['run_id']])
    assert job['state'] == 'SUCCEEDED', job
    assert projected.projection_calls == [run['run_id']]
    assert [item['run_id'] for item in projected.verification['borrowed']] == [run['run_id']]
    assert ledger['runs'][0]['status'] == ('LEGAL_EMPTY_POOL' if legal else 'UNKNOWN_EMPTY_POOL_PROOF_REQUIRED')
    assert ledger['runs'][0]['empty_pool_proof'] == ('VERIFIED_FROZEN_QUALIFICATION' if legal else 'NOT_ESTABLISHED')
    assert all(projected.store.list_documents(namespace) == [] for namespace in NAMESPACES.values())


@pytest.mark.parametrize('fault', ['unknown', 'due', 'missing', 'duplicate', 'reordered', 'bool', 'extra',
                                 'due_list', 'unknown_list'])
def test_only_exact_five_pending_horizons_can_use_projection(projected, monkeypatch, fault):
    setup = projected
    current = pending_run(setup)
    schedule = setup.cycle._schedule
    def altered(*args):
        plans = schedule(*args)
        row = plans[0]
        if fault == 'unknown':
            row['horizons'][0]['maturity_state'] = 'UNKNOWN'
            row['unknown_horizons'] = [1]
        elif fault == 'due':
            row['horizons'][0]['maturity_state'] = 'DUE'
            row['due_horizons'] = [1]
        elif fault == 'missing':
            row['horizons'].pop()
        elif fault == 'duplicate':
            row['horizons'][-1]['horizon'] = 1
        elif fault == 'reordered':
            row['horizons'].reverse()
        elif fault == 'bool':
            row['horizons'][0]['horizon'] = True
        elif fault == 'extra':
            row['horizons'].append(deepcopy(row['horizons'][-1]))
        else:
            row['due_horizons' if fault == 'due_list' else 'unknown_horizons'] = [1]
        return plans
    monkeypatch.setattr(setup.cycle, '_schedule', altered)
    # Forced due rows are only a seam to assert full borrowing. The existing
    # evaluator may reject them; no formula is replaced to manufacture success.
    job, ledger = completed(setup, [current['run_id']])
    assert setup.projection_calls == []
    assert [item['run_id'] for item in setup.verification['borrowed']] == [current['run_id']]
    if fault == 'unknown':
        assert job['state'] == 'SUCCEEDED' and ledger['runs'][0]['status'] == 'UNKNOWN'


@pytest.mark.parametrize('legacy', ['policy', 'view'])
def test_missing_opt_in_policy_or_view_fact_preserves_full_borrow(projected, legacy):
    if legacy == 'policy':
        projected.projection_policy.pop('pending_run_materialization')
    else:
        def without_fact(view):
            view.pop('pool_nonempty')
            return view
        projected.projection_transform = without_fact
    current = pending_run(projected)
    job, ledger = completed(projected, [current['run_id']])
    assert job['state'] == 'SUCCEEDED' and ledger['runs'][0]['status'] == 'PENDING'
    assert [item['run_id'] for item in projected.verification['borrowed']] == [current['run_id']]
    assert projected.projection_calls == ([] if legacy == 'policy' else [current['run_id']])


@pytest.mark.parametrize('bad', [None, 0, 1, 'true', [], {}])
def test_non_boolean_projection_refused_without_products(projected, monkeypatch, bad):
    current = pending_run(projected)
    projected.projection_transform = lambda view: {**view, 'pool_nonempty': bad}
    errors = capture_worker_errors(projected, monkeypatch)
    job, ledger = completed(projected, [current['run_id']])
    assert job['state'] == 'FAILED' and ledger is None
    assert errors == ['SHORT_HORIZON_CLOSURE_PENDING_POOL_PROJECTION_INVALID']
    assert_no_products(projected)


@pytest.mark.parametrize('part', ['run_metadata', 'credential', 'unexpected_run', 'false_nonempty'])
def test_projection_cannot_replace_authenticated_identity_or_contradict_full_run(projected, monkeypatch, part):
    current = pending_run(projected)
    def forged(view):
        if part == 'run_metadata':
            view['run_metadata']['observation_date'] = '2026-01-01'
        elif part == 'credential':
            view['credential']['run_id'] = 'TEST_FIXTURE_OTHER_RUN'
        else:
            view['run'] = deepcopy(current)
            if part == 'false_nonempty':
                view['pool_nonempty'] = False
        return view
    projected.projection_transform = forged
    errors = capture_worker_errors(projected, monkeypatch)
    job, ledger = completed(projected, [current['run_id']])
    assert job['state'] == 'FAILED' and ledger is None
    assert errors == [('SHORT_HORIZON_CLOSURE_BORROWED_RUN_IDENTITY_CHANGED'
                       if part in {'run_metadata', 'credential'} else
                       'SHORT_HORIZON_CLOSURE_PENDING_POOL_PROJECTION_INVALID')]
    assert_no_products(projected)


@pytest.mark.parametrize('tamper', ['remove', None, False, 'TEST_FIXTURE_OTHER_PROJECTION'])
def test_policy_change_cannot_rehash_request_into_permission(projected, monkeypatch, tamper):
    current = pending_run(projected)
    request = deepcopy(projected.cycle.plan([current['run_id']], projected.input_id)['request'])
    if tamper == 'remove':
        request['policy'].pop('pending_run_materialization')
    else:
        request['policy']['pending_run_materialization'] = tamper
    request['root_job_id'] = projected.cycle.job_prefix + digest(projected.cycle._request_identity(request))
    errors = capture_worker_errors(projected, monkeypatch)
    projected.service.jobs.register(daily.JOB_KIND, projected.cycle._worker)
    job = finished(projected.service, projected.service.jobs.submit(daily.JOB_KIND, request,
        job_id=request['root_job_id'])['job_id'])
    assert job['state'] == 'FAILED'
    assert errors == ['SHORT_HORIZON_CLOSURE_POLICY_CHANGED_AFTER_SUBMISSION']
    assert projected.projection_calls == []
    assert_no_products(projected)


def test_due_single_run_never_requests_projection_or_adds_projection_audit(projected):
    job, ledger = completed(projected, [projected.run['run_id']])
    assert job['state'] == 'SUCCEEDED', job
    assert projected.projection_calls == []
    assert [run['run_id'] for run in projected.verification['borrowed']] == [projected.run['run_id']]
    assert 'pending_run_projections' not in ledger['archive_verification']
    assert 'borrowed_value_identities' not in ledger['archive_verification']
    assert ledger['runs'][0]['due_horizons'] == list(HORIZONS)
    assert ledger['runs'][0]['counts'] == {'MATURE': 20}
    assert all(len(projected.store.documents_for_run(namespace, projected.run['run_id'])) == 1
               for namespace in NAMESPACES.values())


def test_cancel_after_pending_projection_still_prevents_atomic_publication(projected):
    current = pending_run(projected)
    def cancel(view):
        active = projected.service.jobs.list(kind=daily.JOB_KIND)
        running = [job for job in active if job['state'] == 'RUNNING']
        assert len(running) == 1
        projected.service.jobs.cancel(running[0]['job_id'])
        return view
    projected.projection_transform = cancel
    job, ledger = completed(projected, [projected.run['run_id'], current['run_id']])
    assert job['state'] == 'CANCELLED' and ledger is None
    assert projected.projection_calls == [current['run_id']]
    assert_no_products(projected)


@pytest.mark.parametrize('late', ['raw', 'borrowed_run'])
def test_pending_projection_keeps_post_ledger_terminal_and_entire_batch_rollback(
        projected, monkeypatch, tmp_path, late):
    setup = projected
    current = pending_run(setup)
    source = tmp_path / 'TEST_FIXTURE_pending_source.json'
    source.write_bytes(b'{"coverage_type":"TEST_FIXTURE"}')
    base = setup.bridge.ArchiveVerificationSession
    errors = []
    class Sources(base):
        def __init__(self, *, checkpoint=lambda: None):
            super().__init__(checkpoint=checkpoint)
            self.sources = ArchiveVerificationSession(checkpoint=checkpoint)
            self.sources.read(source)
        def terminal(self):
            try:
                result = super().terminal()
                self.sources.terminal()
                return result
            except ValueError as exc:
                errors.append(str(exc))
                raise
    monkeypatch.setattr(setup.bridge, 'ArchiveVerificationSession', Sources)
    put, changed = setup.store._put_document, []
    def late_change(connection, namespace, key, value):
        result = put(connection, namespace, key, value)
        if namespace == daily.LEDGERS:
            assert connection.in_transaction
            assert sum(connection.execute('SELECT COUNT(*) FROM documents WHERE namespace=?', (ns,)).fetchone()[0]
                       for ns in NAMESPACES.values()) == 3
            assert [run['run_id'] for run in setup.verification['borrowed']] == [setup.run['run_id']]
            if late == 'raw':
                source.write_bytes(source.read_bytes() + b' TEST_FIXTURE_LATE_CHANGE')
            else:
                borrowed = setup.verification['borrowed'][0]
                borrowed['results'][borrowed['strategy_id']]['items'][0]['comparable_basis'] = 'TEST_FIXTURE_CHANGED'
            changed.append(key)
        return result
    monkeypatch.setattr(setup.store, '_put_document', late_change)
    ids = sorted([setup.run['run_id'], current['run_id']])
    before = {key: setup.store.get_document_bytes('strategy_experiment_runs', key) for key in ids}
    job, ledger = completed(setup, ids)
    assert changed == [job['job_id']] and job['state'] == 'FAILED' and ledger is None
    assert any(error == 'R2_ARCHIVE_BRIDGE_FILE_SHA_OR_IDENTITY_MISMATCH'
               if late == 'raw' else 'BORROWED_RUN_CHANGED' in error for error in errors), errors
    assert_no_products(setup)
    assert all(event['state'] != 'SUCCEEDED' for event in setup.service.jobs.events(job['job_id']))
    assert {key: setup.store.get_document_bytes('strategy_experiment_runs', key) for key in ids} == before
