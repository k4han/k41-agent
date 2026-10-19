"""Move one protected ChatGPT registration between independent hosts."""

from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path
import re
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from agent.modules.providers.chatgpt.auth import ChatGPTAuthError, DYNAMIC_CLIENT_ID, ISSUER
from agent.modules.providers.chatgpt.storage import ChatGPTCredentialStore

_HEADER = b"k41-chatgpt-session-v1\n"
_MAX_FILE_BYTES = 128 * 1024
_SESSION_FIELDS = {"access_token", "refresh_token", "id_token", "scopes", "expires_at"}
_PROFILE_FIELDS = _SESSION_FIELDS | {"client_id", "subject", "issuer", "email"}


def _validate_provider_name(provider_name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", provider_name):
        raise ChatGPTAuthError("Provider names must contain only letters, numbers, underscores, or hyphens.")


def _validate_profile(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ChatGPTAuthError("Invalid ChatGPT session profile.")
    profile = {key: value[key] for key in _PROFILE_FIELDS if key in value}
    for key in ("client_id", "access_token", "refresh_token", "subject", "issuer"):
        if not isinstance(profile.get(key), str) or not profile[key].strip():
            raise ChatGPTAuthError("A renewable ChatGPT session is required. Sign in locally before exporting.")
    if profile["issuer"] != ISSUER or profile["client_id"] == DYNAMIC_CLIENT_ID:
        raise ChatGPTAuthError("Invalid ChatGPT registration in session file.")
    if not isinstance(profile.get("scopes"), list) or any(not isinstance(scope, str) for scope in profile["scopes"]):
        raise ChatGPTAuthError("Invalid ChatGPT session scopes.")
    expiry = profile.get("expires_at")
    if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= 0:
        raise ChatGPTAuthError("Invalid ChatGPT session expiry.")
    if any(key in profile and not isinstance(profile[key], str) for key in ("id_token", "email")):
        raise ChatGPTAuthError("Invalid ChatGPT session metadata.")
    return profile


def _cipher(passphrase: str, salt: bytes) -> Fernet:
    if len(passphrase) < 12:
        raise ChatGPTAuthError("Use a session-file passphrase of at least 12 characters.")
    key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


class ChatGPTSessionTransfer:
    """Transfer selected credentials without copying installation IDs or keys."""

    def __init__(self, store: ChatGPTCredentialStore | None = None) -> None:
        self.store = store or ChatGPTCredentialStore()

    def export_session(self, provider_name: str, path: Path, passphrase: str) -> None:
        """Save an encrypted file, then detach local tokens without revocation."""
        _validate_provider_name(provider_name)
        created = False
        try:
            with self.store.transaction() as transaction:
                saved = self.store.read(transaction, provider_name)
                profile = _validate_profile(saved)
                salt = os.urandom(16)
                payload = _HEADER + salt + _cipher(passphrase, salt).encrypt(json.dumps(profile).encode("utf-8"))
                if len(payload) > _MAX_FILE_BYTES:
                    raise ChatGPTAuthError("ChatGPT session is too large to export.")
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                created = True
                with os.fdopen(descriptor, "wb") as output:
                    output.write(payload)
                    output.flush()
                    os.fsync(output.fileno())
                self.store.write(transaction, provider_name, {
                    key: value for key, value in saved.items() if key not in _SESSION_FIELDS
                })
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            raise

    def import_session(self, provider_name: str, path: Path, passphrase: str) -> None:
        """Re-encrypt credentials locally while preserving the destination host ID."""
        _validate_provider_name(provider_name)
        with path.open("rb") as source:
            payload = source.read(_MAX_FILE_BYTES + 1)
        if len(payload) > _MAX_FILE_BYTES or not payload.startswith(_HEADER) or len(payload) <= len(_HEADER) + 16:
            raise ChatGPTAuthError("Invalid or unsupported ChatGPT session file.")
        salt = payload[len(_HEADER):len(_HEADER) + 16]
        try:
            profile = _validate_profile(json.loads(_cipher(passphrase, salt).decrypt(payload[len(_HEADER) + 16:])))
        except InvalidToken:
            raise ChatGPTAuthError("Could not unlock the ChatGPT session file. Check its passphrase and integrity.") from None
        except (ValueError, UnicodeError, OverflowError):
            raise ChatGPTAuthError("Invalid ChatGPT session file.") from None
        self.store.host_id()
        with self.store.transaction() as transaction:
            saved = self.store.read(transaction, provider_name)
            if saved.get("access_token") or saved.get("refresh_token"):
                raise ChatGPTAuthError("This connection already has a session. Import into a new ChatGPT provider.")
            if any(saved.get(key) and saved[key] != profile[key] for key in ("client_id", "subject", "issuer")):
                raise ChatGPTAuthError("The saved account or workspace differs. Import into a new ChatGPT provider.")
            self.store.write(transaction, provider_name, profile)
