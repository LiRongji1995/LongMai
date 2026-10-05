# 测试入口与边界

所有业务测试入口保持 `python scripts/run_core_tests.py tests -q`（工作目录 `stock-fish`）。本次未删除测试、改默认排除规则或扩大 skip；r02/r03 等旧名称仍覆盖兼容和安全断言。

- 日常闭环：`python scripts/run_core_tests.py tests/r05_product/test_daily_observation.py tests/r05_product/test_daily_research_delivery.py tests/r05_product/test_daily_research_delivery_boundaries.py tests/r05_product/test_daily_research_service_outcomes.py tests/r05_product/test_daily_research_scheduler.py -q`。
- 存储/资源瘦身：`python scripts/run_core_tests.py tests/r05_product/test_slimming_storage.py tests/r05_product/test_runtime_resources.py -q`。
- 保留的旧量化/账户兼容：`python scripts/run_core_tests.py tests/quant tests/risk tests/recommendation tests/r02_correctness tests/integration -q`。完整旧研究依赖使用 `requirements-dev.txt`；最小环境无法代表原生 Qlib 模型已验证。
- Qlib 上游测试：从仓库根 `python -m pytest qlib/tests`，需独立安装其开发依赖和指定数据。本任务未执行上游模型/下载测试。
- MiroFish 可选集成：业务目录 `python -m pytest MiroFish/backend/scripts/test_profile_format.py`，须先按该组件说明配置依赖和资源。本任务未启用此外部集成。

不要把裸 `--collect-only`（未传 `tests`，可能搜到 MiroFish）与业务套件收集数字比较。真实行情/外部服务不属于本次隔离验收。

`tests/support/daily_delivery.py` 提取自 `r05_product/test_daily_research_delivery_boundaries.py` 的 `stamp/FixtureJobs/FixtureDelivery/identity/add_job/add_attempt/add_ready`，内容与语义保留；旧测试模块重导出以兼容已有导入。测试 node id 未迁移。其他共享辅助函数暂时保留原位置，避免与本次无关的大范围重排。

本次实际测试、既有失败、安装入口与限制以 [瘦身主报告](../docs/archive/stock-fish/docs/SLIMMING_REVIEW_AND_RESULT.md) 为准。
