"""TEST_FIXTURE: isolated minute evidence, immutable partitions and PIT reads."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import multiprocessing
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq
import pytest

from stockfish_live.common import LiveError
from stockfish_live.contracts.market import MinuteBar, SourceReceipt
from stockfish_live.storage.duckdb_store import DuckDBStore
from stockfish_live.storage.parquet_store import ParquetStore, _writer_lock

SH = ZoneInfo("Asia/Shanghai")
DAY = "2026-10-09"


def at(hour=9, minute=45):
    return datetime(2026, 10, 9, hour, minute, tzinfo=SH)


def fixture(*, minute=45, delay=0, missing=False, token="TEST_FIXTURE_MINUTE", asset_type="INDEX"):
    raw = token.encode("utf-8")
    source_id = hashlib.sha256(raw).hexdigest()
    event = at(minute=minute)
    available = event + timedelta(minutes=delay)
    receipt = SourceReceipt(source_id=source_id, provider="TEST_FIXTURE", source_timestamp=event,
                            source_timestamp_basis="PUBLISHED", received_at=available,
                            available_at=available, pit_status="TEST_FIXTURE")
    bar = MinuteBar(instrument_id="000001.SH", exchange="SH", symbol="000001", asset_type=asset_type,
                    timestamp=event, open=10, high=11, low=9, close=None if missing else 10.5,
                    volume=None if missing else 1000, turnover=None if missing else 10000,
                    provider=receipt.provider, source_timestamp=event,
                    source_timestamp_basis="PUBLISHED", available_at=available,
                    quality="PARTIAL" if missing else "VALID", source_id=source_id, pit_status="TEST_FIXTURE")
    return bar, receipt, raw


def test_empty_reads_do_not_create_storage_and_coverage_is_unavailable(tmp_path):
    root = tmp_path / "absent"
    store = ParquetStore(root)
    assert store.read_day(DAY) == ()
    assert store.receipts() == {}
    assert store.coverage()["status"] == "UNAVAILABLE"
    with DuckDBStore(root) as query:
        assert query.visible(DAY, cutoff=at(), now=at()) == ()
    assert not root.exists()


def test_nullable_roundtrip_schema_source_and_offset_normalization(tmp_path):
    bar, receipt, raw = fixture(missing=True)
    # Model input accepts an equivalent timezone; persisted fields use Shanghai.
    payload = bar.model_dump(mode="python")
    for key in ("timestamp", "source_timestamp", "available_at"):
        payload[key] = payload[key].astimezone(timezone.utc)
    utc_bar = MinuteBar.model_validate(payload)
    store = ParquetStore(tmp_path)
    path = store.append([utc_bar], receipt, raw)
    assert path.parent.name == "date=" + DAY
    assert path.name.startswith("part-")
    schema = pq.ParquetFile(path).schema_arrow
    assert schema.field("timestamp").type.tz == "Asia/Shanghai"
    assert schema.field("close").nullable
    saved, = store.read_day(DAY)
    assert saved == bar
    assert saved.timestamp.utcoffset() == timedelta(hours=8)
    assert saved.close is None and saved.volume is None and saved.turnover is None
    assert store.receipts() == {receipt.source_id: receipt}
    assert store.coverage() == {"status": "AVAILABLE", "dates": [DAY], "day_count": 1,
                                "row_count": 1, "instrument_count": 1,
                                "pit_statuses": ["TEST_FIXTURE"], "test_fixture_only": True}


def test_duplicate_rejected_without_overwrite_across_partitions_and_asset_types(tmp_path):
    bar, receipt, raw = fixture()
    store = ParquetStore(tmp_path)
    path = store.append([bar], receipt, raw)
    original = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    for batch in ([bar], [bar.model_copy(update={"asset_type": "STOCK"})]):
        with pytest.raises(LiveError, match="DUPLICATE_MINUTE_IDENTITY"):
            store.append(batch, receipt, raw)
    assert {p: p.read_bytes() for p in original} == original
    assert len(list(path.parent.glob("*.parquet"))) == 1
    with pytest.raises(LiveError, match="DUPLICATE_MINUTE_IDENTITY"):
        ParquetStore(tmp_path / "new").append([bar, bar], receipt, raw)


def test_parent_raw_hash_and_receipt_metadata_cannot_be_replaced(tmp_path):
    bar, receipt, raw = fixture()
    store = ParquetStore(tmp_path)
    with pytest.raises(LiveError, match="SOURCE_HASH_MISMATCH"):
        store.append([bar], receipt, b"wrong raw bytes")
    store.append([bar], receipt, raw)
    next_bar = bar.model_copy(update={"timestamp": at(minute=46), "source_timestamp": at(minute=46),
                                      "available_at": at(minute=47)})
    changed_receipt = receipt.model_copy(update={"received_at": at(minute=46), "available_at": at(minute=47)})
    with pytest.raises(LiveError, match="SOURCE_RECEIPT_CONFLICT"):
        store.append([next_bar], changed_receipt, raw)
    assert store.read_day(DAY) == (bar,)


@pytest.mark.parametrize("kind", ["raw", "receipt", "parquet", "manifest"])
def test_source_partition_and_manifest_tampering_fail_closed(tmp_path, kind):
    bar, receipt, raw = fixture()
    store = ParquetStore(tmp_path)
    part = store.append([bar], receipt, raw)
    if kind == "raw":
        path = tmp_path / "sources" / (receipt.source_id + ".raw")
        path.write_bytes(b"tampered")
    elif kind == "receipt":
        path = tmp_path / "sources" / (receipt.source_id + ".receipt.json")
        value = json.loads(path.read_text())
        value["provider"] = "tampered"
        path.write_text(json.dumps(value))
    elif kind == "parquet":
        part.write_bytes(b"tampered")
    else:
        path = part.with_suffix(".manifest.json")
        value = json.loads(path.read_text())
        value["schema_sha256"] = "0" * 64
        path.write_text(json.dumps(value))
    with pytest.raises(LiveError, match="STORAGE_INTEGRITY_ERROR"):
        store.read_day(DAY)


def test_duckdb_filters_future_event_and_late_parent_and_denies_future_cutoff(tmp_path):
    store = ParquetStore(tmp_path)
    for values in (fixture(), fixture(minute=44, delay=2, token="TEST_FIXTURE_LATE_PARENT"),
                   fixture(minute=46, token="TEST_FIXTURE_FUTURE")):
        bar, receipt, raw = values
        store.append([bar], receipt, raw)
    with DuckDBStore(tmp_path) as query:
        result = query.visible(DAY, cutoff=at(), now=at())
        assert [bar.timestamp for bar in result] == [at()]
        with pytest.raises(LiveError, match="FUTURE_ACCESS_DENIED"):
            query.visible(DAY, cutoff=at(minute=46), now=at())
        assert len(query.visible(DAY, cutoff=at(minute=46), now=at(minute=46))) == 3
        assert query.visible(DAY, cutoff=at(), now=at(), instrument_ids=["000001.SH' OR TRUE --"]) == ()
        assert query.visible(DAY, cutoff=at(), now=at(), instrument_ids=[]) == ()
    with pytest.raises(LiveError, match="STORAGE_CLOSED"):
        query.visible(DAY, cutoff=at(), now=at())


def _lock_probe(root, queue):
    try:
        with _writer_lock(Path(root)):
            queue.put("ACQUIRED")
    except LiveError as exc:
        queue.put(exc.code)


def test_single_writer_lock_excludes_another_process(tmp_path):
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    with _writer_lock(tmp_path):
        process = context.Process(target=_lock_probe, args=(str(tmp_path), queue))
        process.start()
        process.join(timeout=10)
        assert process.exitcode == 0
        assert queue.get(timeout=2) == "STORAGE_WRITER_BUSY"
    with _writer_lock(tmp_path):
        pass


def test_multiple_days_or_unknown_parent_rejected(tmp_path):
    bar, receipt, raw = fixture()
    tomorrow = bar.model_copy(update={"timestamp": bar.timestamp + timedelta(days=1),
                                     "source_timestamp": bar.source_timestamp + timedelta(days=1),
                                     "available_at": bar.available_at + timedelta(days=1)})
    store = ParquetStore(tmp_path)
    with pytest.raises(LiveError, match="BATCH_REQUIRES_ONE_BUSINESS_DAY"):
        store.append([bar, tomorrow], receipt, raw)
    with pytest.raises(LiveError, match="SOURCE_RECEIPT_MISMATCH"):
        store.append([bar.model_copy(update={"source_id": "0" * 64})], receipt, raw)


@pytest.mark.parametrize("folder", ["sources", "minute", "minute/date=" + DAY])
def test_symlinked_storage_subdirectory_cannot_escape_root(tmp_path, folder):
    root = tmp_path / "data"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    target = root / folder
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)
    bar, receipt, raw = fixture()
    with pytest.raises(LiveError, match="STORAGE_INTEGRITY_ERROR"):
        ParquetStore(root).append([bar], receipt, raw)
    assert list(outside.iterdir()) == []


def test_unvalidated_model_copy_cannot_write_impossible_bar(tmp_path):
    bar, receipt, raw = fixture()
    with pytest.raises(LiveError, match="INVALID_STORAGE_RECORD"):
        ParquetStore(tmp_path).append([bar.model_copy(update={"low": 999})], receipt, raw)
    assert not (tmp_path / "minute").exists()


def test_interrupted_partition_publication_can_resume_without_overwrite(tmp_path, monkeypatch):
    from stockfish_live.storage import parquet_store
    bar, receipt, raw = fixture()
    store = ParquetStore(tmp_path)
    original_publish = parquet_store._publish
    def interrupt_part(path, content, **kwargs):
        if path.suffix == ".parquet":
            raise OSError("TEST_FIXTURE interrupted publication")
        original_publish(path, content, **kwargs)
    monkeypatch.setattr(parquet_store, "_publish", interrupt_part)
    with pytest.raises(OSError, match="TEST_FIXTURE"):
        store.append([bar], receipt, raw)
    assert store.read_day(DAY) == ()
    manifest, = (tmp_path / "minute" / ("date=" + DAY)).glob("*.manifest.json")
    saved_manifest = manifest.read_bytes()
    monkeypatch.setattr(parquet_store, "_publish", original_publish)
    store.append([bar], receipt, raw)
    assert manifest.read_bytes() == saved_manifest
    assert store.read_day(DAY) == (bar,)
