[CmdletBinding()]
param(
    [string]$Root = $PSScriptRoot,
    [ValidateRange(5, 300)][int]$TimeoutSeconds = 90
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Root = [System.IO.Path]::GetFullPath($Root)
$Repository = Join-Path $Root 'stock-fish'
$Python = Join-Path $Repository '.venv\Scripts\python.exe'
$App = Join-Path $Repository 'app.py'
$LogDirectory = Join-Path $Root 'logs'
$WorkDirectory = Join-Path $Root 'work'
$StateFile = Join-Path $WorkDirectory 'stockfish-process.json'
$HealthUrl = 'http://127.0.0.1:8000/api/config'
$VenvConfiguration = Join-Path $Repository '.venv\pyvenv.cfg'
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

function Get-ProcessStamp($Process) {
    return ([datetime]$Process.CreationDate).ToUniversalTime().ToString('o')
}

function Test-AppCommand($Process) {
    $command = [string]$Process.CommandLine
    $pythonAlternatives = ($AllowedPythonPaths | ForEach-Object { '"' + [regex]::Escape($_) + '"|' + [regex]::Escape($_) }) -join '|'
    $pythonPattern = '^\s*(?:' + $pythonAlternatives + ')(?=\s|$)'
    $appPattern = '(?:^|\s)(?:"' + [regex]::Escape($App) + '"|' + [regex]::Escape($App) + ')(?=\s|$)'
    return (($Process.ExecutablePath -iin $AllowedPythonPaths -or $command -imatch $pythonPattern) -and $command -imatch $appPattern)
}

function Get-VerifiedProcess($Record) {
    if ($null -eq $Record -or $null -eq $Record.ProcessId -or $null -eq $Record.CreationTimeUtc) { return $null }
    $process = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$Record.ProcessId)
    $recordedStamp = ([datetime]$Record.CreationTimeUtc).ToUniversalTime().ToString('o')
    if ($null -ne $process -and (Test-AppCommand $process) -and (Get-ProcessStamp $process) -eq $recordedStamp) {
        return $process
    }
    return $null
}

function New-ProcessRecord($Process) {
    return [pscustomobject]@{ ProcessId = [int]$Process.ProcessId; CreationTimeUtc = Get-ProcessStamp $Process }
}

function Get-Listeners {
    # Enumerate listeners without treating an unused port as a command failure.
    return @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -eq 8000 })
}

function Test-Health {
    try {
        $configuration = Invoke-RestMethod -Method Get -Uri $HealthUrl -TimeoutSec 5
        return ($null -ne $configuration -and $null -ne $configuration.PSObject.Properties['backend'] -and $null -ne $configuration.PSObject.Properties['llm_configured'])
    } catch { return $false }
}

function Save-State($State) {
    $temporaryState = Join-Path $WorkDirectory ('stockfish-process-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        $State | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $temporaryState -Encoding UTF8
        Move-Item -LiteralPath $temporaryState -Destination $StateFile -Force
    } finally {
        if (Test-Path -LiteralPath $temporaryState) { Remove-Item -LiteralPath $temporaryState -Force }
    }
}

function Update-ChildRecords($State) {
    # Windows venv launchers can have a child Python process running app.py.
    $knownProcesses = @($State.Processes | ForEach-Object { Get-VerifiedProcess $_ } | Where-Object { $null -ne $_ })
    $knownIds = @($knownProcesses | ForEach-Object { [int]$_.ProcessId })
    if ($knownIds.Count -eq 0) { return }
    $candidates = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { Test-AppCommand $_ })
    for ($pass = 0; $pass -lt 4; $pass++) {
        $added = $false
        foreach ($candidate in $candidates) {
            if ([int]$candidate.ProcessId -in $knownIds -or [int]$candidate.ParentProcessId -notin $knownIds) { continue }
            $parentProcess = $knownProcesses | Where-Object { [int]$_.ProcessId -eq [int]$candidate.ParentProcessId } | Select-Object -First 1
            if ([datetime]$candidate.CreationDate -lt [datetime]$parentProcess.CreationDate) { continue }
            $State.Processes = @($State.Processes) + @(New-ProcessRecord $candidate)
            $knownProcesses += $candidate
            $knownIds += [int]$candidate.ProcessId
            $added = $true
        }
        if (-not $added) { break }
    }
}

$mutex = [System.Threading.Mutex]::new($false, 'Local\StockRecommendation-StockFish-8000')
$lockTaken = $false
try {
    try { $lockTaken = $mutex.WaitOne(10000) } catch [System.Threading.AbandonedMutexException] { $lockTaken = $true }
    if (-not $lockTaken) { throw 'Another StockFish start/stop operation is running. Retry after it finishes.' }
    foreach ($directory in @($LogDirectory, $WorkDirectory)) {
        if (-not (Test-Path -LiteralPath $directory)) { New-Item -ItemType Directory -Path $directory | Out-Null }
    }
    foreach ($requiredPath in @($Python, $App, (Join-Path $Repository '.env'))) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) { throw "Required file is missing: $requiredPath" }
    }

    $state = $null
    if (Test-Path -LiteralPath $StateFile) {
        try {
            $state = Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($state.Python -ine $Python -or $state.App -ine $App -or $null -eq $state.Processes) { throw 'Unexpected state file contents.' }
        } catch { throw "Cannot safely read $StateFile. Inspect it before retrying. $($_.Exception.Message)" }
    }

    $verified = @()
    if ($null -ne $state) {
        Update-ChildRecords $state
        $verified = @($state.Processes | ForEach-Object { Get-VerifiedProcess $_ } | Where-Object { $null -ne $_ })
    }
    $listeners = @(Get-Listeners)
    if ($verified.Count -eq 0) {
        if ($listeners.Count -gt 0) {
            $owners = ($listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '
            throw "Port 8000 is already occupied (PID $owners). No process was stopped. Use that application's own stop command or resolve the port conflict."
        }
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $suffix = [guid]::NewGuid().ToString('N').Substring(0, 8)
        $stdoutLog = Join-Path $LogDirectory "stockfish-$stamp-$suffix.stdout.log"
        $stderrLog = Join-Path $LogDirectory "stockfish-$stamp-$suffix.stderr.log"
        # -X utf8 and -u affect only this Python process; no environment or policy changes.
        $launched = Start-Process -FilePath $Python -ArgumentList @('-X', 'utf8', '-u', ('"' + $App + '"')) -WorkingDirectory $Repository -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
        $launchProcess = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $launched.Id)
        if ($null -eq $launchProcess -or -not (Test-AppCommand $launchProcess)) {
            throw "StockFish exited before its process identity could be recorded. Read $stderrLog"
        }
        $state = [pscustomobject]@{
            Version = 1
            Python = $Python
            App = $App
            HealthUrl = $HealthUrl
            StartedAtUtc = [datetime]::UtcNow.ToString('o')
            StdoutLog = $stdoutLog
            StderrLog = $stderrLog
            Processes = @(New-ProcessRecord $launchProcess)
        }
        Save-State $state
        Write-Host "Starting StockFish. Logs: $LogDirectory"
    }

    $deadline = [datetime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        Update-ChildRecords $state
        Save-State $state
        $verified = @($state.Processes | ForEach-Object { Get-VerifiedProcess $_ } | Where-Object { $null -ne $_ })
        if ($verified.Count -eq 0) { throw "StockFish exited during startup. Read $($state.StderrLog)" }
        $verifiedIds = @($verified | ForEach-Object { [int]$_.ProcessId })
        $listeners = @(Get-Listeners)
        $foreignListeners = @($listeners | Where-Object { [int]$_.OwningProcess -notin $verifiedIds })
        if ($foreignListeners.Count -gt 0) { throw 'Port 8000 belongs to another process. No process was stopped. Inspect the logs and process state.' }
        if ($listeners.Count -gt 0 -and (Test-Health)) {
            Write-Host "StockFish is ready: http://127.0.0.1:8000 (PID $($verifiedIds -join ', '))"
            Write-Host "Stop: & '$Root\Stop-StockFish.ps1'"
            Write-Host "Logs: & '$Root\Logs-StockFish.ps1' -Follow"
            return
        }
        Start-Sleep -Seconds 1
    } while ([datetime]::UtcNow -lt $deadline)
    throw "StockFish did not become ready within $TimeoutSeconds seconds. Its recorded process was left running for diagnosis. Read $($state.StderrLog), or run Stop-StockFish.ps1."
} finally {
    if ($lockTaken) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
