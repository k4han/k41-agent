import sys
import tempfile
from pathlib import Path
from uuid import uuid4


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
