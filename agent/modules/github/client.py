from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import jwt

from agent.modules.github.config import GitHubSettings, get_github_settings

GITHUB_API_URL = "https://api.github.com"
GITHUB_WEB_URL = "https://github.com"
GITHUB_API_VERSION = "2022-11-28"
TOKEN_REFRESH_SKEW_SECONDS = 60


@dataclass(slots=True)
class InstallationToken:
    token: str
    expires_at: datetime

    def is_valid(self) -> bool:
        return datetime.now(timezone.utc) < self.expires_at - timedelta(
            seconds=TOKEN_REFRESH_SKEW_SECONDS
        )


class GitHubAppClient:
    def __init__(self, settings: GitHubSettings | None = None) -> None:
        self.settings = settings or get_github_settings()
        self._tokens: dict[int, InstallationToken] = {}

    def app_jwt(self) -> str:
        private_key = self.settings.resolve_private_key()
        if not private_key:
            raise ValueError("GitHub App private key is not configured.")
        if not self.settings.app_id:
            raise ValueError("GitHub App ID is not configured.")

        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + 540,
            "iss": self.settings.app_id,
        }
        return jwt.encode(payload, private_key, algorithm="RS256")

    async def get_installation_token(self, installation_id: int) -> str:
        cached = self._tokens.get(installation_id)
        if cached and cached.is_valid():
            return cached.token

        headers = self._headers(self.app_jwt())
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{GITHUB_API_URL}/app/installations/{installation_id}/access_tokens",
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

        expires_at = _parse_github_datetime(data["expires_at"])
        token = InstallationToken(token=str(data["token"]), expires_at=expires_at)
        self._tokens[installation_id] = token
        return token.token

    async def list_installations(self) -> list[dict[str, Any]]:
        return await self._get_all_pages(
            "/app/installations",
            token=self.app_jwt(),
        )

    async def list_installation_repositories(
        self,
        installation_id: int,
    ) -> list[dict[str, Any]]:
        token = await self.get_installation_token(installation_id)
        return await self._get_all_pages(
            "/installation/repositories",
            token=token,
            envelope_key="repositories",
        )

    async def create_issue_comment(
        self,
        *,
        installation_id: int,
        full_name: str,
        issue_number: int,
        body: str,
    ) -> dict[str, Any]:
        token = await self.get_installation_token(installation_id)
        return await self._post_json(
            f"/repos/{full_name}/issues/{issue_number}/comments",
            token=token,
            json={"body": body},
        )

    async def create_pull_request(
        self,
        *,
        installation_id: int,
        full_name: str,
        title: str,
        head: str,
        base: str,
        body: str,
    ) -> dict[str, Any]:
        token = await self.get_installation_token(installation_id)
        return await self._post_json(
            f"/repos/{full_name}/pulls",
            token=token,
            json={
                "title": title,
                "head": head,
                "base": base,
                "body": body,
                "maintainer_can_modify": True,
            },
        )

    async def create_pull_request_review_comment_reply(
        self,
        *,
        installation_id: int,
        full_name: str,
        pull_request_number: int,
        comment_id: int,
        body: str,
    ) -> dict[str, Any]:
        token = await self.get_installation_token(installation_id)
        return await self._post_json(
            f"/repos/{full_name}/pulls/{pull_request_number}/comments/{comment_id}/replies",
            token=token,
            json={"body": body},
        )

    async def _get_all_pages(
        self,
        path: str,
        *,
        token: str,
        envelope_key: str | None = None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                response = await client.get(
                    f"{GITHUB_API_URL}{path}",
                    headers=self._headers(token),
                    params={"per_page": 100, "page": page},
                )
                response.raise_for_status()
                data = response.json()
                page_items = data.get(envelope_key, []) if envelope_key else data
                if not page_items:
                    break
                items.extend(page_items)
                if len(page_items) < 100:
                    break
                page += 1
        return items

    async def _post_json(
        self,
        path: str,
        *,
        token: str,
        json: dict[str, Any],
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{GITHUB_API_URL}{path}",
                headers=self._headers(token),
                json=json,
            )
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "kaka-agent",
        }


def _parse_github_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


__all__ = [
    "GITHUB_API_URL",
    "GITHUB_WEB_URL",
    "GitHubAppClient",
    "InstallationToken",
]
