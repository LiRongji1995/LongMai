"""TEST_FIXTURE: cumulative budgets and immutable invocation semantics."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from scripts import codex_recovered_premarket_v1 as bounded
from scripts import codex_supervised_premarket_v2 as subject


def usage(**changes):
    values = dict(elapsed_seconds=100.1,
        calls=dict(requests=1, sdk_reserved=3, http_reserved=0, sdk_actual=None,
                   sdk_actual_known=2, sdk_actual_unknown_requests=1),
        reads=dict(capture=1234, core=0, theme=0, presentation=0),
        output_bytes=2048, supervisor_read_bytes=400)
    values.update(changes)
    return values


def test_one_original_budget_accounts_calendar_and_preserves_unknown_actual():
    limits, consumed = deepcopy(bounded.CAPS), usage()
    before = deepcopy((limits, consumed))
    remaining = subject.remaining_limits(limits, **consumed)
    assert remaining['max_requests'] == 1023
    assert remaining['max_sdk_calls'] == 9997
    assert remaining['max_seconds'] == 10669
    assert remaining['capture_read_bytes'] == limits['capture_read_bytes']-1234
    assert remaining['supervisor_read_bytes'] == limits['supervisor_read_bytes']-400
    assert remaining['output_bytes'] == limits['output_bytes']-2048
    assert remaining['cache_bytes'] == limits['cache_bytes']
    assert remaining['tree_rss_bytes'] == limits['tree_rss_bytes']
    assert (limits, consumed) == before
    assert consumed['calls']['sdk_actual'] is None


@pytest.mark.parametrize('changes,reason', [
    ({'elapsed_seconds':10800}, 'ORIGINAL_BUDGET_EXHAUSTED:max_seconds'),
    ({'elapsed_seconds':float('nan')}, 'FINITE_NONNEGATIVE_ELAPSED'),
    ({'elapsed_seconds':True}, 'FINITE_NONNEGATIVE_ELAPSED'),
    ({'calls':dict(requests=1,sdk_reserved=10000,http_reserved=0)}, 'max_sdk_calls'),
    ({'calls':dict(requests=1,sdk_reserved=None,http_reserved=0)}, 'SDK_RESERVED'),
    ({'calls':dict(requests=1,sdk_reserved=3.1,http_reserved=0)}, 'INTEGER_CALL_COUNTER'),
    ({'reads':dict(capture=-1)}, 'FINITE_NONNEGATIVE_capture_read_bytes'),
    ({'reads':dict(unaccounted=1)}, 'UNKNOWN_READ_PHASE'),
    ({'output_bytes':bounded.CAPS['output_bytes']}, 'ORIGINAL_BUDGET_EXHAUSTED:output_bytes'),
    ({'supervisor_read_bytes':bounded.CAPS['supervisor_read_bytes']}, 'supervisor_read_bytes'),
])
def test_budget_unknown_negative_or_exhausted_fails_before_collection(changes,reason):
    with pytest.raises(ValueError, match=reason):
        subject.remaining_limits(deepcopy(bounded.CAPS), **usage(**changes))


def configuration():
    return dict(schema_version='STANDALONE_PREMARKET_WORKFLOW_V1',
        state_root='/TEST_FIXTURE/state', minimum_amount_cny=300000000,
        capture=dict(retry_failed=False, offline=False), limits=deepcopy(bounded.CAPS), seed_sources=[])


def test_reduced_invocation_budget_keeps_strategy_and_original_configuration():
    original = configuration()
    before = deepcopy(original)
    derived = deepcopy(original)
    derived['limits'] = subject.remaining_limits(original['limits'], **usage())
    subject.validate_derived_configuration(original, derived)
    assert original == before


@pytest.mark.parametrize('mutation', ['strategy', 'state', 'retry', 'extra', 'increase', 'bool', 'nan', 'missing'])
def test_invocation_cannot_change_strategy_state_retry_or_expand_limits(mutation):
    original, derived = configuration(), configuration()
    if mutation == 'strategy': derived['minimum_amount_cny'] = 1
    if mutation == 'state': derived['state_root'] = '/TEST_FIXTURE/other'
    if mutation == 'retry': derived['capture']['retry_failed'] = True
    if mutation == 'extra': derived['seed_sources'] = ['/TEST_FIXTURE/forged']
    if mutation == 'increase': derived['limits']['max_seconds'] += 1
    if mutation == 'bool': derived['limits']['tree_rss_bytes'] = True
    if mutation == 'nan': derived['limits']['max_seconds'] = float('nan')
    if mutation == 'missing': del derived['limits']['tree_rss_bytes']
    with pytest.raises(ValueError):
        subject.validate_derived_configuration(original, derived)


def test_sleep_interval_uses_real_utc_even_when_monotonic_does_not_advance(monkeypatch):
    monkeypatch.setattr(subject.time, 'monotonic', lambda:101)
    now = datetime.now(timezone.utc)
    assert subject._elapsed(100,now-timedelta(seconds=90)) >= 90


def test_original_planner_limits_are_not_mutated(monkeypatch):
    from scripts import codex_premarket_preflight_v1 as preflight
    before = deepcopy(preflight.sources.LIMITS)
    # The original body still runs; stop at its first cache boundary after the
    # source facade is selected. A diagnostic adjustment must not patch globals.
    monkeypatch.setattr(bounded,'configuration',lambda *a:configuration())
    def stop(*args): raise RuntimeError('TEST_FIXTURE_STOP')
    runtime = SimpleNamespace(profile={'backend':'premarket'},check_binding=stop)
    with pytest.raises(RuntimeError,match='TEST_FIXTURE_STOP'):
        subject.current_plan(runtime)
    assert preflight.sources.LIMITS == before


def test_monitor_and_worker_cannot_each_spend_original_read_or_output_cap():
    original = deepcopy(bounded.CAPS)
    worker, monitor = subject.partition_limits(original)
    assert worker['supervisor_read_bytes'] + monitor['supervisor_read_bytes'] + subject.CONTROL_READ_RESERVE + subject.CHILD_CONTROL_READ_RESERVE == original['supervisor_read_bytes']
    assert worker['output_bytes'] + monitor['output_bytes'] + subject.CONTROL_OUTPUT_RESERVE == original['output_bytes']
    assert worker['max_seconds'] == monitor['max_seconds'] == original['max_seconds']
    assert original == bounded.CAPS


def test_monitor_reservation_cannot_expand_small_remaining_budget():
    small = deepcopy(bounded.CAPS)
    small['supervisor_read_bytes'] = subject.TREE_READ_RESERVE
    with pytest.raises(ValueError,match='RESERVATION_EXCEEDS'):
        subject.partition_limits(small)


@pytest.mark.parametrize('field,value', [('status','FAILED'),('root_reaped',False),
    ('all_observed_descendants_gone',False),('child_exit_code',137)])
def test_inner_success_never_overrides_supervisor_failure(field,value):
    monitor = dict(status='COMPLETE',root_reaped=True,all_observed_descendants_gone=True,child_exit_code=0)
    monitor[field] = value
    inner = dict(schema_version=subject.VERSION,context_sha256='TEST_FIXTURE',execution_exit_code=0,
                 result=dict(complete_daily_delivery=True))
    with pytest.raises(ValueError):
        subject.checked_workflow_result(monitor,inner,'TEST_FIXTURE')


def test_supervision_success_does_not_replace_daily_delivery_validation():
    monitor = dict(status='COMPLETE',root_reaped=True,all_observed_descendants_gone=True,child_exit_code=0)
    inner = dict(schema_version=subject.VERSION,context_sha256='TEST_FIXTURE',execution_exit_code=0,
                 result=dict(complete_daily_delivery=False))
    with pytest.raises(ValueError,match='COMPLETE_DAILY_DELIVERY_REQUIRED'):
        subject.checked_workflow_result(monitor,inner,'TEST_FIXTURE')


def test_inventory_plan_larger_than_legacy_small_receipt_roundtrips_without_overwrite(tmp_path):
    path = tmp_path/'TEST_FIXTURE_large_plan'/'plan.json'
    value = dict(role='TEST_FIXTURE', inventory_evidence='x'*(2*1024**2))
    reference = subject.write(path,value)
    saved, same = subject.read(path,reference)
    assert saved == value and same == reference
    with pytest.raises(FileExistsError):
        subject.write(path,dict(role='TEST_FIXTURE_replacement'))
    assert subject.read(path,reference)[0] == value


def test_bridge_control_limit_is_finite_and_rejects_before_creating_file(tmp_path):
    path = tmp_path/'TEST_FIXTURE_oversize.json'
    with pytest.raises(ValueError,match='CONTROL_FILE_LIMIT'):
        subject.write(path,dict(value='x'*subject.CONTROL_LIMIT))
    assert not path.exists()


@pytest.mark.parametrize('previous_status', ['FAILED_OR_UNKNOWN', 'COMPLETE_CURRENT_ORIGINAL_DELIVERY'])
def test_v2_cannot_bypass_existing_v1_day_claim(tmp_path, monkeypatch, previous_status):
    import json
    from contextlib import nullcontext
    root = tmp_path/'TEST_FIXTURE_cross_version_day'
    local = str(datetime.now(timezone.utc).astimezone(subject.SH).date())
    slot = root/'supervised-current-premarket'/local
    slot.mkdir(parents=True)
    claim = slot/'claim.json'
    claim.write_text(json.dumps(dict(schema_version='CODEX_SUPERVISED_CURRENT_PREMARKET_V1',
        status=previous_status, retry_authorized=False)))
    before = claim.read_bytes()
    config = configuration()
    snapshot = dict(bundle_root='/TEST_FIXTURE/bundle', manifest=dict(sha256='TEST_FIXTURE'))
    def denied(**_kwargs):
        raise AssertionError('TEST_FIXTURE_MUST_STOP_BEFORE_NEW_ADMISSION_OR_SDK')
    runtime = SimpleNamespace(directory=root, scope='TEST_FIXTURE',
        profile=dict(name='TEST_FIXTURE',storage=dict(expected_growth_bytes=1)),
        exclusive=lambda:nullcontext(), preflight=denied)
    monkeypatch.setattr(subject,'registered',lambda _runtime:config)
    monkeypatch.setattr(subject,'source_refs',lambda *_args:(SimpleNamespace(snapshot=snapshot),[]))
    plan = dict(schema_version=subject.VERSION,scope=runtime.scope,runtime_name='TEST_FIXTURE',
        original_limits=config['limits'],planning_local_date=local,source_bundle=snapshot,
        source_references=[],preparation_elapsed_seconds=0)
    with pytest.raises(ValueError,match='EXISTING_DAY_CLAIM_NO_RETRY'):
        subject._execute(runtime,plan,started=subject.time.monotonic(),utc_started=datetime.now(timezone.utc))
    assert claim.read_bytes() == before
    assert sorted(p.name for p in slot.iterdir()) == ['claim.json']
