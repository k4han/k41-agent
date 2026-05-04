"""Centralized tool registry for LangChain tools."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from agent.modules.tools.langchain.agent_tools.call_agent import (
    call_agent,
)
from agent.modules.tools.langchain.file_tools.list_files import (
    list_files,
)
from agent.modules.tools.langchain.file_tools.read_file import read_file
from agent.modules.tools.langchain.file_tools.write_file import (
    write_file,
)
from agent.modules.tools.langchain.shell_tools.run_bash import run_bash
from agent.modules.tools.langchain.skill_tools.skill import skill
from agent.modules.tools.langchain.utility_tools.echo import echo
from agent.modules.tools.langchain.utility_tools.get_current_time import (
    get_current_time,
)
from agent.modules.tools.langchain.schedule_tools.schedule import (
    schedule_task,
    list_scheduled_tasks,
    delete_scheduled_task,
)

def get_all_langchain_tools() -> list[BaseTool]:
    """Return all available LangChain tools."""
    return [
        get_current_time,
        echo,
        skill,
        read_file,
        write_file,
        run_bash,
        list_files,
        call_agent,
        schedule_task,
        list_scheduled_tasks,
        delete_scheduled_task,
    ]
