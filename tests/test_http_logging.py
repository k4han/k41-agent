import logging

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from agent.shared.infrastructure.http_logging import HTTPLoggingMiddleware, LogViewerAccessFilter


@pytest.mark.parametrize(("path", "logged"), [
    ("/settings/logs", False),
    ("/dashboard-api/logs", False),
    ("/dashboard-api/logs/", False),
    ("/dashboard-api/logs/server?after=cursor", False),
    ("/dashboard-api/logs/tray?before=cursor", False),
    ("/dashboard-api/logs/update", False),
    ("/health", True),
    ("/dashboard-api/logs-other", True),
    ("/settings/logs-other", True),
    ("/health?next=/dashboard-api/logs/server", True),
])
def test_log_viewer_requests_are_excluded_from_both_logs(path, logged, caplog):
    app = FastAPI()
    app.add_middleware(HTTPLoggingMiddleware)

    @app.get("/{path:path}")
    def endpoint(path: str):
        return {"ok": True}

    with caplog.at_level(logging.INFO):
        with TestClient(app) as client:
            response = client.get(path)
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    requests = [record for record in caplog.records if record.getMessage() == "HTTP request completed"]
    assert bool(requests) is logged
    if logged:
        assert requests[0].path == path.partition("?")[0]
        assert requests[0].status == 200

    access_logger = logging.getLogger("uvicorn.access")
    record = access_logger.makeRecord(
        access_logger.name, logging.INFO, __file__, 0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:12345", "GET", path, "1.1", 200), None,
    )
    assert LogViewerAccessFilter().filter(record) is logged


@pytest.mark.parametrize(("status", "level"), [(400, logging.WARNING), (500, logging.ERROR)])
def test_other_request_errors_are_still_logged(status, level, caplog):
    app = FastAPI()
    app.add_middleware(HTTPLoggingMiddleware)

    @app.get("/error")
    def endpoint():
        return Response(status_code=status)

    with caplog.at_level(logging.INFO):
        with TestClient(app) as client:
            assert client.get("/error").status_code == status
    requests = [record for record in caplog.records if record.getMessage() == "HTTP request completed"]
    assert len(requests) == 1
    assert requests[0].levelno == level
    assert requests[0].status == status
