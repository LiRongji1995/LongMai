"""The pre-market list (盘前清单) for 龙脉 V9: groups 主线龙头 / 连板梯队 / 观察 / 高波动, a header with
mood, regulation, mainlines, the two-axis temperature, name waves, market high-flyers and the theme
radar, and the saved run directory read by `sf show` and `sf review`.
Candidates and risk notes only: the person decides every trade.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import (baserates, crowding, intraday, longmai as lm, mainline, manifest, namethemes, notes as hype, radar, seats, store,
               strategy, temperature)


def build(observation: str | None = None, update_external: bool = True, watch_limit: int = 10,
              avoid_limit: int = 10, log=print) -> dict:
    sessions = store.calendar()
    observation = observation or store.latest_complete_session(sessions)
    target = store.next_session(sessions, observation)
    stored = sorted(x.stem for x in (store.home() / "bars").glob("*.parquet"))
    window = [s for s in stored if s <= observation][-lm.HISTORY_SESSIONS:]
    if window[-1] != observation:
        raise RuntimeError(f"bars for {observation} are not in the store")
    seat_day, seat_status = None, "not captured"
    try:
        if update_external:
            seats.capture(observation)
        seat_day = seats.day_features(observation, seats.skill_table(log=lambda *_: None))
        seat_status = "ok" if seat_day is not None else "not captured"
    except Exception as error:
        seat_status = f"FAILED: {str(error)[:100]}"
    ctx = lm.context(window, live_target=target, update_external=update_external, log=log)
    t = len(window) - 1
    p = lm.params()
    d = lm.decide(ctx, t, target, p, seat_day)
    g, leaders, pool = d["gate"], d["leaders"], d["pool"]
    sp = strategy.params()
    facts = strategy.evaluate(strategy.features(ctx["w"], t, pool, sp), sp)
    basic = ctx["basic"].drop_duplicates("code").set_index("code")
    name = basic["code_name"]
    turn_all = ctx["aw"]["turn"].iloc[t]
    close = ctx["w"]["close"].iloc[t]

    turn_rank_all = turn_all.rank(ascending=False, method="min")

    def activity_text(code, amount):
        r = turn_rank_all.get(code, np.nan)
        tv = turn_all.get(code, np.nan)
        return (f"{amount / 1e8:.1f}亿·换手{tv:.1f}%" if pd.notna(tv) else f"{amount / 1e8:.1f}亿") + \
            (f"·换手第{int(r)}" if pd.notna(r) else "")

    one_price = (ctx["w"]["high"].iloc[t] == ctx["w"]["low"].iloc[t]) & ctx["w"]["limit_up"].iloc[t].fillna(False).astype(bool)

    def liquidity(code, amount):
        out = []
        if bool(one_price.get(code, False)):
            out.append("一字板(今日几乎无换手)")
        if pd.notna(amount) and amount < 1e8:
            out.append(f"成交额仅{amount / 1e8:.1f}亿，流动性差")
        return out

    zt = mainline.concentration(ctx["ts"], t)
    noted = hype.active(observation, sessions)
    rows = []
    slots_left = g["slots"]
    for code, s in leaders.iterrows():
        stage = facts.loc[code, "stages"] if code in facts.index else ""
        role = f"空间板(市场最高板)·{int(s['height'])}板" if s["board"] == mainline.SPACE else \
            f"{s['board']}·龙{int(s['leader_rank'])}·{int(s['height'])}板" if s["height"] >= 1 else \
            f"{s['board']}·龙{int(s['leader_rank'])}·趋势"
        risk = "；".join(liquidity(code, s["amount"]) + s["vetoes"] + s["notes"])
        stop = round(float(close[code]) * (1 - p["stop_loss"]), 2)
        crowd = s.get("crowded") or []
        if crowd:
            risk = "；".join(["拥挤：" + "，".join(crowd)] + ([risk] if risk else []))
        if s["vetoes"]:
            group, plan = "高波动", f"主线龙头·{HIGH_VOL}"
        elif crowd:
            group, plan = "主线龙头", "拥挤：不列为候选，等分歧转一致再看"
        elif g["slots"] == 0:
            group, plan = "主线龙头", f"{g['mood_tier']}档情绪：规则不开新仓，只观察"
        else:
            group = "主线龙头"
            tag = "候选（由你决定）" if slots_left > 0 else "候选·超出仓位上限的备选"
            slots_left -= 1 if slots_left > 0 else 0
            plan = f"{tag}：看竞价量价与分时承接；参考止损{stop}（-{p['stop_loss']:.0%}）"
            left = ctx["severe"]["events_left"].iloc[t].get(code, 9)
            if s["board"] == mainline.SPACE:  # information only (V6 backtest, CHANGELOG 2026-11-10)
                risk = "；".join(([risk] if risk else []) + ["历史提示：空间板次日开盘追高三段均亏(每笔约-2%~-6%)"])
            if s["board"] == mainline.SPACE and left <= 2:
                risk = "；".join(([risk] if risk else []) + [f"10日偏离{ctx['severe']['dev10'].iloc[t][code]:.0%}，再触发{int(left)}次3日异动即严重异动"])
        home = radar.home_board(ctx["ts"], t, code, zt)
        role += hype.tag(code, home if home != s["board"] else None, noted)
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
    r2["act_rank"] = turn_rank_all.reindex(r2.index)
    r2 = r2.sort_values("act_rank")
    watch, avoid = [], []
    for code, f in r2.iterrows():
        v = lm.vetoes(ctx, t, code, p, seat_day)
        n = lm.notes(ctx, t, code, seat_day)
        home = radar.home_board(ctx["ts"], t, code, zt)
        role = f"非主线龙头·{f['stages']}" + hype.tag(code, home, noted) + \
            (f"·名字扩散({wave_of[code]})" if code in wave_of else "")
        if code in wave_of and not bool(ctx["aw"]["limit_up"].iloc[t].get(code, False)):
            n = n + ["名字跟风(历史次日偏弱)"]
        row = dict(名称=name.get(code, code), code=code, amount_raw=float(f["amount"]), close=float(close[code]), 角色=role,
                   成交额=activity_text(code, f["amount"]),
                   风险提示="；".join(v + n))
        if v:
            avoid.append(dict(row, 分组="高波动", 参与建议=HIGH_VOL))
        else:
            watch.append(dict(row, 分组="观察", 参与建议="只观察：不在当前前3主线或非题材内前2"))
    # 连板梯队: every main-board 2板+ stock not already a mainline leader (they hold ~half of the 妖股)
    ladder = []
    h_main = ctx["w"]["height"].iloc[t]
    pool_t = pool.reindex(h_main.index).fillna(False)
    turn_t = ctx["aw"]["turn"].iloc[t]
    for code in h_main[(h_main >= 2) & pool_t].index:
        if code in taken:
            continue
        v = lm.vetoes(ctx, t, code, p, seat_day)
        flags = lm.crowded(ctx, t, code, p)
        left = ctx["severe"]["events_left"].iloc[t].get(code, 9)
        home = radar.home_board(ctx["ts"], t, code, zt)
        risk = (["高波动：" + "，".join(v)] if v else []) + (["拥挤：" + "，".join(flags)] if flags else [])
        if left <= 2:
            risk.append(f"再触发{int(left)}次3日异动即严重异动")
        amt = float(ctx["w"]["amount"].iloc[t].get(code, np.nan))
        risk = liquidity(code, amt) + risk + lm.notes(ctx, t, code, seat_day)
        if v:
            plan = HIGH_VOL
        elif flags:
            plan = "拥挤：历史上次日续板约3-8%，5日走出来约6-15%"
        else:
            plan = "梯队候选：历史上2板+约12-21%在5日内再涨停3次（看竞价与分时，由你决定）"
        amt = float(ctx["w"]["amount"].iloc[t].get(code, np.nan))
        ladder.append(dict(分组="连板梯队", 名称=name.get(code, code), code=code, amount_raw=amt, close=float(close[code]),
                           角色=f"{int(h_main[code])}板" + hype.tag(code, home, noted, "·题材未明(需查新闻)"),
                           成交额=activity_text(code, amt), 参与建议=plan, 风险提示="；".join(risk),
                           _h=float(h_main[code]), _t=float(turn_t.get(code, 0) or 0)))
    ladder = [{k: v for k, v in r.items() if not k.startswith("_")}
              for r in sorted(ladder, key=lambda r: (-r["_h"], -r["_t"]))]
    taken |= {r["code"] for r in ladder}
    watch = [r for r in watch if r["code"] not in taken]
    avoid = [r for r in avoid if r["code"] not in taken]
    shown = set(leaders.index) | {r["code"] for r in watch[:watch_limit] + avoid[:avoid_limit]} | taken
    for wv in waves:
        for code in wv["lead_codes"]:
            if code in shown or code not in close.index:
                continue
            v = lm.vetoes(ctx, t, code, p, seat_day)
            amt = float(ctx["aw"]["amount"].iloc[t].get(code, np.nan))
            watch.append(dict(分组="高波动" if v else "观察", 名称=name.get(code, code), code=code, amount_raw=amt,
                              close=float(close[code]), 角色=f"名字扩散龙头({wv['group']})·{int(height_all.get(code, 0) or 0)}板",
                              成交额=activity_text(code, amt) if pd.notna(amt) else "-",
                              参与建议=HIGH_VOL if v else f"只观察：名字扩散不在{p['version'].rsplit('_', 1)[-1]}选股范围",
                              风险提示="；".join(v + lm.notes(ctx, t, code, seat_day))))
            shown.add(code)
    extra = [r for r in watch[watch_limit:] if r["角色"].startswith("名字扩散龙头")]
    rows += ladder + watch[:watch_limit] + extra + avoid[:avoid_limit]
    table = pd.DataFrame(rows, columns=["分组", "名称", "code", "角色", "成交额", "参与建议", "风险提示", "amount_raw", "close"])
    # observation-day intraday facts per listed stock: description only, added after every group and order is fixed
    supp = None
    if update_external:
        try:
            supp = intraday.supplement(observation, table["code"].tolist(), log=lambda *_: None)
        except Exception as error:  # reported; the stocks show as missing
            supp = dict(status=f"FAILED: {str(error)[:100]}")
    try:
        inotes, istatus, ifacts = intraday.list_notes(observation, table["code"].tolist())
    except Exception as error:  # reported in the header as missing, never silently blank
        inotes, ifacts = {c: "分时说明出错" for c in table["code"]}, pd.DataFrame()
        istatus = dict(day=observation, captured=None, error=str(error)[:100], noted=0, missing=len(table))
    istatus["supplement"] = supp
    table["昨日分时"] = table["code"].map(inotes)
    # historical base rates of the same cell (description only, after every group and order is fixed)
    btab, bmeta = baserates.latest(observation)
    if btab is None:
        table["历史同类"] = "同类统计表未生成"
        bstatus = dict(run=None)
    else:
        role = table["分组"].map(GROUP_CODES).map({"leader": "leader", "avoid": "leader", "ladder": "ladder",
                                                    "watch": "observe"})
        hgt = facts["board_height"].reindex(table["code"]).to_numpy()
        one = facts["one_price"].reindex(table["code"]).astype(float).to_numpy()
        table["历史同类"] = [baserates.describe(baserates.lookup(btab, r, h, o)) if isinstance(r, str) else ""
                         for r, h, o in zip(role, hgt, one)]
        bstatus = dict(run=bmeta["run"], cutoff=bmeta["cutoff"], version=bmeta["version"])
    lines = lm.mainline_table(ctx, t, p)
    group_of = table.set_index("code")["分组"].map(GROUP_CODES)
    facts["lm_group"] = group_of.reindex(facts.index)
    facts["name"] = name.reindex(facts.index)
    strong = ctx["aw"]["limit_up"].iloc[t].fillna(False).astype(bool) | \
        ((ctx["aw"]["close"].iloc[t] / ctx["aw"]["preclose"].iloc[t] - 1) >= 0.05)
    traded = ctx["aw"]["amount"].iloc[t].notna()
    links = namethemes.links(table["code"].tolist(), names_all, strong[strong & traded].index, traded[traded].index)
    # theme radar, high-flyers, and name links that are really a shared hot concept
    hot_boards = radar.radar(ctx["ts"], t)
    h_all = radar.heights_all(ctx["aw"]).iloc[t]
    turn_t = ctx["aw"]["turn"].iloc[t]

    def label(c):
        return name.get(c, c) + ("(创)" if c[3:5] in ("30", "68") else "")

    for hb in hot_boards:
        core = sorted(hb.pop("codes"), key=lambda c: (-float(h_all.get(c, 0)), -float(turn_t.get(c, 0) or 0)))
        hb["core"] = [f"{label(c)}{int(h_all.get(c, 0))}板" if h_all.get(c, 0) >= 2 else label(c) for c in core[:4]]
    flyers = []
    for c in radar.high_flyers(ctx["aw"], ctx["ts"], t):
        j = list(ctx["ts"]["cols"]).index(c) if c in ctx["ts"]["cols"] else None
        own = [] if j is None else [ctx["ts"]["names"][i] for i in np.flatnonzero(ctx["ts"]["M"][:, j] > 0)]
        hot_own = zt.reindex(own).dropna().sort_values(ascending=False)
        flyers.append(dict(code=c, name=label(c), height=int(h_all[c]),
                           boards=[b for b in hot_own.index[:3] if hot_own[b] >= 2.0],
                           main_board=c[3:5] not in ("30", "68"), hype=noted.get(c),
                           in_list=table.loc[table["code"] == c, "分组"].tolist()[:1]))
    code_of = {v: k for k, v in names_all.items()}
    M, cols_l = ctx["ts"]["M"], list(ctx["ts"]["cols"])
    hot_names = set(zt[zt >= 2.0].index)
    for link in links:
        if link["code"] not in cols_l:
            continue
        own = {ctx["ts"]["names"][i] for i in np.flatnonzero(M[:, cols_l.index(link["code"])] > 0)} & hot_names
        same, other, concept = [], [], None
        for n in link["partners"]:
            c = code_of.get(n)
            theirs = set() if c not in cols_l else {ctx["ts"]["names"][i] for i in np.flatnonzero(M[:, cols_l.index(c)] > 0)}
            common = own & theirs
            if common:
                concept = concept or max(common, key=lambda b: zt[b])
                same.append(n)
            else:
                other.append(n)
        if same:
            link.update(concept=concept, same=same, other=other)
    spec = crowding.spec_share(ctx["aw"])
    spec_now, spec_pct = float(spec.iloc[t]), float(spec.iloc[max(0, t - 249):t + 1].rank(pct=True).iloc[-1])
    # names to check in the news (no note yet): the 空间板, 3板+ ladder stocks and high-flyers; a mainline leader
    # already carries its mainline as the theme
    tall = ctx["w"]["height"].iloc[t]
    check = [c for c in table.loc[table["分组"].isin(["主线龙头", "连板梯队"]), "code"]
             if c not in noted and (table.loc[table["code"] == c, "角色"].iloc[0].startswith("空间板") or tall.get(c, 0) >= 3)]
    check += [f["code"] for f in flyers if f["code"] not in noted and f["code"] not in check]
    # listed stocks grouped by their noted themes ("A+B" / "A/B" count for both; brackets are explanations)
    hype_groups: dict[str, list[str]] = {}
    for c in table["code"]:
        if c in noted:
            for part in noted[c]["theme"].split("(")[0].replace("/", "+").split("+"):
                if part.strip():
                    hype_groups.setdefault(part.strip(), []).append(label(c))
    return dict(observation=observation, target=target, strategy_version=p["version"], regime=g["mood_tier"],
                hype_notes=noted, to_check=[label(c) for c in check], hype_groups=hype_groups,
                name_links=links, radar=hot_boards, high_flyers=flyers,
                temperature=temperature.reading(temperature.table(ctx["w"], ctx["aw"], ind=ctx["mood"]), t), spec_share=round(spec_now, 4), spec_pct=round(spec_pct, 3),
                generated_at=datetime.now(timezone.utc).isoformat(), gate=g,
                mainlines=lines.to_dict("records"), name_waves=[{k: v for k, v in w_.items() if k != "lead_codes"} for w_ in waves],
                params=p, seats=seat_status, manifest=manifest.build(window),
                abnormal_index_ok=bool(ctx["index_ok"]), intraday=istatus, intraday_facts=ifacts, baserates=bstatus,
                table=table, leaders=leaders, facts=facts)


def header(result: dict) -> list[str]:
    g = result["gate"]
    pos = {4: "满仓(4只×1/4)", 2: "半仓(2只×1/4)", 1: "1/4仓(1只)", 0: "空仓：规则不开新仓"}.get(g["slots"], f"{g['slots']}只×1/4")
    tag = result.get("strategy_version", lm.VERSION).rsplit("_", 1)[-1]
    lines = [f"龙脉{tag} 盘前清单  观察日 {result['observation']} → 交易日 {result['target']}",
             f"情绪：{g['mood_tier']}档（分位 {g['mood_level']}）   监管温度：{g['reg_tier']}"
             f"（深市周处置 {g['szse_cases']} 起）   规则仓位上限：{pos}",
             "说明：本系统不自动交易，以下为盯盘候选与风险提示，买卖时机和仓位由你决定。"]
    if result["mainlines"]:
        lines.append("主线：" + "；".join(f"{m['board']}（今日涨停{m['lu']}家，3日{m['lu3']}家，连续{m['days']}天，"
                                       f"成交占全市场{m.get('share', 0):.1%}）" for m in result["mainlines"]))
        hot = [m["board"] for m in result["mainlines"]
               if m.get("share_pct60") is not None and m["share_pct60"] >= crowding.HOT_PCT]
        if hot:
            lines.append(f"题材拥挤：{'、'.join(hot)} 成交占比处于60日最高位，历史上2日内退潮概率约高出一半（提示，不是规则）")
    else:
        lines.append("主线：无（没有题材当日涨停≥3家）")
    if result.get("temperature"):
        lines.append(result["temperature"]["text"])
    for wv in result.get("name_waves", []):
        lines.append(f"名字扩散：{wv['group']} 今日涨停{wv['limit_ups']}家（随机期望{wv['expected']}家，"
                     f"组内{wv['members']}只），龙头 {'、'.join(wv['leaders'])}")
    for fl in result.get("high_flyers", []):
        where = f"，所属热点：{'、'.join(fl['boards'])}" if fl["boards"] else "，所属板块都不热"
        status = f"，清单中为{fl['in_list'][0]}" if fl["in_list"] else ("，非主板不可买" if not fl["main_board"] else "，不在清单")
        why = f"炒作{fl['hype']['theme']}（新闻{fl['hype']['date'][5:]}）" if fl.get("hype") else "炒作逻辑需查新闻公告"
        lines.append(f"市场高标：{fl['name']} {fl['height']}板{where}{status}；{why}")
    if result.get("radar"):
        lines.append("题材雷达（3日涨停集中度）：" + "；".join(
            f"{r['board']} 今{r['lu']}/3日{r['lu3']}（{'、'.join(r['core'])}）" for r in result["radar"]))
    for link in result.get("name_links", []):
        if link.get("concept"):
            tail = f"；{'、'.join(link['other'])} 只是名字同「{link['key']}」" if link["other"] else ""
            lines.append(f"同概念联动：{link['name']} 与 {'、'.join(link['same'])} 同属热门概念「{link['concept']}」，"
                         f"不是名字玄学{tail}")
            continue
        lines.append(f"玄学线索：{link['name']} 与今日强势股 {'、'.join(link['partners'])} 同{link['kind']}「{link['key']}」"
                     f"（{link['k']}只，随机期望{link['expected']}）")
    groups = {k: v for k, v in result.get("hype_groups", {}).items() if len(v) >= 2}
    if groups:
        lines.append("新闻题材（清单内，来自 sf note）：" + "；".join(
            f"{k} {len(v)}只（{'、'.join(v)}）" for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))))
    if result.get("to_check"):
        lines.append(f"待查题材（无新闻记录，板块归属只是成分）：{'、'.join(result['to_check'])}；查到后用 sf note 记录")
    missing = []
    if result.get("seats", "ok") != "ok":
        state = "未采集" if result["seats"] == "not captured" else result["seats"]
        missing.append(f"龙虎榜席位{state}（拉萨散户否决与席位提示未检查）")
    if not result.get("abnormal_index_ok", True):
        missing.append("近30日指数行情不全（异动/严重异动状态未知）")
    it = result.get("intraday")
    if it is not None:
        if it.get("error"):
            missing.append(f"昨日分时说明出错（{it['error']}）")
        elif not it.get("captured"):
            missing.append(f"{it['day']} 盘中分时未采集（清单无分时说明）")
        elif it.get("missing"):
            missing.append(f"{it['missing']} 只清单股无 {it['day']} 分时（见“昨日分时”列）")
    if it is not None and it.get("captured"):
        lines.append(f"昨日分时：{it['day']} 的盘中事实，只作描述，不影响分组与排序；已采集 {it.get('sessions', '?')} 个交易日，"
                     f"不足 {intraday.MIN_SESSIONS_FOR_TEST} 日前不下结论（docs/INTRADAY_STUDY.md）")
    br = result.get("baserates")
    if br is not None and br.get("run") is None:
        missing.append("历史同类统计表未生成（运行 sf baserates）")
    elif br is not None:
        lines.append(f"历史同类：同角色·同板数·是否一字的历史结果（统计表 {br['run']}，截至 {br['cutoff']}），只作描述，"
                     "不影响分组与排序；“未涨停开时”指次日没有涨停开、能买进的那部分")
    if missing:
        lines.append("数据缺失：" + "；".join(missing))
    lines.append(f"评估方式：按选股命中率与水温判断命中率复盘（不按开盘买入盈亏），规格见 docs/STRATEGY_LONGMAI_{tag}.md；非投资建议。")
    return lines


HIGH_VOL = "高波动：最强也最危险（历史上次日续板约24-45%，大面约38-58%），只适合盘中判断"
GROUP_CODES = {"主线龙头": "leader", "连板梯队": "ladder", "观察": "watch", "高波动": "avoid", "回避": "avoid"}
VIEW = ["分组", "名称", "角色", "成交额", "参与建议", "风险提示", "昨日分时", "历史同类"]


def save(result: dict) -> Path:
    run_id = datetime.fromisoformat(result["generated_at"]).strftime("%Y%m%dT%H%M%SZ")
    out = store.home() / "lists" / result["target"] / run_id
    out.mkdir(parents=True, exist_ok=False)
    table = result["table"]
    table[VIEW].to_csv(out / "盘前清单.csv", index=False, encoding="utf-8-sig")
    (out / "盘前概况.txt").write_text("\n".join(header(result)) + "\n", encoding="utf-8")
    # picks.csv: compact per-run table (rank, list group as tier, name, role as industry, amount, close)
    picks = table.set_index("code").assign(rank=range(1, len(table) + 1), tier=table["分组"].to_numpy(),
                                           name=table["名称"].to_numpy(), industry=table["角色"].to_numpy(),
                                           amount=table["amount_raw"].to_numpy())
    picks.to_csv(out / "picks.csv", encoding="utf-8-sig")
    leaders = result["leaders"]
    if not leaders.empty:
        leaders.assign(vetoes=leaders["vetoes"].map("；".join), notes=leaders["notes"].map("；".join)).to_csv(
            out / "leaders.csv", encoding="utf-8-sig")
    result["facts"].to_parquet(out / "facts.parquet")
    ifacts = result.get("intraday_facts")
    if ifacts is not None and not ifacts.empty:
        ifacts.to_parquet(out / "intraday.parquet")  # the observation-day intraday facts behind 昨日分时
    meta = {k: v for k, v in result.items() if k not in ("table", "leaders", "facts", "intraday_facts")}
    (out / "summary.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str))
    (out.parent / "LATEST").write_text(run_id)
    return out


def latest_run(target: str | None = None) -> Path | None:
    """The newest saved run for `target` (default: the latest target session)."""
    root = store.home() / "lists"
    if not root.exists():
        return None
    targets = sorted(p.name for p in root.iterdir() if p.is_dir())
    if not targets:
        return None
    day = target or targets[-1]
    marker = root / day / "LATEST"
    if marker.exists():
        return root / day / marker.read_text().strip()
    runs = sorted(p for p in (root / day).iterdir() if p.is_dir()) if (root / day).exists() else []
    return runs[-1] if runs else None


def all_runs() -> list[Path]:
    """Latest run per target session that has a fact table, oldest first."""
    root = store.home() / "lists"
    if not root.exists():
        return []
    runs = [latest_run(p.name) for p in sorted(root.iterdir()) if p.is_dir()]
    return [r for r in runs if r is not None and (r / "facts.parquet").exists()]
