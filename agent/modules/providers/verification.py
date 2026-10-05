"""Provider connection verification and model discovery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import inspect
import logging
import time
from typing import Any

import httpx

from agent.modules.providers.catalog import get_provider_catalog_entry
from agent.modules.providers.ports import ChatModelFactory
from agent.modules.providers.provider import ProviderConfig, ProviderType

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProviderVerificationResult:
    """Result of testing provider credentials and discovering models."""

    ok: bool
    message: str
    latency_ms: int | None = None
    models: list[str] = field(default_factory=list)
    suggested_default_model: str = ""
    error_code: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "models": self.models,
            "suggested_default_model": self.suggested_default_model,
            "error_code": self.error_code,
            "details": self.details,
        }


def resolve_suggested_default_model(models: list[str], catalog_default: str = "") -> str:
    """Resolve suggested default model from discovered models list.

    Priority order:
    1. Catalog default model if present in discovered models (exact or suffix match).
    2. Common fast/efficient models containing 'flash', 'mini', or 'sonnet' (in that order).
    3. First model in the discovered list if non-empty.
    4. Empty string if no models discovered.
    """
    if not models:
        return ""

    if catalog_default:
        normalized_cat = catalog_default.strip().lower()
        cat_base = normalized_cat.split("/")[-1]
        for m in models:
            norm_m = m.strip().lower()
            model_base = norm_m.split("/")[-1]
            if (
                norm_m == normalized_cat
                or model_base == cat_base
                or norm_m.endswith("/" + normalized_cat)
                or norm_m.endswith("/" + cat_base)
                or normalized_cat.endswith("/" + norm_m)
            ):
                return m

    for keyword in ("flash", "mini", "sonnet"):
        for m in models:
            if keyword in m.lower():
                return m

    return models[0]


async def verify_provider_connection(
    provider_type: str | ProviderType,
    api_key: str,
    base_url: str = "",
    *,
    catalog_id: str = "",
    provider_name: str = "",
    factory: ChatModelFactory | None = None,
    timeout: float = 10.0,
) -> ProviderVerificationResult:
    """Verify provider credentials by querying available models and measuring round-trip latency."""
    norm_type_str = str(provider_type).strip().lower().replace("-", "_")
    if norm_type_str == "openai":
        norm_type_str = "openai_compatible"
        if not base_url:
            base_url = "https://api.openai.com/v1"

    if norm_type_str not in ("google", "anthropic", "openai_compatible"):
        return ProviderVerificationResult(
            ok=False,
            message=f"Unsupported provider type: {provider_type}.",
            error_code="INVALID_CONFIG",
            latency_ms=0,
        )

    clean_key = str(api_key or "").strip()
    if not clean_key:
        return ProviderVerificationResult(
            ok=False,
            message="API key is required.",
            error_code="INVALID_CONFIG",
            latency_ms=0,
        )

    clean_base_url = str(base_url or "").strip()
    if norm_type_str == "openai_compatible" and not clean_base_url:
        return ProviderVerificationResult(
            ok=False,
            message="Base URL is required for OpenAI-compatible providers.",
            error_code="INVALID_CONFIG",
            latency_ms=0,
        )

    if factory is None:
        try:
            from agent.modules.providers import _get_provider_service

            service = _get_provider_service()
            factory = service.get_factory(
                ProviderType(norm_type_str),
                provider_name=provider_name,
                catalog_id=catalog_id,
            )
        except Exception:
            if norm_type_str == "openai_compatible":
                from agent.modules.providers.openai_compatible.factory import OpenAICompatibleFactory

                factory = OpenAICompatibleFactory()
            elif norm_type_str == "google":
                from agent.modules.providers.google.factory import GoogleFactory

                factory = GoogleFactory()
            elif norm_type_str == "anthropic":
                from agent.modules.providers.anthropic.factory import AnthropicFactory

                factory = AnthropicFactory()

    list_models_func = getattr(factory, "list_models", None)
    if not callable(list_models_func):
        return ProviderVerificationResult(
            ok=False,
            message=f"Factory for {norm_type_str} does not support listing models.",
            error_code="INVALID_CONFIG",
            latency_ms=0,
        )

    prov_config = ProviderConfig(
        name=provider_name or "verify_target",
        provider_type=ProviderType(norm_type_str),
        base_url=clean_base_url,
        api_key=clean_key,
        default_model="",
        catalog_id=catalog_id,
    )

    start_time = time.perf_counter()
    try:
        try:
            sig = inspect.signature(list_models_func)
            accepts_timeout = "timeout" in sig.parameters
        except (TypeError, ValueError):
            accepts_timeout = False
        if accepts_timeout:
            coro_or_result = list_models_func(prov_config, clean_key, timeout=timeout)
        else:
            coro_or_result = list_models_func(prov_config, clean_key)
        if inspect.isawaitable(coro_or_result):
            raw_models = await asyncio.wait_for(coro_or_result, timeout=timeout)
        else:
            raw_models = coro_or_result
        latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))
    except httpx.HTTPStatusError as exc:
        latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))
        if exc.response.status_code in (401, 403):
            return ProviderVerificationResult(
                ok=False,
                message="Authentication failed: Invalid API key",
                latency_ms=latency_ms,
                error_code="AUTH_FAILED",
                details={"status_code": exc.response.status_code},
            )
        return ProviderVerificationResult(
            ok=False,
            message=f"HTTP error {exc.response.status_code}: {exc.response.text[:200]}",
            latency_ms=latency_ms,
            error_code="CONNECTION_ERROR",
            details={"status_code": exc.response.status_code},
        )
    except (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError):
        latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))
        return ProviderVerificationResult(
            ok=False,
            message="Connection timed out",
            latency_ms=latency_ms,
            error_code="TIMEOUT",
        )
    except (httpx.ConnectError, httpx.NetworkError) as exc:
        latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))
        return ProviderVerificationResult(
            ok=False,
            message=f"Could not connect to base URL: {exc}",
            latency_ms=latency_ms,
            error_code="CONNECTION_ERROR",
        )
    except Exception as exc:
        latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))
        exc_str = str(exc).lower()
        if "package is not installed" in exc_str:
            return ProviderVerificationResult(
                ok=False,
                message=f"Server configuration error: {exc}",
                latency_ms=latency_ms,
                error_code="INVALID_CONFIG",
            )
        status = getattr(exc, "status_code", getattr(exc, "code", None))
        try:
            status_int = int(status) if status is not None else None
        except (TypeError, ValueError):
            status_int = None
        if status_int in (401, 403) or any(
            phrase in exc_str
            for phrase in (
                "unauthorized",
                "forbidden",
                "invalid api key",
                "invalid_api_key",
                "invalid key",
                "incorrect api key",
                "authentication failed",
                "authentication error",
                "invalid credentials",
                "invalid token",
                "expired token",
                "permission denied",
                "permission_denied",
                "access denied",
                "api key not valid",
                "missing api key",
            )
        ):
            return ProviderVerificationResult(
                ok=False,
                message=f"Authentication failed: {exc}",
                latency_ms=latency_ms,
                error_code="AUTH_FAILED",
            )
        if "timeout" in exc_str or "timed out" in exc_str:
            return ProviderVerificationResult(
                ok=False,
                message="Connection timed out",
                latency_ms=latency_ms,
                error_code="TIMEOUT",
            )
        if any(
            phrase in exc_str
            for phrase in (
                "connection error",
                "connection failed",
                "could not connect",
                "failed to connect",
                "connection refused",
                "connection reset",
                "connecterror",
                "connect error",
                "name resolution",
                "getaddrinfo failed",
                "nodename nor servname",
                "network is unreachable",
                "network unreachable",
                "no route to host",
                "dns error",
            )
        ):
            return ProviderVerificationResult(
                ok=False,
                message=f"Connection error: {exc}",
                latency_ms=latency_ms,
                error_code="CONNECTION_ERROR",
            )
        return ProviderVerificationResult(
            ok=False,
            message=f"Verification failed: {exc}",
            latency_ms=latency_ms,
            error_code="CONNECTION_ERROR",
        )

    # Process and deduplicate models preserving discovery order
    seen = set()
    discovered_models: list[str] = []
    for item in raw_models or []:
        m_id = str(item).strip()
        if m_id and m_id not in seen:
            seen.add(m_id)
            discovered_models.append(m_id)

    # Determine catalog default model if known.
    # Only use catalog_id here: provider_name is user-chosen
    # (e.g. "my-openai") and may coincidentally match a catalog key,
    # which would select the wrong catalog_default.
    catalog_default = ""
    if catalog_id:
        entry = get_provider_catalog_entry(catalog_id)
        if entry and entry.default_model:
            catalog_default = entry.default_model

    suggested_default = resolve_suggested_default_model(discovered_models, catalog_default)

    if not discovered_models:
        return ProviderVerificationResult(
            ok=False,
            message="Connection succeeded but no models were discovered. Check API key permissions or provider configuration.",
            latency_ms=latency_ms,
            models=[],
            suggested_default_model="",
            error_code="NO_MODELS",
            details={"model_count": 0},
        )

    return ProviderVerificationResult(
        ok=True,
        message="Connection established successfully",
        latency_ms=latency_ms,
        models=discovered_models,
        suggested_default_model=suggested_default,
        error_code=None,
        details={"model_count": len(discovered_models)},
    )
