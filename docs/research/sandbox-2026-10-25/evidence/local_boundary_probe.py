import asyncio
import base64
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path.cwd()))
from agent.modules.tools.coding.contracts import CodingError, InvocationContext
from agent.modules.tools.coding.engine import CodingEngine
from agent.modules.tools.coding.files import FileService
from agent.modules.tools.coding.models import PermissionRule
from agent.modules.tools.coding.permissions import Permissions
from agent.modules.tools.coding.processes import ProcessManager, resolve_shell
from agent.modules.tools.coding.storage import OutputStore

async def main():
    root = Path(tempfile.mkdtemp(prefix="kaka-boundary-probe-"))
    workspace = root / "workspace"
    workspace.mkdir()
    outside = root / "outside-canary.txt"
    outside.write_text("SYNTHETIC_CANARY_ONLY", encoding="utf-8")
    control = root / "controller-storage"
    store = OutputStore(control)
    engine = CodingEngine()
    engine.storage = store
    engine.permissions = Permissions(store)
    engine.files = FileService(engine.permissions)
    engine.processes = ProcessManager(store, register_sessions=False)
    engine.shell = resolve_shell()
    context = InvocationContext("probe", str(workspace), "probe-thread", permission_rules=(
        PermissionRule(action="external_directory", effect="deny"),
        PermissionRule(action="read", resource=str(outside), effect="deny"),
    ))
    result = {"platform": sys.platform, "synthetic_files_only": True}
    try:
        await engine.prepare("read", {"path": str(outside)}, context)
        result["file_tool_read"] = "allowed"
    except CodingError as exc:
        result["file_tool_read"] = exc.code
    marker = control / "synthetic-write-marker.txt"
    source = "from pathlib import Path; print(Path(" + repr(str(outside)) + ").read_text()); Path(" + repr(str(marker)) + ").write_text('SYNTHETIC_WRITE_ONLY')"
    payload = base64.b64encode(source.encode()).decode()
    invocation = "import base64;exec(base64.b64decode('" + payload + "'))"
    command = '& "' + sys.executable + '" -c "' + invocation + '"' if os.name == "nt" else None
    if command is None:
        import shlex
        command = shlex.quote(sys.executable) + " -c " + shlex.quote(invocation)
    values = {"command": command, "workdir": str(workspace), "timeout_seconds": 15, "yield_time_ms": 1000}
    try:
        await engine.prepare("bash", values, context)
        execution = await engine.execute("bash", values, context)
        job = engine.processes.get(context, execution.data["process_id"])
        await job.finished.wait()
        result["shell_exit_code"] = job.process.poll()
        result["shell_read_outside_workspace"] = "SYNTHETIC_CANARY_ONLY" in job.retained_plain()
        result["shell_wrote_controller_canary"] = marker.exists()
    finally:
        await engine.processes.close()
        await store.close()
    print(json.dumps(result, indent=2))

asyncio.run(main())
