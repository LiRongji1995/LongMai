"""Isolated small demo processing, never a real-history validation."""
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from stockfish_live.calendar import TradingCalendar
from stockfish_live.common import LiveError, digest
from stockfish_live.features.market import MarketStateEngine
from stockfish_live.features.streaming import StreamingMarketStateEngine
from stockfish_live.forecast.engine import state_from_market_state
from stockfish_live.contracts.market import DatasetScope
from stockfish_live.providers.demo import create_demo
from stockfish_live.providers.mock import INDICES, STOCKS
from stockfish_live.storage.context_store import ContextStore
from stockfish_live.storage.parquet_store import ParquetStore
from stockfish_live.storage.research_store import ResearchStore

TZ = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 9, 30)


def moment(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("TEST_FIXTURE_DEMO") / "dataset"
    report = create_demo(root, DAY, history_sessions=2)
    return root, report


def test_demo_processes_history_and_keeps_target_results_unmatured(demo):
    root, report = demo
    assert report["label"] == "TEST_FIXTURE"
    assert report["scope"] == "DECLARED_SCOPE_ONLY"
    assert report["rows"] == 3 * 240 * 8
    assert report["historical_states"] == 2 * 46 * 5
    assert report["target_day_historical_labels"] == 0
    assert report["investment_evaluation"] == "NOT_EVALUATED"
    assert report["market_edge"] == "NOT_ESTABLISHED"
    assert report["calendar_status"] == "SYNTHETIC_EXPLICIT_DATES_NOT_OFFICIAL"
    assert report["future_path_points"] > report["mature_outcomes"]
    assert not (root / "FAILED_DEMO").exists()
    cutoff = moment(DAY, 9, 30)
    store = ResearchStore(root)
    states = store.visible("features", cutoff=cutoff, now=cutoff)
    outcomes = store.visible("outcomes", cutoff=cutoff, now=cutoff)
    assert len(states) == report["historical_states"]
    assert len(outcomes) == report["mature_outcomes"]
    assert all(item.decision_at.date() < DAY and item.pit_status == "TEST_FIXTURE" for item in (*states, *outcomes))
    assert all(item.available_at >= item.target_at for item in outcomes)
    config = json.loads((root / "research-config.json").read_text())
    assert config["forecast"]["k_candidates"] == [50, 100, 200]
    assert config["forecast"]["parameters_status"] == "UNVALIDATED"


def test_demo_bars_have_per_minute_parent_visibility_and_explicit_identity(demo):
    root, _ = demo
    bars = ParquetStore(root).read_day(DAY)
    assert len(bars) == 240 * 8
    assert {bar.instrument_id for bar in bars} == set(INDICES + STOCKS)
    assert {bar.asset_type for bar in bars} == {"INDEX", "STOCK"}
    assert len({bar.source_id for bar in bars}) == 240
    store = ParquetStore(root)
    for bar in bars:
        parent = store._source(bar.source_id)
        assert parent.available_at == parent.received_at == bar.available_at == bar.timestamp
        assert parent.pit_status == bar.pit_status == "TEST_FIXTURE"
        assert bar.provider == "mock"
        assert bar.source_timestamp_basis == "PUBLISHED"


def test_demo_preopen_context_visibility_and_declared_coverage(demo):
    root, _ = demo
    bundle = ContextStore(root).load()
    assert bundle.turnover_history == ()
    before = bundle.visible(bundle.reference_prices, moment(DAY, 8, 59))
    assert all(item.trading_date != DAY for item in before)
    visible = bundle.visible(bundle.reference_prices, moment(DAY, 9))
    today = [record for record in visible if record.trading_date == DAY]
    assert {record.instrument_id for record in today} == set(INDICES + STOCKS)
    coverage = bundle.visible(bundle.universe_coverage, moment(DAY, 9))
    assert len(coverage) == 1
    assert coverage[0].coverage == "DECLARED_SCOPE_ONLY"
    assert set(coverage[0].instrument_ids) == set(STOCKS)
    assert all(item.pit_status == "TEST_FIXTURE" for item in bundle.records())
    assert len(bundle.visible(bundle.security_statuses, moment(DAY, 9))) == 3
    assert len(bundle.visible(bundle.sector_memberships, moment(DAY, 9))) == 3
    assert all(item.based_on_date < DAY for item in bundle.visible(bundle.daily_context, moment(DAY, 9)))


def test_demo_saved_feature_matches_real_prefix_rebuild(demo):
    root, _ = demo
    calendar = TradingCalendar.from_file(root / "calendar.json")
    scope = DatasetScope.model_validate_json((root / "dataset.json").read_text())
    first = calendar.sessions[0]
    tick = moment(first, 9, 45)
    store = ParquetStore(root)
    prefix = [bar for bar in store.read_day(first) if bar.timestamp <= tick]
    parents = {bar.source_id: store._source(bar.source_id) for bar in prefix}
    context = ContextStore(root).load()
    engine = StreamingMarketStateEngine(calendar, scope, context=context)
    for current in calendar.ticks(first):
        if current > tick:
            break
        delta = [bar for bar in prefix if bar.timestamp == current]
        rebuilt = engine.build(current, delta, {bar.source_id: parents[bar.source_id] for bar in delta})
    expected = state_from_market_state(rebuilt, INDICES[0], tick)
    states = ResearchStore(root).visible("features", cutoff=tick, now=tick)
    actual = next(item for item in states if item.instrument_id == INDICES[0] and item.decision_at == tick)
    assert actual == expected
    reference = MarketStateEngine(calendar, scope, context=context).build(tick, prefix, parents)
    assert actual.features == pytest.approx(state_from_market_state(reference, INDICES[0], tick).features)
    index_bars = sorted((bar for bar in prefix if bar.instrument_id == INDICES[0]), key=lambda bar: bar.timestamp)
    assert actual.features["return_from_open_pct"] == pytest.approx((index_bars[-1].close / index_bars[0].open - 1) * 100)
    assert actual.features["turnover_ratio"] is None


def test_demo_refuses_existing_destination_and_invalid_count(demo, tmp_path):
    root, _ = demo
    before = digest(json.loads((root / "demo-report.json").read_text()))
    with pytest.raises(LiveError, match="FIXTURE_DESTINATION_EXISTS"):
        create_demo(root, DAY, history_sessions=2)
    assert digest(json.loads((root / "demo-report.json").read_text())) == before
    invalid = tmp_path / "TEST_FIXTURE_INVALID"
    for count in (0, -1, True, 1.5):
        with pytest.raises(LiveError, match="DEMO_HISTORY_SESSIONS_INVALID"):
            create_demo(invalid, DAY, history_sessions=count)
    assert not invalid.exists()


def test_demo_fixed_seed_determinism_for_minute_and_history(demo, tmp_path):
    root, report = demo
    other = tmp_path / "TEST_FIXTURE_REPEATED_DEMO"
    other_report = create_demo(other, DAY, history_sessions=2)
    assert other_report["historical_states"] == report["historical_states"]
    for day in (DAY - timedelta(days=2), DAY - timedelta(days=1), DAY):
        assert ParquetStore(root).read_day(day) == ParquetStore(other).read_day(day)
    cutoff = moment(DAY, 9, 30)
    for kind in ("features", "outcomes"):
        assert ResearchStore(root).visible(kind, cutoff=cutoff, now=cutoff) == ResearchStore(other).visible(kind, cutoff=cutoff, now=cutoff)


def test_demo_rejects_nondate_before_creating_destination(tmp_path):
    root = tmp_path / "TEST_FIXTURE_INVALID_DATE"
    for invalid in ("2026-09-30", moment(DAY, 9), None):
        with pytest.raises(LiveError, match="DEMO_DATE_INVALID"):
            create_demo(root, invalid)
    assert not root.exists()


def test_demo_cancellation_keeps_explicit_incomplete_marker(tmp_path, monkeypatch):
    root = tmp_path / "TEST_FIXTURE_CANCELLED_DEMO"
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt("TEST_FIXTURE cancellation")
    monkeypatch.setattr(ParquetStore, "append_many", interrupt)
    with pytest.raises(KeyboardInterrupt):
        create_demo(root, DAY, history_sessions=1)
    assert (root / "FAILED_DEMO").read_text() == "TEST_FIXTURE_DEMO_INCOMPLETE\n"
    assert not (root / "demo-report.json").exists()
