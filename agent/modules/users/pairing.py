from typing import Callable, Awaitable

from agent.modules.users.service import PairingService
from agent.shared.infrastructure.cache import get_cache

AUTH_CACHE_TTL_SECONDS = 3600

_pairing_service: PairingService | None = None


def get_pairing_service() -> PairingService:
    """Get or create a singleton PairingService instance."""
    global _pairing_service
    if _pairing_service is None:
        _pairing_service = PairingService()
    return _pairing_service


def make_auth_cache_key(platform: str, user_id: str) -> str:
    """Generate cache key for authentication status."""
    platform_str = platform.value if hasattr(platform, "value") else str(platform)
    return f"auth:{platform_str}:{user_id}"


def _cache_user_auth(platform: str, user_id: str) -> None:
    """Cache user authentication status."""
    cache = get_cache()
    cache_key = make_auth_cache_key(platform, user_id)
    cache.set(cache_key, True, ttl_seconds=AUTH_CACHE_TTL_SECONDS)


async def handle_pairing_command(
    platform: str,
    user_id: str,
    text: str,
    reply_fn: Callable[[str], Awaitable[None]]
) -> bool:
    """
    Handle /pair command for any platform.

    Returns True if command was handled (whether success or failure),
    False if not a pairing command.
    """
    if not text.startswith("/pair "):
        return False

    parts = text.split(" ", 1)
    code = parts[1].strip() if len(parts) > 1 else ""

    if not code:
        await reply_fn("Vui lòng cung cấp mã liên kết.")
        return True

    pairing_service = get_pairing_service()
    success = await pairing_service.process_pairing(platform, user_id, code)

    if success:
        _cache_user_auth(platform, user_id)
        await reply_fn("Tài khoản đã được liên kết thành công!")
    else:
        await reply_fn("Mã liên kết không hợp lệ hoặc đã hết hạn.")

    return True


async def check_user_authenticated(
    platform: str,
    user_id: str,
    reply_fn: Callable[[str], Awaitable[None]]
) -> bool:
    """
    Check if user is authenticated. If not, send pairing instruction.

    Returns True if authenticated, False otherwise.
    """
    cache = get_cache()
    cache_key = make_auth_cache_key(platform, user_id)

    cached_auth = cache.get(cache_key)
    if cached_auth is True:
        return True

    pairing_service = get_pairing_service()
    identity = await pairing_service.get_or_create_identity(platform, user_id)

    if identity.user_id:
        _cache_user_auth(platform, user_id)
        return True

    await reply_fn("Vui lòng nhận mã liên kết và gửi lệnh /pair MÃ_SỐ để xác thực.")
    return False


async def authenticate_channel_message(
    platform: str,
    user_id: str,
    text: str,
    reply_fn: Callable[[str], Awaitable[None]]
) -> bool:
    """
    Authenticate a channel message. Handles pairing commands and checks authentication.

    Returns True if message should be processed, False if authentication was handled.
    """
    if await handle_pairing_command(platform, user_id, text, reply_fn):
        return False

    if not await check_user_authenticated(platform, user_id, reply_fn):
        return False

    return True
