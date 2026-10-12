# 字典行帧审计与有界 LRU V4

版本 `EXACT_EVIDENCE_ROW_AUDIT_DICTIONARY_LRU_JOINT_SPOOL_V4`。显式新模块，未接默认服务、CLI或真实67会话caller。旧V2/V3、策略、来源和保存run不变。工程夹具不认证真实完整67库存、重复次数、缓存命中率或4GiB预算就绪。

## 接口与能力边界

`SpoolingEvidenceResolverV4(receipts, *, evidence_directory, cache_max_bytes, cache_max_entries, segment_bytes=SEGMENT_BYTES, logical_read_callback=None, **kwargs)` 保留原 `resolve`、`effective_dependencies`、`joint_dependency_digest_and_audit_manifest`、`audit_manifest`、`read_metrics`、`failure_provenance`、`flush/close` 和上下文管理接口。`DictionaryRowJournalV4` 为 owned append journal。两个缓存限额必须显式传入；没有生产默认。任一为0即禁用缓存；负数、bool、非int和整数溢出拒绝。修改已登记限额拒绝。

真实SourceArtifactReader、resolver、receipt、源字段、可见时间、证券/日期范围及最终RAW来源whole认证仍由原消费链完成。本后端继承原EvidenceResolver的状态和来源判断，不发行来源能力，不能用manifest/index或JSON恢复owner。`source_identity()` 只登记本模块、真实V2/V3基类、visibility、history_artifacts和三份规格；它不是完整caller/runtime union，也不是来源认证。

## 物理格式与索引

每32MiB以内逻辑segment的首条通过canonical验证的JSON去掉结尾LF，取末尾至多32768B作为固定非空字典。所有该segment的行含LF使用 zlib level6、wbits15、同一字典、Z_FINISH。字典为真实独占派生文件，不训练外部语料。新schema使用Adler32，不称gzip CRC32。

每条索引84B，big-endian `>32s32sQIII`：完整原canonical SHA256、完整frame SHA256、offset uint64、compressed uint32、含LF logical uint32、segment uint32。保留每个完整key，不截断或按前缀匹配；bool、越界整数、重复、缺行、错位、重叠/孔洞、尾部、非法segment等拒绝。

写入期间 `record-index-append.bin` 保存真实写时84B索引，普通读取按owned slot核完整entry。finish仅一次生成按完整32B key排序的 `record-index.bin`，含真实4096B头和全部84B entries；封存后普通访问执行磁盘binary search并核每个实际slot。原append索引保留并全量认证，不能隐藏第二份索引的空间和I/O。`dictionary-segments.json` 完整绑定实际segment、字典、首行hash、逻辑字节/hash；头绑定其SHA、当次generation及目录身份。字段与manifest的真实字节分别计费。

所有文件使用原O_EXCL/O_NOFOLLOW/O_NONBLOCK、regular/nlink1、目录身份/文件覆盖、完整dev/ino/mode/size/mtime/ctime/flags、真实写入ledger与SHA守卫。原失败与partial文件保留。封存manifest物理检查不能自称完成逻辑解压认证。

## 缓存与完整审核

只有 `journal[key]` 的ordinary路径允许LRU；`resolve` 的原重复记录路径仍真正走这个接口。每次先真实读索引slot/头、字典/segmentmanifest及完整物理frame，核frame SHA及初终7FP。cache key绑定不可序列化本进程owner、generation、完整84B entry、实际目录和文件身份、字典SHA及manifest身份。

miss受限inflate，逐实际返回逻辑chunk计费，完整核CM/window/FCHECK/FDICT、字典Adler ID、逻辑Adler trailer、EOF、无unused/unconsumed或多流/尾部、精确长度/LF、原row SHA、canonical JSON。完成所有frame/index/dictionary后验守卫后才保存immutable bytes。hit仍整帧物理读取和SHA校验；只省inflate，返回每次新parse的嵌套对象。损坏cache类型/内容拒绝，超大行不缓存，逐出后重读会重新inflate。缓存payload bytes不是Python对象或OS RSS。

joint、authenticated_records、effective_dependencies的完整迭代均强制uncached全行解码；joint另核实际逻辑返回delta等于全部J。原局部索引/切片语义保留：所选行uncached解码，PRE/POST仍认证全journal物理文件，不冒称解码了未选行。PRE/POST保持全部append索引、sorted索引、字典、segmentmanifest、帧及封存manifestwhole SHA/7FP；没有跳过required records或完整来源终验。joint同窗内仅复用本journal真实whole扫描产生的精确pre对象和已认证immutable字典/entry，逐行仍检查这些文件的实际7FP并完整读取frame；复制JSON、过期或篡改pre拒绝。此处避免逐行重读已在同窗完整认证的索引/字典manifest；PRE/POST whole不减少。

`verify`/`close` 与V3一样为完整物理写账本验证，明确 `complete_allowed_row_audit=False`。只有真实joint全部逻辑解码成功的返回proof为True；磁盘manifest保持False。logical callback是计费接口，非来源能力。其异常原对象sticky；EvidenceResolver吞ValueError的格式路径不能使预算失败降为UNKNOWN。主因与reader/metadata/close清理失败分列。

## 计量与尚未验证范围

指标分别记录physical/logical返回、ordinary访问/完整frame读、索引/头/字典/segmentmanifest/guardmanifest字节、hit/miss/eviction/uncacheable、缓存bytes/entries当前及峰值和所有物理read buckets。实际错误前返回逻辑字节也计费。外部read wrapper在未把bytes交回本模块前抛错时，其真实已返回费用由外层live meter记录；本模块指标不冒称全部source/kernel/import I/O。

物理产物包含 `C + B_dict + 84N + (4096+84N) + segment_manifest + seal_manifest`，还须加caller/报告/临时峰值。完整joint按本实现实数计算：PRE/POST读全部物理文件，canonical再读全部frames并返回J；append与sorted索引均计，manifest读取亦计。普通additional cost为Σ[n_i*C_i + m_i*u_i]，另加全部索引binary search、头、字典和guard读取。最坏m_i=n_i，不能预设低miss。旧前缀单I84诊断不是本实现的完整预算上界。

所有新caller code/config/spec/runtime union、真实完整67行库存、n_i/m_i、Q/字典与guard费用、actual RSS/时间/输出仍需真实fresh登记和独立预飞。不少会话/股票、不改公式/未知、不恢复旧owner、不提高4GiB或512MiB预算。当前仅工程原型可接显式新adapter；本规格没有授权真实67执行或默认服务切换。
