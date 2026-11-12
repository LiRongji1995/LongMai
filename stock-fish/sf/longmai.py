"""龙脉 V13 (`SF_LONGMAI_V13`; V9 + the V12 liquidity floor + the V13 volume-surge note, applied by listing.py) screening: market gate, mainline leaders, risk vetoes and crowding flags.

Specification: docs/STRATEGY_LONGMAI_V9.md (on top of docs/STRATEGY_LONGMAI_V7.md). The same `context()` + `decide()` path serves the live
list (`listing.py`) and the entry-free evaluation (`evaluate.py`). Versions V1-V6 are at tag `legacy-r2`.
The system never trades; a person decides every entry and exit intraday.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import abnormal, crowding, mainline, mood, regulation, store, strategy, themes

VERSION = "SF_LONGMAI_V13"  # V13 = V12 + the volume-surge risk note on every listed stock (user decision 2026-11-12)
# V12 = V9 + the user's liquidity floor (user decision 2026-11-12, no backtest)
PARAMS = dict(
    version=VERSION,
    top_k=3, min_theme_lu3=5, overlap_max=0.6,          # mainlines (concentration over 3 sessions)
    leader_rank_max=2, space_min_height=4,              # leaders and the 空间板 class
    crowd_surge=2.0, stop_loss=0.07,                    # crowding flag; reference stop shown in the list
    severe_margin=0.30, post_severe_days=10, retail_ratio=0.02,             # vetoes (高波动)
    budget={"高": 1.0, "中": 0.5, "低": 0.0}, reg_cut=0.5, max_slots=4,       # position ceiling (display)
)
# V12 liquidity floor (user decision 2026-11-12: "in the current market only stocks trading >= 1e9 a day", imposed
# without a backtest). A stock may be listed (any group) only if, on the observation day or on at least LIQ_MIN_DAYS of
# the last LIQ_DAYS sessions up to it (the user's choice, 2026-11-12), it traded at least LIQ_MARKET_SHARE of the whole-market (SSE/SZSE A shares)
# average daily amount over the LIQ_MARKET_DAYS sessions ending that day (~1e9 at the 2026-11 level of 1.66e12), or
# closed one-price limit-up that day (no turnover because nobody would sell). The floor follows market volume.
LIQ_DAYS, LIQ_MIN_DAYS, LIQ_MARKET_SHARE, LIQ_MARKET_DAYS = 5, 2, 0.0006, 20


def liquidity_floor(ctx: dict, t: int) -> dict:
    """{ok: code -> bool, threshold: today's floor in yuan (NaN when the market average is unknown), days: count of
    qualifying sessions}. With an unknown floor every stock passes and `threshold` is NaN (reported, not imputed)."""
    amt = ctx["aw"]["amount"]
    market = amt.sum(axis=1, min_count=1)
    floor = market.rolling(LIQ_MARKET_DAYS, min_periods=LIQ_MARKET_DAYS).mean() * LIQ_MARKET_SHARE
    lo = max(0, t - LIQ_DAYS + 1)
    w = ctx["w"]
    cols = amt.columns
    one = ((w["high"] == w["low"]) & w["limit_up"].fillna(False).astype(bool)).reindex(columns=cols).fillna(False)
    big = amt.iloc[lo:t + 1].ge(floor.iloc[lo:t + 1], axis=0)
    good = big | one.iloc[lo:t + 1].astype(bool)
    days = good.sum()
    thr = float(floor.iloc[t])
    ok = (good.iloc[-1] | (days >= LIQ_MIN_DAYS)) if pd.notna(thr) else pd.Series(True, index=cols)
    return dict(ok=ok, threshold=thr, days=days)


# V13 volume-surge note (user decision 2026-11-12, after 福建水泥 traded 16.9e8 against ~4-6e8 on the days before
# with no warning): every listed stock, any group, sealed or not, gets a risk note when its observation-day amount is
# at least SURGE_RATIO times the mean of its previous SURGE_DAYS sessions. A note only: never changes a group or order.
SURGE_RATIO, SURGE_DAYS, SURGE_MIN_PRIOR = 3.0, 5, 3


def volume_surge(ctx: dict, t: int, code: str) -> list[str]:
    """The V13 risk note for one stock (empty when below the ratio; explicit when the prior amounts are unknown)."""
    amt = ctx["aw"]["amount"]
    if code not in amt.columns:
        return ["前5日成交额缺失，放量未检查"]
    today = amt.iloc[t].get(code, np.nan)
    prior = amt.iloc[max(0, t - SURGE_DAYS):t][code].dropna()
    if pd.isna(today) or len(prior) < SURGE_MIN_PRIOR or prior.mean() <= 0:
        return ["前5日成交额缺失，放量未检查"]
    ratio = float(today) / float(prior.mean())
    if ratio >= SURGE_RATIO:
        return [f"放量：成交额{today / 1e8:.1f}亿，为前{len(prior)}日均值{prior.mean() / 1e8:.1f}亿的{ratio:.1f}倍"]
    return []


HISTORY_SESSIONS = 320  # 250-session mood percentiles + warm-up


def params(overrides: dict | None = None) -> dict:
    p = {k: (dict(v) if isinstance(v, dict) else v) for k, v in PARAMS.items()}
    p.update(overrides or {})
    return p


# ------------------------------------------------------------------ shared context

def context(sessions: list[str], live_target: str | None = None, update_external: bool = False,
            seat_features: pd.DataFrame | None = None, log=print) -> dict:
    """All panels needed by `decide` for the given window of stored sessions."""
    loaded = themes.load()
    if loaded is None:
        raise FileNotFoundError("theme data missing; run `sf themes` first")
    bars = store.load_window(sessions)
    w = strategy.panel(bars)
    aw = mainline.a_share_panel(bars)
    ts = mainline.theme_stats(aw, *loaded)
    tab = mood.table(w)
    tr = w["traded"].fillna(False).astype(bool)
    pct = (w["close"] / w["preclose"] - 1).where(tr)
    idx = abnormal.index_returns(update=update_external)
    dev = abnormal.deviation(pct, idx).where(tr)
    _, trig, room = abnormal.trigger3(dev)
    sv = abnormal.severe(dev, trig)
    if update_external:
        try:
            regulation.update_szse(log=lambda *_: None)
        except Exception as error:  # the last stored report is used; stated in the summary
            log(f"  regulation update failed: {error}")
    amount = w["amount"].where(tr).sum(axis=1)
    days = list(w["close"].index)
    # Severe abnormal volatility looks back 30 sessions; a missing benchmark day inside that window makes the
    # facts unknown (the deviation would otherwise silently count as zero).
    bench = idx.reindex(columns=["sh.000001", "sz.399001"]).reindex(days)
    abnormal_unknown = bench.isna().any(axis=1).astype(float).rolling(30, min_periods=1).max().astype(bool)
    reg = regulation.daily(days + ([live_target] if live_target else []), amount)
    return dict(w=w, aw=aw, ts=ts, mood=tab, trig=trig, room=room, severe=sv, reg=reg,
                basic=store.stock_basic(), calendar=store.calendar(), seat_features=seat_features,
                index_ok=not bool(abnormal_unknown.iloc[-1]), abnormal_unknown=abnormal_unknown)


def gate(ctx: dict, t: int, target: str, p: dict) -> dict:
    tier = ctx["mood"]["tier"].iloc[t]
    level = ctx["mood"]["level"].iloc[t]
    reg_pct = ctx["reg"]["szse_per_turnover_pct"].get(target, np.nan) if len(ctx["reg"]) else np.nan
    reg_tier = "UNKNOWN" if pd.isna(reg_pct) else "高" if reg_pct > 2 / 3 else "低" if reg_pct < 1 / 3 else "中"
    budget = p["budget"].get(tier, 0.0)
    if reg_tier == "高":
        budget *= p["reg_cut"]
    slots = int(round(budget * p["max_slots"]))
    cases = ctx["reg"]["szse_cases"].get(target, np.nan) if len(ctx["reg"]) else np.nan
    return dict(mood_tier=tier, mood_level=None if pd.isna(level) else round(float(level), 3),
                reg_tier=reg_tier, reg_pct=None if pd.isna(reg_pct) else round(float(reg_pct), 3),
                szse_cases=None if pd.isna(cases) else int(cases), budget=budget, slots=slots)


def mainline_table(ctx: dict, t: int, p: dict, lookback: int = 10) -> pd.DataFrame:
    """Today's mainlines with limit-up counts and consecutive days in the top list."""
    lines = mainline.mainlines(ctx["ts"], t, p)
    day = ctx["ts"]["lu"].index[t]
    rows = []
    for board in lines:
        streak = 0
        for k in range(t, max(-1, t - lookback), -1):
            if board in mainline.mainlines(ctx["ts"], k, p):
                streak += 1
            else:
                break
        share, pct = crowding.theme_heat(ctx["ts"], t, board)
        rows.append(dict(board=board, lu=int(ctx["ts"]["lu"].loc[day, board]),
                         lu3=int(ctx["ts"]["lu3"].loc[day, board]), days=streak,
                         share=round(share, 4), share_pct60=None if pd.isna(pct) else round(pct, 3)))
    return pd.DataFrame(rows)


def vetoes(ctx: dict, t: int, code: str, p: dict, seat_day: pd.DataFrame | None) -> list[str]:
    out = []
    sv = ctx["severe"]
    if bool(sv["flag"].iloc[t].get(code, False)):
        out.append("今日达严重异动标准")
    elif 0 < sv["days_since"].iloc[t].get(code, np.inf) <= p["post_severe_days"]:
        out.append(f"严重异动后第{int(sv['days_since'].iloc[t][code])}日")
    if sv["dev10_left"].iloc[t].get(code, 1.0) < p["severe_margin"]:
        out.append(f"10日偏离{sv['dev10'].iloc[t][code]:.0%}，距严重异动不足{p['severe_margin']:.0%}")
    if seat_day is not None and code in seat_day.index and seat_day.loc[code, "retail_net_ratio"] > p["retail_ratio"]:
        out.append("拉萨散户席位大额净买")
    return out


def crowded(ctx: dict, t: int, code: str, p: dict) -> list[str]:
    """放量分歧: turnover >= crowd_surge x the stock's own previous 5-session mean without a sealed limit-up."""
    turn = ctx["aw"]["turn"]
    today = turn.iloc[t].get(code, np.nan)
    prior = turn.iloc[max(0, t - 5):t][code].mean() if code in turn else np.nan
    sealed = bool(ctx["aw"]["limit_up"].iloc[t].get(code, False))
    if pd.notna(today) and pd.notna(prior) and prior > 0 and today >= p["crowd_surge"] * prior and not sealed:
        return [f"放量分歧：换手{today:.1f}%为前5日均值{today / prior:.1f}倍且未封板"]
    return []


def notes(ctx: dict, t: int, code: str, seat_day: pd.DataFrame | None) -> list[str]:
    """Informational risk notes (not vetoes)."""
    out = []
    unknown = ctx.get("abnormal_unknown")
    if unknown is not None and bool(unknown.iloc[t]):
        out.append("异动状态未知(近30日指数行情缺失，严重异动否决未检查)")
    left = ctx["severe"]["events_left"].iloc[t].get(code, 9)
    if left == 1:
        out.append("再触发1次3日异动即严重异动")
    if seat_day is not None and code in seat_day.index:
        f = seat_day.loc[code]
        if f.get("skilled_buyers", 0) >= 1:
            out.append(f"有效游资买入{int(f['skilled_buyers'])}家")
        if f.get("inst_net_ratio", 0) > 0.02:
            out.append("机构净买")
        if f.get("seat_net_ratio", 0) < -0.02:
            out.append("榜单净卖")
    return out


def decide(ctx: dict, t: int, target: str, p: dict, seat_day: pd.DataFrame | None, pool: pd.Series | None = None) -> dict:
    """Gate info and ranked leaders (with vetoes, notes and crowding flags) for observation session t."""
    w = ctx["w"]
    if pool is None:
        pool = strategy.eligible(w, ctx["basic"], ctx["calendar"], t, strategy.params())
    g = gate(ctx, t, target, p)
    sig = mainline.signals(w, ctx["aw"], ctx["ts"], pool, t, p)
    if sig.empty:
        return dict(gate=g, leaders=sig, pool=pool)
    day = w["close"].index[t]
    sig["board_lu3"] = [np.inf if b == mainline.SPACE else float(ctx["ts"]["lu3"].loc[day, b]) for b in sig["board"]]
    sig["vetoes"] = [vetoes(ctx, t, c, p, seat_day) for c in sig.index]
    sig["notes"] = [notes(ctx, t, c, seat_day) for c in sig.index]
    sig["crowded"] = [crowded(ctx, t, c, p) for c in sig.index]
    sig = sig.sort_values(["board_lu3", "leader_rank", "turn_rank"], ascending=[False, True, True])
    return dict(gate=g, leaders=sig, pool=pool)


def seat_day(ctx: dict, day: str) -> pd.DataFrame | None:
    f = ctx["seat_features"]
    if f is None:
        return None
    part = f[f["date"] == day]
    return part.set_index("code") if len(part) else pd.DataFrame(columns=["retail_net_ratio"])
