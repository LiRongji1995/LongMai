# R1 实施主结果（2026-10-01）

ENGINEERING_STATUS = PARTIAL_VERIFIED。
MARKET_VALIDATION_STATUS = INCONCLUSIVE。
MARKET_EDGE = NOT_ESTABLISHED；production_promotion = DISABLED。

已交付可运行、默认关闭的量价研究核心、12组消融、独立执行/组合fixture引擎、显式离线CLI与JSON/Markdown报告。未声称整个附件的工程验收全部完成：真实执行证据适配、企业行动账务和真实成对日期统计接线尚有明确缺口。下文区分已执行的检查和未完成项。

## 工作区与边界

起始与交付HEAD均为 `d8091a4e9f76a6ff783c132f9519cdc520068e24`；当前分支 `review`，初始dirty为空。实际origin为 `git@github.com:LiRongji1995/stockfish-qlib-quant-system.git`；本轮 `git ls-remote origin refs/heads/main` 同为上述SHA。没有切换分支、创建worktree、提交、推送、合并或改写历史。

实际变更：

- 新增 `config/stage_aware_research_r1.json`：冻结配置、12组父配置与差异。
- 新增 `stockfish_core/yaogu/research_r1.py`：复用V2事实/原语，独立研究决策。
- 新增 `stockfish_core/yaogu/execution_research_r1.py`：独立执行/预算模拟。
- 新增 `stockfish_core/yaogu/research_r1_io.py`：manifest、只读输入、报告、隔离SQLite适配、统计helper。
- 修改 `scripts/codex_research.py`：仅增加显式 `research-r1` 子命令及懒加载分派。
- 新增 `tests/r05_product/test_research_r1.py`、`tests/support/research_r1_fixture.py`、`tests/support/r1_v2_golden.json`。
- 新增本报告与 `docs/STAGE_AWARE_RESEARCH_R1.md`；综述增加该手动研究入口与限制。

初始patch、环境、原输入、完整golden、测试日志及私有结果均在根目录 `review/r1-baseline/`、`review/r1-accepted-fixture/`、`review/r1-accepted-real/`；均为Git忽略目录。没有修改真实.env、生产数据库、服务、调度或账户。真实演示只读既有离线快照库，未复制整库或采集行情。

## 规则与原策略保护

规格：[STAGE_AWARE_RESEARCH_R1.md](STAGE_AWARE_RESEARCH_R1.md)。配置：[stage_aware_research_r1.json](../config/stage_aware_research_r1.json)。

|规则|R1_FULL|原V2|
|---|---|---|
|身份/日期/可见性/可比价/资格|复用原事实解析；不放松|不改|
|成交额|≥500,000,000元|不改|
|当日换手|取消统一7%；START/ACC内部条件保留|不改|
|前三形态趋势|C(T)>MA20(T)，MA20(T)≥MA20(T−5会话)|不改|
|趋势回踩|原长期趋势与回踩原语|不改|
|ACC|绝对ret5≥15% OR ret10≥25%，再AND rs5>0、ret1/ret3/rs1/rs3>0、原RISING、5日含T高换手≥3|原HIGH算法不改|
|市场/题材/资金/供应压力|独立标签；资金仍验证同链与来源|不改|
|独立setup|全部完成决策；任一PASS入CORE_PASS|原primary优先不改|
|跌停未知|共同安全UNKNOWN|原历史行为保留|
|输出|CORE_PASS/FAIL/UNKNOWN；不写旧FOCUS|原合同不改|

没有移动或修改 `stage_aware_momentum.py`、`strategy_experiments.py`、`candidate_service.py`、validation_identity、daily_research、scheduler、daily_read_context、评分源码或V2配置。R1只在用户显式命令中导入；关闭时无额外全池计算/网络请求。

在源码修改前以固定TEST_FIXTURE和 `2026-09-11T20:30:00+08:00` 保存完整V2结果与验证身份。可分享golden只保存该完整结果digest和全部验证身份，测试不重写golden。当前完整结果相同、验证epoch相同，**允许叶子差异列表为空**，没有忽略metadata。原快照、后验、查询、评分、历史读取由下述原测试验证。

独立研究source hash覆盖3个新模块、配置、规格、CLI、SQLiteStore及V2原来源闭包；每组配置hash、冻结输入hash、截止、manifest hash均保存。CORE结果不进入旧缓存、FOCUS统计或official score。

## 消融与演示

|组|父组/唯一差异|
|---|---|
|V2_CONTROL|原V2完整结果；选FOCUS，WATCH仅诊断|
|V2_ACCEL_ONLY|CONTROL，仅换ACC识别；保留原setup优先、原决策及组内排序|
|V2_SETUP_ONLY|CONTROL，仅独立完成各setup链路|
|SEMANTIC_BASE|SETUP_ONLY，再换ACC识别|
|SEM_NO_GLOBAL_TURNOVER|BASE，仅取消统一换手|
|SEM_SETUP_TREND|BASE，仅分形态趋势与对应反证|
|SEM_MARKET_TAG|BASE，仅市场改标签|
|SEM_SUPPLY_TAG|BASE，仅供应警告不阻止|
|SEM_THEME_HARD_FUNDS_TAG|BASE，仅资金改标签|
|SEM_THEME_TAG_FUNDS_HARD|BASE，仅板块强度改标签；资金仍用日期化成员与原资金校验|
|SEM_THEME_TAG_FUNDS_TAG|BASE，题材/资金都改标签|
|R1_FULL|上述组合，另显式启用未知跌停安全保护|

所有组共用一次V2事实上下文，未在线改写CONFIG，也没有12份策略源码。单改组配置差异有断言；FULL安全差异在manifest单独声明，未偷偷加入V2_SETUP_ONLY。

完整fixture包含8只股票；FULL为CORE_PASS 1、CORE_FAIL 7。CONTROL为FOCUS 0、WATCH 7、REJECTED 1。ACC_ONLY为WATCH 1、REJECTED 7；SETUP_ONLY为CORE_UNKNOWN 7、CORE_FAIL 1；SEMANTIC_BASE及七个单项/四格组均为CORE_UNKNOWN 1、CORE_FAIL 7。它们在该fixture中的选择集合相同，不意味着实现等价或市场表现相同。FULL执行有1条CLOSED，0/10/30bps均有独立记录；随机和ret5同数量对照使用同一执行引擎。fixture不是市场样本。

真实有界演示使用原保存的2026-09-30导出，配套manifest声明9,212条历史分片行；原master 4,606、资格池3,040、已知排除1,566。用今天的资料截止重新研究，原输入采集属性保留，信号明确标 **RECONSTRUCTED_HISTORICAL**，无历史发布时间、不构造前瞻记录。真实评价只是一个资料不足日期；不作为开发/验证/留出拆分，不宣称验证集。

真实结果及最终run_id见文末验收摘要。零CORE_PASS仍有必要事实UNKNOWN，属于INSUFFICIENT_EVIDENCE；不是完整评价后的合法空仓，不能记零收益。

## 可复现命令

以下工作目录为仓库根目录，Python是本机项目 `.venv`，输出路径每次必须不存在；旧目录不会被覆盖。

```sh
# 本轮实际使用的fixture输入已经保存在忽略目录。
stock-fish/.venv/bin/python stock-fish/scripts/codex_research.py research-r1 \
  --input review/r1-baseline/demo-input.json --output review/r1-accepted-fixture

# 单组R1或V2；新输出目录示例尚未运行。
stock-fish/.venv/bin/python stock-fish/scripts/codex_research.py research-r1 \
  --input review/r1-baseline/demo-input.json --group R1_FULL --output review/r1-reproduce-r1
stock-fish/.venv/bin/python stock-fish/scripts/codex_research.py research-r1 \
  --input review/r1-baseline/demo-input.json --group V2_CONTROL --output review/r1-reproduce-v2

# 全部消融复用冻结manifest；本轮已运行，run_id与原运行相同。
stock-fish/.venv/bin/python stock-fish/scripts/codex_research.py research-r1 \
  --input review/r1-baseline/demo-input.json --manifest review/r1-accepted-fixture/manifest.json \
  --output review/r1-accepted-replay

# 本轮实际执行的真实只读演示（没有执行tape，不会有真实收益）。
stock-fish/.venv/bin/python stock-fish/scripts/codex_research.py research-r1 \
  --input review/r1-baseline/real-export-envelope.json \
  --history-db review/data-release-20261001/snapshot/database.sqlite3 \
  --group R1_FULL --group V2_CONTROL --output review/r1-accepted-real
```

新检出生成合成输入：在 `stock-fish` 工作目录执行 `.venv/bin/python -m tests.support.research_r1_fixture <不存在的私有JSON路径>`。fixture日期是故意不规则的合成交易日历，不是交易所真实日历。`--input`可重复，`--start/--end`按观察日期过滤；一天仅一个冻结输入。CLI不读取.env或连接HTTP生产服务。现有run ID需先导出其原始MarketInput；仅展示行不足时拒绝重建。

## 执行、组合和统计状态

- 已验证：T+1开盘入、T+6开盘出；严格早于09:15的发布时间；时区/缺失/晚到、停牌、涨停买/跌停卖、缺开盘、基准不一致、最低佣金、双边滑点、退出延误及末期持仓。
- 已验证：按截止前已知限价计算整手数量，未来开盘改变不改变提交数量；20%单股/40%多题材/现金同比例缩减；顺序不敏感；已有持仓不重复加仓和延长；同开盘卖款不用于新单；残余预算留现金；未知题材标覆盖不足。
- 信号级独立假设与重叠持仓账户分开保存。未退出/未知成交未从记录分母删除。资料不完整不输出完整信号均值；缺合法估值不补零NAV。不增加止盈止损或动态持有期。
- 规则来源、生效日期与fixture成本见规格来源节。真实规则、企业行动/拆并股/分红、真实佣金及订单证据适配未交付，真实执行入口fail closed为UNAVAILABLE。这不是“真实扣费收益已实现”。
- 日期块bootstrap与Holm helper已做工程测试；真实成对日期/共同setup收益、完整多组统计自动装配仍未接通，报告保持UNAVAILABLE。未输出收益显著性或EVIDENCE_SUPPORTS_CHANGE。

## 实际验证记录

环境：macOS 13.6.7 x86_64；Python 3.12.14。以下命令均在仓库根运行，`run_core_tests.py`内部切到 `stock-fish`。路径前缀 `tests/r05_product/` 在表中以T表示。

|检查|实际命令/节点|退出码与结果|
|---|---|---|
|修改前基线|`stock-fish/.venv/bin/python stock-fish/scripts/run_core_tests.py T/test_stage_aware_momentum_v1.py T/test_stage_aware_momentum_v2_semantic_completion.py T/test_daily_research_snapshot.py -q`|0；64 passed，45.59s|
|首批R1|同入口 `T/test_research_r1.py -q`|0；43 passed，4.58s|
|扩大原路径|同入口：上面3文件，加 `test_daily_research_outcomes.py`、`test_daily_research_service_outcomes.py`、`test_daily_observation.py`、`test_codex_research.py`、`test_research_r1.py`|0；246 passed，77.07s|
|历史/评分/快路径|同入口 `T/test_research_r1.py T/test_r12_score_integrity.py T/test_r16_r1_history.py T/test_r14_daily_fast_path.py -q`|0；134 passed，42.52s|
|最终扩大回归|与扩大原路径相同8文件（新增测试后）|0；252 passed，92.54s|
|最后适配解释及未知账户结果修复|同入口 `T/test_research_r1.py -q`|0；54 passed，9.09s；含完整V2 golden、身份与最后新增两例|
|源码变化发布保护|`T/test_research_r1.py::test_source_change_during_run_refuses_result -q`|0；1 passed，2.56s（随后纳入54项）|
|静态/入口|新模块及CLI的 `compileall -q`，CLI `research-r1 --help`，`git diff --check`|均0|

测试批次有重叠，不能相加成独立测试总数。没有失败或skip；没有改旧断言、减少旧样本或扩大skip。未运行全仓所有测试、Windows、Docker、真实券商/账户或付费供应商；旧瘦身报告中的全套失败并非本轮测试结果。

实施中发现并处理：基线捕获脚本最初误用不存在的canonical_json及fixture函数签名，退出1；修正后在策略改动前独占创建golden。一次中间真实运行遇到源码修改导致manifest/result身份不一致，保留原记录并写INVALIDATED说明，不用于验收；新增source-change guard并从冻结源码重跑。最后发现ACC_ONLY若复制原行会遗留旧解释字段，改为独立一致的研究行并增回归；另外将未知信号日期的账户收益/回撤显式置空，避免INCOMPLETE旁边仍出现现金零收益。没有依据收益调参。

## 未完成项与安全撤回

|项目|状态/精确缺口|
|---|---|
|真实成交评价|BLOCKED：缺日期化完整规则/成本/企业行动/截止前订单证据；真实tape适配不接受原始调用方字典直接冒充已验证市场执行|
|企业行动账户处理|NOT_IMPLEMENTED：仅显式完整无行动fixture路径；不同基准或未知行动保留不可评价|
|正式run ID只读一键入口|NOT_IMPLEMENTED：支持冻结原始导出与显式只读历史库；不自动从生产run反查或猜测输入|
|完整统计报告自动装配|IMPLEMENTED_HELPER_ONLY：连续日期块/Holm helper可用，真实按setup/共同可评价日期的净收益对比、主导性分析、完整统计表未接通|
|真实12组选择/执行比较|BLOCKED_DATA：只对现有单日输入做FULL/CONTROL，不将资料不完整的一个日期冒充市场消融样本|
|低内存导出|IMPLEMENTED_UNVERIFIED_AT_FULL_HISTORY_SCALE：逐组保留完整解释有重复JSON；真实双组结果约276MB，不声称全历史低内存/高性能|
|全面验收|PARTIAL_VERIFIED：上述54个新增节点与原相关回归通过，未声称附件A–H所有组合与所有平台穷尽|

安全撤回只涉及上文列出的本任务文件：先保存当前diff，再逐个审阅删除新增R1文件，移除CLI的research-r1 parser/分派与综述新增段落；不要整仓reset/clean、覆盖用户后来改动或删除生产数据。私有研究结果可单独保留用于审计。默认生产路径从未启用R1，因此不需要部署回滚或服务切换。

本任务结束后没有后台持续积累承诺；未自动提交推送、未部署、未更换主策略、未操作真实账户或生产数据库。

## 最终产物验收摘要

- `r1-accepted-fixture`：run_id `r1_129b2806b289ee409ebd1fa7ac1c2431c6fc8890c146e65f6cc7d5865e510bd2`；manifest `10eaa5429d64786404b2f2da70d859773d47899b7cb119a28bf59466a4065421`。manifest、结果与最终源码身份逐一相同。
- `r1-accepted-real`：run_id `r1_7974299399f2ba1658f781b2c6b82edcedb50b00048c1626ed2a15022464d422`；manifest `960521fc5ccb6130fcfc7a60a41a565db79f1d5de1dde71e2d454bb6c13819a1`。manifest、结果与最终源码身份逐一相同。

最终source_hash：`9377976295c687f5ea1722ce987757bdc76bf86fbdc21b662d9aad9aae308e53`。真实R1为CORE_FAIL 2642、CORE_UNKNOWN 398、CORE_PASS 0；V2为REJECTED 2926、UNKNOWN 114、FOCUS 0。两者均INSUFFICIENT_EVIDENCE。真实执行/收益为不可评价。

最终工作区2个已跟踪文件修改、9个新增文件；HEAD/分支/remote未变。完整配置、代码及测试均保留为未提交改动。

## 后续发布授权（2026-10-01）

用户在本轮交付后明确要求“推送到远端仓库”，本节取代上文截至实施交付时的“不提交、不推送”限制。发布范围仅为本任务11个源码、配置、测试和文档文件；review下的输入、行情、数据库、日志和研究产物不发布。目标为既有origin的main，使用普通快进推送，不强推、不部署、不启用R1默认路径。PARTIAL_VERIFIED与INCONCLUSIVE结论保持，不因发布而升级验收状态。
