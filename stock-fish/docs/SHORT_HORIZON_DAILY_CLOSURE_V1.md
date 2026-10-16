# 短周期每日后验闭环 V1

本模块是显式、本地管理员调用的单次作业；不会创建计时器、采集行情、自动选择最新输入或接入旧 SAM 自动调度。它复用现有服务数据库、JobService 与 `SHORT_HORIZON_OUTCOME_V1`，不修改 R2 选股规则、冻结 run 或既有后验口径。操作入口与本轮实际验收见 [运行环境与 R2 闭环交付](RUNTIME_R2_CLOSURE_DELIVERY_V1.md)。

## 输入与信任边界

- 必须明确给出原 run 集合及本次已登记 `input_id`；两个方向都必须来自可信 standalone archive bridge。只有自声明 coverage、JSON hash 或普通注册输入不够。
- run 必须是已保存的 `SHORT_HORIZON_STRENGTH_R2` 单策略研究运行，保留原输入身份、全量 items、截止及 content hash。
- 本次输入的观察日与最新已登记会话必须都等于实际上海交易日。旧输入不能冒充今日 daily 输入；普通后验 API 的历史研究用途不受此门禁修改。
- 计划与执行分别创建新认证会话；同批全部 run 与新输入共用一次总预算。认证结束必须终验。执行中的文件读取、规范化与终验接受取消检查，超过文件、字节、时间或 RSS 预算即失败，不按 run 重置额度。
- `plan()`、`pending()`、`status()` 及对象构造只读；仅 `submit()` / `retry()` 注册新作业处理器。没有第二个权威库或另行维护的研究名单。

计划与待办的日期检查使用桥接接口在本次完整认证后产生的小日历视图，不为取得会话重新展开完整行情 input，也不信任普通保存索引。执行先认证当日输入及所有原 run，再加载一份已认证的当日 typed input；原 run 借用桥接已核对的同一对象，不再次从数据库解码副本。释放归档缓存只释放重对象，保留全批原件登记、实际读取预算及终验；借用 run/input 的内容 hash 也在终验复核，原地篡改仍整批失败。多 run 的冻结对象仍占用内存，受原共享资源上限约束；超限失败，不自动截断范围、缩减原件验证或提高额度。

## 真实会话与成熟待办

使用既有、有明确范围和验证日期的 2026 年沪深交易所日历，不把普通工作日、政府调休工作日或周末计入 H。H 固定为 1、2、3、5、10 个交易会话，成熟时刻为对应会话上海时间 15:00。

原输入与当日输入允许采用逐日滚动的历史窗口。若当日输入仍包含信号日，只对两份已认证日历共同保留的历史范围（两个窗口起点的较晚者至信号日）逐项核对；滚动丢弃更早历史不构成冲突，共同范围漏会话或插入假日仍拒绝。若当日输入已不含信号日，由原后验规则明确保留 `UNKNOWN`，不会补造信号参考价。信号日之后的 H 仍按官方完整会话轴计算，缺日不能压缩期限。

计划中的 `DUE` **只表示日历已到期**，不表示价格、证券资格、父来源或可见时间已通过。执行仅把已到期的 H 交给原 `SHORT_HORIZON_OUTCOME_V1.evaluate()`；该规则继续区分 `MATURE / PENDING / UNKNOWN`，检查原参考价格、逐会话价格和日期化资格，以及行和父来源的实际取得时间。缺失、晚到、停牌、未知资格、缺会话、口径不一致不会补成零收益。

未来 H 留在 ledger 的 `horizons` 中，并标为 `PENDING`。验证日历超出范围的 H 保留 `UNKNOWN`。不支持的年份不会自动扩展日历。周末或节假日提交得到 `NO_SESSION`，收盘前得到 `BEFORE_SESSION_CLOSE`；未来截止、非当日截止及早于冻结信号的截止也不能提交。

`pending(input_id, limit=100, offset=0)` 从同库 bridge 绑定与证书发现历史 R2，并读取原 run 和最新后验产品：

- `total`、`indexed_run_total`、`stored_complete_total` 与 `actionable_total` 覆盖全部扫描范围；输出分页不改变这些总数，也不会因为单批默认 5 个 run 而只发现前 5 个。
- 必须是**最新一份后验产品**完整覆盖冻结 `items × H1/H2/H3/H5/H10`，没有重复且每行都是 `MATURE`，才算保存证据已完成。只计算 H10 或只覆盖研究选中股票不能关闭任务。
- 空池不能使用 `all(empty)` 推出完成。仅当冻结资格 scope 明确 `COMPLETE / universe_complete`、资格与评价计数均为 0、结果为 `EMPTY`、全池保留声明及空 `eligible_pool_ids` 一致时，计为 `legal_empty_pool_total`；否则为 `UNKNOWN_EMPTY_POOL_PROOF_REQUIRED`。
- `UNKNOWN_AWAITING_NEW_INPUT` 表示当前相同输入仍有未知，需要新的显式、可信输入复核；不触发自动采集。`WAITING_MATURITY` 与 `UNKNOWN_CALENDAR` 分开保留。
- 发现阶段验证当前保存的绑定、证书、run 与后验 payload 字节 hash；不会为了列待办重读所有历史 RAW。结果明确标注 `source_reauthentication_required=true`。被选入 `plan/submit` 的 run 必须再完整认证原件，发现记录本身不授予执行信任。
- 发现最多允许扫描 10,000 条绑定；超过时明确失败，不返回假装完整的部分总数。分页上限为 1,000。

## 幂等、重试与原子提交

每日任务身份绑定完整 run 集合、桥接身份、明确输入、实际交易日，以及闭环/后验实现和日历的源码身份。第一次接受的评价截止冻结在 job payload；同日相同输入的再次调用返回已有任务或其最新重试，不因墙钟变化重跑。新输入或新交易日可以形成新的任务。后验 revision 继续由原 V1 内容身份决定；内容相同不重复写入，第一次观察时间不改写。

单批默认最多 5 个不同 run，显式配置可在 1..100 范围内调整。超限直接拒绝，不能静默截断。重试也检查当前显式额度；原大批次不能凭持久 payload 自动提升新进程的默认额度。

整个批次先计算，在一次 `JobContext.complete()` 事务内提交全部 run 的 outcomes、tracking、review 与 `short_horizon_daily_closure_ledgers_v1`。已有 revision 必须通过当前 payload hash 检查；忽略唯一允许不同的 `evaluated_at` 后，所有字段仍须完全一致，否则整批回滚。第二个 run 失败、任何产品冲突、终验失败或提交前取消，都不能留下本批部分产品或成功 ledger。

执行时的空池也必须通过与待办发现相同的资格证明：合法空明确记录 `LEGAL_EMPTY_POOL`；证明不足记录 `UNKNOWN_EMPTY_POOL_PROOF_REQUIRED` 并使本批 ledger 保持有未知。两种空池都不编造价格后验产品或零收益。

失败、进程中断及取消保留在原 jobs 与 job_events 中。显式 `retry(job_id)` 使用原冻结输入和截止，最多 3 次尝试；不暗中续采或扩大源认证预算。已成功原子提交之后的取消不能撤销已完成事实。

若上次进程崩溃，指定 job 可能仍停留于 `QUEUED / RUNNING / CANCEL_REQUESTED`。显式 retry 可以只恢复这个目标：必须由真实 JobService 持有同一数据库的未关闭 OS 所有者锁，目标不在当前进程执行集合、也不属于当前活跃 owner；额度先通过，再把目标记为 `INTERRUPTED` 或已请求取消的 `CANCELLED`，追加专门恢复事件，最后以原 payload 建立下一次尝试。不会调用全局恢复、修改其他任务或启动旧队列。

## 状态的含义

| 作业/ledger 状态 | 含义 |
| --- | --- |
| job `SUCCEEDED` | 本次批次事务提交成功；不代表数据全部成熟或策略有效 |
| ledger `COMPLETED` | 本批无剩余未来 H、也无未知；仍仅是描述性价格后验 |
| ledger `COMPLETED_PENDING_MATURITY` | 本次已完成应做部分，仍有未来 H 待成熟 |
| ledger `COMPLETED_WITH_UNKNOWNS` | 存在未知价格、资格、会话或日历；未知优先于待成熟状态 |
| job `FAILED / CANCELLED / INTERRUPTED` | 查看持久事件与安全错误码；没有本批成功 ledger |

ledger 保留 run 范围、输入、实际评价会话、首次截止、桥接身份、源码身份、各 H 状态、产品 revision、UNKNOWN 计数以及原件认证资源统计。`returns` 不填零，`market_edge=NOT_ESTABLISHED`，不产生 SAM episode、模拟成交、成本后收益或投资有效性结论。

本轮隔离 TEST_FIXTURE 的作业正确性测试不等于真实每日稳定性、实际市场数据齐备或生产调度已经启动；验收命令与结果在统一交付记录中单独列明。
