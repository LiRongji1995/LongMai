# V1 模型、预测与评价

当前核心为可解释 `SIMILARITY_MODEL_V1`，工程已实现 feature、成熟 label、历史相似、分位 forecast、revision、baseline 和 expanding walk-forward。它仍是初始研究模型：`parameters_status=UNVALIDATED`、`MARKET_EDGE=NOT_ESTABLISHED`。本机当前只有合成资料，没有真实长期 OOS 或独立前瞻证据。

## 版本与输入

| 版本 | 当前语义 |
|---|---|
| `MARKET_FEATURES_V2` | 新演示主路径；流式分钟增量、首次观察的过去快照，指数/宽度/Live 情绪/行业事实及相应边际变化 |
| `MARKET_FEATURES_V1` | 无状态累计 prefix 参考兼容；相同事实字段和单位，保留原过去视图与输入 hash 语义 |
| `SENTIMENT_TIDE_V1` | `WEAK / REPAIR / EXPANSION / STRONG / DIVERGENCE / RETREAT / UNKNOWN` 的结构化初始规则，独立于 Research |
| `SECTOR_ENGINE_V1` | PIT 申万一级版本与成员、收益/宽度/成交额比/相对强度、5/15/30分钟变化、动量/离散度/相关性 |
| `SIMILARITY_STATE_V1` | 证券、决策时点、实际可见时点、当前价、feature_version/features、context、警告和 PIT 等级 |
| `MATURE_OUTCOME_V1` | 独立的对应 horizon 实际结果、实际成熟/可见时点、当前价锚点、目标价、完整未来路径及父来源 |
| `MARKET_REGIME_V1` | 成熟未来窗口的六类规则标签；不是当时已知的未来事实 |
| `SIMILARITY_CONFIG_V1` | K、候选、最小样本、feature 选择、距离/区间/freshness 初始参数与 hash |
| `INTRADAY_PATH_V1` | 各 horizon 五分位、方向/六类 regime 经验概率、支持、相似解释、置信和 lineage |

`research-config.json` 中 `forecast.feature_version` 选择特征路径；新 demo 默认 V2，配置缺失时保留 V1 兼容默认。StreamingMarketStateEngine V2 逐分钟消费已接受 delta，以首次观察的 cutoff 保存过去视图，不因后来的 context 重算过去；MarketStateEngine V1 以当时完整累计 prefix 进行参考计算。V2 输出 `MARKET_STATE_V2`，其滚动与过去视图语义参与特征配置 hash；切换必须生成独立数据集与 run，不自动升级旧材料。

两条路径只消费当时可见的完整 RAW 分钟和父来源。盘前日线、昨收、限价、资格/ST/大小、历史行业成员、同时段成交额基准由带来源与实际可见时间的 ContextBundle 提供。缺项为 `None/UNAVAILABLE/UNKNOWN`；不把不存在的涨停/资格证据当 FALSE。feature 中 `_pct` 为百分数单位（1.0 表示 1%）；forecast/outcome 的 return、区间宽度为小数收益（0.01 表示 1%）。

## 相似历史与训练窗

对某证券某个决策时刻，仅匹配同证券、同上海时区 HH:MM、相同 feature_version 与特征配置 hash、更早业务日的历史 state。state 和 label 的实际 available_at 都必须不晚于本次 as_of，label 已完整成熟，证券、决策、锚点和 horizon 目标一致。未来未可见 label 的实际值与路径不参与归一化、距离、hash 或解释。历史下载不会把 availability 回拨。

盘前加载与评价先检查 label 的不可变 raw/receipt、完整未来分钟路径、source_ids、父可见时间、原 state 价格锚点、目标价/horizon 和 regime。仅有研究表合法 hash 不能跳过来源证明。特征配置、模型 ForecastConfig、Collector 输入链、状态输入链和各 horizon model_hash 分别留存；V2 输入链为 VISIBLE_BATCH_CHAIN_V1 与 APPEND_ONLY_DELTA_CONTEXT_CHAIN_V1，不能与 V1 完整输入 hash 混同。

每个 horizon 单独使用有成熟 label 的合格训练样本。默认 feature 维度为 query 中非空字段的排序列表；可以显式配置 `feature_names`。缺任何所选维度的训练 state 被排除，query 缺显式要求字段则不给分布。不会把缺值填零。所选维度、训练 state ID、均值、尺度、配置与模型 hash 保留在输出中。

归一化均值与总体标准差仅 fit 本次训练窗；零标准差采用尺度 1。距离为所选维度标准化差异的均方根，取距离最小 K 个，相同距离按历史时刻与 state_id 稳定排序。K 默认 50，候选 50/100/200 均为尚未验证的初始参数；较小工程 fixture 可显式设置小 K/样本数，但必须 LOW 并提示 `SMALL_SAMPLE_CONFIGURATION`。无足够成熟样本为 `INSUFFICIENT`，分位和概率为空，不输出零收益。

当前模型是透明标准库实现；预加载/索引缓存用于减少重复验证和搜索，不改变 eligible universe、实际 cutoff 或训练窗。没有测试窗 fit、随机切分、GPU 或可选 LightGBM 依赖。

## horizon 与概率

默认 09:45 起每 5 分钟更新；09:35/09:40 仅通过 `--early-forecasts` 显式启用，带 `LOW_CONFIDENCE_EARLY`。午休不算交易分钟，+30/+60 跨午休按显式日历推进。目标包括 `30m`、`60m`、`11:30`、`14:00`、`15:00`；已经到期或当日剩余交易分钟不足的目标省略。

snapshot.forecasts 只包含本轮决策新增分布，非决策分钟为空，语义为 APPEND_ONLY_DECISION_DELTAS；latest_forecast_ids 引用各指数当时最近的保存预测。完整 ForecastResult 追加至 run 私有 ResearchStore，不因之后价格变化或新预测而覆写。

每个目标根据所选历史 return 经验分布给出 `q10/q25/q50/q75/q90`，使用排序后的线性插值，并从本次 current_price 换算绝对 price_quantiles。方向概率是所选实际 return 大于 0 的比例。各 horizon 分位是边际分布，连接成扇形不代表已经拟合联合路径或保证某条轨迹出现。实际路径与未来分位分别显示。

六类 regime 标签按“当前价锚点 + 该成熟窗口内未来 close”的范围与终点定义；不把单个未来窗口说成完整日行情。初始优先级为：

1. 已知开盘 gap >=0.3%，窗口收益为负：`HIGH_OPEN_FADE`。
2. 窗口收益 >=0.3% 且终点位于范围上方 30%：`TREND_UP`；<=-0.3% 且处于下方 30%：`TREND_DOWN`。
3. 其余窗口收益 >=0.1%：`RANGE_STRONG`；<=-0.1%：`WEAKENING`；其余：`RANGE_NEUTRAL`。

未知 regime label 不进分母；有效分母独立保留，六个概率和为 1。阈值是 `MARKET_REGIME_V1` 的初始假设，尚未验证。top 10 相似 state 保存距离、当时 context、已成熟 future_path 和真实可见时间，不能展示尚未可见的未来实际结果。

## 置信、warning 与修订

`HIGH/MEDIUM/LOW/INSUFFICIENT` 表示样本与数据支持程度，不是投资成功概率。样本量、候选不足、高距离、宽区间、未证明 PIT、缺 feature/标签、collector 质量和 freshness 都单列。fixture 和小样本配置保持 LOW；初始参数状态在输出中持续可见。距离和 q90-q10 区间阈值来自 config，不用新阈值改写旧 forecast。

`LIVE_WARNING_RULES_V1` 保存规则版本、配置 hash、证据和未验证标记。当前代码包括指数/宽度背离、低量上涨、板块龙头缺宽度、资料质量、模型低置信、regime 分歧、宽区间、高距离和连续 forecast 改善/恶化。无法证明的市场事件不凭空发警告。

`FORECAST_REVISION_V1` 比较 5/15/30 交易分钟前与当前对**同一个未来 15:00 目标价**的 q50。输出绝对预期价变化、已经发生的当前价变化，以及剩余收益 q50 变化；不会仅用 now-to-close 收益大小误判修订。连续改善/恶化要求三次连续 5 交易分钟同目标预期价一致变化，不能凭重叠 lookback 直接断言连续。

情绪下一状态和行业延续当前为受限辅助统计：仅基于上证指数 top 10 相似状态的已成熟 30 交易分钟 state 转移。情绪需相同起始情绪且目标为六类已知状态；行业需同分类版本与 sector_id，比较成熟相对强度，初始变化阈值为 0.1 个百分点。至少 10 个有效样本才给概率，固定 LOW；少于阈值、缺 target state 或缺行业结果为 UNAVAILABLE。不能将它称为经过独立验证的转移模型。

## expanding walk-forward

`VALIDATION_REPORT_V1` 按时间排序，扩展训练窗，对多个证券/决策时刻/horizon 产生 OOS 诊断。每个 inner validation prediction 仍仅 fit 更早的训练资料；K 选择只使用 outer decision 之前已成熟实际结果，各可行 K 在相同验证 state 上比较。候选不足或内层验证不足明确记录，不制造参数胜出。

W/F 使用 query 实际 available_at 作为推理 cutoff，保留 actual_as_of；实际 capture 必须仍在名义决策的同一分钟，否则记录 STALE_DECISION_CAPTURE。内层选择同样使用真实实际时钟并记录 outer_actual_as_of，不将秒/微秒延迟回拨到整点。此部分是当前配置下重新生成预测的回顾诊断，与原保存预测后验分开。

对照为 `ZERO_RETURN`、`CURRENT_TREND`（已观察 5分钟收益外推）、`TIME_OF_DAY_MEAN`、`PREVIOUS_DAY_PATH`（最近合格同时间历史路径）、`EMPIRICAL_PRIOR`。缺 baseline 或缺实际结果保持 unavailable，不填零。报告包含 MAE、RMSE、方向准确率、Spearman、方向 Brier/log loss/calibration、各分位 pinball loss、50%/80% coverage 与区间宽度、regime 多类 Brier/log loss/calibration/confusion。

真实 VERIFIED_PIT OOS、TEST_FIXTURE 工程诊断、PIT 未证明研究结果分开保存；按证券、时间与 horizon 独立分组。没有真实样本，`investment_evaluation=NOT_EVALUATED`、真实指标为空。即使存在真实 OOS 诊断也不能自动建立优势，仍为 `NO_EVIDENCE_OF_INCREMENTAL_EDGE`，没有交易成本/执行模型或独立长期前瞻验证。重叠 horizon 相关，不能把所有行当作独立实验扩充统计显著性。

当前不会自动全历史研究、升级模型或扩大预算。源码测试与合成演示只证明所检查的工程行为，真实来源覆盖、长期稳定性、校准与统计优势分别取得证据。


## 保存预测的正式成熟后验

`SAVED_FORECAST_EVALUATION_V1` / `evaluate_saved_forecasts` 与上述W/F回顾研究分开。它只读取已经过ResearchStore及父来源检查的原ForecastResult，用原分位/方向/regime概率对照已成熟实际结果；不调用模型、不重新选择K、不重算预测，也不改变旧forecast。CLI报告的`saved_forecast_posterior`同时保留原forecast_id、model_hash、configuration_hash、feature/input lineage、名义decision与实际as_of。

实际结果必须available_at不晚于评价as_of，证券/state/decision/anchor/target与原预测一致，state在原forecast时已可见。允许同一名义分钟内的真实秒级延迟；跨入下一分钟的预测单列STALE_DECISION_CAPTURE。原预测INSUFFICIENT、无state、缺label或结果未成熟均明确skipped，不填零；完全相同的重复回放forecast可去重，同ID冲突拒绝。

后验复用回归、方向概率、分位coverage/pinball及regime指标，真实/fixture/PIT未证明结果和证券/时刻/horizon分组分别保存。没有保存baseline时为NOT_EVALUATED_NO_SAVED_BASELINE，不补造对照。即使存在真实保存预测诊断，仍无自动投资优势结论；无真实样本为NOT_EVALUATED、NO_EVIDENCE_OF_INCREMENTAL_EDGE。
