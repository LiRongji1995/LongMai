"""Explicit synthetic fixtures. No real market or investment outcome claims."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from stockfish_core.common import digest
from stockfish_core.data import CanonicalMarketBar, SecurityState
from stockfish_core.experiments import TrainRequest, TrainingSample
from stockfish_core.quant import FeatureRow
from stockfish_core.service import ApplicationService, InputSnapshot
from stockfish_core.storage import SQLiteStore

ASOF = datetime(2026, 7, 1, 7, tzinfo=timezone.utc)
DECISION = ASOF + timedelta(hours=1)
IIDS = ("600000.SH", "600001.SH", "000001.SZ")


def fixture_service(root: Path):
    store = SQLiteStore(root / "state.sqlite3")
    core = ApplicationService(root / "core", store=store, data_dir=root / "qlib-data")
    samples = tuple(TrainingSample(instrument_id=IIDS[0], as_of=datetime(2026, 1, d, 7, tzinfo=timezone.utc),
        feature_available_at=datetime(2026, 1, d, 7, tzinfo=timezone.utc), label_end=datetime(2026, 1, d + 1, 7, tzinfo=timezone.utc),
        values=(float(d),), label=float(d) / 100) for d in range(1, 23))
    def instant(d):
        return datetime(2026, 1, d, 7, tzinfo=timezone.utc)
    request = TrainRequest(model_id="explicit-fixture-ridge", samples=samples, feature_schema=("test_x",),
        training_window=(instant(1), instant(10)), validation_window=(instant(12), instant(15)),
        test_window=(instant(18), instant(21)), evaluation_cutoff=instant(23), label_definition="fixture-next-session-label",
        max_label_horizon=1, data_version=digest([s.model_dump(mode="json") for s in samples]),
        universe_version=digest(IIDS), git_commit="FIXTURE_NOT_REAL_MARKET")
    trained = core.quant.train(request)
    bars = tuple(CanonicalMarketBar(instrument_id=iid, exchange=iid[-2:], symbol=iid[:6], timestamp=ASOF,
        open=10, high=10.2, low=9.8, close=10, volume=100000, turnover=1000000, price_adjustment="RAW",
        provider="explicit-offline-fixture", source_timestamp=ASOF, available_at=ASOF, quality="VERIFIED",
        adjustment_factor=1, snapshot_id="fixture-input") for iid in IIDS)
    securities = tuple(SecurityState(instrument_id=iid, effective_from=date(2026, 7, 1), effective_to=date(2026, 7, 2),
        available_at=ASOF, provider="explicit-offline-fixture", verified=True, listing_date=date(2000, 1, 1),
        board="MAIN", is_st=False, suspended=False, lot_size=100, industry=iid, limits_date=date(2026, 7, 1),
        limit_up=11, limit_down=9, no_price_limit=False) for iid in IIDS)
    sessions = tuple(date(2026, 7, 1) + timedelta(days=d) for d in range(60) if (date(2026, 7, 1) + timedelta(days=d)).weekday() < 5)
    snapshot = InputSnapshot(as_of=ASOF, available_at=ASOF, source="EXPLICIT_TEST_FIXTURE", kind="TEST_FIXTURE",
        bars=bars, securities=securities, sessions=sessions, calendar_start=date(2026, 7, 1), calendar_end=date(2026, 8, 29),
        features=tuple(FeatureRow(instrument_id=iid, available_at=ASOF, values=(3.0 - n,)) for n, iid in enumerate(IIDS)),
        evidence={"purpose": "offline integration, no actual financial performance"})
    snapshot_id = core.register_snapshot(snapshot)
    (core.root / "active_snapshot.txt").write_text(snapshot_id, encoding="utf-8")
    return core, snapshot_id, trained.model
