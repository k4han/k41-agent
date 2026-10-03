"""Data models and exceptions for the decisions module."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# --- Exceptions ---


class DecisionError(Exception):
    """Base exception for decision model operations."""


class DecisionClientError(DecisionError):
    """Client-side validation, configuration, or usage error."""


class DecisionAPIError(DecisionError):
    """Remote decision API error."""

    def __init__(
        self,
        message: str,
        status_code: int = 500,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.errors = errors or []


class DecisionAuthenticationError(DecisionAPIError):
    """Authentication or authorization failure (HTTP 401/403)."""


class DecisionRateLimitError(DecisionAPIError):
    """Rate limit exceeded (HTTP 429)."""

    def __init__(
        self,
        message: str,
        retry_after: float | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message, status_code=429, errors=errors)
        self.retry_after = retry_after


class DecisionTimeoutError(DecisionError):
    """Inference request timed out."""


# --- Questions ---


class BaseQuestion(BaseModel):
    """Base model for decision questions."""

    model_config = ConfigDict(extra="ignore")

    instructions: str = Field(
        ...,
        description="Instructions or prompt describing the decision task.",
    )


class ChoiceQuestion(BaseQuestion):
    """Categorical choice evaluation question."""

    type: Literal["choice"] = "choice"
    criteria: dict[str, str] = Field(
        ...,
        min_length=1,
        description="Mapping from candidate options to descriptive criteria.",
    )


class NoulQuestion(BaseQuestion):
    """Probabilistic binary decision question (yes/no)."""

    type: Literal["noul"] = "noul"
    threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Probability threshold for resolving boolean determination.",
    )


class ScoreQuestion(BaseQuestion):
    """Rubric evaluation question over ordered scoring levels."""

    type: Literal["score"] = "score"
    criteria: list[str] = Field(
        ...,
        min_length=1,
        description="Ordered list of rubric levels from lowest to highest.",
    )


QuestionUnion = Annotated[
    Union[ChoiceQuestion, NoulQuestion, ScoreQuestion],
    Field(discriminator="type"),
]


# --- Answers ---


class BaseAnswer(BaseModel):
    """Base model for decision evaluation answers."""

    model_config = ConfigDict(extra="ignore")

    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Calibrated confidence score in [0.0, 1.0].",
    )


class ChoiceAnswer(BaseAnswer):
    """Typed answer for a choice question."""

    type: Literal["choice"] = "choice"
    choice: str = Field(description="Selected option label.")
    probabilities: dict[str, float] = Field(
        default_factory=dict,
        description="Normalized probability distribution over candidate options.",
    )

    @field_validator("probabilities", mode="after")
    @classmethod
    def _validate_probabilities(cls, v: dict[str, float]) -> dict[str, float]:
        for k, p in v.items():
            if not (0.0 <= p <= 1.0):
                raise ValueError(
                    f"Probability for '{k}' must be in [0.0, 1.0], got {p}"
                )
        return v

    @model_validator(mode="before")
    @classmethod
    def _fill_choice_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "type" not in data:
                data["type"] = "choice"
            if ("choice" not in data or data.get("choice") is None) and "value" in data:
                data["choice"] = str(data["value"])
            probs = data.get("probabilities")
            choice = data.get("choice")
            if (data.get("confidence") is None or "confidence" not in data) and isinstance(probs, dict) and choice in probs:
                try:
                    data["confidence"] = float(probs[choice])
                except (ValueError, TypeError):
                    pass
        return data


class NoulAnswer(BaseAnswer):
    """Typed answer for a probabilistic binary question."""

    type: Literal["noul"] = "noul"
    noul: float = Field(
        ge=0.0,
        le=1.0,
        description="Calibrated probability float of a 'yes' determination.",
    )
    decision: bool = Field(
        default=False,
        description="Resolved boolean determination (true if noul >= threshold).",
    )

    @model_validator(mode="before")
    @classmethod
    def _fill_noul_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "type" not in data:
                data["type"] = "noul"
            if ("noul" not in data or data.get("noul") is None) and "value" in data:
                data["noul"] = data["value"]
            if "noul" in data and data["noul"] is not None:
                try:
                    n = float(data["noul"])
                    threshold = float(data.get("threshold", 0.5))
                    if "decision" not in data or data.get("decision") is None:
                        data["decision"] = n >= threshold
                    if "confidence" not in data or data.get("confidence") is None:
                        data["confidence"] = max(n, 1.0 - n)
                except (ValueError, TypeError):
                    pass
        return data


class ScoreAnswer(BaseAnswer):
    """Typed answer for an ordered rubric score question."""

    type: Literal["score"] = "score"
    score: float = Field(
        description="Probability-weighted score along the rubric criteria.",
    )
    selected_level: str | None = Field(
        default=None,
        description="Level label corresponding to the highest probability or weighted index.",
    )
    probabilities: dict[str, float] = Field(
        default_factory=dict,
        description="Normalized probability distribution over rubric levels.",
    )

    @field_validator("probabilities", mode="after")
    @classmethod
    def _validate_probabilities(cls, v: dict[str, float]) -> dict[str, float]:
        for k, p in v.items():
            if not (0.0 <= p <= 1.0):
                raise ValueError(
                    f"Probability for '{k}' must be in [0.0, 1.0], got {p}"
                )
        return v

    @model_validator(mode="before")
    @classmethod
    def _fill_score_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "type" not in data:
                data["type"] = "score"
            if ("score" not in data or data.get("score") is None) and "value" in data:
                data["score"] = data["value"]
            probs = data.get("probabilities")
            if isinstance(probs, dict) and probs:
                if "confidence" not in data or data.get("confidence") is None:
                    try:
                        data["confidence"] = max(float(v) for v in probs.values())
                    except (ValueError, TypeError):
                        pass
                if "selected_level" not in data or data.get("selected_level") is None:
                    try:
                        data["selected_level"] = max(probs, key=lambda k: float(probs[k]))
                    except (ValueError, TypeError):
                        pass
        return data


AnswerUnion = Annotated[
    Union[ChoiceAnswer, NoulAnswer, ScoreAnswer],
    Field(discriminator="type"),
]


# --- Result ---


class DecisionResult(BaseModel):
    """Container for multi-question decision evaluation outputs."""

    model_config = ConfigDict(extra="ignore")

    model: str = Field(description="Decision model identifier executed.")
    answers: dict[str, AnswerUnion] = Field(
        default_factory=dict,
        description="Mapping of question identifiers to typed answers.",
    )
    latency_ms: float = Field(
        default=0.0,
        description="Execution latency in milliseconds.",
    )
    raw_response: dict[str, Any] | None = Field(
        default=None,
        description="Raw API response envelope for auditing and telemetry.",
    )
