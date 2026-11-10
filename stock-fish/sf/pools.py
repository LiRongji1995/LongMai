"""Daily hot-money pools from Eastmoney via AKShare, stored per session.

Kinds:
- zt:   limit-up pool (first/last seal time, break count, seal amount, board count, industry)
- zbgc: failed-board pool (touched limit-up, closed below)
- dtgc: limit-down pool
- lhb:  dragon-tiger list summary rows for the session

Eastmoney serves these pools only for recent sessions, so capture them every day; a missing file
means "not captured" (unknown), never "empty pool". Empty zt / lhb answers are not stored (see
MUST_HAVE_ROWS); an empty zbgc / dtgc pool is stored as an empty file.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import store

KINDS = {
    "zt": "stock_zt_pool_em",
    "zbgc": "stock_zt_pool_zbgc_em",
    "dtgc": "stock_zt_pool_dtgc_em",
}


# Every session has limit-ups and LHB listings, so an empty answer for these is "not published yet" and is
# not stored (stays "not captured"). Failed-board and limit-down pools can be genuinely empty.
MUST_HAVE_ROWS = ("zt", "lhb")


def _dir(kind: str):
    path = store.home() / "pools" / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def path(kind: str, day: str):
    return _dir(kind) / f"{day}.parquet"


def to_code(series: pd.Series) -> pd.Series:
    series = series.astype(str).str.zfill(6)
    return np.where(series.str.startswith("6"), "sh.", "sz.") + series


def _fetch(kind: str, day: str) -> pd.DataFrame:
    import akshare as ak
    compact = day.replace("-", "")
    if kind == "lhb":
        return ak.stock_lhb_detail_em(start_date=compact, end_date=compact)
    return getattr(ak, KINDS[kind])(date=compact)


def capture(day: str, kinds=("zt", "zbgc", "dtgc", "lhb"), refresh: bool = False, log=print) -> dict:
    status = {}
    for kind in kinds:
        target = path(kind, day)
        if target.exists() and not refresh:
            status[kind] = "cached"
            continue
        try:
            frame = store._retry(_fetch, kind, day, attempts=2, pause=2.0)
        except Exception as error:
            status[kind] = f"FAILED: {str(error)[:120]}"
            log(f"  pool {kind} {day} FAILED: {error}")
            continue
        frame = frame if frame is not None else pd.DataFrame()
        if frame.empty and kind in MUST_HAVE_ROWS:
            status[kind] = "FAILED: empty answer (not published yet?); not stored"
            log(f"  pool {kind} {day}: empty answer, not stored")
            continue
        frame = frame.astype({c: str for c in frame.columns if frame[c].dtype == object})
        if "代码" in frame:
            frame["code"] = to_code(frame["代码"])
        frame["captured_at"] = datetime.now(timezone.utc).isoformat()
        frame.to_parquet(target, index=False)
        status[kind] = f"{len(frame)} rows"
    return status


def load(kind: str, day: str) -> pd.DataFrame | None:
    """None means not captured (unknown); an empty frame means a captured empty pool."""
    target = path(kind, day)
    return pd.read_parquet(target) if target.exists() else None


def hhmm(value) -> str | None:
    text = str(value or "").strip()
    if not text or text in ("nan", "None"):
        return None
    text = text.zfill(6)
    return f"{text[:2]}:{text[2:4]}"


def seal_facts(day: str) -> pd.DataFrame | None:
    """Per-code limit-up seal facts for one session."""
    zt = load("zt", day)
    if zt is None:
        return None
    if zt.empty:
        return pd.DataFrame(columns=["first_seal", "last_seal", "breaks", "seal_amount", "zt_stat", "em_industry"])
    z = zt.set_index("code")
    return pd.DataFrame({
        "first_seal": z["首次封板时间"].map(hhmm),
        "last_seal": z["最后封板时间"].map(hhmm),
        "breaks": pd.to_numeric(z["炸板次数"], errors="coerce"),
        "seal_amount": pd.to_numeric(z["封板资金"], errors="coerce"),
        "zt_stat": z["涨停统计"],
        "em_industry": z["所属行业"],
    })


def lhb_window(days: list[str]) -> pd.DataFrame | None:
    """LHB appearances and net buy over the given sessions; None if any session is uncaptured."""
    frames = []
    for day in days:
        frame = load("lhb", day)
        if frame is None:
            return None
        frames.append(frame)
    rows = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(not f.empty for f in frames) else pd.DataFrame()
    if rows.empty:
        return pd.DataFrame(columns=["lhb_times", "lhb_net"])
    rows["net"] = pd.to_numeric(rows.get("龙虎榜净买额"), errors="coerce")
    return rows.groupby("code").agg(lhb_times=("code", "size"), lhb_net=("net", "sum"))


def coverage(days: list[str]) -> dict:
    return {kind: [d for d in days if not path(kind, d).exists()] for kind in (*KINDS, "lhb")}


if __name__ == "__main__":  # manual check
    print(json.dumps(capture(store.latest_complete_session(store.calendar())), ensure_ascii=False))
