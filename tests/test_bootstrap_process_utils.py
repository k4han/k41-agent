from types import SimpleNamespace

import psutil
import pytest

from agent.bootstrap import process_utils


@pytest.mark.parametrize("status", [psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD])
def test_exited_process_with_existing_pid_is_not_alive(monkeypatch, status):
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(status=lambda: status))
    assert process_utils.is_process_alive(123) is False


def test_process_exiting_during_liveness_check_is_not_alive(monkeypatch):
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)

    def disappeared(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", disappeared)
    assert process_utils.is_process_alive(123) is False


def test_unreadable_process_status_is_treated_as_alive(monkeypatch):
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)

    def inaccessible(pid):
        raise psutil.AccessDenied(pid)

    monkeypatch.setattr(psutil, "Process", inaccessible)
    assert process_utils.is_process_alive(123) is True
