from typing import Annotated, Any, Literal

from langchain_core.tools import InjectedToolArg, StructuredTool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import interrupt
from pydantic import BaseModel, Field, model_validator

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory


PLAN_MODE_TOOL_NAME = "plan_mode_respond"
PLAN_REVIEW_INTERRUPT_TYPE = "plan_review"
PLAN_REVIEW_APPROVED_PREFIX = "PLAN_REVIEW_APPROVED"
PLAN_REVIEW_REVISION_PREFIX = "PLAN_REVIEW_REVISION_REQUESTED"


class PlanModeRespondInput(BaseModel):
    """Input schema for requesting human review of an implementation plan."""

    plan: str = Field(
        ...,
        min_length=1,
        description="The complete plan to show to the user for approval or revision.",
    )


PlanResumeAction = Literal["approve", "revise"]


class PlanModeResumePayload(BaseModel):
    """Payload supplied by the dashboard when resuming a plan review."""

    action: PlanResumeAction
    target_agent: str | None = None
    feedback: str | None = None

    @model_validator(mode="after")
    def _validate_action_payload(self) -> "PlanModeResumePayload":
        if self.action == "approve":
            if not str(self.target_agent or "").strip():
                raise ValueError("target_agent is required when approving a plan.")
        elif not str(self.feedback or "").strip():
            raise ValueError("feedback is required when revising a plan.")
        return self


PLAN_MODE_RESPOND_DESCRIPTION = (
    "Show an implementation plan to the user and pause execution until the "
    "user either approves it with a target agent or provides feedback to revise it."
)


def _normalize_resume_payload(value: Any) -> PlanModeResumePayload:
    if not isinstance(value, dict):
        return PlanModeResumePayload(action="revise", feedback=str(value or "").strip())
    return PlanModeResumePayload.model_validate(value)


def _plan_mode_respond(
    plan: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    normalized_plan = str(plan or "").strip()
    resume_value = interrupt(
        {
            "type": PLAN_REVIEW_INTERRUPT_TYPE,
            "tool_call_id": runtime.tool_call_id,
            "plan": normalized_plan,
        }
    )
    payload = _normalize_resume_payload(resume_value)

    if payload.action == "approve":
        target_agent = str(payload.target_agent or "").strip()
        return (
            f"{PLAN_REVIEW_APPROVED_PREFIX}\n"
            f"Target agent: {target_agent}\n\n"
            f"Approved plan:\n{normalized_plan}"
        )

    feedback = str(payload.feedback or "").strip()
    return (
        f"{PLAN_REVIEW_REVISION_PREFIX}\n"
        f"User feedback:\n{feedback}\n\n"
        "Revise the plan according to the feedback and call plan_mode_respond again."
    )


async def _aplan_mode_respond(
    plan: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    return _plan_mode_respond(plan, runtime)


plan_mode_respond = StructuredTool.from_function(
    name=PLAN_MODE_TOOL_NAME,
    description=PLAN_MODE_RESPOND_DESCRIPTION,
    func=_plan_mode_respond,
    coroutine=_aplan_mode_respond,
    args_schema=PlanModeRespondInput,
    infer_schema=False,
)

register_tool(
    category=ToolCategory.UTILITY,
    capabilities=[
        ToolCapability.MUTATES_STATE,
        ToolCapability.REQUIRES_THREAD,
    ],
    tags=["planning", "approval", "human-in-the-loop"],
)(plan_mode_respond)


__all__ = [
    "PLAN_MODE_TOOL_NAME",
    "PLAN_REVIEW_APPROVED_PREFIX",
    "PLAN_REVIEW_INTERRUPT_TYPE",
    "PLAN_REVIEW_REVISION_PREFIX",
    "PlanModeRespondInput",
    "PlanModeResumePayload",
    "plan_mode_respond",
]
