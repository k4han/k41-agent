import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTEST_TEMP_ROOT = REPO_ROOT / ".tmp_pytest"
PYTEST_RUNTIME_TEMP = PYTEST_TEMP_ROOT / "runtime"
PYTEST_BASETEMP_ROOT = PYTEST_TEMP_ROOT / "sessions"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def pytest_configure(config) -> None:
    PYTEST_RUNTIME_TEMP.mkdir(parents=True, exist_ok=True)
    PYTEST_BASETEMP_ROOT.mkdir(parents=True, exist_ok=True)

    runtime_temp = str(PYTEST_RUNTIME_TEMP)
    tempfile.tempdir = runtime_temp

    if getattr(config.option, "basetemp", None) is None:
        config.option.basetemp = str(PYTEST_BASETEMP_ROOT / f"run-{uuid4()}")


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
