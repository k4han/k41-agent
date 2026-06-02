from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.modules.workflows.message_history import normalize_messages_for_chat_model


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


def test_normalize_tool_image_blocks_to_string_omits_binary_payload():
    artifact = {"raw": "kept outside model payload"}
    message = ToolMessage(
        content=[
            {"type": "text", "text": "Screenshot 'wiki_mat_troi' taken at 0x0"},
            {
                "type": "image",
                "base64": "raw-image-data",
                "mime_type": "image/png",
                "id": "lc_123",
            },
        ],
        tool_call_id="call-1",
        name="mcp__sanbox__browser_screenshot",
        artifact=artifact,
    )

    normalized = normalize_messages_for_chat_model([message])

    assert normalized[0].content == (
        "Screenshot 'wiki_mat_troi' taken at 0x0\n\n"
        "[image content omitted: mime_type=image/png, source=base64, id=lc_123]"
    )
    assert "raw-image-data" not in normalized[0].content
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
