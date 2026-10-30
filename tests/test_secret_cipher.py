"""Encryption key creation, recovery, and read-only credential access."""

import os
import stat

import pytest
from cryptography.fernet import Fernet

from agent.shared.infrastructure.secret_cipher import SecretCipher, load_or_create_fernet_key


@pytest.mark.parametrize("contents", [None, b"", b" \r\n\t"])
def test_missing_or_empty_key_is_created_and_reused(tmp_path, contents):
    path = tmp_path / "data" / "runtime_config.key"
    if contents is not None:
        path.parent.mkdir()
        path.write_bytes(contents)
    key = load_or_create_fernet_key(path)
    encrypted = Fernet(key).encrypt(b"credential")
    assert path.read_bytes() == key
    assert load_or_create_fernet_key(path) == key
    assert Fernet(key).decrypt(encrypted) == b"credential"
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("contents", [None, b"", b"invalid-key"])
def test_reading_credentials_never_replaces_missing_or_invalid_key(tmp_path, contents):
    path = tmp_path / "runtime_config.key"
    if contents is not None:
        path.write_bytes(contents)
    encrypted = Fernet(Fernet.generate_key()).encrypt(b"credential").decode("ascii")
    with pytest.raises((FileNotFoundError, ValueError)):
        SecretCipher(path).decrypt(encrypted)
    if contents is None:
        assert not path.exists()
    else:
        assert path.read_bytes() == contents
