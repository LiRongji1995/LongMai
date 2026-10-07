# StockFish Live PIT 政策

当前 replay、研究 pipeline、评价与HTTP capture的可见性由事件、真实来源/收件时钟和父证据共同决定。不重设历史下载时间，不采用 Research 的历史重建例外，不把工程 fixture 视为真实历史。实现见 [pit.py](../stockfish_live/pit.py)、[ReplayProvider](../stockfish_live/providers/replay.py)、[Collector](../stockfish_live/ingest/collector.py)；字段说明见 [DATA_CONTRACT.md](DATA_CONTRACT.md)。

## cutoff 与完整分钟

在模拟时刻 `T`，记录进入消费路径必须同时满足：

```text
bar.timestamp (= event_time, minute END) <= T
bar.available_at <= T
bar.source_timestamp <= T                 # 如果已知
parent.received_at <= T
parent.available_at <= T
parent.source_timestamp <= T              # 如果已知
```

还需通过行与父来源的 identity/provider/PIT/time-basis 对应、时钟顺序、显式交易日和分钟结束点、声明 scope 及 RAW 口径校验。未知 source_timestamp 保留为空，不补一个发布时间；相应记录不能声明 VERIFIED_PIT，缺字段也不能标 VALID。

END 标签下，09:45 只能使用截至 09:45 已完整结束且已经实际取得的分钟；09:46 bar 不能因在磁盘上存在而提前可见。START 标签必须加一分钟得到完整事件时间，父来源和 availability 原值保留。集合竞价、分钟聚合和盘后补录的原始口径须由实际 provider 另外举证。

`ReplayClock` 只前进，反向推进报 `CLOCK_REVERSAL`。`PITGuard.cutoff` 明确拒绝晚于 clock.now 的请求。Provider 的 snapshot、minute、index 三种入口均受该 guard 约束；未来 cutoff/end 请求报 `FUTURE_ACCESS_DENIED`。请求本身合法但行或父来源尚未可见时，该记录不进入返回结果，不伪造 bar，也不表示请求区间完整。区间完整性由 declared scope 和 gap 状态另报。

## 晚采与重建

2026 年下载的旧历史记录必须保留 2026 年的真实 received_at/available_at。即使事件和发布时间能够定位到 2025 年，也不能在 2025 年 replay 中绕过实际收件时钟；`RECONSTRUCTED` 或 `RESEARCH_ONLY` 标签不会解除上述不等式。

09:38 bar 若到 09:46 才收到，只能在 09:46 以后申请可见；若同一证券已经接受 09:45 bar，QualityMonitor 会将旧 timestamp 晚到拒为 `OUT_OF_ORDER`，不会回写先前快照或补成此前完整覆盖。需要专门研究晚到修订时，应另登记版本化修订流程；本轮保留错误和缺口。

未来实际结果保存在独立MatureOutcome/ResearchStore路径，只有对应窗口完整且实际可见后才能用于训练或评价。forecast使用更早会话的成熟历史，OOS实际结果仅在成熟后对照原预测；不得混入当时feature或重写旧forecast。本轮没有真实投资有效性结论。

## source basis 与证据等级

`source_timestamp_basis` 区分 `PUBLISHED`（声明的来源发布时间）、`RECEIVED`（响应收件时刻）和 `UNKNOWN`。不能把 HTTP 收件时间写成已证明的 vendor 发布时间。`VERIFIED_PIT` 合同要求非空 PUBLISHED 时间；这个结构性要求不认证来源、商业权限或历史证据真实性。原始响应 SHA256 证明归档字节一致，不能单独证明过去已经获得或 vendor 身份。

状态记录使用 [effective_pit_status](../stockfish_live/contracts/quality.py)，由 scope、calendar、全部已接受 bar 与父回执中的最弱等级决定：

| 输入组合 | 状态等级 |
|---|---|
| 全部为 `TEST_FIXTURE` | `TEST_FIXTURE` |
| `TEST_FIXTURE` 与任一其他等级混合 | 拒绝 `DATA_PROVENANCE_MIXED` |
| 任一为 `RESEARCH_ONLY` | `RESEARCH_ONLY` |
| 其余中任一为 `RECONSTRUCTED` | `RECONSTRUCTED` |
| 全部满足 `VERIFIED_PIT` | `VERIFIED_PIT` |

空等级集合保守返回 RESEARCH_ONLY。`VALID` 属于数据完整性质量，不提升 PIT 等级；来源标记较强也不能提升实际更弱的日历或 scope。`provider="mock"` 必须保持 TEST_FIXTURE。fixture 通过显式命令在新隔离目录创建，已有目录拒绝覆盖；无实时 provider 时明确 `LIVE_PROVIDER_UNAVAILABLE`，不会自动改用 mock。

## 日历与 scope 也是当时输入

日历必须给出明确 sessions、有限覆盖、交易所范围、source 和真实 available_at；不可按 weekday 延展。scope 必须绑定 calendar hash，包含明确 identity 和 STOCK/INDEX 类别，并记录实际 available_at。日历或 scope 晚于该日 09:30 时，整日 replay 拒绝启动；范围外日期、覆盖内未列入的休市日、交易所范围错配分别失败。

`DECLARED_SCOPE_ONLY` 仅描述预期流。scope 本身不提供 ST、停牌、上市、退市、指数成分、行业成员及全市场资格；缺少对应的独立 PIT Context 时这些事实保持 UNAVAILABLE；不能因为缺分钟就确认停牌，也不能因为出现分钟就确认历史资格。若用户提供官方日历或真实历史 scope，来源证明和捕获时钟仍需独立核验，不能复用 fixture 标记。

## 档案、查询与消费边界

来源原始字节、不可变回执和 manifest 共同绑定分钟分片。读取会校验 hash、schema、业务日与身份；发现损坏或冲突失败。档案可以包含晚于当前模拟时点的数据，消费只经 guard 后的 provider 接口取得当时可见部分。

DuckDB 的 `visible(day, cutoff=..., now=...)` 拒绝 cutoff > now，并同时过滤行和父回执的时钟。这里的 now 是调用方提供的可信时刻；replay 的 provider API 另绑定 ReplayClock。不能由业务调用方填写未来 now 来冒充当前模拟时间。

Parquet 序列化排序用于稳定档案；ReplayProvider 按真实 available_at、event time 和 identity 构建到达序列。Collector/QualityMonitor 再验证到达顺序与同日状态，失败批次不推进已消费 cursor。provider timeout、异常返回、数据缺口和质量错误均有明确状态，不会换成零收益或成功空集。状态文件追加到新 output；既有文件或目录拒绝覆盖。可恢复的 provider 周期错误保留在快照和汇总；replay 执行异常中断时保留已写快照并写入 `FAILED`，不冒充完整日输出。

## 防护范围和测试证据

本轮防护是应用接口和调用路径合同。可信档案 loader 会预先读取完整日期档案；拥有 Python 内部对象、任意文件读取或替换进程代码权限的调用方仍可能接触底层历史文件。当前实现没有建立进程、操作系统或恶意插件隔离，也不声称 Python 私有属性能提供这种安全边界。当前 feature/inference 模块只接收已校验消费状态；若需要限制不可信代码，须另外设计执行隔离。

自动测试定义覆盖：

| 测试文件 | 证据范围 |
|---|---|
| [test_replay_pit.py](../tests/test_replay_pit.py) | 09:45 在 snapshot/minute/index 入口均无法请求 09:46；逐一未来行/父时钟隐藏与 guard 拒绝；事后下载不回拨收件；完整日 replay 确定性；缺 09:38 不合成；timeout 恢复；恶意 model_copy 重新校验 |
| [test_storage.py](../tests/test_storage.py) | DuckDB 过滤未来事件与晚父来源、未来 cutoff 拒绝；raw/receipt/Parquet/manifest 篡改拒绝；重复身份与不可覆写回执；中断分片续写；单写者和独立临时目录 |
| [test_calendar.py](../tests/test_calendar.py) | 显式日期与可见范围；240 个 END / 242 个 tick；午休、开盘与非法时刻；未知交易所和越界日期 |
| [test_normalizer.py](../tests/test_normalizer.py) | START 完整性、单位、缺字段、RAW 边界；atomic 批次、乱序与晚到；gap / 午休 freshness；fixture 隔离；未来输入与反向观察拒绝 |
| [test_contracts.py](../tests/test_contracts.py) | 证券身份与时区、部分 OHLC 矛盾、父回执不能早于完整事件、无隐式 VERIFIED_PIT 升级 |
| [test_cli.py](../tests/test_cli.py) | 空 doctor 无建库副作用、缺实时配置时拒绝 live、fixture 防覆盖、scope/calendar 晚可见或版本错配失败、范围配置错误不生成成功输出 |

这些测试使用显式 TEST_FIXTURE 证明工程行为。实际执行命令、退出码、性能和未执行范围由本轮统一验证报告登记；测试定义、fixture 完整日及哈希一致性不证明真实分钟覆盖、真实实时成功或模型有效性。


## 上下文也需要父来源与实际时钟

ContextRecord 的 effective_from/effective_to 只表示业务有效区间，不替代 available_at 或父SourceReceipt。ContextBundle.visible 同时检查有效区间、行/父source、received和available时钟；相同identity与同availability冲突拒绝。今天取得的旧行业成员、资格、昨收、限价或日线不能用于过去决策。

日线环境必须基准日早于本业务日且实际盘前可见；同时段成交额历史 observation_date 必须早于当日。历史行业变化按当时成员重算，成员有变化时不冒充同一cohort的边际变化。未知资格/ST/停牌保留None，缺分钟不推断停牌；没有完整market inventory时宽度保持声明池口径。

## 历史相似与未来实际结果

HistoricalState/ForecastConfig在消费边界重新校验。更早业务日、同证券、同上海HH:MM、相同feature_version、state实际availability<=as_of是历史候选的必要条件；每个horizon还必须有实际已成熟且available_at<=as_of的MatureOutcome。标签证券/decision/anchor/target与state及显式日历一致，未来未可见标签的实际价格不参与距离、归一化、model hash或top10解释。

特征配置 hash 也必须匹配，不能混用 V1/V2 或同版本不同配置的历史。新 `demo` 默认逐分钟调用 `MARKET_FEATURES_V2` 流式引擎，过去视图保存首次观察的 cutoff；晚取得的 context 不重算过去 snapshot。V1 无状态累计 prefix 仅作为明确选择的兼容参考路径。选择由 research-config 的 forecast.feature_version 决定，缺配置保留 V1 默认；新的数据集/run/hash 不改变旧预测或升级旧 run。

启动和 evaluate 在模型消费前核验标签父 raw/receipt、完整未来交易分钟与来源集合、实际可见时钟、原 state 价格锚点、目标价/horizon、保存路径和 regime；ResearchStore 的自洽 payload/manifest hash 不足以证明标签真实来自归档。校验报告为 LIVE_LABEL_PROOF_V1，不代表认证外部来源。

每个horizon只在合格训练窗fit均值/尺度。query与测试窗不进fit，不随机切分。缺输入维度排除或拒绝相应预测；缺成熟label不填0。top10实际路径也必须当时已可见，不因底层档案有未来行而提前显示。

+30/+60 horizon按交易分钟推进，跳过午休；已过11:30/14:00/15:00不回补。revision只比较当前已产生与先前已产生的预测，使用共同未来目标的绝对价格锚点；不读未来实际走势来修订旧forecast。

walk-forward对每次外层决策只使用更早实际可见训练资料，内层K选择只看外层决策之前已经成熟的验证结果；每个内层预测仍仅fit更早资料。秒/微秒延迟必须保留实际as_of，不能为凑名义分钟切分回拨availability。跨分钟晚采/缺结果单列，不算零收益。真实OOS、fixture、PIT未证明研究结果分开，未验证模型参数不升级为投资结论。

原 snapshot.forecasts 只追加本轮正式决策新预测，latest_forecast_ids 引用当时最近记录。非决策分钟没有新 forecast 不代表已保存预测失效；历史读取按所选时点解析已经产生的记录，不能用未来 forecast 替换过去记录。

## HTTP实际收件与live cutoff

HttpFeedProvider没有默认端点。显式capture取得response原始字节，SourceReceipt.received_at/available_at由本机实际响应取得/解析时钟产生，feed不能指定或覆盖。默认source时间依据RECEIVED、RESEARCH_ONLY；显式声明可信publication语义后才能采用PUBLISHED/VERIFIED_PIT，但配置声明不认证外部证据。

capture结束后，runner使用实际wall-clock调用同一个Collector；不能把09:31:00.200实际取得的记录写成09:31整点已见。名义decision_at可以是完整分钟标签，as_of/availability保留实际秒/微秒。若capture跨入下一分钟，当前runner报LIVE_CAPTURE_MISSED_MINUTE并保留失败记录，不能把迟到回执写入旧分钟快照。

get_market_snapshot/minute/index默认只查询已捕获缓存，future cutoff拒绝；capture/poll的显式网络动作与历史查询分开。HTTP错误、无效schema、来源/单位/身份错配、future publication/bar、bar修订/旧分钟晚补、跨日均明确拒绝。显式EMPTY保持PARTIAL，不证明完整市场合法空集。相同字节重传保留第一次回执，不改变actual availability。详情见 [PROVIDER_FEED.md](PROVIDER_FEED.md)。

EMPTY 本身也是带父来源的实际观察：原始响应、receipt 和 manifest 单独归档，只在父来源已经可见后展示 FEED_DECLARED_EMPTY，不能将稍后收到的 EMPTY 提前到名义分钟，也不能由后来的 AVAILABLE 响应抹掉过去已观察 EMPTY。研究 Collector 的 VISIBLE_BATCH_CHAIN_V1 绑定已接受可见批次；流式状态的 APPEND_ONLY_DELTA_CONTEXT_CHAIN_V1 绑定实际 cutoff、增量与 context。hash 算法版本和配置隔离，实际 latency 独立记录。

## 当前测试与证据

新增test_market_state/test_streaming_market约束PITcontext、身份、缺资格/成员/基准、V1参考与V2流式事实及过去视图；test_forecasting/test_prepared_history约束成熟label、未来值不可影响hash、训练窗归一化、时间/证券/配置匹配、revision共同锚点、walk-forward与指标；test_demo约束逐分钟生成、目标日不预制label和固定seed；test_http_provider/test_runtime_audit约束实际收件、父证据、EMPTY与受控localhost真实HTTP；test_saved_evaluation约束原预测后验；test_api约束已保存run、时间选择、future访问和缺产物。对应定义和实际执行证据分开，统一结果见 [VALIDATION.md](VALIDATION_V1.md)。

这些检查不会认证外部发布时间、真实全市场覆盖、合法数据使用权、长期实时SLA或投资优势。本机当前仅合成资料，真实feed未配置且未进行实际联网验收。


保存预测正式后验使用SAVED_FORECAST_EVALUATION_V1，消费原forecast distribution、原实际as_of与成熟outcome，不用当前配置重算旧预测。W/F重新按历史时钟生成预测属于另列的回顾诊断，不能冒称当时已经执行的保存结果。两者均检查state/label实际可见时钟并保留缺结果、未成熟与样本不足，不填零。
