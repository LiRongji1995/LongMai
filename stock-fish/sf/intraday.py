"""Intraday 1-minute bars for the stocks that matter on a session, and per-stock intraday features.

Source: Eastmoney `push2his .../stock/trends2/get` (the endpoint AKShare's `stock_zh_a_hist_min_em` uses for
period 1), one request per stock returning the last 5 sessions, 241 bars per session (09:30 bar = call
auction + first minute, then 09:31-11:30 and 13:01-15:00). The source keeps only ~5 sessions, so every day
not captured is lost for good; capture runs after the close (`sf premarket` / `sf intraday`).

Layers under intraday/ (see docs/INTRADAY_STUDY.md for definitions):
- raw/<UTC stamp>.jsonl.gz      every response body exactly as received, one line per request
- minute/<day>.parquet          normalised bars: date, code, minute (0..240), time, open (NaN when the
                                source sends 0.00), high, low, close, volume (shares), amount (yuan), avg (source VWAP)
- universe/<day>.parquet        which stocks were captured for the day and why (known after that close)
- capture/<day>.json            requested / ok / failed / incomplete codes, raw file, fetched_at, format
- features/<FEATURES>/<day>.parquet   derived, recomputable (excluded from backups)
Session files are written once and never modified. Missing stocks are listed in the capture log, never
dropped silently; a stock with fewer than 241 bars on a traded day is "incomplete".
"""
from __future__ import annotations

import gzip
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import store

FORMAT = "SF_INTRADAY_V1"
FEATURES = "SF_INTRADAY_FEATURES_V1"
URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
PARAMS = {"fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
          "ut": "7eea3edcaed734bea9cbfc24409ed989", "ndays": "5", "iscr": "0"}
BARS_PER_DAY = 241
LOT = 100  # the source's volume unit is 手 (100 shares)
READY_HOUR_MINUTE = (15, 30)  # a session's bars are complete after the close


def _dir(name: str):
    path = store.home() / "intraday" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def minute_path(day: str):
    return _dir("minute") / f"{day}.parquet"


def captured(day: str) -> bool:
    return minute_path(day).exists()


# ------------------------------------------------------------------ universe

def universe(day: str, list_codes: dict[str, list[str]] | None = None) -> pd.DataFrame:
    """SSE/SZSE A shares that closed at or touched the limit on `day`, plus stocks on the saved lists for
    `day` (as target or observation session). One row per code with its reasons; uses `day`'s bars only."""
    from .strategy import limit_ratio
    bars = pd.read_parquet(store.bar_path(day))
    num = bars["code"].str[3:]
    keep = (bars["code"].str.startswith("sh.") & num.str.startswith(("60", "68"))) | \
           (bars["code"].str.startswith("sz.") & num.str.startswith(("00", "30")))
    b = bars[keep & bars["tradestatus"].astype(str).eq("1") & (bars["volume"] > 0)].copy()
    ratio = limit_ratio([day], b["code"].to_numpy(), b["isST"].astype(str).eq("1").to_numpy()[None, :])[0]
    up = np.floor(b["preclose"].to_numpy() * (1 + ratio) * 100 + 0.5) / 100
    reasons: dict[str, list[str]] = {}
    for code, hit_close, hit_high in zip(b["code"], b["close"].to_numpy() >= up - 1e-9, b["high"].to_numpy() >= up - 1e-9):
        if hit_close:
            reasons.setdefault(code, []).append("limit_up")
        elif hit_high:
            reasons.setdefault(code, []).append("touched")
    for reason, codes in (list_codes if list_codes is not None else listed_codes(day)).items():
        for code in codes:
            reasons.setdefault(code, []).append(reason)
    return pd.DataFrame({"code": list(reasons), "reasons": ["+".join(r) for r in reasons.values()]}).assign(date=day)


def listed_codes(day: str) -> dict[str, list[str]]:
    """Codes of the latest saved list whose target is `day` and of the one built on `day` (observation)."""
    from .listing import all_runs, latest_run
    out = {"list_target": [], "list_obs": []}
    run = latest_run(day) if (store.home() / "lists" / day).exists() else None
    if run is not None and (run / "picks.csv").exists():
        out["list_target"] = pd.read_csv(run / "picks.csv")["code"].tolist()
    for run in all_runs():
        summary = json.loads((run / "summary.json").read_text())
        if summary.get("observation") == day and (run / "picks.csv").exists():
            out["list_obs"] = pd.read_csv(run / "picks.csv")["code"].tolist()
    return out


# ------------------------------------------------------------------ fetch and normalise

def _secid(code: str) -> str:
    return ("1." if code.startswith("sh.") else "0.") + code[3:]


def fetch(code: str) -> str:
    """One raw response body (text) for `code`; raises after bounded retries."""
    import requests

    def get():
        response = requests.get(URL, params={**PARAMS, "secid": _secid(code)}, timeout=15)
        response.raise_for_status()
        return response.text
    return store._retry(get, attempts=3, pause=1.0)


def parse(code: str, body: str) -> pd.DataFrame:
    """Normalised bars for every session in one response. Empty frame when the source has no data."""
    data = (json.loads(body) or {}).get("data") or {}
    rows = [t.split(",") for t in data.get("trends") or []]
    if not rows:
        return pd.DataFrame(columns=["date", "code", "minute", "time", "open", "high", "low", "close",
                                     "volume", "amount", "avg"])
    f = pd.DataFrame(rows, columns=["ts", "open", "close", "high", "low", "volume", "amount", "avg"])
    for c in ("open", "close", "high", "low", "volume", "amount", "avg"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    f["open"] = f["open"].where(f["open"] > 0)  # the source sends 0.00 for opens of earlier sessions
    f["volume"] = f["volume"] * LOT
    f["date"], f["time"] = f["ts"].str[:10], f["ts"].str[11:16]
    f["minute"] = f.groupby("date").cumcount()
    f["code"] = code
    return f[["date", "code", "minute", "time", "open", "high", "low", "close", "volume", "amount", "avg"]]


def capture(days: list[str], workers: int = 4, log=print) -> dict[str, dict]:
    """Capture every day in `days` that is not stored yet, with one request per needed stock (each response
    covers the source's last 5 sessions). Returns the capture log per day."""
    now = store.now_sh()
    today = str(now.date())
    todo = [d for d in days if not captured(d)]
    blocked = [d for d in todo if d == today and (now.hour, now.minute) < READY_HOUR_MINUTE]
    todo = [d for d in todo if d not in blocked]
    result = {d: dict(status="cached") for d in days if d not in todo and d not in blocked}
    result.update({d: dict(status="FAILED: session not closed yet") for d in blocked})
    if not todo:
        return result
    universes = {d: universe(d) for d in todo}
    codes = sorted(set().union(*(set(u["code"]) for u in universes.values())))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = _dir("raw") / f"{stamp}.jsonl.gz"
    bodies, errors = {}, {}

    def one(code):
        try:
            return code, fetch(code), None
        except Exception as error:  # recorded per code in the capture log; the day reports it as failed
            return code, None, str(error)[:120]

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool, gzip.open(raw_path, "wt", encoding="utf-8") as raw:
        for i, (code, body, error) in enumerate(pool.map(one, codes), 1):
            fetched_at = datetime.now(timezone.utc).isoformat()
            raw.write(json.dumps(dict(code=code, url=URL, fetched_at=fetched_at, error=error, body=body),
                                 ensure_ascii=False) + "\n")
            if error:
                errors[code] = error
            else:
                bodies[code] = body
            if i % 100 == 0:
                log(f"  intraday {i}/{len(codes)} ({time.monotonic() - started:.0f}s)")
    frames = {code: parse(code, body) for code, body in bodies.items()}
    for day in todo:
        u = universes[day]
        parts, ok, incomplete, absent = [], [], [], []
        for code in u["code"]:
            if code in errors:
                continue
            part = frames[code][frames[code]["date"] == day]
            if part.empty:
                absent.append(code)  # no bars for this day in the response (outside the 5-session window)
                continue
            parts.append(part)
            (ok if len(part) == BARS_PER_DAY else incomplete).append(code)
        failed = sorted(set(u["code"]) & set(errors))
        entry = dict(format=FORMAT, date=day, raw=raw_path.name, captured_at=stamp, requested=len(u),
                     ok=len(ok), incomplete=incomplete, failed={c: errors[c] for c in failed}, absent=absent)
        if not parts:
            entry["status"] = "FAILED: no bars (outside the source's 5-session window?)"
            (_dir("capture") / f"{day}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1))
            result[day] = entry
            continue
        for frame, path in ((pd.concat(parts, ignore_index=True), minute_path(day)),
                            (u, _dir("universe") / f"{day}.parquet")):
            tmp = path.with_name(path.name + ".tmp")
            frame.to_parquet(tmp, index=False)
            tmp.replace(path)
        entry["status"] = "ok" if not (failed or incomplete or absent) else "partial"
        (_dir("capture") / f"{day}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1))
        result[day] = entry
    return result


def load(day: str) -> pd.DataFrame | None:
    return pd.read_parquet(minute_path(day)) if captured(day) else None


# ------------------------------------------------------------------ features

def features(day: str, minute: pd.DataFrame | None = None, daily: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per-stock intraday facts for `day` (index: code), all known after that close. Minutes count trading
    minutes from 09:30 (0 = the 09:30 bar, 120 = 11:30, 240 = 15:00). NaN = not applicable or unknown."""
    from .strategy import limit_ratio
    minute = load(day) if minute is None else minute
    if minute is None or minute.empty:
        return pd.DataFrame()
    daily = pd.read_parquet(store.bar_path(day)) if daily is None else daily
    d = daily.set_index("code")
    rows = []
    for code, g in minute.groupby("code", sort=True):
        g = g.sort_values("minute")
        pre = float(d.at[code, "preclose"]) if code in d.index else np.nan
        st = str(d.at[code, "isST"]) == "1" if code in d.index else False
        up = np.floor(pre * (1 + limit_ratio([day], [code], np.array([[st]]))[0, 0]) * 100 + 0.5) / 100
        close, high, low = g["close"].to_numpy(), g["high"].to_numpy(), g["low"].to_numpy()
        vol, amt = g["volume"].to_numpy(), g["amount"].to_numpy()
        at = close >= up - 1e-9
        touched = high >= up - 1e-9
        first_touch = int(np.argmax(touched)) if touched.any() else np.nan
        first_seal = int(np.argmax(at)) if at.any() else np.nan
        after = at[int(first_seal):] if at.any() else np.array([], dtype=bool)
        reopen = int(((after[:-1]) & (~after[1:])).sum()) if len(after) > 1 else 0
        closed_at = bool(at[-1]) if len(at) else False
        last_seal = np.nan
        if closed_at:
            breaks = np.flatnonzero(~at)
            last_seal = int(breaks[-1] + 1) if len(breaks) else 0
        total_vol, total_amt = vol.sum(), amt.sum()
        vwap = total_amt / total_vol if total_vol > 0 else np.nan
        running = g["avg"].to_numpy()
        rows.append(dict(
            code=code, bars=len(g), complete=len(g) == BARS_PER_DAY, preclose=pre, up_limit=up,
            closed_at_limit=closed_at, touched_limit=bool(touched.any()),
            one_word=bool(len(g) and (low >= up - 1e-9).all()),
            first_touch_min=first_touch, first_seal_min=first_seal, last_seal_min=last_seal,
            sealed_share=float(after.mean()) if len(after) else np.nan, reopen_count=reopen if at.any() else np.nan,
            vwap=vwap, close_vs_vwap=close[-1] / vwap - 1 if vwap and not np.isnan(vwap) else np.nan,
            above_vwap_share=float((close > running).mean()) if len(g) else np.nan,
            auction_vol_share=vol[0] / total_vol if total_vol > 0 else np.nan,
            open30_vol_share=vol[:31].sum() / total_vol if total_vol > 0 else np.nan,
            tail30_vol_share=vol[-30:].sum() / total_vol if total_vol > 0 else np.nan,
            fade_from_high=(high.max() - close[-1]) / pre if pre else np.nan,
        ))
    out = pd.DataFrame(rows).set_index("code")
    out["seal_type"] = seal_type(out)
    out.attrs.update(date=day, version=FEATURES)
    return out


def seal_type(f: pd.DataFrame) -> pd.Series:
    """一字 / 秒板 (sealed by minute 5) / 早盘封 (by 11:30) / 午后封 / 回封 (reopened then closed at limit) /
    炸板 (touched, did not close at limit) / "" (never touched)."""
    closed, touched = f["closed_at_limit"].astype(bool), f["touched_limit"].astype(bool)
    reopened = f["reopen_count"].fillna(0) > 0
    out = np.select(
        [f["one_word"].astype(bool), closed & reopened, closed & (f["first_seal_min"] <= 5),
         closed & (f["first_seal_min"] <= 120), closed, touched],
        ["一字", "回封", "秒板", "早盘封", "午后封", "炸板"], "")
    return pd.Series(out, index=f.index)


def feature_path(day: str):
    return _dir(f"features/{FEATURES}") / f"{day}.parquet"


def stored_features(day: str) -> pd.DataFrame:
    """Features for `day`, computed once from the minute file and cached under the features version."""
    path = feature_path(day)
    if path.exists():
        return pd.read_parquet(path)
    f = features(day)
    if not f.empty:
        f.to_parquet(path)
    return f


# ------------------------------------------------------------------ report (docs/INTRADAY_STUDY.md)

SEAL_ORDER = ("一字", "秒板", "早盘封", "午后封", "回封", "炸板")
MIN_SEALED_IN_BOARD = 3
MIN_SESSIONS_FOR_TEST = 40


def lead_ranks(f: pd.DataFrame, members: pd.DataFrame) -> pd.DataFrame:
    """For stocks that closed at the limit: their board (the hot board, >= 3 sealed members, with the most sealed
    members among the stock's boards) and their rank by first seal minute inside it (1 = sealed first)."""
    sealed = f[f["closed_at_limit"].astype(bool)]
    m = members[members["code"].isin(sealed.index)]
    counts = m.groupby("board")["code"].nunique()
    hot = counts[counts >= MIN_SEALED_IN_BOARD]
    m = m[m["board"].isin(hot.index)].assign(n=lambda x: x["board"].map(hot))
    best = m.sort_values(["code", "n", "board"], ascending=[True, False, True]).drop_duplicates("code")
    out = best.set_index("code")[["board", "n"]].join(sealed[["first_seal_min"]])
    out["lead_rank"] = out.groupby("board")["first_seal_min"].rank(method="min")
    return out


def report(log=print) -> dict:
    """Descriptive tables for H1-H3 over every captured session (entry-free outcomes, pending stays NaN)."""
    from . import evaluate, strategy, themes
    days = sorted(p.stem for p in _dir("minute").glob("*.parquet"))
    stored = sorted(p.stem for p in (store.home() / "bars").glob("*.parquet"))
    w = strategy.panel(store.load_window(stored))
    out = evaluate.stock_outcomes(w)
    loaded = themes.load()
    rows = []
    for day in days:
        f = stored_features(day)
        if f.empty or day not in w["close"].index:
            continue
        f = f[strategy.is_main_board(pd.Series(f.index, index=f.index))]
        ranks = lead_ranks(f, loaded[1]) if loaded is not None else pd.DataFrame(columns=["board", "lead_rank"])
        f = f.join(ranks[["board", "lead_rank"]])
        for k in ("lu1", "bigloss1", "out5"):
            f[k] = out[k].loc[day].reindex(f.index)
        rows.append(f.assign(date=day).reset_index())
    panel = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    result = dict(sessions=len(days), first=days[0] if days else None, last=days[-1] if days else None,
                  panel=panel, testable=len(days) >= MIN_SESSIONS_FOR_TEST)
    if not panel.empty:
        def summary(group):
            g = panel[panel[group].notna() & (panel[group] != "")].groupby(group)
            t = g[["lu1", "bigloss1", "out5"]].mean().round(3)
            t["n"] = g.size()
            t["pending_lu1"] = g["lu1"].apply(lambda s: int(s.isna().sum()))
            return t
        seal = summary("seal_type")
        result["seal"] = seal.reindex([s for s in SEAL_ORDER if s in seal.index])
        panel["lead"] = np.where(panel["lead_rank"] == 1, "先封(1)", np.where(panel["lead_rank"] >= 2, "后封(≥2)", ""))
        result["lead"] = summary("lead")
    result["priced"] = priced_in(w, out)
    return result


def priced_in(w: dict, out: dict) -> pd.DataFrame:
    """H3 (daily bars, full stored history): next-open gap and unbuyable share of main-board limit-ups by height."""
    tr = w["traded"].fillna(False).astype(bool) & ~w["st"].fillna(False).astype(bool)
    lu = w["limit_up"].fillna(False).astype(bool) & tr
    gap = (w["open"] / w["preclose"] - 1).shift(-1)
    unbuyable = w["one_price_up"].shift(-1).astype("boolean").where(w["traded"].shift(-1).astype("boolean"))
    h = w["height"].where(lu)
    frame = pd.DataFrame({"height": h.stack(), "gap": gap.where(lu).stack(),
                          "unbuyable": unbuyable.where(lu).stack().astype(float), "max3": out["max3"].where(lu).stack()})
    frame = frame[frame["height"] >= 1]
    frame["bucket"] = pd.cut(frame["height"], [0, 1, 2, 3, np.inf], labels=["首板", "2板", "3板", "4板+"])
    frame["gap_share"] = (frame["gap"] / frame["max3"]).where(frame["max3"] > 0.05)
    g = frame.groupby("bucket", observed=True)
    t = pd.DataFrame({"n": g.size(), "gap_mean": g["gap"].mean(), "unbuyable": g["unbuyable"].mean(),
                      "max3_mean": g["max3"].mean(), "gap_share_median": g["gap_share"].median()}).round(3)
    t.attrs.update(first=str(w["close"].index[0]), last=str(w["close"].index[-1]))
    return t
