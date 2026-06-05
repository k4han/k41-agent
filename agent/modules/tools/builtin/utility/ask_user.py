import json
from typing import Annotated, Any, Literal, TypeAlias

from langchain_core.tools import InjectedToolArg, StructuredTool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import interrupt
from pydantic import BaseModel, Field, model_validator

from agent.modules.tools.builtin.utility.plan_resume import PlanResumePayload
from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory


ASK_USER_TOOL_NAME = "ask_user"
ASK_USER_INTERRUPT_TYPE = "user_input_request"
ASK_USER_ANSWER_TYPE = "ask_user_answer"

SelectionMode = Literal["single", "multiple"]


class AskUserOption(BaseModel):
    id: str = Field(..., min_length=1)
    label: str = Field(..., min_length=1)
    description: str = ""


class AskUserFreeText(BaseModel):
    enabled: bool = True
    label: str = "Your answer"
    placeholder: str = ""
    required: bool = False

    @model_validator(mode="after")
    def _validate_required(self) -> "AskUserFreeText":
        if self.required and not self.enabled:
            raise ValueError("free_text.required requires free_text.enabled.")
        return self


class AskUserQuestion(BaseModel):
    id: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)
    selection_mode: SelectionMode = "single"
    required: bool = True
    options: list[AskUserOption] = Field(default_factory=list)
    free_text: AskUserFreeText = Field(default_factory=AskUserFreeText)

    @model_validator(mode="after")
    def _validate_question(self) -> "AskUserQuestion":
        option_ids = [option.id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError(f"Duplicate option id in question '{self.id}'.")
        return self


class AskUserInput(BaseModel):
    title: str = ""
    questions: list[AskUserQuestion] = Field(..., min_length=1)
    submit_label: str = ""

    @model_validator(mode="after")
    def _validate_questions(self) -> "AskUserInput":
        question_ids = [question.id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("Duplicate question id.")
        return self


class UserQuestionAnswer(BaseModel):
    question_id: str = Field(..., min_length=1)
    selected_option_ids: list[str] = Field(default_factory=list)
    custom_text: str = ""


class AskUserAnswerResumePayload(BaseModel):
    action: Literal["answer"]
    answers: list[UserQuestionAnswer] = Field(default_factory=list)
    summary: str = ""


HumanResumePayload: TypeAlias = PlanResumePayload | AskUserAnswerResumePayload


ASK_USER_DESCRIPTION = (
    "Ask the user one or more structured questions and pause execution until "
    "the user answers. Use this only when the user must make a choice or "
    "provide information that cannot be inferred from available context."
)


def _normalize_answer_payload(value: Any) -> AskUserAnswerResumePayload:
    if isinstance(value, dict):
        return AskUserAnswerResumePayload.model_validate(value)
    return AskUserAnswerResumePayload(
        action="answer",
        answers=[],
        summary=str(value or "").strip(),
    )


def _answer_map(
    payload: AskUserAnswerResumePayload,
) -> dict[str, UserQuestionAnswer]:
    out: dict[str, UserQuestionAnswer] = {}
    for answer in payload.answers:
        if answer.question_id in out:
            raise ValueError(f"Duplicate answer for question '{answer.question_id}'.")
        out[answer.question_id] = answer
    return out


def _validate_answer_payload(
    request: AskUserInput,
    payload: AskUserAnswerResumePayload,
) -> None:
    by_question = {question.id: question for question in request.questions}
    answers = _answer_map(payload)

    for question_id, answer in answers.items():
        question = by_question.get(question_id)
        if question is None:
            raise ValueError(f"Unknown question id '{question_id}'.")

        selected_ids = list(answer.selected_option_ids)
        selected_set = set(selected_ids)
        if len(selected_ids) != len(selected_set):
            raise ValueError(f"Duplicate selected option id in '{question_id}'.")
        if question.selection_mode == "single" and len(selected_ids) > 1:
            raise ValueError(
                f"Question '{question_id}' allows only one selected option."
            )

        option_ids = {option.id for option in question.options}
        unknown_options = selected_set.difference(option_ids)
        if unknown_options:
            unknown = ", ".join(sorted(unknown_options))
            raise ValueError(
                f"Question '{question_id}' contains unknown option id(s): {unknown}."
            )

        custom_text = str(answer.custom_text or "").strip()
        if custom_text and not question.free_text.enabled:
            raise ValueError(f"Question '{question_id}' does not allow custom text.")
        if question.free_text.required and not custom_text:
            raise ValueError(f"Question '{question_id}' requires custom text.")

    for question in request.questions:
        answer = answers.get(question.id)
        selected_ids = list(answer.selected_option_ids) if answer else []
        custom_text = str(answer.custom_text or "").strip() if answer else ""
        has_answer = bool(selected_ids) or bool(custom_text)
        if question.required and not has_answer:
            raise ValueError(f"Question '{question.id}' requires an answer.")


def _tool_result_payload(payload: AskUserAnswerResumePayload) -> str:
    return json.dumps(
        {
            "type": ASK_USER_ANSWER_TYPE,
            "summary": str(payload.summary or "").strip(),
            "answers": [
                answer.model_dump()
                for answer in payload.answers
            ],
        },
        ensure_ascii=False,
    )


def _ask_user(
    title: str,
    questions: list[AskUserQuestion],
    submit_label: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    request = AskUserInput(
        title=title,
        questions=questions,
        submit_label=submit_label,
    )
    resume_value = interrupt(
        {
            "type": ASK_USER_INTERRUPT_TYPE,
            "tool_call_id": runtime.tool_call_id,
            "title": request.title,
            "questions": [
                question.model_dump()
                for question in request.questions
            ],
            "submit_label": request.submit_label,
        }
    )
    payload = _normalize_answer_payload(resume_value)
    _validate_answer_payload(request, payload)
    return _tool_result_payload(payload)


async def _aask_user(
    title: str,
    questions: list[AskUserQuestion],
    submit_label: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
) -> str:
    return _ask_user(title, questions, submit_label, runtime)


ask_user = StructuredTool.from_function(
    name=ASK_USER_TOOL_NAME,
    description=ASK_USER_DESCRIPTION,
    func=_ask_user,
    coroutine=_aask_user,
    args_schema=AskUserInput,
    infer_schema=False,
)

register_tool(
    category=ToolCategory.UTILITY,
    capabilities=[
        ToolCapability.MUTATES_STATE,
        ToolCapability.REQUIRES_THREAD,
    ],
    tags=["human-in-the-loop", "questions", "choices"],
)(ask_user)


__all__ = [
    "ASK_USER_ANSWER_TYPE",
    "ASK_USER_INTERRUPT_TYPE",
    "ASK_USER_TOOL_NAME",
    "AskUserAnswerResumePayload",
    "AskUserFreeText",
    "AskUserInput",
    "AskUserOption",
    "AskUserQuestion",
    "HumanResumePayload",
    "UserQuestionAnswer",
    "ask_user",
]
