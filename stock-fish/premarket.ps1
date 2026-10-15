param([Parameter(ValueFromRemainingArguments = $true)][string[]]$WorkflowArgs)
$ErrorActionPreference = 'Stop'
$taskPython = $env:STOCKFISH_PYTHON
if (-not $taskPython) {
    $taskPythonFile = $env:STOCKFISH_PREMARKET_PYTHON_FILE
    if (-not $taskPythonFile) {
        $taskPythonFile = Join-Path $PSScriptRoot 'reports/premarket-runtime/python.path'
    }
    if (Test-Path -LiteralPath $taskPythonFile -PathType Leaf) {
        $taskPython = (Get-Content -LiteralPath $taskPythonFile -TotalCount 1).Trim()
    } else {
        $taskPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    }
}
if (-not $taskPython -or -not (Test-Path -LiteralPath $taskPython -PathType Leaf)) {
    throw '请用 STOCKFISH_PYTHON 指定现有项目Python，或配置 reports/premarket-runtime/python.path。'
}
& $taskPython -B (Join-Path $PSScriptRoot 'scripts/stockfish.py') premarket @WorkflowArgs
exit $LASTEXITCODE
