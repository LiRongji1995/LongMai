# Go HTTP 保存结果兼容边界

新路由通过同一个 application Service、Repository 和 ResearchQueries 查询；不导入 Python、不重算旧结果。所有 ResearchQueries 入口先要求迁移 PUBLISHED。旧 DAILY_V1 的完整计算/配置入口继续由保留模块负责，Go 不把该入口映射为 V2。

- `/api/strategy-experiments?limit=1..1000&offset=N` 同时列出旧保存结果与新 PUBLISHED 结果，真实分页；旧缺投影时保留 `PROJECTION_REBUILD_REQUIRED`。
- `/<run>/summary` 接受 `strategy_id`、`selection=all|focus|unselected|core_pass`、`page`、`page_size=1..500`。旧结果读取保存的 header/50条page文档，核对原hash和投影source hash；新结果读取不可变app_item_summaries，核对成员总数、detail source hash、投影版本及字节hash。R1 的 CORE_PASS 不冒充 V2 的 FOCUS，R1 筛选焦点会明确拒绝。
- `/<run>/items/<instrument>` 保留旧结果的strategy_results、focus_selection、selection_policy、后验和跟踪字段；历史未知收益保留null。Go新详情带显式 `implementation_version` 和 `saved_native_detail`，保存的原始新格式仍可完整访问。
- `/<run>/review` 读取原保存review，缺后验时返回明确PENDING_MARKET_VALIDATION；不发起后验计算。
- `/<run>/export?format=json|csv&strategy_id=...&selection=...`：旧无筛选JSON是已校验归档原字节；旧筛选依据原Python authoritative ranking规则，保留source_content_hash和独立view_content_hash；CSV带BOM、CRLF和公式字符串转义，数字负号不改写。Go新筛选导出先VerifyRun，再按summary中保存排序/筛选的ID逐条读取明细，标记SAVED_GO_NATIVE_SELECTION_VIEW和独立view_content_hash，不伪装旧序列化hash。

Go新筛选导出限制64MiB的展开条目；超限明确拒绝并提示完整流式JSON/分页摘要。HTTP请求与写响应上限5分钟。旧逐股观察读取按100条metadata页检索并校验，单股超过10000条观察明确报错，避免无限载入。

同一参数重复、未知参数、非法selection/format/page、POST重复JSON键和额外字段均拒绝。POST `observation_date` 若提供必须与显式input_id对应输入一致；省略Idempotency-Key时分配本次请求ID，跨请求幂等需调用方显式提供稳定key。队列检查与提交串行，避免并发请求在持久化后无界阻塞排队。

`/api/research/runs` 保持新格式page/page_size查询；旧candidate-runs完整原始文档可读，其未移植的DAILY_V1专用视图明确501，不能无声套用新策略。认证/loopback约束由原Go传输层保留；未支持的外部研究/执行/Qlib路由继续明确404并指出保留模块。

已执行显式TEST_FIXTURE回归：跨50条存储chunk的摘要分页、limit/offset、焦点和null收益、JSON/CSV选择、CLI/API旧原字节一致、重复参数/body拒绝、新摘要/筛选导出及发布后丢失成员拒绝。普通测试退出0；race HTTP 11.067秒，architecture依赖方向5.440秒，lifecycle 32.795秒（此race在APFS增强之前）。最终扩展测试命令以统一验收报告为准，不把该小样本声明为全池或真实投资验证。

补充回归：旧run显式detail_ref的内容还必须与(run,instrument,strategy)索引返回的展开字节hash/size一致，合法但不同版本的索引目标不得替代旧引用。新增故障样本通过；HTTP整包退出0（1.047秒），该样本race退出0（2.615秒）。

新增`selection=core_pass`仅限Go原生R1，按已保存summary的CORE_PASS状态筛选，支持相同分页和JSON/CSV导出；V2/legacy明确拒绝该选择。R1导出JSON标记core_pass_total，CSV明确candidate_status=CORE_PASS且selected_focus=False，不把CORE_PASS命名为焦点。观察日期核对改用InputObservation轻量元数据查询，执行阶段原字节验证保持不变。新增小样本验证跨页CORE_PASS、候选导出、策略边界和元数据查询不载入raw。
