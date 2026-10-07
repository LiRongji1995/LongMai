"""TEST_FIXTURE: writer identity caching retains immutable evidence checks."""
import os

import pytest

from stockfish_live.common import LiveError
from stockfish_live.storage import parquet_store
from stockfish_live.storage.parquet_store import ParquetStore
from test_storage import DAY, fixture


def batch(minute):
    bar, receipt, raw = fixture(minute=minute, token=f"TEST_FIXTURE_WRITER_{minute}")
    return [bar], receipt, raw


def count_reads(store, monkeypatch):
    calls = []
    original = store.read_day

    def read(day):
        calls.append(day)
        return original(day)

    monkeypatch.setattr(store, "read_day", read)
    return calls


def test_own_append_extends_index_without_reading_old_partitions(tmp_path, monkeypatch):
    store = ParquetStore(tmp_path)
    calls = count_reads(store, monkeypatch)
    for minute in range(45, 49):
        store.append(*batch(minute))
    assert len(calls) == 1  # Initial verification of the empty day.
    with pytest.raises(LiveError, match="DUPLICATE_MINUTE_IDENTITY"):
        store.append(*batch(45))
    assert len(calls) == 1
    assert len(store.read_day(DAY)) == 4


def test_new_writer_verifies_existing_day_once_then_reuses_index(tmp_path, monkeypatch):
    ParquetStore(tmp_path).append(*batch(45))
    store = ParquetStore(tmp_path)
    calls = count_reads(store, monkeypatch)
    store.append(*batch(46))
    store.append(*batch(47))
    assert len(calls) == 1
    assert len(store.read_day(DAY)) == 3


def test_valid_external_append_invalidates_index_and_catches_external_duplicate(tmp_path, monkeypatch):
    store = ParquetStore(tmp_path)
    store.append(*batch(45))
    calls = count_reads(store, monkeypatch)
    ParquetStore(tmp_path).append(*batch(46))
    with pytest.raises(LiveError, match="DUPLICATE_MINUTE_IDENTITY"):
        store.append(*batch(46))
    assert len(calls) == 1
    store.append(*batch(47))
    assert len(calls) == 1


def test_changed_metadata_requires_fresh_full_verification(tmp_path, monkeypatch):
    store = ParquetStore(tmp_path)
    path = store.append(*batch(45))
    calls = count_reads(store, monkeypatch)
    value = path.stat()
    os.utime(path, ns=(value.st_atime_ns, value.st_mtime_ns + 1_000_000))
    store.append(*batch(46))
    assert len(calls) == 1
    store.append(*batch(47))
    assert len(calls) == 1


def evidence(tmp_path, part, receipt, kind):
    if kind == "parquet":
        return part
    if kind == "partition_manifest":
        return part.with_suffix(".manifest.json")
    suffix = {"raw": ".raw", "receipt": ".receipt.json", "source_manifest": ".manifest.json"}[kind]
    return tmp_path / "sources" / (receipt.source_id + suffix)


@pytest.mark.parametrize("kind", ["raw", "receipt", "source_manifest", "parquet", "partition_manifest"])
def test_cached_writer_reverifies_all_changed_parent_and_partition_files(tmp_path, monkeypatch, kind):
    store = ParquetStore(tmp_path)
    bars, receipt, raw = batch(45)
    part = store.append(bars, receipt, raw)
    calls = count_reads(store, monkeypatch)
    path = evidence(tmp_path, part, receipt, kind)
    value = path.stat()
    content = path.read_bytes()
    # Preserve size and mtime. ctime still changes and must invalidate the cache.
    path.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    os.utime(path, ns=(value.st_atime_ns, value.st_mtime_ns))
    with pytest.raises(LiveError, match="STORAGE_INTEGRITY_ERROR"):
        store.append(*batch(46))
    # Manifest discovery can reject malformed JSON before read_day is reached.
    assert len(calls) == (0 if kind == "partition_manifest" else 1)
    assert len(list(part.parent.glob("*.parquet"))) == 1


@pytest.mark.parametrize("kind", ["raw", "receipt", "source_manifest", "parquet", "partition_manifest"])
def test_cached_writer_rejects_removed_verified_evidence(tmp_path, kind):
    store = ParquetStore(tmp_path)
    bars, receipt, raw = batch(45)
    part = store.append(bars, receipt, raw)
    evidence(tmp_path, part, receipt, kind).unlink()
    with pytest.raises(LiveError, match="STORAGE_INTEGRITY_ERROR"):
        store.append(*batch(46))


def test_replaced_file_with_same_bytes_is_fully_reverified(tmp_path, monkeypatch):
    store = ParquetStore(tmp_path)
    path = store.append(*batch(45))
    calls = count_reads(store, monkeypatch)
    content = path.read_bytes()
    replacement = path.with_suffix(".replacement")
    replacement.write_bytes(content)
    replacement.replace(path)
    store.append(*batch(46))
    assert len(calls) == 1


def test_read_day_never_uses_writer_cache_for_hash_verification(tmp_path, monkeypatch):
    store = ParquetStore(tmp_path)
    store.append(*batch(45))
    calls = []
    original = parquet_store._sha

    def sha(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(parquet_store, "_sha", sha)
    assert len(store.read_day(DAY)) == 1
    assert len(calls) >= 4  # Parquet, schema, raw source, source receipt.
    calls.clear()
    assert len(store.read_day(DAY)) == 1
    assert len(calls) >= 4


def test_tamper_during_full_verification_is_not_cached(tmp_path, monkeypatch):
    first = ParquetStore(tmp_path)
    part = first.append(*batch(45))
    store = ParquetStore(tmp_path)
    original = store.read_day

    def changed_after_read(day):
        rows = original(day)
        value = part.stat()
        os.utime(part, ns=(value.st_atime_ns, value.st_mtime_ns + 1_000_000))
        return rows

    monkeypatch.setattr(store, "read_day", changed_after_read)
    with pytest.raises(LiveError, match="STORAGE_INTEGRITY_ERROR"):
        store.append(*batch(46))
    assert not store._writer_indexes
    assert len(list(part.parent.glob("*.parquet"))) == 1


@pytest.mark.parametrize("target", ["new_partition", "old_parent"])
def test_tamper_during_own_publication_is_not_blessed_as_cached_evidence(tmp_path, monkeypatch, target):
    store = ParquetStore(tmp_path)
    first = batch(45)
    store.append(*first)
    original = parquet_store._publish

    def tamper(path, content, **kwargs):
        original(path, content, **kwargs)
        if path.suffix == ".parquet":
            victim = path if target == "new_partition" else tmp_path / "sources" / (first[1].source_id + ".raw")
            victim.write_bytes(b"TEST_FIXTURE_TAMPERED")

    monkeypatch.setattr(parquet_store, "_publish", tamper)
    with pytest.raises(LiveError, match="STORAGE_INTEGRITY_ERROR"):
        store.append(*batch(46))
    with pytest.raises(LiveError, match="STORAGE_INTEGRITY_ERROR"):
        store.append(*batch(47))


def test_bulk_append_uses_and_extends_same_verified_index(tmp_path, monkeypatch):
    store = ParquetStore(tmp_path)
    calls = count_reads(store, monkeypatch)
    store.append_many([batch(45), batch(46)])
    store.append_many([batch(47), batch(48)])
    store.append(*batch(49))
    assert len(calls) == 1
    with pytest.raises(LiveError, match="DUPLICATE_MINUTE_IDENTITY"):
        store.append_many([batch(45), batch(50)])
    assert len(calls) == 1
    assert len(store.read_day(DAY)) == 5


def test_new_writer_tracks_every_parent_of_bulk_partition(tmp_path):
    ParquetStore(tmp_path).append_many([batch(45), batch(46)])
    store = ParquetStore(tmp_path)
    store.append(*batch(47))
    receipt = batch(46)[1]
    (tmp_path / "sources" / (receipt.source_id + ".raw")).write_bytes(b"TEST_FIXTURE_BAD")
    with pytest.raises(LiveError, match="STORAGE_INTEGRITY_ERROR"):
        store.append(*batch(48))


def test_mutation_after_new_parent_verification_fails_signature_comparison(tmp_path, monkeypatch):
    store = ParquetStore(tmp_path)
    store.append(*batch(45))
    incoming = batch(46)
    original = store._source

    def changed_after_verify(source_id):
        value = original(source_id)
        if source_id == incoming[1].source_id:
            path = tmp_path / "sources" / (source_id + ".raw")
            current = path.stat()
            os.utime(path, ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000))
        return value

    monkeypatch.setattr(store, "_source", changed_after_verify)
    with pytest.raises(LiveError, match="STORAGE_INTEGRITY_ERROR"):
        store.append(*incoming)
    index, = store._writer_indexes.values()
    assert len(index.identities) == 1


def test_cached_leaf_symlink_is_rejected(tmp_path):
    store = ParquetStore(tmp_path)
    part = store.append(*batch(45))
    copied = part.with_suffix(".copy")
    copied.write_bytes(part.read_bytes())
    part.unlink()
    part.symlink_to(copied)
    with pytest.raises(LiveError, match="STORAGE_INTEGRITY_ERROR"):
        store.append(*batch(46))


def test_unrelated_empty_capture_archive_does_not_invalidate_day_index(tmp_path, monkeypatch):
    store = ParquetStore(tmp_path)
    store.append(*batch(45))
    calls = count_reads(store, monkeypatch)
    _, receipt, raw = batch(50)
    store.archive_source(receipt, raw)
    store.append(*batch(46))
    assert calls == []
