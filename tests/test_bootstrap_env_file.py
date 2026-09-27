"""Tests for the startup ``.env`` loading in ``agent.bootstrap``."""

from __future__ import annotations

import os
from pathlib import Path

from pytest import MonkeyPatch

from agent.bootstrap import load_env_files

ENV_KEYS = (
    "K41_ENV_TEST_CWD",
    "K41_ENV_TEST_HOME",
    "K41_ENV_TEST_SHARED",
    "K41_ENV_TEST_PRESET",
)


def test_load_env_files_reads_cwd_and_agent_home(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    workdir = tmp_path / "work"
    agent_home = tmp_path / "home"
    workdir.mkdir()
    agent_home.mkdir()
    (workdir / ".env").write_text(
        "K41_ENV_TEST_CWD=cwd-value\nK41_ENV_TEST_SHARED=cwd-shared\n",
        encoding="utf-8",
    )
    (agent_home / ".env").write_text(
        "K41_ENV_TEST_HOME=home-value\nK41_ENV_TEST_SHARED=home-shared\n",
        encoding="utf-8",
    )

    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("K41_ENV_TEST_PRESET", "shell-value")
    monkeypatch.setenv("K41_AGENT_HOME", str(agent_home))
    monkeypatch.chdir(workdir)

    load_env_files()

    assert os.environ["K41_ENV_TEST_CWD"] == "cwd-value"
    assert os.environ["K41_ENV_TEST_HOME"] == "home-value"
    # The working directory wins when both files define the same key.
    assert os.environ["K41_ENV_TEST_SHARED"] == "cwd-shared"
    # Values already present in the environment are never overwritten.
    assert os.environ["K41_ENV_TEST_PRESET"] == "shell-value"


def test_load_env_files_without_env_files_is_a_noop(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("K41_AGENT_HOME", str(empty_home))
    monkeypatch.chdir(tmp_path)

    load_env_files()

    assert not any(key in os.environ for key in ENV_KEYS)
