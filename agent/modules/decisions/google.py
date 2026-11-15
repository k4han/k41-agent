"""Asynchronous REST client for the Gemini Decisions API."""

from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
import json
import logging
import math
import random
import re
import time
import unicodedata
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from agent.modules.decisions.models import (
    AnswerUnion,
    BaseQuestion,
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionAPIError,
    DecisionAuthenticationError,
    DecisionClientError,
    DecisionRateLimitError,
    DecisionResult,
    DecisionTimeoutError,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
)
from agent.modules.decisions.ports import DecisionClient

logger = logging.getLogger(__name__)


def _serialize_questions(questions: dict[str, BaseQuestion]) -> dict[str, dict[str, Any]]:
    """Validate Google-specific limits without restricting other providers."""
    if not 1 <= len(questions) <= 1000:
        raise DecisionClientError("Google Decisions requires 1 to 1,000 questions.")
    serialized = {}
    for key, question in questions.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key):
            raise DecisionClientError("Google question keys must match [A-Za-z0-9_-]{1,64}.")
        if not isinstance(question, (NoulQuestion, ChoiceQuestion, ScoreQuestion)):
            raise DecisionClientError(f"Unsupported Google question type for '{key}'.")
        if not question.instructions.strip():
            raise DecisionClientError(f"Question '{key}' requires non-empty instructions.")
        payload = {"type": question.type, "instructions": question.instructions}
        if isinstance(question, (ChoiceQuestion, ScoreQuestion)):
            if not 2 <= len(question.criteria) <= 255:
                raise DecisionClientError(f"Question '{key}' requires 2 to 255 criteria.")
            if isinstance(question, ChoiceQuestion):
                for option in question.criteria:
                    if (
                        not 1 <= len(option) <= 128
                        or len(option.encode("utf-8")) > 512
                        or option != option.strip()
                        or any(unicodedata.category(char) == "Cc" or (char.isspace() and char != " ") for char in option)
                    ):
                        raise DecisionClientError(f"Invalid Google choice option key in question '{key}'.")
            elif any(not level.strip() for level in question.criteria):
                raise DecisionClientError(f"Score question '{key}' requires non-empty criteria.")
            payload["criteria"] = question.criteria
        # Noul thresholds are application-side settings, never API fields.
        serialized[key] = payload
    return serialized


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    """Honor numeric and HTTP-date Retry-After values with bounded waits."""
    if response is not None and (header := response.headers.get("Retry-After")):
        try:
            delay = float(header)
        except ValueError:
            try:
                delay = parsedate_to_datetime(header).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                delay = float("nan")
        if math.isfinite(delay) and delay >= 0:
            return delay
    return min(0.5 * (2 ** min(attempt, 4)), 5.0) + random.uniform(0.05, 0.2)


class GoogleDecisionClient(DecisionClient):
    """Evaluate typed questions using Gemini Decision 1 with bounded retries."""

    provider_type = "google"
    DEFAULT_MODEL = "gemini-decision-1"
    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(
        self,
        api_key: str,
        *,
        default_model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 5.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise DecisionClientError("Google API key must be provided.")
        url = urlsplit(base_url.strip())
        if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise DecisionClientError("Google base URL must be an HTTP(S) URL without credentials, query, or fragment.")
        if not default_model.strip():
            raise DecisionClientError("Google decision model must be provided.")
        if not math.isfinite(timeout) or timeout <= 0:
            raise DecisionClientError("Google decision timeout must be a positive finite number.")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise DecisionClientError("Google decision max_retries must be a non-negative integer.")

        self.api_key = api_key.strip()
        self.default_model = default_model.strip()
        self.base_url = base_url.strip().rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._external_client = client is not None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(3.0, timeout)),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
        )

    async def evaluate(
        self,
        state: str | dict[str, Any],
        questions: dict[str, BaseQuestion],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> DecisionResult:
        """Send text or serialized JSON context to the Decisions endpoint."""
        serialized = _serialize_questions(questions)
        if not isinstance(state, (str, dict)):
            raise DecisionClientError("Google decision input must be text or a JSON object.")
        try:
            input_text = json.dumps(state, ensure_ascii=False, allow_nan=False) if isinstance(state, dict) else state
        except (TypeError, ValueError, RecursionError) as exc:
            raise DecisionClientError("Google decision state must be JSON serializable.") from exc
        target_model = (model or self.default_model).strip()
        if not target_model:
            raise DecisionClientError("Google decision model must be provided.")
        payload = {"model": target_model, "input": input_text, "questions": serialized}
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json", "Accept": "application/json"}
        started = time.perf_counter()

        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.post(
                    f"{self.base_url}/decisions", json=payload, headers=headers, timeout=self.timeout,
                )
            except httpx.TimeoutException as exc:
                if attempt == self.max_retries:
                    raise DecisionTimeoutError(f"Google decision request timed out after {self.timeout}s.") from exc
                await asyncio.sleep(min(_retry_delay(None, attempt), 5.0))
                continue
            except httpx.RequestError as exc:
                if attempt == self.max_retries:
                    raise DecisionAPIError("Network error communicating with Google Decisions.") from exc
                await asyncio.sleep(min(_retry_delay(None, attempt), 5.0))
                continue

            if response.status_code == 200:
                return self._parse_response(response, questions, target_model, (time.perf_counter() - started) * 1000)

            try:
                data = response.json()
            except ValueError:
                data = {}
            error = data.get("error", {}) if isinstance(data, dict) else {}
            error = error if isinstance(error, dict) else {}
            message = str(error.get("message") or f"Google Decisions returned HTTP {response.status_code}.")
            message = message.replace(self.api_key, "[redacted]")[:500]
            details = error.get("details", [])
            invalid_key = isinstance(details, list) and any(
                isinstance(detail, dict) and detail.get("reason") in {"API_KEY_INVALID", "API_KEY_EXPIRED"}
                for detail in details
            )
            if response.status_code in {401, 403} or invalid_key:
                raise DecisionAuthenticationError(message, status_code=response.status_code)
            if response.status_code == 429 or 500 <= response.status_code < 600:
                delay = _retry_delay(response, attempt)
                if attempt < self.max_retries:
                    logger.warning("Google Decisions returned HTTP %s; retrying.", response.status_code)
                    await asyncio.sleep(min(delay, 5.0))
                    continue
                if response.status_code == 429:
                    raise DecisionRateLimitError(message, retry_after=delay)
            raise DecisionAPIError(message, status_code=response.status_code)

        raise DecisionAPIError("Google decision retries exhausted.")

    def _parse_response(
        self,
        response: httpx.Response,
        questions: dict[str, BaseQuestion],
        model: str,
        latency_ms: float,
    ) -> DecisionResult:
        """Decode the flat Google envelope and verify every requested answer."""
        try:
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
                raise ValueError("Expected an answers object.")
            answers: dict[str, AnswerUnion] = {}
            for key, question in questions.items():
                raw = data["answers"].get(key)
                if not isinstance(raw, dict) or raw.get("type") != question.type or "confidence" not in raw:
                    raise ValueError(f"Missing or malformed answer for question '{key}'.")
                if isinstance(question, NoulQuestion):
                    answers[key] = NoulAnswer.model_validate({
                        "noul": raw.get("noul"), "confidence": raw["confidence"], "threshold": question.threshold,
                    })
                else:
                    probabilities = raw.get("probabilities")
                    expected = set(question.criteria) if isinstance(question, ChoiceQuestion) else {str(i) for i in range(len(question.criteria))}
                    if not isinstance(probabilities, dict) or set(probabilities) != expected:
                        raise ValueError(f"Invalid probability keys for question '{key}'.")
                    if isinstance(question, ChoiceQuestion):
                        if raw.get("choice") not in expected:
                            raise ValueError(f"Invalid choice for question '{key}'.")
                        answers[key] = ChoiceAnswer.model_validate(dict(raw))
                    else:
                        answer = ScoreAnswer.model_validate(dict(raw))
                        if not math.isfinite(answer.score) or not 0 <= answer.score <= len(question.criteria) - 1:
                            raise ValueError(f"Invalid score for question '{key}'.")
                        answer.selected_level = max(answer.probabilities, key=answer.probabilities.get)
                        answers[key] = answer
            return DecisionResult(model=model, answers=answers, latency_ms=latency_ms, raw_response=data)
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            # Never expose arbitrary response bodies or echoed credentials.
            raise DecisionAPIError("Malformed response from Google Decisions.", status_code=200) from exc

    async def close(self) -> None:
        """Close only the HTTP client owned by this adapter."""
        if not self._external_client:
            await self._client.aclose()
