from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from agent.shared.config import get_config_service
from agent.shared.infrastructure.validation import is_placeholder_value

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org"
DISCORD_API_URL = "https://discord.com/api/v10"
GITHUB_API_URL = "https://api.github.com"
TEST_TIMEOUT_SECONDS = 8.0


@dataclass(slots=True)
class TestResult:
    ok: bool
    message: str
    latency_ms: int | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "message": self.message,
        }
        if self.latency_ms is not None:
            payload["latency_ms"] = self.latency_ms
        if self.details:
            payload["details"] = self.details
        return payload


async def test_channel_connection(name: str) -> TestResult:
    """Verify channel credentials by calling the provider API."""
    normalized = name.strip().lower()
    if normalized == "telegram":
        return await test_telegram_connection()
    if normalized == "discord":
        return await test_discord_connection()
    if normalized == "github":
        return await test_github_connection()
    return TestResult(ok=False, message=f"Channel '{name}' does not support connection testing.")


async def test_telegram_connection() -> TestResult:
    config = get_config_service()
    token = config.get_str("channels.telegram.bot_token", "")
    if is_placeholder_value(token):
        return TestResult(
            ok=False,
            message="Telegram bot token is not configured.",
        )

    url = f"{TELEGRAM_API_URL}/bot{token}/getMe"
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=TEST_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
    except httpx.TimeoutException:
        return TestResult(ok=False, message="Timed out contacting Telegram API.")
    except httpx.HTTPError as exc:
        return TestResult(ok=False, message=f"Telegram request failed: {exc}")

    latency_ms = int((time.perf_counter() - started) * 1000)

    if response.status_code == 401:
        return TestResult(
            ok=False,
            message="Telegram rejected the bot token (401 Unauthorized).",
            latency_ms=latency_ms,
        )

    try:
        payload = response.json()
    except ValueError:
        return TestResult(
            ok=False,
            message=f"Telegram returned non-JSON response (HTTP {response.status_code}).",
            latency_ms=latency_ms,
        )

    if not response.is_success or not payload.get("ok"):
        description = str(payload.get("description") or "Unknown Telegram error")
        return TestResult(
            ok=False,
            message=f"Telegram error: {description}",
            latency_ms=latency_ms,
        )

    result = payload.get("result") or {}
    details = {
        "bot_id": result.get("id"),
        "username": result.get("username"),
        "name": result.get("first_name") or result.get("username"),
        "can_join_groups": result.get("can_join_groups"),
        "can_read_all_group_messages": result.get("can_read_all_group_messages"),
    }
    return TestResult(
        ok=True,
        message=f"Connected as @{result.get('username', 'unknown')}.",
        latency_ms=latency_ms,
        details=details,
    )


async def test_discord_connection() -> TestResult:
    config = get_config_service()
    token = config.get_str("channels.discord.bot_token", "")
    if is_placeholder_value(token):
        return TestResult(
            ok=False,
            message="Discord bot token is not configured.",
        )

    url = f"{DISCORD_API_URL}/users/@me"
    headers = {"Authorization": f"Bot {token}"}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=TEST_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        return TestResult(ok=False, message="Timed out contacting Discord API.")
    except httpx.HTTPError as exc:
        return TestResult(ok=False, message=f"Discord request failed: {exc}")

    latency_ms = int((time.perf_counter() - started) * 1000)

    if response.status_code == 401:
        return TestResult(
            ok=False,
            message="Discord rejected the bot token (401 Unauthorized).",
            latency_ms=latency_ms,
        )

    if not response.is_success:
        try:
            data = response.json()
            description = str(data.get("message") or response.text)
        except ValueError:
            description = response.text or f"HTTP {response.status_code}"
        return TestResult(
            ok=False,
            message=f"Discord error: {description}",
            latency_ms=latency_ms,
        )

    try:
        payload = response.json()
    except ValueError:
        return TestResult(
            ok=False,
            message="Discord returned non-JSON response.",
            latency_ms=latency_ms,
        )

    details = {
        "bot_id": payload.get("id"),
        "username": payload.get("username"),
        "discriminator": payload.get("discriminator"),
        "verified": payload.get("verified"),
    }
    return TestResult(
        ok=True,
        message=f"Connected as {payload.get('username', 'unknown')}.",
        latency_ms=latency_ms,
        details=details,
    )


async def test_github_connection() -> TestResult:
    config = get_config_service()
    app_id = config.get_str("channels.github.app_id", "")
    private_key_inline = config.get_str("channels.github.private_key", "")
    private_key_path = config.get_str("channels.github.private_key_path", "")

    if is_placeholder_value(app_id):
        return TestResult(ok=False, message="GitHub App ID is not configured.")

    private_key = _resolve_github_private_key(private_key_inline, private_key_path)
    if not private_key:
        return TestResult(
            ok=False,
            message="GitHub App private key is not configured.",
        )

    try:
        token = _sign_github_jwt(app_id, private_key)
    except ValueError as exc:
        return TestResult(ok=False, message=str(exc))
    except Exception as exc:  # noqa: BLE001 - PyJWT raises broad types for bad keys
        return TestResult(ok=False, message=f"Failed to sign JWT: {exc}")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=TEST_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{GITHUB_API_URL}/app", headers=headers)
    except httpx.TimeoutException:
        return TestResult(ok=False, message="Timed out contacting GitHub API.")
    except httpx.HTTPError as exc:
        return TestResult(ok=False, message=f"GitHub request failed: {exc}")

    latency_ms = int((time.perf_counter() - started) * 1000)

    if response.status_code == 401:
        return TestResult(
            ok=False,
            message="GitHub rejected the App credentials (401 Unauthorized).",
            latency_ms=latency_ms,
        )

    if not response.is_success:
        try:
            data = response.json()
            description = str(data.get("message") or response.text)
        except ValueError:
            description = response.text or f"HTTP {response.status_code}"
        return TestResult(
            ok=False,
            message=f"GitHub error: {description}",
            latency_ms=latency_ms,
        )

    try:
        payload = response.json()
    except ValueError:
        return TestResult(
            ok=False,
            message="GitHub returned non-JSON response.",
            latency_ms=latency_ms,
        )

    details = {
        "app_id": payload.get("id"),
        "slug": payload.get("slug"),
        "name": payload.get("name"),
        "owner": (payload.get("owner") or {}).get("login"),
        "html_url": payload.get("html_url"),
    }
    return TestResult(
        ok=True,
        message=f"Authenticated as GitHub App '{payload.get('slug', 'unknown')}'.",
        latency_ms=latency_ms,
        details=details,
    )


def _resolve_github_private_key(inline_value: str, path_value: str) -> str:
    if inline_value and not is_placeholder_value(inline_value):
        return inline_value.strip()
    candidate = (path_value or "").strip()
    if not candidate or is_placeholder_value(candidate):
        return ""
    try:
        with open(candidate, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError as exc:
        logger.warning("Unable to read GitHub private key from '%s': %s", candidate, exc)
        return ""


def _sign_github_jwt(app_id: str, private_key: str) -> str:
    cleaned = app_id.strip()
    try:
        int(cleaned)
    except ValueError as exc:
        raise ValueError("GitHub App ID must be numeric.") from exc

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 540,
        "iss": cleaned,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


__all__ = [
    "TestResult",
    "test_channel_connection",
    "test_discord_connection",
    "test_github_connection",
    "test_telegram_connection",
]
