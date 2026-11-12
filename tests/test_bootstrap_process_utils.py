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


@pytest.mark.parametrize("pid", [0, -1, -123])
def test_invalid_pid_is_never_alive(pid):
    assert process_utils.is_process_alive(pid) is False


@pytest.mark.parametrize("content, expected", [("123", True), ("456", False), ("", False), ("invalid", False)])
def test_shutdown_signal_only_targets_its_server(tmp_path, content, expected):
    path = tmp_path / "shutdown.signal"
    path.write_text(content)
    assert process_utils.consume_shutdown_signal(path, 123) is expected
    assert path.exists() is not expected


def test_missing_shutdown_signal_is_ignored(tmp_path):
    assert process_utils.consume_shutdown_signal(tmp_path / "missing", 123) is False


def test_wait_treats_pid_reuse_as_original_process_exit():
    process = SimpleNamespace(is_running=lambda: False)
    assert process_utils.wait_for_process_exit(process, 0) is True


def test_wait_cannot_confirm_exit_when_access_is_denied():
    def denied():
        raise psutil.AccessDenied(123)

    assert process_utils.wait_for_process_exit(SimpleNamespace(is_running=denied), 0) is False
