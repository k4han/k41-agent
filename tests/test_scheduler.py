import pytest

def test_schedule_task_in_registry():
    from agent.modules.tools.infrastructure.langchain.registry import get_all_langchain_tools
    tools = get_all_langchain_tools()
    tool_names = [tool.name for tool in tools]
    assert "schedule_task" in tool_names

def test_schedule_task_import():
    from agent.modules.tools.infrastructure.langchain.schedule_tools.schedule import (
        schedule_task, ScheduleTaskInput
    )
    assert schedule_task is not None
    assert ScheduleTaskInput is not None
    
    schema = ScheduleTaskInput.model_json_schema()
    required_fields = schema.get("required", [])
    assert "task_description" in required_fields
