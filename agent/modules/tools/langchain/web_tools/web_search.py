"""Tool to search the web using Google Custom Search API or DuckDuckGo fallback."""

from __future__ import annotations

import logging
import os
from typing import TypedDict

import httpx
from bs4 import BeautifulSoup, SoupStrainer
from langchain_core.tools import tool

from agent.modules.tools.langchain.web_tools.web_fetch import DEFAULT_HEADERS, MAX_RESPONSE_BYTES

logger = logging.getLogger(__name__)

GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"
DDGS_HTML_URL = "https://html.duckduckgo.com/html/"
DDGS_RESULT_SELECTOR = ".result"
DDGS_TITLE_SELECTOR = ".result__a"
DDGS_SNIPPET_SELECTOR = ".result__snippet"


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


def _google_search(query: str, num_results: int = 5) -> str | None:
    """Search using Google Custom Search JSON API."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    cse_id = os.environ.get("GOOGLE_CSE_ID")
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
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as e:
        logger.warning("Google search failed: %s", e)
        return None


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
                content = response.read(MAX_RESPONSE_BYTES)

        soup = BeautifulSoup(
            content.decode(response.encoding or "utf-8", errors="replace"),
            "html.parser",
            parse_only=SoupStrainer(class_="result"),
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
    except httpx.TimeoutException:
        return "[Error] DuckDuckGo search timed out after 10 seconds."
    except httpx.HTTPStatusError as e:
        return f"[Error] DuckDuckGo HTTP {e.response.status_code}: {e.response.reason_phrase}"
    except httpx.RequestError as e:
        return f"[Error] DuckDuckGo request failed: {e}"
    except UnicodeError as e:
        return f"[Error] DuckDuckGo response decode failed: {e}"


@tool
def web_search(query: str, num_results: int = 5) -> str:
    """Search the web and return a list of results with titles, URLs and snippets."""
    num_results = max(1, min(num_results, 10))
    result = _google_search(query, num_results)
    if result is not None:
        return result
    return _duckduckgo_search(query, num_results)
