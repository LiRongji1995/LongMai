# 龙虎榜原件席位身份解释附件 V1

版本 `LHB_REPORTED_SEAT_CONTEXT_V1`，特征 `EASTMONEY_UPSTREAM_DEPARTMENT_IDENTITY_V1`。本版仅恢复来源原件中存在的提供方编码，并审核买卖榜重复展示。来源语义仍是初始、尚未验证的研究设计；默认服务、选股器、题材 FSM 与 FUNDS_V1 均未接线。

## 修复范围与证据边界

原 `AkshareLhbAdapter` 调用的 SDK DataFrame 删除了上游 `OPERATEDEPT_CODE`、`OPERATEDEPT_CODE_OLD`、`TRADE_ID`、`CHANGE_TYPE`、`SECUCODE` 等字段。新独立适配器从已保存的上游 canonical JSON 读取这些字段，不修改旧适配器、原件或旧结果。

归档是 SDK 已解析响应的 canonical JSON，**不是 HTTP wire bytes**。精确金额只相对于这些已保存数字文本成立，不能追回 SDK/JSON 首次解析前可能丢失的精度。外部记录的 manifest SHA 绑定实际结果、全部 receipt 与 raw 文件；本地 SHA 证明字节未变，不证明发布者的安全真实性。可信来源仍依赖实际原生采集与归档调用者，不接受文件中的 `authenticated` 布尔作为能力。

`BUY/SELL/NET/ACCUM_AMOUNT` 不自带币种、单位或完整定义。当前文档与 receipt 未证明这些字段的 CNY 单位，故输出 `reported_*_amount_exact`，`currency/amount_unit/measurement_definition` 保持空与 `UNKNOWN`。不命名为 `buy_cny`，不转万元，不作资金评分。`NET=BUY-SELL` 仅审核同一原记录的数字算术，不表示已证明市场净流入。每行重复的 `ACCUM_AMOUNT` 是报告的证券窗口总额，不能逐席位累加。

`TRADE_DATE` 的午夜值只确认业务日期；`TRADE_ID/CHANGE_TYPE/EXPLANATION` 是来源声明的披露标识及原因，不认证交易窗口。三日异常原因不能推断窗口起点、各席位所属窗口或单日净买入。独立披露发布时间、逐 HTTP 请求起始时间均未知。保留真实 receipt 的 `first_seen_at/ingested_at` 和真实批次界限，不补四时钟以强塞 `SourceReceipt`。任何实际收到或入库时间晚于上下文 cutoff 的资料均不可用；历史重建标签不能放宽。

非零 `OPERATEDEPT_CODE` 仅认证该提供方声明的来源编码。保留字符串和命名空间，不把旧编码当作跨日更名或个人别名证据。`0`、全零、空、缺失编码保持身份未解析。机构专用的 `0` 不能成为统一机构身份；无编码的同名行不合并。深股通/沪股通是提供方渠道标识，即使有编码也不是自然人或独立游资账户。营业部编码也不等于实际个人。

## 重复与可用性

BUY 和 SELL 是榜单展示侧，同一证券披露可同时出现同一个来源编码。未知窗口时，仅按证券、业务日、`TRADE_ID`、`CHANGE_TYPE`、有效当前编码进行同一披露 appearance 合并；金额、名称、原因及保留的身份字段必须一致，冲突拒绝，不挑一份、不求和。合并后保留全部 appearance、两侧 receipt 与原字段路径。此合并不是经过认证窗口的资金去重。

本页 `pages=1/count=len(data)` 与已保存 SDK 行数、金额/名称/原因投影一致，只说明提供方本页自述一致；不证明全体交易席位覆盖、完整披露范围或参与者人数。`availability=AVAILABLE` 只表示原件报告事实可读，`decision_readiness`、单位、窗口及独立发布时间仍为 `UNKNOWN`。空数据、缺一侧、失败、晚捕获或本页自述不一致则事实可用性为 `UNKNOWN`，不表示没有游资。缺金额不补 0；布尔、非有限数字、负买卖额与净额算术冲突拒绝。概率、三日历史买卖次数、比例、涨幅与收盘价列不进入解释特征。

解析资源边界为每件 2MiB、数值最多 128 位有效数字且十进制指数/数量级绝对值不超过 128。这是防止极端 JSON 指数消耗内存的输入边界，不是投资金额门槛。`TEST_FIXTURE` 来源仅可绑定 `TEST_FIXTURE` 父，不能混入真实历史上下文。

2026-10-09 雄韬 probe 的真实原件展示了 10 次 appearance：深股通编码 `10634757` 在两侧完全重复；机构编码为 `0`。因此可以恢复 8 个不同非零提供方编码，并保留一条未解析机构 appearance；不能声称 10 个独立席位、9 个游资或某个知名个人。该事实来自本次 Oct10 实际捕获，不写回原 Oct9 盘前或原 21:24 截止父结果。

## 接口

```python
source = ArchivedLhbSeatSource(manifest_path, expected_manifest_sha256)
facts = parse_archived_lhb_seats(
    source, instrument_id='002733.SZ', business_session='2026-10-09', cutoff=new_cutoff)
attachment = bind_lhb_seat_context(
    facts, bundle=bundle, native_parent=verified_parent,
    selected_ids=frozen_selected_ids, cutoff=new_cutoff,
    _parent_validator=native_archive.verify_context,
    _selection_validator=native_archive.verify_selection)
```

解析器会重新读取原件校验 SHA，返回带进程内来源能力的字典。序列化 JSON 自身不能重新产生能力；归档重新认证需再次调用真实 reader。父能力必须实际验证原父对象、原输入和原 cutoff，选择能力必须验证冻结合格集合；二者需要以异常拒绝不符并返回 `None`。绑定方法形状只是接口约束，不能防恶意 Python 实现。

显式新上下文 cutoff 可晚于原父 cutoff，但输入、观察日、原父数值、原选择必须由真实父能力保持冻结；新来源时钟必须在新 cutoff 前。附件只针对已选中股票，不生成新名单或主线，不修改父对象、FSM、安全门禁或正式 FOCUS。`funds_score_authority=false`，官方 PIT 与情绪周期验证数为 0，`MARKET_EDGE=NOT_ESTABLISHED`。

后续若要认证币种、披露窗口、席位持续/买卖方向趋势，必须补独立原件能力、来源定义与实际可见时间，登记新语义版本及负例测试。本版不接受自称 verified 的窗口字典，也不抓取新数据、访问数据库或计算权重。
