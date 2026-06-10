from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agent.shared.infrastructure.http_errors import register_http_exception_handlers


def _make_app() -> FastAPI:
    app = FastAPI()
    register_http_exception_handlers(app)
    return app


def _client_with_handler() -> TestClient:
    return TestClient(_make_app())


def test_http_exception_handler_preserves_status_and_detail() -> None:
    client = _client_with_handler()

    @client.app.get("/boom")
    def boom() -> None:
        raise HTTPException(status_code=409, detail="Conflict")

    response = client.get("/boom")

    assert response.status_code == 409
    assert response.json() == {
        "code": "conflict",
        "detail": "Conflict",
    }


def test_value_error_becomes_bad_request() -> None:
    client = _client_with_handler()

    @client.app.get("/bad")
    def bad() -> None:
        raise ValueError("Invalid payload")

    response = client.get("/bad")

    assert response.status_code == 400
    assert response.json() == {
        "code": "bad_request",
        "detail": "Invalid payload",
    }


def test_unhandled_exception_becomes_safe_internal_error() -> None:
    client = _client_with_handler()

    @client.app.get("/crash")
    def crash() -> None:
        raise RuntimeError("Secret value")

    response = client.get("/crash")

    assert response.status_code == 500
    payload = response.json()
    assert payload["code"] == "unknown"
    assert "Something went wrong" in payload["detail"]
    assert "Secret value" not in payload["detail"]


def test_validation_errors_are_normalized() -> None:
    client = _client_with_handler()

    @client.app.get("/validate")
    def validate(age: int) -> None:
        return None

    response = client.get("/validate?age=not-a-number")

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "validation_error"
    assert payload["detail"] == "Request validation failed."
    assert payload["errors"][0]["loc"] == ["query", "age"]
