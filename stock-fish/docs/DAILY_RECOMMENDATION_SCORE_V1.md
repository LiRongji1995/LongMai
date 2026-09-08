# DAILY_RECOMMENDATION_SCORE_V1

核对日期：2026-09-08（Asia/Shanghai）。

`DAILY_RECOMMENDATION_SCORE_V1` 是 `STAGE_AWARE_MOMENTUM_V2` 的只读表现观察层。它读取已经保存的 V2 信号 run 和追加 outcome label revision，生成每日推荐成绩、Focus/Watch 对比、setup 拆分、20D/60D rolling 摘要与 SVG scorecard。它不是策略特征，不是调参损失函数，不是交易胜率，也不触发自动交易。

## 固定口径

- Score spec：`DAILY_RECOMMENDATION_SCORE_V1`。
- 实现版本：`R11_VALIDATION_SCORING`。
- 选择收益：`SELECTION_CLOSE_TO_CLOSE_EQUAL_WEIGHT_RETURN_V1`，使用 outcome metrics 中的 `close_reference_change`，按入选股票等权平均。
- Benchmark：`SAME_SIGNAL_DAY_FROZEN_ELIGIBLE_UNIVERSE_EQUAL_WEIGHT_V1`，同一 signal day 的冻结 V2 eligible universe 等权收益。
- 随机比较：`SAME_M_WITHOUT_REPLACEMENT_DETERMINISTIC_RANDOM_BASKETS_V1`，入选数量为 M 时，从同日 eligible universe 中无放回抽取同样 M 只；组合数不超过 50,000 时精确枚举，否则用 10,000 个确定性随机篮子。
- 分数：入选组合收益在 same-M random basket return 分布中的 percentile，范围 0-100。它衡量该日选股相对同日同规模随机篮子的选择质量，不是上涨概率。
- Rolling：`SIGNAL_DAY_EQUAL_WEIGHT_VALID_COMPLETE_SCORES_V1`，20D/60D 只对已有完整 score 的 signal day 等权平均；缺失日不补 0。

## 分层与状态

正式推荐成绩只看 `FOCUS_SCORE`。`WATCH_RESEARCH_SCORE`、`FOCUS:<setup>`、`WATCH:<setup>`、`WATCH:WEAKENING_HIGH_ACTIVITY_OBSERVATION`、`REJECTED` 和 `UNKNOWN` 都是研究或诊断 cohort，不能混同为正式推荐成绩。

证据类型分三类：

- `OFFICIAL_PROSPECTIVE`：run 的 `prospective_validation.official_primary_signal_countable` 为 true，才可进入正式 prospective score 历史。
- `RECONSTRUCTED_HISTORICAL`：历史重建输入只进入重建历史 score，不可计入 prospective validation。
- `PRELIMINARY_RESEARCH`：手工补采、预备研究或不可计数输入，只可作研究观察。

常见状态：

- `COMPLETE`：选中 outcome 和 benchmark coverage 均满足口径，可以出 score。
- `N_A_NO_SELECTION`：M=0，没有正式 FOCUS，可显示 N/A，不记 0 分。
- `NOT_MATURED`：D+1/D+3/D+5 等 outcome 尚未成熟。
- `PARTIAL_SELECTED_OUTCOME_INCOMPLETE`：选中股票 outcome 有缺口。
- `INSUFFICIENT_BENCHMARK_COVERAGE`：同日 eligible universe outcome 覆盖低于 98%。

## API 与页面

后端只返回已保存 score revision 和历史汇总：

- `GET /api/recommendation-scores/latest`
- `GET /api/recommendation-scores`
- `GET /api/recommendation-scores/<run_id>`

可选过滤：`tier`、`setup`、`horizon`、`evidence_type`。API 不接受公式、阈值、随机篮子数量、coverage 门槛等覆盖参数；页面不得重新计算 score。

页面的“策略成绩”区读取 `/api/recommendation-scores/latest`，展示保存的 headline、Focus/Watch、setup heatmap、coverage 和 rolling 摘要。SVG/Markdown 离线交付由 `scripts/generate_recommendation_score_report.py` 生成，包括：

- `DAILY_RECOMMENDATION_SCORECARD.svg`
- `DAILY_SCORE_TREND.svg`
- `FOCUS_VS_WATCH.svg`
- `SETUP_SCORE_HEATMAP.svg`
- `SCORE_COVERAGE.svg`
- `score-summary.json`
- `score-report.md`

## 当前 R11 状态

R11 截止 2026-09-08 12:24 上海时间，数据库中没有已保存 `recommendation_score_revisions`。因此 latest score 为 `NO_SCORE_REVISION/N/A`，official prospective score days 为 0，reconstructed historical score days 为 0。交付 scorecard 位于 `stock-fish/reports/codex/r11_validation_scoring_20260908_20260908_122302/recommendation-score/`。

`MARKET_EDGE=NOT_ESTABLISHED` 继续成立。任何 score 历史都只能作为策略健康证据，不能自动改变 `STAGE_AWARE_MOMENTUM_V2` 的阈值、setup、资金/题材规则或调度行为。
