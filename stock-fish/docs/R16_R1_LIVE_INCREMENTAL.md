# R16-R1 有界真实增量入口

`r16_service_path_trace.py --mode live-incremental` 在一条真实 CLI 根 trace 内请求一次 baostock `market_daily`，随后调用现有 normalizer、HistoryWriter 和 Daily Primary API。它使用已验证的隔离基础输入与本地 master/factor 资产；不构造全目录 RequestQueue 或 CandidateScheduler，不回填历史，不激活输入，不改写生产 official signals/score。

这是代码路径说明，不是该路径已经在真实数据上验收通过的声明。实际结论必须绑定新运行的 commit/tree、原始结果和 trace。

## 输入和边界

- `--db` 必须是已存在的非 official 隔离 corrected DB；只允许 candidate inputs/indexes、history manifests、strategy config/activation namespaces，jobs 必须为空。源连接采用 SQLite URI `mode=ro`，目标必须是新目录。无法满足隔离条件时中止，不自动扩大复制范围。
- `--input-id` 明确指定已验证的 T 基础输入；校验其 history ref/hash、先后日期、来源/schema 与分区完整性。无需按 hash 推断最新输入。
- 基底须先在独立 corrected 构建中完成 factor revision review。live 同时启用 review 与旧身份适用性门禁，但这里必须幂等：任何旧分区变化都拒绝并要求先独立修复，不能用 review 的 session 白名单放宽正常增量。修复的 writer、RSS 和存储增长单列，不能并入 live 日增量。
- `--inventory` 必须包含该 base 所需的原始身份/factor 文件与 receipt 绑定；读取时核对文件 SHA、canonical result hash 和 source content hash。旧行情不重新正常化，历史分区必须原样保留。
- 日期来自已注册交易日历和实际时钟，选择最近已收市 session。保留 base 历史 calendar，只追加注册日历支持的新日；例如 T=09-09、目标=09-11 时必须保留 09-10 的真实缺价日，不能删日制造连续行情。
- provider 请求固定为 `market_daily(date=target)`，timeout=45 秒、max_http_requests=1，无自动重试或其他 endpoint。COMPLETE、完整分页、raw archive/receipt、唯一证券/session、RAW adjustflag 和真实可见 clocks 均须通过校验。失败保留真实错误及原始文件，不拿旧行情替代。

## 保留身份与 UNKNOWN

本模式向 normalizer 传入 `retained_identity_base`。该对象必须绑定同一个 prior history ref/hash；复用的当前名录/退市集合 receipt 必须已经存在于该 base，且 SourceReceipt 的完整内容相等。不属于该 base 的身份 receipt 在此“仅保留已验证旧身份”模式下明确拒绝，不因“不在 base”就视为新日有效。

其含义是：这些身份来源只保留此前输入的参考范围，没有新增下一交易日的适用性证明。normalizer 在构造 SecurityRecord 和写入历史之前将旧名录派生的新日 board 设为 UNKNOWN，保留 delisting_period=None，并令当前 universe.complete=false；当前 RAW 实际提供的 ST/trading status、OHLC、原 receipt 状态、原文 hash 和 clocks 保持不变。不能仅在报告写 stale 而让实际消费者继续读已知 False。

该判断依据是显式绑定的 prior input reference scope，不是 `ingestion>=target_date`。后来取得来源、当前名录或修复后的基础历史也不获得当时官方 PIT 身份证明。默认 normalizer 当前输入调用不启用这个保留模式；测试分别覆盖当期有效身份正例和保留身份跨日反例。

旧 factor 的真实完整窗口到哪里就只能用到哪里。超出 end 的新日 comparable 值保持 null，并记录实际 reason；没有 factor 时不补 1。输入保留 `RECONSTRUCTED_HISTORICAL`，live transport 不把后来修复的历史升级成 `PROSPECTIVE_CAPTURED`。

由于身份缺证明，真实运行可能 eligible=0。此时仍调用原 Daily Primary API、实际 JobService、持久化与 export/RESULT_READY；报告必须写明 `NOT_EXERCISED_NO_ELIGIBLE_FACT_POPULATION`，不得以 input/hash 改变声称非空 feature 消费。非空路径由有实际当期身份和 PV 的 targeted 正例，以及已有真实 offline/data-ready 证据单独证明。provider 的全部原始响应保留；缺乏来源支持身份、未进入 normalizer 的证券另外列出，不能声称完整主板覆盖。

## 运行

先运行小型 TEST_FIXTURE 测试，不进行网络请求：

```powershell
stock-fish/.venv/Scripts/python.exe -m pytest -q stock-fish/tests/r05_product/test_r16_r1_live_incremental.py stock-fish/tests/r03_sources/test_provider_boundaries.py stock-fish/tests/r05_product/test_r16_r1_history.py stock-fish/tests/r05_product/test_r16_r1_e2e.py
```

真实运行示例；下面三个变量必须来自已完成的 corrected 构建与验收证据：

```powershell
$LiveBaseDb = '<verified isolated corrected.sqlite3>'
$LiveBaseInput = '<verified corrected T input_id>'
$LiveInventory = '<verified raw_asset_inventory.json>'
stock-fish/.venv/Scripts/python.exe stock-fish/scripts/r16_r1_supervise.py --output work/r16-r1/live-incremental-process --registry work/r16-r1/live-incremental-registry.json --label R16_R1_BOUNDED_LIVE_INCREMENTAL --budget-seconds 960 --input-manifest $LiveInventory -- stock-fish/.venv/Scripts/python.exe stock-fish/scripts/r16_service_path_trace.py --mode live-incremental --db $LiveBaseDb --input-id $LiveBaseInput --inventory $LiveInventory --output-dir work/r16-r1/live-incremental --budget-seconds 900
```

重连后先读取 registry、run/checkpoint 和实际 PID/create_time；有相同运行时不得再次启动。supervisor 记录进程族 RSS、10 秒心跳和硬预算；normalizer/source 阶段写 `live-checkpoint.json`。已经存在的输出目录被拒绝；失败运行保留，不覆写再试。

## 证据

保存 provider-result 与 raw/receipt archive、live-incremental-build、live-normalization-report、accepted-response、job、result-reference、完整 result 和 trace。build manifest 包含 base/new input/history IDs、源资产 SHA、calendar SHA、完整新增交易日、缺价日、原分区不变检查、新 capture 实际 clocks、归一化当前 bar 数和未映射原始身份。

Trace 的 provider request invocation、HTTP attempts、SDK operations 分列，底层总网络请求不可测时为 null/NOT_MEASURED。max_http_requests 是预算，不能当实际计数。API 保存输入子路径会另外标记 provider SKIPPED，表示该子路径不重复请求；不能覆盖外层实际 provider span，也不能将父子 duration 相加。

实际 API 正常提交、业务事务结束、响应序列化和 RESULT_READY 后，再按报告的精确 document_id/job_id/run_id 进行隔离库只读回读。REQUEST_ACCEPTED、BUSINESS_COMMITTED、RESULT_READY snapshot 原文 SHA/注册 hash、最终 span、job terminal state 与 run/result hash 均须关联一致。外部 TCP、agent scheduling 和用户收到结果时间仍不是这个 in-process Flask 测量的范围。

只有实际 trace 验证通过、trace 为 SUCCESS、job 与 trace 的业务状态均为 SUCCEEDED 时，汇总才标记工程路径 VERIFIED。定向负例让 RESULT_READY snapshot 的真实写入失败；即使业务结果成功返回，汇总仍为 FAILED_TRACE_VALIDATION，CLI 验证失败退出，不保留虚假的 verified 标记。

已结束的 `live-attempt` 结果只可作为录制源 continuation 的输入，不能把其旧 span 搬入新 root，不能用一次离线 continuation 冒充这条从新网络请求开始的 E2E。工程 SUCCESS、数据就绪、非空特征消费和投资有效性分别报告；本轮停止后不扩展 R17。
