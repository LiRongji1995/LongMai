# 使用说明

默认让 Codex 操作。唯一主入口为 `stock-fish/scripts/codex_daily_v2.py`；以下 `python` 指已登记的项目解释器。全局 `--runtime NAME`、`--registry-root PATH` 放在子命令之前。沿用现有登记及默认实例，不为每次聊天创建配置或账本。

## 状态与盘前研究

```sh
python stock-fish/scripts/codex_daily_v2.py doctor
python stock-fish/scripts/codex_daily_v2.py status
python stock-fish/scripts/codex_daily_v2.py operations
python stock-fish/scripts/codex_daily_v2.py premarket-plan
```

先看实际观察日、目标日、资料截止、逐阶段缺口和原预算。状态正常不代表行情或 DAILY_V1 就绪。计划允许执行且本次已有采集授权时才运行：

```sh
python stock-fish/scripts/codex_daily_v2.py premarket \
  --observation-date YYYY-MM-DD --target-session YYYY-MM-DD \
  --request-key ORIGINAL_REQUEST_KEY
```

只有 original 流程，复用有效原件，连接采集、原核心、题材解释、正式导出及终验。失败或提交不明不自动重试；换 key、版本或同会话参数不能重开旧请求。缺数据与预算不足保留原原因，不能靠凑数量形成清单。

需要更新明确缺失的数据时，仍使用这一流程，先只读检查补缺准入：

```sh
python stock-fish/scripts/codex_daily_v2.py premarket-plan --collect-missing
python stock-fish/scripts/codex_daily_v2.py premarket \
  --observation-date YYYY-MM-DD --target-session YYYY-MM-DD \
  --request-key ORIGINAL_REQUEST_KEY --collect-missing
```

只有计划的 `collection_admitted` 成立且本次采集已获授权时，才执行第二条命令。已保存的必需来源先按SHA与实际可见时间认证；只补真正MISSING及其明确父依赖，不把失败、不明或未来来源当缺失。使用原登记预算、请求账本和进程监督；换flag也不能重试旧失败。必须已有可认证交易日历，计划准入不保证供应商请求最终成功。普通历史查询不使用此flag。

## 采集范围与初筛

盘前流程增加 `COLLECTION_PRESCREEN_V1`，在个股资讯请求前按两条通道的并集入围：最近5个已完成交易会话内有完整来源支持的龙虎榜记录，或当日成交额达到原R2动态参考线且原近期参与度为PASS。动态参考线仍为1亿元加沪深四板A股近5会话总成交额中位数的0.02%；参与度仍为近5会话至少一天合法换手率达到5%。初筛参数尚未验证，不改变任何原策略公式、名单或排序。

龙虎榜使用一次明确日期区间的汇总请求，沿用原缓存、请求账本和累计预算；榜单PARTIAL、失败、缺页、晚于截止或日期异常均为UNKNOWN，不当成未上榜。净卖出上榜也可作为调查线索，上榜后收益不进入初筛。计划中的 `PRESCREEN_LHB` 列出来源与成本，`prescreen_plan` 的候选数量在原核心计算前为null，不冒充零；缺源仍须显式 `--collect-missing` 准入，不能改变日期窗口重试旧失败。

每次任务的 `collection-prescreen.json` 保存全资格池入围、暂不优先和证据未知的原因及原始来源引用。报告显示三类数量；原研究入选但未通过采集初筛的股票仍保留在原名单中。个股资讯只抓取标题包含入围股票名称的文章；零可用名称时不发新闻请求，未采不代表没有新闻。此有界名称匹配不是全网新闻覆盖证明。

主动补采只保留原核心必需的近25交易会话全市场行情/master、交易日历、官方身份和资格资料、复权及龙虎榜汇总。复权前复用原资格规则检查当日身份，仅跳过明确不合资格的证券；全部合资格证券及资格未知者仍保留，完整历史复权锚点不截短。已有同窗口批次原样复用，不因缩小代码范围重新下载；改批次或日期也不能绕过旧失败、未完成请求。

个股新闻和可选题材解释在初筛后处理。新闻只请求入围股票的相关文章；题材解释只读取入围且原策略入选股票相关的已保存同日目录和完整成员节点，并按需要读取既有额外42会话行情。不再为可选解释主动扫全行业/概念或补齐这42日的全市场资料。缺少缓存时显示UNKNOWN；部分缓存只支持部分解释，不能宣称完整题材覆盖。原研究名单、排序与题材计算公式不变，原题材节点的全体成员和原入选名单分母不裁剪。

冷缓存下，全市场行情/master这一部分从67×2降为25×2个逻辑请求，减少84次（约62.7%）；这不是总采集量或实际耗时降幅，缓存命中时节省会更少。计划中三个可选题材阶段显示`CACHE_ONLY_AFTER_PRESCREEN`与零网络预算；资格预览正常化次数单独列出，不冒充已经执行策略。仍不新增逐股席位或资金流采集。

## 已保存成果、历史与导出

```sh
python stock-fish/scripts/codex_daily_v2.py views
python stock-fish/scripts/codex_daily_v2.py show --view-id VIEW_ID
python stock-fish/scripts/codex_daily_v2.py export --view-id VIEW_ID --role all.csv --output NEW_FILE.csv
python stock-fish/scripts/codex_daily_v2.py show --job EXISTING_JOB_DIRECTORY
```

view 查看验证本地封签和五份原导出，保留原日期及最后完整认证时间，明确本次没有重新认证供应商来源。`--job` 核验指定原任务及其控制件，不把部分阶段成功当成完整交付。`export` 只写新文件，不覆盖旧报告。可选 role 用 `show --help` 查看。

SQLite 中的原研究结果：

```sh
python stock-fish/scripts/codex_daily_v2.py history --db EXISTING_DB --state EXISTING_STATE
python stock-fish/scripts/codex_daily_v2.py history --db EXISTING_DB --state EXISTING_STATE \
  --run-id ORIGINAL_RUN --format csv --output NEW_FILE.csv
```

历史查询不重新选股。旧结果中的淘汰策略字段仍按原 JSON 只读保留。

## 对明确输入研究与后验

对已有、已通过来源校验的输入运行 PV 与主动 V2，并单列冻结 DAILY 可用性：

```sh
python stock-fish/scripts/codex_daily_v2.py research \
  --db EXISTING_DB --state EXISTING_STATE --input-id AUTHENTICATED_INPUT \
  --request-key ORIGINAL_REQUEST_KEY
python stock-fish/scripts/codex_daily_v2.py research-outcomes \
  --db EXISTING_DB --state EXISTING_STATE --run-id ORIGINAL_RUN
```

追加后验须显式加 `--update --input-id AUTHENTICATED_INPUT --request-key ORIGINAL_REQUEST_KEY`；观察仍缺失或未成熟时保留 UNKNOWN/PENDING，不当零收益。`jobs --db ... --state ...` 查看已保存作业状态。

R2 原生归档和后验由同入口 `outcomes` 分发：

```sh
python stock-fish/scripts/codex_daily_v2.py outcomes r2-daily-plan \
  --db EXISTING_DB --state EXISTING_STATE --run-id ORIGINAL_RUN --input-id AUTHENTICATED_INPUT
python stock-fish/scripts/codex_daily_v2.py outcomes --request-key ORIGINAL_REQUEST_KEY r2-daily-run \
  --db EXISTING_DB --state EXISTING_STATE --run-id ORIGINAL_RUN --input-id AUTHENTICATED_INPUT
```

仅保留 `r2-archive-verify/import`、`r2-daily-plan/pending/run/status`。归档导入同样须在 `outcomes` 后先提供 `--request-key`，并显式给原任务目录及 `--expected-final-sha256`；完整认证缺原代码字节或证据时拒绝。旧失败、过时输入、来源不齐或不满足标签时间的请求不会被换入口补跑。日常不运行全历史回测。

备份、恢复及登记方法见 [DEPLOYMENT](DEPLOYMENT.md)。本轮实际验证范围见 [CURRENT_STATUS](CURRENT_STATUS.md)。
