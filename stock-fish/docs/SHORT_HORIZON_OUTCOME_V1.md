# SHORT_HORIZON_OUTCOME_V1：R1 / R2 独立后验观察

本契约新增短周期研究的后验服务能力，适用于同一权威服务库已保存的 `SHORT_HORIZON_STRENGTH_R1`、`SHORT_HORIZON_STRENGTH_R2` run。原始 run、配置、选择、`outcomes_status=NOT_IMPLEMENTED` 等历史字段均保持原值；这些字段描述生成当时的能力。当前服务层通过独立 `SHORT_HORIZON_OUTCOME_V1` 产品报告能力与结果。它不修改 DAILY_V1、SAM V1 / V2 或默认激活策略。

## 使用前提与入口

原 run 和原始 `market_…` 输入必须已经保存在当前服务库，新后验输入也必须经现有输入登记流程获得 `market_…` ID。输入读取沿用当前字节哈希验证、MarketInput 校验及历史 artifact owner 绑定，TEST_FIXTURE 仍只允许在显式启用 fixture 的测试服务中使用。

standalone premarket 私有归档中的 run / JSON 并不会自动成为业务库中的权威 run。本版本没有实现 standalone 到服务库的带来源身份导入，禁止直接把普通 JSON 恢复成 source / history owner。用户已有归档若不满足上述前提，仍需后续独立、可审计的衔接工程。

使用既有 API，显式给出已登记的新输入，不隐式选择“最新”资料：

```text
POST /api/strategy-experiments/{run_id}/outcomes
{"input_id":"market_<registered hash>","horizons":[1,2,3,5,10]}

POST /api/strategy-experiments/{run_id}/tracking
{"input_id":"market_<registered hash>","horizons":[1,2,3,5,10]}

GET /api/jobs/{job_id}
GET /api/strategy-experiments/{run_id}/outcomes
GET /api/strategy-experiments/{run_id}/tracking
GET /api/strategy-experiments/{run_id}/review
GET /api/strategy-experiments/{run_id}/items/{instrument_id}
```

提交 outcomes 或 tracking 都在同一现有作业中计算并原子保存 outcomes、tracking、review 三种产品。复盘查询读取保存产品；不单独发起自动重算。省略 horizons 默认 H1、H2、H3、H5、H10；只支持这些正整数，会去重排序。省略 input_id 拒绝请求。作业复用既有取消、状态、异常清洗和原子完成机制；逐证券检查取消，取消先于提交成功则不保存三种产品。

## 观察与未知语义

范围来自冻结 run 的全部评估 items。`research_selected`、setup / `primary_stage`、`matched_arms` 和原 `decision_status` 被保留；`formal_focus=false`，不得把研究选择当成 FOCUS。参考价格只使用冻结的 `exact_features.close_comparable` 精确分数及 `comparable_basis`，不用展示用的八位小数值。后验输入中的信号日参考必须可见且与冻结值、复权口径一致；不一致不重述旧 run。

H1 / 2 / 3 / 5 / 10 以交易会话计数。当前实现用项目既有 `exchange_calendar_2026.json` 的有限沪深官方日历校验登记输入中的完整会话轴，并保存来源链接、校验日期、文件哈希和实现源码哈希。它不外推 2027。真实评估时点早于本地日历 `verified_at=2026-09-05` 时，日历知识不足；TEST_FIXTURE 明确标记该时间限制的测试例外。日历校验只发生在评估时，不声称旧信号生成时已经掌握后来验证的日历。

如果信号日不在新输入日历，或 H 所需登记会话不足、漏日、含额外错误会话、超出已验证日历，`calendar_mature=null`、`label_end=null`、行 `status=UNKNOWN`。后面的会话不能前移填补缺日。已知完整 label_end 尚未到上海时间 15:00 才是 `calendar_mature=false`、`PENDING`。收盘且整个窗口证据完整为 `MATURE`；已收盘但价格 / 资格 / 父来源不足则 `UNKNOWN`。

评估采用实际四时钟可见性。即便输入标为 `RECONSTRUCTED_HISTORICAL`，也不放宽评估时点之后的首次取得 / 入库时间。`UNVERIFIABLE_AS_OF` 的信号或后验输入直接拒绝，不能因严格时钟策略被升级为可用。父回执必须 COMPLETE 且分页完成，证券有效区间和原始身份必须匹配。参考与未来日线自身的 event_time、source_published_at 均不得早于该会话上海时间 15:00；早盘行不能仅因评估发生在收盘后就变成终盘价格。停牌、退市、身份未知、价格缺口、PARTIAL / 非法 OHLC、参考被修订或复权不一致均不产生零收益。

终盘还必须有可识别的价格父响应：本版本仅接受既有归一化价格端点 dataset `history`、`market_daily`、`history_adjusted`、`tushare_daily`。至少一条且所有这类价格父回执的 first_seen_at、ingested_at 均须不早于该会话 15:00；不能在 10 点取得原始价格响应后，仅把派生行的时钟改成 15 点来取得终盘权威。未知 dataset 不猜作价格父，保留 `UNKNOWN`。复权因子、预先发布限价等辅助父来源可以早于收盘，但仍须通过完整性及评估时点可见检查，且不能代替价格父。收盘之后实际下载的历史批量响应，只要在评估时点确已可见、与冻结参考一致，仍可用于后验；不会倒填成旧信号时点已取得。

窗口中每个会话必须可观察，才能输出终点相对冻结收盘变化、次日开盘缺口、次日开盘至终点变化及 OHLC 上下界 MFE / MAE。缺中间会话时，不能仅凭最后一天计算完整窗口收益。这里的指标均为可比标价变化，`execution_status=NOT_VERIFIED`；没有模拟实际买入、费用、涨跌停排队、滑点或 T+1 可成交性。次日开盘到当日收盘尤其不能声称为 T+1 可实现利润。日线 OHLC 也不能识别盘中先后顺序。

tracking 是逐证券、逐交易会话的状态 / 原因 / 证据哈希与各 H 成熟度和未知覆盖，不创建 SAM episode、交易、shadow outcome 或推荐评分。review 按 run、horizon、全样本 / research_selected 两组提供分母、成熟 / 未成熟 / 未知数量、证券数和描述性标价均值。个股指标在 outcomes 与详情接口中可查询。单个 run 只有一个信号日期；同日横截面、重叠窗口及重复证券不作为独立时间样本。这里不计算显著性、市场优势或正式验证通过，`MARKET_EDGE=NOT_ESTABLISHED`。

## 不可变修订和查询

三种产品使用现有 SQLite documents 表中的独立 namespaces：

- `short_horizon_outcome_revisions_v1`
- `short_horizon_tracking_revisions_v1`
- `short_horizon_review_revisions_v1`

修订 ID 绑定原 run 内容哈希、原输入 ID、新输入内容哈希、H 集合、登记会话轴、官方日历身份、执行器及其价格 / 可见性依赖源码哈希、实际依赖证据和结果。相同输入、可见性和成熟度重复评估时保留首个 `evaluated_at`，不会因墙钟走动覆写或新增相同修订。资料修订、成熟度变化、实际可见证据变化或实现身份变化生成新修订；旧修订仍可审计。

查询只在指定 run 的 namespace 范围内使用带哈希校验的 scoped API。当前响应选择最大评估时点，平局按修订 ID，提供修订摘要；同一时点不同输入并存时该规则仅是确定性展示，不代表一个输入更权威。每个产品保留 outcome_input_id、evaluated_at、horizons 等身份供调用方核对。后续较短 H 的新提交可能成为最新产品，旧 H 产品仍列于修订摘要，不静默合并不同输入 / 不同评估时点。

未执行时服务能力 `IMPLEMENTED`、产品 `PENDING`、原因为 `SHORT_HORIZON_OBSERVATION_NOT_RUN`。已经执行的产品顶层 `AVAILABLE` 只表示保存产物存在，不能代替每行 `MATURE / PENDING / UNKNOWN`。个股详情同步返回该股新产品与 schema / 状态 / revision 元数据，未执行不伪装成有权威结果的合法空集。

当前查询仍需校验并解码指定 run 的全部产品修订，单个产品含全池 × H 结果；这是第一阶段的容量限制。没有扩大成全库扫描，但长期大量修订的索引摘要、分页和最新产品读取仍需后续优化。服务也没有把短周期 run 加入旧自动采集的 pending / horizon_requests 队列；该版本不自动采集或启动全历史回测。

## 验收与未实现边界

专用 TEST_FIXTURE 测试覆盖精确参考、全池 / 选择组分母、实际会话轴、漏日不压缩、三值成熟度、父来源缺失 / 不完整 / 晚可见、历史覆盖禁止评估时点后数据、不可验证覆盖拒绝、停牌 / 退市 / 状态未知、复权修订、禁止展示价格代替参考、不可变输入与原 run、三产品原子保存、重复幂等、修订查询、详情一致和取消不落产物。旧 R1 兼容测试的端点能力断言按新版本更新，保持不污染正式策略、旧 pending 和正式验证的断言。

这些测试是工程正确性验证，不是投资验证。真实 R2 市场效果、独立日期的前瞻积累、基准 / 匹配对照、成本容量、账户组合、执行退出、跨 run 样本治理与统计显著性仍需独立实验，不由本契约推断。真实数据验证也必须显式选择范围和已登记输入，不因后验能力上线自动扩大采集预算。
