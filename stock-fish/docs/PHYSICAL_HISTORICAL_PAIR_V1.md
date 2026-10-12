# 同物理原件 R1/R2 成对历史研究 V1

本版本提供显式研究入口，不接默认服务、CLI、数据库或调度。旧物理历史 V1、R1/R2
公式、配置、来源发行器、资格规则和保存 run 全部保留，不改为历史已经发出的预测。

```python
run_physical_historical_pair(
    sources_manifest_path, source_policy, observation_date,
    registered_worker_deadline, history_sessions=26,
)
```

两个路径分别指向真实七项合同来源清单和实际官方政策原件目录，不接受保存的 input、
base run、policy JSON、reader、回调或外部 validator。模块新增独立版本
`PHYSICAL_ORIGINAL_HISTORICAL_R1_R2_PAIR_V1`，特征为
`SAME_PHYSICAL_INPUT_CUTOFF_NATIVE_PAIR_V1`。参数仍为“尚未验证”，
`MARKET_EDGE=NOT_ESTABLISHED`。

## 一次发行与两个原生研究臂

一次真实 `issue_saved_r2_source_reader` 重新认证物理原件、receipt、真实时钟和来源
映射；一次调用冻结 `physical_historical_research._build` 构造 26 会话新 inline
输入。旧构建器的全 RAW/master 集合、完整日历、全部因子尝试、PARTIAL 祖先、
继承时钟、精确乘积、换手口径及日期化有限深市身份资格守卫继续执行。

构造结束后才记录派生时间，并取一个真实当前截止。外层原生
`run_short_horizon_strategy` 对 R1、R2 各尝试一次，共用同一个 bundle 对象、
输入 hash、同一完整资格和精确截止，不从保存 base run 恢复权限。冻结 R2 原生
实现自身重新执行 R1 collector；因此外层 R1/R2 各一次，内部 R1 评估实际预期
总共两次，不能把外层次数称为全部 collector 次数。

R2 直接使用这次 issuer 发行的真正 `SourceArtifactReader` 及原 receipts 读取
原 RAW bytes。原生消费者重新验证父 SHA、COMPLETE、分页、字段版本、日期、
原时钟、完整四板 A 股 RAW/master 集合和精确金额。近五个真实交易日含观察日，
任一天合法分母缺失则市场参考 UNKNOWN，不使用四天中位数或旧 JSON 总额。
个股近期参与按冻结 PASS/FAIL/UNKNOWN 规则计算；成交软参考不充当筛选门槛。

## 活能力与唯一消费

新 factory 在本进程独立登记 job、PID、owner/issuer/bundle/reader/policy registry
对象身份、输入及 receipts hash、实施身份、精确 cutoff、政策登记表 hash、状态、
两臂尝试数和已返回 run 对象/hash。
初始实施身份同时封印两策略的完整内存配置/规格/选择政策 hash 和参数状态；消费时
与初始封印比较，不能仅重新读取当前 identity 而接受运行期间的内存 CONFIG 变更。
外部构造、浅/深复制、字典重建、改变 PID、截止或输入、清空来源/政策登记、状态
复位、重复 factory 和失败后再消费均拒绝。
第一臂之后保存全文 hash，第二臂返回、逐股比较及终态再次核验；失败封印 FAILED，
不重试。注册的绝对期限通过旧 `_Deadline` 包住发行、构造、两臂及比较，保留其
主线程和已有 signal timer 冲突拒绝，不覆盖调用方既有计时器。

## 完整同股对照

返回一个归档对象：原完整 input、两份完整 native run、原件和政策 registry、
原构建 audit、每臂实际尝试数、完整逐股 comparison 和独立新 source identity。
两臂外层/内层输入、固定 input_id、日期、截止、完整配置/规格/源身份（含全部源文件
hash）、coverage/audit/history 角色、原生 stable run ID、资格 hash、
全部合格 IID、选择 IDs 和各决策计数一致性分别核验。
data_watermark 依真实原 receipts 重算，R2 的 base/context watermark 另按同 cutoff
的实际 used receipt 集合核对；展示一个旧日期不能改变这次真实资料截止或捕获角色。

逐股对照保留原 R1 阶段、匹配模型、决策、R2 决策与参与证据、精确金额及流动性
软参考。所有原 R1 基础字段（包括量价、源引用、exact_features 和 conditions）
必须相同，仅排除冻结 R2 明确改写的版本/决策/展示字段；新增条件映射不能改原值。
行字段集合只允许原字段加冻结 R2 的十三项新增字段，不能混入未登记预测特征。
被改写的版本、决策、style_status、research_status、watch_reasons、reason union、
展示次序及流动性展示标量也分别按原公式与证据重推，不因列入比较排除表就放弃核验。
独立 R1 臂仅剥离冻结外包装新增的字段，重构原生核心内容 hash，并与 R2 真正
内存原生父的完整 provenance、核心 hash 和 native_r1 ID 比较；该纯投影不运行第三次
selector，也不产生来源权限或保存父的恢复入口。
R2 的原 R1 决策和选择必须对应独立 R1 臂，选择仅为 R1 观察与参与 PASS 的交集。
原决策 UNKNOWN 映射到 comparison 的 selected_state=null，不能计为 False 或零。
原 native research_selected 布尔值另保留，避免混淆冻结核心输出与三值研究解释。

归档不包含可恢复的 source/owner，保存或读取 JSON 不授权消费。有限深市资格不扩
为全市场主板资格；四板市场金额分母不替代历史合格池。无历史题材资料保持 UNKNOWN，
单日成对成功不授予完整情绪周期、历史盘前 PIT、收益、可成交性或投资优势。工程
检查、实际原件运行和投资验证分别记录；本版本没有新增预测阈值、权重或生产接线。
