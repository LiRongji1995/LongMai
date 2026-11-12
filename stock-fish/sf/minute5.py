"""5-minute bars from BaoStock: a coarse fallback for stocks whose 1-minute bars (intraday.py, Eastmoney) are missing.

BaoStock (the documented API that also serves the daily bars) keeps 5-minute bars for a long history, so a day the
1-minute source refused or that fell out of its ~5-session window can still be described, at 5-minute resolution:
48 bars, 09:35..11:30 and 13:05..15:00, each labelled by its end time (the 09:35 bar includes the call auction).

Layers under intraday/bs5/ (written once, never edited; a different source from the 1-minute layers, never mixed):
- minute/<day>/<UTC stamp>.parquet   date, code, bar (0..47), time (HH:MM bar end), open, high, low, close,
                                     volume (shares), amount (yuan)
- capture/<day>/<UTC stamp>.json     requested / ok / failed (code -> reason) / absent, source, fetched_at

Each query has a time limit (BaoStock sometimes stalls); a stalled or failed query is logged, the session is
re-opened, and the stock stays missing (reported, never filled in).
"""
from __future__ import annotations

import contextlib
import io
import json
import signal
import threading
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import store

VERSION = "SF_MINUTE5_V1"
BARS_PER_DAY = 48
QUERY_TIMEOUT = 20  # seconds per stock
FIELDS = "date,time,code,open,high,low,close,volume,amount"


def _dir(kind: str, day: str):
    path = store.home() / "intraday" / "bs5" / kind / day
    path.mkdir(parents=True, exist_ok=True)
    return path


def _files(kind: str, day: str, pattern: str) -> list:
    path = store.home() / "intraday" / "bs5" / kind / day
    return sorted(path.glob(pattern)) if path.exists() else []


class _Timeout(Exception):
    pass


@contextlib.contextmanager
def _limit(seconds: int):
    """A per-query time limit (SIGALRM; main thread only, elsewhere no limit)."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    def stop(*_):
        raise _Timeout()
    old = signal.signal(signal.SIGALRM, stop)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _login(bs) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        bs.login()


def _times() -> list[str]:
    morning = [f"{(9 * 60 + 30 + 5 * i) // 60:02d}:{(9 * 60 + 30 + 5 * i) % 60:02d}" for i in range(1, 25)]
    afternoon = [f"{(13 * 60 + 5 * i) // 60:02d}:{(13 * 60 + 5 * i) % 60:02d}" for i in range(1, 25)]
    return morning + afternoon


TIMES = _times()
BAR_OF = {t: i for i, t in enumerate(TIMES)}


def parse(rows: list[list[str]]) -> pd.DataFrame:
    """BaoStock rows (FIELDS) -> normalised bars; unknown times are dropped, zero prices become NaN."""
    if not rows:
        return pd.DataFrame(columns=["date", "code", "bar", "time", "open", "high", "low", "close", "volume", "amount"])
    f = pd.DataFrame(rows, columns=FIELDS.split(","))
    f["time"] = f["time"].str[8:10] + ":" + f["time"].str[10:12]
    f["bar"] = f["time"].map(BAR_OF)
    f = f[f["bar"].notna()].copy()
    f["bar"] = f["bar"].astype(int)
    for c in ("open", "high", "low", "close", "volume", "amount"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    for c in ("open", "high", "low", "close"):
        f[c] = f[c].where(f[c] > 0)
    return f[["date", "code", "bar", "time", "open", "high", "low", "close", "volume", "amount"]].sort_values("bar")


def capture(day: str, codes: list[str], log=print, bs=None) -> dict:
    """Fetch `day`'s 5-minute bars for `codes` (those already stored are skipped) as one new write-once file."""
    have = covered(day)
    need = sorted(set(codes) - have)
    if not need:
        return dict(status="ok", requested=0, ok=0, failed={}, absent=[])
    if bs is None:
        import baostock as bs
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    frames, failed, absent = [], {}, []
    _login(bs)
    for code in need:
        try:
            with _limit(QUERY_TIMEOUT):
                rs = bs.query_history_k_data_plus(code, FIELDS, start_date=day, end_date=day, frequency="5",
                                                  adjustflag="3")
                rows = []
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
            if rs.error_code != "0":
                failed[code] = f"错误 {rs.error_code}"
                continue
            bars = parse(rows)
            if bars.empty:
                absent.append(code)
            else:
                frames.append(bars)
        except _Timeout:
            failed[code] = "超时"
            log(f"{code} 5分钟线查询超时，重新登录")
            with contextlib.suppress(Exception):
                _login(bs)
        except Exception as error:  # logged per stock; the stock stays missing
            failed[code] = str(error)[:80]
    with contextlib.suppress(Exception), contextlib.redirect_stdout(io.StringIO()):
        bs.logout()
    ok = [f["code"].iloc[0] for f in frames]
    if frames:
        pd.concat(frames, ignore_index=True).to_parquet(_dir("minute", day) / f"{stamp}.parquet", index=False)
    entry = dict(source="baostock", version=VERSION, date=day, fetched_at=stamp, requested=len(need), ok=len(ok),
                 incomplete=sorted(f["code"].iloc[0] for f in frames if len(f) < BARS_PER_DAY),
                 failed=failed, absent=absent, status="ok" if not (failed or absent) else "partial")
    (_dir("capture", day) / f"{stamp}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1))
    return entry


def load(day: str) -> pd.DataFrame | None:
    files = _files("minute", day, "*.parquet")
    if not files:
        return None
    return pd.concat([pd.read_parquet(p) for p in files], ignore_index=True).drop_duplicates(["code", "bar"], keep="first")


def covered(day: str) -> set[str]:
    bars = load(day)
    return set() if bars is None else set(bars["code"])


def features(day: str, bars: pd.DataFrame | None = None, daily: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per-stock facts from 5-minute bars (index: code). Times are bar end times; NaN = not applicable/unknown."""
    from .strategy import limit_ratio
    bars = load(day) if bars is None else bars
    if bars is None or bars.empty:
        return pd.DataFrame()
    daily = pd.read_parquet(store.bar_path(day)) if daily is None else daily
    d = daily.set_index("code")
    rows = []
    for code, g in bars.groupby("code", sort=True):
        g = g.sort_values("bar")
        pre = float(d.at[code, "preclose"]) if code in d.index else np.nan
        st = str(d.at[code, "isST"]) == "1" if code in d.index else False
        up = np.floor(pre * (1 + limit_ratio([day], [code], np.array([[st]]))[0, 0]) * 100 + 0.5) / 100
        close, high, low = g["close"].to_numpy(), g["high"].to_numpy(), g["low"].to_numpy()
        vol, amt = g["volume"].to_numpy(), g["amount"].to_numpy()
        at = close >= up - 1e-9
        touched = high >= up - 1e-9
        first = int(np.argmax(at)) if at.any() else None
        after = at[first:] if first is not None else np.array([], dtype=bool)
        vwap = amt.sum() / vol.sum() if vol.sum() > 0 else np.nan
        rows.append(dict(
            code=code, bars=len(g), complete=len(g) == BARS_PER_DAY, up_limit=up,
            closed_at_limit=bool(at[-1]) if len(at) else False,
            one_word=bool(len(g) and (low >= up - 1e-9).all()),
            first_touch=g["time"].iloc[int(np.argmax(touched))] if touched.any() else None,
            first_seal=g["time"].iloc[first] if first is not None else None,
            reopen_bars=int(((after[:-1]) & (~after[1:])).sum()) if len(after) > 1 else 0,
            sealed_share=float(after.mean()) if len(after) else np.nan,
            close_vs_vwap=close[-1] / vwap - 1 if vwap and not np.isnan(vwap) else np.nan,
            open30_vol_share=vol[:6].sum() / vol.sum() if vol.sum() > 0 else np.nan,
            tail30_vol_share=vol[-6:].sum() / vol.sum() if vol.sum() > 0 else np.nan,
            fade_from_high=(np.nanmax(high) - close[-1]) / pre if pre else np.nan,
        ))
    out = pd.DataFrame(rows).set_index("code")
    out.attrs.update(date=day, version=VERSION)
    return out


def _has(x) -> bool:
    return x is not None and not (isinstance(x, float) and np.isnan(x))


def describe(r: pd.Series) -> str:
    """One coarse line; it always says it comes from 5-minute bars."""
    parts = ["5分钟线（粗）"]
    if r.get("one_word"):
        parts.append("一字封死")
    elif _has(r.get("first_seal")):
        seal = f"约{r['first_seal']}前首封"
        if r.get("reopen_bars"):
            seal += f"·开板约{int(r['reopen_bars'])}次"
        parts.append(seal + ("" if r.get("closed_at_limit") else "·收盘未封"))
        if r.get("closed_at_limit") and pd.notna(r.get("sealed_share")):
            parts.append(f"封住约{r['sealed_share']:.0%}时间")
    elif _has(r.get("first_touch")):
        parts.append(f"约{r['first_touch']}前触板，收盘未封")
    if not r.get("one_word"):
        if pd.notna(r.get("close_vs_vwap")):
            parts.append(f"收盘较均价{r['close_vs_vwap']:+.1%}")
        if pd.notna(r.get("fade_from_high")) and r["fade_from_high"] >= 0.02:
            parts.append(f"冲高回落{r['fade_from_high']:.1%}")
        if pd.notna(r.get("open30_vol_share")) and pd.notna(r.get("tail30_vol_share")):
            parts.append(f"成交 早30分{r['open30_vol_share']:.0%}/尾30分{r['tail30_vol_share']:.0%}")
    if not r.get("complete", True):
        parts.append(f"5分钟线不全（{int(r['bars'])}/{BARS_PER_DAY}根）")
    return "；".join(parts)


def notes(day: str, codes: list[str]) -> dict[str, str]:
    """Coarse notes for the `codes` that have stored 5-minute bars of `day` (others are left out)."""
    have = covered(day)
    want = [c for c in codes if c in have]
    if not want:
        return {}
    f = features(day)
    return {c: describe(f.loc[c]) for c in want if c in f.index}
