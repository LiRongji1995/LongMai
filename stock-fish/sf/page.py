"""Local HTML page (本地页面) for one saved pre-market list: alerts first, a one-screen dashboard, then the full tables.

Built only from files already saved (the list run, the latest auction snapshot of its target, the latest review,
judgment scores and the scheduled-job status), so it never recomputes or changes anything. Written once under
SF_HOME/pages/<target>/<stamp>.html; one self-contained file, no network, no external scripts or fonts.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import listing, store

GROUPS = ("主线龙头", "连板梯队", "观察", "高波动")
GROUP_CLASS = {"主线龙头": "g-lead", "连板梯队": "g-ladder", "观察": "g-watch", "高波动": "g-risk"}


def _e(x) -> str:
    return "" if x is None or (isinstance(x, float) and pd.isna(x)) else html.escape(str(x))


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
        stored = sorted(x.stem for x in (store.home() / "bars").glob("*.parquet"))[-5:]
        for day in stored:
            f = cap / f"{day}.json"
            if f.exists() and json.loads(f.read_text()).get("status", "").startswith("FAILED") and \
                    not (store.home() / "intraday" / "minute" / f"{day}.parquet").exists():
                out.append(f"{day} 的盘中分时没有抓到，仍在数据源约 5 个交易日的窗口内：运行 sf intraday --backfill 补抓")
    return out


def _latest(path: Path) -> Path | None:
    if not path.exists():
        return None
    items = sorted(p for p in path.iterdir() if p.is_dir())
    return items[-1] if items else None


# ------------------------------------------------------------------ small visual pieces (inline SVG / CSS only)

def ring(value, label: str, sub: str, tone: str) -> str:
    """A circular gauge for a 0..1 value (unknown shows as an empty ring with '?')."""
    known = value is not None and not (isinstance(value, float) and pd.isna(value))
    v = min(max(float(value), 0.0), 1.0) if known else 0.0
    r, c = 34, 2 * 3.14159 * 34
    return (f'<div class="gauge {tone}"><svg viewBox="0 0 84 84" aria-hidden="true"><circle class="trk" cx="42" cy="42" r="{r}"/>'
            f'<circle class="val" cx="42" cy="42" r="{r}" stroke-dasharray="{c * v:.1f} {c:.1f}"/></svg>'
            f'<div class="gv">{f"{v * 100:.0f}" if known else "?"}</div><div class="gl">{_e(label)}</div>'
            f'<div class="gs">{_e(sub)}</div></div>')


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


def _stock_card(r: pd.Series, vs_hi) -> str:
    risk = f'<div class="risk">{_e(r["风险提示"])}</div>' if r.get("风险提示") else ""
    return (f'<article class="stock"><div class="sh"><span class="nm">{_e(r["名称"])}</span>'
            f'<span class="chip {GROUP_CLASS.get(r["分组"], "")}">{_e(r["分组"])}</span></div>'
            f'<div class="role">{_e(r["角色"])}</div><div class="kv">{_e(r.get("成交额", ""))}</div>'
            f'<div class="kv">{_e(r.get("位置", ""))}</div>{posbar(vs_hi)}'
            f'<div class="plan">{_e(r["参与建议"])}</div>{risk}</article>')


def render(run: Path) -> str:
    summary = json.loads((run / "summary.json").read_text())
    header = listing.header(summary)
    table = pd.read_csv(run / "盘前清单.csv", dtype=str).fillna("")
    pos = {}
    if (run / "facts.parquet").exists():
        f = pd.read_parquet(run / "facts.parquet")
        if "vs_high250" in f and "name" in f:
            pos = dict(zip(f["name"], f["vs_high250"]))
    target, obs = summary["target"], summary["observation"]
    al = alerts(summary, header)
    g = summary["gate"]
    tp = summary.get("temperature") or {}
    focus = table[table["参与建议"].str.startswith("候选（由你决定）")]
    risky = table[(table["分组"] == "高波动") | table["角色"].str.startswith("空间板")]

    nav = "".join(f'<a href="#{k}">{v}</a>' for k, v in (("dash", "总览"), ("threads", "题材线索"), ("focus", "候选"),
                                                        ("auction", "竞价"), ("groups", "完整清单"), ("review", "复盘")))
    parts = [f'<div class="top"><div class="brand"><span class="dot"></span>龙脉 <i>LONGMAI</i></div><nav>{nav}</nav></div>',
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
    parts.append(
        '<section id="dash" class="dash">'
        + ring(g.get("mood_level"), "情绪分位", f"{g.get('mood_tier', '?')}档", "t-mood")
        + ring(tp.get("heat"), "热度", f"较前3日{tp.get('trend', '?')}{trend}", "t-heat")
        + ring(tp.get("pain"), "亏钱效应", tp.get("cell", ""), "t-pain")
        + f'<div class="tile"><div class="tl">规则仓位上限</div><div class="slots">{dots}</div>'
          f'<div class="tv">{slots} 只 × 1/4</div><div class="tl">监管温度 {_e(g.get("reg_tier"))}'
          f'（深市周处置 {_e(g.get("szse_cases"))} 起）</div></div>'
        + '</section>')
    if tp.get("text"):
        parts.append(f'<section class="note">{_e(tp["text"])}</section>')

    cards = []
    for th in summary.get("threads") or []:
        share = th["today"] / th["peak"] if th.get("peak") else 0
        lead = (f'<div class="kv">清单内 {_e("、".join(th["listed"]))}</div>'
                f'<div class="kv">{_e(th["listed"][0])}：{_e(th.get("lead_position"))}</div>') if th.get("listed") else ""
        cards.append(
            f'<article class="thread"><div class="th"><span class="age">第{th["age"]}天</span>'
            f'<span class="nm">{_e("/".join(th["boards"][:3]))}{"等" if len(th["boards"]) > 3 else ""}</span>'
            f'{"<span class=live>今日主线</span>" if th.get("mainline_today") else ""}</div>'
            f'{spark(th.get("series") or [])}'
            f'<div class="kv">今日 <b>{th["today"]}</b> 家 · 峰值 {th["peak"]} 家（第{th["peak_age"]}天） · '
            f'剩 {share * 100:.0f}%</div>{lead}</article>')
    fates = summary.get("high_fate") or []
    fate_html = "".join(f'<div class="fate {"up" if f.get("sealed") else "down"}"><b>{_e(f["name"])}</b>'
                        f'<span>{f["height"]}板 → {_e(f["fate"])}</span></div>' for f in fates)
    mainl = "".join(f'<div class="ml"><b>{_e(m["board"])}</b><span>今{m["lu"]} · 3日{m["lu3"]} · 连续{m["days"]}天 · '
                    f'成交占比{m.get("share", 0) * 100:.1f}%</span></div>' for m in summary.get("mainlines") or [])
    parts.append(f'<section id="threads"><h2>主线与题材线索 <small>只作描述，不改分组</small></h2>'
                 f'<div class="mls">{mainl}</div><div class="grid">{"".join(cards) or "<p>没有线索记录（旧版清单）。</p>"}</div>'
                 + (f'<h3>高标结局</h3><div class="fates">{fate_html}</div>' if fate_html else "") + '</section>')

    fcards = "".join(_stock_card(r, pos.get(r["名称"])) for _, r in focus.iterrows())
    parts.append(f'<section id="focus"><h2>仓位内的候选 <small>规则给出，买卖由你决定</small></h2>'
                 f'<div class="grid">{fcards or "<p>今日没有仓位内的候选。</p>"}</div>')
    if len(risky):
        parts.append('<h3>风险最大</h3><div class="grid">' +
                     "".join(_stock_card(r, pos.get(r["名称"])) for _, r in risky.iterrows()) + "</div>")
    parts.append("</section>")

    auction = _latest(store.home() / "auction" / target)
    if auction is not None and (auction / "snapshot.parquet").exists():
        s = pd.read_parquet(auction / "snapshot.parquet")
        s = s.assign(竞价缺口=(s["gap"] * 100).round(1).astype(str) + "%",
                     竞价标签=s["label"].map({"deny": "竞价否定（5日内较少走出来）", "limit_open": "涨停开",
                                            "neutral": "中性", "confirm": "中性", "description": "只描述"}).fillna(s["label"]))
        parts.append(f'<section id="auction"><h2>竞价快照 <small>{_e(auction.name)}</small></h2>' +
                     _table(s.rename(columns={"name": "名称", "group": "分组", "bands": "同类开盘后（历史）"}),
                            ["名称", "分组", "竞价缺口", "竞价标签", "同类开盘后（历史）"]) + "</section>")
    else:
        parts.append('<section id="auction"><h2>竞价快照</h2><p class="dim">还没有这个交易日的竞价快照（09:25 后 sf auction）。</p></section>')

    tabs, panes = [], []
    for i, grp in enumerate(g_ for g_ in GROUPS if (table["分组"] == g_).any()):
        part = table[table["分组"] == grp]
        tabs.append(f'<button class="tab {GROUP_CLASS[grp]}{" on" if i == 0 else ""}" data-p="p{i}">{grp} {len(part)}</button>')
        panes.append(f'<div class="pane{" on" if i == 0 else ""}" id="p{i}">' + _table(part, listing.VIEW[1:], pos) + "</div>")
    parts.append(f'<section id="groups"><h2>完整清单</h2><div class="tabs">{"".join(tabs)}</div>{"".join(panes)}</section>')

    rev = _latest(store.home() / "reviews")
    rv = ""
    if rev is not None and (rev / "stats.csv").exists():
        st = pd.read_csv(rev / "stats.csv").rename(columns={"Unnamed: 0": "组"})
        rv += f'<h3>最近一次复盘 <small>{_e(rev.name)}</small></h3>' + _table(st.round(3), list(st.columns))
    sc = store.home() / "journal" / "scores"
    if sc.exists() and sorted(sc.glob("*.csv")):
        j = pd.read_csv(sorted(sc.glob("*.csv"))[-1], dtype=str).fillna("")
        rv += '<h3>判断日记</h3>' + _table(j.tail(15), ["observation", "kind", "subject", "text", "result"])
    parts.append(f'<section id="review"><h2>复盘</h2>{rv or "<p class=dim>还没有复盘记录。</p>"}</section>')
    parts.append('<section class="foot"><details><summary>表头原文</summary><pre>' + _e("\n".join(header)) +
                 "</pre></details><p>本页只读取已保存的文件，不重新计算。本系统不交易，买卖由你决定；非投资建议。</p></section>")
    return TEMPLATE.replace("{{title}}", f"龙脉 {target}").replace("{{body}}", "\n".join(parts))


def write(run: Path) -> Path:
    summary = json.loads((run / "summary.json").read_text())
    out = store.home() / "pages" / summary["target"]
    out.mkdir(parents=True, exist_ok=True)
    path = out / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".html")
    with open(path, "x", encoding="utf-8") as f:
        f.write(render(run))
    return path


TEMPLATE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{title}}</title>
<style>
:root{--bg:#07090f;--bg2:#0c1220;--panel:rgba(18,26,44,.72);--line:rgba(120,160,255,.14);--fg:#e8edf7;--dim:#8a96ad;
--cyan:#38e1ff;--violet:#a78bfa;--amber:#fbbf24;--red:#ff5d6c;--green:#34d399;--lead:#ff8a3d;--ladder:#5b8cff;
--watch:#94a3b8;--risk:#ff5d6c;--glow:0 0 0 1px var(--line),0 10px 40px -12px rgba(56,225,255,.18)}
*{box-sizing:border-box}html{scroll-behavior:smooth;background:var(--bg)}
body{margin:0;color:var(--fg);font:14px/1.6 -apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
background:radial-gradient(1200px 600px at 10% -10%,rgba(56,225,255,.12),transparent 60%),
radial-gradient(900px 500px at 100% 0%,rgba(167,139,250,.14),transparent 60%),linear-gradient(var(--bg),var(--bg2));
background-attachment:fixed;min-height:100vh}
body:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.35;
background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);
background-size:44px 44px;-webkit-mask-image:linear-gradient(to bottom,#000,transparent 70%);mask-image:linear-gradient(to bottom,#000,transparent 70%)}
main{position:relative;max-width:1240px;margin:0 auto;padding:0 16px 40px}
.top{position:sticky;top:0;z-index:5;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;
-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);background:linear-gradient(var(--bg) 40%,transparent)}
.brand{font-weight:800;letter-spacing:.08em;display:flex;align-items:center;gap:8px;white-space:nowrap}
.brand i{font-style:normal;color:var(--dim);font-weight:500;font-size:12px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--cyan);box-shadow:0 0 12px var(--cyan)}
nav{display:flex;gap:4px;overflow-x:auto}nav a{color:var(--dim);text-decoration:none;padding:4px 10px;border-radius:999px;white-space:nowrap;font-size:13px}
nav a:hover{color:var(--fg);background:var(--panel)}
.hero{padding:18px 0 6px;display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 18px}
.date{font-size:clamp(34px,7vw,60px);font-weight:800;letter-spacing:-.02em;line-height:1;
background:linear-gradient(90deg,#fff,var(--cyan) 55%,var(--violet));-webkit-background-clip:text;background-clip:text;color:transparent}
.meta{color:var(--dim)}.tag{padding:2px 12px;border-radius:999px;border:1px solid var(--violet);color:var(--violet);font-weight:600}
section{background:var(--panel);border-radius:16px;padding:16px 18px;margin:14px 0;box-shadow:var(--glow)}
h2{font-size:17px;margin:0 0 12px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}h3{font-size:14px;margin:16px 0 8px;color:var(--dim)}
small{color:var(--dim);font-weight:400;font-size:12px}.dim{color:var(--dim)}
.alert{background:linear-gradient(135deg,rgba(255,93,108,.18),rgba(251,191,36,.08));box-shadow:0 0 0 1px rgba(255,93,108,.45)}
.pulse{width:10px;height:10px;border-radius:50%;background:var(--red);box-shadow:0 0 0 0 rgba(255,93,108,.7);animation:pl 1.6s infinite}
@keyframes pl{70%{box-shadow:0 0 0 10px rgba(255,93,108,0)}100%{box-shadow:0 0 0 0 rgba(255,93,108,0)}}
.ok{color:var(--green)}ul{margin:0;padding-left:20px}
.dash{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;background:none;box-shadow:none;padding:0}
.gauge,.tile{position:relative;background:var(--panel);border-radius:16px;box-shadow:var(--glow);padding:14px;text-align:center}
.gauge svg{width:96px;height:96px;transform:rotate(-90deg)}.trk{fill:none;stroke:rgba(255,255,255,.07);stroke-width:8}
.val{fill:none;stroke:currentColor;stroke-width:8;stroke-linecap:round;filter:drop-shadow(0 0 6px currentColor)}
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
.spark rect.last{fill:var(--cyan);filter:drop-shadow(0 0 4px var(--cyan))}
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
.foot{color:var(--dim);font-size:12px}pre{white-space:pre-wrap}summary{cursor:pointer}
@media (max-width:600px){.gauge svg{width:80px;height:80px}.gv{top:38px;font-size:20px}nav{display:none}}
</style></head><body><main>
{{body}}
</main>
<script>
document.querySelectorAll('.tab').forEach(function(b){b.addEventListener('click',function(){
document.querySelectorAll('.tab,.pane').forEach(function(x){x.classList.remove('on')});
b.classList.add('on');document.getElementById(b.dataset.p).classList.add('on');});});
</script></body></html>
"""
