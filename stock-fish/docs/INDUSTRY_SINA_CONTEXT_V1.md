# 显式新浪行业当前成分来源 V1

`CURRENT_SINA_INDUSTRY_CONTEXT_V1` 是独立 opt-in 来源适配。它不修改默认概念读取器、R1/R2 规则、选股、评分、日报或 CLI，也不把供应商行业目录整体升为主线。初始规则尚未投资验证，`MARKET_EDGE=NOT_ESTABLISHED`。

新增登记端点：

| endpoint | SDK 函数及参数 | 作用 |
| --- | --- | --- |
| `industry_list_sina` | `stock_sector_spot(indicator='行业')`，调用参数为空 | 当前行业目录 |
| `industry_members_sina` | `stock_sector_detail(sector=<真实目录 label>)`，参数 `symbol` | 指定当前行业成员 |

实际已存在 AKShare 1.18.94 的 `stock_industry.py` 登记了以下路径；此文档及工程测试不声称端点在线健康，也未执行真实网络请求。

- 目录：`http://money.finance.sina.com.cn/q/view/newFLJK.php`，`param=industry`，一个完整 HTTP 响应。
- 人数：`http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount`，`node=label`。
- 分页：`http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData`，80 行一页；真实请求次数是一次人数加 `ceil(count/80)` 次分页，不将 SDK 调用次数当 HTTP 次数。

仅新增行业分支发出 `AKSHARE_<实际SDK版本>_SINA_INDUSTRY_ORIGINAL_COUNT_PAGES_V1`。当前适配登记接受 `AKSHARE_1.18.94_SINA_INDUSTRY_ORIGINAL_COUNT_PAGES_V1`；未来 SDK/字段契约变化不能自动获得来源权限。旧 Sina 概念分支仍发原 `AKSHARE_<version>_R03_V1`，其完整性逻辑不改。失败、重复或缺页的原响应保持归档，不补零；HTTP 预算、调用者 retry、缓存和真实 attempt 计数仍由原采集系统管理，本模块不新增请求。

`IndustrySinaSourceArtifactReader(receipts, paths)` 明确继承 `SourceArtifactReader`。`build_current_industry_context(cutoff=..., nodes=(...))` 或 `build_industry_context(reader, cutoff=..., nodes=(...))` 只展示调用者明确指定、且真实当前目录中存在的行业 node。必须先取得目录再选择 node；不根据“零售”名称或 SDK 文档示例猜实际 node。`nodes` 不能为空、重复或跨 `hangye_` namespace，单次最多 32 个明确节点；没有全行业自动采集或默认全行业主线功能。

新源验证包括完整 RAW SHA、严格拒绝重复 JSON key、已登记字段 schema、目录 label/name/人数唯一性、成员人数与全部 80 行分页、证券 code 与 `sh/sz` symbol 冲突、完整成分集合和四个实际 aware 时钟。成员必须保留沪深 A 股原代码；不静默过滤不支持或冲突证券来凑目录人数。目录和成员的 undated 报价、成交额、涨幅、换手等报价列不进入 `ThemeSnapshot` 或历史价格。

新来源 `coverage.sina_industry` 必须带 `SINA_INDUSTRY_ORIGINAL_COUNT_PAGES_V1` 原采集证明。目录记录单响应范围和行数。成员分别保留独立人数、要求/实际页码及每页实际行数；任何一页缺失、重复、错 node 或人数不一致均不可成为完整成员。worker 同时比较完整原页 code/symbol 序列与 SDK 返回成分，不以仅有 rows 或 status=COMPLETE 自称通过。

目录“公司家数”与 node 人数请求是两个不同原字段，分别保留来源；本版只在二者与实际全成员人数一致时认证该节点。若供应商给出不同定义或不同修订数量，返回冲突 UNKNOWN，不以较小值、较新值或静默过滤放宽等式。异常巨大人数原值与实际已取得页仍保留，但不据此分配无界页码列表；未认证页列表为 null，不能解释为零页。旧概念原件即使曾保存 COMPLETE/若干 HTTP，也不能升级为本版行业全页证明；新目录人数不能追认旧成员人数或修复旧窗口。

新的来源对象绑定全部收到的 receipts、lookup 和 paths；验证或节点子集不能删减注册来源，已登记但未请求的行业源同样做终态 SHA 检查。目录/成员只有 COMPLETE、pagination_complete 且四时钟不晚于 cutoff 才能提供成分。原 RAW `first_seen_at` 要与归档 receipt 同一 aware 时刻；新 current endpoint 的 event_time、publication alias 与 first_seen 相同，ingested 保持真实原值。缺失/晚捕获/PARTIAL 返回 UNKNOWN，不能解释为合法空行业或不存在主线。

行业快照 namespace 为 `industry_list_sina:<node>`，类型为供应商 `INDUSTRY_PROXY`。名字、成员、定义版本和父引用直接绑定一份目录加一份成员；四时钟逐项取两父最大值，`effective_from` 为真实成员捕获的北京时间业务日期，不回填旧交易日。`CURRENT_SINA_INDUSTRY_DIRECTORY_COUNT_AND_ORIGINAL_MEMBERS_V1` 不认证过去成分或经济题材完整性，也不授予证券资格、历史主板、限价安全或资金题材归因。

新 reader 的 `_validate_theme` 只处理两个新 dataset，其他概念仍调用 `super()` 的原验证。默认 `build_current_theme_context` 完全继承旧行为。原市场面板/主题市场包验证也沿用基类；新增 TEST_FIXTURE 行业来源不能用于生产覆盖类型。显式 `TEST_FIXTURE_SINA_INDUSTRY_V1` 只供隔离工程测试，输出标为 TEST_FIXTURE，正式 PIT 始终不可计数。

`source_identity()` 登记新模块、本规格与实际依赖源码 SHA；新来源身份随代码/规格变化，旧 run、来源 hash 和失败记录保留。constructor 必须拿到真实原件路径并在消费前后检查，保存的 context/reader JSON、source_hash 或 verified 标记不能恢复原生 capability。输出只有当前供应商成分事实、范围和 UNKNOWN，不是选股结果、资金净流入、交易信号或完整情绪周期。

工程测试仅使用隔离 TEST_FIXTURE 原件及明确临时目录。计划覆盖人数/目录名称/node/交易所冲突、原件篡改、重复 JSON、缺/重复页、晚时钟、PARTIAL、注册源子集削弱、保存 JSON 拒绝、历史回填拒绝，以及旧概念 `super()` 行为保持。实际免费网络采集必须另登记端点、次数、原件大小、时钟、独立截止和资源预算；本工程不执行它。
