# Phase 1 Daily Observation Monitor

【事实】观察入口回答“今天有没有正常工作”，只读同一权威服务的交付记录、持久化 snapshot 和已有 outcome。它不触发 Daily Primary、采集或后验任务，不写原 run/snapshot/outcome，不新建数据库，不调整策略、readiness 或 evidence。

## 使用

【事实】在 `stock-fish` 目录执行；`--base-url` 必须采用实际实例地址，下面的 8001 是当前本机管理实例。全局参数在子命令前；鉴权沿用原 `STOCKFISH_API_KEY` 或 `--env-file`，不要在报告中复制密钥。

```powershell
# 当前上海日期（非交易日取日历内最近交易日），含最近20个交易日
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py --base-url http://127.0.0.1:8001 daily-observation

# 指定目标会话；盘后可显式查看下一目标日
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py --base-url http://127.0.0.1:8001 daily-observation --target-session 2026-09-24 --days 20

# 查看历史日期截至指定时刻的已知事实；截止必须带时区且不能在未来
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py --base-url http://127.0.0.1:8001 daily-observation --target-session 2026-09-22 --days 5 --as-of 2026-09-23T10:00:00+08:00

# 可选私有报告目录：目录必须不存在
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py --base-url http://127.0.0.1:8001 daily-observation --output reports/codex/observation/first-check
```

【事实】对应只读接口：`GET /api/daily-research/observation?target_session=YYYY-MM-DD&days=20&as_of=带时区时间`。所有参数可省略；days 范围 1..20。接口返回 `DAILY_OBSERVATION_V1` JSON，内含简单 Markdown。CLI 默认只打印 JSON，显式 `--output` 才保存 `report.json`、`report.md` 和 SHA-256 manifest；这些私有运行报告不提交到 Git。

## 交付与推荐口径

【事实】`target_session` 是应交付的目标交易日；`observation_date` 是原观察日。查询历史 target 默认使用现在可见的后验，若要回到历史知悉时刻须指定 `as_of`。同一次历史概览使用同一截止时间。20 个交易日来自现有有限交易所日历，不按周一至周五猜测；日历不足返回 PARTIAL/NOT_AVAILABLE，不补造日期。

| 字段 | 含义 |
| --- | --- |
| execution_status | 原 Daily Research Service 的交付状态，不改原状态机 |
| status | 展示状态：保留 READY/NOT_READY/FAILED/MISSED/PLANNED/RUNNING/QUEUED；无登记历史或日历不明时显示 NOT_AVAILABLE，并保留原状态及原因 |
| daily_primary_activity | 已有 attempt/已发布证据、正在运行、排队、未开始或信息不足；不等于策略已经运行 |
| run_id / strategy_version / config_version / data_cutoff | 原交付身份，不拿当前配置补缺失历史 |
| no_run_reason / blockers / error | 无 run 原因、未就绪阻塞项或失败信息 |
| snapshot_status | SAVED 表示确实存在原持久化快照；旧 run 兼容投影不会被声称为原 snapshot |
| counts / recommendations | 按冻结四类统计；FOCUS/WATCH 原序展示证券代码、setup、reason_summary，不重算、不翻译为新理由 |

【事实】READY 且冻结 FOCUS/WATCH 均为 0 是“正常交付、没有推荐”；UNKNOWN/REJECTED 数量仍保留。没有持久化快照时数量是 null、Markdown 为 NOT_AVAILABLE，不能冒充合法空结果。已交付后重试失败，原 READY 保留，并展示 `latest_attempt_status`。

## Outcome 状态口径

【事实】只统计所查每日 run 的原 FOCUS/WATCH。`registered_recommendations` 是核对 pending 登记的 run_id、snapshot_hash、时间及证券范围后确认的数量。REGISTERED 仅说明进入后验待办；RECORDED 仅说明已有后验标签，不表示每只股票或全部 20 档完整。

【事实】`horizons` 包含 T+1..T+20；Markdown 重点展示 T+1、T+5、T+20。每档分母是原 FOCUS/WATCH 推荐数；`totals` 单位是“推荐 × 交易日”（20 × 推荐数），不是股票只数，也不是独立交易数。

| 计数 | 口径 |
| --- | --- |
| matured | 交易日收盘时刻已到，与是否采集、收益是否有效无关 |
| recorded | 经原版本绑定和 revision 筛选的已保存标签，可能包含 NOT_MATURED 或异常 |
| normal | 原 NORMAL 标签数量，不代表完整价格路径或策略有效 |
| not_matured | 该观察期限还未到 |
| pending_capture | 已到期但无标签，或原 NOT_MATURED 标签到期后尚待更新 |
| pending | not_matured + pending_capture；等待不等于成功或失败 |
| missing | 仅原显式 MISSING；不能把未来未采标签或停牌都算缺失行情 |
| suspended / unavailable | 分别保留原停牌 / 不可用标签 |
| calendar_unavailable | 无法确认该期限；可能与 UNAVAILABLE 重叠，所有列不能直接相加 |

【事实】汇总复用原单 run outcome summary 的成熟、异常及有效绑定计数；不计算新价格收益、不输出收益排名或调参结论。异常样本仍保留。

## 运行边界

【事实】新接口按需读取，最多查看 20 个目标交易日，不循环生成包含全库策略分析的日报，不启动定时监控或外部通知。网页日报和原 report/outcomes 接口继续可用。

【需要验证】自然 20 日稳定交付、真实 FOCUS/WATCH 首次捕获及 T+20 成熟须等待实际运行。工程 fixture 不能替代真实覆盖；每次调用只观察当时状态，不保证机器离线期间仍可响应。
