"""Classify agent/provider runtime errors into user-facing messages."""

from __future__ import annotations

import asyncio
import ast
import json
import re
from dataclasses import dataclass

ERROR_CODE_RATE_LIMIT = "rate_limit"
ERROR_CODE_TIMEOUT = "timeout"
ERROR_CODE_AUTH = "auth"
ERROR_CODE_CONNECTION = "connection"
ERROR_CODE_UPSTREAM = "upstream"
ERROR_CODE_UNKNOWN = "unknown"

_ERROR_MESSAGES: dict[str, str] = {
    ERROR_CODE_RATE_LIMIT: (
        "The AI provider is rate limiting requests right now. "
        "Please wait a moment and try again."
    ),
    ERROR_CODE_TIMEOUT: (
        "The AI provider took too long to respond. Please try again."
    ),
    ERROR_CODE_AUTH: (
        "Authentication with the AI provider failed. "
        "Please check the API key in settings."
    ),
    ERROR_CODE_CONNECTION: (
        "Could not connect to the AI provider. "
        "Please check your network or the provider status and try again."
    ),
    ERROR_CODE_UPSTREAM: (
        "The AI provider returned an error. Please try again shortly."
    ),
    ERROR_CODE_UNKNOWN: (
        "Something went wrong while generating a response. Please try again."
    ),
    "recursion_limit_reached": "The agent reached its step limit. Continue to keep working on this task.",
    "execution_incomplete": "The previous response stopped before finishing. Continue from the last saved step.",
}


def _error_message_text(value: object, depth: int = 0) -> str | None:
    """Extract a provider's message field from JSON, Python repr, or an exception wrapper."""
    if depth > 5:
        return None
    if isinstance(value, dict):
        for key in ("message", "error", "detail", "body"):
            nested = value.get(key)
            if isinstance(nested, str):
                return _error_message_text(nested, depth + 1) or nested.strip() or None
            message = _error_message_text(nested, depth + 1)
            if message:
                return message
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])
    wrapper = re.fullmatch(r"[\w.]+(?:Error|Exception|ExceptionGroup)\((.*)\)", text, re.DOTALL)
    if wrapper:
        candidates.append(wrapper.group(1))
    for candidate in candidates:
        for parse in (json.loads, ast.literal_eval):
            try:
                parsed = parse(candidate)
            except (ValueError, SyntaxError, TypeError, RecursionError):
                continue
            if isinstance(parsed, dict) or isinstance(parsed, str) and parsed != text:
                message = _error_message_text(parsed, depth + 1)
                if message:
                    return message
    return None


def friendly_agent_error_message(value: object, *, code: str | None = None) -> str:
    """Return one readable message while keeping serialized diagnostics out of the UI."""
    message = _error_message_text(value)
    if code in {"recursion_limit_reached", "execution_incomplete"}:
        return _ERROR_MESSAGES[code]
    text = message or str(value or "").strip()
    wrapper = re.fullmatch(r"([\w.]+(?:Error|Exception|ExceptionGroup))\((.*)\)", text, re.DOTALL)
    if wrapper:
        name = wrapper.group(1).lower()
        for token, error_code in (
            ("timeout", ERROR_CODE_TIMEOUT), ("ratelimit", ERROR_CODE_RATE_LIMIT),
            ("authentication", ERROR_CODE_AUTH), ("permissiondenied", ERROR_CODE_AUTH),
            ("connection", ERROR_CODE_CONNECTION),
        ):
            if token in name:
                return _ERROR_MESSAGES[error_code]
        try:
            detail = ast.literal_eval(wrapper.group(2))
        except (ValueError, SyntaxError, TypeError, RecursionError):
            detail = None
        if isinstance(detail, str):
            text = detail.strip()
        else:
            return _ERROR_MESSAGES.get(code, _ERROR_MESSAGES[ERROR_CODE_UNKNOWN])
    prefixed_error = re.fullmatch(r"[\w.]+(?:Error|Exception|ExceptionGroup):\s*(.*)", text, re.DOTALL)
    if prefixed_error:
        text = prefixed_error.group(1).strip()
    if not text or any(marker in text for marker in ("Traceback (", "Error code:")) or not message and any(marker in text for marker in ("{", "}")):
        return _ERROR_MESSAGES.get(code, _ERROR_MESSAGES[ERROR_CODE_UNKNOWN])
    return next((line.strip() for line in text.splitlines() if line.strip()), _ERROR_MESSAGES[ERROR_CODE_UNKNOWN])


@dataclass(frozen=True, slots=True)
class AgentError:
    """A classified runtime error with a user-facing message."""

    code: str
    message: str


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "code", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if isinstance(response_status, int):
        return response_status
    return None


def _code_from_status(status: int) -> str | None:
    if status == 429:
        return ERROR_CODE_RATE_LIMIT
    if status in (401, 403):
        return ERROR_CODE_AUTH
    if status in (408, 504):
        return ERROR_CODE_TIMEOUT
    if 500 <= status < 600:
        return ERROR_CODE_UPSTREAM
    return None


def _code_from_type(exc: BaseException) -> str | None:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return ERROR_CODE_TIMEOUT

    name = type(exc).__name__.lower()
    if "ratelimit" in name:
        return ERROR_CODE_RATE_LIMIT
    if "timeout" in name:
        return ERROR_CODE_TIMEOUT
    if any(token in name for token in ("authentication", "permissiondenied", "apikey")):
        return ERROR_CODE_AUTH
    if "connection" in name:
        return ERROR_CODE_CONNECTION
    return None


def _classify_single(exc: BaseException) -> str | None:
    code = _code_from_type(exc)
    if code is not None:
        return code
    status = _status_code(exc)
    if status is not None:
        return _code_from_status(status)
    return None


def _find_code(exc: BaseException, seen: set[int]) -> str | None:
    """Search an exception, its chain, and any grouped sub-exceptions."""
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))

        code = _classify_single(current)
        if code is not None:
            return code

        nested = getattr(current, "exceptions", None)
        if isinstance(nested, (list, tuple)):
            for sub in nested:
                if isinstance(sub, BaseException):
                    sub_code = _find_code(sub, seen)
                    if sub_code is not None:
                        return sub_code

        current = current.__cause__ or current.__context__

    return None


def find_exception(
    exc: BaseException,
    target: type[BaseException] | tuple[type[BaseException], ...],
) -> BaseException | None:
    """Return the first exception in the chain/group matching ``target``."""
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))

        if isinstance(current, target):
            return current

        nested = getattr(current, "exceptions", None)
        if isinstance(nested, (list, tuple)):
            stack.extend(sub for sub in nested if isinstance(sub, BaseException))
        if current.__cause__ is not None:
            stack.append(current.__cause__)
        if current.__context__ is not None:
            stack.append(current.__context__)

    return None


def classify_agent_error(exc: BaseException) -> AgentError:
    """Map a provider/runtime exception to a stable code and friendly message.

    Walks the exception chain (``__cause__`` / ``__context__``) and any grouped
    sub-exceptions (``ExceptionGroup``) so wrapped provider errors are still
    recognized.
    """
    from agent.modules.providers import ModelPoolExhausted

    exhausted = find_exception(exc, ModelPoolExhausted)
    if exhausted is not None:
        return AgentError(code="model_pool_exhausted", message=str(exhausted))
    code = _find_code(exc, set())
    if code is None:
        code = ERROR_CODE_UNKNOWN
    return AgentError(code=code, message=_ERROR_MESSAGES[code])
