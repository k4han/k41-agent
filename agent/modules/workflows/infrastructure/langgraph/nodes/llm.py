from functools import lru_cache

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.runtime import Runtime

from agent.modules.providers.public import get_chat_model
from agent.modules.skills.public import get_skills_catalog_xml
from agent.modules.workflows.infrastructure.langgraph.run_config import (
    WorkflowContext,
    get_context_value,
)


SKILLS_DISCLOSURE_PROMPT = (
    "The following skills provide specialized instructions for specific tasks.\n"
    "When a task matches a skill description, call the skill tool with the skill "
    "name to load full instructions before proceeding."
)


def _has_skill_tool(tools: list[BaseTool]) -> bool:
    return any(getattr(tool, "name", "") == "skill" for tool in tools)


@lru_cache(maxsize=2)
def _build_skills_prompt_section() -> str:
    catalog_xml = get_skills_catalog_xml().strip()
    if catalog_xml == "<available_skills/>":
        return ""

    return f"\n\n{SKILLS_DISCLOSURE_PROMPT}\n{catalog_xml}"


@lru_cache(maxsize=32)
def _get_bound_llm(tool_names_key: tuple[str, ...], model: str):
    """
    Cache LLM with bound tools. Tool names used as cache key.
    Don't cache by working_dir since working_dir is only used at runtime in tools.
    """
    return None


def make_llm_node(
    tools: list[BaseTool],
    model: str = "devstral-2512",
    system_prompts: dict[str, str] | None = None,
):
    """
    Factory to create llm_node with a fixed set of tools.
    system_prompts: dict mapping service_type → prompt template
                    template can use {working_dir}
    """
    llm = get_chat_model(model=model).bind_tools(tools)
    include_skills_catalog = _has_skill_tool(tools)

    default_prompts = {
        "default": "You are a helpful AI assistant.",
        "backend": "You are a Python/backend engineer assistant.\nWorking directory: {working_dir}",
        "frontend": "You are a React/frontend engineer assistant.\nWorking directory: {working_dir}",
        "devops": "You are a DevOps engineer assistant.\nWorking directory: {working_dir}",
    }

    prompts = {**default_prompts, **(system_prompts or {})}

    def llm_node(state, runtime: Runtime[WorkflowContext]):
        service_type = get_context_value(runtime.context, "service_type", "default")
        working_dir = get_context_value(runtime.context, "working_dir", ".")

        prompt_template = prompts.get(service_type, prompts["default"])
        system_prompt = prompt_template.format(working_dir=working_dir)
        if include_skills_catalog:
            system_prompt = f"{system_prompt}{_build_skills_prompt_section()}"

        messages: list[BaseMessage] = [
            SystemMessage(content=system_prompt),
            *state["messages"],
        ]

        response = llm.invoke(messages)
        return {"messages": [response]}

    return llm_node
