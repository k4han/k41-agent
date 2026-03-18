# agent/nodes/llm_node.py

from functools import lru_cache
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from agent.providers.llm import get_llm


# Cache llm+tools binding theo (service_type, tool_names tuple)
@lru_cache(maxsize=32)
def _get_bound_llm(tool_names_key: tuple[str, ...], model: str):
    """
    Cache LLM đã bind tools. Tool names dùng làm cache key.
    Không cache theo working_dir vì working_dir chỉ dùng runtime trong tool.
    """
    return None  # Placeholder — actual binding xảy ra trong make_llm_node


def make_llm_node(
    tools: list[BaseTool],
    model: str = "devstral-2512",
    system_prompts: dict[str, str] | None = None,
):
    """
    Factory tạo llm_node với bộ tools cố định.
    system_prompts: dict mapping service_type → prompt template
                    template có thể dùng {working_dir}
    """
    # Bind tools 1 lần khi build graph
    llm = get_llm(model=model).bind_tools(tools)

    default_prompts = {
        "default":  "Bạn là AI assistant hữu ích.",
        "backend":  "Bạn là Python/backend engineer assistant.\nWorking directory: {working_dir}",
        "frontend": "Bạn là React/frontend engineer assistant.\nWorking directory: {working_dir}",
        "devops":   "Bạn là DevOps engineer assistant.\nWorking directory: {working_dir}",
    }

    prompts = {**default_prompts, **(system_prompts or {})}

    def llm_node(state, config: RunnableConfig):
        cfg          = config.get("configurable", {})
        service_type = cfg.get("service_type", "default")
        working_dir  = cfg.get("working_dir", ".")

        # Lấy system prompt theo service_type, fallback về default
        prompt_template = prompts.get(service_type, prompts["default"])
        system_prompt   = prompt_template.format(working_dir=working_dir)

        messages: list[BaseMessage] = [
            SystemMessage(content=system_prompt),
            *state["messages"],
        ]

        response = llm.invoke(messages)
        return {"messages": [response]}

    return llm_node
