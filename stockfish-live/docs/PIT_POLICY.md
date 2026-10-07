# StockFish Live Phase 1 PIT 政策

当前 replay 的可见性由真实时钟和父来源共同决定。Phase 1 不重设历史下载时间，不采用 Research 的历史重建例外，不把工程 fixture 视为真实历史。实现见 [pit.py](../stockfish_live/pit.py)、[ReplayProvider](../stockfish_live/providers/replay.py)、[Collector](../stockfish_live/ingest/collector.py)；字段说明见 [DATA_CONTRACT.md](DATA_CONTRACT.md)。

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

历史回放的未来实际结果暂未实现。后续 forecast/evaluation 必须将未来标签放在独立结果路径，等标签成熟后追加评价，不得混入当时输入或重写预测。Phase 1 未产生市场预测，也未形成投资有效性结论。

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

`DECLARED_SCOPE_ONLY` 仅描述预期流。ST、停牌、上市、退市、指数成分、行业成员及全市场资格仍为 UNAVAILABLE；不能因为缺分钟就确认停牌，也不能因为出现分钟就确认历史资格。若用户提供官方日历或真实历史 scope，来源证明和捕获时钟仍需独立核验，不能复用 fixture 标记。

## 档案、查询与消费边界

来源原始字节、不可变回执和 manifest 共同绑定分钟分片。读取会校验 hash、schema、业务日与身份；发现损坏或冲突失败。档案可以包含晚于当前模拟时点的数据，消费只经 guard 后的 provider 接口取得当时可见部分。

DuckDB 的 `visible(day, cutoff=..., now=...)` 拒绝 cutoff > now，并同时过滤行和父回执的时钟。这里的 now 是调用方提供的可信时刻；replay 的 provider API 另绑定 ReplayClock。不能由业务调用方填写未来 now 来冒充当前模拟时间。

Parquet 序列化排序用于稳定档案；ReplayProvider 按真实 available_at、event time 和 identity 构建到达序列。Collector/QualityMonitor 再验证到达顺序与同日状态，失败批次不推进已消费 cursor。provider timeout、异常返回、数据缺口和质量错误均有明确状态，不会换成零收益或成功空集。状态文件追加到新 output；既有文件或目录拒绝覆盖。可恢复的 provider 周期错误保留在快照和汇总；replay 执行异常中断时保留已写快照并写入 `FAILED`，不冒充完整日输出。

## 防护范围和测试证据

本轮防护是应用接口和调用路径合同。可信档案 loader 会预先读取完整日期档案；拥有 Python 内部对象、任意文件读取或替换进程代码权限的调用方仍可能接触底层历史文件。当前实现没有建立进程、操作系统或恶意插件隔离，也不声称 Python 私有属性能提供这种安全边界。未来 feature/inference 模块必须只接收已校验消费状态；若需要限制不可信代码，须另外设计执行隔离。

自动测试定义覆盖：

| 测试文件 | 证据范围 |
|---|---|
| [test_replay_pit.py](../tests/test_replay_pit.py) | 09:45 在 snapshot/minute/index 入口均无法请求 09:46；逐一未来行/父时钟隐藏与 guard 拒绝；事后下载不回拨收件；完整日 replay 确定性；缺 09:38 不合成；timeout 恢复；恶意 model_copy 重新校验 |
| [test_storage.py](../tests/test_storage.py) | DuckDB 过滤未来事件与晚父来源、未来 cutoff 拒绝；raw/receipt/Parquet/manifest 篡改拒绝；重复身份与不可覆写回执；中断分片续写；单写者和独立临时目录 |
| [test_calendar.py](../tests/test_calendar.py) | 显式日期与可见范围；240 个 END / 242 个 tick；午休、开盘与非法时刻；未知交易所和越界日期 |
| [test_normalizer.py](../tests/test_normalizer.py) | START 完整性、单位、缺字段、RAW 边界；atomic 批次、乱序与晚到；gap / 午休 freshness；fixture 隔离；未来输入与反向观察拒绝 |
| [test_contracts.py](../tests/test_contracts.py) | 证券身份与时区、部分 OHLC 矛盾、父回执不能早于完整事件、无隐式 VERIFIED_PIT 升级 |
| [test_cli.py](../tests/test_cli.py) | 空 doctor 无建库副作用、禁用 live、fixture 防覆盖、scope/calendar 晚可见或版本错配失败、范围配置错误不生成成功输出 |

这些测试使用显式 TEST_FIXTURE 证明工程行为。实际执行命令、退出码、性能和未执行范围由本轮统一验证报告登记；测试定义、fixture 完整日及哈希一致性不证明真实分钟覆盖、真实实时成功或模型有效性。
