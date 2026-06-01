from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import create_engine, make_url, select
from sqlalchemy.orm import Session, sessionmaker

from agent.shared.config.constants import (
    is_database_runtime_key,
    is_sensitive_runtime_key,
)
from agent.shared.config.models import SettingsSource, SettingsValue, build_settings_values
from agent.shared.infrastructure.config_file import DEFAULT_CONFIG_PATH
from agent.shared.infrastructure.db.runtime_settings import RuntimeSetting

logger = logging.getLogger(__name__)

_MISSING = object()


def _default_key_path() -> Path:
    return DEFAULT_CONFIG_PATH.parent / "data" / "runtime_config.key"


def _load_or_create_fernet_key(path: Path) -> bytes:
    try:
        existing = path.read_bytes().strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass

    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    try:
        path.chmod(0o600)
    except OSError:
        logger.debug("Could not restrict permissions for runtime config key file: %s", path)
    return key


class DatabaseConfigSource:
    """Read and write database-owned runtime settings."""

    def __init__(
        self,
        database_url: str,
        *,
        key_path: Path | None = None,
    ) -> None:
        from agent.shared.infrastructure.db.engine import _normalize_url_to_sync

        self.database_url = database_url
        sync_url = _normalize_url_to_sync(database_url)
        connect_args = {}
        if "sqlite" in make_url(sync_url).drivername:
            connect_args["check_same_thread"] = False
        self._engine = create_engine(sync_url, echo=False, future=True, connect_args=connect_args)
        self._session_maker = sessionmaker(self._engine, class_=Session, expire_on_commit=False)
        self._key_path = key_path or _default_key_path()
        self._fernet: Fernet | None = None
        self._cache: dict[str, Any] | None = None
        self._priority = 200

    def can_update_key(self, key: str) -> bool:
        return is_database_runtime_key(key)

    def get(self, key: str) -> Any | None:
        if not self.can_update_key(key):
            return None
        data = self._load()
        return data.get(key)

    def get_all(self) -> dict[str, Any]:
        return self._load()

    def get_settings_value(self, key: str) -> SettingsValue | None:
        if not self.can_update_key(key):
            return None
        data = self._load()
        val = data.get(key, _MISSING)
        if val is _MISSING:
            return None
        return SettingsValue(key=key, value=val, source=SettingsSource.DATABASE)

    def get_all_settings_values(
        self,
        keys: set[str] | None = None,
    ) -> dict[str, SettingsValue]:
        data = self._load()
        return build_settings_values(data, SettingsSource.DATABASE, keys)

    def update_setting(self, key: str, value: Any) -> None:
        self.update_settings({key: value})

    def update_settings(self, updates: dict[str, Any]) -> None:
        writable_updates = {
            key: value
            for key, value in updates.items()
            if self.can_update_key(key)
        }
        if not writable_updates:
            return

        with self._session_maker.begin() as session:
            existing_rows = session.execute(
                select(RuntimeSetting).where(RuntimeSetting.key.in_(writable_updates))
            )
            by_key = {row.key: row for row in existing_rows.scalars().all()}

            for key, value in writable_updates.items():
                encrypted = is_sensitive_runtime_key(key)
                value_json = self._encode_value(value, encrypted=encrypted)
                row = by_key.get(key)
                if row is None:
                    session.add(
                        RuntimeSetting(
                            key=key,
                            value_json=value_json,
                            encrypted=encrypted,
                        )
                    )
                else:
                    row.value_json = value_json
                    row.encrypted = encrypted
        self.reload()

    def seed_missing_settings(self, updates: dict[str, Any]) -> set[str]:
        writable_updates = {
            key: value
            for key, value in updates.items()
            if self.can_update_key(key)
        }
        if not writable_updates:
            return set()

        with self._session_maker() as session:
            existing_rows = session.execute(
                select(RuntimeSetting.key).where(RuntimeSetting.key.in_(writable_updates))
            )
            existing_keys = set(existing_rows.scalars().all())

        missing_updates = {
            key: value
            for key, value in writable_updates.items()
            if key not in existing_keys
        }
        if not missing_updates:
            return set()

        self.update_settings(missing_updates)
        return set(missing_updates)

    def delete_setting_tree(self, key: str) -> bool:
        prefix = f"{key}."
        deleted = False
        with self._session_maker.begin() as session:
            rows = session.execute(
                select(RuntimeSetting).where(RuntimeSetting.key.like(f"{key}%"))
            )
            for row in rows.scalars().all():
                if row.key == key or row.key.startswith(prefix):
                    session.delete(row)
                    deleted = True
        self.reload()
        return deleted

    def reload(self) -> None:
        self._cache = None

    def close(self) -> None:
        self._engine.dispose()

    @property
    def priority(self) -> int:
        return self._priority

    def _load(self) -> dict[str, Any]:
        if self._cache is not None:
            return self._cache

        loaded: dict[str, Any] = {}
        with self._session_maker() as session:
            rows = session.execute(select(RuntimeSetting)).scalars().all()

        for row in rows:
            if not self.can_update_key(row.key):
                continue
            try:
                loaded[row.key] = self._decode_value(
                    row.value_json,
                    encrypted=bool(row.encrypted),
                )
            except (InvalidToken, json.JSONDecodeError, UnicodeDecodeError):
                logger.warning("Skipping unreadable runtime setting: %s", row.key)

        self._cache = loaded
        return loaded

    def _encode_value(self, value: Any, *, encrypted: bool) -> str:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if not encrypted:
            return payload
        return self._get_fernet().encrypt(payload.encode("utf-8")).decode("ascii")

    def _decode_value(self, stored: str, *, encrypted: bool) -> Any:
        payload = stored
        if encrypted:
            payload = self._get_fernet().decrypt(stored.encode("ascii")).decode("utf-8")
        return json.loads(payload)

    def _get_fernet(self) -> Fernet:
        if self._fernet is None:
            self._fernet = Fernet(_load_or_create_fernet_key(self._key_path))
        return self._fernet


__all__ = ["DatabaseConfigSource"]
