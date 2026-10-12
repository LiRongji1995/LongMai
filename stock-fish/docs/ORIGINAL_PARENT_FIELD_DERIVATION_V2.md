# 原父可见、后来逐字段派生 V2

独立工程研究入口，未接默认服务或CLI。初始参数尚未验证，
`MARKET_EDGE=NOT_ESTABLISHED`；不修改旧正常化器、resolver、R1/R2公式或旧run。

## 入口与真实权限

```python
run_original_parent_field_r2(
    artifacts, observation_date, original_parent_as_of,
    registered_worker_deadline, *, requested_target_date=None, limits=None,
)
```

`artifacts`只能是原`OriginalCaptureArtifact`精确类型的locator，包含实际归档根、
result路径及预期整件SHA；locator不是来源能力。调用方不能传bundle、reader、
价格覆盖、selector或validator回调。函数重新调用既有
`normalize_original_parent_asof`，保留其真实三件鉴权、字段一致性、原钟和祖先
检查。所有成功、PARTIAL和失败结果的原件仍硬绑定并终态核对，不补造缺结果。
完整attempt库存不能从一组locator自行推断，调用方须另完成库存闭合审计。

工厂内部重新读取已认证whole RAW，通过真正`SourceArtifactReader`和共享
`comparable_rows`、`factor_series`、`_turn`构造字段证明。它没有普通JSON恢复入口。
唯一owner封印实例identity、PID、job、精确旧截止、input digest、field/audit hash、
完整来源registry、reader receipt/path映射、实现和消费状态。copy、deepcopy、
token重建、修改字段/父状态/路径、重复发行或消费均拒绝。失败占用一次尝试。
消费前后完整原件终读，所有原PARTIAL仍为PARTIAL；不修改require_complete。
实现身份包括原鉴权器完整依赖及原生R2完整配置/规格/情境依赖，另封印实际
加载的原生配置身份；发行后漂移拒绝。
原鉴权器已接受的零行FETCH_FAILED负包仍保留完整原件/receipt/audit/registry。
仅其负包schema标签默认被认可，不产生RAW或因子值；非零或不一致负包拒绝。

## 数值父和审计祖先

来源版`ORIGINAL_PARENT_FIELD_DERIVATION_V2`，特征版
`FACTOR_AUDIT_SEPARATED_FIELDS_V1`，与原R2数值身份分别保存。

| 字段 | 数值依据 | 未知的处理 |
|---|---|---|
| RAW OHLC、前收、成交量、成交额 | 相同IID/会话的真实COMPLETE RAW父，adjustflag=3；与原正常化值exact一致 | 不完整或RAW首见钟缺失时UNKNOWN，不填0 |
| 换手 | 原RAW的turn及原`_turn`精确百分比/100、float round-trip门禁 | 缺值、非法值或不可精确表示时UNKNOWN |
| 可比OHLC和basis | whole COMPLETE因子完整请求、逐股游标/行数、真实RAW钟及自足锚窗；与全部attempt oracle exact一致 | 缺锚/缺覆盖/缺钟保持UNKNOWN；不回退RAW冒充可比价 |
| 限价 | 本版本没有独立字段权限 | UNKNOWN，不继承混合来源的已验证标签 |

全部PARTIAL中已完整片段仍进入冲突oracle；不同值、遗漏旧action或插入修订
拒绝。whole COMPLETE数值父须自足，不能仅因retry label而通过。
数值计算只使用按真实原首见排序的最后一份具备RAW首见钟的whole COMPLETE
快照；不能拼接多个窗或从PARTIAL借用左锚。它必须独立覆盖该RAW会话，并与
保留全部attempt的oracle在值及basis上exact一致。缺锚行仍UNKNOWN。
每个可比OHLC还须通过`Fraction(可比价)==Fraction(RAW)*Fraction(因子)`，
两次相同Decimal舍入不是exact证据；无法精确往返的乘积拒绝。
继承片段仍须
同provider、请求scope、片段声明和原行多重集一致，祖先started ≤ captured ≤
first_seen ≤ ingested ≤ completed ≤ retry started。复用既有严格祖先实现，不能
把缺原件、缺scope或倒序时钟吞为正常缺解释。祖先RAW缺钟的旧UNKNOWN保留；
它不因此成为数值父，也不升级为历史PIT。final COMPLETE RAW缺首见钟不授予
可比价数值权限。

新DailyBar的source_refs只登记此次真正使用的数值父。审计父没有被删除：完整
registry、original_source_audit、field_bindings.audit_dependency_refs和ancestor
关系均封印。若可比价未知，真实RAW金额/换手仍可独立可见，而R1价格窗仍未知。
原R2整体决策继续由未改公式得出，参与度可用不等于整体入选。

## 时间、历史store与研究边界

原父四钟和状态不改；派生first_seen/ingested是真实今天。native用明确旧父截止，
经原RECONSTRUCTED_HISTORICAL/prior_source_captured通路读取，不手写时间proof。
publication仅原父可见上界，不是供应商独立发布时间证据。任何原父晚旧截止
拒绝；后来取得资料不得偷偷加入旧features。

目标日只查原input轴；没有Oct9的旧日历时单列UNKNOWN，不延长日历、不把观察日
合法计算阻断或改为新截止。实际发行、派生、终态时间与旧来源截止分别输出。

本工厂仅支持原三件构造的inline input，不发行原42a2/315历史store能力，
`history_artifact_ref`被拒绝。真实历史owner合并尚需独立官方payload/PIT/basis
消费路径，不能用保存JSON、315 index验证或新input引用冒充已完成该工作。
资格沿旧正常化器的原语义；当前目录不变成全年度历史board/ST资格证明。

返回archive仅为数据，不可恢复权限；最多一次normalizer、一次未改原生R2，
不采集、不联网、不打开DB、不运行多策略suite或题材producer。

`DerivationLimits.total_source_bytes`沿原实现限制唯一原件的size之和，
不是累计物理读取字节预算。新registry、reader和终态鉴权会额外完整读取，
本API没有累计IO计量或RSS采样，返回相应指标为`None`，不估算已读字节或峰值。
绝对worker deadline由既有真实定时器守卫；normalizer另有自身wall限制。
真实全市场消费者必须另登记并观测累计physical_read、OS/tree RSS及总wall守卫；
本fixture测试不替代这些资源认证。

`historical_PIT_count=0`、`complete_independent_cycles=0`、投资优势未建立。
TEST_FIXTURE只证明工程边界，真实Oct9全池消费和收益验证未由本规格认证。
