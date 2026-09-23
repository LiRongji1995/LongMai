# StockRecommendation Phase 1：Daily Observation Monitor 实现报告

日期：2026-09-23（Asia/Shanghai）。本轮为真实运行观察工具增量，非策略开发或 Phase 2。

## 结论

**【事实】实现与入口验收：PASS。** 已提供只读 API、现有 CLI 子命令和简单 Markdown 输出；合并回归 **185 passed，退出码 0**。原实际运行实例已加载新出口，真实 API 与 CLI 核验成功。

**【需要验证】连续真实观察：PASS WITH OBSERVATION。** 工具现在可用于每天检查交付，但最近 20 个交易日概览不代表新版已经连续运行 20 日；真实首次 FOCUS/WATCH、自然 T+20 成熟与长期响应时间仍需后续记录。

## 1. 实现内容

【事实】先更新了 [implementation_plan.md](implementation_plan.md)，保留旧 Phase 1 计划，再进行本轮代码修改。

| 内容 | 实现与复用 |
| --- | --- |
| 统一入口 | 新增 GET `/api/daily-research/observation`；现有 `scripts/codex_research.py` 新增 `daily-observation` |
| 日期与状态 | 复用 `daily_research_delivery` 原判断；保留 target_session、observation_date、run_id、版本、cutoff、execution_status、blocker/error 和无 run 原因 |
| 冻结推荐 | 直接读取同库 `daily_research_snapshots`；显示四类数量、FOCUS/WATCH 原序列表、setup、reason_summary，以及 UNKNOWN/REJECTED 原解释汇总 |
| 正常空结果 | READY 且冻结 FOCUS/WATCH 为 0，明确显示正常交付、无推荐；缺快照时计数是 null/NOT_AVAILABLE |
| 后验状态 | 复用单 run `daily_recommendation_outcomes().summary`，汇总 T+1..T+20；重点展示 T+1/T+5/T+20 的成熟、记录及异常计数，不输出收益评价 |
| 20 日概览 | 使用现有交易所日历，最多 20 个目标交易日，按日期倒序；无记录 NOT_AVAILABLE，已确认漏跑保留 MISSED |
| 输出 | 默认终端 JSON；显式 `--output` 保存 JSON、Markdown、SHA-256 manifest 到新私有目录，拒绝覆盖 |

【事实】新增消费者为 `stock-fish/stockfish_core/yaogu/daily_observation.py`；API 接线仅增加新 GET。没有新增数据库、迁移、数据采集任务、调度器或另一套推荐逻辑，也没有改变原 Daily Report。

【事实】`execution_status` 保留原服务值；`status` 仅在原原因是无登记计划或已验证日历不可用时显示 NOT_AVAILABLE，原状态与原因同时保留。原 QUEUED 也如实展示。已发布 READY 后重试失败，原交付保持 READY，同时显示 latest_attempt_status。

【事实】不存在真正持久化的 Phase 1 snapshot 时，Monitor 不把旧 run 的兼容投影当成 SAVED，不补写旧快照或原解释。所有日期使用同一个 as_of；未来 cutoff、无时区 cutoff 和超过 20 日的窗口被拒绝。

## 2. 后验计数边界

【事实】每个 T+n 的分母是原 FOCUS/WATCH 推荐数；全 20 档 totals 的单位是“推荐 × 交易日”。

- `matured`：交易日收盘已到，不表示已采集或有效收益。
- `recorded`：已有通过原版本绑定/revision 筛选的标签，允许未成熟和异常标签。
- `pending`：未到期 + 已到期等待捕获（包括尚未更新的旧 NOT_MATURED 标签）。
- `missing`：仅显式 MISSING；SUSPENDED、UNAVAILABLE 和日历未知分别展示。
- `registered_recommendations`：核对原 pending 登记的 run_id、snapshot_hash、时间和证券范围后确认的数量；登记不是捕获成功。

【事实】日历未知可以与 UNAVAILABLE 重叠，所有列不能相加当成互斥分区。NORMAL 标签也不自动意味着路径完整。没有推荐显示 NO_TRACKED_RECOMMENDATIONS，不制造虚假 pending。

## 3. 工程测试

【事实】最终定向回归命令（工作目录 `stock-fish`）：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/r05_product/test_daily_observation.py tests/r05_product/test_daily_observation_cli.py tests/r05_product/test_codex_research.py tests/r05_product/test_daily_research_snapshot.py tests/r05_product/test_daily_research_service_outcomes.py tests/r05_product/test_daily_research_delivery.py tests/r05_product/test_daily_research_delivery_boundaries.py tests/r05_product/test_daily_research_outcomes.py
```

【事实】结果为 **185 passed in 14.05s，退出码 0**。其中新增 Monitor 定向 30 项、CLI 定向 28 项，已包含在 185 项中，不重复相加。所有工程场景使用明确 TEST_FIXTURE 与隔离 DB/state，不访问真实市场或运行真实策略。

| 验收场景 | 验证结果 |
| --- | --- |
| READY + 有推荐 | PASS：run_id、SAVED snapshot、FOCUS/WATCH、原 setup/解释和身份一致 |
| READY + 合法空结果 | PASS：真正零条目以及只有 UNKNOWN/REJECTED 两种情况均正常显示，无虚假跟踪 |
| NOT_READY | PASS：原 blocker、missing evidence、无 run 原因保留；数量不填零 |
| FAILED | PASS：原 error/stage/失败原因保留，不冒充空推荐 |
| 历史日期 | PASS：20 个交易日、节假日、无登记、MISSED、as_of 发布边界分别覆盖 |
| 后验异常 | PASS：正常/缺失/停牌/不可用、到期无 row、旧未成熟标签、登记不匹配均覆盖 |
| 只读与输出 | PASS：API 查询前后完整测试 DB fingerprint 一致；CLI 单 GET、无覆盖、hash 和凭据保护通过 |

【事实】相关回归初轮为 154 passed、1 failed（10.88 秒，退出码 1），复现此前已发现的旧测试同步竞态：作业终态先于工作线程最后的 trace 持久化。只修改该测试，使其在完整 DB 指纹断言前等待同一个 Future 退出（5 秒上限）；原断言保留，未修改生产作业、readiness 或 trace 顺序。修正单项复跑 1 passed，最终整组合并回归如上通过。

【事实】新增测试开发中曾各出现一次参数/排序契约假设不匹配：接口未承诺 format 参数，摘要顺序未承诺排序。测试按本计划的实际接口契约修正；没有更改业务规则或删除异常样本来获得通过。

## 4. 实际运行核验

【事实】本轮沿用前一任务已授权的原实例启用流程。切换前核对管理 PID、原 cwd/venv、DB/state/port、health 和作业空闲；复核已有 SQLite 在线备份文件与 manifest 可用，该备份已通过 quick_check 和 SHA-256。本轮无 schema migration，未新建业务库，也未再次复制约 42 GB 数据库。

【事实】使用既有 Stop/Start 脚本加载新查询路由，命令如下（仓库根目录）：

```powershell
pwsh -NoProfile -File ./Stop-StockFish.ps1 -Root D:/stockfish-qlib-quant-system
pwsh -NoProfile -File ./Start-StockFish.ps1 -Root D:/stockfish-qlib-quant-system -Port 8001 -DatabasePath D:/stockfish-qlib-quant-system/stock-fish/data/stockfish.sqlite3 -StateRoot D:/stockfish-qlib-quant-system/stock-fish/data/core -TimeoutSeconds 300
```

【事实】Stop/Start 均退出 0，启动约 7.01 秒。新实例于 10:22:20 创建，原 Python 环境、DB/state 和 8001 端口保留。新 endpoint 从 404 变为 200；10:23:49 前完成以下核验：

| 实际查询 | 结果 | 单次耗时 |
| --- | --- | --- |
| `/health/ready` | 200、ready=true | 0.032 秒 |
| `/daily-research` | 200，原 HTML 保持 | 0.078 秒 |
| `/api/daily-research/observation?days=20` | 200；默认目标 2026-09-23，NOT_READY，20 个交易日 | 8.203 秒 |
| `?target_session=2026-09-24&days=20` | 200；PLANNED / AWAITING_DAILY_PRIMARY | 8.125 秒 |
| `?target_session=2026-09-22&days=1` | 200；历史 NOT_READY | 0.437 秒 |
| `?days=21` | 400，参数校验 | 0.016 秒 |
| 实际 CLI 查询 2026-09-24、20 日并保存报告 | 退出 0，PLANNED，JSON/Markdown/manifest 校验通过 | 约 8.41 秒，含本机输出核验 |

【事实】2026-09-23 的原 blocker 为 `CURRENT_COMPARABLE_PRICE_COVERAGE` 和 `CORE_TREND_INPUT_COVERAGE`；Monitor 如实显示已有 attempt、尚无可发布 run。没有触发重算、续采或用旧推荐填充当天。

【事实】实际最近 20 个目标日中，显示 15 个 NOT_AVAILABLE、2 个 MISSED、2 个 NOT_READY 和 1 个历史 READY。历史 READY 为旧交付记录，原持久化 Phase 1 snapshot 不存在，因此 Snapshot/Outcome 显示 NOT_AVAILABLE；没有补造历史快照或宣称该旧记录已完成新版跟踪。

【事实】全部上述 GET/CLI 查询前后，六个相关 artifact namespace 的文档 ID/hash 以及全部 job ID/state/payload 指纹一致（15 份文档、62 个 job）。这项真实验证覆盖 run、attempt、snapshot、outcome、pending、capture 及 job；不声称整个运行服务的 scheduler 计划登记永远零写入。

【事实】策略源码/配置、原 daily research service、outcome 计算、candidate service、scheduler 和主入口等 9 份受保护文件哈希未变。当前身份仍为：

```text
strategy_id: STAGE_AWARE_MOMENTUM_V2
strategy_version: R12_STAGE_AWARE_MOMENTUM_V2_INTEGRITY_HISTORY
config_version: 508e3fc6110ff887aadf9e6d7bcf5adb7a05bd0733cd09c36b79f65414bb7965
validation_epoch_id: validation_epoch_24a489fda0b16cc2f0ea1f3896937e2b8819018ef5f62d8f10aef88b1e320a24
```

【事实】私有验证材料在 `work/phase1-observation-monitor-20260923T021918Z/`，含 HTTP 结果、CLI 输出及 manifest、查询前后指纹、进程登记和实际部署源码 SHA-256；不提交数据库、完整运行响应或私有报告目录。

## 5. 未覆盖范围与使用

【需要验证】没有执行在线 provider 采集、策略回测、收益验证、完整全仓/Go 测试、浏览器 UI 测试或压力测试。五个业务场景的全面覆盖来自隔离工程 fixture；真实运行本次实际覆盖 NOT_READY、PLANNED、无记录、历史状态及只读查询，未为凑真实场景生成推荐。

【需要验证】连续 20 日积累后的延迟和大批 outcome revisions 查询性能仍待观察。本次 20 日概览约 8 秒是一次真实测量，不是 SLA 保证。不增加开机自启、后台提醒、自动调参或新调度任务。

【建议】日常在 Codex 中直接说“检查 StockRecommendation 今天有没有正常工作，显示最近 20 个交易日”。也可在业务目录执行：

```powershell
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py --base-url http://127.0.0.1:8001 daily-observation
```

【事实】完整字段和参数说明见 [PHASE1_OBSERVATION_MONITOR.md](stock-fish/docs/PHASE1_OBSERVATION_MONITOR.md)。没有股票、合法空结果、未就绪和漏跑各自保留原含义；工程工具通过不等于今日交付 READY。
