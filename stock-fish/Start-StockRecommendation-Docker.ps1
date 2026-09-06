[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$NonInteractive,
    [switch]$NoBuild,
    [string]$BackupDirectory
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ComposeFile = Join-Path $ProjectRoot "docker-compose.yml"
$EnvFile = Join-Path $ProjectRoot ".env.docker.local"

function Write-Step([string]$Message) {
    Write-Host "[StockFish Docker] $Message"
}

function New-ApiKey {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

function Read-EnvValue([string]$Name) {
    if (-not (Test-Path -LiteralPath $EnvFile)) { return $null }
    foreach ($line in Get-Content -LiteralPath $EnvFile) {
        if ($line -match "^\s*$([regex]::Escape($Name))=(.*)$") {
            return $Matches[1].Trim()
        }
    }
    return $null
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI was not found. Install Docker Desktop/Engine and retry."
}
if (-not (Test-Path -LiteralPath $ComposeFile)) {
    throw "docker-compose.yml not found at $ComposeFile"
}

$dockerVersion = (& docker --version) -join "`n"
$composeVersion = (& docker compose version) -join "`n"
Write-Step $dockerVersion
Write-Step $composeVersion

try {
    & docker info *> $null
} catch {
    throw "Docker daemon is not reachable. Start Docker Desktop/Engine and retry."
}

if (-not (Test-Path -LiteralPath $EnvFile)) {
    $apiKey = New-ApiKey
    @(
        "STOCKFISH_PORT=$Port",
        "STOCKFISH_ALLOWED_HOSTS=localhost,127.0.0.1",
        "STOCKFISH_API_KEY=$apiKey"
    ) | Set-Content -LiteralPath $EnvFile -Encoding UTF8
    Write-Step "Created private local env file: $EnvFile"
} else {
    Write-Step "Using existing private local env file: $EnvFile"
}

$effectivePort = Read-EnvValue "STOCKFISH_PORT"
if (-not $effectivePort) { $effectivePort = "$Port" }
$apiKey = Read-EnvValue "STOCKFISH_API_KEY"
if (-not $apiKey) {
    throw "STOCKFISH_API_KEY is missing from $EnvFile"
}

$services = (& docker compose --env-file $EnvFile -f $ComposeFile ps --services --status running) 2>$null
$alreadyRunning = $services -contains "stockfish"
if (-not $alreadyRunning -and (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
    $listeners = Get-NetTCPConnection -LocalPort ([int]$effectivePort) -State Listen -ErrorAction SilentlyContinue
    if ($listeners) {
        throw "Port $effectivePort is already in use. Stop the conflicting listener or edit STOCKFISH_PORT in $EnvFile."
    }
}

$composeArgs = @("compose", "--env-file", $EnvFile, "-f", $ComposeFile, "up", "-d", "--wait")
if (-not $NoBuild) {
    $composeArgs += "--build"
}
Write-Step ("Running: docker " + ($composeArgs -join " "))
& docker @composeArgs

$imageId = (& docker compose --env-file $EnvFile -f $ComposeFile images -q stockfish) -join ""
Write-Step "Image ID: $imageId"

$healthUrl = "http://127.0.0.1:$effectivePort/api/health"
$headers = @{ Authorization = "Bearer $apiKey" }
for ($i = 0; $i -lt 60; $i++) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -Headers $headers -TimeoutSec 5
        if ($response.StatusCode -eq 200) {
            Write-Step "Health check passed: $healthUrl"
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
    if ($i -eq 59) {
        & docker compose --env-file $EnvFile -f $ComposeFile logs --tail=120 stockfish
        throw "Container did not become healthy within the wait window."
    }
}

if ($BackupDirectory) {
    $backupRoot = Resolve-Path -LiteralPath $BackupDirectory -ErrorAction SilentlyContinue
    if (-not $backupRoot) {
        New-Item -ItemType Directory -Path $BackupDirectory | Out-Null
        $backupRoot = Resolve-Path -LiteralPath $BackupDirectory
    }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $target = Join-Path $backupRoot "stockfish-$stamp.sqlite3"
    & docker compose --env-file $EnvFile -f $ComposeFile cp stockfish:/app/data/stockfish.sqlite3 $target
    Write-Step "Database backup copied to: $target"
}

Write-Step "Open http://127.0.0.1:$effectivePort/ . The API key is stored in $EnvFile and was not printed."
