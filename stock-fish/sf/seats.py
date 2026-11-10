"""Dragon-tiger list (LHB) seat detail: who bought and sold each listed stock.

Source: Eastmoney datacenter reports RPT_BILLBOARD_DAILYDETAILSBUY / ...SELL (the same endpoint AKShare's
`stock_lhb_stock_detail_em` uses), queried by date only, so one session costs two requests. History is
served back to at least 2022.

Point-in-time: LHB rows for session t are published after the close of t, so they may be used for
decisions at the open of t+1 and later, never on t itself.

Files: pools/seats/<date>.parquet, one row per (stock, seat, LHB reason). A session is stored only when
both exchanges are present; a missing (or legacy empty) file means "not captured", never "no LHB".

Seat types (by name, no future information):
  INSTITUTION  机构专用
  NORTHBOUND   沪股通专用 / 深股通专用
  RETAIL       Lhasa retail hubs (拉萨...), heavily used by retail accounts
  BROKER       any other branch (hot money, quant and ordinary accounts mixed)

Seat skill is estimated point-in-time from past outcomes only: for each seat, the average next-open
-> following-open return of stocks it net-bought, using events whose outcome was already known.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import store

URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORTS = {"buy": "RPT_BILLBOARD_DAILYDETAILSBUY", "sell": "RPT_BILLBOARD_DAILYDETAILSSELL"}
COLUMNS = {"SECURITY_CODE": "symbol", "OPERATEDEPT_CODE": "seat_code", "OPERATEDEPT_NAME": "seat",
           "EXPLANATION": "reason", "CHANGE_RATE": "pct", "ACCUM_AMOUNT": "amount", "BUY": "buy",
           "SELL": "sell", "NET": "net", "TRADE_ID": "trade_id"}


def _dir():
    path = store.home() / "pools" / "seats"
    path.mkdir(parents=True, exist_ok=True)
    return path


def path(day: str):
    return _dir() / f"{day}.parquet"


def to_code(symbol: pd.Series) -> pd.Series:
    s = symbol.astype(str).str.zfill(6)
    return pd.Series(np.select([s.str.startswith(("92", "4", "8")), s.str.startswith(("6", "9"))],
                               ["bj." + s, "sh." + s], "sz." + s), index=symbol.index)


def seat_type(name: pd.Series) -> pd.Series:
    name = name.fillna("")
    return pd.Series(np.select(
        [name.str.contains("机构专用"), name.str.contains("股通专用"), name.str.contains("拉萨")],
        ["INSTITUTION", "NORTHBOUND", "RETAIL"], "BROKER"), index=name.index)


def _report(report: str, day: str) -> pd.DataFrame:
    import requests
    rows, page = [], 1
    while True:
        params = dict(reportName=report, columns="ALL", filter=f"(TRADE_DATE='{day}')", pageNumber=page,
                      pageSize=500, sortTypes=-1, sortColumns="SECURITY_CODE", source="WEB", client="WEB")
        response = requests.get(URL, params=params, timeout=30)
        response.raise_for_status()
        result = response.json().get("result")
        if not result:  # Eastmoney returns result=null for a day without rows
            break
        rows += result["data"]
        if page >= int(result.get("pages") or 1):
            break
        page += 1
    return pd.DataFrame(rows)


def fetch(day: str) -> pd.DataFrame:
    parts = []
    for side, report in REPORTS.items():
        frame = store._retry(_report, report, day, attempts=3, pause=2.0)
        if not frame.empty:
            parts.append(frame.assign(listed_on=side))
    if not parts:
        return pd.DataFrame(columns=list(COLUMNS.values()) + ["code", "seat_type", "date", "in_buy", "in_sell"])
    raw = pd.concat(parts, ignore_index=True)
    keep = [c for c in COLUMNS if c in raw]
    frame = raw[keep + ["listed_on"]].rename(columns=COLUMNS)
    # buy/sell are part of the key: all 机构专用 seats share one seat code within a listing.
    key = ["trade_id", "seat_code", "symbol", "reason", "buy", "sell"]
    frame[["buy", "sell"]] = frame[["buy", "sell"]].fillna(0.0)
    flags = frame.pivot_table(index=key, columns="listed_on", values="net", aggfunc="size").notna()
    frame = frame.drop(columns="listed_on").drop_duplicates(key).set_index(key)
    frame["in_buy"] = flags.get("buy", pd.Series(False, index=flags.index)).reindex(frame.index).fillna(False)
    frame["in_sell"] = flags.get("sell", pd.Series(False, index=flags.index)).reindex(frame.index).fillna(False)
    frame = frame.reset_index()
    for c in ("pct", "amount", "buy", "sell", "net"):
        frame[c] = pd.to_numeric(frame[c], errors="coerce")
    frame["code"] = to_code(frame["symbol"])
    frame["seat_type"] = seat_type(frame["seat"])
    frame["date"] = day
    frame["seat_code"] = frame["seat_code"].astype(str)
    frame["trade_id"] = frame["trade_id"].astype(str)
    return frame


def is_captured(day: str) -> bool:
    """A session counts as captured only with a non-empty file. Every SSE/SZSE session has LHB listings,
    so an empty answer means "not published yet" (or a failed request), never "no LHB"; empty files
    written by earlier versions are therefore treated as not captured and retried."""
    import pyarrow.parquet as pq
    target = path(day)
    return target.exists() and pq.read_metadata(target).num_rows > 0


def capture(day: str, refresh: bool = False) -> str:
    """Fetch and store one session's seats. Raises (and stores nothing) when the answer is empty or covers
    only one exchange: Eastmoney publishes SSE and SZSE separately in the evening."""
    target = path(day)
    if is_captured(day) and not refresh:
        return "cached"
    frame = fetch(day)
    codes = frame["code"].astype(str) if "code" in frame else pd.Series(dtype=str)
    absent = [ex for ex in ("sh", "sz") if not codes.str.startswith(ex + ".").any()]
    if absent:
        raise RuntimeError(f"LHB seats for {day} incomplete (no {'/'.join(absent)} rows published yet); not stored")
    frame["captured_at"] = datetime.now(timezone.utc).isoformat()
    tmp = target.with_suffix(".tmp")
    frame.to_parquet(tmp, index=False)
    tmp.replace(target)
    return f"{len(frame)} rows"


def backfill(days: list[str], workers: int = 4, log=print) -> dict:
    todo = [d for d in days if not is_captured(d)]
    failures = {}
    start = time.time()

    def one(day):
        try:
            return day, capture(day)
        except Exception as error:  # recorded and reported; the day stays "not captured"
            return day, f"FAILED: {str(error)[:120]}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (day, status) in enumerate(pool.map(one, todo), 1):
            if status.startswith("FAILED"):
                failures[day] = status
            if i % 50 == 0 or i == len(todo):
                log(f"  seats {i}/{len(todo)} ({time.time() - start:.0f}s), failures {len(failures)}")
    return dict(requested=len(days), fetched=len(todo) - len(failures), failures=failures)


def load(day: str) -> pd.DataFrame | None:
    """None means not captured (unknown), including empty files from earlier versions."""
    return pd.read_parquet(path(day)) if is_captured(day) else None


def load_all(days: list[str] | None = None) -> pd.DataFrame:
    files = sorted(_dir().glob("*.parquet"))
    if days is not None:
        wanted = set(days)
        files = [f for f in files if f.stem in wanted]
    frames = [pd.read_parquet(f) for f in files]
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def captured_days() -> list[str]:
    return sorted(f.stem for f in _dir().glob("*.parquet") if is_captured(f.stem))


# ------------------------------------------------------------------ seat skill and daily features

def stock_seat_rows(rows: pd.DataFrame) -> pd.DataFrame:
    """The seat list of one LHB listing per (date, code).

    A stock listed for several reasons repeats its seats once per reason, so keep a single listing:
    a one-day reason when there is one (its amounts refer to that session), else the 3-day reason.
    Seats are not de-duplicated by code: all 机构专用 rows share one code but are distinct seats.
    """
    if rows.empty:
        return rows
    lists = rows.drop_duplicates(["date", "code", "trade_id"])[["date", "code", "trade_id", "reason"]].copy()
    lists["multi_day"] = lists["reason"].str.contains("三个交易日|连续", regex=True)
    chosen = lists.sort_values(["multi_day", "trade_id"]).drop_duplicates(["date", "code"])
    keep = rows.merge(chosen[["date", "code", "trade_id"]], on=["date", "code", "trade_id"])
    return keep.drop_duplicates(["date", "code", "trade_id", "seat_code", "buy", "sell"]).reset_index(drop=True)


def seat_skill(rows: pd.DataFrame, outcome: pd.Series, sessions: list[str], min_events: int = 10,
               lag: int = 2) -> pd.DataFrame:
    """Point-in-time skill per (seat, date).

    `outcome` maps (date, code) -> return from the next session's open to the open after it (the
    realistic trade for a list built from session `date`). That outcome is known at the open of
    date+2, so an event on day d is counted in the skill used on day t only if d <= t - lag.
    Returns columns date, seat_code, skill_n, skill_avg, skill_hit (values usable on `date`).
    """
    rows = stock_seat_rows(rows)
    ev = rows[rows["net"] > 0][["date", "code", "seat_code"]].copy()
    ev["r"] = outcome.reindex(pd.MultiIndex.from_frame(ev[["date", "code"]])).to_numpy()
    ev = ev.dropna(subset=["r"])
    pos = {d: i for i, d in enumerate(sessions)}
    ev["i"] = ev["date"].map(pos)
    daily = ev.groupby(["seat_code", "i"]).agg(s=("r", "sum"), n=("r", "size"), w=("r", lambda x: (x > 0).sum()))
    daily = daily.reset_index().sort_values(["seat_code", "i"])
    g = daily.groupby("seat_code")
    daily["cs"], daily["cn"], daily["cw"] = g["s"].cumsum(), g["n"].cumsum(), g["w"].cumsum()
    daily["i"] = daily["i"] + lag  # first session on which these cumulative outcomes are known
    daily = daily[daily["i"] < len(sessions)]
    daily["date"] = [sessions[i] for i in daily["i"]]
    out = daily[["date", "seat_code", "cn", "cs", "cw"]]
    out = out.assign(skill_n=out["cn"], skill_avg=out["cs"] / out["cn"], skill_hit=out["cw"] / out["cn"])
    return out[["date", "seat_code", "skill_n", "skill_avg", "skill_hit"]]


def attach_skill(rows: pd.DataFrame, skill: pd.DataFrame) -> pd.DataFrame:
    """As-of join: each seat row gets the latest skill known on its date."""
    rows = stock_seat_rows(rows).sort_values("date")
    skill = skill.sort_values("date")
    rows["_d"], skill = pd.to_datetime(rows["date"]), skill.assign(_d=pd.to_datetime(skill["date"]))
    merged = pd.merge_asof(rows.sort_values("_d"), skill.drop(columns="date").sort_values("_d"),
                           on="_d", by="seat_code", direction="backward")
    return merged.drop(columns="_d")


def stock_features(rows: pd.DataFrame, min_events: int = 10, good: float = 0.0) -> pd.DataFrame:
    """Per (date, code) LHB seat features. `rows` must already carry skill columns (attach_skill)."""
    r = rows.copy()
    r["skilled"] = (r["skill_n"] >= min_events) & (r["skill_avg"] > good) & r["seat_type"].eq("BROKER")
    r["unskilled"] = (r["skill_n"] >= min_events) & (r["skill_avg"] <= good) & r["seat_type"].eq("BROKER")
    pos_net = r["net"].clip(lower=0)
    g = r.assign(
        inst_net=r["net"].where(r["seat_type"].eq("INSTITUTION"), 0.0),
        north_net=r["net"].where(r["seat_type"].eq("NORTHBOUND"), 0.0),
        retail_net=r["net"].where(r["seat_type"].eq("RETAIL"), 0.0),
        broker_net=r["net"].where(r["seat_type"].eq("BROKER"), 0.0),
        skilled_net=r["net"].where(r["skilled"], 0.0),
        unskilled_net=r["net"].where(r["unskilled"], 0.0),
        skilled_buyer=(r["skilled"] & (r["net"] > 0)).astype(int),
        inst_buyer=(r["seat_type"].eq("INSTITUTION") & (r["net"] > 0)).astype(int),
        top_buy=pos_net,
        skill_x_net=r["skill_avg"].fillna(0) * pos_net,
    ).groupby(["date", "code"])
    f = g.agg(lhb_amount=("amount", "max"), seat_net=("net", "sum"), inst_net=("inst_net", "sum"),
              north_net=("north_net", "sum"), retail_net=("retail_net", "sum"), broker_net=("broker_net", "sum"),
              skilled_net=("skilled_net", "sum"), unskilled_net=("unskilled_net", "sum"),
              skilled_buyers=("skilled_buyer", "sum"), inst_buyers=("inst_buyer", "sum"),
              top_buy=("top_buy", "max"), skill_x_net=("skill_x_net", "sum"), pos_net=("top_buy", "sum"))
    amount = f["lhb_amount"].where(f["lhb_amount"] > 0)
    for c in ("seat_net", "inst_net", "north_net", "retail_net", "broker_net", "skilled_net", "unskilled_net"):
        f[c + "_ratio"] = f[c] / amount
    f["buy_concentration"] = f["top_buy"] / f["pos_net"].where(f["pos_net"] > 0)
    f["seat_skill"] = f["skill_x_net"] / f["pos_net"].where(f["pos_net"] > 0)
    return f.drop(columns=["skill_x_net", "pos_net", "top_buy"]).reset_index()


def top_seats(skill_rows: pd.DataFrame, rows: pd.DataFrame, asof: str, n: int = 20, min_events: int = 20) -> pd.DataFrame:
    """Seat leaderboard known on `asof` (BROKER seats with enough events)."""
    s = skill_rows[skill_rows["date"] <= asof].sort_values("date").groupby("seat_code").tail(1)
    s = s[s["skill_n"] >= min_events]
    names = rows.drop_duplicates("seat_code").set_index("seat_code")[["seat", "seat_type"]]
    s = s.join(names, on="seat_code")
    s = s[s["seat_type"].eq("BROKER")]
    return s.sort_values("skill_avg", ascending=False).head(n)[["seat", "skill_n", "skill_avg", "skill_hit"]]


# ------------------------------------------------------------------ research dataset

def outcome_panel(bars: pd.DataFrame) -> dict:
    """All A shares (SSE/SZSE): next-open -> following-open return for a list built on each session.

    r[t, code] = adj_open[t+2] / adj_open[t+1] - 1, NaN when the stock can't be bought at the t+1 open
    (suspended or opened at limit-up) or the future is not in the store yet.
    """
    code = bars["code"]
    bars = bars[code.str.startswith(("sh.", "sz."))]
    w = {c: bars.pivot(index="date", columns="code", values=c).sort_index()
         for c in ("open", "close", "preclose", "volume", "amount")}
    st = bars.pivot(index="date", columns="code", values="isST").sort_index() == "1"
    cols = w["close"].columns
    wide_board = cols.str[3:].str.startswith(("30", "68"))
    from .strategy import limit_ratio
    ratio = limit_ratio(w["close"].index, cols, st)
    up = np.floor(w["preclose"] * (1 + ratio) * 100 + 0.5) / 100
    traded = (w["volume"] > 0) & (w["close"] > 0) & (w["preclose"] > 0)
    present = w["close"].notna()
    step = (w["close"] / w["preclose"]).where(traded, 1.0)
    adj = step.where(present, 1.0).cumprod().where(present)
    adj_open = w["open"] * adj / w["close"]
    buyable = traded & (w["open"] < up - 1e-9)
    entry = adj_open.shift(-1).where(buyable.shift(-1).fillna(False).astype(bool))
    r = adj_open.shift(-2) / entry - 1
    limit_up = traded & (w["close"] >= up - 1e-9)
    gap_next = (w["open"] / w["preclose"] - 1).shift(-1)
    return dict(r=r, limit_up=limit_up, amount=w["amount"].where(traded), pct=w["close"] / w["preclose"] - 1,
                gap_next=gap_next, main=pd.Series(~wide_board, index=cols))


def dataset(min_events: int = 10, log=print) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One row per (LHB session, listed SSE/SZSE stock) with seat features and the next-open trade outcome.

    Also returns the seat skill table (point-in-time) for leaderboards.
    """
    rows = load_all()
    days = sorted(rows["date"].unique())
    stored = sorted(p.stem for p in (store.home() / "bars").glob("*.parquet"))
    bars = store.load_window(stored)
    op = outcome_panel(bars)
    long = op["r"].stack(future_stack=True).rename("r")
    long.index.names = ["date", "code"]
    skill = seat_skill(rows, long.dropna(), stored)
    feat = stock_features(attach_skill(rows, skill), min_events=min_events)
    feat = feat[feat["code"].str.startswith(("sh.", "sz."))]
    idx = pd.MultiIndex.from_frame(feat[["date", "code"]])
    amount_rank = op["amount"].rank(axis=1, ascending=False)
    for name, frame in (("r", op["r"]), ("limit_up", op["limit_up"]), ("amount", op["amount"]),
                        ("pct", op["pct"]), ("gap", op["gap_next"]), ("amount_rank", amount_rank)):
        s = frame.stack(future_stack=True)
        s.index.names = ["date", "code"]
        feat[name] = s.reindex(idx).to_numpy()
    feat["main_board"] = feat["code"].map(op["main"]).fillna(False).astype(bool)
    feat["obs"] = feat["date"]
    feat["unbuyable"] = feat["r"].isna()
    log(f"  seat dataset: {len(feat)} stock-days, {feat['date'].nunique()} sessions, "
        f"{rows['seat_code'].nunique()} seats")
    return feat, skill


def skill_table(log=print) -> pd.DataFrame:
    """Point-in-time seat skill, cached per last captured seat session."""
    last = captured_days()[-1]
    path = store.home() / "research" / f"seat_skill-{last}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _, skill = dataset(log=log)
    skill.to_parquet(path, index=False)
    return skill


def day_features(day: str, skill: pd.DataFrame) -> pd.DataFrame | None:
    """Seat features for one session's LHB stocks (index: code); None if the day is not captured."""
    rows = load(day)
    if rows is None:
        return None
    return stock_features(attach_skill(rows, skill)).set_index("code")
