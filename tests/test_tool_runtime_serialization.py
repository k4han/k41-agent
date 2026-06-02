import inspect
import warnings
from typing import Annotated, Any, get_args, get_origin, get_type_hints

import pytest
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.langchain.file_tools.list_files import list_files
from agent.modules.tools.langchain.registry import get_all_langchain_tools
from agent.modules.workflows.run_config import make_context


def _contains_unparameterized_tool_runtime(annotation: object) -> bool:
    if annotation is ToolRuntime:
        return True

    origin = get_origin(annotation)
    if origin is Annotated:
        return _contains_unparameterized_tool_runtime(get_args(annotation)[0])

    return False


def test_tool_runtime_annotations_are_parameterized() -> None:
    unparameterized: list[str] = []

    for tool in get_all_langchain_tools():
        for attr_name in ("func", "coroutine"):
            fn = getattr(tool, attr_name, None)
            if fn is None:
                continue

            hints = get_type_hints(fn, include_extras=True)
            for parameter in inspect.signature(fn).parameters.values():
                annotation = hints.get(parameter.name, parameter.annotation)
                if _contains_unparameterized_tool_runtime(annotation):
                    unparameterized.append(f"{tool.name}.{parameter.name}")

    assert unparameterized == []


@pytest.mark.asyncio
async def test_runtime_injected_tool_validation_does_not_warn(tmp_path) -> None:
    runtime = ToolRuntime[Any, Any](
        state={},
        context=make_context(
            working_dir=str(tmp_path),
            max_context_tokens=100,
            agent_name="default",
            allowed_tool_names=[],
        ),
        config={},
        stream_writer=lambda _: None,
        tool_call_id="call-1",
        store=None,
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = await list_files.ainvoke(
            {
                "type": "tool_call",
                "name": list_files.name,
                "args": {"runtime": runtime},
                "id": "call-1",
            }
        )

    assert result.content == "(Empty directory)"
    assert not any(
        "Pydantic serializer warnings" in str(warning.message)
        for warning in caught
    )
