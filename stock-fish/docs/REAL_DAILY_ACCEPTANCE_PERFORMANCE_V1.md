# 当日可信输入、真实每日验收与性能改进

**当前结论：当日可信核心输入及双日期归档接库已完成；真实每日 worker 仍失败，H1 闭环验收未完成。** 本轮保留全部失败记录和原预算，不切换生产服务。

本轮观察日为 2026-10-16，承接[独立环境与 R2 闭环交付](RUNTIME_R2_CLOSURE_DELIVERY_V1.md)。工程测试、真实来源、每日作业完成和投资有效性分别验收。实际命令、原件身份、退出码和失败保存在私有 `reports/codex/2026-10-16-daily-real-performance/`，不发布行情、数据库、日志、环境或完整审阅包。

## 交付范围

保留 `DAILY_V1`、主动 V2、R1/R2 的公式、阈值、配置、冻结 run 和原输入。后验仍使用原全池，不重新选股；缺资料为 UNKNOWN/null，未到期为 PENDING。系统不连接券商、不下单，`MARKET_EDGE=NOT_ESTABLISHED`。

本轮沿用已验收的独立 daily/review 环境，所有真实导入使用新建隔离服务库，工程测试使用显式 TEST_FIXTURE。 两份真实输入继续标为 `RECONSTRUCTED_HISTORICAL`；本地原件、回执、源码和取得时钟的一致性鉴证不增加供应商签名或历史 PIT 证明。原环境、现有业务数据、调度和生产服务没有切换。源码发布不意味着运行实例已更新，也不使旧桥接凭证自动兼容新的 verifier 身份。

## 保持可信边界的实现

1. **事务末完整终验。** 每日批次保留发布前终验，在全部产品及 ledger 写入后、同一个完成事务提交前，再完整核对原件、定位器和借用对象。任何迟变、取消或预算失败回滚全部产品、ledger 及作业成功状态。ledger 保存的是发布前审计快照，不冒称包含事务末终验计数。
2. **减少重复大输入。** 桥接先严格解析并计算保存 JSON 的全部 canonical hash，仅保留小型日期、会话和 receipt 元数据，再释放完整 dict。未修改的 normalizer 从全部真实来源重建唯一 typed 输入；要求保存 JSON、manifest、run 和新 typed 全文 hash 一致。重复键、非有限值、额外或缺失字段、强制转换及时间表示差异继续拒绝。
3. **普通 JSON 编码提速。** 桥接使用标准库相同 JSONEncoder 选项的 C 编码路径，完整 Unicode 文本之后分块 UTF-8 编码。排序、数字、Unicode、拒绝条件和全部 canonical 字节保持。它仍分配完整 Unicode 文本；只有 UTF-8 临时复制每块不超过 1 MiB，不能称全文内存有界。原累计 spool 上限、私有临时文件清理、SQLite TEXT/JSON 约束、真实 BLOB hash 和完整来源终验保持。
4. **官方导出避免无效复制。** 仅短周期 JSON 导出在深拷贝前排除最终会被替换或删除的完整池字段；所有最终保留的可变字段和选中行仍深拷贝。原字段插入顺序、完整输出字节、view hash、四份 CSV 和父 run 不变。其他策略仍走原路径。
5. **本次鉴证内复用输入。** worker 先完整鉴证所有旧 run 并释放未借出缓存，再完整鉴证当日输入。fresh normalized 先由本次 session 的私有 capability 保留，供 loader 复用时必须通过绑定同一 store 的私有 proof，不通过普通 JSON 恢复。loader 仍检查 certificate、binding、来源登记、数据库两个父文档的实际全文字节和 typed 全文 hash，再登记强借用守卫。两个 terminal 仍重算全部借用 hash；原件迟变仍使整批失败。没有保留输入的调用继续使用原 gzip 加载路径。
6. **私有字段和元数据隔离。** inline typed 输入的历史 ref/hash 及不参与 canonical hash 的 `_history_store` 均必须为 None。已完成全部鉴证后的 run 小元数据以独立副本保留，metadata-only 视图避免再次展开或哈希未借出的完整 run；真正借用 run 时仍全文重验。公开 metadata 或 audit 的修改不能改变私有 proof，已借出对象的守卫不因后续调用而清除。

7. **typed 输入按 128 行序列化。** 桥接本地的 typed canonical 路径按固定 128 行调用 TypeAdapter，保留排序、完整 hash、所有终验及原 JSON 非有限值拒绝语义。仅 exact tuple 容器且全部行均为精确声明模型类型时进入批量序列化；其他容器、子类、异类行或非标准 input/universe 整体回到冻结 reference，保留原逐行 dump 与编码的执行顺序。每批前后及 UTF-8 块间继续预算/取消检查，没有缓存可变对象的 hash。128 是行数上界，不是单行或 envelope 的字节上界；长底层调用仍为合作式检查。

全部 9 个 normalizer 依赖文件保持原字节。没有改变来源可见时间、历史 PIT、复权基准或价格后验公式，没有把下载时间倒填为历史取得时间，也没有扩大原 300 秒、2 GiB 的共享鉴证预算。C 编码和部分底层解析属于合作式检查，长调用完成后才检查时间/RSS，不宣称硬实时抢占。

## 已完成的资源诊断

以下均为同一份旧真实归档的单次诊断；原件 SHA/物理身份按各自回执核对，导出微测另核父 run 全文 hash。这些微测未打开业务数据库。不同进程和机器负载不能拼接为完整闭环性能或吞吐承诺。

| 操作 | 原实现 | 改进或候选 | 结论 |
| --- | ---: | ---: | --- |
| run 普通 JSON hash | 10.5467 秒 | 5.7657 秒 | canonical hash 一致 |
| input 普通 JSON hash | 19.1053 秒 | 6.4461 秒 | canonical hash 一致 |
| input 私有 spool 写入及回读 | 25.7514 秒 | 7.7082 秒 | 原字节/hash 一致，不包含完整 SQLite 导入 |
| 官方 observation JSON 导出 | 23.978817 秒 | 1.362803 秒 | 全部 9,934,756 字节及 SHA 与原归档完全一致 |
| 上一行进程 CPU 时间 | 19.108035 秒 | 1.141809 秒 | 父 run 全文 hash 不变 |
| 先 run 后解析 typed 输入 | 峰值 2,196,832,256 B | — | 超过 2 GiB，不能用于验收 |
| 先 typed 输入后 run | — | 峰值 1,893,457,920 B | 单独对象生命周期诊断在限额内，不代表完整 worker |

保守 subtree 编码及逐行 dump 后的 8/32/128 行 typed 批处理没有取得性能收益，没有进入产品。原始失败和更慢结果均保留。另一个 TypeAdapter 批量原型在同一旧输入上将 typed hash 从 13.966810 秒降至 9.889533 秒，完整 hash 一致。其固定 128 行实现随后加入桥接本地路径，未改冻结归一器；单次约 4 秒的收益不能累加推定完整批次一定通过。

共享预算预演使用同一旧 archive 两次认证，第一轮 171.689860 秒，第二轮在 311.551345 秒的检查点触发原时间预算，退出 1、峰值 1,687,097,344 B；未进入 typed 加载、评估和最后两次终验。当时主题采集同时消耗 CPU，该结果不能精确归因于某个函数，但明确不能作为 300 秒全链通过证明。该预演发生在官方导出和 fresh typed 复用改进之前，且原件并集小于两个不同日期，不能替代真实验收。

完整复用改进后的第二轮同旧归档预演仍失败：两次认证累计 206.266849 秒，typed/run 借用完成于 235.593614 秒，未修改 evaluator 完成于 290.804093 秒；第一次发布终验在 311.293467 秒的检查点触发原 300 秒限额，退出 1、峰值 1,753,309,184 B。第三次 terminal 尚未完成，第四次尚未开始，不能理解为仅需再节省 11 秒。旧输入使这次预演的 3041 项均为 UNKNOWN，它不是当日价格后验。真实双日期还包含更多来源及数据库父字节复验，不能由 CPU 竞争结束推定必然通过。

开发源组合另完成一次旧 10 月 15 日单归档的新隔离库导入诊断：内部总计 210.066646 秒、峰值 1,650,380,800 B、四次 terminal、实际读取 1,188,746,485 B，退出 0。它在原 300 秒/2 GiB 内完成，但不属于最终 publisher 源组合，也不证明当日输入或双日期后验已通过。

## 真实输入与每日后验

当日 standalone 使用真实当前时钟补采并封存，2026-10-16 14:57:35 UTC 完成，状态 `COMPLETED_WITH_UNKNOWNS`，worker 与外层 CLI 均退出 2；核心、执行源码身份和来源 terminal binding 均通过，1345 条来源引用保留。完整执行约 5813 秒，71 个逻辑采集请求（另记 5 次 HTTP 尝试、3331 次 SDK 调用），theme 阶段实际读取 11,497,125,238 B，在本次显式 12 GiB/7200 秒预算内完成；没有失败后自动重试或扩额。该归档状态不等于所有资料完整。

10 月 16 日原始日线 5226 条、当日证券目录 7427 条；3197 个证券的复权值由 64 批返回截至 10 月 16 日的 46,676 条历史记录（请求起始于 1990-01-01；3197 是来源范围，不等于新核心或旧后验池），均保留原件、回执、实际取得时钟与完整请求范围。行情首见于 13:23:26 UTC，复权末批约 13:51:43 UTC，核心 cutoff 为 13:52:01.514530 UTC；不能回填为历史已可见。新闻为 UNKNOWN，主题来源/模型为 PARTIAL，请求范围 70 项，其中建模 34 个、UNKNOWN 35 个，另 1 项为明确排除的 ST 资格类别（`QUALIFICATION_CATEGORY_NOT_THEME`），不把它当经济主题。

收紧副作用边界后的最终源码在新的 final-3 隔离库完成两份真实归档导入，均退出 0：旧日内部 133.661391 秒、外层 134.562184 秒、峰值 1,672,380,416 B；当日内部 116.084432 秒、外层 116.749114 秒、峰值 1,686,331,392 B。两次均完成四次 terminal，保持原 300 秒/2 GiB 限额。随后真实 run 阶段退出 1：完整 submit.plan 后的 worker 于 300.017463 秒结束为 FAILED，整个 run 回执 459.785286 秒、峰值 1,785,253,888 B。公共错误为脱敏 `WORKER_FAILED`/ValueError，不能仅凭约 300 秒耗时冒称已记录精确内部原因。失败后严格复核四个父 BLOB 的完整 hash/bytes 均未变，原核心原件保持，新增文档列表为空、ledger 为 null。每日作业未通过，同日幂等、三产品回查及真实独立 H1 复算均未执行；没有把导入成功升级为每日验收完成。

最终源码的显式诊断重试保持原已受理请求的 cutoff、源码及限额，仅记录原方法的返回/异常，未倒填时钟。重试仍 FAILED：两次 terminal 分别完成于 136.650345、294.857300 秒，随后在 `_authenticated → _verified → _stored_matches(candidate_inputs)` 的 `session.check()` 触发 `R2_ARCHIVE_BRIDGE_SESSION_TIME_BUDGET_EXCEEDED`，尚未完成对象借用、评估及最后两次终验。诊断内部 300.517724 秒、外层 304.041997 秒，退出 0 仅表示异常已被记录；原正式 run 失败结论不变。诊断后的只读核验退出 0，四个真实父 BLOB、全部核心原件和主库/WAL 读取前后均保持，508 个导入文档仍在，两个作业均失败、无产品或 ledger。此时仅对已静止且 WAL 为空的本轮新隔离库使用 immutable 读取，没有用于生产库或先前严格只读验收。

原 10 月 15 日冻结信号池为 3041，研究观察集合 25，FOCUS 0；本轮当日新核心池为 3042。二者是不同 run，不互相替代。10 月 16 日仅 H1 到期；H2/H3/H5/H10 分别等待 10 月 19、20、22、29 日。H1 的成熟价格观察不代表成交、费用后收益或超额收益。

## 检查与失败记录

最终发布目录完整组合回归 **275 项通过，415.84 秒，外层 417.371797 秒，退出 0**：bridge 185、daily 47、CLI 20、导出合同 23。对应六个产品/测试文件与 final-3 实际验收源码 SHA 逐一相同；数据保护检查也通过。此结论仅限工程检查，真实每日 worker 的失败及未运行的后续验收不因此改变。

```sh
"$DAILY_PYTHON" -I -B "$PUBLISHER_ROOT/stock-fish/scripts/run_isolated_tests.py" \
  tests/yaogu/test_short_horizon_archive_bridge_v1.py \
  tests/yaogu/test_short_horizon_daily_closure_v1.py \
  tests/yaogu/test_r2_closure_cli.py \
  tests/yaogu/test_strategy_json_export_copy_contract.py -q
```

实际隔离入口形式如下，`DAILY_PYTHON` 为明确选择且已验收的独立解释器；原始 argv、时间和退出码保存在各命令回执。

```sh
"$DAILY_PYTHON" -I -B stock-fish/scripts/run_isolated_tests.py tests/yaogu/test_short_horizon_archive_bridge_v1.py -q
"$DAILY_PYTHON" -I -B stock-fish/scripts/run_isolated_tests.py tests/yaogu/test_short_horizon_daily_closure_v1.py -q
"$DAILY_PYTHON" -I -B stock-fish/scripts/run_isolated_tests.py tests/yaogu/test_strategy_json_export_copy_contract.py -q
```

加入 TypeAdapter 之前的开发源完整回归：bridge 141 项（329.14 秒）、daily 47 项（77.18 秒）、导出复制合同 23 项（1.12 秒）通过，退出码均为 0；原 117 个 bridge 测试及 9 个 normalizer 文件原字节保持。随后核对当时六个产品/测试文件与发布目录 SHA 一致，在发布目录跑下列集成集，128 项通过（178.72 秒，外层 180.24 秒），退出 0。覆盖 native 原件、R1/R2、后验、CLI、JSON 导出和模拟账户安全，未降低覆盖要求或扩大 skip。独立 H1 审计脚本的 22 项轻量反例另通过，不能当作真实 H1 已验收。各阶段重复测试不累加为一次全仓通过。 收紧副作用快速资格后的发布目录全部 TypeAdapter 定向组为 44 项通过、141 项未选中，2.56 秒、退出 0；这里未选中的是定向选择范围，完整桥接/每日/CLI/导出回归另行记录。

```sh
"$DAILY_PYTHON" -I -B "$PUBLISHER_ROOT/stock-fish/scripts/run_isolated_tests.py" \
  tests/yaogu/test_premarket_native_core_v1.py \
  tests/yaogu/test_short_horizon_strength_r1_integration.py \
  tests/yaogu/test_short_horizon_strength_r2_integration.py \
  tests/yaogu/test_short_horizon_outcomes_v1.py \
  tests/yaogu/test_r2_closure_cli.py \
  tests/yaogu/test_strategy_json_export_copy_contract.py \
  tests/backtest/test_execution_exposure.py \
  tests/r05_product/test_weekly_paper_execution.py \
  tests/integration/test_final_paired_accounting_gate.py -q
```

未执行全仓、Qlib、Live、历史全市场回测或投资策略晋级验收。旧 standalone 文件初次和交付前两次保护检查均退出 0（最终内部 13.645422 秒、外层 15.004417 秒）：3816 个文件事前记录的 size/mtime/ctime/inode/device 均不变；其中具有既存原生引用的 892 个文件，SHA/bytes/完整 7FP 全部匹配；全体当前读取 SHA/7FP 均稳定。其余文件缺完整事前 SHA/7FP，不能把本次新计算的 hash 当作事前证据。

首次真实 plan 的业务结果为 READY，鉴证 178.778930 秒，但外层严格文件守卫报 `READONLY_DATABASE_FILE_CHANGED`，整步退出 1，未启动作业；四个父 BLOB 的完整 hash、508 个文档及 0 个 job/event 均不变。独立 TEST_FIXTURE 复现了 SQLite 首次 `mode=ro` 读取建立空 WAL 的行为；非同步临时目录中第二次读取的原严格守卫通过。原真实失败未保存完整前后文件明细，不能追溯断言它只有空 WAL 新增这一原因。保留首次失败后，在同源码、同新隔离库做一次显式复核，原 harness 字节和所有断言不变：只读前置检查通过，plan 于 206.301932 秒完成，内部鉴证 182.292868 秒，主库/WAL 全文 hash 与 7FP 前后一致，退出 0。没有删边车、改时钟、修改文件属性或使用 immutable 连接隐藏 WAL。

首次实际每日 worker 于 312.40 秒结束为 FAILED，公共错误为脱敏的 `WORKER_FAILED`；四个父文档未变，没有新增产品或 ledger。随后对同一个失败作业显式重试，仅包装异常和 terminal 事件作诊断，未改实参、源码、时钟或 300 秒/2 GiB 限额：第三次 terminal 入口累计 344.65 秒，明确抛出 `R2_ARCHIVE_BRIDGE_SESSION_TIME_BUDGET_EXCEEDED`，重试仍为 FAILED、ledger 为空。该内部原因属于这次诊断重试，不追溯冒称第一次脱敏失败已记录相同原因；诊断退出 0 只表示成功记录异常。

TypeAdapter 候选的独立复核进一步发现，绕过模型赋值校验注入有副作用的可重放迭代器，可以在批量取行期间恢复被改字段，使候选 terminal hash 匹配原值，而冻结 reference 会拒绝。虽然真实归一器及 JSON loader 均产生 tuple，这仍违反借用迟变守卫的等价要求。因此主动中断 final-2：旧归档导入完成于 202.315520 秒，当日导入约 29 秒时停止，未进入 daily；完整工程组合到 99 项通过时中断，退出 2，不能算完整回归。保留该隔离库及所有记录，不发布存在此边界的候选。随后将快速资格收紧为两层 exact tuple 且所有 row 均 exact 声明模型，其他对象整体回到原 reference，并补专门反例；后续验证单独记录。

保留的实际失败包括：首次采集因旧 producer 字节不符在零网络请求时拒绝；第一次旧真实归档导入在 300.286962 秒的事务末终验失败，整笔回滚，documents/jobs 均为 0、数据库 SHA 不变；Documents 下新维护文件出现 flags/ctime 改变时严格拒绝，后续在新独立状态目录重做维护，没有改变旧原件。不能推断后台修改者身份。

新增测试曾因错误路径而未发现测试、使用 pytest 保留参数名而收集失败，以及对公共错误脱敏/时间表示作了错误预期。TypeAdapter 定向组首轮 34 通过、3 失败；三项新增反例错误预期 DailyBar 非有限值会变 null，实际冻结 reference 会拒绝。修正新增测试为两条路径均抛 ValueError 后，37 项通过（11.45 秒，退出 0），原 141 项测试逐字保留。各次退出码保留，修正的是测试入口或新增反例本身，原产品安全断言、失败样本和覆盖要求没有删除或放宽。

## 剩余验收与后续顺序

- 当日封存与最终源码双日期接库已完成；最优先解决完整 worker 在原共享预算内仍失败的问题，再验收每日作业、三产品回查、同日幂等和独立逐证券复算。任一步失败须明确停在哪一层，不能把前一层成功升级为全链完成。
- 先在完整、重复的真实轨迹上拆分认证、数据库父字节核验、评估及每次 terminal；不同负载下第二次认证终验已到 294.86 秒，不能把较快试跑视作稳定容量。冻结 evaluator 中仍有原逐 token run hash 和逐行 typed hash，可在明确的新实现身份下单独验证同字节编码；公式、完整身份和终验保持。再评估双 run hash 的同次 canonical 编码及严格流式解析，不能缓存早期 hash 代替末端复核。
- 再测 1/2/5 个实际规模 run 的完整批次。当前仍保留所有借用守卫与计算产品；默认单批上限为 5，可显式配置 1–100；任何配置均不代表已验证容量。后续若需要串行对象借用或产品 spool，必须保留整批原子提交和迟变回滚。
- 多个真实交易日验证滚动会话窗口、当日准确输入、恢复/重试、源文件保存和资源分布。H2 以后逐日资格及父来源须真实取得，不能用今天的目录回填过去。
- verifier 源码改变后使用明确的新鉴证过程与新隔离库验收。精确旧源码副本只证明原内容，不恢复原物理 owner；长期升级、归档保存与恢复仍需专门演练。
- 最后另做费用、滑点、容量、可成交性及独立周期的样本外研究。工程成功与单日观察均不能建立市场优势或解除生产晋级限制。
