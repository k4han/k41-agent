"""Verify protected session handoff without real accounts or network requests."""

import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from typer.testing import CliRunner

from agent.bootstrap.cli import app
from agent.modules.providers.chatgpt.auth import ChatGPTAuthError, ChatGPTAuthManager, ISSUER, PLAN_SCOPE
from agent.modules.providers.chatgpt.storage import ChatGPTCredentialStore
from agent.modules.providers.chatgpt.transfer import ChatGPTSessionTransfer


PASSPHRASE = "a long transfer passphrase"


@pytest.fixture
def handoff(tmp_path):
    source = ChatGPTCredentialStore(tmp_path / "laptop")
    target = ChatGPTCredentialStore(tmp_path / "server")
    profile = {
        "client_id": "oaiapp_selected", "subject": "account-1", "issuer": ISSUER,
        "email": "user@example.com", "access_token": "private-access",
        "refresh_token": "private-refresh", "id_token": "retained-identity",
        "scopes": ["openid", PLAN_SCOPE], "expires_at": time.time() + 3600,
    }
    with source.transaction() as transaction:
        source.write(transaction, "personal", profile)
        source.write(transaction, "other", {**profile, "client_id": "oaiapp_other"})
    return source, target, profile, tmp_path / "session.k41"


def test_transfer_moves_only_selected_session_and_preserves_destination_identity(handoff):
    source, target, profile, path = handoff
    laptop_host, server_host = source.host_id(), target.host_id()
    target_key = (target.directory / "credentials.key").read_bytes()
    with target.transaction() as transaction:
        target.write(transaction, "unrelated", {"access_token": "unrelated-access"})

    ChatGPTSessionTransfer(source).export_session("personal", path, PASSPHRASE)
    exported = path.read_bytes()
    for value in (profile["access_token"], profile["refresh_token"], profile["client_id"], profile["email"], laptop_host):
        assert value.encode() not in exported
    assert source.get("personal") == {key: profile[key] for key in ("client_id", "subject", "issuer", "email")}
    assert source.get("other")["refresh_token"] == "private-refresh"

    ChatGPTSessionTransfer(target).import_session("remote", path, PASSPHRASE)
    assert target.get("remote") == profile
    assert target.get("unrelated") == {"access_token": "unrelated-access"}
    assert target.host_id() == server_host != laptop_host
    assert (target.directory / "credentials.key").read_bytes() == target_key
    assert b"private-access" not in (target.directory / "credentials.db").read_bytes()


def test_import_creates_host_and_server_owns_refresh_and_later_authorization(handoff, monkeypatch):
    source, target, profile, path = handoff
    with source.transaction() as transaction:
        source.write(transaction, "personal", {**profile, "expires_at": time.time() - 60})
    ChatGPTSessionTransfer(source).export_session("personal", path, PASSPHRASE)
    ChatGPTSessionTransfer(target).import_session("remote", path, PASSPHRASE)
    manager = ChatGPTAuthManager(target)
    requests = []

    def refresh(data):
        requests.append(data)
        return {"access_token": "renewed-access", "refresh_token": "rotated-refresh", "expires_in": 3600}

    monkeypatch.setattr(manager, "_token_request", refresh)
    try:
        assert manager.access_token("remote") == "renewed-access"
        assert manager.access_token("remote") == "renewed-access"
        assert requests == [{"grant_type": "refresh_token", "client_id": profile["client_id"],
                             "refresh_token": profile["refresh_token"]}]
        assert target.get("remote")["refresh_token"] == "rotated-refresh"
        assert target.get("remote")["id_token"] == profile["id_token"]
        params = parse_qs(urlsplit(manager.start_sign_in("remote")["authorization_url"]).query)
        assert params["ext_agent_host_id"] == [target.host_id()]
        assert params["client_id"] == [profile["client_id"]]
        assert target.host_id() != source.host_id()
        assert not ChatGPTAuthManager(source).session("personal")["connected"]
    finally:
        manager.close()


@pytest.mark.parametrize("damage", ["wrong_passphrase", "tampered", "truncated", "oversized", "plaintext"])
def test_invalid_transfer_does_not_change_destination(handoff, damage):
    source, target, profile, path = handoff
    host_id = target.host_id()
    ChatGPTSessionTransfer(source).export_session("personal", path, PASSPHRASE)
    passphrase = PASSPHRASE
    payload = path.read_bytes()
    if damage == "wrong_passphrase":
        passphrase = "another long passphrase"
    elif damage == "tampered":
        path.write_bytes(payload[:-5] + b"abcde")
    elif damage == "truncated":
        path.write_bytes(payload[:25])
    elif damage == "oversized":
        path.write_bytes(payload + b"x" * (128 * 1024))
    else:
        path.write_text(json.dumps(profile), encoding="utf-8")
    with pytest.raises(ChatGPTAuthError):
        ChatGPTSessionTransfer(target).import_session("remote", path, passphrase)
    assert target.get("remote") == {}
    assert target.host_id() == host_id


def test_export_never_overwrites_or_detaches_on_failure(handoff, monkeypatch):
    source, _target, profile, path = handoff
    path.write_bytes(b"existing-file")
    with pytest.raises(FileExistsError):
        ChatGPTSessionTransfer(source).export_session("personal", path, PASSPHRASE)
    assert path.read_bytes() == b"existing-file"
    assert source.get("personal") == profile
    path.unlink()

    def fail_write(*_args):
        raise OSError("Could not save local credentials")

    monkeypatch.setattr(source, "write", fail_write)
    with pytest.raises(OSError):
        ChatGPTSessionTransfer(source).export_session("personal", path, PASSPHRASE)
    assert not path.exists()
    assert source.get("personal") == profile


@pytest.mark.parametrize("saved", [{"access_token": "existing"}, {"refresh_token": "existing"},
                                  {"client_id": "oaiapp_different"}, {"subject": "another-account"}])
def test_import_preserves_connected_or_different_registration(handoff, saved):
    source, target, _profile, path = handoff
    ChatGPTSessionTransfer(source).export_session("personal", path, PASSPHRASE)
    with target.transaction() as transaction:
        target.write(transaction, "remote", saved)
    with pytest.raises(ChatGPTAuthError):
        ChatGPTSessionTransfer(target).import_session("remote", path, PASSPHRASE)
    assert target.get("remote") == saved


def test_transfer_rejects_internal_names_and_weak_passphrases(handoff):
    source, _target, profile, path = handoff
    host_id = source.host_id()
    with pytest.raises(ChatGPTAuthError, match="Provider names"):
        ChatGPTSessionTransfer(source).export_session("host:installation", path, PASSPHRASE)
    with pytest.raises(ChatGPTAuthError, match="12 characters"):
        ChatGPTSessionTransfer(source).export_session("personal", path, "short")
    assert not path.exists()
    assert source.get("personal") == profile
    assert source.host_id() == host_id


def test_cli_handoff_uses_hidden_passphrase_and_redacts_credentials(handoff):
    source, target, profile, path = handoff
    runner = CliRunner()
    exported = runner.invoke(app, ["chatgpt", "export-session", "--provider", "personal", "--output", str(path),
                                   "--directory", str(source.directory)], input=f"{PASSPHRASE}\n{PASSPHRASE}\n")
    assert exported.exit_code == 0, exported.output
    assert "without revocation" in exported.output
    imported = runner.invoke(app, ["chatgpt", "import-session", "--provider", "remote", "--input", str(path),
                                   "--directory", str(target.directory)], input=f"{PASSPHRASE}\n")
    assert imported.exit_code == 0, imported.output
    assert target.get("remote") == profile
    for secret in (PASSPHRASE, profile["access_token"], profile["refresh_token"], profile["id_token"]):
        assert secret not in exported.output + imported.output

    failed = runner.invoke(app, ["chatgpt", "import-session", "--provider", "another", "--input", str(path),
                                 "--directory", str(target.directory)], input="wrong long passphrase\n")
    assert failed.exit_code == 1
    assert "Could not unlock" in failed.output
    assert target.get("another") == {}
