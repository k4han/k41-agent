from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, StructuredTool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory
from agent.modules.tools.builtin.utility.plan_resume import (
    PlanResumeAction,
    PlanResumePayload,
)


REQUEST_PLAN_REVIEW_TOOL_NAME = "request_plan_review"
PLAN_MODE_TOOL_NAME = REQUEST_PLAN_REVIEW_TOOL_NAME
PLAN_REVIEW_INTERRUPT_TYPE = "plan_review"
PLAN_REVIEW_APPROVED_PREFIX = "PLAN_REVIEW_APPROVED"
PLAN_REVIEW_REVISION_PREFIX = "PLAN_REVIEW_REVISION_REQUESTED"


class RequestPlanReviewInput(BaseModel):
    """Input schema for requesting human review of an implementation plan."""

    plan: str = Field(
        ...,
        min_length=1,
        description="The complete plan to show to the user for approval or revision.",
    )


PlanModeRespondInput = RequestPlanReviewInput
PlanModeResumePayload = PlanResumePayload


REQUEST_PLAN_REVIEW_DESCRIPTION = (
    "Show an implementation plan to the user and pause execution until the "
    "user either approves it with a target agent or provides feedback to revise it. "
    "Use when the user requests plan review before implementation or an explicit handoff decision is needed. "
    "Provide a complete plan that another agent can execute without guessing. "
    "After approval, continue with the approved plan. If the user requests changes, "
    "revise the plan and call request_plan_review again."
)
PLAN_MODE_RESPOND_DESCRIPTION = REQUEST_PLAN_REVIEW_DESCRIPTION


def _normalize_resume_payload(value: Any) -> PlanModeResumePayload:
    if not isinstance(value, dict):
        return PlanModeResumePayload(action="revise", feedback=str(value or "").strip())
    return PlanModeResumePayload.model_validate(value)


def _request_plan_review(
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
        "Revise the plan according to the feedback and call request_plan_review again."
    )


def _plan_mode_respond(
    plan: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    return _request_plan_review(plan, runtime)


async def _arequest_plan_review(
    plan: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    return _request_plan_review(plan, runtime)


async def _aplan_mode_respond(
    plan: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    return _request_plan_review(plan, runtime)


request_plan_review = StructuredTool.from_function(
    name=REQUEST_PLAN_REVIEW_TOOL_NAME,
    description=REQUEST_PLAN_REVIEW_DESCRIPTION,
    func=_request_plan_review,
    coroutine=_arequest_plan_review,
    args_schema=RequestPlanReviewInput,
    infer_schema=False,
)

register_tool(
    category=ToolCategory.UTILITY,
    capabilities=[
        ToolCapability.MUTATES_STATE,
        ToolCapability.REQUIRES_THREAD,
    ],
    tags=["planning", "approval", "human-in-the-loop"],
)(request_plan_review)

plan_mode_respond = request_plan_review


__all__ = [
    "PLAN_MODE_TOOL_NAME",
    "PLAN_REVIEW_APPROVED_PREFIX",
    "PLAN_REVIEW_INTERRUPT_TYPE",
    "PLAN_REVIEW_REVISION_PREFIX",
    "PlanResumeAction",
    "PlanModeRespondInput",
    "PlanModeResumePayload",
    "REQUEST_PLAN_REVIEW_TOOL_NAME",
    "RequestPlanReviewInput",
    "plan_mode_respond",
    "request_plan_review",
]
