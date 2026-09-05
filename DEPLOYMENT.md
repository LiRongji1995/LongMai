# Windows 部署与验收

本指南介绍当前 StockFish + Qlib 核心的 Windows 部署，并保留 2026-09-05 原生推理历史验收所用的模型和数据安装步骤。当前架构及输入要求见 [工程基线](stock-fish/ENGINEERING_BASELINE.md)。在其他空目录克隆本仓库不会切换已有服务使用的源码或虚拟环境。

## 1. 安装依赖

准备 Windows x64、Python 3.12、PowerShell 7 和 Git。下列命令在本仓库根目录执行，`$ProjectRoot` 始终表示当前检出目录。

```powershell
$ProjectRoot = (Get-Location).Path
py -3.12 -m venv .\stock-fish\.venv
$ProjectPython = Join-Path $ProjectRoot 'stock-fish\.venv\Scripts\python.exe'
& $ProjectPython -m pip install -r .\requirements-windows.lock.txt
& $ProjectPython -m pip check
& $ProjectPython -c 'import qlib; print(qlib.__version__)'
```

如果没有 Python Launcher，使用已安装 Python 3.12 的绝对路径代替 `py -3.12`。锁文件记录实际 Windows 运行环境，其中包含 `pyqlib==0.9.7` 和 `lightgbm==4.7.0`。依赖安装在项目虚拟环境内，不需要向全局 Python 安装应用包。

`qlib/` 是 Microsoft Qlib main 源码快照。本流程不执行 `pip install -e ./qlib`；原机源码构建因缺少 MSVC 14+ 失败，运行时 wheel 已完成验收。

## 2. 配置本地路径

```powershell
Copy-Item -LiteralPath .\stock-fish\.env.windows.example -Destination .\stock-fish\.env
New-Item -ItemType Directory -Force -Path .\data\cn_data, .\downloads, .\work\tmp, .\logs | Out-Null
```

只在首次配置时复制样例，已有 `.env` 应直接编辑。样例中的 `D:/StockRecommendation` 是部署示例，**新检出目录必须改成实际路径**。路径推荐用正斜杠；保留如下本地运行选项，并调整所有数据、缓存和临时目录：

```dotenv
HOST=127.0.0.1
PORT=8000
DEBUG=False
STOCK_BACKEND=advanced
QLIB_EXECUTION_MODE=native
QLIB_KERNELS=1
PYTHONUTF8=1
QLIB_DATA_DIR=D:/StockRecommendation/data/cn_data
QLIB_HOST_DATA_DIR=D:/StockRecommendation/data
QLIB_HOST_MLRUNS_DIR=D:/StockRecommendation/stock-fish/qlib-zh/mlruns
TEMP=D:/StockRecommendation/work/tmp
TMP=D:/StockRecommendation/work/tmp
XDG_CACHE_HOME=D:/StockRecommendation/downloads/cache
MPLCONFIGDIR=D:/StockRecommendation/downloads/cache/matplotlib
HF_HOME=D:/StockRecommendation/downloads/cache/huggingface
```

`.env` 被 Git 忽略。离线 Qlib 推理不需要 API Key；启用 LLM 时再填写 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL_NAME`。模型名需使用所选服务实际支持的值。Tushare、搜索、MiroFish/Zep 和 Bot 凭据按需配置，未配置状态不会等同于功能验收通过。

当前核心默认将 SQLite 写入 `stock-fish/data/stockfish.sqlite3`，将模型登记及量化产物写入 `stock-fish/data/core/`；可分别通过 `STOCKFISH_DB_PATH` 和 `STOCKFISH_STATE_DIR` 指定部署路径。数据库、登记的模型和产物属于运行数据，不随源码发布。单个 SQLite 数据库只运行一个应用任务服务，保持 `DEBUG=False`，不启用调试重载或多 worker。

## 3. 下载行情数据

历史验收使用 [chenditc/investment_data 的 2026-09-04 发布](https://github.com/chenditc/investment_data/releases/tag/2026-09-04)，属于第三方数据发布，不是 Microsoft 官方最新数据。

```powershell
Invoke-WebRequest -Uri 'https://github.com/chenditc/investment_data/releases/download/2026-09-04/qlib_bin.tar.gz' -OutFile .\downloads\qlib_bin-2026-09-04.tar.gz
(Get-FileHash -LiteralPath .\downloads\qlib_bin-2026-09-04.tar.gz -Algorithm SHA256).Hash
```

预期文件大小 **563,987,713 bytes**，SHA-256：

```text
958934555332e6cb759439753472394e3d1449dce51c195c59e70153a3acc2a4
```

确认摘要一致后，在空数据目录解压。归档顶层是 `qlib_bin/`，使用 `--strip-components=1` 去掉这一层：

```powershell
tar -xzf .\downloads\qlib_bin-2026-09-04.tar.gz -C .\data\cn_data --strip-components=1
```

解压后应直接存在 `data/cn_data/calendars/day.txt`、`data/cn_data/instruments/csi300.txt` 和 `data/cn_data/features/`。上述命令展开全包，会比原机仅提取 CSI300 历史成分和基准占用更多空间；本次运行只验证了 CSI300 子集，不能据此声称其他指数全部通过。

原机日历覆盖 **2000-01-04 至 2026-09-04**，最新 CSI300 成分为 300 只；证券实际覆盖区间可能不同。已读取 `SH600519` 在 2026-08-24 至 2026-09-04 的 `$open`、`$close`、`$volume`，共 10 个交易日。Qlib 的调整/归一化价格不能直接当作未复权人民币报价；独立 Baostock 查询得到 2026-09-04 收盘价 1330.00。

## 4. 下载已发布模型

使用 [StockFish CSI300 Alpha158 模型发布](https://github.com/freenowill/stock-fish/releases/tag/csi300-alpha158) 的实际资产：

```powershell
Invoke-WebRequest -Uri 'https://github.com/freenowill/stock-fish/releases/download/csi300-alpha158/csi300-alpha158-model.tar.gz' -OutFile .\downloads\csi300-alpha158-model.tar.gz
(Get-FileHash -LiteralPath .\downloads\csi300-alpha158-model.tar.gz -Algorithm SHA256).Hash
```

预期文件大小 **194,315,445 bytes**，SHA-256：

```text
30e373380b54fd0f712419bad8c12f45a39c6e026b6fb46eb5ae36085e90e4f5
```

确认摘要一致后，解压到模型发现目录，保留包内模型目录：

```powershell
New-Item -ItemType Directory -Force -Path .\stock-fish\qlib-zh\DATA\analysis_outputs | Out-Null
tar -xzf .\downloads\csi300-alpha158-model.tar.gz -C .\stock-fish\qlib-zh\DATA\analysis_outputs
```

实际模型目录应为：

```text
stock-fish/qlib-zh/DATA/analysis_outputs/2026-06-21-csi300-alpha158/
```

最新检查点位于其下：

```text
model_predict/walk_forward/2024-06-12/model_runs/lightgbm/mlflow_run/artifacts/params.pkl
```

该文件 SHA-256 为 `bc5480d90dbefc1d7611c3391b3c0d165f0b9bfdc99c7a3be4a066966c9268de`。模型为 Qlib `LGBModel` / LightGBM Booster，158 特征、250 棵树。模型目录还需保留保存的 dataset 和配置，原生入口会用它们校验特征表达式、顺序及处理器兼容性。

当前核心通过模型登记表读取模型，仅解压不会自动登记。确认上述已审阅发布资产的来源和摘要后，可在仓库根目录执行：

```powershell
& $ProjectPython .\stock-fish\scripts\core_admin.py import-reviewed-model .\stock-fish\qlib-zh\DATA\analysis_outputs\2026-06-21-csi300-alpha158 --expected-sha256 bc5480d90dbefc1d7611c3391b3c0d165f0b9bfdc99c7a3be4a066966c9268de
```

该历史模型登记为 `RESEARCH_ONLY`，不因此获得样本外或生产验证状态。正常推荐还需要符合 [数据契约](stock-fish/stockfish_core/DATA_CONTRACT.md) 的可信 RAW 快照；仅下载 Qlib binary 行情不足以提供完整 RAW 价格、时点和交易状态。用 `core_admin.py --help` 查看本地管理员的快照导入与激活命令，HTTP 请求只能引用已登记的 ID。

## 5. 启动、验证与停止

```powershell
pwsh -NoProfile -File .\Start-StockFish.ps1
Invoke-RestMethod 'http://127.0.0.1:8000/api/config'
Invoke-RestMethod 'http://127.0.0.1:8000/api/health'
Invoke-RestMethod 'http://127.0.0.1:8000/api/qlib/models'
```

打开 [Web 页面](http://127.0.0.1:8000/)。`/api/health` 分别报告 application、market_data、quant、model_registry、research、llm、tracker 和 jobs；服务启动成功不表示所有研究组件均可用，也不表示 `production_ready=true`。未登记模型、未选择可信快照或未配置 LLM transport 的组件会明确不可用。

模型列表来自登记表；量化任务保存候选，最终名单由统一推荐与风控流程产生。旧版“选择下载目录并直接回填 Top20”的历史验收不能作为当前页面操作保证。当前路由见 `stock-fish/stockfish_core/api.py`。

```powershell
pwsh -NoProfile -File .\Logs-StockFish.ps1 -Tail 80
pwsh -NoProfile -File .\Logs-StockFish.ps1 -Follow
pwsh -NoProfile -File .\Stop-StockFish.ps1
```

三个脚本的 `-Root` 默认为脚本所在目录。例如，可从另一检出目录显式管理原有部署：

```powershell
pwsh -NoProfile -File .\Logs-StockFish.ps1 -Root 'D:\StockRecommendation' -Tail 80
```

启动脚本校验虚拟环境、应用路径和端口，隐藏启动进程并写入独立日志，通过 `/api/health` 检查应用、历史存储和任务组件；重复启动会识别现有实例。停止脚本核对已登记 PID、创建时间和命令行，避免终止无关进程。进程登记文件和日志分别在 `<Root>/work/`、`<Root>/logs/`。默认端口为 8000；已有实例占用端口时不要启动第二个实例。管理脚本当前按这个默认端口检查服务。

任务状态及结果保存到 SQLite，发布结果与成功终态原子保存；启动恢复会把中断任务标为 `INTERRUPTED`，需要显式重试并创建新 ID。分析归档模块已实现，推荐历史及修订不可覆盖，后续评价单独追加。仍应在停止服务前等待任务结束，并为数据库和关联产物配置备份。

## 离线开发验证

在仓库根目录执行以下命令；`requirements-dev.txt` 提供测试依赖，新检出不需要原工作站的审阅目录：

```powershell
& $ProjectPython -m pip install -r .\stock-fish\requirements-dev.txt
& $ProjectPython .\stock-fish\scripts\run_core_tests.py tests -q
& $ProjectPython -m pip check
```

离线测试使用显式 Mock，不需要网络或密钥，不代替真实数据、模型、LLM 或部署验收。`capture_acceptance.py`、`verify_runtime.py` 和 `native_quant_smoke.py` 引用原工作站的输入，不是新检出的通用测试入口。

## 历史原生推理验收与当前边界

核心更新前，原机在 2026-09-05 使用 Python 3.12.14、pyqlib 0.9.7、LightGBM 4.7.0 完成依赖检查、Qlib CN 初始化及 **300 × 158** 特征生成。从浏览器新发起的推理约 96 秒完成，API 输出 20 只候选与评分，完整结果含 300 条唯一股票排名。当时健康报告为 0 错误，浏览器控制台无 error/warn。启动、停止、重启和重复启动也已实际验证。

[执行清单](docs/verification/inference-execution.json) 保存模型指纹、预测日、特征校验和 `trained=false`；其中路径已转换为仓库相对路径。此记录对应旧流程及原机环境，不代表当前核心或任意新机器安装后自动通过。

| 事项 | 当前影响 |
|---|---|
| Docker / Compose | 原机 Docker Desktop 因本地 socket 访问失败未启动；已采用原生推理 |
| 训练、微调、其他模型 | 历史原生验收没有执行训练；当前核心训练/重拟合、回测与评估的工程验证不等于投资样本外验证 |
| Qlib main 源码扩展构建 | 原机缺 MSVC 14+；不影响已安装的 0.9.7 wheel |
| LLM 与可选服务 | 缺密钥或服务未启动；未验收真实调用 |
| 分析归档与历史 | 模块已补齐，使用不可变 SQLite 记录；运行数据库及产物需在部署环境备份 |
| 数据范围 | 原机只展开 CSI300 历史相关股票与基准，日历截至 2026-09-04 |
| 长任务管理 | 当前使用持久任务状态；进程中断后显式重试，不自动接续执行 |

建议推理完成后再停止服务。当前服务仅监听本机回环地址，没有设置 Windows 自启动或外网监听。
