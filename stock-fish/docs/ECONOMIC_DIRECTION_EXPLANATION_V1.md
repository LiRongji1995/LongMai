# 经济方向解释附件 V1

显式独立解释层，API 位于 `stockfish_core.yaogu.economic_direction_explanation_v1`。它不运行选股，不接默认服务、CLI 或数据库；保存 JSON 不恢复任何来源、主题或选择能力。旧策略、来源合同、IndustrySinaSourceArtifactReader 和 V4 诊断保持原样。

`build_economic_direction_explanation(frozen_grouped=RegisteredFile(...), business_sources=(OfficialBusinessRegistration(...), ...), counted_nodes=(CountedNodeRegistration(...), ...), cutoff=..., extra_registered_files=...)` 接收已固定整文件 SHA 的保存分组附件、正式业务原件及原提供方节点完整原件。调用方先完成原 run 全文和原生主题验收；本接口核对保存附件的完整观察项、原始项 hash、同 run/input/日期绑定，不声称重新认证 selector 或保存 native parent。注册 SHA 是外部执行计划中的固定身份，文件自己声明的 hash 不能授予权限。

三个来源层分别保存：

- 正式年报、主营披露或专项公告证明有限证券的经营、参股、委托管理或项目关系。PDF、实际捕获回执和文本派生父 hash 逐件核对，逐页核对 code/name 与已登记业务 literal。自动 literal 核对不代替人工语义审查；登记的摘要和限制仍是可审阅的解释。报告期、官方路径发布日期定位、精确发布时间 UNKNOWN 和本次实际取得时钟分开。项目终止、小批量/试生产、非主营及其他业务均保留。
- 保存的原生 V4 子题材确认、发酵观察、活跃分歧与原强度顺序照原值并列保留。固态或钠电的供应商子范围不认证广义电池全集；共同成分不跨子范围重复求和。不因阶段名称删除 DIVERGENCE。
- 新的 `CURRENT_WHOLE_COUNTED_PROVIDER_NODE_V2` 只验真原始独立 count 请求与全部 80 行分页、原父完整 RAW/receipt/result、证券 code/symbol/name 与四个捕获顺序时钟。它可审阅既有 `gn_ldc` 原件，不改其旧 field_version 或旧 coverage。公司家数与技术分页总数分别保存，冲突不静默放松。全部 BJ/B/其他编码行原样保留；同 run 相交只是已冻结证券身份的交集，不从代码前缀认证历史资格，也不将 A 股投影称为经济完整性。

`CountedNodeRegistration.originals` 必须包含整个 result 的全部原始回执及 RAW，不能只登记本次命中的股票或部分页。冻结 `verify_capture` 和原时钟合同在新解释中显式复核；页序、数量、重复、规范化身份与原页面组合完全匹配。报价无明确业务日期，绝不进入量价轴。本轮不发行历史成员、经济完整或 ThemeSnapshot 能力。

分页期望集合只在技术 count 与完整规范化结果实际行数一致后构造；超长或异常计数先拒绝，不能把小原件变成无界 range 分配。正式 PDF 回执须是实际 HTTP200 完整捕获状态；失败响应即使有 PDF 头或同形字段也不能支持业务关系。

每个解释主显示组的并集必须精确等于原全部观察项，各项全文保持；原模型和原分组另存。电池及消费业务关系子集按原选择顺序显示。有限已证观察子集的金额只取原 R2 `exact_features.amount` Fraction，广度只取原 `exact_features.returns['1']`；任何项缺失，整轴 UNKNOWN，不补零。金额/广度不代表全板块、全概念资金或强度。广义经济方向发酵、持续性、强度、资金净流入与竞争均 UNKNOWN；不新增权重或预测信号。证券的业务联系不是当日资金归因，供应商标签缺业务原件时仍 UNKNOWN，不据行业硬排除。

可选 `reported_scope=RegisteredFile(...)` 显式复用已固定 SHA 的原文主体审阅。其全部 source registry、HTML 原件与 HTTP 回执均须随 `extra_registered_files` 登记并重读；发布日期区间、真实收到时间及观察日一致。另存“当日报道了此方向”，不据关键词、新闻具名或同稿转载增加成员/资金计分，不把转载或发布者数量称为独立资金来源。此次投影不重跑原文 parser，须明确记录 `SAVED_PRIOR_ORIGINAL_TEXT_REVIEW_REUSED_NOT_REPARSED`；保存审阅不恢复 native 权限，原件缺失或子集登记必须拒绝。

原选股 cutoff、原 native context cutoff、本次证据 cutoff 和实际附件生成时点单列。`render_economic_direction_explanation(attachment, as_of=...)` 仅在新 cutoff 与实际生成均已可见时显示，否则返回 NOT_YET_VISIBLE 且不输出数值。保存 artifact hash 仅作完整性复核；不认证来源能力。所有原件、依赖代码和 spec 在前后边界复核 SHA/stat，不能用只读 getter 隐藏来源 registry 变化。

初始状态为描述研究：PIT=false、完整情绪周期=0、MARKET_EDGE=NOT_ESTABLISHED。真实业务/提供方原件检查、TEST_FIXTURE 工程检查与投资验证分别报告。当前目录异常、子题材漏成员、非 A 行与经济定义冲突都属于覆盖诊断，不据此抬高或降低原选股评分。

实际调用由新私有有界 runner 登记原件 SHA、调用次数、资源与来源终态，产物保存到独立新目录，不覆盖原附件。正式跨方向强度排序和每日自动接线尚未完成，不能把本接口解释分组称为已经完整覆盖所有主线。
