"""In-memory scheduler progress, independent of storage and market readiness."""
from __future__ import annotations

from datetime import datetime
import math
import threading
import time


class SchedulerHealth:
    def __init__(self, interval, *, monotonic=time.monotonic):
        if isinstance(interval, bool) or not math.isfinite(interval) or interval <= 0:
            raise ValueError('scheduler interval must be finite and positive')
        self.stale_after = 3 * float(interval)
        self.monotonic = monotonic
        self.lock = threading.Lock()
        self.started_at = self.finished_at = self.succeeded_at = None
        self.success_clock = None
        self.running = False
        self.failures = 0
        self.error_type = None
        self.diagnostic_write_failed = False

    @staticmethod
    def _stamp(now):
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('scheduler clock must be timezone-aware')
        return now.isoformat()

    def begin(self, now):
        with self.lock:
            self.started_at = self._stamp(now)
            self.running = True

    def success(self, now):
        with self.lock:
            self.finished_at = self.succeeded_at = self._stamp(now)
            self.success_clock = self.monotonic()
            self.running = False
            self.failures = 0
            self.error_type = None
            self.diagnostic_write_failed = False

    def failure(self, now, error):
        with self.lock:
            self.finished_at = self._stamp(now)
            self.running = False
            self.failures += 1
            # Never persist exception messages, payloads, or provider credentials.
            self.error_type = type(error).__name__

    def diagnostic_failure(self):
        with self.lock:
            self.diagnostic_write_failed = True

    def snapshot(self, *, thread_alive):
        with self.lock:
            age = None if self.success_clock is None else max(0., self.monotonic() - self.success_clock)
            reasons = []
            if not thread_alive:
                reasons.append('SCHEDULER_THREAD_NOT_ALIVE')
            if self.success_clock is None:
                reasons.append('SCHEDULER_NO_SUCCESSFUL_TICK')
            elif age > self.stale_after:
                reasons.append('SCHEDULER_PROGRESS_STALE')
            if self.failures:
                reasons.append('SCHEDULER_TICK_FAILED')
            if self.diagnostic_write_failed:
                reasons.append('SCHEDULER_DIAGNOSTIC_WRITE_FAILED')
            return dict(contract='SCHEDULER_PROGRESS_HEALTH_V1',
                        status='not_ready' if reasons else 'ok', reason_codes=reasons,
                        thread_alive=bool(thread_alive), tick_running=self.running,
                        last_tick_started_at=self.started_at, last_tick_finished_at=self.finished_at,
                        last_successful_tick_at=self.succeeded_at, success_age_seconds=age,
                        stale_after_seconds=self.stale_after, consecutive_failures=self.failures,
                        last_error_type=self.error_type,
                        diagnostic_write_failed=self.diagnostic_write_failed,
                        market_readiness='NOT_EVALUATED')
