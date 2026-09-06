"""Synthetic engineering proofs for scoped price-context reuse; no market claims."""
from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest

from tests.yaogu.fixtures import make_bundle
from stockfish_core.yaogu import strategy_tracking as tracking


def signal(bundle, count, run_id='TEST_CONTEXT'):
    day = bundle.sessions[60]
    bars = {bar.instrument_id: bar for bar in bundle.bars if bar.session == day}
    items = [{'instrument_id': f'TEST{i:03d}', 'rank': i + 1, 'S_PV': 90 - i,
              'comparable_close': str(bars[f'TEST{i:03d}'].comparable_close),
              'comparable_basis': bars[f'TEST{i:03d}'].comparable_basis} for i in range(count)]
    return {'run_id': run_id, 'input_id': 'TEST_FIXTURE_INPUT', 'observation_date': str(day),
            'results': {'PV_OBSERVATION_V1': {'strategy_id': 'PV_OBSERVATION_V1', 'items': items}},
            'selection_results': {'PV_FOCUS_PREVIEW_V0.1': {'selected_ids': [row['instrument_id'] for row in items]}}}


def archive_setup(bundle, cutoff, run):
    initial = tracking.build_strategy_tracking(run, bundle, cutoff)
    events = [{'episode_id': episode['episode_id'], 'state': 'ARCHIVED',
               'effective_session': str(bundle.sessions[70])} for episode in initial['episodes']]
    return {'existing_episodes': initial['episodes'], 'episode_events': events}


def test_context_and_default_paths_produce_identical_labels_and_exit_shadows():
    bundle, cutoff = make_bundle(4)
    run = signal(bundle, 4)
    options = archive_setup(bundle, cutoff, run)
    context = tracking.OutcomeContext(bundle, cutoff)
    expected_labels = tracking.evaluate_strategy_experiment_outcomes(run, bundle, cutoff, horizons=(1, 2, 61))
    actual_labels = tracking.evaluate_strategy_experiment_outcomes(run, bundle, cutoff, horizons=(1, 2, 61), _context=context)
    assert actual_labels == expected_labels
    expected_tracking = tracking.build_strategy_tracking(run, bundle, cutoff, **options)
    actual_tracking = tracking.build_strategy_tracking(run, bundle, cutoff, _context=context, **options)
    assert actual_tracking == expected_tracking
    assert len(actual_tracking['shadow_outcomes']) == 8
    assert {label['horizon'] for label in actual_tracking['shadow_outcomes']} == {5, 10}
    assert all(label['observation_date'] == str(bundle.sessions[70]) for label in actual_tracking['shadow_outcomes'])


@pytest.mark.parametrize('function', [tracking.evaluate_strategy_experiment_outcomes, tracking.build_strategy_tracking])
@pytest.mark.parametrize('mismatch', ['input', 'cutoff'])
def test_context_rejects_wrong_input_identity_or_evaluation_cutoff(function, mismatch):
    bundle, cutoff = make_bundle(1)
    run = signal(bundle, 1)
    context = tracking.OutcomeContext(bundle, cutoff)
    other_bundle = bundle.model_copy() if mismatch == 'input' else bundle
    other_cutoff = cutoff + timedelta(seconds=1) if mismatch == 'cutoff' else cutoff
    with pytest.raises(ValueError, match='outcome context'):
        function(run, other_bundle, other_cutoff, _context=context)


def test_context_cannot_be_mutated_and_rejects_naive_cutoff():
    bundle, cutoff = make_bundle(1)
    context = tracking.OutcomeContext(bundle, cutoff)
    with pytest.raises(FrozenInstanceError):
        context.input_hash = 'REPLACED'
    with pytest.raises(TypeError):
        context.bars[('TEST000', bundle.observation_date)] = None
    assert isinstance(context.sessions, tuple)
    with pytest.raises(ValueError, match='timezone'):
        tracking.OutcomeContext(bundle, cutoff.replace(tzinfo=None))


@pytest.mark.parametrize('reuse_across_runs', [False, True])
def test_many_episode_exits_scan_and_hash_the_input_only_once(monkeypatch, reuse_across_runs):
    bundle, cutoff = make_bundle(8)
    run = signal(bundle, 8)
    options = archive_setup(bundle, cutoff, run)
    calls = {'visible_bars': 0, 'bundle_hash': 0}
    original_visible, original_digest = tracking._visible_bars, tracking.digest

    def visible_spy(value, evaluated_at):
        calls['visible_bars'] += 1
        return original_visible(value, evaluated_at)

    def hash_spy(value):
        if value is bundle:
            calls['bundle_hash'] += 1
        return original_digest(value)

    monkeypatch.setattr(tracking, '_visible_bars', visible_spy)
    monkeypatch.setattr(tracking, 'digest', hash_spy)
    context = tracking.OutcomeContext(bundle, cutoff) if reuse_across_runs else None
    for index in range(20 if reuse_across_runs else 1):
        revision = {**run, 'run_id': f'TEST_CONTEXT_REVISION_{index:02d}'}
        result = tracking.build_strategy_tracking(revision, bundle, cutoff, _context=context, **options)
        assert len(result['shadow_outcomes']) == 16
        if reuse_across_runs:
            assert len(tracking.evaluate_strategy_experiment_outcomes(revision, bundle, cutoff, horizons=(1,), _context=context)) == 8
    assert calls == {'visible_bars': 1, 'bundle_hash': 1}


def test_run_and_metric_policy_are_logical_keys_but_evaluations_are_revisions():
    bundle, cutoff = make_bundle(1)
    run = {**signal(bundle, 1), 'parameter_version': 'TEST_PARAMS_V1',
           'implementation_version': 'TEST_IMPLEMENTATION_V1', 'config_hash': 'TEST_CONFIG_HASH',
           'source_hash': 'TEST_SOURCE_HASH', 'spec_hash': 'TEST_SPEC_HASH'}
    revisions = [tracking.evaluate_strategy_experiment_outcomes(run, bundle, cutoff + timedelta(seconds=i), horizons=(1,))[0]
                 for i in range(10)]
    assert len({row['logical_label_id'] for row in revisions}) == 1
    assert len({row['observation_id'] for row in revisions}) == 10
    assert all(row['metric_policy_id'] == tracking.METRIC_POLICY_ID for row in revisions)
    selected = tracking.select_label_revisions(revisions, cutoff + timedelta(seconds=9))
    assert selected == revisions[-1:]
    other_run = {**run, 'run_id': 'TEST_OTHER_RUN'}
    other = tracking.evaluate_strategy_experiment_outcomes(other_run, bundle, cutoff, horizons=(1,))[0]
    assert other['logical_label_id'] != revisions[0]['logical_label_id']
    report = tracking.build_strategy_feedback_report(run, revisions + [other], [], cutoff + timedelta(seconds=9),
                                                      runs=[run, other_run], sessions=bundle.sessions)
    assert report['counts']['stored_revisions'] == 11
    assert report['counts']['mature'] == 2
    assert len(report['revision_selection']) == 2
    # Changing the outcome input is another revision of this run, never a new logical signal.
    revised_input = bundle.model_copy(update={'coverage_type': 'UNVERIFIABLE_AS_OF'})
    input_revision = tracking.evaluate_strategy_experiment_outcomes(run, revised_input, cutoff, horizons=(1,))[0]
    assert input_revision['input_hash'] != revisions[0]['input_hash']
    assert input_revision['logical_label_id'] == revisions[0]['logical_label_id']


def test_archived_state_survives_prior_observations_without_repeating_event():
    bundle, cutoff = make_bundle(1)
    run = signal(bundle, 1)
    options = archive_setup(bundle, cutoff, run)
    archived = tracking.build_strategy_tracking(run, bundle, cutoff, **options)
    repeated = {**run, 'run_id': 'TEST_REPEAT', 'observation_date': str(bundle.sessions[61])}
    replayed = tracking.build_strategy_tracking(repeated, bundle, cutoff,
                                                existing_episodes=archived['episodes'],
                                                prior_observations=archived['observations'])
    assert replayed['episodes'][0]['episode_id'] == archived['episodes'][0]['episode_id']
    assert all(row['state'] == 'ARCHIVED' for row in replayed['observations'] if row['session'] >= str(bundle.sessions[70]))
    assert replayed['shadow_outcomes'] == archived['shadow_outcomes']
    later = {**run, 'run_id': 'TEST_REENTRY', 'observation_date': str(bundle.sessions[82])}
    reentry = tracking.build_strategy_tracking(later, bundle, cutoff,
                                               existing_episodes=archived['episodes'],
                                               prior_observations=archived['observations'])
    assert reentry['episodes'][0]['previous_episode_id'] == archived['episodes'][0]['episode_id']
    assert reentry['episodes'][0]['episode_id'] != archived['episodes'][0]['episode_id']


@pytest.mark.parametrize('evidence_kind', ['event', 'observation'])
def test_future_recorded_archive_is_not_visible_at_earlier_cutoff(evidence_kind):
    bundle, cutoff = make_bundle(1)
    run = signal(bundle, 1)
    initial = tracking.build_strategy_tracking(run, bundle, cutoff)
    evidence = {'episode_id': initial['episodes'][0]['episode_id'], 'state': 'ARCHIVED',
                'effective_session': str(bundle.sessions[70]), 'session': str(bundle.sessions[70]),
                'recorded_at' if evidence_kind == 'event' else 'evaluated_at': (cutoff + timedelta(seconds=1)).isoformat()}
    options = {'episode_events' if evidence_kind == 'event' else 'prior_observations': [evidence]}
    value = tracking.build_strategy_tracking(run, bundle, cutoff, existing_episodes=initial['episodes'], **options)
    assert not any(row['state'] == 'ARCHIVED' for row in value['observations'])
