# R1 冻结研究规格 v1

本规格于 2026-10-01 在查看本轮收益前冻结。默认关闭，RESEARCH_ONLY，INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED，MARKET_EDGE=NOT_ESTABLISHED，production_promotion=DISABLED。V2 保持默认及历史合同。

机器 manifest 为 `config/stage_aware_research_r1.json`；每次显式执行先独占创建输出目录并写入包含输入、源码、配置与本规格哈希的 manifest，再计算结果。初始没有冻结真实区间、独立验证或留出样本，不将 fixture 或已看过样本声明为市场留出集。主要比较 R1_FULL−V2_CONTROL；种子 20261001，连续会话块长 20，Holm 校正，主滑点每边 10bps，0/30bps 对照。全部合格候选参加研究，无 top-N。

## 规则

沿用原 `_prepare_context` 资格、来源、可见性、冲突和可比价格规则。共有成交额 ≥5亿元、已核验收盘非跌停；跌停未知保留 UNKNOWN。取消统一当日换手门槛；START 前5日不含T高换手计数≤2，ACC 最近5日含T高换手≥3，阈值7%，均保留。

START / ACCELERATION / HIGH_LEVEL_REPAIR：C(T)>MA20(T) 且 MA20(T)≥MA20(T−5会话)。TREND_PULLBACK 沿用 V2 全长期趋势、1–3会话回踩、贴近MA5/10/20、缩量和相对韧性。前三形态长期MA仅诊断。

START 原语不改：T−1结束的前5会话收益≤8%，前5日高换手≤2，C(T)>前5收盘最高，amount(T)/前5平均≥1.2，ret1>0，rs1>0，共享动量RISING。不启用配置中未实际生效字段。

ACC：(ret5≥15% OR ret10≥25%) AND rs5>0 AND ret1>0 AND ret3>0 AND rs1>0 AND rs3>0 AND RISING AND 最近5会话含T至少3日换手≥7%。以 Fraction 比较，OR已知通过优先；AND已知失败优先。

共享强度动量复用 EQUAL_WINDOW_RECENT_MINUS_PREVIOUS_3_SESSION_RETURN_AND_RS_V1：最近3会话收益与前一等长3会话收益之差；相对主板等权基准的同期RS也取相同窗口差。稳定带±0.001，组合状态按V2原语；不挑指数基准。

HIGH_LEVEL_REPAIR 保留T−1及以前背景、T日分歧/修复/必要相对强度与行为确认；日线解释标DAILY_OHLC_ONLY，不引入次日反转、修复时延或突破保持。每个setup独立完成共有、趋势、识别、行为与反证；任何完整PASS即CORE_PASS；无PASS且有UNKNOWN为CORE_UNKNOWN，否则CORE_FAIL。共同硬失败可NOT_EVALUATED。展示顺序不影响集合。

市场/板块/资金/供应压力在R1_FULL仅为标签；各消融明确恢复硬门槛。题材+资金双硬复用V2同链判断；资金单硬通过已验证题材身份直接使用原资金校验，不再要求板块强度。既有V2未知收盘跌停可能不阻断FOCUS，这是原行为差异：V2_CONTROL与V2_ACCEL_ONLY保持原样；仅R1_FULL增加显式安全未知检查；其他消融保留原检查，确保单改组不夹带这一合同修正。完整组另记录此安全差异。

## 执行与组合

独立 EXECUTION_RESEARCH_R1；T+1开盘入、T+6开盘计划出。严格早于T+1上海09:15的真实发布时间及订单输入可见时间；历史重建独立标识。预先提交价格计算整手数量、滑点后价格必须满足限价和现金，不用未来开盘倒算数量。入场受阻不追入，出场受阻逐会话检查，未退出保留EXIT_PENDING和资本占用。

执行使用原始价格，比较价必须有一致基准；企业行动缺口拒绝完整收益。普通开盘仅ASSUMED_OPEN_FILL。规则、费率须日期化、有来源；无已验证规则不计算真实执行。fixture规则仅用于工程测试。真实佣金未知，费用为显式假设场景，佣金最低费双边计算、税卖方计算、过户双边计算，不与佣金重复包含。

纸面初值100万元。候选去重，已有同股不加仓、不延长退出。先等预算，再按单股20%、每个题材40%、现金约束取全体最小同比例缩减系数；多题材全部计入，未知标覆盖不足。整手/未成交余款留现金；同开盘卖出回款不用于该次买入。已有持仓按截止前可见估值计入，缺估值阻止新预算；漂移仅限制新增。

实际实现、测试和阻塞统一记录在 `R1_IMPLEMENTATION_RESULT.md`。本规格不意味着所有交付均已验证，也不授权生产接线或自动调度。

## 入口、身份和规则来源边界

`research-r1 --input exported.json --output <私有新目录>` 是唯一接线；可重复 `--input`、`--group`，支持 `--start/--end/--manifest`。文件外壳是 `input: MarketInput, cutoff: 带时区时刻, publication: 可空的原发布时间证据, execution_tape: 可选`。已有run必须提供其冻结原始输入，不能从展示行倒造事实；不支持只给run ID自动回查生产。

引用历史分片的导出须显式 `--history-db`；复用 ReadOnlySQLiteStore + HistoryArtifactStore/bind_history，最多10万条声明分片行。拒绝复制数据库或跳过分片哈希。真实重算一律标RECONSTRUCTED_HISTORICAL，原输入采集属性另外保留。研究结果只写本地忽略目录；SQLite研究namespace适配只在隔离fixture调用，无生产自动接线。

本轮已查阅 [上交所2026交易规则发布说明](https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml) 与 [深交所2026交易规则通知](https://investor.szse.cn/lawrules/rule/trade/t20260424_620190.html)，均说明2026-07-06起施行，不能外推覆盖此前历史。[财政部证券交易印花税公告](https://www.mof.gov.cn/caizhengshipin/caizhengxinwen2/202308/t20230828_3904230.htm)对应2023-08-28起减半。以上是资料索引，尚未形成证券×日期完整执行规则库；中国结算历史过户费证据与账户佣金仍未齐备。effective_to未知，不能虚填永久有效。

因此真实执行tape适配当前明确UNAVAILABLE。可运行的执行/组合验收仅接受TEST_FIXTURE；fixture内有效区间等于合成会话区间，100股/手、0.01元价格单位、佣金0.0003且最低5元、过户0.00001、卖方税0.0005，均为工程假设，不声称任何真实日期适用。企业行动只实现“有完整无行动证据”路径；真实分红/拆并股账务转换未实现，保留不可评价状态。

日期块估计helper采用固定20会话循环移动块bootstrap、2000次、中心化双侧p值及全预登记族Holm；至少40个连续共同完整日期，否则UNAVAILABLE。延迟退出≥20会话拒绝这一估计。信号独立假设与真实重叠资金账户分开输出；统计helper的fixture测试不代表真实统计适配或留出验证完成。

初次规格记录位于忽略的review/r1-baseline。实现复审明确了FULL未知跌停保护、只读历史界限和执行未支持路径；这些是软件合同补充，没有根据收益改变阈值或选组。每次演示的manifest冻结当次最终规格及所有来源哈希；源码中途变化拒绝发布结果。
