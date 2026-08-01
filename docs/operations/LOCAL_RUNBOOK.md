# Local Runbook：Native 验证

## 文档元数据

| 字段 | 值 |
| --- | --- |
| status | Draft |
| 目的 | 让不了解既有机器历史的开发者，从受控 Git source 完成 Python 基线检查、Native C# LEAN 基础验证和 Review Bundle 验证。 |
| 适用范围 | Phase 1 的本地、离线优先、合成 fixture 基础设施验证；不构成策略、回测盈利能力、Broker、Paper 或 Live 能力说明。 |
| 权威来源 | [AGENTS.md](../../AGENTS.md)、[Project Charter](../PROJECT_CHARTER.md)、[Risk Policy](../RISK_POLICY.md)、[Review Bundle Policy](../REVIEW_BUNDLE_POLICY.md)、[ADR 0002](../adr/0002-lean-native-source-foundation.md)、[Round 1B 正式报告](../reports/phase-01-round-01b-native-lean-foundation-report.md)、[toolchain lock](../../config/toolchain.lock.json) 和当前任务的显式边界。 |
| 当前实现状态 | Python 基线脚本、合成 fixture validator、C# smoke project、normalizer、Golden、manifest 与 Review Bundle generator 已存在；Native 流程仍由真实分步命令组成，没有统一 orchestrator。 |
| 更新触发条件 | Python/.NET/LEAN 版本、锁定 commit、环境变量、fixture、Golden、normalizer、Launcher 配置、检查脚本或 Review Bundle CLI 变化。 |
| owner role | LEAN Integration Owner；Python 门禁由 QA Owner；Bundle 步骤由 Review Bundle Maintainer。 |
| 失败行为 | 任一版本、commit、hash、配置、exit code、timeout、Golden、最终持仓或 Git gate 不一致即 fail closed；不得跳过、伪造、覆盖 Golden 或转入下一 gate。 |
| 测试或验证要求 | 本文命令必须对应仓库真实文件；Python 廉价检查、Git gate、fixture/hash、Native evidence 与 Bundle validation 分别记录实际命令、exit code 和输出。 |

实现状态必须按以下三类阅读：

- `IMPLEMENTED`：仓库当前真实存在的 Python 检查脚本、fixture validator、C# smoke project、normalizer、Golden、manifest 与 Review Bundle generator。
- `CONTRACT_DECISION`：本文规定的环境变量、fail-closed gate 和真实分步验证流程；它们不等于统一 orchestrator 已实现。
- `TBD`：文末 register 列出的未实现或未确认能力；不得以手工假设伪装成支持。

## 1. 安全边界

当前固定安全状态为：

- PAPER_TRADING_ENABLED=false
- LIVE_TRADING_ENABLED=false
- LIVE_ORDER_SUBMISSION_ALLOWED=false
- Round 1B 的 Phase01DeterministicSmokeTest 是 infrastructure smoke test，不是盈利策略。
- Execution Intent 不是真实 order；本流程不连接 Broker，不下载真实行情，不发送真实订单。
- LLM 不参与实时 signal、risk decision 或 order submission。
- 本 Round 1C 只编写文档，没有重新运行 Native Run C 或 deterministic A/B build；不得把 Round 1B 证据冒充本 Round 新证据。

所有 runtime、data、results 和 NuGet 内容都位于 Git 外部。开始前应让目标目录为新目录或空目录；若已有未知内容，立即停止并选择新的受控目录。本文不提供目录删除命令。

## 2. 路径和环境变量合同

只使用下列 filesystem root；文档、证据和命令不得记录其机器实际值。

| 环境变量 | 语义 | 必要条件 |
| --- | --- | --- |
| PROJECT_ROOT | 本项目 Git checkout | main branch；正式 Bundle 时 HEAD 与 origin/main 相同且工作树 clean |
| LEAN_SOURCE_ROOT | 官方 QuantConnect/Lean Git checkout | detached at toolchain lock 的 commit；tracked source clean |
| LEAN_DOTNET_ROOT | 隔离的 Launcher runtime copy | 新目录或空目录；不等同于 LEAN source |
| LEAN_DATA_ROOT | Run C 的合成数据和静态 support data | 新目录或空目录；不得含真实市场数据 |
| LEAN_RESULTS_ROOT | A/B build、Run C、clean-source 和 evidence 输出根 | Git 外部或 Git-ignored；不得提交 |
| NUGET_PACKAGES | 机器本地 NuGet cache | 不得提交；restore 后可用于离线 build |
| PYTHON312 | Python 3.12 interpreter | 必须输出 3.12.x |

PowerShell 预检：

~~~powershell
$ErrorActionPreference = 'Stop'
$required = @(
    'PROJECT_ROOT',
    'LEAN_SOURCE_ROOT',
    'LEAN_DOTNET_ROOT',
    'LEAN_DATA_ROOT',
    'LEAN_RESULTS_ROOT',
    'NUGET_PACKAGES',
    'PYTHON312'
)
foreach ($name in $required) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Required environment variable is unset: $name"
    }
}
Set-Location -LiteralPath $env:PROJECT_ROOT
~~~

环境变量只保存 path，不保存 credential。任何 Secret 均不得写入 shell history、config、日志、evidence 或 Git。

## 3. 获取项目和 Git 同步 gate

首次获取项目时，PROJECT_GIT_URL 是经批准的 remote URL，不是 filesystem path：

~~~powershell
git clone $env:PROJECT_GIT_URL $env:PROJECT_ROOT
Set-Location -LiteralPath $env:PROJECT_ROOT
~~~

已有 checkout 必须执行：

~~~powershell
Set-Location -LiteralPath $env:PROJECT_ROOT
git fetch origin --prune

$branch = (git branch --show-current).Trim()
$head = (git rev-parse HEAD).Trim()
$originMain = (git rev-parse origin/main).Trim()
$aheadBehind = (git rev-list --left-right --count HEAD...origin/main).Trim()
$status = git status --porcelain
$expectedAheadBehind = '0' + [char]9 + '0'

if ($branch -ne 'main') { throw "Expected main, got $branch" }
if ($status) { throw 'Working tree is not clean.' }
if ($head -ne $originMain) { throw 'HEAD does not match origin/main.' }
if ($aheadBehind -ne $expectedAheadBehind) { throw "Unexpected ahead/behind: $aheadBehind" }

git merge-base --is-ancestor b9874e26240f8dfffdbf3ff2d270ffae9ccab92a HEAD
if ($LASTEXITCODE -ne 0) { throw 'Round 1B frozen commit is not an ancestor of HEAD.' }
~~~

开发中的普通检查可以在 feature state 执行，但正式 Round evidence 与 Review Bundle 必须满足上述 main、clean、origin/main gate。

## 4. 强制阅读 gate

写入或执行验证前，依次阅读：

1. AGENTS.md
2. README.md
3. docs/PROJECT_CHARTER.md
4. docs/ROADMAP.md
5. docs/ARCHITECTURE.md
6. docs/RISK_POLICY.md
7. docs/DATA_GOVERNANCE.md
8. docs/TESTING_STRATEGY.md
9. docs/SECURITY.md
10. docs/REVIEW_BUNDLE_POLICY.md
11. docs/adr 下全部现有 ADR
12. docs/reports/phase-01-round-01b-native-lean-foundation-report.md
13. integrations/lean 下的 README、fixture manifest、Golden、run manifest、algorithm identity 和相关 scripts/tests
14. 当前用户 prompt 的 baseline 与 boundary

仓库文件可用以下 PowerShell 强制打开；当前 prompt 必须在任务界面另行完整阅读：

~~~powershell
$requiredDocs = @(
    'AGENTS.md',
    'README.md',
    'docs/PROJECT_CHARTER.md',
    'docs/ROADMAP.md',
    'docs/ARCHITECTURE.md',
    'docs/RISK_POLICY.md',
    'docs/DATA_GOVERNANCE.md',
    'docs/TESTING_STRATEGY.md',
    'docs/SECURITY.md',
    'docs/REVIEW_BUNDLE_POLICY.md',
    'docs/reports/phase-01-round-01b-native-lean-foundation-report.md',
    'integrations/lean/README.md',
    'integrations/lean/data/fixture-manifest.json',
    'integrations/lean/expected/phase01-golden-orders.json',
    'integrations/lean/expected/phase01-golden-statistics.json',
    'integrations/lean/expected/phase01-run-manifest.json',
    'integrations/lean/expected/algorithm-build-identity.json'
)
foreach ($relative in $requiredDocs) {
    $path = Join-Path $env:PROJECT_ROOT $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required material is missing: $relative"
    }
    Get-Content -LiteralPath $path -Raw | Out-Host
}
Get-ChildItem -LiteralPath (Join-Path $env:PROJECT_ROOT 'docs/adr') -File |
    Sort-Object Name |
    ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw | Out-Host }
~~~

缺失材料是 stop-and-report condition。

## 5. Python 3.12 与项目基线

scripts/bootstrap.ps1 是真实脚本，但它要求 Windows py launcher。优先使用 PYTHON312，以免把不存在的 py launcher 当成前提：

~~~powershell
$version = (& $env:PYTHON312 --version 2>&1).Trim()
if ($version -notmatch '^Python 3\.12\.\d+$') {
    throw "Expected Python 3.12.x, got $version"
}

Set-Location -LiteralPath $env:PROJECT_ROOT
& $env:PYTHON312 -m venv (Join-Path $env:PROJECT_ROOT '.venv')
$projectPython = Join-Path $env:PROJECT_ROOT '.venv/Scripts/python.exe'
& $projectPython -m pip install --upgrade pip
& $projectPython -m pip install -e '.[dev]'
~~~

若 py launcher 已确认映射 Python 3.12，也可使用真实入口：

~~~powershell
& (Join-Path $env:PROJECT_ROOT 'scripts/bootstrap.ps1')
~~~

依赖获取与 verification network isolation 是不同阶段。当前脚本不自动证明网络已禁用；正式 evidence 只能在实际关闭网络访问后记录 network_disabled=true。

## 6. .NET SDK 10.0.302 和官方 LEAN source

版本和 commit 以 config/toolchain.lock.json 为权威：

~~~powershell
Set-Location -LiteralPath $env:PROJECT_ROOT
$toolchain = Get-Content -LiteralPath 'config/toolchain.lock.json' -Raw | ConvertFrom-Json
$leanCommit = [string]$toolchain.lean.commit
$dotnetVersion = (dotnet --version).Trim()

if ($dotnetVersion -ne [string]$toolchain.dotnet_sdk_version) {
    throw "Expected .NET SDK $($toolchain.dotnet_sdk_version), got $dotnetVersion"
}
if ($leanCommit -ne '0269115d3cfbf691c7a0b7cfcc9ed412cafb91f6') {
    throw "Unexpected toolchain lock LEAN commit: $leanCommit"
}
~~~

首次获取官方 source：

~~~powershell
git clone $toolchain.lean.repository $env:LEAN_SOURCE_ROOT
git -C $env:LEAN_SOURCE_ROOT checkout --detach $leanCommit
~~~

已有 source：

~~~powershell
git -C $env:LEAN_SOURCE_ROOT fetch origin $leanCommit
git -C $env:LEAN_SOURCE_ROOT checkout --detach $leanCommit

$actualLean = (git -C $env:LEAN_SOURCE_ROOT rev-parse HEAD).Trim()
$leanStatus = git -C $env:LEAN_SOURCE_ROOT status --porcelain
if ($actualLean -ne $leanCommit) { throw 'LEAN commit mismatch.' }
if ($leanStatus) { throw 'LEAN source has tracked or untracked changes.' }
~~~

恢复和编译官方 solution：

~~~powershell
$leanSolution = Join-Path $env:LEAN_SOURCE_ROOT 'QuantConnect.Lean.sln'
dotnet restore $leanSolution
if ($LASTEXITCODE -ne 0) { throw 'LEAN restore failed.' }
dotnet build $leanSolution --configuration Release --no-restore
if ($LASTEXITCODE -ne 0) { throw 'LEAN Release build failed.' }
~~~

NUGET_PACKAGES 由 dotnet 自动读取。restore 可能需要网络；后续 offline build 不得把 restore 的网络访问误记为离线验证。

## 7. 提交的 synthetic fixture、map 和 factor 验证

scripts/generate_phase01_synthetic_data.py 中的 validate_fixture 是当前真实 validator。以下命令同时检查 daily ZIP 内容、严格递增且唯一的日期、OHLCV 基本约束，以及 daily/map/factor 三个文件的 SHA 与提交 manifest 完全一致：

~~~powershell
Set-Location -LiteralPath $env:PROJECT_ROOT
& $env:PYTHON312 -c "import json; from pathlib import Path; from scripts.generate_phase01_synthetic_data import validate_fixture; root=Path('integrations/lean/data'); actual=validate_fixture(root); expected=json.loads((root/'fixture-manifest.json').read_text(encoding='utf-8')); assert actual == expected, {'expected': expected, 'actual': actual}; print(json.dumps(actual, indent=2, sort_keys=True))"
if ($LASTEXITCODE -ne 0) { throw 'Fixture, map, factor, or manifest validation failed.' }
~~~

该 validator 只接受仓库中的 SYNTHETIC TEST DATA。它不证明实时行情、完整 point-in-time database 或 corporate action 能力。

fixture regeneration 不是日常验证步骤。现有 generator 还要求隔离 ToolBox runtime，当前没有仅依赖本 Runbook 七个 path root 的统一 regeneration 流程；见 TBD register。

## 8. C# smoke algorithm 编译

真实 project 是 integrations/lean/algorithms/Phase01DeterministicSmokeTest.csproj：

~~~powershell
Set-Location -LiteralPath $env:PROJECT_ROOT
$algorithmProject = Join-Path $env:PROJECT_ROOT 'integrations/lean/algorithms/Phase01DeterministicSmokeTest.csproj'
$leanProperty = "-p:LeanSourceRoot=$env:LEAN_SOURCE_ROOT"

dotnet restore $algorithmProject $leanProperty
if ($LASTEXITCODE -ne 0) { throw 'Algorithm restore failed.' }
dotnet build $algorithmProject --configuration Release --no-restore $leanProperty
if ($LASTEXITCODE -ne 0) { throw 'Algorithm Release build failed.' }
~~~

这一步只证明 C# infrastructure smoke assembly 能编译，不证明 Python algorithm、strategy 或 profitability。

## 9. Deterministic A/B build 与 DLL SHA

仓库没有 deterministic-build 统一命令。真实分步边界是从同一个 committed HEAD 建立两个独立 source clone，分别 restore/build，再比较 DLL：

~~~powershell
Set-Location -LiteralPath $env:PROJECT_ROOT
$head = (git rev-parse HEAD).Trim()
$sourceA = Join-Path $env:LEAN_RESULTS_ROOT 'deterministic-source-a'
$sourceB = Join-Path $env:LEAN_RESULTS_ROOT 'deterministic-source-b'

git clone --no-hardlinks --no-local $env:PROJECT_ROOT $sourceA
git clone --no-hardlinks --no-local $env:PROJECT_ROOT $sourceB
git -C $sourceA checkout --detach $head
git -C $sourceB checkout --detach $head

foreach ($source in @($sourceA, $sourceB)) {
    $project = Join-Path $source 'integrations/lean/algorithms/Phase01DeterministicSmokeTest.csproj'
    dotnet restore $project "-p:LeanSourceRoot=$env:LEAN_SOURCE_ROOT"
    if ($LASTEXITCODE -ne 0) { throw "Restore failed for $source" }
    dotnet build $project --configuration Release --no-restore "-p:LeanSourceRoot=$env:LEAN_SOURCE_ROOT"
    if ($LASTEXITCODE -ne 0) { throw "Build failed for $source" }
}

$dllA = Join-Path $sourceA 'integrations/lean/algorithms/bin/Release/net10.0/Phase01DeterministicSmokeTest.dll'
$dllB = Join-Path $sourceB 'integrations/lean/algorithms/bin/Release/net10.0/Phase01DeterministicSmokeTest.dll'
$hashA = (Get-FileHash -LiteralPath $dllA -Algorithm SHA256).Hash.ToLowerInvariant()
$hashB = (Get-FileHash -LiteralPath $dllB -Algorithm SHA256).Hash.ToLowerInvariant()
$identity = Get-Content -LiteralPath (Join-Path $sourceA 'integrations/lean/expected/algorithm-build-identity.json') -Raw | ConvertFrom-Json

if ($hashA -ne $hashB) { throw "A/B DLL SHA mismatch: $hashA != $hashB" }
if ($hashA -ne [string]$identity.deterministic_build_a_b_dll_sha256) {
    throw 'DLL SHA does not match algorithm-build-identity.json.'
}
$hashA
~~~

禁止用复制同一个 DLL 两次代替两次 build。任一 mismatch 都是 FAIL，不得更新 identity 或 Golden 来掩盖差异。本 Round 1C 未执行本节。

## 10. 隔离 Launcher runtime 和 data copy

仓库没有 Launcher runtime-copy script。真实分步流程如下，并要求目标目录为空：

~~~powershell
$launcherSource = Join-Path $env:LEAN_SOURCE_ROOT 'Launcher/bin/Release/net10.0'
if (-not (Test-Path -LiteralPath (Join-Path $launcherSource 'QuantConnect.Lean.Launcher.dll') -PathType Leaf)) {
    throw 'Pinned LEAN Launcher output is missing.'
}

foreach ($target in @($env:LEAN_DOTNET_ROOT, $env:LEAN_DATA_ROOT)) {
    if (Test-Path -LiteralPath $target) {
        if (Get-ChildItem -LiteralPath $target -Force | Select-Object -First 1) {
            throw "Target must be empty: $target"
        }
    } else {
        New-Item -ItemType Directory -Path $target | Out-Null
    }
}

Copy-Item -Path (Join-Path $launcherSource '*') -Destination $env:LEAN_DOTNET_ROOT -Recurse
Copy-Item -LiteralPath $dllA -Destination (Join-Path $env:LEAN_DOTNET_ROOT 'Phase01DeterministicSmokeTest.dll')

Copy-Item -Path (Join-Path $env:PROJECT_ROOT 'integrations/lean/data/equity') -Destination $env:LEAN_DATA_ROOT -Recurse
New-Item -ItemType Directory -Path (Join-Path $env:LEAN_DATA_ROOT 'market-hours') | Out-Null
New-Item -ItemType Directory -Path (Join-Path $env:LEAN_DATA_ROOT 'symbol-properties') | Out-Null
Copy-Item -LiteralPath (Join-Path $env:LEAN_SOURCE_ROOT 'Data/market-hours/market-hours-database.json') -Destination (Join-Path $env:LEAN_DATA_ROOT 'market-hours/market-hours-database.json')
Copy-Item -LiteralPath (Join-Path $env:LEAN_SOURCE_ROOT 'Data/symbol-properties/symbol-properties-database.csv') -Destination (Join-Path $env:LEAN_DATA_ROOT 'symbol-properties/symbol-properties-database.csv')
~~~

market-hours 和 symbol-properties 来自已核对 commit 的官方 source；其 provenance 与 SHA 必须进入外部 execution evidence。LEAN source、runtime copy 和 result output 均不得进入 Review Bundle source。

## 11. 本地安全 config

仓库当前没有 Launcher config generator。不得引用不存在的 project_run_c_config.py 或虚构 --config 参数。Launcher 从 LEAN_DOTNET_ROOT 下的 config.json 读取配置，因此使用下面的本地-only、纯 backtesting 配置生成步骤：

~~~powershell
$runRoot = Join-Path $env:LEAN_RESULTS_ROOT 'run-c'
if (Test-Path -LiteralPath $runRoot) {
    if (Get-ChildItem -LiteralPath $runRoot -Force | Select-Object -First 1) {
        throw 'Run C result target must be empty.'
    }
} else {
    New-Item -ItemType Directory -Path $runRoot | Out-Null
}

$configPath = Join-Path $env:LEAN_DOTNET_ROOT 'config.json'
@'
import json
import os
import sys
from pathlib import Path

target = Path(sys.argv[1])
data_root = str(Path(os.environ["LEAN_DATA_ROOT"]).resolve())
results_root = str((Path(os.environ["LEAN_RESULTS_ROOT"]) / "run-c").resolve())
payload = {
    "environment": "backtesting",
    "algorithm-id": "phase01-run-c",
    "algorithm-type-name": "Phase01DeterministicSmokeTest",
    "algorithm-language": "CSharp",
    "algorithm-location": "Phase01DeterministicSmokeTest.dll",
    "data-folder": data_root,
    "results-destination-folder": results_root,
    "close-automatically": True,
    "debugging": False,
    "forward-console-messages": False,
    "log-handler": "QuantConnect.Logging.CompositeLogHandler",
    "messaging-handler": "QuantConnect.Messaging.Messaging",
    "job-queue-handler": "QuantConnect.Queues.JobQueue",
    "api-handler": "QuantConnect.Api.Api",
    "map-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskMapFileProvider",
    "factor-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskFactorFileProvider",
    "data-provider": "QuantConnect.Lean.Engine.DataFeeds.DefaultDataProvider",
    "data-channel-provider": "DataChannelProvider",
    "object-store": "QuantConnect.Lean.Engine.Storage.LocalObjectStore",
    "data-aggregator": "QuantConnect.Lean.Engine.DataFeeds.AggregationManager",
    "show-missing-data-logs": True,
    "force-exchange-always-open": False,
    "parameters": {},
    "environments": {
        "backtesting": {
            "live-mode": False,
            "setup-handler": "QuantConnect.Lean.Engine.Setup.BacktestingSetupHandler",
            "result-handler": "QuantConnect.Lean.Engine.Results.BacktestingResultHandler",
            "data-feed-handler": "QuantConnect.Lean.Engine.DataFeeds.FileSystemDataFeed",
            "real-time-handler": "QuantConnect.Lean.Engine.RealTime.BacktestingRealTimeHandler",
            "history-provider": [
                "QuantConnect.Lean.Engine.HistoricalData.SubscriptionDataReaderHistoryProvider"
            ],
            "transaction-handler": (
                "QuantConnect.Lean.Engine.TransactionHandlers."
                "BacktestingTransactionHandler"
            ),
        }
    },
}
target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(target)
'@ | & $env:PYTHON312 - $configPath
if ($LASTEXITCODE -ne 0) { throw 'Safe config generation failed.' }
~~~

运行前必须重新解析 config 并断言：

~~~powershell
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
if ($config.environment -ne 'backtesting') { throw 'Unsafe environment.' }
if ($config.environments.backtesting.'live-mode' -ne $false) { throw 'live-mode must be false.' }
if ($config.'algorithm-language' -ne 'CSharp') { throw 'Unexpected algorithm language.' }
if ($config.'algorithm-type-name' -ne 'Phase01DeterministicSmokeTest') { throw 'Unexpected algorithm.' }
$expectedDataRoot = [IO.Path]::GetFullPath($env:LEAN_DATA_ROOT)
$expectedRunRoot = [IO.Path]::GetFullPath($runRoot)
if ([IO.Path]::GetFullPath([string]$config.'data-folder') -ne $expectedDataRoot) {
    throw 'Unexpected data root.'
}
if ([IO.Path]::GetFullPath([string]$config.'results-destination-folder') -ne $expectedRunRoot) {
    throw 'Unexpected results root.'
}

$env:PAPER_TRADING_ENABLED = 'false'
$env:LIVE_TRADING_ENABLED = 'false'
$env:LIVE_ORDER_SUBMISSION_ALLOWED = 'false'
~~~

config 不得包含 credential、live environment 或 brokerage handler。生成文件和实际 SHA 只能作为本地 evidence，不得提交。

## 12. Run C：timeout、stdout、stderr 和 exit code

仓库没有 Run C orchestrator。以下真实 Python subprocess 边界从隔离 runtime 启动 QuantConnect.Lean.Launcher.dll，分别记录 stdout、stderr、exit code、开始/结束时间和 timeout。runTimeoutSeconds 是本次运行明确记录的本地 guard；项目尚未批准统一阈值。

~~~powershell
$runTimeoutSeconds = 600
if ($runTimeoutSeconds -le 0) { throw 'Timeout guard must be positive.' }
$env:RUN_TIMEOUT_SECONDS = [string]$runTimeoutSeconds

@'
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

runtime = Path(os.environ["LEAN_DOTNET_ROOT"])
results = Path(os.environ["LEAN_RESULTS_ROOT"]) / "run-c"
timeout_seconds = int(os.environ["RUN_TIMEOUT_SECONDS"])
stdout_path = results / "launcher.stdout.txt"
stderr_path = results / "launcher.stderr.txt"
record_path = results / "launcher-execution.json"
started = datetime.now(timezone.utc)
timed_out = False
exit_code = 1
try:
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        completed = subprocess.run(
            ["dotnet", "QuantConnect.Lean.Launcher.dll"],
            cwd=runtime,
            stdout=stdout,
            stderr=stderr,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        exit_code = completed.returncode
except subprocess.TimeoutExpired:
    timed_out = True
    exit_code = 124
ended = datetime.now(timezone.utc)
record = {
    "run_start_time_utc": started.isoformat(),
    "run_end_time_utc": ended.isoformat(),
    "duration_seconds": (ended - started).total_seconds(),
    "timeout_seconds": timeout_seconds,
    "timed_out": timed_out,
    "launcher_exit_code": exit_code,
    "stdout_file": stdout_path.name,
    "stderr_file": stderr_path.name,
}
record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(record, sort_keys=True))
raise SystemExit(exit_code)
'@ | & $env:PYTHON312 -
if ($LASTEXITCODE -ne 0) { throw "Run C failed or timed out with exit code $LASTEXITCODE" }
~~~

成功必须同时满足：没有 timeout、Launcher exit code 为 0、raw order-events 文件存在、日志无 runtime error、后续 normalizer/Golden/final holdings 全部通过。summary JSON 曾出现编码/完整性问题，不能单独作为 order evidence。

本 Round 1C 不执行本节。

## 13. Normalizer、Golden 和最终持仓

真实 normalizer CLI 是 scripts/normalize_lean_result.py：

仓库当前没有把 Launcher 结果导出为独立 raw order-events JSON 的命令；下列文件名只是本地 evidence 约定。必须由经审阅的 Run C evidence collector 产生该文件，否则在此停止，不能从可能损坏的 summary 猜测或伪造输入。

~~~powershell
Set-Location -LiteralPath $env:PROJECT_ROOT
$fixtureManifest = Get-Content -LiteralPath 'integrations/lean/data/fixture-manifest.json' -Raw | ConvertFrom-Json
$fixtureSha = [string]$fixtureManifest.files.'equity/usa/daily/synth.zip'
$rawOrders = Join-Path $runRoot 'phase01-run-c-order-events.json'
if (-not (Test-Path -LiteralPath $rawOrders -PathType Leaf)) {
    throw 'Authoritative Run C raw order-events export is missing; exporter is TBD.'
}
$normalizerArgs = @(
    'scripts/normalize_lean_result.py',
    '--orders', $rawOrders,
    '--output-dir', $runRoot,
    '--lean-commit', $leanCommit,
    '--fixture-sha', $fixtureSha,
    '--algorithm-source', 'integrations/lean/algorithms/Phase01DeterministicSmokeTest.cs'
)
& $env:PYTHON312 @normalizerArgs
if ($LASTEXITCODE -ne 0) { throw 'Normalizer failed.' }
~~~

normalizer 会拒绝不是两个 Filled event 的输入，并在最终 holdings 非零时失败。

仓库没有 Golden compare CLI；真实 compare_semantic 函数位于同一 script。以下步骤分别比较 orders/statistics 并显式检查最终持仓：

~~~powershell
@'
import json
import os
from pathlib import Path
from scripts.normalize_lean_result import compare_semantic

root = Path(os.environ["PROJECT_ROOT"])
results = Path(os.environ["LEAN_RESULTS_ROOT"]) / "run-c"
pairs = (
    (
        root / "integrations/lean/expected/phase01-golden-orders.json",
        results / "normalized-orders.json",
    ),
    (
        root / "integrations/lean/expected/phase01-golden-statistics.json",
        results / "normalized-statistics.json",
    ),
)
for expected_path, actual_path in pairs:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    actual = json.loads(actual_path.read_text(encoding="utf-8"))
    compare_semantic(expected, actual)
statistics = json.loads((results / "normalized-statistics.json").read_text(encoding="utf-8"))
if float(statistics["final_holdings"]) != 0.0:
    raise SystemExit("final_holdings is not zero")
print("Golden orders: PASS")
print("Golden statistics: PASS")
print("final_holdings: 0")
'@ | & $env:PYTHON312 -
if ($LASTEXITCODE -ne 0) { throw 'Golden or final holdings validation failed.' }
~~~

Golden 只验证固定 smoke semantics；不得从 smoke 现金或组合数值得出 profitability 声明。

## 14. Manifest 与 evidence 绑定

必须分别绑定：

- integrations/lean/data/fixture-manifest.json：fixture、map、factor、seed、LEAN commit 与文件 SHA。
- integrations/lean/expected/algorithm-build-identity.json：algorithm source、csproj、SDK、LEAN assembly、deterministic DLL SHA。
- integrations/lean/expected/phase01-run-manifest.json：冻结的 Run A/B smoke 结果；它不是新 Run C 的替代证据。
- Run C 本地 evidence：safe config SHA、runtime DLL SHA、support-data provenance/SHA、raw order-events SHA、normalized files SHA、stdout/stderr、exit code、timeout、实际 interval 和开始/结束时间。
- 正式 backtest evidence 未来还必须绑定 Git commit、configuration、data manifest、interval、cost model、strategy/risk version；当前 smoke 不是正式策略 backtest。

可用 Get-FileHash -Algorithm SHA256 计算本地文件 SHA。任何 manifest 与实际 bytes 不一致都必须失败，不能修改预期文件来使检查通过。

## 15. Python 廉价检查、coverage、build 和 CLI

Windows 的完整真实入口是 scripts/check.ps1，要求 PROJECT_ROOT/.venv 已存在：

~~~powershell
Set-Location -LiteralPath $env:PROJECT_ROOT
& (Join-Path $env:PROJECT_ROOT 'scripts/check.ps1')
~~~

该脚本依次执行：

~~~powershell
& $projectPython -m ruff check .
& $projectPython -m ruff format --check .
& $projectPython -m mypy src
& $projectPython -m pytest
& $projectPython -m build
& $projectPython -m quant_system.cli status
git diff --check
~~~

pyproject.toml 已为 python -m pytest 配置 --cov=quant_system 和 --cov-fail-under=90，因此普通 pytest 命令就是当前 coverage gate。需要可读明细时可额外运行：

~~~powershell
& $projectPython -m pytest --cov-report=term-missing
~~~

当前 CLI 只有离线 status；预期输出必须包含 paper trading enabled: false 与 live trading enabled: false。它当前仍报告实现常量 CURRENT_PHASE=Phase 0，不能伪称 CLI 已进入 Phase 1。

Makefile 的 check target 不含 build、CLI 或 git diff --check，不能单独作为完整 acceptance。CI 包含 Ruff、format、mypy、pytest、build 和 CLI，但不包含 Native LEAN、Bundle 或显式 clean-source 流程。

## 16. Clean-source validation

仓库没有 clean-source runner。真实分步边界是在 LEAN_RESULTS_ROOT 下建立新的 committed-source clone，不删除旧目录，并在该 clone 中运行现有检查：

~~~powershell
Set-Location -LiteralPath $env:PROJECT_ROOT
$head = (git rev-parse HEAD).Trim()
$cleanSource = Join-Path $env:LEAN_RESULTS_ROOT 'clean-source'
git clone --no-hardlinks --no-local $env:PROJECT_ROOT $cleanSource
git -C $cleanSource checkout --detach $head

Set-Location -LiteralPath $cleanSource
& $env:PYTHON312 -m venv (Join-Path $cleanSource '.venv')
$cleanPython = Join-Path $cleanSource '.venv/Scripts/python.exe'
& $cleanPython -m pip install --upgrade pip
& $cleanPython -m pip install -e '.[dev]'
& (Join-Path $cleanSource 'scripts/check.ps1')

if ((git rev-parse HEAD).Trim() -ne $head) { throw 'Clean-source HEAD mismatch.' }
if (git status --porcelain) { throw 'Clean-source worktree is not clean.' }
Set-Location -LiteralPath $env:PROJECT_ROOT
~~~

正式 evidence 必须记录依赖准备是否访问网络，并在 verification 阶段真实满足 network_disabled 要求。当前没有跨平台自动隔离网络的仓库脚本，见 TBD register。

## 17. Review Bundle 生成

Review Bundle: REQUIRED，因为 Round 1C 将提交外部独立审阅且 clean-source 是 acceptance criterion。

真实 CLI 必须包含 --evidence-json；README 中缺少该参数的旧示例不能直接复制。生成前必须：

- Round 1C report 已提交并由 HEAD 跟踪。
- branch 为 main，working tree clean，HEAD 等于 origin/main。
- Base commit 是 b9874e26240f8dfffdbf3ff2d270ffae9ccab92a 且为 HEAD 祖先。
- evidence 是 UTF-8 schema_version 1，所有实际 command、exit code、pytest count/coverage、Git 和文档验证结果均真实。
- document evidence 记录六份 created_documents 及 SHA-256、relative links、JSON examples、absolute path scan、Secret scan、cross-document consistency、Python checks 和 Git checks。
- 不传 Round 1B 专用 Run C artifact 参数，不复用 Round 1B evidence。

PowerShell：

~~~powershell
Set-Location -LiteralPath $env:PROJECT_ROOT
$roundStartTime = '<recorded-ISO-8601-UTC-round-start-time>'
$evidencePath = Join-Path $env:LEAN_RESULTS_ROOT 'phase-01-round-01c-evidence.json'
$bundleOutput = Join-Path $env:PROJECT_ROOT 'artifacts/review-bundles'
$bundleArgs = @(
    '--round-id', 'phase-01-round-01c-documentation-interface-contract-baseline',
    '--base-commit', 'b9874e26240f8dfffdbf3ff2d270ffae9ccab92a',
    '--output-dir', $bundleOutput,
    '--report-path', 'docs/reports/phase-01-round-01c-documentation-interface-contract-baseline-report.md',
    '--round-start-time', $roundStartTime,
    '--evidence-json', $evidencePath
)
& $env:PYTHON312 scripts/build_review_bundle.py @bundleArgs
if ($LASTEXITCODE -ne 0) { throw 'Review Bundle generation failed.' }
~~~

report-path 必须是 Git 使用的 repository-relative forward-slash path；evidence 文件可位于 LEAN_RESULTS_ROOT 且不得提交。generator 从 committed HEAD 读取 source，不直接归档工作目录。

## 18. 独立 validate_bundle 与 ZIP SHA-256

validate_bundle 是 scripts/build_review_bundle.py 中的 Python 函数，不是独立 --validate CLI。正式 ZIP 生成后，必须再次独立调用，并让失败映射为非零 exit code：

~~~powershell
Set-Location -LiteralPath $env:PROJECT_ROOT
$bundle = Get-ChildItem -LiteralPath (Join-Path $env:PROJECT_ROOT 'artifacts/review-bundles') -Filter '*.zip' |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
if ($null -eq $bundle) { throw 'No Review Bundle ZIP found.' }
$expectedHead = (git rev-parse HEAD).Trim()

@'
import json
import sys
from dataclasses import asdict
from pathlib import Path
from scripts.build_review_bundle import validate_bundle

result = validate_bundle(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]).resolve())
print(json.dumps(asdict(result), indent=2, sort_keys=True))
raise SystemExit(0 if result.status == "PASS" else 1)
'@ | & $env:PYTHON312 - $bundle.FullName $expectedHead $env:PROJECT_ROOT
if ($LASTEXITCODE -ne 0) { throw 'Independent validate_bundle failed.' }

$zipSha256 = (Get-FileHash -LiteralPath $bundle.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$zipSha256
~~~

该函数会在独立 temporary extraction 中检查 ZIP 可读性、path traversal、single root、required review files、checksums、manifest、tracked-file set、source 对 Git object bytes、Secret/absolute-path scan、evidence schema 和 offline CLI。它需要 PROJECT_ROOT 中存在 expected HEAD 的 Git objects；只有解压目录而没有 Git repository 时，不能完成 Git-object comparison。

最终交付记录必须写明 Bundle path、ZIP SHA-256 和独立 validate_bundle 结果。内部 candidate validation 不能替代这次独立调用。

## 19. 安全收尾（安全清理边界）

本 Runbook 只允许在全部 hash、manifest、stdout/stderr、exit code、Golden、Git 和 Bundle evidence 已记录后结束验证。当前没有获批准的自动清理或递归删除命令；runtime、NuGet、results、clean-source 与 Bundle 的保留/处置流程为 TBD。操作者必须保留正式 Review Bundle 和重现实验所需的受治理证据，不得覆盖未知目录、旧 evidence 或历史 Bundle。目标范围无法精确证明时 fail closed，并改用新的空输出目录。

## 20. 简洁 POSIX 对照

POSIX 使用相同七个 path root、同一 toolchain lock 和同一 Python scripts；将 PowerShell 的 $env:NAME 改为 $NAME，并使用正斜杠。核心入口：

~~~sh
set -eu
cd "$PROJECT_ROOT"

"$PYTHON312" --version
"$PYTHON312" -m venv "$PROJECT_ROOT/.venv"
"$PROJECT_ROOT/.venv/bin/python" -m pip install --upgrade pip
"$PROJECT_ROOT/.venv/bin/python" -m pip install -e '.[dev]'
PATH="$PROJECT_ROOT/.venv/bin:$PATH" "$PROJECT_ROOT/scripts/check.sh"

test "$(dotnet --version)" = "10.0.302"
git -C "$LEAN_SOURCE_ROOT" rev-parse HEAD
git -C "$LEAN_SOURCE_ROOT" status --porcelain

"$PYTHON312" -c 'import json; from pathlib import Path; from scripts.generate_phase01_synthetic_data import validate_fixture; root=Path("integrations/lean/data"); actual=validate_fixture(root); expected=json.loads((root/"fixture-manifest.json").read_text(encoding="utf-8")); assert actual == expected; print(json.dumps(actual, sort_keys=True))'

dotnet restore "$LEAN_SOURCE_ROOT/QuantConnect.Lean.sln"
dotnet build "$LEAN_SOURCE_ROOT/QuantConnect.Lean.sln" --configuration Release --no-restore
dotnet restore "$PROJECT_ROOT/integrations/lean/algorithms/Phase01DeterministicSmokeTest.csproj" "-p:LeanSourceRoot=$LEAN_SOURCE_ROOT"
dotnet build "$PROJECT_ROOT/integrations/lean/algorithms/Phase01DeterministicSmokeTest.csproj" --configuration Release --no-restore "-p:LeanSourceRoot=$LEAN_SOURCE_ROOT"
~~~

deterministic A/B、safe config、timeout wrapper、normalizer、Golden compare 和 validate_bundle 均使用上文相同 Python 代码，不改变语义。POSIX 没有仓库自带的 Native orchestrator；不得用一个虚构命令替代这些步骤。

Review Bundle：

~~~sh
cd "$PROJECT_ROOT"
"$PYTHON312" scripts/build_review_bundle.py \
  --round-id phase-01-round-01c-documentation-interface-contract-baseline \
  --base-commit b9874e26240f8dfffdbf3ff2d270ffae9ccab92a \
  --output-dir "$PROJECT_ROOT/artifacts/review-bundles" \
  --report-path docs/reports/phase-01-round-01c-documentation-interface-contract-baseline-report.md \
  --round-start-time "$ROUND_START_TIME" \
  --evidence-json "$LEAN_RESULTS_ROOT/phase-01-round-01c-evidence.json"
~~~

## 21. 常见失败

| 现象 | 真实原因与处理 |
| --- | --- |
| scripts/bootstrap.ps1 报 Python launcher 不存在 | 该脚本真实依赖 py；使用 PYTHON312 的手工 venv 流程。 |
| Python 不是 3.12.x | 停止；不得用 3.11 或 3.13 代替 evidence。 |
| dotnet version 不是 10.0.302 | 停止；按 toolchain provisioning TBD 安装正确 SDK。 |
| LEAN HEAD 或 status 不一致 | 停止；只接受 toolchain lock 的官方 commit 和 clean tracked source。 |
| fixture/map/factor hash mismatch | 停止；不得重写 manifest 或 fixture 使测试通过。 |
| MSBuild 要求 LeanSourceRoot | 传入 -p:LeanSourceRoot=LEAN_SOURCE_ROOT，并确认 official checkout。 |
| deterministic DLL SHA mismatch | FAIL；保留 A/B 输出供诊断，不更新 algorithm identity。 |
| Launcher runtime/data/results 目标已有未知内容 | 停止并选择新的受控目录，不覆盖。 |
| config 出现 live mode、Broker 或 credential 字段 | FAIL；不得启动 Launcher。 |
| Run C timeout、exit code 非零或 raw order-events 缺失 | FAIL；记录 stdout/stderr/exit evidence 后诊断。 |
| summary JSON 无法解析 | summary 不可信；不能据此宣称成功，仍须检查 raw order-events、normalizer 和 Golden。 |
| Golden mismatch 或 final_holdings 非零 | FAIL；不得修改 Golden 掩盖业务差异。 |
| python -m pytest coverage 低于 90 | FAIL；不得删除测试或降低 threshold。 |
| Makefile check 通过但 build/CLI 未运行 | 证据不完整；运行 scripts/check.ps1 或 scripts/check.sh。 |
| Bundle 报 working tree、branch 或 origin/main mismatch | 先完成 commit/push 与 clean Git gate；不得打包 uncommitted source。 |
| Bundle 报 report untracked | 使用已由 HEAD 跟踪的 Round 1C 正式 report。 |
| Bundle evidence 缺少 command、count、coverage 或 clean-source HEAD | 重新生成真实 evidence；不得填占位 PASS。 |
| validate_bundle offline_cli 失败 | 确认 Python 3.12 可执行且 source/src 可导入；不得跳过该 check。 |

## 22. TBD register

每个 TBD 都是不支持或未批准的事实，不得当作已实现能力。

| TBD | 未实现或未确认内容 | 原因 | owner role | 触发补充条件 |
| --- | --- | --- | --- | --- |
| Toolchain provisioning | .NET SDK 10.0.302 和 Python 3.12 的受批准下载、checksum 与安装流程 | 仓库只锁版本，没有 installer 或供应链校验脚本 | Platform Owner / Security Owner | 新机器正式 onboarding 前批准来源与 checksum |
| Fixture regeneration | 仅用本 Runbook 七个 path root 的统一 ToolBox regeneration 命令 | 现有 generator 需要额外隔离 ToolBox runtime；本 Round 不重生成 fixture | LEAN Integration Owner | fixture 或 LEAN commit 获批变更时 |
| Launcher config generator | 可复用、测试覆盖的 config generator 与 portable config evidence | 当前只有本地 inline 分步流程，没有提交脚本 | LEAN Integration Owner / Independent Risk Owner | 下次必须重跑 Native Run 或配置结构变化前 |
| Native Run orchestrator | 统一执行 runtime copy、config、timeout、stdout/stderr/exit 和 manifest 的命令 | 当前仓库没有 orchestrator | LEAN Integration Owner | 下一次 Native acceptance Run 前，test-first 实现 |
| Raw order-events exporter | 从实际 Launcher 结果生成独立、可追溯的 raw order-events JSON | 当前仓库只有 normalizer，没有受控 exporter；summary 不能作为不可信输入的替代 | LEAN Integration Owner / QA Owner | 下一次 Run C normalizer 执行前 |
| Deterministic A/B command | 统一的双 source build 与 identity validator | 当前仅有真实分步流程和静态 identity | Build Owner | algorithm/csproj/toolchain 变化或下一次重建证据前 |
| Golden compare CLI | 独立 compare-orders / compare-statistics CLI | 当前只有 compare_semantic Python 函数 | QA Owner | normalizer/Golden schema 变化或频繁人工运行前 |
| Support-data evidence generator | 自动记录 market-hours/symbol-properties provenance 与 SHA | 当前需人工收集 | Data Governance Owner / LEAN Integration Owner | 下一次 Run C bundle 前 |
| Run timeout policy | 统一 timeout 数值和机器性能归一化规则 | 当前 600 秒仅为本地 guard，不是批准 gate | Platform Owner / QA Owner | 跨机器重复运行并审阅分布后 |
| Network isolation automation | Windows/POSIX 可验证的 network-disabled runner | bootstrap/restore 需要网络，现有 scripts 不实施隔离 | Security Owner / Platform Owner | 正式 clean-source evidence 自动化前 |
| Clean-source runner | 统一、可移植且产生结构化 evidence 的 clean-source 命令 | 当前只有新 clone 的分步流程 | QA Owner | 下一 Round 把 clean-source 作为自动 gate 前 |
| Round evidence assembler | 从实际命令结果生成 schema_version 1 evidence JSON | generator 接收 evidence，但不负责采集所有命令输出 | Review Bundle Maintainer | 每次正式 Bundle 生成前；实现需保持 Round 1B 语义 |
| POSIX Native closure | 在 POSIX 主机上正式复现 Launcher Run C、A/B identity 和 Golden | Round 1B 冻结证据不授权声称所有 POSIX 主机已验证 | LEAN Integration Owner | POSIX 成为受支持验收平台时 |
| Retention procedure | runtime、NuGet、results、clean-source 和 Bundle 的批准保留/处置流程 | 当前任务明确不执行本地清理 | Operations Owner / Security Owner | 独立保留政策获批后 |
