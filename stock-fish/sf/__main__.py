"""`sf` — LongMai (龙脉) personal short-term screening CLI (龙脉 V7). The system never trades.

    sf update                 append missing sessions + capture today's limit/LHB pools
    sf premarket              build the pre-market list for the next session
    sf show [TARGET]          print a saved list
    sf review                 hit rates of the saved lists (entry-free, see sf/evaluate.py)
    sf evaluate               historical hit rates of the screening and the temperature calls
    sf crowding / sf mood     short-term market temperature (review context, not stock selection)
    sf seats / sf themes      LHB seats; board membership refresh
    sf note CODE THEME        record why a stock is traded (from news); shown in the list
    sf intraday [report]      1-minute bars of limit-up / touched / listed stocks; descriptive study report
    sf status                 data store coverage
    sf backup / sf restore    one portable archive of the data directory (moving to another machine)
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time

import pandas as pd

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)
pd.set_option("display.max_rows", 300)
pd.set_option("display.unicode.east_asian_width", True)


def _user_path(text: str):
    """A path argument as the user typed it: relative to the caller's directory (bin/sf cds into stock-fish/)."""
    import os
    from pathlib import Path
    path = Path(text).expanduser()
    return path if path.is_absolute() else Path(os.environ.get("SF_CWD", ".")) / path


def _quiet():
    """Silence BaoStock's login/logout prints."""
    return contextlib.redirect_stdout(io.StringIO())


# ------------------------------------------------------------------ commands

def _capture_seats(day: str) -> str:
    from . import seats
    try:
        return seats.capture(day)
    except Exception as error:  # reported; the day stays "not captured" and `sf seats --backfill` retries
        return f"FAILED: {str(error)[:120]}"


def _capture_intraday(days: list[str]) -> str:
    """Capture minute bars; failures are reported (the source keeps ~5 sessions, so retry within days)."""
    from . import intraday
    try:
        res = intraday.capture(days, log=lambda *_: None)
    except Exception as error:
        return f"FAILED: {str(error)[:120]}"
    return "；".join(f"{d} {r['status']}" + (f"（{r['ok']}/{r['requested']}）" if "ok" in r else "") for d, r in res.items())


def cmd_intraday(args) -> int:
    from . import intraday, store
    if args.action == "report":
        return _intraday_report()
    stored = sorted(p.stem for p in (store.home() / "bars").glob("*.parquet"))
    days = stored[-5:] if args.backfill else [args.date or store.latest_complete_session(store.calendar())]
    missing = [d for d in days if d not in stored]
    if missing:
        print(f"行情未入库：{missing}，请先 sf update", file=sys.stderr)
        return 1
    started = time.monotonic()
    res = intraday.capture(days, workers=args.workers)
    bad = False
    for day, r in res.items():
        if "requested" in r:
            print(f"{day} {r['status']}：抓取 {r['requested']} 只，完整 {r['ok']} 只，不完整 {len(r['incomplete'])}，"
                  f"失败 {len(r['failed'])}，当天无数据 {len(r['absent'])}")
        else:
            print(f"{day} {r['status']}")
        bad = bad or r["status"].startswith("FAILED") or r["status"] == "partial"
    print(f"耗时 {time.monotonic() - started:.0f}s；源数据只保留约 5 个交易日，失败的日子请尽快重跑")
    return 1 if bad else 0


def _intraday_report() -> int:
    import json as _json

    from . import intraday, manifest, store
    with _quiet():
        rep = intraday.report(log=lambda *_: None)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    out = store.home() / "research" / f"intraday-report-{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    print(f"盘中分时：{rep['sessions']} 个交易日（{rep['first']} ~ {rep['last']}），规格 docs/INTRADAY_STUDY.md")
    if not rep["testable"]:
        print(f"不足 {intraday.MIN_SESSIONS_FOR_TEST} 个交易日：以下只是描述，不下结论、不改规则。")
    for key, title in (("seal", "H2 封板质量（主板，次日结果）"), ("lead", "H1 板块内封板顺序（主板）")):
        if key in rep:
            print(f"\n{title}\n{rep[key].to_string()}")
            rep[key].to_csv(out / f"{key}.csv", encoding="utf-8-sig")
    p = rep["priced"]
    print(f"\nH3 拥挤代价（日线 {p.attrs['first']} ~ {p.attrs['last']}，主板非 ST 涨停股，描述性）")
    print(p.rename(columns={"n": "样本", "gap_mean": "次日开盘缺口", "unbuyable": "次日一字买不到", "max3_mean": "3日最高涨幅",
                            "gap_share_median": "缺口占3日涨幅(中位)"}).to_string())
    p.to_csv(out / "priced_in.csv", encoding="utf-8-sig")
    if not rep["panel"].empty:
        rep["panel"].to_parquet(out / "panel.parquet")
    stored = sorted(x.stem for x in (store.home() / "bars").glob("*.parquet"))
    (out / "manifest.json").write_text(_json.dumps(manifest.build(stored), ensure_ascii=False, indent=2, default=str))
    print(f"\n已保存 {out}")
    return 0


def cmd_seats(args) -> int:
    from . import seats, store
    if args.backfill:
        days = sorted(p.stem for p in (store.home() / "bars").glob("*.parquet"))[-args.backfill:]
        result = seats.backfill(days, workers=args.workers)
        print(f"请求 {result['requested']} 天，新抓 {result['fetched']} 天，失败 {len(result['failures'])} 天")
        return 1 if result["failures"] else 0
    if args.code:
        day = args.date or seats.captured_days()[-1]
        rows = seats.load(day)
        if rows is None:
            print(f"{day} 未采集席位明细", file=sys.stderr)
            return 1
        code = args.code if "." in args.code else seats.to_code(pd.Series([args.code])).iloc[0]
        r = seats.stock_seat_rows(rows[rows["code"] == code])
        if r.empty:
            print(f"{day} {code} 未上榜")
            return 0
        print(f"{day} {code} 上榜原因: {' / '.join(r['reason'].unique())}")
        view = r.sort_values("net", ascending=False)[["seat", "seat_type", "buy", "sell", "net"]]
        for c in ("buy", "sell", "net"):
            view[c] = (view[c] / 1e4).round(0)
        print(view.rename(columns={"seat": "席位", "seat_type": "类型", "buy": "买入(万)", "sell": "卖出(万)",
                                   "net": "净额(万)"}).to_string(index=False))
        return 0
    print("计算席位能力（只用已知结果，约1分钟）...")
    data, skill = seats.dataset(log=lambda *_: None)
    asof = args.date or data["date"].max()
    top = seats.top_seats(skill, seats.load_all(), asof, n=args.top, min_events=args.min_events)
    top = top.assign(skill_avg=top["skill_avg"].map("{:.2%}".format), skill_hit=top["skill_hit"].map("{:.0%}".format))
    print(f"截至 {asof} 的席位榜（被其净买入的股票：次日开盘买、再次日开盘卖的平均收益，已知结果）")
    print(top.rename(columns={"seat": "席位", "skill_n": "次数", "skill_avg": "平均", "skill_hit": "胜率"}).to_string(index=False))
    return 0


def cmd_mood(args) -> int:
    from . import mood, store, strategy
    days = sorted(p.stem for p in (store.home() / "bars").glob("*.parquet"))
    w = strategy.panel(store.load_window(days[-(args.days + 260):]))
    tab = mood.table(w).tail(args.days)
    cols = ["lu_count", "ld_count", "broken_rate", "promo_rate", "big_loss", "prem_lu", "up_ratio", "level", "tier", "advice"]
    view = tab[cols].copy()
    for c in ("broken_rate", "promo_rate", "prem_lu", "up_ratio", "level"):
        view[c] = view[c].map(lambda v: f"{v:.0%}" if c != "prem_lu" else f"{v:.2%}")
    print(view.rename(columns={"lu_count": "涨停", "ld_count": "跌停", "broken_rate": "炸板率", "promo_rate": "晋级率",
                               "big_loss": "大面", "prem_lu": "接力溢价", "up_ratio": "上涨占比",
                               "level": "情绪分位", "tier": "档位", "advice": "建议"}).to_string())
    print(f"{mood.VERSION}: 档位只作风控（低档少亏），不产生超额收益；详见 USER_GUIDE")
    return 0


def cmd_crowding(args) -> int:
    from . import crowding, mainline, store, themes
    loaded = themes.load()
    if loaded is None:
        print("缺少题材数据，请先运行 sf themes", file=sys.stderr)
        return 1
    days = sorted(p.stem for p in (store.home() / "bars").glob("*.parquet"))
    with _quiet():
        aw = mainline.a_share_panel(store.load_window(days[-(args.days + 260):]))
        ts = mainline.theme_stats(aw, *loaded)
    tab = crowding.environment(aw, ts, args.days)
    view = pd.DataFrame({
        "全市场成交(亿)": (tab["market_amount"] / 1e8).map(lambda v: f"{v:,.0f}"),
        "较20日均": tab["amount_ratio20"].map(lambda v: f"{v:.2f}"),
        "短线占比": tab["spec_share"].map(lambda v: f"{v:.1%}"),
        "250日分位": tab["spec_pct250"].map(lambda v: "-" if pd.isna(v) else f"{v:.0%}"),
        "高换手占比": tab["hot_turn_share"].map(lambda v: f"{v:.1%}"),
        "主线(成交占比)": tab["mainlines"]}, index=tab.index)
    print(view.to_string())
    print("短线占比=近5日涨停过的股票成交额/全市场；高换手=换手≥15%；!=该主线成交占比处于60日最高位（历史上2日内退潮概率约1.5倍）。")
    print("只用于复盘和判断短线水温，不用于选股或定龙头；回测未发现市场级拥挤阈值（docs/CROWDING_STUDY.md）。")
    return 0


def cmd_update(args) -> int:
    from . import pools, store
    started = time.monotonic()
    with _quiet():
        result = store.update(sessions_needed=args.sessions, observation=args.date, workers=args.workers,
                              log=lambda *_: None)
    status = pools.capture(result["observation"])
    status["seats"] = _capture_seats(result["observation"])
    status["intraday"] = _capture_intraday([result["observation"]])
    print(f"观察日 {result['observation']} | 新增 {len(result['fetched'])} 天 | 失败 {result['failed'] or '无'}")
    print(f"资金池: {json.dumps(status, ensure_ascii=False)}")
    print(f"耗时 {time.monotonic() - started:.1f}s")
    return 1 if result["failed"] else 0


def cmd_premarket(args) -> int:
    from . import listing, longmai, pools, store
    started = time.monotonic()
    if not args.no_update:
        with _quiet():
            result = store.update(sessions_needed=longmai.HISTORY_SESSIONS, observation=args.date,
                                  workers=2, log=lambda *_: None)
        if result["failed"]:
            print(f"行情缺失，无法生成清单: {result['failed']}", file=sys.stderr)
            return 1
        pools.capture(result["observation"], log=lambda *_: None)
        _capture_seats(result["observation"])
        intraday_status = _capture_intraday([result["observation"]])
    with _quiet():
        result = listing.build(args.date, update_external=not args.no_update)
    path = listing.save(result)
    print("\n".join(listing.header(result)))
    table = result["table"]
    for group in ("主线龙头", "连板梯队", "观察", "高波动"):
        part = table[table["分组"] == group]
        print(f"\n【{group}】{len(part)} 只")
        if len(part):
            print(part[listing.VIEW[1:]].to_string(index=False))
    if not args.no_update:
        print(f"\n盘中分时：{intraday_status}")
    print(f"\n已保存 {path}/盘前清单.csv  (耗时 {time.monotonic() - started:.1f}s)")
    return 0


def cmd_show(args) -> int:
    from . import listing
    run = listing.latest_run(args.target)
    if run is None or not (run / "盘前清单.csv").exists():
        print("没有已保存的清单")
        return 1
    if (run / "盘前概况.txt").exists():
        print((run / "盘前概况.txt").read_text().strip())
    print(pd.read_csv(run / "盘前清单.csv").to_string(index=False))
    return 0


def cmd_review(args) -> int:
    from . import review
    with _quiet():
        rows = review.collect(args.since)
    if rows.empty:
        print("没有可复盘的清单")
        return 1
    rep = review.report(rows)
    print(f"复盘 {rep['lists']} 份清单（{rep['first']} ~ {rep['last']}），口径：观察日收盘起，不按开盘买入盈亏")
    print("lu1=次日涨停  max3=3日内最高涨幅  out5=5日内≥2个涨停或最高+20%  bigloss1=次日大面；baseline=当日全部主板涨停")
    print(rep["stats"].to_string())
    if rep["recall"] is not None:
        print(f"\n妖股召回（观察日涨停、5日内再涨停≥3次）：{rep['recall']:.0%}（{rep['winners']}只）")
        for m in rep["missed"]:
            print(f"  漏掉 {m['obs']} {m['name'] or m['code']}")
    print(f"已保存 {review.save(rows, rep)}")
    return 0


def cmd_evaluate(args) -> int:
    from . import evaluate, manifest, store
    started = time.monotonic()
    stamp = time.strftime("%Y%m%dT%H%M%S")
    out = store.home() / "evaluations" / stamp
    out.mkdir(parents=True, exist_ok=False)
    stored = sorted(p.stem for p in (store.home() / "bars").glob("*.parquet"))
    (out / "manifest.json").write_text(json.dumps(manifest.build(stored), ensure_ascii=False, indent=2, default=str))
    if args.part in ("all", "screening"):
        print("选股命中率（全历史逐日重算，约数分钟）...")
        with _quiet():
            panel = evaluate.screening_panel(log=lambda *_: None)
        rep = evaluate.screening_report(panel)
        print(rep["stats"].to_string())
        print("妖股召回：", rep["recall"].to_dict(), " 样本：", rep["winners"].to_dict())
        panel.to_parquet(out / "screening_panel.parquet")
        rep["stats"].to_csv(out / "screening_stats.csv", encoding="utf-8-sig")
    if args.part in ("all", "temperature"):
        with _quiet():
            df = evaluate.temperature_panel()
        rep = evaluate.temperature_report(df)
        print("\n水温节点命中率（hit 对比 base）：")
        print(rep.to_string())
        rep.to_csv(out / "temperature_report.csv", encoding="utf-8-sig")
    print(f"\n已保存 {out}  (耗时 {time.monotonic() - started:.0f}s)")
    return 0


def cmd_note(args) -> int:
    from . import notes, pools, store
    if not args.code:
        frame = notes.load()
        print(frame.tail(args.last).to_string(index=False) if len(frame) else "还没有炒作题材记录")
        return 0
    if not args.theme:
        print("用法：sf note 001379 商业航天 [--source URL]", file=sys.stderr)
        return 1
    code = args.code if "." in args.code else pools.to_code(pd.Series([args.code])).iloc[0]
    day = args.date or store.latest_complete_session(store.calendar())
    notes.add(day, code, args.theme, args.source or "")
    print(f"已记录 {day} {code} 炒作{args.theme}（{notes.MAX_AGE}个交易日内的清单会显示）")
    return 0


def cmd_status(args) -> int:
    from . import pools, store
    bars = sorted(p.stem for p in (store.home() / "bars").glob("*.parquet"))
    print(f"数据目录 {store.home()}")
    print(f"行情 {len(bars)} 个交易日：{bars[0] if bars else '-'} ~ {bars[-1] if bars else '-'}")
    for kind in (*pools.KINDS, "lhb", "seats"):
        files = sorted(p.stem for p in (store.home() / "pools" / kind).glob("*.parquet")) \
            if (store.home() / "pools" / kind).exists() else []
        print(f"资金池 {kind}: {len(files)} 天 {files[-1] if files else ''}")
    return 0


def cmd_themes(args) -> int:
    from . import themes
    started = time.monotonic()
    result = themes.refresh(with_history=not args.no_history)
    print(f"板块 {result['boards']} 个，成分 {result['members']} 条，沿用旧成分 {len(result['carried'])} 个，"
          f"缺失 {len(result['failures'])} 个，耗时 {time.monotonic() - started:.0f}s")
    for kind, name, error in result["failures"]:
        print(f"  缺失 {kind}/{name}: {error}")
    return 1 if result["failures"] or result["carried"] else 0


def cmd_backup(args) -> int:
    from pathlib import Path

    from . import backup, store
    out = _user_path(args.out) if args.out else store.home().parent / "stockfish-backups"
    started = time.monotonic()
    print(f"打包数据目录 {store.home()} ...（打包期间不要运行其他 sf 命令）")
    result = backup.create(out)
    b = result["bars"]
    print(f"已生成 {result['archive']}\n校验文件 {result['archive']}.sha256\n"
          f"共 {result['files']} 个文件，行情 {b['first']} ~ {b['last']}（{b['sessions']} 天），"
          f"耗时 {time.monotonic() - started:.0f}s")
    print("请把这两个文件一起上传到网盘。恢复：sf restore <压缩包>")
    return 0


def cmd_restore(args) -> int:
    import tarfile
    from pathlib import Path

    from . import backup, store
    archive = _user_path(args.archive)
    print(f"恢复到数据目录 {store.home()}（已有文件不覆盖）...")
    try:
        result = backup.restore(archive, expected_sha256=args.sha256)
    except (ValueError, OSError, tarfile.TarError) as error:
        print(f"恢复失败：{error}", file=sys.stderr)
        return 1
    b = result["bars"]
    print(f"备份时间 {result['created_at']}，行情 {b['first']} ~ {b['last']}（{b['sessions']} 天）")
    print(f"新恢复 {result['restored']} 个，已存在且相同 {result['skipped']} 个")
    problems = [("内容不同、未覆盖", result["conflicts"]), ("压缩包内损坏", result["damaged"]),
                ("压缩包内缺失", result["missing"])]
    for label, items in problems:
        for item in items[:20]:
            print(f"  {label}: {item}", file=sys.stderr)
    if any(items for _, items in problems):
        return 1
    print("下一步：sf status 检查覆盖情况，sf update 补上备份之后的交易日。")
    return 0


# ------------------------------------------------------------------ parser

def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="sf", description="龙脉 个人短线研究工具")
    sub = root.add_subparsers(dest="command", required=True)

    up = sub.add_parser("update", help="补齐行情并采集当日涨停/炸板/跌停池与龙虎榜")
    up.add_argument("--date", help="观察日（默认最近一个已收盘交易日）")
    up.add_argument("--sessions", type=int, default=30, help="保证最近N个交易日都在库中")
    up.add_argument("--workers", type=int, default=2)
    up.set_defaults(func=cmd_update)

    pm = sub.add_parser("premarket", help="生成下一交易日盘前清单")
    pm.add_argument("--date", help="观察日（默认最近一个已收盘交易日）")
    pm.add_argument("--no-update", action="store_true", help="不更新数据")
    pm.set_defaults(func=cmd_premarket)

    sh = sub.add_parser("show", help="查看已保存的清单")
    sh.add_argument("target", nargs="?", help="目标交易日（默认最新）")
    sh.set_defaults(func=cmd_show)

    rv = sub.add_parser("review", help="已保存清单的命中率复盘")
    rv.add_argument("--since", help="只看此目标日之后")
    rv.set_defaults(func=cmd_review)

    ev = sub.add_parser("evaluate", help="全历史选股与水温命中率（不按开盘买入盈亏）")
    ev.add_argument("part", nargs="?", default="all", choices=["all", "screening", "temperature"])
    ev.set_defaults(func=cmd_evaluate)

    th = sub.add_parser("themes", help="刷新概念/行业板块成分（约20分钟，建议每周一次）")
    th.add_argument("--no-history", action="store_true", help="不取板块起始日期（会有前视偏差）")
    th.set_defaults(func=cmd_themes)

    se = sub.add_parser("seats", help="龙虎榜席位：回填、席位榜、个股席位")
    se.add_argument("--backfill", type=int, help="回填最近N个交易日的席位明细")
    se.add_argument("--workers", type=int, default=4)
    se.add_argument("--code", help="查看某只股票某天的席位，如 600000")
    se.add_argument("--date")
    se.add_argument("--top", type=int, default=20)
    se.add_argument("--min-events", type=int, default=30)
    se.set_defaults(func=cmd_seats)

    cr = sub.add_parser("crowding", help="短线资金水温（复盘用，不选股）")
    cr.add_argument("--days", type=int, default=20)
    cr.set_defaults(func=cmd_crowding)
    mo = sub.add_parser("mood", help="市场情绪指标与阶段")
    mo.add_argument("--days", type=int, default=10)
    mo.set_defaults(func=cmd_mood)

    no = sub.add_parser("note", help="记录个股的炒作题材（来自新闻公告）；不带参数时列出记录")
    no.add_argument("code", nargs="?", help="如 001379")
    no.add_argument("theme", nargs="?", help="如 商业航天")
    no.add_argument("--date", help="新闻对应的交易日（默认最近已收盘交易日）")
    no.add_argument("--source", help="新闻链接")
    no.add_argument("--last", type=int, default=30)
    no.set_defaults(func=cmd_note)

    it = sub.add_parser("intraday", help="抓取当天涨停/触板/清单股的 1 分钟线；report 输出分时研究描述")
    it.add_argument("action", nargs="?", default="capture", choices=["capture", "report"])
    it.add_argument("--date", help="交易日（默认最近已收盘交易日）")
    it.add_argument("--backfill", action="store_true", help="补抓最近 5 个已入库交易日中还没抓的")
    it.add_argument("--workers", type=int, default=4)
    it.set_defaults(func=cmd_intraday)

    bk = sub.add_parser("backup", help="把数据目录打成一个压缩包（换设备用），附校验文件")
    bk.add_argument("--out", help="输出目录（默认数据目录旁的 stockfish-backups）")
    bk.set_defaults(func=cmd_backup)

    rs = sub.add_parser("restore", help="从压缩包恢复数据目录（不覆盖已有文件，逐个校验）")
    rs.add_argument("archive", help="longmai-data-*.tar.gz")
    rs.add_argument("--sha256", help="压缩包校验值（默认读同目录的 .sha256 文件）")
    rs.set_defaults(func=cmd_restore)

    st = sub.add_parser("status", help="数据覆盖情况")
    st.set_defaults(func=cmd_status)
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
