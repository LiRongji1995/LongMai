# 财联社可选署名原件解析 V2

`CLS_NEXT_DATA_OPTIONAL_BYLINE_V2` 是独立原件与报道姓名证据模块。未接冻结 V1、May13 worker、默认策略、服务或 CLI；旧解析失败和旧 run 保留。初始研究语义尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`。

## 固定原件与署名

`parse_cls_article_v2(raw, source_url=...)` 只提取固定正文，不发行认证能力。原件最多 2 MiB，必须是 UTF-8；URL 仅允许 HTTPS 的 cls.cn/www.cls.cn、默认或 443 端口、`/detail/正整数` 路径，无凭据、query 或 fragment。仅接受唯一完整的 `__NEXT_DATA__` application/json 脚本，拒绝重复 JSON 键、非有限常量及不符合 props/pageProps/articleDetail 路径的结构。

id 必须是与 URL 一致的正整数，ctime 是可转换的正整数秒时间；title/content 必须非空文字，正文通过冻结 HTML 文本提取器解析，不执行脚本。缺 author、author=null、author={}、缺 name 或 name 为空文字均保存 author=None、author_status=UNKNOWN；其它 author/name 类型拒绝。非空署名保存 AVAILABLE。作者未知不会丢弃已认证正文，也不新增来源家族或独立信息源。

publisher_family/origin_family 始终为 CLS，只代表取得文章的媒体家族，上游原创性与独立数据源仍未知。不得从署名数、文章数推算独立催化数量。

## 原件能力与实际时钟

`authenticate_cls_original_v2(artifact_reader=..., artifact=ArticleArtifact(...), cutoff=..., coverage_type=...)` 要求真实 SourceArtifactReader，实际重读原件并核对 receipt SHA、身份、文件大小及 request/received/ingested 时钟。received/ingested 必须分别等于 receipt.first_seen_at/ingested_at。完整性不足或任何原件/实际时钟晚于 cutoff 为 UNKNOWN；RECONSTRUCTED_HISTORICAL 不豁免晚收件。

返回对象为新模块发行的进程内封存对象，弱引用登记原对象，并在消费与终态重新读取原件、来源身份及 receipt。普通 JSON、自算 hash、深复制对象均不能恢复能力；这些是接口约束，不是对恶意 Python 调用者的安全边界。

旧 HTTP 日志缺 ingested_at 时，不以 received_at、mtime 或日期补造。调用方必须提供实际已记录的 ingestion，或显式登记当前再次摄入的真实时刻并保留新 context 身份。历史原件现在取得或再摄入不能声称过去已可见。official_pit_validation_countable 与 historical_first_seen_proven 始终 false。

## 显式业务日期和姓名见证

`CLSNamedWitnessClaim` 必须显式提供 reported_business_date、reported_name、evidence_text 和 receipt_id。`build_cls_named_witness_v2(original, claim)` 仅消费新发行原件对象。姓名必须实际出现在精确正文片段中；输出字符定位和 UTF-8 片段 SHA。

业务日期必须出现在该片段或显式提供的原文连续上下文中。上下文定位必须包含同一份已定位姓名片段，不能借文章另一处日期拼接。完整年月日直接认证；明确“月日”只可结合原件 ctime 年份，保留此推年依据。上下文有去年、明年等相对年或没有形成完整日期的年份标记时，不作出版年推断，保守 UNKNOWN。没有日期、日期矛盾、多日期歧义或报道业务日晚于出版日/截止日为 UNKNOWN。发布日期恰好相同也不能替代业务日期证据；“今日”不自动换成 publication 日。上下文关联仍是显式人工审核声明，不是语言语义证明。

只有正文与业务日期均可用时才可计为有限的报道姓名见证。代码、普通 A 股身份、历史资格、完整题材成员、交易所限价事件、数值市场权威及独立信息源均不由文章证明；requested_instrument_id 仅保留声明，resolved_instrument_id 始终 None。未来姓名或后来代码不能回填历史身份，selection_or_focus_effect=NONE。

## 检查与边界

独立 TEST_FIXTURE 检查覆盖缺署名保留正文、旧 V1 同原件仍失败、固定身份和结构拒绝、SHA/receipt/clock 篡改、晚 cutoff、日期缺失与歧义、上下文错绑和后来身份保持 UNKNOWN。真实原件验证必须单独报告；测试不是投资验证。没有网络、DB、SDK、券商或收费服务调用，也没有自动替换冻结策略。
