# STAGE_AWARE_MOMENTUM_V2 主动核心阶段感知强势股策略

核对日期：2026-09-08（Asia/Shanghai）。

`STAGE_AWARE_MOMENTUM_V2` 是当前主动核心强势股策略。2026-09-08 本轮完成 `R10_DATA_REPLAY_PROSPECTIVE_ACTIVATION`，在 R09 语义修正基础上补齐真实 2026-09-07 数据、重建历史 replay 输入、并登记下一次 20:30 prospective primary 调度；不创建 V3、不新增长期并行策略臂。`STAGE_AWARE_MOMENTUM_V1` 保留为历史只读兼容和审计材料；旧 DAILY、PV、焦点、DNA、THEME 与 `THEME_CAPITAL_TREND_V1` 不改名、不重写历史。

## 身份与边界

- 策略 ID：`STAGE_AWARE_MOMENTUM_V2`。
- 实现版本：`R10_STAGE_AWARE_MOMENTUM_V2_DATA_REPLAY_PROSPECTIVE_ACTIVATION`。
- 规格修订：`R10_DATA_REPLAY_PROSPECTIVE_ACTIVATION`。
- 配置：[stage_aware_momentum_v2.yaml](../config/stage_aware_momentum_v2.yaml)。
- 实现：[stage_aware_momentum.py](../stockfish_core/yaogu/stage_aware_momentum.py)。
- Codex profile：`STAGE_AWARE_MOMENTUM_V2`。
- 实施与 replay 报告：[STAGE_AWARE_MOMENTUM_V2_IMPLEMENTATION_REPORT.md](STAGE_AWARE_MOMENTUM_V2_IMPLEMENTATION_REPORT.md)。
- 历史 alias：`STAGE_AWARE_MOMENTUM_V1` 只读保留。
- 参数状态：`INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED` / **尚未验证**。
- 市场优势：`MARKET_EDGE=NOT_ESTABLISHED`。

具体股票是否入选不是验收标准。国芳集团、金健米业等只能作为诊断样例，不能成为参数补丁目标。

## 分层决策

V2 先保存事实和中间判定，再给最终标签：

1. `trend_state`：只用慢趋势事实判断 `STRONG_UPTREND / TREND_DETERIORATING / TREND_BROKEN / UNKNOWN`。MA250 或斜率窗口缺失保持 `UNKNOWN`，不补零。
2. `setup_identification`：识别 `START / ACCELERATION / HIGH_LEVEL_REPAIR / TREND_PULLBACK`。旧 `HIGH_LEVEL_DIVERGENCE` 仅作为 `HIGH_LEVEL_REPAIR` 的历史 alias。
3. `behavioral_confirmation`：竞价弱转强、日内弱转强、修复时延、突破保持等分开保存；没有真实竞价 phase 或首封时间戳时保持 `UNKNOWN` 或 `NOT_EVALUATED`。
4. `market_compatibility`：涨跌停生态状态为 `EXPANSION / DIVERGENCE / REPAIR / RETREAT / UNKNOWN`。未知限价事件只降低 coverage，不按 0 个涨停、LOW 市场处理。
5. `same_theme_context`：板块和资金必须来自同一个已通过题材链。proxy 资金、未来窗口、缺父回执、冲突披露、重叠窗口不能成为正式资金通过。
6. `critical_counterevidence`：确认收盘跌停、趋势破位等硬反证会拒绝；单根上影线只作供应压力观察，反复未收复才进入 WATCH/UNKNOWN。

最终 `status` 使用产品决策标签：

- `FOCUS`：趋势、setup、行为确认、市场兼容、同题材板块/资金和关键反证全部通过。
- `WATCH`：核心形态有意义，但市场/行为/题材/资金或供应压力仍需验证，或高热度但当前动量转弱。
- `REJECTED`：已知硬失败或关键反证成立。
- `UNKNOWN`：核心事实不足，不能判断。

`formal_status` 为兼容字段：`FOCUS -> PASS`，`REJECTED -> FAIL`，`WATCH/UNKNOWN -> UNKNOWN`。

## 关键语义

- `START` 不是 `A>=1.2 + ret5>0`。它要求前期非扩张、当日突破或收复、成交额放大、当日相对强度和相对强度动量同步成立。
- `ACCELERATION` 必须同时满足高强度和当前继续抬升。旧 5 日强、当前 1/3 日动量或 RS 转弱时标记 `WEAKENING_HIGH_ACTIVITY`，进入 WATCH，不进入 FOCUS。
- `HIGH_LEVEL_REPAIR` 必须有高位背景、同日分歧和同日修复。修复只用当日 recovery、CLV、正收盘收益或当日 RS 支撑；不能用旧 3/5/10 日 RS 冒充今日修复。
- `TREND_PULLBACK` 必须在 `STRONG_UPTREND` 内，回踩 1-3 个会话、贴近 MA5/10/20、成交收缩，且没有明确相对弱势反证。
- 5 日高换手计数为最近五个会话含 T：`sessions[end_index-4:end_index+1]`；另存 prior5 excluding T 作为诊断，避免 6 日窗口错误。
- 排名只在 decision tier 与 setup 内按透明特征排序；跨 setup 仅是显示顺序，`rank_semantics=SETUP_AND_DECISION_TIER_RESEARCH_ORDER_NOT_GLOBAL_SCORE`。PV 参考分只作诊断，不作 V2 全局排名。

## R09/R10 完成点

- `Behavioral Confirmation = FAIL` 会阻止要求该行为的 setup 成为 FOCUS，但不把股票全局永久拒绝；UNKNOWN 仍可进入 WATCH/待确认。
- `ACCELERATION` 使用共享的等长窗口强度动量：最近 3 个交易会话的收益与 RS，分别减去前一个 3 会话窗口的收益与 RS；边际增强为 `RISING`，稳定为 `STABLE`，减弱为 `FALLING`。强度 level 和动量 momentum 分开保存。
- `WEAKENING_HIGH_ACTIVITY` 只属于 ACCELERATION 反证，不再污染 `HIGH_LEVEL_REPAIR`、`START` 或 `TREND_PULLBACK`。
- Sector 分为 Level、Momentum、Breadth、Stock Role 四维。Level 用 peer ret5 均值，不用目标股自身收益；Momentum 用 peer 等长窗口变化；Breadth 用 eligible peer 分母；Role 用收益、RS、成交额、换手、连续强度和涨停分位，不用日内时间戳作假领先。
- 每个 sector 窗口独立记录 expected/valid/coverage/used_as_pass_evidence，不能用一个窗口的完整性替另一个窗口背书。
- `HIGH_LEVEL_REPAIR` 的高位背景只取 T-1 或更早，T 日只用于分歧和修复判定。
- 慢趋势用 MA60/120/250 与斜率；MA5/10/20 只作为短期健康/回踩信息，缺短窗不会把长期趋势误写成 UNKNOWN。
- 长上影供给压力记录 `first_reclaim_date`、`ever_reclaimed`、`reclaimed_then_failed` 和 `unresolved_supply`，区分“从未收复”和“收复后再跌回”。
- 资金 `COMPLETE_NO_RECORD` 必须有完整查询证据；无记录但无完整父回执时仍是 UNKNOWN。
- tracking/outcome 增加 V2 setup-specific outcome：次日反转、修复时延、T+3 新高、突破保持/失败、供给吸收；这些只追加后验，不改 T 日信号。
- R10 进一步要求市场 `EXPANSION` 不能遮蔽跌停/炸板分歧压力；市场 momentum 只在前后 coverage 可比时给出，coverage 不可比时保持 `UNKNOWN`。
- Sector Level 明确拆成绝对 peer return 与相对全市场 RS；Sector Momentum 改为 matched peers 等长窗口差；Stock Role 输出 known/unknown feature coverage 和真实涨停/连板分位证据。
- 历史回放器读取 durable request-queue `results` 与实际 `raw/**/raw` archive，交易日历来自已捕获 provider calendar；交易所日历网页只作为可选审计附件。

## 2026-09-07 实测状态

本轮实际补取了 2026-09-07 BaoStock 日线并生成 `PROSPECTIVE_CAPTURED` 输入，派生目录为 `reports/codex/v2_semantic_completion_20260907_20260907T190336/`。输入 hash 为 `ac9666cc9d0ddc4f60a270828cadfc9aa37f940572148045f1b531b54c0ce0fd`，9.7 主板 bar 覆盖 4600 条，eligible 3041。V2 结果：FOCUS 0，WATCH 40，REJECTED 2887，UNKNOWN 114；下一目标交易会话为 2026-09-08。

当前 WATCH 清单不是正式推荐，更不是交易指令。它表示有形态或研究价值，但缺长窗趋势、PIT 题材、资金、市场兼容或行为确认等证据；完整清单见 `reports/codex/v2_semantic_completion_20260907_20260907T190336/V2_NEXT_SESSION_RESEARCH_LIST.md`。

长历史 readiness 以 V2 配置反推：MA250、斜率 warmup 和 60 个 replay signal sessions 需要 315 个交易会话。当前 9.7 prospective 输入中 MA5/10、RET1/3/5/10、RS1/3/5/10 已完整；MA20/60/120/250、长均线斜率、PIT 题材、资金、竞价与日内时间戳仍不足。R10 已用 BaoStock 官方文档、深交所主板代码区间公告和真实 cache receipt 组装 `RECONSTRUCTED_HISTORICAL` 输入，2026-09-07 单日 replay `success_count=1`，`PRICE_TREND_REPLAY_SCOPE` 成功 1 天，`FULL_CONTEXT_REPLAY_SCOPE` 因题材/资金缺失仍为 0。

Prospective validation 不从 9.7 手工补采倒填正式样本。当前真实 scheduler state 为 `ACTUAL_CANDIDATE_SCHEDULER_STATE_R10`，已登记 `schedule_2026-09-08_EVENING`，等待 2026-09-08 20:30 形成首个可计数 official primary；对应目标交易会话为 2026-09-09。

## Astra 评审对应修正

- V2 是主动核心策略，不再只是 V1 的 additive arm。
- 市场 benchmark 和限价生态都有覆盖率状态；低覆盖为 `INSUFFICIENT_COVERAGE/UNKNOWN`。
- 资金证据走父 source resolver，拒绝无 source_refs、未来窗口、late capture、proxy 自称 classified、重复冲突和重叠窗口。
- 题材和资金必须同链路通过；不可把 A 题材与 B 资金拼接。
- 无可见题材或证据不可授权时为 `UNKNOWN`，不当作已知失败。
- 行为确认不把收盘快照当竞价弱转强；时间领先无首封/首动时间戳时保持 `UNKNOWN`。
- 收盘跌停是关键反证；单根上影线不是硬拒绝。
- 顶层 `strategy_experiments.source_identity()` 纳入 V2/TCT 源码和配置哈希，避免下层语义变化但上层 run 身份不变。

## 复盘与后续

V2 尚未建立市场优势。后续每 5 个交易会话应做一次独立 review，比较 `PV_OBSERVATION_V1`、`PV_FOCUS_PREVIEW_V0.1`、`THEME_CAPITAL_TREND_V1` 和 `STAGE_AWARE_MOMENTUM_V2`，报告必须包含现象、数据、原因假设、反例、最小修改建议、可能新错误和后续验证计划。
