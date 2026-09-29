#!/usr/bin/env bash
set -euo pipefail

Owner="${K41_AGENT_OWNER:-k4han}"
Repo="${K41_AGENT_REPO:-k41-agent}"
Branch="${K41_AGENT_BRANCH:-main}"
ReleaseTag="${K41_AGENT_RELEASE_TAG:-}"
ArtifactName="${K41_AGENT_ARTIFACT_NAME:-k41-agent-release.zip}"
PythonVersion="${K41_AGENT_PYTHON_VERSION:-3.13}"
UvVersion="${K41_AGENT_UV_VERSION:-0.12.13}"
UseBranchSource="${K41_AGENT_USE_BRANCH_SOURCE:-}"
SkipInit="${K41_AGENT_SKIP_INIT:-}"
EnableAutostart="${K41_AGENT_ENABLE_AUTOSTART:-true}"

AgentName="k41-agent"
DataHome="${XDG_DATA_HOME:-$HOME/.local/share}"
AgentHome="${K41_AGENT_HOME:-$DataHome/$AgentName}"
AppDir="$AgentHome/app"
BinDir="$AgentHome/bin"
ToolsDir="$AgentHome/tools"
EnvsDir="$AgentHome/envs"
DownloadDir="$AgentHome/download"
BackupDir="$AgentHome/backup"

UvExe="$ToolsDir/uv"
PythonExe="$EnvsDir/bin/python"
K41Cmd="$BinDir/k41"
UninstallSh="$AgentHome/uninstall.sh"
PathBlockBegin="# >>> k41-agent >>>"
PathBlockEnd="# <<< k41-agent <<<"

usage() {
  cat <<'EOF'
Usage: install.sh [options]

Options:
  --owner VALUE            GitHub owner. Defaults to k4han.
  --repo VALUE             GitHub repository. Defaults to k41-agent.
  --branch VALUE           Branch used with --use-branch-source. Defaults to main.
  --release-tag VALUE      Install a specific release tag.
  --artifact-name VALUE    Release artifact name. Defaults to k41-agent-release.zip.
  --python-version VALUE   Python version managed by uv. Defaults to 3.13.
  --uv-version VALUE       Pinned uv version. Defaults to 0.12.13 (use "latest" to follow upstream).
  --use-branch-source      Download source from the configured branch instead of a release artifact.
  --skip-init              Skip runtime initialization.
  --enable-autostart       Enable systemd user service autostart on boot (default).
  --no-autostart           Disable systemd user service autostart.
  --skip-autostart         Alias for --no-autostart.
  -h, --help               Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --owner)
      Owner="${2:?Missing value for --owner}"
      shift 2
      ;;
    --owner=*)
      Owner="${1#*=}"
      shift
      ;;
    --repo)
      Repo="${2:?Missing value for --repo}"
      shift 2
      ;;
    --repo=*)
      Repo="${1#*=}"
      shift
      ;;
    --branch)
      Branch="${2:?Missing value for --branch}"
      shift 2
      ;;
    --branch=*)
      Branch="${1#*=}"
      shift
      ;;
    --release-tag)
      ReleaseTag="${2:?Missing value for --release-tag}"
      shift 2
      ;;
    --release-tag=*)
      ReleaseTag="${1#*=}"
      shift
      ;;
    --artifact-name)
      ArtifactName="${2:?Missing value for --artifact-name}"
      shift 2
      ;;
    --artifact-name=*)
      ArtifactName="${1#*=}"
      shift
      ;;
    --python-version)
      PythonVersion="${2:?Missing value for --python-version}"
      shift 2
      ;;
    --python-version=*)
      PythonVersion="${1#*=}"
      shift
      ;;
    --uv-version)
      UvVersion="${2:?Missing value for --uv-version}"
      shift 2
      ;;
    --uv-version=*)
      UvVersion="${1#*=}"
      shift
      ;;
    --use-branch-source)
      UseBranchSource="true"
      shift
      ;;
    --skip-init)
      SkipInit="true"
      shift
      ;;
    --enable-autostart)
      EnableAutostart="true"
      shift
      ;;
    --no-autostart|--skip-autostart|--disable-autostart)
      EnableAutostart="false"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

stage() {
  printf '\n==> %s\n' "$1"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "$1 is required but was not found." >&2
    exit 1
  fi
}

download_file() {
  local url="$1"
  local output="$2"

  echo "Downloading $url"
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 --retry-delay 2 "$url" -o "$output"
    return
  fi

  if command -v wget >/dev/null 2>&1; then
    wget --tries=3 -O "$output" "$url"
    return
  fi

  echo "curl or wget is required to download files." >&2
  exit 1
}

normalize_bool() {
  case "${1:-}" in
    1|true|TRUE|True|yes|YES|Yes) return 0 ;;
    *) return 1 ;;
  esac
}

safe_rm_rf() {
  local target="$1"
  local resolved_target
  local resolved_home

  [[ -n "$target" ]] || {
    echo "Refusing to remove an empty path." >&2
    exit 1
  }

  resolved_home="$(cd "$AgentHome" && pwd -P)"
  if [[ -e "$target" ]]; then
    resolved_target="$(cd "$target" && pwd -P)"
  else
    resolved_target="$(cd "$(dirname "$target")" && pwd -P)/$(basename "$target")"
  fi

  case "$resolved_target" in
    "$resolved_home"/*) rm -rf "$target" ;;
    *) echo "Refusing to remove $target because it is outside AGENT_HOME." >&2; exit 1 ;;
  esac
}

get_uv_download_url() {
  local os
  local arch
  local libc="gnu"
  local version="${UvVersion:-0.12.13}"
  local base

  if [[ -z "$version" || "$version" == "latest" ]]; then
    base="https://github.com/astral-sh/uv/releases/latest/download"
  else
    # Upstream uv tags carry no "v" prefix (e.g. 0.12.13).
    version="${version#v}"
    version="${version#V}"
    base="https://github.com/astral-sh/uv/releases/download/$version"
  fi

  os="$(uname -s)"
  arch="$(uname -m)"

  case "$os" in
    Darwin)
      case "$arch" in
        x86_64) echo "$base/uv-x86_64-apple-darwin.tar.gz" ;;
        arm64|aarch64) echo "$base/uv-aarch64-apple-darwin.tar.gz" ;;
        *) echo "Unsupported macOS architecture: $arch" >&2; exit 1 ;;
      esac
      ;;
    Linux)
      if ldd --version 2>&1 | grep -qi musl; then
        libc="musl"
      fi

      case "$arch" in
        x86_64|amd64) echo "$base/uv-x86_64-unknown-linux-$libc.tar.gz" ;;
        aarch64|arm64) echo "$base/uv-aarch64-unknown-linux-$libc.tar.gz" ;;
        *) echo "Unsupported Linux architecture: $arch" >&2; exit 1 ;;
      esac
      ;;
    *)
      echo "Unsupported operating system: $os" >&2
      exit 1
      ;;
  esac
}

install_uv() {
  if [[ -x "$UvExe" ]]; then
    local installed
    installed="$("$UvExe" --version 2>/dev/null || true)"
    echo "Found $installed"
    if [[ -n "${UvVersion:-}" && "$UvVersion" != "latest" ]]; then
      local wanted="${UvVersion#v}"
      wanted="${wanted#V}"
      local installed_version
      installed_version="$(printf '%s' "$installed" | awk '{print $NF}')"
      if [[ "$installed_version" == "$wanted" ]]; then
        return
      fi
      echo "Updating uv to $wanted (was: $installed)."
    else
      return
    fi
  fi

  local uv_archive="$DownloadDir/uv.tar.gz"
  local uv_extract_dir="$DownloadDir/uv"
  local uv_url

  safe_rm_rf "$uv_extract_dir"
  mkdir -p "$uv_extract_dir"

  uv_url="$(get_uv_download_url)"
  if ! download_file "$uv_url" "$uv_archive"; then
    echo "Failed to download uv from $uv_url" >&2
    echo "The pinned version may not exist. Retry with --uv-version latest or set K41_AGENT_UV_VERSION=latest." >&2
    exit 1
  fi
  tar -xzf "$uv_archive" -C "$uv_extract_dir"

  local found_uv
  found_uv="$(find "$uv_extract_dir" -type f -name uv -perm -111 | head -n 1)"
  if [[ -z "$found_uv" ]]; then
    echo "uv was not found in the downloaded archive." >&2
    exit 1
  fi

  cp "$found_uv" "$UvExe"
  chmod +x "$UvExe"

  local found_uvx
  found_uvx="$(find "$uv_extract_dir" -type f -name uvx -perm -111 | head -n 1)"
  if [[ -n "$found_uvx" ]]; then
    cp "$found_uvx" "$ToolsDir/uvx"
    chmod +x "$ToolsDir/uvx"
  fi

  "$UvExe" --version
}

test_k41_project_root() {
  local path="$1"
  [[ -f "$path/pyproject.toml" ]] || return 1
  grep -Eq '^[[:space:]]*name[[:space:]]*=[[:space:]]*["'\'']k41-agent["'\''][[:space:]]*$' "$path/pyproject.toml"
}

find_k41_project_root() {
  local path="$1"

  if test_k41_project_root "$path"; then
    (cd "$path" && pwd -P)
    return
  fi

  while IFS= read -r project_file; do
    local candidate
    candidate="$(dirname "$project_file")"
    if test_k41_project_root "$candidate"; then
      (cd "$candidate" && pwd -P)
      return
    fi
  done < <(find "$path" -name pyproject.toml -type f)
}

assert_dashboard_build() {
  local root_path="$1"
  local index_file="$root_path/agent/delivery/http/dashboard/static/index.html"

  if [[ ! -f "$index_file" ]]; then
    echo "Dashboard frontend build is missing. Expected $index_file." >&2
    echo "Use a release artifact that includes agent/delivery/http/dashboard/static, or run pnpm dashboard:build before local development install." >&2
    exit 1
  fi
}

get_local_source_root() {
  local candidates=()
  local script_path="${BASH_SOURCE[0]:-}"

  if [[ -n "$script_path" && -f "$script_path" ]]; then
    candidates+=("$(cd "$(dirname "$script_path")" && pwd -P)")
  fi
  candidates+=("$(pwd -P)")

  local candidate
  for candidate in "${candidates[@]}"; do
    if test_k41_project_root "$candidate"; then
      (cd "$candidate" && pwd -P)
      return
    fi
  done
}

copy_source_tree() {
  local source_path="$1"
  local destination_path="$2"
  local source_resolved
  local destination_parent
  local destination_resolved

  source_resolved="$(cd "$source_path" && pwd -P)"
  mkdir -p "$destination_path"
  destination_resolved="$(cd "$destination_path" && pwd -P)"

  if [[ "$source_resolved" == "$destination_resolved" ]]; then
    echo "Source already matches the app directory."
    return
  fi

  case "$destination_resolved" in
    "$source_resolved"/*)
      echo "The app directory cannot be inside the source tree. Run the installer from a clone outside AGENT_HOME." >&2
      exit 1
      ;;
  esac

  # Keep in sync with install.ps1, release.yml and agent/bootstrap/update.py.
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude .git \
      --exclude .github \
      --exclude .venv \
      --exclude __pycache__ \
      --exclude .pytest_cache \
      --exclude .ruff_cache \
      --exclude .mypy_cache \
      --exclude '.tmp_*' \
      --exclude build \
      --exclude dist \
      --exclude node_modules \
      --exclude wheels \
      --exclude local-dev \
      --exclude data \
      --exclude '*.egg-info' \
      --exclude '*.pyc' \
      --exclude '*.pyo' \
      --exclude .env \
      --exclude '.env.*' \
      "$source_resolved/" "$destination_resolved/"
    return
  fi

  safe_rm_rf "$destination_path"
  mkdir -p "$destination_path"
  destination_parent="$(dirname "$destination_path")"
  (
    cd "$source_resolved"
    tar \
      --exclude .git \
      --exclude .github \
      --exclude .venv \
      --exclude __pycache__ \
      --exclude .pytest_cache \
      --exclude .ruff_cache \
      --exclude .mypy_cache \
      --exclude '.tmp_*' \
      --exclude build \
      --exclude dist \
      --exclude node_modules \
      --exclude wheels \
      --exclude local-dev \
      --exclude data \
      --exclude '*.egg-info' \
      --exclude '*.pyc' \
      --exclude '*.pyo' \
      --exclude .env \
      --exclude '.env.*' \
      -cf - .
  ) | (
    cd "$destination_parent"
    mkdir -p "$(basename "$destination_path")"
    cd "$(basename "$destination_path")"
    tar -xf -
  )
}

extract_zip() {
  local zip_path="$1"
  local destination="$2"

  mkdir -p "$destination"
  if command -v unzip >/dev/null 2>&1; then
    unzip -q -o "$zip_path" -d "$destination"
    return
  fi

  local py_bin=""
  if command -v python3 >/dev/null 2>&1; then
    py_bin="python3"
  elif [[ -x "$PythonExe" ]]; then
    py_bin="$PythonExe"
  elif command -v python >/dev/null 2>&1; then
    py_bin="python"
  else
    echo "unzip or python3 is required to extract release archives." >&2
    exit 1
  fi

  "$py_bin" - "$zip_path" "$destination" <<'PY'
import sys
import zipfile
from pathlib import Path

zip_path = Path(sys.argv[1])
destination = Path(sys.argv[2])
destination.mkdir(parents=True, exist_ok=True)

with zipfile.ZipFile(zip_path) as archive:
    archive.extractall(destination)
PY
}

install_source() {
  local local_source
  local_source="$(get_local_source_root || true)"

  if [[ -n "$local_source" ]]; then
    echo "Using local source at $local_source"
    copy_source_tree "$local_source" "$AppDir"
    assert_dashboard_build "$AppDir"
    return
  fi

  local source_zip="$DownloadDir/source.zip"
  local extract_dir="$DownloadDir/source"
  local source_url

  safe_rm_rf "$extract_dir"
  mkdir -p "$extract_dir"

  if normalize_bool "$UseBranchSource"; then
    source_url="https://github.com/$Owner/$Repo/archive/refs/heads/$Branch.zip"
  elif [[ -z "$ReleaseTag" ]]; then
    source_url="https://github.com/$Owner/$Repo/releases/latest/download/$ArtifactName"
  else
    source_url="https://github.com/$Owner/$Repo/releases/download/$ReleaseTag/$ArtifactName"
  fi

  download_file "$source_url" "$source_zip"
  extract_zip "$source_zip" "$extract_dir"

  local root
  root="$(find_k41_project_root "$extract_dir" || true)"
  if [[ -z "$root" ]]; then
    echo "Downloaded archive did not contain a k41-agent project root." >&2
    exit 1
  fi

  copy_source_tree "$root" "$AppDir"
  assert_dashboard_build "$AppDir"
}

normalize_python_version() {
  local value="${1:-}"
  if [[ "$value" =~ ^([0-9]+)(\.([0-9]+))? ]]; then
    if [[ -n "${BASH_REMATCH[3]:-}" ]]; then
      echo "${BASH_REMATCH[1]}.${BASH_REMATCH[3]}"
    else
      echo "${BASH_REMATCH[1]}"
    fi
  else
    echo "$value"
  fi
}

ensure_venv() {
  "$UvExe" python install "$PythonVersion"

  local wanted_version
  wanted_version="$(normalize_python_version "$PythonVersion")"
  local needs_create="true"
  if [[ -x "$PythonExe" ]]; then
    local current_version
    current_version="$("$PythonExe" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    if [[ "$(normalize_python_version "$current_version")" == "$wanted_version" ]]; then
      needs_create="false"
    fi
  fi

  if [[ "$needs_create" == "true" ]]; then
    safe_rm_rf "$EnvsDir"
    "$UvExe" venv --python "$PythonVersion" "$EnvsDir"
  fi

  "$PythonExe" --version
}

sync_app() {
  (
    cd "$AppDir"
    VIRTUAL_ENV="$EnvsDir" PATH="$EnvsDir/bin:$PATH" "$UvExe" sync --active --frozen --no-dev --compile-bytecode
  )
}

write_command_wrappers() {
  cat >"$K41Cmd" <<EOF
#!/usr/bin/env sh
export K41_AGENT_HOME="$AgentHome"
export AGENT_HOME="$AgentHome"
PYTHON_EXE="\$AGENT_HOME/envs/bin/python"
if [ ! -x "\$PYTHON_EXE" ]; then
  echo "python was not found at \$PYTHON_EXE." >&2
  exit 1
fi
export VIRTUAL_ENV="\$AGENT_HOME/envs"
export PATH="\$AGENT_HOME/envs/bin:\$AGENT_HOME/tools:\$PATH"
exec "\$PYTHON_EXE" -m agent.bootstrap.cli "\$@"
EOF
  chmod +x "$K41Cmd"
}

write_uninstall_wrapper() {
  cat >"$UninstallSh" <<EOF
#!/usr/bin/env bash
set -euo pipefail

AgentHome="$AgentHome"
BinDir="$BinDir"
PythonExe="$PythonExe"
RuntimeHome="\$HOME/.k41-agent"
RemoveRuntimeData="false"
PathBlockBegin="$PathBlockBegin"
PathBlockEnd="$PathBlockEnd"

while [[ \$# -gt 0 ]]; do
  case "\$1" in
    --remove-runtime-data)
      RemoveRuntimeData="true"
      shift
      ;;
    -h|--help)
      echo "Usage: uninstall.sh [--remove-runtime-data]"
      exit 0
      ;;
    *)
      echo "Unknown option: \$1" >&2
      exit 2
      ;;
  esac
done

stage() {
  printf '\\n==> %s\\n' "\$1"
}

remove_profile_block() {
  local file="\$1"
  [[ -f "\$file" ]] || return

  local tmp
  tmp="\$(mktemp)"
  awk -v begin="\$PathBlockBegin" -v end="\$PathBlockEnd" '
    \$0 == begin { skip = 1; next }
    \$0 == end { skip = 0; next }
    !skip { print }
  ' "\$file" >"\$tmp"
  mv "\$tmp" "\$file"
}

stage "Stop app"
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user disable --now k41-agent.service 2>/dev/null || true
  rm -f "\$HOME/.config/systemd/user/k41-agent.service" 2>/dev/null || true
  systemctl --user daemon-reload 2>/dev/null || true
  echo "Removed systemd user service (if present)."
fi
if [[ -x "\$PythonExe" ]]; then
  "\$PythonExe" -m agent.bootstrap.cli stop --with-tray 2>/dev/null || true
  echo "Existing app stop command completed (including tray)."
  "\$PythonExe" -c "from agent.bootstrap.tray import disable_autostart; disable_autostart()" 2>/dev/null || true
  # Also remove autostart files directly
  rm -f "\$HOME/.config/autostart/k41-agent-tray.desktop" 2>/dev/null || true
  rm -f "\$HOME/Library/LaunchAgents/com.k41.agent.tray.plist" 2>/dev/null || true
else
  echo "No existing virtual environment found."
fi

stage "Update PATH"
remove_profile_block "\$HOME/.profile"
remove_profile_block "\$HOME/.bashrc"
remove_profile_block "\$HOME/.zshrc"
rm -f "\$HOME/.config/fish/conf.d/k41-agent.fish" 2>/dev/null || true
echo "Removed K41 Agent PATH block from supported shell profiles."

if [[ "\$RemoveRuntimeData" == "true" ]]; then
  stage "Remove runtime data"
  rm -rf "\$RuntimeHome"
  echo "Removed \$RuntimeHome."
else
  stage "Keep runtime data"
  echo "Runtime data was kept at \$RuntimeHome."
fi

stage "Remove installation"
rm -rf "\$AgentHome"

echo
echo "Uninstallation completed."
EOF
  chmod +x "$UninstallSh"
}

initialize_app() {
  if normalize_bool "$SkipInit"; then
    echo "Runtime initialization skipped."
    return
  fi

  "$PythonExe" -m agent.bootstrap.cli init
}

wait_for_pid_exit() {
  local pid="$1"
  local timeout_seconds="${2:-15}"
  local waited=0

  while kill -0 "$pid" 2>/dev/null; do
    if [[ "$waited" -ge "$timeout_seconds" ]]; then
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
  return 0
}

backup_app_source() {
  BACKUP_PATH=""
  if [[ ! -d "$AppDir" ]]; then
    return
  fi
  if ! test_k41_project_root "$AppDir"; then
    echo "Existing app directory is not a k41-agent project, skipping backup."
    return
  fi

  local version="unknown"
  version="$(grep -E '^[[:space:]]*version[[:space:]]*=' "$AppDir/pyproject.toml" 2>/dev/null | head -n 1 | sed -E 's/.*["'\'']([^"'\'']+)["'\''].*/\1/' || true)"
  [[ -n "$version" ]] || version="unknown"
  local timestamp
  timestamp="$(date -u +%Y%m%d%H%M%S)"
  mkdir -p "$BackupDir"
  BACKUP_PATH="$BackupDir/app-$version-$timestamp"
  echo "Backing up existing app to $BACKUP_PATH"
  copy_source_tree "$AppDir" "$BACKUP_PATH"

  # Keep the 2 newest backups.
  local count=0
  while IFS= read -r old_backup; do
    [[ -n "$old_backup" ]] || continue
    count=$((count + 1))
    if [[ "$count" -gt 2 ]]; then
      safe_rm_rf "$old_backup"
    fi
  done < <(ls -dt "$BackupDir"/app-* 2>/dev/null || true)
}

stop_existing_app() {
  if command -v systemctl >/dev/null 2>&1; then
    if systemctl --user is-active --quiet k41-agent.service 2>/dev/null; then
      echo "Stopping systemd service k41-agent.service."
      systemctl --user stop k41-agent.service 2>/dev/null || true
    fi
  fi
  if [[ ! -x "$PythonExe" ]]; then
    echo "No existing virtual environment found."
    return
  fi

  local running_pid=""
  if [[ -f "$HOME/.k41-agent/server.pid" ]]; then
    running_pid="$(cat "$HOME/.k41-agent/server.pid" 2>/dev/null | tr -d '[:space:]' || true)"
  fi

  if "$PythonExe" -m agent.bootstrap.cli stop --with-tray 2>/dev/null; then
    echo "Existing app stop command completed (including tray)."
  else
    echo "Existing app stop command was skipped."
  fi

  if [[ -n "$running_pid" && "$running_pid" =~ ^[0-9]+$ ]]; then
    if wait_for_pid_exit "$running_pid" 15; then
      echo "Server process $running_pid stopped."
    else
      echo "WARNING: Server process $running_pid is still running. Files may be locked." >&2
    fi
  fi

  "$PythonExe" -c "from agent.bootstrap.tray import disable_autostart; disable_autostart()" 2>/dev/null || true
}

select_profile_file() {
  local shell_name
  shell_name="$(basename "${SHELL:-}")"

  case "$shell_name" in
    zsh) echo "$HOME/.zshrc" ;;
    bash) echo "$HOME/.bashrc" ;;
    *) echo "$HOME/.profile" ;;
  esac
}

write_path_block() {
  local profile_file="$1"
  touch "$profile_file"

  if grep -Fq "$PathBlockBegin" "$profile_file"; then
    echo "$BinDir is already configured in $profile_file."
    return
  fi

  {
    echo
    echo "$PathBlockBegin"
    echo "export PATH=\"$BinDir:\$PATH\""
    echo "$PathBlockEnd"
  } >>"$profile_file"

  echo "Added $BinDir to PATH in $profile_file."
}

add_user_path() {
  case ":$PATH:" in
    *":$BinDir:"*) ;;
    *) export PATH="$BinDir:$PATH" ;;
  esac

  if [[ "${#PATH}" -gt 1900 ]]; then
    echo "WARNING: PATH is getting long (${#PATH} chars)." >&2
  fi

  # Fish shell uses a different config location.
  local fish_config="$HOME/.config/fish/conf.d/k41-agent.fish"
  if command -v fish >/dev/null 2>&1 || [[ -d "$HOME/.config/fish" ]]; then
    mkdir -p "$(dirname "$fish_config")"
    if [[ ! -f "$fish_config" ]] || ! grep -Fq "$BinDir" "$fish_config"; then
      echo "fish_add_path \"$BinDir\"" >"$fish_config"
      echo "Added $BinDir to PATH in $fish_config."
    else
      echo "$BinDir is already configured in $fish_config."
    fi
  fi

  local profile_file
  profile_file="$(select_profile_file)"
  write_path_block "$profile_file"
  # Also ensure .profile has the block so login shells pick it up
  # even when the installer guessed bash/zsh from $SHELL.
  if [[ "$profile_file" != "$HOME/.profile" ]]; then
    write_path_block "$HOME/.profile"
  fi
}

clear_download_directory() {
  if [[ -d "$DownloadDir" ]]; then
    find "$DownloadDir" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  fi
}

install_systemd_service() {
  if ! normalize_bool "$EnableAutostart"; then
    echo "Autostart disabled (--no-autostart or K41_AGENT_ENABLE_AUTOSTART=false)."
    if command -v systemctl >/dev/null 2>&1; then
      systemctl --user disable --now k41-agent.service 2>/dev/null || true
      rm -f "$HOME/.config/systemd/user/k41-agent.service" 2>/dev/null || true
      systemctl --user daemon-reload 2>/dev/null || true
    fi
    return
  fi

  if [[ "$(uname -s)" != "Linux" ]]; then
    echo "Systemd autostart is only supported on Linux, skipping."
    return
  fi

  if ! command -v systemctl >/dev/null 2>&1; then
    echo "WARNING: systemctl was not found, skipping systemd autostart." >&2
    return
  fi

  if [[ ! -x "$PythonExe" ]]; then
    echo "WARNING: Python was not found at $PythonExe, skipping systemd autostart." >&2
    return
  fi

  export K41_AGENT_HOME="$AgentHome"
  export AGENT_HOME="$AgentHome"
  if "$PythonExe" -m agent.bootstrap.cli service install; then
    echo "Systemd user service installed and started."
    if systemctl --user is-active --quiet k41-agent.service 2>/dev/null; then
      echo "Verified k41-agent.service is active."
    else
      echo "WARNING: k41-agent.service is not active. Check logs with: journalctl --user -u k41-agent.service -e" >&2
    fi
  else
    echo "WARNING: Could not install systemd user service. The app still runs via 'k41'." >&2
  fi
}

require_command uname
require_command tar
require_command grep
require_command find
require_command chmod

if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
  echo "curl or wget is required to download files." >&2
  exit 1
fi

if ! command -v unzip >/dev/null 2>&1 && ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
  echo "unzip or python3 is required to extract release archives." >&2
  exit 1
fi

stage "1. Prepare AGENT_HOME"
mkdir -p "$AgentHome" "$AppDir" "$BinDir" "$ToolsDir" "$DownloadDir" "$BackupDir"
echo "AGENT_HOME=$AgentHome"

stage "2. Stop existing app"
stop_existing_app

stage "3. Install uv (pinned: $UvVersion)"
install_uv

BACKUP_PATH=""
stage "4. Backup existing app"
backup_app_source
if [[ -n "$BACKUP_PATH" ]]; then
  echo "Backup created at $BACKUP_PATH"
else
  echo "No backup needed (fresh install)."
fi

report_install_failure() {
  local step="$1"
  echo "Installation failed during $step." >&2
  if [[ -n "$BACKUP_PATH" && -d "$BACKUP_PATH" ]]; then
    echo "Previous app backup was kept at $BACKUP_PATH." >&2
    echo "To restore manually, copy it back to $AppDir and re-run uv sync." >&2
  fi
  exit 1
}

# Keep install.sh stage order in sync with install.ps1:
# source -> venv -> sync, so a fresh venv never blocks archive extraction.
stage "5. Install source"
if ! install_source; then
  report_install_failure "source installation"
fi

stage "6. Prepare virtual environment"
if ! ensure_venv; then
  report_install_failure "virtual environment setup"
fi

stage "7. Sync application"
if ! sync_app; then
  report_install_failure "dependency sync"
fi

stage "8. Create command wrappers"
write_command_wrappers
write_uninstall_wrapper
"$PythonExe" -m agent.bootstrap.cli --version

stage "9. Initialize runtime"
initialize_app

stage "10. Update PATH"
add_user_path

stage "11. Clean download cache"
clear_download_directory

stage "12. Enable autostart (systemd)"
install_systemd_service

echo
echo "Installation completed."
echo "Open a new terminal and run:"
echo "  k41"
echo "  k41 status"
echo "  k41 stop"
echo "  k41 service status"
