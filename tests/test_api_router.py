import importlib
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from agent.modules.admin_auth import get_current_admin
from agent.delivery.http.api.schemas import ChatRequest
from agent.modules.providers.models import ModelOption, ProviderModelCatalog


router_module = importlib.import_module("agent.delivery.http.api.router")


def _workspace_payload(path: str | Path) -> dict:
    return {
        "backend": "local",
        "locator": str(path),
        "label": str(path),
        "metadata": {},
    }


def _resolved_workspace(path: str | Path):
    return router_module.resolve_workspace_ref(_workspace_payload(path))


def _create_client() -> TestClient:
    app = FastAPI()
    app.include_router(router_module.router)

    async def mock_admin(_: Request) -> str:
        return "test_admin"

    app.dependency_overrides[get_current_admin] = mock_admin
    return TestClient(app)


def test_chat_request_validates_plan_resume_payload() -> None:
    request = ChatRequest(
        message="",
        resume=True,
        resume_payload={"action": "approve", "target_agent": "worker"},
    )
    assert request.resume_payload is not None
    assert request.resume_payload.action == "approve"
    assert request.resume_payload.target_agent == "worker"


def test_chat_sync_returns_response_payload(monkeypatch):
    requested_workspace = _workspace_payload("D:/workspace/sample")
    resolved_workspace = _resolved_workspace("D:/workspace/sample")
    built_params = {
        "user_input": "List files",
        "thread_id": "api_alice",
        "agent_name": "default",
        "workflow": "react_agent",
        "workspace": resolved_workspace,
        "max_context_tokens": None,
        "provider": None,
        "model": None,
    }

    def fake_build_run_params(**params):
        assert params["workspace"].model_dump() == requested_workspace
        assert {**params, "workspace": None} == {
            "platform": "api",
            "user_id": "alice",
            "user_input": "List files",
            "workflow": "react_agent",
            "workspace": None,
            "agent_name": "default",
            "provider": None,
            "model": None,
            "resume": False,
        }
        return {**built_params, "workspace": params["workspace"]}

    async def fake_run_agent_full(**params):
        assert params == built_params
        return "stubbed-response"

    monkeypatch.setattr(router_module, "build_run_params", fake_build_run_params)
    monkeypatch.setattr(router_module, "run_agent_full", fake_run_agent_full)

    client = _create_client()
    response = client.post(
        "/api/chat",
        json={
            "message": "List files",
            "user_id": "alice",
            "workflow": "react_agent",
            "workspace": requested_workspace,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "response": "stubbed-response",
        "thread_id": "api_alice",
        "workflow": "react_agent",
    }


def test_chat_sync_prefers_agent_name_over_workflow(monkeypatch):
    built_params = {
        "user_input": "Deep research",
        "thread_id": "api_bob",
        "agent_name": "research-agent",
        "workflow": "react_agent",
        "workspace": None,
        "max_context_tokens": None,
        "provider": None,
        "model": None,
    }

    def fake_build_run_params(**params):
        assert params == {
            "platform": "api",
            "user_id": "bob",
            "user_input": "Deep research",
            "workflow": "react_agent",
            "workspace": None,
            "agent_name": "research-agent",
            "provider": None,
            "model": None,
            "resume": False,
        }
        return dict(built_params)

    async def fake_run_agent_full(**params):
        assert params == built_params
        return "research-response"

    monkeypatch.setattr(router_module, "build_run_params", fake_build_run_params)
    monkeypatch.setattr(router_module, "run_agent_full", fake_run_agent_full)

    client = _create_client()
    response = client.post(
        "/api/chat",
        json={
            "message": "Deep research",
            "user_id": "bob",
            "workflow": "react_agent",
            "agent_name": "research-agent",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "response": "research-response",
        "thread_id": "api_bob",
        "workflow": "react_agent",
    }


def test_chat_sync_passes_model_override(monkeypatch):
    built_params = {
        "user_input": "Use a faster model",
        "thread_id": "api_cara",
        "agent_name": "default",
        "workflow": "react_agent",
        "workspace": None,
        "max_context_tokens": None,
        "provider": "openai-main",
        "model": "direct-model",
    }

    def fake_build_run_params(**params):
        assert params == {
            "platform": "api",
            "user_id": "cara",
            "user_input": "Use a faster model",
            "workflow": None,
            "workspace": None,
            "agent_name": "default",
            "provider": "openai-main",
            "model": "direct-model",
            "resume": False,
        }
        return dict(built_params)

    async def fake_run_agent_full(**params):
        assert params == built_params
        return "model-response"

    monkeypatch.setattr(router_module, "build_run_params", fake_build_run_params)
    monkeypatch.setattr(router_module, "run_agent_full", fake_run_agent_full)

    client = _create_client()
    response = client.post(
        "/api/chat",
        json={
            "message": "Use a faster model",
            "user_id": "cara",
            "provider": "openai-main",
            "model": "direct-model",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "response": "model-response",
        "thread_id": "api_cara",
        "workflow": "react_agent",
    }


def test_chat_events_passes_attachments(monkeypatch):
    attachments = [
        {
            "name": "notes.txt",
            "mime_type": "text/plain",
            "size": 12,
            "kind": "text",
            "content": "hello world!",
            "base64": None,
        },
        {
            "name": "diagram.png",
            "mime_type": "image/png",
            "size": 4,
            "kind": "image",
            "content": None,
            "base64": "abcd",
        },
    ]
    built_params = {
        "user_input": "Review these",
        "thread_id": "api_alice",
        "agent_name": "default",
        "workflow": None,
        "workspace": None,
        "max_context_tokens": None,
        "provider": None,
        "model": None,
        "attachments": attachments,
    }

    def fake_build_run_params(**params):
        assert params == {
            "platform": "api",
            "user_id": "alice",
            "user_input": "Review these",
            "workflow": None,
            "workspace": None,
            "agent_name": "default",
            "provider": None,
            "model": None,
            "attachments": attachments,
            "resume": False,
        }
        return dict(built_params)

    async def fake_run_agent_stream(**params):
        assert params == built_params
        yield {"type": "final", "content": "attached"}

    monkeypatch.setattr(router_module, "build_run_params", fake_build_run_params)
    monkeypatch.setattr(router_module, "run_agent_stream", fake_run_agent_stream)

    client = _create_client()
    response = client.post(
        "/api/chat/events",
        json={
            "message": "Review these",
            "user_id": "alice",
            "attachments": [
                {
                    "name": "notes.txt",
                    "mime_type": "text/plain",
                    "size": 12,
                    "kind": "text",
                    "content": "hello world!",
                },
                {
                    "name": "diagram.png",
                    "mime_type": "image/png",
                    "size": 4,
                    "kind": "image",
                    "base64": "abcd",
                },
            ],
        },
    )

    assert response.status_code == 200
    assert response.text == '{"type": "final", "content": "attached"}\n'


def test_chat_events_can_resume_existing_thread(monkeypatch):
    built_params = {
        "user_input": "Continue",
        "thread_id": "telegram_123_456",
        "agent_name": "default",
        "workflow": None,
        "workspace": None,
        "max_context_tokens": None,
        "provider": None,
        "model": None,
    }

    def fake_build_run_params(**params):
        assert params == {
            "platform": "api",
            "user_id": "dashboard",
            "user_input": "Continue",
            "workflow": None,
            "workspace": None,
            "agent_name": "default",
            "provider": None,
            "model": None,
            "thread_id": "telegram_123_456",
            "resume": False,
        }
        return dict(built_params)

    async def fake_run_agent_stream(**params):
        assert params == built_params
        yield {"type": "final", "content": "resumed"}

    monkeypatch.setattr(router_module, "build_run_params", fake_build_run_params)
    monkeypatch.setattr(router_module, "run_agent_stream", fake_run_agent_stream)

    client = _create_client()
    response = client.post(
        "/api/chat/events",
        json={
            "message": "Continue",
            "user_id": "dashboard",
            "thread_id": "telegram_123_456",
        },
    )

    assert response.status_code == 200
    assert response.text == '{"type": "final", "content": "resumed"}\n'


def test_chat_events_can_create_new_thread(monkeypatch, tmp_path):
    requested_workspace = _workspace_payload(tmp_path)
    resolved_workspace = _resolved_workspace(tmp_path)
    remembered: list[tuple[str, dict]] = []
    built_params = {
        "user_input": "Start",
        "thread_id": "api_dashboard_generated",
        "agent_name": "default",
        "workflow": None,
        "workspace": requested_workspace,
        "max_context_tokens": None,
        "provider": None,
        "model": None,
    }

    monkeypatch.setattr(
        router_module,
        "create_thread_id",
        lambda **kwargs: "api_dashboard_generated",
    )

    def fake_build_run_params(**params):
        assert params["workspace"].model_dump() == requested_workspace
        assert {**params, "workspace": None} == {
            "platform": "api",
            "user_id": "dashboard",
            "user_input": "Start",
            "workflow": None,
            "workspace": None,
            "agent_name": "default",
            "provider": None,
            "model": None,
            "thread_id": "api_dashboard_generated",
            "resume": False,
        }
        return dict(built_params)

    async def fake_remember_thread_workspace_ref(thread_id: str, workspace):
        remembered.append((thread_id, workspace.model_dump()))
        return workspace

    async def fake_run_agent_stream(**params):
        assert params == {**built_params, "workspace": resolved_workspace}
        yield {"type": "final", "content": "started"}

    monkeypatch.setattr(router_module, "build_run_params", fake_build_run_params)
    monkeypatch.setattr(router_module, "remember_thread_workspace_ref", fake_remember_thread_workspace_ref)
    monkeypatch.setattr(router_module, "run_agent_stream", fake_run_agent_stream)

    client = _create_client()
    response = client.post(
        "/api/chat/events",
        json={
            "message": "Start",
            "user_id": "dashboard",
            "new_thread": True,
            "workspace": requested_workspace,
        },
    )

    assert response.status_code == 200
    assert response.text == (
        '{"type": "thread_created", "thread_id": "api_dashboard_generated"}\n'
        '{"type": "final", "content": "started"}\n'
    )
    assert remembered == [("api_dashboard_generated", resolved_workspace.model_dump())]


def test_chat_events_rejects_dashboard_new_thread_without_workspace(monkeypatch):
    monkeypatch.setattr(
        router_module,
        "create_thread_id",
        lambda **kwargs: "api_dashboard_generated",
    )
    client = _create_client()
    response = client.post(
        "/api/chat/events",
        json={
            "message": "Start",
            "user_id": "dashboard",
            "new_thread": True,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Dashboard chats require a resolved workspace."


def test_chat_events_resolves_and_remembers_workspace(monkeypatch, tmp_path):
    requested_workspace = _workspace_payload(tmp_path)
    resolved_workspace = _resolved_workspace(tmp_path)
    remembered: list[tuple[str, dict]] = []
    built_params = {
        "user_input": "Work here",
        "thread_id": "api_dashboard_workspace",
        "agent_name": "default",
        "workflow": None,
        "workspace": requested_workspace,
        "max_context_tokens": None,
        "provider": None,
        "model": None,
    }

    def fake_build_run_params(**params):
        assert params["workspace"].model_dump() == requested_workspace
        return dict(built_params)

    async def fake_remember_thread_workspace_ref(thread_id: str, workspace):
        remembered.append((thread_id, workspace.model_dump()))
        return workspace

    async def fake_run_agent_stream(**params):
        assert params == {**built_params, "workspace": resolved_workspace}
        yield {"type": "final", "content": "done"}

    monkeypatch.setattr(router_module, "build_run_params", fake_build_run_params)
    monkeypatch.setattr(router_module, "remember_thread_workspace_ref", fake_remember_thread_workspace_ref)
    monkeypatch.setattr(router_module, "run_agent_stream", fake_run_agent_stream)

    client = _create_client()
    response = client.post(
        "/api/chat/events",
        json={
            "message": "Work here",
            "user_id": "dashboard",
            "thread_id": "api_dashboard_workspace",
            "workspace": requested_workspace,
        },
    )

    assert response.status_code == 200
    assert response.text == '{"type": "final", "content": "done"}\n'
    assert remembered == [("api_dashboard_workspace", resolved_workspace.model_dump())]


def test_chat_events_ensures_stored_sandbox_workspace_before_stream(monkeypatch):
    from agent.modules.workspaces import WorkspaceRef

    stored_workspace = WorkspaceRef(
        backend="modal",
        locator="sb-expired",
        label="acme/widgets",
        metadata={
            "root": "/workspace",
            "source": "github",
            "repository_id": 44,
            "repository_full_name": "acme/widgets",
        },
    )
    ready_workspace = WorkspaceRef(
        backend="modal",
        locator="sb-new",
        label="acme/widgets",
        metadata={
            "root": "/workspace",
            "source": "github",
            "repository_id": 44,
            "repository_full_name": "acme/widgets",
        },
    )
    remembered: list[tuple[str, dict]] = []
    ensure_calls: list[tuple[str, str]] = []
    built_params = {
        "user_input": "Continue",
        "thread_id": "api_dashboard_old_thread",
        "agent_name": "default",
        "workflow": None,
        "workspace": None,
        "max_context_tokens": None,
        "provider": None,
        "model": None,
    }

    def fake_build_run_params(**params):
        assert params["workspace"] is None
        return dict(built_params)

    async def fake_get_thread_workspace_ref(thread_id: str):
        assert thread_id == "api_dashboard_old_thread"
        return stored_workspace

    async def fake_ensure_workspace_ready(workspace, *, thread_id: str | None = None):
        assert workspace == stored_workspace
        assert thread_id == "api_dashboard_old_thread"
        ensure_calls.append((thread_id, workspace.locator))
        return ready_workspace

    async def fake_remember_thread_workspace_ref(thread_id: str, workspace):
        remembered.append((thread_id, workspace.model_dump()))
        return workspace

    async def fake_run_agent_stream(**params):
        assert params == {**built_params, "workspace": ready_workspace}
        yield {"type": "final", "content": "continued"}

    monkeypatch.setattr(router_module, "build_run_params", fake_build_run_params)
    monkeypatch.setattr(router_module, "get_thread_workspace_ref", fake_get_thread_workspace_ref)
    monkeypatch.setattr(router_module, "ensure_workspace_ready", fake_ensure_workspace_ready)
    monkeypatch.setattr(router_module, "remember_thread_workspace_ref", fake_remember_thread_workspace_ref)
    monkeypatch.setattr(router_module, "run_agent_stream", fake_run_agent_stream)

    client = _create_client()
    response = client.post(
        "/api/chat/events",
        json={
            "message": "Continue",
            "user_id": "dashboard",
            "thread_id": "api_dashboard_old_thread",
        },
    )

    assert response.status_code == 200
    assert response.text == '{"type": "final", "content": "continued"}\n'
    assert ensure_calls == [("api_dashboard_old_thread", "sb-expired")]
    assert remembered == [
        ("api_dashboard_old_thread", ready_workspace.model_dump())
    ]


def test_chat_events_streams_tool_calls_as_ndjson(monkeypatch):
    built_params = {
        "user_input": "Use a tool",
        "thread_id": "api_alice",
        "agent_name": "default",
        "workflow": None,
        "workspace": None,
        "max_context_tokens": None,
        "provider": None,
        "model": None,
    }

    def fake_build_run_params(**params):
        return dict(built_params)

    async def fake_run_agent_stream(**params):
        assert params == built_params
        yield {"type": "tool_call", "id": "call-1", "name": "list_dir", "args": {"path": "."}}
        yield {
            "type": "tool_result",
            "tool_call_id": "call-1",
            "name": "list_dir",
            "content": "README.md",
        }
        yield {"type": "final", "content": "done"}

    monkeypatch.setattr(router_module, "build_run_params", fake_build_run_params)
    monkeypatch.setattr(router_module, "run_agent_stream", fake_run_agent_stream)

    client = _create_client()
    response = client.post("/api/chat/events", json={"message": "Use a tool", "user_id": "alice"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.text == (
        '{"type": "tool_call", "id": "call-1", "name": "list_dir", "args": {"path": "."}}\n'
        '{"type": "tool_result", "tool_call_id": "call-1", "name": "list_dir", "content": "README.md"}\n'
        '{"type": "final", "content": "done"}\n'
    )


def test_chat_events_streams_classified_agent_errors(monkeypatch):
    built_params = {
        "user_input": "Use a busy model",
        "thread_id": "api_alice",
        "agent_name": "default",
        "workflow": None,
        "workspace": None,
        "max_context_tokens": None,
        "provider": None,
        "model": None,
    }

    class FakeRateLimitError(Exception):
        status_code = 429

    def fake_build_run_params(**params):
        return dict(built_params)

    async def fake_run_agent_stream(**params):
        assert params == built_params
        raise FakeRateLimitError("429 too many requests")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(router_module, "build_run_params", fake_build_run_params)
    monkeypatch.setattr(router_module, "run_agent_stream", fake_run_agent_stream)

    client = _create_client()
    response = client.post(
        "/api/chat/events",
        json={"message": "Use a busy model", "user_id": "alice"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    event = json.loads(response.text)
    assert event["type"] == "error"
    assert event["code"] == "rate_limit"
    assert "rate limiting" in event["content"]


def test_provider_models_endpoint_refreshes_and_serializes_catalog(monkeypatch):
    async def fake_list_provider_model_catalog(provider_name, include_remote=False):
        assert provider_name == "openai-main"
        assert include_remote is True
        return ProviderModelCatalog(
            provider="openai-main",
            provider_type="openai_compatible",
            default_model="openai-default",
            can_list_models=True,
            models=(
                ModelOption(id="openai-live", label="openai-live", source="live"),
                ModelOption(id="openai-config", label="openai-config", source="config"),
            ),
        )

    monkeypatch.setattr(
        router_module,
        "list_provider_model_catalog",
        fake_list_provider_model_catalog,
    )

    client = _create_client()
    response = client.get("/api/providers/openai-main/models?refresh=true")

    assert response.status_code == 200
    assert response.json() == {
        "provider": "openai-main",
        "provider_type": "openai_compatible",
        "default_model": "openai-default",
        "can_list_models": True,
        "models": [
            {
                "id": "openai-live",
                "label": "openai-live",
                "source": "live",
                "context_window": None,
                "input_types": None,
            },
            {
                "id": "openai-config",
                "label": "openai-config",
                "source": "config",
                "context_window": None,
                "input_types": None,
            },
        ],
        "error": None,
    }


def test_graph_endpoints_reflect_registered_workflows(monkeypatch):
    monkeypatch.setattr(
        router_module,
        "list_registered_workflows",
        lambda: ["react_agent", "research_chain", "router"],
    )

    client = _create_client()

    graphs_response = client.get("/api/graphs")
    assert graphs_response.status_code == 200
    assert graphs_response.json() == {
        "graphs": ["react_agent", "research_chain", "router"],
    }

    health_response = client.get("/api/health")
    assert health_response.status_code == 200
    assert health_response.json() == {
        "status": "ok",
        "graphs": ["react_agent", "research_chain", "router"],
    }
