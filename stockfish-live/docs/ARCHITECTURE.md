# StockFish Live 架构

Live 使用独立包、运行环境与私有数据根。当前工程覆盖分钟归档、状态计算、历史相似预测、Dashboard 和 chronological OOS 诊断；标准 HTTP feed 已有适配器，真实端点与生产运行尚未验收。[验证记录](VALIDATION_V1.md)描述实际运行证据，功能存在不等于真实数据就绪或模型有效。

```text
私有来源 raw + 实际 SourceReceipt
  -> 规范化 MinuteBar -> ParquetStore / DuckDBStore
  -> ReplayProvider 或显式 HttpFeedProvider
  -> Collector + QualityMonitor（身份/父来源/PIT/缺口/超时）
  -> MarketResearchPipeline
       -> StreamingMarketStateEngine V2（每分钟 delta、PIT context、首次观察快照）
          或 MarketStateEngine V1（兼容无状态累计 prefix 参考路径）
       -> ForecastEngine（历史同证券/同时刻、成熟标签、训练窗归一化）
       -> RevisionEngine + 版本化 warning
  -> immutable snapshots + 独立 latency + ResearchStore
  -> FastAPI / WebSocket / 本地 Dashboard（读取指定 run）

ContextStore -> 原始上下文/receipt/manifest -> 当时可见 context
ResearchStore(features/outcomes) -> 盘前加载受控历史 -> 预测
历史 state/outcome -> expanding walk-forward 回顾诊断
成熟实际 outcome + 原保存 forecast -> 独立保存预测后验
```

## 相同消费路径与独立边界

ReplayEngine 按明确交易日历推进 ReplayClock，live runner 使用实际时钟；两者经同一 Collector、QualityMonitor 和可选 MarketResearchPipeline。`--foundation-only` 显式选择冻结基础层；普通 replay 默认启用研究 pipeline。provider 只提供行情与来源，不实现策略。

启动研究 pipeline 时，`runtime.load_pipeline` 按已知盘前时点读取历史 feature/outcome，排除当前业务日的训练 state，保留实际 availability。`validate_history_provenance` 核验标签原始父来源、完整合法未来分钟路径、来源集合、实际时钟、锚点/目标价、horizon、regime 和最弱 PIT 等级；研究表的自洽 hash 不是实际标签证据。标签按完整分片校验后逐条完成来源证明，再进入无损压缩 PreparedHistory；只保留单分片 typed outcome 与单日 RAW 证明缓存。全部 feature 状态与压缩历史仍随档案规模增长，不能将其称为恒定总内存。上下文按当天有效区间缩小范围，盘中仍逐时点检查可见性；已经声明但尚未取得的修订不能提前消费。ContextStore、ResearchStore、provider.json 均位于私有数据根。

没有 `stockfish_core`、Research Flask、CandidateService、recommendation SQLite、scheduler 或 Qlib 运行依赖。运行不要求 GPU、LLM、Redis、Kafka 或微服务。新服务默认仅监听 `127.0.0.1:8765`，不会自动启动/停止/切换 Research；没有券商、订单或仓位模块。

## 状态和模型

`research-config.json` 的 `forecast.feature_version` 选择计算路径。新 `demo` 默认 `MARKET_FEATURES_V2`，StreamingMarketStateEngine 每分钟只消费 Collector 本轮接受的 delta，维护滚动统计与首次观察的过去视图。V2 的 5/15/30 分钟行业变化引用当时已观察快照，不用当前取得的资料重算过去。`MARKET_FEATURES_V1` 保留 MarketStateEngine 消费本会话累计 prefix 的无状态参考语义；无研究配置时沿用 V1 兼容默认。两者版本、配置和输入 hash 独立，旧数据集与 run 不自动升级。

身份/资产类别/RAW/父来源/完整分钟逐项验证；资格、ST、限价、昨收、日线、行业成员和同时段成交额基准由独立 ContextBundle 提供。宽度保留声明池分母、未知资格数量与独立完整市场状态。行业变化要求过去和现在成员一致，不能由当前成员回填历史。

ForecastEngine 以证券、决策时间、feature_version 和特征配置 hash 匹配更早会话，只使用当时实际可见、对应窗口已成熟的 label。每个 horizon 独立 fit 均值和尺度、计算距离及分位；午休由交易分钟日历处理。top 10 相似 state 保留距离、原环境和成熟未来实际路径。RevisionEngine 比较同一个未来收盘目标的绝对价格预期，明确分开已发生价格变化与剩余收益预测变化。

snapshot 的 `forecasts` 是 `APPEND_ONLY_DECISION_DELTAS`：仅本轮正式 5 分钟决策的新记录，非决策分钟为空。`latest_forecast_ids` 追踪各指数最近 forecast；完整记录追加至私有 ResearchStore，API 按请求时点解析已产生的预测。旧 forecast 及其分布不随后续 snapshot 重写。

情绪下一状态与行业延续概率是单独的初始规则：目前仅使用上证指数 top 10 相似状态中已成熟的 30 交易分钟转移，至少 10 个有效样本才给概率，置信度固定 LOW。它们不是已训练的独立概率模型；缺目标 state、标签或稳定行业版本时保持未知。

## 存储、完整性和恢复

分钟分片在 `minute/date=YYYY-MM-DD/part-<hash>.parquet`；父证据在 `sources/`。`append` 保存单父来源批次，`append_many` 保存单日多父来源 V2 分片，仍绑定每行原始 source_id、raw、receipt 与 manifest。单写者 OS lock、staging/fsync、禁止覆盖原子发布；最后发布 Parquet 作为提交点。中断可留下未被读者发现的辅助文件，不修改旧历史。

ContextStore 追加 hash 寻址 bundle，并核验所有 raw 父证据。ResearchStore 使用独立 `research/features`、`outcomes`、`forecasts` Parquet，含 event_time、available_at、identity、PIT 等级、payload/hash；评价保存为不可覆盖 JSON。feature、未来实际 outcome、forecast 不混成一个当时输入表。重复身份或内容冲突拒绝。

读取校验 schema、内容、manifest 和父来源。显式 EMPTY 响应也归档原 raw/receipt/manifest，且只有来源实际可见后才构成空响应事实，不升级为完整市场空集。重复检测缓存只在同一存储实例中复用已验证不可变分片的 metadata；文件 identity/stat 改变会重新完整检查。缓存优化不改变实际来源或可见时间。DuckDB 使用 `:memory:`、固定参数化 PIT 查询，不提供任意 SQL 或 Research 数据库连接。

研究 Collector 使用 `VISIBLE_BATCH_CHAIN_V1` 链式绑定已接受可见批次及父回执；V2 状态使用 `APPEND_ONLY_DELTA_CONTEXT_CHAIN_V1` 继续绑定实际 cutoff、增量和可见 context。V1 参考状态保留原完整输入 hash 语义。hash 算法版本参与配置隔离，不能把不同路径的 hash 当成相同输入证明；独立 latency 不进入特征 hash。

每个 run 必须新建目录，每个 tick 快照独占追加。summary 写成功后才表示运行完成；取消、异常或持久化失败保留 `FAILED`。重复 replay 独立目录并存，snapshot 内容 hash 排除非确定性 latency。

## API 与故障观测

FastAPI 读取指定已保存或进行中的 run，提供 status/health/market/indices/breadth/sentiment/sectors/forecast/history/similar-states/warnings 和 `/ws/live`。时间选择受保存 run 的可见边界约束；页面读取不会自动采集、训练或评价。实际路径与未来分位扇形分开呈现，fixture/PIT/质量限制持续可见。

provider timeout、非法返回、未来时钟、来源缺失、乱序与重复分别记录；失败批次不改变已接受状态。记录 event cutoff、as_of、input/config/model/snapshot hash、缺口、警告，以及 provider call、event-to-available、内部计算/持久化 latency。provider event-to-available 依赖源时钟，不等于网络往返或实时 SLA。

可信 loader 可以读完整档案，业务访问接口施加 PIT 约束；这不是进程/操作系统安全沙箱，不能阻止任意 Python 代码读取文件。全市场规模、跨平台原生运行、真实 provider 长期稳定性、统计 OOS 优势与独立前瞻验证须分别验收。

预测修订使用 Pipeline 私有的 PreparedRevisionHistory：新预测完整校验一次，后续只使用不可变标量摘要；公开 RevisionEngine.compare 对任意外部历史保留严格验证。原预测、完整未来路径与修订 hash 不变，摘要不替代正式保存档案。
