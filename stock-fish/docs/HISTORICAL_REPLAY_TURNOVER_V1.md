# 历史重建换手适配器 V1

`R12_DATED_REGISTERED_IDENTITY_TURNOVER_RECONSTRUCTION_V1` 是显式调用的新原生适配版本，入口为 `HistoricalReplayTurnoverAssembler`。冻结 `HistoricalReplayAssembler`、旧 R12 CLI、旧来源语义和保存 run 保留原样。新版本先调用冻结 build 验真价格、资格、复权和父来源，再从该根目录同一原 RAW 父的同证券、同业务日、`adjustflag=3` 记录补全 `DailyBar.turnover_fraction/turnover_basis`。它不建立新 selector，也不改变 R1/R2 的公式、窗口、阈值或题材数值依赖。

只接受登记的 `provider=baostock`、`field_version=BAOSTOCK_00.9.30_R03_V1` 原件字段 `turn`。该字段沿用现有正常化器的百分数及供应商流通股份口径，输出 basis 为 `BAOSTOCK_TURN_PERCENT_FLOAT_SHARES`。原始 capture、receipt 和 RAW 文件 SHA、请求业务日、代码、调整标识、行内容和原价格须一致。局部 SHA 提供归档完整性，不是供应商签名、历史交易所身份认证或历史 PIT 认证。

登记 `market_daily` 请求参数为 `date`，worker 返回的每日 RAW 行须逐行具有同业务日及 `adjustflag=3`。因此支持真实的 date-only 请求；若原件显式给出调整参数且不是 3，则拒绝。每个已消费原件的 RAW 与 archived receipt 必须有 timezone-aware `first_seen_at` 且代表同一瞬间；result 通常没有该字段，若键存在，其值也必须有效并同刻，不能把显式 null 当成缺键。receipt 的 first_seen 不晚于 ingested；result 的 started/completed 若存在须有效并符合 `started≤seen≤ingested≤completed`。完成可晚于入库毫秒，不要求相等；等效 UTC offset 按瞬间比较。该额外一致性检查拒绝初始时钟矛盾，不改旧重建可见性机制、不改写实收时钟或增加 PIT 授权。

先以 Decimal/Fraction 精确计算百分数除以 100。共享 DailyBar 的字段是 float，而冻结 R2 以 `Fraction(str(float))` 读取它；只有该往返精确等于原分数时才填入。不能无损表达的数值保持 UNKNOWN，原精确分数留在 audit。空值、非法数、负数、非有限数、超过共享字段上界的值、未登记提供方或字段版本均不填零。原件明确报告的零才是合法零。超过 128 字符或十进制指数绝对值超过 128 的字段按不支持精确表示处理，不扩大全局整数限制。

新 audit 保存版本/source identity、冻结基类 SHA、原 build 的 input/audit hash、新 input hash、每行原件/receipt SHA 与 exact fraction。原 normalized receipt ID、内容 hash、历史时间证明及四时钟不改。消费者必须同时保存新 input 和新 audit；原来源字段版本仍标旧 R12 的 receipt 重建语义，新 adapter 身份从 audit 单独绑定。资料抓取或新版本成功不自动证明历史资格齐全。未知板块、退市期、复权或限价证据仍受冻结基类及原生策略门禁约束；`archived_point_in_time=False`、`MARKET_EDGE=NOT_ESTABLISHED`。

本版本仅支持 inline 历史，`history_store` 非空在调用 writer 之前拒绝。未接入旧 CLI、服务、数据库或默认任务；持久化历史接线须另行登记版本和完整 artifact 检查。构造对象后来源文档、checkpoint/result 集合或内容、代码/spec 发生变化均拒绝，需用新的明确上下文构造对象。每个已消费父在读取和返回前均按旧验真逻辑校验原件；新 run 不覆盖任何旧 run。构造与 inline 数据仍在内存中加载；该适配器本身不承诺全年度 streaming、RSS 或时间上限，实际 runner 须保留独立预算和资源护栏。

工程测试使用隔离临时目录和显式 `TEST_FIXTURE_ENGINEERING_ONLY` 捕获、合成来源说明。它们验证精确换手、5% 边界、三值逻辑、同父绑定、篡改拒绝及原价格/资格/R1 选择不变；不是证券投资有效性、真实历史范围或 PIT 验收。
