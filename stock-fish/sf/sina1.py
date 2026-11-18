"""1-minute bars from Sina: a second source for stocks the Eastmoney 1-minute capture (intraday.py) is missing.

Source: the Sina K-line endpoint AKShare's `stock_zh_a_minute` uses (period 1), one request per stock returning its
last 1970 one-minute bars (about 8 sessions, a little longer than Eastmoney's ~5). 238 bars per session: 09:31..11:30
and 13:01..15:00 without 14:58 / 14:59 (closing call auction); the 09:31 bar includes the opening call auction, so
there is no separate auction bar. Bars are numbered like Eastmoney's (09:31 = 1, 11:30 = 120, 13:01 = 121,
15:00 = 240) so the same feature code applies (`intraday.features(..., auction_bar=False)`).

Layers under intraday/sina1/ (written once, never edited; a separate source, never mixed into the Eastmoney
1-minute files or the study tables of docs/INTRADAY_STUDY.md):
- raw/<UTC stamp>.jsonl.gz          every response body as received, one line per request
- minute/<day>/<UTC stamp>.parquet  date, code, minute, time, open, high, low, close, volume (shares), amount (yuan),
                                    avg (running VWAP computed from amount / volume)
- capture/<day>/<UTC stamp>.json    requested / ok / incomplete / failed (code -> reason) / absent, source, fetched_at

Requests are sent one at a time with a pause (AKShare notes that Sina blocks heavy scraping); when the first
GIVE_UP_AFTER requests all fail the rest are not sent and logged as not requested.
"""
from __future__ import annotations

import gzip
import json
import time
from datetime import datetime, timezone

import pandas as pd

from . import store

VERSION = "SF_SINA1_V1"
URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData"
BARS_PER_DAY = 238
PAUSE = 0.5  # seconds between requests
GIVE_UP_AFTER = 12
NOT_REQUESTED = "未请求：本次前几次请求全部失败"
COLUMNS = ["date", "code", "minute", "time", "open", "high", "low", "close", "volume", "amount", "avg"]


def _dir(kind: str, day: str | None = None):
    path = store.home() / "intraday" / "sina1" / kind / (day or "")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _files(kind: str, day: str, pattern: str) -> list:
    path = store.home() / "intraday" / "sina1" / kind / day
    return sorted(path.glob(pattern)) if path.exists() else []


def minute_of(t: str) -> int | None:
    """HH:MM bar end -> Eastmoney minute number (09:31 = 1 ... 15:00 = 240); None outside the sessions."""
    h, m = int(t[:2]), int(t[3:5])
    x = h * 60 + m
    if 9 * 60 + 31 <= x <= 11 * 60 + 30:
        return x - (9 * 60 + 30)
    if 13 * 60 + 1 <= x <= 15 * 60:
        return 120 + x - 13 * 60
    return None


def fetch(code: str) -> str:
    """One raw response body for `code` (e.g. sh.600001); raises after bounded retries."""
    import requests

    def get():
        r = requests.get(URL, params=dict(symbol=code.replace(".", ""), scale="1", ma="no", datalen="1970"),
                         timeout=15)
        r.raise_for_status()
        return r.text
    return store._retry(get, attempts=2, pause=2.0)


def parse(code: str, body: str) -> pd.DataFrame:
    """Normalised bars for every session in one response; empty frame when the source has no data."""
    try:
        rows = json.loads(body.split("=(", 1)[1].rsplit(");", 1)[0]) if "=(" in body else []
    except ValueError:
        rows = []
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    f = pd.DataFrame(rows)
    f["date"], f["time"] = f["day"].str[:10], f["day"].str[11:16]
    f["minute"] = f["time"].map(minute_of)
    f = f[f["minute"].notna()].copy()
    f["minute"] = f["minute"].astype(int)
    for c in ("open", "high", "low", "close", "volume", "amount"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    for c in ("open", "high", "low", "close"):
        f[c] = f[c].where(f[c] > 0)
    f = f.sort_values(["date", "minute"])
    cum_v = f.groupby("date")["volume"].cumsum()
    f["avg"] = (f.groupby("date")["amount"].cumsum() / cum_v).where(cum_v > 0)
    f["code"] = code
    return f[COLUMNS].reset_index(drop=True)


def capture(day: str, codes: list[str], log=print, fetcher=None, pause: float = PAUSE) -> dict:
    """Fetch `day`'s Sina 1-minute bars for `codes` not stored yet, as one new write-once file."""
    need = sorted(set(codes) - covered(day))
    if not need:
        return dict(status="ok", requested=0, ok=0, failed={}, absent=[], incomplete=[])
    fetcher = fetcher or fetch
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    frames, failed, absent, tried_ok = [], {}, [], 0
    with gzip.open(_dir("raw") / f"{stamp}.jsonl.gz", "wt", encoding="utf-8") as raw:
        for i, code in enumerate(need):
            if i >= GIVE_UP_AFTER and tried_ok == 0:
                failed[code] = NOT_REQUESTED
                continue
            if i and pause:
                time.sleep(pause)
            try:
                body, error = fetcher(code), None
            except Exception as exc:  # logged per stock; the stock stays missing
                body, error = None, str(exc)[:120]
            raw.write(json.dumps(dict(code=code, url=URL, fetched_at=datetime.now(timezone.utc).isoformat(),
                                      error=error, body=body), ensure_ascii=False) + "\n")
            if error:
                failed[code] = error
                continue
            tried_ok += 1
            bars = parse(code, body)
            part = bars[bars["date"] == day]
            (frames.append(part) if len(part) else absent.append(code))
    if frames:
        pd.concat(frames, ignore_index=True).to_parquet(_dir("minute", day) / f"{stamp}.parquet", index=False)
    entry = dict(source="sina", version=VERSION, date=day, fetched_at=stamp, requested=len(need),
                 ok=len(frames), incomplete=sorted(f["code"].iloc[0] for f in frames if len(f) < BARS_PER_DAY),
                 failed=failed, absent=absent, status="ok" if not (failed or absent) else "partial")
    (_dir("capture", day) / f"{stamp}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1))
    log(f"{day} 新浪1分钟线：请求 {len(need)} 只，成功 {len(frames)}，失败 {len(failed)}，当天无数据 {len(absent)}")
    return entry


def load(day: str) -> pd.DataFrame | None:
    files = _files("minute", day, "*.parquet")
    if not files:
        return None
    return pd.concat([pd.read_parquet(p) for p in files], ignore_index=True).drop_duplicates(["code", "minute"],
                                                                                            keep="first")


def covered(day: str) -> set[str]:
    bars = load(day)
    return set() if bars is None else set(bars["code"])


def features(day: str, daily: pd.DataFrame | None = None) -> pd.DataFrame:
    from . import intraday
    bars = load(day)
    if bars is None or bars.empty:
        return pd.DataFrame()
    return intraday.features(day, minute=bars, daily=daily, bars_per_day=BARS_PER_DAY, auction_bar=False)


def notes(day: str, codes: list[str]) -> dict[str, str]:
    """Notes for the `codes` with stored Sina bars of `day` (others left out); each says where it comes from."""
    from . import intraday
    want = [c for c in codes if c in covered(day)]
    if not want:
        return {}
    f = features(day)
    return {c: "新浪1分钟线：" + (intraday.describe(f.loc[c], per_day=BARS_PER_DAY) or "无涨停相关事实")
            for c in want if c in f.index}
