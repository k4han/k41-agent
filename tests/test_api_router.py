import importlib

from fastapi import FastAPI
from fastapi.testclient import TestClient


router_module = importlib.import_module("agent.delivery.http.api.router")


def test_chat_sync_returns_response_payload(monkeypatch):
    built_params = {
        "workflow": "react_agent",
        "user_input": "List files",
        "service_type": "backend",
        "working_dir": "D:/workspace/sample",
        "max_context_tokens": 50_000,
        "thread_id": "api_alice",
    }

    def fake_build_run_params(**params):
        assert params == {
            "platform": "api",
            "user_id": "alice",
            "user_input": "List files",
            "workflow": "react_agent",
            "service_type": "backend",
            "working_dir": "D:/workspace/sample",
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
