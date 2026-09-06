# R05 最终报告

## 缺陷关闭

E01-E04 均已落到代码和测试：上海业务日严格校验、worker 部分结果超时保留、规则推导需要本地认证哈希、summary API 优先轻量投影。

## 数据供应接通

Tushare Pro HTTP adapter 已按官方文档接入端点表、字段、分页、token 缺失错误和脱敏日志；当前无 token，因此真实状态为 `AUTH_REQUIRED / REAL_NOT_VERIFIED`。BaoStock/AkShare/SSE 路径完成 R05 PV 必需数据真实采集。

## 真实量价观察

观察日：2026-09-04。R05 专用真实采集完成 43/43 个请求，当前主板合格池 3043，资格未知 0，PV 必需字段完整 3039，PV 必需字段缺失 4，`PV_OBSERVATION_V1` 入围 525。Top20 见 `pv-observation-top20.csv`。

## 原五族 DAILY

`DAILY_V1` 仍是原五族主策略，不改名、不降级；R05 没有把 PV 控制组冒充完整 DAILY。

## 股性与题材实验

`DNA_RERANK_V1` 当前为 `INSUFFICIENT_COVERAGE`，因为缺历史事件/次日样本覆盖。`THEME_CONTEXT_V1` 当前为 `UNAVAILABLE`，因为本轮 PV 专用采集没有题材成员。二者均不影响 S1。

## 策略对照

新增三组实验均标记 `INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED`，`MARKET_EDGE` 仍为 `NOT_ESTABLISHED`。详见 `strategy-comparison.md`。

## 后验与竞价

本轮未伪造 09:25 竞价或后验收益；已有竞价/后验 API 保持独立追加事实的语义。

## 未完成

Tushare 真实权限、DNA 样本库、题材成员冻结、完整 DAILY 全链路和旧 MiroFish 全 pytest 收集问题仍需后续处理。

## 运行与交付

核心业务测试脚本通过 665 项，R04/R05 相关子集通过 213 项。审阅索引、摘要、CSV 和本机 ZIP 均在本目录或 `deliverables/` 中。
