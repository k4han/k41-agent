import importlib

from fastapi import FastAPI
from fastapi.testclient import TestClient


router_module = importlib.import_module("agent.delivery.http.api.router")


def test_chat_sync_returns_response_payload(monkeypatch):
    built_params = {
        "user_input": "List files",
        "thread_id": "api_alice",
        "agent_name": "default",
        "workflow": "react_agent",
        "service_type": "backend",
        "working_dir": "D:/workspace/sample",
        "max_context_tokens": None,
    }

    def fake_build_run_params(**params):
        assert params == {
            "platform": "api",
            "user_id": "alice",
            "user_input": "List files",
            "workflow": "react_agent",
            "service_type": "backend",
            "working_dir": "D:/workspace/sample",
            "agent_name": "default",
        }
        return dict(built_params)

    async def fake_run_agent_full(**params):
        assert params == built_params
        return "stubbed-response"

    monkeypatch.setattr(router_module, "build_run_params", fake_build_run_params)
    monkeypatch.setattr(router_module, "run_agent_full", fake_run_agent_full)

    app = FastAPI()
    app.include_router(router_module.router)

    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={
            "message": "List files",
            "user_id": "alice",
            "workflow": "react_agent",
            "service_type": "backend",
            "working_dir": "D:/workspace/sample",
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
        "working_dir": None,
        "max_context_tokens": None,
    }

    def fake_build_run_params(**params):
        assert params == {
            "platform": "api",
            "user_id": "bob",
            "user_input": "Deep research",
            "workflow": "react_agent",
            "working_dir": None,
            "agent_name": "research-agent",
        }
        return dict(built_params)

    async def fake_run_agent_full(**params):
        assert params == built_params
        return "research-response"

    monkeypatch.setattr(router_module, "build_run_params", fake_build_run_params)
    monkeypatch.setattr(router_module, "run_agent_full", fake_run_agent_full)

    app = FastAPI()
    app.include_router(router_module.router)

    client = TestClient(app)
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


def test_graph_endpoints_reflect_registered_workflows(monkeypatch):
    monkeypatch.setattr(
        router_module,
        "list_registered_workflows",
        lambda: ["react_agent", "research_chain", "router"],
    )

    app = FastAPI()
    app.include_router(router_module.router)

    client = TestClient(app)

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
