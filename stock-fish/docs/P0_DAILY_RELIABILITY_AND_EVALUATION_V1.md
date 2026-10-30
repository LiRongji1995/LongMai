# P0：交付账本、全池覆盖与 R2 执行评价基础 V1

2026-10-30。本轮落实项目审阅报告的首批 P0 工程：让已保存日常请求的终态、完整原池的数据缺口和 R2 执行评价的证据边界能够直接检查。只新增 scripts 层模块和隔离测试；原核心、来源采集器、策略配置、正式导出、旧请求和历史 run 保持。默认新盘前入口仍为 `codex_daily_v2.py premarket-plan/premarket`，引擎仍为 original。

这次交付不表示当日完整复权已补齐、旧失败可重试、连续20交易日已验收或策略具有投资优势。没有自动切换生产实例、登记、预算和调度，没有启动采集、数据库迁移或券商操作。

## 统一只读入口

在业务目录、已核验的原 daily/review Python 下执行 `scripts/codex_p0.py`。所有输入和输出路径使用实际绝对路径；`--output` 是新私有 JSON 文件，父目录须已存在，已有文件拒绝覆盖。只读检查退出0表示完成检查，不能替代报告里的作业终态。

```sh
"$REGISTERED_PYTHON" -I -B scripts/codex_p0.py delivery \
  --job "$ORIGINAL_JOB_DIRECTORY" --output "$NEW_PRIVATE_REPORT"

"$REGISTERED_PYTHON" -I -B scripts/codex_p0.py ledger \
  --runtime "$EXISTING_RUNTIME" \
  --expected-session YYYY-MM-DD --expected-session YYYY-MM-DD \
  --output "$NEW_PRIVATE_REPORT"
```

已恢复登记必须显式加 `--recovered-runtime`；该选项只读取已有恢复登记，不建立新 epoch、不改变默认实例、不重试祖先请求。自定义登记根继续使用原 `--registry-root`，不能另建平行登记。

`delivery` 也支持 `--operation ORIGINAL_OPERATION --state-root ORIGINAL_STATE`，或 `--preflight SAVED_PLAN`。旧计划只代表其保存时点，不重新准入。可选 `--deadline` 必须带时区，仅比较调用方提供的目标，不能证明目标曾事前登记或用户确已收到。

### 交付账本的语义

`codex_delivery_audit_v1.py` 核对原 operation → final → completion → 核心/题材/展示产物的实际引用，重读完整 SHA 和七字段物理身份，再进行末验。默认单次只读审阅限额为256MiB累计返回读取、120秒；超限拒绝，不截断。它不重认全部供应商 RAW，不发行来源能力。

- 原 `FAILED_OR_UNKNOWN`、`FAILED`、`BUDGET_EXCEEDED` 保持；中间 core PASS 不能覆盖最终失败。
- 缺少最终绑定的 worker 文件单列，不猜测它属于哪个 request。旧失败回执没有 job 时，保留 `NO_VALIDATED_JOB_RECEIPT`。
- `archived_workflow_complete`、`data_completeness`、`archived_core_status` 和 `user_delivery_status` 分列。流程完成仍不能证明全池来源齐备；原用户接收未测量则保持 UNKNOWN。
- 原 UTC 起止差、单调耗时和调用方目标分别展示，不把休眠期间的实际等待从用户耗时中消除。
- 同日多请求需要核对，不能挑成功者统计。显式日期仅是调用方声明，不是认证交易日历。没有支持版本的记录记为 `NO_SUPPORTED_DELIVERY_OPERATION_IN_SELECTED_SCOPE`，不能据此声称系统未运行；未支持版本数量另列。
- 多日汇总只覆盖所选登记 scope，不自动合并恢复祖先或 standalone 作业。完整数据交付率、用户送达率在证据不足时为 null，连续交付验收保持 false。

## 全池与后验覆盖审计

```sh
"$REGISTERED_PYTHON" -I -B scripts/codex_p0.py coverage \
  --run "$ORIGINAL_CANONICAL_RUN_JSON_OR_GZIP" \
  --run-file-sha256 "$ORIGINAL_CARRIER_SHA256" \
  --output "$NEW_PRIVATE_REPORT"
```

若有同 run 的完整后验，追加 `--outcomes ORIGINAL_OUTCOMES_JSON_OR_GZIP --outcomes-file-sha256 ORIGINAL_OUTCOMES_CARRIER_SHA256`。必须使用完整 canonical run，不能传 observation 筛选导出。载体原SHA、严格JSON（拒绝重复键/非有限数）、有界gzip回解及起末物理身份分别核验。默认单载体64MiB、展开JSON256MiB，新报告16MiB；不自动扩额。保存报告在发布前后重新核对消费的输入载体，并终验新输出SHA、七字段身份、0600权限及原父目录路径。持有创建描述符防止清理期间inode复用；发布失败按本次inode回滚，其他写入方替换的报告或临时文件保留。

`codex_research_coverage_v1.py` 的纯函数 `build_coverage_report(run, outcomes=None, expected_horizons=(1,2,3,5,10))` 校验 run 全文hash、资格分区、原全池、排序及选择声明；有后验时校验 revision、run/input/hash和完整证券×期限集合。拒绝错配、截断、重复、假FOCUS和UNKNOWN填零。

报告保留原声明总体、资格排除、资格未知、缺身份、原eligible池、研究观察与未选分母；按记录中的交易所、阶段和原因分层。未知身份不按代码猜交易所。基准覆盖门槛达标与100%覆盖单列，缺规模或流动性分层字段则明确不可用。

未提供后验记为 `NOT_PROVIDED`，不声称从未运行后验。提供后验后的 MATURE、PENDING、UNKNOWN 仍按原合同；已观察子集均值不能代表总体效果，缺失不计零。API调用者若明确审阅较短期限，必须显式登记 expected_horizons；CLI保持全五期限，不能通过缺行缩小范围。空池不自行认证合法EMPTY。该审计不恢复来源真实性、历史PIT或成交能力，不修改策略。

## R2 执行评价适配

```sh
"$REGISTERED_PYTHON" -I -B scripts/codex_p0.py execution-readiness \
  --run "$ORIGINAL_CANONICAL_RUN_JSON_OR_GZIP" \
  --run-file-sha256 "$ORIGINAL_CARRIER_SHA256" \
  --output "$NEW_PRIVATE_REPORT"
```

保存JSON入口只检查原完整R2和执行证据缺口，返回 `BLOCKED_DATA`、退出4，不生成账户收益。

显式Python API为 `codex_r2_execution_v1.evaluate_r2`。真实信号要求当前原生 `NativeCore` 或本次 `ArchiveBridgeCapability`，前后核对原run并完整终验；不能从保存JSON、伪owner或调用方自行构造的ResolvedTape恢复来源能力。执行归档由本次调用原 `adapt_execution` 读取，再调用原 `simulate`，所有实现依赖和消费文件进行有界SHA/七字段末验。旧账户、费用、整手、T+1、限价不成交、资本占用和未知退出断言保持。

原 `generated_at` 只作为明确的假设发布代理，不证明用户接收；晚生成信号保持LATE，不补造历史发布时间。缺目标交易日或生成时间的旧run不能被猜测补齐。

**执行归档的旧适配器只证明本地归档、receipt及成员一致性，不证明供应商真实性。** 新结果明确 `execution_evidence_role=LOCAL_ARCHIVE_CONSISTENCY_ONLY`、执行来源 `source_reauthenticated_now=false`、`evaluation_admissible_for_market_validation=false`；正式 `strategy_account_return` 保持null，条件模拟结果单独保存在原engine输出中。容量和完整题材风险尚未验证，不能把夹具或条件模拟当成真实R2组合验收，生产晋级关闭。

## 本轮验证范围

工程检查使用原登记CPython 3.12.14、`-I -B`、新字节码查找路径和独立TEST_FIXTURE。新增测试覆盖完整/失败/不明请求、缺记录与多请求、全池/后验错配、零填充、发布迟变/落盘故障、源文件替换、预算、原R2信号到原执行器的费用和可成交性边界；原请求防重及原账户研究安全检查另在组合中执行。发布目录的最终P0四文件定向检查为116 passed，pytest 8.93秒、外层11.447秒、退出0；其中统一入口28、交付35、覆盖28、执行25。无skip。最后的输出身份修复后，仅对受影响的P0组合补验，没有将此前旧回归冒充再次运行。

较早的发布目录九文件组合为321 passed、1 failed，pytest 284.69秒、外层287.389秒、退出1；其中当时P0为111项，旧入口/账户执行/后验回归为210通过、1项既有黄金身份失败，详见下节。不能将两次命令累加成全仓通过数。

实际执行形式如下；`REGISTERED_PYTHON` 为原核验解释器，`NEW_TEST_BASE`、`NEW_PYCACHE_PATH`、`NEW_JUNIT_XML` 每条命令分别使用此前不存在的私有路径，测试目录显式标记TEST_FIXTURE。完整实际参数、stdout及JUnit存于本轮私有审计目录，不发布本机路径或测试数据。

```sh
# 仓库根执行；九文件组合，退出1（保留旧黄金失败）
"$REGISTERED_PYTHON" -I -B -X "pycache_prefix=$NEW_PYCACHE_PATH" \
  stock-fish/scripts/run_isolated_tests.py -q \
  tests/unit/test_codex_p0_entry.py \
  tests/unit/test_codex_delivery_audit_v1.py \
  tests/unit/test_codex_research_coverage_v1.py \
  tests/unit/test_codex_r2_execution_v1.py \
  tests/unit/test_codex_daily_entry_v2.py \
  tests/r05_product/test_research_r1.py \
  tests/r05_product/test_research_r1_repairs.py \
  tests/r05_product/test_research_r1_g_repairs.py \
  tests/yaogu/test_short_horizon_outcomes_v1.py \
  --basetemp "$NEW_TEST_BASE" --junitxml "$NEW_JUNIT_XML" --tb=short

# 最终输出身份修复后的P0组合，退出0
"$REGISTERED_PYTHON" -I -B -X "pycache_prefix=$NEW_PYCACHE_PATH" \
  stock-fish/scripts/run_isolated_tests.py -q \
  tests/unit/test_codex_p0_entry.py \
  tests/unit/test_codex_delivery_audit_v1.py \
  tests/unit/test_codex_research_coverage_v1.py \
  tests/unit/test_codex_r2_execution_v1.py \
  --basetemp "$NEW_TEST_BASE" --junitxml "$NEW_JUNIT_XML" --tb=short
```

八个新增Python文件AST、九个新增文档链接和Git whitespace检查通过。563份既有core/config/scripts的SHA及七字段物理身份保持，原真实run载体亦保持。首次开发组合误用包含stock-fish前缀的测试路径时退出4、未收集测试；之后已按运行器工作目录使用上述tests路径，没有删除或跳过失败样本。未执行全仓测试、全历史真实回测、新来源采集或生产服务切换。

已有真实归档的只读检查确认：原26→27请求仍为 BUDGET_EXCEEDED，原28→29请求仍为 FAILED；恢复scope的两个operation仍为 FAILED_OR_UNKNOWN，没有升级成功。原2026-10-26完整run的声明总体5227、eligible3042、资格未知621、研究观察22、FOCUS0被完整保留；未提供后验不生成收益。执行readiness正确阻塞，退出4。以上是归档一致性与诊断验收，不是最新行情、来源本次重认或新增投资结论。

### 既有黄金身份校验失败

本轮改动同步前，在发布检出 `8166f1fdf39ac6a8a6272a3dd20edeeea95f603d` 独立执行 `tests/r05_product/test_research_r1.py::test_v2_golden_and_identity`，结果为1 failed、2.73秒、退出1，与开发目录联合检查的失败一致。黄金文件绑定旧基线 `d8091a4e9f76a6ff783c132f9519cdc520068e24`；当前既有 `strategy_experiments.py` 的源码身份与其不同，验证身份及epoch也已有差异。在只读内存诊断中，仅还原旧V2源码身份字段，完整控制结果摘要即恢复黄金值；这不是测试通过，也不是重新认证旧策略。本轮保留原失败断言和黄金文件，不修改核心以迁就旧身份。该项属于已确认的前置基线问题，完整旧黄金回归不能宣称全绿。

## 后续 P0 验收门槛

1. 在新来源协议中处理真实收件时首见钟与中断检查点；旧失败RAW不补钟。批量复权或历史修订证明仍需独立来源合同，不能用尾窗拼接放宽原完整性要求。
2. 后续明确新执行版本需在作业创建前持久绑定 request 和 job，解决旧失败回执无法绑定的问题；不能回写旧回执或借新版本重试。
3. 接入有真实来源绑定的执行证据、日期化规则/费用、容量情景与已冻结的多日期组合评价；现有条件归档模拟不能替代这一步。
4. 连续20真实交易日的请求终态、准时完整交付、后验成熟和人工介入分别记账。它是运行验收窗口，不是投资统计充分性；不自动安排采集、扩预算或改变通知计划。

`MARKET_EDGE=NOT_ESTABLISHED`。真实采集、连续日常交付、完整后验及独立投资验证均未由本轮工程检查完成。
