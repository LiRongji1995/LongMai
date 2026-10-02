# V2 输入、证据及资源合同（2026-10-02）

实现版本 `GO_STRICT_MARKET_INPUT_V1`。本文件的工程小样本不代表全池策略等价、真实数据库迁移验收或生产激活。

## 模型、默认值与身份

`DecodeFrozenContext` 在类型化解码前拒绝重复 JSON 键和非法 UTF-8。必填字段、JSON 类型、枚举、合法日期、显式时区、非负计数、精确十进制、历史引用/hash配对、唯一升序日历、按UTC实际瞬间识别的重复bar revision均校验。包装允许现有oracle的可选 `purpose` 元数据；MarketInput不因此增加业务属性。

Security、Universe、DailyBar、Receipt、Theme、HistoricalTimeProof、MarketEmotion的额外字段拒绝。Funds在Python原合同为任意dict，本层只验证对象形态；消费者仍须校验来源/窗口/单位与字段用途，不能据此宣称资金业务合同已满足。

默认值只在键缺失时插入：`st`/`suspended`必填可null；缺`listed_on`拒绝；`delisting_period`缺失默认false但显式null保持未知；`limit_verified`显式null拒绝。缺数值不会补零。`ValidateFrozenContext`只用于已构造Go对象；Go零值不携带JSON presence，外部输入必须走Decode。

原字节SHA256、旧Python模型/source hash、Go规范化投影hash独立。`CanonicalInputProjection`和`RowHash`产生带转换版本的Go身份，不能冒用旧source_hash。Decimal字符串按有限精确表示规范化、保留有效精度与末尾零；datetime按冻结Python模型的微秒精度规范化，原字节另存。JSON原数字不会先经float64，因此任意数值lexeme不承诺与旧Python json.loads浮点路径hash恒等。

Go严格拒绝Pydantic原来能强转的字符串/数字布尔、字符串turnover、1.0型计数。此为版本化输入合同差异，不调整策略阈值。合法typed模型的默认/Decimal/微秒时间投影已用Python实际生成的 `core/testdata/input_contract_python.json` 做hash一致回归；它明确是TEST_FIXTURE。

## 来源与用途

沿用visibility.py四时钟、父回执、证券/会话/有效区间、来源类型/定义版本、COMPLETE及cutoff。未解析父来源、同ID异内容冲突、未来事件/发布、row capture早于父回执都不能授权。返回source IDs排序去重。

RECONSTRUCTED_HISTORICAL只允许capture晚于cutoff；事件与发布仍须可见。晚到父来源需要完整历史时点证明和适用日期范围；cutoff前已取得的父来源可许可后来规范化行。TEST_FIXTURE/prospective没有重建许可。证明声明不等于来源真实性的密码学证明。

PARTIAL可用于已存在单行字段；`Scope.Fields`要求使用字段存在且非null；`Scope.Complete`拒绝PARTIAL及分页未完成，不能把缺行/缺字段推成合法EMPTY。总体/题材/资金完整性仍由消费者显式要求Complete。最高优先级同钟异内容修订返回冲突。

## 本轮确认的旧漏洞

|问题|修复与实际回归|
|---|---|
|OpenHistory命名返回覆盖清理引用|defer捕获独立owner；十种失败后立即BEGIN EXCLUSIVE成功，callback失败可立即再次读|
|manifest只验Canonical(raw)|先Hash(raw)==SQLite content_hash；只加前导空白且不更新旧hash必须失败|
|缺listed_on空字符串可计算足龄|输入拒绝缺/null/非法日期，策略资格函数另加防御|
|typed索引身份可能脱离payload|LEFT JOIN保留缺失检测，比较row_hash及源表kind/instrument/session，流式行读取先验原字节|
|oracle缺失skip|普通可选差分的skip不能计入正式验收；显式oracle验收门禁由统一应用交付处理|

## 取消、资源和平台

Context覆盖JSON parse/encode、模型循环、SQL、索引及row callback；空历史窗口也检查取消。Budget启动即测RSS并每秒采样；Go heap与实际resident set口径分开。采样拒绝不是OS强制内存隔离；负数计量不能减少预算。

Windows新增Microsoft官方 `GetProcessMemoryInfo` / `PROCESS_MEMORY_COUNTERS.WorkingSetSize` 当前驻留工作集实现：[API](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-getprocessmemoryinfo)、[结构](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters)。Windows原生运行未验证，交叉编译仅证明绑定能构建。

已执行（私有Go 1.27.1，系统Go过旧不采用）：

- `go test ./core -run 'TestStrictInput|TestHistory|TestOpenHistory|TestCanonical|TestPartialFields|TestReconstruction|TestReceiptConflict|TestPythonContract' -count=1`：退出0，0.700秒。最终统一测试覆盖后续完整typed元数据增强。
- `GOOS=windows GOARCH=amd64 go test -c -o /tmp/stockfish-evidence-windows.test.exe core/rss_windows.go`：退出0；无原生Windows执行。
- Python Clocked定向实测：.100规范化为.100000、七位小数截断微秒；退出0。Python模型fixture hash：335abfa13e3708b0315a3d8ea1f0edec3abc48c6be3ae676cb6607869742df67。

全池对照、跨存储证据集合、race/vet、真实迁移与独立运行以统一交付报告为准，不由上述小样本代替。

架构落位：严格输入/Resolver在`evidence`；Row/精确数字/Canonical在`domain/contracts`；预算与RSS在`infrastructure/runtime`；旧SQLite历史读取在`infrastructure/legacy`；新库读取在`infrastructure/sqlitev2`。`core`仅保留向这些所有者的兼容适配。新增architecture依赖测试遍历全部平台源文件，禁止新层反向依赖core以及纯领域层依赖SQL/HTTP/OS/应用适配器。

## 每次分析的背景批缓存

`facts.NewBackgroundBatch`一次构建证券到可见题材的索引、题材成员集合及资金分组；预先解码精确金额、窗口、分类、去重身份、完整性范围与证据拒绝原因。题材收益运算复用同一成员列表，个股资金仍先排除自身，再按原来源顺序处理去重冲突与重叠窗口。完整空集证明的expected scope仍包含自身；PARTIAL能支持已观测值，不能证明完整空集。原Row仅作为返回证据保留，批构建后不得修改该分析输入。

Resolver缓存父receipt四时钟、证券/会话scope及历史证明解析结果；证明适用日期仍逐请求判断。为保留旧可变Row调用语义，每次命中只比较相关字段原字节，有变化才重建该receipt缓存，不重复对稳定父来源执行JSON、日期及时间解析。`NewResolverContext`与BackgroundBatch构造循环均检查取消。

本轮使用冻结的14个`TEST_FIXTURE-oracle-full-v3/*.expected.json`，`STOCKFISH_FORMAL_ACCEPTANCE=1`与显式`STOCKFISH_R1_ORACLE`运行 `go test -p 1 -timeout 30m ./core ./facts ./evidence ./application/research ./architecture -count=1`：退出0，core 99.088秒（R1/V2完整语义差分）、facts 0.576秒、evidence 0.592秒、architecture 0.361秒。新增资金边界和缓存失效/取消回归，`go test -race -p 1 -timeout 10m ./facts ./evidence -count=1`退出0（1.978/1.755秒）。未运行真实策略或大库查询；此结果不能替代真实规模性能验收。
