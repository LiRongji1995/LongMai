# 测试入口与边界

从业务目录使用已安装 `requirements-dev.txt` 的独立 Python 环境运行：

```sh
"$TEST_PYTHON" -I -B scripts/run_isolated_tests.py tests -q --basetemp /全新私有/TEST_FIXTURE目录 --junitxml /私有目录/results.xml
```

runner 关闭第三方 pytest 插件自动加载、缓存及字节码；不要使用真实数据库、登记根、RAW 或历史成果作为测试输出。所有写入使用临时 DB/state/artifact，测试输入显式标记 TEST_FIXTURE。最终隔离验收另在操作系统层拒绝外网；仅本机回环服务夹具可以通信。网络采集、真实数据就绪与投资有效性不属于工程测试通过结论。

保留范围是量价 PV、主动 SAM V2、冻结 DAILY 对照、历史 R1/R2 结果与后验、证券和来源时间、UNKNOWN、保存结果只读、SHA/长度、存储原子性、防重账本、资源预算、进程监督、备份恢复及主命令行。`unit/test_personal_cli_workflow.py` 使用真实子进程走主入口研究、历史读取、导出和备份；`unit/test_codex_daily_entry_v2.py` 与盘前工作流夹具覆盖计划和完整流程。共享夹具在 `tests/support`。

移除功能专属的 Web/API、账户/模型、Go/Live、LLM、候选性能执行、旧包装器测试随实现删除。仍有效的业务断言已迁到直接 CandidateService 或唯一主入口，不能通过扩大 skip、降低覆盖门槛或删除失败样本制造通过。报告准确命令、退出码及未验证范围；真实行情只读检查单独报告。
