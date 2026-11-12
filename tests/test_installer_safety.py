"""Run installer functions in isolation; never install or stop real processes."""

import hashlib
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs

ROOT = Path(__file__).resolve().parents[1]


def _powershell(script: str, directory: Path):
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("PowerShell is not installed")
    return subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=30,
        **hidden_subprocess_kwargs(),
    )


def _ps_function(name: str) -> str:
    path = str(ROOT / "install.ps1").replace("'", "''")
    return f"""
$ErrorActionPreference = 'Stop'
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{path}', [ref]$null, [ref]$null)
$definition = $ast.Find({{ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq '{name}' }}, $true)
Invoke-Expression $definition.Extent.Text
"""


def _bash(script: str, directory: Path):
    candidates = [shutil.which("bash"), "C:/Program Files/Git/bin/bash.exe"]
    executable = next(
        (
            candidate
            for candidate in candidates
            if candidate and Path(candidate).is_file()
        ),
        None,
    )
    if executable is None:
        pytest.skip("Bash is not installed")
    return subprocess.run(
        [executable, "-c", script],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=30,
        **hidden_subprocess_kwargs(),
    )


def _sh_function(name: str) -> str:
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    match = re.search(
        r"^" + name + r"\(\) \{\n.*?^\}", source, re.MULTILINE | re.DOTALL
    )
    assert match is not None
    return match[0]


def test_powershell_installer_refuses_to_replace_running_application(tmp_path):
    stub = tmp_path / "python-stub.ps1"
    stub.write_text("exit 0\n")
    script = (
        _ps_function("Stop-ExistingApp")
        + """
$PythonExe = Join-Path (Get-Location) 'python-stub.ps1'
function Get-RunningServerPid { return 123 }
function Wait-ProcessExit { return $false }
Stop-ExistingApp
Set-Content -LiteralPath 'replaced' -Value 'unsafe'
"""
    )
    result = _powershell(script, tmp_path)
    assert result.returncode != 0
    assert "still running" in result.stdout + result.stderr
    assert not (tmp_path / "replaced").exists()


def test_shell_installer_refuses_unconfirmed_shutdown(tmp_path):
    stub = tmp_path / "python-stub"
    stub.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    stub.chmod(0o700)
    script = (
        _sh_function("stop_existing_app")
        + """
PythonExe=./python-stub
systemctl() { return 1; }
wait_for_pid_exit() { return 1; }
stop_existing_app || exit 17
touch replaced
"""
    )
    result = _bash(script, tmp_path)
    assert result.returncode == 17, result.stdout + result.stderr
    assert "Installation was aborted" in result.stderr
    assert not (tmp_path / "replaced").exists()


@pytest.mark.parametrize("valid", [True, False])
def test_powershell_verifies_checksum_before_extracting(tmp_path, valid):
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(b"release archive")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest() if valid else "0" * 64
    (tmp_path / "checksum").write_text(digest + "  k41-agent-release.zip\n")
    script = (
        _ps_function("Install-Source")
        + """
$DownloadDir = Join-Path (Get-Location) 'download'
$AppDir = Join-Path (Get-Location) 'app'
$Owner = 'test'; $Repo = 'test'; $ReleaseTag = 'v1'; $ArtifactName = 'k41-agent-release.zip'
$UseBranchSource = $false
function Get-LocalSourceRoot { return $null }
function Invoke-WebRequest {
    param($Uri, $OutFile)
    Copy-Item -LiteralPath $(if ($Uri.EndsWith('.sha256')) { 'checksum' } else { 'artifact.zip' }) -Destination $OutFile
}
function Expand-Archive { Set-Content -LiteralPath 'extracted' -Value 'verified' }
function Find-K41ProjectRoot { return (Get-Location).Path }
function Copy-SourceTree { Set-Content -LiteralPath 'replaced' -Value 'verified' }
function Assert-DashboardBuild { }
New-Item -ItemType Directory -Path $DownloadDir | Out-Null
Install-Source
"""
    )
    result = _powershell(script, tmp_path)
    assert (result.returncode == 0) is valid, result.stdout + result.stderr
    assert (tmp_path / "extracted").exists() is valid
    assert (tmp_path / "replaced").exists() is valid


@pytest.mark.parametrize("valid", [True, False])
def test_shell_verifies_checksum_before_extracting(tmp_path, valid):
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(b"release archive")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest() if valid else "0" * 64
    (tmp_path / "checksum").write_text(digest + "  k41-agent-release.zip\n")
    script = (
        _sh_function("install_source")
        + """
DownloadDir=download
AppDir=app
Owner=test
Repo=test
ReleaseTag=v1
ArtifactName=k41-agent-release.zip
UseBranchSource=false
get_local_source_root() { return 1; }
normalize_bool() { [[ "$1" == true ]]; }
safe_rm_rf() { return 0; }
download_file() {
    if [[ "$1" == *.sha256 ]]; then cp checksum "$2"; else cp artifact.zip "$2"; fi
}
extract_zip() { touch extracted; }
find_k41_project_root() { printf 'source'; }
copy_source_tree() { touch replaced; }
assert_dashboard_build() { return 0; }
install_source || exit 19
"""
    )
    result = _bash(script, tmp_path)
    assert (result.returncode == 0) is valid, result.stdout + result.stderr
    assert (tmp_path / "extracted").exists() is valid
    assert (tmp_path / "replaced").exists() is valid
