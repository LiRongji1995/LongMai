# 标准 HTTP JSON feed 适配器

`providers/http_feed.py` 提供可配置 `HttpFeedProvider`。它需要用户自行提供并核实数据权限的 feed URL；仓库没有内置交易所、商业供应商、Token 或自动回退来源。工程检查包括注入 transport 和 TEST_FIXTURE localhost 临时 HTTP 服务经默认客户端的真实 socket 传输；后者采用明确模拟会话时钟，**没有连接真实分钟源，真实行情覆盖、来源发布时间真实性与实时 SLA 均未验证**。不连接券商、不下单；`MARKET_EDGE=NOT_ESTABLISHED`。

## Feed 合同

HTTP 200，`Content-Type: application/json`，UTF-8 JSON。以下字段必须显式提供；未知字段、重复 JSON key、非有限数字、未声明单位、START、复权数据及没有明确 EMPTY 状态的空结果均拒绝。单个响应最大 32 MiB。

```json
{
  "schema_version": "LIVE_HTTP_FEED_V1",
  "provider": "http_feed",
  "timestamp_label": "END",
  "volume_unit": "SHARE",
  "turnover_unit": "CNY",
  "price_adjustment": "RAW",
  "state": "AVAILABLE",
  "published_at": "2026-09-30T09:31:00+08:00",
  "bars": [
    {
      "instrument_id": "000001.SH",
      "exchange": "SH",
      "symbol": "000001",
      "asset_type": "INDEX",
      "timestamp": "2026-09-30T09:31:00+08:00",
      "open": 3000.0,
      "high": 3002.0,
      "low": 2999.0,
      "close": 3001.0,
      "volume": 100.0,
      "turnover": 300100.0,
      "quality": "VALID"
    }
  ]
}
```

上述数字仅解释格式，不是历史市场数据。指数价格单位是指数点，股票是人民币/股；volume 为本分钟股数、turnover 为本分钟人民币元。来源需自行解释指数成交量/额的统计范围；适配器不会将指数成交额称为全市场成交额。证券 ID、交易所、symbol 和 asset_type 必须全部正确并与 `DatasetScope` 一致，不推断裸代码身份。

OHLC、volume、turnover 可为 `null`，必须相应声明 `quality="PARTIAL"`；不会补价格、零成交或虚构完整分钟。`VALID` 最终仍经过 `MinuteBar` 合同校验。合法 END 时间为 `09:31…11:30 / 13:01…15:00`，秒/微秒必须为零。提供 `calendar=` 时进一步校验明确交易日和交易所；接入 Collector 后仍须通过共同日历和质量检查。

`published_at` 必须存在这个字段，可为 `null`。非空时必须不晚于真实收件时刻，且不能早于响应中的任何完整 bar。`state="EMPTY"` 必须同时 `bars=[]`；该结果在 Live 中保持 PARTIAL，并带 `FEED_DECLARED_EMPTY`，不证明全市场合法为空或研究就绪。

EMPTY 是有来源的捕获事实，必须保留原 raw/receipt/manifest，并仅在父来源实际可见后展示。缓存按 cutoff 选择最近已可见的 capture 状态；未来 EMPTY 不提前出现，未来 AVAILABLE 不抹掉过去已观察 EMPTY。没有 bar 的显式 EMPTY 仍携带对应可见父回执，不能用无来源空列表代替。

## 真实收件时间与来源等级

每次完整 HTTP response body 的原始字节 SHA256 成为本地 `source_id`。本地 `SourceReceipt` 的 `received_at` 取响应取得后的真实时钟，`available_at` 取解析校验后的真实时钟；行和父回执绑定同一 hash、provider、时间依据和 PIT 等级。服务端不能提供或覆盖本机 `available_at`、`received_at`、`source_id`、PIT 标记。

- 默认 `trusted_published_timestamps=False`：本地 `source_timestamp=received_at`，`source_timestamp_basis=RECEIVED`，`pit_status=RESEARCH_ONLY`。服务端填了 published_at 也不会自动升级。
- 只有操作者已独立核实 feed 发布时钟语义并显式配置 `trusted_published_timestamps=True`，才使用 `published_at / PUBLISHED / VERIFIED_PIT`；此时缺 published_at 拒绝。该开关是操作者的来源声明，**代码本身不验证外部证据真实性**，也不能使今天取得的历史下载在过去可见。
- `fixture_mode=True` 必须与 TEST_FIXTURE scope 一起使用，输出始终 TEST_FIXTURE；注入测试时钟不会变成真实历史证据。

实时 runner 应先 `await provider.capture()`，再用捕获结束后的**真实 wall clock** 调用 `Collector.step(now)`；`get_market_snapshot(as_of,since)` 默认只读缓存，不再发网络请求。这样 09:31:00.200 取得的完整 09:31 bar 可在真实 09:31:00.200 之后当轮消费，仍不会冒充 09:31 整点已经知道。预测 decision_at 可另取该完整分钟标签，availability 必须保留真实晚到时刻。

当前 live runner 要求实际 capture 与计划 tick 仍处于同一分钟；若跨入下一分钟，报 `LIVE_CAPTURE_MISSED_MINUTE` 并保留失败产物。研究 Collector 使用 VISIBLE_BATCH_CHAIN_V1 绑定已接受可见批次与父回执；V2 流式状态使用 APPEND_ONLY_DELTA_CONTEXT_CHAIN_V1。真实延迟另记，不改变来源时钟或混入确定性特征 hash。

显式便利方法 `poll(as_of,since)` 会抓取后按给定 cutoff 读缓存。例如 `poll(as_of=09:31)` 的响应实际取得于 `09:31:00.200`，首轮返回空 PARTIAL 和 `FEED_CAPTURE_PENDING_NEXT_CUTOFF`；数据保留在 buffer，到后续 cutoff 才可见。`capture_if_empty=True` 仅用于首次未捕获时的便利抓取，默认关闭。适配器不会修改 snapshot.as_of，也不会把来源时间改成整点。

超时、HTTP 错误或无效批次不丢弃已经捕获的 buffer，不改变接受记录。capture 失败会同时抛出异常并保留错误；后续 market snapshot 缓存读取持续抛出该错误，直到下一次 capture 成功，确保 Collector 明确记录失败而不是把旧缓存当作健康行情。Collector 的失败 cursor 语义保持一致。

同一响应字节重复取得时保留第一次真实回执。不同响应中完全相同的 `(instrument_id,timestamp)` 行会去重；修改同一 bar、给已出现更新分钟的证券补旧分钟、重复行、跨业务日和未来完整分钟均拒绝。整批校验后才原子接受，不覆盖旧数据。每个 provider 实例只对应一个业务日，应在新会话创建新实例。

## 接入方式

私有数据根显式配置 `provider.json`，同时提供 calendar.json 和 dataset.json；对应 ContextStore 可选，缺失会限制宽度、行业及预测。配置合同为：

```json
{
  "schema_version": "LIVE_PROVIDER_CONFIG_V1",
  "url": "http://127.0.0.1:9000/fixture-feed",
  "provider_name": "http_feed",
  "trusted_published_timestamps": false
}
```

URL 仅为格式示例，未随仓库启动这个服务。真实源需由操作者替换并独立核验来源语义与权限。命令为 `stockfish-live live --data-root /path/to/private-feed --output /path/to/new-live-run --max-ticks 240 --timeout 10`；缺配置报 LIVE_PROVIDER_UNAVAILABLE，不联网、不自动改用 synthetic fallback。研究特征路径由独立 research-config.json 的 forecast.feature_version 选择，V1/V2 训练材料与特征配置 hash 不混用。

```python
from stockfish_live.providers.http_feed import HttpFeedProvider

provider = HttpFeedProvider(
    configured_feed_url,
    declared_scope,
    calendar=explicit_calendar,
    timeout=10.0,
    trusted_published_timestamps=False,
)
# 实时 runner 使用真实时钟，不把 availability 改成计划整点。
await provider.capture()
record, latency = await collector.step(actual_wall_clock_now())
# Collector 已配置上述 provider，仍负责预期范围、缺口与数据质量。
```

`provider_name=` 默认 `http_feed`，必须与 feed 声明一致。URL 没有自动添加查询参数，服务端应提供可重复轮询的增量或近期窗口；重复已捕获行会去重。只有已捕获的记录可由 `get_minute_bars / get_index_bars` 查询，这两个方法不会补下载历史。`capture()` 返回新响应的本地 SourceReceipt；若 payload 字节完全相同，则返回第一次回执。`source_records()` 返回原始 response bytes 与本地 SourceReceipt，供获授权的独立归档层保存；适配器本身不写生产目录。

测试可以注入 `async transport(url, timeout) -> HttpResponse` 和 `now_fn()`，或 `clock.now`。默认实现使用标准库 HTTP 请求；请求失败不会自动换源、安装服务或扩大访问范围。运行方应调用与 Collector 一致的节奏和 timeout，实际运行日志/延迟不要混入确定性特征 hash。

只配置五指数 scope 可以使用指数状态；证券资格、完整市场名单、prevclose/限价、ST/停牌、日线环境和历史申万一级成员仍需各自明确的 `ContextBundle` 来源。没有这些输入时全市场宽度、情绪支持、行业状态和相应模型输入保持 UNAVAILABLE/UNKNOWN，不由本适配器填充。

工程检查定义见 `tests/test_http_provider.py` 和 `tests/test_runtime_audit.py`：实际收件晚于 cutoff、处理可见时钟、buffer 经失败保留、SHA 与父回执、默认来源降级、显式发布时间声明、未来事件、身份/单位/时间反证、去重/禁止修订、显式 EMPTY、离线查询和 TEST_FIXTURE 边界。localhost 检查使用默认 HTTP 客户端，核验实际响应字节 hash、收件/解析/Collector 顺序、缓存读取不额外请求，以及 HTTP 503 后质量降级且原 buffer 不丢失；报告保留在私有临时目录，不作为真实行情验收。实际执行与退出码见 [V1 验证](VALIDATION_V1.md)。
