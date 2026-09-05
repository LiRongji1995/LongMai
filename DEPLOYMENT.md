# Windows 部署与验收

本指南对应 2026-09-05 验收的 StockFish + Qlib 原生推理部署。原有 `D:\StockRecommendation` 服务独立运行；在其他空目录克隆本仓库不会切换该服务使用的源码或虚拟环境。

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

## 3. 下载行情数据

本次使用 [chenditc/investment_data 的 2026-09-04 发布](https://github.com/chenditc/investment_data/releases/tag/2026-09-04)，属于第三方数据发布，不是 Microsoft 官方最新数据。

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

## 5. 启动、验证与停止

```powershell
pwsh -NoProfile -File .\Start-StockFish.ps1
Invoke-RestMethod 'http://127.0.0.1:8000/api/config'
Invoke-RestMethod 'http://127.0.0.1:8000/api/config/health'
Invoke-RestMethod 'http://127.0.0.1:8000/api/qlib/models'
```

打开 [Web 页面](http://127.0.0.1:8000/)，切换批量模式，启用 Qlib，选择 `2026-06-21-csi300-alpha158` 模型并开始运行。配置正确时页面显示进度，并在结束后回填候选股票。服务没有 Swagger UI；API 见 `stock-fish/app.py` 路由。

```powershell
pwsh -NoProfile -File .\Logs-StockFish.ps1 -Tail 80
pwsh -NoProfile -File .\Logs-StockFish.ps1 -Follow
pwsh -NoProfile -File .\Stop-StockFish.ps1
```

三个脚本的 `-Root` 默认为脚本所在目录。例如，可从另一检出目录显式管理原有部署：

```powershell
pwsh -NoProfile -File .\Logs-StockFish.ps1 -Root 'D:\StockRecommendation' -Tail 80
```

启动脚本校验虚拟环境、应用路径和端口，隐藏启动进程并写入独立日志；重复启动会识别现有实例。停止脚本核对已登记 PID、创建时间和命令行，避免终止无关进程。运行状态和日志分别在 `<Root>/work/`、`<Root>/logs/`。默认端口为 8000；已有实例占用端口时不要启动第二个实例。管理脚本当前按这个默认端口检查服务。

## 验收结果与边界

原机在 2026-09-05 使用 Python 3.12.14、pyqlib 0.9.7、LightGBM 4.7.0 完成依赖检查、Qlib CN 初始化及 **300 × 158** 特征生成。从浏览器新发起的推理约 96 秒完成，API 输出 20 只候选与评分，完整结果含 300 条唯一股票排名。最终健康报告为 0 错误，浏览器控制台无 error/warn。启动、停止、重启和重复启动也已实际验证。

[执行清单](docs/verification/inference-execution.json) 保存模型指纹、预测日、特征校验和 `trained=false`；其中路径已转换为仓库相对路径。此验收记录对应原机环境，并不代表任意新机器安装后自动通过。

| 尚未完成的部分 | 当前影响 |
|---|---|
| Docker / Compose | 原机 Docker Desktop 因本地 socket 访问失败未启动；已采用原生推理 |
| 训练、微调、其他模型 | 上游 runner 仍采用 Docker；此次没有训练或验证全部模型 |
| Qlib main 源码扩展构建 | 原机缺 MSVC 14+；不影响已安装的 0.9.7 wheel |
| LLM 与可选服务 | 缺密钥或服务未启动；未验收真实调用 |
| 分析归档 | 上游缺少 `memory/analysis/analysis_store.py`；已避免其阻断启动，归档仍不可用 |
| 数据范围 | 原机只展开 CSI300 历史相关股票与基准，日历截至 2026-09-04 |
| 长任务管理 | 现有输出读取不能严格保证 900 秒总时限；重启不会保留进行中的任务状态 |

建议推理完成后再停止服务。当前服务仅监听本机回环地址，没有设置 Windows 自启动或外网监听。
