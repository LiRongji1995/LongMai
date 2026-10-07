# V1 模型规划与当前实现范围

Phase 1 **尚未实现模型、feature、regime、情绪、行业或预测**。当前 CLI/快照明确返回 `NOT_IMPLEMENTED_PHASE1`，不把合成行情展示成 forecast。

后续按[实施计划](IMPLEMENTATION_PLAN.md)交付。首个核心模型为可解释 Historical Similarity / Nearest Neighbour，K=50/100/200 仅为尚未验证的候选参数，经 expanding walk-forward 选择，禁止随机切分和在测试窗 fit 标准化。

## 冻结前必须明确的合同

| 版本 | 计划内容 |
|---|---|
| MARKET_FEATURES_V1 | 长/中期环境、昨日情绪、开盘 gap、开盘以来收益/高低/回撤/区间位置、同时段成交额比、宽度/中位收益、大小盘与指数相对强弱、行业强度 |
| MARKET_REGIME_V1 | TREND_UP / RANGE_STRONG / RANGE_NEUTRAL / HIGH_OPEN_FADE / WEAKENING / TREND_DOWN；定义、标签成熟时点和概率分母必须登记 |
| SENTIMENT_TIDE_V1 | WEAK / REPAIR / EXPANSION / STRONG / DIVERGENCE / RETREAT / UNKNOWN；结构化指标与规则阈值独立于 Research 情绪 |
| SECTOR_ENGINE_V1 | 优先申万一级 PIT 成员，收益/宽度/成交额比/相对强度及其边际变化、动量/离散度/相关性；当前成员不得回填历史 |
| SIMILARITY_MODEL_V1 | 仅训练窗归一化、同决策时间历史状态、距离、样本支持、top 10 解释与成熟未来标签 |
| INTRADAY_PATH_V1 | 从当前价出发，+30/+60 交易分钟、11:30、14:00、15:00 的 q10/q25/q50/q75/q90；过期 horizon 忽略 |

上述名称是计划保留的版本，**尚不是已冻结实现**。正式实现时同时登记完整 feature 时钟、availability、分母、缺失语义、训练窗、configuration hash 和模型 hash。新阈值必须新版本/hash，不改写旧预测。

分钟 feature 只能消费受控可见记录。日线环境使用开盘前实际可见数据；历史行业、ST、停牌、指数成分、涨停/炸板/连板与主题未证明 PIT 时输出 UNAVAILABLE 或仅作明确研究展示。Qlib 日线 QLIB_NORMALIZED 数据没有分钟来源时间证明，不能直接进入 VERIFIED_PIT 训练。

## 未来输出与风险

概率必须归一，分位必须单调；实际路径和未来概率 fan 分开。保存相似日/时刻/距离/环境/成熟结果，保留 HIGH/MEDIUM/LOW/INSUFFICIENT、样本量、距离、regime support、模型分歧、区间宽度、缺数据和 freshness。

预测每 5 分钟更新、盘前加载模型；默认 09:45 起正式预测，09:35/09:40 仅 LOW_CONFIDENCE_EARLY。午休与 horizon 采用显式交易分钟，不能随意自然时钟加法。

预测 revision 5/15/30 分钟与连续改善/恶化警告追加保存。INDEX_BREADTH_DIVERGENCE、SECTOR_LEADER_BREADTH_DIVERGENCE、LOW_VOLUME_RALLY、MODEL_LOW_CONFIDENCE、REGIME_DISAGREEMENT 等必须由已版本化规则产生。LightGBM 仅可选、独立评估，不作为 Phase 1 依赖。

只有经过后续真实 OOS 和独立长期前瞻验证才可能讨论统计预测价值。当前 `MARKET_EDGE=NOT_ESTABLISHED`；复杂模型未稳定优于 naive baseline 时必须报告 `NO_EVIDENCE_OF_INCREMENTAL_EDGE`。
