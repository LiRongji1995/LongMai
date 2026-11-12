"""Judgment diary (判断日记) and manual trade log (交易记录). Local records only; never connects to a broker.

Judgments test the person's own reading of the market forward in time (no data mining of the past): each entry is
written once, with the session it is based on (`observation` = the latest closed session when it is entered) and
a checkable kind, scored later from stored bars only:

  stock_lu       the stock closes limit-up at least once within t+1..t+days        (days default 1)
  stock_bigloss  big loss on t+1 (low <= -7% or close <= -5% vs t's close, as `sf evaluate`)
  stock_out5     out5 (>= 2 limit-ups within t+1..t+5, or a high >= +20%)
  theme_fade     the board's limit-ups over t+1..t+days <= half its 3-session limit-ups at t   (days default 3)
  free           text only, not scored
`expect=False` records the opposite call ("will NOT ..."). An entry made during a trading session is based on the
previous close and flagged `intraday` (it may already see part of t+1).

Trades are typed in by the person after they happen (date, code, side, price, quantity, reason). The report pairs
buys and sells first-in first-out per stock and joins each buy to the list of that day (was it listed, which group).
Records live under SF_HOME/journal/ (judgments/, trades/), one file per entry, never edited.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

import numpy as np
import pandas as pd

from . import store

KINDS = {"stock_lu": "涨停", "stock_bigloss": "次日大面", "stock_out5": "5日走出来", "theme_fade": "题材退潮", "free": "自由记录"}
DEFAULT_DAYS = {"stock_lu": 1, "stock_bigloss": 1, "stock_out5": 5, "theme_fade": 3, "free": 0}


def _dir(name: str):
    path = store.home() / "journal" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_once(kind: str, record: dict) -> dict:
    path = _dir(kind) / f"{record['id']}.json"
    with open(path, "x", encoding="utf-8") as f:  # exclusive create: never overwrite
        json.dump(record, f, ensure_ascii=False, indent=1)
    return record


def _load(kind: str) -> pd.DataFrame:
    rows = [json.loads(p.read_text()) for p in sorted(_dir(kind).glob("*.json"))]
    return pd.DataFrame(rows)


def _new_id(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]


def resolve_stock(text: str, basic: pd.DataFrame | None = None) -> tuple[str, str]:
    """(code, name) from a code ("sh.600327", "600327") or an exact name."""
    basic = store.stock_basic() if basic is None else basic
    b = basic.drop_duplicates("code").set_index("code")["code_name"]
    t = text.strip()
    if t in b.index:
        return t, b[t]
    by6 = {c[3:]: c for c in b.index}
    if t in by6:
        return by6[t], b[by6[t]]
    hit = b[b == t]
    if len(hit) == 1:
        return hit.index[0], t
    raise ValueError(f"找不到股票：{text}")


def basis(now: datetime, sessions: list[str]) -> tuple[str, bool]:
    """The session a judgment entered at `now` is based on, and whether it was entered during a session."""
    today = str(now.date())
    if today in sessions and (now.hour, now.minute) >= (15, 0):
        return today, False
    past = [s for s in sessions if s < today]
    intraday = today in sessions and (9, 15) <= (now.hour, now.minute) < (15, 0)
    return past[-1], intraday


def add_judgment(text: str, kind: str = "free", subject: str | None = None, days: int | None = None,
                 expect: bool = True, now: datetime | None = None, sessions: list[str] | None = None,
                 basic: pd.DataFrame | None = None, boards: set[str] | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"未知类型：{kind}（可用：{'、'.join(KINDS)}）")
    now = now or store.now_sh()
    sessions = sessions if sessions is not None else store.calendar()
    obs, intraday = basis(now, sessions)
    code = name = None
    if kind.startswith("stock_"):
        if not subject:
            raise ValueError("股票类判断需要 --subject（代码或名称）")
        code, name = resolve_stock(subject, basic)
    elif kind == "theme_fade":
        if boards is None:
            from . import themes
            loaded = themes.load()
            boards = set(loaded[0]["board"]) if loaded else set()
        if subject not in boards:
            raise ValueError(f"找不到板块：{subject}（用东方财富的板块名，如 传媒）")
        name = subject
    days = DEFAULT_DAYS[kind] if days is None or kind in ("stock_bigloss", "stock_out5") else int(days)
    rec = dict(id=_new_id(now), entered_at=now.isoformat(timespec="seconds"), observation=obs, intraday=intraday,
               kind=kind, code=code, subject=name, days=days, expect=bool(expect), text=text)
    return _write_once("judgments", rec)


def judgments() -> pd.DataFrame:
    return _load("judgments")


def _board_fade(aw: dict, ts: dict, board: str, obs: str, days: int):
    lu, lu3 = ts["lu"], ts["lu3"]
    if board not in lu.columns or obs not in lu.index:
        return np.nan
    i = list(lu.index).index(obs)
    fwd = lu[board].iloc[i + 1:i + 1 + days]
    if len(fwd) < days or fwd.isna().any() or pd.isna(lu3[board].iloc[i]):
        return np.nan
    return float(fwd.sum() <= 0.5 * lu3[board].iloc[i])


def score(j: pd.DataFrame, w: dict, out: dict, ts: dict | None = None) -> pd.DataFrame:
    """One row per judgment with `happened` (1/0/NaN pending or unknown) and `result` (对/错/待定/不评分)."""
    if j.empty:
        return j
    rows = []
    lu = w["limit_up"].fillna(False).astype(bool)
    index = list(w["close"].index)
    for _, r in j.iterrows():
        happened = np.nan
        obs = r["observation"]
        if r["kind"] == "free":
            pass
        elif obs in index and r["kind"].startswith("stock_") and r["code"] in w["close"].columns:
            i = index.index(obs)
            if r["kind"] == "stock_lu":
                fwd = lu[r["code"]].iloc[i + 1:i + 1 + r["days"]]
                traded = w["traded"][r["code"]].iloc[i + 1:i + 1 + r["days"]]
                if len(fwd) == r["days"]:
                    happened = float(fwd.any()) if (traded.fillna(False).all() or fwd.any()) else np.nan
            else:
                key = "bigloss1" if r["kind"] == "stock_bigloss" else "out5"
                happened = out[key][r["code"]].loc[obs]
        elif r["kind"] == "theme_fade" and ts is not None:
            happened = _board_fade(None, ts, r["subject"], obs, int(r["days"]))
        right = np.nan if pd.isna(happened) else float(bool(happened) == bool(r["expect"]))
        result = "不评分" if r["kind"] == "free" else "待定" if pd.isna(right) else "对" if right else "错"
        rows.append(dict(r, happened=happened, right=right, result=result))
    return pd.DataFrame(rows)


def judgment_summary(s: pd.DataFrame) -> pd.DataFrame:
    scored = s[s["right"].notna()] if not s.empty else s
    if scored.empty:
        return pd.DataFrame()
    g = scored.groupby("kind").agg(n=("right", "size"), right=("right", "mean"))
    g.index = [KINDS[k] for k in g.index]
    return g.round(3)


# ------------------------------------------------------------------ trades

def add_trade(date: str, code: str, side: str, price: float, qty: int, reason: str = "",
              now: datetime | None = None, basic: pd.DataFrame | None = None) -> dict:
    if side not in ("buy", "sell"):
        raise ValueError("side 只能是 buy 或 sell")
    if not (price > 0 and qty > 0):
        raise ValueError("价格和数量必须为正")
    now = now or store.now_sh()
    code, name = resolve_stock(code, basic)
    rec = dict(id=_new_id(now), entered_at=now.isoformat(timespec="seconds"), date=date, code=code, name=name,
               side=side, price=float(price), qty=int(qty), reason=reason)
    return _write_once("trades", rec)


def trades() -> pd.DataFrame:
    return _load("trades")


def round_trips(tr: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(closed round trips, open lots), first-in first-out per code."""
    closed, open_lots = [], []
    if tr.empty:
        return pd.DataFrame(), pd.DataFrame()
    for code, g in tr.sort_values(["date", "entered_at"]).groupby("code"):
        lots: list[dict] = []
        for _, r in g.iterrows():
            if r["side"] == "buy":
                lots.append(dict(code=code, name=r["name"], buy_date=r["date"], buy_price=r["price"], qty=r["qty"],
                                 reason=r["reason"]))
                continue
            left = r["qty"]
            while left > 0 and lots:
                lot = lots[0]
                q = min(left, lot["qty"])
                closed.append(dict(code=code, name=lot["name"], buy_date=lot["buy_date"], buy_price=lot["buy_price"],
                                   sell_date=r["date"], sell_price=r["price"], qty=q, reason=lot["reason"],
                                   ret=r["price"] / lot["buy_price"] - 1))
                lot["qty"] -= q
                left -= q
                if lot["qty"] == 0:
                    lots.pop(0)
            if left > 0:
                closed.append(dict(code=code, name=r["name"], buy_date=None, buy_price=np.nan, sell_date=r["date"],
                                   sell_price=r["price"], qty=left, reason="卖出数量多于记录的买入", ret=np.nan))
        open_lots += lots
    return pd.DataFrame(closed), pd.DataFrame(open_lots)


def list_group(day: str, code: str) -> str:
    """The group of `code` in the latest list whose target is `day` ("未入清单" / "无清单")."""
    from .listing import latest_run
    run = latest_run(day) if (store.home() / "lists" / day).exists() else None
    if run is None or not (run / "facts.parquet").exists():
        return "无清单"
    f = pd.read_parquet(run / "facts.parquet", columns=["lm_group"])
    g = f["lm_group"].get(code)
    return {"leader": "主线龙头", "ladder": "连板梯队", "watch": "观察", "avoid": "高波动"}.get(g, "未入清单") \
        if isinstance(g, str) else "未入清单"


def trade_report(tr: pd.DataFrame, bars_for=None) -> dict:
    closed, open_lots = round_trips(tr)
    warnings = []
    if not tr.empty and bars_for is not None:
        for _, r in tr.iterrows():
            b = bars_for(r["date"], r["code"])
            if b is not None and not (b["low"] - 1e-6 <= r["price"] <= b["high"] + 1e-6):
                warnings.append(f"{r['date']} {r['name']} {r['side']} {r['price']} 不在当日最低{b['low']}~最高{b['high']}之间")
    if not closed.empty:
        closed["group"] = [list_group(d, c) if d else "未知" for d, c in zip(closed["buy_date"], closed["code"])]
    by_group = closed.groupby("group").agg(n=("ret", "size"), win=("ret", lambda x: float((x > 0).mean())),
                                           mean_ret=("ret", "mean")).round(4) if not closed.empty else pd.DataFrame()
    return dict(closed=closed, open=open_lots, by_group=by_group, warnings=warnings)
