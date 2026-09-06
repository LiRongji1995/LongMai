> 2026-09-06 最终身份修订：选中证券逐模型规范 JSON 后计算资格哈希，修复时区 Python 对象表示导致的 JSON 往返不稳定。V4 保持 V3 已纠正的原公式、阈值及名单语义，spec hash 不变；实际重新执行全部 20 个日期，原 V3 名单和源码保留审计。

# R05 规格到实现的追溯

本次仍为 R05。`PV_OBSERVATION_V1`、`DNA_RERANK_V1`、`THEME_CONTEXT_V1` 的参数恢复原已签发规格；实现版本为 `R05_STRATEGY_EXPERIMENTS_V4_CANONICAL_IDENTITY`。`DNA_REVERSAL_V1`、`THEME_LIMITUP_V1` 仅为旧客户端兼容别名，带 `compatibility_alias=true` 和原正式 `canonical_strategy_id`，不得作为两个额外实验重复计数。

数值输入、评分、入围及焦点比较使用有理数 `Fraction`。Wilson 平方根使用 50 位 Decimal，比例分项仍有固定可复算的输入计数；输出显示六位小数不会反向参与入围、同分或子池重算。所有新参数仍是 `INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED`；`MARKET_EDGE=NOT_ESTABLISHED`。

## 唯一规格与来源

| 文件 | 内容 SHA-256 | 作用 |
|---|---|---|
| `config/r05_strategy_experiments.yaml` | `8cd729cab31f80ec84d6486c733165360ba264fc3efb2d70c0c69f5a78148462` | 原 R05 实验 YAML 的逐字节副本，运行时所有数值参数的唯一来源 |
| `config/r05_completion_requirements.yaml` | `203325cad6cde76ca9399353af163e9b122b73b1ab2d3c8ecbfd1878fb3f5e11` | 原 Completion YAML 副本，焦点政策唯一来源 |
| `config/yaogu_v1.defaults.yaml` | `5863af482cc396a000477fdfcfe694aac162a935ece7fa11279a2e3ecc0376b6` | S0 原五族冻结文件，本次未改 |
| `config/r05_source_lock.json` | 运行时计算并并入 `source_hash` | 绑定前两份批准源文件及上游只读研究版本 |

原 YAML 中 `source_commit: RESOLVE_AND_PIN_BEFORE_IMPLEMENTATION` 原文保留；已解析的源码版本存入独立 source lock，并写入每次策略运行。加载时校验批准 YAML 的完整内容哈希，避免在原名称下改参数。不解释或执行 YAML 中的公式字符串。

每次运行绑定 `input_hash`、`effective_input_hash`、`universe_hash`、`config_hash`、完整 `spec_hash`、实际 `source_hash`、逐源文件 hash、`decision_cutoff`、实现版本及数据水位。运行 ID 由上述实际内容派生；每条排名含名称、精确因子分子分母、RAW 与可比价格、资格记录 hash、六日价格记录 hash、来源引用、未核验板事件及研究风险。相同输入但 cutoff 改变会创建新运行，原排名不覆盖。

`implementation_conformance(run)` 是独立追加审计接口：旧 V2 返回 `SPEC_NONCONFORMANT` 并排除合规绩效分母；缺充分绑定的其他旧记录返回 `SPEC_NOT_VERIFIED`。该函数不修改原历史文档。服务层负责保存追加审计及投影。

## 逐项实现与独立断言

| 发现/原规格 | 实现函数 | 可复核规则 | 测试 |
|---|---|---|---|
| F01 日期化资格及可见性 | `_prepare_context`、`_qualification`、`_select_revision` | 统一 EvidenceResolver；父回执状态/范围/四时钟；有效区间 `[from,to)`；按证券唯一化；沪深 MAIN/A_COMMON；非 ST/停牌/退市整理；60 个交易所会话；同一修订优先级冲突为未知 | 原样 29 探针的证券范围、上市50交易日、未来/INVALID/重复反例；新增同钟冲突测试 |
| F02 PV 精确规则 | `_complete_pv_row`、`_rank_pv`、`run_pv_observation` | 连续 T-5..T，A 为当日成交额/前五日均值，R 为同基准五日收益，H 分母为当日 reference_prev_close；先 A≥1.2 且 R>0，再对入围全体用 .45/.35/.20 中位百分位 | `test_pv_independent_three_row_golden_and_coverage_before_screen`；原 H、权重及 A=1 反例 |
| F03 焦点无配额 | `run_pv_focus_preview` | A≥2，R≥.10，CLV≥.70，close≥open；没有 H 门槛、名次门槛、最低或最高人数；旧 max_count 参数只作兼容，不能改变资格；零振幅明确不适用 | 原 45 项探针；新增 100 项且 max_count=1 仍全保留；显示四舍五入跨门槛反例 |
| F04 DNA | `run_dna_rerank` | 252 会话支持 T 前250成熟事件和含T的250计数；下一日 RAW open/close 用该日 reference_prev_close；三类 Wilson；原始首板成功率>70%、尝试≥3和必需分母>0；固定合格事件加权 m_pool、强度5；五因子 .25/.25/.25/.15/.10 百分位；子池 PV + G 的 .60/.40 | `test_dna_independent_counts_prior_factor_percentiles_and_6040_golden`；下一日参考价缺失独立分母测试；未来价格不改变旧分项测试 |
| F04 主题 | `run_theme_context`、`_theme_peer_stat` | 一份当日可见完整主题修订；成员为完整资格池；至少5成员，必需同伴覆盖≥95%；排除目标自身；冻结唯一主题总体；同伴收盘涨停数/五日收益中位数/当日上涨比例的 .40/.30/.30；主主题最高T后theme_id；合格子池 .75/.25 | `test_theme_independent_frozen_theme_population_and_daily_up_golden`；过期/未来主题与INVALID/PARTIAL父凭证；原非PV同伴反例 |
| F09 同条件与权威语义 | `_pv_population_scores`、`source_identity`、`strategy_comparison` | 子池按原精确因子重算，保留 original_S_PV；无 DNA/THEME 的 PV 替代名单；共同池需以资格集合单独做绩效比较，交集字段只作描述 | hash/cutoff/旧记录只读审计测试；服务/API及周回放由统一验收单独验证 |

资格分区保存已知排除、资格未知、目录缺失及实际唯一 ID。PV 的字段覆盖分母是完整合格证券池，分子为所有必需字段完整的证券，**不是通过 A/R 门槛后的候选数**。完整数据上 0 个入围为 `EMPTY`；未取到或不可验证为 `UNAVAILABLE`；真实有限范围可保存为 `LIMITED_SCOPE`。`eligible_feature_items` 暴露完整字段的全部资格样本，供主回放独立等权与单因子诊断使用。

历史行情后取并非当时实际发布。核心不提供全局关闭时钟检查的路径；历史重建仍走 `RECONSTRUCTED_HISTORICAL` 与作用区间明确的 `HistoricalTimeProof`，真实 first_seen/ingested 不更改。资料发布时间晚于截止时间仍拒绝。周回放数据装配与来源时点证据是独立审计对象，不能因这里的工程测试通过直接获得严格 PIT 认证。

DNA 的窗口中未知事件会使完整250日事件计数不可用；已知事件但下一日溢价参考字段未知，只从相应成熟结果分母排除，不记失败。主题在剔除目标后若使冻结主题总体中的一个统计不可用，目标本轮明确未覆盖，避免悄悄更换百分位总体。

情绪仅输出明确声明的“当日唯一资格证券群”上的昨日梯队晋级及分母、昨日涨停队列今日可比收益/翻红/再封板、当前涨停与炸板宽度和已核验高度/缺口。字段覆盖不足为 `UNAVAILABLE`；空梯队为 `NO_COHORT`；不向个股评分加市场常数，不覆盖原 DAILY 的 M 公式。该群体不是无偏的全历史入市/退市总体，跨日期评价需要保留群体变化。

## 独立手算结果

`tests/r05_product/test_r05_spec_goldens.py` 全为显式 `TEST_FIXTURE`，不是历史行情或投资验证。三个核心手算例：

- PV：A 从低到高、R 和 H 不同序的三证券，分数分别 46.666667、45、58.333333，按精确值排序为第三、第一、第二。
- DNA：合格股票分别有 3/3 和 3/4 首板封住，开盘溢价事件数分别3和4；不合格第三股不进入先验。股性 G 为66.25和33.75，子池PV并列50，最终分56.5和43.5。当日额外一板只进入含T计数，未知次日参考价只剔除依赖分母。
- 主题：两组各5成员，各自排除目标后4同伴。A组当日上涨、B组五日收益更高但当日下跌；主题T为60和40。子池PV为41.25和58.75，最终分45.9375和54.0625。这样能识别把五日收益正负冒充当日上涨的错误。

实际 runner 日志、命令、退出码、JUnit 和环境写入统一 `review/r05-rectification/` 证据。全量业务、独立审阅、真实行情回放、Docker 和市场表现分别报告。

## 上游只读研究和适配差异

2026-09-06 实际读取并下载以下固定 commit 的目标源码及 LICENSE，文件大小与 hash 见 `review/r05-rectification/upstream-research/pinned_source_manifest.json`。没有导入或执行外部交易、邮件或通知入口。

| 上游 | 固定版本及许可 | 原对象与本项目差异 |
|---|---|---|
| [limit-up-sniper](https://github.com/guoyaohua/limit-up-sniper/tree/98edc5efcfe65f71e69f7bfca60e6dee88ee612e) | `98edc5efcfe65f71e69f7bfca60e6dee88ee612e`，MIT | 实读 `core/gene_calculator.py`、`core/stock_pool.py`、`docs/core-strategy.md`。参考历史板事件、成熟下一日溢价与样本修正思想。本项目依据批准R05独立实现数值模块；不移植QMT/账户、当天首板买入、昨日涨停排除、自然日新股门槛、低价股门槛或发送邮件；不用原当前股票资料倒填历史限价。 |
| [vibe-astock](https://github.com/simonlin1212/vibe-astock/tree/5764a22b4ec6cfdade0f35bd1d6fe66b3ef5e92c) | `5764a22b4ec6cfdade0f35bd1d6fe66b3ef5e92c`，Apache-2.0 | 实读 `duanxian/emotion_metrics.py`。借鉴晋级、涨停队列与历史定稿数据的上下文思想；.40/.30/.30 主题分和 .75/.25 组合是批准的本项目假设，不声称上游提供或验证该选股模型。没有移植腾讯当前行情回填、缺连板值补1或较低覆盖率门槛。 |

上述许可原件随隔离研究文件保留，产品运行代码是按用户冻结规格独立重写的实现。上游项目的测试声明或历史表现均未算成本项目通过或市场优势。最初 GitHub API 获取尝试失败，后经 GitHub 固定 commit 页面及 raw 固定路径成功；失败记录未用成功输出覆盖。
