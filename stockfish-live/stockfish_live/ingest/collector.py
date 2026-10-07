"""One collection/consumption path for any provider; no replay strategy branch."""
from __future__ import annotations

import asyncio
import math
from datetime import datetime
from time import perf_counter

from pydantic import ValidationError

from ..common import LiveError, aware, digest
from ..contracts.market import DatasetScope, MarketSnapshot, MinuteBar, SourceReceipt
from ..contracts.quality import effective_pit_status
from .normalizer import QualityMonitor


class Collector:
    def __init__(self, provider, calendar, scope: DatasetScope, *, timeout: float = 10., stale_minutes: int = 2):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        self.provider, self.calendar, self.scope = provider, calendar, scope
        self.timeout = timeout
        self.monitor = QualityMonitor(calendar, tuple(i.instrument_id for i in scope.instruments), stale_minutes)
        self._types = {i.instrument_id: i.asset_type for i in scope.instruments}
        self._since: datetime | None = None
        self._last_now: datetime | None = None
        self._inputs: list[MinuteBar] = []
        self._receipts: dict[str, SourceReceipt] = {}
        self.config_hash = digest({"schema": "LIVE_COLLECTION_V1", "stale_minutes": stale_minutes, "timeout": timeout,
                                   "calendar": calendar.version, "scope": scope})

    async def step(self, now: datetime) -> tuple[dict, dict]:
        now = aware(now)
        if self._last_now and now <= self._last_now:
            raise LiveError("CLOCK_NOT_ADVANCING")
        self._last_now = now
        if self.scope.available_at > now:
            raise LiveError("SCOPE_NOT_YET_AVAILABLE")
        if self.scope.calendar_version != self.calendar.version:
            raise LiveError("SCOPE_CALENDAR_MISMATCH")
        if (self.scope.pit_status == "TEST_FIXTURE") != (self.calendar.pit_status == "TEST_FIXTURE"):
            raise LiveError("DATA_PROVENANCE_MIXED")
        self.calendar.require_session(now.date(), now, tuple(i[-2:] for i in self._types))
        start = perf_counter()
        error = None
        received = ()
        provider_warnings = ()
        provider_quality = "VALID"
        try:
            snapshot = await asyncio.wait_for(self.provider.get_market_snapshot(now, since=self._since), self.timeout)
        except Exception as exc:
            error = "PROVIDER_TIMEOUT" if isinstance(exc, TimeoutError) else (
                exc.code if isinstance(exc, LiveError) else "PROVIDER_FAILURE")
            snapshot = None
        if snapshot is None and error is None:
            error = "INVALID_PROVIDER_DATA"
        provider_ms = (perf_counter() - start) * 1000
        processing_start = perf_counter()
        if snapshot is not None:
            try:
                # Revalidate external data even if a provider constructed a model
                # using model_copy/model_construct, which bypass validation.
                if not isinstance(snapshot, (MarketSnapshot, dict)):
                    raise LiveError("INVALID_PROVIDER_DATA")
                snapshot = MarketSnapshot.model_validate(snapshot.model_dump(mode="json")
                    if isinstance(snapshot, MarketSnapshot) else snapshot)
                if snapshot.as_of != now:
                    raise LiveError("PROVIDER_AS_OF_MISMATCH")
                provider_warnings = snapshot.warnings
                if snapshot.quality == "INVALID":
                    raise LiveError("INVALID_PROVIDER_DATA")
                provider_quality = snapshot.quality
                received = tuple(MinuteBar.model_validate(b.model_dump(mode="json")) for b in snapshot.bars)
                receipts = {r.source_id: SourceReceipt.model_validate(r.model_dump(mode="json")) for r in snapshot.receipts}
                if len(receipts) != len(snapshot.receipts):
                    raise LiveError("DUPLICATE_RECEIPT")
                for bar in received:
                    if self._types.get(bar.instrument_id) != bar.asset_type:
                        raise LiveError("INSTRUMENT_TYPE_MISMATCH")
                    if (bar.pit_status == "TEST_FIXTURE") != (self.scope.pit_status == "TEST_FIXTURE"):
                        raise LiveError("DATA_PROVENANCE_MIXED")
                self.monitor.accept(received, receipts, now)
                self._inputs.extend(received)
                self._receipts.update(receipts)
                self._since = now  # Failed/invalid batches do not advance cursor.
            except (LiveError, ValidationError, ValueError) as exc:
                error = exc.code if isinstance(exc, LiveError) else "INVALID_PROVIDER_DATA"
                received = ()
        state = self.monitor.state(now)
        warnings = sorted(set(state["warnings"]) | set(provider_warnings) | ({error} if error else set()))
        severity = {"VALID": 0, "PARTIAL": 1, "STALE": 2, "GAPPED": 3, "INVALID": 4}
        if (received or provider_quality in ("STALE", "GAPPED")) and severity[provider_quality] > severity[state["quality"]]:
            state["quality"] = provider_quality
        quality_warning = {"GAPPED": "DATA_GAP", "STALE": "DATA_STALE", "PARTIAL": "DATA_PARTIAL"}.get(provider_quality)
        if quality_warning and (received or provider_quality in ("STALE", "GAPPED")):
            warnings = sorted(set(warnings) | {quality_warning})
        if error:
            if snapshot is not None or error == "INVALID_PROVIDER_DATA":
                state["quality"] = "INVALID"
            elif state["quality"] not in ("GAPPED", "STALE"):
                state["quality"] = "PARTIAL"
        cutoff = max((b.available_at for b in self._inputs), default=None)
        input_hash = digest({"config_hash": self.config_hash,
            "bars": sorted(self._inputs, key=lambda b: (b.timestamp, b.instrument_id)),
            "receipts": [self._receipts[r] for r in sorted(self._receipts)]})
        pit_status = effective_pit_status([self.scope.pit_status, self.calendar.pit_status,
            *(b.pit_status for b in self._inputs), *(r.pit_status for r in self._receipts.values())])
        record = {"schema_version": "LIVE_REPLAY_STATE_V1", "as_of": now.isoformat(),
            "data_cutoff": cutoff.isoformat() if cutoff else None, "input_hash": input_hash,
            "config_hash": self.config_hash, "scope": self.scope.scope, "pit_status": pit_status,
            "declared_scope_pit_status": self.scope.pit_status,
            "cycle_error": error,
            "state": {**state, "warnings": warnings},
            "received_bars": [b.model_dump(mode="json") for b in received],
            "forecast_status": "NOT_IMPLEMENTED_PHASE1", "market_edge": "NOT_ESTABLISHED"}
        record["snapshot_id"] = digest(record)
        provider_delay_ms = max(((b.available_at - b.timestamp).total_seconds() * 1000 for b in received), default=None)
        latency = {"as_of": now.isoformat(), "provider_call_ms": provider_ms,
            "provider_event_to_available_ms": provider_delay_ms,
            "internal_ms": (perf_counter() - processing_start) * 1000}
        return record, latency
