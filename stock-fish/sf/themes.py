"""Theme (concept + industry board) membership from Eastmoney via AKShare.

Point-in-time caveats (stated in every output that uses themes):
- Boards are only used on days after their index history starts (`first_date`). Concept boards are often
  created after a theme starts, so using today's board list historically would leak the future.
- Membership is today's snapshot; historical additions/removals are not known.

Files (under meta/):
- theme_boards.parquet      board, kind (concept|industry), board_code, first_date, n_members, captured_at,
                            source (fresh | carried: request failed, previous members kept)
- theme_members.parquet     board, kind, code
Every refresh is also archived by content hash (store.archive), so a saved list can name the exact
membership it used.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pandas as pd

from . import pools, store

# Concepts that describe index/fund/flow/status membership or past price action, not a theme.
NOISE = ("昨日", "融资融券", "沪股通", "深股通", "MSCI", "标准普尔", "富时罗素", "预增", "预盈", "预减", "扭亏",
         "转债", "机构重仓", "基金重仓", "QFII", "社保重仓", "证金", "汇金", "HS300", "上证", "深证", "中证",
         "创业板综", "破净", "低价股", "百元股", "次新股", "AH股", "B股", "送转", "高送转", "参股", "股权转让",
         "举牌", "央视50", "茅指数", "宁组合", "ST股", "微盘股", "专精特新", "注册制", "北交所", "超级品牌",
         "壳资源", "养老金", "长江三角", "大湾区", "京津冀", "成渝", "西部大开发", "一带一路", "国企改革",
         "央企改革", "地方国资", "MLOps", "季报", "年报", "增持", "回购", "分拆", "IPO受益", "创投", "AB股",
         "GDR", "科创", "广东板块", "浙江板块", "江苏板块", "上海板块", "北京板块", "深圳特区",
         # price-action / popularity / status buckets (not themes)
         "多板", "连板", "首板", "热股", "人气", "题材股", "新高", "新低", "涨停", "跌停", "龙虎榜", "活跃",
         "亏损", "微利", "破发", "低市盈率", "高股息", "外资背景", "大盘", "中盘", "小盘", "绩优", "蓝筹", "白马",
         "东方财富", "百元", "异动", "放量", "缩量", "强势", "弱势", "历史", "近期", "重仓", "持股", "陆股通",
         # earnings-status buckets
         "中报", "首亏", "续亏", "预亏", "业绩", "快报", "三季报", "一季报",
         # valuation / style / ownership buckets
         "破增发", "价值股", "市净率", "市盈率", "成长股", "反转股", "周期股", "超跌", "趋势股", "红利股",
         "权重股", "行业龙头", "微盘", "风格", "成份", "深成", "基金增仓", "基金减仓", "新进", "金股",
         "调研", "股权分散", "股权集中", "股权激励")


def _meta(name: str):
    return store.home() / "meta" / name


def refresh(kinds=("industry", "concept"), with_history: bool = True, log=print) -> dict:
    """Re-capture board lists and members. A board whose request fails keeps its previous members and
    first date (marked `source="carried"` with the old `captured_at`); a failed board with no previous
    capture is missing and reported. The new files replace the old ones atomically and are archived."""
    import akshare as ak
    boards, members = [], []
    listing = {"industry": ak.stock_board_industry_name_em, "concept": ak.stock_board_concept_name_em}
    cons = {"industry": ak.stock_board_industry_cons_em, "concept": ak.stock_board_concept_cons_em}
    hist = {"industry": ak.stock_board_industry_hist_em, "concept": ak.stock_board_concept_hist_em}
    prev_b, prev_m = _previous()
    stamp = datetime.now(timezone.utc).isoformat()
    failures, carried = [], []
    for kind in kinds:
        names = store._retry(listing[kind])
        for _, row in names.iterrows():
            name = row["板块名称"]
            if kind == "concept" and any(word in name for word in NOISE):
                continue
            try:
                frame = store._retry(cons[kind], name, attempts=2, pause=1.0)
                codes = pools.to_code(frame["代码"])
                first = None
                if with_history:
                    period = "日k" if kind == "industry" else "daily"  # the two AKShare APIs differ
                    h = store._retry(lambda n: hist[kind](symbol=n, period=period, start_date="20100101",
                                                           end_date="20991231", adjust=""), name, attempts=2, pause=1.0)
                    first = str(h["日期"].iloc[0])[:10] if len(h) else None
                members += [(name, kind, c) for c in codes]
                boards.append((name, kind, row.get("板块代码"), first, len(codes), stamp, "fresh"))
            except Exception as error:
                old = prev_b[(prev_b["board"] == name) & (prev_b["kind"] == kind)] if prev_b is not None else None
                if old is not None and len(old):
                    o = old.iloc[0]
                    codes = prev_m.loc[(prev_m["board"] == name) & (prev_m["kind"] == kind), "code"].tolist()
                    members += [(name, kind, c) for c in codes]
                    boards.append((name, kind, o.get("board_code"), o.get("first_date"), len(codes),
                                   o.get("captured_at"), "carried"))
                    carried.append((kind, name))
                    log(f"  theme {kind}/{name} FAILED, previous members kept: {error}")
                else:
                    failures.append((kind, name, str(error)[:100]))
                    log(f"  theme {kind}/{name} FAILED (missing): {error}")
            time.sleep(0.2)
        log(f"  {kind}: {sum(1 for b in boards if b[1] == kind)} boards")
    board_frame = pd.DataFrame(boards, columns=["board", "kind", "board_code", "first_date", "n_members",
                                                "captured_at", "source"])
    member_frame = pd.DataFrame(members, columns=["board", "kind", "code"]).drop_duplicates()
    ids = {}
    for frame, name in ((board_frame, "theme_boards.parquet"), (member_frame, "theme_members.parquet")):
        tmp = _meta(name).with_suffix(".tmp")
        frame.to_parquet(tmp, index=False)
        tmp.replace(_meta(name))
        ids[name] = store.archive(_meta(name))
    record_snapshot(stamp, ids, store.now_sh().date().isoformat())
    return dict(boards=len(boards), members=len(members), failures=failures, carried=carried)


SNAPSHOTS = "theme_snapshots.jsonl"  # one line per refresh: which archived membership was current from which day


def record_snapshot(captured_at: str, ids: dict, day: str) -> None:
    """Append (never rewrite) the archive ids of a refresh, so a later exam can use the membership that was current
    on each day instead of today's snapshot (removes the look-ahead of board membership from that day on)."""
    line = json.dumps(dict(day=day, captured_at=captured_at, archive=ids), ensure_ascii=False)
    with open(_meta(SNAPSHOTS), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def snapshot_for(day: str) -> dict | None:
    """The newest recorded refresh made on or before `day` (None before the first record)."""
    path = _meta(SNAPSHOTS)
    if not path.exists():
        return None
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [r for r in rows if r["day"] <= day]
    return max(rows, key=lambda r: (r["day"], r["captured_at"])) if rows else None


def _previous() -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    b, m = _meta("theme_boards.parquet"), _meta("theme_members.parquet")
    if not (b.exists() and m.exists()):
        return None, None
    boards = pd.read_parquet(b)
    if "captured_at" not in boards:
        boards["captured_at"] = None
    return boards, pd.read_parquet(m)


def load() -> tuple[pd.DataFrame, pd.DataFrame] | None:
    b, m = _meta("theme_boards.parquet"), _meta("theme_members.parquet")
    if not (b.exists() and m.exists()):
        return None
    boards, members = pd.read_parquet(b), pd.read_parquet(m)
    noise = boards["board"].map(lambda n: any(word in n for word in NOISE)) & boards["kind"].eq("concept")
    keep = set(boards.loc[~noise, "board"])
    return boards[boards["board"].isin(keep)], members[members["board"].isin(keep)]


if __name__ == "__main__":
    print(refresh())
