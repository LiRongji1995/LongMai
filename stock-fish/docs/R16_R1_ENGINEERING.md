# R16-R1 工程边界与验收方法

本说明优先于 R16 Phase 0 的历史说明。交付状态由 post-commit Review Bundle 的逐问题矩阵和原始执行结果决定；源码中的说明不预先宣布全量验收通过。R16-R1 不开始 R17，不接入 Go 生产、不改策略公式、Setup、FOCUS/WATCH、排序、阈值或 official score。`MARKET_EDGE=NOT_ESTABLISHED`。

## Contract 与生产 oracle

`FEATURE_EVIDENCE_CONTRACT_V1` 的严格 schema revision 为 `FEATURE_EVIDENCE_SCHEMA_R16_R1`。ret1_pct 使用 percent 和生产 HALF_EVEN 4 位舍入；OHLC 8 位、MA 6 位。JSONL 和 manifest 是权威输入，CSV/Parquet 的字符串展示不代替无损 provenance。重复 JSON member、重复 logical key、单位/版本/identity 不匹配、value/value_json 冲突、非法数值和未知扩展字段均拒绝。数值容差不豁免 provenance。

KNOWN 要求完整、可验证且在 cutoff 前实际可见的证据；missing/null 保留 UNKNOWN。底层 limit 事实未知与明确的状态值 UNKNOWN 分开。manifest 绑定实际 calendar/source/feature spec。两侧可以不同的 builder/creation 元数据由契约显式列出，来源、时点和计算定义不在豁免内。

[`R16_R1_PRODUCTION_ORACLE.md`](R16_R1_PRODUCTION_ORACLE.md) 描述原生产 Python 函数调用、冻结 golden 和同一个 Python Strategy 的事实注入。当前切片为 OHLC、prev_close、comparable_price/basis、ret1_pct、MA20/60。exact rational 用于恢复生产内部未舍入值，展示值仍独立验证。有效干预必须改变实际消费或业务结果；负例不能以全 UNKNOWN 形式通过。basis 未明确的旧生产 return 与 MA 行为不一致，保留为不支持域负例；不将该域或整个 Go Core 宣称等价。

## 来源、历史与缓存

实际可见时间和 revision 比较采用 aware UTC instant，保留原始时区文本。resolver 为 `CANONICAL_RESOLVED_SOURCE_VIEW_V2_R16_R1_PIT_INSTANT`，派生版本为 `DERIVED_FEATURE_STORE_V2_FAMILY_SPECS_R16_R1_PIT_INSTANT`；旧 cache 保留，不以清空缓存或放松 fingerprint 制造命中。

同一次 canonical source view 复用 provenance，较低有效 revision 不再重复构造大 token；PIT/rejected-source 检查先执行，等时冲突仍保留。重叠窗口共享原投影 token 的精确序列化字节；窗口顺序和 dependency identity 不变。只释放本次 context 拥有的引用，不清空其他消费者保留的 view。

[`R16_R1_CORRECTED_HISTORY.md`](R16_R1_CORRECTED_HISTORY.md) 和全量 raw evidence 记录本地资产的真实 normalization。corrected T 保留原 T 的当前 RAW/资格及其原始来源；实际新增历史通过原 normalizer 构建。当前 comparable 不能无条件逐字段保留：已有完整新 factor capture 与旧 basis 冲突时，须在已证明的影响范围内降为 UNKNOWN，并携带旧 RAW/basis authority、新冲突 receipt 和真实依赖 clocks 的 typed review。corrected T+1 从经过该修订审查的基底处理实际新增日。两者使用同一个实际资料截止，标记 `RECONSTRUCTED_HISTORICAL`。后来取得的材料不能冒充过去已经可见。旧坏输入和所有失败迭代保留。

旧 77bcb67 的 base-basis proof 仅证明每个 row 按自身旧 refs 的 RAW/factor 算术成立，没有证明多个 complete capture 之间修订兼容；603083.SH 的真实冲突是该范围缺口的反例。旧 PASS 原件保留，不能作为新知识下 comparable 全量正确的证明。`factor_revision_review.py` 调用既有 `factor_series` 的 series-level refusal，不接受替代因子的数值；只处理 `DEFINITION:iid:` basis 或明确依赖同 provider 的 `adjustment_factors` 父来源。独立 Tushare/direct-capture basis 在 `outside_factor_scope` 原样保留。类型化 DailyBar review 把四个 `comparable_*` 价格和 `comparable_basis` 设为 null/None，RAW/资格和 event_time 不改，三个 revision clocks 取旧行与全部真实依赖的 max；无严格时点进展则拒绝。被替换的旧 inline 原文以原 clocks 保留到 history，使旧 cutoff 仍可选择。T envelope 仅按 report 的实际已选 key/hash 替换。最终受影响验收须在源码冻结后重跑，不能预写 PASS。

参考分区完整性、schema 相容、每行 RAW/factor 算术和跨 capture 修订相容证明分别执行。RAW-only 不升级 comparable；因子覆盖、证券片段完整性、修订/retraction、缺交易日均有独立拒绝/未知状态。全量审计与 readiness 分开，coverage=0 不等于全部历史不存在。

## 外层 trace

真实 `/api/daily-primary/run` 接收请求、后台 job、domain transaction commit 和后续真实结果 export/序列化关联同一 trace。202 只表示 REQUEST_ACCEPTED；成功序列化的结果 bytes 才产生 RESULT_READY。trace 持久化独立于不可变业务 run，通过 separate trace link 关联；记录业务成功但 telemetry 失败，不伪造全链路成功。并发/重复导出使用独立边界，不改写已完成 trace。

每个 span 有真实墙钟起止、单调时钟时长、父子 ID 和状态。缺测为 NOT_MEASURED/null，主动不执行为 SKIPPED+原因，失败不升级成功。父子时间不可简单相加。内部函数调用为 INTERNAL_ONLY，失去原请求关联的导出为 PARTIAL。进程重启后的未完成请求不能从数据库假造出完整内存 trace。

`r16_service_path_trace.py` 从实际 CLI 进入 Flask API 并等待持久化/导出。data-ready、E2E_OFFLINE_REPLAY、live-attempt 与 live-incremental 明确区分。新的 live-incremental 入口在同一根 trace 内执行一次新 baostock market_daily 请求、normalizer/history/input 注册及真实 API/job/export；只使用已验证的非 official 隔离基底与原始本地 master/factor，不扩大到全 DAG 或历史回填。旧身份 receipt 仅证明 prior input 的参考范围，严格模式在写 row 前将新日资格降 UNKNOWN；缺价交易日和 RECON 保留。eligible=0 时仍保存真实业务结果，同时明确非空 feature 路径未触达，不用 hash 变化冒充消费证明。有效当前身份的非空定向正例与已有 offline/data-ready 证据另列。

详见 [`R16_R1_LIVE_INCREMENTAL.md`](R16_R1_LIVE_INCREMENTAL.md)。新增 live 路径的真实验收待最终源码冻结后执行；已成功的旧 live-attempt 仅证明 provider 边界，缺编排不能称为 provider 失败。HTTP attempts、SDK operations 与调用次数分别记录，底层总网络请求不可测时为 null/NOT_MEASURED，预算不是实际计数。隔离准备时间单列；测试 client 的 response bytes 不测 TCP 送达、用户聊天调度和用户接收时间。

## 性能证据范围

同一 worker 驱动只读解出的 R16 算法和最终算法，源 archive 与每个已加载模块有 hash 绑定。cold 指新进程和空 derived store，未清操作系统页缓存；固定先 baseline 后 final，不能当冷机硬件实验。每版 cold 一次、warm 三次，报告 median/range，不报告 P95。source metadata、profile 落盘、store audit/checkpoint 不计入 controlled workload 时长。

RSS 同时提供全部 worker 后代范围的采样峰值及实际 workload 区间的采样峰值，另记录单进程 native peak。Node dbstat 的内存不冒充 Strategy workload 内存。SQLite 记录 DB/WAL/SHM/checkpoint、UTF8 payload/dependency/identity bytes、物理表/索引页与 free pages；剩余差额为 UNATTRIBUTED。

corrected T→T+1 从确切 Day-T cache 副本起步；旧坏输入仅作 invalidation stress。小样本 profile 证明原字节/业务不变后才运行全市场，不扩展无限性能重构。本轮不预先承诺一分钟目标。factor review 的 writer rectification 是修复成本，单独计量；普通 corrected T→T+1 和 live 增量必须从已 reviewed 的 corrected base 开始。live 的旧分区不变守卫保持严格，有旧分区变化就失败并要求先独立修复，不能把修复增长算正常新增日。

首次 post-commit 全市场外层运行在 source resolution 暴露 MemoryError，原失败 trace 和进程族 RSS 必须保留。修复将 250-instrument 分批边界前移到完整 source/dependency/lookup/build/write，而非先保留全市场来源再只分批计算。每个 instrument 仍使用完整 source windows、相同 PIT、fingerprint、payload 和 feature spec；跨批保留 resolver 证据集合及紧凑 runtime features。row/family lifecycle 按互不重叠的 instrument 求和，比例用总分子/分母，阶段时间为各批实际成本之和，total 独立测量外层墙钟；具体批次与耗时保留在 stats。旧成功构建和失败执行分别绑定其原 commit，执行代码改动后的受影响验收重新运行。

## 运行与只读审计

真实资产、DB、完整 provider cache 和 evidence 不提交 Git。`r16_r1_supervise.py` 提供明确预算、PID/create_time、后代 RSS、10 秒 checkpoint 和退出码；重连先检查 registry 及已观察进程身份。超时只处理本任务拥有且身份一致的进程，不按名称终止其他 Python。

prospective 审计只用 SQLite URI mode=ro/query_only，流式哈希大父 run 并保存必要 JSON1 投影及其 SQL，逐条保留 outcome 原文。实际 countable、各 horizon score/coverage、pending、registered/calculated epoch 与进程/端口分开。只读 score 汇总按 signal run、validation epoch、immutable revision group 选版，逐条核对 document/record 的 run 与 epoch 绑定；不同 epoch 不合并，父 run 的重算 epoch 即使没有 score 也须列 NO_SAVED_SCORE。SQL 投影保留 JSON missing 与显式 null 的区别，维持生产 dict.get fallback 的真实语义；旧未保留 presence 的重算结论须重跑。该修复只改变审计解释，不计算或改写 official score。保存的计划不证明 live scheduler enabled/下一次实际执行。不得通过 GET candidate-schedule 或服务构造器进行只读审计，因为这些路径可能写计划/epoch。

最终命令见 [`REPRODUCE.md`](REPRODUCE.md)。最终源码冻结、commit/push、确认 clean 后生成外置验收和 bundle；页面端负责独立验收。
