"""Tool to fetch and extract content from a web page as Markdown."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup, SoupStrainer
from langchain_core.tools import StructuredTool
from markdownify import markdownify as md

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import (
    ToolCapability,
    ToolCategory,
    ToolConfigField,
    ToolConfigSchema,
    ToolConfigValue,
)
from agent.modules.tools.result import ToolError, ToolErrorCode
from agent.shared.config.web_connections import ENV_FIELDS, with_web_connections
from agent.modules.tools.runtime.output_policy import MAX_STORED_BYTES, capture_bytes

logger = logging.getLogger(__name__)

_ENV_TO_KIND_FIELD = {env: pair for pair, env in ENV_FIELDS.items()}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
MAX_RESPONSE_BYTES = MAX_STORED_BYTES
_NOISE_TAGS = ["script", "style", "nav", "footer", "header", "aside", "noscript"]

FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"

VALID_WEB_FETCH_PROVIDERS = ("auto", "local", "firecrawl", "tavily")

WEB_FETCH_TOOL_NAME = "web_fetch"
WEB_FETCH_TOOL_DESCRIPTION = "Fetch a web page and return its content as Markdown."

WEB_FETCH_CONFIG_SCHEMA = ToolConfigSchema(
    fields=(
        ToolConfigField(
            name="provider",
            input_type="select",
            label="Provider",
            description=(
                "Fetch provider to use. 'auto' tries configured providers in "
                "order (Firecrawl, Tavily) and falls back to local."
            ),
            default="auto",
            options=VALID_WEB_FETCH_PROVIDERS,
        ),
        ToolConfigField(
            name="firecrawl_api_key",
            input_type="password",
            label="Firecrawl API Key",
            description=(
                "Firecrawl API key. Falls back to tools.web_search.firecrawl_api_key "
                "or the FIRECRAWL_API_KEY environment variable when empty."
            ),
            default=None,
            secret=True,
            show_when={"provider": ("auto", "firecrawl")},
        ),
        ToolConfigField(
            name="firecrawl_base_url",
            input_type="text",
            label="Firecrawl Base URL",
            description=(
                "Optional Firecrawl API base URL (default: https://api.firecrawl.dev/v2). "
                "Leave empty to use the official Firecrawl API. "
                "Falls back to tools.web_search.firecrawl_base_url or the "
                "FIRECRAWL_BASE_URL environment variable when empty."
            ),
            default="",
            show_when={"provider": ("auto", "firecrawl")},
        ),
        ToolConfigField(
            name="tavily_api_key",
            input_type="password",
            label="Tavily API Key",
            description=(
                "Tavily Search API key. Falls back to tools.web_search.tavily_api_key "
                "or the TAVILY_API_KEY environment variable when empty."
            ),
            default=None,
            secret=True,
            show_when={"provider": ("auto", "tavily")},
        ),
    )
)

WEB_FETCH_CONFIG_SCHEMA = with_web_connections(WEB_FETCH_CONFIG_SCHEMA, "web_fetch")


def _effective_credential(
    configured: Any,
    env_name: str,
    *,
    fallback_setting_key: str = "",
) -> str:
    """Resolve a credential from tool config with peer tool and env fallback.

    The closure captures the configured value at materialization time while
    the fallback setting key and environment are read lazily so long-lived
    instances pick up runtime settings and local-dev env changes.
    """
    text = str(configured or "").strip()
    if text:
        return text

    if fallback_setting_key:
        try:
            from agent.shared.config import get_config_service

            val = get_config_service().get(fallback_setting_key)
            val_text = str(val or "").strip()
            if val_text:
                return val_text
        except Exception:
            pass

    return os.environ.get(env_name, "").strip()


def _html_to_markdown(html: str) -> str:
    soup = BeautifulSoup(
        html,
        "html.parser",
        parse_only=SoupStrainer(["main", "article", "body"]),
    )

    for tag in soup(_NOISE_TAGS):
        tag.decompose()

    text = md(str(soup), heading_style="ATX", strip=["img"])
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _read_limited_response(response: httpx.Response) -> bytes:
    return capture_bytes(response.iter_bytes(), MAX_RESPONSE_BYTES)[0]


def _resolve_firecrawl_scrape_url(base_url: str = "") -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return FIRECRAWL_SCRAPE_URL
    if base.endswith("/scrape"):
        return base
    return f"{base}/scrape"


def _local_fetch(url: str) -> str:
    """Fetch a web page directly via HTTP and return its content as Markdown."""
    try:
        with httpx.Client(
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=True,
            headers=DEFAULT_HEADERS,
        ) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                content, capture_truncated = capture_bytes(response.iter_bytes(), MAX_RESPONSE_BYTES)

            content_type = response.headers.get("content-type", "")
            text = content.decode(response.encoding or "utf-8", errors="replace")
            notice = "\n\n[source download limit reached; some content may be missing]" if capture_truncated else ""
            if "text/html" in content_type:
                return _html_to_markdown(text) + notice
            if "application/json" in content_type or "text/" in content_type:
                return text + notice
            return f"[Info] Non-text content type: {content_type}. Response size: {len(content)} bytes."
    except httpx.TimeoutException as exc:
        raise ToolError(
            ToolErrorCode.TIMEOUT, "Request timed out after 30 seconds."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM,
            f"HTTP {exc.response.status_code}: {exc.response.reason_phrase}",
        ) from exc
    except httpx.RequestError as exc:
        raise ToolError(ToolErrorCode.UPSTREAM, f"Request failed: {exc}") from exc
    except UnicodeError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM, f"Failed to decode response: {exc}"
        ) from exc


def _firecrawl_fetch(
    url: str,
    *,
    api_key: str = "",
    base_url: str = "",
) -> str | None:
    """Fetch webpage markdown using Firecrawl Scrape API."""
    headers = {**DEFAULT_HEADERS, "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    scrape_url = _resolve_firecrawl_scrape_url(base_url)

    try:
        with httpx.Client(timeout=httpx.Timeout(30, connect=10), headers=headers) as client:
            response = client.post(
                scrape_url,
                json={
                    "url": url,
                    "formats": ["markdown"],
                },
            )
            response.raise_for_status()
            data = response.json()

        if data.get("success") is False:
            error_msg = data.get("error") or "Firecrawl failed to scrape URL."
            raise ToolError(
                ToolErrorCode.UPSTREAM,
                f"Firecrawl scrape failed: {error_msg}",
            )

        payload = data.get("data")
        markdown = ""
        if isinstance(payload, dict):
            metadata = payload.get("metadata") or {}
            status_code = metadata.get("statusCode")
            error = metadata.get("error")
            markdown = payload.get("markdown") or ""
            if not markdown and status_code and status_code >= 400:
                raise ToolError(
                    ToolErrorCode.UPSTREAM,
                    f"Firecrawl scrape HTTP {status_code}: {error or 'Page error'}",
                )
        elif isinstance(payload, str):
            markdown = payload

        if not markdown:
            markdown = data.get("markdown") or ""

        if markdown:
            return markdown.strip()
        return "No content found."

    except httpx.TimeoutException as exc:
        raise ToolError(
            ToolErrorCode.TIMEOUT, "Firecrawl fetch timed out after 30 seconds."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM,
            f"Firecrawl fetch HTTP {exc.response.status_code}: {exc.response.reason_phrase}",
        ) from exc
    except (httpx.RequestError, ValueError) as exc:
        logger.warning("Firecrawl fetch failed: %s", exc)
        raise ToolError(
            ToolErrorCode.UPSTREAM, f"Firecrawl fetch failed: {exc}"
        ) from exc


def _tavily_fetch(
    url: str,
    *,
    api_key: str = "",
) -> str | None:
    """Extract webpage content using Tavily Extract API."""
    if not api_key:
        return None

    headers = {
        **DEFAULT_HEADERS,
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(30, connect=10), headers=headers) as client:
            response = client.post(
                TAVILY_EXTRACT_URL,
                json={
                    "api_key": api_key,
                    "urls": [url],
                    "extract_depth": "basic",
                    "format": "markdown",
                },
            )
            response.raise_for_status()
            data = response.json()

        results = data.get("results") or []
        if results and isinstance(results, list):
            first = results[0]
            if isinstance(first, dict):
                raw_content = first.get("raw_content") or ""
                if raw_content:
                    return raw_content.strip()

        failed = data.get("failed_results") or []
        if failed and isinstance(failed, list):
            err_info = failed[0]
            err_msg = err_info.get("error") if isinstance(err_info, dict) else str(err_info)
            raise ToolError(
                ToolErrorCode.UPSTREAM,
                f"Tavily extract failed: {err_msg}",
            )
        return "No content found."
    except httpx.TimeoutException as exc:
        raise ToolError(
            ToolErrorCode.TIMEOUT, "Tavily extract timed out after 30 seconds."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM,
            f"Tavily extract HTTP {exc.response.status_code}: {exc.response.reason_phrase}",
        ) from exc
    except (httpx.RequestError, ValueError) as exc:
        logger.warning("Tavily extract failed: %s", exc)
        raise ToolError(
            ToolErrorCode.UPSTREAM, f"Tavily extract failed: {exc}"
        ) from exc


def _build_web_fetch_tool(config: dict[str, ToolConfigValue]) -> StructuredTool:
    provider = str(config.get("provider") or "auto").strip().lower() or "auto"
    if provider not in VALID_WEB_FETCH_PROVIDERS:
        allowed = ", ".join(VALID_WEB_FETCH_PROVIDERS)
        raise ValueError(
            f"Unknown web_fetch provider '{provider}'. "
            f"Expected one of: {allowed}."
        )

    def credential(env_name: str, _service: Any | None = None) -> str:
        from agent.shared.config import get_config_service
        from agent.shared.config.web_connections import resolve_web_field

        kind, field = _ENV_TO_KIND_FIELD[env_name]
        service = _service if _service is not None else get_config_service()
        try:
            return resolve_web_field(service, "web_fetch", kind, field, config)[0]
        except ValueError as exc:
            if provider == "auto":
                logger.warning("Ignoring invalid web_fetch connection in auto mode: %s", exc)
                return ""
            raise ToolError(ToolErrorCode.INVALID_INPUT, f"Invalid web_fetch connection: {exc}.") from exc


    def _fetch_sync(url: str) -> str:
        if provider == "local":
            return _local_fetch(url)

        if provider == "firecrawl":
            api_key = credential("FIRECRAWL_API_KEY")
            base_url = credential("FIRECRAWL_BASE_URL")
            if not (api_key or base_url):
                raise ToolError(
                    ToolErrorCode.INVALID_INPUT,
                    ("Firecrawl fetch is not configured. Configure a Firecrawl connection in Settings > Providers > Search & Web, or set FIRECRAWL_API_KEY or FIRECRAWL_BASE_URL."),
                )
            result = _firecrawl_fetch(url, api_key=api_key, base_url=base_url)
            if result is not None:
                return result
            return "No content found."

        if provider == "tavily":
            api_key = credential("TAVILY_API_KEY")
            if not api_key:
                raise ToolError(
                    ToolErrorCode.INVALID_INPUT,
                    ("Tavily extract is not configured. Configure a Tavily connection in Settings > Providers > Search & Web, or set TAVILY_API_KEY."),
                )
            result = _tavily_fetch(url, api_key=api_key)
            if result is not None:
                return result
            return "No content found."

        # provider == "auto": cascade through configured providers, then local
        from agent.shared.config import get_config_service as _get_service
        _auto_service = _get_service()

        def _auto_credential(env_name: str) -> str:
            return credential(env_name, _auto_service)

        firecrawl_key = _auto_credential("FIRECRAWL_API_KEY")
        firecrawl_base = _auto_credential("FIRECRAWL_BASE_URL")
        if firecrawl_key or firecrawl_base:
            try:
                res = _firecrawl_fetch(url, api_key=firecrawl_key, base_url=firecrawl_base)
                if res is not None:
                    return res
            except ToolError:
                logger.warning(
                    "Firecrawl fetch failed in auto mode, falling back.",
                    exc_info=True,
                )

        tavily_key = _auto_credential("TAVILY_API_KEY")
        if tavily_key:
            try:
                res = _tavily_fetch(url, api_key=tavily_key)
                if res is not None:
                    return res
            except ToolError:
                logger.warning(
                    "Tavily extract failed in auto mode, falling back.",
                    exc_info=True,
                )

        return _local_fetch(url)

    async def _fetch_async(url: str) -> str:
        return await asyncio.to_thread(_fetch_sync, url)

    return StructuredTool.from_function(
        func=_fetch_sync,
        coroutine=_fetch_async,
        name=WEB_FETCH_TOOL_NAME,
        description=WEB_FETCH_TOOL_DESCRIPTION,
    )


web_fetch = register_tool(
    category=ToolCategory.WEB,
    capabilities=[ToolCapability.NETWORK],
    tags=["fetch", "web"],
    config_schema=WEB_FETCH_CONFIG_SCHEMA,
    default_config=WEB_FETCH_CONFIG_SCHEMA.defaults(),
    factory=_build_web_fetch_tool,
)(_build_web_fetch_tool({}))


__all__ = [
    "DEFAULT_HEADERS",
    "VALID_WEB_FETCH_PROVIDERS",
    "WEB_FETCH_CONFIG_SCHEMA",
    "WEB_FETCH_TOOL_DESCRIPTION",
    "WEB_FETCH_TOOL_NAME",
    "_effective_credential",
    "_read_limited_response",
    "web_fetch",
]
