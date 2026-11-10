"""龙脉 V1 (`SF_LONGMAI_V1`): mainline leaders with a market gate, risk vetoes and crowded exits.

Specification: docs/STRATEGY_LONGMAI_V1.md (frozen). The same `context()` + `decide()` code path serves the
live pre-market list and the historical backtest, so what is tested is what is used.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import abnormal, crowding, mainline, mood, namethemes, outcomes, regulation, seats, store, strategy, themes

# V4 is the version under forward test (user decision 2026-11-10); V1-V3 stay for backtest comparison only.
FORWARD_VERSION = 4
VERSION = "SF_LONGMAI_V4"
PARAMS_V1 = dict(
    version="SF_LONGMAI_V1", activity=False, veto_3day=True,
    top_k=3, min_theme_limit_ups=3, amount_rank_max=30, leader_rank_max=2,   # layers 2-3
    gap_min=0.0, churn_mult=2.0, fade_ratio=0.34, stop_loss=0.07, max_hold=10, trend_ma=0,  # layer 5
    severe_margin=0.30, post_severe_days=10, retail_ratio=0.02,             # layer 4
    budget={"高": 1.0, "中": 0.5, "低": 0.0}, reg_cut=0.5, max_slots=4,       # layer 1
)
# V2 (docs/STRATEGY_LONGMAI_V2.md): activity = amount + turnover ranks; no 3-session abnormal veto.
PARAMS_V2 = dict(PARAMS_V1, version="SF_LONGMAI_V2", activity=True, veto_3day=False)
# V3 (docs/STRATEGY_LONGMAI_V3.md): turnover identifies the leader; crowded leaders are not bought.
PARAMS = dict(PARAMS_V2, version="SF_LONGMAI_V3", crowd_turn=30.0, crowd_surge=2.0)
# V4 (docs/STRATEGY_LONGMAI_V4.md): leaders by height then turnover rate; amount only as a fixed
# threshold (today or 3-session mean >= 5亿); no 3-day veto; V3 crowding block kept.
PARAMS_V4 = dict(PARAMS_V1, version="SF_LONGMAI_V4", veto_3day=False, leader_key="turn", amount_min=5e8,
                 crowd_turn=30.0, crowd_surge=2.0)
VERSIONS = {1: PARAMS_V1, 2: PARAMS_V2, 3: PARAMS, 4: PARAMS_V4}
HISTORY_SESSIONS = 320  # 250-session mood percentiles + warm-up


def params(overrides: dict | None = None, version: int = FORWARD_VERSION) -> dict:
    base = VERSIONS[version]
    p = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
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
    reg = regulation.daily(days + ([live_target] if live_target else []), amount)
    return dict(w=w, aw=aw, ts=ts, mood=tab, trig=trig, room=room, severe=sv, reg=reg,
                basic=store.stock_basic(), calendar=store.calendar(), seat_features=seat_features,
                index_ok=set(days) <= set(idx.index))


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
    if p.get("veto_3day", True) and bool(ctx["trig"].iloc[t].get(code, False)):
        out.append("今日触发3日异动")
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
    """V3 crowding: absolute turnover rate or a surge against the stock's own previous 5 sessions."""
    if not p.get("crowd_turn"):
        return []
    turn = ctx["aw"]["turn"]
    today = turn.iloc[t].get(code, np.nan)
    prior = turn.iloc[max(0, t - 5):t][code].mean() if code in turn else np.nan
    out = []
    if pd.notna(today) and today >= p["crowd_turn"]:
        out.append(f"换手{today:.1f}%≥{p['crowd_turn']:.0f}%")
    if pd.notna(today) and pd.notna(prior) and prior > 0 and today >= p["crowd_surge"] * prior:
        out.append(f"换手为前5日均值{today / prior:.1f}倍")
    return out


def notes(ctx: dict, t: int, code: str, seat_day: pd.DataFrame | None) -> list[str]:
    """Informational risk notes (not vetoes)."""
    out = []
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
    """Layers 1-4 for observation session index t. Returns gate info and ranked leaders."""
    w = ctx["w"]
    if pool is None:
        pool = strategy.eligible(w, ctx["basic"], ctx["calendar"], t, strategy.params())
    g = gate(ctx, t, target, p)
    sig = mainline.signals(w, ctx["aw"], ctx["ts"], pool, t, p)
    if sig.empty:
        return dict(gate=g, leaders=sig, pool=pool)
    day = w["close"].index[t]
    sig["board_lu3"] = [float(ctx["ts"]["lu3"].loc[day, b]) for b in sig["board"]]
    sig["vetoes"] = [vetoes(ctx, t, c, p, seat_day) for c in sig.index]
    sig["notes"] = [notes(ctx, t, c, seat_day) for c in sig.index]
    sig["crowded"] = [crowded(ctx, t, c, p) for c in sig.index]
    key = "turn_rank" if p.get("leader_key") == "turn" else "activity_rank" if p.get("activity") else "amount_rank"
    sig = sig.sort_values(["board_lu3", "leader_rank", key], ascending=[False, True, True])
    return dict(gate=g, leaders=sig, pool=pool)


# ------------------------------------------------------------------ backtest

def _seat_day(ctx: dict, day: str) -> pd.DataFrame | None:
    f = ctx["seat_features"]
    if f is None:
        return None
    part = f[f["date"] == day]
    return part.set_index("code") if len(part) else pd.DataFrame(columns=["retail_net_ratio"])


def backtest(overrides: dict | None = None, ctx: dict | None = None, log=print, version: int = FORWARD_VERSION) -> tuple[pd.DataFrame, dict]:
    """Portfolio replay with slot limits. Returns (trades, ctx) so robustness runs can reuse ctx."""
    p = params(overrides, version)
    if ctx is None:
        stored = sorted(x.stem for x in (store.home() / "bars").glob("*.parquet"))
        feats, _ = seats.dataset(log=lambda *_: None)
        ctx = context(stored, seat_features=feats, log=log)
        ctx["pools"] = {}
    w = ctx["w"]
    index = list(w["close"].index)
    open_until: list[str] = []  # exit dates of open positions
    rows = []
    sp = strategy.params()
    start = max(strategy.MIN_HISTORY, 60)
    for t in range(start, len(index) - 1):
        day, target = index[t], index[t + 1]
        if pd.isna(ctx["mood"]["level"].iloc[t]):
            continue
        if t not in ctx["pools"]:
            ctx["pools"][t] = strategy.eligible(w, ctx["basic"], ctx["calendar"], t, sp)
        d = decide(ctx, t, target, p, _seat_day(ctx, day), ctx["pools"][t])
        open_until = [x for x in open_until if x is None or x > target]
        free = d["gate"]["slots"] - len(open_until)
        for code, s in d["leaders"].iterrows():
            base = dict(obs=day, target=target, code=code, board=s["board"], leader_rank=s["leader_rank"],
                        amount_rank=s["amount_rank"], activity_rank=s.get("activity_rank"), turn=s.get("turn"),
                        turn_rank=s.get("turn_rank"), amount=s.get("amount"),
                        height=s["height"], mood_tier=d["gate"]["mood_tier"],
                        reg_tier=d["gate"]["reg_tier"], slots=d["gate"]["slots"])
            if s["vetoes"]:
                rows.append(dict(base, status="VETO", reason="；".join(s["vetoes"])))
                continue
            if s.get("crowded"):
                rows.append(dict(base, status="CROWDED", reason="；".join(s["crowded"])))
                continue
            if free <= 0:
                rows.append(dict(base, status="NO_SLOT"))
                continue
            trade = mainline.simulate(w, ctx["ts"], t, code, s["board"], s["board_lu"], p) or {}
            rows.append(dict(base, **trade))
            if trade.get("status") in ("CLOSED", "OPEN"):
                free -= 1
                ed = trade.get("exit_date")
                open_until.append(ed if ed is not None and not pd.isna(ed) else None)
    return pd.DataFrame(rows), ctx


def report(trades: pd.DataFrame, slot_weight: float = 0.25, extra_cost: float = 0.0, split=(0.6, 0.8)) -> dict:
    if trades.empty or "status" not in trades:
        return dict(trades=0)
    closed = trades[trades["status"] == "CLOSED"].copy()
    closed["ret"] = closed["ret"] - extra_cost
    days = sorted(trades["obs"].unique())
    c1, c2 = days[int(len(days) * split[0])], days[int(len(days) * split[1])]
    closed["seg"] = np.select([closed["obs"] < c1, closed["obs"] < c2], ["train", "test1"], "test2")
    pnl = (closed.groupby("exit_date")["ret"].sum() * slot_weight).sort_index()
    equity = pnl.cumsum()
    dd = (equity - equity.cummax()).min() if len(equity) else np.nan
    out = dict(trades=len(closed), vetoed=int((trades["status"] == "VETO").sum()),
               no_slot=int((trades["status"] == "NO_SLOT").sum()),
               no_entry=int((trades["status"] == "NO_ENTRY").sum()),
               crowded=int((trades["status"] == "CROWDED").sum()),
               avg=round(closed["ret"].mean(), 4), hit=round((closed["ret"] > 0).mean(), 3),
               payoff=round(closed.loc[closed.ret > 0, "ret"].mean() / -closed.loc[closed.ret <= 0, "ret"].mean(), 2)
               if (closed.ret <= 0).any() and (closed.ret > 0).any() else np.nan,
               held=round(closed["held"].mean(), 1), total_return=round(float(equity.iloc[-1]), 4) if len(equity) else 0.0,
               max_drawdown=round(float(dd), 4) if pd.notna(dd) else np.nan)
    for name in ("train", "test1", "test2"):
        x = closed.loc[closed["seg"] == name, "ret"]
        out[f"{name}_n"] = len(x)
        out[f"{name}_avg"] = round(x.mean(), 4) if len(x) else np.nan
        out[f"{name}_t"] = round(x.mean() / x.std(ddof=1) * np.sqrt(len(x)), 2) if len(x) > 2 else np.nan
    out["exit_mix"] = closed["exit_reason"].value_counts(normalize=True).round(2).to_dict()
    out["by_year"] = closed.groupby(closed["obs"].str[:4])["ret"].agg(["size", "mean"]).round(4).to_dict("index")
    out["equity"] = equity
    return out


ROBUST = {
    "amount_rank_max": (20, 40), "leader_rank_max": (1, 3), "top_k": (2, 4), "min_theme_limit_ups": (2, 4),
    "churn_mult": (1.5, 2.5), "fade_ratio": (0.25, 0.5), "stop_loss": (0.05, 0.10), "max_hold": (5, 15),
    "severe_margin": (0.2, 0.4), "gap_min": (-0.01, 0.01),
}
ROBUST_V3 = {"crowd_turn": (25.0, 35.0), "crowd_surge": (1.5, 2.5)}
ROBUST_V4 = {"amount_min": (3e8, 8e8), "crowd_turn": (25.0, 35.0, 0), "crowd_surge": (1.5, 2.5)}


def save_backtest(trades: pd.DataFrame, rep: dict, robust: pd.DataFrame | None = None, version: int = FORWARD_VERSION) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    p = params(version=version)
    out = store.home() / "backtests" / f"{p['version']}-{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    trades.to_csv(out / "trades.csv", index=False, encoding="utf-8-sig")
    rep = dict(rep)
    eq = rep.pop("equity", None)
    if eq is not None:
        eq.rename("equity").to_csv(out / "equity.csv")
    (out / "report.json").write_text(json.dumps(dict(version=p["version"], params=p, report=rep),
                                                ensure_ascii=False, indent=2, default=str))
    if robust is not None:
        robust.to_csv(out / "robustness.csv", encoding="utf-8-sig")
    return out


# ------------------------------------------------------------------ pre-market list

def premarket(observation: str | None = None, update_external: bool = True, watch_limit: int = 10,
              avoid_limit: int = 10, log=print) -> dict:
    sessions = store.calendar()
    observation = observation or store.latest_complete_session(sessions)
    target = store.next_session(sessions, observation)
    stored = sorted(x.stem for x in (store.home() / "bars").glob("*.parquet"))
    window = [s for s in stored if s <= observation][-HISTORY_SESSIONS:]
    if window[-1] != observation:
        raise RuntimeError(f"bars for {observation} are not in the store")
    seat_day, seat_status = None, "not captured"
    try:
        seats.capture(observation)
        seat_day = seats.day_features(observation, seats.skill_table(log=lambda *_: None))
        seat_status = "ok" if seat_day is not None else "not captured"
    except Exception as error:
        seat_status = f"FAILED: {str(error)[:100]}"
    ctx = context(window, live_target=target, update_external=update_external, log=log)
    t = len(window) - 1
    p = params()
    d = decide(ctx, t, target, p, seat_day)
    g, leaders, pool = d["gate"], d["leaders"], d["pool"]
    sp = strategy.params()
    facts = strategy.evaluate(strategy.features(ctx["w"], t, pool, sp), sp)
    basic = ctx["basic"].drop_duplicates("code").set_index("code")
    name = basic["code_name"]
    amount_rank = ctx["aw"]["amount"].iloc[t].rank(ascending=False)
    _, act_rank = mainline.activity_rank(ctx["aw"], t)
    turn_all = ctx["aw"]["turn"].iloc[t]
    close = ctx["w"]["close"].iloc[t]

    by_turn = p.get("leader_key") == "turn"
    turn_rank_all = turn_all.rank(ascending=False, method="min")
    amt3_all = ctx["w"]["amount"].iloc[max(0, t - 2):t + 1].mean()

    def activity_text(code, amount):
        r = (turn_rank_all if by_turn else act_rank).get(code, np.nan)
        tv = turn_all.get(code, np.nan)
        return (f"{amount / 1e8:.1f}亿·换手{tv:.1f}%" if pd.notna(tv) else f"{amount / 1e8:.1f}亿") + \
            (f"·{'换手' if by_turn else '活跃'}第{int(r)}" if pd.notna(r) else "")

    def below_amount(code):
        a, a3 = ctx["w"]["amount"].iloc[t].get(code, np.nan), amt3_all.get(code, np.nan)
        return not ((pd.notna(a) and a >= p["amount_min"]) or (pd.notna(a3) and a3 >= p["amount_min"]))

    rows = []
    slots_left = g["slots"]
    for code, s in leaders.iterrows():
        stage = facts.loc[code, "stages"] if code in facts.index else ""
        role = f"{s['board']}·龙{int(s['leader_rank'])}·{int(s['height'])}板" if s["height"] >= 1 else \
            f"{s['board']}·龙{int(s['leader_rank'])}·趋势"
        risk = "；".join(s["vetoes"] + s["notes"])
        stop = round(float(close[code]) * (1 - p["stop_loss"]), 2)
        crowd = s.get("crowded") or []
        if crowd:
            risk = "；".join(["拥挤：" + "，".join(crowd)] + ([risk] if risk else []))
        if s["vetoes"]:
            group, plan = "回避", "主线龙头但触发风险否决，不参与"
        elif crowd:
            group, plan = "主线龙头", "拥挤不买：换手过高，等换手回落、分歧转一致再看"
        elif g["slots"] == 0:
            group, plan = "主线龙头", f"{g['mood_tier']}档情绪不开新仓，只观察"
        else:
            group = "主线龙头"
            tag = "可参与" if slots_left > 0 else "备选(仓位已满)"
            slots_left -= 1 if slots_left > 0 else 0
            plan = (f"{tag}：竞价高开才买，低开/平开放弃；仓位1/4；离场：炸板/放量滞涨/题材退潮/"
                    f"跌破{stop}/满{p['max_hold']}日，次日开盘卖")
        rows.append(dict(分组=group, 名称=name.get(code, code), code=code, amount_raw=float(s["amount"]),
                         close=float(close[code]), 角色=role + (f"·{stage}" if stage else ""),
                         成交额=activity_text(code, s["amount"]), 参与建议=plan, 风险提示=risk))
    # name contagion (informational in V1; not part of the frozen decision)
    names_all = name.reindex(ctx["aw"]["close"].columns).dropna()
    hot = namethemes.scan(ctx["aw"]["limit_up"], ctx["aw"]["amount"].notna(), namethemes.groups(names_all), t)
    height_all = ctx["w"]["height"].iloc[t]
    waves, wave_of = [], {}
    covered: set[str] = set()
    for r in hot.itertuples():
        if len(waves) >= 3:
            break
        if covered and len(set(r.codes) & covered) >= 0.7 * len(r.codes):
            continue  # same wave seen through another character / homophone / word group
        covered |= set(r.codes)
        members = namethemes.groups(names_all)[r.group]
        lead = sorted(r.codes, key=lambda c: (-float(height_all.get(c, 0) or 0), -float(ctx["aw"]["amount"].iloc[t].get(c, 0) or 0)))
        waves.append(dict(group=r.group, limit_ups=r.limit_ups, expected=r.expected, members=r.members,
                          leaders=[name.get(c, c) for c in lead[:3]], lead_codes=lead[:2]))
        for c in members:
            wave_of.setdefault(c, r.group)
    for row in rows:
        if row["code"] in wave_of:
            row["角色"] += f"·名字扩散({wave_of[row['code']]})"
    taken = set(leaders.index)
    r2 = facts[facts["r2_selected"].fillna(False).astype(bool) & ~facts.index.isin(taken)].copy()
    r2["act_rank"] = (turn_rank_all if by_turn else act_rank).reindex(r2.index)
    r2 = r2.sort_values("act_rank")
    watch, avoid = [], []
    for code, f in r2.iterrows():
        v = vetoes(ctx, t, code, p, seat_day)
        n = notes(ctx, t, code, seat_day)
        rank = f["act_rank"]
        role = f"非主线龙头·{f['stages']}" + (f"·名字扩散({wave_of[code]})" if code in wave_of else "")
        if code in wave_of and not bool(ctx["aw"]["limit_up"].iloc[t].get(code, False)):
            n = n + ["名字跟风(历史次日偏弱)"]
        row = dict(名称=name.get(code, code), code=code, amount_raw=float(f["amount"]), close=float(close[code]), 角色=role,
                   成交额=activity_text(code, f["amount"]),
                   风险提示="；".join(v + n))
        if v:
            avoid.append(dict(row, 分组="回避", 参与建议="强势形态但触发风险否决，不参与"))
        else:
            if by_turn:
                why = f"成交额未达{p['amount_min'] / 1e8:.0f}亿门槛(当日或近3日均)" if below_amount(code) else "不在当前前3主线或非题材内前2"
            else:
                why = "活跃度(成交额+换手)排名不足前30" if pd.isna(rank) or rank > p["amount_rank_max"] else "不在当前前3主线"
            watch.append(dict(row, 分组="观察", 参与建议=f"只观察：{why}"))
    shown = set(leaders.index) | {r["code"] for r in watch[:watch_limit] + avoid[:avoid_limit]}
    for wv in waves:
        for code in wv["lead_codes"]:
            if code in shown or code not in close.index:
                continue
            v = vetoes(ctx, t, code, p, seat_day)
            amt = float(ctx["aw"]["amount"].iloc[t].get(code, np.nan))
            watch.append(dict(分组="回避" if v else "观察", 名称=name.get(code, code), code=code, amount_raw=amt,
                              close=float(close[code]), 角色=f"名字扩散龙头({wv['group']})·{int(height_all.get(code, 0) or 0)}板",
                              成交额=activity_text(code, amt) if pd.notna(amt) else "-",
                              参与建议="触发风险否决，不参与" if v else f"只观察：名字扩散不在{p['version'].rsplit('_', 1)[-1]}选股范围",
                              风险提示="；".join(v + notes(ctx, t, code, seat_day))))
            shown.add(code)
    extra = [r for r in watch[watch_limit:] if r["角色"].startswith("名字扩散龙头")]
    rows += watch[:watch_limit] + extra + avoid[:avoid_limit]
    table = pd.DataFrame(rows, columns=["分组", "名称", "code", "角色", "成交额", "参与建议", "风险提示", "amount_raw", "close"])
    lines = mainline_table(ctx, t, p)
    group_of = table.set_index("code")["分组"].map({"主线龙头": "leader", "观察": "watch", "回避": "avoid"})
    facts["lm_group"] = group_of.reindex(facts.index)
    facts["name"] = name.reindex(facts.index)
    strong = ctx["aw"]["limit_up"].iloc[t].fillna(False).astype(bool) | \
        ((ctx["aw"]["close"].iloc[t] / ctx["aw"]["preclose"].iloc[t] - 1) >= 0.05)
    traded = ctx["aw"]["amount"].iloc[t].notna()
    links = namethemes.links(table["code"].tolist(), names_all, strong[strong & traded].index, traded[traded].index)
    spec = crowding.spec_share(ctx["aw"])
    spec_now, spec_pct = float(spec.iloc[t]), float(spec.iloc[max(0, t - 249):t + 1].rank(pct=True).iloc[-1])
    return dict(observation=observation, target=target, strategy_version=p["version"], regime=g["mood_tier"],
                name_links=links, spec_share=round(spec_now, 4), spec_pct=round(spec_pct, 3),
                generated_at=datetime.now(timezone.utc).isoformat(), gate=g,
                mainlines=lines.to_dict("records"), name_waves=[{k: v for k, v in w_.items() if k != "lead_codes"} for w_ in waves],
                params=p, seats=seat_status,
                abnormal_index_ok=bool(ctx["index_ok"]), table=table, leaders=leaders, facts=facts)


def header(result: dict) -> list[str]:
    g = result["gate"]
    pos = {4: "满仓(4只×1/4)", 2: "半仓(2只×1/4)", 1: "1/4仓(1只)", 0: "空仓：不开新仓"}.get(g["slots"], f"{g['slots']}只×1/4")
    tag = result.get("strategy_version", "SF_LONGMAI_V1").rsplit("_", 1)[-1]
    lines = [f"龙脉{tag} 盘前清单  观察日 {result['observation']} → 交易日 {result['target']}",
             f"情绪：{g['mood_tier']}档（分位 {g['mood_level']}）   监管温度：{g['reg_tier']}"
             f"（深市周处置 {g['szse_cases']} 起）   建议仓位：{pos}"]
    if result["mainlines"]:
        lines.append("主线：" + "；".join(f"{m['board']}（今日涨停{m['lu']}家，3日{m['lu3']}家，连续{m['days']}天，"
                                       f"成交占全市场{m.get('share', 0):.1%}）" for m in result["mainlines"]))
        hot = [m["board"] for m in result["mainlines"]
               if m.get("share_pct60") is not None and m["share_pct60"] >= crowding.HOT_PCT]
        if hot:
            lines.append(f"题材拥挤：{'、'.join(hot)} 成交占比处于60日最高位，历史上2日内退潮概率约高出一半（提示，不是规则）")
    else:
        lines.append("主线：无（没有题材当日涨停≥3家）")
    if result.get("spec_share") is not None:
        lines.append(f"短线资金：近5日涨停股成交占全市场{result['spec_share']:.1%}（近250日分位{result['spec_pct']:.0%}；"
                     "回测中对次日接力无稳定预测力，仅作背景）")
    for wv in result.get("name_waves", []):
        lines.append(f"名字扩散：{wv['group']} 今日涨停{wv['limit_ups']}家（随机期望{wv['expected']}家，"
                     f"组内{wv['members']}只），龙头 {'、'.join(wv['leaders'])}")
    for link in result.get("name_links", []):
        lines.append(f"玄学线索：{link['name']} 与今日强势股 {'、'.join(link['partners'])} 同{link['kind']}「{link['key']}」"
                     f"（{link['k']}只，随机期望{link['expected']}）")
    lines.append(f"回测说明：本策略为冻结的{tag}假设，规格与前向检验见 docs/STRATEGY_LONGMAI_{tag}.md；非投资建议。")
    return lines


VIEW = ["分组", "名称", "角色", "成交额", "参与建议", "风险提示"]


def save(result: dict) -> Path:
    run_id = datetime.fromisoformat(result["generated_at"]).strftime("%Y%m%dT%H%M%SZ")
    out = store.home() / "lists" / result["target"] / run_id
    out.mkdir(parents=True, exist_ok=False)
    table = result["table"]
    table[VIEW].to_csv(out / "盘前清单.csv", index=False, encoding="utf-8-sig")
    (out / "盘前概况.txt").write_text("\n".join(header(result)) + "\n", encoding="utf-8")
    # picks.csv keeps the columns `sf auction` reads (rank, tier, name, industry, amount, close)
    picks = table.set_index("code").assign(rank=range(1, len(table) + 1), tier=table["分组"].to_numpy(),
                                           name=table["名称"].to_numpy(), industry=table["角色"].to_numpy(),
                                           amount=table["amount_raw"].to_numpy())
    picks.to_csv(out / "picks.csv", encoding="utf-8-sig")
    leaders = result["leaders"]
    if not leaders.empty:
        leaders.assign(vetoes=leaders["vetoes"].map("；".join), notes=leaders["notes"].map("；".join)).to_csv(
            out / "leaders.csv", encoding="utf-8-sig")
    result["facts"].to_parquet(out / "facts.parquet")
    meta = {k: v for k, v in result.items() if k not in ("table", "leaders", "facts")}
    (out / "summary.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str))
    (out.parent / "LATEST").write_text(run_id)
    return out
