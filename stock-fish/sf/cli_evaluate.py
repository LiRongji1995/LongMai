"""`sf evaluate`: the entry-free hit-rate exams (base run, pre-registered replays and studies). Moved out of
__main__ so the command list stays short; the exams themselves live in evaluate.py and the study modules."""
from __future__ import annotations

import contextlib
import io
import json
import time


def _quiet():
    """Silence BaoStock's login/logout prints."""
    return contextlib.redirect_stdout(io.StringIO())


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
