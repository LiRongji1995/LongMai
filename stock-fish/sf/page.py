"""Local HTML page (本地页面) for one saved pre-market list: alerts first, a one-screen dashboard, then the full tables.

Built only from files already saved (the list run, the latest auction snapshot of its target, the latest review,
judgment scores and the scheduled-job status), so it never recomputes or changes anything. Written once under
SF_HOME/pages/<target>/<stamp>.html; one self-contained file, no network, no external scripts or fonts.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import charts, listing, store
from .pagereview import daily_report_html, rate_chart, review_html, review_report_html  # noqa: F401
from .pagestyle import INDEX_CSS, TEMPLATE
from .pagetext import GROUP_CLASS, GROUPS, _e, zh  # noqa: F401

def alerts(summary: dict, header: list[str], now: datetime | None = None) -> list[str]:
    """Data gaps and failed jobs; each a plain sentence."""
    out = [h.replace("【数据缺失】", "数据缺失：") for h in header if h.startswith("【数据缺失】")]
    status_dir = store.home() / "schedule" / "status"
    if status_dir.exists():
        for p in sorted(status_dir.glob("*.json")):
            s = json.loads(p.read_text())
            if s.get("exit") not in (0, None):
                out.append(f"定时任务 {s['job']} 在 {s['finished_at'][:16].replace('T', ' ')} 失败（退出码 {s.get('exit')}），"
                           f"日志：{s.get('log')}")
    cap = store.home() / "intraday" / "capture"
    if cap.exists():
        from . import intraday
        stored = sorted(x.stem for x in (store.home() / "bars").glob("*.parquet"))[-5:]
        for day in stored:
            f = cap / f"{day}.json"
            if not f.exists():
                continue
            if not intraday.captured(day):
                out.append(f"{day} 的盘中分时没有抓到，仍在数据源约 5 个交易日的窗口内：定时任务会在 21:30 和次日 08:40 自动补抓")
            elif intraday.missing_codes(day):
                from . import minute5, sina1
                gap = intraday.missing_codes(day)
                if not set(gap) - sina1.covered(day):
                    continue  # every gap has Sina 1-minute bars (same resolution): not a data problem
                ns = len(set(gap) & sina1.covered(day))
                n5 = len((set(gap) - sina1.covered(day)) & minute5.covered(day))
                fill = "，".join(x for x in (f"{ns} 只已用新浪1分钟线补" if ns else "",
                                             f"{n5} 只已用5分钟线粗补" if n5 else "") if x)
                out.append(f"{day} 的盘中分时还缺 {len(gap)} 只东方财富 1 分钟线"
                           + (f"（其中 {fill}）" if fill else "")
                           + "，仍在 5 日窗口内：定时任务会在 21:30 和次日 08:40 自动补抓")
    return out


def _latest(path: Path) -> Path | None:
    if not path.exists():
        return None
    items = sorted(p for p in path.iterdir() if p.is_dir())
    return items[-1] if items else None


# ------------------------------------------------------------------ small visual pieces (inline SVG / CSS only)

def spark(series: list[int], width: int = 220, height: int = 46) -> str:
    """Daily bars of a thread's limit-ups; the peak and the latest day are highlighted."""
    if not series:
        return ""
    n, top = len(series), max(max(series), 1)
    bw = width / n
    peak = series.index(max(series))
    bars = []
    for i, x in enumerate(series):
        h = max(2.0, (height - 6) * x / top)
        cls = "last" if i == n - 1 else "peak" if i == peak else ""
        bars.append(f'<rect class="{cls}" x="{i * bw + 1:.1f}" y="{height - h:.1f}" width="{max(bw - 2, 1):.1f}" '
                    f'height="{h:.1f}" rx="1.5"><title>第{i + 1}天 {x}家</title></rect>')
    return f'<svg class="spark" viewBox="0 0 {width} {height}" preserveAspectRatio="none">{"".join(bars)}</svg>'


def posbar(vs_hi) -> str:
    """How far below its one-year high a stock is (full bar = at the high)."""
    if vs_hi is None or pd.isna(vs_hi):
        return '<div class="pos unknown"></div>'
    fill = max(0.0, min(1.0, 1 + float(vs_hi)))
    return f'<div class="pos"><span style="width:{fill * 100:.0f}%"></span></div>'


GROUP_CSS = {"leader": "g-lead", "ladder": "g-ladder", "watch": "g-watch", "avoid": "g-risk"}


def history(summary: dict, n: int = 20) -> list[dict]:
    """Per observation day up to this list's: mood, heat, pain (from the saved lists) and limit-up / limit-down counts,
    promotion and failed-board rates (the lists' market facts, older days from this list's `market.recent`)."""
    days: dict[str, dict] = {}
    root = store.home() / "lists"
    targets = sorted(p.name for p in root.iterdir() if p.is_dir() and p.name < summary["target"]) if root.exists() else []
    for t in targets[-n:]:
        run = listing.latest_run(t)
        try:
            s = json.loads((run / "summary.json").read_text()) if run is not None else None
        except (OSError, ValueError):
            s = None
        if s:
            days[s["observation"]] = _facts_of(s)
    days[summary["observation"]] = _facts_of(summary)
    for r in (summary.get("market") or {}).get("recent") or []:  # market facts of earlier days not on a saved list
        d = days.setdefault(r["day"], {})
        for k, src in (("lu", "lu_count"), ("ld", "ld_count"), ("promo", "promo_rate"), ("broken", "broken_rate")):
            if d.get(k) is None:
                d[k] = r.get(src)
    return [dict(day=d, **days[d]) for d in sorted(days)[-n:]]


def _facts_of(s: dict) -> dict:
    g, tp = s.get("gate") or {}, s.get("temperature") or {}
    td = (s.get("market") or {}).get("today") or {}
    return dict(mood=g.get("mood_level"), heat=tp.get("heat"), pain=tp.get("pain"), lu=td.get("lu_count"),
                ld=td.get("ld_count"), promo=td.get("promo_rate"), broken=td.get("broken_rate"))


def minute_bars(day: str, codes: list[str]) -> dict[str, tuple[pd.DataFrame, str]]:
    """The observation day's bars of `codes` from the stored layers, best first: Eastmoney 1-minute, Sina 1-minute,
    BaoStock 5-minute (bar end times mapped to trading minutes). Codes with none are left out."""
    from . import intraday, minute5, sina1
    out: dict[str, tuple[pd.DataFrame, str]] = {}
    for loader, label in ((intraday.load, "1分钟线"), (sina1.load, "新浪1分钟线")):
        try:
            bars = loader(day)
        except Exception:  # a damaged layer: the next one is tried
            bars = None
        if bars is not None:
            for c, g in bars[bars["code"].isin([c for c in codes if c not in out])].groupby("code"):
                out[c] = (g[["minute", "close", "volume"]], label)
    try:
        b5 = minute5.load(day)
    except Exception:
        b5 = None
    if b5 is not None:
        for c, g in b5[b5["code"].isin([c for c in codes if c not in out])].groupby("code"):
            m = g["bar"].map(lambda i: 5 * (i + 1) if i < 24 else 120 + 5 * (i - 23))
            out[c] = (g.assign(minute=m)[["minute", "close", "volume"]], "5分钟线（粗）")
    return out


def up_limits(day: str, codes: list[str]) -> dict[str, tuple[float, float]]:
    """(previous close, limit-up price) of `codes` on `day` from the stored daily bars."""
    from .strategy import _round_price, limit_ratio
    try:
        d = pd.read_parquet(store.bar_path(day)).drop_duplicates("code").set_index("code")
    except OSError:
        return {}
    have = [c for c in codes if c in d.index]
    if not have:
        return {}
    st = d.loc[have, "isST"].astype(str).eq("1").to_numpy()[None, :]
    ratio = limit_ratio([day], have, st)[0]
    pre = d.loc[have, "preclose"].astype(float).to_numpy()
    return {c: (p, float(u)) for c, p, u in zip(have, pre, _round_price(pre * (1 + ratio)))}


def _table(frame: pd.DataFrame, cols: list[str], pos: dict | None = None) -> str:
    cols = [c for c in cols if c in frame.columns]
    head = "".join(f"<th>{_e(c)}</th>" for c in cols)
    rows = []
    for _, r in frame.iterrows():
        cells = []
        for c in cols:
            v = _e(r[c])
            if c == "位置" and pos is not None:
                v += posbar(pos.get(r.get("名称")))
            if c == "名称":
                v = f"<b>{v}</b>"
            cells.append(f"<td>{v}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    width = max(560, 150 * len(cols))
    return (f'<div class="scroll"><table style="min-width:{width}px"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def _notes(notes: list[str]) -> str:
    """The numbered notes of listing.fold_repeats under a table."""
    return "" if not notes else '<p class="note">' + "<br>".join(_e(n) for n in notes) + "</p>"


def thread_dialog(key: str, th: dict) -> str:
    """A dialog with the thread's day-by-day record, newest day first: the mainline boards linked that day, how many of
    the thread's stocks closed limit-up and which ones (with their streak; stocks on this list are marked)."""
    rows = []
    is_name = th.get("kind") in ("name", "xuan")
    peak = max((len(d["lu"]) for d in th["days"]), default=0) or 1
    for d in reversed(th["days"]):
        stocks = "、".join(f'<span class="{"in" if x.get("listed") else ""}">{_e(x["name"])}'
                          + (f'<i>{x["height"]}板</i>' if x["height"] > 1 else "") + "</span>" for x in d["lu"])
        boards = "、".join(_e(b) for b in d["boards"]) or \
            f'<span class="dim">{("前奏（统计未确认）" if d.get("prelude") else "未达异常") if is_name else "不在前 3 主线"}</span>'
        bar = f'<span class="bar"><span style="width:{len(d["lu"]) / peak * 100:.0f}%"></span></span>'
        rows.append(f'<tr><td>{_e(d["day"][5:])}<small> 第{d["age"]}天</small></td><td>{boards}</td>'
                    f'<td class="n">{len(d["lu"])}{bar}</td><td class="lus">{stocks or "<span class=dim>无</span>"}</td></tr>')
    title = "玄学：名字炒作" if is_name else \
        "/".join(th["boards"][:3]) + ("等" if len(th["boards"]) > 3 else "")
    return (f'<dialog id="{key}" class="tdlg"><div class="dh"><div><span class="age">第{th["age"]}天</span> '
            f'<b>{_e(title)}</b><div class="dim">{_e(th["first"])} 起 · '
            f'{"包括 " + _e("、".join(th["boards"])) if is_name else "涉及板块 " + _e("、".join(th["boards"]))} · '
            f'共 {th["stocks"]} 只股票</div></div><button class="x" aria-label="关闭">×</button></div>'
            f'<div class="scroll"><table><thead><tr><th>日期</th><th>{"当天异常的名字组" if is_name else "当天的主线板块"}</th><th>涨停家数</th>'
            f'<th>涨停股（几连板；<span class="in">高亮</span>=在本清单里）</th></tr></thead><tbody>{"".join(rows)}</tbody>'
            f'</table></div><p class="note">只作描述，不改分组。'
            + ("名字组按股票名字里同一个字、词或谐音划分；“未达异常”的日子，组内股票仍可能涨停，只是没多到不像巧合。" if is_name else "“不在前 3 主线”的日子，线索里的股票仍可能涨停。")
            + '</p></dialog>')


# Short card tags for the risk notes (display only: the saved list and the table keep the full sentences, which also
# show on hover). Order matters: the first pattern that matches a note names it. Tone: warn = a risk, info = a fact,
# unk = something that could not be checked.
RISK_TAGS = (
    (r"前\d+日成交额缺失", lambda m, t: "放量未检查", "unk"),
    (r"放量分歧", lambda m, t: "放量分歧", "warn"),
    (r"放量：.*?的([\d.]+)倍", lambda m, t: f"放量{float(m.group(1)):.1f}倍", "warn"),
    (r"有效游资买入(\d+)家", lambda m, t: f"游资买入{m.group(1)}家", "info"),
    (r"一字板", lambda m, t: "一字无换手", "info"),
    (r"榜单净卖", lambda m, t: "榜单净卖", "warn"),
    (r"机构净买", lambda m, t: "机构净买", "info"),
    (r"流动性差", lambda m, t: "流动性差", "warn"),
    (r"追高.*均亏", lambda m, t: "追高历史均亏", "warn"),
    (r"拉萨散户", lambda m, t: "散户席位净买", "warn"),
    (r"严重异动", lambda m, t: "近严重异动", "warn"),
    (r"名字跟风", lambda m, t: "名字跟风", "warn"),
)


def risk_tags(text: str) -> str:
    """The risk notes of one stock as small tags; an unknown note keeps its first words (up to 8 characters)."""
    out = []
    for note in (n.strip() for n in re.split(r"[；;]", text or "") if n.strip()):
        for pattern, label, tone in RISK_TAGS:
            m = re.search(pattern, note)
            if m:
                out.append((label(m, note), tone, note))
                break
        else:
            short = re.split(r"[：:（(，,]", note)[0][:8]
            out.append((short, "warn", note))
    return "".join(f'<span class="rk {tone}" title="{_e(full)}">{_e(lab)}</span>' for lab, tone, full in out)


def _stock_card(r: pd.Series, vs_hi, chart: str = "", position: str = "", common: set | None = None) -> str:
    """One stock: role, intraday chart, amount and turnover, position, plan (left out when it is the shared plan shown
    once above the cards) and risk notes."""
    risk = f'<div class="risks">{risk_tags(r["风险提示"])}</div>' if r.get("风险提示") else ""
    where = position or (f'<div class="kv">{_e(r.get("位置", ""))}</div>{posbar(vs_hi)}')
    amount = "·".join(str(r.get("成交额", "")).split("·")[:2])  # amount and turnover; ranks stay in the table
    plan = "" if common and r["参与建议"] in common else f'<div class="plan">{_e(r["参与建议"])}</div>'
    return (f'<article class="stock"><div class="sh"><span class="nm">{_e(r["名称"])}</span>'
            f'<span class="chip {GROUP_CLASS.get(r["分组"], "")}">{_e(r["分组"])}</span>'
            f'<span class="kv amt" title="{_e(r.get("成交额", ""))}">{_e(amount)}</span></div>'
            f'<div class="role">{_e(r["角色"])}</div>{chart}{where}{plan}{risk}</article>')


def render(run: Path) -> str:
    summary = json.loads((run / "summary.json").read_text())
    header = listing.header(summary)
    table = pd.read_csv(run / "盘前清单.csv", dtype=str).fillna("")
    pos, facts = {}, pd.DataFrame()
    if (run / "facts.parquet").exists():
        facts = pd.read_parquet(run / "facts.parquet")
        if "vs_high250" in facts and "name" in facts:
            pos = dict(zip(facts["name"], facts["vs_high250"]))
    picks = pd.read_csv(run / "picks.csv", dtype=str) if (run / "picks.csv").exists() else pd.DataFrame(columns=["code", "名称"])
    code_of = dict(zip(picks["名称"], picks["code"]))
    target, obs = summary["target"], summary["observation"]
    al = alerts(summary, header)
    g = summary["gate"]
    tp = summary.get("temperature") or {}
    focus = table[table["参与建议"].str.startswith("候选（由你决定）")]
    risky = table[(table["分组"] == "高波动") | table["角色"].str.startswith("空间板")]

    nav = "".join(f'<a href="#{k}"><i></i>{v}</a>' for k, v in (
        ("dash", "总览"), ("threads", "题材线索"), ("focus", "候选"), ("auction", "竞价"), ("groups", "完整清单"),
        ("review", "复盘"), ("daily", "当日复盘报告")))
    hop = (f'<div class="hop"><a href="../index.html" title="页面目录">← 目录</a>'
           f'<a href="../go/prev-{_e(target)}.html" title="前一个有页面的交易日">‹ 前一天</a>'
           f'<a href="../go/next-{_e(target)}.html" title="后一个有页面的交易日">后一天 ›</a></div>')
    parts = [f'<div class="top"><div class="brand"><span class="dot"></span>龙脉</div>{hop}</div>'
             f'<aside class="side" aria-label="本页目录"><div class="side-t">本页目录</div>{nav}</aside>'
             f'<button class="side-btn" aria-label="本页目录">目录</button>',
             f'<header class="hero"><div class="date">{_e(target)}</div><div class="meta">盘前清单 · 观察日 {_e(obs)} · '
             f'版本 {_e(run.name)} · {_e(summary.get("strategy_version", ""))}</div>'
             + (f'<div class="tag">{_e(tp.get("name"))}</div>' if tp.get("name") else "") + '</header>']
    if al:
        parts.append('<section class="alert"><h2><span class="pulse"></span>先看这里：数据和任务问题</h2><ul>' +
                     "".join(f"<li>{_e(a)}</li>" for a in al) + "</ul></section>")
    else:
        parts.append('<section class="ok">数据完整，定时任务没有失败记录。</section>')

    slots = int(g.get("slots") or 0)
    dots = "".join(f'<i class="{"on" if i < slots else ""}"></i>' for i in range(4))
    trend = {"升": "↑", "降": "↓"}.get(tp.get("trend"), "")
    hist = history(summary)

    def series(key):
        return [(h["day"], h.get(key)) for h in hist]

    def val(x, fmt):
        return "?" if x is None or (isinstance(x, float) and pd.isna(x)) else fmt.format(x)
    td = (summary.get("market") or {}).get("today") or {}
    parts.append(
        '<div id="dash" class="sec-h"><h2>总览</h2></div><section class="dash kpis">'
        + charts.trend_tile("情绪分位", val(g.get("mood_level"), "{:.0%}"), f"{g.get('mood_tier', '?')}档",
                            series("mood"), "t-mood", lo=0, hi=1, key="kd-mood")
        + charts.trend_tile("热度", val(tp.get("heat"), "{:.0%}"), f"较前3日{tp.get('trend', '?')}{trend}",
                            series("heat"), "t-heat", lo=0, hi=1, key="kd-temp")
        + charts.trend_tile("亏钱效应", val(tp.get("pain"), "{:.0%}"), tp.get("cell", ""), series("pain"), "t-pain", lo=0, hi=1,
                            key="kd-temp")
        + charts.trend_tile("涨停家数", val(td.get("lu_count"), "{:.0f}"), f"跌停 {val(td.get('ld_count'), '{:.0f}')} 家",
                            series("lu"), "t-lu", fmt="{:.0f}家", key="kd-lu")
        + charts.trend_tile("晋级率", val(td.get("promo_rate"), "{:.0%}"), f"炸板率 {val(td.get('broken_rate'), '{:.0%}')}",
                            series("promo"), "t-pro", key="kd-pro")
        + f'<div class="tile"><div class="tl">规则仓位上限</div><div class="slots">{dots}</div>'
          f'<div class="tv">{slots} 只 × 1/4</div><div class="tl">监管温度 {_e(g.get("reg_tier"))}'
          f'（深市周处置 {_e(g.get("szse_cases"))} 起）</div></div>'
        + '</section>')
    parts.append(  # the large charts behind the overview tiles
        charts.trend_dialog("kd-mood", "情绪分位（近一年分位，越高越热）", [("情绪分位", "t-mood", series("mood"))], lo=0, hi=1,
                            note="情绪档位按这个分位划分；规则仓位上限由情绪和监管温度共同决定。")
        + charts.trend_dialog("kd-temp", "水温：热度与亏钱效应", [("热度", "t-heat", series("heat")),
                                                         ("亏钱效应", "t-pain", series("pain"))], lo=0, hi=1,
                              note="两个分数都是近一年的分位。热度高、亏钱低是暖；亏钱高是痛。")
        + charts.trend_dialog("kd-lu", "涨停与跌停家数（主板，不含 ST）", [("涨停", "t-lu", series("lu")), ("跌停", "t-ld", series("ld"))],
                              fmt="{:.0f}")
        + charts.trend_dialog("kd-pro", "晋级率与炸板率", [("晋级率", "t-pro", series("promo")), ("炸板率", "t-brk", series("broken"))],
                              lo=0, hi=1, note="晋级率：昨日涨停今日再涨停的比例；炸板率：盘中触及涨停但收盘没封住的比例。"))
    if slots == 0:
        parts.append('<section class="alert slim"><b>规则空仓</b>：今天只观察，不开新仓。</section>')
    brief = []
    if tp.get("text"):  # the full sentence stays in the tooltip and in 表头原文
        src = "、".join(re.sub(r"\(.*?\)", "", x) for x in tp.get("sources") or [])
        quiet = '<span class="rk unk" title="这一格历史上次日亏钱效应与平时相同">对次日无区分度</span>' \
            if tp.get("signal") is False else ""
        brief.append(f'<div title="{_e(tp["text"])}"><span class="bk">水温</span>{_e(tp.get("cell", ""))}'
                     f'（{_e(tp.get("name", ""))}）{quiet}' + (f'<span class="dim"> · 压力来自{_e(src)}</span>' if src else "") + "</div>")
    fw = summary.get("forward")
    if fw:
        names = (("leader", "龙头"), ("ladder", "梯队"), ("watch", "观察"))
        rows = fw.get("current") or fw.get("all") or {}
        base = rows.get("baseline", {}).get("bigloss1")
        cells = " · ".join(f"{n} {rows[k]['bigloss1']:.0%}" for k, n in names
                           if k in rows and rows[k]["bigloss1"] == rows[k]["bigloss1"])
        if cells:
            brief.append(f'<div title="{_e(listing.forward_line(fw))}"><span class="bk">次日大面</span>{_e(cells)}'
                         + (f'<span class="dim"> ｜ 同日全部涨停 {base:.0%}</span>' if base == base and base is not None else "")
                         + f'<span class="dim"> · {"本版 " + str(fw["current_lists"]) if fw.get("current") else "全部 " + str(fw.get("lists") or "")} 份清单</span></div>')
    if brief:
        parts.append(f'<section class="brief">{"".join(brief)}</section>')

    cards, dialogs = [], []
    name_th = [(summary["xuan"], True)] if summary.get("xuan") else []
    for i, (th, is_name) in enumerate(name_th + [(th, False) for th in summary.get("threads") or []]):
        share = th["today"] / th["peak"] if th.get("peak") else 0
        lead = (f'<div class="kv">清单内 {_e("、".join(th["listed"]))}</div>'
                f'<div class="kv">{_e(th["listed"][0])}：{_e(th.get("lead_position"))}</div>') if th.get("listed") else ""
        has_days = bool(th.get("days"))
        if has_days:
            dialogs.append(thread_dialog(f"th{i}", th))
        if is_name:
            title = "名字炒作"
            live = "<span class=live>今日活跃</span>" if th.get("mainline_today") else ""
            o, top = th.get("origin"), th.get("prelude_top")
            origin = (f'<div class="kv">起点：<b>{_e(o["name"])}</b> {_e(o["day"][5:])} 起 {o["height"]} 连板'
                      + (f' · 前奏最高 <b>{_e(top["name"])}</b> {top["height"]} 连板（{_e(top["day"][5:])}～{_e(top["end"][5:])}）'
                         if top and top["code"] != o["code"] else "")
                      + f'<br><span class="dim">{_e(th["confirmed"][5:])} 统计确认，之前是前奏</span></div>') if o else ""
            extra = origin + "".join(
                f'<div class="xg"><b>{_e(g["label"])}</b><span>20日涨停 {g["window_limit_ups"]} 次（随机约 '
                f'{g["window_expected"]:.0f}）· 今日 {g["today"]}</span></div>' for g in th.get("groups") or [])
        else:
            title = f'{_e("/".join(th["boards"][:3]))}{"等" if len(th["boards"]) > 3 else ""}'
            live = "<span class=live>今日主线</span>" if th.get("mainline_today") else ""
            extra = ""
        cls = "thread name" if is_name else "thread"
        cards.append(
            (f'<article class="{cls} open" data-d="th{i}" tabindex="0" role="button" title="点开看每天的详情">'
             if has_days else f'<article class="{cls}">') +
            f'<div class="th">{"<span class=xuan>玄学</span>" if is_name else ""}<span class="age">第{th["age"]}天{"+" if th.get("age_capped") else ""}</span>'
            f'<span class="nm">{title}</span>{live}</div>'
            f'{spark(th.get("series") or [])}'
            f'<div class="kv">今日 <b>{th["today"]}</b> 家 · 峰值 {th["peak"]} 家（第{th["peak_age"]}天） · '
            f'剩 {share * 100:.0f}%</div>{extra}{lead}'
            + ('<div class="more">每天详情 →</div>' if has_days else "") + '</article>')
    fates = summary.get("high_fate") or []
    fate_html = "".join(f'<div class="fate {"up" if f.get("sealed") else "down"}"><b>{_e(f["name"])}</b>'
                        f'<span>{f["height"]}板 → {_e(f["fate"])}</span></div>' for f in fates)
    mainl = "".join(f'<div class="ml"><b>{_e(m["board"])}</b><span>今{m["lu"]} · 3日{m["lu3"]} · 连续{m["days"]}天 · '
                    f'成交占比{m.get("share", 0) * 100:.1f}%</span></div>' for m in summary.get("mainlines") or [])
    waves = "".join(f'<div class="ml wave"><b>{_e(w["group"].replace("字·", "名字带「").replace("词·", "名字带「"))}」</b>'
                    f'<span>今日涨停 {w["limit_ups"]} 家（随机约 {w["expected"]:.2f} 家，组内 {w["members"]} 只）· 龙头 '
                    f'{_e("、".join(w.get("leaders") or []))}</span></div>' for w in summary.get("name_waves") or [])
    links = "".join(f'<p class="note">同概念联动：{_e(l["name"])} 与 {_e("、".join(l["same"]))} 同属「{_e(l["concept"])}」，'
                    f'不只是名字；{_e("、".join(l["other"]))} 只是名字同「{_e(l["key"])}」。</p>'
                    for l in summary.get("name_links") or [] if l.get("same"))
    if name_th:  # tracked across days: shown as cards beside the theme threads
        waves = ""
    wave_html = (f'<h3>名字扩散（玄学）<small>按股票名字里的同一个字或词统计，板块成分里没有这类题材</small></h3>'
                 f'<div class="mls">{waves}</div>{links}') if waves else (links if name_th else "")
    main_names = {m["board"] for m in summary.get("mainlines") or []}
    heat_rows = "".join(
        f'<tr><td><b>{_e(h["board"])}</b>{" <span class=live>主线</span>" if h["board"] in main_names else ""}</td>'
        f'<td class="hn">{h["lu3"]}</td><td class="hn">{h["lu"]}</td>'
        f'<td class="hn">{h.get("streak", "")}{"+" if h.get("streak_capped") else ""}</td>'
        f'<td class="hs">{spark(h.get("series") or [], 160, 30)}</td>'
        f'<td class="hn">{h["n"]}</td><td class="hn">{h["share"] * 100:.1f}%</td>'
        f'<td class="hn">{"" if h.get("z") is None else format(h["z"], ".2f")}</td></tr>' for h in summary.get("heat") or [])
    heat_html = (f'<p class="note">按近 3 日涨停家次排序；主线按“涨停是否集中”选，会压低成分股多的大题材，两者对照看。</p>'
                 f'<div class="scroll"><table class="heat"><colgroup><col class="c-b"><col class="c-n"><col class="c-n">'
                 f'<col class="c-n"><col class="c-s"><col class="c-n"><col class="c-n"><col class="c-n"></colgroup>'
                 f'<thead><tr><th>板块</th><th class="hn">3日涨停</th><th class="hn">今日涨停</th>'
                 f'<th class="hn" title="连续几个交易日在热度榜前 8">在榜天数</th><th class="hs">近 20 日每天涨停</th>'
                 f'<th class="hn">成分股</th><th class="hn">涨停/成分股</th>'
                 f'<th class="hn" title="涨停集中度分，主线按它排序">集中度</th></tr></thead>'
                 f'<tbody>{heat_rows}</tbody></table></div>') if heat_rows else ""
    srows, sdays = [], set()
    for i, (th, is_name) in enumerate(name_th + [(th, False) for th in summary.get("threads") or []]):
        counts = {d["day"]: len(d["lu"]) for d in th.get("days") or []}
        sdays |= set(counts)
        srows.append(dict(name="名字炒作" if is_name else "/".join(th["boards"][:2]) + ("等" if len(th["boards"]) > 2 else ""),
                          age=th["age"], live=th.get("mainline_today"), xuan=is_name, counts=counts,
                          key=f"th{i}" if th.get("days") else None))
    strip = charts.thread_strip(srows, sorted(d for d in sdays if d <= obs)[-15:])
    ladder_html = ""
    if not facts.empty and {"limit_up", "board_height", "name"} <= set(facts.columns):
        lu = facts[facts["limit_up"].fillna(False).astype(bool)
                   & (facts["eligible"].fillna(False).astype(bool) if "eligible" in facts else True)]
        by_h: dict[int, list] = {}
        for _, r in lu.sort_values("amount", ascending=False).iterrows():
            if pd.notna(r["board_height"]):
                by_h.setdefault(int(r["board_height"]), []).append(
                    (r["name"], GROUP_CSS.get(r.get("lm_group")) if isinstance(r.get("lm_group"), str) else None))
        ladder_html = ('<h3>连板天梯 <small>彩色 = 清单股</small></h3>'
                       + charts.ladder(by_h, by_h.pop(1, [])))
    parts.append(f'<section id="threads"><h2>主线与题材线索 <small>只作描述，不改分组</small></h2>'
                 f'<div class="mls">{mainl}</div>{ladder_html}'
                 + (f'<h3>题材线索 <small>格内是当天涨停家数，点一行看股票</small></h3>{strip}'
                    if strip else "")
                 + (f'<h3>高标结局</h3><div class="fates">{fate_html}</div>' if fate_html else "")
                 + (f'<details class="more-t"><summary>题材热度榜（表格）</summary>{heat_html}</details>' if heat_html else "")
                 + f'{wave_html}<details class="more-t"><summary>线索卡片（详细）</summary>'
                 f'<div class="grid">{"".join(cards) or "<p>没有线索记录（旧版清单）。</p>"}</div></details></section>'
                 + "".join(dialogs))

    codes = [c for c in picks["code"].dropna()]
    mins, lims = minute_bars(obs, codes), up_limits(obs, codes)
    fx = facts.set_index("name") if "name" in facts else pd.DataFrame()

    def visuals(r):
        c = code_of.get(r["名称"])
        bars, label = mins.get(c, (None, ""))
        pre, up = lims.get(c, (None, None))
        chart = charts.intraday_chart(bars, pre, up, f"{obs[5:]} {label}") if c else ""
        vh = fx["vs_high250"].get(r["名称"]) if "vs_high250" in fx else None
        vm = fx["vs_ma250"].get(r["名称"]) if "vs_ma250" in fx else None
        return chart, charts.position_bar(vh, vm)

    plans = table["参与建议"].value_counts()
    common = {p for p, n in plans.items() if n >= 3 and p}  # shown once above the cards instead of on each

    def card(r):
        return _stock_card(r, pos.get(r["名称"]), *visuals(r), common=common)
    fcards = "".join(card(r) for _, r in focus.iterrows())
    risk_names = "".join(f'<span class="chip {GROUP_CLASS.get(r["分组"], "")}" title="{_e(r["角色"])}">{_e(r["名称"])}</span>'
                         for _, r in risky.iterrows())
    parts.append(f'<section id="focus"><h2>仓位内的候选 <small>买卖由你决定</small></h2>'
                 f'<div class="grid">{fcards or "<p class=dim>今日没有仓位内的候选。</p>"}</div>'
                 + (f'<p class="riskline">风险最大（空间板、高波动）：{risk_names}<span class="dim"> 详见完整清单</span></p>'
                    if risk_names else "") + "</section>")

    auction = _latest(store.home() / "auction" / target)
    if auction is not None and (auction / "snapshot.parquet").exists():
        s = pd.read_parquet(auction / "snapshot.parquet")
        s = s.assign(竞价缺口=(s["gap"] * 100).round(1).astype(str) + "%",
                     竞价标签=s["label"].map({"deny": "竞价否定（5日内较少走出来）", "limit_open": "涨停开",
                                            "neutral": "中性", "confirm": "中性", "description": "只描述"}).fillna(s["label"]))
        s, anotes = listing.fold_repeats(s.rename(columns={"name": "名称", "group": "分组", "bands": "同类开盘后（历史）"}),
                                         ["同类开盘后（历史）"])
        gaps = charts.gap_bars([(r["名称"], r["gap"], "竞价否定" if r["label"] == "deny" else
                                 "涨停开" if r["label"] == "limit_open" else "") for _, r in s.iterrows()])
        parts.append(f'<section id="auction"><h2>竞价快照 <small>{_e(auction.name)}</small></h2>'
                     f'<p class="note">开盘价相对昨收：向右红色是高开，向左绿色是低开。</p>{gaps}'
                     '<details class="more-t"><summary>表格（全部文字）</summary>' +
                     _table(s, ["名称", "分组", "竞价缺口", "竞价标签", "同类开盘后（历史）"]) + _notes(anotes) + "</details></section>")
    else:
        parts.append('<section id="auction"><h2>竞价快照</h2><p class="dim">还没有这个交易日的竞价快照（09:26 定时任务会自动抓取）。</p></section>')

    tabs, panes = [], []
    folded, lnotes = listing.fold_repeats(table)  # repeated texts shown once below the table
    for i, grp in enumerate(g_ for g_ in GROUPS if (table["分组"] == g_).any()):
        part = folded[folded["分组"] == grp]
        tabs.append(f'<button class="tab {GROUP_CLASS[grp]}{" on" if i == 0 else ""}" data-p="p{i}">{grp} {len(part)}</button>')
        panes.append(f'<div class="pane{" on" if i == 0 else ""}" id="p{i}"><div class="cards">'
                     + "".join(card(r) for _, r in table[table["分组"] == grp].iterrows()) + '</div>'
                     + '<details class="more-t"><summary>表格（全部文字）</summary>'
                     + _table(part, listing.VIEW[1:], pos) + "</details></div>")
    shared = "".join(f'<div class="plan">{_e(p)}<small>（{plans[p]} 只相同，卡片上不重复）</small></div>' for p in sorted(common))
    parts.append(f'<section id="groups"><h2>完整清单</h2>{shared}<div class="tabs">{"".join(tabs)}</div>{"".join(panes)}'
                 f'{_notes(lnotes)}</section>')

    rev = _latest(store.home() / "reviews")
    daily = daily_report_html(summary, table)
    rv = "" if rev is not None and (rev / "rows.csv").exists() else daily
    if rev is not None and (rev / "rows.csv").exists():
        rows = pd.read_csv(rev / "rows.csv")
        rv += rate_chart(rows) + review_html(rows, rev.name) + daily + review_report_html(rev)
    elif rev is not None and (rev / "stats.csv").exists():
        st = pd.read_csv(rev / "stats.csv").rename(columns={"Unnamed: 0": "组"})
        rv += f'<h3>最近一次复盘 <small>{_e(rev.name)}</small></h3>' + _table(st.round(3), list(st.columns))
    sc = store.home() / "journal" / "scores"
    if sc.exists() and sorted(sc.glob("*.csv")):
        j = pd.read_csv(sorted(sc.glob("*.csv"))[-1], dtype=str).fillna("")
        rv += '<h3>判断日记</h3>' + _table(j.tail(15).rename(columns={"observation": "观察日", "kind": "类型", "subject": "对象", "text": "原话",
                                                                     "result": "结果"}), ["观察日", "类型", "对象", "原话", "结果"])
    parts.append(f'<section id="review"><h2>复盘</h2>{rv or "<p class=dim>还没有复盘记录。</p>"}</section>')
    parts.append('<section class="foot"><details><summary>表头原文</summary><pre>' + _e("\n".join(header)) +
                 "</pre></details><p>本页只读取已保存的文件，不重新计算。本系统不交易，买卖由你决定；非投资建议。</p></section>")
    return TEMPLATE.replace("{{title}}", f"龙脉 {target}").replace("</style>", charts.CSS + "</style>", 1) \
        .replace("{{body}}", "\n".join(parts))


def write(run: Path) -> Path:
    summary = json.loads((run / "summary.json").read_text())
    out = store.home() / "pages" / summary["target"]
    out.mkdir(parents=True, exist_ok=True)
    path = out / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".html")
    with open(path, "x", encoding="utf-8") as f:
        f.write(render(run))
    for older in out.glob("*.html"):  # one page per day: a page is only a rendering of saved files
        if older != path:
            older.unlink()
    write_index()
    return path


def _replace(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _made(stem: str) -> str:
    """A page file stamp (UTC, e.g. 20261112T175555Z) as Shanghai time 'MM-DD HH:MM:SS'; the raw stem if unparsable."""
    try:
        when = datetime.strptime(stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return stem
    return when.astimezone(store.SH).strftime("%m-%d %H:%M:%S")


def _weekday(day: str) -> str:
    try:
        return "周" + "一二三四五六日"[datetime.strptime(day, "%Y-%m-%d").weekday()]
    except ValueError:
        return ""





def _hop_page(target: str | None, text: str) -> str:
    head = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
    if target:
        return head + f'<meta http-equiv="refresh" content="0; url=../{target}"><title>龙脉</title></head><body></body></html>'
    return (head + '<title>龙脉</title></head><body style="background:#07090f;color:#e8edf7;font:15px -apple-system,'
            f'\'PingFang SC\',sans-serif;padding:40px"><p>{_e(text)}</p><p><a style="color:#38e1ff" href="../index.html">'
            '← 回到页面目录</a></p></body></html>')


def _write_hops(root: Path, days: list[tuple[str, str]]) -> None:
    """pages/go/prev-<day>.html and next-<day>.html: redirects to the neighbouring day's page, rewritten with the index so
    the links in older pages stay current; at either end a short note with a link back to the index."""
    go = root / "go"
    go.mkdir(exist_ok=True)
    for i, (day, _) in enumerate(days):
        prev = f"{days[i - 1][0]}/{days[i - 1][1]}" if i > 0 else None
        nxt = f"{days[i + 1][0]}/{days[i + 1][1]}" if i + 1 < len(days) else None
        _replace(go / f"prev-{day}.html", _hop_page(prev, "这是最早的一天，没有更早的页面。"))
        _replace(go / f"next-{day}.html", _hop_page(nxt, "这是最新的一天，后一个交易日的页面还没有生成。"))


def write_index() -> Path:
    """pages/index.html (every target, newest first, with its page versions) and pages/latest.html (opens the newest
    page). Navigation only: rewritten on each new page; the pages themselves are never changed."""
    root = store.home() / "pages"
    days = sorted((d for d in root.iterdir() if d.is_dir() and _weekday(d.name) and sorted(d.glob("*.html"))),
                  reverse=True)  # date folders only (not pages/go/)
    from .listing import after_open, latest_run, run_time

    def facts(d: Path) -> tuple[Path, str]:
        page = sorted(d.glob("*.html"))[-1]
        try:
            run = latest_run(d.name)
        except ValueError:
            run = None
        bits = []
        if run is not None:
            try:
                version = json.loads((run / "summary.json").read_text()).get("strategy_version", "")
            except (OSError, ValueError):
                version = ""
            when = run_time(run.name)
            if when is not None:
                bits.append(f"清单生成于 {when.astimezone(store.SH).strftime('%m-%d %H:%M')}")
            if version:
                bits.append(zh(version))
            if after_open(run):
                bits.append('<b class="ix-warn">开盘后生成</b>')
        else:
            bits.append("清单缺失")
        return page, " · ".join(bits)

    rows = []
    for d in days:
        page, info = facts(d)
        rows.append(f'<div class="ix-row"><a class="ix-main" href="{d.name}/{page.name}">'
                    f'<span class="ix-d">{_e(d.name)}<span class="ix-w">{_weekday(d.name)}</span></span>'
                    f'<span class="ix-t">{info}</span><span class="ix-go">打开 →</span></a></div>')
    latest = f"{days[0].name}/{facts(days[0])[0].name}" if days else None
    if latest:
        feat = (f'<a class="ix-feat" href="{latest}"><div><span class="ix-badge">最新</span>'
                f'<div class="ix-big">{_e(days[0].name)}<span>{_weekday(days[0].name)}</span></div>'
                f'<div class="ix-when">这个交易日的盘前页面 · {facts(days[0])[1]}</div></div>'
                f'<span class="ix-btn">打开页面 →</span></a>')
    else:
        feat = '<p class="dim">还没有页面。生成盘前清单后会出现在这里。</p>'
    body = (f'<div class="top"><div class="brand"><span class="dot"></span>龙脉</div></div>'
            f'<header class="ix-hero"><div class="ix-kicker">龙脉 · 页面目录</div><div class="ix-title">盘前页面</div>'
            f'<div class="ix-sub">日期是清单要用的那个交易日（前一交易日收盘后生成）。每个交易日只保留一份有效清单：'
            f'当天 09:25 之前最后保存的那一份。</div></header>{feat}'
            f'<div class="ix-h"><span>全部日期</span><small class="dim">共 {len(days)} 天</small></div>'
            f'<div class="ix-list">{"".join(rows)}</div>'
            f'<p class="ix-foot">只是描述，不是买卖建议；每笔交易由你自己决定。</p>')
    _replace(root / "index.html", TEMPLATE.replace("{{title}}", "龙脉 页面目录").replace("</style>", INDEX_CSS + "</style>")
             .replace("{{body}}", body))
    _write_hops(root, [(d.name, facts(d)[0].name) for d in sorted(days)])
    if latest:
        _replace(root / "latest.html", f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                 f'<meta http-equiv="refresh" content="0; url={latest}"><title>龙脉 最新</title></head>'
                 f'<body style="background:#07090f;color:#e8edf7"><a href="{latest}">打开最新页面</a></body></html>')
    return root / "index.html"
