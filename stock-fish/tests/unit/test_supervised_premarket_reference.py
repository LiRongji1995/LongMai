"""Independent TEST_FIXTURE checks of the bridge's stage and receipt boundaries.

Native control publication and physical reference reads remain real. Network,
SQL, provider processes, source attestation and workflow stages are replaced
only inside this explicitly isolated fixture; this cannot prove source/PIT.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import build_premarket_source_bundle_v3 as files
from scripts import codex_current_calendar_v1 as calendar
from scripts import codex_factor_checkpoint_premarket_v1 as gateway
from scripts import codex_recovered_premarket_v1 as bounded
from scripts import codex_sdk_tree_supervisor_v1 as supervisor
from scripts import codex_supervised_premarket_v1 as subject

REAL_CALENDAR_EXECUTE = calendar.execute


def metric(read=0):
    return dict(returned_read_bytes=dict(capture=read, core=0, theme=0, presentation=0),
        calls=dict(requests=0, sdk_reserved=0, http_reserved=0), failures=[],
        pending_request_results=0, elapsed_seconds=0)


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path / 'TEST_FIXTURE_bridge_reference'
    root.mkdir()

    def forbidden(*_args, **_kwargs):
        raise AssertionError('TEST_FIXTURE_NO_NETWORK_SQL_OR_PROVIDER')

    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)

    clock = SimpleNamespace(offset=0)
    origin = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)

    class FixtureClock:
        @staticmethod
        def now(tz=None):
            value = origin + timedelta(seconds=clock.offset)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(subject, 'datetime', FixtureClock)
    config = dict(schema_version='TEST_FIXTURE_ONLY', state_root=str(root / 'state'),
        capture=dict(offline=True, retry_failed=False), seed_sources=[],
        limits=deepcopy(bounded.CAPS))
    config['limits']['max_seconds'] = 3600
    config_path = root / 'configuration.json'
    config_path.write_text(json.dumps(config), encoding='utf-8')
    reference = files.read(config_path)[1]
    trace = []

    @contextmanager
    def exclusive():
        trace.append('lock_enter')
        try:
            yield
        finally:
            trace.append('lock_exit')

    runtime = SimpleNamespace(directory=root, registry_root=root,
        scope='TEST_FIXTURE_SCOPE', profile=dict(name='TEST_FIXTURE',
            config=str(config_path), python=sys.executable,
            storage=dict(expected_growth_bytes=1)), exclusive=exclusive,
        preflight=lambda **_kwargs: dict(allowed=True))
    guard = SimpleNamespace(directory=root, sha256='TEST_FIXTURE_MANIFEST',
        snapshot=dict(bundle_root=str(root), manifest=dict(sha256='TEST_FIXTURE_MANIFEST')),
        check=lambda: trace.append('source_check'))
    monkeypatch.setattr(subject, 'registered', lambda _runtime: deepcopy(config))
    monkeypatch.setattr(subject, 'source_refs', lambda *_args: (guard, [reference]))
    graph = dict(observation_date='2026-10-30', target_session='2026-11-02', metrics=metric(17))

    def graph_stage(_runtime):
        trace.append('graph')
        return deepcopy(graph)

    monkeypatch.setattr(subject, 'current_plan', graph_stage)
    receipt = dict(schema_version='TEST_FIXTURE_CALENDAR',
        status='COMPLETE_CURRENT_CALENDAR_SOURCE', cumulative_accounting=dict(
            calls=metric()['calls'], returned_read_bytes=metric(19)['returned_read_bytes'],
            output_bytes=0, supervisor_returned_read_bytes=23, actual_elapsed_seconds=0))

    def calendar_stage(*_args, **kwargs):
        trace.append('calendar')
        observations['calendar_limits'] = deepcopy(kwargs['limits'])
        return deepcopy(receipt)

    monkeypatch.setattr(calendar, 'execute', calendar_stage)
    monkeypatch.setattr(gateway, 'prior_request', lambda *_args: trace.append('prior_request'))
    observations = {}

    def supervised(command, **kwargs):
        trace.append('supervisor')
        context_path = command[command.index('--context') + 1]
        context, _ = subject.read(context_path)
        observations['context'] = deepcopy(context)
        observations['monitor_limits'] = deepcopy(kwargs['limits'])
        observations['derived'], _ = subject.read(context['derived_configuration']['path'])
        subject.write(context['result_path'], dict(schema_version=subject.VERSION,
            execution_exit_code=0, context_sha256=subject.digest(context),
            result=dict(complete_daily_delivery=True, execution_engine='original')))
        return dict(status='COMPLETE', child_exit_code=0, root_reaped=True,
            all_observed_owned_exited=True, all_observed_descendants_gone=True,
            terminal_supervisor_returned_read_bytes=29,
            terminal_actual_utc_elapsed_seconds=0, terminal_monotonic_elapsed_seconds=0)

    monkeypatch.setattr(supervisor, 'run_supervised', supervised)
    plan = dict(schema_version=subject.VERSION, scope=runtime.scope,
        runtime_name=runtime.profile['name'], original_limits=deepcopy(config['limits']),
        planning_local_date='2026-11-01', source_bundle=deepcopy(guard.snapshot),
        source_references=[reference], preparation_elapsed_seconds=2,
        calendar_plan=dict(schema_version='TEST_FIXTURE_CALENDAR',
            planning=dict(actual_elapsed_seconds=1, metrics=metric(7)),
            limits=deepcopy(config['limits'])),
        full_graph_preflight=dict(metrics=metric(11)), control_read_accounting=metric(13))
    slot = root / 'supervised-current-premarket' / '2026-11-01'
    return SimpleNamespace(root=root, clock=clock, config=config, runtime=runtime,
        plan=plan, guard=guard, refs=[reference], trace=trace, observations=observations,
        slot=slot)


def assert_failed(case):
    value = json.loads((case.slot / 'failure.json').read_text(encoding='utf-8'))
    assert value['complete_daily_delivery'] is False
    assert value['status'] == 'FAILED_OR_UNKNOWN'
    assert value['retry_authorized'] is False


def test_real_control_flow_uses_canonical_business_key_and_only_reduced_limits(case):
    result = subject.execute(case.runtime, deepcopy(case.plan))
    assert result['status'] == 'COMPLETE_CURRENT_ORIGINAL_DELIVERY'
    assert case.trace.index('calendar') < case.trace.index('graph')
    assert case.trace.index('graph') < case.trace.index('prior_request') < case.trace.index('supervisor')
    context = case.observations['context']
    assert context['request_key'] == 'premarket-2026-10-30-target-2026-11-02'
    assert context['engine'] == 'original'
    derived = case.observations['derived']
    assert {k:v for k,v in derived.items() if k != 'limits'} == {
        k:v for k,v in case.config.items() if k != 'limits'}
    assert all(0 < value <= case.config['limits'][name] for name, value in derived['limits'].items())
    assert derived['limits']['capture_read_bytes'] < case.config['limits']['capture_read_bytes']
    assert json.loads(Path(case.runtime.profile['config']).read_text()) == case.config
    assert not (case.slot / 'failure.json').exists()


def test_execution_source_validation_time_is_not_a_fresh_budget(case, monkeypatch):
    def slow_source(*_args):
        case.clock.offset = 3601
        return case.guard, case.refs
    monkeypatch.setattr(subject, 'source_refs', slow_source)
    with pytest.raises(Exception, match='TIME|BUDGET|DEADLINE|SECONDS'):
        subject.execute(case.runtime, deepcopy(case.plan))
    assert 'supervisor' not in case.trace
    assert not (case.slot / 'completion.json').exists()


def test_real_calendar_budget_combines_both_planning_prefixes_instead_of_taking_larger(case, monkeypatch):
    # Calendar's own 27 s / 19 B and the bridge's other 73 s / 11 B are
    # disjoint consumption. Exercise the actual callee's allocation, stopping
    # before it acquires a lock, creates a directory or can reach a provider.
    plan = deepcopy(case.plan)
    plan['preparation_elapsed_seconds'] = 100
    plan['calendar_plan']['planning'] = dict(actual_elapsed_seconds=27, metrics=metric(19))
    selected = {}
    monkeypatch.setattr(calendar, 'execute', REAL_CALENDAR_EXECUTE)
    monkeypatch.setattr(calendar, '_runtime', lambda *_args: False)
    monkeypatch.setattr(calendar, '_limits', lambda _runtime, requested=None:
        (deepcopy(case.config), deepcopy(requested or case.config['limits'])))
    def inspect_before_calendar_side_effects(limits):
        selected.update(limits)
        raise RuntimeError('TEST_FIXTURE_INSPECTED_CALENDAR_ALLOCATION')
    monkeypatch.setattr(calendar, '_budget', inspect_before_calendar_side_effects)
    with pytest.raises(RuntimeError, match='TEST_FIXTURE_INSPECTED_CALENDAR_ALLOCATION'):
        subject.execute(case.runtime, plan)
    assert selected['max_seconds'] + 100 + subject.FINAL_RESERVE_SECONDS <= case.config['limits']['max_seconds']
    assert selected['capture_read_bytes'] + 19 + 11 <= case.config['limits']['capture_read_bytes']
    assert 'supervisor' not in case.trace
    assert_failed(case)


def test_failed_calendar_cannot_start_a_workflow(case, monkeypatch):
    def failure(*_args, **_kwargs):
        case.trace.append('calendar')
        raise RuntimeError('TEST_FIXTURE_CALENDAR_FAILED')
    monkeypatch.setattr(calendar, 'execute', failure)
    with pytest.raises(RuntimeError, match='TEST_FIXTURE_CALENDAR_FAILED'):
        subject.execute(case.runtime, deepcopy(case.plan))
    assert 'graph' not in case.trace and 'supervisor' not in case.trace
    assert_failed(case)
    assert (case.slot / 'claim.json').exists()


def test_prior_failed_business_request_blocks_any_new_workflow(case, monkeypatch):
    def no_retry(_runtime, identity):
        assert identity['request_key'] == 'premarket-2026-10-30-target-2026-11-02'
        case.trace.append('prior_request')
        raise ValueError('TEST_FIXTURE_OLD_FAILED_BUSINESS_REQUEST')
    monkeypatch.setattr(gateway, 'prior_request', no_retry)
    with pytest.raises(ValueError, match='TEST_FIXTURE_OLD_FAILED_BUSINESS_REQUEST'):
        subject.execute(case.runtime, deepcopy(case.plan))
    assert 'supervisor' not in case.trace
    assert_failed(case)
    assert (case.slot / 'claim.json').exists()


@pytest.mark.parametrize('mutation', ['replace', 'delete'])
def test_final_completion_path_mutation_cannot_return_success_or_erase_foreign_file(case, monkeypatch, mutation):
    original = subject.write
    foreign = b'{"TEST_FIXTURE_FOREIGN_REPLACEMENT":true}'
    def write_then_replace(path, value):
        result = original(path, value)
        path = Path(path)
        if path == case.slot / 'completion.json':
            if mutation == 'replace':
                replacement = case.root / 'TEST_FIXTURE_external_candidate.json'
                replacement.write_bytes(foreign)
                os.replace(replacement, path)
            else:
                path.unlink()
        return result
    monkeypatch.setattr(subject, 'write', write_then_replace)
    with pytest.raises(Exception):
        subject.execute(case.runtime, deepcopy(case.plan))
    assert_failed(case)
    assert (case.slot / 'claim.json').exists()
    if mutation == 'replace':
        assert (case.slot / 'completion.json').read_bytes() == foreign
    else:
        assert not (case.slot / 'completion.json').exists()
