# 最新日采集与首次核心计算 V1

2026-10-26。本轮增加三个显式执行入口：最新已闭市日的有界采集、25日核心来源先行、首次核心的等价编码。它们沿用已登记共享环境和原来源，不复制数据库，不覆盖历史原件；冻结策略、资格UNKNOWN、完整复权要求和67会话题材数学不变。当前SAM V2、DAILY_V1与显式R2的使用边界保持。

## 聊天入口与执行顺序

所有命令从登记源码目录、登记Python执行。`--runtime`、`--registry-root`为聊天入口全局参数。下面大写路径和日期均须替换为核验后的真实值。

```sh
python scripts/codex_chat.py --runtime shared-daily latest-plan \
  --mode market-session --profile LATEST_MARKET_SESSION_V1 \
  --calendar-record ORIGINAL_CALENDAR_METADATA.json

python scripts/codex_chat.py --runtime shared-daily latest-sync \
  --mode market-session --profile LATEST_MARKET_SESSION_V1 \
  --calendar-record ORIGINAL_CALENDAR_METADATA.json --execute-network

python scripts/codex_chat.py --runtime shared-daily core-sources-plan \
  --profile CORE_SOURCES_FIRST_V1 --identity-policy NATIVE_CORE_UNKNOWN_PRESERVED_V1

python scripts/codex_chat.py --runtime shared-daily core-sources-build \
  --profile CORE_SOURCES_FIRST_V1 --identity-policy NATIVE_CORE_UNKNOWN_PRESERVED_V1

python scripts/codex_chat.py --runtime shared-daily core-first-run \
  --manifest ORIGINAL_CORE_REQUEST.json --profile FIRST_CORE_CANONICAL_128_V1
```

先计划和核缺口，再按现有授权与显式预算采集。最新行情完成不等于核心来源齐全：官方身份、完整复权或25日窗口仍缺时，来源构建拒绝并列出缺口。不能从已有23日结果推断26日就绪。核心完成独立保存；题材和新闻附件另报UNKNOWN，补全原67会话来源后才能运行原题材算法。

普通日常调用不传 `--force-first`。此参数仅用于明确的有界工程首次A/B；同一ID和profile仍保留成功复用、失败不自动重试。已有保存结果继续使用原归档鉴证入口。

## 最新交易日的原始采集

[`codex_latest_capture.py`](../scripts/codex_latest_capture.py)依据认证原日历和实际上海18:00门限确定最新完成日，结束时复核日期。全市场模式对 `market_daily`、`master_all` 各最多一个原请求，已存在原件先完整鉴证复用。它不补历史67日、不刷新全部股票完整因子，也不把行情采集完成标为DAILY_V1就绪。

小范围历史模式最多两只主板证券、三个真实交易日。只有缺失证券集合相同、且在原交易日历中相邻的日期才合并；周末可以跨过，缺失交易日不可跳过。原完整range响应保留，各日仅建立原件引用，不制造逐日RAW或receipt。

```sh
python scripts/codex_chat.py --runtime shared-daily latest-sync \
  --mode scoped-history --profile LATEST_SCOPED_HISTORY_V1 \
  --calendar-record ORIGINAL_CALENDAR_METADATA.json \
  --codes CODE_A CODE_B --sessions SESSION_A SESSION_B SESSION_C --execute-network
```

三个连续缺失日、两证券可以由3请求/12次SDK预约合为1请求/4次SDK预约。单日全市场本来就是两个请求，不能据此宣称单日请求数下降。Baostock原跨进程会话锁保留，没有通过盲目并行提高下载量。

两个显式新档位将该操作的原cache上限定为256MiB，单次新增分别限4MiB/16MiB，单请求90秒、整次240秒、进程RSS512MiB、进程树768MiB。原登记的共享总容量和磁盘最小剩余量继续生效；旧64MiB操作配置文件没有被修改。网络需要显式 `--execute-network`，注册offline规则仍优先；失败、未完成和不完整来源保持，不自动重试。

原SDK没有网络流量计量，因此只报告实际请求、SDK记录、归档文件字节和磁盘增长，不把文件体积称为网络下载字节。增长上限与原供应商响应限制共同准入；超限终止自有进程树并保留失败证据，不删除历史腾空间。

## 核心来源先行

[`codex_core_sources.py`](../scripts/codex_core_sources.py)严格离线，一次扫描有限缓存索引。同次调用只对选中的原件做一次语义鉴证，结束仍逐文件全SHA及物理身份终验。来源变化或未完成请求不能被普通mtime缓存掩盖。先认证日历及最新日market/master，再核当天官方目录；前置来源缺失则立即报告，后续历史、资格和因子标为NOT_EVALUATED。资料齐备才走完整25日鉴证，不用浅检查授予成功。

原R2核心需要最后25交易日的行情/身份，题材附件需要67会话。新来源入口保留完整67会话日历，只把另外42日的市场/身份补采移出核心前置步骤；最多84个请求不再成为核心的先决条件。这是依赖顺序的改变，不是缩短题材算法窗口，也不代表每次实际省去84次联网。

`NATIVE_CORE_UNKNOWN_PRESERVED_V1`沿用既有原生核心允许未知资格的路径，保留全部RAW和UNKNOWN，不把缺上市日期的证券变成合格、不删掉未知池。旧collector的更严格资格就绪及其缺口另列。主板完整历史复权仍要求齐备，尾窗没有升级为完整因子。构建只输出原格式core-request及小型证据，真正核心仍由原normalizer和selector执行。

`--as-of ORIGINAL_AWARE_CUTOFF`仅用于显式历史回放。来源以实际首见/完成时间核可见性，结果注明历史回放；不传则核实际最新日及当前目录。旧资料不得改日期冒充最新。

## 首次计算的等价编码

[`codex_first_core.py`](../scripts/codex_first_core.py)在独立受监督子进程中调用原压缩核心。`FIRST_CORE_REFERENCE_V1`使用原编码，`FIRST_CORE_CANONICAL_128_V1`使用已有桥接模块的C JSON编码和128行typed批次。新执行身份加入档位和新协调源码引用，不能命中旧compact操作来冒充首次计算。

每次仍完整计算输入/run哈希，无可变对象哈希缓存。normalizer、selector、全池排序、原NativeCore owner、来源终验和正式导出不替换。精确类型之外走原编码fallback，退出恢复所有绑定；原策略和旧源码文件的字节、物理身份保持。C编码会分配整段Unicode文本，不能称恒定内存；原900秒、进程2.5GiB、进程树3GiB、原/新输出峰值准入继续有效。

新结果沿用上一版压缩保存，压缩收益不能再次算作本轮新增收益。旧文件没有被重压缩或删除，因此本轮历史回收空间为0。

## 验证记录

真实联网仅使用两只证券（sh.600519、sz.000002）和2026-10-22、23、26三个交易日。同90秒单请求预算下，分日与合并取得的6行原始响应逐字段一致。分日参考使用独立的小型owned cache，只引用原日历，不复制共享行情库；该对照使用原分日分组算法，不能说原30秒默认档位本身完成了数据获取。

| 本轮真实采集对照 | 分日参考（相同预算） | 连续日期合并 |
| --- | ---: | ---: |
| 实际原请求 / SDK调用 | 3 / 12 | 1 / 4 |
| 原请求采集累计耗时 | 113.96秒 | 46.65秒 |
| 采集scope耗时 | 116.35秒 | 56.79秒 |
| 完整测量命令墙钟 | 120.09秒 | 66.80秒 |
| 原result/RAW/receipt合计 | 14,315字节 | 7,173字节 |
| cache新增（含索引等） | 34,356字节 | 14,551字节 |

本次命令墙钟减少44.37%，cache新增减少57.65%；供应商网络波动、分日小cache与共享cache扫描、监督包装的差异均包含在单次观测中，不承诺每日固定加速。单日全市场请求数没有下降，也未用此样本证明全市场端到端提速。SDK不计网络传输量。

在此之前，原30秒档位三个分日请求全部超时，94.37秒后得到PARTIAL、0行；原失败及来源记录完整保留。这次失败不计入成功对照的节省比例。同预算工程对照另立明确执行身份，未覆写旧失败或修改旧超时配置。本轮合计7个小型原请求，其中3个失败，成功对照分别6行，未下载全市场新数据。

首次计算对照使用同一已存真实小输入，按参考/优化/优化/参考顺序运行四个独立操作：每次normalizer一次、selector一次，provider零次；不是重复查询。四次输入哈希相同，input及四份CSV字节完全一致，完整run和observation JSON除实际生成时间及关联哈希外一致。该输入只有一个合格UNKNOWN、没有完整25日窗口，属于工程小样，不能当作最新完整盘前名单或投资验证。

| 真实首次计算，两个观测的中位数 | 原编码 | 128行等价编码 |
| --- | ---: | ---: |
| 两类编码CPU合计 | 2.292秒 | 1.077秒 |
| 核心worker监督范围 | 17.108秒 | 16.259秒 |
| 完整聊天入口墙钟 | 21.320秒 | 22.491秒 |
| 每次共享数据新增 | 921,568.5字节 | 921,664.5字节 |

编码CPU减少53.01%，但小样完整入口墙钟反而增加5.49%；本轮不能宣称首次清单整体提速已被证明。原件终验、启动、归档回读等仍保留，整条路径继续是后续优化对象。仅对已存完整真实输入/结果做的只读局部基准也得到等价哈希，无新下载或全市场重新选股，不能把其局部倍数推广成整体倍数。

源码与旧证据保护、最终工程回归及实际当前来源计划另外留账。所有真实原件、失败记录及完整测量命令保存在私有reports/codex目录，不进入Git；未执行全历史回测、连续自动交付或投资有效性验证。

真实最新日预检另测了当前26日全市场两份原件缺失的情形：相同两个缺口，旧候选先鉴证56份原件耗时131.62秒，新路径只消费原日历后早停，7.11秒，减少94.60%，零网络、零磁盘增长。未检查的资格、因子和旧24日状态保持NOT_EVALUATED；这只是更早报告缺口，不是完整盘前生成速度。

原冻结策略和1,407份既有文件均通过内容SHA及七字段物理身份保护检查；本轮共享数据目录从269,376,916增至273,508,126字节，净增3.94MiB，包含四次首次计算、成功/失败小样和监督证据。共享数据加已有解释器约552.97MiB，原1GiB总容量与10GiB剩余磁盘门槛不变；不自动删除历史或扩大总预算。

发布检出组合回归命令（登记Python、`-I -B`）：

```sh
python -I -B scripts/run_isolated_tests.py -q \
  tests/unit/test_codex_first_core.py tests/unit/test_codex_core_sources.py \
  tests/unit/test_codex_latest_capture.py tests/unit/test_codex_first_run_entry.py \
  tests/unit/test_codex_optimization_entry.py tests/unit/test_codex_incremental.py \
  tests/yaogu/test_premarket_native_core_v1.py
```

该组合122项、59个子断言通过，230.90秒，退出0。早期组合检查发现的专有暂存目录删除竞态已修复并重验：仅该暂存成员消失允许最多三次完整容量重读，旧来源或归档丢失仍失败。最后追加来源早停后，单独运行 `tests/unit/test_codex_core_sources.py tests/unit/test_codex_first_run_entry.py`：22项、5个子断言通过，93.30秒，退出0；与上述范围重叠，不相加虚增覆盖数。
