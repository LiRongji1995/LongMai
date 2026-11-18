"""Review parts of the local page: hit-rate chart and table of the latest `sf review`, the daily review report of
the observation day and the full review report (facts and historical rates only, never advice)."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import charts
from .pagetext import GROUP_CLASS, _e, zh

REVIEW_GROUPS = (("leader", "主线龙头"), ("ladder", "连板梯队"), ("watch", "观察"), ("avoid", "高波动"))
REVIEW_METRICS = (("lu1", "次日涨停", "观察日后第 1 个交易日涨停"),
                  ("bigloss1", "次日大面", "第 1 个交易日最低跌到 -7% 或收盘 -5%（相对观察日收盘）"),
                  ("max3", "3 日最高涨幅", "之后 3 个交易日里的最高价，相对观察日收盘的涨幅（平均）"),
                  ("out5", "5 日走出来", "之后 5 个交易日内至少 2 个涨停，或最高涨到 +20%"))


def _cell(part: pd.Series, key: str, base: float | None) -> tuple[str, float | None]:
    """One review cell: rate (or mean gain) with how many outcomes are known; '待定' while none is."""
    known = part.dropna()
    if known.empty:
        return f'<span class="dim">待定（0/{len(part)}）</span>', None
    value = float(known.mean())
    if key == "max3":
        text = f"{value * 100:+.1f}%"
    else:
        text = f"{value * 100:.0f}%<small> {int(known.sum())}/{len(known)}</small>"
    tone = ""
    if base is not None and key in ("lu1", "out5", "max3"):
        tone = "good" if value > base + 0.03 else "bad" if value < base - 0.03 else ""
    elif base is not None and key == "bigloss1":
        tone = "good" if value < base - 0.03 else "bad" if value > base + 0.03 else ""
    return (f'<span class="{tone}">{text}</span>' if tone else text), value


def rate_chart(rows: pd.DataFrame) -> str:
    """5-session breakout and next-day big loss per list group, next to all main-board limit-ups of the same days."""
    if rows.empty:
        return ""
    rows = rows.assign(baseline=rows["baseline"].astype(str).str.lower().eq("true"))

    def rate(part, k):
        known = part[k].dropna()
        return float(known.mean()) if len(known) else None
    base = rows[rows["baseline"]]
    items = [(label, GROUP_CLASS[label], rate(part, "out5"), rate(part, "bigloss1"), len(part))
             for key, label in REVIEW_GROUPS if len(part := rows[rows["group"] == key])]  # same rows as review_html
    return ('<h3 title="全部已存清单；虚线竖标是同日全部主板涨停股（对照）；样本少只看方向">各组命中与大面</h3>'
            + charts.rate_bars(items, (rate(base, "out5"), rate(base, "bigloss1"))))


def review_html(rows: pd.DataFrame, name: str) -> str:
    """The latest review in words: per list group, how often the listed stocks hit each outcome, next to all main-board
    limit-ups of the same days. Outcomes not yet in the data show as 待定 (never as misses)."""
    if rows.empty:
        return "<p class=dim>复盘里还没有清单。</p>"
    rows = rows.copy()
    rows["baseline"] = rows["baseline"].astype(str).str.lower().eq("true")
    targets = sorted(rows["target"].dropna().astype(str).unique())
    base_part = rows[rows["baseline"]]
    base = {k: (float(base_part[k].dropna().mean()) if base_part[k].notna().any() else None) for k, *_ in REVIEW_METRICS}
    head = "".join(f'<th title="{_e(tip)}">{_e(label)}</th>' for _, label, tip in REVIEW_METRICS)
    body = []
    for key, label in REVIEW_GROUPS + (("baseline", "对照：当天全部主板涨停股"),):
        part = base_part if key == "baseline" else rows[rows["group"] == key]
        if part.empty:
            continue
        cells = "".join(f"<td>{_cell(part[k], k, None if key == 'baseline' else base[k])[0]}</td>" for k, *_ in REVIEW_METRICS)
        chip = "" if key == "baseline" else f'<span class="chip {GROUP_CLASS[label]}">{_e(label)}</span>'
        body.append(f'<tr class="{"base" if key == "baseline" else ""}"><td>{chip or _e(label)}</td>'
                    f"<td>{len(part)}</td>{cells}</tr>")
    days = "、".join(t[5:] for t in targets)
    vline = ""
    if "strategy_version" in rows:
        per = rows.dropna(subset=["strategy_version"]).groupby("strategy_version", sort=False)["target"].nunique()
        if len(per):
            vline = ('<p class="note">按策略版本：' + "、".join(f"{_e(zh(v))} {n} 份" for v, n in per.items()) +
                     "。不同版本的规则不同，下表合在一起只能粗看；每个版本要攒够 60 份清单才单独下结论。</p>")
    legend = "".join(f"<li><b>{_e(label)}</b>：{_e(tip)}</li>" for _, label, tip in REVIEW_METRICS)
    return (f'<h3 title="{len(targets)} 份清单；绿色比对照好、红色比对照差（3 个百分点以上）">复盘表</h3>'
            f'<div class="scroll"><table class="rev"><thead><tr><th>分组</th><th>入选次数</th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>'
            f'<details class="legend"><summary>怎么读这张表</summary><ul>{legend}</ul>'
            f'<p class="note">复盘 {_e(name)}，覆盖交易日 {days}，每只股票每次入选算一次。百分比后面的小字是“命中/已有结果”；'
            f'还没走完的天数显示为待定，不算失败。绿色、红色表示比对照好或差 3 个百分点以上。</p>{vline}'
            f'<p class="note">样本还很少（几天、几十只次），只能看个方向，不能下结论。只是描述，不是买卖建议。</p></details>')


def _pct(x) -> str:
    return "待定" if x is None or (isinstance(x, float) and pd.isna(x)) else f"{float(x) * 100:.0f}%"


ROLE_ZH = {"leader": "主线龙头", "ladder": "连板梯队", "observe": "观察", "watch": "观察", "avoid": "高波动",
           "high_vol": "高波动"}
CLASS_ZH = {"deny": "竞价否定", "confirm": "高开确认", "neutral": "中性", "description": "只描述", "limit_open": "涨停开"}


def _mini(head: list[str], rows: list[list[str]]) -> str:
    return ('<div class="scroll"><table class="rev"><thead><tr>' + "".join(f"<th>{_e(h)}</th>" for h in head)
            + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
            + "</tbody></table></div>")


def _num(x, fmt: str = "{:.0f}") -> str:
    return "未知" if x is None or (isinstance(x, float) and pd.isna(x)) else fmt.format(x)


def _delta(a, b, fmt: str = "{:+.0f}") -> str:
    if a is None or b is None or (isinstance(a, float) and pd.isna(a)) or (isinstance(b, float) and pd.isna(b)):
        return ""
    return f"（较前日{fmt.format(a - b)}）"


def _rank(p) -> str:
    """A one-year percentile in words."""
    if p is None or (isinstance(p, float) and pd.isna(p)):
        return ""
    word = "极低" if p < 0.1 else "偏低" if p < 0.35 else "中等" if p <= 0.65 else "偏高" if p <= 0.9 else "极高"
    return f"，近一年分位{p * 100:.0f}%（{word}）"


def daily_report_html(summary: dict, table: pd.DataFrame) -> str:
    """The observation day's review of the short-term market, from the saved list only: market breadth and relay
    (limit-ups, promotion, failed boards, big losses), the ladder and the tall boards, mainlines, heat, threads and
    name hype, the leaders, then what to watch on the target day (facts and historical rates, never advice)."""
    obs, target = summary.get("observation", ""), summary.get("target", "")
    m = summary.get("market") or {}
    td, pv = m.get("today") or {}, m.get("prev") or {}
    tp = summary.get("temperature") or {}
    g = summary.get("gate") or {}
    out = [f'<p class="note">观察日 {_e(obs)} 收盘后的短线环境总结，和对 {_e(target)} 需要关注的地方。全部来自已保存的数据，'
           f'只是描述和历史统计，不是买卖建议。</p>']
    # 1. market
    if td:
        amt = m.get("amount")
        amt_txt = (f"两市成交 {amt / 1e12:.2f} 万亿" + (f"（较前日{(amt / m['amount_prev'] - 1) * 100:+.0f}%）"
                                                      if m.get("amount_prev") else "")) if amt else "两市成交额未知"
        items = [
            f"主板（不含 ST）涨停 {_num(td.get('lu_count'))} 家{_delta(td.get('lu_count'), pv.get('lu_count'))}，"
            f"跌停 {_num(td.get('ld_count'))} 家{_delta(td.get('ld_count'), pv.get('ld_count'))}；{amt_txt}",
            f"主板上涨家数占比 {_num(td.get('up_ratio'), '{:.0%}')}{_rank(td.get('up_ratio_pct'))}",
            f"晋级率（昨日涨停今日再涨停）{_num(td.get('promo_rate'), '{:.0%}')}{_rank(td.get('promo_rate_pct'))}",
            f"炸板率 {_num(td.get('broken_rate'), '{:.0%}')}{_rank(td.get('broken_rate_pct'))}",
            f"大面（昨日涨停今日跌 5% 以上）{_num(td.get('big_loss'))} 家{_rank(td.get('big_loss_pct'))}",
            f"昨日涨停今日平均溢价 {_num(td.get('prem_lu'), '{:+.1%}')}{_rank(td.get('prem_lu_pct'))}",
        ]
        rec = m.get("recent") or []
        if len(rec) > 1:
            items.append("近几日（主板）：" + "；".join(
                f"{_e(r['day'][5:])} 涨停{_num(r.get('lu_count'))}/跌停{_num(r.get('ld_count'))}/"
                f"晋级{_num(r.get('promo_rate'), '{:.0%}')}/大面{_num(r.get('big_loss'))}" for r in rec))
        out.append("<h3>一、市场概况</h3><ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
    elif m.get("error"):
        out.append(f'<h3>一、市场概况</h3><p class="note">市场数据出错：{_e(m["error"])}</p>')
    else:
        out.append('<h3>一、市场概况</h3><p class="note">这份清单没有保存当日市场数据（旧版清单），只能看下面几项。</p>')
    # 2. mood
    mood = [f"情绪 {_e(g.get('mood_tier', '未知'))}档（分位 {_num(g.get('mood_level'), '{:.2f}')}），"
            f"监管温度 {_e(g.get('reg_tier', '未知'))}，规则仓位上限 {_e(g.get('slots', '未知'))} 只"]
    if tp.get("text"):
        mood.append(_e(tp["text"]))
    out.append("<h3>二、情绪与水温</h3><ul>" + "".join(f"<li>{x}</li>" for x in mood) + "</ul>")
    # 3. ladder
    lad = []
    if m.get("ladder"):
        lad.append("涨停高度分布：" + "、".join(f"{k}板 {v} 家" for k, v in sorted(m["ladder"].items(),
                                                                         key=lambda kv: -int(kv[0]))))
    if m.get("tall"):
        lad.append("3 板以上：" + "、".join(f"{_e(x['name'])} {x['height']}板" for x in m["tall"]))
    for f in summary.get("high_fate") or []:
        lad.append(f"昨日高标 {_e(f['name'])} {f['height']}板 → {_e(f['fate'])}")
    if lad:
        out.append("<h3>三、连板梯队与高标</h3><ul>" + "".join(f"<li>{x}</li>" for x in lad) + "</ul>")
    # 4. themes
    th = []
    for ml in summary.get("mainlines") or []:
        th.append(f"主线 {_e(ml['board'])}：今日涨停 {ml['lu']} 家，3 日 {ml['lu3']} 家，连续 {ml['days']} 天，"
                  f"成交占全市场 {ml.get('share', 0) * 100:.1f}%")
    main_names = {ml["board"] for ml in summary.get("mainlines") or []}
    big = [h for h in (summary.get("heat") or [])[:3] if h["board"] not in main_names]
    if big:
        th.append("涨停最多但不在主线：" + "、".join(f"{_e(h['board'])}（3 日 {h['lu3']} 家）" for h in big))
    for t_ in summary.get("threads") or []:
        share = t_["today"] / t_["peak"] if t_.get("peak") else 0
        th.append(f"线索 {_e('/'.join(t_['boards'][:3]))}：第 {t_['age']} 天，今日 {t_['today']} 家，"
                  f"只剩峰值（{t_['peak']} 家）的 {share:.0%}")
    x = summary.get("xuan")
    if x:
        th.append(f"玄学（名字炒作）：第 {x['age']} 天，今日 {x['today']} 家；"
                  + "、".join(_e(gr["label"]) for gr in x.get("groups") or []))
    if th:
        out.append("<h3>四、主线与题材</h3><ul>" + "".join(f"<li>{y}</li>" for y in th) + "</ul>")
    # 5. leaders
    if table is not None and not table.empty and "分组" in table:
        lead = table[table["分组"] == "主线龙头"]
        if len(lead):
            out.append("<h3>五、龙头</h3><ul>" + "".join(
                f"<li><b>{_e(r['名称'])}</b>：{_e(r['角色'])}；{_e(str(r['成交额']).split('·')[0])}"
                + (f"；{_e(r['风险提示'])}" if isinstance(r.get("风险提示"), str) and r["风险提示"] else "") + "</li>"
                for _, r in lead.iterrows()) + "</ul>")
    # 6. outlook
    look = []
    if tp.get("loss_rate") is not None:
        look.append(f"历史上同类水温（{_e(tp.get('name', ''))}）的次日，出现亏钱效应的比例约 {tp['loss_rate']:.0%}")
    if g.get("slots") is not None:
        look.append(f"规则仓位上限 {g['slots']} 只（{_e(g.get('mood_tier', ''))}档情绪）")
    if td.get("promo_rate_pct") is not None and td["promo_rate_pct"] < 0.2:
        look.append("晋级率处在近一年低位，接力环境偏弱")
    if td.get("big_loss_pct") is not None and td["big_loss_pct"] > 0.8:
        look.append("大面家数处在近一年高位，亏钱效应还没消退")
    for t_ in summary.get("threads") or []:
        if t_.get("peak") and t_["age"] >= 5 and t_["today"] / t_["peak"] <= 0.2:
            look.append(f"{_e(t_['boards'][0])}这条线已第 {t_['age']} 天，今日只剩峰值的 {t_['today'] / t_['peak']:.0%}，处于后期")
    if table is not None and not table.empty and "风险提示" in table:
        for _, r in table.iterrows():
            note = r.get("风险提示") if isinstance(r.get("风险提示"), str) else ""
            if "严重异动" in note and r.get("分组") in ("主线龙头", "高波动"):
                look.append(f"{_e(r['名称'])} 接近严重异动（{_e([n for n in note.split('；') if '严重异动' in n][0])}）")
            if note.startswith("放量") and r.get("分组") in ("主线龙头", "高波动"):
                look.append(f"{_e(r['名称'])} {_e(note.split('；')[0])}")
    if summary.get("to_check"):
        look.append("炒作逻辑待查（没有新闻记录）：" + "、".join(_e(n) for n in summary["to_check"]))
    look.append(f"{_e(target)} 09:26 定时抓竞价快照，竞价否定标签只针对主线龙头和连板梯队")
    out.append("<h3>六、明日关注</h3><ul>" + "".join(f"<li>{y}</li>" for y in look) + "</ul>")
    return ('<details id="daily" class="legend fullrep daily"><summary>当日复盘报告（点开查看）</summary>' + "".join(out)
            + '<p class="note">以上是数据描述和历史统计，不是买卖建议；每笔交易由你自己决定。</p></details>')


def review_report_html(rev: Path) -> str:
    """The rest of the saved review (what `sf review` prints), folded below the table: coverage and 妖股 recall, the
    V9-vs-V7 forward record, base-rate calibration and the auction-label forward record. From saved files only."""
    from . import review as rv
    parts = []
    try:
        rep = json.loads((rev / "report.json").read_text())
        rec = (f"妖股召回（观察日涨停、之后 5 日再涨停 ≥3 次的股票，有多少在清单里）：{_pct(rep.get('recall'))}，"
               f"共 {rep.get('winners', 0)} 只" if rep.get("recall") is not None
               else "妖股召回：还没有走完 5 个交易日的样本，待定")
        missed = "、".join(_e(m.get("name") or m.get("code")) for m in rep.get("missed") or [])
        parts.append(f'<p class="note">本次复盘覆盖 {rep.get("lists")} 份清单（观察日 {_e(rep.get("first"))} 至 '
                     f'{_e(rep.get("last"))}）。{rec}' + (f"；漏掉 {missed}" if missed else "") + "。</p>")
    except (OSError, ValueError):
        pass
    try:
        fwd = json.loads((rev / "forward_v9_v7.json").read_text())
        tab = fwd.get("table") or {}
        rows = [[_e(zh(v)), _pct(tab.get("lu1", {}).get(v)), _pct(tab.get("bigloss1", {}).get(v)),
                 _pct(tab.get("out5", {}).get(v)), _e(int(tab.get("n", {}).get(v) or 0)), _e(tab.get("per_day", {}).get(v))]
                for v in ("V7", "V9") if v in (tab.get("n") or {})]
        if rows:
            parts.append("<h3>第9版与第7版的前向对比 <small>观察组按两版形态规则各算一遍</small></h3>"
                         + _mini(["版本", "次日涨停", "次日大面", "5 日走出来", "样本", "每天几只"], rows)
                         + f'<p class="note">{_e(fwd.get("status", ""))}</p>')
    except (OSError, ValueError):
        pass
    try:
        crep = rv.calibration_report(pd.read_csv(rev / "baserate_calibration.csv"))
        if not crep.empty:
            rows = [[_e(ROLE_ZH.get(role, zh(role)))] + [f'{_pct(r.get(f"历史_{k}"))} → {_pct(r.get(f"实际_{k}"))}'
                                                        f'<small> {int(r.get(f"n_{k}") or 0)}只</small>'
                                                        for k in ("lu1", "out5", "bigloss1")]
                    for role, r in crep.iterrows()]
            parts.append("<h3>历史同类对照 <small>清单当时给的历史比例 → 实际发生，只算已有结果的股票</small></h3>"
                         + _mini(["角色", "次日涨停", "5 日走出来", "次日大面"], rows))
    except (OSError, ValueError, KeyError):
        pass
    try:
        arep = rv.auction_forward_report(pd.read_csv(rev / "auction_forward.csv"))
        if not arep.empty:
            rows = [[_e(ROLE_ZH.get(role, zh(role))), _e(CLASS_ZH.get(cls, zh(cls))), _e(int(r["n"])), _e(int(r["days"])),
                     _pct(r["a_lu"]), _pct(r["a_bigloss"]), _pct(r["a_out5"])] for (role, cls), r in arep.iterrows()]
            parts.append("<h3>竞价标签前向记录 <small>按预注册分界点重新标注</small></h3>"
                         + _mini(["分组", "竞价标签", "样本", "天数", "当日涨停", "开盘后大面", "开盘起 5 日走出来"], rows))
    except (OSError, ValueError, KeyError):
        pass
    if not parts:
        return ""
    return ('<details class="legend fullrep"><summary>多日命中统计明细（点开查看）</summary>' + "".join(parts)
            + '<p class="note">样本少时只看方向；“待定”表示还没到能判断的日子，不算失败。</p></details>')
