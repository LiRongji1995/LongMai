# 历史闭环部分验收与剩余容量缺口（2026-10-20）

本次部分交付包含通过相应检查的工程改动及明确未启用的[盘前源码 bundle V2](PREMARKET_FACTOR_CHECKPOINT_SOURCE_BUNDLE_V2.md)。历史10月16日单 run 已在相同源码的隔离克隆中通过显式重试、幂等、三产品回读和独立 H1；双 run 容量及最新10月20日清单仍未完成。原当日和正式失败记录保持，未切换生产服务、活动库、locator 或调度。

## 证据范围与源码身份

本轮闭环冻结集合为16个源码/测试文件：6个产品模块及10个测试文件。涉及归档桥接、每日/历史闭环、后验编码、SQLite短读连接、作业取消与CLI；9个 normalizer 依赖文件以及 DAILY_V1、主动V2、R1/R2 的公式、阈值、配置、旧 run 和输入保持。单 run 成功与后续两次双 run 失败属于该相同源码集合，不把更早版本的成功拼接为当前全链通过。

历史模式为 `EXPLICIT_CLOSED_SESSION_REPLAY`，使用明确业务会话和历史 knowledge cutoff，沿用完整鉴证、来源钟、归档封存时间与原 evaluator。系统和作业的真实执行时钟不回拨；evaluator 显式以历史资料截止判断可见性和成熟度。原失败后的隔离克隆显式重试产生独立作业记录，旧 FAILED 及事件保留，不能追认为原当日准时交付。

## 工程检查

| 范围 | 实际结果 | 限制 |
| --- | --- | --- |
| 本轮16文件对应的23个测试文件组合 | 676 passed；pytest 742.24秒，外层743.588秒，退出0 | TEST_FIXTURE隔离；不是全仓回测、生产负载或投资验证 |
| 新 bundle 正式脚本与测试 | 32 passed；pytest 8.22秒，外层9.917秒，退出0 | 单独一次命令；无网络、真实采集或业务DB |
| 实际独立源码快照 | 创建退出0；后续两次verify、原源与bundle离线入口/身份烟测均退出0，续验5.345秒 | 166件含manifest；无真实capture、网络或DB，未验采集进程树 |
| 原件与工作区保护快照 | 1934个文件、319项非本轮 dirty 保护通过 | 对应检查时点；不授权覆盖之后的改动 |

32项不是676项的同次补跑，不将两次命令合称一次708项全套。测试使用明确TEST_FIXTURE及独立临时目录，保留首次失败和修正后的实际命令；未删除失败样本、放宽来源断言或以skip掩盖问题。

复现工具检查的模板（仓库根目录；`RUNTIME_PYTHON` 为已验收的独立解释器）：

```sh
cd stock-fish
"$RUNTIME_PYTHON" -I -B scripts/run_isolated_tests.py \
  tests/yaogu/test_premarket_source_bundle_v2.py -q
```

676项使用原记录的23文件清单运行。完整实际argv、源码清单、日志与退出码保存在私有工程回执，不发布本机路径、真实DB或行情。

## 同源码历史单 run：已完成的范围

| 阶段 | 实际状态 | 耗时与资源 |
| --- | --- | --- |
| 原已受理请求在隔离克隆中显式重试 | worker `SUCCEEDED`；三产品及完成ledger原子持久化 | worker 292.136秒；第4次完整终验于291.796621秒完成；峰值1,936,584,704 B |
| 幂等补充验收 | PASS，退出0；复用原成功job/revision，零新增文档/作业 | 193.859秒；峰值1,734,594,560 B |
| 正式回读补充验收 | PASS，退出0；三产品实际BLOB与API一致 | 93.763秒；峰值758,312,960 B |
| 独立H1数学与来源资格复核 | PASS，命令退出0；全池3041只H1 MATURE，研究集合25只 | 105.754秒；峰值1,459,118,080 B |

成功的第一次记录是诊断运行：外层退出0只表示诊断完成，业务成功另由 durable worker 状态、四次完整终验和提交后保护证明。随后独立的幂等、回读及H1补充验收已经执行并通过，因此不再将这些阶段写成“未运行”；原正式 final3 失败没有改判。

回读核对 outcome、tracking、review 三产品全部实际SQLite BLOB、公开API修订信息和保存内容；四份CSV与原导出正文逐字节一致（不含HTTP附加BOM），observation JSON核对完整值语义，不宣称JSON原始文本字节相同。四份父文档、原失败/成功作业与事件保持。H1使用独立精确Fraction计算，逐全池核对精确参考价、未来日线终盘、日期化身份、实际父来源和可见截止；该审计不重复全部RAW生产者鉴证，其来源能力以之前真实完整桥接为前提。

H2/H3/H5/H10及更多后续交易日没有在本轮完成真实成熟验收。H1 MATURE只表示该观察期限有合格事实，不代表可成交、扣费收益、策略优势或投资有效性；`MARKET_EDGE=NOT_ESTABLISHED`。

## 原失败与双 run 容量仍未解决

原正式 final3 单 run 在第3次发布前终验入口报时间预算，记录308.345629秒；后两轮完整终验未完成、四父不变、零新增文档。这是原正式失败；不能因后来的同源码克隆重试成功将其改判，也不能把超过300的8.346秒当成全部待省成本。

本次后续双 run 仅包含两份真实归档：一条旧 run 的H1到期、另一条 run 全5期限PENDING。不是两个成熟 run，也不是5条容量试验。

| 双 run 尝试 | 失败位置 | 保留状态 |
| --- | --- | --- |
| 正式提交后的worker | 两次认证终验完成于127.097406/268.534320秒，随后借用旧到期run并重算完整canonical hash时超过300秒；尚未进入evaluator | FAILED；零新增产品/ledger，原单run成果和四父保持 |
| 同一已受理请求的一次显式诊断重试 | 独立新进程，不执行plan；第2次认证终验于298.433095秒进入并触限，worker 300.023秒 | worker FAILED；外层诊断退出0；零新增文档，旧文档/作业/事件保持 |

双 run 幂等和回读未在失败后继续执行。新进程诊断仍失败，不能把plan残留大对象当作已证原因；当时其他进程负载也没有形成因果证明。不同运行的wall/CPU差异与嵌套计时不能直接相加或当作确定可节省时间。严格往返JSON候选的真实样本完整hash与守卫相等，但没有稳定CPU收益，未安装；顺序执行和同时离线烟测限制wall比较。其他解析、编码和生命周期候选仍待等价与完整实测，300秒批量缺口没有因此解决。

## 当前实现与不可省略的检查

1. **取消与状态短读。** 连接复用限线程/作用域，只有取消或状态读激活；每次仍新事务、完整迁移/索引/WAL及物理身份检查，并读取最新已提交取消态。5项PRAGMA每次读取，偏离时恢复；写连接独立，sleep与取消写在读scope外。
2. **显式编码profile。** 默认`TYPED_ROWS_128_V1`保持；闭环policy绑定`TYPED_ROWS_1024_V1`。TypeAdapter只处理精确模型、exact tuple及精确行类型，其余整输入走参考路径。所有canonical字节、非有限拒绝和迟变守卫保持。1024是行批次，C编码仍可能分配整段Unicode文本，不能称恒定内存或任意大小输入已受控。
3. **二次展开与强借用。** 首次严格JSON、完整原件、原normalizer重建和全文hash不变。已经鉴证的run仅能在同session、同store的私有proof下使用stdlib二次解码；读取前绑定已登记原引用，双父实际BLOB与来源继续重验，借出后保留强引用并在terminal重新计算完整hash。
4. **仅未到期非空池避免展开。** policy显式绑定`pending_run_materialization=AUTHENTICATED_NONEMPTY_V1`。仅5个期限全部精确PENDING、DUE/UNKNOWN为空、完整鉴证私有事实证明原池非空时，不借出该完整run。空池仍走原fullborrow/legal-empty检查；未知、到期、缺字段或身份不一致不能获得该豁免。audit保留精确投影及实际借用身份。省的是未暴露大对象与重复hash，不是源文件、双父全文或终验。
5. **提交窗口继续全验。** 保留发布前完整terminal，以及产品和ledger写入后同一事务内的完整terminal；迟变、取消、预算或写入失败整批回滚。ledger内审计是发布前快照，不冒称记录了之后的第4次终验。

计划和worker各自完整鉴证预算保持300秒、RSS 2 GiB、唯一原件4 GiB、实际读取8 GiB、4096文件。外层等待360秒或子进程watchdog900秒是调度/收尾限制，不扩大worker预算。检查是合作式，超限可能在下一检查点抛出；不据此声称严格300秒CPU抢占。

事务末终验持有同一写事务锁；并发写入、锁等待与常驻服务响应尚未经过生产负载验收。隔离worker耗时不是响应SLA。源码身份改变可能使旧凭证不兼容，必须保留旧归档依赖的精确producer路径及字节，不能默默重绑或降低来源合同。

## 10月20日盘前：没有可信新清单

用户明确授权在剩余原预算内重试一次。目录和当日日线已有COMPLETE缓存，但一个复权PARTIAL因缺RAW `first_seen_at` 在核心计算前被原守卫拒绝；父超时结果不因若干逐证券COMPLETE而升级，缺钟不回填。该次14个请求、外层980.369秒、退出3；终态capture计量读取1,561,630,437 B。HTTP/SDK实际调用仍有未知，不能把已知小计或未直接调用字段写成精确零。

core、题材、报道和展示未运行，没有生成可信10月21日清单或新run；全量/焦点数量为未知，不是合法空集。旧日期名单不改标，原失败、配置和原件保持。未来[源码bundle V2](PREMARKET_FACTOR_CHECKPOINT_SOURCE_BUNDLE_V2.md)的离线测试不为旧partial补钟，也没有执行新的真实采集。

## 后续验收顺序

| 缺口 | 下一步应满足的验收 |
| --- | --- |
| 当日可信输入与新bundle真实可用性 | 独立源码快照已构建并离线核验；尚需显式网络许可和原预算内真实capture，保留旧producer路径，不能沿用缺钟partial或旧日期名单 |
| 完整每日与双run仍超300秒 | 定位实现开销，保持完整来源/双父/借用终验，在原预算下完成两份真实归档的run、幂等、回读与失败原子性检查 |
| 两个及以上成熟run | 单独验证大对象同时存活、全池全期限与提交锁成本；默认5条不是容量证据 |
| H2+与连续交易日 | 使用每个实际可见截止与日期化资格，未知不计零，不倒填历史资料 |
| 常驻服务和多平台 | 另验并发、锁等待、取消、恢复、Windows及连续运行；不由本轮部分交付自动切换 |

工程检查、真实事实与投资结论保持分列。系统不连接券商或下单；初始策略参数仍尚未验证。

## 私有复核定位

脱敏正文可公开；以下仅是私有回执文件名，不要求发布真实资料：

- `publisher-final3-regression-command.json` / `publisher-final3-regression.log`：676项与源码集合。
- `final3-clone-gc-diagnostic.json`：历史单run显式重试、4次terminal及durable状态。
- `final3-clone-idempotence.json`、`final3-clone-readback.json`、`final3-clone-h1.json`及各自command回执：独立补充验收。
- `clone-two-20261020-2-run.json`、`clone-two-explicit-retry-gc-v3.json`：两次双run失败和零新增保护。
- `explicit-retry-final-status.json`、`explicit-retry-partial-clock-diagnosis.json`：10月20日资料缺口。
- `source-bundle-formal-final-manifest.json`：新工具的32项正式路径测试；`source-bundle-real-02-result.json`：实际源码快照与离线验证。首次`source-bundle-real-01-result.json`烟测超时保留，不改判。
- `gzip-roundtrip-real-micro-v1.json`：未安装的严格往返JSON候选，仅样本等价检查，不是完整认证或性能提升证明。

更早当日尝试完整保留于[原执行记录](REAL_DAILY_ACCEPTANCE_PERFORMANCE_V1.md)，环境与归档操作见[原闭环交付](RUNTIME_R2_CLOSURE_DELIVERY_V1.md)，现状与部署分别见[当前状态](../../CURRENT_STATUS.md)和[部署说明](../../DEPLOYMENT.md)。
