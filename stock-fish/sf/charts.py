"""Inline charts for the local page (page.py): plain SVG / HTML strings, no scripts, no network.

Every chart is description only and is drawn from values the page already reads from saved files. Unknown values
are left out of a chart (a gap or a '?'), never drawn as zero.
"""
from __future__ import annotations

import html
import math

import pandas as pd


def _e(x) -> str:
    return html.escape(str(x))


def _known(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


# ------------------------------------------------------------------ overview: big number + trend line

def trend_tile(label: str, value: str, sub: str, series: list[tuple[str, float | None]], tone: str,
               fmt: str = "{:.0%}", lo: float | None = None, hi: float | None = None) -> str:
    """A tile with the latest value large and a small line of the recent days under it (hover shows each day)."""
    pts = [(d, v) for d, v in series if _known(v)]
    svg = ""
    if len(pts) >= 2:
        w, h, pad = 160, 40, 4
        vals = [v for _, v in pts]
        a = min(vals) if lo is None else lo
        b = max(vals) if hi is None else hi
        b = b if b > a else a + 1
        idx = {d: i for i, (d, _) in enumerate(series)}
        n = max(len(series) - 1, 1)

        def xy(d, v):
            return pad + (w - 2 * pad) * idx[d] / n, h - pad - (h - 2 * pad) * (v - a) / (b - a)
        coords = [xy(d, v) for d, v in pts]
        line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        area = f"{coords[0][0]:.1f},{h} " + line + f" {coords[-1][0]:.1f},{h}"
        dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" class="hit"><title>{_e(d)} {_e(fmt.format(v))}</title></circle>'
                       for (x, y), (d, v) in zip(coords, pts))
        lx, ly = coords[-1]
        svg = (f'<svg class="trend" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">'
               f'<polygon class="ar" points="{area}"/><polyline class="ln" points="{line}"/>'
               f'<circle class="now" cx="{lx:.1f}" cy="{ly:.1f}" r="3"/>{dots}</svg>')
        sub = sub + f" · 近{len(series)}日"
    return (f'<div class="kpi {tone}"><div class="kl">{_e(label)}</div><div class="kn">{_e(value)}</div>'
            f'{svg}<div class="ks">{_e(sub)}</div></div>')


# ------------------------------------------------------------------ limit-up ladder

def ladder(by_height: dict[int, list[tuple[str, str | None]]], first: list[tuple[str, str | None]]) -> str:
    """Columns from the tallest board down to 2 boards, one chip per stock (listed stocks coloured by group), and
    the first boards as a count with the listed ones named."""
    cols = []
    for h in sorted((k for k in by_height if k >= 2), reverse=True):
        chips = "".join(f'<span class="lc{" " + g if g else ""}">{_e(n)}</span>' for n, g in by_height[h])
        cols.append(f'<div class="lcol"><div class="lh">{h}板<small>{len(by_height[h])}</small></div>{chips}</div>')
    listed = [(n, g) for n, g in first if g]
    chips = "".join(f'<span class="lc {g}">{_e(n)}</span>' for n, g in listed)
    cols.append(f'<div class="lcol first"><div class="lh">首板<small>{len(first)}</small></div>{chips}'
                + (f'<span class="lmore">其余 {len(first) - len(listed)} 只</span>' if len(first) > len(listed) else "")
                + "</div>")
    return f'<div class="ladder">{"".join(cols)}</div>'


# ------------------------------------------------------------------ theme threads: one row per thread, one cell per day

def thread_strip(rows: list[dict], days: list[str]) -> str:
    """rows: {name, age, live, xuan, key, counts: {day: limit-ups}}; days: the columns, oldest first. A darker cell is
    more limit-ups that day; an empty cell is a day the thread did not exist yet or had ended."""
    if not rows or not days:
        return ""
    top = max((c for r in rows for c in r["counts"].values()), default=1) or 1
    head = "".join(f'<div class="sd{" today" if i == len(days) - 1 else ""}">{_e(d[5:].replace("-", "/"))}</div>'
                   for i, d in enumerate(days))
    body = []
    for r in rows:
        cells = []
        for i, d in enumerate(days):
            c = r["counts"].get(d)
            if c is None:
                cells.append('<div class="sc none"></div>')
                continue
            alpha = 0.12 + 0.88 * (c / top) ** 0.6 if c else 0.06
            cells.append(f'<div class="sc{" today" if i == len(days) - 1 else ""}{" lt" if alpha < 0.45 else ""}" style="--a:{alpha:.2f}" '
                         f'title="{_e(d)} 涨停 {c} 家">{c if c else ""}</div>')
        tag = '<span class="xuan">玄学</span>' if r.get("xuan") else ""
        live = '<span class="live">今日主线</span>' if r.get("live") else ""
        attrs = f' data-d="{_e(r["key"])}" tabindex="0" role="button" title="点开看每天的详情"' if r.get("key") else ""
        body.append(f'<div class="srow{" open" if r.get("key") else ""}"{attrs}><div class="sn">{tag}<b>{_e(r["name"])}</b>'
                    f'<small>第{r["age"]}天</small>{live}</div>{"".join(cells)}</div>')
    return (f'<div class="scroll"><div class="strip" style="--n:{len(days)}"><div class="srow hd"><div class="sn"></div>{head}</div>'
            f'{"".join(body)}</div></div>')


# ------------------------------------------------------------------ intraday mini chart

def intraday_chart(bars: pd.DataFrame, preclose: float, up: float | None, source: str) -> str:
    """Price vs the previous close through the day (x = trading minute 0..240), the limit-up price dashed, volume as
    thin bars below. `bars`: minute, close, volume. `source` is written under the chart."""
    if bars is None or bars.empty or not _known(preclose) or preclose <= 0:
        return '<div class="ic none">无分时数据</div>'
    w, h, vh = 240, 70, 16
    b = bars.dropna(subset=["close"]).sort_values("minute")
    if b.empty:
        return '<div class="ic none">无分时数据</div>'
    pct = (b["close"] / preclose - 1).tolist()
    up_pct = up / preclose - 1 if _known(up) else None
    lo = min(min(pct), -0.02, 0.0)
    hi = max(max(pct), up_pct if up_pct is not None else 0.0, 0.02)
    span = hi - lo

    def y(p):
        return 2 + (h - 4) * (hi - p) / span
    xs = [w * m / 240 for m in b["minute"]]
    line = " ".join(f"{x:.1f},{y(p):.1f}" for x, p in zip(xs, pct))
    zero = y(0.0)
    area = f"{xs[0]:.1f},{zero:.1f} {line} {xs[-1]:.1f},{zero:.1f}"
    tone = "upc" if pct[-1] >= 0 else "dnc"
    vol = b["volume"].fillna(0).to_numpy()
    vmax = vol.max() if len(vol) and vol.max() > 0 else 1
    bw = max(w / 241, 0.6) if len(b) > 60 else w / 48
    vbars = "".join(f'<rect x="{x - bw / 2:.1f}" y="{h + vh - vh * v / vmax:.1f}" width="{bw:.2f}" height="{vh * v / vmax:.1f}"/>'
                    for x, v in zip(xs, vol) if v > 0)
    limit = (f'<line class="lim" x1="0" x2="{w}" y1="{y(up_pct):.1f}" y2="{y(up_pct):.1f}"/>'
             f'<text class="lt" x="{w - 2}" y="{y(up_pct) - 2 if y(up_pct) > 12 else y(up_pct) + 10:.1f}">涨停 {up_pct:+.0%}</text>') if up_pct is not None else ""
    return (f'<div class="ic"><svg viewBox="0 0 {w} {h + vh}" preserveAspectRatio="none" aria-hidden="true">'
            f'<line class="mid" x1="{w / 2}" x2="{w / 2}" y1="0" y2="{h}"/>'
            f'<line class="zero" x1="0" x2="{w}" y1="{zero:.1f}" y2="{zero:.1f}"/>{limit}'
            f'<polygon class="ar {tone}" points="{area}"/><polyline class="ln {tone}" points="{line}"/>'
            f'<g class="vol">{vbars}</g></svg>'
            f'<div class="icx"><span>9:30</span><span>11:30/13:00</span><span>15:00</span></div>'
            f'<div class="ics">收盘 {pct[-1]:+.1%} · {_e(source)}</div></div>')


# ------------------------------------------------------------------ position: price and MA250 against the one-year high

def position_bar(vs_high, vs_ma) -> str:
    """A scale from 60% below the one-year high (left) to the high (right): a dot for the price, a tick for MA250."""
    if not _known(vs_high):
        return '<div class="pb none">位置未知</div>'
    lo = min(-0.6, float(vs_high) - 0.05)

    def x(v):
        return max(0.0, min(100.0, (v - lo) / -lo * 100))
    ma = (1 + float(vs_high)) / (1 + float(vs_ma)) - 1 if _known(vs_ma) and float(vs_ma) > -1 else None
    tick = f'<i class="ma" style="left:{x(ma):.1f}%" title="年线"></i>' if ma is not None else ""
    words = f"距一年高 {float(vs_high):.0%}" + (f" · 年线{'上' if float(vs_ma) >= 0 else '下'} {abs(float(vs_ma)):.0%}"
                                              if _known(vs_ma) else "")
    return (f'<div class="pb"><div class="pt">{tick}<b style="left:{x(float(vs_high)):.1f}%"></b></div>'
            f'<div class="pl"><span>{lo:.0%}</span><span>{_e(words)}</span><span>一年高</span></div></div>')


# ------------------------------------------------------------------ review: hit rate vs big loss per group

def rate_bars(rows: list[tuple[str, str, float | None, float | None, int]], base: tuple[float | None, float | None]) -> str:
    """rows: (label, css class, out5, bigloss1, n). Two bars per group with the all-limit-ups baseline as a dashed
    mark on each scale; a missing rate shows as 待定."""
    m1 = max([v for _, _, v, _, _ in rows if _known(v)] + [base[0] or 0, 0.05])
    m2 = max([v for _, _, _, v, _ in rows if _known(v)] + [base[1] or 0, 0.05])

    def bar(v, top, cls, ref):
        mark = f'<i style="left:{ref / top * 100:.1f}%"></i>' if _known(ref) else ""
        if not _known(v):
            return f'<div class="rb"><div class="rt">{mark}</div><span class="dim">待定</span></div>'
        return (f'<div class="rb"><div class="rt"><b class="{cls}" style="width:{v / top * 100:.1f}%"></b>{mark}</div>'
                f'<span>{v:.0%}</span></div>')
    out = []
    for label, cls, o5, bl, n in rows:
        out.append(f'<div class="rr"><div class="rl"><span class="chip {cls}">{_e(label)}</span><small>{n} 次</small></div>'
                   f'{bar(o5, m1, "go", base[0])}{bar(bl, m2, "bl", base[1])}</div>')
    legend = ('<div class="rr hd"><div class="rl"></div><div class="rb">5 日走出来</div><div class="rb">次日大面</div></div>')
    note = ('<p class="note">虚线竖标是同日全部主板涨停股的比例（对照）。走出来越长越好，大面越长越差。'
            + (f'对照：走出来 {base[0]:.0%}，大面 {base[1]:.0%}。' if _known(base[0]) and _known(base[1]) else "") + '</p>')
    return f'<div class="rates">{legend}{"".join(out)}</div>{note}'


# ------------------------------------------------------------------ auction gaps

def gap_bars(rows: list[tuple[str, float | None, str]]) -> str:
    """rows: (name, gap, label). Bars to the right are higher opens, to the left lower; a missing gap shows 缺失."""
    known = [abs(g) for _, g, _ in rows if _known(g)]
    top = max(known + [0.02])
    out = []
    for name, g, lab in rows:
        if not _known(g):
            out.append(f'<div class="gr"><span class="gn">{_e(name)}</span><div class="gt"></div><span class="dim">缺失</span>'
                       '<span class="gl2"></span></div>')
            continue
        width = abs(g) / top * 50
        side = f"left:50%;width:{width:.1f}%" if g >= 0 else f"left:{50 - width:.1f}%;width:{width:.1f}%"
        out.append(f'<div class="gr"><span class="gn">{_e(name)}</span><div class="gt"><b class="{"upc" if g >= 0 else "dnc"}" '
                   f'style="{side}"></b></div><span class="gv2">{g:+.1%}</span>'
                   + f'<span class="gl2">{_e(lab)}</span></div>')
    return f'<div class="gaps">{"".join(out)}</div>'


CSS = """
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kpi{background:var(--panel);border-radius:16px;box-shadow:var(--glow);padding:12px 14px;display:flex;flex-direction:column;gap:2px}
.kl{color:var(--dim);font-size:12px}.kn{font-size:26px;font-weight:800;line-height:1.2;font-variant-numeric:tabular-nums}
.ks{color:var(--dim);font-size:12px}.trend{width:100%;height:40px;margin:4px 0;overflow:visible}
.trend .ln{fill:none;stroke:currentColor;stroke-width:1.6;vector-effect:non-scaling-stroke}
.trend .ar{fill:currentColor;opacity:.12}.trend .now{fill:currentColor}.trend .hit{fill:transparent}
.kpi.t-mood{color:var(--cyan)}.kpi.t-heat{color:var(--amber)}.kpi.t-pain{color:var(--red)}.kpi.t-lu{color:var(--green)}
.kpi.t-ld{color:var(--red)}.kpi.t-pro{color:var(--violet)}.kpi .kn{color:var(--fg)}
.ladder{display:flex;gap:10px;overflow-x:auto;padding-bottom:4px}
.lcol{flex:0 0 auto;min-width:96px;display:flex;flex-direction:column;gap:6px;padding:10px;border-radius:12px;
background:rgba(255,255,255,.03);border:1px solid var(--line)}.lcol.first{min-width:150px}
.lh{font-weight:800;font-size:16px;display:flex;align-items:baseline;gap:6px}.lh small{font-weight:400}
.lc{font-size:13px;padding:3px 8px;border-radius:8px;background:rgba(255,255,255,.06);white-space:nowrap}
.lc.g-lead,.lc.g-ladder,.lc.g-watch,.lc.g-risk{color:#08101d;font-weight:700}.lc.g-lead{background:var(--lead)}.lc.g-ladder{background:var(--ladder)}.lc.g-watch{background:var(--watch)}.lc.g-risk{background:var(--risk)}.lmore{color:var(--dim);font-size:12px}
.strip{display:grid;gap:3px;min-width:calc(170px + var(--n) * 34px)}
.srow{display:grid;grid-template-columns:170px repeat(var(--n),minmax(30px,1fr));gap:3px;align-items:center}
.srow.open{cursor:pointer;border-radius:8px}.srow.open:hover{background:rgba(56,225,255,.06)}
.sn{display:flex;align-items:center;gap:6px;font-size:13px;overflow:hidden;white-space:nowrap}.sn b{overflow:hidden;text-overflow:ellipsis;min-width:3.2em}.sn small{flex:none}
.sd{font-size:11px;color:var(--dim);text-align:center}.sd.today{color:var(--cyan);font-weight:700}
.sc{height:26px;border-radius:5px;background:rgba(251,191,36,var(--a));display:flex;align-items:center;justify-content:center;
font-size:11px;font-weight:700;color:#1b1206}.sc.none{background:transparent;border:1px dashed rgba(255,255,255,.05)}
.sc.today{outline:1px solid var(--cyan)}.sc.lt{color:var(--fg)}
.ic{margin:8px 0 4px}.ic svg{width:100%;height:86px;display:block}.ic.none{color:var(--dim);font-size:12px}
.ic .zero{stroke:rgba(255,255,255,.25);stroke-width:1;stroke-dasharray:2 3;vector-effect:non-scaling-stroke}
.ic .mid{stroke:rgba(255,255,255,.08);vector-effect:non-scaling-stroke}
.ic .lim{stroke:var(--red);stroke-width:1;stroke-dasharray:4 3;vector-effect:non-scaling-stroke}
.ic .lt{fill:var(--red);font-size:9px;text-anchor:end}
.ic .ln{fill:none;stroke-width:1.5;vector-effect:non-scaling-stroke}.ic .ln.upc{stroke:var(--red)}.ic .ln.dnc{stroke:var(--green)}
.ic .ar{opacity:.14}.ic .ar.upc{fill:var(--red)}.ic .ar.dnc{fill:var(--green)}.ic .vol rect{fill:rgba(138,150,173,.45)}
.icx{display:flex;justify-content:space-between;font-size:10px;color:var(--dim)}.ics{font-size:12px;color:var(--dim)}
.pb{margin:6px 0}.pt{position:relative;height:6px;border-radius:6px;background:linear-gradient(90deg,rgba(255,93,108,.25),rgba(52,211,153,.35))}
.pt b{position:absolute;top:-3px;width:12px;height:12px;margin-left:-6px;border-radius:50%;background:var(--fg);box-shadow:0 0 8px var(--cyan)}
.pt i{position:absolute;top:-4px;width:2px;height:14px;margin-left:-1px;background:var(--amber)}
.pl{display:flex;justify-content:space-between;font-size:11px;color:var(--dim);margin-top:4px}.pb.none{color:var(--dim);font-size:12px}
.rates{display:grid;gap:8px}.rr{display:grid;grid-template-columns:150px 1fr 1fr;gap:12px;align-items:center}
.rr.hd{color:var(--dim);font-size:12px}.rl{display:flex;align-items:center;gap:6px}
.rb{display:flex;align-items:center;gap:8px;font-variant-numeric:tabular-nums}
.rt{position:relative;flex:1;height:12px;border-radius:6px;background:rgba(255,255,255,.06)}
.rt b{position:absolute;left:0;top:0;bottom:0;border-radius:6px}.rt b.go{background:var(--cyan)}.rt b.bl{background:var(--red)}
.rt i{position:absolute;top:-3px;bottom:-3px;border-left:2px dashed var(--fg);opacity:.7}
.gaps{display:grid;gap:6px}.gr{display:grid;grid-template-columns:90px 1fr 56px 64px;gap:10px;align-items:center;font-size:13px}
.gt{position:relative;height:12px;background:rgba(255,255,255,.04);border-radius:4px}
.gt:before{content:"";position:absolute;left:50%;top:-3px;bottom:-3px;border-left:1px solid rgba(255,255,255,.3)}
.gt b{position:absolute;top:0;bottom:0;border-radius:4px}.gt b.upc{background:var(--red)}.gt b.dnc{background:var(--green)}
.gv2{font-variant-numeric:tabular-nums;text-align:right}.gl2{color:var(--amber);font-size:12px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px;margin-bottom:10px}
details.more-t summary{color:var(--cyan);font-weight:700;margin:6px 0}
@media (max-width:600px){.rr{grid-template-columns:96px 1fr 1fr;gap:8px}.srow{grid-template-columns:120px repeat(var(--n),minmax(26px,1fr))}
.strip{min-width:calc(120px + var(--n) * 29px)}.gr{grid-template-columns:72px 1fr 50px}.gl2{display:none}}
"""
