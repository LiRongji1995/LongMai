# 原父截止与后来派生 V1

`ORIGINAL_PARENT_ASOF_LATE_DERIVATION_V1` 是显式研究输入生产者，未接入默认 CLI、服务或策略权重。它解决的是：原始资料确实在旧截止前取得，而今天才按已登记规则组装新事实。它不把今天生成的输入或名单称为当时已发布的预测。`MARKET_EDGE=NOT_ESTABLISHED`。

## 正式接口与来源边界

实现位于 `stockfish_core/yaogu/original_parent_late_derivation.py`。调用方提供 `OriginalCaptureArtifact(archive_root, result_path, result_sha256)`；这些字段只是定位信息，不是来源能力或可信 proof。生产者实际读取整份 result、RAW 和 receipt，再调用原生 `historical_replay.verify_capture` 认证原归档字节、receipt 身份、请求范围、行、状态、分页和覆盖。SHA256 只证明本地完整性，不证明供应商真实性。

```python
bundle, audit = normalize_original_parent_asof(
    original_capture_artifacts,
    observation_date,
    original_source_cutoff,
    limits=DerivationLimits(),
)
```

不接受保存 JSON 恢复的 capability，不接受 CSV 造输入，不创建 `HistoricalTimeProof`。所有提供的 primary 父来源，包括失败尝试，都必须实际在旧截止前取得；重新读取也不得改变它们的原四时钟。原始数据业务范围不得超过观察日。正数来源必须有与实际 RAW、receipt 一致的字段版本；只有真正失败、零行、未完成分页且错误身份一致的负包，允许原 worker 缺少字段版本时沿用已登记 schema 标签。该标签不授予数值权限。

RAW 或 wrapper 明示的 `first_seen_at` 必须与原 receipt 相等；实际 wrapper 起止时间如有登记也必须顺序正确，完成时间不能晚于旧截止。原 RAW 缺少该字段时明确记 `UNKNOWN_MISSING_RAW_FIRST_SEEN_AT`，不把 receipt 的归档代理时钟描述为供应商发布证据。跨 RAW 父的同证券、同日、同复权标记，逐一比较原 normalizer 消费的业务字段：OHLC、前收、量、额、换手及三个状态标记。数值使用精确 Decimal/Fraction，等值小数格式及额外元数据不构成修订；状态字符串不作数值归并。真实业务差异、单父重复行、错误请求证券或复权标记均拒绝，不能通过原 normalizer 的 last-wins 顺序覆盖。

原因子 worker 明示的逐证券 `captured_at` 若早于该次实际 request start，则是 checkpoint 继承线索。V1 要求同时提供此前整份 genuine 归档：相同 provider/request scope、逐证券 COMPLETE 片段状态与时钟、全部行多重集一致，且此前 wrapper 已完成后才启动本次请求。缺祖先或行冲突即停止；此前原 PARTIAL 仍原样交给 normalizer，并继续受原 strict resolver 拒绝。这里仅核显式继承线索，不授予新的 lineage capability，也不从 locator 清单推断完整采集库存；真实任务仍应登记完整原尝试库存。未明示片段时钟的旧包不被宣称已经完成 checkpoint 祖先认证。

V1 只接受已有量价、身份和日历端点：当前 SSE/SZSE 目录、calendar、master_all、market_daily、history/history_adjusted、adjustment_factors、delisting_sse 与 stock_basic/stock_basic_all。完整 BaoStock 日历必须覆盖每一个请求日，重复日期、冲突开闭市标记与合并后的断档拒绝。没有目标交易日的原日历，不延长日期轴或猜目标日。

生产者调用原 `normalize_capture_with_report` 恰好一次，`history_store=None`，使用调用过程实际取得的当前时间。公式、值、缺失、来源引用、父状态、资格声明与原 normalizer 保持一致。新模块随后通过原 Pydantic 类型构造新的派生行与新 `MarketInput` 身份，按下表记录四时钟。

| 字段 | 新派生行语义 |
|---|---|
| `event_time` | 保留 normalizer 已明确的业务事件；DailyBar 必须是该交易日 15:00 上海时间。Universe 使用原目录父事件的最大边界，表示旧截止时可见的当前目录观察，不能冒充历史板块登记。 |
| `source_published_at` | 原依赖父 `source_published_at` 的最大值。明确标记 `PARENT_VISIBILITY_BOUND_NOT_INDEPENDENT_PUBLICATION`，不是新增独立发布证据。 |
| `first_seen_at` | 本次实际开始派生的当前时间，晚于全部依赖父收件时间。 |
| `ingested_at` | 本次实际开始构造新派生输入的当前时间，不早于本次 first_seen 或任何依赖父入库时间。产物持久化仍由调用方另外登记完成时间。 |

不修改任何 `SourceReceipt`。原 resolver 首先要求事件及发布边界不晚于旧截止，再在 `RECONSTRUCTED_HISTORICAL` 且所有真实父已可见时允许后来派生的 seen/ingested，标记 `reconstruction_used=True`。其他覆盖模式、晚到父来源、未来事件或发布仍受原门禁限制。这里没有修改或放宽 resolver。

## 保留的未知与后续消费

当前目录及原 listing 信息不升级为交易所历史板块 effective 登记。原 dated RAW/master 风险状态和当前目录来源分字段记账；normalizer 的日期适用声明不额外证明历史资格。原 PARTIAL、失败、复权冲突、缺比较基、限价与主题/资金不足继续保留。每行审计保存前后内容 hash、原 normalizer 四时钟、本次真实四时钟、全部真实父和逐字段来源角色。

返回输入不绑定历史、不写业务库，也不调用 R1/R2。原 history ref/hash、原输入及旧 run 没有被改写。若后续需要拼接既有真实历史，必须另外执行官方只读 owner、payload SHA、typed identity、逐行 PIT、实际所需窗口和复权基准精确链接；不能用本报告、完整分区索引认证或相同 schema 代替这些检查。真实旧因子或精确链接不足时保持 UNKNOWN，不能清除原 PARTIAL 祖先或把新值回写旧事实。

原 calendar 只截止观察日时，目标日期证据仍 UNKNOWN。R2 五日全市场软金额参考需要五份同日完整四 A RAW/master；合格池金额不能替代。没有原主题、资金、限价资料时，不把它们记为 FALSE 或零。本模块不证明主线识别、历史预测 PIT、投资优势、完整独立周期或可执行买卖。

## 身份、预算与工程验收

`source_identity()` 单独绑定新 producer、合同文档及直接依赖实现 SHA。它不是旧 run 身份，新增输入身份为实际 `digest_market_input`，不把 Go projection hash 当 Python 原输入 hash。

`DerivationLimits` 默认 128 份 capture、每原件 8 MiB、唯一来源合计 256 MiB、60 秒；允许显式登记的上限分别为 128、256 MiB、2 GiB、7200 秒。内置检查在读取、normalizer 的 progress 边界及终态执行；调用方仍须提供独立墙钟/RSS 外层守卫，不能把该合作式检查称为硬 OS 限制。Source SHA/stat 和实现身份在终态再次核对。

定向测试只用明确的独立 `TEST_FIXTURE` 归档，覆盖原父与后来派生、默认 normalizer 不变、旧身份/源字节不改、部分父严格拒绝、复权未知/冲突、来源篡改与重复、晚父、未来事件/发布、日历缺失/缺日、失败负包及大小/来源竞态，以及实际 RAW 时钟冲突、精确业务修订、checkpoint 缺祖先和同片段时钟/行数却改值的反例。工程通过不等于真实旧 25 会话资料、名单或投资验证已完成。
