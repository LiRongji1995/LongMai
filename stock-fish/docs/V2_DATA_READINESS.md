# V2 Data Readiness — R12 最终来源续采后（2026-09-08）

**17:13 真实采集作业 SUCCEEDED，但 consumer quality=UNAVAILABLE；17:15 最新 20 日重构仍为 20 尝试 / 1 构造 / 0 可评价。** 这是 `ON_DEMAND_NOT_OFFICIAL_PRIMARY`，不代表真实 20:30 primary 已存在。`MARKET_EDGE=NOT_ESTABLISHED`。

本轮继续使用现有 provider cache 与同一权威 DB，未扩大 128 请求 / 600 秒采集 admission 预算。实际发起 98 个 adapter task、74 个 HTTP admission、179 个 SDK operation，cache hits=57。完成含 normalize、consumer、持久化的整条作业耗时 **767.454 s**；600 秒是采集 admission 预算，不能把它描述为整个作业的硬耗时上限。

| 最新数据/内存口径 | 实际结果 |
|---|---|
| V2 60 日供应目标 / 单信号暖机 | 315 / 255 交易日 |
| 9/8 暖机非空 RAW 会话 | 67/255；成功回执 68，其中 1 日空 |
| 暖机剩余缺口 | 187 日无成功回执＋1 日空回执 |
| 9/8 dated master / RAW | 回执存在，均 available_nonempty=false |
| MA20 / MA60 / MA120 / MA250 | 均 observed=0、evaluable denominator=0、coverage=null、UNKNOWN |
| 最近 20 日 PRICE_TREND / FULL_CONTEXT 可评价日 | 0 / 0 |
| 最新 prospective capture daily input / manifest | 10,163,247 / 210,254 bytes |
| 最新 reconstructed input / manifest | 1,882,171 / 165,327 bytes |
| 真实完整 capture peak RSS | **923,172,864 bytes**（约 923 MB / 880 MiB） |
| 真实完整 capture OS peak working set | 923,463,680 bytes |

真实 capture 峰值明显高于单独流式迁移的 130,613,248 bytes，也高于隔离合成 artifact benchmark 的 159,555,584 bytes。它包含当前 provider 结果、raw-normalization 索引、consumer 与保存过程；不能用 131 MB 或 160 MB 代表全市场真实采集。仍存在原始结果与 normalization 索引的内存成本，尚未宣称端到端常量内存。详细分层测量见 [MEMORY_PROFILE](MEMORY_PROFILE.md)。

### 全局队列最终快照

| 状态 | 原始存储行 | 当前有效逻辑请求 |
|---|---:|---:|
| COMPLETE | 103 | 98 |
| PARTIAL | 4 | 2 |
| PENDING | 1,097 | 921 |
| BLOCKED | 2 | 2 |
| 总数 | 1,206 | 1,023 |

183 条旧重复行保持审计，存储行总数没有因本轮重复 capture 增长；逻辑 COMPLETE 从迁移后的 59 增至 98，PENDING 从 963 降至 921。即使通过了某次传输，数据仍须通过日期、非空 scope、可比基准和 PIT consumer 的独立验证。最终来源缺口及研究 input identity 见 [Historical Replay Report](V2_HISTORICAL_REPLAY_REPORT.md)。

最新通过项目入口保存的 capture 输入为 `market_e642f51a9803358574ffde5f809b0b09e93f08e4c5e7500ef4dbae8174225b5c`，history hash 为 `6ee1bba28331479d66a4dd243148f0cdf8a75a687e3080394b8a5b9dfe2862ff`；该输入不表示 official primary 可计数。仓库根目录本机证据：`reports/codex/2026-09-08/r12_integrity_history/live-capture/capture-report.json` 和 `replay-after-capture/V2_HISTORICAL_REPLAY_REPORT.json`。

## 16:36 及首次迁移快照（保留历史上下文，以下不代表最终来源状态）


R12 已完成 history artifact 架构及逻辑请求去重；**数据就绪仍未成立**。截至 `2026-09-08 16:36 Asia/Shanghai` 的归档来源快照，最近 20 日重构尝试为 **20 尝试 / 1 构造 / 0 可评价**。缺完整 dated universe、连续价格窗口及可比基准；不能把 eligible=0 显示成策略合法无推荐。`MARKET_EDGE=NOT_ESTABLISHED`。

## 可评价覆盖与具体缺口

| 数据族 / 范围 | 真实覆盖 | 状态 |
|---|---|---|
| 最近 20 日对应日期 master | 1 日成功回执，但该日为 0 行；其余 19 日缺失 | `INSUFFICIENT_IDENTITY` |
| 最近 20 日对应日期 RAW daily | 3/20 日有成功回执，其中 9/8 零行；仅 2 日非空 | 17 日无成功回执＋1 日空回执 |
| 9/8 V2 趋势暖机 RAW | 27/255 日有成功回执，其中 1 日零行；26 日非空 | 228 日无成功回执＋1 日空回执 |
| MA20 | 0 observed / 0 evaluable denominator；比例 null | `UNKNOWN` |
| MA60 | 0 observed / 0 evaluable denominator；比例 null | `UNKNOWN` |
| MA120 | 0 observed / 0 evaluable denominator；比例 null | `UNKNOWN` |
| MA250 与长均线斜率 | 0 observed / 0 evaluable denominator；比例 null | `UNKNOWN` |
| 原始上市日与类型佐证 | 4,600 份一致事实 | 不替代历史 dated membership |
| 可比价格 | 重构审计有 1,501 个 `NO_COMPARABLE_SOURCE` 缺口 | `UNKNOWN` |
| PRICE_TREND_REPLAY_SCOPE | 0/20 可评价日 | 来源合同未满足 |
| FULL_CONTEXT_REPLAY_SCOPE | 0/20 可评价日 | Context 独立保持未知 |

原 replay JSON 的 `captured_core_price_sessions=27` 是成功回执数量；本页再次检查回执 rows，明确排除了 9/8 零行。原统计未因此被删除或改写。

V2 60 日回放供应目标仍需 315 个交易日；单个信号的最长暖机为 255 日。指标按实际证券、交易日连续窗口和 comparable basis 核验，不能以“已 enqueue 315 日”证明数据齐备。0/0 是不可评价分母，不是 0% 覆盖率。详情和 source-contract blocker 见 [Historical Replay Report](V2_HISTORICAL_REPLAY_REPORT.md)。

## 长历史存储的真实测量

同一现有 DB 保存 immutable history rows、session partitions 和 content-addressed manifest；daily MarketInput 引用历史 hash，并保留近期 PIT evidence。历史身份、provider/source refs、复权基准和四时钟不因迁移变化。

| 实际旧输入迁移指标 | 数值 |
|---|---:|
| 原 R11 MarketInput bytes | 321,622,653 |
| 原始 bar / security rows | 150,626 / 155,226 |
| 迁移后 daily MarketInput bytes | 10,018,721 |
| 首次历史行 payload bytes | 234,077,094 |
| 首次迁移新增不可变行 | 305,852 |
| 迁移 peak RSS bytes | 130,613,248 |
| 64 KiB 流式迁移耗时 | 25.146 s |

此迁移保留原 `PROSPECTIVE_CAPTURED` 身份，并没有回溯运行信号；不得用工程迁移宣称新的正式信号存在。测量来自仓库根目录本机 `reports/codex/2026-09-08/r12_integrity_history/real-migration/migration-report.json`。旧不可变输入仍保留，所以总磁盘不会因迁移立即减少；以上历史行 bytes 不含 SQLite 索引/WAL 开销。

另一次真实历史重构的 daily input 为 1,772,086 bytes，manifest 为 69,592 bytes；它与上述旧输入覆盖范围不同，不能直接作为同输入前后性能对比。512证券×315日的隔离合成前后内存/增量对比见 [MEMORY_PROFILE](MEMORY_PROFILE.md)，合成工程测量不混入真实数据覆盖。

## 请求去重：实际迁移结果

| 口径 | 迁移前存储行 | 迁移后有效逻辑请求 |
|---|---:|---:|
| 总数 | 1,206 | 1,023 |
| COMPLETE | 64 | 59 |
| PARTIAL | 3 | 1 |
| PENDING | 1,139 | 963 |

183 条既有重复行及所有历史 attempt/raw bytes 保留审计，逻辑 alias 复用已有结果；迁移没有网络调用，存储行总数仍为 1,206。完成数减少反映去重口径变化，不是删除成功证据或抓取退步。迁移时未 enqueue 新 capture，不能把这个数量当成后续采集成果。

重复根因为当前端点把小时放进 cache identity。`daily_limits_eastmoney` 原 320 行对应 160 个同日逻辑请求；`mainboard_sse` 和 `master_szse` 各 3→1；`master_all` 与 `market_daily` 各 317→315。R12 身份绑定 provider＋endpoint＋规范参数＋业务 session，日期格式和证券批次顺序不再生成额外 pending，definition version 改变仍隔离来源合同。

本机证据入口：业务目录 `reports/codex/2026-09-08/r12_integrity_history/request_queue_migration.json`。统计区分 stored rows、active logical requests 与本轮 selected demand，不能混用。

## 优先级、最终行情刷新与预算

采集执行顺序为 P0 当前身份/价格、P1 核心历史价格、P2 复权可比依据、P3 限价事件、P4 题材、P5 可选资金/补充。价格和日期化身份先于主题目录或逐股限价请求；已发布候选的 outcome demand 仍保存，不因退出当前名单而丢弃。

预算保持每轮最多 128 adapter safety admissions / HTTP admissions、600 秒，单调用最多 40 秒、普通失败最多两次。SDK operations 另计，不能把 task count 当作实际 HTTP 次数。有限预算用尽后续采同一逻辑请求。

零行核心行情/身份回执不得冻结为可评价 COMPLETE；R12 将其标为 `SOURCE_SCOPE_EMPTY`，后续显式 capture 在同一 request ID 上尝试。收盘前取得的非空当日行情也不能永久充当最终行情：BaoStock daily/master 的 source release 检查使用 17:30；spot/限价使用 15:00。超过对应 release clock 后，旧 first_seen 触发 `PRE_FINAL_CAPTURE` 版本刷新，原 attempt 和真实四时钟保留。没有伪造 20:30 的证据可见性。

已归档 BaoStock 文档说明：日 K 在当日 17:30 入库、复权因子在 18:00 入库。因此北京时间 16:50 虽已收盘，不能仅按“已过15点”断言 BaoStock 当日 dated master/RAW 已最终可用。17:30/18:00 之后的有界续采可以尝试补齐；是否成功必须检查实际非空回执、业务日、source clocks 和 consumer status。UI/API job 成功不等于数据就绪或正式可计数信号。

后续采集使用同一正式 API `POST /api/candidate-data-pipeline/capture`，空 JSON body；服务器决定真实时间、观察日和预算。重构始终保持 `RECONSTRUCTED_HISTORICAL`，不能进入正式 prospective 分母。

## R11 历史覆盖快照（只作前轮上下文）

下表是原 12:23 输入的旧覆盖，不是 R12 新采集或信号运行。R12 的当前事实以上述来源快照及真实迁移报告为准。


- input_hash: `b7bfe39df96b574b357348f3a506ea1e4edfa519a7053103976c132be6ff12c1`
- observation_date: `2026-09-08`
- coverage_type: `PROSPECTIVE_CAPTURED`
- required_history_sessions_for_v2_60_session_replay: `315`
- MARKET_EDGE: `NOT_ESTABLISHED`

| 数据族 | 覆盖 | 状态 | 缺口 |
|---|---:|---|---|
| MA5 | 0/0 (None) | UNKNOWN |  |
| MA10 | 0/0 (None) | UNKNOWN |  |
| MA20 | 0/0 (None) | UNKNOWN |  |
| MA60 | 0/0 (None) | UNKNOWN |  |
| MA120 | 0/0 (None) | UNKNOWN |  |
| MA250 | 0/0 (None) | UNKNOWN |  |
| MA_SLOPE_LONG | 0/0 (None) | UNKNOWN |  |
| RET1 | 0/0 (None) | UNKNOWN |  |
| RET3 | 0/0 (None) | UNKNOWN |  |
| RET5 | 0/0 (None) | UNKNOWN |  |
| RET10 | 0/0 (None) | UNKNOWN |  |
| RET20 | 0/0 (None) | UNKNOWN |  |
| RS_MARKET_1 | 0/0 (None) | UNKNOWN |  |
| RS_MARKET_3 | 0/0 (None) | UNKNOWN |  |
| RS_MARKET_5 | 0/0 (None) | UNKNOWN |  |
| RS_MARKET_10 | 0/0 (None) | UNKNOWN |  |
| LIMIT_EVENTS_CURRENT_SESSION | 0/0 (None) | UNKNOWN |  |
| PIT_THEME_LINKAGE | 0/0 (None) | UNKNOWN |  |
| SECTOR_PEER_CONTEXT | 0/0 (None) | UNKNOWN | UNKNOWN_OR_FAIL_COUNT=0 |
| CAPITAL_LHB_OR_CLASSIFIED_FUNDS | 0/0 (None) | UNKNOWN | UNKNOWN_OR_FAIL_COUNT=0 |
| AUCTION_FINAL_0925 | 0/0 (None) | UNKNOWN | SAM_AUCTION_PHASES_NOT_IN_MARKET_INPUT |
| INTRADAY_TIMESTAMPS | 0/0 (None) | UNKNOWN | SAM_INTRADAY_TIMESTAMPS_NOT_IN_MARKET_INPUT |
