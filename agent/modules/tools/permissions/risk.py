"""Static operation risk classification and the shared decision table."""

from enum import StrEnum
import os

from agent.modules.tools.coding.paths import comparable_path
from agent.modules.tools.runtime.shell_guard import classify_command


class RiskLevel(StrEnum):
    SAFE = "safe"
    MUTATING = "mutating"
    DANGEROUS = "dangerous"
    BLOCKED = "blocked"


ACTION_RISK: dict[str, RiskLevel] = {}
for _action in (
    "read list_dir glob grep read_process_output browser_snapshot browser_screenshot "
    "computer_screenshot instabox_machine_list instabox_machine_get instabox_port_list "
    "instabox_snapshot_list instabox_snapshot_get web_search web_fetch get_current_time "
    "list_scheduled_tasks list_skills read_skill ask_user plan_mode plan_resume"
).split():
    ACTION_RISK[_action] = RiskLevel.SAFE
for _action in (
    "bash computer_action skill instabox_machine_resume instabox_machine_suspend edit write write_process_input stop_process copy_files browser_run browser_close browser_handoff "
    "computer_click computer_double_click computer_triple_click computer_right_click computer_move computer_drag "
    "computer_scroll computer_type computer_key computer_press computer_wait "
    "instabox_machine_start instabox_machine_stop instabox_machine_restart instabox_snapshot_create "
    "instabox_port_publish instabox_port_set_access instabox_port_rotate_token instabox_port_close "
    "schedule_task delete_scheduled_task generate_image delegate_task activate_skill "
    "write_todos html_preview"
).split():
    ACTION_RISK[_action] = RiskLevel.MUTATING
for _action in (
    "external_directory instabox_machine_create instabox_machine_fork instabox_machine_delete "
    "instabox_snapshot_delete instabox_snapshot_restore instabox_port_public "
    "computer_open_url computer_type_secret"
).split():
    ACTION_RISK[_action] = RiskLevel.DANGEROUS

ALWAYS_CONFIRM = frozenset({"instabox_snapshot_restore"})


def classify(action: str, resource: str) -> RiskLevel:
    if action == "shell":
        return {"normal": RiskLevel.MUTATING, "dangerous": RiskLevel.DANGEROUS,
                "blocked": RiskLevel.BLOCKED}[classify_command(resource)]
    return ACTION_RISK.get(action, RiskLevel.DANGEROUS)


def decide(mode: str, risk: RiskLevel | str, rule_effect: str | None = None,
           *, always_confirm: bool = False) -> str:
    try:
        risk = RiskLevel(risk)
    except (TypeError, ValueError):
        risk = RiskLevel.DANGEROUS
    if risk == RiskLevel.BLOCKED or rule_effect == "deny":
        return "deny"
    if mode == "full_access":
        return "allow"
    if always_confirm:
        return "ask"
    if rule_effect is not None:
        return rule_effect
    if risk == RiskLevel.SAFE:
        return "allow"
    return "ask" if mode == "ask" or risk == RiskLevel.DANGEROUS else "allow"


def _acp_paths(tool_call, cwd):
    locations = tool_call.get("locations")
    if not isinstance(locations, list) or not locations:
        return []
    if any(not isinstance(item, dict) or not isinstance(item.get("path"), str)
           or not item["path"].strip() for item in locations):
        return []
    return [str(comparable_path(os.path.join(cwd, item["path"]))) for item in locations]


def _acp_command(tool_call):
    raw = tool_call.get("rawInput")
    if not isinstance(raw, dict):
        return None
    raw = raw.get("arguments", raw)
    if not isinstance(raw, dict):
        return None
    return next((raw[key] for key in ("command", "CommandLine", "command_line", "cmd")
                 if isinstance(raw.get(key), str) and raw[key].strip()), None)


def classify_acp_tool_call(tool_call, cwd) -> RiskLevel:
    kind = tool_call.get("kind", "other")
    if kind == "execute":
        command = _acp_command(tool_call)
        risk = classify("shell", command) if command else RiskLevel.DANGEROUS
    elif kind in {"read", "search", "think", "fetch"}:
        risk = RiskLevel.SAFE
    elif kind == "edit":
        cwd = str(comparable_path(cwd))
        paths = _acp_paths(tool_call, cwd)
        try:
            inside = bool(paths) and all(os.path.normcase(os.path.commonpath([cwd, path])) == os.path.normcase(cwd)
                                         for path in paths)
        except ValueError:
            inside = False
        risk = RiskLevel.MUTATING if inside else RiskLevel.DANGEROUS
    else:
        risk = RiskLevel.DANGEROUS
    command = _acp_command(tool_call)
    if command and classify_command(command) == "blocked":
        risk = RiskLevel.BLOCKED
    if risk != RiskLevel.BLOCKED and "agy.security.warning" in str(tool_call.get("_meta", {})):
        risk = RiskLevel.DANGEROUS
    return risk


def acp_operations(tool_call, cwd):
    """Normalize every resource; reject incomplete enforcement inputs."""
    cwd = str(comparable_path(cwd))
    kind = tool_call.get("kind", "other")
    risk = classify_acp_tool_call(tool_call, cwd)
    if kind == "execute" or (kind == "other" and _acp_command(tool_call)):
        command = _acp_command(tool_call)
        if command is None:
            raise ValueError("permission_denied: ACP execute request has no command for shell_guard.")
        return [("shell", command, risk)]
    paths = _acp_paths(tool_call, cwd)
    locations = tool_call.get("locations")
    if locations and not paths:
        raise ValueError("permission_denied: ACP request contains invalid resource locations.")
    raw = tool_call.get("rawInput") or {}
    if not paths and isinstance(raw, dict):
        path = next((raw[key] for key in ("path", "file_path", "TargetFile") if isinstance(raw.get(key), str) and raw[key].strip()), None)
        if path:
            paths = [str(comparable_path(os.path.join(cwd, path)))]
    if kind == "think":
        return [("think", "*", risk)]
    if kind == "fetch" and isinstance(raw, dict) and raw.get("url"):
        return [("fetch", str(raw["url"]), risk)]
    if kind == "search" and not paths:
        paths = [cwd]
    if not paths:
        raise ValueError("permission_denied: ACP request has no resources for policy enforcement.")
    return [("edit" if kind in {"edit", "delete", "move"} else kind, path, risk) for path in paths]
