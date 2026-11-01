"""Independent TEST_FIXTURE checks for the one historical recovery boundary.

Literal accounting and small synthetic identity sets are independent oracles.
No provider, database, old job, registered state, or saved strategy is touched.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace

import pytest

from scripts import codex_explicit_failure_recovery_v1 as control
from scripts import codex_full_factor_recovery_v1 as source
from stockfish_core.yaogu.provider_comparability import comparable_rows
from stockfish_core.yaogu.premarket_capture_cache_v1 import factor_contract
from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report


LIMITS = dict(max_requests=1024, max_sdk_calls=10000, max_http_attempts=32768,
    request_http_attempts=64, request_timeout_seconds=90, max_seconds=10800,
    cache_bytes=1073741824, output_bytes=805306368, capture_read_bytes=34359738368,
    core_read_bytes=34359738368, theme_read_bytes=34359738368,
    presentation_read_bytes=4294967296, supervisor_read_bytes=8589934592,
    self_rss_bytes=2684354560, tree_rss_bytes=3221225472)
FP_FIELDS = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_flags')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode())
    os.chmod(path, 0o600)
    return reference(path)


def reference(path):
    """Independent SHA/7FP source for fixture manifests, not the tested reader."""
    raw, info = path.read_bytes(), path.lstat()
    return dict(path=str(path.absolute()), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
        fp={name: int(getattr(info, name, 0)) for name in FP_FIELDS})


@pytest.fixture(autouse=True)
def isolated_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_NETWORK_AND_DATABASE_FORBIDDEN')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)


@pytest.fixture
def old_failure(tmp_path):
    state, directory = tmp_path / 'TEST_FIXTURE_state', tmp_path / 'TEST_FIXTURE_scope'
    job = state / 'jobs/TEST_FIXTURE_original_failed'
    config = dict(limits=deepcopy(LIMITS))
    completion = dict(status='FAILED', phases=dict(capture='FAILED', core='NOT_RUN'),
        expected_sessions=['2026-10-28', '2026-10-29', '2026-10-29'],
        error=dict(reason='ORIGINAL_RAW_FIRST_SEEN_REQUIRED_NOT_BACKFILLED'),
        metrics=dict(limits=deepcopy(LIMITS), elapsed_seconds=1625.21,
            calls=dict(requests=25, sdk_reserved=948, http_reserved=192,
                sdk_actual=None, http_actual=None, sdk_actual_known_subtotal=896,
                http_actual_known_subtotal=3, sdk_actual_unknown_requests=1,
                http_actual_unknown_requests=1, actual_unknown=2),
            returned_read_bytes=dict(capture=2998127258)))
    final = dict(status='FAILED', job=str(job), external_worker_reaped=True,
        elapsed_seconds=1628.056, supervisor_returned_read_bytes=46510618)
    operation = dict(phase='FAILED_OR_UNKNOWN',
        identity=dict(observation_date='2026-10-28', target_session='2026-10-29',
                      backend='premarket', execution_engine='original'),
        started_at='2026-10-28T16:05:41.488623+00:00',
        finished_at='2026-10-28T16:47:41.115870+00:00')
    producer = tmp_path / 'TEST_FIXTURE_producer.py'
    producer.write_text('# TEST_FIXTURE\n', encoding='utf-8')
    runtime = SimpleNamespace(profile=dict(state_root=str(state), config=str(state / 'configuration.json')),
        directory=directory, check_binding=lambda: None)
    def seal():
        operation['identity']['configuration'] = save(state / 'configuration.json', config)
        save(job / 'configuration.json', config)
        final['completion'] = save(job / 'worker-completion.json', completion)
        final['code_registry'] = save(job / 'code-registry.json', [reference(producer)])
        save(job / 'final-control.json', final)
        save(directory / ('operations/' + 'a' * 64 + '.json'), operation)
    seal()
    return SimpleNamespace(runtime=runtime, job=job, config=config, completion=completion,
        final=final, operation=operation, seal=seal)


def test_original_three_hours_are_not_restarted_and_unknown_actuals_survive(old_failure):
    before = {path: path.read_bytes() for path in old_failure.job.rglob('*') if path.is_file()}
    result = source.budget_baseline(old_failure.runtime, old_failure.job)
    assert result['measured_wall_seconds'] == 2519.627247
    assert result['consumed']['wall_seconds'] == 2580
    assert result['remaining_limits']['max_seconds'] == 8220
    assert result['remaining_limits']['max_requests'] == 999
    assert result['remaining_limits']['max_sdk_calls'] == 9052
    assert result['remaining_limits']['max_http_attempts'] == 32576
    assert result['consumed']['capture_read_bytes'] == 2998127258
    assert result['consumed']['supervisor_read_bytes'] == 46510618
    assert result['remaining_limits']['capture_read_bytes'] == 8543423974
    assert result['preserved_unknown_actual_counts'] == dict(sdk_actual=None, http_actual=None,
        sdk_actual_known_subtotal=896, http_actual_known_subtotal=3, actual_unknown=2)
    assert result['consumed']['output_bytes'] == sum(len(raw) for raw in before.values())
    assert before == {path: path.read_bytes() for path in old_failure.job.rglob('*') if path.is_file()}


@pytest.mark.parametrize('counter', ['WORKER', 'SUPERVISOR'])
def test_larger_observed_monotonic_duration_wins_over_utc(old_failure, counter):
    if counter == 'WORKER':
        old_failure.completion['metrics']['elapsed_seconds'] = 3000.125
    else:
        old_failure.final['elapsed_seconds'] = 3000.125
    old_failure.seal()
    result = source.budget_baseline(old_failure.runtime, old_failure.job)
    assert result['consumed']['wall_seconds'] == 3061
    assert result['remaining_limits']['max_seconds'] == 7739


@pytest.mark.parametrize('name,value', [('requests', True), ('sdk_reserved', -1),
                                      ('http_reserved', 1.5)])
def test_invalid_original_reservations_never_become_free_budget(old_failure, name, value):
    old_failure.completion['metrics']['calls'][name] = value
    old_failure.seal()
    with pytest.raises(ValueError, match='FINITE_NONNEGATIVE'):
        source.budget_baseline(old_failure.runtime, old_failure.job)


def test_missing_original_counter_fails_closed(old_failure):
    del old_failure.completion['metrics']['calls']['sdk_reserved']
    old_failure.seal()
    with pytest.raises((KeyError, ValueError)):
        source.budget_baseline(old_failure.runtime, old_failure.job)


def test_disagreeing_original_limits_fail_closed(old_failure):
    old_failure.completion['metrics']['limits']['max_seconds'] += 1
    old_failure.seal()
    with pytest.raises(ValueError, match='ORIGINAL_LIMITS_DISAGREE'):
        source.budget_baseline(old_failure.runtime, old_failure.job)


def test_exhausted_original_reservations_are_not_reauthorized(old_failure):
    old_failure.completion['metrics']['calls']['sdk_reserved'] = 10000
    old_failure.seal()
    with pytest.raises(ValueError, match='ORIGINAL_BUDGET_EXHAUSTED:max_sdk_calls'):
        source.budget_baseline(old_failure.runtime, old_failure.job)


def test_original_completion_physical_replacement_is_rejected(old_failure):
    path = old_failure.job / 'worker-completion.json'
    raw = path.read_bytes()
    path.unlink()
    path.write_bytes(raw)
    with pytest.raises(ValueError, match='SOURCE_REFERENCE_CHANGED'):
        source.budget_baseline(old_failure.runtime, old_failure.job)


def test_second_matching_original_operation_is_ambiguous(old_failure):
    save(old_failure.runtime.directory / ('operations/' + 'b' * 64 + '.json'), old_failure.operation)
    with pytest.raises(ValueError, match='EXACTLY_ONE_ORIGINAL_OPERATION'):
        source.budget_baseline(old_failure.runtime, old_failure.job)


def test_planning_work_is_deducted_before_execution_without_phase_reset():
    remainder = deepcopy(LIMITS)
    remainder.update(max_seconds=8220, capture_read_bytes=8543423974,
                     supervisor_read_bytes=8543423974)
    plan = dict(baseline=dict(remaining_limits=remainder), planning=dict(
        conservative_elapsed_seconds=21, artifact_bytes=1234,
        started_at='2026-11-01T01:00:00+00:00',
        finished_at='2026-11-01T01:00:20.1+00:00', metrics=dict(elapsed_seconds=20.1, failures=[],
            calls=dict(requests=0, sdk_reserved=0, http_reserved=0), returned_read_bytes=dict(
            capture=1001, core=2002, theme=3003, presentation=4004))))
    result = control._execution_limits(plan)
    assert result['max_seconds'] == 8199
    assert result['supervisor_read_bytes'] == 8543413964
    assert result['capture_read_bytes'] == 8543413964
    assert result['core_read_bytes'] == 34359736366
    assert result['theme_read_bytes'] == 34359735365
    assert result['presentation_read_bytes'] == 4294963292
    assert result['output_bytes'] == 805305134
    assert result['max_sdk_calls'] == 10000
    assert plan['baseline']['remaining_limits'] == remainder


def test_ambiguous_empty_claim_slot_and_old_failed_slot_never_auto_retry(tmp_path):
    slot, job = tmp_path / 'TEST_FIXTURE_slot', tmp_path / 'TEST_FIXTURE_job'
    for which in ('empty_slot', 'failed_slot', 'job_only'):
        if which == 'empty_slot':
            slot.mkdir()
        elif which == 'failed_slot':
            save(slot / 'failure.json', dict(status='FAILED_RETAINED_NO_RETRY'))
        else:
            (slot / 'failure.json').unlink()
            slot.rmdir()
            job.mkdir()
        with pytest.raises(ValueError, match='EXISTING_SLOT_RECONCILE_NO_RETRY'):
            control._empty_slot(slot, job)


def scope_rows():
    return dict(master_all=dict(parameters={'date': '2026-10-28'}, rows=[dict(code=c) for c in
        ('sh.600001', 'sz.000001', 'sz.300001', 'bj.430001', 'sh.000001', 'sz.000002')]),
        mainboard_sse=dict(parameters={}, rows=[{'证券代码': '600001'}]),
        master_szse=dict(parameters={}, rows=[{'A股代码': '000001', '板块': '主板'},
                              {'A股代码': '300001', '板块': '创业板'}]))


def test_reconstructed_scope_is_only_original_official_mainboard_intersection():
    assert source._scope(scope_rows()) == ['sh.600001', 'sz.000001']
    specs = source._batch_specs([f'sh.{600000 + index}' for index in range(101)])
    assert [len(spec.parameters['codes']) for spec in specs] == [50, 50, 1]
    assert all(spec.parameters['start_date'] == '1990-01-01'
               and spec.parameters['end_date'] == '2026-10-28'
               and spec.business_session == '2026-10-28' for spec in specs)
    assert len({spec.key for spec in specs}) == 3


@pytest.mark.parametrize('endpoint', ['master_all', 'mainboard_sse', 'master_szse'])
def test_scope_does_not_silently_deduplicate_conflicting_source_identity(endpoint):
    value = scope_rows()
    value[endpoint]['rows'].append(deepcopy(value[endpoint]['rows'][0]))
    with pytest.raises(ValueError, match='IDENTITY'):
        source._scope(value)


def test_complete_empty_query_does_not_certify_factor_one_or_price_comparability():
    code, day = 'sh.600001', date(2026, 10, 28)
    payload = dict(status='COMPLETE', parameters=dict(codes=[code], start_date='1990-01-01',
        end_date=day.isoformat()), rows=[], pagination_complete=True,
        coverage=dict(requested_codes=[code], security_statuses=[dict(code=code,
            status='COMPLETE', cursor_exhausted=True, row_count=0)]))
    envelope = factor_contract(payload)
    assert envelope['independent_complete_codes'] == [code]
    assert envelope['eligibility_inferred'] is False
    actual, status = comparable_rows(code, [(day, (dict(open='10', high='11', low='9', close='10'),
        'TEST_FIXTURE_RAW'))], {}, [([], 'TEST_FIXTURE_EMPTY', day.isoformat(), '1990-01-01')])
    assert actual == {} and status == 'NO_COMPARABLE_SOURCE'


@pytest.mark.parametrize('coverage', ['TEST_FIXTURE', 'RECONSTRUCTED_HISTORICAL'])
def test_late_recovery_completion_cannot_be_used_at_original_historical_cutoff(coverage):
    capture = dict(started_at='2026-11-01T01:00:00+00:00',
        completed_at='2026-11-01T01:01:00+00:00', first_seen_at='2026-11-01T01:00:30+00:00',
        request_receipt_id='TEST_FIXTURE_LATE_RECEIPT', receipts=[dict(
            receipt_id='TEST_FIXTURE_LATE_RECEIPT', first_seen_at='2026-11-01T01:00:30+00:00',
            ingested_at='2026-11-01T01:01:00+00:00')])
    with pytest.raises(ValueError, match='CUTOFF_PRECEDES_ACTUAL_CAPTURE_COMPLETION'):
        normalize_capture_with_report([capture], '2026-10-28',
            datetime(2026, 10, 28, 23, 0, tzinfo=timezone.utc), [date(2026, 10, 28)],
            coverage_type=coverage)


@pytest.fixture
def semantic_result(tmp_path, monkeypatch):
    """Only result semantics: an explicitly fake verified-input boundary.

    Native archive/hash authentication is exercised in the producer tests. This
    small boundary never calls a provider or claims authenticated source input.
    """
    root, job = tmp_path / 'TEST_FIXTURE_cache', tmp_path / 'TEST_FIXTURE_job'
    codes = ['sh.600001', 'sz.000001']
    records, batches = [], []
    for index, code in enumerate(codes):
        key = ('a' if index == 0 else 'b') * 64
        spec = dict(endpoint='adjustment_factors', parameters=dict(codes=[code],
            start_date='1990-01-01', end_date='2026-10-28'), business_session='2026-10-28',
            role='FULL_ANCHORED_FACTOR_WINDOW', current=False)
        record = dict(request_key=key, request_spec=spec, status='COMPLETE',
            first_seen_at='2026-10-28T16:10:00+00:00' if index == 0 else '2026-10-31T00:00:30+00:00',
            capture_attempt_dir=str(root / 'raw' / key / 'raw' / ('TEST_FIXTURE_attempt_' + str(index))),
            factor_envelope=dict(independent_complete_codes=[code]))
        records.append(record)
        batches.append(dict(request_key=key, request_spec=deepcopy(spec),
            action='REUSE_COMPLETE' if index == 0 else 'RETRY_PARTIAL_ONCE',
            original_record=deepcopy(record) if index == 0 else None))
    plan = dict(plan_hash='TEST_FIXTURE_PLAN', batches=batches, requested_codes=codes,
        requested_codes_count=2, cache_root=str(root), original_cache_inventory=[])
    result = dict(schema_version=source.VERSION, plan_hash=plan['plan_hash'], job=str(job),
        status='COMPLETE_SOURCE_RECOVERY_LATE', source_complete=True, complete_codes=codes,
        complete_codes_count=2, requested_codes_count=2, records=records,
        new_records=[deepcopy(records[1])], new_attempts=1, original_complete_reused=1,
        source_references=[], claimed_at='2026-10-31T00:00:00+00:00',
        acquisition_actual_cutoff='2026-10-31T00:01:00+00:00',
        historical_pit_restored=False, source_capability_issued=False, strategy_executed=False,
        completeness_scope='QUERY_COMPLETENESS_ONLY', adjustment_ready_not_established=True)
    class FixtureCache:
        def verify(self, record):
            assert record['request_key'] in {'a' * 64, 'b' * 64}
            return record
        def terminal(self):
            return []
    cache = FixtureCache()
    def fake_validated_plan(value, budget, *, appended=False):
        assert value is plan and appended is True
        return cache
    def fake_refs(refs, budget):
        assert refs == []
        return {}
    def fake_appends(value, records):
        assert value is plan and len(records) == 1
    monkeypatch.setattr(source, '_validate_plan', fake_validated_plan)
    monkeypatch.setattr(source, '_cache', lambda root, budget: cache)
    monkeypatch.setattr(source, '_verify_refs', fake_refs)
    monkeypatch.setattr(source, '_validate_appends', fake_appends)
    budget = SimpleNamespace(_meter_active=True, check=lambda *args, **kwargs: None)
    return SimpleNamespace(result=result, plan=plan, job=job, budget=budget)


def test_historical_recovery_verifier_retains_narrow_query_only_meaning(semantic_result):
    case = semantic_result
    verified = source.verify_result(case.result, case.plan, case.job, case.budget)
    assert verified['status'] == 'VERIFIED_LATE_SOURCE_REFERENCES'
    assert verified['source_complete'] is True
    assert verified['historical_pit_restored'] is verified['source_capability_issued'] is False
    assert case.result['new_records'][0]['request_key'] == case.plan['batches'][1]['request_key']
    assert case.result['new_records'][0]['first_seen_at'] > case.result['claimed_at']


@pytest.mark.parametrize('field,value', [
    ('status', 'INCOMPLETE_SOURCE_RECOVERY_LATE'), ('source_complete', False),
    ('historical_pit_restored', True), ('source_capability_issued', True),
    ('strategy_executed', True), ('new_attempts', 0), ('original_complete_reused', 2),
    ('new_attempts', True), ('original_complete_reused', True),
    ('new_records', []), ('completeness_scope', 'ADJUSTMENT_READY'),
    ('adjustment_ready_not_established', False),
    ('acquisition_actual_cutoff', '2026-10-31T00:00:20+00:00'),
    ('claimed_at', '2026-10-31T00:00:40+00:00'),
])
def test_result_cannot_promote_status_authority_counts_or_reverse_actual_clocks(semantic_result, field, value):
    case = semantic_result
    case.result[field] = value
    with pytest.raises(ValueError):
        source.verify_result(case.result, case.plan, case.job, case.budget)


def test_result_cannot_substitute_a_new_request_key(semantic_result):
    case = semantic_result
    case.result['records'][1]['request_key'] = 'c' * 64
    with pytest.raises(ValueError, match='RESULT_BATCH_SCOPE'):
        source.verify_result(case.result, case.plan, case.job, case.budget)


@pytest.fixture
def append_cache(tmp_path):
    root = tmp_path / 'TEST_FIXTURE_shared_cache'
    old_path = root / 'raw' / ('a' * 64) / 'raw/TEST_FIXTURE_original/old.json'
    save(old_path, {'status': 'PARTIAL', 'first_seen_at': '2026-10-28T16:10:00+00:00'})
    (root / 'index').mkdir()
    return dict(cache_root=str(root), old_path=old_path,
        original_cache_inventory=source._inventory(root),
        batches=[dict(request_key='a' * 64, action='RETRY_PARTIAL_ONCE'),
                 dict(request_key='b' * 64, action='NEW_ONCE'),
                 dict(request_key='c' * 64, action='REUSE_COMPLETE')])


def test_shared_cache_accepts_new_attempt_for_exact_key_and_retains_old_failure(append_cache):
    value = append_cache
    before = reference(value['old_path'])
    path = Path(value['cache_root']) / 'raw' / ('a' * 64) / 'raw/TEST_FIXTURE_fresh/new.json'
    save(path, {'status': 'COMPLETE', 'first_seen_at': '2026-10-31T00:00:30+00:00'})
    source._original_inventory(value, appended=True)
    assert reference(value['old_path']) == before


@pytest.mark.parametrize('mutation', ['replace_old', 'overwrite_old', 'unplanned_key', 'complete_key'])
def test_shared_cache_rejects_old_history_changes_or_unplanned_append(append_cache, mutation):
    value = append_cache
    path = value['old_path']
    if mutation == 'replace_old':
        raw = path.read_bytes()
        path.unlink()
        path.write_bytes(raw)
    elif mutation == 'overwrite_old':
        path.write_text('{"status":"COMPLETE"}', encoding='utf-8')
    else:
        key = ('c' if mutation == 'complete_key' else 'd') * 64
        save(Path(value['cache_root']) / 'raw' / key / 'raw/TEST_FIXTURE_new/new.json', {})
    with pytest.raises(ValueError, match='ORIGINAL_CACHE_CHANGED|UNAUTHORIZED_CACHE_APPEND'):
        source._original_inventory(value, appended=True)


@pytest.mark.parametrize('mutation', ['none', 'graft_old_attempt', 'second_attempt', 'second_index', 'duplicate_record'])
def test_append_terminal_is_exactly_one_owned_attempt_and_content_addressed_index(append_cache, mutation):
    value = append_cache
    root, key = Path(value['cache_root']), 'a' * 64
    attempt = root / 'raw' / key / 'raw' / ('1' * 32)
    record = dict(request_key=key, capture_attempt_dir=str(attempt), status='COMPLETE', test_fixture=True)
    save(attempt / 'TEST_FIXTURE_receipt.json', {'first_seen_at': '2026-10-31T00:00:30+00:00'})
    serialized = json.dumps(record, sort_keys=True, separators=(',', ':')).encode()
    save(root / 'index' / key / (hashlib.sha256(serialized).hexdigest() + '.json'), record)
    records = [record]
    if mutation == 'graft_old_attempt':
        save(value['old_path'].with_name('grafted.json'), {'TEST_FIXTURE': True})
    elif mutation == 'second_attempt':
        save(root / 'raw' / key / 'raw' / ('2' * 32) / 'extra.json', {'TEST_FIXTURE': True})
    elif mutation == 'second_index':
        save(root / 'index' / key / ('0' * 64 + '.json'), {'TEST_FIXTURE': True})
    elif mutation == 'duplicate_record':
        records.append(deepcopy(record))
    # The broad during-request check passes even a graft under the authorized
    # key; the final check must narrow that allowance to this exact attempt.
    source._original_inventory(value, appended=True)
    if mutation == 'none':
        source._validate_appends(value, records)
    else:
        with pytest.raises(ValueError, match='APPEND_OUTSIDE_EXACT_NEW_ATTEMPT_OR_INDEX|EXACTLY_ONE_FRESH_ATTEMPT_PER_KEY'):
            source._validate_appends(value, records)


@pytest.mark.parametrize('mutation', ['replace', 'delete'])
def test_completion_replaced_during_last_runtime_check_cannot_report_success(tmp_path, monkeypatch, mutation):
    # Reuse only the controller author's isolated input factory. The adversarial
    # mutation, physical-history oracle and outcome assertion are independent.
    path = Path(__file__).with_name('test_codex_explicit_failure_recovery_v1.py')
    spec = importlib.util.spec_from_file_location('TEST_FIXTURE_independent_control_inputs', path)
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    case = fixtures.make_case(tmp_path, monkeypatch)
    plan = fixtures.plan_case(case)
    historical = {p: reference(p) for p in case['job'].rglob('*') if p.is_file()}
    original_operation = reference(case['operation'])
    completion = Path(plan['slot']) / 'completion.json'
    original_check = case['runtime'].check_binding
    mutated = []
    replacement = []
    def replace_after_publication():
        original_check()
        if completion.exists() and not mutated:
            raw = completion.read_bytes()
            completion.unlink()
            if mutation == 'replace':
                completion.write_bytes(raw)
                replacement.append(reference(completion))
            mutated.append(True)
    monkeypatch.setattr(case['runtime'], 'check_binding', replace_after_publication)
    try:
        result = fixtures.execute_case(case, plan)
    except ValueError:
        result = None
    assert mutated == [True]
    assert result is not None and result['status'] == control.FAILED
    if mutation == 'replace':
        assert reference(completion) == replacement[0]
        assert result['rejected_completion']['status'] == 'UNOWNED_REPLACEMENT_PRESERVED'
    else:
        assert not completion.exists()
        assert result['rejected_completion']['status'] == 'COMPLETION_MISSING_NOT_REMOVED'
    assert control.status(case['runtime'], plan['operation_id'])['status'] == control.FAILED
    assert reference(case['operation']) == original_operation
    assert {p: reference(p) for p in historical} == historical
    assert case['collector'].calls == 1
    with pytest.raises(ValueError, match='EXISTING_SLOT_RECONCILE_NO_RETRY'):
        fixtures.execute_case(case, plan)
