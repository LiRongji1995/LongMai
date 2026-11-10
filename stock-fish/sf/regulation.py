"""Regulatory intensity (监管温度) from the exchanges' weekly supervision reports.

Inputs (built by the research scrapers, see stockfish-data/research/regulation_notes.md section 3):
  research/szse_weekly_supervision.csv   《深市监管动态》, weekly since 2016 (complete)
  research/sse_weekly_supervision.csv    SSE weekly press release (gaps before 2022-H2)

The count used is the number of abnormal-trading cases handled in the week (异常交易行为 处置起数).
A report published on date P is usable from the first session after P. Enforcement is pro-cyclical,
so the count is also normalised by that week's market turnover.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import store

FILES = {"szse": ("szse_weekly_supervision.csv", "abnormal_trading_measures"),
         "sse": ("sse_weekly_supervision.csv", "abnormal_trading_measures_total")}


def weekly() -> pd.DataFrame | None:
    frames = []
    for ex, (name, col) in FILES.items():
        path = store.home() / "research" / name
        if not path.exists():
            continue
        f = pd.read_csv(path, parse_dates=["pub_date"])[["pub_date", col]].rename(columns={col: ex})
        frames.append(f.set_index("pub_date"))
    if not frames:
        return None
    return pd.concat(frames, axis=1).sort_index()


def daily(sessions: list[str], market_amount: pd.Series, window_weeks: int = 52) -> pd.DataFrame:
    """Per-session regulatory intensity known before that session's open.

    Columns: szse_cases, szse_per_turnover, and their percentiles over the trailing `window_weeks` reports.
    """
    w = weekly()
    if w is None:
        return pd.DataFrame(index=sessions)
    w = w[w["szse"].notna()].copy()
    idx = pd.to_datetime(pd.Index(sessions))
    amt = market_amount.copy()
    amt.index = pd.to_datetime(amt.index)
    # turnover of the 5 sessions up to the report date
    roll = amt.rolling(5, min_periods=3).sum()
    w["turnover5"] = roll.reindex(w.index, method="ffill")
    w["szse_per_turnover"] = w["szse"] / (w["turnover5"] / 1e12)
    for c in ("szse", "szse_per_turnover"):
        w[c + "_pct"] = w[c].rolling(window_weeks, min_periods=20).rank(pct=True)
    # usable from the first session strictly after the publication date
    pos = np.searchsorted(idx.values, w.index.values, side="right")
    w = w.assign(first_session=[idx[p] if p < len(idx) else pd.NaT for p in pos]).dropna(subset=["first_session"])
    w = w.drop_duplicates("first_session", keep="last").set_index("first_session")
    out = w[["szse", "szse_per_turnover", "szse_pct", "szse_per_turnover_pct"]].reindex(idx, method="ffill")
    out.index = sessions
    return out.rename(columns={"szse": "szse_cases"})


# ------------------------------------------------------------------ weekly update (SZSE)

SZSE_LIST = "https://www.szse.cn/disclosure/supervision/dynamic/index.html"
SZSE_BASE = "https://www.szse.cn/disclosure/supervision/dynamic/"
UA = {"User-Agent": "Mozilla/5.0"}


def parse_szse(text: str) -> dict:
    """Extract the weekly numbers from a 《深市监管动态》 PDF text."""
    import re
    t = re.sub(r"\s+", "", text)
    seg = t[t.find("市场交易监管动态"):] if "市场交易监管动态" in t else t
    end = seg.find("会员监管动态")
    seg = seg[:end] if end > 0 else seg
    cases = re.search(r"共对(\d+)起证券异常交易行为", seg)
    checked = re.search(r"(\d+)起上市公司重大事项", seg)
    clue = re.search(r"上报证监会(\d+)起|向证监会上报(\d+)起", seg)
    focus = re.findall(r"对([^；。]*?)(?:进行|实行|实施)重点监控", seg)
    return dict(abnormal_trading_measures=int(cases.group(1)) if cases else None,
                company_matters_checked=int(checked.group(1)) if checked else None,
                csrc_clues=next((int(g) for g in clue.groups() if g), None) if clue else None,
                focus_monitor_text=" | ".join(focus)[:200] or None,
                account_suspension_mentioned="Y" if ("暂停" in seg and "账户" in seg) else None)


def update_szse(log=print) -> int:
    """Append reports newer than the last row of szse_weekly_supervision.csv. Returns rows added."""
    import io
    import re

    import pypdf
    import requests
    path = store.home() / "research" / FILES["szse"][0]
    existing = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["pub_date"])
    last = str(existing["pub_date"].max()) if len(existing) else "0000"
    html = requests.get(SZSE_LIST, headers=UA, timeout=30).content.decode("utf-8", "ignore")
    items = re.findall(r"var curHref = '\./([^']+)';.*?var curTitle ='([^']*)'.*?<span class=\"time\">\s*(\d{4}-\d{2}-\d{2})",
                       html, flags=re.S)
    rows = []
    for href, title, date in sorted(items, key=lambda x: x[2]):
        if date <= last:
            continue
        url = href if href.startswith("http") else SZSE_BASE + href
        pdf = store._retry(lambda u: requests.get(u, headers=UA, timeout=60).content, url)
        text = "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
        row = dict(pub_date=date, **parse_szse(text))
        if row["abnormal_trading_measures"] is None:
            log(f"  {date} {title}: case count not found (kept as missing)")
        rows.append(row)
    if rows:
        pd.concat([existing, pd.DataFrame(rows)], ignore_index=True).to_csv(path, index=False)
    return len(rows)
