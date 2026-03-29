import pytest
import pytest_asyncio

from agent.modules.channels.infrastructure.repository import ChannelSettingsRepository
from agent.modules.settings.infrastructure.repository import UserPreferencesRepository
from agent.persistence.models import User, get_persistence_metadata
from agent.shared.infrastructure.db.engine import close_async_engine, initialize_async_engine
from agent.shared.infrastructure.db.session import get_async_session_maker


@pytest_asyncio.fixture
async def repository_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    db_path = tmp_path / "repository.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.resolve().as_posix()}")
    monkeypatch.setenv("PERSISTENCE_ALLOW_ANY_PATH", "true")

    await initialize_async_engine(metadata=get_persistence_metadata())

    session_maker = get_async_session_maker()
    async with session_maker() as session:
        user = User(username="repo-user")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

    try:
        yield user_id
    finally:
        await close_async_engine()


@pytest.mark.asyncio
async def test_channel_settings_repository_crud(repository_db: int):
    repo = ChannelSettingsRepository()

    created = await repo.upsert(
        repository_db,
        "telegram",
        True,
        '{"token":"abc"}',
    )
    fetched = await repo.get(repository_db, "telegram")

    assert created.platform == "telegram"
    assert created.enabled is True
    assert fetched is not None
    assert fetched.config_json == '{"token":"abc"}'

    updated = await repo.upsert(
        repository_db,
        "telegram",
        False,
        '{"token":"xyz"}',
    )
    listed = await repo.list_for_user(repository_db)

    assert updated.id == created.id
    assert updated.enabled is False
    assert listed[0].platform == "telegram"
    assert listed[0].config_json == '{"token":"xyz"}'

    deleted = await repo.delete(repository_db, "telegram")
    missing = await repo.get(repository_db, "telegram")

    assert deleted is True
    assert missing is None
    assert await repo.delete(repository_db, "telegram") is False


@pytest.mark.asyncio
async def test_user_preferences_repository_crud(repository_db: int):
    repo = UserPreferencesRepository()

    created = await repo.upsert(repository_db, "language", "vi")
    fetched = await repo.get(repository_db, "language")

    assert created.key == "language"
    assert created.value == "vi"
    assert fetched is not None
    assert fetched.value == "vi"

    updated = await repo.upsert(repository_db, "language", "en")
    await repo.upsert(repository_db, "timezone", "Asia/Bangkok")
    listed = await repo.list_for_user(repository_db)

    assert updated.id == created.id
    assert updated.value == "en"
    assert [item.key for item in listed] == ["language", "timezone"]

    deleted = await repo.delete(repository_db, "language")
    missing = await repo.get(repository_db, "language")

    assert deleted is True
    assert missing is None
    assert await repo.delete(repository_db, "language") is False
