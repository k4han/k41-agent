import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent.modules.tools.builtin.image.generate_image as generate_image_module
import agent.modules.tools.runtime.thread_storage as thread_storage
from agent.modules.workspaces import WorkspaceRef, derive_workspace_scope


def _runtime(working_dir: str, thread_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        context={"working_dir": working_dir},
        config={"configurable": {"thread_id": thread_id}},
    )


class _FakeImages:
    def generate(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    b64_json=base64.b64encode(b"image-bytes").decode("ascii"),
                )
            ]
        )


class _FakeOpenAI:
    def __init__(self, **_: object) -> None:
        self.images = _FakeImages()


def test_generate_image_saves_to_workspace_storage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(thread_storage, "THREAD_STORAGE_BASE_DIR", tmp_path / "storage")
    monkeypatch.setattr(generate_image_module, "OpenAI", _FakeOpenAI)
    monkeypatch.setattr(
        generate_image_module,
        "_resolve_provider",
        lambda _provider_name: SimpleNamespace(api_key="key", base_url=None),
    )

    tool = generate_image_module._build_generate_image_tool({})
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = tool.func(
        "A Small Cat",
        _runtime(str(workspace), "chat_user:sub:worker:abcd1234"),
    )

    assert result.startswith(
        "Generated image saved to: .k41-agent/generated-images/a-small-cat-"
    )
    filename = result.rsplit("/", 1)[-1]
    scope = derive_workspace_scope(WorkspaceRef(locator=str(workspace.resolve())))
    path = thread_storage.generated_images_dir_for_workspace(
        scope,
        create=False,
    ) / filename
    assert path.read_bytes() == b"image-bytes"


def test_output_target_falls_back_when_workspace_resolution_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "generated-images"
    monkeypatch.setattr(generate_image_module, "GENERATED_IMAGES_DIR", output_dir)

    def raise_workspace_error(_: object) -> WorkspaceRef:
        raise ValueError("workspace unavailable")

    monkeypatch.setattr(generate_image_module, "get_workspace", raise_workspace_error)

    target, thread_scoped = generate_image_module._output_target(
        _runtime(str(tmp_path / "workspace"), "chat_user:sub:worker:abcd1234")
    )

    assert target == output_dir
    assert thread_scoped is False
    assert output_dir.is_dir()
