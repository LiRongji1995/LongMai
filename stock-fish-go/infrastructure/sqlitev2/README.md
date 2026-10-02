# SQLite V2 权威存储

该模块只在新的隔离数据库写入。生产 SQLite 通过 `mode=ro` 读取；一致性来源通过 modernc SQLite 的 online backup API 创建，不复制活跃主文件。冻结库旁的 `*.snapshot.json` 记录完整文件 SHA256，每次恢复与验证重新核对。

## 模型与身份

`schema.sql` 为第 2 版 DDL，`schema_versions` 保存其 SHA256。`evidence_objects` 保存原始字节（可逆 deflate 压缩），`legacy_records` 保留 namespace、外部 ID、旧存储 hash 与创建时间。不同 receipt 在 `source_receipts` 中按快照及序号区分，不按 receipt_id 跨快照合并。

`history_rows` 将行情、资格、原始/可比价格、成交额、限价、四时钟和原始引用投影到列；金额与价格保持 TEXT 十进制精度，null 保持 SQL NULL。每行同时保留压缩原字节、原 SHA256、转换版本和独立投影 hash。所有历史候选修订继续存在。`partitions/partition_members` 使用整数内部键，外部行与分区 hash 不变。`snapshots/snapshot_members/snapshot_partitions` 保存冻结输入与成员关系。`legacy_result_headers/items` 提供轻量列表和按股详情映射，旧结果不按新策略重算。

旧 schema、配置、任务事件、其他非缓存表逐行保存原列值与独立 hash，在 `source_inventory` 登记去向。历史任务额外进入 `historical_jobs`，`executable=0` 由 CHECK 约束强制保证。旧 `yaogu_derived_features_v2` 明确丢弃并重建，数量单列，不计入原始事实迁移数量。新应用运行、结果、任务和事实缓存由同库 `app_*` 表承载。

## 执行与发布

```sh
stockfish-migrate inventory --source /private/production.sqlite3
stockfish-migrate snapshot --source /private/production.sqlite3 --target /new/private/frozen.sqlite3
stockfish-migrate migrate --source /new/private/frozen.sqlite3 --target /new/private/v2.sqlite3 --batch 1000
stockfish-migrate verify --source /new/private/frozen.sqlite3 --target /new/private/v2.sqlite3
stockfish-migrate publish --target /new/private/v2.sqlite3
```

每个批次同事务保存数据和稳定 rowid 检查点。成员映射在每个事务内复用 prepared statements 和有界分区 ID 缓存，仍逐条比较原始成员的 kind、证券和日期。新库在全部验证完成前处于 MIGRATING；验证结束原子写 VERIFIED 与摘要；publish 才写 PUBLISHED。重验先关闭发布状态，失败或取消不会继续宣称已发布；publish 核对实际 DDL 与完整验证摘要。应用就绪检查拒绝此前状态。`--max-batches` 可进行有记录的中断；相同冻结源再次 migrate 从已提交检查点恢复。源字节变化、同键异内容、未知 codec、缺引用、原字节损坏、投影篡改均报错。

验证包含源/目标双向键集合、完整原始 hash、全部类型化列重建比较、完整快照成员、所有分区内容 hash、来源列、压缩旧结果明细、旧结果 detail/history 引用闭合、所有非缓存表归档、foreign_key_check 和 integrity_check。必要完整性扫描不会因为查询计划出现 SCAN 而删除。

大表集合验收顺序读取紧凑身份，使用有硬上限的排序数组与重复位图精确比较。历史键保留完整 32 字节 SHA256、kind/证券/日期的无损 intern 编号；成员键由已核对的分区与历史身份编号构成。逐条匹配且无重复，再核对基数，证明双向集合完全相同，能够拒绝同数换键；不采用概率聚合 checksum。成员集合一致且行元数据一致后，用同一精确成员重建既有排序规则下的全部分区内容 hash。保守元数据预算不超过 384 MiB（另有 16 MiB 字符串上限、300 万历史键和800 万成员上限），超过即拒绝；payload 继续逐行解压校验。此设计避免 EXCEPT 大型临时排序及 hash 索引后反复随机读取大 payload 页。

`incremental --source NEW_FROZEN --target V2` 对完整内容集合对账，拒绝旧不可变记录消失或改变；相同 created_at 的晚到新事实不会遗漏。可变任务新增历史 revision，原状态不会覆盖。增量过程关闭发布边界，在 `incremental_epochs` 保存原验证与检查点；恢复必须仍使用相同新冻结源。验证并发布后仍只声称与该源快照一致，不声称追平实时生产。

## 资源与驱动

默认有界 1000 行写事务；单个大文档仍是最小原子对象。CLI 默认 command deadline 7200 秒；调用者还必须累计快照、迁移、验证与演练的总耗时，项目实测运行器保存在私有验收目录。CLI 设置 512 MiB Go 堆软目标，并采样实际 RSS，1 GiB 为进程限制；每秒检查至少 10 GiB 可用空间。冻结文件 SHA256 逐 1 MiB 检查 context。SQLite 连接使用 WAL、FULL、外键和有限 busy timeout。

锁定 modernc.org/sqlite v1.60.1，真实快照执行查询得到 SQLite 3.53.4，包含官方 WAL-reset 正确性修复；旧生产驱动未升级。许可见模块 LICENSES.md。Windows 实现 RSS 与磁盘 API；交叉编译不代表原生运行验证。

`Store.QueryPlans(ctx)` 返回证券窗口、会话横截面、完整历史窗口 join、类型化快照窗口、分区校验、结果列表与快照成员的实际 EXPLAIN QUERY PLAN；`Stats` 同时输出 dbstat 的页面、有效内容及空闲空间成本。

`Store.Quotes(ctx, QuoteWindow, consume)` 可直接读取十进制列，检查原字节 hash 与类型化列 hash，不解析完整行情 JSON。它依赖已发布全量验证和不可变表约束；能同时替换数据及校验值的恶意篡改需重新全量验证。历史策略桥仍验证并解码原证据行后交付共享合同，不能宣称研究已全程避免 JSON 解码。两端 `BarsWithIdentity` 单独交付 manifest parent、原字节 SHA256、locator 和逻辑字节数；原始身份不注入投影 Row。原字节 hash 与补默认值后的 Go RowHash 保持区分。

历史读取通过可选 observer 累计 SQL、原字节验证、解码、类型化投影校验、consume 与分区 hash 时间，使用每次窗口调用的本地计时，避免逐行锁。V2 原字节验证时间包含解压，旧适配器该阶段只包含 hash。新型写入与缓存、任务协议见 application/cache 模块。

## 冻结批次的类型投影扩展

`batch_projection*.go` 维护独立版本 `SQLITE_BATCH_EVIDENCE_PROJECTION_V1`，不改主 schema 或冻结输入模型版本。题材头/成员、资金精确 TEXT 字段及关系、receipt 时钟/来源范围/参数/重建合同均可按列查询。原始证据仍是权威；每条投影绑定 owner、父原字节 hash、成员原字节 hash、原 evidence_id 或 JSON pointer、转换版本与投影 hash。资金原开放合同中的无效可选值以 typed NULL 加 `INVALID_*` presence 标记保存，并保留原始字节，不把 UNKNOWN 改为合法空值。

每个 owner 一次有界事务，内部逐记录生成、验证和释放；最后写不可变 owner manifest。全库发布清单仅在所有现存 owner 齐备时追加。相同 owner 重跑先重建并核对，不覆盖旧列；中断保留此前完整 owner。`PutInput` 的输入、类型投影、成员清单和发布清单同事务提交。普通 readiness 只读取 owner 元数据及清单；深度 verify 从原始字节重建每条类型列和子关系。

```sh
work/stockfish database project-batches --target /path/to/private/v2.sqlite --budget-seconds 300
work/stockfish database verify-batches --target /path/to/private/v2.sqlite --budget-seconds 300
```

基础迁移必须先 VERIFIED。`database publish` 会回填并深度验证该扩展，再开放整个库的 PUBLISHED 门禁；旧的仅基础库完成标记不能绕过扩展 readiness。剩余累计迁移预算仍由本次任务账本约束，命令参数不代表新增授权。
