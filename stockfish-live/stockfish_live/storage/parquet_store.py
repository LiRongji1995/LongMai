"""Independent immutable minute partitions and hash-bound source evidence.

Readers never initialize storage. Publication uses an exclusive staging file and
an atomic no-overwrite hard link; an OS lock admits one writer per data root.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import uuid4
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from ..common import LiveError, canonical_json, digest
from ..contracts.market import MinuteBar, SourceReceipt, validate_receipt

SHANGHAI = ZoneInfo("Asia/Shanghai")
FORMAT_VERSION = "LIVE_MINUTE_PARQUET_V1"
BULK_FORMAT_VERSION = "LIVE_MINUTE_PARQUET_V2"
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


def arrow_rows(table: pa.Table) -> list[dict]:
    """Convert verified microsecond timestamps without repeated timezone imports.

    Arrow's scalar conversion resolves the timezone for every value. Casting
    only the timestamp columns to integer microseconds keeps exact precision
    and uses the already loaded timezone. Schema/hash validation stays with the
    caller and is performed on the original, unchanged table.
    """
    timestamps = {}
    converted = table
    for index, field in enumerate(table.schema):
        if pa.types.is_timestamp(field.type):
            if field.type.unit != "us" or field.type.tz != "Asia/Shanghai":
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            timestamps[field.name] = True
            converted = converted.set_column(index, field.name, table.column(index).cast(pa.int64()))
    rows = converted.to_pylist()
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    for row in rows:
        for name in timestamps:
            if row[name] is not None:
                row[name] = (epoch + timedelta(microseconds=row[name])).astimezone(SHANGHAI)
    return rows


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


def _file_signature(path: Path) -> tuple[int, ...]:
    """A writer cache guard, never a substitute for a reader's SHA checks."""
    try:
        value = path.lstat()
    except OSError as exc:
        raise LiveError("STORAGE_INTEGRITY_ERROR") from exc
    if not stat.S_ISREG(value.st_mode):
        raise LiveError("STORAGE_INTEGRITY_ERROR")
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


@dataclass(frozen=True)
class _WriterIndex:
    identities: frozenset[tuple[str, datetime]]
    source_ids: frozenset[str]
    signatures: dict[Path, tuple[int, ...]]


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
        # Only append duplicate detection uses this cache. read_day always
        # verifies every byte, parent and contract independently of it.
        self._writer_indexes: dict[date, _WriterIndex] = {}

    def _writer_signatures(self, day: date, source_ids) -> dict[Path, tuple[int, ...]]:
        folder = self.root / "minute" / ("date=" + day.isoformat())
        sources = self.root / "sources"
        if any(path.is_symlink() for path in (folder.parent, folder, sources)):
            raise LiveError("STORAGE_INTEGRITY_ERROR")
        paths = []
        for path in sorted(folder.glob("*.parquet")):
            paths.extend((path, path.with_suffix(".manifest.json")))
        for source_id in sorted(source_ids):
            if (not isinstance(source_id, str) or len(source_id) != 64
                    or any(char not in "0123456789abcdef" for char in source_id)):
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            paths.extend(sources / (source_id + suffix)
                         for suffix in (".raw", ".receipt.json", ".manifest.json"))
        return {path: _file_signature(path) for path in paths}

    def _writer_inventory(self, day: date) -> tuple[frozenset[str], dict[Path, tuple[int, ...]]]:
        """Discover all declared parents before performing full verification."""
        folder = self.root / "minute" / ("date=" + day.isoformat())
        if folder.is_symlink() or folder.parent.is_symlink():
            raise LiveError("STORAGE_INTEGRITY_ERROR")
        source_ids = set()
        for path in sorted(folder.glob("*.parquet")):
            manifest = _load_json(path.with_suffix(".manifest.json"))
            if manifest.get("format_version") == FORMAT_VERSION:
                parents = [manifest.get("source_id")]
            elif manifest.get("format_version") == BULK_FORMAT_VERSION:
                parents = manifest.get("source_ids")
            else:
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            if not isinstance(parents, list) or not parents or any(not isinstance(item, str) for item in parents):
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            source_ids.update(parents)
        sources = frozenset(source_ids)
        return sources, self._writer_signatures(day, sources)

    def _remember_writer_index(self, day: date, index: _WriterIndex) -> _WriterIndex:
        self._writer_indexes[day] = index
        # Live writes need today's identities, not an unbounded history cache.
        while len(self._writer_indexes) > 2:
            del self._writer_indexes[next(iter(self._writer_indexes))]
        return index

    def _writer_index(self, day: date) -> _WriterIndex:
        """Called under the writer lock; changed evidence forces full read_day."""
        cached = self._writer_indexes.get(day)
        if cached is not None:
            current = self._writer_signatures(day, cached.source_ids)
            # Immutable committed partitions cannot disappear, even if the
            # remaining archive would otherwise pass a fresh full read.
            if not cached.signatures.keys() <= current.keys():
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            if current == cached.signatures:
                return cached
        source_ids, before = self._writer_inventory(day)
        bars = self.read_day(day)
        after_sources, after = self._writer_inventory(day)
        if (before != after or source_ids != after_sources
                or source_ids != {bar.source_id for bar in bars}):
            raise LiveError("STORAGE_INTEGRITY_ERROR")
        return self._remember_writer_index(day, _WriterIndex(
            frozenset((bar.instrument_id, bar.timestamp) for bar in bars), source_ids, after))

    def _published_writer_index(self, day: date, prior: _WriterIndex, path: Path,
                                raw: bytes, manifest: dict, parents: dict[str, SourceReceipt], identities) -> None:
        """Extend a verified index using only the newly published transaction.

        Old files retain their verified signatures. New bytes and all new
        parents are verified between two complete metadata inventories, so a
        concurrent mutation cannot be blessed as the next cached baseline.
        """
        source_ids = prior.source_ids | parents.keys()
        before = self._writer_signatures(day, source_ids)
        expected_paths = set(prior.signatures) | {path, path.with_suffix(".manifest.json")}
        for source_id in parents:
            expected_paths.update(self.root / "sources" / (source_id + suffix)
                                  for suffix in (".raw", ".receipt.json", ".manifest.json"))
        if (set(before) != expected_paths
                or any(before.get(file) != signature for file, signature in prior.signatures.items())
                or _checked_file(path) != raw
                or _checked_file(path.with_suffix(".manifest.json")) != _json_bytes(manifest)):
            raise LiveError("STORAGE_INTEGRITY_ERROR")
        for source_id, receipt in parents.items():
            if self._source(source_id) != receipt:
                raise LiveError("STORAGE_INTEGRITY_ERROR")
        after = self._writer_signatures(day, source_ids)
        if before != after:
            raise LiveError("STORAGE_INTEGRITY_ERROR")
        self._remember_writer_index(day, _WriterIndex(
            prior.identities | frozenset(identities), frozenset(source_ids), after))

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

    def archive_source(self, receipt: SourceReceipt, raw_payload: bytes) -> None:
        """Persist successful EMPTY/duplicate captures as actual source evidence."""
        receipt = SourceReceipt.model_validate(receipt.model_dump())
        if not isinstance(raw_payload, bytes) or _sha(raw_payload) != receipt.source_id:
            raise LiveError("SOURCE_HASH_MISMATCH")
        with _writer_lock(self.root):
            folder = self.root / "sources"
            if folder.is_symlink():
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            folder.mkdir(exist_ok=True)
            receipt_bytes = _json_bytes(receipt)
            manifest = _json_bytes({"format_version": SOURCE_VERSION, "source_id": receipt.source_id,
                "raw_sha256": receipt.source_id, "receipt_sha256": _sha(receipt_bytes)})
            receipt_path = folder / (receipt.source_id + ".receipt.json")
            if receipt_path.exists() and _checked_file(receipt_path) != receipt_bytes:
                raise LiveError("SOURCE_RECEIPT_CONFLICT")
            if (folder / (receipt.source_id + ".manifest.json")).exists():
                self._source(receipt.source_id)
            for suffix, content in ((".raw", raw_payload), (".receipt.json", receipt_bytes), (".manifest.json", manifest)):
                _publish(folder / (receipt.source_id + suffix), content, accept_identical=True)

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
            index = self._writer_index(day)
            if index.identities.intersection(identities):
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
            self._published_writer_index(day, index, path, parquet_bytes, manifest,
                                         {receipt.source_id: receipt}, identities)
            return path

    def append_many(self, batches) -> Path:
        """Atomically import one day, preserving each batch's actual receipt.

        This is a bounded historical import, not a shortcut around validation.
        Existing evidence is verified under the writer lock; an unchanged,
        previously verified identity index can be reused by this writer.
        """
        rows, parents, payloads = [], {}, {}
        for bars, raw_receipt, raw_payload in batches:
            try:
                receipt = SourceReceipt.model_validate(raw_receipt.model_dump(mode="python"))
                batch = tuple(MinuteBar.model_validate(bar.model_dump(mode="python")) for bar in bars)
            except (ValueError, AttributeError) as exc:
                raise LiveError("INVALID_STORAGE_RECORD") from exc
            if not batch:
                raise LiveError("EMPTY_MINUTE_BATCH")
            if not isinstance(raw_payload, bytes) or _sha(raw_payload) != receipt.source_id:
                raise LiveError("SOURCE_HASH_MISMATCH")
            if receipt.source_id in parents and parents[receipt.source_id] != receipt:
                raise LiveError("SOURCE_RECEIPT_CONFLICT")
            for bar in batch:
                validate_receipt(bar, receipt)
            parents[receipt.source_id], payloads[receipt.source_id] = receipt, raw_payload
            rows.extend(batch)
        bars = tuple(sorted(rows, key=lambda bar: (bar.timestamp, bar.instrument_id)))
        if not bars:
            raise LiveError("EMPTY_MINUTE_BATCH")
        days = {bar.timestamp.date() for bar in bars}
        if len(days) != 1:
            raise LiveError("BATCH_REQUIRES_ONE_BUSINESS_DAY")
        day = next(iter(days))
        identities = {(bar.instrument_id, bar.timestamp) for bar in bars}
        if len(identities) != len(bars):
            raise LiveError("DUPLICATE_MINUTE_IDENTITY")
        with _writer_lock(self.root):
            index = self._writer_index(day)
            if index.identities & identities:
                raise LiveError("DUPLICATE_MINUTE_IDENTITY")
            sources = self.root / "sources"
            folder = self.root / "minute" / ("date=" + day.isoformat())
            if any(path.is_symlink() for path in (sources, folder.parent, folder)):
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            sources.mkdir(exist_ok=True)
            # Check every conflict before publishing any of this transaction.
            for source_id, receipt in parents.items():
                path = sources / (source_id + ".receipt.json")
                if path.exists() and _checked_file(path) != _json_bytes(receipt):
                    raise LiveError("SOURCE_RECEIPT_CONFLICT")
                if (sources / (source_id + ".manifest.json")).exists():
                    self._source(source_id)
            for source_id, receipt in sorted(parents.items()):
                receipt_bytes = _json_bytes(receipt)
                manifest = _json_bytes({"format_version": SOURCE_VERSION, "source_id": source_id,
                    "raw_sha256": source_id, "receipt_sha256": _sha(receipt_bytes)})
                for suffix, content in ((".raw", payloads[source_id]), (".receipt.json", receipt_bytes),
                                        (".manifest.json", manifest)):
                    _publish(sources / (source_id + suffix), content, accept_identical=True)
            sink = pa.BufferOutputStream()
            pq.write_table(table_for_bars(bars), sink, compression="zstd")
            raw = sink.getvalue().to_pybytes()
            batch_hash = _content_hash(bars)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / ("part-" + batch_hash + ".parquet")
            manifest = {"format_version": BULK_FORMAT_VERSION, "business_date": day.isoformat(),
                "file_sha256": _sha(raw), "schema_sha256": _schema_hash(MINUTE_SCHEMA),
                "content_sha256": batch_hash, "row_count": len(bars), "source_ids": sorted(parents)}
            _publish(path.with_suffix(".manifest.json"), _json_bytes(manifest), accept_identical=True)
            _publish(path, raw)
            self._published_writer_index(day, index, path, raw, manifest, parents, identities)
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
            if (manifest.get("format_version") not in (FORMAT_VERSION, BULK_FORMAT_VERSION) or manifest.get("business_date") != day.isoformat()
                    or manifest.get("file_sha256") != _sha(raw)):
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            try:
                table = pq.ParquetFile(pa.BufferReader(raw)).read()
                if table.schema != MINUTE_SCHEMA or _schema_hash(table.schema) != manifest.get("schema_sha256"):
                    raise LiveError("STORAGE_INTEGRITY_ERROR")
                batch = tuple(MinuteBar.model_validate(row) for row in arrow_rows(table))
            except (ValueError, pa.ArrowException) as exc:
                raise LiveError("STORAGE_INTEGRITY_ERROR") from exc
            if (len(batch) != manifest.get("row_count") or _content_hash(batch) != manifest.get("content_sha256")
                    or path.name != "part-" + manifest["content_sha256"] + ".parquet"):
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            source_ids = [manifest.get("source_id", "")] if manifest["format_version"] == FORMAT_VERSION else manifest.get("source_ids")
            if not isinstance(source_ids, list) or not source_ids or len(source_ids) != len(set(source_ids)) or set(source_ids) != {bar.source_id for bar in batch}:
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            for source_id in source_ids:
                if source_id not in sources:
                    sources[source_id] = self._source(source_id)
            for bar in batch:
                if bar.timestamp.astimezone(SHANGHAI).date() != day:
                    raise LiveError("STORAGE_INTEGRITY_ERROR")
                validate_receipt(bar, sources[bar.source_id])
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
