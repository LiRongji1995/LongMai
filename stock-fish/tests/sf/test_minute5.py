from __future__ import annotations

import json

import pandas as pd

from sf import minute5, store

DAY = "2026-11-12"


def _rows(code: str, closes: list[float], highs: list[float] | None = None, lows: list[float] | None = None):
    highs = highs or closes
    lows = lows or closes
    out = []
    for i, t in enumerate(minute5.TIMES[:len(closes)]):
        stamp = DAY.replace("-", "") + t.replace(":", "") + "00000"
        out.append([DAY, stamp, code, str(closes[i]), str(highs[i]), str(lows[i]), str(closes[i]), "1000", "10000"])
    return out


class _RS:
    def __init__(self, rows, code="0"):
        self.rows, self.error_code, self.error_msg = list(rows), code, ""

    def next(self):
        return bool(self.rows)

    def get_row_data(self):
        return self.rows.pop(0)


class FakeBS:
    def __init__(self, data, broken=()):
        self.data, self.broken, self.logins = data, set(broken), 0

    def login(self):
        self.logins += 1

    def logout(self):
        pass

    def query_history_k_data_plus(self, code, fields, start_date, end_date, frequency, adjustflag):
        assert frequency == "5" and start_date == end_date == DAY
        if code in self.broken:
            raise RuntimeError("socket closed")
        return _RS(self.data.get(code, []))


def _daily(home):
    (home / "bars").mkdir(parents=True, exist_ok=True)
    pd.DataFrame([dict(code="sh.600001", preclose=10.0, isST="0"), dict(code="sh.600002", preclose=10.0, isST="0")]) \
        .to_parquet(store.bar_path(DAY))


def test_capture_writes_once_and_logs_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    full = [10.5] * 10 + [11.0] * 38
    bs = FakeBS({"sh.600001": _rows("sh.600001", full)}, broken={"sh.600003"})
    entry = minute5.capture(DAY, ["sh.600001", "sh.600002", "sh.600003"], log=lambda *_: None, bs=bs)
    assert entry["ok"] == 1 and entry["absent"] == ["sh.600002"] and "sh.600003" in entry["failed"]
    assert entry["status"] == "partial" and entry["source"] == "baostock"
    assert minute5.covered(DAY) == {"sh.600001"}
    again = minute5.capture(DAY, ["sh.600001"], log=lambda *_: None, bs=bs)   # already stored: nothing fetched
    assert again["requested"] == 0
    logs = list((tmp_path / "intraday" / "bs5" / "capture" / DAY).glob("*.json"))
    assert len(logs) == 1 and json.loads(logs[0].read_text())["failed"]["sh.600003"].startswith("socket")


def test_features_and_coarse_note(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    _daily(tmp_path)
    closes = [10.5] * 10 + [11.0] * 5 + [10.8] + [11.0] * 32          # seals at bar 10 (10:25), opens once
    bars = minute5.parse(_rows("sh.600001", closes))
    f = minute5.features(DAY, bars)
    r = f.loc["sh.600001"]
    assert r["first_seal"] == "10:25" and r["reopen_bars"] == 1 and r["closed_at_limit"] and r["complete"]
    note = minute5.describe(r)
    assert note.startswith("5分钟线（粗）") and "约10:25前首封·开板约1次" in note
    both = minute5.features(DAY, pd.concat([minute5.parse(_rows("sh.600001", closes)),
                                            minute5.parse(_rows("sh.600002", [10.2] * 48, highs=[10.4] * 48))]))
    assert "nan" not in minute5.describe(both.loc["sh.600002"])        # never sealed, never touched
    assert minute5.describe(both.loc["sh.600002"]).startswith("5分钟线（粗）；收盘较均价")
    yizi = minute5.features(DAY, minute5.parse(_rows("sh.600002", [11.0] * 48)))
    assert minute5.describe(yizi.loc["sh.600002"]) == "5分钟线（粗）；一字封死"


def test_list_notes_falls_back_to_five_minute_bars(tmp_path, monkeypatch):
    from sf import intraday
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    _daily(tmp_path)
    minute5.capture(DAY, ["sh.600001"], log=lambda *_: None,
                    bs=FakeBS({"sh.600001": _rows("sh.600001", [11.0] * 48)}))
    notes, status, _ = intraday.list_notes(DAY, ["sh.600001", "sh.600002"], members=pd.DataFrame())
    assert notes["sh.600001"].startswith("5分钟线（粗）") and notes["sh.600002"] == "分时未采集"
    assert status["coarse"] == 1 and status["missing"] == 1
