# STAGE_AWARE_MOMENTUM_V2 主动核心阶段感知强势股策略

核对日期：2026-09-07（Asia/Shanghai）。

`STAGE_AWARE_MOMENTUM_V2` 是当前主动核心强势股策略。`STAGE_AWARE_MOMENTUM_V1` 保留为历史只读兼容和审计材料；旧 DAILY、PV、焦点、DNA、THEME 与 `THEME_CAPITAL_TREND_V1` 不改名、不重写历史。

## 身份与边界

- 策略 ID：`STAGE_AWARE_MOMENTUM_V2`。
- 实现版本：`R08_STAGE_AWARE_MOMENTUM_V2`。
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
- 排名只在 setup 内按透明特征排序；跨 setup 仅是显示顺序，`rank_semantics=DETERMINISTIC_DISPLAY_ORDER_NOT_GLOBAL_SCORE`。PV 参考分只作诊断，不作 V2 全局排名。

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
