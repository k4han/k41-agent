import pytest
import pytest_asyncio

import agent.shared.infrastructure.db.engine as db_engine
from agent.modules.admin_auth.service import AdminAuthService, DEFAULT_ADMIN_PASSWORD
from agent.shared.infrastructure.db.base import Base
from agent.shared.infrastructure.db.engine import close_async_engine, initialize_async_engine
from agent.shared.infrastructure.db.models import load_orm_models


class _StubConfigService:
    def __init__(self, database_url: str, browser_root: str) -> None:
        self._database_url = database_url
        self._browser_root = browser_root

    def get_str(self, key: str, default: str = "") -> str:
        if key == "database.url":
            return self._database_url
        if key == "browser.storage_root":
            return self._browser_root
        return default


@pytest_asyncio.fixture
async def admin_auth_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    from agent.bootstrap.container import AppContainer, set_active_container

    db_path = tmp_path / "admin-auth.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_path.resolve().as_posix()}"
    monkeypatch.setattr(db_engine, "DEFAULT_DATABASE_URL", db_url)
    set_active_container(AppContainer(config_service=_StubConfigService("", str(tmp_path / "browser"))))

    load_orm_models()
    await initialize_async_engine(metadata=Base.metadata)
    try:
        yield
    finally:
        await close_async_engine()


@pytest.mark.asyncio
async def test_default_admin_password_creates_initial_admin(admin_auth_db) -> None:
    service = AdminAuthService()

    admin = await service.authenticate(DEFAULT_ADMIN_PASSWORD)

    assert admin is not None
    assert admin.username == "admin"
    assert await service.verify_current_password(DEFAULT_ADMIN_PASSWORD)


@pytest.mark.asyncio
async def test_wrong_initial_password_does_not_create_admin(admin_auth_db) -> None:
    service = AdminAuthService()

    admin = await service.authenticate("wrong")

    assert admin is None
    assert await service.get_admin() is None


@pytest.mark.asyncio
async def test_default_password_does_not_override_existing_admin(admin_auth_db) -> None:
    service = AdminAuthService()
    custom_password = "Custom123!"
    await service.set_admin_password(custom_password)

    default_admin = await service.authenticate(DEFAULT_ADMIN_PASSWORD)
    custom_admin = await service.authenticate(custom_password)

    assert default_admin is None
    assert custom_admin is not None


@pytest.mark.asyncio
async def test_legacy_default_password_can_be_replaced(admin_auth_db):
    service = AdminAuthService()
    await service.set_admin_password(DEFAULT_ADMIN_PASSWORD, validate_policy=False)
    assert await service.authenticate(DEFAULT_ADMIN_PASSWORD) is not None
    assert not await service.has_secure_password()
    await service.set_admin_password("Private123!")
    assert await service.has_secure_password()


@pytest.mark.asyncio
async def test_bootstrap_creation_never_overwrites_an_existing_password(admin_auth_db):
    service = AdminAuthService()
    existing = await service.set_admin_password("Private123!")
    recovered = await service._create_default_admin()
    assert recovered.id == existing.id
    assert await service.authenticate(DEFAULT_ADMIN_PASSWORD) is None
    assert await service.authenticate("Private123!") is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_default_password_session_requires_change_before_console_access(
    admin_auth_db, monkeypatch, legacy
):
    import importlib
    from fastapi import Depends, FastAPI
    from httpx import ASGITransport, AsyncClient
    from agent.modules.admin_auth import auth
    from agent.shared.infrastructure.csrf_protection import CSRFProtectionMiddleware

    routes = importlib.import_module("agent.delivery.http.dashboard.auth_router")
    service = AdminAuthService()
    if legacy:
        await service.set_admin_password(DEFAULT_ADMIN_PASSWORD, validate_policy=False)
    monkeypatch.setattr(routes, "get_admin_auth_service", lambda: service)
    monkeypatch.setattr(auth, "get_admin_auth_service", lambda: service)
    monkeypatch.setattr(auth, "get_secret_key", lambda: "test-only-secret-" * 4)
    app = FastAPI()
    app.add_middleware(CSRFProtectionMiddleware, exempt_paths={"/login"})
    app.include_router(routes.router)

    async def protected():
        return {"status": "ok"}

    paths = ["/dashboard-api/protected", "/v1/api/protected", "/api/protected", "/"]
    for path in paths:
        app.add_api_route(path, protected, dependencies=[Depends(auth.get_current_admin)])

    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        response = await client.post("/login", json={"password": DEFAULT_ADMIN_PASSWORD})
        assert response.json()["password_change_required"] is True
        token = client.cookies["admin_token"]
        assert (await client.get("/")).headers["location"] == "/change-password"
        for path in paths[:-1]:
            response = await client.get(path)
            assert response.status_code == 403
            assert response.json()["detail"]["error"] == "password_change_required"
            assert (await client.get(path, headers={"Authorization": f"Bearer {token}"})).status_code == 403

        response = await client.get("/change-password")
        assert response.status_code == 200
        csrf = {"X-CSRF-Token": response.headers["X-CSRF-Token"]}
        response = await client.post("/change-password", json={
            "old_password": DEFAULT_ADMIN_PASSWORD, "new_password": "1234",
        }, headers=csrf)
        assert response.status_code == 400
        assert (await client.get(paths[0])).status_code == 403
        response = await client.post("/change-password", json={
            "old_password": DEFAULT_ADMIN_PASSWORD, "new_password": "Private123!",
        }, headers=csrf)
        assert response.status_code == 200
        assert "admin_token" not in client.cookies
        assert (await client.get(paths[0], headers={"Authorization": f"Bearer {token}"})).status_code == 401
        assert (await client.post("/login", json={"password": DEFAULT_ADMIN_PASSWORD})).status_code == 401
        response = await client.post("/login", json={"password": "Private123!"})
        assert response.json()["password_change_required"] is False
        assert (await client.get(paths[0])).status_code == 200


@pytest.mark.asyncio
async def test_default_password_html_login_redirects_to_password_change(admin_auth_db, monkeypatch):
    import importlib
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from agent.modules.admin_auth import auth

    routes = importlib.import_module("agent.delivery.http.dashboard.auth_router")
    service = AdminAuthService()
    monkeypatch.setattr(routes, "get_admin_auth_service", lambda: service)
    monkeypatch.setattr(auth, "get_admin_auth_service", lambda: service)
    monkeypatch.setattr(auth, "get_secret_key", lambda: "test-only-secret-" * 4)
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        response = await client.post("/login", data={"password": DEFAULT_ADMIN_PASSWORD})
        assert response.status_code == 302
        assert response.headers["location"] == "/change-password"
        assert (await client.post("/logout", json={})).status_code == 200
        assert "admin_token" not in client.cookies


@pytest.mark.asyncio
async def test_password_change_and_logout_revoke_copied_tokens(admin_auth_db, monkeypatch):
    import importlib
    from fastapi import Depends, FastAPI
    from httpx import ASGITransport, AsyncClient
    from agent.modules.admin_auth import auth

    routes = importlib.import_module("agent.delivery.http.dashboard.auth_router")
    service = AdminAuthService()
    await service.set_admin_password("Private123!")
    monkeypatch.setattr(routes, "get_admin_auth_service", lambda: service)
    monkeypatch.setattr(auth, "get_admin_auth_service", lambda: service)
    monkeypatch.setattr(auth, "get_secret_key", lambda: "test-only-secret-" * 4)
    app = FastAPI()
    app.include_router(routes.router)

    @app.get("/dashboard-api/protected", dependencies=[Depends(auth.get_current_admin)])
    async def protected():
        return {"status": "ok"}

    async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
        response = await client.post("/login", json={"password": "Private123!"})
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]
        assert (await client.get("/dashboard-api/protected", headers={"Authorization": "Bearer invalid"})).status_code == 401
        assert (await client.get("/dashboard-api/protected")).status_code == 200
        token = client.cookies["admin_token"]
        client.cookies.clear()
        headers = {"Authorization": f"Bearer {token}"}
        assert (await client.get("/dashboard-api/protected", headers=headers)).status_code == 200
        await service.set_admin_password("Changed123!")
        assert (await client.get("/dashboard-api/protected", headers=headers)).status_code == 401
        await client.post("/login", json={"password": "Changed123!"})
        copied = client.cookies["admin_token"]
        response = await client.post("/logout", json={})
        assert response.status_code == 200
        assert (await client.get("/dashboard-api/protected", headers={"Authorization": f"Bearer {copied}"})).status_code == 401
        assert (await client.get("/logout")).status_code == 405


def test_login_throttle_expires_and_bounds_clients(monkeypatch):
    from agent.modules.admin_auth import service as module
    from agent.modules.admin_auth import LoginThrottled

    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    service = AdminAuthService()
    for _ in range(5):
        service.check_login_allowed("client")
    with pytest.raises(LoginThrottled) as error:
        service.check_login_allowed("client")
    assert error.value.retry_after == 60
    for index in range(25):
        service.check_login_allowed(str(index))
    with pytest.raises(LoginThrottled):
        service.check_login_allowed("new-client")
    assert len(service._login_attempts) == 30
    now[0] += 61
    service.check_login_allowed("client")
    assert len(service._login_attempts) == 1


def test_session_migration_preserves_existing_credentials(tmp_path):
    import sqlite3
    from agent.modules.admin_auth import migrate_admin_auth_tables

    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE admin_credentials (id INTEGER PRIMARY KEY, password_hash TEXT)")
        connection.execute("INSERT INTO admin_credentials VALUES (1, 'existing-hash')")
    for _ in range(2):
        migrate_admin_auth_tables(f"sqlite:///{path.as_posix()}")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT password_hash, session_version FROM admin_credentials").fetchone() == ("existing-hash", 0)


def test_signing_secret_is_persisted_and_scoped_to_configuration(tmp_path, monkeypatch):
    import jwt
    from agent.modules.admin_auth import auth
    from agent.shared.config.factory import create_config_service

    first = create_config_service(yaml_path=tmp_path / "first.yaml")
    monkeypatch.setattr(auth, "get_config_service", lambda: first)
    token = auth.create_access_token({"sub": "1", "ver": 0})
    secret = first.get_str("security.jwt_secret")
    assert len(secret) == 64
    assert auth.get_secret_key() == secret
    assert jwt.decode(token, secret, algorithms=["HS256"])["ver"] == 0
    reloaded = create_config_service(yaml_path=tmp_path / "first.yaml")
    assert reloaded.get_str("security.jwt_secret") == secret
    second = create_config_service(yaml_path=tmp_path / "second.yaml")
    monkeypatch.setattr(auth, "get_config_service", lambda: second)
    assert auth.get_secret_key() != secret


@pytest.mark.asyncio
async def test_login_throttle_returns_retry_after_before_authentication(admin_auth_db, monkeypatch):
    import importlib
    from unittest.mock import AsyncMock
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    routes = importlib.import_module("agent.delivery.http.dashboard.auth_router")
    service = AdminAuthService()
    service.authenticate = AsyncMock(return_value=None)
    monkeypatch.setattr(routes, "get_admin_auth_service", lambda: service)
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        for _ in range(5):
            assert (await client.post("/login", json={"password": "wrong"})).status_code == 401
        response = await client.post("/login", json={"password": "wrong"})
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0
        assert service.authenticate.await_count == 5
