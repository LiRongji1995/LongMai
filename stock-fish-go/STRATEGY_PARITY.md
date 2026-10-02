# Go R1 / V2 语义迁移矩阵

本文件记录 2026-10-02 原生 Go 实现与冻结 Python 研究实现的对应关系。工程实现、数据完整性与投资验证分别判定：`STAGE_AWARE_RESEARCH_R1` 为 `RESEARCH_ONLY`，`production_promotion=DISABLED`；`STAGE_AWARE_MOMENTUM_V2` 保持独立策略身份。参数仍为“尚未验证”，`MARKET_EDGE=NOT_ESTABLISHED`。没有改写旧 run、旧配置、旧源码身份或把 R1 输出改名为 V2。

## 权威输入与调用边界

原语义来源为 `../stock-fish/stockfish_core/yaogu/research_r1.py`、`stage_aware_momentum.py`、`research_r1_contracts.py` 及其委托的共同价格/证据函数；参数来自 `../stock-fish/config/stage_aware_research_r1.json` 与 `stage_aware_momentum_v2.yaml`。R1 配置为 version 1；V2 配置为 version 2、`R12_INTEGRITY_HISTORY`。Go manifest 记录实际配置哈希、Go 源码哈希、冻结输入原字节哈希、历史身份及 cutoff；旧 Python 身份作为 oracle/历史身份保留，不伪装成 Go 源码身份。

`application/research.AnalyzeStrategyFrozen` 通过 `HistoryReader`、预算接口和可选 `FactsCache` 读取事实，只执行指定 R1 或 V2 策略。`AnalyzeFrozen` 是 R1 兼容入口。`facts` 不导入任一最终策略；`strategies/shared` 只实现共同 setup 与背景兼容规则；`strategies/r1` 和 `strategies/v2` 独立消费事实，V2 不导入 R1。`core` 仅作旧入口/测试兼容。Go 执行、归档导入和新库读取不调用 Python；Python 仅用于离线 oracle 与差分工具。

## 事实、背景与证据

| 原 Python 语义 | Go 实现 | 保留的边界 |
| --- | --- | --- |
| security eligibility / revision resolver | `evidence`、`domain/contracts.Qualification` | 身份、交易日、父来源可见时间先于业务字段。缺上市日不能推定满 60 会话；未知状态不能当 FALSE。原字节及投影验证详见 `EVIDENCE_CONTRACT.md`。 |
| `_price_return`, `_return_map`, `_liquidity`, `_ma_snapshot` | `facts/facts.go`, `build.go` | 1/3/5/10/20 会话收益；MA 5/10/20/60/120/250；RAW 比较基础；缺会话、缺字段、基础不兼容分别保留 reason。数值使用精确有理数。 |
| `_market_benchmarks`, `_relative_strength`, percentile midpoint | `facts/facts.go` 与应用共享截面 | 合资格母池、每窗口有效分母、最低 60% coverage；中点秩保留 ties。一次排序与 lookup 替代逐股重扫。 |
| `_daily_path`, `_strength_momentum` | `facts/build.go` | CLV、振幅、低点/收盘收益、恢复幅度；最近与前 3 会话同窗收益/RS、成交额及换手变化；稳定带 ±0.001；不事后挑窗。 |
| `_trend` | `facts/facts.go` | 长短均线排列、长均线非负斜率、跌破 MA5 1–3 日及贴线距离；长窗不足 UNKNOWN；MA20 当前值与前值另存供 R1。 |
| `_market_day_summary`, `_market_position_context`, `_market_strong_cohort_feedback` | `facts/background.go` | 昨今限价事件覆盖、20 会话连板高度、宽度、状态转移、同一强股队列反馈；EXPANSION/DIVERGENCE/REPAIR/RETREAT/UNKNOWN 不按输入标签硬写。 |
| `_supply_pressure` | `facts/background.go` | 最近 5 会话上影线、回补、回补后失败与未解决压力；影线比例 ≥0.50，未收复 ≥2 触发警示。缺价格保留 UNKNOWN。 |
| `_visible_stage_themes`, `_sector_window_stats`, `_sector_stats_for_theme` | `facts/background.go`, `sector.go` | EVENT/INDUSTRY、证据和 cutoff、版本选择、peer 排除自身、1/3/5 窗、至少 2 个有效其他成员且 ≥60% coverage。共同和仅减去实际存在的自身值；同窗匹配 momentum；中位数排除自身与 ties 均保留。 |
| sector level / breadth / role / `_sector_setup_compatibility` | `facts/sector.go`, `strategies/shared/setup.go` | 高/低位置、相对水平、强化/稳定/弱化、扩散及 leader/core/follower 分开；setup 兼容不混入公共事实计算。 |
| `_visible_theme_funds`, `_dedupe_funds`, `_funds_completeness_for_theme`, `_grade_funds` | `facts/funds.go` | 分类席位与 proxy 分开；排除自身；5 会话窗口；父证据可见性；重复键冲突、重叠窗口为 UNKNOWN；金额 Decimal token 的标度身份保留；完整无记录与未查询不同。 |
| `_funds_for_theme`, `_funds`, `theme_gate` | `facts/funds.go`, `strategies/shared/context.go` | STRONG：≥2股/2席、买入≥1亿、净额正且最近≤3会话；MODERATE：≥1股/1席、买入≥2000万、净额正且最近≤5；分类证据不足门槛为 WEAK，proxy-only 为 UNKNOWN。同主题绑定，不跨主题拼接。 |

现有 `selected_evidence_hashes` 是 `RowHash` 的规范模型投影身份，并非存储原字节 SHA-256；不能由二者相等推断原字节一致。原字节完整性由 adapter 验证；`selected_evidence_bindings` 旁路保存选中投影到原字节的绑定，不向模型 Row 注入元数据。历史绑定为 HISTORY_ROW + history manifest + raw SHA；内联绑定为 FROZEN_INPUT + input bytes hash + 原始 JSON pointer。修订选择返回index，从并行来源数组取同一行身份，缓存命中后重新装绑定。保存时按manifest明确的同索引合同共享parent及projection身份，仅在raw SHA与projection确切相等时使用 `raw_sha256_is_projection=true`，否则显式保留raw SHA。

公共事实缓存只缓存 `BuildFacts` 输出。必须先完成原字节/投影验证、来源解析与修订选择再 lookup；key 包括计算版本、冻结输入字节、history、观察日/cutoff、证券、会话窗口、已选证据 hashes 和证据政策。市场/题材截面每次重新计算；暖缓存不能跳过完整性校验。

## 独立策略矩阵

| 层/条件 | R1_FULL | V2_CONTROL / 原生 V2 |
| --- | --- | --- |
| 公共门槛 | 成交额 ≥5亿；收盘跌停 TRUE 为 FAIL、UNKNOWN 为 UNKNOWN；取消统一当日换手门槛 | 成交额 ≥5亿且当日换手 ≥7%；保留 V2 liquidity/trend gates |
| START | 复用冻结共同公式：前 5 日收益 ≤8%、高换手 ≤2日、突破前5日收盘高、量比 ≥1.2、ret1/RS1/RS momentum 正、strength rising | 相同原始 setup 公式 |
| ACCELERATION | `(ret5≥15% OR ret10≥25%) AND RS5>0`，ret1/ret3/RS1/RS3>0、strength rising、含 T 的5日高换手≥3 | 原 V2 HIGH 水平保持 `ret5≥15% OR ret10≥25% OR RS5>0`；不套用 R1 合取重建 |
| HIGH_LEVEL_REPAIR | 原 high-background、divergence、repair 与日内弱转强完整合取；单独 MA20 setup trend | 相同原始 repair setup，行为确认、统一原趋势/市场/同主题/反证仍参与 V2 决策 |
| TREND_PULLBACK | 原均线贴近≤3%、跌破MA5 1–3日、缩量≤0.90、韧性等；保留原强趋势要求 | 同共同 setup，保留 V2 最终 hard gates |
| setup trend | START/ACCELERATION/HIGH_LEVEL_REPAIR：收盘严格高于MA20且MA20非下降；pullback仍用强趋势 | 全部使用原 V2 `trend_state` |
| 市场/资金/题材/压力 | R1_FULL 配置下为背景标签，仍完整计算并保留状态/reasons/证据；非 hard gate | 市场 compatibility、同主题 sector+funds 与关键反证参与最终条件 |
| 多形态与排序 | 所有 setup 独立输出，不因 display_order 删去其他已满足形态；primary 为显示用 | 保留 primary/alternate、FOCUS/WATCH/REJECTED/UNKNOWN、弱化观察桶；保存 setup rank features，仅同 setup 排名，不生成全局投资分 |
| 三态组合 | AND 中已知 FAIL 可以短路 UNKNOWN；OR 中已知 PASS 可以短路 UNKNOWN；缺失不是 FALSE | 相同真值代数，保留阶段条件 reason 与 final/watch reasons |

数值/阈值判断采用精确分数。Python 保存结果仅公开已四舍五入字段时，差分仅按原字段规定小数位执行 `ROUND_HALF_EVEN`（收益百分比4位、金额2位、多数比率6位），不使用 epsilon 掩盖偏差。

## 覆盖与共享输出

`application/research/coverage.go` 独立记录 universe completeness、core facts 可判定性、selection truth/decidability、背景完整性与候选是否存在。R1 只扫描原背景合同字段；V2 背景覆盖取 sector_context/capital_linkage 原 summary conditions，不能用 market_compatibility/same_theme 代替。已知失败可以使决策可判定，背景仍可能不完整。无候选不代表数据缺失，也不代表整个母池完整。

原生存储每证券保存事实、选定策略决策和 support；`shared_market_ref=summary.market_context` 将全市场背景保存一次。`tools/compare_saved.py` 明确解析这个引用，引用缺失/未知路径失败。不能因为背景被共享就省略比较。`SUCCEEDED` 只表示运行完成；`DIFFERENTIAL_ACCEPTANCE_REQUIRED` 不自动升级为完全验收。

## 已执行的定向证据

14 个独立 `TEST_FIXTURE`：repair、weakening、pullback、low_amount、missing_price、funds_missing、funds_proxy、funds_future、funds_conflict、funds_complete_empty、sector_negative、repeated_supply、close_limit_down、theme_missing。oracle 保留 Python 完整原结果与明确比较投影。最后一次使用 `TEST_FIXTURE-oracle-full-v3`；v3 在 v2 14例结果上增加原 coverage 合同计算，没有再跑真实策略。

执行命令（工作区根目录；路径通过当前 `$PWD`）：

```sh
STOCKFISH_R1_ORACLE="$PWD/reports/codex/stockfish-v2-rebuild/TEST_FIXTURE-oracle-full-v3" STOCKFISH_FORMAL_ACCEPTANCE=1 work/r1-go-tools/go/bin/go -C stock-fish-go test ./core ./facts ./strategies/... ./application/research -count=1
```

退出 0；日志 `reports/codex/stockfish-v2-rebuild/strategy-final-test.log`。核心差分测试 118.820s。正式模式无 oracle 明确失败，不静默 skip。另有针对性测试验证缺/非法 MA 字段、非法 OHLC、Decimal scale 去重冲突、均值自身排除、ties 中位数、已知失败/未知背景、R1/V2 加速差异与取消。

| 比较层 | 已检查内容 | 边界 |
| --- | --- | --- |
| 原始资格/窗口 | 合资格名单、选中规范模型行哈希、全部公开收益/RS、流动性及关键MA | 14例为人工构造合成数据，不能代表真实母池覆盖 |
| R1 决策树 | 所有 setup、common、子条件状态/reason/value/threshold、matched/primary/status | Go 与 Python JSON 字段布局不同；并非整份 DAG 字节相等 |
| 背景 | 市场状态/限价事实、供给压力/趋势、sector身份/状态/role/1-3-5窗数值与兼容、funds状态/grade；完整覆盖合同 | 定向 fixtures 未穷尽所有历史修订/市场状态组合 |
| V2 | 所有 setup、有效决策条件状态与reasons、primary/alternate/final/rank、coverage | 全字段原始行字节证据链同时依赖 evidence 合同测试；投资效果未验证 |

合成算法对照 `algorithm-benchmarks.log`：3500 股中点秩逐股重扫约 2.203s，预计算约 0.00963s（约229倍）；500 peer重复扫和约0.249s，共享和约0.00563s（约44倍）。这是受控算法基准；分配字节为累计 alloc，并非RSS。不能据此声称真实整管线加速；全池旧/新 cold/warm 指标由根验收账本记录。

## 真实原 run 与离线语义比较

已只读检查 `reports/codex/r1-next-20261002-repair/run-optimized` 同时保存 R1_FULL 与 V2_CONTROL。原 run_id `r1_c8b5bcad861cb2b4deba15f161c91b3c6346306e44237dbc2c2b0ec0dab64918`；共享结果包含 79,531 个对象、23 个 shard。不能混用相邻 run/selection-api 的身份。

`tools/compare_saved.py` 先验证原 completion、manifest/result canonical hashes 和全部共享 shard/object 引用；支持 `--go-db ... --go-run-id ...` 只读逐 item gzip读取，校验 Go header、每条原字节哈希、排序/成员哈希；也支持历史 diagnostic 目录和应用 export。以同一冻结输入字节/cutoff比较全母池 facts、setup条件树、背景及 coverage；差异必须退出 1。成功标记为 `PASS_CHECKED_SEMANTIC_PROJECTION`，诚实标注原保存结果无法恢复的“每个历史 bar 原字节绑定”需要 fixture模型选择 oracle 与旧/新存储一致性验证补足。本子任务未擅自执行真实全池策略；真实比较结果以根验收产物为准。

## 旧 R1 bundle 入新库

`infrastructure/sqlitev2/bundle.go` 提供 `ImportBundle/VerifyBundle/ExportBundle`。原 run_id 原样映射；manifest/result/completion/report/index/shard 原字节各自存储，另存 SHA-256、canonical identity 与 object→shard索引。逐 shard 上限64MiB，事务可恢复；所有原字节、canonical哈希、引用闭合和root身份校验后最后发布。半途导入不能查询为已发布；相同源可继续，变化源拒绝。导出只到新目录，completion 最后写，禁止覆盖旧输出。附加 bundle schema独立校验版本/checksum，不修改原表定义。

独立 DB 测试覆盖 indexed 与早期逐对象布局逐字节往返、重复导入、注入中断/恢复、源变化拒绝、缺引用/损坏BLOB拒绝、未知合同/计数不符、取消、symlink拒绝与已发布不可变。命令 `go test ./infrastructure/sqlitev2 -run TestBundle -count=1`，结果见 `bundle-test.log`。真实 bundle 导入/验证/导出由根统一串行运行，并记录实际退出码与资源预算。

保存格式适配另以 repair 的8股 `TEST_FIXTURE` 运行真实 NativeEngine，分别生成 R1/V2 应用导出，原 Python 已保存fixture仅重新包装共享DAG、未重跑策略。`compare_saved.py --go-export` 两策略均0差异，`--go-db` 对隔离逐item存储probe为0差异；`compare_go_runs.py` 对backend/cache统计不同而业务内容相同的fixture比较10,021个值、0差异。记录在 `reports/codex/stockfish-v2-rebuild/TEST_FIXTURE-saved-probe`。`tools/test_saved_go.py` 四项测试退出0，覆盖应用导出 `detail` 字段、成员hash、原字节hash损坏拒绝和只读路径不创建数据库。


新增选中证据绑定测试：`infrastructure/sqlitev2/history_identity_test.go` 在独立旧库中存入故意非canonical（空白、+0010.00、5e8、缺默认字段）的原字节，经实际snapshot/migrate/verify/publish、旧/新adapter和NativeEngine选择，验证原字节身份与模型投影不同、compact binding可还原、两存储与cold/warm完整item逐字节一致。暖cache故意携带旧binding也必须重新装填；缺认证HistoryIdentityReader拒绝；内联 `/input/bars/0` 从已认证父输入可重新投影为选中模型hash。定向sqlitev2命令退出0（1.791s，`binding-test.log`）；native compact原字节相等/不等/缺绑定测试及evidence revision-index/同钟冲突测试退出0。

内存审阅：RawBytes是计数，没有把原payload缓冲放入binding；历史原字节在逐行验证后可回收。来源数组与seenRaw按证券限定。内部所有股票的完整bindings在事实完成前仍累积；百万行可带来约200MiB量级额外身份对象和字符串，实际RSS不能由此静态估算替代。序列化按股清除事实/决策、V2索引逐项删除，输出采用共享父身份和同索引投影以避免重复三遍hash；最终items仍累积JSON。真实全池首轮必须依据阶段RSS实测，fixture通过不能声称内存上限已覆盖真实母池。
