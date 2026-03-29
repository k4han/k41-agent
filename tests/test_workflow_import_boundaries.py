from pathlib import Path


def test_workflows_do_not_import_legacy_persistence() -> None:
    workflow_root = Path("agent/modules/workflows")
    offenders: list[str] = []

    for path in workflow_root.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        if "agent.persistence" in content:
            offenders.append(path.as_posix())

    assert offenders == []
