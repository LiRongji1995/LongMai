"""Theme (concept + industry board) membership from Eastmoney via AKShare.

Point-in-time caveats (stated in every output that uses themes):
- Boards are only used on days after their index history starts (`first_date`). Concept boards are often
  created after a theme starts, so using today's board list historically would leak the future.
- Membership is today's snapshot; historical additions/removals are not known.

Files (under meta/):
- theme_boards.parquet      board, kind (concept|industry), board_code, first_date
- theme_members.parquet     board, kind, code
"""
from __future__ import annotations

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
         "东方财富", "百元", "异动", "放量", "缩量", "强势", "弱势", "历史", "近期", "重仓", "持股", "陆股通")


def _meta(name: str):
    return store.home() / "meta" / name


def refresh(kinds=("industry", "concept"), with_history: bool = True, log=print) -> dict:
    import akshare as ak
    boards, members = [], []
    listing = {"industry": ak.stock_board_industry_name_em, "concept": ak.stock_board_concept_name_em}
    cons = {"industry": ak.stock_board_industry_cons_em, "concept": ak.stock_board_concept_cons_em}
    hist = {"industry": ak.stock_board_industry_hist_em, "concept": ak.stock_board_concept_hist_em}
    failures = []
    for kind in kinds:
        names = store._retry(listing[kind])
        for _, row in names.iterrows():
            name = row["板块名称"]
            if kind == "concept" and any(word in name for word in NOISE):
                continue
            try:
                frame = store._retry(cons[kind], name, attempts=2, pause=1.0)
                codes = pools.to_code(frame["代码"])
                members += [(name, kind, c) for c in codes]
                first = None
                if with_history:
                    period = "日k" if kind == "industry" else "daily"  # the two AKShare APIs differ
                    h = store._retry(lambda n: hist[kind](symbol=n, period=period, start_date="20100101",
                                                           end_date="20991231", adjust=""), name, attempts=2, pause=1.0)
                    first = str(h["日期"].iloc[0])[:10] if len(h) else None
                boards.append((name, kind, row.get("板块代码"), first, len(codes)))
            except Exception as error:
                failures.append((kind, name, str(error)[:100]))
                log(f"  theme {kind}/{name} FAILED: {error}")
            time.sleep(0.2)
        log(f"  {kind}: {sum(1 for b in boards if b[1] == kind)} boards")
    stamp = datetime.now(timezone.utc).isoformat()
    pd.DataFrame(boards, columns=["board", "kind", "board_code", "first_date", "n_members"]).assign(
        captured_at=stamp).to_parquet(_meta("theme_boards.parquet"), index=False)
    pd.DataFrame(members, columns=["board", "kind", "code"]).drop_duplicates().to_parquet(
        _meta("theme_members.parquet"), index=False)
    return dict(boards=len(boards), members=len(members), failures=failures)


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
