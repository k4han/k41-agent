"""Bootstrap imports must not construct an application or load HTTP routes."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_script(script, tmp_path):
    env = os.environ.copy()
    env["K41_AGENT_HOME"] = str(tmp_path)
    env["AGENT_HOME"] = str(tmp_path)
    script = (
        "import os\n"
        "from pathlib import Path\n"
        "Path.home = classmethod(lambda cls: cls(os.environ['K41_AGENT_HOME']))\n"
    ) + textwrap.dedent(script)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize("statement", [
    "from agent.bootstrap.app import create_app, main, run",
    "from agent.bootstrap import create_app, main, run",
    "import main",
])
def test_entrypoint_imports_do_not_initialize_runtime(statement, tmp_path):
    run_script(f"""
        import sys
        import agent.bootstrap.container as containers

        def unexpected_container(*args, **kwargs):
            raise AssertionError("Import must not construct a container")

        containers.create_app_container = unexpected_container
        {statement}

        assert containers.get_active_container() is None
        assert containers._default_container is None
        assert "agent.bootstrap.runtime" not in sys.modules
        assert not any(name.startswith("agent.delivery.http") for name in sys.modules)
    """, tmp_path)


def test_explicit_factory_does_not_create_default_app(tmp_path):
    run_script("""
        import importlib
        from agent.bootstrap.container import create_test_container, get_active_container

        module = importlib.import_module("agent.bootstrap.app")
        container = create_test_container()
        app = module.create_app(container=container)

        assert app.state.container is container
        assert get_active_container() is container
        assert module._default_app is None
        assert module._default_container is None
    """, tmp_path)


def test_headless_run_does_not_load_http_routes(tmp_path):
    run_script("""
        import importlib
        import os
        from pathlib import Path
        import sys
        from agent.bootstrap.container import create_test_container
        from agent.bootstrap.runtime import AppRuntime

        module = importlib.import_module("agent.bootstrap.app")
        container = create_test_container()
        calls = []

        async def startup(self):
            assert self.container is container
            calls.append("startup")

        async def shutdown(self):
            calls.append("shutdown")

        def unexpected_app(*args, **kwargs):
            raise AssertionError("Headless startup must not create a web application")

        AppRuntime.startup = startup
        AppRuntime.shutdown = shutdown
        module._default_container = container
        module.create_app = unexpected_app
        module.SHUTDOWN_SIGNAL = Path(os.environ["K41_AGENT_HOME"]) / "shutdown.signal"
        module.SHUTDOWN_SIGNAL.write_text(str(os.getpid()), encoding="utf-8")
        module.run()

        assert calls == ["startup", "shutdown"]
        assert not module.SHUTDOWN_SIGNAL.exists()
        assert module._default_app is None
        assert not any(name.startswith("agent.delivery.http") for name in sys.modules)
    """, tmp_path)
