# R16-R1 工程边界与验收方法

本说明优先于 R16 Phase 0 的历史说明。交付状态由 post-commit Review Bundle 的逐问题矩阵和原始执行结果决定；源码中的说明不预先宣布全量验收通过。R16-R1 不开始 R17，不接入 Go 生产、不改策略公式、Setup、FOCUS/WATCH、排序、阈值或 official score。`MARKET_EDGE=NOT_ESTABLISHED`。

## Contract 与生产 oracle

`FEATURE_EVIDENCE_CONTRACT_V1` 的严格 schema revision 为 `FEATURE_EVIDENCE_SCHEMA_R16_R1`。ret1_pct 使用 percent 和生产 HALF_EVEN 4 位舍入；OHLC 8 位、MA 6 位。JSONL 和 manifest 是权威输入，CSV/Parquet 的字符串展示不代替无损 provenance。重复 JSON member、重复 logical key、单位/版本/identity 不匹配、value/value_json 冲突、非法数值和未知扩展字段均拒绝。数值容差不豁免 provenance。

KNOWN 要求完整、可验证且在 cutoff 前实际可见的证据；missing/null 保留 UNKNOWN。底层 limit 事实未知与明确的状态值 UNKNOWN 分开。manifest 绑定实际 calendar/source/feature spec。两侧可以不同的 builder/creation 元数据由契约显式列出，来源、时点和计算定义不在豁免内。

[`R16_R1_PRODUCTION_ORACLE.md`](R16_R1_PRODUCTION_ORACLE.md) 描述原生产 Python 函数调用、冻结 golden 和同一个 Python Strategy 的事实注入。当前切片为 OHLC、prev_close、comparable_price/basis、ret1_pct、MA20/60。exact rational 用于恢复生产内部未舍入值，展示值仍独立验证。有效干预必须改变实际消费或业务结果；负例不能以全 UNKNOWN 形式通过。basis 未明确的旧生产 return 与 MA 行为不一致，保留为不支持域负例；不将该域或整个 Go Core 宣称等价。

## 来源、历史与缓存

实际可见时间和 revision 比较采用 aware UTC instant，保留原始时区文本。resolver 为 `CANONICAL_RESOLVED_SOURCE_VIEW_V2_R16_R1_PIT_INSTANT`，派生版本为 `DERIVED_FEATURE_STORE_V2_FAMILY_SPECS_R16_R1_PIT_INSTANT`；旧 cache 保留，不以清空缓存或放松 fingerprint 制造命中。

同一次 canonical source view 复用 provenance，较低有效 revision 不再重复构造大 token；PIT/rejected-source 检查先执行，等时冲突仍保留。重叠窗口共享原投影 token 的精确序列化字节；窗口顺序和 dependency identity 不变。只释放本次 context 拥有的引用，不清空其他消费者保留的 view。

[`R16_R1_CORRECTED_HISTORY.md`](R16_R1_CORRECTED_HISTORY.md) 和全量 raw evidence 记录本地资产的真实 normalization。corrected T 保留原 T 的当前 RAW/资格及 provenance，只加入真实归一化的历史；corrected T+1 从该基底处理实际新增日。两者使用同一个实际资料截止，标记 `RECONSTRUCTED_HISTORICAL`。后来取得的材料不能冒充过去已经可见。旧坏输入和所有失败迭代保留。

参考分区完整性、schema 相容与 RAW/factor 数值独立证明分别执行。RAW-only 不升级 comparable；因子覆盖、证券片段完整性、修订/retraction、缺交易日均有独立拒绝/未知状态。全量审计与 readiness 分开，coverage=0 不等于全部历史不存在。

## 外层 trace

真实 `/api/daily-primary/run` 接收请求、后台 job、domain transaction commit 和后续真实结果 export/序列化关联同一 trace。202 只表示 REQUEST_ACCEPTED；成功序列化的结果 bytes 才产生 RESULT_READY。trace 持久化独立于不可变业务 run，通过 separate trace link 关联；记录业务成功但 telemetry 失败，不伪造全链路成功。并发/重复导出使用独立边界，不改写已完成 trace。

每个 span 有真实墙钟起止、单调时钟时长、父子 ID 和状态。缺测为 NOT_MEASURED/null，主动不执行为 SKIPPED+原因，失败不升级成功。父子时间不可简单相加。内部函数调用为 INTERNAL_ONLY，失去原请求关联的导出为 PARTIAL。进程重启后的未完成请求不能从数据库假造出完整内存 trace。

`r16_service_path_trace.py` 从实际 CLI 进入 Flask API 并等待持久化/导出。data-ready、E2E_OFFLINE_REPLAY 与 live-attempt 明确区分。隔离准备时间单列；live attempt 只允许单次有界当前日请求，失败原样记录。测试 client 的 response bytes 不测 TCP 送达、用户聊天调度和用户接收时间。

## 性能证据范围

同一 worker 驱动只读解出的 R16 算法和最终算法，源 archive 与每个已加载模块有 hash 绑定。cold 指新进程和空 derived store，未清操作系统页缓存；固定先 baseline 后 final，不能当冷机硬件实验。每版 cold 一次、warm 三次，报告 median/range，不报告 P95。source metadata、profile 落盘、store audit/checkpoint 不计入 controlled workload 时长。

RSS 同时提供全部 worker 后代范围的采样峰值及实际 workload 区间的采样峰值，另记录单进程 native peak。Node dbstat 的内存不冒充 Strategy workload 内存。SQLite 记录 DB/WAL/SHM/checkpoint、UTF8 payload/dependency/identity bytes、物理表/索引页与 free pages；剩余差额为 UNATTRIBUTED。

corrected T→T+1 从确切 Day-T cache 副本起步；旧坏输入仅作 invalidation stress。小样本 profile 证明原字节/业务不变后才运行全市场，不扩展无限性能重构。本轮不预先承诺一分钟目标。

首次 post-commit 全市场外层运行在 source resolution 暴露 MemoryError，原失败 trace 和进程族 RSS 必须保留。修复将 250-instrument 分批边界前移到完整 source/dependency/lookup/build/write，而非先保留全市场来源再只分批计算。每个 instrument 仍使用完整 source windows、相同 PIT、fingerprint、payload 和 feature spec；跨批保留 resolver 证据集合及紧凑 runtime features。row/family lifecycle 按互不重叠的 instrument 求和，比例用总分子/分母，阶段时间为各批实际成本之和，total 独立测量外层墙钟；具体批次与耗时保留在 stats。旧成功构建和失败执行分别绑定其原 commit，执行代码改动后的受影响验收重新运行。

## 运行与只读审计

真实资产、DB、完整 provider cache 和 evidence 不提交 Git。`r16_r1_supervise.py` 提供明确预算、PID/create_time、后代 RSS、10 秒 checkpoint 和退出码；重连先检查 registry 及已观察进程身份。超时只处理本任务拥有且身份一致的进程，不按名称终止其他 Python。

prospective 审计只用 SQLite URI mode=ro/query_only，流式哈希大父 run 并保存必要 JSON1 投影及其 SQL，逐条保留 outcome 原文。实际 countable、各 horizon score/coverage、pending、registered/calculated epoch 与进程/端口分开。保存的计划不证明 live scheduler enabled/下一次实际执行。不得通过 GET candidate-schedule 或服务构造器进行只读审计，因为这些路径可能写计划/epoch。

最终命令见 [`REPRODUCE.md`](REPRODUCE.md)。最终源码冻结、commit/push、确认 clean 后生成外置验收和 bundle；页面端负责独立验收。
