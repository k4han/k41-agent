"""Normalize structured provider errors for model pool availability decisions."""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx


@dataclass(frozen=True)
class PoolFailure:
    code: str
    retryable: bool = False
    retry_after: float | None = None


def _duration(value: Any, *, now: float) -> float | None:
    try:
        result = float(value)
        return max(0, result) if math.isfinite(result) else None
    except (TypeError, ValueError):
        try:
            result = parsedate_to_datetime(str(value)).timestamp() - now
            return max(0, result) if math.isfinite(result) else None
        except (ValueError, TypeError, OverflowError):
            return None


def _reset_delay(value: Any, now: float) -> float | None:
    try:
        timestamp = float(value)
    except (ValueError, TypeError):
        try:
            timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, timestamp - now) if math.isfinite(timestamp) else None


def _interval(value: Any) -> float | None:
    matches = re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", str(value))
    if not matches:
        return None
    return sum(float(amount) * {"ms": .001, "s": 1, "m": 60, "h": 3600}[unit] for amount, unit in matches)


def classify_pool_failure(error: BaseException) -> PoolFailure | None:
    """Inspect exception chains without treating arbitrary application errors as outages."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, "response", None)
        status = next((value for value in (getattr(current, "status_code", None),
                      getattr(current, "code", None), getattr(current, "http_status", None),
                      getattr(response, "status_code", None)) if isinstance(value, int)), None)
        body = getattr(current, "body", None)
        if body is None:
            body = getattr(current, "details", None)
        if body is None and isinstance(current, ValueError) and current.args:
            # LangChain preserves some API errors as the argument to ValueError.
            value = current.args[0]
            if isinstance(value, dict):
                body = value
            elif type(value).__name__ == "ResponseError" and hasattr(value, "model_dump"):
                body = value.model_dump()
            elif isinstance(value, str):
                match = re.match(r"^ResponseError\(code=['\"]([a-z0-9_]+)['\"], message=", value)
                if match:
                    body = {"code": match[1]}
        if body is None and response is not None:
            try:
                body = response.json()
            except Exception:
                pass
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                body = {}
        body = body if isinstance(body, dict) else {}
        payload = body.get("error", body)
        payload = payload if isinstance(payload, dict) else body
        codes = {str(payload.get(key, "")).lower() for key in ("code", "type", "status")}
        codes.add(str(getattr(current, "code", "")).lower())
        headers = getattr(response, "headers", None) or getattr(current, "headers", {}) or {}
        headers = {str(k).lower(): v for k, v in headers.items()}
        now = time.time()
        retry_after = _duration(headers.get("retry-after"), now=now)
        if retry_after is None:
            reset = payload.get("resets_at") or payload.get("reset_at") or body.get("resets_at")
            if reset is not None:
                retry_after = _reset_delay(reset, now)
        if retry_after is None:
            intervals = [_interval(headers.get(key)) for key in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens")]
            retry_after = max((value for value in intervals if value is not None), default=None)
        if retry_after is None:
            for detail in payload.get("details", []) if isinstance(payload.get("details"), list) else []:
                if isinstance(detail, dict) and detail.get("@type", "").endswith("RetryInfo"):
                    retry_after = _interval(detail.get("retryDelay"))
        if codes & {"content_filter", "content_policy_violation", "safety",
                    "context_length_exceeded", "invalid_argument"}:
            return None
        quota_codes = {"insufficient_quota", "quota_exceeded", "billing_hard_limit_reached", "credit_balance_too_low",
                       "insufficient_credits", "insufficient_balance", "usage_limit_reached",
                       "subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable"}
        message = str(payload.get("message", "")).lower()
        depleted_credit = status in (400, 429) and any(phrase in message for phrase in (
            "credit balance is too low", "insufficient credit balance", "insufficient balance",
        ))
        if status == 402 or codes & quota_codes or depleted_credit:
            return PoolFailure("quota", retry_after=retry_after)
        name = type(current).__name__.lower()
        if status in (401, 403) or codes & {"invalid_api_key", "authentication_error", "permission_error"} or "authentication" in name or "permissiondenied" in name or name == "chatgptautherror":
            return PoolFailure("auth", retry_after=retry_after)
        if "model_not_found" in codes or status == 404 and ("notfound" in name or "model" in str(payload.get("message", "")).lower()):
            return PoolFailure("model_not_found", retry_after=retry_after)
        if "invalid_request_error" in codes and not (
            status in (408, 429, 504) or status is not None and status >= 500
            or codes & {"resource_exhausted", "rate_limit_exceeded", "rate_limit_error", "server_error", "internal_server_error", "overloaded_error"}
        ):
            return None
        if status == 429 or "ratelimit" in name or codes & {"resource_exhausted", "rate_limit_exceeded", "rate_limit_error"}:
            return PoolFailure("rate_limit", True, retry_after)
        if isinstance(current, TimeoutError) or status in (408, 504) or "timeout" in name:
            return PoolFailure("timeout", True, retry_after)
        if isinstance(current, (ConnectionError, httpx.NetworkError, httpx.RemoteProtocolError, httpx.ProxyError)) or "connection" in name:
            return PoolFailure("connection", True, retry_after)
        if status is not None and 500 <= status < 600 or codes & {"server_error", "internal_server_error", "overloaded_error"}:
            return PoolFailure("upstream", True, retry_after)
        current = current.__cause__ or current.__context__
    return None
