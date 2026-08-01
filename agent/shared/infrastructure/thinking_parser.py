"""Thinking-aware content parsing utilities.

This module centralises detection of "thinking" content that some chat models
emit alongside their final answer. It supports two delivery styles:

* **Structured part** - provider returns a list of parts with
  ``{"type": "thinking", "thinking": "..."}`` entries (Anthropic,
  Google Gemini, OpenAI o-series, etc.).
* **Inline tag** - provider embeds the reasoning inside the visible text
  using ``<thinking>...</thinking>`` style tags. This is increasingly common
  for smaller or open-source models that simply do not expose a structured
  channel.

Both forms should resolve to the same output contract so the rest of the
runtime only has to deal with a single shape:

>>> ContentWithThinking(text="visible answer", thinking="reasoning")

A streaming variant (:class:`ThinkingTagStreamParser`) handles tag
boundaries that may be split across LangChain ``AIMessageChunk`` chunks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


# Recognised thinking tag markers.  ``think`` covers Qwen3 / DeepSeek-R1
# distilled output, ``thought``/``reflection``/``reflect`` cover a handful
# of open-source chat models, and ``inner_monologue`` / ``plan`` cover
# tool-style CoT markers. Extend this tuple when a new style shows up
# rather than hard-coding the regex inline elsewhere.
THINKING_TAG_ALIASES = (
    "thinking",
    "think",
    "thought",
    "thoughts",
    "reflection",
    "reflect",
    "reason",
    "reasoning",
    "inner_monologue",
    "plan",
)
_THINKING_OPEN_TAG_PATTERN = re.compile(
    r"<\s*(?:" + "|".join(THINKING_TAG_ALIASES) + r")\s*>",
    re.IGNORECASE,
)
_THINKING_CLOSE_TAG_PATTERN = re.compile(
    r"</\s*(?:" + "|".join(THINKING_TAG_ALIASES) + r")\s*>",
    re.IGNORECASE,
)
_THINKING_PART_TYPES = frozenset({"thinking", "reasoning", "reasoning_content"})


@dataclass(frozen=True)
class ContentWithThinking:
    """A pair of visible text and thinking content extracted from a model reply.

    Either field may be empty when the source did not include that kind of
    content. ``text`` is guaranteed to contain no ``<thinking>...</thinking>``
    style markers so it can be safely rendered to end users.
    """

    text: str
    thinking: str

    def has_thinking(self) -> bool:
        """Return ``True`` if any thinking content was extracted."""
        return bool(self.thinking)

    def has_visible_text(self) -> bool:
        """Return ``True`` if any visible text was extracted."""
        return bool(self.text)


def strip_thinking_tags(text: str) -> str:
    """Remove inline thinking tag content from ``text``.

    Only the wrapper tags (and the reasoning enclosed inside them) are
    removed; surrounding text is left untouched.  Whitespace around the
    stripped block is collapsed so two adjacent visible pieces do not end up
    with double spaces.

    When the source has no matching close tag (only an opening fragment),
    the original text is returned unchanged so a malformed-but-not-thinking
    sequence like ``hello <thinking>x`` is preserved verbatim.
    """
    if not text or _THINKING_OPEN_TAG_PATTERN.search(text) is None:
        return text
    if _THINKING_CLOSE_TAG_PATTERN.search(text) is None:
        return text

    visible, _, retained = _split_text_by_thinking_tags(text, partial=False)
    return visible + retained


def extract_thinking_from_text(text: str) -> str:
    """Extract the thinking content encoded inside ``<thinking>`` tags.

    Tags themselves are stripped - only the reasoning text itself is
    returned. When multiple blocks are present (e.g. the model re-entered a
    thinking block), the pieces are concatenated with newlines between them.
    """
    if not text or _THINKING_OPEN_TAG_PATTERN.search(text) is None:
        return ""

    _, thinking, _ = _split_text_by_thinking_tags(text, partial=False)
    return thinking


@dataclass
class ThinkingTagStreamParser:
    """Stateful parser that splits a stream of text into visible vs thinking parts.

    LangChain emits ``AIMessageChunk`` deltas whose concatenated string may
    split a ``<thinking>`` tag across two chunks ("``Hello <think``" + "``ing>...``").
    A naive regex applied per chunk would leak the open/close tag fragments
    into the visible stream.

    This parser keeps the smallest unread suffix around whenever the
    currently-incomplete region could still grow into a tag. The carry is
    drained on each :meth:`flush` once the stream finishes so nothing is
    silently dropped.
    """

    _carry: str = ""
    _inside: bool = False

    def feed(self, chunk: str) -> ContentWithThinking:
        """Consume the next chunk and return the (text, thinking) delta.

        The delta covers everything that can be safely emitted given this
        chunk alone; incompletely-arrived tag fragments are buffered for the
        next call.
        """
        combined = self._carry + chunk
        self._carry = ""

        if not combined:
            return ContentWithThinking(text="", thinking="")

        visible: list[str] = []
        thinking: list[str] = []

        pos = 0
        length = len(combined)

        while pos < length:
            if self._inside:
                close_match = _THINKING_CLOSE_TAG_PATTERN.search(combined, pos)
                if close_match is not None:
                    thinking.append(combined[pos:close_match.start()])
                    pos = close_match.end()
                    self._inside = False
                    continue

                # At least one ``</`` lives at or after ``pos``; everything
                # preceding it is safe to emit while the trailing fragment
                # may still grow into the full ``</thinking>`` close tag.
                last_close = combined.rfind("</", pos)
                if last_close > pos:
                    thinking.append(combined[pos:last_close])
                    self._carry = combined[last_close:]
                else:
                    # The suffix already starts with ``</`` and must wait.
                    self._carry = combined[pos:]
                pos = length
                continue

            open_match = _THINKING_OPEN_TAG_PATTERN.search(combined, pos)
            if open_match is not None:
                if open_match.start() > pos:
                    visible.append(combined[pos:open_match.start()])
                pos = open_match.end()
                self._inside = True
                continue

            # No confirmed open tag from this position; check whether the
            # remaining suffix could still grow into one (``<think``,
            # ``<reason``...) and buffer it if so.
            last_lt = combined.rfind("<", pos)
            if last_lt < pos:
                visible.append(combined[pos:])
                pos = length
                continue
            if last_lt == pos:
                visible.append(combined[:pos])
                self._carry = combined[pos:]
            else:
                visible.append(combined[pos:last_lt])
                self._carry = combined[last_lt:]
            pos = length

        return ContentWithThinking(text="".join(visible), thinking="".join(thinking))

    def flush(self) -> ContentWithThinking:
        """Drain the carry buffer at the end of a stream.

        Anything still buffered is treated as the appropriate bucket based
        on whether the parser was inside a thinking block when the stream
        ended (tags the model never closed leave their reasoning visible).
        """
        remaining = self._carry
        self._carry = ""
        if not remaining:
            self._inside = False
            return ContentWithThinking(text="", thinking="")
        if self._inside:
            self._inside = False
            return ContentWithThinking(text="", thinking=remaining)
        self._inside = False
        return ContentWithThinking(text=remaining, thinking="")


def _looks_like_open_tag_start(buffer: str) -> bool:
    """Return ``True`` when ``buffer`` could still grow into an open tag.

    Looks at the trailing ``<`` (if any) and the characters that follow it so
    that ``<thinking``, ``<think``, and ``<think>`` style partials are kept
    until the next chunk arrives.
    """
    open_idx = buffer.rfind("<")
    if open_idx < 0:
        return False
    tail = buffer[open_idx + 1 :].lower().lstrip()
    for alias in THINKING_TAG_ALIASES:
        if alias.startswith(tail):
            return True
    return False


def _split_text_by_thinking_tags(
    text: str,
    *,
    partial: bool,
    initial_inside: bool = False,
) -> tuple[str, str, str]:
    """Walk ``text`` once and bucket characters into visible / thinking.

    Args:
        text: The string to scan.
        partial: When ``True``, an unclosed open tag is retained in the
            third tuple element instead of being emitted so the caller can
            carry it forward across chunks.
        initial_inside: Whether the parser was already inside a thinking
            block when this chunk began.
    Returns:
        ``(visible_delta, thinking_delta, retained_suffix)``
    """
    visible: list[str] = []
    thinking: list[str] = []
    retained: str = ""

    pos = 0
    inside = initial_inside
    length = len(text)

    while pos < length:
        if inside:
            close_match = _THINKING_CLOSE_TAG_PATTERN.search(text, pos)
            if close_match is None:
                if partial:
                    tail_idx = _safe_tail_for_close_tag(text, pos)
                    if tail_idx > pos:
                        thinking.append(text[pos:tail_idx])
                        retained = text[tail_idx:]
                    else:
                        retained = text[pos:]
                    pos = length
                else:
                    thinking.append(text[pos:])
                    pos = length
                continue
            thinking.append(text[pos:close_match.start()])
            pos = close_match.end()
            inside = False
            continue

        open_match = _THINKING_OPEN_TAG_PATTERN.search(text, pos)
        if open_match is None:
            if partial:
                tail_idx = _safe_tail_for_open_tag(text, pos)
                if tail_idx > pos:
                    visible.append(text[pos:tail_idx])
                    retained = text[tail_idx:]
                else:
                    retained = text[pos:]
                pos = length
            else:
                visible.append(text[pos:])
                pos = length
            continue
        if open_match.start() > pos:
            visible.append(text[pos:open_match.start()])
        pos = open_match.end()
        inside = True

    return "".join(visible), "".join(thinking), retained


def _safe_tail_for_open_tag(text: str, start: int) -> int:
    """Return the largest cutoff index such that no open tag starts in ``text[start:cutoff]``."""
    candidate = text.rfind("<", start)
    if candidate < start:
        return start
    return candidate


def _safe_tail_for_close_tag(text: str, start: int) -> int:
    """Trim the trailing ``</`` fragment so a half-closed tag survives across chunks."""
    candidate = text.rfind("</", start)
    if candidate < start:
        return start
    return candidate


def parse_content_for_thinking(value: Any) -> ContentWithThinking:
    """Extract visible text and thinking content from any model message value.

    Accepts the same shapes that LangChain ``AIMessage`` / ``AIMessageChunk``
    expose for ``message.content``:

    * a plain ``str``;
    * a list mixed of string parts and structured parts (dict or objects);
    * a single dict part.

    Structured parts with ``type`` in :data:`_THINKING_PART_TYPES` are routed
    to the thinking bucket. String parts and ``{"type": "text", ...}`` parts
    are routed through :func:`extract_thinking_from_text` so both delivery
    styles collapse into the same answer.
    """
    if value is None:
        return ContentWithThinking(text="", thinking="")

    if isinstance(value, str):
        _visible, thinking, _ = _split_text_by_thinking_tags(value, partial=False)
        return ContentWithThinking(text=_visible, thinking=thinking)

    if isinstance(value, dict):
        return _parse_part_for_thinking(value)

    if isinstance(value, Iterable):
        visible_parts: list[str] = []
        thinking_parts: list[str] = []
        for part in value:
            part_value = _parse_part_for_thinking(part)
            if part_value.text:
                visible_parts.append(part_value.text)
            if part_value.thinking:
                thinking_parts.append(part_value.thinking)
        return ContentWithThinking(
            text="".join(visible_parts),
            thinking="\n".join(part for part in thinking_parts if part),
        )

    text_attr = getattr(value, "text", None)
    if isinstance(text_attr, str):
        return parse_content_for_thinking(text_attr)
    return ContentWithThinking(text="", thinking="")


def _parse_part_for_thinking(part: Any) -> ContentWithThinking:
    if isinstance(part, str):
        return parse_content_for_thinking(part)
    if isinstance(part, dict):
        part_type = str(part.get("type", "") or "").strip().lower()
        if part_type in _THINKING_PART_TYPES:
            thinking_attr_keys = ("thinking", "reasoning", "reasoning_content")
            for key in thinking_attr_keys:
                value = part.get(key)
                if value is not None:
                    thinking = _coerce_thinking_value(value)
                    if thinking:
                        return ContentWithThinking(text="", thinking=thinking)
            thinking = _coerce_thinking_value(part.get("text") or part.get("content"))
            return ContentWithThinking(text="", thinking=thinking)
        text_value = part.get("text")
        if isinstance(text_value, str):
            return parse_content_for_thinking(text_value)
        content_value = part.get("content")
        if content_value is not None:
            return parse_content_for_thinking(content_value)
        return ContentWithThinking(text="", thinking="")
    thinking_attr = (
        getattr(part, "thinking", None)
        or getattr(part, "reasoning", None)
        or getattr(part, "reasoning_content", None)
    )
    if isinstance(thinking_attr, str) and thinking_attr.strip():
        return ContentWithThinking(text="", thinking=thinking_attr)
    text_attr = getattr(part, "text", None)
    if isinstance(text_attr, str):
        return parse_content_for_thinking(text_attr)
    content_attr = getattr(part, "content", None)
    if content_attr is not None:
        return parse_content_for_thinking(content_attr)
    return ContentWithThinking(text="", thinking="")


def _coerce_thinking_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        nested_text = value.get("text") or value.get("thinking")
        if isinstance(nested_text, str):
            return nested_text
        nested_content = value.get("content")
        if nested_content is not None:
            return _coerce_thinking_value(nested_content)
        return ""
    if isinstance(value, list):
        pieces = [_coerce_thinking_value(item) for item in value]
        return "\n".join(part for part in pieces if part)
    text_attr = getattr(value, "text", None)
    if isinstance(text_attr, str):
        return text_attr
    return ""


__all__ = [
    "ContentWithThinking",
    "THINKING_TAG_ALIASES",
    "ThinkingTagStreamParser",
    "extract_thinking_from_text",
    "parse_content_for_thinking",
    "strip_thinking_tags",
]
