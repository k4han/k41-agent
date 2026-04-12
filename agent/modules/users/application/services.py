from datetime import timedelta, timezone
import logging
import secrets
from sqlalchemy import select, update

from agent.shared.infrastructure.db.session import get_async_session
from agent.shared.infrastructure.db.base import utcnow
from agent.modules.users.infrastructure.models import UserIdentity, PairingCode, User

logger = logging.getLogger(__name__)


class PairingService:
    async def get_or_create_identity(self, platform: str, external_id: str) -> UserIdentity:
        session = await get_async_session()
        async with session:
            try:
                stmt = select(UserIdentity).where(
                    UserIdentity.platform == platform,
                    UserIdentity.external_id == external_id
                )
                result = await session.execute(stmt)
                identity = result.scalar_one_or_none()

                if identity is None:
                    identity = UserIdentity(
                        platform=platform,
                        external_id=external_id,
                        user_id=None
                    )
                    session.add(identity)
                    await session.commit()
                    await session.refresh(identity)

                return identity
            except Exception:
                await session.rollback()
                raise

    async def process_pairing(self, platform: str, external_id: str, code: str) -> bool:
        session = await get_async_session()
        async with session:
            try:
                stmt = select(PairingCode).where(
                    PairingCode.code == code,
                    PairingCode.is_used == False
                )
                result = await session.execute(stmt)
                pairing_code = result.scalar_one_or_none()

                if not pairing_code:
                    return False

                now = utcnow()
                if pairing_code.expires_at.tzinfo is None:
                    pairing_code_expires = pairing_code.expires_at.replace(tzinfo=timezone.utc)
                else:
                    pairing_code_expires = pairing_code.expires_at

                if pairing_code_expires < now:
                    return False

                identity_stmt = select(UserIdentity).where(
                    UserIdentity.platform == platform,
                    UserIdentity.external_id == external_id
                )
                identity_result = await session.execute(identity_stmt)
                identity = identity_result.scalar_one_or_none()

                if identity:
                    if identity.user_id is not None:
                        return False
                    identity.user_id = pairing_code.user_id
                else:
                    identity = UserIdentity(
                        platform=platform,
                        external_id=external_id,
                        user_id=pairing_code.user_id
                    )
                    session.add(identity)

                # Optimistic concurrency control lock
                update_stmt = (
                    update(PairingCode)
                    .where(PairingCode.code == code, PairingCode.is_used == False)
                    .values(is_used=True)
                )
                update_result = await session.execute(update_stmt)
                
                if update_result.rowcount == 0:
                    await session.rollback()
                    return False

                await session.commit()
                return True
            except Exception:
                await session.rollback()
                raise

    async def create_pairing_root_user_and_code(self) -> tuple[str, int]:
        session = await get_async_session()
        async with session:
            try:
                stmt = select(User).where(User.is_active == True).order_by(User.id)
                result = await session.execute(stmt)
                user = result.scalars().first()

                if not user:
                    user = User(is_active=True)
                    session.add(user)
                    await session.commit()
                    await session.refresh(user)

                raw_code = secrets.token_hex(4).upper()
                code = f"{raw_code[:4]}-{raw_code[4:]}"

                expires_at = utcnow() + timedelta(hours=24)
                pairing = PairingCode(
                    code=code,
                    user_id=user.id,
                    expires_at=expires_at,
                    is_used=False
                )
                session.add(pairing)
                await session.commit()
                
                return code, user.id
            except Exception:
                await session.rollback()
                raise

    async def list_paired_identities(self) -> list[UserIdentity]:
        """List all identities that are paired with users."""
        session = await get_async_session()
        async with session:
            try:
                stmt = select(UserIdentity).where(UserIdentity.user_id.isnot(None))
                result = await session.execute(stmt)
                return list(result.scalars().all())
            except Exception:
                logger.exception("Failed to list paired identities")
                return []

    async def unpair_identity(self, identity_id: int) -> tuple[str, str] | None:
        """Unpair an identity, clear cache and session. Returns (platform, external_id) if successful."""
        session = await get_async_session()
        async with session:
            try:
                stmt = select(UserIdentity).where(UserIdentity.id == identity_id)
                result = await session.execute(stmt)
                identity = result.scalar_one_or_none()
                if not identity:
                    return None

                platform = identity.platform
                external_id = identity.external_id
                identity.user_id = None
                await session.commit()

                # Clear authentication cache
                from agent.modules.users.application.pairing_handler import make_auth_cache_key
                from agent.shared.infrastructure.cache import get_cache
                get_cache().delete(make_auth_cache_key(platform, external_id))

                # Clear agent session
                try:
                    from agent.modules.agent_runtime.public import clear_agent_session
                    await clear_agent_session(platform=platform, user_id=external_id)
                except Exception as e:
                    logger.warning(f"Failed to clear agent session for {platform}:{external_id}: {e}")

                return (platform, external_id)
            except Exception:
                await session.rollback()
                raise
