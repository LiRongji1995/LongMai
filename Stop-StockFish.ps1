[CmdletBinding()]
param([string]$Root = $PSScriptRoot)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Root = [System.IO.Path]::GetFullPath($Root)
$Python = Join-Path $Root 'stock-fish\.venv\Scripts\python.exe'
$App = Join-Path $Root 'stock-fish\app.py'
$StateFile = Join-Path $Root 'work\stockfish-process.json'
$VenvConfiguration = Join-Path $Root 'stock-fish\.venv\pyvenv.cfg'
$BasePython = $null
if (Test-Path -LiteralPath $VenvConfiguration -PathType Leaf) {
    foreach ($configurationLine in (Get-Content -LiteralPath $VenvConfiguration)) {
        if ($configurationLine -match '^executable\s*=\s*(.+?)\s*$') {
            $BasePython = [System.IO.Path]::GetFullPath($Matches[1])
            break
        }
    }
}
$AllowedPythonPaths = @($Python)
if ($BasePython -and (Test-Path -LiteralPath $BasePython -PathType Leaf)) { $AllowedPythonPaths += $BasePython }

function Get-VerifiedProcess($Record) {
    if ($null -eq $Record -or $null -eq $Record.ProcessId -or $null -eq $Record.CreationTimeUtc) { return $null }
    $process = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$Record.ProcessId)
    if ($null -eq $process) { return $null }
    $creationStamp = ([datetime]$process.CreationDate).ToUniversalTime().ToString('o')
    $recordedStamp = ([datetime]$Record.CreationTimeUtc).ToUniversalTime().ToString('o')
    if ($creationStamp -ne $recordedStamp) { return $null }
    $command = [string]$process.CommandLine
    $pythonAlternatives = ($AllowedPythonPaths | ForEach-Object { '"' + [regex]::Escape($_) + '"|' + [regex]::Escape($_) }) -join '|'
    $pythonPattern = '^\s*(?:' + $pythonAlternatives + ')(?=\s|$)'
    $appPattern = '(?:^|\s)(?:"' + [regex]::Escape($App) + '"|' + [regex]::Escape($App) + ')(?=\s|$)'
    if (($process.ExecutablePath -iin $AllowedPythonPaths -or $command -imatch $pythonPattern) -and $command -imatch $appPattern) { return $process }
    return $null
}

$mutex = [System.Threading.Mutex]::new($false, 'Local\StockRecommendation-StockFish-8000')
$lockTaken = $false
try {
    try { $lockTaken = $mutex.WaitOne(10000) } catch [System.Threading.AbandonedMutexException] { $lockTaken = $true }
    if (-not $lockTaken) { throw 'Another StockFish start/stop operation is running. Retry after it finishes.' }
    if (-not (Test-Path -LiteralPath $StateFile)) {
        Write-Host 'No managed StockFish process is recorded. No process was stopped.'
        return
    }
    try {
        $state = Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($state.Python -ine $Python -or $state.App -ine $App -or $null -eq $state.Processes) { throw 'Unexpected state file contents.' }
    } catch { throw "Cannot safely read $StateFile. No process was stopped. $($_.Exception.Message)" }

    $records = @($state.Processes)
    [array]::Reverse($records)
    $stopped = @()
    foreach ($record in $records) {
        # Recheck exact app/interpreter identity and creation time immediately before stopping.
        $process = Get-VerifiedProcess $record
        if ($null -eq $process) { continue }
        try {
            Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction Stop
            $stopped += [int]$process.ProcessId
        } catch {
            if ($null -ne (Get-VerifiedProcess $record)) { throw }
        }
    }
    $deadline = [datetime]::UtcNow.AddSeconds(10)
    do {
        $remaining = @($records | ForEach-Object { Get-VerifiedProcess $_ } | Where-Object { $null -ne $_ })
        if ($remaining.Count -eq 0) { break }
        Start-Sleep -Milliseconds 250
    } while ([datetime]::UtcNow -lt $deadline)
    if ($remaining.Count -gt 0) { throw 'A verified StockFish process remains alive; the PID state was kept for a later retry.' }
    Remove-Item -LiteralPath $StateFile -Force
    if ($stopped.Count -gt 0) { Write-Host "StockFish stopped (PID $($stopped -join ', ')). Logs were retained." }
    else { Write-Host 'The recorded StockFish process had already exited. Stale state was removed; no other process was stopped.' }
} finally {
    if ($lockTaken) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
