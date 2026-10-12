# 物理原件历史有限资格研究 V1

状态：独立工程研究入口；初始参数尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`。
不接入默认服务、CLI或数据库，不修改历史构建器、正常化器、R1公式和旧run。

## 唯一入口

```python
run_physical_historical_r1(
    sources_manifest_path, source_policy, observation_date,
    registered_worker_deadline, history_sessions=26,
)
```

`source_policy`必须为实际官方原件目录路径，不能是保存的policy JSON。
函数在主线程运行，必须传时区明确的未来绝对工作截止；调用方已有活动计时器
时直接拒绝、保留其handler和timer，不假设旧handler必定退出。运行环境另登记
OS/进程树RSS与输出预算，模块不声称提供硬内存隔离。
同一登记路径、观察日和截止的第二次调用拒绝；一次发行绑定只能消费R1一次。
实例由唯一factory登记，封印原对象identity、PID、登记job、精确决策截止和消费状态；
shallowcopy、deepcopy、相同token重新构造、改变截止或重置状态都不能再次消费。
失败占用一次尝试，不自动采集或重试。

来源清单严格沿`SAVED_R2_DELIVERY_SOURCES_V2`七项合同。日行情、同日目录允许
whole RAW加physical receipt；日历、stockbasic和因子须保留真实V1三件。
未知旧result wrapper不补造，不伪装成旧capture。来源SHA是本地完整性，不是
供应商签名、历史证券身份或历史PIT证明。保存JSON仅为归档，不能恢复能力。
没有外部bundle、reader、价格覆盖、策略覆盖或validator回调参数。

## 事实与身份

新版本为`PHYSICAL_ORIGINAL_HISTORICAL_R1_RESEARCH_V1`，特征为
`PHYSICAL_DATED_IDENTITY_AND_EXACT_PRICE_V1`。新来源身份与原R1数值身份分别保存。
实际再次读取全部注册原件，真发行`AuthenticatedSourceArtifactsV2`；消费前后
复核live reader、物理路径、receipt映射、完整字节及实现身份。派生input的typed
digest另绑定，普通JSON、lambda或deepcopy不授予权限。

完整真实日历定位观察日前26会话；缺日不压缩。每个可用会话调用现有四板
RAW/master验证：全代码范围、日期、adjustflag=3、交易状态和非交易空额语义。
来源缺失或合法PARTIAL会话留UNKNOWN；真实范围、价格或状态冲突拒绝。
当日RAW缺失但同日目录完整时保留真实目录的UNKNOWN身份行和预期数量，不能
把价格缺源造成的零合格误称合法空池。
完整同日目录缺失时直接拒绝本次计算，不能用历史目录或零数填补未知分母。

价格只为日期化SZSE `000001–004999`构造；同日证券目录、同日RAW、原IPO日期、
stockbasic type=1和2021-04-06合并原件共同支持有限资格。当前type只作原规范的
佐证，不代表已获得独立历史类型原件。SSE、创业板、科创及其他未证明身份均不
扩为合格；IPO缺失/冲突、ST/停牌/退市证据不足分别UNKNOWN。缺IPO不能编造
listed_on，记录逐IID身份缺口及universe expected_count，完整市场资格保持false。

## 复权与原钟

使用原`comparable_rows`/`factor_series`，不复制复权公式。先对全部真实逐股完整
片段（包括PARTIAL批次中原已完整片段）做冲突oracle，再以whole COMPLETE、完整
请求及自足锚窗作为数值父；value和basis必须精确一致。PARTIAL仍为PARTIAL，
不会成为新COMPLETE数值父。有效日之后的因子行仅从此次计算视图排除，原whole
行数、scope、hash和状态不改。遗漏旧action、新插入action或冲突保持UNKNOWN。

因子`security_statuses`须逐请求代码完整、行数精确、游标耗尽且真实捕获钟不晚于
原父。片段捕获钟早于本wrapper开始时，必须找到同次发行中真实认证祖先三件：
同code、同请求起止范围、同片段声明、原行多重集和完成钟均一致。缺祖先拒绝。
直接捕获祖先必须显式满足started ≤ fragment captured ≤ first_seen ≤ ingested
≤ completed ≤ 新wrapper started；继承中间retry不冒充片段捕获起点。
whole COMPLETE因子RAW缺原first_seen时不成为数值父，保留真实receipt、片段及
缺钟审计为UNKNOWN；不因旧认证允许fallback而补造RAW首见钟。
继承证明为本次审计关系，不重新赋予旧祖先数值或PIT权限。

换手沿原`_turn`精确Decimal百分比除100及float round-trip拒绝门槛；缺数不填0。
原SourceReceipt的四钟、真实状态、whole行数和空scope保持；物理row_count=null
单列保留，typed row_count记录实际whole行数。原physical无完成钟不补造完成钟。
新DailyBar/SecurityRecord event为事实业务日，publication仅为实际父可见上界，
first_seen/ingested为本次视图构造完成后首次记录钟；派生开始/完成分别保存，不能
把派生开始称为已构造完成。新实际决策截止在发行、构建和原件复验之后；
最终源复验钟单列，不能倒填决策截止。无手写HistoricalTimeProof。

## 输出与验收边界

只调用未改`se.run_short_horizon_strategy(..., strategy_id=R1)`一次，不运行R2、
全策略suite、历史存储或题材producer。返回新版本input归档、native run、真实
source/政策registry、继承关系、逐行RAW/turn/price审计及缺口；不持久造旧包装。
证券scope、25会话缺口、数值未知、来源失败分别保留，限价仍UNKNOWN。

`historical_PIT_count=0`、`complete_independent_cycles=0`、投资优势未建立。
后来取得的历史原件只能用于新的回顾研究截止，不能冒充过去盘前预测。
TEST_FIXTURE工程验收不等于真实来源消费或投资验证；真正May18/19/20执行另行
登记来源、实现、一次调用、时限/RSS和输出预算，并保留所有失败。
