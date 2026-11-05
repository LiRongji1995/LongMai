# DAILY_RECOMMENDATION_SCORE_V1

核对日期：2026-09-08（Asia/Shanghai）。

`DAILY_RECOMMENDATION_SCORE_V1` 是 `STAGE_AWARE_MOMENTUM_V2` 的只读表现观察层。它读取已经保存的 V2 信号 run 和追加 outcome label revision，生成每日推荐成绩、Focus/Watch 对比、setup 拆分、20D/60D rolling 摘要与 SVG scorecard。它不是策略特征，不是调参损失函数，不是交易胜率，也不触发自动交易。

## 固定口径

- Score spec：`DAILY_RECOMMENDATION_SCORE_V1`。
- 实现版本：`R12_SCORE_INTEGRITY`。
- Outcome 身份：先严格匹配 `run_id / strategy_id / observation_date / implementation_version / implementation_source_hash / config_hash / spec_hash / requested horizon`，再按 `(run_id, instrument_id, horizon)` 选择截至报告时点的有效 revision。同一时点使用 observation ID/content digest 确定 tie，不依赖输入顺序。parent source hash 已包含阶段实现、配置和资格/可见性依赖；显式 signal identity 字段存在时也必须一致。
- 选择收益：`SELECTION_CLOSE_TO_CLOSE_EQUAL_WEIGHT_RETURN_V1`，使用 outcome metrics 中的 `close_reference_change`，按入选股票等权平均。
- Benchmark：`SAME_SIGNAL_DAY_FROZEN_ELIGIBLE_UNIVERSE_EQUAL_WEIGHT_V1`，同一 signal day 的冻结 V2 eligible universe 等权收益。
- 随机比较：`SAME_M_WITHOUT_REPLACEMENT_DETERMINISTIC_RANDOM_BASKETS_V1`，入选数量为 M 时，从同日 eligible universe 中无放回抽取同样 M 只；组合数不超过 50,000 时精确枚举，否则用 10,000 个确定性随机篮子。
- 分数：入选组合收益在 same-M random basket return 分布中的 percentile，范围 0-100。它衡量该日选股相对同日同规模随机篮子的选择质量，不是上涨概率。
- Monte Carlo 保存 `percentile_sampling_mode / K / percentile_mc_standard_error`。标准误使用每个篮子贡献 0、0.5（tie）、1 的样本方差除以 K 后开方，单位为 0-100 Score 分数点；exact enumeration 不伪造 Monte Carlo 标准误。主 Badge 继续显示整数。
- Rolling：`SIGNAL_DAY_EQUAL_WEIGHT_VALID_COMPLETE_SCORES_V1`，20D/60D 只对已有完整 score 的 signal day 等权平均；缺失日不补 0。
- Rolling 分别保存 `median_daily_selection_return` 与 `median_daily_excess_return`，后者不再错误命名为 selection return。收益与分数使用相同完整 score 日范围。

## 分层与状态

正式推荐成绩只看 `FOCUS_SCORE`。`WATCH_RESEARCH_SCORE`、`FOCUS:<setup>`、`WATCH:<setup>`、`WATCH:WEAKENING_HIGH_ACTIVITY_OBSERVATION`、`REJECTED` 和 `UNKNOWN` 都是研究或诊断 cohort，不能混同为正式推荐成绩。

`FOCUS:<setup>` 与 `WATCH:<setup>` 保留 `primary_stage == setup` 的 `PRIMARY_SETUP_COHORT`。新增 `ALL_PASSING_SETUP:<setup>`，凡对应 `stage_candidates[setup].status == PASS` 均纳入，与最终推荐层级和 precedence 无关。它仅用于研究；同一股票进入多个 all-passing cohort 是有意重叠，不修改正式名单。

证据类型分三类：

- `OFFICIAL_PROSPECTIVE`：必须同时满足 prospective coverage、primary role、EVENING、run/策略/日期绑定、真实 cutoff、输入与保存完整性、完整日期化资格和实际评价，并有新的 `official_validation_signal_countable=true`。单一旧 official flag 不足以升级证据类型。
- `RECONSTRUCTED_HISTORICAL`：历史重建输入只进入重建历史 score，不可计入 prospective validation。
- `PRELIMINARY_RESEARCH`：手工补采、预备研究或不可计数输入，只可作研究观察。

历史身份始终优先：run coverage、嵌套 prospective coverage 或 audit mode 任一表示重建历史时，都不能因为错误 official flag 变为正式 prospective。

常见状态：

- `COMPLETE`：选中 outcome 和 benchmark coverage 均满足口径，可以出 score。
- `N_A_NO_SELECTION`：合法完整 universe 已实际评价且 M=0，可显示 N/A，不记 0 分。可作为 focus coverage 的合格分母，但不进入 numerical Score rolling。
- `N_A_SIGNAL_DATA_UNAVAILABLE`：资格/当前价格/来源身份/输入完整性/实际评价/持久化或官方 cutoff 证据不足，不能解读为策略没有选出股票；从 focus coverage 分母及 rolling 排除。
- `NOT_MATURED`：D+1/D+3/D+5 等 outcome 尚未成熟。
- `PARTIAL_SELECTED_OUTCOME_INCOMPLETE`：选中股票 outcome 有缺口。
- `INSUFFICIENT_BENCHMARK_COVERAGE`：同日 eligible universe outcome 覆盖低于 98%。

`official_primary_capture` 与 `official_validation_signal_countable` 分开保存。theme/funds UNKNOWN 本身不使 signal 无法评价；core liquidity/trend/setup 仍须已知，或该股票已因已知 liquidity/trend hard FAIL 确定 REJECTED。仅六日行情导致核心长历史 UNKNOWN 不能成为 valid no-focus。source failure 导致 eligible=0 不能计数。合法全市场 eligible=0 必须有完整、日期化、已知排除的 universe evidence。汇总保存 `primary_capture_attempted_days / evaluable_official_signal_days / data_unavailable_primary_days / valid_no_focus_days`。

## Validation epoch 与 cutoff

每个 revision 保存 `validation_epoch_id / signal_implementation_version / signal_spec_revision / signal_config_hash / signal_source_hash`。epoch digest 同时绑定 stage identity、parent 实现/config/spec/source、validation protocol hash、score spec identity、primary cutoff policy。protocol hash 包含协议实现文件 bytes；score spec identity 包含 scorer 实现 bytes。更改这些语义身份会创建新 epoch，已保存 epoch 不覆写。

若安装的 scorer bytes 已不同于 signal 冻结时的 score spec，新计算报告 `FROZEN_SCORE_SPEC_UNAVAILABLE_READ_ONLY_EPOCH`，旧 Score 保持只读；outcome 仍可独立追加，不用当前 scorer 改写旧 epoch。

R12 使用 `SCHEDULED_2030_ACTUAL_COMPLETION`，protocol 为 `SCHEDULED_TRIGGER_20_30_ACTUAL_DECISION_CUTOFF`。当前 provider 在 trigger 后获取资料，因此保存 scheduled/start/completed/actual cutoff 和 latency；实际 20:38 就显示 20:38。旧缺少明确协议的 run 保留 `LEGACY_UNSPECIFIED`，不继承新协议。official 计数还会拒绝缺少 timezone、时序矛盾、虚假 exact cutoff 或不一致 latency。

默认 API、离线报告及 20D/60D history 只读取当前安装 identity 的 epoch。显式 `validation_epoch_id` 可只读查看旧 epoch；未知 epoch 返回空结果，不回退到旧实现。客户端不得合并 epoch。

## API 与页面

后端只返回已保存 score revision 和历史汇总：

- `GET /api/recommendation-scores/latest`
- `GET /api/recommendation-scores`
- `GET /api/recommendation-scores/<run_id>`

可选过滤：`tier`、`setup`、`horizon`、`evidence_type`、`validation_epoch_id`。API 不接受公式、阈值、随机篮子数量、coverage 门槛等覆盖参数；页面不得重新计算 score。

页面的“策略成绩”区读取 `/api/recommendation-scores/latest`，展示保存的 headline、Focus/Watch、setup heatmap、coverage 和 rolling 摘要。SVG/Markdown 离线交付由 `scripts/generate_recommendation_score_report.py` 生成，包括：

- `DAILY_RECOMMENDATION_SCORECARD.svg`
- `DAILY_SCORE_TREND.svg`
- `FOCUS_VS_WATCH.svg`
- `SETUP_SCORE_HEATMAP.svg`
- `SCORE_COVERAGE.svg`
- `score-summary.json`
- `score-report.md`

离线命令的 `--validation-epoch-id` 明确选择旧 epoch；省略时读取当前 epoch，latest scorecard 与两类 history 使用同一 epoch，不从其他 evidence type 补齐空结果。Scorecard 保留设计，增加 Validation Epoch、Evidence Type、Actual Signal Cutoff、Data Quality Status 和实现/spec 小型标注；data unavailable 不显示为 NO FOCUS SIGNAL。

## R11 历史记录与 R12 验证

R11 截止 2026-09-08 12:24 上海时间，数据库中没有已保存 `recommendation_score_revisions`。因此 latest score 为 `NO_SCORE_REVISION/N/A`，official prospective score days 为 0，reconstructed historical score days 为 0。交付 scorecard 位于 `stock-fish/reports/codex/r11_validation_scoring_20260908_20260908_122302/recommendation-score/`。


`MARKET_EDGE=NOT_ESTABLISHED` 继续成立。任何 score 历史都只能作为策略健康证据，不能自动改变 `STAGE_AWARE_MOMENTUM_V2` 的阈值、setup、资金/题材规则或调度行为。
