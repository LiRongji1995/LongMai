> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../../../CURRENT_STATUS.md)；原路径可在[归档索引](../../README.md)查找。

# StockRecommendation Phase 1 工程验收记录

日期：2026-09-23。基线提交：`b4b133a4c8ce9b2b8e792f791d2919dfcb884770`。

【事实】先保存根目录 `implementation_plan.md`，再实现本轮代码。实现与约束见 [升级方案](../../../PHASE1_DAILY_RESEARCH_LOOP.md)。以下是隔离工程验证，不是线上连续运行或投资有效性证明。

## 1. 最终相关回归

【事实】在业务目录执行以下完整命令，实际结果：**297 passed in 162.48s，退出码 0，无失败、无 skip、无 deselection**。

```powershell
./.venv/Scripts/python.exe -m pytest -q tests/r05_product/test_daily_research_snapshot.py tests/r05_product/test_daily_research_outcomes.py tests/r05_product/test_daily_research_service_outcomes.py tests/r05_product/test_daily_research_delivery.py tests/r05_product/test_daily_research_delivery_boundaries.py tests/r05_product/test_daily_research_scheduler.py tests/r05_product/test_daily_primary_reliability_v1.py tests/r05_product/test_daily_primary_critical_capture_boundary_v1.py tests/r05_product/test_r14_daily_fast_path.py tests/r05_product/test_r12_score_integrity.py tests/r05_product/test_outcome_context.py tests/r05_product/test_codex_research.py tests/integration/test_core_api.py tests/r02_product/test_scheduled_outcomes.py tests/r02_product/test_r12_scheduler_epoch.py tests/r02_product/test_candidate_product_path.py
```

【事实】新测试使用独立临时 DB/state/artifact，或明确标注 TEST_FIXTURE 的内存数据；没有伪造生产历史。覆盖：

| 用户验收问题 | 工程证据 |
|---|---|
| 当天是否成功交付 | READY 需完成保存证明；NOT_READY/FAILED/MISSED/在途重试分开；无登记计划不虚构漏跑 |
| 为什么推荐 | 四类原决定、setup、reason、事实/关键证据/风险同 run 原子冻结；后验不覆盖解释 |
| 没推荐为何没推荐 | 无 FOCUS/WATCH 与无快照分开；缺失证据、阶段、错误、时间保留；失败日也可导出 |
| 每股未来 T+1..T+20 如何 | 20 个交易会话位置；原始 close、可比终点和路径；缺失/停牌/不可比/未成熟保留，缺口不填零 |
| 属于哪个版本 | 原 strategy/config/run/input/cutoff/epoch 可追溯；旧身份不由当前配置补写，统计不混版本和证据类型 |

【事实】还覆盖快照保存失败整事务回滚、读取不写库、并发重复任务去重、不同 job 身份拒绝复用、后验 policy 失配拒绝、来源未来可见性、无效新 revision 不隐藏、退池证券跟踪、20 会话完整分母、已登记计划跨日 MISSED、人工/自动重试同族、截止/预算/协作式超时，以及已有 API、CLI、评分和旧观察路径兼容。

【事实】精确时间边界包括：生成时间不是提交完成时间；截止或发布时间晚于 `as_of` 的已知结果不提前出现；查询时点之前的失败不被未来重试结果覆盖。历史可见性排除计数在日报和 JSON 中公开。

## 2. 额外静态检查

【事实】在业务目录将新页面内联脚本传给 Node 语法检查，退出码 0：

```powershell
$inline = [regex]::Match([IO.File]::ReadAllText((Resolve-Path 'templates/daily_research.html')), '(?s)<script>(.*?)</script>').Groups[1].Value
$inline | node --check
```

【事实】仓库根目录 `git diff --check` 退出码 0。新增页面由隔离 Flask 客户端验证成功返回，读取不写库；本次没有执行浏览器视觉验收。

【事实】使用 `git rev-parse HEAD:<path>` 与 `git hash-object -- <path>` 比对下列受保护文件，四项内容均与基线一致；同时 `git diff --exit-code -- <四个路径>` 退出码 0。

| 文件（相对业务目录） | 基线及当前 Git blob |
|---|---|
| `stockfish_core/yaogu/stage_aware_momentum.py` | `e159a679f069f5db1a2f09f8695f896150af8735` |
| `config/stage_aware_momentum_v2.yaml` | `5c05f748a2e5596fc560e7e60f631c1505e58e86` |
| `stockfish_core/yaogu/strategy_experiments.py` | `9d755b06755c8c2e59e6c37840ca5a45606eeaec` |
| `stockfish_core/yaogu/recommendation_scoring.py` | `fd6a23db874ac2b06553ba1043f84c55a276dcfa` |

【事实】调度/服务接线变化按已有源码身份机制形成新 validation epoch；这不意味着修改 V2 因子、阈值、决策语义或旧 score。

## 3. 尚未执行的验收

【需要验证】没有读写本机真实研究 DB/state、没有触发真实 provider 采集、没有部署/重启服务、没有新增开机常驻服务或切换 Go。没有执行全仓库所有测试、完整历史回测、连续盘前交付 SLA 或自然成熟 20 会话验收。

【需要验证】单 worker 不强制抢占已运行任务；30 分钟超时为协作式期限。线上长任务、供应商延迟、交易日历后续覆盖以及 outcome 积压的实际消化速度需要在部署后观察，工程 fixture 不替代这些事实。

【推断】本轮代码已具备可追溯交付和描述性复盘的最小闭环。是否长期稳定交付需要运行证据；任何上涨比例、MFE/MAE 或 setup 分组都不能据此升级为已证实的市场优势。

【建议】后续先按现有部署协议确认实例/代码/DB 身份，再开展连续观察；验收使用交付状态及理由、原快照可读性和到期后验覆盖，不设强制股票数量，不自动改策略。始终保持 `NOT_VERIFIED` 与 `MARKET_EDGE=NOT_ESTABLISHED`。
