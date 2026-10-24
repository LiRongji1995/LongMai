# 日期化证券身份输入与真实数值 H1：2026-10-24

本轮把“作业成功但全部 UNKNOWN”推进到一段可以独立复算的真实数值 H1。新增显式输入版本与后验入口，不修改旧输入、原信号、冻结策略、九个 normalizer 或生产服务。默认 300 秒是否达标，必须以其自身真实运行结果单独判断。

## 本轮实际范围

复用已封存的 10 月 15 日、16 日和 23 日三个真实归档，未重新选择股票。23 日输入提供后续价格，16 日已采集的官方目录和日期化市场记录提供仅适用于 16 日的身份补证。新输入版本为 `DATED_IDENTITY_OUTCOME_INPUT_V1`，输入为 `market_44b151bbf6a7f501e90200b0d367d1184d2539997c7cf6359791ebc7428a45f6`。

实际资料截止为 `2026-10-24T12:53:51.050614+00:00`，业务会话为 10 月 23 日；这是显式 late-completed 事后处理，`current_session_acceptance=false`、`historical_visibility_upgraded=false`。不追认旧盘前、历史 PIT、可成交结果或连续每日交付。

仅补入 3,197 条 16 日证券记录的 board/security_type 证据。交易所、上市退市日、ST、停牌、风险及有效区间须与原记录一致，冲突拒绝；19 日身份不从 16 日延伸。该证据来自实际 capture-day 目录与同日日期化市场记录，不声称官方 16 日收盘全量历史名册。价格、复权基准、日历、题材、资金和旧 receipt 前缀逐分支保持。

价格诊断完整覆盖 15→16 的原全池 3,041 只／观察 25 只，以及 16→19 的原全池 3,042 只／观察 26 只。原冻结参考价、后续 OHLC、tick、可比 OHLC、`BAOSTOCK_HFQ_RAW_X_BACK_FACTOR_R03_V1` 基准和父来源实际可见时间通过。第二组价格通过不代表日期化身份通过；未选的 603759.SH 停牌状态保留。

## 输入与存储边界

`dated_identity_composite_input_v1.py` 只进行确定性字段投影，不发行来源能力。`dated_identity_outcome_closure_v1.py` 每次重新认证原归档，认证完整原始 JSON、行数、schema、normalization report、typed hash、原 run、五导出、来源与当前物理身份，再在私有 store/session proof 中完整重构新输入。

登记分两阶段追加：先提交新输入、索引和 descriptor，再以真实时钟写入独占新文件并 fsync，最后追加完成 seal。中断保留不可用半成品；无完整 seal 就拒绝使用。seal 绑定完整 descriptor、输入 hash、两份派生源代码、路径、inode 与七字段物理身份；文件及源代码实际 ctime 必须不晚于 cutoff，不能仅靠 JSON 中声明的时间倒填可见性。

新 loader 只接受本次私有 proof。计划阶段也完整重构，写前及事务内末验重新读取 descriptor、seal、新输入 BLOB 和全部原父来源；产品、完成 ledger 与作业成功仍在同一事务。原 documents 的 update/delete 保护保留，失败不能留下部分产品。源码复制到另一目录不迁移这些私有 seal；在新 source/owner epoch 中需重新完整登记。

CLI 新增 `r2-dated-input-register` 及 `r2-dated-late-plan/run/status/retry`。各写入与认证入口显式选择现有独立 DB/state、`SEALED_RESULT_LOCAL_COPY_V1` 和 manifest SHA；登记另给 base input 与 identity input，late 计划／执行选择已登记的新 input、原 run、业务会话及截止，状态查询使用明确 job id；不自动初始化、迁移、恢复作业或选用生产库。默认认证仍为 300 秒、2 GiB；`FULL_ARCHIVE_900S_V1` 是既有显式正确性档位，不能混称默认达标。

## 数值 H1 验收

原 run `strategy_experiment_cfd3f30d0e1c2408bed5ebfd` 的 15→16 H1 全池 3,041 条和观察集合 25 条全部为 MATURE。独立程序不导入项目 projector、evaluator 或产品模块，以原封存资料和新库完整 BLOB 进行 Fraction 复算；每只证券的五项指标共 15,205 个数值逐项一致，tracking 和 review 的计数、均值一致。

| 集合 | 成熟 H1 | 收盘相对参考价变化均值 | 正值数量 |
|---|---:|---:|---:|
| 原完整资格池 | 3,041 / 3,041 | +0.6523643803% | 1,920 / 3,041 |
| 原研究观察集合 | 25 / 25 | −0.5680538304% | 12 / 25 |

这是标记价格观察，不是持仓收益、成交模拟或投资优势；只有一个信号日，不能据此验证参数或调参。`MARKET_EDGE=NOT_ESTABLISHED`、`execution_status=NOT_VERIFIED` 保持。

新 revision 为 `short_outcome_4bfcc3b72628eac04b70adcf5ad92505be2839de9d07d9f1fa1302c335de2d09`。单 run 的四个已到期期限共 12,164 行：H1 的 3,041 条 MATURE，其余 9,123 条 UNKNOWN；H10 单独保留 PENDING 调度状态。未成熟或缺证据不计零收益。

独立程序不重新发行来源能力；完整 RAW／九个 normalizer 鉴证由已通过的原生前置阶段承担，独立程序重新核其引用与消费全文。独立 H1 用时 201.993353 秒，峰值 RSS 1,625,468,928 字节，原 300 秒／2 GiB／8 GiB 读取门禁通过；前后主库家族、原件全文 SHA 和七字段身份保持。第一次独立脚本因 cutoff 的 datetime/ISO 类型接缝退出 1，未进入数值计算；修复后增加 15 个接缝反例，在新脚本与新结果文件中重验，旧失败保留。该独立程序通过不代表 native 后验 300 秒通过。

## 真实闭环与耗时

所有阶段均在新 owned 库执行，未打开旧库 SQL。下面各阶段实际退出均为 0；900 秒档位的时间与默认 300 秒结果分开。

| 阶段 | 实际耗时（秒） | 验收内容 |
|---|---:|---|
| 原15日归档接入 | 158.000 | 完整重新认证并追加原 input/run/导出 |
| 原16日归档接入 | 140.110 | 日期化身份父来源完整认证 |
| 原23日归档接入 | 129.570 | 原价格输入与全部来源完整认证 |
| 新日期化输入登记 | 242.060 | 新输入全 hash、字段 lineage、两阶段 seal |
| 单 run 提交至结束 | 706.054 | 含计划；worker 412.797720 秒 |
| 单 run 幂等 | 304.146 | 原作业、状态、事件、文档集合不变 |
| 单 run 三产品回读 | 29.699 | API / 完整 BLOB / canonical hash / revision 全身份一致 |
| 独立数值 H1 | 201.993 | 全池、观察集、五项 Fraction 与汇总一致 |
| 双 run 提交至结束 | 947.411 | 含计划；worker 601.294514 秒 |
| 双 run 幂等 | 357.037 | 原作业、状态、事件及完整文档集合不变 |
| 双 run 三产品回读 | 40.266 | 两组共六产品 API／全文 BLOB／revision 一致 |

计划与 worker 分别创建原生鉴证 session，各自执行既有显式 900 秒限制；947.411 秒是两阶段加调度的总墙钟，不是某一 session 超过 900 秒后被放行。

双 run 仍使用同一原始截止和新输入。第一组复用相同 revision；第二组 `strategy_experiment_578bc65c3af13d8935641bd4` 的 12,168 条到期期限行仍全部 UNKNOWN，19 日身份缺口保留。单／双 worker 的 ledger 写前认证快照分别为 384.184662／546.561346 秒，峰值 RSS 分别 2,037,207,040／2,030,252,032 字节；快照时间不包含之后的完整事务末验，不能当作 worker 总耗时。

## 发布目录隔离工程检查

发布目录的九个完整测试文件实际通过 **495 项，0 failures／errors／skips**，退出 0。pytest 549.31 秒，子进程墙钟 551.520224 秒，外层 554.209669 秒。使用锁定的原生 CPython 3.12.14／58 包独立环境，不属于全仓测试或投资验证。

实际命令的路径参数化形式如下，`PY` 为上述已验收解释器，`BUSINESS` 为发布业务目录，`NEW_TEST_ROOT` 为独立私有新目录；父层清除外来 Python／pytest／项目及凭据环境，禁用自动插件和 cacheprovider：

```sh
cd "$BUSINESS"
"$PY" -I -B -X "pycache_prefix=$NEW_TEST_ROOT/empty_lookup" \
  "$BUSINESS/scripts/run_isolated_tests.py" -q \
  tests/yaogu/test_short_horizon_archive_bridge_v1.py \
  tests/yaogu/test_short_horizon_daily_closure_v1.py \
  tests/yaogu/test_short_horizon_late_completed_session_v1.py \
  tests/yaogu/test_short_horizon_outcomes_v1.py \
  tests/yaogu/test_dated_identity_composite_input_v1.py \
  tests/yaogu/test_dated_identity_outcome_closure_v1.py \
  tests/yaogu/test_dated_identity_closure_cli.py \
  tests/yaogu/test_closed_session_cli.py \
  tests/yaogu/test_r2_closure_cli.py \
  --basetemp="$NEW_TEST_ROOT/TEST_FIXTURE_pytest" \
  --junitxml="$NEW_TEST_ROOT/junit.xml"
```

原 PUB 的缓存没有删除；原 core／tests／scripts、目录 inventory 和缓存的完整 SHA／物理身份前后保持。新的 pyc 查找目录始终为空。单独的陈旧但合法 pyc 反例说明 `-I -B` 仍可能读取原 pyc，因此使用新空查找目录，不能只靠禁止写 pyc 认定执行了新源码。

最初发布测试入口因拒绝已有 cache，在 pytest 启动前退出 1、0.241157 秒；后继 tests-only 入口保护旧缓存并重定向查找后完成验收。未删除失败、缩小原断言或新增 skip。

## 未采用的真实性能候选

父时钟投影／tuple 范围索引做了 74 项独立夹具差分，以及同一真实旧归档的三个顺序新进程对照。每臂仍完整重新认证 RAW、九个原 normalizer、报告、原 input/run 与来源；130,520 行全部 Resolution、完整有序行情行、89 个全文来源对象和 effective dependencies 的 hash 逐项相同。该真实批次全部允许，拒绝、循环参数及 ABA 等反例来自独立夹具，不能混称真实样本覆盖。

| 实验臂 | 全臂墙钟（秒） | 全行情映射及差分阶段墙钟／CPU（秒） | 所有者进程树峰值 RSS（字节） |
|---|---:|---:|---:|
| 原参考 | 143.636853 | 39.168／36.815 | 1,742,282,752 |
| 只改时钟投影 | 164.027553 | 47.255／44.327 | 1,744,429,056 |
| 时钟投影及范围索引 | 151.927454 | 49.591／45.086 | 1,744,359,424 |

映射阶段还包含全部 Resolution／来源表、排序与完整行 hash、依赖 hash，未单独测纯 resolver 或全进程 CPU。三个进程的原 fresh authentication 墙钟有波动，不能算作候选收益；本批非空范围 membership 快路计数为 0，不能宣称真实索引收益。相关阶段 CPU 增加约 20.40%／22.47%，该候选未安装。

第一版可实际运行的 GC 候选 V2 仅抑制 `_authenticated` 范围，却在每次原 terminal 前额外 full collect 并恢复自动 GC。真实单 run 在计划末端原 terminal 的全文读取处达到 `SESSION_TIME_BUDGET_EXCEEDED`，退出 1，外层 334.515633 秒，进程树峰值 1,865,314,304 字节；worker 未启动、新产品和新 ledger 均为 0。已有完整 BLOB／schema／索引、源码与 GC 最终保护通过，旧失败封存。V2 的 43 项小夹具通过不能代替真实性能通过；其双 run 未启动。

## 执行接缝及失败记录

真实数据链在成功前保留三次私有 harness 失败：source-overrides schema 不合入口合同（退出 1、1.223371 秒）；较早 producer runtime 原路径物理身份已因前轮发布改变（退出 1、19.893946 秒），同期保护误把测试缓存变化计为源码变化；新 owned 库的 URI／`:memory:` 结构参考被过窄审计拦截（退出 1、113.250040 秒）。修复分别限于正确的 override 控制、已保留原字节的显式源码映射和新库／结构参考白名单；RAW、旧 producer 归档和业务校验没有放宽。新私有执行版本 V4 随后完整重验通过，不覆写前次失败。

这些是工程调用接缝的失败，不是新采集成功或原件物理身份恢复。旧 25 个 RESULT 原路径仍为 `LOST_NOT_RESTORED`；0400＋UF_IMMUTABLE 的 25 个不同 inode 同字节本地复制件，只能在显式本地复制协议下使用，不能称找回旧原件、历史 owner 或 PIT。

最终保护扫描、精确交付文件、运行命令与 Git 发布核验分别保存私有收据；完整本机审阅包、数据库、RAW、复制件、缓存、环境和日志不进入源码提交。源码发布不触发服务切换。


## GC V3 的真实拒绝与未完成范围

V3 把自动 GC 抑制扩至同一 genuine session 的完整计算与全部原 terminal；以显式 PRECOMMIT callback 委托原 `JobService.complete_atomically`，在原写事务提交前执行 full collect、原完整末验、恢复和资源检查。源文件、原 `_run`／`ctx.complete`／原子事务 body 及九个 normalizer 不替换。运行根显式传入，与源码分离。

最终独立 44 项夹具通过，19.60 秒、外层 22.1795 秒、退出 0。先前两次分别 42／44、43／44，涉及临时强引用、样例审计列表初始化与缺原生 summary 的 TEST_FIXTURE 构造问题；实际 API 和原断言保留，失败源码快照与日志不删。最终同字节源码通过静审后，才准备新 claim 和真实执行；并未向发布目录复制候选。

真实单 run **退出 1**：计划 `_reconstruct → digest_market_input → universe securities` 的完整 hash 校验触及原 300 秒。外层 333.503784 秒、owned 子进程树峰值 1,866,919,936 字节；worker、幂等、三产品补验均未启动，`base_atomic_commit_succeeded=false`、新增文档 0。另保留 5 项清理阶段 ValueError 记录；最终 GC 状态还原、源代码／控制、完整旧 BLOB／schema／索引保护通过，不能把清理阶段写成无错误。

该计划仍使用原默认 `TYPED_ROWS_128_V1`；1024 行 profile 是原 worker 的显式选项，不能把本次失败说成已经实测 1024 行计划。V2／V3 的双 run 均未启动，不将未运行改称失败或通过。原默认线程池路径、这两版候选与独立 H1 的 300 秒检查是三个不同范围。

当前 inline fresh normalize 后已使用 bridge 批量 hash；旧 `history_artifacts.digest_market_input` 不是三份 RAW normalize 中可以直接删除的重复编码。需先测各归档 normalization、typed 重构／hash、数据库父 hash、terminal 和每 run resolver 的分别成本。显式计划 1024 可作为下一步对照，但不能据此承诺已实测 412.798／601.295 秒的 worker 都会降到 300 秒。

V3 child 源 SHA 为 `acc2ccb8b882f5e54f665a8c22fdaf91a8b80780c04c0eeca1d5d45fa388c756`，失败监督收据 SHA 为 `57b2f636b16996c8b569620e9a7ffa4aa183bb36a1f5916f8f66f46d1e65979f`。这些只用于追溯私有未采用候选，不构成发布、默认启用或性能成功。

## 本次精确交付与后续门槛

本次源码交付为 19 个文件：两份新的日期化输入／后验模块、三份既有桥接／closure／CLI 修改、三份对应测试、十份现有入口文档及本报告。GC V2／V3、父时钟／范围索引候选及其私有测试工具均未纳入。原完整九文件工程检查为 495 项通过，不将独立 44 项私有候选测试加总为同次 539 项验收。

发布前按精确路径、完整 SHA、原两项发布目录改动及开发目录其他 WIP 末验，只暂存本次范围；中文提交到约定 main，远端实际结果另以交付回复及私有 Git 收据核对，本文不预写自己的提交 SHA 或推送成功。现有生产实例、默认入口和调度没有切换。

所有数值与来源完整性仍为当前已证范围。继续推进须完成原 300 秒／2 GiB 下的单／双 run 计划、worker、幂等和三产品验收，另取得 19 日及后续适用的证券身份；当前可用磁盘约 1.73 GiB，低于新独立库实验 2 GiB 的准入要求，后续新库实验需先安排存储。


## 尚需完成的蓝图

1. **日期化身份完整性。** 为 19 日及后续会话取得可认证的 MAIN/A_COMMON 适用证据与生命周期／风险来源。官方日期行情能够证明价格，却未提供完整 board/type；当前目录忽略历史参数的响应、包含 B 股／CDR 的混合排名及旧前缀政策不能顶替。现有有限探查未找到合格证据，不推断其全球不存在。
2. **父来源解析成本。** 保留每行 status、完整性、类型、定义、证券／会话范围、行父时钟和迟到规则；只对不可变 tuple 的纯 membership 与精确时钟投影建立执行期索引。循环参数、畸形值、自定义时区、子类、迟变和 ABA 必须有参考回退及完整差分。真实全行／依赖检查通过后，才考虑正式执行版本。
3. **独立进程 GC 调度（本轮候选未通过）。** 仅在独占、单 OS/Python 线程的显式进程试验，保留原 JobService 状态、取消、锁、原 handler 和原子完成；自动 GC 的清理、恢复与所有原 terminal 都留在原预算内。不得在普通 Web 服务中全局关闭 GC，也不能把清理移到成功提交之后。
4. **认证与多 run 复用。** 若需新的归一化执行版本，原九文件和旧归档保持；新代码／执行 policy 完整登记，逐归档对比完整 typed hash、report 与拒绝行为。多 run 只能复用同一 store/session、同一完整输入／cutoff 的私有只读验证结果；全行、全父与末验继续，不能用保存 JSON、缩短窗口、跳行或 ID 缓存替代认证。
5. **最终容量验收。** 单、双 run 分别在原生默认 300 秒／2 GiB 下完成计划、worker、幂等、三产品及独立复算；分别记录首次写与一致版本复用。当前成果不外推默认五 run、连续交易日、冷启动、Windows 或生产切换。

旧结果、失败和参数“尚未验证”标记保留；本轮不连接券商、不下单，不修改 Qlib 0.9.7 wheel、模型 hash、运行服务或默认主动 V2／DAILY_V1。许可与第三方通知保持。
