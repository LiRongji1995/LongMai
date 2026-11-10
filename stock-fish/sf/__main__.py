"""`sf` — personal short-term research CLI.

    sf update                 append missing sessions + capture today's limit/LHB pools
    sf premarket              build the pre-market list for the next session (3-column view)
    sf show [TARGET]          print a saved list
    sf auction                09:25 re-rank of the latest list with call-auction data
    sf review                 forward review of all saved lists (T+1-open entry, costs)
    sf backtest               historical replay; --grid / --walk-forward for parameter checks
    sf seats                  LHB seat detail: backfill, seat leaderboard, one stock's seats
    sf mood                   market sentiment indicators and phase
    sf status                 data store coverage
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


def _quiet():
    """Silence BaoStock's login/logout prints."""
    return contextlib.redirect_stdout(io.StringIO())


def _pct(frame: pd.DataFrame, cols=("hit_rate", "avg", "median", "unbuyable_rate", "limit_up_T", "broken_T", "mae2")):
    frame = frame.copy()
    for c in cols:
        if c in frame:
            frame[c] = frame[c].map(lambda v: f"{v:.2%}" if pd.notna(v) else "-")
    return frame


# ------------------------------------------------------------------ commands

def _capture_seats(day: str) -> str:
    from . import seats
    try:
        return seats.capture(day)
    except Exception as error:  # reported; the day stays "not captured" and `sf seats --backfill` retries
        return f"FAILED: {str(error)[:120]}"


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


def cmd_auto(args) -> int:
    """Unattended runs for launchd: only acts on trading days inside the right Shanghai time window."""
    import subprocess
    from datetime import time as dtime
    from . import premarket, store
    now = store.now_sh()
    today = now.strftime("%Y-%m-%d")
    log_dir = store.home() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"auto-{today}.log"

    def note(text):
        with log.open("a") as f:
            f.write(f"{now.strftime('%H:%M:%S')} [{args.job}] {text}\n")
        print(text)

    try:
        sessions = store.calendar()
        if today > sessions[-1]:
            sessions = store.calendar(refresh=True)
    except Exception as error:
        note(f"calendar unavailable, skipped: {error}")
        return 1
    if today not in sessions:
        note("not a trading day, skipped")
        return 0
    if args.job == "auction":
        if not dtime(9, 25) <= now.time() <= dtime(9, 30):
            note(f"outside 09:25-09:30 ({now.strftime('%H:%M')}), skipped")
            return 0
        if premarket.latest_run(today) is None:
            note(f"no pre-market list for {today}, skipped")
            return 1
        job = [sys.executable, "-m", "sf", "auction", "--target", today]
    else:
        if now.time() < dtime(18, 0):
            note(f"before 18:00 ({now.strftime('%H:%M')}), skipped")
            return 0
        target = store.next_session(sessions, today)
        if premarket.latest_run(target) is not None and not args.force:
            note(f"list for {target} already exists, skipped")
            return 0
        job = [sys.executable, "-m", "sf", "premarket"]
    result = subprocess.run(job, capture_output=True, text=True)
    output = "\n".join(line for line in (result.stdout + result.stderr).splitlines() if "login" not in line)
    note(f"exit {result.returncode}\n{output}")
    if args.job == "evening" and result.returncode == 0:
        review = subprocess.run([sys.executable, "-m", "sf", "review"], capture_output=True, text=True)
        note(f"review exit {review.returncode}\n{review.stdout[-3000:]}{review.stderr[-1000:]}")
    return result.returncode


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
    print(f"观察日 {result['observation']} | 新增 {len(result['fetched'])} 天 | 失败 {result['failed'] or '无'}")
    print(f"资金池: {json.dumps(status, ensure_ascii=False)}")
    print(f"耗时 {time.monotonic() - started:.1f}s")
    return 1 if result["failed"] else 0


def cmd_premarket(args) -> int:
    from . import pools, premarket, ranking, store
    started = time.monotonic()
    if not args.no_update:
        with _quiet():
            result = store.update(sessions_needed=premarket.HISTORY_SESSIONS, observation=args.date,
                                  workers=2, log=lambda *_: None)
        if result["failed"]:
            print(f"行情缺失，无法生成清单: {result['failed']}", file=sys.stderr)
            return 1
        pools.capture(result["observation"], log=lambda *_: None)
        _capture_seats(result["observation"])
    if not args.r2:
        return _premarket_longmai(args, started)
    with _quiet():
        result = premarket.build(args.date)
    path = premarket.save(result)
    picks = result["picks"].head(args.limit)
    m = result["risk"]["mood"]
    g = result["risk"].get("regulation", {})
    print(f"盘前清单  {result['observation']} → {result['target']}   情绪{m['tier']}档（分位 {m['level']}）：{m['advice']}"
          f"   监管温度{g.get('tier', '?')}（深市周处置 {g.get('szse_cases', '-')} 起）")
    if args.detail:
        s = result["sentiment"]
        print(f"涨停{s['limit_up']} 跌停{s['limit_down']} 炸板率{s['broken_rate']} 最高{s['max_height']}板 "
              f"晋级率{s['promotion_rate']} | 资格池{result['counts']['eligible']} R2入选{result['counts']['r2']}")
        cols = ["tier", "score", "name", "ret1", "board_height", "stages", "industry", "first_seal", "breaks", "plan"]
        print(picks[[c for c in cols if c in picks]].to_string())
    else:
        print(premarket.simple(picks).to_string(index=False))
    missing = [k for k, v in result["coverage"].items() if not v]
    if missing:
        print(f"\n缺失数据（按未知处理，未当作零）: {missing}")
    print(f"\n已保存 {path}/盘前清单.csv  (耗时 {time.monotonic() - started:.1f}s)")
    print("提示：建议为规则生成、未经验证，仅供研究。")
    return 0


def _premarket_longmai(args, started) -> int:
    from . import longmai
    with _quiet():
        result = longmai.premarket(args.date, update_external=not args.no_update)
    path = longmai.save(result)
    print("\n".join(longmai.header(result)))
    table = result["table"]
    for group in ("主线龙头", "观察", "回避"):
        part = table[table["分组"] == group]
        print(f"\n【{group}】{len(part)} 只")
        if len(part):
            print(part[longmai.VIEW[1:]].to_string(index=False))
    if result["seats"] != "ok" or not result["abnormal_index_ok"]:
        print(f"\n数据提示：席位 {result['seats']}；指数完整 {result['abnormal_index_ok']}（缺失按未知处理）")
    print(f"\n已保存 {path}/盘前清单.csv  (耗时 {time.monotonic() - started:.1f}s)")
    return 0


def cmd_longmai(args) -> int:
    from . import longmai
    if args.version is None:
        args.version = longmai.FORWARD_VERSION
    print(f"龙脉V{args.version} 组合回测（约5-10分钟）...")
    with _quiet():
        trades, ctx = longmai.backtest(version=args.version)
    rep = longmai.report(trades)
    rep30 = longmai.report(trades, extra_cost=0.0015)
    keys = ["trades", "avg", "hit", "payoff", "held", "total_return", "max_drawdown", "vetoed", "crowded", "no_slot", "no_entry"]
    print({k: rep[k] for k in keys})
    print(pd.DataFrame({s: [rep[f"{s}_n"], rep[f"{s}_avg"], rep[f"{s}_t"]] for s in ("train", "test1", "test2")},
                       index=["笔数", "平均", "t"]).to_string())
    print(f"成本0.30%：平均 {rep30['avg']}，累计 {rep30['total_return']}，最大回撤 {rep30['max_drawdown']}")
    print("离场构成", rep["exit_mix"])
    print("分年", rep["by_year"])
    robust = None
    if args.robust:
        rows = []
        grid = {**longmai.ROBUST, **(longmai.ROBUST_V3 if args.version == 3 else {})}
        for key, vals in grid.items():
            for v in vals:
                with _quiet():
                    r = longmai.report(longmai.backtest({key: v}, ctx=ctx, version=args.version)[0])
                rows.append(dict(variant=f"{key}={v}", trades=r.get("trades"), avg=r.get("avg"),
                                 total=r.get("total_return"), dd=r.get("max_drawdown"),
                                 test1=r.get("test1_avg"), test2=r.get("test2_avg")))
        robust = pd.DataFrame(rows).set_index("variant")
        print(robust.to_string())
    print(f"已保存 {longmai.save_backtest(trades, rep, robust, version=args.version)}")
    return 0


def cmd_show(args) -> int:
    from . import premarket
    run = premarket.latest_run(args.target)
    if run is None:
        print("没有已保存的清单")
        return 1
    simple_path = run / "盘前清单.csv"
    if simple_path.exists():
        frame = pd.read_csv(simple_path)
    else:  # lists saved before the 3-column format
        picks = pd.read_csv(run / "picks.csv", index_col=0)
        frame = premarket.simple(picks)
    summary = json.loads((run / "summary.json").read_text())
    if (run / "盘前概况.txt").exists():
        print((run / "盘前概况.txt").read_text().strip())
    else:
        print(f"目标日 {summary['target']}  情绪 {summary.get('regime')}：{summary.get('advice')}")
    print(frame.to_string(index=False))
    return 0


def cmd_auction(args) -> int:
    from . import auction
    result = auction.run(args.target)
    if result["window"] == "TOO_EARLY":
        print("注意：现在早于09:25，竞价尚未撮合，价格仅为参考。")
    elif result["window"] == "AFTER_OPEN":
        print("注意：已过09:30，成交额包含连续竞价，竞价额比例偏大。")
    print(result["simple"].to_string(index=False))
    print(f"\n已保存到 {result['run']}")
    return 0


def _print_tables(result: dict, keys):
    for key, title in keys:
        table = result.get(key)
        if isinstance(table, pd.DataFrame) and not table.empty:
            print(f"\n== {title} ==")
            print(_pct(table).to_string())


def cmd_review(args) -> int:
    from . import outcomes, review
    with _quiet():
        rows = review.collect(args.since)
    if rows.empty:
        print("没有可复盘的清单")
        return 1
    result = review.report(rows, args.ret, args.cost)
    print(f"复盘口径: {args.ret}（T日开盘买入），扣成本 {args.cost:.2%}；买不进(开盘即涨停)不计入收益")
    if result["pending_targets"]:
        print(f"尚未成熟（缺后续行情）: {result['pending_targets']}")
    _print_tables(result, [("per_day", "逐日"), ("cohorts", "入选 vs 差一点入选 vs 其他"), ("tier", "按档位"),
                           ("regime", "按情绪"), ("score_q", "按评分分位")])
    if args.detail:
        _print_tables(result, [("detail", "逐股明细")])
    print(f"\n资金曲线: {result['equity_summary']}")
    path = review.save(result, dict(ret=args.ret, cost=args.cost))
    print(f"已保存 {path}")
    return 0


def cmd_backtest(args) -> int:
    from . import backtest, strategy
    started = time.monotonic()
    days = backtest.prepare(args.start, args.end)
    meta = dict(start=days[0]["obs"], end=days[-1]["obs"], ret=args.ret, cost=args.cost,
                strategy_version=strategy.STRATEGY_VERSION,
                note="historical replay ranks without pool facts (seal time, LHB unknown)")
    if args.grid:
        overrides = [{}] + [o for o in backtest.parse_grid(args.grid) if o]
        if args.walk_forward:
            table = backtest.walk_forward(days, overrides, args.walk_forward, args.ret, args.cost)
            print("== walk-forward（每折用此前数据选参，只报告样本外结果） ==")
        else:
            table = backtest.grid(days, overrides, args.ret, args.cost)
            print("== 参数网格（全样本，注意过拟合） ==")
        print(_pct(table, ("hit_rate", "avg", "median", "train_avg", "test_avg", "test_hit", "default_test_avg",
                           "unbuyable_rate", "limit_up_T", "broken_T", "mae2")).to_string())
        path = backtest.save("grid", {"grid": table}, {**meta, "grid": args.grid, "walk_forward": args.walk_forward})
    else:
        overrides = backtest.parse_grid(args.param) if args.param else [{}]
        rows = backtest.run(days, strategy.params(overrides[0]))
        result = backtest.report(rows, args.ret, args.cost)
        print(f"回放 {meta['start']} ~ {meta['end']}，{len(days)} 个交易日；口径 {args.ret}，成本 {args.cost:.2%}")
        _print_tables(result, [("cohorts", "入选 vs 差一点入选 vs 其他"), ("tier", "按档位"), ("stage", "按形态"),
                               ("regime", "按情绪"), ("score_q", "按评分分位"), ("gap", "按开盘缺口"),
                               ("near_miss_gate", "差一点入选：卡在哪道门槛")])
        print(f"\n资金曲线（每日等权买入全部入选）: {result['equity_summary']}")
        path = backtest.save("replay", result, {**meta, "params": overrides[0]})
    print(f"已保存 {path}  (耗时 {time.monotonic() - started:.1f}s)")
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
    print(f"板块 {result['boards']} 个，成分 {result['members']} 条，失败 {len(result['failures'])} 个，耗时 {time.monotonic() - started:.0f}s")
    return 0


def cmd_research(args) -> int:
    from . import research
    with _quiet():
        data = research.dataset(refresh=args.refresh, log=lambda *_: None)
    seg, (c1, c2) = research.segments(data)
    print(f"样本 {data['obs'].min()} ~ {data['obs'].max()}，训练 <{c1} ≤ 验证1 <{c2} ≤ 验证2；按天统计，口径 {args.ret}，成本 {args.cost:.2%}")
    print("判定样本外有效：两个验证段都为正且 t≥1.5、各≥20天\n")
    if args.scan:
        table = research.scan(data, args.ret, args.cost)
        valid = table[table["oos_valid"]]
        print(f"扫描 {len(table)} 条规则，样本外有效 {len(valid)} 条（注意多重比较，个别为偶然）")
        table = valid.head(args.top) if len(valid) else table.head(args.top)
    else:
        rules = args.rule or list(research.PRESETS)
        table = pd.DataFrame([research.evaluate(data, research.PRESETS.get(r, r), args.ret, args.cost, seg) for r in rules])
    for c in [c for c in table.columns if c.endswith("_avg")]:
        table[c] = table[c].map(lambda v: f"{v:+.2%}" if pd.notna(v) else "-")
    for c in [c for c in table.columns if c.endswith("_t")]:
        table[c] = table[c].map(lambda v: f"{v:.1f}" if pd.notna(v) else "-")
    print(table.to_string(index=False))
    return 0


def cmd_mainline(args) -> int:
    from . import mainline
    started = time.monotonic()
    cache = {}
    variants = [{}]
    for spec in args.vary or []:
        key, values = spec.split("=", 1)
        variants += [{key: float(v) if "." in v else int(v)} for v in values.split(",")]
    rows = []
    for v in variants:
        with _quiet():
            trades = mainline.backtest(v, args.start, args.end, cache=cache, log=lambda *_: None)
        summary = mainline.summarize(trades)
        rows.append(dict(variant=";".join(f"{k}={x}" for k, x in v.items()) or "default", **summary))
        if not v and args.save:
            out = store_path = mainline.store.home() / "backtests"
            store_path.mkdir(parents=True, exist_ok=True)
            trades.to_csv(store_path / f"mainline-trades-{time.strftime('%Y%m%dT%H%M%S')}.csv", encoding="utf-8-sig", index=False)
    table = pd.DataFrame(rows).set_index("variant")
    print("主线龙头 + 拥挤离场 回测（按入场日等权、t值按天数；板块成分为当前快照，存在幸存偏差）")
    print(table.drop(columns=["exit_mix"], errors="ignore").to_string())
    print("\n离场原因占比:")
    for name, mix in table.get("exit_mix", pd.Series(dtype=object)).items():
        print(f"  {name}: {mix}")
    print(f"耗时 {time.monotonic() - started:.0f}s")
    return 0


# ------------------------------------------------------------------ parser

def parser() -> argparse.ArgumentParser:
    from . import outcomes
    root = argparse.ArgumentParser(prog="sf", description="StockFish 个人短线研究工具")
    sub = root.add_subparsers(dest="command", required=True)

    up = sub.add_parser("update", help="补齐行情并采集当日涨停/炸板/跌停池与龙虎榜")
    up.add_argument("--date", help="观察日（默认最近一个已收盘交易日）")
    up.add_argument("--sessions", type=int, default=30, help="保证最近N个交易日都在库中")
    up.add_argument("--workers", type=int, default=2)
    up.set_defaults(func=cmd_update)

    pm = sub.add_parser("premarket", help="生成下一交易日盘前清单")
    pm.add_argument("--date", help="观察日（默认最近一个已收盘交易日）")
    pm.add_argument("--limit", type=int, default=50)
    pm.add_argument("--detail", action="store_true", help="显示详细字段")
    pm.add_argument("--no-update", action="store_true", help="不更新数据")
    pm.add_argument("--r2", action="store_true", help="旧版R2清单（SF_R2_LEAN_V1）")
    pm.set_defaults(func=cmd_premarket)

    sh = sub.add_parser("show", help="查看已保存的清单")
    sh.add_argument("target", nargs="?", help="目标交易日（默认最新）")
    sh.set_defaults(func=cmd_show)

    au = sub.add_parser("auction", help="09:25竞价后重排最新清单")
    au.add_argument("--target", help="目标交易日（默认最新清单）")
    au.set_defaults(func=cmd_auction)

    def outcome_args(p):
        p.add_argument("--ret", default=outcomes.PRIMARY, choices=["r_oc", "r_n1o", "r_n1c", "r_n2c"],
                       help="收益口径：r_n1o=T开盘买T+1开盘卖（默认）")
        p.add_argument("--cost", type=float, default=outcomes.DEFAULT_COST, help="单笔往返成本，默认0.0015")

    rv = sub.add_parser("review", help="复盘所有已保存清单")
    rv.add_argument("--since", help="只看此目标日之后")
    rv.add_argument("--detail", action="store_true", help="显示逐股明细")
    outcome_args(rv)
    rv.set_defaults(func=cmd_review)

    bt = sub.add_parser("backtest", help="历史回放与参数检验")
    bt.add_argument("--start"), bt.add_argument("--end")
    bt.add_argument("--param", nargs="*", help="单组参数覆盖，如 STRONG_START.ret1_min=0.07")
    bt.add_argument("--grid", nargs="*", help="参数网格，如 STRONG_START.ret1_min=0.05,0.06,0.07")
    bt.add_argument("--walk-forward", type=int, metavar="FOLDS", help="与--grid合用：滚动样本外检验")
    outcome_args(bt)
    bt.set_defaults(func=cmd_backtest)

    th = sub.add_parser("themes", help="刷新概念/行业板块成分（约20分钟，建议每周一次）")
    th.add_argument("--no-history", action="store_true", help="不取板块起始日期（会有前视偏差）")
    th.set_defaults(func=cmd_themes)

    rs = sub.add_parser("research", help="规则研究：按天统计、三段样本外检验")
    rs.add_argument("--rule", nargs="*", help="规则表达式或预设名，如 \"r2_selected and amount>=1e9\"")
    rs.add_argument("--scan", action="store_true", help="自动扫描单条件与两两组合")
    rs.add_argument("--top", type=int, default=30)
    rs.add_argument("--refresh", action="store_true", help="重建研究数据集")
    rs.add_argument("--ret", default="r_n1c", choices=["r_oc", "r_n1o", "r_n1c", "r_n2c"])
    rs.add_argument("--cost", type=float, default=0.0015)
    rs.set_defaults(func=cmd_research)

    ml = sub.add_parser("mainline", help="主线龙头+拥挤离场策略回测")
    ml.add_argument("--start"), ml.add_argument("--end")
    ml.add_argument("--vary", nargs="*", help="参数对比，如 amount_rank_max=50,200,10000")
    ml.add_argument("--save", action="store_true", help="保存默认参数的逐笔交易")
    ml.set_defaults(func=cmd_mainline)

    lm = sub.add_parser("longmai", help="龙脉组合回测（--robust 做参数稳健性检查）")
    lm.add_argument("--robust", action="store_true")
    lm.add_argument("--version", type=int, default=None, choices=[1, 2, 3], help="默认为前向检验版本V1")
    lm.set_defaults(func=cmd_longmai)

    se = sub.add_parser("seats", help="龙虎榜席位：回填、席位榜、个股席位")
    se.add_argument("--backfill", type=int, help="回填最近N个交易日的席位明细")
    se.add_argument("--workers", type=int, default=4)
    se.add_argument("--code", help="查看某只股票某天的席位，如 600000")
    se.add_argument("--date")
    se.add_argument("--top", type=int, default=20)
    se.add_argument("--min-events", type=int, default=30)
    se.set_defaults(func=cmd_seats)

    ao = sub.add_parser("auto", help="定时任务入口：交易日自动跑竞价或晚间清单（供launchd调用）")
    ao.add_argument("job", choices=["auction", "evening"])
    ao.add_argument("--force", action="store_true")
    ao.set_defaults(func=cmd_auto)

    cr = sub.add_parser("crowding", help="短线资金水温（复盘用，不选股）")
    cr.add_argument("--days", type=int, default=20)
    cr.set_defaults(func=cmd_crowding)
    mo = sub.add_parser("mood", help="市场情绪指标与阶段")
    mo.add_argument("--days", type=int, default=10)
    mo.set_defaults(func=cmd_mood)

    st = sub.add_parser("status", help="数据覆盖情况")
    st.set_defaults(func=cmd_status)
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
