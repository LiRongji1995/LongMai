> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../../../CURRENT_STATUS.md)；原路径可在[归档索引](../../README.md)查找。

# STAGE_AWARE_MOMENTUM_V2 实施与轻量 replay 报告

核对日期：2026-09-07（Asia/Shanghai）。

## 变更结论

`STAGE_AWARE_MOMENTUM_V2` 已接管为主动核心阶段感知强势股策略。V1 保留为历史只读 alias，不重写旧 PV/FOCUS/DAILY/DNA/THEME/TCT 结果。V2 参数状态为 **尚未验证**，`MARKET_EDGE=NOT_ESTABLISHED`。

## Astra 评审对应状态

- V2 不再是 additive arm；`product_role=ACTIVE_DEFAULT_CORE_STRONG_STOCK_STRATEGY`。
- 慢趋势、setup、行为确认、市场兼容、同题材板块/资金、关键反证和最终决策分层保存。
- `HIGH_LEVEL_REPAIR` 取代旧 `HIGH_LEVEL_DIVERGENCE`，后者只作为 alias；同日修复不能用旧多日 RS 冒充。
- 5 日高换手计数改为最近五个会话含 T，并另存 prior5 excluding T。
- 市场 benchmark 低覆盖和限价事件低覆盖不再填 0 或标 LOW。
- 资金证据必须通过父 source resolver；proxy、未来窗口、缺 source_refs、披露冲突、重叠窗口均不能 formal pass。
- 板块与资金必须同一通过题材链，不跨主题拼接。
- 竞价弱转强、时间领先、后续反转/修复时延/创新高等没有信号期证据时为 `UNKNOWN` 或 `NOT_EVALUATED`。
- 收盘跌停作为关键反证；单根上影线不是硬拒绝。
- 顶层 `strategy_experiments.source_identity()` 纳入 V2/TCT 源码和配置哈希。

## 2026-09-04 保存输入 replay

输入：`reports/codex/pv_observation_20260904_rebuilt_20260907T091708/market-input.json`。该目录被 Git 忽略，原始行情不上传。

决策截止：`2026-09-07T09:17:11.036672+08:00`。

完整 `run_strategy_experiments` replay：

- `run_id`: `strategy_experiment_ca0528df2faa2e4f1310eb93`
- 顶层 `source_hash`: `1dcd873d64985d018030ec2d11dc3b897c19663aee6243a17b14772b96c80190`
- V2 配置文件 hash: `3e8cf12be77a364a9b8a57636225cad1ab313d89d9c810e1c14287fb33387970`
- comparison 策略包含：`PV_OBSERVATION_V1`、`DNA_RERANK_V1`、`THEME_CONTEXT_V1`、`THEME_CAPITAL_TREND_TECHNICAL_ONLY_V1`、`THEME_CAPITAL_TREND_V1`、`SIMPLE_TREND_TEMPLATE_REFERENCE_V1`、`STAGE_AWARE_MOMENTUM_V2`

结果：

- `strategy_id`: `STAGE_AWARE_MOMENTUM_V2`
- `status`: `INSUFFICIENT_EVIDENCE`
- eligible/evaluated: `3043 / 3043`
- `FOCUS`: `0`
- `WATCH`: `71`
- `REJECTED`: `2844`
- `UNKNOWN`: `128`
- `top_ids`: `[]`
- 1 日等权主板 benchmark：`COMPLETE`，coverage `1.0`，return `-0.0626%`
- visible theme count: `0`
- 资金正式证据：无可授权同题材资金链
- 长窗趋势：该重建输入缺 MA10/20/60/120/250 连续窗口，趋势层全部为 `UNKNOWN`

## 两只样例股诊断

国芳集团 `601086.SH`：

- 最终：`UNKNOWN`
- setup：`HIGH_LEVEL_REPAIR` 通过；`INTRADAY_WEAK_TO_STRONG` 从日线 OHLC 可确认
- 关键数值：`A=5.306857`，`ret1=3.6036%`，`ret3=25.3717%`，`ret5=51.6787%`，`CLV=0.683128`，`recovery=13.5954%`
- 缺口：长窗趋势 `UNKNOWN`、市场限价生态 coverage 不足、题材/资金无可授权证据
- 解释：不是 V2 FOCUS；属于“高位修复 setup 成立，但核心上下文证据不足”

金健米业 `600127.SH`：

- 最终：`WATCH`
- setup：`ACCELERATION` 标记 `WEAKENING_HIGH_ACTIVITY`，不允许进入 FOCUS
- 关键数值：`A=1.03101`，`ret1=-2.9203%`，`ret3=-7.0997%`，`ret5=4.503%`，`CLV=0.555556`，`recovery=7.1034%`
- 缺口：长窗趋势 `UNKNOWN`、市场/题材/资金证据不足、行为确认不足
- 解释：不是为了修补旧 A 门槛而入选；V2 将其作为高热度转弱观察，不作为确认加速

## 未解决事项

- 需要更完整的长窗可比价格、日期化题材成员、同题材可授权资金和限价事件 coverage，才能让 V2 产生更有意义的 `FOCUS`。
- 后续真实复盘尚未建立市场优势；每 5 个交易会话应按配置 review cadence 做一次 Astra/人工复核。
- 后续 outcome/tracking 只能评价后验事件，不能把未来反转、修复时延或创新高倒灌为信号期特征。
