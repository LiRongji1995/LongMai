"""Call-auction layer (竞价层) `SF_AUCTION_V1`: reads the 09:25 auction of the stocks on the saved pre-market list.

Pre-registration: docs/AUCTION_V1_PREREG.md (written before the exam ran on any data). The layer never adds
stocks: it only labels the listed ones. Until the exam adopts a label for a role (`ADOPTED`), the live output
is description only.

Decision time is 09:25 of session T. Allowed inputs: the saved list for T (built from T-1 close data) and the
auction itself (open price; auction amount only when captured before 09:30). The reference price of every
outcome is therefore T's open, the last price known at decision time (entry-free, no P&L):
  a_lu      limit-up close on T
  a_max3    highest high over T..T+2 relative to T's open
  a_out5    ">= 2 limit-ups within T..T+4, or highest high over T..T+4 >= +20%" relative to T's open
  a_bigloss on T: low <= open x 0.93 or close <= open x 0.95
Labels per role (leader = 主线龙头 + 高波动, ladder = 连板梯队, observe = 观察), from the auction gap
g = open_T / preclose_T - 1 and role-specific cut points fixed on the train segment only:
  limit_open  opened at the limit-up price (may not be buyable; its own class, never confirm/deny)
  confirm     g > upper cut (train 2/3 quantile of the role's buyable gaps)
  deny        g < lower cut (train 1/3 quantile)
  neutral     otherwise; unknown when T has no traded bar
"""
from __future__ import annotations

import json
from datetime import datetime, time as dtime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import baserates, evaluate, listing, longmai, store, strategy

VERSION = "SF_AUCTION_V1"
CUTS = ("2025-02-06", "2025-12-17")  # train / test1 / test2, as `sf evaluate`
SEGS = ("train", "test1", "test2")
ROLES = ("leader", "ladder", "observe")
ROLE_NAMES = {"leader": "主线龙头/高波动", "ladder": "连板梯队", "observe": "观察"}
LIST_ROLE = {"leader": "leader", "avoid": "leader", "ladder": "ladder", "watch": "observe"}
OUTCOMES = ("a_lu", "a_max3", "a_out5", "a_bigloss")
CLASSES = ("confirm", "neutral", "deny", "limit_open", "unknown")
CLASS_NAMES = {"confirm": "竞价确认", "neutral": "竞价中性", "deny": "竞价否定", "limit_open": "涨停开",
               "unknown": "竞价缺失"}
QUANT = (1 / 3, 2 / 3)
NEIGHBOUR_QUANT = (0.25, 0.75)  # robustness only, never used for choosing
RULE = dict(min_train=200, min_test=50, min_out5_gain=0.02, loss_share=0.5, max_loss_gain=0.02, deny_margin=0.02)
# Filled from the exam result only (procedure fixed in the pre-registration): role -> dict(cuts=(lo, hi),
# confirm=bool, deny=bool, exam=<evaluation stamp>). Empty = description only.
ADOPTED: dict[str, dict] = {
    # exam evaluations/20261112T015915 (2022-06-20 ~ 2026-11-11): only the deny label passed, for two roles
    "leader": dict(cuts=(-0.002525040015572707, 0.029760674676979775), confirm=False, deny=True, exam="20261112T015915"),
    "ladder": dict(cuts=(-0.001360788743966393, 0.03322519630030141), confirm=False, deny=True, exam="20261112T015915"),
}


def segment(day: str, cuts=CUTS) -> str:
    return "train" if day < cuts[0] else "test1" if day < cuts[1] else "test2"


# ------------------------------------------------------------------ outcomes (reference: T's open)

def auction_outcomes(w: dict) -> dict[str, pd.DataFrame]:
    """Per session T and code: gap, open_limit and the open-anchored outcomes (NaN = not traded / pending)."""
    ok = w["traded"].fillna(False).astype(bool)
    k = w["adj"] / w["close"]
    open_adj, high_adj = w["open"] * k, w["high"] * k
    lu = w["limit_up"].fillna(False).astype(bool)
    n = len(w["close"].index)

    def fwd_high(m):  # highest high over T..T+m-1, NaN until all m sessions are in the store
        mx = pd.concat([high_adj.shift(-i) for i in range(m)]).groupby(level=0).max().reindex(w["close"].index)
        return mx.where(pd.Series(np.arange(n) + m - 1 < n, index=w["close"].index), axis=0)

    max5 = fwd_high(5) / open_adj - 1
    lu5 = sum(lu.shift(-i, fill_value=False).astype(int) for i in range(5))
    return dict(
        gap=(w["open"] / w["preclose"] - 1).where(ok),
        open_limit=w["open_at_limit"].astype(float).where(ok),
        a_lu=lu.astype(float).where(ok),
        a_max3=(fwd_high(3) / open_adj - 1).where(ok),
        a_out5=((lu5 >= 2) | (max5 >= 0.20)).astype(float).where(ok & max5.notna()),
        a_bigloss=((w["low"] <= w["open"] * 0.93) | (w["close"] <= w["open"] * 0.95)).astype(float).where(ok),
    )


# ------------------------------------------------------------------ replay (the exam)

def replay(sessions: list[str], start: int = 60, log=print) -> pd.DataFrame:
    """One row per (observation t, listed stock) with its V9 role and the auction facts / outcomes of T = t+1."""
    ctx = longmai.context(sessions, log=log)
    w = ctx["w"]
    sp, lp = strategy.params(), longmai.params()  # the live V9 list
    index = list(w["close"].index)
    rows = []
    for t in range(start, len(index) - 1):
        pool = strategy.eligible(w, ctx["basic"], ctx["calendar"], t, sp)
        facts = strategy.evaluate(strategy.features(w, t, pool, sp), sp)
        leaders = set(longmai.decide(ctx, t, index[t + 1], lp, None, pool)["leaders"].index)
        h = w["height"].iloc[t]
        ladder = set(h[(h >= 2) & pool.reindex(h.index).fillna(False)].index) - leaders
        observe = set(facts.index[facts["r2_selected"].fillna(False).astype(bool)]) - leaders - ladder
        role = {**{c: "observe" for c in observe}, **{c: "ladder" for c in ladder}, **{c: "leader" for c in leaders}}
        if role:
            rows.append(pd.DataFrame({"code": list(role), "role": list(role.values()), "obs": index[t],
                                      "day": index[t + 1]}))
        if (t - start) % 100 == 0:
            log(f"  replay {index[t]} ({t - start + 1}/{len(index) - 1 - start})")
    panel = pd.concat(rows, ignore_index=True)
    for key, frame in auction_outcomes(w).items():
        s = frame.stack()
        s.index.names = ["day", "code"]
        panel = panel.join(s.rename(key), on=["day", "code"])
    for key, frame in evaluate.stock_outcomes(w).items():  # t-close outcomes, description only
        if key in ("lu1", "out5", "bigloss1"):
            s = frame.stack()
            s.index.names = ["obs", "code"]
            panel = panel.join(s.rename(f"c_{key}"), on=["obs", "code"])
    panel["seg"] = panel["day"].map(segment)
    return panel


# ------------------------------------------------------------------ labels, statistics, verdict

def classify(gap: pd.Series, open_limit: pd.Series, lo: float, hi: float) -> pd.Series:
    known = gap.notna()
    lim = open_limit.eq(1.0)
    out = np.select([~known, lim, gap > hi, gap < lo], ["unknown", "limit_open", "confirm", "deny"], "neutral")
    return pd.Series(out, index=gap.index)


def fit_cuts(panel: pd.DataFrame, q=QUANT) -> dict[str, tuple[float, float]]:
    """Role cut points from the train segment's buyable (known gap, not limit-open) rows only."""
    train = panel[(panel["seg"] == "train") & panel["gap"].notna() & ~panel["open_limit"].eq(1.0)]
    return {r: tuple(float(x) for x in train.loc[train["role"] == r, "gap"].quantile(list(q)))
            for r in ROLES if (train["role"] == r).any()}


def label(panel: pd.DataFrame, cuts: dict) -> pd.Series:
    out = pd.Series("unknown", index=panel.index)
    for r, (lo, hi) in cuts.items():
        m = panel["role"] == r
        out[m] = classify(panel.loc[m, "gap"], panel.loc[m, "open_limit"], lo, hi)
    return out


def class_stats(panel: pd.DataFrame, labels: pd.Series) -> pd.DataFrame:
    """Per role, segment and class (plus `buyable` = every known-gap, not limit-open row): n and outcome means."""
    rows = []
    for r in ROLES:
        for seg in SEGS:
            m = (panel["role"] == r) & (panel["seg"] == seg)
            parts = {c: m & labels.eq(c) for c in CLASSES}
            parts["buyable"] = m & labels.isin(["confirm", "neutral", "deny"])
            for c, sel in parts.items():
                s = panel[sel]
                rows.append(dict(role=r, seg=seg, cls=c, rows=int(sel.sum()), n=int(s["a_out5"].notna().sum()),
                                 **{k: s[k].mean() for k in OUTCOMES}))
    return pd.DataFrame(rows).set_index(["role", "seg", "cls"])


def acceptable(d_out5: float, d_loss: float, rule: dict | None = None) -> bool:
    """The user's hit-vs-big-loss rule (AGENTS.md, 2026-11-11) applied to the open-anchored outcomes."""
    rule = rule or RULE
    return bool(d_out5 >= rule["min_out5_gain"] and d_loss <= min(rule["loss_share"] * d_out5, rule["max_loss_gain"]))


def verdict(stats: pd.DataFrame, role: str, rule: dict | None = None) -> dict:
    rule = rule or RULE
    res = {}
    for cls in ("confirm", "deny"):
        reason, ok = "三段均满足", True
        for seg in SEGS:
            c, b = stats.loc[(role, seg, cls)], stats.loc[(role, seg, "buyable")]
            need = rule["min_train"] if seg == "train" else rule["min_test"]
            if c["n"] < need:
                reason, ok = f"{seg} 样本不足（{c['n']}，需 ≥ {need}）", False
                break
            d5, dl = c["a_out5"] - b["a_out5"], c["a_bigloss"] - b["a_bigloss"]
            good = acceptable(d5, dl, rule) if cls == "confirm" else \
                (d5 <= -rule["deny_margin"] or dl >= rule["deny_margin"])
            if not good:
                reason, ok = f"{seg} 不满足（走出来 {d5 * 100:+.1f}，大面 {dl * 100:+.1f} 个百分点）", False
                break
        res[cls] = dict(adopted=ok, reason=reason)
    return res


def report(panel: pd.DataFrame) -> dict:
    cuts = fit_cuts(panel)
    stats = class_stats(panel, label(panel, cuts))
    verdicts = {r: verdict(stats, r) for r in ROLES if r in cuts}
    ncuts = fit_cuts(panel, NEIGHBOUR_QUANT)
    nstats = class_stats(panel, label(panel, ncuts))
    neighbours = {r: verdict(nstats, r) for r in ROLES if r in ncuts}
    return dict(cuts=cuts, stats=stats, verdicts=verdicts, neighbour_cuts=ncuts, neighbours=neighbours,
                first=str(panel["day"].min()), last=str(panel["day"].max()), rows=len(panel))


def run(log=print) -> tuple[pd.DataFrame, dict]:
    stored = sorted(x.stem for x in (store.home() / "bars").glob("*.parquet"))
    panel = replay(stored, log=log)
    return panel, report(panel)


# ------------------------------------------------------------------ live snapshot (`sf auction`)

QUOTE_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
QUOTE_FIELDS = "f12,f14,f2,f5,f6,f17,f18"
AUCTION_END = dtime(9, 30)


def fetch_quotes(codes: list[str]) -> pd.DataFrame:
    """Latest quotes for `codes` (one batched request, bounded retry). Missing codes are simply absent."""
    import requests
    secids = ",".join(("1." if c.startswith("sh.") else "0.") + c[3:] for c in codes)

    def get():
        r = requests.get(QUOTE_URL, params=dict(fltt=2, invt=2, fields=QUOTE_FIELDS, secids=secids),
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        return r.json()
    body = store._retry(get, attempts=3, pause=1.0)
    diff = ((body or {}).get("data") or {}).get("diff") or []
    q = pd.DataFrame(diff)
    if q.empty:
        return pd.DataFrame(columns=["code", "open", "preclose", "price", "volume", "amount"])
    by6 = {c[3:]: c for c in codes}
    q = q.rename(columns={"f12": "c6", "f2": "price", "f5": "volume", "f6": "amount", "f17": "open", "f18": "preclose"})
    q["code"] = q["c6"].astype(str).map(by6)
    for c in ("price", "volume", "amount", "open", "preclose"):
        q[c] = pd.to_numeric(q[c], errors="coerce")  # the source sends "-" for no trade yet
    q.loc[~(q["open"] > 0), "open"] = np.nan
    return q[["code", "open", "preclose", "price", "volume", "amount"]].dropna(subset=["code"])


def snapshot(target: str | None = None, quotes: pd.DataFrame | None = None, now: datetime | None = None) -> dict:
    """Auction facts and labels for the saved list of `target` (default: the latest list). Never writes."""
    run_dir = listing.latest_run(target)
    if run_dir is None:
        raise FileNotFoundError("no saved pre-market list; run `sf premarket` first")
    summary = json.loads((run_dir / "summary.json").read_text())
    facts = pd.read_parquet(run_dir / "facts.parquet")
    listed = facts[facts["lm_group"].notna()]
    lu_prev = facts.index[facts["limit_up"].fillna(False).astype(bool) & facts["eligible"].fillna(False).astype(bool)]
    codes = sorted(set(listed.index) | set(lu_prev))
    now = now or store.now_sh()
    if quotes is None:
        quotes = fetch_quotes(codes)
    q = quotes.set_index("code").reindex(codes)
    in_auction = now.date().isoformat() == summary["target"] and now.time() < AUCTION_END
    preclose = q["preclose"].where(q["preclose"] > 0, facts["close"].reindex(codes))
    ratio = strategy.limit_ratio(pd.Index([summary["target"]]), pd.Index(codes),
                                 pd.DataFrame(False, index=[summary["target"]], columns=codes))[0]
    up = pd.Series(strategy._round_price(preclose.to_numpy() * (1 + ratio)), index=codes)
    gap = q["open"] / preclose - 1
    open_limit = (q["open"] >= up - 1e-9).astype(float).where(q["open"].notna())
    rows = pd.DataFrame({"name": facts["name"].reindex(codes), "group": facts["lm_group"].reindex(codes),
                         "gap": gap, "open_limit": open_limit, "open": q["open"], "preclose": preclose,
                         "auction_amount": q["amount"] if in_auction else q["amount"] * np.nan,
"prev_amount": facts["amount"].reindex(codes)})
    rows["amount_share"] = rows["auction_amount"] / rows["prev_amount"]
    rows["role"] = rows["group"].map(LIST_ROLE)
    rows["label"] = "description"
    for r, a in ADOPTED.items():
        m = rows["role"] == r
        lab = classify(rows.loc[m, "gap"], rows.loc[m, "open_limit"], *a["cuts"])
        keep = lab.isin(["limit_open", "unknown"]) | (lab.eq("confirm") & a["confirm"]) | \
            (lab.eq("deny") & a["deny"])
        rows.loc[m, "label"] = lab.where(keep, "neutral")
    rows["bands"] = ""
    btab, bmeta = baserates.latest(summary["observation"])
    if btab is not None:
        for code in listed.index:
            r = rows.loc[code]
            if pd.isna(r["gap"]) or r["open_limit"] == 1.0 or not isinstance(r["role"], str):
                continue  # bands describe buyable stocks only
            cls = "*" if r["role"] not in ADOPTED else ("deny" if r["label"] == "deny" else "other")
            rows.loc[code, "bands"] = baserates.describe_bands(baserates.lookup(
                btab, r["role"], facts.at[code, "board_height"], facts.at[code, "one_price"], cls))
    prev = rows.loc[rows.index.isin(lu_prev)]
    market = dict(prev_limit_ups=int(len(prev)), quoted=int(prev["gap"].notna().sum()),
                  mean_gap=float(prev["gap"].mean()) if prev["gap"].notna().any() else None,
                  open_limit=int(prev["open_limit"].eq(1.0).sum()), gap_le_m5=int((prev["gap"] <= -0.05).sum()))
    missing = [c for c in listed.index if pd.isna(rows.loc[c, "gap"])]
    return dict(version=VERSION, target=summary["target"], observation=summary["observation"], list_run=run_dir.name,
                list_version=summary.get("version") or summary.get("strategy_version"),
                fetched_at=now.isoformat(timespec="seconds"), in_auction=in_auction, adopted=sorted(ADOPTED),
                listed=rows.loc[listed.index], market=market, baserates=None if bmeta is None else bmeta["run"], missing=missing, quotes=quotes)


def save(snap: dict) -> Path:
    """Write once: auction/<target>/<UTC stamp>/ (snapshot.parquet, quotes.parquet, summary.json)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = store.home() / "auction" / snap["target"] / stamp
    out.mkdir(parents=True, exist_ok=False)
    snap["listed"].rename_axis("code").to_parquet(out / "snapshot.parquet")
    snap["quotes"].to_parquet(out / "quotes.parquet")
    meta = {k: v for k, v in snap.items() if k not in ("listed", "quotes")}
    (out / "summary.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str))
    return out


def lines(snap: dict) -> list[str]:
    m = snap["market"]
    head = [f"竞价层 {VERSION}  交易日 {snap['target']}（清单 {snap['list_run']}，{snap['list_version']}）  抓取 {snap['fetched_at']}",
            "说明：只对清单内股票描述竞价，不加股票；买卖由你决定，非投资建议。"]
    if not snap["adopted"]:
        head.append("状态：竞价规则尚未通过预注册检验（docs/AUCTION_V1_PREREG.md），以下只是描述，不分确认/否定。")
    else:
        head.append("标签（预注册检验 2026-11-12）：只有“竞价否定”通过，且只用于主线龙头/高波动（缺口 < -0.3%）和连板梯队"
                    "（缺口 < -0.1%）。含义：历史上 5 日走出来少约 4-7 个百分点；当日开盘后大面并不更多。"
                    "高开没有通过“竞价确认”（走出来更多，但开盘后大面多得更多）；观察组只描述。")
    if not snap["in_auction"]:
        head.append("数据缺失：抓取时间不在当日 9:30 前，竞价成交额无法单独取得（只给开盘缺口）。")
    if snap.get("baserates") is None:
        head.append("数据缺失：历史同类统计表未生成（运行 sf baserates），不显示开盘后区间")
    else:
        head.append(f"同类开盘后：历史上同角色·同板数·是否一字（及竞价否定）且没有涨停开的票，开盘后最高/最低/收盘相对开盘价的中位数"
                    f"（统计表 {snap['baserates']}）；只描述历史分布，不是买卖点位")
    if snap["missing"]:
        head.append(f"数据缺失：{len(snap['missing'])} 只没有竞价价格（停牌或源无数据）")
    if m["mean_gap"] is not None:
        head.append(f"昨日涨停 {m['prev_limit_ups']} 家，今日竞价平均 {m['mean_gap'] * 100:+.1f}%，"
                    f"涨停开 {m['open_limit']} 家，低开 ≥5% {m['gap_le_m5']} 家（只作描述）")
    return head


def table(snap: dict) -> pd.DataFrame:
    s = snap["listed"]
    group = s["group"].map({"leader": "主线龙头", "avoid": "高波动", "ladder": "连板梯队", "watch": "观察"})
    lab = s["label"].map(lambda x: "" if x == "description" else CLASS_NAMES[x])
    lab = lab.where(~s["open_limit"].eq(1.0) | (lab != ""), "涨停开")
    out = pd.DataFrame({"分组": group, "名称": s["name"],
                        "竞价缺口": s["gap"].map(lambda x: "缺失" if pd.isna(x) else f"{x * 100:+.1f}%"),
                        "竞价额(亿)": s["auction_amount"].map(lambda x: "" if pd.isna(x) else f"{x / 1e8:.2f}"),
                        "占昨日成交": s["amount_share"].map(lambda x: "" if pd.isna(x) else f"{x * 100:.1f}%"),
                        "竞价标签": lab, "同类开盘后（历史）": s["bands"]})
    order = {"主线龙头": 0, "高波动": 1, "连板梯队": 2, "观察": 3}
    return out.sort_values("分组", key=lambda g: g.map(order), kind="stable")
