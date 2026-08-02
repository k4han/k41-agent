from types import SimpleNamespace

import pytest

import agent.modules.tools.builtin.filesystem.read_file as read_file_module
import agent.modules.tools.builtin.filesystem.write_file as write_file_module


def _runtime(working_dir: str) -> SimpleNamespace:
    return SimpleNamespace(context={"working_dir": working_dir})


@staticmethod
def _write(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestReadFilePaging:
    @pytest.mark.asyncio
    async def test_default_reads_whole_file(self, tmp_path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        _write(sandbox / "a.txt", "line1\nline2\nline3\n")

        result = await read_file_module.read_file.coroutine(
            file_path="a.txt",
            runtime=_runtime(str(sandbox)),
        )

        assert result == "line1\nline2\nline3\n"

    @pytest.mark.asyncio
    async def test_offset_and_limit_return_requested_slice(self, tmp_path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        _write(sandbox / "a.txt", "l1\nl2\nl3\nl4\nl5\n")

        result = await read_file_module.read_file.coroutine(
            file_path="a.txt",
            offset=2,
            limit=2,
            runtime=_runtime(str(sandbox)),
        )

        assert result == "[lines 2-3 of 5]\nl2\nl3"

    @pytest.mark.asyncio
    async def test_limit_only_reads_from_start(self, tmp_path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        _write(sandbox / "a.txt", "l1\nl2\nl3\n")

        result = await read_file_module.read_file.coroutine(
            file_path="a.txt",
            limit=2,
            runtime=_runtime(str(sandbox)),
        )

        assert result == "[lines 1-2 of 3]\nl1\nl2"

    @pytest.mark.asyncio
    async def test_line_numbers_prefixes_each_line(self, tmp_path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        _write(sandbox / "a.txt", "l1\nl2\nl3\n")

        result = await read_file_module.read_file.coroutine(
            file_path="a.txt",
            offset=2,
            line_numbers=True,
            runtime=_runtime(str(sandbox)),
        )

        assert result == "[lines 2-3 of 3]\n2: l2\n3: l3"

    @pytest.mark.asyncio
    async def test_offset_beyond_file_length_reports_no_lines(self, tmp_path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        _write(sandbox / "a.txt", "l1\nl2\n")

        result = await read_file_module.read_file.coroutine(
            file_path="a.txt",
            offset=10,
            runtime=_runtime(str(sandbox)),
        )

        assert "exceeds file length" in result

    @pytest.mark.asyncio
    async def test_binary_file_reported_instead_of_dumped(self, tmp_path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "bin.dat").write_bytes(b"\x00\x01\x02binary")

        result = await read_file_module.read_file.coroutine(
            file_path="bin.dat",
            runtime=_runtime(str(sandbox)),
        )

        assert "Binary file detected" in result


class TestWriteFileAppend:
    @pytest.mark.asyncio
    async def test_append_adds_to_existing_file(self, tmp_path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        _write(sandbox / "a.txt", "first\n")

        result = await write_file_module.write_file.coroutine(
            file_path="a.txt",
            content="second\n",
            append=True,
            runtime=_runtime(str(sandbox)),
        )

        assert "Wrote file" in result
        assert (sandbox / "a.txt").read_text(encoding="utf-8") == "first\nsecond\n"

    @pytest.mark.asyncio
    async def test_append_creates_file_when_missing(self, tmp_path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()

        await write_file_module.write_file.coroutine(
            file_path="a.txt",
            content="hello\n",
            append=True,
            runtime=_runtime(str(sandbox)),
        )

        assert (sandbox / "a.txt").read_text(encoding="utf-8") == "hello\n"

    @pytest.mark.asyncio
    async def test_replace_overwrites_content(self, tmp_path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        _write(sandbox / "a.txt", "old\n")

        await write_file_module.write_file.coroutine(
            file_path="a.txt",
            content="new\n",
            runtime=_runtime(str(sandbox)),
        )

        assert (sandbox / "a.txt").read_text(encoding="utf-8") == "new\n"

    @pytest.mark.asyncio
    async def test_replace_does_not_leave_temp_files(self, tmp_path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()

        await write_file_module.write_file.coroutine(
            file_path="a.txt",
            content="data",
            runtime=_runtime(str(sandbox)),
        )

        leftovers = list(sandbox.glob("*.tmp"))
        assert leftovers == []
