# 同股参与变化研究 V1

`COHORT_PARTICIPATION_TRENDS_V1` 是独立、纯数学的描述层。调用方先认证完整 saved/native 每日事实、同日身份、规则资格、原件时钟与日历，再显式调用；本模块不读行情、DB、网络，不构造来源能力，不运行 selector，也未接入 CLI、默认服务或正式选择规则。数学测试通过不代表历史资格、PIT 或投资有效性通过。

研究标识及特征版本：`COHORT_PARTICIPATION_TRENDS_V1`；实现版本：`COHORT_PARTICIPATION_TRENDS_V1_EXACT_MATH`；规格：`COHORT_PARTICIPATION_TRENDS_V1_SPEC_1`。`PARAMETER_STATUS=INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED`（尚未验证），`MARKET_EDGE=NOT_ESTABLISHED`。没有新增预测权重、阈值、排名或 FOCUS。

## 声明式输入与权限边界

入口为 `stockfish_core.yaogu.cohort_participation_trends`。`CohortRecord` 包含 `instrument_id`、`decision_status`、严格 bool 的 `research_selected`、CNY 单位的 `Fraction` 金额或 `None`。`DailySnapshot` 包含 `datetime.date` 业务日、完整 eligible 池的全部记录、非空 `comparison_basis` 声明、显式 `eligible_pool_complete=True` 和资料类型。它是普通不可变声明，不是资格/来源认证能力。

调用方必须把未知资格与 known excluded 的审计另存；这里的池外仅表示“不在本日声明的 eligible 池”，不推断 ST、退市、IPO、停牌或资格失败原因。本模块不能检测调用方漏报 eligible 行，因此完整性声明的可信度仍来自调用方的真实认证。

金额只接受 `Fraction` 或 `None`，拒绝 float、Decimal、int、bool 和负额。调用方从原始精确小数显式构造 `Fraction(Decimal(raw_literal))`，不得把已有 float 转回 Fraction 冒充原始精度。合法真实零金额与缺金额不同；空集合不产生零比率。

状态允许 `WATCH/FOCUS/REJECTED/UNKNOWN`。`research_selected=True` 只能伴随 WATCH/FOCUS；未知或拒绝被写成 selected 会拒绝输入。保存结果的 `UNKNOWN + False` 保留原 bool，但数学选择状态为 `None`，不当已知未选中。WATCH 状态和 selected bool 分别比较；WATCH→FOCUS 可以保留选择而退出 WATCH 子集，模块不会提升原决策。

日历是调用方显式提供的严格递增、无重复交易日列表；不按工作日推算。观察日必须在该日历内，快照必须严格递增且不重复；datetime 不自动丢弃时钟变成业务日。这里仅核对输入一致性，不认证日历原件。

`snapshot_content_hash` 对全部记录、日期、basis 与完整池声明作规范内容绑定。`source_identity()` 绑定本模块与本规格文件字节。即使声明 hash 或 basis 字符串相等，也没有重新核验 selector、原件、历史资格、PIT、实际 cutoff 或来源能力；JSON 保存/加载不能恢复 source capability。

## 两日比较

`compare_snapshots(previous, current, *, calendar, fixed_cohorts=None)` 保留两日全 IID 并集和各行状态。eligible 交集中的同股才进入 selected/WATCH 留存、新增、退出与 Jaccard：

| 统计 | 同股分母与含义 |
|---|---|
| 留存率 | 两日均为正 / 前日为正 |
| 新增率 | 前日已知负、当日正 / 当日为正 |
| 退出率 | 前日正、当日已知负 / 前日为正 |
| Jaccard | 两日均正 / 至少一日为正 |

资格池 entering/leaving 单列，不能计为 setup 新增/退出。共同 eligible 内任一决策未知，完整比率为 UNKNOWN，仍保留其余已知逐股转换，不能把已知子集比率伪装成完整总体比率。空分母为 UNKNOWN，不填 0；已知空 positive 集与未知观察保持区分。basis 声明改变时不授予跨规则转换，逐股事实保留，转换与金额增长 UNKNOWN。

跳过交易日的两日比较显式标为 `ENDPOINT_COMPARISON_ACROSS_MISSING_SESSIONS`，列出中间业务日。selected/WATCH 的 `transition_observation_role` 同时为 `ENDPOINT_MEMBERSHIP_CHANGE_NOT_CONTINUOUS_RETENTION`；相邻交易日则为 `ADJACENT_EXCHANGE_SESSION_MEMBERSHIP_CHANGE`。端点比率即便 AVAILABLE，也不能当连续每日留存。

默认固定金额 cohort 是两日共同 eligible IID。可通过 `fixed_cohorts={'frozen_cohort': unique_ids}` 追加事先固定的同股篮子；`common_eligible` 标签保留。重复 IID 拒绝，不把重叠主题中的同一股重复相加。多日入口会冻结 iterable，保证每个两日比较使用同一篮子。

固定 cohort 全部 IID 必须两日均 eligible、金额均已知且 basis 声明相等，才输出两个完整总额及 `当日总额/前日总额-1`。任何缺额/资格边界使完整总额与增长 UNKNOWN；另列 `known_both` 同股子集及其小计，明确不是完整 cohort。真实前日总额为 0 时总额可知、增长未定义。

每日变动 selected 池总额另列，不计算其跨日增长。举例：A 金额 100、B 金额 1000，两股金额均不变，但选择从 A 换成 B，每日候选总额会从 100 升至 1000；这不表示同股成交增长或净流入。交易成交额本身也不是净资金流。

## 连续观察与截断

`observed_selected_runs(snapshots, *, calendar)` 对所有曾在 eligible 池出现的 IID 保存首尾观察区间内每个真实交易日的状态。只有相邻实际交易日、同 basis、明确 selected 才累计观察到的连续出现天数。

- 缺整日快照：`SNAPSHOT_MISSING`，不是未选中。
- 当日池外：`ELIGIBILITY_BOUNDARY`，不猜具体资格原因。
- 行为未知：`DECISION_UNKNOWN`，不是已知退出。
- 规则声明改变：`COMPARISON_BASIS_BOUNDARY`，不能跨声明续接。
- 研究覆盖首尾：`OBSERVATION_BOUNDARY`，不猜观察前后状态。

以上边界分别保存并截断。已知未选中可以证明观察段的结束；缺证据只产生左右 censor。周五至下周一若日历相邻，可以连续，不按自然日计算。没有任何正观察且存在未知时，最长观察段为 UNKNOWN，不能填零持续时间。有正观察时最长段仅为已观察下界，永远不是完整 setup 持续时间、持仓长度或情绪周期认证。缺 May13 的 May12→May14 只能是跨缺日端点，不能推断持续或最长连选。

## 显式研究调用

```python
from datetime import date
from fractions import Fraction
from stockfish_core.yaogu.cohort_participation_trends import (
    CohortRecord, DailySnapshot, run_cohort_participation_trends, encode_exact,
)

# TEST_FIXTURE；真实研究须先由调用方完成来源与保存 run 验真。
calendar = (date(2026, 5, 14), date(2026, 5, 15))
snapshots = [
    DailySnapshot(day, (CohortRecord('TEST_FIXTURE_A', 'WATCH', True, amount),),
                  'TEST_FIXTURE_FROZEN_RULE_CNY', True, 'TEST_FIXTURE')
    for day, amount in zip(calendar, (Fraction(100), Fraction(120)))
]
result = run_cohort_participation_trends(snapshots, calendar=calendar)
json_ready = encode_exact(result)  # numerator/denominator，不舍入成 float
```

多日入口保存全部声明、内容 hash、相邻快照比较、连续观察和未知原因。调用方自行保存私有结果，本模块不写文件。留存/新增/退出、Jaccard 与 selected cohort 金额都受选择定义及阈值机械影响；观察长度也受资格范围和资料覆盖影响。它们只能提出待验证假设，不能直接称可预测信号、胜率、完整周期或投资优势。后续原生资格交集、主题日期成员、市场/席位来源和独立周期检验仍由原有认证路径完成。
