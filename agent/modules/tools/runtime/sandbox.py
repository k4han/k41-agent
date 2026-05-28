"""Sandbox utilities — environment isolation for tool execution."""

from __future__ import annotations

import os
import platform
import re
from typing import Dict, Optional, Set

# ===================== Env Filtering =====================

# Whitelist: only essential system variables needed for Python subprocess.
# All other variables (API keys, tokens, secrets...) are removed by default.
_WINDOWS_ESSENTIAL = {
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "WINDIR",
    "PATHEXT",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "COMMONPROGRAMFILES",
    "COMMONPROGRAMFILES(X86)",
    "USERPROFILE",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "OS",
}

_UNIX_ESSENTIAL = {
    "USER",
    "LOGNAME",
    "SHELL",
    "DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
}

# Common variables (cross-platform) always allowed
_COMMON_ESSENTIAL = {
    "PATH",
    "HOME",
    "TEMP",
    "TMP",
    "TMPDIR",
    "PYTHONPATH",
    "PYTHONUNBUFFERED",
    "PYTHONHASHSEED",
    "VIRTUAL_ENV",
    "CONDA_DEFAULT_ENV",
    "CONDA_PREFIX",
    "UV_CACHE_DIR",
    "PIP_CACHE_DIR",
    "TERM",
    "COLORTERM",
    "COLUMNS",
    "LINES",
    "ENCODING",
    "HOSTNAME",
}

# Absolute block patterns — blocked even if in whitelist
_BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(r".*API[_-]?KEY", re.IGNORECASE),
    re.compile(r".*SECRET", re.IGNORECASE),
    re.compile(r".*TOKEN", re.IGNORECASE),
    re.compile(r".*PASSWORD", re.IGNORECASE),
    re.compile(r".*CREDENTIAL", re.IGNORECASE),
    re.compile(r".*PRIVATE[_-]?KEY", re.IGNORECASE),
    re.compile(r".*AUTH", re.IGNORECASE),
    re.compile(r"^AWS_", re.IGNORECASE),
    re.compile(r"^AZURE_", re.IGNORECASE),
    re.compile(r"^GCP_", re.IGNORECASE),
    re.compile(r"^GOOGLE_", re.IGNORECASE),
    re.compile(r"^OPENAI_", re.IGNORECASE),
    re.compile(r"^ANTHROPIC_", re.IGNORECASE),
    re.compile(r"^HF_", re.IGNORECASE),
    re.compile(r"^HUGGING", re.IGNORECASE),
    re.compile(r"^ZAI_", re.IGNORECASE),
    re.compile(r"^OPENROUTER_", re.IGNORECASE),
    re.compile(r"^MEGALLM_", re.IGNORECASE),
    re.compile(r"^GEMINI_", re.IGNORECASE),
    re.compile(r"^MISTRAL_", re.IGNORECASE),
    re.compile(r"^OPENCODE_", re.IGNORECASE),
    re.compile(r"^NVIDIA_", re.IGNORECASE),
]


def _is_blocked(var_name: str) -> bool:
    """Check if variable is in block list."""
    return any(pat.search(var_name) for pat in _BLOCKED_PATTERNS)


def build_safe_env(
    extra_allowed: Optional[Set[str]] = None,
    extra_vars: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Build safe environment dict for subprocess.

    Whitelist strategy:
    1. Start from essential variables set (platform-specific + common).
    2. Add ``extra_allowed`` if needed.
    3. Filter through blocked patterns → remove all sensitive variables.
    4. Merge ``extra_vars`` last (override).

    Args:
        extra_allowed: Additional variable names allowed from os.environ.
        extra_vars: Variables to inject directly (e.g., ``PYTHONUNBUFFERED=1``).

    Returns:
        Safe env dict, ready to pass to ``subprocess.Popen(env=...)``.
    """
    allowed: Set[str] = set(_COMMON_ESSENTIAL)

    if platform.system() == "Windows":
        allowed |= _WINDOWS_ESSENTIAL
    else:
        allowed |= _UNIX_ESSENTIAL

    if extra_allowed:
        allowed |= extra_allowed

    # Filter from os.environ: only keep variables in whitelist AND not blocked
    safe: Dict[str, str] = {}
    for key in allowed:
        val = os.environ.get(key)
        if val is not None and not _is_blocked(key):
            safe[key] = val

    # Merge extra_vars (override), but still check blocked
    if extra_vars:
        for key, val in extra_vars.items():
            if not _is_blocked(key):
                safe[key] = val

    return safe
