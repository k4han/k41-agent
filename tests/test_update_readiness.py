"""Updates accept only readiness from the requested installed version."""

import httpx
import pytest

from agent.bootstrap import update, update_backup


@pytest.mark.parametrize("successful", [True, False])
def test_update_readiness_requires_target_version(tmp_path, monkeypatch, successful):
    monkeypatch.setattr(update_backup, "runtime_home", lambda: tmp_path)
    responses = []

    def handle(request):
        assert str(request.url) == "http://127.0.0.1:4141/ready"
        responses.append(request)
        version = "0.1.3" if successful and len(responses) >= 2 else "0.1.2"
        return httpx.Response(200, json={"status": "ready", "version": version})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(update.httpx, "Client", lambda **kwargs: client)
    monkeypatch.setattr(update.time, "sleep", lambda seconds: None)
    if successful:
        update.wait_for_server_ready(None, "0.1.3", timeout=1)
        assert len(responses) == 2
    else:
        with pytest.raises(update.UpdateError, match="expected version"):
            update.wait_for_server_ready(None, "0.1.3", timeout=0.02)
