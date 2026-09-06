# R05 运行验收

| 检查 | 结果 | 日志 |
| --- | --- | --- |
| R04/R05/供应商/队列/主策略子集 | 通过，213 passed | `logs/r05-targeted-tests.log` |
| Python 编译检查 | 通过 | `logs/r05-compileall.log` |
| 前端 JS 语法检查 | 通过 | `logs/r05-node-check.log` |
| Git diff 空白检查 | 通过 | `logs/r05-git-diff-check.log` |
| 核心业务测试脚本 | 通过，665 passed in 166.55s | `logs/r05-core-test-runner.log` |
| 直接 `pytest -q` | 收集阻塞，旧 MiroFish 缺 `locales/languages.json` | `logs/r05-full-tests.log` |

真实数据运行：`actual-pv-capture-03`，请求 43/43 完成；当前主板合格池 3043，只 3039 只完成 PV 必需字段核验，缺失 4 只，最终入围 525 只。
