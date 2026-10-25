# 共享结果复用、新输出压缩与复权尾窗 V2

2026-10-25。本轮在固定共享运行环境上增加完整旧核心的归档登记、新核心的压缩保存，以及复权原件复用和尾窗诊断。继续使用 `scripts/codex_chat.py`，不复制大库，不改变冻结策略、全股票池、历史 run、原 RAW/receipt/producer 或旧来源代码闭包。

这三项有不同的能力边界：已有完整结果经鉴证后可以跨聊天读取；新计算可以选择压缩格式；复权尾窗只提供新事实和修订诊断，不能自动替代原策略要求的完整历史因子。代码实现、隔离工程检查、真实数据验证和投资结论分别记录。

## 统一入口与日常顺序

从已登记源码目录、已登记解释器执行。全局 `--runtime` 和 `--registry-root` 放在子命令前；私有默认登记仍由所有聊天共用。示例中的大写路径、日期和 ID 均需替换为已核实的实际值。

```sh
python scripts/codex_chat.py runtime-status
python scripts/codex_chat.py storage-status
python scripts/codex_chat.py --runtime shared-daily core-archive-status
```

`runtime-status` 只核位置和物理绑定；`core-archive-status` 只列归档定位及原操作阶段。二者都不验证名单内容或当天资料就绪。展示清单前，使用同一 `archive-id` 执行 `core-archive-reuse`；需要不同观察日、不同来源或新截止时，不把旧归档改日期继续用。

核心清单与题材、新闻附件独立报告。R2 量价核心所需的25个交易日和题材演化的67会话范围不合并为一个“已就绪”标记；核心完成不意味着题材或 DAILY_V1 完整。当前主动 V2、已确认的 PV 上下文和显式 R2 的选择规则均保持。

## 已完成原核心的归档登记与复用

新增 [`codex_core_archive.py`](../scripts/codex_core_archive.py)，协议为 `CODEX_FULL_CORE_ARCHIVE_V1`。本版仅接纳已识别的完整原生 R2 核心及其成功父 supervisor 终态，不接纳孤立 summary、半成品 `core-ready.json`、失败作业或任意 JSON 导出。

```sh
python scripts/codex_chat.py --runtime shared-daily core-archive-register \
  --manifest PRIVATE_CORE_REQUEST.json \
  --worker-dir PRIVATE_ORIGINAL_CORE_DIRECTORY \
  --supervisor-dir PRIVATE_ORIGINAL_SUPERVISOR_DIRECTORY \
  --profile FULL_CORE_ARCHIVE_READONLY_V1

python scripts/codex_chat.py --runtime shared-daily core-archive-reuse \
  --archive-id ARCHIVE_ID \
  --profile FULL_CORE_ARCHIVE_READONLY_V1 \
  --observation-date OBSERVATION_DATE --target-session TARGET_SESSION \
  --cutoff ORIGINAL_AWARE_CUTOFF --manifest PRIVATE_CORE_REQUEST.json
```

首次登记重新认证父终态、原请求、完整25日范围、证券身份和逐股复权完备性，核原 RAW/result/receipt、实际首见钟、producer、locator、完整代码及策略身份。调用原 normalizer 一次验证原输入语义，再核原资格池、canonical run/input 哈希和全部八份产物、五份正式导出；不运行 selector、不下载数据、不写业务数据库。

成功后只追加小型鉴证文件和新版操作回执，原目录、文件字节及物理身份保持。重复登记沿用原 `run_once` 状态机；失败或结果不明保持原状态，不因重新发起相同命令而自动重试，也不伪造旧 `premarket` 的完成状态。

复用仍重建必要来源和实现闭包，核原终态、日期、全部文件 SHA 与七字段物理身份、原输入与运行哈希、正式导出和摘要。篡改小回执并重算其自身哈希不能删掉必需来源或改变原清单。复用不运行 normalizer 或 selector，不新增大产物；它是归档研究结果，不恢复 `NativeCore`、来源能力或历史 PIT，也不自动接入题材和后验执行。

`FULL_CORE_ARCHIVE_READONLY_V1` 显式限定累计读取4 GiB、唯一原件1 GiB、最多4096文件、900秒、进程RSS 2.5 GiB；登记鉴证文件最多1 MiB。读取预算不等于网络额度或磁盘写入额度。实际返回量和耗时在每次结果中记录，超限拒绝，不扩大原配置。

## 新核心的压缩保存

新增 [`codex_compact_core.py`](../scripts/codex_compact_core.py) 和 [`codex_compact_artifacts.py`](../scripts/codex_compact_artifacts.py)。明确需要计算新核心时，才使用独立的 `FULL_CORE_COMPACT_V1` 档位：

```sh
python scripts/codex_chat.py --runtime shared-daily core-run-compact \
  --manifest PRIVATE_CORE_REQUEST.json --profile FULL_CORE_COMPACT_V1
```

原 `run_core`、normalizer、selector 和 live owner 校验保持。原八份文件先写入本次专有暂存目录，完成原终验、压缩、完整回解及再次终验后，才移除本次暂存的八份文件。旧 run 没有“接管并删除”入口；不能把已存在的历史目录当成本次暂存目录。

新存储协议 `CODEX_COMPACT_CORE_ARTIFACTS_V1` 分开记录逻辑内容哈希和压缩后存储哈希。已有 `canonical-run.json.gz`、`input.json.gz` 保持原字节，其余文本产物用 gzip 保存。原 manifest 中的暂存路径保留为历史描述，不篡改成新位置；新读者按独立映射验证全部八个逻辑角色及其原字节，不能把压缩 manifest 当作旧 owner。

同请求、同来源、同实现和同截止再次调用时，重新验证来源及压缩产物后复用原运行。首轮原生调用计为 normalizer一次、selector一次；R2 内部保留的原 R1 对照不因此消失。复用两者均为零，不产生第二份大输出。旧 `core-run` 和旧归档格式继续使用各自协议，不静默迁移历史资料。

显式档位为累计核心读取4 GiB、900秒、进程RSS 2.5 GiB、进程树RSS 3 GiB。压缩模块的原逻辑输出及新存储各限128 MiB，原件与新包并存的峰值需单独准入；协调器另为控制文件保留空间。小包不代表免费计算或省略来源校验。

压缩或终验失败不登记成功。若精确清理本次暂存期间中断，可能留下完整已校验压缩包和部分暂存文件，操作保持失败或未知，不自动继续删除。有效的存储字节也不能单独证明业务终态。

## 复权原件复用与尾窗诊断

新增 [`codex_factor_incremental.py`](../scripts/codex_factor_incremental.py)。先认证同一原缓存中的完整基线及原交易日历，再检查目标日期是否已有相同代码、1990年起点的完整原请求。已有完整原件可由原消费者继续验真使用，不重复下载。

```sh
python scripts/codex_chat.py --runtime shared-daily factor-plan \
  --baseline-record PRIVATE_ORIGINAL_FACTOR_METADATA.json \
  --calendar-record PRIVATE_ORIGINAL_CALENDAR_METADATA.json \
  --target-session COMPLETED_TARGET_SESSION --profile FACTORS_READONLY_V1

python scripts/codex_chat.py --runtime shared-daily factor-reuse \
  --baseline-record PRIVATE_ORIGINAL_FACTOR_METADATA.json \
  --calendar-record PRIVATE_ORIGINAL_CALENDAR_METADATA.json \
  --target-session COMPLETED_TARGET_SESSION --profile FACTORS_READONLY_V1
```

`factor-plan` 和 `factor-reuse` 只读，不采集；缺精确原件时明确报告缺口。范围限定为同一批最多50只原主板代码，目标在基线后31个自然日以内，原日历和实际收盘时钟必须允许该会话；代码范围不是策略合格池。

确有尾窗事实诊断需求时，须显式指定用途、档位和联网参数：

```sh
python scripts/codex_chat.py --runtime shared-daily factor-sync \
  --baseline-record PRIVATE_ORIGINAL_FACTOR_METADATA.json \
  --calendar-record PRIVATE_ORIGINAL_CALENDAR_METADATA.json \
  --target-session COMPLETED_TARGET_SESSION --overlap-days 7 \
  --profile FACTORS_DELTA_DIAGNOSTIC_V1 --purpose DELTA_FACTS_ONLY \
  --execute-network
```

不传 `--execute-network` 时仍离线。联网诊断通过共享资源锁和原缓存 writer 锁，在真实请求前再次检查容量；最多1个新请求、52次SDK额度、单请求90秒、整次240秒、原缓存总量256 MiB，并受已登记写入配置中更低额度约束。单次新请求按16 MiB增长预留，不自动增加原配置的请求数或全局容量。

默认7个自然日重叠窗用来发现相同事件行的修订。即使重叠窗完全一致，供应商更早历史是否改变仍是未知；尾窗为空只表示该精确查询没有事件行，不能补成因子1，也不能宣告完整历史不变。

| 返回状态 | 可使用的含义 |
| --- | --- |
| `ORIGINAL_FULL_WINDOW_AVAILABLE` | 精确目标完整原件存在；仍经原消费者校验，不由尾窗合成。 |
| `PLANNED_DELTA_FACTS_ONLY` / `MISSING_DELTA_NO_NETWORK` | 只计划或缺原件，没有发新请求。 |
| `DELTA_FACTS_OBSERVED_REVALIDATION_REQUIRED` | 已取得尾窗事实，完整历史仍需重验。 |
| `OVERLAP_REVISION_DETECTED_REVALIDATION_REQUIRED` | 重叠窗发现修订，不能沿用旧完整结果。 |
| 父源不完整或未完成请求 | 保留失败／PARTIAL／未完成状态，不自动重试。 |

本版不把“旧基线＋新尾窗”拼成新的完整 result/receipt，不发行来源能力，不交给 normalizer 充当完整输入；`strategy_input_promoted` 和 `synthetic_full_result_created` 均保持 false。因此不能据本轮入口声称全市场跨日复权下载已经全部增量化。真正晋级为策略可用的完整因子，仍须原完整请求，或另行实现并验证包含历史修订证明的来源协议。

## 容量、来源和版本边界

本机已登记的共享数据与环境总逻辑上限1 GiB保持，实际磁盘余量要求也不改。显式只读和压缩档位是本次动作的独立预算，不改写私有原profile、旧worker预算或失败记录。写入准入不是文件系统硬配额；并行外部应用占盘仍可能导致中途失败。

完整来源、旧run和原代码闭包不重压缩、不搬迁，不重建大库或下载模型。新压缩仅处理当前调用拥有且尚未发布的输出；现用环境、缓存和历史证据不由查询自动清理。真实首见时间、覆盖类型、UNKNOWN和合法EMPTY分列，原DAILY_V1就绪状态、`UNVALIDATED`及`MARKET_EDGE=NOT_ESTABLISHED`保留。

日常请求使用同一保存身份。不同日期、截止、来源、实现或存储协议不会误命中；旧结果可以作历史查询，不被抬升为当天新结果。源码发布、共享登记、运行服务切换和连续每日验收分别记录，本轮没有自动切换服务或调度。

## 本轮验收记录

所有测量均在本机固定 daily/review CPython 3.12 环境完成。没有请求行情供应商、复制业务库、下载模型或执行投资回测。下述耗时是实际墙钟记录，受系统负载影响，不能当成严格同条件 A/B 或次日冷启动承诺。

### 真实完整结果复用

使用已保存的2026-10-23观察／2026-10-26目标原R2结果，119份真实来源、3045只原合格证券和25日窗口保持。64 WATCH、2980 REJECTED、1 UNKNOWN、0 FOCUS未改变；原资格未知、题材／限价缺口及DAILY_V1未知保持，不宣称全市场所有证券或完整盘前附件就绪。

| 动作 | 实测耗时 | 新增大产物 | 原normalizer／selector／供应商调用 |
| --- | ---: | ---: | --- |
| 原首次运行历史记录 | 406.63秒 | 原八份核心文件53,005,596字节 | 原完整计算；用作历史比较基线 |
| 本轮一次性归档登记 | 241.76秒 | 0；鉴证元数据263,707字节，另有小回执 | 1／0／0 |
| 新进程通过聊天入口复用1 | 49.93秒 | 0 | 0／0／0 |
| 再次新进程复用2 | 60.16秒 | 0 | 0／0／0 |

两次重复查询耗时较原首次运行历史记录少约85.2%～87.7%，没有重新选股。第二轮还逐项对比共享数据和登记目录的文件元数据，清单完全一致，逻辑字节增长0。首次归档登记存在一次性完整鉴证成本，不能把该成本隐藏成首次即免费。

重复查询仍执行完整内容和来源校验：每轮累计返回读取约848 MiB，进程RSS峰值约995～1223 MiB。这是Python返回字节及核验内存，不能称为磁盘实际读取量或网络流量；当前实现仍不是秒开摘要模式。

真实归档命令均退出0：上述 `core-archive-register` 参数来自原request、worker、supervisor目录；随后两次 `core-archive-reuse` 固定同一archive-id、观察日与目标日，使用独立新进程。完整命令、结果与时钟保存在本机私有工程测量中，不发布真实资料路径和结果集。

### 压缩与复权

对真实完整核心八份原文件做只读内存压缩及逐字节还原，载荷由53,005,596降为35,898,758字节（50.55→34.24 MiB），减少17,106,838字节，即32.27%。耗时0.87秒。原有两个gzip文件保持原字节；六份文本用gzip level 6。该试验未写入新包，未计新manifest元数据，也未压缩或删除旧历史，实际回收旧磁盘空间为0；收益用于估计今后同结构新输出的大小。

复权使用原真实50股完整因子及原日历，从统一 `factor-reuse` 入口返回 `ORIGINAL_FULL_WINDOW_AVAILABLE`，耗时2.88秒、退出0；requests、SDK和HTTP实际调用全部0。缓存前后均196,271,308字节，文件元数据清单相同。该记录证明原件复用，无跨日供应商请求，也没有验证全市场跨日尾窗可替代完整因子。

本机原写入配置仍为64 MiB缓存上限，已保存缓存约187.18 MiB；只读档位可验证原件，显式联网诊断仍会受较低原写入门禁限制。本轮没有把只读256 MiB额度转成采集授权，后续采集需单独核定有界写入配置。

最初一次复权控制文件读取误把正常变化的atime纳入身份比较，退出2并保持缓存不变；已修为安全文件描述符读取及七字段身份校验，新增回归通过后执行上述成功实测。失败记录保留，不把首次失败隐藏成成功。

### 工程检查

在发布检出的 `stock-fish/`、固定共享解释器下执行以下真实命令；所有样本使用独立DB/state/artifact及显式TEST_FIXTURE：

```sh
python -I -B scripts/run_isolated_tests.py -q \
  tests/unit/test_codex_optimization_entry.py tests/unit/test_codex_chat_units.py \
  tests/unit/test_codex_factor_incremental.py tests/unit/test_codex_compact_artifacts.py \
  tests/unit/test_codex_core_archive.py tests/unit/test_codex_core.py \
  tests/unit/test_codex_incremental.py tests/unit/test_codex_workspace.py \
  tests/unit/test_codex_capacity.py tests/unit/test_codex_premarket.py \
  tests/unit/test_codex_workspace_api.py
```

该组合259项及99项子测试通过，214.36秒，退出0。随后修复atime、补充压缩输入／导出、locator终验和外部监督，执行：

```sh
python -I -B scripts/run_isolated_tests.py -q \
  tests/unit/test_codex_factor_incremental.py tests/unit/test_codex_compact_artifacts.py \
  tests/unit/test_codex_compact_core.py tests/unit/test_codex_chat_units.py \
  tests/unit/test_codex_optimization_entry.py
```

该修订组合109项及49项子测试通过，45.02秒，退出0；两组存在重叠，不能相加为一次测试数量。覆盖实际POSIX子进程执行与复用、时间／空间超限停止、失败不自动重试、来源篡改及逻辑产物还原。随后最终独立审查补充的来源关联检查和新格式真实验收另记下方。

未执行全仓回归、Windows验收、全市场次日冷启动及完整67会话题材补齐基准、长期连续运行或投资有效性验证。原大库已被用户清理，无法报告清理前后的几十GB节省；容量上限用于拒绝继续超额增长，不等于可以无限保存每日结果。

### 最终来源关联检查与新格式落盘验收

最终审查进一步把原请求result/raw/receipt与输入typed receipts逐值绑定，并用原资格函数的小型身份投影重新核合格池，拒绝连计数、哈希和导出一起裁剪的伪结果。价格、normalizer及selector均不重算。发布检出再次执行 `python -I -B scripts/run_isolated_tests.py -q tests/unit/test_codex_compact_core.py`：21项及7项子测试通过，44.25秒，退出0。它与前面的组合有重叠，属于最终修订回归。

最终源码下，以既有7份真实采集、1只合格但窗口不完整的原小样请求，经统一 `core-run-compact --profile FULL_CORE_COMPACT_V1` 执行和再次复用，两次退出均0；没有发供应商请求。样本只验证工程，不作为新的完整盘前清单，其UNKNOWN、原input hash与资格结果保持。

| 最终小样 | 首次新格式运行 | 新进程重复调用 |
| --- | ---: | ---: |
| 墙钟 | 24.68秒 | 8.79秒 |
| normalizer／selector／供应商调用 | 1／1／0 | 0／0／0 |
| 八份原逻辑产物 | 5,343,480字节 | 复用同包 |
| 新压缩包（含8,275字节manifest） | 816,109字节 | 0新增大产物 |
| 共享data实际新增（含监管／鉴证元数据） | 914,819字节 | 15,228字节小监管记录 |

同次原逻辑文件与压缩包比较减少4,527,371字节，即84.73%；全部八份逐字节还原验证通过，只删除本次拥有的未发布暂存。小样包含较多重复的未知说明，不能将84.73%外推到完整市场；完整真实核心的32.27%只读测量更适合估计该结构的后续收益。前一候选实现的约0.89 MiB小样和报告保留为工程记录，最终测量使用修订后实现；没有覆盖历史run或将候选记录冒充最终验收。

最终保护核验：任务开始固定的524份原来源、冻结源码、旧产物及控制文件，SHA256、字节数、七字段物理身份全部一致。新旧格式分版本保存，不改原策略、旧run、未知状态或投资结论。没有删除旧真实资料，因此本轮“旧空间实际回收”为0；本轮新增鉴证和两次工程小样约2.03 MiB，收益体现为压缩今后新结果和避免重复大产物。
