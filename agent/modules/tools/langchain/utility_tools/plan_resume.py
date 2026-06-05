from typing import Literal

from pydantic import BaseModel, model_validator


PlanResumeAction = Literal["approve", "revise"]


class PlanResumePayload(BaseModel):
    """Payload supplied when resuming a plan review."""

    action: PlanResumeAction
    target_agent: str | None = None
    feedback: str | None = None

    @model_validator(mode="after")
    def _validate_action_payload(self) -> "PlanResumePayload":
        if self.action == "approve":
            if not str(self.target_agent or "").strip():
                raise ValueError("target_agent is required when approving a plan.")
        elif not str(self.feedback or "").strip():
            raise ValueError("feedback is required when revising a plan.")
        return self


__all__ = [
    "PlanResumeAction",
    "PlanResumePayload",
]
