"""Local HTML page (本地页面) for one saved pre-market list: alerts first, a one-screen dashboard, then the full tables.

Built only from files already saved (the list run, the latest auction snapshot of its target, the latest review,
judgment scores and the scheduled-job status), so it never recomputes or changes anything. Written once under
SF_HOME/pages/<target>/<stamp>.html; one self-contained file, no network, no external scripts or fonts.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import charts, listing, namethemes, store

GROUPS = ("主线龙头", "连板梯队", "观察", "高波动")
GROUP_CLASS = {"主线龙头": "g-lead", "连板梯队": "g-ladder", "观察": "g-watch", "高波动": "g-risk"}


ZH_WORDS = {
    "STRONG_CONTINUATION": "强势延续", "STRONG_START": "强势启动", "FIRST_PULLBACK": "首次回调", "NEUTRAL": "中性",
    "UNKNOWN": "未知", "leader": "主线龙头", "ladder": "连板梯队", "watch": "观察", "avoid": "高波动",
    "baseline": "对照", "description": "只描述", "confirm": "确认", "deny": "否定", "neutral": "中性",
    "stock_lu": "个股涨停", "stock_bigloss": "个股大面", "stock_out5": "个股走出来", "theme_fade": "题材退潮",
    "free": "其他", "ok": "正常", "partial": "部分缺失", "FAILED": "失败", "skipped": "跳过", "pending": "待定",
    "not captured": "未采集",
}
ZH_COMMANDS = {
    "intraday --backfill": "分时补抓", "intraday": "分时抓取", "auction": "竞价快照", "premarket": "盘前清单",
    "baserates": "历史同类统计", "themes": "板块成分更新", "note": "题材记录", "page": "页面生成", "review": "复盘",
    "evaluate": "检验", "update": "数据更新", "seats": "龙虎榜席位", "schedule": "定时任务",
}


def _stamp_zh(m: re.Match) -> str:
    when = datetime.strptime(m.group(0), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(store.SH)
    return when.strftime("%m-%d %H:%M")


def zh(text: str) -> str:
    """Page text in Chinese only: stage codes, group codes, version names, UTC run stamps (shown as Beijing time),
    pinyin in homophone groups, command and file names. Stock and board names (e.g. *ST, TOPCon) are kept."""
    t = re.sub(r"\d{8}T\d{6}Z", _stamp_zh, text)
    t = re.sub(r"SF_LONGMAI_V(\d+)", r"龙脉第\1版", t)
    t = re.sub(r"SF_[A-Z_]+?_V(\d+)", r"第\1版", t)
    t = re.sub(r"龙脉\s*V(\d+)", r"龙脉第\1版", t)
    t = re.sub(r"(?<![A-Za-z])V(\d+)(?![A-Za-z0-9])", r"第\1版", t)
    t = re.sub(r"音·[a-z]+\(([^)]*)\)", r"谐音「\1」", t)
    t = re.sub(r"谐音 [a-z]+（([^）]*)）", r"谐音「\1」", t)
    t = re.sub(r"名字谐音「[a-z]+\(([^)]*)\)」", r"谐音「\1」", t)
    t = re.sub(r"(?:bin/)?sf ([a-z]+(?: --[a-z\-]+)?)",
               lambda m: ZH_COMMANDS.get(m.group(1), ZH_COMMANDS.get(m.group(1).split()[0], "相应")) + "命令", t)
    t = re.sub(r"docs/[\w./\-]+", "说明文档", t)
    for en, cn in sorted(ZH_WORDS.items(), key=lambda kv: -len(kv[0])):
        t = re.sub(rf"(?<![A-Za-z_]){re.escape(en)}(?![A-Za-z_])", cn, t)
    return t


def _e(x) -> str:
    return "" if x is None or (isinstance(x, float) and pd.isna(x)) else html.escape(zh(str(x)))


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


def _stock_card(r: pd.Series, vs_hi, chart: str = "", position: str = "", common: set | None = None) -> str:
    """One stock: role, intraday chart, amount and turnover, position, plan (left out when it is the shared plan shown
    once above the cards) and risk notes."""
    risk = f'<div class="risk">{_e(r["风险提示"])}</div>' if r.get("风险提示") else ""
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
                            series("mood"), "t-mood", lo=0, hi=1)
        + charts.trend_tile("热度", val(tp.get("heat"), "{:.0%}"), f"较前3日{tp.get('trend', '?')}{trend}",
                            series("heat"), "t-heat", lo=0, hi=1)
        + charts.trend_tile("亏钱效应", val(tp.get("pain"), "{:.0%}"), tp.get("cell", ""), series("pain"), "t-pain", lo=0, hi=1)
        + charts.trend_tile("涨停家数", val(td.get("lu_count"), "{:.0f}"), f"跌停 {val(td.get('ld_count'), '{:.0f}')} 家",
                            series("lu"), "t-lu", fmt="{:.0f}家")
        + charts.trend_tile("晋级率", val(td.get("promo_rate"), "{:.0%}"), f"炸板率 {val(td.get('broken_rate'), '{:.0%}')}",
                            series("promo"), "t-pro")
        + f'<div class="tile"><div class="tl">规则仓位上限</div><div class="slots">{dots}</div>'
          f'<div class="tv">{slots} 只 × 1/4</div><div class="tl">监管温度 {_e(g.get("reg_tier"))}'
          f'（深市周处置 {_e(g.get("szse_cases"))} 起）</div></div>'
        + '</section>')
    if slots == 0:
        parts.append('<section class="alert slim"><b>规则空仓</b>：今天只观察，不开新仓。</section>')
    brief = []
    if tp.get("text"):  # the full sentence stays in the tooltip and in 表头原文
        src = "、".join(re.sub(r"\(.*?\)", "", x) for x in tp.get("sources") or [])
        brief.append(f'<div title="{_e(tp["text"])}"><span class="bk">水温</span>{_e(tp.get("cell", ""))}'
                     f'（{_e(tp.get("name", ""))}）' + (f'<span class="dim"> · 压力来自{_e(src)}</span>' if src else "") + "</div>")
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
    return ('<h3>各组命中与大面 <small>全部已存清单；样本少只看方向</small></h3>'
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
    return (f'<h3>复盘表 <small>{len(targets)} 份清单 · <span class="good">绿</span>比对照好 · <span class="bad">红</span>比对照差</small></h3>'
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


INDEX_CSS = """
main{max-width:860px;padding-bottom:40px}body{padding-right:0!important}
.ix-hero{padding:36px 0 22px}.ix-kicker{color:var(--cyan);font-size:12px;font-weight:700;letter-spacing:.2em}
.ix-title{font-size:clamp(30px,6vw,44px);font-weight:800;letter-spacing:-.01em;margin:6px 0 8px}
.ix-sub{color:var(--dim);max-width:560px}
.ix-feat{position:relative;display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap;
padding:26px 28px;border-radius:20px;text-decoration:none;color:var(--fg);overflow:hidden;
background:linear-gradient(135deg,rgba(56,225,255,.14),rgba(167,139,250,.12) 60%,rgba(18,26,44,.8));
box-shadow:0 0 0 1px rgba(56,225,255,.35),0 10px 28px -16px rgba(56,225,255,.4);transition:transform .2s}
.ix-feat:hover{transform:translateY(-3px)}
.ix-feat:after{content:"";position:absolute;right:-80px;top:-80px;width:240px;height:240px;border-radius:50%;
background:radial-gradient(rgba(167,139,250,.35),transparent 70%);pointer-events:none}
.ix-badge{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:700;color:var(--green);
padding:2px 10px;border-radius:999px;background:rgba(52,211,153,.12)}
.ix-badge:before{content:"";width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 8px var(--green)}
.ix-big{font-size:clamp(34px,8vw,56px);font-weight:800;line-height:1.05;margin:10px 0 4px;letter-spacing:-.02em;
background:linear-gradient(90deg,#fff,var(--cyan) 60%,var(--violet));-webkit-background-clip:text;background-clip:text;color:transparent}
.ix-big span{font-size:.42em;margin-left:10px;-webkit-text-fill-color:var(--dim);letter-spacing:0}
.ix-when{color:var(--dim);font-size:13px}
.ix-btn{position:relative;z-index:1;padding:12px 22px;border-radius:14px;font-weight:800;color:#08101d;white-space:nowrap;
background:linear-gradient(90deg,var(--cyan),var(--violet))}
.ix-h{display:flex;align-items:baseline;justify-content:space-between;margin:34px 0 12px;font-weight:700}
.ix-h small{font-weight:400}
.ix-list{display:grid;gap:10px}
.ix-row{background:var(--panel);border:1px solid var(--line);border-radius:14px;transition:border-color .15s}
.ix-row:hover{border-color:rgba(56,225,255,.45)}
.ix-main{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:16px;padding:14px 18px;
text-decoration:none;color:var(--fg)}
.ix-d{font-size:18px;font-weight:800;font-variant-numeric:tabular-nums}.ix-w{color:var(--dim);font-size:13px;margin-left:6px;font-weight:500}
.ix-t{color:var(--dim);font-size:13px;font-variant-numeric:tabular-nums}.ix-go{color:var(--cyan);font-weight:700}
.ix-row details{border-top:1px dashed var(--line);padding:8px 18px 12px}
.ix-row summary{cursor:pointer;color:var(--dim);font-size:12px;list-style:none}
.ix-row summary::-webkit-details-marker{display:none}.ix-row summary:before{content:"▸ ";color:var(--violet)}
.ix-row details[open] summary:before{content:"▾ "}
.ix-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.ix-chip{font-size:12px;padding:3px 10px;border-radius:999px;color:var(--fg);text-decoration:none;
background:rgba(255,255,255,.05);border:1px solid var(--line);font-variant-numeric:tabular-nums}
.ix-chip:hover{border-color:var(--cyan);color:var(--cyan)}
.ix-warn{color:var(--amber);font-weight:700}
.ix-foot{margin:30px 0 0;color:var(--dim);font-size:12px;text-align:center}
@media (max-width:560px){.ix-main{grid-template-columns:1fr auto}.ix-t{grid-column:1/-1;order:3}}
"""


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


TEMPLATE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{title}}</title>
<style>
:root{--bg:#07090f;--bg2:#0c1220;--panel:rgba(18,26,44,.72);--line:rgba(120,160,255,.14);--fg:#e8edf7;--dim:#8a96ad;
--cyan:#38e1ff;--violet:#a78bfa;--amber:#fbbf24;--red:#ff5d6c;--green:#34d399;--lead:#ff8a3d;--ladder:#5b8cff;
--watch:#94a3b8;--risk:#ff5d6c;--glow:0 0 0 1px var(--line),0 6px 18px -12px rgba(56,225,255,.22)}
*{box-sizing:border-box}html{scroll-behavior:smooth;background:var(--bg)}
body{margin:0;color:var(--fg);font:14px/1.6 -apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
min-height:100vh}
/* the glow and grid sit on fixed layers of their own: painted once and composited, not repainted on every scroll */
body:before,body:after{content:"";position:fixed;inset:0;pointer-events:none;z-index:-1;will-change:transform;transform:translateZ(0)}
body:before{background:radial-gradient(1200px 600px at 10% -10%,rgba(56,225,255,.12),transparent 60%),
radial-gradient(900px 500px at 100% 0%,rgba(167,139,250,.14),transparent 60%),linear-gradient(var(--bg),var(--bg2))}
body:after{opacity:.35;background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);
background-size:44px 44px;-webkit-mask-image:linear-gradient(to bottom,#000,transparent 70%);mask-image:linear-gradient(to bottom,#000,transparent 70%)}
main{position:relative;max-width:1240px;margin:0 auto;padding:0 16px 60vh}
.top{position:sticky;top:0;z-index:5;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;
background:linear-gradient(rgba(7,9,15,.97) 70%,rgba(7,9,15,0))}
.brand{font-weight:800;letter-spacing:.08em;display:flex;align-items:center;gap:8px;white-space:nowrap}

.dot{width:9px;height:9px;border-radius:50%;background:var(--cyan);box-shadow:0 0 12px var(--cyan)}
.side{position:fixed;right:22px;top:50%;transform:translateY(-50%);z-index:6;display:flex;flex-direction:column;gap:2px;
padding:14px 10px;border-radius:18px;background:rgba(12,18,32,.62);border:1px solid rgba(120,160,255,.2);
box-shadow:0 12px 32px -16px rgba(0,0,0,.6)}
.side-t{font-size:12px;color:var(--dim);letter-spacing:.12em;padding:0 12px 8px}
.side a{display:flex;align-items:center;gap:10px;color:#aab6cc;text-decoration:none;font-size:15px;padding:8px 14px 8px 10px;
border-radius:10px;white-space:nowrap;transition:background .15s,color .15s}
.side a i{width:6px;height:6px;border-radius:50%;background:rgba(170,182,204,.35);flex:none;transition:background .15s,box-shadow .15s}
.side a:hover{color:var(--fg);background:rgba(255,255,255,.05)}
.side a.on{color:var(--fg);background:rgba(56,225,255,.10)}.side a.on i{background:var(--cyan);box-shadow:0 0 8px var(--cyan)}
.side-btn{display:none}
@media (min-width:1101px){body{padding-right:210px}}
@media (max-width:1100px){.side{display:none;top:auto;bottom:72px;transform:none}.side.open{display:flex;background:rgba(12,18,32,.97)}
.side-btn{display:block;position:fixed;right:16px;bottom:20px;z-index:7;border:1px solid rgba(120,160,255,.3);border-radius:999px;
padding:9px 16px;font:inherit;font-size:14px;color:var(--fg);background:rgba(12,18,32,.75);cursor:pointer}}
section[id],.sec-h,details[id]{scroll-margin-top:78px}
.sec-h{margin:22px 0 0}.sec-h h2{margin:0}
.hop{display:flex;gap:6px;margin-right:auto;margin-left:10px}.hop a{color:var(--cyan);text-decoration:none;font-size:13px;
padding:3px 10px;border-radius:999px;border:1px solid var(--line);white-space:nowrap}.hop a:hover{border-color:var(--cyan);background:var(--panel)}
.hero{padding:18px 0 6px;display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 18px}
.date{font-size:clamp(34px,7vw,60px);font-weight:800;letter-spacing:-.02em;line-height:1;
background:linear-gradient(90deg,#fff,var(--cyan) 55%,var(--violet));-webkit-background-clip:text;background-clip:text;color:transparent}
.meta{color:var(--dim)}.tag{padding:2px 12px;border-radius:999px;border:1px solid var(--violet);color:var(--violet);font-weight:600}
section{background:var(--panel);border-radius:16px;padding:18px 20px;margin:22px 0;box-shadow:var(--glow)}
.sec-h+section{margin-top:12px}
h2{font-size:19px;margin:0 0 14px;padding-bottom:10px;border-bottom:1px solid var(--line);display:flex;align-items:center;
gap:10px;flex-wrap:wrap}
h2:before{content:"";width:4px;height:18px;border-radius:2px;background:linear-gradient(var(--cyan),var(--violet));flex:none}
.sec-h h2{border-bottom:0;padding-bottom:0}h3{font-size:14px;margin:16px 0 8px;color:var(--dim)}
small{color:var(--dim);font-weight:400;font-size:12px}.dim{color:var(--dim)}
.alert{background:linear-gradient(135deg,rgba(255,93,108,.18),rgba(251,191,36,.08));box-shadow:0 0 0 1px rgba(255,93,108,.45)}
.pulse{width:10px;height:10px;border-radius:50%;background:var(--red);box-shadow:0 0 8px var(--red);animation:pl 1.6s infinite}
@keyframes pl{50%{opacity:.35}}
.ok{color:var(--green)}ul{margin:0;padding-left:20px}
.dash{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;background:none;box-shadow:none;padding:0}
.gauge,.tile{position:relative;background:var(--panel);border-radius:16px;box-shadow:var(--glow);padding:14px;text-align:center}
.gauge svg{width:96px;height:96px;transform:rotate(-90deg)}.trk{fill:none;stroke:rgba(255,255,255,.07);stroke-width:8}
.val{fill:none;stroke:currentColor;stroke-width:8;stroke-linecap:round}
.t-mood{color:var(--cyan)}.t-heat{color:var(--amber)}.t-pain{color:var(--red)}
.gv{position:absolute;top:46px;left:0;right:0;font-size:24px;font-weight:800;color:var(--fg)}
.gl{font-weight:700;color:var(--fg)}.gs,.tl{color:var(--dim);font-size:12px}
.tile{display:flex;flex-direction:column;justify-content:center;gap:6px}.tv{font-size:22px;font-weight:800}
.slots{display:flex;gap:8px;justify-content:center}.slots i{width:22px;height:22px;border-radius:6px;background:rgba(255,255,255,.07)}
.slots i.on{background:linear-gradient(135deg,var(--cyan),var(--violet));box-shadow:0 0 12px rgba(56,225,255,.6)}
.note{color:var(--dim)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.thread,.stock{background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:14px;padding:12px;transition:transform .15s,border-color .15s}
.thread:hover,.stock:hover{transform:translateY(-2px);border-color:rgba(56,225,255,.5)}
.th,.sh{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.nm{font-weight:700;font-size:15px}
.age{font-size:12px;font-weight:700;padding:1px 8px;border-radius:999px;background:rgba(167,139,250,.18);color:var(--violet)}
.live{font-size:11px;padding:1px 8px;border-radius:999px;background:rgba(52,211,153,.15);color:var(--green)}
.spark{width:100%;height:46px;margin:8px 0}.spark rect{fill:rgba(91,140,255,.45)}.spark rect.peak{fill:var(--amber)}
.spark rect.last{fill:var(--cyan)}
.kv{color:var(--dim);font-size:13px}.kv b{color:var(--fg)}.role{margin:4px 0;font-size:13px}
.plan{margin-top:8px;font-size:13px;padding:6px 8px;border-radius:8px;background:rgba(56,225,255,.07);border-left:2px solid var(--cyan)}
.risk{margin-top:6px;font-size:12px;color:#ffb3ba}
.pos{height:4px;border-radius:4px;background:rgba(255,255,255,.08);margin:6px 0 2px;overflow:hidden;min-width:60px}
.pos span{display:block;height:100%;background:linear-gradient(90deg,var(--red),var(--amber),var(--green))}
.mls{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}.ml{padding:6px 12px;border-radius:10px;border:1px solid var(--line);font-size:13px}
.ml span{color:var(--dim);margin-left:6px}
.fates{display:flex;flex-wrap:wrap;gap:8px}.fate{padding:8px 12px;border-radius:10px;font-size:13px;display:flex;gap:8px}
.fate.up{background:rgba(52,211,153,.12);color:var(--green)}.fate.down{background:rgba(255,93,108,.12);color:#ffb3ba}.fate b{color:var(--fg)}
.chip{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;font-weight:700;color:#08101d}
.g-lead{background:var(--lead)}.g-ladder{background:var(--ladder)}.g-watch{background:var(--watch)}.g-risk{background:var(--risk)}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.tab{border:0;cursor:pointer;border-radius:999px;padding:5px 14px;font:inherit;font-weight:700;color:#08101d;opacity:.45}
.tab.on{opacity:1;box-shadow:0 0 14px rgba(255,255,255,.18)}.pane{display:none}.pane.on{display:block}
.scroll{overflow-x:auto;border-radius:10px}table{border-collapse:collapse;width:100%;font-size:13px}
th{background:#111a2c;color:var(--dim);font-weight:600;white-space:nowrap;text-align:left;padding:8px}
td{border-top:1px solid var(--line);padding:8px;vertical-align:top;min-width:6em}td:first-child{white-space:nowrap;min-width:0}
tbody tr:hover{background:rgba(56,225,255,.06)}
.rev td{white-space:nowrap}.rev td small{color:var(--dim);font-size:11px}.rev tr.base td{color:var(--dim);border-top:1px dashed var(--line)}
.fullrep summary{color:var(--cyan);font-weight:700}.daily ul{margin:4px 0 8px;line-height:1.9}.fullrep h3{margin-top:14px}
.good{color:var(--green)}.bad{color:var(--red)}.legend{margin-top:10px}.legend ul{margin:6px 0}
.wave{border-color:rgba(167,139,250,.45)}.wave b{color:var(--violet)}
.thread.name{border-color:rgba(167,139,250,.5);background:linear-gradient(160deg,rgba(167,139,250,.10),rgba(255,255,255,.02))}
.xg{display:flex;flex-direction:column;margin-top:6px;font-size:13px}.xg b{color:var(--violet)}.xg span{color:var(--dim);font-size:12px}
.xuan{font-size:12px;font-weight:800;padding:1px 8px;border-radius:999px;background:var(--violet);color:#120b26}
.heat{table-layout:fixed;min-width:760px}.heat .c-b{width:150px}.heat .c-n{width:84px}.heat .c-s{width:auto}
.heat th.hn,.heat td.hn{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;min-width:0;padding-right:14px}
.heat th.hs,.heat td.hs{padding-left:18px;padding-right:18px}.heat td{vertical-align:middle}
.heat .spark{display:block;width:100%;height:30px;margin:0}
.thread.open{cursor:pointer}.more{margin-top:6px;color:var(--cyan);font-size:12px;font-weight:700}
.tdlg{width:min(920px,calc(100vw - 32px));max-height:85vh;padding:18px;border:1px solid var(--line);border-radius:16px;
background:#0d1424;color:var(--fg)}.tdlg::backdrop{background:rgba(3,6,12,.72)}
.dh{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:12px}.dh b{font-size:17px}
.x{background:none;border:1px solid var(--line);color:var(--fg);border-radius:10px;font-size:20px;line-height:1;padding:4px 10px;cursor:pointer}
.tdlg td small{color:var(--dim);font-size:11px}.tdlg td.n{white-space:nowrap;min-width:90px}
.bar{display:block;height:4px;margin-top:4px;border-radius:2px;background:rgba(255,255,255,.07)}
.bar span{display:block;height:100%;border-radius:2px;background:var(--amber)}
.lus{line-height:1.9}.lus i{font-style:normal;font-size:11px;color:var(--amber);margin-left:2px}.in{color:var(--cyan);font-weight:700}
.foot{color:var(--dim);font-size:12px}pre{white-space:pre-wrap}summary{cursor:pointer}
@media (max-width:600px){.gauge svg{width:80px;height:80px}.gv{top:38px;font-size:20px}}
</style></head><body><main>
{{body}}
</main>
<script>
document.querySelectorAll('.tab').forEach(function(b){b.addEventListener('click',function(){
document.querySelectorAll('.tab,.pane').forEach(function(x){x.classList.remove('on')});
b.classList.add('on');document.getElementById(b.dataset.p).classList.add('on');});});
document.querySelectorAll('[data-d]').forEach(function(c){function o(){var d=document.getElementById(c.dataset.d);
if(d&&d.showModal)d.showModal();}c.addEventListener('click',o);c.addEventListener('keydown',function(e){if(e.key==='Enter')o();});});
document.querySelectorAll('dialog').forEach(function(d){d.addEventListener('click',function(e){if(e.target===d)d.close();});
d.querySelector('.x').addEventListener('click',function(){d.close();});});
(function(){var side=document.querySelector('.side');if(!side)return;
var links=[].slice.call(side.querySelectorAll('a'));
var targets=links.map(function(a){return document.getElementById(a.getAttribute('href').slice(1));});
var lock=null;function mark(k){links.forEach(function(a,i){a.classList.toggle('on',i===k);});}
links.forEach(function(a,i){a.addEventListener('click',function(e){var t=targets[i];if(!t)return;e.preventDefault();
if(t.tagName==='DETAILS')t.open=true;var top=t.getBoundingClientRect().top+window.pageYOffset-76;
window.scrollTo({top:Math.max(0,top),behavior:'smooth'});history.replaceState(null,'','#'+t.id);
mark(i);clearTimeout(lock);lock=setTimeout(function(){lock=null;},900);side.classList.remove('open');});});
var btn=document.querySelector('.side-btn');if(btn)btn.addEventListener('click',function(){side.classList.toggle('open');});
var ticking=false;function spy(){ticking=false;if(lock)return;var cur=0;
targets.forEach(function(t,i){if(t&&t.getBoundingClientRect().top<=120)cur=i;});mark(cur);}
addEventListener('scroll',function(){if(!ticking){ticking=true;requestAnimationFrame(spy);}},{passive:true});spy();})();
</script></body></html>
"""
