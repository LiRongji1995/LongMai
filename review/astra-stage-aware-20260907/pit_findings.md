# Astra 独立审阅：历史时点、资金证据、可复现性

审阅日期：2026-09-07。正式源码位置：`D:/stockfish-qlib-quant-system/stock-fish/`；父审阅固定 HEAD `a9c6deec75319ce0fb2d6fabca0a4c6021323c05`。本子任务未修改正式源码、配置、生产数据库，未调用外部采集或付费数据，未读取凭据，未执行 Git 操作。仅读取代码和现有测试；审阅脚本只使用显式 `TEST_FIXTURE`，其中日期、价格及证券均为合成，不能当市场案例。

轻量复现：`pit_probes.py`；结构化结果：`pit_probe_results.json`。SQLite 和 pytest 临时目录、日志是隔离工程产物，不应提交。证据等级对应主任务枚举：E1=`STATIC_REVIEW`（当前代码静态可核）；E2=`SYNTHETIC_COUNTEREXAMPLE`及`ACTUAL_EXECUTION`（本次执行合成反例）；E3=`ACTUAL_EXECUTION`（父审阅已读取真实保存产物，此处仅关联其代码成因）。这里没有独立重新取得外部真实行情。

## 优先级结论

没有确认发生生产数据覆写、真实资金污染或真实前瞻收益误报的无条件 P0。资金父证据/披露窗口和信号时点隔离应作为**启用正式资金名单及宣称前瞻评价前的阻断项**（P1，若总报告用 P0 表示上线阻断可列 P0）。当前真实样本资金为空，不能把合成污染说成两只案例实际已经遭遇。

## PIT-01：阶段感知资金绕过已有父来源与历史可见性校验（P1，E1+E2）

代码：`stockfish_core/yaogu/stage_aware_momentum.py:632`、`:645` 调用 `_visible_fund`；代理函数见同文件 `:132`，实际实现为 `theme_capital_trend.py:417`，仅执行 `clock_visible`，重建模式直接允许 late capture。没有调用 `EvidenceResolver`；与价格/主题在 `strategy_experiments.py:137` 和 SAM `:197` 使用的验证链不同。`contracts.py:231` 的 `funds` 是自由字典，不能由 schema 补上该验证。

合成结果：

- 完全无 `source_refs` 和资金 receipt，正式状态仍 `PASS`，资金 `STRONG`；同一行交给现有 `EvidenceResolver` 返回 `SOURCE_REFERENCE_REQUIRED`。
- 增加指向“未来发布、INVALID、证券范围为 OTHER”的父 receipt，仍 `PASS/STRONG`。
- 在 `RECONSTRUCTED_HISTORICAL` 中把 first_seen/ingested 推迟两天且无历史证明，仍 `PASS/STRONG`。
- 把 window_end 设为观察日之后三天，仍 `PASS/STRONG`。`theme_capital_trend.py:449` 仅验证 start<=end，SAM `:638-648` 仅限制 session 处于近五日，没有验证披露起止日都在观察日内、窗口对应交易所日历/单日或三日口径及发布日期。
- 原配置明确的代理层 `MAIN_NET_FLOW_PROXY` 加上 `classified_active=True` 就成为 `STRONG`：SAM `:616-624` 的 OR 条件可覆盖代理层判断，未要求版本化、日期化席位映射。
- 删除所有资金前后正式结果不同，但顶层 `effective_input_hash` 相同；funds 未登记入 resolver 的 effective dependencies，完整原始 `input_hash` 正确不同，因此不是所有溯源完全丢失。

最低建议：将资金统一接入已有 resolver，要求 receipt 完整性、来源定义/单位、证券和业务日期范围、真实父来源时钟及历史证明；验证披露窗口。分类席位须单独引用有版本与有效期的映射，代理层不得靠布尔字段晋升；把实际采用资金及映射加入 effective dependencies。供应不足维持 `UNKNOWN`，不通过放松门槛解决。必须为未来父来源、未证明晚到、未来窗口及 proxy 自称身份增设反例回归。

供给边界：当前 normalizer 的龙虎榜公共摘要 `provider_funds.py:22` 用白名单投影，剔除“上榜后N日”和解读，这是正确行为；`provider_normalization.py:391-445` 把公共摘要/Tushare 记录封装为未验证嵌套证据，并未产出 SAM 要求的顶层 theme_id/classified/buy_amount_cny 链接记录。请求成功或有龙虎榜摘要不会自动让 SAM 资金可用。这是未完成供给，不应记为 `WEAK`，也不意味着上述自由输入漏洞已在实际摘要路径被触发。

## PIT-02：重复资金按输入顺序保留首条，冲突可改变正式入选（P1，E1+E2）

代码：SAM `:653-670`，同类逻辑也在 TCT `:486-501`。去重 key 包括证券、主题、席位（或来源兜底）、session、窗口和 layer，也接受任意 `dedupe_key`。相同 key 第二条被无条件丢弃，没有相同内容断言或可见修订排序；跨源席位别名无日期化映射。

合成结果：同证券、同席位、同日、同 key 的两条金额冲突数据，输入 `[strong, weak]` 得 `MODERATE/PASS`，改成 `[weak, strong]` 得 `WEAK/FAIL`。这是相同证据集合的顺序敏感性，不是证明真实重复上榜已被累加。已有 `_overlap_risk(:673)` 能阻断同 holder 不同 key 的重叠窗口，但该校验在相同 key 的冲突已经被丢弃以后运行，不能救这个分支。

最低建议：同 key 同内容可去重；不同内容需按来源明确修订规则选择或 `UNKNOWN/CONFLICT`。跨上榜原因、单日/三日窗口、跨供应商及席位别名须统一事件/身份键后验证，保存保留/拒绝的依据，不采用第一条获胜。

## PIT-03：主题条件与资金条件可以从不同主题拼接（P1，E1+E2）

代码：SAM `:599-613` 主题 any-pass，`:782-801` 独立资金 any-pass，`:997-1012` 把二者合成正式结论；资金循环不要求对应 `sector.status == PASS`。主题排序固定 theme_id、peer 剔除本股（`:523-528`、`:640-643`）已实现，不能笼统说代码仍按最好主题排序。

合成结果：主题 A 的 peer 足够，sector=PASS，但没有资金；主题 B 仅一只其他成员，sector=FAIL，但该成员有 6000 万买入/4000 万净买入，funds=MODERATE。没有一个主题同时满足主题与资金条件，个股仍正式 PASS；结果明示 sector.primary_theme_id=THEME_A，funds.primary_theme_id=THEME_B。

最低建议：对每个主题形成成套资格/板块/资金结论；预先确定单一主主题或允许多个主题但要求至少同一主题全链通过。保存全部主题，避免“任一主题各取一个条件”。若产品真正允许跨主题拼接，应把这项语义明示、配置化并独立评价，不能借“不挑最佳主题”的说明隐藏它。

## PIT-04：不可见主题被判为确定性落选（P2，E1+E2）

代码：SAM `:605-608` 在没有有效 candidates 时仅凭 `ctx.bundle.themes` 非空返回 FAIL。`_visible_stage_themes(:197)` 正确拒绝未来发布、非有效期和冲突修订，但拒绝原因没有参与这里的 UNKNOWN/FAIL 分型。

合成结果：把唯一题材的发布时间移到 cutoff 后，主题不可见；结果 `sector_linkage=FAIL / SAM_NO_AUTHORIZED_SECTOR_LINKAGE`，资金 UNKNOWN，正式 FAIL。它只证明“此刻没有可核验链接”，不证明“完备来源已证实本股没有链接”。

最低建议：来源缺失/不可见/不完整为 UNKNOWN；只有完整、可见且涵盖所需题材目录的证据明确排除本股才 FAIL。保留各拒绝原因；五日落选反思必须把缺资料与规则否定分开，否则漏选归因失真。

## PIT-05：阶段配置/实现未进入聚合 run_id 与复用一致性校验（P1，E1+E2）

代码：`strategy_experiments.py:531-539` 的顶层 source_identity 路径没有 SAM/TCT 实现和其配置；`:569` 的 run_id 只包含顶层身份。SAM 自身在 `stage_aware_momentum.py:140-152` 保存专属 config/source hash，但未传递到 run 身份。`scripts/codex_research.py:284-301` 校验的是聚合实现一致性和格式，并未专门校验所选 stage profile 身份。

合成结果：**仅在 Python 内存**提高 SAM 两个资金等级的金额要求，正式入选数由 7 变 0，stage config hash 改变，顶层 source_hash 和 run_id 完全相同。将两份写入审阅目录独立 SQLite，第二份被 `ImmutableConflict` 正确拒绝，旧数据内容保持不变；同内容重复保存成功。这证明的是身份遗漏及版本碰撞，不是生产覆写已发生。脚本不改正式 YAML。

最低建议：聚合 run_id/复用校验纳入每个研究臂的配置、策略实现及实际依赖身份；后验参数分组也使用对应策略身份。保留现有 immutable storage 约束，不靠允许覆写解决碰撞。

## PIT-06：历史观察/实际生成/前瞻评价标签没有完成隔离（P1，E1+E2；真实标签关联E3）

代码：`provider_normalization.py:447` 固定 `coverage_type=PROSPECTIVE_CAPTURED`；`scripts/r05_pv_observation_capture.py:123` 以当前时钟评估用户指定观察日；`candidate_service.py:482-495` 提交同样以当前时刻为 cutoff；`strategy_experiments.py:554` 仅由 coverage_type 映射 `ARCHIVED_POINT_IN_TIME`。这套逻辑能表达“这些数据在当前 cutoff 已取得”，**不能证明该信号在旧观察日晚间已经保存或当时已经可用**。

父审阅真实产物事实：观察日 2026-09-04，cutoff=2026-09-07T09:17:11.036672+08，run_id=strategy_experiment_89b7d814846787ff13843abd，audit_mode=ARCHIVED_POINT_IN_TIME、coverage_type=PROSPECTIVE_CAPTURED。此处未另读真实原始包。必须在总报告称为 9/7 才保存的 9/4 数据研究，不能称为 9/4 当时的前瞻荐股；“相对9/7 cutoff可见”和“9/4已归档”是两个问题。

后验代码：`strategy_tracking.py:145-161` 从 observation_date 起算未来 sessions，未以原 decision_cutoff 判别前瞻/事后窗口；`:216-239` 记录的 coverage_type 来自**后验输入**而非原信号；`:458-515` 按 strategy_id/horizon 池化，没有原信号 coverage/audit_mode/cutoff 的分层。logical_label_id **包含 run_id**，不同 run 不会互相覆盖去重，不能报告相反结论。

合成结果：同证券同观察日一份早期信号，一份标 RECONSTRUCTED 且 decision_cutoff 晚于 evaluated_at 十天的信号，后者仍产生 MATURE(+2.67857%) 标签；两份放进反馈，signals=2、unique_instruments=1、signal_dates=1、mature_valid=2。不同 run 在描述性 pooled_signals 内保持两个样本，这与代码字面一致；问题是缺少独立前瞻池/重复计算策略，不能把这个合并池当独立前瞻验证。

最低建议：分开 input capture coverage、signal created_at/decision_cutoff、intended execution/target date、historical replay cutoff、outcome evaluated_at。标签同时保存原信号与后验输入覆盖。只允许结果生成之后的有效执行窗口进入前瞻绩效；旧日期晚跑可保留事后价格诊断。反馈明确分层，并规定同日期重复 run 的择一/并列敏感性统计，不静默重复加权。

## 已验证防线与未验证范围

- 历史价格、证券资格和主题外层 resolver 已有父来源、四时钟、有效期、证券/日期范围、冲突修订校验；现有未来污染、当前身份不倒灌测试本次通过。
- BaoStock comparable_rows 使用后复权历史有效因子；现有未来公司行为不改旧价、历史因子冲突、缺左锚和复权价交叉验证测试通过；HistoricalReplayAssembler 截断未来价格/公司行为、保留真实捕获时间、校验原始hash和日期化板块来源测试通过。本次未发现这些已测路径复现“未来复权污染”，不沿用旧印象列bug。外部公司行为公告完整性、真实历史因子修订频率未独立联网验证。
- 当前主题有效期与本股剔除已实现；未取得真正多日 PIT 题材成员与官方龙虎榜席位映射的真实样本，故未知真实源是否足够，而非声称真实链已通过。
- provider/request/queue 定向测试覆盖分页完整性、预算/重试、定义版本隔离、缓存hash、防静默截断、部分结果保留，本次通过；未进行付费端点登录或网络采集，也未把 provider health 当数据就绪。
- SQLite documents update/delete 有不可变触发器，应用层同ID异内容拒绝；本次碰撞反例确认此防线。调度器把后验/跟踪写入不同命名空间，现有重试backoff、超100条排队、旧run仍保留、缺失不补零等测试通过；没有发现调度直接写回正式选股名单。
- `config/stage_aware_momentum_v1.yaml:143` 的 AI 职责是声明；本次追踪的策略/跟踪逻辑为确定性计算，没有发现 LLM 自动更改排序、阈值或旧 run。白名单投影可以阻挡供应商“上榜后N日”字段，但不能由此宣称存在完整前瞻 AI 审计。未见这个新增研究臂独立保存 typed AI 判断的生成时点、所见 source/input hash、model/prompt版本和事后/前瞻标签的实现；当前文档中的事后解释应如实标识，不能回填为历史当时判断。

## 本次命令与结果

工作目录均为 `D:/stockfish-qlib-quant-system/stock-fish`。

1. 首轮选择 visibility、consumer visibility、historical replay、TCT、SAM 五文件，执行 `./.venv/Scripts/python.exe -X utf8 -m pytest ... -q --basetemp ../review/astra-stage-aware-20260907/pit_pytest_tmp -p no:cacheprovider`，退出码 1：62 passed，15 errors。错误全部是子审阅目录尚未创建导致 pytest 临时目录 mkdir 的 FileNotFoundError，属于本次 harness 失误，不能算产品测试失败。
2. 创建审阅目录后完整执行：

```powershell
& ./.venv/Scripts/python.exe -X utf8 -m pytest tests/r03_evidence/test_visibility_and_funds.py tests/r03_evidence/test_consumer_visibility.py tests/r05_product/test_historical_replay_adapter.py tests/r05_product/test_theme_capital_trend_v1.py tests/r05_product/test_stage_aware_momentum_v1.py tests/r03_sources/test_provider_boundaries.py tests/r03_pipeline/test_request_queue.py tests/r03_pipeline/test_queue_review.py tests/r05_product/test_rectification_tracking_service.py -q --basetemp ../review/astra-stage-aware-20260907/pit_pytest_tmp2 -p no:cacheprovider
```

退出码 0，**162 passed in 11.81s**，输出保存在本机 `pit_tests.log`。这些是合成工程正确性测试，不能证明资金管道完整或市场收益有效。

3. `& ./.venv/Scripts/python.exe -X utf8 ../review/astra-stage-aware-20260907/pit_probes.py`。最初脚本误用类名 `Store` 导致 ImportError，修正为真实 `SQLiteStore` 后两次退出码 0；第二次加入成熟后验混样探针，结果见 `pit_probe_results.json`。内存配置在 finally 恢复，正式代码未改。
