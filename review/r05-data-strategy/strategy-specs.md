# R05 策略规格

`DAILY_V1` 保持原五族主策略，不改名、不降级，参数状态仍为 `INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED`。

`PV_OBSERVATION_V1` 是量价观察控制组。它只使用当前可信主板普通股合格池内 T 与前 5 个交易日的完整同口径数据：RAW 成交额、同 basis 可比收盘、T 日 RAW high/low/reference_prev_close。公式为 `A=amount_t/mean(amount_t-5...t-1)`、`R=comparable_close_t/comparable_close_t-5-1`、`H=(high_t-low_t)/reference_prev_close_t`，入围条件 `A>=1.2 && R>0`，`S_PV=0.45*PR(A)+0.35*PR(R)+0.20*PR(H)`。PR 使用中位百分位，N=1 时为 50。

`DNA_RERANK_V1` 只对 S1 入围项复排。它要求历史涨停/触板事件和次日可比结果，输出 Wilson95 成功率下界与均值收缩；样本不足时标记 `INSUFFICIENT_COVERAGE`，不影响 S1。

`THEME_CONTEXT_V1` 只对 S1 入围项追加题材上下文。它要求冻结主题成员和同伴行情；没有完整主题时标记 `UNAVAILABLE`，不影响 S1。
