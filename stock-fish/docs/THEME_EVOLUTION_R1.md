# 题材演进研究 R1

`THEME_EVOLUTION_R1` / `THEME_EVOLUTION_DATED_COHORT_REFERENCE_CHAIN_V2` 是正式保存运行可追加的研究情境层，不改变 R1、V2、DAILY 或正式名单。配置与参数是尚未验证的初始假设，`MARKET_EDGE=NOT_ESTABLISHED`，不证明预测优势。模块不联网、不读历史 DB、不另建库，不激活服务或策略；由现有运行体系保存输出，原输入与旧运行不覆写。

## 来源接口与角色

入口是 `run_theme_evolution(bundle, cutoff=..., market_pack=None, _market_pack_validator=None, membership_mode='DATED_ONLY', output_history=False, history_scope='LATEST_67')`。`bundle` 是原 `MarketInput`，其 hash/history ref 不变。可选 `THEME_MARKET_PACK_V1` 是显式附加来源层：绑定 `source_input_hash`、`observation_date`、`decision_cutoff`、`coverage_type`；包含原 `SourceReceipt`、逐会话面板及可选 `ThemeSnapshot`。面板要求 `session`、`scope=SSE_SZSE_A_COMMON_ALL_BOARDS`、完整 `expected_instrument_ids`、完整 `DailyBar`、`source_refs`、四时钟 `clocks`、`status` 与 `pagination_complete`。

`output_history=True` 仅展示同次计算的全部会话，不改变每日特征、阶段或所需窗口，不扩大读取范围。默认 `history_scope='LATEST_67'` 仍计算最近 67 个会话、只输出最后 2 会话；只有显式 `ALL_OBSERVED_SESSIONS_RESEARCH` 才使用已提供且不晚于观察日的完整日历，用于年度事后多轮分析。不接受未来观察日，不读取新数据或选取回报最优窗口。输出 `requested_output_scope`、`requested_history_scope` 均进入 context hash，完整范围强制 `RETROSPECTIVE_CONTEXT_ANALYSIS` 角色，不能算历史 PIT 验证或市场优势。不足 20/60 日的窗口仍 UNKNOWN。

非空 pack 必须由原生调用者提供 callable `_market_pack_validator(pack, bundle=bundle, cutoff=cutoff)` 能力，认证原 payload byte SHA、逐日 A 股证券身份范围及字段映射；拒绝时抛错。调用者提供的 JSON `verified` 布尔值不能授予权限。模块还重新核验输入绑定、parent receipt 冲突/完整性、真实可见时钟、交易日、逐行身份、完整范围与同 clock 冲突，并重算金额，不信面板的金额 scalar。`nontrading_instrument_ids` 仅在原生 validator 认证 RAW 停牌事实且 `nontrading_policy=NONTRADING_EXCLUDED` 时允许排除；缺值不填零。没有 pack 时全沪深 A 股金额 UNKNOWN，不能把原主板池充当市场分母。

返回 `market_pack_hash`、`source_refs`、`effective_input_hash` 和 `context_content_hash`。外层必须将 context/pack hash 纳入同一保存运行的 effective hash 与 run ID；源和规则不同的附加情境不得覆盖同 ID 的旧运行。不是新数据库或平行名单。

默认 `DATED_ONLY` 每天只采用当天有效、截止前可见、完整且无冲突的成员快照；允许此前已知且当日仍有效的 cohort。当前采集成员不能倒填此前 20/60 个会话。默认处理最后 67 会话、输出最近 2 会话，缺失不缩短窗口。价格需 VALID、完整 parents、同日身份及相同可比价格 basis；采用原 inline 或认证 pack 的价格，不自动补史。

显式 `CURRENT_COHORT_BACKVIEW` 仅选择决策 cutoff 前已取得、`effective_from` 不晚于决策日的最新快照，固定这组成员回看价格。快照原生有效日与四时钟保持；例如 10 月 10 日成员可以作为 10 月 12 日预测时已知信息，但不能称为 10 月 9 日当时成员。输出明确 `CURRENT_COHORT_BACKVIEW_ASOF_DECISION`、`cohort_captured_at`、`cohort_effective_from`、`historical_membership_verified=false`。这类输出是当前 cohort 的回看情境，不是历史真实题材演进。所有模式均 `official_pit_validation_countable=false`；事后规则不能伪装成旧日实际信号。

当前 cohort 原 `member_ids` 与数量保持。只有成员全为沪深普通 A 股标识且同日市场/证券 scope 已认证 COMPLETE 时，历史 eligible 是 `cohort∩dated expected_ids`，再排除已验证停牌；输出 `CURRENT_COHORT_WITH_DATED_ELIGIBILITY`、`dated_eligibility_verified`、`eligible_member_ids` 及 `absent_from_dated_market_ids/count`。当前新上市成员早日未在完整证券 scope 内，可以显式缺席，但不能猜具体上市原因、填零价格或金额。全 cohort 当日均缺席时相应主题事实 UNKNOWN。BSE、B 股或未知身份不静默剔除；DATED_ONLY 的成员 outside scope 仍 UNKNOWN；不完整市场不能授予这种排除能力。

## 特征与状态

SPEC_5 修复真实 6 日全市场、142 主题试运行暴露的 Python 默认 4300 位整数转 JSON 限制。`exact_numeric_encoding=FRACTION_INTS_OR_DECIMAL_STRINGS_V2` 保留小 Fraction 的 `{numerator:int,denominator:int}` 格式；超过 12000 bit 的整数以规范十进制字符串编码。编码 divmod 与解析都按最多 1000 位块处理，不对大整数直接调用 str/int，不调整进程全局限制，不舍入原精确数值或改变经济门槛。相同来源与 cutoff 的同次运行按股票/日期/前会话/收益 basis 缓存日收益，首次仍完整核验 parents/PIT 并记录来源；缓存不跨 run，也不缓存后来的来源修订。旧失败诊断和旧已保存运行保持原版本。

同次运行还按完整会话窗口、逐日 basis 和 3/5 会话长度复用相同的全市场等权收益链；第一次依然调用原精确复利函数，包含窗口不足和混合口径的 UNKNOWN。主题自身的日收益链仍各自计算。缓存关闭对照要求全部 items/context/effective dependency hashes 完全相同，不通过缩市场范围、压缩缺失日期或降低数值精度提速。

金额单位是 CNY。逐日去重的主题交易成员金额为 C，全沪深四板 A 股同日 RAW 金额为 M，份额 `share=C/M`。同一股票可属于多个概念，因此各概念份额之和不要求为 1。`E=share/此前20个完整会话share中位数`，历史零中位数或缺失为 UNKNOWN。上涨广度 B 是同日 active dated 成员正日收益比例；不把未知收益计作未上涨。

成员收益每天等权，按每天已证明的成员 cohort 再平衡；连续日收益复利形成 3/5 日主题收益，再减相同市场 scope 的等权日收益链得 RS3/RS5。默认是 `DAILY_REBALANCED_DATED_COHORT_EQUAL_WEIGHT`；显式当前成员回看则标 `FIXED_CURRENT_COHORT_EQUAL_WEIGHT_BACKVIEW`。全部所需日/成员收益必须合法，否则相应收益窗口 UNKNOWN。市场基准与成员采用同 scope，不混用主板与全部 A 股分母。N 调整金额集中度为 `(N*sum((C_i/C)^2)-1)/(N-1)`；N≤1 或 C=0 时不造集中度。

认证 pack 面板还可显式声明 `return_basis=BAOSTOCK_REFERENCE_RETURN_CHAIN_V1`。只有这一声明且 RAW 字段经原生 byte validator 认证，才以当日 `close/reference_prev_close-1` 计算日收益；RAW OHLC/reference 必须为正、价域合法、来源完整且实际可见。BaoStock [官方接口定义](https://github.com/zxygithub/baostock/blob/master/docs/pythonAPI.md) 的 `pctChg=100*(close-preclose)/preclose` 中，`preclose` 是当日交易所除权除息参考价，不能解释为简单昨日 RAW 收盘价。输出 `SOURCE_DAILY_REFERENCE_CHAIN_EQUAL_WEIGHT`，不是投资组合总回报或连续复权价；不填充 `comparable_close`，也不更改原 `DailyBar`。缺声明仍按原可比价格门禁；未知声明或连续 3/5 日混用两种口径时收益窗口 UNKNOWN，不能静默降级。此收益角色与成员历史/当前回看角色分别标明。

- 发酵候选：E≥1.25、RS3>0、B≥55%。
- 走强候选：E≥1.5、RS5≥2个百分点、B≥60%。
- 走弱候选：RS3<0、B≤40%。

候选必须连续两个交易所会话成立才确认 `EMERGING`、`STRENGTHENING` 或 `WEAKENING`；单日为相应 `_PENDING`，不能跨缺失日期确认。已强后，广度或 RS5 较前日削弱标 `DIVERGENCE`。已知但混合、不匹配的事实标 `MIXED_PENDING`；所需事实不足保留 UNKNOWN。三值条件保留 known FAIL 和未知子项，不把缺证当 FALSE。状态变化不授予正式 FOCUS、CORE_PASS 或可成交性。

高位反证是独立轴：当前份额≥此前60会话份额 p90，B 较此前5会话峰值下降≥15个百分点，且主题日收益≤0 或 RS3 较前日下降。p90 使用 `p*(N-1)` 的线性插值，历史窗口都不含当前日。保留各子条件与 UNKNOWN；独立 risk 不覆盖阶段，放量本身不能证明出货或资金主体行为。

## 竞争、协同的解释边界

`describe_theme_association` 只描述已来源校验的逐日成员收益：先去掉两个主题共同成员，各自等权收益减同日市场收益，再计算描述性相关；同时报告有效天数和去掉的重叠成员数量。固定单位市场暴露调整不等于估计 beta，少于3天或零方差为 UNKNOWN，没有置信度或因果结论。不以份额闭合导致的负相关推断竞争，不将正相关称为资金协同或轮动因果。成员历史、价格、市场或实际时间缺失时不能推出全年周期结论；年度分析是后续有界研究，需保留原来源和后验角色。

工程单测使用明确 TEST_FIXTURE，不携带真实行情、不访问 DB，覆盖精确阈值、日期/PIT、部分来源、角色隔离、缺失窗口、独立 risk 及重叠剔除。通过工程检查不等于真实数据就绪或投资验证。
