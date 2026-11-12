from __future__ import annotations

import base64
import json
from contextlib import closing
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agent.bootstrap.logs import RotatingLogWriter
from agent.delivery.http.dashboard.routes import logs
from agent.shared.infrastructure import log_reader
from agent.shared.infrastructure.log_reader import LogSnapshot, LogSnapshotChanged


@pytest.fixture
def client(tmp_path, monkeypatch):
    from agent.delivery.http.dashboard.router import router
    from agent.modules.admin_auth import get_current_admin

    monkeypatch.setattr(logs, "LOG_DIR", tmp_path)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_admin] = lambda: "admin"
    return TestClient(app)


def test_sources_and_empty_logs(client, tmp_path):
    (tmp_path / "tray.log").touch()
    response = client.get("/dashboard-api/logs")
    assert response.headers["cache-control"] == "no-store"
    sources = response.json()["sources"]
    assert [item["id"] for item in sources] == ["server", "tray", "update"]
    assert [item["available"] for item in sources] == [False, True, False]
    for source in ("server", "tray", "update"):
        page = client.get(f"/dashboard-api/logs/{source}").json()
        assert page["text"] == ""
        assert not page["has_older"] and not page["has_newer"]


def test_tail_backward_and_forward_across_backups(client, tmp_path):
    for name, data in [("server.log.2", b"oldest\n"), ("server.log.1", b"middle\n"), ("server.log", b"newest\n")]:
        (tmp_path / name).write_bytes(data)
    page = client.get("/dashboard-api/logs/server", params={"limit": 9}).json()
    assert page["text"] == "e\nnewest\n"
    text = page["text"]
    while page["has_older"]:
        page = client.get("/dashboard-api/logs/server", params={"before": page["before"], "limit": 9}).json()
        text = page["text"] + text
    assert text == "oldest\nmiddle\nnewest\n"
    forward = client.get("/dashboard-api/logs/server", params={"after": page["after"], "limit": 9}).json()
    assert forward["text"] == text[len(page["text"]):len(page["text"]) + 9]


def test_utf8_and_tracebacks_survive_small_pages_and_file_boundaries(client, tmp_path):
    text = "First\nTraceback:\n  fail('🌍')\nError: café 日本語\n" * 11
    raw = text.encode()
    split = raw.index("🌍".encode()) + 2
    (tmp_path / "server.log.1").write_bytes(raw[:split])
    (tmp_path / "server.log").write_bytes(raw[split:])
    page = client.get("/dashboard-api/logs/server", params={"limit": 7}).json()
    result = page["text"]
    while page["has_older"]:
        page = client.get("/dashboard-api/logs/server", params={"before": page["before"], "limit": 7}).json()
        result = page["text"] + result
    assert result == text
    result = page["text"]
    while page["has_newer"]:
        page = client.get("/dashboard-api/logs/server", params={"after": page["after"], "limit": 7}).json()
        result += page["text"]
    assert result == text


def test_incomplete_utf8_waits_for_remaining_bytes(client, tmp_path):
    path = tmp_path / "server.log"
    path.write_bytes(b"start\n" + "🌍".encode()[:2])
    page = client.get("/dashboard-api/logs/server").json()
    assert page["text"] == "start\n"
    with path.open("ab") as stream:
        stream.write("🌍".encode()[2:] + b"\n")
    page = client.get("/dashboard-api/logs/server", params={"after": page["after"]}).json()
    assert page["text"] == "🌍\n"


def test_long_lines_are_bounded_and_append_is_not_duplicated(client, tmp_path):
    path = tmp_path / "server.log"
    path.write_bytes(b"x" * 100_000)
    response = client.get("/dashboard-api/logs/server")
    assert response.headers["cache-control"] == "no-store"
    page = response.json()
    assert len(page["text"]) == page["bytes"] == 65536
    assert page["has_older"]
    with path.open("ab") as stream:
        stream.write(b"new\n")
    page = client.get("/dashboard-api/logs/server", params={"after": page["after"]}).json()
    assert page["text"] == "new\n"
    assert client.get("/dashboard-api/logs/server", params={"after": page["after"]}).json()["text"] == ""


def test_cursors_follow_rotating_writer_and_restarts(client, tmp_path):
    path = tmp_path / "server.log"
    with closing(RotatingLogWriter(path, max_bytes=12)) as writer:
        writer.write(b"first\n")
        original = client.get("/dashboard-api/logs/server").json()
        writer.write(b"second\nthird\n")
    page = client.get("/dashboard-api/logs/server", params={"after": original["after"]}).json()
    assert page["text"] == "second\nthird\n"
    before = client.get("/dashboard-api/logs/server", params={"before": page["before"]}).json()
    assert before["text"] == "first\n"


def test_open_snapshot_does_not_block_rotation(tmp_path):
    path = tmp_path / "server.log"
    with closing(RotatingLogWriter(path, max_bytes=8)) as writer:
        writer.write(b"first\n")
        with LogSnapshot(path, 5) as snapshot:
            writer.write(b"second\n")
            assert writer.dropped == 0
            assert snapshot.page("server")["text"] == "first\n"
    assert path.read_bytes() == b"second\n"


def test_cursors_survive_each_rotation_stage(client, tmp_path, monkeypatch):
    path = tmp_path / "server.log"
    staged_cursors = []
    with closing(RotatingLogWriter(path, max_bytes=12)) as writer:
        writer.write(b"oldest\n")
        writer.write(b"first\n")
        original = client.get("/dashboard-api/logs/server").json()
        archive = writer._archive_rotation

        def check_stage():
            response = client.get("/dashboard-api/logs/server", params={"after": original["after"]})
            assert response.status_code == 200
            assert response.json()["text"] == ""
            tail = client.get("/dashboard-api/logs/server").json()
            assert tail["text"] == "oldest\nfirst\n"
            staged_cursors.append(tail["after"])

        def archive_with_reads(rotating):
            check_stage()
            archive(rotating)
            check_stage()

        monkeypatch.setattr(writer, "_archive_rotation", archive_with_reads)
        writer.write(b"second\n")
        assert writer.dropped == 0

    for cursor in [original["after"], *staged_cursors]:
        response = client.get("/dashboard-api/logs/server", params={"after": cursor})
        assert response.status_code == 200
        assert response.json()["text"] == "second\n"


def test_staging_file_precedes_active_log(client, tmp_path):
    for name, data in [
        ("server.log.1", b"oldest\n"),
        ("server.log.rotating", b"staged\n"),
        ("server.log", b"active\n"),
    ]:
        (tmp_path / name).write_bytes(data)
    assert client.get("/dashboard-api/logs/server").json()["text"] == "oldest\nstaged\nactive\n"


@pytest.mark.parametrize("change", ["delete", "truncate", "overwrite"])
def test_expired_history_is_explicit(client, tmp_path, change):
    path = tmp_path / "server.log"
    path.write_bytes(b"old output\n")
    page = client.get("/dashboard-api/logs/server").json()
    if change == "delete":
        path.unlink()
    elif change == "truncate":
        path.write_bytes(b"x")
    else:
        path.write_bytes(b"new output\n")
    response = client.get("/dashboard-api/logs/server", params={"after": page["after"]})
    assert response.status_code == 410
    assert "latest" in response.json()["detail"]


@pytest.mark.parametrize("params", [
    {"before": "bad"}, {"after": ""}, {"before": "a", "after": "b"},
    {"after": base64.urlsafe_b64encode(json.dumps(["tray", 1, 1, 0, "x" * 32]).encode()).decode()},
    {"after": base64.urlsafe_b64encode(json.dumps(["server", [], 1, 0, "x" * 32]).encode()).decode()},
])
def test_invalid_cursors(client, params):
    assert client.get("/dashboard-api/logs/server", params=params).status_code == 400


@pytest.mark.parametrize("limit", [0, 3, 65537])
def test_limit_validation(client, limit):
    assert client.get("/dashboard-api/logs/server", params={"limit": limit}).status_code == 422


def test_source_validation_and_authentication(client):
    from agent.modules.admin_auth import get_current_admin

    assert client.get("/dashboard-api/logs/private").status_code == 404

    def deny():
        raise HTTPException(status_code=401, detail="Not authenticated")

    client.app.dependency_overrides[get_current_admin] = deny
    assert client.get("/dashboard-api/logs").status_code == 401
    assert client.get("/dashboard-api/logs/server").status_code == 401
    assert client.get("/settings/logs").status_code == 401


def test_file_and_directory_symlinks_are_rejected(client, tmp_path, monkeypatch):
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "server.log").write_text("secret", encoding="utf-8")
    link = tmp_path / "server.log"
    try:
        link.symlink_to(outside / "server.log")
    except OSError:
        pytest.skip("Creating symbolic links is unavailable on this host")
    assert client.get("/dashboard-api/logs/server").status_code == 503
    assert client.get("/dashboard-api/logs").json()["sources"][0]["error"]
    directory = tmp_path / "linked"
    directory.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(logs, "LOG_DIR", directory)
    assert client.get("/dashboard-api/logs/server").status_code == 503


def test_rotation_during_open_retries_once(client, tmp_path, monkeypatch):
    path = tmp_path / "server.log"
    path.write_bytes(b"output\n")
    original = logs.LogSnapshot
    attempts = []

    def snapshot(*args):
        attempts.append(True)
        if len(attempts) == 1:
            raise LogSnapshotChanged
        return original(*args)

    monkeypatch.setattr(logs, "LogSnapshot", snapshot)
    assert client.get("/dashboard-api/logs/server").json()["text"] == "output\n"
    assert len(attempts) == 2


def test_snapshot_rejects_a_file_replaced_during_open(tmp_path, monkeypatch):
    path = tmp_path / "server.log"
    path.write_bytes(b"first")
    original = log_reader._open_file

    def replace_then_open(name):
        Path(name).replace(tmp_path / "server.log.1")
        Path(name).write_bytes(b"second")
        return original(name)

    monkeypatch.setattr(log_reader, "_open_file", replace_then_open)
    with pytest.raises(LogSnapshotChanged):
        with LogSnapshot(path, 5):
            pass


def test_snapshot_rejects_a_file_created_during_open(tmp_path, monkeypatch):
    path = tmp_path / "server.log"
    Path(f"{path}.1").write_bytes(b"archived\n")
    original = log_reader._open_file

    def create_then_open(name):
        path.write_bytes(b"new output\n")
        return original(name)

    monkeypatch.setattr(log_reader, "_open_file", create_then_open)
    with pytest.raises(LogSnapshotChanged):
        with LogSnapshot(path, 5):
            pass
