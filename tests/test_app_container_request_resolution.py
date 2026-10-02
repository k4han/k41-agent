"""Web requests must resolve the app's container, never a stale default.

Regression coverage for the review finding where module-level bootstrap
created a process-default container (C0) while ``create_app`` built a
different one (C1); request tasks never inherited the lifespan activation
and therefore resolved C0 (missing DB engine -> HTTP 500).
"""

import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import Request

from agent.bootstrap.container import (
    clear_active_container,
    create_test_container,
    get_active_container,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_create_app_activates_container_immediately() -> None:
    import agent.bootstrap.container as container_module
    from agent.bootstrap.app import create_app

    clear_active_container()
    container = create_test_container()
    try:
        fastapi_app = create_app(container=container)

        assert get_active_container() is container
        assert fastapi_app.state.container is container
        assert container_module._default_container is None
    finally:
        clear_active_container()


@pytest.mark.asyncio
async def test_request_task_resolves_app_container() -> None:
    from agent.bootstrap.app import create_app
    from agent.delivery.http.dashboard.routes.helpers.deps import get_app_container
    from agent.shared.config import get_config_service

    clear_active_container()
    container = create_test_container()
    try:
        fastapi_app = create_app(container=container)

        @fastapi_app.get("/__probe_container", include_in_schema=False)
        async def probe(request: Request) -> dict:
            return {
                "active_matches": get_active_container()
                is request.app.state.container,
                "config_matches": get_config_service()
                is request.app.state.config_service,
                "deps_matches": get_app_container(request)
                is request.app.state.container,
            }

        # Drop the process-level activation so only the request middleware can
        # restore the app container inside the request task.
        clear_active_container()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://testserver",
        ) as client:
            resp = await client.get("/__probe_container")

        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["active_matches"] is True
        assert payload["config_matches"] is True
        assert payload["deps_matches"] is True
    finally:
        clear_active_container()


def test_module_import_activates_app_container() -> None:
    script = (
        "import asyncio\n"
        "import sys\n"
        "\n"
        "from agent.bootstrap import app as app_module\n"
        "from agent.bootstrap.container import get_active_container\n"
        "\n"
        "async def probe():\n"
        "    return get_active_container() is app_module.app.state.container\n"
        "\n"
        "import agent.bootstrap.container as container_module\n"
        "ok = (\n"
        "    get_active_container() is app_module.app.state.container\n"
        "    and app_module.settings is app_module.app.state.bootstrap_config\n"
        "    and container_module._default_container is None\n"
        "    and asyncio.run(probe())\n"
        ")\n"
        "print('CONTAINER_OK' if ok else 'CONTAINER_MISMATCH')\n"
        "sys.exit(0 if ok else 1)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "CONTAINER_OK" in result.stdout


def test_register_runtime_defaults_does_not_spawn_default_container() -> None:
    import agent.bootstrap.container as container_module
    from agent.shared.config.constants import DEFAULT_CONFIG, KNOWN_RUNTIME_KEYS
    from agent.shared.config.service import register_runtime_defaults

    key = "tools.register_defaults_no_container.enabled"
    previous_known = key in KNOWN_RUNTIME_KEYS
    sentinel = object()
    previous_default = DEFAULT_CONFIG.get(key, sentinel)
    clear_active_container()
    try:
        register_runtime_defaults({key: True})

        assert container_module._default_container is None
        assert container_module.get_active_container() is None
        assert key in KNOWN_RUNTIME_KEYS
        assert DEFAULT_CONFIG.get(key) is True
    finally:
        if not previous_known:
            KNOWN_RUNTIME_KEYS.discard(key)
        if previous_default is sentinel:
            DEFAULT_CONFIG.pop(key, None)
        else:
            DEFAULT_CONFIG[key] = previous_default
        clear_active_container()


def test_container_activation_context_tokens_restore_previous() -> None:
    from agent.bootstrap.container import (
        activate_context_container,
        restore_context_container,
    )

    clear_active_container()
    container = create_test_container()
    try:
        token = activate_context_container(container)
        assert get_active_container() is container
        restore_context_container(token)
        assert get_active_container() is None
    finally:
        clear_active_container()


def test_get_container_dependency_raises_runtime_error_without_app_state() -> None:
    from agent.bootstrap.container import get_container
    from fastapi import FastAPI

    clear_active_container()
    fake_request = type("FakeRequest", (), {"app": FastAPI()})()
    with pytest.raises(RuntimeError, match="AppContainer is not available"):
        get_container(fake_request)
