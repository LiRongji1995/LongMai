"""Independent immutable minute partitions and hash-bound source evidence.

Readers never initialize storage. Publication uses an exclusive staging file and
an atomic no-overwrite hard link; an OS lock admits one writer per data root.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from ..common import LiveError, canonical_json, digest
from ..contracts.market import MinuteBar, SourceReceipt, validate_receipt

SHANGHAI = ZoneInfo("Asia/Shanghai")
FORMAT_VERSION = "LIVE_MINUTE_PARQUET_V1"
SOURCE_VERSION = "LIVE_SOURCE_ARCHIVE_V1"

MINUTE_SCHEMA = pa.schema([
    pa.field("schema_version", pa.string(), nullable=False),
    pa.field("instrument_id", pa.string(), nullable=False),
    pa.field("exchange", pa.string(), nullable=False),
    pa.field("symbol", pa.string(), nullable=False),
    pa.field("asset_type", pa.string(), nullable=False),
    pa.field("timestamp", pa.timestamp("us", tz="Asia/Shanghai"), nullable=False),
    *[pa.field(name, pa.float64()) for name in ("open", "high", "low", "close", "volume", "turnover")],
    pa.field("provider", pa.string(), nullable=False),
    pa.field("source_timestamp", pa.timestamp("us", tz="Asia/Shanghai")),
    pa.field("source_timestamp_basis", pa.string(), nullable=False),
    pa.field("available_at", pa.timestamp("us", tz="Asia/Shanghai"), nullable=False),
    pa.field("quality", pa.string(), nullable=False),
    pa.field("price_adjustment", pa.string(), nullable=False),
    pa.field("source_id", pa.string(), nullable=False),
    pa.field("pit_status", pa.string(), nullable=False),
])


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value) -> bytes:
    encoded = canonical_json(value)
    return encoded.encode("utf-8") if isinstance(encoded, str) else encoded


def _day(value: date | str) -> date:
    if isinstance(value, datetime):
        raise LiveError("INVALID_BUSINESS_DATE")
    try:
        return value if isinstance(value, date) else date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise LiveError("INVALID_BUSINESS_DATE") from exc


def _row(bar: MinuteBar) -> dict:
    value = bar.model_dump(mode="python")
    for key in ("timestamp", "source_timestamp", "available_at"):
        if value[key] is not None:
            value[key] = value[key].astimezone(SHANGHAI)
    return value


def table_for_bars(bars) -> pa.Table:
    return pa.Table.from_pylist([_row(bar) for bar in bars], schema=MINUTE_SCHEMA)


def _content_hash(bars) -> str:
    # A UTC-offset input and its Parquet Shanghai-offset round trip are identical.
    return digest([bar.model_copy(update={key: value for key, value in _row(bar).items()
                                         if key in ("timestamp", "source_timestamp", "available_at")})
                   .model_dump(mode="json") for bar in bars])


def _schema_hash(schema: pa.Schema) -> str:
    return _sha(schema.serialize().to_pybytes())


def _checked_file(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise LiveError("STORAGE_INTEGRITY_ERROR")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise LiveError("STORAGE_INTEGRITY_ERROR") from exc


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(_checked_file(path))
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (ValueError, TypeError, UnicodeError) as exc:
        raise LiveError("STORAGE_INTEGRITY_ERROR") from exc


def _publish(path: Path, content: bytes, *, accept_identical: bool = False) -> None:
    """Atomic publication without replacing any existing file."""
    if path.exists() or path.is_symlink():
        if accept_identical and _checked_file(path) == content:
            return
        raise LiveError("IMMUTABLE_STORAGE_CONFLICT")
    temporary = path.parent / (".staging-" + uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise LiveError("IMMUTABLE_STORAGE_CONFLICT") from exc
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _writer_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".writer.lock"
    if lock_path.is_symlink():
        raise LiveError("STORAGE_INTEGRITY_ERROR")
    handle = lock_path.open("a+b")
    locked = False
    try:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise LiveError("STORAGE_WRITER_BUSY") from exc
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


class ParquetStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _source(self, source_id: str) -> SourceReceipt:
        # source_id comes from validated models or a manifest and must remain a hash.
        if not isinstance(source_id, str) or len(source_id) != 64 or any(char not in "0123456789abcdef" for char in source_id):
            raise LiveError("STORAGE_INTEGRITY_ERROR")
        folder = self.root / "sources"
        if folder.is_symlink():
            raise LiveError("STORAGE_INTEGRITY_ERROR")
        manifest = _load_json(folder / (source_id + ".manifest.json"))
        raw = _checked_file(folder / (source_id + ".raw"))
        receipt_bytes = _checked_file(folder / (source_id + ".receipt.json"))
        if (manifest.get("format_version") != SOURCE_VERSION or manifest.get("source_id") != source_id
                or manifest.get("raw_sha256") != source_id or _sha(raw) != source_id
                or _sha(receipt_bytes) != manifest.get("receipt_sha256")):
            raise LiveError("STORAGE_INTEGRITY_ERROR")
        try:
            receipt = SourceReceipt.model_validate_json(receipt_bytes)
        except ValueError as exc:
            raise LiveError("STORAGE_INTEGRITY_ERROR") from exc
        if receipt.source_id != source_id:
            raise LiveError("STORAGE_INTEGRITY_ERROR")
        return receipt

    def receipts(self) -> dict[str, SourceReceipt]:
        folder = self.root / "sources"
        if not folder.exists():
            return {}
        if folder.is_symlink():
            raise LiveError("STORAGE_INTEGRITY_ERROR")
        return {path.name.removesuffix(".manifest.json"): self._source(path.name.removesuffix(".manifest.json"))
                for path in sorted(folder.glob("*.manifest.json"))}

    def append(self, bars, receipt: SourceReceipt, raw_payload: bytes) -> Path:
        # Pydantic model_copy deliberately skips validators; revalidate at the disk boundary.
        try:
            receipt = SourceReceipt.model_validate(receipt.model_dump(mode="python"))
            bars = tuple(MinuteBar.model_validate(bar.model_dump(mode="python")) for bar in bars)
        except (ValueError, AttributeError) as exc:
            raise LiveError("INVALID_STORAGE_RECORD") from exc
        bars = tuple(sorted(bars, key=lambda bar: (bar.timestamp, bar.instrument_id)))
        if not bars:
            raise LiveError("EMPTY_MINUTE_BATCH")
        if not isinstance(raw_payload, bytes) or _sha(raw_payload) != receipt.source_id:
            raise LiveError("SOURCE_HASH_MISMATCH")
        days = {bar.timestamp.astimezone(SHANGHAI).date() for bar in bars}
        if len(days) != 1:
            raise LiveError("BATCH_REQUIRES_ONE_BUSINESS_DAY")
        day = next(iter(days))
        identities = [(bar.instrument_id, bar.timestamp) for bar in bars]
        if len(identities) != len(set(identities)):
            raise LiveError("DUPLICATE_MINUTE_IDENTITY")
        for bar in bars:
            validate_receipt(bar, receipt)
        with _writer_lock(self.root):
            existing = {(bar.instrument_id, bar.timestamp) for bar in self.read_day(day)}
            if existing.intersection(identities):
                raise LiveError("DUPLICATE_MINUTE_IDENTITY")
            sources = self.root / "sources"
            if sources.is_symlink() or (self.root / "minute").is_symlink():
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            sources.mkdir(exist_ok=True)
            receipt_bytes = _json_bytes(receipt.model_dump(mode="json"))
            receipt_path = sources / (receipt.source_id + ".receipt.json")
            if receipt_path.exists() and _checked_file(receipt_path) != receipt_bytes:
                raise LiveError("SOURCE_RECEIPT_CONFLICT")
            source_manifest = _json_bytes({"format_version": SOURCE_VERSION, "source_id": receipt.source_id,
                                          "raw_sha256": receipt.source_id, "receipt_sha256": _sha(receipt_bytes)})
            if (sources / (receipt.source_id + ".manifest.json")).exists():
                self._source(receipt.source_id)
            for suffix, content in ((".raw", raw_payload), (".receipt.json", receipt_bytes),
                                    (".manifest.json", source_manifest)):
                _publish(sources / (receipt.source_id + suffix), content, accept_identical=True)
            table = table_for_bars(bars)
            sink = pa.BufferOutputStream()
            pq.write_table(table, sink, compression="zstd")
            parquet_bytes = sink.getvalue().to_pybytes()
            batch_hash = _content_hash(bars)
            folder = self.root / "minute" / ("date=" + day.isoformat())
            if folder.is_symlink():
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / ("part-" + batch_hash + ".parquet")
            manifest = {"format_version": FORMAT_VERSION, "business_date": day.isoformat(),
                        "file_sha256": _sha(parquet_bytes), "schema_sha256": _schema_hash(MINUTE_SCHEMA),
                        "content_sha256": batch_hash, "row_count": len(bars), "source_id": receipt.source_id}
            # Readers discover only Parquet files; the last publication commits the batch.
            _publish(path.with_suffix(".manifest.json"), _json_bytes(manifest), accept_identical=True)
            _publish(path, parquet_bytes)
            return path

    def read_day(self, day: date | str) -> tuple[MinuteBar, ...]:
        day = _day(day)
        folder = self.root / "minute" / ("date=" + day.isoformat())
        if not folder.exists():
            return ()
        if folder.is_symlink() or folder.parent.is_symlink():
            raise LiveError("STORAGE_INTEGRITY_ERROR")
        bars = []
        sources = {}
        identities = set()
        for path in sorted(folder.glob("*.parquet")):
            manifest = _load_json(path.with_suffix(".manifest.json"))
            raw = _checked_file(path)
            if (manifest.get("format_version") != FORMAT_VERSION or manifest.get("business_date") != day.isoformat()
                    or manifest.get("file_sha256") != _sha(raw)):
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            try:
                table = pq.ParquetFile(pa.BufferReader(raw)).read()
                if table.schema != MINUTE_SCHEMA or _schema_hash(table.schema) != manifest.get("schema_sha256"):
                    raise LiveError("STORAGE_INTEGRITY_ERROR")
                batch = tuple(MinuteBar.model_validate(row) for row in table.to_pylist())
            except (ValueError, pa.ArrowException) as exc:
                raise LiveError("STORAGE_INTEGRITY_ERROR") from exc
            if (len(batch) != manifest.get("row_count") or _content_hash(batch) != manifest.get("content_sha256")
                    or path.name != "part-" + manifest["content_sha256"] + ".parquet"):
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            source_id = manifest.get("source_id", "")
            if source_id not in sources:
                sources[source_id] = self._source(source_id)
            for bar in batch:
                if bar.timestamp.astimezone(SHANGHAI).date() != day or bar.source_id != source_id:
                    raise LiveError("STORAGE_INTEGRITY_ERROR")
                validate_receipt(bar, sources[source_id])
                identity = (bar.instrument_id, bar.timestamp)
                if identity in identities:
                    raise LiveError("DUPLICATE_MINUTE_IDENTITY")
                identities.add(identity)
                bars.append(bar)
        return tuple(sorted(bars, key=lambda bar: (bar.timestamp, bar.instrument_id)))

    def coverage(self) -> dict:
        minute = self.root / "minute"
        if minute.is_symlink():
            raise LiveError("STORAGE_INTEGRITY_ERROR")
        dates = []
        bars = []
        for folder in sorted(minute.glob("date=*")) if minute.exists() else ():
            try:
                day = _day(folder.name.removeprefix("date="))
            except LiveError:
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            batch = self.read_day(day)
            if batch:
                dates.append(day.isoformat())
                bars.extend(batch)
        return {"status": "AVAILABLE" if bars else "UNAVAILABLE", "dates": dates,
                "day_count": len(dates), "row_count": len(bars),
                "instrument_count": len({bar.instrument_id for bar in bars}),
                "pit_statuses": sorted({bar.pit_status for bar in bars}),
                "test_fixture_only": bool(bars) and all(bar.pit_status == "TEST_FIXTURE" for bar in bars)}
