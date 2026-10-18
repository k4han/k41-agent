"""Encrypted local credentials with serialized cross-process updates."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
import json
import os
from pathlib import Path
import sqlite3
from typing import Any
import uuid

from cryptography.fernet import Fernet

from agent.shared.infrastructure.config_file import DEFAULT_CONFIG_PATH

type CredentialTransaction = tuple[sqlite3.Connection, Fernet]


class ChatGPTCredentialStore:
    """Keep registrations separate and lock rotating refresh-token updates."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or DEFAULT_CONFIG_PATH.parent / "data" / "chatgpt"

    @contextmanager
    def transaction(self) -> Iterator[CredentialTransaction]:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.directory / "credentials.db"
        connection = sqlite3.connect(path, timeout=60)
        try:
            path.chmod(0o600)
            connection.execute("CREATE TABLE IF NOT EXISTS credentials (name TEXT PRIMARY KEY, value BLOB NOT NULL)")
            connection.execute("BEGIN IMMEDIATE")
            key_path = self.directory / "credentials.key"
            if not key_path.exists():
                descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as key_file:
                    key_file.write(Fernet.generate_key())
            cipher = Fernet(key_path.read_bytes())
            yield connection, cipher
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def read(transaction: CredentialTransaction, name: str) -> dict[str, Any]:
        connection, cipher = transaction
        row = connection.execute("SELECT value FROM credentials WHERE name = ?", (name,)).fetchone()
        return json.loads(cipher.decrypt(row[0])) if row else {}

    @staticmethod
    def write(transaction: CredentialTransaction, name: str, value: dict[str, Any]) -> None:
        connection, cipher = transaction
        encrypted = cipher.encrypt(json.dumps(value).encode("utf-8"))
        connection.execute("INSERT OR REPLACE INTO credentials VALUES (?, ?)", (name, encrypted))

    def get(self, name: str) -> dict[str, Any]:
        with self.transaction() as transaction:
            return self.read(transaction, name)

    def host_id(self) -> str:
        with self.transaction() as transaction:
            host = self.read(transaction, "host:installation")
            if not host:
                host = {"id": uuid.uuid4().urn}
                self.write(transaction, "host:installation", host)
            return host["id"]
