from typing import Callable, Awaitable

from agent.modules.users.application.services import UserService
from agent.shared.infrastructure.cache import get_cache


_user_service: UserService | None = None


def get_user_service() -> UserService:
    """Get or create a singleton UserService instance."""
    global _user_service
    if _user_service is None:
        _user_service = UserService()
    return _user_service


def _make_auth_cache_key(platform: str, user_id: str) -> str:
    """Generate cache key for authentication status."""
    return f"auth:{platform}:{user_id}"


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

    user_service = get_user_service()
    success = await user_service.process_pairing(platform, user_id, code)

    if success:
        cache = get_cache()
        cache_key = _make_auth_cache_key(platform, user_id)
        cache.set(cache_key, True, ttl_seconds=3600)
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
    cache_key = _make_auth_cache_key(platform, user_id)

    cached_auth = cache.get(cache_key)
    if cached_auth is True:
        return True

    user_service = get_user_service()
    identity = await user_service.get_or_create_identity(platform, user_id)

    if not identity.user_id:
        await reply_fn("Vui lòng nhận mã liên kết và gửi lệnh /pair <MÃ> để xác thực.")
        return False

    cache.set(cache_key, True, ttl_seconds=3600)
    return True
