"""Compare exact legacy kernels from a git revision with coding_v2.

Run each measurement in a fresh worker to avoid allocator history bias.
The legacy module is loaded without decorated tool registration functions.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import base64
import json
import os
import platform
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path

import psutil

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))


def legacy_module(revision: str, path: str) -> dict:
    source = subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=REPOSITORY).decode("utf-8")
    parsed = ast.parse(source, filename=path)
    parsed.body = [node for node in parsed.body if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign,
        ast.AnnAssign, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not getattr(node, "decorator_list", [])]
    namespace = {"__name__": "coding_benchmark_legacy"}
    exec(compile(parsed, path, "exec"), namespace)
    return namespace


def command_for(source: str) -> str:
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    argument = f"import base64;exec(base64.b64decode('{encoded}'))"
    if os.name == "nt":
        return f'& "{sys.executable}" -c "{argument}"'
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(argument)}"


async def measure(args, directory: Path) -> dict:
    from agent.bootstrap.container import create_test_container
    from agent.modules.tools.coding.files import read_page
    from agent.modules.tools.coding.models import InvocationContext
    from agent.modules.tools.coding.service import CodingService
    from agent.modules.workspaces import workspace_ref_from_local_path

    old_shell = legacy_module(args.baseline_ref, "agent/modules/tools/builtin/shell/run_bash_tool.py")
    old_files = legacy_module(args.baseline_ref, "agent/modules/workspaces/local_backend.py")
    workspace = directory / "workspace"
    workspace.mkdir()
    container = create_test_container(tmp_path=directory)
    previous = container.activate()
    service = CodingService(directory / "outputs")
    container._coding_service = service
    context = InvocationContext("benchmark", str(workspace), "benchmark")
    container.active_session_registry
    await asyncio.to_thread(lambda: None)
    size = args.size_mib * 1024 * 1024
    target = workspace / "dataset.txt"
    if args.case == "read":
        unit = b"line:" + b"x" * 250 + b"\n"
        with target.open("wb") as handle:
            for _ in range(size // (len(unit) * 4096)):
                handle.write(unit * 4096)
    command = command_for(f"import sys; block=b'x'*65536; out=sys.stdout.buffer;\nfor _ in range({size // 65536}): out.write(block)\nout.flush()")
    process = psutil.Process()
    initial_rss = process.memory_info().rss
    peak_rss = [initial_rss]
    done = threading.Event()
    def sample():
        while not done.wait(0.01):
            peak_rss[0] = max(peak_rss[0], process.memory_info().rss)
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    tracemalloc.start()
    started = time.perf_counter()
    capture_bytes, stored_bytes = None, None
    try:
        if args.case == "shell" and args.worker == "legacy":
            content = await old_shell["_run_local"](command, str(workspace), 120, 120000, [])
        elif args.case == "shell":
            job = await service.processes.start(context, command, workspace, 120, service.shell)
            await job.finished.wait()
            result = await service.processes.observe(job, preview=True)
            content = result.content
            capture_bytes = len(job.head) + len(job.tail)
            stored_bytes = job.stored_bytes
        elif args.worker == "legacy":
            backend = old_files["LocalWorkspaceBackend"](workspace_ref_from_local_path(str(workspace)))
            raw = await backend.read_text("dataset.txt")
            # The original tool paginates after its backend reads the whole file.
            content = "\n".join(raw.splitlines()[:2000])
        else:
            content = (await asyncio.to_thread(read_page, target, 1, 1999))["content"]
        duration = time.perf_counter() - started
        traced_peak = tracemalloc.get_traced_memory()[1]
        peak_rss[0] = max(peak_rss[0], process.memory_info().rss)
        return {"engine": args.worker, "case": args.case, "input_bytes": size,
                "duration_seconds": round(duration, 4), "python_peak_bytes": traced_peak,
                "rss_peak_delta_bytes": max(0, peak_rss[0] - initial_rss),
                "model_content_bytes": len(content.encode("utf-8")),
                "capture_buffer_bytes": capture_bytes, "stored_output_bytes": stored_bytes}
    finally:
        tracemalloc.stop()
        done.set()
        sampler.join(timeout=1)
        await service.processes.close()
        container.deactivate(previous)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=("legacy", "v2"))
    parser.add_argument("--case", choices=("shell", "read"), default="shell")
    parser.add_argument("--size-mib", type=int, default=1)
    parser.add_argument("--sizes", default="1,8,32")
    parser.add_argument("--baseline-ref", default="HEAD")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.worker:
        with tempfile.TemporaryDirectory(prefix="k41-benchmark-") as name:
            print(json.dumps(asyncio.run(measure(args, Path(name)))))
        return
    revision = subprocess.check_output(["git", "rev-parse", args.baseline_ref], cwd=REPOSITORY, text=True).strip()
    measurements = []
    for case in ("shell", "read"):
        for size in map(int, args.sizes.split(",")):
            if size < 1:
                parser.error("Sizes must be positive whole MiB.")
            for engine in ("legacy", "v2"):
                child = [sys.executable, str(Path(__file__).resolve()), "--worker", engine,
                         "--case", case, "--size-mib", str(size), "--baseline-ref", revision]
                completed = subprocess.run(child, cwd=REPOSITORY, capture_output=True, text=True, timeout=180)
                if completed.returncode:
                    raise RuntimeError(completed.stderr)
                row = json.loads(completed.stdout.splitlines()[-1])
                measurements.append(row)
                print(json.dumps(row), flush=True)
    report = {"baseline_revision": revision, "platform": platform.platform(), "python": platform.python_version(),
              "measurements": measurements, "method": "Fresh workers; Python allocation peak and sampled RSS delta; one sample per case. Shell runs the exact legacy kernel; read compares legacy full read plus paging with versioned streaming paging."}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
