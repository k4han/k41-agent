from __future__ import annotations

import shutil
import subprocess
import sys
from uuid import uuid4

import pytest

from agent.bootstrap.service import is_systemd_available


@pytest.mark.skipif(sys.platform != "linux", reason="Requires a Linux host with a systemd user manager.")
@pytest.mark.parametrize("suffix", ["", ".scope"])
def test_systemd_run_accepts_update_scope_name(suffix: str) -> None:
    available, reason = is_systemd_available()
    systemd_run = shutil.which("systemd-run")
    if not available or systemd_run is None:
        pytest.skip(reason if not available else "systemd-run is not installed.")
    unit_name = f"k41-agent-update-test-{uuid4().hex}"
    result = subprocess.run(
        [
            systemd_run, "--user", "--scope", "--quiet", f"--unit={unit_name}{suffix}", "--",
            sys.executable, "-c", "from pathlib import Path; print(Path('/proc/self/cgroup').read_text())",
        ],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert f"{unit_name}.scope" in result.stdout
