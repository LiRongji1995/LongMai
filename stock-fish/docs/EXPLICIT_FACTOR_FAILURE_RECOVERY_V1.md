# 原失败请求的显式完整复权补采 V1

新增[SDK 子进程树监督 V2 与本轮盘前验收记录](SDK_TREE_SUPERVISED_CURRENT_PREMARKET_V2.md)：监督与回收采用版本化修复，保留实际执行的 V1 原件。11 月 2 日固定请求已补齐当前日历、原主板范围 3,194 只/64 批完整复权并生成核心产物，但后续流程因采样超时与 UTC 总限时失败，根进程当时的回收未获确认；10 项题材 PARTIAL 保留，完整盘前尚未通过。V2 仅完成隔离工程验收，未重跑真实业务；原三小时预算、登记、策略、历史结果与全部失败记录保持，未切换服务。

**2026-11-01 验收结果：**原 28→29 主板查询范围 **3,194/3,194 只、64/64 批**已补齐。复用原 16 批、800 只；新增 48 次真实 BaoStock 请求、2,394 只，全部父结果 COMPLETE。两个旧失败批次各有一次新的完整来源，旧失败终态与原件保留。独立原生 cache 回查通过；这只是历史来源恢复，不是最新盘前名单或历史 PIT 恢复。

本轮按用户明确授权，处理原 2026-10-28 观察日、2026-10-29 目标日请求的真实来源缺口。恢复沿用原登记、原 request-key、原共享 cache 和原预算，另存恢复计划、创建前绑定和执行回执；旧失败终态、原 RAW、策略和历史结果保持。

恢复角色是 `HISTORICAL_LATE_SOURCE_RECOVERY`。新取得的完整历史因子带本次真实首见时间，不追认成 10 月 28 日已知数据，不生成或替换旧策略名单，也不代表最新交易日盘前交付。

## 原范围与失败

从原 28 日 master 和当时 29 日官方目录重新推导，项目原沪深主板普通 A 股查询范围为 **3,194 只**，其中沪市 1,700 只、深市主板 1,494 只。原有 64 批，前 63 批各 50 只，最后一批 44 只。此查询范围不等于策略最终资格，也不扩展到创业板、科创板或北交所。

已有 16 个完整父请求可复用，覆盖 800 只。一个 PARTIAL 父请求仅有 12 只完整片段、38 只失败；另一个未入索引的失败父请求虽然有 17 只检查点片段，却缺少 RAW 原始首见时间。另有 46 批从未发出。补采维持原批次、原证券顺序及 `1990-01-01 → 2026-10-28` 完整窗口，共需 48 个新 attempt、2,394 次证券查询；旧片段不续贴新钟或升格为完整父请求。

原 operation 为 `FAILED_OR_UNKNOWN`，没有 job 回执。恢复显式对账原 operation 与用户授权处理的失败 job，保留“旧账本未原生绑定 job”这一事实；新的恢复 claim 在启动采集前持久绑定自己的 job 路径。

## 预算与时间

原请求已预留 25 个请求、948 次 SDK 调用及 192 次 HTTP 尝试。实际 SDK 已知小计为 896，但有一次请求的实际次数未知，不能用 896 代替完整用量，也不能将未知计为零。补采计划最多再预留 48 个请求、2,490 次 SDK 调用，累计 73 个请求、3,438 次 SDK 调用，HTTP 预留不增加。

恢复扣除原调用、读取和输出消耗；时间取可认证 UTC 与单调时钟耗时的较大值，并额外保守预留 60 秒外层开销。这 60 秒是预算预留，不能称为实测耗时。原 10,800 秒、cache 1 GiB、output 768 MiB、登记容量政策及每请求最多 90 秒均不扩大；每次实际请求仍按剩余预算准入。

资源验收有一项明确缺口：本入口直接调用采集父进程，没有调用原完整工作流的进程树监督器。`Budget.check()`检查控制进程自身 RSS，`tree_rss_bytes=3 GiB` 仅保留原配置值，SDK 子进程树没有主动采样、限额或峰值记录；阻塞请求期间也没有独立内存 watchdog。不能用自身 RSS 或旧 job 的峰值替代本次 SDK 子树峰值。默认完整工作流的监督器保持，但该历史恢复入口的进程树监管仍属剩余 P0，需另加版本化的自有进程组监督与回收验收。

本轮核查时，原缓存日历只覆盖至 2026-10-30，行情与 master 只到 10 月 28 日。旧日历不能为当前上海自然日和下一交易日背书。历史来源补采不关闭最新交易日门禁；新盘前请求仍须取得足够日历、最新行情并按原完整协议准入。

## 恢复控制与来源边界

独立恢复控制与完整复权采集模块只增加在 scripts 层。原 V3 bundle、原 producer 路径和现有核心认证集合保持；调用固定原登记解释器、原资源锁、显式 V3 manifest 和本次新增脚本身份。

计划绑定原 operation、失败 job、终态、配置、预算、来源范围、完整与失败缓存，以及新脚本和 bundle 身份。同一原 operation 只允许一个恢复槽；claim 已存在时只可查询，不能重新执行、自动续跑或通过改 key 再提交。失败和不明提交继续保留。

新 attempt 通过原 cache 归档，逐证券实际时钟、完整游标验证与 timeout 回收沿用[完整复权来源 V3](FACTOR_FULL_WINDOW_CHECKPOINT_V3.md)。旧完整来源按原 SHA、物理身份和 producer 重新认证。新失败也独立保存，单批最多一次；预算或来源完整性门禁失败即停止。只有整个原查询范围的父请求均完整并通过终验，才能声明来源补采完成。

完整父请求只证明该查询完成。可比价格仍要求每股有效左锚、合法因子事件和原 OHLC 检查；合法空响应不自动补因子 1。历史 PIT、资格、题材完整性和 DAILY_V1 就绪仍须各自证据，不能从采集完成推导。

## 显式执行方式

本版采集器仅接受本轮已核实的 28→29 捕获失败、原 50 只分批和两类不完整父请求。它不是任意日期或任意失败的通用重试开关。控制入口位于原登记源码目录，以原解释器和全新、尚不存在的字节码查找路径运行；下列变量均来自实际登记或已核验的原件。

```sh
"$REGISTERED_PYTHON" -I -B -X "pycache_prefix=$NEW_PLAN_PYCACHE" \
  stock-fish/scripts/codex_explicit_failure_recovery_v1.py \
  --runtime "$ORIGINAL_RECOVERY_RUNTIME" --registry-root "$ORIGINAL_REGISTRY" \
  prepare --operation "$ORIGINAL_OPERATION" --job "$ORIGINAL_FAILED_JOB" \
  --bundle "$VERIFIED_V3_BUNDLE" --manifest-sha256 "$V3_MANIFEST_SHA256" \
  --output "$NEW_PRIVATE_PLAN"

"$REGISTERED_PYTHON" -I -B -X "pycache_prefix=$NEW_EXECUTION_PYCACHE" \
  stock-fish/scripts/codex_explicit_failure_recovery_v1.py \
  --runtime "$ORIGINAL_RECOVERY_RUNTIME" --registry-root "$ORIGINAL_REGISTRY" \
  execute --plan "$NEW_PRIVATE_PLAN" --plan-sha256 "$EXACT_PLAN_SHA256"

"$REGISTERED_PYTHON" -I -B -X "pycache_prefix=$NEW_STATUS_PYCACHE" \
  stock-fish/scripts/codex_explicit_failure_recovery_v1.py \
  --runtime "$ORIGINAL_RECOVERY_RUNTIME" --registry-root "$ORIGINAL_REGISTRY" \
  status --operation-id "$ORIGINAL_OPERATION_ID"
```

`prepare` 不联网、不认领恢复槽，记录自身消耗；`execute` 再扣准备阶段消耗，并在原锁内重认证同一计划。`status` 只核归档控制一致性，不表示本次重认供应商来源。存在 claim、失败或不明状态时不可再次执行同一槽。

每批请求前后同时核 UTC 与单调时钟，timeout 取两者剩余预算和原 90 秒上限的较小值。原 V3 子 worker 的单个在途批次仍使用原单调时钟 timeout；强制合盖期间不能承诺每个 SDK 调用立即中止。防空闲休眠也不能阻止强制合盖。终态必须通过实际 UTC 总耗时检查，超限不能封存为成功。

## 验收记录

发布检出使用原登记 CPython 3.12 环境、新字节码查找位置和独立 TEST_FIXTURE，八文件组合 **194 passed**，pytest 111.23 秒、外层 113.228 秒、退出 0。没有失败、skip 或删除失败样本。新增控制、采集和独立反例共 78 项，旧 V3 producer、bundle、gateway、cache 和有限恢复 profile 共 116 项；开发定向检查不与这次组合重复累计。

```sh
"$REGISTERED_PYTHON" -I -B -X "pycache_prefix=$NEW_TEST_PYCACHE" \
  stock-fish/scripts/run_isolated_tests.py -q \
  tests/unit/test_codex_explicit_failure_recovery_v1.py \
  tests/unit/test_codex_full_factor_recovery_v1.py \
  tests/unit/test_explicit_factor_recovery_reference.py \
  tests/unit/test_codex_factor_capture_v3.py \
  tests/unit/test_premarket_source_bundle_v3.py \
  tests/unit/test_codex_factor_checkpoint_premarket_v1.py \
  tests/yaogu/test_premarket_capture_cache_v1.py \
  tests/unit/test_codex_recovered_premarket_v1.py \
  --basetemp "$NEW_TEST_FIXTURE_DIRECTORY" --junitxml "$NEW_JUNIT_XML" --tb=short
```

独立反例推动修复了重复证券被集合吞并、结果状态与权限标记矛盾、实际晚钟/预算、末次回执替换或删除误报成功、同 key 陌生 attempt 及旧失败目录新增文件被错误接纳等问题。失败撤销只处理本次仍拥有的文件；无法撤销的异常回执保留，并以独立失败终态优先解释。

真实只读计划退出 0，外层 18.968 秒；计划内部计量 15.818 秒，保守扣除 16 秒，采集调用数为 0。计划确认 3,194 只、64 批、16 批复用与 48 次新尝试，执行时间余额进一步降为 8,204 秒。准备和执行参数、源码身份、JUnit 与完整数据均保留在私有审计目录。

真实执行从 2026-10-31 23:14:41 UTC 至 2026-11-01 00:11:00 UTC，最终 `COMPLETE_SOURCE_RECOVERY_LATE`，退出 0。外层单调耗时 **3,379.232 秒**、UTC 耗时 3,379.205 秒；控制器末次计量 3,377.329 秒，返回读取 989,810,161 字节。完成回执的出版前快照与末次计量分别保存，不能混作同一计量时点。执行进程及绑定本次 PID 的防空闲休眠进程均已回收。

| 实际核查项 | 结果 |
|---|---|
| 原查询范围 | 3,194 只、64 个完整父请求、46,720 行因子；沪 1,700、深主板 1,494 |
| 复用与新增 | 16 旧批/800 只；48 新批/2,394 只，新增 30,530 行 |
| 新采集调用 | 48 请求、SDK 实际与预留均 2,490、HTTP 为 0；无新增失败或未知调用计数 |
| 原请求累计预留 | 73 请求、3,438 SDK、192 HTTP；原实际 SDK/HTTP 的未知仍保留，不能改为完整实际总数 |
| 新父首见时间 | 2026-10-31 23:17:48.765232 UTC 至 2026-11-01 00:03:54.971920 UTC |
| 控制查询 | `status` 退出 0，确认封存控制一致性；自身不重认供应商来源 |

独立离线验收使用原解释器，在原共享 cache 对全部 64 个原 RequestSpec 逐项 `lookup → verify → terminal`，一次退出 0，外层 UTC 10.115 秒、单调 10.112 秒。它没有调用整段采集器终验代替独立核查，也没有网络、SDK、HTTP、SQL、writer 或缓存目录创建。64/64 父、3,194/3,194 证券均完整，零行证券为空；无重复证券/事件日，三因子正有限，逐证券观测时间及父 RAW/receipt 首见链一致。该查询检查没有使用 OHLC 重算可比价格，不能据此声明复权价格或策略就绪。

独立验收读取 148,064,403 字节，核对 334 个物理引用及 266 个原生验证引用；两批旧失败的 12 个引用前后原 SHA/七字段身份保持。预算进一步按原消耗 2,580 秒、准备外层向上取整 19 秒、执行外层向上取整 3,380 秒和验收外层向上取整 11 秒保守扣减，剩余 4,810 秒。SDK 子树 RSS 仍明确为未验证、峰值 UNKNOWN，不能把这项来源验收称为全部资源限额认证。

既有 581 份源码、24 份登记 JSON 和 4,525 份历史文件逐项完整 SHA/七字段物理身份保护检查通过；旧策略、模型、历史 run 与失败记录未改写。完整 argv、stdout、JUnit、源码身份、原始采集、恢复控制和独立验收报告均保存于私有审计/运行目录，不随源码发布。

## 仍未完成的 P0 与范围

- SDK 子进程树 RSS 监督及自有进程组限额回收，见前述明确资源缺口。原自身 RSS 检查不替代它。
- 当前交易日历和最新行情/master 的补采，以及原完整流程的新盘前交付。此次只恢复 28 日原查询范围，不把截至 10 月 28 日的数据标为最新；当次查询的旧日历仅覆盖至 10 月 30 日。
- 原 26→27 作业仍为 `BUDGET_EXCEEDED`，其全流程时间额度已经耗尽。本次未借用 28→29 的预算重开该题材失败，也未扩大原额度。
- 历史 PIT、逐日资格、题材完整性、DAILY_V1 就绪及投资有效性仍需各自证据，不能从完整复权查询推导。

本轮未重跑全仓、历史收益回测或连续日常交付；未测试 Windows 或强制合盖。工程测试、真实来源齐备、历史 PIT、当日交付和投资有效性分别判断；`MARKET_EDGE=NOT_ESTABLISHED`。
