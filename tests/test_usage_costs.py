from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent.modules.providers import ProviderConfig, ProviderType
from agent.modules.usage import estimate_usage_cost


@pytest.fixture
def prices(monkeypatch):
    model = SimpleNamespace(id="model-a", cost_input=2.0, cost_output=8.0)
    catalog = SimpleNamespace(models=[model])
    lookups = []
    monkeypatch.setattr("agent.modules.providers.get_provider_catalog_entry", lambda key: lookups.append(key) or catalog)
    provider = ProviderConfig(
        name="my-provider", provider_type=ProviderType.OPENAI_COMPATIBLE,
        base_url="https://example.com", api_key="", default_model="model-a", catalog_id="catalog-provider",
    )
    return provider, model, lookups


def test_cost_estimate_uses_configured_catalog_and_sums_model_usage(prices):
    provider, _, lookups = prices
    estimate = estimate_usage_cost([
        {"provider": "my-provider", "model": "model-a", "input_tokens": 100000, "output_tokens": 20000},
        {"provider": "my-provider", "model": "model-a", "input_tokens": 100000, "output_tokens": 20000},
    ], lambda name: provider)
    assert estimate == {"estimated_cost_usd": 0.72, "cost_complete": True}
    assert lookups == ["catalog-provider", "catalog-provider"]


def test_cost_estimate_marks_missing_prices_and_token_reports_partial(prices):
    provider, _, _ = prices
    estimate = estimate_usage_cost([
        {"provider": "my-provider", "model": "model-a", "input_tokens": 100000, "output_tokens": 20000, "missing_usage_count": 1},
        {"provider": "my-provider", "model": "unknown", "input_tokens": 1000000, "output_tokens": 20000},
    ], lambda name: provider)
    assert estimate == {"estimated_cost_usd": 0.36, "cost_complete": False}


def test_empty_usage_and_account_subscriptions_have_no_on_demand_estimate(prices):
    provider, _, _ = prices
    assert estimate_usage_cost([], lambda name: provider) == {"estimated_cost_usd": None, "cost_complete": False}
    usage = [{"provider": "my-provider", "model": "model-a", "input_tokens": 100000, "output_tokens": 20000}]
    estimate = estimate_usage_cost(usage, lambda name: replace(provider, provider_type=ProviderType.CHATGPT))
    assert estimate == {"estimated_cost_usd": None, "cost_complete": False}


@pytest.mark.parametrize("price", [None, -1, float("nan"), float("inf"), True, "2"])
def test_invalid_prices_never_become_a_zero_dollar_estimate(prices, price):
    provider, model, _ = prices
    model.cost_input = price
    estimate = estimate_usage_cost([
        {"provider": "my-provider", "model": "model-a", "input_tokens": 100000, "output_tokens": 20000},
    ], lambda name: provider)
    assert estimate == {"estimated_cost_usd": None, "cost_complete": False}


def test_free_models_and_zero_output_price_are_supported(prices):
    provider, model, _ = prices
    model.cost_input = 0
    model.cost_output = 0
    assert estimate_usage_cost([
        {"provider": "my-provider", "model": "model-a", "input_tokens": 100000, "output_tokens": 20000},
    ], lambda name: provider) == {"estimated_cost_usd": 0.0, "cost_complete": True}
    model.cost_input = 2
    model.cost_output = None
    assert estimate_usage_cost([
        {"provider": "my-provider", "model": "model-a", "input_tokens": 100000, "output_tokens": 0},
    ], lambda name: provider) == {"estimated_cost_usd": 0.2, "cost_complete": True}


def test_deleted_providers_and_missing_usage_are_unavailable(prices):
    provider, _, _ = prices
    def missing_provider(name):
        raise ValueError("Provider not found")
    usage = [{"provider": "deleted", "model": "model-a", "input_tokens": 100000, "output_tokens": 0}]
    assert estimate_usage_cost(usage, missing_provider) == {"estimated_cost_usd": None, "cost_complete": False}
    usage = [{"provider": "my-provider", "model": "model-a", "input_tokens": 0, "output_tokens": 0, "missing_usage_count": 1}]
    assert estimate_usage_cost(usage, lambda name: provider) == {"estimated_cost_usd": None, "cost_complete": False}


@pytest.mark.asyncio
async def test_thread_usage_api_includes_estimated_cost_and_host_platform(prices, monkeypatch):
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from agent.delivery.http.dashboard.routes import usage as routes

    provider, _, _ = prices
    payload = {"thread_id": "thread-a", "models": [
        {"provider": "my-provider", "model": "model-a", "input_tokens": 100000, "output_tokens": 20000},
    ]}
    service = SimpleNamespace(get_thread_usage=AsyncMock(return_value=payload))
    monkeypatch.setattr(routes, "get_usage_service", lambda: service)
    monkeypatch.setattr(routes, "get_app_container", lambda request: SimpleNamespace(
        provider_service=SimpleNamespace(get_provider=lambda name: provider),
    ))
    monkeypatch.setattr(routes.platform, "system", lambda: "Linux")
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard-api/usage/thread/thread-a")
    assert response.status_code == 200
    data = response.json()
    assert data["estimated_cost_usd"] == 0.36
    assert data["cost_complete"] is True
    assert data["host_platform"] == "Linux"
    assert data["models"] == payload["models"]
