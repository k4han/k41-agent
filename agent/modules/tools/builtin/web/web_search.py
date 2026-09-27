"""Tool to search the web using Google Custom Search API or DuckDuckGo fallback."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, TypedDict

import httpx
from bs4 import BeautifulSoup
from langchain_core.tools import StructuredTool

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import (
    ToolCapability,
    ToolCategory,
    ToolConfigField,
    ToolConfigSchema,
    ToolConfigValue,
)
from agent.modules.tools.builtin.web.web_fetch import (
    DEFAULT_HEADERS,
    _effective_credential,
    _read_limited_response,
)
from agent.modules.tools.result import ToolError, ToolErrorCode

logger = logging.getLogger(__name__)

GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
FIRECRAWL_SEARCH_URL = "https://api.firecrawl.dev/v2/search"
BING_SEARCH_URL = "https://api.bing.microsoft.com/v7.0/search"
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
DDGS_HTML_URL = "https://html.duckduckgo.com/html/"
DDGS_RESULT_SELECTOR = ".result"
DDGS_TITLE_SELECTOR = ".result__a"
DDGS_SNIPPET_SELECTOR = ".result__snippet"

VALID_WEB_SEARCH_PROVIDERS = ("auto", "google", "duckduckgo", "tavily", "bing", "brave", "firecrawl")

WEB_SEARCH_TOOL_NAME = "web_search"
WEB_SEARCH_TOOL_DESCRIPTION = (
    "Search the web and return a list of results with titles, URLs and snippets."
)

WEB_SEARCH_CONFIG_SCHEMA = ToolConfigSchema(
    fields=(
        ToolConfigField(
            name="provider",
            input_type="select",
            label="Provider",
            description=(
                "Search provider to use. 'auto' tries configured providers in "
                "order (Google, Tavily, Firecrawl, Brave, Bing) and falls back to DuckDuckGo."
            ),
            default="auto",
            options=VALID_WEB_SEARCH_PROVIDERS,
        ),
        ToolConfigField(
            name="google_api_key",
            input_type="password",
            label="Google API Key",
            description=(
                "Google Custom Search API key. Falls back to the "
                "GOOGLE_API_KEY environment variable when empty."
            ),
            default=None,
            secret=True,
            show_when={"provider": ("auto", "google")},
        ),
        ToolConfigField(
            name="google_cse_id",
            input_type="text",
            label="Google CSE ID",
            description=(
                "Google Custom Search Engine ID. Falls back to the "
                "GOOGLE_CSE_ID environment variable when empty."
            ),
            default=None,
            show_when={"provider": ("auto", "google")},
        ),
        ToolConfigField(
            name="tavily_api_key",
            input_type="password",
            label="Tavily API Key",
            description=(
                "Tavily Search API key. Falls back to tools.web_fetch.tavily_api_key "
                "or the TAVILY_API_KEY environment variable when empty."
            ),
            default=None,
            secret=True,
            show_when={"provider": ("auto", "tavily")},
        ),
        ToolConfigField(
            name="bing_api_key",
            input_type="password",
            label="Bing API Key",
            description=(
                "Bing Web Search API subscription key. Falls back to the "
                "BING_API_KEY environment variable when empty."
            ),
            default=None,
            secret=True,
            show_when={"provider": ("auto", "bing")},
        ),
        ToolConfigField(
            name="brave_api_key",
            input_type="password",
            label="Brave API Key",
            description=(
                "Brave Search API key. Falls back to the "
                "BRAVE_API_KEY environment variable when empty."
            ),
            default=None,
            secret=True,
            show_when={"provider": ("auto", "brave")},
        ),
        ToolConfigField(
            name="firecrawl_api_key",
            input_type="password",
            label="Firecrawl API Key",
            description=(
                "Firecrawl API key. Falls back to tools.web_fetch.firecrawl_api_key "
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
                "Falls back to tools.web_fetch.firecrawl_base_url or the "
                "FIRECRAWL_BASE_URL environment variable when empty."
            ),
            default="",
            show_when={"provider": ("auto", "firecrawl")},
        ),
    )
)


class SearchResult(TypedDict):
    title: str
    link: str
    snippet: str


def _format_search_results(results: list[SearchResult]) -> str:
    if not results:
        return "No results found."
    return "\n\n".join(
        f"{i}. {result['title']}\n   {result['link']}\n   {result['snippet']}"
        for i, result in enumerate(results, 1)
    )


def _google_search(
    query: str,
    num_results: int = 5,
    *,
    api_key: str = "",
    cse_id: str = "",
) -> str | None:
    """Search using Google Custom Search JSON API.

    Returns ``None`` only when credentials are missing so callers can fall
    back to another provider. Transport failures raise ``ToolError`` so an
    explicitly selected provider surfaces the outage instead of silently
    switching providers.
    """
    if not api_key or not cse_id:
        return None

    try:
        with httpx.Client(timeout=httpx.Timeout(10, connect=5), headers=DEFAULT_HEADERS) as client:
            response = client.get(
                GOOGLE_SEARCH_URL,
                params={
                    "key": api_key,
                    "cx": cse_id,
                    "q": query,
                    "num": num_results,
                },
            )
            response.raise_for_status()
            data = response.json()

        return _format_search_results(
            [
                {
                    "title": item.get("title", "No title"),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", "No description"),
                }
                for item in data.get("items", [])
            ]
        )
    except httpx.TimeoutException as exc:
        raise ToolError(
            ToolErrorCode.TIMEOUT, "Google search timed out after 10 seconds."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM,
            f"Google search HTTP {exc.response.status_code}: {exc.response.reason_phrase}",
        ) from exc
    except (httpx.RequestError, ValueError) as exc:
        logger.warning("Google search failed: %s", exc)
        raise ToolError(
            ToolErrorCode.UPSTREAM, f"Google search failed: {exc}"
        ) from exc


def _tavily_search(
    query: str,
    num_results: int = 5,
    *,
    api_key: str = "",
) -> str | None:
    """Search using Tavily Search API."""
    if not api_key:
        return None

    try:
        with httpx.Client(timeout=httpx.Timeout(10, connect=5), headers=DEFAULT_HEADERS) as client:
            response = client.post(
                TAVILY_SEARCH_URL,
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": num_results,
                },
            )
            response.raise_for_status()
            data = response.json()

        results = [
            {
                "title": item.get("title", "No title"),
                "link": item.get("url", ""),
                "snippet": item.get("content", "No description"),
            }
            for item in data.get("results", [])
        ]
        return _format_search_results(results)
    except httpx.TimeoutException as exc:
        raise ToolError(
            ToolErrorCode.TIMEOUT, "Tavily search timed out after 10 seconds."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM,
            f"Tavily search HTTP {exc.response.status_code}: {exc.response.reason_phrase}",
        ) from exc
    except (httpx.RequestError, ValueError) as exc:
        logger.warning("Tavily search failed: %s", exc)
        raise ToolError(
            ToolErrorCode.UPSTREAM, f"Tavily search failed: {exc}"
        ) from exc


def _bing_search(
    query: str,
    num_results: int = 5,
    *,
    api_key: str = "",
) -> str | None:
    """Search using Bing Web Search API."""
    if not api_key:
        return None

    headers = {**DEFAULT_HEADERS, "Ocp-Apim-Subscription-Key": api_key}
    try:
        with httpx.Client(timeout=httpx.Timeout(10, connect=5), headers=headers) as client:
            response = client.get(
                BING_SEARCH_URL,
                params={"q": query, "count": num_results},
            )
            response.raise_for_status()
            data = response.json()

        items = data.get("webPages", {}).get("value", [])
        results = [
            {
                "title": item.get("name", "No title"),
                "link": item.get("url", ""),
                "snippet": item.get("snippet", "No description"),
            }
            for item in items
        ]
        return _format_search_results(results)
    except httpx.TimeoutException as exc:
        raise ToolError(
            ToolErrorCode.TIMEOUT, "Bing search timed out after 10 seconds."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM,
            f"Bing search HTTP {exc.response.status_code}: {exc.response.reason_phrase}",
        ) from exc
    except (httpx.RequestError, ValueError) as exc:
        logger.warning("Bing search failed: %s", exc)
        raise ToolError(
            ToolErrorCode.UPSTREAM, f"Bing search failed: {exc}"
        ) from exc


def _brave_search(
    query: str,
    num_results: int = 5,
    *,
    api_key: str = "",
) -> str | None:
    """Search using Brave Web Search API."""
    if not api_key:
        return None

    headers = {
        **DEFAULT_HEADERS,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(10, connect=5), headers=headers) as client:
            response = client.get(
                BRAVE_SEARCH_URL,
                params={"q": query, "count": num_results},
            )
            response.raise_for_status()
            data = response.json()

        items = data.get("web", {}).get("results", [])
        results = [
            {
                "title": item.get("title", "No title"),
                "link": item.get("url", ""),
                "snippet": item.get("description", "No description"),
            }
            for item in items
        ]
        return _format_search_results(results)
    except httpx.TimeoutException as exc:
        raise ToolError(
            ToolErrorCode.TIMEOUT, "Brave search timed out after 10 seconds."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM,
            f"Brave search HTTP {exc.response.status_code}: {exc.response.reason_phrase}",
        ) from exc
    except (httpx.RequestError, ValueError) as exc:
        logger.warning("Brave search failed: %s", exc)
        raise ToolError(
            ToolErrorCode.UPSTREAM, f"Brave search failed: {exc}"
        ) from exc


def _firecrawl_search(
    query: str,
    num_results: int = 5,
    *,
    api_key: str = "",
    base_url: str = "",
) -> str | None:
    """Search using Firecrawl v2 Search API."""
    headers = {**DEFAULT_HEADERS, "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    base = (base_url or "").strip().rstrip("/")
    if not base:
        url = FIRECRAWL_SEARCH_URL
    elif base.endswith("/search"):
        url = base
    else:
        url = f"{base}/search"

    try:
        with httpx.Client(timeout=httpx.Timeout(10, connect=5), headers=headers) as client:
            response = client.post(
                url,
                json={
                    "query": query,
                    "limit": num_results,
                },
            )
            response.raise_for_status()
            data = response.json()

        payload = data.get("data")
        items: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            items = payload.get("web", []) or []
        elif isinstance(payload, list):
            items = payload

        results = [
            {
                "title": item.get("title", "No title") if item.get("title") else "No title",
                "link": item.get("url") or item.get("link", ""),
                "snippet": item.get("description") or item.get("snippet") or "No description",
            }
            for item in items
            if isinstance(item, dict)
        ]
        return _format_search_results(results)
    except httpx.TimeoutException as exc:
        raise ToolError(
            ToolErrorCode.TIMEOUT, "Firecrawl search timed out after 10 seconds."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM,
            f"Firecrawl search HTTP {exc.response.status_code}: {exc.response.reason_phrase}",
        ) from exc
    except (httpx.RequestError, ValueError) as exc:
        logger.warning("Firecrawl search failed: %s", exc)
        raise ToolError(
            ToolErrorCode.UPSTREAM, f"Firecrawl search failed: {exc}"
        ) from exc


def _duckduckgo_search(query: str, num_results: int = 5) -> str:
    """Search using DuckDuckGo HTML endpoint."""
    try:
        with httpx.Client(
            timeout=httpx.Timeout(10, connect=5),
            follow_redirects=True,
            headers=DEFAULT_HEADERS,
        ) as client:
            with client.stream("POST", DDGS_HTML_URL, data={"q": query}) as response:
                response.raise_for_status()
                content = _read_limited_response(response)

        soup = BeautifulSoup(
            content.decode(response.encoding or "utf-8", errors="replace"),
            "html.parser",
        )
        results = []
        for element in soup.select(DDGS_RESULT_SELECTOR):
            title_el = element.select_one(DDGS_TITLE_SELECTOR)
            snippet_el = element.select_one(DDGS_SNIPPET_SELECTOR)
            results.append(
                {
                    "title": title_el.get_text(strip=True) if title_el else "No title",
                    "link": title_el.get("href", "") if title_el else "",
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else "No description",
                }
            )
            if len(results) == num_results:
                break
        return _format_search_results(results)
    except httpx.TimeoutException as exc:
        raise ToolError(
            ToolErrorCode.TIMEOUT, "DuckDuckGo search timed out after 10 seconds."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM,
            f"DuckDuckGo HTTP {exc.response.status_code}: {exc.response.reason_phrase}",
        ) from exc
    except httpx.RequestError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM, f"DuckDuckGo request failed: {exc}"
        ) from exc
    except UnicodeError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM, f"DuckDuckGo response decode failed: {exc}"
        ) from exc


def _clamp_num_results(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 5
    return max(1, min(number, 10))


def _build_web_search_tool(config: dict[str, ToolConfigValue]) -> StructuredTool:
    provider = str(config.get("provider") or "auto").strip().lower() or "auto"
    if provider not in VALID_WEB_SEARCH_PROVIDERS:
        allowed = ", ".join(VALID_WEB_SEARCH_PROVIDERS)
        raise ValueError(
            f"Unknown web_search provider '{provider}'. "
            f"Expected one of: {allowed}."
        )

    configured_google_key = config.get("google_api_key")
    configured_google_cse = config.get("google_cse_id")
    configured_tavily_key = config.get("tavily_api_key")
    configured_bing_key = config.get("bing_api_key")
    configured_brave_key = config.get("brave_api_key")
    configured_firecrawl_key = config.get("firecrawl_api_key")
    configured_firecrawl_base_url = config.get("firecrawl_base_url")

    def _search_sync(query: str, num_results: int = 5) -> str:
        count = _clamp_num_results(num_results)
        if provider == "duckduckgo":
            return _duckduckgo_search(query, count)

        if provider == "google":
            api_key = _effective_credential(configured_google_key, "GOOGLE_API_KEY")
            cse_id = _effective_credential(configured_google_cse, "GOOGLE_CSE_ID")
            if not (api_key and cse_id):
                raise ToolError(
                    ToolErrorCode.INVALID_INPUT,
                    (
                        "Google search is not configured. Set the web_search "
                        "google_api_key and google_cse_id in Dashboard > Settings "
                        "> Tools > web_search (or the GOOGLE_API_KEY / "
                        "GOOGLE_CSE_ID environment variables)."
                    ),
                )
            result = _google_search(query, count, api_key=api_key, cse_id=cse_id)
            if result is not None:
                return result
            return "No results found."

        if provider == "tavily":
            api_key = _effective_credential(
                configured_tavily_key,
                "TAVILY_API_KEY",
                fallback_setting_key="tools.web_fetch.tavily_api_key",
            )
            if not api_key:
                raise ToolError(
                    ToolErrorCode.INVALID_INPUT,
                    (
                        "Tavily search is not configured. Set the web_search "
                        "tavily_api_key in Dashboard > Settings > Tools > web_search "
                        "(or tools.web_fetch.tavily_api_key / the TAVILY_API_KEY "
                        "environment variable)."
                    ),
                )
            result = _tavily_search(query, count, api_key=api_key)
            if result is not None:
                return result
            return "No results found."

        if provider == "bing":
            api_key = _effective_credential(configured_bing_key, "BING_API_KEY")
            if not api_key:
                raise ToolError(
                    ToolErrorCode.INVALID_INPUT,
                    (
                        "Bing search is not configured. Set the web_search "
                        "bing_api_key in Dashboard > Settings > Tools > web_search "
                        "(or the BING_API_KEY environment variable)."
                    ),
                )
            result = _bing_search(query, count, api_key=api_key)
            if result is not None:
                return result
            return "No results found."

        if provider == "brave":
            api_key = _effective_credential(configured_brave_key, "BRAVE_API_KEY")
            if not api_key:
                raise ToolError(
                    ToolErrorCode.INVALID_INPUT,
                    (
                        "Brave search is not configured. Set the web_search "
                        "brave_api_key in Dashboard > Settings > Tools > web_search "
                        "(or the BRAVE_API_KEY environment variable)."
                    ),
                )
            result = _brave_search(query, count, api_key=api_key)
            if result is not None:
                return result
            return "No results found."

        if provider == "firecrawl":
            api_key = _effective_credential(
                configured_firecrawl_key,
                "FIRECRAWL_API_KEY",
                fallback_setting_key="tools.web_fetch.firecrawl_api_key",
            )
            base_url = _effective_credential(
                configured_firecrawl_base_url,
                "FIRECRAWL_BASE_URL",
                fallback_setting_key="tools.web_fetch.firecrawl_base_url",
            )
            if not (api_key or base_url):
                raise ToolError(
                    ToolErrorCode.INVALID_INPUT,
                    (
                        "Firecrawl search is not configured. Set the web_search "
                        "firecrawl_api_key in Dashboard > Settings > Tools > web_search "
                        "(or tools.web_fetch.firecrawl_api_key / the FIRECRAWL_API_KEY "
                        "environment variable). A self-hosted FIRECRAWL_BASE_URL alone "
                        "is also accepted."
                    ),
                )
            result = _firecrawl_search(query, count, api_key=api_key, base_url=base_url)
            if result is not None:
                return result
            return "No results found."

        # provider == "auto": cascade through configured providers, then DuckDuckGo
        google_key = _effective_credential(configured_google_key, "GOOGLE_API_KEY")
        google_cse = _effective_credential(configured_google_cse, "GOOGLE_CSE_ID")
        if google_key and google_cse:
            try:
                res = _google_search(query, count, api_key=google_key, cse_id=google_cse)
                if res is not None:
                    return res
            except ToolError:
                logger.warning(
                    "Google search failed in auto mode, falling back.",
                    exc_info=True,
                )

        tavily_key = _effective_credential(
            configured_tavily_key,
            "TAVILY_API_KEY",
            fallback_setting_key="tools.web_fetch.tavily_api_key",
        )
        if tavily_key:
            try:
                res = _tavily_search(query, count, api_key=tavily_key)
                if res is not None:
                    return res
            except ToolError:
                logger.warning(
                    "Tavily search failed in auto mode, falling back.",
                    exc_info=True,
                )

        firecrawl_key = _effective_credential(
            configured_firecrawl_key,
            "FIRECRAWL_API_KEY",
            fallback_setting_key="tools.web_fetch.firecrawl_api_key",
        )
        firecrawl_base = _effective_credential(
            configured_firecrawl_base_url,
            "FIRECRAWL_BASE_URL",
            fallback_setting_key="tools.web_fetch.firecrawl_base_url",
        )
        if firecrawl_key or firecrawl_base:
            try:
                res = _firecrawl_search(
                    query, count, api_key=firecrawl_key, base_url=firecrawl_base
                )
                if res is not None:
                    return res
            except ToolError:
                logger.warning(
                    "Firecrawl search failed in auto mode, falling back.",
                    exc_info=True,
                )

        brave_key = _effective_credential(configured_brave_key, "BRAVE_API_KEY")
        if brave_key:
            try:
                res = _brave_search(query, count, api_key=brave_key)
                if res is not None:
                    return res
            except ToolError:
                logger.warning(
                    "Brave search failed in auto mode, falling back.",
                    exc_info=True,
                )

        bing_key = _effective_credential(configured_bing_key, "BING_API_KEY")
        if bing_key:
            try:
                res = _bing_search(query, count, api_key=bing_key)
                if res is not None:
                    return res
            except ToolError:
                logger.warning(
                    "Bing search failed in auto mode, falling back.",
                    exc_info=True,
                )

        return _duckduckgo_search(query, count)

    async def _search_async(query: str, num_results: int = 5) -> str:
        return await asyncio.to_thread(_search_sync, query, num_results)

    return StructuredTool.from_function(
        func=_search_sync,
        coroutine=_search_async,
        name=WEB_SEARCH_TOOL_NAME,
        description=WEB_SEARCH_TOOL_DESCRIPTION,
    )


web_search = register_tool(
    category=ToolCategory.WEB,
    capabilities=[ToolCapability.NETWORK],
    tags=["search", "web"],
    config_schema=WEB_SEARCH_CONFIG_SCHEMA,
    default_config=WEB_SEARCH_CONFIG_SCHEMA.defaults(),
    factory=_build_web_search_tool,
)(_build_web_search_tool({}))


__all__ = [
    "WEB_SEARCH_CONFIG_SCHEMA",
    "WEB_SEARCH_TOOL_DESCRIPTION",
    "WEB_SEARCH_TOOL_NAME",
    "web_search",
]
