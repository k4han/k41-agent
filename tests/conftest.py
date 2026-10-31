import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]

# Keep test databases and scratch files on the OS temp volume. Repositories are
# often stored on slow or network-backed drives where every SQLite fsync costs
# hundreds of milliseconds; the suite becomes an order of magnitude slower.
_DEFAULT_TEMP_ROOT = Path(tempfile.gettempdir()) / "k41-pytest"
PYTEST_TEMP_ROOT = Path(
    os.environ.get("K41_PYTEST_TEMP", os.fspath(_DEFAULT_TEMP_ROOT))
).expanduser()
PYTEST_RUNTIME_TEMP = PYTEST_TEMP_ROOT / "runtime"
PYTEST_BASETEMP_ROOT = PYTEST_TEMP_ROOT / "sessions"
KEEP_SESSION_RUNS = 5
RUNTIME_TEMP_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _prune_session_runs(root: Path, keep: int) -> None:
    """Remove old pytest basetemp directories, keeping the newest ones."""
    try:
        runs = [item for item in root.iterdir() if item.is_dir()]
        runs.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        return
    for stale in runs[keep:]:
        shutil.rmtree(stale, ignore_errors=True)


def _prune_runtime_temp(root: Path, max_age_seconds: float) -> None:
    """Remove stale scratch directories left behind by interrupted runs."""
    cutoff = time.time() - max_age_seconds
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


def pytest_configure(config) -> None:
    PYTEST_RUNTIME_TEMP.mkdir(parents=True, exist_ok=True)
    PYTEST_BASETEMP_ROOT.mkdir(parents=True, exist_ok=True)
    _prune_session_runs(PYTEST_BASETEMP_ROOT, KEEP_SESSION_RUNS)
    _prune_runtime_temp(PYTEST_RUNTIME_TEMP, RUNTIME_TEMP_MAX_AGE_SECONDS)

    tempfile.tempdir = str(PYTEST_RUNTIME_TEMP)

    if getattr(config.option, "basetemp", None) is None:
        config.option.basetemp = str(PYTEST_BASETEMP_ROOT / f"run-{uuid4()}")


def pytest_report_header(config) -> str | None:
    if not config.option.verbose:
        return None
    return f"pytest temp root: {PYTEST_TEMP_ROOT}"


@pytest.fixture(scope="session")
def sqlite_schema_template(tmp_path_factory):
    """Create the application SQLite schema once and reuse it across tests."""
    from agent.shared.infrastructure.db import Base, load_orm_models
    from agent.shared.infrastructure.db.engine import create_tables

    template_path = tmp_path_factory.mktemp("sqlite-schema") / "template.db"
    load_orm_models()
    create_tables(f"sqlite:///{template_path.as_posix()}", metadata=Base.metadata)
    return template_path


@pytest.fixture(autouse=True)
def reset_system_prompt_cache():
    """Keep the process-wide system prompt cache from leaking across tests."""
    from agent.modules.workflows.system_prompt_cache import (
        invalidate_system_prompt_cache,
    )
    from agent.shared.infrastructure.revisions import reset_revisions

    invalidate_system_prompt_cache()
    reset_revisions()
    yield
    invalidate_system_prompt_cache()
    reset_revisions()


@pytest.fixture
def isolated_container(tmp_path, sqlite_schema_template):
    """Build isolated AppContainer activated for the current test."""
    from agent.bootstrap.container import clear_active_container, create_test_container

    database_path = tmp_path / "test-agent.db"
    if not database_path.exists():
        shutil.copyfile(sqlite_schema_template, database_path)

    container = create_test_container(tmp_path=tmp_path)
    previous = container.activate()
    try:
        yield container
    finally:
        container.deactivate(previous)
        clear_active_container()


@pytest.fixture
def activate_container():
    """Activate an explicit AppContainer for the current test."""
    from agent.bootstrap.container import set_active_container

    def _activate(container):
        set_active_container(container)
        return container

    yield _activate


@pytest.fixture(autouse=True)
def _clear_active_container_after_test():
    """Guarantee no container leaks across tests."""
    yield
    try:
        from agent.bootstrap.container import clear_active_container

        clear_active_container()
    except Exception:
        pass
