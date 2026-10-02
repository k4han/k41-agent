from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from agent.shared.config import get_config_service
from agent.shared.infrastructure.validation import is_placeholder_value

logger = logging.getLogger(__name__)

ZALO_API_BASE = "https://bot-api.zaloplatforms.com"
ZALO_UPDATE_MODE_POLLING = "polling"
ZALO_UPDATE_MODE_WEBHOOK = "webhook"


@dataclass(frozen=True, slots=True)
class ZaloWebhookRuntime:
    token: str
    secret: str
    webhook_url: str


def get_zalo_webhook_runtime(container=None) -> ZaloWebhookRuntime | None:
    """Return container-scoped zalo webhook runtime."""
    from agent.bootstrap.container import require_active_container

    return require_active_container(container)._zalo_webhook_runtime


def set_zalo_webhook_runtime(runtime: ZaloWebhookRuntime | None, container=None) -> None:
    """Store zalo webhook runtime in container scope."""
    from agent.bootstrap.container import require_active_container

    require_active_container(container)._zalo_webhook_runtime = runtime


def _resolve_update_mode(value: str) -> str:
    mode = (value or ZALO_UPDATE_MODE_POLLING).strip().lower()
    if mode not in {ZALO_UPDATE_MODE_POLLING, ZALO_UPDATE_MODE_WEBHOOK}:
        raise ValueError("Invalid channels.zalo.update_mode. Use 'polling' or 'webhook'.")
    return mode


def _api_url(token: str, method: str) -> str:
    return f"{ZALO_API_BASE}/bot{token}/{method}"


async def _set_webhook(token: str, webhook_url: str, webhook_secret: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _api_url(token, "setWebhook"),
            json={"url": webhook_url, "secret_token": webhook_secret},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"setWebhook failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"setWebhook error: {data}")


async def _delete_webhook(token: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        with contextlib.suppress(Exception):
            await client.post(_api_url(token, "deleteWebhook"), json={})


async def _run_polling_bot(token: str) -> None:
    logger.info("Zalo bot starting in polling mode...")
    # Ensure webhook is removed so getUpdates works
    await _delete_webhook(token)
    async with httpx.AsyncClient(timeout=40) as client:
        while True:
            try:
                resp = await client.post(
                    _api_url(token, "getUpdates"),
                    json={"timeout": "30"},
                )
            except asyncio.CancelledError:
                raise
            except httpx.TimeoutException:
                logger.warning("Zalo getUpdates timed out, retrying...")
                await asyncio.sleep(2)
                continue
            except httpx.HTTPError as exc:
                logger.warning("Zalo getUpdates HTTP error: %s", exc)
                await asyncio.sleep(5)
                continue
            except Exception as exc:
                logger.exception("Zalo polling unexpected error: %s", exc)
                await asyncio.sleep(5)
                continue

            if resp.status_code != 200:
                if resp.status_code == 408:
                    logger.debug(
                        "Zalo getUpdates HTTP 408 timeout (no new messages), continuing polling."
                    )
                    await asyncio.sleep(0.2)
                    continue
                logger.warning("Zalo getUpdates failed %s %s", resp.status_code, resp.text)
                await asyncio.sleep(5)
                continue

            try:
                data = resp.json()
            except ValueError:
                logger.warning("Zalo getUpdates returned non-JSON: %s", resp.text)
                await asyncio.sleep(5)
                continue

            if not data.get("ok"):
                error_code = data.get("error_code")
                description = str(data.get("description") or "")
                is_timeout = error_code == 408 or error_code == "408" or "timeout" in description.lower()
                if is_timeout:
                    logger.debug(
                        "Zalo getUpdates long-poll timeout (no new messages), continuing polling."
                    )
                    await asyncio.sleep(0.2)
                    continue
                logger.warning("Zalo getUpdates ok false: %s", data)
                await asyncio.sleep(5)
                continue

            result = data.get("result")
            updates: list[Any] = []
            if isinstance(result, list):
                updates = result
            elif isinstance(result, dict):
                # Some implementations return single object
                updates = [result]
            elif result is None:
                updates = []
            else:
                updates = []

            for update in updates:
                try:
                    # Handle webhook-style wrapper {event_name, message} or {ok,result}
                    payload: dict[str, Any] | None = None
                    if isinstance(update, dict):
                        if "event_name" in update and "message" in update:
                            payload = update
                        elif "result" in update and isinstance(update["result"], dict):
                            inner = update["result"]
                            if isinstance(inner, dict) and "event_name" in inner:
                                payload = inner
                            elif isinstance(inner, list):
                                # rare nested list
                                for inner_item in inner:
                                    if isinstance(inner_item, dict) and "event_name" in inner_item:
                                        from agent.modules.channels.zalo.adapter import handle_zalo_update

                                        await handle_zalo_update(inner_item)
                                continue
                            else:
                                payload = inner if isinstance(inner, dict) else None
                        else:
                            payload = update
                    if payload is not None:
                        from agent.modules.channels.zalo.adapter import handle_zalo_update

                        await handle_zalo_update(payload)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("Failed to handle Zalo update: %s", exc)

            # Small delay to avoid busy loop when no updates
            if not updates:
                await asyncio.sleep(0.5)


async def _run_webhook_bot(token: str, webhook_url: str, webhook_secret: str) -> None:
    logger.info("Zalo bot starting in webhook mode...")
    try:
        await _set_webhook(token, webhook_url, webhook_secret)
        set_zalo_webhook_runtime(
            ZaloWebhookRuntime(token=token, secret=webhook_secret, webhook_url=webhook_url)
        )
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        logger.info("Zalo webhook runtime cancelled.")
        raise
    finally:
        set_zalo_webhook_runtime(None)
        with contextlib.suppress(Exception):
            await _delete_webhook(token)


async def run_zalo_bot() -> None:
    config = get_config_service()
    token = config.get_str("channels.zalo.bot_token", "")
    if is_placeholder_value(token):
        raise ValueError(
            "Zalo bot token not configured. "
            "Set 'channels.zalo.bot_token' in the dashboard channel settings."
        )

    update_mode = _resolve_update_mode(
        config.get_str("channels.zalo.update_mode", ZALO_UPDATE_MODE_POLLING)
    )
    webhook_url = ""
    webhook_secret = ""
    if update_mode == ZALO_UPDATE_MODE_WEBHOOK:
        webhook_url = config.get_str("channels.zalo.webhook_url", "")
        webhook_secret = config.get_str("channels.zalo.webhook_secret", "")
        if is_placeholder_value(webhook_url):
            raise ValueError(
                "Zalo webhook URL not configured. "
                "Set 'channels.zalo.webhook_url' in the dashboard channel settings."
            )
        if is_placeholder_value(webhook_secret):
            raise ValueError(
                "Zalo webhook secret not configured. "
                "Set 'channels.zalo.webhook_secret' in the dashboard channel settings."
            )

    if update_mode == ZALO_UPDATE_MODE_POLLING:
        await _run_polling_bot(token)
        return

    await _run_webhook_bot(token, webhook_url=webhook_url, webhook_secret=webhook_secret)
