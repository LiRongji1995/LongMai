# 失败题材日志的可恢复冷存 V1

本轮空间优化直接针对真实占用最大的已结束派生产物。新工具只处理已失败盘前 job 的 V2 `theme/price-journal`；它不清理行情 RAW、receipt、producer、原核心、原失败控制或登记请求，也不改变默认 original 引擎、策略或生产服务。

## 当前占用与选择依据

2026-10-29 的只读盘点覆盖开发检出、发布检出、StockFish 私有 state 和固定 daily/review 运行环境四个根，初始逻辑量合计 1,656,245,852 字节，约 1.54 GiB。这个范围不是整块磁盘；通过目录元数据统计普通文件，在每个根内按 device/inode 去重，不计目录自身空间，不跟随符号链接，不读取云占位内容。

26 日观察、27 日目标的失败 job 中，V2 price-journal 为 493,565,280 字节，约 470.70 MiB，占该 job 的 89.82%。其中 13 个行分片共 433,602,498 字节，文字索引 59,958,642 字节，manifest 4,140 字节。479,457 条记录已按完整行 hash 去重，不能把它们当作重复垃圾删除。

相较之下，本轮初始盘点时，上一轮真实盘前工程验收的 TEST_FIXTURE 约 1.67 MiB，四个工程审计任务原有合计约 11.84 MiB；不为这些小目录扩大清理范围。运行环境与旧来源代码保持现有物理身份。

## 为什么这份派生日志可以进入候选范围

原 job 终态为 `BUDGET_EXCEEDED`，worker 已被回收，display 为 null；没有成功 completion、最终题材父或 theme-context。原核心八份产物位于独立 core 目录，不依赖随后生成的 journal。V2 journal 的 live owner 只能在本次写入时建立，不能从保存 manifest 恢复。

本轮对当前管理范围的 13,098 份文件执行全文 SHA、七字段物理身份和压缩内容核对，读取物理字节 1,299,016,854、gzip 解压字节 837,961,107，未见错误。原 job 标识的七个命中均为自身 core/news/final 及两份工程诊断，未发现其他有效登记或成功产物消费者。这个结论限定于已核对的运行登记与消费者合同，不宣称证明任意外部目录没有引用。

原 `premarket-verify` 对这个没有 completion 的失败分支不核 journal；因此其归档通过不能代替本工具的完整日志验证。移除前必须在原资源锁及 state 作业锁内重新检查终态、原件、引用集合与活动 owner。

## 冷存合同

新增 `scripts/codex_failed_theme_coldstore_v1.py`，按下列独立动作执行：

1. **prepare**：固定原 job、失败控制、原 manifest、精确载荷集合、SHA/七字段身份和本次工具身份。严格限定原 manifest 声明的行分片及索引；拒绝额外文件、软硬链接、路径重定向和不完整声明。
2. **pack**：只写本次新建归档目录，将载荷和原 manifest 字节完整保存。原件尚不移除。
3. **verify**：核全包成员、每件 SHA/尺寸、压缩流完整性，并实际恢复到新目录逐件验证。
4. **retire**：显式提交固定计划与验证引用，在锁内再次完整核验原件、归档及恢复副本。只移除原 manifest `files` 中声明的载荷，逐件记录操作；部分失败必须保留已发生事实，不得报整体完成或自动再试。
5. **release-verification-copy**：在成功退休及归档重验后，按精确白名单清除本次验证创建的展开副本，保留小型验证、恢复映射和释放回执。独立用户恢复副本不受此动作管理。
6. **restore**：从归档恢复原字节到明确的新目录，拒绝覆盖现存目录、路径遍历、链接或额外成员。

原 `manifest.json` 是保护项。即使盘点将整个 journal 目录标为候选，也不能据此删除 manifest。原失败控制、core、RAW、receipt、producer 及请求登记的 SHA/七字段身份必须保持。

归档状态是 **ARCHIVE_ONLY**：可以恢复字节，不恢复旧 inode、原七字段物理身份、live owner、来源能力或历史 PIT；原 job 继续保持失败。原 manifest 继续作为原时点的历史声明，不能因冷存而改写原成功条件。

工具按每次显式动作约束时间、实际读取、输出和文件数，并记录失败边界。普通查询不自动冷存，源码发布不自动运行清理或切换服务。本次工程优化不授予后续日常任务任意退休数据的权限。

## 真实减量与检查

本次真实五步均退出 0。`prepare`、`pack`、`verify`、`retire`、`release-verification-copy` 的外层实际时间分别为 12.626、31.410、59.847、90.917、37.534 秒，包含相应资源准入与保护检查；这些不是盘前计算速度。

原 14 件载荷共 493,561,140 字节，连同原 manifest 和计划保存为 46,460,214 字节的 gzip 包。工具实际恢复全部 16 件成员；独立标准库检查再核精确集合、尺寸、全部 SHA、gzip CRC/EOF、归档当前身份和全部实体恢复，9.786 秒通过。随后只移除原声明的 14 件载荷，以及本次验证创建的对应 14 件副本。原 manifest 保持 4,140 字节，原失败状态仍为 `BUDGET_EXCEEDED`。

锁内移除前完整核验 13,098 项；移除后与释放后其余 13,084 项全文 SHA/七字段身份均保持，仅允许这 14 个源载荷缺失。最终保护证明时点为 2026-10-29 03:32:59 UTC，之后的本轮文档修改与 Git 发布另行登记。归档 SHA256 为 `9ffde0b4515414b431cde104ab2b580253d6df79646e4f971cb9a4ea440b7115`。

发布前 03:33:45 UTC 对同一四根重盘点，已扣归档、控制、代码、测试、审计及计量报告自身：

| 指标 | 初始字节 | 计量时字节 | 净减少 |
|---|---:|---:|---:|
| 四根普通文件逻辑量 | 1,656,245,852 | 1,232,214,679 | 424,031,173（404.39 MiB，25.60%） |
| 四根文件分配量 `st_blocks × 512` | 1,723,039,744 | 1,307,271,168 | 415,768,576（396.51 MiB） |
| 原 journal + 新冷存目录 + 验证残留 | 493,565,280 | 46,685,910 | 446,879,370（90.54%） |

新冷存目录共 46,618,243 字节，验证残留为 63,527 字节；工程审计与测试共新增 22,693,860 字节，全部计入上述净量。四根文件数因保留测试与审计反而增加 2,669，不宣称文件数量下降。固定运行环境字节没有变化。后续文档与 Git 增量须在发布后的私有最终快照另计；约 404 MiB 是这一轮实测净逻辑减量的量级，不与历史瘦身重复相加。

文件分配量不是 APFS 独占物理块，卷可用空间也受其他进程及共享块影响。本次不把两者直接称为实际卷空闲收益，不声称降低运行峰值内存。

发布检出中的最终定向命令如下；变量指向已核验的固定解释器、新字节码路径和独立私有 TEST_FIXTURE 目录：

```sh
"$STOCKFISH_PYTHON" -I -B -X "pycache_prefix=$STOCKFISH_FRESH_PYCACHE" \
  scripts/run_isolated_tests.py -q \
  tests/unit/test_codex_failed_theme_coldstore_v1.py \
  --basetemp "$STOCKFISH_TEST_FIXTURE"
```

实际结果 **59 passed，46.63 秒；外层 49.544 秒；退出 0**。未运行全仓测试、市场采集、盘前失败重试或投资验证。前版 53 项与开发目录相同 59 项检查各自保留，不累加成新的通过数。最终脚本 SHA 为 `0bd38fe7b4787b2672539b1310d763fc418ea46d3c5ab08939ddc614f26b1880`，测试 SHA 为 `dd554b6429e18387d43d2292b4636385bdc5b1bab2972349744b230e8d7de2b0`。

## 执行与恢复入口

各命令都是显式独立动作，不自动串联或失败重试。作业终态、管理范围依赖与活动 owner 必须先检查；真实执行时还须持有原登记的资源锁，`retire` 使用本次锁内依赖复核回执。工具本身另持有 state 作业锁及归档锁。新目录、计划 SHA 和验证回执 SHA 固定，不能换计划绕过一次失败。

```sh
"$STOCKFISH_PYTHON" -I -B -X "pycache_prefix=$STOCKFISH_FRESH_PYCACHE" \
  scripts/codex_failed_theme_coldstore_v1.py prepare \
  --state-root "$STOCKFISH_STATE" --job "$STOCKFISH_FAILED_JOB" --output "$STOCKFISH_NEW_PLAN"
"$STOCKFISH_PYTHON" -I -B -X "pycache_prefix=$STOCKFISH_FRESH_PYCACHE" \
  scripts/codex_failed_theme_coldstore_v1.py pack \
  --plan "$STOCKFISH_NEW_PLAN/plan.json" --plan-sha256 "$STOCKFISH_PLAN_SHA" --output "$STOCKFISH_NEW_ARCHIVE"
"$STOCKFISH_PYTHON" -I -B -X "pycache_prefix=$STOCKFISH_FRESH_PYCACHE" \
  scripts/codex_failed_theme_coldstore_v1.py verify \
  --archive "$STOCKFISH_NEW_ARCHIVE" --plan-sha256 "$STOCKFISH_PLAN_SHA" --restore-output "$STOCKFISH_NEW_VERIFY_COPY"
```

全件恢复及独立核验通过后，`retire` 还要求 `--verification-receipt`、`--verification-sha256`、`--dependency-review`、`--dependency-review-sha256` 和 `--allow-retire-payloads`。释放本次验证副本使用 `release-verification-copy`，传同一归档、计划和验证引用及 `--allow-release-verification-copy`。每次实际读取上限 8 GiB、新输出上限 2 GiB、文件上限 256、时间上限 900 秒，CLI 仅允许收紧；私有外层另计完整保护扫描和运行容量，不能把临时恢复展开忽略在预算之外。

以后需要原字节时，用原登记匹配的工具身份显式恢复到新的目录：

```sh
"$STOCKFISH_PYTHON" -I -B -X "pycache_prefix=$STOCKFISH_FRESH_PYCACHE" \
  scripts/codex_failed_theme_coldstore_v1.py restore \
  --archive "$STOCKFISH_ARCHIVE" --plan-sha256 "$STOCKFISH_PLAN_SHA" --output "$STOCKFISH_NEW_RESTORE"
```

保存的计划、归档、失败槽、回执和工具身份需要一并保留。工具、归档路径或物理身份变更须另行处理，不能把普通解压或另一机器的同名文件当作原登记复用。独立恢复副本由调用者管理；使用原研究消费者前还要重新认证新路径和真实来源。

## 尚未解决的持续增长

当前 original 流程仍使用展开 V2 journal，并将行情 rows 同时保存在 RAW 和带缩进的 RESULT。未来新输出可接入已有的 RESULT 压缩和紧凑日志合同，但必须验证完整来源、逻辑字节、物理身份、读取成本及原预算后，才能作为新执行版本使用。

V4 日志并非天然更小：它保留两份 84 字节索引，在本次 479,457 行规模下索引本身约 80.55 MB，比当前 59.96 MB 文字索引还大。不能仅凭版本号或小样本压缩率切换默认后端。本次实际冷存解决已结束失败派生产物的占用，尚不宣称未来每日增长已全面压低。
