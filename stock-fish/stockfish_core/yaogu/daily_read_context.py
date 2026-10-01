"""Request-local SQLite snapshot for the read-only observation consumer.

Keep the existing delivery/outcome state machines. Cache decoded records only
inside this request; never attach a cache or alternate store to the live service.
"""
from contextlib import contextmanager
from copy import copy
from functools import cache
from types import MethodType, SimpleNamespace

from ..storage import SQLiteStore
from ..jobs import JobService
from .daily_research_service import OUTCOMES


class _SnapshotStore(SQLiteStore):
    def __init__(self, connection):
        # No migrations, directory creation, or second database connection.
        self.connection = connection
        self.get_document = cache(self.get_document)
        self.list_documents = cache(self.list_documents)

    @contextmanager
    def transaction(self, *, write=True):
        if write:
            raise RuntimeError('DAILY_OBSERVATION_READ_ONLY')
        yield self.connection


@contextmanager
def observation_read_context(service):
    """One WAL read transaction, including jobs and downstream outcome revisions.

    Full namespace reads remain O(history); repeated deserialization and database
    scans are removed. The request holds its snapshot until rendering completes.
    """
    if not isinstance(service.store, SQLiteStore):
        # Existing injected in-memory implementations keep the single-day contract.
        yield service
        return
    with service.store.transaction(write=False) as connection:
        connection.execute('PRAGMA query_only=ON')
        # Establish the read version now, before any concurrent publisher commits.
        connection.execute('SELECT count(*) FROM schema_migrations').fetchone()
        view = copy(service)
        view.store = _SnapshotStore(connection)
        view.jobs = copy(service.jobs)
        view.jobs.store = view.store
        view.jobs.list = cache(view.jobs.list)
        view._all = cache(view._all)
        sessions = tuple(service._daily_research_sessions())
        clock = service._daily_research_now()
        view.scheduler = SimpleNamespace(sessions=sessions, clock=lambda: clock)

        original_get = view.jobs.get

        @cache
        def referenced_jobs():
            # Include event-linked cross-day work and old primary indexes. Do
            # not infer a date window that could drop retries/publication proof.
            ids = set()
            for namespace in ('candidate_schedule_events', 'daily_primary_attempts',
                              'stage_aware_prospective_primary_runs'):
                for row in view._all(namespace):
                    ids.update((row.get('job_id'), row.get('scheduler_job_id')))
                    if namespace == 'candidate_schedule_events':
                        ids.add(row.get('job_id') or row.get('event_id'))
            ids.discard(None)
            result = dict.fromkeys(ids)
            keys = sorted(ids)
            for start in range(0, len(keys), 500):
                batch = keys[start:start + 500]
                marks = ','.join('?' for _ in batch)
                for row in connection.execute('SELECT * FROM jobs WHERE job_id IN (' + marks + ')', batch):
                    result[row['job_id']] = view.jobs._decode(row)
            return result

        @cache
        def get_job(job_id):
            known = referenced_jobs()
            return known[job_id] if job_id in known else original_get(job_id)

        view.jobs.get = get_job if isinstance(service.jobs, JobService) else cache(original_get)

        @cache
        def outcomes_by_run():
            result = {}
            for row in view._all(OUTCOMES):
                result.setdefault(row.get('run_id'), []).append(row)
            return result

        def outcome_rows(self, run_id=None):
            if run_id is None:
                return list(self._all(OUTCOMES))
            return list(outcomes_by_run().get(run_id, ()))

        @cache
        def publication_jobs():
            result = {}
            for row in view._all('daily_primary_attempts'):
                result.setdefault(row.get('run_id'), set()).add(row.get('job_id'))
            return result

        def publication_job_ids(self, run_id):
            return publication_jobs().get(run_id, set())

        view._daily_outcome_rows = MethodType(outcome_rows, view)
        view._daily_publication_job_ids = MethodType(publication_job_ids, view)
        try:
            yield view
        finally:
            # Bound-method caches form reference cycles; release historical
            # payloads now instead of waiting for cyclic garbage collection.
            for cached in (view.store.get_document, view.store.list_documents,
                           view.jobs.get, view.jobs.list, view._all,
                           referenced_jobs, outcomes_by_run, publication_jobs):
                cached.cache_clear()
