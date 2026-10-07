"""Bounded wall-clock capture using the same Collector and research pipeline."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from ..calendar import TradingCalendar
from ..common import LiveError, SHANGHAI, aware, canonical_json, digest
from ..contracts.market import DatasetScope
from ..providers.http_feed import HttpFeedProvider
from ..replay.engine import percentile
from ..runtime import load_pipeline
from ..storage.parquet_store import ParquetStore, _json_bytes, _publish
from ..storage.research_store import ResearchStore
from .collector import Collector


class _CapturedFeed:
    def __init__(self, provider):
        self.provider, self.error = provider, None

    async def get_market_snapshot(self, as_of, *, since=None):
        if self.error is not None:
            raise self.error
        return await self.provider.get_market_snapshot(as_of, since=since)


def configured_provider(root, scope, calendar, timeout, now_fn):
    path = Path(root) / "provider.json"
    if not path.is_file() or path.is_symlink():
        raise LiveError("LIVE_PROVIDER_UNAVAILABLE")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("schema_version") != "LIVE_PROVIDER_CONFIG_V1"
        or set(value) - {"schema_version", "url", "provider_name", "trusted_published_timestamps"}
        or not {"url", "provider_name"} <= set(value)
        or not isinstance(value.get("trusted_published_timestamps", False), bool)):
        raise LiveError("INVALID_PROVIDER_CONFIG")
    if scope.pit_status == "TEST_FIXTURE":
        raise LiveError("TEST_FIXTURE_LIVE_DENIED")
    return HttpFeedProvider(value["url"], scope, provider_name=value["provider_name"], timeout=timeout,
        trusted_published_timestamps=value.get("trusted_published_timestamps", False),
        calendar=calendar, now_fn=now_fn)


async def run_live(root, *, output=None, max_ticks=240, timeout=10., provider=None,
                   now_fn=None, sleeper=asyncio.sleep, _test_fixture=False):
    """Capture one session only. No scheduler, service switch or implicit source.

    Injectable clocks/providers are exclusively for labelled engineering tests.
    A real run records the actual post-capture clock, including sub-minute delay.
    """
    root = Path(root)
    if provider is None and not (root / "provider.json").is_file():
        raise LiveError("LIVE_PROVIDER_UNAVAILABLE")
    if isinstance(max_ticks, bool) or not 1 <= max_ticks <= 240:
        raise LiveError("INVALID_MAX_TICKS")
    if not math.isfinite(timeout) or timeout <= 0:
        raise LiveError("INVALID_PROVIDER_TIMEOUT")
    now_fn = now_fn or (lambda: datetime.now(SHANGHAI))
    initial = aware(now_fn())
    calendar = TradingCalendar.from_file(root / "calendar.json")
    scope = DatasetScope.model_validate_json((root / "dataset.json").read_text(encoding="utf-8"))
    if scope.pit_status == "TEST_FIXTURE" and not _test_fixture:
        raise LiveError("TEST_FIXTURE_LIVE_DENIED")
    calendar.require_session(initial.date(), initial, tuple(item.instrument_id[-2:] for item in scope.instruments))
    minutes = [tick for tick in calendar.minutes(initial.date()) if tick >= initial.replace(second=0, microsecond=0)]
    if not minutes:
        raise LiveError("LIVE_SESSION_CLOSED")
    pipeline = load_pipeline(root, calendar, scope, initial.date(), known_at=initial)
    provider = provider or configured_provider(root, scope, calendar, timeout, now_fn)
    feed = _CapturedFeed(provider)
    collector = Collector(feed, calendar, scope, timeout=timeout, processor=pipeline)
    output = Path(output) if output else root / "live-runs" / uuid4().hex
    if output.exists():
        raise LiveError("LIVE_OUTPUT_EXISTS")
    output.mkdir(parents=True)
    (output / "snapshots").mkdir()
    hashes, latencies, warnings = [], [], set()
    failed = 0
    archive = ParquetStore(root)
    archived_sources = set()
    try:
        with (output / "latency.jsonl").open("x", encoding="utf-8") as timing:
            for minute in minutes[:max_ticks]:
                # Fetch after a minute completes; never fabricate the cutoff.
                due = minute + timedelta(milliseconds=100)
                while aware(now_fn()) < due:
                    await sleeper(min(1., (due - aware(now_fn())).total_seconds()))
                capture_start = perf_counter()
                feed.error = None
                try:
                    await asyncio.wait_for(provider.capture(), timeout)
                except Exception as exc:
                    feed.error = LiveError("PROVIDER_TIMEOUT" if isinstance(exc, TimeoutError) else
                        exc.code if isinstance(exc, LiveError) else "PROVIDER_FAILURE")
                capture_ms = (perf_counter() - capture_start) * 1000
                now = aware(now_fn())
                if now.date() != initial.date():
                    raise LiveError("LIVE_SESSION_CHANGED")
                if now.replace(second=0, microsecond=0) != minute:
                    raise LiveError("LIVE_CAPTURE_MISSED_MINUTE")
                cycle_start = perf_counter()
                record, latency = await collector.step(now)
                parents = {receipt.source_id: (raw, receipt) for raw, receipt in provider.source_records()}
                for source, (raw, receipt) in parents.items():
                    if source not in archived_sources:
                        archive.archive_source(receipt, raw)
                        archived_sources.add(source)
                if record["received_bars"]:
                    from ..contracts.market import MinuteBar
                    grouped = {}
                    for row in record["received_bars"]:
                        bar = MinuteBar.model_validate(row)
                        grouped.setdefault(bar.source_id, []).append(bar)
                    archive.append_many((bars, parents[source][1], parents[source][0]) for source, bars in grouped.items())
                _publish(output / "snapshots" / f"{minute:%H%M}.json", _json_bytes(record))
                latency.update({"capture_ms": capture_ms,
                    "internal_with_persistence_ms": (perf_counter() - cycle_start) * 1000 - latency["provider_call_ms"]})
                timing.write(canonical_json(latency) + "\n")
                timing.flush()
                hashes.append(record["snapshot_id"])
                latencies.append(latency["internal_with_persistence_ms"])
                failed += record["cycle_error"] is not None
                warnings.update(record["state"]["warnings"])
        pipeline.persist(ResearchStore(output))
        summary = {"date": initial.date().isoformat(), "ticks": len(hashes), "mode": "LIVE_CAPTURE",
            "failed_cycles": failed, "warnings": sorted(warnings), "snapshot_sequence_hash": digest(hashes),
            "final_state": record["state"], "pit_status": record["pit_status"], "scope": scope.scope,
            "forecast_status": record["forecast_status"], "market_edge": "NOT_ESTABLISHED",
            "internal_with_persistence_p50_ms": percentile(latencies, .5),
            "internal_with_persistence_p95_ms": percentile(latencies, .95),
            "internal_with_persistence_max_ms": max(latencies),
            "session_complete": minutes[min(max_ticks, len(minutes)) - 1] == calendar.minutes(initial.date())[-1]}
        _publish(output / "summary.json", _json_bytes(summary))
        return {"output": str(output.resolve()), "results": [summary]}, 2 if failed else 0
    except BaseException:
        try:
            _publish(output / "FAILED", b"LIVE_CAPTURE_INCOMPLETE\n")
        except (OSError, LiveError):
            pass
        raise
