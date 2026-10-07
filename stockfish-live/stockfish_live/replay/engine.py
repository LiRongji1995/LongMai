from __future__ import annotations

import asyncio
import math
from datetime import date
from pathlib import Path
from time import perf_counter

from ..common import LiveError, canonical_json, digest
from ..ingest.collector import Collector


def percentile(values, p):
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * p
    left, right = math.floor(position), math.ceil(position)
    return values[left] + (values[right] - values[left]) * (position - left)


def replay_speed(speed: str) -> float | None:
    if speed == "max":
        return None
    try:
        rate = float(speed)
    except ValueError as exc:
        raise LiveError("INVALID_REPLAY_SPEED") from exc
    if not math.isfinite(rate) or rate <= 0:
        raise LiveError("INVALID_REPLAY_SPEED")
    return rate


class ReplayEngine:
    def __init__(self, provider, clock, calendar, scope, output: Path, *, timeout: float = 10., processor=None):
        self.provider, self.clock, self.calendar, self.scope = provider, clock, calendar, scope
        self.output = Path(output)
        self.collector = Collector(provider, calendar, scope, timeout=timeout, processor=processor)

    async def run(self, day: date, *, speed: str = "max") -> dict:
        # Validate all configuration before creating an output directory.
        ticks = self.calendar.ticks(day)
        self.calendar.require_session(day, ticks[0], tuple(i.instrument_id[-2:] for i in self.scope.instruments))
        if self.scope.available_at > ticks[0]:
            raise LiveError("SCOPE_NOT_YET_AVAILABLE")
        if self.scope.calendar_version != self.calendar.version:
            raise LiveError("SCOPE_CALENDAR_MISMATCH")
        rate = replay_speed(speed)
        try:
            self.output.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise LiveError("REPLAY_OUTPUT_EXISTS") from exc
        (self.output / "snapshots").mkdir()
        hashes, latencies, failed_cycles = [], [], 0
        all_warnings = set()
        try:
            with (self.output / "latency.jsonl").open("x", encoding="utf-8") as timings:
                for tick in ticks:
                    cycle_start = perf_counter()
                    self.clock.advance(tick)
                    record, latency = await self.collector.step(tick)
                    all_warnings.update(record["state"]["warnings"])
                    failed_cycles += record["cycle_error"] is not None
                    target = self.output / "snapshots" / f"{tick:%H%M}.json"
                    with target.open("x", encoding="utf-8") as handle:
                        handle.write(canonical_json(record) + "\n")
                    latency["internal_with_persistence_ms"] = (perf_counter() - cycle_start) * 1000 - latency["provider_call_ms"]
                    timings.write(canonical_json(latency) + "\n")
                    hashes.append(record["snapshot_id"])
                    latencies.append(latency["internal_with_persistence_ms"])
                    if rate is not None and tick != ticks[-1]:
                        # Sleep in small chunks; simulates minute cadence while
                        # keeping interrupt/steering responsive.
                        remaining = max(0., 60. / rate - (perf_counter() - cycle_start))
                        while remaining:
                            chunk = min(remaining, 1.)
                            await asyncio.sleep(chunk)
                            remaining -= chunk
            summary = {"date": day.isoformat(), "ticks": len(ticks), "completed_minutes": 240,
                "failed_cycles": failed_cycles, "warnings": sorted(all_warnings),
                "snapshot_sequence_hash": digest(hashes), "final_state": record["state"],
                "pit_status": record["pit_status"], "scope": self.scope.scope,
                "internal_with_persistence_p50_ms": percentile(latencies, .5),
                "internal_with_persistence_p95_ms": percentile(latencies, .95),
                "internal_with_persistence_max_ms": max(latencies),
                "forecast_status": record["forecast_status"], "market_edge": "NOT_ESTABLISHED"}
            if self.collector.processor is not None:
                from ..storage.research_store import ResearchStore
                self.collector.processor.persist(ResearchStore(self.output))
                summary["model"] = {"version": self.collector.processor.forecast.config.model_version,
                    "feature_version": self.collector.processor.feature_version,
                    "configuration_hash": self.collector.processor.config_hash,
                    "history_hash": self.collector.processor.prepared.history_hash,
                    "preloaded_states": self.collector.processor.prepared.state_count,
                    "preloaded_mature_outcomes": self.collector.processor.prepared.outcome_count,
                    "published_forecasts": len(self.collector.processor.forecasts),
                    "parameters_status": "UNVALIDATED"}
            with (self.output / "summary.json").open("x", encoding="utf-8") as handle:
                handle.write(canonical_json(summary) + "\n")
            return summary
        except BaseException:
            # Cancellation and summary publication are incomplete runs too.
            # A full/unavailable filesystem must not hide the original error.
            try:
                with (self.output / "FAILED").open("x", encoding="utf-8") as handle:
                    handle.write("REPLAY_INCOMPLETE\n")
            except OSError:
                pass
            raise
