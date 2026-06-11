import pytest

from agent.modules.conversations import history


@pytest.mark.asyncio
async def test_attach_checkpoint_stats_keeps_threads_when_one_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_checkpoint_stats(thread_id: str) -> dict[str, object]:
        if thread_id == "bad":
            raise RuntimeError("stats failed")
        return {
            "latest_checkpoint_id": f"{thread_id}-checkpoint",
            "checkpoint_count": 3,
        }

    monkeypatch.setattr(history, "get_checkpoint_stats", fake_get_checkpoint_stats)

    threads = [
        {"thread_id": "good", "title": "Good"},
        {"thread_id": "bad", "title": "Bad"},
    ]

    result = await history._attach_checkpoint_stats(threads)

    assert result == [
        {
            "thread_id": "good",
            "title": "Good",
            "latest_checkpoint_id": "good-checkpoint",
            "checkpoint_count": 3,
        },
        {
            "thread_id": "bad",
            "title": "Bad",
            "latest_checkpoint_id": "",
            "checkpoint_count": 0,
        },
    ]
