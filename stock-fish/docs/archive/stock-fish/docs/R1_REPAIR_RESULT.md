> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../../../CURRENT_STATUS.md)；原路径可在[归档索引](../../README.md)查找。

# R1 四项口径修复、覆盖诊断与执行/统计接线结果

MARKET_VALIDATION_STATUS=INCONCLUSIVE；MARKET_EDGE=NOT_ESTABLISHED；production_promotion=DISABLED。

后续发布授权：用户在本报告完成后明确要求“推送代码到远端仓库”。据此将本次14个源码、测试及文档文件提交至既定远端main；下文“未提交/未推送”及HEAD记录描述修复验收完成时的状态。发布前复核21个冻结源码/配置文件哈希与最终测试时完全一致。私有研究产物、数据库和日志不随提交发布；不部署或切换运行服务。

本轮实施了研究合同、冻结执行规格、归档证据适配、共享输出与统计命令接线。工程验收与真实市场验证分开：真实冻结输入仍没有确定入选，且398只仍不可确定选择，因此不能提供完整策略收益，更不能把它解释成成功空仓。

## 工作区与授权

起始 HEAD 为 `8990e5a00312e7ceaa67b7953840142a402bd36c`，分支 `review`，工作区干净。origin 为 `git@github.com:LiRongji1995/stockfish-qlib-quant-system.git`。本任务较新的“不提交、不推送”指令优先；未切分支、创建worktree、提交、推送、部署、启用调度、修改生产数据库或采集行情。

实际读取了用户粘贴任务、两级 AGENTS、部署/工程基线、原R1/V2规格、历史分片/身份/后验合同。指定的独立审阅Markdown/zip没有在附件目录找到，不以此阻塞。所有原始日志、运行输出、hash、基准脚本和合成SQLite在忽略的 `review/r1-fix-evidence/` 及 `review/r1-fix-*`；未复制真实7.2GB快照库。

## 分项交付

|项目|状态|验收及边界|
|---|---|---|
|F01 可评价与决策分离|IMPLEMENTED_AND_TESTED|根据有效依赖与三态短路计算核心支持和完整选择可判定性；WATCH不是缺资料的替代标签；新增支持标明同输入条件需求变化|
|F02 公共/形态漏斗|IMPLEMENTED_AND_TESTED|公共叶子按证券×信号日只计一次；保留PASS/FAIL/UNKNOWN/NOT_EVALUATED；交集不截断，终态互斥对账|
|F03 覆盖与执行完整性|IMPLEMENTED_AND_TESTED|候选、总体、选择、必要/背景覆盖、执行、完整策略评价分离；一个PASS加一个UNKNOWN只能保留observed_subset收益|
|F04 冻结规格驱动引擎|IMPLEMENTED_AND_TESTED|200万元/5%实际预算10万元；题材、偏移、滑点、最低费用、种子/块长/次数接线；未知或固定政策变更拒绝|
|有界历史读取与共享输出|IMPLEMENTED_AND_TESTED|80万条真实临时SQLite写读；1000只完整声明总体实际研究流程成功；按日期释放，共享哈希分片可重建，预算失败不发布完成结果|
|真实覆盖诊断|IMPLEMENTED_AND_TESTED|复核原input hash、cutoff和history hash；逐字段窗口、来源及缺口样本保存，数量与原报告一致|
|真实执行适配|IMPLEMENTED_REAL_DATA_NOT_VALIDATED|实际归档JSON→SourceReceipt/字节哈希/成员/时点校验→订单→数量/成交→退出/费用已通过隔离集成；真实来源档案不足，没有真实成交样本|
|统计接线|IMPLEMENTED_AND_TESTED|实际40日CLI产生可用账户日期块统计；完整预登记族Holm，父组和主比较、账户/条件信号/共同与新增范围/setup分离|
|真实策略收益与市场验证|BLOCKED_DATA|398只选择仍未知；缺完整历史、限价与未来执行证据；无独立留出，不能得出市场优势|

企业行动账务转换（分红/拆并股）、券商真实成交、真实佣金识别仍未实现，也不属于本轮最小支持路径。适配器只支持完整覆盖区间确认无行动的原始开盘场景；未知或有行动均保留原信号、缺口、未完成状态和资本占用/承诺。未从样本中事后删除。

## 四项反例与原规则保护

先新增四个项目内pytest回归，原代码实际结果为 **4 failed，退出1**；修复后通过。F02/F03进入真实 `run_local`→保存JSON流程；F04同时验证引擎与run_local的manifest/账户预算。F01包含纯依赖反例、资金/核心未知/已知硬失败，以及明确标识的上游日内路径反例经真实报告链路；该上游替身不被冒称为真实市场集成证据。

补充逐项卡条件的消融样本，包括换手、趋势、市场、供应、题材/资金四格、ACC替换、独立形态和FULL单独的未知跌停保护；不是只比较配置字典。一个setup完整通过不会被其他未知setup否定，公共硬失败可短路无关形态。

原 V2 golden 未重写。另从本机原 `r1-accepted-fixture`（核验原manifest）生成 `r1_frozen_decisions_8990e5a.json`，逐组固定原items、选择、counts、状态、options及配置hash；当前12组决策与理由逐字段摘要相等。历史绑定fixture还比较原预加载V2与研究缓存路径的完整输出，二者相等。

原V2实现、共享历史/存储原模块、validation identity、正式评分、默认策略配置、原R1配置和旧报告保持原字节。默认参数仍为100万元、20%/40%、T+1/T+6、严格早于上海09:15、0/10/30bps及主10bps，20会话块、2000次、种子20261001。现金不计利息。测试覆盖值不是新默认值或优化结果。

新增身份是 `R1_FACT_SELECTION_COVERAGE_V2`、`R1_EFFECTIVE_EXECUTION_SPEC_V2`、`R1_ARCHIVED_EXECUTION_EVIDENCE_V2`、`R1_SHARED_RESULT_V2` 与 `R1_PAIRED_RESULT_STATISTICS_V2`。新增helper全部在研究源码hash覆盖内；manifest显式登记比较族与含成本的有效执行规格。旧结果不改写；运行期间源码变化会拒绝发布。

## 原真实输入的覆盖结论

观察日 `2026-09-30`，资料截止 `2026-10-01T16:03:42.393321+00:00`；是用实际晚到资料重建，不是当日已发布的前瞻记录。

- input hash：`10d0ed728aa90abeb78dae0ce3637e166eb2f48459d2cf03c9b8c59e08c73e13`。
- history hash：`791cf216161ca6a3c64ac5ac18c4ebf75cf77b3dec869838a81d23b0afc0ca1f`。
- 原manifest只有2026-09-30一个分片，9212行：4606条价格及4606条身份。不是读取上限裁掉了旧历史，也不能据此推断其他档案从未采集过。
- 总体4606：合格3040，已知排除1566，资格未知0。排除由主板普通A股范围1409、ST145、上市会话不足6、当日停牌6构成。
- R1：FAIL2642、UNKNOWN398、PASS0；V2：REJECTED2926、UNKNOWN114、FOCUS0。共同选择可判定2642，R1新增0；另284只是V2可确定排除而R1仍未定，不能只宣传新增可评价。

|事实窗口|有效/期望观测|证券覆盖及影响|最小补齐需求|
|---|---:|---|---|
|金额T、换手T|各3040/3040|金额失败2642次，恰好每证券一次|这些字段不需要因零候选再补采|
|当前可比价格|1595/3040|1445只缺有效可比价格|同链复权/可比基准证据；保留原时钟|
|MA20(T)|1595/60800|3040只窗口均不足|每只连续20会话同基准价格|
|MA20(T−5)|0/60800|3040只均不足|前移5会话的20日窗口|
|START前5日|0/15200|历史价格与前期状态不可充分评价|原规则所需前期价格、金额、换手及相对强度总体|
|ACC等长3+3价格|1595/21280|3040只均不足|7个锚点会话及同总体基准；不改动量定义|
|ACC近5日换手|3040/15200|3040只均不足|近5会话换手与定义/来源|
|前5日换手|0/15200|全部缺前期换手|历史换手/流通口径证据|
|回踩MA250斜率|1595/775200|长期历史均不足|原250日与5日斜率窗口，同基准|
|收盘跌停事实|0/3040|全部未知|该证券×日期已核验限价/规则或直接事件证据|

2642个金额失败证券，各有四个明确的COMMON_HARD_FAIL/NOT_EVALUATED；不能计成“四次金额失败”，也不能捏造未调用形态判断。另398只的必要未知真实限制完整选择结论。缺口明细保留日期、有效/期望数、来源引用、可比基准和拒绝原因；无法由冻结输入证明“从未采集”或某个原始解析异常时明确保留未知。

本轮未联网补采。即使以后补齐今天可下载的数据，也不能保证取得历史时点可见性证据。真实执行还需要T+1至T+6及延迟退出期间的日历、原始开盘、交易限制、截止前合法上限、完整企业行动覆盖、日期化规则/税费和明确假设佣金；没有这些输入就不可评价。

## 大规模读写与性能

所有大规模数据均为TEST_FIXTURE，实际执行SQLite写入/读取，未用计数器代替行。原版本对80万总行数直接拒绝，故没有编造旧版耗时或提速倍数。

|测量|实际结果|口径|
|---|---|---|
|80万条归档写入|800000行，约424.74秒|隔离数据库约1.40GB，不是真实库副本|
|索引校验+流式全量读取|800000行，约242.46秒|每条实际反序列化、哈希核验|
|单证券依赖窗口|255条；实际payload读取255；约0.049秒|不因档案大就读取全部价格|
|读写进程峰值|64114688字节，约61MiB|包含建库和读取的进程高水位，不是完整策略峰值|
|原全窗口预加载尝试|触发1GiB预算，退出2|保留不完整目录，不发布完整结果|
|按证券缓存初次尝试|触发300秒读取预算，退出2|没有提高总行数上限或缩小股票池|
|同输入1000只全流程|全部1000只进入声明总体；实际消费390000行，核验800000条索引成员|V2及R1均已知排除1000；不是盈利样本|
|上述全流程成本|453.84秒，峰值269737984字节，约257MiB，输出约45MiB|含全池计算和共享输出；并非恒定内存，也非实时性能承诺|
|大结果只读汇总|约8.79秒，峰值约117MiB|逐分片校验，不重新展开12份共享事实|

390000是时间窗口内实际读取/解析量，包括被原可见性/日历规则拒绝的记录；没有冒称只解析最终参与运算的行。历史索引全部核验与消费价格字节核验分开记录。8证券LRU、研究64MiB压缩缓存、当前横截面和标量上下文仍有与总体相关的成本。真实旧双组单JSON约276MiB，新摘要与共享明细分离；实际各轮字节/峰值以本地记录为准。

## 执行与统计的实际范围

适配器通过归档字节SHA256、回执完整性、记录成员关系、证券/日期范围和四时钟解析证据。普通真实tape字典不能靠verified=true进入引擎。归档成功和缺行动、伪造引用、篡改、截止及冲突拒绝均在隔离测试中验证；模拟订单使用截止前合法上限，数量不使用未来开盘。未知成交保留资本承诺，未退出保留持仓，缺估值不填零。

真实佣金只接受PREDECLARED_HYPOTHETICAL，净收益仅在该场景下解释。官方资料及适用边界见新规格；未取得中国结算等完整日期化真实档案，不以当前规则覆盖所有历史。没有真实已完成交易样本，REAL_DATA_NOT_VALIDATED。

实际40日TEST_FIXTURE经CLI调用日期块统计，默认种子/20会话块/2000次不变。它是完全已知排除的合法现金账户：主账户差异0、区间[0,0]、p和Holm均1；信号均值没有样本，保持N/A/UNAVAILABLE。固定非零正/负差异的数学边界另有测试，不能把现金样本当成非零投资优势验证。另有较短CLI参数一致性测试、缺日期、资料不足、退出受阻、子集与完整成绩隔离测试。

预登记族包含每组对父组和R1_FULL−V2_CONTROL，分别提供8种估计目标，共96项；不可评价项仍计入Holm族。共同支持在收益之前冻结；条件信号只在双方有信号且结果完整时有定义，不冒充全时段价值。原session_index保留，缺日期不重新编号。单个随机篮子明确不是随机分布的百分位；same-M数量不足时显式不足，不静默缩为min(M,N)。

## 运行、测试与安全回退

最终版本、运行身份、实际命令和最后测试汇总见下文。测试批次有重叠，不相加为独立数量。未运行全仓全部测试、Windows、Docker、在线采集、真实券商/账户、模型训练、真实独立留出或市场盈利验证。

保留过的中间失败：四项原反例；两处适配测试构造（规则业务日时钟、最低佣金之外的过户费）；基准包装脚本最初解析了venv解释器符号链接，导致子进程找不到项目依赖，修正为保留venv路径；一次源码还在变更时综合回归中的CLI正确触发SOURCE_CHANGED，最终冻结后重跑。没有降低断言、删除失败样本或扩大skip。

安全回退：先保存本任务diff，逐文件恢复本任务新增研究helper、CLI research-r1分支及文档；不要整仓reset/clean，不覆盖后续用户改动，不删除真实库。原策略从未切换，因此不需要停服务或部署回滚。没有任务结束后的后台继续承诺。

## 最终版本与变更清单

最终HEAD仍为 `8990e5a00312e7ceaa67b7953840142a402bd36c`，分支仍为review；本轮是未提交工作区修改。环境为Darwin x86_64、项目venv Python 3.12.14。未安装或切换Qlib运行环境。

修改文件（均在stock-fish/）：

- `stockfish_core/yaogu/research_r1.py`：事实支持、诊断、有界历史上下文和完整源码身份。
- `stockfish_core/yaogu/research_r1_io.py`：三种CLI模式、冻结规格、逐日期编排、覆盖与执行/统计接线。
- `stockfish_core/yaogu/execution_research_r1.py`：有效规格、证据门禁、资本承诺与未知估值。
- `scripts/codex_research.py`：研究模式与参数入口。
- `docs/PROJECT_OVERVIEW.md`：本轮协议及结果入口。

新增文件（均在stock-fish/）：

- `stockfish_core/yaogu/research_r1_contracts.py`、`research_r1_diagnostics.py`、`research_r1_storage.py`、`research_r1_adapter.py`、`research_r1_statistics.py`：分别承载版本化合同、窗口诊断、只读/共享存储、归档证据解析和统计装配。
- `tests/r05_product/test_research_r1_repairs.py`、`tests/support/r1_frozen_decisions_8990e5a.json`：47项修复验收及原冻结输出决策指纹；原V2 golden未改。
- `docs/R1_REPAIR_SPEC_V2.md`、本报告。

原R1 JSON和V2 YAML配置、V2核心、原历史/存储/身份原语、旧规格及旧报告未修改；默认值的源码差异检查和参数化测试分别验证“默认未变”及“非默认实际生效”。允许的变化是研究身份、支持/覆盖字段、去重漏斗、共享输出schema、执行/统计协议；没有反向刷新旧决策结果。

最终源码与配置身份：

- `source_hash`：`190438336161100aacbb9426bf7e5d12cc4e8a54042a7ac5d001c40d547703b9`
- `config_hash`：`4d238ee2c298c5e56fe37a7423bf367b3bdb0b42200cbd5796772dbc8b6ef86e`
- `spec_hash`：`2656153a802a24d6d3595ec99af2fd862024fe6678e49c45b0ffafd8ea48a345`

各实际运行保留自己的源码身份。大规模运行之后增加了保存结果验证；真实/40日运行之后又收紧未知企业行动与限价适用性的适配检查。没有改写早期运行的source_hash，也不声称这些早期运行使用最终源码。最终源码另通过101项相关回归及冻结清单双次CLI运行。

|本地运行目录（review/下）|run_id|manifest_hash|source_hash|
|---|---|---|---|
|r1-fix-delivery-real|`r1_5396b0e1fbe496c73494b9a4427c8629bd5e6faf3c57ae048eb6f86b612cf19c`|`c4e89061622cd89d582329768590514b68f9c8db7b6f529f372573844adf9d5a`|`3b6b91bbecd68cd5e9d3450cfee10b5a14448afdcf3d5520e99e2470969c6eca`|
|r1-fix-delivery-multiday|`r1_64b6d4d1c582237f0f2a6b486932a8a52a39326f2d5c81175b7ec908a7420c4d`|`9962557319ec8a6366233e8257d0631bdac5d3e95c5bb935ba4ad0b9d68497fc`|`3b6b91bbecd68cd5e9d3450cfee10b5a14448afdcf3d5520e99e2470969c6eca`|
|r1-fix-snapshot-large-pool|`r1_26c235e2e3c49a46c250b3f2c92128d59a440af313847543ee053aa7ae66b4c8`|`5a3a790c47485b990126f154f68c34b69d7fd6855cba7c9fdf69170dfcd049a0`|`072615d18bddceef6caf666c56613617752868f1be166a32c9e22c441c8faf67`|
|r1-fix-final-protocol-fixture|`r1_dfda28663157559310bef167ea0e9e2bcc30f7e35d88d7ae1accfa0ee6acc8d2`|`8f6c3ed77abe065e703cf4afd2c630f5f82674eac60c525956074d6cfea7a0e2`|`190438336161100aacbb9426bf7e5d12cc4e8a54042a7ac5d001c40d547703b9`|

`r1-fix-final-protocol-replay`与final-protocol-fixture的run_id和manifest_hash完全相同，两个CLI均退出0。完整逐文件身份记录在 `review/r1-fix-evidence/delivery-identities.json`。

## 实际测试与三类命令

以下命令从仓库根目录执行；日志保存在 `review/r1-fix-evidence/`。已存在的输出目录会被拒绝覆盖，重跑时须使用新的私有目录。旧清单仅能由匹配身份的代码重放，不能拿早期source_hash清单强行配最终代码。

|批次|结果/退出码|日志与范围|
|---|---|---|
|原R1基线|54 passed；0|baseline.log|
|修复前四项反例|4 failed；1|failed-regressions.log；这是预期失败证据|
|首次修复|58 passed；0|first-repairs.log|
|扩展回归|372 passed；0|final-expanded-regression.log；历史、评分、快速路径等相关边界|
|后续综合回归|379 passed，220.40秒；0|acceptance-expanded.log；发生于最后两项适配收紧之前|
|最终源码相关回归|101 passed，79.60秒；0|final-r1-contracts.log；原54项R1加47项修复验收|
|最终源码CLI/冻结重放/保存结果汇总|均0|final-protocol-fixture.log、final-protocol-replay.log、statistics-summary.log|
|静态/差异检查|0|compileall、git diff --check、受保护原文件git diff --exit-code|

上述批次重叠，不能累加。原R1基线实际调用及最终相关测试调用为：

```sh
stock-fish/.venv/bin/python stock-fish/scripts/run_core_tests.py tests/r05_product/test_research_r1.py -q
stock-fish/.venv/bin/python stock-fish/scripts/run_core_tests.py tests/r05_product/test_research_r1.py tests/r05_product/test_research_r1_repairs.py -q
```

四项失败回归使用 `test_research_r1_repairs.py -k 'f01 or f02 or f03 or f04'`（当时仅四个测试），完整堆栈保留。综合批次结果作为额外覆盖记录，最终源码验收以最后101项为准，不用旧批次替代新适配断言。

1. 已有真实输入的诊断模式已运行成功，输入为原冻结导出，输出 `r1-fix-real-diagnostic`，run_id见real-diagnostic.log。对应入口参数如下，历史库只读：

```sh
stock-fish/.venv/bin/python stock-fish/scripts/codex_research.py research-r1 --mode diagnose \
  --input review/r1-baseline/real-export-envelope.json \
  --history-db review/data-release-20261001/snapshot/database.sqlite3 \
  --group R1_FULL --group V2_CONTROL --output review/r1-fix-real-diagnostic
```

2. 最终源码先生成冻结组/日期范围清单，再成功原样重放。未传execution-spec时，原默认执行/统计情景已冻结到清单；变更支持参数必须用 `--execution-spec` 并生成新身份：

```sh
stock-fish/.venv/bin/python stock-fish/scripts/codex_research.py research-r1 \
  --input review/r1-baseline/demo-input.json --group R1_FULL --group V2_CONTROL \
  --start 2026-09-11 --end 2026-09-11 --output review/r1-fix-final-protocol-fixture
stock-fish/.venv/bin/python stock-fish/scripts/codex_research.py research-r1 \
  --input review/r1-baseline/demo-input.json --group R1_FULL --group V2_CONTROL \
  --start 2026-09-11 --end 2026-09-11 \
  --manifest review/r1-fix-final-protocol-fixture/manifest.json \
  --output review/r1-fix-final-protocol-replay
```

3. 已保存40日研究结果汇总已实际执行成功。汇总验证原结果身份、完成标记、索引和所有共享分片，复用已计算的比较与统计；不会偷换旧source_hash或用当前参数重新估计旧结果：

```sh
stock-fish/.venv/bin/python stock-fish/scripts/codex_research.py research-r1 --mode summarize \
  --saved-result review/r1-fix-delivery-multiday/result.json \
  --output review/r1-fix-statistics-summary
```

40日完整原始CLI参数、退出0及122.16秒记录在 `review/r1-fix-evidence/delivery-multiday-command.json`，输入为 `multiday-2/TEST_FIXTURE_00.json` 至 `TEST_FIXTURE_39.json`（逐项--input），生成脚本为同目录 `multiday_cli.py`。80万基准脚本为 `large_history_benchmark.py`，读写计数和测量在 `large-history/benchmark.json`；1000只全流程日志为 `snapshot-whole-pool.log`及对应time.log。所有这些均为本地忽略产物。

最终边界：研究工程接线已完成；真实收益因明确数据缺口仍不可评价；没有未交付的后台工作，没有策略晋升、提交、推送或部署。
