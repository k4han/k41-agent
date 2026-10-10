"""Clean process presentation preserves source bytes and repeatable paging."""

from types import SimpleNamespace

import pytest

from agent.modules.tools.coding.contracts import InvocationContext
from agent.modules.tools.coding.processes import ProcessJob, ProcessManager
from agent.modules.tools.coding.storage import OutputStore


@pytest.fixture
def output_job(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = InvocationContext("default", str(workspace), "output-thread")
    storage = OutputStore(tmp_path / "storage")
    manager = ProcessManager(storage, register_sessions=False)
    process = SimpleNamespace(returncode=0)
    process.poll = lambda: process.returncode
    job = ProcessJob("output-process", context.owner, context.thread_id, str(workspace),
                     process, None, None, 120, storage, context, status="completed")
    job.finished.set()
    return manager, job


@pytest.mark.asyncio
async def test_fragmented_stdout_json_and_stderr_are_not_framed(output_job):
    manager, job = output_job
    for part in ['{"result":', '"[stdout bytes=2] literal"', '}\n']:
        job.append(part)
    job.append("warning detail\n", "stderr")
    result = await manager.observe(job)
    expected = '{"result":"[stdout bytes=2] literal"}\nwarning detail\n'
    assert result.content == expected + "\n\nCommand exited with code 0."
    assert job.retained_plain() == expected
    assert result.data["cursor"] == len(expected.encode())
    assert "process_id=" not in result.content and "next_cursor=" not in result.content


@pytest.mark.asyncio
async def test_terminal_chrome_is_cleaned_without_changing_retained_source(output_job):
    manager, job = output_job
    source = "\x1b]0;Window title\x07\x1b[32mok\x1b[0m\r\nprogress 10%\rprogress 100%\r\nx\bz\n"
    for part in [source[:4], source[4:18], source[18:]]:
        job.append(part)
    result = await manager.observe(job)
    assert result.content == "ok\nprogress 100%\nz\n\n\nCommand exited with code 0."
    assert job.retained_plain() == source
    assert result.data["cursor"] == len(source.encode())


@pytest.mark.asyncio
async def test_paging_preserves_unicode_crlf_and_escape_boundaries(output_job, monkeypatch):
    manager, job = output_job
    monkeypatch.setattr("agent.modules.tools.coding.processes.MAX_MODEL_BYTES", 1060)
    source = "a" * 34 + "\x1b[32m" + "\U0001f600" * 6 + "\x1b[0m\r\n" + "b" * 35 + "\r\nend"
    job.append(source)
    assert job.path is not None and job.path.read_bytes() == source.encode()
    cursor = 0
    pieces = []
    while cursor < job.stored_bytes:
        result = await manager.observe(job, cursor=cursor)
        repeated = await manager.observe(job, cursor=cursor)
        assert result.content == repeated.content
        assert result.data["cursor"] == repeated.data["cursor"] > cursor
        pieces.append(result.content.split("\n\nCommand exited with code ", 1)[0])
        cursor = result.data["cursor"]
    assert cursor == len(source.encode())
    assert "".join(pieces) == "a" * 34 + "\U0001f600" * 6 + "\n" + "b" * 35 + "\nend"


@pytest.mark.asyncio
async def test_running_output_keeps_model_cursor_out_of_display(output_job):
    manager, job = output_job
    job.finished.clear()
    job.status = "running"
    job.process.returncode = None
    job.append("ready\r\n")
    result = await manager.observe(job)
    assert result.status == "running"
    assert f"Process {job.id}: running; next_cursor=7." in result.content
    assert result.display_content == "ready\n\n\nProcess is still running."
    assert "next_cursor" not in result.display_content and job.id not in result.display_content


@pytest.mark.asyncio
@pytest.mark.parametrize("state, exit_code, outcome, error", [
    ("completed", 0, "Command exited with code 0.", None),
    ("completed", 2, "Command exited with code 2.", "nonzero_exit"),
    ("completed", -9, "Command exited with code -9.", "nonzero_exit"),
    ("timeout", -9, "Command timed out.", "timeout"),
    ("cancelled", -9, "Command was cancelled.", "cancelled"),
])
async def test_empty_output_and_command_outcome(output_job, state, exit_code, outcome, error):
    manager, job = output_job
    job.status = state
    job.process.returncode = exit_code
    result = await manager.observe(job)
    assert result.content == f"(no output)\n\n{outcome}"
    assert result.status == ("error" if error else "success")
    assert (result.error.code if result.error else None) == error


@pytest.mark.asyncio
async def test_large_output_keeps_continuation_in_model_and_source_in_file(output_job):
    manager, job = output_job
    source = "\x1b[32mline\x1b[0m\r\n" * 5000
    job.append(source)
    result = await manager.observe(job, preview=True)
    assert result.output_truncated and result.data["tail_preview"]
    assert f"read_process_output process_id={job.id} cursor={result.data['cursor']}" in result.content
    assert "read_process_output" not in result.display_content
    assert "more output available" in result.display_content
    assert job.path.read_bytes() == source.encode()
    assert "\x1b" not in result.content and "\r" not in result.content


@pytest.mark.asyncio
async def test_tail_preview_does_not_start_inside_an_ansi_escape(output_job):
    manager, job = output_job
    source = "x" * 30000 + "\x1b[31m" + "y" * 24570 + "\x1b[0m"
    job.append(source)
    result = await manager.observe(job, preview=True)
    tail = result.content.split("[tail preview; subsequent cursor reads continue through the retained output]\n", 1)[1]
    tail = tail.split("\n\nCommand exited with code ", 1)[0]
    assert tail == "x" * 6 + "y" * 24570
    assert job.path.read_bytes() == source.encode()
