from agent.modules.projects.models import Project, ProjectWorkspace, WorkspaceRuntime


def __getattr__(name):
    from importlib import import_module
    modules = {
        "get_project_workspace_service": "workspaces", "ProjectWorkspaceService": "workspaces",
        "ProjectWorkspaceError": "workspaces", "source_key": "workspaces", "managed_backend": "backend",
        "get_project_instructions": "resources", "save_project_instructions": "resources",
        "register_thread_project": "repository", "cursor_condition": "repository", "encode_cursor": "repository",
    }
    if name not in modules:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"{__name__}.{modules[name]}"), name)


from agent.modules.projects.service import add_project, get_project, import_existing_projects, list_projects, update_project

__all__ = ["Project", "ProjectWorkspace", "WorkspaceRuntime", "add_project", "get_project", "import_existing_projects", "list_projects", "update_project"]
__all__ += ["get_project_workspace_service", "ProjectWorkspaceService", "ProjectWorkspaceError", "source_key", "managed_backend",
            "get_project_instructions", "save_project_instructions", "register_thread_project", "cursor_condition", "encode_cursor"]
