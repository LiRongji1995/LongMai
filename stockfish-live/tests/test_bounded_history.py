"""TEST_FIXTURE bounded startup preserves complete archive and source proof."""
from datetime import date, timedelta
import shutil
import weakref

import pytest

from stockfish_live.calendar import TradingCalendar
from stockfish_live.common import LiveError, canonical_json
from stockfish_live.contracts.forecast import ForecastConfig
from stockfish_live.contracts.market import DatasetScope
from stockfish_live.providers.demo import create_demo
from stockfish_live.runtime import load_pipeline
from stockfish_live.storage.lineage import validate_history_provenance
from stockfish_live.storage.parquet_store import ParquetStore
from stockfish_live.storage.research_store import ResearchStore
from test_demo import moment
from test_forecasting import state


def test_iter_visible_releases_previous_partition_and_preserves_sorted_api(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path / "TEST_FIXTURE_BOUNDED_PARTS")
    expected = [state(0),state(1)]
    for item in expected:
        store.append("features",[item])
    original = store._part
    previous = []
    class Batch(list):
        pass
    def bounded(kind,path):
        if previous:
            assert previous[-1]() is None, "previous whole partition survived into next read"
        result = Batch(original(kind,path))
        previous.append(weakref.ref(result))
        return result
    monkeypatch.setattr(store,"_part",bounded)
    cutoff = state(4).available_at
    observed = tuple(store.iter_visible("features",cutoff=cutoff,now=cutoff))
    assert set(item.state_id for item in observed) == set(item.state_id for item in expected)
    assert all(reference() is None for reference in previous)
    assert store.visible("features",cutoff=cutoff,now=cutoff) == tuple(expected)


def test_iter_visible_detects_global_duplicates_even_when_all_rows_are_future(tmp_path):
    first = ResearchStore(tmp_path / "TEST_FIXTURE_DUPLICATE_A")
    second = ResearchStore(tmp_path / "TEST_FIXTURE_DUPLICATE_B")
    item = state(0)
    first.append("features",[item])
    other = item.model_copy(update={"current_price":item.current_price+1})
    part = second.append("features",[other])
    for path in (part,part.with_suffix(".manifest.json")):
        shutil.copyfile(path,first.root / "research" / "features" / path.name)
    cutoff = item.decision_at-timedelta(minutes=1)
    with pytest.raises(LiveError,match="STORAGE_INTEGRITY_ERROR"):
        tuple(first.iter_visible("features",cutoff=cutoff,now=item.decision_at))


def test_iter_visible_rechecks_a_later_tampered_part_before_yielding_it(tmp_path):
    store = ResearchStore(tmp_path / "TEST_FIXTURE_LATE_PART_TAMPER")
    for item in (state(0),state(1)):
        store.append("features",[item])
    paths = sorted((store.root / "research" / "features").glob("*.parquet"))
    cutoff = state(4).available_at
    iterator = store.iter_visible("features",cutoff=cutoff,now=cutoff)
    next(iterator)
    paths[1].write_bytes(b"TEST_FIXTURE_CHANGED_AFTER_FIRST_PART")
    with pytest.raises(LiveError,match="STORAGE_INTEGRITY_ERROR"):
        next(iterator)


@pytest.fixture(scope="module")
def complete_archive(tmp_path_factory):
    root = tmp_path_factory.mktemp("TEST_FIXTURE_BOUNDED_HISTORY") / "dataset"
    day = date(2026,9,30)
    report = create_demo(root,day,history_sessions=2)
    calendar = TradingCalendar.from_file(root / "calendar.json")
    scope = DatasetScope.model_validate_json((root / "dataset.json").read_text())
    return root,day,calendar,scope,report


def test_lineage_iterator_bounds_daily_evidence_and_keeps_report_contract(complete_archive):
    root,day,calendar,scope,report = complete_archive
    cutoff = moment(day,9,30)
    store = ResearchStore(root)
    history = store.visible("features",cutoff=cutoff,now=cutoff)
    checked = validate_history_provenance(root,history,
        store.iter_visible("outcomes",cutoff=cutoff,now=cutoff),calendar,scope,as_of=cutoff,yield_outcomes=True)
    count = points = 0
    sources, days = set(),set()
    while True:
        try:
            item = next(checked)
        except StopIteration as completed:
            proof = completed.value
            break
        count += 1
        points += len(item.future_path)
        sources.update(item.source_ids)
        days.add(item.decision_at.date())
        current = checked.gi_frame.f_locals
        assert {bar.timestamp.date() for bar in current["rows"].values()} == {item.decision_at.date()}
        assert len(current["parents"]) <= 240
        assert len(current["targets"]) <= 46
    assert count == report["mature_outcomes"] and points == report["future_path_points"]
    assert proof == {"schema_version":"LIVE_LABEL_PROOF_V1","states":len(history),
        "outcomes":count,"source_receipts":len(sources),"sessions":len(days)}
    empty = validate_history_provenance(root,history,iter(()),calendar,scope,as_of=cutoff)
    assert empty == {"schema_version":"LIVE_LABEL_PROOF_V1","states":len(history),
        "outcomes":0,"source_receipts":0,"sessions":0}


def test_runtime_streams_full_proved_history_into_live_compatible_pipeline(complete_archive,monkeypatch):
    import stockfish_live.runtime as runtime
    root,day,calendar,scope,report = complete_archive
    def config(_root):
        return ForecastConfig(feature_version="MARKET_FEATURES_V2",k=1,min_samples=1,
            feature_names=("return_from_open_pct",))
    monkeypatch.setattr(runtime,"forecast_config",config)
    original = ResearchStore.visible
    def forbid_outcome_pool(store,kind,**kwargs):
        assert kind != "outcomes", "runtime must not materialize full typed label pool"
        return original(store,kind,**kwargs)
    monkeypatch.setattr(ResearchStore,"visible",forbid_outcome_pool)
    pipeline = load_pipeline(root,calendar,scope,day)
    assert pipeline.prepared.state_count == report["historical_states"]
    assert pipeline.prepared.outcome_count == report["mature_outcomes"]
    assert sum(item.path_point_count for item in pipeline.prepared._labels.values()) == report["future_path_points"]
    store = ParquetStore(root)
    bars = store.read_day(day)
    decision = None
    for tick in calendar.ticks(day):
        if tick > moment(day,9,46):
            break
        delta = [bar for bar in bars if bar.timestamp == tick]
        parents = {bar.source_id:store._source(bar.source_id) for bar in delta}
        result = pipeline.process(tick,delta,parents,{"quality":"VALID","warnings":[]})
        if tick == moment(day,9,45):
            decision = result
            frozen = canonical_json(decision)
    assert decision["forecasts"]["000001.SH"]["status"] == "AVAILABLE"
    assert all(h["neighbors"][0]["outcome"]["future_path"] for h in decision["forecasts"]["000001.SH"]["horizons"])
    assert result["forecasts"] == {} and canonical_json(decision) == frozen
    assert decision["forecasts"]["000001.SH"]["pit_status"] == "TEST_FIXTURE"


@pytest.mark.parametrize("corruption,code",[("target","OUTCOME_TARGET_SOURCE_MISMATCH"),
    ("provenance","DATA_PROVENANCE_MIXED")])
def test_runtime_rejects_later_bad_label_before_returning_pipeline(complete_archive,monkeypatch,corruption,code):
    root,day,calendar,scope,_ = complete_archive
    original = ResearchStore.iter_visible
    seen = []
    def corrupt(store,kind,**kwargs):
        for n,item in enumerate(original(store,kind,**kwargs)):
            if kind == "outcomes":
                seen.append(item.state_id)
                if n == 4:
                    if corruption == "provenance":
                        item = item.model_copy(update={"pit_status":"RESEARCH_ONLY"})
                    else:
                        changed = item.target_price+1.
                        path = (*item.future_path[:-1],item.future_path[-1].model_copy(update={"price":changed}))
                        item = item.model_copy(update={"target_price":changed,"future_path":path})
            yield item
    monkeypatch.setattr(ResearchStore,"iter_visible",corrupt)
    with pytest.raises(LiveError,match=code):
        load_pipeline(root,calendar,scope,day)
    assert len(seen) == 5
