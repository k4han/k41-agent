import pytest
import pytest_asyncio
import uuid

from agent.modules.channels.infrastructure.repository import ChannelSettingsRepository
from agent.shared.infrastructure.db import UserPreferencesRepository, Base
from agent.modules.users.public import User
from agent.shared.infrastructure.db.engine import close_async_engine, initialize_async_engine
from agent.shared.infrastructure.db.session import get_async_session_maker


@pytest_asyncio.fixture
async def repository_db(monkeypatch: pytest.MonkeyPatch, tmp_path, request):
    """Create a fresh database for each test."""
    # Ensure clean state
    await close_async_engine()

    # Use test name to ensure unique DB per test
    test_name = request.node.name
    db_path = tmp_path / f"{test_name}.sqlite"
    db_url = f"sqlite:///{db_path.resolve().as_posix()}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("PERSISTENCE_ALLOW_ANY_PATH", "true")
    
    # Also mock get_database_url so it uses the test DB
    import agent.shared.infrastructure.db.engine as engine_module
    monkeypatch.setattr(engine_module, "get_database_url", lambda: db_url)
    # Clear the cached database URL if any
    engine_module._cached_database_url = None

    await initialize_async_engine(metadata=Base.metadata)

    try:
        yield
    finally:
        await close_async_engine()


async def _create_test_user() -> int:
    """Helper to create a test user and return its ID."""
    session_maker = get_async_session_maker()
    async with session_maker() as session:
        user = User(is_active=True)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


@pytest.mark.asyncio
async def test_channel_settings_repository_crud(repository_db):
    user_id = await _create_test_user()
    repo = ChannelSettingsRepository()

    created = await repo.upsert(
        user_id,
        "telegram",
        True,
        '{"token":"abc"}',
    )
    fetched = await repo.get(user_id, "telegram")

    assert created.platform == "telegram"
    assert created.enabled is True
    assert fetched is not None
    assert fetched.config_json == '{"token":"abc"}'

    updated = await repo.upsert(
        user_id,
        "telegram",
        False,
        '{"token":"xyz"}',
    )
    listed = await repo.list_for_user(user_id)

    assert updated.id == created.id
    assert updated.enabled is False
    assert listed[0].platform == "telegram"
    assert listed[0].config_json == '{"token":"xyz"}'

    deleted = await repo.delete(user_id, "telegram")
    missing = await repo.get(user_id, "telegram")

    assert deleted is True
    assert missing is None
    assert await repo.delete(user_id, "telegram") is False


@pytest.mark.asyncio
async def test_user_preferences_repository_crud(repository_db):
    user_id = await _create_test_user()
    repo = UserPreferencesRepository()

    created = await repo.upsert(user_id, "language", "vi")
    fetched = await repo.get(user_id, "language")

    assert created.key == "language"
    assert created.value == "vi"
    assert fetched is not None
    assert fetched.value == "vi"

    updated = await repo.upsert(user_id, "language", "en")
    await repo.upsert(user_id, "timezone", "Asia/Bangkok")
    listed = await repo.list_for_user(user_id)

    assert updated.id == created.id
    assert updated.value == "en"
    assert [item.key for item in listed] == ["language", "timezone"]

    deleted = await repo.delete(user_id, "language")
    missing = await repo.get(user_id, "language")

    assert deleted is True
    assert missing is None
    assert await repo.delete(user_id, "language") is False
