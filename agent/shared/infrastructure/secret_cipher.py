"""Shared authenticated encryption for application-owned credentials."""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet

from agent.shared.infrastructure.config_file import DEFAULT_CONFIG_PATH


def default_key_path() -> Path:
    return DEFAULT_CONFIG_PATH.parent / "data" / "runtime_config.key"


def load_or_create_fernet_key(path: Path) -> bytes:
    try:
        existing = path.read_bytes().strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        descriptor = os.open(path, os.O_RDWR)
    with os.fdopen(descriptor, "r+b") as handle:
        existing = handle.read().strip()
        if existing:
            return existing
        if os.name != "nt":
            os.fchmod(handle.fileno(), 0o600)
        handle.seek(0)
        handle.write(key)
        handle.truncate()
    return key


class SecretCipher:
    def __init__(self, key_path: Path | None = None):
        self.key_path = key_path or default_key_path()

    def encrypt(self, value: str) -> str:
        return self.fernet(create=True).encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        # Never replace a missing key while trying to read existing credentials.
        return self.fernet().decrypt(value.encode("ascii")).decode("utf-8")

    def fernet(self, *, create: bool = False) -> Fernet:
        key = load_or_create_fernet_key(self.key_path) if create else self.key_path.read_bytes().strip()
        return Fernet(key)
