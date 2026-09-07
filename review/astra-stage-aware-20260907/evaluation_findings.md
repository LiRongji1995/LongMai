# Astra 独立评价体系审阅证据

审阅仓库：`D:\stockfish-qlib-quant-system`；父审阅冻结 HEAD：`a9c6deec75319ce0fb2d6fabca0a4c6021323c05`。本子任务只读正式源码/本机既有输入，输出限定本目录。结论：评价工程基础 **PARTIAL**；现有真实资料 **不能判断新策略是否进步**。没有证据可以宣称策略具有投资优势。

## 1. 本轮执行与分母

`ACTUAL_EXECUTION`：在仓库根目录运行以下命令，均退出码 0。

```powershell
& stock-fish/.venv/Scripts/python.exe review/astra-stage-aware-20260907/evaluation_recompute.py
& stock-fish/.venv/Scripts/python.exe stock-fish/scripts/run_core_tests.py tests/r05_product/test_replay_statistics.py tests/r05_product/test_weekly_paper_execution.py tests/r05_product/test_outcome_context.py -q
```

第一项调用当前 `run_strategy_experiments`，使用真实保存 `market-input.json` 及原 cutoff；复算结果与保存新版 stage 的 items/counts/qualified_ids/top_ids/status 全部一致。旧 PV、DNA、theme 的名单、覆盖和相关字段及旧焦点名单/政策 hash 全部一致。详细布尔核对、输入 hash、分母和 ablation 见 [evaluation_actual_summary.json](evaluation_actual_summary.json)；实现见 [evaluation_recompute.py](evaluation_recompute.py)。未保存全市场输入副本或改写历史运行。

第二项实际得到 **23 passed in 0.97s**。它验证零收益分母、日期等权、修订去重、未成熟、episode、纸面成交和 outcome context 的既有合成测试；不是 23 个真实投资案例，也不是策略有效性证据。没有运行全仓测试。

真实观察日只有 **2026-09-04**。原 cutoff **2026-09-07T09:17:11.036672+08:00**；输入包含 27,587 行行情，但行情日期只有六天，交易日历虽有 535 日却只到 09-04；主题 0，资金 0。保存覆盖类型是 `PROSPECTIVE_CAPTURED`，run 标记 `ARCHIVED_POINT_IN_TIME`，然而该信号使用 09-07 实际取回资料、对应 09-04 观察日；本审阅只把本轮计算称为**晚取真实保存输入的复算**，不把标签当作 09-04 当时前瞻存档的证明。

本仓 `reports/codex` 可用策略文件两份（旧和新），业务观察日相同。缓存中的两个 SQLite 经 URI `mode=ro` 读取 schema：`request-queue.sqlite3` 仅 requests/attempts；`transport-budget.sqlite3` 仅 http_budgets/transport_attempts。未发现它们包含历史候选或后验价格表。没有以旧 `D:\StockRecommendation` 为执行依据。

## 2. 真实覆盖与共同池

| 对象 | 当前实际结果 | 解释 |
|---|---:|---|
| 日期化合格证券池 | 3,043 | 资格池，不等于每个策略的完整特征池 |
| 六日 PV 字段完备池 | 3,039 | 缺 4 个 amount mean5；不能作为 0 收益 |
| PV 实际名单 | 525 | LIMITED_SCOPE |
| 旧焦点实际名单 | 17 | PV 父名单内动态筛选，无固定配额 |
| 新阶段正式名单 | 0 | INSUFFICIENT_EVIDENCE；FAIL 2,844，UNKNOWN 199 |
| DNA / theme context | 0 / 0 | INSUFFICIENT_COVERAGE |
| theme-capital 技术诊断 / 完整 / simple template | 0 / 0 / 0 | INSUFFICIENT_EVIDENCE |
| 新阶段四个顶层条件均为已知（PASS 或 FAIL） | 0 | 全池 sector/funds UNKNOWN |

“PV 字段完备 ∩ 新阶段四条件均已知 = 0”是本审阅选择的**最严格共同条件可判定诊断**，不是系统已经登记的唯一正式共同数据合格池。已知 FAIL 与未知要区分；不能把各策略共同选中部分称为共同合格池，也不能仅凭一个终态 FAIL 推断全部特征均齐备。当前源码尚未为跨阶段/旧策略给出一致的必需数据覆盖定义。

`STATIC_REVIEW`：`strategy_tracking.py:521–524` 输出 `same_date_common_eligibility` 和 `strategy_actual_coverage`，后者直接采用各 result 的 `qualified_ids`。这些字段语义不同：PV (`strategy_experiments.py:270`) 是日期化资格全集，stage (`stage_aware_momentum.py:1095`) 是正式 selected，DNA (`strategy_experiments.py:390`) 是可重排集合。直接横比这些 counts 容易把资格覆盖与筛选通过混淆。

## 3. 四格及落选原因

`STATIC_REVIEW`：不能断言常规链路没有全池标签。`candidate_service.py:517–528` 已加入“日期化资格 + 信号日可比收盘”基准；`strategy_tracking.py:104–128,145–238` 会为该 baseline 产生价格标签。周回放另用六日 PV 完备池（`scripts/r05_weekly_replay.py:79,92–102`），两个基准定义不同。本轮独立脚本明确使用后者 3,039 条作为诊断，不伪装为已有服务保存 baseline。

缺口在 `strategy_tracking.py:458–540` 的反馈聚合：能汇总选中策略、焦点、同日基准、episode 和市场状态，但没有把**全合格证券 × 各策略选中状态 × 原排除原因 × 后验标签**连成四格及条件贡献表。`candidate_service.py:681–687` 的 `selection=unselected` 表示“本策略已排名 rows 中不在旧 focus IDs 的证券”，不是完整股票池中未被该策略选中的证券；stage 的未通过诊断 rows 在 authoritative 排名集合之外。这是范围语义，不应向用户当作全池落选研究。

`ACTUAL_EXECUTION`：本轮按完整合格池保存四格模板和**互斥排除原因组合组**，含缺资料证券，不重复累计多重原因；每组未来上涨/下跌/持平都为 null、成熟分母 0。全池 PV：选 525、未选 2,518；未选中 A 单独不达标 791、R 单独不达标 278、A/R 同时不达标 1,445、均额缺失 4。旧焦点：选 17、未选 3,026；其中 2,518 未进入 PV 父池，508 在 PV 内被焦点拒绝。新策略：选 0、未选 3,043，但其中 UNKNOWN 199 不能称已证明坏样本。

**选中上涨/下跌、未选上涨/下跌及各排除原因的未来表现均 `NOT_VERIFIED`**，不是零胜率。终点价格标签总 17,820 = (525 PV + 3,039 完备池基准) × 5 窗口；旧焦点标签从 PV 子集派生，不另加独立信号。每个窗口可成熟价格分母为 0，主窗口 1/2/3/5/10 日均无可用未来价格和未来日历目标。

## 4. 单条件消融与 Case Rescue 群体

`ACTUAL_EXECUTION`：只删除现有失败条件一项，用保存原因/三态组合重算通过数；没有改变正式策略阈值，没有寻找最佳收益，没有把 UNKNOWN 改 PASS。

| 冻结对象及删除条件 | 原通过 | 新增通过 | 新总数 |
|---|---:|---:|---:|
| PV：A ≥ 1.2 | 525 | 791 | 1,316 |
| PV：R > 0 | 525 | 278 | 803 |
| 旧焦点：A ≥ 2.0（PV 父池不变） | 17 | 12 | 29 |
| 旧焦点：R ≥ 0.10（PV 父池不变） | 17 | 43 | 60 |
| 旧焦点：close ≥ open（PV 父池不变） | 17 | 0 | 17 |
| 旧焦点：CLV ≥ 0.70（PV 父池不变） | 17 | 13 | 30 |
| 新正式策略：分别删除流动性/阶段/板块/资金任一顶层条件 | 0 | 各 0 | 各 0 |

新策略顶层单删都无新增正式 PASS，是其余未知门槛仍在，不证明这些条件无贡献。MA/长期趋势数据普遍缺失，不能给出删除均线后的真实好坏贡献；板块/资金缺真实输入也无法估计经济意义。这些效益项均 `NOT_VERIFIED`。

| 阶段诊断（允许重叠） | 阶段 PASS | 阶段 + 绝对流动性 PASS | 此诊断中 PV 外 | 此诊断中焦点外 |
|---|---:|---:|---:|---:|
| START | 525 | 92 | 0 | 86 |
| ACCELERATION | 221 | 118 | 50 | 113 |
| HIGH_LEVEL_DIVERGENCE | 76 | 47 | 5 | 41 |
| TREND_PULLBACK | 0 | 0 | 0 | 0 |

最后两列均以“阶段 + 流动性”集合为分母。国芳通过 HIGH_LEVEL_DIVERGENCE 与流动性但不通过旧焦点；同一路径还有 **40 只其他焦点外证券**。只删旧焦点 CLV 条件会加入国芳及 **12 只其他股票**。金健通过 ACCELERATION 与流动性但不进旧 PV；同一路径还有 **49 只其他 PV 外证券**。只删旧 PV 的 A 条件会加入金健及 **790 只其他股票**。这几组定义不同、允许交叉，不相加，不替代正式名单。

两只案例正式状态均 UNKNOWN（板块、资金缺证据）；新正式名单没有“完美救回”两只。所有新增组后验分母仍为 0：不能判定新增了多少好样本/坏样本，也不能称其成功提升。“语义覆盖更宽”与“预测有效”必须分开。当前仅一日且案例参与设计的背景使事后归因风险存在，尚无未参与设计窗口可解除风险；没有实证证明发生过网格调参或只保留最优版本。

阶段重叠真实计数：0 个 PASS 2,395；1 个 528；2 个 66；3 个 54。该重叠只描述本日诊断，不能解释为独立样本或多个交易机会。

## 5. 价格、日期权重、重复证券与模拟交易

`STATIC_REVIEW`：`replay_statistics.py:12–35` 和 `strategy_tracking.py:378–392` 已区分 pooled 与逐日等权、零收益与正收益、未成熟与不可比，返回 unique instruments / signal dates。`replay_statistics.py:72–75` 的成熟补充窗口按日历选择，未按收益挑日期。`strategy_tracking.py:434–455` 与反馈保留真实 episode-first 和 window-first 区别；没有独立 stage episode 政策时明确披露。属于已有正确工程基础，不应重复指控其仍简单把重复证券当独立抽样。

但现有一日资料无法比较 pooled/date-equal 实际价格差异、连续重复证券、成熟多窗口，也不支持可泛化置信区间。统计单位须为“证券 × 信号日 × 策略（窗口另列）”，不是独立随机交易次数。

`STATIC_REVIEW`：`paper_execution.py:17–29,49–118,121–135` 清楚分离价格标签和纸面交易。次日 open 尝试买；同日不能卖，h=1 最早退出是信号 T+2 收盘；开盘边界、限价缺证、停牌保留不可确认/无成交；跌停或缺证退出保留开放状态并延后尝试。成本 0/30/60 bps 是全包研究假设，滑点并入假设，**不是核实的真实佣金/印花税费率**。日线非边界模型不证明实际可成交，不得称实盘可实现胜率。纸面 h=1 与价格标签 h=1 的终点本就相差一天，横比必须保留各自日期。

`ACTUAL_EXECUTION`：本轮纸面输出 **2,710 行 = (525 PV 信号 + 17 焦点信号) × 5 持有窗口**，不是 2,710 只股票或独立交易；PV/焦点重复证券保留于不同策略组。所有 attempted/evaluable/closed 为 0，成本情景 win_rate/mean_return 为 null。其标签是 `NOT_MATURED`，本数据集还缺 09-04 之后的交易日历/价格，不能推断现实市场本时刻一定尚未发生所有后验。

## 6. 尚未验证与建议

以下要求明确 `NOT_VERIFIED`：至少最近五个可用信号日完整比较（当前只有一日）、后续四格/排除原因收益、主要条件避免坏样本/错过好样本的数量、Case Rescue 新增群体后续表现、样本外有效性、日期等权真实收益对比、重复证券时间依赖和成熟长期窗口。没有用合成数据补齐真实历史需求。

少量后续建议，仅供决定，不实施：

1. **P1 评价口径**：给每个策略分开记录日期资格池、必需字段已知池、规则通过池和正式名单；登记横比共同池，避免直接比较语义不同的 `qualified_ids`。
2. **P1 落选复盘**：利用已有 baseline 标签连接全池未选与原始排除原因，按信号日/窗口输出四格，并同时留独立未知、持平和未成熟；不是另建平行名单。
3. **P1 研究证据**：在新的未参与设计窗口冻结现行规则/参数与真实可见时点，积累成熟结果后做单条件贡献。当前停止于覆盖诊断，不以国芳/金健是否被标记评价策略成功。
4. **P3 暂不做**：不得根据本日 ablation 的候选数量自动调参或宣称最优方案；无需为追求投资结论购买来源或进行全历史广泛回放。

GPT-5.5 可负责字段一致性、四格连接、报表和既定公式维护，必须配独立分母/三态/历史时点回归。共同池或评价方法发生重大语义变化时，值得再次独立高级审阅。
