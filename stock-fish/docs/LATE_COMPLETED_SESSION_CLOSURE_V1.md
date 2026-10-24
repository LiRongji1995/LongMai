# 跨午夜已完成交易日后验 V1

本合同用于资料在次日才完成封存的显式后验处理：业务日仍是原交易日，知识截止使用实际已发生的时间，不倒填到业务日。它新增 `SHORT_HORIZON_LATE_COMPLETED_SESSION_V1`，复用既有归档桥接、`SHORT_HORIZON_OUTCOME_V1` 和单库原子提交；不重选股票、不改变九个冻结归一器、资格判断、价格公式或原 run/input。

当前阶段是候选实现的隔离工程验证：35 项跨日与资源接线检查分两轮通过（26 项、追加 9 项），最终 38 项资源/CLI 检查退出 0。早期失败和超时记录保留。以上均为 `TEST_FIXTURE`，没有据此认证真实档案、每日结果、双 run 容量或生产运行；真实验收结果待另行登记。此前其他版本的回归和真实结果不能充作本合同的本轮检查。

相关合同见[原每日闭环](SHORT_HORIZON_DAILY_CLOSURE_V1.md)、[原件桥接](SHORT_HORIZON_ARCHIVE_BRIDGE_V1.md)和[独立后验口径](SHORT_HORIZON_OUTCOME_V1.md)。

## 三种入口不能互相冒用

| 模式 | 显式命令 | 日期与截止 | 对外含义 |
| --- | --- | --- | --- |
| daily | `r2-daily-plan/run/status/retry` | 资料观察日和最新登记会话等于实际上海交易日，收盘后准入；第一次接受的截止冻结 | 实际当日闭环；旧输入不能冒充当日输入 |
| closed-session replay | `r2-closed-session-plan/run/status/retry` | 指定业务交易日；知识截止在同一上海日期且不早于该日 15:00，不晚于真实现在 | 明确历史知识截止的重放，不追认当日曾执行 |
| late-completed | `r2-late-completed-plan/run/status/retry` | 指定业务交易日；知识截止的上海日期晚于业务日，并满足下述最新已完成会话门禁 | 使用实际较晚资料的跨日后验，不声称业务日当时已掌握 |

late 不覆盖 daily 或 replay 的日期检查，也不提供自动发现最新输入的入口。调用者必须指定原 run 集合、可信 `input_id`、`--business-session` 和含时区的 `--knowledge-cutoff`。内部模式为 `EXPLICIT_LATE_COMPLETED_SESSION`，独立 job kind、job 前缀与 ledger 命名空间分别为 `short_horizon_late_completed_session_v1`、`short_late_`、`short_horizon_late_completed_session_ledgers_v1`。

## 真实时间与来源门禁

1. 知识截止不得晚于真实执行时钟；无时区、同业务日截止、未来截止均不能作为 late 输入。真实服务不能注入伪时钟，时钟替换只供明确的隔离 `TEST_FIXTURE`。
2. 使用有范围和验证日期的官方 2026 年沪深会话日历。业务日必须同时是“截止时已完成的最新交易日”和“新请求准入时已完成的最新交易日”。已完成以该会话上海时间 15:00 为界；不以普通工作日、周末调休推定，也不扩展到未覆盖年份。
3. 认证前、认证后及实际提交前检查新准入门禁。长认证跨过下一交易日收盘后，不能把旧业务日重新作为最新输入提交。
4. 输入的观察日及最大登记会话都必须等于业务日。原信号生成时间和原信号截止不得晚于本次知识截止；原输入与新输入共同保留的会话轴仍按原规则核对。
5. 每个原 run 及本次 input 均须通过本次完整 bridge 认证，其真实归档封存钟不得晚于知识截止。封存钟来自本次私有认证证明，普通 JSON、自填时间和旧索引不能替代。RAW、receipt、result、producer、原始字节和物理身份等原有检查保持。
6. 价格及资格仍以实际四时钟、父来源完整性、价格基准和逐日证据决定；把知识截止延后不会把资料改写为过去已经可见。源原件和借用 run/input 的完整终验不减少。

例如业务日为 10 月 20 日、实际截止为 10 月 21 日上午，新的 late 请求可在 10 月 21 日 15:00 之前接受，前提是全部资料和封存已经真实发生。若实际交付晚于 09:30，仍须如实标明迟到；本后验入口本身不证明盘前发表或交易时可用。

已经接受的任务可以在更晚时间完成或显式重试，保留相同截止和来源，不能加入后来取得的新证据。下一收盘之后重新调用 `submit_late`，包括重复 submit，仍受新准入门禁限制；已受理结果可用 `status` 读取，失败任务可按原请求 `retry`。因此同请求 submit 的幂等验收应在仍满足新准入条件时完成，不能把跨窗口的拒绝解释为原结果丢失。

## 显式资源配置与原默认值

`--verification-profile FULL_ARCHIVE_900S_V1` 是运行资源选择，不是策略或可见性版本。它可以显式用于归档验证/导入、daily、replay 与 late 的相应执行命令；不填写时保持原认证会话默认值。

| 每个认证会话共享上限 | 默认 | `FULL_ARCHIVE_900S_V1` |
| --- | ---: | ---: |
| 墙钟时间 | 300 秒 | 900 秒 |
| 进程历史峰值 RSS | 2 GiB | 2 GiB |
| 原件累计实际读取量 | 8 GiB | 8 GiB |
| 登记原件唯一字节 | 4 GiB | 4 GiB |
| 登记文件数 | 4096 | 4096 |

同一 worker 的所有原 run、输入、认证、评价和发布终验共享该会话，不按档案重置；plan 与 worker 分别创建会话。文件读取预算不包含 SQLite 数据库 I/O，但其耗时仍计入会话墙钟。守卫按既有检查点协作执行，不是能在任意指令或系统调用中即时中断的硬实时配额。

资源名称、实现 SHA 与精确 limits 进入请求 policy、任务身份和认证审计；终验还核对实际 limits。重试和孤儿恢复先验证原资源身份：不能给旧 300 秒失败 job 加一个参数就改成 900 秒，也不能改写它的原 payload、事件或失败结论。采用新配置必须创建新身份请求；更改代码还会改变完整桥接源码身份，须按新实现完整鉴证并取得相应凭证，旧库证书不自动承认新实现。

`--wait-seconds` 独立控制 CLI 对已经提交任务的等待，默认仍为 120 秒，范围 1..3600；它不放宽认证预算。900 秒模式可显式使用 `--wait-seconds 1200`。等待超时只向本次拥有的任务请求取消，不能据此断言整个进程已经退出或数据库从未写入；须查实际终态。完整命令还包含提交前的 plan，不能把等待值当作整个命令的时长上限。

900 秒下成功只证明所测批次在该显式配置下完成，不能称原 300 秒性能目标已解决，也不证明默认 5 个 run、连续调度或服务容量。

## 运行前提与命令

从明确选择的业务源码根和已有 Python 3.12 环境执行。需要已存在、符合当前 schema/index/WAL 要求的服务数据库与对应 state；CLI 不创建或迁移数据库，不启动应用服务、不采集数据、不自动选择 run。写命令先持有该数据库的 JobService 单 writer 锁；已有活跃服务持锁时拒绝，不停止它、不接管其他队列。新隔离验收库的建立和旧资料接入应在另行明确的操作范围内完成。

两个原信号 run 和本次资料输入必须已经在同一库完成可信归档接入。若三者来自三个不同档案，应分别保留并鉴证三个原件；只给输入所属的新 run 不能代替两个旧信号 run。导入过程和必要的原源码内容证明按[桥接合同](SHORT_HORIZON_ARCHIVE_BRIDGE_V1.md)执行，不能从普通 JSON 恢复历史 owner。

下列路径、ID 和时间均为占位示例，不是可直接照抄的真实验收记录。请替换成明确批准的现有位置和实际值；不应把示例截止当作现在。

```sh
export PYTHONDONTWRITEBYTECODE=1
RUNTIME_PYTHON='<已有隔离 Python 的绝对路径>'
CODE_ROOT='<本次已验证的 stock-fish 业务根>'
SERVICE_DB='<已初始化且明确选定的服务数据库>'
SERVICE_STATE='<对应的已有 state 目录>'
RUN_ID_15='<10月15日原冻结 run_id>'
RUN_ID_16='<10月16日原冻结 run_id>'
INPUT_ID_20='<10月20日业务资料的可信 input_id>'
KNOWLEDGE_CUTOFF='<实际已发生且不早于全部归档封存钟的含时区 ISO8601 时间>'
```

先查看计划。退出 0 只说明计划查询成功；必须检查 JSON 的 `can_submit=true`、`status=READY`、完整 run/input 身份、两种日期和各 H 的状态。

```sh
"$RUNTIME_PYTHON" -I -B "$CODE_ROOT/scripts/stockfish.py" r2-late-completed-plan \
  --db "$SERVICE_DB" --state "$SERVICE_STATE" \
  --run-id "$RUN_ID_15" --run-id "$RUN_ID_16" --input-id "$INPUT_ID_20" \
  --business-session 2026-10-20 --knowledge-cutoff "$KNOWLEDGE_CUTOFF" \
  --max-runs 2 --verification-profile FULL_ARCHIVE_900S_V1
```

确认原计划范围后显式提交；run 命令仍会执行自己的完整 plan 和 worker 认证，不把先前 plan 回执当作授权凭证。

```sh
"$RUNTIME_PYTHON" -I -B "$CODE_ROOT/scripts/stockfish.py" r2-late-completed-run \
  --db "$SERVICE_DB" --state "$SERVICE_STATE" \
  --run-id "$RUN_ID_15" --run-id "$RUN_ID_16" --input-id "$INPUT_ID_20" \
  --business-session 2026-10-20 --knowledge-cutoff "$KNOWLEDGE_CUTOFF" \
  --max-runs 2 --verification-profile FULL_ARCHIVE_900S_V1 --wait-seconds 1200
```

保留返回的 job ID。状态命令不接受资源升级参数，也不创建 worker。

```sh
"$RUNTIME_PYTHON" -I -B "$CODE_ROOT/scripts/stockfish.py" r2-late-completed-status '<实际 job_id>' \
  --db "$SERVICE_DB" --state "$SERVICE_STATE"
```

只有需要并明确决定重试失败、中断或取消任务时才执行下例；它保持原截止、原输入和原资源身份。默认 300 秒请求重试时应省略 `--verification-profile`；不能使用此例给它升档。最多 3 次尝试，不自动循环。

```sh
"$RUNTIME_PYTHON" -I -B "$CODE_ROOT/scripts/stockfish.py" r2-late-completed-retry '<实际失败 job_id>' \
  --db "$SERVICE_DB" --state "$SERVICE_STATE" --max-runs 2 \
  --verification-profile FULL_ARCHIVE_900S_V1 --wait-seconds 1200
```

真实命令不加 `--allow-test-fixture`。CLI 的运行等待退出 0 表示 job `SUCCEEDED`，3 表示作业失败或命令错误，4 表示等待超时，130 表示中断；参数解析错误由 argparse 报告。成功 job 仍可能对应 `COMPLETED_PENDING_MATURITY` 或 `COMPLETED_WITH_UNKNOWNS`，不能只看退出码省略 ledger 和逐项结果。

## 双 run 成熟度示例

以下仅按已登记官方日历推导，假设新输入完整覆盖到 2026-10-20、截止在 2026-10-21 15:00 之前；不是本次真实采集或后验验收结论。

| 原观察日 | H1 | H2 | H3 | H5 | H10 |
| --- | --- | --- | --- | --- | --- |
| 2026-10-15 | 10-16，DUE | 10-19，DUE | 10-20，DUE | 10-22，PENDING | 10-29，PENDING |
| 2026-10-16 | 10-19，DUE | 10-20，DUE | 10-21，PENDING | 10-23，PENDING | 10-30，PENDING |

这两个 run 都有到期 H，不能沿用此前“一条到期、一条全 PENDING”的容量验收。`DUE` 只表示日历成熟，实际每项仍须通过原全池、精确可比价、日期化证券资格、终盘及父来源四时钟检查。缺行情、缺来源、口径冲突或未知资格均保留 `UNKNOWN` 和空数值，不补成零收益；H 不因漏行情而压缩。未来期限保留在 ledger 中，不强行生成成熟收益。

## 原子性、回查与验收边界

每批仍先完整计算，在同一次 `JobContext.complete()` 事务中写入全部应产生的 outcomes、tracking、review 与本模式 ledger。提交前及事务末的原件和借用值终验都保留；任何 run 失败、来源变化、内容冲突或取消导致本批产品与成功 ledger 一起回滚，原失败 job 和事件继续留存。已提交成功后发生的外部报告失败必须如实区分，不能称为没有持久结果。

完整真实验收至少需要：

- 固定本次源码、配置、运行环境、资源 policy、档案封存身份和现有库/state，分别记录计划、执行、幂等、回查、独立审计的实际命令及退出码。
- 检查原 run/input、全部原有文档和 job/event 保护；不重新选股、不覆盖旧名单或旧失败。
- 对两个指定 run 的完整原池和到期期限核对 MATURE/UNKNOWN 分母、未知原因、未成熟期限和三产品 revision；完整产品与数据库实际字节 hash 对应，并核正式导出与个股查询一致。
- 在准入窗口内用完全相同截止与 policy 再次 submit，确认返回同一成功尝试，不新增 worker、产品或完成事件。若窗口已过，记录这一限制，不修改时钟绕过。
- 独立核对 H1 的精确分数价格公式、基准、来源实际时钟和资格证据；单纯 job 成功、汇总数量或合法空列表不能替代逐项证据。
- 分别报告工程 fixture、真实数据验证和投资验证。保留 `MARKET_EDGE=NOT_ESTABLISHED`、未知与初始参数标记；本合同不验证可成交收益、费用、容量、账户或交易执行。

真实后验与双 run 结果尚待补充。在结果、幂等、正式回查与独立 H1 未全部通过前，不将本合同描述为真实每日或双 run 闭环已经完成。
