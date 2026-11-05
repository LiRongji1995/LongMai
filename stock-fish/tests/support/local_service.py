"""Minimal local research fixture; no web server, model registry or account engine."""
from pathlib import Path
from stockfish_core.storage import SQLiteStore
from stockfish_core.jobs import JobService
from stockfish_core.yaogu.candidate_service import CandidateService


class ResearchService:
    def __init__(self, root, *, store=None, jobs=None, allow_test_fixture=False, data_dir=None):
        self.root = Path(root)
        self.store = store or SQLiteStore(self.root / "TEST_FIXTURE.sqlite3")
        self.jobs = jobs or JobService(self.store, max_workers=1, max_pending=20, recover=False)
        self.candidates = CandidateService(self.store, self.jobs, self.root / "yaogu",
                                          allow_test_fixture=allow_test_fixture)
        self.candidate_scheduler = None

    def close(self):
        if self.candidate_scheduler is not None:
            self.candidate_scheduler.stop()
        self.jobs.shutdown(wait=True)
