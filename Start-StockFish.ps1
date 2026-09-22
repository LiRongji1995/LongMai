[CmdletBinding()]
param(
    [string]$Root = $PSScriptRoot,
    [ValidateRange(0, 65535)][int]$Port = 0,
    [string]$DatabasePath = '',
    [string]$StateRoot = '',
    [switch]$AdoptExisting,
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
$ServiceConfigFile = Join-Path $WorkDirectory 'stockfish-service.json'
if (Test-Path -LiteralPath $ServiceConfigFile) {
    $serviceConfig = Get-Content -LiteralPath $ServiceConfigFile -Raw | ConvertFrom-Json
    if ($Port -eq 0) { $Port = [int]$serviceConfig.Port }
    if (-not $DatabasePath) { $DatabasePath = [string]$serviceConfig.DatabasePath }
    if (-not $StateRoot) { $StateRoot = [string]$serviceConfig.StateRoot }
}
if ($Port -eq 0) { $Port = 8000 }
if (-not $DatabasePath) { $DatabasePath = if ($env:STOCKFISH_DB_PATH) { $env:STOCKFISH_DB_PATH } else { Join-Path $Repository 'data/stockfish.sqlite3' } }
if (-not $StateRoot) { $StateRoot = if ($env:STOCKFISH_STATE_DIR) { $env:STOCKFISH_STATE_DIR } else { Join-Path $Repository 'data/core' } }
$DatabasePath = [System.IO.Path]::GetFullPath($DatabasePath)
$StateRoot = [System.IO.Path]::GetFullPath($StateRoot)
$HealthUrl = "http://127.0.0.1:$Port/health/ready"
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
    return @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -eq $Port })
}

function Test-Health {
    try {
        $healthHeaders = @{}
        $readinessKey = $env:STOCKFISH_API_KEY
        if (-not $readinessKey) {
            foreach ($environmentLine in (Get-Content -LiteralPath (Join-Path $Repository '.env'))) {
                if ($environmentLine -match '^\s*STOCKFISH_API_KEY\s*=\s*(.*?)\s*$') {
                    $readinessKey = $Matches[1].Trim().Trim('"').Trim("'")
                    break
                }
            }
        }
        if ($readinessKey) { $healthHeaders['Authorization'] = 'Bearer ' + $readinessKey }
        $health = Invoke-RestMethod -Method Get -Uri $HealthUrl -Headers $healthHeaders -TimeoutSec 5
        if ($health.status -ne 'ready' -or -not $health.ready) { return $false }
        if ($health.checks.database_connection.status -ne 'ok') { return $false }
        if ($health.checks.jobs.status -ne 'ok') { return $false }
        if ($health.checks.scheduler.status -ne 'ok') { return $false }
        if ([System.IO.Path]::GetFullPath($health.service_identity.db_path) -ine $DatabasePath) { return $false }
        if ([System.IO.Path]::GetFullPath($health.service_identity.state_root) -ine (Join-Path $StateRoot 'yaogu')) { return $false }
        $script:ScheduleProbe = [pscustomobject]@{ prospective_validation = $health }
        return $true
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

$rootStamp = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($Root.ToLowerInvariant()))).Substring(0, 20)
$mutex = [System.Threading.Mutex]::new($false, ('Local\StockRecommendation-StockFish-' + $rootStamp))
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
    if ($verified.Count -eq 0 -and $listeners.Count -gt 0 -and $AdoptExisting) {
        $ownedProcesses = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $_) })
        if (@($ownedProcesses | Where-Object { -not (Test-AppCommand $_) }).Count -gt 0 -or -not (Test-Health)) {
            throw 'Existing listener does not match the exact repository, DB, state root and live scheduler. It was not adopted or stopped.'
        }
        if ([int]$script:ScheduleProbe.prospective_validation.service_identity.process_id -notin @($ownedProcesses.ProcessId)) {
            throw 'Health process identity does not match the listening process.'
        }
        foreach ($child in @($ownedProcesses)) {
            $parent = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$child.ParentProcessId)
            if ($parent -and (Test-AppCommand $parent) -and [datetime]$parent.CreationDate -le [datetime]$child.CreationDate) { $ownedProcesses = @($parent) + $ownedProcesses }
        }
        $state = [pscustomobject]@{
            Version = 2; Python = $Python; App = $App; RepositoryRoot = $Root
            DatabasePath = $DatabasePath; StateRoot = $StateRoot; Port = $Port; HealthUrl = $HealthUrl
            StartedAtUtc = [datetime]::UtcNow.ToString('o'); AdoptedExisting = $true
            StdoutLog = ''; StderrLog = ''; SchedulerIdentity = $script:ScheduleProbe.prospective_validation.service_identity
            Processes = @($ownedProcesses | Sort-Object CreationDate,ProcessId -Unique | ForEach-Object { New-ProcessRecord $_ })
        }
        Save-State $state
        $verified = $ownedProcesses
    }
    if ($verified.Count -eq 0) {
        if ($listeners.Count -gt 0) {
            $owners = ($listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '
            throw "Port $Port is already occupied (PID $owners). No process was stopped."
        }
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $suffix = [guid]::NewGuid().ToString('N').Substring(0, 8)
        $stdoutLog = Join-Path $LogDirectory "stockfish-$stamp-$suffix.stdout.log"
        $stderrLog = Join-Path $LogDirectory "stockfish-$stamp-$suffix.stderr.log"
        # -X utf8 and -u affect only this Python process; no environment or policy changes.
        $env:STOCKFISH_DB_PATH = $DatabasePath
        $env:STOCKFISH_STATE_DIR = $StateRoot
        $launched = Start-Process -FilePath $Python -ArgumentList @('-X', 'utf8', '-u', ('"' + $App + '"'), '--port', $Port) -WorkingDirectory $Repository -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
        $launchProcess = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $launched.Id)
        if ($null -eq $launchProcess -or -not (Test-AppCommand $launchProcess)) {
            throw "StockFish exited before its process identity could be recorded. Read $stderrLog"
        }
        $state = [pscustomobject]@{
            Version = 2
            RepositoryRoot = $Root
            DatabasePath = $DatabasePath
            StateRoot = $StateRoot
            Port = $Port
            SchedulerIdentity = $null
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
        if ($foreignListeners.Count -gt 0) { throw "Port $Port belongs to another process. No process was stopped. Inspect the logs and process state." }
        if ($listeners.Count -gt 0 -and (Test-Health)) {
            $state | Add-Member -NotePropertyName SchedulerIdentity -NotePropertyValue $script:ScheduleProbe.prospective_validation.service_identity -Force
            Save-State $state
            [pscustomobject]@{ Version = 1; RepositoryRoot = $Root; Port = $Port; DatabasePath = $DatabasePath; StateRoot = $StateRoot } | ConvertTo-Json | Set-Content -LiteralPath $ServiceConfigFile -Encoding UTF8
            Write-Host "StockFish is ready: http://127.0.0.1:$Port (PID $($verifiedIds -join ', '))"
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
