#!/usr/bin/env bash
set -euo pipefail

Owner="${K41_AGENT_OWNER:-k4han}"
Repo="${K41_AGENT_REPO:-k41-agent}"
Branch="${K41_AGENT_BRANCH:-main}"
ReleaseTag="${K41_AGENT_RELEASE_TAG:-}"
ArtifactName="${K41_AGENT_ARTIFACT_NAME:-k41-agent-release.zip}"
PythonVersion="${K41_AGENT_PYTHON_VERSION:-3.13}"
UseBranchSource="${K41_AGENT_USE_BRANCH_SOURCE:-}"
SkipInit="${K41_AGENT_SKIP_INIT:-}"

AgentName="k41-agent"
DataHome="${XDG_DATA_HOME:-$HOME/.local/share}"
AgentHome="${K41_AGENT_HOME:-$DataHome/$AgentName}"
AppDir="$AgentHome/app"
BinDir="$AgentHome/bin"
ToolsDir="$AgentHome/tools"
EnvsDir="$AgentHome/envs"
DownloadDir="$AgentHome/download"

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
  --use-branch-source      Download source from the configured branch instead of a release artifact.
  --skip-init              Skip runtime initialization.
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
    --use-branch-source)
      UseBranchSource="true"
      shift
      ;;
    --skip-init)
      SkipInit="true"
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
    curl -fL "$url" -o "$output"
    return
  fi

  if command -v wget >/dev/null 2>&1; then
    wget -O "$output" "$url"
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

  os="$(uname -s)"
  arch="$(uname -m)"

  case "$os" in
    Darwin)
      case "$arch" in
        x86_64) echo "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-apple-darwin.tar.gz" ;;
        arm64|aarch64) echo "https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-apple-darwin.tar.gz" ;;
        *) echo "Unsupported macOS architecture: $arch" >&2; exit 1 ;;
      esac
      ;;
    Linux)
      if ldd --version 2>&1 | grep -qi musl; then
        libc="musl"
      fi

      case "$arch" in
        x86_64|amd64) echo "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-$libc.tar.gz" ;;
        aarch64|arm64) echo "https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-unknown-linux-$libc.tar.gz" ;;
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
    "$UvExe" --version
    return
  fi

  local uv_archive="$DownloadDir/uv.tar.gz"
  local uv_extract_dir="$DownloadDir/uv"
  local uv_url

  safe_rm_rf "$uv_extract_dir"
  mkdir -p "$uv_extract_dir"

  uv_url="$(get_uv_download_url)"
  download_file "$uv_url" "$uv_archive"
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
      --exclude '*.egg-info' \
      --exclude '*.pyc' \
      --exclude '*.pyo' \
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
      --exclude '*.egg-info' \
      --exclude '*.pyc' \
      --exclude '*.pyo' \
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

  "$PythonExe" - "$zip_path" "$destination" <<'PY'
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

ensure_venv() {
  "$UvExe" python install "$PythonVersion"

  local needs_create="true"
  if [[ -x "$PythonExe" ]]; then
    local current_version
    current_version="$("$PythonExe" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    if [[ "$current_version" == "$PythonVersion" ]]; then
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
AGENT_HOME="$AgentHome"
PYTHON_EXE="\$AGENT_HOME/envs/bin/python"
if [ ! -x "\$PYTHON_EXE" ]; then
  echo "python was not found at \$PYTHON_EXE." >&2
  exit 1
fi
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
if [[ -x "\$PythonExe" ]]; then
  "\$PythonExe" -m agent.bootstrap.cli stop || true
  echo "Existing app stop command completed."
else
  echo "No existing virtual environment found."
fi

stage "Update PATH"
remove_profile_block "\$HOME/.profile"
remove_profile_block "\$HOME/.bashrc"
remove_profile_block "\$HOME/.zshrc"
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

stop_existing_app() {
  if [[ ! -x "$PythonExe" ]]; then
    echo "No existing virtual environment found."
    return
  fi

  if "$PythonExe" -m agent.bootstrap.cli stop; then
    echo "Existing app stop command completed."
  else
    echo "Existing app stop command was skipped."
  fi
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

add_user_path() {
  case ":$PATH:" in
    *":$BinDir:"*) ;;
    *) export PATH="$BinDir:$PATH" ;;
  esac

  local profile_file
  profile_file="$(select_profile_file)"
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

clear_download_directory() {
  if [[ -d "$DownloadDir" ]]; then
    find "$DownloadDir" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  fi
}

require_command uname
require_command tar
require_command grep
require_command find
require_command chmod

stage "1. Prepare AGENT_HOME"
mkdir -p "$AgentHome" "$AppDir" "$BinDir" "$ToolsDir" "$DownloadDir"
echo "AGENT_HOME=$AgentHome"

stage "2. Stop existing app"
stop_existing_app

stage "3. Install uv"
install_uv

stage "4. Prepare virtual environment"
ensure_venv

stage "5. Install source"
install_source

stage "6. Sync application"
sync_app

stage "7. Create command wrappers"
write_command_wrappers
write_uninstall_wrapper
"$PythonExe" -m agent.bootstrap.cli --version

stage "8. Initialize runtime"
initialize_app

stage "9. Update PATH"
add_user_path

stage "10. Clean download cache"
clear_download_directory

echo
echo "Installation completed."
echo "Open a new terminal and run:"
echo "  k41"
echo "  k41 status"
echo "  k41 stop"
