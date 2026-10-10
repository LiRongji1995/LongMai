# 题材报道事件证据 V1

`EVENT_REPORTED_CONTEXT_V1` / `AUTHENTICATED_REPORTED_THEME_EVENTS_V1` 是新增解释附件，不接默认策略、服务或CLI。语义为初始、尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`。旧R1/R2、5%参与度、动态成交参考、主题FSM与审计spool均未修改。此模块不能输出新的选股名单、升级FOCUS或证明历史主线资格。

## 原件与时钟

调用方提供现有 `SourceArtifactReader`，模块实际读取原始bytes并再次验证SourceReceipt的SHA，最多2MiB，不执行HTML脚本、资产或网络。不能用manifest的authenticated布尔替代原件能力。`ArticleArtifact`记录receipt ID、固定parser、实际request start、实际received、ingested；后两者必须分别与receipt的first_seen/ingested相同，且request≤received≤ingested。原件需要COMPLETE、完整分页与至少一篇记录，否则UNKNOWN。

固定解析器包括CLS的NEXT_DATA、证券时报HTML、上证报纸版、上证报NEXT_DATA、中国政府网国新办发布会；测试JSON解析器仅允许明确TEST_FIXTURE。未支持的来源（例如每经）保留原件SHA和UNSUPPORTED/UNKNOWN，不从多个发布时间猜选一个。已支持解析器字段或ID矛盾则拒绝，不能降为缺源来吞掉错误。

出版日期只有日精度时，保存半开界限 `[本日00:00,次日00:00)`；分钟精度同样保存半开界限。界限不是填补的精确publication timestamp。只有出版上界、实际request/received/ingested和全部原receipt时钟均不晚于cutoff才可供当前解释使用。原件较晚取得时，即使coverage为RECONSTRUCTED_HISTORICAL、有人工HistoricalTimeProof或网页日期很早也不通过旧cutoff。官方PIT可计数始终false。

现在实际取得的旧日文章可在新的、允许这些真实时钟的研究context中复盘；不能写回旧run或声称当时本机已持有。网页历史版本是否未被修改仍未知。

## 三类事实与数值

`EventClaim`只有CATALYST_REPORTED、LADDER_REPORTED、DIVERGENCE_REPORTED三类。原文片段必须实际存在于固定解析器的正文，输出字段定位、字符区间和片段SHA。种类、comparison key、population与upstream标签是显式人工审核声明，不是已认证的市场统计池、题材成员或因果关系。新闻文本认证不等于语义或交易事实认证。

有限的数值解析只支持涨停数、炸板数、封板率、Token数量、涨价区间、连续板与X天Y板。匹配缺失或歧义为UNKNOWN。缺失不填0，数量使用Decimal/Fraction精确表示。单位冲突保存原单位，不能把词元数当人民币成交额。新闻涨停、梯队和资金叙述永远不能替代交易所限价事件、RAW成交额、可成交性或龙虎榜席位原件。

同一声明池内，封板率只做假设算术检查，并按原文百分数的小数位保留显示精度；不从59/19/80的冲突擅自修改任何市场真值。相同日期、snapshot和metric的不同数量保留冲突，不平均、不擅自把“90只涨停”改称触板。盘中与收盘、连续板与X天Y板分别保存；圣阳“午间第八板、收盘炸板止步七板”仅生成时点路径提示，不自动认为某来源错误。snapshot仍是报道范围声明，原生交易路径未知。

来源标签归到原创家族；证券时报转载财联社仍归CLS，上证报网站/纸版/微信公众号不作为多个独立家族。不同署名报道也不证明市场数据源、上游催化或公司公告独立。上游origin未经原件认证时保持“declared”。

## 原生父与冻结范围

接口：

```python
context = build_theme_event_context(
    bundle, native_theme_parent, native_selection_parent,
    cutoff=cutoff, selected_ids=selected_ids, qualified_ids=eligible_pool_ids,
    artifact_reader=reader, artifacts=artifact_specs, claims=claim_specs,
    _theme_parent_validator=capability.verify_theme,
    _selection_parent_validator=capability.verify_selection,
)
attachment = annotate_theme_observation_diagnostics(native_diagnostics, context)
```

父validator签名为`verify_theme(parent, *, bundle, cutoff)`；selection为`verify_selection(parent, *, selected_ids, qualified_ids, bundle, cutoff)`。二者必须由真正的原生producer或已严格认证的归档adapter绑定，成功返回None，失败raise；布尔True、lambda True或调用方自算hash不能认证父。绑定方法检查只是接口约束，不能防恶意Python调用者伪造能力。

模块核对bundle/input hash、原生主题父canonical hash、cutoff/观察日/coverage、原选择父的eligible全集、selected集合和FOCUS0。调用方能力负责认证父来自实际原生计算/冻结归档，而非自称native的JSON。测试使用真实原生te/R1 TEST_FIXTURE父，按原对象、hash与source identity绑定，不能用lambdaTrue替代。

证券关联只允许冻结selected中的代码、观察日、当时资格记录有效区间与姓名完全相符，且姓名实际出现在原文片段中。旧日姓名、更名、姓名与代码不匹配、未选中股票均保持身份UNKNOWN，不凭当前搜索或记忆补代码。同证券在多个题材叙事出现单列共享，不计独立协同，完整历史成员仍UNKNOWN。

返回context为producer发行、JSON可序列化的进程内能力。注释函数重新认证父和原件、拒绝篡改或普通自hash JSON；保存JSON后重新使用需要真实归档adapter，目前未实现。它以新envelope保留原诊断原文/hash，只给原生已经存在的确认、发酵、活跃分歧及缺数组附加claim IDs，不生成新的主线。没有新闻不等于没有催化；未知题材不能由文章创建原生标签。

## 尚未接线与后续需求

未修改现有research_context_artifacts、strategy_experiments、默认run、保存/查询/export或日常通知。本轮不能宣传已成为每日生效规则。未来接线需要显式新context版本，将原件/receipt/claim manifest/父hash及模式纳入research_context_hash、effective_input_hash、source_refs和新run身份；旧input/run/spool身份保持原样。不得仅在私有报告脚本另造平行名单。

催化、报道梯队、分歧是新signal的事实入口。RAW资金量、广度/参与股数、持续时间、量价变化与大盘同步趋势需独立版本和同源完整时间窗；涨停数、晋级率须先验证同池同截面。游资需要交易所原始席位金额与买卖方向，公开席位别名不代表个人身份。新闻不用于新增未经验证权重，不因两篇报道或报道热度改变R2门槛。多轮情绪验证、因果、统计规律与市场优势均尚未建立。
