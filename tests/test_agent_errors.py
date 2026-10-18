import pytest

from agent.shared.infrastructure.errors import (
    ERROR_CODE_AUTH,
    ERROR_CODE_CONNECTION,
    ERROR_CODE_RATE_LIMIT,
    ERROR_CODE_TIMEOUT,
    ERROR_CODE_UNKNOWN,
    ERROR_CODE_UPSTREAM,
    classify_agent_error,
    friendly_agent_error_message,
)


def test_classify_by_status_code() -> None:
    class RateErr(Exception):
        status_code = 429

    class AuthErr(Exception):
        status_code = 401

    class TimeoutStatusErr(Exception):
        status_code = 504

    class UpstreamErr(Exception):
        status_code = 503

    assert classify_agent_error(RateErr()).code == ERROR_CODE_RATE_LIMIT
    assert classify_agent_error(AuthErr()).code == ERROR_CODE_AUTH
    assert classify_agent_error(TimeoutStatusErr()).code == ERROR_CODE_TIMEOUT
    assert classify_agent_error(UpstreamErr()).code == ERROR_CODE_UPSTREAM


def test_classify_by_response_status_code() -> None:
    class Response:
        status_code = 429

    class WrappedErr(Exception):
        response = Response()

    assert classify_agent_error(WrappedErr()).code == ERROR_CODE_RATE_LIMIT


def test_classify_by_class_name() -> None:
    class RateLimitError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    class AuthenticationError(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    assert classify_agent_error(RateLimitError()).code == ERROR_CODE_RATE_LIMIT
    assert classify_agent_error(APITimeoutError()).code == ERROR_CODE_TIMEOUT
    assert classify_agent_error(AuthenticationError()).code == ERROR_CODE_AUTH
    assert classify_agent_error(APIConnectionError()).code == ERROR_CODE_CONNECTION


def test_classify_builtin_timeout() -> None:
    assert classify_agent_error(TimeoutError()).code == ERROR_CODE_TIMEOUT


def test_classify_walks_cause_chain() -> None:
    class RateLimitError(Exception):
        pass

    try:
        try:
            raise RateLimitError("429")
        except RateLimitError as inner:
            raise ValueError("wrapped") from inner
    except ValueError as exc:
        result = classify_agent_error(exc)

    assert result.code == ERROR_CODE_RATE_LIMIT


def test_classify_walks_exception_group() -> None:
    class RateLimitError(Exception):
        pass

    group = ExceptionGroup("task failed", [ValueError("noise"), RateLimitError("429")])

    assert classify_agent_error(group).code == ERROR_CODE_RATE_LIMIT


def test_classify_unknown_has_message() -> None:
    result = classify_agent_error(ValueError("boom"))
    assert result.code == ERROR_CODE_UNKNOWN
    assert result.message


@pytest.mark.parametrize(("raw", "code", "expected"), [
    ("The request failed.\nRequest ID: raw-diagnostic", "unknown", "The request failed."),
    ('{"error":{"message":"The selected model is unavailable.","type":"invalid_request"}}', "unknown", "The selected model is unavailable."),
    ("Error code: 400 - {'error': {'message': 'Please choose another model.', 'code': 'model_missing'}}", "unknown", "Please choose another model."),
    ('''BadRequestError("Error code: 400 - {'error': {'message': 'The model cannot read images.'}}")''', "execution_failed", "The model cannot read images."),
    ("TimeoutError('raw provider request ID')", "execution_failed", "The AI provider took too long to respond. Please try again."),
    ("ValueError('The selected model is unavailable.')", "execution_failed", "The selected model is unavailable."),
    ("RuntimeError()", "unknown", "Something went wrong while generating a response. Please try again."),
    ("Traceback (most recent call last):\n  File 'internal.py', line 12", "upstream", "The AI provider returned an error. Please try again shortly."),
    ("Recursion limit reached.\nFor troubleshooting, visit internal docs.", "recursion_limit_reached", "The agent reached its step limit. Continue to keep working on this task."),
])
def test_friendly_error_message_omits_raw_diagnostics(raw, code, expected):
    assert friendly_agent_error_message(raw, code=code) == expected
