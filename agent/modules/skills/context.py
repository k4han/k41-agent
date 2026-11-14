"""Durable skill activation state and model context projection."""

from __future__ import annotations

from dataclasses import asdict
import hashlib

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.modules.skills.sources import workspace_key


def activation_key(agent_name: str, workspace, name: str) -> str:
    return hashlib.sha256(f"{agent_name}\0{workspace_key(workspace)}\0{name}".encode()).hexdigest()


def skill_events(messages, active: dict | None = None) -> dict:
    result = dict(active or {})
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name != "skill" or message.status == "error":
            continue
        artifact = message.artifact
        if not isinstance(artifact, dict) or artifact.get("kind") != "skill_activation":
            continue
        if artifact["action"] == "unload":
            result.pop(artifact["key"], None)
        else:
            result[artifact["key"]] = artifact["skill"]
    return result


def skill_context_id(key: str, version: str, origin: str = "") -> str:
    return "call_skill_" + hashlib.sha256(f"{key}\0{version}\0{origin}".encode()).hexdigest()[:32]


def bind_skill_context(active: dict, messages) -> dict:
    """Give legacy checkpoints stable references without changing pinned content."""
    result = {}
    for key, entry in active.items():
        call_id = entry.get("context_call_id")
        if not call_id:
            match = next((message for message in reversed(messages)
                if isinstance(message, ToolMessage) and message.name == "skill" and message.status != "error"
                and isinstance(message.artifact, dict) and message.artifact.get("kind") == "skill_activation"
                and message.artifact.get("key") == key and message.artifact.get("action") == "load"
                and message.artifact.get("skill", {}).get("version") == entry.get("version")), None)
            call_id = match.tool_call_id if match else skill_context_id(key, entry.get("version", ""))
        result[key] = {**entry, "context_call_id": call_id}
    return result


def is_skill_instruction(message) -> bool:
    return (isinstance(message, ToolMessage) and message.name == "skill" and message.status != "error"
            and isinstance(message.artifact, dict) and message.artifact.get("kind") == "skill_activation"
            and message.artifact.get("context_instruction") is True)


def _skill_message_pair(key: str, entry: dict, *, call_id: str | None = None,
                        action: str = "load", instructions: bool = True, refresh: bool = False) -> list:
    call_id = call_id or entry["context_call_id"]
    arguments = {"name": entry["name"]}
    artifact = {"kind": "skill_activation", "action": action, "key": key, "context_instruction": instructions}
    if action == "unload":
        arguments["action"] = action
        artifact["name"] = entry["name"]
        content = f"Unloaded skill {entry['name']}."
    else:
        if refresh:
            arguments["refresh"] = True
        artifact["skill"] = entry
        content = entry["content"] if instructions else f"Skill {entry['name']} is already active at version {entry['version']}."
    return [
        AIMessage(content="", id=f"skill-call-{call_id}", tool_calls=[{
            "name": "skill", "args": arguments, "id": call_id, "type": "tool_call",
        }]),
        ToolMessage(content=content, id=f"skill-result-{call_id}", name="skill", tool_call_id=call_id, artifact=artifact),
    ]


def model_skill_history(messages, active_skills: dict | None = None, *, insertion_index: int | None = None):
    """Keep one complete response per activation, rebuilding missing tool pairs."""
    active = bind_skill_context(skill_events(messages) if active_skills is None else active_skills, messages)
    by_call = {entry["context_call_id"]: (key, entry) for key, entry in active.items()}
    result = []
    seen = set()
    for message in messages:
        if isinstance(message, ToolMessage) and message.name == "skill" and message.status != "error":
            artifact = message.artifact if isinstance(message.artifact, dict) else {}
            if message.tool_call_id in by_call and message.tool_call_id not in seen:
                key, entry = by_call[message.tool_call_id]
                seen.add(message.tool_call_id)
                projected = message.model_copy(update={"content": entry["content"], "artifact": {
                    **artifact, "kind": "skill_activation", "action": "load", "key": key,
                    "skill": entry, "context_instruction": True,
                }})
                message = message if message == projected else projected
            elif artifact.get("kind") == "skill_activation":
                name = artifact.get("skill", {}).get("name", artifact.get("name", ""))
                projected = message.model_copy(update={
                    "content": f"Skill {name}: {artifact['action']} completed; no instructions in this receipt.",
                    "artifact": {**artifact, "context_instruction": False},
                })
                # Repeated loads already return a short receipt. Keep it byte-identical.
                if artifact.get("context_instruction") is False:
                    projected = projected.model_copy(update={"content": message.content})
                message = message if message == projected else projected
        result.append(message)
    missing = []
    for key, entry in active.items():
        call_id = entry["context_call_id"]
        if call_id in seen:
            continue
        missing.extend(_skill_message_pair(key, entry))
    index = len(result) if insertion_index is None else insertion_index
    result[index:index] = missing
    return messages if result == messages else result


def skill_commands(messages) -> tuple[list[tuple[str, str]], str]:
    """Only explicit leading commands in the latest user message activate skills."""
    latest = next((message for message in reversed(messages) if isinstance(message, HumanMessage)), None)
    if latest is None:
        return [], ""
    content = latest.content
    if isinstance(content, list):
        content = "\n".join(str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text")
    commands = []
    for line in str(content).splitlines():
        parts = line.split()
        if not parts or parts[0].lower() != "/skill" or not line.startswith("/"):
            break
        if len(parts) < 2:
            raise ValueError("Skill name is required. Use /skill NAME or /skill load NAME.")
        action = parts[1].lower()
        if action in {"load", "unload", "refresh"}:
            if len(parts) < 3:
                raise ValueError(f"Skill name is required. Use /skill {action} NAME.")
            commands.append((action, parts[2]))
        else:
            commands.append(("load", parts[1]))
    return list(dict.fromkeys(commands)), latest.id or hashlib.sha256(str(content).encode()).hexdigest()


def requested_skills(messages) -> list[str]:
    return [name for action, name in skill_commands(messages)[0] if action == "load"]


async def active_context(state: dict, *, workspace, thread_id: str, agent_name: str, names, tools) -> tuple[list, dict, list[str]]:
    from agent.modules.skills.packages import get_skill_packages

    history = state.get("messages", [])
    active = bind_skill_context(state.get("active_skills", {}), history)
    command_messages = []
    processed = list(state.get("processed_skill_messages", []))
    commands, message_id = skill_commands(state.get("messages", []))
    if not any(tool.name == "skill" for tool in tools):
        if commands and message_id not in processed:
            raise PermissionError("This agent does not have the skill tool enabled.")
        return model_skill_history(state.get("messages", []), {}), {}, processed
    packages = get_skill_packages()
    request_key = message_id
    if commands and request_key not in processed:
        for action, name in commands:
            key = activation_key(agent_name, workspace, name)
            if action == "unload":
                active.pop(key, None)
                command_messages.extend(_skill_message_pair(key, {"name": name},
                    call_id=skill_context_id(key, "", f"{thread_id}:{message_id}:{action}"), action="unload", instructions=False))
            else:
                instructions = key not in active or action == "refresh"
                if instructions:
                    loaded = await packages.activate(name, workspace=workspace, thread_id=thread_id, agent_name=agent_name, allowed_names=names)
                    active[key] = {**asdict(loaded), "context_call_id": skill_context_id(key, loaded.version, f"{thread_id}:{message_id}:{action}")}
                command_messages.extend(_skill_message_pair(key, active[key],
                    call_id=skill_context_id(key, active[key]["version"], f"{thread_id}:{message_id}:{action}"),
                    instructions=instructions, refresh=action == "refresh"))
        processed = [*processed[-99:], request_key]
    for key, entry in list(active.items()):
        if packages.authorize_active(entry, workspace=workspace, agent_name=agent_name, allowed_names=names):
            entry = await packages.restore(entry, workspace=workspace, thread_id=thread_id, defer_execution=True)
            active[key] = entry
        else:
            active.pop(key, None)
    if command_messages:
        history = [*history, *command_messages]
    active = bind_skill_context(active, history)
    return model_skill_history(history, active), active, processed
