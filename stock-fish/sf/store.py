"""Lean columnar session store.

One parquet file per trading session holding the full BaoStock daily snapshot
(unadjusted OHLC, exchange preclose, pctChg, turnover, ST and trade status).
Sessions are immutable once written, so a snapshot is checked for completeness first (`check_complete`);
a new trading day appends one file.

Point-in-time rule: a session file is only written after the session's close
data is published (BaoStock publishes after ~17:30 Shanghai time), and the
fetch timestamp is stored alongside the rows.
"""
from __future__ import annotations

import glob
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

SH = timezone(timedelta(hours=8))
CALENDAR_LOOKBACK_DAYS = 1700  # ~4.5 years, enough for multi-year backtests
NUMERIC = ("open", "high", "low", "close", "preclose", "volume", "amount", "turn", "pctChg")


def home() -> Path:
    """Data directory: $SF_HOME, else `stockfish-data/` next to the repository checkout."""
    root = os.environ.get("SF_HOME")
    return Path(root).expanduser() if root else Path(__file__).resolve().parents[2].parent / "stockfish-data"


def _paths():
    root = home()
    (root / "bars").mkdir(parents=True, exist_ok=True)
    (root / "meta").mkdir(parents=True, exist_ok=True)
    (root / "pools").mkdir(parents=True, exist_ok=True)
    return root


def now_sh() -> datetime:
    return datetime.now(SH)


# ---------------------------------------------------------------- BaoStock

class BaoStock:
    """One login per process; the legacy design logged in per request."""

    def __init__(self):
        import baostock as bs
        self.bs = bs
        result = bs.login()
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock login failed: {result.error_msg}")

    def close(self):
        try:
            self.bs.logout()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @staticmethod
    def _rows(result):
        if result.error_code != "0":
            raise RuntimeError(f"BaoStock error {result.error_code}: {result.error_msg}")
        rows = []
        while result.next():
            rows.append(result.get_row_data())
        return pd.DataFrame(rows, columns=result.fields)

    def calendar(self, start: str, end: str) -> list[str]:
        frame = self._rows(self.bs.query_trade_dates(start_date=start, end_date=end))
        return frame.loc[frame["is_trading_day"] == "1", "calendar_date"].tolist()

    def market_daily(self, day: str) -> pd.DataFrame:
        try:
            return self._rows(self.bs.query_daily_history_k_AStock(date=day))
        except RuntimeError as error:
            if "10001001" not in str(error):  # not a "session kicked" error
                raise
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                self.bs.login()
            return self._rows(self.bs.query_daily_history_k_AStock(date=day))

    def index_daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        return self._rows(self.bs.query_history_k_data_plus(
            code, "date,code,close,preclose,pctChg", start_date=start, end_date=end, frequency="d"))

    def stock_basic(self) -> pd.DataFrame:
        return self._rows(self.bs.query_stock_basic(code=""))


def _retry(fn, *args, attempts=3, pause=3.0):
    last = None
    for attempt in range(attempts):
        try:
            return fn(*args)
        except Exception as error:  # network hiccups are routine; retry is explicit and bounded
            last = error
            time.sleep(pause * (attempt + 1))
    raise last


# ---------------------------------------------------------------- store API

def calendar(refresh: bool = False, bao: BaoStock | None = None) -> list[str]:
    root = _paths()
    path = root / "meta" / "calendar.json"
    today = now_sh().date()
    start = str(today - timedelta(days=CALENDAR_LOOKBACK_DAYS))
    if path.exists() and not refresh:
        cached = json.loads(path.read_text())
        if cached.get("end", "") >= str(today + timedelta(days=7)) and cached.get("start", "9999") <= start:
            return cached["sessions"]
    owned = bao is None
    bao = bao or BaoStock()
    try:
        sessions = _retry(bao.calendar, start, str(today + timedelta(days=40)))
    finally:
        if owned:
            bao.close()
    path.write_text(json.dumps({"start": start, "end": str(today + timedelta(days=40)), "sessions": sessions}))
    return sessions


def latest_complete_session(sessions: list[str], ready_hour: int = 18) -> str:
    """Latest session whose daily data is expected to be published."""
    now = now_sh()
    today = str(now.date())
    past = [s for s in sessions if s < today]
    if today in sessions and now.hour >= ready_hour:
        return today
    return past[-1]


def next_session(sessions: list[str], day: str) -> str:
    return next(s for s in sessions if s > day)


def bar_path(day: str) -> Path:
    return _paths() / "bars" / f"{day}.parquet"


def have(day: str) -> bool:
    return bar_path(day).exists()


def _normalize(frame: pd.DataFrame, day: str, fetched_at: str, origin: str) -> pd.DataFrame:
    frame = frame.copy()
    for column in NUMERIC:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column].replace("", None), errors="coerce")
    frame["date"] = day
    frame["isST"] = frame["isST"].astype(str)
    frame["tradestatus"] = frame["tradestatus"].astype(str)
    frame["fetched_at"] = fetched_at
    frame["origin"] = origin
    keep = ["date", "code", *NUMERIC, "tradestatus", "isST", "fetched_at", "origin"]
    return frame[[c for c in keep if c in frame]]


MIN_ROWS_RATIO = 0.97      # a snapshot with fewer rows than this x its nearest stored neighbour is partial
MAX_MISSING_CLOSE = 0.01   # share of traded rows allowed without a close


def check_complete(day: str, frame: pd.DataFrame) -> None:
    """Raise when a snapshot looks partial. Sessions are immutable once written, so a partial snapshot
    (fetched before BaoStock finished publishing) must never be stored."""
    import pyarrow.parquet as pq
    if frame.empty:
        raise ValueError(f"empty market snapshot for {day}")
    stored = sorted(p.stem for p in (_paths() / "bars").glob("*.parquet") if p.stem != day)
    earlier, later = [s for s in stored if s < day], [s for s in stored if s > day]
    neighbour = earlier[-1] if earlier else later[0] if later else None
    if neighbour:
        ref = pq.read_metadata(bar_path(neighbour)).num_rows
        if len(frame) < MIN_ROWS_RATIO * ref:
            raise ValueError(f"partial market snapshot for {day}: {len(frame)} rows vs {ref} on {neighbour}")
    traded = frame["tradestatus"].astype(str).eq("1")
    close = pd.to_numeric(frame["close"].replace("", None), errors="coerce")
    if traded.any() and close[traded].isna().mean() > MAX_MISSING_CLOSE:
        raise ValueError(f"partial market snapshot for {day}: {close[traded].isna().mean():.1%} of traded rows have no close")


def write_session(day: str, frame: pd.DataFrame, fetched_at: str, origin: str) -> Path:
    check_complete(day, frame)
    path = bar_path(day)
    tmp = path.with_suffix(".tmp")
    _normalize(frame, day, fetched_at, origin).to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return path


def import_legacy_cache(cache_root: Path) -> list[str]:
    """Reuse complete market_daily snapshots already captured by the legacy pipeline."""
    imported = []
    for claim in glob.glob(str(cache_root / "raw" / "*" / "raw" / "*" / "request-claim.json")):
        spec = json.loads(Path(claim).read_text()).get("request_spec", {})
        if spec.get("endpoint") != "market_daily":
            continue
        day = spec["parameters"]["date"]
        if have(day):
            continue
        for raw_file in glob.glob(str(Path(claim).parent / "raw" / "*.raw.json")):
            payload = json.loads(Path(raw_file).read_text())
            if payload.get("status") == "COMPLETE" and payload.get("rows"):
                write_session(day, pd.DataFrame(payload["rows"]),
                              payload.get("first_seen_at") or "", "LEGACY_CACHE_IMPORT")
                imported.append(day)
                break
    return sorted(imported)


def _fetch_days(days: list[str]) -> list[tuple[str, str | None, float]]:
    """Worker: one BaoStock login, fetch and store each day. Returns (day, error, seconds)."""
    import contextlib
    import io
    results = []
    with contextlib.redirect_stdout(io.StringIO()):
        bao = BaoStock()
    try:
        for day in days:
            started = time.monotonic()
            try:
                frame = _retry(bao.market_daily, day)
                write_session(day, frame, datetime.now(timezone.utc).isoformat(), "BAOSTOCK_MARKET_DAILY")
                results.append((day, None, time.monotonic() - started))
            except Exception as error:
                results.append((day, str(error), time.monotonic() - started))
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            bao.close()
    return results


def update(sessions_needed: int = 30, observation: str | None = None, workers: int = 1, log=print) -> dict:
    """Ensure the last N sessions up to the observation date are stored.

    Failed days are reported, never silently skipped; rerunning retries them.
    """
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        bao = BaoStock()
    try:
        sessions = calendar(bao=bao)
        observation = observation or latest_complete_session(sessions)
        window = [s for s in sessions if s <= observation][-sessions_needed:]
        missing = [s for s in window if not have(s)]
        basic_path = _paths() / "meta" / "stock_basic.parquet"
        if not basic_path.exists() or missing:
            _retry(bao.stock_basic).to_parquet(basic_path, index=False)
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            bao.close()
    fetched, failed = [], []
    if missing:
        workers = max(1, min(workers, len(missing)))
        chunks = [missing[i::workers] for i in range(workers)]
        if workers == 1:
            batches = [_fetch_days(chunks[0])]
        else:
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(workers) as pool:
                batches = list(pool.map(_fetch_days, chunks))
        for day, error, seconds in sorted(r for batch in batches for r in batch):
            if error:
                failed.append((day, error))
                log(f"  FAILED {day}: {error}")
            else:
                fetched.append(day)
                log(f"  fetched {day} in {seconds:.1f}s")
    return dict(observation=observation, window=window, fetched=fetched, failed=failed)


def load_window(sessions: list[str]) -> pd.DataFrame:
    missing = [s for s in sessions if not have(s)]
    if missing:
        raise FileNotFoundError(f"sessions not in store: {missing}; run `sf update` first")
    return pd.concat([pd.read_parquet(bar_path(s)) for s in sessions], ignore_index=True)


def file_id(path: Path) -> str | None:
    """First 16 hex digits of the file's sha256, or None when the file does not exist."""
    import hashlib
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def archive(path: Path) -> str | None:
    """Content-addressed copy of a mutable input under meta/archive/<stem>-<id><suffix> (kept once per
    content), so outputs that name the id can be re-derived after the file is overwritten."""
    import shutil
    digest = file_id(path)
    if digest is None:
        return None
    dest = _paths() / "meta" / "archive" / f"{path.stem}-{digest}{path.suffix}"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        shutil.copy2(path, tmp)
        tmp.replace(dest)
    return digest


def stock_basic() -> pd.DataFrame:
    path = _paths() / "meta" / "stock_basic.parquet"
    if not path.exists():
        with BaoStock() as bao:
            _retry(bao.stock_basic).to_parquet(path, index=False)
    return pd.read_parquet(path)


def wide(bars: pd.DataFrame, numeric: tuple[str, ...], flags: dict[str, str] | None = None) -> dict[str, pd.DataFrame]:
    """Session x code frames from long bars in one pass (numpy scatter instead of repeated pivots).
    numeric: float columns; flags: {name: column} turned into `column == "1"` booleans.
    Missing (date, code) cells are NaN / False, exactly like DataFrame.pivot."""
    di, dates = pd.factorize(bars["date"], sort=True)
    ci, codes = pd.factorize(bars["code"], sort=True)
    index, columns = pd.Index(dates, name="date"), pd.Index(codes, name="code")
    out = {}
    for col in numeric:
        arr = np.full((len(dates), len(codes)), np.nan)
        arr[di, ci] = pd.to_numeric(bars[col], errors="coerce").to_numpy(dtype=float)
        out[col] = pd.DataFrame(arr, index=index, columns=columns)
    for name, col in (flags or {}).items():
        arr = np.zeros((len(dates), len(codes)), dtype=bool)
        arr[di, ci] = (bars[col].astype(str) == "1").to_numpy()
        out[name] = pd.DataFrame(arr, index=index, columns=columns)
    return out
