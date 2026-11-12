"""`sf` — LongMai (龙脉) personal short-term screening CLI (龙脉 V9). The system never trades.

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


def _page_for(target: str | None = None):
    """Write the local page for the latest list (best effort: a failure is reported, never fatal)."""
    from . import listing, page
    try:
        run = listing.latest_run(target)
        return page.write(run) if run is not None else None
    except Exception as error:
        print(f"本地页面生成失败：{error}", file=sys.stderr)
        return None


def cmd_page(args) -> int:
    import subprocess

    from . import listing, page
    run = listing.latest_run(args.target)
    if run is None:
        print("没有已保存的清单", file=sys.stderr)
        return 1
    path = page.write(run)
    print(f"已生成本地页面：{path}")
    if args.open and sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    return 0


def cmd_prune(args) -> int:
    from . import prune
    items = prune.plan()
    total = 0
    for item in items:
        if item["keep"] is None:
            print(f"{item['target']}：{item['note']}")
            continue
        nbytes = sum(prune.size(p) for p in item["remove"])
        total += nbytes
        print(f"{item['target']}：保留 {item['keep'].name}，移除 {len(item['remove'])} 项（{nbytes / 1e6:.1f} MB）")
        for path in item["remove"]:
            print(f"  - {path.relative_to(prune.store.home())}")
    if not any(i["remove"] for i in items):
        print("没有需要清理的清单")
        return 0
    if not args.yes:
        print(f"以上共 {total / 1e6:.1f} MB。这是预览；加 --yes 才会移到废纸篓。")
        return 0
    trash = prune.apply(items)
    print(f"已移到废纸篓：{trash}（记录在 meta/prune.jsonl）")
    return 0


def cmd_judge(args) -> int:
    from . import journal
    if args.action == "add":
        if not args.text:
            print("用法：sf judge add \"判断原文\" [--kind ... --subject ... --days N --not]", file=sys.stderr)
            return 1
        try:
            rec = journal.add_judgment(args.text, args.kind, args.subject, args.days, expect=not args.negate)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 1
        what = journal.KINDS[rec["kind"]]
        print(f"已记录（基于 {rec['observation']} 收盘{'，盘中记录' if rec['intraday'] else ''}）：{rec['text']}"
              + (f"；检验：{rec['subject']} {rec['days']} 日内{'不' if not rec['expect'] else ''}{what}" if rec["kind"] != "free" else ""))
        return 0
    j = journal.judgments()
    if j.empty:
        print("还没有判断记录")
        return 0
    if args.action == "list":
        print(j[["observation", "kind", "subject", "days", "expect", "text"]].to_string(index=False))
        return 0
    return _judge_score(j)


def _judge_score(j) -> int:
    from datetime import datetime, timezone

    from . import evaluate, journal, mainline, store, strategy, themes
    stored = sorted(p.stem for p in (store.home() / "bars").glob("*.parquet"))
    first = min(j["observation"])
    window = [d for d in stored if d >= first][:400] or stored[-5:]
    window = stored[max(0, stored.index(window[0]) - 5):]
    with _quiet():
        bars = store.load_window(window)
        w = strategy.panel(bars)
        ts = None
        loaded = themes.load()
        if loaded is not None and (j["kind"] == "theme_fade").any():
            ts = mainline.theme_stats(mainline.a_share_panel(bars), *loaded)
    s = journal.score(j, w, evaluate.stock_outcomes(w), ts)
    print(s[["observation", "kind", "subject", "days", "expect", "text", "result"]].to_string(index=False))
    summ = journal.judgment_summary(s)
    if not summ.empty:
        print("\n命中率（已有结果的判断）：")
        print(summ.to_string())
    out = store.home() / "journal" / "scores"
    out.mkdir(parents=True, exist_ok=True)
    path = out / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".csv")
    s.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"已保存 {path}")
    return 0


def cmd_trade(args) -> int:
    from . import journal, store
    if args.action == "add":
        if None in (args.date, args.code, args.side, args.price, args.qty):
            print("用法：sf trade add 日期 代码 buy|sell 价格 数量 [--reason 理由]", file=sys.stderr)
            return 1
        try:
            rec = journal.add_trade(args.date, args.code, args.side, args.price, args.qty, args.reason or "")
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(f"已记录：{rec['date']} {rec['name']} {'买入' if rec['side'] == 'buy' else '卖出'} {rec['qty']}股 @ {rec['price']}")
        return 0
    tr = journal.trades()
    if tr.empty:
        print("还没有交易记录")
        return 0

    def bars_for(day, code):
        path = store.bar_path(day)
        if not path.exists():
            return None
        b = pd.read_parquet(path, columns=["code", "high", "low"])
        b = b[b["code"] == code]
        return None if b.empty else dict(high=float(b["high"].iloc[0]), low=float(b["low"].iloc[0]))
    rep = journal.trade_report(tr, bars_for)
    for wmsg in rep["warnings"]:
        print("注意：" + wmsg)
    if not rep["closed"].empty:
        c = rep["closed"].assign(收益=(rep["closed"]["ret"] * 100).round(2).astype(str) + "%")
        print("已平仓（先进先出）：")
        print(c[["buy_date", "sell_date", "name", "qty", "buy_price", "sell_price", "收益", "group", "reason"]].to_string(index=False))
        print("\n按买入当天清单分组：")
        print(rep["by_group"].to_string())
    if not rep["open"].empty:
        print("\n未平仓：")
        print(rep["open"].to_string(index=False))
    return 0


def cmd_schedule(args) -> int:
    from . import schedule
    if args.action == "run":
        return schedule.run(args.job)
    if args.action == "install":
        paths = schedule.install(args.jobs or None)
        print("已安装定时任务（launchd，本机）：\n" + "\n".join(f"  {p}" for p in paths))
        print(f"时间按北京时间设定，已换算成本机时间（本机比北京慢 {schedule.local_offset():g} 小时），例如竞价 09:26 = 本机 "
              + "{:02d}:{:02d}".format(*schedule.local_slot(1, 9, 26)[1:]) + "。夏令时切换后需要重新安装。")
        print("电脑需要开机、联网且不在睡眠；错过的任务在唤醒后补跑一次（竞价只在北京时间 09:20-11:30 内补跑）。")
        return 0
    if args.action == "uninstall":
        removed = schedule.uninstall(args.jobs or None)
        print("已移除：" + ("、".join(p.name for p in removed) if removed else "无"))
        return 0
    for s in schedule.status():
        last = s["last"]
        tail = "从未运行" if not last else (f"上次 {last['started_at'][:16].replace('T', ' ')}，"
                                            + (f"跳过（{last['skipped']}）" if last.get("skipped") else f"退出码 {last['exit']}"))
        print(f"{s['job']:<10} {'已安装' if s['installed'] else '未安装'}  {s['days']} 北京 {s['times']}（本机 {s['local']}）  {tail}")
    warn = schedule.offset_changed()
    if warn:
        print("注意：" + warn)
    return 0


def cmd_baserates(args) -> int:
    """历史同类统计表 (sf/baserates.py): replays the V9 list, writes research/baserates/<stamp>/."""
    from . import baserates
    print("历史同类统计：逐日重放 V9 清单（约 5 分钟）...")
    with _quiet():
        t, meta = baserates.run(log=lambda *_: None)
    out = baserates.save(t, meta)
    top = t[(t["level"] == 2) & (t["cls"] == "*")]
    cols = ["role", "hb", "n", "lu1", "out5", "bigloss1", "open_limit", "nb", "b_bigloss", "b_hi", "b_lo", "b_cl"]
    print(top[cols].round(3).to_string(index=False))
    print(f"截至 {meta['cutoff']}，{meta['rows']} 行；已保存：{out}")
    return 0


def cmd_auction(args) -> int:
    """竞价层 SF_AUCTION_V1: auction facts for the saved list (docs/AUCTION_V1_PREREG.md)."""
    from . import auction
    try:
        snap = auction.snapshot(args.date)
    except FileNotFoundError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:  # network failure after bounded retries: report, never guess
        print(f"竞价行情抓取失败：{error}", file=sys.stderr)
        return 1
    out = auction.save(snap)
    for line in auction.lines(snap):
        print(line)
    with pd.option_context("display.unicode.east_asian_width", True, "display.width", 200):
        print(auction.table(snap).to_string(index=False))
    print(f"已保存：{out}")
    page_path = _page_for(snap["target"])
    if page_path:
        print(f"本地页面：{page_path}")
    return 0


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
    if args.backfill:  # captured days with missing stocks: retry only those, as new supplement files
        retried = intraday.retry_missing([d for d, r in res.items() if r["status"] in ("cached", "partial")],
                                         log=lambda *_: None)
        for day, r in retried.items():
            print(f"{day} 补抓缺失：{r.get('requested', 0)} 只，完整 {r.get('ok', 0)} 只，失败 {len(r.get('failed', {}))}")
        left = {d: len(intraday.missing_codes(d)) for d in retried}
        bad = any(left.values()) or any(r["status"].startswith("FAILED") for r in res.values())
        for day, n in left.items():
            if n:
                print(f"{day} 仍缺 {n} 只，稍后再跑 sf intraday --backfill")
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
    page_path = _page_for(result["target"])
    if page_path:
        print(f"本地页面：{page_path}")
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
        loaded = review._load()
        rows = review.collect(args.since, loaded)
        forward = review.forward_rows(loaded=loaded)
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
    fwd = review.forward_report(forward)
    if fwd.get("days") is not None and not forward.empty:
        print(f"\nV9 前向记录：观察组按 V9 与 V7 形态规则各算一遍（目标日 {review.FORWARD_FROM} 起 {fwd['lists']} 份清单，"
              f"不受只显示前 10 只的限制）")
        print(fwd["table"].to_string())
        if fwd["days"]:
            print(f"已有结果 {fwd['days']} 天：走出来 V9−V7 {fwd['d_out5'] * 100:+.1f} 个百分点，"
                  f"大面 {fwd['d_bigloss1'] * 100:+.1f} 个百分点")
        print(fwd["status"])
    with _quiet():
        calib = review.baserate_calibration(args.since, loaded)
        auc = review.auction_forward(loaded)
    crep = review.calibration_report(calib)
    if not crep.empty:
        print("\n历史同类对照（清单当时给出的历史比例 vs 实际发生，只统计已有结果的股票）：")
        print(crep.to_string())
    arep = review.auction_forward_report(auc)
    if not arep.empty:
        print("\n竞价标签前向记录（按预注册分界点重新标注；a_lu=当日涨停，a_bigloss=开盘后大面，a_out5=开盘起5日走出来）：")
        print(arep.to_string())
    from . import journal
    j = journal.judgments()
    if not j.empty:
        js = journal.score(j, loaded[0], loaded[1], None)
        summ = journal.judgment_summary(js)
        print(f"\n判断日记：{len(j)} 条，已判 {int(js['right'].notna().sum())} 条（题材类用 sf judge score 判）")
        if not summ.empty:
            print(summ.to_string())
    tr = journal.trades()
    if not tr.empty:
        trep = journal.trade_report(tr)
        if not trep["by_group"].empty:
            print("\n交易记录（按买入当天清单分组，先进先出）：")
            print(trep["by_group"].to_string())
    path = review.save(rows, rep)
    if not calib.empty:
        calib.to_csv(path / "baserate_calibration.csv", index=False, encoding="utf-8-sig")
    if not auc.empty:
        auc.to_csv(path / "auction_forward.csv", index=False, encoding="utf-8-sig")
    if not forward.empty:
        forward.to_csv(path / "forward_v9_v7.csv", index=False, encoding="utf-8-sig")
        (path / "forward_v9_v7.json").write_text(json.dumps(
            {k: v for k, v in fwd.items() if k != "table"} | {"table": fwd["table"].to_dict()},
            ensure_ascii=False, indent=2, default=str))
    print(f"已保存 {path}")
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
    if args.part == "patterns":  # 龙脉 V9 pre-registered pattern split (docs/STRATEGY_LONGMAI_V9_PREREG.md)
        from . import patterns
        print(f"龙脉 V9 形态细分：{stored[0]} ~ {stored[-1]}（{len(stored)} 天），逐日重放（较慢）...")
        with _quiet():
            panel, rep = patterns.run(log=lambda *_: None)
        cols = ["n_yes", "n_no", "unknown", "out5_yes", "out5_no", "d_out5", "bigloss1_yes", "bigloss1_no", "d_bigloss1"]
        print(rep["stats"][cols].round(3).to_string())
        print("\n判定（预注册规则）：")
        for f, v in rep["verdicts"].items():
            print(f"  {f}（{patterns.FLAGS[f][0]}）：{'采用' if v['adopted'] else '不采用'}，{v['reason']}")
        print("稳健性邻域（只报告）：" + "；".join(f"{n} {'通过' if v['adopted'] else '不通过'}" for n, v in rep["neighbours"].items()))
        print("\n观察组重放（V7 对 V9）：")
        print(rep["compare"].round(3).to_string())
        print("结论：" + ("V9 可进入前向记录" if rep["passed"] else "V9 不进入前向记录，V7 保持不变（否定结果如实保留）"))
        panel.to_parquet(out / "patterns_panel.parquet")
        rep["stats"].to_csv(out / "patterns_stats.csv", encoding="utf-8-sig")
        rep["compare"].to_csv(out / "patterns_compare.csv", encoding="utf-8-sig")
        (out / "patterns_verdict.json").write_text(json.dumps(dict(
            verdicts=rep["verdicts"], neighbours=rep["neighbours"], adopted=rep["adopted"], passed=rep["passed"],
            first=rep["first"], last=rep["last"], rows=rep["rows"]), ensure_ascii=False, indent=2))
    if args.part == "auction":  # 竞价层 V1 pre-registered exam (docs/AUCTION_V1_PREREG.md)
        from . import auction
        print(f"竞价层 V1 检验：{stored[0]} ~ {stored[-1]}（{len(stored)} 天），逐日重放 V9 清单（较慢）...")
        with _quiet():
            panel, rep = auction.run(log=lambda *_: None)
        print("分界点（train 段可买股票竞价缺口的 1/3、2/3 分位）：" + "；".join(
            f"{auction.ROLE_NAMES[r]} {lo * 100:+.2f}% / {hi * 100:+.2f}%" for r, (lo, hi) in rep["cuts"].items()))
        print(rep["stats"].round(3).to_string())
        print("\n判定（预注册规则）：")
        for r, v in rep["verdicts"].items():
            for cls, x in v.items():
                print(f"  {auction.ROLE_NAMES[r]} {auction.CLASS_NAMES[cls]}：{'采用' if x['adopted'] else '不采用'}，{x['reason']}")
        print("稳健性邻域（1/4、3/4 分位，只报告）：" + "；".join(
            f"{auction.ROLE_NAMES[r]} {auction.CLASS_NAMES[c]} {'通过' if x['adopted'] else '不通过'}"
            for r, v in rep["neighbours"].items() for c, x in v.items()))
        panel.to_parquet(out / "auction_panel.parquet")
        rep["stats"].to_csv(out / "auction_stats.csv", encoding="utf-8-sig")
        (out / "auction_verdict.json").write_text(json.dumps(dict(
            version=auction.VERSION, cuts=rep["cuts"], verdicts=rep["verdicts"], neighbour_cuts=rep["neighbour_cuts"],
            neighbours=rep["neighbours"], first=rep["first"], last=rep["last"], rows=rep["rows"]),
            ensure_ascii=False, indent=2))
    if args.part == "quality":  # 龙脉 V10 candidate exam (docs/STRATEGY_LONGMAI_V10_PREREG.md)
        from . import auction, quality
        print(f"龙脉 V10 候选（小成交额 / 一字）：{stored[0]} ~ {stored[-1]}（{len(stored)} 天），逐日重放 V9 清单（较慢）...")
        with _quiet():
            panel, rep = quality.run(log=lambda *_: None)
        cols = ["n_yes", "n_no", "unknown", "lu1_yes", "lu1_no", "out5_yes", "out5_no", "bigloss1_yes", "bigloss1_no",
                "open_limit_yes", "a_bigloss_yes", "a_bigloss_no"]
        print(rep["stats"][cols].round(3).to_string())
        print(f"\n判定（预注册规则，组规则 {rep['rule']}）：")
        for role, r in rep["roles"].items():
            name = auction.ROLE_NAMES[role]
            for f, v in r["verdicts"].items():
                print(f"  {name} {f}：{'成立' if v['holds'] else '不成立'}，{v['reason']}")
            print("  稳健性邻域（只报告）：" + "；".join(f"{n} {'成立' if v['holds'] else '不成立'}" for n, v in r["neighbours"].items()))
            print(r["compare"].round(3).to_string())
            print(f"  {name} 结论：" + ("采用（V10 候选）" if r["passed"] else "不改") + "\n")
        panel.to_parquet(out / "quality_panel.parquet")
        rep["stats"].to_csv(out / "quality_stats.csv", encoding="utf-8-sig")
        (out / "quality_verdict.json").write_text(json.dumps(dict(
            version=quality.VERSION, rule=rep["rule"], first=rep["first"], last=rep["last"], rows=rep["rows"],
            roles={k: dict(verdicts=v["verdicts"], neighbours=v["neighbours"], holding=v["holding"], deltas=v["deltas"],
                           passed=v["passed"], compare=v["compare"].reset_index().to_dict("records"))
                   for k, v in rep["roles"].items()}), ensure_ascii=False, indent=2, default=float))
    if args.part == "lateline":  # 龙脉 V11 candidate exam (docs/STRATEGY_LONGMAI_V11_PREREG.md)
        from . import lateline
        print(f"龙脉 V11 候选（主线后期·年线下龙头）：{stored[0]} ~ {stored[-1]}（{len(stored)} 天），逐日重放 V9 清单（较慢）...")
        with _quiet():
            b, l, rep = lateline.run(log=lambda *_: None)
        print(f"老题材门槛：近 {lateline.AGE_WINDOW} 日为主线 ≥ {rep['cut']:g} 天（train 段 2/3 分位）")
        print("\n主线层面（描述：fade3 / still3 / 龙1 次日涨停 / 龙1 次日大面）：")
        print(rep["describe"].to_string())
        seg = lambda d: " / ".join(f"{d[s]['m'] * 100:.1f}%({d[s]['n']})" for s in lateline.SEGS)  # noqa: E731
        print(f"\nQ1 老题材 fade3：龙1年线下 {seg(rep['q1']['yes'])}；龙1年线上 {seg(rep['q1']['no'])}")
        print(f"Q1 判定：{'成立' if rep['q1_passed'] else '不成立'}，{rep['q1']['verdict']['reason']}")
        print(f"\nQ2 老题材主线龙头 lu1：年线下 {seg(rep['q2']['yes'])}；年线上 {seg(rep['q2']['no'])}")
        print(f"Q2 标记：{'成立' if rep['q2']['verdict']['holds'] else '不成立'}，{rep['q2']['verdict']['reason']}")
        print(rep["compare"].round(3).to_string())
        print(f"Q2 结论（组规则 {rep['rule']}）：" + ("采用（V11 候选）" if rep["q2_passed"] else "不改"))
        print("稳健性邻域（只报告）：" + "；".join(
            f"{n} Q1{'成立' if v['q1']['holds'] else '不成立'} Q2{'成立' if v['q2']['holds'] else '不成立'}"
            for n, v in rep["neighbours"].items()))
        b.to_parquet(out / "lateline_boards.parquet")
        l.to_parquet(out / "lateline_leaders.parquet")
        rep["describe"].to_csv(out / "lateline_describe.csv", encoding="utf-8-sig")
        rep["compare"].to_csv(out / "lateline_compare.csv", encoding="utf-8-sig")
        (out / "lateline_verdict.json").write_text(json.dumps(dict(
            version=lateline.VERSION, rule=rep["rule"], cut=rep["cut"], q1=rep["q1"], q2=rep["q2"],
            q1_passed=rep["q1_passed"], q2_passed=rep["q2_passed"], deltas=rep["deltas"], neighbours=rep["neighbours"],
            first=rep["first"], last=rep["last"], boards=rep["boards"], leaders=rep["leaders"]),
            ensure_ascii=False, indent=2, default=float))
    if args.part == "stoploss":  # reference stop line exam (docs/STOPLOSS_V1_PREREG.md)
        from . import auction, stoploss
        print(f"参考止损线检验：{stored[0]} ~ {stored[-1]}（{len(stored)} 天），逐日重放 V9 清单（较慢）...")
        with _quiet():
            res = stoploss.run(log=lambda *_: None)
        for width, r in res.items():
            print(f"\n止损宽度 {width:.0%}" + ("（预注册主规则）" if width == stoploss.STOP else "（邻域，只报告）"))
            print(r["stats"].round(4).to_string())
            for role, v in r["verdicts"].items():
                print(f"  {auction.ROLE_NAMES[role]}：{v['call']}")
            r["table"].to_parquet(out / f"stoploss_{int(width * 100)}.parquet")
            r["stats"].to_csv(out / f"stoploss_{int(width * 100)}_stats.csv", encoding="utf-8-sig")
        (out / "stoploss_verdict.json").write_text(json.dumps(
            {f"{w:.2f}": r["verdicts"] for w, r in res.items()} | {"version": stoploss.VERSION},
            ensure_ascii=False, indent=2))
    if args.part == "swing":  # 龙脉 V8 pre-registered exam (docs/STRATEGY_LONGMAI_V8_PREREG.md)
        from . import manifest, swing
        stored = sorted(x.stem for x in (store.home() / "bars").glob("*.parquet"))
        print(f"龙脉 V8 波段考卷：{stored[0]} ~ {stored[-1]}（{len(stored)} 天），逐日重放 V8 与 V7（较慢）...")
        with _quiet():
            scored, v7, w = swing.replay(stored, log=lambda *_: None)
        rep = swing.evaluate(scored, v7, w)
        names = {"win10": "先涨15%", "loss10": "先跌8%", "max10": "10日最高", "min5": "5日最低", "ret10": "10日收益",
                 "n": "样本", "per_day": "每日只数", "recall20": "波段赢家召回"}
        print(rep["table"].rename(columns=names).to_string())
        for seg, v in rep["verdict"].items():
            print(f"{seg}: {v}")
        print("判定：" + ("全部满足，V8 可进入前向记录" if rep["passed"] else "未全部满足，V7 保持不变（否定结果如实保留）"))
        rep["table"].to_csv(out / "swing_table.csv", encoding="utf-8-sig")
        rep["picks"].to_parquet(out / "swing_picks.parquet")
        (out / "swing_verdict.json").write_text(json.dumps(dict(verdict=rep["verdict"], passed=rep["passed"]),
                                                           ensure_ascii=False, indent=2))
        (out / "manifest.json").write_text(json.dumps(manifest.build(stored),
                                                      ensure_ascii=False, indent=2, default=str))
        print(f"\n已保存 {out}  (耗时 {time.monotonic() - started:.0f}s)")
        return 0
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
    ev.add_argument("part", nargs="?", default="all", choices=["all", "screening", "temperature", "swing", "patterns", "auction", "quality", "lateline", "stoploss"])
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

    br = sub.add_parser("baserates", help="生成历史同类统计表（清单“历史同类”列与竞价“同类开盘后”用；建议每周一次）")
    br.set_defaults(func=cmd_baserates)

    au = sub.add_parser("auction", help="9:25 竞价：对已保存清单的股票抓竞价并描述（规则通过检验前只描述）")
    au.add_argument("--date", help="清单的目标交易日（默认最新清单）")
    au.set_defaults(func=cmd_auction)

    bk = sub.add_parser("backup", help="把数据目录打成一个压缩包（换设备用），附校验文件")
    bk.add_argument("--out", help="输出目录（默认数据目录旁的 stockfish-backups）")
    bk.set_defaults(func=cmd_backup)

    rs = sub.add_parser("restore", help="从压缩包恢复数据目录（不覆盖已有文件，逐个校验）")
    rs.add_argument("archive", help="longmai-data-*.tar.gz")
    rs.add_argument("--sha256", help="压缩包校验值（默认读同目录的 .sha256 文件）")
    rs.set_defaults(func=cmd_restore)

    pr = sub.add_parser("prune", help="每个交易日只保留一份有效清单（预览；--yes 才移到废纸篓）")
    pr.add_argument("--yes", action="store_true", help="确认移除")
    pr.set_defaults(func=cmd_prune)

    pg = sub.add_parser("page", help="生成本地网页（先看数据问题，再看摘要和完整清单）")
    pg.add_argument("target", nargs="?", help="交易日（默认最新清单）")
    pg.add_argument("--open", action="store_true", help="生成后用浏览器打开")
    pg.set_defaults(func=cmd_page)

    jd = sub.add_parser("judge", help="判断日记：记录自己的判断，到期自动判对错")
    jd.add_argument("action", choices=["add", "list", "score"])
    jd.add_argument("text", nargs="?", default="", help="判断原文（add 时）")
    jd.add_argument("--kind", default="free", choices=["stock_lu", "stock_bigloss", "stock_out5", "theme_fade", "free"])
    jd.add_argument("--subject", help="股票代码/名称，或板块名（theme_fade）")
    jd.add_argument("--days", type=int, help="几个交易日内（stock_lu 默认 1，theme_fade 默认 3）")
    jd.add_argument("--not", dest="negate", action="store_true", help="判断“不会发生”")
    jd.set_defaults(func=cmd_judge)

    tr = sub.add_parser("trade", help="交易记录：手动记下自己的买卖（不连券商），report 对照清单")
    tr.add_argument("action", choices=["add", "report"])
    tr.add_argument("date", nargs="?", help="成交日 YYYY-MM-DD")
    tr.add_argument("code", nargs="?", help="代码或名称")
    tr.add_argument("side", nargs="?", choices=["buy", "sell"])
    tr.add_argument("price", nargs="?", type=float)
    tr.add_argument("qty", nargs="?", type=int)
    tr.add_argument("--reason", help="为什么买/卖")
    tr.set_defaults(func=cmd_trade)

    sc = sub.add_parser("schedule", help="本机定时采集（竞价、清单、分时补抓、每周统计表与板块成分）")
    sc.add_argument("action", choices=["install", "uninstall", "status", "run"])
    sc.add_argument("job", nargs="?", help="run 时的任务名")
    sc.add_argument("--jobs", nargs="*", help="只安装/移除这些任务")
    sc.set_defaults(func=cmd_schedule)

    st = sub.add_parser("status", help="数据覆盖情况")
    st.set_defaults(func=cmd_status)
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
