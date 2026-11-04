# 测试入口与边界

新工程检查使用已验收的独立 daily/review 解释器，默认入口为 `"$REGISTERED_PYTHON" -I -B scripts/run_isolated_tests.py tests/明确文件.py -q --basetemp /全新私有/TEST_FIXTURE目录`（工作目录 `stock-fish`）。只使用所选 venv 的第三方包，关闭插件自动加载及 pytest 缓存；不从旧 `review/test-tools` 借包。环境合同见 [独立运行环境](../docs/ISOLATED_DAILY_RUNTIME_V1.md)。

`run_core_tests.py` 原字节保留，仅供已有兼容工作流；其存在 `review/test-tools` 时会注入该路径，不作为新隔离工程验收入口。以下历史命令保留其原范围，r02/r03 等旧名称仍覆盖兼容和安全断言；选择这些用例做新验收时改用上面的隔离 runner，不能删除失败样本或扩大 skip。

根 `.github/workflows/engineering-gates.yml` 按组件变化运行新隔离安全门禁、变更测试和 Go 独立 oracle/race/vet；Live 使用独立环境。daily/review 仅在锁覆盖的原生 macOS x86_64 / Python 3.12 构建，其他平台不复用该锁。CI 不部署、不采集、不重试业务、不使用账户密钥；源端完整性、未知语义、账户约束和进程监督失败都会保留为失败。

CI 的定向门禁、完整历史兼容套件、真实每日交付和投资有效性分别报告。历史 golden 或依赖不匹配仍按原失败记录处理，不改 golden 或跳过用例来宣称全仓通过；定向检查通过不等于历史全套通过。发行输入缺口由 `scripts/release_profile_manifest_v1.py` 只读列出，`HASHED_INPUTS_VERIFIED` 也不等于本次运行已验收。

- 日常闭环：`python scripts/run_core_tests.py tests/r05_product/test_daily_observation.py tests/r05_product/test_daily_research_delivery.py tests/r05_product/test_daily_research_delivery_boundaries.py tests/r05_product/test_daily_research_service_outcomes.py tests/r05_product/test_daily_research_scheduler.py -q`。
- 存储/资源瘦身：`python scripts/run_core_tests.py tests/r05_product/test_slimming_storage.py tests/r05_product/test_runtime_resources.py -q`。
- 保留的旧量化/账户兼容：`python scripts/run_core_tests.py tests/quant tests/risk tests/recommendation tests/r02_correctness tests/integration -q`。完整旧研究依赖使用 `requirements-dev.txt`；最小环境无法代表原生 Qlib 模型已验证。
- Qlib 上游测试：从仓库根 `python -m pytest qlib/tests`，需独立安装其开发依赖和指定数据。本任务未执行上游模型/下载测试。
- MiroFish 可选集成：业务目录 `python -m pytest MiroFish/backend/scripts/test_profile_format.py`，须先按该组件说明配置依赖和资源。本任务未启用此外部集成。

不要把裸 `--collect-only`（未传 `tests`，可能搜到 MiroFish）与业务套件收集数字比较。真实行情/外部服务不属于本次隔离验收。

`tests/support/daily_delivery.py` 提取自 `r05_product/test_daily_research_delivery_boundaries.py` 的 `stamp/FixtureJobs/FixtureDelivery/identity/add_job/add_attempt/add_ready`，内容与语义保留；旧测试模块重导出以兼容已有导入。测试 node id 未迁移。其他共享辅助函数暂时保留原位置，避免与本次无关的大范围重排。

本次实际测试、既有失败、安装入口与限制以 [瘦身主报告](../docs/archive/stock-fish/docs/SLIMMING_REVIEW_AND_RESULT.md) 为准。
