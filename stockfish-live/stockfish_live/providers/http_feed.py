"""Configured JSON feed adapter with local, actual HTTP receipt clocks.

No endpoint is selected automatically. The server cannot set local availability.
Historical queries only inspect already captured responses; they do not rebase
today's downloads into a historical cutoff.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Callable, Literal, Mapping
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import Field, field_validator, model_validator

from ..common import FrozenModel, LiveError, aware
from ..contracts.market import DatasetScope, MarketSnapshot, MinuteBar, SourceReceipt, validate_receipt
from ..contracts.quality import DataQuality


@dataclass(frozen=True)
class HttpResponse:
    body: bytes
    status: int = 200
    headers: Mapping[str, str] = field(default_factory=lambda: {"Content-Type": "application/json"})


class FeedBar(FrozenModel):
    instrument_id: str
    exchange: Literal["SH", "SZ", "BJ"]
    symbol: str
    asset_type: Literal["STOCK", "INDEX"]
    timestamp: datetime
    open: float | None = Field(default=None, gt=0)
    high: float | None = Field(default=None, gt=0)
    low: float | None = Field(default=None, gt=0)
    close: float | None = Field(default=None, gt=0)
    volume: float | None = Field(default=None, ge=0)
    turnover: float | None = Field(default=None, ge=0)
    quality: DataQuality

    @field_validator("timestamp")
    @classmethod
    def clock(cls, value):
        return aware(value)

    @field_validator("open", "high", "low", "close", "volume", "turnover", mode="before")
    @classmethod
    def numeric(cls, value):
        if isinstance(value, bool):
            raise ValueError("boolean is not a market value")
        return value


class FeedEnvelope(FrozenModel):
    schema_version: Literal["LIVE_HTTP_FEED_V1"]
    provider: str = Field(min_length=1)
    timestamp_label: Literal["END"]
    volume_unit: Literal["SHARE"]
    turnover_unit: Literal["CNY"]
    price_adjustment: Literal["RAW"]
    state: Literal["AVAILABLE", "EMPTY"]
    published_at: datetime | None
    bars: tuple[FeedBar, ...]

    @field_validator("published_at")
    @classmethod
    def clock(cls, value):
        return aware(value) if value is not None else value

    @model_validator(mode="after")
    def explicit_empty(self):
        if (self.state == "EMPTY") != (not self.bars):
            raise ValueError("EMPTY must be explicit and contain no bars")
        return self


async def _http_get(url: str, timeout: float) -> HttpResponse:
    def request():
        try:
            with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=timeout) as response:
                body = response.read(32 * 1024 * 1024 + 1)
                return HttpResponse(body=body, status=response.status, headers=dict(response.headers))
        except HTTPError as error:
            return HttpResponse(body=b"", status=error.code)
    return await asyncio.to_thread(request)


def _strict_json(raw: bytes):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("nonfinite JSON constant")

    return json.loads(raw.decode("utf-8"), object_pairs_hook=object_pairs, parse_constant=constant)


class HttpFeedProvider:
    """One-session, append-only capture of an explicitly configured feed.

    ``transport(url, timeout)`` is an async callable returning HttpResponse.
    ``now_fn`` reads actual receive/processing time; custom clocks and transport
    are useful for explicit TEST_FIXTURE tests and are not publication evidence.
    ``trusted_published_timestamps`` is an operator declaration about the feed's
    independently checked publication semantics, never a feed-controlled flag.
    """

    def __init__(self, url: str, scope: DatasetScope, *, provider_name: str = "http_feed",
                 timeout: float = 10.0, trusted_published_timestamps: bool = False,
                 fixture_mode: bool = False, transport=None, now_fn: Callable[[], datetime] | None = None,
                 clock=None, calendar=None, capture_if_empty: bool = False):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("an explicit HTTP(S) feed URL is required")
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")
        if not provider_name or (provider_name == "mock" and not fixture_mode):
            raise ValueError("invalid feed provider name")
        if clock is not None and now_fn is not None:
            raise ValueError("provide clock or now_fn, not both")
        self.scope = DatasetScope.model_validate(scope.model_dump(mode="json"))
        if fixture_mode != (self.scope.pit_status == "TEST_FIXTURE"):
            raise LiveError("DATA_PROVENANCE_MIXED")
        self.url, self.provider_name, self.timeout = url, provider_name, timeout
        self.trusted_published_timestamps, self.fixture_mode = trusted_published_timestamps, fixture_mode
        self.transport = transport or _http_get
        self.now_fn = now_fn or ((lambda: clock.now) if clock is not None else lambda: datetime.now(timezone.utc))
        self.calendar = calendar
        self.capture_if_empty = capture_if_empty
        self._types = {item.instrument_id: item.asset_type for item in scope.instruments}
        self._bars: dict[tuple[str, datetime], MinuteBar] = {}
        self._wire_rows: dict[tuple[str, datetime], FeedBar] = {}
        self._last: dict[str, datetime] = {}
        self._receipts: dict[str, SourceReceipt] = {}
        self._raw_sources: dict[str, bytes] = {}
        self._seen_payloads: set[str] = set()
        self._session_date = None
        self._lock = asyncio.Lock()
        self._last_feed_state = "UNKNOWN"
        self._capture_error: LiveError | None = None
        self._last_capture_source_id: str | None = None
        self._feed_states: dict[str, tuple[str, int]] = {}

    def _cutoff(self, value: datetime, actual_now: datetime | None = None):
        cutoff = aware(value)
        if cutoff > (aware(actual_now) if actual_now is not None else aware(self.now_fn())):
            raise LiveError("FUTURE_ACCESS_DENIED")
        if self.scope.available_at > cutoff:
            raise LiveError("SCOPE_NOT_YET_AVAILABLE")
        if self._session_date is not None and cutoff.date() != self._session_date:
            raise LiveError("FEED_SESSION_MISMATCH")
        if self.calendar is not None:
            if self.scope.calendar_version != self.calendar.version:
                raise LiveError("SCOPE_CALENDAR_MISMATCH")
            self.calendar.require_session(cutoff.date(), cutoff, tuple(identity[-2:] for identity in self._types))
        return cutoff

    def _parse(self, response: HttpResponse, received_at: datetime, day):
        if (not isinstance(response, HttpResponse) or not isinstance(response.body, bytes)
            or not isinstance(response.headers, Mapping)):
            raise LiveError("FEED_RESPONSE_INVALID")
        if response.status != 200:
            raise LiveError("FEED_HTTP_STATUS")
        headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
        if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise LiveError("FEED_CONTENT_TYPE_INVALID")
        if len(response.body) > 32 * 1024 * 1024:
            raise LiveError("FEED_RESPONSE_TOO_LARGE")
        try:
            envelope = FeedEnvelope.model_validate(_strict_json(response.body))
        except (ValueError, TypeError) as error:
            raise LiveError("FEED_SCHEMA_INVALID") from error
        if envelope.provider != self.provider_name:
            raise LiveError("FEED_PROVIDER_MISMATCH")
        if envelope.published_at is not None and envelope.published_at > received_at:
            raise LiveError("FEED_FUTURE_PUBLICATION")
        if self.trusted_published_timestamps and envelope.published_at is None:
            raise LiveError("FEED_PUBLICATION_REQUIRED")
        # Stage the complete response before mutating the accepted archive.
        last = dict(self._last)
        seen = set()
        staged = []
        for row in sorted(envelope.bars, key=lambda bar: (bar.timestamp, bar.instrument_id)):
            identity = (row.instrument_id, row.timestamp)
            if identity in seen:
                raise LiveError("FEED_DUPLICATE_BAR")
            seen.add(identity)
            if self._types.get(row.instrument_id) != row.asset_type:
                raise LiveError("FEED_SCOPE_MISMATCH")
            if row.timestamp > received_at:
                raise LiveError("FEED_FUTURE_BAR")
            if envelope.published_at is not None and row.timestamp > envelope.published_at:
                raise LiveError("FEED_PUBLICATION_BEFORE_EVENT")
            if row.timestamp.date() != day:
                raise LiveError("FEED_SESSION_MISMATCH")
            if row.timestamp.second or row.timestamp.microsecond or not (
                time(9, 31) <= row.timestamp.time() <= time(11, 30)
                or time(13, 1) <= row.timestamp.time() <= time(15)
            ):
                raise LiveError("INVALID_BAR_TIME")
            previous_row = self._wire_rows.get(identity)
            if previous_row is not None:
                if previous_row != row:
                    raise LiveError("FEED_BAR_REVISION_DENIED")
                continue  # identical retransmission preserves the first actual receipt
            if row.instrument_id in last and row.timestamp <= last[row.instrument_id]:
                raise LiveError("FEED_LATE_BAR_DENIED")
            if row.quality == "INVALID":
                raise LiveError("INVALID_MINUTE_BAR")
            last[row.instrument_id] = row.timestamp
            staged.append(row)
        return envelope, staged, last

    async def capture(self) -> SourceReceipt:
        """Capture HTTP now; never relabel the receive time as a requested tick.

        Failures are retained for the Collector's next cache read until a
        subsequent capture succeeds. Existing pending records remain intact.
        """
        async with self._lock:
            try:
                receipt = await self._capture()
            except LiveError as error:
                self._capture_error = error
                raise
            except (ValueError, TypeError, AttributeError) as error:
                self._capture_error = LiveError("FEED_CAPTURE_INVALID")
                raise self._capture_error from error
            self._capture_error = None
            return receipt

    async def _capture(self) -> SourceReceipt:
        started_at = aware(self.now_fn())
        cutoff = self._cutoff(started_at, started_at)
        try:
            response = await asyncio.wait_for(self.transport(self.url, self.timeout), self.timeout)
        except TimeoutError as error:
            raise LiveError("PROVIDER_TIMEOUT") from error
        except LiveError:
            raise
        except Exception as error:
            raise LiveError("FEED_TRANSPORT_FAILURE") from error
        received_at = aware(self.now_fn())
        if received_at < started_at:
            raise LiveError("ACTUAL_CLOCK_REVERSED")
        envelope, staged_rows, last = self._parse(response, received_at, cutoff.date())
        source_id = hashlib.sha256(response.body).hexdigest()
        if source_id not in self._seen_payloads:
            basis = "PUBLISHED" if self.trusted_published_timestamps else "RECEIVED"
            source_time = envelope.published_at if self.trusted_published_timestamps else received_at
            pit_status = "TEST_FIXTURE" if self.fixture_mode else "VERIFIED_PIT" if self.trusted_published_timestamps else "RESEARCH_ONLY"
            receipt = SourceReceipt(source_id=source_id, provider=self.provider_name, source_timestamp=source_time,
                source_timestamp_basis=basis, received_at=received_at, available_at=received_at, pit_status=pit_status)
            try:
                staged_bars = tuple(MinuteBar(**row.model_dump(), provider=self.provider_name,
                    source_timestamp=source_time, source_timestamp_basis=basis, available_at=received_at,
                    source_id=source_id, pit_status=pit_status) for row in staged_rows)
            except ValueError as error:
                raise LiveError("FEED_MINUTE_CONTRACT_INVALID") from error
            for bar in staged_bars:
                validate_receipt(bar, receipt)
            available_at = aware(self.now_fn())
            if available_at < received_at:
                raise LiveError("ACTUAL_CLOCK_REVERSED")
            # All validation completed before this real processing timestamp.
            # Moving only availability forward preserves every checked order.
            receipt = receipt.model_copy(update={"available_at": available_at})
            staged_bars = tuple(bar.model_copy(update={"available_at": available_at}) for bar in staged_bars)
            # Commit only after every row and parent has passed validation.
            self._receipts[source_id] = receipt
            self._raw_sources[source_id] = response.body
            self._seen_payloads.add(source_id)
            self._last = last
            self._session_date = cutoff.date()
            for row, bar in zip(staged_rows, staged_bars):
                identity = (bar.instrument_id, bar.timestamp)
                self._wire_rows[identity] = row
                self._bars[identity] = bar
        self._last_feed_state = envelope.state
        self._last_capture_source_id = source_id
        if source_id not in self._feed_states:
            self._feed_states[source_id] = (envelope.state, len(self._feed_states))
        return self._receipts[source_id]

    async def poll(self, as_of: datetime, *, since: datetime | None = None) -> MarketSnapshot:
        """Explicit convenience capture plus a clock-gated cache read.

        Live runners should call capture(), then read using the real wall time
        after capture, allowing the latest completed minute to be consumed.
        """
        self._cutoff(as_of)
        if since is not None and aware(since) > aware(as_of):
            raise LiveError("INVALID_TIME_RANGE")
        await self.capture()
        return await self.get_market_snapshot(as_of, since=since)

    async def get_market_snapshot(self, as_of: datetime, *, since: datetime | None = None) -> MarketSnapshot:
        if self.capture_if_empty and not self._receipts:
            await self.capture()
        async with self._lock:
            actual_now = aware(self.now_fn())
            cutoff = self._cutoff(as_of, actual_now)
            lower = self._cutoff(since, actual_now) if since is not None else None
            if lower is not None and lower > cutoff:
                raise LiveError("INVALID_TIME_RANGE")
            if self._capture_error is not None:
                raise LiveError(self._capture_error.code)
            bars = self._visible(cutoff, lower)
            refs = {bar.source_id for bar in bars}
            visible_states = [(self._receipts[identity].available_at, order, identity, state)
                for identity, (state, order) in self._feed_states.items() if self._receipts[identity].available_at <= cutoff]
            last_visible_state = max(visible_states, default=None)
            last_receipt = self._receipts[last_visible_state[2]] if last_visible_state is not None else None
            if (last_receipt is not None and last_receipt.available_at <= cutoff
                and (lower is None or last_receipt.available_at >= lower)):
                refs.add(last_receipt.source_id)
            visible_receipts = tuple(self._receipts[identity] for identity in sorted(refs))
            pending = any(receipt.available_at > cutoff for receipt in self._receipts.values())
            warnings = (["FEED_CAPTURE_PENDING_NEXT_CUTOFF"] if pending else [])
            if last_visible_state is not None and last_visible_state[3] == "EMPTY":
                warnings.append("FEED_DECLARED_EMPTY")
            if not self._receipts:
                warnings.append("FEED_NOT_CAPTURED")
            return MarketSnapshot(as_of=cutoff, data_cutoff=max((record.available_at for record in (*bars, *visible_receipts)), default=None),
                provider=self.provider_name, bars=bars, receipts=visible_receipts,
                quality="VALID" if bars and all(bar.quality == "VALID" for bar in bars) else "PARTIAL",
                warnings=tuple(warnings))

    def _visible(self, cutoff, since=None):
        result = []
        for bar in self._bars.values():
            if bar.available_at > cutoff or (since is not None and bar.available_at <= since):
                continue
            validate_receipt(bar, self._receipts[bar.source_id], cutoff)
            result.append(bar)
        return tuple(sorted(result, key=lambda bar: (bar.timestamp, bar.instrument_id)))

    async def get_minute_bars(self, start: datetime, end: datetime, *, as_of: datetime) -> tuple[MinuteBar, ...]:
        cutoff, start, end = self._cutoff(as_of), aware(start), aware(end)
        if start > end or end > cutoff:
            raise LiveError("FUTURE_ACCESS_DENIED" if end > cutoff else "INVALID_TIME_RANGE")
        return tuple(bar for bar in self._visible(cutoff) if start <= bar.timestamp <= end)

    async def get_index_bars(self, start: datetime, end: datetime, *, as_of: datetime) -> tuple[MinuteBar, ...]:
        return tuple(bar for bar in await self.get_minute_bars(start, end, as_of=as_of) if bar.asset_type == "INDEX")

    def source_records(self):
        """Captured bytes and local parents for an authorized archive writer."""
        return tuple((self._raw_sources[identity], self._receipts[identity]) for identity in sorted(self._receipts))
