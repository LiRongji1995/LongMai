"""Historical base rates (历史同类) for listed stocks: description only, never changes a group or order.

`sf baserates` replays the live V9 list over the stored history (`auction.replay`) and writes a table, once,
under research/baserates/<UTC stamp>/. Each listed stock is matched to a cell (role x board height x one-price)
and shown what happened to the same cell historically:
  from the observation close: lu1 (晋级), out5 (5 日走出来), bigloss1 (次日大面), share opening at the limit-up price
  buyable subset (next session did not open at the limit): limit-up close, big loss after the open, and the medians
  of the session's high / low / close relative to its open (open-anchored price bands)
At 09:25 (`sf auction`) the buyable bands are refined by the adopted auction label (deny / not deny).

Point-in-time: only rows whose outcomes are complete in the store are used; the table records its last session
(`cutoff`) and a list uses a table only when cutoff <= observation date. A cell with fewer than MIN_N resolved rows
falls back to (role x height), then to the role.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import auction, store, strategy

VERSION = "SF_BASERATES_V1"
MIN_N = 30
ROLE_NAMES = {"leader": "主线龙头", "ladder": "连板梯队", "observe": "观察"}


def height_bucket(h) -> str:
    if pd.isna(h):
        return "?"
    h = int(h)
    return "未涨停" if h <= 0 else f"{h}板" if h < 4 else "4板+"


def enrich(panel: pd.DataFrame, w: dict) -> pd.DataFrame:
    """Add the cell keys (from t) and the open-anchored session bands (of T) to an `auction.replay` panel."""
    p = panel.copy()
    o = w["open"]
    extra = {"height": w["height"].astype(float).where(w["close"].notna()),
             "one_price": w["one_price_up"].astype(float).where(w["traded"].fillna(False).astype(bool))}
    for k, frame in extra.items():
        s = frame.stack(future_stack=True)
        s.index.names = ["obs", "code"]
        p = p.join(s.rename(k), on=["obs", "code"])
    ok = w["traded"].fillna(False).astype(bool)
    bands = {"hi": w["high"] / o - 1, "lo": w["low"] / o - 1, "cl": w["close"] / o - 1}
    for k, frame in bands.items():
        s = frame.where(ok).stack(future_stack=True)
        s.index.names = ["day", "code"]
        p = p.join(s.rename(k), on=["day", "code"])
    p["hb"] = p["height"].map(height_bucket)
    p["one"] = p["one_price"].map({1.0: "一字", 0.0: "非一字"}).fillna("?")
    p["cls"] = "other"
    for r, a in auction.ADOPTED.items():
        m = p["role"] == r
        lab = auction.classify(p.loc[m, "gap"], p.loc[m, "open_limit"], *a["cuts"])
        p.loc[m, "cls"] = np.where(lab.eq("deny") & a["deny"], "deny", "other")
    return p


def _cell_stats(g: pd.DataFrame) -> dict:
    res = g[g["c_out5"].notna()]
    buy = res[res["open_limit"].eq(0.0) & res["gap"].notna()]
    return dict(n=len(res), lu1=res["c_lu1"].mean(), out5=res["c_out5"].mean(), bigloss1=res["c_bigloss1"].mean(),
                open_limit=res["open_limit"].mean(), nb=len(buy), b_lu=buy["a_lu"].mean(),
                b_bigloss=buy["a_bigloss"].mean(), b_hi=buy["hi"].median(), b_lo=buy["lo"].median(),
                b_cl=buy["cl"].median(), b_below=(buy["cl"] < 0).mean() if len(buy) else np.nan)


def table(p: pd.DataFrame) -> pd.DataFrame:
    rows = []
    levels = (("role", "hb", "one"), ("role", "hb"), ("role",))
    for keys in levels:
        for key, g in p.groupby(list(keys)):
            key = key if isinstance(key, tuple) else (key,)
            cell = dict(zip(keys, key))
            rows.append(dict(level=len(keys), role=cell["role"], hb=cell.get("hb", "*"), one=cell.get("one", "*"),
                             cls="*", **_cell_stats(g)))
            for cls, gc in g.groupby("cls"):  # buyable bands by auction label (only adopted roles have "deny")
                if cls == "deny" or (g["cls"] == "deny").any():
                    rows.append(dict(level=len(keys), role=cell["role"], hb=cell.get("hb", "*"),
                                     one=cell.get("one", "*"), cls=cls, **_cell_stats(gc)))
    return pd.DataFrame(rows)


def run(log=print) -> tuple[pd.DataFrame, dict]:
    stored = sorted(x.stem for x in (store.home() / "bars").glob("*.parquet"))
    panel = auction.replay(stored, log=log)
    w = strategy.panel(store.load_window(stored))
    p = enrich(panel, w)
    meta = dict(version=VERSION, cutoff=stored[-1], first=str(p["obs"].min()), rows=int(len(p)),
                auction=auction.VERSION, adopted=sorted(auction.ADOPTED), min_n=MIN_N,
                built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return table(p), meta


def save(t: pd.DataFrame, meta: dict) -> Path:
    out = store.home() / "research" / "baserates" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=False)
    t.to_parquet(out / "table.parquet")
    (out / "summary.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return out


def latest(observation: str) -> tuple[pd.DataFrame | None, dict | None]:
    """The newest saved table whose cutoff is on or before `observation` (None if there is none)."""
    root = store.home() / "research" / "baserates"
    if not root.exists():
        return None, None
    for run_dir in sorted((d for d in root.iterdir() if d.is_dir()), reverse=True):
        meta = json.loads((run_dir / "summary.json").read_text())
        if meta["cutoff"] <= observation:
            return pd.read_parquet(run_dir / "table.parquet"), dict(meta, run=run_dir.name)
    return None, None


def lookup(t: pd.DataFrame, role: str, height, one_price, cls: str = "*") -> pd.Series | None:
    """The most specific cell with >= MIN_N resolved rows (for a label: >= MIN_N buyable rows)."""
    hb = height_bucket(height)
    one = {1.0: "一字", 0.0: "非一字"}.get(float(one_price) if pd.notna(one_price) else None, "?")
    size = "n" if cls == "*" else "nb"
    for level, cond in ((3, (t["hb"] == hb) & (t["one"] == one)), (2, (t["hb"] == hb) & (t["one"] == "*")),
                        (1, t["hb"] == "*")):
        m = t[(t["level"] == level) & (t["role"] == role) & (t["cls"] == cls) & cond]
        if len(m) and m.iloc[0][size] >= MIN_N:
            return m.iloc[0]
    return None


def _pct(x) -> str:
    return "?" if pd.isna(x) else f"{x * 100:.0f}%"


def _signed(x) -> str:
    return "?" if pd.isna(x) else f"{x * 100:+.1f}%"


def _cell_name(r: pd.Series) -> str:
    parts = [ROLE_NAMES.get(r["role"], r["role"])] + [x for x in (r["hb"], r["one"]) if x not in ("*", "?")]
    return "·".join(parts)


def describe(r: pd.Series | None) -> str:
    """Pre-market text: outcomes from the observation close, plus the buyable subset."""
    if r is None:
        return "同类样本不足"
    s = (f"{_cell_name(r)} {int(r['n'])}次：次日涨停{_pct(r['lu1'])}·5日走出来{_pct(r['out5'])}·"
         f"次日大面{_pct(r['bigloss1'])}；次日涨停开{_pct(r['open_limit'])}")
    if r["nb"] >= MIN_N:
        s += f"，未涨停开时开盘后大面{_pct(r['b_bigloss'])}"
    return s


def describe_bands(r: pd.Series | None) -> str:
    """09:25 text for a buyable stock: open-anchored bands of the same cell and auction label."""
    if r is None:
        return "同类样本不足"
    return (f"{_cell_name(r)}{'·竞价否定' if r['cls'] == 'deny' else ''} {int(r['nb'])}次：开盘后最高中位{_signed(r['b_hi'])}·"
            f"最低中位{_signed(r['b_lo'])}·收盘中位{_signed(r['b_cl'])}；收盘低于开盘{_pct(r['b_below'])}·"
            f"开盘后大面{_pct(r['b_bigloss'])}·当日涨停{_pct(r['b_lu'])}")
