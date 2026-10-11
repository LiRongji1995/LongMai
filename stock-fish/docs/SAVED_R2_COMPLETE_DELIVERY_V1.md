# 保存 R2 完整清单交付 V1

`SAVED_R2_COMPLETE_DELIVERY_V1` 是显式离线协调入口：读取一份完整、已保存的 [R2](SHORT_HORIZON_STRENGTH_R2.md)，核对同一输入与实际来源原件，生成本次题材诊断、完整分组和[经济方向解释](ECONOMIC_DIRECTION_EXPLANATION_V1.md)，最后调用[归档展示器](ECONOMIC_DIRECTION_ARCHIVED_DISPLAY_V1.md)交付完整观察清单。它只协调现有证据消费者，不重新选择股票，不改变 R2 原行、阶段、公式、阈值、排序、score 或 FOCUS。

默认日常服务、`STAGE_AWARE_MOMENTUM_V2` 与冻结 `DAILY_V1` 保留原身份。本入口须明确选择保存 R2；普通名单请求不自动授权修改策略、生产代码或提交研究数据。实际操作仍遵循[日常研究协议](CODEX_DAILY_RESEARCH.md)。

## 参数与调用

公开接口位于 `stockfish_core.yaogu.saved_r2_delivery`：

```python
deliver_saved_r2(
    *,
    run_path,
    run_manifest_path,
    input_path,
    sources_manifest_path,
    output,
    context_cutoff,
    membership_mode="DATED_ONLY",
    as_of=None,
)
```

从业务目录使用项目已经核验的 Python 环境：

```sh
python scripts/codex_research.py deliver-saved-r2 \
  --run SAVED_FULL_R2_JSON \
  --run-manifest SAVED_RUN_MANIFEST_JSON \
  --input EXACT_ORIGINAL_INPUT_JSON \
  --sources-manifest REGISTERED_ORIGINAL_SOURCES_JSON \
  --context-cutoff AWARE_CONTEXT_TIME \
  --membership-mode DATED_ONLY \
  --output NEW_PRIVATE_DIRECTORY
```

| 参数 | 合同 |
|---|---|
| `run_path` / `--run` | 完整保存 R2，包含原全资格池与逐项结果；观察股 CSV、前十摘要或仅选择行不能替代。 |
| `run_manifest_path` / `--run-manifest` | 与该完整文件、原输入、run 身份、日期及数量绑定的原登记清单；实际读取与核验文件字节。 |
| `input_path` / `--input` | 与原 run 的 `input_id`、`input_hash` 一致的原 `MarketInput`；不重建或修改输入。 |
| `sources_manifest_path` / `--sources-manifest` | 本次显式登记的真实原件位置及完整文件 SHA；位置或保存 JSON 本身不授予来源能力。 |
| `output` / `--output` | 尚不存在的私有输出目录；现有目录拒绝覆盖。 |
| `context_cutoff` / `--context-cutoff` | 带时区的本次题材情境资料截止；不得早于冻结选择所需的原截止，不改写原 cutoff。经济附件另登记实际生成后的证据截止。 |
| `membership_mode` / `--membership-mode` | 仅 `DATED_ONLY` 或 `CURRENT_COHORT_BACKVIEW`；默认使用日期化成员。 |
| `as_of` / `--as-of` | 带时区的显示时刻；省略时取实际当前 UTC。该值控制可见性，不改变来源时钟或观察日。 |

CLI 在创建 API `Client` 之前分派该入口。整个交付过程不调用网络、SDK、业务数据库、历史数据库或 R1/R2 selector，不创建第二套名单库，不激活配置或切换运行服务。R2 的采集、计算及其授权预算属于前序工作；交付入口只接受显式指定的那一份保存结果。

## 保存运行与输入身份

首先核对完整 run 的整文件 SHA、canonical content hash、run ID、策略/规格/特征/配置/来源身份、观察日、原资料截止，以及 manifest 中对应的输入和各数量。原输入必须经过真实 `MarketInput` 校验与 `digest_market_input`，不得只相信 JSON 自填的 `input_hash`。

正式全部、观察和焦点投影复用现有 exporter 的保存结果逻辑；不通过服务构造器触发 DB 初始化或迁移。完整 items 与原 `eligible_pool_ids` 的集合必须精确一致、IID 均合法且无重复，原 eligible/evaluated 数量一致；全部行（含 REJECTED/UNKNOWN）的 IID 和 name 均核对同一原输入证券目录，不用当前目录替换。观察集合由完整原结果的选择标志和原 `research_selected_ids` 交叉核对，逐项全文、顺序与 digest 均保持。相同数量但不同 IID、改动选择标志、重名或重号、跨 run/input 混配、遗漏原全池和 UNKNOWN 数量均拒绝。

冻结选择绑定仅证明本次读取了该完整保存结果，不重新发行 selector 能力或声称复验原历史库。原 run 的价格、资格、历史 ref/hash、配置和 source identity 原样保留；新的主题、业务或日历来源只进入独立本次情境，不补写原 run。

### 严格 run manifest

顶层只允许以下五个字段，全部必需；缺字段、未知字段、重复 JSON 键和非有限数值均拒绝。

| 字段 | 类型与含义 |
|---|---|
| `schema_version` | 固定字符串 `SAVED_R2_DELIVERY_RUN_MANIFEST_V1`。 |
| `run_sha256` | 完整 `run_path` 字节的 64 位小写 SHA256。 |
| `input_sha256` | 完整 `input_path` 字节的 64 位小写 SHA256。 |
| `sources_manifest_sha256` | 完整 `sources_manifest_path` 字节的 64 位小写 SHA256。 |
| `binding` | 下表的严格绑定对象。 |

`binding` 只允许以下十四个字段，全部必需：

| 字段 | 类型与核验 |
|---|---|
| `strategy_id` | 固定 `SHORT_HORIZON_STRENGTH_R2`，与原 run 一致。 |
| `run_id` | 原保存 run ID。 |
| `input_id` | 原登记输入 ID，与 run 一致；允许显式自定义 ID，不强制 `market_` 加 hash 的生成格式。 |
| `input_hash` | 原 run 的输入 hash，须等于真实原输入 digest。 |
| `observation_date` | 原观察日期，与 run/input 一致。 |
| `target_session` | 字符串或 null，须精确等于原 `run.get('target_session')`；null 不得推断为下个工作日。 |
| `original_decision_cutoff` | 带时区的原保存选择截止，须与 run 一致。 |
| `source_hash` | 原 run 的来源身份，与保存结果一致；不要求等于当前 selector 的代码 source hash。 |
| `counts` | 严格对象，恰好包含 `eligible`、`focus`、`watch`、`rejected`、`unknown` 五个非负整数；逐项复核完整原结果的状态计数。 |
| `native_counts` | 完整原生计数对象，须与原内层结果深相等，保留 evaluated、research_observation、25 窗口、来源及其他原计数；不裁成五键摘要。 |
| `selection_policy_id` | 与原内层结果及唯一 selection_results 的 key/策略/policy/count/IID 序列一致。其显式 input_id/input_hash 若存在必须一致；原生 selection schema 未提供 input_id 时，记为原同 run 容器绑定，不补造此字段。 |
| `original_strategy_identity` | 恰含 implementation_version、spec_revision、feature_version、source_hash、config_hash、spec_hash、selection_policy_hash、source_file_hashes；逐字段核原 run 顶层与内层一致，不用今日版本冒充旧版本。 |
| `selected_ids` | 原官方 observation 的完整 IID 数组与顺序；不以行数相等代替集合、顺序和选择标志核验。 |
| `selected_original_items_hash` | 按上述原选择顺序排列的全部原逐股行 canonical digest。 |

原完整 run 自身的 canonical content hash 和内层结果仍须校验，登记的 byte SHA 不替代这些断言。该 manifest 是明确登记的保存身份合同，不授予新 selector 权限。原 `target_calendar` 证据另行原样保留；缺失或未知不能升级为 `READY`。

## 严格 sources manifest

顶层恰好包含以下六个字段，全部必需；合法无来源用明确空数组或 null，不能省略字段。不得添加手写 theme scope、prices、members、verified capability 或历史证明来绕过原件消费者。

| 字段 | 类型与含义 |
|---|---|
| `schema_version` | 固定字符串 `SAVED_R2_DELIVERY_SOURCES_V1`。 |
| `captures` | 严格 capture 对象数组；可为空。 |
| `business_sources` | 严格 `OfficialBusinessRegistration` 对象数组；可为空。 |
| `counted_nodes` | 严格 `CountedNodeRegistration` 对象数组；可为空。 |
| `reported_scope` | 一个严格 `RegisteredFile` 或 null。 |
| `extra_registered_files` | 严格 `RegisteredFile` 数组；可为空，用于完整登记报道审阅所依赖的全部原文和回执。 |

所有 `RegisteredFile` 恰好为 `{path, sha256}`：`path` 是非空文件位置，`sha256` 是 64 位小写 SHA256；相对路径均相对于 sources manifest 所在目录解析，绝对路径按显式登记读取。`archive_root` 与 `capture_root` 采用同一路径规则。整文件 SHA 与读取前后 stat/身份校验都必须通过，同路径冲突登记拒绝；文件存在并不代表证据已可见或有效。

每个 `captures` 项恰好包含 `{archive_root, result, raw, receipt, typed_receipt}`。`result`、`raw`、`receipt` 分别为原完整文件的 `RegisteredFile`；`typed_receipt` 是原官方 typed `SourceReceipt` 的字段声明，必须逐字段与实际原三件及原 normalizer 映射核对。原 `_clock` 的时钟语义保持，`historical_time_proof` 不接受手写升级，不能用后来下载资料证明过去已经取得。该声明只是定位和交叉核验资料，不能直接恢复 receipt-to-bytes 能力。

每个 `business_sources` 项恰好包含现有 dataclass 的十四个字段：`instrument_id`、`name`、`direction`、`relation`、`summary`、`boundary`、`covered_period`、`publication_date_locator`、`business_page`、`literals`、`original`、`receipt`、`derived_text`、`transform`。前八项按原 producer 的文本语义核验，`business_page` 为从 1 开始的实际页号，`literals` 为本页登记的非空文本数组；后四项均为完整 `RegisteredFile`。仅已选择且身份/name 一致的证券可登记，不允许业务资料增加或改名股票。正式 PDF、实际 HTTP 回执、文本派生与 transform 父 SHA 均由原 producer 复验；登记摘要与边界仍是可审阅的解释，不是自动语义审查已完成的证明。

每个 `counted_nodes` 项恰好包含八个字段：`node`、`literal_name`、`endpoint`、`capture_root`、`result`、`originals`、`directory_receipt`、`directory_raw`。`result`、目录 receipt/RAW 均为 `RegisteredFile`；`originals` 为全部原 receipt/RAW 配对数组，每对恰好两个 `RegisteredFile`，顺序为 receipt、RAW。必须覆盖整个 result 的全部真实祖先、独立 count 与所有分页，不只登记命中的股票或部分页。原公司计数与技术分页计数冲突、非 A 原行和缺证状态继续保留。

非 null `reported_scope` 只接受已固定 SHA 的原文主体审阅文件，并要求其完整 registry 中的原 HTML、HTTP 回执及全部依赖均在 `extra_registered_files` 登记且重读。缺任何实际文件或 SHA 不符直接拒绝，不自造登记或仅凭审阅 JSON 宣称报道原文已经认证。原 parser 不重跑；保存审阅复用明确标为 `SAVED_PRIOR_ORIGINAL_TEXT_REVIEW_REUSED_NOT_REPARSED`。

## 实际来源能力与本次题材诊断

每个登记 capture 都必须包含原完整 result、RAW 和 receipt，登记实际文件路径、整文件 SHA 和捕获根。核验 `historical_replay.verify_capture` 的原请求、receipt、RAW、分页、行数与状态绑定，并独立核对原提供方时钟与实际捕获时钟。不得把截取几行的原件、重新包装的 result、成功标志或新建 receipt 当成原采集证据；原 `PARTIAL`、合法 null 和未知仍保持。

typed `SourceReceipt` 必须与真实三件原始身份、SHA、字段版本、参数、覆盖和原时钟合同一致；不能重新打时间戳。之后实际构造 `SourceArtifactReader(receipts, raw_paths)`。该对象按 receipt ID 重读完整 RAW 字节并核 SHA；保存的 path map、`verified=true` 或 JSON hash 不等于该能力。

`DATED_ONLY` 使用经认证的日期化成员；`CURRENT_COHORT_BACKVIEW` 才显式调用 reader 的当前目录与成员能力，保留真实首次取得时点、供应商范围、选择偏差和历史成员未知。当前篮子不能 backdate 为过去成员。会话集合由原输入的完整交易所日历和实际历史范围取得，不压缩缺日，不硬编码特定月份、股票数量或窗口日期。保存完整 `LATEST_67` 依赖范围与缺 slot，market pack 只收录真正完成全部范围认证的可用会话；短 pack 不能替代 60 日上下文。缺 20 日、60 日或任何必要源时保留 UNKNOWN，采集成功不证明窗口完整。

通过 reader 建立并从原字节复验 market pack，再调用原 `run_theme_evolution`。本次进程内部只对实际返回的原生父对象、同一 bundle、input digest、cutoff、代码身份与终态来源建立父绑定；冻结 selection 另有独立验证能力。保存 JSON 不恢复此绑定，调用方任意 lambda 不得代替验真。随后调用原[题材展示诊断](THEME_OBSERVATION_DIAGNOSTICS_V1.md)，不修改 FSM 或原生门槛。

## 完整分组与缺解释降级

完整分组的主组并集必须精确等于原全部观察集合，每个 IID 恰好出现一次，原逐股行全文保持。重叠题材保留为次级关系，不能重复占位、重复加总或扩大选择集合。原全部资格池与 UNKNOWN 诊断另有正式导出，不能因为面向用户交付观察股而删去原事实。

保留 `confirmed_mainlines`、`fermentation_watch`、`active_divergence_mainlines` 三种角色和原生各角色内的强度梯度。确认、未确认发酵和活跃分歧分别显示；不跨类别发明新排名，不将 DIVERGENCE 隐藏或改成走强。没有可用题材解释的原观察股按原 `primary_stage` 进入模型组，保留原选择顺序；不能识别的原阶段进入明确 UNKNOWN 组。

正式业务登记交给现有 `OfficialBusinessRegistration`；完整独立节点登记交给 `CountedNodeRegistration`。业务关系、供应商节点、日期化题材与原模型结果分别说明。当前经济解释支持的业务方向与范围沿用既有 producer，不自行扩充行业映射或评分。供应商标签不证明主营关系，有限已证观察股的金额/广度不代表全经济方向资金或强度；广义方向强度仍 UNKNOWN。可选当日报道范围只作有真实原文与捕获证据的独立解释，不赋予成员或资金权重。

来源、题材或业务资料正常缺失时，交付完整原 WATCH/观察集合，缺解释轴标 UNKNOWN，并列出覆盖与缺口原因。日期化题材缺登记父、父在截止之后才可见，或原父状态不是 COMPLETE/分页不完整时，在进入 market pack 前逐题材记 UNKNOWN；完整原件仍认证，原 PARTIAL 与分页状态不改。不能截前十、凑数量、删除无解释个股或把 UNKNOWN 当“无主线”。注册文件损坏、SHA 不符、来源冲突、跨 run 混配或不合法时钟属于失败，不能静默降级并继续使用其事实。

合法零观察股只有在完整原 run、选择标志、全池和正式数量核验均通过后成立。该分支单独输出真实空观察清单及状态，不绕过现有经济 producer 的非空集合断言。缺 run、未运行、输入不可读、未知或失败均不是合法空；原 UNKNOWN 股不自动加入观察集合。

## 时钟、可见性与产物

分别登记原选择 cutoff、原 R2 context cutoff、本次题材 `context_cutoff`、真实 grouped 生成时间、经济附件独立 `economic_cutoff`、真实附件生成时间、附件可见时间及 `as_of`。grouped 保存后取实际当前时刻作为经济证据 cutoff，不把 grouped 的生成时间写成较早的 context cutoff。题材只消费在 context cutoff 可见的真实来源；业务解释按独立经济证据 cutoff 复验。合法晚到来源在相应截止仍不可见时保留缺口，不能将其数值提前显示。不能以历史业务日期代替实际取得时间，新解释不可回填旧盘前，当前时点原件认证也不证明旧 PIT。

最终调用既有归档展示器，核对精确 grouped 父文件、经济附件、全部原项、实际显示索引、分组角色及时间下界。`as_of=None` 在附件完成后取实际当前 UTC。显式较早 as-of 只隐藏晚到的新解释：若原 run 自身在该时刻已经可见，协调入口仍交付当时可见的完整原 WATCH，原模型与 UNKNOWN 边界保持。原 run 尚不可见时，在原档核验后直接停止可见交付：只写不含候选ID、数量、数值的状态 manifest 和 TXT，不生成正式候选导出、分组、业务或主题附件；public return 同样不含候选字段，也不声称完整交付。晚附件由既有展示器返回 `NOT_YET_VISIBLE`；不能为了展示而提前填写生成时间。

新目录保存 `all.csv`、`observation.csv`、`focus.csv`、`unselected.csv`、`observation.json`、`grouped.json`、`source-authentication-audit.json`、`complete-observation-list.txt` 和 `delivery-manifest.json`。本次真正发行主题父时另存 `native-theme-context.json`；非空观察集合另存 `economic-direction-explanation.json` 与既有展示器的 `display/` 文件。合法零观察集合不造非空经济附件。

原完整 run/input/source 的 SHA 与稳定文件身份登记在来源审计，交付 manifest 绑定其文件 SHA、原数量、正式导出、本次来源范围、缺口和实际调用计数。单 run/input 文件上限 256 MiB，单来源原件 8 MiB，所有唯一登记来源合计 2 GiB；派生输出单文件 256 MiB、总计 512 MiB，既有展示器自身更小的归档上限仍生效。这里的字节守卫不是 OS 内存或硬墙钟保证；真实应用须另登记调用方的进程墙钟与实际 RSS 观测，不以 fixture 用量证明实样本可在预算内完成。

成功取得资料、工程测试通过、实际交付和投资有效性分别记账；一次真实交付不能证明连续调度可靠。

## 研究与验证边界

初始参数仍为“尚未验证”，`MARKET_EDGE=NOT_ESTABLISHED`，不因新交付入口抬高 FOCUS、收益概率或策略有效性。保存交付的 `selector_reauthenticated=false`，JSON 不授予来源能力；活 reader 的本次验真结果不能回发到旧选择。供应商范围、主题 taxonomy 完整性、广义经济主线覆盖、历史成员与完整情绪周期证据不足时均保留 UNKNOWN；`PIT=false`、`complete_cycles=0`，完整情绪周期不凭每日调用次数累计。

工程验证使用独立目录和显式 `TEST_FIXTURE`，覆盖大于十股不遗漏、原行/顺序不变、同数异股和跨 run 拒绝、重叠不重复、缺解释完整 fallback、真实零股、晚来源/伪父绑定拒绝、原件变化拒绝、离线零 SDK/DB/selector 调用和目录不覆盖。真实保存结果验收另登记实际身份、命令、退出码及资源范围，不复制旧测试数字，不跑未经批准的新 capture/native selector 或全历史回测。
