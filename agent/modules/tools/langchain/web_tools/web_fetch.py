"""Tool to fetch and extract content from a web page as Markdown."""

from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup, SoupStrainer
from langchain_core.tools import tool
from markdownify import markdownify as md

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory
from agent.modules.tools.result import ToolError, ToolErrorCode

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
MAX_RESPONSE_BYTES = 1_000_000
MAX_OUTPUT_LENGTH = 8000
_NOISE_TAGS = ["script", "style", "nav", "footer", "header", "aside", "noscript"]


def _truncate_text(text: str, max_length: int = MAX_OUTPUT_LENGTH) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length] + "\n\n[... truncated]"


def _html_to_markdown(html: str, max_length: int = MAX_OUTPUT_LENGTH) -> str:
    soup = BeautifulSoup(
        html,
        "html.parser",
        parse_only=SoupStrainer(["main", "article", "body"]),
    )

    for tag in soup(_NOISE_TAGS):
        tag.decompose()

    text = md(str(soup), heading_style="ATX", strip=["img"])
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return _truncate_text(text, max_length)


def _read_limited_response(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    bytes_read = 0

    for chunk in response.iter_bytes():
        remaining = MAX_RESPONSE_BYTES - bytes_read
        if remaining <= 0:
            break
        if len(chunk) > remaining:
            chunks.append(chunk[:remaining])
            break
        chunks.append(chunk)
        bytes_read += len(chunk)

    return b"".join(chunks)


@register_tool(
    category=ToolCategory.WEB,
    capabilities=[ToolCapability.NETWORK],
    tags=["fetch", "web"],
)
@tool
def web_fetch(url: str) -> str:
    """Fetch a web page and return its content as Markdown."""
    try:
        with httpx.Client(
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=True,
            headers=DEFAULT_HEADERS,
        ) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                content = _read_limited_response(response)

            content_type = response.headers.get("content-type", "")
            text = content.decode(response.encoding or "utf-8", errors="replace")
            if "text/html" in content_type:
                return _html_to_markdown(text)
            if "application/json" in content_type or "text/" in content_type:
                return _truncate_text(text)
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
