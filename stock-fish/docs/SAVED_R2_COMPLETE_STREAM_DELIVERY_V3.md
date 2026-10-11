# 保存 R2 完整流式交付 V3

合同：`SAVED_R2_COMPLETE_STREAM_DELIVERY_V3`。特征：`SAVED_R2_COMPLETE_STREAM_DELIVERY_PROJECTION_V3`。来源清单：`SAVED_R2_DELIVERY_SOURCES_V2`。初始参数尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`；本组件不接默认策略、权重或服务，不认证过去可见 PIT、完整独立周期或投资优势。

`saved_r2_stream_delivery_v3.deliver_saved_r2_stream` 的具名参数为 `run_path, run_manifest_path, input_path, sources_manifest_path, output, context_cutoff, membership_mode='DATED_ONLY', as_of=None`。路径全部由调用方传入，输出必须新建。冻结运行清单沿用 `SAVED_R2_DELIVERY_RUN_MANIFEST_V1`，完整原池、所有行、原输入、证券名称、策略内层身份、原 WATCH 顺序及数量均由原 V2 验证器核验。组件不调用 selector、数据库、SDK、HTTP 或多策略作业，不重新选择证券。

## 原件发行

V1 三件来源继续经过原 `verify_capture` 和完整声明核验，语义不改。V2 清单严格含七项：`schema_version, captures, raw_receipt_sources, business_sources, counted_nodes, reported_scope, extra_registered_files`。`captures` 为原 V1 三件登记。`raw_receipt_sources` 每项严格为 `raw:{path,sha256}, receipt:{path,sha256}, typed_receipt:{SourceReceipt全部字段}`。仅接受 baostock 日期化 `market_daily` / `master_all`，参数严格为 `date`。任何缺失文件不由新 JSON 填充，也不合成 normalized result wrapper。

物理回执的原身份、整 RAW SHA、状态、分页、schema、请求日期和实际收件钟必须一致。物理 `row_count=null` 保留为原事实；本次另读整个 RAW 得到真实行数并与 typed 声明比对。原 coverage 的空证券/日期 scope 不扩写。本次 live reader/stream 从完整 RAW/master 重新检查同日证券全集、四 A 板范围、去重、adjustflag=3、交易状态、金额和价格语义。来源 SHA 是本地字节完整性，不是交易所或安全认证。物理回执没有完工钟时记录 UNKNOWN，不编造 completed_at；新发行时刻与原捕获四钟分别记录，publication 字段仍是原捕获可见界别名。

`issue_saved_r2_source_reader` 只返回本次持有的 `AuthenticatedSourceArtifactsV2`。其 receipts、id 映射、文件路径、完整 registry、业务/节点登记及实现身份都封印，并在消费前后再次验真。普通 JSON、gzip、deepcopy 或调用方 lambda 不能恢复该权限。迟到、不完整或缺失的合法父保留 UNKNOWN；真实 hash、内容、身份、请求范围或声明冲突必须拒绝。

## 原生 67 会话与完整交付

观察日以前原完整 calendar 的最后 67 个交易会话为唯一请求窗；不删缺日、不补邻日、不以 22 日替代。少于 67 个真实会话时保留实际数量与不足原因。原 input/themes 不修改；CURRENT_COHORT_BACKVIEW 的成员由 genuine reader 当次原件派生，明确不是历史有效成员。DATED_ONLY 缺原 refs、迟到、PARTIAL 或分页不完整时逐题材 UNKNOWN。

实际链路为 `SourceArtifactReader → SourceMarketStream.from_reader → run_theme_evolution_stream(output_history=True, history_scope='LATEST_67') → build_theme_observation_diagnostics`。每次最多一个 stream、一个原生 producer；没有可用题材或原 WATCH 为空时 producer 为零。原生父绑定检查 exact 对象、原输入摘要、cutoff、stream descriptor、原来源 registry、完整 sealed evidence journal 和实现身份；不接受序列化父、deepcopy 或外部 verifier 授权。完整 WATCH 以原模型行和类内原梯度交付，主归属唯一，次关系另列，跨方向强弱 UNKNOWN，不加 FOCUS。

同原 V2：原 run 在 as_of 后尚不可见时仅保存状态，不泄露 ID/数量/导出；原 run 可见而新解释缺失时仍交付全部原 WATCH 与 UNKNOWN。仅精确 `ECONOMIC_DISPLAY_ARCHIVE_CHANGED` 可以在原来源重新核验后降级为纯模型交付，新题材/业务值留 NON_DELIVERY_AUDIT；其它内容/身份错误硬失败。展示返回的全部文件 SHA、长度和实际 consumed_archives 必须绑定本次写出的真实附件和分组。

## 资源及验收边界

整原件单份上限 8 MiB、原输入/保存运行单份 256 MiB、合计源读取 2 GiB。每个派生文件上限 256 MiB，整个输出树（包括 evidence journal、NON_DELIVERY_AUDIT、正式 CSV/JSON/TXT、两份投影和 manifest）合计 512 MiB，保留控制记录空间。阶段末端及每次写入检查实际输出树，调用方的执行 wrapper 还须在原生 producer 写 journal 期间实时观测并终止超界作业；不能将事后检查称 OS 硬限制。失败产物保留，不自动重试。

派生 JSON 使用紧凑格式保存全部结构和值，减少同内容双投影的额外字节；原正式 CSV 与 observation.json 字节保持不变。预算按实际完整输出字节核验，不能以压缩估计或旧产物代替当次测量。

工程 TEST_FIXTURE、真实来源/交付验收、历史 PIT 和投资验证分开报告。普通归档只提供追溯，不重新发行 capability。缺历史成员、限价、资金/席位或市场覆盖保持 UNKNOWN，不等同无题材、可成交或合法 EMPTY。完整原 UNKNOWN 计数和原因照原 run 保留，但不把 UNKNOWN 全部作为候选推荐。
