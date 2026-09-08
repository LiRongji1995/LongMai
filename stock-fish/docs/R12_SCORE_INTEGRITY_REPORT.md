# R12 Score Integrity

本轮保持 `DAILY_RECOMMENDATION_SCORE_V1`，实现修订为 `R12_SCORE_INTEGRITY`。工程修复不修改任何 Setup、交易阈值、推荐 gate、优先级或市场边际结论。`MARKET_EDGE=NOT_ESTABLISHED`。

## P0 修复

`recommendation_scoring._latest_labels` 在选择 revision 之前匹配目标 signal run、策略、观察日期、parent implementation/config/spec/source 和所需 horizon；字典 key 包含 run ID。parent source hash 覆盖阶段实现及配置依赖，显式 signal identity 标签也必须匹配。Outcome 排序、后续其他 run 的标签及仅报告时钟推进不改变原 run 的 Score 或随机 seed；同一时点 revision tie 由稳定 observation/content identity 决定。各 horizon 的 seed 独立，后来 D+3 成熟也不会重采样已完整 D+1。

回归明确使用 `601086.SH`：Run A / 2026-09-08 / D+1 +10%，Run B / 2026-09-09 / D+1 -10%，穷举四个 label（含 comparator）的输入排列，两个 run 的完整 score records 均分别与独立评价完全一致。另测 run、strategy、date、implementation、source、config、spec、horizon 各字段不匹配时拒绝标签。

## 身份与 countability

`validation_identity.py` 是 service 与 scoring 共用的防御式判定入口。epoch digest 绑定 stage implementation/spec/config/source、parent identity、protocol hash（含 validation_identity、scheduler、candidate_service 三个协议实现文件 bytes）、score spec identity（含 scorer bytes）和实际 cutoff policy。已保存 epoch 的完整身份须能重新匹配其 digest，才保留冻结协议/spec；仅有一个 epoch 字符串不能掩盖实际 signal identity 的改变。

若未来安装的 scorer bytes 与已冻结 signal 的 score spec identity 不一致，重新计算会拒绝并报告 `FROZEN_SCORE_SPEC_UNAVAILABLE_READ_ONLY_EPOCH`；旧 score 仍可只读查看，outcome 追加独立进行，不能用新 scorer 静默覆写旧 epoch。

任何重建历史 coverage/audit 身份优先于 official flags。official capture 仅表示一次真实 primary capture，official countable 还要求已持久化、输入完整性有效、完整日期化资格、eligible 集与实际评价行吻合、有效当前可比价格，以及真实 cutoff/latency。theme/funds UNKNOWN 可继续评价；核心 liquidity/trend/setup 须已知，或已因已知 liquidity/trend hard FAIL 确定 REJECTED。仅短历史导致核心 UNKNOWN 不是 valid no-focus；source failure 造成的 eligible=0 不可计数。

R12 采用 `SCHEDULED_2030_ACTUAL_COMPLETION`，不声称 exact 20:30。计数入口校验 scheduled/start/completed/actual 四个 aware clocks、上海 20:30 trigger、实际 decision cutoff 一致和 latency，拒绝虚假的 EXACT_2030、缺时区及时间先后矛盾。

## 汇总与展示

`N_A_SIGNAL_DATA_UNAVAILABLE` 与已真正评价完的 `N_A_NO_SELECTION` 区分。前者仅计入 operational/data-quality 统计，不进入 Focus coverage 分母或 Score rolling。保留四个统计：attempted primary days、evaluable official days、data unavailable days、valid no-focus days。数值 rolling 仅选择同 epoch 的 COMPLETE score 日。

Setup 分成 `PRIMARY_SETUP_COHORT` 与只读 `ALL_PASSING_SETUP:<setup>`；研究 cohort 可重叠，不影响正式推荐。rolling 分别计算 `median_daily_selection_return` 与 `median_daily_excess_return`。Monte Carlo 保存 sampling mode、K 及 percentile 标准误，SVG 主 Badge 仍为整数。

API 和离线报告默认当前 epoch，显式 epoch 参数只读查看旧 epoch，未知 epoch 返回空结果。既有五个 SVG 设计保留，并添加 epoch、evidence、实际 cutoff、data quality 和实现/spec 元数据；不可评价数据不会显示 NO FOCUS SIGNAL。

## 定向检查

工作目录：`stock-fish`；使用项目现有 `.venv/Scripts/python.exe`，独立临时 DB/state 与 TEST_FIXTURE，不接触真实策略记录。

```text
.venv\Scripts\python.exe -m pytest tests/r05_product/test_recommendation_scoring_v1.py tests/r05_product/test_recommendation_score_artifacts.py tests/r05_product/test_r12_score_integrity.py -q
67 passed, 0 failed, 0 skipped in 1.30s
```

覆盖用户 P0 清单：跨 run/order/future 隔离、malformed reconstructed、implementation epoch、rolling epoch、unavailable 与 no-selection、空 eligible source failure、有效 no-focus、实际 cutoff、双 setup cohort、median 命名，以及 MC uncertainty、API 当前/旧/未知 epoch 与 SVG metadata。全套工程测试与真实数据、scheduler、post-cutoff 验证由本轮总报告分别记录；上述合成工程测试不构成真实行情或投资验证。
