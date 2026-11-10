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
