from functools import lru_cache

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from agent.modules.providers.public import get_chat_model


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

    default_prompts = {
        "default": "You are a helpful AI assistant.",
        "backend": "You are a Python/backend engineer assistant.\nWorking directory: {working_dir}",
        "frontend": "You are a React/frontend engineer assistant.\nWorking directory: {working_dir}",
        "devops": "You are a DevOps engineer assistant.\nWorking directory: {working_dir}",
    }

    prompts = {**default_prompts, **(system_prompts or {})}

    def llm_node(state, config: RunnableConfig):
        cfg = config.get("configurable", {})
        service_type = cfg.get("service_type", "default")
        working_dir = cfg.get("working_dir", ".")

        prompt_template = prompts.get(service_type, prompts["default"])
        system_prompt = prompt_template.format(working_dir=working_dir)

        messages: list[BaseMessage] = [
            SystemMessage(content=system_prompt),
            *state["messages"],
        ]

        response = llm.invoke(messages)
        return {"messages": [response]}

    return llm_node
