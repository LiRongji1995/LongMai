# 历史 factor 续传来源链研究桥 V1

本模块显式选择 `R12_DATED_REGISTERED_IDENTITY_FACTOR_LINEAGE_TURNOVER_RECONSTRUCTION_V1`，类为 `HistoricalReplayFactorLineageTurnoverAssembler`。默认入口没有改动。冻结历史 assembler、turnover V1 adapter、RAW×backFactor、证券资格及百分比换手函数保持原样。

调用者提供活动归档目录、原 source policy 及 `AncestorCapture(capture_root, result_path)` 元组。祖先必须指向真实独立 request archive wrapper 和其完整 RAW/receipt，不能传私有报告、cache checkpoint、模型输入或恢复旧 capability。文件名、receipt/request 自哈希、完整 RAW SHA、三父来源协议、字段版本、implementation hash、请求日期/code 范围、分页、可空原行数及真实 aware wrapper/receipt 时间都重新认证。每文件最多 8 MiB，读取前后流式 SHA 和 stat 一致。

final 三件必须 COMPLETE、分页完成、逐股状态覆盖原请求，final RAW.first_seen 与原 receipt.first_seen 的原 V1 严格门禁仍有效。祖先三件保持 PARTIAL；祖先 RAW.first_seen 缺失记为 `UNKNOWN_MISSING_RAW_FIRST_SEEN_AT`，实际 receipt 归档时钟仅为 `ACTUAL_ARCHIVE_PROXY_NOT_SOURCE_PUBLICATION_OR_PIT`。不回填旧原件字段。

final 中某 code 的捕获时刻位于 final wrapper 外时，只能由相同 provider/完整参数/字段版本的较早原 PARTIAL 三件证明：原逐股 COMPLETE/cursor/row_count/captured_at 状态必须全值相同、所有 factor rows 多重集合必须全值相同、捕获时刻确实位于该祖先 wrapper 区间，祖先完成不晚于 final 开始。存在匹配不代表原 PARTIAL batch 完整，也不消除原 final 的 out-of-parent gap。

通过验证的逐股 fragment 新发行当前实际验证时刻的来源凭证。发行时刻不得早于任何 final/ancestor 真实归档完成时刻。新凭证不带 HistoricalTimeProof，源引用保留原 final 和原 PARTIAL 父三件全 SHA、原状态与时钟角色。祖先归档父凭证仍为 PARTIAL，原 null receipt.row_count 另列，模型接口的行数为实际 RAW 长度。全部 bar（包括 lineage UNKNOWN）引用当前实际验证凭证；它的 COMPLETE 仅表示协议验证完成，不宣称 factor 完整。调用者的研究截止必须晚于 `factor_lineage.issued_at`；该版本不能授权 4 月当时可见性、PIT 或恢复旧 run 的能力。

没有真实祖先证明的股票保留原始价格、成交额、换手及原资格池，只有 comparable OHLC/basis 置为 UNKNOWN（None）并明确 PARTIAL reason。原复权左锚、日期覆盖、RAW 停牌/缺价及资格未知继续由原函数处理，不填 0、不填 factor=1、不删除 400 股。多个源并存时不挑后验最有利证明。

接线是显式新版本研究 worker 先构建、再用晚于实际发行时刻的研究 cutoff 调用现有原生消费者；源能力重新从原件发行。持久 history store 在此版本拒绝；新 source issuer/native 三窗口运行需单独登记，工程测试不授权运行。

工程验证仅使用独立 `TEST_FIXTURE` 临时归档，覆盖真实格式的本地正/负契约，不把测试来源当真实市场证据。该修复不增加 selector、系数、阈值、资金净流入或预测结论。`MARKET_EDGE=NOT_ESTABLISHED`。
