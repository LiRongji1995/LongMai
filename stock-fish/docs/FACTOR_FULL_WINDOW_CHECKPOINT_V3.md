# 完整复权采集、真实首见时间与原请求接线 V3

新增[原失败请求显式复权恢复 V1](EXPLICIT_FACTOR_FAILURE_RECOVERY_V1.md)：28→29 原主板范围 3,194 只/64 批来源已补齐，复用 800 只、真实补采 2,394 只；194 项工程检查和独立原生回查通过。沿用原登记与累计预算，旧失败、策略和历史结果保留。新首见时间不追认历史 PIT；SDK 子树 RSS 监督与最新完整盘前交付仍待完成，边界见记录。

2026-10-30。本轮继续处理 P0 的完整复权与首见时间工程缺口。新版本在独立源码 bundle 内精确升级两个 producer，接入已有恢复登记的 original 全流程；原业务目录与旧 bundle 的核心、配置、三个 producer、策略、历史 run、RAW 和失败记录保持。

这里的完成范围是采集实现、持久检查点、严格回收及同登记接线。没有执行新的全市场联网补采、重试28→29旧请求、自动更新默认登记或切换运行服务。历史缺失的首见时间没有被修复成已知；当前完整复权数据是否齐备仍由实际新采集和原完整来源验证决定。

## 修复的具体问题

旧 worker 只在正常返回时补写父 RAW 的 `first_seen_at`。超时路径读取的 factor checkpoint 缺少这个原始时钟，也可能缺完整请求代码和未完成状态，原 cache 因而正确拒绝。原父回收器还会丢弃零行但已完成游标的合法空响应；logout 异常可能掩盖已完成邻居。已有 V2 bundle 处理了未来 worker checkpoint，但未包含当前统一入口闭包，且父进程在 worker guard 之前超时仍可能读取既有 checkpoint。

V3 将完整单证券接收、检查点落盘、父进程回收和请求防重连成同一条可执行路径。新 helper 仅在构建时内嵌到 worker 和 parent；采集时仍由三份实际 producer 源码 hash 绑定全部实现，不能在旧 parent 下暗换 worker 后继续声明旧来源。

## 完整历史窗口与时间合同

- 新版本 factor 端点只接受 `1990-01-01 → 原目标日期` 的完整窗口和完整、有序、唯一证券列表。尾窗不合成完整历史，已有尾窗诊断及其旧结果保持。
- 每只证券最多查询一次。严格验证五个原字段、证券身份、日期范围、事件日期唯一、正有限因子、行数上限以及游标开始/中途/末尾错误。存在错误的单证券片段整段拒绝，完整邻居保留。
- 游标完整耗尽后记录本次实际 UTC `captured_at`，立即持久化本次检查点。父快照 `first_seen_at` 是本次快照实际形成时间，始终晚于其包含的已完成片段观测时间；它不是交易事件日期、供应商历史发布时间或首次字节到达的精确测量。
- 未完成证券保持 UNKNOWN，无伪造完成钟；明确失败记录实际 `failed_at`。零行完整查询仅表示提供方返回空集合，缺左锚时原复权算法仍为 UNKNOWN，不填因子1。
- 只有全证券完整、全窗有效且清理成功时，正常父结果才能为 COMPLETE。磁盘 checkpoint 始终为 PARTIAL；超时、进程错误、logout异常回收也保持父 PARTIAL，完整邻居不能将父失败升级。
- 正常返回保留 checkpoint 的原首见钟；父进程不能用归档时间补造缺钟。历史下载仍以实际取得时间参与可见性判断，不恢复历史 PIT。

现有 `comparable_rows`、复权公式、R1/R2/SAM V2/DAILY 参数及选择条件不变。独立合成参考覆盖上市锚、拆分、分红、缺锚、历史修订和未来数据；它验证实现边界，不证明真实市场收益。供应商字段与复权因子的背景见 [BaoStock 官方说明](https://www.baostock.com/helpdocs/pdf/BaoStock%E5%A4%8D%E6%9D%83%E5%9B%A0%E5%AD%90%E7%AE%80%E4%BB%8B.pdf)，本轮没有据外部示例改写原公式。

## attempt、持久化与回收

父进程在SDK调用前创建排他的本次attempt声明，绑定随机attempt_id、实际开始时间、完整请求和三producer摘要。既存checkpoint或attempt声明均拒绝，包括没有RAW的早期失败；不删除失败声明、不跨attempt续传、不为旧行重新贴钟。

检查点采用私有临时文件、完整写入及fsync后发布，并验证原路径、内容SHA和七字段物理身份。父回收时重新检查本次attempt绑定、状态全集、时间顺序和producer原件；错误摘要、重复键、特殊文件、符号链接、目录替换及内容迟变均拒绝。预算与I/O故障停止采集，原SDK预算预留和实际计数机制保留；中断后未知实际次数不伪造为零。

这些声明是同一系统本次受控采集与本地字节一致性的证据，不是供应商数字签名。旧无钟checkpoint不迁移、不重新认证成V3。

## 可执行入口与原登记边界

新增文件：`codex_factor_capture_v3.py` 提供固定原SHA的精确producer补丁；`build_premarket_source_bundle_v3.py` 构建/核验新源码目录；`codex_factor_checkpoint_premarket_v1.py` 连接已有恢复登记、原请求账本和 original 工作流。

从仓库根使用原已验收解释器，目标必须是此前不存在、位于源码树外的私有目录：

```sh
"$REGISTERED_PYTHON" -I -B stock-fish/scripts/build_premarket_source_bundle_v3.py create \
  --source-root "$REGISTERED_BUSINESS_SOURCE" --destination "$NEW_SOURCE_BUNDLE"

"$REGISTERED_PYTHON" -I -B stock-fish/scripts/build_premarket_source_bundle_v3.py verify \
  --destination "$NEW_SOURCE_BUNDLE" --manifest-sha256 "$PINNED_MANIFEST_SHA256"
```

manifest绑定全部文件、原producer preimage、精确patch、构建工具与原工作流静态scripts闭包；拒绝缺件、额外文件和迟变。新副本保留许可及第三方声明。构建失败保留现场，但撤销本次拥有的成功manifest。后续verify不把原可变checkout当永久依赖；原旧cache中的producer绝对路径仍须保持。

新入口要求真正通过原环境恢复验收的 `RecoveryRuntime`，不能将普通位置登记包装成恢复资格。显式选择原有限采集profile后，先执行只读计划；只有全新交易日请求才可考虑后续执行：

```sh
"$REGISTERED_PYTHON" -I -B stock-fish/scripts/codex_factor_checkpoint_premarket_v1.py \
  --runtime "$EXISTING_RECOVERY_RUNTIME" --registry-root "$ORIGINAL_REGISTRY_ROOT" \
  --bundle "$NEW_SOURCE_BUNDLE" --manifest-sha256 "$PINNED_MANIFEST_SHA256" \
  --profile REAL_PREMARKET_RECOVERY_3H_V1 \
  premarket-plan --observation-date YYYY-MM-DD --target-session YYYY-MM-DD \
  --request-key "$FIXED_NEW_DAY_REQUEST_KEY"
```

经当次授权执行时，仅将同一命令中的 `premarket-plan` 改为 `premarket`，其余参数固定。源码发布和计划本身不启动采集。该显式新来源profile沿用原有限配置上限、实际日历与容量检查，并在每个请求/阶段重新准入；计划中原完整来源图的UNKNOWN/阻塞单列，不冒称已认证。默认日常入口仍是 `codex_daily_v2.py`，original 引擎保持。

执行子进程使用原登记Python、全新字节码查找位置与manifest钉住的bundle。仅本次执行source_root指向副本；实际登记、state、config、report、资源锁和operations目录均由原runtime持有。原成功缓存仍按原物理producer引用验证，新attempt才使用新producer身份。

原祖先请求检查、同scope的同request-key及同观察日/目标日检查在锁内执行。FAILED、SUBMITTING、不明状态和已完成的另一版本请求均不能通过换key、换bundle或换engine自动重开；相同已RECORDED身份只能原回执复用。没有新建平行登记、改变预算或提供旧失败恢复授权。需要处理旧失败时仍须独立、显式的恢复合同。

## 本轮验收

发布目录使用原CPython 3.12.14、独立TEST_FIXTURE和全新字节码查找位置，九文件组合 **228 passed、24 subtests passed**，pytest 106.32秒、外层108.826秒、退出0，无失败和skip。新增四文件100项（producer32、独立参考20、bundle23、gateway25），其余为原V2 bundle、cache、防重、有限恢复profile和尾窗边界回归；子测试单列，不累加为独立测试数量。实际命令形式如下，变量均为当次原解释器或此前不存在的私有路径：

```sh
"$REGISTERED_PYTHON" -I -B -X "pycache_prefix=$NEW_PYCACHE_PATH" \
  stock-fish/scripts/run_isolated_tests.py -q \
  tests/unit/test_codex_factor_capture_v3.py \
  tests/unit/test_factor_capture_v3_reference.py \
  tests/unit/test_premarket_source_bundle_v3.py \
  tests/unit/test_codex_factor_checkpoint_premarket_v1.py \
  tests/yaogu/test_premarket_source_bundle_v2.py \
  tests/yaogu/test_premarket_capture_cache_v1.py \
  tests/unit/test_codex_daily_entry_v2.py \
  tests/unit/test_codex_recovered_premarket_v1.py \
  tests/unit/test_codex_factor_incremental.py \
  --basetemp "$NEW_TEST_FIXTURE_DIRECTORY" --junitxml "$NEW_JUNIT_XML" --tb=short
```

独立参考包括Fraction与手算OHLC、上市锚/拆分/分红、历史修订/删除/补报拒绝、合法空与缺锚UNKNOWN、早晚可见性和矛盾状态拒绝。实际owned子进程fixture未替换subprocess.run：假SDK第一证券完成后第二证券阻塞，父进程真实3秒timeout、终止并回收worker；PID/PPID、原checkpoint与RAW/receipt首见钟、完整邻居和UNKNOWN均核对。父子禁网络及SQLite；这不是实际供应商采集。

实际登记指向发布检出。按该原路径构建的私有bundle为199份清单成员加manifest，创建及初验2.893秒、退出0；从bundle自身再验1.127秒、退出0。首次以开发根构建的包在实际入口按来源不匹配拒绝（入口退出2、外层诊断断言退出1），随后按原登记路径重建，没有放宽来源门槛或改登记。

在禁止网络、SQLite及worker启动的只读探针中，使用原28→29请求、原request-key和新包执行premarket-plan，4.831秒返回 `FACTOR_ENTRY_EXISTING_REQUEST_RECONCILE_NO_RETRY`、退出2，符合禁止重试的验收预期。15份原登记/操作记录的完整字节与七字段身份保持。两个源码目录571份既有core/config/scripts均保持；新Python语法、文档链接及Git whitespace另检。

完整实际参数、stdout、JUnit、bundle清单与原件保护证据保存在私有审计目录，不发布本机路径、测试数据或源码运行包。开发定向检查与发布组合不重复累加；本轮未重跑全仓/旧R1黄金回归，前轮已记录的旧黄金身份问题没有改断言或宣称修复。

原28→29作业只读复核仍为FAILED，完整交付为false、用户接收未知；原控制文件和源码身份另有末验。该核对不是当日来源重认或新的名单交付。没有执行真实SDK请求、全市场补采、连续20交易日验收或投资回测；也未测试Windows原生运行和合盖恢复。新checkpoint避免丢失已经持久化的完整片段，不能保证阻止合盖或供应商故障。

完整真实补采、旧失败的显式恢复、请求与job的创建前持久绑定、真实执行证据和连续日常交付仍需相应验收。`MARKET_EDGE=NOT_ESTABLISHED`。
