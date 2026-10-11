# 原生市场解释附件 V1

`MARKET_EXPLANATION_V1` / `NATIVE_MARKET_LEVEL_WINDOW_CHANGE_AND_COUNTEREVIDENCE_V1`，规格 `MARKET_EXPLANATION_SPEC_1`。显式独立附件，初始描述口径尚未验证。由 `market_explanation_artifacts.run_market_explanation` / `codex_research market-explanation` 显式协调原件重新发行真实native能力；`report --market-explanation` 与 `daily-report --market-explanation` 只消费同一run/input/观察日已保存附件。原snapshot及正式JSON/CSV不改，不改变选择、score或FOCUS。默认service/collector仍未自动生成附件，不能称默认接线全面完成；DB、R1/R2和V2预测配置未改变。

```python
attachment = build_market_explanation(bundle, issued_market_cap, cutoff=context_cutoff, run=frozen_run)
text = render_market_explanation(attachment, as_of=report_cutoff)
```

## 来源和原run绑定

build只接受真正发行的 `MarketDescriptiveContext`。前后调用原native验证，重新核验消费原件、registry及保存产物；dict、duck type、保存Reader JSON、自hash与PASS文字不能获得能力。新身份绑定本模块、本文规格及 `native.source_identity()` 的hash；不改其旧依赖或源码身份。

run必须由调用方提供已取得的真实保存原结果。核对input_hash、观察日、run_id、原资料截止不晚于附件截止，以及冻结items的证券唯一性。保存原run完整内容hash、每策略items内容hash/原决策计数/原research_selected_ids和selection_results内容hash；build前后原run全文不变。**这只是内容绑定，不认证selector本身或原保存文件身份**：`FROZEN_RUN_CONTENT_BOUND_NOT_SELECTOR_REAUTHENTICATED`。不能用此接口认证调用方虚构run；上游真实原run出处与文件SHA仍由调用方按既有规则验真。

原run绑定在 `frozen_run_binding`：run_id、input_hash、observation_date、decision_cutoff/original_decision_cutoff、run_content_hash（完整canonical内容hash）、declared_run_content_hash（原run可选content_hash）、strategy_items、selection_results_content_hash及binding_role。原run截止与context_cutoff分列，晚捕获事实不回填原盘前。保存native context hash、来源身份、产物索引、观察日panel/ref/content hash和全部已消费receipt的原四时钟及各最大值。历史资格UNKNOWN、PIT0、情绪周期认证0，MARKET_EDGE=NOT_ESTABLISHED。四板当日交易总体不等于历史规则合格池。

## 精确窗口和未知

水平复制原生观察日统计。过去1/3/5交易日窗为完整已观察日历上的T−h…T，共h+1日期。任何中间日期或轴不可用，该轴变化及斜率UNKNOWN；不压缩日期，不从inline价格、其他run或后验补点。每轴沿用原原因；换手另要求每日期全部交易行覆盖完整。

- 成交额变化：M(T)/M(T−h)−1，起点金额必须正。分母非正时变化UNKNOWN，但完整金额水平仍可给斜率。
- 广度、Top10集中度、日收益中位数、换手百分数中位数及IQR：当前减起点。百分数换手变化是百分点；fraction轴仍为fraction。
- 各轴水平斜率：i=0…h，β=Σ(i−均值i)(y_i−均值y)/Σ(i−均值i)²，用精确Fraction。金额斜率单位为元/交易日；其余为原水平单位/交易日。不是累计持有收益、指数表现、因果或预测分数。
- 逐窗保留交易总体hash；全部已知时判断期间是否改变，否则总体比较UNKNOWN。未假设固定成员。

描述反证只有三种明确组合：金额变化>0且广度变化<0；集中度变化>0且广度变化<0；日收益中位数变化>0且金额变化<0。第三项编码 `MEDIAN_DAILY_RETURN_IMPROVING_AMOUNT_DOWN`，中文为“当日收益中位数改善但较窗口起点缩量”；它不是价格上涨或持有收益。已知不满足记NOT_PRESENT，任一轴未知记UNKNOWN。零变化为已知事实；UNKNOWN不当False。不推断净流入、游资、题材竞争、触顶或情绪周期，不调整阶段FSM。

## 保存事实显示

render接受保存附件dict，校验内容hash和四时钟最大值一致性，但**不恢复原生能力或重新认证原件**。任何消费原件四时钟最大值、声明context_cutoff、实际generated_at/first_created_at晚于as_of时输出NOT_YET_VISIBLE并隐藏数值；保存attachment_visible_at为这些时刻的最大值。无消费原件时明确UNKNOWN，不伪造可见时钟。实际资料晚取得仍保持晚时钟；不能把保存JSON用于selector、源恢复或PIT权限。

用户显示采用六轴中文表：当前水平、1日变化、3日变化、5日变化；各变化格另列该完整窗口的水平OLS斜率。金额水平用亿元、金额相对变化用%、金额斜率用亿元/交易日。上涨广度、Top10成交集中度、当日收益中位数和收益分布IQR的fraction水平乘100显示%，端点差及斜率乘100显示pp和pp/交易日。provider换手字段已是百分数，水平直接显示%，变化及斜率直接显示pp和pp/交易日，**不再次乘100**。pp表示百分点，日收益中位数改善不表示累计价格上涨。

显示层以精确Fraction确定两位小数的half-even舍入，再直接转换Decimal固定小数；不转float，不对巨整数调用str，不改保存JSON里的分数或任何数学结果。极靠近舍入边界的分数仍按精确值决定取舍；超过显示范围（小数点前超过13位）的已知数值以“数值超出显示范围（精确值见JSON）”简写，不能变成UNKNOWN或扩大Python全局数字限制。两位小数可能将微小非零显示为有符号0.00；描述反证仍按原精确值判定。

表下列交易家数及涨跌平、来源总体和已知未交易家数、换手覆盖有效/缺失/≥5%家数，以及不含观察日的前20日可用会话数、成交额中位数和当前金额比。缺轴、中间日期、换手完整性或前20日依赖时分别显示UNKNOWN，不填0；没有完整消费来源四时钟时，研究数值全部保持UNKNOWN。逐窗用中文报告交易总体是否变化和三种描述反证的“观察到/本窗口未出现/UNKNOWN”，未知不当未出现。原run截止、新context截止、实际生成/首次创建及原件四时钟最大值仍在正文前单列。这里首次创建为build实际构造时间，不冒充写盘时间。可见性门禁在表格和统计前执行，NOT_YET_VISIBLE不泄露研究数值。完整JSON保留逐轴状态、原因、源绑定与精确分数，正文不倾倒实现JSON。

调用方负责另存新附件及其文件SHA；本模块没有文件写入、网络、SDK、DB、selector或服务切换。测试使用隔离TEST_FIXTURE和真正native issuer，投资和真实数据验证仍独立。

## 显式调用

从仓库根目录运行，用真实已有原件路径替换占位参数；output必须是新目录：

```sh
PYTHONPATH=stock-fish stock-fish/.venv/bin/python -B stock-fish/scripts/codex_research.py market-explanation \
  --input INPUT_JSON --run ORIGINAL_RUN_JSON --source-receipts RECEIPTS_JSON \
  --source-map SOURCE_MAP_JSON --cutoff NEW_AWARE_CUTOFF --output NEW_DIRECTORY
```

sourceadapter保留既有每文件256MiB上限，核原input/run/receipt/map字节；依据完整日历明确T−20…T，共最多21个依赖会话，缺原件记UNKNOWN。由真实SourceArtifactReader、SourceMarketStream和原native producer重新验证实际原件，不加载context JSON恢复能力。它不是自动续采或硬OS时间/RSS限额；调用方须按任务预算管理真实资源。

```sh
stock-fish/.venv/bin/python -B stock-fish/scripts/codex_research.py report RUN_ID \
  --market-explanation SAVED_ATTACHMENT_JSON --output NEW_REPORT_DIRECTORY
stock-fish/.venv/bin/python -B stock-fish/scripts/codex_research.py daily-report --run-id RUN_ID \
  --market-explanation SAVED_ATTACHMENT_JSON --output NEW_DAILY_DIRECTORY
```

只读消费端还须匹配同一run/input/观察日及可用的原run hash；R2不同run不能塞入V2日报。原run截止、新情境截止、原件四时钟最大值和实际附件生成时间单列；所有这些可见性检查不把历史下载授予PIT。
