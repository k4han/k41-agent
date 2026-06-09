param(
    [string]$Owner,
    [string]$Repo,
    [string]$Branch,
    [string]$ReleaseTag,
    [string]$ArtifactName,
    [string]$PythonVersion,
    [switch]$UseBranchSource,
    [switch]$SkipInit
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Owner = if ($Owner) { $Owner } elseif ($env:K41_AGENT_OWNER) { $env:K41_AGENT_OWNER } else { "k4han" }
$Repo = if ($Repo) { $Repo } elseif ($env:K41_AGENT_REPO) { $env:K41_AGENT_REPO } else { "k41-agent" }
$Branch = if ($Branch) { $Branch } elseif ($env:K41_AGENT_BRANCH) { $env:K41_AGENT_BRANCH } else { "main" }
$ReleaseTag = if ($ReleaseTag) { $ReleaseTag } elseif ($env:K41_AGENT_RELEASE_TAG) { $env:K41_AGENT_RELEASE_TAG } else { "" }
$ArtifactName = if ($ArtifactName) { $ArtifactName } elseif ($env:K41_AGENT_ARTIFACT_NAME) { $env:K41_AGENT_ARTIFACT_NAME } else { "k41-agent-release.zip" }
$PythonVersion = if ($PythonVersion) { $PythonVersion } elseif ($env:K41_AGENT_PYTHON_VERSION) { $env:K41_AGENT_PYTHON_VERSION } else { "3.13" }
$UseBranchSource = $UseBranchSource -or ($env:K41_AGENT_USE_BRANCH_SOURCE -match "^(1|true|yes)$")

$AgentName = "k41-agent"

if (-not $env:LOCALAPPDATA) {
    throw "LOCALAPPDATA is not set."
}

$AgentHome = Join-Path $env:LOCALAPPDATA $AgentName
$AppDir = Join-Path $AgentHome "app"
$BinDir = Join-Path $AgentHome "bin"
$ToolsDir = Join-Path $AgentHome "tools"
$EnvsDir = Join-Path $AgentHome "envs"
$DownloadDir = Join-Path $AgentHome "download"

$UvExe = Join-Path $ToolsDir "uv.exe"
$PythonExe = Join-Path $EnvsDir "Scripts\python.exe"
$K41Cmd = Join-Path $BinDir "k41.cmd"
$UninstallCmd = Join-Path $AgentHome "uninstall.cmd"

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

function Add-UserPath {
    param([string]$PathToAdd)

    $target = Normalize-PathEntry $PathToAdd
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $entries = @()
    if ($currentPath) {
        $entries = @($currentPath -split ";" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    }

    $exists = $false
    foreach ($entry in $entries) {
        if ((Normalize-PathEntry $entry) -ieq $target) {
            $exists = $true
            break
        }
    }

    if (-not $exists) {
        $newPath = (@($entries) + $PathToAdd) -join ";"
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        if ($env:Path) {
            $env:Path = "$env:Path;$PathToAdd"
        } else {
            $env:Path = $PathToAdd
        }
        Write-Host "Added $PathToAdd to the user PATH."
        return
    }

    Write-Host "$PathToAdd is already in the user PATH."
}

function Invoke-CheckedCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE."
    }
}

function Test-K41ProjectRoot {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }

    $projectFile = Join-Path $Path "pyproject.toml"
    if (-not (Test-Path -LiteralPath $projectFile -PathType Leaf)) {
        return $false
    }

    $content = Get-Content -LiteralPath $projectFile -Raw
    return $content -match "(?m)^\s*name\s*=\s*[""']k41-agent[""']\s*$"
}

function Find-K41ProjectRoot {
    param([string]$Path)

    if (Test-K41ProjectRoot $Path) {
        return (Resolve-Path -LiteralPath $Path).Path
    }

    $projectFiles = Get-ChildItem -LiteralPath $Path -Filter "pyproject.toml" -Recurse -File
    foreach ($projectFile in $projectFiles) {
        $candidate = $projectFile.Directory.FullName
        if (Test-K41ProjectRoot $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    return $null
}

function Assert-DashboardBuild {
    param([string]$RootPath)

    $indexFile = Join-Path $RootPath "agent\delivery\http\dashboard\static\index.html"
    if (-not (Test-Path -LiteralPath $indexFile -PathType Leaf)) {
        throw "Dashboard frontend build is missing. Expected $indexFile. Use a release artifact that includes agent/delivery/http/dashboard/static, or run pnpm dashboard:build before local development install."
    }
}

function Get-LocalSourceRoot {
    $candidates = @()
    if ($PSScriptRoot) {
        $candidates += $PSScriptRoot
    }
    $candidates += (Get-Location).Path

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-K41ProjectRoot $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    return $null
}

function Get-UvDownloadUrl {
    $architecture = $null
    try {
        $osArchitecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
        if ($null -ne $osArchitecture) {
            $architecture = $osArchitecture.ToString()
        }
    } catch {
        $architecture = $null
    }

    if ([string]::IsNullOrWhiteSpace($architecture)) {
        $architecture = $env:PROCESSOR_ARCHITEW6432
    }
    if ([string]::IsNullOrWhiteSpace($architecture)) {
        $architecture = $env:PROCESSOR_ARCHITECTURE
    }
    if ([string]::IsNullOrWhiteSpace($architecture)) {
        throw "Unable to determine Windows architecture."
    }

    $architecture = $architecture.ToLowerInvariant()

    switch ($architecture) {
        "amd64" { return "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip" }
        "x64" { return "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip" }
        "aarch64" { return "https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-pc-windows-msvc.zip" }
        "arm64" { return "https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-pc-windows-msvc.zip" }
        default { throw "Unsupported Windows architecture: $architecture." }
    }
}

function Install-Uv {
    $legacyUvExe = Join-Path $BinDir "uv.exe"
    if (Test-Path -LiteralPath $legacyUvExe -PathType Leaf) {
        Remove-Item -LiteralPath $legacyUvExe -Force
        Write-Host "Removed legacy uv.exe from $BinDir."
    }

    $legacyUvxExe = Join-Path $BinDir "uvx.exe"
    if (Test-Path -LiteralPath $legacyUvxExe -PathType Leaf) {
        Remove-Item -LiteralPath $legacyUvxExe -Force
        Write-Host "Removed legacy uvx.exe from $BinDir."
    }

    if (Test-Path -LiteralPath $UvExe -PathType Leaf) {
        Invoke-CheckedCommand $UvExe @("--version")
        return
    }

    $uvArchive = Join-Path $DownloadDir "uv.zip"
    $uvExtractDir = Join-Path $DownloadDir "uv"
    if (Test-Path -LiteralPath $uvExtractDir) {
        Remove-Item -LiteralPath $uvExtractDir -Recurse -Force
    }

    $uvUrl = Get-UvDownloadUrl
    Write-Host "Downloading $uvUrl"
    Invoke-WebRequest -Uri $uvUrl -OutFile $uvArchive

    New-Item -ItemType Directory -Force -Path $uvExtractDir | Out-Null
    Expand-Archive -LiteralPath $uvArchive -DestinationPath $uvExtractDir -Force

    $foundUv = Get-ChildItem -LiteralPath $uvExtractDir -Filter "uv.exe" -Recurse | Select-Object -First 1
    if (-not $foundUv) {
        throw "uv.exe was not found in the downloaded archive."
    }

    Copy-Item -LiteralPath $foundUv.FullName -Destination $UvExe -Force

    $foundUvx = Get-ChildItem -LiteralPath $uvExtractDir -Filter "uvx.exe" -Recurse | Select-Object -First 1
    if ($foundUvx) {
        Copy-Item -LiteralPath $foundUvx.FullName -Destination (Join-Path $ToolsDir "uvx.exe") -Force
    }

    Invoke-CheckedCommand $UvExe @("--version")
}

function Copy-SourceTree {
    param(
        [string]$SourcePath,
        [string]$DestinationPath
    )

    $sourceResolved = (Resolve-Path -LiteralPath $SourcePath).Path.TrimEnd("\")
    New-Item -ItemType Directory -Force -Path $DestinationPath | Out-Null
    $destinationResolved = (Resolve-Path -LiteralPath $DestinationPath).Path.TrimEnd("\")

    if ($sourceResolved -ieq $destinationResolved) {
        Write-Host "Source already matches the app directory."
        return
    }

    if ($destinationResolved.StartsWith("$sourceResolved\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "The app directory cannot be inside the source tree. Run the installer from a clone outside AGENT_HOME."
    }

    $excludedDirs = @(
        ".git",
        ".github",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".tmp_*",
        "build",
        "dist",
        "node_modules",
        "wheels",
        "*.egg-info"
    )
    $excludedFiles = @("*.pyc", "*.pyo")

    & robocopy $sourceResolved $destinationResolved /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS /NP /XD $excludedDirs /XF $excludedFiles | Out-Host
    $robocopyExitCode = $LASTEXITCODE
    if ($robocopyExitCode -gt 7) {
        throw "robocopy failed with exit code $robocopyExitCode."
    }
    $global:LASTEXITCODE = 0
}

function Install-Source {
    $localSource = Get-LocalSourceRoot
    if ($localSource) {
        Write-Host "Using local source at $localSource"
        Copy-SourceTree -SourcePath $localSource -DestinationPath $AppDir
        Assert-DashboardBuild $AppDir
        return
    }

    $sourceZip = Join-Path $DownloadDir "source.zip"
    $extractDir = Join-Path $DownloadDir "source"
    if (Test-Path -LiteralPath $extractDir) {
        Remove-Item -LiteralPath $extractDir -Recurse -Force
    }

    if ($UseBranchSource) {
        $sourceUrl = "https://github.com/$Owner/$Repo/archive/refs/heads/$Branch.zip"
    } elseif ([string]::IsNullOrWhiteSpace($ReleaseTag)) {
        $sourceUrl = "https://github.com/$Owner/$Repo/releases/latest/download/$ArtifactName"
    } else {
        $sourceUrl = "https://github.com/$Owner/$Repo/releases/download/$ReleaseTag/$ArtifactName"
    }

    Write-Host "Downloading $sourceUrl"
    Invoke-WebRequest -Uri $sourceUrl -OutFile $sourceZip

    New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
    Expand-Archive -LiteralPath $sourceZip -DestinationPath $extractDir -Force

    $root = Find-K41ProjectRoot $extractDir
    if (-not $root) {
        throw "Downloaded archive did not contain a k41-agent project root."
    }

    Copy-SourceTree -SourcePath $root -DestinationPath $AppDir
    Assert-DashboardBuild $AppDir
}

function Ensure-Venv {
    Invoke-CheckedCommand $UvExe @("python", "install", $PythonVersion)

    $needsCreate = $true
    if (Test-Path -LiteralPath $PythonExe -PathType Leaf) {
        $currentVersion = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ($LASTEXITCODE -eq 0 -and $currentVersion -eq $PythonVersion) {
            $needsCreate = $false
        }
    }

    if ($needsCreate) {
        if (Test-Path -LiteralPath $EnvsDir) {
            Remove-Item -LiteralPath $EnvsDir -Recurse -Force
        }
        Invoke-CheckedCommand $UvExe @("venv", "--python", $PythonVersion, $EnvsDir)
    }

    Invoke-CheckedCommand $PythonExe @("--version")
}

function Sync-App {
    $scriptsDir = Join-Path $EnvsDir "Scripts"
    $previousVirtualEnv = $env:VIRTUAL_ENV
    $previousPath = $env:Path

    Push-Location $AppDir
    try {
        $env:VIRTUAL_ENV = $EnvsDir
        $env:Path = "$scriptsDir;$previousPath"
        Invoke-CheckedCommand $UvExe @("sync", "--active", "--frozen", "--no-dev", "--compile-bytecode")
    } finally {
        $env:VIRTUAL_ENV = $previousVirtualEnv
        $env:Path = $previousPath
        Pop-Location
    }
}

function Write-CommandWrappers {
    $legacyPs1 = Join-Path $BinDir "k41.ps1"
    if (Test-Path -LiteralPath $legacyPs1 -PathType Leaf) {
        Remove-Item -LiteralPath $legacyPs1 -Force
        Write-Host "Removed legacy PowerShell wrapper at $legacyPs1."
    }

    @"
@echo off
set "AGENT_HOME=%LOCALAPPDATA%\$AgentName"
set "PYTHON_EXE=%AGENT_HOME%\envs\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  echo python.exe was not found at "%PYTHON_EXE%".
  exit /b 1
)
"%PYTHON_EXE%" -m agent.bootstrap.cli %*
exit /b %ERRORLEVEL%
"@ | Set-Content -LiteralPath $K41Cmd -Encoding ASCII
}

function Write-UninstallWrapper {
    @"
@echo off
setlocal EnableExtensions

set "AGENT_HOME=%LOCALAPPDATA%\$AgentName"
set "BIN_DIR=%AGENT_HOME%\bin"
set "PYTHON_EXE=%AGENT_HOME%\envs\Scripts\python.exe"
set "RUNTIME_HOME=%USERPROFILE%\.k41-agent"
set "REMOVE_RUNTIME_DATA=0"

:parse_args
if "%~1"=="" goto after_args
if /I "%~1"=="--remove-runtime-data" (
  set "REMOVE_RUNTIME_DATA=1"
  shift
  goto parse_args
)
if /I "%~1"=="-RemoveRuntimeData" (
  set "REMOVE_RUNTIME_DATA=1"
  shift
  goto parse_args
)
if /I "%~1"=="/RemoveRuntimeData" (
  set "REMOVE_RUNTIME_DATA=1"
  shift
  goto parse_args
)
echo Unknown option: %~1
exit /b 2

:after_args
echo.
echo ==> Stop app
if not exist "%PYTHON_EXE%" goto no_python
"%PYTHON_EXE%" -m agent.bootstrap.cli stop
if errorlevel 1 goto stop_skipped
echo Existing app stop command completed.
goto after_stop

:stop_skipped
echo Existing app stop command was skipped.
goto after_stop

:no_python
echo No existing virtual environment found.

:after_stop

echo.
echo ==> Update PATH
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$target = ([string]`$env:BIN_DIR).TrimEnd('\'); `$current = [Environment]::GetEnvironmentVariable('Path', 'User'); if ([string]::IsNullOrWhiteSpace(`$current)) { Write-Host 'The user PATH is empty.' } else { `$entries = @(`$current -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace(`$_) }); `$newEntries = @(`$entries | Where-Object { ((`$_.Trim()) -replace '[\\/]+$', '') -ine `$target }); if (`$newEntries.Count -eq `$entries.Count) { Write-Host (`$env:BIN_DIR + ' is not in the user PATH.') } else { [Environment]::SetEnvironmentVariable('Path', (`$newEntries -join ';'), 'User'); Write-Host (`$env:BIN_DIR + ' was removed from the user PATH.') } }"
if errorlevel 1 (
  echo Failed to update the user PATH.
  exit /b 1
)

if "%REMOVE_RUNTIME_DATA%"=="1" (
  echo.
  echo ==> Remove runtime data
  if exist "%RUNTIME_HOME%" (
    rmdir /s /q "%RUNTIME_HOME%"
    if exist "%RUNTIME_HOME%" (
      echo Failed to remove "%RUNTIME_HOME%".
      exit /b 1
    )
    echo Removed "%RUNTIME_HOME%".
  ) else (
    echo "%RUNTIME_HOME%" does not exist.
  )
) else (
  echo.
  echo ==> Keep runtime data
  echo Runtime data was kept at "%RUNTIME_HOME%".
)

echo.
echo ==> Remove installation
cd /d "%TEMP%"
set "CLEANUP_CMD=%TEMP%\k41-agent-uninstall-%RANDOM%-%RANDOM%.cmd"
> "%CLEANUP_CMD%" echo @echo off
>> "%CLEANUP_CMD%" echo timeout /t 2 /nobreak ^>nul
>> "%CLEANUP_CMD%" echo rmdir /s /q "%AGENT_HOME%"
>> "%CLEANUP_CMD%" echo del /f /q "%%~f0"
start "" /min cmd.exe /d /c call "%CLEANUP_CMD%"

echo Removal of "%AGENT_HOME%" has been scheduled.
echo.
echo Uninstallation completed.
exit /b 0
"@ | Set-Content -LiteralPath $UninstallCmd -Encoding ASCII
}

function Initialize-App {
    if ($SkipInit) {
        Write-Host "Runtime initialization skipped."
        return
    }

    Invoke-CheckedCommand $PythonExe @("-m", "agent.bootstrap.cli", "init")
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

function Clear-DownloadDirectory {
    if (-not (Test-Path -LiteralPath $DownloadDir)) {
        return
    }

    Get-ChildItem -LiteralPath $DownloadDir -Force | Remove-Item -Recurse -Force
}

Stage "1. Prepare AGENT_HOME"
New-Item -ItemType Directory -Force -Path $AgentHome, $AppDir, $BinDir, $ToolsDir, $DownloadDir | Out-Null
Write-Host "AGENT_HOME=$AgentHome"

Stage "2. Stop existing app"
Stop-ExistingApp

Stage "3. Install uv"
Install-Uv

Stage "4. Install source"
Install-Source

Stage "5. Prepare virtual environment"
Ensure-Venv

Stage "6. Sync application"
Sync-App

Stage "7. Create command wrappers"
Write-CommandWrappers
Write-UninstallWrapper
Invoke-CheckedCommand $PythonExe @("-m", "agent.bootstrap.cli", "--version")

Stage "8. Initialize runtime"
Initialize-App

Stage "9. Update PATH"
Add-UserPath $BinDir

Stage "10. Clean download cache"
Clear-DownloadDirectory

Write-Host ""
Write-Host "Installation completed." -ForegroundColor Green
Write-Host "Open a new terminal and run:"
Write-Host "  k41"
Write-Host "  k41 status"
Write-Host "  k41 stop"
