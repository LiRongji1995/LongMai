"""TEST_FIXTURE single-source authorization; no provider SDK or real state."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from stockfish_core.yaogu import premarket_capture_cache_v1 as native
from stockfish_core.yaogu import provider_requests
from stockfish_core.yaogu.collection_source_continuation import AUTHORIZATION, CLAIM
from stockfish_core.yaogu.providers import archive_receipt
from tests.yaogu.test_premarket_capture_cache_v1 import FixtureBudget


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(native._canonical(value)).hexdigest()


def reference(path):
    raw = path.read_bytes()
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(native._canonical(value))
    return reference(path)


class AccountingBudget(FixtureBudget):
    def __init__(self):
        super().__init__()
        self.pending = 0

    def preclaim_request(self, endpoint, params):
        super().preclaim_request(endpoint, params)
        self.pending += 1

    def record_request(self, result):
        self.pending -= 1
        self.calls['actual_unknown'] = sum(type(result['coverage'].get(key)) is not int
            for key in ('sdk_operations', 'http_attempts'))

    @property
    def metrics(self):
        return dict(super().metrics, pending_request_results=self.pending)


class Executor:
    def __init__(self, *, status='COMPLETE', pagination=True, unknown_counts=False,
                 crash=False, future=False, before_claim=False):
        self.status, self.pagination = status, pagination
        self.unknown_counts, self.crash, self.future = unknown_counts, crash, future
        self.before_claim = before_claim
        self.calls = []

    def __call__(self, endpoint, params, output, **kwargs):
        self.calls.append((endpoint, deepcopy(params)))
        if self.crash:
            raise RuntimeError('TEST_FIXTURE_PROCESS_DIED')
        started, seen = now(), now()
        if endpoint == 'master_szse':
            rows = [{'A股代码': '000001', 'A股简称': 'TEST_FIXTURE', 'A股上市日期': '2000-01-01', '板块': '主板'}]
        elif endpoint == 'mainboard_sse':
            rows = [{'证券代码': '600001', '证券简称': 'TEST_FIXTURE', '上市日期': '2000-01-01'}]
        elif endpoint == 'lhb_summary':
            rows = []
        else:
            raise AssertionError('TEST_FIXTURE_UNEXPECTED_ENDPOINT:' + endpoint)
        complete = self.status == 'COMPLETE' and self.pagination
        if self.status != 'COMPLETE':
            rows = []
        coverage = dict(sdk_operations=None if self.unknown_counts else 0, http_attempts=1)
        payload = dict(status=self.status, pagination_complete=complete, rows=rows, coverage=coverage,
                       gaps=[], field_version='TEST_FIXTURE_ROWS', first_seen_at=seen)
        if self.status == 'FETCH_FAILED':
            payload.pop('first_seen_at')
            payload.pop('field_version')
        receipt = archive_receipt(Path(output) / 'raw', 'TEST_FIXTURE', endpoint, params, payload,
            status=self.status, pagination_complete=complete, field_version='TEST_FIXTURE_ROWS', first_seen_at=seen)
        result = dict(endpoint=endpoint, provider='TEST_FIXTURE', parameters=params, rows=rows,
            status=self.status, pagination_complete=complete, coverage=coverage, gaps=[],
            field_version='TEST_FIXTURE_ROWS', request_receipt_id=receipt['receipt_id'], receipts=[receipt],
            started_at='2000-01-01T00:00:00+00:00' if self.before_claim else started,
            completed_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
            if self.future else now(), implementation_source_hashes={
                name: hashlib.sha256(Path(provider_requests.__file__).with_name(name).read_bytes()).hexdigest()
                for name in native.PRODUCERS}, failure_category='NETWORK', error_code='UPSTREAM_TIMEOUT')
        Path(output, 'request-TEST_FIXTURE.json').write_text(json.dumps(result))
        return result


def fixture(tmp_path, executor=None, mutate=None, cache_name='TEST_FIXTURE_cache'):
    root = tmp_path / cache_name
    day = datetime.now(native.SH).date().isoformat()
    spec = native.RequestSpec('master_szse', {}, day, role='CURRENT_OFFICIAL', current=True)
    initial = native.CaptureCache(root, AccountingBudget(), config=dict(allow_test_fixture=True),
                                  executor=Executor(status='FETCH_FAILED'))
    old = initial.capture_one(spec)
    prior_attempt = Path(old.metadata['capture_attempt_dir'])
    prior_index = next((root / 'index' / spec.key).glob('*.json'))
    scope = dict(backend='premarket', device=1, inode=42)
    directory = tmp_path / 'TEST_FIXTURE_registry' / 'scopes' / digest(scope)
    identity = dict(backend='premarket', request_key='TEST_FIXTURE_same_daily_operation')
    operation_id = digest(dict(scope=scope, identity=identity))
    operation_ref = save(directory / 'operations' / (operation_id + '.json'),
        dict(schema_version='CODEX_CHAT_WORKSPACE_V1', scope=scope, operation_id=operation_id,
             identity=identity, phase='FAILED_OR_UNKNOWN'))
    slot = directory / 'operations' / operation_id / 'continuation'
    proof_ref = save(tmp_path / 'TEST_FIXTURE_snapshot_proof.json',
                     dict(schema_version='ENGINEERING_ORIGINAL_SNAPSHOT_PROOF_V1'))
    retry = dict(authorization='ONE_EXACT_SOURCE_ATTEMPT', request_spec=spec.to_dict(), request_key=spec.key,
        prior_receipt_id=old.receipt_id, prior_index_ref=reference(prior_index),
        prior_attempt_dir=str(prior_attempt), prior_attempt_refs=[reference(path)
            for path in sorted(prior_attempt.rglob('*')) if path.is_file()])
    budget = AccountingBudget()
    proposal = dict(schema_version='PREMARKET_CONTINUATION_PROPOSAL_V1',
        status='ADMITTED_ONE_EXACT_SOURCE_RECOVERY_AND_CONTINUATION', scope=scope,
        operation_id=operation_id, request_key=identity['request_key'], source_retry=retry,
        source_job=str(tmp_path / 'TEST_FIXTURE_original_failed_job'), remaining_limits=budget.limits)
    if mutate:
        mutate(proposal)
    proposal_ref = save(slot / 'proposal.json', proposal)
    claim = dict(schema_version=CLAIM, scope=scope, operation_id=operation_id,
        request_key=identity['request_key'], source_retry=proposal['source_retry'],
        source_job=proposal['source_job'], remaining_limits=proposal['remaining_limits'],
        original_operation_ref=operation_ref, snapshot_proof_ref=proof_ref,
        proposal_ref=proposal_ref, claimed_at=now())
    claim_ref = save(slot / 'claim.json', claim)
    context = dict(schema_version=AUTHORIZATION, scope=scope, scope_directory=str(directory),
                   operation_id=operation_id, claim=claim_ref, proposal=proposal_ref)
    executor = executor or Executor()
    cache = native.CaptureCache(root, budget, config=dict(allow_test_fixture=True, retry_failed=False),
                                executor=executor, continuation_context=context)
    return dict(cache=cache, context=context, old=old, spec=spec, prior_attempt=prior_attempt,
                prior_index=prior_index, slot=slot, operation_ref=operation_ref, executor=executor)


def test_only_exact_source_is_recovered_once_and_original_failure_is_preserved(tmp_path):
    f = fixture(tmp_path)
    originals = {path: path.read_bytes() for path in f['prior_attempt'].rglob('*') if path.is_file()}
    old_index_bytes = f['prior_index'].read_bytes()
    result = f['cache'].capture_authorized_once()
    assert result.status == 'COMPLETE' and result.receipt_id != f['old'].receipt_id
    assert f['cache'].capture_authorized_once().receipt_id == result.receipt_id
    assert f['cache'].capture_one(f['spec']).receipt_id == result.receipt_id
    assert f['executor'].calls == [('master_szse', {})]
    assert f['cache'].budget.calls['requests'] == 1 and f['cache'].config['retry_failed'] is False
    assert {path: path.read_bytes() for path in originals} == originals
    assert f['prior_index'].read_bytes() == old_index_bytes
    request_claim = json.loads((Path(result.metadata['capture_attempt_dir']) / 'request-claim.json').read_bytes())
    assert request_claim['continuation_authorization']['prior_receipt_id'] == f['old'].receipt_id
    reopened = native.CaptureCache(f['cache'].root, AccountingBudget(), config=dict(allow_test_fixture=True),
        executor=Executor(crash=True), continuation_context=f['context'])
    assert reopened.capture_authorized_once().receipt_id == result.receipt_id
    assert reopened.budget.calls['requests'] == 0
    f['cache'].terminal()


@pytest.mark.parametrize('executor', [Executor(status='FETCH_FAILED'), Executor(status='PARTIAL'),
    Executor(pagination=False), Executor(unknown_counts=True), Executor(crash=True), Executor(future=True)])
def test_failed_or_unknown_authorized_attempt_stops_and_is_never_retried(tmp_path, executor):
    f = fixture(tmp_path, executor)
    with pytest.raises((ValueError, RuntimeError)):
        f['cache'].capture_authorized_once()
    assert len(executor.calls) == 1
    for cache in (f['cache'], native.CaptureCache(f['cache'].root, AccountingBudget(),
            config=dict(allow_test_fixture=True), executor=Executor(), continuation_context=f['context'])):
        with pytest.raises((ValueError, RuntimeError)):
            cache.capture_authorized_once()
        with pytest.raises(ValueError, match='AUTHORIZED_SOURCE_MUST_COMPLETE_FIRST'):
            cache.capture_one(f['spec'])
    assert len(executor.calls) == 1
    attempts = [p for p in f['prior_attempt'].parent.iterdir() if p != f['prior_attempt']]
    assert len(attempts) == 1
    failure = json.loads((attempts[0] / 'attempt-failed.json').read_bytes())
    assert failure['source_completion'] == ('UNKNOWN' if executor.crash else executor.status)


def test_continuation_stops_after_any_new_provider_failure_and_preserves_its_commit(tmp_path):
    f = fixture(tmp_path)
    f['cache'].capture_authorized_once()
    f['executor'].status = 'FETCH_FAILED'
    other = native.RequestSpec('lhb_summary', dict(start_date='2026-10-01', end_date='2026-10-09'), '2026-10-09')
    with pytest.raises(ValueError, match='NEW_SOURCE_NOT_COMPLETE'):
        f['cache'].capture_one(other)
    assert f['cache'].lookup(other).status == 'FETCH_FAILED'
    with pytest.raises(ValueError, match='CONTINUATION_FAILED_NO_MORE_REQUESTS'):
        f['cache'].capture_authorized_once()
    with pytest.raises(ValueError, match='AUTHORIZED_SOURCE_MUST_COMPLETE_FIRST'):
        f['cache'].capture_one(native.RequestSpec('lhb_summary', dict(
            start_date='2026-10-12', end_date='2026-10-16'), '2026-10-16'))
    assert len(f['executor'].calls) == 2


def test_other_cached_failure_never_receives_the_single_source_authorization(tmp_path):
    f = fixture(tmp_path)
    other = native.RequestSpec('lhb_summary', dict(start_date='2026-10-01', end_date='2026-10-09'), '2026-10-09')
    native.CaptureCache(f['cache'].root, AccountingBudget(), config=dict(allow_test_fixture=True),
        executor=Executor(status='FETCH_FAILED')).capture_one(other)
    f['cache'].capture_authorized_once()
    with pytest.raises(ValueError, match='EXISTING_FAILED_SOURCE_NOT_AUTHORIZED'):
        f['cache'].capture_one(other)
    assert len(f['executor'].calls) == 1


@pytest.mark.parametrize('change', ['endpoint', 'key', 'receipt', 'date', 'limit'])
def test_even_valid_authorization_hashes_cannot_change_the_exact_scope(tmp_path, change):
    def mutate(p):
        if change == 'endpoint': p['source_retry']['request_spec']['endpoint'] = 'mainboard_sse'
        elif change == 'key': p['source_retry']['request_key'] = '0' * 64
        elif change == 'receipt': p['source_retry']['prior_receipt_id'] = '0' * 64
        elif change == 'date': p['source_retry']['request_spec']['business_session'] = '2000-01-01'
        else: p['remaining_limits'] = dict(p['remaining_limits'], max_requests=1)
    f = fixture(tmp_path, mutate=mutate)
    with pytest.raises(ValueError): f['cache'].capture_authorized_once()
    assert f['executor'].calls == [] and f['cache'].budget.calls['requests'] == 0


@pytest.mark.parametrize('target', ['claim', 'proposal', 'old_index', 'old_attempt', 'operation'])
def test_changed_authorization_or_prior_originals_are_not_retried(tmp_path, target):
    f = fixture(tmp_path)
    path = dict(claim=f['slot'] / 'claim.json', proposal=f['slot'] / 'proposal.json',
        old_index=f['prior_index'], old_attempt=f['prior_attempt'] / 'request-claim.json',
        operation=Path(f['operation_ref']['path']))[target]
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError): f['cache'].capture_authorized_once()
    assert f['executor'].calls == []


def test_unknown_extra_attempt_is_blocked(tmp_path):
    f = fixture(tmp_path)
    unknown = f['prior_attempt'].parent / 'TEST_FIXTURE_unknown_attempt'
    save(unknown / 'request-claim.json', dict(schema_version=native.VERSION,
        request_spec=f['spec'].to_dict(), actual_started_at=now()))
    with pytest.raises(ValueError, match='UNAUTHORIZED_OR_UNKNOWN_ADDITIONAL_ATTEMPT'):
        f['cache'].capture_authorized_once()
    assert f['executor'].calls == []


def test_consumed_capture_cannot_predate_its_authorization(tmp_path):
    f = fixture(tmp_path)
    record = f['cache'].capture_authorized_once()
    path = Path(record.metadata['capture_attempt_dir']) / 'request-claim.json'
    value = json.loads(path.read_bytes())
    value['actual_started_at'] = '2000-01-01T00:00:00+00:00'
    save(path, value)
    cache = native.CaptureCache(f['cache'].root, AccountingBudget(), config=dict(allow_test_fixture=True),
        executor=Executor(), continuation_context=f['context'])
    with pytest.raises(ValueError, match='CONSUMPTION_CLOCK_BINDING_CHANGED'):
        cache.capture_authorized_once()
    assert cache.budget.calls['requests'] == 0


def test_fresh_complete_provider_result_cannot_predate_the_authorization(tmp_path):
    f = fixture(tmp_path, executor=Executor(before_claim=True))
    with pytest.raises(ValueError, match='CONSUMPTION_CLOCK_BINDING_CHANGED'):
        f['cache'].capture_authorized_once()
    assert f['cache'].lookup(f['spec']).status == 'COMPLETE'
    assert f['cache'].budget.calls['requests'] == 1 and len(f['executor'].calls) == 1
    with pytest.raises(ValueError, match='CONTINUATION_FAILED_NO_MORE_REQUESTS'):
        f['cache'].capture_authorized_once()
    with pytest.raises(ValueError, match='AUTHORIZED_SOURCE_MUST_COMPLETE_FIRST'):
        f['cache'].capture_one(f['spec'])
    assert len(f['executor'].calls) == 1


@pytest.mark.parametrize('unknown', ['pending', 'counts'])
def test_unknown_parent_accounting_does_not_consume_authorization(tmp_path, unknown):
    f = fixture(tmp_path)
    if unknown == 'pending':
        f['cache'].budget.pending = 1
    else:
        f['cache'].budget.calls['actual_unknown'] = 1
    with pytest.raises(ValueError, match='PENDING_OR_UNKNOWN_REQUEST_ACCOUNTING'):
        f['cache'].capture_authorized_once()
    assert f['executor'].calls == [] and f['cache'].budget.calls['requests'] == 0


def test_no_authorization_retains_normal_cache_failure_behavior(tmp_path):
    f = fixture(tmp_path)
    cache = native.CaptureCache(f['cache'].root, AccountingBudget(), config=dict(allow_test_fixture=True),
                               executor=Executor(crash=True))
    assert cache.capture_one(f['spec']).receipt_id == f['old'].receipt_id
    with pytest.raises(ValueError, match='EXPLICIT_AUTHORIZATION_REQUIRED'):
        cache.capture_authorized_once()
    assert cache.budget.calls['requests'] == 0


def test_no_new_provider_request_after_current_session_rolls_over(tmp_path, monkeypatch):
    from stockfish_core.yaogu import collection_source_continuation as continuation
    f = fixture(tmp_path)
    f['cache'].capture_authorized_once()

    class NextDay(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=1)

    monkeypatch.setattr(continuation, 'datetime', NextDay)
    other = native.RequestSpec('lhb_summary', dict(start_date='2026-10-01', end_date='2026-10-09'), '2026-10-09')
    with pytest.raises(ValueError, match='CURRENT_SESSION_CHANGED_NO_MORE_REQUESTS'):
        f['cache'].capture_one(other)
    assert len(f['executor'].calls) == 1
    with pytest.raises(ValueError, match='CONTINUATION_FAILED_NO_MORE_REQUESTS'):
        f['cache'].capture_authorized_once()


def test_worker_stops_before_collection_when_authorized_source_fails(tmp_path):
    from stockfish_core.yaogu import premarket_workflow_v1 as workflow
    from stockfish_core.yaogu.premarket_runtime_v1 import DEFAULT_LIMITS
    limits = dict(DEFAULT_LIMITS, max_seconds=120)
    f = fixture(tmp_path, cache_name='cache',
        mutate=lambda proposal: proposal.update(remaining_limits=limits))
    executor = Executor(status='FETCH_FAILED')

    def cache_factory(*args, **kwargs):
        return native.CaptureCache(*args, **kwargs, executor=executor)

    def forbidden_collection(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_COLLECTION_MUST_NOT_RUN_AFTER_FAILED_AUTHORIZED_SOURCE')

    config = workflow.load_config(overrides=dict(state_root=str(tmp_path),
        limits=limits, capture=dict(allow_test_fixture=True, retry_failed=False),
        continuation_context=f['context']))
    job = tmp_path / 'TEST_FIXTURE_worker_job'
    job.mkdir()
    result = workflow.execute_workflow(config, job,
        adapters=dict(cache=cache_factory, collect=forbidden_collection))
    assert result['status'] == 'FAILED' and result['phases']['capture'] == 'FAILED'
    assert result['phases']['core'] == 'NOT_RUN'
    assert result['error']['reason'] == 'CONTINUATION_NEW_SOURCE_NOT_COMPLETE_NO_MORE_REQUESTS'
    assert executor.calls == [('master_szse', {})]
    assert result['metrics']['calls']['requests'] == 1
    assert result['metrics']['calls']['actual_unknown'] == 0
    assert config['capture']['retry_failed'] is False
