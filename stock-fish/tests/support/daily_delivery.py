"""Read-only delivery and historical availability, in-memory TEST_FIXTURE only."""
from copy import deepcopy
from datetime import date

import pytest

from stockfish_core.yaogu.daily_research_service import DailyResearchMixin


def stamp(clock, day='2026-09-21'):
    return f'{day}T{clock}:00+08:00'


class FixtureJobs:
    def __init__(self):
        self.rows = {}

    def get(self, key):
        return deepcopy(self.rows.get(key))

    def list(self, *, kind, limit, offset):
        return deepcopy([j for j in self.rows.values() if j['kind'] == kind][offset:offset + limit])

    def submit(self, *args, **kwargs):
        pytest.fail('delivery GET must not submit work')


class FixtureDelivery(DailyResearchMixin):
    def __init__(self):
        self.docs, self.snapshots = {}, {}
        self.jobs = FixtureJobs()

    def _all(self, namespace):
        return deepcopy(self.docs.get(namespace, []))

    def _daily_research_sessions(self):
        return [date(2026, 9, 21), date(2026, 9, 22)]

    def daily_recommendation_snapshot(self, run_id):
        return deepcopy(self.snapshots[run_id])

    def query(self, clock='20:40', day='2026-09-21'):
        original = deepcopy((self.docs, self.jobs.rows, self.snapshots))
        result = self.daily_research_delivery('2026-09-22', as_of=stamp(clock, day))
        assert (self.docs, self.jobs.rows, self.snapshots) == original
        assert result['read_only'] is True
        return result


def identity():
    return {'signal_implementation_version': 'TEST_FIXTURE_V2',
            'signal_config_hash': 'TEST_FIXTURE_CONFIG', 'validation_epoch_id': 'TEST_FIXTURE_EPOCH'}


def add_job(probe, key, *, state='SUCCEEDED', created='20:30', started='20:30', finished='20:32',
            mode='SCHEDULED_EVENING'):
    payload = {'research_identity': identity()}
    if mode == 'SCHEDULED_EVENING':
        kind = 'scheduled_candidate_capture'
        payload.update(session='2026-09-21', market_session='EVENING', root_job_id='TEST_FIXTURE_primary')
    else:
        kind = 'daily_primary_strategy'
        payload.update(expected_observation_session='2026-09-21', daily_primary_mode=mode)
    job = {'job_id': key, 'kind': kind, 'payload': payload, 'state': state,
           'created_at': stamp(created), 'started_at': stamp(started) if started else None,
           'finished_at': stamp(finished) if finished else None,
           'updated_at': stamp(finished or started or created), 'error': {'code': 'TEST_FIXTURE_ERROR'}}
    probe.jobs.rows[key] = job
    return job


def add_attempt(probe, key, status, *, clock='20:31', run_id=None, mode='SCHEDULED_EVENING'):
    row = {'attempt_id': 'TEST_FIXTURE_attempt_' + key, 'job_id': key, 'final_status': status,
           'timestamp': stamp(clock), 'actual_decision_cutoff': stamp('20:30'),
           'expected_observation_session': '2026-09-21', 'daily_primary_mode': mode,
           'run_id': run_id, 'stage': 'READINESS' if status == 'NOT_READY' else 'STRATEGY',
           'error': {'code': 'TEST_FIXTURE_STRATEGY_FAILURE'} if status == 'FAILED' else None,
           'blocking_requirements': ['TEST_FIXTURE_MISSING_PRICE'] if status == 'NOT_READY' else [],
           'missing_evidence': ['TEST_FIXTURE_MISSING_PRICE'] if status == 'NOT_READY' else [],
           'strategy_version': 'TEST_FIXTURE_V2', 'config_version': 'TEST_FIXTURE_CONFIG'}
    probe.docs.setdefault('daily_primary_attempts', []).append(row)
    return row


def add_ready(probe, key='TEST_FIXTURE_primary', *, finished='20:34', generated='20:31'):
    run_id = 'TEST_FIXTURE_run_' + key
    add_job(probe, key, finished=finished)
    add_attempt(probe, key, 'READY', run_id=run_id)
    probe.docs.setdefault('stage_aware_prospective_primary_runs', []).append({
        'run_id': run_id, 'target_session': '2026-09-22', 'scheduler_job_id': key,
        'decision_cutoff': stamp('20:30')})
    probe.snapshots[run_id] = {'identity': {'run_id': run_id, 'observation_date': '2026-09-21',
        'target_session': '2026-09-22', 'generated_at': stamp(generated), 'data_cutoff': stamp('20:30')}}
    return run_id
