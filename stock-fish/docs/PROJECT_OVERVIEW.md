# 项目综述与规则参考

用户入口为[README](../../README.md)、[USER_GUIDE](../../USER_GUIDE.md)、[CURRENT_STATUS](../../CURRENT_STATUS.md)、[CURRENT_STRATEGY](../../CURRENT_STRATEGY.md)和[DEPLOYMENT](../../DEPLOYMENT.md)。本综述解释架构、规则与合同；当前完成度、运行缺口和检查结果看CURRENT_STATUS，逐轮历史可查[整理前固定版本](https://github.com/LiRongji1995/stockfish-qlib-quant-system/blob/9b13e4afdaa243e987822b2218bc87cf85a39131/stock-fish/docs/PROJECT_OVERVIEW.md)。

年度研究只维护[2026短线主线与情绪周期总报告](2026_SHORT_TERM_MAINLINE_REPORT.md)，聚焦活跃且与冻结选股标准相交的主线。市场成交、同股参与、成员更替、价格承接、题材发酵及风险分别研究；当前成员回看不冒充历史成分或过去盘前知识。完整独立周期及历史PIT均未认证，MARKET_EDGE=NOT_ESTABLISHED。

## 当前系统

最新[10月21日盘前尝试](PREMARKET_BOUNDED_RETRY_20261021.md)已按单独授权从[独立源码 bundle V2](PREMARKET_FACTOR_CHECKPOINT_SOURCE_BUNDLE_V2.md)运行：3个官方目录COMPLETE且分页完整，随后采集读取计量触限退出4，capture余额为0，实际超限60,837字节。核心、题材及展示未运行，没有新run或可信名单，DAILY_V1仍为UNKNOWN_NOT_EXECUTED；本次未运行真实每日后验或双run。归档校验退出0只核失败封存字节。此前缺RAW首见钟的PARTIAL及旧run保持；本次未进入复权factor分支，不构成该修复的真实验收，也未切换服务或运行配置。

本轮另完成10项隔离内存候选检查及两次旧归档只读认证，均退出0。提前释放候选在本次测量中降低局部RSS和累计峰值，但认证耗时99.214→100.751秒、CPU83.565→88.717秒，未证明提速，故未安装；新身份未写入旧库。双run的300秒缺口仍在，详见[本轮诊断](PREMARKET_BOUNDED_RETRY_20261021.md)。

[独立环境与 R2 闭环交付](RUNTIME_R2_CLOSURE_DELIVERY_V1.md)补充新的哈希锁运行环境、standalone 原生归档的完整来源鉴证和同库追加桥接，以及明确当日输入的每日后验作业。桥接保留原 run/input、五导出和实际新导入时间；不执行策略，不恢复已失去的内存 owner，不授予历史 PIT。每日待办与成熟度分开，作业批次的产品和账本原子提交；旧配置、策略与调度保持，运行部署和连续真实观察仍单独验收。

此前[历史闭环部分验收](CLOSED_SESSION_PARTIAL_ACCEPTANCE_20261020.md)对应16个源码/测试文件及676项组合回归。工程保持完整来源、双父实际全文、冻结公式和事务末终验，以线程内短读连接、显式1024行 typed 编码、同次私有 proof 的二次解码减少实现开销；`AUTHENTICATED_NONEMPTY_V1` 仅在全5期限 PENDING、无 DUE/UNKNOWN 且已鉴证非空时避免未借出 run 的展开。9个 normalizer 文件、精确类型回退和空池完整检查保持。

历史10月16日单 run 在相同源码的隔离克隆中显式重试成功，worker 292.136秒，随后幂等、三产品回读和独立 H1（全池3041、研究集合25）通过。原当日及正式失败记录不改判；closed-session replay 的历史资料截止不改变系统/作业实际时钟。双 run 两次均在原300秒内未完成、无新产品，只测试了一条到期加一条 PENDING，不能外推两个成熟 run、默认5条容量或连续每日可靠性。原始尝试见[当日执行记录](REAL_DAILY_ACCEPTANCE_PERFORMANCE_V1.md)，各阶段证据见新的部分验收记录。

新增[运行安全、短周期后验与性能第一阶段](BASELINE_OUTCOME_PERFORMANCE_V1.md)。运行身份由独立 doctor 实际导入诊断，调度 readiness 使用成功轮询与停滞状态；Research 健康别名鉴权和 SSE、Live 本机访问与 WebSocket 均有容量边界。SQLite 追加版本化查询索引，单 run 读取仍核原内容 hash；正式运行库未迁移。R1/R2 的新后验版本在同一权威服务库追加逐交易会话观察、跟踪与复盘，不改保存名单，不套用 SAM episode 或账户成交语义，也不恢复普通归档 JSON 的来源能力。原默认环境与本轮新独立环境分开报告；真实连续后验和全链性能仍需验证。

显式[独立终端盘前研究V1](STANDALONE_PREMARKET_WORKFLOW_V1.md)将公开来源请求和JSON缓存索引、原生R2内存owner、真实SOURCES_V2/行业读取器/完整67日市场stream/V2日志、报道原件与纯展示层接为单命令。入口为 `scripts/stockfish.py premarket`，不依赖Codex应用、LLM、运行中的HTTP服务或新业务DB；默认主动SAM V2保持。文件索引只保存真实来源引用，保存JSON不能恢复活能力。旧run、五导出、完整RAW分母和未知保留；3亿元仅在最后展示层生效。完整行业/概念当前篮子仍不是完整经济题材或历史PIT，新闻不因共现变成股票归因。新执行profile分阶段限制实际返回读取，父进程另验真实退出、代码/来源/产物；发布及隔离测试不替代真实冷/暖启动、连续每日或投资验证。

本次补齐真实V2来源终验回调、未知与多关联文本，并修复目录扫描节流且强制阶段入出完整资源检查，原限额和数学不变；73项隔离检查退出0。一次真实暖缓存命令已完成原R2、完整67会话及终验，外部退出2、封存为 `COMPLETED_WITH_UNKNOWNS`：原25只、3亿元展示17只，来源和经济方向未知保留；新冷启动、Windows和连续跨日可靠性仍未验收，记录见[独立入口检查](STANDALONE_PREMARKET_VALIDATION_V1.md)。

Python Web/API、单写者SQLite、持久采集队列与调度是现有业务入口，Codex通过薄封装调用同一保存结果。服务地址和权威库每次实查，不能据旧健康/采集成功判定当日就绪。Go候选工具独立运行，生产未切换；Qlib、旧Alpha158、MiroFish及模型注册保留研究用途，不是规则入口前置。pyqlib使用0.9.7 wheel，上游许可及第三方通知保留。

主动核心为[STAGE_AWARE_MOMENTUM_V2](STAGE_AWARE_MOMENTUM_V2.md)，按阶段分离事实、setup、确认、情境和反证，A/CLV不作统一硬门槛。DAILY_PRIMARY_FAST与RESEARCH_FULL分开，DAILY_V1另报可用性。[短线强势R1](SHORT_HORIZON_STRENGTH_R1.md)及[R2](SHORT_HORIZON_STRENGTH_R2.md)是显式版本，识别启动/延续/首次回撤；R2追加近期参与度和动态金额软参考。参数尚未验证，旧配置、source identity和run冻结，研究WATCH与正式FOCUS分别表达。

[题材演进](THEME_EVOLUTION_R1.md)计算金额、占比、相对价格、广度和集中度；[呈现V4](THEME_OBSERVATION_DIAGNOSTICS_V1.md)分列确认主线、首日发酵观察和仍活跃的分歧，风险独立。只展示有当日活跃依据、独立主线范围证据及合格候选交集的方向，不展开全部PENDING微标签。供应商篮子、经济业务关系和策略资格分别核验，缺题材源显著提示不可用。

## 数据与真实性

RAW、物理receipt、证券身份、业务日、来源公布/取得/入库及实际生成时间先认证，再使用价格、换手、资金或风险。历史后取资料只作事后研究；UNKNOWN、未成熟、合法空与失败分列，缺结果不计零收益。完整日期化四板金额总体与有限主板合格池分开，成交不当净流入。

历史真实复权须完整数值父、真实祖先、无冲突锚和精确同basis乘积；完整同日目录及价格窗不能互相替代。[物理历史对照V2](PHYSICAL_HISTORICAL_PAIR_V2.md)有限批准4/5月600/601/603/605身份和沪市退市前缀，全部代码字段先核ASCII。规则适配、真实因子补齐、原生两臂、历史成员和PIT分别验收；旧V1及原R1/R2冻结。

只读[原input导出](ORIGINAL_MARKET_INPUT_EXPORT_V1.md)保留原字节、原SHA及compact历史引用，同库history owner须实际原生认证。完整[流式交付V3](SAVED_R2_COMPLETE_STREAM_DELIVERY_V3.md)和[保存后当日协调V1](SAVED_R2_DAILY_COORDINATION_V1.md)认证当次原件、日期及完整并集，67会话不压缩；保存JSON不能恢复能力。当前每日完整通知已配置，真实采集到聊天交付、默认市场/业务/席位附件和连续可靠性仍须验收，不自动切换服务或改策略。

最新真实链路已分别完成10月12日原资料到10月13日完整34只R2观察的核心、导出和聊天交付；独立67会话题材附件则触原读取预算失败。核心、各附件及整个每日流程分别验收，缺附件不删原观察股，也不把单日媒体和完整子篮子的活跃提升为连续阶段或正式强度梯度。当前采集、工程状态及唯一年度报告由各自入口持续更新。

[证据日志V2隔离原型](EVIDENCE_AUDIT_SPOOL_V2.md)将规范依赖与manifest放在同一完整前后验证过程，保留写入账本及全部行/索引，减少重复读取。它尚未接线，旧V1、来源发行、题材数学和生产入口保持；工程等价与真实全链预算分别验收。

显式[字典日志V4](EVIDENCE_AUDIT_SPOOL_V4.md)与[主题流V4](THEME_EVOLUTION_STREAMING_V4.md)登记独立执行身份，沿用冻结数值及逐交易日FSM。普通访问有限LRU只减少重复解压，命中仍核完整磁盘frame、索引、字典、owner和身份；完整joint不借缓存省略规范记录。缓存bytes与entries必须显式传入，0禁用，没有未经验证的生产默认。append与sorted两份84字节索引、真实头及清单均计费。根三臂67会话夹具数学等价，但禁缓存和小缓存原生返回量分别比V2多31.28%和23.26%，尚无成本改善；独审另一次临时文件身份变化拒绝分列保留。较小压缩体积不认证真实67会话资源上界。完整caller联合来源、原件终验、旧run/input和五导出保持各自验收，默认入口未切换，投资优势仍未成立。

[主题流V2](THEME_EVOLUTION_STREAMING_V2.md)显式使用真实V2日志，保持旧逐日FSM及全部数值、完整67会话与UNKNOWN；真实日志预算失败不降为格式未知，primary/cleanup分列。作者及独立96项夹具通过，42份依赖初终整SHA/7FP一致；真实保存核心到新67日附件尚待接线，不修改旧run、默认CLI或服务。原件终验callback只执行caller提供的检查，不能认证owner或从JSON恢复来源。外层必须新登记并认证完整原件、原run/input和五份正式导出，保存原截止与新情境截止，不再次选股或正常化，不因工程通过扩大预算。

闭合派生明细可经完整回解认证后无损归档为ARCHIVE_ONLY。活动库、最新完整备份、仍被使用的历史库和真实来源保留，退休索引登记删除及恢复边界。代码与运行数据分开，公开仓库不含DB、凭据、全市场原件或完整私有审阅包。普通日报不继承工程发布或清理权限。

私有产物退休采用明确白名单、原SHA声明、完整归档及独立全件回解核验，再复核全部原件与保护路径后精确移除展开副本；保存计划、档案、删除记录及恢复入口。历史研究脚本消费已退休路径前，先按私有退休索引恢复至新目录并核对原字节与完整集合，再显式处理路径和来源认证。归档恢复仅证明字节可读，不恢复旧inode、owner、来源能力或历史PIT；失败记录保留原终态。活动库、恢复点、原input、真实RAW/回执与每日热材料继续保留。本次首批私有瘦身的工程和真实字节检查见CURRENT_STATUS；不会由普通日报自动触发同类清理。

2026年10月14日追加深度瘦身完成：五组历史材料（含12份旧input）及两份非活动历史库无损冷存，另退休闭合旧实验环境与207份源码缓存的原文件，安装与复建证据保留。九组14,528份原文件共16,122,764,640字节；两库约11.94GB冷存为3.44GB，全部归档原字节与集合、五组全件及两库各三件实体恢复均已核验。数据库状态按相同主库字节与空WAL继承原V3 FULL证明，本轮恢复没有执行SQL；旧失败不追认为成功。已再生的9份新缓存176,308字节保留并扣除，连同所有新增工具、证明、归档、索引与两份文档后，本轮净逻辑减量约11.47GiB，与10月13日首批累计约12.42GiB、87,120份不重复原文件；APFS卷空闲变化另记。活动库、真实RAW/回执/来源owner、现用环境、固定SDK、源码、模型、许可和已有开发改动保持，日常与开发入口未切换。旧脚本须显式恢复至新路径并登记新实际owner、核对来源；冷存字节不恢复旧owner、历史PIT、原inode或来源能力。所有失败记录保留，未执行生产每日链或投资回放。

新增[压缩日志V3](EVIDENCE_AUDIT_SPOOL_V3.md)及[主题执行原型V3](THEME_EVOLUTION_STREAMING_V3.md)分别保持完整物理审计、全部逻辑解码与实际返回计量；不能从manifest或callback恢复来源能力。原数学、日历与UNKNOWN等价已隔离检查，但同67会话夹具总返回比V2多290,351字节，较小输出不代表较低读取成本。它们目前只作诊断原型，未选作真实日报后端，不修改已有完整流式交付V3命令的实现或默认服务。真实预算、外层来源和每日连续交付继续独立验收。

新增[同次源投影V1隔离原型](../stockfish_core/yaogu/session_payload_fusion_v1.py)以同一次实际读取并SHA认证的market/master数据，同时生成原市场统计和完整价格panel；两轮67会话及缺日、日期身份、分页、交易状态、量额矛盾、可见时间和价格反证的54项TEST_FIXTURE检查通过。它仅接受原exact reader/resolver，不缓存跨panel资料或恢复JSON能力；新源码、配置与规格登记只是直接投影的身份元数据，完整caller运行依赖并集和来源终验仍须另行认证。合并删除旧_session中间第二次market读取的观察点，不能宣称所有并发变化异常逐次等价；必须保留外层完整初终整SHA/7FP。原型未接旧stream、正式每日入口或默认服务，真实67会话读取与输出总预算仍未证明READY，不修改选股参数或重写原run。

独立[StockFish Live](../../stockfish-live/README.md)研究分钟合同、Parquet/DuckDB、PIT/replay与盘中预测；真实供应商、长期运行和投资有效性尚未验收，不修改Research的服务、SQLite和日频策略。见[Live审计](../../stockfish-live/docs/REPO_INTEGRATION_AUDIT.md)。

## 模块与合同定位

| 内容 | 入口 |
| --- | --- |
| 日常调用、权限与现状 | [日常协议](CODEX_DAILY_RESEARCH.md)、[AGENTS](../../AGENTS.md)、[当前状态](../../CURRENT_STATUS.md) |
| 来源、时钟与历史存储 | [数据合同](../stockfish_core/DATA_CONTRACT.md)、[特征证据](architecture/FEATURE_EVIDENCE_CONTRACT_V1.md)、[历史合同](R12_HISTORY_ARTIFACT_REPORT.md) |
| 规则、参与和题材 | [主动V2](STAGE_AWARE_MOMENTUM_V2.md)、[短线R2](SHORT_HORIZON_STRENGTH_R2.md)、[题材演进](THEME_EVOLUTION_R1.md) |
| 年度复盘与关系 | [唯一年度报告](2026_SHORT_TERM_MAINLINE_REPORT.md)、[周期标准](MAINLINE_CYCLE_RESEARCH_STANDARD_V1.md)、[原生流式研究](THEME_STREAMING_RESEARCH_R1.md) |
| 市场及参与描述 | [市场事实](MARKET_DESCRIPTIVE_CONTEXT_V1.md)、[市场解释](MARKET_EXPLANATION_V1.md)、[同股参与](COHORT_PARTICIPATION_TRENDS_V1.md) |
| 席位和业务解释 | [龙虎榜](LHB_SEAT_EVIDENCE_V1.md)、[经济方向](ECONOMIC_DIRECTION_EXPLANATION_V1.md)、[归档展示](ECONOMIC_DIRECTION_ARCHIVED_DISPLAY_V1.md) |
| 历史重建与因子 | [物理V1](PHYSICAL_HISTORICAL_RESEARCH_V1.md)、[两臂V2](PHYSICAL_HISTORICAL_PAIR_V2.md)、[因子事实V2](HISTORICAL_FACTOR_VALUE_FACTS_V2.md) |
| Go及全部技术资料 | [Go入口](../../stock-fish-go/README.md)、[隔离生命周期](../../stock-fish-go/ISOLATED_LIFECYCLE.md)、[技术索引](README.md)、[历史归档](archive/README.md) |

## 冻结对照与详细公式

下列DAILY/PV/DNA/THEME公式和跟踪口径从原综述保留。它们用于对应实验和历史解释，不覆盖当前V2/R1的独立规格，也不表示相关数据已就绪。当前实际检查和未达标项目统一看[状态与待办](../../CURRENT_STATUS.md)。表内“最新输入”等可用性描述来自原历史快照，不能视为即时数据就绪。

## 5. 当前全部策略、公式与研究口径

### 5.1 策略身份和相互关系

| 策略 / profile | 当前定位与启用情况 | 主要数据门槛 |
|---|---|---|
| `YAOGU_V1.1.0` / `DAILY_V1` | 原五族六分项正式规则，冻结对照，未降级或改名 | 当期资格、板事件及足够历史、量价、主题、成熟 G、完整市场 M；本机最新真实输入尚不能完成全部六项 |
| `FUNDS_V1` | DAILY 的资金影子对照，默认 `funds_primary: false` | 全同族冻结候选的同源、同窗资金与龙虎榜证据全部可比；当前无完整真实评分证据 |
| `PRICE_ONLY` | B/V/G 的显式诊断，非完整 DAILY，非焦点名单 | 仍需对应 B/V/G 必需数据；不能用“只看价格”绕过缺板事件或成熟 G |
| `PV_OBSERVATION_V1` | 独立量价研究实验；日常量价名单入口，保存全部合格证券 | 日期化资格与连续 6 个会话的完整价格/金额；无需题材、资金、板事件、竞价、252日股性、账户、LLM 或 Qlib |
| `PV_FOCUS_PREVIEW_V0.1` | 完整 PV 的确定性焦点子集，非新增排序策略；正式周期只作用其选中项 | 原 PV + A/R/RAW 阳线与 CLV 条件，无配额 |
| `DNA_RERANK_V1` | 已实现的股性增强实验，在合格子群重算 PV 后混合 G | 连续 252 会话、可核验板事件、成熟样本与可靠性门槛；最新实际输入不足 |
| `THEME_CONTEXT_V1` | 已实现的事件题材增强实验，在合格子群重算 PV 后混合 T | 截止前可见完整 EVENT 主题、至少5成员、同伴价格/板事件覆盖≥95%；最新实际输入不足 |
| `THEME_CAPITAL_TREND_V1` | 上一版板块、资金、多头趋势独立研究臂，保留为对照 | 成交额、换手、长窗均线、题材联动、同题材活跃资金均需 `PASS` |
| `STAGE_AWARE_MOMENTUM_V2` | 当前主动核心阶段感知强势股策略；R14 起为 daily primary 默认 active strategy；V1 冻结为历史只读兼容 | 主板资格、成交额/换手底线、慢趋势状态、setup 识别、行为确认、市场兼容、同题材板块/资金和关键反证；最终输出 `FOCUS/WATCH/REJECTED/UNKNOWN` |
| `SHORT_HORIZON_STRENGTH_R1` | 显式选择的短线强势研究版本；保留全池和独立观察选择，生产晋级关闭 | 共同资格、真实可见RAW与可比价、25会话精确事实；强启动/延续/首次回撤独立条件，情境与限价未知不造通过 |

`DNA_REVERSAL_V1` 与 `THEME_LIMITUP_V1` 是旧客户端兼容别名；实际 canonical ID 分别为 DNA_RERANK/THEME_CONTEXT，不是两个新增策略，也不重复统计。配置 `execution_enabled: false` 指不启用交易执行，不代表禁止运行研究实验。日常以已确认 PV 路径处理量价请求，仍明确 DAILY 是冻结原基线，不拿 PV 结果冒充 DAILY 已齐备。

### 5.2 共同特征和 DAILY 五种结构

以下比例内部为小数，金额 CNY、成交量股、价格元。令 `C*` 表示可比收盘，`O/H/L/C` 表示 RAW OHLC，`ref` 是 T 当日参考前收。

```text
amount_ratio_5 = amount_T / mean(amount_T-5 ... amount_T-1)
volume_ratio_5 = volume_T / mean(volume_T-5 ... volume_T-1)
turnover_fraction = volume_shares / verified_float_shares（或核验同口径供应商值）
ret_k = C*_T / C*_(T-k) - 1，k=3/5/10
MA5 = 最近5交易会话可比收盘均值（含T）
amplitude = (H-L)/ref
CLV = close_location = (C-L)/(H-L)
upper_shadow = (H-max(O,C))/(H-L)
```

放大比的分母不含 T。DAILY 中 H=L 的合法单价：上限一字 CLV=1、下限一字=0、其他单价=.5，上影=0并记标志；PV 焦点的零区间另有“不适用、不入焦点”规则，二者不能混用。`median_amount20` 是最近20会话成交额中位数，`cmax60` 是有足够证据的最近60会话最高严格板数，不能用左删失下界冒充精确历史。

| 家族 | 正式结构资格 |
|---|---|
| ONE_TO_TWO | T收盘封板，严格 c=1，前日已知 |
| TWO_TO_THREE | T收盘封板，严格 c=2 |
| HIGH_BOARD | T收盘封板，严格 c≥3；已证明至少3板可保留结构预览，但精确 c 缺失会阻断依赖它的完整评分 |
| TREND_NM | T不封板，M5≥2 或 M10≥3；C*≥MA5、ret3≥0 |
| REBOUND_SETUP | T不封板，上一轮≥2板且结束距今1–3会话；C*≥MA5、RAW C≥O、CLV≥.6 |

主家族优先 HIGH_BOARD → TWO_TO_THREE → ONE_TO_TWO → REBOUND_SETUP → TREND_NM，其他命中为 secondary_tags，不重复计票。ONE_TO_TWO 已收复上一≥2板轮次结束的可比收盘可附 `REBOUND_COMPLETED`，不再混为尚待完成 setup。没有 PE、市值、累计涨幅或“超过几板永久禁入”的隐藏过滤。

合法 EMPTY 需要全资格池能够判定是否命中结构；当日齐价但历史触发条件未知，仍是 PARTIAL/UNAVAILABLE，并保存 `structural_screening_unknown_ids`。非候选只需判定结构所需数据，不强求每个未命中股票取得全部候选评分历史。

### 5.3 DAILY 六分项完整公式

统一分项为 0–100。`R(x;a,b)=100×clip((x-a)/(b-a),0,1)`，a<b。`Trap(x;a,b,c,d)` 在 x≤a 或 x≥d 为0，a→b线性升至100，[b,c]维持100，c→d线性降至0；缺失输出 NULL，不是0。下面为当前 [engine.py](../stockfish_core/yaogu/engine.py)、[themes.py](../stockfish_core/yaogu/themes.py)、[emotion.py](../stockfish_core/yaogu/emotion.py) 的实际数值。

**B：板结构/趋势匹配。** `c` 当前严格板数，`MN` 最近N会话封板次数，`p_c` 上一轮板数，`C*_end` 上轮结束可比收盘。

```text
B_ONE_TO_TWO = .40 R(M10;0,3) + .30 R(M20;0,5) + .30 R(cmax60;0,4)
B_TWO_TO_THREE = .40 R(M10;1,5) + .30 R(M20;2,7) + .30 R(cmax60;0,5)
B_HIGH_BOARD = .40 R(c;3,8) + .30 R(M10;3,7) + .30 R(M20;3,10)
B_TREND_NM = .50 R(M5;0,3) + .30 R(M10;0,5) + .20 R(ret10;0,.30)
B_REBOUND_SETUP = .40 R(p_c;2,6) + .30 R(M10;1,5) + .30 R(C*_T/C*_end;.90,1.02)
```

**V：量价匹配。**

```text
V = .30 Trap(amount_ratio_5;族参数)
  + .25 Trap(turnover_fraction;0,.03,.20,.45)
  + .25×100×CLV + .20 R(ret3;-.05,.15)
```

成交额 Trap 族参数：ONE_TO_TWO=(.3,.8,2.5,5)，TWO_TO_THREE/HIGH_BOARD=(.1,.35,1.5,4)，TREND_NM/REBOUND_SETUP=(.3,.8,2,4)。这些不是资金身份识别或实际买盘保证。

**T：主题强度；L：主题内相对地位。** DAILY 接受有明确产业/事件定义的 INDUSTRY 或 EVENT 主主题，不接受纯资格标签或 INDUSTRY_PROXY。主板有效成员≥5，剔除目标自身后，板事件、价格、ret3、严格板数等必需字段可用同伴≥95%。记 u 同伴封板数、c2 同伴至少2板数、p_pos 同伴当日 RAW C>ref 的比例、r3_med 同伴 ret3 中位数。

```text
T_theme = .35 R(u;0,5) + .25 R(c2;0,3)
        + .25 R(p_pos;.40,.80) + .15 R(r3_med;-.03,.09)
T = 可用主题最大 T_theme；并列按 theme_id 升序选主主题
P(x) = 100×(rank_average_ascending(x)-1)/(n-1)，n≥2
L = .50 P(M10) + .30 P(ret5) + .20 P(median_amount20)
```

L 的参考集是已冻结主主题完整当期资格成员，**含目标自身**；平均并列名次，完整参考字段缺失则 L 不可用，不按 TopN 或剩余少数样本重算。T 取最大主题后即用该主题 L，不能另挑 L 最好的主题。成员日与行情日分开：晚间取得当前成员配最后收盘价可以形成当前截止的新研究，需标 `CURRENT_MEMBERSHIP_AT_DECISION`；不能声称这些是旧观察日 PIT 成员。

**G：成熟封板事件股性。**

```text
G = .50 R(limit_up_count_last60;0,8) + .50×100×(s+1)/(n+2)
```

历史范围最多250会话、至少真实60会话；n 是截至当前已能观察下一交易会话可比收盘的历史封板事件数，s 是其次日可比收盘变化>0的次数。当前事件的未来标签不进入 G。停牌、缺价与不可比下一日单列 censored，不顺延。完整历史确无成熟事件时原始成功率 NULL、Beta(1,1)先验=.5；有事件但全部结果删失则 G 不可用，不能当成无事件。

**M：主板市场环境。** 使用完整身份分区的默认主板非 ST 范围：`master_expected → eligible / known_excluded / eligibility_unknown`，合法池再拆停牌、需要价格、已取价格与缺价。状态覆盖 `(eligible+known_excluded)/master_expected` 与价格覆盖 `price_observed/price_required` 均至少98%，并仍需来源/母体完整。N 是实际可核验价格/涨跌停状态的交易观察集合，不把98%声称100%。昨日封板 cohort 身份100%保留，今日 ST/停牌不将其删除；昨日身份未知则指标不可用。

```text
p = (s+1)/(n+2)
M = .30 R(p;.10,.50) + .25 R(m;-.03,.05)
  + .20 R(U/N;.003,.020) + .15 R(b;.30,.70)
  + .10 [100-R(D/N;.001,.010)]
```

这里 n 为冻结昨日封板群体数，s 为今天继续封板数，m 为该群体今日合法可比收益中位数，U/D 是当日涨/跌停家数，b 是当日上涨比例。确认停牌留在 n、不晋级；m 单列有效样本。完整零cohort时 raw p/m为NULL，p 使用明确先验，m子分50标 `PRIOR_NO_EVENT`；全cohort确认停牌时 m子分50标 `PRIOR_NO_TRADABLE_EVENT`。来源缺失不是零事件。原始各梯队晋级率与平滑 p 分开显示。

总分 `S_daily=Σ(权重×分项)/100`，冻结权重如下：

| 家族 | B | V | T | L | G | M |
|---|---:|---:|---:|---:|---:|---:|
| ONE_TO_TWO | 20 | 25 | 25 | 10 | 5 | 15 |
| TWO_TO_THREE | 25 | 20 | 25 | 10 | 5 | 15 |
| HIGH_BOARD | 20 | 15 | 25 | 15 | 5 | 20 |
| TREND_NM | 15 | 30 | 25 | 10 | 5 | 15 |
| REBOUND_SETUP | 20 | 30 | 20 | 10 | 5 | 15 |

每行合计100。缺任一项不可逐股删除缺项并重标满分；Qlib、LLM、PE、ROE不进入此分。完整同族以未舍入 S/T/V 降序、证券ID升序排序；跨族不当成一份概率总榜。页面保留两位小数，显示值不反向参与排序。

### 5.4 DAILY 情绪状态、层级和竞价证伪

`d=M_T-mean(M_T-1,M_T-2,M_T-3)`，严格使用三个交易会话各自在截止前成功发布的唯一最新合规版本；配置/特征、主池、覆盖政策与 coverage_type 要匹配。缺任一天则 d=NULL、`UNKNOWN_DELTA`，保存缺失日期；不拿自然日、同日三修订或缩短均值代替。当前 M 若完整仍可保留。

当前 `classify_emotion` 按以下优先级：RETREAT 若 `(d≤-12 且 M<55)` 或 `(D/N≥.008 且昨日n≥5且m≤-.02)`；否则 M<30为 COLD；30≤M<50且d≥8为 REPAIR；M≥75为 HOT；M≥50且d≥0为 FERMENT；其他为 SPLIT；M不可用才为 UNKNOWN。**d缺失本身不强制所有状态为UNKNOWN**，不把 `UNKNOWN_DELTA` 与总状态混为一谈。

FOCUS 要求 S≥70、V≥45、T≥50、数据完整、市场不在 COLD/RETREAT/UNKNOWN 且无观察专用风险；S≥55未进FOCUS为WATCH，严重异常波动/OBSERVE_ONLY也降为WATCH；更低完整分保留STRUCTURAL_MATCH，分数缺失为DATA_PENDING。≥7板只提示风险，没有永久禁止。严重异常波动公告不等于已确认停牌。

页面观察20、重点5为展示；按 ONE_TO_TWO、TWO_TO_THREE、TREND_NM、HIGH_BOARD、REBOUND_SETUP 轮询，完整同族集合持续保存。与 PV 焦点无人数限制相互独立。

前夜冻结对象、主题同伴、原分、公式、阈值和 `AUCTION_CONDITIONS_V1`。次日仅合格09:25撮合快照可复核：接力族 gap≤-3%标弱、FOCUS降WATCH；竞价额/前日额<.5%为参与弱；≥2%且gap>-3%为参与确认但不加分；gap≥8%或撮合价在涨停价提示进入不确定；冻结同伴上涨占比<30%且gap中位数<-2%为主题依据减弱。缺数据为NOT_EVALUATED，不以09:30或盘后字段伪装早期撮合；不改原始分和同族顺位。

### 5.5 FUNDS_V1 与 PRICE_ONLY

资金计算只用具有来源、同窗、金额单位及披露范围证明的白名单字段：

```text
F_money = .60 R(当日同源大单净额/当日成交额;-.10,.10)
        + .40 R(最近3交易会话同源大单净额之和/同窗成交额之和;-.10,.10)
F_lhb = R(不重叠披露净额/同披露窗成交额;-.05,.05)
F = .60 F_money + .40 F_lhb
S_funds = .90 S_daily + .10 F
PRICE_ONLY_score = .40 B + .50 V + .10 G
```

整个同族结构候选集合先冻结，全部资金证据/日窗/口径可比才发布该族 FUNDS 排序，否则整族不可用且保留 DAILY。三日必须恰为观察日结束的最后三个交易会话；多上榜原因、单日/三日重叠不能重复加钱。完整披露全集明确未上榜时 `NOT_DISCLOSED`、raw净额NULL、F_lhb=50；FETCH_FAILED/NOT_PUBLISHED不能当“没上榜”。席位别名只作有来源、有效期、样本量的证据，不能从营业部名推断自然人或按知名名字加分。披露后的1/2/5/10日结果不进当前特征/LLM证据白名单。

PRICE_ONLY 保留 `daily_score=NULL`、tier=DIAGNOSTIC，永不成为完整DAILY的FOCUS。资金在 R05 新实验中只是附加证据/分组分析，不进入 PV/DNA/THEME 权重。

### 5.6 PV、焦点：日常量价入口

通过当期资格后，证券必须具有连续 T-5…T 六交易会话的完整合法 RAW OHLC、非负同口径成交额、同基准正可比收盘与 T参考前收。前五会话总成交额必须>0。

```text
A = amount_T / mean(amount_T-5 ... amount_T-1)
R = C*_T / C*_(T-5) - 1
H_factor = (RAW high_T - RAW low_T) / reference_prev_close_T
基础入围：A≥1.2 且 R>0
PR_mid(x) = 100×[count(values<x)+0.5×count(values=x)]/n
S_PV = .45 PR_mid(A) + .35 PR_mid(R) + .20 PR_mid(H_factor)
```

百分位总体是**通过 A/R 条件的完整候选集合**，与 DAILY L 的 `(rank-1)/(n-1)` 不同。单证券或全并列取50。输入数值和评分用 Fraction 有理数，显示通常六位小数；同分按证券ID升序。`top_ids`、top_n、分页不截断保存的 items。

字段覆盖分母是全部资格证券，分子是六会话必需字段完整者，不能用入围数量作覆盖分子。母体完整且覆盖≥98%时，有排名为COMPLETE、无人入围为EMPTY；范围不完整但有可算样本为LIMITED_SCOPE，完全无可算样本为UNAVAILABLE。低覆盖仍可读真实受限名单，但不称全主板完整判断。限价未知只是 `UNVERIFIED_NOT_ZERO`，不因为缺板事件剥夺独立 PV；保留“振幅是风险”“分数非概率”“限价未核验”“未人工风险复核”等标记。

焦点从完整 PV 同顺序过滤：**A≥2、R≥.10、RAW C≥O、CLV≥.70**。没有 H_factor门槛、rank门槛、最低/最高人数或补位；不在焦点子集重算百分位。RAW H=L时CLV不适用，该股仍可属于PV但不进焦点。详情保留每个未选门槛原因，数值通过不是风险已清除。

### 5.7 DNA 股性增强完整定义

DNA只处理PV候选中已具足连续252交易会话和完整可核验收盘封板/触板事件的证券。窗口包含额外前日用于判断首板：成熟事件观察为 T前250会话（T-250…T-1），计数窗口为含T最近250会话（T-249…T）；当前T未来结果不进入成熟分母。

首板尝试：事件日触上限且前交易日未收盘封板。次日收益用该日 `RAW close/reference_prev_close-1`，开盘溢价用 `RAW open/reference_prev_close-1`，不误用旧RAW收盘跨除权。已知事件但次日参考价缺失只剔除对应成熟结果分母，不记失败。当前实现要求完整历史事件；历史行/事件本身未知使该证券增强不可用。

可靠性资格：首板尝试 n≥3、**原始首板封住率>70%**，且封板次日收益、首板封住次日收益和首板尝试次日开盘溢价三类必需分母均>0。它没有额外“Wilson下界必须>70%”门槛。

```text
Wilson_LB(s,n) = [p + z²/(2n) - z√(p(1-p)/n + z²/(4n²))] / [1+z²/n]
p=s/n，z=1.959963984540054，平方根使用50位Decimal
LB_PREMIUM5 = 历史收盘封板后次日close溢价>5%的Wilson下界
LB_FIRST_RED = 历史首板封住后次日close溢价>0的Wilson下界
LB_FIRST_SEAL = 历史首板尝试当日封住率的Wilson下界
m_pool = 同日冻结可靠性合格证券群全部有效首板尝试开盘溢价的事件加权均值
M_ADJ = (各股有效开盘溢价之和 + 5×m_pool)/(各股有效开盘样本n+5)
LIMIT_COUNT_250D = 含T最近250会话收盘封板次数
G_DNA = .25 PR_mid(LB_PREMIUM5) + .25 PR_mid(LB_FIRST_RED)
      + .25 PR_mid(LB_FIRST_SEAL) + .15 PR_mid(M_ADJ) + .10 PR_mid(LIMIT_COUNT_250D)
S_DNA = .60 S_PV_GENE_COHORT + .40 G_DNA
```

各百分位在固定 DNA 合格证券群计算，PV 的 A/R/H 也在该群重算，另存 original_S_PV。最终未舍入 S_DNA降序、ID升序。部分缺数标LIMITED_SCOPE；无合格增强样本为INSUFFICIENT_COVERAGE，诊断行不作为正常排名导出，不能拿PV原分冒充DNA完成。来源思想参考固定 `limit-up-sniper` commit `98edc5efcfe65f71e69f7bfca60e6dee88ee612e` 的 `core/gene_calculator.py`（MIT），本项目独立实现批准公式，不继承其账户、QMT、发信、昨日涨停剔除或交易规则。

### 5.8 THEME 事件题材增强完整定义

R05主题仅接受截止前可见且完整的 **EVENT** 主题修订；这是与DAILY可接收INDUSTRY的区别。成员先与当期完整资格池相交，至少5成员。先冻结当日有效唯一主题总体；每个目标在所有包含它的主题统计中剔除自身，不能只删目标所属的“最好主题”。同伴不要求自己通过PV条件。

每个同伴需连续6会话合法同基准价格与T可核验封板状态；有效同伴/应有同伴≥95%。若剔除目标后冻结总体任一主题统计变不可用，该目标本轮缺覆盖，不能静默缩小百分位总体。记同伴T封板数 u、ret5中位数 r5_med、当日可比收益>0比例 p_up：

```text
T_theme = .40 PR_mid(u) + .30 PR_mid(r5_med) + .30 PR_mid(p_up)
主主题 = 目标所属主题中最高T_theme，并列theme_id升序
S_THEME = .75 S_PV_THEME_COHORT + .25 T_theme
```

三个主题百分位的总体是同日冻结有效主题、对当前目标统一排自身后的各主题统计，不是只比目标的所属主题。随后在满足主题资格的PV子群重算A/R/H百分位，保留original_S_PV；最终未舍入S_THEME降序、ID升序。缺主题不补中性分，无增强名单不以PV替代。

情绪/昨日梯队晋级、封板队列收益/翻红/再封板、当日涨停/炸板宽度作为覆盖明确的上下文与分层分析，不加新的市场常数或硬否决，不覆盖DAILY M。主题构成变化与行情变化分开说明。来源只借鉴 `vibe-astock` commit `5764a22b4ec6cfdade0f35bd1d6fe66b3ef5e92c` 的 `duanxian/emotion_metrics.py`（Apache-2.0）上下文思想，.40/.30/.30与.75/.25是本项目假设，不称上游已验证选股模型。

## 6. 持续跟踪、后验与评价

### 6.1 研究周期和停止关注

当前 `TRACKING_POLICY_PILOT_V0.1` / `R05_RESEARCH_EPISODE_TRACKING_V2` 只对新焦点形成正式episode。保存首次signal、后续重复signal和稳定episode_id；重复入选不重启周期，不因为某日未上焦点、其他题材升温或达到固定关注天数就删除旧研究。失效/归档以后新合格信号形成关联previous_episode_id的新周期。

研究状态是ACTIVE、COOLING、THESIS_INVALIDATED、ARCHIVED，数据状态另列。每天对各自会话的价格计算含当日20个连续可比收盘的MA20，**连续两个有效交易会话 C*<各日自己的MA20**触发失效。未知打断已验证连续条件，不解释为通过或失败。

量缩条件用周期开始至当前的可比成交股数峰值：当前量/峰值≤30%的有效连续会话至少3个，且触发时ret5≤0，产生COOLING警示而非结束。实现中的计数要求各会话ret5可计算，非正判断在触发时；条件不再成立且数据可评价时可回ACTIVE。量的复权/无公司行动基准未知则量缩指标不可用，不能用成交额替代。缺价格、MA20或量基准分别保留原因。

人工归档已有 `POST /api/research-episodes/<episode_id>/archive`（reason必填），没有专用网页归档按钮。归档或失效停止默认活跃关注，但原始排名、信号和尚未完成后验继续保留；退出后另作5/10会话影子观察。退出不是从收益统计中剔除坏样本。归档是明确用户意图，普通查名单不自动执行。

### 6.2 价格后验、逻辑主键与成熟度

R05后验版本 `R05_STRATEGY_OUTCOME_OBSERVATION_V3`，默认 h=1/2/3/4/5/10/20，API支持自定义正整数h，均按交易会话。T信号的价格目标为T+h，待目标当日15:00上海时间结束才成熟。标签独立记录：

```text
close_reference_change = C*_(T+h)/保存的C*_T - 1
next_open_gap = O*_(T+1)/保存的C*_T - 1
mfe_bound = max(未来h会话可比high)/保存的C*_T - 1
mae_bound = min(未来h会话可比low)/保存的C*_T - 1
any_close_limit_up / down = 未来窗口至少一次可核验收盘涨/跌停
```

端点收盘变化不被中间缺一日或未知板事件自动抹掉；极值界需全部窗口可比高低价，板事件独立判定：已知一次真可判真，全部已知假才判假，否则NULL。每个metric有自己的成熟/缺数状态；整条标签CENSORED不意味着其中已核验端点收益不可用。日线无法判定止盈止损先后，开盘/极值是参考变化，不是成交结果。

逻辑标签主键含run、strategy、证券、观察日、h、规格/配置/实现及源码身份、metric_policy和label_kind。重评只追加revision，按报告cutoff选择最新有效版本；旧结果不覆盖。同一证券不同信号日保留，统计另以episode首次/窗口首次去重，不能把每次重复入选视作独立投资事件。未成熟、已成熟缺数、不可比和有效0涨跌分开：价格上涨率=有效成熟且收益>0个数/有效成熟样本数，0涨跌留分母但不算上涨，空分母返回NULL。

原DAILY结构目标独立：接力三族看下一交易会话收盘继续封板；TREND_NM看未来3会话至少一次封板；REBOUND_SETUP看未来3会话可比收盘收复上一轮结束收盘，另报3日封板。DAILY价格观察为1/2/3/5/10交易会话；更早旧组合记录的7/30/90自然日不改单位，也不混算。

### 6.3 模拟交易口径

[paper_execution.py](../stockfish_core/yaogu/paper_execution.py) 的 `NEXT_OPEN_HOLD_H_PAPER_V1` 是无账户研究情景。信号T，尝试入场 E=T+1开盘，计划退出 **E+h收盘，h≥1**。因此h=1退出T+2，不能拿价格T+1标签当同一持有期交易。买不到不追买；无下单指令。

入场需日期化停牌状态、可比开盘、已验证当日上下限价及合法日线；停牌为NO_FILL，未知限价/价格/状态或开盘触上下限为FILL_UNVERIFIED。日线边界内模型确认只是一种纸面可成交假设，不证明实际排队成交。退出遇停牌、跌停/无效限价或不可比价，保留OPEN_BLOCKED与每个受阻会话，尝试后续已结束且可核验收盘；保留市值参考和延期退出，不能删除受限持仓。

统一往返成本b=0/30/60bp，30bp主口径，买卖各一半且假设已含滑点：`net_return=(exit_C*/entry_O*)×(1-b/20000)/(1+b/20000)-1`。这是研究假设，不是当前券商税费报价。胜率只统计已验证闭合交易中净收益>0者；另列总信号、已尝试、可评价、closed、未成交/未证实/受阻与closed/attempted覆盖。无closed则模拟胜率NULL，不填0%，也不阻止有效价格研究。

### 6.4 对照、复盘与已完成历史范围

复盘 `R05_RESEARCH_FEEDBACK_V2` 提供5/20/60交易会话窗口；实际只有20个信号日时仍显示已观察天数，不能把请求60日当60日实证。并列报告合并signal、日期等权、唯一股票、episode首次与窗口首次，保留零名单、失败日期与未成熟样本。比较在同日期、共同资格、相同输入、持有期、成熟条件、成本下单列，同时保留各策略实际覆盖；不能只比较“各榜获胜者交集”。

R05历史诊断包括全资格池及成交额强度Top5/10/20等同条件对照，TopK只作诊断不改PV动态名单。DAILY历史评价配置保留252日历史/60日warmup、最多504请求会话；仅板数、ret5、amount_ratio与100次固定seed=20260905随机对照；bootstrap 1000次同seed；120/40/20 train/valid/test、步长20，按label_end做purge。±10%敏感性只研究，不自动上线最佳参数。是否实际跑足这些窗口必须查具体运行，不由配置存在推断成功。

既有R05已真实执行2026-08-31…09-04主五日，以及08-10…09-04共20个信号日的**历史重建**。主五日有沪深日期化资格认证，较早十五日仅深圳认证范围；全局仍LIMITED_SCOPE。主五日h=5/10/20在原回放截止未成熟，历史限价/可成交性不足，没有已验证闭合模拟成交胜率。正文不复述旧测试总数或单个收益百分比作为本轮结果；历史价格标签与独立工程golden、前瞻日常运行各属不同证据层次。

这些相关重复证券、短窗口及重叠标签不足以证明投资优势。AI回顾历史可能知道后续事实，仅限制资料日期不能证明模型忘记未来；目前没有本项目“PV对AI”的真实月度优势结论。

### 6.5 同股参与变化与趋势研究

[COHORT_PARTICIPATION_TRENDS_V1](COHORT_PARTICIPATION_TRENDS_V1.md)是显式、纯数学的研究组件，保留原 selector 与保存 run，由调用方先认证完整每日合格池、同日身份、来源时钟、规则身份和真实日历。普通声明、相等 basis 字符串及内容 hash 不恢复来源能力；模块本身不读 DB、网络或行情，未接默认 CLI、服务或正式选择。

输入保留 WATCH/FOCUS/REJECTED/UNKNOWN 和原 research_selected；未知决策不计已知未选中。相邻日只在共同 eligible 同股内计算留存、新增、退出及 Jaccard，资格 entering/leaving 单列，不能把停牌等资格迁移算成 setup 消失。跨缺交易日保留端点统计及缺日清单，不称连续留存。固定篮子要求两日全部成员资格与精确金额已知才有完整增长；每日更换成员后的 selected 总额另列，不能当同股金额扩张。多个篮子可重叠，跨题材总量及竞争关系仍须认证共享成员并去重；成交额不代表净流入。

逐股连续观察段按真实相邻交易会话累计，并对缺日、未知决策、资格、规则和首尾覆盖分别截断。这些长度是观察下界，不是完整 setup、持仓时长或情绪周期。资金、广度、集中度、市场相对强度、接力留存与修复质量先各自留证，再联合提出可证伪假设；小样本中的题材走弱不能自动否决全部个股，原 REJECTED 也不能替代后续价格核验。

本轮30项定向工程测试及128个独立 TEST_FIXTURE 枚举、5114项反算断言通过；5月12日至15日四份有限深圳资格 R1 全池事实的5517条每日记录及1380个唯一证券完成一次独立数学应用和全状态反算。上述检查不认证完整年度主线、历史盘前可见性或投资优势；跨完整独立周期的预测检验仍未完成，参数尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`。价格后验沿用6.2的同股可比口径，真实新情境、旧价格截止及报告生成时间分别保存，不把后取资料填入旧盘前。

### 6.6 历史复权续传来源链

[历史来源链研究桥V1](HISTORICAL_FACTOR_RESUME_LINEAGE_V1.md)是显式、独立的新版本适配器，组合冻结历史与换手适配函数。最终批次的RAW时钟仍经过原严格守卫；落在最终wrapper区间之外的逐股捕获，必须与实际较早PARTIAL祖先的完整三件、全值状态、factor行多重集合及真实区间逐项匹配。所有活动父来源完整SHA/stat及内存上下文在使用前后核对。缓存checkpoint、保存研究报告和普通JSON不恢复来源能力。

验证后的fragment与全部bars引用当前实际发行的研究凭证，且发行不得早于任一真实父归档完成；不带HistoricalTimeProof，不授权过去盘前。祖先缺RAW.first_seen仍UNKNOWN，原PARTIAL状态与旧时间缺口保留，归档代理时钟另列。未证明的股票保留RAW价格、金额、换手、资格及池范围，只把可比OHLC/basis置UNKNOWN；已有左锚和日期缺口继续由原函数判断。该桥不修改门槛或默认入口，持久history store接线不在此版本范围。工程TEST_FIXTURE、真实窗口及多周期投资检验分别报告。

## 原始父来源时间与行业代理来源

显式[晚派生来源时间V1](ORIGINAL_PARENT_ASOF_LATE_DERIVATION_V1.md)核验原始RAW、receipt、result及全部父时钟，将旧截止已实际可见的数值证据与今天派生行的真实时间分列。原文件和旧run保留，晚到父来源、未知历史资格和目标日历仍阻止相应结论；28项隔离测试已通过，真实窗口验收另列。

[Sina行业代理V1](INDUSTRY_SINA_CONTEXT_V1.md)在既有采集逻辑增加目录及成员两个endpoint，目录、原始节点计数、分页与逐证券身份分别验证。行业代理命名空间保留供应商事实；经济题材和历史成分完整性另证，未标日期的报价不进入量价计算。相关140项隔离工程测试通过；真实采集、主线覆盖、默认服务接线与投资验证分别验收。原东方财富失败和此前来源版本保留，R2选择及阈值保持。

### 复权值事实与强制祖先审计V2

[新事实来源与显式消费者](HISTORICAL_FACTOR_VALUE_FACTS_V2.md)保留全部真实PARTIAL祖先审计，将已证明完整的逐股值发行成当前截止事实；不会把原批次升级COMPLETE。新内存owner只描述实际inline范围，不伪称SQLite完整历史，不从保存JSON恢复权限。真实旧capture实现文件与当前validator身份分别核验，冻结R1通过一次显式消费者使用。

64项隔离工程测试通过；4月14日同日有限深圳1411股的真实对照保持完整资格、RAW/金额/CLV/可比价格及基准不变，来源不可用误拒354降至0，完整25会话窗口1057升至1411，WATCH31升至38，决策仍有1只UNKNOWN。4份原PARTIAL、381条原缺口与40条原可比价格未知保留。这是消费层修复，不能解释为真实市场情绪迁移，也不能认证沪市历史范围、全年完整周期或历史盘前预测。初始参数尚未验证，MARKET_EDGE=NOT_ESTABLISHED。

## 经济方向业务解释与完整观察集合

[经济方向解释V1](ECONOMIC_DIRECTION_EXPLANATION_V1.md)是一项显式保存结果附件。正式PDF及真实回执、逐页业务文字与转换来源绑定；完整供应商节点重新核验计数和全部分页，仍不发行经济全集或历史成员权限。原run、全部观察项、原精确数值和V4三种呈现角色保留。有限已证业务关系观察子集可显示原成交额与上涨比例；整体强度、净流入、当日题材归因及资金竞争另证，不用子集替代。

14项隔离工程测试及真实同61只附件通过，独立复核96份原件终态、完整并集及每项全文相等。当前5只电池关系与6只零售经营或资产关系已证，原固态、钠电、文化及模型仍并列；112节点与目录100冲突、零售96中的4只非A证券保留。材料期、其他业务、参股及终止项目逐项说明；新取得来源只在新截止解释，不回填原选择。尚未接默认服务、未证明整个经济题材及时完整，也未完成全年独立周期或PIT投资验证。

官方域名兼容特征V2仅增加已观察的深交所披露hostname，同页业务文字、HTTP完整状态、真实时钟和父SHA门槛保持。新的同61只解释增加天赐材料及金龙羽的有限电池业务关系，现为7只电池和6只零售经营或资产关系；材料类型、未经审计及项目收入0.09%等限制保留，不修改旧5只附件或原子题材排序。

新增[经济方向归档展示V1](ECONOMIC_DIRECTION_ARCHIVED_DISPLAY_V1.md)和薄封装显式读取入口，输出完整文本、JSON与清单。实际渲染索引、模型fallback角色及父/捕获时钟下界分别验证，保存自hash不绕过交叉核对。32项隔离工程检查、独立三反例拒绝及真实同61只交付分别通过；记录脚本的初次失败保留。该读取器不运行selector、不恢复来源capability或业务DB，不自动生成当日附件。完整年度周期、历史PIT、每日连续交付与投资优势仍分别待验收。
