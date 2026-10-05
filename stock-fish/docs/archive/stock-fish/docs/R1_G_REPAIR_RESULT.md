> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../../../CURRENT_STATUS.md)；原路径可在[归档索引](../../README.md)查找。

# R1 G01—G05 修复结果（2026-10-02）

工程：G01/G02/G03/G05 为 IMPLEMENTED_AND_TESTED。G04 跨平台实现及模拟为 IMPLEMENTED_AND_TESTED，原生 Windows 为 IMPLEMENTED_ENVIRONMENT_NOT_VERIFIED / WINDOWS_NATIVE_NOT_VERIFIED。真实市场验证为 BLOCKED_DATA，MARKET_EDGE=NOT_ESTABLISHED，production_promotion=DISABLED。合成盈亏不是市场证据。

发布授权补充：用户在上述修复验收完成后明确要求“推送到仓库”。据此仅提交本轮12个源码、测试和文档文件并推送既定远端main；下文未提交、未推送及HEAD记录描述修复验收结束时的状态。发布前重新核对冻结源码身份与203项测试批次一致。私有档案、数据库、日志及完整运行产物不提交；本授权不包含部署或服务切换。

## 工作区和交付

起始、结束 HEAD 均为 `1c8cab393073e14b4a6ac7283c99b4fc71aaa726`，分支 `review`，origin 为 `git@github.com:LiRongji1995/stockfish-qlib-quant-system.git`。起始 dirty 为空，结束为下列12个本任务文件的未提交修改/新增，没有既存用户修改被覆盖。业务目录为 `stock-fish/`，已读取两级 AGENTS、部署/工程基线、日常协议及当前 R1/V2 规格、配置、旧修复结果。指定独立审阅 Markdown/zip 未在附件目录发现；按粘贴任务反例实施。

修改：`execution_research_r1.py`、`research_r1.py`（仅规格身份入口）、`research_r1_adapter.py`、`research_r1_contracts.py`、`research_r1_io.py`、`research_r1_statistics.py`、`research_r1_storage.py`；新增测试 `test_research_r1_g_repairs.py`、fixture `research_r1_multiday.py`；新增 [V3规格](../../../R1_REPAIR_SPEC_V3.md) 和本报告，更新 [项目综述](../../../PROJECT_OVERVIEW.md)。上述 Python 文件位于既有 yaogu / tests 目录，没有平行执行系统。

没有提交、推送、切分支、worktree、部署、重启服务、启用调度、写生产数据库、真实账户操作或真实交易；没有全市场联网采集、付费调用或复制生产库。所有档案、SQLite、脚本、账本、输出、失败日志均在 Git 忽略的 `review/r1-g-fix/`，没有放进源码根目录。旧规格和主报告原字节保留。

## 分项反例与修复

|项目|旧版真实失败|新行为与验收|剩余边界|
|---|---|---|---|
|G01|匹配归档字节/回执的 INVALID、矛盾 OHLC 都能 CLOSED|共享字段校验同时接入适配、成交、前收盘预算、收盘净值；坏入场保留 ENTRY_UNCERTAIN/资本承诺，坏退出保留 EXIT_PENDING/持仓，缺估值不补零；合法档案仍 CLOSED|缺质量或 PARTIAL 仅支持合法的用途字段，不宣称完整日线；真实企业行动转换仍不支持|
|G02|40信号日统计遗漏第40日信号产生的6会话尾部|manifest 先冻结共同窗口；账户按46个会话评价；缺日报、缺价格、未成熟、长曲线裁取、合法现金、正负尾部均测试；缺数据限制总体推断，已知子集仅描述|交易所日历及来源仍须外部真实证据；没有用合成会话冒称真实交易日|
|G03|真实模拟延误25会话仍 ESTIMATED|从相关实际交易计算会话延误，在估计器前限制资格；19可估计，20/25拒绝；pending及未知字段拒绝，保存阈值、原因及trade_id；完整Holm族保留|少于20不保证其他条件通过；未建立新推断方法或延长默认块长|
|G04|真实SQLite第1024条读取遇不可导入resource中断|2048条实际读取通过；Unix/Windows/不可采样分别记录方法、字节、current/peak和状态；超时、超字节、超内存、异常释放均测试|macOS原生；Windows API、Linux单位换算为明确注入模拟，非原生认证|
|G05|PASS节点 reason_codes=[] 生成两个背景缺口|忽略正常空原因/警告/反证，按UNKNOWN、未取得、查询完整性、适用性与必要来源状态区分；完整有来源空记录不等于资金支持；父来源可继承|NO_RECORDED_GAP不保证供应商完整覆盖，也不改变CORE决策|

G01 定向覆盖买入、退出、预算估值、持有期收盘，INVALID、矛盾高低、NaN、Infinity、布尔数值、基准冲突、缺字段和部分记录。正常日线经真正归档、哈希回执、解析、执行和报告保持通过。延误及坏行情报告使用不同输出/身份；资料修订使用新的档案副本，原档案和旧报告保留。

G03另用间隔两自然日的合成交易所会话独立复核：延误19/20/25会话分别为38/40/50自然日，资格仍分别为 ESTIMATED/UNAVAILABLE/UNAVAILABLE。避免只用自然日恰好等于会话数的样本证明正确性。

## 冻结源码的综合验收

环境：macOS 13.6.7 x86_64、项目 `.venv` Python 3.12.14。最终实际命令（仓库根目录）：

```sh
stock-fish/.venv/bin/python -m pytest \
  stock-fish/tests/r05_product/test_research_r1_g_repairs.py \
  stock-fish/tests/r05_product/test_research_r1.py \
  stock-fish/tests/r05_product/test_research_r1_repairs.py \
  stock-fish/tests/r05_product/test_stage_aware_momentum_v1.py \
  stock-fish/tests/r05_product/test_stage_aware_momentum_v2_semantic_completion.py \
  -q --basetemp=review/r1-g-fix/pytest-frozen
```

最终 **203 passed，0 failed，0 skipped，退出0，301.80秒**。这包含58项本轮回归、101项原R1回归和44项V2相关回归；重叠中间批次不累加。`git diff --check` 退出0。独立逐日账本与非等距会话脚本各退出0。最终源码身份再次核对与冻结文件一致。

基线：原R1两组101 passed、退出0，没有本轮观察到的既有失败。新增五项反例（G01两条）在未改源码时实际6 failed、退出1。首次资源fixture缺历史表是测试准备错误，纠正后仍于1024行复现真正 ModuleNotFoundError，两个日志均保留。中间发现并修复校验元数据触发DailyBar额外字段拒绝的问题；一次超时测试在构造期间触发而非断言区间，已修正注入时点。两批在代码仍调整时运行的多日CLI正确拒绝 SOURCE_CHANGED，均不充当最终通过证据。

未运行全仓全部测试、80万行重基准、原生Windows/Linux、Docker、生产在线数据、真实独立留出或市场盈利验证。没有放宽断言、扩大skip或删除失败样本。当前没有已知待实现的G01—G05接线缺口；原生平台是环境未验证，真实市场证据是缺真实数据，不混称工程 INCONCLUSIVE。

## 非零多日 CLI、身份和逐笔核对

实际CLI由上述项目内集成测试通过子进程调用，无最终决策/曲线/统计状态mock。完整参数数组、stdout、stderr及退出码在 [command.json](../../../../../review/r1-g-fix/pytest-frozen/test_nonzero_archived_multiday0/command.json)。命令为项目Python调用 `scripts/codex_research.py research-r1 --group R1_FULL --group V2_CONTROL --output <pytest-frozen/test_nonzero_archived_multiday0/out>`，并依次传入40个 `--input <TEST_FIXTURE/input_00.json>` 至 `input_39.json`；**退出0**。saved summarize 也实际子进程退出0，保存统计与原结果逐字段相等。输出拒绝覆盖，重跑必须新目录。

数据标识明确为TEST_FIXTURE。实际链路为归档文件与SourceReceipt → 行情/规则/行动覆盖解析 → 冻结MarketInput与实际V2/R1决策 → 预算/整手 → 非零成交、重叠持仓及退出 → 共同窗口 → 日期块统计 → result/completion/Markdown → saved summarize。非入选日由真实条件得到合法空名单；另有必要资料不完整及被拒绝执行/推断的测试，不删除失败样本。

|身份|值|
|---|---|
|run_id|`r1_229337d74d3ebd1f0740fc5556aa255a2eed90db8aae4e5d19e3c857777ba254`|
|manifest_hash|`d0dfce87fc013b91a39d70a43ac7b4c38207cd1974408449cb5714e9de8a7c1b`|
|source_hash|`3405e4eee8f13e7b842e6abc9e2cd35fbf5f196fb22746e18b5ae9cdb8cf2fa1`|
|spec_hash|`eee3ae1394045c206c9657f5c1f6cea0b1a846a4dace981dedc8164f6df1d6a0`|
|首输入hash|`3b27379de48b703f020ff6f0c049e7da825a63c56835afc1ca3fdc0c5d79d2da`|
|末输入hash|`8038834ba13d1bb67c6b4ace0fb69f4eb2b42026bc2f153bd708dcab02096c0f`|
|归档执行描述hash|`e497df33952c94a6ed4dbd5191ead013c0e6f57d9978b1b9c28ae4b50ca5fc9f`|
|result_hash|`8f674ae75bb819efdd64925f353e2ad80bf9754446e37e52ce5106b5e2ddedbe`|

全部40个输入hash在原manifest内。上述日期为合成交易所会话，含实际当前日期之后的TEST_FIXTURE，不是当时已取得的真实市场信息。

信号窗 `2026-09-11—2026-10-20`（40会话）；共同账户窗 `2026-09-11—2026-10-26`（46会话），计划结束日10月26日，证据截至 `2026-10-26T16:00:00+08:00`，尾部6会话。双方expected/observed均46，missing_sessions和未了结交易均空。V2该样本经实际规则得到合法现金，R1得到5笔交易、最多2只重叠持仓。

|信号日|证券|股数|买价|卖价|买费|卖费|净损益CNY|
|---|---|---:|---:|---:|---:|---:|---:|
|09-11|TEST000|18100|10.01|10.48|56.16611|153.64728|8297.18661|
|09-12|TEST001|18100|10.01|9.49|56.16611|139.13289|-9607.29900|
|09-21|TEST000|18100|10.01|10.48|56.16611|153.64728|8297.18661|
|09-22|TEST001|18100|10.01|9.49|56.16611|139.13289|-9607.29900|
|10-20|TEST000|18100|10.01|10.48|56.16611|153.64728|8297.18661|

独立账本不调用生产fees/NAV函数：按11元预声明上限及100股整手、双边最低佣金/过户与卖方税独立计算，逐日比对全部46个现金、持仓数、持仓净值及账户净值。初值1000000；末值 `1005676.9618300`；日收益复合 `0.005676961830000000000000000`，与期末/初值−1一致。逐日证据在 [daily-ledger.json](../../../../../review/r1-g-fix/daily-ledger.json)，脚本在同目录。

主账户统计 n=46、20会话块、2000次、默认种子；均值差 `0.00012724966993428756`，CI95 `[-0.00027516208910227414, 0.0005465075440596046]`，p约0.58421，96项族Holm后为1。日均差不是累计收益，未强令二者相等。不要求显著，也不把合成正收益解释为R1市场胜出。

## 规则保护、身份和资源边界

原R1 JSON、V2 YAML、V2实现、validation_identity及旧V2修复规格/报告逐字节与HEAD相同，哈希见 `review/r1-g-fix/protected-hashes.json`。V2 golden 和全部十二组固定候选、setup、理由回归通过。仅research_r1的规格指针升级，选择函数未改。

默认仍为T+1/T+6、严格早于上海09:15、100万元、单股20%/已知题材40%、无杠杆、每边0/10/30bps、主10bps；20会话块、2000次、种子20261001及Holm族不变。允许变化为行情用途有效性、窗口/尾部与推断资格、资源诊断、背景覆盖、V3评价/schema/源码身份。新增helper处于原research_r1_*源码身份覆盖内；旧保存结果只校验读取，不以新窗口重算。

资源不可采样明确 MEMORY_MEASUREMENT_UNAVAILABLE；仍保留批量、2GiB消费字节、300秒时间预算，不能保证硬内存上限。可采样时仍按1GiB峰值检查。Unix单位换算、Windows API注入替身均测试；本机原生macOS，不宣称Windows实机通过。小规模真实SQLite2048行验证足以覆盖本轮1024边界；没有因此重跑80万行基准或编造性能倍速。

## 安全撤回

本次专属补丁保存在 `review/r1-g-fix/task.patch`，含上述12文件，不含运行数据。先保存后续修改，执行 `git apply --reverse --check review/r1-g-fix/task.patch`；只有检查通过且期间工作区未再变更，才可执行同路径 `git apply --reverse`。检查失败时逐文件审阅冲突，不使用强制、reset、clean或整仓回退。不要删除历史证据、旧run或用户数据。默认策略从未切换，无需服务回滚。
