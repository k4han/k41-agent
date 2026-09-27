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

function Wait-ProcessExit {
    param(
        [int]$ProcessId,
        [int]$TimeoutSeconds = 15
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $proc = Get-Process -Id $ProcessId -ErrorAction Stop
            if ($proc.HasExited) {
                return $true
            }
        } catch {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    try {
        $proc = Get-Process -Id $ProcessId -ErrorAction Stop
        return $proc.HasExited
    } catch {
        return $true
    }
}

function Stop-ExistingApp {
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        Write-Host "No existing virtual environment found."
    } else {
        $pidFile = Join-Path $HOME ".k41-agent\server.pid"
        $runningPid = $null
        if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
            try {
                $runningPid = [int]((Get-Content -LiteralPath $pidFile -Raw).Trim())
            } catch {
                $runningPid = $null
            }
        }

        # Keep in sync with install.ps1 generated uninstall.cmd.
        & $PythonExe -m agent.bootstrap.cli stop --with-tray 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Existing app stop command completed (including tray)."
        } else {
            Write-Host "Existing app stop command was skipped with exit code $LASTEXITCODE."
        }
        $global:LASTEXITCODE = 0

        if ($null -ne $runningPid) {
            if (Wait-ProcessExit -ProcessId $runningPid -TimeoutSeconds 15) {
                Write-Host "Server process $runningPid stopped."
            } else {
                Write-Host "WARNING: Server process $runningPid is still running." -ForegroundColor Yellow
            }
        }

        try {
            & $PythonExe -c "from agent.bootstrap.tray import disable_autostart; disable_autostart()" 2>$null
            Write-Host "Autostart disabled."
        } catch {
        }
        $global:LASTEXITCODE = 0
    }

    # Also remove autostart registry entry directly in case Python is broken
    try {
        Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "k41-agent-tray" -ErrorAction SilentlyContinue
        Write-Host "Removed tray autostart registry entry."
    } catch {
    }
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
