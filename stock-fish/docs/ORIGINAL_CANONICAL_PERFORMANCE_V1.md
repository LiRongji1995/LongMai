# Original 完整编码与认证性能 V1

2026-10-29。本轮为明确授权的工程优化和离线测量。新执行版本位于 `scripts/`，原 `stockfish_core`、九个 normalizer、producer、策略、配置、历史 run 和失败记录保持。源码发布不修改默认入口、私有登记、预算、服务或调度。

## 实现范围

`codex_premarket_fast_v1.py` 将已有 bridge 的完整 canonical C 编码和 `TYPED_ROWS_128_V1` 接到原生 original 工作流。每次仍完整计算原 hash，没有可变对象 hash 缓存、跨请求来源缓存或认证删减。精确 MarketInput、tuple 和原行类型才使用批量路径；其他类型继续原参考 fallback。所有 Fraction、顺序、UNKNOWN、来源时钟、原件 SHA/七字段身份、借用对象和事务合同不变。

编码范围只允许单线程，由独占子进程承载。进入前验证原函数绑定，退出时验证绑定及源码全文，再恢复所有已有与动态导入的别名。异常也恢复。首次进入或最终认证失败保留真实失败 completion；不得将已经写出的中间产物作为完整成功。原 phase budget、进程树监督、真实退出码和父进程终验继续执行。

`codex_daily_fast_v1.py` 是显式登记入口，复用 V2 的原准入、共享资源锁、run_once、正式回执与交付验证。普通默认继续 `codex_daily_v2.py` 和 `--engine original`。新入口也只允许 original；同 request-key 或同观察日/目标日的其他请求不能借新版本或新 key 重试。`--recovered-runtime` 只加载既有恢复登记并检查祖先请求，不新建 epoch、不修改旧 default。

```sh
"$REGISTERED_PYTHON" -I -B -X "pycache_prefix=$FRESH_ABSENT_PATH" \
  scripts/codex_daily_fast_v1.py --runtime "$RUNTIME" premarket-plan
```

只有原准入通过且是获准的新请求，才以相同入口、同登记和固定 request-key 调用 `premarket`。已失败/不明请求仍须独立显式恢复协议；本工具不授予恢复权限。原封存结果仍用 `codex_daily.py show`。

## 独立候选

`codex_capture_decoded_v1.py` 保留原采集验证器的所有 fresh 读取、完整 SHA 和物理检查，只在一次 `_authenticate` 内复用未外借的严格 JSON 解码对象。不同字节、读取身份变化、BOM 和未知调用路径保持原拒绝/参考行为，调用结束清空。它不会缩短提供方网络等待。

`codex_theme_grouped_v1.py` 固定原题材执行源码 SHA，对候选遍历作受守卫的局部 AST 变换，使用按 theme_id 分组的原序 tuple。全部原谓词、resolver 调用顺序、67 会话、数学及 journal 终验保留，新增独立执行身份。结构扫描计数不代表耗时改善。

这些候选分别测量，不因工程测试通过就接入默认服务或宣称全市场提速。

## 真实性能实验合同

`benchmark_premarket_stages_v1.py` 定义明确的历史来源离线阶段实验合同；本轮完整 core 复测使用 `benchmark_premarket_stages_v2.py`。每个臂重新完整认证保存 capture-summary 中的真实来源，调用原 native core 发行新 owner；不从保存 JSON 恢复来源能力，不复活失败作业，不联网、不写业务 DB 或原 state。

观察日及目标交易日保持原值；所有臂的 evaluation cutoff 是实验计划创建时实际取得的同一个 UTC 时刻。每臂生成时间仍取实际时钟，没有冻结系统时钟或回填历史可见性。比较完整 input、run_id、全部策略结果和原因、完整 CSV、正式 JSON 与来源。只有验证真实生成时钟及其 canonical hash 绑定后，才单列这些不同的生成时间/派生 hash；不泛删 timestamp/hash 字段。

实验使用独立新进程、全新字节码查找路径和显式 ABBA 顺序。每臂使用同一原配置预算；`--arm-output-mib` 只能收紧原 output limit。报告阶段 wall/CPU、进程 wall、包含式编码计时、实际返回读取、RSS、输出及比较开销；包含式计时不能求和，返回读取不等于内核磁盘 I/O。失败立即停止后续臂，不自动再试。

```sh
"$REGISTERED_PYTHON" -I -B -X "pycache_prefix=$FRESH_ABSENT_PATH" \
  scripts/benchmark_premarket_stages_v2.py run \
  --config "$ORIGINAL_CONFIGURATION" --capture-summary "$ORIGINAL_CAPTURE_SUMMARY" \
  --scope core --order reference canonical canonical reference \
  --arm-output-mib 128 --output "$NEW_PRIVATE_EXPERIMENT_DIRECTORY"
```

V2 只接受 `core`。它在原阶段计时和所有原生终验结束后，快照普通文件引用、销毁真实 owner，并确认 owner 弱引用已死亡（夹具另验证大输入对象同时释放），再调用完全相同的 V1 文件比较；没有从文件恢复来源能力。释放耗时计入阶段外比较，预算、阶段计时、原输入/时钟/hash 比较及发布回滚不变。V1 原字节和旧失败保留。

V1 的 `core-theme` 支持另需真实完整题材来源摘要及67会话对照，本轮没有该范围的真实验收。core 实验不包含采集、新闻、完整题材和聊天网关，不将其结果称为新清单端到端提速。

## 独立来源认证测量

`benchmark_capture_decoded_v1.py` 对原 capture-summary 的全部 core_records 执行 `reference/candidate/candidate/reference`，要求显式记录总数且不能筛样本。每个新进程仍调用完整 `verify`、`terminal` 和结果 canonical hash；证明必须同时匹配记录顺序、全部返回内容、原件引用及实际返回读取量。只读锁协调现有 writer，原 cache 目录只检查而不重写，采集、seed import 和缓存写入均禁止。

所有臂预算只从原配置收紧，四臂共用总预算；默认每臂至多900秒。发布前后重新检查全部来源七字段身份，迟变撤销成功回执和比较资格。这里测量的是已保存来源的认证，不是网络采集阶段。

```sh
"$REGISTERED_PYTHON" -I -B -X "pycache_prefix=$FRESH_ABSENT_PATH" \
  scripts/benchmark_capture_decoded_v1.py run \
  --configuration "$ORIGINAL_CONFIGURATION" --capture-summary "$ORIGINAL_CAPTURE_SUMMARY" \
  --expected-record-count "$ORIGINAL_CORE_RECORD_COUNT" --arm-seconds 900 \
  --output "$NEW_PRIVATE_EXPERIMENT_DIRECTORY"
```

两种真实实验都从原登记源码和解释器执行，输出使用与源码、原业务 state 分开的新私有本地目录。源码、运行环境、原件或输出身份在实验中变化时，保留失败记录，不放宽身份检查以获得成功。

## 测量与交付状态

### 全量保存来源认证

2026-10-29 UTC，以原登记 Python 3.12.14 对观察日2026-10-26、目标日2026-10-27 的完整119份 core_records 作只读 ABBA，退出0，四臂成功。没有筛选记录；360件来源引用、119项完整返回内容、记录顺序和每臂496,728,312字节的返回读取计量完全一致，所有外部请求计数为0。

| 指标 | 原版两次 | 解码候选两次 | 原版中位数 | 候选中位数 | 本次耗时下降 |
| --- | --- | --- | ---: | ---: | ---: |
| 完整认证范围 wall（秒） | 30.2563 / 16.6815 | 21.9198 / 17.8546 | 23.4689 | 19.8872 | 15.26% |
| 完整认证范围 CPU（秒） | 18.3648 / 15.5377 | 16.7953 / 15.6695 | 16.9512 | 16.2324 | 4.24% |
| verify 调用 wall（秒） | 23.8348 / 12.5131 | 16.7505 / 12.9675 | 18.1739 | 14.8590 | 18.24% |

完整认证范围包含所有 verify、完整结果编码及 terminal；verify 子指标不能与之相加。候选每臂实际复用238次 JSON 解码，保留476次 native fresh read，原始数据最大同时保留1,849,383字节；这些计数不用于推算全市场加速。

原版两轮波动明显，最后一轮原版比最后一轮候选更快。因此15.26%只描述该机器、这批来源、这一次ABBA的中位数，不是稳定收益或置信区间；本轮没有把解码候选接入默认工作流。题材分组仅完成等价性及边界检查，没有真实耗时结论，也未自动启用。

### 核心阶段

V2 以同一完整119份历史来源、25个核心会话完成 `reference/canonical/canonical/reference`，四臂全部成功并回收，CLI退出0。共同实际截止为 **2026-10-29T16:10:02.115035+00:00**；观察日2026-10-26、目标日2026-10-27 和 `RECONSTRUCTED_HISTORICAL` 保持，未重试原盘前请求。

| 指标 | 原版两次（秒） | 优化两次（秒） | 原版中位数 | 优化中位数 | 本次耗时下降 |
| --- | --- | --- | ---: | ---: | ---: |
| 核心阶段 wall | 397.6582 / 390.0172 | 311.2273 / 307.1678 | 393.8377 | 309.1976 | **21.49%** |
| 核心阶段 CPU | 373.7055 / 371.3335 | 295.6642 / 294.3366 | 372.5195 | 295.0004 | **20.81%** |
| worker 内全部区段 wall | 446.4362 / 436.2482 | 363.0218 / 354.8904 | 441.3422 | 358.9561 | 18.67% |

核心阶段每次中位数节约 **84.64秒**，速度比 **1.274倍**。范围包括原 native core、原 owner 校验及编码作用域终验；所有臂使用相同计时边界。worker 内计时另含前置读取及独立比较，不包含解释器启动和父进程最终回执发布，不能称完整盘前或完整封存耗时。独立比较各臂另用43.83～49.37秒，其中对象释放约1.02～1.10秒，均未计入核心阶段提速。

四臂的完整来源、完整 MarketInput、run_id、所有策略字段/原因、正式CSV单元及顺序、正式视图和正常化报告均通过对照；只排除已验证的真实生成钟及其派生hash。每臂仍调用完整输入哈希8次、JSON哈希12次，没有复用成功哈希。全部HTTP/SDK/request计数为0；UTC起止与阶段秒表最大绝对差约11毫秒，没有观察到休眠造成的计时断层。

worker 记录的峰值RSS（含比较）：原版最高2,065,768,448字节，优化版最高2,088,513,536字节（约1.92/1.95GiB），均低于原2.5GiB上限。优化版峰值略高，不能宣称降低了生产峰值内存。每臂产物约47.53MiB；相同128MiB产物上限仅从原预算收紧，其他限额未扩大。返回读取计量不代表内核磁盘IO。

这是同一机器、同一完整历史来源批次、每种版本两次测量的结果；没有控制操作系统冷缓存，也没有验证新日期、全部市场场景或整个实时盘前链路。编码优化通过显式 `codex_daily_fast_v1.py` 提供；解码和题材分组仍为独立候选。

### 失败记录与修正

- 第一次 V1 实验的首臂在计算前因 `ARCHIVED_REFERENCE_CHANGED` 失败。Documents 中独立 TEST_FIXTURE 探针实测写后 `st_flags` 从0变64且 ctime改变；原777件引用的内容与指纹随后核对无变化。新实验改用独立私有本地 state 输出，保留失败，不放宽七字段检查。
- 本地 V1 实验前两臂成功，第三臂核心阶段完成后，独立比较因 `COMPARISON_RSS_LIMIT` 失败；worker 峰值2,690,605,056字节超过原2,684,354,560字节上限，第四臂未运行，整体退出4。此前披露的501.53和483.82秒只属未完成对照中的局部记录。V2 通过释放已结束的原生对象降低比较峰值，预算保持。
- 发布组合发现新编码作用域遗漏 `scripts.*` 懒导入别名还原，导致随后旧 shared 差分使用过期预算。已在新脚本中补全项目模块绑定还原，并增加正常/异常/预算耗尽和脚本迟变回归；旧核心及 shared 字节保持。修正前新脚本字节、SHA和7FP留作私有审计，不作为来源能力。
- 另一轮组合发现继承的 TEST_FIXTURE 测试在上海午夜用“当前时间加一分钟”造成业务日与模拟 first_seen 不一致。只在新测试中固定该夹具的三处时钟，调用原测试保留全部断言，并加入原版/候选跨日必须拒绝且不重采的两项反例；真实实验不冻结时钟。

此前0.8秒封存查询、shared小夹具回退、历史3小时超时和默认300秒后验未达标均保持各自原结论，不移作本次性能验收。

## 发布检查

在发布检出的 `stock-fish/` 目录，使用原登记 Python 3.12.14、`-I -B`、全新未存在的 pycache 路径和独立 `TEST_FIXTURE_publish_verified` 目录执行下列组合：**218 passed in 196.22s，退出0**。其中包含旧 original/shared 完整流程差分、新 original 编码入口、登记防重、解码认证、两版阶段测量、题材分组，以及失败、迟变、绑定还原和跨午夜反例。

```sh
"$REGISTERED_PYTHON" -I -B -X "pycache_prefix=$FRESH_ABSENT_PATH" \
  scripts/run_isolated_tests.py -q --basetemp "$NEW_TEST_FIXTURE_DIRECTORY" \
  tests/unit/test_codex_premarket_fast_v1.py \
  tests/unit/test_codex_daily_fast_v1.py \
  tests/unit/test_codex_capture_decoded_v1.py \
  tests/unit/test_benchmark_capture_decoded_v1.py \
  tests/unit/test_benchmark_premarket_stages_v1.py \
  tests/unit/test_benchmark_premarket_stages_v2.py \
  tests/yaogu/test_codex_theme_grouped_v1.py \
  tests/yaogu/test_premarket_shared_workflow_v1.py
```

两组成功ABBA结束后，额外只读终审825件来源、源码、控制和产物引用，完整读取654,180,725字节，SHA与七字段身份全部通过；该普通文件审计不恢复来源能力。

真实实验使用原业务来源及独立私有输出；工程测试只使用 TEST_FIXTURE。没有执行全仓测试、生产新日采集、完整实时盘前计时、67会话真实题材性能、回测或投资有效性检验。公开仓库仅交付代码、测试和汇总文档，不包含私有测量原件、配置、运行产物或完整审阅包。

没有修改策略参数或新增投资验证，`MARKET_EDGE=NOT_ESTABLISHED`。
