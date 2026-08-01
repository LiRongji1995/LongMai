# Phase 1 Round 1C 文档与接口合同基线正式报告

文档状态：**Draft**

Round 验证结论：**PASS WITH LIMITATIONS**

## Round 基线

- Round ID：`phase-01-round-01c-documentation-interface-contract-baseline`
- Phase：Phase 1
- branch：`main`
- Base commit：`b9874e26240f8dfffdbf3ff2d270ffae9ccab92a`
- Final commit：本报告所在 Git commit；确切 SHA 由最终 `ROUND_METADATA.json`、`EVIDENCE.json` 与交付记录提供，避免在提交内容中形成自引用。
- 初始状态：工作树 clean，HEAD 与 `origin/main` 相同，ahead/behind 为 `0/0`，Round 1B 冻结提交是 HEAD 祖先。
- 单一目标：建立文档地图、数据合同、Strategy/Portfolio/Risk 接口合同、回测验证标准、策略生命周期与本地验证手册。

## 已完成范围

创建六份指定文档：

1. [`docs/DOCUMENTATION_MAP.md`](../DOCUMENTATION_MAP.md)
2. [`docs/contracts/DATA_CONTRACTS.md`](../contracts/DATA_CONTRACTS.md)
3. [`docs/contracts/STRATEGY_PORTFOLIO_RISK_CONTRACTS.md`](../contracts/STRATEGY_PORTFOLIO_RISK_CONTRACTS.md)
4. [`docs/research/BACKTEST_VALIDATION_STANDARD.md`](../research/BACKTEST_VALIDATION_STANDARD.md)
5. [`docs/research/STRATEGY_LIFECYCLE.md`](../research/STRATEGY_LIFECYCLE.md)
6. [`docs/operations/LOCAL_RUNBOOK.md`](../operations/LOCAL_RUNBOOK.md)

治理要求正式 Round 报告且 Review Bundle generator 要求 `--report-path` 指向 committed report，因此额外创建本报告。没有创建任何 Deferred 文档。

`DOCUMENTATION_MAP.md` 登记全部 34 份既有 tracked `.md` / `.txt` 文档、本 Round 六文档和本报告，定义四值 status、冲突优先级、唯一权威、禁止重复、owner role、更新/取代/归档流程和跨文档术语；13 份未来文档仅以 `planning_state: Deferred` 登记。

## 合同主要决策

### Data

- 统一 `event_time`、`availability_time`、`ingestion_time`、`as_of_time`、UTC 与 IANA exchange timezone。
- Bar 使用 `[bar_start_time, bar_end_time)` 且 `event_time == bar_end_time`；point-in-time 以 availability 判断。
- 精确 Decimal 在 JSON 边界使用十进制字符串；离散 size/volume 使用非负整数；非有限浮点拒绝。
- 定义 Bar、Tick、Corporate Action、Feature Schema、session、quality、source、`data_version` 和 manifest 绑定。
- 当前只承认 SYNTH Daily fixture 的有限基础设施事实；Tick、Corporate Action pipeline、Feature pipeline、生产 source 与完整 point-in-time 数据库均为 TBD。

### Strategy / Portfolio / Risk / Execution

- 稳定边界为 `StrategySignal → PortfolioTarget → RiskDecision → ExecutionIntent`。
- Risk decision 只允许 `APPROVED`、`REJECTED`、`PARTIALLY_APPROVED`。
- long-only、无杠杆、gross/net exposure、有效期、stale、duplicate、幂等与 fail-closed 不变量已形成合同。
- Strategy 不得输出 Broker order；ExecutionIntent 不是 order，且只能由已批准或部分批准的目标生成。
- 当前没有真实策略、Portfolio/Risk runtime、ExecutionIntent consumer、order path 或真实风险预算。

### Backtest validation

- 规定可证伪 hypothesis、train/validation/test 隔离、walk-forward、benchmark、成本、流动性/capacity、偏差控制、parameter search、多重检验、稳定性/sensitivity/stress/regime 和复现 identity。
- 每次回测绑定 Git commit、configuration、interval、benchmark、data/strategy/parameter/risk/cost version、manifest、Golden 和 random seed 或确定性原因。
- 不编造 Sharpe、收益率、回撤、OOS 或准入数值门槛；所有未批准数值进入 TBD。
- 回测通过不等于允许进入 Paper Trading。

### Strategy lifecycle

- 定义 `Research Hypothesis → Prototype → Backtest Candidate → Validated Candidate → Shadow → Paper → Limited Live → Production`，以及 `Suspended`、`Retired`。
- 每个状态定义活动、禁令、输入、证据、approval role、risk budget、code/data/strategy/parameter 版本、测试、Review Bundle 与退出条件。
- Shadow 不发送 order；Paper 真实资本为 0；Limited Live 未来单独批准；Production 不能由策略作者单独批准。
- 当前没有真实策略进入生命周期，Round 1B Smoke 不属于策略状态，真实风险预算未批准。

### Local Runbook

- 使用 `PROJECT_ROOT`、`LEAN_SOURCE_ROOT`、`LEAN_DOTNET_ROOT`、`LEAN_DATA_ROOT`、`LEAN_RESULTS_ROOT`、`NUGET_PACKAGES`、`PYTHON312`，不写具体本机路径。
- 覆盖 Git/toolchain、Python 3.12、.NET SDK 10.0.302、锁定 LEAN source、fixture/map/factor、C# build、A/B identity、Launcher 副本、安全 config、Run C、timeout、stdout/stderr/exit code、normalizer、Golden、final holdings、manifest、Python checks、Review Bundle 和独立 `validate_bundle`。
- 仓库没有统一 Native orchestrator、Launcher config generator、raw order-events exporter 或 Golden compare CLI；Runbook 只给出现有脚本与真实分步边界，并将缺口登记 TBD。
- 本 Round 没有重新执行 Native Run C 或 deterministic A/B build，未复用 Round 1B Run C 证据作为本 Round 新证据。

## Review Bundle 最小通用化

当前 generator 只允许 Docker evidence 或会强制要求 Native Run C 的 native evidence；本机无法诚实产出二者之一，而本 Round 明确禁止复用 Round 1B Run C 证据。按当前 Prompt 的条件例外，进行了 test-first 的最小通用化：

- 增加通用 `document` verification environment 及五项文档 evidence checks；
- 对 `created_documents`、document SHA、相对链接、JSON 示例、Secret 与具体绝对路径重新验证；
- 将安全 `round_id`、规范 repository-relative POSIX 文档路径及完整 Base..Head 变更范围绑定到 evidence；
- 非 Round 1B Bundle 不再携带空的 `RUN_C_*` artifact；
- Round 1B identity、Run C artifacts、hash binding、Golden 和 limitation 验证语义保持不变；
- 新增 document evidence、tamper fail-closed、范围/路径/identity 拒绝和无 Run C artifact 的 Bundle tests。

该变化只涉及 `scripts/build_review_bundle.py` 与 `tests/unit/test_review_bundle.py`，不涉及业务、数据、策略、Risk、Execution、fixture、Golden、manifest 业务值或配置。

## 实际验证结果

| 检查 | 结果 |
|---|---|
| 六文档 UTF-8/元数据 | PASS；6/6 具有目的、范围、权威来源、当前状态、更新触发、owner role、失败行为和验证要求 |
| 相对链接 | PASS；六文档共 102 个 repository-relative links，0 broken/case mismatch |
| JSON 示例 | PASS；10/10 可由 Python 3.12 标准 `json` 解析器解析 |
| 跨文档一致性 | PASS；Risk 枚举、lifecycle 状态、时间、版本、long-only/无杠杆、Shadow/Paper/Live 和术语 10 项检查通过 |
| TBD | PASS；48 项，均登记未实现/未确认内容、原因、owner role 与触发条件 |
| Secret scan | PASS；六文档高置信 pattern 0 findings |
| 具体绝对路径 scan | PASS；六文档 0 findings |
| `python -m ruff check .` | PASS |
| `python -m ruff format --check .` | PASS；56 files already formatted |
| `python -m mypy src` | PASS；12 source files |
| `python -m mypy scripts tests/unit/test_review_bundle.py` | PASS；4 source files |
| `python -m pytest` | PASS；75 passed，coverage 100.00% |
| `python -m build` | PASS；sdist 与 wheel 构建成功 |
| `python -m quant_system.cli status` | PASS；offline，Paper/Live 均为 false |
| `git diff --check` | PASS |
| Native Run C / deterministic A/B | 按 Round 边界未重跑，不作为本 Round PASS 证据 |

最终 committed HEAD 的 clean-source、Git、document SHA、Bundle checksums/manifest/source-to-Git、独立 ZIP 解压、`validate_bundle` 和 ZIP SHA-256 结果必须写入正式 Bundle 的 evidence 与外部交付记录；这些动作只能在本报告提交后完成。

## 不完整工作与 limitations

1. 六份基线文档仍为 Draft，等待外部独立审阅；本报告不冻结 Round 1C。
2. 文档定义合同而非 runtime；数据、策略、Portfolio、Risk、Execution 和生命周期能力大多尚未实现。
3. 48 个 TBD 涉及数据/质量、接口/聚合/Risk、回测门槛、生命周期 gate 以及 Native/Bundle 运营自动化。
4. README、CLI `CURRENT_PHASE` 与部分旧测试说明仍保留 Phase 0 文字；本 Round 明确禁止修改这些文件，当前治理事实由 Prompt、ADR 0002、冻结 Round 1B report 与本报告界定。
5. Round 1B 的 upstream dependency 与可选结果告警仍然存在；本 Round 未改变 LEAN source 或 toolchain。
6. Local Runbook 的完整 Native closure 仍需未来使用真实外部 LEAN 环境验证；本 Round 只审阅命令与边界。

## 结论与下一动作

本 Round 的文档与接口合同基线满足预定范围，状态为 **PASS WITH LIMITATIONS**。下一动作仅为：提交并普通 push 当前 Round；从 committed HEAD 生成并独立验证 `Review Bundle: REQUIRED`；等待外部独立审阅。Round 1C 尚未冻结，不启动后续 Round。

**Review Bundle: REQUIRED**，原因是本 Round 将作为正式验收与外部审阅对象，且包含为文档 Round 所需的最小 Bundle machinery 变更。
