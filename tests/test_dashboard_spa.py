import pytest
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from agent.delivery.http.dashboard import spa
from agent.delivery.http.dashboard.router import router as dashboard_router
from agent.modules.admin_auth import get_current_admin


@pytest.fixture
def spa_client():
    app = FastAPI()
    app.include_router(dashboard_router)
    app.dependency_overrides[get_current_admin] = lambda: "test_admin"
    return TestClient(app, headers={"Accept": "text/html"})


@pytest.mark.parametrize("path", [
    "/",
    "/settings",
    "/settings/decisions",
    "/settings/providers/llm/openai",
    "/settings/providers/web/my.connection",
    "/settings/providers/decision/default",
    "/settings/providers/workspace/local",
    "/settings/providers/legacy",
    "/settings/backends/local",
    "/settings/connections/github",
    "/settings/channels/telegram",
    "/settings/agents/new",
    "/settings/agents/my-agent",
    "/settings/sandboxes/instabox/machine-123",
    "/settings/sandboxes/instabox/machine-123/?tab=files",
    "/settings/pairing",
    "/channels",
    "/agents",
    "/future-page/nested/detail",
])
def test_html_navigation_serves_spa(spa_client, path):
    response = spa_client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<div id="root">' in response.text


@pytest.mark.parametrize("path", ["/", "/settings", "/future-page/nested/detail"])
@pytest.mark.parametrize("built", [True, False])
def test_spa_html_disables_caching(spa_client, tmp_path, monkeypatch, path, built):
    index_file = tmp_path / "index.html"
    if built:
        index_file.write_text('<div id="root">Built dashboard</div>', encoding="utf-8")
    monkeypatch.setattr(spa, "INDEX_FILE", index_file)

    response = spa_client.get(path)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    if built:
        assert response.text == index_file.read_text(encoding="utf-8")
        assert response.headers["etag"]
        assert response.headers["last-modified"]
    else:
        assert "Dashboard frontend is not built" in response.text


def test_static_index_disables_caching_while_hashed_assets_remain_immutable(spa_client, tmp_path):
    (tmp_path / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-abc123.js").write_text("export const ready = true;", encoding="utf-8")
    spa_client.app.mount("/dashboard-assets", spa.CachedStaticFiles(directory=tmp_path))

    response = spa_client.get("/dashboard-assets/index.html")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    etag = response.headers["etag"]
    revalidated = spa_client.get("/dashboard-assets/index.html", headers={"If-None-Match": etag})
    assert revalidated.status_code == 304
    assert revalidated.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert revalidated.headers["etag"] == etag

    asset_response = spa_client.get("/dashboard-assets/assets/index-abc123.js")
    assert asset_response.status_code == 200
    assert asset_response.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize(("path", "status"), [
    ("/api/missing", 404),
    ("/v1/api/missing", 404),
    ("/v2/api/missing", 404),
    ("/dashboard-api/missing", 404),
    ("/dashboard-api", 404),
    ("/dashboard-assets/missing.js", 404),
    ("/channels/missing", 404),
    ("/channels/github/webhook", 404),
    ("/agents/cards/missing", 405),
    ("/scheduler/jobs/missing", 405),
    ("/tasks/list/missing", 404),
    ("/services/missing/endpoint", 404),
    ("/providers/missing", 404),
    ("/prompt-variables/missing", 405),
    ("/integrations/missing", 404),
    ("/docs/missing", 404),
    ("/redoc/missing", 404),
    ("/openapi.json/missing", 404),
])
def test_reserved_paths_do_not_use_spa_fallback(spa_client, path, status):
    # No credentials: rejected fallback paths must not trigger admin redirects.
    spa_client.app.dependency_overrides.clear()
    response = spa_client.get(path, follow_redirects=False)

    assert response.status_code == status
    assert response.json() == {
        "detail": "Not Found" if status == 404 else "Method Not Allowed",
    }


@pytest.mark.parametrize("accept", ["application/json", "text/css", "*/*", ""])
def test_non_html_requests_do_not_use_spa_fallback(spa_client, accept):
    response = spa_client.get("/future-page/nested/detail", headers={"Accept": accept})

    assert response.status_code == 404


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def test_other_methods_do_not_use_spa_fallback(spa_client, method):
    response = spa_client.request(method, "/future-page/nested/detail")

    assert response.status_code == 404


def test_spa_fallback_keeps_admin_authentication(spa_client):
    spa_client.app.dependency_overrides.clear()
    response = spa_client.get("/settings/decisions", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_static_mount_after_dashboard_router_stays_public(spa_client, tmp_path):
    (tmp_path / "app.js").write_text("export const ready = true;", encoding="utf-8")
    spa_client.app.mount("/dashboard-assets", StaticFiles(directory=tmp_path))
    spa_client.app.dependency_overrides.clear()

    response = spa_client.get("/dashboard-assets/app.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert spa_client.get("/dashboard-assets/missing.js").status_code == 404


def test_reserved_endpoint_after_dashboard_router_keeps_its_response(spa_client):
    @spa_client.app.get("/health")
    def health():
        return {"status": "ok"}

    @spa_client.app.post("/channels/test/webhook")
    def webhook():
        raise HTTPException(status_code=404, detail="Channel not found")

    spa_client.app.dependency_overrides.clear()
    assert spa_client.get("/health").json() == {"status": "ok"}
    assert spa_client.get("/channels/test/webhook").status_code == 405
    response = spa_client.post("/channels/test/webhook")
    assert response.status_code == 404
    assert response.json() == {"detail": "Channel not found"}


def test_new_namespaces_registered_before_dashboard_keep_their_responses(tmp_path):
    app = FastAPI()

    @app.get("/plugin/status")
    def plugin_status():
        return {"status": "ok"}

    @app.get("/plugin/missing")
    def plugin_missing():
        raise HTTPException(status_code=404, detail="Plugin not found")

    @app.post("/plugin/webhook")
    def plugin_webhook():
        return {"status": "accepted"}

    (tmp_path / "app.js").write_text("export const ready = true;", encoding="utf-8")
    app.mount("/plugin-assets", StaticFiles(directory=tmp_path))
    app.include_router(dashboard_router)
    client = TestClient(app, headers={"Accept": "text/html"}, follow_redirects=False)

    assert client.get("/plugin/status").json() == {"status": "ok"}
    response = client.get("/plugin/missing")
    assert response.status_code == 404
    assert response.json() == {"detail": "Plugin not found"}
    assert client.post("/plugin/webhook").json() == {"status": "accepted"}
    assert client.get("/plugin-assets/app.js").status_code == 200
    assert client.get("/plugin-assets/missing.js").status_code == 404
