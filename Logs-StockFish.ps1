[CmdletBinding()]
param(
    [string]$Root = $PSScriptRoot,
    [ValidateSet('Both', 'Stdout', 'Stderr')][string]$Stream = 'Both',
    [ValidateRange(1, 10000)][int]$Tail = 80,
    [switch]$Follow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Root = [System.IO.Path]::GetFullPath($Root)
$LogDirectory = Join-Path $Root 'logs'
$StateFile = Join-Path $Root 'work\stockfish-process.json'
$paths = @()
$state = $null
if (Test-Path -LiteralPath $StateFile) {
    try { $state = Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { Write-Warning 'The process state could not be read; selecting the latest log files.' }
}
foreach ($kind in @('Stdout', 'Stderr')) {
    if ($Stream -ne 'Both' -and $Stream -ne $kind) { continue }
    $path = $null
    $propertyName = $kind + 'Log'
    if ($null -ne $state -and $null -ne $state.PSObject.Properties[$propertyName]) {
        $candidate = [string]$state.$propertyName
        if ($candidate -and [System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($candidate)) -ieq $LogDirectory -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            $path = $candidate
        }
    }
    if ($null -eq $path -and (Test-Path -LiteralPath $LogDirectory)) {
        $latest = Get-ChildItem -LiteralPath $LogDirectory -Filter ('stockfish-*.' + $kind.ToLowerInvariant() + '.log') -File | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
        if ($null -ne $latest) { $path = $latest.FullName }
    }
    if ($null -ne $path) { $paths += $path }
}
if ($paths.Count -eq 0) { Write-Host 'No StockFish log files exist yet.'; return }
foreach ($path in $paths) {
    Write-Host "`n--- $path ---"
    Get-Content -LiteralPath $path -Tail $Tail -Encoding UTF8
}
if ($Follow) {
    Write-Host "`nFollowing these logs. Press Ctrl+C to stop viewing."
    # Poll both streams together; Get-Content -Wait on an array can block on its first file.
    $readers = @()
    try {
        foreach ($path in $paths) {
            $fileStream = [System.IO.File]::Open($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
            $fileStream.Seek(0, [System.IO.SeekOrigin]::End) | Out-Null
            $reader = [System.IO.StreamReader]::new($fileStream, [System.Text.Encoding]::UTF8, $true)
            $readers += [pscustomobject]@{ Path = $path; Reader = $reader }
        }
        while ($true) {
            foreach ($entry in $readers) {
                while (-not $entry.Reader.EndOfStream) {
                    $line = $entry.Reader.ReadLine()
                    Write-Output ('[' + [System.IO.Path]::GetFileName($entry.Path) + '] ' + $line)
                }
            }
            Start-Sleep -Milliseconds 500
        }
    } finally {
        foreach ($entry in $readers) { $entry.Reader.Dispose() }
    }
}
