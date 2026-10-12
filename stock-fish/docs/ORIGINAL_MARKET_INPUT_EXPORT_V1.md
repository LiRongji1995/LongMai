# 已登记市场输入原字节导出 V1

显式合同 `ORIGINAL_MARKET_INPUT_EXPORT_V1`，特征 `EXACT_REGISTERED_SQLITE_INPUT_BYTES_READ_ONLY_V1`。它补齐当日保存交付协调器取得精确原 input 的本地只读路径。只选择已有权威库和一个完整 `market_<64位小写SHA256>`；无最新输入回退、不采集、不运行 selector、不建库，不修改旧 input/run 或服务路由。

从业务目录使用现有健康项目 Python：

```sh
.venv/bin/python -B -m stockfish_core.yaogu.original_market_input_export_v1 \
  --database EXISTING_AUTHORITY_DB --input-id EXACT_REGISTERED_INPUT_ID \
  --output NEW_PRIVATE_DIRECTORY
```

输出 `input.json` 是原 `candidate_inputs` 文档的完整 UTF-8 字节；SHA 必须等于原 ID 后缀及该行保存的 content_hash。随后可以原路径及 SHA 传入 [当日交付协调 V1](SAVED_R2_DAILY_COORDINATION_V1.md) 的 `--input` / `--input-sha256`。原 run 仍须通过原无筛选 JSON 导出取得，不能用 summary 或筛选 view 重拼原件。导出不修改原 content_hash，也不把导出生成时点当历史盘前可见时间。

读取采用既有 `ReadOnlySQLiteStore` 的 SQLite URI `mode=ro` 和 `query_only`，同一只读事务中按精确 namespace/ID 查询 metadata，并用只读 `blobopen` 每块最多 64KiB 读原 payload。先用 `len(blob)` 核原字节长度，不以 SQL CAST/json_extract 在限额判断前展开巨大 JSON。关闭事务后再流式核输出 SHA/完整指纹。不会构造 CandidateService、执行迁移、重建索引、注册配置、修改调度状态或复制数据库；不用 `immutable=1`，已提交 WAL 输入可见。

独立预算为 120 秒、输入单件 256MiB、OS peak RSS 256MiB；累计原件和输出核验返回量最多输入上限两倍，终态回执读取另限固定 16KiB 控制额度。SQLite 页读取及物理磁盘 IO 未测量。输出只保留一份 input 和固定 16KiB 回执；控制路径被替换而不能安全封存时，另用最多一份固定 16KiB FAILED 记录。输出目录必须已存在父目录下的新目录，目标原子发布不覆盖；写入原 partial 的 fd/inode 与完整指纹固定，发布后核 regular 类型和同 inode，输出及回执重读使用 `O_NOFOLLOW | O_NONBLOCK` 后核 fstat，拒绝替换 symlink、FIFO 或外部同字节 hardlink。回执固定 inode，封存不覆盖被换路径。最后回执 SHA/完整指纹、输出指纹和资源边界再核；最终 seal 后超时仍为 FAILED。失败保留原 partial/输出和记录，不自动重试、扩大预算或删除现场。

回执记录原 created_at、本次真实导出时间、原地址、实际返回量与完整文件指纹。数据库 dev/ino/mode 固定；数据库 mtime/大小等变化单独记录，不能把这些元数据当完整数据库或 WAL 快照 hash。保存输入的内容地址和 SHA 只证明当前精确原文的本地完整性；回执不是 RAW、资格、历史 owner 或 source capability。

## compact 历史与真实每日边界

`CandidateService.register_input` 通常先将超过六个会话的行情收纳进同一权威 SQLite 的内容寻址 history artifact。导出的完整原 input 因此可以包含 `history_artifact_ref/hash` 与近期 inline 行；“原 input 全文”不等于“所有历史行情已内联”。不补拼旧 partitions、不生成新 input ID、不新建平行名单或数据库，也不把 history 路径写成可恢复权限。

现有 V3 交付内部重新解析 input；外部预绑定 `_history_store` 再调 V3 不会传递该权限。不过 V3 的主题窗口由当次 SOURCES_V2 原始市场 panels 提供，不依靠该历史 owner，因此 compact 本身不必然阻止其完整交付。成功导出/交付仍不能认证原 selector 的历史资格和全部历史价格。

真实 owner 接点应是新版本内部从显式同一 `ReadOnlySQLiteStore` 重验同 input 原文，并创建实际 `HistoryArtifactStore` 后绑定同一 live bundle；不能从回执或普通 JSON 恢复权限，不能 monkeypatch 冻结 V3。`HistoryArtifactStore.verify` 只核 manifest、partition 索引、行存在与计数；所消费行的 payload SHA、身份与来源四钟仍须通过原 `iter_rows/history_resolver` 分别验证，UNKNOWN 保留。本版本不执行这一 history 认证，相关每日上游接线仍未闭合。

## 验证

独立 TEST_FIXTURE 测试覆盖原字节/中文/地址不变、缺 ID 不回退、地址冲突、损坏 partial 保留、超限零 payload 读取、真实只读与已提交 WAL、原子目标碰撞、partial 被换为 symlink/FIFO/外部同字节 hardlink、同长终态篡改、回执替换不覆盖外部 sentinel、最后封存超时、资源上限和重复目录拒绝。导出的原 input 实际交给当日协调器的冻结验证器，核完整 17 行原池及 13 行原选择；compact fixture 原地址保持、JSON 无 owner 权限，以及显式原生同库只读绑定能读取原历史分别验证。工程 fixture 不证明真实当日采集就绪、原历史 PIT、连续无人值守或投资优势；`MARKET_EDGE=NOT_ESTABLISHED`。
