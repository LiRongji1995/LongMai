[CmdletBinding()]
param([int]$Port=8000,[switch]$NonInteractive,[switch]$NoBuild,[string]$BackupDirectory)
$ErrorActionPreference='Stop'
$ProjectRoot=Split-Path -Parent $MyInvocation.MyCommand.Path
$ComposeFile=Join-Path $ProjectRoot 'docker-compose.yml'
$EnvFile=Join-Path $ProjectRoot '.env.docker.local'

function Invoke-DockerChecked {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
    $output=& docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed (exit $LASTEXITCODE); this invocation is not verified." }
    return $output
}
function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
    Invoke-DockerChecked compose --env-file $EnvFile -f $ComposeFile @Arguments
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker CLI is unavailable.' }
Invoke-DockerChecked --version
Invoke-DockerChecked compose version
Invoke-DockerChecked info --format '{{.ServerVersion}}'
if (-not (Test-Path -LiteralPath $EnvFile)) {
    $bytes=New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $apiKey=-join ($bytes | ForEach-Object {$_.ToString('x2')})
    @("STOCKFISH_PORT=$Port",'STOCKFISH_ALLOWED_HOSTS=localhost,127.0.0.1',"STOCKFISH_API_KEY=$apiKey") |
        Set-Content -LiteralPath $EnvFile -Encoding utf8
}
# Quiet validation does not emit container credentials.
Invoke-Compose config --quiet
$services=@(Invoke-Compose ps --services --status running)
if ($services -contains 'stockfish') {
    if (-not $BackupDirectory) { $BackupDirectory=Join-Path $ProjectRoot '.docker-backups' }
    $stamp=Get-Date -Format 'yyyyMMdd-HHmmss-ffff'
    $target=Join-Path $BackupDirectory "before-start-$stamp"
    if (Test-Path -LiteralPath $target) { throw 'Backup destination must be new.' }
    [void](New-Item -ItemType Directory -Path $BackupDirectory -Force)
    $inside="/app/data/.backups/stockfish-backup-$stamp"
    # Missing backup CLI or active jobs abort before build/up. Never copy raw WAL DB.
    Invoke-Compose exec -T stockfish python scripts/backup_restore_state.py backup --database /app/data/stockfish.sqlite3 --artifact-root /app/data/core --destination $inside
    Invoke-Compose cp "stockfish:$inside" $target
    Write-Host "Verified pre-start SQLite/WAL/artifact backup copied to $target"
}
if (-not $NoBuild) { Invoke-Compose build stockfish }
$expectedImage=(Invoke-DockerChecked image inspect stockfish-core:local --format '{{.Id}}') -join ''
Invoke-Compose up -d --wait --no-build stockfish
$container=(Invoke-Compose ps -q stockfish) -join ''
if (-not $container) { throw 'This invocation has no stockfish container.' }
$actualImage=(Invoke-DockerChecked inspect $container --format '{{.Image}}') -join ''
$health=(Invoke-DockerChecked inspect $container --format '{{.State.Health.Status}}') -join ''
if ($actualImage -ne $expectedImage -or $health -ne 'healthy') { throw 'Current image/health verification failed.' }
Write-Host "This invocation verified image $actualImage and container $container as healthy."
Write-Host "Credentials remain in the private $EnvFile; optional Tushare variables are explicitly mapped."
