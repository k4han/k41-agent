param(
    [switch]$RemoveRuntimeData
)

$ErrorActionPreference = "Stop"

$AgentName = "k41-agent"

if (-not $env:LOCALAPPDATA) {
    throw "LOCALAPPDATA is not set."
}

$AgentHome = Join-Path $env:LOCALAPPDATA $AgentName
$BinDir = Join-Path $AgentHome "bin"
$PythonExe = Join-Path $AgentHome "envs\Scripts\python.exe"
$RuntimeHome = Join-Path $HOME ".k41-agent"

function Stage {
    param([string]$Name)

    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
}

function Normalize-PathEntry {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ""
    }

    return ($Value.Trim() -replace '[\\/]+$', '')
}

function Remove-UserPath {
    param([string]$PathToRemove)

    $target = Normalize-PathEntry $PathToRemove
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ([string]::IsNullOrWhiteSpace($currentPath)) {
        Write-Host "The user PATH is empty."
        return
    }

    $entries = @($currentPath -split ";" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $newEntries = @($entries | Where-Object { (Normalize-PathEntry $_) -ine $target })

    if ($newEntries.Count -eq $entries.Count) {
        Write-Host "$PathToRemove is not in the user PATH."
    } else {
        [Environment]::SetEnvironmentVariable("Path", ($newEntries -join ";"), "User")
        Write-Host "Removed $PathToRemove from the user PATH."
    }

    if ($env:Path) {
        $processEntries = @($env:Path -split ";" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        $env:Path = (@($processEntries | Where-Object { (Normalize-PathEntry $_) -ine $target }) -join ";")
    }
}

function Stop-ExistingApp {
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        Write-Host "No existing virtual environment found."
        return
    }

    & $PythonExe -m agent.bootstrap.cli stop
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Existing app stop command completed."
    } else {
        Write-Host "Existing app stop command was skipped with exit code $LASTEXITCODE."
    }
    $global:LASTEXITCODE = 0
}

Stage "1. Stop app"
Stop-ExistingApp

Stage "2. Remove installation"
Set-Location $env:TEMP
if (Test-Path -LiteralPath $AgentHome) {
    Remove-Item -LiteralPath $AgentHome -Recurse -Force
    Write-Host "Removed $AgentHome"
} else {
    Write-Host "$AgentHome does not exist."
}

Stage "3. Update PATH"
Remove-UserPath $BinDir

if ($RemoveRuntimeData) {
    Stage "4. Remove runtime data"
    if (Test-Path -LiteralPath $RuntimeHome) {
        Remove-Item -LiteralPath $RuntimeHome -Recurse -Force
        Write-Host "Removed $RuntimeHome"
    } else {
        Write-Host "$RuntimeHome does not exist."
    }
} else {
    Stage "4. Keep runtime data"
    Write-Host "Runtime data was kept at $RuntimeHome"
}

Write-Host ""
Write-Host "Uninstallation completed." -ForegroundColor Green
