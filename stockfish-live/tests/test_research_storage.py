"""TEST_FIXTURE: new storage boundaries, lineage and immutable publication."""
import hashlib
import json

import pytest

from stockfish_live.common import LiveError, canonical_json
from stockfish_live.contracts.context import ContextBundle, ReferencePrice
from stockfish_live.contracts.market import SourceReceipt
from stockfish_live.forecast.engine import ForecastEngine
from stockfish_live.storage.context_store import ContextStore
from stockfish_live.storage.parquet_store import ParquetStore
from stockfish_live.storage.research_store import ResearchStore
from test_forecasting import calendar, labels, small_config, state
from test_storage import fixture, at, DAY


def test_feature_outcome_forecast_roundtrip_and_separate_availability(tmp_path, calendar):
    history = [state(i) for i in range(3)]
    outcomes = [item for s in history for item in labels(calendar, s)]
    query = state(4)
    forecast = ForecastEngine(calendar, small_config()).predict(query, history, outcomes, as_of=query.available_at)
    store = ResearchStore(tmp_path)
    for kind, rows in (("features", history + [query]), ("outcomes", outcomes), ("forecasts", [forecast])):
        store.append(kind, rows)
    assert store.visible("features", cutoff=history[0].decision_at, now=query.decision_at) == (history[0],)
    assert store.visible("outcomes", cutoff=history[0].decision_at, now=query.decision_at) == ()
    assert store.visible("forecasts", cutoff=query.available_at, now=query.available_at) == (forecast,)
    assert len(store.visible("outcomes", cutoff=query.available_at, now=query.available_at)) == len(outcomes)
    with pytest.raises(LiveError, match="FUTURE_ACCESS_DENIED"):
        store.visible("features", cutoff=query.decision_at, now=history[0].decision_at)
    with pytest.raises(LiveError, match="RESEARCH_RECORD_CONFLICT"):
        store.append("forecasts", [forecast])


@pytest.mark.parametrize("what", ["parquet", "manifest", "missing_manifest"])
def test_store_reads_and_cached_writer_detect_changed_evidence(tmp_path, what):
    store = ResearchStore(tmp_path)
    path = store.append("features", [state(0)])
    if what == "parquet":
        path.write_bytes(b"TEST_FIXTURE tamper")
    elif what == "manifest":
        manifest = path.with_suffix(".manifest.json")
        value = json.loads(manifest.read_text())
        value["row_count"] = 100
        manifest.write_text(canonical_json(value))
    else:
        path.with_suffix(".manifest.json").unlink()
    for action in (lambda: store.visible("features", cutoff=state(4).available_at, now=state(4).available_at),
                   lambda: store.append("features", [state(1)])):
        with pytest.raises((LiveError, OSError)):
            action()


def test_no_fixture_and_real_mix_and_invalid_kind_or_edge(tmp_path):
    store = ResearchStore(tmp_path)
    store.append("features", [state(0)])
    real = state(1, pit_status="RESEARCH_ONLY")
    with pytest.raises(LiveError, match="DATA_PROVENANCE_MIXED"):
        store.append("features", [real])
    with pytest.raises(LiveError, match="INVALID_RESEARCH_KIND"):
        store.append("../../secrets", [state(0)])
    with pytest.raises(LiveError, match="EVALUATION_EDGE_ASSERTION_DENIED"):
        store.save_evaluation({"market_edge": "ESTABLISHED"})
    target = store.save_evaluation({"market_edge": "NOT_ESTABLISHED", "fixture": True})
    assert store.save_evaluation({"market_edge": "NOT_ESTABLISHED", "fixture": True}) == target


def test_context_archive_raw_hash_receipt_and_bundle_tamper(tmp_path):
    raw = b"TEST_FIXTURE PREOPEN reference"
    source_id = hashlib.sha256(raw).hexdigest()
    known = at(hour=9, minute=0)
    parent = SourceReceipt(source_id=source_id, provider="mock", source_timestamp=known,
        source_timestamp_basis="PUBLISHED", received_at=known, available_at=known, pit_status="TEST_FIXTURE")
    reference = ReferencePrice(instrument_id="000001.SH", trading_date=known.date(), previous_close=100,
        source_id=source_id, provider="mock", source_timestamp=known, source_timestamp_basis="PUBLISHED",
        available_at=known, pit_status="TEST_FIXTURE", effective_from=known)
    bundle = ContextBundle(receipts=(parent,), reference_prices=(reference,))
    store = ContextStore(tmp_path)
    with pytest.raises(LiveError, match="SOURCE_HASH_MISMATCH"):
        store.save(bundle, {source_id: b"wrong"})
    path = store.save(bundle, {source_id: raw})
    assert store.load() == bundle
    value = json.loads(path.read_text())
    value["reference_prices"][0]["previous_close"] = 999
    path.write_text(canonical_json(value))
    with pytest.raises(LiveError, match="STORAGE_INTEGRITY_ERROR"):
        store.load()


def test_bulk_import_preserves_each_minute_parent_and_single_partition(tmp_path):
    first = fixture(minute=44, token="TEST_FIXTURE_44")
    second = fixture(minute=45, delay=1, token="TEST_FIXTURE_45_LATE")
    store = ParquetStore(tmp_path)
    path = store.append_many(([bar], parent, raw) for bar, parent, raw in (first, second))
    assert json.loads(path.with_suffix(".manifest.json").read_text())["format_version"] == "LIVE_MINUTE_PARQUET_V2"
    assert store.read_day(DAY) == (first[0], second[0])
    assert store.receipts() == {first[1].source_id: first[1], second[1].source_id: second[1]}
    assert len(list(path.parent.glob("*.parquet"))) == 1
    with pytest.raises(LiveError, match="DUPLICATE_MINUTE_IDENTITY"):
        store.append_many([([first[0]], first[1], first[2])])


def test_bulk_bad_later_batch_does_not_publish_earlier_batch(tmp_path):
    first = fixture(minute=44, token="TEST_FIXTURE_44")
    second = fixture(minute=45, token="TEST_FIXTURE_45")
    with pytest.raises(LiveError, match="SOURCE_HASH_MISMATCH"):
        ParquetStore(tmp_path).append_many([([first[0]], first[1], first[2]), ([second[0]], second[1], b"wrong")])
    assert list(tmp_path.iterdir()) == []


def test_bulk_and_single_legacy_partitions_coexist(tmp_path):
    store = ParquetStore(tmp_path)
    a, b, c = (fixture(minute=m, token=f"TEST_FIXTURE_{m}") for m in (43, 44, 45))
    store.append([a[0]], a[1], a[2])
    store.append_many([([b[0]], b[1], b[2]), ([c[0]], c[1], c[2])])
    assert store.read_day(DAY) == (a[0], b[0], c[0])
