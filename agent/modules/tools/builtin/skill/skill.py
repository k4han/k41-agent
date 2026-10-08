from typing import Annotated, Any, Literal
from dataclasses import asdict
from langchain_core.messages import ToolMessage

from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime

from agent.modules.skills import get_effective_skill_content_xml
from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCategory
from agent.modules.tools.result import ToolError, ToolErrorCode
from agent.modules.tools.runtime.context import get_context_value, get_thread_id


@register_tool(category=ToolCategory.SKILL, tags=["skill"])
@tool
async def skill(
    name: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
    action: Literal["load", "unload"] = "load",
    refresh: bool = False,
) -> Any:
    """
    Load or unload a skill by name. Use refresh=True to load its current version.
    Resources are made available to read and bash tools; task outputs use the workspace.
    Wait for loading to complete before accessing its resources in a subsequent tool batch.
    Coding tools in the same batch use the previously active skills for authorization.
    """
    allowed_names = get_context_value(runtime.context, "allowed_skill_names", None)
    if getattr(runtime, "tool_call_id", None):
        from agent.modules.skills import activation_key, get_skill_packages
        from agent.modules.tools.builtin.workspace import get_workspace
        workspace = get_workspace(runtime)
        agent_name = get_context_value(runtime.context, "agent_name", "default")
        key = activation_key(agent_name, workspace, name)
        if action == "unload":
            artifact = {"kind": "skill_activation", "action": "unload", "key": key, "name": name}
            return ToolMessage(content=f"Unloaded skill {name}.", artifact=artifact, name="skill", tool_call_id=runtime.tool_call_id)
        active = getattr(runtime, "state", {}).get("active_skills", {}).get(key)
        packages = get_skill_packages()
        try:
            if active and not refresh:
                if not packages.authorize_active(active, workspace=workspace, agent_name=agent_name, allowed_names=allowed_names):
                    raise ToolError(ToolErrorCode.PERMISSION_DENIED, "skill is not available")
                loaded = await packages.restore(active, workspace=workspace, thread_id=get_thread_id(runtime.config) or "")
            else:
                loaded = asdict(await packages.activate(name, workspace=workspace,
                    thread_id=get_thread_id(runtime.config) or "", agent_name=agent_name, allowed_names=allowed_names))
        except PermissionError as exc:
            raise ToolError(ToolErrorCode.PERMISSION_DENIED, str(exc)) from exc
        except FileNotFoundError as exc:
            raise ToolError(ToolErrorCode.NOT_FOUND, str(exc)) from exc
        except ValueError as exc:
            raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc
        except (OSError, RuntimeError) as exc:
            raise ToolError(ToolErrorCode.EXECUTION_ERROR, str(exc)) from exc
        receipt = f"Skill {name} is already active at version {loaded['version']}. skill_root: {loaded['skill_root']}"
        return ToolMessage(content=receipt if active and not refresh else loaded["content"], artifact={"kind": "skill_activation", "action": "load", "key": key, "skill": loaded},
                           name="skill", tool_call_id=runtime.tool_call_id)
    if action == "unload":
        return f"Unloaded skill {name}."
    content_xml = await get_effective_skill_content_xml(
        name,
        allowed_names=allowed_names,
        workspace=get_context_value(runtime.context, "workspace", None),
        thread_id=get_thread_id(getattr(runtime, "config", None)),
    )
    if content_xml is None:
        allowed = (
            None
            if allowed_names is None
            else {str(value).strip() for value in allowed_names if str(value).strip()}
        )
        if allowed is not None and str(name or "").strip() not in allowed:
            raise ToolError(ToolErrorCode.PERMISSION_DENIED, "skill is not available")
        raise ToolError(ToolErrorCode.NOT_FOUND, "skill not found")
    return content_xml
