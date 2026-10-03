"""Cloudflare Workers AI client for Clef and Clef-flash decision models."""

from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
import logging
import math
import random
import time
from typing import Any

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


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    """Safely decode JSON response or return fallback dictionary with raw text for HTML/non-JSON content."""
    try:
        return resp.json() if resp.content else {}
    except Exception:
        raw_text = resp.text[:500] if resp.content else ""
        return {"raw": raw_text, "errors": [{"message": raw_text}]}


def _serialize_question_for_cf(q: BaseQuestion) -> dict[str, Any]:
    """Serialize question into Cloudflare Workers AI schema format."""
    if isinstance(q, ChoiceQuestion):
        return {
            "type": "choice",
            "instructions": q.instructions,
            "criteria": q.criteria,
        }
    if isinstance(q, NoulQuestion):
        return {
            "type": "noul",
            "instructions": q.instructions,
        }
    if isinstance(q, ScoreQuestion):
        return {
            "type": "score",
            "instructions": q.instructions,
            "criteria": q.criteria,
        }
    return q.model_dump(exclude_none=True)


class CloudflareClefClient(DecisionClient):
    """Resilient asynchronous client for Cloudflare Workers AI Clef / Clef-flash models."""

    DEFAULT_MODEL = "@cf/cloudflare/clef-flash"
    DEFAULT_BASE_URL = "https://api.cloudflare.com/client/v4"

    def __init__(
        self,
        account_id: str,
        api_token: str,
        *,
        default_model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 8.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not account_id or not account_id.strip():
            raise DecisionClientError("Cloudflare account_id must be provided.")
        if not api_token or not api_token.strip():
            raise DecisionClientError("Cloudflare api_token must be provided.")

        self.account_id = account_id.strip()
        self.api_token = api_token.strip()
        self.default_model = default_model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

        self._external_client = client is not None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(3.0, timeout)),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
        )

    def _build_url(self, model: str) -> str:
        """Construct the REST endpoint URL for a given model."""
        return f"{self.base_url}/accounts/{self.account_id}/ai/run/{model}"

    def _headers(self) -> dict[str, str]:
        """Generate authentication and content negotiation headers."""
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def evaluate(
        self,
        state: str | dict[str, Any],
        questions: dict[str, BaseQuestion],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> DecisionResult:
        """Evaluate state against questions using Cloudflare Workers AI."""
        if not questions:
            raise DecisionClientError("At least one question must be specified.")

        target_model = model or self.default_model
        endpoint_url = self._build_url(target_model)

        payload = {
            "model": target_model.split("/")[-1],
            "state": state,
            "questions": {k: _serialize_question_for_cf(v) for k, v in questions.items()},
        }

        start_time = time.perf_counter()
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.post(
                    endpoint_url,
                    json=payload,
                    headers=self._headers(),
                )

                if response.status_code == 200:
                    latency_ms = (time.perf_counter() - start_time) * 1000.0
                    data = _safe_json(response)
                    if data.get("success") is False:
                        raise DecisionAPIError(
                            f"Cloudflare Workers AI returned success=false: {data.get('errors') or data.get('raw')}",
                            status_code=200,
                            errors=data.get("errors", []),
                        )
                    return self._parse_success_response(
                        data=data,
                        model=target_model,
                        questions=questions,
                        latency_ms=latency_ms,
                    )

                if response.status_code in (401, 403):
                    data = _safe_json(response)
                    err_msg = data.get("errors") or data.get("raw") or "Unknown error"
                    raise DecisionAuthenticationError(
                        f"Authentication failed ({response.status_code}): {err_msg}",
                        status_code=response.status_code,
                        errors=data.get("errors", []),
                    )

                if response.status_code == 429:
                    retry_header = response.headers.get("Retry-After")
                    retry_after: float | None = None
                    if retry_header:
                        try:
                            retry_after = float(retry_header)
                        except (ValueError, TypeError):
                            try:
                                dt = parsedate_to_datetime(retry_header)
                                retry_after = max(0.0, dt.timestamp() - time.time())
                            except Exception:
                                pass
                    if (
                        retry_after is None
                        or math.isnan(retry_after)
                        or math.isinf(retry_after)
                        or retry_after < 0.0
                    ):
                        retry_after = (1.0 * (2 ** attempt)) + random.uniform(0.1, 0.5)

                    data = _safe_json(response)
                    if attempt < self.max_retries:
                        logger.warning(
                            "Cloudflare rate limited (429), retrying after %.2fs...", retry_after
                        )
                        await asyncio.sleep(min(retry_after, 5.0))
                        continue
                    raise DecisionRateLimitError(
                        "Cloudflare Workers AI rate limit exceeded.",
                        retry_after=retry_after,
                        errors=data.get("errors", []),
                    )

                if response.status_code in (500, 502, 503, 504) and attempt < self.max_retries:
                    backoff = (0.5 * (2 ** attempt)) + random.uniform(0.05, 0.2)
                    logger.warning(
                        "Cloudflare server error (%s), retrying in %.2fs...",
                        response.status_code,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue

                data = _safe_json(response)
                err_msg = data.get("errors") or data.get("raw") or "Unknown error"
                raise DecisionAPIError(
                    f"Cloudflare Workers AI returned status {response.status_code}: {err_msg}",
                    status_code=response.status_code,
                    errors=data.get("errors", []),
                )

            except httpx.TimeoutException as te:
                last_error = te
                if attempt < self.max_retries:
                    await asyncio.sleep(0.3 + random.uniform(0.05, 0.15))
                    continue
                raise DecisionTimeoutError(
                    f"Cloudflare inference timed out after {self.timeout}s: {te}"
                ) from te
            except (httpx.RequestError, httpx.HTTPError) as he:
                last_error = he
                if attempt < self.max_retries:
                    await asyncio.sleep(0.3 + random.uniform(0.05, 0.15))
                    continue
                raise DecisionAPIError(
                    f"Network error communicating with Cloudflare Workers AI: {he}"
                ) from he

        raise DecisionAPIError(f"Failed after {self.max_retries} retries: {last_error}")

    def _parse_success_response(
        self,
        data: dict[str, Any],
        model: str,
        questions: dict[str, BaseQuestion],
        latency_ms: float,
    ) -> DecisionResult:
        """Unpack Cloudflare response envelope into typed answers."""
        result = data.get("result") or {}
        raw_answers = result.get("answers") or {}

        typed_answers: dict[str, AnswerUnion] = {}
        for q_id, q_def in questions.items():
            if q_id not in raw_answers:
                raise DecisionAPIError(
                    f"Missing answer for question '{q_id}' in Cloudflare response.",
                    status_code=500,
                    errors=data.get("errors", []),
                )
            ans_data = raw_answers[q_id]

            if isinstance(q_def, ChoiceQuestion):
                if isinstance(ans_data, str):
                    ans_data = {"choice": ans_data}
                elif not isinstance(ans_data, dict):
                    ans_data = {"choice": str(ans_data)}
                else:
                    ans_data = dict(ans_data)
            elif isinstance(q_def, NoulQuestion):
                if isinstance(ans_data, bool):
                    ans_data = {
                        "noul": 1.0 if ans_data else 0.0,
                        "decision": ans_data,
                        "threshold": q_def.threshold,
                    }
                elif isinstance(ans_data, (int, float)):
                    ans_data = {
                        "noul": float(ans_data),
                        "threshold": q_def.threshold,
                    }
                elif not isinstance(ans_data, dict):
                    ans_data = {
                        "noul": 0.0,
                        "threshold": q_def.threshold,
                    }
                else:
                    ans_data = dict(ans_data)
                    if "threshold" not in ans_data:
                        ans_data["threshold"] = q_def.threshold
            elif isinstance(q_def, ScoreQuestion):
                if isinstance(ans_data, (int, float)):
                    ans_data = {"score": float(ans_data)}
                elif not isinstance(ans_data, dict):
                    ans_data = {"score": 0.0}
                else:
                    ans_data = dict(ans_data)
            else:
                if isinstance(ans_data, str):
                    ans_data = {"choice": ans_data}
                elif not isinstance(ans_data, dict):
                    ans_data = {"value": ans_data}
                else:
                    ans_data = dict(ans_data)

            try:
                if isinstance(q_def, ChoiceQuestion):
                    typed_answers[q_id] = ChoiceAnswer.model_validate(ans_data)
                elif isinstance(q_def, NoulQuestion):
                    typed_answers[q_id] = NoulAnswer.model_validate(ans_data)
                elif isinstance(q_def, ScoreQuestion):
                    typed_answers[q_id] = ScoreAnswer.model_validate(ans_data)
                else:
                    typed_answers[q_id] = ChoiceAnswer.model_validate(ans_data)
            except ValidationError as ve:
                raise DecisionAPIError(
                    f"Malformed answer payload for question '{q_id}': {ve}",
                    status_code=500,
                    errors=data.get("errors", []),
                ) from ve
            except Exception as e:
                raise DecisionAPIError(
                    f"Malformed answer payload for question '{q_id}': {e}",
                    status_code=500,
                    errors=data.get("errors", []),
                ) from e

        return DecisionResult(
            model=result.get("model", model),
            answers=typed_answers,
            latency_ms=latency_ms,
            raw_response=data,
        )

    async def close(self) -> None:
        """Release underlying HTTP client resources."""
        if not self._external_client:
            await self._client.aclose()
