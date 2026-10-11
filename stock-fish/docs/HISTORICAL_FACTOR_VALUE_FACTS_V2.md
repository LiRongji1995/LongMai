# 逐证券 factor 数值事实与强制审计血缘 V2

本版本显式采用 `R12_FACTOR_FRAGMENT_VALUE_FACTS_AND_MANDATORY_AUDIT_V2`，生产者为 `HistoricalReplayFactorFactsAssembler`，消费入口为 `run_short_horizon_with_factor_fact_context`。默认入口、已发布 lineage V1、turnover V1、RAW×backFactor、历史证券资格、换手函数、R1公式/配置/阈值和 `EvidenceResolver.require_complete=True` 均不改。历史 V1 和已保存 Apr14 结果冻结。

V1 中真正逐股 fragment 已经 COMPLETE，但 bar 同级数值来源也引用 PARTIAL 祖先批次，严格 R1 因此给出 `SOURCE_NOT_COMPLETE`。V2 为独立证据语义版本：完整逐证券数值事实仍必须满足严格数值来源完整性；原批次和祖先改为独立、必需鉴权的审计依赖。不能把此工程漏洞解释为题材退潮。

调用者提供真实活动 capture 目录、原 policy、`AncestorCapture` 元组及 `CaptureImplementationFiles` 三个真实代码文件。旧捕获 implementation identity 来自真实保存的原代码字节，可为验证过的 Git 对象快照；它与当前 validator/consumer 代码身份分别完整记录。快照只做 SHA/stat 认证，不执行旧 worker、不联网；不能只提供旧 SHA 表、不能把新 provider 代码 SHA 伪装旧 SHA。现有 provider 新增 endpoint 不自动改变旧捕获声明。

生产者组合冻结 turnover 的数值 build，并复用冻结 lineage 的完整三父认证和逐股全行证明函数。所有 RAW/receipt/result、原 request/receipt 自哈希、RAW 内容 hash、嵌入 receipt、provider/字段版本/implementation、status/pagination、rows/coverage/gaps/error/diagnostics、唯一 code/日期 scope、非 null 行数、真实 aware clocks 均重新验证。每原件至多 8 MiB；SHA/stat 读前后及消费前后不变。原 final RAW.first_seen 与 receipt/wrapper 必须通过冻结严格门禁，缺失不得由 archive proxy 代替。

若 final 的 code captured_at 位于 final wrapper 外，必须有真实原 PARTIAL 三件：参数/provider/字段版本一致，逐证券 COMPLETE/cursor/整数 row_count/captured_at 状态全值相同，全部 factor rows 多重集合相同，包括重复行的数量，捕获确实在祖先真实 wrapper 区间，祖先完成不晚于 final 开始。全部匹配祖先保留，不挑事后有利证明。原 PARTIAL/pagination=false、原 null 行数、原381 out-of-parent gap 和缺 RAW.first_seen 的 UNKNOWN 保留；receipt 仅为实际 archive proxy，不能证明 source publication 或 April PIT。

新 COMPLETE fact 表示这个 code 的原捕获已完整、在当前真实时刻完成验证；它不表示原 batch COMPLETE、价基必然就绪或历史可见。空 COMPLETE 捕获仍可能没有左锚；非正 factor、日期不覆盖、缺价格、停牌、历史资格未知等由原函数保持 UNKNOWN，不补0、不carry、不填factor=1、不删证券或较窄替代400股。原始OHLC、成交额、精确换手、限价未知轴和资格分母保持。

`DailyBar.source_refs` 只引用完整 MD 数值来源、新完整逐证券 fact 和当前验证凭证。原 final 与所有 PARTIAL 祖先 archive proxy 继续保存在 bundle.receipts；完整原三件、状态、scope、时钟及逐 fact→audit parents 链同时存于强制审计图。`SourceReceipt.parameters` 只放合法 scalar graph/proof hash，完整 refs 在审计图中。丢审计父、错rows、未来父或改变完整图均不能获得消费许可。

保存 JSON 不授予来源权限。新 COMPLETE fact/validation receipts 由新同进程私有证据 owner 的真实 manifest 提供，普通 bundle.receipts 不复制这些数值权限；bar 中所需 fact refs 全部保留。owner manifest 使用 `CURRENT_FACTOR_FACT_INLINE_EVIDENCE_OWNER_V1`、真实内容hash和完整 inline rows scope，明确不是 `CONTENT_ADDRESSED_HISTORY_ARTIFACT_V1`、不是 SQLite 分区、不是完整历史或旧 history ref。`history_<hash>` 仅是冻结 MarketInput 字段要求的引用语法。没有数据库、持久writer或旧 owner 借用。原 MarketInput JSON恢复丢失私有owner，原 history_resolver 严格拒绝；删除 history ref仍没有必需 fact receipts，原 resolver不能认可数值来源。

manifest 的 `partitions=[]` 诚实声明没有 SQLite 分区，供冻结覆盖诊断接口读取；已有 `selected_rows` 桥逐行提供本次真实 inline typed bar 的 JSON 候选，核验同进程 owner、bundle hash、原行与完整 refs。原诊断仍选择修订，原 resolver 仍校验时钟和 COMPLETE 数值来源。此 owner 仅支持本版本显式 R1 消费路径，不宣称提供通用 SQLite canonical history 接口或完整历史；空 partitions 不代表空行情。

真实工厂在当前实际发行之后生成真实 UTC cutoff，并绑定输入/source identity/审计图/owner/PID/登记 worker deadline。只有工厂注册的 context/authority 对象可用，复制、替换、JSON描述、过期或跨输入/cutoff对象不授权。生命周期 ISSUED→CONSUMING 一次→SEALED 或 INVALIDATED，失败同样不能重用。私有owner只在受控消费过程中读取。这个边界防止正常API调用把保存JSON恢复为权限，不宣称防御任意恶意同进程Python。

消费入口先对全原件及全部逐股证明重新鉴权，构建同一新 bundle 的原资格/价格 context，接 `FactorFactAuditResolver` 后调用未修改的 R1 一次。新 resolver 检查原行身份、所需 fact refs 和必需审计链接，随后调用原严格 `EvidenceResolver.resolve`；不放宽 complete、clock/session/code/basis 或冻结价格选择。当前 reconstructed 路径不使用 prospective 快路径。消费后全源/代码/输入/manifest/证明终态复核失败不得封存成功，输出同时绑定数值依赖和必需审计依赖、原R1身份和新 producer/consumer身份。

本版本只支持实际当前 cutoff 的事后研究与显式 TEST_FIXTURE。工程测试必须覆盖丢祖先/错全rows/未来时钟/不完整fragment/缺final RAW clock、原PARTIAL与RAW时钟未知保真、缺左锚、JSON恢复及删history ref、过期/替换/复制权限、源漂移和严格R1真实接线；不能删样本或弱化断言。真实 Apr14 新版及 Apr16/20 需独立注册和授权，工程检查不授权 native。`MARKET_EDGE=NOT_ESTABLISHED`，完整独立情绪周期证据仍为0；不增加策略系数、阈值、资金净流入分数或收益结论。
