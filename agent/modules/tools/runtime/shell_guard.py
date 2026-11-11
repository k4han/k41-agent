"""Shell command guard — blocks dangerous patterns for local execution."""

from __future__ import annotations

import re

# Patterns that are considered destructive and should be blocked on local backend.
# Each entry is (regex, reason) — kept deliberately narrow to avoid false positives
# on legitimate file operations inside workspace.
_BLOCKED_COMMAND_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:Format-Volume|Clear-Disk|Initialize-Disk|Stop-Computer|Restart-Computer)\b", re.IGNORECASE), "destructive Windows system operation"),
    (re.compile(r"\b(?:Remove-Item|rmdir|rd)\b[^\r\n;|]*\s[\"']?[A-Za-z]:[\\/](?:\*|[\"']|\s|$)", re.IGNORECASE), "removal targeting Windows filesystem root"),
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


# Global Git options precede the subcommand and may contain quoted arguments.
# Consume words and options atomically to bound backtracking on long inputs.
_SHELL_WORD = r'''(?>(?:[^\s'";&|]+|'[^'\r\n]*'|"[^"\r\n]*")+)'''
_GIT_COMMAND_PREFIX = (
    r"\bgit(?:\.exe)?"
    rf"(?>(?:\s+(?:(?:-C|-c|--git-dir|--work-tree|--namespace|--config-env)\s+{_SHELL_WORD}|-{_SHELL_WORD}))*)\s+"
)

_DANGEROUS_COMMAND_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\brm\b[^\r\n;|]*\s(?:-[a-z]*r|--recursive)",
    r"\b(?:Remove-Item|ri)\b[^\r\n;|]*\s-(?:Recurse|r)\b",
    _GIT_COMMAND_PREFIX + r"push\b[^\r\n;|]*(?:--force(?:-with-lease)?\b|\s-f\b|\s\+)",
    _GIT_COMMAND_PREFIX + r"reset\b[^\r\n;|]*--hard\b",
    _GIT_COMMAND_PREFIX + r"clean\b[^\r\n;|]*-[a-z]*f",
    r"\b(?:sudo|runas)\b",
    r"\b(?:curl|wget)\b[^\r\n;]*\|\s*(?:sh|bash|zsh)\b",
    r"\b(?:iwr|Invoke-WebRequest|irm|Invoke-RestMethod)\b[^\r\n;]*\|\s*(?:iex|Invoke-Expression)\b",
    r"\b(?:chmod|chown)\b[^\r\n;|]*\s-(?:[a-z]*R|recursive)\b",
    r"\btaskkill\b|\bkill\s+(?:-9|-KILL)\b",
    r"\b(?:npm|pnpm|yarn)\s+publish\b",
    r"\bdocker\s+system\s+prune\b",
    r"\bDROP\s+(?:TABLE|DATABASE)\b",
))


def classify_command(command: str) -> str:
    """Classify shell input before consulting modes, rules or grants."""
    if check_command_blocked(command)[0]:
        return "blocked"
    if any(pattern.search(command) for pattern in _DANGEROUS_COMMAND_PATTERNS):
        return "dangerous"
    return "normal"
