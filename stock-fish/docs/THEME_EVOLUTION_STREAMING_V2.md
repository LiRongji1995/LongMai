# 主题流 V2 显式执行版本

`THEME_EVOLUTION_STREAMING_R2` 使用真实
`EXACT_EVIDENCE_ROW_AUDIT_JOINT_SPOOL_V2` 的当前 owned journal。旧主题流、旧日志、
生产入口、服务和策略配置未切换。本版不选股、不抓取数据、不读业务数据库，不重试此前真实失败。

## 保留的语义

数值、FSM、风险、时钟可见性、修订选择、缺日、dated absence 和 current-cohort 语义
来自冻结 `theme_evolution_streaming.py` helpers 及原 `theme_evolution.py`。
原交易所日历按顺序完整推进；`LATEST_67` 与全观察日研究口径、全部输出与最后两日展示口径不变。
未知轴仍为 UNKNOWN，不压缩缺日，不将当前成员反推成历史成员。所有 allowed evidence 行均写入完整日志，
重复行仍做新鲜磁盘 SHA/规范内容检查。没有 LRU、记录删减、成员过滤或参数调整。

实现、特征、规格及输出 schema 使用显式新版本。`source_identity()` 登记新模块、新规格、
真实 V2 journal，以及旧 helper、数值配置、规格和既有依赖的整 SHA；数值来源身份另列。
旧 context hash 不覆写，新执行身份使 context hash 自然不同。

## 联合审计与失败

成功收尾直接调用当前 resolver 的 `joint_dependency_digest_and_audit_manifest()`：完整 pre
SHA/7FP 与写入账本验证、全部排序规范依赖记录摘要、完整 post SHA/7FP。原依赖 hash 与原内存/旧流一致。
不用 caller manifest、缓存或 JSON 的 verified 恢复审计能力；成功后不再另做一次 close 与独立清单重扫。

普通 evidence envelope 的错误仍按原 resolver 成为 UNKNOWN。真实 journal 写入或重复读取中的
ValueError/TypeError/KeyError 经狭窄 RuntimeError sentinel 穿过格式 catch，再在 resolver 外
以原异常对象重抛。实际预算失败不能伪装 `INVALID_EVIDENCE_ENVELOPE`。
数值/来源/联合审计/终验失败时仍尝试关闭并验证真实 partial journal；原 primary 与 cleanup
分别登记，cleanup 不覆盖先发生的异常。异常附带的 provenance 与 read metrics 是复制的诊断值，不能恢复能力。
部分文件保留；不删除、清空或重新命名失败 journal 为成功。

## 原件终验的接线边界

入口仍要求原 `SourceMarketStream` 的真实能力；只有显式 TEST_FIXTURE 可以使用隔离 pack 能力。
joint 后再次校验 bundle、stream descriptor/binding、源码身份；执行 live
`_source_terminal_validator()` 后再校验这些身份。callback 的原失败对象直接传播。

真实 caller 必须传入其当前已登记 owned original registry 的固定 closure，执行全原件整 SHA/7FP
终验。V2 本身不认证这个 closure 的 owner 或 outer 全原件，也不因任意 callable 或返回 dict
宣称物理来源已获认证。callback 只加强检查，不能替代真实 SourceMarketStream、初始原件能力、
外层登记或来源时钟；不能从 JSON、归档派生文件或旧失败运行恢复。输出仅声明 live callback 是否返回，
outer 原件认证明确由 caller 负责。

接入真实研究仍需要新私有执行版本显式选择本模块、登记本模块/V2 journal/旧 helper 的 source identity、
绑定当前 original registry 终验与完整新 run/input/目标日身份。当前旧 caller、默认策略与生产服务未改变。

## 资源与验证范围

本实现每次只保留一日市场 panel、最多两日 selected bars 和每主题60日 feature 历史；
输出全部行与日志索引仍占内存。日志段/单行上限沿用 V2 的32MiB，完整审计文件仍写到 caller 私有目录。
`journal_read_metrics` 只计真实日志显式返回字节及调用，不冒充全部 kernel、行情原件、输入解压或 worker I/O。

原 `SourceMarketStream` 的67日预校验与逐日重建没有改变；inline 输入的解析、原件读取、完整 journal
写入/重复检查以及输出仍消耗资源。联合收尾仅减少日志的重复全读。现有真实执行预算为
2400秒 worker/2415秒监督、2.5GiB worker RSS/3GiB进程树、4GiB实际返回读取及512MiB新输出；
本版没有提高任何上限，512MiB不是此前真实 worker RSS 上限。

隔离测试须逐项比较批量、旧流、新流的完整数值/FSM/未知、每条规范证据和依赖 hash，覆盖缺日、
迟到/部分父来源、dated absence、不同成员与价格基准、原件/descriptor/journal 篡改及 fatal budget。
测试计量只能证明 TEST_FIXTURE 集成与日志重复读取减少，不能声称真实67日或全局4GiB/512MiB预算已通过，
也不能证明年度覆盖、投资有效性、生产接线或外层物理来源认证。

## 本轮实际工程检查

2026-10-12 使用固定项目 Python `-B`、已有纯 Python pytest 目录及独立 TemporaryDirectory，
运行52项新主题流、37项V2日志及7项V1日志测试，共96项通过，pytest退出0；实际测试89.48秒，
子进程89.61秒、外层91.25秒，子进程峰值RSS 63,262,720字节，DB/网络尝试0。未安装或升级依赖。
完整逐会话数值/FSM for-loop 的 AST 与冻结 V1 相同；七个旧实现初终整SHA相同。

第一轮实际退出1，22项失败、26项通过：测试把 batch 内存的 tuple source_refs 与磁盘 JSON list
直接作 Python 类型比较，导致完整数值断言前失败。修正为完整规范 JSON 逐行比较，保留原依赖 hash，
没有删样本或放宽数值、来源、未知和篡改断言。该失败保留为工程 oracle 错误，不追认为通过。

67会话完整夹具的402条allowed证据行，V1日志实际返回读取2,255,980字节，V2为1,620,240字节；
完整依赖hash同为 `79e274c8fad669515c35052c871bc0233a5bdfefc8ef5a090d812bea8cb0fc15`。
计量仅覆盖日志显式读取，不能外推真实67日来源预校验、全市场规模或 worker 总预算。
首次失败中的96.21秒与最终89.48秒不是同一次执行，资源数字不混用。
