from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
import pytest

from agent.modules.workflows.message_history import normalize_messages_for_chat_model


@pytest.mark.parametrize("legacy", [False, True])
def test_coding_diff_is_removed_at_model_boundary_without_changing_ui_history(legacy):
    from agent.modules.tools.coding.models import ToolResult
    from agent.modules.conversations.compaction import _format_messages_for_summary
    from agent.modules.conversations.history import _serialize_thread_messages

    summary = "Changed source.txt: +1/-0; version=version-1"
    diff = "--- source.txt\n+++ source.txt\n@@ -0,0 +1 @@\n+submitted-content\n"
    display = summary + "\n" + diff
    result = ToolResult(data={"path": "source.txt", "additions": 1, "deletions": 0,
                             "version": "version-1", "diff": diff},
                        content=display if legacy else summary,
                        display_content=None if legacy else display)
    message = ToolMessage(content=result.content, name="write_file", tool_call_id="coding-call",
                          artifact=result.model_dump(exclude={"content"}), id="coding-result")
    normalized = normalize_messages_for_chat_model([message])[0]
    assert normalized.content == summary
    assert normalized.artifact is None
    assert normalized.tool_call_id == message.tool_call_id and normalized.id == message.id
    assert "+submitted-content" in _serialize_thread_messages([message])[0]["content"]
    assert "submitted-content" not in _format_messages_for_summary([message])
    assert message.artifact["data"]["diff"] == diff


def test_explicit_read_of_diff_is_preserved_at_model_boundary():
    from agent.modules.tools.coding.models import ToolResult

    content = "1: +submitted-content\n[next_offset=None]"
    result = ToolResult(content=content)
    message = ToolMessage(content=content, name="read_tool_output", tool_call_id="read-call",
                          artifact=result.model_dump(exclude={"content"}))
    normalized = normalize_messages_for_chat_model([message])[0]
    assert normalized.content == content
    assert normalized.artifact is None


def test_serialized_coding_result_from_older_checkpoint_only_sends_receipt():
    import json
    from agent.modules.tools.coding.models import ToolResult
    from agent.modules.conversations.compaction import _format_messages_for_summary

    summary = "Changed source.txt: +1/-0; version=version-1"
    result = ToolResult(content=summary + "\n+submitted-content",
                        data={"path": "source.txt", "additions": 1, "deletions": 0,
                              "version": "version-1", "diff": "+submitted-content"})
    serialized = json.dumps(result.model_dump())
    message = ToolMessage(content=serialized, name="write_file", tool_call_id="old-call")
    normalized = normalize_messages_for_chat_model([message])[0]
    assert normalized.content == summary and normalized.artifact is None
    assert "submitted-content" not in _format_messages_for_summary([message])
    assert message.content == serialized


@pytest.mark.parametrize("content", [
    "{invalid json",
    '{"diff": "requested diff"}',
    '{"status": "custom", "data": {}, "output_refs": [], "capture_truncated": false, "output_truncated": false}',
])
def test_plain_json_tool_output_is_not_treated_as_a_coding_result(content):
    message = ToolMessage(content=content, name="exec_command", tool_call_id="plain-call")
    assert normalize_messages_for_chat_model([message])[0].content == content


def test_normalize_human_text_blocks_to_string_preserves_metadata():
    message = HumanMessage(
        content=[
            {"type": "text", "text": "Review attachments"},
            {"type": "text", "text": "Attached text file: auth.py"},
        ],
        id="user-1",
        additional_kwargs={"attachments": [{"name": "auth.py", "kind": "text"}]},
    )

    normalized = normalize_messages_for_chat_model([message])

    assert normalized[0].content == "Review attachments\n\nAttached text file: auth.py"
    assert normalized[0].id == "user-1"
    assert normalized[0].additional_kwargs == message.additional_kwargs


def test_normalize_human_multimodal_blocks_preserves_list_content():
    content = [
        {"type": "text", "text": "Review screenshot"},
        {"type": "image", "base64": "YWJjZA==", "mime_type": "image/png"},
    ]
    message = HumanMessage(content=content)

    normalized = normalize_messages_for_chat_model([message])

    assert normalized[0].content == content


def test_normalize_tool_text_blocks_to_string_preserves_tool_metadata():
    artifact = {"raw": [{"type": "text", "text": "Tool output"}]}
    message = ToolMessage(
        content=[{"type": "text", "text": "Tool output"}],
        tool_call_id="call-1",
        name="mcp__demo__tool",
        artifact=artifact,
        status="success",
    )

    normalized = normalize_messages_for_chat_model([message])

    assert normalized[0].content == "Tool output"
    assert normalized[0].tool_call_id == "call-1"
    assert normalized[0].name == "mcp__demo__tool"
    assert normalized[0].artifact == artifact
    assert normalized[0].status == "success"


def test_normalize_tool_image_blocks_preserves_multimodal_payload():
    artifact = {"raw": "kept outside model payload"}
    content = [
        {"type": "text", "text": "Screenshot 'wiki_mat_troi' taken at 0x0"},
        {
            "type": "image",
            "base64": "raw-image-data",
            "mime_type": "image/png",
            "id": "lc_123",
        },
    ]
    message = ToolMessage(
        content=content,
        tool_call_id="call-1",
        name="mcp__sanbox__browser_screenshot",
        artifact=artifact,
    )

    normalized = normalize_messages_for_chat_model([message])

    assert normalized[0].content == content
    assert normalized[0].tool_call_id == "call-1"
    assert normalized[0].name == "mcp__sanbox__browser_screenshot"
    assert normalized[0].artifact == artifact


def test_normalize_tool_non_text_only_blocks_to_placeholder_string():
    message = ToolMessage(
        content=[
            {
                "type": "file",
                "url": "https://example.test/report.pdf",
                "mimeType": "application/pdf",
            }
        ],
        tool_call_id="call-1",
    )

    normalized = normalize_messages_for_chat_model([message])

    assert normalized[0].content == (
        "[file content omitted: mime_type=application/pdf, "
        "url=https://example.test/report.pdf]"
    )


def test_normalize_assistant_blocks_still_strips_non_text_blocks():
    message = AIMessage(
        content=[
            {"type": "thinking", "text": "hidden"},
            {"type": "text", "text": "Visible answer"},
        ]
    )

    normalized = normalize_messages_for_chat_model([message])

    assert normalized[0].content == "Visible answer"


def test_normalize_tool_mixed_image_and_file_omits_non_text():
    message = ToolMessage(
        content=[
            {"type": "text", "text": "See screenshot"},
            {"type": "image", "base64": "YWJjZA==", "mime_type": "image/png"},
            {
                "type": "file",
                "url": "https://example.test/report.pdf",
                "mimeType": "application/pdf",
            },
        ],
        tool_call_id="call-1",
    )

    normalized = normalize_messages_for_chat_model([message])

    content = normalized[0].content
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "See screenshot"}
    assert content[1]["type"] == "image"
    assert content[2]["type"] == "text"
    assert "file content omitted" in content[2]["text"]


def test_normalize_tool_image_url_blocks_preserved():
    content = [
        {"type": "text", "text": "Look"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]
    message = ToolMessage(content=content, tool_call_id="call-1")

    normalized = normalize_messages_for_chat_model([message])

    assert normalized[0].content == content
