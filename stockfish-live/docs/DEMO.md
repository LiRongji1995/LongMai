# 明确标记的合成演示

`stockfish_live.providers.demo.create_demo(root, day, history_sessions=60, feature_version="MARKET_FEATURES_V2")` 创建新的独立目录。目标目录已经存在时拒绝覆盖。必须显式调用；真实 provider 缺失时不会自动切换到演示。CLI `demo` 默认采用 V2；程序接口可显式创建独立的 V1 参考数据集，不能原地转换已有目录。

演示包含 60 个历史合成会话和目标日，五个指数、三个股票流。日期为明确列出的连续日历日期，包含周末，**不是官方交易日历**。全部分钟、父回执、盘前上下文、历史 feature 和未来结果均固定为 `TEST_FIXTURE`。生成器使用固定 seed，六类走势依次循环；这是用于检查合同和界面的合成数据。

每个完整分钟有独立原始 payload 和父回执，`timestamp/source_timestamp/received_at/available_at` 均为该分钟完成时刻。原始分钟通过 `ParquetStore.append_many` 写入，不能把整日行情伪装为盘前已知。盘前上下文包含昨收、股票限价、资格、ST、大小盘、带版本的合成行业成员及合成日线环境。覆盖固定为 `DECLARED_SCOPE_ONLY`；三只股票不代表全市场。没有生成历史同时段成交额基准，相关字段保留未知。

V2 对每个历史会话的全部日历 tick 逐分钟调用 `StreamingMarketStateEngine`，只传本轮实际可见的 delta 和父回执；过去视图在首次观察时保存。正式 5 分钟决策时刻提取五个指数 `HistoricalState`，使用原 feature 合同桥接，未另建平行计算器。V1 参考生成则只在正式决策时刻调用无状态 `MarketStateEngine`，传当时完整累计 prefix。两条路径配置与 hash 独立，不能混训。

未来结果在当日 15:00 通过 `mature_outcomes` 生成，要求对应窗口每个交易分钟与父来源完整可见；保留完整 future path。历史 feature 与 label 分别追加至 `ResearchStore`。目标日不预先生成 feature、label 或 forecast，由普通 replay 路径处理。启动和评价再次校验标签 raw 父来源、完整未来分钟路径、锚点、目标和 regime。

`demo-report.json` 记录实际生成数量、路径点数、每会话耗时与总耗时。生成途中失败时保留已有产物并写 `FAILED_DEMO`，不能冒充成功数据集。完整路径存在重复窗口，历史较长时会占用内存、磁盘和校验时间；生成器逐日提交 feature 与 outcome，避免把全部未来路径同时驻留内存。

生成的 `research-config.json` 为 LIVE_RESEARCH_CONFIG_V1，`forecast.feature_version` 与报告、训练 state 相同。普通 replay 按该配置选择引擎。缺配置的旧数据根保留 V1 默认；安装新版或创建新 V2 demo 不会升级旧 run。V2 研究 Collector 的输入链为 VISIBLE_BATCH_CHAIN_V1，状态输入链为 APPEND_ONLY_DELTA_CONTEXT_CHAIN_V1，latency 独立记录。

```sh
stockfish-live demo --date 2026-09-30 --history-sessions 60 --data-root /path/to/new-demo
stockfish-live replay --date 2026-09-30 --speed max --data-root /path/to/new-demo --output /path/to/new-run
stockfish-live serve --run-dir /path/to/new-run --host 127.0.0.1 --port 8765
stockfish-live evaluate --as-of 2026-09-30T16:00:00+08:00 --data-root /path/to/new-demo --run-dir /path/to/new-run
```

snapshot.forecasts 仅含本轮决策新增记录，latest_forecast_ids 追踪最近预测；完整分布保存在 run 的私有 ResearchStore。evaluate 同时登记重新生成预测的 W/F 回顾诊断和读取原保存分布的 saved_forecast_posterior，后者不重训、不改旧预测、不补造 baseline。运行产物不随源码提交。

默认 `K=50`，候选 `50/100/200`，初始参数仍为 `UNVALIDATED`。较小 fixture 用于测试流程；60 个合成会话也不证明模型有效。fixture 评价只能出现在 `synthetic_engineering_metrics`，真实投资评价保持 `NOT_EVALUATED`、`MARKET_EDGE=NOT_ESTABLISHED`。缺少真实长期 OOS 与独立前瞻验证，不得引用演示数字宣称预测优势。
