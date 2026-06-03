"""Shared constants for workspace backends."""

from __future__ import annotations

# Tree and file browsing limits
MAX_TREE_ENTRIES = 500
MAX_FILE_BYTES = 300_000
MAX_LIST_FILES_ENTRIES = 5000
MAX_DIRECTORY_BROWSE_ENTRIES = 500

# Git-related constants
GIT_TIMEOUT_SECONDS = 10
MAX_DIFF_CHARS = 200_000
MAX_UNTRACKED_FILE_CHARS = 120_000

# Directories to ignore when listing files
IGNORED_DIR_NAMES: frozenset[str] = frozenset({
    ".cache",
    ".git",
    ".mypy_cache",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
})

__all__ = [
    "GIT_TIMEOUT_SECONDS",
    "IGNORED_DIR_NAMES",
    "MAX_DIFF_CHARS",
    "MAX_DIRECTORY_BROWSE_ENTRIES",
    "MAX_FILE_BYTES",
    "MAX_LIST_FILES_ENTRIES",
    "MAX_TREE_ENTRIES",
    "MAX_UNTRACKED_FILE_CHARS",
]
