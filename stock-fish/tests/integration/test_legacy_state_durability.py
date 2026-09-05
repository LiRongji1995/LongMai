from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import subprocess
import sys

import pytest

from memory.atomic_io import atomic_write_json
from memory.analysis.analysis_store import AnalysisStore
from memory.cache.ttl_cache import TtlCache
from memory.masters.accuracy import verify_predictions
from memory.masters.master_track import MasterTrackDB
from memory.stocks.stock_library import StockLibrary


def test_repeated_windows_atomic_replace_preserves_previous_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "value.json"
    atomic_write_json(path, {"version": 1})
    atomic_write_json(path, {"version": 2})
    assert json.loads(path.read_text())["version"] == 2
    def fail(*args):
        raise OSError("simulated replacement denied")
    monkeypatch.setattr("memory.atomic_io.os.replace", fail)
    with pytest.raises(OSError):
        atomic_write_json(path, {"version": 3})
    assert json.loads(path.read_text())["version"] == 2
    assert list(tmp_path.glob("*.tmp")) == []


def test_cache_repeated_write_collision_clear_and_zero_ttl(tmp_path):
    cache = TtlCache("test", tmp_path)
    cache.set("a:b", 1)
    cache.set("a_b", 2)
    cache.set("a:b", 3)
    reopened = TtlCache("test", tmp_path)
    assert reopened.get("a:b") == 3 and reopened.get("a_b") == 2
    reopened.set("zero", 1, ttl=0)
    assert reopened.get("zero") is None
    corrupt = tmp_path / "unknown.json"
    corrupt.write_text("invalid")
    reopened.clear()
    assert TtlCache("test", tmp_path).get("a:b") is None
    assert corrupt.read_text() == "invalid"


def test_master_concurrent_instances_preserve_all_decisions(tmp_path):
    def record(number):
        MasterTrackDB(tmp_path).record_decision("buffett", "600000", "2024-01-01", {"quote": {"price": 100}},
            {"short_term": {"change_pct": number}, "mid_term": {"change_pct": 0}, "long_term": {"change_pct": 1}})
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(record, range(30)))
    assert len(MasterTrackDB(tmp_path)._load_records("buffett")) == 30
    (tmp_path / "buffett.json").write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError):
        record(31)
    assert (tmp_path / "buffett.json").read_text() == "broken"


def test_legacy_history_cross_process_writer_lock(tmp_path):
    code = '''import sys
from pathlib import Path
from memory.masters.master_track import MasterTrackDB
db=MasterTrackDB(Path(sys.argv[1]))
for number in range(8):
    db.record_decision("buffett", "600000", "2024-01-01", {"quote": {"price": 100}})
'''
    processes = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for _ in range(3)]
    for process in processes:
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr.decode(errors="replace")
    assert len(MasterTrackDB(tmp_path)._load_records("buffett")) == 24


def test_legacy_each_horizon_revisited_and_neutral_not_bearish(tmp_path):
    db = MasterTrackDB(tmp_path)
    db.record_decision("buffett", "600000", "2024-01-01", {"quote": {"price": 100}},
        {"short_term": {"change_pct": 1}, "mid_term": {"change_pct": 0}, "long_term": {"change_pct": -1}})
    original = db._load_records("buffett")[0]
    calls = []
    def load(symbol, day):
        calls.append(day)
        return 101 if day == "2024-01-15" else 99
    verify_predictions(db=db, now=datetime(2024, 1, 16), price_loader=load)
    assert db.get_unverified_records()[0]["was_correct_short"] is True
    verify_predictions(db=db, now=datetime(2025, 1, 2), price_loader=load)
    result = db._load_records("buffett")[0]
    assert result["was_correct_mid"] is False
    assert result["was_correct_long"] is True
    assert calls == ["2024-01-15", "2024-03-31", "2024-12-31"]
    assert db.get_unverified_records() == []
    assert db.get_accuracy("buffett")["mid_term_accuracy"] == 0
    with pytest.raises(ValueError):
        db.update_outcome(original["id"], "short", -1, False)


def test_stock_history_never_substitutes_intraday_or_previous_close(tmp_path):
    library = StockLibrary(tmp_path)
    state = {"quote": {"date": "2024-01-02", "price": 100, "prev_close": 90},
             "financial_summary": {"revenue": 100}}
    library.update("600000", state)
    library.update("600000", state)
    assert library.get_meta("600000")["analysis_count"] == 2
    assert library.get_price_at_date("600000", "2024-01-02") is None
    state["quote"].update(close=101, is_final=True, price_adjustment="raw")
    library.update("600000", state)
    assert library.get_price_at_date("600000", "2024-01-02") == 101
    for path in (tmp_path / "600000" / "financials").glob("*.json"):
        financial = json.loads(path.read_text(encoding="utf-8"))
        assert financial["pit_status"] == "PIT_UNAVAILABLE"
        assert "quarter" not in financial and "report_date" not in financial


def test_full_analysis_archive_survives_restart(tmp_path):
    archive = AnalysisStore(tmp_path)
    state = {"symbol": "600000", "decision": {"full_reason": "reason" * 1000}, "nodes": [1, 2, 3]}
    identity = archive.save(symbol="600000", state=state, timestamp="2024-01-01T15:30:00",
                            employee_reports=[{"content": "full"}], cio_decision={"decision": "HOLD"}, prediction={"p": .5})
    reopened = AnalysisStore(tmp_path)
    assert reopened.get(identity)["state"] == state
    assert reopened.list_all_symbols() == ["600000"]
    assert reopened.list_history("600000")[0]["archive_id"] == identity
