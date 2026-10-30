from agent.modules.projects.models import Project, ProjectSecret, ProjectWorkspace, WorkspaceRuntime


def __getattr__(name):
    from importlib import import_module
    modules = {
        "get_project_workspace_service": "workspaces", "ProjectWorkspaceService": "workspaces",
        "ProjectWorkspaceError": "workspaces", "source_key": "workspaces", "managed_backend": "backend",
        "get_project_instructions": "resources", "save_project_instructions": "resources",
        "register_thread_project": "repository", "cursor_condition": "repository", "encode_cursor": "repository",
        "storage_timestamp": "workspaces", "_session": "workspaces",
        "LocalProjectStorage": "storage",
        "ACTIVE_PHASES": "runtime_switch", "transition_summary": "runtime_switch",
        "apply_workspace_revision": "local_sync", "diff_workspace_revision": "local_sync",
        "has_conflicting_revision": "local_sync", "read_workspace_snapshot": "local_sync",
        "_apply": "local_sync", "_capture": "local_sync", "_plan": "local_sync",
        "_recover_replacement": "local_sync", "_run_writer": "local_sync", "_same_entry": "local_sync",
        "_sweep_temporary": "local_sync", "_verify": "local_sync", "plan_changes": "local_sync",
    }
    if name not in modules:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"{__name__}.{modules[name]}"), name)


from agent.modules.projects.service import add_project, get_project, import_existing_projects, list_projects, update_project

__all__ = ["Project", "ProjectSecret", "ProjectWorkspace", "WorkspaceRuntime", "add_project", "get_project", "import_existing_projects", "list_projects", "update_project"]
__all__ += ["get_project_workspace_service", "ProjectWorkspaceService", "ProjectWorkspaceError", "source_key", "managed_backend",
            "get_project_instructions", "save_project_instructions", "register_thread_project", "cursor_condition", "encode_cursor",
            "storage_timestamp", "_session", "LocalProjectStorage", "ACTIVE_PHASES", "transition_summary",
            "apply_workspace_revision", "diff_workspace_revision", "has_conflicting_revision", "read_workspace_snapshot",
            "_apply", "_capture", "_plan", "_recover_replacement", "_run_writer", "_same_entry", "_sweep_temporary", "_verify", "plan_changes"]
