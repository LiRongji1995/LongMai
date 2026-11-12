"""Name-based contagion (名字/谐音炒作): stocks rallying together because their names share a character
or a homophone (e.g. 华字辈; 羊年 → 阳/洋/扬/杨). Concept boards do not capture this, so groups are built
from the stock names themselves and tested daily for an abnormal concentration of limit-ups.

Groups
- char:   every stock whose cleaned name contains the character (e.g. 华)
- pinyin: every stock whose name contains a character with that toneless pinyin (e.g. yang)
- word:   every stock whose name contains the same two-character word (e.g. 东方)
Characters that encode the industry or the legal form (科, 技, 股, 份, 电, 药, ...) are excluded: a
sector rally would otherwise look like name contagion.

Daily test: with market limit-up rate p (SSE/SZSE A shares that traded), a group of n traded members
with k limit-ups gets the binomial tail probability P(X >= k). A group is "hot" when k >= 3 and the
tail probability is below 1e-4. Names are today's snapshot (renames are not tracked).
"""
from __future__ import annotations

import math
import re
from functools import lru_cache

import numpy as np
import pandas as pd

VERSION = "SF_NAME_CONTAGION_V1"
INDUSTRY_WORDS = (
    "股份", "集团", "科技", "控股", "实业", "发展", "电子", "电气", "电器", "能源", "新材", "材料", "药业", "医药",
    "制药", "生物", "医疗", "机械", "汽车", "电力", "银行", "证券", "保险", "地产", "置业", "化工", "化学", "环境",
    "环保", "信息", "网络", "软件", "通信", "智能", "技术", "工业", "有限", "公司", "国际", "中国", "传媒", "文化",
    "食品", "农业", "矿业", "资源", "钢铁", "水泥", "设备", "重工", "精密", "动力", "光电", "半导体", "照明", "数据",
    "数码", "微电", "电缆", "线缆", "装备", "制造", "建设", "建筑", "工程", "投资", "物流", "航空", "港口", "交通",
    "高速", "铁路", "能科", "新能", "储能", "光伏", "风电", "锂业", "钴业", "铝业", "铜业", "金属", "稀土", "黄金",
    "纸业", "包装", "纺织", "服饰", "家居", "家电", "酒业", "乳业", "饮料", "旅游", "酒店", "商业", "百货", "零售",
    "贸易", "物产", "传感", "芯片", "自动化", "仪器", "仪表", "检测", "测控", "电池", "电源", "陶瓷", "玻璃", "塑胶",
    "橡胶", "轮胎", "管业", "阀门", "泵业", "轴承", "模具", "印刷", "影视", "游戏", "教育", "体育", "健康", "养殖",
    "种业", "牧业", "渔业", "水务", "燃气", "热电", "核电", "石化", "石油", "天然气", "煤业", "煤电", "电工", "机电",
    "车辆", "船舶", "海工", "军工", "防务", "卫星", "导航", "激光", "显示", "光学", "新能源", "智造", "互联", "云",
    "出版", "电视", "广电", "期货", "信托", "租赁", "创投", "园区", "开发", "城投", "农商", "机场", "航运", "客车",
    # commodities and equipment words: a sector move, not name contagion
    "钨钼锡锑镍钒钛锰锗镓铟锆镁硅磷氟锌铅银铂", "变压", "变电", "退市")
STOP_CHARS = set("".join(INDUSTRY_WORDS)) | set("ABＡＢ")
PREFIX = re.compile(r"^(\*?S?\*?ST|XD|XR|DR|N|C|U|W|退市|退)+")


def clean(name: str) -> str:
    name = PREFIX.sub("", str(name).strip())
    return "".join(ch for ch in name if "一" <= ch <= "鿿")


@lru_cache(maxsize=None)
def _pinyin(ch: str) -> str:
    from pypinyin import lazy_pinyin
    return lazy_pinyin(ch)[0]


def groups(names: pd.Series, min_members: int = 3) -> dict[str, list[str]]:
    """names: code -> stock name. Returns label -> member codes."""
    by_char, by_pin, chars_of_pin, by_word = {}, {}, {}, {}
    for code, name in names.items():
        seen_c, seen_p = set(), set()
        text = clean(name)
        for word in {text[i:i + 2] for i in range(len(text) - 1)}:
            if not (set(word) & STOP_CHARS):
                by_word.setdefault(word, []).append(code)
        for ch in text:
            if ch in STOP_CHARS:
                continue
            if ch not in seen_c:
                by_char.setdefault(ch, []).append(code)
                seen_c.add(ch)
            py = _pinyin(ch)
            chars_of_pin.setdefault(py, set()).add(ch)
            if py not in seen_p:
                by_pin.setdefault(py, []).append(code)
                seen_p.add(py)
    out = {f"字·{c}": m for c, m in by_char.items() if len(m) >= min_members}
    out.update({f"词·{w}": m for w, m in by_word.items() if len(m) >= min_members})
    for py, m in by_pin.items():
        chars = chars_of_pin[py]
        if len(m) >= min_members and len(chars) > 1:  # single-character pinyin groups duplicate the char group
            out[f"音·{py}({''.join(sorted(chars))})"] = m
    return out


def binom_tail(k: int, n: int, p: float) -> float:
    if k <= 0:
        return 1.0
    if p <= 0:
        return 0.0
    logs = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * math.log(p) + (n - i) * math.log1p(-p)
            for i in range(k, n + 1)]
    m = max(logs)
    return float(min(1.0, math.exp(m) * sum(math.exp(x - m) for x in logs)))


def scan(limit_up: pd.DataFrame, traded: pd.DataFrame, grp: dict[str, list[str]], t: int,
         min_k: int = 3, alpha: float = 1e-4) -> pd.DataFrame:
    """Hot name groups on session index t. limit_up/traded: day x code booleans (all SSE/SZSE A shares)."""
    lu, tr = limit_up.iloc[t].fillna(False).astype(bool), traded.iloc[t].fillna(False).astype(bool)
    p = lu[tr].mean() if tr.any() else 0.0
    lu3 = limit_up.iloc[max(0, t - 2):t + 1].fillna(False).astype(bool).any()
    rows = []
    for label, members in grp.items():
        idx = pd.Index(members).intersection(lu.index)
        n = int(tr.reindex(idx).fillna(False).sum())
        k = int(lu.reindex(idx).fillna(False).sum())
        if k < min_k or n == 0:
            continue
        pv = binom_tail(k, n, p)
        if pv < alpha:
            rows.append(dict(group=label, members=n, limit_ups=k, limit_ups_3d=int(lu3.reindex(idx).fillna(False).sum()),
                             expected=round(n * p, 2), p_value=pv,
                             codes=list(idx[lu.reindex(idx).fillna(False).to_numpy()])))
    frame = pd.DataFrame(rows)
    if not len(frame):
        return frame
    # most specific first on ties: word, then character, then homophone group
    frame["kind"] = frame["group"].str[0].map({"词": 0, "字": 1, "音": 2})
    frame = frame.sort_values(["p_value", "kind"]).drop(columns="kind")
    return frame.reset_index(drop=True)


def scan_all(limit_up: pd.DataFrame, traded: pd.DataFrame, grp: dict[str, list[str]], min_k: int = 3,
             alpha: float = 1e-4) -> pd.DataFrame:
    """Vectorized `scan` over every session: rows (date, group, members, limit_ups, p_value)."""
    cols = limit_up.columns
    pos = {c: i for i, c in enumerate(cols)}
    labels = list(grp)
    G = np.zeros((len(labels), len(cols)), dtype=np.float32)
    for gi, label in enumerate(labels):
        for c in grp[label]:
            if c in pos:
                G[gi, pos[c]] = 1.0
    lu = limit_up.fillna(False).to_numpy(dtype=np.float32)
    tr = traded.fillna(False).to_numpy(dtype=np.float32)
    K, N = lu @ G.T, tr @ G.T
    p = lu.sum(axis=1) / np.maximum(tr.sum(axis=1), 1)
    rows = []
    for d, g in zip(*np.nonzero(K >= min_k)):
        pv = binom_tail(int(K[d, g]), int(N[d, g]), float(p[d]))
        if pv < alpha:
            rows.append((limit_up.index[d], labels[g], int(N[d, g]), int(K[d, g]), pv))
    return pd.DataFrame(rows, columns=["date", "group", "members", "limit_ups", "p_value"])


def links(list_codes: list[str], names: pd.Series, strong: pd.Index, universe: pd.Index,
          alpha: float = 1e-3, max_links: int = 8) -> list[dict]:
    """Post-list check: for each listed stock, today's strong stocks (limit-up or +5%) sharing a word,
    character, homophone or 3-digit code suffix with it, kept when the overlap is improbable
    (binomial tail < alpha with the share of strong stocks in the universe as the rate). alpha is strict
    because each list checks ~15 keys for ~10-20 stocks (multiple comparisons)."""
    universe = pd.Index(universe).intersection(names.index)
    strong = pd.Index(strong).intersection(universe)
    if not len(universe) or not len(strong):
        return []
    rate = len(strong) / len(universe)
    cleaned = {c: clean(names[c]) for c in universe}

    def keys(code):
        text = cleaned.get(code, "")
        out = {("词", text[i:i + 2]) for i in range(len(text) - 1) if not (set(text[i:i + 2]) & STOP_CHARS)}
        for ch in text:
            if ch not in STOP_CHARS:
                out.add(("字", ch))
                out.add(("音", _pinyin(ch)))
        out.add(("代码尾号", code[-3:]))
        return out

    index: dict[tuple, set] = {}
    for c in universe:
        for k in keys(c):
            index.setdefault(k, set()).add(c)
    found, seen = [], set()
    for code in list_codes:
        if code not in cleaned:
            continue
        best = []
        for k in keys(code):
            members = index.get(k, set())
            partners = sorted((members & set(strong)) - {code})
            n, kk = len(members), len(partners) + (1 if code in strong else 0)
            if len(partners) < (1 if k[0] in ("词", "代码尾号") else 2):
                continue
            pv = binom_tail(kk, n, rate)
            if pv < alpha:
                best.append((pv, k, partners, n))
        order = {"词": 0, "字": 1, "音": 2, "代码尾号": 3}  # most specific first on ties
        for pv, (kind, key), partners, n in sorted(best, key=lambda x: (x[0], order[x[1][0]]))[:1]:
            sig = (kind, key)
            if sig in seen:
                continue
            seen.add(sig)
            found.append(dict(code=code, name=names[code], kind=kind, key=key, k=len(partners) + 1,
                              expected=round(n * rate, 1), p_value=pv,
                              partners=[names.get(c, c) for c in partners[:6]]))
    return sorted(found, key=lambda x: x["p_value"])[:max_links]



THREADS_VERSION = "SF_XUAN_V1"
XUAN_WINDOW = 20       # sessions of the cumulative test (slow, persistent name hype)
XUAN_MIN_K = 5         # at least this many limit-ups (stock-days) in the window
XUAN_LOOKBACK = 120    # sessions searched back for the start of the current run
XUAN_MAX_GAP = 2       # the run breaks after more than this many sessions with no group hot
XUAN_MAX_GROUPS = 5
XUAN_MIN_STOCKS = 3    # the cumulative test needs limit-ups from at least this many different stocks
XUAN_FAMILY_ALPHA = 0.01  # cumulative test: family-wise error over all name groups (Bonferroni: 0.01 / groups)


def poisson_tail(k: int, lam: float) -> float:
    """P(X >= k) for X ~ Poisson(lam)."""
    if k <= 0:
        return 1.0
    if lam <= 0:
        return 0.0
    logs = [-lam + i * math.log(lam) - math.lgamma(i + 1) for i in range(k)]
    m = max(logs)
    below = math.exp(m) * sum(math.exp(x - m) for x in logs)
    if below < 0.999:
        return float(max(0.0, 1.0 - below))
    logs = [-lam + i * math.log(lam) - math.lgamma(i + 1) for i in range(k, k + 400)]  # far tail: sum it directly
    m = max(logs)
    return float(min(1.0, math.exp(m) * sum(math.exp(x - m) for x in logs)))


def xuan(limit_up: pd.DataFrame, traded: pd.DataFrame, grp: dict[str, list[str]], t: int,
         min_k: int = 3, alpha: float = 1e-4) -> dict | None:
    """All name hype (玄学) of the current run as one thread. Description only; never changes a group or an order.

    A name group is hot on a day when either
      daily:      the `scan` test (>= min_k limit-ups that day, binomial tail < alpha; unchanged), or
      cumulative: over the last XUAN_WINDOW sessions >= XUAN_MIN_K limit-ups (stock-days) from >= XUAN_MIN_STOCKS
                  different stocks, with a Poisson tail below XUAN_FAMILY_ALPHA / (number of groups) against the sum
                  of each day's expected count
                  (catches slow hype such as 羊字辈; one stock's streak alone does not count).
    Groups hot at t or within XUAN_MAX_GAP sessions before it are the active ones, strongest first. A weaker group is
    kept only if it is still hot (either test, recent days) on its members not already in a kept group: otherwise it
    is the same hype seen through another character / word / homophone (e.g. 东 and 方 inside 东方). The run starts at the earliest day reached backwards from t through days on which an active group was
    hot, with gaps of at most XUAN_MAX_GAP sessions (the confirmation day), and is then traced back through the lead-in:
    days on which a member was inside a streak of >= 2 limit-ups (see below); `origin` is the earliest such streak and
    `prelude_top` the tallest. Per day: active groups hot that day and the limit-up stocks of all
    active groups with their streaks."""
    from .threads import _streaks
    lo = max(0, t - XUAN_LOOKBACK - XUAN_WINDOW + 1)
    lu = limit_up.iloc[lo:t + 1].fillna(False).astype(bool)
    tr = traded.iloc[lo:t + 1].fillna(False).astype(bool)
    cols = lu.columns
    colpos = {c: i for i, c in enumerate(cols)}
    labels = [g for g in grp if any(c in colpos for c in grp[g])]
    G = np.zeros((len(labels), len(cols)), dtype=np.float32)
    for gi, g in enumerate(labels):
        for c in grp[g]:
            if c in colpos:
                G[gi, colpos[c]] = 1.0
    L, T = lu.to_numpy(np.float32), tr.to_numpy(np.float32)
    K, N = L @ G.T, T @ G.T                      # day x group: limit-ups, traded members
    rate = L.sum(axis=1) / np.maximum(T.sum(axis=1), 1)
    E = N * rate[:, None]                        # expected limit-ups per day and group
    Kw = pd.DataFrame(K).rolling(XUAN_WINDOW, min_periods=XUAN_WINDOW).sum().to_numpy()
    seen = pd.DataFrame(L).rolling(XUAN_WINDOW, min_periods=XUAN_WINDOW).max().fillna(0).to_numpy(np.float32)
    Dw = seen @ G.T                              # different stocks with a limit-up in the window
    Ew = pd.DataFrame(E).rolling(XUAN_WINDOW, min_periods=XUAN_WINDOW).sum().to_numpy()
    nd = len(lu)
    alpha_cum = XUAN_FAMILY_ALPHA / max(len(labels), 1)  # thousands of groups are tested on every window
    hot = np.zeros((nd, len(labels)), dtype=bool)
    for d, g in zip(*np.nonzero(K >= min_k)):
        hot[d, g] = binom_tail(int(K[d, g]), int(N[d, g]), float(rate[d])) < alpha
    for d, g in zip(*np.nonzero((np.nan_to_num(Kw) >= XUAN_MIN_K) & (Dw >= XUAN_MIN_STOCKS))):
        if not hot[d, g] and Kw[d, g] > 2 * Ew[d, g]:
            hot[d, g] = poisson_tail(int(Kw[d, g]), float(Ew[d, g])) < alpha_cum
    last = nd - 1
    active = [g for g in range(len(labels)) if hot[max(0, last - XUAN_MAX_GAP):, g].any()]
    if not active:
        return None
    def strength(g):
        return poisson_tail(int(Kw[last, g]), float(Ew[last, g])) if not np.isnan(Kw[last, g]) else 1.0
    active.sort(key=lambda g: (strength(g), {"词": 0, "字": 1, "音": 2}.get(labels[g][0], 3)))
    def still_hot(idx: list[int]) -> bool:
        """The two tests on a subset of members (column indices), on the recent days."""
        if not idx:
            return False
        Ls, Ts = L[:, idx], T[:, idx]
        for d in range(max(0, last - XUAN_MAX_GAP), last + 1):
            k, n = int(Ls[d].sum()), int(Ts[d].sum())
            if k >= min_k and binom_tail(k, n, float(rate[d])) < alpha:
                return True
            w = slice(max(0, d - XUAN_WINDOW + 1), d + 1)
            kw, ew = int(Ls[w].sum()), float((Ts[w] * rate[w, None]).sum())
            if kw >= XUAN_MIN_K and int((Ls[w].max(axis=0) > 0).sum()) >= XUAN_MIN_STOCKS and kw > 2 * ew \
                    and poisson_tail(kw, ew) < alpha_cum:
                return True
        return False

    chosen, covered = [], set()
    for g in active:
        members = [c for c in grp[labels[g]] if c in colpos]
        rest = [colpos[c] for c in members if c not in covered]
        if chosen and not still_hot(rest):
            continue
        covered |= set(members)
        chosen.append(g)
        if len(chosen) >= XUAN_MAX_GROUPS:
            break
    any_hot = hot[:, chosen].any(axis=1)
    first = last
    gap = 0
    for d in range(last, -1, -1):
        if any_hot[d]:
            first, gap = d, 0
        else:
            gap += 1
            if gap > XUAN_MAX_GAP and d < first:
                break
    # the lead-in (前奏): statistics confirm a name wave only once enough names follow, often days after its leader
    # started. Walk back from the confirmation day through days on which a member of the kept groups was inside a
    # streak of >= 2 consecutive limit-ups (gaps of at most XUAN_MAX_GAP sessions, at most XUAN_WINDOW sessions back).
    allm_idx = sorted(set(colpos[c] for g in chosen for c in grp[labels[g]] if c in colpos))
    Lm = L[:, allm_idx] > 0
    def in_streak(d: int) -> bool:
        prev = Lm[d - 1] if d > 0 else np.zeros(Lm.shape[1], bool)
        nxt = Lm[d + 1] if d < last else np.zeros(Lm.shape[1], bool)
        return bool((Lm[d] & (prev | nxt)).any())
    confirmed, gap, d = first, 0, first - 1
    while d >= max(0, confirmed - XUAN_WINDOW):
        if in_streak(d):
            first, gap = d, 0
        else:
            gap += 1
            if gap > XUAN_MAX_GAP:
                break
        d -= 1

    def runs_from(d0: int, d1: int) -> list[tuple[int, int, int]]:
        """(column, start, length) of every run of consecutive limit-ups starting in [d0, d1)."""
        out = []
        for j in range(Lm.shape[1]):
            d = d0
            while d < d1:
                if Lm[d, j] and (d == 0 or not Lm[d - 1, j]):
                    n = 0
                    while d + n <= last and Lm[d + n, j]:
                        n += 1
                    out.append((j, d, n))
                    d += n
                else:
                    d += 1
        return out
    origin = prelude_top = None
    if first < confirmed:
        pre = [r for r in runs_from(first, confirmed) if r[2] >= 2]
        if pre:
            o = min(pre, key=lambda r: (r[1], -r[2]))
            top = max(pre, key=lambda r: (r[2], -r[1]))
            origin = dict(code=cols[allm_idx[o[0]]], day=lu.index[o[1]], height=o[2])
            prelude_top = dict(code=cols[allm_idx[top[0]]], day=lu.index[top[1]], end=lu.index[top[1] + top[2] - 1],
                               height=top[2])
    start_capped = first == 0 and lo > 0
    members_of = {labels[g]: [c for c in grp[labels[g]] if c in colpos] for g in chosen}
    allm = sorted(set().union(*members_of.values()))
    daily = lu.iloc[first:last + 1][allm].sum(axis=1)
    days = list(lu.index)
    group_rows = [dict(group=labels[g], label=label_text(labels[g]), members=len(members_of[labels[g]]),
                       window_limit_ups=int(np.nan_to_num(Kw[last, g])), window_expected=round(float(np.nan_to_num(Ew[last, g])), 2),
                       today=int(K[last, g]), expected_today=round(float(E[last, g]), 2), hot_today=bool(hot[last, g]))
                  for g in chosen]
    return dict(kind="xuan", first=days[first], age=last - first + 1, age_capped=bool(start_capped),
                confirmed=days[confirmed], origin=origin, prelude_top=prelude_top,
                boards=[r["label"] for r in group_rows], groups=group_rows,
                today=int(daily.iloc[-1]), peak=int(daily.max()), peak_day=str(daily.idxmax()),
                peak_age=int(np.argmax(daily.to_numpy())) + 1, stocks=len(allm), mainline_today=bool(any_hot[last]),
                series=[int(x) for x in daily.to_numpy()], codes=allm,
                days=[dict(day=days[d], age=d - first + 1, prelude=d < confirmed,
                           boards=[label_text(labels[g]) for g in chosen if hot[d, g]],
                           lu=_streaks(lu, d, allm)) for d in range(first, last + 1)])

def label_text(group: str) -> str:
    """字·洋 -> 名字带「洋」, 词·东方 -> 名字带「东方」, 音·yang(洋阳) -> 谐音「洋阳」."""
    kind, _, key = group.partition("·")
    if kind == "音":
        py, _, chars = key.partition("(")
        return f"谐音「{chars.rstrip(')')}」" if chars else "谐音"
    return f"名字带「{key}」"
