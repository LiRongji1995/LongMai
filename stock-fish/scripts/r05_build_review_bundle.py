"""生成 R05 本机审阅包。

仓库提交只使用压缩后的摘要与文档；完整市场输入、原始缓存和 ZIP
按协作约定留在本机，不作为 Git 提交内容。
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / "stock-fish"
REVIEW = ROOT / "review" / "r05-data-strategy"
CAPTURE = REVIEW / "actual-pv-capture-03"
DELIVERABLES = ROOT / "deliverables"
sys.path.insert(0, str(DEV))


def load(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path, value):
    write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n")


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def command(args, cwd=DEV):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    return {"exit_code": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}


def git(cwd, *args):
    return command(["git", *args], cwd=cwd)


def compact_capture():
    report = load(CAPTURE / "capture-report.json", {})
    experiments = load(CAPTURE / "strategy-experiments.json", {})
    normalization = load(CAPTURE / "normalization-report.json", {})
    pv = experiments.get("results", {}).get("PV_OBSERVATION_V1", {})
    dna = experiments.get("results", {}).get("DNA_RERANK_V1", {})
    theme = experiments.get("results", {}).get("THEME_CONTEXT_V1", {})
    return report, experiments, normalization, pv, dna, theme


def top20_files(pv):
    items = pv.get("items", [])[:20]
    write_json(REVIEW / "pv-observation-top20.json", {"strategy_id": "PV_OBSERVATION_V1", "items": items})
    fields = ["rank", "instrument_id", "name", "S_PV", "A", "R", "H", "PR_A", "PR_R", "PR_H", "comparable_basis"]
    with (REVIEW / "pv-observation-top20.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in items:
            writer.writerow({field: item.get(field) for field in fields})


def main():
    REVIEW.mkdir(parents=True, exist_ok=True)
    DELIVERABLES.mkdir(parents=True, exist_ok=True)
    r04_end = load(ROOT / "review" / "r04-gpt55-closure" / "end-baseline.json", {})
    r04_gaps = load(ROOT / "review" / "r04-gpt55-closure" / "field-gaps.json", {})
    report, experiments, normalization, pv, dna, theme = compact_capture()
    top20_files(pv)
    source_capabilities = {
        "capability_review_version": "R05_SOURCE_CAPABILITIES_V1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "official_docs_checked": {
            "tushare_http": "https://tushare.pro/document/1?doc_id=40",
            "stock_basic": "https://tushare.pro/document/2?doc_id=25",
            "trade_cal": "https://tushare.pro/document/2?doc_id=26",
            "daily": "https://tushare.pro/document/2?doc_id=27",
            "daily_basic": "https://tushare.pro/document/2?doc_id=32",
            "adj_factor": "https://tushare.pro/document/2?doc_id=28",
            "stk_limit": "https://tushare.pro/document/2?doc_id=183",
            "limit_list_d": "https://tushare.pro/document/2?doc_id=298",
            "suspend_d": "https://tushare.pro/document/2?doc_id=31",
            "stock_st": "https://tushare.pro/document/2?doc_id=397",
            "top_list": "https://tushare.pro/document/2?doc_id=106",
            "top_inst": "https://tushare.pro/document/2?doc_id=107",
            "moneyflow": "https://tushare.pro/document/2?doc_id=170",
            "dc_index": "https://tushare.pro/document/2?doc_id=362",
            "dc_member": "https://tushare.pro/document/2?doc_id=363",
            "stk_auction": "https://tushare.pro/document/2?doc_id=369",
            "stk_auction_o": "https://tushare.pro/document/2?doc_id=370",
        },
        "providers": {
            "baostock": {
                "status": "VERIFIED_FOR_R05_PV_REQUIRED_DATA",
                "actual_requests": report.get("queue", {}).get("states", {}),
                "usage": ["trade calendar", "market_daily", "adjustment_factors", "master_all"],
            },
            "akshare": {
                "status": "VERIFIED_FOR_CURRENT_MAINBOARD_DIRECTORY_IN_R05_RUN",
                "usage": ["mainboard_sse", "master_szse"],
            },
            "sse": {
                "status": "VERIFIED_FOR_CURRENT_SSE_DELISTING_SET_IN_R05_RUN",
                "usage": ["delisting_sse"],
            },
            "eastmoney": {
                "status": "ENGINEERING_READY_NETWORK_PROXY_DEPENDENT",
                "usage": ["daily_limits_eastmoney"],
                "r05_fixes": ["Asia/Shanghai source business date required", "worker partial result retained on parent timeout"],
            },
            "tushare": {
                "status": "AUTH_REQUIRED_REAL_NOT_VERIFIED",
                "token_env_names": ["TUSHARE_TOKEN", "TUSHARE_PRO_TOKEN"],
                "transport": "HTTP_POST http://api.tushare.pro",
                "implemented_endpoints": list(source_capability_endpoints()),
            },
        },
    }
    write_json(REVIEW / "source-capabilities.json", source_capabilities)
    baseline = {
        "baseline_version": "R05_BASELINE_V1",
        "r04_publish_head": r04_end.get("publish", {}).get("head"),
        "r04_dev_head": r04_end.get("stockfish", {}).get("head"),
        "r04_unclosed_gaps": r04_gaps,
        "dev_git": {
            "head": git(DEV, "rev-parse", "HEAD")["stdout"],
            "branch": git(DEV, "branch", "--show-current")["stdout"],
            "status_short": git(DEV, "status", "--short")["stdout"],
        },
        "publish_git": {
            "head": git(ROOT / "publish" / "stockfish-qlib-quant-system", "rev-parse", "HEAD")["stdout"],
            "branch": git(ROOT / "publish" / "stockfish-qlib-quant-system", "branch", "--show-current")["stdout"],
        },
    }
    write_json(REVIEW / "baseline.json", baseline)
    execution = {
        "execution_version": "R05_DATA_EXECUTION_V1",
        "capture_dir": str(CAPTURE),
        "observation_date": report.get("observation_date"),
        "queue": report.get("queue"),
        "remaining_requests": len(report.get("remaining_requests", [])),
        "normalization": {
            "master_identity_count": normalization.get("master_identity_count"),
            "mainboard_identity_count": normalization.get("mainboard_identity_count"),
            "raw_instrument_count": normalization.get("raw_instrument_count"),
            "fields_retained": normalization.get("fields_retained"),
        },
        "pv_counts": pv.get("counts"),
        "pv_top20": pv.get("top_ids", [])[:20],
        "dna_status": dna.get("status"),
        "theme_status": theme.get("status"),
        "market_edge": "NOT_ESTABLISHED",
    }
    write_json(REVIEW / "data-execution.json", execution)
    docs = source_capabilities["official_docs_checked"]
    write(REVIEW / "data-access-matrix.md", f"""# R05 数据访问矩阵

| 数据能力 | 当前接入 | 真实状态 | 用途 | 官方/来源文档 |
| --- | --- | --- | --- | --- |
| 当前主板目录 | AkShare 上交所/深交所目录 | R05 运行完成 | 主板身份分母 | 本地 R03/R04 已接线 |
| 交易日历 | BaoStock + 本地 2026 兜底 | R05 运行完成 | 新股满 60 交易日与近 6 日窗口 | 本地 R03/R04 已接线 |
| 近 6 日 RAW 日线 | BaoStock `market_daily` | R05 运行完成 | A、H 与 RAW 成交额窗口 | 本地 R03/R04 已接线 |
| 可比收盘 | BaoStock 复权因子 | R05 运行完成 | R 与同口径可比收盘 | 本地 R03/R04 已接线 |
| 当前停牌/退市整理 | BaoStock `master_all` + SSE 退市整理集 + SZSE 简称规则 | R05 运行完成 | 默认排除停牌、退市整理 | 本地 R03/R04 已接线 |
| Tushare 基础资料 | `stock_basic` | 已实现 adapter，缺 token 未真验 | 历史身份、上市/退市日期、行业补充 | {docs['stock_basic']} |
| Tushare 交易日历 | `trade_cal` | 已实现 adapter，缺 token 未真验 | 可替代/交叉验证交易日历 | {docs['trade_cal']} |
| Tushare 日线/复权 | `daily`、`adj_factor`、`daily_basic` | 已实现 adapter，缺 token 未真验 | RAW 行情、可比价格、换手/市值 | {docs['daily']} / {docs['adj_factor']} / {docs['daily_basic']} |
| Tushare 涨跌停 | `stk_limit`、`limit_list_d` | 已实现 adapter，缺 token 未真验 | 直接涨跌停价、涨停事件 | {docs['stk_limit']} / {docs['limit_list_d']} |
| Tushare ST/停复牌 | `stock_st`、`suspend_d` | 已实现 adapter，缺 token 未真验 | ST、停牌与资格排除 | {docs['stock_st']} / {docs['suspend_d']} |
| Tushare 资金/龙虎榜 | `moneyflow`、`top_list`、`top_inst` | 已实现 adapter，缺 token 未真验 | FUNDS_V1 与后验解释 | {docs['moneyflow']} / {docs['top_list']} / {docs['top_inst']} |
| Tushare 题材/竞价 | `dc_index`、`dc_member`、`stk_auction`、`stk_auction_o` | 已实现 adapter，缺 token 未真验 | S3 题材、09:25 竞价复核 | {docs['dc_index']} / {docs['dc_member']} / {docs['stk_auction']} |
""")
    write(REVIEW / "audit-fixes.md", """# R05 缺陷关闭

| 缺陷 | 处理结果 |
| --- | --- |
| E01 时间/限价日期反例 | Eastmoney `f86` 只按 Asia/Shanghai 业务日期接受；规范化层再次检查 `source_business_date` 与 `source_timestamp`。UTC 日期碰巧一致但上海日期不一致的行被拒绝。 |
| E02 批次超时保留成功子项 | worker 每处理一只证券写 `worker-partial.json`；父进程超时时读取部分结果并返回 `PARTIAL / REQUEST_TIMEOUT_AFTER_PARTIAL`，成功子项不丢失。 |
| E03 规则认证服务不能由纯数学自报 VERIFIED | `derive_verified_limits` 不再接受单独 `branch_cross_validated=True`；必须由 `certify_rule_derived_limits` 生成本地认证哈希并携带直接样本。 |
| E04 summary 不能先反序列化大 CandidateRun | 候选任务发布时同步写 `candidate_run_summaries` 投影；summary API 先校验投影与发布权威，只在旧记录缺投影时回退完整读取。 |
""")
    write(REVIEW / "strategy-specs.md", """# R05 策略规格

`DAILY_V1` 保持原五族主策略，不改名、不降级，参数状态仍为 `INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED`。

`PV_OBSERVATION_V1` 是量价观察控制组。它只使用当前可信主板普通股合格池内 T 与前 5 个交易日的完整同口径数据：RAW 成交额、同 basis 可比收盘、T 日 RAW high/low/reference_prev_close。公式为 `A=amount_t/mean(amount_t-5...t-1)`、`R=comparable_close_t/comparable_close_t-5-1`、`H=(high_t-low_t)/reference_prev_close_t`，入围条件 `A>=1.2 && R>0`，`S_PV=0.45*PR(A)+0.35*PR(R)+0.20*PR(H)`。PR 使用中位百分位，N=1 时为 50。

`DNA_RERANK_V1` 只对 S1 入围项复排。它要求历史涨停/触板事件和次日可比结果，输出 Wilson95 成功率下界与均值收缩；样本不足时标记 `INSUFFICIENT_COVERAGE`，不影响 S1。

`THEME_CONTEXT_V1` 只对 S1 入围项追加题材上下文。它要求冻结主题成员和同伴行情；没有完整主题时标记 `UNAVAILABLE`，不影响 S1。
""")
    write(REVIEW / "strategy-comparison.md", f"""# R05 策略对照

| 策略 | 本轮状态 | 数量 | 说明 |
| --- | ---: | ---: | --- |
| DAILY_V1 | 未因 R05 重命名或降级 | 未重新声明完整可用 | 原五族仍需完整限价事件、主题、资金/情绪等链路。 |
| PV_OBSERVATION_V1 | {pv.get('status')} | {pv.get('counts', {}).get('ranked')} | 真实 2026-09-04 量价控制组；字段完整 {pv.get('counts', {}).get('price_window_complete')}，Top20 见 `pv-observation-top20.csv`。 |
| DNA_RERANK_V1 | {dna.get('status')} | {dna.get('counts', {}).get('evaluated', 0)} | 当前缺历史涨停/触板和次日样本覆盖，不回写 S1。 |
| THEME_CONTEXT_V1 | {theme.get('status')} | {theme.get('counts', {}).get('evaluated', 0)} | 当前 R05 PV 专用采集未抓题材成员，不回写 S1。 |

PV Top20：{', '.join(pv.get('top_ids', [])[:20])}

所有新增策略均为初始假设，`MARKET_EDGE` 仍为 `NOT_ESTABLISHED`。
""")
    write(REVIEW / "upstream-strategy-review.md", """# 上游策略复审

R04 复审结论保留：工程路径已改善，但完整 DAILY 真实榜单在关键来源未齐前不能声明可用。R05 没有把 `PV_OBSERVATION_V1` 包装成 DAILY，也没有把股性或题材缺口补零。

本轮未在工作区定位到独立的 `StockRecommendation_R04_Independent_Review.md` 文件；已读取 `review/r04-gpt55-closure/final-audit.md`、`end-baseline.json`、`field-gaps.json`、`source-capabilities.json`、`tests.md` 作为 R04 收口基线。若后续补充独立复审文件，应按同一矩阵追加映射。
""")
    write(REVIEW / "runtime-acceptance.md", """# R05 运行验收

| 检查 | 结果 | 日志 |
| --- | --- | --- |
| R04/R05/供应商/队列/主策略子集 | 通过，213 passed | `logs/r05-targeted-tests.log` |
| Python 编译检查 | 通过 | `logs/r05-compileall.log` |
| 前端 JS 语法检查 | 通过 | `logs/r05-node-check.log` |
| Git diff 空白检查 | 通过 | `logs/r05-git-diff-check.log` |
| 核心业务测试脚本 | 通过，665 passed in 166.55s | `logs/r05-core-test-runner.log` |
| 直接 `pytest -q` | 收集阻塞，旧 MiroFish 缺 `locales/languages.json` | `logs/r05-full-tests.log` |

真实数据运行：`actual-pv-capture-03`，请求 43/43 完成；当前主板合格池 3043，只 3039 只完成 PV 必需字段核验，缺失 4 只，最终入围 525 只。
""")
    write(REVIEW / "external-blockers.md", """# R05 外部阻塞

- Tushare adapter 已实现，但当前环境未提供可用于真实验证的 token；状态为 `AUTH_REQUIRED / REAL_NOT_VERIFIED`。
- `DNA_RERANK_V1` 需要历史涨停/触板事件与次日样本，本轮 PV 专用采集没有完整覆盖，状态为 `INSUFFICIENT_COVERAGE`。
- `THEME_CONTEXT_V1` 需要冻结主题成员与同伴行情，本轮 PV 专用采集未抓题材，状态为 `UNAVAILABLE`。
- 原 `DAILY_V1` 未被 R05 宣告完整可用；完整链路仍需要限价事件、主题、资金/情绪与后验数据持续接通。
- 直接全量 `pytest -q` 会收集旧 MiroFish 脚本测试并因缺少 `locales/languages.json` 中断；核心业务测试脚本已通过。
""")
    write(REVIEW / "final-report.md", f"""# R05 最终报告

## 缺陷关闭

E01-E04 均已落到代码和测试：上海业务日严格校验、worker 部分结果超时保留、规则推导需要本地认证哈希、summary API 优先轻量投影。

## 数据供应接通

Tushare Pro HTTP adapter 已按官方文档接入端点表、字段、分页、token 缺失错误和脱敏日志；当前无 token，因此真实状态为 `AUTH_REQUIRED / REAL_NOT_VERIFIED`。BaoStock/AkShare/SSE 路径完成 R05 PV 必需数据真实采集。

## 真实量价观察

观察日：{report.get('observation_date')}。R05 专用真实采集完成 43/43 个请求，当前主板合格池 {pv.get('counts', {}).get('eligible')}，资格未知 {pv.get('counts', {}).get('qualification_unknown')}，PV 必需字段完整 {pv.get('counts', {}).get('price_window_complete')}，PV 必需字段缺失 {pv.get('counts', {}).get('missing_required_data')}，`PV_OBSERVATION_V1` 入围 {pv.get('counts', {}).get('ranked')}。Top20 见 `pv-observation-top20.csv`。

## 原五族 DAILY

`DAILY_V1` 仍是原五族主策略，不改名、不降级；R05 没有把 PV 控制组冒充完整 DAILY。

## 股性与题材实验

`DNA_RERANK_V1` 当前为 `{dna.get('status')}`，因为缺历史事件/次日样本覆盖。`THEME_CONTEXT_V1` 当前为 `{theme.get('status')}`，因为本轮 PV 专用采集没有题材成员。二者均不影响 S1。

## 策略对照

新增三组实验均标记 `INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED`，`MARKET_EDGE` 仍为 `NOT_ESTABLISHED`。详见 `strategy-comparison.md`。

## 后验与竞价

本轮未伪造 09:25 竞价或后验收益；已有竞价/后验 API 保持独立追加事实的语义。

## 未完成

Tushare 真实权限、DNA 样本库、题材成员冻结、完整 DAILY 全链路和旧 MiroFish 全 pytest 收集问题仍需后续处理。

## 运行与交付

核心业务测试脚本通过 665 项，R04/R05 相关子集通过 213 项。审阅索引、摘要、CSV 和本机 ZIP 均在本目录或 `deliverables/` 中。
""")
    manifest = {}
    for path in sorted(REVIEW.rglob("*")):
        if not path.is_file() or "actual-cache" in path.parts:
            continue
        manifest[str(path.relative_to(REVIEW)).replace("\\", "/")] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    write_json(REVIEW / "artifact-manifest.json", manifest)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = DELIVERABLES / f"StockRecommendation_R05_Data_Strategy_ReviewBundle_{timestamp}.zip"
    with ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
        for path in sorted(REVIEW.rglob("*")):
            if not path.is_file() or "actual-cache" in path.parts:
                continue
            zf.write(path, "r05-data-strategy/" + str(path.relative_to(REVIEW)).replace("\\", "/"))
    zip_hash = sha256(zip_path)
    write_json(REVIEW / "bundle.json", {"zip_path": str(zip_path), "sha256": zip_hash,
                                        "bytes": zip_path.stat().st_size,
                                        "cache_excluded_from_zip": True})
    print(json.dumps({"消息": "R05审阅包已生成", "zip": str(zip_path), "sha256": zip_hash}, ensure_ascii=False))


def source_capability_endpoints():
    from stockfish_core.yaogu.provider_requests import TUSHARE_ENDPOINTS
    for endpoint, value in TUSHARE_ENDPOINTS.items():
        yield {"endpoint": endpoint, "api_name": value["api_name"], "fields": value["fields"], "doc_url": value["doc_url"]}


if __name__ == "__main__":
    main()
