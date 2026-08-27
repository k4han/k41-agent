"""Shell command guard — blocks dangerous patterns for local execution."""

from __future__ import annotations

import re

# Patterns that are considered destructive and should be blocked on local backend.
# Each entry is (regex, reason) — kept deliberately narrow to avoid false positives
# on legitimate file operations inside workspace.
_BLOCKED_COMMAND_PATTERNS: list[tuple[re.Pattern, str]] = [
    # rm -rf targeting root: block `rm -rf /` or `rm -rf /*` but NOT `rm -rf /tmp/foo`
    (re.compile(r"\brm\s+[^|;]*-rf\s+/\s*(?:$|[;|&\n\"'])", re.IGNORECASE), "rm -rf targeting filesystem root"),
    (re.compile(r"\brm\s+[^|;]*-rf\s+/\*\s*(?:$|[;|&\n\"'])", re.IGNORECASE), "rm -rf targeting filesystem root (wildcard)"),
    (re.compile(r"\brm\s+[^|;]*--no-preserve-root", re.IGNORECASE), "rm with --no-preserve-root"),
    (re.compile(r"\bmkfs(\.|\s)", re.IGNORECASE), "filesystem formatting (mkfs)"),
    (re.compile(r"\bmkswap\b", re.IGNORECASE), "swap formatting (mkswap)"),
    (re.compile(r"\bfdisk\b", re.IGNORECASE), "disk partitioning (fdisk)"),
    (re.compile(r"\bdd\s+[^|;]*of\s*=\s*/dev/(sda|nvme|hda|vda)", re.IGNORECASE), "block-device overwrite via dd"),
    (re.compile(r":\(\)\s*\{\s*:.*\|\s*:.*&\s*\}\s*;", re.IGNORECASE), "fork bomb"),
    (re.compile(r"\bshutdown\b", re.IGNORECASE), "system shutdown"),
    (re.compile(r"\breboot\b", re.IGNORECASE), "system reboot"),
    (re.compile(r"\bhalt\b", re.IGNORECASE), "system halt"),
    (re.compile(r"\bpoweroff\b", re.IGNORECASE), "system poweroff"),
    (re.compile(r">\s*/dev/sd[a-z]", re.IGNORECASE), "direct write to block device"),
    # chmod 777 targeting root: block `chmod 777 /` but NOT `chmod 777 /tmp/file`
    (re.compile(r"\bchmod\s+[^|;]*777\s+/\s*(?:$|[;|&\n\"'])", re.IGNORECASE), "chmod 777 on root"),
]

# Allow-list override via env/config could be added later; for now deny takes precedence.


def check_command_blocked(command: str) -> tuple[bool, str]:
    """Check if command matches a blocked pattern.

    Returns (is_blocked, reason). ``reason`` is empty when not blocked.
    """
    if not command or not command.strip():
        return False, ""
    for pattern, reason in _BLOCKED_COMMAND_PATTERNS:
        if pattern.search(command):
            return True, reason
    return False, ""


def assert_command_allowed(command: str) -> None:
    """Raise ValueError if command is blocked."""
    blocked, reason = check_command_blocked(command)
    if blocked:
        raise ValueError(f"Blocked dangerous command ({reason}): command rejected for local execution")
