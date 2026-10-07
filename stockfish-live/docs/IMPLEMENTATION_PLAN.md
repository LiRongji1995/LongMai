# StockFish Live V1 实施与验收边界

当前已将 Phase 1 基础层扩展为独立研究工程流程。下表区分代码能力与仍需取得的真实证据；实际测试、退出码和运行性能由[验证记录](VALIDATION_V1.md)与[当前状态](../CURRENT_STATUS.md)登记，不把“已实现”写成“真实供应商或投资价值已验收”。

| 阶段 | 当前工程能力 | 验收边界 |
|---|---|---|
| 0 | 独立运行边界、仓库与许可审计 | 原审计记录保留其当时范围，新增模块由当前文档与测试说明 |
| 1 | 严格分钟合同、显式日历、raw/receipt/manifest、Parquet/DuckDB、doctor、单日/范围 replay | 日历身份和真实历史完整性不能由 fixture 证明 |
| 2 | V2 逐分钟增量与首次观察快照、V1 无状态参考，五指数、PIT context、宽度、Live 情绪、行业强度与边际变化 | 缺资格/成员/完整市场清单/成交额基准保留未知，特征版本/配置严格隔离，初始规则尚未验证 |
| 3 | 同证券同时刻 Historical Similarity、训练窗归一化、成熟实际 label、5 个 horizon 分位、regime、置信度、warning、追加 forecast/revision | K=50/100/200 为候选，经验分布和低样本概率不等于已校准投资预测 |
| 4 | 独立 FastAPI/WebSocket、本地 Dashboard、实际/未来路径、历史时间选择、top 10 相似状态 | 读取指定 run，不自动采集；浏览器和接口检查以验证记录为准 |
| 5 | expanding walk-forward 回顾诊断、内层 K 选择、5 类 baseline；原保存预测成熟后验；回归/概率/分位/coverage/calibration/confusion | 两类评价分别登记；不为原预测补造 baseline，真实/fixture/PIT 未证明分开；无真实历史为 NOT_EVALUATED |
| 6 | 显式标准 HTTP feed、实际本机收件时钟、同一 Collector/pipeline、EMPTY 父证据归档、受控 localhost HTTP 工程检查 | localhost 是 TEST_FIXTURE；未配置真实端点、未验证真实供应商授权、完整交易日 capture 或持续 SLA |

## 当前代码范围

- `contracts/{market,quality,context,forecast}.py`：版本化事实、上下文、feature state、成熟结果和 forecast。
- `providers/{replay,mock,demo,http_feed}.py`：归档重放、显式合成演示、显式 HTTP feed。
- `ingest/`、`storage/`：Collector、质量与时钟守卫、分钟/上下文/研究结果的独立不可覆盖保存。
- `features/`、`models/`、`forecast/`、`evaluation/`：流式/参考状态、规则、相似模型、baseline、revision、chronological 诊断与保存预测后验。
- `runtime.py`、`cli.py`、`api/`、`stockfish_live/frontend/`：盘前加载与标签来源证明、命令行、本地只读服务与打包界面。
- 独立 tests、docs；运行数据留私有目录，不发布合成生成物或真实分钟源。

Research 既有 Python/Qlib/SQLite、正式策略、账户安全断言和运行服务不变。不引入 broker 或订单。默认模型不依赖账户、LLM、GPU 或旧 Alpha158；不会自动购买服务或扩大采集预算。

## 持续自检

每次合同或实现变化先做对应独立 TEST_FIXTURE 检查，再检查完整回放、重复回放确定性、缺分钟/晚来源、非法 provider、不可覆盖持久化、API 时间选择与未来访问、dashboard 行为及 walk-forward 训练/测试边界。失败和未执行范围如实保留，不靠削弱断言、删样本或扩大 skip 过关。

合成 `demo` 默认逐分钟调用 MARKET_FEATURES_V2 流式引擎，保存首次观察的过去快照，正式 5 分钟决策再提取 HistoricalState；未来 label 经完整窗口成熟路径生成，不手写真实历史效果。research-config 的 forecast.feature_version 可明确选择 V1 无状态累计 prefix 参考，缺配置沿用 V1 兼容默认。版本与特征配置 hash 不混训，新数据集/run 独立，不升级旧 run。

Collector 使用 VISIBLE_BATCH_CHAIN_V1，V2 状态使用 APPEND_ONLY_DELTA_CONTEXT_CHAIN_V1；标签在启动/评价前核验完整 raw 父证据、未来路径、锚点/目标和 regime。snapshot.forecasts 只保存本轮决策 delta，latest_forecast_ids 追踪最近记录，全部原预测追加至私有 ResearchStore。初始参数、情绪规则、行业延续、warning 阈值、feature/config/model hash 必须可追溯。后续语义变化登记新版本/hash，不覆写旧 run。

## 真实研究与交付门槛

真实分钟来源首先证明证券身份、许可、业务日、源 publication 与本机 actual availability、父证据和覆盖。全市场宽度还需历史完整 inventory；行业依赖对应时点成员，不使用当前名单回填。下载成功、作业成功和 health 正常都不代表研究就绪。

有足够真实可比历史后，固定 chronological 样本范围、候选和 baseline，记录 OOS 与校准；再做独立长期前瞻验证。当前始终 `MARKET_EDGE=NOT_ESTABLISHED`，没有额外证据时 `NO_EVIDENCE_OF_INCREMENTAL_EDGE`。没有自动部署、服务切换或真实外部消息。

发布前核对实际 main 远端、dirty、文件差异，仅同步本次授权文件，保护用户修改。不创建分支/worktree、不改历史、不强推；按项目约定中文提交推送并核对远端 SHA。真实数据、凭据及私有运行产物不进入源码发布。
