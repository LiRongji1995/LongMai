"""Immutable, hash-checked Parquet feature/label/forecast/evaluation batches.

Labels are stored separately from features. Readers apply their own trusted
clock and never interpret a missing label as a zero return.
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ..common import LiveError, aware, canonical_json, digest
from ..contracts.forecast import ForecastResult, HistoricalState, MatureOutcome
from .parquet_store import _checked_file, _json_bytes, _load_json, _publish, _sha, _writer_lock, arrow_rows

FORMAT = "LIVE_RESEARCH_PARQUET_V1"
MODELS = {"features": HistoricalState, "outcomes": MatureOutcome, "forecasts": ForecastResult}
SCHEMA = pa.schema([
    pa.field("record_id", pa.string(), nullable=False),
    pa.field("event_time", pa.timestamp("us", tz="Asia/Shanghai"), nullable=False),
    pa.field("available_at", pa.timestamp("us", tz="Asia/Shanghai"), nullable=False),
    pa.field("instrument_id", pa.string(), nullable=False),
    pa.field("pit_status", pa.string(), nullable=False),
    pa.field("payload", pa.string(), nullable=False),
    pa.field("payload_hash", pa.string(), nullable=False),
])


def _record(kind, raw):
    model = MODELS.get(kind)
    if model is None:
        raise LiveError("INVALID_RESEARCH_KIND")
    item = model.model_validate(raw.model_dump(mode="json") if isinstance(raw, model) else raw)
    identity = item.state_id if kind == "features" else item.forecast_id if kind == "forecasts" else digest({
        "state_id": item.state_id, "horizon": item.horizon, "target_at": item.target_at})
    available = item.as_of if kind == "forecasts" else item.available_at
    payload = canonical_json(item)
    return item, {"record_id": identity, "event_time": item.decision_at, "available_at": available,
        "instrument_id": item.instrument_id, "pit_status": item.pit_status,
        "payload": payload, "payload_hash": _sha(payload.encode())}


class ResearchStore:
    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser().absolute()
        if self.root.is_symlink():
            raise LiveError("STORAGE_INTEGRITY_ERROR")
        self._index_cache = {}

    def _directory(self, kind):
        if kind not in MODELS:
            raise LiveError("INVALID_RESEARCH_KIND")
        directory = self.root / "research" / kind
        if any(p.is_symlink() for p in (self.root, self.root / "research", directory)):
            raise LiveError("STORAGE_INTEGRITY_ERROR")
        return directory

    @staticmethod
    def _signature(path):
        values = []
        for item in (path, path.with_suffix(".manifest.json")):
            if item.is_symlink():
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            stat = item.stat()
            values.append((stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        return tuple(values)

    def _part(self, kind, path):
        result, index = [], []
        signature = self._signature(path)
        try:
            raw = _checked_file(path)
            manifest = _load_json(path.with_suffix(".manifest.json"))
            expected = {"schema_version", "kind", "file", "file_hash", "content_hash", "row_count", "schema_hash"}
            if set(manifest) != expected or manifest["schema_version"] != FORMAT or manifest["kind"] != kind or manifest["file"] != path.name or manifest["file_hash"] != _sha(raw):
                raise LiveError("STORAGE_INTEGRITY_ERROR")
            try:
                table = pq.ParquetFile(BytesIO(raw)).read()
                if table.schema != SCHEMA or manifest["schema_hash"] != _sha(SCHEMA.serialize().to_pybytes()):
                    raise LiveError("STORAGE_INTEGRITY_ERROR")
                rows = arrow_rows(table)
                if len(rows) != manifest["row_count"] or digest(rows) != manifest["content_hash"] or path.name != f"part-{manifest['content_hash']}.parquet":
                    raise LiveError("STORAGE_INTEGRITY_ERROR")
                for row in rows:
                    item, checked = _record(kind, json.loads(row["payload"]))
                    if checked != row:
                        raise LiveError("STORAGE_INTEGRITY_ERROR")
                    index.append((row["record_id"], row["pit_status"]))
                    result.append(item)
            except (ValueError, TypeError, KeyError, pa.ArrowException) as exc:
                raise LiveError("STORAGE_INTEGRITY_ERROR") from exc
            if self._signature(path) != signature:
                raise LiveError("STORAGE_CHANGED_DURING_READ")
            self._index_cache[path] = (signature, tuple(index))
            return result
        except OSError as exc:
            raise LiveError("STORAGE_INTEGRITY_ERROR") from exc

    def _all(self, kind):
        directory = self._directory(kind)
        if not directory.exists():
            return []
        result, seen = [], set()
        for path in sorted(directory.glob("part-*.parquet")):
            result.extend(self._part(kind, path))
            for identity, _ in self._index_cache[path][1]:
                if identity in seen:
                    raise LiveError("STORAGE_INTEGRITY_ERROR")
                seen.add(identity)
        return result

    def _existing_index(self, kind):
        directory = self._directory(kind)
        result = {}
        for path in sorted(directory.glob("part-*.parquet")) if directory.exists() else ():
            signature = self._signature(path)
            if path not in self._index_cache or self._index_cache[path][0] != signature:
                self._part(kind, path)
            for identity, status in self._index_cache[path][1]:
                if identity in result:
                    raise LiveError("STORAGE_INTEGRITY_ERROR")
                result[identity] = status
        return result

    def append(self, kind, records):
        records = tuple(records)
        if not records:
            return None
        pairs = [_record(kind, record) for record in records]
        rows = sorted((row for _, row in pairs), key=lambda row: (row["event_time"], row["record_id"]))
        if len({row["record_id"] for row in rows}) != len(rows):
            raise LiveError("RESEARCH_RECORD_CONFLICT")
        statuses = {row["pit_status"] == "TEST_FIXTURE" for row in rows}
        if len(statuses) > 1:
            raise LiveError("DATA_PROVENANCE_MIXED")
        directory = self._directory(kind)
        with _writer_lock(self.root):
            existing = self._existing_index(kind)
            if existing and any((status == "TEST_FIXTURE") != next(iter(statuses)) for status in existing.values()):
                raise LiveError("DATA_PROVENANCE_MIXED")
            if existing.keys() & {row["record_id"] for row in rows}:
                raise LiveError("RESEARCH_RECORD_CONFLICT")
            directory.mkdir(parents=True, exist_ok=True)
            content_hash = digest(rows)
            target = directory / f"part-{content_hash}.parquet"
            sink = pa.BufferOutputStream()
            pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), sink, compression="zstd")
            content = sink.getvalue().to_pybytes()
            manifest = {"schema_version": FORMAT, "kind": kind, "file": target.name,
                "file_hash": _sha(content), "content_hash": content_hash, "row_count": len(rows),
                "schema_hash": _sha(SCHEMA.serialize().to_pybytes())}
            _publish(target.with_suffix(".manifest.json"), _json_bytes(manifest), accept_identical=True)
            _publish(target, content)
            self._index_cache[target] = (self._signature(target), tuple((row["record_id"], row["pit_status"]) for row in rows))
        return target

    def iter_visible(self, kind, *, cutoff: datetime, now: datetime):
        """Yield fully verified parts without retaining their typed path pool.

        Order is deterministic by partition filename, then stored row order;
        callers needing global event order use ``visible``. Exhaustion is
        required to establish integrity of the complete selected archive.
        Future rows still participate in partition and duplicate verification.
        """
        cutoff, now = aware(cutoff), aware(now)
        if cutoff > now:
            raise LiveError("FUTURE_ACCESS_DENIED")
        directory = self._directory(kind)
        def records():
            seen = set()
            for path in sorted(directory.glob("part-*.parquet")) if directory.exists() else ():
                batch = self._part(kind, path)
                for identity, _ in self._index_cache[path][1]:
                    if identity in seen:
                        raise LiveError("STORAGE_INTEGRITY_ERROR")
                    seen.add(identity)
                for item in batch:
                    if item.decision_at <= cutoff and (item.as_of if kind == "forecasts" else item.available_at) <= cutoff:
                        yield item
                # Release the previous whole part before allocating the next.
                del batch
        return records()

    def visible(self, kind, *, cutoff: datetime, now: datetime):
        return tuple(sorted(self.iter_visible(kind, cutoff=cutoff, now=now),
            key=lambda item: (item.decision_at, item.state_id if kind == "features" else item.forecast_id
                             if kind == "forecasts" else (item.state_id, item.horizon))))

    def save_evaluation(self, report: dict):
        if report.get("market_edge") != "NOT_ESTABLISHED":
            raise LiveError("EVALUATION_EDGE_ASSERTION_DENIED")
        content = _json_bytes(report)
        directory = self.root / "research" / "evaluations"
        if any(p.is_symlink() for p in (self.root, self.root / "research", directory)):
            raise LiveError("STORAGE_INTEGRITY_ERROR")
        with _writer_lock(self.root):
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"evaluation-{_sha(content)}.json"
            _publish(target, content, accept_identical=True)
        return target
