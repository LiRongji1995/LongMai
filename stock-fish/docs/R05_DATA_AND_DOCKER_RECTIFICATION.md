# R05 数据运输与 Docker 验收说明

> 最终交付补充（优先于下方 runtime-07 历史）：runtime-09 于 2026-09-06 14:10:13—14:10:56 UTC 实际执行，42.95 秒、23 条命令全部 exit 0，71 个镜像文件与宿主字节一致。最终镜像 `sha256:34b376a6a32443fb6c929e151524e3c0e5a102c8dad0edecf066ddd5b38b84bd`。该轮含最终详情查询和派生索引，证据 `docker-final-command-09/`、`docker-runtime-09/`；隔离容器已停止。完整核心测试 752 passed、1 skipped，最后索引补丁另有 34 项相关测试及两日期真实 API 通过。runtime-08 的实际成功记录亦保留。

本文件记录 F11/F12 的修复和 2026-09-06 实际执行证据。市场回放、策略统计和投资优势见统一周回放报告；下面的故障注入样本都是明确的 TEST_FIXTURE。

## 已执行检查

- 数据队列、来源、直接限价、备份恢复及新增进程/部署回归：**104 passed in 36.79s**。命令、起止时点、退出码、环境和 stdout/stderr 哈希位于本机 `review/r05-rectification/tests-data-deploy-01/`。没有把旧测试数量写入证据生成器。
- 上次独立反例原文件重新执行：**8 passed**，证据 `review/r05-rectification/provider-prior-independent-tests/`。没有改这些独立断言。
- 后续补充无 Token 队列在凭据变为可用时恢复的回归，保留旧 attempts/回执；相关队列与新测试再次实际执行 **48 passed in 18.19s**，证据 `tests-data-deploy-02/`。
- 真子进程先成功保存第一页，第二页阻塞后杀死调用父进程和 worker；新进程从 offset=2 继续，最终三行完整，访问 offset 序列为 0、2、2。第一页没有重拉。
- 单独验证嵌套请求队列 SQLite 的已提交 WAL 被在线备份，恢复到新目录后该行可读；没有裸拷主文件冒充一致性备份。
- Tushare 16 个已登记端点均用实际环境执行授权检查：全部 **AUTH_REQUIRED**，真实 HTTP attempts 为 **0**。因此不声称这些付费/授权供应商端点已获权或真实接通。证据 `review/r05-rectification/tushare-auth-probe/endpoint-status.json`。

## 请求、预算和来源

`RequestQueue` 分列 task attempts、HTTP attempts、SDK operations、page counts、task retries 和 cache hits。`network_requests_this_run` 保留兼容字段，但其含义现为实际 HTTP 预扣数量。BaoStock 使用非 HTTP 协议，其 SDK 调用单列，不能称为 HTTP 请求。原有任务数量保护仍保留，任务上限和 HTTP 上限分别判断。

HTTP 在进入网络调用前，经 SQLite `BEGIN IMMEDIATE` 预扣。账本写入后即使进程被终止也不会漏计已准入请求。明确的预算耗尽归 `BUDGET`，不再归网络故障。每次源请求记录运输器、worker、预算实现文件的 SHA-256；原始响应和请求回执继续存入内容可核验缓存。

成功分页/证券子项使用临时文件、flush、fsync 和原子替换保存 checkpoint。Tushare 恢复页游标；Eastmoney 恢复成功证券；BaoStock 因子恢复逐证券完整子项；AkShare 保存并复用已成功 HTTP 页。不同端点和规范化参数分开保存 checkpoint，避免共享输出目录的并发请求互相覆盖。

BaoStock 的匿名 session 用跨进程 OS 锁覆盖 login/query/logout。游标返回 False 后仍检查错误码，翻页失败不会再把前 2000 行标为完整。全股票基础档使用 `stock_basic_all` 调用官方空代码查询，不作几千次逐股重复请求。

免费量价结构采集优先于逐证券限价增强，旧实验、池外证券、活跃周期和退出影子需求与旧候选后验需求合并；保留服务提供的起始日期，包含 MA20 前史。无 Token 不成为 PV 的依赖。

## Tushare 标准化边界

请求计划登记全部端点；每次标准化报告逐端点列文档、参数、回执、HTTP、状态和消费模块。下列文档于 2026-09-06 实际复核：

| 端点 | 标准化/消费 | 官方语义与边界 |
|---|---|---|
| stock_basic | 当前 UniverseSnapshot、上市日期 | 当前基础档不能自动证明历史板块/退市整理状态。[官方](https://tushare.pro/document/2?doc_id=25) |
| trade_cal | 日历交叉核对报告 | 不静默替换已冻结交易日历。[官方](https://tushare.pro/document/2?doc_id=26) |
| daily | RAW OHLC、reference_prev_close、股数、CNY 成交额，供 PV/后验 | 手乘 100，千元乘 1000；pre_close 是除权参考价，不能代替前一 RAW close。[官方](https://tushare.pro/document/2?doc_id=27) |
| daily_basic | 日期化 turnover_fraction | 百分数转换为比例。[官方](https://tushare.pro/document/2?doc_id=32) |
| adj_factor | 同供应商 RAW × 日期因子可比 OHLC | 基准内容绑定；不与 BaoStock 因子混乘。[官方](https://tushare.pro/document/2?doc_id=28) |
| stk_limit | 日期化直接上下限价 | 与同日 RAW 高低价交叉核对；不是当前报价补历史。[官方](https://tushare.pro/document/2?doc_id=183) |
| limit_list_d | 日期化事件证据 | 单页 2500；缺失不能当事件为否。[官方](https://tushare.pro/document/2?doc_id=298) |
| suspend_d | 全日停牌状态及日期化证据 | 已纠正为 trade_date/suspend_type/suspend_timing，不再使用旧停复牌接口字段。[官方](https://tushare.pro/document/2?doc_id=214) |
| stock_st | SecurityRecord.st | 单页 1000；只有完整、未限单股的当日集合允许推断集合外非 ST。[官方](https://tushare.pro/document/2?doc_id=397) |
| top_list / top_inst | 明确来源的龙虎榜研究证据 | 营业部标签不等于自然人；一日/三日披露窗口未知时不授权资金评分。[汇总](https://tushare.pro/document/2?doc_id=106)、[席位](https://tushare.pro/document/2?doc_id=107) |
| moneyflow | 大单/特大单 CNY 净额研究证据 | 万元乘 10000；供应商 net_mf 单列，不以大小单相减冒充它；三日与披露窗口未认证不评分。[官方](https://tushare.pro/document/2?doc_id=170) |
| dc_index / dc_member | 同日有界 ThemeSnapshot | idx_type 指定概念板块；BK*.DC 与证券代码区分；历史成员不由当前快照回填。[定义](https://tushare.pro/document/2?doc_id=362)、[成员](https://tushare.pro/document/2?doc_id=363) |
| stk_auction / stk_auction_o | 日期化研究证据 | 前者 09:26—09:29 才可取得，后者盘后更新；都不能倒填成 09:25 决策输入。[当日](https://tushare.pro/document/2?doc_id=369)、[盘后](https://tushare.pro/document/2?doc_id=353) |

工程链证明包含 Tushare 格式日线、因子、限价、ST 回执实际经过 normalizer 后被 PV 消费。它是合成供应商响应的测试，不是合法 Token 的真实市场成功声明。资金、席位、竞价即使取得也仍受上述具体口径门槛限制。

## Docker 真实验收

最终记录在 `review/r05-rectification/docker-runtime-07/runtime-summary.json`，`runtime_verified=true`。真实外层命令证据为 `docker-final-command-07/execution.json`，2026-09-06 13:49:18—13:50:00 UTC 执行，用时 41.75 秒，退出码 0；23 条实际执行记录覆盖 20 个编号步骤及 3 个子步骤，退出码均为 0。基础镜像从 [Docker 官方镜像的 ECR 发布源](https://aws.amazon.com/blogs/containers/docker-official-images-now-available-on-amazon-elastic-container-registry-public/)取得，固定摘要：

`public.ecr.aws/docker/library/python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea`

最终实际应用镜像：`sha256:a5679d75ee52bddf5289974e84bb4183e1cad1ffabf92203be53869f54376b8d`。`docker-runtime-07/06a-image-source-hashes/` 从真实容器读取源码哈希，`image-source-comparison.json` 验证 71 个核心、配置（含 JSON 锁）、静态文件、app/config/wsgi、requirements 与备份脚本均和当时冻结宿主字节相同。此轮包含最终复盘卡片统计范围提示和焦点筛选切换修正；核心策略与回放源码沿用 runtime-06 的冻结版本，不能拿后续宿主源码变更冒充该镜像内容。

隔离项目 `r05verify20260906214918` 使用新的命名卷、`127.0.0.1:18005` 和私有 API key，实际完成 build、up --wait、health/API 200、无 Token PV 工程样本 6 项、restart、force-recreate、持久数据读取、在线一致性备份、复制到本机私有目录、隔离目录恢复及恢复后数据读取。独立 sentinel 只检验 Token 映射，未向供应商发送假 Token。最终已停止本次隔离容器，未切换原有服务。

runtime-04、runtime-05、runtime-06 的成功执行均保留，分别对应较早的源字节。runtime-05 的摘要另标记其早于最后规范身份哈希/原周期首次信号修正。runtime-06 于 2026-09-06 13:11:56—13:12:34 UTC 完整通过，镜像为 `sha256:3626be84024e4eae75ada1bde50ac3ef29a89ea1c30ac628b1fd27b15681c673`，当时 71 个文件比对一致，包含规范 JSON 身份哈希与原周期首次信号口径修正；随后发生静态页面修正，因此最终前端与部署依据是 runtime-07 的实际字节比对。

失败记录一并保留：第一次 Docker Hub 授权地址连接超时；之后发现 Docker Desktop 的 cp API 不能向只读 rootfs 的 tmpfs 写文件，且从 tmpfs 复制备份失败。最终保留只读 rootfs，以 stdin 传入工程输入，备份写入持久数据卷的独立 `.backups/` 目录后复制。没有改变管理员配置、全局代理、监听公网或挂载 Docker socket。

启动脚本逐项检查 Docker 退出码，build 失败不能继续接受旧健康容器；up 后检查本次镜像 ID。已有服务的备份先于 build/up，活动任务或备份失败会停止。Linux 启动脚本只依赖 Docker 和基础 shell 工具，Python 健康检查在容器内运行。Compose [显式传入](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/) TUSHARE_TOKEN/TUSHARE_PRO_TOKEN，未把本机插值文件误当容器环境。

SQLite 主库和嵌套队列库均使用 [Online Backup API](https://www.sqlite.org/backup.html)，[已提交 WAL](https://www.sqlite.org/wal.html) 包含在快照内。恢复工具拒绝覆盖已有目的目录，原有不可变记录的绝对路径保持原样；跨部署运行仍须按 path-map 核对引用，不能宣称自动重写历史记录。

私有 `.env`、数据库、备份、供应商大响应和 Docker 私有卷不属于可发布源码；这里只发布实现、测试、配置模板与脱敏运行证据。
