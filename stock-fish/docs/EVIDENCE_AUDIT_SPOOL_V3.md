# 压缩证据日志 V3：显式隔离后端

`EXACT_EVIDENCE_ROW_AUDIT_COMPRESSED_JOINT_SPOOL_V3` 保存完整原规范证据行，
每行是独立 gzip frame；多个 frame 连续写入一个 segment。它不切换生产入口、
不抓取行情、不发行来源、不重试真实失败运行，也不改变策略、窗口、资格或历史可见性。
旧 V1/V2 源码及其测试、规格保持冻结。显式消费者仍须登记新执行与来源实现身份。

## 数据与写时身份

唯一行的规范 JSON、换行、原 record SHA、key 排序、来源依赖及
`digest_json_stream` 算法沿用 V2。重复证据仍从实际磁盘读取该行、完整解压核验，
没有缓存或 LRU。逻辑单行及逻辑 segment 均受原最大32MiB限制；segment 的
分界按逻辑字节计算，压缩大小不能扩大逻辑范围。压缩 frame 也有显式物理长度界限。

当前 owner 写入实际 frame 时保留不可从 JSON 恢复的活账本：文件 inode/设备/模式、
实际追加字节数与 SHA、每行压缩 offset/length、逻辑 length、frame SHA、原 record SHA、
插入顺序，以及每个逻辑 segment 的写时 SHA。索引保存这些位置与长度的规范表示。
首次封存必须核实际文件符合写时账本，不把 closing 状态重新 hash 当成新权威。

## 两层完整校验

联合操作先对全部压缩 segment、索引和既有 manifest 做物理 pre 校验，随后按原 key
排序完整解码每条证据并计算同一规范依赖 hash，最后再做完整物理 post 校验。
pre/post 均核真实 whole SHA、完整7FP、原写时账本、索引 exact fields、整数类型、
连续 offset、完整行顺序、全部字节覆盖及目录文件全集。它们不会重复解压逻辑日志。

中间那一次规范消费逐 frame 核完整 CRC、EOF、无尾随字节、无第二 frame、
精确逻辑长度、原行 SHA、换行、规范 JSON 和全量逻辑字节覆盖。
解压采用有界 chunk；长度超限的实际返回 chunk 也先记入读取量再拒绝。
所有 frame 的写时 SHA/length 与完整物理 pre/post 绑定，保证规范消费使用的正是
当前 owner 写入并完整认证的字节。截断、重复 frame、尾随、CRC 损坏、解压炸弹、
索引篡改、额外文件、相同大小且恢复 mtime 的修改均拒绝。

`close()`、物理 `verify()` 和磁盘 manifest 仅声明
`physical_write_ledger_audit_complete=True`，并明确
`complete_allowed_row_audit=False`、`logical_decode_validation=NOT_PERFORMED_BY_PHYSICAL_SCAN`。
它们不能代替完整逻辑审计。只有成功的联合操作或 `audit_manifest()` 完整解码后的
返回副本声明逻辑完整；磁盘 manifest 不被升级或改写成成功能力。
普通规范记录索引、迭代和重复行访问也完整解码所访问的行并执行原 fresh 物理守卫。
普通完整迭代仍做完整 pre/post；没有由前次 joint 结果恢复权限的快速路径。

## 显式接口与计量

`SpoolingEvidenceResolverV3(..., logical_read_callback=callback)` 保持原 EvidenceResolver
的来源、业务日期、时钟及 UNKNOWN 决策。非 `TEST_FIXTURE` 必须给 callable；
夹具可省略，给出时仍检查类型。callback 每次接收实际解码器返回的 `bytes`，
可直接接真实 outer budget 的 `charge_value`。计量先更新本日志实际字节，再调用
callback；跨预算的 chunk 不会从失败计量中消失。它只计量，不认证来源或 owner。

真实 caller 仍须用其固定 owned live IO meter 计全部物理读取和其他应用 I/O，
并保留 SourceMarketStream、输入/descriptor、bundle/source 终验及原件 whole SHA/7FP。
本模块不认证全部 outer 原件，任意 callable 或返回 dict 不能授予来源能力。
不允许把 JSON 中的 callback、verified 标记、manifest 或 joint 返回对象重建成 live owner。

联合接口不接受外部验证结果，固定 validation 为
`PRE_POST_WHOLE_SHA_7FP_AND_ALL_CANONICAL_FRAMES_CRC_EOF_EXACT_INDEX`。
`read_metrics` 分列实际物理返回、实际逻辑解码返回、两者合计及调用次数；
这是本实现显式读取，不是全部内核 I/O。物理重复磁盘检查仍保留。
`_JointRecords` 是 frozen V2 的同一内部类；`_RowJournal` 名称指新压缩 journal，
其安全文件操作继承 frozen V2。来源实现身份包含 V3、自身规格、frozen V2/规格、
visibility 与 history_artifacts 的整 SHA；这些身份不发行来源能力。

## 失败与预算边界

完整性 RuntimeError 使用 frozen V2 同一专用类型，保证其继承的安全文件守卫
不会被排除。逻辑计量 callback 的任何原异常（包括 ValueError）是 sticky fatal，
不会被 EvidenceResolver 格式异常分支吞成 UNKNOWN。后续操作重新抛同一对象。
读、终验、writer/manifest 关闭的主异常与清理异常分别登记，原主对象优先；
关闭仍尝试两个 writer。symlink/FIFO/目录别名在阻塞打开前拒绝，读取后再核父路径。
失败原件保留，不删除日志、不补行、不重写索引或封存 pin。

V3 降低的是重复完整物理扫描的字节，完整逻辑解码仍真实计量。若完整日志逻辑为 J、
压缩物理为 C，联合主体近似从 V2 的三次 J 扫描变为两次 C 物理扫描和一次 C+J
解码读取，另有索引、manifest 和所有原重复行读取；实际值以真实 meter 为准。
节约与压缩率有关，不能用极易压缩合成数据推断真实市场日志压缩率。

本轮只允许独立 `TEST_FIXTURE` 工程和合成逻辑规模证明，不消费真实 RAW、DB、
issuer 或 selector。合成420MiB逻辑日志仍逐条完整解码并核规范 hash，输出不超64MiB、
测试时间不超120秒、RSS不超512MiB。它不能证明真实67会话或全局4GiB成功。
未来真实旧上限仍是 worker2400s/监督2415s、OS2.5GiB/进程树3GiB、
实际应用返回4GiB、新输出512MiB、单旧原件16MiB/新逻辑输入256MiB；本原型不扩大它们。

## 检查记录

针对性测试将对比原 resolver 与 V2 的完整决策、所有规范行、排序和依赖 hash，
验证写前/封存后/迭代中篡改、原预算异常与清理失败同现、完整 frame 负例及真实计量。
以下仅记录实际隔离执行，不声明真实市场验证。

作者首次隔离入口使用固定 lexical Python `-B`，只追加既有纯 Python pytest
依赖路径，没有安装依赖或更换 native core。2026-10-12 UTC（北京时间13日）执行46项 V3 与37项冻结 V2：
83项通过，pytest退出0/29.08秒，外层30.7206秒，OS峰55,173,120字节，
自有夹具输出峰1,670,610字节，DB/网络尝试均0；临时夹具在子进程退出后清理，
真实日志未改动。后续新增索引精确字段及逻辑长度反例的最终检查另列，不将此首轮
83项追认为已经覆盖新增加的断言。

最终定向增加5个索引覆盖、布尔位置和逻辑长度反例及 public identity 断言后，
51项 V3 与原37项 V2 共88项通过，pytest退出0/27.76秒，子进程28.0218秒、
外层29.0158秒，OS峰55,193,600字节，自有夹具输出峰1,735,790字节，
DB/网络尝试0，终态owned PID为0。全部原 V2 断言及三件整SHA保持不变。

24行完整同规范hash `44fd80b9f9f572f6c81269c06081465554e48bd70e6932f439e0ce80ec20f85c`：
V2 `close + digest + audit_manifest` 实际返回110,728字节；V3 joint 的物理与逻辑合计
61,160字节，少49,568字节。这里只比较populate后的完整收尾；普通重复读取仍完整保留，
其populate阶段成本不计入这组delta。不省证据行。
420条合成日志逻辑440,422,390字节，物理输出1,220,992字节；joint 实际物理+逻辑
返回443,978,422字节，全部规范hash为
`8f9e305f7cf272e5ebdc113289aaf74e5cf9334ca79d32978a0570a5bac4795c`。
该合成内容重复度极高；这些数字是隔离资源与完整性证明，不是实际市场压缩率或
真实67会话全局预算结论。

根独立执行相同51项V3与37项冻结V2，共88项通过，pytest退出0/38.73秒、
实际40.3076秒，OS峰值58,560,512字节，自有输出峰1,751,409字节；
42份记录依赖整SHA/七字段初终一致。原24行与420条合成完整规范hash及返回量重现，
这些仍是TEST_FIXTURE，不代表真实市场或全部外层I/O。

独立完整主题夹具还验证了相反的总读取结果：同67会话的402条唯一行逻辑269,422字节，
V2行/index/manifest输出319,476字节，V3 frame/index/manifest输出228,306字节。
新索引由49,251增至94,517字节；相同普通重复访问的逻辑711,866字节之外，
V3另读取339,972字节frame。完整joint由908,374减至858,753字节，
整个原生执行却由1,620,240增至1,910,591字节。全记录、依赖hash和UNKNOWN保持相等。
小日志和高压缩合成案例的节省不能推广为任意消费者的净节省；本版尚未被选为真实日报后端。
