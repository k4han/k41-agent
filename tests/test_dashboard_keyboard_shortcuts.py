from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.bootstrap.app import ContainerActivationMiddleware
from agent.delivery.http.dashboard.router import router
from agent.modules.admin_auth import auth
from agent.modules.admin_auth.models import AdminCredential
from agent.shared.infrastructure.csrf_protection import CSRFProtectionMiddleware
from agent.shared.infrastructure.db.engine import close_async_engine, initialize_async_engine
from agent.shared.infrastructure.db.session import get_async_session_maker

PATH = "/dashboard-api/keyboard-shortcuts"


@pytest.fixture
def shortcut_client(isolated_container, monkeypatch):
    monkeypatch.setattr(auth, "get_secret_key", lambda: "keyboard-shortcut-test-secret-key")

    @asynccontextmanager
    async def lifespan(app):
        await initialize_async_engine(container=isolated_container)
        try:
            async with get_async_session_maker()() as session:
                admins = [
                    AdminCredential(username=username, password_hash="unused")
                    for username in ("primary", "secondary")
                ]
                session.add_all(admins)
                await session.commit()
                app.state.tokens = [
                    auth.create_access_token({"sub": str(admin.id), "ver": admin.session_version})
                    for admin in admins
                ]
            yield
        finally:
            await close_async_engine(container=isolated_container)

    app = FastAPI(lifespan=lifespan)
    app.state.container = isolated_container
    app.add_middleware(ContainerActivationMiddleware, container=isolated_container)
    app.add_middleware(CSRFProtectionMiddleware)
    app.include_router(router)
    with TestClient(app) as client:
        client.cookies.set("admin_token", app.state.tokens[0])
        assert client.get(PATH).status_code == 200
        client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
        yield client, app.state.tokens


def test_shortcuts_require_authentication_and_csrf(shortcut_client):
    client, _ = shortcut_client
    client.headers.pop("X-CSRF-Token")
    body = {"revision": 0, "overrides": {}}
    assert client.put(PATH, json=body).status_code == 403
    client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
    client.cookies.delete("admin_token")
    assert client.get(PATH).status_code == 401
    assert client.put(PATH, json=body).status_code == 401


def test_shortcuts_save_and_reset_per_account(shortcut_client):
    client, tokens = shortcut_client
    page = client.get("/settings/keyboard-shortcuts", headers={"Accept": "text/html"})
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    defaults = client.get(PATH)
    assert defaults.headers["cache-control"] == "no-store"
    initial = defaults.json()
    assert initial["revision"] == 0
    assert len(initial["defaults"]) == 9
    assert initial["defaults"]["command-palette"] == ["Mod+KeyK", "Mod+Shift+Space"]
    assert initial["defaults"]["focus-message-input"] == ["Slash", "Mod+Shift+Period"]
    assert initial["overrides"] == {}

    response = client.put(PATH, json={"revision": 0, "overrides": {
        "toggle-sidebar": ["Shift+Mod+KeyZ", "Alt+Shift+Digit2"], "copy-link": [],
    }})
    assert response.status_code == 200
    saved = {**initial, "revision": 1, "overrides": {
        "toggle-sidebar": ["Mod+Shift+KeyZ", "Alt+Shift+Digit2"], "copy-link": [],
    }}
    assert response.json() == saved

    client.cookies.set("admin_token", tokens[1])
    assert client.get(PATH).json() == initial
    assert client.put(PATH, json={
        "revision": 0, "overrides": {"new-session": ["Alt+KeyP"], "focus-message-input": ["Slash"]},
    }).status_code == 200
    client.cookies.set("admin_token", tokens[0])
    assert client.get(PATH).json() == saved
    reset_one = client.put(PATH, json={
        "revision": saved["revision"], "overrides": {"copy-link": []},
    }).json()
    assert reset_one["overrides"] == {"copy-link": []}
    reset_all = client.put(PATH, json={"revision": reset_one["revision"], "overrides": {}})
    assert reset_all.json() == {**initial, "revision": 3}
    client.cookies.set("admin_token", tokens[1])
    assert client.get(PATH).json()["overrides"] == {"new-session": ["Alt+KeyP"], "focus-message-input": ["Slash"]}


@pytest.mark.parametrize("overrides", [
    {"unknown": ["Mod+KeyY"]},
    {"copy-link": ["KeyL"]},
    {"copy-link": ["Slash"]},
    {"copy-link": ["Mod+Tab"]},
    {"copy-link": ["Mod+Ctrl+KeyL"]},
    {"copy-link": ["Mod+KeyK"]},
    {"copy-link": ["Ctrl+KeyK"]},
    {"copy-link": ["Meta+KeyK"]},
    {"copy-link": ["Shift+Alt+KeyP", "Alt+Shift+KeyP"]},
])
def test_rejects_invalid_or_conflicting_shortcuts(shortcut_client, overrides):
    client, _ = shortcut_client
    response = client.put(PATH, json={"revision": 0, "overrides": overrides})
    assert response.status_code == 422
    assert client.get(PATH).json()["revision"] == 0


def test_invalid_revisions_are_rejected(shortcut_client):
    client, _ = shortcut_client
    for revision in (-1, "0", True, 2**80):
        assert client.put(PATH, json={"revision": revision, "overrides": {}}).status_code == 422


def test_can_reassign_an_unbound_default(shortcut_client):
    client, _ = shortcut_client
    response = client.put(PATH, json={"revision": 0, "overrides": {
        "command-palette": [], "copy-link": ["Mod+KeyK"],
    }})
    assert response.status_code == 200
    assert response.json()["overrides"] == {"command-palette": [], "copy-link": ["Mod+KeyK"]}
    assert client.put(PATH, json={
        "revision": 1, "overrides": {"copy-link": ["Mod+KeyK"]},
    }).status_code == 422


@pytest.mark.parametrize("revision", [0, 1])
def test_concurrent_saves_only_update_the_expected_revision(shortcut_client, revision):
    client, _ = shortcut_client
    if revision:
        assert client.put(PATH, json={"revision": 0, "overrides": {}}).status_code == 200

    barrier = Barrier(2, timeout=10)

    def save(binding):
        barrier.wait()
        return client.put(PATH, json={"revision": revision, "overrides": {"new-session": [binding]}})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save, ["Alt+KeyP", "Alt+KeyQ"]))

    assert {response.status_code for response in responses} == {200, 409}
    saved = next(response.json() for response in responses if response.status_code == 200)
    assert saved["revision"] == revision + 1
    assert client.get(PATH).json() == saved
