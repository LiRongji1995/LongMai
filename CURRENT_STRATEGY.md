# 当前研究口径

本轮仅精简工程实现，不调整策略公式、权重、阈值或资格规则，不覆盖既有 run。以下参数均尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`。

| 保留能力 | 用途与边界 | 权威定义 |
|---|---|---|
| PV_OBSERVATION_V1 | 已确认的日常量价研究；实验结论与冻结主策略分开 | [实验配置](stock-fish/config/r05_strategy_experiments.yaml)及 `strategy_experiments.py` |
| STAGE_AWARE_MOMENTUM_V2 | 主动核心，区分事实、setup、行为确认、市场/题材/资金情境、关键反证，输出 FOCUS/WATCH/REJECTED/UNKNOWN | [V2规格](stock-fish/docs/STAGE_AWARE_MOMENTUM_V2.md)、[原配置](stock-fish/config/stage_aware_momentum_v2.yaml) |
| DAILY_V1 | 冻结五族主策略与可用性对照；不能用PV或R2替代其缺失结论 | [五族规格](stock-fish/STRATEGY_SPEC_V1.md)、[原配置](stock-fish/config/yaogu_v1.defaults.yaml) |
| SHORT_HORIZON_STRENGTH_R2 | 既有盘前流程的短线研究匹配；原R1数学为必要依赖，保留近期参与度和动态成交软参考 | [R2规格](stock-fish/docs/SHORT_HORIZON_STRENGTH_R2.md)、[R1规格](stock-fish/docs/SHORT_HORIZON_STRENGTH_R1.md)、[原R2配置](stock-fish/config/short_horizon_strength_r2.yaml) |
| 题材解释与后验 | 原来源情境、保存结果及成熟期限统计，不作为未经批准的新筛选门槛 | [题材规格](stock-fish/docs/THEME_EVOLUTION_R1.md)、[成绩口径](stock-fish/docs/DAILY_RECOMMENDATION_SCORE_V1.md) |

证券代码与交易所、日期化资格、业务日期、父来源实际可见时间和完整性先于价格/题材/资金字段。A/CLV 不作 V2 所有阶段统一硬门槛。未知限价事件不能当 FALSE，合法空与来源不全分别报告；缺后验不记零收益。

标签仅在其真实可见时间用于后验，不能回流决策。历史下载标明重建类型，不能冒充过去已取得的数据。历史名单用原结果只读查看，不能用当前规则重算后冒充当时名单。旧策略额外 arm 可在旧 JSON 中保留，当前入口不再运行淘汰实验。

源码身份会随重构变化；新运行记录新的实现身份。历史结果中的原配置、源 hash、日期和认证时间不改写。工程测试通过不证明投资有效。
