# 独立 daily / review 运行环境 V1

本入口只创建新的私有虚拟环境，不修补原 `.venv`，不改盘前 Python locator，不启动或切换服务，不迁移真实数据库。选股公式、配置、旧 run、Qlib 环境和模型不变。环境可用、工程测试、真实每日流程和投资有效性分别验收。

## 已锁定的范围

当前可复建目标为原生 **macOS x86_64、CPython 3.12**。其他操作系统、ARM 或 Rosetta 翻译运行会明确拒绝，不能直接复用本锁。Python 本体需调用者已有的安装；脚本不下载、升级或替换 Python。验收记录实际 Python 补丁版本、基础解释器路径及 SHA256。

`runtime-locks/daily-review-py312-macos-x86_64.json` 登记 58 个发行包及各自官方 PyPI 工件 URL/SHA256，两个 `.lock` 文件用于 pip 的 `--require-hashes`。原 `requirements-rules.txt`、`requirements-daily.txt`、`requirements-review.txt` 的直接版本号全部保留，并登记其文件哈希；任何变动要求显式重建锁，不会静默重新解析。

传递依赖首次解析于 2026-10-16，之后按确切版本和工件哈希安装。AkShare 的传递依赖 `jsonpath==0.82.2` 使用官方源码包；其源码哈希同样固定。构建工具另锁 `pip==26.2.1`、`setuptools==84.0.0`、`wheel==0.48.0` 和 `packaging==26.3`，先安装这些工具，再以 `--no-build-isolation` 构建，避免未登记的构建依赖。此处的可复建指相同包版本与已校验输入工件，不声称新建 venv 的绝对路径、安装时间和全部物理字节一致。

不安装 pyqlib、PyTorch、Live 的专用依赖或模型；这三个 profile 不代表 Qlib/Live 已就绪。旧项目环境中已有的包不能作为新环境的补充来源。

## 创建和验收

从仓库根执行。`PYTHON_312` 必须是明确选择的原生 CPython 3.12，`NEW_RUNTIME` 是尚不存在的私有目录；两者都不是仓库内硬编码路径。

```sh
"$PYTHON_312" -I -B stock-fish/scripts/bootstrap_daily_runtime.py create \
  --destination "$NEW_RUNTIME"
```

创建入口拒绝已有目录及符号链接，失败时保留新目录和诊断，不自动删除后重试。安装固定使用 HTTPS PyPI、哈希校验和关闭 pip 缓存，不继承 Python、pip、StockFish、Qlib 的路径或业务配置。安装后的每个发行包必须位于该 venv，版本集合必须与锁完全一致，禁 editable、用户 site 和外部 site-packages 混用。解释器身份及 WHEEL/RECORD 摘要保存到私有验收文件。

入口随后执行：

1. `pip check`。
2. `runtime_doctor.py` 的 `rules`、`daily`、`review`。每包最长 45 秒、每个 profile 总预算 240 秒，沿用 doctor 的写入/网络限制。
3. 真实终端入口 `scripts/stockfish.py --help`，不执行采集。
4. 全新 `TEST_FIXTURE` 目录中的 Flask 测试客户端、原子作业、SQLite 重开和不可变写入检查。没有 HTTP listener、调度线程、真实数据或模型；未启动调度器时 readiness 应为 503，不能伪报生产已就绪。

全部通过才输出 `ACCEPTED_TEST_FIXTURE_RUNTIME`。每一步的实际 argv、工作目录、起止时间、退出码、stdout/stderr 和 summary 保存在新环境的 `acceptance/<唯一编号>/`。命令超时也记录 `TIMED_OUT`、时限、已产生的输出和非零退出码，并终止该步自行创建的进程组。这些包含本机绝对路径，只保留私有，不提交源码仓库。

已有的新环境可以再次验证，不重新安装包或接管服务；每次产生新的验收目录：

```sh
"$NEW_RUNTIME/bin/python" -I -B stock-fish/scripts/bootstrap_daily_runtime.py verify \
  --destination "$NEW_RUNTIME"
```

## 在该环境运行工程测试

使用独立 runner，禁止追加旧环境 site-packages，也无需预载另一套 Pydantic。它检查包来源、关闭 pytest 插件自动加载和 pytest 缓存，只把本项目源码加入导入路径：

```sh
"$NEW_RUNTIME/bin/python" -I -B stock-fish/scripts/run_isolated_tests.py \
  tests/unit/test_runtime_bootstrap.py tests/unit/test_runtime_doctor.py -q
```

选择与改动相关的明确测试文件；所有 DB/state/artifact 使用隔离 fixture。旧 `run_core_tests.py` 的兼容性 fallback 保留给原工作流，这里不使用它的 `review/test-tools` 路径注入。

## 本次实际结果与边界

两个全新私有目录均以原生 x86_64 的 CPython 3.12.14 创建；各自完成 58 包安装、`pip check`、三个 doctor profile 及应用/CLI/独立 SQLite 烟测，最终验收均退出 0。两个环境的发行包名称与版本集合一致，第二次重建保留了全部 58 包的 WHEEL/RECORD 摘要。运行工具和既有 doctor 的定向检查为 36 passed、20 subtests passed，退出 0，包括真实子进程超时后留存部分输出的反例；其他业务回归由本轮对应交付报告列出。

首次创建时 baostock 冷导入超过 doctor 默认 15 秒，保留失败记录；随后在独立有界探测中 2.86 秒通过，验收入口明确采用上述冷启动预算，没有跳过该包或降低版本要求。系统 Python 3.10 的一次额外 PyPI 元数据读取因本机证书链失败，也没有据此关闭 TLS 校验；实际依赖解析和安装均由新 Python 3.12 环境的 pip 通过固定官方 HTTPS 源完成。

源码发布不代表运行实例已加载。默认旧环境、旧 locator 和已有运行服务仍保持原状；切换前还需明确的进程、路径、来源身份、真实只读查询和运行流程验收。交易数据可用性、每日连续交付、收益及市场优势均不由本环境检查证明。
