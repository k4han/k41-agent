"""Shared capture quotas preserve exact text until the safety limit is reached."""

import io

from agent.modules.tools.runtime import output_policy


def test_capture_preserves_unicode_and_newlines_before_the_quota():
    capture = output_policy.TextCapture(max_bytes=100)
    capture.append_line("")
    capture.append_line("emoji: \U0001f600")
    capture.append_line("last")
    assert capture.content() == "\nemoji: \U0001f600\nlast"
    assert not capture.truncated


def test_capture_quota_never_splits_unicode_and_reports_loss():
    capture = output_policy.TextCapture(max_bytes=5)
    capture.append("x\U0001f600\U0001f600")
    capture.append("later")
    assert capture.truncated and capture.size == 5
    assert capture.content() == "x\U0001f600\n\n" + output_policy.CAPTURE_NOTICE


def test_byte_capture_distinguishes_exact_quota_from_missing_content():
    assert output_policy.capture_bytes([b"ab", b"cde"], max_bytes=5) == (b"abcde", False)
    assert output_policy.capture_bytes([b"ab", b"cde", b"f"], max_bytes=5) == (b"abcde", True)


def test_captured_lines_keeps_physical_line_numbers_after_the_quota(monkeypatch):
    monkeypatch.setattr(output_policy, "MAX_STORED_BYTES", 5)
    lines = list(output_policy.captured_lines(io.StringIO("abcdefgh\nnext\n")))
    assert lines == [("abcde", True), ("next\n", False)]


def test_captured_lines_joins_chunks_without_dropping_the_line_suffix():
    line = "x" * (128 * 1024) + "\U0001f600 end\n"
    assert list(output_policy.captured_lines(io.StringIO(line + "next\n"))) == [(line, False), ("next\n", False)]
